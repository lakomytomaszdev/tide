"""Codex Desktop (aplikacja ChatGPT): otwieranie wątku odnośnikiem codex://threads/<id>.

Samo podniesienie okna aplikacji pokazuje ostatnio oglądaną rozmowę, a nie tę z karty sesji.
Aplikacja obsługuje schemat `codex:` (MimeType=x-scheme-handler/codex w chatgpt.desktop),
a `chatgpt "codex://threads/<id>"` przekazane działającej instancji przełącza ją na ten wątek.
Identyfikator to id sesji z session_meta (UUID z nazwy pliku rollout). Wątek podagenta nie
ma własnej rozmowy w aplikacji („Nie znaleziono konwersacji”), więc zawsze prowadzimy do
wątku głównego — ustala go AgentScanner (agents/scanner.py).

Program bierzemy z linii Exec pliku .desktop (jak menu i xdg-open), bez powłoki: argumenty
idą listą, a odnośnik podstawiamy w miejsce %U/%u. Usługa systemd użytkownika może nie mieć
WAYLAND_DISPLAY/DISPLAY (zależy od tego, czy sesja zaimportowała środowisko), więc brakujące
zmienne graficzne dobieramy z `systemctl --user show-environment`. Program startuje przez
`systemd-run --user --scope` (jak Konsole i Rider z worktrees), żeby restart usługi nie
zamknął aplikacji, gdy to my ją uruchomiliśmy.
"""
import os
import re
import shlex
import shutil
import subprocess
import threading
import time

SCHEME_MIME = "x-scheme-handler/codex"
DESKTOP_IDS = ("chatgpt.desktop",)
# Nazwy procesu głównego okna aplikacji (comm) — do podniesienia okna przez KWin.
WINDOW_PROCESSES = ("ChatGPT",)
LABEL = "Codex"
# Zmienne potrzebne programowi graficznemu; uzupełniamy tylko brakujące.
GRAPHICAL_ENV = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                 "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "XDG_SESSION_DESKTOP", "KDE_FULL_SESSION")
# Identyfikator wątku: UUID (Codex używa UUID v7). Nic innego nie trafia do odnośnika.
THREAD_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
OVERRIDE_ENV = "TIDE_CODEX_DESKTOP"  # ścieżka pliku .desktop zamiast szukania w katalogach XDG
PROBE_ID ="00000000-0000-4000-8000-000000000000"  # do sprawdzenia, czy polecenie da się złożyć
CACHE_S = 60.0  # tyle pamiętamy, gdzie jest plik .desktop (sessions() liczy się co 5 s)


def is_desktop(originator: str) -> bool:
    """Sesja z aplikacji Codex Desktop („Codex Desktop”, także warianty typu codex_work_desktop)."""
    return "desktop" in (originator or "").lower()


def thread_url(thread_id: str) -> str:
    """codex://threads/<id> albo pusty napis dla identyfikatora, który nie jest UUID."""
    return f"codex://threads/{thread_id}" if THREAD_ID.match(thread_id or "") else ""


def data_dirs(env) -> list[str]:
    """Katalogi danych XDG w kolejności pierwszeństwa (użytkownik przed systemem)."""
    home = env.get("HOME") or os.path.expanduser("~")
    user = env.get("XDG_DATA_HOME") or os.path.join(home, ".local/share")
    system = [d for d in (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d]
    return [user] + system


def read_entry(path: str) -> dict:
    """Klucze grupy [Desktop Entry] (bez wersji językowych). Pusty słownik przy błędzie."""
    entry, inside = {}, False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("["):
                    inside = line == "[Desktop Entry]"
                    continue
                if inside and "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    entry.setdefault(key.strip(), value.strip())
    except OSError:
        return {}
    return entry


def handles_scheme(entry: dict) -> bool:
    return SCHEME_MIME in [m.strip() for m in entry.get("MimeType", "").split(";")]


def find_handler(env) -> str:
    """Plik .desktop aplikacji obsługującej codex:. Najpierw znane nazwy (chatgpt.desktop),
    potem dowolny program z MimeType=x-scheme-handler/codex. Pusty napis, gdy nie ma."""
    dirs = [os.path.join(d, "applications") for d in data_dirs(env)]
    for name in DESKTOP_IDS:
        for d in dirs:
            path = os.path.join(d, name)
            entry = read_entry(path) if os.path.isfile(path) else {}
            if entry.get("Exec") and handles_scheme(entry) and entry.get("Hidden", "").lower() != "true":
                return path
    for d in dirs:
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith(".desktop"))
        except OSError:
            continue
        for name in names:
            entry = read_entry(os.path.join(d, name))
            if entry.get("Exec") and handles_scheme(entry) and entry.get("Hidden", "").lower() != "true":
                return os.path.join(d, name)
    return ""


def exec_argv(exec_line: str, url: str, path: str | None = None) -> list[str]:
    """Linia Exec z odnośnikiem w miejscu %U/%u/%F/%f (pozostałe kody pól znikają).

    Program musi dać się znaleźć (PATH albo ścieżka bezwzględna) — inaczej pusta lista.
    Bez kodu pola odnośnik idzie na koniec, jak robi to KIO.
    """
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        return []
    argv, placed = [], False
    for token in tokens:
        if token in ("%U", "%u", "%F", "%f"):
            if not placed:
                argv.append(url)
                placed = True
            continue
        if re.fullmatch(r"%[a-zA-Z]", token):
            continue  # %i, %c, %k i przestarzałe kody — bez znaczenia dla odnośnika
        argv.append(token.replace("%%", "%"))
    if not argv:
        return []
    program = argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0], path=path)
    if not program or not os.access(program, os.X_OK):
        return []
    argv[0] = program
    if not placed:
        argv.append(url)
    return argv


def parse_environment(text: str) -> dict:
    """Wyjście `systemctl --user show-environment` (KLUCZ=wartość, wartości ze znakami
    specjalnymi w postaci $'…'). Wartości z cudzysłowem rozpakowujemy tylko w prostych
    przypadkach — zmienne ekranu i magistrali są zwykłymi napisami."""
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if value.startswith("$'") and value.endswith("'"):
            value = value[2:-1].replace("\\'", "'").replace("\\\\", "\\")
        out[key] = value
    return out


def manager_environment(run=subprocess.run) -> dict:
    """Środowisko menedżera usług użytkownika. Pusty słownik przy błędzie."""
    try:
        result = run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_environment(result.stdout) if result.returncode == 0 else {}


def graphical_env(env, manager=manager_environment) -> dict:
    """Kopia env z dobranymi zmiennymi graficznymi, gdy brakuje ekranu (WAYLAND_DISPLAY/DISPLAY)."""
    out = dict(env)
    if out.get("WAYLAND_DISPLAY") or out.get("DISPLAY"):
        return out
    extra = manager()
    for key in GRAPHICAL_ENV:
        if not out.get(key) and extra.get(key):
            out[key] = extra[key]
    return out


class CodexApp:
    """Otwieranie wątków w Codex Desktop. Wszystko, co dotyka systemu, da się podmienić w testach."""

    def __init__(self, env=None, manager=manager_environment, popen=subprocess.Popen, clock=time.monotonic):
        self.env = dict(os.environ if env is None else env)
        self.manager = manager
        self.popen = popen
        self.clock = clock
        self._handler = None      # (ścieżka .desktop, chwila sprawdzenia)

    def handler(self) -> str:
        """Plik .desktop aplikacji (pamiętany przez CACHE_S — aplikację można doinstalować)."""
        if OVERRIDE_ENV in self.env:
            # Jawnie wskazany plik (testy, nietypowa instalacja); pusty albo brakujący = brak aplikacji.
            path = self.env[OVERRIDE_ENV]
            return path if path and os.path.isfile(path) else ""
        now = self.clock()
        if self._handler is None or now - self._handler[1] > CACHE_S:
            self._handler = (find_handler(self.env), now)
        return self._handler[0]

    def available(self) -> bool:
        return bool(self.command(PROBE_ID))

    def command(self, thread_id: str) -> list[str]:
        """Polecenie otwierające wątek (lista argumentów, bez powłoki) albo pusta lista."""
        url = thread_url(thread_id)
        path = self.handler()
        if not url or not path:
            return []
        argv = exec_argv(read_entry(path).get("Exec", ""), url, self.env.get("PATH", ""))
        if not argv:
            return []
        scope = shutil.which("systemd-run", path=self.env.get("PATH", ""))
        if scope:
            argv = [scope, "--user", "--scope", "--collect", "--quiet", "--"] + argv
        return argv

    def open_thread(self, thread_id: str) -> bool:
        """Przekazuje aplikacji codex://threads/<id>. Nie czeka na jej zakończenie."""
        argv = self.command(thread_id)
        if not argv:
            return False
        home = self.env.get("HOME") or os.path.expanduser("~")
        try:
            proc = self.popen(argv, cwd=home if os.path.isdir(home) else "/", env=graphical_env(self.env, self.manager),
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              start_new_session=True)
        except OSError:
            return False
        # Zbieramy proces po zakończeniu, żeby nie zostawał zombie.
        threading.Thread(target=proc.wait, daemon=True).start()
        return True
