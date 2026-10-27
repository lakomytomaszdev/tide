"""Widok „Docker” okna Tide: kontenery, akcje, logi i porządki — dane i akcje z tide-daemon przez D-Bus
(/Docker, docs/tide-daemon.md#docker) albo stałe dane z pliku (--data; akcje są wtedy wyłączone).

Moduł widoku (apps/tide/views.json): okno Tide ładuje go z views/docker/backend.py i woła
create(bus, data). Strona QML to qml/DockerView.qml.

Plik dla --data: wynik `tide-daemon.py --print-docker` ({"snapshot", "disk"}) albo sama migawka; do
podglądu można dołożyć "stats" (kształt Stats()) i "logs" ({nazwa albo ID kontenera: [linie]}, linia to
napis albo {text, stderr}).

Na starcie tylko migawka z pamięci usługi (plakietka w pasku okna). Stats() i Disk() woła widok — Stats
tylko wtedy, gdy jest na wierzchu (statystyki kosztują Dockera, decyzja 58). Plakietka: kontenery, które
padły, w różu („● 1”), inaczej liczba działających; zero — ukryta (te same reguły co Badge() w usłudze).
Prune i GroupAction to zadania: odpowiedź od razu daje `job`, a wynik przychodzi sygnałem
ActionFinished(job, json) — backend łączy się z nim od początku i dopasowuje wynik po `job`.
"""
import json
import time

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)

SERVICE, PATH, IFACE = "org.tide.Daemon", "/Docker", "org.tide.Daemon.Docker"
ACTION_TIMEOUT_MS = 30_000            # usługa odpowiada najpóźniej po ok. 20 s (REPLY_BUDGET)
READ_TIMEOUT_MS = 25_000              # Stats (≤ 8 s) i Disk (≤ 20 s)
# Zadanie bez wyniku (usługa zniknęła w trakcie) nie może blokować przycisków na zawsze.
JOB_TIMEOUT_MS = {"group": 10 * 60_000, "prune": 30 * 60_000}
PREVIEW_ERROR = "podgląd bez usługi — nic nie zmieniono"
LOST_ERROR = "usługa tide-daemon nie odpowiedziała — sprawdź stan po odświeżeniu"
GONE_ERROR = "usługa tide-daemon zniknęła w trakcie — wynik nieznany, sprawdź stan po odświeżeniu"
ACTIONS = ("start", "stop", "restart")
EMPTY_STATS = {"ok": False, "error": "", "containers": {}, "total": {}, "missing": []}
EMPTY_DISK = {"ok": False, "error": "", "categories": [], "totalReclaimableBytes": 0}
EARLY_TTL_S = 60          # wynik zadania bez znanego `job` (cudze zadanie) nie leży w pamięci dłużej
EARLY_MAX = 32


def badge(snapshot: dict) -> tuple[str, str]:
    """Plakietka widoku: („● N”, negative), gdy coś padło, inaczej (liczba działających, muted); zero — ("", muted).
    Te same reguły co Badge() w usłudze (daemon/tide_daemon/docker/snapshot.py)."""
    summary = (snapshot.get("summary") or {}) if isinstance(snapshot, dict) and snapshot.get("available") else {}
    failed = int(summary.get("failed") or 0)
    if failed > 0:
        return f"● {failed}", "negative"
    running = int(summary.get("running") or 0)
    return (str(running) if running > 0 else ""), "muted"


def split_data(data) -> tuple[dict, dict, dict, dict]:
    """Dane z pliku (--data) → (migawka, statystyki, dysk, logi)."""
    if not isinstance(data, dict):
        return {}, {}, {}, {}
    snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else (data if "groups" in data else {})
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    disk = data.get("disk") if isinstance(data.get("disk"), dict) else {}
    logs = data.get("logs") if isinstance(data.get("logs"), dict) else {}
    return snapshot, stats, disk, logs


def older(new: str, current: str) -> bool:
    """Odpowiedź z pamięci usługi starsza niż to, co już mamy (np. Disk z pamięci po świeżym DiskNow)?"""
    try:
        return int(json.loads(new).get("generatedTs") or 0) < int(json.loads(current).get("generatedTs") or 0)
    except (ValueError, TypeError, AttributeError):
        return False


def preview_logs(logs: dict, snapshot: dict, ident: str) -> dict:
    """Odpowiedź jak z Logs() dla podglądu: linie z pliku po nazwie albo ID kontenera."""
    target = next((c for g in snapshot.get("groups") or [] for c in g.get("containers") or []
                   if ident in (c.get("id"), c.get("name"), c.get("shortId"))), None)
    if target is None:
        return {"ok": False, "error": "nie ma takiego kontenera", "id": ident, "lines": []}
    raw = logs.get(target.get("name")) or logs.get(target.get("id")) or []
    lines = [line if isinstance(line, dict) else {"text": str(line), "stderr": False} for line in raw]
    return {"ok": True, "error": "", "id": target.get("id"), "name": target.get("name"), "lines": lines}


class Backend(QObject):
    """Most między QML a tide-daemon: migawka, statystyki i dysk (JSON), akcje i ich wyniki jako sygnały."""

    snapshotChanged = pyqtSignal()
    availableChanged = pyqtSignal()
    statsChanged = pyqtSignal()
    diskChanged = pyqtSignal()
    badgeChanged = pyqtSignal()
    actionFinished = pyqtSignal(str, str, str)  # (kontener, akcja: start|stop|restart|remove, wynik JSON)
    groupFinished = pyqtSignal(str, str, str)   # (projekt compose, akcja, wynik JSON z ActionFinished)
    pruneFinished = pyqtSignal(str)             # wynik JSON z ActionFinished (albo odmowa)
    logsReady = pyqtSignal(str, str)            # (kontener, odpowiedź Logs() jako JSON)
    opened = pyqtSignal(str, str, bool)         # (logs|shell, kontener, czy karta Konsoli się otworzyła)

    def __init__(self, bus: QDBusConnection | None, data=None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._preview = data is not None
        snapshot, stats, disk, logs = split_data(data)
        self._snapshot = json.dumps(snapshot, ensure_ascii=False)
        self._stats = json.dumps(stats or EMPTY_STATS, ensure_ascii=False)
        self._disk = json.dumps(disk or EMPTY_DISK, ensure_ascii=False)
        self._logs = logs
        self._available = self._preview
        self._badge = badge(snapshot)
        self._pending: set = set()      # QDBusPendingCallWatcher muszą żyć do odpowiedzi
        self._reading: set = set()      # Stats/Disk w drodze — bez nakładania się zapytań
        self._jobs: dict = {}           # job → (rodzaj, projekt, akcja, zegar)
        self._early: dict = {}          # job → (czas, wynik): przyszedł przed odpowiedzią z `job`
        self._disk_fresh = 0            # ile świeżych odczytów DiskNow() przyszło (potwierdzenie porządków)
        if self._preview or bus is None:
            return
        bus.connect(SERVICE, PATH, IFACE, "Changed", self._changed)
        bus.connect(SERVICE, PATH, IFACE, "ActionFinished", self._action_finished)
        self._watcher = QDBusServiceWatcher(SERVICE, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: self.reload())
        self._watcher.serviceUnregistered.connect(lambda _name: self._service_gone())
        self.reload()

    # --- właściwości dla QML ------------------------------------------------------------------

    @pyqtProperty(str, notify=snapshotChanged)
    def snapshotJson(self) -> str:
        return self._snapshot

    @pyqtProperty(str, notify=statsChanged)
    def statsJson(self) -> str:
        return self._stats

    @pyqtProperty(str, notify=diskChanged)
    def diskJson(self) -> str:
        return self._disk

    # Rośnie z każdą odpowiedzią DiskNow() — potwierdzenie porządków czeka na odczyt nowszy niż jego otwarcie.
    @pyqtProperty(int, notify=diskChanged)
    def diskFreshSerial(self) -> int:
        return self._disk_fresh

    # Usługa odpowiada (albo dane z pliku). Stan samego Dockera jest w migawce (available, state, error).
    @pyqtProperty(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    # Podgląd z pliku: akcje niczego nie zmieniają (widok mówi o tym przy wyniku).
    @pyqtProperty(bool, constant=True)
    def preview(self) -> bool:
        return self._preview

    @pyqtProperty(str, notify=badgeChanged)
    def badgeText(self) -> str:
        return self._badge[0]

    @pyqtProperty(str, notify=badgeChanged)
    def badgeTone(self) -> str:
        return self._badge[1]

    # --- odczyty ------------------------------------------------------------------------------

    @pyqtSlot()
    def reload(self) -> None:
        """Migawka z pamięci usługi (tanio, bez pytania Dockera)."""
        if self.bus is not None and not self._preview:
            self._call("Snapshot", [], self._got_snapshot)

    @pyqtSlot()
    def refreshStats(self) -> None:
        """CPU i RAM działających kontenerów — widok woła to tylko wtedy, gdy jest na wierzchu."""
        if self._preview:
            self.statsChanged.emit()
            return
        self._read("Stats", "_stats", self.statsChanged)

    @pyqtSlot()
    def refreshDisk(self) -> None:
        if self._preview:
            self.diskChanged.emit()
            return
        self._read("Disk", "_disk", self.diskChanged)

    @pyqtSlot()
    def refreshDiskNow(self) -> None:
        """Świeży odczyt miejsca (DiskNow — bez pamięci usługi) dla potwierdzenia porządków."""
        if self._preview:
            disk = json.loads(self._disk)
            if disk.get("ok"):
                disk["generatedTs"] = int(time.time())  # podgląd: dane z pliku udają świeży odczyt
                self._disk = json.dumps(disk, ensure_ascii=False)
            self._disk_fresh += 1
            self.diskChanged.emit()
            return
        if self.bus is None:
            return

        def done(ok: bool, value) -> None:
            if ok and isinstance(value, str):
                self._disk = value
            else:
                self._disk = json.dumps(dict(EMPTY_DISK, error=LOST_ERROR), ensure_ascii=False)
            self._disk_fresh += 1
            self.diskChanged.emit()
        self._call("DiskNow", [], done, timeout=READ_TIMEOUT_MS)

    @pyqtSlot(str, int)
    def logs(self, ident: str, lines: int) -> None:
        if self._preview:
            self.logsReady.emit(ident, json.dumps(preview_logs(self._logs, json.loads(self._snapshot), ident),
                                                  ensure_ascii=False))
            return
        if self.bus is None:
            self.logsReady.emit(ident, json.dumps({"ok": False, "error": LOST_ERROR, "lines": []}, ensure_ascii=False))
            return
        self._call("Logs", [ident, int(lines)], lambda ok, value: self.logsReady.emit(
            ident, value if ok and isinstance(value, str) else json.dumps(
                {"ok": False, "error": LOST_ERROR, "lines": []}, ensure_ascii=False)), timeout=ACTION_TIMEOUT_MS)

    # --- akcje --------------------------------------------------------------------------------

    @pyqtSlot(str)
    def start(self, ident: str) -> None:
        self._action(ident, "start", "Start")

    @pyqtSlot(str)
    def stop(self, ident: str) -> None:
        self._action(ident, "stop", "Stop")

    @pyqtSlot(str)
    def restart(self, ident: str) -> None:
        self._action(ident, "restart", "Restart")

    @pyqtSlot(str)
    def remove(self, ident: str) -> None:
        self._action(ident, "remove", "Remove")

    @pyqtSlot(str, str)
    def groupAction(self, project: str, action: str) -> None:
        def refused(error: str) -> None:
            self.groupFinished.emit(project, action, json.dumps(
                {"ok": False, "error": error, "job": "", "project": project, "done": [], "failed": []},
                ensure_ascii=False))
        if action not in ACTIONS:
            refused(f"nieznana akcja: {action}")
            return
        if self._preview or self.bus is None:
            refused(PREVIEW_ERROR if self._preview else LOST_ERROR)
            return
        self._call("GroupAction", [project, action],
                   lambda ok, value: self._job_started("group", project, action, ok, value, refused),
                   timeout=ACTION_TIMEOUT_MS)

    @pyqtSlot(str)
    def prune(self, categories: str) -> None:
        """Porządki dla listy JSON z id kategorii, np. ["cache", "images"] — dokładnie to, co zaznaczono."""
        def refused(error: str) -> None:
            self.pruneFinished.emit(json.dumps({"ok": False, "error": error, "job": "", "reclaimedBytes": 0,
                                                "details": []}, ensure_ascii=False))
        if self._preview or self.bus is None:
            refused(PREVIEW_ERROR if self._preview else LOST_ERROR)
            return
        self._call("Prune", [categories], lambda ok, value: self._job_started("prune", "", "prune", ok, value, refused),
                   timeout=ACTION_TIMEOUT_MS)

    @pyqtSlot(str)
    def openLogs(self, ident: str) -> None:
        self._open("logs", "OpenLogs", ident)

    @pyqtSlot(str)
    def openShell(self, ident: str) -> None:
        self._open("shell", "OpenShell", ident)

    # --- wnętrze ------------------------------------------------------------------------------

    def _action(self, ident: str, action: str, member: str) -> None:
        if self._preview or self.bus is None:
            self.actionFinished.emit(ident, action, json.dumps(
                {"ok": False, "error": PREVIEW_ERROR if self._preview else LOST_ERROR, "id": ident},
                ensure_ascii=False))
            return
        self._call(member, [ident], lambda ok, value: self.actionFinished.emit(
            ident, action, value if ok and isinstance(value, str) else json.dumps(
                {"ok": False, "error": LOST_ERROR, "id": ident}, ensure_ascii=False)), timeout=ACTION_TIMEOUT_MS)

    def _open(self, what: str, member: str, ident: str) -> None:
        if self._preview or self.bus is None:
            self.opened.emit(what, ident, False)
            return
        self._call(member, [ident], lambda ok, value: self.opened.emit(what, ident, bool(ok and value is True)))

    def _job_started(self, kind: str, project: str, action: str, ok: bool, value, refused) -> None:
        try:
            started = json.loads(value) if ok and isinstance(value, str) else None
        except ValueError:
            started = None
        if not isinstance(started, dict):
            refused(LOST_ERROR)
            return
        job = started.get("job") or ""
        if not started.get("ok") or not job:
            refused(started.get("error") or "usługa odmówiła")
            return
        timer = QTimer(self, singleShot=True, interval=JOB_TIMEOUT_MS[kind])
        timer.timeout.connect(lambda: self._finish_job(job, None, LOST_ERROR))
        self._jobs[job] = (kind, project, action, timer)
        if job in self._early:
            self._finish_job(job, self._early.pop(job)[1])
        else:
            timer.start()

    @pyqtSlot(str, str)
    def _action_finished(self, job: str, text: str) -> None:
        if job in self._jobs:
            self._finish_job(job, text)
            return
        # Odpowiedź z `job` jeszcze w drodze — albo zadanie innego klienta (kafelek, drugie okno), na które
        # nikt tu nie czeka: takie wpisy znikają po EARLY_TTL_S, a pamięć ma najwyżej EARLY_MAX wpisów.
        now = time.monotonic()
        self._early = {j: v for j, v in self._early.items() if now - v[0] < EARLY_TTL_S}
        while len(self._early) >= EARLY_MAX:
            self._early.pop(next(iter(self._early)))
        self._early[job] = (now, text)

    def _finish_job(self, job: str, text, error: str = "") -> None:
        entry = self._jobs.pop(job, None)
        if entry is None:
            return
        kind, project, action, timer = entry
        timer.stop()
        timer.deleteLater()
        if text is None:
            text = json.dumps({"ok": False, "error": error or LOST_ERROR, "job": job, "project": project,
                               "done": [], "failed": [], "reclaimedBytes": 0, "details": []}, ensure_ascii=False)
        if kind == "group":
            self.groupFinished.emit(project, action, text)
        else:
            self.pruneFinished.emit(text)
            self.refreshDisk()  # usługa skasowała pamięć Disk po porządkach

    def _service_gone(self) -> None:
        self._set_available(False)
        for job in list(self._jobs):
            self._finish_job(job, None, GONE_ERROR)

    @pyqtSlot()
    def _changed(self) -> None:
        self.reload()

    def _got_snapshot(self, ok: bool, value) -> None:
        if not ok:
            self._set_available(False)
            return
        if isinstance(value, str) and value != self._snapshot:
            self._snapshot = value
            self.snapshotChanged.emit()
        self._set_available(True)
        try:
            parsed = json.loads(self._snapshot)
        except ValueError:
            parsed = {}
        self._set_badge(badge(parsed))  # także po powrocie usługi z tą samą migawką

    def _read(self, member: str, attr: str, signal) -> None:
        if self.bus is None or member in self._reading:
            return
        self._reading.add(member)

        def done(ok: bool, value) -> None:
            self._reading.discard(member)
            if ok and isinstance(value, str) and value != getattr(self, attr) and not older(value, getattr(self, attr)):
                setattr(self, attr, value)
                signal.emit()
        self._call(member, [], done, timeout=READ_TIMEOUT_MS)

    def _set_badge(self, value: tuple[str, str]) -> None:
        if value != self._badge:
            self._badge = value
            self.badgeChanged.emit()

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.availableChanged.emit()
            if not value:
                self._set_badge(("", "muted"))

    def _call(self, member: str, args: list, then, timeout: int = 10_000) -> None:
        message = QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, member)
        if args:
            message.setArguments(args)
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(message, timeout), self)
        self._pending.add(watcher)

        def finished(w):
            self._pending.discard(w)
            reply = QDBusPendingReply(w)
            ok = not reply.isError()
            value = reply.argumentAt(0) if ok and reply.reply().arguments() else None
            w.deleteLater()
            if then:
                then(ok, value)

        watcher.finished.connect(finished)


def create(bus: QDBusConnection | None, data=None) -> Backend:
    """Backend widoku dla okna Tide: z magistralą (na żywo) albo ze stałymi danymi (data: dict z pliku)."""
    if data is not None:
        return Backend(None, data if isinstance(data, dict) else {})
    return Backend(bus)
