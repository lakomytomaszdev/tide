# Sprawa z rozmowy

Zapisz ocenialny raport Markdown o problemie lub oczekiwaniu użytkownika. Korzystaj
wyłącznie z rozmowy i jawnie dostarczonych dowodów. Nie skanuj projektu. Nie wymyślaj
plików, ścieżek, logów, stosów błędów, przeprowadzonych testów ani diagnozy.
Nie twórz HTML-a, kart, ticketów ani planu implementacji. Nie akceptuj sprawy za użytkownika.

Rozdziel fakty potwierdzone konkretnym dowodem, relacje użytkownika, oczekiwania,
hipotezy/założenia do sprawdzenia oraz niewiadome i braki dowodów. Opis ekranu przez
użytkownika nie jest niezależnym odczytem obrazu. Jeśli możesz obejrzeć dostarczony
obraz, podaj dokładnie, co potwierdza, i czego nie pokazuje. Oczekiwanie nie jest
zachowaniem już działającym. Zachowaj warunki wystąpienia, częstotliwość, zakres,
wyjątki i odrzucone warianty wraz z powodem odrzucenia. Kryteria mają podawać
obserwowalne kroki i wynik, włącznie z przypadkami brzegowymi i ograniczeniami.
Nagłówki i komplet sekcji same w sobie nie są dowodem jakości syntezy.

## Zapis i wznowienie

Wszystkie zapisy sprawy wykonuj wyłącznie przez publiczny CLI `tide ... --json`.
Helper `scripts/issue_adapter.py` wykonuje ten sam kontrakt dla obu programów.
Najpierw sprawdza `tide info --json`: API v1 i możliwości create/show/update/attach/submit.
Przy niedostępności API zatrzymaj się z komunikatem; nie obchodź API zapisem plików Tablicy.

Przed długą syntezą ustal jawny niepusty title i project (slug znanego projektu;
nie zgaduj go ze ścieżki). Zapisz krótki początkowy report bez chronionego frontmatteru.
Uruchom `begin --session <nowy-katalog> --title <tytuł> --project <slug> --report <plik>`.
Helper utrwala request-id i niezmienne argumenty create PRZED wywołaniem CLI.
Po create pokazuje S-N/draft i kończy: przerwanie nie gubi szkicu. Powtórz `begin`
z tym samym katalogiem po utracie odpowiedzi, bez zmiany request-id ani treści create.
Nie twórz nowej sesji w celu ponowienia tej samej intencji.

Dla poprawki istniejącego S-N użyj `resume --session <nowy-katalog> --id S-N`.
Helper odczytuje wersję przez show. Zawsze poprawiaj ten sam S-N, z odczytanym
expectedVersion; nie nadpisuj konfliktu. Po konflikcie pokaż różnicę użytkownikowi;
nowy odczyt nie jest zgodą na nadpisanie. Zmiana ready wraca do draft i wymaga
ponownego submit. Tytuł i projekt podawaj jawnie także przy poprawce.

Po syntezie uruchom `finish --session <katalog> --title <tytuł> --project <slug>
--report <plik> [--file <jawnie-wskazany-plik>] [--missing <nazwa-brakującego-dowodu>]`.
Powtórz --file i --missing dla każdego dowodu. Plik report to czysta treść Markdown:
bez YAML frontmatteru oraz pól id/state/version/attachments zarządzanych przez usługę.
Załącz tylko pliki z dostępnymi bajtami; opis lub podgląd bez pliku nie jest załącznikiem.
Po każdym attach helper zapisuje nową wersję. Nie usuwaj istniejących dowodów.
Gdy attach zawiedzie, helper zapisuje opis błędu w tym samym draft i zatrzymuje submit.
Nie ogłaszaj załączenia, dopóki CLI go nie potwierdzi. Błąd open nie cofa zapisu:
pokaż S-N i pozwól ponowić samo `tide issue open S-N --json`.

Brak bajtów obrazu wymaga --missing i opisu ograniczenia w raporcie: pozostaje draft,
bez submit. To obowiązek adaptera, nie walidacja prozy przez daemon; usługa sprawdza
tylko spójność istniejącego manifestu. Sam upływ czasu, brak odpowiedzi i prośba
ponowienia błędu nie oznaczają rezygnacji z dowodu.

Dopiero po odrębnej, świadomej i jawnej odpowiedzi użytkownika rezygnującej z konkretnego
brakującego dowodu użyj `waive --session <katalog> --evidence <dokładna-nazwa>
--decision <dosłowna-odpowiedź-użytkownika>`. Nie używaj waive na podstawie własnego
wniosku. Helper poprawia raport tego samego S-N przez expectedVersion, zachowuje
informację o rezygnacji i ograniczeniu dowodów, a następnie jawnie submituje wersję
po poprawce, o ile nie ma innych braków lub błędów attach. Nie tworzy nowej sprawy,
nie usuwa załączników, nie twierdzi, że brakujący plik zapisano. Konflikt zatrzymuje operację.
Kompletna sprawa przechodzi submit do ready i open do widoku Sprawy; przyjęcie należy
wyłącznie do użytkownika. Zawsze podaj końcowy S-N, wersję, stan i ograniczenia.
