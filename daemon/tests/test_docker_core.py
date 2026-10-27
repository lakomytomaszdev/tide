"""Obszar Docker bez Qt: klient Engine API, migawka, porty, dysk, logi, statystyki i akcje.

Wszystko na atrapie Engine API (fake_docker.py) na tymczasowym sockecie — prawdziwego Dockera
testy nie dotykają (daemon/tests/__init__.py ustawia DOCKER_HOST na nieistniejący socket).
"""
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest

from .fake_docker import Container, FakeDocker, frame, make_workspace, populate
from tide_daemon.docker import client as client_mod
from tide_daemon.docker import disk as disk_mod
from tide_daemon.docker import snapshot as snap
from tide_daemon.docker import stats as stats_mod
from tide_daemon.docker.client import DockerUnavailable, EngineClient, log_lines, socket_path
from tide_daemon.docker.engine import DockerEngine
from tide_daemon.docker.repos import resolve


class World(unittest.TestCase):
    """Atrapa Dockera z kontenerami na wzór pulpitu i katalog projektów z repozytoriami."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-docker-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = make_workspace(self.tmp)
        self.fake = FakeDocker(os.path.join(self.tmp, "docker.sock")).start()
        self.addCleanup(self.fake.stop)
        self.c = populate(self.fake, self.ws)
        self.engine = DockerEngine(EngineClient(self.fake.path), workspace=self.ws["work"], clock=lambda: 1793090000)

    def snapshot(self):
        return self.engine.refresh()

    def group(self, snapshot, gid):
        return next(g for g in snapshot["groups"] if g["id"] == gid)

    def item(self, snapshot, name):
        return next(c for g in snapshot["groups"] for c in g["containers"] if c["name"] == name)


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class SocketPathTest(unittest.TestCase):
    def test_docker_host(self):
        self.assertEqual(socket_path({}), ("/var/run/docker.sock", ""))
        self.assertEqual(socket_path({"DOCKER_HOST": "unix:///run/user/1000/docker.sock"}),
                         ("/run/user/1000/docker.sock", ""))
        path, problem = socket_path({"DOCKER_HOST": "tcp://10.0.0.5:2376"})
        self.assertEqual(path, "")
        self.assertIn("tylko lokalny socket unix", problem)
        self.assertIn("tcp://", problem)
        self.assertNotIn("10.0.0.5", problem)

    def test_unsupported_host_is_a_state(self):
        engine = DockerEngine(EngineClient(env={"DOCKER_HOST": "ssh://me@box"}))
        snapshot = engine.refresh()
        self.assertEqual((snapshot["available"], snapshot["state"]), (False, "unsupported"))
        self.assertEqual(snapshot["groups"], [])
        self.assertEqual(snapshot["summary"]["running"], 0)


class UnavailableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-docker-off-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_missing_socket(self):
        engine = DockerEngine(EngineClient(os.path.join(self.tmp, "brak.sock")))
        snapshot = engine.refresh()
        self.assertEqual((snapshot["available"], snapshot["state"]), (False, "unavailable"))
        self.assertIn("Docker nie działa", snapshot["error"])
        self.assertFalse(engine.stats()["ok"])
        self.assertFalse(engine.disk()["ok"])
        self.assertEqual(engine.action("x", "start")["error"], "nie ma takiego kontenera")

    def test_refused_socket(self):
        path = os.path.join(self.tmp, "stary.sock")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(path)
        sock.close()  # plik socketu został, nikt nie słucha
        snapshot = DockerEngine(EngineClient(path)).refresh()
        self.assertEqual(snapshot["state"], "unavailable")
        self.assertIn("odrzuca połączenia", snapshot["error"])

    @unittest.skipIf(os.geteuid() == 0, "root ma dostęp do każdego socketu")
    def test_permission_denied(self):
        path = os.path.join(self.tmp, "cudzy.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        self.addCleanup(server.close)
        os.chmod(path, 0)
        snapshot = DockerEngine(EngineClient(path)).refresh()
        self.assertEqual((snapshot["available"], snapshot["state"]), (False, "denied"))
        self.assertIn("grupy docker", snapshot["error"])

    def test_hanging_daemon_times_out(self):
        path = os.path.join(self.tmp, "wisi.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        self.addCleanup(server.close)
        snapshot = DockerEngine(EngineClient(path, timeout=0.3)).refresh()
        self.assertEqual(snapshot["state"], "unavailable")
        self.assertIn("nie odpowiada", snapshot["error"])


class BacklogTest(unittest.TestCase):
    def test_full_backlog_is_retried_not_failed(self):
        tmp = tempfile.mkdtemp(prefix="tide-docker-backlog-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "zatkany.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(0)
        self.addCleanup(server.close)
        waiting = []
        for _ in range(4):  # zapchana kolejka: kolejny connect z limitem czasu dostałby od razu EAGAIN
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.setblocking(False)
            try:
                s.connect(path)
            except BlockingIOError:
                s.close()
                break
            waiting.append(s)

        def serve():
            time.sleep(0.3)
            for s in waiting:  # zapychacze rezygnują — ich wpisy w kolejce dadzą puste połączenia
                s.close()
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                if data:
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
                conn.close()
        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(EngineClient(path, timeout=3).request("GET", "/_ping"), (200, b"OK"))


class DeadlineTest(unittest.TestCase):
    def test_trickling_response_bounded_by_one_deadline(self):
        tmp = tempfile.mkdtemp(prefix="tide-docker-slow-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "wolny.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(4)
        self.addCleanup(server.close)

        def serve():
            try:
                conn, _ = server.accept()
            except OSError:
                return
            conn.recv(4096)
            try:  # po bajcie co 0,1 s — żaden pojedynczy odczyt nie przekracza limitu
                for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + b"x" * 1000:
                    conn.sendall(bytes([byte]))
                    time.sleep(0.1)
            except OSError:
                pass
            conn.close()
        threading.Thread(target=serve, daemon=True).start()
        start = time.monotonic()
        with self.assertRaises(DockerUnavailable) as ctx:
            EngineClient(path).request("GET", "/version", timeout=0.6)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertIn("nie odpowiada na czas", ctx.exception.message)


class ClientTest(World):
    def test_chunked_and_content_length(self):
        client = EngineClient(self.fake.path)
        items = client.containers()  # atrapa wysyła listę w kilku kawałkach chunked
        self.assertEqual(len(items), len(self.fake.containers))
        self.assertEqual(client.version()["ApiVersion"], "1.56")  # Content-Length
        status, body = client.request("GET", "/_ping")
        self.assertEqual((status, body), (200, b"OK"))

    def test_http_error_carries_docker_message(self):
        with self.assertRaises(client_mod.DockerError) as ctx:
            EngineClient(self.fake.path).inspect("nie-ma")
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("No such container", ctx.exception.message)

    def test_label_filter_is_sent(self):
        EngineClient(self.fake.path).containers({"label": ["com.docker.compose.project=cookapp"]})
        method, path, query = self.fake.requests[-1]
        self.assertEqual((method, path, query["all"]), ("GET", "/containers/json", "1"))
        self.assertEqual(json.loads(query["filters"]), {"label": ["com.docker.compose.project=cookapp"]})


class SnapshotTest(World):
    def test_groups_and_repos(self):
        s = self.snapshot()
        self.assertTrue(s["available"])
        self.assertEqual(s["engine"], {"version": "29.9.2", "apiVersion": "1.56"})
        ids = [g["id"] for g in s["groups"]]
        # Najpierw projekty compose z działającymi kontenerami (alfabetycznie), potem zatrzymane,
        # na końcu „bez compose” i testcontainers.
        self.assertEqual(ids, ["compose:agent-team", "compose:app4-release-localhost", "compose:cookapp",
                               "compose:youtrack", "compose:app4-release-i", "compose:app4-release-t",
                               "compose:translationsdownloader", "standalone", "testcontainers"])
        cookapp = self.group(s, "compose:cookapp")
        self.assertEqual(cookapp["repo"], {"name": "CookApp", "path": os.path.realpath(self.ws["cookapp"]),
                                           "worktree": ""})
        self.assertEqual((cookapp["running"], cookapp["total"], cookapp["failed"]), (3, 3, 0))
        app4 = self.group(s, "compose:app4-release-localhost")
        self.assertEqual(app4["repo"]["name"], "LogViewer")
        self.assertEqual(app4["repo"]["path"], os.path.realpath(self.ws["app4"]))
        self.assertEqual(app4["repo"]["worktree"], os.path.realpath(self.ws["feature"]))
        self.assertEqual(app4["workingDir"], self.ws["feature_compose"])
        self.assertIsNone(self.group(s, "compose:youtrack")["repo"])          # katalog bez gita
        self.assertIsNone(self.group(s, "compose:agent-team")["repo"])    # repozytorium spoza katalogu projektów
        self.assertIsNone(self.group(s, "compose:translationsdownloader")["repo"])  # katalog nie istnieje
        standalone = self.group(s, "standalone")
        self.assertEqual((standalone["label"], standalone["project"], standalone["kind"]), ("bez compose", "", "standalone"))
        self.assertEqual([c["name"] for c in standalone["containers"]], ["legal-agents-library-work"])

    def test_container_fields(self):
        s = self.snapshot()
        api = self.item(s, "cookapp-api")
        self.assertEqual(api["id"], self.c["api"].id)
        self.assertEqual(api["shortId"], self.c["api"].id[:12])
        self.assertEqual((api["state"], api["running"], api["health"], api["stateText"]),
                         ("running", True, "healthy", "działa · zdrowy"))
        self.assertNotIn("status", api)  # tekst „Up 2 hours” zmienia się sam — okno liczy wiek z czasów
        self.assertEqual(api["startedTs"], 1793090000 - 3600)
        self.assertEqual(api["finishedTs"], 0)  # „0001-01-01T00:00:00Z”
        self.assertEqual((api["project"], api["service"]), ("cookapp", "api"))
        web = self.item(s, "app4-release-localhost-web-1")
        self.assertEqual(web["displayName"], "web-1")  # nazwa wygenerowana przez compose
        self.assertEqual(api["displayName"], "cookapp-api")  # własna container_name zostaje
        self.assertEqual(self.item(s, "youtrack")["health"], "")  # „none” z API → pusty

    def test_failed_and_exit_codes(self):
        s = self.snapshot()
        bot = self.item(s, "translations-slack-bot")
        self.assertEqual((bot["state"], bot["exitCode"], bot["failed"]), ("exited", 137, True))
        self.assertEqual(bot["reason"], "zabity (kod 137), możliwy brak pamięci")
        self.assertEqual(bot["stateText"], bot["reason"])
        self.assertEqual(bot["finishedTs"], 1793090000 - 22 * 3600)
        old = self.item(s, "app4-release-t-postgres-1")
        self.assertEqual((old["failed"], old["reason"], old["stateText"]), (False, "", "zakończony"))
        termed = self.item(s, "app4-release-i-web-1")
        self.assertEqual((termed["exitCode"], termed["failed"]), (143, False))  # SIGTERM z docker stop
        self.assertEqual(self.group(s, "compose:translationsdownloader")["failed"], 1)

    def test_failure_rules(self):
        self.assertEqual(snap.failure("exited", 1, False), (True, "padł (kod 1)"))
        self.assertEqual(snap.failure("exited", 137, True)[1], "zabity przez brak pamięci (OOM, kod 137)")
        self.assertEqual(snap.failure("exited", 0, False), (False, ""))
        self.assertEqual(snap.failure("exited", 143, False), (False, ""))
        self.assertTrue(snap.failure("dead", 0, False)[0])
        self.assertEqual(snap.failure("running", 1, False), (False, ""))
        failed, reason = snap.failure("restarting", 1, False)
        self.assertTrue(failed)
        self.assertTrue(reason.startswith("restartuje się po błędzie"))

    def test_created_container_that_failed_to_start(self):
        broken = Container("broken-api", "cookapp-api", state="created", exit_code=128,
                           error="driver failed programming external connectivity: port is already allocated")
        fresh = Container("fresh", "nginx", state="created")
        self.fake.add(broken, fresh)
        s = self.snapshot()
        item = self.item(s, "broken-api")
        self.assertTrue(item["failed"])
        self.assertEqual(item["reason"], "nie wystartował: driver failed programming external connectivity: "
                                         "port is already allocated")
        self.assertEqual(item["stateText"], item["reason"])
        self.assertEqual((self.item(s, "fresh")["failed"], self.item(s, "fresh")["stateText"]),
                         (False, "utworzony, nie uruchomiony"))
        self.assertEqual(snap.failure("created", 128, False), (True, "nie wystartował (kod 128)"))

    def test_exit_137_after_stop_from_tide_is_neutral(self):
        self.snapshot()
        self.assertTrue(self.engine.action("cookapp-docs", "stop")["ok"])
        self.c["docs"].exit_code = 137  # nie zamknął się w 10 s — Docker go zabił
        docs = self.item(self.snapshot(), "cookapp-docs")
        self.assertFalse(docs["failed"])
        self.assertIn("zatrzymany z Tide", docs["reason"])
        self.assertEqual(docs["stateText"], docs["reason"])
        self.assertTrue(self.item(self.snapshot(), "translations-slack-bot")["failed"])  # 137 bez Tide — awaria
        self.c["docs"].state = "running"  # działa, ale z tym samym StartedAt — to wciąż zatrzymywanie
        self.snapshot()
        self.assertIn(self.c["docs"].id, self.engine.stopped_by_tide)
        self.c["docs"].started = time.time() + 1  # wystartował po zatrzymaniu
        self.engine.forget()
        self.snapshot()
        self.assertNotIn(self.c["docs"].id, self.engine.stopped_by_tide)  # znowu działa — zapomniany
        self.c["docs"].state, self.c["docs"].exit_code = "exited", 137
        self.assertTrue(self.item(self.snapshot(), "cookapp-docs")["failed"])
        self.c["docs"].oom = True
        self.engine.mark_stopped(self.c["docs"].id)
        self.engine.forget()
        self.assertTrue(self.item(self.snapshot(), "cookapp-docs")["failed"])  # OOM zawsze jest awarią

    def test_refresh_during_stop_keeps_the_marker(self):
        # Regresja: zdarzenie „kill” (SIGTERM) przychodzi od razu, odświeżenie widzi kontener jeszcze
        # działający — znacznik nie może wtedy zniknąć, bo po 10 s przyjdzie 137.
        self.snapshot()
        self.fake.stop_delay, self.fake.stop_exit = 0.6, 137
        result = {}
        stopper = threading.Thread(target=lambda: result.update(self.engine.action("cookapp-docs", "stop")))
        stopper.start()
        self.assertTrue(wait_for(lambda: self.c["docs"].id in self.engine.stopped_by_tide))
        mid = self.item(self.snapshot(), "cookapp-docs")
        self.assertEqual(mid["state"], "running")
        self.assertIn(self.c["docs"].id, self.engine.stopped_by_tide)
        stopper.join(5)
        self.assertTrue(result["ok"], result)
        docs = self.item(self.snapshot(), "cookapp-docs")
        self.assertEqual((docs["exitCode"], docs["failed"]), (137, False))
        self.assertIn("zatrzymany z Tide", docs["reason"])
        self.assertTrue(self.engine.action("cookapp-docs", "start")["ok"])  # start po znaczniku — znika
        self.snapshot()
        self.assertNotIn(self.c["docs"].id, self.engine.stopped_by_tide)

    def test_markers_survive_concurrent_add_and_refresh(self):
        details = {f"id{i}": {"State": {"StartedAt": "2020-01-01T00:00:00Z"}} for i in range(2000)}
        errors, stop = [], threading.Event()

        def adder():
            i = 0
            while not stop.is_set():
                self.engine.mark_stopped(f"id{i % 2000}")
                self.engine.mark_stopped(f"gone{i}")  # nieznany kontener — odświeżenie go zdejmie
                i += 1

        thread = threading.Thread(target=adder)
        thread.start()
        try:
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                try:
                    self.engine._settle_stopped(details)
                except RuntimeError as exc:  # „dictionary changed size during iteration”
                    errors.append(exc)
                    break
        finally:
            stop.set()
            thread.join(5)
        self.assertEqual(errors, [])
        left = self.engine._settle_stopped(details)
        self.assertTrue(left and all(i.startswith("id") for i in left))

    def test_testcontainers(self):
        s = self.snapshot()
        tc = self.group(s, "testcontainers")
        self.assertEqual(tc["kind"], "testcontainers")
        self.assertEqual(sorted(c["name"] for c in tc["containers"]), ["cool_diffie", "testcontainers-ryuk-c6715127"])
        self.assertTrue(all(c["testcontainers"] for c in tc["containers"]))
        self.assertFalse(self.item(s, "cookapp-api")["testcontainers"])

    def test_summary(self):
        s = self.snapshot()
        # Działające bez testcontainers: 3 cookapp + web + youtrack + legal + biblioteka = 7.
        self.assertEqual(s["summary"], {"total": 12, "running": 7, "stopped": 3, "failed": 1, "projects": 4,
                                        "projectsTotal": 7, "testcontainers": 2, "testcontainersRunning": 2})

    def test_badge(self):
        """Plakietka widoku: padnięte w różu, inaczej liczba działających, zero i brak Dockera — ukryta."""
        s = dict(self.snapshot(), available=True)
        self.assertEqual(snap.badge(s), {"text": "● 1", "tone": "negative"})
        healthy = dict(s, summary=dict(s["summary"], failed=0))
        self.assertEqual(snap.badge(healthy), {"text": "7", "tone": "muted"})
        idle = dict(s, summary=dict(s["summary"], failed=0, running=0))
        self.assertEqual(snap.badge(idle), {"text": "", "tone": "muted"})
        self.assertEqual(snap.badge({"available": False, "summary": s["summary"]}), {"text": "", "tone": "muted"})
        self.assertEqual(snap.badge({}), {"text": "", "tone": "muted"})

    def test_ports(self):
        s = self.snapshot()
        api = self.item(s, "cookapp-api")
        # IPv4 i IPv6 tego samego portu to jeden wpis.
        self.assertEqual(api["ports"], [{"hostIp": "0.0.0.0", "hostPort": 8080, "containerPort": 8080, "proto": "tcp",
                                         "likelyHttp": True, "url": "http://localhost:8080"}])
        pg = self.item(s, "cookapp-postgres")["ports"]
        self.assertEqual([(p["hostPort"], p["containerPort"], p["likelyHttp"], p["url"]) for p in pg],
                         [(5434, 5432, False, "")])
        self.assertEqual(self.item(s, "cookapp-docs")["ports"][0]["url"], "http://localhost:8000")
        self.assertEqual(self.item(s, "app4-release-localhost-web-1")["ports"][0]["hostIp"], "127.0.0.1")
        self.assertFalse(self.item(s, "legal-agents-library-work")["ports"][0]["likelyHttp"])
        self.assertFalse(self.item(s, "testcontainers-ryuk-c6715127")["ports"][0]["likelyHttp"])  # obraz ryuk
        self.assertEqual(self.item(s, "translations-slack-bot")["ports"], [])

    def test_likely_http_heuristic(self):
        self.assertTrue(snap.likely_http(3000, "tcp", "node:22"))
        self.assertFalse(snap.likely_http(6379, "tcp", "my-app"))           # znany port Redis
        self.assertFalse(snap.likely_http(8080, "tcp", "docker.io/library/redis:7"))  # obraz bazy
        self.assertFalse(snap.likely_http(9999, "tcp", "mongo"))
        self.assertTrue(snap.likely_http(15672, "tcp", "rabbitmq:3-management"))    # panel WWW
        self.assertFalse(snap.likely_http(5672, "tcp", "rabbitmq:3-management"))
        self.assertFalse(snap.likely_http(8080, "udp", "nginx"))
        self.assertTrue(snap.likely_http(8111, "tcp", "jetbrains/youtrack:2026.2"))
        # Panele WWW baz i kolejek: nazwa zaczyna się jak baza, ale to strona.
        for image in ("mongo-express:1.0", "provectuslabs/kafka-ui:latest", "rediscommander/redis-commander",
                      "redis/redisinsight:2.60", "dpage/pgadmin4", "adminer", "phpmyadmin/phpmyadmin",
                      "prometheuscommunity/postgres-exporter"):
            self.assertTrue(snap.likely_http(8081, "tcp", image), image)
        self.assertFalse(snap.likely_http(8080, "tcp", "bitnami/postgresql:16"))
        self.assertFalse(snap.likely_http(8080, "tcp", "confluentinc/cp-kafka"))
        ports = snap.ports({"Ports": [{"IP": "0.0.0.0", "PrivatePort": 443, "PublicPort": 8443, "Type": "tcp"},
                                      {"PrivatePort": 9000, "Type": "tcp"}]}, "caddy")
        self.assertEqual([(p["hostPort"], p["url"]) for p in ports], [(8443, "https://localhost:8443")])

    def test_time_parsing(self):
        self.assertEqual(snap.parse_time("2026-10-25T07:35:57.595215036Z"), snap.parse_time("2026-10-25T07:35:57.595215Z"))
        self.assertEqual(snap.parse_time("0001-01-01T00:00:00Z"), 0.0)
        self.assertEqual(snap.parse_time("2026-10-27T13:15:59.92+01:00"), snap.parse_time("2026-10-27T12:15:59.92Z"))
        self.assertEqual(snap.parse_time("bzdura"), 0.0)

    def test_inspect_cached_until_state_changes(self):
        self.snapshot()

        def inspects():
            return len([r for r in self.fake.requests if r[1].endswith("/json") and r[1] != "/containers/json"])
        before = inspects()
        self.assertEqual(before, len(self.fake.containers))
        first = json.dumps(self.snapshot())
        self.assertEqual(inspects(), before)  # nic się nie zmieniło — bez ponownych inspect
        # Tekst Status zmienia się sam z czasem — ani inspect, ani inna migawka.
        self.c["api"].status_text = lambda: "Up 3 hours (healthy)"
        self.assertEqual(json.dumps(self.snapshot()), first)
        self.assertEqual(inspects(), before)
        self.c["bot"].state, self.c["bot"].exit_code = "running", 0
        self.snapshot()
        self.assertEqual(inspects(), before + 1)
        self.engine.refresh(full=True)  # bez strumienia zdarzeń: wszystko od nowa
        self.assertEqual(inspects(), before + 1 + len(self.fake.containers))


class ReposTest(unittest.TestCase):
    def test_worktree_outside_workspace_maps_to_main_repo(self):
        tmp = tempfile.mkdtemp(prefix="tide-docker-repos-")
        self.addCleanup(shutil.rmtree, tmp, True)
        ws = make_workspace(tmp)
        codex = os.path.join(tmp, "codex", "worktrees", "hotfix", "LogViewer")
        os.makedirs(os.path.join(codex, "tools"))
        with open(os.path.join(codex, ".git"), "w") as fh:
            fh.write(f"gitdir: {ws['app4']}/.git/worktrees/hotfix\n")
        found = resolve(os.path.join(codex, "tools"), ws["work"])
        self.assertEqual(found["name"], "LogViewer")
        self.assertEqual(found["worktree"], os.path.realpath(codex))
        self.assertIsNone(resolve(ws["outside"], ws["work"]))
        self.assertIsNone(resolve("", ws["work"]))


class LogsTest(unittest.TestCase):
    def test_demux_marks_stderr_and_joins_split_lines(self):
        data = frame(1, "pierwsza\ndru") + frame(2, "błąd\n") + frame(1, "ga\nbez końca")
        self.assertEqual(log_lines(data), [{"text": "pierwsza", "stderr": False}, {"text": "błąd", "stderr": True},
                                           {"text": "druga", "stderr": False}, {"text": "bez końca", "stderr": False}])

    def test_tty_raw_text(self):
        raw = "linia 1\r\nlinia 2\n".encode()
        self.assertEqual(log_lines(raw, tty=True), [{"text": "linia 1", "stderr": False},
                                                     {"text": "linia 2", "stderr": False}])
        self.assertEqual(log_lines(raw), log_lines(raw, tty=True))  # bez ramek — rozpoznane jako surowy tekst

    def test_multibyte_character_split_between_frames(self):
        text = "zażółć gęślą jaźń\n".encode()
        cut = text.index("ó".encode()) + 1  # w środku dwubajtowego „ó”
        data = frame_bytes(1, text[:cut]) + frame_bytes(2, "e\n".encode()) + frame_bytes(1, text[cut:])
        self.assertEqual(log_lines(data), [{"text": "e", "stderr": True},
                                           {"text": "zażółć gęślą jaźń", "stderr": False}])

    def test_truncated_last_frame_keeps_what_arrived(self):
        data = frame(1, "cała\n") + frame(2, "ucięta w poło")[:-4]
        self.assertEqual(log_lines(data), [{"text": "cała", "stderr": False}, {"text": "ucięta w p", "stderr": True}])

    def test_corrupt_header_mid_stream(self):
        data = frame(1, "dobra\n") + b"\x07garbage\nbez ramki\n"
        self.assertEqual([l["text"] for l in log_lines(data)], ["dobra", "\x07garbage", "bez ramki"])
        self.assertFalse(any(l["stderr"] for l in log_lines(data)))

    def test_limit_keeps_last_lines(self):
        data = b"".join(frame(1, f"{i}\n") for i in range(10))
        self.assertEqual([l["text"] for l in log_lines(data, limit=3)], ["7", "8", "9"])


def frame_bytes(stream: int, data: bytes) -> bytes:
    import struct
    return bytes([stream, 0, 0, 0]) + struct.pack(">I", len(data)) + data


class StatsTest(unittest.TestCase):
    def test_cpu_and_memory_like_docker_cli(self):
        stats = Container("x", "y").stats_json()
        entry = stats_mod.entry({"id": "x", "name": "x"}, stats)
        # (2M − 1M) / (400M − 300M) × 8 rdzeni × 100 = 8%
        self.assertEqual(entry["cpuPercent"], 8.0)
        self.assertEqual(entry["memBytes"], 200 * 2**20)  # zużycie − inactive_file
        self.assertEqual(entry["memLimitBytes"], 16 * 2**30)
        self.assertEqual(stats_mod.cpu_percent({}), 0.0)
        total = stats_mod.total([entry, dict(entry, cpuPercent=4.0)])
        self.assertEqual((total["cpuPercent"], total["hostCpuPercent"], total["memBytes"]), (12.0, 1.5, 400 * 2**20))


class DiskTest(World):
    def test_categories(self):
        result = self.engine.disk()
        self.assertTrue(result["ok"])
        cats = {c["id"]: c for c in result["categories"]}
        self.assertEqual([c["id"] for c in result["categories"]], ["cache", "images", "volumes", "stopped"])
        # Cache: tylko nieużywane i niewspółdzielone wpisy liczą się do odzyskania.
        self.assertEqual((cats["cache"]["count"], cats["cache"]["total"], cats["cache"]["reclaimableBytes"]),
                         (2, 3, 347_279_469))
        # Obrazy: bez żadnego kontenera (także zatrzymanego); Size − SharedSize, ujemne SharedSize = 0.
        self.assertEqual((cats["images"]["count"], cats["images"]["reclaimableBytes"]), (2, 600_000_000 + 400_000_000))
        self.assertEqual(cats["images"]["note"], "2 obrazy bez żadnego kontenera")
        # Wolumeny: RefCount 0; rozmiar -1 (nieznany) liczy się jako 0; ostrzeżenie o danych baz.
        self.assertEqual((cats["volumes"]["count"], cats["volumes"]["reclaimableBytes"], cats["volumes"]["warn"]),
                         (2, 400_000_000, True))
        self.assertIn("dane baz", cats["volumes"]["note"])
        self.assertEqual((cats["stopped"]["count"], cats["stopped"]["reclaimableBytes"]), (2, 86_000))
        self.assertEqual((cats["stopped"]["warn"], cats["stopped"]["anonymousVolumes"]), (False, 0))
        self.assertEqual(result["totalReclaimableBytes"], 347_279_469 + 1_000_000_000 + 400_000_000 + 86_000)
        self.assertEqual(self.fake.mutating(), [])

    def test_stopped_with_anonymous_volume_warns(self):
        anon = "f" * 64
        self.fake.df["Containers"][1]["Mounts"] = [{"Type": "volume", "Name": anon, "Destination": "/var/lib/data"}]
        self.fake.df["Containers"][2]["Mounts"] = [{"Type": "bind", "Source": "/srv", "Destination": "/srv"},
                                                   {"Type": "volume", "Name": "named_data"}]
        cats = {c["id"]: c for c in self.engine.disk()["categories"]}
        self.assertEqual((cats["stopped"]["warn"], cats["stopped"]["anonymousVolumes"]), (True, 1))
        self.assertIn("1 z anonimowym wolumenem", cats["stopped"]["note"])
        # Wolumen z etykietą Dockera „anonymous” też, nawet z inną nazwą.
        self.fake.df["Volumes"].append({"Name": "x1", "Labels": {"com.docker.volume.anonymous": ""},
                                        "UsageData": {"RefCount": 1, "Size": 1}})
        self.fake.df["Containers"][2]["Mounts"] = [{"Type": "volume", "Name": "x1"}]
        cats = {c["id"]: c for c in self.engine.disk()["categories"]}
        self.assertEqual(cats["stopped"]["anonymousVolumes"], 2)

    def test_new_api_usage_blocks(self):
        df = {"ImageUsage": {"Items": [{"Containers": 0, "Size": 10, "SharedSize": 0}]},
              "VolumeUsage": {"Items": []}, "ContainerUsage": {"Items": []}, "BuildCacheUsage": {"Items": []}}
        cats = {c["id"]: c for c in disk_mod.categories(df)["categories"]}
        self.assertEqual(cats["images"]["reclaimableBytes"], 10)

    def test_selection_parsing(self):
        self.assertEqual(disk_mod.parse_selection('["stopped", "cache", "images"]'), (["images", "cache", "stopped"], ""))
        self.assertEqual(disk_mod.parse_selection("[]"), ([], "nic nie zaznaczono"))
        # Wyłącznie lista: obiekt (także z true) to odmowa całej prośby.
        for text in ('{"volumes": true}', '{"volumes": "false"}', '{"volumes": 1}', "null", "true", '"images"'):
            self.assertEqual(disk_mod.parse_selection(text), ([], "niepoprawny opis kategorii — nic nie usunięto"), text)
        self.assertIn("nieznana kategoria: system", disk_mod.parse_selection('["images", "system"]')[1])
        self.assertIn("niepoprawny", disk_mod.parse_selection("nie json")[1])
        self.assertIn("niepoprawny", disk_mod.parse_selection("[1]")[1])

    def test_prune_only_listed_categories_in_safe_order(self):
        result = self.engine.prune('["stopped", "images"]')
        self.assertTrue(result["ok"], result)
        calls = self.fake.mutating()
        self.assertEqual([(m, p) for m, p, _q in calls], [("POST", "/images/prune"), ("POST", "/containers/prune")])
        self.assertEqual(json.loads(calls[0][2]["filters"]), {"dangling": ["false"]})
        self.assertEqual(result["reclaimedBytes"], 1_000_000_000 + 86_000)
        self.assertEqual([(d["id"], d["removed"]) for d in result["details"]], [("images", 1), ("stopped", 1)])

    def test_prune_volumes_and_cache_queries(self):
        self.engine.prune('["volumes", "cache"]')
        calls = self.fake.mutating()
        self.assertEqual([p for _m, p, _q in calls], ["/volumes/prune", "/build/prune"])
        self.assertEqual(json.loads(calls[0][2]["filters"]), {"all": ["true"]})
        self.assertEqual(calls[1][2], {"all": "true"})

    def test_prune_refuses_bad_selection_without_calls(self):
        # Regresja: {"volumes": "false"} wysyłało POST /volumes/prune all=true.
        for text in ("[]", '["everything"]', "{", '"images"', '{"volumes": "false"}', '{"volumes": true}',
                     '["volumes", null]', '[["volumes"]]'):
            result = self.engine.prune(text)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reclaimedBytes"], 0)
        self.assertEqual(self.fake.mutating(), [])

    def test_prune_missing_endpoint_message(self):
        self.fake.fail["/build/prune"] = (404, "page not found")
        result = self.engine.prune('["cache"]')
        self.assertEqual(result["details"][0]["error"], "ten Docker nie zna tej operacji porządków (za stary silnik?)")
        self.assertNotIn("kontenera", result["error"])

    def test_prune_partial_failure(self):
        self.fake.fail["/build/prune"] = (500, "builder busy")
        result = self.engine.prune('["cache", "images"]')
        self.assertFalse(result["ok"])
        self.assertIn("Pamięć podręczna budowania", result["error"])
        self.assertIn("builder busy", result["error"])
        self.assertEqual(result["reclaimedBytes"], 1_000_000_000)


class ActionsTest(World):
    def setUp(self):
        super().setUp()
        self.snapshot()
        self.fake.requests.clear()

    def test_start_stop_restart(self):
        result = self.engine.action(self.c["bot"].id, "start")
        self.assertEqual((result["ok"], result["name"]), (True, "translations-slack-bot"))
        self.assertEqual(self.fake.mutating(), [("POST", f"/containers/{self.c['bot'].id}/start", {})])
        self.fake.requests.clear()
        self.engine.action("cookapp-api", "stop")  # po nazwie też
        self.assertEqual(self.fake.mutating(), [("POST", f"/containers/{self.c['api'].id}/stop", {"t": "10"})])
        self.fake.requests.clear()
        self.engine.action(self.c["pg"].id[:12], "restart")  # początek ID od 12 znaków
        self.assertEqual(self.fake.mutating(), [("POST", f"/containers/{self.c['pg'].id}/restart", {"t": "10"})])

    def test_already_running_is_ok_with_note(self):
        result = self.engine.action("cookapp-api", "start")
        self.assertEqual((result["ok"], result["note"]), (True, "już działał"))

    def test_unknown_container_or_action_makes_no_calls(self):
        self.assertEqual(self.engine.action("nie-ma", "stop")["error"], "nie ma takiego kontenera")
        self.assertEqual(self.engine.action(self.c["api"].id[:6], "stop")["ok"], False)  # za krótki początek ID
        self.assertIn("nieznana akcja", self.engine.action("cookapp-api", "kill")["error"])
        self.assertEqual(self.fake.mutating(), [])

    def test_docker_error_in_polish(self):
        self.fake.fail[f"/containers/{self.c['api'].id}/stop"] = (500, "driver failed")
        result = self.engine.action("cookapp-api", "stop")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Docker nie pozwolił zatrzymać: driver failed")

    def test_remove_only_stopped(self):
        refused = self.engine.remove("cookapp-api")
        self.assertEqual((refused["ok"], refused["error"]), (False, "kontener działa — najpierw go zatrzymaj"))
        self.assertEqual(self.fake.mutating(), [])
        done = self.engine.remove("translations-slack-bot")
        self.assertTrue(done["ok"], done)
        self.assertEqual(self.fake.mutating(), [("DELETE", f"/containers/{self.c['bot'].id}", {})])  # bez force i v

    def test_group_start_in_dependency_order(self):
        for key in ("api", "pg", "docs"):
            self.c[key].state = "exited"
        self.snapshot()
        self.fake.requests.clear()
        result = self.engine.group_action("cookapp", "start")
        self.assertTrue(result["ok"], result)
        self.assertEqual([p for _m, p, _q in self.fake.mutating()],
                         [f"/containers/{self.c[k].id}/start" for k in ("pg", "api", "docs")])
        listed = self.fake.calls("GET", "/containers/json")[0][2]
        self.assertEqual(json.loads(listed["filters"]), {"label": ["com.docker.compose.project=cookapp"]})
        self.assertEqual(result["done"], ["cookapp-postgres", "cookapp-api", "cookapp-docs"])

    def test_group_stop_reverse_and_restart(self):
        self.engine.group_action("cookapp", "stop")
        self.assertEqual([p for _m, p, _q in self.fake.mutating()],
                         [f"/containers/{self.c[k].id}/stop" for k in ("docs", "api", "pg")])
        for key in ("api", "pg", "docs"):
            self.c[key].state = "running"
        self.fake.requests.clear()
        self.engine.group_action("cookapp", "restart")
        self.assertEqual([p.rsplit("/", 1)[1] + ":" + p.split("/")[2][:4] for _m, p, _q in self.fake.mutating()],
                         [f"stop:{self.c['docs'].id[:4]}", f"stop:{self.c['api'].id[:4]}", f"stop:{self.c['pg'].id[:4]}",
                          f"start:{self.c['pg'].id[:4]}", f"start:{self.c['api'].id[:4]}",
                          f"start:{self.c['docs'].id[:4]}"])

    def test_group_restart_partial_failure(self):
        self.fake.fail[f"/containers/{self.c['api'].id}/stop"] = (500, "cannot kill")
        result = self.engine.group_action("cookapp", "restart")
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], [{"name": "cookapp-api", "action": "stop",
                                             "error": "Docker nie pozwolił zatrzymać: cannot kill"}])
        self.assertEqual(result["done"], ["cookapp-docs", "cookapp-postgres"])
        self.assertIn("cookapp-api: Docker nie pozwolił zatrzymać", result["error"])
        steps = [p.split("/")[2][:6] + ":" + p.rsplit("/", 1)[1] for _m, p, _q in self.fake.mutating()]
        ident = {k: self.c[k].id[:6] for k in ("api", "pg", "docs")}
        # api nie zatrzymał się — nie dostaje ponownego startu; reszta zatrzymana i uruchomiona.
        self.assertEqual(steps, [f"{ident['docs']}:stop", f"{ident['api']}:stop", f"{ident['pg']}:stop",
                                 f"{ident['pg']}:start", f"{ident['docs']}:start"])

    def test_expired_deadline_refuses_without_calls(self):
        past = time.monotonic() - 0.1
        from tide_daemon.docker.engine import BUSY
        for result in (self.engine.action("cookapp-docs", "stop", past), self.engine.remove("translations-slack-bot", past),
                       self.engine.logs("cookapp-api", 5, past)):
            self.assertEqual((result["ok"], result["error"]), (False, BUSY))
        self.assertEqual(self.fake.requests, [])
        self.assertNotIn(self.c["docs"].id, self.engine.stopped_by_tide)

    def test_single_action_timeouts_fit_dbus_limit(self):
        from tide_daemon.docker import engine as engine_mod
        # Odpowiedź D-Bus pojedynczej akcji musi zdążyć przed domyślnym limitem klienta (25 s).
        self.assertLess(engine_mod.REPLY_BUDGET, 25)
        self.assertLess(engine_mod.ACTION_TIMEOUT, 25)
        self.assertLess(engine_mod.INSPECT_TIMEOUT + engine_mod.REMOVE_TIMEOUT, 25)
        self.assertLess(engine_mod.LOGS_TIMEOUT, 25)
        self.assertLess(engine_mod.DISK_TIMEOUT, 25)
        self.assertLess(engine_mod.STATS_TIMEOUT, 25)
        self.assertGreater(engine_mod.ACTION_TIMEOUT, engine_mod.STOP_TIMEOUT_S)

    def test_group_errors(self):
        self.assertEqual(self.engine.group_action("nie-ma", "start")["error"], "nie ma takiego projektu compose")
        self.assertIn("nieznana akcja", self.engine.group_action("cookapp", "rm")["error"])
        result = self.engine.group_action("youtrack", "start")
        self.assertEqual((result["ok"], result["note"]), (True, "wszystko już działa"))
        self.assertEqual(self.fake.mutating(), [])

    def test_logs(self):
        result = self.engine.logs("cookapp-api", 3)
        self.assertTrue(result["ok"])
        self.assertEqual(result["lines"], [{"text": "12:01:44 info: Application started", "stderr": False},
                                           {"text": "12:04:02 warn: wolne zapytanie (612 ms)", "stderr": True},
                                           {"text": "12:05:00 info: GET /api/cookapp → 200", "stderr": False}])
        method, path, query = self.fake.requests[-1]
        self.assertEqual((method, path), ("GET", f"/containers/{self.c['api'].id}/logs"))
        self.assertEqual(query, {"stdout": "1", "stderr": "1", "tail": "3"})
        self.engine.logs("cookapp-api", 0)
        self.assertEqual(self.fake.requests[-1][2]["tail"], "200")
        self.engine.logs("cookapp-api", 10**9)
        self.assertEqual(self.fake.requests[-1][2]["tail"], "5000")

    def test_stats_running_only_in_parallel(self):
        self.fake.stats_delay = 0.3
        start = time.monotonic()
        result = self.engine.stats()
        elapsed = time.monotonic() - start
        self.assertTrue(result["ok"])
        running = {c.id for c in self.fake.containers if c.state == "running"}
        self.assertEqual(set(result["containers"]), running)
        self.assertLess(elapsed, 0.3 * len(running) / 2)  # równolegle, nie po kolei
        stats_calls = self.fake.calls("GET")
        self.assertTrue(all(q == {"stream": "false"} for _m, p, q in stats_calls if p.endswith("/stats")))
        self.assertEqual(result["total"]["containers"], len(running))
        self.assertEqual(result["containers"][self.c["api"].id]["name"], "cookapp-api")

    def test_stats_overall_timeout(self):
        self.fake.stats_delay = 2.0
        result = self.engine.stats(timeout=0.3)
        self.assertFalse(result["ok"])
        self.assertEqual(result["containers"], {})
        self.assertTrue(result["missing"])


class EventsTest(World):
    def test_events_stream_filters_quiet_actions(self):
        seen, stop = [], threading.Event()
        connected = threading.Event()
        thread = threading.Thread(target=lambda: self.engine.events(stop, lambda i, a: seen.append((i, a)),
                                                                     connected.set), daemon=True)
        thread.start()
        self.assertTrue(connected.wait(3))
        _m, path, query = self.fake.calls("GET", "/events")[0]
        self.assertEqual(json.loads(query["filters"]), {"type": ["container", "volume"]})
        bot = self.c["bot"]
        self.fake.emit(bot, "exec_start: sh -c healthcheck")
        self.fake.emit(bot, "health_status: healthy")
        self.fake.emit(bot, "start")
        self.fake.emit_volume("recipes_pgdata", "mount")    # razem ze startem kontenera — cisza
        self.fake.emit_volume("old_data", "destroy")
        deadline = time.time() + 3
        while len(seen) < 3 and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(seen, [(bot.id, "health_status"), (bot.id, "start"), ("old_data", "volume:destroy")])
        self.fake.drop_events()
        thread.join(3)
        self.assertFalse(thread.is_alive())  # koniec strumienia kończy jedno połączenie

    def test_stop_flag_ends_quiet_stream(self):
        stop = threading.Event()
        connected = threading.Event()
        thread = threading.Thread(target=lambda: self.engine.events(stop, lambda i, a: None, connected.set),
                                  daemon=True)
        thread.start()
        self.assertTrue(connected.wait(3))
        stop.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
