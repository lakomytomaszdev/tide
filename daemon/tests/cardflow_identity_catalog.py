"""PT-288: niezależny katalog zapisów dzienników i konsumentów tożsamości procesów.

Katalog powstaje wyłącznie z kodu źródłowego usługi (AST), bez importu modelu
`cardflow.processidentity` ani generatorów testów. Stałe, opakowania i dynamiczne
słowniki rozwiązuje ten moduł: najpierw literalnie (słowniki, przypisania lokalne,
parametry **kwargs opakowań, zwracane słowniki funkcji), a resztę ręcznie utrzymywana
tabela z uzasadnieniem. Każdy wpis tabeli ma własną kontrolę w
`test_cardflow_identity_catalog.py`. Nierozwiązany zapis nie jest pomijany: trafia
do `unresolved`, a test kompletności kończy się porażką.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "tide_daemon"
CARD_ENVELOPE = frozenset({"v", "seq", "ts", "card", "type"})
BOX_ENVELOPE = frozenset({"v", "ts", "type", "box"})
# Znacznik zakresu dynamicznego w zbiorze pól. Zakres dynamiczny NIE ukrywa pól jawnych:
# zbiór zachowuje każde znane pole obok znacznika (M4 review 1), a model musi je sklasyfikować.
# Sam znacznik dozwolony tylko dla zdarzeń/akcji metadanych z jawnym `dynamic` w modelu.
ANY = "*"
DYN = frozenset({ANY})


def dynamic(fields) -> bool:
    return ANY in fields


def explicit(fields) -> frozenset:
    return frozenset(fields) - DYN


@dataclass(frozen=True)
class Write:
    store: str          # "card" albo "box"
    path: str           # ścieżka względem tide_daemon
    function: str       # funkcja z zapisem albo z wywołaniem opakowania
    line: int
    kind: str           # rozwiązany typ zdarzenia
    discriminator: str  # wartość `action` dla intent/result, inaczej ""
    fields: frozenset   # pola ładunku (bez koperty); ANY w zbiorze = dodatkowy zakres dynamiczny
    via: str = ""       # opakowanie lub reguła rozwiązania


@dataclass(frozen=True)
class Consumer:
    path: str
    function: str
    line: int
    reads: frozenset


@dataclass(frozen=True)
class Effect:
    path: str
    function: str
    line: int
    effect: str


@dataclass
class Catalog:
    writes: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)   # (plik, funkcja, linia, opis)
    used_scopes: set = field(default_factory=set)


# ------------------------------------------------------------------ ręczna tabela rozwiązań
#
# (plik, funkcja, wyrażenie **) → (reguła, pola). Reguła wskazuje kontrolę w teście.
# ANY wolno użyć wyłącznie dla zdarzeń/akcji, które model klasyfikuje jako metadane.
DYNAMIC = {
    # Placement.entry() w cardflow/boxstep.py (nazwa `entry` jest niejednoznaczna w pakiecie);
    # kontrola AST zwracanego słownika i przypisań indeksowanych.
    ("cardflow/engine.py", "FlowEngine._launch", "placement.entry()"):
        ("placement-entry", frozenset({"runner", "box", "boxSlug", "boxIp", "boxAccess", "boxPortOwner"})),
    ("cardflow/engine.py", "FlowEngine._launch.launch", "placement.entry()"):
        ("placement-entry", frozenset({"runner", "box", "boxSlug", "boxIp", "boxAccess", "boxPortOwner"})),
    # _step_base_context zwraca krotkę (opis, słownik); kontrola AST zwracanego słownika.
    ("cardflow/engine.py", "FlowEngine._launch", "base_context"):
        ("step-base-context", frozenset({"baseVersion", "baseRef", "baseCommit"})),
    ("cardflow/engine.py", "FlowEngine._launch_full_planning", "base_context"):
        ("step-base-context", frozenset({"baseVersion", "baseRef", "baseCommit"})),
    # Ładunki metadanych (reduktor ich nie czyta; model musi mieć klasyfikację metadanych).
    ("cardflow/engine.py", "FlowEngine._guard_baseline", "base"): ("metadata-payload", DYN),
    ("cardflow/engine.py", "FlowEngine._guard_rebase", "fresh"): ("metadata-payload", DYN),
    ("cardflow/engine.py", "FlowEngine._finish", "guarded"): ("metadata-payload", DYN),
    ("cardflow/engine.py", "FlowEngine._finish", "verdict"): ("metadata-payload", DYN),
    ("cardflow/engine.py", "FlowEngine._recover_verdicts", "verdict"): ("metadata-payload", DYN),
    ("cardflow/engine.py", "FlowEngine._merge_card_to_main_locked", "prepared"): ("metadata-payload", DYN),
    ("cardflow/engine.py", "FlowEngine._resume_main_merge.checkpoint", "data"): ("metadata-payload", DYN),
    ("cardflow/engine.py", "FlowEngine._resume_main_merge", "result"): ("metadata-payload", DYN),
    ("cardflow/basegeneration.py", "BaseGeneration.checkpoint", "data"): ("metadata-payload", DYN),
    ("cardflow/planediting.py", "PlanEditing._record_plan_review", "report"): ("metadata-payload", DYN),
    ("cardflow/consent.py", "revoke", "request"): ("metadata-payload", DYN),
    ("cardflow/boxwait.py", "set_box_wait", "extra"): ("metadata-payload", DYN),
    ("cardflow/tracker.py", "TrackerOutbox.run", "data"): ("metadata-payload", DYN),
    ("cardflow/boxstep.py", "recover_network", "guarded"): ("metadata-payload", DYN),
    ("cardflow/caretakerchat.py", "CaretakerChat._poll_caretaker_chat",
     "{k: v for k, v in run.items() if k not in ('seq', 'ts', 'type', 'step')}"): ("metadata-payload", DYN),
}

# Wynik pracy (`work`) w opakowaniu zamiar → wynik, gdy funkcja pracy nie zwraca literału.
# Klucz: wartość dyskryminatora. Kroki procesu muszą mieć wynik rozwiązany literalnie.
WORK_BY_ACTION = {
    "plan-commit": "commit planu (metadane Gita)",
    "merge": "scalenie ticketu do karty (metadane Gita)",
    "caretaker-action": "akcja opiekuna na stanie planu/kroku (metadane opiekuna)",
}

# Dyskryminator przepisany z istniejącego zamiaru (replay); nie wprowadza nowej wartości.
REPLAY_ACTIONS = {
    ("cardflow/engine.py", "FlowEngine._reconcile", "action"): "intent.get('action')",
}

# Wywołania zwrotne: definicja z parametrem typu → wywołania atrybutu w module odbiorcy.
CALLBACKS = {
    ("cardflow/boxstep.py", "runner_of.<lambda>"): ("box/runner.py", "journal"),
}

# Nazwy niejednoznaczne w pakiecie: (prefiks ścieżki, końcówka odbiorcy) wywołań opakowania.
WRAPPER_SCOPE = {"run": ("cardflow/", "tracker"), "_act": ("cardflow/", "self")}


# ------------------------------------------------------------------ narzędzia AST

def _rel(path: Path) -> str:
    return path.relative_to(PACKAGE).as_posix()


def link(tree):
    """Drzewo AST z odnośnikami `_parent` (także dla drzew zmienionych w kontrolach mutacyjnych)."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node
    return tree


@lru_cache(maxsize=None)
def _parsed(path: str):
    return link(ast.parse((PACKAGE / path).read_text(encoding="utf-8"), filename=path))


# Kontrole mutacyjne podmieniają drzewo modułu (albo dodają nowy moduł) bez zmiany plików.
_OVERRIDES: dict = {}


def module(path: str):
    if path in _OVERRIDES:
        return _OVERRIDES[path]
    return _parsed(path)


@lru_cache(maxsize=None)
def modules():
    return tuple(sorted({_rel(p) for p in PACKAGE.rglob("*.py")} | set(_OVERRIDES)))


@contextmanager
def overridden(trees: dict):
    """Katalog liczony z podmienionych drzew AST `{ścieżka: drzewo}` (kontrole mutacyjne)."""
    previous = dict(_OVERRIDES)
    _OVERRIDES.update({path: link(tree) for path, tree in trees.items()})
    _clear()
    try:
        yield
    finally:
        _OVERRIDES.clear()
        _OVERRIDES.update(previous)
        _clear()


def _clear():
    for cached in (modules, imports, constants, local_constants, definitions, _graph_cached, _journal_aliases):
        cached.cache_clear()


def qualname(node) -> str:
    names = []
    current = node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
        else getattr(node, "_parent", None)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(current.name)
        elif isinstance(current, ast.Lambda):
            names.append("<lambda>")
        current = getattr(current, "_parent", None)
    return ".".join(reversed(names)) or "<module>"


def enclosing_function(node):
    current = getattr(node, "_parent", None)
    while current is not None and not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        current = getattr(current, "_parent", None)
    return current


def params(function) -> list:
    args = [a.arg for a in function.args.posonlyargs + function.args.args]
    if args and args[0] in ("self", "cls"):
        args = args[1:]
    return args


def all_params(function) -> set:
    return set(params(function)) | {a.arg for a in function.args.kwonlyargs}


def _resolve_module(path: str, level: int, name: str | None) -> str | None:
    base = Path(path).parent
    for _ in range(level - 1):
        base = base.parent
    target = base / (name.replace(".", "/") if name else "")
    for candidate in (target.with_suffix(".py"), target / "__init__.py"):
        if (PACKAGE / candidate).is_file():
            return candidate.as_posix()
    return None


@lru_cache(maxsize=None)
def imports(path: str) -> dict:
    """alias → (moduł, nazwa albo None) dla importów pakietu."""
    out = {}
    for node in ast.walk(module(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE.name + "."):
                    source = _resolve_module(path, len(Path(path).parts),
                                             alias.name.removeprefix(PACKAGE.name + "."))
                    if source:
                        out.setdefault(alias.asname or alias.name, (source, None))
        if isinstance(node, ast.ImportFrom) and not node.level and (node.module or "").split(".")[0] == PACKAGE.name:
            # Import bezwzględny pakietu (`from tide_daemon.box.state import record`): jak względny
            # od korzenia pakietu (M6 review 2).
            node = ast.ImportFrom(module=".".join(node.module.split(".")[1:]) or None, names=node.names,
                                  level=len(Path(path).parts))
        if isinstance(node, ast.ImportFrom) and node.level:
            for alias in node.names:
                as_module = _resolve_module(path, node.level, (node.module + "." if node.module else "") + alias.name)
                if as_module:
                    out.setdefault(alias.asname or alias.name, (as_module, None))
                else:
                    source = _resolve_module(path, node.level, node.module)
                    if source:
                        out.setdefault(alias.asname or alias.name, (source, alias.name))
    return out


@lru_cache(maxsize=None)
def constants(path: str) -> dict:
    """Stałe modułu i stałe importowane po nazwie (z wykrywaniem cykli importów)."""
    out = dict(local_constants(path))
    seen = {path}
    pending = [(alias, source, name) for alias, (source, name) in imports(path).items() if name]
    while pending:
        alias, source, name = pending.pop()
        if alias in out:
            continue
        found = local_constants(source)
        if name in found:
            out[alias] = found[name]
        elif source not in seen:
            seen.add(source)
            target = imports(source).get(name)
            if target and target[1]:
                pending.append((alias, target[0], target[1]))
    return out


@lru_cache(maxsize=None)
def local_constants(path: str) -> dict:
    """Stałe modułu: napisy i krotki napisów (także przypisania wielokrotne)."""
    out = {}

    def literal(value):
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)) and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts):
            return tuple(e.value for e in value.elts)
        return None

    for node in module(path).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and literal(value) is not None:
            out[target.id] = literal(value)
        elif isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple) and len(target.elts) == len(value.elts):
            for name, item in zip(target.elts, value.elts):
                if isinstance(name, ast.Name) and literal(item) is not None:
                    out[name.id] = literal(item)
    return out


def resolve_constant(path: str, node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return tuple(e.value for e in node.elts)
    if isinstance(node, ast.Name):
        function = enclosing_function(node)
        if function is not None and node.id in all_params(function):
            return None
        return constants(path).get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        target = imports(path).get(node.value.id)
        if target and target[1] is None:
            return constants(target[0]).get(node.attr)
    return None


def resolve_strings(path: str, node):
    """Zbiór możliwych napisów (stała albo wyrażenie warunkowe stałych)."""
    if isinstance(node, ast.IfExp):
        left, right = resolve_strings(path, node.body), resolve_strings(path, node.orelse)
        return None if left is None or right is None else left | right
    value = resolve_constant(path, node)
    return frozenset({value}) if isinstance(value, str) else None


def guard_values(node, name: str):
    """Wartości zmiennej z otaczającego `if name == "x"` albo `if name in (...)`."""
    child, current = node, getattr(node, "_parent", None)
    while current is not None and not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if isinstance(current, ast.If) and child in current.body:
            test = current.test
            if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == name
                    and len(test.ops) == 1):
                right = test.comparators[0]
                if isinstance(test.ops[0], ast.Eq) and isinstance(right, ast.Constant):
                    return frozenset({right.value})
                if isinstance(test.ops[0], ast.In) and isinstance(right, (ast.Tuple, ast.Set, ast.List)) \
                        and all(isinstance(e, ast.Constant) for e in right.elts):
                    return frozenset(e.value for e in right.elts)
        child, current = current, getattr(current, "_parent", None)
    return None


@lru_cache(maxsize=None)
def definitions() -> dict:
    out = {}
    for path in modules():
        for node in ast.walk(module(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, []).append((path, node))
    return out


def call_sites(name: str):
    prefix, receiver = WRAPPER_SCOPE.get(name, ("", ""))
    for path in modules():
        if not path.startswith(prefix):
            continue
        for node in ast.walk(module(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == name and not receiver:
                yield path, node
            elif isinstance(func, ast.Attribute) and func.attr == name:
                if receiver and not ast.unparse(func.value).endswith(receiver):
                    continue
                yield path, node


def call_argument(call, function, parameter: str):
    names = params(function)
    if parameter in names:
        index = names.index(parameter)
        if index < len(call.args) and not any(isinstance(a, ast.Starred) for a in call.args[:index + 1]):
            return call.args[index]
    return next((k.value for k in call.keywords if k.arg == parameter), None)


class Unresolved(Exception):
    pass


def literal_keys(node) -> frozenset:
    """Znane klucze zmiennej `node` w jej funkcji: `x["k"] = …`, `x["k"] += …`, `x.update(k=…)`,
    `x.update({"k": …})`, `x.setdefault("k", …)`, `x |= {"k": …}` i słownik w przypisaniu `x = {…}`.
    Klucze niestałe pozostają częścią zakresu dynamicznego (osobna kontrola w teście)."""
    if not isinstance(node, ast.Name):
        return frozenset()
    function = enclosing_function(node)
    if function is None:
        return frozenset()

    def dict_keys(value):
        if isinstance(value, ast.Dict):
            return {k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        return set()

    def same(target):
        return isinstance(target, ast.Name) and target.id == node.id

    keys = set()
    for child in ast.walk(function):
        targets = child.targets if isinstance(child, ast.Assign) else \
            [child.target] if isinstance(child, (ast.AugAssign, ast.AnnAssign)) else []
        for target in targets:
            if isinstance(target, ast.Subscript) and same(target.value) and isinstance(target.slice, ast.Constant) \
                    and isinstance(target.slice.value, str):
                keys.add(target.slice.value)
            elif same(target) and getattr(child, "value", None) is not None:
                keys |= dict_keys(child.value)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and same(child.func.value):
            if child.func.attr == "update":
                keys |= {k.arg for k in child.keywords if k.arg}
                for arg in child.args:
                    keys |= dict_keys(arg)
            elif child.func.attr == "setdefault" and child.args and isinstance(child.args[0], ast.Constant) \
                    and isinstance(child.args[0].value, str):
                keys.add(child.args[0].value)
    return frozenset(keys)


class Resolver:
    """Rozwiązywanie kluczy słowników w zapisach; porażka zgłasza Unresolved."""

    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.depth = 0

    def keys(self, path: str, owner: str, node):
        self.depth += 1
        try:
            if self.depth > 12:
                raise Unresolved("zbyt głębokie rozwinięcie")
            return self._keys(path, owner, node)
        finally:
            self.depth -= 1

    def _union(self, parts):
        # Suma zachowuje pola jawne także obok zakresu dynamicznego (ANY jest zwykłym elementem).
        return frozenset().union(*parts) if parts else frozenset()

    def _keys(self, path, owner, node):
        text = ast.unparse(node)
        if (path, owner, text) in DYNAMIC:
            self.catalog.used_scopes.add((path, owner, text))
            # Zakres dynamiczny nie zastępuje analizy zmiennej: znane klucze przypisań
            # i aktualizacji w tej funkcji zostają obok niego (M5 review 2).
            return self._union([DYNAMIC[(path, owner, text)][1], literal_keys(node)])
        if isinstance(node, ast.Dict):
            parts = []
            for key, value in zip(node.keys, node.values):
                if key is None:
                    parts.append(self.keys(path, owner, value))
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    parts.append(frozenset({key.value}))
                else:
                    raise Unresolved("klucz niestały: " + ast.unparse(key))
            return self._union(parts)
        if isinstance(node, ast.IfExp):
            return self._union([self.keys(path, owner, node.body), self.keys(path, owner, node.orelse)])
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            return self._union([self.keys(path, owner, v) for v in node.values])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._union([self.keys(path, owner, node.left), self.keys(path, owner, node.right)])
        if isinstance(node, ast.DictComp) and len(node.generators) == 1:
            generator = node.generators[0]
            source = resolve_constant(path, generator.iter)
            if isinstance(source, tuple) and isinstance(generator.target, ast.Name) \
                    and isinstance(node.key, ast.Name) and node.key.id == generator.target.id:
                return frozenset(source)
            if isinstance(generator.iter, ast.Tuple) and isinstance(generator.target, ast.Tuple) \
                    and isinstance(node.key, ast.Name) and all(
                        isinstance(e, ast.Tuple) and isinstance(e.elts[0], ast.Constant) for e in generator.iter.elts):
                return frozenset(e.elts[0].value for e in generator.iter.elts)
            raise Unresolved("wyrażenie słownikowe: " + text)
        if isinstance(node, ast.Name):
            return self._name(path, owner, node)
        if isinstance(node, ast.Call):
            return self._returned(path, node)
        raise Unresolved(text)

    def _name(self, path, owner, node):
        function = enclosing_function(node)
        if function is None or isinstance(function, ast.Lambda):
            raise Unresolved(node.id)
        if function.args.kwarg and function.args.kwarg.arg == node.id:
            return self._kwargs(function)
        assigned = []
        for child in ast.walk(function):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name) and target.id == node.id:
                        assigned.append(self.keys(path, owner, child.value))
                    elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) \
                            and target.value.id == node.id:
                        key = target.slice
                        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                            raise Unresolved("indeks niestały: " + ast.unparse(target))
                        assigned.append(frozenset({key.value}))
                    elif isinstance(target, ast.Tuple) and any(
                            isinstance(e, ast.Name) and e.id == node.id for e in target.elts):
                        raise Unresolved("rozpakowanie krotki: " + node.id)
            elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr == "update" and isinstance(child.func.value, ast.Name) \
                    and child.func.value.id == node.id:
                assigned.extend(self.keys(path, owner, arg) for arg in child.args)
                assigned.extend(self.keys(path, owner, k.value) if k.arg is None else frozenset({k.arg})
                                for k in child.keywords)
            elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr == "setdefault" and isinstance(child.func.value, ast.Name) \
                    and child.func.value.id == node.id:
                key = resolve_constant(path, child.args[0]) if child.args else None
                if not isinstance(key, str):
                    raise Unresolved("setdefault z niestałym kluczem: " + ast.unparse(child))
                assigned.append(frozenset({key}))
            elif isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name) \
                    and child.target.id == node.id:
                if not isinstance(child.op, ast.BitOr):
                    raise Unresolved("nieznana aktualizacja słownika: " + ast.unparse(child))
                assigned.append(self.keys(path, owner, child.value))
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name) \
                    and child.target.id == node.id and child.value is not None:
                assigned.append(self.keys(path, owner, child.value))
        if not assigned:
            raise Unresolved("brak przypisania: " + node.id)
        return self._union(assigned)

    def _kwargs(self, function):
        named = all_params(function)
        parts = []
        for site_path, site in call_sites(function.name):
            site_owner = qualname(site)
            keys = set()
            for keyword in site.keywords:
                if keyword.arg is None:
                    keys |= self.keys(site_path, site_owner, keyword.value)
                elif keyword.arg not in named:
                    keys.add(keyword.arg)
            parts.append(frozenset(keys))
        return self._union(parts)

    def _returned(self, path, call):
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        candidates = definitions().get(name, [])
        if len(candidates) != 1:
            raise Unresolved("wywołanie niejednoznaczne: " + ast.unparse(call))
        source, function = candidates[0]
        owner = qualname(function)
        returns = [n for n in ast.walk(function) if isinstance(n, ast.Return) and n.value is not None
                   and enclosing_function(n) is function]
        if not returns:
            raise Unresolved("brak zwracanego słownika: " + name)
        return self._union([self.keys(source, owner, r.value) for r in returns])


# ------------------------------------------------------------------ katalog zapisów

def _named_journal(node):
    return (isinstance(node, ast.Name) and node.id == "journal") or \
        (isinstance(node, ast.Attribute) and node.attr == "journal")


@lru_cache(maxsize=None)
def _journal_aliases(scope):
    """Jawne aliasy w tym samym zakresie, także łańcuchowe.

    Analiza jest zachowawcza: ponowne przypisanie nie usuwa rozpoznanego pochodzenia.
    Dzięki temu alias nie może ukryć zapisu przed katalogiem.
    """
    if scope is None:
        return frozenset()
    bindings = []
    for child in _function_body_nodes(scope):
        targets = child.targets if isinstance(child, ast.Assign) else \
            [child.target] if isinstance(child, ast.AnnAssign) else []
        if targets and child.value is not None:
            bindings.extend((ast.unparse(target), child.value) for target in targets)
    aliases = set()
    changed = True
    while changed:
        before = len(aliases)
        for name, value in bindings:
            if _named_journal(value) or ast.unparse(value) in aliases:
                aliases.add(name)
        changed = len(aliases) != before
    return frozenset(aliases)


def _journal_reference(node) -> bool:
    return _named_journal(node) or ast.unparse(node) in _journal_aliases(enclosing_function(node))


def _is_card_append(node) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("append", "append_complete")):
        return False
    return _journal_reference(node.func.value)


BOX_RECORD = ("box/state.py", "record")


def _record_reference(path: str, node) -> bool:
    """Odniesienie do zapisującego API `box/state.record`: nazwa w module, import po nazwie
    (także z aliasem lub bezwzględny) albo atrybut aliasu modułu (M6 review 2)."""
    if isinstance(node, ast.Name):
        if path == BOX_RECORD[0] and node.id == "record":
            return True
        return imports(path).get(node.id) == BOX_RECORD
    if isinstance(node, ast.Attribute) and node.attr == "record":
        return imports(path).get(ast.unparse(node.value)) == (BOX_RECORD[0], None)
    return False


def _is_box_record(path: str, node) -> bool:
    return isinstance(node, ast.Call) and _record_reference(path, node.func)


def _record_escapes(path: str, node) -> bool:
    """Odniesienie do `record` poza bezpośrednim wywołaniem (np. `save = bs.record`, przekazanie
    jako argument): zapis przez takie odniesienie nie jest rozwiązywany — zgłaszamy go zachowawczo."""
    if not isinstance(node, (ast.Name, ast.Attribute)) or not _record_reference(path, node):
        return False
    parent = getattr(node, "_parent", None)
    if isinstance(parent, ast.Call) and parent.func is node:
        return False
    if isinstance(parent, ast.Attribute):
        return False    # `record` jako odbiorca dalszego atrybutu nie jest tu wywołaniem zapisu
    if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
        return False    # definicja/przypisanie nazwy, nie odniesienie
    if path == BOX_RECORD[0] and isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return True


def _payload(resolver, path, owner, call, *, skip_star=()):
    parts = [frozenset(k.arg for k in call.keywords if k.arg is not None)]
    for keyword in call.keywords:
        if keyword.arg is None and ast.unparse(keyword.value) not in skip_star:
            parts.append(resolver.keys(path, owner, keyword.value))
    return resolver._union(parts)


def _action_values(path, owner, call, node, catalog):
    """(wartość, reguła) dyskryminatora albo None, gdy pochodzi z parametru opakowania."""
    values = resolve_strings(path, node)
    if values is not None:
        return [(v, "") for v in sorted(values)]
    if isinstance(node, ast.Name):
        guarded = guard_values(call, node.id)
        if guarded:
            return [(v, "guard") for v in sorted(guarded)]
        if (path, owner, node.id) in REPLAY_ACTIONS:
            catalog.used_scopes.add((path, owner, node.id))
            return [("<replay>", "replay")]
        function = enclosing_function(call)
        if function is not None and not isinstance(function, ast.Lambda) and node.id in params(function):
            return None
    raise Unresolved("action=" + ast.unparse(node))


def _work_star(function) -> str:
    """Zmienna wyniku pracy w opakowaniu: `data = work()`."""
    for child in ast.walk(function):
        if isinstance(child, ast.Assign) and isinstance(child.value, ast.Call) \
                and isinstance(child.value.func, ast.Name) and child.value.func.id in params(function) \
                and len(child.targets) == 1 and isinstance(child.targets[0], ast.Name):
            return child.targets[0].id
    return ""


def _wrapper(resolver, path, owner, call, kind, catalog):
    """Zapis w opakowaniu z parametrem `action`: rozwinięcie w każdym miejscu wywołania."""
    function = enclosing_function(call)
    action_name = next(k.value for k in call.keywords if k.arg == "action").id
    kwarg = function.args.kwarg.arg if function.args.kwarg else ""
    work_var = _work_star(function)
    work_param = next((child.value.func.id for child in ast.walk(function)
                       if isinstance(child, ast.Assign) and isinstance(child.value, ast.Call)
                       and isinstance(child.value.func, ast.Name) and child.targets[0].id == work_var), "") \
        if work_var else ""
    skip = {kwarg, work_var, f"{work_var} or {{}}", f"({work_var} or {{}})"} - {""}
    base = _payload(resolver, path, owner, call, skip_star=skip)
    uses_kwarg = any(k.arg is None and ast.unparse(k.value) == kwarg for k in call.keywords)
    uses_work = any(k.arg is None and work_var and ast.unparse(k.value).startswith(work_var) for k in call.keywords)
    named = all_params(function)
    sites = list(call_sites(function.name))
    if not sites:
        raise Unresolved("opakowanie bez wywołań: " + owner)
    for site_path, site in sites:
        site_owner = qualname(site)
        try:
            argument = call_argument(site, function, action_name)
            if argument is None:
                raise Unresolved("brak dyskryminatora w wywołaniu")
            values = _action_values(site_path, site_owner, site, argument, catalog)
            if values is None:
                raise Unresolved("dyskryminator z parametru zewnętrznego: " + ast.unparse(argument))
            fields = base
            if uses_kwarg:
                extra = [frozenset(k.arg for k in site.keywords if k.arg and k.arg not in named)]
                extra += [resolver.keys(site_path, site_owner, k.value) for k in site.keywords if k.arg is None]
                fields = resolver._union([fields, *extra])
            for value, rule in values:
                current = fields
                if uses_work:
                    work = call_argument(site, function, work_param)
                    try:
                        current = resolver._union([current, _work_keys(resolver, site_path, site_owner, work)])
                    except Unresolved:
                        if value not in WORK_BY_ACTION:
                            raise
                        catalog.used_scopes.add(("work", value))
                        current = current | DYN
                catalog.writes.append(Write("card", site_path, site_owner, site.lineno, kind, value, current,
                                            via=f"{path}:{owner}" + (f"/{rule}" if rule else "")))
        except Unresolved as exc:
            catalog.unresolved.append((site_path, site_owner, site.lineno, f"{owner}: {exc}"))


def _work_keys(resolver, path, owner, work):
    if work is None:
        return frozenset()
    if isinstance(work, ast.Lambda):
        return resolver.keys(path, owner, work.body)
    if isinstance(work, ast.Name):
        function = enclosing_function(work)
        nested = [n for n in ast.walk(function) if isinstance(n, ast.FunctionDef) and n.name == work.id]
        if len(nested) == 1:
            inner = qualname(nested[0])
            returns = [n for n in ast.walk(nested[0]) if isinstance(n, ast.Return) and n.value is not None
                       and enclosing_function(n) is nested[0]]
            return resolver._union([resolver.keys(path, inner, r.value) for r in returns])
    raise Unresolved("work=" + ast.unparse(work))


def writes() -> Catalog:
    catalog = Catalog()
    resolver = Resolver(catalog)
    for path in modules():
        for node in ast.walk(module(path)):
            try:
                if _is_card_append(node):
                    _card_write(resolver, path, node, catalog)
                elif _is_box_record(path, node):
                    _box_write(resolver, path, node, catalog)
                elif _record_escapes(path, node):
                    raise Unresolved("odniesienie do box/state.record poza wywołaniem: " + ast.unparse(node))
                elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and (path, node.func.attr) in CALLBACKS.values() and isinstance(node.func.value, ast.Name)):
                    _callback_write(resolver, path, node, catalog)
            except Unresolved as exc:
                catalog.unresolved.append((path, qualname(node), node.lineno, str(exc)))
    return catalog


def _card_write(resolver, path, call, catalog):
    owner = qualname(call)
    if len(call.args) < 2:
        raise Unresolved("brak typu zdarzenia")
    kinds = resolve_strings(path, call.args[1])
    if kinds is None:
        if (path, owner) in CALLBACKS:
            catalog.used_scopes.add((path, owner))
            return   # zapisy rozwinięte w miejscach wywołania zwrotnego
        raise Unresolved("typ=" + ast.unparse(call.args[1]))
    action = next((k.value for k in call.keywords if k.arg == "action"), None)
    for kind in sorted(kinds):
        if kind in ("intent", "result") and action is not None:
            values = _action_values(path, owner, call, action, catalog)
            if values is None:
                _wrapper(resolver, path, owner, call, kind, catalog)
                continue
        else:
            values = [("", "")]
        fields = _payload(resolver, path, owner, call)
        for value, rule in values:
            catalog.writes.append(Write("card", path, owner, call.lineno, kind, value, fields, via=rule))


def _box_write(resolver, path, call, catalog):
    owner = qualname(call)
    if path == "box/state.py" and owner == "record":
        return
    kinds = resolve_strings(path, call.args[1]) if len(call.args) > 1 else None
    if kinds is None:
        raise Unresolved("typ pudełka=" + (ast.unparse(call.args[1]) if len(call.args) > 1 else "?"))
    fields = _payload(resolver, path, owner, call)
    for kind in sorted(kinds):
        catalog.writes.append(Write("box", path, owner, call.lineno, kind, "", fields))


def _callback_write(resolver, path, call, catalog):
    owner = qualname(call)
    for source, target in CALLBACKS.items():
        if target != (path, call.func.attr):
            continue
        kinds = resolve_strings(path, call.args[0]) if call.args else None
        if kinds is None or len(call.args) < 2:
            raise Unresolved("wywołanie zwrotne dziennika")
        fields = resolver.keys(path, owner, call.args[1])
        for kind in sorted(kinds):
            catalog.writes.append(Write("card", path, owner, call.lineno, kind, "", fields, via=":".join(source)))


# ------------------------------------------------------------------ katalog konsumentów i skutków
#
# Odczyty tożsamości: zamknięty słownik prymitywów wspólnego wyniku redukcji i jego bramek
# (nie lista metod macierzy). Każda funkcja pakietu, która ich używa, jest konsumentem;
# bez przypisanej bariery test kompletności kończy się porażką.
IDENTITY_NAMES = frozenset({
    "process_identities", "read_process_history", "preserves_project", "_identityHeld",
    "_process_state", "_process_check", "public_processes", "public_inventory", "active_steps",
    "box_event_error", "start_error", "_validate_process_identity", "_validate_box_event",
    "process_history_checked",
    # Bieżące uruchomienie outside ze wspólnego wyniku (M4/M7 review 2).
    "outside_run", "outside_runs", "current_outside", "has_outside_start", "has_outside_finish",
    "has_outside_request",
})

# Surowe odczyty dowodów: porównanie typu zdarzenia (`e.get("type")`/`e["type"]`) z rodzajem śladu
# procesu. Lista utrzymywana niezależnie od modelu (rodzaje zapisywane przez proces kroku, Konsolę,
# outside i pudełko); `intent`/`result` liczą się, gdy funkcja porównuje też akcję `step-start`.
RAW_PROCESS_TYPES = frozenset({
    "step.started", "step.entered", "step.console_opened", "step.console_closed", "step.decision",
    "step.finished", "outside.started", "outside.finished", "outside.ticket-consent", "box.use", "box.release",
})
RAW_ACTION_TYPES = frozenset({"intent", "result"})
# Atrybuty: (atrybut, wymagany odbiorca albo None).
IDENTITY_ATTRIBUTES = (("held", None), ("outside_unit", None), ("inventory", "trialhold"),
                       ("held", "trialhold"))
# Skutki: (atrybut wywołania, końcówka odbiorcy) → nazwa skutku.
EFFECTS = {
    ("Popen", "subprocess"): "Popen",
    ("open", "terminal"): "Konsola",
    ("start", "runner"): "start kroku",
    ("stop", "incus"): "incus.stop",
    ("start", "incus"): "incus.start",
    ("delete", "incus"): "incus.delete",
    ("device_remove", "incus"): "incus.device_remove",
    ("cleanup", "packs"): "packs.cleanup",
    ("start", "procedure"): "procedura próby",
    ("stop_unit", "stepstop"): "zatrzymanie jednostki",
}


def _function_body_nodes(function):
    stack = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def identity_reads(function) -> frozenset:
    found = set()
    for node in _function_body_nodes(function):
        if isinstance(node, ast.Name) and node.id in IDENTITY_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "held":
            found.add("held")
        elif isinstance(node, ast.Attribute):
            if node.attr in IDENTITY_NAMES:
                found.add(node.attr)
                continue
            receiver = ast.unparse(node.value)
            for attr, wanted in IDENTITY_ATTRIBUTES:
                if node.attr != attr:
                    continue
                if wanted is None and attr == "held" and not receiver.endswith(("identity", "trialhold")):
                    continue
                if wanted is not None and receiver != wanted:
                    continue
                found.add(attr if wanted is None else f"{wanted}.{attr}")
    return frozenset(found)


def _type_read(node) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
            and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "type":
        return True
    return isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "type"


def _literals(node) -> set:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return set()


def raw_reads(function) -> frozenset:
    """Porównania typu zdarzenia, także przez łańcuch lokalnych przypisań."""
    found, actions = set(), False
    nodes = list(_function_body_nodes(function))
    aliases = set()

    def is_type(node):
        return _type_read(node) or isinstance(node, ast.Name) and node.id in aliases

    def bind(target, value):
        if isinstance(target, ast.Name) and is_type(value):
            aliases.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for left, right in zip(target.elts, value.elts):
                bind(left, right)

    changed = True
    while changed:
        changed = False
        for node in nodes:
            targets = node.targets if isinstance(node, ast.Assign) else \
                [node.target] if isinstance(node, ast.AnnAssign) else []
            before = len(aliases)
            for target in targets:
                bind(target, node.value)
            changed |= len(aliases) != before
    for node in nodes:
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            names = set().union(*(_literals(o) for o in operands))
            if any(is_type(o) for o in operands):
                found |= names & (RAW_PROCESS_TYPES | RAW_ACTION_TYPES)
            actions |= "step-start" in names
    if not actions:
        found -= RAW_ACTION_TYPES
    return frozenset(found)


def raw_readers() -> list:
    out = []
    for path in modules():
        if path == "cardflow/processidentity.py":
            continue
        for node in ast.walk(module(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                reads = raw_reads(node)
                if reads:
                    out.append(Consumer(path, qualname(node), node.lineno, reads))
    return out


def consumers() -> list:
    out = []
    for path in modules():
        if path == "cardflow/processidentity.py":
            continue
        for node in ast.walk(module(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                reads = identity_reads(node)
                if reads:
                    out.append(Consumer(path, qualname(node), node.lineno, reads))
    return out


# Producenci wspólnego wyniku: liczą lub ściśle walidują historię, ale nie decydują o skutku.
# Ich wywołanie nie jest delegacją (odczyt wyniku wykrywa `identity_reads` u wywołującego).
PRODUCERS = frozenset({("cardflow/state.py", "fold"), ("cardflow/journal.py", "Journal.read_complete"),
                       ("cardflow/journal.py", "_validate_process_identity")})


@dataclass(frozen=True)
class Delegation:
    path: str
    function: str
    line: int
    calls: frozenset    # (ścieżka, funkcja) wywołanych konsumentów/delegacji


def _functions():
    out = {}
    for path in modules():
        for node in ast.walk(module(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[(path, qualname(node))] = node
    return out


def _enclosing_class(node):
    current = getattr(node, "_parent", None)
    while current is not None and not isinstance(current, ast.ClassDef):
        current = getattr(current, "_parent", None)
    return current


def _class_of(path, function, receiver):
    """Nazwa klasy, gdy odbiorca to `Klasa(...)` albo zmienna lokalna przypisana z `Klasa(...)`."""
    def called_class(node):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            return name if name[:1].isupper() else ""
        return ""
    if isinstance(receiver, ast.Call):
        return called_class(receiver)
    if isinstance(receiver, ast.Name) and not isinstance(function, ast.Lambda):
        # Adnotacja typu parametru (`lc: lifecycle.Lifecycle`, `lc: "Lifecycle"`) — M7 review 2.
        arguments = function.args
        for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
            if arg.arg == receiver.id and arg.annotation is not None:
                note = arg.annotation
                if isinstance(note, ast.Constant) and isinstance(note.value, str):
                    return note.value.split(".")[-1].split("|")[0].strip()
                if isinstance(note, ast.BinOp):     # `Klasa | None`
                    note = note.left
                if isinstance(note, ast.Name):
                    return note.id
                if isinstance(note, ast.Attribute):
                    return note.attr
    if isinstance(receiver, ast.Name):
        for child in _function_body_nodes(function):
            if isinstance(child, ast.Assign) and any(isinstance(t, ast.Name) and t.id == receiver.id
                                                     for t in child.targets):
                name = called_class(child.value)
                if name:
                    return name
    return ""


def call_targets(path, function, by_name, ambiguous=None) -> set:
    """Rozwiązane cele wywołań funkcji: nazwy modułu/importów, self/cls, alias modułu,
    `Klasa(...).metoda`, a dla pozostałych odbiorców — nazwa jednoznaczna w pakiecie.
    Wywołania niejednoznacznych metod (np. `stop`) nie są rozwiązywane; skutek w wywoływanej
    funkcji ma wtedy własną bezpośrednią kontrolę (katalog skutków, EFFECT_SCOPE)."""
    targets = set()
    for call in _function_body_nodes(function):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Name):
            target = imports(path).get(func.id)
            if target and target[1]:
                targets |= {k for k in by_name.get(target[1], []) if k[0] == target[0]}
            else:
                targets |= {k for k in by_name.get(func.id, []) if k[0] == path and "." not in k[1]}
            continue
        if not isinstance(func, ast.Attribute):
            continue
        receiver, candidates = func.value, by_name.get(func.attr, [])
        if isinstance(receiver, ast.Name) and receiver.id in ("self", "cls"):
            owner = _enclosing_class(call)
            if owner is not None:
                targets |= {k for k in candidates if k[0] == path and k[1] == f"{owner.name}.{func.attr}"}
            continue
        if isinstance(receiver, ast.Name) and receiver.id in imports(path) and imports(path)[receiver.id][1] is None:
            targets |= {k for k in candidates if k[0] == imports(path)[receiver.id][0]}
            continue
        owner = _class_of(path, function, receiver)
        typed = {k for k in candidates if k[1] == f"{owner}.{func.attr}"} if owner else set()
        if typed:
            targets |= typed
        elif owner and owner[:1].isupper():
            continue    # znana klasa odbiorcy bez takiej metody w pakiecie (np. Thread.start)
        elif len(candidates) == 1:
            targets |= set(candidates)
        else:
            # Wskazówka z nazwy odbiorcy: `self.engine.x` → FlowEngine.x, `builder.reset` → Builder.reset.
            hint = ast.unparse(receiver).split(".")[-1].strip("_").lower()
            hinted = {k for k in candidates if "." in k[1] and hint in k[1].split(".")[-2].lower()} \
                if len(hint) >= 4 else set()
            targets |= hinted
            if not hinted and candidates and ambiguous is not None:
                # Nierozstrzygnięte wywołanie metody o nazwie funkcji pakietu: rozliczane zachowawczo
                # przez `ambiguous_calls` (M7 review 2), nie pomijane milcząco.
                ambiguous.append((call, frozenset(candidates)))
    return targets


@dataclass(frozen=True)
class Ambiguous:
    path: str
    function: str
    line: int
    call: str           # tekst wywołania (odbiorca.metoda)
    candidates: frozenset


@lru_cache(maxsize=None)
def _graph_cached():
    functions = _functions()
    by_name = {}
    for key, node in functions.items():
        by_name.setdefault(node.name, []).append(key)
    ambiguous = {}
    targets = {}
    for key, node in functions.items():
        if key[0] == "cardflow/processidentity.py":
            continue
        found = []
        targets[key] = call_targets(key[0], node, by_name, found)
        ambiguous[key] = found
    return functions, targets, ambiguous


_graph = _graph_cached


def ambiguous_calls() -> list:
    """Wywołania bez rozstrzygniętego celu, których możliwy cel jest konsumentem albo delegacją
    (M7 review 2). Każde musi mieć jawną regułę odbiorcy w teście; nowa niejednoznaczna ścieżka
    do konsumenta bez reguły daje porażkę zamiast milczącego pominięcia."""
    _, _, ambiguous = _graph()
    reached = {(c.path, c.function) for c in consumers()} | {(d.path, d.function) for d in delegations()}
    out = []
    for (path, function), found in sorted(ambiguous.items()):
        for call, candidates in found:
            hit = candidates & reached
            if hit:
                out.append(Ambiguous(path, function, call.lineno, ast.unparse(call.func), frozenset(hit)))
    return out


def delegations() -> list:
    """Funkcje, które (pośrednio) wywołują konsumenta tożsamości: builder/require_idle, CLI/D-Bus,
    bramka diagnostyki. Domknięcie grafu wywołań od konsumentów z `consumers()` bez producentów."""
    functions, targets, _ = _graph()
    reached = {(c.path, c.function) for c in consumers()} - PRODUCERS
    found = {}
    changed = True
    while changed:
        changed = False
        for key, called in targets.items():
            if key in reached:
                continue
            hit = called & reached
            if hit:
                reached.add(key)
                found[key] = frozenset(hit)
                changed = True
    return [Delegation(path, name, functions[(path, name)].lineno, calls) for (path, name), calls in sorted(found.items())]


def effects() -> list:
    out = []
    for path in modules():
        for node in ast.walk(module(path)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            receiver = ast.unparse(node.func.value)
            for (attr, suffix), effect in EFFECTS.items():
                if node.func.attr == attr and receiver.split(".")[-1].endswith(suffix):
                    out.append(Effect(path, qualname(node), node.lineno, effect))
    return out
