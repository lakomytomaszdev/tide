# Zasoby zainstalowanego Tide

`make install` wykonuje kopię zapasową, po czym instaluje usługę, CLI i zasoby Tide.
`tide info --json` przez publiczny D-Bus zwraca `resources`: dla standardu planu,
narzędzia rezerwacji numerów i kontroli planów są to `path`, `version`, `available`
oraz SHA-256 dostępnej treści. Odpowiedź nie zawiera instrukcji skilli, ustawień ani sekretów.
Dotychczasowe `apiVersion` i możliwości API spraw pozostają dostępne także przy niepełnych zasobach.

Pliki Tide leżą w `$XDG_DATA_HOME/tide/resources` (domyślnie `~/.local/share/tide/resources`):
`docs/agent-plan-standard.md`, `tools/next_id.py`, `tools/check_plans.py`.
CLI uruchamia narzędzia z lokalizacji zwróconej przez usługę, przekazując argumenty bez powłoki:

```sh
tide info --json
tide next-id "/ścieżka ze spacjami/projekt" --title "Opis ticketu" --dry-run
tide check-plans "/katalog projektów" --no-youtrack --json
```

Wpisy narzędzi w `info` zawierają też `pythonPath`: katalog modułów działającej usługi.
Klient przekazuje go jako `PYTHONPATH` procesu narzędzia, niezależnie od lokalizacji zasobów.
Narzędzia wymagają zainstalowanych modułów usługi; sam klient nadal wymaga publicznego D-Bus
i `python3-pyqt6.qtdbus`. Nie trzeba mieć checkoutu autora. W checkoutcie resolver korzysta
z jego własnych `docs/` i `tools/`; uruchomienie samodzielnej kopii usługi wybiera sąsiedni katalog
`resources`. Własny pakiet można wskazać przez `TIDE_RESOURCES_DIR` w środowisku usługi
(katalog zawierający `docs/` i `tools/`). Jawne ustawienie nie ma cichego fallbacku.
Brak standardu lub narzędzia w kroku daje komunikat z nazwą zasobu i sposobem konfiguracji;
`info` pokazuje `available: false`, aby umożliwić diagnozę.

## Metodologie i szablony

Tide dystrybuuje własny standard i narzędzia. Zewnętrznych metodologii nie kopiuje.
Mechanizm K-59 dostarcza Claude plugin z odsyłaczami do oryginalnych SKILL.md,
a Codexowi katalog i instrukcję bezpośredniego odczytu. Oba programy czytają zasoby
względem oryginalnego katalogu skilla. Instrukcje muszą być zainstalowane osobno,
zgodnie z ich prawami dystrybucji, w `~/.claude/skills` lub `~/.agents/skills`
(Codex obsługuje też starsze `~/.codex/skills`). Brak wymaganej instrukcji agent
zgłasza jawnym błędem kroku. Nie jest to odmowa przed modelem. Prywatne ustawienia,
hooki i konfiguracja MCP nie są częścią pakietu Tide ani informacji o zasobach.

Zaczyny `flows/` używają pola `{tide_standard}`, rozwiązywanego przy renderowaniu,
oraz poleceń CLI. Migracja rozpoznaje SHA-256 dawnych niezmienionych zaczynów;
pojedyncza zmiana użytkownika wyklucza automatyczne nadpisanie. Nadpisania projektu
pozostają własnością użytkownika. Własny szablon zawierający niedostępne odwołanie
do checkoutu autora pozostaje bajtowo bez zmian i dostaje ostrzeżenie w błędach Tablicy.
Można poprawić go ręcznie, używając `{tide_standard}`, `tide next-id` i `tide check-plans`.

## Odinstalowanie

`make uninstall` usuwa klienta, usługę i cały katalog zasobów Tide.
`uninstall-cli` usuwa tylko klienta: zasoby pozostają dla działającej usługi.
`uninstall-daemon` usuwa także `tide/resources`. Własny katalog wskazany przez
`TIDE_RESOURCES_DIR`, skille użytkownika, projekty i zmodyfikowane szablony Tablicy
nie są usuwane. Instalacja ani odinstalowanie nie powinny być wykonywane ręcznymi
edycjami plików w katalogach konfiguracji użytkownika.

Testy instalacyjne używają własnych HOME/XDG, innej nazwy użytkownika, ścieżek ze spacjami
i atrap systemd/modelu. Nie uruchamiają instalacji na prawdziwym pulpicie.
