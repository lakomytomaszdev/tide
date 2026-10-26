"""Limity AI w usłudze: historia odczytów, prognoza wyczerpania i powiadomienia o progach.

Dane pochodzą z lokalnego API usługi QuotaApp (GET /status — tylko odczyt z jej pamięci,
bez wczytywania stron). Wszystko tutaj jest bez Qt:

- config.py   — progi i okna z palety (sekcja „limits” w theme/palette/mist.json),
- backend.py  — port i token z config.json usługi (czytane w chwili zapytania), GET /status,
- readings.py — odpowiedź /status → odczyty (usługa, okno, zużycie, reset jako znacznik czasu),
- history.py  — historia w $XDG_STATE_HOME/tide/limits-history.jsonl i podział na okna limitu,
- forecast.py — tempo z ostatnich odczytów okna i prognoza „wyczerpie się ok. 17:40”,
- alerts.py   — powiadomienia raz na okno (80%, 95%, wyczerpanie przed resetem), stan na dysku,
- tracker.py  — spina powyższe: odczyt → historia → prognoza → alerty → JSON dla D-Bus.

Opis i kontrakt D-Bus: docs/tide-daemon.md (sekcja „Limity AI”), uzasadnienie: docs/decisions.md.
"""
