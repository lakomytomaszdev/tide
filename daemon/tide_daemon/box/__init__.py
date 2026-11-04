"""Pudełko karty (tide-box): dane stosów i profil środowiska.

- templates.py — dane stosów (szablony YAML) i funkcje z nich wyprowadzone,
- yamlmini.py  — mały czytnik i zapis podzbioru YAML (bez biblioteki, jak reszta usługi),
- profile.py   — schemat `tide-box: 1`, klasyfikacja ryzyka, scalanie, hash przepisu, zapis w Tablicy,
- settings.py  — ustawienia pudełka usługi (`box.json`: projekt Incusa, proxy, karta sieciowa),
- incus.py     — cienki adapter CLI `incus` (zawsze `--project`),
- state.py     — dziennik pudełek w Tablicy, stany, `BoxStatus`, `card_box_name`,
- builder.py   — budowa z profilu, filtrowanie NIC, kopia per karta, status, reset, usunięcie, zatwierdzenie,
- cli.py       — `tide box …` (punkt wejścia `daemon/tide.py`).

Opis: docs/tide-daemon.md (Pudełko karty: profil środowiska; Pudełko → Budowa pudełka).
"""
