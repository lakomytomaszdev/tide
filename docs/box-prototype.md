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

Przebiegi (2026-11-04): 1 — 12:33 (P-6 padło na źródłach apt); 2 — 13:07 (pełny P-6 bez Playwright i NO_PROXY,
P-3 Claude, P-2 wariant A); 3 — 13:51 (`P6_ONLY=playwright,noproxy`: Playwright, NO_PROXY, P-3 Codex, P-2 warianty A
i B); 4 — 14:27 (`TRIALS="p3 p2"`: Codex z własnym dostawcą modelu i identyfikatorem konta hosta w atrapie, P-2
z Codexem przez model).

## Tabela prób

| Próba | Pytanie | Skrypt | Wynik | Dziennik |
| --- | --- | --- | --- | --- |
| P-2 | bwrap (Claude Code) i landlock (Codex) w kontenerze przy `apparmor_restrict_unprivileged_userns=1` | `p2.sh` | **bwrap (Claude Code): działa po wczytaniu profilu AppArmor w pudełku (wariant B); z gołego obrazu (A) nie.** A: `bwrap --unshare-all` → `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`, jądro: `apparmor="DENIED" operation="capable" namespace="root//incus-user-1000_tide-proto-net_<var-lib-incus>" profile="unprivileged_userns"` (setpcap, net_admin) — ograniczenie hosta obowiązuje w przestrzeni AppArmor pudełka; `claude -p` z `failIfUnavailable` odmawia startu („Ubuntu's AppArmor restriction on user namespaces stops bubblewrap here”). B: profil `profile bwrap /usr/bin/bwrap flags=(unconfined) { userns, }` (ten sam co na hoście, `scripts/sandbox/check.py profile`) wczytany przez `apparmor_parser -r` **wewnątrz** pudełka (root pudełka, host bez zmian) → goły bwrap `bwrap-ok`; `claude -p` z `sandbox.enabled=true` kod 0: odczyt `~/.claude/p2-marker` → `No such file or directory` (denyRead maskuje katalog), zapis w katalogu roboczym tak, poza nim `Read-only file system`. `unshare --user` bez profilu nadal odmowa — ograniczenie zostaje dla reszty procesów. Decyzja `bwrap-profil-w-pudelku`. **Codex: działa po wczytaniu profilu (B); z gołego obrazu (A) nie.** Codex 0.166 na Linuksie używa bubblewrap, nie samego landlocka („Codex's Linux sandbox uses bubblewrap and needs access to create user namespaces”), więc dzieli los Claude Code. A: `codex exec --sandbox workspace-write` — polecenia nie ruszyły, „the sandbox failed to start with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`”, żaden plik nie powstał. B (przebieg 4): `codex exec --sandbox workspace-write` (model przez proxy, wariant `provider`) kod 0 — `echo ok > ./codex-model-inside` kod 0 i plik jest, `echo bad > ~/p2-outside-codex-model` kod 1 „read-only file system” i pliku nie ma. Próba bez modelu (`codex sandbox …`) nie dała wyniku przez składnię CLI 0.166 (`linux` jako polecenie, potem brak `--full-auto`) — odpowiedź opiera się na `codex exec` | `p2-2026-11-04T142904.log`, `p2-2026-11-04T135550.log`, `p2-2026-11-04T132223.log` |
| P-3 | Claude Code (OAuth) przez `ANTHROPIC_BASE_URL` i Codex przez `chatgpt_base_url` na proxy wstrzykujące (K3) | `p3.sh` | **Claude Code: K3 możliwe** — `ANTHROPIC_BASE_URL=http://<mostek>:18080/anthropic CLAUDE_CODE_OAUTH_TOKEN=<atrapa> claude -p 'hi'` 9/9 kod 0 w trzech przebiegach, proxy `INJECT POST /anthropic/v1/messages` → **200**, w pudełku tylko atrapa. Odświeżenie OAuth: 0 żądań przez proxy (ani base URL, ani CONNECT) — pudełko nie odświeża, token odświeża host, proxy czyta plik przy każdym żądaniu. Poza base URL Claude Code próbuje `CONNECT api.anthropic.com:443` (odmowa, bez wpływu). **Codex: K3 możliwe — tylko z własnym dostawcą modelu** (przebieg 4): `codex exec --skip-git-repo-check -c 'chatgpt_base_url="http://<mostek>:18080/chatgpt/backend-api/"' -c 'model_provider="tide"' -c 'model_providers.tide={ name = "tide", base_url = "http://<mostek>:18080/chatgpt/backend-api/codex", wire_api = "responses", requires_openai_auth = true }' 'Reply with the single word: hi'` kod 0, proxy `INJECT POST /chatgpt/backend-api/codex/responses` → **200** (plus `codex/models`, `wham/accounts/check`, `ps/plugins/*`, `ps/mcp`, `analytics-events` → 200/204). W pudełku `~/.codex/auth.json` z tokenami-atrapami (JWT bez podpisu); z pliku hosta przechodzą tylko identyfikator konta i rodzaj planu — z wymyślonym kontem Codex kończy „selected workspace missing from routing discovery”. Same `chatgpt_base_url` albo `+OPENAI_BASE_URL` **nie wystarczą** (przebiegi 3 i 4): wywołanie modelu idzie przez CONNECT prosto do `chatgpt.com:443` (odmowa), kod 1. Odświeżenie OAuth Codexa: 0 żądań (`auth.openai.com` się nie pojawia) | `p3-2026-11-04T142744.log`, `p3-2026-11-04T135419.log`, `p3-2026-11-04T132151.log` |
| P-6 | klienci przez CONNECT bez DNS, `NO_PROXY` dla localhost | `p6.sh` | **działa**: apt (źródła przepisane na `https://`, decyzja `apt-przez-https`), npm, NuGet, pip, `docker pull`, Playwright (`playwright@1.49.1`), instalator Claude Code i `npm i -g @openai/codex` przechodzą przez CONNECT bez DNS z listą z dziennika odmów. **NO_PROXY honorują wszyscy badani** (curl, Node `fetch`, dotnet `HttpClient`, Playwright/Chromium) dla `localhost` i `127.0.0.1`: serwer w pudełku dostał żądanie, proxy 0 wpisów. Wyjątki: **Node `fetch`** (18.19) nie używa proxy wcale — ruch na zewnątrz pada (`ConnectTimeoutError`, połączenie bezpośrednie blokuje nftables); **Playwright ≥ 1.50 wymaga Node ≥ 20** (Ubuntu 24.04 ma 18.19) — obraz bazowy potrzebuje nowszego Node (#16/#29) | `p6-2026-11-04T135145.log`, `p6-2026-11-04T130727.log`, przebieg 1: `p6-2026-11-04T123348.log` |
| P-7 | Incus 6.0 z Ubuntu universe: `images:` i `images:ubuntu/24.04` | `p7.sh` | **działa**: `incus 6.0.0-1ubuntu0.3` z noble-security/universe, `incus image list images: ubuntu/24.04` kod 0, `incus launch images:ubuntu/24.04` kod 0 w 1–2 s (Ubuntu 24.04.5 LTS) w projekcie `user-1000`, trzy przebiegi. Zabbly niepotrzebne (decyzja `incus-z-ubuntu`) | `p7-2026-11-04T135140.log`, `p7-2026-11-04T130723.log`, `p7-2026-11-04T123343.log` |

### Pomiar `claude -p 'hi'` (P-3, trzy powtórzenia na przebieg)

| Gdzie | Przebieg 2 [s] | Przebieg 3 [s] | Przebieg 4 [s] | Mediana z 9 [s] |
| --- | --- | --- | --- | --- |
| pudełko, przez proxy wstrzykujące | 4,26 · 5,73 · 3,52 (mediana 4,26) | 5,81 · 5,45 · 12,18 (mediana 5,81) | 3,09 · 2,22 · 2,14 (mediana 2,22) | **4,26** |
| host | 6,93 · 4,42 · 5,65 (mediana 5,65) | 4,32 · 4,08 · 5,09 (mediana 4,32) | 5,33 · 5,55 · 4,28 (mediana 5,33) | **5,09** |

Narzutu proxy nie widać: w dwóch przebiegach z trzech pudełko było szybsze, mediana z dziewięciu prób to 4,26 s
w pudełku vs 5,09 s na hoście. Pojedyncze 12,18 s to odstający wynik (opóźnienie API — przez proxy idzie jeden POST).
Host uruchamia pełną konfigurację użytkownika (CLAUDE.md, wtyczki), pudełko gołe `claude`.

## Domeny z dziennika odmów

Źródło dla szablonów stosów (#16/PT-192, #29, #30/PT-193). Runda 1 każdego klienta biegnie z pustą listą; kolejne
dokładają domeny z odmów, aż klient przejdzie (`docs/box-prototype/p6-*/domains-*.txt`).

| Klient | Polecenie | Pusta lista | Domeny potrzebne | Rundy |
| --- | --- | --- | --- | --- |
| apt | `apt-get update` (źródła `https://`) | kod 1 | `archive.ubuntu.com`, `security.ubuntu.com`; dodatkowo `motd.ubuntu.com` (motd-news, niepotrzebne do aktualizacji) | 3 |
| apt (instalacja) | `apt-get install … dotnet-sdk-8.0 docker.io bubblewrap …` | kod 100 | `archive.ubuntu.com`, `security.ubuntu.com` | 2 |
| npm | `npm install --package-lock-only && npm ci` | kod 1 (E403) | `registry.npmjs.org` | 2 |
| NuGet / dotnet | `dotnet restore` | kod 1 | `api.nuget.org`, `crl3.digicert.com`, `ocsp.digicert.com` (sprawdzanie unieważnienia certyfikatów podpisu paczek) | 3 |
| pip | `pip download requests==2.32.3` | kod 1 | `pypi.org`, `files.pythonhosted.org` | 3 |
| docker pull | `docker pull hello-world` (dockerd z drop-inem proxy) | kod 1 | `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com` | 4 |
| Playwright | `npm i playwright@1.49.1 && npx playwright install chromium` | kod 1 | `registry.npmjs.org`, `playwright.azureedge.net`, `playwright-akamai.azureedge.net`, `playwright-verizon.azureedge.net`, `playwright.download.prss.microsoft.com` (lustra pobierania przeglądarki, próbowane po kolei) | 4 |
| instalator Claude Code | `curl -fsSL https://claude.ai/install.sh \| bash -s 2.1.317` | kod 22 | `claude.ai`, `downloads.claude.ai`; `api.anthropic.com` po instalacji (`claude --version`), niepotrzebne do instalacji | 4 |
| Codex | `npm i -g @openai/codex` (0.166.0) | kod 1 | `registry.npmjs.org` | 2 |
| Claude Code (praca, P-3) | `claude -p` przez base URL | — | żadnej przez CONNECT; odmowy `api.anthropic.com` bez wpływu na wynik | — |
| Codex (praca, P-3) | `codex exec` (wariant `provider`) | — | żadnej przez CONNECT — model i usługi konta przez base URL; odmowy `ab.chatgpt.com` (telemetria), `sdmntprcentralus.oaiusercontent.com`, `sdmntprsouthcentralus.oaiusercontent.com` (pliki/obrazy wtyczek) bez wpływu na wynik. Bez wariantu `provider` model idzie do `chatgpt.com` — z K3 ta domena ma **nie** być na liście (poszłaby z atrapą) | — |

## NO_PROXY per klient

`NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal,<adres mostka>`; serwer HTTP w pudełku
(`python3 -m http.server 8765 --bind ::`) na pętli zwrotnej. „Tak” = serwer dostał żądanie, a dziennik proxy nie ma
wpisu (przebieg 3, `docs/box-prototype/p6-2026-11-04T135145.log`).

| Klient | Przez CONNECT | `localhost` bez proxy | `127.0.0.1` bez proxy | Obejście |
| --- | --- | --- | --- | --- |
| curl | tak (instalator Claude Code) | tak | tak | — |
| Node `fetch` (18.19) | **nie** — ignoruje `HTTP(S)_PROXY` | tak (nie używa proxy wcale) | tak | ruch na zewnątrz: Node ≥ 24 z `NODE_USE_ENV_PROXY=1` albo `undici` `EnvHttpProxyAgent` w kodzie; Node 18 z `NODE_USE_ENV_PROXY=1` — bez zmian (`ConnectTimeoutError`) |
| dotnet `HttpClient` | tak (NuGet) | tak | tak | — |
| Playwright (Chromium 1.49.1) | tak (pobranie przeglądarki) | tak | tak | — |
| apt, npm, pip, dockerd, instalator Codexa | tak | nie dotyczy (bez żądań do localhost) | | — |
