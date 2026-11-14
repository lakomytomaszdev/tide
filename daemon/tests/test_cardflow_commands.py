"""K-109 PT-478: polecenia poza piaskownicą w cardflow/commands.py — jawne wejścia, jeden obiekt stanu, bez silnika.

Testy nie importują `engine`: funkcje dostają dziennik, zegar, projekt i stan w parametrach. Struktura silnika
(cienkie wrappery `_outside*`) jest sprawdzana z AST pliku, bez jego importu.
"""
import ast
import dataclasses
import io
import os
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

from tide_daemon.cardflow import commands

CARDFLOW = Path(commands.__file__).resolve().parent
OUTSIDE_METHODS = (
    "_outside_remembered", "_outside_history", "_outside_ready", "_outside_collect", "_outside_stopped",
    "_outside_recheck", "_outside_recheck_rules", "_outside_busy", "_outside_heavy_state", "_outside_quiet_state",
    "_outside_heavy_of", "_outside_quiet_of", "_outside_class", "_outside_describe", "_outside_behind",
    "_outside_log", "_outside_exit", "_outside_still_allowed", "_outside_launch")


def ports(**given):
    """Porty z atrapami; nieużyty port kończy test błędem (funkcja nie sięga po niepodane wejście)."""
    def unused(field):
        def call(*args, **kwargs):
            raise AssertionError(f"nieoczekiwane wywołanie portu {field}")
        return call
    values = {f.name: unused(f.name) for f in dataclasses.fields(commands.Ports)}
    values["journal"] = None
    values.update(given)
    return commands.Ports(**values)


def project_of(slugs):
    return lambda card: (None, types.SimpleNamespace(slug=slugs[card], repo="", denied_commands=[]))


class ModuleBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse((CARDFLOW / "commands.py").read_text(encoding="utf-8"))

    def test_module_does_not_import_engine(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [alias.name for alias in node.names]
                self.assertNotIn("engine", [n.split(".")[-1] for n in names], ast.unparse(node))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name.split(".")[-1], "engine", ast.unparse(node))

    def test_module_has_no_module_level_state(self):
        # Na poziomie modułu tylko docstring, importy, klasy i funkcje: żadnych zmiennych (stan żyje w CommandsState).
        for node in self.tree.body:
            docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            self.assertTrue(docstring or isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)),
                            f"instrukcja modułu w linii {node.lineno}: {ast.unparse(node)[:80]}")
        self.assertNotIn("global", (CARDFLOW / "commands.py").read_text(encoding="utf-8").split())

    def test_engine_keeps_only_thin_wrappers_for_all_outside_methods(self):
        engine = ast.parse((CARDFLOW / "engine.py").read_text(encoding="utf-8"))
        flow = next(n for n in engine.body if isinstance(n, ast.ClassDef) and n.name == "FlowEngine")
        methods = {n.name: n for n in flow.body if isinstance(n, ast.FunctionDef) and n.name.startswith("_outside")}
        self.assertEqual(sorted(methods), sorted(OUTSIDE_METHODS))
        for name, method in methods.items():
            body = [s for s in method.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            self.assertEqual(len(body), 1, f"{name}: wrapper ma jedną instrukcję")
            call = body[0].value
            self.assertIsInstance(call, ast.Call, name)
            self.assertEqual(ast.unparse(call.func), "commands." + name.lstrip("_"), name)

    def test_engine_owns_one_state_instance_and_old_field_names_alias_it(self):
        class Owner:
            _outside_waiting = commands.StateAlias("waiting")
            _outside_active = commands.StateAlias("active")
        owner = Owner()
        owner._commands_state = state = commands.CommandsState()
        owner._outside_waiting = {"s": ("K-1", "p", 0.0, 0.0)}
        self.assertIs(owner._outside_waiting, state.waiting)
        self.assertIsNone(owner._outside_active)
        bare = Owner()      # silnik zbudowany przez __new__ (testy): pusty stan przy pierwszym dostępie
        self.assertEqual(bare._outside_waiting, {})
        self.assertIsInstance(bare.__dict__["_commands_state"], commands.CommandsState)


class QueueTest(unittest.TestCase):
    def behind(self, state, card, name, ts, *, busy=(), now=1000.0, slugs=None):
        slugs = slugs or {"K-1": "plasma-tide", "K-2": "plasma-tide", "K-3": "kamp"}
        p = ports(wall_clock=lambda: now, project=project_of(slugs), outside_busy=lambda c: c in busy,
                  outside_log=lambda n, t: None)
        return commands.outside_behind(p, state, card, name, {"ts": ts}, stale=300)

    def test_older_consent_goes_first_and_state_is_the_given_object(self):
        state = commands.CommandsState(seeded=True)
        self.assertFalse(self.behind(state, "K-1", "K-1-PT-1-1", "2026-11-05T15:00:00+01:00"))
        self.assertTrue(self.behind(state, "K-2", "K-2-PT-2-1", "2026-11-05T15:10:00+01:00"))
        self.assertEqual(sorted(state.waiting), ["K-1-PT-1-1", "K-2-PT-2-1"])
        state.waiting.pop("K-1-PT-1-1")
        self.assertFalse(self.behind(state, "K-2", "K-2-PT-2-1", "2026-11-05T15:10:00+01:00"))

    def test_stale_entry_leaves_the_queue_by_the_given_clock(self):
        state = commands.CommandsState(seeded=True)
        state.waiting["K-1-PT-1-1"] = ("K-1", "plasma-tide", 0.0, 0.0)
        self.assertFalse(self.behind(state, "K-2", "K-2-PT-2-1", "2026-11-05T15:10:00+01:00", now=301.0))
        self.assertNotIn("K-1-PT-1-1", state.waiting)

    def test_first_call_seeds_the_queue_once(self):
        state, seeded = commands.CommandsState(), []
        p = ports(wall_clock=lambda: 50.0, project=project_of({"K-1": "p"}), seed=seeded.append)
        commands.outside_behind(p, state, "K-1", "K-1-PT-1-1", {}, stale=300)
        commands.outside_behind(p, state, "K-1", "K-1-PT-1-1", {}, stale=300)
        self.assertEqual(seeded, [50.0])
        self.assertTrue(state.seeded)

    def test_busy_counts_project_and_total_limits_from_arguments(self):
        state = commands.CommandsState(active={"a": ("K-1", "u1"), "b": ("K-3", "u2")})
        p = ports(project=project_of({"K-1": "plasma-tide", "K-2": "plasma-tide", "K-3": "kamp"}),
                  outside_exit=lambda card, name: "/nonexistent/in/exit-code")
        self.assertTrue(commands.outside_busy(p, state, "K-2", total=7, per_project=1))
        self.assertFalse(commands.outside_busy(p, state, "K-2", total=7, per_project=2))
        self.assertTrue(commands.outside_busy(p, state, "K-2", total=2, per_project=6))

    def test_log_prints_only_a_changed_reason(self):
        state, out = commands.CommandsState(), io.StringIO()
        with redirect_stdout(out):
            commands.outside_log(state, "s", "miejsce zajęte")
            commands.outside_log(state, "s", "miejsce zajęte")
            commands.outside_log(state, "s", "start")
        self.assertEqual(out.getvalue().count("tide-daemon: kolejka poza piaskownicą: s:"), 2)
        self.assertEqual(state.logged, {"s": "start"})


class CollectTest(unittest.TestCase):
    def test_finished_run_returns_code_and_tail_and_releases_its_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            step_dir = os.path.join(tmp, "outside")
            os.makedirs(os.path.join(step_dir, "in"))
            with open(os.path.join(step_dir, "in", "exit-code"), "w", encoding="utf-8") as fh:
                fh.write("0\n")
            log = os.path.join(step_dir, "outside.log")
            with open(log, "w", encoding="utf-8") as fh:
                fh.write("x" * 50 + "koniec")
            state = commands.CommandsState(active={}, heavy={"s": True}, quiet={"s": False})
            p = ports(outside_busy=lambda card: False, wall_clock=lambda: 0.0,
                      outside_heavy_state=lambda: state.heavy, outside_quiet_state=lambda: state.quiet)
            started = {"unit": "tide-outside-s", "exitFile": os.path.join(step_dir, "in", "exit-code"), "log": log}
            with mock.patch.object(commands.stepstop, "_systemctl") as systemctl:
                code, tail = commands.outside_collect(p, state, "K-1", "s", started, timeout=3600, tail_size=6)
        self.assertEqual((code, tail), (0, "koniec"))
        self.assertEqual((state.active, state.heavy, state.quiet), ({}, {}, {}))
        systemctl.assert_called_once_with("stop", "tide-outside-s.scope")


class LaunchTest(unittest.TestCase):
    def test_launch_argv_uses_given_unit_runner_and_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit, log, exit_file, argv, env = commands.outside_launch(
                "K-1-PT-1-1", tmp, "", "make test", systemd_run="/x/systemd-run", cpu_weight=40, old_jobs=4)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "outside", "in")))
        self.assertEqual(unit, "tide-outside-K-1-PT-1-1")
        self.assertEqual(argv[:6], ["/x/systemd-run", "--user", "--scope", "--collect", "--quiet",
                                    "--unit=tide-outside-K-1-PT-1-1"])
        self.assertIn("CPUWeight=40", argv)
        self.assertEqual(argv[-3:], ["/bin/sh", "-c", "make test"])
        self.assertEqual((log, exit_file), (os.path.join(tmp, "outside", "outside.log"),
                                            os.path.join(tmp, "outside", "in", "exit-code")))
        self.assertEqual(env["QML_DISABLE_DISK_CACHE"], os.environ.get("QML_DISABLE_DISK_CACHE", "1"))


if __name__ == "__main__":
    unittest.main()
