"""Sprawy: asynchroniczny klient publicznego /Issues; lokalne bufory edycji i obrazy w pamięci."""
import base64
import hashlib
import importlib.util
import json
import os
import re
import stat
import uuid

from PyQt6.QtCore import QByteArray, QObject, QTemporaryDir, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply, QDBusServiceWatcher
from PyQt6.QtGui import QColor, QDesktopServices

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE, PATH, IFACE = "org.tide.Daemon", "/Issues", "org.tide.Daemon.Issues"
FILE_MAX = 20 * 1024 * 1024


def common(name):
    for folder in (HERE, os.path.join(os.path.dirname(HERE), "common")):
        path = os.path.join(folder, name + ".py")
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("tide_issue_" + name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise ImportError(name)


report = common("issuereport")


def failure(message, error="unavailable"):
    return {"ok": False, "apiVersion": 1, "error": error, "message": message}


def validate_read(issue, entry, result, raw):
    return (result.get("ok") and result.get("id") == issue["id"] and
            result.get("version") == issue["version"] and result.get("attachment") == entry and
            len(raw) <= FILE_MAX and len(raw) == entry.get("size") and
            hashlib.sha256(raw).hexdigest() == entry.get("sha256"))


def read_source(url):
    """Jawny wybór użytkownika, każdy komponent bez dowiązań i bez plików specjalnych."""
    if not url.isLocalFile() or url.host() or ".." in url.toLocalFile().split("/"):
        raise ValueError("Wybierz zwykły lokalny plik bez dowiązań.")
    path = url.toLocalFile()
    parts = path.split("/")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > FILE_MAX:
                raise ValueError("Załącznik musi być zwykłym plikiem do 20 MiB.")
            raw = stream.read(FILE_MAX + 1)
            stream.seek(0)
            again = stream.read(FILE_MAX + 1)
            after = os.fstat(stream.fileno())
            current = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if (len(raw) > FILE_MAX or raw != again or signature(before) != signature(after) or
                    signature(before) != signature(current)):
                raise ValueError("Plik zmienił się podczas odczytu; wybierz go ponownie.")
        return parts[-1], raw
    finally:
        os.close(fd)


class Backend(QObject):
    changed = pyqtSignal()

    def __init__(self, bus, data=None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._preview = bus is None
        self._data = data or {}
        self._issues, self._list_errors, self._projects = [], [], []
        self._buffers = {}
        self._selected = ""
        self._notice = ""
        self._available = data is not None
        self._busy = False
        self._pending = set()
        self._capabilities = set()
        self._generation = 0
        self._images = {}
        self._provider = None
        self._exports = []
        if self._preview:
            self._issues = self._data.get("issues", [])
            self._projects = self._data.get("projects", [])
            self._list_errors = self._data.get("errors", [])
        else:
            self._watcher = QDBusServiceWatcher(SERVICE, bus,
                QDBusServiceWatcher.WatchModeFlag.WatchForRegistration |
                QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
            self._watcher.serviceRegistered.connect(lambda _: self.reload())
            self._watcher.serviceUnregistered.connect(lambda _: self._lost())
            self._timer = QTimer(self)
            self._timer.setInterval(5000)
            self._timer.timeout.connect(self.reload)
            self._timer.start()
            self.reload()

    def install_image_provider(self, engine):
        self._provider = report.ImageProvider()
        engine.addImageProvider("issue-evidence", self._provider)

    @pyqtProperty(bool, constant=True)
    def preview(self):
        return self._preview

    @pyqtProperty(bool, notify=changed)
    def available(self):
        return self._available

    @pyqtProperty(str, notify=changed)
    def badgeText(self):
        count = sum(i.get("state") == "ready" for i in self._issues)
        return str(count) if self._available and count else ""

    @pyqtProperty(str, constant=True)
    def badgeTone(self):
        return "waiting"

    @pyqtProperty(str, notify=changed)
    def stateJson(self):
        buf = self._buffers.get(self._selected, {})
        return json.dumps(dict(issues=self._issues, listErrors=self._list_errors, projects=self._projects,
            selected=self._selected, notice=self._notice, available=self._available, busy=self._busy,
            issue=buf.get("issue"), draft=buf.get("draft", {}), dirty=self._dirty(buf),
            conflict=buf.get("conflict", 0), current=buf.get("current"), error=buf.get("error", ""),
            evidence=buf.get("evidence", {})), ensure_ascii=False)

    def _dirty(self, buf):
        return bool(buf.get("issue") and buf.get("draft") != self._fields(buf["issue"]))

    def _fields(self, issue):
        return {k: issue.get(k) for k in ("title", "report", "project")}

    def _lost(self):
        self._available = False
        self._notice = "Usługa Tide jest niedostępna. Wpisany tekst pozostaje w oknie."
        self.changed.emit()

    def _call(self, method, args, callback, path=PATH, interface=IFACE):
        message = QDBusMessage.createMethodCall(SERVICE, path, interface, method)
        message.setArguments(args)
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(message, 30000), self)
        self._pending.add(watcher)

        def finished(w):
            self._pending.discard(w)
            reply = QDBusPendingReply(w).reply()
            raw = b""
            try:
                values = reply.arguments()
                expected = 2 if method == "ReadAttachment" else 1
                if reply.type() == QDBusMessage.MessageType.ErrorMessage:
                    raise ValueError()
                if len(values) != expected:
                    raise ValueError()
                result = json.loads(values[0])
                if path == PATH and (type(result.get("apiVersion")) is not int or result["apiVersion"] != 1 or
                                     type(result.get("ok")) is not bool):
                    raise ValueError()
                if expected == 2:
                    if not isinstance(values[1], QByteArray) or len(values[1]) > FILE_MAX:
                        raise ValueError()
                    raw = bytes(values[1])
                    if not result["ok"] and raw:
                        raise ValueError()
            except (ValueError, TypeError, AttributeError):
                result = failure("Usługa Tide jest niedostępna lub ma niezgodne API spraw v1.")
            w.deleteLater()
            callback(result, raw)

        watcher.finished.connect(finished)

    @pyqtSlot()
    def reload(self):
        if self._preview or self._busy:
            return
        def info(result, _raw):
            self._capabilities = set(result.get("capabilities", [])) if result.get("ok") else set()
            self._available = "issue.show" in self._capabilities and "issue.list" in self._capabilities
            if not self._available:
                self._lost()
                return
            self._call("List", [], listed)
        def listed(result, _raw):
            if not result.get("ok"):
                self._notice = result.get("message", "Nie można odczytać spraw.")
            else:
                self._issues = result.get("issues", [])
                self._list_errors = result.get("errors", [])
                if self._notice.startswith("Usługa Tide"):
                    self._notice = ""
            self.changed.emit()
        def projects(result, _raw):
            self._projects = result.get("projects", [])
            self.changed.emit()
        self._call("Info", [], info)
        self._call("Snapshot", [], projects, "/Board", "org.tide.Daemon.Board")

    @pyqtSlot(str)
    def selectIssue(self, ident):
        if not re.fullmatch(r"S-[1-9][0-9]{0,8}", ident) or self._busy:
            return
        self._selected = ident
        self._generation += 1
        self._images.clear()
        if self._provider:
            self._provider.images.clear()
        self.changed.emit()
        if self._preview:
            issue = next((i for i in self._issues if i.get("id") == ident), None)
            self._shown(ident, {"ok": True, "issue": issue} if issue else
                        failure("Nie ma sprawy " + ident + ".", "not-found"))
        else:
            self._call("Show", [ident], lambda result, _: self._shown(ident, result))

    def _shown(self, ident, result, discard=False):
        buf = self._buffers.setdefault(ident, {})
        if not result.get("ok"):
            buf["error"] = result.get("message", "Nie można odczytać sprawy.")
        else:
            issue = result["issue"]
            if not discard and self._dirty(buf):
                if issue["version"] != buf["issue"]["version"]:
                    buf.update(current=issue, conflict=issue["version"])
            else:
                buf.update(issue=issue, draft=self._fields(issue), conflict=0, current=None, error="")
            if ident == self._selected:
                self._load_evidence(buf["issue"])
        self.changed.emit()

    @pyqtSlot(str, str)
    def updateDraft(self, field, value):
        buf = self._buffers.get(self._selected, {})
        if field in ("title", "report", "project") and buf.get("issue") and not self._busy:
            buf["draft"][field] = None if field == "project" and value == "" else value
            self.changed.emit()

    @pyqtSlot()
    def reloadSelected(self):
        if self._busy or self._preview:
            return
        ident = self._selected
        self._call("Show", [ident], lambda result, _: self._shown(ident, result, discard=True))

    def _can_write(self, method):
        buf = self._buffers.get(self._selected, {})
        allowed = {"Update": ("draft", "ready"), "Submit": ("draft",),
                   "Reject": ("draft", "ready"), "Reopen": ("rejected",),
                   "Attach": ("draft", "ready"), "Detach": ("draft", "ready")}
        return (not self._preview and self._available and not self._busy and buf.get("issue") and
                not buf.get("conflict") and buf["issue"]["state"] in allowed[method] and
                "issue." + method.lower() in self._capabilities)

    @pyqtSlot()
    def save(self):
        if not self._can_write("Update"):
            return
        buf = self._buffers[self._selected]
        patch = {k: v for k, v in buf["draft"].items() if v != buf["issue"].get(k)}
        if patch:
            self._mutate("Update", patch)

    @pyqtSlot(str)
    def transition(self, method):
        if method in ("Submit", "Reject", "Reopen") and self._can_write(method):
            if self._dirty(self._buffers[self._selected]):
                self._notice = "Najpierw zapisz albo ponownie wczytaj lokalne zmiany."
                self.changed.emit()
                return
            self._mutate(method, {})

    def _mutate(self, method, patch, raw=None):
        ident = self._selected
        buf = self._buffers[ident]
        self._busy = True
        self._notice = ""
        args = [ident, json.dumps(dict(patch, expectedVersion=buf["issue"]["version"]), ensure_ascii=False)]
        if raw is not None:
            args.append(QByteArray(raw))
        self.changed.emit()
        def done(result, _raw):
            self._busy = False
            if result.get("ok"):
                self._shown(ident, result, discard=method == "Update")
                # Attach/Detach są zablokowane przy niezapisanym tekście; przejścia również.
                self._notice = result.get("commitError", "")
                self.reload()
            else:
                self._notice = result.get("message", "Nie udało się zapisać sprawy.")
                if result.get("error") == "version-conflict":
                    buf["conflict"] = result.get("currentVersion", 0)
                    self._call("Show", [ident], lambda reply, _: self._conflict_current(ident, reply))
            self.changed.emit()
        self._call(method, args, done)

    def _conflict_current(self, ident, result):
        if result.get("ok"):
            self._buffers[ident]["current"] = result["issue"]
        self.changed.emit()

    @pyqtSlot(QUrl)
    def attach(self, url):
        if not self._can_write("Attach") or self._dirty(self._buffers[self._selected]):
            return
        try:
            name, raw = read_source(url)
        except (OSError, ValueError) as exc:
            self._notice = str(exc)
            self.changed.emit()
            return
        self._mutate("Attach", {"name": name}, raw)

    @pyqtSlot(str)
    def detach(self, attachment):
        if self._can_write("Detach") and not self._dirty(self._buffers[self._selected]):
            if any(e["id"] == attachment for e in self._buffers[self._selected]["issue"]["attachments"]):
                self._mutate("Detach", {"attachmentId": attachment})

    def _read(self, issue, entry, callback):
        def got(result, raw):
            if result.get("ok") and not validate_read(issue, entry, result, raw):
                result, raw = failure("Dowód jest niezgodny z manifestem.", "broken-file"), b""
            callback(result, raw)
        if self._preview:
            try:
                raw = base64.b64decode(self._data.get("attachmentData", {}).get(entry["id"], ""), validate=True)
            except ValueError:
                raw = b""
            got(dict(ok=True, id=issue["id"], version=issue["version"], attachment=entry), raw)
        elif "issue.attachment" in self._capabilities:
            self._call("ReadAttachment", [issue["id"], json.dumps({"attachmentId": entry["id"],
                         "expectedVersion": issue["version"]})], got)
        else:
            callback(failure("Brak publicznego odczytu dowodów w usłudze."), b"")

    def _load_evidence(self, issue):
        self._generation += 1
        generation = self._generation
        self._images.clear()
        if self._provider:
            self._provider.images.clear()
        buf = self._buffers[issue["id"]]
        entries = list(issue.get("attachments", []))
        buf["evidence"] = {e["id"]: "Sprawdzanie…" for e in entries}
        def next_entry():
            if generation != self._generation:
                return
            if not entries:
                self.changed.emit()
                return
            entry = entries.pop(0)
            # Ścieżka jest tylko kluczem Markdown, nigdy argumentem otwarcia pliku.
            ident = entry.get("id", "")
            if not report.ATTACHMENT.fullmatch(ident) or entry.get("path") != "attachments/" + ident:
                buf["evidence"][ident] = "Niedozwolona ścieżka manifestu."
                self.changed.emit()
                next_entry()
                return
            def got(result, raw):
                if generation != self._generation:
                    return
                status = "Spójny z manifestem" if result.get("ok") else result.get("message", "Błąd dowodu")
                if result.get("ok") and entry.get("type") in report.IMAGE_TYPES:
                    image = report.decode_image(raw, entry["type"])
                    if image.isNull():
                        status = "Nieprawidłowy obraz — podgląd zablokowany."
                    elif self._provider:
                        token = uuid.uuid4().hex
                        self._provider.images[token] = image
                        self._images[entry["path"]] = ("image://issue-evidence/" + token, image.size())
                buf["evidence"][ident] = status
                self.changed.emit()
                next_entry()
            self._read(issue, entry, got)
        next_entry()

    @pyqtSlot(str, QColor, result=str)
    def renderReport(self, markdown, link):
        return report.render(markdown, self._images, link)

    @pyqtSlot(str)
    def openLink(self, link):
        if not self._preview and report.LINK.fullmatch(link):
            QDesktopServices.openUrl(QUrl(link))

    @pyqtSlot(str)
    def openAttachment(self, attachment):
        if self._preview or self._busy or not self._available:
            return
        issue = self._buffers.get(self._selected, {}).get("issue")
        entry = next((e for e in issue["attachments"] if e["id"] == attachment), None) if issue else None
        if not entry:
            return
        def opened(result, raw):
            if not result.get("ok"):
                self._notice = result.get("message", "Nie można odczytać dowodu.")
            else:
                # Niezaufane rozszerzenia (w tym HTML/SVG/skrypty) nie wybierają aplikacji.
                # Otwarcie pozostałych dowodów jako tekst jest jawną akcją użytkownika.
                temp = QTemporaryDir()
                self._exports.append(temp)
                path = os.path.join(temp.path(), "dowod.txt")
                if entry["type"] in report.IMAGE_TYPES:
                    image = report.decode_image(raw, entry["type"])
                    if image.isNull():
                        self._notice = "Nieprawidłowy obraz."
                        self.changed.emit()
                        return
                    path = os.path.join(temp.path(), "dowod.png")
                    image.save(path, "PNG")
                else:
                    with open(path, "x", encoding="utf-8") as fh:
                        fh.write(raw.decode("utf-8", errors="replace"))
                os.chmod(path, 0o400)
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            self.changed.emit()
        self._read(issue, entry, opened)


def create(bus, data=None):
    return Backend(bus, data)
