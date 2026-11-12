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
trwających: nowe ruszają dopiero, gdy trwających jest mniej niż limit. Priorytet (#8) i podbicie (#11) dojdą przez
klucz `order`.

**Cisza (K-39 #6, PT-376).** Polecenie wymagające ciszy (`Waiting.exclusive`: krok dopisał do wyniku
`"exclusive": true` albo polecenie pasuje do `quietCommands`, `pattern_matches`) jest zawsze ciężkie i:

- rusza tylko przy spełnionym warunku `quiet`: brak trwających ciężkich i `loadavg1` mniejsze niż liczba rdzeni / 2
  (odczyt wstrzykiwany, `read_load`; błąd odczytu to warunek niespełniony z powodem — nigdy start);
- gdy trwa (`exclusive_running`) albo właśnie rusza, żadne inne ciężkie polecenie nie startuje;
- gdy czeka na czele kolejki (pierwszy wpis w kolejności, który nie ruszył), nowsze ciężkie też nie startują
  (opróżnianie), żeby nie czekało w nieskończoność. Wpis wymagający ciszy dalej w kolejce niczego nie wstrzymuje.

Lekkie polecenia i kroki agentów nie przechodzą przez `admit`, więc cisza ich nie wstrzymuje. Powód wstrzymania
(`Admission.held`) to `HELD_LIMIT` albo zdanie usługi o ciszy (`quiet_hold`).
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
    """Czekające ciężkie polecenie: nazwa procesu (krok albo pseudokrok próby), czas zgody (epoka), czy wymaga ciszy
    (#6) i karta (do zdania o opróżnianiu kolejki)."""
    name: str
    queued: float
    exclusive: bool = False
    card: str = ""


@dataclass(frozen=True)
class Load:
    """Odczyt obciążenia maszyny: `avg1` — 1-minutowe `loadavg`, `cores` — liczba rdzeni, `error` — powód błędu
    odczytu (wtedy warunek ciszy nie jest spełniony)."""
    avg1: float | None = None
    cores: int | None = None
    error: str = ""

    @property
    def threshold(self) -> float | None:
        return self.cores / 2 if self.cores else None

    def as_dict(self) -> dict:
        """Kształt `queue.heavy.load` migawki."""
        return {"avg1": self.avg1, "cores": self.cores, "threshold": self.threshold, "error": self.error}


@dataclass(frozen=True)
class Quiet:
    """Wynik warunku ciszy: `ok` — można uruchomić polecenie wymagające ciszy, `reason` — zdanie usługi, gdy nie."""
    ok: bool
    reason: str
    load: Load


@dataclass(frozen=True)
class Admission:
    """`admitted` — nazwy, które mogą ruszyć (w kolejności), `held` — nazwa → powód wstrzymania (`HELD_LIMIT` albo
    zdanie o ciszy), `busy` — trwające ciężkie, `limit` — obowiązujący limit, `load` — odczyt obciążenia (None, gdy
    w kolejce nie było polecenia wymagającego ciszy i nic go nie czytało)."""
    admitted: tuple
    held: dict = field(default_factory=dict)
    busy: int = 0
    limit: int = DEFAULT_LIMIT
    load: Load | None = None


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


def quiet_patterns_problem(value) -> str:
    """Opis błędu `quietCommands` do `bad-settings`; pusty — poprawna lista (może być pusta)."""
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return "wzorce poleceń wymagających ciszy muszą być listą napisów"
    if any(not item.strip() or item.strip() == "*" for item in value):
        return "wzorzec polecenia wymagającego ciszy nie może być pusty"
    return ""


def quiet_patterns_of(value) -> list[str]:
    """`quietCommands` z ustawień przepływu (#6); brak klucza albo niepoprawna wartość — lista pusta."""
    if value is None or quiet_patterns_problem(value):
        return []
    return [item.strip() for item in value]


def exclusive_of(value) -> bool:
    """Pole `exclusive` wyniku kroku: tylko JSON-owe `true` oznacza polecenie wymagające ciszy; zły typ — `false`."""
    return value is True


def needs_quiet(command: str, roots=(), patterns: Iterable[str] = (), *, marked: bool = False) -> bool:
    """Czy polecenie wymaga ciszy: krok je oznaczył (`marked`) albo pasuje do wzorca `quietCommands` (ta sama funkcja
    dopasowania co `heavyCommands`, argumenty z `roots` i normalizacją)."""
    return bool(marked) or pattern_matches(command_argv(command, roots), patterns, roots)


# --- warunek ciszy (#6) ----------------------------------------------------------------------------

LOAD_FILE = "/proc/loadavg"

QUIET_LOAD = "czeka na ciszę: obciążenie {avg1} przy progu {threshold}"
QUIET_RUNNING = "czeka na ciszę: biegnie {running}"
QUIET_ERROR = "czeka na ciszę: {error}"
HELD_DRAIN = "wstrzymane: na czele kolejki czeka polecenie wymagające ciszy ({card})"
HELD_EXCLUSIVE = "wstrzymane: biegnie polecenie wymagające ciszy: {running}"
QUIET_PREFIXES = ("czeka na ciszę: ", "wstrzymane: ")


def read_load(path: str = LOAD_FILE, cpu_count: Callable[[], int | None] = os.cpu_count) -> Load:
    """Domyślny odczyt obciążenia: pierwsza liczba `/proc/loadavg` i `os.cpu_count()`. Błąd odczytu nie rzuca
    wyjątku — wraca jako `Load.error` (warunek ciszy niespełniony)."""
    try:
        with open(path, encoding="ascii") as fh:
            avg1 = float(fh.read().split()[0])
    except (OSError, ValueError, IndexError, UnicodeError) as exc:
        return Load(error=f"nie da się odczytać obciążenia maszyny ({type(exc).__name__})")
    if avg1 != avg1 or avg1 < 0:
        return Load(error="nie da się odczytać obciążenia maszyny (zła wartość)")
    try:
        cores = cpu_count()
    except Exception:
        cores = None
    if isinstance(cores, bool) or not isinstance(cores, int) or cores < 1:
        return Load(avg1=avg1, error="nie da się odczytać liczby rdzeni")
    return Load(avg1=avg1, cores=cores)


def _decimal(value: float) -> str:
    """Liczba w zdaniu usługi: jedna cyfra po przecinku (polski zapis), np. 7,1."""
    return f"{value:.1f}".replace(".", ",")


def measure(load: Callable[[], Load]) -> Load:
    try:
        found = load()
    except Exception as exc:
        return Load(error=f"nie da się odczytać obciążenia maszyny ({type(exc).__name__})")
    if not isinstance(found, Load):
        return Load(error="nie da się odczytać obciążenia maszyny")
    if not found.error and (found.avg1 is None or found.threshold is None):
        return Load(found.avg1, found.cores, "nie da się odczytać obciążenia maszyny")
    return found


def quiet(running: Iterable[str], *, describe: Callable[[str], str] = str,
          load: Callable[[], Load] = read_load) -> Quiet:
    """Warunek ciszy: brak trwających ciężkich (`running`) i `loadavg1` < rdzenie / 2. `describe(nazwa)` daje opis
    trwającego do zdania („make test (K-36)”). Obciążenie jest czytane zawsze (do widoku), a błąd odczytu albo jego
    brak to warunek niespełniony z powodem."""
    names = sorted(set(running))
    current = measure(load)
    if names:
        return Quiet(False, QUIET_RUNNING.format(running=", ".join(describe(n) for n in names)), current)
    if current.error:
        return Quiet(False, QUIET_ERROR.format(error=current.error), current)
    if not current.avg1 < current.threshold:
        return Quiet(False, QUIET_LOAD.format(avg1=_decimal(current.avg1), threshold=_decimal(current.threshold)),
                     current)
    return Quiet(True, "", current)


def quiet_hold(reason) -> bool:
    """Czy powód wstrzymania pochodzi z ciszy (a nie z limitu). Wpis wstrzymany ciszą nie trzyma kolejki zgód
    (`_outside_behind`): lekkie polecenia i inne projekty idą dalej."""
    return isinstance(reason, str) and reason.startswith(QUIET_PREFIXES)


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
          order: Callable[[Waiting], tuple] = default_order, exclusive_running: Iterable[str] = (),
          describe: Callable[[str], str] = str, load: Callable[[], Load] = read_load) -> Admission:
    """Które czekające ciężkie polecenia mogą ruszyć przy limicie `limit`. `running` — nazwy trwających ciężkich
    (żywy zbiór silnika po każdym starcie w ticku). Każda decyzja startu liczy wolne miejsca od nowa, więc zmniejszony
    limit nie przerywa trwających, a nowe ruszają dopiero, gdy trwających jest mniej niż limit.

    Cisza (#6): `exclusive_running` — trwające wymagające ciszy (podzbiór `running`), `describe(nazwa)` — opis
    polecenia do zdania, `load` — odczyt obciążenia (czytany najwyżej raz i tylko przy czekającym poleceniu
    wymagającym ciszy). Bez wpisów `exclusive` wynik jest taki sam jak przed #6."""
    running = set(running)
    busy = len(running)
    limit = limit_of(limit)
    queue = sorted({w.name: w for w in waiting}.values(), key=order)
    free = max(0, limit - busy)
    measured: list[Load] = []

    def load_once() -> Load:
        if not measured:
            measured.append(measure(load))
        return measured[0]

    exclusive_now = sorted(set(exclusive_running) & running)
    # `gate` — zdanie wstrzymujące wszystkie dalsze ciężkie: trwa albo właśnie rusza polecenie wymagające ciszy,
    # albo takie polecenie czeka na czele kolejki (opróżnianie).
    gate = HELD_EXCLUSIVE.format(running=", ".join(describe(n) for n in exclusive_now)) if exclusive_now else ""
    admitted: list[str] = []
    held: dict = {}
    for entry in queue:
        if entry.exclusive:
            state = quiet(running | set(admitted), describe=describe, load=load_once)
            if state.ok and not gate:
                admitted.append(entry.name)
                free -= 1
                gate = HELD_EXCLUSIVE.format(running=describe(entry.name))
                continue
            head = not held and not gate
            held[entry.name] = state.reason if state.reason else gate
            if head:
                gate = HELD_DRAIN.format(card=entry.card or entry.name)
            continue
        if gate:
            held[entry.name] = gate
        elif free > 0:
            admitted.append(entry.name)
            free -= 1
        else:
            held[entry.name] = HELD_LIMIT
    return Admission(tuple(admitted), held, busy, limit, measured[0] if measured else None)


def holds_queue(heavy: bool, busy: int, limit: int) -> bool:
    """Czy starsza czekająca zgoda może trzymać w kolejce młodsze (reguła czasu zgody, `_outside_behind`).
    Ciężka przy zajętym limicie ciężkich i tak nie ruszy, więc nie blokuje lekkich ani innych projektów."""
    return not heavy or busy < limit_of(limit)


STATUS_SUFFIX =" (ciężkie polecenia: {busy} z {limit} zajęte)"


def queued_label(label: str, busy: int, limit: int) -> str:
    """Napis statusu ciężkiego polecenia w kolejce: `w kolejce: <polecenie> (ciężkie polecenia: 2 z 2 zajęte)`.
    Prefiks `w kolejce: ` zostaje (napis statusu; prognozę okna liczy pole `queue` migawki, `queueview`)."""
    return label + STATUS_SUFFIX.format(busy=busy, limit=limit)
