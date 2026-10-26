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

## Później

- Tryb skupienia.
- Wariant tapety dzień/noc.
- Własny dock „Tide Dock”, jeśli mieszanka ikon nie przejdzie.
- Hooki Claude Code i Codex dla natychmiastowych stanów.

## Rozstrzygnięte po sesji

- **Logo (#3):** propozycja 5, „trzy fale” (teal, lawenda, brzoskwinia). Generowana z palety w trzech wersjach: pełna na kafelku do dużych rozmiarów, uproszczona bez kafelka na pasek (czytelna od 18 px) i jednokolorowa, przefarbowywana przez Plasmę.
- **Firefox:** profil lakomytomaszdev zostaje przy własnym niebieskim motywie, bo kolory motywów rozróżniają profile.
- **Ikony (#7):** za spójną uznajemy tylko ikonę z rodziny motywu bazowego (dla McMojave-circle-dark także McMojave-circle). Ikona znaleziona dopiero w motywie zapasowym (np. Breeze dla Falkona) ma inny kształt, więc audyt traktuje ją jak własną ikonę aplikacji i dostaje kafelek. Własna ikona aplikacji, także z `/usr/share/pixmaps` (ChatGPT), ma pierwszeństwo przed generowanym glifem. Glif powstaje tylko wtedy, gdy ikony nie ma albo jest bitmapą mniejszą niż 48 px (Kasumi), bo po powiększeniu byłaby rozmyta.
