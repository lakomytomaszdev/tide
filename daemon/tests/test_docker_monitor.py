"""DockerMonitor w pętli Qt (bez D-Bus): zdarzenia → Changed, ponowne połączenie strumienia,
odpytywanie bez Dockera, pamięć Stats/Disk i karty Konsoli z logami i powłoką (atrapa terminala).
"""
import json
import os
import shutil
import tempfile
import unittest

from .fake_docker import FakeDocker, make_workspace, populate
from .privatebus import app, wait_until

try:
    from tide_daemon.docker_monitor import SHELL_COMMAND, DockerMonitor
    from tide_daemon.docker.client import EngineClient
    from tide_daemon.docker.engine import DockerEngine
    from tide_daemon.tasks.core import TaskFiles
except ImportError:  # pragma: no cover — bez PyQt6
    DockerMonitor = None


class FakeTerminal:
    def __init__(self, mode="tab"):
        self.mode = mode
        self.plans = []

    def open(self, plan):
        with open(plan.task_file, encoding="utf-8") as fh:
            self.plans.append((plan, json.load(fh)))
        return self.mode


@unittest.skipIf(DockerMonitor is None, "brak PyQt6")
class MonitorTest(unittest.TestCase):
    def setUp(self):
        app()
        self.tmp = tempfile.mkdtemp(prefix="tide-docker-mon-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = make_workspace(self.tmp)
        self.sock = os.path.join(self.tmp, "docker.sock")
        self.fake = FakeDocker(self.sock)
        self.c = populate(self.fake, self.ws)
        self.terminal = FakeTerminal()
        self.files = TaskFiles(os.path.join(self.tmp, "tasks"))

    def monitor(self, **kwargs):
        engine = DockerEngine(EngineClient(self.sock, timeout=2), workspace=self.ws["work"])
        kwargs.setdefault("poll_ms", 150)
        mon = DockerMonitor(engine, terminal=self.terminal, files=self.files, debounce_ms=50,
                            backoff=(0.1, 0.3), **kwargs)
        self.changes = []
        mon.changed.connect(lambda: self.changes.append(mon.snapshot_json()))
        mon.start()
        self.addCleanup(mon.stop)
        return mon

    def snapshot(self, mon):
        return json.loads(mon.snapshot_json())

    def state_of(self, mon, name):
        for g in self.snapshot(mon)["groups"]:
            for c in g["containers"]:
                if c["name"] == name:
                    return c["state"]
        return None

    def call(self, method, *args):
        box = []
        method(*args, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=5), "brak odpowiedzi")
        return json.loads(box[0])

    def test_events_trigger_changed(self):
        self.fake.start()
        mon = self.monitor(poll_ms=60_000)  # bez odpytywania — zmiana musi przyjść zdarzeniem
        self.assertTrue(wait_until(lambda: self.snapshot(mon).get("live"), timeout=5))
        self.assertEqual(self.state_of(mon, "translations-slack-bot"), "exited")
        before = len(self.changes)
        self.c["bot"].state, self.c["bot"].exit_code = "running", 0
        self.fake.emit(self.c["bot"], "start")
        self.assertTrue(wait_until(lambda: self.state_of(mon, "translations-slack-bot") == "running", timeout=5))
        self.assertGreater(len(self.changes), before)

    def test_quiet_events_do_not_publish(self):
        self.fake.start()
        mon = self.monitor(poll_ms=60_000)
        self.assertTrue(wait_until(lambda: self.snapshot(mon).get("live"), timeout=5))
        wait_until(lambda: False, timeout=0.3)
        lists = len(self.fake.calls("GET", "/containers/json"))
        for _ in range(5):
            self.fake.emit(self.c["api"], "exec_die")
        wait_until(lambda: False, timeout=0.4)
        self.assertEqual(len(self.fake.calls("GET", "/containers/json")), lists)

    def test_burst_of_events_is_debounced(self):
        self.fake.start()
        mon = self.monitor(poll_ms=60_000)
        self.assertTrue(wait_until(lambda: self.snapshot(mon).get("live"), timeout=5))
        wait_until(lambda: False, timeout=0.3)
        lists = len(self.fake.calls("GET", "/containers/json"))
        for action in ("kill", "die", "stop", "destroy", "create", "start"):
            self.fake.emit(self.c["api"], action)
        wait_until(lambda: False, timeout=0.6)
        self.assertLessEqual(len(self.fake.calls("GET", "/containers/json")) - lists, 2)

    def test_stream_reconnects_after_drop(self):
        self.fake.start()
        mon = self.monitor(poll_ms=60_000)
        self.assertTrue(wait_until(lambda: self.fake.listeners() == 1, timeout=5))
        self.fake.drop_events()
        self.assertTrue(wait_until(lambda: self.fake.event_streams >= 2 and self.fake.listeners() == 1, timeout=5))
        self.assertTrue(wait_until(lambda: self.snapshot(mon).get("live"), timeout=5))
        self.c["old"].state = "running"
        self.fake.emit(self.c["old"], "start")
        self.assertTrue(wait_until(lambda: self.state_of(mon, "app4-release-t-postgres-1") == "running", timeout=5))

    def test_polls_until_docker_appears(self):
        mon = self.monitor()
        self.assertTrue(wait_until(lambda: self.snapshot(mon)["state"] == "unavailable", timeout=5))
        snapshot = self.snapshot(mon)
        self.assertFalse(snapshot["available"])
        self.assertIn("Docker nie działa", snapshot["error"])
        self.assertEqual(snapshot["groups"], [])
        self.fake.start()
        self.assertTrue(wait_until(lambda: self.snapshot(mon)["available"], timeout=5))
        self.assertTrue(wait_until(lambda: self.snapshot(mon)["live"], timeout=5))
        self.assertEqual(self.snapshot(mon)["summary"]["failed"], 1)

    def test_docker_going_away(self):
        self.fake.start()
        mon = self.monitor()
        self.assertTrue(wait_until(lambda: self.snapshot(mon).get("live"), timeout=5))
        self.fake.stop()
        self.assertTrue(wait_until(lambda: self.snapshot(mon)["state"] == "unavailable", timeout=5))
        self.assertFalse(self.snapshot(mon)["live"])

    def test_stats_cached_and_coalesced(self):
        self.fake.start()
        mon = self.monitor(stats_ttl=60)
        self.assertTrue(wait_until(lambda: self.snapshot(mon)["available"], timeout=5))
        boxes = [], []
        mon.stats(boxes[0].append)
        mon.stats(boxes[1].append)  # w trakcie — dołącza do tego samego odczytu
        self.assertTrue(wait_until(lambda: boxes[0] and boxes[1], timeout=5))
        self.assertEqual(boxes[0], boxes[1])
        running = sum(1 for c in self.fake.containers if c.state == "running")
        stats_calls = lambda: len([r for r in self.fake.requests if r[1].endswith("/stats")])  # noqa: E731
        self.assertEqual(stats_calls(), running)
        result = self.call(mon.stats)
        self.assertEqual(stats_calls(), running)  # z pamięci
        self.assertTrue(result["ok"])
        self.assertIn(self.c["api"].id, result["containers"])

    def test_disk_cached_and_dropped_after_prune(self):
        self.fake.start()
        mon = self.monitor()
        self.assertTrue(wait_until(lambda: self.snapshot(mon)["available"], timeout=5))
        self.assertTrue(self.call(mon.disk)["ok"])
        self.call(mon.disk)
        self.assertEqual(len(self.fake.calls("GET", "/system/df")), 1)
        result = self.call(mon.prune, '["images"]')
        self.assertEqual(result["reclaimedBytes"], 1_000_000_000)
        self.call(mon.disk)
        self.assertEqual(len(self.fake.calls("GET", "/system/df")), 2)

    def test_actions_reply_json(self):
        self.fake.start()
        mon = self.monitor()
        self.assertTrue(wait_until(lambda: self.snapshot(mon)["available"], timeout=5))
        result = self.call(mon.action, "translations-slack-bot", "start")
        self.assertEqual((result["ok"], result["error"]), (True, ""))
        self.assertTrue(wait_until(lambda: self.state_of(mon, "translations-slack-bot") == "running", timeout=5))
        self.assertEqual(self.call(mon.remove, "cookapp-api")["ok"], False)
        group = self.call(mon.group_action, "cookapp", "stop")
        self.assertEqual(group["done"], ["cookapp-docs", "cookapp-api", "cookapp-postgres"])
        logs = self.call(mon.logs, "cookapp-api", 2)
        self.assertEqual(len(logs["lines"]), 2)

    def test_open_logs_and_shell(self):
        self.fake.start()
        mon = self.monitor()
        self.assertTrue(wait_until(lambda: self.snapshot(mon)["available"], timeout=5))
        self.assertTrue(mon.open_logs("translations-slack-bot"))  # logi także zatrzymanego
        plan, task = self.terminal.plans[-1]
        self.assertEqual(plan.title, "docker · translations-slack-bot")
        docker = task["argv"][0]
        self.assertEqual(task["argv"][1:], ["-H", f"unix://{self.sock}", "logs", "-f", "--tail", "200",
                                            "translations-slack-bot"])
        self.assertTrue(docker.endswith("docker"))
        self.assertTrue(mon.open_shell(self.c["api"].id))
        _plan, task = self.terminal.plans[-1]
        self.assertEqual(task["argv"][3:], ["exec", "-it", "cookapp-api", "sh", "-c", SHELL_COMMAND])
        self.assertFalse(mon.open_shell("translations-slack-bot"))  # zatrzymany — nie ma do czego wejść
        self.assertFalse(mon.open_logs("nie-ma"))
        self.assertEqual(len(self.terminal.plans), 2)

    def test_failed_terminal_cancels_task_file(self):
        self.fake.start()
        self.terminal.mode = ""
        mon = self.monitor()
        self.assertTrue(wait_until(lambda: self.snapshot(mon)["available"], timeout=5))
        self.assertFalse(mon.open_logs("cookapp-api"))
        plan, _task = self.terminal.plans[-1]
        self.assertFalse(os.path.exists(plan.task_file))
        self.assertTrue(os.path.exists(plan.task_file + ".cancelled"))
        with open(plan.task_file + ".cancelled", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")  # treść polecenia znika od razu


if __name__ == "__main__":
    unittest.main()
