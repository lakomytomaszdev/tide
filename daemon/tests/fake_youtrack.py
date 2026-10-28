"""Atrapa REST API YouTracka do testów synchronizacji (PT-7): zgłoszenia w pamięci, GET jednego zgłoszenia,
POST zmiany stanu (pole State, kształt jak w prawdziwym YouTracku: StateIssueCustomField / StateBundleElement),
wyszukiwanie zgłoszeń użytkownika i dziennik wywołań (metoda, ścieżka, nagłówek Authorization, treść).

Kształty odpowiedzi skopiowane z odczytu (GET) prawdziwego zgłoszenia — testy nigdy nie piszą do prawdziwego
serwera: usługa testowa dostaje adres tej atrapy albo zamknięty port (fixtures/youtrack-closed.json).
"""
import json
import re
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORKFLOW = ("Submitted", "Open", "In Progress", "To be discussed", "Reopened", "Can't Reproduce", "Duplicate",
            "Fixed", "Won't fix", "Incomplete", "Obsolete", "Verified")
RESOLVED = ("Can't Reproduce", "Duplicate", "Fixed", "Won't fix", "Incomplete", "Obsolete", "Verified")
BASE_MS = 1_790_000_000_000


class FakeYouTrack:
    def __init__(self, token: str):
        self.token = token
        self.issues: dict[str, dict] = {}
        self.calls: list = []
        self.status = 200            # inny kod — każda odpowiedź z tym kodem (np. 503)
        self.workflow = list(WORKFLOW)
        self.projects: set | None = None   # None — każdy klucz projektu istnieje; zbiór — inne dostają 400 przy zakładaniu
        self.comments: dict[str, list] = {}    # klucz → [{"id", "text"}]
        self.links: list = []                  # (zgłoszenie, polecenie) z POST /api/commands
        self.lose_replies = 0                  # tyle następnych POST-ów serwer wykona, ale odpowiedź zgubi (zerwane połączenie)
        self.clock_ms = BASE_MS
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake._handle(self, None)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                fake._handle(self, self.rfile.read(length).decode("utf-8") if length else "")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # --- dane ------------------------------------------------------------------------------------

    def add(self, ident: str, state: str = "Open", summary: str = "", description: str = "", mine: str = "reporter",
            updated: int | None = None, created: int | None = None) -> None:
        """mine: assignee (przypisane do mnie), reporter (zgłoszone przeze mnie, bez przypisania), other."""
        with self.lock:
            stamp = updated if updated is not None else self._tick()
            self.issues[ident] = {"state": state, "summary": summary or f"Zgłoszenie {ident}",
                                  "description": description, "mine": mine, "created": created or stamp,
                                  "updated": stamp}

    def human_sets(self, ident: str, state: str) -> None:
        """Zmiana ręczna w YouTracku (człowiek w przeglądarce)."""
        with self.lock:
            self.issues[ident]["state"] = state
            self.issues[ident]["updated"] = self._tick()

    def issue_with(self, marker: str) -> list:
        """Klucze zgłoszeń, których opis zawiera napis (np. znacznik karty)."""
        return [i for i, issue in sorted(self.issues.items()) if marker in issue["description"]]

    def state(self, ident: str) -> str:
        return self.issues[ident]["state"]

    def posts(self) -> list:
        return [(path, json.loads(body)) for method, path, _auth, body in self.calls if method == "POST"]

    def gets(self) -> list:
        return [path for method, path, _auth, _body in self.calls if method == "GET"]

    def _tick(self) -> int:
        self.clock_ms += 1000
        return self.clock_ms

    # --- HTTP ------------------------------------------------------------------------------------

    def _handle(self, handler, body) -> None:
        with self.lock:
            self.calls.append((handler.command, handler.path, handler.headers.get("Authorization"), body or ""))
            if self.status != 200:
                return self._send(handler, self.status, {"error": "unavailable"})
            if handler.headers.get("Authorization") != f"Bearer {self.token}":
                return self._send(handler, 401, {"error": "Unauthorized"})
            parts = urllib.parse.urlsplit(handler.path)
            query = urllib.parse.parse_qs(parts.query)
            if parts.path == "/api/issues" and handler.command == "GET":
                return self._send(handler, 200, self._search(query.get("query", [""])[0]))
            if parts.path == "/api/issues" and handler.command == "POST":
                return self._create(handler, body)
            if parts.path == "/api/commands" and handler.command == "POST":
                return self._command(handler, body)
            if parts.path.startswith("/api/issues/") and parts.path.endswith("/comments"):
                ident = urllib.parse.unquote(parts.path[len("/api/issues/"):-len("/comments")])
                if ident not in self.issues:
                    return self._send(handler, 404, {"error": "Not Found"})
                if handler.command == "POST":
                    try:
                        text = json.loads(body or "{}")["text"]
                    except (ValueError, KeyError, TypeError):
                        return self._send(handler, 400, {"error": "bad_request"})
                    if not isinstance(text, str) or not text.strip():
                        return self._send(handler, 400, {"error": "bad_request", "error_description": "Empty comment"})
                    items = self.comments.setdefault(ident, [])
                    items.append({"id": f"{len(items) + 1}-0", "text": text})
                    return self._send(handler, 200, items[-1])
                return self._send(handler, 200, list(self.comments.get(ident, [])))
            if parts.path.startswith("/api/issues/"):
                ident = urllib.parse.unquote(parts.path[len("/api/issues/"):])
                issue = self.issues.get(ident)
                if issue is None:
                    return self._send(handler, 404, {"error": "Not Found"})
                if handler.command == "POST":
                    return self._post(handler, ident, issue, body)
                return self._send(handler, 200, self._issue_json(ident, issue))
            return self._send(handler, 404, {"error": "Not Found"})

    def _create(self, handler, body) -> None:
        try:
            data = json.loads(body or "{}")
            project = data["project"]["shortName"]
            summary = data["summary"]
        except (ValueError, KeyError, TypeError):
            return self._send(handler, 400, {"error": "bad_request", "error_description": "Bad body"})
        if not isinstance(summary, str) or not summary.strip():
            return self._send(handler, 400, {"error": "bad_request", "error_description": "Summary required"})
        if self.projects is not None and project not in self.projects:
            return self._send(handler, 400, {"error": "bad_request", "error_description": f"Unknown project {project}"})
        number = 1 + max([int(i.rsplit("-", 1)[1]) for i in self.issues if i.rsplit("-", 1)[0] == project] or [0])
        ident = f"{project}-{number}"
        stamp = self._tick()
        self.issues[ident] = {"state": "Submitted", "summary": summary, "description": data.get("description") or "",
                              "mine": "reporter", "created": stamp, "updated": stamp}
        return self._send(handler, 200, {"idReadable": ident, "summary": summary, "$type": "Issue"})

    def _command(self, handler, body) -> None:
        try:
            data = json.loads(body or "{}")
            command = data["query"]
            idents = [i["idReadable"] for i in data["issues"]]
        except (ValueError, KeyError, TypeError):
            return self._send(handler, 400, {"error": "bad_request", "error_description": "Bad body"})
        words = command.split()
        target = words[-1] if words else ""
        if any(i not in self.issues for i in idents) or target not in self.issues:
            return self._send(handler, 400, {"error": "bad_request", "error_description": "Unknown issue"})
        for ident in idents:
            if (ident, command) not in self.links:     # ponowienie polecenia niczego nie zmienia, jak w YouTracku
                self.links.append((ident, command))
        return self._send(handler, 200, {})

    def _post(self, handler, ident, issue, body) -> None:
        try:
            data = json.loads(body or "{}")
            field = data["customFields"][0]
            name = field["value"]["name"]
        except (ValueError, KeyError, IndexError, TypeError):
            return self._send(handler, 400, {"error": "bad_request", "error_description": "Bad body"})
        if field.get("name") != "State" or field.get("$type") != "StateIssueCustomField":
            return self._send(handler, 400, {"error": "bad_request", "error_description": "Unknown field"})
        if name not in self.workflow:
            return self._send(handler, 400, {"error": "bad_request",
                                             "error_description": f"Value {name} not found in State bundle"})
        issue["state"] = name
        issue["updated"] = self._tick()
        return self._send(handler, 200, self._issue_json(ident, issue))

    def _search(self, text: str) -> list:
        out = []
        phrases = re.findall(r'"([^"]+)"', text)
        project = re.search(r"project:\s*(\S+)", text)
        for ident, issue in sorted(self.issues.items()):
            if project and ident.rsplit("-", 1)[0] != project.group(1):
                continue
            if any(p not in issue["description"] for p in phrases):
                continue
            if "#Unresolved" in text and issue["state"] in RESOLVED:
                continue
            if "for: me" in text and issue["mine"] != "assignee":
                continue
            if "reporter: me" in text and issue["mine"] != "reporter":
                continue
            item = self._issue_json(ident, issue)
            item["description"] = issue["description"]
            out.append(item)
        return out

    def _issue_json(self, ident: str, issue: dict) -> dict:
        return {"idReadable": ident, "summary": issue["summary"], "updated": issue["updated"], "created": issue["created"],
                "resolved": issue["updated"] if issue["state"] in RESOLVED else None,
                "project": {"shortName": ident.rsplit("-", 1)[0], "$type": "Project"},
                "customFields": [
                    {"value": {"name": "Normal", "$type": "EnumBundleElement"}, "name": "Priority",
                     "$type": "SingleEnumIssueCustomField"},
                    {"value": {"name": issue["state"], "$type": "StateBundleElement"}, "name": "State",
                     "$type": "StateIssueCustomField"},
                    {"value": None, "name": "Assignee", "$type": "SingleUserIssueCustomField"}],
                "$type": "Issue"}

    def _send(self, handler, code: int, payload) -> None:
        if self.lose_replies > 0 and handler.command == "POST" and code == 200:
            self.lose_replies -= 1      # zmiana już zapisana, ale klient dostaje zerwane połączenie
            handler.close_connection = True
            handler.connection.shutdown(socket.SHUT_RDWR)
            return
        body = json.dumps(payload).encode()
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
