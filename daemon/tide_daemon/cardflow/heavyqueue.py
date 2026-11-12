"""Kolejka ciężkich poleceń na maszynę (K-39 #3, PT-373): czyste reguły, bez stanu i bez wejścia-wyjścia.

10.11 sześć zgód „uruchamiaj zawsze” dało sześć `make test-changed` naraz i wszystkie przekroczyły 60 minut. Tide
pilnuje więc, żeby na maszynie biegło najwyżej `heavyLimit` ciężkich poleceń naraz, niezależnie od projektu, karty
i rodzaju zgody. Silnik (`engine.py`) podaje tylko dane wejściowe: polecenie, korzenie, efektywny kontrakt testów
projektu, czekające wpisy kolejki i żywy zbiór trwających (`_outside_active`); zasada jest tutaj.

`classify(command, roots, contract, patterns, merge_check=…)` — polecenie jest ciężkie, gdy:

- to próba testów po scaleniu (K-88, `merge_check=True`) — zawsze;
- pasuje do rodziny `tests.changed` albo `tests.full` efektywnego kontraktu testów projektu. Rodzina to argumenty
  polecenia kontraktu do pierwszego parametru (`{base}`), porównywane słowo po słowie z początkiem polecenia
  (parser zgód `outside_argv`, bez początkowych przypisań): `make test-changed BASE=<dowolny SHA> JOBS=4` pasuje do
  `make test-changed BASE={base}`, `make test` do `make test`, a `make test-cardflow` ani `make lint` nie pasują.

- pasuje do wzorca `heavyCommands` (#12, PT-382; domyślnie `DEFAULT_PATTERNS`): budowy, testy innych technologii
  i pomiary. Dopasowanie jest jedną funkcją, `pattern_matches` (użyje jej też `quietCommands`, #6).

Argumenty biorą się z `outside_argv(command, roots)`, a `normalize` (tylko do klasyfikacji, polecenie uruchamia się bez
zmian) sprowadza interpreter podany ścieżką do `python3`, a `bash`/`sh <skrypt z roots>` do `bash <ścieżka względna>`.
Pozostałe polecenia są lekkie; skrypt spoza `roots` nie pasuje do żadnego wzorca.

`admit(waiting, running, limit, order=…)` — które czekające ciężkie polecenia mogą ruszyć: tyle, ile wolnych miejsc
(`limit` minus trwające), w kolejności klucza porządku (dziś czas zgody, potem nazwa). Zmniejszony limit nie przerywa
trwających: nowe ruszają dopiero, gdy trwających jest mniej niż limit. Priorytet (#8), podbicie (#11) i cisza (#6)
dojdą przez klucz `order` i powód wstrzymania.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

DEFAULT_LIMIT = 2
MIN_LIMIT, MAX_LIMIT = 1, 6
BASE_PARAM = "{base}"

# Domyślne `heavyCommands` (rodziny z końcowym ` *`; `make test *` dopasowuje cel `test`, a nie `test-cardflow`,
# dlatego cele są wyliczone).
DEFAULT_PATTERNS = tuple(f"{family} *" for family in (
    "make test", "make test-changed", "make test-cardflow", "make test-daemon", "make test-board", "make mist-golden",
    "npm test", "npm run build", "npm run e2e", "npx playwright test", "dotnet test", "dotnet build", "go test",
    "cargo test", "pytest", "python3 -m pytest", "python3 -m unittest", "python3 tools/test_runner.py",
    "docker build", "docker run", "docker compose up", "docker compose build"))

# Powody (napisy stałe do dziennika usługi i testów).
LIGHT = ""
MERGE_CHECK = "merge-check"
TESTS_CHANGED = "tests.changed"
TESTS_FULL = "tests.full"
PATTERN = "pattern"
HELD_LIMIT = "limit"


@dataclass(frozen=True)
class Verdict:
    """Wynik klasyfikacji: `heavy` i powód (`tests.changed`, `tests.full`, `merge-check` albo pusty)."""
    heavy: bool
    reason: str = LIGHT


@dataclass(frozen=True)
class Waiting:
    """Czekające ciężkie polecenie: nazwa procesu (krok albo pseudokrok próby) i czas zgody (epoka)."""
    name: str
    queued: float


@dataclass(frozen=True)
class Admission:
    """`admitted` — nazwy, które mogą ruszyć (w kolejności), `held` — nazwa → powód wstrzymania,
    `busy` — trwające ciężkie, `limit` — obowiązujący limit."""
    admitted: tuple
    held: dict = field(default_factory=dict)
    busy: int = 0
    limit: int = DEFAULT_LIMIT


def limit_of(value) -> int:
    """`heavyLimit` z ustawień przepływu; brak albo wartość spoza 1..6 (także bool i ułamek) — domyślne 2."""
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_LIMIT <= value <= MAX_LIMIT:
        return DEFAULT_LIMIT
    return value


def limit_problem(value) -> str:
    """Opis błędu `heavyLimit` do `bad-settings`; pusty — poprawna wartość."""
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_LIMIT <= value <= MAX_LIMIT:
        return f"liczba ciężkich poleceń naraz musi być liczbą całkowitą od {MIN_LIMIT} do {MAX_LIMIT}"
    return ""


def _contract_commands(contract) -> list[tuple[str, str]]:
    if contract is None:
        return []
    get = contract.get if isinstance(contract, dict) else lambda key: getattr(contract, key, "")
    return [(reason, str(get(key) or "").strip()) for reason, key in ((TESTS_CHANGED, "changed"), (TESTS_FULL, "full"))]


PYTHON_NAME = re.compile(r"python(3(\.\d+)?)?")


def _relative_script(path: str, roots) -> str:
    """Ścieżka skryptu względem worktree albo repozytorium projektu (`normpath`, bez `./`)."""
    base = path if path.startswith("/") else os.path.join(roots[0] or "", path)
    real = os.path.realpath(base)
    for root in roots:
        if root and (real == os.path.realpath(root) or real.startswith(os.path.realpath(root) + os.sep)):
            return os.path.relpath(real, os.path.realpath(root))
    return os.path.normpath(path)


def normalize(argv: list[str] | None, roots=()) -> list[str]:
    """Postać polecenia do klasyfikacji (nie do uruchomienia): interpreter podany ścieżką (`/usr/bin/python3`,
    `.venv/bin/python`, `python3.12`) to `python3`, a `sh`/`bash <skrypt z roots>` to `bash <ścieżka względna>`.
    Początkowe przypisania i `env NAZWA=…` zdejmuje już `outside_argv`."""
    from ..board.commands import script_in_roots
    if not argv:
        return []
    if script_in_roots(argv, roots):
        return ["bash", _relative_script(argv[1], roots), *argv[2:]]
    if PYTHON_NAME.fullmatch(os.path.basename(argv[0])):
        return ["python3", *argv[1:]]
    return list(argv)


def command_argv(command: str, roots=()) -> list[str]:
    """Znormalizowane argumenty polecenia (jak zgoda przy starcie: `outside_argv` z `roots`); puste dla łańcucha,
    podpowłoki, zakazanej zmiennej albo `bash`/`sh` ze skryptem spoza `roots`."""
    from ..board.commands import outside_argv
    return normalize(outside_argv(command, roots), roots)


def _family(command: str, roots) -> list[str]:
    """Rodzina polecenia kontraktu: jego znormalizowane argumenty do pierwszego parametru `{base}` (bez niego)."""
    argv = command_argv(command, roots)
    head = []
    for token in argv:
        if BASE_PARAM in token:
            break
        head.append(token)
    return head


def _matches(argv: list[str], family: list[str]) -> bool:
    return bool(family) and argv[:len(family)] == family


def pattern_matches(argv: list[str], patterns: Iterable[str], roots=()) -> bool:
    """Jedyna funkcja dopasowania wzorców poleceń (`heavyCommands`, w #6 także `quietCommands`). `argv` — wynik
    `command_argv`. Wzorzec ma format zgód „zawsze w projekcie”: rodzina z końcowym ` *` (prefiks argumentów) albo
    dokładne polecenie (całe argumenty). Wzorzec jest parsowany tak jak polecenie (z `roots`, z normalizacją), więc
    `bash daemon/tests/x.sh *` pasuje do `sh ./daemon/tests/x.sh a` i do ścieżki bezwzględnej w worktree."""
    if not argv:
        return False
    for pattern in patterns or ():
        text = str(pattern or "").strip()
        family = text.endswith(" *")
        words = command_argv(text[:-2] if family else text, roots)
        if not words:
            continue
        if (argv[:len(words)] == words) if family else (argv == words):
            return True
    return False


def default_patterns() -> list[str]:
    return list(DEFAULT_PATTERNS)


def patterns_problem(value) -> str:
    """Opis błędu `heavyCommands` do `bad-settings`; pusty — poprawna lista (może być pusta)."""
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return "wzorce ciężkich poleceń muszą być listą napisów"
    if any(not item.strip() or item.strip() == "*" for item in value):
        return "wzorzec ciężkiego polecenia nie może być pusty"
    return ""


def patterns_of(value) -> list[str]:
    """`heavyCommands` z ustawień przepływu; brak klucza albo niepoprawna wartość — lista domyślna."""
    if value is None or patterns_problem(value):
        return default_patterns()
    return [item.strip() for item in value]


def classify(command: str, roots=(), contract=None, patterns: Iterable[str] = (), *,
             merge_check: bool = False) -> Verdict:
    """Czy polecenie jest ciężkie. `contract` — efektywny kontrakt testów projektu (`{"changed", "full"}` albo
    `testpolicy.TestPolicy` z nierozwiniętym `{base}`), `roots` — worktree ticketu, repozytorium projektu (i katalog
    kroku), `patterns` — wzorce `heavyCommands` (`patterns_of`), `merge_check` — próba testów po scaleniu (zawsze
    ciężka). Argumenty jak przy zgodzie na start (`outside_argv` z `roots`), potem `normalize`."""
    if merge_check:
        return Verdict(True, MERGE_CHECK)
    argv = command_argv(command, roots)
    if not argv:
        return Verdict(False)
    for reason, declared in _contract_commands(contract):
        if declared and _matches(argv, _family(declared, roots)):
            return Verdict(True, reason)
    if pattern_matches(argv, patterns, roots):
        return Verdict(True, PATTERN)
    return Verdict(False)


def default_order(entry: Waiting) -> tuple:
    """Dzisiejszy porządek kolejki: czas zgody, przy remisie nazwa (stabilnie między tickami)."""
    return (entry.queued, entry.name)


def admit(waiting: Iterable[Waiting], running: Iterable[str], limit: int, *,
          order: Callable[[Waiting], tuple] = default_order) -> Admission:
    """Które czekające ciężkie polecenia mogą ruszyć przy limicie `limit`. `running` — nazwy trwających ciężkich
    (żywy zbiór silnika po każdym starcie w ticku). Każda decyzja startu liczy wolne miejsca od nowa, więc zmniejszony
    limit nie przerywa trwających, a nowe ruszają dopiero, gdy trwających jest mniej niż limit."""
    busy = len(set(running))
    limit = limit_of(limit)
    queue = sorted({w.name: w for w in waiting}.values(), key=order)
    free = max(0, limit - busy)
    admitted = tuple(w.name for w in queue[:free])
    held = {w.name: HELD_LIMIT for w in queue[free:]}
    return Admission(admitted, held, busy, limit)


def holds_queue(heavy: bool, busy: int, limit: int) -> bool:
    """Czy starsza czekająca zgoda może trzymać w kolejce młodsze (reguła czasu zgody, `_outside_behind`).
    Ciężka przy zajętym limicie ciężkich i tak nie ruszy, więc nie blokuje lekkich ani innych projektów."""
    return not heavy or busy < limit_of(limit)


STATUS_SUFFIX =" (ciężkie polecenia: {busy} z {limit} zajęte)"


def queued_label(label: str, busy: int, limit: int) -> str:
    """Napis statusu ciężkiego polecenia w kolejce: `w kolejce: <polecenie> (ciężkie polecenia: 2 z 2 zajęte)`.
    Prefiks `w kolejce: ` zostaje (napis statusu; prognozę okna liczy pole `queue` migawki, `queueview`)."""
    return label + STATUS_SUFFIX.format(busy=busy, limit=limit)
