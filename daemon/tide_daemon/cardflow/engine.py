"""Silnik przepływu karty bez Qt (PT-58): zlecenie „Od razu”, krok ticketu w tle, strażnik kroku, scalenie do gałęzi
karty i ręczne kolumny. Wszystkie metody poza `derived`, `has_journal`, `is_hidden` i `warnings` woła jeden wątek
roboczy (cardflow_monitor.py) — po kolei, więc dwie akcje jednej karty nigdy nie idą naraz.

Przebieg zlecenia (`start`): sprawdzenie (karta, projekt z repozytorium, wykonawca Claude z katalogu, program
`claude`, prefiks ticketów — bez zapisu), potem zdarzenia: `card.started`, pary „zamiar → wynik” dla rezerwacji
numeru (`coordination/reservations.py`), gałęzi i worktree karty (od gałęzi głównej), gałęzi i worktree ticketu (od
gałęzi karty) i startu kroku (`Runner.start`; `HostRunner` to `agentcmd.background()` + `Background.start()` w jednostce
`systemd-run --user --scope`), na końcu `step.started`. Błąd sprawdzenia nie zostawia niczego — ani zdarzenia, ani gałęzi.

`tick()` (co chwilę): dziennik cofnięty z zewnątrz → odtworzenie z kopii i ostrzeżenie; zaczęte akcje bez wyniku
(restart usługi) → dokończenie albo jawny błąd; trwający krok → koniec (jednostka nieaktywna) albo strażnik; ticket
gotowy → scalenie do gałęzi karty. Po restarcie usługa nie ma procesu kroku, tylko jednostkę — stan jednostki mówi,
czy krok żyje; wynik i tak bierze się z pliku wyniku, nigdy z tekstu rozmowy.

Koniec kroku: plik wyniku `<krok>/out/result.json` (`{"result": "done"|"question"|"denied"|"error", …}`); brak
pliku — błąd kroku `no-result`, zły plik — `bad-result`. Wynik i notki trafiają do `flow/<K-12>/steps/<krok>/`
w repozytorium tablicy.

Strażnik kroku: krok, który przez `stepGuardMinutes` (domyślnie 15, `$XDG_CONFIG_HOME/tide/cardflow.json`) nie
zmienia plików w worktree (bez `.git`) ani nie dopisuje do dziennika sesji, zatrzymuje `stepstop.stop_step` (commit
WIP gitem w piaskownicy albo zmiany zostają w worktree) i kończy się błędem `no-progress`. Licznik startuje od
pierwszego sprawdzenia — po restarcie usługi od nowa (krok dostaje pełny czas, nigdy mniej).
"""
from __future__ import annotations

import json
import hashlib
import hmac
import logging
import os
import signal
import shutil
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone

from ..models import queue as model_queue
from .. import codex_homes
from ..board.model import DESCRIPTION_MAX, BoardError, card_num
from ..board import assignment
from ..coordination import reservations, youtrack as yt
from ..coordination import plan as source_plans
from ..coordination.repoplans import RepoPlans
from ..board.plan_candidates import candidates as plan_candidates
from ..tasks import agentcmd, launch, stepstop
from ..tasks.runner import HostRunner, Runner
from ..tasks.core import Plan, TaskFiles
from ..worktrees.naming import slug_from_prompt
from . import testrerun
from . import caretaker
from . import decisions
from . import caretakerctx
from . import handoff
from . import boxstep
from . import trialhold
from . import processidentity
from ..box import trialgate
from ..box import nftcontract
from ..box import settings as bsettings
from . import consent
from .decisions import clean_options
from . import environment as flow_environment
from . import execution
from . import evidence
from . import faults
from . import fullcard
from . import ticketentry
from . import guard
from . import limits as flow_limits
from . import planning
from .planediting import PLAN_AUTHOR, PlanEditing, proposal_author_of
from .caretakerchat import CaretakerChat
from . import plans as flow_plans
from .gitops import FlowGit, FlowGitError, worktree_for, lock_for
from . import baseproposal, planartifacts
from .mergepreview import source_dirty
from .mergeop import MergeWorktree
from .journal import Journal
from .stepnotes import close_console
from .labels import step_name
from .state import (REASON_LABELS, FlowState, derived, fold, base_of, ticket_of, graph_waves, attention_step, decision_step,
                    guard_reason, ticket_waits, _epoch, _bookkeeping)
from .tracker import TrackerOutbox

GUARD_MINUTES = 15.0
TIMEOUT_MINUTES = 240.0
SETTINGS_FILE = "cardflow.json"
DEFAULT_PROVIDER_SLOTS = 3      # kroki naraz u jednego dostawcy chmurowego (providerSlots w ustawieniach)
MAX_PROVIDER_SLOTS = 12
RESULTS = ("done", "question", "denied", "error", "outside")
OUTSIDE_TIMEOUT = 3600          # najdłużej działające polecenie uruchomione poza piaskownicą (s)
OUTSIDE_TAIL = 12_000           # tyle końcowych znaków wyniku dostaje wznowiony krok
ENV_QUESTION = re.compile(r"piaskownic|sandbox|docker|testcontainers|\boutside\b|ng serve|\bnpm\b|\bnpx\b|dotnet test|"
                          r"playwright|\bport(?:u|y|em)?\b|:\d{4}\b|\bsieci\b|\bsieć\b|network|środowisk|"
                          r"zainstalow|instalacj|uruchomi[ćeł]\w* (?:test|polecen|serwer)", re.IGNORECASE)
ENV_ANSWER = ("Odpowiedź Tide (automatyczna — pytanie dotyczy środowiska, nie decyzji użytkownika): nie pytaj "
              "użytkownika o środowisko. Tide uruchamia polecenia poza piaskownicą sam (sieć, Docker/Testcontainers, "
              "serwery, pełne testy): zapisz {\"result\":\"outside\",\"command\":\"jedno pełne polecenie\","
              "\"reason\":\"po co\"} i zakończ krok — przy wznowieniu dostaniesz kod wyjścia i koniec wyniku. Polecenie "
              "może być długie i mieć zmienne w cudzysłowie, ale bez łączenia (&&, ;, |). Gdy polecenia nie da się "
              "uruchomić, zakończ wynikiem error z przyczyną. Jeśli pytanie dotyczyło jednak zakresu albo decyzji "
              "biznesowej, zadaj je ponownie: krótko, z opcjami i rekomendacją.")
REVIEW_ROUNDS = 6              # rundy review przed pytaniem użytkownika
ESCALATE_FIX_ROUND = 3          # od tej rundy poprawki robi mocny wykonawca spoza recenzentów (K-88/PT-312)
# PT-181 (#18): rodzaje kroków, które uruchamiają kod projektu — w projekcie z gotowym pudełkiem idą do `BoxRunner`.
# Planowanie w każdej odmianie (`planning`, `plan-fix`, `plan-amend`, `plan-revise`, `plan-review`, pełne planowanie
# w Konsoli) czyta repozytorium i Tablicę, więc zostaje na hoście w bwrap; `conflict` też (scalenie gałęzi).
# Wznowienie dziedziczy runner wznawianego kroku. Wybór: `cardflow/boxstep.py`.
BOX_STEP_KINDS = ("ticket", "fix", "review", "close")
OUTSIDE_PER_PROJECT = 6         # polecenia poza piaskownicą naraz w projekcie (06.11 19:00, decyzja użytkownika: stałe 6; zmiana tylko przy wyraźnym wzroście porażek testów pod obciążeniem, nie przy samym load average)
OUTSIDE_TOTAL = 7               # … i łącznie (testy i budowanie obciążają całą maszynę)
OLD_RUNNER_JOBS = 4             # procesy testów dla gałęzi bez wspólnej puli maszyny (tools/test_runner.py bez MachineSlots)


def test_pool_available(worktree: str) -> bool:
    """Czy uruchamiacz testów w worktree korzysta ze wspólnej puli maszyny. Brak uruchamiacza (inny projekt) — tak:
    nie zmieniamy środowiska projektów, które nie używają tools/test_runner.py."""
    path = os.path.join(worktree, "tools", "test_runner.py") if worktree else ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "class MachineSlots" in fh.read()
    except OSError:
        return True


CLOSE_AUTHOR = "close"           # `reassign-author` z ticket=close przepina wykonawcę domknięcia karty
OUTSIDE_QUEUE_STALE = 300       # s bez sprawdzenia, po których zgoda wypada z kolejki poleceń poza piaskownicą
STEP_CPU_WEIGHT = 40            # kroki i polecenia poza piaskownicą ustępują pulpitowi (domyślna waga 100)
OUTPUT_MAX = 256 * 1024
FLOW_ERROR_PERSIST = 10.0       # s: ten sam błąd Tide w kolejnym przebiegu po tylu sekundach — wyzwalacz opiekuna
HANDOFF_DIFF_MAX = 64 * 1024
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
DESCRIPTION_LIMIT_BYTES = DESCRIPTION_MAX - 1024   # zapas na kolejne wystąpienie bez wycinka (PT-125)
REQUEST_FIELDS = ("entry", "executor", "reviewer", "preferredProvider", "images", "check", "planPath", "mode",
                  "projects", "baseRef", "baseCommit", "cardBranch")
# Zatwierdzenie planu z ticketami w kilku projektach zakłada karty rodzeństwa (K-33 #5); do tego czasu odmowa.
GROUP_APPROVAL = "zatwierdzanie grupy kart — w następnej wersji"



AUTO_DENY_NOTE = ("Tide nie może zezwolić na takie polecenie (łańcuch z &&, ;, |, przypisanie zmiennej, podpowłoka "
                  "albo polecenie zabronione). Uruchamiaj polecenia pojedynczo, bez łączenia i zmiennych; do czytania "
                  "plików używaj narzędzia Read. Jeśli pojedyncze polecenie spoza listy jest naprawdę potrzebne, "
                  "zapisz wynik denied z tym jednym poleceniem.")
AUTO_DENY_LONG = ("Tide nie może zezwolić na polecenie dłuższe niż {limit} znaków. Podziel je na kilka krótszych "
                  "(np. jeden katalog albo kilka plików naraz, `git rm -r <katalog>`), każde jako osobny wynik denied.")
STEP_READ_NOTE = ("Odczyt własnego katalogu kroku (<krok>/in i <krok>/out) jest zawsze dozwolony i nie wymaga zgody. "
                  "Uruchom te polecenia pojedynczo, bez łączenia, albo czytaj pliki narzędziem Read.")


def reviewer_override(st, step: dict, decision: dict, current: str) -> str:
    """Recenzent przepięty przez użytkownika po decyzji o kroku review (06.11, K-60/PT-254: Codex odmówił review,
    Tide sam przełączył na Sonnet przy limicie Claude 96%, a późniejsze „Przepnij recenzenta” na Codex Astra nie
    działało, bo wznowienie brało wykonawcę z automatycznej decyzji). Nowszy wybór użytkownika wygrywa."""
    if step.get("kind") != "review":
        return ""
    chosen = st.reviewer_overrides.get(step.get("ticket") or "") or {}
    newer = chosen.get("seq", 0) > max(step.get("seq", 0), decision.get("seq", 0) or 0)
    return chosen.get("reviewer") or "" if newer and chosen.get("reviewer") not in ("", None, current) else ""

class FlowDeferred(Exception):
    """Krok czeka na wolne miejsce; nie jest to błąd karty."""



LOGIN_MARKERS = ("failed to authenticate", "oauth session expired", "please run /login", "invalid api key",
                 "authentication_error", "not logged in", "401 unauthorized")


def _login_expired(log: str) -> bool:
    """Koniec dziennika kroku mówi o wygasłym albo brakującym logowaniu programu agenta."""
    try:
        with open(log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 20_000))
            tail = fh.read().decode("utf-8", "replace").casefold()
    except OSError:
        return False
    return any(marker in tail for marker in LOGIN_MARKERS)




PLACEHOLDER_AGE = 60   # s; świeży plik może być w trakcie zapisu przez gita


def _placeholder(path: str, now: float | None = None) -> bool:
    """Pusty zwykły plik bez prawa zapisu, starszy niż minuta — punkt montowania zostawiony przez piaskownicę kroku
    (bwrap tworzy brakujące ścieżki, które ma zasłonić). Git takich plików nie tworzy: jego pliki mają prawo zapisu."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    import stat
    return (stat.S_ISREG(info.st_mode) and info.st_size == 0 and not info.st_mode & 0o222
            and (now or time.time()) - info.st_mtime > PLACEHOLDER_AGE)


def _ghost_entry(folder: str) -> bool:
    """Wpis `.git/worktrees/<nazwa>` złożony wyłącznie z punktów montowania (bez HEAD i gitdir)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return bool(names) and "HEAD" not in names and "gitdir" not in names and all(
        _placeholder(os.path.join(folder, name)) for name in names)


def _remove_placeholder(path: str, found: list, label: str) -> None:
    if _placeholder(path):
        try:
            os.chmod(path, 0o644)
            os.remove(path)
            found.append(label)
        except OSError:
            pass


UNTRACKED_BEFORE = "untracked-before.json"    # nieśledzone pliki worktree ticketu w chwili startu review


# Odmowy z filtra treści dostawcy w dzienniku kroku — ponowienie u tego samego dostawcy nic nie da.
CONTENT_REFUSAL = re.compile(r"flagged for possible cybersecurity risk|content was flagged|violates (?:our|the) "
                             r"(?:usage )?polic|Usage Policy|refused to (?:help|continue) for safety", re.I)


def content_refusal(step: dict) -> bool:
    """Krok zakończony odmową filtra treści (ostatnie 20 KB `out/step.log`)."""
    path = os.path.join(step.get("stepDir") or "", "out", "step.log")
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - 20_000))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return False
    return bool(CONTENT_REFUSAL.search(tail))


def worktree_guard(repo: str, memory: dict) -> list[str]:
    """Strażnik worktree (03.11): w trakcie kroków agentów z `.git/worktrees/*/` repozytorium plasma-tide zniknęły pliki
    `HEAD` i `gitdir` (19 worktree naraz, dwa razy), więc git przestał znać worktree kart i ticketów. Zapamiętuje oba
    pliki z każdego zdrowego wpisu i odtwarza je, gdy znikną, a katalog worktree wciąż istnieje. 04.11: piaskownica
    przy każdym poleceniu kroku tworzy puste pliki tylko do odczytu (punkty montowania) dla ścieżek usuniętych już
    worktree — pusty `commondir` takiego „ducha” psuje `git worktree list` całego repozytorium, a pusty `config.lock`
    blokuje zapis konfiguracji. Strażnik usuwa duchy i takie pliki. Zwraca naprawione wpisy."""
    admin = os.path.join(repo, ".git", "worktrees")
    restored = []
    _remove_placeholder(os.path.join(repo, ".git", "config.lock"), restored, "config.lock")
    try:
        names = os.listdir(admin)
    except OSError:
        return restored
    for name in names:
        folder = os.path.join(admin, name)
        head, gitdir = os.path.join(folder, "HEAD"), os.path.join(folder, "gitdir")
        try:
            if _ghost_entry(folder):
                # Duch: piaskownica kroku odtworzyła wpis usuniętego worktree jako punkty montowania (04.11).
                shutil.rmtree(folder)
                restored.append(f"{name} (duch usunięty)")
                continue
            commondir = os.path.join(folder, "commondir")
            if _placeholder(commondir) and os.path.isfile(head) and os.path.isfile(gitdir):
                os.chmod(commondir, 0o644)
                with open(commondir, "w", encoding="utf-8") as fh:
                    fh.write("../..\n")
                restored.append(f"{name} (commondir)")
            if os.path.isfile(head) and os.path.isfile(gitdir):
                with open(head, encoding="utf-8") as fh_head, open(gitdir, encoding="utf-8") as fh_dir:
                    memory[folder] = (fh_head.read(), fh_dir.read())
                continue
            saved = memory.get(folder)
            if not saved or not os.path.isdir(os.path.dirname(saved[1].strip())):
                continue
            for path, text in ((head, saved[0]), (gitdir, saved[1])):
                if not os.path.isfile(path):
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(text)
            restored.append(name)
        except OSError:
            continue
    if restored:
        print(f"tide-daemon: strażnik worktree naprawił {repo}: {', '.join(restored)}", flush=True)
    return restored

def reviewer_changed(reviewer, expected: dict, source: dict, assigned: dict, transferred: bool) -> bool:
    """Recenzent rozwiązany dziś nie jest tym z planu (albo z poprzedniej rundy) lub łamie regułę niezależności."""
    return bool(reviewer.tool != expected["program"] or reviewer.model != expected["model"] or \
            reviewer.provider != expected["provider"] or reviewer.effort != expected["effort"] or \
            (reviewer.route.url if reviewer.route else "") != expected["routeUrl"] or \
            (reviewer.route.kind if reviewer.route else "") != expected["routeKind"] or \
            (reviewer.route.token_file if reviewer.route else "") != expected["routeTokenFile"] or \
            reviewer.model.lower() == (source.get("model") or "").lower() or \
            reviewer.local != (source.get("provider") == "local") or \
            (not transferred and assigned.get("preferredProvider") == "mixed" and
             reviewer.provider == source.get("provider")) or \
            (not transferred and assigned.get("preferredProvider") != "mixed" and
             reviewer.provider != source.get("provider")))


REMARK = re.compile(r"^\s*(?:#{2,4}\s*)?(?:\*\*)?((?:BLOCKER|MAJOR|B|M)[- ]?\d*)[.:)]?\s*(?:\*\*)?\s*[—–:-]?\s*(.+)$")


def review_question(ticket: str, round_no: int, review_md: str) -> str:
    """Pytanie po kolejnej rundzie review z listą pozostałych uwag BLOCKER/MAJOR i podpowiedzią, co zwykle wybrać
    (03.11: samo „ponowić, scalić czy odrzucić?” nie mówiło użytkownikowi, o co chodzi)."""
    remarks = []
    try:
        with open(review_md, encoding="utf-8", errors="replace") as fh:
            for line in fh.read(200_000).splitlines():
                found = REMARK.match(line)
                if found and found.group(1).upper().startswith(("B", "M")) and len(found.group(2)) > 8:
                    label = found.group(1).replace(" ", "").upper()
                    if label in ("BLOCKER", "MAJOR"):
                        continue          # sam nagłówek sekcji
                    remarks.append(f"{label}: {found.group(2).replace('**', '').strip(' *')[:160]}")
    except OSError:
        pass
    listed = "\n".join(f"- {r}" for r in remarks[:6]) or "- (szczegóły w review.md kroku review)"
    return (f"{ticket}: po {round_no} rundach review zostały uwagi:\n{listed}\n"
            "Zwykle: „Ponów poprawki”, gdy uwagi dotyczą kodu. „Scal mimo uwag” tylko gdy dotyczą środowiska "
            "(np. testów niewykonalnych w piaskownicy). „Odrzuć ticket”, gdy ticketu nie chcesz.")

class FlowError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _CaretakerPort:
    """Port silnika dla `caretaker.ACTIONS[…].apply` (PT-143): jedyne miejsca, w których akcja opiekuna zmienia
    kartę. Kolejne akcje dopisują tu własne metody."""

    def __init__(self, engine, card: str, run: dict):
        self.engine, self.card, self.run = engine, card, run

    def escalate(self, problem: dict, params: dict) -> None:
        run = self.run
        self.engine.journal.append(self.card, caretaker.ESCALATED, problem=run.get("problem") or problem.get("id"),
                                   step=run.get("step") or "", target=run.get("target") or "",
                                   ticket=run.get("ticket") or "", action="escalate",
                                   summary=params.get("summary") or "",
                                   recommendation=params.get("recommendation") or "",
                                   optionId=params.get("optionId") or "", failed=False, error="", code="",
                                   mode=run.get("mode") or "background")

    def retry(self, problem: dict, params: dict) -> dict:
        """Akcja `retry` (PT-150), wołana wewnątrz pary „zamiar → wynik” `caretaker-action`, więc powtórzona po
        restarcie musi być bezpieczna: zapis już zrobiony przez ten krok opiekuna nie powstaje drugi raz.

        Problem z decyzją — istniejące `resolve_step` `retry` z instrukcją jako `answer` (`step.decision` z `auto`
        i `caretaker`); bez decyzji — wewnętrzna ścieżka automatycznego ponowienia: `step.retry` z `caretaker`
        i instrukcją, a wznowienie z `parentStep` robi `_advance`. Krok spoza problemu → `not-problem-step`."""
        engine, run = self.engine, self.run
        st = engine.journal.state(self.card)
        name, ident = params.get("step") or "", run.get("problem") or problem.get("id") or ""
        # Zapis sprzed restartu (`step.retry` albo `step.decision` z `caretakerStep` tego kroku opiekuna) — przed
        # walidacją problemu i `resolve_step`: decyzja już jest, więc krok „nie czeka” (np. inna decyzja karty
        # w `focus`), a skutek mimo to powstał dokładnie raz.
        own = run.get("step") or ""
        if own and any((found.get(name) or {}).get("caretakerStep") == own
                       for found in (st.step_retries, st.step_decisions)):
            return {"target": name, "label": self._label(st, name)}
        if name not in caretaker.problem_steps(problem, st):
            where = "nie należy do problemu" if name not in st.steps or not self._in_chain(st, problem, name) \
                else "nie czeka na ponowienie"
            return {"failed": caretaker.NOT_PROBLEM_STEP, "target": name,
                    "error": f"{caretaker.NOT_PROBLEM_STEP}: krok {name[:80]} {where} {ident}"}
        instruction = params.get("instruction") or ""
        if problem.get("decision"):
            reply = engine.resolve_step(self.card, {"choice": "retry", "answer": instruction, "step": name},
                                        caretaker_problem=ident, caretaker_step=own)
            if not reply.get("ok") or reply.get("step") != name or reply.get("choice") != "retry" or \
                    reply.get("tracker"):
                return {"failed": "retry-not-applied",
                        "error": f"retry-not-applied: nie zapisano ponowienia kroku {name[:80]}"}
        else:
            step, done = st.steps[name], st.finished.get(name) or {}
            engine.journal.append(self.card, "step.retry", step=name, ticket=step.get("ticket") or "",
                                  reason=done.get("reason") or "", caretaker=ident,
                                  caretakerStep=run.get("step") or "", instruction=instruction)
        return {"target": name, "label": self._label(st, name)}

    def retry_stage(self, problem: dict, params: dict) -> dict:
        """Akcja `retry-stage` (PT-147), wołana wewnątrz pary „zamiar → wynik” `caretaker-action`: przebieg etapu
        karty od razu, raz (`FlowEngine._retry_stage`)."""
        return self.engine._retry_stage(self.card, self.run, problem)

    def resolve_step(self, problem: dict, request: dict, *, step_kinds=()) -> dict:
        """Wspólna decyzja kroku z rejestru: zakres problemu, ograniczenie rodzajów i idempotencja skutku."""
        st = self.engine.journal.state(self.card)
        name, own = request["step"], self.run.get("step") or ""
        previous = st.step_decisions.get(name) or {}
        if own and previous.get("caretakerStep") == own and previous.get("choice") == request["choice"]:
            return {"target": name, "label": self._label(st, name)}
        if name not in caretaker.problem_steps(problem, st):
            return {"failed": caretaker.NOT_PROBLEM_STEP,
                    "error": f"krok {name[:80]} nie należy do problemu albo już nie czeka"}
        if step_kinds and (st.steps.get(name) or {}).get("kind") not in step_kinds:
            return {"failed": "substantive-question", "error": "pytania planowania i zakresu wymagają użytkownika"}
        try:
            self.engine.resolve_step(self.card, request,
                                     caretaker_problem=self.run.get("problem") or problem["id"], caretaker_step=own)
        except FlowError as exc:
            return {"failed": exc.code, "error": str(exc)}
        return {"target": name, "label": self._label(st, name)}

    def return_plan(self, problem: dict, params: dict) -> dict:
        """Akcja `return-plan` (PT-146), wołana wewnątrz pary „zamiar → wynik” `caretaker-action`: odesłanie planu
        istniejącym `edit_plan` (`action: return`) — bez kopii walidacji. Powtórzona po restarcie nie zapisuje
        drugiego `plan.returned` (rozpoznaje własny zapis po `caretakerStep`). Numery `findings` (od 1) to uwagi
        ostatniego review (`caretaker.review_items`); numer spoza listy to nieudane działanie z powodem."""
        engine, run = self.engine, self.run
        st = engine.journal.state(self.card)
        own, ident = run.get("step") or "", run.get("problem") or problem.get("id") or ""
        target = problem.get("step") or run.get("target") or ""
        if own and any(e.get("caretakerStep") == own for e in st.plan_returns):
            return {"target": target, "label": "plan"}
        items, chosen = caretaker.review_items(st), []
        for part in (params.get("findings") or "").replace(";", ",").replace(" ", ",").split(","):
            if not part:
                continue
            try:
                index = int(part) if part.isdigit() else 0
            except ValueError:
                # `isdigit()` dopuszcza też np. „²”, którego `int()` nie umie odczytać.
                index = 0
            if not 1 <= index <= len(items):
                return {"failed": "bad-findings", "target": target,
                        "error": f"bad-findings: uwaga {part[:20]} spoza listy uwag review ({len(items)})"}
            if items[index - 1] not in chosen:
                chosen.append(items[index - 1])
        request = {"action": "return", "feedback": params.get("feedback") or ""}
        if chosen:
            request["findings"] = chosen
        try:
            engine.edit_plan(self.card, request, caretaker_problem=ident, caretaker_step=own, caretaker_from=target)
        except FlowError as exc:
            return {"failed": exc.code, "target": target, "error": f"{exc.code}: {exc}"[:caretaker.REASON_MAX]}
        return {"target": target, "label": "plan"}

    @staticmethod
    def _label(st: FlowState, name: str) -> str:
        step = st.steps.get(name) or {}
        return step_name(step.get("kind"), step.get("ticket")) if step else name

    @staticmethod
    def _in_chain(st: FlowState, problem: dict, name: str) -> bool:
        root, ticket = problem.get("root") or "", problem.get("ticket") or ""
        if root:
            return caretaker.chain_root(st, name) == root
        return not ticket or (st.steps.get(name) or {}).get("ticket") == ticket


def settings_path(env=None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "tide", SETTINGS_FILE)


def guard_seconds(path: str) -> float:
    """Czas strażnika kroku z ustawień (minuty, liczba > 0); brak pliku albo zła wartość — 15 minut."""
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh).get("stepGuardMinutes", GUARD_MINUTES)
    except (OSError, ValueError, AttributeError):
        value = GUARD_MINUTES
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        value = GUARD_MINUTES
    return float(value) * 60.0


def timeout_seconds(path: str) -> float:
    """Maksymalny czas jednej próby kroku (domyślnie cztery godziny)."""
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh).get("stepTimeoutMinutes", TIMEOUT_MINUTES)
    except (OSError, ValueError, AttributeError):
        value = TIMEOUT_MINUTES
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        value = TIMEOUT_MINUTES
    return float(value) * 60.0


class FlowEngine(CaretakerChat, PlanEditing):
    def __init__(self, store, journal: Journal | None = None, *, claude_projects: str = "",
                 systemd_run: str = "", catalog=None, models_config=None, model_probe=None, codex_roots=None,
                 tmp_root: str | None = None, settings: str | None = None,
                 flow_git: FlowGit | None = None, clock=time.time, which=shutil.which, grace: float = 10.0,
                 guard_every: float = 30.0, terminal=None, task_files: TaskFiles | None = None,
                 limits_source=None, runner: Runner | None = None):
        self.store = store
        self.runner = runner or HostRunner(systemd_run)   # PT-180: jedyna droga uruchomienia i zatrzymania kroku
        self.journal = journal or Journal(store.root)
        self._outside_active = None     # krok → (karta, jednostka); budowane z dzienników przy pierwszym użyciu
        self._outside_waiting = {}      # krok → (karta, projekt, czas zgody, ostatnie sprawdzenie): kolejka FIFO
        self._worktree_heads: dict = {}  # katalog administracyjny worktree → (HEAD, gitdir) z ostatniego zdrowego odczytu
        self._main_branches: dict[str, str] = {}  # odczyt w ticku; projekcja migawki nie uruchamia gita
        self._preview_secret = os.urandom(32)  # token sprzed restartu nie uprawnia do nowej operacji
        self.claude_projects = claude_projects
        self.systemd_run = systemd_run
        self.catalog = catalog            # funkcja() → models.executors.Catalog
        self.models_config = models_config  # funkcja() → ModelsConfig (lokalny i inni dostawcy)
        self.limits_source = limits_source  # świeża migawka Limitów AI z wątku monitora; bez HTTP w kroku
        self.sessions_source = None  # migawka monitora także dla sesji ukrytych na liście agentów
        self.model_probe = model_probe      # funkcja(endpoint) → stan punktu lokalnego
        self.codex_roots = codex_homes.default_homes() if codex_roots is None else codex_roots
        self.tmp_root = tmp_root or tempfile.gettempdir()
        self.settings = settings or settings_path()
        self.box_rules = None   # plik reguł inet tide-box (None — nftcontract.rules_file(): /etc/tide-box/…)
        self.git = flow_git or FlowGit()
        self.clock = clock
        self.tracker = TrackerOutbox(self.journal, clock)
        self.which = which
        self.grace = grace
        self.guard_every = guard_every
        # Czas procesora jednostki kroku jako znak postępu strażnika (None w testach bez systemd).
        self.unit_cpu = stepstop.unit_cpu_bucket
        # Strażnik opiekuna (PT-158): zamrożenie jednostki kroku na czas działania Tide (atrapa w testach).
        self.freeze_unit = stepstop.freeze_unit
        self.terminal = terminal
        self.task_files = task_files or TaskFiles()
        self.procs: dict[str, object] = {}         # krok → Popen (tylko kroki uruchomione przez tę usługę)
        self.watch: dict[str, tuple] = {}          # krok → (odcisk postępu, od kiedy bez zmian, ostatnie sprawdzenie)
        self._warnings: dict[str, str] = {}
        self._hidden: tuple = (frozenset(), ())
        self._busy: tuple = ()
        self._notices: list[dict] = []
        self._local_claims: dict[str, object] = {}
        self.box_launcher = None        # PT-172: funkcja(slug, profil, "build"|"rebuild"); None — wątek z Builderem (#4)
        self.box_builder = None         # PT-172: fabryka() → Builder dla BoxStatus; None — Builder z ustawień pudełka
        # PT-181: krok w pudełku. None — ustawienia z `cardflow.json`, proxy uruchomione w usłudze, BoxRunner z ustawień.
        self.box_settings_source = None  # funkcja() → BoxSettings
        self.box_proxy = None            # obiekt z set_box_policy/clear_box_policy, listen, port
        self.box_runner_factory = None   # funkcja(ustawienia, slug, karta, per_card) → BoxRunner
        self.box_sleep = None            # funkcja(sekundy) — czekanie na start pudełka
        self._box_runners: dict = {}     # pudełko → BoxRunner do sprawdzania i zatrzymywania kroków z dziennika
        self._box_network_state = None  # proxy i zestaw kroków, których polityki oraz atrapy już odtworzono
        self._box_network_errors = {}   # nieczytelny kontekst zatrzymuje tylko dotknięty krok
        self._env_cache: dict = {}      # karta → (klucz, sekcja „Środowisko karty”)
        self.trial_gate = trialgate.TrialGate()  # zamknięty magazyn do odbioru PT-270/PT-271

    @staticmethod
    def _trial_check(ticket):
        try:
            if isinstance(ticket, dict):
                if trialhold.held(ticket):
                    raise trialgate.TrialDenied(trialgate.MESSAGE)
            else:
                trialgate.require_ticket(ticket)
        except trialgate.TrialDenied as exc:
            raise FlowError("k38-trial-held", str(exc)) from None

    def _process_state(self, card):
        """Ścisły odczyt i jeden model tożsamości przed skutkami procesu."""
        try:
            return fold(trialhold.read_process_history(self.journal, card))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise FlowError("process-history", f"nieznana historia procesów — operacja wstrzymana: {exc}") from None

    def _process_check(self, card, step):
        st = self._process_state(card)
        name = step.get("step") or step.get("key") or ""
        identity = st.process_identities.get(name)
        if identity is not None:
            if identity.held:
                raise FlowError("k38-trial-held", trialgate.MESSAGE)
            from .processidentity import START_FIELDS
            if any(k in step and k in identity.data and step[k] != identity.data[k] for k in START_FIELDS):
                raise FlowError("process-history", "tożsamość procesu nie zgadza się z historią")
        self._trial_check(step)
        return st

    def register_trial_diagnostic(self):
        """Wyłącznie integracja hostowej procedury PT-273; brak polecenia i ścieżek w żądaniu.

        Nie jest metodą D-Bus ani obsługą wyniku agenta. Do odbioru PT-273 odmawia.
        Każda rejestracja ma nową tożsamość, także po restarcie.
        """
        with self.journal.lock:
            try:
                events = trialhold.read_process_history(self.journal, "K-38")
                return self._register_trial_diagnostic(events)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise FlowError("k38-trial-held", "K-38 v1: niepełna historia diagnostyki") from exc

    def _register_trial_diagnostic(self, events):
        from dataclasses import asdict
        name = "k38-diagnostic-" + uuid.uuid4().hex
        try:
            binding = self.trial_gate.register_bootstrap(name)
        except trialgate.TrialDenied as exc:
            raise FlowError("k38-trial-held", str(exc)) from None
        for event in events:
            if event.get("type") != "k38.diagnostic-requested":
                continue
            previous = event.get("binding")
            if (not isinstance(previous, dict) or not previous.get("target") or not previous.get("attempt")
                    or previous["target"] == binding.target or previous["attempt"] == binding.attempt):
                raise FlowError("k38-trial-held", "K-38 v1: wymagany nowy cel i próba także po restarcie")
        self.journal.append_complete("K-38", "k38.diagnostic-requested", step=name, binding=asdict(binding))
        return {"ok": True, "step": name}

    def _resolve_trial_diagnostic(self, card, request):
        # Walidacja, konsumpcja i końcowa kontrola procesu pod jedną blokadą.
        # Zapis zgody nie może wcześniej naprawić uszkodzonego fallbacku Tablicy.
        with self.journal.lock:
            try:
                events = trialhold.read_process_history(self.journal, card)
                return self._start_trial_diagnostic(card, request, events)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise FlowError("k38-trial-held", "K-38 v1: niepełna historia diagnostyki") from exc

    def _start_trial_diagnostic(self, card, request, events):
        name = request.get("step")
        if (card != "K-38" or set(request) != {"step", "choice"} or request.get("choice") != "run"
                or not any(e.get("type") == "k38.diagnostic-requested" and e.get("step") == name for e in events)
                or any(e.get("type") == "k38.diagnostic-consumed" and e.get("step") == name for e in events)):
            raise FlowError("k38-trial-held", "brak hostowej zgody na zarejestrowaną diagnostykę")
        # Dziennik konsumujemy PRZED startem. Restart lub błąd wymaga nowej rejestracji i decyzji.
        self.journal.append_complete(card, "k38.diagnostic-consumed", step=name)
        def safe():
            try:
                cards = set(self.journal.cards_complete()) | {"K-38"}
            except (OSError, ValueError):
                return False
            states = [trialhold.inventory(self, c, fold([])) for c in sorted(cards)]
            return all(states)
        try:
            self.trial_gate.start_registered(name, consent=True, inventory=safe, bootstrap=True)
        except trialgate.TrialDenied as exc:
            raise FlowError("k38-trial-held", str(exc)) from None
        return {"ok": True, "step": name, "id": card}

    # --- odczyt (wątek główny) -------------------------------------------------------------------

    def has_journal(self, card_id: str) -> bool:
        return self.journal.has(card_id)

    def add_step_note(self, card_id: str, request: dict) -> dict:
        from . import stepnotes
        try:
            card = f"K-{card_num(card_id)}"
            return stepnotes.add(self.journal, card, request, fold)
        except (BoardError, stepnotes.NoteError) as exc:
            raise FlowError(exc.code, str(exc)) from None

    def _settings_data(self) -> dict:
        try:
            with open(self.settings, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, UnicodeError):
            return {}

    def migrate_environment(self) -> list:
        """PT-190: raz przy starcie usługi tworzy `box.proposed.yaml` z `networkDomains`/`outsideCommands`
        (tylko propozycja do zatwierdzenia; stare klucze zostają w cardflow.json)."""
        from ..box import migration
        data = self._settings_data()
        return migration.migrate(migration.settings_slugs(data), data)

    def get_flow_settings(self) -> dict:
        data = self._settings_data()
        transfer = data.get("limitTransfer") or {}
        if not isinstance(transfer, dict):
            transfer = {}
        threshold = data.get("limitThresholdPercent", 10)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 100:
            threshold = 10
        mode = transfer.get("mode") if transfer.get("mode") in ("ask", "auto") else "ask"
        executor = transfer.get("executor") if isinstance(transfer.get("executor"), str) else ""
        slots = data.get("providerSlots") if isinstance(data.get("providerSlots"), dict) else {}
        slots = {k: v for k, v in slots.items() if isinstance(k, str) and isinstance(v, int)
                 and not isinstance(v, bool) and 1 <= v <= MAX_PROVIDER_SLOTS}
        box = bsettings.from_dict(data)
        return {"ok": True, "limitTransfer": {"mode": mode, "executor": executor},
                "limitThresholdPercent": threshold, "providerSlots": slots,
                "providerSlotsDefault": DEFAULT_PROVIDER_SLOTS, "reviewRoundsBeforeQuestion": self._review_rounds(),
                **caretaker.settings_of(data), "box": bsettings.view(box), "boxWarnings": list(box.warnings)}

    def _box_patch(self, request: dict, data: dict) -> dict | None:
        """PT-292: sekcja `box` po częściowym zapisie (`None` — żądanie bez `box`). Ścisła walidacja kluczy
        z `box/settings.py`, a zmiana portu albo włączenie pudełka wymaga zgodności z zainstalowanymi regułami
        nftables (`box/nftcontract.py`, tylko odczyt). Zapis nie przełącza działającego proxy ani runnerów:
        proxy czyta port przy starcie usługi."""
        if "box" not in request:
            return None
        patch = request["box"]
        problem = bsettings.problem(patch)
        if problem:
            raise FlowError("bad-settings", problem)
        stored = data.get("box") if isinstance(data.get("box"), dict) else {}
        merged = {**stored, **patch}
        before, after = bsettings.from_dict({"box": stored}), bsettings.from_dict({"box": merged})
        port_changed = after.proxy_port != before.proxy_port
        activated = after.enabled and (not before.enabled or port_changed or after.bridge != before.bridge)
        if port_changed or activated:
            path = self.box_rules or nftcontract.rules_file()
            problem = nftcontract.check_file(path, port=after.proxy_port, bridge=after.bridge)
            if problem:
                raise FlowError("box-install-mismatch", problem)
        return merged

    def set_flow_settings(self, request: dict) -> dict:
        allowed = {"limitTransfer", "limitThresholdPercent", "providerSlots", "reviewRoundsBeforeQuestion", "box",
                   *caretaker.SETTINGS_DEFAULTS}
        if not isinstance(request, dict) or set(request) - allowed:
            raise FlowError("bad-settings", "niepoprawne ustawienia przepływu karty")
        # Ustawienia opiekuna (PT-151): walidacja w usłudze, zanim cokolwiek trafi do pliku.
        care = {key: request[key] for key in caretaker.SETTINGS_DEFAULTS if key in request}
        catalog = self._catalog_now() if "caretakerExecutor" in care else None
        for key, value in care.items():
            problem = caretaker.setting_problem(key, value, catalog)
            if problem:
                raise FlowError(*problem)
        if "caretakerExecutor" in care:
            care["caretakerExecutor"] = care["caretakerExecutor"].strip()
        current = self.get_flow_settings()
        transfer = request.get("limitTransfer", current["limitTransfer"])
        if not isinstance(transfer, dict) or set(transfer) - {"mode", "executor"}:
            raise FlowError("bad-settings", "niepoprawne ustawienia przekazania")
        mode = transfer.get("mode", current["limitTransfer"]["mode"])
        executor = transfer.get("executor", current["limitTransfer"]["executor"])
        if mode not in ("ask", "auto") or not isinstance(executor, str) or len(executor) > 120 or \
                (mode == "auto" and not executor.strip()):
            raise FlowError("bad-settings", "automatyczne przekazanie wymaga wykonawcy")
        threshold = request.get("limitThresholdPercent", current["limitThresholdPercent"])
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 100:
            raise FlowError("bad-settings", "próg limitu musi być procentem od 0 do 100")
        slots = request.get("providerSlots", current["providerSlots"])
        if not isinstance(slots, dict) or any(
                not isinstance(k, str) or not k or len(k) > 40 or isinstance(v, bool) or not isinstance(v, int)
                or not 1 <= v <= MAX_PROVIDER_SLOTS for k, v in slots.items()):
            raise FlowError("bad-settings", f"liczba kroków naraz na dostawcę musi być od 1 do {MAX_PROVIDER_SLOTS}")
        # Rundy review przed pytaniem użytkownika (08.11: decyzja o progu 4 bez ręcznej edycji ~/.config).
        rounds = request.get("reviewRoundsBeforeQuestion", current["reviewRoundsBeforeQuestion"])
        if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 20:
            raise FlowError("bad-settings", "liczba rund review przed pytaniem musi być od 1 do 20")
        data = self._settings_data()
        box = self._box_patch(request, data)   # odmowa przed zapisem: plik zostaje bez zmian
        data.update(limitTransfer={"mode": mode, "executor": executor.strip()},
                    limitThresholdPercent=threshold, providerSlots=dict(slots), **care)
        if "reviewRoundsBeforeQuestion" in request:
            data["reviewRoundsBeforeQuestion"] = rounds
        if box is not None:
            data["box"] = box
        self._write_settings(data)
        return self.get_flow_settings()

    def _catalog_now(self):
        """Katalog wykonawców z models.json (albo domyślny); None — silnik bez katalogu."""
        cfg = self.models_config() if self.models_config else None
        return cfg.catalog if cfg is not None else self.catalog() if self.catalog else None

    def _write_settings(self, data: dict) -> None:
        folder = os.path.dirname(self.settings) or "."
        os.makedirs(folder, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".cardflow-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.settings)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _low_limit(self, provider: str) -> dict | None:
        if self.limits_source is None:
            return None
        try:
            snapshot = self.limits_source()
        except Exception:
            return None
        return flow_limits.low_limit(snapshot, provider, self.get_flow_settings()["limitThresholdPercent"],
                                     self.clock())

    def _limit_view(self, st: FlowState, out: dict) -> None:
        """`flow.otherProviders[]` i `flow.limitTransfer` (PT-130) z odczytu Limitów AI i katalogu wykonawców;
        silnik niczego tu nie zmienia — przekazanie idzie istniejącym `ResolveCardStep`."""
        flow = out["flow"]
        stopped = flow.get("stoppedByLimit") or []
        if not stopped:
            return
        try:
            snapshot = self.limits_source() if self.limits_source else {}
        except Exception:
            snapshot = {}
        flow["otherProviders"] = flow_limits.other_providers(
            snapshot, {item["provider"] for item in stopped}, self.clock())
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        settings = self.get_flow_settings()
        preferred = settings["limitTransfer"]["executor"]
        wanted = (st.steps.get(stopped[0]["step"]) or {}).get("executor") or ""
        own = getattr(catalog.get(wanted), "classes", ()) if catalog is not None and wanted else ()

        def pick(provider: str) -> str:
            options = [e for e in (catalog.executors if catalog is not None else ())
                       if e.provider == provider and e.program and e.model]
            for executor in options:
                if executor.name == preferred:
                    return executor.name
            for executor in options:
                if own and set(executor.classes) & set(own):
                    return executor.name
            return options[0].name if options else ""

        flow["limitTransfer"] = fullcard.limit_transfer(stopped, flow["otherProviders"],
                                                        settings["limitThresholdPercent"], pick)

    def _environment_view(self, card: str, st: FlowState, out: dict) -> None:
        """PT-172: `flow.boxWait` i sekcja „Środowisko karty” w planie do zatwierdzenia (albo zatwierdzonym z „Tylko plan”).
        Błąd odczytu profilu nie psuje migawki: sekcja dostaje komunikat zamiast pozycji."""
        from . import boxstep, boxwait, envconsent
        wait = boxwait.card_box_wait(st)
        flow = out.get("flow") or {}
        flow["boxWait"], flow["boxWaitLabel"] = wait, boxwait.label(wait)
        # 09.11: przy wyłączonym pudełku zgoda środowiska nie obowiązuje (PT-290), więc sekcji nie liczymy — chyba
        # że karta już czeka na środowisko (dawna historia). Bez tego każde odświeżenie kart wczytywało całą
        # Tablicę po kilka razy na kartę (pomiar: 107 wczytań na odświeżenie, odpowiedzi D-Bus 14–46 s).
        if wait != boxwait.ENVIRONMENT and not boxstep.settings_of(self).enabled:
            return
        plan = flow.get("plan")
        try:
            slug, _repo = envconsent._project_state(self, card)
        except FlowError:
            return
        if slug == "plasma-tide":
            try:
                flow["environment"] = envconsent.model(self, card, st)
                if plan:
                    plan["environment"] = flow["environment"]
            except Exception as exc:
                flow["environment"] = {"projectExposure": True, "items": [], "notes": [str(exc)],
                                       "stateLabel": "brak zgodnego manifestu", "yaml": "", "exposure": {}}
            return
        if not plan or not (plan.get("status") == "pending" or wait == boxwait.ENVIRONMENT):
            return
        proposal = st.plan_proposed or {}
        try:
            slug, _repo = envconsent._project_state(self, card)
            stamp = tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in
                          (envconsent.bp.project_path(slug), envconsent.bp.card_path(slug, card),
                           envconsent.bs.journal_path(slug)))
            key = (proposal.get("seq"), wait, bool(st.plan_approved), stamp)
            hit = self._env_cache.get(card)
            if hit is None or hit[0] != key:
                hit = (key, envconsent.model(self, card, st))
                self._env_cache[card] = hit
            plan["environment"] = hit[1]
        except Exception as exc:
            plan["environment"] = {"items": [], "notes": [f"środowisko karty: {exc}"], "state": "error", "stateLabel": "",
                                   "boxWait": wait, "boxWaitLabel": boxwait.label(wait), "question": "", "yaml": "",
                                   "acceptKey": envconsent.CONSENT_KEY, "diff": {"added": [], "removed": []}}

    def approve_environment(self, card_id: str, text: str, only_plan: bool) -> dict:
        """ApproveCardEnvironment: walidacja i klasyfikacja w usłudze, zapis do Tablicy, `box.approved`, budowa w tle."""
        from . import envconsent
        return envconsent.approve(self, card_id, text, bool(only_plan))

    def box_status(self, slug: str, card: str = "") -> dict:
        from . import envconsent
        return envconsent.status(self, slug, card)

    def stop_box(self, slug: str, card: str = "") -> dict:
        from . import boxcontrol
        return boxcontrol.stop(self, slug, card)

    def card_base(self, card_id: str) -> dict:
        """Baza migawki niezależna od tego, czy karta ma wyliczany stan przepływu."""
        st = self.journal.state(card_id)
        if not st.started:
            return {}
        base = base_of(st)
        return {**{key: base[key] for key in ("baseRef", "baseBranch", "baseCommit")},
                "baseVersion": st.base_version}

    def derived(self, card: dict) -> dict | None:
        st = self.journal.state(card.get("id") or "")
        if st.started is not None:
            st.caretaker_setup = self._caretaker_setup(st)
        out = derived(st, card)
        if out:
            ticketentry.enrich(st, out)
            self._limit_view(st, out)
            self._environment_view(card.get("id") or "", st, out)
            trialhold.view(self, card.get("id") or "", out)
            if st.closed and not st.merged:
                out["flow"]["mainBranch"] = self._main_branches.get((st.started or {}).get("repo") or "", "")
        step = st.steps.get(st.last_step) or {}
        if not out or out.get("column") != "progress" or step.get("mode") != "interactive" or \
                st.last_step in st.finished or step.get("consoleClosed") or not step.get("session") or \
                self.sessions_source is None:
            return out
        try:
            sessions = self.sessions_source() or []
        except Exception:
            return out
        sid = f"{step.get('program')}:{step['session']}"
        waiting = next((s for s in sessions if s.get("id") == sid and s.get("state") == "waiting"), None)
        if waiting is None:
            return out
        question = " ".join(str(waiting.get("waitingText") or "").split())[:120]
        out.update(column="waiting", agentState="waiting", question=question,
                   note=(f"pytanie w Konsoli: {question}" if question else "czeka na odpowiedź w Konsoli"),
                   nextStep="odpowiedz w Konsoli")
        out["flow"]["consoleWaiting"] = True
        return out

    def tracker_tickets(self, card):
        st = self.journal.state(card.get('id') or '')
        if not st.started:
            return None
        events = self.journal.read(card['id'])
        merged = {key for (action, key), value in st.results.items() if action == 'merge' and value.get('ok')}
        tickets = []
        for (action, _), result in st.results.items():
            if action != 'reserve' or not result.get('ok'):
                continue
            ticket = result['ticket']
            if not any(i.get('youtrack') and i.get('action') == 'reserve' and
                       i.get('key') == result.get('key') for i in st.intents.values()):
                continue
            related = [e for e in events if e.get('ticket') == ticket or e.get('key') == ticket]
            stamp = lambda e: datetime.fromisoformat(e['ts']).timestamp()
            tickets.append({'id': ticket, 'state': self._plan_row_state(st, ticket, merged),
                            'linkedTs': stamp(result),
                            'activityTs': max([stamp(e) for e in related] or [stamp(result)])})
        return {'tickets': tickets}

    def warnings(self) -> list[dict]:
        return [{"path": self.journal.path(card), "message": text} for card, text in sorted(self._warnings.items())]

    def take_notices(self) -> list[dict]:
        notices, self._notices = self._notices, []
        return notices

    def busy_places(self) -> list[str]:
        """Worktree trwających kroków i worktree kart w toku — dla okna Worktrees zajęte jak katalog agenta (bez
        usuwania). Sesja kroku nie jest wierszem agenta, więc bez tego worktree kroku wyglądałby na wolny."""
        busy = set()
        for card in self.journal.cards():
            st = self.journal.state(card)
            for intent in st.intents.values():
                if intent.get("action") == "change-base":
                    busy.update(r[p] for r in intent["generation"] for p in ("worktree", "oldWorktree"))
            busy.update(step["worktree"] for step in st.running() if step.get("worktree"))
            busy.update(step["worktree"] for step in st.steps.values() if step.get("worktree") and
                        step.get("mode") == "interactive" and not step.get("consoleClosed"))
            if st.started is not None and not st.card_finished:
                worktree = ticket_of(st)["cardWorktree"]
                if worktree:
                    busy.add(worktree)
        return sorted(busy)

    def is_hidden(self, session: dict) -> bool:
        """Sesja kroku w tle: nie jest wierszem agenta (kafelek Praca, lista agentów, powiadomienia)."""
        ids, places = self._hidden
        if str(session.get("sessionId") or "") in ids:
            return True
        cwd = os.path.realpath(str(session.get("cwd") or "")) if session.get("cwd") else ""
        return bool(cwd) and any(cwd == p or cwd.startswith(p + os.sep) for p in places)

    # --- zlecenie --------------------------------------------------------------------------------

    def _start_base(self, card, project, request, st, *, resume=False):
        """Jedno rozstrzygnięcie bazy dla wejść karty, przed trwałym zapisem zlecenia."""
        previous = ticket_of(st)["cardBranch"]
        chosen = request.get("cardBranch")
        if chosen is not None and (not isinstance(chosen, str) or not chosen
                or chosen.startswith("-") or chosen == "HEAD"
                or not self.git.run(project.repo, "check-ref-format", f"refs/heads/{chosen}").ok):
            raise FlowError("bad-request", "niepoprawna nazwa nowej gałęzi karty")
        if previous and chosen and chosen != previous:
            raise FlowError("branch-mismatch", "karta ma już inną gałąź — nie można zmienić jej nazwy przy starcie")
        branch = previous or chosen or f"card/{card.id.lower()}-{slug_from_prompt(card.title, words=4, length=32)}"
        if chosen and not previous and not resume and self.git.sha(project.repo, f"refs/heads/{chosen}"):
            raise FlowError("branch-exists", "gałąź o tej nazwie już istnieje — podaj nazwę nowej gałęzi karty")
        # Utrwalony wybór może już nie istnieć albo wskazywać inny commit.
        if resume or (previous and request.get("baseRef") in (None, base_of(st)["baseRef"])):
            base = base_of(st)
            if base["status"] != "known" and (not previous or not self.git.sha(project.repo, f"refs/heads/{previous}")):
                raise FlowError("no-base", "historyczna baza jest nieznana i nie ma gałęzi karty — "
                                "przywróć gałąź karty albo rozpocznij nową kartę z wybraną bazą")
            if not resume and request.get("baseCommit") is not None and (
                    base["status"] != "known" or request["baseCommit"] != base["baseCommit"]):
                raise FlowError("base-mismatch", "oczekiwany commit różni się od zapisanej bazy karty")
        else:
            try:
                base = self.git.resolve_base(project.repo, request.get("baseRef"), request.get("baseCommit"))
            except FlowGitError as exc:
                raise FlowError("no-base", str(exc)) from None
            if previous or self.git.sha(project.repo, f"refs/heads/{branch}"):
                recorded = base_of(st)
                if recorded["status"] != "known" or any(
                        recorded[key] != base[key] for key in ("baseRef", "baseBranch", "baseCommit")):
                    raise FlowError("base-mismatch", "gałąź karty ma inną lub nieznaną bazę — "
                                    "zmiana bazy wymaga osobnej korekty karty")
        return {key: base[key] for key in ("baseRef", "baseBranch", "baseCommit")} | {
            "baseVersion": 1, "cardBranch": branch, "request": request}

    def _planning_branch(self, card, st, project):
        """Kontynuacje korzystają z aktualnej karty; tylko stare dzienniki wymagają polityki głównej."""
        item, _ = self._project(card)
        branch = (ticket_of(st)["cardBranch"] or st.started.get("cardBranch")
                  or f"card/{card.lower()}-{slug_from_prompt(item.title, words=4, length=32)}")
        for row in st.base_change.get("generation", []):
            if row["action"] == "card-branch" and row["branch"] == branch:
                # Aktywacja już przygotowała ten zasób; nie zakładamy nowej operacji pod nowym refem.
                return st.results[(row["action"], row["key"])]
        base = base_of(st)
        start = (f"refs/heads/{branch}" if self.git.sha(project.repo, f"refs/heads/{branch}") else
                 base["baseCommit"] if base["status"] == "known" else self.git.main_branch(project.repo))
        if not start:
            raise FlowError("no-base", "brak przypiętej bazy albo gałęzi karty")
        worktree = worktree_for(project.repo, branch)
        return self._act(card, "card-branch", branch,
                         lambda: self.git.branch_worktree(project.repo, branch, worktree, start),
                         repo=project.repo, branch=branch, worktree=worktree, base=start,
                         baseRef=base["baseRef"], baseBranch=base["baseBranch"])

    def start(self, card_id: str, request: dict, *, _resume=False, _order=None) -> dict:
        # Ta sama granica dla zwykłego StartCard, go i jawnego retry. Flock Tablicy
        # chroni również drugą instancję usługi, RLock — dziennik w tym procesie.
        try:
            card_id = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        with self.journal.lock, self.store.transaction():
            from . import startorder
            return startorder.start(self, card_id, request, resume=_resume, order=_order)

    def _start(self, card_id: str, request: dict, *, _resume=False, _prepare=None, _order=None) -> dict:
        try:
            card_id = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if baseproposal.blocked(self.journal.state(card_id)):
            raise FlowError("base-decision", "najpierw rozstrzygnij pytanie o bazę karty")
        unknown = [k for k in request if k not in REQUEST_FIELDS]
        if unknown:
            raise FlowError("bad-request", f"nieznane pole „{unknown[0]}”; dozwolone: {', '.join(REQUEST_FIELDS)}")
        if request.get("projects") and request.get("entry") != "planning":
            # P7: Od razu i Mam plan pracują w jednym projekcie karty.
            raise FlowError("single-project", "Od razu i Mam plan pracują w jednym projekcie karty; "
                                              "kilka projektów zlecenia — wybierz Planowanie")
        if request.get("entry") == "import":
            return self.start_import(card_id, request, _prepare=_prepare, _order=_order)
        if request.get("entry") == "plan":
            if _prepare is not None or _order is not None:
                raise FlowError("bad-request", "Mam plan wymaga entry: import oraz planPath")
            return self.propose_plan(card_id, str(request.get("planPath") or ""), request=request)
        if request.get("entry") == "planning" and request.get("mode") in ("full", "light"):
            return self.start_planning(card_id, request, _prepare=_prepare, _order=_order)
        if (request.get("entry") or "") != "now":
            raise FlowError("bad-request", "w przepływie karty działa na razie wejście „Od razu” (entry: now)")
        try:
            num = card_num(card_id)
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        board = self.store.load()
        card = _prepare if _prepare is not None else board.cards.get(num)
        if card is None:
            raise FlowError("not-found", f"nie ma karty K-{num}")
        if card.column in ("done", "archived"):
            raise FlowError("card-finished", f"{card.id}: karta jest w Gotowych albo Archiwum")
        project = board.projects.get(card.project)
        if project is None or not project.repo:
            raise FlowError("no-project", f"{card.id}: karta potrzebuje projektu z repozytorium")
        if project.repo_missing or not os.path.isdir(project.repo):
            raise FlowError("no-repo", f"nie ma repozytorium {project.repo}")
        name = str(request.get("executor") or "").strip()
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        preferred = str(request.get("preferredProvider") or "").strip()
        if preferred and preferred not in ("mixed", "local") and not catalog.known_provider(preferred):
            raise FlowError("unknown-provider", f"nie ma dostawcy „{preferred}” w katalogu")
        try:
            selected = execution.resolve(name, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if preferred and preferred != "mixed" and selected.provider != preferred:
            raise FlowError("provider-mismatch", f"„{name}” należy do dostawcy {selected.provider}, nie {preferred}")
        preferred = preferred or selected.provider
        preferred_name = "Sonnet" if name == "Opus" else "Codex Astra" if name == "Codex mocny" else "Opus"
        reviewer_name = str(request.get("reviewer") or "").strip()

        def resolve_reviewer(candidate: str):
            try:
                found = execution.resolve(candidate, catalog, config=cfg, probe=self.model_probe, which=self.which)
            except execution.ExecutionError as exc:
                raise FlowError("unknown-reviewer" if exc.code == "unknown-executor" else exc.code, str(exc)) from None
            if found.name == selected.name or not flow_plans.independent_reviewer(selected, found):
                raise FlowError("bad-reviewer", "recenzent musi mieć inny model albo inną siłę myślenia niż autor")
            if selected.local != found.local:
                raise FlowError("bad-reviewer-provider", "praca lokalna wymaga lokalnego recenzenta")
            if preferred == "mixed" and found.provider == selected.provider:
                raise FlowError("bad-reviewer-provider", "w trybie mieszanym recenzent musi być u innego dostawcy")
            if preferred and preferred != "mixed" and found.provider != selected.provider:
                raise FlowError("bad-reviewer-provider", "recenzent musi należeć do wybranego dostawcy")
            return found

        if reviewer_name:
            reviewer = resolve_reviewer(reviewer_name)
        else:
            reviewer = None
            for candidate in [preferred_name, *(e.name for e in catalog.executors)]:
                if candidate == selected.name:
                    continue
                try:
                    reviewer = resolve_reviewer(candidate)
                except FlowError:
                    continue
                break
            if reviewer is None:
                raise FlowError("bad-reviewer", "brak dostępnego recenzenta z innym modelem")
        check = str(request.get("check") or card.check or "auto").strip().lower()
        if check not in ("auto", "manual"):
            raise FlowError("bad-request", "sprawdzenie musi być auto albo manual")
        images = self._images(request.get("images"))
        source_evidence = None if _prepare is not None else self._issue_evidence(card)
        st = self.journal.state(card.id)
        if st.active() and not _resume:
            raise FlowError("flow-active", f"{card.id}: {st.busy_reason()}")
        slug = slug_from_prompt(card.title, words=4, length=32)
        with lock_for(project.repo):
            base = self._start_base(card, project, request, st, resume=_resume)
            card_branch = base["cardBranch"]
            try:
                if not project.youtrack:
                    reservations.reserve(project.repo, count=1, title=card.title, dry_run=True, local_fallback=True)
            except reservations.ReservationError as exc:
                raise FlowError("no-ticket-number", f"numer ticketu: {exc}") from None
            if _prepare is not None:
                return {**request, "baseRef": base["baseRef"], "baseCommit": base["baseCommit"],
                        "cardBranch": card_branch, "executor": selected.name, "reviewer": reviewer.name,
                        "preferredProvider": preferred, "check": check}
            started = st.started if _resume else self.journal.append(card.id, "card.started", entry="now", executor=selected.name,
                                      **({"orderId": _order} if _order else {}),
                                      program=selected.tool, model=selected.model, provider=selected.provider,
                                      executorRouteUrl=selected.route.url if selected.route else "",
                                      executorRouteKind=selected.route.kind if selected.route else "",
                                      executorRouteTokenFile=selected.route.token_file if selected.route else "",
                                      executorEffort=selected.effort,
                                      reviewer=reviewer.name, reviewerProgram=reviewer.tool,
                                      reviewerModel=reviewer.model, reviewerProvider=reviewer.provider,
                                      reviewerRouteUrl=reviewer.route.url if reviewer.route else "",
                                      reviewerRouteKind=reviewer.route.kind if reviewer.route else "",
                                      reviewerRouteTokenFile=reviewer.route.token_file if reviewer.route else "",
                                      reviewerEffort=reviewer.effort,
                                      preferredProvider=preferred or selected.provider, check=check,
                                      repo=project.repo, project=project.slug, column=card.column,
                                      moveId=card.move_id, request=request, trackerProject=project.youtrack,
                                      trackerUrl=yt.load_config()["url"] if project.youtrack else "", images=images,
                                      sourceEvidence=source_evidence.source if source_evidence else None,
                                      baseRef=base["baseRef"], baseBranch=base["baseBranch"],
                                      baseCommit=base["baseCommit"], baseVersion=1)
        self._project_base(card.id)
        launch = f"launch-{started['seq']}"
        request_key = f"{card.id}:{launch}"
        if project.youtrack:
            try:
                reserved = self._tracker_reserve(card.id, launch, 1, card.title, card.description)
            except yt.YouTrackError as exc:
                if exc.permanent:
                    raise FlowError('youtrack', str(exc)) from None
                return {"ok": True, "id": card.id, "queued": True, "message": str(exc)}
        else:
            reserved = self._act(card.id, "reserve", launch,
                                 lambda: self._reserve(project.repo, card.title, request_key),
                                 repo=project.repo, title=card.title)
        ticket = reserved["ticket"]
        card_worktree = worktree_for(project.repo, card_branch)
        made = self._act(card.id, "card-branch", card_branch,
                         lambda: self.git.branch_worktree(project.repo, card_branch, card_worktree, base["baseCommit"]),
                         repo=project.repo, branch=card_branch, worktree=card_worktree, base=base["baseCommit"],
                         baseRef=base["baseRef"], baseBranch=base["baseBranch"])
        ticket_branch = f"ticket/{ticket.lower()}-{slug}"
        ticket_worktree = worktree_for(project.repo, ticket_branch)
        made_ticket = self._act(card.id, "ticket-branch", ticket, lambda: {
            **self.git.branch_worktree(project.repo, ticket_branch, ticket_worktree,
                                       f"refs/heads/{card_branch}"), "ticket": ticket},
            repo=project.repo, branch=ticket_branch, worktree=ticket_worktree,
            base=f"refs/heads/{card_branch}", ticket=ticket)
        step_id = f"{card.id}-{ticket}-{1 + sum(1 for s in st.steps.values() if s.get('ticket') == ticket)}"
        try:
            step = self._launch(card, project, selected, ticket, made, made_ticket, step_id, images)
        except FlowDeferred:
            kept_images = []
            for index, source in enumerate(images):
                rel = os.path.join("queued-images", launch, str(index), os.path.basename(source))
                with open(source, "rb") as fh:
                    self.journal.write_file(card.id, rel, fh.read())
                kept_images.append(os.path.join(self.store.root, self.journal.rel_dir(card.id), rel))
            self.journal.append(card.id, "card.inputs", images=kept_images)
            step = {"session": ""}
        return {"ok": True, "id": card.id, "ticket": ticket, "branch": made_ticket["branch"],
                "worktree": made_ticket["worktree"], "cardBranch": made["branch"], "cardWorktree": made["worktree"],
                "step": step_id, "session": step["session"], "executor": selected.name,
                "queued": "step" not in step, **({'map': {'#1': ticket}} if project.youtrack else {})}

    def start_import(self, card_id: str, request: dict, *, _prepare=None, _order=None) -> dict:
        """Przejmuje wskazany plan; źródło pozostaje w repozytorium bez zmian."""
        card_id = f"K-{card_num(card_id)}"
        item, project = self._start_project(card_id, _prepare)
        if item.column in ("done", "archived"):
            raise FlowError("card-finished", "karta jest zakończona")
        busy = self.journal.state(card_id).busy_reason()
        if busy:
            raise FlowError("flow-active", f"{card_id}: {busy}")
        if not project.repo or project.repo_missing:
            raise FlowError("no-project", "karta potrzebuje dostępnego repozytorium projektu")
        path = os.path.realpath(str(request.get("planPath") or item.plan or ""))
        if not any(p["path"] == path and p["card"] in ("", card_id) and p["status"] != "done"
                   for p in plan_candidates(project.repo, self.store.root)):
            raise FlowError("bad-plan", "wybrany plan nie należy do tej karty i projektu")
        source = source_plans.read(path)
        if source is None or source.errors or not source.rows:
            raise FlowError("bad-plan", "plan jest niepoprawny albo nie ma ticketów")
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        name = str(request.get("executor") or source.rows[0].agent or "Sonnet")
        try:
            selected = execution.resolve(name, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if source.tracker not in ("local", "youtrack") or (source.tracker == "youtrack") != bool(project.youtrack):
            raise FlowError("bad-plan", "tracker planu różni się od projektu karty")
        if project.youtrack and (source.tracker_project != project.youtrack or
                                 source.tracker_url.rstrip("/") != yt.load_config()["url"].rstrip("/")):
            raise FlowError("bad-plan", "projekt albo adres YouTracka w planie różni się od projektu karty")
        try:
            prefix = project.youtrack or reservations.reserve(project.repo, dry_run=True, local_fallback=True)["prefix"]
        except reservations.ReservationError as exc:
            raise FlowError("no-ticket-number", str(exc)) from None
        if source.prefix != prefix:
            raise FlowError("bad-plan", "prefiks planu różni się od prefiksu projektu")
        for row in source.rows:
            number = row.ticket[len(prefix) + 1:] if row.ticket.startswith(prefix + "-") else ""
            if not (flow_plans.WORK_ID.fullmatch(row.ticket) or
                    number and number[0] in "123456789" and number.isdigit()):
                raise FlowError("bad-plan", f"{row.ticket}: nie można zachować istniejącego numeru ticketu")
        check = str(request.get("check") or item.check or "auto")
        if check not in ("auto", "manual"):
            raise FlowError("bad-request", "sprawdzenie musi być auto albo manual")
        images = self._images(request.get("images"))
        source_evidence = None if _prepare is not None else self._issue_evidence(item)
        with open(path, "rb") as fh:
            source_bytes = fh.read(source_plans.MAX_BYTES + 1)
        if len(source_bytes) > source_plans.MAX_BYTES:
            raise FlowError("bad-plan", "plan przekracza 512 KB")
        import_rows = [{"id": row.ticket, "state": row.state, "title": row.title,
                        "agent": row.agent, "blockers": list(row.blockers), "note": row.note}
                       for row in source.rows]
        proposal = flow_plans.read(path, card_id, catalog, allowed_roots=(project.repo, self.store.root),
                                   tracker=source.tracker, imported=True,
                                   existing_ids=[row["id"] for row in import_rows])
        _meta, body = source_plans.split_frontmatter(source_bytes.decode("utf-8"))
        loose_text = any(line.strip() and not line.lstrip().startswith("|") for line in body.splitlines())
        complete = (not proposal.errors and b"| Blokery |" in source_bytes and
                    not loose_text and all(row.agent and row.work_class and row.reviewer and row.note for row in source.rows))
        with lock_for(project.repo):
            base = self._start_base(item, project, request, self.journal.state(card_id))
        if _prepare is not None:
            return {**request, "baseRef": base["baseRef"], "baseCommit": base["baseCommit"],
                    "cardBranch": base["cardBranch"], "executor": selected.name, "check": check}
        self.store.update_card(card_id, {"planPath": path})
        # Kopia robocza nie jest granicą trwałości. Dziennik zachowuje dane przejęcia
        # przed przyjęciem zlecenia, aby restart mógł dokończyć oba późniejsze zapisy.
        prepared = self.journal.append(card_id, "plan.import-prepared", path=path,
                                       source=source_bytes.decode("utf-8"), rows=import_rows,
                                       **({"cards": proposal.cards, "specification": self._plan_spec(path)} if complete else {}),
                                       needsCompletion=not complete)
        self.journal.append(card_id, "card.started", entry="plan", repo=project.repo, project=project.slug,
                            importPreparation=prepared["seq"],
                            **({"orderId": _order} if _order else {}),
                            column=item.column, moveId=item.move_id, check=check, images=images,
                            sourceEvidence=source_evidence.source if source_evidence else None,
                            executor=selected.name, program=selected.tool, model=selected.model,
                            provider=selected.provider, preferredProvider=request.get("preferredProvider") or selected.provider,
                            prefix=prefix, trackerProject=project.youtrack,
                            trackerUrl=yt.load_config()["url"] if project.youtrack else "", **base)
        self._project_base(card_id)
        st = self._recover_import(card_id, self.journal.state(card_id))
        if complete:
            result = self.propose_plan(card_id, st.imported.get("snapshotPath") or path, import_source=True)
            return {**result, "imported": True, "needsCompletion": False}
        self._planning_launch(card_id, self.journal.state(card_id), selected, catalog, images=images)
        st = self.journal.state(card_id)
        return {"ok": True, "id": card_id, "imported": True, "needsCompletion": True,
                "step": st.last_step, "executor": selected.name}

    def _recover_import(self, card: str, st: FlowState) -> FlowState:
        """Dokańcza przejęcie planu tej samej generacji, bez nowego zlecenia."""
        preparation = st.started.get("importPreparation")
        if preparation is None:
            return st
        prepared = next((e for e in self.journal.read(card)
                         if e["seq"] == preparation and e["type"] == "plan.import-prepared"), None)
        if prepared is None:
            raise FlowError("broken-import", "brakuje zapisanego źródła przejęcia planu")

        def restore(rel, text):
            data = text.encode("utf-8")
            target = os.path.join(self.store.root, self.journal.rel_dir(card), rel)
            try:
                with open(target, "rb") as fh:
                    if fh.read() == data:
                        return os.path.relpath(target, self.store.root)
            except FileNotFoundError:
                pass
            return self.journal.write_file(card, rel, data)

        # plan.imported utrwala przejęcie, ale kopie wejść są odbudowywalne.
        # Po awarii systemu mogą zniknąć lub pozostać niepełne także po tym
        # zdarzeniu. Naprawiamy wyłącznie niezmienne wejścia, nie propozycję.
        restore(os.path.join("plan", "source.md"), prepared["source"])
        snapshot = {}
        if "cards" in prepared:
            # Wejścia przejęcia są osobne od później edytowanej propozycji.
            folder = os.path.join("plan", "import")
            rel = restore(os.path.join(folder, "source.md"), prepared["source"])
            for ident, body in prepared["cards"].items():
                restore(os.path.join(folder, "tickets", ident.lstrip("#") + ".md"), body)
            restore(os.path.join(folder, "spec.md"), prepared["specification"])
            snapshot = {"snapshotPath": os.path.join(self.store.root, rel)}
        if st.imported is not None:
            return st
        self.journal.append(card, "plan.imported", path=prepared["path"], rows=prepared["rows"],
                            needsCompletion=prepared["needsCompletion"], **snapshot)
        return self.journal.state(card)

    def _start_project(self, card_id, prepared):
        if prepared is None:
            return self._project(card_id)
        project = self.store.load().projects.get(prepared.project)
        if project is None:
            raise FlowError("no-project", "sprawa potrzebuje projektu z repozytorium")
        return prepared, project

    def start_planning(self, card_id: str, request: dict, *, _prepare=None, _order=None) -> dict:
        card_id = f"K-{card_num(card_id)}"
        item, project = self._start_project(card_id, _prepare)
        if item.column in ("done", "archived"):
            raise FlowError("card-finished", "karta jest zakończona")
        if not project.repo:
            raise FlowError("no-project", "karta potrzebuje projektu z repozytorium")
        if project.repo_missing or not os.path.isdir(project.repo):
            raise FlowError("no-repo", "repozytorium projektu jest niedostępne")
        busy = self.journal.state(card_id).busy_reason()
        if busy:
            raise FlowError("flow-active", f"{card_id}: {busy}")
        prepared_board = None
        if "projects" in request:
            if _prepare is not None:
                item, prepared_board = self._prepare_order_projects(item, request["projects"])
            else:
                item = self._save_order_projects(card_id, item, request["projects"])
        order = self._order_projects(item, project, board=prepared_board)
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        try:
            selected = execution.resolve(str(request.get("executor") or ""), catalog, config=cfg,
                                         probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        mode = str(request.get("mode") or "")
        if mode not in ("full", "light"):
            raise FlowError("bad-request", "tryb planowania musi być Pełny albo Lekki")
        if mode == "full" and (self.terminal is None or not self.terminal.available()):
            raise FlowError("no-terminal", "nie znaleziono Konsoli")
        preferred = str(request.get("preferredProvider") or selected.provider)
        if preferred not in ("mixed", "local") and not catalog.known_provider(preferred):
            raise FlowError("unknown-provider", "nieznany preferowany dostawca")
        if (preferred == "local") != selected.local:
            raise FlowError("provider-mismatch", "planowanie lokalne wymaga lokalnego wykonawcy")
        check = str(request.get("check") or item.check or "auto")
        if check not in ("auto", "manual"):
            raise FlowError("bad-request", "sprawdzenie musi być auto albo manual")
        images = self._images(request.get("images"))
        source_evidence = True if _prepare is not None else self._issue_evidence(item)
        if mode == "full":
            self._require_safe_evidence_console(selected.tool, source_evidence)
        with lock_for(project.repo):
            base = self._start_base(item, project, request, self.journal.state(card_id))
        if project.youtrack:
            prefix = project.youtrack
        else:
            try:
                prefix = reservations.reserve(project.repo, dry_run=True, local_fallback=True)["prefix"]
            except reservations.ReservationError as exc:
                raise FlowError("no-ticket-number", str(exc)) from None
        if _prepare is not None:
            return {**request, "baseRef": base["baseRef"], "baseCommit": base["baseCommit"],
                    "cardBranch": base["cardBranch"], "executor": selected.name,
                    "preferredProvider": preferred, "check": check}
        self.journal.append(card_id, "card.started", entry="planning", mode=mode, repo=project.repo,
                            **({"orderId": _order} if _order else {}),
                            project=project.slug, column=item.column, moveId=item.move_id,
                            executor=selected.name, program=selected.tool, model=selected.model,
                            provider=selected.provider, preferredProvider=preferred, prefix=prefix,
                            check=check,
                            assignmentRules=assignment.rules(self.store.load(), project), images=images,
                            sourceEvidence=source_evidence.source if source_evidence else None,
                            effort=selected.effort,
                            routeUrl=selected.route.url if selected.route else "",
                            routeKind=selected.route.kind if selected.route else "",
                            routeTokenFile=selected.route.token_file if selected.route else "",
                            trackerProject=project.youtrack,
                            trackerUrl=yt.load_config()['url'] if project.youtrack else '',
                            projects=order, **base)
        self._project_base(card_id)
        try:
            self._planning_launch(card_id, self.journal.state(card_id), selected, catalog, images=images)
        except FlowDeferred:
            # Brak wolnego miejsca u dostawcy: karta już ruszyła, planowanie wystartuje z kolejnym tickiem
            # (_advance_planning). Wcześniej StartCard zgłaszał wtedy „błąd usługi” mimo zapisanego startu (05.11).
            pass
        st = self.journal.state(card_id)
        low = self._low_limit(selected.provider)
        return {"ok": True, "id": card_id, "step": st.last_step, "executor": selected.name,
                "interactive": mode == "full",
                "limitWarning": flow_limits.planning_warning(low) if low else "",
                "cardBranch": ticket_of(st)["cardBranch"], "cardWorktree": ticket_of(st)["cardWorktree"]}

    def _prepare_order_projects(self, item, value):
        from dataclasses import replace
        from ..board.store import Change, _card_values
        from ..board.frontmatter import Document
        from ..board.model import parse_project
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",")]
        board = self.store.load()
        change = Change()
        values = _card_values({"projects": value})
        wanted = self.store._extra_projects(board, values["projects"], item.project, change)
        for _message, writes in change.commits:
            for path, text in writes:
                slug = os.path.basename(path).removesuffix(".md")
                errors = []
                board.projects[slug] = parse_project(Document.parse(text), path, slug, errors)
                if errors:
                    raise FlowError("bad-request", "nie można przygotować dodatkowego projektu")
        return replace(item, projects=wanted), board

    def _save_order_projects(self, card_id: str, item, value):
        """Pole `projects` żądania (dodatkowe projekty zlecenia: slugi albo ścieżki repozytoriów) trafia do
        `projects` karty, gdy różni się od zapisanego; zapis i sprawdzenie (PROJECTS_MAX) robi Tablica."""
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",")]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise FlowError("bad-request", "projects to lista slugów projektów albo ścieżek repozytoriów")
        board = self.store.load()
        wanted = []
        for entry in (v.strip() for v in value):
            known = board.project_by_repo(entry) if entry.startswith("/") else board.projects.get(entry)
            slug = known.slug if known is not None else entry
            if slug and slug != item.project and slug not in wanted:
                wanted.append(slug)
        if wanted == list(item.projects):
            return item
        try:
            self.store.update_card(card_id, {"projects": value})
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        return self.store.load().cards[card_num(card_id)]

    def _order_projects(self, item, project, *, board=None) -> list[dict]:
        """Projekty zlecenia (K-33 #4): projekt karty i jej dodatkowe projekty, każdy z dostępnym repozytorium.
        Źródło numerów: klucz YouTracka projektu Tablicy albo lokalny prefiks repozytorium. Repozytorium, które
        numeruje w YouTracku, a projekt Tablicy nie ma klucza YouTracka, dostaje `configured: false` —
        sprawdzanie propozycji odrzuci jego tickety. Projekt bez repozytorium kończy zlecenie błędem `no-repo`."""
        board = self.store.load() if board is None else board
        out = []
        for slug in [project.slug, *item.projects]:
            found = board.projects.get(slug)
            name = found.name if found is not None else slug
            if found is None or not found.repo:
                raise FlowError("no-repo", f"projekt „{name}” nie ma repozytorium — zlecenie nie wystartowało")
            if found.repo_missing or not os.path.isdir(found.repo):
                raise FlowError("no-repo", f"repozytorium projektu „{name}” jest niedostępne ({found.repo}) — "
                                           "zlecenie nie wystartowało")
            info = {"slug": found.slug, "name": found.name, "repo": found.repo, "main": not out,
                    "tracker": "youtrack" if found.youtrack else "local", "trackerProject": found.youtrack,
                    "prefix": found.youtrack, "configured": True}
            if not found.youtrack:
                try:
                    info["prefix"] = reservations.reserve(found.repo, dry_run=True, local_fallback=True)["prefix"]
                except reservations.ReservationError as exc:
                    text = str(exc)
                    if reservations.REFUSE_YOUTRACK in text:
                        key = text.split(":", 1)[0].strip()
                        info.update(tracker="youtrack", trackerProject=key, prefix=key, configured=False)
                    elif not out:
                        raise FlowError("no-ticket-number", text) from None
                    # Dodatkowy projekt bez ustalonego prefiksu: numery nada dopiero zatwierdzenie grupy (#5).
            info["source"] = flow_plans.source_label(info) if info["prefix"] else "lokalny (prefiks nieustalony)"
            out.append(info)
        return out

    @staticmethod
    def _proposal_projects(order: list[dict]) -> dict:
        return {p["slug"]: {k: p.get(k) for k in ("name", "prefix", "tracker", "trackerProject", "configured")}
                for p in order}

    def _planning_launch(self, card, st, selected, catalog, *, errors=(), previous="", images=(), round_no=0):
        item, project = self._project(card)
        made = self._planning_branch(card, st, project)
        branch, worktree = made["branch"], made["worktree"]
        importing = st.started.get("entry") == "plan"
        kind = "plan-import" if importing else "plan-fix" if round_no else "planning"
        # Granica generacji jest trwała: restart zachowuje klucz, korekta dostaje nowy.
        boundary = max(st.started["seq"], st.base_change.get("seq", 0))
        step_id = f"{card}-plan-{boundary}-{round_no}"
        out = worktree + f".{kind}-{step_id}.step/out"
        shots = [os.path.join(os.path.dirname(out), "in", "images", f"{i + 1}-{os.path.basename(p)}")
                 for i, p in enumerate(images)]
        if importing:
            with open(os.path.join(self.store.root, self.journal.rel_dir(card), "plan", "source.md"), encoding="utf-8") as fh:
                source = fh.read(source_plans.MAX_BYTES)
            text = planning.import_prompt(card=item, project=project, catalog=catalog, prefix=st.started["prefix"],
                                          source=source, source_rows=st.imported["rows"], out_dir=out,
                                          errors=errors, previous=previous, images=shots,
                                          tracker="youtrack" if project.youtrack else "local",
                                          tracker_url=st.started.get("trackerUrl") or "",
                                          tracker_project=project.youtrack or "")
        else:
            prompt_fn = planning.full_prompt if st.started.get("mode") == "full" and not round_no else planning.prompt
            args = dict(card=item, project=project, catalog=catalog,
                        preferred=st.started["preferredProvider"], assignment_rules=st.started["assignmentRules"],
                        prefix=st.started["prefix"], out_dir=out, tool=selected.tool, images=shots,
                        tracker="youtrack" if project.youtrack else "local",
                        tracker_url=st.started.get("trackerUrl") or "",
                        tracker_project=project.youtrack or "",
                        projects=st.started.get("projects") or [])
            args["environment"] = flow_environment.section(worktree, project.slug, out)   # PT-190
            if prompt_fn is planning.prompt:
                args.update(errors=errors, previous=previous)
            text = prompt_fn(**args)
        if st.started.get("mode") == "full":
            low = self._low_limit(selected.provider)
            if low:
                text += "\n\nUwaga o limicie: " + flow_limits.planning_warning(low) + ". Możesz kontynuować rozmowę albo przekazać planowanie.\n"
        if st.started.get("mode") == "full" and kind == "planning":
            self._launch_full_planning(item, project, selected, made, step_id, list(images), shots, text)
            return
        if round_no:
            frozen = next((s.get("snapshotDir") for s in reversed(list(st.finished.values())) if s.get("snapshotDir")), "")
            if frozen:
                text += planartifacts.prepare(frozen, os.path.dirname(out), seed=True)
        self._launch(item, project, selected, "", made, made, step_id, list(images), kind=kind,
                     round_no=round_no, prompt_text=text)

    def _launch_full_planning(self, item, project, selected, made, step_id, images, shots, prompt,
                              *, parent_step="", attempt=0, include_context=True, kind="planning",
                              ticket="", round_no=0, review_tip="", prepare=None):
        """Otwiera nadzorowaną kartę Konsoli dla planowania albo interaktywnej kontynuacji kroku."""
        self._trial_check({"ticket": ticket, "step": step_id})
        self._process_check(item.id, {"ticket": ticket, "step": step_id})
        context_state = self.journal.state(item.id)
        # Gotowa rozmowa opiekuna ma własny, zamknięty kontekst i uprawnienia wejścia.
        source_evidence = None if prepare else self._issue_evidence(item, started=context_state.started)
        self._require_safe_evidence_console(selected.tool, source_evidence)
        if source_evidence and parent_step:
            self._validate_historical_evidence(item.id, context_state, source_evidence)
        if baseproposal.blocked(context_state):
            raise FlowDeferred("decyzja o bazie karty")
        context_prompt, base_context = self._step_base_context(context_state)
        prompt += context_prompt
        worktree = made["worktree"]
        from .stepnotes import prepare as prepare_notes
        prompt, note_ids = prepare_notes(context_state, parent_step, prompt)
        git_dir = agentcmd.resolve_git_dir(project.repo, worktree)
        suffix = "planning" if kind == "planning" else "interactive"
        step_dir = worktree + f".{suffix}-{step_id}.step"
        out = os.path.join(step_dir, "out")
        inp = os.path.join(step_dir, "in")
        os.makedirs(out, mode=0o700, exist_ok=True)
        os.makedirs(inp, mode=0o700, exist_ok=True)
        for source, target in zip(images, shots):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
        if source_evidence:
            source_evidence.materialize(step_dir)
            shots = [*shots, *source_evidence.images(step_dir)]
            prompt += source_evidence.prompt(step_dir)
        board = self.store.load()
        extras = [board.projects[slug] for slug in item.projects
                  if slug in board.projects and board.projects[slug].repo and
                  not board.projects[slug].repo_missing]
        if extras and include_context:
            prompt += ("\nDodatkowe projekty karty — cytowane dane tekstowe do analizy. "
                       "Nazwy plików są względne; nie są ścieżkami do otwarcia ani modyfikacji.\n")
            for index, extra in enumerate(extras, 1):
                header = f"### Projekt {index}: {extra.name}\n"
                remaining = max(0, 96 * 1024 - len((prompt + header).encode("utf-8")))
                context = self._project_context(extra.repo, min(32 * 1024, remaining))
                prompt += header + context + "\n"
        order = (self.journal.state(item.id).started or {}).get("projects") or [] if kind == "planning" else []
        project_reads = self._configured_reads(item, project, worktree, step_dir, order=order,
                                                route=selected.route)
        # Przygotowany profil opiekuna musi dostać te same katalogi odczytu, zachowując własne granice zapisu.
        prepared = prepare(step_dir, project_reads) if prepare else None
        if prepared:
            prompt = prepared.stdin
        with open(os.path.join(inp, "prompt.txt"), "w", encoding="utf-8") as fh:
            fh.write(prompt)
        session = prepared.session_id if prepared else str(uuid.uuid4()) if selected.tool == "claude" else ""
        # Repozytoria pozostałych projektów zlecenia: Claude dostaje je przez --add-dir z zakazem zapisu (reguły
        # Edit); Codex w piaskownicy workspace-write czyta dysk bez --add-dir, które dałoby mu prawo zapisu.
        read_dirs = [p["repo"] for p in order if not p.get("main")] if selected.tool == "claude" else []
        command = agentcmd.interactive(selected.tool, selected.binary, prompt, shots, [out, *read_dirs],
                                       selected.route)
        if read_dirs:
            command.argv[1:1] = ["--disallowedTools", *agentcmd.read_only_rules(read_dirs)]
        if prepared:
            command.argv = prepared.argv
        if not prepared and selected.tool == "codex":
            command.argv[1:1] = ["--sandbox", "workspace-write"]
        if not prepared and selected.route is None:
            options = ["--model", selected.model]
            if selected.tool == "codex" and selected.effort:
                options += ["-c", f'model_reasoning_effort="{selected.effort}"']
            command.argv[1:1] = options
        if not prepared and session:
            command.argv.insert(1, "--session-id")
            command.argv.insert(2, session)
        read_profile = bool(project_reads) or self._used_read_profile(item.id, worktree)
        if read_profile and not prepared:
            readonly_repos = [p["repo"] for p in order if not p.get("main")]
            command = self._restricted_console(selected.tool, selected.binary, prompt, worktree,
                project.repo, step_dir, step_id, project_reads + readonly_repos, session=session,
                route=selected.route, model=selected.model, effort=selected.effort, images=shots,
                project=project, kind=kind)
        closed = os.path.join(inp, "console-closed.json")
        process_file = os.path.join(inp, "console-process.json")
        claim_dir = os.path.join(inp, "console-claim")
        os.makedirs(claim_dir, mode=0o700, exist_ok=True)
        with open(os.path.join(claim_dir, launch.FLOW_TRANSFER_CLOSE), "w", encoding="utf-8"):
            pass  # spóźniona druga karta bez zadania zamknie się zamiast otworzyć powłokę
        console_unit = "tide-console-" + uuid.uuid4().hex
        payload = {"cwd": claim_dir, "workdir": worktree, "argv": command.argv,
                   "flowCard": item.id, "flowTicket": ticket, "flowStep": step_id, "flowKind": kind,
                   "title": f"{item.id} · " + ("rozmowa z opiekunem" if kind == "caretaker-chat" else ticket or "planowanie Pełne"), "closedFile": closed,
                   "processFile": process_file, "resultFile": os.path.join(out, handoff.RESULT_FILE),
                   "consoleUnit": console_unit,
                   "systemdRun": self.systemd_run or agentcmd.SYSTEMD_RUN,
                   "systemctl": self.which("systemctl") or "/usr/bin/systemctl"}
        payload.update(command.task_fields())
        if prepared:
            payload.update(workdir=prepared.workdir, env=prepared.env, cleanEnv=True,
                           secretEnv=prepared.secret_env, tokenFiles=prepared.token_files)
        task_file = self.task_files.write(payload)
        interactive = ({"mode": "interactive", "closeFile": closed, "processFile": process_file,
                        "consoleUnit": console_unit, "taskFile": task_file, "enteredAt": self.clock(),
                        "routeUrl": selected.route.url if selected.route else "",
                        "routeKind": selected.route.kind if selected.route else "",
                        "routeTokenFile": selected.route.token_file if selected.route else "",
                        "consoleClosed": False, "consoleOpened": False} if kind == "caretaker-chat" else {})
        self.journal.append(item.id, "step.started", step=step_id, kind=kind, ticket=ticket, round=round_no,
                            executor=selected.name, program=selected.tool, model=selected.model,
                            provider=selected.provider, local=selected.local, effort=selected.effort,
                            unit="", session=session, worktree=worktree, branch=made["branch"],
                            stepDir=step_dir, repo=project.repo,
                            gitDir=git_dir,
                            readDirsProfile=read_profile,
                            parentStep=parent_step, attempt=attempt, reviewTip=review_tip,
                            deliveredNoteIds=note_ids, **base_context, **interactive)
        self.journal.append(item.id, "step.entered", step=step_id, session=session, source="",
                            deliveredNoteIds=note_ids,
                            closeFile=closed, processFile=process_file, consoleUnit=console_unit,
                            taskFile=task_file, enteredAt=self.clock())
        plan = Plan(repo_path=project.repo, repo_name=project.name, tool=selected.tool, prompt=prompt,
                    cwd=claim_dir, branch=made["branch"], title=payload["title"],
                    argv=command.argv, task_file=task_file)
        try:
            with self.journal.lock:
                self._process_check(item.id, {"step": step_id})
                opened = self.terminal.open(plan)
        except Exception as exc:
            print(f"tide-daemon: planowanie {item.id}: Konsola: {exc!r}", flush=True)
            opened = ""
        if not opened:
            if self.task_files.cancel(task_file):
                if kind == "caretaker-chat":
                    self.journal.append(item.id, "step.console_closed", step=step_id)
                else:
                    close_console(self.journal, item.id, step_id, cancelled=True)
                current = self.journal.state(item.id)
                self._finish(item.id, (current.steps.get(step_id) or current.caretakers[step_id]),
                             forced={"result": "stopped", "reason": "interactive-closed",
                                     "question": "Nie udało się otworzyć Konsoli. Wznowić czy porzucić?"})
            raise FlowError("no-terminal", "nie udało się otworzyć karty Konsoli")
        self.journal.append(item.id, "step.console_opened", step=step_id)

    @staticmethod
    def _project_context(repo: str, budget: int) -> str:
        """Małe pliki tekstowe jako dane w poleceniu; bez ścieżki do zapisywalnego repozytorium."""
        if budget <= 0:
            return "(pominięto: osiągnięto limit danych dodatkowych projektów)"
        root = os.path.realpath(repo)
        try:
            from ..gitcommand import git_command
            argv, env = git_command("-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
            listed = subprocess.run(argv, capture_output=True, timeout=30, env=env)
        except (OSError, subprocess.TimeoutExpired):
            raise FlowError("extra-project", f"nie można odczytać plików dodatkowego projektu {repo}") from None
        if listed.returncode != 0:
            raise FlowError("extra-project", f"nie można odczytać plików dodatkowego projektu {repo}")
        paths = [os.fsdecode(name) for name in listed.stdout.split(b"\0") if name]
        if len(paths) > 5000:
            raise FlowError("extra-project", f"dodatkowy projekt {repo} ma za dużo plików do kontekstu")
        chunks = []
        used = 0
        omitted = 0
        for rel in paths:
            try:
                rel.encode("utf-8")
            except UnicodeError:
                omitted += 1
                continue
            parts = rel.split("/")
            if not parts or any(part in ("", ".", "..", ".git") for part in parts):
                continue
            source = os.path.join(root, rel)
            if not os.path.realpath(source).startswith(root + os.sep) or os.path.islink(source):
                continue
            try:
                if not os.path.isfile(source) or os.path.getsize(source) > 32 * 1024:
                    omitted += 1
                    continue
                with open(source, "rb") as fh:
                    data = fh.read(32 * 1024 + 1)
            except OSError:
                omitted += 1
                continue
            if len(data) > 32 * 1024 or b"\0" in data:
                omitted += 1
                continue
            try:
                contents = data.decode("utf-8")
            except UnicodeError:
                omitted += 1
                continue
            chunk = json.dumps({"plik": rel, "treść": contents}, ensure_ascii=False)
            size = len(chunk.encode("utf-8"))
            if used + size > budget or len(chunks) >= 100:
                omitted += 1
                continue
            chunks.append(chunk)
            used += size
        context = "\n".join(chunks) + (f"\n(pominięto {omitted} pozostałych plików)" if omitted else "")
        return context or "(brak plików tekstowych)"

    def _advance_planning(self, card: str, st: FlowState) -> bool:
        """Osobny etap karty; nie zakłada, że ostatni krok jest jedynym krokiem ticketów."""
        if (st.started or {}).get("entry") not in ("planning", "plan") or st.plan_approved is not None:
            return False
        if st.paused or st.plan_proposed is not None:
            return True
        if st.started.get("entry") == "plan":
            st = self._recover_import(card, st)
        steps = [step for step in st.steps.values() if step.get("kind") in ("planning", "plan-fix", "plan-import")
                 and step.get("seq", 0) > st.started["seq"] and step["step"] not in st.superseded]
        if not steps:
            if st.imported is not None and not st.imported.get("needsCompletion", True):
                self.propose_plan(card, st.imported.get("snapshotPath") or st.imported["path"], import_source=True)
                return True
            selected = self._resume_executor(card, st, st.started, st.started["executor"])
            cfg = self.models_config() if self.models_config else None
            catalog = cfg.catalog if cfg is not None else self.catalog()
            self._planning_launch(card, st, selected, catalog, images=st.started.get("images") or [])
            return True
        step = steps[-1]
        done = st.finished.get(step["step"]) or {}
        result = done.get("result")
        decision = st.step_decisions.get(step["step"])
        if result in ("question", "denied", "error"):
            if decision:
                self._resume_step(card, st, step, decision)
            elif self._caretaker_resume(card, st, step):
                pass
            elif result == "denied":
                self._consent_recheck(card, step, done)
            elif result == "error" and int(step.get("attempt") or 0) == 0 and done.get("reason") != "login" \
                    and not self._caretaker_takes_over(card, st, step["step"]):    # PT-148: opiekun zamiast ślepej próby
                if step["step"] not in st.step_retries:
                    self.journal.append(card, "step.retry", step=step["step"], ticket="",
                                        reason=done.get("reason") or "")
                self._resume_step(card, self.journal.state(card), step, {"choice": "auto"}, attempt=1)
        elif result == "done":
            self._planning_result(card, st, step)
        return True

    def _planning_result(self, card, st, step):
        if st.plan_proposed is not None:
            return
        if self._caretaker_resume(card, st, step):
            return          # `retry` opiekuna po drugiej nieudanej poprawce formatu (PT-146), z jego instrukcją
        frozen = (st.finished.get(step["step"]) or {}).get("snapshotDir") or ""
        path = os.path.join(frozen or os.path.join(step["stepDir"], "out"), "plan.md")
        if (st.plan_checked or {}).get("step") != step["step"] or (st.plan_checked or {}).get("valid"):
            self.propose_plan(card, path, continuation=True, step=step["step"])
            st = self.journal.state(card)
        if st.plan_proposed is not None or int(step.get("round") or 0) >= 2:
            return
        selected = self._resume_executor(card, st, step, step["executor"])
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog()
        try:
            with open(path, encoding="utf-8") as fh:
                previous = fh.read(OUTPUT_MAX)
        except (OSError, UnicodeError):
            previous = "(brak czytelnego pliku planu)"
        self._planning_launch(card, st, selected, catalog, errors=st.plan_checked["errors"], previous=previous,
                              images=st.started.get("images") or [],
                              round_no=int(step.get("round") or 0) + 1)
    def _tracker_reserve(self, card, key, number, title, description):
        item, project = self._project(card)
        started = self.journal.state(card).started or {}
        payload = {'op': 'issue', 'url': started.get('trackerUrl') or yt.load_config()['url'],
                   'project': started.get('trackerProject') or project.youtrack,
                   'number': number, 'title': title, 'description': description}
        result = self.tracker.run(card, 'reserve', key, payload)
        if item.youtrack:
            self.tracker.run(card, 'youtrack-link', result['ticket'],
                             {'op': 'link', 'url': payload['url'], 'ticket': result['ticket'],
                              'parent': item.youtrack})
        return result

    def _tracker_notes(self, card):
        st = self.journal.state(card)
        if not st.started:
            return
        _, project = self._project(card)
        if not project.youtrack:
            return
        for (action, ticket), result in list(st.results.items()):
            if action != 'merge' or not result.get('ok') or ('youtrack-note', ticket) in st.results:
                continue
            source = next((s for s in reversed(list(st.steps.values())) if s.get('ticket') == ticket
                           and s.get('kind') in ('ticket', 'fix')), None)
            if source is None:
                continue
            verdict = next((v for (t, _), v in reversed(list(st.verdicts.items())) if t == ticket), {})
            text = (self._note(card, source) + '\n\nGałąź: ' + source.get('branch', '') +
                    '\nReview: ' + verdict.get('verdict', '') + '\nWykonawca: ' + source.get('executor', ''))
            try:
                self.tracker.run(card, 'youtrack-note', ticket,
                                 {'op': 'comment', 'url': st.started.get('trackerUrl') or yt.load_config()['url'], 'ticket': ticket,
                                  'event': str(result['seq']), 'text': text})
            except yt.YouTrackError:
                pass

    def _reserve(self, repo: str, title: str, request_key: str, prefix: str = "", minimum_number: int = 0) -> dict:
        got = reservations.reserve(repo, prefix=prefix, count=1, title=title, request_key=request_key, local_fallback=True,
                                   at_least=minimum_number)
        return {"ticket": got["ids"][0], "title": title}

    def enter(self, card_id: str, step_id: str) -> dict:
        """Stare wejście po ID albo warunkowe przejęcie próby wybranej z Snapshot."""
        if not step_id.lstrip().startswith("{"):
            return self._enter_step(card_id, step_id)
        try:
            card_id = f"K-{card_num(card_id)}"
            request = json.loads(step_id)
        except (ValueError, BoardError):
            raise FlowError("bad-request", "Niepoprawna karta lub żądanie EnterStep.") from None
        if (not isinstance(request, dict) or set(request) != {"ticket", "step", "revision"} or
                not all(isinstance(value, str) and value for value in request.values()) or
                not re.fullmatch(r"PT-[1-9][0-9]*", request["ticket"])):
            raise FlowError("bad-request", "Podaj ticket, krok i znacznik aktualności z Snapshot.")
        st = self.journal.state(card_id)
        projected = ticketentry.view(st)

        def refusal(code, message):
            return {"ok": False, "error": code, "message": message, "attempts": projected["attempts"]}

        if request["revision"] != projected["revision"]:
            return refusal("stale-context", "Stan karty zmienił się. Odczytaj aktualne możliwości i wybierz ponownie.")
        candidates = [item for item in projected["attempts"]
                      if item["ticket"] == request["ticket"] and item["available"]]
        if len(candidates) != 1 or candidates[0]["id"] != request["step"]:
            current = [item for item in projected["attempts"]
                       if item["ticket"] == request["ticket"] and item["currentAttempt"]]
            code = current[0]["error"] if not candidates and len(current) == 1 else "no-step"
            return refusal(code or "no-step", "Brak jednej jednoznacznej dostępnej próby tego ticketu.")
        # Monitor wykonuje wybór i istniejące przejęcie w jednym szeregowym zadaniu.
        try:
            return self._enter_step(card_id, request["step"])
        except FlowError as exc:
            projected = ticketentry.view(self.journal.state(card_id))
            return refusal(exc.code, str(exc))

    def _enter_step(self, card_id: str, step_id: str) -> dict:
        """Przejmuje krok w karcie Konsoli. Dziennik oznacza zajęte worktree przed uruchomieniem karty."""
        try:
            num = card_num(card_id)
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        card_id = f"K-{num}"
        st = self._process_state(card_id)
        if baseproposal.blocked(st):
            raise FlowError("base-decision", "najpierw rozstrzygnij pytanie o bazę karty")
        source_step = st.steps.get(step_id)
        self._trial_check(source_step or {"step": step_id})
        if step_id in st.superseded:
            raise FlowError("stale-context", "Krok należy do poprzedniego kontekstu bazy.")
        if source_step is None or st.card_finished:
            raise FlowError("no-step", f"{card_id}: nie ma kroku {step_id} do wznowienia")
        item, _ = self._project(card_id)
        source_evidence = self._issue_evidence(item, started=st.started)
        self._require_safe_evidence_console(source_step.get("program") or "claude", source_evidence)
        if source_evidence:
            # Natywne resume może czytać stare ścieżki z całej historii sesji.
            # Nowa kopia nie potwierdza integralności wcześniejszych wejść tej sesji.
            raise FlowError("interactive-evidence-unsafe",
                            "Nie można bezpiecznie przejąć sesji z dowodami sprawy w Konsoli. "
                            "Wznów krok w tle, aby otrzymał ponownie sprawdzone dowody.")
        if self.terminal is None or not self.terminal.available():
            raise FlowError("no-terminal", "nie znaleziono Konsoli")
        worktree = source_step.get("worktree") or ""
        reopen = not os.path.isdir(worktree)
        if reopen and not (st.results.get(("cleanup", source_step.get("ticket"))) or {}).get("ok"):
            raise FlowError("no-worktree", "katalog kroku już nie istnieje")
        for running in st.running():
            if running.get("worktree") == worktree and running.get("step") != step_id:
                raise FlowError("worktree-busy", "w katalogu kroku pracuje już inna sesja")
        if source_step.get("mode") == "interactive" and step_id not in st.finished:
            raise FlowError("already-entered", "karta Konsoli tego kroku jest już otwarta")
        if any(item.get("worktree") == worktree and item.get("mode") == "interactive" and
               not item.get("consoleClosed")
               for item in st.steps.values()):
            raise FlowError("console-open", "karta Konsoli tego worktree jest jeszcze otwarta")
        session, source = self._session(source_step)
        if not session:
            raise FlowError("no-session", "nie znaleziono ID sesji kroku; spróbuj ponownie za chwilę")
        if source_step.get("program") == "codex" and not source:
            raise FlowError("no-session-source", "nie znaleziono dziennika sesji Codexa; nie można dobrać programu do wznowienia")
        if reopen:
            repo = source_step.get("repo") or self._repo(card_id)
            branch = source_step.get("branch") or ""
            if not self.git.sha(repo, f"refs/heads/{branch}"):
                raise FlowError("no-branch", "gałąź ticketu już nie istnieje")
            self._act(card_id, "step-reopen", f"{step_id}-{st.last_seq + 1}",
                      lambda: self.git.branch_worktree(repo, branch, worktree, f"refs/heads/{branch}"),
                      repo=repo, branch=branch, worktree=worktree, base=f"refs/heads/{branch}")
        route = None
        if (source_step.get("provider") or "") not in ("anthropic", "openai", ""):
            cfg = self.models_config() if self.models_config else None
            catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
            if catalog is None:
                raise FlowError("executor-unavailable", "katalog wykonawców jest niedostępny")
            try:
                selected = execution.resolve(source_step.get("executor") or "", catalog, config=cfg,
                                             probe=self.model_probe, which=self.which)
            except execution.ExecutionError as exc:
                raise FlowError(exc.code, str(exc)) from None
            route = selected.route
        item, project = self._project(card_id)
        order = ((st.started or {}).get("projects") or []
                 if source_step.get("kind") in ("planning", "plan-fix") else [])
        # Błędne uprawnienie nie może najpierw zatrzymać zdrowego procesu ani skasować jego wyniku.
        self._configured_reads(item, project, worktree, source_step.get("stepDir") or worktree + ".step",
                               order=order, route=route)
        if step_id not in st.finished:
            if not self._stop_for_enter(card_id, source_step):
                raise FlowError("not-stopped", "trwający krok nie zatrzymał się; Konsola nie została otwarta")
            # Agent mógł właśnie zapisać wynik. Utrwalamy go, zanim powstanie następna próba.
            result_path = os.path.join(source_step.get("stepDir") or "", "out", handoff.RESULT_FILE)
            if os.path.isfile(result_path):
                self._finish(card_id, source_step, exit_code=0)
                st = self.journal.state(card_id)
        current = st.steps.get(step_id) or source_step
        new_id = step_id
        if step_id in st.finished:
            index = 1
            while f"{step_id}-enter-{index}" in st.steps:
                index += 1
            new_id = f"{step_id}-enter-{index}"
        step_dir = (source_step.get("stepDir") or worktree + ".step") if new_id == step_id \
            else (worktree + f".step-enter-{index}")
        out = os.path.join(step_dir, "out")
        os.makedirs(out, mode=0o700, exist_ok=True)
        closed = os.path.join(step_dir, "in", "console-closed.json")
        process_file = os.path.join(step_dir, "in", "console-process.json")
        os.makedirs(os.path.dirname(closed), mode=0o700, exist_ok=True)
        claim_dir = os.path.join(step_dir, "in", "console-claim")
        os.makedirs(claim_dir, mode=0o700, exist_ok=True)
        with open(os.path.join(claim_dir, launch.FLOW_TRANSFER_CLOSE), "w", encoding="utf-8"):
            pass
        for path in (closed, process_file, os.path.join(out, handoff.RESULT_FILE)):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        tool = current.get("program") or "claude"
        binary = self.terminal.launchers.agents.get(tool) or tool
        if tool == "codex":
            binary = self.terminal.launchers.codex_for(source)
        old_result = (st.finished.get(step_id) or {}).get("result") or ""
        kind = "interactive" if old_result == "done" else current.get("kind") or "ticket"
        console_unit = "tide-console-" + uuid.uuid4().hex
        prompt = handoff.resume_step(card_id, current.get("ticket") or "", out, kind=kind)
        from .stepnotes import prepare as prepare_notes
        prompt, note_ids = prepare_notes(st, step_id, prompt)
        command = agentcmd.resume_interactive(tool, binary, session, prompt, route)
        item, project = self._project(card_id)
        order = ((st.started or {}).get("projects") or []
                 if current.get("kind") in ("planning", "plan-fix") else [])
        project_reads = self._configured_reads(item, project, worktree, step_dir, order=order, route=route)
        read_profile = bool(project_reads) or self._used_read_profile(card_id, worktree)
        # Kolejne wejście po wyniku done ma rodzaj interactive, ale zachowuje profil pierwotnej pracy.
        tooling_kind = current.get("toolingKind") or current.get("kind") or "ticket"
        if read_profile:
            board = self.store.load()
            readonly_repos = ([p["repo"] for p in order if not p.get("main")] if order else
                              [board.projects[s].repo for s in item.projects if s in board.projects
                               and board.projects[s].repo and not board.projects[s].repo_missing])
            command = self._restricted_console(tool, binary, prompt, worktree,
                current.get("repo") or project.repo, step_dir, new_id, project_reads + readonly_repos,
                session=session, resume=True, route=route, model=current.get("model") or "",
                effort=current.get("effort") or "",
                context_dirs=[d for d in self._ancestor_outs(card_id, step_id) if d != out],
                project=project, kind=tooling_kind)
        payload = {"cwd": claim_dir, "workdir": worktree, "argv": command.argv,
                   "flowCard": card_id, "flowTicket": current.get("ticket") or "", "flowStep": new_id,
                   "flowKind": kind,
                   "title": f"{card_id} · {current.get('ticket') or 'krok'}", "closedFile": closed,
                   "processFile": process_file, "consoleUnit": console_unit,
                   "systemdRun": self.systemd_run or agentcmd.SYSTEMD_RUN,
                   "systemctl": self.which("systemctl") or "/usr/bin/systemctl"}
        payload.update(command.task_fields())
        task_file = self.task_files.write(payload)
        if new_id != step_id:
            self.journal.append(card_id, "step.started", step=new_id, kind=kind,
                                ticket=current.get("ticket") or "", executor=current.get("executor") or "",
                                program=tool, provider=current.get("provider") or "", local=bool(current.get("local")),
                                model=current.get("model") or "", unit="", session=session,
                                readDirsProfile=read_profile, toolingKind=tooling_kind,
                                worktree=worktree, branch=current.get("branch") or "", stepDir=step_dir,
                                gitDir=(agentcmd.resolve_git_dir(current.get("repo") or self._repo(card_id), worktree)
                                        if reopen else current.get("gitDir") or ""),
                                round=current.get("round") or 0, reviewTip=current.get("reviewTip") or "",
                                parentStep=step_id, deliveredNoteIds=note_ids,
                                baseVersion=current.get("baseVersion", 0),
                                baseRef=current.get("baseRef") or "", baseCommit=current.get("baseCommit") or "",
                                repo=current.get("repo") or self._repo(card_id))
        self.journal.append(card_id, "step.entered", step=new_id, session=session, source=source,
                            deliveredNoteIds=note_ids,
                            readDirsProfile=read_profile,
                            closeFile=closed, processFile=process_file, consoleUnit=console_unit,
                            taskFile=task_file,
                            enteredAt=self.clock())
        plan = Plan(repo_path=current.get("repo") or self._repo(card_id), repo_name="", tool=tool,
                    prompt=prompt, cwd=claim_dir, branch=current.get("branch") or "", title=payload["title"],
                    argv=command.argv, task_file=task_file)
        try:
            with self.journal.lock:
                self._process_check(card_id, {"step": new_id})
                mode = self.terminal.open(plan)
        except Exception as exc:
            print(f"tide-daemon: przepływ karty {card_id}: Konsola: {exc!r}", flush=True)
            mode = ""
        if not mode:
            if self.task_files.cancel(task_file):
                close_console(self.journal, card_id, new_id, cancelled=True)
                self._finish(card_id, self.journal.state(card_id).steps[new_id],
                             forced={"result": "stopped", "reason": "interactive-closed",
                                     "question": "Nie udało się otworzyć Konsoli. Wznowić czy porzucić?"})
            raise FlowError("no-terminal", "nie udało się otworzyć karty Konsoli")
        self.journal.append(card_id, "step.console_opened", step=new_id)
        return {"ok": True, "id": card_id, "step": new_id, "session": session, "mode": mode}

    def abandon(self, card_id: str, step_id: str) -> dict:
        """Rozstrzygnięcie pytania po zamknięciu Konsoli bez wyniku."""
        st = self.journal.state(card_id)
        done = st.finished.get(step_id) or {}
        if done.get("reason") != "interactive-closed" or done.get("result") != "stopped":
            raise FlowError("not-waiting", "ten krok nie czeka na wybór „wznów / porzuć”")
        self.journal.append(card_id, "step.finished", step=step_id, ticket=done.get("ticket") or "",
                            result="stopped", reason="abandoned", question="", command="", notes=[], kept=[])
        return {"ok": True, "id": card_id, "step": step_id, "result": "stopped"}

    def _kill(self, step: dict) -> bool:
        """Zatrzymanie kroku przez Runner; uchwyt procesu jest tylko dla kroków uruchomionych przez tę usługę
        (po restarcie `proc` to None, a krok znamy po jednostce zapisanej w dzienniku)."""
        return self._runner_for(step).kill(step, proc=self.procs.get(step.get("step") or ""), grace=self.grace)

    def _runner_for(self, step: dict):
        """Runner trwającego kroku według jego wpisu (`runner: box` → BoxRunner pudełka z wpisu), nie według
        dzisiejszego stanu projektu — także po restarcie usługi."""
        if step.get("runner") != boxstep.RUNNER:
            return self.runner
        box = step.get("box") or ""
        runner = self._box_runners.get(box)
        if runner is None:
            slug, card = step.get("boxSlug") or "", step.get("card") or ""
            runner = boxstep.runner_of(self, boxstep.settings_of(self), slug, card,
                                       bool(card) and box == boxstep.bs.card_box_name(slug, card))
            self._box_runners[box] = runner
        return runner

    def _unit_state(self, step: dict) -> str:
        if step.get("runner") == boxstep.RUNNER:
            try:
                return self._runner_for(step).step_state(step)
            except agentcmd.StepError:
                return "unknown"
        return stepstop.unit_state(step.get("unit") or "")

    def _stop_for_enter(self, card: str, step: dict) -> bool:
        try:
            self._process_check(card, step)
        except FlowError:
            return False
        stopped = stepstop.stop_step(self.procs.get(step.get("step") or ""), unit=step.get("unit") or "",
                                     worktree=step.get("worktree") or "", step_dir=step.get("stepDir") or "",
                                     repo=step.get("repo") or self._repo(card), git_dir=step.get("gitDir") or "",
                                     branch=step.get("branch") or "", tool=step.get("program") or "claude",
                                     session_id=step.get("session") or "", claude_projects=self.claude_projects,
                                     codex_roots=self.codex_roots, grace=self.grace,
                                     kill=lambda: self._kill(step))
        if not stopped.stopped:
            return False
        self.procs.pop(step.get("step") or "", None)
        return True

    def _session(self, step: dict) -> tuple[str, str]:
        sid = step.get("session") or ""
        source = step.get("source") or ""
        if step.get("program") == "codex":
            sid = sid or execution.codex_session_id(os.path.join(step.get("stepDir") or "", "out", "step.log"))
            source = source or stepstop.find_codex_log(self.codex_roots, step.get("worktree") or "", sid)
            if source and not sid:
                sid = execution.codex_session_id(source)
        return sid, source

    def _other_plan_todos(self, repo: str, card_id: str) -> dict:
        """Tickety `todo` z innych planów repozytorium: ID → {title, path} (ticket z innego planu, K-33 D2).

        ID, który gdziekolwiek (inna gałąź, nowsza kopia planu) ma stan inny niż `todo`, się nie liczy.
        """
        reader = self.__dict__.setdefault("_repo_plans", RepoPlans())
        try:
            found, _ = reader.read([repo])
        except Exception:  # noqa: BLE001 — brak gita albo uszkodzony plan nie może zablokować propozycji
            return {}
        todo, other_state = {}, set()
        for item in found:
            if getattr(item, "card", "") == card_id:
                continue
            for row in getattr(item, "rows", ()):
                if row.state == "todo":
                    todo.setdefault(row.ticket, {"title": row.title, "path": item.path})
                else:
                    other_state.add(row.ticket)
        return {ident: info for ident, info in todo.items() if ident not in other_state}

    def propose_plan(self, card_id: str, path: str, *, continuation=False, step="", import_source=False, request=None) -> dict:
        """Przyjmuje propozycję z pliku; walidacja i migawka nie nadają numerów."""
        try:
            card_id = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        self._require_base_context(card_id)
        board = self.store.load()
        item = board.cards.get(card_num(card_id))
        project = board.projects.get(item.project) if item else None
        if item is None or project is None or not project.repo:
            raise FlowError("no-project", f"{card_id}: karta potrzebuje projektu z repozytorium")
        if item.column in ("done", "archived"):
            raise FlowError("card-finished", f"{card_id}: karta jest zakończona")
        busy = "" if continuation or import_source else self.journal.state(card_id).busy_reason()
        if busy:
            raise FlowError("flow-active", f"{card_id}: {busy}")
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        step_roots = [os.path.join(s.get("stepDir") or "", "out") for s in
                      self.journal.state(card_id).steps.values() if s.get("stepDir")]
        finished = self.journal.state(card_id).finished.get(step) or {}
        roots = ((project.repo, os.path.join(self.store.root, "docs"), *step_roots) if not continuation else
                 (finished.get("snapshotDir") or os.path.join(self.journal.state(card_id).steps[step]["stepDir"], "out"),))
        imported = self.journal.state(card_id).imported
        if import_source and (imported or {}).get("snapshotPath") == os.path.realpath(path):
            roots = (*roots, os.path.dirname(imported["snapshotPath"]))
        source_states = {row["id"]: row["state"] for row in (imported or {}).get("rows", [])}
        # Projekty zlecenia z card.started (Planowanie); inne wejścia i dawne dzienniki — tylko projekt karty.
        order = ((self.journal.state(card_id).started or {}).get("projects") or []
                 if continuation else [])
        order = order or [{"slug": project.slug, "name": project.name, "prefix": "", "configured": True,
                           "tracker": "youtrack" if project.youtrack else "local",
                           "trackerProject": project.youtrack}]
        proposal = flow_plans.read(path, card_id, catalog, allowed_roots=roots,
                                   tracker='youtrack' if project.youtrack else 'local',
                                   existing_ids=[*source_states,
                                                 *[r["id"] for r in ((self.journal.state(card_id).plan_approved or {}).get("rows") or [])]],
                                   imported=bool(imported),
                                   other_plans=self._other_plan_todos(project.repo, card_id),
                                   projects=self._proposal_projects(order))
        if imported:
            original = {row["id"]: row["title"] for row in imported.get("rows") or []}
            proposed = {row["workId"]: row["title"] for row in proposal.rows}
            for ident, title in original.items():
                if ident not in proposed:
                    proposal.errors.append(f"{ident}: przejęty ticket musi pozostać w planie")
                elif proposed[ident] != title:
                    proposal.errors.append(f"{ident}: tytuł przejętego ticketu musi pozostać bez zmian")
        if proposal.plan is not None and proposal.plan.tracker == 'youtrack':
            config_url = yt.load_config()['url'].rstrip('/')
            if proposal.plan.tracker_project != project.youtrack:
                proposal.errors.append('tracker-project planu różni się od projektu karty')
            if proposal.plan.tracker_url.rstrip('/') != config_url:
                proposal.errors.append('tracker-url planu różni się od serwera projektu karty')
        if proposal.plan is not None and not project.youtrack:
            try:
                established = reservations.reserve(project.repo, dry_run=True, local_fallback=True)["prefix"]
                if proposal.plan.prefix != established:
                    proposal.errors.append(f"prefiks planu {proposal.plan.prefix} różni się od prefiksu repozytorium {established}")
            except reservations.ReservationError as exc:
                proposal.errors.append(str(exc))
        if not continuation and not import_source:
            with lock_for(project.repo):
                base = self._start_base(item, project, request or {}, self.journal.state(card_id))
            self.journal.append(card_id, "card.started", entry="plan", repo=project.repo, project=project.slug,
                                check=item.check or "auto", column=item.column, moveId=item.move_id,
                                trackerProject=project.youtrack,
                                trackerUrl=yt.load_config()['url'] if project.youtrack else '', **base)
            self._project_base(card_id)
        checked = self.journal.state(card_id).plan_checked or {}
        if not continuation or checked.get("step") != step or checked.get("errors") != proposal.errors:
            self.journal.append(card_id, "plan.checked", valid=not proposal.errors, errors=proposal.errors, step=step)
        if proposal.errors:
            return {"ok": False, "id": card_id, "error": "invalid-plan", "errors": proposal.errors}
        artifact_rel = os.path.join("plan", "sources", str(uuid.uuid4()))
        planartifacts.save(self.journal, card_id, artifact_rel, proposal.artifacts)
        artifact_path = os.path.join(self.store.root, self.journal.rel_dir(card_id), artifact_rel)
        source = proposal.artifacts[os.path.basename(path)]
        self.journal.write_file(card_id, os.path.join("plan", "plan.md"), source)
        for ident, body in proposal.cards.items():
            self.journal.write_file(card_id, os.path.join("plan", "tickets", ident.lstrip("#") + ".md"),
                                    body.encode("utf-8"))
        rows = [{**row, "sourceState": source_states.get(row["workId"], ""),
                 "body": proposal.cards[row["workId"]], "cardPath": os.path.join(self.store.root, self.journal.rel_dir(card_id),
                                                   "plan", "tickets", row["workId"].lstrip("#") + ".md")}
                for row in proposal.rows]
        self.journal.append(card_id, "plan.proposed", title=proposal.plan.title,
                            prefix=project.youtrack or proposal.plan.prefix, coordinator=proposal.plan.coordinator,
                            session=proposal.plan.session, rows=rows,
                            planner=(self.journal.state(card_id).steps.get(step) or {}).get("executor") or
                                    (self.journal.state(card_id).started or {}).get("executor") or proposal.rows[0]["executor"],
                            box=proposal.box,   # PT-172: propozycja rozszerzenia profilu (out/box.yaml) do pakietu zgód
                            **({"projects": [{k: p.get(k) for k in ("slug", "name", "prefix", "tracker",
                                                                     "trackerProject", "source", "main")}
                                             for p in order]} if len(order) > 1 else {}),
                            sourceStep=step, sourceKind=(self.journal.state(card_id).steps.get(step) or {}).get("kind") or "",
                            specification=self._plan_spec(path) or (self.journal.state(card_id).plan_proposed or {}).get("specification") or item.description, planPath=os.path.join(artifact_path, os.path.basename(path)), artifactPath=artifact_path)
        return {"ok": True, "id": card_id, "rows": proposal.rows, "waves": proposal.waves}

    def approve_plan(self, card_id: str) -> dict:
        """Nadaje numery roboczym ticketom i zapisuje mapę w dzienniku dokładnie raz."""
        try:
            card_id = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        st = self.journal.state(card_id)
        self._require_base_context(card_id)
        if st.plan_revision and st.plan_revision.get("pending"):
            return self._approve_revision(card_id, st)
        if st.plan_proposed and not st.plan_reviewed and not st.plan_approved:
            raise FlowError("review-pending", "plan czeka na review")
        if st.plan_approved is not None:
            return {"ok": True, "id": card_id, "map": st.plan_approved.get("map") or {}, "already": True}
        proposal = st.plan_proposed or {}
        if not proposal or st.card_finished:
            raise FlowError("no-plan", f"{card_id}: nie ma planu do zatwierdzenia")
        main = next((p.get("slug") for p in proposal.get("projects") or [] if p.get("main")), "")
        others = sorted({row.get("project") for row in proposal.get("rows") or []} - {None, "", main})
        if others:
            # Plan z ticketami w kilku projektach zakłada karty rodzeństwa (K-33 #5); dziennik zostaje bez zmian.
            raise FlowError("group-approval", f"{GROUP_APPROVAL} (tickety w projektach: {', '.join(others)})")
        if not any(e['type'] == 'plan.approval_requested' and e.get('proposal') == proposal['seq']
                   for e in self.journal.read(card_id)):
            self.journal.append(card_id, 'plan.approval_requested', proposal=proposal['seq'])
        repo = (st.started or {}).get("repo") or ""
        prefix = proposal.get("prefix") or ""
        imported_floor = max((int(row["id"][len(prefix) + 1:]) for row in (st.imported or {}).get("rows", [])
                              if row["id"].startswith(prefix + "-") and row["id"][len(prefix) + 1:].isdigit()), default=0)
        _, project = self._project(card_id)
        mapping = {}
        for row in proposal.get("rows") or []:
            work_id = row["workId"]
            if work_id.startswith("#"):
                key = f"plan:{proposal['seq']}:{work_id}"
                if project.youtrack:
                    try:
                        old = st.results.get(('reserve', key)) or {}
                        source = row['cardPath']
                        if not os.path.isfile(source) and old.get('ticket'):
                            source = os.path.join(os.path.dirname(source), old['ticket'] + '.md')
                        with open(source, encoding='utf-8') as fh:
                            description = fh.read()
                        result = self._tracker_reserve(card_id, key, int(work_id[1:]), row['title'], description)
                    except yt.YouTrackError as exc:
                        if exc.permanent:
                            raise FlowError('youtrack', str(exc)) from None
                        return {"ok": True, "id": card_id, "queued": True, "map": mapping, "message": str(exc)}
                else:
                    result = self._act(card_id, "reserve", key,
                                       lambda row=row, key=key: self._reserve(repo, row["title"],
                                                                                f"{card_id}:{key}", prefix, imported_floor),
                                       retry_failed=True, repo=repo, title=row["title"], prefix=prefix,
                                       minimumNumber=imported_floor)
                mapping[work_id] = result["ticket"]
            else:
                mapping[work_id] = work_id
        rows = [{**row, "id": mapping[row["workId"]],
                 "blockers": [mapping[b] for b in row["blockers"]],
                 "cardPath": os.path.join(self.store.root, self.journal.rel_dir(card_id), "plan", "tickets",
                                          mapping[row["workId"]] + ".md")}
                for row in proposal["rows"]]
        for row in rows:
            old = row["workId"].lstrip("#")
            old_path = os.path.join(self.store.root, self.journal.rel_dir(card_id), "plan", "tickets", old + ".md")
            new_path = row["cardPath"]
            with open(old_path if os.path.isfile(old_path) else new_path, "rb") as fh:
                body = fh.read()
            self.journal.write_file(card_id, os.path.join("plan", "tickets", row["id"] + ".md"), body)
            if old != row["id"]:
                self.journal.remove_file(card_id, os.path.join("plan", "tickets", old + ".md"))
        waves = graph_waves(rows)
        plan_file = f"docs/agents/plans/{proposal['ts'][:10]}-{card_id.lower()}-flow.md"
        self.journal.append(card_id, "plan.approved", map=mapping, rows=rows, planFile=plan_file)
        for row in rows:
            if row.get("sourceState") in ("gotowe", "odrzucone"):
                self.journal.append(card_id, "ticket.imported", ticket=row["id"], state=row["sourceState"])
        # PT-172: plan bez zatwierdzonego środowiska to „czeka na środowisko”; czyści to dopiero ApproveCardEnvironment.
        # PT-290 (D1): tylko przy `box.enabled: true`; przy wyłączonym pudełku zgoda jest odroczona, host działa dalej.
        from . import boxwait
        boxwait.require_environment(self, card_id, source="plan-approval")
        return {"ok": True, "id": card_id, "map": mapping, "waves": waves}

    def _images(self, value) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value) or len(value) > 20:
            raise FlowError("bad-request", "images to lista ścieżek (najwyżej 20)")
        out = []
        for path in value:
            real = os.path.realpath(path)
            if not os.path.isfile(real) or not real.lower().endswith(IMAGE_SUFFIXES):
                raise FlowError("bad-image", f"{os.path.basename(path)} nie jest obrazem")
            out.append(real)
        return out

    def _issue_evidence(self, card, *, started=None):
        ref = started.get("sourceEvidence", evidence.source(card)) if started else evidence.source(card)
        try:
            return evidence.load(self.store, card.id, ref)
        except (BoardError, OSError, ValueError, UnicodeError) as exc:
            raise FlowError("broken-evidence", f"{card.id}: odmowa dostarczenia dowodów sprawy: {exc}") from None

    @staticmethod
    def _require_safe_evidence_console(tool, source_evidence):
        # Konsola Claude nie wymusza piaskownicy Bash; Edit i tryb 0444 nie chronią przed powłoką.
        # Dotyczy też przejęcia sesji w tle, której argv z --settings nie przechodzi do resume.
        if tool == "claude" and source_evidence:
            raise FlowError("interactive-evidence-unsafe",
                            "Interaktywny Claude nie zapewnia dowodów sprawy tylko do odczytu. "
                            "Wybierz Lekkie planowanie lub Codexa; krok Claude można wznowić w tle.")

    @staticmethod
    def _validate_historical_evidence(card_id, st, source_evidence):
        """Sprawdza wszystkie kopie zlecenia nadal wskazane przez kartki i historię sesji."""
        try:
            previous = {**st.steps, **st.caretakers}
            for old_step in previous.values():
                # Przygotowana rozmowa ma tylko in/context i nigdy nie otrzymuje dowodów.
                # Opiekun w tle nadal podlega kontroli, również po zamknięciu kroku.
                if old_step.get("kind") == "caretaker-chat":
                    continue
                if old_step.get("seq", 0) >= (st.started or {}).get("seq", 0):
                    source_evidence.validate(old_step.get("stepDir"))
        except (BoardError, OSError, ValueError, UnicodeError) as exc:
            raise FlowError("broken-evidence", f"{card_id}: odmowa wznowienia ze starymi dowodami: {exc}") from None

    def _wait_cause(self, reason: str, selected) -> tuple[str, str]:
        """(przyczyna, odnowienie) czekania do `step.waiting` (pełna karta, PT-128): `quota` (okno limitu, z godziną
        odnowienia), `local` (kolejka modeli lokalnych) albo `slots` (kroki dostawcy naraz)."""
        if reason.startswith("czeka na limit"):
            return "quota", (self._low_limit(selected.provider) or {}).get("reset") or ""
        if "modele lokalne" in reason:
            return "local", ""
        return "slots", ""

    def _wait_reason(self, selected, reserved: int = 0, queued: int = 0) -> str:
        """Powód czekania kroku (pusty — może startować). `reserved` — miejsca u tego dostawcy zarezerwowane dla
        gotowych kroków opiekuna tej samej karty (PT-150, `_caretaker_reserved`), liczone jak trwające kroki;
        `queued` — wcześniej czekające kroki innych kart, którym ustępuje krok opiekuna (`_earlier_waiters`):
        zajmują miejsca, ale licznik w powodzie nie przekracza limitu (miejsca są zajęte, nie przepełnione)."""
        quota = self._low_limit(selected.provider)
        if quota:
            return flow_limits.wait_reason(quota)
        running = [step for card in self.journal.cards()
                   for step in self.journal.state(card).running()]
        provider = selected.provider
        limit = 1 if selected.local else DEFAULT_PROVIDER_SLOTS
        if not selected.local:
            try:
                with open(self.settings, encoding="utf-8") as fh:
                    value = json.load(fh).get("providerSlots", {}).get(provider, DEFAULT_PROVIDER_SLOTS)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    limit = value
            except (OSError, ValueError, AttributeError):
                pass
        count = sum(bool(step.get("local") or step.get("provider") == "local") if selected.local
                    else step.get("provider") == provider for step in running) + reserved
        if count >= limit:
            return f"limit kroków dostawcy {provider}: {count}/{limit}"
        if count + queued >= limit:
            return f"limit kroków dostawcy {provider}: {limit}/{limit}"
        if selected.local and model_queue.shared().busy():
            return "czeka na modele lokalne — raport i opisy kart mają pierwszeństwo"
        return ""

    def _caretaker_reserved(self, card: str, st: FlowState, selected, kind: str) -> int:
        """Pierwszeństwo slotu (PT-150, US 37): gotowy krok opiekuna tej karty (`caretaker.started` bez startu,
        bez wyniku) u tego samego dostawcy rezerwuje miejsce przed każdym innym krokiem tej karty. Nie dotyczy
        innych kart ani samego kroku opiekuna, nie omija limitu slotów (rezerwacja liczy się jak trwający krok);
        wcześniej czekające kroki innych kart mają pierwszeństwo przed opiekunem (`_earlier_waiters`)."""
        if kind == caretaker.KIND or selected.local or not st.caretaker_runs:
            return 0
        pending = [name for name in st.caretaker_runs if name not in st.caretakers and
                   name not in st.caretaker_outcomes and name not in st.finished and
                   ("step-start", name) not in st.results]
        if not pending:
            return 0
        catalog = self._catalog_now()
        conf = self._caretaker_setup(st, catalog)
        if caretaker.unavailable(st, self._card_column(card), conf):
            return 0
        found = catalog.get(conf["executor"]) if catalog is not None and conf["executor"] else None
        return len(pending) if found is not None and found.provider == selected.provider else 0

    def _earlier_waiters(self, card: str, st: FlowState, selected, ticket: str, kind: str) -> int:
        """Krok opiekuna nie wyprzedza kroków innych kart (PT-150, US 37): liczba kroków innych kart, które czekają
        na miejsce u tego samego dostawcy (`cause: slots`) od chwili nie późniejszej niż ten krok opiekuna (`since`
        z `step.waiting`; krok jeszcze nieczekający przychodzi po wszystkich czekających). Liczą się jak trwające
        kroki, więc zwolnione miejsce bierze najpierw wcześniej czekająca karta. Pomijane są karty zakończone,
        wstrzymane, przeniesione do Gotowe/Archiwum/Backlogu i odrzucone tickety — ich wpis czekania nie zajmie
        już miejsca. Remis w obrębie sekundy (`ts`) rozstrzyga jeden porządek wspólny dla wszystkich kart:
        (`since`, identyfikator karty, ticket, rodzaj kroku) — dwaj opiekunowie z równym `since` nie czekają na
        siebie nawzajem (PT-150, runda 2). Krok zablokowany rezerwacją własnego opiekuna nie zajmuje miejsca
        w tej kolejce: inaczej ticket A → opiekun B → opiekun A tworzy cykl czekania (runda 3)."""
        if kind != caretaker.KIND or selected.local:
            return 0
        since = (st.waiting.get((ticket, kind)) or {}).get("since") or ""
        mine = (since, card, ticket or "", kind)
        count = 0
        for other in self.journal.cards():
            if other == card:
                continue
            ost = self.journal.state(other)
            if ost.card_finished or ost.paused or ost.moved_to in ("done", "archived", "backlog"):
                continue
            running = {(s.get("ticket"), s.get("kind")) for s in ost.running()}
            reserved = self._caretaker_reserved(other, ost, selected, "ticket")
            blocked_by_caretaker = reserved and bool(self._wait_reason(selected, reserved))
            count += sum(1 for key, wait in ost.waiting.items()
                         if wait.get("cause") == "slots" and wait.get("provider") == selected.provider and
                         key[0] not in ost.rejected and key not in running and
                         (key[1] == caretaker.KIND or not blocked_by_caretaker) and
                         (not since or ((wait.get("since") or ""), other, key[0] or "", key[1] or "") < mine))
        return count

    def _admit(self, card: str, selected, ticket: str, kind: str, step_id: str) -> None:
        st = self.journal.state(card)
        if baseproposal.blocked(st):
            raise FlowDeferred("decyzja o bazie karty")
        reserved = self._caretaker_reserved(card, st, selected, kind)
        queued = self._earlier_waiters(card, st, selected, ticket, kind)
        reason = "karta wstrzymana" if st.paused else (
            self._wait_reason(selected, reserved, queued) if reserved or queued else self._wait_reason(selected))
        if not reason and selected.local:
            claim = model_queue.shared().claim_step(self.store.root + ":" + step_id, endpoint=selected.route.url,
                                                   model=selected.model)
            if claim is None:
                reason = "czeka na modele lokalne — raport i opisy kart mają pierwszeństwo"
            else:
                self._local_claims[step_id] = claim
        if reason:
            cause, until = ("paused", "") if st.paused else self._wait_cause(reason, selected)
            previous = st.waiting.get((ticket, kind)) or {}
            if previous.get("reason") != reason:
                self.journal.append(card, "step.waiting", ticket=ticket, kind=kind,
                                    provider=selected.provider, reason=reason, cause=cause, until=until)
            raise FlowDeferred(reason)

    def _ancestor_outs(self, card_id: str, parent_step: str, limit: int = 6) -> list[str]:
        """Katalogi `out` kroku-rodzica i jego przodków (najbliższe pierwsze), tylko istniejące."""
        steps = self.journal.state(card_id).steps
        found, name = [], parent_step
        while name and name in steps and len(found) < limit:
            step = steps[name]
            out = os.path.join(step.get("stepDir") or "", "out")
            if step.get("stepDir") and os.path.isdir(out):
                found.append(out)
            name = step.get("parentStep") or ""
        return found

    @staticmethod
    def _step_base_context(st) -> tuple[str, dict]:
        """Ten sam kontekst generacji w poleceniu i metadanych kroku w tle oraz Konsoli."""
        prompt = baseproposal.context(base_of(st))
        prompt += f"\nWersja bazy karty: {st.base_version}\n"
        if st.base_change:
            prompt += ("\nNotka korekty bazy: uruchomiono nową sesję na nowej pustej generacji. "
                       "Poprzednie review, zgody i domknięcie są historyczne. Sprawdź bieżący kod "
                       "i wykonaj wymagane testy; wynik pracy przejdzie zwykłe review.\n")
        return prompt, {"baseVersion": st.base_version, "baseRef": st.base.get("baseRef", ""),
                        "baseCommit": st.base.get("baseCommit", "")}

    def _configured_reads(self, card, project, worktree, step_dir, *, order=(), route=None):
        """Wyłącznie projekty, których repozytoria ten krok już może czytać; świeży odczyt ustawień."""
        from ..tasks import readaccess
        board = self.store.load()
        primary = board.projects.get(project.slug, project)
        projects = [primary]
        if order:
            for entry in order:
                extra = board.projects.get(entry.get("slug"))
                if extra and not entry.get("main") and extra.repo and not extra.repo_missing and \
                        os.path.realpath(extra.repo) == os.path.realpath(entry.get("repo") or ""):
                    projects.append(extra)
        else:
            projects += [board.projects[s] for s in card.projects if s in board.projects
                         and board.projects[s].repo and not board.projects[s].repo_missing]
        git = os.path.join(project.repo, ".git")
        writable = [worktree, os.path.join(step_dir, "out"), git]
        try:
            return readaccess.project_dirs(projects, writable=writable,
                                           secrets=[route.token_file] if route else [])
        except ValueError as exc:
            raise FlowError("read-dirs", str(exc)) from None

    def _used_read_profile(self, card, worktree):
        return any(s.get("readDirsProfile") and s.get("worktree") == worktree
                   for s in self.journal.state(card).steps.values())

    def _project_tooling(self, project, worktree, tool, kind):
        """Składniki profilu i środowiska wspólne dla tła i Konsoli, bez instalowania zależności."""
        from ..tasks import dotnet, node
        code = kind in ("ticket", "fix", "review", "conflict", "close")
        is_dotnet = code and dotnet.is_dotnet(worktree)
        node_home = node.node_home(os.path.expanduser("~")) if code and node.is_node(worktree) else ""
        domains = ()
        if tool == "claude" and kind in ("ticket", "fix", "conflict"):
            extra = (self._settings_data().get("networkDomains") or {}).get(project.slug) or []
            domains = tuple(dict.fromkeys([*node.registry_domains(worktree, dotnet=is_dotnet),
                                           *[d for d in extra if isinstance(d, str)]]))
        return {"dotnet": is_dotnet, "node_home": node_home, "network_domains": domains,
                "chrome_bin": node.chrome_bin(os.path.expanduser("~")) if node_home else ""}

    def _restricted_console(self, tool, binary, prompt, worktree, repo, step_dir, step_id, read_dirs,
                            *, project, kind, session="", resume=False, route=None, model="", effort="",
                            images=(), context_dirs=()):
        try:
            return agentcmd.restricted_interactive(agentcmd.Step(
                tool, binary, prompt, worktree, repo, step_dir, step_id,
                session_id=session, resume=resume, local=route, model=model, effort=effort,
                tmp_root=self.tmp_root, read_dirs=tuple(dict.fromkeys(read_dirs)),
                read_profile=True,
                context_dirs=tuple(context_dirs),
                permission_mode="acceptEdits" if tool == "claude" else "",
                codex_mcp_skip=tuple(self._settings_data().get("codexMcpSkip") or ()),
                **self._project_tooling(project, worktree, tool, kind),
                **self._git_identity(repo)), images=images)
        except (agentcmd.StepError, OSError) as exc:
            raise FlowError("read-dirs", f"Nie można zastosować profilu tylko do odczytu: {exc}") from None

    def _launch(self, card, project, selected, ticket, made, made_ticket, step_id, images,
                *, kind="ticket", round_no=0, prompt_text="", review_tip="", parent_step="",
                attempt=0, resume_session="", make_prompt=None, evidence=None, executor_reason="") -> dict:
        """`make_prompt(step_dir) -> str` — kartka składana dopiero przy starcie (po przyjęciu przez `_admit`, w parze
        „zamiar → wynik”): krok opiekuna buduje wtedy `<krok>/in/context/` (PT-153).
        `evidence` — wpis kroku autora dla review: spis jego artefaktów dostaje tylko review w pudełku (PT-291)."""
        self._trial_check({"ticket": ticket, "step": step_id})
        self._process_check(card.id, {"ticket": ticket, "step": step_id})
        evidence_state = self.journal.state(card.id)
        source_evidence = self._issue_evidence(card, started=evidence_state.started)
        if source_evidence and parent_step:
            # Kartka i historia wznowionej sesji zachowują dawne ścieżki. Sprawdzamy wszystkie
            # wejścia zlecenia, nie tylko ostatniego rodzica ani ograniczoną listę ancestor_outs.
            self._validate_historical_evidence(card.id, evidence_state, source_evidence)
        self._admit(card.id, selected, ticket, kind, step_id)
        try:
            placement = self._box_place(card.id, project, selected, ticket, kind, parent_step, step_id)
        except BaseException:
            self._release_local(step_id)
            raise
        worktree = made_ticket["worktree"]
        step_dir = worktree + (f".resume-{step_id}.step" if parent_step else
                               ".step" if kind == "ticket" else
                               f".review-{round_no}.step" if kind == "review" else
                               f".{kind}-{step_id}.step" if kind in ("planning", "plan-fix", "plan-review", "plan-revise", "plan-amend", "conflict", "close", caretaker.KIND) else f".fix-{round_no}.step")
        out = os.path.join(step_dir, "out")
        shots = [os.path.join(step_dir, "in", "images", f"{i + 1}-{os.path.basename(p)}") for i, p in enumerate(images)]
        issue_shots = source_evidence.images(step_dir) if source_evidence else []
        prompt = "" if make_prompt else prompt_text or handoff.ticket_card(card_id=card.id, title=card.title, description=card.description,
                                                     project=project.name, ticket=ticket, branch=made_ticket["branch"],
                                                     card_branch=made["branch"], worktree=worktree, out_dir=out,
                                                     images=[*shots, *issue_shots], test_base=made_ticket.get("base") or "")
        if source_evidence:
            prompt += source_evidence.prompt(step_dir)
        # PT-291: dodatki pudełka zależą od zapisanego runnera tego kroku (placement), nie od profilu ani autora.
        step_runner = handoff.BOX_RUNNER if placement is not None else handoff.HOST_RUNNER
        if evidence is not None:
            prompt += handoff.review_evidence(step_runner, evidence)
        context_prompt, base_context = self._step_base_context(self.journal.state(card.id))
        prompt += context_prompt
        read_dirs, context_dirs = [], []
        order = ((self.journal.state(card.id).started or {}).get("projects") or []
                 if kind in ("planning", "plan-fix") else [])
        if order:
            # Projekty zlecenia (K-33 #4): lista z repozytoriami jest w poleceniu planowania; tu tylko odczyt.
            read_dirs = [p["repo"] for p in order if not p.get("main")]
        else:
            # Dodatkowe projekty karty są do odczytu w każdym kroku, nie tylko w planowaniu (04.11, K-43/BO-9: ticket
            # w BO potrzebował zestawu klatek z repo streamer i musiał prosić o `git archive` poza piaskownicą).
            board = self.store.load()
            extras = [board.projects[slug] for slug in card.projects if slug in board.projects]
            read_dirs = [p.repo for p in extras if p.repo and not p.repo_missing]
            if extras:
                prompt += "\nDodatkowe projekty karty (wyłącznie do odczytu):\n" + "\n".join(
                    f"- {p.name}: {p.repo}" for p in extras)
        if parent_step:
            # Wznowienie widzi katalogi wyniku poprzednich kroków tego ticketu: polecenie poza piaskownicą zapisuje
            # raport (np. TRX testów) tam, gdzie wskazał je poprzedni krok (K-40/LEGAL-78, 04.11: trzy wznowienia
            # z rzędu nie mogły przeczytać raportu z poprzedniego `out`).
            context_dirs = self._ancestor_outs(card.id, parent_step)
            if context_dirs:
                prompt += ("\nKatalogi wyniku poprzednich kroków tego ticketu (możesz je czytać; tam trafiły m.in. raporty "
                           "poleceń uruchomionych poza piaskownicą):\n" + "\n".join(f"- {d}" for d in context_dirs))
        # Zrzuty ekranu z karty leżą w `in/images` kroku (a w wznowieniu — kroku, od którego zaczął się ticket):
        # narzędzie Read poza katalogami sesji jest odrzucane, więc są katalogami do odczytu (04.11, K-49:
        # agent nie mógł przeczytać zrzutu z komentarzami review).
        image_dirs = [os.path.join(step_dir, "in", "images")] if shots or issue_shots else []
        image_dirs += [os.path.join(os.path.dirname(d), "in", "images") for d in context_dirs]
        image_dirs += [os.path.join(worktree + ".step", "in", "images")]
        # Pełne wyniki poleceń poza piaskownicą poprzednich kroków (<krok>/outside/outside.log).
        image_dirs += [os.path.join(os.path.dirname(d), "outside") for d in context_dirs]
        read_dirs = list(read_dirs) + [d for d in dict.fromkeys(image_dirs)
                                       if ((shots or issue_shots) and d == os.path.join(step_dir, "in", "images")) or os.path.isdir(d)]
        if source_evidence:
            read_dirs.append(os.path.join(step_dir, "in", "evidence"))
        from .. import resources
        standard = resources.resolve("plan-standard")
        if str(standard) not in prompt:
            prompt += resources.instructions()
        # Korzystamy z istniejących katalogów tylko do odczytu; bez nowej polityki izolacji.
        read_dirs += [str(standard.parent), str(resources.resolve("next-id").parent)]
        if kind != caretaker.KIND:      # opiekun: stała lista tylko do odczytu w kartce, bez listy projektu (PT-158)
            prompt += handoff.runner_policy(step_runner, project.allowed_commands, project.denied_commands,
                                            step_dir=step_dir,
                                            profile=placement.profile if placement is not None else None)
        from .stepnotes import prepare as prepare_notes
        prompt, note_ids = prepare_notes(self.journal.state(card.id), parent_step, prompt)
        session = resume_session or (str(uuid.uuid4()) if selected.tool == "claude" else "")
        ids, places = self._hidden    # ukryta, zanim program zapisze pierwszą linię dziennika sesji
        self._hidden = (ids | {session}, tuple(sorted(set(places) | {os.path.realpath(worktree)})))

        def launch() -> dict:
            nonlocal prompt
            if make_prompt:
                prompt = make_prompt(step_dir)
                if source_evidence:
                    prompt += source_evidence.prompt(step_dir)
            configured = self._configured_reads(card, project, worktree, step_dir, order=order,
                                                route=selected.route)
            read_profile = bool(configured) or self._used_read_profile(card.id, worktree)
            if configured:
                prompt += "\nKatalogi danych projektu (wyłącznie do odczytu):\n" + "\n".join(configured) + "\n"
            from ..board.commands import claude_tools
            from ..tasks import dotnet
            # Krok opiekuna (PT-158) nie dostaje listy poleceń projektu: jego stałą listę tylko do odczytu składa
            # `agentcmd` (`write_out_only`).
            allowed_tools, disallowed_tools = claude_tools(project.allowed_commands, project.denied_commands,
                                                           step_dir=step_dir) if kind != caretaker.KIND else ((), ())
            tooling = self._project_tooling(project, worktree, selected.tool, kind)
            if placement is not None:
                tooling.update(node_home="", network_domains=(), chrome_bin="")
            # W pudełku zależności instaluje agent przez proxy, bez instalacji na hoście.
            if tooling["dotnet"] and placement is None:
                ok, tail = dotnet.restore(worktree)
                prompt += dotnet.prompt_note(ok, tail)
            from ..tasks import node as node_mod
            if kind in ("ticket", "fix", "conflict") and tooling["node_home"]:
                ok, tail = node_mod.install(worktree, os.path.expanduser("~"))
                prompt += node_mod.prompt_note(ok, tail, network=bool(tooling["network_domains"]))
            def copy_images(_bg):
                if placement is not None:
                    boxstep.configure_background(self, placement, card.id, step_id, parent_step, _bg)
                for source, target in zip(images, shots):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copyfile(source, target)
                if source_evidence:
                    source_evidence.materialize(step_dir)

                if kind == caretaker.KIND:
                    # Strażnik (PT-158): stan bazowy tuż przed startem programu, po wszystkich przygotowaniach Tide.
                    # Przy problemie ticketu pod strażą jest też drzewo i HEAD worktree karty (review M2); katalog gita
                    # ustala usługa z repozytorium, jak dla worktree kroku.
                    watched = [(worktree, _bg.git_dir)]
                    card_tree = made.get("worktree") or ""
                    if card_tree and os.path.normpath(card_tree) != os.path.normpath(worktree):
                        watched.append((card_tree, agentcmd.resolve_git_dir(project.repo, card_tree)))
                    self._guard_baseline(card.id, step_id, project.repo, watched,
                                         (made_ticket["branch"], made["branch"]))

            runner = self.runner if placement is None else placement.runner
            try:
                if placement is not None:
                    boxstep.before_start(self, placement, card.id, step_id,
                                         selected.tool if selected.route is None else "")
                runner_step = agentcmd.Step(
                    tool=selected.tool, binary=selected.binary, prompt=prompt, worktree=worktree, repo=project.repo,
                    step_dir=step_dir, step_id=step_id, session_id=session, model=selected.model,
                    branch=made_ticket["branch"],
                    resume=bool(resume_session),
                    local=selected.route, effort=selected.effort,
                    permission_mode="acceptEdits" if selected.tool == "claude" else "",
                    allowed_tools=allowed_tools if selected.tool == "claude" else (),
                    disallowed_tools=disallowed_tools if selected.tool == "claude" else (),
                    enforce_command_policy=selected.tool == "claude" or kind == caretaker.KIND,
                    tmp_root=self.tmp_root, read_dirs=tuple(dict.fromkeys([*read_dirs, *configured])),
                    context_dirs=tuple(context_dirs), read_profile=read_profile,
                    codex_mcp_skip=tuple(self._settings_data().get("codexMcpSkip") or ()), **tooling,
                    write_out_only=kind == caretaker.KIND, **self._git_identity(project.repo))
                if placement is not None:
                    boxstep.prepare_runner(self, placement, runner_step)
                self._trial_check(ticket)
                with self.journal.lock:
                    self._process_check(card.id, {"ticket": ticket, "step": step_id})
                    bg, proc = runner.start(runner_step, before_start=copy_images)
            except BaseException:
                if placement is not None:
                    boxstep.start_failed(self, placement, step_id)
                raise
            self.procs[step_id] = proc
            if placement is None:
                return {"unit": bg.unit, "gitDir": bg.git_dir, "readDirsProfile": read_profile}
            self._box_runners[placement.box] = placement.runner
            return {"unit": bg.unit, "gitDir": bg.git_dir, "readDirsProfile": read_profile, **placement.entry()}

        box_fields = placement.entry() if placement is not None else {}
        # K-88/PT-312: dlaczego ten wykonawca robi poprawki (override | escalated | author).
        reason_field = {"executorReason": executor_reason} if executor_reason else {}
        if kind == "review":
            # Nieśledzone pliki sprzed review: nowe po review to pliki recenzenta (wynik pomiaru, notatki) — Tide
            # przenosi je do katalogu kroku zamiast unieważniać review (04.11, K-43/BO-9).
            try:
                os.makedirs(os.path.join(step_dir, "in"), exist_ok=True)
                with open(os.path.join(step_dir, "in", UNTRACKED_BEFORE), "w", encoding="utf-8") as fh:
                    json.dump(self.git.untracked(project.repo, worktree), fh)
            except OSError:
                pass
        try:
            # Montowania dotyczą całego pudełka. Sprawdzamy je przed zamiarem startu,
            # a blokada chroni wynik aż do rezerwacji i uruchomienia jednostki.
            with boxstep.start_lock(placement):
                if placement is not None:
                    configured = self._configured_reads(card, project, worktree, step_dir, order=order,
                                                        route=selected.route)
                    boxstep.check_mounts(placement, agentcmd.Step(
                        tool=selected.tool, binary=selected.binary, prompt="", worktree=worktree, repo=project.repo,
                        step_dir=step_dir, step_id=step_id, branch=made_ticket["branch"],
                        read_dirs=tuple(dict.fromkeys([*read_dirs, *configured])), context_dirs=tuple(context_dirs)))
                begun = self._act(card.id, "step-start", step_id, launch, unit=f"tide-step-{step_id}", **box_fields,
                                  session=session, stepDir=step_dir, worktree=worktree, kind=kind,
                                  ticket=ticket, round=round_no, executor=selected.name,
                                  program=selected.tool, model=selected.model, provider=selected.provider,
                                  effort=selected.effort,
                                  routeUrl=selected.route.url if selected.route else "",
                                  routeKind=selected.route.kind if selected.route else "",
                                  routeTokenFile=selected.route.token_file if selected.route else "",
                                  local=selected.local, branch=made_ticket["branch"],
                                  reviewTip=review_tip, parentStep=parent_step, attempt=attempt,
                                  deliveredNoteIds=note_ids, **reason_field, **base_context)
        except boxstep.BoxWaiting as exc:
            boxstep.release_ports(placement.entry())
            self._release_local(step_id)
            self._box_defer(card.id, selected, ticket, kind, exc.reason)
        except FlowError:
            if step_id not in self.procs:
                if placement is not None:
                    boxstep.release_ports(placement.entry())
                self._release_local(step_id)
            raise
        except (agentcmd.StepError, OSError) as exc:
            # Błąd zapisu wyniku nie cofa uruchomienia jednostki. Otwarty zamiar pozwala
            # odzyskać krok; porty i miejsce lokalne należą do niego aż do potwierdzonego końca.
            if step_id in self.procs:
                raise FlowError("step", f"krok wystartował, ale nie utrwalono startu: {exc}") from None
            if placement is not None:
                boxstep.release_ports(placement.entry())
            self._release_local(step_id)
            raise FlowError("step", f"krok nie wystartował: {exc}") from None
        if placement is not None:
            # Dodatkowy wpis ryzyka nie może zmienić udanego startu w wynik ok=False.
            boxstep.started(self, placement, card.id, step_id, selected.tool)
        return self.journal.append(card.id, "step.started", step=step_id, kind=kind, ticket=ticket,
                                   round=round_no, executor=selected.name, program=selected.tool,
                                   model=selected.model, provider=selected.provider, local=selected.local,
                                   effort=selected.effort,
                                   routeUrl=selected.route.url if selected.route else "",
                                   routeKind=selected.route.kind if selected.route else "",
                                   routeTokenFile=selected.route.token_file if selected.route else "",
                                   reviewTip=review_tip, parentStep=parent_step, attempt=attempt,
                                   deliveredNoteIds=note_ids, **reason_field,
                                   unit=begun["unit"], session=session, worktree=worktree,
                                   branch=made_ticket["branch"], stepDir=step_dir, gitDir=begun["gitDir"],
                                   readDirsProfile=begun.get("readDirsProfile", False),
                                   repo=project.repo, **box_fields, **base_context)

    def _box_place(self, card: str, project, selected, ticket: str, kind: str, parent_step: str, step_id: str = ""):
        """PT-181: `None` (host), `Placement` (pudełko) albo FlowDeferred/FlowError — nigdy cichy powrót na hosta."""
        try:
            placement = boxstep.place(self, card, project.slug, kind, parent_step, BOX_STEP_KINDS)
            if placement is not None and step_id:
                boxstep.reserve_ports(self, placement, card, step_id)
            return placement
        except boxstep.BoxWaiting as exc:
            self._box_defer(card, selected, ticket, kind, exc.reason)
        except boxstep.BoxError as exc:
            raise FlowError("box", str(exc)) from None

    def _box_defer(self, card, selected, ticket, kind, reason):
        previous = fold(self.journal.read(card)).waiting.get((ticket, kind)) or {}
        if previous.get("reason") != reason:
            self.journal.append(card, "step.waiting", ticket=ticket, kind=kind, provider=selected.provider,
                                reason=reason, cause="box", until="")
        raise FlowDeferred(reason) from None

    def _act(self, card: str, action: str, key: str, work, *, retry_failed: bool = False, **intent) -> dict:
        """Para „zamiar → wynik”: wynik już jest — zwracamy go (powtórka nic nie robi); błąd — wynik ok: false i wyjątek."""
        st = self.journal.state(card)
        if baseproposal.blocked(st):
            raise FlowDeferred("decyzja o bazie karty")
        done = st.results.get((action, key))
        if done is not None:
            if not done.get("ok") and not retry_failed:
                raise FlowError(action, done.get("error") or action)
            if done.get("ok"):
                return done
        if (action, key) not in st.intents:
            self.journal.append(card, "intent", action=action, key=key, **intent)
        # Strażnik (PT-158, review M1 rundy 2): najpierw zamrożenie, potem kontrola — zapis kroku opiekuna tuż
        # przed skutecznym zamrożeniem trafia do `wrote`, zanim nowy stan bazowy po działaniu Tide go przykryje.
        frozen = self._guard_freeze(st, self._guard_watched(st))
        try:
            guarded = self._guard_check(card, st)
        except BaseException:
            self._guard_thaw(st, frozen)
            raise
        try:
            data = work()
        except (FlowGitError, reservations.ReservationError, agentcmd.StepError, OSError, FlowError) as exc:
            self._guard_rebase(card, guarded, action, frozen)
            if done is None or done.get("error") != str(exc):
                self.journal.append(card, "result", action=action, key=key, ok=False, error=str(exc))
            if isinstance(exc, (agentcmd.StepError, OSError)):
                raise
            raise FlowError(action, str(exc)) from None
        except BaseException:
            self._guard_thaw(st, frozen)
            raise
        self._guard_rebase(card, guarded, action, frozen)
        return self.journal.append(card, "result", action=action, key=key, ok=True, **(data or {}))

    # --- strażnik kroku opiekuna (PT-158) -------------------------------------------------------

    def _guard_baseline(self, card: str, step: str, repo: str, worktrees, branches) -> None:
        """Stan bazowy kroku opiekuna (`caretaker.guard`, `cause: start`): drzewo i HEAD worktree kroku i worktree
        karty (pary worktree, gitDir), gałąź kroku i gałąź karty."""
        base = guard.snapshot(self.git, repo=repo, worktrees=worktrees, branches=branches)
        self.journal.append(card, guard.EVENT, step=step, cause="start", wrote=[], **base)

    def _guard_freeze(self, st: FlowState, names: list[str]) -> dict:
        """Na czas działania Tide zamraża jednostki trwających kroków opiekuna (M1 review PT-158): zmiana w oknie
        działania jest wtedy zmianą Tide. Wynik: krok → "frozen" (zamrożony), "gone" (procesu już nie ma — nic nie
        pisze) albo "" (żyje albo nie wiadomo, a zamrożenie się nie udało — zmiany z okna należą do kroku)."""
        frozen = {}
        for name in names:
            watched = st.caretakers.get(name) or {}
            unit = watched.get("consoleUnit") or watched.get("unit") or ""
            if unit and self.freeze_unit(unit, True):
                frozen[name] = "frozen"
                continue
            proc = self.procs.get(name)
            if proc is not None and proc.poll() is not None or \
                    proc is None and unit and stepstop.unit_state(unit) == "inactive":
                frozen[name] = "gone"
            else:
                frozen[name] = ""
        return frozen

    def _guard_thaw(self, st: FlowState, frozen: dict) -> None:
        for name, how in frozen.items():
            watched = st.caretakers.get(name) or {}
            unit = watched.get("consoleUnit") or watched.get("unit") or ""
            if how == "frozen" and not self.freeze_unit(unit, False):
                logging.getLogger(__name__).warning("nie udało się odmrozić kroku opiekuna %s (%s)", name, unit)

    def _guard_watched(self, st: FlowState) -> list[str]:
        """Trwające kroki opiekuna pod strażą."""
        return [name for name in st.caretaker_guards if name not in st.finished]

    def _guard_check(self, card: str, st: FlowState) -> list[str]:
        """Przed działaniem Tide, które może zmienić worktree albo gałąź pod strażą (scalenie, commit planu, gałęzie,
        sprzątanie), już po zamrożeniu kroków (`_guard_freeze`): zmiany od stanu bazowego trwającego kroku opiekuna
        należą do kroku — zapisane w `wrote` przed działaniem, żeby zapis Tide ich nie przykrył. Zwraca kroki do
        wzięcia stanu na nowo po działaniu."""
        names = self._guard_watched(st)
        for name in names:
            base = st.caretaker_guards[name]
            found = guard.changes(base, guard.again(self.git, base))
            if found:
                self.journal.append(card, guard.EVENT, step=name, cause="check", wrote=found,
                                    **{k: base.get(k) for k in guard.FIELDS})
        return names

    def _guard_rebase(self, card: str, names: list[str], action: str, frozen: dict) -> None:
        """Po działaniu Tide, jeszcze przed odmrożeniem kroku, stan pod strażą bierze się na nowo. Zmiana w oknie
        działania jest zmianą Tide tylko wtedy, gdy krok był zamrożony albo już nie działał (`_guard_freeze`);
        inaczej zostaje zapisana jako zapis kroku (`wrote`) — strażnik woli odrzucić, niż przepuścić."""
        if not names:
            return
        try:
            st = self.journal.state(card)
            for name in names:
                base = st.caretaker_guards.get(name)
                if base is None or name in st.finished:
                    continue
                fresh = guard.again(self.git, base)
                if guard.same(base, fresh):
                    continue
                wrote = [] if frozen.get(name) else [
                    f"zmiana w czasie działania Tide `{action}`, gdy kroku opiekuna nie dało się zamrozić — "
                    + "; ".join(guard.changes(base, fresh))]
                self.journal.append(card, guard.EVENT, step=name, cause=action, wrote=wrote, **fresh)
        finally:
            self._guard_thaw(self.journal.state(card), frozen)

    def _guard_verdict(self, card: str, name: str) -> str:
        """Powód odrzucenia wyniku kroku opiekuna (pusty — strażnik przepuszcza). Bez stanu bazowego wynik też jest
        odrzucony: nie da się wykazać, że krok nie pisał."""
        st = self.journal.state(card)
        base = st.caretaker_guards.get(name)
        if base is None:
            return "brak stanu worktree sprzed kroku opiekuna — nie da się sprawdzić, czy krok nie pisał"
        found = [*st.caretaker_wrote.get(name, []), *guard.changes(base, guard.again(self.git, base))]
        return guard.reason(found)[:caretaker.REASON_MAX] if found else ""

    # --- ręczne kolumny --------------------------------------------------------------------------

    def set_paused(self, card_id: str, paused: bool) -> dict:
        try:
            card_id = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        st = self.journal.state(card_id)
        if st.started is None or st.card_finished or st.plan_finished:
            raise FlowError("no-flow", "karta nie ma trwającego przepływu")
        if st.paused != paused:
            self.journal.append(card_id, "card.paused" if paused else "card.resumed")
        if not paused and st.store_column == "backlog":
            # Wznowiona karta wraca z Backlogu do pracy (06.11, K-55: wznowiona po ustawianiu kolejności w Backlogu
            # pracowała dalej, a Tablica pokazywała ją w Backlogu, bo zapisana kolumna została „backlog”).
            with self.journal.lock:
                self.store.move_card(card_id, "todo", -1, from_column="backlog")
            moved = self.store.load().cards.get(card_num(card_id))
            self.moved(card_id, "todo", "backlog", getattr(moved, "move_id", "") or "")
        return {"ok": True, "id": card_id, "paused": paused}

    def moved(self, card_id: str, column: str, shown: str = "", move_id: str = "") -> None:
        """Karta przeniesiona ręcznie (MoveCard; `shown` — kolumna, w której ją widać): Backlog wstrzymuje, Gotowe
        i Archiwum kończą (krok zatrzymany z zapisem zmian, niescalony ticket odrzucony). Karta bez dziennika — nic."""
        if not self.journal.has(card_id):
            return
        st = self.journal.state(card_id)
        if st.started is None or st.card_finished:
            return
        if (move_id and st.store_move_id == move_id) or (not move_id and st.store_column == column):
            return
        self.journal.append(card_id, "card.moved", to=column, moveId=move_id,
                            **({"from": shown} if shown else {}))
        if column == "backlog" and not st.paused:
            self.journal.append(card_id, "card.paused")
        if column in ("done", "archived"):
            self._complete_manual_finish(card_id, column)

    def _complete_manual_finish(self, card: str, column: str) -> bool:
        """Gotowe/Archiwum zapisujemy dopiero po zatrzymaniu wszystkich kroków; tick ponawia po błędzie."""
        try:
            st = self._process_state(card)
        except FlowError:
            return False
        if baseproposal.generation_blocked(st):
            return False
        if st.card_finished:
            return True
        # Dawny ruch do Gotowe/Archiwum nie uprawnia do zatrzymania chronionej próby.
        # Sprawdzamy całą kartę przed pierwszym stopem, także okno sprzed step.started.
        if trialhold.preserves_project(st):
            return False
        for step in st.running():
            if not self._stop(card, step, "stopped", ""):
                return False
        st = self.journal.state(card)
        if st.running():
            return False
        tickets = dict.fromkeys([
            *(row.get("id") for row in (st.plan_approved or {}).get("rows") or []),
            *(step.get("ticket") for step in st.steps.values()),
            ticket_of(st)["id"],
        ])
        for ticket in tickets:
            merged = st.results.get(("merge", ticket)) or {}
            if ticket and not merged.get("ok") and ticket not in st.imported_done and ticket not in st.rejected:
                self.journal.append(card, "ticket.rejected", ticket=ticket)
        operation_id = (st.merged or {}).get("operationId")
        self.journal.append(card, "card.finished", column=column,
                            **({"operationId": operation_id} if operation_id else {}))
        return True

    # --- co chwilę -------------------------------------------------------------------------------

    def _release_local(self, step_id: str) -> None:
        claim = self._local_claims.pop(step_id, None)
        if claim is not None:
            model_queue.shared().release_step(claim)

    def _project_base(self, card: str) -> None:
        """Awaria projekcji nie cofa zlecenia; kolejny tick ponawia wyłącznie zapis pól."""
        st = self.journal.state(card)
        if st.started:
            try:
                with self.journal.lock:
                    self.store.project_card_base(card, base_of(st))
            except Exception as exc:
                print(f"tide-daemon: projekcja bazy {card}: {exc!r}", flush=True)

    def tick(self) -> None:
        try:
            # Recovery i journal.check zapisują stan. Żadna z tych operacji nie
            # może wyprzedzić rozpoznania niepełnej historii po restarcie.
            for card in self.journal.cards_complete():
                trialhold.read_process_history(self.journal, card)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._warnings["journal"] = f"niepełna historia kart — odzyskiwanie wstrzymane: {exc}"
            return
        self._warnings.pop("journal", None)
        from ..issue_start import recover as recover_issue_starts
        from ..issues import IssueStore
        recover_issue_starts(IssueStore(self.store), self)
        from .basegeneration import BaseGeneration
        from . import startorder
        unreadable_starts = set()
        for card in self.journal.cards():
            if any(e["type"].startswith("order.") for e in self.journal.read(card)):
                try:
                    startorder.events(self, card)
                except (OSError, ValueError):
                    unreadable_starts.add(card)
                    continue
            try:
                BaseGeneration(self).recover(card)
            except Exception as exc:
                print(f"tide-daemon: odzyskiwanie generacji {card}: {exc!r}", flush=True)
            self._project_base(card)
            current = self.journal.state(card)
            if not baseproposal.blocked(current):
                self._recover_started_steps(card, current)
        boxstep.recover_network(self)
        # Odtwarzamy zajęte tory przed sprawdzeniem końca kroków i startem nowych.
        running_local = {s["step"]: s for card in self.journal.cards()
                         for s in self.journal.state(card).running() if s.get("local")}
        for name in set(self._local_claims) - set(running_local):
            self._release_local(name)
        for name, step in running_local.items():
            if name not in self._local_claims:
                self._local_claims[name] = model_queue.shared().claim_step(
                    self.store.root + ":" + name, endpoint=step.get("routeUrl") or "", model=step.get("model") or "",
                    recovering=True)
        hidden_ids, hidden_places, busy = set(), set(), set()
        guarded: set[str] = set()
        for card in self.journal.cards():
            if card in unreadable_starts:
                continue
            warning = self.journal.check(card)
            if warning:
                self._warnings[card] = warning
                print(f"tide-daemon: przepływ karty: {warning}", flush=True)
            st = self.journal.state(card)
            for step in [*st.steps.values(), *st.caretakers.values()]:
                hidden_ids.add(str(step.get("session") or ""))
                if step.get("worktree"):
                    hidden_places.add(os.path.realpath(step["worktree"]))
            try:
                repo = (st.started or {}).get("repo") or ""
                if repo and repo not in guarded:
                    guarded.add(repo)
                    self._main_branches[repo] = self.git.main_branch(repo)
                    worktree_guard(repo, self._worktree_heads)
            except Exception as exc:
                print(f"tide-daemon: strażnik worktree: {exc!r}", flush=True)
            try:
                self._tick_card(card, st)
            except Exception as exc:  # jedna karta nie zatrzymuje pozostałych
                print(f"tide-daemon: przepływ karty {card}: {exc!r}", flush=True)
            try:
                self._guard_card(card)
                self._queue_notices(card)
            except Exception as exc:
                print(f"tide-daemon: przepływ karty {card}: strażnik/powiadomienie: {exc!r}", flush=True)
            st = self.journal.state(card)
            busy |= {s["worktree"] for s in st.running() if s.get("worktree")}
            busy |= {s["worktree"] for s in st.steps.values() if s.get("worktree") and
                     s.get("mode") == "interactive" and not s.get("consoleClosed")}
            if st.started is not None and not st.card_finished and ticket_of(st)["cardWorktree"]:
                busy.add(ticket_of(st)["cardWorktree"])
        self._hidden = (frozenset(hidden_ids - {""}), tuple(sorted(hidden_places)))
        self._busy = tuple(sorted(busy))
        boxstep.lifecycle.tick(self)

    def _tick_card(self, card: str, st: FlowState) -> None:
        self._outside_history(card)
        trialhold.inventory(self, card, st)
        if baseproposal.blocked(st):
            for step in st.running():
                self._poll(card, step)
            self._queue_notices(card)
            return
        self._recover_transfer_requests(card, st)
        st = self.journal.state(card)
        self._recover_console_openings(card, st)
        st = self.journal.state(card)
        self._sync_plan_cards(card, st)
        self._recover_started_steps(card, st)
        self._recover_verdicts(card)
        self._recover_review_rejections(card)
        st = self.journal.state(card)
        if st.started is not None and not st.card_finished and st.store_column:
            stored = self.store.load().cards.get(card_num(card))
            if stored is not None and (stored.column != st.store_column or
                                       (stored.move_id and stored.move_id != st.store_move_id)):
                self.moved(card, stored.column, stored.moved_from or st.store_column, stored.move_id)
                st = self.journal.state(card)
        # Ruch użytkownika wygrywa z recovery. Oczekiwanie scalenia nie zatrzymuje reszty karty.
        # Tylko zapisany zamiar jest zgodą na recovery; tick nigdy nie zaczyna nowego scalenia.
        self._recover_main_merge(card)
        st = self.journal.state(card)
        blocking_tracker_error = any(e.get('action') != 'youtrack-note' for e in st.tracker_failures())
        try:
            if not blocking_tracker_error and st.started and not st.paused and not st.card_finished and st.started.get('entry') == 'now' \
                    and not st.steps and st.started.get('request'):
                _, project = self._project(card)
                if project.youtrack or st.started.get("orderId"):
                    self.start(card, st.started['request'], _resume=True)
            if not blocking_tracker_error and st.plan_proposed and st.plan_reviewed and not st.plan_approved and any(
                    e['type'] == 'plan.approval_requested' and e.get('proposal') == st.plan_proposed['seq']
                    for e in self.journal.read(card)):
                self.approve_plan(card)
            self._tracker_notes(card)
        except yt.YouTrackError:
            pass
        st = self.journal.state(card)
        for intent in st.open_intents(include_background=True):
            if baseproposal.blocked(st):
                break
            self._reconcile(card, intent)
        st = self.journal.state(card)
        for item in st.steps.values():
            if item.get("step") in st.transfer_requests and item.get("step") not in st.step_decisions:
                continue
            if item.get("mode") == "interactive" and not item.get("consoleClosed") and self._console_ended(item):
                close_console(self.journal, card, item.get("step"))
        st = self.journal.state(card)
        if st.moved_to in ("done", "archived") and not st.card_finished:
            self._complete_manual_finish(card, st.moved_to)
            return
        for step in st.running():
            if step.get("step") in st.transfer_requests and step.get("step") not in st.step_decisions:
                continue
            self._poll(card, step)
        # Zbieramy wyniki procesów, ale nie oceniamy ani nie integrujemy ich w czasie decyzji.
        if baseproposal.blocked(self.journal.state(card)):
            return
        self._advance_user_revision(card, self.journal.state(card))
        current = self.journal.state(card)
        if (current.plan_revision or {}).get("pending"):
            self._advance_plan_review(card, current)
        self._queue_notices(card)
        for found in self._advance_card(card):
            self._flow_error(card, found)
        # Opiekun karty (PT-143) po istniejących automatach (ponowienie, mocniejszy wykonawca, odpowiedź
        # o środowisko, auto-odmowa) i przed powiadomieniami (`_queue_notices` w `tick`).
        self._caretake(card, self.journal.state(card))

    def _advance_card(self, card: str, *, own: tuple | None = None) -> list[dict]:
        """Przebieg etapów karty: ostatnie kroki ticketów, commit planu i kolejne tickety, domknięcie karty.
        Zwraca błędy Tide z tego przebiegu (`{error, stage, step, ticket}`), nie zapisuje ich — zapisuje
        `_flow_error` w zwykłym przebiegu, a ponowienie etapu opiekuna (`retry-stage`, PT-147) ocenia nimi swój
        wynik. `own` — para „zamiar → wynik” akcji opiekuna, w której ten przebieg się wykonuje: nie blokuje go
        jako zaczęta akcja karty. Wyjątki spoza błędów Tide (poza izolowanymi przy krokach) przechodzą dalej."""
        found: list[dict] = []

        def fresh() -> FlowState:
            current = self.journal.state(card)
            if own is not None:
                current.intents.pop(own, None)
            return current

        st = fresh()
        if not st.card_finished:
            try:
                latest = {}
                for name, step in st.steps.items():
                    if name in st.superseded and name not in st.plan_resumes:
                        continue
                    latest[step.get("ticket")] = name
                for name in latest.values():
                    current = fresh()
                    step = current.steps[name]
                    open_same = [s for s in current.open_consoles()
                                 if s.get("ticket") == step.get("ticket")]
                    planning_result_ready = ((current.started or {}).get("mode") == "full" and
                                             step.get("kind") in ("planning", "plan-fix", "plan-review", "plan-revise",
                                                                  "plan-amend") and
                                             all(s.get("kind") == "planning" and s["step"] in current.finished
                                                 for s in open_same))
                    if name not in current.finished or (open_same and not planning_result_ready):
                        continue
                    current.last_step = name
                    try:
                        self._advance(card, current, own=own)
                    except FlowDeferred:
                        pass
                    except Exception as exc:
                        # Izolacja (03.11, „błędy koordynacji to rak”): błąd przy jednym tickecie zatrzymuje tylko
                        # ten ticket — pozostałe tickety, start nowych i domknięcie karty idą dalej.
                        found.append(self._isolated_fault(card, current, name, exc))
                    else:
                        self._clear_flow_error(card, f"advance:{step['ticket']}" if step.get("ticket") else "advance",
                                               own=own)
                current = fresh()
                try:
                    if current.plan_approved is not None:
                        self._plan_next(card, current)
                        self._clear_flow_error(card, "plan-next", own=own)
                    elif not latest:
                        self._advance(card, current, own=own)
                        self._clear_flow_error(card, "advance", own=own)
                except FlowDeferred:
                    pass
                except Exception as exc:
                    found.append(self._isolated_fault(card, current, "", exc))
                if self._advance_close(card, fresh()):
                    self._clear_flow_error(card, "close", own=own)
            except FlowDeferred:
                pass
            except (FlowError, FlowGitError) as exc:
                found.append({"error": f"{exc.__class__.__name__}: {exc}", "stage": "close", "step": "",
                              "ticket": ""})
        return found

    def _isolated_fault(self, card: str, st: FlowState, step_name: str, exc: Exception) -> dict:
        """Błąd koordynacji jednego kroku (albo kolejnego ticketu planu): opis do `_flow_error` i ślad w dzienniku
        usługi. Etap (`stage`, część id problemu opiekuna `flow-error:<etap>`, PT-147): `advance:<ticket>` — dalszy
        etap ticketu po jego ostatnim kroku, `plan-next` — start kolejnego ticketu planu, `advance` — pierwszy krok."""
        import traceback
        step = st.steps.get(step_name) or {}
        ticket = step.get("ticket") or ""
        stage = (f"advance:{ticket}" if ticket else "advance") if step_name else \
            "plan-next" if st.plan_approved is not None else "advance"
        if isinstance(exc, FlowError) and exc.code == "plan-commit":
            stage, step_name, ticket = "plan-commit", "", ""
        if not isinstance(exc, (FlowError, FlowGitError)):
            print(f"tide-daemon: przepływ karty {card}, krok {step_name or '-'}: "
                  + "".join(traceback.format_exception_only(type(exc), exc)).strip(), flush=True)
        return {"error": f"{exc.__class__.__name__}: {exc}", "stage": stage, "step": step_name, "ticket": ticket}

    def _flow_error(self, card: str, found: dict) -> None:
        """Zapis błędu Tide: `flow.error` raz dla tego samego etapu i tekstu (z chwilą `at` z zegara silnika); ten sam
        tekst w kolejnym przebiegu co najmniej `FLOW_ERROR_PERSIST` s po pierwszym — jeden `flow.error-persisted`
        (wyzwalacz opiekuna, PT-147). Błąd, który w następnym przebiegu już nie wystąpił, zostaje jednorazowy."""
        st = self.journal.state(card)
        error = found["error"]
        stage = found.get("stage") or "advance"
        item = st.flow_errors.get(stage) or {}
        first = item.get("event") or {}
        if first.get("error") != error:
            self.journal.append(card, "flow.error", error=error, stage=stage,
                                step=found.get("step") or "", ticket=found.get("ticket") or "", at=self.clock())
            return
        if item.get("persisted"):
            return
        at = float(first.get("at") or _epoch(first.get("ts")) or 0.0)
        if self.clock() - at >= FLOW_ERROR_PERSIST:
            self.journal.append(card, "flow.error-persisted", error=error, stage=first.get("stage") or "",
                                step=first.get("step") or "", ticket=first.get("ticket") or "",
                                since=first.get("seq") or 0)

    def _clear_flow_error(self, card: str, stage: str, *, own: tuple | None = None) -> None:
        """Etap wykonał się bez błędu, także bez startu kroku. Rozwiązanie dotyczy tylko tego etapu."""
        item = self.journal.state(card).flow_errors.get(stage)
        if item:
            fields = {"caretakerStep": own[1], "caretaker": "retry-stage"} if own else {}
            self.journal.append(card, "flow.error-cleared", stage=stage, error=item["event"].get("error") or "",
                                **fields)

    @staticmethod
    def _wip_for(st: FlowState, step_id: str) -> dict:
        return (st.wip_retries.get(step_id) or {}).get("wip") or \
               (st.finished.get(step_id) or {}).get("wip") or {}

    @staticmethod
    def _wip_safe(wip: dict) -> bool:
        return bool(wip.get("stopped") and not wip.get("error") and
                    (wip.get("committed") or wip.get("files") == 0))

    def _repair_wip(self, card: str, st: FlowState, step: dict) -> dict:
        """Po błędzie piaskownicy ponów tylko commit; zatrzymanego programu nie wznawiaj przed zapisem."""
        self._trial_check(step)
        name = step.get("step") or ""
        previous = self._wip_for(st, name)
        if self._wip_safe(previous):
            return previous
        snap = stepstop.git_snapshot(step.get("repo") or self._repo(card), step.get("gitDir") or "",
                                     step.get("worktree") or "", step.get("stepDir") or "",
                                     step.get("branch") or "", stale_locks=True)
        done = st.finished.get(name) or {}
        stopped = stepstop.StopResult(stopped=True, committed=snap.committed, commit=snap.commit,
                                     files=snap.files, error=snap.error, notes=snap.notes,
                                     messages=done.get("messages") or [])
        wip = {"stopped": True, "committed": snap.committed, "commit": snap.commit,
               "files": snap.files, "error": snap.error, "notes": snap.notes,
               "handoff": self._handoff_note(card, step, stopped, done.get("reason") or "transfer")}
        self.journal.append(card, "step.wip", step=name, ticket=step.get("ticket") or "", wip=wip)
        return wip

    def _limit_recovered(self, provider: str, reset_ts: float, window: str = "") -> bool:
        if reset_ts and self.clock() >= reset_ts:
            return True
        if self.limits_source is None:
            return False
        try:
            snapshot = self.limits_source()
        except Exception:
            return False
        return flow_limits.recovered(snapshot, provider, self.get_flow_settings()["limitThresholdPercent"],
                                     self.clock(), window)

    def _repair_wip_due(self, card: str, st: FlowState, step: dict) -> dict:
        name = step.get("step") or ""
        previous = self._wip_for(st, name)
        retry = st.wip_retries.get(name) or {}
        if self._wip_safe(previous) or self.clock() - _epoch(retry.get("ts")) < 60:
            return previous
        return self._repair_wip(card, st, step)

    def _advance_limit(self, card: str, st: FlowState) -> bool:
        step = st.steps.get(st.last_step) or {}
        done = st.finished.get(st.last_step) or {}
        if done.get("reason") not in ("limit", "transfer"):
            return False
        choice = st.step_decisions.get(st.last_step) or {}
        if not choice and done.get("reason") == "limit":
            settings = self.get_flow_settings()["limitTransfer"]
            if settings["mode"] == "auto" and settings["executor"]:
                wip = self._repair_wip_due(card, st, step)
                if not self._wip_safe(wip):
                    return True
                try:
                    target = self._resume_executor(card, st, step, settings["executor"], changed=True, transfer=True)
                    reviewer = self._transfer_reviewer(st, step, target)
                except FlowError:
                    return True
                self._save_transfer_reviewer(card, step, reviewer)
                self.journal.append(card, "step.decision", step=st.last_step, ticket=step.get("ticket") or "",
                                    choice="transfer", executor=settings["executor"], automatic=True)
                return True
            # Limit nie jest decyzją (D3, PT-130): bez wyboru Tide czeka na odnowienie sam.
            choice = {"choice": "wait-limit"}
        if not choice:
            return True
        if choice.get("choice") == "wait-limit":
            limit = st.limits.get(st.last_step) or {}
            reset_ts = choice.get("resetTs") or limit.get("resetTs") or 0
            if not self._limit_recovered(step.get("provider") or "", reset_ts, limit.get("window") or ""):
                return True
            if not self._wip_safe(self._repair_wip_due(card, st, step)):
                return True
            self._resume_step(card, st, step, {"choice": "retry"})
            return True
        if choice.get("choice") == "transfer":
            if not self._wip_safe(self._repair_wip_due(card, st, step)):
                return True
            self._resume_step(card, st, step, choice)
        return True

    def _advance(self, card: str, st: FlowState, *, own: tuple | None = None) -> None:
        """Jedna zmiana etapu ticketu: ticket → review → poprawki/review → scalenie i sprzątanie.
        Start kolejnych ticketów i domknięcie wykonuje wyłącznie `_advance_card`, raz po obsłudze ticketów."""
        selected = st.last_step
        try:
            st = self._process_state(card)
        except FlowError:
            return
        # Ścisły odczyt zachowuje wszystkie ślady procesu, ale bieżąca akcja
        # opiekuna nadal nie blokuje własnego ponowienia etapu (jak w _advance_card).
        if own is not None and own[0] == "caretaker-action":
            st.intents.pop(own, None)
        # Fale wybierają kolejny ticket przez last_step, niezależnie od kolejności
        # startów w dzienniku. Zachowujemy wyłącznie selektor, nigdy stare dane.
        if selected:
            if selected not in st.steps:
                return
            st.last_step = selected
        if trialhold.held(st.steps.get(st.last_step) or {}) or baseproposal.blocked(st):
            return
        if st.open_intents():
            return
        if self._advance_limit(card, st):
            return
        if self._advance_plan_review(card, st):
            return
        if self._advance_planning(card, st):
            return
        if not st.last_step:
            if st.started and st.started.get("entry") == "now" and st.started.get("executor") and not st.paused:
                info = ticket_of(st)
                # Nieudana rezerwacja lub przygotowanie gałęzi nie uprawnia do
                # startu kroku bez tożsamości. Czekamy przed zapisem zamiaru.
                if not all(info[key] for key in ("id", "cardBranch", "cardWorktree", "branch", "worktree")):
                    return
                item, project = self._project(card)
                if info["id"] in st.rejected:
                    return
                cfg = self.models_config() if self.models_config else None
                catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
                selected = execution.resolve(st.started["executor"], catalog, config=cfg,
                                             probe=self.model_probe, which=self.which)
                self._launch(item, project, selected, info["id"],
                             {"branch": info["cardBranch"], "worktree": info["cardWorktree"]},
                             {"branch": info["branch"], "worktree": info["worktree"]},
                             self._next_step_id(card, info["id"], st), st.inputs.get("images", st.started.get("images") or []),
                             parent_step=next((name for name, old in reversed(list(st.steps.items()))
                                               if name in st.superseded and st.base_change and
                                               old.get("ticket") == info["id"] and
                                               old.get("kind") in ("ticket", "fix")), ""))
            return
        step = st.steps.get(st.last_step) or {}
        done = st.finished.get(st.last_step) or {}
        if done.get("outside"):
            try:
                self._outside_history(card, step)
            except FlowError:
                return
        result = done.get("result")
        if (step.get("ticket") or "") in st.rejected and result != "done":
            # Odrzucony ticket (także z decyzji o błędzie kroku) nie jest wznawiany ani ponawiany (K-16/LEGAL-75).
            return
        # Odtworzenie przerwy między step.finished a decyzją czekania. Ta sama walidacja co przy wyniku.
        if done.get("waitFor") and st.last_step not in st.step_decisions and \
                self._declared_wait(card, st.last_step, step, done):
            return
        if self._caretaker_resume(card, st, step):
            return
        if st.last_step in st.superseded and st.last_step not in st.step_decisions:
            blockers = st.plan_resumes.get(st.last_step)
            if blockers is not None and not st.plan_revision:
                ready = st.imported_done | st.rejected | {
                    ticket for (action, ticket), made in st.results.items() if action == "merge" and made.get("ok")}
                if blockers <= ready:
                    info = ticket_of(st, step.get("ticket") or "")
                    answer = ("Użytkownik rozstrzygnął poprawę planu po odesłaniu przez opiekuna. "
                              f"Dodatkowa praca jest zakończona: {', '.join(sorted(blockers)) or '(bez nowych blokerów)'}. "
                              f"Uwzględnij zmiany z gałęzi karty {info['cardBranch']} w bieżącym worktree, "
                              "zachowując dotychczasową pracę, i kontynuuj pierwotny zakres ticketu.")
                    self._resume_step(card, st, step, {"choice": "retry", "answer": answer, "caretaker": True})
            return              # Stary błąd nie wraca do decyzji; jawne ponowienie nadal działa.
        waiting_ticket = (st.step_decisions.get(st.last_step) or {}).get("waitTicket")
        if result in ("question", "error") and waiting_ticket:
            merged = (st.results.get(("merge", waiting_ticket)) or {}).get("ok")
            if merged or waiting_ticket in st.rejected or waiting_ticket in st.imported_done:
                self._resume_step(card, st, step, {"choice": "answer" if result == "question" else "retry",
                                                   "answer": self._wait_ticket_resume_text(st, step, waiting_ticket)})
            return
        waiting_card = (st.step_decisions.get(st.last_step) or {}).get("waitCard")
        if result in ("question", "error") and waiting_card:
            if self._card_done(waiting_card):
                text = (st.step_decisions[st.last_step].get("answer") or
                        f"Karta {waiting_card}, na którą czekał ten krok, jest domknięta — jej wynik jest na jej gałęzi; kontynuuj.")
                self._resume_step(card, st, step, {"choice": "answer" if result == "question" else "retry", "answer": text})
            return
        if result in ("question", "denied"):
            decision = st.step_decisions.get(st.last_step)
            if decision:
                self._resume_step(card, st, step, decision)
            elif result == "denied":
                self._consent_recheck(card, step, done)
            else:
                self._env_question(card, step, done)
            return
        if result == "stopped" and done.get("reason") == "interactive-closed":
            # Konsola zamknięta bez wyniku: „Wznów w tle” oddaje krok pracy w tle w tej samej sesji (03.11).
            decision = st.step_decisions.get(st.last_step)
            if decision and decision.get("choice") == "background":
                self._resume_step(card, st, step, decision)
            return
        if result == "error":
            decision = st.step_decisions.get(st.last_step)
            if decision:
                self._resume_step(card, st, step, decision)
            elif content_refusal(step) and self._other_provider_executor(st, step):
                # Filtr treści dostawcy (04.11, K-37/PT-158: Codex odrzucał review granic piaskownicy jako „ryzyko
                # cyberbezpieczeństwa”) — ten sam dostawca odmówi znowu, więc krok od razu idzie do innego.
                self.journal.append(card, "step.decision", step=st.last_step, ticket=step.get("ticket") or "",
                                    kind=step.get("kind") or "", choice="change-executor", answer="", command="",
                                    executor=self._other_provider_executor(st, step), auto=True, refusal=True)
            elif self._codex_broken_mcp(step, dict(done)):
                self.journal.append(card, "step.decision", step=st.last_step, ticket=step.get("ticket") or "",
                                    kind=step.get("kind") or "", choice="retry", answer="", command="",
                                    executor="", auto=True)
            elif int(step.get("attempt") or 0) == 0 and done.get("reason") != "login":
                if self._caretaker_takes_over(card, st, st.last_step):
                    return      # PT-148: opiekun zamiast kolejnej ślepej próby, także bez mocniejszego wykonawcy
                if st.last_step not in st.step_retries:
                    self.journal.append(card, "step.retry", step=st.last_step, ticket=step.get("ticket") or "",
                                        reason=done.get("reason") or "")
                    st = self.journal.state(card)
                self._resume_step(card, st, step, {"choice": "auto"}, attempt=1)
            else:
                stronger = self._stronger_executor(step, done)
                if stronger:
                    # Po nieudanym ponowieniu ten sam wykonawca rzadko pomaga — Tide sam przekazuje krok mocniejszemu
                    # modelowi tego dostawcy (03.11, K-31/DPT-5: „co mnie obchodzi, że Haiku czegoś nie umie”).
                    self.journal.append(card, "step.decision", step=st.last_step, ticket=step.get("ticket") or "",
                                        kind=step.get("kind") or "", choice="change-executor", answer="",
                                        command="", executor=stronger, auto=True, escalated=True)
            return
        if result != "done":
            return
        if step.get("kind") in ("planning", "plan-fix", "plan-review", "plan-revise", "plan-amend", "close"):
            return
        ticket = step.get("ticket") or ""
        if not ticket:
            return
        if (st.results.get(("cleanup", ticket)) or {}).get("ok"):
            return
        if ticket in st.rejected:
            return
        if step.get("kind") == "conflict":
            if (st.results.get(("merge", ticket)) or {}).get("ok"):
                self._cleanup(card, st, ticket, int(step.get("round") or 1))
            return
        if step.get("kind") in ("ticket", "fix"):
            round_no = 1 if step.get("kind") == "ticket" else int(step.get("round") or 0) + 1
            self._review(card, st, step, round_no)
            return
        if step.get("kind") != "review":
            return
        verdict = st.verdicts.get((ticket, step.get("round")))
        if verdict is None:
            return
        accepted = (st.review_decisions.get((ticket, step.get("round"))) or {}).get("choice") == "accept"
        if verdict.get("verdict") == "REQUEST_CHANGES" and not accepted:
            round_no = int(step.get("round") or 0)
            if round_no < self._review_rounds() or \
                    (st.review_decisions.get((ticket, round_no)) or {}).get("choice") == "retry":
                if (ticket, round_no) in st.review_questions and (ticket, round_no) not in st.review_decisions:
                    # Pytanie zadane przy dawnym progu (3 rundy) — Tide sam wybiera „Ponów poprawki”.
                    self.journal.append(card, "review.decision", ticket=ticket, round=round_no, choice="retry",
                                        auto=True)
                    st = self.journal.state(card)
                self._fix(card, st, step)
            elif (ticket, round_no) not in st.review_questions:
                self.journal.append(card, "review.question", ticket=ticket, round=round_no,
                                    question=review_question(ticket, round_no,
                                                             os.path.join(step.get("stepDir") or "", "out", "review.md")))
            return
        if any(s.get("kind") == "conflict" and s.get("ticket") not in st.rejected and
               not (st.results.get(("merge", s.get("ticket"))) or {}).get("ok")
               for s in st.steps.values()):
            return  # wszystkie scalenia używają jednego worktree karty
        previous_merge = st.results.get(("merge", ticket))
        # Scalenie, które padło nie przez konflikt (np. chwilowo uszkodzony worktree karty, 03.11 K-33/PT-115), jest
        # ponawiane; konflikt idzie swoją ścieżką niżej.
        retry_merge = bool(previous_merge) and not previous_merge.get("ok") and \
            "konflikt" not in (previous_merge.get("error") or "").lower() and \
            self.clock() - _epoch(previous_merge.get("ts")) >= 60
        if previous_merge is None or retry_merge:
            source = next((s for name, s in st.steps.items() if name not in st.superseded and
                           s.get("ticket") == ticket and
                           s.get("kind") == "ticket"), None)
            if source is not None:
                self._merge(card, st, source, retry=retry_merge)
        merged = self.journal.state(card).results.get(("merge", ticket))
        if merged and not merged.get("ok") and "konflikt" in (merged.get("error") or "").lower():
            current = self.journal.state(card)
            if not any(s.get("kind") == "conflict" and s.get("ticket") == ticket for s in current.steps.values()):
                self._start_conflict(card, current, step, merged)
            return
        if merged and merged.get("ok"):
            self._cleanup(card, self.journal.state(card), ticket, int(step.get("round") or 1))

    def _plan_next(self, card: str, st: FlowState) -> None:
        """Zapisuje aktualny plan i uruchamia gotowe tickety w granicach slotów, po scaleniu ich blokerów.
        Jedyne miejsce commita planu w przebiegu; zapisuje także końcowy stan po domknięciu."""
        if baseproposal.blocked(st):
            return
        approved = st.plan_approved or {}
        if not approved or st.card_finished or st.paused or st.open_intents():
            return
        rows = approved.get("rows") or []
        merged = set(st.imported_done) | {row["id"] for row in rows if (st.results.get(("merge", row["id"])) or {}).get("ok")}
        item, project = self._project(card)
        made = self._planning_branch(card, st, project)
        card_branch, card_worktree = made["branch"], made["worktree"]
        card_work = any(s.get("kind") in ("conflict", "close", "plan-review", "plan-revise", "plan-amend") and
                        (s["step"] not in st.finished or
                         (s.get("kind") == "conflict" and s.get("ticket") not in st.rejected and
                          not (st.results.get(("merge", s.get("ticket"))) or {}).get("ok")))
                        for s in st.steps.values())
        if not card_work:
            self._plan_commit(card, self.journal.state(card), project.repo, card_branch, card_worktree)
        if st.plan_finished or len((merged | st.rejected) & {row["id"] for row in rows}) == len(rows):
            return
        for row in rows:
            ticket = row["id"]
            if trialgate.blocked(ticket):
                continue
            if ticket in merged or ticket in st.rejected:
                continue
            if any(s.get("ticket") == ticket for s in st.steps.values()):
                continue  # inne tickety mogą pracować mimo pytania tego ticketu
            # Odrzucony bloker nie blokuje na zawsze: użytkownik wydzielił go z karty (K-16/LEGAL-75, 03.11).
            if not set(row["blockers"]) <= merged | set(st.rejected):
                continue
            cfg = self.models_config() if self.models_config else None
            catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
            if catalog is None:
                raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
            try:
                selected = execution.resolve(row["executor"], catalog, config=cfg,
                                             probe=self.model_probe, which=self.which)
                reviewer = execution.resolve(row["reviewer"], catalog, config=cfg,
                                             probe=self.model_probe, which=self.which)
            except execution.ExecutionError as exc:
                raise FlowError(exc.code, str(exc)) from None
            if not flow_plans.independent_reviewer(selected, reviewer) or selected.local != reviewer.local:
                raise FlowError("bad-reviewer", f"{ticket}: recenzent nie spełnia warunku innego modelu")
            slug = slug_from_prompt(row["title"], words=4, length=32)
            branch = f"ticket/{ticket.lower()}-{slug}"
            worktree = worktree_for(project.repo, branch)
            made_ticket = self._act(card, "ticket-branch", ticket, lambda: {
                **self.git.branch_worktree(project.repo, branch, worktree, f"refs/heads/{card_branch}"),
                "ticket": ticket}, repo=project.repo, branch=branch, worktree=worktree,
                base=f"refs/heads/{card_branch}", ticket=ticket)
            if ticket not in st.assignments:
                self.journal.append(card, "ticket.assigned", ticket=ticket, executor=selected.name,
                                    program=selected.tool, model=selected.model, provider=selected.provider,
                                    executorRouteUrl=selected.route.url if selected.route else "",
                                    executorRouteKind=selected.route.kind if selected.route else "",
                                    executorRouteTokenFile=selected.route.token_file if selected.route else "",
                                    executorEffort=selected.effort,
                                    reviewer=reviewer.name, reviewerProgram=reviewer.tool,
                                    reviewerModel=reviewer.model, reviewerProvider=reviewer.provider,
                                    reviewerRouteUrl=reviewer.route.url if reviewer.route else "",
                                    reviewerRouteKind=reviewer.route.kind if reviewer.route else "",
                                    reviewerRouteTokenFile=reviewer.route.token_file if reviewer.route else "",
                                    reviewerEffort=reviewer.effort,
                                    preferredProvider="mixed" if selected.provider != reviewer.provider else selected.provider)
            body = self._plan_card(card, row)
            out = worktree + ".step/out"
            prompt = handoff.ticket_card(card_id=card, title=row["title"], description=body,
                                         project=project.name, ticket=ticket, branch=branch,
                                         card_branch=card_branch, worktree=worktree, out_dir=out,
                                         test_base=made_ticket.get("base") or "")
            step_id = self._next_step_id(card, ticket, st)
            try:
                self._launch(item, project, selected, ticket, made, made_ticket, step_id, [], prompt_text=prompt)
            except FlowDeferred:
                continue
            st = self.journal.state(card)
        # Odrzucony ticket nie blokuje niezależnych gałęzi grafu.

    def _plan_commit(self, card: str, st: FlowState, repo: str, branch: str, worktree: str) -> None:
        approved = st.plan_approved or {}
        rows = approved.get("rows") or []
        merged = set(st.imported_done) | {row["id"] for row in rows if (st.results.get(("merge", row["id"])) or {}).get("ok")}
        key = f"{approved.get('seq')}:{st.plan_change_seq}"
        files = self._plan_files(card, st, merged, worktree=worktree)
        self._act(card, "plan-commit", key,
                  lambda: self.git.commit_plan(repo, branch, worktree, files),
                  retry_failed=True,
                  repo=repo, branch=branch, worktree=worktree)

    @staticmethod
    def _plan_extra(text: str) -> tuple[str, str]:
        """Tekst planu poza nagłówkiem i tabelą ticketów: (przed tabelą, po tabeli). Aktualizacja tabeli go zachowuje
        (05.11, K-50: odnośnik „Zakres poza v1” do szkicu followups znikał przy pierwszej zmianie stanu ticketu)."""
        lines = text.splitlines()
        if len(lines) < 2 or lines[0].strip() != "---":
            return "", ""
        try:
            end = lines.index("---", 1)
        except ValueError:
            return "", ""
        body = lines[end + 1:]
        start = next((i for i, line in enumerate(body) if line.startswith("| Ticket |")), None)
        if start is None:
            return "", ""
        stop = start
        while stop < len(body) and body[stop].startswith("|"):
            stop += 1
        return "\n".join(body[:start]).strip(), "\n".join(body[stop:]).strip()

    def _plan_files(self, card: str, st: FlowState, merged: set[str], *, worktree: str = "") -> dict[str, bytes]:
        approved, proposed = st.plan_approved or {}, st.plan_proposed or {}
        def cell(value):
            return " ".join(str(value or "").replace("|", "\\|").split())
        tracker = (st.started or {}).get('trackerProject') or ''
        lines = ["---", "tide-plan: 1", f"title: Plan karty {card}",
                 'tracker: youtrack' if tracker else 'tracker: local',
                 'tracker-url: ' + ((st.started or {}).get('trackerUrl') or ''), 'tracker-project: ' + tracker,
                 f"coordinator: {proposed.get('coordinator') or 'claude'}",
                 f"session: {cell(proposed.get('session'))}",
                 f"ticket-prefix: {proposed.get('prefix') or ''}", f"card: {card}",
                 "status: done" if len(merged | st.rejected) == len(approved.get("rows") or []) else "status: active",
                 f"updated: {st.plan_change_ts or approved.get('ts') or ''}", "---", ""]
        before, after = "", ""
        source = planartifacts.source_for_commit(st)
        artifacts = planartifacts.read(source["artifactPath"]) if source.get("artifactPath") else {}
        if source.get("planPath") and os.path.basename(source["planPath"]) in artifacts:
            before, after = self._plan_extra(artifacts[os.path.basename(source["planPath"])].decode("utf-8"))
        if worktree and approved.get("planFile") and (not artifacts or planartifacts.keep_committed_plan_text(st)):
            try:
                with open(os.path.join(worktree, approved["planFile"]), encoding="utf-8") as fh:
                    before, after = self._plan_extra(fh.read())
            except OSError:
                pass
        if before:
            lines += [before, ""]
        lines += ["| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |",
                 "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        files, ticket_paths = {}, {}
        for row in approved.get("rows") or []:
            ticket = row["id"]
            state = self._plan_row_state(st, ticket, merged)
            branch = f"ticket/{ticket.lower()}-{slug_from_prompt(row['title'], words=4, length=32)}"
            lines.append("| " + " | ".join(cell(value) for value in
                         (ticket, row["title"], state, branch, row["executor"], row.get("note") or "",
                          row.get("class") or "", row["reviewer"], ", ".join(row["blockers"]))) + " |")
            ticket_dir = os.path.dirname(approved["planFile"]).replace("/plans", "/tickets")
            ticket_path = f"{ticket_dir}/{card.lower()}-flow/{ticket.lower()}.md"
            ticket_paths[f"tickets/{(row.get('workId') or ticket).lstrip('#')}.md"] = ticket_path
            # Kartka w propozycji leżała w tickets/ obok followups/ („../followups/x.md”); w repozytorium szkice są
            # obok planu, więc odnośnik wskazuje je względem katalogu kartki (05.11, K-50: martwe linki w PT-201/208).
            followups = os.path.relpath(os.path.join(os.path.dirname(approved["planFile"]), "followups"),
                                        os.path.dirname(ticket_path))
            text = planartifacts.relocate_ticket_links(self._plan_card(card, row), followups)
            if worktree:
                # Dopisek pod kartką z pracy ticketu (np. odnośnik do szkicu) zostaje, gdy reszta kartki się zgadza.
                try:
                    with open(os.path.join(worktree, ticket_path), encoding="utf-8") as fh:
                        existing = fh.read()
                    if existing.rstrip() != text.rstrip() and existing.startswith(text.rstrip()):
                        text = existing
                except OSError:
                    pass
            files[ticket_path] = text.encode("utf-8")
        files.update(planartifacts.followup_files(artifacts, source, approved["planFile"], ticket_paths))
        if after:
            lines += ["", after]
        files[approved["planFile"]] = ("\n".join(lines) + "\n").encode("utf-8")
        return files

    @staticmethod
    def _plan_row_state(st: FlowState, ticket: str, merged: set[str]) -> str:
        if ticket in merged:
            return "gotowe"
        if ticket in st.rejected:
            return "odrzucone"
        if any(waiting_ticket == ticket for waiting_ticket, _ in st.waiting):
            return "czeka"
        if (st.results.get(("merge", ticket)) or {}).get("ok") is False:
            return "czeka"
        steps = [s for s in st.steps.values() if s.get("ticket") == ticket]
        if not steps:
            return "w-toku" if ticket in st.assignments else "todo"
        step = max(steps, key=lambda s: int(s.get("seq") or 0))
        finished = st.finished.get(step.get("step")) or {}
        if not finished:
            return "review" if step.get("kind") == "review" else "w-toku"
        if finished.get("result") != "done":
            return "czeka"
        if step.get("kind") in ("ticket", "fix"):
            return "review"
        verdict = st.verdicts.get((ticket, step.get("round"))) or {}
        choice = (st.review_decisions.get((ticket, step.get("round"))) or {}).get("choice")
        if verdict.get("verdict") == "REQUEST_CHANGES" and choice != "accept":
            if int(step.get("round") or 0) >= 3 and choice != "retry":
                return "czeka"
            return "w-toku"
        return "review"

    def _plan_card(self, card: str, row: dict) -> str:
        if "body" in row:
            return row["body"]
        path = os.path.join(self.store.root, row.get("cardPath") or "")
        root = os.path.realpath(os.path.join(self.store.root, self.journal.rel_dir(card), "plan", "tickets"))
        if not os.path.realpath(path).startswith(root + os.sep):
            raise FlowError("bad-plan-card", "kartka ticketu leży poza migawką planu")
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read(OUTPUT_MAX)
        except OSError as exc:
            raise FlowError("bad-plan-card", f"brak kartki {row.get('id')}: {exc}") from None

    def _ticket_text(self, card: str, st: FlowState, ticket: str, item) -> tuple[str, str]:
        if st.plan_approved is not None:
            row = next((r for r in st.plan_approved.get("rows") or [] if r.get("id") == ticket), None)
            if row is not None:
                return row["title"], self._plan_card(card, row) + "\n\nOpis karty:\n" + (item.description or "")
        return item.title, item.description

    def _next_step_id(self, card: str, ticket: str, st: FlowState) -> str:
        prefix = f"{card}-{ticket}-"
        used = set(st.steps) | {key for action, key in st.intents if action == "step-start"}
        numbers = [int(name[len(prefix):]) for name in used
                   if name.startswith(prefix) and name[len(prefix):].isdigit()]
        return f"{prefix}{max(numbers, default=0) + 1}"

    def _resolve_transfer(self, card: str, st: FlowState, request: dict) -> dict:
        st = self._process_state(card)
        choice = request.get("choice") or ""
        focus = str(request.get("step") or decision_step(st) or "")
        step = st.steps.get(focus) or {}
        self._trial_check(step)
        done = st.finished.get(focus) or {}
        if st.card_finished or not step:
            raise FlowError("no-transfer-step", f"{card}: nie ma kroku do przekazania")
        if choice == "wait-limit" and done.get("reason") != "limit":
            raise FlowError("no-limit", f"{card}: ten krok nie czeka na odnowienie limitu")
        if done and done.get("reason") not in ("limit", "transfer"):
            raise FlowError("no-transfer-step", f"{card}: ten krok już się zakończył")
        previous = st.step_decisions.get(focus) or {}
        executor = str(request.get("executor") or "").strip()
        intent = st.transfer_requests.get(focus) or {}
        if choice == "transfer" and intent and intent.get("executor") != executor:
            raise FlowError("already-decided", "przekazanie do innego wykonawcy już trwa")
        if previous.get("choice") == choice and (choice != "transfer" or previous.get("executor") == executor):
            return {"ok": True, "id": card, "step": focus, "choice": choice, "already": True}
        if previous and not (previous.get("choice") == "wait-limit" and choice == "transfer"):
            raise FlowError("already-decided", "decyzja o tym kroku została już zapisana")
        reviewer = None
        if choice == "transfer":
            if not executor:
                raise FlowError("bad-executor", "wybierz wykonawcę u innego dostawcy")
            target = self._resume_executor(card, st, step, executor, changed=True, transfer=True)
            reviewer = self._transfer_reviewer(st, step, target)
            if not intent:
                base_tip = self.git.sha(step.get("repo") or self._repo(card),
                                        f"refs/heads/{step.get('branch') or ''}")
                if not base_tip:
                    raise FlowError("no-base", "nie można ustalić commita przed przekazaniem")
                self.journal.append(card, "step.transfer_requested", step=focus,
                                    ticket=step.get("ticket") or "", executor=executor, baseTip=base_tip)
                st = self.journal.state(card)
        if not done:
            if choice != "transfer" or any(other.get("ticket") == step.get("ticket") and
                                            other.get("step") != focus for other in st.running()):
                raise FlowError("no-transfer-step", "krok nie może teraz zostać przekazany")
            stopped = (self._stop_interactive_transfer(card, step) if step.get("mode") == "interactive" else
                       self._stop(card, step, "stopped", "transfer", handoff=True))
            if not stopped:
                raise FlowError("stop-failed", "nie udało się zatrzymać kroku; przekazanie czeka")
            st = self.journal.state(card)
        wip = self._wip_for(st, focus)
        if not self._wip_safe(wip):
            wip = self._repair_wip(card, st, step)
        if not self._wip_safe(wip) and choice != "transfer":
            raise FlowError("wip-failed", "zmiany kroku nie zostały bezpiecznie zapisane; przekazanie czeka")
        limit = st.limits.get(focus) or {}
        self._save_transfer_reviewer(card, step, reviewer)
        event = self.journal.append(card, "step.decision", step=focus, ticket=step.get("ticket") or "",
                                    choice=choice, executor=executor if choice == "transfer" else "",
                                    resetTs=limit.get("resetTs") or 0)
        return {"ok": True, "id": card, "step": focus, "choice": choice, "seq": event["seq"],
                "queued": not self._wip_safe(wip)}

    def _stop_interactive_transfer(self, card: str, step: dict) -> bool:
        """Zamyka rzeczywistą sesję Konsoli albo anuluje nieprzejęte zadanie przed migawką WIP."""
        try:
            self._process_check(card, step)
        except FlowError:
            return False
        task = step.get("taskFile") or ""
        claim_dir = os.path.join(step.get("stepDir") or "", "in", "console-claim")
        try:
            with open(os.path.join(claim_dir, launch.FLOW_TRANSFER_CLOSE), "w", encoding="utf-8"):
                pass
        except OSError:
            return False
        cancelled = bool(task) and (self.task_files.cancel(task) or os.path.exists(task + ".cancelled"))
        if not cancelled:
            path = step.get("processFile") or ""
            unit = step.get("consoleUnit") or ""
            if not path or not unit or launch.process_alive(path) is None:
                return False
            launch.signal_flow_launcher(path, signal.SIGTERM)
            if not stepstop.terminate(None, unit, self.grace):
                return False
            deadline = time.monotonic() + max(1.0, self.grace)
            while not self._console_ended(step) and time.monotonic() < deadline:
                time.sleep(0.05)
            if not self._console_ended(step):
                launch.signal_flow_launcher(path, signal.SIGKILL)
                return False
        if not (cancelled or self._console_ended(step)):
            return False
        if not step.get("consoleClosed"):
            close_console(self.journal, card, step.get("step") or "", cancelled=cancelled)
        return self._stop(card, step, "stopped", "transfer", handoff=True)

    def _recover_transfer_requests(self, card: str, st: FlowState) -> None:
        """Po restarcie dokańcza stop, WIP i decyzję przed zwykłym odpytywaniem Konsoli."""
        for name, intent in st.transfer_requests.items():
            current = self.journal.state(card)
            if current.card_finished or name in current.step_decisions:
                continue
            try:
                self._resolve_transfer(card, current,
                                       {"choice": "transfer", "step": name,
                                        "executor": intent.get("executor") or ""})
            except FlowError:
                pass

    def _recover_console_openings(self, card: str, st: FlowState) -> None:
        """Otwiera oczekujące zadanie Konsoli po przerwaniu usługi między zapisem a terminal.open."""
        try:
            st = self._process_state(card)
        except FlowError:
            return
        if self.terminal is None or not self.terminal.available():
            return
        for step in [*st.steps.values(), *st.caretakers.values()]:
            if trialhold.held(step):
                continue
            name = step.get("step") or ""
            if step.get("mode") != "interactive" or step.get("consoleOpened") or step.get("consoleClosed") or \
                    name in st.finished or (name in st.transfer_requests and name not in st.step_decisions):
                continue
            task = step.get("taskFile") or ""
            if not task:
                continue
            if not os.path.isfile(task):
                if launch.process_alive(step.get("processFile") or "") is True:
                    self.journal.append(card, "step.console_opened", step=name, recovered=True)
                continue
            try:
                with open(task, encoding="utf-8") as fh:
                    payload = json.load(fh)
                plan = Plan(repo_path=step.get("repo") or self._repo(card), repo_name="",
                            tool=step.get("program") or "", prompt="", cwd=payload["cwd"],
                            branch=step.get("branch") or "", title=payload["title"],
                            argv=payload["argv"], task_file=task)
                with self.journal.lock:
                    self._process_check(card, step)
                    opened = self.terminal.open(plan)
            except Exception as exc:
                print(f"tide-daemon: przepływ karty {card}: wznowienie Konsoli: {exc!r}", flush=True)
                continue
            if opened:
                self.journal.append(card, "step.console_opened", step=name, recovered=True)

    def revoke_ticket_consent(self, card_id: str, request: dict) -> dict:
        return consent.revoke(self, card_id, request)

    def resolve_caretaker(self, card_id: str, request: dict) -> dict:
        """Decyzja użytkownika o problemie bez kroku: zamknięcie i ewentualnie jeden przebieg etapu."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if not isinstance(request, dict) or set(request) - {"choice", "problem", "description"}:
            raise FlowError("bad-request", "oczekiwano wyboru i ewentualnie id problemu")
        choice = request.get("choice")
        if choice not in ("retry-stage", "dismiss", "report"):
            raise FlowError("bad-choice", "wybierz retry-stage, dismiss albo report")
        if choice == "report":
            self._caretaker_description(request)
        st = self.journal.state(card)
        waiting = [d for d in decisions.build_decisions(st) if d["kind"] == "caretaker"]
        wanted = request.get("problem")
        decision = next((d for d in waiting if not wanted or d["technical"]["problem"] == wanted), None)
        if decision is None:
            raise FlowError("not-waiting", "karta nie czeka na decyzję opiekuna")
        ident = decision["technical"]["problem"]
        problem = next(p for p in caretaker.problems(st) if p["id"] == ident)
        reported = {}
        if choice == "report":
            reported = self._save_fault_report(card, st, problem.get("step") or "",
                                               request["description"].strip(), problem=problem)
        event = self.journal.append(card, "caretaker.decision", problem=ident, choice=choice)
        if choice == "retry-stage":
            self._retry_stage(card, {"problem": ident, "step": f"user:{event['seq']}",
                                     "trigger": problem["trigger"], "ticket": problem.get("ticket") or ""}, problem)
        return {"ok": True, "id": card, "choice": choice, "problem": ident, **reported}

    def resolve_step(self, card_id: str, request: dict, *, caretaker_problem: str = "",
                     caretaker_step: str = "") -> dict:
        """Odpowiedź na wynik kroku; zdarzenie pozwala wznowić go także po restarcie usługi.
        `caretaker_problem` — decyzję zapisuje akcja opiekuna (PT-150, tylko z silnika, nie z D-Bus): `step.decision`
        dostaje `auto: true` i `caretaker: <problem>`, więc nie zamyka problemu ani nie zeruje licznika działań;
        `caretaker_step` (`caretakerStep`) — krok opiekuna, po którym akcja po restarcie rozpoznaje własny zapis."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if not isinstance(request, dict) or set(request) - {"choice", "answer", "executor", "step", "allow", "instead", "auto",
                                                            "card", "ticket", "baseDecision", "baseRef", "baseCommit",
                                                            "previewId", "waitTicket"}:
            raise FlowError("bad-request", "oczekiwano wyboru i ewentualnie odpowiedzi lub wykonawcy")
        if str(request.get("step") or "").startswith("k38-diagnostic-"):
            return self._resolve_trial_diagnostic(card, request)
        if "allow" in request and request.get("choice") != "allow" or \
                "instead" in request and request.get("choice") != "deny":
            raise FlowError("bad-request", "`allow` idzie tylko z choice=allow, `instead` tylko z choice=deny")
        choice = str(request.get("choice") or "")
        st = self.journal.state(card)
        if choice == "change-base":
            from .basegeneration import BaseGeneration
            return BaseGeneration(self).resolve(card, request)
        self._require_base_context(card)
        if set(request) & {"baseRef", "baseCommit", "previewId"}:
            raise FlowError("bad-request", "parametry wykonania bazy wymagają choice=change-base")
        if st.base_request and request.get("step") == st.base_request["decision"]:
            if choice != "answer" or request.get("baseDecision") not in ("keep", "change"):
                raise FlowError("base-decision", "wybierz strukturalnie Zachowaj albo Zmień bazę")
            if request["baseDecision"] == "change":
                return {"ok": True, "id": card, "waiting": True, "changed": False,
                        "preview": self.preview_card_base_change(card, {})}
            self.journal.append(card, "card.base-kept", decision=st.base_request["decision"])
            return {"ok": True, "id": card, "changed": False, "baseDecision": "keep"}
        if choice in ("transfer", "wait-limit"):
            if baseproposal.pending(st):
                raise FlowError("base-decision", "najpierw rozstrzygnij pytanie o bazę karty")
            return self._resolve_transfer(card, st, request)
        if choice == "reassign-reviewer":
            return self._reassign_waiting_reviewer(card, st, request)
        if choice == "reassign-author":
            return self._reassign_fix_author(card, st, request)
        wanted = str(request.get("step") or "")
        # Ponowienie wskazanego kroku (także przez opiekuna) nie jest globalnym ponowieniem trackera.
        if choice == 'retry' and not wanted and not caretaker_problem and not st.card_finished and \
                not st.running() and st.tracker_failures():
            self.tracker.reset_failures(card)
            return {'ok': True, 'id': card, 'choice': choice, 'tracker': True}
        if choice == "retry" and wanted and not caretaker_problem and wanted in caretaker.plan_fix_waiting(st) \
                and not st.card_finished and not st.running():
            # Druga nieudana poprawa formatu planu (PT-146): ponowienie z instrukcją użytkownika, bez opiekuna.
            instruction = str(request.get("answer") or "").strip()
            if len(instruction) > 4000:
                raise FlowError("bad-answer", "odpowiedź musi mieć do 4000 znaków")
            event = self.journal.append(card, "step.retry", step=wanted, ticket="", reason="", user=True,
                                        instruction=instruction)
            return {"ok": True, "id": card, "step": wanted, "choice": choice, "seq": event["seq"]}
        focus = decision_step(st)
        if wanted in st.superseded and wanted not in st.plan_resumes:
            raise FlowError("stale-context", "Krok należy do poprzedniego kontekstu bazy; zgody utraciły ważność.")
        if wanted and wanted != focus:
            # Kilka decyzji naraz (K-15: pytanie LEGAL-60 i LEGAL-62): wskazany krok musi sam czekać na decyzję —
            # zakończony pytaniem, odmową albo błędem, bez kroku-następcy i bez wcześniejszej decyzji.
            done_wanted = st.finished.get(wanted) or {}
            if wanted not in st.steps or wanted in st.step_decisions or \
                    done_wanted.get("result") not in ("question", "denied", "error") or \
                    any(s.get("parentStep") == wanted for s in st.steps.values()):
                raise FlowError("not-waiting", f"krok {wanted} nie czeka na decyzję")
            focus = wanted
        step = st.steps.get(focus) or {}
        done = st.finished.get(focus) or {}
        self._trial_check(step)
        result = done.get("result")
        proposal = done.get("baseProposal") or {}
        base_decision = request.get("baseDecision")
        if proposal.get("valid") is True and result == "question":
            if st.card_finished:
                raise FlowError("no-step-question", "karta została zakończona")
            if choice != "answer" or base_decision not in ("keep", "change"):
                raise FlowError("base-decision", "wybierz strukturalnie Zachowaj albo Zmień bazę")
            if focus in st.step_decisions:
                raise FlowError("already-decided", "odpowiedź na ten krok jest już zapisana")
            if base_decision == "change":
                # Sam wybór otwiera odczyt; pytanie pozostaje nierozstrzygnięte.
                if focus not in st.base_waits:
                    self.journal.append(card, "step.base-wait", step=focus)
                return {"ok": True, "id": card, "step": focus, "waiting": True, "changed": False,
                        "preview": self.preview_card_base_change(card, {"step": focus})}
        elif base_decision is not None:
            raise FlowError("bad-request", "ten krok nie ma poprawnej propozycji bazy")
        valid = {"question": ("answer", "wait-card", "wait-ticket"),
                 "denied": ("run", "run-always", "run-ticket", "deny") if done.get("outside")
                 else ("box-allow", "deny") if done.get("network") else ("allow", "deny"), "error": ("retry", "change-executor", "reject", "wait-card", "wait-ticket"),
                 "stopped": ("background",) if done.get("reason") == "interactive-closed" else ()}.get(result, ())
        # Pracujący krok opiekuna nie blokuje decyzji użytkownika (PT-150): decyzja wygrywa, a spóźniona akcja
        # opiekuna jest nieaktualna (`caretaker.stale`) i niczego nie wykonuje.
        busy = any((s.get("worktree") == step.get("worktree") if step.get("kind") in
                    ("plan-amend", "plan-review", "plan-revise") else s.get("ticket") == step.get("ticket"))
                   for s in st.running() if s.get("kind") not in (caretaker.KIND, "caretaker-chat"))
        if st.card_finished or busy or not step or choice not in valid or \
                (result == "error" and int(step.get("attempt") or 0) == 0 and done.get("reason") != "login" and
                 not caretaker_problem and not caretaker.handed_repeat(st, focus)):
            raise FlowError("no-step-question", f"{card}: ten krok nie czeka na taką decyzję")
        previous = st.step_decisions.get(focus)
        if previous:
            if previous.get("choice") == choice and previous.get("answer", "") == str(request.get("answer") or "") and \
                    previous.get("executor", "") == str(request.get("executor") or "") and \
                    previous.get("allow") == request.get("allow") and \
                    previous.get("instead", "") == str(request.get("instead") or "").strip() and \
                    previous.get("caretaker", "") == caretaker_problem:
                return {"ok": True, "id": card, "step": focus, "choice": choice, "already": True}
            raise FlowError("already-decided", f"{card}: odpowiedź na ten krok jest już zapisana")
        answer = str(request.get("answer") or "").strip()
        if base_decision == "keep" and not answer:
            answer = "Zachowaj " + (base_of(st).get("baseRef") or "unknown")
        executor = str(request.get("executor") or "").strip()
        instead = str(request.get("instead") or "").strip()
        if choice == "answer" and (not answer or len(answer) > 4000):
            raise FlowError("bad-answer", "odpowiedź musi mieć od 1 do 4000 znaków")
        if choice in ("deny", "retry") and (len(answer) > 4000 or len(instead) > 4000):
            raise FlowError("bad-answer", "odpowiedź musi mieć do 4000 znaków")
        if choice == "change-executor":
            if not executor:
                raise FlowError("bad-executor", "wybierz wykonawcę")
            if len(answer) > 4000:
                raise FlowError("bad-answer", "instrukcja musi mieć do 4000 znaków")
            self._resume_executor(card, st, step, executor, changed=True, automated=bool(caretaker_problem))
        granted, skipped = [], []
        if choice == "reject":
            # „Odrzuć ticket” przy błędzie kroku (03.11): ticket wypada z karty, zależne tickety ruszają dalej.
            ticket = step.get("ticket") or ""
            if not ticket:
                raise FlowError("bad-choice", "odrzucić można tylko krok ticketu")
            event = self.journal.append(card, "step.decision", step=focus, ticket=ticket, choice="reject",
                                        answer=answer, command="", executor="")
            self.journal.append(card, "ticket.rejected", ticket=ticket)
            return {"ok": True, "id": card, "step": focus, "choice": choice, "seq": event["seq"]}
        if choice == "allow":
            granted, skipped = self._grant(card, step, done, request.get("allow"))
        if choice == "box-allow":
            # PT-172: domena z odmowy proxy trafia do profilu karty (tylko nie-czerwona), przebudowa jest zdarzeniem
            # dla silnika (#18), a krok wznawia się z tą informacją.
            from . import envconsent
            domain = envconsent.network_domain(done.get("command"))
            slug = self._project(card)[1].slug
            try:
                envconsent.add_card_domain(slug, card, domain)
            except envconsent.bp.ProfileError as exc:
                raise FlowError("red-domain", "; ".join(exc.errors)) from None
            self.journal.append(card, "box.rebuild-requested", step=focus, domain=domain, project=slug)
        if choice in ("run", "run-always", "run-ticket"):
            self._outside_history(card, step)
            if step.get("runner") == boxstep.RUNNER:
                raise FlowError("box-outside", "krok w pudełku nie może uruchamiać poleceń na hoście")
            from ..board.commands import outside_allowed, outside_pattern
            command = str(done.get("command") or "").strip()
            _, project = self._project(card)
            roots = (step.get("worktree") or "", project.repo, step.get("stepDir") or "")
            if not command or not outside_allowed(command, project.denied_commands, roots):
                raise FlowError("forbidden-command", f"polecenia „{command}” nie można uruchomić poza piaskownicą")
            if choice == "run-ticket":
                # Zgoda na polecenia poza piaskownicą do końca ticketu (03.11, K-33/PT-110: trzy kopie, trzy pytania).
                self.journal.append(card, "outside.ticket-consent", ticket=step.get("ticket") or "", step=focus,
                                    orderSeq=(st.started or {}).get("seq"), baseVersion=st.base_version)
            if choice == "run-always":
                from ..board.commands import outside_risky
                if outside_risky(command, roots, self._extra_repos(card)):
                    raise FlowError("risky-command", "ryzykownego polecenia nie można zapamiętać — uruchom je raz")
                self._remember_outside(project.slug, outside_pattern(command))
        extra = {}
        if base_decision == "keep":
            extra["baseDecision"] = "keep"
        if choice == "allow" and "allow" in request:
            extra = {"allow": request.get("allow"), "commands": granted, "skipped": skipped}
        if choice == "deny" and instead:
            extra = {"instead": instead}
        if request.get("auto") or caretaker_problem:
            extra["auto"] = True
        if caretaker_problem:
            extra["caretaker"] = caretaker_problem
        if caretaker_step:
            extra["caretakerStep"] = caretaker_step
        if choice in ("wait-card", "wait-ticket"):
            event = self._save_wait_decision(card, focus, step, choice,
                                             request.get("card" if choice == "wait-card" else "waitTicket"),
                                             answer=answer, **extra)
            return {"ok": True, "id": card, "step": focus, "choice": choice, "seq": event["seq"]}
        event = self.journal.append(card, "step.decision", step=focus, ticket=step.get("ticket") or "",
                                    choice=choice, answer=answer if choice in ("answer", "deny", "retry", "wait-card", "wait-ticket", "change-executor") else "",
                                    command=done.get("command") or "" if choice in ("allow", "deny", "run", "run-always", "run-ticket", "box-allow")
                                    else "", executor=executor if choice == "change-executor" else "", **extra)
        return {"ok": True, "id": card, "step": focus, "choice": choice, "seq": event["seq"]}

    # --- zgłoszenie błędu kroku (PT-125) ----------------------------------------------------------

    def preview_step_fault(self, card_id: str, request: dict) -> dict:
        """PreviewStepFault: treść karty z błędem, która powstanie albo zostanie dopisana — bez zapisu."""
        if isinstance(request, dict) and "problem" in request:
            self._caretaker_description(request)
            if set(request) - {"problem", "choice", "description"}:
                raise FlowError("bad-request", "oczekiwano problemu i opisu")
            try:
                card = f"K-{card_num(card_id)}"
            except BoardError as exc:
                raise FlowError(exc.code, str(exc)) from None
            st = self.journal.state(card)
            waiting = [d for d in decisions.build_decisions(st) if d["kind"] == "caretaker"]
            if not any(d["technical"]["problem"] == request["problem"] for d in waiting):
                raise FlowError("not-waiting", "karta nie czeka na decyzję opiekuna")
            problem = next(p for p in caretaker.problems(st) if p["id"] == request["problem"])
            return {"ok": True, "id": card, "preview": True,
                    **self._fault_plan(card, st, "", request["description"].strip(), problem=problem)}
        card, st, name = self._fault_request(card_id, request)
        plan = self._fault_plan(card, st, name, str(request.get("description") or "").strip())
        return {"ok": True, "id": card, "step": name, "preview": True, **plan}

    def report_step_fault(self, card_id: str, request: dict) -> dict:
        """ReportStepFault („Opisz problem i ponów”): karta w Backlogu projektu plasma-tide albo nowe wystąpienie
        w otwartej karcie o tym samym podpisie, potem ponowienie kroku istniejącym StepResolve (`retry`).

        Karta powstaje przez zwykłe API tablicy (`create_card`, `update_card`) wyłącznie w projekcie
        `faults.REPORT_PROJECT` — projektu nie podaje żądanie. Zapis jest w dzienniku karty (`step.reported`), więc
        powtórzone zgłoszenie tego samego kroku nie zakłada drugiej karty, tylko ponawia krok."""
        card, st, name = self._fault_request(card_id, request)
        description = str(request.get("description") or "").strip()
        reported = next((e for e in reversed(self.journal.read(card))
                         if e.get("type") == "step.reported" and e.get("step") == name), None)
        if reported is None:
            plan = self._save_fault_report(card, st, name, description)
            reported = self.journal.append(card, "step.reported", step=name,
                                           ticket=(st.steps.get(name) or {}).get("ticket") or "",
                                           reported=plan["reported"], action=plan["action"],
                                           project=faults.REPORT_PROJECT, signature=plan["signature"],
                                           fault=plan["fault"])
        resumed = self.resolve_step(card, {"choice": "retry", "step": name})
        return {**resumed, "reported": reported.get("reported") or "", "action": reported.get("action") or "",
                "project": faults.REPORT_PROJECT}

    def _fault_request(self, card_id: str, request: dict) -> tuple[str, FlowState, str]:
        """Sprawdzenie zgłoszenia przed czymkolwiek: krok tej karty czeka na decyzję po błędzie (jak „Ponów krok”)."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if not isinstance(request, dict) or set(request) - {"step", "description"}:
            raise FlowError("bad-request", "oczekiwano kroku i opisu problemu")
        description = request.get("description")
        if not isinstance(description, str) or not description.strip() or \
                len(description.strip()) > faults.DESCRIPTION_LIMIT:
            raise FlowError("bad-description", f"opis problemu musi mieć od 1 do {faults.DESCRIPTION_LIMIT} znaków")
        st = self.journal.state(card)
        name = str(request.get("step") or "") or decision_step(st)
        step, done = st.steps.get(name) or {}, st.finished.get(name) or {}
        busy = any((s.get("worktree") == step.get("worktree") if step.get("kind") in
                    ("plan-amend", "plan-review", "plan-revise") else s.get("ticket") == step.get("ticket"))
                   for s in st.running())
        if not step or st.card_finished or st.merged or busy or done.get("result") != "error" or \
                int(step.get("attempt") or 0) == 0 or name in st.step_decisions or \
                name in st.superseded or any(s.get("parentStep") == name for s in st.steps.values()):
            raise FlowError("not-waiting", f"krok {name or '?'} nie czeka na decyzję po błędzie")
        return card, st, name

    @staticmethod
    def _caretaker_description(request: dict) -> None:
        value = request.get("description")
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= faults.DESCRIPTION_LIMIT:
            raise FlowError("bad-request", "opis problemu musi mieć od 1 do 4000 znaków")

    def _save_fault_report(self, card, st, name, description, *, problem=None, occurrence_id=""):
        """Wspólny zapis zgłoszenia; wybór karty i zapis pod jedną blokadą, bez ponawiania pracy."""
        try:
            with self.journal.lock:
                plan = self._fault_plan(card, st, name, description, problem=problem, occurrence_id=occurrence_id)
                if plan["action"] == "existing":
                    return {**plan, "reported": plan["card"]}
                if plan["action"] == "append":
                    target = self.store.load().cards[card_num(plan["card"])]
                    saved = self.store.update_card(target.id, {
                        "description": target.description.rstrip("\n") + "\n\n" + plan["occurrence"]})
                else:
                    saved = self.store.create_card({"title": plan["title"], "description": plan["description"],
                                                    "project": faults.REPORT_PROJECT, "column": "backlog"})
        except BoardError as exc:
            raise FlowError(exc.code, f"nie zapisano karty z błędem: {exc}") from None
        return {**plan, "reported": saved.get("id") or plan["card"]}

    def _fault_plan(self, card: str, st: FlowState, name: str, description: str, *, problem=None,
                    occurrence_id: str = "") -> dict:
        """Co zapisze zgłoszenie: nowa karta albo wystąpienie dopisane do otwartej karty o tym samym podpisie."""
        from .decisions import build_decisions
        step, done = st.steps.get(name) or {}, st.finished.get(name) or {}
        found = faults.of_finished(done)
        if not name and problem:
            stage = "stalled" if problem.get("trigger") == "stalled" else problem.get("stage") or "stalled"
            source = (st.flow_errors.get(stage) or {}).get("event") or {}
            step = {"kind": "flow", "ticket": problem.get("ticket") or ""}
            done = {**source, "result": "error", "reason": source.get("error") or "karta stoi"}
            found = {"fault": "tide", "cause": stage}
        sig = faults.signature(step.get("kind") or "", found["fault"], found["cause"])
        # Wycinek wyłącznie z dziennika tego kroku (bez dowiązań), po zamaskowaniu sekretów.
        step_dir = step.get("stepDir") or ""
        log = os.path.join(step_dir, "out", "step.log") if step_dir else ""
        lines = faults.read_log(log) if log and os.path.realpath(log).startswith(
            os.path.realpath(step_dir) + os.sep) else []
        board = self.store.load()
        if faults.REPORT_PROJECT not in board.projects:
            raise FlowError("no-report-project", f"na tablicy nie ma projektu {faults.REPORT_PROJECT} — "
                                                 "załóż go, żeby zgłaszać błędy Tide")
        when = datetime.fromtimestamp(_epoch(done.get("ts")) or self.clock()).astimezone().strftime("%Y-%m-%d %H:%M")
        occurrence = faults.occurrence(
            when=when, card=card, step=name, step_kind=step.get("kind") or "", ticket=step.get("ticket") or "",
            executor=step.get("executor") or "", attempt=int(step.get("attempt") or 0),
            result=done.get("result") or "", reason=done.get("reason") or "", fault=found["fault"],
            cause=found["cause"], description=description, log_excerpt=faults.excerpt(lines),
            reason_labels=REASON_LABELS, occurrence_id=occurrence_id)
        plan = {"signature": sig, "fault": found["fault"], "project": faults.REPORT_PROJECT,
                "occurrence": occurrence}
        # Znacznik jest zapisany razem z wystąpieniem, więc przetrwa przerwanie przed caretaker.reported.
        # Odzyskanie własnego zapisu obejmuje również karty zarchiwizowane i scalone.
        marker = faults.occurrence_marker(occurrence_id)
        if marker:
            for item in board.cards.values():
                if item.project == faults.REPORT_PROJECT and marker in item.description.splitlines():
                    return {**plan, "action": "existing", "card": item.id, "title": item.title,
                            "description": item.description}
        target = None
        for item in sorted(board.cards.values(), key=lambda c: c.num, reverse=True):
            if item.project == faults.REPORT_PROJECT and item.column != "archived" and \
                    faults.has_signature(item.description, sig) and not self._card_merged(item.id):
                target = item
                break
        if target is not None:
            body = target.description.rstrip("\n") + "\n\n" + occurrence
            if len(body.encode("utf-8")) > DESCRIPTION_LIMIT_BYTES:
                occurrence = faults.occurrence(
                    when=when, card=card, step=name, step_kind=step.get("kind") or "",
                    ticket=step.get("ticket") or "", executor=step.get("executor") or "",
                    attempt=int(step.get("attempt") or 0), result=done.get("result") or "",
                    reason=done.get("reason") or "", fault=found["fault"], cause=found["cause"],
                    description=description, log_excerpt="", reason_labels=REASON_LABELS, with_excerpt=False,
                    occurrence_id=occurrence_id)
                plan["occurrence"] = occurrence
            return {**plan, "action": "append", "card": target.id, "title": target.title, "description": occurrence}
        decision = next((d for d in build_decisions(st) if (
            (d.get("technical") or {}).get("problem") == problem.get("id") if problem and not name
            else d.get("step") == name)), None) or {}
        happened = decision.get("happened") or "Krok zakończył się błędem."
        title = faults.card_title(step.get("kind") or "", found["fault"], found["cause"], REASON_LABELS)
        body = faults.card_body(card=card, sig=sig, happened=happened, first=occurrence, caretaker=problem is not None)
        return {**plan, "action": "create", "card": "", "title": title, "description": body}

    def _card_merged(self, card_id: str) -> bool:
        """Karta scalona do zapisanego celu, także w historycznym formacie."""
        try:
            return self.journal.has(card_id) and bool(self.journal.state(card_id).merged)
        except Exception:
            return False

    def _grant(self, card: str, step: dict, done: dict, allow) -> tuple[list[str], list[str]]:
        """Zgoda na polecenie kroku: dopisuje je do listy dozwolonych projektu. Bez `allow` — całe polecenie, jak
        dotąd (pojedyncze, niezabronione, do MAX_COMMAND znaków). Z `allow` (PT-124) — dokładnie wybrane polecenia
        z łańcucha, wskazane indeksami listy `commands[]` decyzji; flagi liczone są od nowa z bieżącymi zakazami
        projektu, więc zakaz dopisany po zakończeniu kroku też wygrywa, a lista musi być tą samą, którą pokazało
        okno (zapisaną przy wyniku kroku). Zwraca (dopisane, pominięte)."""
        from ..board import chain
        from ..board.commands import MAX_COMMAND, is_allowed, is_forbidden
        command = str(done.get("command") or "").strip()
        if not command:
            raise FlowError("bad-command", "krok nie podał polecenia do zatwierdzenia")
        _, project = self._project(card)
        if allow is None:
            if is_forbidden(command, project.denied_commands):
                raise FlowError("forbidden-command", f"polecenie „{command}” jest zabronione w projekcie")
            from ..board.commands import sandbox_grant
            grant = sandbox_grant(command, project.denied_commands)
            if grant is None:
                raise FlowError("command-too-long", f"polecenie przekracza {MAX_COMMAND} znaków i nie może być zatwierdzone")
            granted, skipped = [grant], []
        else:
            if not isinstance(allow, list) or not allow or len(allow) > chain.MAX_PIECES or \
                    any(type(i) is not int for i in allow) or len(set(allow)) != len(allow):
                raise FlowError("bad-allow", "`allow` to niepusta lista różnych indeksów poleceń z decyzji")
            items = chain.describe(command, denied=project.denied_commands, step_dir=step.get("stepDir") or "")
            shown = done.get("commands")
            if isinstance(shown, list) and shown and \
                    [str(i.get("text") or "") if isinstance(i, dict) else None for i in shown] != \
                    [item["text"] for item in items]:
                # Okno pokazało listę zapisaną przy wyniku kroku; gdy podział liczy dziś inaczej (np. po
                # aktualizacji usługi), indeks mógłby wskazać inne polecenie — lepiej odmówić niż zgadywać.
                raise FlowError("stale-decision", "lista poleceń zmieniła się od wyniku kroku; uruchom krok ponownie")
            for index in allow:
                if not 0 <= index < len(items):
                    raise FlowError("bad-allow", f"nie ma polecenia o indeksie {index} (poleceń: {len(items)})")
                item = items[index]
                if item["forbidden"]:
                    raise FlowError("forbidden-command", f"polecenie „{item['text']}” jest zabronione: {item['reason']}")
                if not item["canAllow"]:
                    raise FlowError("command-too-long" if len(item["text"]) > MAX_COMMAND else "cannot-allow",
                                    f"na polecenie „{item['text']}” nie można zezwolić: {item['reason']}")
            granted = [item["text"] for at, item in enumerate(items) if at in allow]
            skipped = [item["text"] for at, item in enumerate(items) if at not in allow]
        new = [text for text in granted if text not in project.allowed_commands and
               (allow is None or not is_allowed(text, project.allowed_commands))]   # już dozwolone — bez dopisywania
        if new:
            try:
                self.store.upsert_project({"slug": project.slug, "allowedCommands": [*project.allowed_commands, *new]})
            except BoardError as exc:
                raise FlowError(exc.code, str(exc)) from None
        return granted, skipped

    @staticmethod
    def _reviewer_context(st: FlowState, ticket: str) -> tuple[dict, dict]:
        """Przydział i ostatnie review; nowszy przydział recenzenta unieważnia poprzedni krok review."""
        assigned = st.assignments.get(ticket) or st.started or {}
        override = st.reviewer_overrides.get(ticket) or {}
        previous = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ticket and
                         s.get("kind") == "review"), {})
        if override and override.get("seq", 0) > previous.get("seq", 0):
            previous = {}
        return {**assigned, **override}, previous

    def _resolve_reviewer(self, assigned: dict, previous: dict, catalog, cfg):
        """Bieżący wykonawca review: historia wybiera nazwę, katalog rozwiązuje model i dostawcę."""
        name = previous.get("executor") or assigned.get("reviewer") or ""
        try:
            return execution.resolve(name, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None

    def _resume_executor(self, card: str, st: FlowState, step: dict, name: str, *, changed=False,
                         transfer=False, automated=False):
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        try:
            selected = execution.resolve(name, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if transfer and selected.provider == step.get("provider"):
            raise FlowError("same-provider", "przekazanie wymaga innego dostawcy")
        if transfer and selected.local != bool(step.get("local")):
            raise FlowError("bad-executor-provider", "praca lokalna pozostaje lokalna")
        if changed and automated and (step.get("local") or step.get("provider") == "local" or
                                      caretaker.local_only(st)) and not selected.local:
            raise FlowError("bad-executor-provider", "krok lokalny i karta „Tylko lokalnie” pozostają lokalne")
        if not changed and (selected.name != step.get("executor") or selected.model != step.get("model") or
                            selected.provider != step.get("provider") or selected.tool != step.get("program") or
                            selected.effort != step.get("effort", "") or
                            (selected.route.url if selected.route else "") != step.get("routeUrl", "") or
                            (selected.route.kind if selected.route else "") != step.get("routeKind", "") or
                            (selected.route.token_file if selected.route else "") != step.get("routeTokenFile", "")):
            raise FlowError("changed-executor", f"{card}: wykonawca zmienił się od startu kroku")
        if step.get("kind") == "plan-review":
            planner = catalog.get((st.plan_proposed or {}).get("reviewAuthor") or proposal_author_of(st, st.plan_proposed))
            if not planner or not flow_plans.independent_reviewer(planner, selected):
                raise FlowError("bad-executor", "review planu wymaga niezależnego recenzenta autora tej wersji")
        if changed and step.get("kind") not in ("ticket", "fix", "review"):
            if step.get("kind") not in ("conflict", "close") and selected.local != bool(step.get("local")):
                raise FlowError("bad-executor-provider", "zachowaj lokalny albo chmurowy zakres planowania")
            return selected
        if changed:
            assigned = st.assignments.get(step.get("ticket") or "") or st.started or {}
            if step.get("kind") == "review":
                opposite = next((s for s in reversed(list(st.steps.values()))
                                 if s.get("ticket") == step.get("ticket") and
                                 s.get("kind") in ("ticket", "fix")), {})
                other_model = opposite.get("model") or assigned.get("model") or ""
                other_provider = opposite.get("provider") or assigned.get("provider") or ""
            else:
                reviewer, opposite = self._reviewer_context(st, step.get("ticket") or "")
                if automated:
                    resolved = self._resolve_reviewer(reviewer, opposite, catalog, cfg)
                    other_model, other_provider = resolved.model, resolved.provider
                else:
                    other_model = opposite.get("model") or reviewer.get("reviewerModel") or ""
                    other_provider = opposite.get("provider") or reviewer.get("reviewerProvider") or ""
            # 03.11 (decyzja użytkownika): niezależność autora i recenzenta to rekomendacja planu, nie blokada —
            # wybór użytkownika albo eskalacja Tide wygrywa (K-31/DPT-5: „Ponów innym wykonawcą” kończył się błędem
            # przepływu, bo recenzentem był ten sam model). Zostaje tylko rozdział pracy lokalnej i chmurowej.
            # PT-145: opiekun nie ma prawa korzystać z wyjątku dla świadomego wyboru użytkownika.
            if automated and other_model and selected.model.lower() == other_model.lower():
                raise FlowError("bad-executor", "autor i recenzent wymagają różnych modeli")
            if automated and assigned.get("preferredProvider") == "mixed" and other_provider and \
                    selected.provider == other_provider:
                raise FlowError("bad-executor-provider", "tryb mieszany wymaga innego dostawcy autora i recenzenta")
            if selected.local != (other_provider == "local") and other_provider:
                raise FlowError("bad-executor-provider", "praca lokalna wymaga lokalnego autora i recenzenta")
        return selected

    def _transfer_reviewer(self, st: FlowState, step: dict, target) -> object | None:
        """W trybie mieszanym dobiera recenzenta spoza dostawcy nowego autora."""
        ticket = step.get("ticket") or ""
        if not ticket or step.get("kind") not in ("ticket", "fix", "review"):
            return None
        assigned = st.assignments.get(ticket) or st.started or {}
        if assigned.get("preferredProvider") != "mixed":
            return None
        if step.get("kind") == "review":
            author = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ticket and
                           s.get("kind") in ("ticket", "fix")), {})
            if target.provider == (author.get("provider") or assigned.get("provider")):
                raise FlowError("bad-executor-provider", "recenzent musi pozostać u innego dostawcy niż autor")
            return None
        override = st.reviewer_overrides.get(ticket) or {}
        reviewer_provider = override.get("reviewerProvider") or assigned.get("reviewerProvider") or ""
        if target.provider != reviewer_provider:
            return None
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-reviewer", "katalog recenzentów jest niedostępny")
        names = [step.get("executor") or "", *(entry.name for entry in catalog.executors)]
        for name in dict.fromkeys(names):
            try:
                candidate = execution.resolve(name, catalog, config=cfg, probe=self.model_probe,
                                              which=self.which)
            except execution.ExecutionError:
                continue
            if candidate.provider != target.provider and candidate.local == target.local and \
                    candidate.model.lower() != target.model.lower():
                return candidate
        raise FlowError("no-reviewer", "nie ma dostępnego recenzenta u innego dostawcy")

    def _reassign_waiting_reviewer(self, card: str, st: FlowState, request: dict) -> dict:
        """Recenzent review, które czeka przed startem (limit dostawcy, kolejka), przepięty na innego wykonawcę
        (05.11: okno tygodniowe Claude na 10% — recenzje Opusa stały do soboty, użytkownik chciał Codexa).
        Wybór użytkownika: reguła „inny model niż autor” daje tylko notkę w historii, jak przy przekazaniu."""
        ticket = str(request.get("ticket") or "").strip()
        executor = str(request.get("executor") or "").strip()
        if ("review" not in {kind for (t, kind) in st.waiting if t == ticket} or
                any(s.get("ticket") == ticket for s in st.running())):
            raise FlowError("not-waiting", f"{card}: review ticketu {ticket or '?'} nie czeka na start")
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None or not executor or catalog.get(executor) is None:
            raise FlowError("bad-executor", "wybierz recenzenta z katalogu wykonawców")
        try:
            candidate = execution.resolve(executor, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        source = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ticket), None)
        if source is None:
            raise FlowError("not-waiting", f"{card}: ticket {ticket} nie ma jeszcze kroku")
        self._save_transfer_reviewer(card, source, candidate)
        return {"ok": True, "id": card, "ticket": ticket, "reviewer": candidate.name}

    def _reassign_fix_author(self, card: str, st: FlowState, request: dict) -> dict:
        """Autor kolejnych poprawek ticketu przepięty na innego wykonawcę (05.11: poprawki po review czekały na limit
        Claude do soboty). Poprawka zawsze startuje świeżą sesją, więc zmiana autora nie gubi rozmowy."""
        ticket = str(request.get("ticket") or "").strip()
        executor = str(request.get("executor") or "").strip()
        if ticket == PLAN_AUTHOR:
            # Planista kolejnych poprawek planu (06.11, K-38: poprawka czekała na limit Claude do soboty).
            if not st.plan_proposed or st.card_finished or any(
                    s.get("kind") in ("plan-amend", "plan-revise", "planning", "plan-review") for s in st.running()):
                raise FlowError("not-waiting", f"{card}: planista karty właśnie pracuje albo karta nie ma planu")
        elif ticket == CLOSE_AUTHOR:
            # Domknięcie karty (06.11, K-37: domknięcie planisty Opus czekało na limit Claude do soboty).
            if st.card_finished or any(s.get("kind") == "close" for s in st.running()):
                raise FlowError("not-waiting", f"{card}: domknięcie karty właśnie trwa albo karta jest skończona")
        elif not any(s.get("ticket") == ticket for s in st.steps.values()) or \
                any(s.get("ticket") == ticket and s.get("kind") in ("ticket", "fix") for s in st.running()):
            raise FlowError("not-waiting", f"{card}: autor ticketu {ticket or '?'} właśnie pracuje")
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None or not executor or catalog.get(executor) is None:
            raise FlowError("bad-executor", "wybierz autora z katalogu wykonawców")
        source = next((s for name, s in reversed(list(st.steps.items()))
                       if ticket == PLAN_AUTHOR and s.get("kind") in ("plan-amend", "plan-revise")
                       and name not in st.superseded), {})
        resumable = ((st.finished.get(source.get("step")) or {}).get("result") in ("question", "denied", "error")
                     and not any(s.get("parentStep") == source.get("step") for s in st.steps.values()))
        try:
            if resumable:
                # Sukces API musi oznaczać, że istniejący krok da się wznowić tym wykonawcą.
                self._resume_executor(card, st, source, executor, changed=True)
            else:
                execution.resolve(executor, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        event = self.journal.append(card, "author.reassigned", ticket=ticket, executor=executor)
        return {"ok": True, "id": card, "ticket": ticket, "executor": executor, "seq": event["seq"]}

    def _save_transfer_reviewer(self, card: str, step: dict, candidate) -> None:
        if candidate is None:
            return
        prior = self.journal.state(card).reviewer_overrides.get(step.get("ticket") or "") or {}
        if prior.get("sourceStep") == step.get("step") and prior.get("reviewer") == candidate.name:
            return
        route = candidate.route
        self.journal.append(card, "reviewer.reassigned", ticket=step.get("ticket") or "",
                            sourceStep=step.get("step") or "", reason="transfer-provider",
                            reviewer=candidate.name, reviewerProgram=candidate.tool,
                            reviewerModel=candidate.model, reviewerProvider=candidate.provider,
                            reviewerRouteUrl=route.url if route else "",
                            reviewerRouteKind=route.kind if route else "",
                            reviewerRouteTokenFile=route.token_file if route else "",
                            reviewerEffort=candidate.effort)

    def _caretaker_takes_over(self, card: str, st: FlowState, name: str) -> bool:
        """Ten sam powód drugi raz (PT-148): opiekun zamiast automatycznego ponowienia albo automatycznej odmowy
        łańcucha. Ocenia `caretaker.takes_over` — bez opiekuna zostaje dzisiejszy automat."""
        st.caretaker_setup = self._caretaker_setup(st)
        return caretaker.takes_over(st, name, self._card_column(card))

    def _caretaker_resume(self, card: str, st: FlowState, step: dict) -> bool:
        """`retry` opiekuna przy problemie bez decyzji (PT-150): wewnętrzna ścieżka automatycznego ponowienia
        (`step.retry` → wznowienie z `attempt: 1`), z instrukcją opiekuna w kartce wznowienia. Wspólne dla kroków
        ticketów (`_advance`), planowania (`_advance_planning`) i review/poprawek planu (`_plan_step_decision`)."""
        name = step.get("step") or ""
        retried = st.step_retries.get(name) or {}
        if not (retried.get("caretaker") or retried.get("user")) or name in st.step_decisions:
            return False
        self._resume_step(card, st, step, {"choice": "retry", "answer": retried.get("instruction") or "",
                                           "caretaker": retried.get("caretaker") or ""}, attempt=1)
        return True

    def _resume_step(self, card: str, st: FlowState, step: dict, decision: dict, *, attempt=0) -> None:
        """Ten sam ticket i worktree; nowa próba ma osobny katalog wyniku i zdarzenie."""
        st = self._process_check(card, step)
        if step.get("step") in st.superseded and step.get("step") not in st.plan_resumes:
            raise FlowError("stale-context", "Nie można wznowić sesji poprzedniego kontekstu.")
        if any(s.get("parentStep") == step.get("step") for s in st.steps.values()):
            return
        done = st.finished.get(step.get("step")) or {}
        if done.get("outside") and decision.get("choice") in ("run", "run-always", "run-ticket"):
            if step.get("runner") == boxstep.RUNNER:
                raise FlowError("box-outside", "krok w pudełku nie może uruchamiać poleceń na hoście")
            decision = self._outside_ready(card, step, done, decision)
            if decision is None:
                return          # polecenie poza piaskownicą jeszcze trwa
            st = self.journal.state(card)
        item, project = self._project(card)
        info = ticket_of(st, step.get("ticket") or "")
        resume_ticket = step.get("ticket") or ("" if step.get("kind") == "close" or
                                               str(step.get("kind") or "").startswith("plan") else info["id"])
        choice = decision.get("choice") or ""
        name = decision.get("executor") if choice in ("change-executor", "transfer") else step.get("executor") or ""
        # Przepięty autor ticketu (05.11, limit Claude) wznawia krok autora świeżą sesją, jak przy zmianie wykonawcy.
        kind = step.get("kind")
        author_key = (CLOSE_AUTHOR if kind == "close" else PLAN_AUTHOR if kind in ("plan-amend", "plan-revise")
                      else step.get("ticket") or "")
        author_override = st.author_overrides.get(author_key) or {}
        reassigned = (choice not in ("change-executor", "transfer") and
                      kind in ("ticket", "fix", "close", "plan-amend", "plan-revise") and
                      author_override.get("seq", 0) > step.get("seq", 0) and
                      author_override.get("executor") not in ("", None, step.get("executor")))
        if reassigned:
            name = author_override["executor"]
        override = reviewer_override(st, step, decision, name)
        if override:
            name, reassigned = override, True
        fresh = choice in ("change-executor", "transfer") or reassigned
        fix_reason, fix_author, moved = "", {}, False
        if kind == "fix":
            # K-88/PT-312: jawna zmiana wykonawcy albo przepięty autor — `override`; zwykłe wznowienie przechodzi
            # przez regułę niezależności review (recenzent nie wznawia poprawek, które potem oceni).
            if fresh:
                fix_reason = "override"
            else:
                target, fix_reason, fix_author = self._resumed_fix_target(card, st, step)
                if target and target != name:
                    name, reassigned, fresh, moved = target, True, True, True
        step_id = self._next_step_id(card, resume_ticket or "plan", st)
        try:
            selected = self._resume_executor(card, st, step, name,
                                             changed=fresh,
                                             transfer=choice == "transfer", automated=bool(decision.get("caretaker")))
        except FlowError as exc:
            if exc.code != "local-unavailable" or choice in ("change-executor", "transfer"):
                raise
            self.journal.append(card, "step.started", step=step_id, kind=step.get("kind") or "ticket",
                                ticket=resume_ticket, round=step.get("round") or 0,
                                executor=name, program=step.get("program") or "", model=step.get("model") or "",
                                provider=step.get("provider") or "", local=True,
                                effort=step.get("effort") or "", routeUrl=step.get("routeUrl") or "",
                                routeKind=step.get("routeKind") or "",
                                routeTokenFile=step.get("routeTokenFile") or "",
                                parentStep=step.get("step") or "", attempt=attempt, session=step.get("session") or "",
                                worktree=step.get("worktree") or "", branch=step.get("branch") or "",
                                baseRef=step.get("baseRef") or "", baseCommit=step.get("baseCommit") or "",
                                baseVersion=step.get("baseVersion", 0),
                                stepDir=step.get("stepDir") or "", reviewTip=step.get("reviewTip") or "",
                                **{k: step[k] for k in ("runner", "box", "boxSlug", "boxIp", "boxAccess") if k in step},
                                **({"executorReason": fix_reason} if fix_reason else {}))
            self.journal.append(card, "step.finished", step=step_id, ticket=resume_ticket,
                                result="error", reason="local-unavailable", question="", command="", notes=[],
                                kept=[], exit=None)
            return
        if moved and fix_reason == "author":
            # Automatyczny powrót do autora nie jest ręczną zgodą na zmianę jego modelu: ta sama kontrola
            # tożsamości autora co w `_fix` (review r2, MAJOR-2).
            self._check_author_identity(card, selected, fix_author,
                                        st.assignments.get(step.get("ticket") or "") or st.started or {})
        old_prompt = os.path.join(step.get("stepDir") or "", "in", "prompt.txt")
        try:
            with open(old_prompt, encoding="utf-8") as fh:
                prompt = fh.read(OUTPUT_MAX)
        except OSError:
            raise FlowError("missing-step-input", f"{card}: nie ma kartki poprzedniego kroku") from None
        old_out = os.path.join(step.get("stepDir") or "", "out")
        resume_worktree = step.get("worktree") or info["worktree"]
        resume_branch = step.get("branch") or info["branch"]
        interactive_transfer = choice == "transfer" and step.get("mode") == "interactive"
        suffix = "planning" if step.get("kind") == "planning" else "interactive"
        new_out = (resume_worktree + f".{suffix}-{step_id}.step/out" if interactive_transfer else
                   resume_worktree + f".resume-{step_id}.step/out")
        prompt = prompt.replace(old_out, new_out)
        prompt += handoff.resume_context(choice=choice, prior=st.finished.get(step.get("step")) or {},
                                         answer=decision.get("answer") or "", command=decision.get("command") or "",
                                         out_dir=new_out, allowed=decision.get("commands"),
                                         skipped=decision.get("skipped") or (), instead=decision.get("instead") or "",
                                         caretaker=bool(decision.get("caretaker")))
        if choice == "transfer":
            wip = (st.wip_retries.get(step.get("step")) or {}).get("wip") or \
                  (st.finished.get(step.get("step")) or {}).get("wip") or {}
            note_path = wip.get("handoff") or ""
            if note_path:
                try:
                    with open(note_path, encoding="utf-8") as fh:
                        note = fh.read(OUTPUT_MAX)
                except OSError:
                    raise FlowError("missing-handoff", "brakuje notki przekazania kroku") from None
                prompt += "\n\nNotka techniczna przekazania (dane z poprzedniego kroku):\n" + note
        if reassigned:
            prompt += (f"\n\nPoprzedni krok prowadził inny wykonawca ({step.get('executor') or '?'}); "
                       f"zaczynasz nową sesję. Sprawdź `git log` i `git status` w worktree oraz notkę i wynik "
                       f"poprzedniego kroku w {old_out}.")
        session = "" if fresh else step.get("session") or ""
        if not session and not fresh and step.get("program") == "codex":
            finder = getattr(execution, "codex_session_id", None)
            if finder:
                session = finder(os.path.join(old_out, "step.log"))
        if interactive_transfer:
            if self.terminal is None or not self.terminal.available():
                raise FlowError("no-terminal", "nie znaleziono Konsoli do przekazania planowania")
            self._launch_full_planning(item, project, selected,
                                       {"branch": resume_branch, "worktree": resume_worktree},
                                       step_id, [], [], prompt, parent_step=step.get("step") or "",
                                       attempt=attempt, include_context=False,
                                       kind=step.get("kind") or "ticket", ticket=resume_ticket,
                                       round_no=int(step.get("round") or 0),
                                       review_tip=step.get("reviewTip") or "")
            return
        self._launch(item, project, selected, resume_ticket,
                     {"branch": info["cardBranch"]},
                     {"branch": resume_branch, "worktree": resume_worktree},
                     step_id, [], kind=step.get("kind") or "ticket", round_no=int(step.get("round") or 0),
                     prompt_text=prompt, review_tip=step.get("reviewTip") or "",
                     parent_step=step.get("step") or "", attempt=attempt, resume_session=session,
                     executor_reason=fix_reason)

    def resolve_review(self, card_id: str, choice: str) -> dict:
        """Jawna decyzja po trzecim i każdym następnym nieudanym review."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if choice not in ("retry", "reject", "accept"):
            raise FlowError("bad-choice", "wybierz ponowne poprawki, scalenie mimo uwag albo odrzucenie ticketu")
        st = self.journal.state(card)
        self._require_base_context(card)
        st.last_step = attention_step(st) or st.last_step
        step = st.steps.get(st.last_step) or {}
        if step.get("kind") != "review" and choice == "reject":
            # Po odrzuceniu karta może już mieć nowszy krok domknięcia. Zachowaj
            # idempotencję decyzji, gdy nie ma kolejnego pytania o review.
            rejected = next((decision for decision in reversed(list(st.review_decisions.values()))
                             if decision.get("choice") == "reject" and
                             decision.get("ticket") in st.rejected), None)
            if rejected and not attention_step(st):
                step = next((candidate for candidate in reversed(list(st.steps.values()))
                             if candidate.get("kind") == "review" and
                             candidate.get("ticket") == rejected.get("ticket") and
                             candidate.get("round") == rejected.get("round")), step)
        ticket, round_no = step.get("ticket") or "", int(step.get("round") or 0)
        verdict = st.verdicts.get((ticket, round_no)) or {}
        previous = st.review_decisions.get((ticket, round_no)) or {}
        if previous.get("choice") == choice == "reject" and not st.card_finished:
            if ticket not in st.rejected:
                self.journal.append(card, "ticket.rejected", ticket=ticket)
            return {"ok": True, "id": card, "ticket": ticket, "round": round_no, "choice": choice,
                    "already": True}
        # Opiekun analizuje widoczną decyzję, ale nie blokuje jej rozstrzygnięcia przez użytkownika.
        ticket_running = any(s.get("ticket") == ticket and s.get("kind") != "caretaker" for s in st.running())
        if st.card_finished or ticket_running or step.get("kind") != "review" or round_no < 3 or \
                verdict.get("verdict") != "REQUEST_CHANGES" or \
                (ticket, round_no) not in st.review_questions or \
                previous:
            raise FlowError("no-review-question", f"{card}: nie ma pytania o review do rozstrzygnięcia")
        self.journal.append(card, "review.decision", ticket=ticket, round=round_no, choice=choice)
        if choice == "reject":
            self.journal.append(card, "ticket.rejected", ticket=ticket)
        return {"ok": True, "id": card, "ticket": ticket, "round": round_no, "choice": choice}

    def _project(self, card: str):
        board = self.store.load()
        item = board.cards.get(card_num(card))
        project = board.projects.get(item.project) if item else None
        if item is None or project is None:
            raise FlowError("no-project", f"{card}: nie ma karty albo projektu dla kroku")
        return item, project

    def _note(self, card: str, step: dict) -> str:
        name = handoff.REVIEW_FILE if step.get("kind") == "review" else handoff.NOTE_FILE
        path = os.path.join(self.store.root, self.journal.rel_dir(card), "steps", step.get("step") or "", name)
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read(OUTPUT_MAX)
        except OSError:
            return ""

    def _desk_items(self, card: str, st) -> list[tuple[str, str, list[str]]]:
        """Pozycje „Do sprawdzenia na pulpicie” z notek wszystkich kroków karty, w kolejności kroków;
        kroki odrzuconych ticketów są pomijane (ich zmian nie scalono)."""
        found = []
        for step in sorted(st.steps.values(), key=lambda s: int(s.get("seq") or 0)):
            if step.get("kind") == "close" or (step.get("ticket") or "") in st.rejected:
                continue
            items = handoff.desk_items(self._note(card, step))
            if items:
                found.append((step.get("step") or "", step.get("ticket") or "", items))
        return found

    def _rules(self, repo: str) -> str:
        out = []
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = os.path.join(repo, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    out.append(f"### {name}\n{fh.read(OUTPUT_MAX)}")
            except OSError:
                pass
        return "\n\n".join(out)

    def _review(self, card: str, st: FlowState, source: dict, round_no: int) -> None:
        item, project = self._project(card)
        ticket = source["ticket"]
        assigned, previous = self._reviewer_context(st, ticket)
        info = ticket_of(st, ticket)
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-reviewer", f"{card}: katalog recenzentów jest niedostępny")
        reviewer = self._resolve_reviewer(assigned, previous, catalog, cfg)
        expected = {
            "program": previous.get("program") or assigned.get("reviewerProgram"),
            "model": previous.get("model") or assigned.get("reviewerModel"),
            "provider": previous.get("provider") or assigned.get("reviewerProvider"),
            "effort": previous.get("effort", assigned.get("reviewerEffort", "")),
            "routeUrl": previous.get("routeUrl", assigned.get("reviewerRouteUrl", "")),
            "routeKind": previous.get("routeKind", assigned.get("reviewerRouteKind", "")),
            "routeTokenFile": previous.get("routeTokenFile", assigned.get("reviewerRouteTokenFile", "")),
        }
        # Przeniesienie przy limicie i ręczna zmiana wykonawcy (K-16/LEGAL-66: Codex padł, użytkownik wybrał Sonnet)
        # zwalniają z reguły dostawców planu; recenzent nadal musi być innym modelem niż autor.
        transferred = any((st.step_decisions.get(name) or {}).get("choice") in ("transfer", "change-executor") and
                          (st.steps.get(name) or {}).get("ticket") == ticket for name in st.step_decisions)
        if reviewer_changed(reviewer, expected, source, assigned, transferred) and \
                not any(e.get("type") == "review.reviewer-note" and e.get("ticket") == ticket and
                        e.get("round") == round_no for e in self.journal.read(card)):
            # 03.11: niezależny recenzent to rekomendacja planu, nie blokada — karta stała na „katalog zmienił
            # recenzenta”, a użytkownik nie miał czego wybrać. Review rusza, w historii zostaje notka.
            self.journal.append(card, "review.reviewer-note", ticket=ticket, round=round_no,
                                reviewer=reviewer.name, author=source.get("executor") or source.get("model") or "")
        path = info["worktree"]
        try:
            tip = self.git.clean_tip(project.repo, info["branch"], path)
        except FlowGitError as exc:
            # K-35/PT-132: autor skończył, ale zostawił nieśledzony plik diagnostyczny — zamiast stać, krok autora
            # wraca raz z prośbą o porządek (commit potrzebnych zmian, usunięcie tymczasowych plików).
            asked = sum(1 for e in self.journal.read(card) if e.get("type") == "step.cleanup" and e.get("ticket") == ticket)
            if "lokalne zmiany" not in str(exc):
                raise
            if asked >= 1 or not source.get("step") or \
                    any(s.get("parentStep") == source["step"] for s in st.steps.values()):
                # Po jednej prośbie o porządek zostały już tylko nieśledzone pliki (dane wejściowe, wyniki pomiarów):
                # nie zmieniają gałęzi ani scalenia, więc review rusza; zmiany śledzonych plików dalej blokują
                # (04.11, K-43/BO-9: ponowione review kończyło się flow.error „worktree ticketu ma lokalne zmiany”).
                tip = self.git.clean_tip(project.repo, info["branch"], path, untracked=False)
            else:
                self.journal.append(card, "step.cleanup", step=source["step"], ticket=ticket)
                self._resume_step(card, self.journal.state(card), source, {"choice": "cleanup"})
                return
        diff = self.git.review_diff(project.repo, info["cardBranch"], info["branch"])
        step_id = self._next_step_id(card, ticket, st)
        out = path + f".review-{round_no}.step/out"
        title, description = self._ticket_text(card, st, ticket, item)
        prompt = handoff.review_card(card_id=card, title=title, description=description, ticket=ticket,
                                     round_no=round_no, card_branch=info["cardBranch"],
                                     ticket_branch=info["branch"], worktree=path, out_dir=out,
                                     diff=diff, note=self._note(card, source), rules=self._rules(project.repo),
                                     test_base=info["base"])
        self._launch(item, project, reviewer, ticket,
                     {"branch": info["cardBranch"]},
                     {"branch": info["branch"], "worktree": path}, step_id, [],
                     kind="review", round_no=round_no, prompt_text=prompt, review_tip=tip, evidence=source)

    def _fix(self, card: str, st: FlowState, review_step: dict) -> None:
        item, project = self._project(card)
        ticket = review_step["ticket"]
        assigned = st.assignments.get(ticket) or st.started or {}
        info = ticket_of(st, ticket)
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("no-executor", f"{card}: katalog wykonawców jest niedostępny")
        round_no = int(review_step.get("round") or 1)
        name, reason, author = self._fix_target(card, st, ticket, review_step, catalog, cfg)
        try:
            executor = execution.resolve(name, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if reason == "author":
            self._check_author_identity(card, executor, author, assigned)
        step_id = self._next_step_id(card, ticket, st)
        out = info["worktree"] + f".fix-{round_no}.step/out"
        title, description = self._ticket_text(card, st, ticket, item)
        prompt = handoff.fix_card(card_id=card, title=title, description=description, ticket=ticket,
                                  round_no=round_no, branch=info["branch"], worktree=info["worktree"],
                                  out_dir=out, review=self._note(card, review_step), test_base=info["base"])
        self._launch(item, project, executor, ticket,
                     {"branch": info["cardBranch"]},
                     {"branch": info["branch"], "worktree": info["worktree"]},
                     step_id, [], kind="fix", round_no=round_no, prompt_text=prompt, executor_reason=reason)

    def _fix_target(self, card: str, st: FlowState, ticket: str, review_step: dict, catalog, cfg) -> tuple:
        """K-88/PT-312: recenzent nie ocenia własnych poprawek. Zwraca `(nazwa wykonawcy, powód, krok autora)`.
        Kolejność: ręczna zmiana autora (`override`), eskalacja do mocnego wykonawcy spoza recenzentów i autora
        (`escalated`), dotychczasowy autor (`author`). Autor = ostatni krok autora, który nie był eskalacją ani
        pracą recenzenta — ani bieżącego, ani recenzenta tamtej rundy (stara historia: od 3. rundy łatał recenzent,
        który mógł potem zostać zmieniony)."""
        assigned = st.assignments.get(ticket) or st.started or {}
        reviewers = self._fix_reviewers(st, ticket, review_step)
        authored = [s for s in st.steps.values() if s.get("ticket") == ticket and s.get("kind") in ("ticket", "fix")]
        taken = self._reviewer_fixes(st, ticket)
        own = [s for s in authored if s.get("executorReason") != "escalated" and s.get("step") not in taken]
        author = next((s for s in reversed(own) if s.get("executor") not in reviewers), None) or \
            (own[-1] if own else authored[0] if authored else None)
        if author is None:
            raise FlowError("no-executor", f"{card}: nie ma kroku autora do poprawienia")
        # Ręczna zmiana autora z panelu dotyczy wszystkich kolejnych poprawek ticketu (`_reassign_fix_author`),
        # więc wygrywa z eskalacją także w rundach po pierwszym kroku nowego autora (review r2, MAJOR-3).
        override = st.author_overrides.get(ticket) or {}
        if override.get("executor"):
            return override["executor"], "override", author
        author_name = author.get("executor") or assigned.get("executor") or ""
        if int(review_step.get("round") or 1) >= ESCALATE_FIX_ROUND:
            escalated = self._escalated_fixer(st, ticket, assigned, author, {*reviewers, author_name}, catalog, cfg)
            if escalated is not None:
                return escalated.name, "escalated", author
        return author_name, "author", author

    @staticmethod
    def _check_author_identity(card: str, executor, author: dict, assigned: dict) -> None:
        """Gałąź „autor”: wykonawca z katalogu musi być tym samym autorem (program, model, dostawca, effort, route),
        który pisał ticket — inaczej `changed-executor`. Wspólne dla `_fix` i automatycznego powrotu do autora
        przy wznowieniu poprawek (review r2, MAJOR-2)."""
        if executor.tool != (author.get("program") or assigned.get("program")) or \
                executor.model != (author.get("model") or assigned.get("model")) or \
                executor.provider != (author.get("provider") or assigned.get("provider")) or \
                executor.effort != author.get("effort", assigned.get("executorEffort", "")) or \
                (executor.route.url if executor.route else "") != author.get("routeUrl", assigned.get("executorRouteUrl", "")) or \
                (executor.route.kind if executor.route else "") != author.get("routeKind", assigned.get("executorRouteKind", "")) or \
                (executor.route.token_file if executor.route else "") != author.get("routeTokenFile", assigned.get("executorRouteTokenFile", "")):
            raise FlowError("changed-executor", f"{card}: katalog zmienił wykonawcę poprawek")

    @staticmethod
    def _reviewer_fixes(st: FlowState, ticket: str) -> set:
        """Kroki poprawek ze starej historii (bez `executorReason`), które pisał recenzent tej samej rundy —
        praca recenzenta, nie autora, także gdy recenzent ticketu został potem zmieniony."""
        reviews = [s for s in st.steps.values() if s.get("ticket") == ticket and s.get("kind") == "review"]
        taken = set()
        for s in st.steps.values():
            if s.get("ticket") != ticket or s.get("kind") != "fix" or s.get("executorReason"):
                continue
            earlier = [r for r in reviews if r.get("seq", 0) < s.get("seq", 0)]
            paired = next((r for r in reversed(earlier) if int(r.get("round") or 0) == int(s.get("round") or 0)),
                          earlier[-1] if earlier else None)
            if paired is not None and paired.get("executor") == s.get("executor"):
                taken.add(s.get("step"))
        return taken

    def _resumed_fix_target(self, card: str, st: FlowState, step: dict) -> tuple[str, str, dict]:
        """Wznowienie kroku `fix` bez jawnej zmiany wykonawcy: ta sama reguła co `_fix`, licząc od review, które
        krok poprawia. Inny wynik niż wykonawca kroku (recenzent łatał w starej historii, recenzentem został
        eskalowany wykonawca) przenosi wznowienie na nowego wykonawcę świeżą sesją. Ręczny wybór autora zostaje.
        Zwraca `(nazwa, powód, krok autora)`; krok autora (pusty poza regułą) służy kontroli `changed-executor`."""
        current = step.get("executor") or ""
        ticket = step.get("ticket") or ""
        override = st.author_overrides.get(ticket) or {}
        if step.get("executorReason") == "override" or (
                override.get("executor") == current and override.get("seq", 0) < step.get("seq", 0)):
            return current, "override", {}
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        reviews = [s for s in st.steps.values() if s.get("ticket") == ticket and s.get("kind") == "review"
                   and s.get("seq", 0) < step.get("seq", 0)]
        review = next((r for r in reversed(reviews) if int(r.get("round") or 0) == int(step.get("round") or 0)),
                      reviews[-1] if reviews else None)
        if catalog is None or review is None:
            return current, step.get("executorReason") or "", {}
        return self._fix_target(card, st, ticket, review, catalog, cfg)

    def _fix_reviewers(self, st: FlowState, ticket: str, review_step: dict) -> set:
        """Nazwy recenzentów, którym nie wolno pisać poprawek: autor ocenianego review i recenzent następnego
        (ręczna zmiana recenzenta po review wskazuje nowego)."""
        names = {review_step.get("executor") or ""}
        assigned, previous = self._reviewer_context(st, ticket)
        names.add(previous.get("executor") or assigned.get("reviewer") or "")
        names.discard("")
        return names

    @staticmethod
    def _ticket_class(st: FlowState, ticket: str) -> str:
        """Klasa ticketu z zatwierdzonego planu; brak wiersza albo kolumny (Od razu, plan sześciokolumnowy) —
        `standard`, jak w standardzie planu."""
        row = next((r for r in (st.plan_approved or {}).get("rows") or [] if str(r.get("id") or "") == ticket), {})
        return row.get("class") or "standard"

    def _escalated_fixer(self, st: FlowState, ticket: str, assigned: dict, author: dict, avoid: set, catalog, cfg):
        """K-88/PT-312: wykonawca poprawek od rundy ESCALATE_FIX_ROUND — klasa `mocny` preferowanego dostawcy
        zlecenia, inny niż recenzenci i autor; tylko dla ticketów spoza klas `mocny` i `lokalny`. `None` — poprawki
        zostają u autora."""
        if self._ticket_class(st, ticket) in ("mocny", "lokalny"):
            return None
        started = st.started or {}
        provider = assigned.get("preferredProvider") or started.get("preferredProvider") or ""
        if provider in ("", "mixed"):
            # Tryb mieszany: autor i recenzent są u różnych dostawców — poprawki zostają u dostawcy autora.
            provider = author.get("provider") or assigned.get("provider") or started.get("provider") or ""
        for candidate in catalog.executors:
            if candidate.provider != provider or "mocny" not in candidate.classes or candidate.name in avoid:
                continue
            try:
                return execution.resolve(candidate.name, catalog, config=cfg, probe=self.model_probe, which=self.which)
            except execution.ExecutionError:
                continue
        return None

    def _cleanup(self, card: str, st: FlowState, ticket: str, rounds: int) -> None:
        self._trial_check(ticket)
        info = ticket_of(st, ticket)
        repo = self._repo(card)
        if any(row["worktree"] == info["worktree"] for row in st.base_change.get("generation", [])):
            # Zasoby korekty pozostają pod ochroną PT-202, także po zakończeniu nowej pracy.
            self._act(card, "cleanup", ticket, lambda: {"removed": False, "preserved": True,
                                                        "reason": "base-generation"})
            return
        # Sprzątanie jest powtarzalne: nieudane (np. pliki w worktree, K-15/LEGAL-60) ponawia się przy każdym
        # przebiegu, zamiast na zawsze zatrzymać kartę.
        self._act(card, "cleanup", ticket,
                  lambda: self.git.remove_worktree(repo, info["branch"], info["worktree"]),
                  retry_failed=True, repo=repo, branch=info["branch"], worktree=info["worktree"])

    def _recover_verdicts(self, card: str) -> None:
        st = self.journal.state(card)
        for name, finished in st.finished.items():
            step = st.steps.get(name) or {}
            if name in st.superseded or step.get("kind") != "review" or finished.get("result") != "done" or \
                    (step.get("ticket"), step.get("round")) in st.verdicts:
                continue
            path = os.path.join(step.get("stepDir") or "", "out", handoff.RESULT_FILE)
            verdict = self._review_result(path)
            self._move_reviewer_files(step, step.get("repo") or self._repo(card))
            try:
                tip = self.git.clean_tip(step.get("repo") or self._repo(card),
                                         step.get("branch") or "", step.get("worktree") or "", untracked=False)
            except FlowGitError:
                tip = ""
            if verdict and tip and tip == step.get("reviewTip"):
                self.journal.append(card, "review.verdict", step=name, ticket=step["ticket"],
                                    round=step["round"], reviewer=step.get("executor") or "",
                                    baseVersion=step.get("baseVersion", 0), reviewTip=tip, **verdict)

    def _recover_review_rejections(self, card: str) -> None:
        """Decyzja reject i zdarzenie odrzucenia mogą zostać rozdzielone restartem usługi."""
        st = self.journal.state(card)
        for (ticket, _round), decision in st.review_decisions.items():
            if decision.get("choice") == "reject" and ticket not in st.rejected:
                self.journal.append(card, "ticket.rejected", ticket=ticket)
                st.rejected.add(ticket)

    def _reconcile(self, card: str, intent: dict) -> None:
        """Akcja zaczęta przed restartem usługi, bez wyniku: dokończenie albo jawny błąd (nigdy druga kopia)."""
        try:
            self._process_check(card, intent)
        except FlowError:
            return
        action, key = intent.get("action"), intent.get("key")
        if action == "main-merge":
            # Właścicielem jest _recover_main_merge; oczekiwanie nie jest końcowym wynikiem.
            return
        if intent.get('youtrack'):
            try:
                self.tracker.run(card, action, key, intent['payload'])
            except yt.YouTrackError:
                pass
            return
        if action == "reserve":
            self._act(card, action, key,
                      lambda: self._reserve(intent["repo"], intent["title"], f"{card}:{key}",
                                            intent.get("prefix") or "", int(intent.get("minimumNumber") or 0)))
            return
        if action == "plan-commit":
            self._plan_commit(card, self.journal.state(card), intent["repo"],
                              intent["branch"], intent["worktree"])
            return
        if action in ("card-branch", "ticket-branch", "review-branch", "step-reopen"):
            def make_branch():
                made = self.git.branch_worktree(intent["repo"], intent["branch"],
                                                intent["worktree"], intent["base"])
                return {**made, **({"ticket": intent["ticket"]} if action == "ticket-branch" else {})}
            self._act(card, action, key, make_branch)
            return
        if action == "cleanup":
            self._act(card, action, key,
                      lambda: self.git.remove_worktree(intent["repo"], intent["branch"], intent["worktree"]),
                      retry_failed=True)
            return
        if action == "merge":
            st = self.journal.state(card)
            step = next((s for s in st.steps.values() if s.get("ticket") == key), None)
            if step is not None:
                self._merge(card, st, step)
            return
        if action == "caretaker-action":
            # Akcja opiekuna przerwana restartem między zamiarem a wynikiem (PT-150): dokończenie tą samą ścieżką;
            # `apply` sprawdza, co już zapisał ten krok opiekuna, więc skutek powstaje dokładnie raz.
            st = self.journal.state(card)
            run = st.caretaker_runs.get(key) or st.caretaker_chat_actions.get(key)
            done = st.finished.get(key) or {}
            if run and run.get("mode") == "chat":
                done = {"outcome": run.get("outcome") or {}}
            outcome = done.get("outcome") or {}
            if run is not None and outcome.get("ok") and outcome.get("action") in caretaker.ACTIONS:
                self._caretaker_act(card, run, outcome["action"], outcome.get("params") or {})
                return
        if action == "step-start":
            unit = intent.get("unit") or f"tide-step-{key}"
            result = os.path.join(intent.get("stepDir") or "", "out", handoff.RESULT_FILE)
            if intent.get("runner") == boxstep.RUNNER:
                boxstep.recover_network(self)
            state = self._unit_state({**intent, "step": key, "unit": unit})
            code = stepstop.read_exit_code(intent.get("stepDir") or "") if state == "inactive" else None
            if state == "unknown":
                return
            proc = self.procs.get(key)
            if state == "inactive" and code is None and proc is not None and proc.poll() is None:
                return  # Klient może dopiero tworzyć jednostkę; zachowujemy otwarty zamiar i porty.
            if intent.get("runner") == boxstep.RUNNER and state == "inactive" and code is None:
                return  # Brak jednostki/kodu nie dowodzi, że proces nigdy nie wystartował.
            if state == "active" or (intent.get("runner") == boxstep.RUNNER and code is not None) or \
                    (code == 0 and os.path.isfile(result)):
                # Kod wrappera, również niezerowy i bez result.json, potwierdza wykonanie procesu.
                # Odtwarzamy krok; _poll/_finish odbiorą błąd i sprawdzą refy przed zwolnieniem próby.
                git_dir = ""
                try:
                    git_dir = agentcmd.resolve_git_dir(self._repo(card), intent.get("worktree") or "")
                except agentcmd.StepError:
                    pass
                self.journal.append(card, "result", action=action, key=key, ok=True, unit=unit, gitDir=git_dir)
                self._recover_started_steps(card, self.journal.state(card))
                return
            if code not in (None, 0):
                self.journal.append(card, "result", action=action, key=key, ok=False,
                                    error=f"program zakończył się kodem {code}")
                self._release_box(card, {**intent, "step": key})
                return
        self.journal.append(card, "result", action=action, key=key, ok=False,
                            error="przerwane restartem usługi przed końcem akcji")
        if action == "step-start":
            self._release_box(card, {**intent, "step": key})

    def _recover_started_steps(self, card: str, st: FlowState) -> None:
        """Wynik startu zapisany przed awarią oznacza żywy albo zakończony krok, nawet bez step.started."""
        try:
            st = fold(trialhold.read_process_history(self.journal, card))
        except (OSError, ValueError, TypeError, KeyError):
            return  # także bezpośrednie recovery zachowuje bajty nieznanej historii
        ticket = ticket_of(st)
        for (action, key), result in st.results.items():
            if action != "step-start" or not result.get("ok") or key in st.steps or key in st.caretakers:
                continue
            intent = st.intents.get((action, key)) or {}
            if intent.get("seq", 0) < st.base_change.get("seq", 0):
                continue
            self.journal.append_complete(card, "step.started", step=key, kind=intent.get("kind") or "ticket",
                                ticket=intent.get("ticket") or ticket["id"], round=intent.get("round") or 0,
                                executor=intent.get("executor") or (st.started or {}).get("executor") or "",
                                program=intent.get("program") or (st.started or {}).get("program") or "claude",
                                model=intent.get("model") or (st.started or {}).get("model") or "",
                                provider=intent.get("provider") or (st.started or {}).get("provider") or "",
                                effort=intent.get("effort") or "", routeUrl=intent.get("routeUrl") or "",
                                routeKind=intent.get("routeKind") or "",
                                routeTokenFile=intent.get("routeTokenFile") or "",
                                local=bool(intent.get("local", (st.started or {}).get("provider") == "local")),
                                unit=result.get("unit") or intent.get("unit") or "",
                                session=intent.get("session") or "", worktree=intent.get("worktree") or "",
                                branch=intent.get("branch") or ticket["branch"], stepDir=intent.get("stepDir") or "",
                                reviewTip=intent.get("reviewTip") or "",
                                parentStep=intent.get("parentStep") or "", attempt=intent.get("attempt") or 0,
                                deliveredNoteIds=intent.get("deliveredNoteIds") or [],
                                **({"executorReason": intent["executorReason"]} if intent.get("executorReason") else {}),
                                readDirsProfile=bool(result.get("readDirsProfile")),
                                gitDir=result.get("gitDir") or "", repo=self._repo(card),
                                baseVersion=intent.get("baseVersion", 0),
                                baseRef=intent.get("baseRef", ""), baseCommit=intent.get("baseCommit", ""),
                                # PT-181: runner kroku w pudełku (po nim `_runner_for` sprawdza i zatrzymuje krok).
                                **{k: intent[k] for k in ("runner", "box", "boxSlug", "boxIp", "boxAccess",
                                                         "boxPortOwner") if intent.get(k)})

    def _repo(self, card: str) -> str:
        st = self.journal.state(card)
        return (st.started or {}).get("repo") or ""

    def _poll(self, card: str, step: dict) -> None:
        try:
            self._process_check(card, step)
        except FlowError:
            return
        if step.get("mode") == "interactive":
            self._poll_interactive(card, step)
            return
        name = step.get("step") or ""
        if name in self._box_network_errors:
            self._stop(card, step, "error", self._box_network_errors[name])
            return
        log = os.path.join(step.get("stepDir") or "", "out", "step.log")
        base_wait = bool(baseproposal.pending(self.journal.state(card)))
        if not base_wait and flow_limits.exhausted_log(log, step.get("program") or ""):
            if name not in self.journal.state(card).limits:
                low = self._low_limit(step.get("provider") or "") or {}
                base_tip = self.git.sha(step.get("repo") or self._repo(card),
                                        f"refs/heads/{step.get('branch') or ''}")
                self.journal.append(card, "step.limit", step=name, ticket=step.get("ticket") or "",
                                    provider=step.get("provider") or "", reset=low.get("reset") or "",
                                    resetTs=low.get("resetTs") or 0, remaining=low.get("remaining"),
                                    window=low.get("window") or "", baseTip=base_tip)
            self._stop(card, step, "error", "limit", handoff=True)
            return
        proc = self.procs.get(name)
        exit_code = proc.poll() if proc is not None else None
        if step.get("runner") == boxstep.RUNNER:
            # Usługa kroku przeżywa klienta incus exec; tylko jej stan potwierdza koniec.
            state = self._unit_state(step)
            if state == "unknown":
                return
            alive = state == "active"
            if not alive:
                exit_code = stepstop.read_exit_code(step.get("stepDir") or "")
                if exit_code is None and proc is not None and proc.poll() is None:
                    return                   # klient dopiero tworzy jednostkę; brak kodu nie dowodzi końca
        elif exit_code is not None:
            alive = False
        else:
            state = self._unit_state(step)
            if state == "unknown":
                return                       # bez menedżera nie wiemy — nie zgadujemy końca kroku
            alive = state == "active"
            if not alive and proc is not None:
                try:
                    exit_code = proc.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    return                   # menedżer już nie widzi jednostki, launcher jeszcze się kończy
            elif not alive:
                exit_code = stepstop.read_exit_code(step.get("stepDir") or "")
        if not alive:
            self._finish(card, step, exit_code=exit_code)
            return
        if base_wait:
            return
        try:
            started_at = datetime.fromisoformat(step.get("ts") or "").timestamp()
        except ValueError:
            started_at = self.clock()
        if self.clock() - started_at >= timeout_seconds(self.settings):
            self._stop(card, step, "error", "timeout")
            return
        self._guard(card, step)

    def _poll_interactive(self, card: str, step: dict) -> None:
        """Plik wyniku kończy próbę; po zamknięciu bez pliku użytkownik wybiera dalszy ruch."""
        if step.get("kind") == "caretaker-chat":
            self._poll_caretaker_chat(card, step)
            return
        out = os.path.join(step.get("stepDir") or "", "out", handoff.RESULT_FILE)
        if os.path.isfile(out):
            data = self._result(out)
            if data.get("reason") != "bad-result":
                self._finish(card, step, exit_code=0)
                return
        if step.get("consoleClosed") or self._console_ended(step):
            if not step.get("consoleClosed"):
                close_console(self.journal, card, step.get("step"))
            self._finish(card, step, forced={"result": "stopped", "reason": "interactive-closed",
                                             "question": "Karta Konsoli została zamknięta bez wyniku. Wznowić czy porzucić?"})
            return
        task_file = step.get("taskFile") or ""
        try:
            timed_out = bool(task_file) and time.time() - os.stat(task_file).st_mtime > 30
        except OSError:
            timed_out = False  # plik przejęty przez kartę; czekamy na wynik albo znacznik zamknięcia
        if timed_out:
            if self.task_files.cancel(task_file):
                close_console(self.journal, card, step.get("step"), cancelled=True)
                self._finish(card, step, forced={"result": "stopped", "reason": "interactive-closed",
                                                 "question": "Konsola nie przejęła kroku. Wznowić czy porzucić?"})
                return
        self._guard(card, step, stop_stalled=False)

    def _console_ended(self, step: dict) -> bool:
        """Znacznik lub trwała tożsamość procesu potwierdza koniec, również po restarcie usługi."""
        unit = step.get("consoleUnit") or ""
        if unit and stepstop.unit_state(unit) != "inactive":
            return False  # także nieznany stan jednostki trzyma worktree
        alive = launch.process_alive(step.get("processFile") or "")
        if alive is True:
            return False
        if os.path.isfile(step.get("closeFile") or "") or alive is False:
            return True
        if os.path.exists(step.get("processFile") or ""):
            return False  # nieczytelny zapis procesu nie dowodzi jego końca
        task_file = step.get("taskFile") or ""
        return bool(task_file and not os.path.exists(task_file) and
                    self.clock() - float(step.get("enteredAt") or self.clock()) > 30)

    def _guard(self, card: str, step: dict, *, stop_stalled: bool = True) -> None:
        try:
            self._process_check(card, step)
        except FlowError:
            return
        name = step.get("step") or ""
        now = time.monotonic()
        limit = guard_seconds(self.settings)
        seen = self.watch.get(name)
        if seen is not None and now - seen[2] < min(self.guard_every, limit / 10.0):
            return
        mark = self._progress_mark(step)
        if seen is None:
            self.watch[name] = (mark, now, now)
            return
        if seen[0] != mark:
            self.watch[name] = (mark, now, now)
            activity = (self.journal.state(card).activity.get(name) or step).get("ts")
            if self.clock() - _epoch(activity) >= 60:
                self.journal.append(card, "step.activity", step=name, ticket=step.get("ticket") or "")
            return
        self.watch[name] = (mark, seen[1], now)
        if stop_stalled and now - seen[1] >= limit:
            print(f"tide-daemon: przepływ karty {card}: krok {name} bez postępu od {int(now - seen[1])} s — zatrzymuję",
                  flush=True)
            stalled = datetime.fromtimestamp(self.clock() - (now - seen[1]), timezone.utc).isoformat(timespec="seconds")
            self._stop(card, step, "error", "no-progress", stalled_since=stalled)

    def _guard_card(self, card: str) -> None:
        """Po trzech minutach bez pracy i bez nazwanego powodu czekania zapisuje pojedynczy alarm."""
        st = self.journal.state(card)
        if guard_reason(st) != "" or st.stalled_since or not st.last_activity_ts:
            return
        item = self.store.load().cards.get(card_num(card))
        if item is None or item.column in ("done", "archived") or self.clock() - st.last_activity_ts < 180:
            return
        event = self.journal.append(card, "card.stalled", reason=st.flow_error or "brak kroku i powodu czekania")
        if caretaker.unavailable(self.journal.state(card), item.column):
            self._notices.append({"card": card, "step": f"stalled:{event['seq']}", "ticket": "", "result": "stalled",
                                  "detail": event["reason"]})

    def _progress_mark(self, step: dict) -> tuple:
        """Odcisk postępu: pliki worktree (bez .git) i dziennik sesji kroku."""
        count = size = newest = 0
        for folder, dirs, files in os.walk(step.get("worktree") or "", followlinks=False):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                try:
                    st = os.lstat(os.path.join(folder, name))
                except OSError:
                    continue
                count, size, newest = count + 1, size + st.st_size, max(newest, st.st_mtime_ns)
        log = (stepstop.find_claude_log(self.claude_projects, step.get("session") or "")
               if step.get("program") == "claude" else os.path.join(step.get("stepDir") or "", "out", "step.log"))
        try:
            log_st = os.stat(log) if log else None
        except OSError:
            log_st = None
        cpu = self.unit_cpu(step.get("unit") or "") if self.unit_cpu else None
        return count, size, newest, (log_st.st_size, log_st.st_mtime_ns) if log_st else None, cpu

    def _handoff_note(self, card: str, step: dict, stopped, reason: str) -> str:
        """Notka z zatrzymania: dane techniczne i ograniczony diff, bez ponownego użycia modelu."""
        diff = ""
        if stopped.committed and stopped.commit:
            shown = self.git.run(step.get("repo") or self._repo(card), "show", "--format=", "--no-ext-diff",
                                 "--no-textconv", "--no-renames", stopped.commit)
            if shown.ok:
                diff = shown.out[:HANDOFF_DIFF_MAX]
                if len(shown.out) > HANDOFF_DIFF_MAX:
                    diff += "\n… diff przycięty do 64 KiB\n"
        lines = [f"# Przekazanie kroku {step.get('step') or ''}", "",
                 f"Ticket: {step.get('ticket') or '(planowanie)'}", f"Powód: {reason}",
                 f"Wykonawca: {step.get('executor') or ''} ({step.get('provider') or ''})",
                 f"Worktree: {step.get('worktree') or ''}",
                 f"Commit WIP: {stopped.commit if stopped.committed else '(brak zmian albo błąd)'}",
                 f"Liczba zmienionych plików: {stopped.files}",
                 f"Błąd zapisu WIP: {stopped.error or '(brak)'}", "", "## Ostatnie wiadomości"]
        for message in stopped.messages[-3:]:
            role = message.get("role") or "?"
            body = str(message.get("text") or "")[:4000]
            lines += [f"### {role}", "", body, ""]
        lines += ["## Uwagi techniczne", *[str(note)[:1000] for note in stopped.notes[:20]],
                  "", "## Diff WIP", "", "```diff", diff or "(brak nowego commita WIP)", "```", ""]
        relative = os.path.join("steps", step.get("step") or "", "handoff.md")
        self.journal.write_file(card, relative, "\n".join(lines).encode("utf-8")[:OUTPUT_MAX])
        return os.path.join(self.store.root, self.journal.rel_dir(card), relative)

    def _recovered_wip_snapshot(self, card: str, step: dict) -> stepstop.Snapshot | None:
        """Odtwarza commit WIP zapisany przed awarią, lecz jeszcze nieutrwalony w dzienniku."""
        st = self.journal.state(card)
        name = step.get("step") or ""
        signal_event = st.transfer_requests.get(name) or st.limits.get(name) or {}
        base = signal_event.get("baseTip") or ""
        if not base:
            return None
        repo = step.get("repo") or self._repo(card)
        branch = step.get("branch") or ""
        worktree = step.get("worktree") or ""
        try:
            tip = self.git.clean_tip(repo, branch, worktree)
        except FlowGitError:
            return None
        if tip == base:
            return None
        trusted = stepstop.StepGit(repo, step.get("gitDir") or "", worktree,
                                   step.get("stepDir") or "")
        head = trusted.run("symbolic-ref", "--quiet", "HEAD")
        message = self.git.run(repo, "show", "-s", "--format=%s", tip)
        ancestor = self.git.run(repo, "merge-base", "--is-ancestor", base, tip)
        files = self.git.run(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", tip)
        if not head.ok or head.out.strip() != f"refs/heads/{branch}" or not message.ok or \
                message.out.strip() != stepstop.WIP_MESSAGE or not ancestor.ok or not files.ok:
            return None
        count = len([path for path in files.out.split("\0") if path])
        return (stepstop.Snapshot(committed=True, commit=tip, files=count,
                                  notes=["odtworzono commit WIP po restarcie usługi"])
                if count else None)

    def _stop(self, card: str, step: dict, result: str, reason: str, stalled_since: str = "",
              *, handoff: bool = False) -> bool:
        try:
            self._process_check(card, step)
        except FlowError:
            return False
        if step.get("kind") == "caretaker-chat":
            if not self._stop_caretaker_chat(step):
                return False
            self._finish_caretaker_chat(card, step, reason)
            return True
        if handoff and not self._kill(step):
            return False  # sprawdzenie czystości WIP dopiero po potwierdzonym końcu programu
        recovered = self._recovered_wip_snapshot(card, step) if handoff else None
        stopped = stepstop.stop_step(self.procs.get(step.get("step") or ""), unit=step.get("unit") or "",
                                     worktree=step.get("worktree") or "", step_dir=step.get("stepDir") or "",
                                     repo=step.get("repo") or self._repo(card), git_dir=step.get("gitDir") or "",
                                     branch=step.get("branch") or "", tool=step.get("program") or "claude",
                                     session_id=step.get("session") or "", claude_projects=self.claude_projects,
                                     codex_roots=self.codex_roots,
                                     grace=self.grace, recovered_snapshot=recovered,
                                     commit_wip=step.get("kind") != caretaker.KIND,
                                     kill=lambda: self._kill(step))
        wip = {"stopped": stopped.stopped, "committed": stopped.committed, "commit": stopped.commit,
               "files": stopped.files, "error": stopped.error, "notes": stopped.notes}
        if not stopped.stopped:
            print(f"tide-daemon: przepływ karty {card}: kroku {step.get('step')} nie dało się zatrzymać", flush=True)
            return False                         # następne sprawdzenie spróbuje znowu; worktree nietknięty
        if handoff:
            wip["handoff"] = self._handoff_note(card, step, stopped, reason)
        self._finish(card, step, forced={"result": result, "reason": reason, "stalledSince": stalled_since}, wip=wip,
                     messages=stopped.messages, exit_code=stopped.exit_code)
        return True

    def _release_box(self, card: str, step: dict) -> None:
        if step.get("runner") == boxstep.RUNNER:
            try:
                boxstep.release(self, card, step)
            except Exception as exc:   # Trwały wynik pozwala ponowić sprzątanie w recover_network.
                print(f"tide-daemon: przepływ karty {card}: zwolnienie pudełka po {step.get('step')}: {exc!r}", flush=True)

    def _finish(self, card: str, step: dict, forced: dict | None = None, wip: dict | None = None,
                messages=None, exit_code=None) -> None:
        name = step.get("step") or ""
        if name in self.journal.state(card).finished:
            self._release_box(card, step)
            return
        if step.get("kind") == "caretaker-chat":
            self._finish_caretaker_chat(card, step, (forced or {}).get("reason") or "")
            return
        if step.get("kind") == caretaker.KIND:
            self._finish_caretaker(card, step, forced, exit_code)
            return
        out = os.path.join(step.get("stepDir") or "", "out")
        data = forced or self._result(os.path.join(out, handoff.RESULT_FILE))
        if forced is None and exit_code != 0:
            data = {**data, "result": "error", "reason": "lost" if exit_code is None else "exit"}
        if forced is None and data.get("result") == "error" and _login_expired(os.path.join(out, "step.log")):
            # K-35, 03.11 07:58: wygasłe logowanie wyglądało jak zwykły błąd „exit” i zużywało ponowienia.
            data = {**data, "reason": "login"}
        if forced is None and step.get("program") == "claude" and data.get("result") == "error":
            denied = self._claude_denied_command(os.path.join(out, "step.log"))
            if denied:
                data = {"result": "denied", "command": denied, "notes": data.get("notes") or []}
        proxy_note = ""
        if forced is None and step.get("runner") == boxstep.RUNNER:
            # PT-181: odmowa proxy, na której krok się zatrzymał (pytanie albo błąd wskazujące domenę z dziennika
            # odmów TEGO kroku) → `denied` z `network: <domena>`; inne odmowy tylko w notce.
            data, proxy_note = boxstep.after_step(step, data, out)
        if step.get("runner") == boxstep.RUNNER:
            from ..box import refguard
            guarded = refguard.finish(step.get("repo") or self._repo(card), card, name,
                                      branch=step.get("branch") or "")
            if guarded:
                self.journal.append(card, "refs.guard", step=name, ticket=step.get("ticket") or "", **guarded)
                data = {"result": "error", "reason": "refs.guard", "notes": data.get("notes") or []}
        verdict = None
        if step.get("kind") == "review" and data.get("result") == "done":
            verdict = self._review_result(os.path.join(out, handoff.RESULT_FILE))
            if verdict is None:
                data = {"result": "error", "reason": "bad-review"}
            else:
                self._move_reviewer_files(step, step.get("repo") or self._repo(card))
                try:
                    tip = self.git.clean_tip(step.get("repo") or self._repo(card),
                                             step.get("branch") or "", step.get("worktree") or "", untracked=False)
                except FlowGitError:
                    tip = ""
                if not tip or tip != step.get("reviewTip"):
                    verdict = None
                    data = {"result": "error", "reason": "review-changed-ticket"}
        merged_tip = ""
        if step.get("kind") == "conflict" and data.get("result") == "done":
            info = ticket_of(self.journal.state(card), step.get("ticket") or "")
            try:
                merged_tip = self.git.merged_tip(step.get("repo") or self._repo(card),
                                                 info["cardBranch"], info["branch"], info["cardWorktree"],
                                                 step.get("reviewTip") or "",
                                                 (self.journal.state(card).intents.get(
                                                     ("merge", step.get("ticket") or "")) or {}).get("cardTip") or "")
            except FlowGitError as exc:
                data = {"result": "error", "reason": f"konflikt nierozwiązany: {exc}"}
        if step.get("kind") == "close" and data.get("result") == "done":
            summary = os.path.join(out, handoff.SUMMARY_FILE)
            try:
                valid = not os.path.islink(summary) and os.path.getsize(summary) <= OUTPUT_MAX
                with open(summary, encoding="utf-8") as fh:
                    text = fh.read(OUTPUT_MAX)
                valid = valid and bool(text.strip()) and (
                    (self.journal.state(card).started or {}).get("check") != "manual" or
                    "Co przetestować ręcznie i jak" in text)
            except (OSError, UnicodeError):
                valid = False
            if not valid:
                data = {"result": "error", "reason": "brak poprawnego podsumowania karty"}
        kept = []
        for entry in sorted(os.listdir(out)) if os.path.isdir(out) else []:
            path = os.path.join(out, entry)
            if (entry == handoff.RESULT_FILE or entry.endswith(".md")) and os.path.isfile(path) \
                    and not os.path.islink(path) and os.path.getsize(path) <= OUTPUT_MAX:
                with open(path, "rb") as fh:
                    self.journal.write_file(card, os.path.join("steps", name, entry), fh.read())
                kept.append(entry)
        if proxy_note:
            # Notka usługi (nie z katalogu kroku, który pudełko może zmienić).
            self.journal.write_file(card, os.path.join("steps", name, boxstep.NOTE_FILE), proxy_note.encode("utf-8"))
            if boxstep.NOTE_FILE not in kept:
                kept.append(boxstep.NOTE_FILE)
            data = {**data, "notes": [*(n for n in data.get("notes") or [] if n != boxstep.NOTE_FILE),
                                      boxstep.NOTE_FILE]}
        snapshot_dir = ""
        if data.get("result") == "done" and step.get("kind") in ("planning", "plan-fix", "plan-import", "plan-revise", "plan-amend"):
            try:
                snapshot_dir = self._freeze_planning_output(card, name, out)
            except (OSError, UnicodeError, ValueError) as exc:
                data = {"result": "error", "reason": f"niepoprawne artefakty planowania: {exc}"}
        extra = {k: v for k, v in (("wip", wip), ("messages", messages)) if v}
        if data.get("result") == "question" and data.get("baseProposal") is not None:
            extra["baseProposal"] = baseproposal.validate(data["baseProposal"], self.git, self._repo(card))
        if data.get("waitFor"):
            extra["waitFor"] = data["waitFor"]
        remembered = False
        auto_allow = False
        broken_mcp = self._codex_broken_mcp(step, data)
        if data.get("result") == "denied":
            from ..board import chain
            from ..board.commands import MAX_COMMAND, is_forbidden, outside_risky
            _, project = self._project(card)
            command = data.get("command") or ""
            from ..board.commands import sandbox_grant
            data["canAllow"] = sandbox_grant(command, project.denied_commands) is not None
            from . import envconsent
            network = "" if data.get("outside") else envconsent.network_domain(command)
            if network:
                # PT-172: odmowa proxy to pytanie o domenę (dopisz do profilu karty i przebuduj / wznów bez), nie zgoda
                # na polecenie — bez rozkładu łańcucha i bez automatycznej odmowy.
                extra["network"] = network
                data["canAllow"] = False
            elif data.get("outside"):
                from ..board.commands import outside_allowed, outside_pattern
                extra["outside"] = True
                extra["pattern"] = outside_pattern(command)
                roots = (step.get("worktree") or "", project.repo, step.get("stepDir") or "")
                data["canAllow"] = step.get("runner") != boxstep.RUNNER and outside_allowed(command, project.denied_commands, roots)
                remembered = data["canAllow"] and consent.automatic(self, card, step, command)
            else:
                # PT-124: łańcuch rozłożony na pojedyncze polecenia z flagami i powodem blokady (dla okna decyzji).
                extra["commands"] = chain.describe(command, denied=project.denied_commands,
                                                   step_dir=step.get("stepDir") or "") if str(command).strip() else []
                if data["canAllow"] and self._auto_consent():
                    auto_allow = True   # w piaskownicy: zapisy tylko w worktree, bez sieci — wystarczy brak zakazu
        if data.get("result") in ("error", "stopped"):
            # Czyja wina (PT-125): z powodu wyniku i dziennika tego kroku, w chwili wyniku — fold() nie czyta dysku.
            log = os.path.join(out, "step.log")
            found = faults.classify(data.get("reason") or "", faults.read_log(log), out_dir=out,
                                    tool=step.get("program") or "", log_path=log)
            extra.update(fault=found["fault"], faultCause=found["cause"])
        self.journal.append(card, "step.finished", step=name, ticket=step.get("ticket") or "",
                            result=data.get("result"), reason=data.get("reason") or "",
                            question=data.get("question") or "", command=data.get("command") or "",
                            options=data.get("options") or [],
                            canAllow=data.get("canAllow", True), stalledSince=data.get("stalledSince") or "",
                            notes=[n for n in data.get("notes") or [] if n in kept], kept=kept,
                            exit=exit_code, snapshotDir=snapshot_dir, **extra)
        waiting = bool(data.get("waitFor")) and self._declared_wait(card, name, step, data)
        if data.get("result") == "question" and not waiting:
            self._env_question(card, step, data)
        if broken_mcp:
            # Błąd konfiguracji Codexa po stronie Tide: zapamiętany serwer jest odtąd pomijany, krok rusza sam.
            self.journal.append(card, "step.decision", step=name, ticket=step.get("ticket") or "",
                                kind=step.get("kind") or "", choice="retry", answer="", command="", executor="",
                                auto=True)
        if data.get("result") == "denied" and not data.get("outside") and not extra.get("network"):
            # Prośbie o uruchomienie poza piaskownicą Tide sam nie odmawia (K-16/LEGAL-66: odmowa bez wyjścia).
            listed = extra.get("commands") or []
            if listed and not data.get("canAllow", True) and not any(item["canAllow"] for item in listed):
                self._auto_deny(card, name, step, data.get("command") or "")
            elif str(data.get("command") or "").strip() and not listed:
                # Same odczyty własnego <krok>/in i <krok>/out — zawsze dozwolone, nie ma o co pytać użytkownika.
                self._auto_deny(card, name, step, data.get("command") or "", note=STEP_READ_NOTE)
            elif auto_allow and not self._denied_before(card, step.get("ticket") or "", data.get("command") or ""):
                # Automatyczna zgoda na pojedyncze, bezpieczne polecenie w piaskownicy (03.11: „nie mam ochoty co
                # 2 minuty klikać zgody”). Piaskownica dalej chroni system; zgoda trafia do listy projektu.
                try:
                    self.resolve_step(card, {"choice": "allow", "step": name, "auto": True})
                except FlowError:
                    pass
        elif remembered:
            # Polecenie zapamiętane przez użytkownika albo bezpieczne przy automatycznych zgodach — bez pytania.
            self.journal.append(card, "step.decision", step=name, ticket=step.get("ticket") or "",
                                kind=step.get("kind") or "", choice="run", answer="",
                                command=data.get("command") or "", executor="", auto=True)
        # Agent konfliktu już scalił w worktree karty. Utrwalamy potwierdzony stan także podczas
        # decyzji o bazie; blokada nadal odracza sprzątanie i kolejne scalenia w _tick_card/_act.
        if merged_tip:
            self.journal.append(card, "result", action="merge", key=step.get("ticket") or "",
                                ok=True, commit=merged_tip, resolved=True)
        if verdict is not None:
            self.journal.append(card, "review.verdict", step=name, ticket=step.get("ticket") or "",
                                round=step.get("round") or 1, reviewer=step.get("executor") or "",
                                baseVersion=step.get("baseVersion", 0), reviewTip=step.get("reviewTip", ""), **verdict)
        self._queue_notices(card)
        self._release_local(name)
        self._release_box(card, step)
        self.procs.pop(name, None)
        self.watch.pop(name, None)
        shutil.rmtree(os.path.join(self.tmp_root, f"tide-step-{name}"), ignore_errors=True)

    def _freeze_planning_output(self, card: str, step: str, out: str) -> str:
        """Migawka artefaktów z chwili wyniku, poza zapisywalnym katalogiem Konsoli."""
        relative = os.path.join("steps", step, "proposal")
        planartifacts.save(self.journal, card, relative, planartifacts.read(out))
        return os.path.join(self.store.root, self.journal.rel_dir(card), relative)

    def _queue_notices(self, card: str) -> None:
        """Zdarzenie przed wysłaniem: po restarcie pytanie nie daje drugiego powiadomienia."""
        st = self.journal.state(card)
        if st.card_finished:
            return
        withheld = self._caretaker_withheld(card, st)
        for name, done in st.finished.items():
            step = st.steps.get(name) or {}
            result = done.get("result")
            if name in st.caretakers or name in withheld:
                continue        # krok opiekuna i problem, który sprawdza (albo zaraz sprawdzi) opiekun — bez powiadomienia
            if name in st.step_notices or name in st.step_decisions or st.card_finished or not (
                    result in ("question", "denied") or
                    result == "error" and (int(step.get("attempt") or 0) >= 1 or done.get("reason") == "limit" or
                                           caretaker.handed_repeat(st, name))):
                continue
            detail = done.get("question") if result == "question" else done.get("command") if result == "denied" \
                else done.get("reason") or ""
            self.journal.append(card, "step.notice", step=name, ticket=step.get("ticket") or "",
                                result=result)
            st.step_notices.add(name)
            self._notices.append({"card": card, "step": name, "ticket": step.get("ticket") or "",
                                  "result": result, "detail": detail or ""})
        st = self.journal.state(card)

        def once(key: str, result: str, detail: str = "") -> None:
            if key in st.flow_notices:
                return
            self.journal.append(card, "flow.notice", key=key, result=result)
            st.flow_notices.add(key)
            self._notices.append({"card": card, "step": key, "ticket": "", "result": result,
                                  "detail": detail})

        for decision in decisions.build_decisions(st):
            if decision["kind"] == "caretaker" or (
                    decision["kind"] in ("plan-revision-failed", "plan-fix-failed") and decision.get("caretaker")):
                once(decision["id"], "caretaker", decision["happened"])

        if st.plan_revision and st.plan_revision.get("pending") and st.plan_reviewed:
            once(f"revision:{st.plan_revision.get('seq')}", "plan", "Różnica planu czeka na zatwierdzenie")
        elif st.plan_proposed and st.plan_reviewed and not st.plan_approved:
            once(f"plan:{st.plan_proposed.get('seq')}", "plan", "Plan czeka na zatwierdzenie")
        legacy_plan_done = st.plan_finished and not st.plan_revision and not any(
            step.get("kind") == "close" for step in st.steps.values())
        if (st.closed or legacy_plan_done) and not st.running():
            once(f"review:{st.closed.get('seq') if st.closed else st.plan_approved.get('seq') if st.plan_approved else st.last_seq}",
                 "review", "Karta czeka na sprawdzenie")
        elif st.moved_to == "review" and not st.card_finished and not st.running():
            moved = next((e for e in reversed(self.journal.read(card)) if e.get("type") == "card.moved" and
                          e.get("to") == "review"), {})
            once(f"review:{moved.get('seq') or st.last_seq}", "review", "Karta czeka na sprawdzenie")
        for wait in st.waiting.values():
            if wait.get("kind") == caretaker.KIND:
                continue      # opiekun w kolejce do dostawcy: problem nadal jego, powiadamia dopiero eskalacja
            if "limit" in (wait.get("reason") or "").lower():
                once(f"limit:{wait.get('seq')}", "limit", wait.get("reason") or "Limit dostawcy")

    # --- opiekun karty (PT-143) --------------------------------------------------------------------

    def _card_column(self, card: str) -> str:
        try:
            item = self.store.load().cards.get(card_num(card))
        except (BoardError, OSError, ValueError):
            return ""
        return item.column if item is not None else ""

    def _caretaker_withheld(self, card: str, st: FlowState) -> set:
        """Kroki bez powiadomienia o decyzji: sprawdza je opiekun albo zaraz zacznie (problem jeszcze nieoddany)."""
        st = self._caretaker_state(st)
        held = {name for name in st.caretaking if caretaker.hidden(st, name)}
        if st.started is None or caretaker.unavailable(st, self._card_column(card), self._caretaker_setup(st)):
            return held
        for problem in caretaker.problems(st):
            policy = caretaker.PROBLEM_POLICIES.get(problem["trigger"]) or {}
            if not policy.get("visible") and not caretaker.owned(st, problem["id"])["handed"]:
                held.add(problem["step"])
        return held

    def _caretake(self, card: str, st: FlowState) -> None:
        """Jedyne wejście opiekuna w silniku: dokończenie zapisanych kroków opiekuna (wynik → akcja z rejestru,
        `caretaker.started` bez startu po restarcie → start bez drugiej kopii) i start opiekuna przy nowych
        problemach. Krok opiekuna kończy się istniejącym `_poll`/`_finish` (rodzaj `caretaker`)."""
        if st.started is None or st.card_finished:
            return
        for name, run in list(st.caretaker_runs.items()):
            current = self.journal.state(card)
            if name in current.caretaker_outcomes:
                continue
            if name in current.finished:
                self._caretaker_apply(card, current, run, current.finished[name])
            elif name not in current.caretakers:
                self._caretaker_launch(card, current, run)
        st = self._caretaker_state(self.journal.state(card))
        conf = self._caretaker_setup(st)
        if caretaker.unavailable(st, self._card_column(card), conf):
            return
        limit = conf["limit"]
        for problem in caretaker.problems(st):
            own = caretaker.owned(st, problem["id"])
            if own["open"] or own["handed"] or any(s.get("ticket") == problem["ticket"] for s in st.running()):
                continue
            if own["count"] >= limit:
                # Limit działań (PT-150): kolejny krok opiekuna nie startuje, problem wraca do użytkownika ze
                # streszczeniem prób (`tried[]` w decyzji z `annotate`).
                last = own["runs"][-1]
                self.journal.append(card, caretaker.ESCALATED, problem=problem["id"], step=last.get("step") or "",
                                    target=problem["step"], ticket=problem["ticket"], action="",
                                    summary=caretaker.limit_summary(st, problem["id"], limit),
                                    recommendation="Sprawdź podsumowanie prób i wybierz dalsze działanie w decyzji.",
                                    optionId="", failed=True, limit=True, count=own["count"],
                                    error=f"wyczerpany limit działań opiekuna ({limit})", code=caretaker.LIMIT)
                st = self._caretaker_state(self.journal.state(card))
                continue
            step_id = f"{card}-care-{len(st.caretaker_runs) + 1}"
            run = self.journal.append(card, caretaker.STARTED, problem=problem["id"],
                                      key=f"caretaker:{problem['id']}:{own['next']}", n=own["next"], step=step_id,
                                      target=problem["step"], root=problem["root"], ticket=problem["ticket"],
                                      trigger=problem["trigger"], triggers=problem["triggers"],
                                      decision=problem.get("decision") or "", watermark=st.last_seq)
            self._caretaker_launch(card, self.journal.state(card), run)
            st = self._caretaker_state(self.journal.state(card))

    def _caretaker_state(self, st: FlowState) -> FlowState:
        """Migawka ustawień i zegara także dla wyzwalaczy i sprawdzania aktualności, nie tylko widoków."""
        st.caretaker_setup = self._caretaker_setup(st)
        return st

    def _caretaker_setup(self, st: FlowState, catalog=None) -> dict:
        """Opiekun przy tej karcie według `cardflow.json` i katalogu wykonawców (`caretaker.setup`, PT-151): jedno
        miejsce wyboru wykonawcy z regułą „Tylko lokalnie”, wyłącznik i limit działań."""
        return {**caretaker.setup(st, self._settings_data(), catalog if catalog is not None else self._catalog_now()),
                "now": self.clock()}

    def _caretaker_problem(self, st: FlowState, run: dict) -> dict:
        st = self._caretaker_state(st)
        found = next((p for p in caretaker.problems(st) if p["id"] == run.get("problem")), None)
        return found or {"id": run.get("problem") or "", "trigger": run.get("trigger") or "",
                         "triggers": [run.get("trigger") or ""], "step": run.get("target") or "",
                         "root": run.get("root") or "", "ticket": run.get("ticket") or "", "options": [],
                         "decision": run.get("decision") or ""}

    def _caretaker_record(self, card: str, run: dict, action: str, params: dict, *, ok: bool, error: str = "",
                          code: str = "", label: str = "", retried: str = "", stale: bool = False,
                          detail: str = "") -> None:
        """Wynik akcji opiekuna innej niż oddanie decyzji (`caretaker.action`); nieudana akcja nie oddaje problemu —
        kolejny krok opiekuna rusza aż do limitu działań (PT-150). `target` to zawsze krok problemu (po nim stan
        zdejmuje „opiekun sprawdza”), krok wskazany w akcji jest w `retried`."""
        self.journal.append(card, caretaker.ACTION, problem=run.get("problem") or "", step=run.get("step") or "",
                            target=run.get("target") or "", ticket=run.get("ticket") or "", action=action,
                            params=params, ok=ok, error=error[:caretaker.REASON_MAX], code=code, label=label,
                            retried=retried, stale=stale, mode=run.get("mode") or "background",
                            **({"detail": detail[:caretaker.REASON_MAX]} if detail else {}))

    def _caretaker_fail(self, card: str, run: dict, code: str, error: str, *, action: str = "",
                        summary: str = "", recommendation: str = "") -> None:
        """Nieudany wynik kroku opiekuna (zły plik wyniku, pytanie, akcja spoza rejestru, zapis poza `out`, krok
        zatrzymany albo niewystartowany): problem od razu wraca do użytkownika (PT-143)."""
        self.journal.append(card, caretaker.ESCALATED, problem=run.get("problem") or "", step=run.get("step") or "",
                            target=run.get("target") or "", ticket=run.get("ticket") or "", action=action,
                            summary=summary, recommendation=recommendation or
                            "Sprawdź błąd wyniku opiekuna i wybierz dalsze działanie w decyzji.", optionId="", failed=True,
                            error=error[:caretaker.REASON_MAX], code=code, mode=run.get("mode") or "background")

    def _caretaker_launch(self, card: str, st: FlowState, run: dict) -> None:
        st = self._caretaker_state(st)
        name = run.get("step") or ""
        target = st.steps.get(run.get("target") or "") or {}
        started = st.results.get(("step-start", name))
        if started is None and ("step-start", name) in st.intents:
            return              # start zaczęty przed restartem — rozstrzyga `_reconcile`, nigdy druga kopia
        if started is not None and started.get("ok"):
            return              # wynik startu jest, `step.started` dopisze `_recover_started_steps`
        if started is not None and not started.get("ok"):
            self._caretaker_fail(card, run, caretaker.NOT_STARTED,
                                 f"krok opiekuna nie wystartował: {started.get('error') or 'błąd startu'}")
            return
        # Problem bez kroku źródłowego (`flow-error:<etap>`, `stalled:card`) jest pełnoprawny: wystarcza wpis
        # w `TRIGGERS`; aktualność zawsze rozstrzyga `problems()`, a brakujący krok — tylko gdy problem go ma.
        if run.get("target") and not target:
            self._caretaker_fail(card, run, caretaker.NOT_STARTED, "brak kroku problemu w dzienniku karty")
            return
        # Nieaktualny przed startem (PT-150; np. decyzja użytkownika, gdy opiekun czekał na slot): bez kroku opiekuna
        # i bez eskalacji — problem rozstrzygnięto inaczej, a działanie nie liczy się do limitu.
        reason = caretaker.stale(st, run, self.journal.read(card))
        if reason:
            self._caretaker_record(card, run, "", {}, ok=False, error=caretaker.STALE, code=caretaker.STALE,
                                   stale=True, detail=reason)
            return
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        conf = self._caretaker_setup(st, catalog)
        if conf["reason"]:
            # PT-151: opiekun wyłączony albo karta „Tylko lokalnie” bez wykonawcy lokalnego, zanim krok ruszył
            # (np. czekał na slot) — bez kroku i bez eskalacji, działanie się nie liczy; decyzja wraca jak dziś
            # (z dopiskiem przy braku wykonawcy lokalnego).
            self._caretaker_record(card, run, "", {}, ok=False, error=caretaker.STALE, code=caretaker.STALE,
                                   stale=True, detail=conf["reason"])
            return
        executor = conf["executor"]
        try:
            if not executor:
                raise FlowError("unknown-executor", "brak wykonawcy opiekuna u preferowanego dostawcy karty")
            try:
                selected = execution.resolve(executor, catalog, config=cfg, probe=self.model_probe, which=self.which)
            except execution.ExecutionError as exc:
                raise FlowError(exc.code, str(exc)) from None
            if caretaker.local_only(st) and not selected.local:
                # PT-151: kontekst karty „Tylko lokalnie” nigdy nie trafia do chmury (druga linia obrony za
                # `caretaker.executor_name`).
                raise FlowError("local-only", "wykonawca opiekuna karty „Tylko lokalnie” musi być lokalny")
            item, project = self._project(card)
            info = ticket_of(st, run.get("ticket") or "")
            # Bez kroku i bez ticketu — katalog roboczy i gałąź karty.
            card_level = not target and not run.get("ticket")
            worktree = target.get("worktree") or (info["cardWorktree"] if card_level else info["worktree"])
            branch = target.get("branch") or (info["cardBranch"] if card_level else info["branch"])
            if not worktree:
                raise FlowError("no-worktree", "brak katalogu roboczego karty dla opiekuna")
            problem = self._caretaker_problem(st, run)

            def make_prompt(step_dir: str) -> str:
                # Kontekst (PT-153) powstaje przy samym starcie, ze stanu dziennika z tej chwili.
                events = self.journal.read(card)
                now = fold(events)
                context_dir = os.path.join(step_dir, "in", caretakerctx.CONTEXT_DIR)
                files = caretakerctx.build(problem, now, events, card=card, worktree=worktree,
                                           context_dir=context_dir,
                                           card_dir=os.path.join(self.journal.root, self.journal.rel_dir(card)),
                                           step=name, include_text=selected.tool != "claude")
                return caretaker.brief(problem, now, card=card, out_dir=os.path.join(step_dir, "out"),
                                       worktree=worktree, commands=selected.tool == "claude", context=files,
                                       context_dir=context_dir, limit=conf["limit"])

            self._launch(item, project, selected, run.get("ticket") or "", {"branch": info["cardBranch"], "worktree": info["cardWorktree"]},
                         {"branch": branch, "worktree": worktree}, name, [],
                         kind=caretaker.KIND, make_prompt=make_prompt)
        except FlowDeferred:
            return              # czeka na miejsce u dostawcy; `caretaker.started` zostaje, start w kolejnym przebiegu
        except FlowError as exc:
            self._caretaker_fail(card, run, caretaker.NOT_STARTED, f"nie udało się uruchomić opiekuna: {exc}")

    def _caretaker_apply(self, card: str, st: FlowState, run: dict, done: dict) -> None:
        outcome = done.get("outcome") or {"ok": False, "code": caretaker.BAD_RESULT, "error": "brak wyniku opiekuna"}
        action = caretaker.ACTIONS.get(outcome.get("action") or "") if outcome.get("ok") else None
        step = run.get("step") or ""
        if ("caretaker-action", step) not in st.intents:
            # Nieaktualność przed każdym skutkiem (PT-150), także przed oddaniem nieudanego wyniku: problem
            # rozstrzygnięty w czasie pracy opiekuna (np. decyzją użytkownika) nie wraca do użytkownika drugi raz.
            # Wznowiony w tym czasie krok mógł zmienić worktree — powód strażnika zostaje w `detail`.
            events = self.journal.read(card)
            reason = caretaker.stale(self._caretaker_state(fold(events)), run, events)
            if reason:
                if outcome.get("code") == guard.WROTE:
                    reason += f"; strażnik: {outcome.get('error') or ''}"
                self._caretaker_record(card, run, outcome.get("action") or "", outcome.get("params") or {},
                                       ok=False, error=caretaker.STALE, code=caretaker.STALE, stale=True,
                                       detail=reason)
                return
        if action is None:
            self._caretaker_fail(card, run, outcome.get("code") or caretaker.BAD_ACTION,
                                 outcome.get("error") or "nieudane działanie opiekuna",
                                 action=outcome.get("action") or "", summary=outcome.get("summary") or "")
            return
        if outcome.get("report") and not any(e.get("type") == "caretaker.reported" and e.get("step") == step
                                              for e in self.journal.read(card)):
            problem = self._caretaker_problem(st, run)
            reason = faults.mask((outcome.get("params") or {}).get("reason") or
                                 (outcome.get("params") or {}).get("summary") or outcome["report"]["description"])
            try:
                made = self._save_fault_report(card, st, run.get("target") or "",
                                               outcome["report"]["description"], problem=problem,
                                               occurrence_id=json.dumps(["caretaker", card, step]))
                self.journal.append(card, "caretaker.reported", step=step, target=run.get("target") or "",
                                    problem=run.get("problem") or "", ok=True, reported=made["reported"],
                                    signature=made["signature"], reason=reason)
            except FlowError as exc:
                self.journal.append(card, "caretaker.reported", step=step, target=run.get("target") or "",
                                    problem=run.get("problem") or "", ok=False, code=exc.code,
                                    error=faults.mask(str(exc)), reason=reason)
        self._caretaker_act(card, run, outcome["action"], outcome.get("params") or {})

    def _caretaker_act(self, card: str, run: dict, name: str, params: dict) -> None:
        """Wspólna ścieżka akcji opiekuna (PT-150), także po restarcie (`_reconcile`):
        1. akcja nieaktualna (`caretaker.stale`) → `caretaker.action` z `ok: false, error: "stale"`, nic nie jest
           wykonane, działanie nie liczy się do limitu; po zaczętym zamiarze nie sprawdzamy — dokończenie,
        2. `escalate` — jedno zdarzenie `caretaker.escalated` (samo jest wynikiem, bez pary),
        3. inne akcje — para „zamiar → wynik” `caretaker-action` z kluczem kroku opiekuna (`_act`): zapisany wynik
           nie wykonuje akcji drugi raz, a `apply` jest bezpieczne do powtórzenia między zamiarem a wynikiem; potem
           `caretaker.action` z wynikiem (`{failed, error}` z `apply` — nieudane działanie, liczy się do limitu)."""
        events = self.journal.read(card)
        st = self._caretaker_state(fold(events))
        step = run.get("step") or ""
        if step in st.caretaker_outcomes:
            return
        spec = caretaker.ACTIONS[name]
        begun = ("caretaker-action", step) in st.intents
        if not begun:
            reason = caretaker.stale(st, run, events)
            if reason:
                self._caretaker_record(card, run, name, params, ok=False, error=caretaker.STALE,
                                       code=caretaker.STALE, stale=True, detail=reason)
                return
        port, problem = _CaretakerPort(self, card, run), self._caretaker_problem(st, run)
        if not begun and name not in caretaker.allowed_actions(problem):
            self._caretaker_fail(card, run, caretaker.BAD_ACTION,
                                 f"akcja {name} niedozwolona przy tej decyzji", action=name,
                                 recommendation="Rozstrzygnij decyzję samodzielnie; opiekun może tylko dopisać rekomendację.")
            return
        if name == "escalate":
            spec.apply(port, problem, params)
            return
        try:
            def apply():
                if run.get("mode") == "chat":
                    wrote = self._guard_verdict(card, run.get("chat") or "")
                    if wrote:
                        return {"failed": guard.WROTE, "error": wrote}
                return spec.apply(port, problem, params) or {}
            made = self._act(card, "caretaker-action", step, apply,
                             problem=run.get("problem") or "", caretakerAction=name, params=params)
        except FlowError as exc:
            self._caretaker_record(card, run, name, params, ok=False, error=str(exc), code="caretaker-action")
            return
        failed = made.get("failed") or ""
        if failed and spec.failure_recommendation:
            self._caretaker_fail(card, run, failed, made.get("error") or "nieudane działanie", action=name,
                                 recommendation=spec.failure_recommendation)
            return
        self._caretaker_record(card, run, name, params, ok=not failed, error=made.get("error") or "",
                               code=failed, label=made.get("label") or "", retried=made.get("target") or "")

    def _retry_stage(self, card: str, run: dict, problem: dict) -> dict:
        """Ponowienie etapu karty od razu, raz (akcja opiekuna `retry-stage`, PT-147): `stage.retry` (zapis opiekuna,
        pole `caretaker`) i jeden przebieg `_advance_card` — ta sama ścieżka co zwykły przebieg, z parą akcji
        opiekuna wyłączoną z „zaczętych akcji karty”. Wynik:
        - błąd Tide tego etapu (przy `stalled`: dowolnego etapu) → `stage-failed` (zapisuje zwykły `_flow_error`,
          ten sam etap i tekst nie daje nowego zdarzenia), etap bez zmian (poza pracą opiekuna) →
          `stage-no-change`; oba to nieudane działania liczone do limitu,
        - etapy przebyte bez błędu zdejmują własny błąd, jak w zwykłym przebiegu, żeby rozwiązany problem
          nie uruchamiał kolejnych kroków opiekuna,
        - `stage.retry` tego kroku opiekuna już jest (restart między zamiarem a wynikiem) → `stage-interrupted`
          bez drugiego przebiegu: etap ponawia się dokładnie raz, a dalej i tak ponawia go zwykły przebieg."""
        ident = run.get("problem") or problem.get("id") or ""
        own = run.get("step") or ""
        stage = problem.get("stage") or (ident.split(":", 1)[1] if ":" in ident else "")
        label = f"„{stage}”" if stage else "karty"
        events = self.journal.read(card)
        if any(e.get("type") == caretaker.STAGE_RETRY and e.get("caretakerStep") == own for e in events):
            return {"failed": caretaker.STAGE_INTERRUPTED, "target": stage, "label": label,
                    "error": f"{caretaker.STAGE_INTERRUPTED}: ponowienie etapu {label} przerwał restart usługi"}
        mark = self.journal.append(card, caretaker.STAGE_RETRY, problem=ident, stage=stage,
                                   ticket=run.get("ticket") or "", caretaker=ident, caretakerStep=own)
        try:
            found = self._advance_card(card, own=("caretaker-action", own))
        except Exception as exc:        # jak w `tick`: błąd przebiegu nie zatrzymuje opiekuna ani karty
            found = [{"error": f"{exc.__class__.__name__}: {exc}", "stage": stage, "step": "", "ticket": ""}]
        for item in found:
            self._flow_error(card, item)
        relevant = [f for f in found if f.get("stage") == stage] if run.get("trigger") == "flow-error" else found
        if relevant:
            error = relevant[0]["error"]
            return {"failed": caretaker.STAGE_FAILED, "target": stage, "label": label,
                    "error": f"{caretaker.STAGE_FAILED}: {error}"}
        after = self.journal.read(card)
        now = fold(after)
        changed = [e for e in after if int(e.get("seq") or 0) > mark["seq"] and not _bookkeeping(now, e)]
        if not changed:
            return {"failed": caretaker.STAGE_NO_CHANGE, "target": stage, "label": label,
                    "error": f"{caretaker.STAGE_NO_CHANGE}: ponowienie etapu {label} niczego nie zmieniło"}
        return {"target": stage, "label": label}

    def _finish_caretaker(self, card: str, step: dict, forced: dict | None, exit_code) -> None:
        """Koniec kroku opiekuna: wynik sprawdza `caretaker.parse_result` (granica uprawnień w Tide), w dzienniku
        zostaje `step.finished` z `outcome`; akcję wykonuje `_caretake`."""
        name = step.get("step") or ""
        out = os.path.join(step.get("stepDir") or "", "out")
        st = self.journal.state(card)
        problem = self._caretaker_problem(st, st.caretaker_runs.get(name) or {})
        # Strażnik (PT-158) przed wszystkim innym, także przed `parse_result`: z kroku, który pisał poza `out`,
        # żadna akcja nie przechodzi, nawet z poprawnym plikiem wyniku.
        wrote = self._guard_verdict(card, name)
        if wrote:
            outcome = {"ok": False, "code": guard.WROTE, "action": "", "summary": "", "error": wrote}
        elif forced:
            reason = forced.get("reason") or forced.get("result") or ""
            outcome = {"ok": False, "code": caretaker.STOPPED, "action": "", "summary": "",
                       "error": f"krok opiekuna zatrzymany: {REASON_LABELS.get(reason, reason)}"}
        elif exit_code != 0:
            outcome = {"ok": False, "code": caretaker.OTHER_RESULT, "action": "", "summary": "",
                       "error": "program opiekuna zakończył się " +
                                (f"kodem {exit_code}" if exit_code is not None else "bez kodu wyjścia")}
        else:
            outcome = caretaker.parse_result(self._raw_result(os.path.join(out, handoff.RESULT_FILE)), problem, st)
        kept = []
        for entry in sorted(os.listdir(out)) if os.path.isdir(out) else []:
            path = os.path.join(out, entry)
            if (entry == handoff.RESULT_FILE or entry.endswith(".md")) and os.path.isfile(path) \
                    and not os.path.islink(path) and os.path.getsize(path) <= OUTPUT_MAX:
                with open(path, "rb") as fh:
                    self.journal.write_file(card, os.path.join("steps", name, entry), fh.read())
                kept.append(entry)
        self.journal.append(card, "step.finished", step=name, ticket=step.get("ticket") or "",
                            result="done" if outcome.get("ok") else "error",
                            reason="" if outcome.get("ok") else outcome.get("code") or "", question="", command="",
                            options=[], canAllow=True, stalledSince="", notes=[], kept=kept, exit=exit_code,
                            outcome=outcome)
        self._release_local(name)
        self.procs.pop(name, None)
        self.watch.pop(name, None)
        shutil.rmtree(os.path.join(self.tmp_root, f"tide-step-{name}"), ignore_errors=True)

    @staticmethod
    def _raw_result(path: str):
        """Plik wyniku opiekuna: obiekt JSON, None (brak pliku) albo "" (za duży albo nie JSON)."""
        try:
            if os.path.getsize(path) > OUTPUT_MAX or os.path.islink(path):
                return ""
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError):
            return ""

    @staticmethod
    def _result(path: str) -> dict:
        try:
            if os.path.getsize(path) > OUTPUT_MAX:
                return {"result": "error", "reason": "bad-result"}
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {"result": "error", "reason": "no-result"}
        except (OSError, ValueError):
            return {"result": "error", "reason": "bad-result"}
        if not isinstance(data, dict) or data.get("result") not in RESULTS:
            return {"result": "error", "reason": "bad-result"}
        if (data["result"] == "question" and
                (not isinstance(data.get("question"), str) or not data["question"].strip())) or \
                (data["result"] in ("denied", "outside") and
                 (not isinstance(data.get("command"), str) or not data["command"].strip() or
                  any(c in data["command"] for c in "\n\r\0"))):
            return {"result": "error", "reason": "bad-result"}
        notes = data.get("notes") if isinstance(data.get("notes"), list) else []
        if data["result"] == "outside":
            # Prośba o uruchomienie poza piaskownicą idzie ścieżką zgody na polecenie (dziennik, decyzja,
            # wznowienie, restart), z flagą `outside` — wynik polecenia dostaje wznowiony krok (03.11).
            return {"result": "denied", "outside": True, "reason": str(data.get("reason") or "")[:600],
                    "question": "", "command": data["command"].strip(),
                    "notes": [str(n) for n in notes if isinstance(n, str)][:20]}
        return {"result": data["result"], "reason": str(data.get("reason") or "")[:300],
                "question": str(data.get("question") or "")[:2000], "command": str(data.get("command") or ""),
                "options": clean_options(data.get("options")) if data["result"] == "question" else [],
                "baseProposal": baseproposal.clean(data.get("baseProposal")) if data["result"] == "question" else None,
                "waitFor": str(data.get("waitFor") or "").strip()[:32]
                if data["result"] in ("question", "error") and isinstance(data.get("waitFor"), str) else "",
                "notes": [str(n) for n in notes if isinstance(n, str)][:20]}

    @staticmethod
    def _claude_denied_command(path: str) -> str:
        """Końcowa odmowa Bash w CLI jest wynikiem `denied`, gdy agent nie zapisał własnego JSON."""
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if len(line) > OUTPUT_MAX or '"permission_denials"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict) or event.get("type") != "result":
                        continue
                    for denial in event.get("permission_denials") or []:
                        if not isinstance(denial, dict) or denial.get("tool_name") != "Bash":
                            continue
                        tool_input = denial.get("tool_input") or {}
                        command = tool_input.get("command") if isinstance(tool_input, dict) else ""
                        if isinstance(command, str) and command.strip() and not any(c in command for c in "\n\r\0"):
                            return command.strip()
        except OSError:
            pass
        return ""

    @staticmethod
    def _review_result(path: str) -> dict | None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("result") != "done":
            return None
        verdict = data.get("verdict")
        counts = [data.get(key) for key in ("blockers", "majors", "nits")]
        if verdict not in ("APPROVE", "REQUEST_CHANGES") or any(
            isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 999 for n in counts):
            return None
        if (verdict == "APPROVE") != (counts[0] == 0 and counts[1] == 0):
            return None
        return dict(verdict=verdict, blockers=counts[0], majors=counts[1], nits=counts[2])

    def _merge(self, card: str, st: FlowState, step: dict, retry: bool = False) -> None:
        if baseproposal.blocked(st):
            return
        ticket = step.get("ticket") or ""
        if step.get("step") in st.superseded:
            raise FlowError("stale-context", "Scalenie wymaga kroku z bieżącego kontekstu bazy.")
        info = ticket_of(st)
        repo = step.get("repo") or self._repo(card)
        # Zatwierdzony commit: APPROVE recenzenta albo świadoma decyzja użytkownika „scal mimo uwag” po 3. rundzie
        # (03.11, K-33/PT-108: uwagi dotyczyły wyłącznie testów niewykonalnych w piaskownicy).
        approved = next((v for (ticket_id, round_no), v in reversed(list(st.verdicts.items()))
                         if ticket_id == ticket and (v.get("verdict") == "APPROVE" or
                                                     (st.review_decisions.get((ticket, round_no)) or {}).get("choice")
                                                     == "accept")), None)
        review_step = st.steps.get((approved or {}).get("step")) or {}
        expected_tip = review_step.get("reviewTip") or ""
        if not expected_tip:
            raise FlowError("merge", "brak zatwierdzonego commita ticketu")
        try:
            self._act(card, "merge", ticket, lambda: self.git.merge(
                repo, info["cardBranch"], step.get("branch") or info["branch"], info["cardWorktree"],
                f"Merge ticket {ticket} into card {card}", expected_tip=expected_tip),
                branch=step.get("branch") or info["branch"], into=info["cardBranch"], expectedTip=expected_tip,
                cardTip=self.git.sha(repo, f"refs/heads/{info['cardBranch']}"), retry_failed=retry)
        except FlowError as exc:
            print(f"tide-daemon: przepływ karty {card}: scalenie {ticket}: {exc}", flush=True)

    def _start_conflict(self, card: str, st: FlowState, review_step: dict, failure: dict) -> None:
        """Oddaje konflikt wykonawcy w worktree karty; wynik sprawdza _finish."""
        item, project = self._project(card)
        ticket = review_step["ticket"]
        info = ticket_of(st, ticket)
        assigned = st.assignments.get(ticket) or st.started or {}
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        # Przepięty autor ticketu rozwiązuje też jego konflikt (05.11: konflikty PT-147/PT-151 czekały na limit
        # Claude do soboty, choć ticket był już przepięty na Codexa).
        override = (st.author_overrides.get(ticket) or {}).get("executor")
        try:
            selected = execution.resolve(override or assigned.get("executor") or "", catalog, config=cfg,
                                         probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        step_id = self._next_step_id(card, ticket, st)
        out = info["cardWorktree"] + f".conflict-{step_id}.step/out"
        prompt = handoff.conflict_card(card_id=card, title=item.title, description=item.description,
                                       ticket=ticket, card_branch=info["cardBranch"],
                                       ticket_branch=info["branch"], worktree=info["cardWorktree"],
                                       out_dir=out, error=failure.get("error") or "konflikt")
        self._launch(item, project, selected, ticket,
                     {"branch": info["cardBranch"]},
                     {"branch": info["cardBranch"], "worktree": info["cardWorktree"]},
                     step_id, [], kind="conflict", prompt_text=prompt,
                     review_tip=review_step.get("reviewTip") or "", attempt=1)

    def card_diff(self, card_id: str, target_ref: str = "") -> dict:
        """Różnica względem bieżącego refu bazy lub jawnego celu; bez zapisu i zgody na scalenie."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        st = self.journal.state(card)
        if not st.closed or st.card_finished:
            raise FlowError("not-closed", "karta nie jest jeszcze do sprawdzenia")
        info = ticket_of(st)
        repo = self._repo(card)
        if not isinstance(target_ref, str):
            raise FlowError("bad-request", "cel diff musi być pełnym refem")
        ref = target_ref or base_of(st)["baseRef"]
        if not ref or not info["cardBranch"]:
            raise FlowError("no-base", "baza jest nieznana — wybierz jawny pełny ref porównania")
        try:
            target = self.git.resolve_base(repo, ref, self.git.sha(repo, ref))
            diff = self.git.diff(repo, target["baseRef"], info["cardBranch"])
        except FlowGitError as exc:
            raise FlowError("diff", str(exc)) from None
        return {"ok": True, "id": card, "main": target["baseBranch"], "targetRef": target["baseRef"],
                "targetBranch": target["baseBranch"], "branch": info["cardBranch"], "diff": diff}

    # --- polecenia poza piaskownicą (03.11) --------------------------------------------------------

    def _outside_remembered(self, slug: str) -> list:
        from ..board.commands import outside_pattern
        value = (self._settings_data().get("outsideCommands") or {}).get(slug) or []
        # Wpisy sprzed rodzin (dokładne polecenia) liczą się jako ich rodzina — tak rozumiał to użytkownik.
        return [c if c.endswith(" *") else outside_pattern(c) for c in value if isinstance(c, str)] \
            if isinstance(value, list) else []

    def _remember_outside(self, slug: str, command: str) -> None:
        data = self._settings_data()
        allowed = data.get("outsideCommands") if isinstance(data.get("outsideCommands"), dict) else {}
        current = [c for c in (allowed.get(slug) or []) if isinstance(c, str)]
        if command not in current:
            allowed[slug] = current + [command]
        data["outsideCommands"] = allowed
        self._write_settings(data)

    def _outside_history(self, card: str, step: dict | None = None, *, command: str | None = None):
        """Niepełna historia nie uprawnia do zgody, startu ani sprzątania outside."""
        try:
            events = trialhold.read_process_history(self.journal, card)
            if step is not None:
                st = fold(events)
                name = step.get("step") or ""
                recorded = st.steps.get(name)
                if recorded is None or any(recorded.get(key) != step.get(key) for key in
                                           ("ticket", "kind", "runner", "worktree", "stepDir", "branch")):
                    raise ValueError("tożsamość outside nie zgadza się z zapisanym krokiem")
                self._trial_check(recorded)
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

    def _outside_ready(self, card: str, step: dict, done: dict, decision: dict):
        """Zgoda na polecenie poza piaskownicą: uruchamia je raz (jednostka systemd, kod wyjścia w pliku) i zwraca
        decyzję z wynikiem do wznowienia kroku, albo None, gdy polecenie jeszcze trwa."""
        self._trial_check(step)
        name = step.get("step") or ""
        # Start i zakończenie bieżącego uruchomienia outside tego procesu ze wspólnego wyniku redukcji:
        # zakończenie innej próby tej samej nazwy nie jest odpowiedzią bieżącej (PT-288 review 2, M4).
        state, started, finished = processidentity.outside_run(self._outside_history(card, step), name)
        if state != "ok":
            raise FlowError("outside-history", "nieznana historia outside — wykonanie wstrzymane")
        if finished:
            return {**decision, "answer": f"Kod wyjścia: {finished.get('exit')}\nKoniec wyniku:\n{finished.get('tail') or ''}"}
        if started is None:
            if self.journal.state(card).paused:
                # Wstrzymana karta nie uruchamia nowych poleceń poza piaskownicą i nie trzyma miejsca w kolejce
                # (07.11, K-38/PT-264: automatyczna zgoda 3 min po wstrzymaniu puściła pełne testy w trakcie gry).
                # Po wznowieniu zgoda wraca do kolejki; polecenie już uruchomione kończy się normalnie.
                self._outside_waiting.pop(name, None)
                return None
            if not consent.before_start(self, card, step, done, decision):
                return None
            if self._outside_busy(card):
                # Zgłoszenie w kolejce także przy zajętym miejscu (05.11: bez tego kolejka była pusta właśnie wtedy,
                # gdy miejsce się zwalniało, i wskakiwała karta sprawdzana jako pierwsza).
                self._outside_behind(card, name, decision, quiet=True)
                self._outside_log(name, "miejsce zajęte: " + ", ".join(sorted(self._outside_active or {})))
                return None     # kolejka: stan karty mówi „czeka na wolne miejsce poza piaskownicą”
            if self._outside_behind(card, name, decision):
                return None
            self._outside_waiting.pop(name, None)
            self._outside_log(name, "start")
            self._start_outside(card, step, done.get("command") or "")
            return None
        self._outside_busy(card)            # buduje zbiór aktywnych po restarcie
        self._outside_active[name] = (card, started["unit"])
        code = stepstop.read_exit_code(os.path.dirname(os.path.dirname(started["exitFile"])))
        if code is None and time.time() - _epoch(started.get("ts")) < OUTSIDE_TIMEOUT:
            return None
        if code is None:
            stepstop._systemctl("stop", started["unit"] + ".scope")
            code = "przekroczony czas"
        tail = ""
        try:
            with open(started["log"], "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - OUTSIDE_TAIL))
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            tail = "(brak wyniku)"
        self._outside_active.pop(name, None)
        # Procesy zostawione przez polecenie (np. atrapy z testów, serwery w tle) nie trzymają jednostki w nieskończoność.
        stepstop._systemctl("stop", started["unit"] + ".scope")
        self.journal.append(card, "outside.finished", step=name, ticket=step.get("ticket") or "",
                            command=started.get("command") or "", exit=code, tail=tail)
        # Długi wynik (np. pełne testy) nie mieści się w końcówce — krok czyta całość z pliku (04.11, K-46/KAMP-31:
        # z obciętego wyniku zniknęły wyniki testów i krok prosił o ten sam przebieg drugi raz).
        return {**decision, "answer": f"Kod wyjścia: {code}\nPełny wynik (do odczytu): {started['log']}\n"
                                      f"Koniec wyniku:\n{tail}"}

    def _review_rounds(self) -> int:
        """Po tylu rundach REQUEST_CHANGES Tide pyta użytkownika (domyślnie 6; 03.11: po 3 karty stały co chwilę)."""
        try:
            return max(1, min(20, int(self._settings_data().get("reviewRoundsBeforeQuestion", REVIEW_ROUNDS))))
        except (TypeError, ValueError):
            return REVIEW_ROUNDS

    def _codex_broken_mcp(self, step: dict, data: dict) -> str:
        """Nazwa serwera MCP, na którym Codex nie wystartował („invalid transport”), gdy jest nowa — zapisana w
        ustawieniach przepływu (`codexMcpSkip`); pusta, gdy to inny błąd albo serwer był już pomijany."""
        if data.get("result") != "error" or step.get("program") != "codex":
            return ""
        try:
            with open(os.path.join(step.get("stepDir") or "", "out", "step.log"), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - 4000))
                found = agentcmd.CODEX_BROKEN_MCP.search(fh.read().decode("utf-8", "replace"))
        except OSError:
            return ""
        if not found:
            return ""
        settings = self._settings_data()
        skip = [n for n in settings.get("codexMcpSkip") or [] if isinstance(n, str)]
        if found.group(1) in skip:
            return ""
        settings["codexMcpSkip"] = skip + [found.group(1)]
        self._write_settings(settings)
        data["reason"] = f"konfiguracja Codexa: serwer MCP {found.group(1)} — pomijam go i ponawiam krok"
        return found.group(1)

    def _auto_consent(self) -> bool:
        """Automatyczne zgody (domyślnie włączone); `autoConsent: false` w ustawieniach przepływu je wyłącza."""
        return self._settings_data().get("autoConsent", True) is not False

    def _auto_outside(self, card: str) -> bool:
        """Automatyczne uruchamianie poza piaskownicą — przełącznik na projekt (`outsideAuto: {"slug": false}`),
        bo poza piaskownicą kod z repozytorium działa z prawami użytkownika (docs/sandbox-and-permissions.md)."""
        if not self._auto_consent():
            return False
        slug = self._project(card)[1].slug
        return (self._settings_data().get("outsideAuto") or {}).get(slug, True) is not False

    def _env_question(self, card: str, step: dict, done: dict) -> None:
        """Pytanie kroku o środowisko (piaskownica, Docker, porty, instalacje, testy) dostaje odpowiedź Tide zamiast
        trafić do użytkownika (03.11: „to serio tak ma działać, że muszę odpalać sesję ratunkową?”). Raz na ticket
        z rzędu: gdy agent zapyta ponownie zaraz po odpowiedzi Tide, pytanie idzie do użytkownika."""
        if done.get("baseProposal") is not None or baseproposal.pending(self.journal.state(card)):
            return
        question = str(done.get("question") or "")
        name = step.get("step") or ""
        if not ENV_QUESTION.search(question) or self.journal is None:
            return
        events = self.journal.read(card)
        if any(e.get("type") == "step.decision" and e.get("step") == name for e in events):
            return
        parent = step.get("parentStep") or ""
        if any(e.get("type") == "step.decision" and e.get("step") == parent and e.get("envAnswer") for e in events):
            return      # Tide już odpowiedział w poprzedniej próbie — teraz decyduje użytkownik
        self.journal.append(card, "step.decision", step=name, ticket=step.get("ticket") or "",
                            kind=step.get("kind") or "", choice="answer", answer=ENV_ANSWER, command="",
                            executor="", auto=True, envAnswer=True)

    def _consent_recheck(self, card: str, step: dict, done: dict) -> None:
        """Prośba czekająca na zgodę sprzed zmiany reguł: zatwierdzana tak, jak zostałaby zgłoszona dziś."""
        if trialhold.held(step):
            return
        if done.get("outside"):
            self._outside_recheck(card, step, done)
            return
        from ..board.commands import sandbox_grant
        allowed = sandbox_grant(done.get("command") or "", self._project(card)[1].denied_commands) is not None
        if allowed != bool(done.get("canAllow", True)):
            self.journal.append(card, "permission.rechecked", step=step.get("step") or "",
                                ticket=step.get("ticket") or "", canAllow=allowed)
        if allowed and self._auto_consent():
            try:
                self.resolve_step(card, {"choice": "allow", "step": step.get("step") or "", "auto": True})
            except FlowError:
                pass

    def _outside_recheck(self, card: str, step: dict, done: dict) -> None:
        """Prośba sprzed zmiany reguł (np. `E2E_PORT=… npm run e2e` odrzucane jako łańcuch, rodziny zamiast dokładnych
        poleceń): gdy dziś pasuje do zapamiętanej rodziny projektu, uruchamia się bez pytania."""
        if trialhold.held(step):
            return
        try:
            self._outside_history(card, step)
        except FlowError:
            return
        if step.get("runner") == boxstep.RUNNER:
            if done.get("canAllow", True):
                self.journal.append(card, "permission.rechecked", step=step.get("step") or "",
                                    ticket=step.get("ticket") or "", canAllow=False)
            return
        from ..board.commands import outside_allowed
        command = done.get("command") or ""
        _, project = self._project(card)
        roots = (step.get("worktree") or "", project.repo, step.get("stepDir") or "")
        allowed = outside_allowed(command, project.denied_commands, roots)
        if allowed != bool(done.get("canAllow", True)):
            # Reguły zmieniły się od wyniku kroku (K-33/PT-110: `find -exec … \;` uznane za łańcuch) — decyzja
            # w oknie ma pokazać dzisiejszy stan, inaczej użytkownik nie ma czego wybrać.
            self.journal.append(card, "permission.rechecked", step=step.get("step") or "",
                                ticket=step.get("ticket") or "", canAllow=allowed)
        if allowed and consent.automatic(self, card, step, command):
            self.journal.append(card, "step.decision", step=step.get("step") or "", ticket=step.get("ticket") or "",
                                kind=step.get("kind") or "", choice="run", answer="", command=command,
                                executor="", auto=True)

    CLASS_RANK = {"lekki": 0, "standard": 1, "mocny": 2}

    def _move_reviewer_files(self, step: dict, repo: str) -> list[str]:
        """Nieśledzone pliki, które pojawiły się w worktree ticketu podczas review, idą do `out/pliki-recenzenta/`
        kroku review — ticket zostaje czysty (autor ich nie zacommituje), a review nie przepada."""
        worktree = step.get("worktree") or ""
        step_dir = step.get("stepDir") or ""
        before_file = os.path.join(step_dir, "in", UNTRACKED_BEFORE)
        if not worktree or not os.path.isfile(before_file):
            return []
        try:
            with open(before_file, encoding="utf-8") as fh:
                before = set(json.load(fh))
        except (OSError, ValueError):
            return []
        moved = []
        target_root = os.path.realpath(os.path.join(step_dir, "out", "pliki-recenzenta"))
        root = os.path.realpath(worktree)
        for rel in self.git.untracked(repo, worktree):
            if rel in before:
                continue
            source = os.path.realpath(os.path.join(root, rel))
            target = os.path.realpath(os.path.join(target_root, rel))
            if not source.startswith(root + os.sep) or not target.startswith(target_root + os.sep):
                continue
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(source, target)
                moved.append(rel)
            except OSError:
                continue
        return moved

    def _denied_before(self, card: str, ticket: str, command: str) -> bool:
        """Użytkownik odmówił już tego polecenia w tym tickecie — Tide nie zgadza się na nie sam (04.11, K-43/BO-13)."""
        wanted = " ".join(str(command).split())
        return bool(wanted) and any(
            d.get("choice") == "deny" and d.get("ticket") == ticket and " ".join(str(d.get("command") or "").split()) == wanted
            for d in self.journal.state(card).step_decisions.values())

    def _git_identity(self, repo: str) -> dict:
        """Autor commitów kroku = tożsamość gita użytkownika dla tego repozytorium (lokalna, inaczej globalna).
        Commity agenta mają wyglądać jak commity użytkownika (04.11, K-49: „Krok <krok@tide.invalid>” na firmowym
        GitLabie). Bez tożsamości w gicie zostaje domyślna kroku."""
        found = {}
        for key, field in (("user.name", "git_name"), ("user.email", "git_email")):
            result = self.git.run(repo, "config", key)
            value = (result.out or "").strip() if result.ok else ""
            if value:
                found[field] = value
        return found

    @staticmethod
    def _wait_ticket_target(st: FlowState, step: dict, target) -> str:
        """Ticket planu tej karty, na którego scalenie krok ma czekać: identyfikator (PT-171) albo numer z planu (#3).
        Odmowa: spoza planu, własny ticket, odrzucony albo czekanie w kółko (cel czeka na ten ticket)."""
        raw = str(target or "").strip()
        own = step.get("ticket") or ""
        rows = (st.plan_approved or {}).get("rows") or []
        found = next((str(r.get("id") or "") for r in rows if raw and (
            str(r.get("id") or "").upper() == raw.upper() or r.get("workId") == raw)), "")
        if not found or found == own:
            raise FlowError("bad-ticket", "podaj inny ticket z planu tej karty, na który krok ma czekać (np. PT-171)")
        if found in st.rejected:
            raise FlowError("bad-ticket", f"{found} jest odrzucony — nie ma na co czekać")
        if found in st.imported_done:
            raise FlowError("bad-ticket", f"{found} jest gotowy z przejętego planu — nie ma na co czekać")
        from . import waiting
        if waiting.would_cycle({"": st}, "", step, ticket=found):
            raise FlowError("bad-ticket", f"{found} pośrednio lub bezpośrednio czeka na {own} — czekanie w kółko")
        return found

    @staticmethod
    def _wait_ticket_resume_text(st: FlowState, step: dict, target: str) -> str:
        decision = next((d for name, d in st.step_decisions.items() if d.get("waitTicket") == target and
                         (st.steps.get(name) or {}).get("ticket") == step.get("ticket")), {})
        note = str(decision.get("answer") or "").strip()
        if target in st.rejected:
            text = (f"Ticket {target}, na który czekał ten krok, został odrzucony i nie trafi do gałęzi karty. "
                    "Dokończ ticket bez niego albo zakończ krok pytaniem o zakres.")
        elif target in st.imported_done:
            text = (f"Ticket {target}, na który czekał ten krok, jest gotowy z przejętego planu. "
                    "Sprawdź dostępność jego wyniku w worktree i kontynuuj ticket.")
        else:
            card_branch = ticket_of(st, step.get("ticket") or "").get("cardBranch") or "gałąź karty"
            text = (f"Ticket {target}, na który czekał ten krok, jest scalony do gałęzi karty {card_branch}. "
                    f"Najpierw scal ją do gałęzi ticketu w tym worktree (`git merge --no-edit {card_branch}`; "
                    "konflikty rozwiąż i zatwierdź), potem kontynuuj ticket.")
        return text + (f"\nUwaga użytkownika: {note}" if note else "")

    def _declared_wait(self, card: str, name: str, step: dict, data: dict) -> bool:
        """Krok sam zgłosił w wyniku `waitFor` (ticket planu albo karta): Tide zapisuje decyzję czekania bez pytania
        użytkownika i wznowi krok, gdy cel się domknie. Zły cel — zwykłe pytanie albo błąd kroku."""
        target = str(data.get("waitFor") or "").strip()
        if data.get("result") not in ("question", "error"):
            return False
        choice = "wait-card" if re.fullmatch(r"[Kk]-\d+", target) else "wait-ticket"
        try:
            self._save_wait_decision(card, name, step, choice, target, auto=True)
        except FlowError:
            return False
        return True

    def _save_wait_decision(self, card, name, step, choice, target, **fields):
        """Odczyt grafu, kontrola i zapis są jedną sekcją dla wyników kroków i decyzji ręcznych."""
        from . import waiting
        with self.journal.lock:
            st = self.journal.state(card)
            if baseproposal.blocked(st):
                raise FlowError("base-decision", "najpierw rozstrzygnij pytanie o bazę karty")
            if name in st.step_decisions or name in st.superseded or st.card_finished:
                raise FlowError("already-decided", "krok nie czeka już na decyzję")
            states = {ident: self.journal.state(ident) for ident in self.journal.cards()}
            states[card] = st
            if choice == "wait-card":
                other = str(target or "").strip().upper()
                if not re.fullmatch(r"K-\d+", other) or other == card or other not in states:
                    raise FlowError("bad-card", "podaj inną kartę z przepływem, na którą krok ma czekać (np. K-43)")
                extra = {"waitCard": other}
                cyclic = waiting.would_cycle(states, card, step, other=other)
            else:
                ticket = self._wait_ticket_target(st, step, target)
                extra = {"waitTicket": ticket}
                cyclic = waiting.would_cycle(states, card, step, ticket=ticket)
            if cyclic:
                raise FlowError("wait-cycle", "cel pośrednio lub bezpośrednio czeka na ten krok — czekanie w kółko")
            return self.journal.append(card, "step.decision", step=name, ticket=step.get("ticket") or "",
                                       kind=step.get("kind") or "", choice=choice, command="", executor="",
                                       answer=fields.pop("answer", ""), **fields, **extra)

    def _card_done(self, card: str) -> bool:
        """Karta domknięta (krok domknięcia przeszedł albo karta skończona) — koniec czekania „czeka na kartę”."""
        try:
            other = self.journal.state(card)
        except Exception:
            return False
        return bool(other.card_finished or other.closed)

    def _extra_repos(self, card: str) -> tuple:
        """Repozytoria dodatkowych projektów karty — do odczytu gitem w poleceniach poza piaskownicą."""
        try:
            board = self.store.load()
            item = board.cards.get(card_num(card))
        except Exception:
            return ()
        if item is None:
            return ()
        return tuple(board.projects[slug].repo for slug in item.projects
                     if slug in board.projects and board.projects[slug].repo)

    def _other_provider_executor(self, st: FlowState, step: dict) -> str:
        """Wykonawca innego dostawcy w tej samej klasie (albo mocny), inny model niż autor ticketu; pusty — brak."""
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            return ""
        current = next((e for e in catalog.executors if e.name == step.get("executor")), None)
        if current is None or current.provider == "local":
            return ""
        # Recenzent nie może mieć modelu ostatniego autora kodu ticketu (krok ticket/fix przed tym review).
        authors = [s for s in st.steps.values() if s.get("ticket") == step.get("ticket") and s.get("ticket")
                   and s.get("kind") not in ("review",) and s.get("step") != step.get("step")]
        avoid = ((authors[-1].get("model") if authors and step.get("kind") == "review" else "") or "").lower()
        options = [e for e in catalog.executors if e.classes and e.provider not in (current.provider, "local")
                   and e.model.lower() != avoid]
        same = [e for e in options if set(e.classes) & set(current.classes)]
        strong = [e for e in options if "mocny" in e.classes]
        pick = (same or strong or options)
        return pick[0].name if pick else ""

    def _stronger_executor(self, step: dict, done: dict) -> str:
        """Następny mocniejszy wykonawca tego samego dostawcy (lekki → standard → mocny) po błędzie kroku, który nie
        jest winą użytkownika ani limitu; pusty — nie ma mocniejszego albo błąd tego nie uzasadnia."""
        if self._settings_data().get("escalateErrors", True) is False or \
                done.get("reason") in ("login", "limit", "local-unavailable") or \
                faults.of_finished(done)["fault"] in ("user", "provider"):
            return ""
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            return ""
        current = next((e for e in catalog.executors if e.name == step.get("executor")), None)
        if current is None:
            return ""
        rank = max((self.CLASS_RANK.get(c, -1) for c in current.classes), default=-1)
        if rank < 0:
            return ""
        better = [e for e in catalog.executors if e.provider == current.provider and e.name != current.name and
                  max((self.CLASS_RANK.get(c, -1) for c in e.classes), default=-1) > rank]
        better.sort(key=lambda e: max(self.CLASS_RANK.get(c, -1) for c in e.classes))
        return better[0].name if better else ""

    def _ticket_consent(self, card: str, ticket: str) -> bool:
        return (self.journal.state(card).ticket_consents.get(ticket) or {}).get("type") == consent.GRANTED

    def _outside_busy(self, card: str) -> bool:
        """Jedno polecenie poza piaskownicą naraz w projekcie i OUTSIDE_TOTAL łącznie (03.11, K-31: pięć przebiegów
        e2e naraz na jednym porcie kończyło się kodem 1 i agenci prosili o kolejne)."""
        if self._outside_active is None:
            # Po restarcie usługi: polecenia rozpoczęte (bez zakończenia) we wszystkich kartach.
            # Wspólny wynik redukcji (PT-288 review 2, M7): bieżące uruchomienie każdego procesu;
            # proces wstrzymany z jakimkolwiek startem zajmuje miejsce zachowawczo.
            self._outside_active = {}
            for other in self.journal.cards():
                for name, started in processidentity.outside_runs(self.journal.read(other)).items():
                    unit = started.get("unit")
                    self._outside_active[name] = (other, unit if isinstance(unit, str) else "")
        alive = {}
        for name, (other, unit) in self._outside_active.items():
            if stepstop.read_exit_code(os.path.dirname(os.path.dirname(self._outside_exit(other, name)))) is None:
                alive[name] = (other, unit)
        self._outside_active = alive
        if len(alive) >= OUTSIDE_TOTAL:
            return True
        slug = self._project(card)[1].slug
        return sum(1 for other, _ in alive.values() if self._project(other)[1].slug == slug) >= OUTSIDE_PER_PROJECT

    def _outside_behind(self, card: str, name: str, decision: dict, *, quiet: bool = False) -> bool:
        """Kolejka według czasu zgody (05.11: polecenie K-38 czekało ponad godzinę, bo każde zwolnione miejsce
        projektu brała karta sprawdzana wcześniej). Zgoda zgłasza się przy każdym sprawdzeniu; wpis bez sprawdzenia
        przez OUTSIDE_QUEUE_STALE (karta zatrzymana, krok odrzucony) wypada z kolejki."""
        now = time.time()
        if not getattr(self, "_outside_seeded", False):
            self._outside_seeded = True
            self._seed_outside_waiting(now)
        slug = self._project(card)[1].slug
        previous = self._outside_waiting.get(name)
        queued = previous[2] if previous else (_epoch(decision.get("ts")) if decision.get("ts") else now)
        self._outside_waiting[name] = (card, slug, queued, now)
        self._outside_waiting = {k: v for k, v in self._outside_waiting.items() if now - v[3] < OUTSIDE_QUEUE_STALE}
        for other, (other_card, other_slug, other_queued, _) in self._outside_waiting.items():
            if other == name or (other_queued, other) >= (queued, name):
                continue
            if other_slug == slug or not self._outside_busy(other_card):
                if not quiet:
                    self._outside_log(name, f"czeka za starszą zgodą {other}")
                return True     # starsza zgoda w tym projekcie albo starsza, która może ruszyć gdzie indziej
        return False

    def _outside_log(self, name: str, text: str) -> None:
        """Jedna linia w dzienniku usługi przy zmianie powodu czekania polecenia poza piaskownicą."""
        seen = self.__dict__.setdefault("_outside_logged", {})
        if seen.get(name) != text:
            seen[name] = text
            print(f"tide-daemon: kolejka poza piaskownicą: {name}: {text}", flush=True)

    def _seed_outside_waiting(self, now: float) -> None:
        """Po restarcie usługi: zgody na polecenia poza piaskownicą, które jeszcze nie ruszyły, z czasem zgody
        z dziennika — inaczej pierwsza sprawdzona karta wyprzedza starsze zgody (05.11, K-37 przed K-38)."""
        for other in self.journal.cards():
            try:
                events = self.journal.read(other)
                slug = self._project(other)[1].slug
            except Exception:
                continue
            # Starty outside, wznowienia i prośby ze wspólnego wyniku redukcji i stanu karty (PT-288 review 2, M7):
            # proces z jakimkolwiek śladem startu outside (także wstrzymany) nie wraca do kolejki.
            identities = processidentity.process_identities(events)
            started = {name for name, identity in identities.items() if isinstance(name, str)
                       and identity.has_outside_start}
            current = fold(events)
            if current.paused:
                continue        # wstrzymana karta nie zajmuje miejsca w kolejce (wraca do niej po wznowieniu)
            resumed = {s.get("parentStep") for s in current.steps.values() if s.get("parentStep")}
            outside = {name for name, identity in identities.items() if isinstance(name, str)
                       and identity.has_outside_request}
            for e in current.step_decisions.values():
                name = e.get("step")
                queued = _epoch(e.get("ts")) if e.get("ts") else now
                # Stara zgoda (karta porzucona) nie blokuje kolejki nawet przez OUTSIDE_QUEUE_STALE.
                if e.get("type") == "step.decision" and e.get("choice") in ("run", "run-always", "run-ticket") and \
                        name in outside and name not in started and name not in resumed and \
                        name not in current.superseded and now - queued < 86400:
                    self._outside_waiting.setdefault(name, (other, slug, queued, now))

    def _outside_exit(self, card: str, name: str) -> str:
        """Plik kodu wyjścia uruchomienia outside procesu ze wspólnego wyniku redukcji (PT-288 review 2, M7):
        niezakończone uruchomienie, które zajmuje miejsce (najpierw bieżącej próby), inaczej bieżące."""
        events = self.journal.read(card)
        found = processidentity.outside_runs(events).get(name)
        if found is None:
            found = processidentity.outside_run(events, name)[1] or {}
        value = found.get("exitFile")
        return value if isinstance(value, str) and value else "/nonexistent/in/exit-code"

    def _start_outside(self, card: str, step: dict, command: str) -> None:
        self._trial_check(step)
        if card == "K-38" and not step.get("ticket"):
            raise FlowError("k38-trial-held", "K-38 v1: nieznana tożsamość kroku")
        if step.get("runner") == boxstep.RUNNER:
            raise FlowError("box-outside", "krok w pudełku nie może uruchamiać poleceń na hoście")
        self._outside_history(card, step, command=command)
        name = step.get("step") or ""
        worktree = step.get("worktree") or ""
        # Pełny przebieg testów na niezmienionym kodzie po porażce: tylko padnięte moduły (cardflow/testrerun.py).
        requested_command = command
        command, head, rerun_of = testrerun.substitute(self.journal.read(card), worktree, command)
        folder = os.path.join(step.get("stepDir") or "", "outside")
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
        if not env.get("JOBS") and not test_pool_available(step.get("worktree") or ""):
            # Gałąź sprzed wspólnej puli testów (06.11: PT-173/PT-186/PT-220 po 14 procesów każdy, obciążenie 65):
            # `make` czyta JOBS ze środowiska, więc przebieg bierze najwyżej OLD_RUNNER_JOBS procesów.
            env["JOBS"] = str(OLD_RUNNER_JOBS)
        argv = [self.systemd_run or "systemd-run", "--user", "--scope", "--collect", "--quiet", f"--unit={unit}",
                "-p", f"CPUWeight={STEP_CPU_WEIGHT}", "-p", f"IOWeight={STEP_CPU_WEIGHT}", "--",
                "/usr/bin/env", "-u", "INVOCATION_ID", "/bin/sh", "-c", agentcmd.EXIT_WRAPPER, "tide-outside",
                exit_file, "/bin/sh", "-c", command]
        # Ta sama blokada obejmuje świeży odczyt, proces i zapis startu: decyzja
        # ani historia nie mogą zmienić się pomiędzy kontrolą a Popen.
        with open(log, "ab") as out, self.journal.lock:
            self._outside_history(card, step, command=requested_command)
            subprocess.Popen(argv, cwd=step.get("worktree") or None, env=env, stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            self.journal.append(card, "outside.started", step=name, ticket=step.get("ticket") or "", command=command,
                                unit=unit, log=log, exitFile=exit_file, worktree=worktree, head=head, rerunOf=rerun_of)
        if self._outside_active is None:
            self._outside_active = {}
        self._outside_active[name] = (card, unit)

    AUTO_DENY_LIMIT = 3

    def _auto_deny(self, card, name, step, command, *, note=None):
        """Na polecenie, na które nie da się zezwolić (łańcuch, przypisanie zmiennej, zabronione), użytkownik i tak
        może tylko odmówić — Tide robi to sam i wznawia krok z prośbą o pojedyncze polecenia (K-33/K-35/K-15:
        karty stały w „Czeka na mnie” z wyszarzonym „Zezwól”). Po AUTO_DENY_LIMIT odmowach w tym samym tickecie
        (albo kroku planu) decyzja wraca do użytkownika, żeby nie kręcić się w kółko."""
        ticket = step.get("ticket") or ""
        kind = step.get("kind") or ""
        if self._caretaker_takes_over(card, self.journal.state(card), name):
            return      # ta sama odmowa drugi raz (PT-148): opiekun przed kolejną ślepą próbą i przed AUTO_DENY_LIMIT
        used = sum(1 for e in self.journal.read(card) if e.get("type") == "step.decision" and e.get("auto")
                   and e.get("choice") == "deny"
                   and (e.get("ticket") or "") == ticket and (ticket or e.get("kind") == kind))
        if used >= self.AUTO_DENY_LIMIT:
            return
        from ..board.commands import MAX_COMMAND
        if note is None:
            note = AUTO_DENY_LONG.format(limit=MAX_COMMAND) if len(command) > MAX_COMMAND else AUTO_DENY_NOTE
        self.journal.append(card, "step.decision", step=name, ticket=ticket, kind=kind, choice="deny",
                            answer=note, command=command, executor="", auto=True)

    def suggest_card_base(self, card_id: str, request: dict) -> dict:
        result = self.prepare_card_base_suggestion(card_id, request)
        return result() if callable(result) else result

    def prepare_card_base_suggestion(self, card_id: str, request: dict):
        """Lokalny wynik albo odczyt hostingu pracujący wyłącznie na przygotowanej migawce."""
        from .basesuggestions import suggest_text
        if not isinstance(request, dict) or set(request) - {"text", "revision", "scope"}:
            raise FlowError("bad-request", "analiza przyjmuje text, revision i scope")
        text, revision, scope = request.get("text"), request.get("revision"), request.get("scope")
        if (not isinstance(text, str) or len(text) > 200000 or type(revision) is not int or revision < 0
                or scope not in ("text", "files", "links")):
            raise FlowError("bad-request", "wymagane: text (do 200000 znaków), nieujemna revision i scope text|files|links")
        card, project = self._project(card_id)
        identity = {"revision": revision, "card": card_id, "project": project.slug, "repo": project.repo}
        if not project.repo or project.repo_missing or not os.path.isdir(project.repo):
            raise FlowError("no-repo", "projekt karty nie ma dostępnego repozytorium")
        if scope == "files":
            from .filesuggestions import suggest_files
            return {"ok": True, **identity, **suggest_files(self.git, project.repo, card.description, text)}
        try:
            branches = self.git.list_branches(project.repo, "", 2**31, "base")["branches"]
        except FlowGitError as exc:
            raise FlowError("suggestions", str(exc)) from None
        if scope == "links":
            from .linksuggestions import remote_config, suggest_links
            try:
                remotes = remote_config(self.git, project.repo)
            except Exception as exc:
                # Wyłącznie typ błędu: repr wyjątku może zawierać URL albo poświadczenia.
                print(f"tide-daemon: SuggestCardBase: {type(exc).__name__}", flush=True)
                return {"ok": True, **identity, "candidates": [],
                        "warnings": ["Nie można sprawdzić linków. Wybierz bazę ręcznie."]}
            repo, description = project.repo, card.description

            def read_links():
                try:
                    suggestions = suggest_links(None, repo, branches, description, text, remotes=remotes)
                except Exception as exc:
                    print(f"tide-daemon: SuggestCardBase: {type(exc).__name__}", flush=True)
                    suggestions = {"candidates": [], "warnings": ["Nie można sprawdzić linków. Wybierz bazę ręcznie."]}
                return {"ok": True, **identity, **suggestions}

            return read_links
        suggestions = suggest_text(branches, card.description, text)
        return {"ok": True, **identity, **suggestions}

    def list_card_branches(self, card_id: str, request: dict) -> dict:
        if set(request) - {"purpose", "text", "limit"}:
            raise FlowError("bad-request", "lista przyjmuje tylko purpose, text i limit")
        if request.get("purpose") not in ("base", "merge"):
            raise FlowError("bad-request", "purpose musi być base albo merge")
        text, limit = request.get("text", ""), request.get("limit", 50)
        if not isinstance(text, str) or type(limit) is not int or not 1 <= limit <= 100:
            raise FlowError("bad-request", "tekst musi być napisem, limit liczbą od 1 do 100")
        st = self.journal.state(card_id) if request["purpose"] == "merge" else None
        if st is not None and st.started:
            # Zmiana projektu karty nie zmienia repozytorium rozpoczętego zlecenia.
            # Lista i kwalifikacja celu muszą używać tego samego repo co podgląd i zgoda.
            repo = self._repo(card_id)
            if not repo or not os.path.isdir(repo):
                raise FlowError("no-repo", "zlecenie karty nie ma dostępnego repozytorium")
        else:
            try:
                _, project = self._project(card_id)
            except BoardError as exc:
                raise FlowError(exc.code, str(exc)) from None
            repo = project.repo
            if not repo or project.repo_missing or not os.path.isdir(repo):
                raise FlowError("no-repo", "projekt karty nie ma dostępnego repozytorium")
        try:
            result = self.git.list_branches(repo, text if request["purpose"] == "base" else "",
                                            limit if request["purpose"] == "base" else 2**31,
                                            request["purpose"])
            if request["purpose"] == "merge":
                from . import targetprep
                protected = self._protected_merge_targets(card_id, repo)
                checkouts = self.git.branch_checkouts(repo)
                source_ref = "refs/heads/" + ticket_of(st)["cardBranch"]
                for item in result["branches"]:
                    path = checkouts.get(item["ref"], "")
                    blocks = self._merge_target_blocks(source_ref, item["ref"], path,
                                                       bool(path) and os.path.isdir(path), protected)
                    item.update(selectable=not blocks, reason=blocks[0][1] if blocks else "")
                try:
                    result["recentTargets"] = self._recent_targets(repo, result["branches"])
                except Exception:
                    result["warnings"].append("Nie można odczytać ostatnich celów. Wybierz cel z listy.")
                result["branches"] = [b for b in result["branches"] if text.casefold() in b["ref"].casefold()]
                base = base_of(st)
                choices = targetprep.remote_choices(self.git, repo, base)
                result.update(choices)
                proposal = choices["createTarget"]
                if proposal and text.casefold() in (proposal["ref"] + proposal["label"]).casefold():
                    # Kolizja nazwy pozostawia istniejący cel na liście, z osobnym wyjaśnieniem propozycji.
                    if not any(b["ref"] == proposal["ref"] for b in result["branches"]):
                        result["branches"].append(proposal)
                base_ref = base["baseRef"] if base["status"] == "known" else ""
                result["branches"].sort(key=lambda b: (b["ref"] != choices["defaultTargetRef"],
                    b["ref"] != base_ref, b["ref"] not in choices["trackingTargets"],
                    b["ref"] != (proposal or {}).get("ref"), b["ref"] != result["mainRef"]))
                result["hasMore"] = len(result["branches"]) > limit
                result["branches"] = result["branches"][:limit]
        except FlowGitError as exc:
            raise FlowError("branches", str(exc)) from None
        if request["purpose"] == "base":
            try:
                result["recentBases"] = self._recent_bases(project.repo)
            except Exception:
                result["warnings"].append("Nie można odczytać ostatnich baz. Wybierz bazę z listy.")
        return {"ok": True, **result}

    def _protected_merge_targets(self, card: str, repo: str) -> list[dict]:
        """Ten sam zbiór dla listy, podglądu i ponownego sprawdzenia zgody."""
        key = self.git.repository_key(repo)
        protected = []
        for other_id in self.journal.cards():
            if other_id == card:
                continue
            other = self.journal.state(other_id)
            other_repo = (other.started or {}).get("repo")
            if not other_repo:
                try:
                    other_repo = self._repo(other_id)
                except FlowError:
                    continue

            def same_repository(path):
                try:
                    return self.git.repository_key(path) == key
                except FlowGitError:
                    return False

            info = ticket_of(other)
            if same_repository(other_repo) and info["cardBranch"]:
                protected.append({"card": other_id, "branch": info["cardBranch"], "worktree": info["cardWorktree"]})
            outside_steps = [other.steps.get(name, {}) for name in other.outside_started]
            for step in other.running() + other.open_consoles() + outside_steps:
                if same_repository(step.get("repo") or other_repo):
                    protected.append({"card": other_id, "branch": step.get("branch") or "",
                                      "worktree": step.get("worktree") or ""})
        return protected

    @staticmethod
    def _merge_target_blocks(source_ref, target_ref, path, exists, protected):
        """Kwalifikacja celu; kolizje treści i gotowość karty ocenia dopiero podgląd."""
        reasons = []
        if source_ref == target_ref:
            reasons.append(("card-target", "Celem nie może być gałąź karty."))
        for item in protected:
            if (target_ref == "refs/heads/" + item["branch"] or
                    (exists and item["worktree"] and os.path.realpath(item["worktree"]) == os.path.realpath(path))):
                reasons.append(("protected-target", "Cel należy do karty " + item["card"] + " lub jej czynnego kroku."))
                break
        if path and not exists:
            reasons.append(("no-target-worktree", "Zarejestrowany checkout celu jest niedostępny."))
        return reasons

    def _recent_targets(self, repo: str, branches: list[dict]) -> list[dict]:
        """Historia ukończonych scaleń; kwalifikacja i dzisiejszy SHA pochodzą ze wspólnej listy."""
        key = self.git.repository_key(repo)
        identities = {os.path.realpath(repo): key}
        choices = []
        for card in self.journal.cards():
            events = self.journal.read(card)
            for index, event in enumerate(events):
                if event.get("type") not in ("card.merged", "card.main-merged"):
                    continue
                state = fold(events[:index + 1])
                merged = state.merged or {}
                # Mieszane dzienniki mogą powtarzać wynik jednej operacji.
                if merged.get("seq") != event.get("seq"):
                    continue
                ref = merged.get("targetRef") or ""
                source = (state.started or {}).get("repo")
                if not source or not ref.startswith("refs/heads/"):
                    continue
                source = os.path.realpath(source)
                if source not in identities:
                    try:
                        identities[source] = self.git.repository_key(source)
                    except FlowGitError:
                        identities[source] = ""
                if identities[source] == key:
                    choices.append((datetime.fromisoformat(merged["ts"]).timestamp(), card,
                                    merged.get("seq", 0), ref))
        available = {b["ref"]: b for b in branches}
        seen, recent = set(), []
        for _, _, _, ref in sorted(choices, reverse=True):
            if ref in seen or ref not in available:
                continue
            seen.add(ref)
            recent.append(dict(available[ref]))
            if len(recent) == 10:
                break
        return recent

    def _recent_bases(self, repo: str) -> list[dict]:
        """Skróty są projekcją udanych zdarzeń, wspólną dla worktrees rzeczywistego repozytorium."""
        key = self.git.repository_key(repo)
        identities = {os.path.realpath(repo): key}
        choices = []
        for card in self.journal.cards():
            events = self.journal.read(card)
            for index, event in enumerate(events):
                if event.get("type") not in ("card.started", "card.base-changed"):
                    continue
                state = fold(events[:index + 1])
                if event["type"] == "card.base-changed" and state.base_change != event:
                    continue
                source = (state.started or {}).get("repo")
                base = base_of(state)
                if not source or base["status"] != "known":
                    continue
                source = os.path.realpath(source)
                if source not in identities:
                    try:
                        identities[source] = self.git.repository_key(source)
                    except FlowGitError:
                        identities[source] = ""
                if identities[source] == key:
                    stamp = datetime.fromisoformat(event["ts"]).timestamp()
                    choices.append((stamp, card, event.get("seq", 0), base["baseRef"]))
        seen, recent = set(), []
        for _, _, _, ref in sorted(choices, reverse=True):
            if ref in seen:
                continue
            seen.add(ref)
            try:
                # Skrót wybiera dzisiejszy SHA refu; historyczny SHA zostaje w dzienniku.
                commit = self.git.sha(repo, ref)
                base = self.git.resolve_base(repo, ref, commit)
            except FlowGitError:
                continue
            remote = ref.startswith("refs/remotes/")
            name = base["baseBranch"]
            recent.append({"ref": ref, "name": name, "commit": base["baseCommit"],
                           "kind": "remote" if remote else "local",
                           "remote": name.partition("/")[0] if remote else "", "selectable": True, "reason": ""})
            if len(recent) == 10:
                break
        return recent

    @staticmethod
    def _card_merge_blocks(st: FlowState, *, operation_id: str = "") -> list[tuple[str, str]]:
        """Jedna polityka domknięcia dla podglądu i wykonania rozpoczętej operacji."""
        reasons = []
        if baseproposal.blocked(st):
            reasons.append(("base-decision", "Najpierw rozstrzygnij pytanie o bazę karty; wyniki pozostają niescalone."))
        if not st.closed:
            reasons.append(("not-closed", "Karta nie ma aktualnego domknięcia."))
        if st.running() or st.open_consoles() or st.outside_started:
            reasons.append(("active-step", "Krok karty jest jeszcze czynny."))
        closed_seq = (st.closed or {}).get("seq", 0)
        if st.plan_revision or any((e or {}).get("seq", 0) > closed_seq
                                   for e in (st.plan_proposed, st.plan_approved)):
            reasons.append(("plan-change", "Zmiana planu czeka na zakończenie lub decyzję."))
        if st.base_change.get("seq", 0) > closed_seq:
            reasons.append(("base-change", "Po korekcie bazy karta wymaga aktualnego domknięcia."))
        pending = [i for i in st.open_intents() if not (
            operation_id and i.get("action") == "main-merge" and i.get("key") == operation_id)]
        if pending or st.merge_pending():
            reasons.append(("pending-action", "Akcja karty, korekta bazy lub scalanie ticketu nie zostały zakończone."))
        return reasons

    def request_card_base_change(self, card_id: str, request: dict) -> dict:
        """Jawny wybór użytkownika tworzy decyzję, bez zatrzymywania kroków i bez zmiany bazy."""
        from . import basechange
        if not isinstance(request, dict) or set(request) - {"baseRef", "baseCommit"}:
            raise FlowError("bad-request", "wybór użytkownika przyjmuje baseRef i baseCommit")
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        st = self.journal.state(card)
        self._require_base_context(card)
        if not st.started or st.card_finished:
            raise FlowError("no-order", "karta nie ma otwartego zlecenia")
        if baseproposal.pending(st):
            raise FlowError("base-decision", "najpierw rozstrzygnij istniejącą decyzję o bazie")
        try:
            new = basechange.candidate(self.git, self._repo(card), request)
        except (ValueError, FlowGitError) as exc:
            raise FlowError("bad-base", str(exc)) from None
        decision = "base-request:" + str(st.last_seq + 1)
        self.journal.append(card, "card.base-requested", decision=decision,
                            baseProposal={"valid": True, "ref": new["baseRef"], "baseCommit": new["baseCommit"],
                                          "evidence": "Jawny wybór użytkownika w pełnej karcie."})
        return {"ok": True, "id": card, "waiting": True, "changed": False,
                "preview": self.preview_card_base_change(card, {})}

    def _base_change_processes(self, st):
        """Odczyt procesów obejmuje także wynik zapisany przed końcem procesu."""
        from ..tasks import launch
        processes = []
        for name, step in st.steps.items():
            unit = step.get("unit") or ""
            unit_state = stepstop.unit_state(unit) if unit else ""
            process = launch.process_alive(step["processFile"]) if step.get("processFile") else False
            active = (name not in st.finished or unit_state not in ("", "inactive") or
                      process is not False or name in st.outside_started or
                      step.get("mode") == "interactive" and not step.get("consoleClosed"))
            processes.append({"step": name, "unit": unit, "state": unit_state, "active": active,
                              "previousOrder": name in st.superseded,
                              "processAlive": process, "finished": name in st.finished,
                              "worktree": step.get("worktree") or ""})
        return processes

    def _require_base_context(self, card):
        st = self.journal.state(card)
        if baseproposal.generation_blocked(st):
            raise FlowError("base-validation", "Generacja czeka na odzyskanie lub walidację kontekstu.")

    def preview_card_base_change(self, card_id: str, request: dict) -> dict:
        """Odcisk całej mapy i dowody kwalifikacji; brak zdarzeń i brak mutacji gita."""
        from . import basechange
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if not isinstance(request, dict) or set(request) - {"baseRef", "baseCommit", "step"}:
            raise FlowError("bad-request", "oczekiwano baseRef/baseCommit albo kroku propozycji")
        if "step" in request and not isinstance(request["step"], str):
            raise FlowError("bad-request", "krok propozycji musi być tekstem")
        events = self.journal.read(card)
        st = fold(events)
        asked = request.get("step") or next(iter(baseproposal.pending(st)), "")
        proposal = (st.base_request.get("baseProposal") if asked == st.base_request.get("decision") else
                    (st.finished.get(asked) or {}).get("baseProposal")) or {}
        wanted = dict(request)
        if "baseRef" not in wanted and proposal.get("valid"):
            wanted.update(baseRef=proposal["ref"], baseCommit=proposal["baseCommit"])
        repo = self._repo(card)
        try:
            with lock_for(repo):
                new = basechange.candidate(self.git, repo, wanted)
                processes = self._base_change_processes(st)
                result = basechange.preview(self.git, repo, card, events, st, new, processes)
                # Kontrolny odczyt używa tylko indeksu/statusu/metadanych, bez hashowania lokalnej treści.
                after = basechange.preview(self.git, repo, card, events, st,
                                           basechange.candidate(self.git, repo, wanted),
                                           self._base_change_processes(st))
                if result["previewId"] != after["previewId"] or events != self.journal.read(card):
                    result.update(canChange=False, previewId="")
                    result["blockedReasons"].append({"reason": "state-changed", "message":
                        "Stan zmienił się podczas odczytu. Ponów podgląd; stary identyfikator jest nieważny."})
        except (ValueError, FlowGitError) as exc:
            raise FlowError("bad-base", str(exc)) from None
        result["decisionStep"] = asked if asked in baseproposal.pending(st) else ""
        result["evidence"] = proposal.get("evidence") or "Jawny podgląd wybranej bazy."
        return result

    def preview_card_merge(self, card_id: str, request: dict) -> dict:
        """Odczyt bez zdarzeń w dzienniku, provisioningu ani potwierdzenia wykonania."""
        if not isinstance(request, dict) or set(request) - {"targetRef"}:
            raise FlowError("bad-request", "podgląd przyjmuje opcjonalny lokalny targetRef")
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        st = self.journal.state(card)
        branch = ticket_of(st)["cardBranch"]
        if not branch:
            raise FlowError("no-base", "brak gałęzi karty")
        from . import targetprep
        try:
            repo = self._repo(card)
            target_ref = request.get("targetRef")
            if "targetRef" in request and (not isinstance(target_ref, str) or
                    not target_ref.startswith("refs/heads/") or
                    not self.git.run(repo, "check-ref-format", target_ref).ok or
                    self.git.run(repo, "symbolic-ref", "--quiet", target_ref).ok):
                raise FlowError("bad-request", "cel musi być pełnym lokalnym refem refs/heads/…")
            base = base_of(st)
            remote_ref = base["baseRef"] if base["status"] == "known" and base["baseRef"].startswith("refs/remotes/") else ""
            if not target_ref:
                choices = targetprep.remote_choices(self.git, repo, base)
                target_ref = choices["defaultTargetRef"]
                if not target_ref and remote_ref:
                    raise FlowError("target-required", "Wybierz lokalny cel lub utworzenie gałęzi od zdalnej bazy.")
                target_ref = target_ref or "refs/heads/" + self.git.main_branch(repo)
            if not targetprep.ref_exists(self.git, repo, target_ref) and not remote_ref:
                raise FlowError("bad-request", "brak lokalnego celu i zdalnej bazy do jego utworzenia")
            preparation = targetprep.plan(self.git, repo, target_ref, remote_ref)
            preview = self.git.preview_merge(repo, branch, target_ref, preparation=preparation)
            preview["targetPreparation"] = preparation
            # Ponowny odczyt obejmuje nieistnienie refu/katalogu i zdalny SHA.
            if targetprep.plan(self.git, repo, target_ref, remote_ref) != preparation:
                preview.update(reason="state-changed", message="Stan celu zmienił się podczas odczytu.")
            if not request:
                self._main_branches[repo] = preview["targetBranch"]
        except FlowGitError as exc:
            raise FlowError("merge-preview", str(exc)) from None
        snapshots = preview.pop("_snapshot", None)
        reasons = []

        def block(reason, message):
            if not any(r["reason"] == reason for r in reasons):
                reasons.append({"reason": reason, "message": message})

        if preview["reason"]:
            block(preview["reason"], preview["message"])
        for reason, message in self._card_merge_blocks(st):
            block(reason, message)
        protected = self._protected_merge_targets(card, repo)
        for reason, message in self._merge_target_blocks(
                preview["sourceRef"], preview["targetRef"],
                "" if preparation["createWorktree"] else preview["targetWorktree"],
                bool(snapshots and snapshots["target"]["exists"]), protected):
            block(reason, message)
        if preparation["pathOccupied"] or preparation["parentSymlink"]:
            block("target-path-occupied", "Planowany katalog celu jest zajęty lub jego rodzic jest dowiązaniem.")
        if preparation["nameConflict"]:
            block("target-name-occupied", "Lokalna nazwa koliduje z istniejącą gałęzią. Wybierz inną nazwę lub lokalny cel.")
        if snapshots:
            source, target = snapshots["source"], snapshots["target"]
            if not source["exists"] or source_dirty(source):
                block("source-dirty", "Gałąź karty nie ma czystego checkoutu.")
            if any(any(s.get("operations", {}).values()) for s in (source, target)):
                block("git-operation", "W checkoucie trwa operacja gita lub indeks jest zablokowany.")
            if preview.get("localCollisionCount"):
                block("local-collision", "Lokalna praca koliduje ze ścieżkami scalenia.")
        else:
            block("local-unassessed", "Nie udało się potwierdzić aktualnego stanu checkoutów.")
        preview.update(blockedReasons=reasons, canMerge=not reasons, baseVersion=st.base_version)
        if reasons:
            preview.update(reason=reasons[0]["reason"], message=reasons[0]["message"])
        if snapshots and preview["reason"] != "state-changed":
            # To odcisk, nie zapis zgody. PT-204 ponownie odczyta stan przed rozpoczęciem operacji.
            context = {"card": card, "order": st.started, "baseVersion": st.base_version,
                       "journalSeq": st.last_seq, "preview": preview, "checkout": snapshots,
                       "protected": protected}
            payload = json.dumps(context, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
            preview["previewToken"] = hmac.new(self._preview_secret, payload, hashlib.sha256).hexdigest()
        return {"ok": True, "id": card, **preview}

    def merge_card(self, card_id: str, request: dict) -> dict:
        """Zgoda dotyczy dokładnego podglądu; rozpoczętą operację ponawiamy po jej ID."""
        if (not isinstance(request, dict) or set(request) - {"targetRef", "previewToken", "operationId"} or
                not all(isinstance(request.get(k), str) and request[k] for k in ("targetRef", "previewToken")) or
                ("operationId" in request and not isinstance(request["operationId"], str))):
            raise FlowError("bad-request", "scalenie wymaga targetRef i previewToken; operationId wskazuje rozpoczętą operację")
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        repo = self._repo(card)
        with lock_for(repo), self.journal.lock:
            st = self.journal.state(card)
            self._require_base_context(card)
            intent = self._main_merge_intent(st)
            if (intent and intent.get("previewToken") == request["previewToken"] and
                    intent["targetRef"] == request["targetRef"] and
                    request.get("operationId", intent["key"]) == intent["key"]):
                if baseproposal.pending(st):
                    raise FlowError("base-decision", "najpierw rozstrzygnij pytanie o bazę karty")
                if self._abandon_main_merge(card, intent):
                    raise FlowError("stale-preview", "Operacja została porzucona. Przygotuj nowy podgląd i potwierdź go.")
                self._resume_main_merge(card, intent)
                return {"ok": True, "id": card, **MergeWorktree.result(intent)}
            if request.get("operationId"):
                raise FlowError("stale-preview", "Nie ma takiej rozpoczętej operacji tego podglądu.")
            try:
                preview = self.preview_card_merge(card, {"targetRef": request["targetRef"]})
            except FlowError:
                raise FlowError("stale-preview", "Cel lub kontekst zmienił się. Przygotuj nowy podgląd i potwierdź go.") from None
            if not hmac.compare_digest(preview.get("previewToken", "").encode(), request["previewToken"].encode()):
                raise FlowError("stale-preview", "Podgląd jest nieaktualny. Przygotuj nowy podgląd i potwierdź go.")
            if not preview["canMerge"]:
                raise FlowError(preview["reason"], preview["message"])
            if intent or st.merged:
                raise FlowError("stale-preview", "Zlecenie ma już operację scalenia. Użyj jej ID albo nowego zlecenia.")
            self._merge_card_to_main_locked(card, repo, preview=preview)
            intent = self._main_merge_intent(self.journal.state(card))
            return {"ok": True, "id": card, **MergeWorktree.result(intent)}

    def merge_card_to_main(self, card_id: str) -> dict:
        """Jawna zgoda po domknięciu; ponowienie odnajduje operację tego samego zlecenia."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        intent = self._main_merge_intent(self.journal.state(card))
        repo = intent["repo"] if intent else self._repo(card)
        with lock_for(repo):
            return self._merge_card_to_main_locked(card, repo)

    def _main_merge_intent(self, st: FlowState) -> dict | None:
        return next((i for i in reversed(list(st.intents.values())) if i.get("action") == "main-merge" and
                     i.get("key") not in st.abandoned_main_merges and
                     i.get("requestSeq") == (st.started or {}).get("seq")), None)

    def _pending_main_merge(self, card: str) -> dict | None:
        events = self.journal.read(card)
        intent = self._main_merge_intent(fold(events))
        if not intent or any(e.get("type") == "main-merge.checkpoint" and e.get("key") == intent["key"] and
                             e.get("stage") == "projected" for e in events):
            return None
        return intent

    def _recover_main_merge(self, card: str) -> bool:
        intent = self._pending_main_merge(card)
        if not intent:
            return False
        try:
            with lock_for(intent["repo"]):
                # Równoległe MergeCardToMain mogło porzucić ten zamiar i zapisać nowy — czytamy pod blokadą.
                intent = self._pending_main_merge(card)
                if not intent or self._abandon_main_merge(card, intent):
                    return False
                self._resume_main_merge(card, intent)
        except FlowError:
            pass  # Jawne oczekiwanie jest już w dzienniku; brak pętli nowych zamiarów.
        return True

    def _result_in_target(self, intent: dict, current: str) -> bool:
        """Ref celu wskazuje wynik albo jego potomka: git tej operacji jest zakończony."""
        return bool(current) and (current == intent["resultCommit"] or
                                  self.git.run(intent["repo"], "merge-base", "--is-ancestor",
                                               intent["resultCommit"], current).ok)

    def _abandon_main_merge(self, card: str, intent: dict) -> bool:
        """Jawna decyzja użytkownika zamyka zamiar, którego wynik nie trafił do refu celu.

        Obcy ref (np. commit użytkownika na głównej) nie jest zgodą na wykonanie, ale decyzja „Do poprawy”, zwrot
        planu, nowe domknięcie albo ręczne Gotowe/Archiwum kończy oczekiwanie. Zapisujemy tylko porzucenie —
        bez dotykania plików, indeksu i refu. Ewentualna materializacja zostaje w checkoutcie; nowy zamiar
        odziedziczy ją wyłącznie przy niezmienionym refie (MergeWorktree.prepare)."""
        st = self.journal.state(card)
        returned = any(e["seq"] > intent["seq"] and (e["type"] in ("plan.returned", "card.closed") or
                       (e["type"] == "card.moved" and e.get("to") in ("todo", "done", "archived")))
                       for e in self.journal.read(card))
        if not returned or ("main-merge", intent["key"]) in st.results:
            return False
        current = self.git.sha(intent["repo"], intent["targetRef"])
        if self._result_in_target(intent, current):
            return False  # Ref już przesunięty: recovery dokańcza wynik i projekcję, nie porzuca.
        reason = ("decyzja użytkownika przed przesunięciem refu celu" if current == intent["targetCommitBefore"]
                  else "decyzja użytkownika przy obcym refie celu — bez wykonania i bez nadpisania")
        self.journal.append(card, "main-merge.abandoned", key=intent["key"], reason=reason)
        return True

    def _merge_card_to_main_locked(self, card: str, repo: str, *, preview: dict | None = None) -> dict:
        legacy = preview is None
        st = self.journal.state(card)
        if baseproposal.blocked(st):
            raise FlowError("base-decision", "najpierw rozstrzygnij pytanie o bazę karty")
        intent = self._main_merge_intent(st)
        if intent and self._abandon_main_merge(card, intent):
            st = self.journal.state(card)
            intent = None
        if intent:
            already = bool(st.merged)
            self._resume_main_merge(card, intent)
            return {"ok": True, "id": card, "main": intent["targetBranch"], "already": already}
        if self._card_merge_blocks(st):
            raise FlowError("not-closed", "karta nie jest jeszcze gotowa do scalenia")
        info = ticket_of(st)
        if st.merged:
            # Historyczny sukces bez zamiaru: cel pochodzi ze zdarzenia, nigdy z dzisiejszej konfiguracji.
            self._project_main_merge(card, st, operation_id=st.merged.get("operationId") or "")
            return {"ok": True, "id": card, "main": st.merged["targetBranch"], "already": True}
        main = preview["targetBranch"] if preview is not None else self.git.main_branch(repo)
        if not main or not info["cardBranch"]:
            raise FlowError("no-base", "brak gałęzi głównej albo gałęzi karty")
        if preview is not None:
            # Podgląd czyta rekordy NUL i zachowuje również ścieżki z tabulatorem lub nową linią.
            main_tree = preview["targetWorktree"] if preview["targetWorktreeExists"] else ""
        else:
            main_tree = self.git.branch_checkouts(repo).get(f"refs/heads/{main}", "")
        if not main_tree:
            # Stare API ma własną jawną zgodę, ale używa tego samego planu zasobów i recovery.
            if preview is None:
                preview = self.preview_card_merge(card, {"targetRef": "refs/heads/" + main})
                if not preview["canMerge"]:
                    raise FlowError(preview["reason"], preview["message"])
            main_tree = preview["targetWorktree"]
        try:
            tip = self.git.clean_tip(repo, info["cardBranch"], info["cardWorktree"])
            if preview is not None and preview.get("targetPreparation", {}).get("createWorktree"):
                prepared = MergeWorktree.prepare_fresh(self.git, repo, preview, f"Merge card {card} into {main}")
            else:
                prepared = MergeWorktree(self.git, repo, main, main_tree).prepare(
                    f"refs/heads/{info['cardBranch']}", tip, f"Merge card {card} into {main}",
                    abandoned=next((i for i in reversed(list(st.intents.values()))
                                    if i.get("action") == "main-merge" and i["key"] in st.abandoned_main_merges and
                                    i.get("requestSeq") == (st.started or {}).get("seq") and
                                    i.get("targetRef") == f"refs/heads/{main}"), None))
        except FlowGitError as exc:
            if preview is not None:
                raise FlowError("stale-preview", "Stan przygotowania zmienił się. Potwierdź nowy podgląd: " + str(exc)) from None
            raise FlowError("main-merge", str(exc)) from None
        if preview is not None and any(prepared[k] != preview[v] for k, v in (
                ("sourceCommit", "sourceCommit"), ("targetCommitBefore", "targetCommit"),
                ("targetRef", "targetRef"), ("targetWorktree", "targetWorktree"), ("resultTree", "resultTree"))):
            raise FlowError("stale-preview", "Stan zmienił się podczas przygotowania. Potwierdź nowy podgląd.")
        if preview is not None:
            # Przygotowanie na kopii indeksu nie zmienia odcisku zgody. Wykrywamy także edycje użytkownika
            # między ponownym podglądem a zakończeniem przygotowania, zanim zapiszemy zamiar i pliki.
            try:
                latest = self.preview_card_merge(card, {"targetRef": preview["targetRef"]})
            except FlowError:
                raise FlowError("stale-preview", "Cel lub kontekst zmienił się podczas przygotowania. Potwierdź nowy podgląd.") from None
            if latest.get("previewToken") != preview["previewToken"]:
                raise FlowError("stale-preview", "Stan lokalny zmienił się podczas przygotowania. Potwierdź nowy podgląd.")
        ident = uuid.uuid4().hex
        intent = self.journal.append(card, "intent", action="main-merge", key=ident, operationId=ident,
                                     requestSeq=(st.started or {}).get("seq"), baseVersion=st.base_version,
                                     repo=repo, sourceWorktree=info["cardWorktree"],
                                     **({"previewToken": preview["previewToken"]} if not legacy else {}), **prepared)
        self._resume_main_merge(card, intent)
        return {"ok": True, "id": card, "main": main, "already": False}

    def _resume_main_merge(self, card: str, intent: dict) -> None:
        """Wspólna trwała operacja dla starej zgody i przyszłych adapterów, pod lock_for(repo)."""
        key = intent["key"]
        stages = {e.get("stage") for e in self.journal.read(card)
                  if e["type"] == "main-merge.checkpoint" and e.get("key") == key}

        def checkpoint(stage, **data):
            if stage not in stages:
                self.journal.append(card, "main-merge.checkpoint", key=key, stage=stage, **data)
                stages.add(stage)

        try:
            st = self.journal.state(card)
            if st.card_finished and st.merged and st.merged.get("operationId") == key:
                checkpoint("projected")
                return
            checkpoint("prepared")
            branch = intent["sourceRef"][len("refs/heads/"):]
            if self.git.run(intent["repo"], "symbolic-ref", "--quiet", intent["targetRef"]).ok:
                raise FlowGitError("cel stał się refem symbolicznym — scalenie czeka")
            current = self.git.sha(intent["repo"], intent["targetRef"])
            if intent.get("targetPreparation", {}).get("createRef") and current:
                owner = "refs/tide/merge-targets/" + key
                if (self.git.run(intent["repo"], "symbolic-ref", "--quiet", owner).ok or
                        self.git.sha(intent["repo"], owner) != intent["targetCommitBefore"]):
                    raise FlowGitError("obcy ref celu — brak dowodu własności operacji")
            # Przy already ani wynik, ani jego potomek nie dowodzi wykonania obiecanego etapu zasobów.
            # Trwałe ref-moved dowodzi zakończenia tego etapu także bez zmiany SHA, przed zwolnieniem blokady.
            needs_worktree = (intent.get("already") and
                              intent.get("targetPreparation", {}).get("createWorktree") and "ref-moved" not in stages)
            if self._result_in_target(intent, current) and not needs_worktree:
                tree = self.git.run(intent["repo"], "rev-parse", "--verify", f"{intent['resultCommit']}^{{tree}}")
                if not tree.ok or tree.out.strip() != intent["resultTree"]:
                    raise FlowGitError("obiekt wyniku nie odpowiada zapisanemu zamiarowi")
                # Ref (wynik albo jego potomek po dalszych commitach użytkownika) dowodzi zakończenia gita także
                # po awarii przed checkpointem. Polityka gałęzi głównej dotyczy tylko wykonania przed przesunięciem
                # refu. Źródło, worktree i indeks mogą już zawierać dalszą pracę — projekcja ich nie dotyka.
                checkpoint("materialized")
                checkpoint("ref-moved")
                result = MergeWorktree.result(intent)
            else:
                if not intent.get("previewToken") and intent["targetRef"] != f"refs/heads/{self.git.main_branch(intent['repo'])}":
                    raise FlowGitError("gałąź główna zmieniła się po przygotowaniu operacji — scalenie czeka")
                if (not st.closed or st.plan_revision or st.base_version != intent["baseVersion"] or
                        (st.started or {}).get("seq") != intent["requestSeq"]):
                    raise FlowGitError("kontekst karty zmienił się po przygotowaniu operacji — scalenie czeka")
                blocks = self._card_merge_blocks(st, operation_id=key)
                if blocks:
                    raise FlowGitError(blocks[0][1])
                if intent.get("previewToken"):
                    blocks = self._merge_target_blocks(
                        intent["sourceRef"], intent["targetRef"],
                        ("" if intent.get("targetPreparation", {}).get("createWorktree") and
                         not os.path.isdir(intent["targetWorktree"]) else intent["targetWorktree"]),
                        os.path.isdir(intent["targetWorktree"]), self._protected_merge_targets(card, intent["repo"]))
                    if blocks:
                        raise FlowGitError(blocks[0][1])
                if self.git.clean_tip(intent["repo"], branch, intent["sourceWorktree"]) != intent["sourceCommit"]:
                    raise FlowGitError("gałąź karty zmieniła się po przygotowaniu operacji")
                from .targetprep import provision
                provision(self.git, intent, self.journal.read(card), checkpoint)
                worktree = MergeWorktree(self.git, intent["repo"], intent["targetBranch"], intent["targetWorktree"])
                result = worktree.execute(intent, checkpoint)
            from .targetprep import release
            release(self.git, intent)
            if st.merge_waiting:
                self.journal.append(card, "main-merge.resumed", key=key)
            if ("main-merge", key) not in st.results:
                self.journal.append(card, "result", action="main-merge", key=key, ok=True,
                                    commit=result["resultCommit"], **result)
            checkpoint("result-saved")
            st = self.journal.state(card)
            if not st.merged:
                self.journal.append(card, "card.merged", branch=branch,
                                    commit=result["resultCommit"], **result)
            self._project_main_merge(card, self.journal.state(card), operation_id=key)
            checkpoint("projected")
        except (FlowGitError, BoardError, OSError, FlowError) as exc:
            waiting = self.journal.state(card).merge_waiting or {}
            if waiting.get("key") != key or waiting.get("reason") != str(exc):
                self.journal.append(card, "main-merge.waiting", key=key, reason=str(exc))
            raise FlowError("main-merge", str(exc)) from None

    def _project_main_merge(self, card: str, st: FlowState, *, operation_id: str = "") -> None:
        # Recovery zapisuje wynik gita, ale nie odwraca późniejszego ręcznego zakończenia w Archiwum.
        if st.card_finished and st.moved_to == "archived":
            return
        # BoardMonitor.sync() opróżnia tę samą kolejkę commitów Tablicy pod blokadą dziennika.
        with self.journal.lock:
            try:
                item = self.store.load().cards.get(card_num(card))
                if item is None:
                    raise FlowError("no-card", "nie ma karty do scalenia")
                if item.column != "done":
                    self.store.move_card(card, "done", -1, from_column="review")
            except BoardError as exc:
                raise FlowError(exc.code, str(exc)) from None
            if not st.card_finished:
                self.journal.append(card, "card.finished", column="done", **({"operationId": operation_id} if operation_id else {}))

    def _advance_close(self, card: str, st: FlowState) -> bool:
        """Po ostatnim tickecie tworzy podsumowanie; True tylko po wykonaniu etapu, False przy pominięciu.
        Sam brak wyjątku przy pominięciu (np. trwa opiekun) nie oznacza rozwiązania błędu domknięcia."""
        if baseproposal.blocked(st):
            return False
        if (st.started is None or st.card_finished or st.closed or st.paused or st.plan_revision or
                st.moved_to == "todo" or st.open_intents()):
            return False
        if st.plan_approved:
            rows = st.plan_approved.get("rows") or []
            if not rows or not all((st.results.get(("merge", row["id"])) or {}).get("ok") or
                                   row["id"] in st.rejected or row["id"] in st.imported_done for row in rows):
                return False
            if any(row["id"] not in st.rejected and row["id"] not in st.imported_done and
                   not (st.results.get(("cleanup", row["id"])) or {}).get("ok") for row in rows):
                return False
            tickets = [{"id": row["id"], "state": "odrzucone" if row["id"] in st.rejected else "gotowe"}
                       for row in rows]
            executor = (st.plan_proposed or {}).get("planner") or rows[0]["executor"]
        elif st.started.get("entry") == "now":
            ticket = next((s.get("ticket") for s in st.steps.values() if s.get("kind") == "ticket"), "")
            if not ticket or not ((st.results.get(("merge", ticket)) or {}).get("ok") or ticket in st.rejected):
                return False
            if ticket not in st.rejected and not (st.results.get(("cleanup", ticket)) or {}).get("ok"):
                return False
            tickets = [{"id": ticket, "state": "odrzucone" if ticket in st.rejected else "gotowe"}]
            executor = st.started.get("executor") or ""
        else:
            return False
        executor = (st.author_overrides.get(CLOSE_AUTHOR) or {}).get("executor") or executor
        last_return = max((int(e.get("seq") or 0) for e in self.journal.read(card)
                           if e.get("type") == "plan.returned"), default=0)
        last_return = max(last_return, st.base_change.get("seq", 0))
        closings = [s for s in st.steps.values() if s.get("kind") == "close" and
                    int(s.get("seq") or 0) > last_return]
        if closings:
            latest = closings[-1]
            finished = st.finished.get(latest["step"]) or {}
            if finished.get("result") == "done":
                path = os.path.join(self.store.root, self.journal.rel_dir(card), "steps", latest["step"], handoff.SUMMARY_FILE)
                with open(path, encoding="utf-8") as fh:
                    summary = fh.read(OUTPUT_MAX)
                if (st.started or {}).get("check") == "manual":
                    summary += handoff.desk_section(self._desk_items(card, st))
                self.journal.append(card, "card.closed", step=latest["step"], summary=summary,
                                    check=(st.started or {}).get("check") or "auto",
                                    baseVersion=st.base_version, baseCommit=st.base.get("baseCommit", ""))
                if st.plan_approved and not st.plan_finished:
                    self.journal.append(card, "plan.finished", merged=sum(1 for t in tickets if t["state"] == "gotowe"),
                                        rejected=sum(1 for t in tickets if t["state"] == "odrzucone"))
                return True
            return False
        if st.running():
            return False
        item, project = self._project(card)
        info = ticket_of(st)
        if not info["cardWorktree"]:
            raise FlowError("no-card-worktree", "brak worktree karty po zakończeniu ticketów")
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        try:
            selected = execution.resolve(executor, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        step_id = self._next_step_id(card, "", st)
        out = info["cardWorktree"] + f".close-{step_id}.step/out"
        prompt = handoff.close_card(card_id=card, title=item.title, description=item.description,
                                    worktree=info["cardWorktree"], out_dir=out, tickets=tickets,
                                    manual=(st.started or {}).get("check") == "manual")
        self._launch(item, project, selected, "", {"branch": info["cardBranch"]},
                     {"branch": info["cardBranch"], "worktree": info["cardWorktree"]},
                     step_id, [], kind="close", prompt_text=prompt)
        return True
