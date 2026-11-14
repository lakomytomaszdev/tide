"""Testy skryptu kolejki koordynatora (K-105, PT-458): `build-data`, `measure`, wznawianie i strona `before`.

Nie uruchamiają usługi i nie mierzą czasu: wyniki są syntetyczne, a dane budowane w małej skali."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from . import perf_board_refresh as pbr
from . import perf_idle_cpu as perf
from . import perf_k105_queue as queue

GOOD_LOAD = "0.10 0.10 0.10 1/100 1"


def synthetic(percents=None, load=GOOD_LOAD, complete=True):
    percents = percents if percents is not None else [10.0] * 10
    windows = [{"index": i + 1, "cpuPercent": p, "wallS": 60.0} for i, p in enumerate(percents)]
    return {"cpuCount": 8, "loadavgBefore": load, "loadavgAfter": load, "sha": "abc", "boxEnabled": False,
            "cpuWindows": {"pid": 7, "expected": 10, "windows": windows, "complete": complete,
                           "invalidReason": None if complete else "limit czasu próby"}}


class Env(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="tide-k105-queue-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.data = os.path.join(self.root, "data")
        os.makedirs(self.data)
        with open(os.path.join(self.data, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"basePath": "/x"}, fh)
        self.directory = os.path.join(self.root, "measure")
        self.out = []


class BuildDataTest(Env):
    def fake_prepare(self, base, src, box, cards, journal_mb):
        self.builds = getattr(self, "builds", 0) + 1
        with open(os.path.join(base, "journal.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(f"{base}\n" * 100)
        return {"disk": {"cards": cards}}

    def build(self, data_dir, **kwargs):
        return queue.build_data(data_dir=data_dir, src=self.root, prepare=self.fake_prepare, out=self.out.append,
                                **kwargs)

    def test_builds_once_then_compatible_manifest_does_nothing(self):
        target = os.path.join(self.root, "built")
        built, manifest = self.build(target, cards=7, journal_mb=0.1)
        self.assertTrue(built)
        self.assertEqual(self.builds, 1)
        for key in ("params", "generatorSha256", "buildSeconds", "diskBytes", "builtAt", "repoHead"):
            self.assertIn(key, manifest)
        self.assertGreater(manifest["diskBytes"], 0)
        self.assertEqual(manifest["params"], {"cards": 7, "journalMb": 0.1, "seed": 105})
        again, _ = self.build(target, cards=7, journal_mb=0.1)
        self.assertFalse(again)
        self.assertEqual(self.builds, 1)
        self.assertTrue(any("nic do roboty" in line for line in self.out))

    def test_changed_parameters_rebuild(self):
        target = os.path.join(self.root, "built")
        self.build(target, cards=7, journal_mb=0.1)
        self.build(target, cards=8, journal_mb=0.1)
        self.assertEqual(self.builds, 2)

    def test_paths_are_relocated_to_the_final_directory(self):
        target = os.path.join(self.root, "built")
        _built, manifest = self.build(target, cards=4, journal_mb=0.1)
        with open(os.path.join(target, "base", "journal.jsonl"), encoding="utf-8") as fh:
            self.assertEqual(fh.readline().strip(), os.path.join(target, "base"))
        self.assertEqual(manifest["basePath"], os.path.join(target, "base"))

    def test_data_dir_is_inside_the_ignored_build_directory(self):
        self.assertTrue(queue.DATA_DIR.startswith(os.path.join(queue.REPO, "build") + os.sep))
        with open(os.path.join(queue.REPO, ".gitignore"), encoding="utf-8") as fh:
            self.assertIn("build/", fh.read().split())


class SmokeDataTest(Env):
    """Dym w małej skali: dane z `build-data` kopiowane do świeżego katalogu zachowują ścisły odczyt dzienników."""

    def test_small_scale_copy_is_strictly_readable_and_box_setting_follows_the_argument(self):
        target = os.path.join(self.root, "data-real")
        built, manifest = queue.build_data(cards=5, journal_mb=0.3, data_dir=target, out=self.out.append,
                                           box_file=False)          # piaskownica kroku nie ma przestrzeni nazw
        self.assertTrue(built)
        self.assertGreater(manifest["buildSeconds"], 0)
        self.assertEqual(manifest["scale"]["problems"], [])
        for box in (False, True):
            base = os.path.realpath(tempfile.mkdtemp(prefix="tide-k105-copy-"))
            self.addCleanup(shutil.rmtree, base, ignore_errors=True)
            perf.copy_data(target, base, box)
            self.assertNotEqual(base, manifest["basePath"])
            with open(os.path.join(base, "config", "tide", "cardflow.json"), encoding="utf-8") as fh:
                self.assertEqual(bool(json.load(fh).get("box", {}).get("enabled")), box)
            with pbr.isolated_env(base):
                from tide_daemon.cardflow.journal import Journal
                journal = Journal(os.path.join(base, "board"))
                cards = journal.cards_complete()
                self.assertEqual(len(cards), 5)
                for card in cards:
                    events = journal.read_complete(card)
                    self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
                    self.assertEqual(journal.check(card), "")
            self.assertTrue(os.path.isdir(perf.mirror_dir(base)))
        self.assertFalse(os.path.exists(target + ".tmp"))
        with open(os.path.join(target, "manifest.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["basePath"], os.path.join(target, "base"))


class ResumeTest(Env):
    def run_cli(self, argv, run_measure, repo=None):
        args = queue.build_parser().parse_args(argv)
        return queue.measure_command(args, repo=repo or self.root, data_dir=self.data, directory=self.directory,
                                     run_measure=run_measure, out=self.out.append)

    def runner(self, results):
        calls = []

        def run_measure(src, sha, box, sessions, warmup, window, **kwargs):
            calls.append({"src": src, "sha": sha, "box": box, **kwargs})
            partial = dict(synthetic()["cpuWindows"])
            partial["windows"] = partial["windows"][:2]
            kwargs["on_window"](partial)
            with open(queue.result_path("after", box, self.directory), encoding="utf-8") as fh:
                self.mid = json.load(fh)["attempts"][-1]
            return results[len(calls) - 1]
        run_measure.calls = calls
        return run_measure

    def document(self, box=False):
        with open(queue.result_path("after", box, self.directory), encoding="utf-8") as fh:
            return json.load(fh)

    def test_one_configuration_per_call_and_valid_result_is_skipped_on_rerun(self):
        run = self.runner([synthetic(load="9.00 9 9 1/2 3"), synthetic([10.0] * 9 + [31.0]), synthetic()])
        self.assertEqual(self.run_cli(["measure", "after", "false"], run), 2)           # nieważna: loadavg
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(self.run_cli(["measure", "after", "false"], run), 0)           # druga próba: ważna (okno > 30)
        self.assertEqual(len(run.calls), 2)
        document = self.document()
        self.assertEqual([a["status"] for a in document["attempts"]], ["invalid", "valid"])
        self.assertEqual(document["decisive"], 2)
        self.assertEqual(document["attempts"][1]["evaluation"]["verdict"], "fail")      # ważny wynik niespełniony
        self.assertEqual(self.run_cli(["measure", "after", "false"], run), 0)           # pominięta
        self.assertEqual(len(run.calls), 2)
        self.assertFalse(os.path.exists(queue.result_path("after", True, self.directory)))   # druga konfiguracja nietknięta

    def test_incremental_write_after_each_window(self):
        run = self.runner([synthetic()])
        self.run_cli(["measure", "after", "true"], run)
        self.assertEqual(self.mid["status"], "running")
        self.assertEqual(len(self.mid["windows"]), 2)
        self.assertTrue(run.calls[0]["box"])
        self.assertEqual(run.calls[0]["windows"], 10)

    def test_three_invalid_attempts_exhaust_the_configuration(self):
        bad = synthetic(load="9.00 9 9 1/2 3")
        run = self.runner([bad, bad, bad, synthetic()])
        codes = [self.run_cli(["measure", "after", "false"], run) for _ in range(4)]
        self.assertEqual(codes, [2, 2, 2, 1])
        self.assertEqual(len(run.calls), 3)                                             # czwartego wywołania nie ma
        self.assertIsNone(self.document()["decisive"])

    def test_killed_attempt_counts_as_invalid_and_run_continues(self):
        os.makedirs(self.directory)
        queue.write_json(queue.result_path("after", False, self.directory),
                         {"side": "after", "boxEnabled": False, "baseSha": "", "decisive": None,
                          "attempts": [{"n": 1, "status": "running", "windows": [{"index": 1}]}]})
        run = self.runner([synthetic()])
        self.assertEqual(self.run_cli(["measure", "after", "false"], run), 0)
        attempts = self.document()["attempts"]
        self.assertEqual([a["status"] for a in attempts], ["invalid", "valid"])
        self.assertIn("przerwana", attempts[0]["reason"])

    def test_three_killed_attempts_exhaust_the_configuration(self):
        os.makedirs(self.directory)
        running = {"status": "running", "windows": []}
        queue.write_json(queue.result_path("after", False, self.directory),
                         {"side": "after", "boxEnabled": False, "baseSha": "", "decisive": None,
                          "attempts": [{"n": 1, **running}, {"n": 2, **running}, {"n": 3, **running}]})
        run = self.runner([synthetic()])
        self.assertEqual(self.run_cli(["measure", "after", "false"], run), 1)
        self.assertEqual(run.calls, [])

    def test_limit_exceeded_attempt_is_invalid(self):
        run = self.runner([synthetic([10.0] * 3, complete=False)])
        self.assertEqual(self.run_cli(["measure", "after", "false"], run), 2)
        attempt = self.document()["attempts"][0]
        self.assertEqual(attempt["status"], "invalid")
        self.assertIn("limit czasu", attempt["reason"])

    def test_tool_exception_is_an_invalid_attempt_not_a_lost_file(self):
        def broken(*args, **kwargs):
            raise RuntimeError("usługa nie wystartowała")
        self.assertEqual(self.run_cli(["measure", "after", "false"], broken), 2)
        self.assertIn("RuntimeError", self.document()["attempts"][0]["reason"])

    def test_only_one_run_at_a_time(self):
        run = self.runner([synthetic()])
        with queue.Lock(os.path.join(self.directory, ".lock")):
            self.assertEqual(self.run_cli(["measure", "after", "false"], run), 3)
        self.assertEqual(run.calls, [])

    def test_missing_data_is_reported(self):
        os.remove(os.path.join(self.data, "manifest.json"))
        self.assertEqual(self.run_cli(["measure", "after", "false"], self.runner([synthetic()])), 4)

    def test_default_limit_is_thirteen_minutes(self):
        self.assertEqual(queue.build_parser().parse_args(["measure", "after", "true"]).limit_min, 13.0)

    def test_results_file_name_has_side_and_box(self):
        self.assertTrue(queue.result_path("before", False).endswith("build/k105-measure/before-box-false.json"))
        self.assertTrue(queue.result_path("after", True).endswith("build/k105-measure/after-box-true.json"))


class BeforeSideTest(Env):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "card")
        self.first = self.commit("v1")
        self.second = self.commit("v2")

    def git(self, *args):
        return subprocess.run(["git", "-C", self.repo, "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                               "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=True
                              ).stdout.strip()

    def commit(self, text):
        with open(os.path.join(self.repo, "code.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
        self.git("add", "code.txt")
        self.git("commit", "-q", "-m", text)
        return self.git("rev-parse", "HEAD")

    def test_before_code_comes_from_a_detached_worktree_that_is_removed_afterwards(self):
        head = self.git("symbolic-ref", "HEAD")
        with queue.BeforeCode(self.repo, self.first) as path:
            with open(os.path.join(path, "code.txt"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "v1")
            self.assertEqual(self.git("-C", path, "rev-parse", "HEAD"), self.first)
            self.assertEqual(self.git("-C", path, "branch", "--show-current"), "")            # odłączony HEAD
            self.assertEqual(len(self.git("worktree", "list").splitlines()), 2)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(len(self.git("worktree", "list").splitlines()), 1)
        self.assertEqual(self.git("symbolic-ref", "HEAD"), head)                              # gałąź bez zmian
        self.assertEqual(self.git("rev-parse", "HEAD"), self.second)
        self.assertEqual(self.git("branch", "--list").split(), ["*", "card"])

    def test_worktree_is_removed_even_when_the_measurement_fails(self):
        def broken(src, sha, box, *args, **kwargs):
            self.seen = src
            raise RuntimeError("boom")
        args = queue.build_parser().parse_args(["measure", "before", "true", "--base-sha", self.first])
        code = queue.measure_command(args, repo=self.repo, data_dir=self.data, directory=self.directory,
                                     run_measure=broken, out=self.out.append)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.seen))
        self.assertEqual(len(self.git("worktree", "list").splitlines()), 1)

    def test_before_runs_the_pinned_sha_and_after_runs_the_current_worktree(self):
        seen = {}

        def run_measure(src, sha, box, *args, **kwargs):
            with open(os.path.join(src, "code.txt"), encoding="utf-8") as fh:
                seen[sha or "after"] = (fh.read(), src)
            return synthetic()
        before = queue.build_parser().parse_args(["measure", "before", "false", "--base-sha", self.first[:10]])
        after = queue.build_parser().parse_args(["measure", "after", "false"])
        for args in (before, after):
            self.assertEqual(queue.measure_command(args, repo=self.repo, data_dir=self.data,
                                                   directory=self.directory, run_measure=run_measure,
                                                   out=self.out.append), 0)
        self.assertEqual(seen[self.first][0], "v1")
        self.assertEqual(seen["after"], ("v2", self.repo))

    def test_before_requires_an_existing_base_sha(self):
        args = queue.build_parser().parse_args(["measure", "before", "false"])
        code = queue.measure_command(args, repo=self.repo, data_dir=self.data, directory=self.directory,
                                     run_measure=lambda *a, **k: synthetic(), out=self.out.append)
        self.assertEqual(code, 4)
        bad = queue.build_parser().parse_args(["measure", "before", "false", "--base-sha", "0" * 40])
        self.assertEqual(queue.measure_command(bad, repo=self.repo, data_dir=self.data, directory=self.directory,
                                               run_measure=lambda *a, **k: synthetic(), out=self.out.append), 4)


if __name__ == "__main__":
    unittest.main()
