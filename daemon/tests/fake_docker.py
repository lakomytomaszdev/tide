"""Atrapa Docker Engine API na tymczasowym sockecie unix — dla testów obszaru Docker.

Kształty odpowiedzi naśladują prawdziwe (Docker 29, API 1.56): lista kontenerów, inspect, /events
(strumień chunked z jednym JSON-em na linię), logi w ramkach 8-bajtowych, stats, /system/df
i endpointy prune. Żadnych prawdziwych danych — nazwy, obrazy i etykiety są wymyślone na wzór
kontenerów z pulpitu, bez zmiennych środowiskowych i ścieżek prywatnych plików.

Każde zapytanie trafia do `requests` jako (metoda, ścieżka, zapytanie), więc testy sprawdzają
dokładne wywołania. Akcje zmieniają stan atrapy i wysyłają zdarzenia jak Docker.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import socketserver
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlsplit

PROJECT = "com.docker.compose.project"
WORKDIR = "com.docker.compose.project.working_dir"
SERVICE = "com.docker.compose.service"
NUMBER = "com.docker.compose.container-number"
DEPENDS = "com.docker.compose.depends_on"
ONEOFF = "com.docker.compose.oneoff"
T0 = 1793090000  # 2026-10-27, około południa


def _iso(ts: float) -> str:
    if not ts:
        return "0001-01-01T00:00:00Z"
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".123456789Z"


class Container:
    def __init__(self, name, image, state="running", project="", workdir="", service="", number="", depends="",
                 oneoff=False, exit_code=0, oom=False, health="", ports=(), labels=None, tty=False,
                 started=T0 - 3600, finished=0, size_rw=4096, ident=None, error="", mounts=()):
        self.id = ident or hashlib.sha256(name.encode()).hexdigest()
        self.name = name
        self.image = image
        self.state = state
        self.exit_code = exit_code
        self.oom = oom
        self.health = health
        self.ports = list(ports)  # (hostIp, hostPort, containerPort, proto)
        self.tty = tty
        self.started = started
        self.finished = finished
        self.size_rw = size_rw
        self.error = error
        self.mounts = list(mounts)  # (typ, nazwa) — np. ("volume", "<64 znaki hex>") dla wolumenu anonimowego
        self.labels = dict(labels or {})
        if project:
            self.labels.update({PROJECT: project, WORKDIR: workdir, SERVICE: service, NUMBER: number or "1",
                                DEPENDS: depends, ONEOFF: "True" if oneoff else "False",
                                "com.docker.compose.version": "5.6.1"})
        self.logs = b""
        self.stats = {"total": 2_000_000, "pre_total": 1_000_000, "system": 400_000_000, "pre_system": 300_000_000,
                      "cpus": 8, "usage": 300 * 2**20, "inactive": 100 * 2**20, "limit": 16 * 2**30}

    def status_text(self) -> str:
        if self.state == "running":
            extra = f" ({self.health})" if self.health else ""
            return f"Up 2 hours{extra}"
        if self.state == "exited":
            return f"Exited ({self.exit_code}) 2 days ago"
        if self.state == "created":
            return "Created"
        return self.state.capitalize()

    def listed(self) -> dict:
        ports = [{"IP": ip, "PrivatePort": cport, "PublicPort": hport, "Type": proto}
                 for ip, hport, cport, proto in self.ports]
        return {"Id": self.id, "Names": ["/" + self.name], "Image": self.image,
                "ImageID": "sha256:" + "ab" * 32, "Command": "run", "Created": T0 - 86400,
                "Ports": ports, "Labels": dict(self.labels), "State": self.state, "Status": self.status_text(),
                "HostConfig": {"NetworkMode": "bridge"},
                "Health": {"Status": self.health or "none", "FailingStreak": 0},
                "NetworkSettings": {"Networks": {}},
                "Mounts": [{"Type": kind, "Name": name, "Destination": "/data"} for kind, name in self.mounts]}

    def inspected(self) -> dict:
        state = {"Status": self.state, "Running": self.state == "running", "Paused": self.state == "paused",
                 "Restarting": self.state == "restarting", "OOMKilled": self.oom, "Dead": self.state == "dead",
                 "Pid": 4242 if self.state == "running" else 0, "ExitCode": self.exit_code, "Error": self.error,
                 "StartedAt": _iso(self.started), "FinishedAt": _iso(self.finished)}
        if self.health:
            state["Health"] = {"Status": self.health, "FailingStreak": 0, "Log": []}
        return {"Id": self.id, "Name": "/" + self.name, "Created": _iso(T0 - 86400), "State": state,
                "RestartCount": 0, "Image": "sha256:" + "ab" * 32,
                "Config": {"Image": self.image, "Tty": self.tty, "Labels": dict(self.labels), "Env": []},
                "HostConfig": {}, "NetworkSettings": {"Ports": {}}}

    def stats_json(self) -> dict:
        s = self.stats
        return {"read": _iso(T0), "preread": _iso(T0 - 1),
                "cpu_stats": {"cpu_usage": {"total_usage": s["total"]}, "system_cpu_usage": s["system"],
                              "online_cpus": s["cpus"]},
                "precpu_stats": {"cpu_usage": {"total_usage": s["pre_total"]}, "system_cpu_usage": s["pre_system"],
                                 "online_cpus": s["cpus"]},
                "memory_stats": {"usage": s["usage"], "limit": s["limit"],
                                 "stats": {"inactive_file": s["inactive"], "active_file": 1}}}


def frame(stream: int, text: str) -> bytes:
    data = text.encode()
    return bytes([stream, 0, 0, 0]) + struct.pack(">I", len(data)) + data


class FakeDocker:
    def __init__(self, path: str):
        self.path = path
        self.containers: list[Container] = []
        self.requests: list[tuple[str, str, dict]] = []
        self.df = {"LayersSize": 0, "Images": [], "Containers": [], "Volumes": [], "BuildCache": []}
        self.prune_results = {
            "/images/prune": {"ImagesDeleted": [{"Untagged": "old:1"}, {"Deleted": "sha256:" + "cd" * 32}],
                              "SpaceReclaimed": 1_000_000_000},
            "/volumes/prune": {"VolumesDeleted": ["old_data"], "SpaceReclaimed": 400_000_000},
            "/build/prune": {"CachesDeleted": ["a", "b", "c"], "SpaceReclaimed": 5_900_000_000},
            "/containers/prune": {"ContainersDeleted": ["x"], "SpaceReclaimed": 86_000},
        }
        self.fail = {}             # ścieżka → (status, komunikat) — wymuszony błąd
        self.stats_delay = 0.0
        self.event_streams = 0     # ile razy ktoś połączył się z /events
        self._listeners: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.server = None
        self.thread = None

    # --- sterowanie --------------------------------------------------------------------

    def start(self):
        fake = self

        class Handler(_Handler):
            docker = fake

        if os.path.exists(self.path):
            os.unlink(self.path)
        self.server = _Server(self.path, Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.drop_events()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if os.path.exists(self.path):
            os.unlink(self.path)

    def add(self, *containers: Container):
        self.containers.extend(containers)

    def find(self, ident: str) -> Container | None:
        for c in self.containers:
            if c.id == ident or c.name == ident or (len(ident) >= 12 and c.id.startswith(ident)):
                return c
        return None

    def emit(self, container: Container, action: str):
        event = {"status": action, "id": container.id, "from": container.image, "Type": "container",
                 "Action": action, "Actor": {"ID": container.id, "Attributes": {"name": container.name}},
                 "scope": "local", "time": int(time.time()), "timeNano": time.time_ns()}
        with self._lock:
            listeners = list(self._listeners)
        for q in listeners:
            q.put(event)

    def drop_events(self):
        """Zrywa wszystkie strumienie /events (jak restart Dockera)."""
        with self._lock:
            listeners, self._listeners = self._listeners, []
        for q in listeners:
            q.put(None)

    def listeners(self) -> int:
        with self._lock:
            return len(self._listeners)

    def calls(self, method=None, prefix=""):
        return [(m, p, q) for m, p, q in self.requests if (method is None or m == method) and p.startswith(prefix)]

    def mutating(self):
        return [(m, p, q) for m, p, q in self.requests if m != "GET"]


class _Server(socketserver.ThreadingUnixStreamServer):
    def handle_error(self, request, client_address):
        # Klient, który zrezygnował (limit czasu w teście), to nie błąd atrapy.
        import sys
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    docker: FakeDocker = None

    def log_message(self, *args):  # cisza w testach (adres klienta unix to pusty napis)
        pass

    def address_string(self):
        return "unix"

    def _send(self, status: int, body=None, chunked=False, content_type="application/json"):
        data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Api-Version", "1.56")
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            # Celowo w kilku kawałkach, także rozcinając znaki — klient musi je skleić.
            step = max(1, len(data) // 3)
            for i in range(0, len(data), step):
                part = data[i:i + step]
                self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        else:
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        self.close_connection = True

    def _error(self, status: int, message: str):
        self._send(status, {"message": message})

    def _route(self, method: str):
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        fake = self.docker
        fake.requests.append((method, path, query))
        if path in fake.fail:
            status, message = fake.fail[path]
            return self._error(status, message)
        if method == "GET" and path == "/version":
            return self._send(200, {"Version": "29.9.2", "ApiVersion": "1.56", "MinAPIVersion": "1.40"})
        if method == "GET" and path == "/_ping":
            return self._send(200, b"OK", content_type="text/plain")
        if method == "GET" and path == "/events":
            return self._events()
        if method == "GET" and path == "/containers/json":
            items = [c.listed() for c in fake.containers]
            labels = json.loads(query.get("filters") or "{}").get("label") or []
            for label in labels:
                key, _, value = label.partition("=")
                items = [i for i in items if i["Labels"].get(key) == value]
            return self._send(200, items, chunked=True)
        if method == "GET" and path == "/system/df":
            return self._send(200, fake.df)
        if method == "POST" and path in fake.prune_results:
            return self._send(200, fake.prune_results[path])
        if path.startswith("/containers/"):
            rest = path[len("/containers/"):]
            ident, _, action = rest.partition("/")
            c = fake.find(ident)
            if c is None:
                return self._error(404, f"No such container: {ident}")
            return self._container(method, c, action, query)
        return self._error(404, "page not found")

    def _container(self, method, c: Container, action, query):
        fake = self.docker
        if method == "GET" and action == "json":
            return self._send(200, c.inspected())
        if method == "GET" and action == "stats":
            if fake.stats_delay:
                time.sleep(fake.stats_delay)
            return self._send(200, c.stats_json())
        if method == "GET" and action == "logs":
            return self._send(200, c.logs, chunked=True, content_type="application/vnd.docker.multiplexed-stream")
        if method == "POST" and action == "start":
            if c.state == "running":
                return self._send(304)
            c.state, c.started = "running", time.time()
            fake.emit(c, "start")
            return self._send(204)
        if method == "POST" and action == "stop":
            if c.state != "running":
                return self._send(304)
            c.state, c.exit_code, c.finished = "exited", 0, time.time()
            fake.emit(c, "kill")
            fake.emit(c, "die")
            fake.emit(c, "stop")
            return self._send(204)
        if method == "POST" and action == "restart":
            c.state, c.started = "running", time.time()
            fake.emit(c, "restart")
            return self._send(204)
        if method == "DELETE" and not action:
            if c.state == "running":
                return self._error(409, "cannot remove container: container is running: stop the container "
                                        "before removing or force remove")
            fake.containers.remove(c)
            fake.emit(c, "destroy")
            return self._send(204)
        return self._error(404, "page not found")

    def _events(self):
        fake = self.docker
        q: queue.Queue = queue.Queue()
        with fake._lock:
            fake._listeners.append(q)
            fake.event_streams += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.flush()
        try:
            while True:
                try:
                    event = q.get(timeout=0.2)
                except queue.Empty:
                    if fake.server is None:
                        break
                    continue
                if event is None:
                    break
                data = json.dumps(event).encode() + b"\n"
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            pass
        finally:
            with fake._lock:
                if q in fake._listeners:
                    fake._listeners.remove(q)
        self.close_connection = True

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")


# --- świat testowy -------------------------------------------------------------------------

def make_workspace(root: str) -> dict:
    """Katalog projektów z repozytoriami (bez gita — wystarczą katalogi i plik .git worktree):
    CookApp, LogViewer z worktree w LogViewer-worktrees/feature i repozytorium poza katalogiem."""
    work = os.path.join(root, "work")
    cookapp = os.path.join(work, "CookApp")
    os.makedirs(os.path.join(cookapp, ".git"))
    app4 = os.path.join(work, "LogViewer")
    os.makedirs(os.path.join(app4, ".git", "worktrees", "feature"))
    feature = os.path.join(work, "LogViewer-worktrees", "feature")
    os.makedirs(os.path.join(feature, "tools", "localhost"))
    with open(os.path.join(feature, ".git"), "w", encoding="utf-8") as fh:
        fh.write(f"gitdir: {app4}/.git/worktrees/feature\n")
    outside = os.path.join(root, "Downloads", "other-repo")
    os.makedirs(os.path.join(outside, ".git"))
    youtrack = os.path.join(work, "youtrack")  # katalog bez repozytorium
    os.makedirs(youtrack)
    return {"work": work, "cookapp": cookapp, "app4": app4, "feature": feature,
            "feature_compose": os.path.join(feature, "tools", "localhost"), "outside": outside, "youtrack": youtrack}


def populate(fake: FakeDocker, ws: dict) -> dict:
    """Kontenery na wzór pulpitu: projekty compose, jeden bez compose, testcontainers, padnięty z kodem 137."""
    c = {}
    c["api"] = Container("cookapp-api", "cookapp-api", project="cookapp", workdir=ws["cookapp"], service="api",
                         depends="postgres:service_healthy:false", health="healthy",
                         ports=[("0.0.0.0", 8080, 8080, "tcp"), ("::", 8080, 8080, "tcp")])
    c["pg"] = Container("cookapp-postgres", "postgres:16", project="cookapp", workdir=ws["cookapp"],
                        service="postgres", health="healthy",
                        ports=[("0.0.0.0", 5434, 5432, "tcp"), ("::", 5434, 5432, "tcp")])
    c["docs"] = Container("cookapp-docs", "cookapp-docs", project="cookapp", workdir=ws["cookapp"], service="docs",
                          depends="api:service_started:false", ports=[("0.0.0.0", 8000, 80, "tcp")])
    c["web"] = Container("app4-release-localhost-web-1", "app4-logviewer-localhost:feature",
                         project="app4-release-localhost", workdir=ws["feature_compose"], service="web", number="1",
                         ports=[("127.0.0.1", 15075, 8080, "tcp")])
    c["yt"] = Container("youtrack", "jetbrains/youtrack:2026.2.19656", project="youtrack", workdir=ws["youtrack"],
                        service="youtrack", ports=[("0.0.0.0", 8111, 8080, "tcp")])
    c["legal"] = Container("legal-agents-postgres", "pgvector/pgvector:pg16", project="agent-team",
                           workdir=ws["outside"], service="postgres", health="healthy",
                           ports=[("0.0.0.0", 5432, 5432, "tcp")])
    c["lib"] = Container("legal-agents-library-work", "pgvector/pgvector:pg16",
                         ports=[("127.0.0.1", 55440, 5432, "tcp")])
    c["bot"] = Container("translations-slack-bot", "translationsdownloader-slack-bot", state="exited",
                         project="translationsdownloader", workdir="/nonexistent/TranslationsDownloader",
                         service="slack-bot", exit_code=137, finished=T0 - 22 * 3600, size_rw=86_000)
    c["old"] = Container("app4-release-t-postgres-1", "postgres:16-alpine", state="exited", project="app4-release-t",
                         workdir="/nonexistent/preprod", service="postgres", number="1", exit_code=0,
                         finished=T0 - 14 * 86400)
    c["termed"] = Container("app4-release-i-web-1", "app4-logviewer-i:local-ui", state="exited",
                            project="app4-release-i", workdir=ws["feature_compose"], service="web", number="1",
                            exit_code=143, finished=T0 - 2 * 86400)
    c["ryuk"] = Container("testcontainers-ryuk-c6715127", "testcontainers/ryuk:0.11.0",
                          labels={"org.testcontainers": "true", "org.testcontainers.sessionId": "c6715127"},
                          ports=[("0.0.0.0", 34137, 8080, "tcp")])
    c["tcpg"] = Container("cool_diffie", "postgres:16-alpine",
                          labels={"org.testcontainers": "true", "org.testcontainers.sessionId": "c6715127"},
                          ports=[("0.0.0.0", 34139, 5432, "tcp")])
    c["api"].logs = (frame(1, "12:01:44 info: Now listening on: http://[::]:8080\n12:01:44 info: Application ")
                     + frame(1, "started\n") + frame(2, "12:04:02 warn: wolne zapytanie (612 ms)\n")
                     + frame(1, "12:05:00 info: GET /api/cookapp → 200\n"))
    fake.add(*c.values())
    fake.df = {
        "LayersSize": 10_369_245_190,
        "Images": [
            {"Id": "sha256:aa", "RepoTags": ["cookapp-api:latest"], "Containers": 1, "Size": 456_663_599,
             "SharedSize": 341_723_299},
            {"Id": "sha256:bb", "RepoTags": ["old:1"], "Containers": 0, "Size": 700_000_000, "SharedSize": 100_000_000},
            {"Id": "sha256:cc", "RepoTags": ["older:2"], "Containers": 0, "Size": 400_000_000, "SharedSize": -1},
            {"Id": "sha256:dd", "RepoTags": ["stopped-only:1"], "Containers": 1, "Size": 50_000_000, "SharedSize": 0},
        ],
        "Containers": [
            {"Id": c["api"].id, "State": "running", "SizeRw": 28_672},
            {"Id": c["bot"].id, "State": "exited", "SizeRw": 86_000},
            {"Id": c["old"].id, "State": "exited", "SizeRw": 0},
        ],
        "Volumes": [
            {"Name": "recipes_pgdata", "UsageData": {"RefCount": 1, "Size": 82_832_645}},
            {"Name": "old_data", "UsageData": {"RefCount": 0, "Size": 400_000_000}},
            {"Name": "anon", "UsageData": {"RefCount": 0, "Size": -1}},
        ],
        "BuildCache": [
            {"ID": "a", "InUse": False, "Shared": False, "Size": 347_279_469},
            {"ID": "b", "InUse": False, "Shared": True, "Size": 1_000},
            {"ID": "c", "InUse": True, "Shared": False, "Size": 5_000_000},
        ],
    }
    return c
