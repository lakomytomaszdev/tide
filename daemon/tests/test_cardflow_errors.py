"""PT-461: wyjątki przepływu żyją w cardflow/errors.py, a silnik tylko je ponownie eksportuje."""
import ast
from pathlib import Path
import tempfile
import unittest

from tide_daemon.cardflow import boxcontrol, consent, errors, engine

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "daemon/tide_daemon"
ENGINE_MODULE = "tide_daemon.cardflow.engine"
MOVED = {"FlowError", "FlowDeferred", "fold"}
# Jedyne importy z silnika w kodzie usługi (poza samym silnikiem): FlowEngine w rollback.py i app.py.
ALLOWED = {("cardflow/rollback.py", "FlowEngine"), ("app.py", "FlowEngine")}


def engine_imports(path, package=PACKAGE):
    """(plik względem pakietu, importowana nazwa) dla każdego importu z silnika, także w funkcji (AST).

    `from . import engine` i `import …engine as x` dają moduł; użycie `x.<nazwa>` liczy się jak import nazwy."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(package).as_posix()
    found, aliases = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = ["tide_daemon"] + list(path.relative_to(package).parent.parts)
            if node.level:
                base = base[:len(base) - node.level + 1]
                parts = base + (node.module.split(".") if node.module else [])
            else:
                parts = (node.module or "").split(".")
            dotted = ".".join(parts)
            for alias in node.names:
                if dotted == ENGINE_MODULE:
                    found.append((rel, alias.name))
                elif dotted == ENGINE_MODULE.rpartition(".")[0] and alias.name == "engine":
                    aliases.add(alias.asname or "engine")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == ENGINE_MODULE:
                    aliases.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            found.append((rel, node.attr))
    return found


class ReexportTest(unittest.TestCase):
    def test_engine_reexports_the_same_objects(self):
        self.assertIs(engine.FlowError, errors.FlowError)
        self.assertIs(engine.FlowDeferred, errors.FlowDeferred)

    def test_single_definition_in_service_code(self):
        defined = []
        for path in PACKAGE.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ClassDef) and node.name in {"FlowError", "FlowDeferred"}:
                    defined.append((path.relative_to(PACKAGE).as_posix(), node.name))
        self.assertEqual(sorted(defined), [("cardflow/errors.py", "FlowDeferred"), ("cardflow/errors.py", "FlowError")])

    def test_errors_module_imports_nothing(self):
        tree = ast.parse((PACKAGE / "cardflow/errors.py").read_text(encoding="utf-8"))
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual(imports, [])

    def test_helper_error_is_caught_by_engine_name(self):
        with self.assertRaises(engine.FlowError) as caught:
            boxcontrol.target(None, "nie-ma", "", board=_Board())
        self.assertEqual((caught.exception.code, str(caught.exception)), ("no-project", "nie ma projektu nie-ma"))
        with self.assertRaises(engine.FlowError) as caught:
            consent.deny_queued(None, "K-1", "krok", {"answer": "x" * 4001})
        self.assertEqual(caught.exception.code, "bad-answer")

    def test_deferred_is_caught_by_engine_name(self):
        with self.assertRaises(engine.FlowDeferred):
            raise errors.FlowDeferred("czeka")


class FlowErrorShapeTest(unittest.TestCase):
    def test_code_message_and_default_data(self):
        exc = errors.FlowError("bad", "komunikat")
        self.assertEqual((exc.code, str(exc), exc.data), ("bad", "komunikat", {}))
        self.assertIsInstance(exc, Exception)

    def test_data_is_kept(self):
        self.assertEqual(errors.FlowError("c", "m", {"reviews": [1]}).data, {"reviews": [1]})

    def test_data_default_is_not_shared(self):
        first, second = errors.FlowError("c", "m"), errors.FlowError("c", "m")
        first.data["x"] = 1
        self.assertEqual(second.data, {})


class EngineImportsTest(unittest.TestCase):
    def test_no_module_imports_moved_names_from_engine(self):
        offenders = []
        for path in sorted(PACKAGE.rglob("*.py")):
            if path == PACKAGE / "cardflow/engine.py":
                continue
            offenders += [item for item in engine_imports(path) if item[1] in MOVED]
        self.assertEqual(offenders, [])

    def test_only_flowengine_is_imported_from_engine(self):
        found = set()
        for path in sorted(PACKAGE.rglob("*.py")):
            if path != PACKAGE / "cardflow/engine.py":
                found.update(engine_imports(path))
        self.assertEqual(found, ALLOWED)


class EngineImportsLogicTest(unittest.TestCase):
    """Sam wykrywacz importów na plikach w katalogu tymczasowym: bez niego asercje wyżej mogłyby milczeć."""

    def names(self, source, relative="cardflow/sample.py"):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(source, encoding="utf-8")
            return sorted(name for _, name in engine_imports(file, Path(tmp)))

    def test_relative_absolute_and_in_function_imports(self):
        self.assertEqual(self.names("from .engine import FlowError\n"), ["FlowError"])
        self.assertEqual(self.names("def f():\n    from tide_daemon.cardflow.engine import fold, FlowDeferred\n"),
                         ["FlowDeferred", "fold"])
        self.assertEqual(self.names("from .cardflow.engine import FlowEngine\n", "app.py"), ["FlowEngine"])

    def test_module_alias_attribute_use(self):
        self.assertEqual(self.names("from . import engine as e\nx = e.FlowError\n"), ["FlowError"])
        self.assertEqual(self.names("import tide_daemon.cardflow.engine as e\nx = e.fold\n"), ["fold"])

    def test_other_engines_are_ignored(self):
        self.assertEqual(self.names("from .docker.engine import ACTIONS\n", "docker_monitor.py"), [])


class _Board:
    projects = {}


if __name__ == "__main__":
    unittest.main()
