"""Sprawy v1: zamknięty kontrakt, trwałe intencje create i niezależny magazyn Tablicy.

Tylko usługa używa tego modułu. CLI rozmawia z /Issues. Rezerwacja numeru i intencji
wyprzedza atomową publikację katalogu; po awarii tę samą intencję można dokończyć.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
from datetime import datetime, timezone

from .board.model import BoardError, SLUG_RE
from . import issue_attachments as attachments

API_VERSION = 1
REPORT_MAX = 64 * 1024
# Raport może wymagać sześciu bajtów na znak po ucieczce JSON (np. U+0001).
RECEIPT_MAX = 6 * REPORT_MAX + 8192
KEY_RE = re.compile(r"[0-9a-f]{64}\Z")
ID_RE = re.compile(r"S-([1-9][0-9]{0,8})\Z")
CREATE_FIELDS = {"report", "title", "project", "requestId"}
UPDATE_FIELDS = {"expectedVersion", "report", "title", "project"}
META_FIELDS = {"tide-issue", "id", "title", "project", "state", "version", "created", "updated", "card"}


class VersionConflict(BoardError):
    def __init__(self, current):
        super().__init__("Wersja sprawy uległa zmianie.", "version-conflict")
        self.current = current


def fail(message, code="bad-request"):
    raise BoardError(message, code)


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail(f"Powtórzone pole: {key}.")
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda value: fail("JSON zawiera niedozwoloną liczbę."))
    except (ValueError, TypeError, RecursionError):
        fail("Żądanie musi być poprawnym JSON.")


def validate(data, *, update=False):
    """Wspólne reguły create/update, przed jakimkolwiek zapisem."""
    if not isinstance(data, dict):
        fail("Żądanie musi być obiektem JSON.")
    allowed = UPDATE_FIELDS if update else CREATE_FIELDS
    if data.keys() - allowed:
        fail("Nieobsługiwane pola: " + ", ".join(sorted(data.keys() - allowed)) + ".")
    if update:
        if type(data.get("expectedVersion")) is not int or data["expectedVersion"] < 1:
            fail("expectedVersion musi być dodatnią liczbą całkowitą.")
        if not data.keys() & {"report", "title", "project"}:
            fail("Podaj co najmniej jedno zmieniane pole.")
    elif not {"report", "title"} <= data.keys():
        fail("Wymagane są pola report i title.")
    for field in ("report", "title", "requestId"):
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str):
            fail(f"Pole {field} musi być tekstem.")
        try:
            encoded = value.encode("utf-8")
        except UnicodeError:
            fail(f"Pole {field} nie jest poprawnym UTF-8.")
        if "\x00" in value:
            fail(f"Pole {field} zawiera znak NUL.")
        if field == "report":
            if len(encoded) > REPORT_MAX:
                fail("Raport przekracza 64 KiB.")
            # Odrzucamy także BOM i CRLF. Wewnętrzne linie --- pozostają treścią.
            first = value.lstrip("\ufeff").splitlines()
            if first and first[0].strip() == "---":
                fail("Raport musi zawierać sam Markdown, bez początkowego frontmatteru.")
        elif not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
            fail(f"Pole {field} musi mieć 1–200 znaków i nie może zawierać znaków sterujących.")
    if "project" in data and data["project"] is not None:
        if not isinstance(data["project"], str) or not SLUG_RE.fullmatch(data["project"]):
            fail("Projekt musi być slugiem albo null.")
    return dict(data)


def _encoded(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic(path, text):
    """Trwały rename pliku w tym samym katalogu (również katalog musi trafić na dysk)."""
    fd, tmp = tempfile.mkstemp(prefix=".tide-tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        _sync_dir(path.parent)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class IssueStore:
    def __init__(self, board):
        self.board = board
        self.root = Path(board.root)
        # Ta sama instancja BoardRepo, a więc ta sama kolejka zaległych commitów.
        self.repo = board.repo

    def _path(self, rel):
        path = self.root / rel
        if path.is_absolute() and not path.is_relative_to(self.root):
            fail("Ścieżka sprawy wychodzi poza Tablicę.", "broken-file")
        current = self.root
        for part in Path(rel).parts:
            if part in ("..", "/"):
                fail("Niepoprawna ścieżka sprawy.", "broken-file")
            current = current / part
            if current.is_symlink():
                fail("Dowiązania w magazynie spraw są niedozwolone.", "broken-file")
        return path

    def _read(self, rel, limit=REPORT_MAX + 8192):
        path = self._path(rel)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                fail("Sprawa zawiera plik inny niż zwykły plik.", "broken-file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read() if limit is None else stream.read(limit + 1)
            if limit is not None and len(raw) > limit:
                fail("Plik sprawy przekracza dopuszczalny rozmiar.", "broken-file")
            return raw.decode("utf-8")
        finally:
            os.close(fd)

    @staticmethod
    def _id(ident):
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
            fail("Identyfikator sprawy musi mieć postać S-N.")
        return ident

    def show(self, ident):
        ident = self._id(ident)
        if not self.board.exists():
            fail(f"Nie ma sprawy {ident}.", "not-found")
        with self._lock():
            return self._show(ident)

    def _show(self, ident):
        ident = self._id(ident)
        try:
            text = self._read(f"issues/{ident}/report.md")
            header, sep, body = text.partition("\n---\n")
            if not header.startswith("---\n") or not sep:
                raise ValueError("brak frontmatteru")
            meta = {}
            for line in header[4:].split("\n"):
                key, colon, value = line.partition(": ")
                if not colon or key in meta:
                    raise ValueError("niepoprawny frontmatter")
                meta[key] = json.loads(value)
            manifest_ref = meta.pop("attachmentManifest", "attachments.json")
            if (manifest_ref != "attachments.json" and
                    (not isinstance(manifest_ref, str) or not attachments.MANIFEST_RE.fullmatch(manifest_ref))):
                raise ValueError("niepoprawne wskazanie manifestu")
            if (meta.keys() != META_FIELDS or type(meta["tide-issue"]) is not int or
                    meta["tide-issue"] != 1 or meta["id"] != ident):
                raise ValueError("niezgodne metadane")
            if type(meta["version"]) is not int or meta["version"] < 1:
                raise ValueError("niepoprawna wersja")
            if meta["state"] not in ("draft", "ready", "accepted", "rejected", "archived"):
                raise ValueError("niepoprawny stan")
            for field in ("created", "updated"):
                if not isinstance(meta[field], str) or datetime.fromisoformat(meta[field]).tzinfo is None:
                    raise ValueError("niepoprawna data")
            if meta["card"] is not None and not re.fullmatch(r"K-[1-9][0-9]*", meta["card"]):
                raise ValueError("niepoprawne powiązanie karty")
            validate({"title": meta["title"], "project": meta["project"], "report": body})
            raw = attachments.read_file(self.root / "issues" / ident, manifest_ref, REPORT_MAX)
            if manifest_ref != "attachments.json":
                if hashlib.sha256(raw).hexdigest() != attachments.MANIFEST_RE.fullmatch(manifest_ref)[1]:
                    raise ValueError("niezgodny SHA-256 manifestu")
            manifest = _json(raw.decode("utf-8"))
            entries = attachments.manifest_valid(manifest)
            issue = self._issue(meta, body)
            issue["attachments"] = entries
            issue["paths"]["manifest"] = f"issues/{ident}/{manifest_ref}"
            return issue
        except FileNotFoundError:
            if not os.path.lexists(self.root / "issues" / ident):
                fail(f"Nie ma sprawy {ident}.", "not-found")
            fail(f"Sprawa {ident} jest uszkodzona: brakuje pliku.", "broken-file")
        except (OSError, ValueError, TypeError, KeyError, RecursionError, BoardError) as exc:
            fail(f"Sprawa {ident} jest uszkodzona: {exc}.", "broken-file")

    def list(self):
        issues, errors = [], []
        try:
            folder = self._path("issues")
            names = sorted(os.listdir(folder)) if folder.exists() else []
        except (OSError, BoardError) as exc:
            return {"issues": [], "errors": [{"message": str(exc)}]}
        for ident in names:
            if not ID_RE.fullmatch(ident):
                continue
            try:
                issue = self.show(ident)
                issues.append({k: v for k, v in issue.items() if k != "report"})
            except BoardError as exc:
                errors.append({"id": ident, "error": exc.code, "message": str(exc)})
        issues.sort(key=lambda issue: int(issue["id"][2:]))
        return {"issues": issues, "errors": errors}

    @staticmethod
    def _issue(meta, report):
        ident = meta["id"]
        return {**{key: value for key, value in meta.items() if key != "tide-issue"},
                "report": report, "attachments": [],
                "paths": {"report": f"issues/{ident}/report.md", "manifest": f"issues/{ident}/attachments.json"}}

    @contextlib.contextmanager
    def _lock(self):
        # D-Bus serializuje wywołania; flock chroni też dwie instancje magazynu/procesy.
        path = self._path(".git/tide-issues.lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _ledger(self):
        path = self._path(".git/tide-issues.json")
        if path.exists():
            try:
                # Dawny format mógł przekroczyć 128 MiB. Jednorazowy odczyt migracji
                # nie ma tej granicy; format 2 zapisuje tu tylko stałej wielkości licznik.
                ledger = json.loads(self._read(".git/tide-issues.json", limit=None))
                if (type(ledger["next"]) is not int or ledger["next"] < 1 or
                        type(ledger["format"]) is not int or ledger["format"] not in (1, 2)):
                    raise ValueError("niepoprawny licznik")
                if ledger["format"] == 1:
                    if not isinstance(ledger["requests"], dict):
                        raise ValueError("niepoprawne potwierdzenia")
                    for key, receipt in ledger["requests"].items():
                        self._save_receipt(key, receipt)
                    # Przełączenie dopiero po utrwaleniu wszystkich potwierdzeń.
                    # Przerwana migracja powtarza te same zapisy, zachowując format 1.
                    ledger = {"format": 2, "next": ledger["next"]}
                    self._save_ledger(ledger)
                return ledger
            except (KeyError, ValueError, TypeError, RecursionError) as exc:
                fail(f"Uszkodzony rejestr numerów spraw: {exc}.", "broken-file")
        # Kopia bez .git/rejestru: odbudowa z historii, także usuniętych spraw.
        history = self.repo.git.run(self.board.root, "log", "--all", "--format=", "--name-only",
                                    "--no-renames", "--", "issues", timeout=120)
        if not history.ok:
            fail("Nie można odczytać historii numerów spraw.", "io-error")
        nums = [int(m.group(1)) for m in re.finditer(r"(?:^|\n)issues/S-([1-9][0-9]*)/", history.out)]
        folder = self._path("issues")
        if folder.exists():
            nums += [int(m.group(1)) for m in map(ID_RE.fullmatch, os.listdir(folder)) if m]
        receipts = self._path(".git/tide-issue-requests")
        if receipts.exists():
            for name in os.listdir(receipts):
                if name.endswith(".json") and KEY_RE.fullmatch(name[:-5]):
                    receipt = self._receipt(name[:-5])
                    nums.append(int(self._id(receipt["issue"]["id"])[2:]))
        return {"format": 2, "next": max(nums, default=0) + 1}

    def _save_ledger(self, ledger):
        _atomic(self._path(".git/tide-issues.json"), _encoded(ledger) + "\n")

    def _receipt(self, key):
        try:
            receipt = json.loads(self._read(f".git/tide-issue-requests/{key}.json", limit=RECEIPT_MAX))
            if (type(receipt["published"]) is not bool or
                    not KEY_RE.fullmatch(receipt["fingerprint"]) or
                    not isinstance(receipt["issue"], dict)):
                raise ValueError("niepoprawne potwierdzenie")
            self._id(receipt["issue"]["id"])
            return receipt
        except FileNotFoundError:
            return None
        except (KeyError, ValueError, TypeError, RecursionError, BoardError) as exc:
            fail(f"Uszkodzone potwierdzenie utworzenia sprawy: {exc}.", "broken-file")

    def _save_receipt(self, key, receipt):
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            fail("Uszkodzony klucz potwierdzenia sprawy.", "broken-file")
        text = _encoded(receipt) + "\n"
        if len(text.encode("utf-8")) > RECEIPT_MAX:
            fail("Potwierdzenie sprawy przekracza dopuszczalny rozmiar.", "broken-file")
        folder = self._path(".git/tide-issue-requests")
        folder.mkdir(exist_ok=True)
        _sync_dir(folder.parent)
        _atomic(self._path(f".git/tide-issue-requests/{key}.json"), text)

    def create(self, request):
        data = validate(request)
        project = data.get("project")
        # Powtórka już zapisanej intencji przeżywa usunięcie projektu. Nowa sprawa
        # nie może jednak zainicjalizować Tablicy dla nieznanego projektu.
        if not self.board.exists() and project is not None:
            fail(f"Nie ma projektu „{project}”.")
        busy = self.repo.busy()
        if busy:
            fail(self.repo.busy_message(busy), "git-busy")
        self.board.ensure()
        with self._lock():
            busy = self.repo.busy()
            if busy:
                fail(self.repo.busy_message(busy), "git-busy")
            ledger = self._ledger()
            payload = {"title": data["title"], "report": data["report"], "project": project}
            fingerprint = hashlib.sha256(_encoded(payload).encode("utf-8")).hexdigest()
            request_id = data.get("requestId")
            key = hashlib.sha256(request_id.encode("utf-8")).hexdigest() if request_id else None
            previous = self._receipt(key) if key else None
            if previous:
                if previous["fingerprint"] != fingerprint:
                    fail("requestId był już użyty z innymi danymi.", "conflict")
                issue = previous["issue"]
                if previous["published"]:
                    return {"issue": issue, "existing": True, "commitError": self.repo.last_error}
            else:
                if project is not None and project not in self.board.load().projects:
                    fail(f"Nie ma projektu „{project}”.")
                folder = self._path("issues")
                occupied = [int(m.group(1)) for m in map(ID_RE.fullmatch, os.listdir(folder)) if m] \
                    if folder.exists() else []
                number = max(ledger["next"], max(occupied, default=0) + 1)
                if number > 999999999:
                    fail("Wyczerpano numery spraw.", "internal")
                now = datetime.fromtimestamp(self.board.clock(), timezone.utc).isoformat()
                meta = {"tide-issue": 1, "id": f"S-{number}", "title": data["title"], "project": project,
                        "state": "draft", "version": 1, "created": now, "updated": now, "card": None}
                issue = self._issue(meta, data["report"])
                ledger["next"] = number + 1
                self._save_ledger(ledger)
                if key:
                    self._save_receipt(key, {"fingerprint": fingerprint, "issue": issue, "published": False})
            self._publish(issue)
            if key:
                self._save_receipt(key, {"fingerprint": fingerprint, "issue": issue, "published": True})
            error = self.repo.commit(f"{issue['id']}: create issue", [f"issues/{issue['id']}"])
            return {"issue": issue, "existing": bool(previous), "commitError": error}

    def mutate(self, ident, request, operation):
        ident = self._id(ident)
        if operation == "Update":
            data = validate(request, update=True)
        else:
            if (not isinstance(request, dict) or request.keys() != {"expectedVersion"} or
                    type(request["expectedVersion"]) is not int or request["expectedVersion"] < 1):
                fail("Podaj wyłącznie expectedVersion jako dodatnią liczbę całkowitą.")
            data = request
        if not self.board.exists():
            fail(f"Nie ma sprawy {ident}.", "not-found")
        with self._lock():
            busy = self.repo.busy()
            if busy:
                fail(self.repo.busy_message(busy), "git-busy")
            # Odczyt i kontrola wersji pod tą samą blokadą co publikacja.
            issue = self._show(ident)
            if data["expectedVersion"] != issue["version"]:
                raise VersionConflict(issue["version"])
            allowed = {"Update": {"draft", "ready"}, "Submit": {"draft"},
                       "Reject": {"draft", "ready"}, "Reopen": {"rejected"}}
            if issue["state"] not in allowed[operation]:
                fail(f"Operacja {operation} jest niedozwolona w stanie {issue['state']}.")
            if operation == "Update":
                if data.get("project") is not None and data["project"] not in self.board.load().projects:
                    fail(f"Nie ma projektu „{data['project']}”.")
                changes = {key: value for key, value in data.items() if key != "expectedVersion"}
                if all(issue[key] == value for key, value in changes.items()):
                    return {"issue": issue, "commitError": self.repo.last_error}
                issue.update(changes)
                issue["state"] = "draft"
            elif operation == "Submit":
                # show zweryfikował metadane i istniejący manifest. Nie interpretujemy prozy.
                validate({key: issue[key] for key in ("title", "report", "project")})
                if not issue["report"].strip():
                    fail("Nie można przygotować sprawy z pustym raportem.")
                if issue["project"] is not None and issue["project"] not in self.board.load().projects:
                    fail(f"Nie ma projektu „{issue['project']}”.")
                for entry in issue["attachments"]:
                    attachments.checked_copy(self.root / "issues" / ident, entry)
                issue["state"] = "ready"
            else:
                issue["state"] = "rejected" if operation == "Reject" else "draft"
            issue["version"] += 1
            issue["updated"] = datetime.fromtimestamp(self.board.clock(), timezone.utc).isoformat()
            # Treść, metadane i wskazanie niezmiennego manifestu są jednym plikiem.
            # Rename publikuje cały nowy stan naraz.
            _atomic(self._path(f"issues/{ident}/report.md"), self._report_text(issue))
            error = self.repo.commit(f"{ident}: {operation.lower()} issue", [f"issues/{ident}/report.md"])
            return {"issue": issue, "commitError": error}

    @staticmethod
    def _report_text(issue):
        meta = {"tide-issue": 1, **{key: issue[key] for key in META_FIELDS - {"tide-issue"}}}
        manifest_ref = issue["paths"]["manifest"].removeprefix(f"issues/{issue['id']}/")
        if manifest_ref != "attachments.json":
            meta["attachmentManifest"] = manifest_ref
        return ("---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in sorted(meta.items()))
                + "---\n" + issue["report"])

    def _publish(self, issue):
        ident = self._id(issue["id"])
        folder = self._path("issues")
        folder.mkdir(exist_ok=True)
        target = self._path(f"issues/{ident}")
        if target.exists():
            if self._show(ident) != issue:
                fail(f"Sprawa {ident} różni się od zapisanej intencji.", "broken-file")
            return
        tmp = Path(tempfile.mkdtemp(prefix=".tide-tmp-", dir=folder))
        try:
            _atomic(tmp / "report.md", self._report_text(issue))
            _atomic(tmp / "attachments.json", '{"version":1,"attachments":[]}\n')
            os.rename(tmp, target)
            _sync_dir(folder)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp)

    def read_attachment(self, ident, request):
        ident = self._id(ident)
        data = attachments.request(request, "attachmentId")
        if not self.board.exists():
            fail(f"Nie ma sprawy {ident}.", "not-found")
        with self._lock():
            issue = self._show(ident)
            if data["expectedVersion"] != issue["version"]:
                raise VersionConflict(issue["version"])
            entry = next((item for item in issue["attachments"] if item["id"] == data["attachmentId"]), None)
            if entry is None:
                fail("Nie ma załącznika w tej wersji sprawy.", "not-found")
            raw = attachments.checked_copy(self.root / "issues" / ident, entry)
            return {"id": ident, "version": issue["version"], "attachment": entry}, raw

    def change_attachment(self, ident, request, operation, raw=b""):
        ident = self._id(ident)
        data = attachments.request(request, "name" if operation == "Attach" else "attachmentId")
        if operation == "Attach" and (not isinstance(raw, bytes) or len(raw) > attachments.FILE_MAX):
            fail("Załącznik przekracza 20 MiB albo nie jest tablicą bajtów.")
        if not self.board.exists():
            fail(f"Nie ma sprawy {ident}.", "not-found")
        with self._lock():
            busy = self.repo.busy()
            if busy:
                fail(self.repo.busy_message(busy), "git-busy")
            issue = self._show(ident)
            if data["expectedVersion"] != issue["version"]:
                raise VersionConflict(issue["version"])
            if issue["state"] not in {"draft", "ready"}:
                fail(f"Operacja {operation} jest niedozwolona w stanie {issue['state']}.")
            entries = list(issue["attachments"])
            if operation == "Attach":
                if len(entries) >= attachments.COUNT_MAX or sum(e["size"] for e in entries) + len(raw) > attachments.TOTAL_MAX:
                    fail("Przekroczono limit 20 plików albo 100 MiB załączników.")
                kind = attachments.media_type(data["name"], raw)
                attachment_id = "A-" + uuid.uuid4().hex
                entry = {"id": attachment_id, "name": data["name"], "path": "attachments/" + attachment_id,
                         "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "type": kind}
                folder = self._path(f"issues/{ident}/attachments")
                folder.mkdir(mode=0o700, exist_ok=True)
                _sync_dir(folder.parent)
                # Kopia jest trwała zanim powstanie manifest, który ją wymienia.
                self._write_blob(self._path(f"issues/{ident}/{entry['path']}"), raw)
                entries.append(entry)
            else:
                entries = [entry for entry in entries if entry["id"] != data["attachmentId"]]
                if len(entries) == len(issue["attachments"]):
                    fail("Nie ma załącznika w tej wersji sprawy.", "not-found")
            manifest = (_encoded({"version": 1, "attachments": entries}) + "\n").encode("utf-8")
            folder = self._path(f"issues/{ident}/manifests")
            folder.mkdir(mode=0o700, exist_ok=True)
            _sync_dir(folder.parent)
            ref = "manifests/" + hashlib.sha256(manifest).hexdigest() + ".json"
            self._write_blob(self._path(f"issues/{ident}/{ref}"), manifest)
            issue["attachments"] = entries
            issue["paths"]["manifest"] = f"issues/{ident}/{ref}"
            issue["state"] = "draft"
            issue["version"] += 1
            issue["updated"] = datetime.fromtimestamp(self.board.clock(), timezone.utc).isoformat()
            # Jedyny punkt publikacji wersji: atomowy raport wskazuje niezmienny manifest.
            _atomic(self._path(f"issues/{ident}/report.md"), self._report_text(issue))
            error = self.repo.commit(f"{ident}: {operation.lower()} issue attachment", [f"issues/{ident}"])
            return {"issue": issue, "commitError": error}

    @staticmethod
    def _write_blob(path, raw):
        fd, tmp = tempfile.mkstemp(prefix=".tide-tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
            _sync_dir(path.parent)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


class IssueService:
    """Publiczne odpowiedzi JSON; brak wyjątków wydostających się do QtDBus."""
    def __init__(self, board=None):
        self.store = IssueStore(board) if board is not None else None

    def call(self, method, *args):
        raw = b""
        try:
            if method == "Info":
                from . import resources
                result = {"capabilities": ["issue." + action for action in
                          ("create", "list", "show", "update", "submit", "reject", "reopen",
                           "attach", "detach", "attachment")] if self.store else []}
                result["resources"] = resources.info()
            elif self.store is None:
                fail("Sprawy są wyłączone w usłudze.", "unavailable")
            elif method == "Create":
                result = self.store.create(_json(args[0]))
            elif method in ("Update", "Submit", "Reject", "Reopen"):
                result = self.store.mutate(args[0], _json(args[1]), method)
            elif method in ("Attach", "Detach"):
                result = self.store.change_attachment(args[0], _json(args[1]), method,
                                                      args[2] if method == "Attach" else b"")
            elif method == "ReadAttachment":
                result, raw = self.store.read_attachment(args[0], _json(args[1]))
            elif method == "Show":
                result = {"issue": self.store.show(args[0])}
            elif method == "List":
                result = self.store.list()
            else:
                fail("Nieobsługiwana metoda API.", "api-incompatible")
            result = {"ok": True, "apiVersion": API_VERSION, **result}
        except BoardError as exc:
            result = {"ok": False, "apiVersion": API_VERSION, "error": exc.code, "message": str(exc)}
            if isinstance(exc, VersionConflict):
                result["currentVersion"] = exc.current
        except (OSError, UnicodeError) as exc:
            result = {"ok": False, "apiVersion": API_VERSION, "error": "io-error",
                      "message": f"Błąd odczytu lub zapisu sprawy: {exc}."}
        except Exception as exc:
            print(f"tide-daemon: sprawy: {exc!r}", flush=True)
            result = {"ok": False, "apiVersion": API_VERSION, "error": "internal",
                      "message": "Błąd usługi spraw — szczegóły w dzienniku usługi."}
        encoded = json.dumps(result, ensure_ascii=False)
        return (encoded, raw if result["ok"] else b"") if method == "ReadAttachment" else encoded
