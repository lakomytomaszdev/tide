"""Przejście do okna sesji: terminal (albo aplikacja) w łańcuchu przodków procesu agenta.

Ustalanie jest najlepszym możliwym przybliżeniem. Gdy w przodkach nie ma znanego terminala
ani aplikacji, sesja po prostu nie jest klikalna. Wyjątek to Codex Desktop: wątek otwiera
odnośnik codex://threads/<id> (codex_app.py), a okno aplikacji podnosimy po nazwie procesu
(app_target), także gdy sesja nie ma procesu.

Aktywacja na Waylandzie idzie przez skrypt KWin ładowany przez D-Bus (org.kde.KWin /Scripting),
bo zwykła aplikacja nie może podnieść cudzego okna. Dla Konsole najpierw przełączamy kartę
(org.kde.konsole.Window.setCurrentSession), żeby okno pokazało właściwą sesję.

Kilka okien Konsole (PT-33). Jeden proces Konsole może mieć kilka okien (karta przeciągnięta poza
okno, „Nowe okno”), a wtedy wszystkie mają ten sam PID i KWin nie odróżni ich po procesie.
KONSOLE_DBUS_WINDOW ze środowiska agenta też nie pomaga: to okno, w którym karta powstała, więc po
przeniesieniu karty wskazuje inne albo nieistniejące okno. Dlatego:

- okno sesji ustalamy na żywo: wszystkie /Windows/* usługi Konsole i to, którego sessionList()
  zawiera numer karty (środowisko tylko wtedy, gdy wyliczenie się nie uda),
- okno w KWin rozpoznajemy po tytule: bieżąca karta przekazuje oknu swój wyświetlany tytuł, więc
  po setCurrentSession okno sesji ma tytuł karty, a pozostałe okna procesu — tytuły swoich
  bieżących kart (czytamy je z D-Bus). Okno, którego tytuł pokazuje inną kartę, nigdy nie jest
  wybierane,
- gdy inne okno tego procesu pokazuje kartę o identycznym tytule (np. dwa „● tide · master”),
  na chwilę dopisujemy do wyświetlanego tytułu karty znacznik MARKER — tak jak robi to tabmarks,
  z wstrzymaniem znacznika stanu dla tej karty — i przywracamy tytuł po RESTORE_MS. Skrypt KWin
  czeka na tytuł ze znacznikiem także wtedy, gdy dotrze on do KWin chwilę po starcie skryptu.

Ani Konsole, ani KWin nie wystawiają innego wspólnego identyfikatora okna (Konsole nie zna
internalId okna w KWin, a okna jednego procesu mają ten sam PID), więc tytuł jest jedynym
rozróżnieniem dostępnym z zewnątrz.
"""
import json
import os
import re
import tempfile

from .processes import ProcessTable

# Nazwy procesów (comm, maks. 15 znaków), które mają okno, do którego warto przejść.
WINDOW_OWNERS = {
    "konsole": "Konsole",
    "yakuake": "Yakuake",
    "kitty": "kitty",
    "alacritty": "Alacritty",
    "foot": "foot",
    "footclient": "foot",
    "wezterm-gui": "WezTerm",
    "ghostty": "Ghostty",
    "xterm": "XTerm",
    "gnome-terminal-": "Terminal GNOME",
    "ptyxis": "Ptyxis",
    "tilix": "Tilix",
    "terminator": "Terminator",
    "code": "VS Code",
    "codium": "VSCodium",
    "cursor": "Cursor",
    "zed-editor": "Zed",
    "ChatGPT": "Codex",
    "t3code": "T3 Code",
    "t3-code": "T3 Code",
}
SCRIPT_NAME = "tide-activate-window"
UNLOAD_MS = 2000   # skrypt wykonuje się od razu; sprzątamy go chwilę później
RESTORE_MS = 1000  # jak długo karta nosi znacznik, gdy tytuł okna trzeba było odróżnić
MARKER = " ⌖"      # dopisek do tytułu karty: odróżnia okno od innego okna z identycznym tytułem
CAPTION_SEPARATOR = " \u2014 "  # Qt dopisuje do tytułu okna „ — <nazwa aplikacji>”


def target_for(table: ProcessTable, pid: int) -> dict | None:
    """Cel aktywacji dla procesu agenta albo None, gdy nie da się go ustalić."""
    proc = table.get(pid)
    if not proc:
        return None
    chain = table.ancestors(pid)
    owner = next((p for p in chain if p.name in WINDOW_OWNERS), None)
    if not owner:
        return None
    pids = [p.pid for p in chain[: chain.index(owner) + 1]]
    target = {"pids": pids, "label": WINDOW_OWNERS[owner.name]}
    env = proc.env or {}
    if owner.name == "konsole" and all(env.get(k) for k in ("KONSOLE_DBUS_SERVICE", "KONSOLE_DBUS_WINDOW",
                                                            "KONSOLE_DBUS_SESSION")):
        target["konsole"] = {
            "service": env["KONSOLE_DBUS_SERVICE"],
            "window": env["KONSOLE_DBUS_WINDOW"],
            "session": env["KONSOLE_DBUS_SESSION"],
        }
    return target


def app_target(table: ProcessTable | None, names, label: str) -> dict | None:
    """Cel aktywacji dla okna aplikacji bez procesu sesji (np. Codex Desktop): główne procesy
    o podanych nazwach — te, których rodzic nie nazywa się tak samo (Electron ma dziesiątki
    procesów potomnych, a okno należy do głównego). None, gdy aplikacja nie działa."""
    if table is None:
        return None
    mains = [p for p in table.by_pid.values()
             if p.name in names and not (table.get(p.ppid) and table.get(p.ppid).name in names)]
    if not mains:
        return None
    mains.sort(key=lambda p: (p.start_time, p.pid))
    return {"pids": [p.pid for p in mains], "label": label}


def normalize(text: str) -> str:
    """Tytuł tak, jak porównuje go KWin (białe znaki zwinięte, bez spacji na końcach)."""
    return re.sub(r"\s+", " ", text or "").strip()


def shows(caption: str, title: str) -> bool:
    """Czy tytuł okna pokazuje tytuł karty (sam albo z dopiskiem „ — Konsola” od Qt)."""
    caption, title = normalize(caption), normalize(title)
    return bool(title) and (caption == title or caption.startswith(title + CAPTION_SEPARATOR))


def kwin_script(pids: list[int], want: str = "", avoid=(), marked: str = "") -> str:
    """Skrypt KWin: aktywuje okno należące do najbliższego z podanych procesów.

    want — wyświetlany tytuł karty, którą okno ma pokazywać (Konsole, po setCurrentSession),
    avoid — tytuły pokazywane przez inne okna tego samego procesu: takiego okna nie wybieramy,
    marked — tytuł karty ze znacznikiem, gdy want nie wystarcza do odróżnienia okien.
    Gdy od razu nie widać jednoznacznego okna (KWin dostaje nowy tytuł asynchronicznie),
    skrypt raz poprawia wybór, kiedy któreś okno procesu dostanie jednoznaczny tytuł.
    """
    return f"""// Wygenerowane przez tide-daemon: przejście do okna sesji agenta.
const pids = {json.dumps(pids)};
const want = {json.dumps(want)};
const avoid = {json.dumps(list(avoid))};
const marked = {json.dumps(marked)};
const separator = {json.dumps(CAPTION_SEPARATOR)};
function norm(text) {{ return String(text || "").replace(/\\s+/g, " ").trim(); }}
function shows(caption, title) {{
    const c = norm(caption), t = norm(title);
    return t !== "" && (c === t || c.indexOf(t + separator) === 0);
}}
// 3 — tytuł ze znacznikiem, 2 — tytuł karty, 1 — nieznany, 0 — okno pokazuje inną kartę.
function kind(w) {{
    if (marked && shows(w.caption, marked)) return 3;
    if (want && shows(w.caption, want)) return 2;
    for (const t of avoid) if (shows(w.caption, t)) return 0;
    return 1;
}}
const candidates = [];
for (const w of workspace.windowList()) {{
    if (w.normalWindow && pids.indexOf(w.pid) >= 0) candidates.push(w);
}}
function certain(w) {{
    const k = kind(w);
    if (marked) return k === 3;
    return k === 2 && candidates.filter(o => kind(o) === 2).length === 1;
}}
function raise(w) {{
    if (w.minimized) w.minimized = false;
    workspace.activeWindow = w;
}}
let best = null, bestScore = -1;
for (const w of candidates) {{
    const k = kind(w);
    if (k === 0) continue;
    const score = k * 1000 + 100 - pids.indexOf(w.pid);
    if (score > bestScore) {{ best = w; bestScore = score; }}
}}
if (best) raise(best);
if (want && !(best && certain(best))) {{
    let fixed = false;
    for (const w of candidates) {{
        w.captionChanged.connect(function () {{
            if (fixed || !certain(w)) return;
            fixed = true;
            if (w !== best) raise(w);
        }});
    }}
}}
"""


class KWinScripts:
    """Jednorazowy skrypt KWin przez org.kde.KWin /Scripting — podmienialny w testach."""

    def __init__(self, bus=None, later=None):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtDBus import QDBusConnection
        self.bus = bus or QDBusConnection.sessionBus()
        self.later = later or QTimer.singleShot

    def _call(self, member, *args):
        from PyQt6.QtDBus import QDBus, QDBusMessage
        msg = QDBusMessage.createMethodCall("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", member)
        if args:
            msg.setArguments(list(args))
        return self.bus.call(msg, QDBus.CallMode.Block, 2000)

    def run(self, source: str) -> bool:
        from PyQt6.QtDBus import QDBusMessage
        if not self.bus.isConnected():
            return False
        runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        script_dir = os.path.join(runtime, "tide")
        os.makedirs(script_dir, exist_ok=True)
        path = os.path.join(script_dir, "activate-window.js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        self._call("unloadScript", SCRIPT_NAME)
        reply = self._call("loadScript", path, SCRIPT_NAME)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return False
        self._call("start")
        self.later(UNLOAD_MS, lambda: self._call("unloadScript", SCRIPT_NAME))
        return True


def session_number(path: str) -> int:
    """Numer karty z /Sessions/<n>; -1, gdy ścieżka nie ma numeru."""
    try:
        return int(str(path).rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return -1


class Activator:
    """Przełącza kartę Konsole (jeśli dotyczy) i podnosi okno przez skrypt KWin.

    konsole: KonsoleTabs albo atrapa — windows(service), sessions(service, okno), current(service, okno),
      set_current(service, okno, n), title(service, n), read(klucz), apply(klucz, title=, local=, remote=),
    kwin: obiekt z run(źródło skryptu) -> bool,
    later: funkcja(ms, wywołanie) — opóźnione przywrócenie tytułu karty,
    guard: TabMarker albo None — hold(klucz)/release(klucz) wstrzymuje znacznik stanu tej karty,
      dopóki nosi ona nasz znacznik okna.
    """

    def __init__(self, konsole, kwin, later, guard=None):
        self.konsole = konsole
        self.kwin = kwin
        self.later = later
        self.guard = guard
        self.pending: dict = {}  # klucz karty → przywrócenie tytułu

    def activate(self, target: dict) -> bool:
        want, avoid, marked = "", [], ""
        konsole = target.get("konsole")
        if konsole and self.konsole is not None:
            # Poprzednie przejście do tej karty jeszcze nosi znacznik — najpierw oddajemy tytuł.
            self._give_back(f"{konsole['service']} {konsole['session']}")
            want, avoid, ambiguous = self._show_session(konsole)
            if ambiguous:
                marked = self._borrow(konsole, want)
        ok = self.kwin.run(kwin_script(target["pids"], want, avoid, marked))
        if marked:
            key = f"{konsole['service']} {konsole['session']}"
            self.later(RESTORE_MS, lambda: self._give_back(key))
        return ok

    def window_of(self, service: str, number: int, windows: list[str], fallback: str = "") -> str:
        """Okno, w którym karta jest teraz; okno ze środowiska tylko, gdy żadne okno jej nie zgłosi."""
        for window in windows:
            if number in (self.konsole.sessions(service, window) or []):
                return window
        return fallback

    def _show_session(self, konsole: dict) -> tuple[str, list[str], bool]:
        """Przełącza kartę we właściwym oknie. Zwraca (tytuł karty, tytuły innych okien procesu,
        czy któreś inne okno pokazuje identyczny tytuł)."""
        service = konsole["service"]
        number = session_number(konsole["session"])
        if number < 0:
            return "", [], False
        windows = self.konsole.windows(service) or []
        window = self.window_of(service, number, windows, konsole.get("window", ""))
        if window:
            self.konsole.set_current(service, window, number)
        want = self.konsole.title(service, number) or ""
        others = []
        for other in windows:
            if other == window:
                continue
            current = self.konsole.current(service, other)
            title = self.konsole.title(service, current) if current is not None else None
            if title:
                others.append(title)
        ambiguous = bool(want) and any(shows(title, want) or shows(want, title) for title in others)
        avoid = [title for title in others if not (shows(title, want) or shows(want, title))]
        return want, avoid, ambiguous

    def _borrow(self, konsole: dict, want: str) -> str:
        """Dopisuje znacznik do wyświetlanego tytułu karty; zwraca ten tytuł albo "" (nie wyszło)."""
        key = f"{konsole['service']} {konsole['session']}"
        current = self.konsole.read(key)
        if current is None:
            return ""
        marked = want + MARKER
        if self.guard is not None:
            self.guard.hold(key)
        # setTitle nadpisuje oba formaty, więc od razu przywracamy je w tym samym wywołaniu —
        # znacznik niesie tylko wyświetlany tytuł, a Konsole wróci do formatu przy aktywności.
        if not self.konsole.apply(key, title=marked, local=current["local"], remote=current["remote"]):
            if self.guard is not None:
                self.guard.release(key)
            return ""

        def give_back():
            now = self.konsole.read(key)
            if now is not None and now["title"] == marked:
                self.konsole.apply(key, title=current["title"], local=now["local"], remote=now["remote"])
            if self.guard is not None:
                self.guard.release(key)

        self.pending[key] = give_back
        return marked

    def _give_back(self, key: str) -> None:
        restore = self.pending.pop(key, None)
        if restore is not None:
            restore()

    def give_back_all(self) -> None:
        """Przywraca tytuły kart od razu (zamknięcie usługi przed upływem RESTORE_MS)."""
        for key in list(self.pending):
            self._give_back(key)


_activator: Activator | None = None
_guard = None


def default_activator() -> Activator:
    """Aktywator na magistrali sesji, tworzony przy pierwszym przejściu do okna."""
    global _activator
    if _activator is None:
        from PyQt6.QtCore import QTimer
        from .konsole import KonsoleTabs
        _activator = Activator(KonsoleTabs(), KWinScripts(), QTimer.singleShot, _guard)
    return _activator


def set_tab_guard(guard) -> None:
    """Znacznik stanu kart (tabmarks.TabMarker), który przejście do okna wstrzymuje na czas
    swojego znacznika tytułu."""
    global _guard
    _guard = guard
    if _activator is not None:
        _activator.guard = guard


def give_back_tabs() -> None:
    if _activator is not None:
        _activator.give_back_all()


def activate(target: dict) -> bool:
    """Przełącza kartę Konsole (jeśli dotyczy) i podnosi okno przez skrypt KWin."""
    return default_activator().activate(target)
