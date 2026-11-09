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

## Partia 0: ręczna konsola offline (instrukcja dla właściciela)

To wyłącznie ręczna konsola offline właściciela w działającym kontenerze `plasma-tide`. Nie jest to
`OpenBoxConsole`, logowanie agenta ani konsola z agentem — tych v1 nie udostępnia. Ticket PT-292 tej próby
nie wykonuje; opis jest instrukcją do samodzielnego przejścia.

**Warunek wejścia:** kontener `plasma-tide` ma stan `RUNNING` (`incus list plasma-tide`), karta jest
wstrzymana (pauza w panelu karty, `SetCardPaused`), a pełny widok karty, dziennik i jednostki `tide-*` pokazują
zero kroków, zero poleceń outside i zero otwartych konsol. Stan nieznany = nie wchodzimy.

**Wejście** (TTY `-t`, uid/gid 1000, `HOME=/home/agent`; `<worktree>` to ścieżka worktree karty):

```bash
incus exec plasma-tide -t --user 1000 --group 1000 --env HOME=/home/agent --cwd <worktree> -- bash -l
```

**Cztery kontrole w konsoli:**

1. `id` pokazuje `uid=1000(agent)`. Jeśli widzisz `uid=0(root)` albo innego użytkownika — **stop**: `exit`
   i nic więcej w tej konsoli.
2. Plik próby: `git status --porcelain > /tmp/pt0-status-before.txt` (snapshot do `/tmp` w pudełku, nigdy do
   worktree), potem `touch pt0-probe.txt`. Na hoście `stat -c %U <worktree>/pt0-probe.txt` pokazuje `tomek`
   (właściciel hosta, nie root ani wysoki uid).
3. W konsoli `rm pt0-probe.txt`, następnie `git status --porcelain | diff /tmp/pt0-status-before.txt -` nie
   pokazuje różnic — git status jest taki sam jak przed próbą.
4. `curl -m5 -I https://example.org` kończy się błędem połączenia (brak DNS/NAT poza proxy) — odmowa
   example.org jest wynikiem oczekiwanym; odpowiedź HTTP oznacza stop i zgłoszenie.

**Wyjście:** `exit`. Snapshot `/tmp/pt0-status-before.txt` zostaje w `/tmp` pudełka, worktree jest bez zmian.

## Ustawienia `box` przez SetCardFlowSettings/GetCardFlowSettings (PT-292)

Sekcję `box` pliku `$XDG_CONFIG_HOME/tide/cardflow.json` (`daemon/tide_daemon/box/settings.py`) zapisuje
i czyta istniejąca para `/Board SetCardFlowSettings(request)` / `GetCardFlowSettings()` usługi `org.tide.Daemon`
(interfejs `org.tide.Daemon.Board`). Sygnatury bez zmian; nie ma osobnego polecenia `daemon/tide.py` dla
ustawień, więc wywołania idą przez `gdbus`, a stan pudełka nadal pokazuje `python3 daemon/tide.py box status`.

Obsługiwane klucze — jawny katalog `box/settings.py` `KEYS`: `enabled` (wyłącznie `true`/`false`),
`proxy-port` (liczba całkowita 1024–65535, `true`/`false` nie są liczbą), `bridge`, `project`, `base-image`,
`pool`, `nic`, `agents-version`, `ipv6-filtering`, `ip-wait-seconds`, `idle-hours`, `agent-uid`, `agent-gid`.

- Zapis jest częściowy: `{"box": {"enabled": false}}` zmienia tylko `box.enabled`; pozostałe pola `box`,
  `limitTransfer`, `providerSlots`, progi, opiekun i inne klucze pliku zostają.
- Zły request (`box` nie-obiekt/`null`/lista, zły typ albo zakres, nieznany klucz) → `{"ok": false,
  "error": "bad-settings"}` przed zapisem. Request mieszany (poprawne pole obok błędnego) nie zapisuje niczego.
  Usługa nie podstawia wartości domyślnej za błędną wartość z żądania.
- Odpowiedź Set i `GetCardFlowSettings` mają `box` ze skutecznymi wartościami (te same, które po restarcie
  czytają proxy, builder i runner) oraz `boxWarnings` — ostrzeżenia dla ręcznie zepsutych wartości pliku.

Sprawdzalne wywołania (odczyt, częściowy zapis, odczyt zwrotny):

```bash
gdbus call --session --dest org.tide.Daemon --object-path /Board \
  --method org.tide.Daemon.Board.GetCardFlowSettings
gdbus call --session --dest org.tide.Daemon --object-path /Board \
  --method org.tide.Daemon.Board.SetCardFlowSettings '{"box": {"enabled": false}}'
gdbus call --session --dest org.tide.Daemon --object-path /Board \
  --method org.tide.Daemon.Board.GetCardFlowSettings
```

Oczekiwane: obie odpowiedzi zawierają `"box": {"enabled": false, "proxy-port": 18080, …}` i niezmienione
`limitTransfer`/`providerSlots`. Odmowa: `SetCardFlowSettings '{"box": {"enabled": "true"}}'` daje
`"ok": false, "error": "bad-settings"`, a `GetCardFlowSettings` zwraca to samo co przed próbą.
Automatycznie sprawdza to `daemon.tests.test_cardflow_box_settings` (prywatna magistrala i restart usługi).

### Kontrakt portu z nftables i zmiana portu

Port proxy jest wspólnym kontraktem trzech miejsc: `box.proxy-port`, `scripts/box/install.sh --proxy-port`
(`make box-install BOX_PROXY_PORT=…`, ten sam zakres 1024–65535) i statycznej tabeli `inet tide-box`
w `/etc/tide-box/tide-box.nft` (generator `scripts/box/files.sh rules`). Usługa czyta ten plik
(`box/nftcontract.py`), nigdy nie wykonuje `sudo` i nie zmienia nftables.

- Zmiana `box.proxy-port` albo włączenie pudełka (`enabled: true`, też ze zmianą `bridge`) przechodzi tylko,
  gdy reguła „proxy Tide” łańcucha `input` tabeli `inet tide-box` wpuszcza dokładnie `box.bridge` → port.
  Inaczej `{"ok": false, "error": "box-install-mismatch"}` z opisem zainstalowanej reguły i procedurą;
  brak pliku = kontrakt niepotwierdzony, ta sama odmowa. Plik ustawień zostaje bez zmian.
- Start proxy z usługą sprawdza dodatkowo adres mostka: nasłuch tylko na adresie i porcie z reguł,
  inaczej proxy nie startuje (wpis w dzienniku), a kroki w pudełku odmawiają bez proxy.
- Wyłączenie (`enabled: false`) nie zależy od instalacji — powrót do pracy na hoście jest zawsze możliwy.

Procedura zmiany portu (np. na 18081) przez zatwierdzoną instalację:

1. Wstrzymaj karty i potwierdź zero kroków, outside i konsol (jak w partii 0).
2. `make box-install BOX_PROXY_PORT=18081` (sudo; plan pokazuje różnicę pliku reguł), potem
   `grep 'proxy Tide' /etc/tide-box/tide-box.nft` pokazuje `tcp dport 18081`.
3. `gdbus call … SetCardFlowSettings '{"box": {"proxy-port": 18081}}'`, odczyt zwrotny przez
   `GetCardFlowSettings` pokazuje `"proxy-port": 18081`.
4. Restart usługi (`systemctl --user restart tide-daemon`); dopiero restart przenosi nasłuch proxy.

### Zapis nie przełącza działających runnerów; włączenie poza v1

Zapis ustawienia nie jest atomowym przełączeniem działającej usługi: proxy czyta port i mostek przy starcie,
a nowe kroki czytają `box.enabled` przy starcie kroku. Kroki już uruchomione zachowują swój runner.
Procedura włączenia: wstrzymaj karty projektów i potwierdź zero kroków, outside i konsol → zapis
`SetCardFlowSettings '{"box": {"enabled": true}}'` (wymaga zgodnej instalacji) → restart usługi →
sprawdzenie nasłuchu proxy (`ss -ltnp 'sport = :18080'` na adresie mostka) → dopiero potem odblokowanie kart.
Wydanie v1 zostaje z `enabled=false`; część aktywująca (odbiór pracy w pudełku) jest poza v1 i nie jest tu
deklarowana jako zrobiona, podobnie jak odłożony PT-268.
