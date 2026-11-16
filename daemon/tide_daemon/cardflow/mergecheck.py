"""Testy po scaleniu ticketu do gałęzi karty (K-88, PT-318): wykrycie, polecenie i bramka karty.

Konflikt semantyczny dwóch ticketów (09.11: PT-264×PT-288) wychodził dopiero w kolejnym tickecie, bo każdy ticket
testował się tylko na własnym punkcie startu. Po scaleniu, które nie jest „fast-forward” względem punktu startu
ticketu (`before != start`), Tide zakłada próbę testów zmian ticketu na nowym wierzchołku gałęzi karty i do jej
wyniku trzyma bramkę karty.

Zdarzenia (dziennik karty):
- `merge.check-armed` — raz na kartę; scalenia zapisane wcześniej (sprzed wdrożenia bramki) nie zakładają prób,
- `merge.check-requested {ticket, check, start, before, after, command, source, fallback?, mergeSeq}` — próba
  `check` = `<ticket>:<n>`; próba bez polecenia ma `unavailable: true` zamiast `command` (problem karty
  `merge-check-unavailable`). Zdarzenia „pominięto” nie ma: brak polecenia nigdy nie przepuszcza karty,
- `merge.check-step {step, ticket, kind, check, command, after, branch, worktree, stepDir, baseVersion}` —
  pseudokrok próby z poleceniem (PT-329): `step` = `merge-check-<ticket>-<n>` (`step_name`), start procesu w modelu
  tożsamości (processidentity), więc decyzja `step.decision` i przebieg tej nazwy (od K-109 PT-483 protokół
  jednego wykonania `command.intent` → `command.started` → `command.finished` z `source: merge-check`; próby
  sprzed zmiany: `outside.started`/`outside.finished`) są zwykłym śladem procesu, a nie wpisem bez startu. Nie jest krokiem karty (`st.steps`): nie pracuje,
  nie wznawia się i nie dostaje review,
- `step.decision {step: merge-check-…, ticket, kind: "merge-check", check, choice, command, auto?}` — zgoda albo
  odmowa jednej próby; ta sama ścieżka co prośba `outside` kroku (reguły projektu, `consent`, PT-311),
- `merge.check-finished {ticket, check, exit, log, failedModules, failedTests}` — wynik próby (zapisuje go przebieg
  pod zgodą, PT-329); `exit: 0` bieżącej próby otwiera bramkę, każda inna wartość daje problem `merge-check-failed`,
  wynik nieaktualnej próby nic nie zmienia. Zamiar porzucony bez startu (`command.finished` z `reason: not-started`)
  zapisuje tu `exit: zatrzymane` — porażkę, nie udaną próbę. Odmowa zgody bieżącej próby daje `merge-check-denied`.
  `failedModules`/`failedTests` (K-100, PT-423) to wyłącznie diagnostyka z `summary.json` uruchamiacza
  (`failed_from_summary`); brak lub błąd raportu daje puste listy, a wpisy bez tych pól czyta się jak dotąd,
- `merge.check-accepted {ticket, check, after, by}` — „Akceptuj mimo porażki” (PT-320): jawna decyzja człowieka
  (`ResolveCardStep`, `choice: accept-merge-check`) o dokładnie tej próbie (`check`) na dokładnie tym wierzchołku
  (`after`), przy problemie `merge-check-failed`, `merge-check-denied` albo `merge-check-unavailable`. Otwiera bramkę
  tylko dla tej próby: kolejna próba ticketu (ponowienie) albo próba innego scalenia znowu ją trzyma. Akceptacja
  nieaktualnej próby (inny `check` albo `after`) niczego nie zmienia także przy odczycie. Nie uruchamia polecenia.

Polecenie jest stałym napisem Tide (bez łańcucha poleceń), z bazą = pełny SHA `start` (nie `before`):
kontrakt testów projektu `tests.changed` (`{base}` → start, źródło `contract`), bez kontraktu cele `Makefile`
gałęzi karty czytane z gita bez uruchamiania `make` (`test-changed` → `make test-changed BASE=<start>`, sam `test`
→ `make test` z `fallback: "full"`), a bez nich — próba niedostępna. Kontrakt, który po podstawieniu nie jest
pojedynczym poleceniem (łańcuch, podpowłoka, powłoka — także wskazana ścieżką), daje próbę niedostępną z
`reason: "contract-not-single"`.
Próbę zakłada każde scalenie z `before != start`, z jednym wyjątkiem: `start..before` to wyłącznie commity planu
zapisane przez Tide na tej karcie, z których każdy zmienia tylko nagłówek i tabelę stanu w pliku planu
(`FlowEngine._only_tide_plan_commits`, `plan_state_only`; ręczny tekst albo dopisek kartki → próba).
"""
from __future__ import annotations

import json
import os
import re
import shlex

from . import testpolicy
from .testrerun import HEADER as RUNNER_HEADER

ARMED, REQUESTED, FINISHED = "merge.check-armed", "merge.check-requested", "merge.check-finished"
STEP = "merge.check-step"
ACCEPTED = "merge.check-accepted"
UNAVAILABLE, FAILED, PENDING = "merge-check-unavailable", "merge-check-failed", "pending"
DENIED = "merge-check-denied"
PROBLEMS = (UNAVAILABLE, FAILED, DENIED)      # stany próby z wyjściem przez ponowienie albo akceptację
ACCEPT = "accept-merge-check"
KIND, STEP_PREFIX = "merge-check", "merge-check-"
RUN = ("run", "run-always", "run-ticket")
CONTRACT, MAKEFILE = "contract", "makefile"
CONTRACT_NOT_SINGLE = "contract-not-single"
BASE_PARAM = "{base}"
CHANGED_TARGET, FULL_TARGET = "test-changed", "test"
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

FAILED_TESTS_MAX = 200     # najwięcej nazw testów w `failedTests` jednego wyniku (dziennik karty, nie raport)
_FAILED_KEYS = ("failures", "errors", "unexpected_successes")


def failed_from_summary(log: str) -> tuple[list[str], list[str]]:
    """Padnięte moduły i testy próby (K-100, PT-423) z `summary.json` uruchamiacza testów projektu: katalog raportu
    wskazuje nagłówek przebiegu w pełnym wyniku próby (`log`), ten sam co przy ponowieniu (`testrerun.HEADER`).
    Brak lub błąd dziennika, nagłówka albo raportu, inny schemat — puste listy, bez wyjątku: pole jest wyłącznie
    diagnostyczne i nie zmienia wyniku próby."""
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            header = RUNNER_HEADER.search(fh.read())
        with open(os.path.join(header.group(2), "summary.json"), encoding="utf-8") as fh:
            results = json.load(fh)["results"]
        modules, tests = [], []
        for item in results if isinstance(results, list) else ():
            if not isinstance(item, dict) or not isinstance(item.get("module"), str) or not item.get("returncode"):
                continue
            modules.append(item["module"])
            for key in _FAILED_KEYS:
                names = item.get(key)
                tests += [n for n in names if isinstance(n, str)] if isinstance(names, list) else []
        return modules, tests[:FAILED_TESTS_MAX]
    except Exception:   # noqa: BLE001 — błąd odczytu raportu nie może zatrzymać zapisu wyniku próby
        return [], []


def make_targets(texts) -> set[str]:
    """Cele reguł z tekstów `Makefile` i `mk/*.mk` — ten sam parser co wykrycie testów kroku (K-69): jedynym
    czytnikiem plików `make` w przepływie karty jest `testpolicy`."""
    return testpolicy.text_targets(texts)


# Programy, które same uruchamiają inne polecenia (powłoki, przełączniki uruchamiania) — odrzucane po nazwie
# pliku także wtedy, gdy kontrakt wskazuje je ścieżką (`/bin/sh -c …`, `/usr/bin/env sh -c …`; review r2, MAJOR-1).
_LAUNCHERS = frozenset({
    "sh", "bash", "zsh", "fish", "dash", "ksh", "mksh", "pdksh", "ash", "csh", "tcsh", "yash", "busybox", "toybox",
    "eval", "exec", "env", "command", "builtin", "sudo", "doas", "su", "nohup", "setsid", "nice", "ionice",
    "timeout", "stdbuf", "xargs", "chroot", "unshare", "nsenter", "runuser", "script", "flock",
    "awk", "gawk", "mawk", "nawk",
})
_INTERPRETERS = ("python", "perl", "ruby", "node", "php", "lua", "tclsh")


def _plan_parts(text: str):
    """Plan Tide rozbity na (nagłówek, tekst przed tabelą, tabela, tekst po tabeli) albo None, gdy plik nie ma
    nagłówka `---` i tabeli `| Ticket |` (ta sama budowa co `FlowEngine._plan_extra`)."""
    lines = str(text or "").splitlines()
    if len(lines) < 2 or lines[0].strip() != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    body = lines[end + 1:]
    start = next((i for i, line in enumerate(body) if line.startswith("| Ticket |")), None)
    if start is None:
        return None
    stop = start
    while stop < len(body) and body[stop].startswith("|"):
        stop += 1
    return lines[:end + 1], body[:start], body[start:stop], body[stop:]


def plan_state_only(old: str, new: str) -> bool:
    """Commit planu zmienia tylko nagłówek i tabelę ticketów — części, które Tide generuje w całości ze stanu karty.
    Tekst przed tabelą i po niej (ręczny, zachowywany przez `_plan_files`) musi być identyczny."""
    before, after = _plan_parts(old), _plan_parts(new)
    return before is not None and after is not None and before[1] == after[1] and before[3] == after[3]


def _interpreter_file_or_module(name: str, args: list[str]) -> bool:
    """Dopuszczamy skrypt albo moduł Pythona, nie kod w argumencie ani wejście standardowe.

    Rozpoznajemy tylko jawny podzbiór opcji Pythona. Ich wartości nie są nazwami skryptów: po `-W ignore`
    i `-X dev` nadal sprawdzamy następne opcje. Nieznane opcje, klastry i postaci `--opcja=…` odrzucamy
    zachowawczo; pozostałe interpretery obsługujemy wyłącznie jako `program plik argumenty`.
    """
    python = re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", name) is not None
    at = 0
    while at < len(args):
        arg = args[at]
        if arg and not arg.startswith("-"):
            return True  # Reszta należy do skryptu, a nie do interpretera.
        if not python:
            return False
        if arg in ("-m", "--"):
            return at + 1 < len(args) and bool(args[at + 1]) and not args[at + 1].startswith("-")
        if arg in ("-W", "-X"):
            if at + 1 >= len(args) or not args[at + 1] or args[at + 1].startswith("-"):
                return False
            at += 2
        elif arg in ("-B", "-E", "-I", "-s", "-S", "-u"):
            at += 1
        else:
            return False
    return False


def single_command(command: str) -> bool:
    """Jedno polecenie bez łańcucha (`;`, `&&`, `|`, przekierowań), podpowłoki (`$(…)`, `` ` ``, `( … )`), powłoki
    (`sh -c`, `bash`, także wskazanej ścieżką: `/bin/sh`, `/usr/bin/env`), `env`/`sudo` i zmiennych zmieniających
    uruchamianie (`PATH=`, `LD_*`) — kryterium polecenia w kolejce poza piaskownicą (`board.commands.outside_argv`,
    bez skryptów z worktree), zaostrzone o nazwę pliku programu. Samej kolejki outside to nie zmienia."""
    from ..board.commands import OUTSIDE_MAX, outside_argv
    if not command or len(command) > OUTSIDE_MAX:
        return False
    argv = outside_argv(command)
    if not argv:
        return False
    name = argv[0].rsplit("/", 1)[-1]
    if not name or name in _LAUNCHERS:
        return False
    # Opcje wykonujące kod (także klastry i `--eval=…`) nie mogą ukrywać łańcucha w cytowanym argumencie.
    if name.startswith(_INTERPRETERS):
        return _interpreter_file_or_module(name, argv[1:])
    return True


def select_command(contract_changed: str, targets, start: str) -> dict:
    """Polecenie próby: `{command, source, fallback?}` albo bez `command` — próba niedostępna: `{}` (brak polecenia)
    albo `{source: "contract", reason: "contract-not-single"}` (kontrakt nie jest pojedynczym poleceniem; nie
    przechodzimy wtedy na Makefile — zatwierdzony kontrakt jest wyborem projektu, a jego błąd ma być widoczny).
    `contract_changed` — pole `tests.changed` zatwierdzonego kontraktu projektu (pusty napis: brak kontraktu),
    `targets` — cele `make` gałęzi karty, `start` — pełny SHA punktu startu ticketu. Bez znanego `start` polecenie
    nie powstaje."""
    if not isinstance(start, str) or not SHA.fullmatch(start):
        return {}
    changed = str(contract_changed or "").strip()
    if changed:
        command = changed.replace(BASE_PARAM, shlex.quote(start))
        if not single_command(command):
            return {"source": CONTRACT, "reason": CONTRACT_NOT_SINGLE}
        return {"command": command, "source": CONTRACT}
    targets = set(targets or ())
    if CHANGED_TARGET in targets:
        return {"command": f"make {CHANGED_TARGET} BASE={start}", "source": MAKEFILE}
    if FULL_TARGET in targets:
        return {"command": f"make {FULL_TARGET}", "source": MAKEFILE, "fallback": "full"}
    return {}


def is_pass(exit_code) -> bool:
    """Tylko liczba 0 jest udanym wynikiem (`False`, `"0"`, `"zatrzymane"`, `"przekroczony czas"` — nie)."""
    return type(exit_code) is int and exit_code == 0


def step_name(check: str) -> str:
    """Nazwa pseudokroku próby: `PT-2:1` → `merge-check-PT-2-1` (jedna próba — jedna nazwa i jedna decyzja)."""
    return STEP_PREFIX + str(check or "").replace(":", "-")


def is_step(name) -> bool:
    return isinstance(name, str) and name.startswith(STEP_PREFIX)


def denied(decision) -> bool:
    """Bieżąca decyzja pseudokroku to odmowa (człowieka; Tide nie odmawia próbie sam)."""
    return (decision or {}).get("choice") == "deny"


def status(item: dict, decisions=None) -> str:
    """Stan bieżącej próby ticketu: "" (bramka otwarta — wynik 0 albo akceptacja tej próby, PT-320), `pending`,
    `merge-check-unavailable`, `merge-check-failed`, `merge-check-denied`."""
    if item.get("accepted"):
        return ""
    return problem(item, decisions)


def problem(item: dict, decisions=None) -> str:
    """Stan próby bez akceptacji: "" (wynik 0), `pending`, `merge-check-unavailable`, `merge-check-failed`,
    `merge-check-denied` (`decisions` — `st.step_decisions`; odmowa przed wynikiem)."""
    requested = item.get("requested") or {}
    finished = item.get("finished")
    if requested.get("unavailable"):
        return UNAVAILABLE
    if finished is None:
        if denied((decisions or {}).get(step_name(requested.get("check")))):
            return DENIED
        return PENDING
    return "" if is_pass(finished.get("exit")) else FAILED


def gate(st) -> list[dict]:
    """Próby, które trzymają bramkę karty (w kolejności zapisu): `{ticket, check, status, requested, finished}`.
    Pusta lista — bramka otwarta."""
    out = []
    decisions = getattr(st, "step_decisions", None) or {}
    for ticket, item in (getattr(st, "merge_checks", None) or {}).items():
        state = status(item, decisions)
        if state:
            out.append({"ticket": ticket, "check": item["requested"].get("check") or "", "status": state,
                        "requested": item["requested"], "finished": item.get("finished")})
    out.sort(key=lambda entry: int(entry["requested"].get("seq") or 0))
    return out


def phase(st, entry: dict) -> str:
    """Etap czekającej próby z poleceniem: `consent` (pytanie o zgodę w panelu), `queued` (zgoda, przed startem
    w kolejce outside), `running` (polecenie trwa) albo "" (pseudokrok jeszcze niezapisany)."""
    name = step_name(entry.get("check"))
    # Zamiar przed startem (`command.intent`, K-109 PT-483) jest w `outside_started`: polecenie mogło już ruszyć.
    if name in (getattr(st, "outside_launched", None) or ()) or name in (getattr(st, "outside_started", None) or ()):
        return "running"
    if (st.step_decisions.get(name) or {}).get("choice") in RUN:
        return "queued"
    return "consent" if name in (getattr(st, "merge_check_steps", None) or {}) else ""


def closed(st) -> bool:
    return bool(gate(st))


def next_check(st, ticket: str) -> str:
    return f"{ticket}:{int((getattr(st, 'merge_check_counts', None) or {}).get(ticket, 0)) + 1}"


def fold_event(st, e: dict) -> None:
    """Odczyt zdarzeń próby w `state.fold`."""
    kind = e.get("type")
    if kind == ARMED:
        if not st.merge_check_armed:
            st.merge_check_armed = int(e.get("seq") or 0) or -1
    elif kind == REQUESTED:
        ticket = e.get("ticket") or ""
        if not ticket:
            return
        # Nowa próba (ponowienie albo kolejne scalenie) zaczyna bez akceptacji — ta dotyczyła poprzedniej próby.
        st.merge_checks[ticket] = {"requested": e, "finished": None, "accepted": None}
        st.merge_check_counts[ticket] = st.merge_check_counts.get(ticket, 0) + 1
        if e.get("mergeSeq") is not None:
            st.merge_check_merges.add((ticket, e.get("mergeSeq")))
    elif kind == STEP:
        if is_step(e.get("step")):
            st.merge_check_steps.setdefault(e["step"], e)
    elif kind == FINISHED:
        item = st.merge_checks.get(e.get("ticket") or "")
        # Wynik nieaktualnej próby (inny `check`) i powtórny wynik tej samej próby niczego nie zmieniają.
        if item and item["finished"] is None and not item["requested"].get("unavailable") and \
                e.get("check") == item["requested"].get("check"):
            item["finished"] = e
    elif kind == ACCEPTED:
        item = st.merge_checks.get(e.get("ticket") or "")
        requested = (item or {}).get("requested") or {}
        # Tylko bieżąca próba na swoim wierzchołku, raz; usługa sprawdza to samo przed zapisem (`accept_merge_check`).
        if item and not item.get("accepted") and e.get("check") == requested.get("check") and \
                e.get("after") == requested.get("after"):
            item["accepted"] = e
