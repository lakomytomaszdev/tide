"""Statyczne zależności usługi i testów. Nigdy nie importuje analizowanego kodu."""
import ast
from collections import defaultdict, deque
from pathlib import PurePosixPath

if __package__:
    from .test_suites import (IMPORT_ROOTS, IMPORT_MAX_DEPTH, IMPORT_MAX_DEPENDENCIES,
                             IMPORT_MAX_IMPORTERS)
else:
    from test_suites import (IMPORT_ROOTS, IMPORT_MAX_DEPTH, IMPORT_MAX_DEPENDENCIES,
                            IMPORT_MAX_IMPORTERS)


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
                elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                    # Jawny import dynamiczny ze stałą nazwą też da się rozpoznać bez wykonania.
                    func = node.func
                    if ((isinstance(func, ast.Name) and func.id == "__import__") or
                        (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and
                         func.value.id == "importlib" and func.attr == "import_module")):
                        if isinstance(node.args[0].value, str):
                            names.add(node.args[0].value)
            dependencies = {self.names[name] for name in names if name in self.names} - {path}
            self.dependencies[path] = dependencies
            for dependency in dependencies:
                self.importers[dependency].add(path)

    def affected(self, source, tests, max_depth=IMPORT_MAX_DEPTH,
                 max_dependencies=IMPORT_MAX_DEPENDENCIES, max_importers=IMPORT_MAX_IMPORTERS):
        """Najkrótszy łańcuch do testu, z jawnymi granicami agregatorów i fixture'ów.

        Bezpośrednie testy zawsze wygrywają. Nie przechodzimy dalej przez moduł
        pośredni o dużej liczbie zależności/odbiorców (np. engine lub FlowInProcess).
        Zmiana samego takiego modułu wybiera bezpośrednich odbiorców; pokrycie
        integracyjne zapewnia dołączany przez selektor kontrakt procesu usługi.
        """
        if (len(self.dependencies.get(source, ())) > max_dependencies or
                len(self.importers[source]) > max_importers):
            max_depth = min(max_depth, 1)
        found, visited = {}, {source}
        queue = deque([(source, (source,))])
        while queue:
            current, chain = queue.popleft()
            if len(chain) > max_depth:
                continue
            for importer in sorted(self.importers[current]):
                if importer in visited:
                    continue
                visited.add(importer)
                reason = (importer, *chain)
                if importer in tests:
                    found[importer] = reason
                if (len(self.dependencies[importer]) <= max_dependencies and
                        len(self.importers[importer]) <= max_importers):
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
