# Klient pudełka v1 (PT-267)

Zachowane wejście: `python3 daemon/tide.py box status <projekt> [--card K-N] [--json]`
oraz `python3 daemon/tide.py box stop <projekt> [--card K-N]`.
Instalator demona kopiuje również `tide.py` obok `tide-daemon.py`; zainstalowany klient działa bez repozytorium.
Ujednolicenie polecenia `tide` pozostaje w K-75.

Klient wymaga działającej usługi `org.tide.Daemon` na sesyjnym D-Bus hosta i PyQt6.QtDBus.
Stan pochodzi z `/Board BoxStatus(slug)` albo `BoxCardStatus(slug, card)`, a zatrzymanie z
`StopBox(slug, card)`. Wszystkie odpowiedzi są obiektami JSON z `ok`; błędy mają `message` i `error`.
Kod wyjścia: 0 sukces, 1 błąd transportu/usługi, 2 argumenty, 3 odmowa lub nieużywalny stan pudełka.
Usługa zapisuje `box.stop` i czas operacji. Brak magistrali/usługi kończy polecenie bez lokalnej zmiany.

Usługa sprawdza przynależność karty i własność instancji, po czym stosuje istniejącą kontrolę
żywych kroków (w tym jednostek o nieznanym stanie po restarcie). Kontrola i stop trzymają tę samą
blokadę cyklu życia co rezerwacje kroków. Nieznana własność oznacza odmowę.
Klient w pudełku nie otrzymuje magistrali hosta: runner nie przekazuje jej adresu ani gniazda sesji.
Adres sesyjnego D-Bus w środowisku kroku wskazuje nieistniejące gniazdo, co wyklucza automatyczny start magistrali.

Do etapów II/III polecenia `base/build/reset/start/destroy/approve/login/run` oraz dawne
`python3 -m tide_daemon.box.runner --demo …` są wyłączone z komunikatem. Modułowe
`python3 -m tide_daemon.box.cli …` korzysta z tego samego klienta, bez bezpośredniego Incusa.

Samodzielne demo i staging sprawdza `daemon.tests.test_box_cli_service.ServiceDemoTest`:
własna Tablica i atrapa Incusa, prywatna magistrala D-Bus, zachowane `daemon/tide.py`, dozwolony stop,
odmowa przy rezerwacji i obcej instancji, zainstalowany klient i alternatywne wejścia, brak usługi.
Próba nie korzysta ze wspólnego repozytorium Git ani zasobów działającej sesji.
