---
status: accepted
date: 2026-11-12
source: decyzja właściciela 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-2
---

# ADR-2: Uprawnienia z profilu projektu i jedna klasyfikacja polecenia

## Kontekst

Jedynym klasyfikatorem poleceń jest dziś `OUTSIDE_RISKY` (lista prefiksów), osobny od klasyfikacji kolejki ciężkiej
pracy i runnerów. Audytorzy (Fable, Astra) różnili się co do polecenia, które nie może działać pod izolacją:
„powrót na host” albo „odmowa”.

## Decyzja

- Profil projektu = klucz `permissions` w `projects/<slug>/box.yaml`, **poza hashem przepisu**, z własną **wersją
  zatwierdzenia** i **cofnięciem**, które działa także na polecenia oczekujące.
- Jedna funkcja `policy.classify(command, roots, profile) -> (klasa, powód)` dla `outside_risky`,
  `heavyqueue.classify` i obu runnerów (`HostRunner`, `BoxRunner`).
- `OUTSIDE_RISKY` zostaje **dolną warstwą** — regułą eskalacji, nie izolacją.
- Rozstrzygnięcie „powrót na host” vs „odmowa” według warunku wiążącego W4:
  - polecenie, które nie może działać pod izolacją (`needs` niespełnione), **nie dostaje automatycznego
    wykonania** — wraca do istniejącej decyzji właściciela (`OUTSIDE_RISKY`/`run`);
  - po jawnej zgodzie biegnie dzisiejszą drogą hostową jako **ta sama** `HostRunner.run_command` z profilem
    `isolation: none` **zapisanym w zdarzeniu** i liczonym w pomiarze (K-109 pkt 6);
  - nie ma trzeciego, stałego trybu bez zgody — jest odmowa automatu i zgoda człowieka;
  - gdy granicy zapisu nie da się ustalić (nieznany zbiór zapisu, nieznana historia) — odmowa, bez pytania.

## Konsekwencje

- Brak automatycznego powrotu na host bez zgody; każde wykonanie bez izolacji jest widoczne w dzienniku karty i w
  pomiarze.
- Kolejka ciężkiej pracy (ADR-3) i runnery (ADR-1) klasyfikują polecenie tą samą funkcją, więc nie mogą się
  rozjechać.

**Co temu dziś przeczy:** `OUTSIDE_RISKY` jako jedyny klasyfikator — przestaje nim być, gdy powstaje
`policy.classify` z profilem (K-109 etap 2).

## Karta wykonawcza i kamień

K-109 etap 2 (profil uprawnień), kamień **M2**.

## Pytania otwarte (dla K-109)

Raport nie zostawia tu decyzji otwartej. Nie wylicza klas zwracanych przez `policy.classify` ani postaci `needs` —
to należy do planu K-109 w ramach powyższej decyzji.
