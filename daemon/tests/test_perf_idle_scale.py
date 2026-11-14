"""Testy trybu skali produkcji reproduktora CPU postoju (K-105, PT-410): wybór wątku przepływu, ocena przed/po,
seria Snapshot i dzienniki o skali produkcji (mała skala, ścisły odczyt).

Nie mierzą czasu — działają na wynikach syntetycznych i izolowanych danych w katalogu tymczasowym."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from . import perf_board_refresh as pbr
from . import perf_idle_cpu as perf
from . import perf_idle_scale as scale

PID = 1000


def thread(name, cpu):
    return {"name": name, "cpuS": cpu}


class FlowThreadTest(unittest.TestCase):
    def test_single_named_thread_wins_even_when_another_is_busier(self):
        threads = {"1000": thread("tide-daemon", 20.0), "1001": thread("tide-cardflow_0", 3.0),
                   "1002": thread("tide-sampler", 9.0), "1003": thread("Thread-5", 12.0)}
        flow = scale.select_flow_thread(threads, PID, 60.0)
        self.assertEqual(flow, {"tid": 1001, "name": "tide-cardflow_0", "cpuPercent": 5.0, "source": "name"})

    def test_without_named_thread_largest_cpu_excluding_main_and_sampler(self):
        threads = {"1000": thread("python3", 50.0), "1001": thread("tide-sampler", 40.0),
                   "1002": thread("Thread-1", 6.0), "1003": thread("Thread-2", 12.0)}
        flow = scale.select_flow_thread(threads, PID, 60.0)
        self.assertEqual((flow["tid"], flow["source"], flow["cpuPercent"]), (1003, "max-cpu", 20.0))

    def test_two_named_threads_fall_back_to_largest_cpu(self):
        threads = {"1001": thread("tide-cardflow_0", 1.0), "1002": thread("tide-cardflow_1", 2.0),
                   "1003": thread("Thread-2", 6.0)}
        flow = scale.select_flow_thread(threads, PID, 60.0)
        self.assertEqual((flow["tid"], flow["source"]), (1003, "max-cpu"))

    def test_tie_takes_the_smaller_tid_numerically(self):
        threads = {"1010": thread("a", 5.0), "1002": thread("b", 5.0), "1003": thread("c", 1.0)}
        self.assertEqual(scale.select_flow_thread(threads, PID, 10.0)["tid"], 1002)

    def test_only_main_and_sampler_gives_no_thread(self):
        threads = {"1000": thread("python3", 5.0), "1001": thread("tide-sampler", 1.0)}
        self.assertIsNone(scale.select_flow_thread(threads, PID, 60.0))
        self.assertIsNone(scale.select_flow_thread({}, PID, 60.0))


def result(flow_cpu=10.0, tick_share=0.1, rss_end=200.0, load="0.50 0.40 0.30 1/200 99", cores=8, per_second=5.0):
    return {"cpuCount": cores, "loadavgBefore": load, "loadavgWindowStart": load, "loadavgWindowEnd": load,
            "loadavgAfter": load, "cpuWindow": {"cpuPercent": 55.0},
            "flowThread": {"tid": 1001, "name": "tide-cardflow_0", "cpuPercent": flow_cpu, "source": "name"},
            "tick": {"share": tick_share, "samples": 100, "perSecond": per_second},
            "snapshot": {"p95Ms": 12.5}, "memory": {"vmRssStartMb": 190.0, "vmRssEndMb": rss_end, "vmHwmMb": 260.0}}


class EvaluateTest(unittest.TestCase):
    def test_single_window_evaluation_has_no_thresholds_and_no_verdict(self):
        evaluation = scale.evaluate(result())
        for gone in ("verdict", "thresholds", "failedThresholds"):
            self.assertNotIn(gone, evaluation)
        self.assertIn("bez oceny progu; rozstrzyga tryb 10 okien", evaluation["mode"])
        self.assertTrue(evaluation["valid"])
        reported = evaluation["reported"]
        self.assertEqual((reported["flowThreadCpuPercent"], reported["flowThreadSource"]), (10.0, "name"))
        self.assertEqual(reported["engineTickShare"], 0.1)
        self.assertEqual(reported["processCpuPercent"], 55.0)
        self.assertEqual(reported["snapshotP95Ms"], 12.5)
        self.assertIn("rozstrzyga pomiar produkcji", evaluation["note"])

    def test_high_flow_thread_and_tick_share_are_only_reported(self):
        evaluation = scale.evaluate(result(flow_cpu=99.0, tick_share=0.95))
        self.assertTrue(evaluation["valid"])
        self.assertEqual(evaluation["reported"]["flowThreadCpuPercent"], 99.0)
        text = scale.evaluation_text(evaluation)
        self.assertIn("bez oceny progu", text)
        self.assertNotIn("NIE", text)
        self.assertNotIn("werdykt", text)

    def test_missing_measurement_is_reported_as_none_not_failed(self):
        data = result()
        data["flowThread"] = None
        evaluation = scale.evaluate(data)
        self.assertTrue(evaluation["valid"])
        self.assertIsNone(evaluation["reported"]["flowThreadCpuPercent"])

    def test_high_load_makes_the_run_invalid(self):
        evaluation = scale.evaluate(result(load="4.00 3.00 2.00 1/200 99", cores=8))
        self.assertFalse(evaluation["valid"])
        self.assertTrue(any("loadavgBefore" in reason for reason in evaluation["invalidReasons"]))
        self.assertTrue(scale.evaluate(result(load="3.99 1 1 1/2 3", cores=8))["valid"])

    def test_load_after_the_window_also_invalidates(self):
        data = result()
        data["loadavgAfter"] = "9.00 1.00 1.00 1/200 99"
        self.assertFalse(scale.evaluate(data)["valid"])

    def test_missing_load_reading_is_invalid(self):
        data = result()
        del data["loadavgWindowEnd"]
        self.assertFalse(scale.evaluate(data)["valid"])

    def test_comparison_and_rss_growth_against_before(self):
        before = result(flow_cpu=40.0, tick_share=0.5, rss_end=200.0, per_second=50.0)
        after = result(flow_cpu=10.0, tick_share=0.1, rss_end=230.0, per_second=10.0)
        evaluation = scale.evaluate(after, before)
        growth = evaluation["vmRssGrowth"]
        self.assertEqual((growth["growthMb"], growth["growthPercent"]), (30.0, 15.0))
        self.assertIsNone(growth["limit"])                                       # bez progu
        comparison = evaluation["comparison"]
        self.assertEqual(comparison["flowThreadCpuPercent"]["delta"], -30.0)
        self.assertEqual(comparison["engineTickPerSecond"]["delta"], -40.0)
        self.assertEqual(comparison["engineTickShare"]["before"], 0.5)
        self.assertIn("+30.0 MB", scale.evaluation_text(evaluation))

    def test_invalid_before_makes_the_comparison_invalid(self):
        before = result(load="9.00 1.00 1.00 1/200 99")
        evaluation = scale.evaluate(result(), before)
        self.assertFalse(evaluation["valid"])
        self.assertTrue(any(reason.startswith("przed:") for reason in evaluation["invalidReasons"]))

    def test_evaluate_command_exit_codes_and_output_file(self):
        base = tempfile.mkdtemp(prefix="tide-perf-scale-test-")
        self.addCleanup(shutil.rmtree, base)
        paths = {}
        for name, data in (("ok", result()), ("high", result(flow_cpu=80.0)), ("load", result(load="8 8 8 1/2 3"))):
            paths[name] = os.path.join(base, name + ".json")
            with open(paths[name], "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        out = os.path.join(base, "ocena.json")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scale.main(["evaluate", "--after", paths["ok"], "--out", out]), 0)
            with open(out, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertNotIn("verdict", saved)
            self.assertTrue(saved["valid"])
            self.assertEqual(scale.main(["evaluate", "--after", paths["high"], "--before", paths["ok"]]), 0)
            self.assertEqual(scale.main(["evaluate", "--after", paths["load"]]), 2)


class MemoryAndSeriesTest(unittest.TestCase):
    def test_status_parsed_to_megabytes(self):
        text = "Name:\tpython3\nVmPeak:\t  999 kB\nVmHWM:\t  262144 kB\nVmRSS:\t  204800 kB\nThreads:\t9\n"
        self.assertEqual(scale.parse_status_mb(text), {"VmRSS": 200.0, "VmHWM": 256.0})
        self.assertEqual(scale.parse_status_mb("Name:\tx\n"), {"VmRSS": None, "VmHWM": None})

    def test_nearest_rank_p95_of_sixty_calls(self):
        self.assertEqual(scale.nearest_rank(list(range(1, 61))), 57)      # ⌈0,95·60⌉ = 57
        self.assertIsNone(scale.nearest_rank([]))

    def test_series_makes_sixty_calls_spaced_by_the_interval_and_stops(self):
        now = [0.0]
        calls = []

        def call():
            calls.append(now[0])
            return float(len(calls))
        series = scale.SnapshotSeries(call, clock=lambda: now[0])
        for _ in range(400):                       # pętla okna woła `step` częściej niż co 0,5 s
            series.step()
            now[0] += 0.25
        result_ = series.result()
        self.assertEqual(len(calls), 60)
        self.assertEqual(result_["calls"], 60)
        self.assertEqual(result_["p95Ms"], 57.0)
        self.assertEqual(result_["errors"], 0)
        self.assertTrue(all(b - a >= 0.5 for a, b in zip(calls, calls[1:])))

    def test_series_counts_failed_calls(self):
        series = scale.SnapshotSeries(lambda: None, calls=3, interval=0.0)
        for _ in range(5):
            series.step()
        self.assertEqual((series.result()["errors"], series.result()["samplesMs"]), (3, []))


class RolesTest(unittest.TestCase):
    def test_production_roles(self):
        roles = scale.assign_roles(46)
        self.assertEqual(len(roles), 46)
        self.assertEqual(roles.count("outside"), 3)
        self.assertGreaterEqual(roles.count("done") + roles.count("archived"), 30)
        self.assertGreater(roles.count("archived"), 0)
        self.assertGreaterEqual(roles.count("question"), 1)

    def test_small_scale_still_has_every_kind(self):
        roles = scale.assign_roles(8)
        self.assertEqual(set(roles), {"outside", "question", "plan", "done"})       # archiwum dopiero w większej skali
        self.assertIn("outside", roles)
        with self.assertRaises(ValueError):
            scale.assign_roles(3)


class PopulateTest(unittest.TestCase):
    """Dym w małej skali: dzienniki z generatora przechodzą ścisły odczyt i kontrolę tożsamości procesów."""

    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="tide-perf-scale-test-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_small_scale_builds_strictly_readable_journals_of_the_requested_size(self):
        with pbr.isolated_env(self.base):
            service = pbr.Service(self.base, 8, box_file=False)
            self.addCleanup(service.close)
            stats = scale.populate(service, self.base, 1.0)
            from tide_daemon.cardflow.journal import Journal
            fresh = Journal(service.root)
            cards = fresh.cards_complete()
            self.assertEqual(len(cards), 8)
            for card in cards:
                events = fresh.read_complete(card)                      # ścisły odczyt: seq od 1, tożsamość procesów
                self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
                self.assertEqual(fresh.check(card), "", "kopia usługi i plik w Tablicy są takie same")
            board = service.store.load()
            columns = sorted(card.column for card in board.cards.values())
        self.assertEqual(stats["problems"], [])
        self.assertEqual(stats["outsideDecisions"], stats["roles"]["outside"])
        self.assertEqual(stats["questionDecisions"], stats["roles"]["question"])
        self.assertGreaterEqual(columns.count("done") + columns.count("archived"), 4)
        disk = stats["disk"]
        self.assertEqual(disk["cards"], 8)
        self.assertEqual(disk["totalBytes"], disk["mirrorBytes"] + disk["boardBytes"])
        self.assertEqual(disk["mirrorBytes"], disk["boardBytes"])
        self.assertGreaterEqual(disk["totalBytes"], 0.95 * 1048576)
        self.assertLessEqual(disk["totalBytes"], 1.05 * 1048576)

    def test_ticks_on_generated_journals_are_quiet_after_warm_up(self):
        """Postój to postój: po rozgrzewce tick niczego nie dopisuje i żaden przebieg karty nie kończy się wyjątkiem.

        Pierwszy tick wolno dopisać jednorazowe zdarzenia (card-branch, plan-commit, merge.check-armed, flow.notice)."""
        with pbr.isolated_env(self.base):
            service = pbr.Service(self.base, 10, box_file=False)
            self.addCleanup(service.close)
            scale.populate(service, self.base, 0.5)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                service.engine.tick()                                   # rozgrzewka
                before = {c: len(service.engine.journal.read(c)) for c in service.cards}
                for _ in range(3):
                    service.engine.tick()
                after = {c: len(service.engine.journal.read(c)) for c in service.cards}
        self.assertEqual(before, after, "kolejne ticki nie dopisują zdarzeń")
        self.assertNotIn("przepływ karty", out.getvalue(), out.getvalue())
        self.assertNotIn("Error(", out.getvalue())

    def test_broken_journal_is_reported_by_the_strict_check(self):
        with pbr.isolated_env(self.base):
            service = pbr.Service(self.base, 5, box_file=False)
            self.addCleanup(service.close)
            scale.populate(service, self.base, 0.3)
            from tide_daemon.cardflow.journal import Journal
            card = service.cards[0]
            fresh = Journal(service.root)
            path = fresh.mirror_path(card)
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines(keepends=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("".join(lines[:2] + lines[3:]))                # luka w seq
            problems, _events = scale.verify(fresh, [card])
        self.assertTrue(problems and "ścisły odczyt" in problems[0], problems)


class DefaultModeTest(unittest.TestCase):
    def test_default_parameters_keep_the_pt346_mode(self):
        args = perf.build_parser().parse_args(["--out", "x.json", "--box", "on"])
        self.assertEqual((args.scale, args.cards, args.sessions, args.warmup, args.window),
                         ("default", None, 200, 45.0, 60.0))
        scaled = perf.build_parser().parse_args(["--out", "x.json", "--box", "on", "--scale", "prod"])
        self.assertEqual((scaled.scale, scaled.journal_mb), ("prod", 60.0))

    def test_scale_summary_names_the_flow_thread_source(self):
        data = {"sha": "abc", "params": {"box": True}, "loadavgBefore": "0.1", "loadavgAfter": "0.2",
                "flowThread": {"name": "tide-cardflow_0", "source": "name", "cpuPercent": 7.5},
                "tick": {"share": 0.2, "perSecond": 4.0}, "snapshot": {"p95Ms": 9.0},
                "memory": {"vmRssStartMb": 1.0, "vmRssEndMb": 2.0, "vmHwmMb": 3.0},
                "scale": {"disk": {"cards": 46, "totalMb": 60.0}}, "cpuWindow": {"cpuPercent": 50.0}}
        text = perf.scale_summary(data)
        self.assertIn("(name)", text)
        self.assertIn("46 kart", text)


if __name__ == "__main__":
    unittest.main()
