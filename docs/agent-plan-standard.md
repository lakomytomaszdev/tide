# Standard planu pracy agentów (Tide)

Wersja 1, 27.10.2026. Ten dokument jest wzorcem, do którego odsyłają pliki `CLAUDE.md` i `AGENTS.md`
projektów. Plan czyta tide-daemon: tablica w oknie **Tide Sesje**, postęp koordynatora w widżecie
agentów i sekcja „Plany i tickety” w raporcie porannym (opis: [tide-daemon.md](tide-daemon.md#koordynacja),
uzasadnienie: decyzja 50 w [decisions.md](decisions.md)).

## Po co

Sesja koordynująca (Claude Code albo Codex), która dzieli pracę na tickety i zleca je podagentom,
zapisuje plan w repozytorium. Dzięki temu stan pracy jest widoczny poza rozmową: co jest do zrobienia,
co w toku, co czeka na decyzję użytkownika, co po review — także dla kolejnej sesji, która przejmie pracę.

## Plik

- Ścieżka: `docs/agents/plans/RRRR-MM-DD-<slug>.md` (data założenia planu, slug z małych liter,
  cyfr i myślników), np. `docs/agents/plans/2026-10-26-release-candidate.md`.
- Plan jest **commitowany** razem z pracą albo osobnym commitem `docs(plan): …`.
- Jeden plan na jeden ciąg pracy koordynatora. Zakończony plan zostaje w repozytorium ze statusem `done`.

## Frontmatter

Płaskie pary `klucz: wartość` (YAML bez zagnieżdżeń i list), pierwsza linia pliku to `---`.

| Klucz | Wartość | Opis |
| --- | --- | --- |
| `tide-plan` | `1` | wersja standardu; plik bez niej nie jest planem Tide |
| `title` | tekst | tytuł planu, np. `Release candidate TOM-20` |
| `tracker` | `youtrack` \| `local` \| `none` | skąd są tickety (niżej) |
| `tracker-url` | adres albo pusty | dla `youtrack`, np. `http://10.20.1.159:8111` |
| `tracker-project` | klucz albo pusty | projekt w trackerze, np. `LOG`, `LEGAL`, `APP5` |
| `ticket-prefix` | wielkie litery | prefiks ID ticketów w tym planie, np. `TOM`, `LA`, `TIDE` |
| `coordinator` | `claude` \| `codex` | narzędzie sesji koordynującej |
| `session` | ID sesji albo pusty | ID sesji koordynatora (Claude Code: nazwa pliku dziennika, Codex: ID wątku), jeśli znane |
| `status` | `active` \| `paused` \| `done` | stan całego planu |
| `updated` | czas ISO 8601 | chwila ostatniej zmiany planu, np. `2026-10-26T21:13:00+01:00` |

**Tracker:**

- `youtrack` — tickety żyją w YouTracku (`tracker-url`, `tracker-project`); plan wskazuje, które
  z nich są w tym ciągu pracy, a Tide dociąga z YouTracka (tylko odczyt) tytuły, stany i odnośniki.
- `local` — **nie ma zewnętrznego trackera**: ID ticketów nadaje plan (`TIDE-1`, `TIDE-2`, …), a plan
  jest źródłem stanu dla tablicy Tide i raportu porannego. `tracker-url` i `tracker-project` puste.
- `none` — praca bez śledzenia ticketów (np. jednorazowe zadanie). Tabela może być pusta albo
  zawierać zwykłe kroki; Tide pokazuje je, ale niczego nie dopasowuje do trackera.

## Tabela

Pod frontmatterem jedna tabela Markdown z dokładnie tym nagłówkiem:

```markdown
| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| TOM-140 | Distribution guard dla kompaktowej dystrybucji | w-toku | `tom-140-guard` | implementer | po review: 1 MAJOR |
| TOM-146 | Eksport raportu do CSV z filtrami | todo | | | |
```

| Kolumna | Opis |
| --- | --- |
| `Ticket` | ID ticketu (`PREFIKS-numer`), ten sam co w trackerze albo nadany przez plan |
| `Tytuł` | krótki tytuł; przy YouTracku może się różnić od tytułu w trackerze (wygrywa plan) |
| `Stan` | jeden z: `todo`, `w-toku`, `review`, `czeka`, `gotowe`, `odrzucone` |
| `Gałąź` | gałąź pracy (może być w `` `…` ``), z ID ticketu małymi literami |
| `Agent` | kto pracuje: typ podagenta (`implementer`, `code-reviewer`…) albo pusty |
| `Uwagi` | jedno zdanie: powód czekania, wynik review, decyzja |

**Stany:**

| Stan | Znaczenie | Kolumna w Tide Sesje |
| --- | --- | --- |
| `todo` | w planie, nikt nad nim nie pracuje | Do zrobienia |
| `w-toku` | implementacja albo poprawki | W toku |
| `review` | recenzent sprawdza zmiany | Review |
| `czeka` | potrzebna decyzja albo odpowiedź użytkownika | Czeka |
| `gotowe` | zrobione (po review albo zaakceptowane) | Gotowe |
| `odrzucone` | świadomie nie robimy | Gotowe (wyciszone, poza postępem) |

Tabelę i `updated` aktualizuj **przy każdym delegowaniu, wyniku podagenta, review i pytaniu do
użytkownika**. Sekcje pod tabelą (notatki, ryzyka, decyzje) to wolny tekst — Tide ich nie czyta.

## Konwencja delegowania

Tide łączy pracę podagentów z ticketami po ID, więc:

1. **Opis podagenta zaczyna się od ID ticketu**: `TOM-140: implement guard`, `TOM-140: review`,
   `TOM-140: review fixes`. Podagent Codexa ma ID w nazwie zadania (`task_name`, np. `tom140_guard`),
   w przydomku i w poleceniu.
2. **Gałąź zawiera ID małymi literami**: `tom-140-guard`, `feat/tide-7-board`, `codex/la-028-…`.
3. **Pytanie do użytkownika wymienia ID ticketu** (`TOM-141: przenieść backlog do osobnego projektu?`)
   — wtedy ticket trafia do kolumny „Czeka” z treścią pytania.
4. Recenzent ma w opisie albo typie słowo „review” (`code-reviewer`, `Review TOM-140 …`), a jego raport
   podaje liczby `BLOCKER` / `MAJOR` / `NIT` i werdykt (`APPROVE` albo `REQUEST CHANGES`).

## Jak Tide wyznacza stan

Z pracy podagentów (dzienniki koordynatora, tylko odczyt): pytanie o ticket bez odpowiedzi → `czeka`;
działa recenzent → `review`; działa inny podagent → `w-toku`; ostatnie review bez BLOCKER/MAJOR →
`gotowe`; review z BLOCKER/MAJOR → `w-toku` („do poprawy”); poprawki skończone po review → `gotowe`.
Plan wygrywa przy `odrzucone`, przy ticketach, nad którymi nikt nie pracował (`todo` i inne), i wtedy,
gdy jego `updated` jest nowsze niż ostatnia praca nad ticketem. YouTrack daje tytuły i adresy; jego
stan liczy się, gdy nie ma ani planu, ani pracy podagentów, albo gdy ticket jest tam zamknięty.

## Przykład

```markdown
---
tide-plan: 1
title: Release candidate TOM-20
tracker: youtrack
tracker-url: http://10.20.1.159:8111
tracker-project: LOG
ticket-prefix: TOM
coordinator: claude
session: e367f096-eb51-4f0d-9e0a-e93893d415c4
status: active
updated: 2026-10-26T21:13:00+01:00
---

| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| TOM-139 | Unikalne filtry w wyszukiwaniu | gotowe | `tom-139-filters` | implementer | review OK |
| TOM-140 | Distribution guard dla kompaktowej dystrybucji | w-toku | `tom-140-guard` | implementer | po review: 1 MAJOR |
| TOM-141 | Migracja epików z Lineara | czeka | | implementer | pytanie o backlog LA-* |
| TOM-146 | Eksport raportu do CSV z filtrami | todo | | | |

## Notatki

Kolejność: najpierw TOM-140 (blokuje RC), potem TOM-146.
```

## Mapa migracji ID

Gdy repozytorium ma `docs/agents/youtrack-migration/*.csv` (kolumny `source_key` albo `source_id`
i `youtrack_id`, opcjonalnie `kind`), Tide grupuje tickety po nowym kluczu: np. `TOM-10` (Linear) →
`LOG-128` (YouTrack), `LA-001` → `LEGAL-1`. Wiersze z `kind` innym niż `ticket` są pomijane.
