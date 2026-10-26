# tide-daemon

Usługa użytkownika systemd (Python 3, PyQt6), która czyta dzienniki sesji Claude Code i Codexa, wyprowadza stan każdej sesji i wystawia go przez D-Bus. Nie zmienia konfiguracji Claude Code ani Codexa — tylko czyta ich pliki.

## Kontrakt D-Bus

| Element | Wartość |
| --- | --- |
| Magistrala | sesji użytkownika |
| Nazwa usługi | `org.tide.Daemon` |
| Obiekt | `/Agents` |
| Interfejs | `org.tide.Daemon.Agents` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Sessions()` | `→ s` | Lista sesji jako JSON (tablica obiektów, opis niżej) |
| `ActivateSession(s id)` | `→ b` | Przechodzi do okna sesji. `false`, gdy okna nie da się ustalić |
| `Changed` | sygnał bez argumentów | Lista sesji się zmieniła — pobierz `Sessions()` ponownie |

Kolejność listy: najpierw czekające (najdłużej czekająca na górze), potem pracujące, bezczynne i zakończone (od najświeższej aktywności).

Pola sesji:

| Pole | Typ | Opis |
| --- | --- | --- |
| `id` | string | `claude:<id-sesji>` albo `codex:<id-sesji>` — argument `ActivateSession` |
| `tool` | string | `claude` albo `codex` |
| `sessionId` | string | identyfikator sesji w narzędziu |
| `project` | string | nazwa repozytorium (dla worktree — repozytorium głównego) albo katalogu |
| `branch` | string | bieżąca gałąź z `.git/HEAD`, zapasowo z dziennika |
| `worktree` | string | nazwa katalogu worktree albo pusty napis |
| `cwd` | string | katalog roboczy sesji |
| `title` | string | krótki tytuł rozmowy (tylko Claude Code, `ai-title`) |
| `state` | string | `working`, `waiting`, `idle`, `ended` |
| `reason` | string | dla `waiting`: `reply` (koniec tury), `approval` (zgoda na narzędzie), `question` (pytanie do użytkownika) albo wartość `waitingFor` zgłoszona przez Claude Code; inaczej pusty |
| `since`, `sinceTs` | ISO 8601 / sekundy epoki | od kiedy trwa bieżący stan (dla `waiting` — od kiedy czeka) |
| `lastActivity`, `lastActivityTs` | ISO 8601 / sekundy epoki | ostatni wpis agenta w dzienniku |
| `lastAction` | string | ostatnia czynność, np. `Edit · main.py`, `shell · make test` |
| `pid` | int | proces agenta albo 0 |
| `canActivate` | bool | czy `ActivateSession` ma dokąd przejść |
| `terminal` | string | nazwa okna docelowego, np. `Konsole`, `Codex` |

Z QML (moduł `org.kde.plasma.workspace.dbus`) korzysta z tego `widgets/lib/DaemonSource.qml`.

## Stany i progi

Pomijamy sesje, w których dziennik nie ma wpisu od ponad **24 h**.

| Stan | Kiedy |
| --- | --- |
| `working` | tura trwa: model myśli, pisze albo działa narzędzie |
| `waiting` | tura skończona (czeka na odpowiedź), narzędzie czeka na zgodę albo agent zadał pytanie |
| `idle` | czeka dłużej niż **30 min**; także tura bez żadnego wpisu przez 30 min i bez śladu działającego procesu |
| `ended` | proces sesji już nie działa |

Skąd to wiemy:

- **Claude Code** sam zapisuje stan działającego procesu w `~/.claude/sessions/<pid>.json` (`status`: `busy`, `waiting`, `idle`, plus `waitingFor`). To główne źródło — obejmuje także czekanie na zgodę. Gdy pliku nie ma (starsze wersje), stan wynika z dziennika `~/.claude/projects/<katalog>/<sesja>.jsonl`: `stop_reason: end_turn` albo `system/turn_duration` kończy turę, `tool_use` bez `tool_result` to wywołanie narzędzia w toku.
- **Codex** — `~/.codex/sessions/RRRR/MM/DD/rollout-*.jsonl`: `task_started` zaczyna turę, `task_complete` i `turn_aborted` ją kończą, `function_call`/`custom_tool_call` bez `*_output` to narzędzie w toku. Wątki podagentów i recenzenta zgód (`source.subagent`) nie są osobnymi sesjami.
- **Narzędzie w toku.** Przez **10 s** od wywołania sesja „pracuje”. Potem pracuje dalej tylko wtedy, gdy proces agenta uruchomił coś po wywołaniu (np. polecenie powłoki), narzędzie jest z natury długie (podagent, czekanie na podagenta) albo Codex ma `approval_policy: never`. W pozostałych przypadkach uznajemy, że czeka na zgodę. Długie wywołanie MCP bez własnego procesu może więc chwilowo wyglądać jak czekanie na zgodę.
- **Proces sesji.** Claude Code: `pid` z pliku `sessions/<pid>.json`. Codex: proces `codex`, który trzyma otwarty plik dziennika (w aplikacji Codex Desktop to `codex app-server`; wątek, którego aplikacja nie trzyma otwartego, jest pokazywany jako zakończony). Zapasowo: proces narzędzia z tym samym katalogiem roboczym.

## Odświeżanie

- `QFileSystemWatcher` na plikach sesji z ostatniej doby i na katalogach, w których pojawiają się nowe sesje; zmiana pliku → doczytanie tylko dopisanych wpisów po 250 ms,
- co 5 s przeliczenie stanów bez czytania dzienników (progi czasowe, procesy z `/proc`, ok. 15 ms),
- co 30 s przegląd katalogów (np. wznowiona stara sesja Codexa w katalogu sprzed tygodnia).

Przy pierwszym odczycie dużego pliku czytamy tylko pierwszą linię (metadane) i ostatni 1 MB.

## Przejście do okna

Best-effort. Z procesu agenta idziemy w górę drzewa procesów do znanego terminala (Konsole, kitty, Alacritty, foot, WezTerm, Ghostty…) albo aplikacji (Codex Desktop, VS Code). Dla Konsole najpierw przełączamy kartę (`org.kde.konsole.Window.setCurrentSession` z numerem z `KONSOLE_DBUS_SESSION`), potem skrypt KWin ładowany przez `org.kde.KWin /Scripting` (`loadScript`, `start`) aktywuje okno należące do tego procesu. Gdy terminala nie da się ustalić (np. tmux, ssh), sesja nie jest klikalna.

## Uruchamianie

```bash
make install-daemon          # kopia do $XDG_DATA_HOME/tide/daemon, usługa systemd, start
make uninstall-daemon        # zatrzymanie i usunięcie
journalctl --user -u tide-daemon -f

# Podgląd bez D-Bus (jednorazowy JSON):
/usr/bin/python3 daemon/tide-daemon.py --print
# Opcje diagnostyczne: --claude-dir, --codex-dir, --no-processes, --now 2026-10-26T12:00:00Z
```

Pliki instalacji: `$XDG_DATA_HOME/tide/daemon/`, `$XDG_CONFIG_HOME/systemd/user/tide-daemon.service`, `$XDG_DATA_HOME/dbus-1/services/org.tide.Daemon.service` (start na żądanie, gdy widżet zapyta, a usługa nie działa). Usługa używa `/usr/bin/python3` z systemowym PyQt6 — `python3` z pyenv go nie ma.

## Testy

`make test` (unittest, `/usr/bin/python3`):

- `daemon/tests/test_agents_core.py` — rdzeń na syntetycznych dziennikach z `daemon/tests/fixtures/` (zamrożony zegar, procesy podawane jawnie),
- `daemon/tests/test_dbus_contract.py` — usługa na prywatnej magistrali (własny `dbus-daemon`): te same dane co z rdzenia, oraz zmiana stanu w ciągu 3 s po dopisaniu wpisu; bez `dbus-daemon` test jest pomijany,
- `daemon/tests/test_install.py`, `widgets/tests/` — instalacja i odinstalowanie na tymczasowym HOME z atrapami `systemctl` i `kpackagetool6`.

Fixtures są pisane ręcznie i naśladują tylko strukturę wpisów — nie zawierają prawdziwych rozmów.
