"""Czy bieżąca aktywność to Stream — jedno źródło dla skrótów Stream i powrotu monitora na PC (PT-38).

Sygnał `changed(activity, stream)` przychodzi po każdej zmianie aktywności (org.kde.ActivityManager,
CurrentActivityChanged) i raz po starcie, gdy ActivityManager odpowie na pytanie o bieżącą aktywność.
`unknown()` — ActivityManager na to pytanie nie odpowiedział (jeszcze nie wstał); pytanie wraca co retry_ms.
Po scaleniu z wyglądem aktywności (PT-36) odbiorcy mogą przejść na jego sygnał bez zmian u siebie —
podpięcie jest w app.py.

Wyścig ze startu: odpowiedź na pytanie wysłane przed ostatnim sygnałem CurrentActivityChanged jest nieaktualna
i przepada (licznik zdarzeń), więc spóźniona odpowiedź „praca” nie cofa wejścia do Streamu i odwrotnie.

Stream to aktywność przypisana do wyglądu „stream” w $XDG_CONFIG_HOME/tide/activities.json (`{"activities":
{"<ID>": "stream"}}`, decyzja 78) — te same przypisania, z których wygląd aktywności bierze opis.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply

AM_SERVICE = "org.kde.ActivityManager"
AM_PATH = "/ActivityManager/Activities"
AM_IFACE = "org.kde.ActivityManager.Activities"
STREAM_LOOK = "stream"


def default_bindings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "tide" / "activities.json"


def is_stream(activity: str, bindings: Path | None = None) -> bool:
    """Aktywność przypisana do wyglądu „stream” (activity_look.py bind). Każda inna to praca."""
    try:
        data = json.loads((bindings or default_bindings_path()).read_text())
    except (OSError, ValueError):
        return False
    items = data.get("activities") if isinstance(data, dict) else None
    return bool(activity) and isinstance(items, dict) and items.get(activity) == STREAM_LOOK


class CurrentActivity(QObject):
    changed = pyqtSignal(str, bool)   # (ID aktywności, czy Stream)
    unknown = pyqtSignal()            # ActivityManager nie odpowiedział na pytanie o bieżącą aktywność

    def __init__(self, stream=is_stream, bus: QDBusConnection | None = None, subscribe: bool = True,
                 retry_ms: int = 5000, retries: int = 12, parent=None):
        super().__init__(parent)
        self.stream = stream
        self.bus = bus or QDBusConnection.sessionBus()
        self.subscribe = subscribe
        self.retries = retries
        self.activity: str | None = None
        self._events = 0      # liczba sygnałów CurrentActivityChanged — wiek pytań w toku
        self._tries = 0
        self._stopped = False
        self._watchers: set = set()
        self._retry = QTimer(self, singleShot=True, interval=retry_ms)
        self._retry.timeout.connect(self.ask)

    def start(self) -> None:
        if self.subscribe and not self.bus.connect(AM_SERVICE, AM_PATH, AM_IFACE, "CurrentActivityChanged",
                                                   self.activity_changed):
            print("tide-daemon: aktywność: nie da się słuchać CurrentActivityChanged", flush=True)
        self.ask()

    def stop(self) -> None:
        self._stopped = True
        self._retry.stop()
        if self.subscribe:
            self.bus.disconnect(AM_SERVICE, AM_PATH, AM_IFACE, "CurrentActivityChanged", self.activity_changed)

    @pyqtSlot(str)
    def activity_changed(self, activity: str) -> None:
        if self._stopped:
            return
        self._events += 1
        self._retry.stop()
        self._publish(activity)

    def ask(self) -> None:
        """Pytanie o bieżącą aktywność; odpowiedź liczy się tylko, jeśli w międzyczasie nie było sygnału."""
        if self._stopped:
            return
        asked_at = self._events
        msg = QDBusMessage.createMethodCall(AM_SERVICE, AM_PATH, AM_IFACE, "CurrentActivity")
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(msg, 5000), self)
        self._watchers.add(watcher)

        def finished(w):
            self._watchers.discard(w)
            reply = QDBusPendingReply(w).reply()
            w.deleteLater()
            ok = reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments()
            self.answered(asked_at, str(reply.arguments()[0]) if ok else None)

        watcher.finished.connect(finished)

    def answered(self, asked_at: int, activity: str | None) -> None:
        if self._stopped or asked_at != self._events:
            return  # nieaktualna: sygnał zmiany aktywności przyszedł po wysłaniu pytania i już rozstrzygnął
        if activity:
            self._publish(activity)
            return
        self.unknown.emit()
        self._tries += 1
        if self._tries < self.retries:
            self._retry.start()

    def _publish(self, activity: str) -> None:
        self.activity = activity
        self.changed.emit(activity, bool(self.stream(activity)))
