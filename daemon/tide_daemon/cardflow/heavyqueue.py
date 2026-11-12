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

Pozostałe polecenia są w tym tickecie lekkie. `roots` i `patterns` (wzorce `heavyCommands`, tu zawsze puste) są
w interfejsie już teraz, żeby #12 rozszerzył dopasowanie bez zmiany wywołań w silniku.

`admit(waiting, running, limit, order=…)` — które czekające ciężkie polecenia mogą ruszyć: tyle, ile wolnych miejsc
(`limit` minus trwające), w kolejności klucza porządku (dziś czas zgody, potem nazwa). Zmniejszony limit nie przerywa
trwających: nowe ruszają dopiero, gdy trwających jest mniej niż limit. Priorytet (#8), podbicie (#11) i cisza (#6)
dojdą przez klucz `order` i powód wstrzymania.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

DEFAULT_LIMIT = 2
MIN_LIMIT, MAX_LIMIT = 1, 6
BASE_PARAM = "{base}"

# Powody (napisy stałe do dziennika usługi i testów).
LIGHT = ""
MERGE_CHECK = "merge-check"
TESTS_CHANGED = "tests.changed"
TESTS_FULL = "tests.full"
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


def _family(command: str, roots) -> list[str]:
    """Rodzina polecenia kontraktu: jego argumenty do pierwszego parametru `{base}` (bez niego). Puste — brak."""
    from ..board.commands import outside_argv
    argv = outside_argv(command, roots) or []
    head = []
    for token in argv:
        if BASE_PARAM in token:
            break
        head.append(token)
    return head


def _matches(argv: list[str], family: list[str]) -> bool:
    return bool(family) and argv[:len(family)] == family


def _pattern_matches(argv: list[str], patterns: Iterable[str]) -> bool:
    """Wzorce `heavyCommands` (#12). W tym tickecie lista jest zawsze pusta; tu tylko miejsce na regułę."""
    for pattern in patterns or ():
        words = str(pattern or "").split()
        if words and words[-1] == "*":
            words = words[:-1]
        if words and argv[:len(words)] == words:
            return True
    return False


def classify(command: str, roots=(), contract=None, patterns: Iterable[str] = (), *,
             merge_check: bool = False) -> Verdict:
    """Czy polecenie jest ciężkie. `contract` — efektywny kontrakt testów projektu (`{"changed", "full"}` albo
    `testpolicy.TestPolicy` z nierozwiniętym `{base}`), `roots` — worktree, repozytorium projektu i katalog kroku,
    `patterns` — wzorce `heavyCommands` (#12; dziś puste), `merge_check` — próba testów po scaleniu (zawsze ciężka)."""
    if merge_check:
        return Verdict(True, MERGE_CHECK)
    from ..board.commands import outside_argv
    argv = outside_argv(command, roots)
    if not argv:
        return Verdict(False)
    for reason, declared in _contract_commands(contract):
        if declared and _matches(argv, _family(declared, roots)):
            return Verdict(True, reason)
    if _pattern_matches(argv, patterns):
        return Verdict(True, "pattern")
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
    Prefiks `w kolejce: ` zostaje (decision.js liczy po nim czekające zgody, `OUTSIDE_QUEUED_PREFIX`)."""
    return label + STATUS_SUFFIX.format(busy=busy, limit=limit)
