"""Koordynacja agentów: sesje, które delegują pracę podagentom, jako tablica ticketów.

- claude.py, codex.py — odczyt dzienników koordynatorów i ich podagentów,
- model.py           — przebiegi (Run) i rundy (Round) podagentów, pytania koordynatora,
- ids.py             — ID ticketów w opisach, gałęziach i poleceniach; mapa migracji ID,
- plan.py            — plan pracy w repozytorium (docs/agent-plan-standard.md),
- review.py          — liczby BLOCKER/MAJOR/NIT i werdykt z raportu recenzenta,
- youtrack.py        — YouTrack tylko do odczytu (GET), z pamięcią podręczną,
- board.py           — tickety, ich stan i oś czasu,
- describe.py        — opis kart po ludzku (title, done, now): model przez bezpieczne `claude -p` albo reguły,
- scanner.py         — koordynatorzy z ostatnich dni i ich tablice.

Opis: docs/tide-daemon.md (Koordynacja), uzasadnienia: docs/decisions.md.
"""
from .scanner import CoordinationScanner

__all__ = ["CoordinationScanner"]
