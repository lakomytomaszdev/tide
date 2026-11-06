"""PT-258: rzeczywiste pliki fixture, kierunek zależności i bezpieczne wyjścia selektora."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import test_runner as runner
from tools import test_changed_benchmark as benchmark
from tools.test_dependencies import ImportGraph
from tools.test_suites import SERVICE_CONTRACT_MODULES, SUITES


class DependencySelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=runner.ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.entries = [("daemon/tests", "test*.py")]
        self.write("daemon/tests/test_contract.py", "# atrapa procesu bez importu kodu\n")
        patcher = patch.object(runner, "SERVICE_CONTRACT_MODULES", ("daemon/tests/test_contract.py",))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, path, body=""):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body, encoding="utf-8")
        return path

    def source(self, name, body=""):
        return self.write("daemon/tide_daemon/" + name + ".py", body)

    def write_test(self, name, body=""):
        return self.write("daemon/tests/test_" + name + ".py", body)

    def select(self, *paths):
        return runner.changed_modules(paths, self.entries, self.root)

    def test_indirect_relative_alias_nested_and_star_imports_and_unrelated_test(self):
        leaf = self.source("cardflow/leaf", 'raise RuntimeError("analiza nie może wykonać kodu")\n')
        self.source("cardflow/middle", "from . import leaf as renamed\n")
        self.source("cardflow/top", "from .middle import *\n")
        direct = self.write_test("direct", "import tide_daemon.cardflow.leaf as l\n")
        indirect = self.write_test("indirect", "def f():\n    from tide_daemon.cardflow.top import value\n")
        helper = self.write("daemon/tests/helper.py", "from tide_daemon.cardflow import leaf\n")
        through_helper = self.write_test("helper", "from .helper import thing\n")
        unrelated = self.write_test("unrelated", "import json\n")
        selected = self.select(leaf)
        self.assertEqual(set(selected.modules), {direct, indirect, through_helper,
                                                 "daemon/tests/test_contract.py"})
        self.assertFalse(selected.full)
        self.assertNotIn(unrelated, selected.modules)
        self.assertIn(helper, selected.reasons[through_helper][0])
        self.assertIn("top.py", selected.reasons[indirect][0])

    def test_dependency_direction_does_not_select_tests_of_dependencies(self):
        self.source("leaf")
        top = self.source("top", "from . import leaf\n")
        leaf_test = self.write_test("leaf", "from tide_daemon import leaf\n")
        top_test = self.write_test("top", "from tide_daemon import top\n")
        self.assertIn(top_test, self.select(top).modules)
        self.assertNotIn(leaf_test, self.select(top).modules)

    def test_cycle_and_chain_beyond_former_depth_limit_preserve_all_tests(self):
        leaf = self.source("leaf", "from . import middle\n")
        self.source("middle", "from . import leaf\n")
        self.source("top", "from . import middle\n")
        self.source("fourth", "from . import top\n")
        self.source("fifth", "from . import fourth\n")
        direct = self.write_test("direct", "from tide_daemon import leaf\n")
        indirect = self.write_test("indirect", "from tide_daemon import fifth\n")
        graph = ImportGraph(self.root, ["daemon/tests"])
        tests = set(runner.modules_for(self.entries, self.root))
        affected = graph.affected(leaf, tests)
        self.assertEqual(set(affected), {direct, indirect})
        self.assertEqual(len(affected[indirect]), 6)

    def test_intermediate_aggregator_preserves_all_integration_tests(self):
        leaf = self.source("leaf")
        imports = []
        for i in range(13):
            self.source(f"module{i}")
            imports.append(f"from . import module{i}")
        hub = self.source("engine", "from . import leaf\n" + "\n".join(imports))
        direct = self.write_test("leaf", "from tide_daemon import leaf\n")
        integration = self.write_test("integration", "from tide_daemon.engine import Engine\n")
        self.assertIn(direct, self.select(leaf).modules)
        self.assertIn(integration, self.select(leaf).modules)
        self.assertIn(integration, self.select(hub).modules)

    def test_changed_shared_source_and_intermediate_hub_preserve_indirect_tests(self):
        leaf = self.source("leaf")
        source = self.source("state", "from . import leaf\n")
        self.source("middle", "from . import state\n")
        direct = self.write_test("direct", "from tide_daemon import state\n")
        indirect = self.write_test("indirect", "from tide_daemon import middle\n")
        for i in range(21):
            self.source(f"consumer{i}", "from . import state\n")
        selected = self.select(source)
        self.assertIn(direct, selected.modules)
        self.assertIn(indirect, selected.modules)
        self.assertIn("daemon/tests/test_contract.py", selected.modules)
        self.assertTrue({direct, indirect} <= set(self.select(leaf).modules))

    def test_constant_dynamic_import_and_service_alias_resolve_to_same_file(self):
        source = self.source("leaf")
        first = self.write_test("first", 'import importlib\nimportlib.import_module("tide_daemon.leaf")\n')
        second = self.write_test("second", "from daemon.tide_daemon import leaf\n")
        self.assertTrue({first, second} <= set(self.select(source).modules))

    def test_dynamic_alias_and_variable_imports_do_not_disappear_beside_static_test(self):
        imports = (
            'from importlib import import_module as load\nload("tide_daemon.leaf")\n',
            'import importlib as loader\nloader.import_module("tide_daemon.leaf")\n',
            'import importlib\nname = "tide_daemon.leaf"\nimportlib.import_module(name)\n',
            'from importlib import import_module as load\nname = "tide_daemon.leaf"\nload(name)\n',
            'from builtins import __import__ as load\nload("tide_daemon.leaf")\n',
            'import builtins as loader\nloader.__import__(name)\n',
            '__import__(name)\n',
            'from importlib import import_module\nimport_module(name="tide_daemon.leaf")\n',
            'import importlib\nimportlib.import_module(name=module_name)\n',
            'import importlib\nimportlib.import_module(".leaf", "tide_daemon")\n',
            '__import__("tide_daemon", fromlist=["leaf"])\n',
            'def first():\n    import importlib as loader\n    loader.import_module("tide_daemon.leaf")\n'
            'def second():\n    import builtins as loader\n    loader.__import__("json")\n',
        )
        source = self.source("leaf")
        direct = self.write_test("direct", "from tide_daemon import leaf\n")
        unrelated = self.write_test("unrelated", "import json\n")
        for body in imports:
            with self.subTest(body=body):
                dynamic = self.write_test("dynamic", body)
                selection = self.select(source)
                self.assertTrue({direct, dynamic} <= set(selection.modules))
                self.assertNotIn(unrelated, selection.modules)

    def test_uncertain_dynamic_helper_selects_indirect_consumers_and_explains_uncertainty(self):
        source = self.source("leaf")
        direct = self.write_test("direct", "from tide_daemon import leaf\n")
        helper = self.source("loader", "from importlib import import_module as load\nload(name)\n")
        self.source("middle", "from . import loader\n")
        indirect = self.write_test("indirect", "from tide_daemon import middle\n")
        selection = self.select(source)
        self.assertTrue({direct, indirect} <= set(selection.modules))
        self.assertIn("potencjalny import dynamiczny", selection.reasons[indirect][0])
        self.assertIn(helper + ":2", selection.reasons[indirect][0])

    def test_uncertain_import_does_not_replace_suite_fallback_for_uncovered_source(self):
        source = self.source("uncovered")
        dynamic = self.write_test("dynamic", "__import__(name)\n")
        other = self.write_test("other")
        selection = self.select(source)
        self.assertTrue({dynamic, other} <= set(selection.modules))
        self.assertIn("brak rozpoznanych importów", selection.reasons[other][0])

    def test_unknown_tools_make_shared_fixtures_deletion_and_parse_failure_are_full(self):
        source = self.source("leaf")
        self.write_test("leaf", "from tide_daemon import leaf\n")
        helper = self.write("daemon/tests/helper.py")
        shared = self.write_test("shared")
        self.write_test("consumer", "from .test_shared import Fixture\n")
        for path in ("unknown.py", "Makefile", "mk/tests.mk", "tools/selector.py",
                     "daemon/tests/fixtures/data.json", helper, shared,
                     "daemon/tests/__init__.py", "daemon/tide_daemon/deleted.py"):
            with self.subTest(path=path):
                selection = self.select(source, path)
                self.assertTrue(selection.full)
                self.assertEqual(selection.modules, runner.modules_for(self.entries, self.root))
                self.assertIn(path, next(iter(selection.reasons.values()))[0])
        self.source("broken", "def ?\n")
        self.assertTrue(self.select(source).full)

    def test_no_changes_no_tests_and_no_ast_needed(self):
        with patch.object(runner, "ImportGraph", side_effect=AssertionError("niepotrzebna analiza")):
            self.assertEqual(self.select().modules, [])

    def test_documentation_only_in_root_docs_and_component_selects_zero_without_ast(self):
        # Także usunięty dokument: nie wymaga odczytu ani nie ma wpływu na kod.
        paths = ("README.md", "docs/testing.md", "daemon/tide_daemon/cardflow/README.md")
        with patch.object(runner, "ImportGraph", side_effect=AssertionError("niepotrzebna analiza")):
            for path in paths:
                with self.subTest(path=path):
                    selection = self.select(path)
                    self.assertEqual(selection.modules, [])
                    self.assertFalse(selection.full)
            self.assertEqual(self.select(*paths).modules, [])

    def test_documentation_mixed_with_code_keeps_code_selection_and_safe_fallbacks(self):
        source = self.source("leaf")
        self.write_test("leaf", "from tide_daemon import leaf\n")
        self.write_test("other")
        expected = self.select(source)
        self.assertEqual(self.select("README.md", "docs/testing.md", source), expected)
        for path in ("tools/README.md", "mk/README.md", "Makefile",
                     "daemon/tests/fixtures/instructions.md", "daemon/tests/README.md",
                     "docs/example.py", "docs/settings.json", "unknown.py"):
            with self.subTest(path=path):
                self.assertTrue(self.select("docs/testing.md", path).full)

    def test_changed_test_selects_itself_and_contract_and_uncovered_source_uses_old_suite(self):
        changed = self.write_test("changed")
        self.assertEqual(set(self.select(changed).modules),
                         {changed, "daemon/tests/test_contract.py"})
        source = self.source("uncovered")
        selected = self.select(source)
        self.assertEqual(selected.modules, runner.modules_for(self.entries, self.root))
        self.assertIn("brak rozpoznanych importów", selected.reasons[changed][0])

    def test_multiple_changes_union_reasons_and_cli_explains_selection_in_list_mode(self):
        first = self.source("first")
        second = self.source("second")
        test = self.write_test("both", "from tide_daemon import first, second\n")
        self.assertEqual(len(self.select(first, second).reasons[test]), 2)
        output = io.StringIO()
        with patch.object(runner, "git_paths", return_value=[first, second]), \
                patch("sys.argv", ["runner", "--dirs", "daemon/tests", "--changed", "--list"]), \
                patch.object(runner, "execute", side_effect=AssertionError("tylko lista")), \
                redirect_stdout(output):
            self.assertEqual(runner.main(self.root), 0)
        for text in ("Wybrane moduły: 2", first, second, "import AST", "kontrakt procesu", test):
            self.assertIn(text, output.getvalue())

    def test_missing_contract_module_is_an_error(self):
        source = self.source("leaf")
        self.write_test("leaf", "from tide_daemon import leaf\n")
        with patch.object(runner, "SERVICE_CONTRACT_MODULES", ("missing.py",)):
            with self.assertRaisesRegex(ValueError, "kontraktowego"):
                self.select(source)


class RepositorySelectionTest(unittest.TestCase):
    def test_cardflow_selection_preserves_known_consumers_and_contract(self):
        entries = [(d, "test*.py") for d in sorted({d for pairs in SUITES.values() for d, _ in pairs})]
        whole = runner.modules_for(SUITES["cardflow"])
        all_modules = runner.modules_for(entries)
        for source, direct in (("basesuggestions", "test_base_suggestions"),
                               ("hostingcli", "test_hostingcli_real"),
                               ("engine", "test_cardflow_caretaker_settings"),
                               ("state", "test_cardflow_base")):
            with self.subTest(source=source):
                selection = runner.changed_modules([f"daemon/tide_daemon/cardflow/{source}.py"], entries)
                self.assertFalse(selection.full)
                self.assertIn(f"daemon/tests/{direct}.py", selection.modules)
                self.assertTrue(set(SERVICE_CONTRACT_MODULES) <= set(selection.modules))
                self.assertLess(len(selection.modules), len(all_modules))
                print(f"[PT-258] {source}.py: {len(selection.modules)} modułów; cały cardflow: {len(whole)}")


class SelectionBenchmarkTest(unittest.TestCase):
    def test_public_verification_and_five_commits_share_identical_real_measurements_on_resume(self):
        self.check_verification(0)

    def test_failed_verification_keeps_failed_status_but_still_produces_five_commit_report(self):
        self.check_verification(1)

    def check_verification(self, returncode):
        with tempfile.TemporaryDirectory(dir=runner.ROOT) as temp:
            output = Path(temp)
            modules = ["daemon/tests/test_a.py", "daemon/tests/test_b.py"]
            results = [dict(module=m, tests=1, returncode=0, seconds=1) for m in modules]

            def verification(command, **kwargs):
                parent = Path(kwargs["env"]["TEST_OUTPUT"]) / "run-1"
                parent.mkdir()
                (parent / "summary.json").write_text(json.dumps({"seconds": 12, "results": results}))
                self.assertEqual(command, ["make", "test-changed", "BASE=pinned-base"])
                return subprocess.CompletedProcess(command, returncode)

            def git(*args):
                if args[0] == "rev-parse":
                    return "tested-tree\n" if args[1] == "HEAD^{tree}" else "tested-head\n"
                if args[0] == "log":
                    return "\n".join(f"commit{i}" for i in range(5))
                if args[0] == "show":
                    return "fix: sample\n"
                return "daemon/tide_daemon/cardflow/engine.py\0"

            with patch.object(benchmark, "snapshot", return_value="same-tree"), \
                    patch.object(benchmark, "git", side_effect=git), \
                    patch.object(benchmark.runner, "modules_for", return_value=modules), \
                    patch.object(benchmark, "legacy_modules", return_value=modules), \
                    patch.object(benchmark.runner, "changed_modules", return_value=runner.Selection({modules[0]: ["AST"]})), \
                    patch.object(benchmark.subprocess, "run", side_effect=verification) as public, \
                    patch.object(benchmark.runner, "execute", return_value=results[:1]) as execute, \
                    patch("sys.argv", ["benchmark", "--base", "pinned-base", "--jobs", "2", "--output", temp]), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(benchmark.main(), returncode)
                self.assertEqual(benchmark.main(), returncode)
            self.assertEqual(public.call_count, 1)
            self.assertEqual(execute.call_count, 1)
            data = json.loads((output / "comparison.json").read_text())
            self.assertEqual(len(data["commits"]), 5)
            self.assertEqual(len(data["measurements"]), 2)
            self.assertEqual(data["verification"]["returncode"], returncode)
            self.assertEqual(data["head"], "tested-head")
            self.assertEqual(data["tree"], "tested-tree")
            self.assertEqual(data["base"], "pinned-base")
            self.assertIn("--base pinned-base", data["command"])
            for row in data["commits"]:
                self.assertEqual(data["measurements"][row["before"]]["seconds"], 12)
                self.assertEqual(len(data["measurements"][row["after"]]["modules"]), 1)
            self.assertIn("| commit4 | 2 | 1 |", (output / "report.md").read_text())
