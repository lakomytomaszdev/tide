"""PT-363: tools/module_loop.py — pętla przebiegów modułu pod obciążeniem (wstrzyknięte loadavg, palniki, polecenie)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import module_loop  # noqa: E402

MODULE = "daemon/tests/test_cardflow_waves.py"
COMPANION = "daemon/tests/test_cardflow_youtrack.py"
HIGH, LOW = (20.0, 15.0, 10.0), (3.0, 8.0, 9.0)


class FakeBurners:
    def __init__(self, count):
        self.count, self.running, self.stopped = count, False, False
        FakeBurners.last = self

    def start(self):
        self.running = True

    def alive(self):
        return self.running

    def stop(self):
        self.stopped, self.running = True, False


class Env:
    """Scenariusz: loadavg z listy (ostatnia wartość się powtarza), wyniki uruchamiacza z listy na przebieg."""

    def __init__(self, loads, results, kill_burner_on_run=None):
        self.loads, self.results, self.kill_on = list(loads), list(results), kill_burner_on_run
        self.commands = []
        self.clock_value = 0.0

    def loadavg(self):
        return self.loads.pop(0) if len(self.loads) > 1 else self.loads[0]

    def clock(self):
        self.clock_value += 1.0
        return self.clock_value

    def run_command(self, command, log_path):
        self.commands.append(command)
        index = len(self.commands)
        result = self.results[min(index, len(self.results)) - 1]
        out = Path(command[command.index("--output") + 1]) / "run-abc"
        out.mkdir(parents=True)
        modules = command[command.index("--modules") + 1:command.index("--output")]
        results = []
        for module in modules:
            failed = result.get(module, [])
            results.append({"module": module, "returncode": 1 if failed else 0, "failures": failed, "errors": [],
                            "unexpected_successes": []})
        (out / "summary.json").write_text(json.dumps({"results": results}), encoding="utf-8")
        if self.kill_on == index:
            FakeBurners.last.running = False
        return int(any(result.values()))

    def deps(self):
        return {"loadavg": self.loadavg, "burners": FakeBurners, "run_command": self.run_command,
                "sleep": lambda _: None, "clock": self.clock, "jobs": lambda: 4}


class ModuleLoopTest(unittest.TestCase):
    def loop(self, env, runs=3, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            kwargs.setdefault("wait_timeout", 0)
            kwargs.setdefault("sample_interval", 3600)  # próbki tylko w teście próbkowania: odczyty deterministyczne
            summary, code = module_loop.run_loop(MODULE, runs=runs, burner_count=2, threshold=8, output=tmp,
                                                 deps=env.deps(), argv=["module_loop.py"], **kwargs)
            files = {name: (Path(tmp) / name).read_text(encoding="utf-8") for name in ("summary.json", "report.md")}
        return summary, code, files

    def test_all_green_returns_zero_and_calls_public_runner_only(self):
        env = Env([HIGH], [{}])
        summary, code, files = self.loop(env)
        self.assertEqual(code, 0)
        self.assertEqual((summary["valid_runs"], summary["green_runs"]), (3, 3))
        self.assertEqual(len(env.commands), 3)
        for command in env.commands:
            self.assertTrue(command[1].endswith("tools/test_runner.py"))
            self.assertEqual(command[2], "--modules")
            self.assertEqual(command[3], MODULE)
            self.assertNotIn("--jobs", command)
        self.assertTrue(FakeBurners.last.stopped)
        self.assertIn(MODULE, files["report.md"])
        self.assertEqual(json.loads(files["summary.json"])["mode"], "burners")

    def test_one_failed_run_is_nonzero_and_names_the_test(self):
        env = Env([HIGH], [{}, {MODULE: ["test_waves.WavesTest.test_restore"]}, {}])
        summary, code, files = self.loop(env)
        self.assertEqual(code, 1)
        self.assertEqual(summary["green_runs"], 2)
        self.assertIn("test_waves.WavesTest.test_restore", files["report.md"])

    def test_start_below_threshold_makes_later_run_invalid(self):
        # Pierwszy przebieg: wysoko; drugi: oczekiwanie kończy się czasem, przebieg rusza, ale jest nieważny.
        env = Env([HIGH, HIGH, LOW], [{}])
        summary, code, files = self.loop(env, runs=2)
        self.assertNotEqual(code, 0)
        self.assertEqual([run["valid"] for run in summary["runs"]], [True, False])
        self.assertIn("poniżej progu", files["report.md"])

    def test_threshold_not_reached_before_first_run_is_error_without_runs(self):
        env = Env([LOW], [{}])
        summary, code, _ = self.loop(env)
        self.assertEqual(code, 2)
        self.assertEqual(env.commands, [])
        self.assertEqual(summary["runs"], [])
        self.assertIn("nieosiągnięty", summary["error"])
        self.assertTrue(FakeBurners.last.stopped)

    def test_burner_death_invalidates_run(self):
        env = Env([HIGH], [{}], kill_burner_on_run=1)
        summary, code, _ = self.loop(env, runs=1)
        self.assertNotEqual(code, 0)
        self.assertFalse(summary["runs"][0]["valid"])
        self.assertIn("palnik", summary["runs"][0]["reasons"][0])

    def test_load_drop_is_warning_but_run_stays_valid(self):
        env = Env([HIGH, LOW], [{}])
        summary, code, files = self.loop(env, runs=1)
        self.assertEqual(code, 0)
        run = summary["runs"][0]
        self.assertTrue(run["valid"])
        self.assertEqual(run["loadavg1_min"], 3.0)
        self.assertIn("spadek obciążenia", run["warnings"][0])
        self.assertIn("spadek obciążenia", files["report.md"])

    def test_monitor_samples_load_during_a_long_run(self):
        env = Env([HIGH], [{}])
        original = env.run_command

        def slow(command, log_path):
            time.sleep(0.15)
            return original(command, log_path)

        env.run_command = slow
        summary, code, _ = self.loop(env, runs=1, sample_interval=0.02)
        self.assertEqual(code, 0)
        self.assertGreater(summary["runs"][0]["samples"], 3)

    def test_companion_failure_keeps_zero_but_is_reported(self):
        env = Env([HIGH], [{COMPANION: ["test_youtrack.Case.test_offline"]}])
        summary, code, files = self.loop(env, runs=2, pool=True, companions=[COMPANION])
        self.assertEqual(code, 0)
        self.assertEqual(summary["companion_failures"], 2)
        self.assertIn("nieudany towarzysz: " + COMPANION + ": test_youtrack.Case.test_offline", files["report.md"])
        self.assertEqual(summary["runs"][0]["failed_tested"], [])

    def test_pool_mode_passes_module_and_companions_in_one_command_without_duplicate(self):
        env = Env([HIGH], [{}])
        summary, code, _ = self.loop(env, runs=2, pool=True, companions=[COMPANION, MODULE, COMPANION])
        self.assertEqual(code, 0)
        self.assertEqual(len(env.commands), 2)  # jedno wywołanie na przebieg, nie osobne wywołania
        command = env.commands[0]
        modules = command[command.index("--modules") + 1:command.index("--output")]
        self.assertEqual(modules, [MODULE, COMPANION])
        self.assertEqual(summary["companions"], [COMPANION])
        self.assertNotIn("--jobs", command)

    def test_serial_flag_follows_serial_modules(self):
        serial = "daemon/tests/test_cardflow_review.py"
        with tempfile.TemporaryDirectory() as tmp:
            summary, _ = module_loop.run_loop(serial, runs=1, burner_count=1, threshold=8, output=tmp,
                                              deps=Env([HIGH], [{}]).deps(), argv=["x"])
        self.assertTrue(summary["module_serial"])

    def test_default_companions_are_valid_and_fill_the_pool(self):
        self.assertEqual(len(set(module_loop.DEFAULT_COMPANIONS)), len(module_loop.DEFAULT_COMPANIONS))
        for module in module_loop.DEFAULT_COMPANIONS:
            self.assertTrue((ROOT / module).is_file(), module)
        parallel = [m for m in module_loop.DEFAULT_COMPANIONS if m not in module_loop.SERIAL_MODULES]
        self.assertGreaterEqual(len(parallel) + 1, 14)
        self.assertGreaterEqual(len(module_loop.DEFAULT_COMPANIONS), 13)

    def test_default_burners_exceed_threshold_by_half(self):
        cores = module_loop.available_cores()
        with tempfile.TemporaryDirectory() as tmp:
            summary, _ = module_loop.run_loop(MODULE, runs=1, output=tmp, wait_timeout=0,
                                              deps=Env([HIGH], [{}]).deps(), argv=["x"])
        self.assertEqual(summary["threshold"], cores)
        self.assertEqual(summary["burners"], -(-3 * cores // 2))

    def test_real_burner_disappears_after_normal_end_and_after_exception(self):
        burners = module_loop.Burners(1)
        burners.start()
        pids = burners.pids()
        self.assertTrue(burners.alive())
        burners.stop()
        self.assertFalse(self.exists(pids[0]))

        holder = {}

        class Recording(module_loop.Burners):
            def start(self):
                super().start()
                holder["pids"] = self.pids()

        def boom(command, log_path):
            raise RuntimeError("awaria przebiegu")

        env = Env([HIGH], [{}])
        deps = {**env.deps(), "burners": Recording, "run_command": boom}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                module_loop.run_loop(MODULE, runs=1, burner_count=1, threshold=1, output=tmp, deps=deps, argv=["x"])
        self.assertFalse(self.exists(holder["pids"][0]))

    @staticmethod
    def exists(pid):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            time.sleep(0.02)
        return True


if __name__ == "__main__":
    unittest.main()
