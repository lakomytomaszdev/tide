# Sprawa z rozmowy

Zapisz raport Markdown o problemie lub oczekiwaniu użytkownika. Raport czyta najpierw
użytkownik, który decyduje o przyjęciu. Po przyjęciu cały raport staje się opisem karty:
bez tej rozmowy czytają go planujący, wykonawcy i recenzenci. Korzystaj wyłącznie
z rozmowy i jawnie dostarczonych dowodów. Nie skanuj projektu. Nie wymyślaj
plików, ścieżek, logów, stosów błędów, przeprowadzonych testów ani diagnozy.
Nie twórz HTML-a, kart, ticketów ani planu implementacji. Nie akceptuj sprawy za użytkownika.

## Układ raportu

Raport ma dokładnie trzy sekcje drugiego poziomu, w tej kolejności: `## Sedno`,
`## Pytania otwarte`, `## Opis sprawy`. Sedno i Pytania otwarte są zleceniem dla
planującego, Opis sprawy to materiał, do którego sięga w razie potrzeby. Nagłówki
i komplet sekcji same w sobie nie są dowodem jakości syntezy.

### Sedno

Krótki, samodzielny opis: czytelnik, który przeczyta tylko tę sekcję, wie, co ma zostać
zrobione i po co. Zwykle kilka akapitów albo krótka lista; długość rośnie z liczbą decyzji
użytkownika, a nie z ilością materiału. Zawiera:

- problem albo potrzebę i oczekiwany wynik słowami użytkownika. Oczekiwanie nie jest
  zachowaniem już działającym;
- zakres i to, co poza zakresem, wraz z warunkami wystąpienia, częstotliwością i wyjątkami;
- decyzje użytkownika oraz odrzucone warianty wraz z powodem odrzucenia;
- sposób sprawdzenia podany przez użytkownika: obserwowalne kroki i wynik, włącznie
  z przypadkami brzegowymi i ograniczeniami.

Nie dopisuj własnych kryteriów akceptacji, wymagań ani zakresu. Ułoży je planujący,
który zobaczy projekt. Własny pomysł, którego użytkownik nie wypowiedział, może trafić
najwyżej do pytań otwartych.

### Pytania otwarte

To, co pozostaje nierozstrzygnięte, w dwóch listach. Pusta lista to jedno słowo „brak”.

- **Do użytkownika**: odpowie tylko użytkownik (cel, zakres, priorytet, wybór wariantu).
  Tylko pytania, od których zależy zakres albo kształt pracy. Każde w jednym zdaniu,
  z dopiskiem, co od niego zależy. Bez pytań o rzeczy, które da się sprawdzić w projekcie.
- **Do sprawdzenia w projekcie**: to, co da się ustalić w repozytorium albo przez
  uruchomienie. Tu trafiają też hipotezy/założenia z rozmowy, opisane jako niepotwierdzone.

Pod nagłówkiem sekcji zapisz dosłownie zdanie: „Planujący: sprawdź w projekcie to, co da
się sprawdzić; pytania do użytkownika zadaj mu, a gdy pracujesz bez pytań, zapisz przy
każdym jawne założenie w specyfikacji.” Dzięki temu pytania przechodzą ze sprawy na kartę
i do planu, a użytkownik widzi przyjęte założenia, zanim zatwierdzi plan.

### Opis sprawy

Materiał z rozmowy, zwięźle. Rozdziel relacje użytkownika (najważniejsze dosłownie),
fakty potwierdzone konkretnym dowodem oraz niewiadome i braki dowodów. Oczekiwania są już
w Sednie, nie powtarzaj ich. Opis ekranu przez użytkownika nie jest niezależnym odczytem
obrazu. Jeśli możesz obejrzeć dostarczony obraz, podaj dokładnie, co potwierdza i czego
nie pokazuje. Faktem jest także to, co ta rozmowa rzeczywiście ustaliła przed zgłoszeniem
(przeczytany plik, wynik polecenia): podaj wtedy źródło.

Nie streszczaj instrukcji projektu, stanu repozytorium ani innego kontekstu, który sesja
dostała sama z siebie. Planujący przeczyta je w projekcie, w aktualnej wersji. Przytocz
z nich tylko to, co jest dowodem w tej sprawie albo co wskazał użytkownik.

## Pytania do użytkownika po zapisie

Nie wstrzymuj zapisu sprawy pytaniami. Po `finish` pokaż użytkownikowi listę „Do
użytkownika” i zaznacz, że może odpowiedzieć teraz albo zostawić pytania planującemu.
Gdy odpowie, popraw ten sam S-N przez `resume`: odpowiedź przenieś do Sedna jako decyzję,
a pytanie usuń z listy. Nie odpowiadaj na te pytania za użytkownika.

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
