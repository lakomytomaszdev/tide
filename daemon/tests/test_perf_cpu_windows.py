"""Testy narzędzia 10 okien CPU całego procesu (K-105, PT-458) i skryptu pomiaru produkcji.

Nie uruchamiają usługi i nie mierzą czasu: `/proc` jest syntetyczny, a zegar i uśpienie — wirtualne."""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from . import perf_cpu_windows as cw

HZ = 100
PID = 4242
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location("measure_idle_prod", os.path.join(ROOT, "scripts", "measure_idle_prod.py"))
prod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prod)


class Sim:
    """Wirtualny zegar i syntetyczny `/proc/<pid>`: `sleep` przesuwa czas i zwiększa czas CPU procesu o `rate(t)` %."""

    def __init__(self, root, rate=lambda t: 10.0, child_rate=0.0, pid=PID):
        self.root, self.pid, self.rate, self.child_rate = root, pid, rate, child_rate
        self.t = 0.0
        self.utime = self.stime = self.cutime = self.cstime = 0.0
        self.start = 5000
        self.threads = {PID: ["tide-daemon", 0.0], PID + 1: ["tide-cardflow_0", 0.0], PID + 2: ["tide-sampler", 0.0]}
        self.events = []
        self.write()

    def clock(self):
        return self.t

    def sleep(self, seconds):
        ticks = int(round(self.rate(self.t) * seconds * HZ / 100.0))           # całkowite ticki: bez błędów zaokrągleń
        self.t += seconds
        self.stime += ticks // 3
        self.utime += ticks - ticks // 3
        self.cutime += int(self.child_rate * seconds * HZ)      # dzieci: duży przyrost, nie wchodzi do miary
        self.cstime += int(self.child_rate * seconds * HZ)
        self.threads[PID][1] += ticks // 5
        self.threads[PID + 1][1] += ticks - ticks // 5
        self.write()

    def write(self):
        directory = os.path.join(self.root, str(self.pid))
        os.makedirs(os.path.join(directory, "task"), exist_ok=True)

        def line(name, u, s, c=0, cs=0):
            return (f"{self.pid} ({name}) S 1 1 1 0 -1 0 0 0 0 0 {int(u)} {int(s)} {int(c)} {int(cs)} 20 0 3 0 "
                    f"{self.start} 0 0\n")
        with open(os.path.join(directory, "stat"), "w", encoding="utf-8") as fh:
            fh.write(line("tide (daemon)", self.utime, self.stime, self.cutime, self.cstime))
        for tid, (name, cpu) in self.threads.items():
            task = os.path.join(directory, "task", str(tid))
            os.makedirs(task, exist_ok=True)
            with open(os.path.join(task, "stat"), "w", encoding="utf-8") as fh:
                fh.write(line(name, cpu, 0))
        with open(os.path.join(directory, "status"), "w", encoding="utf-8") as fh:
            fh.write("Name:\ttide\nVmHWM:\t 307200 kB\nVmRSS:\t 204800 kB\nRssAnon:\t 153600 kB\nThreads:\t3\n")
        with open(os.path.join(self.root, "loadavg"), "w", encoding="utf-8") as fh:
            fh.write("0.20 0.20 0.20 1/100 1\n")


class FakeService:
    """Atrapa usługi: rejestruje każde wywołanie z chwilą zegara."""

    def __init__(self, sim):
        self.sim, self.calls = sim, []

    def diagnostics(self, seconds):
        self.calls.append((self.sim.t, "diagnostics", seconds))
        self.sim.sleep(seconds)
        return {"profileWindow": {"split": {"samples": {"tick": 30}, "share": {"tick": 0.6}}},
                "snapshot": {"samplesMs": [5.0] * 60, "p95Ms": 5.0, "errors": 0}}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-cpuwin-test-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def measure(self, sim, service=None, **kwargs):
        service = service or FakeService(sim)
        params = dict(warmup_s=30, windows=10, window_s=60, proc_root=self.root, clock=sim.clock, sleep=sim.sleep,
                      hz=HZ, cpu_count=8)
        params.update(kwargs)
        return cw.run_idle_measurement(PID, service, **params), service


class WindowMeasureTest(Base):
    def test_ten_windows_from_utime_plus_stime_with_raw_increments(self):
        sim = Sim(self.root, rate=lambda t: 10.0)
        result, _ = self.measure(sim)
        block = result["cpuWindows"]
        self.assertTrue(block["complete"])
        self.assertEqual((block["pid"], len(block["windows"])), (PID, 10))
        first = block["windows"][0]
        self.assertEqual(first["utimeTicks"] + first["stimeTicks"], 600)         # 10 % · 60 s · 100 Hz
        self.assertGreater(first["utimeTicks"], 0)
        self.assertGreater(first["stimeTicks"], 0)
        self.assertAlmostEqual(first["cpuPercent"], 10.0, places=1)
        result["boxEnabled"], result["sha"] = True, "abc123"
        evaluation = cw.evaluate(result)
        self.assertEqual(len(evaluation["windowsPercent"]), 10)
        self.assertAlmostEqual(evaluation["meanPercent"], 10.0, places=1)
        self.assertAlmostEqual(evaluation["maxPercent"], 10.0, places=1)
        self.assertEqual((evaluation["boxEnabled"], evaluation["sha"], evaluation["pid"]), (True, "abc123", PID))
        self.assertEqual(evaluation["verdict"], "pass")
        self.assertTrue(result["loadavgBefore"] and result["loadavgAfter"])

    def test_window_marks_are_read_at_the_edges_of_the_cpu_windows_without_changing_the_cpu(self):
        """K-125 PT-486: licznik (wywołania atrapy `systemctl`) czytany tuż przed pierwszym i po ostatnim oknie CPU."""
        sim = Sim(self.root, rate=lambda t: 10.0)
        plain, _ = self.measure(sim)
        counter = iter(range(100, 1000, 7))
        marked, _ = self.measure(Sim(self.root, rate=lambda t: 10.0), window_marks=lambda: next(counter))
        self.assertEqual(marked["windowMarks"], {"start": 100, "end": 107})
        self.assertNotIn("windowMarks", plain)
        self.assertEqual(marked["cpuWindows"]["windows"], plain["cpuWindows"]["windows"])
        self.assertEqual(marked["cpuWindow"], plain["cpuWindow"])

    def test_children_with_huge_cutime_do_not_change_the_result(self):
        plain, _ = self.measure(Sim(self.root, rate=lambda t: 12.0))
        shutil.rmtree(self.root)
        os.makedirs(self.root)
        with_children, _ = self.measure(Sim(self.root, rate=lambda t: 12.0, child_rate=500.0))
        self.assertEqual(plain["cpuWindows"]["windows"], with_children["cpuWindows"]["windows"])
        self.assertEqual(cw.evaluate(plain)["windowsPercent"], cw.evaluate(with_children)["windowsPercent"])
        self.assertEqual(cw.process_ticks("1 (a b) S 1 1 1 0 -1 0 0 0 0 0 7 3 99999 88888 20 0 1 0 55 0 0\n"),
                         (7, 3, 55))

    def test_process_name_with_spaces_and_parentheses(self):
        name, fields = cw.parse_stat("12 (we ird) name) S 1 1 1 0 -1 0 0 0 0 0 5 6 0 0 20 0 1 0 9 0 0")
        self.assertEqual(name, "we ird) name")
        self.assertEqual(fields[11], "5")

    def test_threads_delta_feeds_flow_thread_selection(self):
        result, _ = self.measure(Sim(self.root, rate=lambda t: 10.0))
        flow = result["flowThread"]
        self.assertEqual((flow["name"], flow["source"]), ("tide-cardflow_0", "name"))
        self.assertAlmostEqual(flow["cpuPercent"], 8.0, delta=0.5)            # 80 % z 10 % procesu

    def test_incremental_callback_after_every_window(self):
        seen = []
        self.measure(Sim(self.root), on_window=lambda block: seen.append(len(block["windows"])))
        self.assertEqual(seen, list(range(1, 11)))


class ThresholdTest(Base):
    def rates(self, over_window=None, base=5.0, over=31.0):
        def rate(t):
            index = int((t - 30) // 60)
            return over if index == over_window else base
        return rate

    def test_one_window_over_limit_with_mean_below_fails(self):
        result, _ = self.measure(Sim(self.root, rate=self.rates(over_window=4)))
        evaluation = cw.evaluate(result)
        self.assertEqual(evaluation["verdict"], "fail")
        self.assertEqual(evaluation["windowsOverLimit"], [5])
        self.assertLess(evaluation["meanPercent"], 30.0)
        self.assertGreater(evaluation["maxPercent"], 30.0)

    def test_exactly_at_limit_passes_and_diagnostics_never_fail(self):
        result, _ = self.measure(Sim(self.root, rate=lambda t: 30.0))
        result["memory"]["after"]["vmRssMb"] = 9999.0                          # ogromny wzrost pamięci
        self.assertEqual(result["tick"]["share"], 0.6)                         # wysoki engine.tick (atrapa)
        evaluation = cw.evaluate(result)
        self.assertEqual(evaluation["verdict"], "pass")
        self.assertEqual(evaluation["diagnostics"]["engineTickShare"], 0.6)
        self.assertIn("bez progu", evaluation["diagnostics"]["note"])

    def test_high_load_makes_the_series_invalid(self):
        result, _ = self.measure(Sim(self.root, rate=self.rates(over_window=0)))
        result["loadavgAfter"] = "9.00 1 1 1/2 3"
        evaluation = cw.evaluate(result)
        self.assertEqual(evaluation["verdict"], "invalid")
        self.assertTrue(any("loadavgAfter" in reason for reason in evaluation["invalidReasons"]))


class InvalidationTest(Base):
    def test_pid_change_invalidates_the_series(self):
        sim = Sim(self.root)
        pids = iter([PID, PID, PID + 99] + [PID + 99] * 20)
        result, service = self.measure(sim, pid_source=lambda: next(pids))
        block = result["cpuWindows"]
        self.assertFalse(block["complete"])
        self.assertIn("zmiana PID", block["invalidReason"])
        self.assertEqual(len(block["windows"]), 2)
        self.assertEqual(cw.evaluate(result)["verdict"], "invalid")
        self.assertEqual(service.calls, [])                                    # bez diagnostyki po nieudanych oknach

    def test_restart_with_new_start_time_invalidates(self):
        sim = Sim(self.root)
        original = sim.sleep

        def sleeping(seconds):
            original(seconds)
            if sim.t > 200:
                sim.start += 1
                sim.write()
        result, _ = self.measure(sim, sleep=sleeping)
        self.assertIn("zmiana PID", result["cpuWindows"]["invalidReason"])

    def test_time_limit_invalidates_and_keeps_partial_windows(self):
        sim = Sim(self.root)
        result, service = self.measure(sim, deadline=30 + 3 * 60 + 5)
        block = result["cpuWindows"]
        self.assertFalse(block["complete"])
        self.assertEqual(block["invalidReason"], "limit czasu próby")
        self.assertEqual(len(block["windows"]), 3)
        self.assertEqual(cw.evaluate(result)["verdict"], "invalid")
        self.assertEqual(service.calls, [])

    def test_time_limit_in_warmup(self):
        result, _ = self.measure(Sim(self.root), warmup_s=500, deadline=100)
        self.assertEqual(cw.evaluate(result)["verdict"], "invalid")

    def test_diagnostics_skipped_at_limit_keeps_cpu_windows_valid(self):
        sim = Sim(self.root)
        result, service = self.measure(sim, deadline=30 + 600 + 10)
        self.assertEqual(service.calls, [])
        self.assertTrue(result["diagnosticsSkipped"])
        self.assertEqual(cw.evaluate(result)["verdict"], "pass")


class CleanIdleTest(Base):
    def test_no_service_requests_during_warmup_and_windows_diagnostics_after(self):
        sim = Sim(self.root)
        result, service = self.measure(sim)
        self.assertEqual([call[1] for call in service.calls], ["diagnostics"])
        called_at = service.calls[0][0]
        windows_end = 30 + 10 * 60
        self.assertGreaterEqual(called_at, windows_end)                        # po warmupie i po 10 oknach
        self.assertEqual(result["tick"]["share"], 0.6)
        self.assertEqual(result["snapshot"]["p95Ms"], 5.0)


class MemoryTest(Base):
    def test_status_before_and_after_windows_with_cgroup_marker(self):
        cgroup = os.path.join(self.root, "cg")
        group = os.path.join(cgroup, "user.slice", "app.scope")
        os.makedirs(group)
        with open(os.path.join(group, "memory.current"), "w", encoding="utf-8") as fh:
            fh.write("314572800\n")
        with open(os.path.join(group, "cgroup.procs"), "w", encoding="utf-8") as fh:
            fh.write(f"{PID}\n777\n")
        sim = Sim(self.root)
        with open(os.path.join(self.root, str(PID), "cgroup"), "w", encoding="utf-8") as fh:
            fh.write("0::/user.slice/app.scope\n")
        result, _ = self.measure(sim, cgroup_root=cgroup)
        before, after = result["memory"]["before"], result["memory"]["after"]
        for snap in (before, after):
            self.assertEqual((snap["rssAnonMb"], snap["vmRssMb"], snap["vmHwmMb"]), (150.0, 200.0, 300.0))
            self.assertEqual(snap["memoryCurrentMb"], 300.0)
            self.assertEqual(snap["cgroup"], {"path": "/user.slice/app.scope", "processes": 2,
                                              "sharedWithOtherProcesses": True})

    def test_cgroup_with_only_the_service_is_not_shared(self):
        group = os.path.join(self.root, "cg", "svc")
        os.makedirs(group)
        with open(os.path.join(group, "cgroup.procs"), "w", encoding="utf-8") as fh:
            fh.write(f"{PID}\n")
        Sim(self.root)
        with open(os.path.join(self.root, str(PID), "cgroup"), "w", encoding="utf-8") as fh:
            fh.write("0::/svc\n")
        cgroup = cw.read_cgroup_memory(PID, self.root, os.path.join(self.root, "cg"))
        self.assertFalse(cgroup["sharedWithOtherProcesses"])

    def test_production_uses_unit_memory_current(self):
        Sim(self.root)
        snap = cw.memory_snapshot(PID, self.root, memory_current=lambda: 104857600)
        self.assertEqual(snap["memoryCurrentMb"], 100.0)
        self.assertNotIn("cgroup", snap)
        self.assertEqual(cw.memory_snapshot(PID, self.root, memory_current=lambda: None)["memoryCurrentMb"], None)


class ApprovalTest(unittest.TestCase):
    class Handle:
        def __init__(self, sim, returns_at):
            self.sim, self.returns_at = sim, returns_at

        def done(self):
            return self.sim.t >= self.returns_at

    class Clock:
        t = 0.0

        def clock(self):
            return self.t

        def sleep(self, seconds):
            self.t += seconds

    def scenario(self, rows, approve_after, allow_small=False):
        sim = self.Clock()
        events = [{"type": "card.started"}, {"type": "plan.proposed", "rows": [{}] * rows}]
        calls = []

        def start():
            calls.append(sim.t)
            self.at = sim.t + approve_after
            return self.Handle(sim, self.at)

        def read():
            if calls and sim.t >= self.at and not any(e["type"] == "plan.approved" for e in events):
                events.append({"type": "plan.approved"})
            return list(events)
        out = cw.measure_approval(start, read, allow_small=allow_small, clock=sim.clock, sleep=sim.sleep,
                                  poll_s=0.5)
        return out, calls

    def test_measures_time_to_the_approval_event_and_rows(self):
        out, calls = self.scenario(18, 3.2)
        self.assertEqual(len(calls), 1)
        self.assertFalse(out["refused"])
        self.assertEqual(out["rows"], 18)
        self.assertAlmostEqual(out["approvalS"], 3.5, delta=0.6)
        self.assertTrue(out["ok"])
        self.assertNotIn("outsideCriterion", out)

    def test_ten_seconds_or_more_is_not_ok(self):
        out, _ = self.scenario(20, 10.2)
        self.assertFalse(out["ok"])
        self.assertGreaterEqual(out["approvalS"], 10.0)

    def test_refuses_small_plan_without_calling_the_service(self):
        out, calls = self.scenario(17, 1.0)
        self.assertTrue(out["refused"])
        self.assertEqual((calls, out["rows"]), ([], 17))
        self.assertIn("18", out["reason"])

    def test_small_plan_with_explicit_consent_is_marked_outside_the_criterion(self):
        out, calls = self.scenario(5, 1.0, allow_small=True)
        self.assertEqual(len(calls), 1)
        self.assertIn("poza kryterium", out["outsideCriterion"])

    def test_missing_event_is_reported(self):
        sim = self.Clock()
        out = cw.measure_approval(lambda: self.Handle(sim, 0), lambda: [{"type": "plan.proposed", "rows": [{}] * 18}],
                                  clock=sim.clock, sleep=sim.sleep, poll_s=1.0, wait_limit_s=5.0)
        self.assertFalse(out["ok"])
        self.assertIn("nie pojawiło się", out["error"])

    def test_already_approved_plan_is_refused(self):
        sim = self.Clock()
        events = [{"type": "plan.proposed", "rows": [{}] * 18}, {"type": "plan.approved"}]
        out = cw.measure_approval(lambda: self.Handle(sim, 0), lambda: events, clock=sim.clock, sleep=sim.sleep)
        self.assertTrue(out["refused"])


STAMPED = {"user.tide.recipe": json.dumps({"hash": "abc123"})}      # odcisk przepisu: pudełko Tide


class FakeHost:
    def __init__(self, sim, root, steps=None):
        self.sim, self.proc_root, self.hz, self.cpu_count = sim, root, HZ, 8
        self.clock, self.sleep = sim.clock, sim.sleep
        self.unit = "tide-daemon.service"
        self.steps = steps or []
        self.service_obj = FakeService(sim)
        self.approved = []
        self.events = [{"type": "plan.proposed", "rows": [{}] * 18}]

    def main_pid(self):
        return PID

    def memory_current(self):
        return 209715200

    def active_steps(self):
        return list(self.steps)

    def box_enabled(self):
        return True

    def service(self, pid):
        return self.service_obj

    def read_events(self, card):
        return list(self.events)

    def start_approve(self, card):
        self.approved.append(card)
        self.events.append({"type": "plan.approved"})
        return ApprovalTest.Handle(self.sim, 0)


class ProductionScriptTest(Base):
    def run_script(self, argv, host):
        out = io.StringIO()
        code = prod.main(argv, host=host, out=lambda text: out.write(str(text) + "\n"))
        return code, out.getvalue()

    def test_idle_measures_main_pid_window_and_writes_json_without_calling_the_service_early(self):
        sim = Sim(self.root, rate=lambda t: 8.0)
        host = FakeHost(sim, self.root)
        target = os.path.join(self.root, "wynik.json")
        code, text = self.run_script(["--out", target, "--warmup", "30", "--wait-idle-min", "0"], host)
        self.assertEqual(code, 0, text)
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual((data["mode"], data["boxEnabled"], data["cpuWindows"]["pid"]), ("idle", True, PID))
        self.assertEqual(len(data["cpuWindows"]["windows"]), 10)
        self.assertEqual(data["evaluation"]["verdict"], "pass")
        self.assertTrue(data["evaluation"]["ok"])
        self.assertEqual(data["memory"]["before"]["memoryCurrentMb"], 200.0)       # MemoryCurrent jednostki
        self.assertEqual(data["snapshot"]["p95Ms"], 5.0)
        self.assertGreaterEqual(host.service_obj.calls[0][0], 30 + 600)
        self.assertEqual(host.approved, [])                                         # tryb idle niczego nie zatwierdza

    def test_snapshot_p95_at_one_second_is_over_the_limit(self):
        sim = Sim(self.root, rate=lambda t: 8.0)
        host = FakeHost(sim, self.root)
        original = host.service_obj.diagnostics

        def slow(seconds):
            data = original(seconds)
            data["snapshot"]["p95Ms"] = 1000.0
            return data
        host.service_obj.diagnostics = slow
        code, text = self.run_script(["--out", os.path.join(self.root, "w.json"), "--warmup", "30",
                                      "--wait-idle-min", "0"], host)
        self.assertEqual(code, 1)
        self.assertIn("bloker M0", text)

    def test_window_over_limit_is_an_open_blocker(self):
        sim = Sim(self.root, rate=lambda t: 45.0 if 150 < t < 200 else 5.0)
        host = FakeHost(sim, self.root)
        code, text = self.run_script(["--out", os.path.join(self.root, "w.json"), "--warmup", "30",
                                      "--wait-idle-min", "0"], host)
        self.assertEqual(code, 1)
        self.assertIn("bloker M0", text)

    def test_steps_in_progress_make_it_invalid_before_any_window(self):
        sim = Sim(self.root)
        host = FakeHost(sim, self.root, steps=["tide-step-K-1-PT-1.scope"])
        code, text = self.run_script(["--out", os.path.join(self.root, "w.json"), "--warmup", "30",
                                      "--wait-idle-min", "0.05"], host)
        self.assertEqual(code, 2)
        self.assertEqual(host.service_obj.calls, [])
        self.assertEqual(sim.t >= 3, True)                                          # czekał na postój (wirtualnie)

    def test_windows_start_no_earlier_than_ten_minutes_after_the_last_step(self):
        sim = Sim(self.root, rate=lambda t: 5.0)
        host = FakeHost(sim, self.root)
        step_end = 100.0
        host.active_steps = lambda: ["tide-step-K-1-PT-1.service"] if sim.t < step_end else []
        code, text = self.run_script(["--out", os.path.join(self.root, "w.json"), "--warmup", "30"], host)
        self.assertEqual(code, 0, text)
        # pierwsze żądanie do usługi dopiero po: postój 600 s + rozgrzewka 30 s + 10 okien po 60 s
        self.assertGreaterEqual(host.service_obj.calls[0][0], step_end + 600 + 30 + 600)

    def test_a_step_during_the_wait_resets_the_idle_counter(self):
        sim = Sim(self.root)
        host = FakeHost(sim, self.root)
        host.active_steps = lambda: ["tide-step-K-1-PT-1.service"] if 300 <= sim.t < 330 else []
        idle = prod.wait_idle(host, 600.0)
        self.assertTrue(idle["reached"])
        self.assertGreaterEqual(sim.t, 330 + 600)
        self.assertEqual(idle["stepResets"], 2)

    def test_idle_not_reached_within_the_wait_limit_is_invalid(self):
        sim = Sim(self.root)
        host = FakeHost(sim, self.root, steps=["tide-step-K-1-PT-1.service"])
        code, text = self.run_script(["--out", os.path.join(self.root, "w.json"), "--warmup", "30",
                                      "--wait-idle-min", "10", "--max-wait-min", "15"], host)
        self.assertEqual(code, 2)
        self.assertEqual(host.service_obj.calls, [])

    def test_owner_idle_shorter_than_required_does_not_skip_the_wait(self):
        sim = Sim(self.root)
        host = FakeHost(sim, self.root)
        idle = prod.wait_idle(host, 600.0, owner_idle_min=1.0)
        self.assertTrue(idle["reached"])
        self.assertGreaterEqual(sim.t, 540)                                         # brakujące 9 min odmierzone

    def test_step_read_error_is_invalid_not_idle(self):
        sim = Sim(self.root)
        host = FakeHost(sim, self.root)

        def broken():
            raise prod.HostReadError("incus exec: kod 1")
        host.active_steps = broken
        target = os.path.join(self.root, "w.json")
        code, _ = self.run_script(["--out", target, "--warmup", "30", "--wait-idle-min", "0"], host)
        self.assertEqual(code, 2)
        self.assertEqual(host.service_obj.calls, [])
        with open(target, encoding="utf-8") as fh:
            self.assertIn("incus exec", json.load(fh)["invalidExtra"][0])

    def test_owner_idle_time_is_recorded(self):
        sim = Sim(self.root)
        host = FakeHost(sim, self.root)
        target = os.path.join(self.root, "w.json")
        self.run_script(["--out", target, "--warmup", "30", "--owner-idle-min", "12"], host)
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["idle"]["ownerIdleMinutes"], 12.0)

    def test_approve_plan_is_the_only_mode_that_calls_the_service(self):
        sim = Sim(self.root)
        host = FakeHost(sim, self.root)
        target = os.path.join(self.root, "z.json")
        code, _ = self.run_script(["--mode", "approve-plan", "--card", "K-105", "--out", target], host)
        self.assertEqual((code, host.approved), (0, ["K-105"]))
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual((data["mode"], data["approval"]["rows"], data["approval"]["ok"]), ("approve-plan", 18, True))

    def test_approve_plan_refuses_small_plan_without_consent(self):
        host = FakeHost(Sim(self.root), self.root)
        host.events = [{"type": "plan.proposed", "rows": [{}] * 6}]
        code, text = self.run_script(["--mode", "approve-plan", "--card", "K-9",
                                      "--out", os.path.join(self.root, "z.json")], host)
        self.assertEqual((code, host.approved), (2, []))
        self.assertIn("odmowa", text)

    def test_output_is_refused_in_owner_data_directories(self):
        host = FakeHost(Sim(self.root), self.root)
        for path in ("~/.local/share/tide/w.json", "~/.config/w.json"):
            code, text = self.run_script(["--out", os.path.expanduser(path)], host)
            self.assertEqual(code, 2)
            self.assertIn("odmowa", text)
        self.assertEqual(host.service_obj.calls, [])

    def test_script_reads_box_enabled_from_the_config_without_writing_it(self):
        config = os.path.join(self.root, "config", "tide")
        os.makedirs(config)
        path = os.path.join(config, "cardflow.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"box": {"enabled": True}}, fh)
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.root, "config")
        self.addCleanup(lambda: os.environ.pop("XDG_CONFIG_HOME") if old is None
                        else os.environ.__setitem__("XDG_CONFIG_HOME", old))
        with open(path, "rb") as fh:
            before = fh.read()
        self.assertTrue(prod.SystemHost().box_enabled())
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def _system_host(self, box_enabled, runner):
        host = prod.SystemHost()
        host.box_enabled = lambda: box_enabled
        patch = mock.patch.object(prod.subprocess, "run", side_effect=runner)
        patch.start()
        self.addCleanup(patch.stop)
        return host

    @staticmethod
    def _done(stdout="", code=0):
        return subprocess.CompletedProcess([], code, stdout, "")

    def test_step_only_inside_the_box_is_seen_when_box_is_enabled(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            if argv[0] == "systemctl":                                              # host: pusto
                return self._done("")
            if argv[1] == "list":
                return self._done(json.dumps([{"name": "tide-box-1", "status": "Running", "config": STAMPED},
                                              {"name": "stara", "status": "Stopped", "config": STAMPED}]))
            return self._done("tide-step-K-1-PT-1.service loaded active running krok\n")
        host = self._system_host(True, runner)
        self.assertEqual(host.active_steps(), ["box:tide-box-1:tide-step-K-1-PT-1.service"])
        self.assertTrue(any("exec" in c and "tide-box-1" in c and "systemctl" in c for c in calls))
        self.assertFalse(any("stara" in c for c in calls))

    def test_box_is_not_queried_when_box_is_disabled(self):
        host = self._system_host(False, lambda argv, **kw: self._done(""))
        self.assertEqual(host.active_steps(), [])
        self.assertEqual(prod.subprocess.run.call_count, 1)

    def test_box_read_failure_raises_instead_of_reporting_no_steps(self):
        def runner(argv, **kwargs):
            if argv[0] == "systemctl":
                return self._done("")
            return self._done("", code=1)
        host = self._system_host(True, runner)
        with self.assertRaises(prod.HostReadError):
            host.active_steps()

    def test_exec_failure_in_one_box_raises(self):
        def runner(argv, **kwargs):
            if argv[0] == "systemctl":
                return self._done("")
            if argv[1] == "list":
                return self._done(json.dumps([{"name": "b", "status": "Running", "config": STAMPED}]))
            return self._done("", code=1)
        host = self._system_host(True, runner)
        with self.assertRaises(prod.HostReadError):
            host.active_steps()

    def _incus(self, instances, exec_result=None):
        """Atrapa `incus`: host pusty, `list` zwraca instancje, `exec` — wynik z `exec_result(argv)`; zapisuje wywołania."""
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            if argv[0] == "systemctl":
                return self._done("")
            if argv[1] == "list":
                return self._done(json.dumps(instances))
            return exec_result(argv)
        return runner, calls

    def _no_bus(self, argv):
        return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus: No medium found\n")

    def test_foreign_container_gets_no_exec_and_is_listed(self):
        runner, calls = self._incus([{"name": "obcy", "status": "Running", "config": {"user.foo": "x"}},
                                     {"name": "bez-config", "status": "Running"},
                                     {"name": "pusty", "status": "Running", "config": {"user.tide.recipe": ""}},
                                     {"name": "tide-1", "status": "Running", "config": STAMPED}],
                                    lambda argv: self._done("tide-step-K-1-PT-1.service loaded active running x\n"))
        host = self._system_host(True, runner)
        self.assertEqual(host.active_steps(), ["box:tide-1:tide-step-K-1-PT-1.service"])
        self.assertEqual(host.ignored_instances(), ["bez-config", "obcy", "pusty"])
        execs = [c for c in calls if len(c) > 1 and c[1] == "exec"]
        self.assertEqual(len(execs), 1)
        self.assertIn("tide-1", execs[0])

    def test_both_stamp_shapes_mark_a_tide_box(self):
        instances = [{"name": "json", "status": "Running", "config": {"user.tide.recipe": '{"hash":"abc","v":1}'}},
                     {"name": "hash", "status": "Running", "config": {"user.tide.recipe": "abc123"}},
                     {"name": "bezhash", "status": "Running", "config": {"user.tide.recipe": '{"v":1}'}},
                     {"name": "pusty-hash", "status": "Running", "config": {"user.tide.recipe": '{"hash":""}'}},
                     {"name": "lista", "status": "Running", "config": {"user.tide.recipe": "[1]"}}]
        runner, calls = self._incus(instances, lambda argv: self._done(""))
        host = self._system_host(True, runner)
        host.active_steps()
        self.assertEqual(host.ignored_instances(), ["bezhash", "lista", "pusty-hash"])
        self.assertEqual(sum(1 for c in calls if len(c) > 1 and c[1] == "exec"), 2)

    def test_tide_box_without_bus_is_a_note_not_an_error_once_per_box(self):
        runner, _ = self._incus([{"name": "tide-1", "status": "Running", "config": STAMPED}], self._no_bus)
        host = self._system_host(True, runner)
        for _ in range(3):
            self.assertEqual(host.active_steps(), [])
        self.assertEqual(len(host.box_notes()), 1)
        self.assertIn("tide-1", host.box_notes()[0])

    def test_other_exec_errors_in_a_tide_box_still_raise(self):
        for result in (subprocess.CompletedProcess([], 1, "", "inny błąd"),
                       subprocess.CompletedProcess([], 255, "", "Error: Instance not found"),
                       subprocess.CompletedProcess([], 0, "", "Failed to connect to bus")):
            runner, _ = self._incus([{"name": "tide-1", "status": "Running", "config": STAMPED}],
                                    lambda argv, r=result: r)
            host = self._system_host(True, runner)
            if result.returncode == 0:
                self.assertEqual(host.active_steps(), [])        # kod 0: nie jest błędem magistrali
            else:
                with self.assertRaises(prod.HostReadError):
                    host.active_steps()

    def test_timeout_in_tide_box_raises(self):
        def boom(argv):
            raise subprocess.TimeoutExpired(argv, 60)
        runner, _ = self._incus([{"name": "tide-1", "status": "Running", "config": STAMPED}], boom)
        host = self._system_host(True, runner)
        with self.assertRaises(prod.HostReadError):
            host.active_steps()

    def test_wait_idle_foreign_and_busless_box_keeps_the_measurement_valid(self):
        runner, _ = self._incus([{"name": "obcy", "status": "Running", "config": {}},
                                 {"name": "tide-1", "status": "Running", "config": STAMPED}], self._no_bus)
        host = self._system_host(True, runner)
        sim = Sim(self.root)
        host.clock, host.sleep = sim.clock, sim.sleep
        idle = prod.wait_idle(host, 30.0, poll_s=15.0)
        self.assertTrue(idle["reached"])
        self.assertIsNone(idle["readError"])
        self.assertEqual(len(idle["boxNotes"]), 1)
        self.assertIn("tide-1", idle["boxNotes"][0])
        self.assertEqual(idle["ignoredInstances"], ["obcy"])

    def test_wait_idle_incus_list_failure_is_invalid(self):
        def runner(argv, **kwargs):
            return self._done("") if argv[0] == "systemctl" else self._done("", code=1)
        host = self._system_host(True, runner)
        sim = Sim(self.root)
        host.clock, host.sleep = sim.clock, sim.sleep
        idle = prod.wait_idle(host, 30.0, poll_s=15.0)
        self.assertFalse(idle["reached"])
        self.assertTrue(idle["readError"])

    def test_wait_idle_tide_box_exec_error_is_invalid(self):
        runner, _ = self._incus([{"name": "tide-1", "status": "Running", "config": STAMPED}],
                                lambda argv: subprocess.CompletedProcess(argv, 1, "", "inny błąd"))
        host = self._system_host(True, runner)
        sim = Sim(self.root)
        host.clock, host.sleep = sim.clock, sim.sleep
        idle = prod.wait_idle(host, 30.0, poll_s=15.0)
        self.assertFalse(idle["reached"])
        self.assertIn("inny błąd", idle["readError"])

    def test_box_disabled_has_no_incus_calls_and_empty_notes(self):
        host = self._system_host(False, lambda argv, **kw: self._done(""))
        sim = Sim(self.root)
        host.clock, host.sleep = sim.clock, sim.sleep
        idle = prod.wait_idle(host, 0.0, poll_s=15.0)
        self.assertEqual((idle["boxNotes"], idle["ignoredInstances"]), ([], []))
        self.assertTrue(all(c.args[0][0] == "systemctl" for c in prod.subprocess.run.call_args_list))

    def test_fake_host_without_new_attributes_gives_empty_fields(self):
        host = FakeHost(Sim(self.root), self.root)
        idle = prod.wait_idle(host, 0.0, poll_s=15.0)
        self.assertEqual((idle["boxNotes"], idle["ignoredInstances"]), ([], []))


if __name__ == "__main__":
    unittest.main()
