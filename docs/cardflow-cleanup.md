# Konserwacja testowych kopii dzienników (PT-252 → PT-255)

Narzędzie `tools/cardflow_cleanup.py` nie odczytuje ustawień ani domyślnych katalogów
użytkownika. Wymaga `--source`: katalogu **kopii** `cardflow`, zawierającego katalogi
12-znakowych skrótów tablic, a w nich pliki `K-<numer>.jsonl`. Nie wskazuj repozytorium
Tablicy, `flow`, całego katalogu stanu ani worktrees.

PT-252 dostarcza narzędzie sprawdzone na danych syntetycznych. Rzeczywiste wykonanie
należy do PT-255 (#12), po review i scaleniu PT-251, PT-252 oraz PT-254 do gałęzi karty.
Dostarczenie poniższych poleceń nie oznacza wykonania sprzątania K-60.

## Kwalifikacja

Każdy plik kwalifikowany jest osobno. Akceptowane są zwykłe pliki bez dowiązań
twardych i symbolicznych, w rozpoznanym układzie ścieżek. Każda linia musi zawierać
poprawny obiekt JSON ze schematem `v: 1`, kolejnym `seq` od 1, zgodnym `card`,
niepustym `type` oraz datą `ts` ze strefą czasową. Nieznany schemat, urwana lub pusta
linia, powtórzony klucz i nieprawidłowa liczba JSON wykluczają usunięcie całego pliku.

Wymagany jest co najmniej jeden `step.started`. **Wszystkie** starty muszą mieć brak
`repo` lub dokładnie pusty napis oraz bezwzględny `worktree`, którego `realpath`
jest potomkiem `/tmp/`. Sam `/tmp`, ścieżka względna, `null`, białe znaki w `repo`,
wyjście przez `..` lub dowiązanie poza `/tmp` oznaczają pominięcie. Nie jest sprawdzana
liczba historycznych kopii. Jeden start testowy nie kwalifikuje innych plików ani
całego skrótu tablicy. Nowe pliki po podglądzie nie są automatycznie dodawane.

Brak historycznego katalogu worktree jest dozwolony. Rozwiązywanie ścieżki toleruje
wyłącznie brak pliku (`realpath` z `strict=os.path.ALLOW_MISSING`, wymagane także
w interpreterze uruchamiającym narzędzie). Pętla dowiązań, odmowa dostępu i składnik
niebędący katalogiem oznaczają `unknown-worktree` i zachowanie kopii. Dowiązanie do
nieistniejącego celu nadal jest rozwiązywane, więc cel poza `/tmp` wyklucza usunięcie.

## Okno bez zapisu

Podgląd niczego nie zmienia, chyba że jawnie podano `--plan`: wtedy tworzy wyłącznie
nowy plik metadanych poza źródłem. Zastosowanie ponownie sprawdza kwalifikację,
SHA-256 i tożsamość pliku (urządzenie, inode, tryb, liczba dowiązań, rozmiar, mtime,
ctime). Zmienione lub brakujące kandydaty są pomijane.
Jeżeli zwykłego pliku zachowywanego w źródle nie można odczytać i wyliczyć jego
sumy, zastosowanie odmawia przed usuwaniem: nie dałoby się wykazać niezmienności.

Sam znacznik `--writers-stopped` **nie zatrzymuje procesów**. Jest potwierdzeniem
obowiązku operatora: przez całe zastosowanie, kontrolę i powtórny przebieg nikt nie
może dopisywać ani podmieniać kopii. Blokada `Journal.lock` jest wewnątrz procesu
usługi i nie synchronizuje tego narzędzia. Dodatkowe odczyty i kontrole nie zastępują
zatrzymania piszących procesów; narzędzie nie gwarantuje ochrony przed procesem,
który celowo modyfikuje dane między ostatnim sprawdzeniem a `unlink`.

W PT-255 użyj `tools/cardflow_cleanup_host.py`. Wymaga początkowo aktywnej usługi
`tide-daemon.service`, zatrzymuje ją, sprawdza `ActiveState=inactive`, wykonuje
zastosowanie, kontrolę niezmienności, nowy podgląd oraz ponowne zastosowanie bez
kandydatów. W `finally` wznawia usługę i sprawdza `ActiveState=active`, również po
błędzie backupu, kontroli i ponownego przebiegu. Wynik niezerowy oznacza konieczność
sprawdzenia raportów; nie jest potwierdzeniem zakończenia sprzątania.

Przed tym poleceniem zakończ stare niezabezpieczone testy, zamknij klientów Tide
i wyklucz automatyczne wznowienie usługi oraz inne procesy piszące kopie. Potwierdza
to `--other-writers-stopped`. Nie uruchamiaj w tym oknie klientów ani starych testów.
Procedura nie zatrzymuje dowolnych procesów użytkownika ani nie zmienia konfiguracji
systemd. Nie używaj jej, jeżeli tych warunków nie można zapewnić. `finally` nie
zadziała po SIGKILL, utracie zasilania ani awarii interpretera; wtedy operator musi
wznowić usługę. Zwykłe błędy poleceń mają ścieżkę wznowienia.

## Pojedyncze polecenia dla PT-255

To konkretne przykłady dla domyślnego stanu użytkownika `tomek`. PT-255 ma najpierw
potwierdzić **metadanymi konfiguracji** rzeczywisty katalog stanu (w szczególności
`XDG_STATE_HOME`) i ścieżkę zaakceptowanego narzędzia w swoim worktree. Jeśli są inne,
wstaw odpowiednie bezwzględne ścieżki. Model nie odczytuje treści rzeczywistych
dzienników. Narzędzie uruchamiane jest poza piaskownicą, osobnym wynikiem `outside`.

Katalog `/home/user/DevWorkspace/tide-maintenance-k60` jest jawnym katalogiem
artefaktów, **poza** `/home/user/.local/state/tide/cardflow`. Musi już istnieć,
być prywatny dla użytkownika i nie zawierać podanych niżej nazw plików ani backupu.
Utworzenie go jest osobnym poleceniem hosta:

```sh
/usr/bin/mkdir -m 700 /home/user/DevWorkspace/tide-maintenance-k60
```

1. Domyślny podgląd z jawnym zapisem sprawdzonego zestawu (pierwszy `outside`):

```sh
/usr/bin/python3 tools/cardflow_cleanup.py --source /home/user/.local/state/tide/cardflow --plan /home/user/DevWorkspace/tide-maintenance-k60/preview.json
```

Po wznowieniu PT-255 sprawdza kod wyjścia i liczniki. Dopiero wtedy przekazuje
drugie polecenie `outside`, osobno od podglądu:

```sh
/usr/bin/python3 tools/cardflow_cleanup_host.py --source /home/user/.local/state/tide/cardflow --plan /home/user/DevWorkspace/tide-maintenance-k60/preview.json --backup-dir /home/user/DevWorkspace/tide-maintenance-k60/backup --report /home/user/DevWorkspace/tide-maintenance-k60/apply.json --repeat-plan /home/user/DevWorkspace/tide-maintenance-k60/repeat-preview.json --repeat-backup-dir /home/user/DevWorkspace/tide-maintenance-k60/repeat-backup --repeat-report /home/user/DevWorkspace/tide-maintenance-k60/repeat-apply.json --other-writers-stopped
```

To polecenie zawiera kontrolę i powtórny przebieg w jednym zatrzymanym oknie. Jeżeli
nowy podgląd znajdzie kandydatów (np. zmienionych od pierwszego podglądu), procedura
odmawia dalszego usuwania i wznawia usługę; potrzebny jest nowy podgląd i ocena.
Nie usuwa nowych danych tylko po to, aby otrzymać zero.

Dla operatora samodzielnie utrzymującego zatrzymane okno te same czynności są
dostępne osobno. Każdy poniższy wiersz jest jednym poleceniem:

```sh
/usr/bin/python3 tools/cardflow_cleanup.py --source /home/user/.local/state/tide/cardflow --apply --plan /home/user/DevWorkspace/tide-maintenance-k60/preview.json --backup-dir /home/user/DevWorkspace/tide-maintenance-k60/backup --report /home/user/DevWorkspace/tide-maintenance-k60/apply.json --writers-stopped
/usr/bin/python3 tools/cardflow_cleanup.py --verify /home/user/DevWorkspace/tide-maintenance-k60/apply.json
/usr/bin/python3 tools/cardflow_cleanup.py --source /home/user/.local/state/tide/cardflow --plan /home/user/DevWorkspace/tide-maintenance-k60/repeat-preview.json
/usr/bin/python3 tools/cardflow_cleanup.py --source /home/user/.local/state/tide/cardflow --apply --plan /home/user/DevWorkspace/tide-maintenance-k60/repeat-preview.json --backup-dir /home/user/DevWorkspace/tide-maintenance-k60/repeat-backup --report /home/user/DevWorkspace/tide-maintenance-k60/repeat-apply.json --writers-stopped
```

`--verify` po wznowieniu legalnej usługi może wykazać jej normalne zmiany. Dowód
niezmienności pochodzi z kontroli **przed** wznowieniem w procedurze hosta, a nie
z późniejszego porównania. Kod 0 oznacza sukces, 1 błąd lub odmowę, 2 błąd argumentów.
Przy zerze kandydatów powstaje raport, lecz nie fikcyjny backup.

## Backup, raport i odzyskanie

Backup powstaje pod nową nazwą, z prywatnymi uprawnieniami. W `files/` zachowuje
relatywne ścieżki kopii. `manifest.json` zawiera ich ścieżki, tożsamości i SHA-256.
Pliki i katalogi są synchronizowane na dysk; cały manifest i wszystkie sumy są
odczytywane ponownie przed pierwszym usunięciem. Awaria tworzenia albo sprawdzania
kopii zostawia wszystkie oryginały i częściowy backup do diagnozy. Nie wznawiaj
operacji przez nadpisanie takiego backupu — użyj nowych nazw po ocenie błędu.

Raport zapisuje liczbę zakwalifikowanych, listę i liczbę usuniętych/pominiętych,
powody pominięcia, bezwzględny backup, wynik jego weryfikacji oraz kontrolę zbioru
ścieżek i sum zachowanych kopii. Jest otwierany przed backupem i aktualizowany po
usunięciach. Zawiera tylko metadane; treść dzienników jest wyłącznie w prywatnym
backupie. Nie publikuj backupu ani treści dzienników. stdout podglądu zawiera tylko
liczniki; błędy JSON nie wypisują fragmentów wejściowych danych.

Usuwany jest tylko konkretny kandydat, potem jego katalog skrótu, jeśli jest pusty.
Nie ma rekurencyjnego usuwania. Backup nie leży w aktywnym drzewie i nie jest źródłem
przyszłych statystyk. Repozytorium Tablicy, worktrees i inne katalogi są poza operacją.

Odzyskiwanie wykonuje operator w zatrzymanym oknie: najpierw sprawdza manifest
i sumę wybranego pliku w `backup/files/<skrót>/K-<numer>.jsonl`, potem odtwarza
**wyłącznie brakujący** plik w tej samej relatywnej ścieżce aktywnych kopii. Tworzy
brakujący katalog skrótu z trybem 0700 i plik z trybem 0600, bez nadpisywania istniejącej
kopii i bez podążania za dowiązaniami. Porównuje SHA-256 odtworzonego pliku z manifestem,
a następnie wznawia usługę także po błędzie. Syntetyczny test odtwarza usunięty plik
i wykazuje identyczność zawartości; samo odzyskiwanie danych użytkownika wymaga osobnej
kontrolowanej operacji hosta. Nie kopiuj backupu do repozytorium Tablicy.
