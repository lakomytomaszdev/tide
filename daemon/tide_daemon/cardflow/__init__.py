"""Przepływ karty (docs/agents/specs/2026-10-28-przeplyw-kart.md): dziennik karty, stan z dziennika, kroki w tle.

- journal.py — dziennik karty w repozytorium tablicy z kopią usługi,
- state.py — stan karty i pole `derived` z dziennika,
- decisions.py — `derived.flow.decisions[]`: decyzje karty dla okna decyzji (co się stało, dlaczego, możliwości),
- gitops.py — gałęzie i worktree karty i ticketu, scalenie ticketu do gałęzi karty,
- handoff.py — kartka przekazania kroku ticketu,
- engine.py — zlecenie „Od razu”, krok w tle, strażnik kroku, ręczne kolumny.
Część z Qt (zegar, wątek roboczy, commity tablicy): cardflow_monitor.py.
"""
