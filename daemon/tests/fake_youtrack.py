"""Atrapa REST API YouTracka do testów synchronizacji (PT-7): zgłoszenia w pamięci, GET jednego zgłoszenia,
POST zmiany stanu (pole State, kształt jak w prawdziwym YouTracku: StateIssueCustomField / StateBundleElement),
wyszukiwanie zgłoszeń użytkownika i dziennik wywołań (metoda, ścieżka, nagłówek Authorization, treść).

Kształty odpowiedzi skopiowane z odczytu (GET) prawdziwego zgłoszenia — testy nigdy nie piszą do prawdziwego
serwera: usługa testowa dostaje adres tej atrapy albo zamknięty port (fixtures/youtrack-closed.json).
"""
import json
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
            updated: int | None = None) -> None:
        """mine: assignee (przypisane do mnie), reporter (zgłoszone przeze mnie, bez przypisania), other."""
        with self.lock:
            self.issues[ident] = {"state": state, "summary": summary or f"Zgłoszenie {ident}",
                                  "description": description, "mine": mine,
                                  "updated": updated if updated is not None else self._tick()}

    def human_sets(self, ident: str, state: str) -> None:
        """Zmiana ręczna w YouTracku (człowiek w przeglądarce)."""
        with self.lock:
            self.issues[ident]["state"] = state
            self.issues[ident]["updated"] = self._tick()

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
            if parts.path.startswith("/api/issues/"):
                ident = urllib.parse.unquote(parts.path[len("/api/issues/"):])
                issue = self.issues.get(ident)
                if issue is None:
                    return self._send(handler, 404, {"error": "Not Found"})
                if handler.command == "POST":
                    return self._post(handler, ident, issue, body)
                return self._send(handler, 200, self._issue_json(ident, issue))
            return self._send(handler, 404, {"error": "Not Found"})

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
        for ident, issue in sorted(self.issues.items()):
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
        return {"idReadable": ident, "summary": issue["summary"], "updated": issue["updated"],
                "resolved": issue["updated"] if issue["state"] in RESOLVED else None,
                "project": {"shortName": ident.rsplit("-", 1)[0], "$type": "Project"},
                "customFields": [
                    {"value": {"name": "Normal", "$type": "EnumBundleElement"}, "name": "Priority",
                     "$type": "SingleEnumIssueCustomField"},
                    {"value": {"name": issue["state"], "$type": "StateBundleElement"}, "name": "State",
                     "$type": "StateIssueCustomField"},
                    {"value": None, "name": "Assignee", "$type": "SingleUserIssueCustomField"}],
                "$type": "Issue"}

    @staticmethod
    def _send(handler, code: int, payload) -> None:
        body = json.dumps(payload).encode()
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
