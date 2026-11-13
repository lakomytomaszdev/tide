"""PT-417: powolny silnik testów (tests/slowengine.py) — deterministyczne opóźnienia w atrapach i fiksturze."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from . import flowfakes, slowengine
from .test_cardflow import FlowInProcess

KEY = "domyslny:417:3"


def without_slow():
    env = {k: v for k, v in os.environ.items() if k != slowengine.ENV}
    return mock.patch.dict(os.environ, env, clear=True)


class SlowEngineTest(unittest.TestCase):
    def test_without_variable_fakes_are_bit_for_bit_the_constants(self):
        with without_slow(), tempfile.TemporaryDirectory() as tmp:
            paths = flowfakes.install(tmp)
            expected = {"claude": flowfakes.CLAUDE, "codex": flowfakes.CLAUDE, "systemd-run": flowfakes.SYSTEMD_RUN,
                        "systemctl": flowfakes.SYSTEMCTL, "konsole": flowfakes.KONSOLE}
            for name, body in expected.items():
                self.assertEqual(Path(paths[name]).read_text(encoding="utf-8"), body, name)
            self.assertFalse(os.path.exists(os.path.join(tmp, ".slow-counters")))
            self.assertEqual(slowengine.poll_ms("200"), "200")
            self.assertEqual(slowengine.sleep("fixture.tick"), 0.0)
            self.assertEqual(slowengine.preamble(["systemctl"], tmp), "")
            self.assertFalse(slowengine.active())

    def test_parse_rejects_bad_values_loudly(self):
        self.assertIsNone(slowengine.parse(""))
        for value in ("417", "domyslny:417", "domyslny:417:x", "nieznany:1:1", "domyslny::1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                slowengine.parse(value)
        slow = slowengine.parse(KEY)
        self.assertEqual((slow.profile, slow.seed, slow.run, slow.key), ("domyslny", "417", 3, KEY))

    def test_delays_depend_only_on_seed_run_point_and_call_number(self):
        one, same, other = slowengine.parse(KEY), slowengine.parse(KEY), slowengine.parse("domyslny:417:4")
        series = [one.delay("claude.result", n) for n in range(40)]
        self.assertEqual(series, [same.delay("claude.result", n) for n in range(40)])
        self.assertNotEqual(series, [other.delay("claude.result", n) for n in range(40)])
        self.assertTrue(any(series), "profil domyślny opóźnia część wywołań")
        self.assertTrue(all(0 <= d <= slowengine.PROFILES["domyslny"]["claude.result"][1] for d in series))
        self.assertNotEqual(series, [one.delay("claude.result", n, scope="test_b") for n in range(40)])

    def test_in_process_sleep_counts_calls_per_test_scope(self):
        slept = []
        with mock.patch.dict(os.environ, {slowengine.ENV: KEY}), \
                mock.patch.object(slowengine.time, "sleep", slept.append):
            slowengine.begin("test_a")
            first = [slowengine.sleep("fixture.observe") for _ in range(10)]
            slowengine.begin("test_a")
            again = [slowengine.sleep("fixture.observe") for _ in range(10)]
            self.assertEqual(slowengine.poll_ms("200"),
                             str(200 + int(slowengine.parse(KEY).delay("service.poll", 0) * 1000)))
        self.assertEqual(first, again)
        self.assertEqual(first, [slowengine.parse(KEY).delay("fixture.observe", n, "test_a") for n in range(10)])
        self.assertEqual(slept, [d for d in first + again if d])

    def test_fakes_with_variable_get_points_and_same_draws_as_the_engine(self):
        with mock.patch.dict(os.environ, {slowengine.ENV: KEY}), tempfile.TemporaryDirectory() as tmp:
            paths = flowfakes.install(tmp)
            for name in ("claude", "codex", "systemd-run", "systemctl"):
                body = Path(paths[name]).read_text(encoding="utf-8")
                self.assertTrue(body.startswith("#!/usr/bin/python3\n# PT-417"), name)
                compile(body, name, "exec")
            self.assertEqual(Path(paths["konsole"]).read_text(encoding="utf-8"), flowfakes.KONSOLE)
            self.assertIn("claude.result", Path(paths["claude"]).read_text(encoding="utf-8"))
            # Wklejony kod liczy wywołania w pliku i losuje tak samo jak silnik w procesie testu.
            code = slowengine.preamble(["systemctl"], os.path.join(tmp, "counters"))
            slept, space = [], {}
            exec(compile(code, "preamble", "exec"), space)
            space["_slow_time"] = mock.Mock(sleep=slept.append)
            for _ in range(12):
                space["_slow"]("systemctl")
            expected = [slowengine.parse(KEY).delay("systemctl", n) for n in range(12)]
            self.assertEqual(slept, [d for d in expected if d])
            self.assertEqual(Path(tmp, "counters", "systemctl").read_text(encoding="utf-8"), "x" * 12)

    def test_slow_fake_systemctl_still_answers_and_counts_calls(self):
        with mock.patch.dict(os.environ, {slowengine.ENV: "domyslny:1:1"}), tempfile.TemporaryDirectory() as tmp:
            systemctl = flowfakes.install(os.path.join(tmp, "bin"))["systemctl"]
            env = {**os.environ, "XDG_RUNTIME_DIR": tmp}
            for _ in range(3):
                done = subprocess.run([systemctl, "--user", "is-active", "--quiet", "brak.scope"], env=env,
                                      timeout=30)
                self.assertEqual(done.returncode, 3)
            self.assertEqual(Path(tmp, "bin", ".slow-counters", "systemctl").read_text(encoding="utf-8"), "xxx")

    def test_slow_fake_agent_delays_only_result_json_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {slowengine.ENV: KEY}):
                claude = flowfakes.install(os.path.join(tmp, "bin"))["claude"]
            out = Path(tmp, "out")
            work = Path(tmp, "work")
            work.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=work, check=True)
            env = {"HOME": tmp, "PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
            prompt = f"Kartka\nATRAPA: gotowe\nPlik wyniku: {out / 'result.json'}\n"
            done = subprocess.run([claude, "-p", "--session-id", "s1"], input=prompt, text=True, cwd=work, env=env,
                                  capture_output=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue((out / "result.json").is_file())
            counters = Path(tmp, "bin", ".slow-counters")
            self.assertEqual((counters / "claude.start").read_text(encoding="utf-8"), "x")
            self.assertEqual((counters / "claude.result").read_text(encoding="utf-8"), "x")


class SlowFixtureTest(FlowInProcess):
    """FlowInProcess z TIDE_TEST_SLOW: tick monitora owinięty opóźnieniem; bez zmiennej — dawny tick silnika."""

    def setUp(self):
        self.slow_value = KEY if self._testMethodName.endswith("_slow") else ""
        patcher = mock.patch.dict(os.environ, {slowengine.ENV: self.slow_value})
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def test_monitor_tick_is_wrapped_only_slow(self):
        self.assertEqual(self.flow.engine.tick.__name__, "slow_tick")
        self.assertTrue(Path(self.fakes["claude"]).read_text(encoding="utf-8").startswith("#!/usr/bin/python3\n# PT-417"))

    def test_monitor_tick_is_the_engine_method_without_variable(self):
        self.assertEqual(self.flow.engine.tick.__func__.__qualname__, "FlowEngine.tick")
        self.assertEqual(Path(self.fakes["claude"]).read_text(encoding="utf-8"), flowfakes.CLAUDE)

    def observe(self, delay):
        """wait_column z opóźnieniem `delay` punktu obserwacji: (punkty, liczba ticków silnika w czasie czekania)."""
        card = self.card()
        self.wait_column(card, "todo", timeout=5)       # poza pomiarem: pierwsze wywołanie punktu
        points, ticks = [], []
        real = slowengine.take

        def take(point):
            if not point.startswith("fixture.observe"):
                return real(point)      # tick/callback trwającego ticku monitora
            points.append(point)
            real(point)
            return delay

        with mock.patch.object(slowengine, "take", take), \
                mock.patch.object(self.flow.engine, "tick", lambda: ticks.append(1)):
            self.wait_column(card, "todo")
        return points, len(ticks)

    def test_wait_column_keeps_the_event_loop_running_while_the_test_lags_slow(self):
        points, ticks = self.observe(0.6)
        self.assertEqual(points, ["fixture.observe"])
        self.assertGreaterEqual(ticks, 2, "ticki monitora (co 150 ms) biegną, gdy wątek testu zostaje w tyle")

    def test_wait_column_does_not_wait_without_variable(self):
        points, _ = self.observe(0.0)
        self.assertEqual(points, ["fixture.observe"])
        self.assertEqual(slowengine.take("fixture.observe"), 0.0)


if __name__ == "__main__":
    unittest.main()
