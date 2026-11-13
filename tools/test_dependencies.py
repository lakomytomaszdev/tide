"""Statyczne zależności usługi i testów. Nigdy nie importuje analizowanego kodu."""
import argparse
import ast
from collections import defaultdict, deque
import json
from pathlib import Path, PurePosixPath

if __package__:
    from .test_suites import IMPORT_ROOTS, SERVICE_PREFIX, SUITES
else:
    from test_suites import IMPORT_ROOTS, SERVICE_PREFIX, SUITES


def dynamic_imports(tree):
    """Wywołania importerów z aliasami; nierozstrzygalne nazwy pozostają jawne."""
    module_functions = {"importlib": "import_module", "builtins": "__import__"}
    modules = {name: {function} for name, function in module_functions.items()}
    functions = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in module_functions:
                    modules.setdefault(alias.asname or alias.name, set()).add(module_functions[alias.name])
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            function = module_functions.get(node.module)
            for alias in node.names:
                if function and alias.name in (function, "*"):
                    functions.add(alias.asname or function)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if ((isinstance(func, ast.Name) and func.id in functions) or
                (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and
                 func.attr in modules.get(func.value.id, ()))):
            name = node.args[0] if node.args else next(
                (kw.value for kw in node.keywords if kw.arg == "name"), None)
            # Import względny wymaga kontekstu pakietu, a fromlist może ładować podmoduły.
            # Nie zgadujemy: taki importer staje się potencjalnym odbiorcą każdej zmiany.
            uncertain = (not isinstance(name, ast.Constant) or not isinstance(name.value, str) or
                         name.value.startswith(".") or len(node.args) > 1 or
                         any(kw.arg != "name" for kw in node.keywords))
            yield node.lineno, None if uncertain else name.value


class ImportGraph:
    def __init__(self, root, test_dirs):
        paths = set()
        for folder in [*IMPORT_ROOTS, *test_dirs]:
            paths.update(p.relative_to(root).as_posix() for p in (root / folder).rglob("*.py"))
        self.names = {}
        self.packages = {}
        for path in sorted(paths):
            name = path.removesuffix(".py").replace("/", ".")
            package = path.endswith("/__init__.py")
            if package:
                name = name.removesuffix(".__init__")
            self.names[name] = path
            self.packages[path] = name if package else name.rpartition(".")[0]
            for prefix, alias in IMPORT_ROOTS.items():
                if path.startswith(prefix):
                    short = alias + "." + path[len(prefix):].removesuffix(".py").replace("/", ".")
                    if package:
                        short = short.removesuffix(".__init__")
                    self.names[short] = path
        self.dependencies = {}
        self.importers = defaultdict(set)
        self.uncertain = {}
        for path in sorted(paths):
            try:
                tree = ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
            except (OSError, UnicodeError, SyntaxError) as exc:
                raise ValueError(f"Nie można odczytać importów {path}: {exc}") from exc
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:
                        parts = self.packages[path].split(".")
                        if node.level > len(parts):
                            raise ValueError(f"Import poza pakietem w {path}")
                        base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                    names.add(base)
                    names.update(base + "." + alias.name for alias in node.names if alias.name != "*")
            for line, name in dynamic_imports(tree):
                if name is None:
                    self.uncertain.setdefault(path, []).append(line)
                else:
                    names.add(name)
            dependencies = {self.names[name] for name in names if name in self.names}
            # Import podmodułu wykonuje też istniejące inicjalizatory wszystkich
            # pakietów nadrzędnych, również przy stałej nazwie importu dynamicznego.
            for name in names:
                parent = name.rpartition(".")[0]
                while parent:
                    initializer = self.names.get(parent)
                    if initializer and initializer.endswith("/__init__.py"):
                        dependencies.add(initializer)
                    parent = parent.rpartition(".")[0]
            dependencies.discard(path)
            self.dependencies[path] = dependencies
            for dependency in dependencies:
                self.importers[dependency].add(path)

    def affected(self, source, tests, include_uncertain=True):
        """Wszyscy odbiorcy, z najkrótszym łańcuchem do testu, także przez agregatory.

        Zbiór odwiedzonych węzłów kończy cykle i ogranicza pracę do rozmiaru grafu.
        Nierozstrzygalny importer może ładować zmienione źródło: dołączamy jego
        testy oraz wszystkich pośrednich odbiorców, nawet przy znanych testach źródła.
        """
        found, visited = {}, {source}
        queue = deque([(source, (source,))])
        while queue:
            current, chain = queue.popleft()
            importers = self.importers[current]
            if current == source and include_uncertain:
                importers = importers | self.uncertain.keys()
            for importer in sorted(importers):
                if importer in visited:
                    continue
                visited.add(importer)
                reason = (importer, *chain)
                if importer in tests:
                    found[importer] = reason
                queue.append((importer, reason))
        return found


def is_test_support(path, test_dirs, shared):
    """Dane, conftest, __init__ oraz współdzielone moduły TestCase są fixture'ami."""
    if path in shared:
        return True
    for directory in test_dirs:
        prefix = directory.rstrip("/") + "/"
        if path.startswith(prefix):
            relative = PurePosixPath(path[len(prefix):])
            return (len(relative.parts) != 1 or not relative.name.startswith("test") or
                    relative.suffix != ".py")
    return False


# --- Raport zależności (PT-460). Wyłącznie nowe funkcje: ImportGraph i is_test_support bez zmian. ---

CARDFLOW = "daemon/tide_daemon/cardflow/"
ENGINE = CARDFLOW + "engine.py"


def import_chain(graph, source, target):
    """Najkrótszy łańcuch importów source → target (source importuje … target) albo None.

    Krawędzie to dokładnie `graph.dependencies`: import w funkcji liczy się, wzmianka w docstringu
    nie, a import podmodułu ciągnie też inicjalizatory pakietów nadrzędnych."""
    if source not in graph.dependencies or target not in graph.dependencies:
        return None
    seen, queue = {source}, deque([(source,)])
    while queue:
        chain = queue.popleft()
        if chain[-1] == target:
            return list(chain)
        for dependency in sorted(graph.dependencies[chain[-1]]):
            if dependency not in seen:
                seen.add(dependency)
                queue.append((*chain, dependency))
    return None


def without_node(graph, node):
    """Kopia grafu bez węzła: te same krawędzie poza jego importami i jego własną niepewnością."""
    clone = object.__new__(ImportGraph)
    clone.names, clone.packages = graph.names, graph.packages
    clone.dependencies = {path: dependencies - {node} for path, dependencies in graph.dependencies.items()
                          if path != node}
    clone.importers = defaultdict(set, {path: importers - {node} for path, importers in graph.importers.items()
                                        if path != node})
    clone.uncertain = {path: lines for path, lines in graph.uncertain.items() if path != node}
    return clone


def selection_marks(path, certain_tests):
    """Oznaczenia doboru `make test-changed` (bez wpływu na liczby): pełny / fallback / kontrakt."""
    # Zgodnie z changed_modules: __init__.py pakietu wybiera pełny przebieg, brak pewnych testów
    # oznacza zestaw, a ścieżka pod SERVICE_PREFIX dokłada moduły kontraktu usługi.
    marks = []
    if path.endswith("/__init__.py"):
        marks.append("pełny")
    elif not certain_tests:
        marks.append("fallback")
    if path.startswith(SERVICE_PREFIX):
        marks.append("kontrakt")
    return marks


def suite_modules(root):
    """Moduły testów wszystkich zestawów i ich katalogi — tak samo jak domyślny dobór test_runner."""
    if __package__:
        from .test_runner import modules_for
    else:
        from test_runner import modules_for
    entries = [(directory, "test*.py") for directory in sorted({d for pairs in SUITES.values() for d, _ in pairs})]
    tests = set(modules_for(entries, root))
    cardflow_tests = set(modules_for(SUITES["cardflow"], root))
    return tests, cardflow_tests, sorted({directory for directory, _ in entries})


def dependency_report(root):
    """Raport zasięgu i sięgania silnika dla engine.py i plików pod cardflow/ w drzewie `root`.

    Definicje zestawów i IMPORT_ROOTS pochodzą z drzewa narzędzia, analizowane pliki z `root`."""
    root = Path(root)
    tests, cardflow_tests, test_dirs = suite_modules(root)
    graph = ImportGraph(root, test_dirs)
    if ENGINE not in graph.dependencies:
        raise ValueError(f"Brak {ENGINE} w {root}")
    engineless = without_node(graph, ENGINE)
    sources = sorted((p for p in graph.dependencies if p.startswith(CARDFLOW)), key=lambda p: (p != ENGINE, p))
    rows = []
    for path in sources:
        certain = graph.affected(path, tests, include_uncertain=False)
        potential = graph.affected(path, tests, include_uncertain=True)
        chain = import_chain(graph, path, ENGINE)
        own = path != ENGINE
        rows.append({
            "path": path,
            "reach_certain": len(certain),
            "reach_potential": len(potential),
            "reaches_engine": chain is not None,
            "chain": chain,
            "own_certain": len(engineless.affected(path, tests, include_uncertain=False)) if own else None,
            "own_potential": len(engineless.affected(path, tests, include_uncertain=True)) if own else None,
            "marks": selection_marks(path, certain),
        })
    modules = [row for row in rows if row["path"] != ENGINE]
    suite = sorted(cardflow_tests)
    suite_free = [t for t in suite if import_chain(graph, t, ENGINE) is None]
    return {
        "root": str(root),
        "engine": ENGINE,
        "tests_total": len(tests),
        "rows": rows,
        "summary": {
            "cardflow_modules": len(modules),
            "cardflow_modules_without_engine": sum(not row["reaches_engine"] for row in modules),
            "cardflow_suite_tests": len(suite),
            "cardflow_suite_tests_without_engine": len(suite_free),
        },
    }


def format_report(report):
    lines = [f"Drzewo: {report['root']}",
             f"Moduły testów (wszystkie zestawy): {report['tests_total']}",
             "Zasięg = klucze ImportGraph.affected (nie liczba modułów wybranych przez make test-changed).",
             "Testy własne = osiągalność w grafie bez engine.py; oznaczenia to dobór, nie wpływają na liczby.",
             "",
             f"{'Plik':<34} {'Zasięg p/pot':>13} {'Sięga':>6} {'Własne p/pot':>13}  Oznaczenia"]
    for row in report["rows"]:
        own = "—" if row["own_certain"] is None else f"{row['own_certain']}/{row['own_potential']}"
        name = row["path"].removeprefix(CARDFLOW)
        reach = f"{row['reach_certain']}/{row['reach_potential']}"
        lines.append(f"{name:<34} {reach:>13} {'tak' if row['reaches_engine'] else 'nie':>6} {own:>13}  "
                     + ", ".join(row["marks"]))
        if row["chain"] and row["path"] != ENGINE:
            lines.append("    łańcuch: " + " → ".join(p.removeprefix(CARDFLOW) for p in row["chain"]))
    s = report["summary"]
    lines += ["",
              f"Moduły cardflow/ (bez engine.py) bez drogi do silnika: "
              f"{s['cardflow_modules_without_engine']} / {s['cardflow_modules']}",
              f"Moduły testów zestawu cardflow niesięgające silnika: "
              f"{s['cardflow_suite_tests_without_engine']} / {s['cardflow_suite_tests']}"]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Raport zależności testów od plików cardflow/ (tylko AST).")
    parser.add_argument("--json", action="store_true", help="te same dane jako JSON")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="drzewo z analizowanymi plikami (definicje zestawów zawsze z drzewa narzędzia)")
    args = parser.parse_args(argv)
    try:
        report = dependency_report(args.root)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
