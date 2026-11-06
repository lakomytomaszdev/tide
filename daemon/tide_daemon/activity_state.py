"""Wygląd bieżącej aktywności w usłudze: stan dla widżetów i filtr powiadomień Stream (PT-36, decyzja 80).

ActivityState śledzi bieżącą aktywność (org.kde.ActivityManager: CurrentActivity przy starcie, potem sygnał
CurrentActivityChanged) i jej wygląd: przypisanie z $XDG_CONFIG_HOME/tide/activities.json (activity_look.py
bind), opis z katalogu wyglądów ($TIDE_ACTIVITY_LOOKS, domyślnie kopia silnika w
$XDG_DATA_HOME/tide/activity-look/theme/activities). Każda inna aktywność ma wygląd pracy — jak w silniku
(decyzja 78). Stan idzie przez D-Bus (/ActivityLook): kafelki pracy chowają się, gdy wygląd nie ma paneli
pracy (`"panels"` inne niż `"work"`).

NotificationFilter: gdy opis wyglądu ma `notifications.allow`, usługa trzyma inhibicję powiadomień
(org.freedesktop.Notifications.Inhibit — rozszerzenie Plasmy, ten sam stan co „Nie przeszkadzać”) i na ten
czas ustawia w plasmanotifyrc ShowPopupsInDndMode=true dla dozwolonych aplikacji ([Applications][<desktop
entry>]) i usług ([Services][<x-kde-appname>]). To ustawienie Plasmy jest globalne — działa przy każdym
„Nie przeszkadzać”, także przy tym włączonym w pracy do 2027 — więc trzymamy je tylko w Stream: poprzednie
wartości (także „brak klucza”) trafiają przed zmianą do $XDG_STATE_HOME/tide/stream-notifications.json
i wracają dokładnie przy wyjściu, przy zatrzymaniu usługi i przy starcie usługi po awarii. Inhibicję serwer
zwalnia też sam, gdy usługa znika z magistrali. Krytyczne powiadomienia Plasma pokazuje w tym trybie i tak
(CriticalInDndMode), reszta czeka w historii.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from PyQt6.QtCore import QMetaType, QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusArgument, QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)

AM_SERVICE = "org.kde.ActivityManager"
AM_PATH = "/ActivityManager/Activities"
AM_IFACE = "org.kde.ActivityManager.Activities"
NOTIFY = ("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications")
DEFAULT_LOOK = "work"
INHIBIT_ENTRY = "tide"  # tide.desktop — Plasma pokaże „Tide” jako tego, kto wstrzymuje powiadomienia
INHIBIT_REASON = "Aktywność Stream"
NOTIFYRC = "plasmanotifyrc"
KEY = "ShowPopupsInDndMode"
UNSET = "__tide_unset__"


def _xdg(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or Path.home() / default)


def default_looks_dir() -> Path:
    value = os.environ.get("TIDE_ACTIVITY_LOOKS")
    if value:
        return Path(value)
    return _xdg("XDG_DATA_HOME", ".local/share") / "tide" / "activity-look" / "theme" / "activities"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class ActivityState(QObject):
    """Bieżąca aktywność i jej wygląd; changed() po każdej zmianie."""

    changed = pyqtSignal()

    def __init__(self, bindings: Path | None = None, looks: Path | None = None, bus: QDBusConnection | None = None,
                 parent=None):
        super().__init__(parent)
        self.bindings = bindings or _xdg("XDG_CONFIG_HOME", ".config") / "tide" / "activities.json"
        self.looks = looks or default_looks_dir()
        self.bus = bus or QDBusConnection.sessionBus()
        self.activity = ""
        self.look = DEFAULT_LOOK
        self.description: dict = {}
        self._watchers: set[QDBusPendingCallWatcher] = set()

    def start(self) -> None:
        self.bus.connect(AM_SERVICE, AM_PATH, AM_IFACE, "CurrentActivityChanged", self.activity_changed)
        msg = QDBusMessage.createMethodCall(AM_SERVICE, AM_PATH, AM_IFACE, "CurrentActivity")
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(msg, 5000), self)
        self._watchers.add(watcher)
        watcher.finished.connect(lambda w: self._current(w))

    def stop(self) -> None:
        self.bus.disconnect(AM_SERVICE, AM_PATH, AM_IFACE, "CurrentActivityChanged", self.activity_changed)

    def _current(self, watcher: QDBusPendingCallWatcher) -> None:
        self._watchers.discard(watcher)
        reply = QDBusPendingReply(watcher).reply()
        if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments() and not self.activity:
            self.set_activity(str(reply.arguments()[0]))
        else:
            self.set_activity(self.activity)  # bez ActivityManagera: wygląd pracy (i sprzątanie po awarii)

    @pyqtSlot(str)
    def activity_changed(self, activity: str) -> None:
        self.set_activity(activity)

    def set_activity(self, activity: str) -> None:
        # Przypisania i opisy czytamy przy każdej zmianie: `make install-stream-activity` dopisuje przypisanie
        # w trakcie działania usługi, a kopia silnika (z opisami) też bywa odświeżana.
        name = str(_load(self.bindings).get("activities", {}).get(activity or "", "")) if activity else ""
        description = _load(self.looks / f"{name}.json") if name else {}
        if not description:
            name, description = DEFAULT_LOOK, _load(self.looks / f"{DEFAULT_LOOK}.json")
        self.activity, self.look, self.description = activity, name, description
        self.changed.emit()

    @property
    def work_panels(self) -> bool:
        return self.description.get("panels", DEFAULT_LOOK) == DEFAULT_LOOK

    @property
    def allowed(self) -> dict:
        notifications = self.description.get("notifications")
        allow = notifications.get("allow") if isinstance(notifications, dict) else None
        return allow if isinstance(allow, dict) else {}

    def state_json(self) -> str:
        return json.dumps({"activity": self.activity, "look": self.look, "workPanels": self.work_panels,
                           "notificationsFiltered": bool(self.allowed)}, ensure_ascii=False)


class NotificationFilter(QObject):
    """Inhibicja i dozwolone aplikacje tylko w wyglądzie z `notifications.allow` (Stream).

    Inhibicja żyje tyle, co serwer powiadomień (plasmashell): po jego restarcie (`--replace`, aktualizacja,
    awaria) ciasteczko nic nie znaczy, więc usługa patrzy na nazwę org.freedesktop.Notifications — zniknięcie
    zeruje ciasteczko, pojawienie się w Stream to nowe Inhibit i ponowne sprawdzenie wyjątków. Nieudane Inhibit
    (np. logowanie od razu w Stream, zanim plasmashell wstanie) jest ponawiane co retry_ms, coraz rzadziej.
    """

    def __init__(self, state: ActivityState, saved: Path | None = None, bus: QDBusConnection | None = None,
                 env: dict | None = None, retry_ms: tuple[int, int] = (2_000, 60_000), parent=None):
        super().__init__(parent)
        self.state = state
        self.saved = saved or _xdg("XDG_STATE_HOME", ".local/state") / "tide" / "stream-notifications.json"
        self.bus = bus or QDBusConnection.sessionBus()
        self.env = env
        self.cookie = 0
        self._wanted = False
        self._allowed: dict = {}
        self._pending: set[QDBusPendingCallWatcher] = set()
        self.retry_first, self.retry_max = retry_ms
        self._retry_ms = self.retry_first
        self._retry = QTimer(self, singleShot=True)
        self._retry.timeout.connect(self._inhibit)
        self._server = QDBusServiceWatcher(NOTIFY[0], self.bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                           | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._server.serviceRegistered.connect(self._server_up)
        self._server.serviceUnregistered.connect(self._server_down)
        state.changed.connect(self.update)

    # --- stan docelowy -----------------------------------------------------------------------

    def update(self) -> None:
        allow = self.state.allowed
        if allow:
            self._hold(allow)
        else:
            self.release()

    def stop(self) -> None:
        self.release(blocking=True)

    def _hold(self, allow: dict) -> None:
        self._wanted, self._allowed = True, allow
        self._inhibit()
        self._allow(allow)

    def _inhibit(self) -> None:
        if not self._wanted or self.cookie or self._pending:
            return
        msg = QDBusMessage.createMethodCall(*NOTIFY, "Inhibit")
        msg.setArguments([INHIBIT_ENTRY, INHIBIT_REASON, {}])
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(msg, 5000), self)
        self._pending.add(watcher)
        watcher.finished.connect(lambda w: self._inhibited(w))

    def _inhibited(self, watcher: QDBusPendingCallWatcher, release_now: bool = True) -> None:
        # waitForFinished przy zamykaniu może dostarczyć finished, zanim release odbierze
        # odpowiedź ręcznie. Nie przywracamy już zwolnionego ciasteczka przy drugim odbiorze.
        if watcher not in self._pending:
            return
        self._pending.remove(watcher)
        reply = QDBusPendingReply(watcher).reply()
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            if self._wanted and release_now:
                print(f"tide-daemon: Stream: nie udało się wstrzymać powiadomień ({reply.errorMessage()}) — "
                      f"ponowię za {self._retry_ms // 1000} s", flush=True)
                self._retry.start(self._retry_ms)
                self._retry_ms = min(self._retry_ms * 2, self.retry_max)
            return
        self.cookie = int(reply.arguments()[0])
        self._retry_ms = self.retry_first
        if not self._wanted and release_now:  # wyjście ze Stream, zanim serwer odpowiedział
            self._uninhibit(blocking=False)

    @pyqtSlot(str)
    def _server_down(self, _name: str) -> None:
        # Serwer powiadomień zniknął razem ze swoimi inhibicjami — ciasteczko już nic nie znaczy.
        self.cookie = 0

    @pyqtSlot(str)
    def _server_up(self, _name: str) -> None:
        if self._wanted:
            self.cookie = 0
            self._retry.stop()
            self._retry_ms = self.retry_first
            self._inhibit()
            self._allow(self._allowed)  # nowy plasmashell czyta plasmanotifyrc od nowa — wyjątki muszą być

    def release(self, blocking: bool = False) -> None:
        self._wanted = False
        self._retry.stop()
        self._retry_ms = self.retry_first
        self._restore()
        if blocking:
            # Zatrzymanie usługi: pętla Qt już nie działa, więc odpowiedź na Inhibit odbieramy tutaj
            # i zwalniamy niżej, czekając na UnInhibit.
            for watcher in list(self._pending):
                watcher.waitForFinished()
                self._inhibited(watcher, release_now=False)
        if self.cookie:
            self._uninhibit(blocking)

    def _uninhibit(self, blocking: bool) -> None:
        msg = QDBusMessage.createMethodCall(*NOTIFY, "UnInhibit")
        msg.setArguments([QDBusArgument(self.cookie, QMetaType.Type.UInt.value)])
        self.cookie = 0
        if blocking:
            self.bus.call(msg, timeout=2000)
        else:
            self.bus.asyncCall(msg, 5000)

    # --- plasmanotifyrc ----------------------------------------------------------------------

    @staticmethod
    def entries(allow: dict) -> list[tuple[str, str]]:
        out = [("Applications", name) for name in allow.get("applications", [])]
        out += [("Services", name) for name in allow.get("services", [])]
        return out

    def _run(self, argv: list[str]) -> str:
        result = subprocess.run(argv, capture_output=True, text=True, env=self.env)
        return result.stdout.strip()

    def _read(self, group: str, name: str) -> str | None:
        value = self._run(["kreadconfig6", "--file", NOTIFYRC, "--group", group, "--group", name, "--key", KEY,
                           "--default", UNSET])
        return None if value == UNSET else value

    def _write(self, group: str, name: str, value: str | None, typed: bool = True) -> None:
        """typed: wartość logiczna (włączenie wyjątku); przywracamy surowy napis sprzed Stream, jak był."""
        base = ["kwriteconfig6", "--file", NOTIFYRC, "--group", group, "--group", name, "--key", KEY, "--notify"]
        if value is None:
            self._run(base + ["--delete", ""])
        else:
            self._run(base + (["--type", "bool"] if typed else []) + [value])

    def _allow(self, allow: dict) -> None:
        saved = _load(self.saved).get("entries")
        saved = saved if isinstance(saved, list) else []
        known = {(e["group"], e["name"]) for e in saved}
        missing = [(g, n) for g, n in self.entries(allow) if (g, n) not in known]
        if missing:
            # Poprzednie wartości zapisane przed pierwszą zmianą; kolejne wejścia ich nie nadpisują.
            saved += [{"group": g, "name": n, "value": self._read(g, n)} for g, n in missing]
            self.saved.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.saved.with_name(self.saved.name + ".tmp")
            tmp.write_text(json.dumps({"file": NOTIFYRC, "key": KEY, "entries": saved}, indent=2) + "\n")
            tmp.replace(self.saved)
        for group, name in self.entries(allow):
            if self._read(group, name) != "true":
                self._write(group, name, "true")

    def _restore(self) -> None:
        entries = _load(self.saved).get("entries")
        if not isinstance(entries, list):
            return
        for entry in entries:
            if self._read(entry["group"], entry["name"]) != entry["value"]:
                self._write(entry["group"], entry["name"], entry["value"], typed=False)
        # Plik z wartościami sprzed Stream znika dopiero, gdy odczyt kontrolny potwierdza każdą z nich —
        # nieudany zapis (np. brak kwriteconfig6) ponowi następne wyjście albo start usługi.
        wrong = [e for e in entries if self._read(e["group"], e["name"]) != e["value"]]
        if wrong:
            print("tide-daemon: Stream: nie udało się przywrócić ustawień powiadomień: "
                  + ", ".join(f"[{e['group']}][{e['name']}]" for e in wrong), flush=True)
            return
        self.saved.unlink(missing_ok=True)
