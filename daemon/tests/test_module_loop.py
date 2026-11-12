"""PT-363, PT-417: tools/module_loop.py — pętla przebiegów modułu (wstrzyknięte loadavg, palniki, polecenie, dzienniki)."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import module_loop  # noqa: E402

MODULE = "daemon/tests/test_cardflow_waves.py"
COMPANION = "daemon/tests/test_cardflow_youtrack.py"
CORES = 16
HIGH, LOW, QUIET = (14.0, 15.0, 10.0), (3.0, 8.0, 9.0), (1.0, 2.0, 3.0)
OTHER = {"card": "K-7", "step": "K-7-PT-9-2", "command": "make test-changed BASE=abc", "worktree": "/inny"}


class FakeBurners:
    def __init__(self, count, *args, **kwargs):
        self.count, self.running, self.stopped = count, False, False
        self.args, self.kwargs = args, kwargs
        FakeBurners.last = self

    def start(self):
        self.running = True

    def alive(self):
        return self.running

    def stop(self):
        self.stopped, self.running = True, False


class Env:
    """Scenariusz: odczyt przed palnikami (`before`), potem loadavg z listy (ostatnia wartość się powtarza),
    wyniki uruchamiacza z listy na przebieg, inne `outside` i obce uruchamiacze z list (na wywołanie)."""

    def __init__(self, loads, results, kill_burner_on_run=None, before=QUIET, outside=(), runners=()):
        self.loads, self.results, self.kill_on = [before, *loads], list(results), kill_burner_on_run
        self.outside, self.runners = list(outside), list(runners)
        self.commands, self.envs = [], []
        self.clock_value = 0.0
        FakeBurners.last = None

    def loadavg(self):
        return self.loads.pop(0) if len(self.loads) > 1 else self.loads[0]

    def clock(self):
        self.clock_value += 1.0
        return self.clock_value

    def scan(self, items):
        return lambda: (items.pop(0) if len(items) > 1 else items[0]) if items else []

    def run_command(self, command, log_path, env=None):
        self.commands.append(command)
        self.envs.append(env)
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
        return {"loadavg": self.loadavg, "burners": FakeBurners, "bounded": FakeBurners,
                "run_command": self.run_command, "sleep": lambda _: None, "clock": self.clock, "jobs": lambda: 4,
                "outside": self.scan(self.outside), "foreign_runners": self.scan(self.runners),
                "runnable": lambda: None}


class ModuleLoopTest(unittest.TestCase):
    def loop(self, env, runs=3, mode="burners", **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            kwargs.setdefault("wait_timeout", 0)
            kwargs.setdefault("sample_interval", 3600)  # próbki tylko w teście próbkowania: odczyty deterministyczne
            kwargs.setdefault("cores", CORES)
            if mode != "slow":
                kwargs.setdefault("threshold", 8)
            if mode != "slow":
                kwargs.setdefault("burner_count", 2)
            summary, code = module_loop.run_loop(MODULE, runs=runs, mode=mode, output=tmp, deps=env.deps(),
                                                 argv=["module_loop.py"], **kwargs)
            files = {name: (Path(tmp) / name).read_text(encoding="utf-8") for name in ("summary.json", "report.md")}
        return summary, code, files

    # --- tryby i kody wyjścia -------------------------------------------------------------------

    def test_bounded_proof_all_green_returns_zero_and_calls_public_runner_only(self):
        env = Env([HIGH], [{}])
        summary, code, files = self.loop(env, mode="bounded", target=16)
        self.assertEqual(code, 0)
        self.assertEqual((summary["valid_runs"], summary["green_runs"]), (3, 3))
        self.assertEqual(len(env.commands), 3)
        for command in env.commands:
            self.assertTrue(command[1].endswith("tools/test_runner.py"))
            self.assertEqual(command[2], "--modules")
            self.assertEqual(command[3], MODULE)
            self.assertNotIn("--jobs", command)
        self.assertEqual(env.envs, [None, None, None])
        self.assertTrue(FakeBurners.last.stopped)
        self.assertEqual(FakeBurners.last.args, (16.0,))     # cel loadavg przekazany regulatorowi
        self.assertIn("Tryb: dowód: palniki-ograniczone", files["report.md"])
        self.assertIn("cel loadavg1: 16", files["report.md"])
        self.assertIn("DOWÓD (kod 0)", files["report.md"])
        self.assertEqual(json.loads(files["summary.json"])["mode_kind"], "dowód")

    def test_default_mode_is_bounded_with_target_equal_to_cores(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary, code = module_loop.run_loop(MODULE, runs=1, output=tmp, wait_timeout=0, cores=6,
                                                 deps=Env([(5.0, 5.0, 5.0)], [{}]).deps(), argv=["x"])
        self.assertEqual((summary["mode"], summary["target"], summary["burners"]), ("bounded", 6.0, 6))
        self.assertAlmostEqual(summary["threshold"], 4.8)
        self.assertEqual(code, 0)

    def test_reproduction_modes_never_return_zero_and_say_so(self):
        for mode, label in (("burners", "palniki-1,5x"), ("pool", "pula")):
            with self.subTest(mode=mode):
                summary, code, files = self.loop(Env([HIGH], [{}]), mode=mode, companions=[COMPANION])
                self.assertEqual((summary["valid_runs"], summary["green_runs"]), (3, 3))
                self.assertEqual(code, module_loop.EXIT_REPRODUCTION)
                self.assertFalse(summary["ok"])
                self.assertIn(f"Tryb: odtworzenie: {label}", files["report.md"])
                self.assertIn("NIE jest dowód", files["report.md"])

    def test_slow_mode_passes_seed_per_run_without_burners(self):
        env = Env([QUIET], [{}])
        summary, code, files = self.loop(env, mode="slow", slow_seed="417")
        self.assertEqual(code, 0)
        self.assertEqual(env.envs, [{"TIDE_TEST_SLOW": f"domyslny:417:{n}"} for n in (1, 2, 3)])
        self.assertEqual([run["seed"] for run in summary["runs"]], [f"domyslny:417:{n}" for n in (1, 2, 3)])
        self.assertIsNone(FakeBurners.last, "powolny silnik nie uruchamia palników")
        self.assertEqual((summary["burners"], summary["threshold"]), (0, None))
        self.assertIn("Tryb: dowód: powolny-silnik", files["report.md"])
        self.assertIn("| domyslny:417:2 |", files["report.md"])

    def test_slow_mode_without_seed_records_generated_seed(self):
        summary, _, _ = self.loop(Env([QUIET], [{}]), runs=1, mode="slow")
        self.assertTrue(summary["slow_seed"])
        self.assertEqual(summary["runs"][0]["seed"], f"domyslny:{summary['slow_seed']}:1")

    def test_one_failed_run_is_nonzero_and_names_the_test(self):
        env = Env([HIGH], [{}, {MODULE: ["test_waves.WavesTest.test_restore"]}, {}])
        summary, code, files = self.loop(env, mode="bounded", target=16)
        self.assertEqual(code, 1)
        self.assertEqual(summary["green_runs"], 2)
        self.assertIn("test_waves.WavesTest.test_restore", files["report.md"])

    # --- ważność przebiegu ----------------------------------------------------------------------

    def test_start_below_threshold_makes_later_run_invalid(self):
        # Pierwszy przebieg: wysoko; drugi: oczekiwanie kończy się czasem, przebieg rusza, ale jest nieważny.
        env = Env([HIGH, HIGH, LOW], [{}])
        summary, code, files = self.loop(env, runs=2, mode="bounded", target=16)
        self.assertEqual(code, 1)
        self.assertEqual([run["valid"] for run in summary["runs"]], [True, False])
        self.assertIn("poniżej progu", files["report.md"])

    def test_start_load_above_one_and_half_cores_invalidates_run(self):
        env = Env([(25.0, 20.0, 10.0)], [{}])
        summary, code, files = self.loop(env, runs=1, mode="bounded", target=16)
        self.assertEqual(code, 1)
        run = summary["runs"][0]
        self.assertFalse(run["valid"])
        self.assertIn("> 1.5 × rdzenie (24)", run["reasons"][0])
        self.assertEqual(run["loadavg_start"], [25.0, 20.0, 10.0])
        self.assertIn("NIEWAŻNY", files["report.md"])

    def test_other_outside_during_run_invalidates_it_with_card_step_and_command(self):
        env = Env([HIGH], [{}], outside=[[], [OTHER]])     # przed serią: nic; w trakcie przebiegu: inne outside
        summary, code, files = self.loop(env, runs=1, mode="bounded", target=16)
        self.assertEqual(code, 1)
        self.assertTrue(summary["quiet"])
        reason = summary["runs"][0]["reasons"][0]
        self.assertIn("karta K-7, krok K-7-PT-9-2, polecenie `make test-changed BASE=abc`", reason)
        self.assertEqual(summary["runs"][0]["outside"], [OTHER])
        self.assertIn("inne polecenie poza piaskownicą", files["report.md"])

    def test_foreign_test_runner_invalidates_run(self):
        env = Env([HIGH], [{}], runners=[[{"pid": 4242, "command": "python3 tools/test_runner.py"}]])
        summary, code, _ = self.loop(env, runs=1, mode="slow")
        self.assertEqual(code, 1)
        self.assertIn("obcy tools/test_runner.py", summary["runs"][0]["reasons"][0])
        self.assertIn("pid 4242", summary["runs"][0]["reasons"][0])

    def test_burner_death_invalidates_run(self):
        env = Env([HIGH], [{}], kill_burner_on_run=1)
        summary, code, _ = self.loop(env, runs=1)
        self.assertNotEqual(code, 0)
        self.assertFalse(summary["runs"][0]["valid"])
        self.assertIn("palnik", summary["runs"][0]["reasons"][0])

    def test_load_drop_is_warning_but_run_stays_valid(self):
        env = Env([HIGH, LOW], [{}])
        summary, code, files = self.loop(env, runs=1, mode="bounded", target=16)
        self.assertEqual(code, 0)
        run = summary["runs"][0]
        self.assertTrue(run["valid"])
        self.assertEqual(run["loadavg1_min"], 3.0)
        self.assertIn("spadek obciążenia", run["warnings"][0])
        self.assertIn("spadek obciążenia", files["report.md"])

    def test_load_above_target_is_reported_with_maximum(self):
        env = Env([HIGH, (17.5, 15.0, 10.0)], [{}])
        summary, _, files = self.loop(env, runs=1, mode="bounded", target=16)
        self.assertEqual(summary["runs"][0]["loadavg1_max"], 17.5)
        self.assertIn("ponad cel", files["report.md"])
        self.assertIn("14.00–17.50", files["report.md"])

    def test_report_has_load_start_and_end_of_every_run(self):
        env = Env([HIGH, (12.0, 11.0, 10.0)], [{}])
        summary, _, files = self.loop(env, runs=1, mode="bounded", target=16)
        self.assertEqual(summary["runs"][0]["loadavg_end"], [12.0, 11.0, 10.0])
        self.assertIn("| 14.00/15.00/10.00 | 12.00/11.00/10.00 |", files["report.md"])

    # --- okno ciszy -----------------------------------------------------------------------------

    def test_high_load_before_burners_is_outside_quiet_window_without_runs(self):
        env = Env([HIGH], [{}], before=(8.0, 4.0, 2.0))
        summary, code, files = self.loop(env, mode="bounded", target=16)
        self.assertEqual(code, module_loop.EXIT_NOT_QUIET)
        self.assertEqual((env.commands, summary["runs"], summary["valid_runs"]), ([], [], 0))
        self.assertIsNone(FakeBurners.last, "palniki nie ruszają poza oknem ciszy")
        self.assertIn("8.00 ≥ 8", summary["quiet_reasons"][0])
        self.assertIn("POZA OKNEM CISZY", files["report.md"])

    def test_quiet_threshold_is_a_parameter(self):
        env = Env([HIGH], [{}], before=(5.0, 4.0, 2.0))
        _, code, _ = self.loop(env, runs=1, mode="bounded", target=16, quiet_max=4)
        self.assertEqual(code, module_loop.EXIT_NOT_QUIET)

    def test_running_other_outside_before_series_is_outside_quiet_window(self):
        env = Env([HIGH], [{}], outside=[[OTHER]])
        summary, code, files = self.loop(env, mode="slow")
        self.assertEqual(code, module_loop.EXIT_NOT_QUIET)
        self.assertEqual(env.commands, [])
        self.assertIn("karta K-7", summary["quiet_reasons"][0])
        self.assertIn("make test-changed BASE=abc", files["report.md"])

    def test_outside_scanner_reads_open_outside_and_skips_own_step_and_worktree(self):
        with tempfile.TemporaryDirectory() as board:
            def journal(card, *events):
                path = Path(board, "flow", card)
                path.mkdir(parents=True)
                (path / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events) + "zły wiersz\n",
                                                    encoding="utf-8")

            journal("K-1", {"type": "outside.started", "step": "a", "command": "make test", "worktree": "/w/a"},
                    {"type": "outside.finished", "step": "a"})
            journal("K-2", {"type": "outside.started", "step": "b", "command": "make test-changed", "worktree": "/w/b"})
            journal("K-3", {"type": "outside.started", "step": "own", "command": "loop", "worktree": "/w/x"})
            journal("K-4", {"type": "outside.started", "step": "c", "command": "loop2", "worktree": board})
            before = {p: p.stat().st_mtime_ns for p in Path(board).rglob("*")}
            scanner = module_loop.OutsideScanner(board, own_steps=["own"], own_worktrees=[board])
            self.assertEqual(scanner(), [{"card": "K-2", "step": "b", "command": "make test-changed",
                                          "worktree": "/w/b"}])
            self.assertEqual(scanner(), scanner())
            self.assertEqual(before, {p: p.stat().st_mtime_ns for p in Path(board).rglob("*")}, "tylko odczyt")
        self.assertEqual(module_loop.OutsideScanner("/nie/ma/tablicy")(), [])

    def test_foreign_runners_skip_own_process_tree_and_workers(self):
        with tempfile.TemporaryDirectory() as proc:
            def process(pid, ppid, *argv):
                Path(proc, str(pid)).mkdir()
                Path(proc, str(pid), "stat").write_text(f"{pid} (python3) S {ppid} 1 1", encoding="utf-8")
                Path(proc, str(pid), "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

            process(100, 1, "python3", "tools/module_loop.py")            # narzędzie
            process(101, 100, "python3", "/r/tools/test_runner.py", "--modules", "x")
            process(102, 101, "python3", "/r/tools/test_runner.py", "--worker", "x")
            process(200, 1, "make", "test")
            process(201, 200, "python3", "/inne/tools/test_runner.py", "--changed")
            process(202, 201, "python3", "/inne/tools/test_runner.py", "--worker", "y")
            Path(proc, "self").mkdir()
            found = module_loop.foreign_runners(own_pid=100, proc_root=proc)
        self.assertEqual(found, [{"pid": 201, "command": "python3 /inne/tools/test_runner.py --changed"}])

    # --- palniki ograniczone ----------------------------------------------------------------------

    def test_regulate_shrinks_above_target_and_pauses_all_when_load_stays_high(self):
        active = 8
        for _ in range(10):
            new = module_loop.regulate(9.0, active + 1, active, target=8, maximum=8)
            self.assertLess(new, max(active, 1))
            active = new
            if active == 0:
                break
        self.assertEqual(active, 0, "powyżej celu palniki są wstrzymywane aż do zera")
        self.assertEqual(module_loop.regulate(9.0, 1, 0, target=8, maximum=8), 0)

    def test_regulate_counts_test_processes_and_never_aims_above_target(self):
        # Spokojna maszyna: palniki do 0,9 × cel.
        self.assertEqual(module_loop.regulate(0.5, 1, 0, target=10, maximum=10), 9)
        # Test zajmuje 6 procesów gotowych: palników tylko 3, łącznie 9 ≤ cel.
        self.assertEqual(module_loop.regulate(0.5, 1 + 9 + 6, 9, target=10, maximum=10), 3)
        # W paśmie 0,8–1,0 × cel nie dokładamy palników, ale ustępujemy testowi.
        self.assertEqual(module_loop.regulate(8.5, 1 + 5, 5, target=10, maximum=10), 5)
        self.assertEqual(module_loop.regulate(8.5, 1 + 5 + 7, 5, target=10, maximum=10), 2)
        # Test sam przekracza cel: wszystkie palniki wstrzymane.
        self.assertEqual(module_loop.regulate(9.5, 1 + 4 + 12, 4, target=10, maximum=10), 0)
        # Brak odczytu procesów gotowych: tylko loadavg.
        self.assertEqual(module_loop.regulate(10.5, None, 4, target=10, maximum=10), 3)

    def test_bounded_burners_pause_above_target_and_resume_below_with_injected_load(self):
        load = [(12.0, 1.0, 1.0)]
        burners = module_loop.BoundedBurners(2, 8, loadavg=lambda: load[0], runnable=lambda: None)
        try:
            burners.start(regulate_in_thread=False)
            for _ in range(3):
                burners.step()
            self.assertEqual(burners.active, 0)
            self.assertTrue(all(self.in_state(pid, "T") for pid in burners.pids()), "wstrzymane SIGSTOP")
            self.assertTrue(burners.alive())
            load[0] = (2.0, 1.0, 1.0)
            self.assertEqual(burners.step(), 2)
            self.assertTrue(all(self.in_state(pid, "RS") for pid in burners.pids()), "wznowione SIGCONT")
            self.assertEqual(burners.take_range(), [0, 2])
        finally:
            pids = burners.pids()
            burners.stop()
        self.assertFalse(any(self.exists(pid) for pid in pids))

    def test_bounded_burners_regulator_thread_follows_load(self):
        load = [(20.0, 1.0, 1.0)]
        burners = module_loop.BoundedBurners(2, 4, loadavg=lambda: load[0], runnable=lambda: None, interval=0.01)
        try:
            burners.start()
            deadline = time.monotonic() + 5
            while burners.active and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(burners.active, 0)
        finally:
            pids = burners.pids()
            burners.stop()
        self.assertFalse(any(self.exists(pid) for pid in pids))

    # --- dotychczasowe zachowanie -----------------------------------------------------------------

    def test_monitor_samples_load_during_a_long_run(self):
        env = Env([HIGH], [{}])
        original = env.run_command

        def slow(command, log_path, env=None):
            time.sleep(0.15)
            return original(command, log_path, env)

        env.run_command = slow
        summary, code, _ = self.loop(env, runs=1, mode="bounded", target=16, sample_interval=0.02)
        self.assertEqual(code, 0)
        self.assertGreater(summary["runs"][0]["samples"], 3)

    def test_companion_failure_is_reported_separately(self):
        env = Env([HIGH], [{COMPANION: ["test_youtrack.Case.test_offline"]}])
        summary, code, files = self.loop(env, runs=2, mode="pool", companions=[COMPANION])
        self.assertEqual(code, module_loop.EXIT_REPRODUCTION)
        self.assertEqual(summary["companion_failures"], 2)
        self.assertIn("nieudany towarzysz: " + COMPANION + ": test_youtrack.Case.test_offline", files["report.md"])
        self.assertEqual(summary["runs"][0]["failed_tested"], [])

    def test_pool_mode_passes_module_and_companions_in_one_command_without_duplicate(self):
        env = Env([HIGH], [{}])
        summary, _, files = self.loop(env, runs=2, mode="pool", companions=[COMPANION, MODULE, COMPANION])
        self.assertEqual(summary["parallel_modules"], 2)
        self.assertIn("Moduły równoległe w wywołaniu: 2 przy JOBS=", files["report.md"])
        self.assertEqual(len(env.commands), 2)  # jedno wywołanie na przebieg, nie osobne wywołania
        command = env.commands[0]
        modules = command[command.index("--modules") + 1:command.index("--output")]
        self.assertEqual(modules, [MODULE, COMPANION])
        self.assertEqual(summary["companions"], [COMPANION])
        self.assertNotIn("--jobs", command)

    def test_threshold_not_reached_before_first_run_is_error_without_runs(self):
        env = Env([LOW], [{}])
        summary, code, _ = self.loop(env, mode="bounded", target=16)
        self.assertEqual(code, 2)
        self.assertEqual(env.commands, [])
        self.assertEqual(summary["runs"], [])
        self.assertIn("nieosiągnięty", summary["error"])
        self.assertTrue(FakeBurners.last.stopped)

    def test_serial_flag_follows_serial_modules(self):
        serial = "daemon/tests/test_cardflow_review.py"
        with tempfile.TemporaryDirectory() as tmp:
            summary, _ = module_loop.run_loop(serial, runs=1, burner_count=1, threshold=8, output=tmp, cores=CORES,
                                              deps=Env([HIGH], [{}]).deps(), argv=["x"])
        self.assertTrue(summary["module_serial"])

    def test_interrupt_writes_partial_report_and_stops_burners(self):
        env = Env([HIGH], [{}])
        deps = env.deps()
        base_run = deps["run_command"]

        def interrupting(command, log_path, env=None):
            if len(env_commands) == 1:
                raise KeyboardInterrupt
            return base_run(command, log_path, env)

        env_commands = env.commands
        deps["run_command"] = interrupting
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyboardInterrupt):
                module_loop.run_loop(MODULE, runs=3, burner_count=1, threshold=8, output=tmp, wait_timeout=0,
                                     sample_interval=3600, deps=deps, argv=["x"], cores=CORES)
            summary = json.loads((Path(tmp) / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(len(summary["runs"]), 1)
            self.assertIn("przerwano", summary["error"])
            self.assertTrue((Path(tmp) / "report.md").is_file())
        self.assertTrue(FakeBurners.last.stopped)

    def test_default_companions_are_valid_and_fill_the_pool(self):
        self.assertEqual(len(set(module_loop.DEFAULT_COMPANIONS)), len(module_loop.DEFAULT_COMPANIONS))
        for module in module_loop.DEFAULT_COMPANIONS:
            self.assertTrue((ROOT / module).is_file(), module)
        parallel = [m for m in module_loop.DEFAULT_COMPANIONS if m not in module_loop.SERIAL_MODULES]
        self.assertGreaterEqual(len(parallel) + 1, module_loop.default_jobs())
        self.assertGreaterEqual(len(module_loop.DEFAULT_COMPANIONS), 13)
        for module in ("test_cardflow_youtrack", "test_worktrees_shutdown", "test_daemon_lifecycle",
                       "test_cardflow_waves", "test_cardflow_integration_k38", "test_cardflow_review",
                       "test_cardflow_dbus"):
            self.assertIn("daemon/tests/%s.py" % module, module_loop.DEFAULT_COMPANIONS)

    def test_reproduction_burners_exceed_threshold_by_half(self):
        cores = module_loop.available_cores()
        with tempfile.TemporaryDirectory() as tmp:
            summary, _ = module_loop.run_loop(MODULE, runs=1, mode="burners", output=tmp, wait_timeout=0,
                                              deps=Env([HIGH], [{}]).deps(), argv=["x"])
        self.assertEqual(summary["threshold"], cores)
        self.assertEqual(summary["burners"], -(-3 * cores // 2))

    def test_cli_rejects_mixed_modes_and_slow_with_burners(self):
        for argv in ([MODULE, "--slow", "1", "--pool"], [MODULE, "--reproduce", "--pool"],
                     [MODULE, "--slow", "--burners", "2"], [MODULE, "--pool", "--target", "4"],
                     [MODULE, "--slow", "--slow-profile", "nieznany"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as raised:
                with open(os.devnull, "w") as null:
                    stderr, sys.stderr = sys.stderr, null
                    try:
                        module_loop.main(argv)
                    finally:
                        sys.stderr = stderr
            self.assertEqual(raised.exception.code, 2)

    def test_real_burner_disappears_after_normal_end_and_after_exception(self):
        before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        burners = module_loop.Burners(1)
        burners.start()
        pids = burners.pids()
        self.assertTrue(burners.alive())
        burners.stop()
        self.assertFalse(self.exists(pids[0]))
        for sig, handler in before.items():
            self.assertEqual(signal.getsignal(sig), handler)

        holder = {}

        class Recording(module_loop.Burners):
            def start(self):
                super().start()
                holder["pids"] = self.pids()

        def boom(command, log_path, env=None):
            raise RuntimeError("awaria przebiegu")

        env = Env([HIGH], [{}])
        deps = {**env.deps(), "burners": Recording, "run_command": boom}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                module_loop.run_loop(MODULE, runs=1, mode="burners", burner_count=1, threshold=1, output=tmp,
                                     deps=deps, argv=["x"], cores=CORES)
        self.assertFalse(self.exists(holder["pids"][0]))

    @staticmethod
    def in_state(pid, states):
        """Czy proces dojdzie do jednego ze stanów /proc (sygnał działa asynchronicznie)."""
        deadline = time.monotonic() + 2
        while True:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                if fh.read().rpartition(")")[2].split()[0] in states:
                    return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)

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
