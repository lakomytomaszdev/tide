"""Kopia powłoki Plasmy z ekranem blokady Tide i jej synchronizacja z pakietem systemowym.

Użycie: python3 shell_sync.py sync [--force] | remove | status

W Plasmie 6.8 greeter bierze ekran blokady wyłącznie z pakietu powłoki wskazanego
w plasmashellrc [Shell] ShellPackage (domyślnie org.kde.plasma.desktop). Żeby ekran Tide
był prawdziwym ekranem blokady bez zmiany powłoki (i pliku paneli
plasma-org.kde.plasma.desktop-appletsrc), robimy pełną kopię systemowego pakietu
/usr/share/plasma/shells/org.kde.plasma.desktop w $XDG_DATA_HOME/plasma/shells/ i podmieniamy
w niej tylko contents/lockscreen na ekran Tide (z zainstalowanego pakietu org.tide.desktop).
Pakiet w katalogu użytkownika wygrywa z systemowym, więc z kopii czyta też plasmashell —
dlatego cała reszta musi być identyczna z systemem.

Po aktualizacji Plasmy kopia byłaby nieaktualna, więc jednostka tide-shell-sync.service
uruchamia ten skrypt przy każdym logowaniu, przed plasmashell. Skrypt liczy odcisk
(SHA-256) plików pakietu systemowego i ekranu Tide i porównuje go z odciskiem zapisanym
w kopii (MARKER). Gdy się różnią, buduje nową kopię obok, sprawdza ją prawdziwym greeterem
(kscreenlocker_greet --testing poza ekranem) i dopiero wtedy podmienia. Gdy ekran Tide się
nie wczyta, kopia jest usuwana w całości — wtedy wygrywa pakiet systemowy (ekran Plasmy).

Stan ostatniej decyzji jest w $XDG_STATE_HOME/tide/shell-sync.json:
- "ok" — kopia działa,
- "fallback" — ekran Tide nie przeszedł sprawdzenia; ponowna próba dopiero po zmianie
  odcisku (aktualizacja Plasmy albo nowe make install), żeby nie opóźniać każdego logowania,
- "removed" — kopię usunięto ręcznie (sposób ratunkowy z README); skrypt jej nie odtwarza,
  dopóki nie zrobi tego make install (sync --force).
Kopię, której nie zrobił Tide (brak pliku MARKER), zostawiamy w spokoju.

Skrypt jest samodzielny (bez importów z repozytorium), bo instalacja kopiuje go do
$XDG_DATA_HOME/tide/shell-sync.py i pulpit nie zależy od katalogu z repozytorium.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SHELL_ID = "org.kde.plasma.desktop"
TIDE_SHELL_ID = "org.tide.desktop"
MARKER = "tide-shell-copy.json"  # w katalogu głównym kopii; KPackage ignoruje nieznane pliki
FORMAT = 1  # zmiana sposobu składania kopii wymusza jej odtworzenie
UNIT = "tide-shell-sync.service"

LOADED = "Tide: ekran blokady wczytany"  # console.log z LockScreen.qml ekranu Tide
FALLBACK = "Failed to load lockscreen QML, falling back to built-in locker"
CHECK_TIMEOUT = 10.0  # s — pierwsze wczytanie QML bez pamięci podręcznej trwa 1–3 s
CHECK_LINGER = 1.5  # s po wczytaniu, żeby złapać późny błąd
GREETERS = ("/usr/lib/x86_64-linux-gnu/libexec/kscreenlocker_greet", "/usr/libexec/kscreenlocker_greet")


def xdg(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or Path.home() / default)


DATA = xdg("XDG_DATA_HOME", ".local/share")
STATE = xdg("XDG_STATE_HOME", ".local/state")
SYSTEM_SHELL = Path(os.environ.get("TIDE_SYSTEM_SHELL") or f"/usr/share/plasma/shells/{SHELL_ID}")
COPY = DATA / "plasma" / "shells" / SHELL_ID
TIDE_LOCKSCREEN = DATA / "plasma" / "shells" / TIDE_SHELL_ID / "contents" / "lockscreen"
# Poza plasma/shells/, żeby KPackage nigdy nie zobaczył niegotowej kopii; ten sam system
# plików co kopia, więc podmiana to dwa rename().
STAGING = DATA / "plasma" / ".tide-shell-staging"
STAMP = STATE / "tide" / "shell-sync.json"


def log(message: str) -> None:
    print(f"tide-shell-sync: {message}", flush=True)


def greeter() -> str | None:
    override = os.environ.get("TIDE_GREETER")
    if override:
        return override
    return next((p for p in GREETERS if os.access(p, os.X_OK)), None)


def fingerprint(*directories: Path) -> str:
    """Odcisk treści katalogów: ścieżki względne, cele dowiązań i zawartość plików."""
    digest = hashlib.sha256(f"tide-shell-copy {FORMAT}\n".encode())
    for directory in directories:
        digest.update(f"[{directory.name}]\n".encode())
        for path in sorted(directory.rglob("*")):
            rel = path.relative_to(directory).as_posix()
            if path.is_symlink():
                digest.update(f"L {rel} {os.readlink(path)}\n".encode())
            elif path.is_file():
                digest.update(f"F {rel} {hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def is_ours(path: Path) -> bool:
    return (path / MARKER).is_file()


def write_stamp(fingerprint_: str, result: str) -> None:
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(json.dumps({"fingerprint": fingerprint_, "result": result, "system": str(SYSTEM_SHELL),
                                 "time": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2) + "\n")


def remove_copy() -> bool:
    """Usuwa kopię, ale tylko naszą. True, gdy kopii (już) nie ma."""
    if not COPY.exists() and not COPY.is_symlink():
        return True
    if not is_ours(COPY):
        log(f"{COPY} nie pochodzi od Tide (brak {MARKER}) — zostawiam bez zmian.")
        return False
    shutil.rmtree(COPY)
    return True


def build(target: Path, fingerprint_: str) -> None:
    """Pełna kopia pakietu systemowego z contents/lockscreen z ekranu Tide."""
    shutil.copytree(SYSTEM_SHELL, target, symlinks=True)
    lockscreen = target / "contents" / "lockscreen"
    if lockscreen.exists():
        shutil.rmtree(lockscreen)
    shutil.copytree(TIDE_LOCKSCREEN, lockscreen, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    notice = TIDE_LOCKSCREEN.parents[1] / "NOTICE"  # licencja i pochodzenie ekranu Tide
    if notice.is_file():
        shutil.copy2(notice, lockscreen / "NOTICE")
    (target / MARKER).write_text(json.dumps({
        "fingerprint": fingerprint_,
        "system": str(SYSTEM_SHELL),
        "lockscreen": str(TIDE_LOCKSCREEN),
        "note": "Kopia robiona przez Tide (scripts/shell_sync.py). Usunięcie katalogu przywraca ekran Plasmy.",
    }, indent=2, ensure_ascii=False) + "\n")


def check(package: Path) -> tuple[bool, str]:
    """Prawdziwy greeter w trybie testowym, poza ekranem, bez sesji i bez magistrali D-Bus.

    Niczego nie blokuje i nie pokazuje okna. Sukces: ekran Tide się wczytał i greeter nie
    przeszedł na awaryjny ekran KDE.
    """
    binary = greeter()
    if binary is None or not os.access(binary, os.X_OK):
        return False, f"brak kscreenlocker_greet ({binary})"
    with tempfile.TemporaryDirectory(prefix="tide-shell-check-") as tmp:
        env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY", "WAYLAND_SOCKET")}
        env.update(QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", QT_LOGGING_RULES="qml.debug=true",
                   XDG_CONFIG_HOME=f"{tmp}/config", XDG_CONFIG_DIRS="/etc/xdg", XDG_CACHE_HOME=f"{tmp}/cache",
                   DBUS_SESSION_BUS_ADDRESS=f"unix:path={tmp}/no-bus")
        proc = subprocess.Popen([binary, "--testing", "--shell", str(package)], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                start_new_session=True)
        lines: queue.Queue[str | None] = queue.Queue()

        def reader() -> None:
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=reader, daemon=True).start()
        output: list[str] = []
        deadline = time.monotonic() + CHECK_TIMEOUT
        loaded_at = None
        try:
            while True:
                now = time.monotonic()
                if loaded_at is not None and now - loaded_at >= CHECK_LINGER:
                    break
                if now >= deadline:
                    break
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    break
                output.append(line)
                if FALLBACK in line:
                    break
                if LOADED in line and loaded_at is None:
                    loaded_at = time.monotonic()
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)  # razem z procesami PAM greetera
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    text = "".join(output)
    return LOADED in text and FALLBACK not in text, text


def replace_copy(new: Path) -> None:
    old = STAGING / "old"
    if old.exists():
        shutil.rmtree(old)
    if COPY.exists():
        COPY.rename(old)
    COPY.parent.mkdir(parents=True, exist_ok=True)
    new.rename(COPY)
    if old.exists():
        shutil.rmtree(old)


def sync(force: bool = False) -> str:
    """Doprowadza kopię do stanu zgodnego z systemem. Zwraca wynik: ok, fallback, removed, …"""
    if not SYSTEM_SHELL.is_dir():
        remove_copy()
        log(f"brak pakietu systemowego {SYSTEM_SHELL} — kopia usunięta.")
        return "no-system"
    if not TIDE_LOCKSCREEN.is_dir():
        remove_copy()
        log(f"brak ekranu Tide w {TIDE_LOCKSCREEN} — kopia usunięta.")
        return "no-tide"

    current = fingerprint(SYSTEM_SHELL, TIDE_LOCKSCREEN)
    stamp = read_json(STAMP)
    copy_exists = COPY.exists()

    if copy_exists and not is_ours(COPY):
        log(f"{COPY} nie pochodzi od Tide — nic nie zmieniam.")
        return "foreign"
    if not force:
        if copy_exists and read_json(COPY / MARKER).get("fingerprint") == current:
            return "up-to-date"
        if stamp.get("result") == "removed":
            return "removed"
        if not copy_exists and stamp.get("result") == "ok":
            # Ktoś usunął kopię ręcznie (sposób ratunkowy) — nie odtwarzamy jej po cichu.
            write_stamp(stamp.get("fingerprint", ""), "removed")
            log("kopia usunięta ręcznie — zostaje ekran Plasmy (make install włącza ekran Tide ponownie).")
            return "removed"
        if stamp.get("result") == "fallback" and stamp.get("fingerprint") == current:
            remove_copy()
            return "fallback"

    try:
        if STAGING.exists():
            shutil.rmtree(STAGING)
        STAGING.mkdir(parents=True)
        new = STAGING / SHELL_ID
        build(new, current)
        ok, output = check(new)
        if ok:
            replace_copy(new)
            write_stamp(current, "ok")
            log(f"kopia powłoki z ekranem blokady Tide gotowa: {COPY}")
            return "ok"
        remove_copy()
        write_stamp(current, "fallback")
        log("ekran Tide nie przeszedł sprawdzenia w greeterze — kopia usunięta, działa ekran Plasmy.")
        for line in output.strip().splitlines()[-15:]:
            log(f"  greeter: {line}")
        return "fallback"
    except BaseException:
        # Przy każdym nieoczekiwanym błędzie najbezpieczniej zostawić sam pakiet systemowy.
        remove_copy()
        raise
    finally:
        shutil.rmtree(STAGING, ignore_errors=True)


def remove() -> None:
    """Odinstalowanie: kopia (tylko nasza) i stan synchronizacji."""
    remove_copy()
    shutil.rmtree(STAGING, ignore_errors=True)
    STAMP.unlink(missing_ok=True)


def status() -> str:
    stamp = read_json(STAMP)
    if COPY.exists():
        owner = "Tide" if is_ours(COPY) else "nie Tide"
        state = f"kopia powłoki: {COPY} ({owner})"
    else:
        state = "kopia powłoki: brak (ekran blokady Plasmy)"
    return f"{state}\nostatnia synchronizacja: {stamp.get('result', '—')} {stamp.get('time', '')}".rstrip()


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "sync"
    if command == "sync":
        result = sync(force="--force" in argv[2:])
        if result not in ("up-to-date",):
            log(f"wynik: {result}")
        return 0
    if command == "remove":
        remove()
        return 0
    if command == "status":
        print(status())
        return 0
    print(__doc__.splitlines()[2], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
