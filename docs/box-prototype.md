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
| P-2 | bwrap (Claude Code) i landlock (Codex) w kontenerze przy `apparmor_restrict_unprivileged_userns=1` | `p2.sh` | czeka na Incusa (`make box-install`), potem próba | — |
| P-3 | Claude Code (OAuth) przez `ANTHROPIC_BASE_URL` i Codex przez `chatgpt_base_url` na proxy wstrzykujące (K3) | `p3.sh` | czeka na Incusa (`make box-install`), potem próba | — |
| P-6 | klienci przez CONNECT bez DNS, `NO_PROXY` dla localhost | `p6.sh` | czeka na Incusa (`make box-install`), potem próba | — |
| P-7 | Incus 6.0 z Ubuntu universe: `images:` i `images:ubuntu/24.04` | `p7.sh` | czeka na Incusa (`make box-install`), potem próba | — |

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
