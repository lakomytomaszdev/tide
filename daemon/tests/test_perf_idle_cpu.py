"""Dym reproduktora CPU na postoju K-98 #5 (`perf_idle_cpu.py`): podział raportu próbnika, dane sesji, atrapa
serwera embeddingów i pomiar w procesie (sonda, indeks bez zmian) na małym zestawie.

Nie mierzy czasu — sprawdza kształt wyniku i liczniki, na izolowanych danych."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request
from unittest import mock

from . import perf_idle_cpu as perf
from . import perf_idle_scale as scale

REPORT = """Próbki stosów: 6000 w 60 s (co 10 ms, czas ścienny — nie czas CPU; oczekiwanie wewnątrz kodu C może wyglądać jak praca)
Próbek poza ramkami rozpoznanymi jako czekanie: 1000

## Funkcje — czas własny (próbki)
    300  processes.py:probe

## Funkcje — czas łączny (próbki)
   1000  threading.py:run
    400  processes.py:probe
    150  engine.py:tick
     60  engine.py:_consent_recheck
     30  trialhold.py:inventory
     15  engine.py:_plan_next
     50  semantic.py:sync

## Najczęstsze stosy (wątek: ostatnie 12 ramek)
    400  [tide-process-probe] threading.py:run > processes.py:probe
"""


class ParseProfileTest(unittest.TestCase):
    def test_split_into_index_probe_tick_and_rest(self):
        split = perf.parse_profile(REPORT)
        self.assertEqual(split["busySamples"], 1000)
        self.assertEqual(split["samples"], {"index": 50, "probe": 400, "tick": 150, "rest": 400})
        self.assertEqual(split["share"]["probe"], 0.4)

    def test_missing_frames_count_as_zero(self):
        split = perf.parse_profile("Próbek poza ramkami rozpoznanymi jako czekanie: 10\n")
        self.assertEqual(split["samples"], {"index": 0, "probe": 0, "tick": 0, "rest": 10})
        self.assertEqual({k: (v["samples"], v["shareOfTick"]) for k, v in split["components"].items()},
                         {"outside": (0, None), "plan": (0, None), "inventory": (0, None)})

    def test_production_roads_are_components_of_tick_not_extra_categories(self):
        """`outside`, `plan`, `inventory`: próbki i udział względem próbek `engine.py:tick`; `rest` bez zmian."""
        split = perf.parse_profile(REPORT)
        components = split["components"]
        self.assertEqual(components["outside"],
                         {"frame": "engine.py:_consent_recheck", "samples": 60, "shareOfTick": 0.4})
        self.assertEqual(components["plan"], {"frame": "engine.py:_plan_next", "samples": 15, "shareOfTick": 0.1})
        self.assertEqual(components["inventory"],
                         {"frame": "trialhold.py:inventory", "samples": 30, "shareOfTick": 0.2})
        self.assertEqual(split["samples"], {"index": 50, "probe": 400, "tick": 150, "rest": 400},
                         "ramki składowe nie zmieniają podziału ani reszty")
        self.assertEqual(split["share"]["tick"], 0.15)
        self.assertEqual(sorted(split["samples"]), ["index", "probe", "rest", "tick"])

    def test_work_samples_exclude_idle_pool_and_event_loop_frames(self):
        """K-125: bezczynne wątki puli i pętla zdarzeń nie wchodzą do próbek pracy; `share` zostaje bez zmian."""
        text = ("Próbek poza ramkami rozpoznanymi jako czekanie: 23208\n\n## Funkcje — czas własny (próbki)\n"
                "  16475  thread.py:_worker\n   5785  app.py:main\n    482  <frozen posixpath>:_joinrealpath\n\n"
                "## Funkcje — czas łączny (próbki)\n    915  engine.py:tick\n    417  engine.py:_consent_recheck\n")
        split = perf.parse_profile(text)
        self.assertEqual(split["work"], {"idleFrames": ["thread.py:_worker", "app.py:main"], "idleSamples": 22260,
                                         "samples": 948, "tickShare": round(915 / 948, 4)})
        self.assertEqual(split["share"]["tick"], round(915 / 23208, 4))
        self.assertEqual(perf.parse_profile(REPORT)["work"]["samples"], 1000)      # bez bezczynnych ramek: bez zmian

    def test_cpu_window_is_not_changed_by_the_profile_categories(self):
        """Miara CPU (okno bez próbnika) nie zależy od kategorii profilu: `cpu_window` nie zna ich w ogóle."""
        import inspect
        self.assertNotIn("COMPONENTS", inspect.getsource(perf.cpu_window))
        self.assertEqual([key for key, _ in perf.COMPONENTS], ["outside", "plan", "inventory"])


class FakeSystemctlTest(unittest.TestCase):
    """Atrapa `systemctl` reproduktora (K-125 PT-486): deterministyczny stan jednostek w `isolated_env`."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="tide-perf-idle-test-")
        self.addCleanup(shutil.rmtree, self.base)
        self.env = perf.isolated_env(self.base)

    def run_fake(self, *args):
        return subprocess.run(["systemctl", "--user", *args], env=self.env, capture_output=True, text=True)

    def test_fake_is_first_in_path_next_to_the_incus_fake(self):
        self.assertEqual(os.path.dirname(self.env["TIDE_INCUS_BIN"]), os.path.join(self.base, "bin"))
        self.assertEqual(self.env["PATH"].split(os.pathsep)[0], os.path.join(self.base, "bin"))
        self.assertTrue(os.access(os.path.join(self.base, "bin", "systemctl"), os.X_OK))
        with open(os.path.join(self.base, "bin", "systemctl"), encoding="utf-8") as fh:
            self.assertLess(len(fh.read().splitlines()), 12, "krótki skrypt /bin/sh")

    def test_held_unit_is_active_with_exit_zero_and_other_subcommands_succeed_silently(self):
        held = f"tide-step-k-5{scale.HELD_UNIT_FRAGMENT}ticket-1.scope"
        run = self.run_fake("is-active", "--quiet", held)
        self.assertEqual((run.returncode, run.stdout.strip()), (0, "active"))
        for args in (("freeze", held), ("thaw", held), ("stop", held), ("show", held)):
            run = self.run_fake(*args)
            self.assertEqual((run.returncode, run.stdout), (0, ""), args)

    def test_every_held_trial_ticket_has_an_active_unit(self):
        """Karta z kilkoma trzymanymi krokami (tryb `--shape`): wszystkie tickety prób K-38 są `active`."""
        for ticket in scale.HELD_TICKETS:
            run = self.run_fake("is-active", "--quiet", f"tide-step-k-7-{ticket.lower()}-ticket-1.scope")
            self.assertEqual((run.returncode, run.stdout.strip()), (0, "active"), ticket)
        self.assertEqual(len(set(scale.HELD_TICKETS)), 7)

    def test_other_units_are_inactive(self):
        run = self.run_fake("is-active", "tide-step-k-5-pt-1011-ticket-1.scope")
        self.assertEqual((run.returncode, run.stdout.strip()), (3, "inactive"))

    def test_every_call_is_logged_and_counted(self):
        self.assertEqual(perf.systemctl_calls(self.base), 0)
        self.run_fake("is-active", "--quiet", "x.scope")
        self.run_fake("freeze", "y.scope")
        self.assertEqual(perf.systemctl_calls(self.base), 2)
        with open(perf.systemctl_log(self.base), encoding="utf-8") as fh:
            self.assertEqual(fh.read().splitlines(), ["--user is-active --quiet x.scope", "--user freeze y.scope"])

    def test_engine_unit_state_gives_active_for_the_held_process_in_the_isolated_env(self):
        from tide_daemon.tasks import stepstop
        unit = f"tide-step-k-5{scale.HELD_UNIT_FRAGMENT}ticket-1"
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"],
                                          "TIDE_FAKE_SYSTEMCTL_LOG": self.env["TIDE_FAKE_SYSTEMCTL_LOG"]}):
            self.assertEqual(stepstop.unit_state(unit), "active")
            self.assertEqual(stepstop.unit_state("tide-step-k-5-pt-1011-ticket-1"), "inactive")
        self.assertEqual(perf.systemctl_calls(self.base), 2)

    def test_stats_report_calls_in_the_cpu_window_per_tick(self):
        stats = perf.systemctl_stats(500, 100, 220, 600.0)
        self.assertEqual((stats["callsTotal"], stats["callsInCpuWindow"], stats["ticksInCpuWindow"]), (500, 120, 120.0))
        self.assertEqual(stats["callsPerTick"], 1.0)
        self.assertEqual(stats["tickPeriodS"], 5.0)
        none = perf.systemctl_stats(5, None, None, None)
        self.assertEqual((none["callsInCpuWindow"], none["callsPerTick"]), (None, None))

    def test_summary_names_components_and_calls(self):
        data = {"profileWindow": {"split": perf.parse_profile(REPORT)},
                "fakeSystemctl": perf.systemctl_stats(10, 0, 10, 60.0)}
        text = perf.components_text(data)
        self.assertIn("outside 40.0 % ticku", text)
        self.assertIn("plan 10.0 % ticku", text)
        self.assertIn("inventory 20.0 % ticku", text)
        self.assertIn("systemctl 10 wywołań", text)
        self.assertEqual(perf.components_text({}), "")


class DataTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="tide-perf-idle-test-")
        self.addCleanup(shutil.rmtree, self.base)

    def test_sessions_spread_over_the_window(self):
        now = time.time()
        claude = perf.write_sessions(self.base, 12, now)
        files = [os.path.join(root, f) for root, _d, names in os.walk(claude) for f in names]
        self.assertEqual(len(files), 12)
        ages = sorted(now - os.path.getmtime(f) for f in files)
        self.assertLess(ages[0], 86400)
        self.assertLess(ages[-1], 30 * 86400)
        self.assertGreater(ages[-1], 86400)

    def test_fake_embed_server_answers_api_embed(self):
        server = perf.EmbedServer()
        self.addCleanup(server.close)
        request = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/embed", method="POST",
                                         data=json.dumps({"model": "bge-m3", "input": ["a", "b"]}).encode())
        with urllib.request.urlopen(request, timeout=5) as reply:
            data = json.loads(reply.read())
        self.assertEqual(len(data["embeddings"]), 2)
        self.assertEqual(len(data["embeddings"][0]), perf.DIMS)
        self.assertEqual((server.calls, server.texts), (1, 2))

    def test_micro_reports_probe_and_idle_index_pass(self):
        claude = perf.write_sessions(self.base, 6, time.time())
        os.makedirs(os.path.join(self.base, "codex", "sessions"))
        server = perf.EmbedServer()
        self.addCleanup(server.close)
        perf.write_models(os.path.join(self.base, "models.json"), server.port)
        result = perf.micro(self.base, perf.DEFAULT_SRC, claude, server.port)
        self.assertNotIn("error", result, result.get("error"))
        self.assertEqual(len(result["probe"]["cpuMs"]), 20)
        index = result["index"]
        self.assertEqual(index["sessions"], 6)
        self.assertEqual(index["state"]["state"], "ready", index["state"])
        self.assertEqual(server.texts, 6, "pierwszy przebieg liczy wszystko, kolejne nic")
        self.assertEqual(index["idlePassBytesWritten"], [0] * 5, "przebieg bez zmian nie zapisuje pliku")


if __name__ == "__main__":
    unittest.main()
