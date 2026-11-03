"""Kartka przekazania kroku ticketu przy wejściu „Od razu”: zbudowana z karty (tytuł, opis, zrzuty), z kontraktem
kroku (gdzie pisać, czego nie robić, plik wyniku). Idzie do programu jako polecenie na stdin (agentcmd.Background)."""
from __future__ import annotations

RESULT_FILE = "result.json"
NOTE_FILE = "note.md"
REVIEW_FILE = "review.md"
SUMMARY_FILE = "summary.md"


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
"""


def close_card(*, card_id: str, title: str, description: str, worktree: str, out_dir: str,
               tickets: list[dict], manual: bool) -> str:
    instruction = ("Dodaj sekcję „Co przetestować ręcznie i jak” z konkretnymi krokami i oczekiwanym wynikiem."
                   if manual else "Nie dodawaj listy testów ręcznych.")
    return f"""Domknij kartę {card_id}: {title}. Pracuj w worktree karty {worktree}.
Opis karty: {description}
Wyniki ticketów: {tickets}
Napisz zwięzłe podsumowanie tego, co faktycznie powstało i co nadal wymaga uwagi, w {out_dir}/{SUMMARY_FILE}.
{instruction} Nie scalaj do gałęzi głównej, nie rób push i nie usuwaj worktree.
Jako ostatnią czynność zapisz plik:
Plik wyniku: {out_dir}/{RESULT_FILE}
{{"result":"done","notes":["{SUMMARY_FILE}"]}} albo
{{"result":"question","question":"…"}} albo {{"result":"error","reason":"…"}}.
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
Nie wykonuj push ani scalenia gałęzi.
"""


def command_policy(allowed: list[str], denied: list[str]) -> str:
    """Wskazówka dla kooperującego agenta; twardą granicą pozostaje piaskownica kroku."""
    allow = "\n".join(f"- {cmd}" for cmd in allowed) or "- (brak)"
    deny = "\n".join(f"- {cmd}" for cmd in denied) or "- (brak)"
    return f"""

Polecenia projektu w tym kroku (najnowsza lista):
Dozwolone polecenia i wzorce (końcowe `*` oznacza argumenty jednego polecenia):
{allow}
Zabronione polecenia:
{deny}
Przed poleceniem spoza listy dozwolonych zapisz wynik `denied` z pełnym poleceniem i zakończ krok.
Polecenia zabronionego nie uruchamiaj i nie proś o zgodę na nie. Zgoda na jedno polecenie nie zezwala
na cały program ani na łańcuch poleceń.
"""


def resume_context(*, choice: str, prior: dict, answer: str, command: str, out_dir: str) -> str:
    """Jawna decyzja do wznowionego kroku; kartka poprzedniej próby pozostaje wyżej w poleceniu."""
    if choice == "answer":
        detail = f"Pytanie z poprzedniej próby: {prior.get('question') or ''}\nOdpowiedź użytkownika: {answer}"
    elif choice == "allow":
        detail = f"Użytkownik zezwolił na dokładne polecenie: {command}"
    elif choice == "deny":
        detail = f"Użytkownik odmówił zgody na polecenie: {command}. Kontynuuj bez niego."
        if answer:
            detail = f"Polecenie nie zostało uruchomione: {command}\n{answer}"
    elif choice == "cleanup":
        detail = ("Krok zakończył się, ale worktree ticketu ma niezacommitowane albo nieśledzone pliki (sprawdź "
                  "`git status`). Zacommituj zmiany potrzebne w tickecie, usuń pliki tymczasowe i diagnostyczne, "
                  "a potem zakończ krok tym samym wynikiem co poprzednio.")
    elif choice == "change-executor":
        detail = "Użytkownik wybrał innego wykonawcę. Zachowaj zmiany z poprzednich prób w tym samym worktree."
    elif choice == "transfer":
        detail = "Krok przekazano od innego dostawcy. Kontynuuj w tym samym worktree z commita WIP; przeczytaj notkę techniczną niżej."
    elif choice == "auto":
        detail = "Krok ponowiony automatycznie po pierwszym błędzie. Zachowaj zmiany z poprzedniej próby."
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
                card_branch: str, worktree: str, out_dir: str, images=()) -> str:
    shots = "".join(f"- {path}\n" for path in images)
    body = description.strip() or "(karta bez opisu — zrób to, co mówi tytuł)"
    return f"""Krok ticketu {ticket} karty {card_id} w przepływie karty Tide (wejście „Od razu”, krok w tle).

Karta {card_id}: {title}
Projekt: {project}
Ticket: {ticket}
Twój katalog roboczy: {worktree} (gałąź {branch}, odgałęziona od gałęzi karty {card_branch})

Opis karty:
{body}
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
  {{"result": "question", "question": "…"}}                     — potrzebujesz decyzji użytkownika,
  {{"result": "denied", "command": "…"}}                        — polecenie wymaga zgody,
  {{"result": "error", "reason": "…"}}                          — nie da się zrobić ticketu.

Bez pliku wyniku krok kończy się błędem, nawet gdy praca jest zrobiona.
"""


def review_card(*, card_id: str, title: str, description: str, ticket: str, round_no: int,
                card_branch: str, ticket_branch: str, worktree: str, out_dir: str,
                diff: str, note: str, rules: str) -> str:
    """Pełne wejście niezależnego przeglądu, przekazane na stdin programu recenzenta."""
    return f"""Krok review {ticket} karty {card_id}, runda {round_no}. Sprawdź wymagania i zmiany, nie edytuj kodu.

Karta {card_id}: {title}
Opis/specyfikacja karty:
{description.strip() or '(brak opisu)'}

Gałąź karty: {card_branch}
Gałąź ticketu: {ticket_branch}
Twój katalog: {worktree}

Dla recenzenta:
- sprawdź zgodność z opisem karty i zasadami projektu,
- podaj konkretne BLOCKER, MAJOR, NIT z plikiem i linią,
- BLOCKER lub MAJOR oznacza REQUEST_CHANGES.

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
             branch: str, worktree: str, out_dir: str, review: str) -> str:
    return f"""Krok poprawek ticketu {ticket} karty {card_id} po review, runda {round_no}.

Karta {card_id}: {title}
Opis/specyfikacja karty:
{description.strip() or '(brak opisu)'}
Twój katalog: {worktree}, gałąź {branch}. Popraw BLOCKER i MAJOR z raportu; uruchom odpowiednie testy.

Raport review:
{review}

Zapisz notkę poprawek w {out_dir}/{NOTE_FILE}; zrób commit na swojej gałęzi. Nie rób push ani merge.
Ostatnią czynnością zapisz plik wyniku.
Plik wyniku: {out_dir}/{RESULT_FILE}
{{"result":"done","notes":["{NOTE_FILE}"]}}
"""
