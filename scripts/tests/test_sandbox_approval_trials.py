"""Skrypty prób rzeczywistej piaskownicy PT-286 (scripts/sandbox/approval_trials.py, approval_probe.py): ocena
wyników prób i odmowa startu z wnętrza kroku. Samych piaskownic ani modeli test nie uruchamia — to robi skrypt
poza piaskownicą (notka PT-286)."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SANDBOX = ROOT / "scripts" / "sandbox"


def load(name):
    spec = importlib.util.spec_from_file_location(f"pt286_{name}", SANDBOX / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load("approval_probe")
trials = load("approval_trials")


class ProbeTest(unittest.TestCase):
    def test_upsert_reply_classification(self):
        for code, out, want in ((1, "Failed to open connection", "denied"),
                                (0, '{"ok": false, "error": "forbidden", "message": "x"}', "refused"),
                                (0, '{"ok": true, "slug": "a", "testsApproved": true}', "succeeded"),
                                (0, '{"ok": false, "error": "bad-request", "message": "x"}', "reached"),
                                (0, "coś innego", "error")):
            with self.subTest(out=out):
                self.assertEqual(probe.classify_upsert(code, out)[0], want)

    def test_file_attempt_outcomes(self):
        results = []
        probe.fs_attempt(results, "ok", lambda: "zapisano")
        probe.fs_attempt(results, "perm", lambda: (_ for _ in ()).throw(PermissionError(13, "x")))
        probe.fs_attempt(results, "rofs", lambda: (_ for _ in ()).throw(OSError(30, "x")))
        probe.fs_attempt(results, "hidden", lambda: (_ for _ in ()).throw(FileNotFoundError(2, "x")))
        probe.fs_attempt(results, "skip", lambda: (_ for _ in ()).throw(probe.Skip("brak")))
        probe.fs_attempt(results, "odd", lambda: 1 / 0)
        self.assertEqual([r["outcome"] for r in results],
                         ["succeeded", "denied", "denied", "denied", "skipped", "error"])
        self.assertIn("niewidoczne", results[3]["detail"])

    def test_upsert_argv_uses_the_given_bus_or_the_environment(self):
        self.assertIn("--session", probe.upsert_argv(None, {"slug": "a"}))
        argv = probe.upsert_argv("unix:path=/tmp/x/bus", {"slug": "a", "tests": {"full": "make"}})
        self.assertIn("--bus=unix:path=/tmp/x/bus", argv)
        self.assertEqual(argv[-1], 'string:{"slug": "a", "tests": {"full": "make"}}')


class TrialsTest(unittest.TestCase):
    def test_every_attempt_must_be_denied_or_refused(self):
        good = [{"name": "a1 x", "outcome": "denied"}, {"name": "b2 x", "outcome": "refused"},
                {"name": "a7 x", "outcome": "skipped"}, {"name": "b4 x", "outcome": "reached"}]
        self.assertTrue(trials.attempts_ok(good))
        self.assertFalse(trials.attempts_ok([]))
        for bad in ({"name": "a1 x", "outcome": "succeeded"}, {"name": "b2 x", "outcome": "reached"},
                    {"name": "a1 x", "outcome": "skipped"}, {"name": "c2 x", "outcome": "error"}):
            with self.subTest(bad=bad):
                self.assertFalse(trials.attempts_ok([*good, bad]))

    def test_report_lists_every_attempt(self):
        text = trials.markdown({"ok": False, "head": "abc", "run": "/r", "steps": [
            {"tool": "codex", "model": "m", "unit": "tide-step-x", "exitCode": 0, "seconds": 1,
             "probe": {"cgroup": "0::/x", "results": [{"name": "a1 nadpisanie", "outcome": "denied",
                                                       "detail": "PermissionError | x"}]}},
            {"tool": "claude", "probeError": "brak wyniku prób"}]})
        self.assertIn("NIEUDANE", text)
        self.assertIn("| a1 nadpisanie | denied | PermissionError \\| x |", text)
        self.assertIn("brak wyniku prób", text)

    def test_codex_program_is_chosen_like_the_service_does(self):
        from unittest import mock
        snap, desktop = trials.codex_homes.SNAP_CODEX, trials.codex_homes.DESKTOP_CODEX
        self.assertEqual(trials.binary("codex", "/x/codex"), "/x/codex")
        with mock.patch.object(trials.os.path, "isfile", lambda p: p == desktop), \
                mock.patch.object(trials.os, "access", lambda p, m: p == desktop):
            self.assertEqual(trials.binary("codex", "", which=lambda n: snap), desktop)
            self.assertEqual(trials.binary("codex", "", which=lambda n: "/usr/local/bin/codex"), "/usr/local/bin/codex")
        with mock.patch.object(trials.os.path, "isfile", lambda p: False):
            self.assertEqual(trials.binary("codex", "", which=lambda n: snap), snap)

    def test_codex_retries_with_the_broken_mcp_server_skipped_like_the_service(self):
        from unittest import mock
        broken = "Error loading config.toml: invalid transport\nin `mcp_servers.codex_app`\n"
        seen = []

        def once(tool, exe, model, repo, wt, cfg, timeout, skip, attempt):
            seen.append(tuple(skip))
            return {"unit": f"u{attempt}", "exitCode": 1, "logTail": broken} if "codex_app" not in skip else \
                {"unit": f"u{attempt}", "exitCode": 0, "probe": {"results": []}}

        with mock.patch.object(trials, "_run_step_once", once):
            entry = trials.run_step("codex", "/c", "", None, None, None, {}, 1, skip=("x",))
        self.assertEqual(seen, [("x",), ("x", "codex_app")])
        self.assertEqual((entry["unit"], entry["codexMcpSkip"]), ("u1", ["x", "codex_app"]))
        self.assertEqual(entry["earlierAttempts"][0]["unit"], "u0")
        seen.clear()
        with mock.patch.object(trials, "_run_step_once", once):
            trials.run_step("claude", "/c", "", None, None, None, {}, 1)   # Claude: bez ponowienia
        self.assertEqual(seen, [()])

    def test_refuses_to_run_inside_a_step(self):
        with tempfile.TemporaryDirectory() as out:
            run = subprocess.run(["/usr/bin/python3", str(SANDBOX / "approval_trials.py"), out, "--skip-tests"],
                                 capture_output=True, text=True, timeout=60,
                                 env=dict(os.environ, CLAUDE_CODE_TMPDIR="/tmp/x"))
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertIn("poza krokiem", run.stderr)
            self.assertEqual(os.listdir(out), [])


if __name__ == "__main__":
    unittest.main()
