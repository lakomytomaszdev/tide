"""Polecenia poza piaskownicą (`outside`) i próba testów po scaleniu (`merge-check`): decyzja o starcie, kolejka,
uruchomienie, zbieranie wyniku i odzyskiwanie po restarcie (K-109 PT-478, PT-484; ADR-1, ADR-6 w docs/adr/).

Jawne wejścia zamiast wspólnego `self`:

- `state` — stan przebiegów w pamięci (`CommandsState`): trwające, kolejka zgód, klasa ciężkości i ciszy, scalenia
  „fast-forward” bez próby. Jedna instancja należy do `FlowEngine` (pole `_commands_state`); moduł nie trzyma stanu
  modułowego ani globalnego.
- `ports` — dziennik, zegar, projekt karty, git, kontrola próby i pozostałe zależności silnika jako wywoływalne
  (`Ports`). Wywołania między funkcjami tego modułu też idą przez `ports`: silnik podaje w nich własne cienkie
  wrappery `_outside*` i `_merge_check*`, więc podmiana wrappera w teście działa tak jak przed wydzieleniem.
- limity (`OUTSIDE_TIMEOUT`, `OUTSIDE_PER_PROJECT`…) przychodzą w argumentach z wrappera silnika.

Moduł nie importuje `engine`. Zdarzenia (`outside.finished`, `merge.check-*`), jednostka i argv, kolejka i odmowy są
te same co przed wydzieleniem.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from ..tasks import agentcmd, stepstop
from . import boxstep, consent, heavyqueue, mergecheck, processidentity, testpolicy, trialhold
from .errors import FlowError
from .state import _epoch, ticket_of


@dataclass
class CommandsState:
    """Stan przebiegów poza piaskownicą w pamięci usługi (odbudowywany z dzienników po restarcie)."""
    active: dict | None = None      # krok → (karta, jednostka); budowane z dzienników przy pierwszym użyciu
    waiting: dict = field(default_factory=dict)  # krok → (karta, projekt, czas zgody, ostatnie sprawdzenie, ciężkie)
    heavy: dict = field(default_factory=dict)    # krok → ciężkie (bool): klasa czekającego albo trwającego (PT-373)
    quiet: dict = field(default_factory=dict)    # krok → wymaga ciszy (bool, K-39 #6)
    held: dict = field(default_factory=dict)     # krok → (zajęte, limit) albo zdanie o ciszy: ciężkie wstrzymane
    seeded: bool = False                         # kolejka odtworzona z dzienników po restarcie
    logged: dict = field(default_factory=dict)   # krok → ostatni powód czekania wypisany w dzienniku usługi
    # (karta, ticket, seq wyniku) scaleń „fast-forward” bez próby testów po scaleniu — bez ponownego gita (PT-484).
    merge_ff: set = field(default_factory=set)


class StateAlias:
    """Dotychczasowa nazwa pola silnika (`_outside_active`, `_merge_check_ff`…) jako widok pola `CommandsState`
    właściciela.

    Zgodność dla kodu spoza tego wydzielenia (testy, `rest.py`, `consent.py`, `_start_outside`):
    odczyt i przypisanie trafiają do jednego obiektu stanu. Silnik zbudowany bez `__init__` (testy przez `__new__`)
    dostaje pusty stan przy pierwszym dostępie."""

    def __init__(self, name: str, holder: str = "_commands_state"):
        self.name, self.holder = name, holder

    def state(self, owner) -> CommandsState:
        return owner.__dict__.setdefault(self.holder, CommandsState())

    def __get__(self, owner, cls=None):
        if owner is None:
            return self
        return getattr(self.state(owner), self.name)

    def __set__(self, owner, value) -> None:
        setattr(self.state(owner), self.name, value)


@dataclass(frozen=True)
class Ports:
    """Zależności funkcji modułu podane przez silnik (dziennik, zegar, odczyt projektu, start jednostki…).

    Parametr `ports` funkcji jest celowo bez adnotacji typu, a pola `outside_*` mają nazwy funkcji tego modułu:
    statyczny katalog tożsamości (daemon/tests/cardflow_identity_catalog.py) rozwiązuje wtedy `ports.outside_busy(…)`
    do `outside_busy` i widzi te same ścieżki do konsumentów historii co przed wydzieleniem. Pozostałe nazwy pól nie
    pokrywają się z funkcjami pakietu (bez fałszywych krawędzi)."""
    journal: object
    wall_clock: Callable[[], float]
    project: Callable           # karta → (pozycja Tablicy, projekt)
    trial_check: Callable       # krok → None albo FlowError (zablokowana próba)
    consent_before_start: Callable  # (karta, krok, wynik, decyzja) → bool: świeża kontrola zgody (consent.before_start)
    record_automatic: Callable  # (karta, krok, polecenie, gate) → automatyczna zgoda (consent.record_automatic)
    start_outside: Callable     # (karta, krok, polecenie) → start jednostki (`_start_outside`)
    seed: Callable              # (teraz) → kolejka z dzienników po restarcie (`_seed_outside_waiting`)
    heavy_admitted: Callable    # (karta, krok) → bool
    heavy_held: Callable        # () → dict: wstrzymane ciężkie (`_heavy_held_state`)
    heavy_running: Callable     # () → set
    heavy_limit: Callable       # () → int
    quiet_held: Callable        # () → set
    heavy_contract: Callable    # (karta, worktree) → kontrakt testów do klasyfikacji
    heavy_patterns: Callable    # () → list
    quiet_patterns: Callable    # () → list
    outside_history: Callable
    outside_busy: Callable
    outside_behind: Callable
    outside_log: Callable
    outside_heavy_of: Callable
    outside_heavy_state: Callable
    outside_quiet_state: Callable
    outside_class: Callable
    outside_collect: Callable
    outside_stopped: Callable
    outside_exit: Callable
    outside_recheck_rules: Callable
    # Próba testów po scaleniu (PT-484): git i Tablica silnika, a nazwy `merge_check_*`/`outside_launch` jak funkcje
    # tego modułu — silnik podaje w nich swoje wrappery `_merge_check*` (punkty patchowania testów).
    record_check_automatic: Callable  # (karta, pseudokrok, polecenie, próba) → automatyczna zgoda z polem `check`
    git: object                     # FlowGit: `run(katalog, *argumenty)` → wynik z `ok`/`out`
    store: object                   # Tablica: `root`, `approvals_path` (zatwierdzenie kontraktu testów, K-69)
    card_repo: Callable             # karta → repozytorium projektu karty (`_repo`)
    request_check: Callable         # (karta, stan, ticket, wynik scalenia) → próba albo None (`_request_merge_check`)
    plan_commits_only: Callable     # (repo, stan, start, przed) → bool (`_only_tide_plan_commits`)
    outside_launch: Callable
    merge_check_waiting: Callable
    merge_check_step: Callable
    merge_check_history: Callable
    merge_check_run: Callable
    merge_check_finish: Callable
    merge_check_still_allowed: Callable
    merge_check_head: Callable
    merge_check_start: Callable
    merge_check_repo: Callable


def queue_heavy(entry) -> bool:
    """Pole `heavy` wpisu kolejki outside (PT-373); wpis bez pola (starszy kształt) — lekki."""
    return len(entry) > 4 and bool(entry[4])


def test_pool_available(worktree: str) -> bool:
    """Czy uruchamiacz testów w worktree korzysta ze wspólnej puli maszyny. Brak uruchamiacza (inny projekt) — tak:
    nie zmieniamy środowiska projektów, które nie używają tools/test_runner.py."""
    path = os.path.join(worktree, "tools", "test_runner.py") if worktree else ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "class MachineSlots" in fh.read()
    except OSError:
        return True


# --- zgody i historia ----------------------------------------------------------------------------

def outside_remembered(settings: dict, slug: str) -> list:
    from ..board.commands import outside_pattern
    value = (settings.get("outsideCommands") or {}).get(slug) or []
    # Wpisy sprzed rodzin (dokładne polecenia) liczą się jako ich rodzina — tak rozumiał to użytkownik.
    return [c if c.endswith(" *") else outside_pattern(c) for c in value if isinstance(c, str)] \
        if isinstance(value, list) else []


def outside_history(ports,card: str, step: dict | None = None, *, command: str | None = None,
                    fresh: bool = True):
    """Niepełna historia nie uprawnia do zgody, startu ani sprzątania outside.

    K-105 PT-415: bez `step` (bariera ticku, odczyt bez skutku) — historia z pamięci według wersji dziennika;
    z `step`/`command` (bramka zgody i startu outside) — świeży pełny odczyt i nowe złożenie stanu. `fresh=False`
    tylko dla obserwacji w każdym ticku (krok czeka na kolejkę, wynik albo regułę), po której każdy skutek
    (zgoda, start, zapis wyniku, wznowienie) ma własne świeże wywołanie tej bramki."""
    try:
        if step is None:
            return trialhold.read_process_history(ports.journal, card)
        events, st = trialhold.process_state(ports.journal, card, fresh=fresh)
        name = step.get("step") or ""
        recorded = st.steps.get(name)
        if recorded is None or any(recorded.get(key) != step.get(key) for key in
                                   ("ticket", "kind", "runner", "worktree", "stepDir", "branch")):
            raise ValueError("tożsamość outside nie zgadza się z zapisanym krokiem")
        ports.trial_check(recorded)
        if command is not None:
            done = st.finished.get(name) or {}
            decision = st.step_decisions.get(name) or {}
            if (done.get("result") != "denied" or not done.get("outside")
                    or done.get("command") != command
                    or decision.get("choice") not in ("run", "run-always", "run-ticket")
                    or ("command" in decision and decision["command"] != command)
                    or name in st.outside_started):
                raise ValueError("brak aktualnej prośby i zgody na start outside")
        return events
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise FlowError("outside-history", f"nieznana historia outside — wykonanie wstrzymane: {exc}") from None


def outside_recheck(ports,card: str, step: dict, done: dict) -> None:
    """Prośba sprzed zmiany reguł (np. `E2E_PORT=… npm run e2e` odrzucane jako łańcuch, rodziny zamiast dokładnych
    poleceń): gdy dziś pasuje do zapamiętanej rodziny projektu, uruchamia się bez pytania."""
    if trialhold.held(step):
        return
    try:
        # K-105 PT-415: sprawdzenie co tick z pamięci; każdy zapis niżej dopiero po świeżej bramce (`gate`).
        ports.outside_history(card, step, fresh=False)
    except FlowError:
        return

    def gate():
        ports.outside_history(card, step)
    try:
        ports.outside_recheck_rules(card, step, done, gate)
    except FlowError:
        return


def outside_recheck_rules(ports,card: str, step: dict, done: dict, gate) -> None:
    if step.get("runner") == boxstep.RUNNER:
        if done.get("canAllow", True):
            gate()
            ports.journal.append(card, "permission.rechecked", step=step.get("step") or "",
                                 ticket=step.get("ticket") or "", canAllow=False)
        return
    from ..board.commands import outside_allowed
    command = done.get("command") or ""
    _, project = ports.project(card)
    roots = (step.get("worktree") or "", project.repo, step.get("stepDir") or "")
    allowed = outside_allowed(command, project.denied_commands, roots)
    if allowed != bool(done.get("canAllow", True)):
        # Reguły zmieniły się od wyniku kroku (K-33/PT-110: `find -exec … \;` uznane za łańcuch) — decyzja
        # w oknie ma pokazać dzisiejszy stan, inaczej użytkownik nie ma czego wybrać.
        gate()
        ports.journal.append(card, "permission.rechecked", step=step.get("step") or "",
                             ticket=step.get("ticket") or "", canAllow=allowed)
    if allowed:
        ports.record_automatic(card, step, command, gate=gate)


# --- decyzja o starcie i zbieranie wyniku ----------------------------------------------------------

def outside_ready(ports,state: CommandsState, card: str, step: dict, done: dict, decision: dict):
    """Zgoda na polecenie poza piaskownicą: uruchamia je raz (jednostka systemd, kod wyjścia w pliku) i zwraca
    decyzję z wynikiem do wznowienia kroku, albo None, gdy polecenie jeszcze trwa."""
    ports.trial_check(step)
    name = step.get("step") or ""
    # Start i zakończenie bieżącego uruchomienia outside tego procesu ze wspólnego wyniku redukcji:
    # zakończenie innej próby tej samej nazwy nie jest odpowiedzią bieżącej (PT-288 review 2, M4).
    # K-105 PT-415: czekanie (kolejka, trwające polecenie) z pamięci; każdy skutek (wznowienie z wynikiem, start,
    # zapis `outside.finished`) dopiero po świeżym odczycie i tej samej redukcji na nim.
    run_state, started, finished = processidentity.outside_run(ports.outside_history(card, step, fresh=False), name)
    if run_state != "ok":
        raise FlowError("outside-history", "nieznana historia outside — wykonanie wstrzymane")

    def fresh_run():
        run = processidentity.outside_run(ports.outside_history(card, step), name)
        if run[0] != "ok":
            raise FlowError("outside-history", "nieznana historia outside — wykonanie wstrzymane")
        return run
    if finished:
        _, started, finished = fresh_run()
    if finished:
        return {**decision, "answer": f"Kod wyjścia: {finished.get('exit')}\nKoniec wyniku:\n{finished.get('tail') or ''}"}
    if started is None:
        if ports.journal.state(card).paused:
            # Wstrzymana karta nie uruchamia nowych poleceń poza piaskownicą i nie trzyma miejsca w kolejce
            # (07.11, K-38/PT-264: automatyczna zgoda 3 min po wstrzymaniu puściła pełne testy w trakcie gry).
            # Po wznowieniu zgoda wraca do kolejki; polecenie już uruchomione kończy się normalnie.
            state.waiting.pop(name, None)
            return None
        if not ports.consent_before_start(card, step, done, decision):
            return None
        # Polecenie wymagające ciszy (#6: `exclusive` wyniku kroku albo `quietCommands`) jest zawsze ciężkie.
        heavy = ports.outside_heavy_of(card, step, done.get("command") or "",
                                       heavyqueue.exclusive_of(done.get("exclusive")))
        if ports.outside_busy(card):
            # Zgłoszenie w kolejce także przy zajętym miejscu (05.11: bez tego kolejka była pusta właśnie wtedy,
            # gdy miejsce się zwalniało, i wskakiwała karta sprawdzana jako pierwsza).
            ports.outside_behind(card, name, decision, quiet=True, heavy=heavy)
            ports.outside_log(name, "miejsce zajęte: " + ", ".join(sorted(state.active or {})))
            return None     # kolejka: stan karty mówi „czeka na wolne miejsce poza piaskownicą”
        if ports.outside_behind(card, name, decision, heavy=heavy):
            return None
        if heavy and not ports.heavy_admitted(card, name):
            return None     # kolejka ciężkich poleceń na maszynę (PT-373)
        state.waiting.pop(name, None)
        ports.heavy_held().pop(name, None)
        ports.outside_log(name, "start")
        ports.start_outside(card, step, done.get("command") or "")
        return None

    def still_current():
        # Historia zmieniła się po odczycie z pamięci: bez zatrzymania jednostki i zapisu, kolejny tick od nowa.
        _, now_started, now_finished = fresh_run()
        return not now_finished and now_started == started
    collected = ports.outside_collect(card, name, started, confirm=still_current)
    if collected is None:
        return None
    code, tail = collected
    ports.journal.append(card, "outside.finished", step=name, ticket=step.get("ticket") or "",
                         command=started.get("command") or "", exit=code, tail=tail)
    # Długi wynik (np. pełne testy) nie mieści się w końcówce — krok czyta całość z pliku (04.11, K-46/KAMP-31:
    # z obciętego wyniku zniknęły wyniki testów i krok prosił o ten sam przebieg drugi raz).
    return {**decision, "answer": f"Kod wyjścia: {code}\nPełny wynik (do odczytu): {started['log']}\n"
                                  f"Koniec wyniku:\n{tail}"}


def outside_collect(ports,state: CommandsState, card: str, name: str, started: dict, confirm=None, *,
                    timeout: float, tail_size: int):
    """Kod wyjścia i koniec wyniku rozpoczętego uruchomienia outside `name` albo None, gdy jeszcze trwa: plik kodu
    wyjścia, zatrzymana jednostka bez kodu (PT-315, `outside_stopped`) albo przekroczony czas `timeout`. Wspólne dla
    prośby kroku i próby testów po scaleniu (PT-329); zapis wyniku należy do wywołującego.

    `confirm` (K-105 PT-415): gdy `started` pochodzi z pamięci historii, świeża kontrola tuż przed pierwszym skutkiem
    (zatrzymanie jednostki, zwolnienie miejsca); False — historia zmieniła się po odczycie z pamięci, bez skutku
    i bez wyniku (None), kolejny tick decyduje od nowa. Wołana tylko, gdy uruchomienie już się skończyło albo
    przekroczyło czas, więc nie dokłada świeżego odczytu do czekania."""
    ports.outside_busy(card)            # buduje zbiór aktywnych po restarcie
    state.active[name] = (card, started["unit"])
    step_dir = os.path.dirname(os.path.dirname(started["exitFile"]))
    code = stepstop.read_exit_code(step_dir)
    if code is None and ports.wall_clock() - _epoch(started.get("ts")) < timeout:
        code = ports.outside_stopped(started, step_dir)
        if code is None:
            return None
    if confirm is not None and not confirm():
        return None
    if code is None:
        stepstop._systemctl("stop", started["unit"] + ".scope")
        code = "przekroczony czas"
    tail = ""
    try:
        with open(started["log"], "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - tail_size))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        tail = "(brak wyniku)"
    state.active.pop(name, None)
    ports.outside_heavy_state().pop(name, None)
    ports.outside_quiet_state().pop(name, None)
    # Procesy zostawione przez polecenie (np. atrapy z testów, serwery w tle) nie trzymają jednostki w nieskończoność.
    stepstop._systemctl("stop", started["unit"] + ".scope")
    return code, tail


def outside_stopped(ports,started: dict, step_dir: str, *, grace: float, state_timeout: float,
                    stopped: str):
    """Zatrzymana jednostka outside bez `exit-code` (PT-315: `systemctl --user stop …scope` zostawia `exit-code.tmp`
    bez zmiany nazwy): kod z kompletnego pliku tymczasowego albo `stopped`. None — jednostka działa albo stan
    nieznany (błąd odczytu nie kończy polecenia); wtedy czekamy dalej do limitu czasu."""
    if ports.wall_clock() - _epoch(started.get("ts")) < grace:
        return None         # systemd-run rejestruje jednostkę chwilę po starcie procesu
    if not stepstop.unit_without_processes(started.get("unit") or "", timeout=state_timeout):
        return None
    # Wrapper mógł skończyć zaraz po pierwszym odczycie (zmiana nazwy tmp → exit-code): najpierw plik docelowy.
    code = stepstop.read_exit_code(step_dir)
    if code is None:
        code = stepstop.read_exit_code(step_dir, stepstop.EXIT_CODE_FILE + ".tmp")
    return code if code is not None else stopped


def outside_exit(journal, card: str, name: str) -> str:
    """Plik kodu wyjścia uruchomienia outside procesu ze wspólnego wyniku redukcji (PT-288 review 2, M7):
    niezakończone uruchomienie, które zajmuje miejsce (najpierw bieżącej próby), inaczej bieżące."""
    events = journal.read(card)
    found = processidentity.outside_runs(events).get(name)
    if found is None:
        found = processidentity.outside_run(events, name)[1] or {}
    value = found.get("exitFile")
    return value if isinstance(value, str) and value else "/nonexistent/in/exit-code"


def outside_still_allowed(ports,state: CommandsState, card: str, step: dict) -> bool:
    """Świeża kontrola decyzji tuż przed startem (PT-311). Odmowa człowieka zapisana po kontroli kolejki
    kończy start bez błędu (krok wznowi się z odmową); automatyczna zgoda przechodzi `before_start`
    (odmowa w innym kroku ticketu ją unieważnia). Brak zgody nadal odrzuca `outside_history` (FlowError),
    a ręczna zgoda nie dostaje nowych kontroli."""
    name = step.get("step") or ""
    current = ports.journal.state(card)
    decision = current.step_decisions.get(name) or {}
    refused = decision.get("choice") == "deny" and not decision.get("auto")
    if refused or decision.get("auto") and decision.get("choice") in consent.RUN and not ports.consent_before_start(
            card, step, current.finished.get(name) or {}, decision):
        state.waiting.pop(name, None)
        return False
    return True


def outside_launch(name: str, step_dir: str, worktree: str, command: str, *, systemd_run: str, cpu_weight: int,
                   old_jobs: int):
    """Jednostka, plik wyniku, plik kodu wyjścia, argv i środowisko polecenia poza piaskownicą (jednostka
    `systemd-run --scope`, kod wyjścia przez `EXIT_WRAPPER`). Wspólne dla prośby kroku i próby testów po
    scaleniu (PT-329); start i jego zapis należą do wywołującego."""
    folder = os.path.join(step_dir, "outside")
    os.makedirs(os.path.join(folder, "in"), exist_ok=True)
    exit_file = os.path.join(folder, "in", "exit-code")
    log = os.path.join(folder, "outside.log")
    unit = f"tide-outside-{name}"
    env = dict(os.environ)
    from ..tasks import node as node_mod
    prefix = node_mod.node_home(os.path.expanduser("~"))
    if prefix:
        env["PATH"] = os.path.join(prefix, "bin") + os.pathsep + env.get("PATH", "")
    chrome = node_mod.chrome_bin(os.path.expanduser("~"))
    # Bez pamięci dyskowej QML (08.11: ~/.cache/QtProject urósł do 100 GB od testów qml6 na ścieżkach tymczasowych);
    # obejmuje też gałęzie kart, których uruchamiacz testów jeszcze tego nie ustawia.
    env.setdefault("QML_DISABLE_DISK_CACHE", "1")
    if chrome and not env.get("CHROME_BIN"):
        env["CHROME_BIN"] = chrome
    if not env.get("JOBS") and not test_pool_available(worktree):
        # Gałąź sprzed wspólnej puli testów (06.11: PT-173/PT-186/PT-220 po 14 procesów każdy, obciążenie 65):
        # `make` czyta JOBS ze środowiska, więc przebieg bierze najwyżej `old_jobs` procesów.
        env["JOBS"] = str(old_jobs)
    argv = [systemd_run or "systemd-run", "--user", "--scope", "--collect", "--quiet", f"--unit={unit}",
            "-p", f"CPUWeight={cpu_weight}", "-p", f"IOWeight={cpu_weight}", "--",
            "/usr/bin/env", "-u", "INVOCATION_ID", "/bin/sh", "-c", agentcmd.EXIT_WRAPPER, "tide-outside",
            exit_file, "/bin/sh", "-c", command]
    return unit, log, exit_file, argv, env


# --- miejsca i kolejka -----------------------------------------------------------------------------

def outside_busy(ports,state: CommandsState, card: str, *, total: int, per_project: int) -> bool:
    """Jedno polecenie poza piaskownicą naraz w projekcie (`per_project`) i `total` łącznie (03.11, K-31: pięć
    przebiegów e2e naraz na jednym porcie kończyło się kodem 1 i agenci prosili o kolejne)."""
    if state.active is None:
        # Po restarcie usługi: polecenia rozpoczęte (bez zakończenia) we wszystkich kartach.
        # Wspólny wynik redukcji (PT-288 review 2, M7): bieżące uruchomienie każdego procesu;
        # proces wstrzymany z jakimkolwiek startem zajmuje miejsce zachowawczo.
        state.active = {}
        for other in ports.journal.cards():
            for name, started in processidentity.outside_runs(ports.journal.read(other)).items():
                unit = started.get("unit")
                state.active[name] = (other, unit if isinstance(unit, str) else "")
    alive = {}
    for name, (other, unit) in state.active.items():
        if stepstop.read_exit_code(os.path.dirname(os.path.dirname(ports.outside_exit(other, name)))) is None:
            alive[name] = (other, unit)
    state.active = alive
    if len(alive) >= total:
        return True
    slug = ports.project(card)[1].slug
    return sum(1 for other, _ in alive.values() if ports.project(other)[1].slug == slug) >= per_project


def outside_behind(ports,state: CommandsState, card: str, name: str, decision: dict, *, quiet: bool = False,
                   heavy: bool = False, stale: float) -> bool:
    """Kolejka według czasu zgody (05.11: polecenie K-38 czekało ponad godzinę, bo każde zwolnione miejsce
    projektu brała karta sprawdzana wcześniej). Zgoda zgłasza się przy każdym sprawdzeniu; wpis bez sprawdzenia
    przez `stale` sekund (karta zatrzymana, krok odrzucony) wypada z kolejki. Wpis ma pole `heavy`
    (PT-373): starsze ciężkie polecenie, które nie ruszy, bo limit ciężkich jest zajęty, nie blokuje innych."""
    now = ports.wall_clock()
    if not state.seeded:
        state.seeded = True
        ports.seed(now)
    slug = ports.project(card)[1].slug
    previous = state.waiting.get(name)
    queued = previous[2] if previous else (_epoch(decision.get("ts")) if decision.get("ts") else now)
    state.waiting[name] = (card, slug, queued, now, heavy)
    state.waiting = {k: v for k, v in state.waiting.items() if now - v[3] < stale}
    heavy_load = None
    quiet_held = None
    for other, entry in list(state.waiting.items()):
        other_card, other_slug, other_queued = entry[:3]
        if other == name or (other_queued, other) >= (queued, name):
            continue
        if queue_heavy(entry):
            if heavy_load is None:
                heavy_load = (len(ports.heavy_running()), ports.heavy_limit())
            if not heavyqueue.holds_queue(True, *heavy_load):
                continue    # czeka na miejsce ciężkich — ruszy dopiero po zwolnieniu, nie trzyma innych
            if quiet_held is None:
                # Cisza (#6): starsze ciężkie wstrzymane ciszą (czeka na spokojną maszynę albo za poleceniem
                # wymagającym ciszy) nie ruszy teraz, więc nie trzyma lekkich ani innych projektów.
                quiet_held = ports.quiet_held()
            if other in quiet_held:
                continue
        if other_slug == slug or not ports.outside_busy(other_card):
            if not quiet:
                ports.outside_log(name, f"czeka za starszą zgodą {other}")
            return True     # starsza zgoda w tym projekcie albo starsza, która może ruszyć gdzie indziej
    return False


def outside_log(state: CommandsState, name: str, text: str) -> None:
    """Jedna linia w dzienniku usługi przy zmianie powodu czekania polecenia poza piaskownicą."""
    if state.logged.get(name) != text:
        state.logged[name] = text
        print(f"tide-daemon: kolejka poza piaskownicą: {name}: {text}", flush=True)


def outside_describe(journal, state: CommandsState, name: str) -> str:
    """Opis polecenia do zdań o ciszy: „make test (K-36)” — polecenie z `outside.started` albo prośby kroku."""
    found = (state.active or {}).get(name) or (state.waiting or {}).get(name)
    card = found[0] if found else ""
    command = ""
    if card:
        try:
            st = journal.state(card)
            command = (st.outside_started.get(name) or {}).get("command") or \
                (st.finished.get(name) or st.merge_check_steps.get(name) or {}).get("command") or ""
        except Exception:
            command = ""
    text = " ".join(str(command or name).split())
    text = text if len(text) <= 40 else text[:39] + "…"
    return f"{text} ({card})" if card else text


# --- klasa polecenia: ciężkie i wymagające ciszy (K-39 #3/#6, PT-373/PT-376; zasada: heavyqueue.py) -----------

def outside_heavy_state(state: CommandsState) -> dict:
    return state.heavy


def outside_quiet_state(state: CommandsState) -> dict:
    return state.quiet


def heavy_held_state(state: CommandsState) -> dict:
    """Ciężkie wstrzymane przy ostatniej decyzji: krok → (zajęte, limit) albo zdanie o ciszy (`_heavy_held_state`)."""
    return state.held


def outside_heavy_of(ports,card: str, step: dict, command: str, marked: bool | None = None) -> bool:
    """Klasa polecenia procesu `step` (raz na proces, pamiętana do końca uruchomienia). Błąd odczytu kontraktu
    albo projektu — lekkie (dotychczasowe limity). Polecenie wymagające ciszy (#6) jest zawsze ciężkie."""
    return ports.outside_class(card, step, command, marked)[0]


def outside_quiet_of(ports,card: str, step: dict, command: str, marked: bool | None = None) -> bool:
    """Czy polecenie procesu `step` wymaga ciszy (K-39 #6, PT-376): pole `exclusive` w `step.finished` albo wzorzec
    `quietCommands`. Pamiętane razem z klasą ciężkości do końca uruchomienia."""
    return ports.outside_class(card, step, command, marked)[1]


def outside_class(ports,card: str, step: dict, command: str, marked: bool | None = None) -> tuple[bool, bool]:
    """(ciężkie, wymaga ciszy) procesu `step`. `marked` — pole `exclusive` wyniku kroku; None — odczyt
    ze stanu karty (`journal.state`, także po restarcie: znacznik odtwarza się z `step.finished` i ustawień).
    Próba testów po scaleniu jest ciężka i nie wymaga ciszy.

    Pamiętana jest tylko klasa policzona bez błędu (i znacznik kroku `exclusive`, najostrzejszy). Błąd odczytu
    (projekt, kontrakt, ustawienia) daje wynik tego wywołania — lekkie albo sam znacznik kroku — bez zapisu łagodniejszej
    klasy w pamięci, więc następne wywołanie liczy od nowa
    (K-109 PT-482: 13.11 odczyt prezentacji przy zajętej Tablicy, `BoardBusy`, zapamiętał `make test-changed`
    K-99-PT-395-1 jako lekkie i polecenie ruszyło obok pomiaru wymagającego ciszy K-113-PT-468-3)."""
    name = step.get("step") or ""
    known, quiet = ports.outside_heavy_state(), ports.outside_quiet_state()
    if name in quiet:
        exclusive = quiet[name]
    else:
        exclusive, failed = False, False
        if not mergecheck.is_step(name):
            try:
                if marked is None:
                    marked = heavyqueue.exclusive_of(
                        (ports.journal.state(card).finished.get(name) or {}).get("exclusive"))
                exclusive = bool(marked)
                _, project = ports.project(card)
                roots = (step.get("worktree") or "", project.repo, step.get("stepDir") or "")
                exclusive = heavyqueue.needs_quiet(command, roots, ports.quiet_patterns(), marked=exclusive)
            except Exception:
                failed = True   # błąd projektu albo ustawień: zostaje sam znacznik kroku
        # Znacznik kroku `exclusive` jest najostrzejszą klasą — pamiętamy go także przy błędzie (`_heavy_running`
        # czyta pamięć); łagodniejszego wyniku z błędu nie pamiętamy.
        if not failed or exclusive:
            quiet[name] = exclusive
    if name in known:
        heavy = known[name]
    elif exclusive or mergecheck.is_step(name):
        heavy = True
        known[name] = heavy
    else:
        try:
            _, project = ports.project(card)
            worktree = step.get("worktree") or ""
            roots = (worktree, project.repo, step.get("stepDir") or "")
            heavy = heavyqueue.classify(command, roots, ports.heavy_contract(card, worktree),
                                        ports.heavy_patterns()).heavy
            known[name] = heavy
        except Exception:
            heavy = False       # bez zapisu w pamięci: następne wywołanie (start w ticku) liczy od nowa
    return bool(heavy or exclusive), bool(exclusive)


# --- próba testów po scaleniu (K-88 PT-318, PT-329; model i zdarzenia: mergecheck.py; wydzielenie: K-109 PT-484) ----

def merge_checks(ports, state: CommandsState, card: str) -> list[dict]:
    """Bramka testów po scaleniu: uzbraja ją (raz na kartę), zakłada brakujące próby po trwałych wynikach scaleń
    (także po restarcie między `result` scalenia a zapisem próby — dokładnie raz, bo próba pamięta `mergeSeq`)
    i zwraca próby, które trzymają bramkę. Pusta lista — bramka otwarta."""
    journal = ports.journal
    with journal.lock:
        st = journal.state(card)
        if st.started is None:
            return []
        if not st.merge_check_armed:
            # Scalenia zapisane przed uzbrojeniem (karty trwające w chwili wdrożenia) nie zakładają prób wstecz.
            journal.append(card, mergecheck.ARMED)
            st = journal.state(card)
        for (action, ticket), merged in list(st.results.items()):
            seq = merged.get("seq")
            if action != "merge" or not merged.get("ok") or int(seq or 0) < st.merge_check_armed or \
                    (ticket, seq) in st.merge_check_merges or (card, ticket, seq) in state.merge_ff:
                continue
            if ports.request_check(card, st, ticket, merged) is None:
                state.merge_ff.add((card, ticket, seq))
            st = journal.state(card)
        held = mergecheck.gate(st)
    for entry in held:
        if entry["status"] == mergecheck.PENDING:
            ports.merge_check_waiting(card, entry)
    return mergecheck.gate(journal.state(card)) if held else held


def merge_check_waiting(ports, card: str, entry: dict) -> None:
    """Próba z poleceniem czeka na przebieg (PT-329): pseudokrok `merge-check-<ticket>-<n>`, zgoda tą samą
    ścieżką co prośba `outside` kroku (bez zgody Tide „z urzędu”), start w kolejce outside i zapis
    `merge.check-finished`. Niepewna historia albo tożsamość wstrzymuje przebieg (bramka zostaje zamknięta)."""
    command = str(entry["requested"].get("command") or "")
    name = mergecheck.step_name(entry["check"])
    if not command:
        return
    try:
        step = ports.merge_check_step(card, entry)
        if step is None:
            return
        decision = ports.journal.state(card).step_decisions.get(name)
        if decision is None:
            # Automatyczna zgoda tylko z reguł projektu (dozwolone rodziny, zgoda ticketu, `outsideAuto`) i bez
            # odmowy człowieka tego polecenia w tym tickecie ani wstrzymania karty (PT-311). Inaczej panel karty
            # pokazuje zwykłe pytanie o zgodę (decisions.py).
            ports.record_check_automatic(card, step, command, entry["check"])
            decision = ports.journal.state(card).step_decisions.get(name)
        if (decision or {}).get("choice") in consent.RUN:
            ports.merge_check_run(card, step, entry, decision)
    except FlowError as exc:
        ports.outside_log(name, f"wstrzymane: {exc}")


def merge_check_step(ports, card: str, entry: dict) -> dict | None:
    """Pseudokrok próby: zapisany `merge.check-step` (raz na próbę, także po restarcie) jako słownik kroku dla
    wspólnych reguł zgody i kolejki. None — brak worktree gałęzi karty."""
    name = mergecheck.step_name(entry["check"])
    journal = ports.journal
    with journal.lock:
        st = journal.state(card)
        found = st.merge_check_steps.get(name)
        if found is None:
            requested = entry["requested"]
            info = ticket_of(st, entry["ticket"])
            worktree = info["cardWorktree"]
            if not worktree:
                return None
            from ..board.commands import outside_allowed
            command, step_dir = requested.get("command") or "", worktree + f".{name}.step"
            project = ports.project(card)[1]
            allowed = outside_allowed(command, project.denied_commands, (worktree, project.repo, step_dir))
            found = journal.append(
                card, mergecheck.STEP, step=name, ticket=entry["ticket"], kind=mergecheck.KIND,
                check=entry["check"], command=command, after=requested.get("after") or "",
                branch=info["cardBranch"], worktree=worktree, stepDir=step_dir,
                baseVersion=st.base_version, canAllow=allowed)
    # Bez `runner`: próba biegnie wyłącznie na hoście, w worktree gałęzi karty.
    return {key: found.get(key) for key in ("step", "ticket", "kind", "check", "command", "after", "branch",
                                            "worktree", "stepDir", "baseVersion")}


def merge_check_history(ports, card: str, *, fresh: bool = False, starting: str = "") -> list:
    """Historia procesów karty dla próby testów po scaleniu. Bez `fresh` z pamięci według wersji dziennika
    (K-105 PT-415) — wyłącznie do obserwacji czekania; `fresh=True` (świeży pełny odczyt, bez pamięci) przed każdym
    skutkiem: start próby (Popen), zatrzymanie jednostki i zapis wyniku. `starting` — nazwa próby przed jej startem:
    świeża historia z jakimkolwiek uruchomieniem tej próby (np. dopisanym z zewnątrz po odczycie z pamięci) albo
    niespójnym stanem outside odmawia startu (FlowError), jak `outside_history` z krokiem i poleceniem."""
    try:
        events = trialhold.read_process_history(ports.journal, card, fresh=fresh or bool(starting))
        if starting:
            run_state, started, finished = processidentity.outside_run(events, starting)
            if run_state != "ok" or started is not None or finished is not None:
                raise ValueError("próba ma już uruchomienie w historii — start odmówiony")
        return events
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise FlowError("outside-history", f"nieznana historia outside — wykonanie wstrzymane: {exc}") from None


def merge_check_run(ports, state: CommandsState, card: str, step: dict, entry: dict, decision: dict) -> None:
    """Zgoda na próbę: start raz w kolejce outside (limit, jednostka, plik kodu wyjścia), a po końcu zapis
    `outside.finished` pseudokroku i `merge.check-finished` z tym samym `exit` (liczba, „zatrzymane”,
    „przekroczony czas”). Odpowiednik `outside_ready` bez wznawiania kroku.

    K-105 PT-415: czekanie (kolejka, trwająca próba) z pamięci historii; każdy skutek — wynik z zapisanego
    zakończenia, start (pod blokadą w `merge_check_start`), zatrzymanie jednostki i zapis wyniku — dopiero po
    świeżym odczycie i tej samej redukcji na nim."""
    ports.trial_check(step)
    name = step["step"]
    run_state, started, finished = processidentity.outside_run(ports.merge_check_history(card), name)
    if run_state != "ok":
        raise FlowError("outside-history", "nieznana historia outside — wykonanie wstrzymane")

    def fresh_run():
        run = processidentity.outside_run(ports.merge_check_history(card, fresh=True), name)
        if run[0] != "ok":
            raise FlowError("outside-history", "nieznana historia outside — wykonanie wstrzymane")
        return run
    if finished:
        _, started, finished = fresh_run()
    if finished:
        # Restart między `outside.finished` a wynikiem próby: wynik z zapisanego zakończenia, dokładnie raz.
        ports.merge_check_finish(card, step, entry, finished.get("exit"), (started or {}).get("log") or "")
        return
    if started is None:
        if ports.journal.state(card).paused:
            # Wstrzymana karta nie startuje próby i nie trzyma miejsca w kolejce; po wznowieniu wraca do niej.
            state.waiting.pop(name, None)
            return
        if not ports.consent_before_start(card, step, {"command": step["command"]}, decision):
            return
        # Próba testów po scaleniu jest zawsze ciężka (PT-373).
        ports.outside_heavy_state()[name] = True
        if ports.outside_busy(card):
            ports.outside_behind(card, name, decision, quiet=True, heavy=True)
            ports.outside_log(name, "miejsce zajęte: " + ", ".join(sorted(state.active or {})))
            return
        if ports.outside_behind(card, name, decision, heavy=True):
            return
        if not ports.heavy_admitted(card, name):
            return
        state.waiting.pop(name, None)
        ports.heavy_held().pop(name, None)
        ports.outside_log(name, "start")
        ports.merge_check_start(card, step, entry)
        return

    def still_current():
        # Historia zmieniła się po odczycie z pamięci: bez zatrzymania jednostki i zapisu, kolejny tick od nowa.
        _, now_started, now_finished = fresh_run()
        return not now_finished and now_started == started
    collected = ports.outside_collect(card, name, started, confirm=still_current)
    if collected is not None:
        code, tail = collected
        with ports.journal.lock:
            ports.journal.append(card, "outside.finished", step=name, ticket=step["ticket"],
                                 command=started.get("command") or "", exit=code, tail=tail)
            ports.merge_check_finish(card, step, entry, code, started.get("log") or "")


def merge_check_finish(ports, card: str, step: dict, entry: dict, code, log: str) -> None:
    """Zapis `merge.check-finished` bieżącej próby, dokładnie raz (inna próba albo zapisany wynik — bez zapisu)."""
    journal = ports.journal
    with journal.lock:
        item = journal.state(card).merge_checks.get(step["ticket"]) or {}
        if (item.get("requested") or {}).get("check") == entry["check"] and item.get("finished") is None:
            journal.append(card, mergecheck.FINISHED, ticket=step["ticket"], check=entry["check"], exit=code, log=log)


def merge_check_still_allowed(ports, state: CommandsState, card: str, step: dict) -> bool:
    """Świeża kontrola tuż przed startem, pod blokadą dziennika: bieżąca próba nadal czeka, jej decyzja to zgoda,
    a zgoda automatyczna nadal spełnia reguły (odmowa człowieka i cofnięcie wygrywają — PT-311)."""
    current = ports.journal.state(card)
    name = step["step"]
    item = current.merge_checks.get(step["ticket"]) or {}
    decision = current.step_decisions.get(name) or {}
    if (item.get("requested") or {}).get("check") != step["check"] or item.get("finished") is not None or \
            name in current.outside_launched or current.paused or current.card_finished or \
            decision.get("choice") not in consent.RUN:
        state.waiting.pop(name, None)
        return False
    if decision.get("auto") and not ports.consent_before_start(card, step, {"command": step["command"]}, decision):
        return False
    return True


def merge_check_head(ports, card: str, step: dict) -> str:
    """HEAD worktree gałęzi karty, gdy to wierzchołek `after` próby (albo `after` z samymi commitami stanu planu
    zapisanymi przez Tide); pusty napis — inny kod niż ten, który próba ma sprawdzić."""
    worktree, after = step.get("worktree") or "", step.get("after") or ""
    found = ports.git.run(worktree, "rev-parse", "HEAD") if worktree and after else None
    head = found.out.strip() if found is not None and found.ok else ""
    if not head:
        return ""
    if head == after:
        return head
    st = ports.journal.state(card)
    repo = ports.merge_check_repo(card, st, step["ticket"])
    return head if ports.plan_commits_only(repo, st, after, head) else ""


def merge_check_start(ports, state: CommandsState, card: str, step: dict, entry: dict) -> None:
    """Start próby: jednostka i argv z `outside_launch`, świeża historia i kontrola zgody pod blokadą dziennika
    przed Popen, zapis `outside.started` z HEAD worktree. Bez startu, gdy worktree gałęzi karty nie stoi na `after`."""
    name, command, worktree = step["step"], step["command"], step["worktree"]
    head = ports.merge_check_head(card, step)
    if not head:
        ports.outside_log(name, f"worktree gałęzi karty nie stoi na {step.get('after') or '?'} — czekam")
        return
    # Pełne polecenie zawsze w całości: ponowienie samych padniętych modułów (testrerun) nie dotyczy próby,
    # bo udane ponowienie nie może zastąpić porażki (zasady wyjątków od testów).
    unit, log, exit_file, argv, env = ports.outside_launch(name, step["stepDir"], worktree, command)
    journal = ports.journal
    with open(log, "ab") as out, journal.lock:
        # K-105 PT-415: świeży ścisły odczyt pod blokadą przed Popen (jak `_start_outside`): uruchomienie dopisane
        # z zewnątrz po ticku (odczyt z pamięci) zamyka start.
        ports.merge_check_history(card, fresh=True, starting=name)
        if not ports.merge_check_still_allowed(card, step):
            return
        subprocess.Popen(argv, cwd=worktree, env=env, stdin=subprocess.DEVNULL, stdout=out,
                         stderr=subprocess.STDOUT, start_new_session=True)
        journal.append(card, "outside.started", step=name, ticket=step["ticket"], command=command,
                       unit=unit, log=log, exitFile=exit_file, worktree=worktree, head=head)
    if state.active is None:
        state.active = {}
    state.active[name] = (card, unit)


def merge_check_repo(ports, card: str, st, ticket: str) -> str:
    """Repozytorium ticketu z jego kroków, inaczej repozytorium projektu karty."""
    return next((s.get("repo") for s in st.steps.values() if s.get("ticket") == ticket and s.get("repo")), "") \
        or ports.card_repo(card)


def merge_check_targets(git, repo: str, commit: str) -> set:
    """Cele `make` gałęzi karty w commicie `commit`: pliki `make` z obiektów gita `git` (nie z drzewa roboczego),
    bez uruchamiania `make`. Wybór plików, limit i parser — `testpolicy.commit_make_targets`."""
    if not commit:
        return set()

    def show(path: str):
        shown = git.run(repo, "show", f"{commit}:{path}")
        return shown.out if shown.ok else None

    def listing(directory: str) -> list:
        listed = git.run(repo, "ls-tree", "--name-only", commit, directory + "/")
        return listed.out.splitlines() if listed.ok else []

    return testpolicy.commit_make_targets(show, listing)


def merge_check_contract(ports, card: str) -> str:
    """`tests.changed` zatwierdzonego kontraktu testów projektu (K-69) albo "" — kontrakt nieobecny,
    niezatwierdzony, uszkodzony albo Tablica bez kontraktów (gałąź sprzed K-69). Każda niepewność → ""."""
    try:
        _, project = ports.project(card)
        tests = getattr(project, "tests", None)
        if not isinstance(tests, dict) or getattr(project, "tests_error", ""):
            return ""
        changed = str(tests.get("changed") or "").strip()
        if not changed:
            return ""
        from ..board import contract_approval  # K-69; na gałęzi bez kontraktów — ImportError
        ok = contract_approval.approved(ports.store.root, project.slug, tests,
                                        getattr(ports.store, "approvals_path", None))
    except Exception:
        return ""
    return changed if ok is True else ""
