"""Kontrakt /Docker (org.tide.Daemon.Docker) na prywatnej magistrali z prawdziwą usługą.

Usługa łączy się z atrapą Engine API (fake_docker.py, wątek testu) przez DOCKER_HOST. Programy to
atrapy w PATH:
- konsole — zapisuje wywołanie i uruchamia polecenie z -e (launch.py) w katalogu z --workdir,
- systemd-run — uruchamia to, co po `--`,
- docker — zapisuje argv do pliku (karta z logami i powłoką nigdy nie woła prawdziwego Dockera).
Profilu Konsoli nie ma i nie ma wiedzy o procesach, więc karta to zawsze „nowe okno” atrapy.
"""
import json
import os
import shutil
import tempfile
import unittest

from .fake_docker import FakeDocker, make_workspace, populate
from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from .test_tasks_dbus import FAKE_KONSOLE, FAKE_SYSTEMD_RUN
from .test_tasks_core import write_exec

PATH, IFACE = "/Docker", "org.tide.Daemon.Docker"
# Atrapa okna Tide (TIDE_WINDOW): zapisuje, z czym usługa je otworzyła.
FAKE_TIDE_WINDOW = """#!/usr/bin/python3
import json, os, sys
with open(os.environ["TIDE_FAKE_WINDOW_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
"""
FAKE_DOCKER_CLI = """#!/usr/bin/python3
import json, os, sys
with open(os.environ["TIDE_FAKE_DOCKER_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
"""
COMMON = ("--no-processes", "--no-notifications", "--no-krunner", "--no-idle", "--no-limits", "--no-coordination",
          "--no-tab-marks", "--no-report")


@requires_dbus
class DockerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-docker-dbus-")
        cls.ws = make_workspace(cls.tmp)
        cls.sock = os.path.join(cls.tmp, "docker.sock")
        cls.fake = FakeDocker(cls.sock)
        cls.c = populate(cls.fake, cls.ws)
        cls.fake.start()
        cls.bin = os.path.join(cls.tmp, "bin")
        os.makedirs(cls.bin)
        write_exec(os.path.join(cls.bin, "konsole"), FAKE_KONSOLE)
        write_exec(os.path.join(cls.bin, "systemd-run"), FAKE_SYSTEMD_RUN)
        write_exec(os.path.join(cls.bin, "docker"), FAKE_DOCKER_CLI)
        cls.window = os.path.join(cls.tmp, "tide.py")
        write_exec(cls.window, FAKE_TIDE_WINDOW)
        cls.window_log = os.path.join(cls.tmp, "window.log")
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.calls = os.path.join(cls.tmp, "calls.log")
        cls.docker_log = os.path.join(cls.tmp, "docker-cli.log")
        cls.runtime = os.path.join(cls.tmp, "run")
        empty = os.path.join(cls.tmp, "empty")
        os.makedirs(empty)
        cls.service = PrivateBus("tide-docker-test")
        cls.service.spawn_daemon(
            "--claude-dir", empty, "--codex-dir", empty, "--workspace", cls.ws["work"], *COMMON,
            extra_env={"DOCKER_HOST": f"unix://{cls.sock}", "PATH": cls.bin + os.pathsep + "/usr/bin:/bin",
                       "HOME": cls.home, "XDG_RUNTIME_DIR": cls.runtime, "SHELL": "/bin/true",
                       "XDG_DATA_HOME": os.path.join(cls.tmp, "data"),
                       "XDG_CONFIG_HOME": os.path.join(cls.tmp, "config"),
                       "TIDE_FAKE_LOG": cls.calls, "TIDE_FAKE_DOCKER_LOG": cls.docker_log,
                       "TIDE_WINDOW": cls.window, "TIDE_FAKE_WINDOW_LOG": cls.window_log})
        assert wait_until(lambda: cls.fetch_snapshot()["available"], timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        cls.fake.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def fetch_snapshot(cls):
        reply = cls.service.call(SERVICE, PATH, IFACE, "Snapshot")
        return json.loads(reply.arguments()[0]) if reply.arguments() else {"available": False}

    def snapshot(self):
        return self.fetch_snapshot()

    def call(self, member, *args, timeout=30000):
        from PyQt6.QtDBus import QDBusMessage
        reply = self.service.call(SERVICE, PATH, IFACE, member, *args, timeout=timeout)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return reply.arguments()[0]

    def call_json(self, member, *args):
        return json.loads(self.call(member, *args))

    def job(self, member, *args):
        """Zadanie: od razu {ok, job}; wynik przychodzi sygnałem ActionFinished(job, json)."""
        from PyQt6.QtCore import QObject, pyqtSlot
        finished = []

        class Receiver(QObject):
            @pyqtSlot(str, str)
            def done(self, job, text):
                finished.append((job, json.loads(text)))

        receiver = Receiver()
        self.assertTrue(self.service.bus.connect(SERVICE, PATH, IFACE, "ActionFinished", receiver.done))
        try:
            started = self.call_json(member, *args)
            if not started["ok"]:
                return started, None
            self.assertTrue(wait_until(lambda: any(j == started["job"] for j, _r in finished), timeout=10))
            return started, next(r for j, r in finished if j == started["job"])
        finally:
            self.service.bus.disconnect(SERVICE, PATH, IFACE, "ActionFinished", receiver.done)

    def state_of(self, name):
        for g in self.snapshot()["groups"]:
            for c in g["containers"]:
                if c["name"] == name:
                    return c["state"]
        return None

    def test_1_introspection(self):
        xml = self.service.call(SERVICE, PATH, "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertIn(f'<interface name="{IFACE}">', xml)
        for member in ("Snapshot", "Stats", "Disk", "Start", "Stop", "Restart", "Remove", "GroupAction", "Prune",
                       "Logs", "OpenLogs", "OpenShell", "Badge", "ShowWindow", "DiskNow"):
            self.assertIn(f'<method name="{member}">', xml)
        self.assertIn('<signal name="Changed"', xml)
        self.assertIn('<signal name="ActionFinished">', xml)

    def test_2_snapshot(self):
        snapshot = self.snapshot()
        self.assertEqual((snapshot["available"], snapshot["state"], snapshot["error"]), (True, "ok", ""))
        self.assertEqual(snapshot["host"], f"unix://{self.sock}")
        self.assertTrue(wait_until(lambda: self.snapshot()["live"], timeout=5))
        self.assertEqual(snapshot["summary"]["failed"], 1)
        ids = [g["id"] for g in snapshot["groups"]]
        self.assertIn("compose:cookapp", ids)
        self.assertEqual(ids[-2:], ["standalone", "testcontainers"])
        cookapp = next(g for g in snapshot["groups"] if g["id"] == "compose:cookapp")
        self.assertEqual(cookapp["repo"]["name"], "CookApp")
        # Usługa sama niczego w Dockerze nie zmienia.
        self.assertEqual(self.fake.mutating(), [])

    def test_3_changed_signal_after_event(self):
        from PyQt6.QtCore import QObject, pyqtSlot
        changes = []

        class Receiver(QObject):
            @pyqtSlot()
            def changed(self):
                changes.append(True)

        receiver = Receiver()
        self.assertTrue(self.service.bus.connect(SERVICE, PATH, IFACE, "Changed", receiver.changed))
        self.c["old"].state = "running"
        self.fake.emit(self.c["old"], "start")
        self.assertTrue(wait_until(lambda: changes, timeout=5))
        self.assertEqual(self.state_of("app4-release-t-postgres-1"), "running")
        self.c["old"].state = "exited"
        self.fake.emit(self.c["old"], "die")
        self.assertTrue(wait_until(lambda: self.state_of("app4-release-t-postgres-1") == "exited", timeout=5))

    def test_4_actions(self):
        self.fake.requests.clear()
        result = self.call_json("Stop", "cookapp-docs")
        self.assertEqual((result["ok"], result["name"]), (True, "cookapp-docs"))
        self.assertEqual(self.fake.mutating(), [("POST", f"/containers/{self.c['docs'].id}/stop", {"t": "10"})])
        self.assertTrue(wait_until(lambda: self.state_of("cookapp-docs") == "exited", timeout=5))
        self.assertTrue(self.call_json("Start", self.c["docs"].id)["ok"])
        self.assertTrue(self.call_json("Restart", "cookapp-docs")["ok"])
        self.assertEqual([p.rsplit("/", 1)[1] for _m, p, _q in self.fake.mutating()], ["stop", "start", "restart"])
        refused = self.call_json("Remove", "cookapp-api")
        self.assertEqual((refused["ok"], refused["error"]), (False, "kontener działa — najpierw go zatrzymaj"))
        unknown = self.call_json("Start", "nie-ma-takiego")
        self.assertEqual((unknown["ok"], unknown["error"]), (False, "nie ma takiego kontenera"))
        self.assertEqual(len(self.fake.mutating()), 3)

    def test_5_group_action(self):
        self.fake.requests.clear()
        started, result = self.job("GroupAction", "cookapp", "restart")
        self.assertTrue(started["job"].startswith("group-"))
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["job"], result["project"]), (started["job"], "cookapp"))
        self.assertEqual([p.rsplit("/", 1)[1] for _m, p, _q in self.fake.mutating()],
                         ["stop", "stop", "stop", "start", "start", "start"])
        refused, none = self.job("GroupAction", "cookapp", "down")
        self.assertEqual((refused["ok"], refused["job"], none), (False, "", None))
        self.assertIn("nieznana akcja", refused["error"])
        _started, missing = self.job("GroupAction", "nie-ma", "start")
        self.assertEqual((missing["ok"], missing["error"]), (False, "nie ma takiego projektu compose"))

    def test_6_prune_only_listed(self):
        self.fake.requests.clear()
        started, result = self.job("Prune", json.dumps(["cache"]))
        self.assertTrue(started["ok"])
        self.assertEqual((result["ok"], result["reclaimedBytes"]), (True, 5_900_000_000))
        self.assertEqual([(m, p) for m, p, _q in self.fake.mutating()], [("POST", "/build/prune")])
        # Regresja: {"volumes": "false"} wybierało wolumeny. Teraz każda prośba spoza listy id to odmowa.
        for text in ("[]", '{"volumes": "false"}', '{"volumes": true}'):
            refused, none = self.job("Prune", text)
            self.assertEqual((refused["ok"], refused["job"], none), (False, "", None), text)
        self.assertEqual(len(self.fake.mutating()), 1)

    def test_7_logs_stats_disk(self):
        logs = self.call_json("Logs", "cookapp-api", 2)
        self.assertTrue(logs["ok"])
        self.assertEqual([(l["text"], l["stderr"]) for l in logs["lines"]],
                         [("12:04:02 warn: wolne zapytanie (612 ms)", True), ("12:05:00 info: GET /api/cookapp → 200", False)])
        stats = self.call_json("Stats")
        self.assertTrue(stats["ok"])
        self.assertIn(self.c["api"].id, stats["containers"])
        self.assertEqual(stats["containers"][self.c["api"].id]["cpuPercent"], 8.0)
        self.assertGreater(stats["total"]["memBytes"], 0)
        disk = self.call_json("Disk")
        self.assertTrue(disk["ok"])
        before = len(self.fake.calls("GET", "/system/df"))
        self.assertTrue(self.call_json("DiskNow")["ok"])
        self.assertEqual(len(self.fake.calls("GET", "/system/df")), before + 1, "DiskNow bez pamięci podręcznej")
        self.assertEqual([c["id"] for c in disk["categories"]], ["cache", "images", "volumes", "stopped"])

    def test_8_open_logs_and_shell_in_konsole(self):
        self.assertTrue(self.call("OpenLogs", "translations-slack-bot"))
        self.assertTrue(wait_until(lambda: os.path.exists(self.docker_log), timeout=10))
        self.assertTrue(self.call("OpenShell", "cookapp-api"))
        def logged():
            with open(self.docker_log, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh]
        self.assertTrue(wait_until(lambda: len(logged()) >= 2, timeout=10))
        entries = logged()
        runs = [e["argv"] for e in entries]
        # Karta przejmuje zadanie po własnym katalogu, ale polecenie (i powłoka po nim) idzie w $HOME.
        self.assertEqual([e["cwd"] for e in entries], [self.home, self.home])
        self.assertEqual(runs[0], ["-H", f"unix://{self.sock}", "logs", "-f", "--tail", "200", self.c["bot"].id])
        self.assertEqual(runs[1][:5], ["-H", f"unix://{self.sock}", "exec", "-it", self.c["api"].id])
        self.assertEqual(runs[1][5:7], ["sh", "-c"])
        with open(self.calls, encoding="utf-8") as fh:
            konsole = fh.read()
        self.assertIn("[tabtitle=docker · translations-slack-bot]", konsole)
        self.assertIn("[tabtitle=docker · cookapp-api]", konsole)
        tasks = os.path.join(self.runtime, "tide", "tasks")
        self.assertFalse([n for n in os.listdir(tasks) if n.endswith(".task")])  # oba zadania przejęte
        self.assertFalse(self.call("OpenShell", "translations-slack-bot"))  # zatrzymany
        self.assertFalse(self.call("OpenLogs", "nie-ma"))


    def test_9_badge_and_show_window(self):
        """Badge() → {text, tone} z migawki; ShowWindow(id) otwiera okno Tide na widoku Docker (atrapa)."""
        before = len(self.fake.mutating())
        self.assertEqual(self.call_json("Badge"), {"text": "● 1", "tone": "negative"})
        self.assertTrue(self.call("ShowWindow", "translations-slack-bot"))
        self.assertTrue(self.call("ShowWindow", ""))

        def shown():
            if not os.path.exists(self.window_log):
                return []
            with open(self.window_log, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh]
        self.assertTrue(wait_until(lambda: len(shown()) >= 2, timeout=10))
        self.assertEqual(sorted(shown(), key=len), [["--view", "docker"],
                                                     ["--view", "docker", "--select", self.c["bot"].id]])
        self.assertEqual(len(self.fake.mutating()), before, "plakietka i okno niczego w Dockerze nie zmieniają")

@requires_dbus
class DockerStatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-docker-dbus2-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bus = PrivateBus("tide-docker-states")
        self.addCleanup(self.bus.stop)

    def spawn(self, *args):
        return self.bus.spawn_daemon("--claude-dir", self.tmp, "--codex-dir", self.tmp, "--workspace", self.tmp,
                                     *COMMON, *args)

    def test_unavailable_socket(self):
        self.spawn()  # privatebus podaje nieistniejący socket

        def snapshot():
            reply = self.bus.call(SERVICE, PATH, IFACE, "Snapshot")
            return json.loads(reply.arguments()[0])
        self.assertTrue(wait_until(lambda: snapshot()["state"] == "unavailable", timeout=5))
        self.assertIn("brak socketu", snapshot()["error"])
        reply = self.bus.call(SERVICE, PATH, IFACE, "Stats")
        self.assertFalse(json.loads(reply.arguments()[0])["ok"])

    def test_no_docker(self):
        self.spawn("--no-docker")
        xml = self.bus.call(SERVICE, "/", "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertNotIn('node name="Docker"', xml)
        self.assertIn('node name="Agents"', xml)


if __name__ == "__main__":
    unittest.main()
