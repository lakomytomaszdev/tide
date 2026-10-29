"""Kartka przekazania kroku ticketu przy wejściu „Od razu”: zbudowana z karty (tytuł, opis, zrzuty), z kontraktem
kroku (gdzie pisać, czego nie robić, plik wyniku). Idzie do programu jako polecenie na stdin (agentcmd.Background)."""
from __future__ import annotations

RESULT_FILE = "result.json"
NOTE_FILE = "note.md"


def resume_step(card_id: str, ticket: str, out_dir: str) -> str:
    """Nowa wiadomość w istniejącej sesji: wynik interaktywny ma własny katalog próby."""
    return f"""Wracamy do kroku ticketu {ticket} karty {card_id}. Kontynuuj pracę w bieżącym worktree.
Na koniec zapisz notkę w {out_dir}/{NOTE_FILE}, a jako ostatnią czynność plik {out_dir}/{RESULT_FILE}.
Kontrakt wyniku jest taki sam jak wcześniej: JSON z result = done, question, denied albo error;
dla question podaj question, dla denied command, dla error reason, dla done notes = [\"{NOTE_FILE}\"].
Nie wykonuj push ani scalenia gałęzi.
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
