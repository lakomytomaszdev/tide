"""Skróty tylko w aktywności Stream (PT-38, decyzja 81): Pause — mikrofon, Meta+Pause — ekran prywatności,
Meta+B — BRB i powrót. Wołają CLI `kamp` rdzenia Streamer (kamp mic.toggle, privacy.toggle, brb.toggle).

KDE nie ma skrótów przypisanych do aktywności, a skrót zarejestrowany na stałe (np. skryptem KWin) łapie
klawisz także w pracy. Dlatego usługa rejestruje skróty w KGlobalAccel (org.kde.kglobalaccel, ten sam
rejestr co Ustawienia systemowe → Skróty) dopiero po wejściu do aktywności Stream, a po wyjściu je usuwa:
w pracy klawiszy nie ma w rejestrze i działają dokładnie jak dziś.

Klawisz zajęty przez inną akcję (dziś Meta+B — „Przełącz profil zasilania”, org_kde_powerdevil) jest na czas
Streamu odbierany tamtej akcji, a po powrocie do pracy oddawany dokładnie: zanim cokolwiek się zmieni,
pełna lista klawiszy tamtej akcji trafia do $XDG_STATE_HOME/tide/stream-shortcuts.json. Powrót ustawia
zapisaną listę, jeśli tamta akcja ma dokładnie to, co jej zostawiliśmy; gdy ktoś zmienił ją w trakcie
Streamu — tylko dopisuje odebrany klawisz. Plik znika po oddaniu wszystkiego, więc jego obecność przy starcie
usługi (zabita w Stream, wylogowanie bez zatrzymania) oznacza „oddaj klawisze”, gdy bieżąca aktywność nie jest
Streamem. W pracy usługa niczego w rejestrze nie zmienia, jeśli nie ma czego oddawać. Gdy plik zginął,
a nasze skróty dalej są w rejestrze, klawisz wraca do akcji, której domyślne skróty (defaultShortcut) go
zawierają — kandydatów podaje kglobalshortcutsrc (tylko odczyt).

Czy bieżąca aktywność to Stream, mówi jeden sygnał (current_activity.CurrentActivity.changed, podpięty
w app.py). Wywołania KGlobalAccel idą w jednym wątku roboczym, po kolei, więc nie zatrzymują pętli usługi;
każde zadanie bierze ostatni żądany stan, więc kolejność zdarzeń nie ma znaczenia. Błąd KGlobalAccel
(np. KWin jeszcze nie wstał) to ponowienie co retry_ms.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QMetaType, QObject, QProcess, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusArgument, QDBusConnection, QDBusMessage

COMPONENT = "tide_stream"
COMPONENT_LABEL = "Tide · Stream"
META = 0x10000000          # Qt::MetaModifier
KEY_PAUSE = 0x01000008     # Qt::Key_Pause
KEY_B = 0x42               # Qt::Key_B
SET_PRESENT, NO_AUTOLOADING = 2, 4   # KGlobalAccel::SetShortcutFlag

ACCEL_SERVICE = "org.kde.kglobalaccel"
ACCEL_PATH = "/kglobalaccel"
ACCEL_IFACE = "org.kde.KGlobalAccel"
COMPONENT_IFACE = "org.kde.kglobalaccel.Component"


@dataclass(frozen=True)
class Shortcut:
    name: str        # akcja w komponencie tide_stream
    keys: str        # zapis klawisza jak w ściądze (widgets/lib/stream.js SHORTCUTS)
    key: int         # kod Qt (klawisz | modyfikatory)
    command: str     # komenda rdzenia dla `kamp`
    label: str       # opis w Ustawieniach systemowych → Skróty


SHORTCUTS = (
    Shortcut("mic", "Pause", KEY_PAUSE, "mic.toggle", "Stream: mikrofon"),
    Shortcut("privacy", "Meta+Pause", META | KEY_PAUSE, "privacy.toggle", "Stream: ekran prywatności"),
    Shortcut("brb", "Meta+B", META | KEY_B, "brb.toggle", "Stream: BRB i powrót"),
)


def action_id(shortcut: Shortcut) -> list[str]:
    return [COMPONENT, shortcut.name, COMPONENT_LABEL, shortcut.label]


def component_path(component: str = COMPONENT) -> str:
    return "/component/" + "".join(c if c.isalnum() or c == "_" else "_" for c in component)


class AccelError(Exception):
    """KGlobalAccel nie odpowiedział albo odrzucił wywołanie."""


# --- rejestr skrótów ----------------------------------------------------------------------------

def _ints(values: list[int]) -> QDBusArgument:
    arg = QDBusArgument()
    arg.beginArray(QMetaType.Type.Int.value)
    for value in values:
        arg.add(int(value), QMetaType.Type.Int.value)
    arg.endArray()
    return arg


def _strings(values: list[str]) -> QDBusArgument:
    return QDBusArgument(list(values), QMetaType.Type.QStringList.value)


class KGlobalAccel:
    """Klient org.kde.KGlobalAccel na tej samej magistrali co usługa (wywołania z kodami Qt jako `ai`)."""

    def __init__(self, bus: QDBusConnection | None = None, timeout_ms: int = 5000):
        self.bus = bus or QDBusConnection.sessionBus()
        self.timeout_ms = timeout_ms

    def _call(self, member: str, *args):
        msg = QDBusMessage.createMethodCall(ACCEL_SERVICE, ACCEL_PATH, ACCEL_IFACE, member)
        msg.setArguments(list(args))
        reply = self.bus.call(msg, timeout=self.timeout_ms)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            raise AccelError(f"{member}: {reply.errorMessage() or reply.errorName() or 'brak odpowiedzi'}")
        values = reply.arguments()
        return values[0] if values else None

    def owner(self, key: int) -> list[str]:
        """Akcja z klawiszem `key` ([komponent, akcja, nazwa komponentu, opis]) albo []."""
        return [str(v) for v in (self._call("action", QDBusArgument(int(key), QMetaType.Type.Int.value)) or [])]

    def keys(self, action: list[str]) -> list[int]:
        return [int(k) for k in (self._call("shortcut", _strings(action)) or []) if int(k)]

    def default_keys(self, action: list[str]) -> list[int]:
        return [int(k) for k in (self._call("defaultShortcut", _strings(action)) or []) if int(k)]

    def set_foreign(self, action: list[str], keys: list[int]) -> None:
        self._call("setForeignShortcut", _strings(action), _ints(keys))

    def register(self, action: list[str], keys: list[int]) -> list[int]:
        self._call("doRegister", _strings(action))
        got = self._call("setShortcut", _strings(action), _ints(keys),
                         QDBusArgument(SET_PRESENT | NO_AUTOLOADING, QMetaType.Type.UInt.value))
        return [int(k) for k in (got or [])]

    def unregister(self, component: str, name: str) -> bool:
        return bool(self._call("unregister", component, name))


# --- logika: wejście do Streamu i powrót ---------------------------------------------------------

def default_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "tide" / "stream-shortcuts.json"


def default_shortcuts_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "kglobalshortcutsrc"


def default_owners(keys: str, path: Path | None = None) -> list[list[str]]:
    """Akcje, których domyślne skróty (drugie pole wpisu w kglobalshortcutsrc) zawierają `keys` — kandydaci
    do oddania klawisza po utracie pliku stanu. Tylko odczyt."""
    found, group = [], ""
    try:
        lines = (path or default_shortcuts_file()).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
            continue
        name, sep, value = line.partition("=")
        if not sep or name.startswith("_k_") or group == COMPONENT:
            continue
        fields = value.split(",")
        defaults = [k.strip() for k in fields[1].split("\t")] if len(fields) > 1 else []
        if keys in defaults:
            found.append([group, name, "", ""])
    return found


def key_text(key: int) -> str:
    for shortcut in SHORTCUTS:
        if shortcut.key == key:
            return shortcut.keys
    return str(key)


class StreamShortcuts:
    """Rejestracja skrótów Stream w KGlobalAccel i oddawanie zajętych klawiszy (bez Qt — testy na atrapie)."""

    def __init__(self, accel, state_path: Path | None = None, owners=default_owners):
        self.accel = accel
        self.state_path = Path(state_path) if state_path is not None else default_state_path()
        self.owners = owners      # klawisz (zapis „Meta+B”) → akcje z nim w domyślnych skrótach
        self.active = False

    # stan na dysku --------------------------------------------------------------------------

    def taken(self) -> list[dict]:
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return []
        items = data.get("taken") if isinstance(data, dict) else None
        return [t for t in items if isinstance(t, dict)] if isinstance(items, list) else []

    def _save(self, taken: list[dict]) -> None:
        if not taken:
            self.state_path.unlink(missing_ok=True)
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"taken": taken}, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.state_path)

    def _ours(self, owner: list[str]) -> bool:
        return len(owner) >= 2 and owner[0] == COMPONENT

    # przejścia ------------------------------------------------------------------------------

    def enter(self) -> list[str]:
        """Wejście do Streamu: odbiera klawisze innym akcjom (z zapisem) i rejestruje skróty Stream."""
        notes = []
        taken = self.taken()
        for shortcut in SHORTCUTS:
            owner = self.accel.owner(shortcut.key)
            if not owner or self._ours(owner):
                continue
            if not any(t.get("key") == shortcut.key for t in taken):
                taken.append({"key": shortcut.key, "keyText": shortcut.keys, "action": owner,
                              "keys": self.accel.keys(owner)})
                self._save(taken)  # zapis przed zmianą — po awarii da się oddać dokładnie
            self.accel.set_foreign(owner, [k for k in self.accel.keys(owner) if k != shortcut.key])
            notes.append(f"{shortcut.keys}: na czas Streamu zabrany akcji „{owner[3] if len(owner) > 3 else owner[1]}”")
        for shortcut in SHORTCUTS:
            got = self.accel.register(action_id(shortcut), [shortcut.key])
            if shortcut.key not in got:
                notes.append(f"{shortcut.keys}: KGlobalAccel nie przypisał klawisza (zajęty?)")
        self.active = True
        return notes

    def needs_leave(self) -> bool:
        """Czy w pracy jest coś do sprzątnięcia: zapisane klawisze do oddania albo nasze skróty w rejestrze."""
        if self.active or self.taken():
            return True
        return any(self._ours(self.accel.owner(s.key)) for s in SHORTCUTS)

    def leave(self) -> list[str]:
        """Powrót do pracy: usuwa skróty Stream i oddaje odebrane klawisze."""
        notes = []
        ours = [s for s in SHORTCUTS if self._ours(self.accel.owner(s.key))]
        for shortcut in SHORTCUTS:
            self.accel.unregister(COMPONENT, shortcut.name)
        left = []
        taken = self.taken()
        recorded = {t.get("key") for t in taken}
        for shortcut in ours:
            if shortcut.key not in recorded:
                notes += self._give_back_default(shortcut)
        for item in taken:
            key, action, saved = item.get("key"), item.get("action") or [], list(item.get("keys") or [])
            current = self.accel.keys(action)
            if key in current:
                continue  # już oddany (np. przez użytkownika)
            free = self.accel.owner(key)
            if free and free[:2] != action[:2]:
                notes.append(f"{item.get('keyText')}: zajęty przez „{free[1]}” — nie oddany akcji „{action[1]}”")
                left.append(item)
                continue
            if current == [k for k in saved if k != key]:
                self.accel.set_foreign(action, saved)
            else:
                self.accel.set_foreign(action, current + [key])
                notes.append(f"{item.get('keyText')}: akcja „{action[1]}” zmieniła się w trakcie Streamu — "
                             "dopisany tylko oddany klawisz")
            if key not in self.accel.keys(action):
                notes.append(f"{item.get('keyText')}: nie udało się oddać akcji „{action[1]}”")
                left.append(item)
        self._save(left)
        self.active = False
        return notes

    def _give_back_default(self, shortcut: Shortcut) -> list[str]:
        """Plik stanu zginął, a nasz skrót był w rejestrze: klawisz wraca do akcji, która ma go w domyślnych."""
        if self.accel.owner(shortcut.key):
            return []
        for action in self.owners(shortcut.keys):
            current = self.accel.keys(action)
            if shortcut.key in self.accel.default_keys(action) and shortcut.key not in current:
                self.accel.set_foreign(action, current + [shortcut.key])
                return [f"{shortcut.keys}: bez zapisu stanu — oddany akcji „{action[1]}” (ma go w domyślnych)"]
        return []

    def command(self, name: str) -> str | None:
        return next((s.command for s in SHORTCUTS if s.name == name), None)


# --- usługa: zmiana aktywności, naciśnięcia, kamp ------------------------------------------------

def default_kamp() -> str | None:
    """CLI rdzenia: $TIDE_KAMP (pusty — wyłączone), PATH albo ~/.local/bin/kamp (usługa systemd nie ma
    ~/.local/bin w PATH)."""
    value = os.environ.get("TIDE_KAMP")
    if value is not None:
        return value or None
    found = shutil.which("kamp")
    local = os.path.expanduser("~/.local/bin/kamp")
    return found or (local if os.access(local, os.X_OK) else None)


class KampRunner(QObject):
    """`kamp <komenda>` w osobnym procesie; done(kod, wyjście). Kody kamp: 0, 1 odmowa, 2 rdzeń nie działa."""

    def __init__(self, kamp: str | None, parent=None):
        super().__init__(parent)
        self.kamp = kamp

    def __call__(self, command: str, done) -> None:
        if not self.kamp:
            done(127, "brak CLI kamp (rdzeń Streamer niezainstalowany?)")
            return
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        def finished(code, status):
            output = bytes(proc.readAll()).decode("utf-8", "replace").strip()
            proc.deleteLater()
            done(code if status == QProcess.ExitStatus.NormalExit else 1, output)

        def failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                proc.deleteLater()
                done(127, f"nie udało się uruchomić {self.kamp}")

        proc.finished.connect(finished)
        proc.errorOccurred.connect(failed)
        proc.start(self.kamp, [command])


def log(message: str) -> None:
    print(f"tide-daemon: skróty Stream: {message}", flush=True)


class StreamShortcutsMonitor(QObject):
    """Skróty według sygnału „czy Stream” (stream_changed) i naciśnięcia (globalShortcutPressed).

    Rejestr KGlobalAccel zmienia jeden wątek roboczy, po kolei; zadanie bierze ostatni żądany stan."""

    _done = pyqtSignal(object)   # lista uwag albo wyjątek — z wątku roboczego do głównego

    def __init__(self, shortcuts: StreamShortcuts, runner=None, bus: QDBusConnection | None = None,
                 subscribe: bool = True, retry_ms: int = 5000, retries: int = 12, parent=None):
        super().__init__(parent)
        self.shortcuts = shortcuts
        self.runner = runner or KampRunner(default_kamp(), parent=self)
        self.bus = bus or QDBusConnection.sessionBus()
        self.subscribe = subscribe
        self.retries = retries
        self._want: bool | None = None      # None — jeszcze nic nie wiadomo o aktywności
        self._tries = 0
        self._stopped = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-stream-keys")
        self._retry = QTimer(self, singleShot=True, interval=retry_ms)
        self._retry.timeout.connect(self._submit)
        self._done.connect(self._on_done)

    def start(self) -> None:
        if self.subscribe and not self.bus.connect(ACCEL_SERVICE, component_path(), COMPONENT_IFACE,
                                                   "globalShortcutPressed", self.pressed):
            log("nie da się słuchać naciśnięć skrótów")

    def stop(self, timeout: float = 10.0) -> None:
        """Wylogowanie albo restart usługi: klawisze wracają do pracy (czeka na wątek najwyżej timeout s)."""
        self._retry.stop()
        if self.subscribe:
            self.bus.disconnect(ACCEL_SERVICE, component_path(), COMPONENT_IFACE, "globalShortcutPressed",
                                self.pressed)
        self._want = False
        self._stopped = True
        try:
            self._pool.submit(self._work).result(timeout)
        except Exception as exc:  # KGlobalAccel niedostępny przy zamykaniu — plik stanu odda klawisze później
            log(f"przy zatrzymaniu: {exc}")
        self._pool.shutdown(wait=False, cancel_futures=True)

    # zdarzenia ----------------------------------------------------------------------------------

    @pyqtSlot(bool)
    def stream_changed(self, stream: bool) -> None:
        if self._stopped:
            return
        self._want = bool(stream)
        self._tries = 0
        self._retry.stop()
        self._submit()

    @pyqtSlot()
    def activity_unknown(self) -> None:
        """ActivityManager nie odpowiada: do czasu odpowiedzi praca (oddaj klawisze, jeśli coś zostało)."""
        if self._want is None:
            self.stream_changed(False)
            self._want = None   # pierwsza odpowiedź ActivityManagera rozstrzygnie

    @pyqtSlot(str, str, "qlonglong")
    def pressed(self, component: str, name: str, _timestamp: int = 0) -> None:
        command = self.shortcuts.command(name)
        if component != COMPONENT or command is None or not self.shortcuts.active or self._want is not True:
            return

        def done(code: int, output: str) -> None:
            if code == 2:
                log(f"{command}: rdzeń Streamer nie działa")
            elif code != 0:
                log(f"{command}: kod {code}{': ' + output if output else ''}")

        self.runner(command, done)

    # wnętrze -----------------------------------------------------------------------------------

    def _submit(self) -> None:
        if self._stopped:
            return
        def run():
            try:
                result = self._work()
            except Exception as exc:
                result = exc
            self._done.emit(result)
        self._pool.submit(run)

    def _work(self) -> list[str]:
        want = self._want
        if want:
            return self.shortcuts.enter()
        if self.shortcuts.needs_leave():
            return self.shortcuts.leave()
        return []  # praca: rejestr skrótów bez zmian

    @pyqtSlot(object)
    def _on_done(self, result) -> None:
        if isinstance(result, Exception):
            log(str(result))
            self._tries += 1
            if not self._stopped and self._tries < self.retries:
                self._retry.start()
            return
        for note in result:
            log(note)
