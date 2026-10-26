"""Karta Konsoli dla nowego zadania: nowa karta w ostatnio używanym oknie albo nowe okno.

Konsole od wersji 2x uruchamia każde okno jako osobny proces (`org.kde.konsole-<pid>`), więc
`konsole --new-tab` otwiera nowe okno, a metody `runCommand`/`sendText` są domyślnie wyłączone
(„API DBus zagrażające bezpieczeństwu”). Dlatego karta powstaje przez
`org.kde.konsole.Window.newSession(profil, katalog)` z profilem „Tide: zadanie agenta”, którego
poleceniem jest launch.py — skrypt sam przejmuje zadanie dla katalogu karty. Tytuł karty
ustawiamy jak znacznik stanu (tabmarks): `setTabTitleFormat` i `setTitle`.

Okno docelowe: to, w którym ostatnio otwarto kartę — najmłodszy proces potomny któregoś procesu
konsole (z jego środowiska KONSOLE_DBUS_SERVICE i KONSOLE_DBUS_WINDOW). Gdy Konsoli nie ma,
profil nie jest zainstalowany albo Konsole odmówi — nowe okno: `konsole --workdir … -p tabtitle=…
-e python3 launch.py --task PLIK`.
"""
import os

from ..processes import ProcessTable, _read_env
from ..worktrees.launchers import PYTHON

PROFILE_NAME = "Tide: zadanie agenta"
PROFILE_FILE = "TideTask.profile"
LAUNCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch.py")


def profile_path(env=None) -> str:
    env = os.environ if env is None else env
    data = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local/share")
    return os.path.join(data, "konsole", PROFILE_FILE)


def pick_window(table: ProcessTable, read_env=None) -> dict | None:
    """Okno Konsoli, w którym ostatnio otwarto kartę: {service, window, pid} albo None."""
    read_env = read_env or (lambda pid: _read_env(f"/proc/{pid}"))
    konsoles = {p.pid for p in table.by_pid.values() if p.name == "konsole"}
    if not konsoles:
        return None
    children = [p for p in table.by_pid.values() if p.ppid in konsoles]
    children.sort(key=lambda p: (p.start_time, p.pid), reverse=True)
    for child in children:
        env = read_env(child.pid) or {}
        service, window = env.get("KONSOLE_DBUS_SERVICE"), env.get("KONSOLE_DBUS_WINDOW")
        if service and window:
            return {"service": service, "window": window, "pid": child.ppid}
    return None


class KonsoleWindows:
    """Wywołania D-Bus Konsoli (newSession, tytuł karty) — podmienialne w testach."""

    LIMIT_MS = 3000

    def __init__(self, bus=None):
        from PyQt6.QtDBus import QDBusConnection
        self.bus = bus or QDBusConnection.sessionBus()

    def _call(self, service, path, iface, member, *args):
        from PyQt6.QtDBus import QDBus, QDBusMessage
        msg = QDBusMessage.createMethodCall(service, path, iface, member)
        if args:
            msg.setArguments(list(args))
        reply = self.bus.call(msg, QDBus.CallMode.Block, self.LIMIT_MS)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return None
        values = reply.arguments()
        return values[0] if values else True

    def new_session(self, service: str, window: str, profile: str, directory: str) -> int | None:
        value = self._call(service, window, "org.kde.konsole.Window", "newSession", profile, directory)
        return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    def set_title(self, service: str, session: int, title: str) -> None:
        from PyQt6.QtCore import QMetaType
        from PyQt6.QtDBus import QDBusArgument

        def i(value):
            return QDBusArgument(value, QMetaType.Type.Int.value)
        path = f"/Sessions/{session}"
        # Jak w tabmarks: setTitle nadpisuje oba formaty, więc formaty idą po nim.
        self._call(service, path, "org.kde.konsole.Session", "setTitle", i(1), title)
        self._call(service, path, "org.kde.konsole.Session", "setTabTitleFormat", i(0), title)
        self._call(service, path, "org.kde.konsole.Session", "setTabTitleFormat", i(1), title)


class TaskTerminal:
    """Otwiera kartę dla planu zadania. open(plan) → "tab", "window" albo "" (nie udało się)."""

    def __init__(self, launchers, konsole=None, probe=None, activate=None, env=None, read_env=None):
        self.launchers = launchers
        self.konsole = konsole            # KonsoleWindows albo atrapa; None — bez kart w oknach
        self.probe = probe                # funkcja() → ProcessTable
        self.activate = activate          # funkcja(target) → bool (windows.activate)
        self.env = os.environ if env is None else env
        self.read_env = read_env

    def available(self) -> bool:
        return bool(self.launchers.terminal)

    def open(self, plan) -> str:
        if self._open_tab(plan):
            return "tab"
        argv = self.window_command(plan)
        return "window" if argv and self.launchers.spawn(argv, plan.cwd) else ""

    def window_command(self, plan) -> list[str]:
        if not self.launchers.terminal:
            return []
        return self.launchers._scoped([self.launchers.terminal, "--workdir", plan.cwd, "-p",
                                       f"tabtitle={plan.title}", "-e", PYTHON, "-s", LAUNCHER, "--task",
                                       plan.task_file])

    def _open_tab(self, plan) -> bool:
        if self.konsole is None or self.probe is None or not os.path.isfile(profile_path(self.env)):
            return False
        try:
            target = pick_window(self.probe(), self.read_env)
            if target is None:
                return False
            session = self.konsole.new_session(target["service"], target["window"], PROFILE_NAME, plan.cwd)
            if session is None:
                return False
            self.konsole.set_title(target["service"], session, plan.title)
            if self.activate:
                self.activate({"pids": [target["pid"]], "label": "Konsole",
                               "konsole": {"service": target["service"], "window": target["window"],
                                           "session": f"/Sessions/{session}"}})
            return True
        except Exception as exc:  # karta to wygoda — zawsze zostaje nowe okno
            print(f"tide-daemon: zadania: karta Konsoli: {exc!r}", flush=True)
            return False
