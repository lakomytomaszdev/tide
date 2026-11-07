"""Statyczne zależności usługi i testów. Nigdy nie importuje analizowanego kodu."""
import ast
from collections import defaultdict, deque
from pathlib import PurePosixPath

if __package__:
    from .test_suites import IMPORT_ROOTS
else:
    from test_suites import IMPORT_ROOTS


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
