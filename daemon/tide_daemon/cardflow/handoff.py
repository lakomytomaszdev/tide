"""Kartka przekazania kroku ticketu przy wejściu „Od razu”: zbudowana z karty (tytuł, opis, zrzuty), z kontraktem
kroku (gdzie pisać, czego nie robić, plik wyniku). Idzie do programu jako polecenie na stdin (agentcmd.Background)."""
from __future__ import annotations
from .. import resources
from . import testpolicy
from .testpolicy import TestPolicy

import json
import os
import re

RESULT_FILE = "result.json"
NOTE_FILE = "note.md"
REVIEW_FILE = "review.md"
SUMMARY_FILE = "summary.md"
OPTIONS_HINT = ('Do pytania możesz dodać "options": [{"title": "…", "effect": "…", "recommended": true}] — '
                "1–4 gotowe odpowiedzi, najwyżej jedna polecona; Tide sam dokłada „Inną odpowiedź…”.")
DESK_HEADING = "Do sprawdzenia na pulpicie"
DESK_HINT = (f"Test wymagający prawdziwego pulpitu oznacz w notce w sekcji „{DESK_HEADING}” (nagłówek, pod nim lista "
             "punktów: co zrobić i czego oczekiwać), nie proś o to użytkownika w trakcie. Tide zbierze te punkty "
             "do listy ręcznych testów w domknięciu karty.")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.*\S)\s*$")


def desk_items(note: str) -> list[str]:
    """Pozycje sekcji „Do sprawdzenia na pulpicie” z notki kroku. Sekcja kończy się na następnym nagłówku;
    pozycja może mieć kolejne wiersze (wcięte albo bez pustego wiersza przed nimi); bloki ``` są pomijane,
    pusta sekcja daje pustą listę."""
    items: list[str] = []
    inside = False
    fenced = False
    blank = False
    for line in (note or "").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        head = _HEADING.match(line)
        if head:
            inside = head.group(1).strip().strip("*_: ").casefold() == DESK_HEADING.casefold()
            continue
        if not inside:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            items.append(bullet.group(1).strip())
            blank = False
        elif not line.strip():
            blank = True
        else:
            if items and (not blank or line[:1] in (" ", "\t")):
                items[-1] += " " + line.strip()
            blank = False
    return items


def desk_section(found: list[tuple[str, str, list[str]]]) -> str:
    """Lista do domknięcia: `found` to (krok, ticket, pozycje) w kolejności kroków. Ta sama pozycja
    (bez względu na wielkość liter i odstępy) pojawia się raz, ze wszystkimi odwołaniami."""
    merged: dict[str, tuple[str, list[str]]] = {}
    for step, ticket, items in found:
        ref = f"krok {step}, ticket {ticket}" if ticket else f"krok {step}"
        for item in items:
            key = " ".join(item.split()).casefold()
            text, refs = merged.setdefault(key, (" ".join(item.split()), []))
            if ref not in refs:
                refs.append(ref)
    if not merged:
        return ""
    lines = [f"- {text} ({'; '.join(refs)})" for text, refs in merged.values()]
    return f"\n\n### {DESK_HEADING} (z notek kroków)\n" + "\n".join(lines) + "\n"


RERUN_MODULES = ("Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\n"
                 "różny od 0) przez uruchamiacz projektu — ta sama pula maszyny i izolacja stanu co w pełnym przebiegu:\n"
                 "`/usr/bin/python3 tools/test_runner.py --modules <ścieżki modułów>`. Tide robi to także sam, gdy prosisz\n"
                 "o to samo pełne polecenie na tym samym HEAD.")
RERUN_RUNNER = ("Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\n"
                "różny od 0), każdy tym samym interpreterem co `make test` i we własnym katalogu stanu:\n"
                "`d=\"$(mktemp -d)\" && XDG_STATE_HOME=\"$d\" /usr/bin/python3 -m unittest discover -t . -s <katalog modułu> -p <plik modułu>`\n"
                "(test bez izolacji nadpisał kiedyś stan działającej Tablicy).")
RERUN_GENERIC = ("Uruchom tylko testy (albo projekty testowe), które padły, narzędziem testów projektu z filtrem\n"
                 "na ich nazwy — tym samym poleceniem i środowiskiem co pełny przebieg.")


def rerun_failed(worktree: str = "", command: str | None = None) -> str:
    """Etap A (06.11: 31% czasu testów to powtórki całego zestawu na tym samym kodzie). Przepis zależy od
    projektu: repozytorium z `tools/test_runner.py` dostaje dokładne polecenie, inne technologie — zasadę.
    `command` — obowiązujące polecenie kartki (PT-279): przepis uruchamiacza Tide tylko dla `make test`
    i `make test-changed`; inne polecenie (np. `dotnet test …`) albo zasada ogólna (`""`) — zasada ponowienia."""
    from .testrerun import runner_reruns
    runner = not worktree or os.path.isfile(os.path.join(worktree, "tools", "test_runner.py"))
    if command is not None and testpolicy.make_target(command) not in (testpolicy.FULL_TARGET,
                                                                       testpolicy.CHANGED_TARGET):
        runner = False
    how = (RERUN_MODULES if runner_reruns(worktree) else RERUN_RUNNER) if runner else RERUN_GENERIC
    return f"""Ponowienie bez zmian kodu: gdy przebieg padł, a od jego commita nie zmieniasz żadnego pliku (podejrzenie
niestabilności lub obciążenia), nie powtarzaj całego zestawu. {how}
W notce zapisz pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, ścieżka raportu) i ponowienie
(ten sam HEAD, czysty `git status`, lista ponowionych testów, wynik). Gdy zmieniasz jakikolwiek plik, obowiązuje
pełne polecenie z akapitu wyżej. Ponowienie zmienia tylko to, co uruchamiasz, nie ocenę: porażka pierwszego
przebiegu zostaje wynikiem, a udane ponowienie jej nie zastępuje. Rozlicz ją jak każde odstępstwo
według zasad wyjątków od testów projektu (w Tide: `docs/testing.md`, „Wyjątki od testów”): ten sam test na bazie
ticketu i na głównej gałęzi projektu, obciążenie, zakres, właściciel.
"""


OLD_BRANCH_TESTS = """Testy ticketu (także po poprawkach): ta gałąź karty powstała przed celem `make test-changed`
i go nie ma. Obowiązuje dla niej dotychczasowa zasada: przed oddaniem uruchom raz pełny `make test` i zapisz
polecenie oraz wynik w notce. Nie przenoś infrastruktury testów z master i nie twórz zamiennika celu.
"""


def sandbox_tests(full: str, worktree: str = "", command: str | None = None) -> str:
    """08.11: kroki uruchamiały pełny przebieg w piaskownicy (moduły padały bez D-Bus, zapisu do /tmp
    i zagnieżdżonego bwrap), a potem drugi raz poza nią — obciążenie maszyny 40–58 na 16 rdzeniach.
    `full` to wymagany pełny przebieg w formie zdania („`make test-changed BASE=…`”, „pełny `make test`”).
    `command` — obowiązujące polecenie z polityki testów (K-69), jak w `rerun_failed`: zestawy celowane Tide
    tylko przy `make test`/`make test-changed`; inne polecenie albo zasada ogólna (`""`) — zasada ogólna."""
    from .testrerun import runner_reruns
    known = not worktree or not os.path.isdir(worktree)
    tide = command is None or testpolicy.make_target(command) in (testpolicy.FULL_TARGET, testpolicy.CHANGED_TARGET)
    if tide and (known or os.path.isfile(os.path.join(worktree, "tools", "test_runner.py"))):
        targeted = "`make test-cardflow`, `make test-daemon`, `make test-board`"
        targeted += (" albo `/usr/bin/python3 tools/test_runner.py --modules <moduły>`"
                     if known or runner_reruns(worktree) else " albo inny zestaw `make test-<zestaw>`")
    else:
        targeted = "narzędziem testów projektu z filtrem na zmieniony obszar"
    return f"""W piaskownicy kroku uruchamiaj tylko testy celowane ({targeted}).
Wymagany {full} zgłoś od razu jako polecenie poza piaskownicą (wynik `outside`),
bez wcześniejszej próby pełnego przebiegu w piaskownicy: część modułów (D-Bus, zapis do /tmp, zagnieżdżona
piaskownica) i tak tam pada, a drugi przebieg poza piaskownicą tylko dociąża maszynę.
"""


SOURCE_LABELS = {
    testpolicy.PROFILE: "zatwierdzony kontrakt testów projektu (profil projektu na Tablicy)",
    testpolicy.DETECTED: "wykryte z Makefile",
    testpolicy.RULES: "zasady projektu (CLAUDE.md/AGENTS.md) — brak zatwierdzonego kontraktu i celu make",
}
# Zasada ogólna przy zastosowanym kontrakcie: przyczyna zależy od pola (review PT-279, runda 1, NIT 1).
RULES_NO_FIELD = "zasady projektu (CLAUDE.md/AGENTS.md) — zatwierdzony kontrakt nie podaje tego polecenia"
RULES_MISSING_TARGET = ("zasady projektu (CLAUDE.md/AGENTS.md) — ta gałąź nie ma celu make "
                        "z zatwierdzonego kontraktu")
UNAPPROVED_LINE = ("Kontrakt testów w profilu projektu nie jest zatwierdzony i nie jest stosowany: jego polecenia "
                   "nie są wymogiem tego kroku.\n")
RULES_CHANGED = ("Testy ticketu (także po poprawkach): projekt nie podaje polecenia testów zmian. Przed oddaniem "
                 "uruchom testy dobrane do zmian\nwedług zasad projektu (CLAUDE.md/AGENTS.md); ")
RULES_FULL = "pełne testy według zasad projektu (CLAUDE.md/AGENTS.md)"
# Wymagany przebieg zasady ogólnej w zdaniu `sandbox_tests` („Wymagany … zgłoś od razu …”).
RULES_SANDBOX = "przebieg testów dobranych do zmian według zasad projektu (CLAUDE.md/AGENTS.md)"


def _tests_policy(policy: TestPolicy | None, worktree: str, base: str) -> TestPolicy:
    """Do czasu #10 silnik nie podaje polityki: kartka liczy ją regułą zgodności z master."""
    return policy if policy is not None else testpolicy.master_policy(worktree, base)


def source_line(source: str, *, close: bool = False, label: str = "") -> str:
    """Jawna linia źródła polecenia (PT-279). Źródło `compat` (zgodność z master bez potwierdzenia celem) — bez linii.
    `label` zastępuje domyślny opis źródła (rzeczywista przyczyna zasady ogólnej)."""
    label = label or SOURCE_LABELS.get(source)
    if not label:
        return ""
    return f"Źródło polecenia {'pełnych testów' if close else 'testów ticketu'}: {label}.\n"


def _source_label(policy: TestPolicy, *, close: bool) -> str:
    """Przyczyna zasady ogólnej dla danego pola, gdy kontrakt jest zastosowany; inaczej opis domyślny."""
    source = policy.full_source if close else policy.changed_source
    if source != testpolicy.RULES or not (policy.declared_changed or policy.declared_full):
        return ""
    return RULES_MISSING_TARGET if ("full" if close else "changed") in policy.missing else RULES_NO_FIELD


def _notes(policy: TestPolicy, *, close: bool = False) -> str:
    out = source_line(policy.full_source if close else policy.changed_source, close=close,
                      label=_source_label(policy, close=close))
    if policy.unapproved:
        out += UNAPPROVED_LINE
    if close and "full" in policy.missing:
        out += (f"Zatwierdzony kontrakt podaje `{policy.declared_full}`, ale ta gałąź nie ma celu "
                f"`{testpolicy.FULL_TARGET}` — obowiązuje zasada projektu.\n")
    if not close and "changed" in policy.missing:
        out += (f"Zatwierdzony kontrakt podaje `{policy.declared_changed}`, ale ta gałąź nie ma celu "
                f"`{testpolicy.CHANGED_TARGET}` ani `{testpolicy.FULL_TARGET}` — obowiązuje zasada projektu.\n")
    if not close and policy.legacy and policy.declared_changed:
        out += (f"Zatwierdzony kontrakt podaje `{policy.declared_changed}`, ale ta gałąź nie ma celu "
                f"`{testpolicy.CHANGED_TARGET}` — obowiązuje dotychczasowa zasada pełnego `make test`.\n")
    return out


def _full_phrases(policy: TestPolicy) -> tuple[str, str, str]:
    """(mianownik, dopełniacz, krótka nazwa) pełnego przebiegu do zdań kartki."""
    if policy.full:
        return f"pełny `{policy.full}`", f"pełnego `{policy.full}`", f"`{policy.full}`"
    return "pełny przebieg testów", "pełnego przebiegu testów", "pełnego przebiegu testów"


def ticket_tests(base: str = "", worktree: str = "", policy: TestPolicy | None = None, *,
                 author: bool = True) -> str:
    """Zasada testów ticketu, poprawek i review z polityki testów (bez polityki — reguła zgodności z master).
    `author=False` (kartka review): zasada bez instrukcji uruchamiania przebiegu — recenzent sprawdza wynik
    autora i nie zgłasza własnego pełnego przebiegu (08.11, review Astry)."""
    policy = _tests_policy(policy, worktree, base)
    sandbox = sandbox_tests if author else (lambda _full, _worktree="", _command=None: "")
    head = _notes(policy)
    if policy.legacy:
        return head + OLD_BRANCH_TESTS + sandbox(f"pełny `{policy.changed}`", worktree, policy.changed) + \
            rerun_failed(worktree, policy.changed)
    full_nom, full_gen, full_short = _full_phrases(policy)
    make = testpolicy.make_target(policy.changed) == testpolicy.CHANGED_TARGET
    if policy.changed:
        text = f"Testy ticketu (także po poprawkach): autor przed oddaniem uruchamia `{policy.changed}`.\n"
        param = "BASE" if make else "Baza w poleceniu"
        if policy.base_pending:
            text += (f"`{testpolicy.BASE_PARAM}` to przypięty punkt startu ticketu na gałęzi karty (nie bieżący "
                     "wierzchołek karty ani master);\npodstaw jego SHA przed uruchomieniem.\n")
        elif policy.base:
            text += f"{param} to przypięty punkt startu ticketu na gałęzi karty, a nie bieżący wierzchołek karty ani master.\n"
        text += "Zapisz polecenie i wynik w notce. "
    else:
        text = RULES_CHANGED + "zapisz polecenie i wynik w notce. "
    text += (f"{full_nom[0].upper() + full_nom[1:]} nie jest kryterium oddania ticketu ani jego review;\n"
             f"jest wykonywany raz przy domknięciu karty. Ta zasada zastępuje wymóg {full_gen} zapisany\n"
             "w kartkach istniejących planów (w tym K-37, K-38 i K-55); nie przepisuj tych planów.\n")
    if make:
        text += f"Jeśli `make test-changed` wybierze pełny zestaw z mapy zmian, wykonaj go bez dodatkowego {full_short}.\n"
    required = f"`{policy.changed}`" if policy.changed else RULES_SANDBOX
    return head + text + sandbox(required, worktree, policy.changed) + rerun_failed(worktree, policy.changed)


def review_tests(policy: TestPolicy) -> str:
    """Punkty recenzenta o testach: w `legacy` wymóg pełnego `make test` w notce (jedyny wyjątek), w każdym innym
    wariancie — polecenie do sprawdzenia w notce i zakaz żądania pełnego przebiegu."""
    if policy.legacy:
        return ("- sprawdź w notce wynik pełnego `make test` (ta gałąź karty nie ma jeszcze `make test-changed`);\n"
                "  nie uruchamiaj go ponownie na potrzeby review,\n")
    _, full_gen, _ = _full_phrases(policy)
    if testpolicy.make_target(policy.changed) == testpolicy.CHANGED_TARGET:
        check = "że `make test-changed` z poniższą bazą przeszedł dla oddawanych zmian"
    elif policy.changed:
        check = f"że polecenie z zasady testów niżej (`{policy.changed}`) przeszło dla oddawanych zmian"
    else:
        check = ("polecenie testów dobranych do zmian według zasad projektu i jego udany wynik dla oddawanych "
                 "zmian")
    return (f"- sprawdź w notce, {check};\n"
            f"  nie żądaj osobnego {full_gen} i nie uruchamiaj go ponownie na potrzeby review,\n")


def close_tests(worktree: str, policy: TestPolicy | None = None) -> str:
    """Pełne testy domknięcia (K-62: raz, porażka → error) z polityki testów."""
    policy = _tests_policy(policy, worktree, "")
    run = f"pełny `{policy.full}`" if policy.full else RULES_FULL
    again = f"`{policy.full}`" if policy.full else "przebiegu"
    return _notes(policy, close=True) + f"""Przed napisaniem podsumowania uruchom raz {run} w worktree gałęzi karty, po scaleniu ticketów.
{run[0].upper() + run[1:]} zgłoś od razu jako polecenie poza piaskownicą (wynik `outside`),
bez wcześniejszej próby pełnego przebiegu w piaskownicy kroku.
Zapisz polecenie i wynik w podsumowaniu. Jeśli testy nie przejdą, nie zgłaszaj done i nie powtarzaj całego
{again} na tym samym kodzie: dowody do rozliczenia zbierz ponowieniem tylko padniętych modułów (kontrakt
outside) według zasady niżej, a potem zapisz wynik error z opisem niepowodzeń i tym rozliczeniem, jak przy innych
problemach domknięcia. O przyjęciu odstępstwa decyduje użytkownik.
{rerun_failed(worktree, policy.full)} Gdy polecenie wymaga zgody lub wykonania
poza piaskownicą, użyj kontraktu denied albo outside i poczekaj na wznowienie; nie powtarzaj udanego przebiegu
bez nowych zmian.
"""


CURRENT_TESTS_OPEN = "<TIDE_CURRENT_TESTS>"
CURRENT_TESTS_CLOSE = "</TIDE_CURRENT_TESTS>"
CURRENT_TESTS_HEAD = ("Obowiązujące testy (aktualna polityka testów tego kroku, policzona przy tym wznowieniu; "
                      "zastępuje zasadę testów zapisaną wyżej w tej kartce):\n")
_CURRENT_TESTS = re.compile(r"\n*" + re.escape(CURRENT_TESTS_OPEN) + r".*?" + re.escape(CURRENT_TESTS_CLOSE) + r"\n?",
                            re.DOTALL)


def current_tests(policy: TestPolicy, *, kind: str, worktree: str = "") -> str:
    """Sekcja „Obowiązujące testy” kartki wznowienia (PT-287): ten sam tekst polityki co kartka startu danego
    rodzaju kroku, z aktualnej polityki. Blok ma znaczniki, więc kolejne wznowienie go zastępuje, a nie dokłada."""
    if kind == "close":
        body = close_tests(worktree, policy)
    elif kind == "conflict":
        body = conflict_tests(policy, worktree)
    elif kind == "review":
        body = ("Dla recenzenta:\n" + review_tests(policy) + "\n" +
                ticket_tests(policy.base, worktree, policy, author=False))
    else:
        body = ticket_tests(policy.base, worktree, policy)
    return f"\n\n{CURRENT_TESTS_OPEN}\n{CURRENT_TESTS_HEAD}{body}{CURRENT_TESTS_CLOSE}\n"


def strip_current_tests(prompt: str) -> str:
    """Kartka bez sekcji „Obowiązujące testy” z poprzedniego wznowienia."""
    return _CURRENT_TESTS.sub("\n", prompt)


CONFLICT_DONE = ("Nie zapisuj wyniku done bez udanego przebiegu tych testów. Poprawki po testach zacommituj na gałęzi "
                 "karty\n(worktree karty ma zostać czyste); gdy testy padają, a nie umiesz ich bezpiecznie naprawić, "
                 "zapisz pytanie\nalbo error z opisem niepowodzeń.\n")


def conflict_tests(policy: TestPolicy, worktree: str = "") -> str:
    """Testy po rozwiązaniu konfliktu scalenia (K-69, PT-284) — nowe zachowanie: wcześniej kartka mówiła tylko
    „uruchom potrzebne testy”. Polecenie z polityki kroku konfliktu (`step_test_policy`), z bazą = przypięty punkt
    startu scalanego ticketu, więc `test-changed` obejmuje zmiany ticketu i karty od tego punktu. Każdy wariant
    wymaga przebiegu i zapisu w notce; zasada ogólna nie podaje polecenia, którego projekt nie ma."""
    head = _notes(policy)
    if policy.legacy:
        text = ("Testy po scaleniu: ta gałąź karty nie ma celu `make test-changed`. Po zapisaniu commita scalenia "
                f"uruchom raz\npełny `{policy.changed}` w worktree karty i zapisz polecenie oraz wynik w notce.\n")
        required = f"pełny `{policy.changed}`"
    elif policy.changed:
        text = (f"Testy po scaleniu: po zapisaniu commita scalenia uruchom w worktree karty `{policy.changed}`\n"
                "i zapisz polecenie oraz wynik w notce.\n")
        param = "BASE" if testpolicy.make_target(policy.changed) == testpolicy.CHANGED_TARGET else "Baza w poleceniu"
        if policy.base_pending:
            text += (f"`{testpolicy.BASE_PARAM}` to przypięty punkt startu scalanego ticketu na gałęzi karty (nie "
                     "bieżący wierzchołek karty ani master);\npodstaw jego SHA przed uruchomieniem.\n")
        elif policy.base:
            text += (f"{param} to przypięty punkt startu scalanego ticketu na gałęzi karty (z utworzenia jego gałęzi), "
                     "a nie bieżący\nwierzchołek karty ani master.\n")
        required = f"`{policy.changed}`"
    else:
        text = ("Testy po scaleniu: projekt nie podaje polecenia testów zmian. Po zapisaniu commita scalenia uruchom "
                "testy\ndobrane do zmian ticketu i karty według zasad projektu (CLAUDE.md/AGENTS.md) i zapisz "
                "polecenie oraz wynik w notce.\n")
        required = RULES_SANDBOX
    return head + text + CONFLICT_DONE + sandbox_tests(required, worktree, policy.changed) + \
        rerun_failed(worktree, policy.changed)


PLAN_TESTS_PREFIX = "Kontrakt testów projektu (informacja dla planu): "
_PLAN_TESTS = re.compile(r"^" + re.escape(PLAN_TESTS_PREFIX) + r".*\n?", re.MULTILINE)
PLAN_TESTS_LABELS = {
    testpolicy.PROFILE: "zatwierdzony kontrakt w profilu projektu",
    testpolicy.DETECTED: "brak zatwierdzonego kontraktu, polecenia wykryte z Makefile",
    testpolicy.COMPAT: "brak zatwierdzonego kontraktu",
}


def plan_tests_line(policy: TestPolicy) -> str:
    """Jedna linia informacyjna dla planowania i review planu (K-69, PT-284): źródło i polecenia efektywnego
    kontraktu testów, żeby kryteria ticketów odwoływały się do poleceń tego projektu. Nie zmienia formatu planu
    ani `plan-review.json`. Polityka planowania nie ma bazy: `{base}` zostaje i jest opisane."""
    unapproved = " Kontrakt zapisany w profilu nie jest zatwierdzony i nie jest stosowany." if policy.unapproved else ""
    if not policy.changed and not policy.full:
        return (f"{PLAN_TESTS_PREFIX}projekt nie ma zastosowanego kontraktu testów ani celów make — kartki ticketów odeślą do zasad "
                "testów projektu (CLAUDE.md/AGENTS.md); w kryteriach nie podawaj poleceń testów innego projektu."
                f"{unapproved}\n")
    changed = f"`{policy.changed}`" if policy.changed else "według zasad projektu (CLAUDE.md/AGENTS.md)"
    if testpolicy.BASE_PARAM in policy.changed:
        changed += f" (`{testpolicy.BASE_PARAM}` to przypięty punkt startu ticketu — podstawia go Tide w kartce)"
    full = f"`{policy.full}`" if policy.full else "według zasad projektu (CLAUDE.md/AGENTS.md)"
    label = PLAN_TESTS_LABELS.get(policy.source, "zasady projektu")
    return (f"{PLAN_TESTS_PREFIX}{label}; testy ticketu {changed}, pełne testy przy domknięciu karty {full}. "
            f"Kryteria testów w kartkach mają odwoływać się do tych poleceń, nie do poleceń innego projektu."
            f"{unapproved}\n")


def with_plan_tests(prompt: str, policy: TestPolicy) -> str:
    """Polecenie planowania z aktualną linią kontraktu: linia z poprzedniej próby (wznowienie) jest zastępowana."""
    text = _PLAN_TESTS.sub("", prompt)
    return text + ("" if text.endswith("\n") else "\n") + plan_tests_line(policy)


def conflict_card(*, card_id: str, title: str, description: str, ticket: str, card_branch: str,
                  ticket_branch: str, worktree: str, out_dir: str, error: str,
                  policy: TestPolicy | None = None, merge_message: str = "") -> str:
    policy = _tests_policy(policy, worktree, "")
    # Repozytorium klienta (`ai-docs: tide`): tytuł commita scalenia bez numerów kart i ticketów (09.11, K-89).
    message = (f"\nTytuł commita scalenia ustaw dokładnie na: {json.dumps(merge_message, ensure_ascii=False)} "
               "(`git commit -m …`); nie zostawiaj domyślnego tytułu gita z nazwami gałęzi."
               if merge_message else "")
    return f"""Rozwiąż konflikt scalenia ticketu {ticket} do karty {card_id}.
Karta: {title}. Opis: {description}
Pracuj wyłącznie w worktree karty {worktree} na gałęzi {card_branch}.
Zatwierdzony ticket jest na gałęzi {ticket_branch}; przyczyna: {error}.
Scal dokładnie tę gałąź do gałęzi karty, rozwiąż konflikty i zapisz commit scalenia z dwoma rodzicami,
a potem uruchom testy według zasady niżej. Nie zmieniaj gałęzi ticketu ani głównej. Nie rób push.{message}
Jeśli nie możesz bezpiecznie rozstrzygnąć konfliktu, zapisz pytanie do użytkownika zamiast zgadywać.
{conflict_tests(policy, worktree)}Zapisz notkę w {out_dir}/{NOTE_FILE}; jako ostatnią czynność zapisz plik:
Plik wyniku: {out_dir}/{RESULT_FILE}
{{"result":"done","notes":["{NOTE_FILE}"]}} albo
{{"result":"question","question":"…"}} albo {{"result":"error","reason":"…"}}.
{OPTIONS_HINT}
"""


def close_card(*, card_id: str, title: str, description: str, worktree: str, out_dir: str,
               tickets: list[dict], manual: bool, policy: TestPolicy | None = None) -> str:
    instruction = ("Dodaj sekcję „Co przetestować ręcznie i jak” z konkretnymi krokami i oczekiwanym wynikiem."
                   if manual else "Nie dodawaj listy testów ręcznych.")
    return f"""Domknij kartę {card_id}: {title}. Pracuj w worktree karty {worktree}.
Opis karty: {description}
Wyniki ticketów: {tickets}
{close_tests(worktree, policy)}Napisz zwięzłe podsumowanie tego, co faktycznie powstało i co nadal wymaga uwagi, w {out_dir}/{SUMMARY_FILE}.
{instruction} Nie scalaj do gałęzi głównej, nie rób push i nie usuwaj worktree.
Jako ostatnią czynność zapisz plik:
Plik wyniku: {out_dir}/{RESULT_FILE}
{{"result":"done","notes":["{SUMMARY_FILE}"]}} albo
{{"result":"question","question":"…"}} albo {{"result":"error","reason":"…"}}.
{OPTIONS_HINT}
"""


def resume_step(card_id: str, ticket: str, out_dir: str, *, kind: str = "ticket") -> str:
    """Nowa wiadomość w istniejącej sesji: wynik interaktywny ma własny katalog próby."""
    review = (f"Jeśli to review, zapisz raport w {out_dir}/{REVIEW_FILE} i w pliku wyniku podaj także verdict "
              "APPROVE albo REQUEST_CHANGES oraz liczby blockers, majors, nits.\n") if kind == "review" else ""
    manual = "To wejście po zakończonym kroku. Późniejsze zmiany wymagają nowego review lub decyzji użytkownika.\n" \
        if kind == "interactive" else ""
    return f"""Wracamy do kroku ticketu {ticket} karty {card_id}. Kontynuuj pracę w bieżącym worktree.
{manual}{review}
Na koniec zapisz notkę w {out_dir}/{NOTE_FILE}, a jako ostatnią czynność plik {out_dir}/{RESULT_FILE}.
Kontrakt wyniku jest taki sam jak wcześniej: JSON z result = done, question, denied albo error;
dla question podaj question, dla denied command, dla error reason, dla done notes = [\"{NOTE_FILE}\"].
{OPTIONS_HINT}
Nie wykonuj push ani scalenia gałęzi.
"""


# PT-291 (K-38 F3): treść kartki zależna od runnera kroku. Tekst hosta (HostRunner) jest tekstem master
# (wzorzec: daemon/tests/fixtures/host-cards-master/), dodatki pudełka są osobnymi stałymi i trafiają
# wyłącznie do kroku, którego zapisanym runnerem jest `box` — nigdy według samego profilu projektu.
HOST_RUNNER = "host"
BOX_RUNNER = "box"

BOX_WRAPPED = ("W pudełku wolno opakować dozwolone polecenie testowe jako `tide-test <polecenie> [argumenty…]`\n"
               "bez osobnego wpisu tide-test na liście zgód. Dla tej formy sprawdzaj listę dozwolonych\n"
               "wobec polecenia wewnątrz owijki. Dla `tide-test sh -c '…'` oceniaj treść przekazaną do sh -c,\n"
               "nie sam program sh; owijka ani powłoka nie rozszerzają zgody na argumenty lub łańcuchy.\n"
               "Zakazy mają pierwszeństwo: sprawdzaj zarówno pełne wywołanie, jak i polecenie wewnętrzne\n"
               "(w tym każde polecenie w treści sh -c). Zabronionego polecenia nie uruchamiaj przez owijkę.\n"
               "Lista verify profilu wskazuje testy do opakowania, sama nie nadaje zgody na ich wykonanie.\n")
HOST_BOUNDARY = "Przed poleceniem spoza listy dozwolonych zapisz wynik `denied` z pełnym poleceniem i zakończ krok."
BOX_BOUNDARY = ("Przed poleceniem spoza listy dozwolonych (dla opakowanych testów ocenionej według powyższych zasad) "
                "zapisz wynik `denied` z pełnym poleceniem i zakończ krok.")
HOST_REPORT = "`denied` albo `outside`"
BOX_REPORT = "`denied`"
HOST_OUTSIDE = """Jeśli potrzebujesz polecenia, którego piaskownica kroku nie wykona (sieć, np. `npm install <paczka>`; Docker/
Testcontainers; pełne testy, które same uruchamiają piaskownicę), zapisz
{"result":"outside","command":"jedno pełne polecenie","reason":"po co i dlaczego nie w piaskownicy"} i zakończ krok.
Użytkownik może je uruchomić poza piaskownicą w tym worktree; przy wznowieniu dostaniesz kod wyjścia i koniec wyniku.
Polecenie startuje w katalogu worktree, więc nie pisz `cd … &&`, `;`, `|` ani `bash -c` — Tide uruchamia sam tylko
jedno polecenie bez łańcucha. Ścieżki podawaj bezwzględnie albo względem worktree; zmienne tylko na początku,
np. `env PYTHONPATH=<worktree>/src <repo>/.venv/bin/python -m pytest -q <worktree>/tests` (PYTHONPATH wyłącznie do
worktree albo repozytorium projektu). Dłuższą procedurę zapisz jako skrypt w worktree i zgłoś `bash <skrypt>`.
"""
# Krok w pudełku nie uruchamia poleceń na hoście: wynik `outside` Tide odrzuca (FlowError box-outside w silniku).
BOX_OUTSIDE = """Ograniczenie pudełka: ten krok biegnie w pudełku i nie ma wyniku `outside` — Tide nie uruchamia poleceń
kroku na hoście i odrzuci taką prośbę. Testy, budowanie, Docker i instalację paczek wykonuj w pudełku; sieć idzie
przez proxy, a odmowę połączenia Tide sam przedstawi użytkownikowi jako pytanie o domenę. Gdy polecenia nie da się
wykonać w pudełku, zakończ wynikiem `error` z opisem przyczyny.
"""


CONTRACT_COMMANDS_OPEN = "<TIDE_CONTRACT_COMMANDS>"
CONTRACT_COMMANDS_CLOSE = "</TIDE_CONTRACT_COMMANDS>"
CONTRACT_COMMANDS_HEAD = ("Polecenia zatwierdzonego kontraktu testów projektu — dozwolone w tym kroku dosłownie, bez "
                          "dodatkowych\nargumentów (zgoda liczona przy tym starcie albo wznowieniu):\n")
CONTRACT_NOT_GRANTED = "Polecenia kontraktu testów bez automatycznej zgody (uruchomienie wymaga zgody w kroku):\n"
CONTRACT_FIX = ("Kontrakt testów projektu wymaga poprawy: jego polecenie pasuje do zakazu projektu albo Tide — "
                "nie uruchamiaj go\ni nie proś o zgodę; zapisz to w notce.\n")
_CONTRACT_COMMANDS = re.compile(r"\n?" + re.escape(CONTRACT_COMMANDS_OPEN) + r".*?" +
                                re.escape(CONTRACT_COMMANDS_CLOSE) + r"\n?", re.DOTALL)


def contract_commands(grants) -> str:
    """Blok zgód z kontraktu testów (PT-280) w liście poleceń kroku; pusty bez zgód i bez uwag (tekst master)."""
    if grants is None or not (grants.commands or grants.notes):
        return ""
    text = ""
    if grants.commands:
        text += CONTRACT_COMMANDS_HEAD + "".join(f"- {command}\n" for command in grants.commands)
    if grants.notes:
        text += CONTRACT_NOT_GRANTED + "".join(f"- `{command}` (tests.{field}): {message}\n"
                                               for field, command, message in grants.notes)
    if grants.fix:
        text += CONTRACT_FIX
    return f"{CONTRACT_COMMANDS_OPEN}\n{text}{CONTRACT_COMMANDS_CLOSE}\n"


def strip_contract_commands(prompt: str) -> str:
    """Kartka bez bloku zgód kontraktu z poprzedniej próby: wznowienie liczy zgody od nowa (PT-280)."""
    return _CONTRACT_COMMANDS.sub("\n", prompt)


def _policy(allowed: list[str], denied: list[str], step_dir: str, *, wrapped: str, boundary: str,
            report: str, outside: str, contract=None) -> str:
    allow = "\n".join(f"- {cmd}" for cmd in allowed) or "- (brak)"
    deny = "\n".join(f"- {cmd}" for cmd in denied) or "- (brak)"
    own = (f"Odczyt własnych katalogów kroku {step_dir}/in i {step_dir}/out (ls, cat) jest zawsze dozwolony —\n"
           "nie proś o zgodę na niego.\n") if step_dir else ""
    return f"""

Polecenia projektu w tym kroku (najnowsza lista):
Dozwolone polecenia i wzorce (końcowe `*` oznacza argumenty jednego polecenia):
{allow}
{contract_commands(contract)}Zabronione polecenia:
{deny}
{own}{wrapped}{boundary}
Polecenia zabronionego nie uruchamiaj i nie proś o zgodę na nie. Zgoda na jedno polecenie nie zezwala
na cały program ani na łańcuch poleceń. Zwykłe pojedyncze polecenia (testy, budowanie, lint, instalacja paczek)
Tide zatwierdza i uruchamia automatycznie — zgłaszaj je śmiało, po jednym, bez łączenia.
NIE zadawaj użytkownikowi pytań o środowisko (porty, serwery, instalacje, sieć, uruchomienie testów, „czy możesz
uruchomić…”). Środowisko to zadanie Tide i Twoje: zgłoś polecenie ({report}); gdy mimo to się nie da,
zakończ wynikiem `error` z opisem przyczyny. Pytanie (`question`) jest tylko na decyzje o zakresie, wymaganiach
i wyborach biznesowych — z krótkimi opcjami i Twoją rekomendacją.
{outside}"""


def command_policy(allowed: list[str], denied: list[str], step_dir: str = "", *, contract=None) -> str:
    """Lista poleceń kroku na hoście (HostRunner) — tekst master; twardą granicą pozostaje piaskownica kroku.
    `contract` — zgody z kontraktu testów (`testpolicy.contract_grants`, PT-280); bez nich tekst master."""
    return _policy(allowed, denied, step_dir, wrapped="", boundary=HOST_BOUNDARY, report=HOST_REPORT,
                   outside=HOST_OUTSIDE, contract=contract)


def box_command_policy(allowed: list[str], denied: list[str], step_dir: str = "", *, contract=None) -> str:
    """Lista poleceń kroku w pudełku: owijka tide-test i ograniczenie pudełka zamiast kontraktu outside."""
    return _policy(allowed, denied, step_dir, wrapped=BOX_WRAPPED, boundary=BOX_BOUNDARY, report=BOX_REPORT,
                   outside=BOX_OUTSIDE, contract=contract)


def runner_policy(runner: str, allowed: list[str], denied: list[str], step_dir: str = "", *,
                  profile: dict | None = None, contract=None) -> str:
    """Jedyny wybór treści listy poleceń według zapisanego runnera kroku. Host: tekst master, bez dodatków
    pudełka (także przy `box.enabled: true` i istniejącym profilu). Box: owijka, verify profilu, ograniczenia."""
    if runner == BOX_RUNNER:
        return box_command_policy(allowed, denied, step_dir, contract=contract) + box_tests(profile or {})
    if runner != HOST_RUNNER:
        raise ValueError(f"nieznany runner kroku: {runner!r}")
    return command_policy(allowed, denied, step_dir, contract=contract)


def review_evidence(runner: str, source: dict) -> str:
    """Spis artefaktów autora w kartce review — tylko dla review biegnącego w pudełku (zapisany runner kroku
    review), i tylko gdy autor biegł w pudełku. Review na hoście zachowuje zasady testów hosta (test-changed
    z notki), także po pudełkowym autorze i przy wyłączonym pudełku."""
    if runner == BOX_RUNNER:
        return test_evidence(source)
    if runner != HOST_RUNNER:
        raise ValueError(f"nieznany runner kroku: {runner!r}")
    return ""


def resume_context(*, choice: str, prior: dict, answer: str, command: str, out_dir: str,
                   allowed=None, skipped=(), instead: str = "", caretaker: bool = False) -> str:
    """Jawna decyzja do wznowionego kroku; kartka poprzedniej próby pozostaje wyżej w poleceniu.
    `allowed` — zgoda na wybrane polecenia z łańcucha (PT-124), `skipped` — niezaznaczone, `instead` — co krok ma
    zrobić zamiast poleceń, na które nie było zgody („Zrób zamiast tego…”), `caretaker` — ponowienie z instrukcją
    od opiekuna karty (PT-150), nie od użytkownika."""
    if caretaker and choice == "retry":
        detail = ("Instrukcja od opiekuna karty (opiekun sprawdził dziennik tego kroku i historię karty; instrukcja ma "
                  f"pierwszeństwo przed wcześniejszym planem kroku):\n{answer}\nZachowaj zmiany z poprzednich prób.")
    elif choice == "answer":
        author = "opiekuna karty" if caretaker else "użytkownika"
        detail = f"Pytanie z poprzedniej próby: {prior.get('question') or ''}\nOdpowiedź {author}: {answer}"
        picked = next((o for o in prior.get("options") or [] if o.get("title") == answer), None)
        if picked:
            detail += "\nUżytkownik wybrał podaną opcję."
            if picked.get("effect"):
                detail += f"\nSkutek opcji: {picked['effect']}"
    elif choice == "allow" and allowed is not None:
        lines = "\n".join(f"- {text}" for text in allowed)
        detail = (f"Prosiłeś o zgodę na łańcuch: {command}\n"
                  f"Użytkownik zezwolił wyłącznie na te pojedyncze polecenia (dopisane do listy dozwolonych):\n{lines}\n"
                  "Uruchamiaj je osobno, nie w łańcuchu.")
        if skipped:
            rest = "\n".join(f"- {text}" for text in skipped)
            detail += f"\nNa pozostałe polecenia zgody nie ma — nie uruchamiaj ich i kontynuuj bez nich:\n{rest}"
    elif choice == "allow":
        detail = f"Użytkownik zezwolił na dokładne polecenie: {command}"
    elif choice == "background":
        detail = ("Rozmowa z użytkownikiem w Konsoli została zamknięta przed zapisaniem wyniku. Kontynuuj pracę w tle od "
                  "miejsca przerwania (sprawdź `git status` i ostatnie zmiany) i zakończ krok plikiem wyniku.")
    elif choice in ("run", "run-always", "run-ticket"):
        detail = (f"Użytkownik uruchomił poza piaskownicą, w tym worktree, polecenie: {command}\n{answer}\n"
                  "Zmiany w plikach (np. package.json, package-lock.json) są już w worktree — sprawdź `git status`.")
    elif choice == "box-allow":
        detail = (f"Proxy odmówiło połączenia: {command}. Użytkownik dopisał tę domenę do profilu karty i zlecił "
                  "przebudowę środowiska. Kontynuuj; jeśli domena nadal jest niedostępna, zgłoś to w notce.")
    elif choice == "deny" and prior.get("network"):
        detail = (f"Użytkownik nie dopisał domeny do profilu środowiska: {command}. Połączenie jest odrzucone — "
                  "kontynuuj bez niego.")
    elif choice == "deny" and prior.get("outside") and (instead or answer):
        # 04.11 (K-43/BO-13, K-38/PT-164): instrukcja „zamiast tego” przy odmowie polecenia poza piaskownicą ginęła —
        # agent widział samo „nie zgodził się” i prosił o to samo polecenie w kółko. 09.11 (K-91, K-88/PT-328): tak samo
        # ginęła odpowiedź użytkownika; idzie tą samą drogą jako treść, odmowa polecenia pozostaje ostateczna.
        detail = (f"Użytkownik nie zgodził się uruchomić poza piaskownicą: {command}\n"
                  "Nie proś ponownie o to polecenie.")
        if instead:
            detail += f" Zamiast tego: {instead}"
        if answer:
            detail += f"\nOdpowiedź użytkownika (treść, nie polecenie do uruchomienia): {answer}"
    elif choice == "deny" and prior.get("outside"):
        detail = f"Użytkownik nie zgodził się uruchomić poza piaskownicą: {command}. Kontynuuj bez tego."
    elif choice == "deny" and instead:
        detail = (f"Polecenie nie zostało uruchomione: {command}\n"
                  f"Użytkownik odmówił zgody na nie i prosi, żebyś zamiast tego: {instead}")
    elif choice == "deny":
        detail = f"Użytkownik odmówił zgody na polecenie: {command}. Kontynuuj bez niego."
        if answer:
            detail = f"Polecenie nie zostało uruchomione: {command}\n{answer}"
    elif choice == "cleanup":
        detail = ("Krok zakończył się, ale worktree ticketu ma niezacommitowane albo nieśledzone pliki (sprawdź "
                  "`git status`). Zacommituj zmiany potrzebne w tickecie, usuń nieśledzone pliki tymczasowe i "
                  "diagnostyczne poleceniem `git clean -f -- <ścieżka>` (nie `rm`), "
                  "a potem zakończ krok tym samym wynikiem co poprzednio.")
    elif choice == "change-executor":
        author = "Opiekun karty wybrał" if caretaker else "Użytkownik wybrał"
        detail = f"{author} innego wykonawcę. Zachowaj zmiany z poprzednich prób w tym samym worktree."
        if answer:
            detail += f"\nInstrukcja dla nowej sesji wykonawcy:\n{answer}"
    elif choice == "transfer":
        detail = "Krok przekazano od innego dostawcy. Kontynuuj w tym samym worktree z commita WIP; przeczytaj notkę techniczną niżej."
    elif choice == "auto":
        detail = "Krok ponowiony automatycznie po pierwszym błędzie. Zachowaj zmiany z poprzedniej próby."
    elif choice == "retry" and answer:
        detail = ("Użytkownik polecił ponowić krok z instrukcją (ma pierwszeństwo przed wcześniejszym planem kroku):\n"
                  f"{answer}\nZachowaj zmiany z poprzednich prób.")
    else:
        detail = "Użytkownik polecił ponowić krok. Zachowaj zmiany z poprzednich prób."
    return f"""

Wznowienie tego samego kroku w tym samym worktree. Poprzedni wynik: {prior.get('result') or ''};
powód: {prior.get('reason') or ''}.
{detail}
Nie powtarzaj już wykonanej pracy ani commitów. Na końcu zapisz nową notkę wyniku i jako ostatni plik:
Plik wyniku: {out_dir}/{RESULT_FILE}
"""


def ticket_card(*, card_id: str, title: str, description: str, project: str, ticket: str, branch: str,
                card_branch: str, worktree: str, out_dir: str, images=(), test_base: str = "",
                policy: TestPolicy | None = None) -> str:
    shots = "".join(f"- {path}\n" for path in images)
    body = description.strip() or "(karta bez opisu — zrób to, co mówi tytuł)"
    return f"""Krok ticketu {ticket} karty {card_id} w przepływie karty Tide (wejście „Od razu”, krok w tle).

Karta {card_id}: {title}
Projekt: {project}
Ticket: {ticket}
{resources.instructions()}
Twój katalog roboczy: {worktree} (gałąź {branch}, odgałęziona od gałęzi karty {card_branch})

Opis karty:
{body}
{ticket_tests(test_base, worktree, policy)}
{f'''
Zrzuty ekranu dołączone do zlecenia (przeczytaj je narzędziem Read):
{shots}''' if shots else ''}
Co masz zrobić:
- zrób to, co opisuje karta, jako ticket {ticket}: pionowo, z testami według zasad projektu (CLAUDE.md, AGENTS.md),
- pracujesz wyłącznie w swoim katalogu roboczym i commitujesz na swojej gałęzi ({branch}); commity w stylu
  repozytorium, bez podpisów i wzmianek o narzędziach,
- nie rób push, nie scalaj do innych gałęzi (gałąź karty i główną prowadzi Tide), nie pisz poza katalogiem
  roboczym i katalogiem wyjściowym {out_dir},
- nikt nie odpowie ci w trakcie: gdy potrzebujesz decyzji albo zgody na polecenie, zakończ krok plikiem wyniku
  z pytaniem albo z brakiem zgody — Tide zapyta użytkownika i wznowi krok,
- na koniec zapisz notkę wyniku w {out_dir}/{NOTE_FILE} (co zrobiono, jak sprawdzono, ograniczenia),
- {DESK_HINT}
- ostatnią czynnością zapisz plik wyniku (JSON):

Plik wyniku: {out_dir}/{RESULT_FILE}

  {{"result": "done", "notes": ["{NOTE_FILE}"]}}                 — ticket gotowy,
  {{"result": "question", "question": "…"}}                     — potrzebujesz decyzji użytkownika
     (opcjonalnie z "options": [{{"title": "…", "effect": "…", "recommended": true}}] — 1–4 gotowe odpowiedzi,
     najwyżej jedna polecona; Tide sam dokłada „Inną odpowiedź…”),
  {{"result": "denied", "command": "…"}}                        — polecenie wymaga zgody,
  {{"result": "error", "reason": "…"}}                          — nie da się zrobić ticketu.
  Gdy brakuje pracy innego ticketu tej karty (np. modułu, który robi PT-12) albo innej karty, nie pytaj i nie
  ponawiaj: dodaj do wyniku `question` albo `error` pole "waitFor": "PT-12" (albo "K-43"). Tide wstrzyma krok bez
  pytania użytkownika i wznowi go sam po scaleniu tamtego ticketu do gałęzi karty (albo domknięciu karty).

Bez pliku wyniku krok kończy się błędem, nawet gdy praca jest zrobiona.
"""


def box_tests(profile: dict) -> str:
    """Instrukcja tylko dla BoxRunner; polecenia profilu są także testami."""
    import shlex
    commands = profile.get("verify") or []
    examples = "\n".join(f"- tide-test {shlex.join(['sh', '-c', command])}" for command in commands)
    return ("\nTesty w pudełku: testy uruchamiaj przez `tide-test <polecenie> [argumenty…]`. "
            "Dotyczy verify profilu oraz npm test, npx playwright test, dotnet test, make test, pytest "
            "i python -m unittest. Dla złożonego polecenia użyj tide-test sh -c '…'. "
            "Owijka zachowuje środowisko, katalog roboczy i kod wyjścia; zapisuje log i podsumowanie "
            "w out/tests katalogu tego kroku, poza worktree.\n" +
            ("Polecenia verify profilu:\n" + examples + "\n" if examples else ""))


def test_evidence(step: dict) -> str:
    """Spis istniejących artefaktów autora; HostRunner zachowuje dawne przekazanie."""
    if step.get("runner") != "box":
        return ""
    import os
    import stat
    from ..box.runner import StepDir
    from ..tasks.agentcmd import StepError
    path = step.get("stepDir") or ""
    found = []
    tests = False
    try:
        fs = StepDir(os.path.dirname(path), path)
        for parts in (("out",), ("out", "tests")):
            try:
                fd = fs._walk(parts, create=False)
            except OSError:
                continue
            try:
                for name in sorted(os.listdir(fd)):
                    wanted = (name == "proxy-denied.log" if len(parts) == 1 else
                              name == "summary.json" or bool(re.fullmatch(r"\d+\.log", name)))
                    if not wanted:
                        continue
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode):
                        artifact = os.path.join(path, *parts, name)
                        found.append(f"- `{artifact}` — {info.st_size} B")
                        tests |= len(parts) == 2
            finally:
                os.close(fd)
    except (OSError, ValueError, StepError):
        pass
    return ("\nArtefakty kroku wykonawcy:\n" + "\n".join(found) + "\n" +
            ("krok bez testów.\n" if not tests else "") +
            "świeży artefakt = dowód; nie proś o ponowny przebieg. "
            "Sprawdź zgodność polecenia, wyniku i czasu artefaktu z ocenianą zmianą.\n")


def review_card(*, card_id: str, title: str, description: str, ticket: str, round_no: int,
                card_branch: str, ticket_branch: str, worktree: str, out_dir: str,
                diff: str, note: str, rules: str, test_base: str = "", policy: TestPolicy | None = None) -> str:
    """Pełne wejście niezależnego przeglądu, przekazane na stdin programu recenzenta."""
    policy = _tests_policy(policy, worktree, test_base)
    return f"""Krok review {ticket} karty {card_id}, runda {round_no}. Sprawdź wymagania i zmiany, nie edytuj kodu.

Karta {card_id}: {title}
Opis/specyfikacja karty:
{description.strip() or '(brak opisu)'}

Gałąź karty: {card_branch}
Gałąź ticketu: {ticket_branch}
Twój katalog: {worktree}

Dla recenzenta:
- sprawdź zgodność z opisem karty i zasadami projektu,
{review_tests(policy)}- ponowienie samych padniętych modułów uznaj tylko przy tym samym HEAD co pełny przebieg i czystym drzewie
  (zapis w notce); inaczej wymagaj pełnego polecenia z zasady testów; udane ponowienie nie zastępuje porażki,
  oceń rozliczenie odstępstwa jak przy każdym nieudanym przebiegu,
- podaj konkretne BLOCKER, MAJOR, NIT z plikiem i linią,
- BLOCKER lub MAJOR oznacza REQUEST_CHANGES,
- {DESK_HINT}

{ticket_tests(test_base, worktree, policy, author=False)}
Zasady projektu:
{rules or '(brak pliku zasad)'}

Notka wyniku wykonawcy:
{note or '(brak notki)'}

Diff ticketu względem gałęzi karty:
{diff}

Zapisz raport w {out_dir}/{REVIEW_FILE}. Ostatnią czynnością zapisz plik wyniku:
Plik wyniku: {out_dir}/{RESULT_FILE}
{{"result":"done","verdict":"APPROVE","blockers":0,"majors":0,"nits":0,"notes":["{REVIEW_FILE}"]}}
albo
{{"result":"done","verdict":"REQUEST_CHANGES","blockers":1,"majors":0,"nits":0,"notes":["{REVIEW_FILE}"]}}
Liczby mają odpowiadać raportowi. Nie rób push ani merge.
"""


def fix_card(*, card_id: str, title: str, description: str, ticket: str, round_no: int,
             branch: str, worktree: str, out_dir: str, review: str, test_base: str = "",
             policy: TestPolicy | None = None) -> str:
    return f"""Krok poprawek ticketu {ticket} karty {card_id} po review, runda {round_no}.

Karta {card_id}: {title}
Opis/specyfikacja karty:
{description.strip() or '(brak opisu)'}
Twój katalog: {worktree}, gałąź {branch}. Popraw BLOCKER i MAJOR z raportu; uruchom odpowiednie testy.
{ticket_tests(test_base, worktree, policy)}

Raport review:
{review}

Zapisz notkę poprawek w {out_dir}/{NOTE_FILE}; zrób commit na swojej gałęzi. Nie rób push ani merge.
Ostatnią czynnością zapisz plik wyniku.
Plik wyniku: {out_dir}/{RESULT_FILE}
{{"result":"done","notes":["{NOTE_FILE}"]}}
"""
