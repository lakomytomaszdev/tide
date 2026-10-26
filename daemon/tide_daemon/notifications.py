"""Powiadomienia KDE „Agent czeka” (Notifier) i „Raport poranny gotowy” (ReportNotifier, niżej)
przez org.freedesktop.Notifications.

- sesja zaczyna czekać → Notify z przyciskiem „Przejdź” (i domyślną akcją po kliknięciu),
- ta sama sesja nie dostaje drugiego powiadomienia, dopóki nie przestanie czekać (attention.py),
- sesja przestała czekać → CloseNotification, żeby nieaktualne powiadomienie nie wisiało,
- ActionInvoked („jump” albo „default”) → przejście do okna sesji (monitor.activate).

Wywołania są asynchroniczne: serwer powiadomień, który nie odpowiada, nie blokuje usługi.

LimitsNotifier (na końcu) wysyła powiadomienia Limitów AI (progi zużycia, wyczerpanie przed
resetem — decyduje limits/alerts.py) w tym samym stylu: aplikacja „Tide”, bez wskazówek, bez
przycisku. Nowe powiadomienie tego samego okna limitu zastępuje poprzednie (replaces_id).
"""
import html

from PyQt6.QtCore import QMetaType, QObject, pyqtSlot
from PyQt6.QtDBus import QDBusArgument, QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply

from .attention import WaitingTracker, notification_text

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
IFACE = "org.freedesktop.Notifications"
APP_NAME = "Tide"
ICON = "utilities-terminal"
JUMP = "jump"
ACTIONS = ("default", "Przejdź", JUMP, "Przejdź")
LIMITS_ICON = "view-statistics"  # ta sama ikona co widżet Limity AI


class Notifier(QObject):
    def __init__(self, activate, bus: QDBusConnection | None = None, parent=None):
        super().__init__(parent)
        self.activate = activate              # funkcja(id sesji) -> bool
        self.bus = bus or QDBusConnection.sessionBus()
        self.tracker = WaitingTracker()
        self.session_of: dict[int, str] = {}  # id powiadomienia → id sesji
        self.notice_of: dict[str, int] = {}   # id sesji → id powiadomienia
        self._close_when_known: set[str] = set()
        self._watchers: set[QDBusPendingCallWatcher] = set()
        self.bus.connect(SERVICE, PATH, IFACE, "ActionInvoked", self._action_invoked)
        self.bus.connect(SERVICE, PATH, IFACE, "NotificationClosed", self._closed)

    def update(self, sessions: list[dict]) -> None:
        started, stopped = self.tracker.update(sessions)
        for sid in stopped:
            self._close(sid)
        for session in started:
            self._notify(session)

    # --- wywołania ----------------------------------------------------------------------

    def _notify(self, session: dict) -> None:
        sid = session["id"]
        summary, body = notification_text(session)
        actions = list(ACTIONS) if session.get("canActivate") else []
        msg = QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, "Notify")
        msg.setArguments([
            APP_NAME,
            QDBusArgument(self.notice_of.get(sid, 0), QMetaType.Type.UInt.value),
            ICON,
            summary,
            html.escape(body, quote=False),  # treść może zawierać prosty HTML — nazwy plików nie
            QDBusArgument(actions, QMetaType.Type.QStringList.value),
            {},  # bez wskazówek: powiadomienie należy do „Tide”, nie podszywa się pod Konsolę
            QDBusArgument(-1, QMetaType.Type.Int.value),
        ])
        self._close_when_known.discard(sid)
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(msg), self)
        self._watchers.add(watcher)
        watcher.finished.connect(lambda w, sid=sid: self._notified(w, sid))

    def _notified(self, watcher: QDBusPendingCallWatcher, sid: str) -> None:
        self._watchers.discard(watcher)
        reply = QDBusPendingReply(watcher).reply()
        watcher.deleteLater()
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return  # brak serwera powiadomień — trudno, stan i tak widać w widżecie
        notice = int(reply.arguments()[0])
        self.session_of[notice] = sid
        self.notice_of[sid] = notice
        if sid in self._close_when_known:
            self._close(sid)

    def _close(self, sid: str) -> None:
        notice = self.notice_of.pop(sid, None)
        if notice is None:
            self._close_when_known.add(sid)  # Notify jeszcze nie odpowiedział
            return
        self.session_of.pop(notice, None)
        msg = QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, "CloseNotification")
        msg.setArguments([QDBusArgument(notice, QMetaType.Type.UInt.value)])
        self.bus.asyncCall(msg)

    # --- sygnały serwera ------------------------------------------------------------------

    @pyqtSlot("uint", str)
    def _action_invoked(self, notice: int, action: str) -> None:
        sid = self.session_of.get(int(notice))
        if sid and action in (JUMP, "default"):
            try:
                self.activate(sid)
            except Exception as exc:  # przejście do okna nie może położyć usługi
                print(f"tide-daemon: przejście z powiadomienia nie powiodło się: {exc}", flush=True)

    @pyqtSlot("uint", "uint")
    def _closed(self, notice: int, _reason: int) -> None:
        sid = self.session_of.pop(int(notice), None)
        if sid and self.notice_of.get(sid) == int(notice):
            del self.notice_of[sid]


REPORT_ICON = "view-calendar-day"
OPEN = "open"
REPORT_ACTIONS = ("default", "Otwórz", OPEN, "Otwórz")


class ReportNotifier(QObject):
    """Powiadomienie „Raport poranny gotowy” z przyciskiem „Otwórz” (report_monitor.py).

    Nowy raport zastępuje powiadomienie poprzedniego (replaces_id), a „Otwórz” albo kliknięcie
    w powiadomienie woła opener(ścieżka index.html)."""

    def __init__(self, opener, bus: QDBusConnection | None = None, parent=None):
        super().__init__(parent)
        self.opener = opener                  # funkcja(ścieżka) -> bool
        self.bus = bus or QDBusConnection.sessionBus()
        self.notice = 0
        self.path_of: dict[int, str] = {}
        self._watchers: set[QDBusPendingCallWatcher] = set()
        self.bus.connect(SERVICE, PATH, IFACE, "ActionInvoked", self._action_invoked)
        self.bus.connect(SERVICE, PATH, IFACE, "NotificationClosed", self._closed)

    def notify(self, summary: str, body: str, path: str) -> None:
        msg = QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, "Notify")
        msg.setArguments([
            APP_NAME,
            QDBusArgument(self.notice, QMetaType.Type.UInt.value),
            REPORT_ICON,
            summary,
            html.escape(body, quote=False),
            QDBusArgument(list(REPORT_ACTIONS), QMetaType.Type.QStringList.value),
            {},
            QDBusArgument(-1, QMetaType.Type.Int.value),
        ])
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(msg), self)
        self._watchers.add(watcher)
        watcher.finished.connect(lambda w, path=path: self._notified(w, path))

    def _notified(self, watcher: QDBusPendingCallWatcher, path: str) -> None:
        self._watchers.discard(watcher)
        reply = QDBusPendingReply(watcher).reply()
        watcher.deleteLater()
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return
        self.notice = int(reply.arguments()[0])
        self.path_of[self.notice] = path

    @pyqtSlot("uint", str)
    def _action_invoked(self, notice: int, action: str) -> None:
        path = self.path_of.get(int(notice))
        if path and action in (OPEN, "default"):
            try:
                self.opener(path)
            except Exception as exc:  # otwarcie przeglądarki nie może położyć usługi
                print(f"tide-daemon: nie udało się otworzyć raportu: {exc}", flush=True)

    @pyqtSlot("uint", "uint")
    def _closed(self, notice: int, _reason: int) -> None:
        self.path_of.pop(int(notice), None)
        if int(notice) == self.notice:
            self.notice = 0


class LimitsNotifier(QObject):
    """Powiadomienia Limitów AI: send({id, summary, body}) — tekst i decyzję daje limits/alerts.py."""

    def __init__(self, bus: QDBusConnection | None = None, parent=None):
        super().__init__(parent)
        self.bus = bus or QDBusConnection.sessionBus()
        self.notice_of: dict[str, int] = {}   # okno limitu („claude/session”) → id powiadomienia
        self._watchers: set[QDBusPendingCallWatcher] = set()

    def send(self, alert: dict) -> None:
        key = alert.get("id", "")
        msg = QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, "Notify")
        msg.setArguments([
            APP_NAME,
            QDBusArgument(self.notice_of.get(key, 0), QMetaType.Type.UInt.value),
            LIMITS_ICON,
            alert.get("summary", ""),
            html.escape(alert.get("body", ""), quote=False),
            QDBusArgument([], QMetaType.Type.QStringList.value),
            {},
            QDBusArgument(-1, QMetaType.Type.Int.value),
        ])
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(msg), self)
        self._watchers.add(watcher)
        watcher.finished.connect(lambda w, key=key: self._notified(w, key))

    def _notified(self, watcher: QDBusPendingCallWatcher, key: str) -> None:
        self._watchers.discard(watcher)
        reply = QDBusPendingReply(watcher).reply()
        watcher.deleteLater()
        if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
            self.notice_of[key] = int(reply.arguments()[0])
