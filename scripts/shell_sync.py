"""Kopia powłoki Plasmy z ekranem blokady Tide i jej synchronizacja z pakietem systemowym.

Użycie: python3 shell_sync.py sync [--force] | remove | status | variant [<nazwa>]

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

Warianty ekranu blokady (decyzja 77): obok ekranu Tide (wariant „mist”, pakiet org.tide.desktop)
mogą być zainstalowane inne, np. ekran aktywności Stream w $XDG_DATA_HOME/tide/lockscreens/streamer
(pakiet powłoki z samym lockscreen/, jak org.tide.desktop). Kopia ma wtedy wszystkie warianty,
każdy sprawdzony greeterem przy składaniu:
- contents/lockscreen — wariant aktywny (ten czyta greeter przy blokadzie),
- contents/lockscreen-variants/<nazwa> — pozostałe.
Każdy katalog wariantu ma plik VARIANT_FILE z nazwą. `variant <nazwa>` zamienia katalog aktywny
z wybranym jednym wywołaniem renameat2(RENAME_EXCHANGE) — atomowo, bez przebudowy i bez greetera
(ułamek milisekundy), a powtórzenie nic nie zmienia. Greeter startuje przy każdej blokadzie,
więc wybór działa od następnej. Ponowne składanie kopii (aktualizacja Plasmy, make install)
zachowuje wybrany wariant; wariant, który nie przeszedł sprawdzenia albo zniknął, wypada,
a aktywny wraca wtedy do „mist”.

Skrypt jest samodzielny (bez importów z repozytorium), bo instalacja kopiuje go do
$XDG_DATA_HOME/tide/shell-sync.py i pulpit nie zależy od katalogu z repozytorium.
"""
from __future__ import annotations

import ctypes
import errno
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
FORMAT = 2  # zmiana sposobu składania kopii wymusza jej odtworzenie (2: warianty ekranu blokady)
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
# Warianty ekranu blokady poza Tide (Mist): <nazwa>/contents/lockscreen, poza plasma/shells/.
VARIANTS = DATA / "tide" / "lockscreens"
DEFAULT_VARIANT = "mist"
VARIANT_FILE = ".tide-lock-variant"  # w katalogu ekranu; QML i KPackage go nie czytają
ACTIVE = COPY / "contents" / "lockscreen"
INACTIVE = COPY / "contents" / "lockscreen-variants"


class LockVariantError(RuntimeError):
    pass


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
        name = directory.parents[1].name if directory.name == "lockscreen" else directory.name
        digest.update(f"[{name}/{directory.name}]\n".encode())
        for path in sorted(directory.rglob("*")):
            rel = path.relative_to(directory).as_posix()
            if path.is_symlink():
                digest.update(f"L {rel} {os.readlink(path)}\n".encode())
            elif path.is_file():
                digest.update(f"F {rel} {hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()


def variant_sources() -> dict[str, Path]:
    """Katalogi lockscreen/ wszystkich zainstalowanych wariantów: {"mist": …, "streamer": …}."""
    sources = {DEFAULT_VARIANT: TIDE_LOCKSCREEN}
    if VARIANTS.is_dir():
        for package in sorted(VARIANTS.iterdir()):
            lockscreen = package / "contents" / "lockscreen"
            if package.name != DEFAULT_VARIANT and not package.name.startswith(".") and lockscreen.is_dir():
                sources[package.name] = lockscreen
    return sources


def sources_fingerprint() -> str:
    sources = variant_sources()
    others = [sources[name] for name in sources if name != DEFAULT_VARIANT]
    return fingerprint(SYSTEM_SHELL, TIDE_LOCKSCREEN, *others)


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


def copy_variant(source: Path, target: Path, name: str) -> None:
    """Katalog lockscreen/ wariantu z licencją pakietu i znacznikiem nazwy."""
    shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    notice = source.parents[1] / "NOTICE"  # licencja i pochodzenie ekranu
    if notice.is_file():
        shutil.copy2(notice, target / "NOTICE")
    (target / VARIANT_FILE).write_text(f"{name}\n")


def distinct_mtimes(directories: list[Path]) -> None:
    """Pliki o tej samej ścieżce i innej treści w różnych wariantach dostają różne czasy modyfikacji.

    Pamięć podręczna QML greetera (qmlcache) rozpoznaje skompilowany plik po ścieżce i czasie
    modyfikacji źródła (z dokładnością do ms). Po przełączeniu wariantu pod tą samą ścieżką
    (np. lockscreen/tide/Tokens.qml) jest inna treść — równy czas oznaczałby stare tokeny.
    """
    seen: dict[str, list[tuple[bytes, int]]] = {}
    for directory in directories:
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(directory).as_posix()
            content = hashlib.sha256(path.read_bytes()).digest()
            mtime = path.stat().st_mtime_ns // 1_000_000
            taken = seen.setdefault(rel, [])
            while any(ms == mtime and digest != content for digest, ms in taken):
                mtime += 1000  # o sekundę później, aż czas będzie wolny
            if mtime != path.stat().st_mtime_ns // 1_000_000:
                os.utime(path, ns=(path.stat().st_atime_ns, mtime * 1_000_000))
            taken.append((content, mtime))


def build(target: Path, fingerprint_: str) -> None:
    """Pełna kopia pakietu systemowego z contents/lockscreen z ekranu Tide i pozostałymi wariantami obok."""
    shutil.copytree(SYSTEM_SHELL, target, symlinks=True)
    lockscreen = target / "contents" / "lockscreen"
    if lockscreen.exists():
        shutil.rmtree(lockscreen)
    sources = variant_sources()
    copy_variant(TIDE_LOCKSCREEN, lockscreen, DEFAULT_VARIANT)
    copied = [lockscreen]
    for name, source in sources.items():
        if name != DEFAULT_VARIANT:
            copy_variant(source, target / "contents" / "lockscreen-variants" / name, name)
            copied.append(target / "contents" / "lockscreen-variants" / name)
    distinct_mtimes(copied)
    (target / MARKER).write_text(json.dumps({
        "fingerprint": fingerprint_,
        "system": str(SYSTEM_SHELL),
        "lockscreen": str(TIDE_LOCKSCREEN),
        "variants": {name: str(path) for name, path in sources.items()},
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


def _variant_name(directory: Path) -> str | None:
    try:
        return (directory / VARIANT_FILE).read_text().strip() or None
    except OSError:
        return None


def active_variant(copy: Path) -> str:
    """Wariant aktywny w kopii; kopia sprzed wariantów (bez znacznika) ma ekran Tide, czyli „mist”."""
    return _variant_name(copy / "contents" / "lockscreen") or DEFAULT_VARIANT


def _inactive(copy: Path) -> dict[str, Path]:
    """Nieaktywne warianty po nazwie ze znacznika (nie z nazwy katalogu — ta może być stara)."""
    directory = copy / "contents" / "lockscreen-variants"
    found = {}
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            name = _variant_name(path)
            if name:
                found[name] = path
    return found


_libc = None


def exchange(a: Path, b: Path) -> None:
    """Atomowa zamiana dwóch katalogów (renameat2 z RENAME_EXCHANGE, Linux ≥ 3.15).

    Gdy system plików tego nie umie, zamiana przez nazwę tymczasową: przez chwilę (dwa
    rename()) aktywnego katalogu nie ma — greeter uruchomiony dokładnie wtedy pokazałby
    awaryjny ekran KDE, który też odblokowuje.
    """
    global _libc
    try:
        if _libc is None:
            _libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = _libc.renameat2
    except (OSError, AttributeError):
        renameat2 = None
    if renameat2 is not None:
        at_fdcwd, rename_exchange = -100, 2
        if renameat2(at_fdcwd, os.fsencode(a), at_fdcwd, os.fsencode(b), rename_exchange) == 0:
            return
        err = ctypes.get_errno()
        if err not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
            raise OSError(err, os.strerror(err), str(a), None, str(b))
    spare = a.with_name(a.name + ".tide-swap")
    a.rename(spare)
    b.rename(a)
    spare.rename(b)


def switch_variant(copy: Path, name: str) -> bool:
    """Ustawia wariant aktywny w kopii. False, gdy takiego wariantu w niej nie ma."""
    active = copy / "contents" / "lockscreen"
    if active_variant(copy) == name:
        return True
    candidates = _inactive(copy)
    if name not in candidates:
        return False
    previous = active_variant(copy)
    exchange(active, candidates[name])
    # Katalog, w którym był wybrany wariant, ma teraz poprzedni — porządkujemy nazwę.
    holder = candidates[name]
    wanted = holder.with_name(previous)
    if holder != wanted and not wanted.exists():
        holder.rename(wanted)
    return True


def lock_variant() -> str:
    """Bieżący wariant ekranu blokady: „mist”, „streamer”…; "" bez kopii Tide (ekran Plasmy z systemu)."""
    if not COPY.is_dir() or not is_ours(COPY):
        return ""
    return active_variant(COPY)


def _complete(lockscreen: Path) -> bool:
    """Ekran wariantu ma QML i wszystkie obrazy tła wymienione w tide/Backdrop.qml."""
    backdrop = lockscreen / "tide" / "Backdrop.qml"
    if not (lockscreen / "LockScreen.qml").is_file() or not backdrop.is_file():
        return False
    prefix = "readonly property var outputs: "
    for line in backdrop.read_text().splitlines():
        line = line.strip()
        if line.startswith(prefix):
            try:
                entries = json.loads(line[len(prefix):])
            except ValueError:
                return False
            files = [e.get(k) for e in entries for k in ("awake", "sleep") if e.get(k)]
            return all((lockscreen / "tide" / f).is_file() for f in files)
    return False


def lock_variants() -> set[str]:
    """Warianty gotowe do nałożenia: w kopii Tide, z QML i obrazami tła; bez kopii — pusty zbiór."""
    if not COPY.is_dir() or not is_ours(COPY):
        return set()
    found = {active_variant(COPY)} if _complete(ACTIVE) else set()
    return found | {name for name, path in _inactive(COPY).items() if _complete(path)}


def apply_lock_variant(name: str) -> None:
    """Przełącza ekran blokady na wariant `name` (od następnej blokady). Idempotentne.

    Nie przebudowuje kopii i nie uruchamia greetera — warianty są w kopii gotowe i sprawdzone.
    Gdy wariantu nie ma (kopii Tide nie ma, wariant nie jest zainstalowany albo nie przeszedł
    sprawdzenia), zgłasza LockVariantError i niczego nie zmienia.
    """
    if not COPY.is_dir() or not is_ours(COPY):
        raise LockVariantError(f"brak kopii powłoki Tide ({COPY}) — wariant {name!r} niedostępny")
    if active_variant(COPY) == name:
        return
    if not switch_variant(COPY, name):
        raise LockVariantError(f"wariantu {name!r} nie ma w kopii powłoki (są: "
                               f"{', '.join([active_variant(COPY), *_inactive(COPY)])})")


def check_variants(new: Path) -> None:
    """Sprawdza greeterem każdy wariant poza „mist” w kopii roboczej; niedziałający usuwa z kopii."""
    for name, path in _inactive(new).items():
        switch_variant(new, name)
        ok, output = check(new)
        switch_variant(new, DEFAULT_VARIANT)
        if not ok:
            shutil.rmtree(_inactive(new)[name])
            log(f"wariant ekranu blokady {name!r} nie przeszedł sprawdzenia w greeterze — pominięty.")
            for line in output.strip().splitlines()[-5:]:
                log(f"  greeter: {line}")
    variants = new / "contents" / "lockscreen-variants"
    if variants.is_dir() and not any(variants.iterdir()):
        variants.rmdir()


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

    current = sources_fingerprint()
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
            check_variants(new)
            # Wybór wariantu przeżywa przebudowę; wariant, którego już nie ma, oddaje miejsce Mist.
            selected = lock_variant() or DEFAULT_VARIANT
            if selected != DEFAULT_VARIANT and not switch_variant(new, selected):
                log(f"wariantu ekranu blokady {selected!r} nie ma w nowej kopii — aktywny: {DEFAULT_VARIANT}.")
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
    return (f"{state}\nwariant ekranu blokady: {lock_variant() or '— (ekran Plasmy)'}\n"
            f"ostatnia synchronizacja: {stamp.get('result', '—')} {stamp.get('time', '')}").rstrip()


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
    if command == "variant":
        if len(argv) > 2:
            try:
                apply_lock_variant(argv[2])
            except LockVariantError as error:
                log(str(error))
                return 1
        print(lock_variant())
        return 0
    print(__doc__.splitlines()[2], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
