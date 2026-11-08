"""Efektywny kontrakt testów kroku karty (K-69, PT-279): jakie testy kartka każe uruchomić i skąd to wiadomo.

`effective_tests(contract, worktree, approved, base)` to jedyna decyzja. Kolejność źródeł:

1. zatwierdzony kontrakt projektu (`tests.changed` / `tests.full` z profilu, `approved=True`) — źródło `profile`;
   brakujące pole uzupełnia zasada ogólna, nie wykrycie;
2. bez zastosowanego kontraktu — wykrycie celów `make` worktree (`make_targets`): `test-changed` →
   `make test-changed BASE={base}`, `test` → `make test` — źródło `detected`; cele badane niezależnie;
3. reszta — zasada ogólna projektu (CLAUDE.md/AGENTS.md) bez polecenia — źródło `rules`.

Zatwierdzenie jest wejściem (wartość logiczna): decyzja nie liczy i nie porównuje odcisku kontraktu — kto i jak
je liczy dla kroku, podłącza silnik (#10) przez `approval_for_step` (#9). Kontrakt niezatwierdzony działa jak brak
kontraktu (`unapproved` mówi kartce, żeby to napisała), uszkodzony profil (`error`) — jak brak kontraktu.

`legacy` = gałąź z celem `test` bez `test-changed`: pełny `make test` w tickecie, poprawkach i review. Przy
zatwierdzonym `make test-changed …` na takiej gałęzi też `legacy`; gdy gałąź nie ma żadnego z celów, `changed`
przechodzi na zasadę ogólną (`missing`), żeby kartka nie podała nieistniejącego polecenia.

`master_policy` to cienka funkcja zgodności dla silnika do czasu #10: odtwarza dotychczasową regułę master
(bez kontraktu; brak `Makefile` → `make test-changed` i `make test` jak dotąd, ze źródłem `compat` bez linii
źródła w kartce), więc zachowanie kroków się nie zmienia, dopóki silnik nie woła `effective_tests`.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import asdict, dataclass

PROFILE, DETECTED, RULES, COMPAT = "profile", "detected", "rules", "compat"
BASE_PARAM = "{base}"
DETECTED_CHANGED = "make test-changed BASE={base}"
DETECTED_FULL = "make test"
CHANGED_TARGET, FULL_TARGET = "test-changed", "test"
MAKEFILE_MAX = 1 << 20

_RULE = re.compile(r"^([^\s:#=][^:#=]*?)[ \t]*(:{1,2})(.*)$")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NAME = re.compile(r"^[A-Za-z0-9_.+/-]+$")


@dataclass(frozen=True)
class TestPolicy:
    """Polityka testów kroku. `changed` — polecenie ticketu/poprawek/review (z podstawioną bazą albo z `{base}`,
    gdy `base_pending`), `full` — polecenie domknięcia; pusty napis = zasada ogólna projektu.
    `declared_*` — polecenia zatwierdzonego kontraktu tak, jak je zapisano (do linii o brakującym celu)."""
    __test__ = False  # nie jest klasą testów mimo nazwy

    changed: str = ""
    full: str = ""
    source: str = RULES
    changed_source: str = RULES
    full_source: str = RULES
    legacy: bool = False
    unapproved: bool = False
    base: str = ""
    base_pending: bool = False
    missing: tuple = ()
    declared_changed: str = ""
    declared_full: str = ""

    def as_dict(self) -> dict:
        """Postać do zapisu w zdarzeniu kroku (`testPolicy`, #10)."""
        data = asdict(self)
        data["missing"] = list(self.missing)
        return {_camel(key): value for key, value in data.items()}


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _inside(root: str, path: str) -> bool:
    real = os.path.realpath(path)
    return real == root or real.startswith(root + os.sep)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(MAKEFILE_MAX)
    except OSError:
        return ""


def make_targets(directory: str) -> list[str]:
    """Cele reguł z `Makefile` i `mk/*.mk` katalogu, posortowane. Tylko odczyt plików (nic nie uruchamia);
    pliki i katalog `mk`, które przez dowiązanie wychodzą poza katalog, są pomijane. Brak katalogu — pusta lista."""
    if not directory or not os.path.isdir(directory):
        return []
    root = os.path.realpath(directory)
    paths = [os.path.join(directory, "Makefile")]
    mk = os.path.join(directory, "mk")
    if os.path.isdir(mk) and _inside(root, mk):
        try:
            names = sorted(os.listdir(mk))
        except OSError:
            names = []
        paths += [os.path.join(mk, name) for name in names if name.endswith(".mk") and not name.startswith(".")]
    targets = set()
    for path in paths:
        if not os.path.isfile(path) or not _inside(root, path):
            continue
        for line in _read(path).splitlines():
            match = _RULE.match(line)
            if not match or match.group(3).startswith("="):  # `X := …`, `X ::= …` to przypisania, nie reguły
                continue
            targets.update(name for name in match.group(1).split() if _NAME.match(name))
    return sorted(targets)


def make_target(command: str) -> str:
    """Cel `make`, od którego zaczyna się polecenie (`make test-changed BASE=…` → `test-changed`), po zmiennych
    środowiska na początku; "" dla poleceń innych niż `make <cel>`."""
    try:
        tokens = shlex.split(command or "")
    except ValueError:
        return ""
    while tokens and _ASSIGN.match(tokens[0]):
        tokens = tokens[1:]
    return tokens[1] if len(tokens) > 1 and tokens[0] == "make" else ""


def _expand(command: str, base: str) -> tuple[str, bool]:
    if BASE_PARAM not in command:
        return command, False
    if not base:
        return command, True
    return command.replace(BASE_PARAM, shlex.quote(base)), False


def _policy(changed, changed_source, full, full_source, base, **rest) -> TestPolicy:
    changed, pending = _expand(changed, base)
    sources = (changed_source, full_source)
    source = PROFILE if PROFILE in sources else DETECTED if DETECTED in sources else \
        COMPAT if COMPAT in sources else RULES
    return TestPolicy(changed=changed, full=full, source=source, changed_source=changed_source,
                      full_source=full_source, base=base, base_pending=pending, **rest)


def effective_tests(contract, worktree: str, approved: bool, base: str = "", *, error: str = "") -> TestPolicy:
    """Efektywny kontrakt testów kroku. `contract` — `{"changed", "full"}` z profilu projektu (albo None),
    `error` — błąd odczytu profilu (`testsError`): uszkodzony profil = brak kontraktu. `base` — przypięty punkt
    startu ticketu; bez niego `{base}` zostaje nierozwinięte (`base_pending`)."""
    data = contract if isinstance(contract, dict) and not error else {}
    declared = {name: str(data.get(name) or "").strip() for name in ("changed", "full")}
    present = any(declared.values())
    applied = present and approved is True
    targets = set(make_targets(worktree))
    if not applied:
        legacy = FULL_TARGET in targets and CHANGED_TARGET not in targets
        changed, changed_source = (DETECTED_FULL, DETECTED) if legacy else \
            (DETECTED_CHANGED, DETECTED) if CHANGED_TARGET in targets else ("", RULES)
        full, full_source = (DETECTED_FULL, DETECTED) if FULL_TARGET in targets else ("", RULES)
        return _policy(changed, changed_source, full, full_source, base, legacy=legacy, unapproved=present)
    changed, changed_source = (declared["changed"], PROFILE) if declared["changed"] else ("", RULES)
    full, full_source = (declared["full"], PROFILE) if declared["full"] else ("", RULES)
    legacy, missing = False, []
    if changed and make_target(changed) == CHANGED_TARGET and CHANGED_TARGET not in targets:
        if FULL_TARGET in targets:
            legacy, changed, changed_source = True, DETECTED_FULL, DETECTED
        else:
            missing.append("changed")
            changed, changed_source = "", RULES
    if full and make_target(full) == FULL_TARGET and FULL_TARGET not in targets:
        missing.append("full")
        full, full_source = "", RULES
    return _policy(changed, changed_source, full, full_source, base, legacy=legacy, missing=tuple(missing),
                   declared_changed=declared["changed"], declared_full=declared["full"])


def master_policy(worktree: str = "", base: str = "") -> TestPolicy:
    """Zgodność z master do czasu #10: projekt bez kontraktu, reguła sprzed K-69. Gałąź z `test` bez
    `test-changed` — `legacy`; każda inna (także bez `Makefile` i bez katalogu) — `make test-changed`, a domknięcie
    zawsze `make test`. Polecenie niepotwierdzone celem ma źródło `compat` (kartka nie pisze linii źródła)."""
    targets = set(make_targets(worktree))
    legacy = FULL_TARGET in targets and CHANGED_TARGET not in targets
    changed_source = DETECTED if legacy or CHANGED_TARGET in targets else COMPAT
    full_source = DETECTED if FULL_TARGET in targets else COMPAT
    return _policy(DETECTED_FULL if legacy else DETECTED_CHANGED, changed_source, DETECTED_FULL, full_source,
                   base, legacy=legacy)
