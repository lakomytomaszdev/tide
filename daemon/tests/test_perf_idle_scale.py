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
from unittest import mock

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

    def test_production_roles_have_the_k125_states(self):
        """4 wstrzymane z 7 prośbami (3, 2, 1, 1), ≥ 1 aktywna z prośbą outside, 1 z trzymanym procesem, 1 z planem."""
        roles = scale.assign_roles(46)
        self.assertEqual(roles.count("paused"), 4)
        self.assertEqual(list(scale.paused_requests(roles).values()), [3, 2, 1, 1])
        self.assertGreaterEqual(roles.count("outside"), 1)
        self.assertEqual(roles.count("held"), 1)
        self.assertEqual(roles.count("approved"), 1)
        self.assertEqual(len(set(scale.paused_requests(roles))), 4)

    def test_small_scale_still_has_every_kind(self):
        roles = scale.assign_roles(8)
        self.assertEqual(set(roles), {"outside", "question", "plan", "paused", "held", "approved", "done"})
        self.assertEqual(list(scale.paused_requests(roles).values()), [3])      # jedna wstrzymana, archiwum dopiero większe
        self.assertEqual(list(scale.paused_requests(scale.assign_roles(scale.FULL_PAUSED_CARDS)).values()),
                         [3, 2, 1, 1])
        with self.assertRaises(ValueError):
            scale.assign_roles(3)

    def test_every_scale_keeps_a_finished_card_and_distinct_places(self):
        for count in range(scale.MIN_CARDS, 60):
            roles = scale.assign_roles(count)
            self.assertEqual(len(roles), count)
            self.assertTrue({"done"} & set(roles), count)
            self.assertIn("outside", roles)


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
        self.assertGreaterEqual(columns.count("done") + columns.count("archived"), 2)
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
            service = pbr.Service(self.base, 12, box_file=False)
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

    def populated(self, cards=scale.FULL_PAUSED_CARDS, journal_mb=0.5):
        """Tablica z danymi skali i świeży odczyt dzienników; usługa zamykana na koniec testu."""
        from tide_daemon.cardflow.journal import Journal
        service = pbr.Service(self.base, cards, box_file=False)
        self.addCleanup(service.close)
        stats = scale.populate(service, self.base, journal_mb)
        return service, stats, Journal(service.root)

    def test_small_board_has_every_production_state(self):
        """Dokładnie 7 decyzji `permission` w 4 kartach wstrzymanych, 1 w aktywnej, karta `held`, karta z planem."""
        from tide_daemon.cardflow import state
        with pbr.isolated_env(self.base):
            service, stats, fresh = self.populated()
            roles = scale.assign_roles(len(service.cards))
            self.assertEqual(stats["problems"], [])
            self.assertEqual(stats["pausedDecisions"], 7)
            self.assertEqual(sorted(stats["pausedRequests"]), [1, 1, 2, 3])
            self.assertEqual(stats["outsideDecisions"], 1)
            self.assertEqual((stats["heldCards"], stats["approvedCards"]), (1, 1))
            states = {role: [state.fold(fresh.read_complete(card)) for card, r in zip(service.cards, roles) if r == role]
                      for role in set(roles)}
        for st in states["paused"]:
            self.assertTrue(st.paused)
            self.assertFalse(st.running())
            asked = [name for name, done in st.finished.items() if done.get("outside") and done["result"] == "denied"]
            self.assertTrue(asked)
            self.assertFalse(set(asked) & set(st.step_decisions), "prośby wiszą bez decyzji")
        (held,) = states["held"]
        self.assertTrue(any(i.held and i.state == "ok" for i in held.process_identities.values()))
        self.assertFalse(held.running())
        (approved,) = states["approved"]
        self.assertTrue(approved.plan_approved)
        self.assertTrue((approved.results[("plan-commit", scale.plan_commit_key(approved))]).get("ok"))
        self.assertFalse(approved.running())
        self.assertFalse(any(st.paused or st.running() for role in ("outside", "question", "plan", "done")
                             for st in states.get(role, [])))

    def test_paused_requests_never_get_an_automatic_consent(self):
        from tide_daemon.board import commands
        from tide_daemon.cardflow import consent, state
        with pbr.isolated_env(self.base):
            service, _stats, fresh = self.populated()
            roles = scale.assign_roles(len(service.cards))
            for card, role in zip(service.cards, roles):
                if role != "paused":
                    continue
                st = state.fold(fresh.read_complete(card))
                project = service.engine._project(card)[1]
                for name, done in st.finished.items():
                    if not done.get("outside"):
                        continue                                                # krok planowania
                    step = st.steps[name]
                    roots = (step["worktree"], project.repo, step["stepDir"])
                    self.assertIn(done["command"], scale.OUTSIDE_COMMANDS)
                    self.assertTrue(commands.outside_allowed(done["command"], project.denied_commands, roots))
                    self.assertTrue(commands.outside_risky(done["command"], roots, []))
                    self.assertFalse(consent.automatic(service.engine, card, step, done["command"]))

    def test_populate_rejects_data_without_the_intended_states(self):
        """Kontrola ról odrzuca dane, które stanu nie dają (zła rola, inna liczba próśb) — z opisem problemu."""
        with pbr.isolated_env(self.base):
            service, _stats, fresh = self.populated()
            roles = scale.assign_roles(len(service.cards))
            requests = scale.paused_requests(roles)
            swapped = list(roles)
            held, done = swapped.index("held"), swapped.index("done")
            swapped[held], swapped[done] = "done", "held"
            _found, problems = scale.check_roles(service.engine, fresh, service.cards, swapped, requests)
            self.assertTrue(any("trzymany proces" in p for p in problems), problems)
            paused = next(iter(requests))
            _found, problems = scale.check_roles(service.engine, fresh, service.cards, roles,
                                                 {**requests, paused: requests[paused] + 1})
            self.assertTrue(any("permission" in p for p in problems), problems)
            noplan = list(roles)
            noplan[noplan.index("approved")], noplan[noplan.index("plan")] = "plan", "approved"
            _found, problems = scale.check_roles(service.engine, fresh, service.cards, noplan, requests)
            self.assertTrue(problems)

    def test_approved_plan_without_plan_commit_result_is_rejected(self):
        from tide_daemon.cardflow import state
        log, marks, extra = scale.build_card("K-9", 9, "approved", "alpha", "/ws/alpha", "/tmp/x", 0, 0, 105)
        events = scale.expand(log, marks, 0, 1.7e9, "approved")
        self.assertEqual(scale._approved_problems("K-9", state.fold(events)), [])
        cut = [e for e in events if not (e["type"] in ("intent", "result") and e.get("action") == "plan-commit")]
        self.assertTrue(any("plan-commit" in p for p in scale._approved_problems("K-9", state.fold(cut))))

    def test_every_tick_walks_the_production_roads_of_the_profile(self):
        """Nowe role naprawdę uruchamiają w ticku `_consent_recheck` (prośba aktywnej karty), `_plan_next`
        (plan w spoczynku, trzymany proces) i `trialhold.inventory` — to ramki kategorii `outside`/`plan`/`inventory`.
        K-125 #2 (PT-487): prośby wstrzymanych kart nie są już oceniane w ticku (pauza przed `_consent_recheck`);
        reproduktor nadal je zakłada (7 próśb w `paused_requests`), a ich nieobecność w ocenie jest wynikiem „po”."""
        import collections
        from tide_daemon.cardflow import consent, engine as engine_mod, trialhold
        with pbr.isolated_env(self.base):
            service, _stats, _fresh = self.populated()
            roles = scale.assign_roles(len(service.cards))
            requests = scale.paused_requests(roles)
            seen = {"recheck": collections.Counter(), "plan": collections.Counter(),
                    "inventory": collections.Counter(), "automatic": collections.Counter()}
            real = {"recheck": engine_mod.FlowEngine._consent_recheck, "plan": engine_mod.FlowEngine._plan_next,
                    "inventory": trialhold.inventory, "automatic": consent.automatic}

            def recheck(this, card, step, done):
                seen["recheck"][card] += 1
                return real["recheck"](this, card, step, done)

            def plan(this, card, st):
                seen["plan"][card] += 1
                return real["plan"](this, card, st)

            def inventory(this, card, st, **kw):
                seen["inventory"][card] += 1
                return real["inventory"](this, card, st, **kw)

            def automatic(this, card, step, command):
                seen["automatic"][card] += 1
                return real["automatic"](this, card, step, command)
            with mock.patch.object(engine_mod.FlowEngine, "_consent_recheck", recheck), \
                    mock.patch.object(engine_mod.FlowEngine, "_plan_next", plan), \
                    mock.patch.object(trialhold, "inventory", inventory), \
                    mock.patch.object(consent, "automatic", automatic), contextlib.redirect_stdout(io.StringIO()):
                service.engine.tick()                                           # rozgrzewka
                for counter in seen.values():
                    counter.clear()
                service.engine.tick()
            paused = {service.cards[i]: n for i, n in requests.items()}
            outside = [service.cards[i] for i, r in enumerate(roles) if r == "outside"]
            held = service.cards[roles.index("held")]
            approved = service.cards[roles.index("approved")]
        self.assertEqual(sum(paused.values()), 7)
        self.assertEqual(dict(seen["recheck"]), {c: 1 for c in outside})
        self.assertFalse(set(seen["recheck"]) & set(paused), "wstrzymana karta oceniła prośbę w ticku")
        self.assertEqual(seen["automatic"], seen["recheck"], "każda prośba przechodzi sprawdzenie zgody")
        self.assertGreaterEqual(seen["plan"][approved], 1)
        self.assertGreaterEqual(seen["plan"][held], 1)
        self.assertGreaterEqual(seen["inventory"][held], 1)

    def test_held_card_with_an_active_unit_is_not_running_and_reads_the_unit_every_tick(self):
        """Atrapa `systemctl` w PATH (`isolated_env` reproduktora): `active`, a karta nadal nie ma kroku w toku."""
        from tide_daemon.cardflow import state
        with pbr.isolated_env(self.base):
            service, _stats, _fresh = self.populated()
            roles = scale.assign_roles(len(service.cards))
            held = service.cards[roles.index("held")]
            fake = perf.isolated_env(self.base)
            with mock.patch.dict(os.environ, {"PATH": fake["PATH"],
                                              "TIDE_FAKE_SYSTEMCTL_LOG": fake["TIDE_FAKE_SYSTEMCTL_LOG"]}):
                with contextlib.redirect_stdout(io.StringIO()):
                    service.engine.tick()                                       # rozgrzewka
                    calls = perf.systemctl_calls(self.base)
                    before = len(service.engine.journal.read(held))
                    for _ in range(3):
                        service.engine.tick()
                    events = service.engine.journal.read(held)
                    after = len(events)
                self.assertGreaterEqual(perf.systemctl_calls(self.base) - calls, 3 * scale.PROD_HELD_STEPS,
                                        "atrapa pytana o każdą jednostkę trzymanego procesu w każdym ticku")
            self.assertEqual(before, after, f"stan `active` jest zgodny z zapisaną inwentaryzacją: {events[before:]}")
            st = state.fold(service.engine.journal.read_complete(held))
            self.assertEqual(st.running(), [])
            inventory = [e for e in service.engine.journal.read(held) if e["type"] == "k38.inventory"]
            self.assertEqual([i["state"] for i in inventory[-1]["items"]], ["active"] * scale.PROD_HELD_STEPS)
            held_ids = [n for n, i in st.process_identities.items() if i.held and i.state == "ok"]
            self.assertEqual(len(held_ids), scale.PROD_HELD_STEPS)

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


def card_shape(**fields):
    base = {"card": "K-1", "bytes": 120_000, "events": 100, "steps": 5, "finished": False, "column": "", "paused": False,
            "running": False, "held": False, "heldSteps": 0, "approved": False, "undecidedOutside": 0,
            "permission": 0, "question": 0}
    return {**base, **fields}


SHAPE = {"kind": "tide-prod-shape", "cards": [
    card_shape(finished=True, column="done", bytes=150_000), card_shape(finished=True, column="archived", bytes=90_000),
    card_shape(finished=True, column="done", bytes=200_000),
    card_shape(paused=True, undecidedOutside=2, permission=2, bytes=180_000),
    card_shape(paused=True, undecidedOutside=1, permission=1, bytes=110_000),
    card_shape(held=True, heldSteps=3, bytes=130_000), card_shape(approved=True, bytes=100_000),
    card_shape(undecidedOutside=1, permission=1, bytes=140_000), card_shape(question=1, bytes=95_000),
    card_shape(paused=True, approved=True, bytes=90_000),                  # wstrzymana bez próśb → plan w spoczynku
]}


class ShapeTest(unittest.TestCase):
    """Tryb `--shape` (K-125 PT-486): liczba kart, role i rozmiary dzienników z produkcji; dane nadal syntetyczne."""

    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="tide-perf-shape-test-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_roles_prompts_held_steps_and_sizes_come_from_the_shape(self):
        roles, requests, held, sizes = scale.shape_plan(SHAPE)
        self.assertEqual(roles, ["done", "archived", "done", "paused", "paused", "held", "approved", "outside",
                                 "question", "approved"])
        self.assertEqual(requests, {3: 2, 4: 1})
        self.assertEqual(held, {5: 3})
        self.assertEqual(sizes[:3], [150_000, 90_000, 200_000])

    def test_held_process_wins_over_finished_like_k38_on_production(self):
        """Na produkcji jedyna karta z trzymanym procesem jest też zakończona; rola `held` nie może zniknąć."""
        shape = {"kind": "tide-prod-shape", "cards": [card_shape(finished=True, column="done", held=True, heldSteps=2),
                                                      card_shape(finished=True, column="done")]}
        roles, _requests, held, _sizes = scale.shape_plan(shape)
        self.assertEqual((roles, held), (["held", "done"], {0: 2}))

    def test_load_shape_rejects_foreign_and_tiny_files(self):
        path = os.path.join(self.base, "shape.json")
        for data in ({"kind": "other", "cards": SHAPE["cards"]}, {"kind": "tide-prod-shape", "cards": []}):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            with self.assertRaises(ValueError):
                scale.load_shape(path)

    def test_populate_follows_the_shape_and_passes_every_check(self):
        with pbr.isolated_env(self.base):
            service = pbr.Service(self.base, len(SHAPE["cards"]), box_file=False)
            self.addCleanup(service.close)
            stats = scale.populate(service, self.base, 0.0, shape=SHAPE)
            from tide_daemon.cardflow import state
            from tide_daemon.cardflow.journal import Journal
            fresh = Journal(service.root)
            held = state.fold(fresh.read_complete(service.cards[5]))
            sizes = [os.path.getsize(fresh.mirror_path(card)) for card in service.cards]
        self.assertEqual(stats["problems"], [])
        self.assertEqual((stats["pausedDecisions"], stats["heldCards"], stats["approvedCards"]), (3, 1, 2))
        self.assertEqual(sum(1 for i in held.process_identities.values() if i.held and i.state == "ok"), 3)
        for index in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9):
            if index in (8,):                                                     # pytanie: kształt ma mało kroków
                continue
            wanted = SHAPE["cards"][index]["bytes"]
            self.assertGreater(sizes[index], 0.7 * wanted, (index, sizes[index], wanted))
            self.assertLess(sizes[index], 1.3 * wanted, (index, sizes[index], wanted))

    def test_file_system_shape_is_reproduced_and_the_checks_still_pass(self):
        """`fs` z kształtu: katalogi i pliki `flow/<karta>/steps` oraz rozmiar `cards/<karta>.md` jak na produkcji."""
        cards = [{**c, "card": f"K-{i + 1}"} for i, c in enumerate(SHAPE["cards"])]
        fs = {"flow": {c["card"]: {"files": 7 + i, "dirs": 3, "bytes": 0} for i, c in enumerate(cards)},
              "cardFiles": {c["card"]: 5_000 + 100 * i for i, c in enumerate(cards)}}
        shape = {**SHAPE, "cards": cards, "fs": fs}
        with pbr.isolated_env(self.base):
            service = pbr.Service(self.base, len(cards), box_file=False)
            self.addCleanup(service.close)
            stats = scale.populate(service, self.base, 0.0, shape=shape)
            root, names = service.root, list(service.cards)
            counts = []
            for name in names:
                steps = os.path.join(root, "flow", name, "steps")
                tree = [(top, files) for top, _dirs, files in os.walk(steps)]
                counts.append((sum(len(f) for _t, f in tree), len(os.listdir(steps))))
            sizes = [os.path.getsize(os.path.join(root, "cards", name + ".md")) for name in names]
        self.assertEqual(stats["problems"], [])
        self.assertEqual(counts, [(7 + i, 3) for i in range(len(cards))])
        self.assertEqual(stats["fsShape"]["files"], sum(7 + i for i in range(len(cards))))
        for i, size in enumerate(sizes):
            self.assertGreaterEqual(size, 5_000 + 100 * i)

    def test_shape_with_a_different_card_count_is_rejected(self):
        with pbr.isolated_env(self.base):
            service = pbr.Service(self.base, 5, box_file=False)
            self.addCleanup(service.close)
            with self.assertRaises(ValueError):
                scale.populate(service, self.base, 0.0, shape=SHAPE)


class ProdShapeTest(unittest.TestCase):
    """`perf_prod_shape.py`: tylko liczby i znaczniki, bez treści dzienników."""

    def setUp(self):
        self.board = tempfile.mkdtemp(prefix="tide-perf-board-test-")
        self.addCleanup(shutil.rmtree, self.board, ignore_errors=True)

    def write(self, number, role, **kwargs):
        log, marks, extra = scale.build_card(f"K-{number}", number, role, "alpha", "/ws/alpha", "/tmp/x", 0, 0, 105,
                                             **kwargs)
        events = scale.expand(log, marks, 0, 1.7e9, role)
        folder = os.path.join(self.board, "flow", f"K-{number}")
        os.makedirs(folder)
        with open(os.path.join(folder, "journal.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
        return events

    def test_collect_reports_roles_as_numbers_and_never_contents(self):
        from . import perf_prod_shape as prod
        self.write(1, "paused", requests=3)
        self.write(2, "held", held_steps=2)
        self.write(3, "approved")
        self.write(4, "done")
        self.write(5, "outside")
        os.makedirs(os.path.join(self.board, "flow", "K-6"))                # karta bez dziennika jest pomijana
        shape = prod.collect(self.board)
        by = {c["card"]: c for c in shape["cards"]}
        self.assertEqual([c["card"] for c in shape["cards"]], ["K-1", "K-2", "K-3", "K-4", "K-5"])
        self.assertTrue(by["K-1"]["paused"])
        self.assertEqual((by["K-1"]["undecidedOutside"], by["K-1"]["permission"]), (3, 3))
        self.assertEqual((by["K-2"]["held"], by["K-2"]["heldSteps"]), (True, 2))
        self.assertTrue(by["K-3"]["approved"] and not by["K-3"]["finished"])
        self.assertEqual((by["K-4"]["finished"], by["K-4"]["column"]), (True, "done"))
        self.assertEqual(by["K-5"]["permission"], 1)
        self.assertTrue(all(c["bytes"] > 0 and c["events"] > 0 and not c["running"] for c in shape["cards"]))
        text = json.dumps(shape, ensure_ascii=False)
        for secret in ("make test-changed", "run-integration", "Ticket PT-", "/ws/alpha", "kontener"):
            self.assertNotIn(secret, text)
        self.assertEqual(shape["totals"]["cards"], 5)
        self.assertEqual(shape["totals"]["undecidedOutside"], 4)             # 3 w K-1 i 1 w K-5

    def test_collect_counts_files_and_directories_without_reading_them(self):
        from . import perf_prod_shape as prod
        self.write(1, "done")
        self.write(2, "done")
        steps = os.path.join(self.board, "flow", "K-1", "steps", "s1", "out")
        os.makedirs(steps)
        for name in ("a", "b", "c"):
            with open(os.path.join(steps, name), "w", encoding="utf-8") as fh:
                fh.write("SECRET-CONTENT")
        os.makedirs(os.path.join(self.board, "cards"))
        with open(os.path.join(self.board, "cards", "K-1.md"), "w", encoding="utf-8") as fh:
            fh.write("x" * 1234)
        shape = prod.collect(self.board)
        self.assertEqual(shape["fs"]["flow"]["K-1"], {"files": 3, "dirs": 3, "bytes": 3 * len("SECRET-CONTENT")})
        self.assertEqual(shape["fs"]["flow"]["K-2"], {"files": 0, "dirs": 0, "bytes": 0})   # dziennik się nie liczy
        self.assertEqual(shape["fs"]["cardFiles"], {"K-1": 1234})
        self.assertEqual((shape["totals"]["flowFiles"], shape["totals"]["flowDirs"]), (3, 3))
        self.assertNotIn("SECRET-CONTENT", json.dumps(shape))

    def test_shape_file_round_trips_through_the_generator_plan(self):
        from . import perf_prod_shape as prod
        self.write(1, "paused", requests=2)
        self.write(2, "done")
        self.write(3, "done")
        self.write(4, "held", held_steps=3)
        out = os.path.join(self.board, "shape.json")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prod.main(["--board", self.board, "--out", out]), 0)
        roles, requests, held, _sizes = scale.shape_plan(scale.load_shape(out))
        self.assertEqual((roles, requests, held), (["paused", "done", "done", "held"], {0: 2}, {3: 3}))


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
