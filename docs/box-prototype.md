# Prototyp pudełka karty — wyniki prób (K-38)

Próby z `docs/card-box-design.md`, sekcja 10. Każda próba to skrypt `scripts/box/prototype/p<N>.sh` uruchamiany
przez użytkownika poza piaskownicą agenta; pełny dziennik w `docs/box-prototype/p<N>-<czas>.log` (wiersze
`WYNIK …`), dzienniki proxy (odmowy, dostęp — bez nagłówków i tokenów) w katalogu `p<N>-<czas>/` obok.
P-1, P-4, P-5, P-9 dopisuje PT-164 (#1); P-2, P-3, P-6, P-7 — PT-178 (#15). Decyzje z prób:
`docs/box-decisions.md`.

Pudełko prób PT-178: `tide-proto-net` (`images:ubuntu/24.04`, `security.nesting=true`) w projekcie użytkownika,
sieć tylko przez nftables z PT-166 (z mostka wolno wyłącznie do `<adres mostka>:18080` i DHCP). Proxy prototypowe
`scripts/box/prototype/proxy.py` słucha na adresie mostka, na porcie 18080: CONNECT do domen z listy, zwykłe HTTP do
domen z listy (wariant awaryjny apt) i trasy wstrzykujące `/anthropic`, `/chatgpt` (K3). Sprzątanie:
`scripts/box/prototype/cleanup.sh`.

Przebiegi: 1 — 2026-11-04 12:33 (P-6 padło na źródłach apt); 2 — 13:07 (pełny P-6 bez Playwright i NO_PROXY, P-3
Claude, P-2 wariant A); 3 — po poprawkach z przebiegu 2 (Playwright na Node 18, NO_PROXY, Codex, P-2 wariant B).

## Tabela prób

| Próba | Pytanie | Skrypt | Wynik | Dziennik |
| --- | --- | --- | --- | --- |
| P-2 | bwrap (Claude Code) i landlock (Codex) w kontenerze przy `apparmor_restrict_unprivileged_userns=1` | `p2.sh` | **wariant A (pudełko z obrazu): NIE działa** ani bwrap, ani piaskownica Codexa. Goły `bwrap --unshare-all` → `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; `unshare --user --map-root-user` → `write failed /proc/self/uid_map: Operation not permitted`. Jądro: `apparmor="DENIED" operation="capable" namespace="root//incus-user-1000_tide-proto-net_<var-lib-incus>" profile="unprivileged_userns"` (setpcap, net_admin, sys_admin) — ograniczenie hosta działa też w przestrzeni AppArmor pudełka (profil init: `incus-user-1000_tide-proto-net_…//&:…:unconfined`). `claude -p` z `sandbox.enabled=true`, `failIfUnavailable` odmawia startu: „sandbox required but unavailable … Ubuntu's AppArmor restriction on user namespaces stops bubblewrap”. Codex 0.166 `codex sandbox linux` też używa bwrap — ten sam błąd. **Wariant B** (profil `bwrap` z `userns,` wczytany w pudełku) — przebieg 3 | `p2-2026-11-04T132223.log` |
| P-3 | Claude Code (OAuth) przez `ANTHROPIC_BASE_URL` i Codex przez `chatgpt_base_url` na proxy wstrzykujące (K3) | `p3.sh` | **Claude Code: K3 możliwe** — `ANTHROPIC_BASE_URL=http://<mostek>:18080/anthropic CLAUDE_CODE_OAUTH_TOKEN=<atrapa> claude -p 'hi'` 3/3 kod 0, proxy: 3× `INJECT POST /anthropic/v1/messages` → **200**, w pudełku tylko atrapa. Odświeżenie OAuth: 0 żądań (ani base URL, ani CONNECT) — pudełko nie odświeża, token odświeża host, proxy czyta plik przy każdym żądaniu. Poza base URL Claude Code próbuje `CONNECT api.anthropic.com:443` (18× w 3 uruchomieniach, odmowa, bez wpływu na wynik). **Codex: bez odpowiedzi** — błąd skryptu: `~/.codex` utworzony jako root → `failed to initialize in-process app-server client: Permission denied (os error 13)` przed jakimkolwiek żądaniem do `/chatgpt` (jedyne: `CONNECT ab.chatgpt.com`, odmowa); poprawione, przebieg 3 | `p3-2026-11-04T132151.log` |
| P-6 | klienci przez CONNECT bez DNS, `NO_PROXY` dla localhost | `p6.sh` | **CONNECT bez DNS działa** dla apt (po przepisaniu na `https://`), npm, NuGet, pip, `docker pull`, instalatora Claude Code i `npm i -g @openai/codex` (tabela niżej). **Playwright**: `npm i playwright` przez proxy OK, ale Playwright 1.64 wymaga Node ≥ 20, a Ubuntu 24.04 ma 18.19 — pobieranie przeglądarki nie ruszyło; przebieg 3 z `playwright@1.49.1`. **Node `fetch`** (18.19) nie używa proxy wcale (`ConnectTimeoutError`, 0 wpisów w proxy; `NODE_USE_ENV_PROXY=1` działa dopiero od Node 24) — bezpośrednie połączenie blokuje nftables. **NO_PROXY** — przebieg 3 (w przebiegu 2 próba przerwana błędem skryptu: `pkill -f` zabił własną powłokę) | `p6-2026-11-04T130727.log`, przebieg 1: `p6-2026-11-04T123348.log` |
| P-7 | Incus 6.0 z Ubuntu universe: `images:` i `images:ubuntu/24.04` | `p7.sh` | **działa**: `incus 6.0.0-1ubuntu0.3` z noble-security/universe, `incus image list images: ubuntu/24.04` kod 0, `incus launch images:ubuntu/24.04` kod 0 w 1–2 s (Ubuntu 24.04.5 LTS) w projekcie `user-1000`, dwa przebiegi. Zabbly niepotrzebne (decyzja `incus-z-ubuntu`) | `p7-2026-11-04T130723.log`, `p7-2026-11-04T123343.log` |

### Pomiar `claude -p 'hi'` (P-3, przebieg 2, trzy powtórzenia)

| Gdzie | Czasy [s] | Mediana [s] |
| --- | --- | --- |
| pudełko, przez proxy wstrzykujące | 4,26 · 5,73 · 3,52 | **4,26** |
| host | 6,93 · 4,42 · 5,65 | **5,65** |

Narzutu proxy nie widać — rozrzut między powtórzeniami (±1,5 s) jest większy niż różnica. Host uruchamia pełną
konfigurację użytkownika (CLAUDE.md, wtyczki), pudełko gołe `claude` — stąd host bywa wolniejszy.

## Domeny z dziennika odmów

Źródło dla szablonów stosów (#16/PT-192, #29, #30/PT-193). Runda 1 każdego klienta biegnie z pustą listą; kolejne
dokładają domeny z odmów, aż klient przejdzie (przebieg 2, `docs/box-prototype/p6-2026-11-04T130727/domains-*.txt`).

| Klient | Polecenie | Pusta lista | Domeny potrzebne | Rundy |
| --- | --- | --- | --- | --- |
| apt | `apt-get update` (źródła `https://`) | kod 1 | `archive.ubuntu.com`, `security.ubuntu.com`; dodatkowo `motd.ubuntu.com` (motd-news, niepotrzebne do aktualizacji) | 3 |
| apt (instalacja) | `apt-get install … dotnet-sdk-8.0 docker.io bubblewrap …` | kod 100 | `archive.ubuntu.com`, `security.ubuntu.com` | 2 |
| npm | `npm install --package-lock-only && npm ci` | kod 1 (E403) | `registry.npmjs.org` | 2 |
| NuGet / dotnet | `dotnet restore` | kod 1 | `api.nuget.org`, `crl3.digicert.com`, `ocsp.digicert.com` (sprawdzanie unieważnienia certyfikatów podpisu paczek) | 3 |
| pip | `pip download requests==2.32.3` | kod 1 | `pypi.org`, `files.pythonhosted.org` | 3 |
| docker pull | `docker pull hello-world` (dockerd z drop-inem proxy) | kod 1 | `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com` | 4 |
| Playwright | `npm i playwright && npx playwright install chromium` | kod 1 | `registry.npmjs.org`; domeny pobierania przeglądarki — przebieg 3 | — |
| instalator Claude Code | `curl -fsSL https://claude.ai/install.sh \| bash -s 2.1.317` | kod 22 | `claude.ai`, `downloads.claude.ai`; `api.anthropic.com` po instalacji (`claude --version`), niepotrzebne do instalacji | 4 |
| Codex | `npm i -g @openai/codex` (0.166.0) | kod 1 | `registry.npmjs.org` | 2 |
| Claude Code (praca, P-3) | `claude -p` przez base URL | — | żadnej przez CONNECT; odmowy `api.anthropic.com` bez wpływu | — |
| Codex (praca, P-3) | `codex exec` | — | odmowa `ab.chatgpt.com` (telemetria); reszta — przebieg 3 | — |

## NO_PROXY per klient

`NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal,<adres mostka>`; serwer HTTP w pudełku na pętli
zwrotnej. „Tak” = serwer dostał żądanie, a dziennik proxy nie ma wpisu.

| Klient | Przez CONNECT | `localhost` bez proxy | `127.0.0.1` bez proxy | Obejście |
| --- | --- | --- | --- | --- |
| curl | tak (instalator Claude Code) | przebieg 3 | przebieg 3 | |
| Node `fetch` (18.19) | **nie** — ignoruje `HTTP(S)_PROXY` | przebieg 3 (bez proxy z definicji) | przebieg 3 | dla ruchu na zewnątrz: Node ≥ 24 z `NODE_USE_ENV_PROXY=1` albo `undici` `EnvHttpProxyAgent` w kodzie |
| dotnet `HttpClient` | tak (NuGet) | przebieg 3 | przebieg 3 | |
| Playwright (Chromium) | — | przebieg 3 | przebieg 3 | |
| apt, npm, pip, dockerd, Codex npm | tak | nie dotyczy (bez żądań do localhost) | | |
