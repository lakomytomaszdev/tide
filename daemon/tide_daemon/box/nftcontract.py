"""Kontrakt portu proxy pudełka z zainstalowanymi regułami nftables (K-38, PT-292).

`scripts/box/install.sh --proxy-port P` (`make box-install BOX_PROXY_PORT=P`) zapisuje w `/etc/tide-box/tide-box.nft`
statyczną tabelę `inet tide-box` z generatora `scripts/box/files.sh rules <adres mostka> <P>`, sprawdza ją
`nft -c`, wczytuje i wczytuje ponownie przy starcie systemu (`tide-box-nft.service`). Plik jest więc potwierdzonym
kontraktem instalacji: z mostka wolno tylko do `<adres>:<P>/tcp` (reguła z komentarzem „proxy Tide” w łańcuchu
`input`). Proxy słuchające na innym porcie albo adresie byłoby odcięte przez DROP.

Ten moduł tylko czyta plik (bez sudo, bez `nft`): usługa nigdy nie zmienia nftables. Używają go zapis
`SetCardFlowSettings` (zmiana `box.proxy-port` albo włączenie pudełka) i start proxy w usłudze (adres mostka).
`TIDE_BOX_NFT_FILE` podstawia plik w testach i próbach na atrapach.
"""
import os
import re

RULES_FILE = "/etc/tide-box/tide-box.nft"
ENV = "TIDE_BOX_NFT_FILE"

_TABLE = re.compile(r"^table inet tide-box \{\n(?P<body>.*?)^\}", re.M | re.S)
_INPUT = re.compile(r"^\s*chain input \{\n(?P<body>.*?)^\s*\}\s*$", re.M | re.S)
_PROXY = re.compile(r'^\s*iifname "(?P<bridge>[^"]+)" ip daddr (?P<address>\d{1,3}(?:\.\d{1,3}){3}) '
                    r'tcp dport (?P<port>\d{1,5}) accept comment "proxy Tide"\s*$', re.M)


def rules_file(env=None) -> str:
    env = os.environ if env is None else env
    return env.get(ENV) or RULES_FILE


def proxy_rules(text: str) -> list:
    """Reguły „proxy Tide” z łańcucha `input` tabeli `inet tide-box`: lista `(mostek, adres, port)`."""
    found = []
    for table in _TABLE.finditer(text or ""):
        for chain in _INPUT.finditer(table.group("body")):
            found += [(m.group("bridge"), m.group("address"), int(m.group("port")))
                      for m in _PROXY.finditer(chain.group("body"))]
    return found


def procedure(port: int) -> str:
    return (f"Uzgodnienie: wstrzymaj karty (zero kroków, outside i konsol), uruchom zatwierdzoną instalację "
            f"make box-install BOX_PROXY_PORT={port} (sudo bash scripts/box/install.sh --proxy-port {port}), "
            "ponów zapis ustawienia i zrestartuj usługę Tide (docs/box-cli-v1.md). Usługa nie zmienia nftables.")


def problem(text: str, *, port: int, bridge: str, address: str | None = None, where: str = RULES_FILE) -> str | None:
    """`None`, gdy reguły dopuszczają dokładnie `bridge` → `address:port` (adres sprawdzany, gdy podany);
    inaczej czytelny błąd z procedurą uzgodnienia."""
    rules = proxy_rules(text)
    if not rules:
        return (f"brak reguły proxy Tide w tabeli inet tide-box ({where}) — kontrakt instalacji niepotwierdzony. "
                + procedure(port))
    if len(rules) != 1:
        return (f"{where}: {len(rules)} reguły proxy Tide zamiast jednej — kontrakt instalacji niejednoznaczny. "
                + procedure(port))
    have_bridge, have_address, have_port = rules[0]
    if (have_bridge, have_port) != (bridge, port) or (address is not None and have_address != address):
        want = f"{bridge} → {address or '<adres mostka>'}:{port}"
        return (f"box.proxy-port/box.bridge niezgodne z instalacją: reguły inet tide-box ({where}) wpuszczają "
                f"{have_bridge} → {have_address}:{have_port}, a ustawienie wymaga {want}. " + procedure(port))
    return None


def check_file(path: str, *, port: int, bridge: str, address: str | None = None) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return (f"kontrakt instalacji niepotwierdzony: brak {path} (tabela inet tide-box z make box-install). "
                + procedure(port))
    except (OSError, UnicodeError) as exc:
        return (f"kontrakt instalacji niepotwierdzony: nie da się odczytać {path} ({type(exc).__name__}). "
                + procedure(port))
    return problem(text, port=port, bridge=bridge, address=address, where=path)
