"""PT-258: rzeczywiste pliki fixture, kierunek zależności i bezpieczne wyjścia selektora."""
from contextlib import redirect_stdout
import ast
import io
from pathlib import Path
import json
import posixpath
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import test_dependencies as deps
from tools import test_runner as runner
from tools import test_changed_benchmark as benchmark
from tools.test_dependencies import ImportGraph
from tools.test_suites import COORDINATION_PATHS, FILE_TEST_MODULES, SERVICE_CONTRACT_MODULES, SUITES


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

    def test_submodule_imports_depend_on_all_parent_initializers(self):
        source = self.source("transport")
        # Dwa poziomy inicjalizatorów; żaden nie może zostać pominięty.
        outer = self.source("models/__init__", "from .. import transport\n")
        inner = self.source("models/nested/__init__", "from ... import transport\n")
        self.source("models/nested/executors")
        direct = self.write_test("transport", "from tide_daemon import transport\n")
        unrelated = self.write_test("unrelated", "import json\n")
        for prefix in ("tide_daemon", "daemon.tide_daemon"):
            for body in (
                f"import {prefix}.models.nested.executors\n",
                f"from {prefix}.models.nested.executors import Executor\n",
                f'import importlib\nimportlib.import_module("{prefix}.models.nested.executors")\n',
                f'__import__("{prefix}.models.nested.executors")\n',
            ):
                with self.subTest(body=body):
                    indirect = self.write_test("executors", body)
                    selection = self.select(source)
                    self.assertIn(direct, selection.modules)
                    self.assertIn(indirect, selection.modules)
                    self.assertNotIn(unrelated, selection.modules)
                    self.assertFalse(selection.full)
                    graph = ImportGraph(self.root, ["daemon/tests"])
                    self.assertTrue({outer, inner} <= graph.dependencies[indirect])
                    self.assertIn(indirect, self.select(outer).modules)
                    self.assertIn(indirect, self.select(inner).modules)

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
        paths = ("README.md", "docs/testing.md", "docs/guide.rst", "docs/images/flow.svg",
                 "daemon/tide_daemon/cardflow/README.md")
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
                     "docs/fixtures/input.md",
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

    def test_explicit_tool_contract_is_not_silently_lost_if_a_test_is_missing(self):
        tool = self.write("tools/known.py")
        with patch.dict(runner.FILE_TEST_MODULES, {tool: ("daemon/tests/missing.py",)}):
            with self.assertRaisesRegex(ValueError, "jawnie przypisanego"):
                self.select(tool)


class RepositorySelectionTest(unittest.TestCase):
    def test_component_and_known_tool_changes_select_only_their_registered_tests(self):
        entries = [(d, "test*.py") for d in sorted({d for pairs in SUITES.values() for d, _ in pairs})]
        all_modules = runner.modules_for(entries)
        cases = [("widgets/build_orders.py", runner.modules_for(SUITES["widgets"])),
                 ("apps/board/qml/BoardPage.qml", runner.modules_for(SUITES["board"])),
                 ("scripts/appearance.py", runner.modules_for(SUITES["scripts"])),
                 ("theme/palette/compare.py", runner.modules_for(SUITES["palette"]))]
        cases += [(path, list(modules)) for path, modules in FILE_TEST_MODULES.items()]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertTrue((runner.ROOT / path).is_file())
                selected = runner.changed_modules([path], entries)
                self.assertFalse(selected.full)
                self.assertEqual(selected.modules, sorted(expected))
                self.assertLess(len(selected.modules), len(all_modules))
                print(f"[PT-258] {path}: {len(selected.modules)}/{len(all_modules)} modułów")

    def test_infrastructure_and_unknown_tools_still_select_full_suite(self):
        entries = [(d, "test*.py") for d in sorted({d for pairs in SUITES.values() for d, _ in pairs})]
        whole = runner.modules_for(entries)
        for path in ("tools/test_runner.py", "tools/test_suites.py", "tools/test_dependencies.py",
                     "tools/test_changed_benchmark.py", "tools/unknown.py", "mk/tests.mk", "Makefile"):
            with self.subTest(path=path):
                selected = runner.changed_modules([path], entries)
                self.assertTrue(selected.full)
                self.assertEqual(selected.modules, whole)

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


class CoordinationSelectionTest(unittest.TestCase):
    """PT-473: pliki koordynacji nie wybierają testów, klucze FILE_TEST_MODULES tak."""
    ENTRIES = [(d, "test*.py") for d in sorted({d for pairs in SUITES.values() for d, _ in pairs})]
    BASE_CHANGE = "daemon/tests/test_cardflow_base_change.py"
    PT201 = "docs/agents/tickets/k-50-flow/pt-201.md"
    PT208 = "docs/agents/tickets/k-50-flow/pt-208.md"

    def select(self, *paths):
        return runner.changed_modules(paths, self.ENTRIES)

    def test_prefix_list_is_literal_directories_and_not_too_broad(self):
        self.assertEqual(COORDINATION_PATHS, ("docs/agents/masterplan/", "docs/agents/interwencje/",
                                              "docs/agents/plans/"))
        for prefix in COORDINATION_PATHS:
            self.assertTrue(prefix.endswith("/"))
            self.assertNotIn("*", prefix)
            self.assertTrue((runner.ROOT / prefix).is_dir())
        self.assertNotIn("docs/", COORDINATION_PATHS)
        self.assertNotIn("docs/agents/", COORDINATION_PATHS)

    def test_no_suite_directory_starts_with_a_coordination_prefix(self):
        for name, entries in SUITES.items():
            for directory, _ in entries:
                with self.subTest(suite=name, directory=directory):
                    self.assertFalse((directory.rstrip("/") + "/").startswith(COORDINATION_PATHS))

    def test_file_test_modules_keys_select_exactly_their_modules(self):
        expected = {
            "docs/agents/plans/followups/replay-code.md": {
                "daemon/tests/test_cardflow_base_change.py",
                "daemon/tests/test_cardflow_base_change_dbus.py", "daemon/tests/test_install.py"},
            "docs/agents/plans/2026-11-05-k-50-flow.md": {self.BASE_CHANGE},
            self.PT201: {self.BASE_CHANGE}, self.PT208: {self.BASE_CHANGE},
        }
        for path, modules in expected.items():
            with self.subTest(path=path):
                self.assertEqual(tuple(sorted(modules)), tuple(sorted(FILE_TEST_MODULES[path])))
                selection = self.select(path)
                self.assertFalse(selection.full)
                self.assertEqual(set(selection.modules), modules)
                self.assertEqual(selection.skipped_coordination, 0)

    def test_keys_outside_coordination_prefixes_bypass_markdown_rule_only_exactly(self):
        self.assertEqual(self.select(self.PT201, self.PT208).modules, [self.BASE_CHANGE])
        for path in ("docs/agents/tickets/k-50-flow/pt-202.md", "docs/agents/tickets/k-50-flow/README.md",
                     "docs/agents/tickets/inny/pt-201.md"):
            with self.subTest(path=path):
                selection = self.select(path)
                self.assertEqual(selection.modules, [])
                self.assertFalse(selection.full)

    def test_other_markdown_stays_zero_also_next_to_a_key(self):
        for path in ("docs/testing.md", "README.md"):
            with self.subTest(path=path):
                self.assertEqual(self.select(path).modules, [])
                self.assertEqual(self.select(path, self.PT201).modules, [self.BASE_CHANGE])

    def test_coordination_only_diff_selects_zero_for_every_extension_and_deletion(self):
        paths = [f"{prefix}{name}" for prefix in COORDINATION_PATHS
                 for name in ("raport.json", "raport.html", "nowy-dziennik.tsv", "notka.txt", "skrypt.py",
                              "plan.md", "podkatalog/usuniety.md", "bez-rozszerzenia")]
        for path in paths:
            with self.subTest(path=path):
                self.assertFalse((runner.ROOT / path).exists())
                selection = self.select(path)
                self.assertEqual(selection.modules, [])
                self.assertFalse(selection.full)
                self.assertEqual(selection.skipped_coordination, 1)
        self.assertEqual(self.select(*paths).skipped_coordination, len(paths))

    def test_mixed_diff_selects_the_same_as_the_code_file_alone(self):
        code = "daemon/tide_daemon/cardflow/engine.py"
        expected = self.select(code)
        self.assertTrue(expected.modules)
        mixed = self.select("docs/agents/plans/x.json", "docs/agents/masterplan/y.html", code)
        self.assertEqual(mixed.modules, expected.modules)
        self.assertFalse(mixed.full)
        self.assertEqual(mixed.skipped_coordination, 2)

    def test_paths_outside_the_list_keep_todays_selection(self):
        self.assertEqual(self.select("docs/testing.md").modules, [])
        for path in ("docs/settings.json", "docs/example.py", "docs/agents/masterplan-old/x.json",
                     "docs/agents/k-38/dane.json", "docs/agents/plans-old/x.json"):
            with self.subTest(path=path):
                selection = self.select(path)
                self.assertTrue(selection.full)
                self.assertEqual(selection.skipped_coordination, 0)

    def test_cli_list_reports_zero_modules_and_skipped_coordination_paths(self):
        output = io.StringIO()
        paths = ["docs/agents/masterplan/a.json", "docs/agents/interwencje/dziennik.tsv"]
        with patch.object(runner, "git_paths", return_value=paths), \
                patch("sys.argv", ["runner", "--dirs", "daemon/tests", "--changed", "--list"]), \
                patch.object(runner, "execute", side_effect=AssertionError("tylko lista")), \
                redirect_stdout(output):
            self.assertEqual(runner.main(runner.ROOT), 0)
        self.assertIn("Wybrane moduły: 0", output.getvalue())
        self.assertIn("Pominięte ścieżki koordynacji: 2", output.getvalue())


JOIN_FUNCTIONS = {"os.path.join", "posixpath.join", "path.join"}
PATH_CLASSES = {"Path", "PurePath", "PurePosixPath", "PosixPath"}


def _dotted(node):
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.append(node.id)
        return ".".join(reversed(names))
    return ""


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_chain(node):
    if isinstance(node, ast.BinOp):
        return isinstance(node.op, ast.Div)
    if isinstance(node, ast.Call):
        name = _dotted(node.func)
        return (name in JOIN_FUNCTIONS or name.rsplit(".", 1)[-1] in PATH_CLASSES
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath"))
    return False


def _segments(node, absorbed):
    """Stałe segmenty wyrażenia ścieżkowego, None w miejscu bazy lub wartości niestałej."""
    if _const(node) is not None:
        return [node.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        absorbed.update((id(node.left), id(node.right)))
        return _segments(node.left, absorbed) + _segments(node.right, absorbed)
    if isinstance(node, ast.Call) and _is_chain(node):
        absorbed.update(id(arg) for arg in node.args)
        own = [seg for arg in node.args
               for seg in (_segments(arg, absorbed) if _is_chain(arg) else [_const(arg)])]
        if isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath":
            absorbed.add(id(node.func.value))
            return _segments(node.func.value, absorbed) + own
        return own
    return [None]


def coordination_candidates(source, prefixes=COORDINATION_PATHS):
    """Ścieżki pod prefiksami koordynacji, które źródło składa jako napis albo ciąg segmentów."""
    start = r"(?:^|(?<=[^\w.-]))"
    pattern = re.compile(start + "((?:" + "|".join(re.escape(p) for p in prefixes) + r")[^\s\"']*)")
    found = set()

    def scan(text):
        found.update(posixpath.normpath(match.group(1)) for match in pattern.finditer(text))

    absorbed = set()
    for node in ast.walk(ast.parse(source)):
        if _const(node) is not None:
            scan(node.value)
        elif _is_chain(node) and id(node) not in absorbed:
            run = []
            for part in _segments(node, absorbed):
                if part is None and run:
                    break
                if part is not None:
                    run.append(part)
            if run:
                scan(posixpath.join(*run))
    return found


# (moduł testu, ścieżka): fałszywe trafienia skanu, czyli nazwy istniejących planów użyte
# w danych albo w repozytorium tymczasowym. Pozycja, która przestała być trafieniem,
# wywala test, więc krotka nie puchnie po cichu.
COORDINATION_SCAN_EXCEPTIONS = (
    # l. ~241, ~259: "/r/docs/…" w danych parsera planu; l. ~279: os.path.join(repo, …) w repozytorium tymczasowym.
    ("daemon/tests/test_coordination_core.py", "docs/agents/plans/2026-10-27-local-models.md"),
    # l. ~243: "/r/docs/…" w danych parsera planu.
    ("daemon/tests/test_coordination_core.py", "docs/agents/plans/2026-10-27-board-and-docker.md"),
    # l. ~145: "/repo/docs/…" jako ścieżka w sztucznym zdarzeniu, plik nie jest otwierany.
    ("daemon/tests/test_cardflow_plans.py", "docs/agents/plans/2026-10-27-board-and-docker.md"),
    # l. ~62, ~64, ~627: "/ws/…/docs/…" i "/r/docs/…" w danych numeracji; pliki powstają w katalogu tymczasowym.
    ("daemon/tests/test_coordination_numbering.py", "docs/agents/plans/2026-10-27-local-models.md"),
    ("daemon/tests/test_coordination_numbering.py", "docs/agents/plans/2026-10-27-board-and-docker.md"),
    ("daemon/tests/test_coordination_numbering.py", "docs/agents/plans/2026-10-27-vivaldi.md"),
    # Ten moduł: ścieżki kluczy FILE_TEST_MODULES i plików diffu przekazywane selektorowi (l. ~346-349, ~408),
    # nikt ich tu nie otwiera.
    ("scripts/tests/test_test_dependencies.py", "docs/agents/plans/followups/replay-code.md"),
    ("scripts/tests/test_test_dependencies.py", "docs/agents/plans/2026-11-05-k-50-flow.md"),
    ("scripts/tests/test_test_dependencies.py", "docs/agents/interwencje/dziennik.tsv"),
    # l. ~494-510: te same nazwy planów w samej krotce wyjątków.
    ("scripts/tests/test_test_dependencies.py", "docs/agents/plans/2026-10-27-vivaldi.md"),
    ("scripts/tests/test_test_dependencies.py", "docs/agents/plans/2026-10-27-local-models.md"),
    ("scripts/tests/test_test_dependencies.py", "docs/agents/plans/2026-10-27-board-and-docker.md"),
)


def scan_coordination_reads():
    """{(moduł testu, ścieżka)}: istniejące, śledzone pliki koordynacji wskazane w testach."""
    listing = subprocess.check_output(["git", "ls-files", "-z"], cwd=runner.ROOT)
    tracked = set(filter(None, listing.decode("utf-8", "surrogateescape").split("\0")))
    hits = set()
    for directory in sorted({d for pairs in SUITES.values() for d, _ in pairs}):
        for file in sorted((runner.ROOT / directory).rglob("*.py")):
            relative = file.relative_to(runner.ROOT).as_posix()
            for path in coordination_candidates(file.read_text(encoding="utf-8")):
                if path in tracked:
                    hits.add((relative, path))
    return hits


class CoordinationExtractorTest(unittest.TestCase):
    """PT-476: ekstraktor ścieżek koordynacji na syntetycznych źródłach."""
    TARGET = "docs/agents/plans/x.md"

    def test_every_shape_yields_the_normalized_path(self):
        cases = {
            "slash_string": 'repo / "docs/agents/plans/x.md"',
            "slash_segments": 'ROOT / "docs" / "agents" / "plans" / "x.md"',
            "parents_base": 'Path(__file__).parents[2] / "docs" / "agents" / "plans" / "x.md"',
            "call_base": 'Path(__file__).resolve() / "docs/agents/plans/x.md"',
            "os_join": 'os.path.join(ROOT, "docs", "agents", "plans", "x.md")',
            "os_join_const_first": 'os.path.join("docs", "agents", "plans", "x.md")',
            "posixpath_join": 'posixpath.join(ROOT, "docs/agents", "plans", "x.md")',
            "path_args": 'Path("docs", "agents", "plans", "x.md")',
            "joinpath": 'repo.joinpath("docs", "agents", "plans", "x.md")',
            "path_of_chain": 'Path(ROOT / "docs" / "agents" / "plans" / "x.md")',
            "join_with_chain_arg": 'os.path.join(ROOT / "docs" / "agents" / "plans", "x.md")',
            "plain_string": '"docs/agents/plans/x.md"',
            "shell_text": """'cp "$src/../docs/agents/plans/x.md" "$dst"'""",
            "absolute_data": '"/r/docs/agents/plans/x.md"',
            "fstring_const_part": 'f"{root}/docs/agents/plans/x.md"',
            "fstring_leading": 'f"docs/agents/plans/x.md {extra}"',
            "dotdot": '"docs/agents/plans/sub/../x.md"',
        }
        for name, source in cases.items():
            with self.subTest(shape=name):
                self.assertEqual(coordination_candidates(source), {self.TARGET})

    def test_paths_outside_the_list_are_not_candidates(self):
        for source in ('"docs/agents/pt-288/catalog.md"', '"docs/agents/masterplan-old/x.json"',
                       'ROOT / "docs" / "agents" / "pt-288" / "catalog.md"',
                       'os.path.join(ROOT, "docs", "agents", "plans-old", "x.md")',
                       '"not-docs/agents/plans/x.md"', '"docs/testing.md"', '"plans"'):
            with self.subTest(source=source):
                self.assertEqual(coordination_candidates(source), set())

    def test_other_prefixes_and_multiple_hits(self):
        source = 'a = "docs/agents/masterplan/a.json"\nb = repo / "docs" / "agents" / "interwencje" / "d.tsv"'
        self.assertEqual(coordination_candidates(source),
                         {"docs/agents/masterplan/a.json", "docs/agents/interwencje/d.tsv"})

    def test_run_of_constants_stops_at_a_non_constant_segment(self):
        self.assertEqual(coordination_candidates('repo / "docs" / "agents" / "plans" / name / "x.md"'), set())


class CoordinationReadsAreMappedTest(unittest.TestCase):
    """PT-476: lista koordynacji nie ukrywa pliku, który test czyta z prawdziwego repozytorium."""

    def test_every_scanned_read_is_a_key_with_its_module_among_receivers(self):
        exceptions = set(COORDINATION_SCAN_EXCEPTIONS)
        self.assertEqual(len(exceptions), len(COORDINATION_SCAN_EXCEPTIONS), "powtórzona pozycja wyjątku")
        for module, path in sorted(scan_coordination_reads() - exceptions):
            with self.subTest(module=module, path=path):
                self.assertIn(module, FILE_TEST_MODULES.get(path, ()),
                              f"{module} czyta {path}: dopisz wpis w FILE_TEST_MODULES (tools/test_suites.py) "
                              "albo, gdy to dane, pozycję w COORDINATION_SCAN_EXCEPTIONS")

    def test_exceptions_are_still_hits_and_are_not_mapped_reads(self):
        hits = scan_coordination_reads()
        for module, path in COORDINATION_SCAN_EXCEPTIONS:
            with self.subTest(module=module, path=path):
                self.assertIn((module, path), hits, "wyjątek przestał być trafieniem: usuń go z krotki")
                self.assertNotIn(module, FILE_TEST_MODULES.get(path, ()))


class SelectionBenchmarkTest(unittest.TestCase):
    def test_five_commits_then_public_verification_and_resume_reuses_measurements(self):
        self.check_verification(0)

    def test_failed_verification_keeps_failed_status_and_five_commit_report(self):
        self.check_verification(1)

    def check_verification(self, returncode):
        with tempfile.TemporaryDirectory(dir=runner.ROOT) as temp:
            output = Path(temp)
            modules = ["daemon/tests/test_a.py", "daemon/tests/test_b.py"]
            results = [dict(module=m, tests=1, returncode=0, seconds=1) for m in modules]

            def verification(command, **kwargs):
                recorded = json.loads((output / "comparison.json").read_text())
                self.assertEqual(len(recorded["commits"]), 5, "benchmark przed walidacją")
                parent = Path(kwargs["env"]["TEST_OUTPUT"]) / "run-1"
                parent.mkdir()
                (parent / "summary.json").write_text(json.dumps({"seconds": 12, "results": results}))
                self.assertEqual(command, ["make", "test-changed", "BASE=pinned-base"])
                return subprocess.CompletedProcess(command, returncode)

            def git(*args):
                if args[0] == "rev-parse":
                    if args[-1] == "history-source^{commit}":
                        return "history-sha\n"
                    return "tested-tree\n" if args[1] == "HEAD^{tree}" else "tested-head\n"
                if args[0] == "log":
                    self.assertEqual(args[-1], "history-sha", "historia niezależna od bazy walidacji")
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
                    patch.object(benchmark.runner, "execute",
                                 side_effect=lambda selected, *a: [r for r in results if r["module"] in selected]) as execute, \
                    patch("sys.argv", ["benchmark", "--base", "pinned-base", "--history-ref", "history-source",
                                       "--jobs", "2", "--output", temp]), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(benchmark.main(), returncode)
                self.assertEqual(benchmark.main(), returncode)
            self.assertEqual(public.call_count, 1)
            self.assertEqual(execute.call_count, 2)
            data = json.loads((output / "comparison.json").read_text())
            self.assertEqual(len(data["commits"]), 5)
            self.assertEqual(len(data["measurements"]), 2)
            self.assertEqual(data["verification"]["returncode"], returncode)
            self.assertEqual(data["head"], "tested-head")
            self.assertEqual(data["tree"], "tested-tree")
            self.assertEqual(data["base"], "pinned-base")
            self.assertEqual(data["history_commit"], "history-sha")
            self.assertIn("--base pinned-base", data["command"])
            for row in data["commits"]:
                self.assertEqual(data["measurements"][row["before"]]["results"], results)
                self.assertGreaterEqual(data["measurements"][row["before"]]["seconds"], 0)
                self.assertEqual(len(data["measurements"][row["after"]]["modules"]), 1)
            self.assertIn("| commit4 | 2 | 1 |", (output / "report.md").read_text())


class DependencyReportTest(unittest.TestCase):
    """PT-460: raport zasięgu i sięgania silnika na drzewie fixture (zestawy z drzewa narzędzia, pliki z --root)."""
    SUITES = {"cardflow": [("daemon/tests", "test_cardflow*.py")], "daemon": [("daemon/tests", "test*.py")]}
    PREFIX = "daemon/tide_daemon/cardflow/"
    ENGINE = PREFIX + "engine.py"

    def setUp(self):
        patcher = patch.object(deps, "SUITES", self.SUITES)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = self.tree({
            "__init__": "",
            "engine": "from . import shared\n",
            "shared": 'raise RuntimeError("analiza nie może wykonać kodu")\n',
            "runner": "from . import engine\n",
            "chained": "from . import runner\n",
            "lazy": "def load():\n    from . import engine\n    return engine\n",
            "doc": '"""Silnik (`engine.py`) podaje dane; from . import engine to tylko wzmianka."""\n# import engine\n',
            "pure": "import json\n",
            "orphan": "",
        }, {
            "cardflow_pure": "from tide_daemon.cardflow import pure\n",
            "cardflow_runner": "from tide_daemon.cardflow import runner\n",
            "cardflow_engine_only": "from tide_daemon.cardflow import engine\n",
            "cardflow_doc": "from tide_daemon.cardflow import doc\n",
            "cardflow_dynamic": "import importlib\n\ndef load(name):\n    return importlib.import_module(name)\n",
            "other": "from tide_daemon.cardflow import pure\n",
        })
        self.tests = set(deps.suite_modules(self.root)[0])

    def tree(self, sources, tests):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, body in sources.items():
            self.put(root, self.PREFIX + name + ".py", body)
        for name, body in tests.items():
            self.put(root, "daemon/tests/test_" + name + ".py", body)
        return root

    @staticmethod
    def put(root, path, body):
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body, encoding="utf-8")

    def rows(self, root=None):
        report = deps.dependency_report(root or self.root)
        return report, {row["path"].removeprefix(self.PREFIX): row for row in report["rows"]}

    def test_reach_equals_affected_keys_for_both_variants_and_engine_is_first(self):
        report, rows = self.rows()
        graph = ImportGraph(self.root, ["daemon/tests"])
        self.assertEqual(report["rows"][0]["path"], self.ENGINE)
        self.assertEqual(set(rows), {"engine.py", "__init__.py", "shared.py", "runner.py", "chained.py",
                                     "lazy.py", "doc.py", "pure.py", "orphan.py"})
        for name, row in rows.items():
            path = self.PREFIX + name
            self.assertEqual(row["reach_certain"], len(graph.affected(path, self.tests, include_uncertain=False)))
            self.assertEqual(row["reach_potential"], len(graph.affected(path, self.tests, include_uncertain=True)))
        # Znane wartości: pewne bez testu importera nierozstrzygalnego, potencjalne z nim.
        self.assertEqual((rows["pure.py"]["reach_certain"], rows["pure.py"]["reach_potential"]), (2, 3))
        self.assertEqual((rows["runner.py"]["reach_certain"], rows["runner.py"]["reach_potential"]), (1, 2))
        self.assertEqual((rows["orphan.py"]["reach_certain"], rows["orphan.py"]["reach_potential"]), (0, 1))

    def test_reaches_engine_with_chain_in_function_import_counted_docstring_not(self):
        _, rows = self.rows()
        self.assertEqual(rows["chained.py"]["chain"], [self.PREFIX + n for n in ("chained.py", "runner.py", "engine.py")])
        self.assertEqual(rows["lazy.py"]["chain"], [self.PREFIX + "lazy.py", self.ENGINE])
        self.assertTrue(rows["runner.py"]["reaches_engine"])
        for name in ("doc.py", "pure.py", "orphan.py", "shared.py", "__init__.py"):
            with self.subTest(name=name):
                self.assertFalse(rows[name]["reaches_engine"])
                self.assertIsNone(rows[name]["chain"])

    def test_summary_counts_modules_and_cardflow_suite_tests_without_engine(self):
        report, _ = self.rows()
        # Bez engine.py: runner, chained, lazy sięgają silnika; __init__, shared, doc, pure, orphan nie.
        self.assertEqual(report["summary"]["cardflow_modules"], 8)
        self.assertEqual(report["summary"]["cardflow_modules_without_engine"], 5)
        # Zestaw cardflow: runner i engine_only sięgają silnika, pure/doc/dynamic nie.
        self.assertEqual(report["summary"]["cardflow_suite_tests"], 5)
        self.assertEqual(report["summary"]["cardflow_suite_tests_without_engine"], 3)

    def test_own_tests_exclude_tests_reachable_only_through_engine(self):
        _, rows = self.rows()
        graph = ImportGraph(self.root, ["daemon/tests"])
        # shared.py: testy wchodzą tylko przez engine → runner/lazy/chained; bez węzła engine nic nie zostaje.
        self.assertEqual(set(graph.affected(self.PREFIX + "shared.py", self.tests, include_uncertain=False)),
                         {"daemon/tests/test_cardflow_runner.py", "daemon/tests/test_cardflow_engine_only.py"})
        self.assertEqual((rows["shared.py"]["own_certain"], rows["shared.py"]["own_potential"]), (0, 1))
        # runner.py ma własny test niezależnie od silnika.
        self.assertEqual((rows["runner.py"]["own_certain"], rows["runner.py"]["own_potential"]), (1, 2))
        self.assertEqual((rows["engine.py"]["own_certain"], rows["engine.py"]["own_potential"]), (None, None))

    def test_import_chain_a_to_b_and_missing_chain(self):
        graph = ImportGraph(self.root, ["daemon/tests"])
        p = self.PREFIX
        self.assertEqual(deps.import_chain(graph, p + "chained.py", self.ENGINE),
                         [p + "chained.py", p + "runner.py", self.ENGINE])
        self.assertEqual(deps.import_chain(graph, self.ENGINE, self.ENGINE), [self.ENGINE])
        self.assertIsNone(deps.import_chain(graph, self.ENGINE, p + "runner.py"))
        self.assertIsNone(deps.import_chain(graph, p + "pure.py", self.ENGINE))
        self.assertIsNone(deps.import_chain(graph, p + "nie_ma.py", self.ENGINE))

    def test_selection_marks_full_fallback_and_service_contract(self):
        _, rows = self.rows()
        self.assertEqual(rows["__init__.py"]["marks"], ["pełny", "kontrakt"])
        self.assertEqual(rows["orphan.py"]["marks"], ["fallback", "kontrakt"])
        self.assertEqual(rows["runner.py"]["marks"], ["kontrakt"])
        self.assertEqual(deps.selection_marks("tools/x.py", {"t"}), [])
        # Oznaczenia zgadzają się z doborem make test-changed na tym samym drzewie.
        entries = [("daemon/tests", "test*.py")]
        with patch.object(runner, "SUITES", self.SUITES), \
                patch.object(runner, "SERVICE_CONTRACT_MODULES", ("daemon/tests/test_other.py",)):
            self.assertTrue(runner.changed_modules([self.PREFIX + "__init__.py"], entries, self.root).full)
            fallback = runner.changed_modules([self.PREFIX + "orphan.py"], entries, self.root)
            self.assertIn("zestaw cardflow: brak rozpoznanych importów testów",
                          fallback.reasons["daemon/tests/test_cardflow_pure.py"][0])
            normal = runner.changed_modules([self.PREFIX + "runner.py"], entries, self.root)
            self.assertFalse(any("brak rozpoznanych" in r for rs in normal.reasons.values() for r in rs))
            self.assertIn("daemon/tests/test_other.py", normal.reasons)

    def test_report_never_imports_analysed_code(self):
        sys.modules.pop("tide_daemon.cardflow.shared", None)
        self.rows()
        self.assertNotIn("tide_daemon.cardflow.shared", sys.modules)

    def test_cli_text_and_json_carry_the_same_data(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(deps.main(["--root", str(self.root)]), 0)
        text = out.getvalue()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(deps.main(["--json", "--root", str(self.root)]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data, deps.dependency_report(self.root))
        self.assertIn("łańcuch: chained.py → runner.py → engine.py", text)
        self.assertIn("bez drogi do silnika: 5 / 8", text)
        self.assertIn("niesięgające silnika: 3 / 5", text)
        self.assertIn("pełny, kontrakt", text)
        self.assertNotIn(str(self.root) + "/" + self.PREFIX, text)

    def test_root_points_to_another_tree_with_suites_from_the_tool_tree(self):
        other = self.tree({"__init__": "", "engine": "", "alone": ""},
                          {"cardflow_alone": "from tide_daemon.cardflow import alone\n"})
        _, rows = self.rows(other)
        self.assertEqual(set(rows), {"engine.py", "__init__.py", "alone.py"})
        self.assertEqual(rows["alone.py"]["reach_certain"], 1)
        self.assertFalse(rows["alone.py"]["reaches_engine"])
        out = io.StringIO()
        with redirect_stdout(out):
            deps.main(["--json", "--root", str(other)])
        self.assertEqual(json.loads(out.getvalue())["root"], str(other))

    def test_missing_engine_is_a_cli_error(self):
        bare = self.tree({"__init__": ""}, {"cardflow_x": ""})
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            deps.main(["--root", str(bare)])
