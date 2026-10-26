# Decyzje projektowe

Ustalenia z sesji projektowej (grilling) z 26.10.2026. Każda decyzja ma krótkie uzasadnienie, żeby za pół roku było wiadomo, dlaczego jest tak, a nie inaczej.

## Projekt i repozytoria

1. **Podział repozytoriów.** `plasma-tide` zawiera motyw i wszystkie własne widżety. [QuotaApp](https://github.com/julia-lakomy/QuotaApp) zostaje osobnym forkiem i służy wyłącznie jako usługa danych dla Limitów AI. Nasze zmiany w nim (sesja z Firefoksa) leżą na osobnej gałęzi. Wygląd widżetu Limity AI należy do Tide. Dzięki temu poprawki parsera od autorki da się ściągnąć zwykłym `git pull`.
2. **Nazwa i hosting.** Projekt „Tide”, pierwszy wariant „Mist”. Prywatne repozytorium na GitHubie (konto lakomytomaszdev). Commity po angielsku, push tylko na wyraźną prośbę.
3. **Instalacja kopią, nie dowiązaniami.** `make install` kopiuje do `~/.local/share` i przed każdą instalacją robi kopię zapasową. Pulpit nie zależy od tego, która gałąź jest akurat wybrana.

## Wygląd

4. **Styl Plasmy.** Własny styl „Tide” wyprowadzony z Breeze, z naszymi kształtami (promienie, ramki, przezroczystość). Kolory nadal pochodzą ze schematu kolorów. Na start (etap 1) Breeze z naszymi kolorami, a własne kształty dochodzą przy górnym pasku i docku.
5. **Dekoracja okien.** Własny motyw Aurorae generowany z palety, bez kompilacji. Przyciski po prawej w kolejności minimalizuj, maksymalizuj, zamknij (masło, mięta, róż). Klassy odrzucona, bo nie ma paczki w KDE neon i wymaga przebudowy po każdej aktualizacji KWin.
6. **Czcionki.** Geist w interfejsie, Geist Mono w liczbach widżetów. Rider zostaje przy swoich czcionkach. SF Pro odpada przez licencję.
7. **Tapeta.** Generowana z palety, płynnie przechodząca między monitorami. Łatwa do podmiany na własną.
8. **Motyw globalny Tide** spina całość i zawiera własny ekran blokady i ekran ładowania. Ekran logowania (SDDM) to osobny, własny motyw instalowany systemowo. Wszystkie trzy najpierw na mockach i testowane w trybach testowych (`kscreenlocker_greet --testing`, `sddm-greeter-qt6 --test-mode`), zanim trafią do systemu.
9. **Logo.** Do wyboru z propozycji na kanwie.
10. **Aplikacje.** Konsole, Firefox (profil lakomytomaszdev, przez motyw systemowy) i aplikacje GTK w palecie Mist. Rider bez zmian. Pozostałe profile Firefoksa bez zmian.

## Układ

11. **Główny monitor (DP-2, 2560×1440):** górny pasek z globalnym menu i zegarem z datą, pływający dock z kafelkami widżetów.
12. **Lewy monitor (HDMI-A-1, 1920×1080):** smukły górny pasek z globalnym menu i zegarem, żeby menu aplikacji było na tym samym ekranie co okno. Do tego chowany pasek boczny na lewej, zewnętrznej krawędzi z rozbudowanymi wersjami widżetów, wysuwany myszą albo skrótem Meta+S. Nie na krawędzi między monitorami, bo tam mysz przechodzi na drugi ekran.

## Ikony

13. **Trzy warstwy:** motyw bazowy, nakładka Tide (aliasy, foldery, brakujące ikony) i kafelki generowane z oryginałów dla aplikacji spoza motywu.
14. **Stonowane glify w docku: decyzja po próbie.** Dock i menu biorą ikonę z tego samego źródła, więc stonowana ikona aplikacji z docka pojawi się też w menu. Rozstrzygamy na prawdziwych ikonach w trzech wersjach: wszystko kolorowe, mieszanka wprost, mieszanka na wspólnym kafelku. Wyjście awaryjne to własny widżet „Tide Dock”.

## Praca z AI

15. **Architektura.** Jedna usługa użytkownika `tide-daemon` (Python, PyQt6, D-Bus) jako jedyne źródło danych. Widżety w czystym QML czytają ją przez `org.kde.plasma.workspace.dbus`. KRunner, skrót i powiadomienia korzystają z tej samej usługi.
16. **Widżet agentów:** Claude Code i Codex (T3 Code wychodzi przy okazji). Najpierw czytanie dzienników sesji (`~/.claude/projects`, `~/.codex/sessions`), hooki dopiero wtedy, gdy opóźnienie zacznie przeszkadzać.
17. **Usprawnienia:** skrót i powiadomienie „przejdź do czekającego agenta”, kolor okna według stanu agenta (najpierw prototyp, bo nie wiadomo, czy reguły KWin da się przełączać w locie), KRunner z sesjami i projektami. Tryb skupienia trafia na listę „później”.
18. **Worktrees:** tylko dane lokalne, bez `git fetch` i bez VPN. Sprzątanie zawsze z potwierdzeniem, bez `--force`, a gałąź jest kasowana tylko po scaleniu.

19. **Stan sesji agentów (26.10.2026, #9).** Claude Code sam zapisuje stan działającego procesu w `~/.claude/sessions/<pid>.json` (`busy`, `waiting`, `idle`), także czekanie na zgodę — to główne źródło, a dziennik jest zapasowym. Codex nie zapisuje próśb o zgodę, więc wnioskujemy o nich z wywołania narzędzia bez wyniku po 10 s, bez nowego procesu potomnego i przy `approval_policy` innym niż `never`. Sesja czekająca ponad 30 min jest bezczynna, sesje bez wpisu od doby pomijamy. Oba formaty są nieudokumentowane, więc parsery ignorują nieznane wpisy, a testy opierają się na syntetycznych dziennikach.
20. **Biblioteka QML w każdym pakiecie.** Plasma instaluje widżety jako osobne pakiety, więc `widgets/lib` jest dołączana do każdego z nich przy składaniu (`widgets/stage.py`), razem z `Tokens.qml` wygenerowanym z palety. Singleton nazywa się `Tokens`, bo `Palette` koliduje z typem wbudowanym w QtQuick.
21. **Limity AI bez tide-daemon (26.10.2026, #15).** Widżet `org.tide.limits` czyta lokalne API QuotaApp bezpośrednio (`GET /status` co 10 s i przy otwarciu popupu, `POST /refresh`, `POST /open`), bo usługa już istnieje, ma własny cykl odczytu i nie zmieniamy jej API. Token nie jest w repozytorium: `widgets/stage.py` przepisuje go z `config.json` usługi do `Config.js` w pakiecie (tryb 0600), tak jak robi to instalator QuotaApp. Bez konfiguracji pakiet powstaje z pustym tokenem, a widżet mówi, co doinstalować — brak QuotaApp nie blokuje `make install`. Podmiana starego widżetu w panelu to osobny cel `make swap-limits-widget`, nie część instalacji.
22. **Worktrees (26.10.2026, #12).** Lista pochodzi z `git worktree list` repozytoriów znalezionych w `~/DevWorkspace` (do 3 poziomów, bez katalogów ukrytych i zależności). „Scalony” to czubek gałęzi będący przodkiem lokalnej `master`/`main`/`develop` — liczone jednym `git rev-list --count`, które daje też liczbę niescalonych commitów. Data ostatniej zmiany to najnowsze z czasu commita HEAD i mtime plików z `git status` — tanie, bo status i tak czytamy; chodzenie po wszystkich plikach co minutę byłoby za drogie. Rozmiar jak `du -s`, w pamięci podręcznej na 30 min. Cała praca z gitem idzie w jednym wątku roboczym, a `Remove` odpowiada z opóźnieniem (delayed reply), więc D-Bus nie stoi. Przed usunięciem stan repozytorium jest czytany na nowo. Otwieranie w terminalu i Riderze robi usługa (`Open`), bo QML nie uruchomi programu z katalogiem roboczym, a skrypt Ridera z Toolboksa trzeba znaleźć poza `PATH`.
23. **Skok do czekającego agenta (26.10.2026, #10).** Skrót Meta+A rejestruje mały skrypt KWin (`registerShortcut`), który woła `ActivateLongestWaiting` w tide-daemon. Odrzucona rejestracja w KGlobalAccel prosto z usługi: na Waylandzie w Plasmie 6 skróty obsługuje KWin, a protokół D-Bus kglobalaccel jest wewnętrzny (struktury klawiszy zmienione między KF5 i KF6, aktywność komponentu zależna od życia klienta) — skrypt KWin to stabilne API, skrót przeżywa restart usługi i da się go zmienić w Ustawieniach. Powiadomienia idą przez `org.freedesktop.Notifications` z akcją „Przejdź”; ta sama sesja dostaje je ponownie dopiero po wyjściu ze stanu „czeka” i ponownym wejściu, a sesje czekające przy starcie usługi są pomijane. Gdy sesja przestaje czekać, powiadomienie jest zamykane. Najdłużej czekająca to ta z najwcześniejszym `sinceTs` spośród sesji z ustalonym oknem.

## Później

- Tryb skupienia.
- Wariant tapety dzień/noc.
- Własny dock „Tide Dock”, jeśli mieszanka ikon nie przejdzie.
- Hooki Claude Code i Codex dla natychmiastowych stanów.

## Rozstrzygnięte po sesji

- **Logo (#3):** propozycja 5, „trzy fale” (teal, lawenda, brzoskwinia). Generowana z palety w trzech wersjach: pełna na kafelku do dużych rozmiarów, uproszczona bez kafelka na pasek (czytelna od 18 px) i jednokolorowa, przefarbowywana przez Plasmę.
- **Firefox:** profil lakomytomaszdev zostaje przy własnym niebieskim motywie, bo kolory motywów rozróżniają profile.
- **Ikony (#7):** za spójną uznajemy tylko ikonę z rodziny motywu bazowego (dla McMojave-circle-dark także McMojave-circle). Ikona znaleziona dopiero w motywie zapasowym (np. Breeze dla Falkona) ma inny kształt, więc audyt traktuje ją jak własną ikonę aplikacji i dostaje kafelek. Własna ikona aplikacji, także z `/usr/share/pixmaps` (ChatGPT), ma pierwszeństwo przed generowanym glifem. Glif powstaje tylko wtedy, gdy ikony nie ma albo jest bitmapą mniejszą niż 48 px (Kasumi), bo po powiększeniu byłaby rozmyta.
- **Styl Plasmy (#16):** w stylu „Tide” są tylko ramki o nowym kształcie, reszta spada na Breeze. Każda ramka ma wszystkie warianty katalogów, które ma Breeze (`translucent/`, `solid/`, `opaque/`), bo Plasma szuka najpierw w wariancie — brak naszego wariantu oznaczałby kształt z Breeze. Górny pasek i dock dzielą `widgets/panel-background`, więc pasek dostaje elementy z prefiksem `north` (Plasma wybiera go dla panelu u góry), a dock elementy bez prefiksu. Cień panelu jest jeden dla obu, rysowany pod mniejszy promień (paska): przy docku daje to wąską szczelinę bez cienia przy rogach, ale nie ciemny klin pod półprzezroczystym paskiem. Tło paska ma kolor `HeaderBackground` ze schematu (najbliższy `panel` z makiety), okna i podpowiedzi zostają przy `Background`, jak w Breeze.
- **Górne paski (#16):** panele zmieniamy skryptem plasmashell, nie edycją `appletsrc`. Główny pasek to górny panel głównego monitora (numer 73 tylko jako podpowiedź), pasek boczny ma znacznik w konfiguracji. Widget „Ikona” (skrót do `index.html`) znika z paska; jego plik `.desktop` jest w zrzucie i wraca przy odinstalowaniu. Kolejność widżetów ustawia drugi skrypt przez `AppletOrder` i `Widget.index`, bo Plasma dokłada nowe widżety asynchronicznie.
