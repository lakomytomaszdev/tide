# PT-191: przerwana trzecia próba

Polecenie: `python3 scripts/box/boundary-trial.py --enable-snapshots --retry-failed-build`.
Wynik przekazany przez Tide: przekroczony czas, `Terminated`.

Ostatnie zdarzenia ze zlecenia:

```text
PRÓBA snapshots-before: ['incus', 'project', 'get', 'user-1000', 'restricted.snapshots']
WYNIK snapshots-before: 0
PRÓBA snapshots-enable: ['sudo', '-A', 'incus', 'project', 'set', 'user-1000', 'restricted.snapshots=allow']
Terminated
```

Zapisane pliki zawierają tylko odczyt sprzed zmiany: pustą wartość właściwości i brak stderr.
Nie ma wyniku polecenia zmieniającego politykę ani odczytu po zmianie. Nie potwierdzamy stanu po przerwaniu.
Budowa, pełne testy, dowody środowiska/argv i czerwona lista nie zostały w tej rundzie wykonane.
