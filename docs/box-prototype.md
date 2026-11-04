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

## Tabela prób

| Próba | Pytanie | Skrypt | Wynik | Dziennik |
| --- | --- | --- | --- | --- |
| P-2 | bwrap (Claude Code) i landlock (Codex) w kontenerze przy `apparmor_restrict_unprivileged_userns=1` | `p2.sh` | **niepełna** (przebieg 1): bwrap nie zainstalowany (P-6 padło przed apt), Claude/Codex brak. Sygnał wstępny: `unshare --user --map-root-user` jako `ubuntu` w pudełku → `Permission denied` (host: `apparmor_restrict_unprivileged_userns=1`, `security.nesting=true`; z wnętrza etykieta `unconfined`). Do powtórki po poprawce P-6 | `p2-2026-11-04T123432.log` |
| P-3 | Claude Code (OAuth) przez `ANTHROPIC_BASE_URL` i Codex przez `chatgpt_base_url` na proxy wstrzykujące (K3) | `p3.sh` | **nieuruchomiona** (przebieg 1): brak `claude` w pudełku, bo P-6 padło przed instalacją | `p3-2026-11-04T123431.log` |
| P-6 | klienci przez CONNECT bez DNS, `NO_PROXY` dla localhost | `p6.sh` | **błąd skryptu** (przebieg 1): obraz `images:ubuntu/24.04` ma klasyczny `/etc/apt/sources.list`, nie `ubuntu.sources` → `cp: cannot stat`. Potwierdzone: brak DNS w pudełku (`getent hosts registry.npmjs.org` pusto). Poprawka: p6.sh wykrywa oba formaty | `p6-2026-11-04T123348.log` |
| P-7 | Incus 6.0 z Ubuntu universe: `images:` i `images:ubuntu/24.04` | `p7.sh` | **działa**: `incus 6.0.0-1ubuntu0.3` z noble-security/universe, `incus image list images: ubuntu/24.04` kod 0, `incus launch images:ubuntu/24.04` kod 0 w 2 s (Ubuntu 24.04.5 LTS) w projekcie `user-1000`. Zabbly niepotrzebne (decyzja `incus-z-ubuntu`) | `p7-2026-11-04T123343.log` |

## Domeny z dziennika odmów

Źródło dla szablonów stosów (#16/PT-192, #29, #30/PT-193). Runda 1 każdego klienta biegnie z pustą listą; kolejne
dokładają domeny z odmów, aż klient przejdzie.

| Klient | Domeny |
| --- | --- |
| — | do uruchomienia `p6.sh` |

## NO_PROXY per klient

`NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal,<adres mostka>`; serwer HTTP w pudełku na pętli
zwrotnej. „Tak” = serwer dostał żądanie, a dziennik proxy nie ma wpisu.

| Klient | Przez CONNECT | `localhost` bez proxy | `127.0.0.1` bez proxy | Obejście |
| --- | --- | --- | --- | --- |
| — | do uruchomienia `p6.sh` | | | |
