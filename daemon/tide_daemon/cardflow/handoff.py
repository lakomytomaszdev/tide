"""Kartka przekazania kroku ticketu przy wejściu „Od razu”: zbudowana z karty (tytuł, opis, zrzuty), z kontraktem
kroku (gdzie pisać, czego nie robić, plik wyniku). Idzie do programu jako polecenie na stdin (agentcmd.Background)."""
from __future__ import annotations
from .. import resources
from . import testpolicy
from .testpolicy import TestPolicy

import os
import re

RESULT_FILE = "result.json"
NOTE_FILE = "note.md"
REVIEW_FILE = "review.md"
SUMMARY_FILE = "summary.md"
OPTIONS_HINT = ('Do pytania możesz dodać "options": [{"title": "…", "effect": "…", "recommended": true}] — '
                "1–4 gotowe odpowiedzi, najwyżej jedna polecona; Tide sam dokłada „Inną odpowiedź…”.")


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


def _policy(policy: TestPolicy | None, worktree: str, base: str) -> TestPolicy:
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


def ticket_tests(base: str = "", worktree: str = "", policy: TestPolicy | None = None) -> str:
    """Zasada testów ticketu, poprawek i review z polityki testów (bez polityki — reguła zgodności z master)."""
    policy = _policy(policy, worktree, base)
    head = _notes(policy)
    if policy.legacy:
        return head + OLD_BRANCH_TESTS + rerun_failed(worktree, policy.changed)
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
    return head + text + rerun_failed(worktree, policy.changed)


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
    policy = _policy(policy, worktree, "")
    run = f"pełny `{policy.full}`" if policy.full else RULES_FULL
    again = f"`{policy.full}`" if policy.full else "przebiegu"
    return _notes(policy, close=True) + f"""Przed napisaniem podsumowania uruchom raz {run} w worktree gałęzi karty, po scaleniu ticketów.
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
    elif kind == "review":
        body = "Dla recenzenta:\n" + review_tests(policy) + "\n" + ticket_tests(policy.base, worktree, policy)
    else:
        body = ticket_tests(policy.base, worktree, policy)
    return f"\n\n{CURRENT_TESTS_OPEN}\n{CURRENT_TESTS_HEAD}{body}{CURRENT_TESTS_CLOSE}\n"


def strip_current_tests(prompt: str) -> str:
    """Kartka bez sekcji „Obowiązujące testy” z poprzedniego wznowienia."""
    return _CURRENT_TESTS.sub("\n", prompt)


def conflict_card(*, card_id: str, title: str, description: str, ticket: str, card_branch: str,
                  ticket_branch: str, worktree: str, out_dir: str, error: str) -> str:
    return f"""Rozwiąż konflikt scalenia ticketu {ticket} do karty {card_id}.
Karta: {title}. Opis: {description}
Pracuj wyłącznie w worktree karty {worktree} na gałęzi {card_branch}.
Zatwierdzony ticket jest na gałęzi {ticket_branch}; przyczyna: {error}.
Scal dokładnie tę gałąź do gałęzi karty, rozwiąż konflikty, uruchom potrzebne testy i zapisz commit
scalenia z dwoma rodzicami. Nie zmieniaj gałęzi ticketu ani głównej. Nie rób push.
Jeśli nie możesz bezpiecznie rozstrzygnąć konfliktu, zapisz pytanie do użytkownika zamiast zgadywać.
Zapisz notkę w {out_dir}/{NOTE_FILE}; jako ostatnią czynność zapisz plik:
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


def command_policy(allowed: list[str], denied: list[str], step_dir: str = "") -> str:
    """Wskazówka dla kooperującego agenta; twardą granicą pozostaje piaskownica kroku."""
    allow = "\n".join(f"- {cmd}" for cmd in allowed) or "- (brak)"
    deny = "\n".join(f"- {cmd}" for cmd in denied) or "- (brak)"
    own = (f"Odczyt własnych katalogów kroku {step_dir}/in i {step_dir}/out (ls, cat) jest zawsze dozwolony —\n"
           "nie proś o zgodę na niego.\n") if step_dir else ""
    return f"""

Polecenia projektu w tym kroku (najnowsza lista):
Dozwolone polecenia i wzorce (końcowe `*` oznacza argumenty jednego polecenia):
{allow}
Zabronione polecenia:
{deny}
{own}Przed poleceniem spoza listy dozwolonych zapisz wynik `denied` z pełnym poleceniem i zakończ krok.
Polecenia zabronionego nie uruchamiaj i nie proś o zgodę na nie. Zgoda na jedno polecenie nie zezwala
na cały program ani na łańcuch poleceń. Zwykłe pojedyncze polecenia (testy, budowanie, lint, instalacja paczek)
Tide zatwierdza i uruchamia automatycznie — zgłaszaj je śmiało, po jednym, bez łączenia.
NIE zadawaj użytkownikowi pytań o środowisko (porty, serwery, instalacje, sieć, uruchomienie testów, „czy możesz
uruchomić…”). Środowisko to zadanie Tide i Twoje: zgłoś polecenie (`denied` albo `outside`); gdy mimo to się nie da,
zakończ wynikiem `error` z opisem przyczyny. Pytanie (`question`) jest tylko na decyzje o zakresie, wymaganiach
i wyborach biznesowych — z krótkimi opcjami i Twoją rekomendacją.
Jeśli potrzebujesz polecenia, którego piaskownica kroku nie wykona (sieć, np. `npm install <paczka>`; Docker/
Testcontainers; pełne testy, które same uruchamiają piaskownicę), zapisz
{{"result":"outside","command":"jedno pełne polecenie","reason":"po co i dlaczego nie w piaskownicy"}} i zakończ krok.
Użytkownik może je uruchomić poza piaskownicą w tym worktree; przy wznowieniu dostaniesz kod wyjścia i koniec wyniku.
Polecenie startuje w katalogu worktree, więc nie pisz `cd … &&`, `;`, `|` ani `bash -c` — Tide uruchamia sam tylko
jedno polecenie bez łańcucha. Ścieżki podawaj bezwzględnie albo względem worktree; zmienne tylko na początku,
np. `env PYTHONPATH=<worktree>/src <repo>/.venv/bin/python -m pytest -q <worktree>/tests` (PYTHONPATH wyłącznie do
worktree albo repozytorium projektu). Dłuższą procedurę zapisz jako skrypt w worktree i zgłoś `bash <skrypt>`.
"""


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
    elif choice == "deny" and prior.get("outside") and instead:
        # 04.11 (K-43/BO-13, K-38/PT-164): instrukcja „zamiast tego” przy odmowie polecenia poza piaskownicą ginęła —
        # agent widział samo „nie zgodził się” i prosił o to samo polecenie w kółko.
        detail = (f"Użytkownik nie zgodził się uruchomić poza piaskownicą: {command}\n"
                  f"Nie proś ponownie o to polecenie. Zamiast tego: {instead}")
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


def review_card(*, card_id: str, title: str, description: str, ticket: str, round_no: int,
                card_branch: str, ticket_branch: str, worktree: str, out_dir: str,
                diff: str, note: str, rules: str, test_base: str = "", policy: TestPolicy | None = None) -> str:
    """Pełne wejście niezależnego przeglądu, przekazane na stdin programu recenzenta."""
    policy = _policy(policy, worktree, test_base)
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
- BLOCKER lub MAJOR oznacza REQUEST_CHANGES.

{ticket_tests(test_base, worktree, policy)}
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
