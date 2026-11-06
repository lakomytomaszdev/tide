"""Profil środowiska pudełka: schemat `tide-box: 1`, klasyfikacja ryzyka, scalanie, hash przepisu, zapis.

Schemat to granica zgody użytkownika:
- profil projektu (Tablica, `projects/<slug>/box.yaml`) nie przyjmuje `network.full`, adresów
  wewnętrznych, `packs` ani `accepted_red` — to wolno wyłącznie w profilu karty (D13),
- `network.full: true` wymaga jawnej flagi `network.full-consent: true` (tylko profil karty),
- klasa czerwona zależy wyłącznie od reguł w tym pliku; dane z szablonów (`known`) mogą pozycję
  najwyżej zaostrzyć, nigdy „odczerwienić”,
- `.tide/box.yaml` z repozytorium projektu jest tylko propozycją: czytamy go, nigdy nie zapisujemy.

Wykrywanie stosu i szablony stosów (#30) oraz budowa pudełka (#4) są poza tym modułem.
Opis: docs/tide-daemon.md (Pudełko karty: profil środowiska).
"""
import copy
import hashlib
import ipaddress
import json
import os
import re
import tempfile
from dataclasses import dataclass, field

from . import yamlmini

SCHEMA_VERSION = 1

GREEN, YELLOW, RED = "green", "yellow", "red"

BOX_KINDS = ("per-project", "per-card")
TYPES = ("container", "vm")

DEFAULT_RESOURCES = {"cpu": 8, "memory": "24GiB", "disk": "60GB", "swap": False}

# Klucze, których zmiana zmienia obraz pudełka, więc wchodzą do hasha przepisu. Reszta (resources,
# ports, network.*, verify, packs, box, notes, project) jest czytana przy starcie albo po budowie.
HASH_KEYS = ("type", "base", "stacks", "node", "dotnet", "apt", "setup", "docker.enabled", "docker.images")
# Klucze celowo poza hashem — test sprawdza, że lista razem z HASH_KEYS pokrywa cały schemat.
NON_HASH_KEYS = ("tide-box", "project", "network.domains", "network.host-ollama", "network.full",
                 "network.full-consent", "ports.pool", "ports.env", "resources.cpu", "resources.memory",
                 "resources.disk", "resources.swap", "verify", "packs", "box", "notes", "accepted_red")

KEY_ORDER = ("tide-box", "project", "type", "base", "stacks", "node", "dotnet", "apt", "setup", "network",
             "ports", "docker", "resources", "verify", "packs", "box", "notes", "accepted_red")

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_STACK = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_APT = re.compile(r"^[a-z0-9][a-z0-9+.\-:=~]{0,100}$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SIZE = re.compile(r"^\d+(\.\d+)?(KiB|MiB|GiB|TiB|KB|MB|GB|TB)$")
_HOST = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
_MAX_FILE = 64 * 1024


class ProfileError(ValueError):
    """Profil narusza schemat albo granicę zgody; `errors` to wszystkie znalezione problemy."""

    def __init__(self, errors):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


# ------------------------------------------------------------ klasyfikacja ryzyka

def normalize_host(item: str) -> str:
    return str(item).strip().lower().rstrip(".")


_INTERNAL_V4 = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16")]


_INTERNAL_SUFFIXES = ("local", "localhost", "localdomain", "lan", "internal", "home.arpa", "intranet",
                      "corp", "firma.example")
_V4_COMPAT = ipaddress.ip_network("::/96")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _bare(host: str) -> str:
    return host[2:] if host.startswith("*.") else host


def _is_internal(host: str) -> bool:
    """Adres wewnętrzny (czerwony): 192.168.*, 10.*, 172.16–31.*, loopback, link-local, 100.64/10,
    prywatne IPv6 (także IPv4 zmapowany), `localhost`, nazwy jednoetykietowe, sufiksy sieci lokalnych (`.local`, `.lan`, `.internal`,
    `.home.arpa`, `.localdomain`, `.intranet`, `.corp`), `*.firma.example`, IPv4 w IPv6 (mapowany,
    zgodny wstecznie, NAT64) oraz nazwy
    z liczbową/szesnastkową ostatnią etykietą (`10.1`, `3232235786`, `0xc0a8010a`) — resolver
    zamienia je na adres IPv4, więc nie traktujemy ich jako nazw."""
    wildcard = host.startswith("*.")
    host = _bare(host)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        last = host.rsplit(".", 1)[-1]
        if last.isdigit() or last.startswith("0x"):
            return True
        if "." not in host and not wildcard:
            return True   # nazwa jednoetykietowa: resolver dopełnia ją domeną wyszukiwania hosta
        return any(host == suf or host.endswith("." + suf) for suf in _INTERNAL_SUFFIXES)
    if ip.version == 6:
        embedded = ip.ipv4_mapped
        if embedded is None and (ip in _V4_COMPAT or ip in _NAT64) and ip.packed[12:] != b"\0\0\0\0":
            embedded = ipaddress.IPv4Address(ip.packed[12:])
        if embedded is not None:
            ip = embedded
        else:
            return (ip.is_private or ip.is_link_local or ip.is_loopback or ip.is_site_local
                    or ip.is_unspecified)
    return any(ip in net for net in _INTERNAL_V4)


def _is_broad_wildcard(host: str) -> bool:
    """`*.com` itp.: symbol wieloznaczny z jedną etykietą po `*.` to prawie `network.full`."""
    return host.startswith("*.") and "." not in host[2:]


def _is_remote_repo(host: str) -> bool:
    labels = host.lstrip("*.").split(".") if host.startswith("*.") else host.split(".")
    host = ".".join(labels)
    return host == "github.com" or host.endswith(".github.com") or "gitlab" in labels


def classify(item: str, known: dict | None = None) -> str:
    """Klasa ryzyka pozycji profilu: `green`, `yellow` albo `red`.

    Pozycje: `network.full`, `host-ollama`, `box:per-card`, `pack:<nazwa>` (albo `packs`) i domena.
    `known` — słownik domena → klasa z szablonów stosów; czerwone reguły sprawdzamy przed nim,
    więc szablon nie „odczerwieni” `github.com`, a wpis `red` w szablonie zaostrza.
    """
    item = str(item).strip()
    if item == "network.full" or item == "packs" or item.startswith("pack:"):
        return RED
    if item == "host-ollama":
        return YELLOW
    if item in ("box:per-card", "box:per-project"):
        return GREEN
    host = normalize_host(item)
    if _is_internal(host) or _is_remote_repo(host) or _is_broad_wildcard(host):
        return RED
    klass = {normalize_host(k): v for k, v in (known or {}).items()}.get(host)
    if klass in (GREEN, YELLOW, RED):
        return klass
    return YELLOW


def internal_hosts(domains) -> list:
    return [d for d in domains if _is_internal(normalize_host(d))]


def items(profile: dict, known: dict | None = None) -> list:
    """Pozycje profilu do pakietu zgód: [(id, klasa)], w stałej kolejności."""
    out = []
    net = profile.get("network") or {}
    if net.get("full"):
        out.append(("network.full", RED))
    if net.get("host-ollama"):
        out.append(("host-ollama", classify("host-ollama")))
    for domain in net.get("domains") or []:
        out.append((domain, classify(domain, known)))
    for pack in profile.get("packs") or []:
        out.append((f"pack:{pack['name'] if isinstance(pack, dict) else pack}", RED))
    if profile.get("box") == "per-card":
        out.append(("box:per-card", GREEN))
    return out


def display_items(profile: dict, known: dict | None = None) -> list:
    """Pozycje profilu do sekcji „Środowisko karty” (PT-172): [(id, grupa, klasa)], w stałej kolejności.

    Grupy: `network`, `docker`, `ports`, `packs`, `resources`, `per-card`. Pozycje ryzykowne (`items`) mają tu te same
    klasy; Docker i obrazy są żółte (K1: grupa docker = root pudełka), porty i zasoby zielone. UI niczego nie klasyfikuje.
    """
    out = [(name, "packs" if name.startswith("pack:") else "network", klass) for name, klass in items(profile, known)
           if name != "box:per-card"]
    docker = profile.get("docker") or {}
    if docker.get("enabled") or "docker" in profile.get("stacks", []):
        out.append(("docker", "docker", YELLOW))
    for image in docker.get("images") or []:
        out.append((f"image:{image}", "docker", YELLOW))
    if profile.get("ports"):
        out.append(("ports", "ports", GREEN))
    if profile.get("resources"):
        out.append(("resources", "resources", GREEN))
    if profile.get("box") == "per-card":
        out.append(("box:per-card", "per-card", GREEN))
    return out


def red_items(profile: dict, known: dict | None = None) -> list:
    return [name for name, klass in items(profile, known) if klass == RED]


# ------------------------------------------------------------ walidacja

def _str_list(value, path, errors, pattern=None, allow_empty_items=False):
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{path}: oczekiwano listy")
        return []
    out = []
    for entry in value:
        if not isinstance(entry, str) or (not entry.strip() and not allow_empty_items):
            errors.append(f"{path}: element musi być niepustym napisem, jest {entry!r}")
        elif pattern is not None and not pattern.match(entry):
            errors.append(f"{path}: niedozwolona wartość {entry!r}")
        elif entry not in out:
            out.append(entry)
    return out


def _check_map(value, allowed, path, errors):
    if not isinstance(value, dict):
        errors.append(f"{path}: oczekiwano mapy")
        return {}
    for key in value:
        if key not in allowed:
            errors.append(f"{path}.{key}: nieznany klucz" if path else f"{key}: nieznany klucz")
    return value


def _bool(value, path, errors):
    if not isinstance(value, bool):
        errors.append(f"{path}: oczekiwano true/false")
        return False
    return value


def validate(raw, scope: str = "project") -> dict:
    """Sprawdza profil i zwraca kanoniczny słownik (tylko klucze podane, bez wartości domyślnych).

    `scope`: `project` (Tablica, propozycja z repozytorium) albo `card` (rozszerzenie karty).
    Rzuca `ProfileError` ze wszystkimi problemami.
    """
    if scope not in ("project", "card"):
        raise ValueError("scope musi być project albo card")
    errors = []
    card = scope == "card"
    allowed = {"tide-box", "project", "type", "base", "stacks", "node", "dotnet", "apt", "setup", "network",
               "ports", "docker", "resources", "verify", "box", "notes"}
    if card:
        allowed |= {"packs", "accepted_red"}
    if not isinstance(raw, dict):
        raise ProfileError("profil musi być mapą")
    for key in raw:
        if key not in allowed:
            hint = " (tylko w profilu karty)" if key in ("packs", "accepted_red") else ""
            errors.append(f"{key}: nieznany klucz{hint}")
    out = {}

    if raw.get("tide-box") != SCHEMA_VERSION or isinstance(raw.get("tide-box"), bool):
        errors.append(f"tide-box: wymagana wersja schematu {SCHEMA_VERSION}")
    out["tide-box"] = SCHEMA_VERSION

    if "project" in raw:
        if not isinstance(raw["project"], str) or not _SLUG.match(raw["project"]):
            errors.append("project: oczekiwano nazwy projektu (małe litery, cyfry, . _ -)")
        else:
            out["project"] = raw["project"]
    if "type" in raw:
        if raw["type"] not in TYPES:
            errors.append(f"type: dozwolone {', '.join(TYPES)}")
        else:
            out["type"] = raw["type"]  # vm przechodzi walidację; budowa odmawia w #4
    if "base" in raw:
        if not isinstance(raw["base"], str) or not raw["base"].strip() or re.search(r"\s", raw["base"]):
            errors.append("base: oczekiwano nazwy obrazu bez spacji")
        else:
            out["base"] = raw["base"].strip()
    if "stacks" in raw:
        out["stacks"] = _str_list(raw["stacks"], "stacks", errors, _STACK)
    if "node" in raw:
        node = _check_map(raw["node"], {"version"}, "node", errors)
        if "version" in node:
            ver = node["version"]
            if isinstance(ver, bool) or not isinstance(ver, (str, int)) or not re.match(r"^\d+(\.\d+){0,2}$", str(ver)):
                errors.append("node.version: oczekiwano numeru wersji")
            else:
                out["node"] = {"version": str(ver)}
    if "dotnet" in raw:
        dn = _check_map(raw["dotnet"], {"sdk"}, "dotnet", errors)
        if "sdk" in dn:
            sdk = dn["sdk"]
            sdks = sdk if isinstance(sdk, list) else [sdk]
            if not sdks or any(isinstance(s, bool) or not isinstance(s, (str, int)) or not re.match(r"^\d+(\.\d+)*$", str(s))
                               for s in sdks):
                errors.append("dotnet.sdk: oczekiwano numeru wersji albo listy numerów")
            else:
                out["dotnet"] = {"sdk": [str(s) for s in sdks]}
    for key in ("apt",):
        if key in raw:
            out[key] = _str_list(raw[key], key, errors, _APT)
    for key in ("setup", "verify"):
        if key in raw:
            out[key] = _str_list(raw[key], key, errors)

    if "network" in raw:
        net = _check_map(raw["network"], {"domains", "host-ollama", "full", "full-consent"}, "network", errors)
        clean = {}
        if "domains" in net:
            domains = []
            for dom in _str_list(net["domains"], "network.domains", errors):
                host = normalize_host(dom)
                if not _HOST.match(host) and not _is_ip(host):
                    errors.append(f"network.domains: {dom!r} nie jest nazwą hosta ani adresem IP")
                elif host not in domains:
                    domains.append(host)
            if not card:
                for host in domains:
                    if _is_broad_wildcard(host):
                        errors.append(f"network.domains: symbol wieloznaczny {host!r} jest zbyt szeroki "
                                      "(wymagane co najmniej dwie etykiety po `*.`)")
                for host in internal_hosts(domains):
                    errors.append(f"network.domains: adres wewnętrzny {host!r} dozwolony tylko w profilu karty")
            clean["domains"] = domains
        if "host-ollama" in net:
            clean["host-ollama"] = _bool(net["host-ollama"], "network.host-ollama", errors)
        full = _bool(net["full"], "network.full", errors) if "full" in net else False
        consent = _bool(net["full-consent"], "network.full-consent", errors) if "full-consent" in net else False
        if full:
            if not card:
                errors.append("network.full: niedozwolone w profilu projektu (tylko profil karty)")
            elif not consent:
                errors.append("network.full: wymaga jawnej zgody (network.full-consent: true)")
        if "full" in net:
            clean["full"] = full
        if "full-consent" in net:
            clean["full-consent"] = consent
        out["network"] = clean

    if "ports" in raw:
        ports = _check_map(raw["ports"], {"pool", "env"}, "ports", errors)
        clean = {}
        if "pool" in ports:
            pool = ports["pool"]
            if (isinstance(pool, list) and len(pool) == 2
                    and all(isinstance(p, int) and not isinstance(p, bool) and 1024 <= p <= 65535 for p in pool)
                    and pool[0] <= pool[1]):
                clean["pool"] = list(pool)
            else:
                errors.append("ports.pool: oczekiwano [od, do] w zakresie 1024–65535")
        if "env" in ports:
            clean["env"] = _str_list(ports["env"], "ports.env", errors, _ENV)
        out["ports"] = clean

    if "docker" in raw:
        dk = _check_map(raw["docker"], {"enabled", "images"}, "docker", errors)
        clean = {}
        if "enabled" in dk:
            clean["enabled"] = _bool(dk["enabled"], "docker.enabled", errors)
        if "images" in dk:
            clean["images"] = _str_list(dk["images"], "docker.images", errors, re.compile(r"^[^\s]+$"))
        out["docker"] = clean

    if "resources" in raw:
        res = _check_map(raw["resources"], {"cpu", "memory", "disk", "swap"}, "resources", errors)
        clean = {}
        if "swap" in res:
            clean["swap"] = _bool(res["swap"], "resources.swap", errors)
        if "cpu" in res:
            if isinstance(res["cpu"], bool) or not isinstance(res["cpu"], int) or not 1 <= res["cpu"] <= 256:
                errors.append("resources.cpu: oczekiwano liczby całkowitej 1–256")
            else:
                clean["cpu"] = res["cpu"]
        for key in ("memory", "disk"):
            if key in res:
                if not isinstance(res[key], str) or not _SIZE.match(res[key]):
                    errors.append(f"resources.{key}: oczekiwano rozmiaru, np. 24GiB albo 60GB")
                else:
                    clean[key] = res[key]
        out["resources"] = clean

    if "box" in raw:
        if raw["box"] not in BOX_KINDS:
            errors.append(f"box: dozwolone {', '.join(BOX_KINDS)}")
        else:
            out["box"] = raw["box"]
    if "notes" in raw:
        if not isinstance(raw["notes"], str):
            errors.append("notes: oczekiwano napisu")
        else:
            out["notes"] = raw["notes"]
    if card and "packs" in raw:
        from .packs import validate_specs
        out["packs"] = validate_specs(raw["packs"], errors)
    if card and "accepted_red" in raw:
        out["accepted_red"] = _str_list(raw["accepted_red"], "accepted_red", errors)

    if errors:
        raise ProfileError(errors)
    return out


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def with_defaults(profile: dict) -> dict:
    """Kopia profilu z wartościami domyślnymi (zasoby 8 / 24GiB / 60GB, typ container, bez sieci)."""
    out = copy.deepcopy(profile)
    out.setdefault("tide-box", SCHEMA_VERSION)
    out.setdefault("type", "container")
    out.setdefault("stacks", [])
    out.setdefault("apt", [])
    out.setdefault("setup", [])
    out.setdefault("verify", [])
    out.setdefault("box", "per-project")
    net = out.setdefault("network", {})
    net.setdefault("domains", [])
    net.setdefault("host-ollama", False)
    net.setdefault("full", False)
    dk = out.setdefault("docker", {})
    dk["enabled"] = bool(dk.get("enabled") or "docker" in out["stacks"])
    if dk["enabled"] and "docker" not in out["stacks"]:
        out["stacks"].append("docker")
    dk.setdefault("images", [])
    res = out.setdefault("resources", {})
    for key, val in DEFAULT_RESOURCES.items():
        res.setdefault(key, val)
    return out


def parse(text: str, scope: str = "project") -> dict:
    try:
        raw = yamlmini.loads(text)
    except yamlmini.YamlError as exc:
        raise ProfileError(f"YAML: {exc}") from None
    return validate(raw, scope)


def dump(profile: dict) -> str:
    return yamlmini.dumps(profile, KEY_ORDER)


# ------------------------------------------------------------ scalanie

@dataclass
class MergeResult:
    profile: dict
    dropped_red: list = field(default_factory=list)  # czerwone pozycje karty bez accepted_red
    project_red: list = field(default_factory=list)  # czerwone pozycje profilu projektu (dla pakietu zgód)


def _union(*lists):
    out = []
    for lst in lists:
        for entry in lst or []:
            if entry not in out:
                out.append(entry)
    return out


def merge(project: dict, card: dict, known: dict | None = None) -> MergeResult:
    """Profil projektu + rozszerzenie karty.

    Listy są sumowane bez duplikatów (najpierw projekt), skalary z karty wygrywają (w tym `box`).
    Czerwone pozycje karty (`network.full`, adresy wewnętrzne, zdalne repozytoria, `packs`) trafiają do
    wyniku tylko, gdy `accepted_red` karty je wymienia; pozostałe są pomijane i raportowane w
    `dropped_red`. `accepted_red` z pozycją, której nie ma w profilu karty, to `ProfileError`.
    Czerwone pozycje profilu projektu przechodzą, ale są zwracane w `project_red`, żeby pakiet zgód
    pokazał je użytkownikowi (np. `github.com` z `.tide/box.yaml` nie wejdzie niezauważenie).
    """
    project = validate(project, "project")
    card = validate(card, "card")
    accepted = [a if a in ("network.full", "host-ollama") or a.startswith("pack:")
                else normalize_host(a) for a in card.get("accepted_red", [])]
    card_red = red_items(card, known)
    unknown = [a for a in accepted if a not in card_red]
    if unknown:
        raise ProfileError([f"accepted_red: pozycji {a!r} nie ma w profilu karty albo nie jest czerwona" for a in unknown])
    dropped = [r for r in card_red if r not in accepted]

    pnet, cnet = project.get("network", {}), card.get("network", {})
    domains = list(pnet.get("domains", []))
    for dom in cnet.get("domains", []):
        if dom in card_red and dom not in accepted and dom not in domains:
            continue
        domains = _union(domains, [dom])
    net = {k: v for k, v in pnet.items() if k not in ("domains", "full", "full-consent")}
    for key in ("host-ollama",):
        if key in cnet:
            net[key] = cnet[key]
    if domains or "domains" in pnet or "domains" in cnet:
        net["domains"] = domains
    if cnet.get("full") and "network.full" in accepted:
        net["full"] = True
        net["full-consent"] = True

    out = {}
    for key in set(project) | set(card):
        if key in ("network", "accepted_red", "packs", "project"):
            continue
        pv, cv = project.get(key), card.get(key)
        if isinstance(pv, list) or isinstance(cv, list):
            out[key] = _union(pv, cv)
        elif isinstance(pv, dict) or isinstance(cv, dict):
            merged = copy.deepcopy(pv or {})
            for sub, val in (cv or {}).items():
                merged[sub] = _union(merged.get(sub), val) if isinstance(val, list) and sub != "pool" else val
            out[key] = merged
        else:
            out[key] = cv if cv is not None else pv
    if "project" in project:
        out["project"] = project["project"]
    elif "project" in card:
        out["project"] = card["project"]
    if net:
        out["network"] = net
    packs = [p for p in card.get("packs", [])
             if f"pack:{p['name'] if isinstance(p, dict) else p}" in accepted]
    if packs:
        out["packs"] = packs
    if accepted:
        out["accepted_red"] = list(accepted)
    return MergeResult(out, dropped, red_items(project, known))


# ------------------------------------------------------------ hash przepisu

def _recipe_parts(profile: dict) -> dict:
    full = with_defaults(profile)
    dotnet = full.get("dotnet", {})
    return {
        "type": full["type"],
        "base": full.get("base", ""),
        "stacks": list(full["stacks"]),                      # kolejność ma znaczenie (kolejność instalacji)
        "node": full.get("node", {}).get("version", ""),
        "dotnet": sorted(set(dotnet.get("sdk", []))),         # zbiór wersji SDK
        "apt": sorted(set(full["apt"])),                     # zbiór pakietów
        "setup": list(full["setup"]),                        # kolejność poleceń ma znaczenie
        "docker.enabled": full["docker"]["enabled"],
        "docker.images": sorted(set(full["docker"]["images"])),
    }


def recipe_stamp(profile: dict, templates_version: str, agents_version: str) -> dict:
    """Odcisk przepisu: `hash` (SHA-256) oraz skróty poszczególnych kluczy do `diff`.

    To jedno źródło hasha dla budowy (#4), pakietu zgód (#9) i recenzji (#18). Profil jest
    normalizowany (domyślne wartości, kolejność kluczy, białe znaki), więc zapis YAML nie wpływa na wynik.
    """
    parts = _recipe_parts(profile)
    parts["templates"] = str(templates_version)
    parts["agents"] = str(agents_version)
    digests = {k: _digest(v) for k, v in parts.items()}
    return {"hash": _digest({"tide-box": SCHEMA_VERSION, "recipe": parts}), "keys": digests}


def recipe_hash(profile: dict, templates_version: str, agents_version: str) -> str:
    return recipe_stamp(profile, templates_version, agents_version)["hash"]


def _digest(value) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def diff(profile: dict, stamp, templates_version: str = "", agents_version: str = "") -> list:
    """Klucze przepisu, które różnią się od zapisanego odcisku (źródło stanu „do przebudowy”).

    `stamp` to wynik `recipe_stamp` zapisany przy budowie (opis migawki). Gdy to sam napis hasha,
    nie znamy kluczy: różnica daje `["recipe"]`. Pusta lista = pudełko aktualne. Wersje szablonów i
    agentów podajemy tak samo jak przy `recipe_stamp`; różnica pojawia się jako `templates`/`agents`.
    """
    current = recipe_stamp(profile, templates_version, agents_version)
    if isinstance(stamp, str):
        return [] if stamp == current["hash"] else ["recipe"]
    if stamp.get("hash") == current["hash"]:
        return []
    old = stamp.get("keys", {})
    changed = [k for k in current["keys"] if old.get(k) != current["keys"][k]]
    return changed or ["recipe"]


# ------------------------------------------------------------ Tablica i repozytorium

def board_dir() -> str:
    return os.environ.get("TIDE_BOARD_DIR") or os.path.expanduser("~/DevWorkspace/tide-board")


def _check_slug(value: str, what: str) -> str:
    if not isinstance(value, str) or not _SLUG.match(value) or value in (".", ".."):
        raise ProfileError(f"{what}: niedozwolona nazwa {value!r}")
    return value


def project_path(slug: str) -> str:
    return os.path.join(board_dir(), "projects", _check_slug(slug, "projekt"), "box.yaml")


def card_path(slug: str, card: str) -> str:
    card_id = str(card).lower()
    return os.path.join(board_dir(), "projects", _check_slug(slug, "projekt"), "cards",
                        _check_slug(card_id, "karta"), "box.yaml")


def _write_atomic(path: str, text: str) -> None:
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".box.yaml.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path: str, scope: str):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read(_MAX_FILE + 1)
    except FileNotFoundError:
        return None
    if len(text) > _MAX_FILE:
        raise ProfileError(f"{path}: plik większy niż {_MAX_FILE} bajtów")
    return parse(text, scope)


def save_project(slug: str, profile: dict) -> str:
    """Zapisuje profil projektu w Tablicy (po walidacji); zwraca ścieżkę."""
    clean = validate(profile, "project")
    if clean.setdefault("project", slug) != slug:
        raise ProfileError(f"project: {clean['project']!r} różni się od sluga {slug!r}")
    path = project_path(slug)
    _write_atomic(path, dump(clean))
    return path


def load_project(slug: str):
    got = _read(project_path(slug), "project")
    if got is not None and got.get("project", slug) != slug:
        raise ProfileError(f"project: {got['project']!r} różni się od sluga {slug!r}")
    return got


def save_card(slug: str, card: str, profile: dict) -> str:
    clean = validate(profile, "card")
    path = card_path(slug, card)
    _write_atomic(path, dump(clean))
    return path


def load_card(slug: str, card: str):
    return _read(card_path(slug, card), "card")


def repo_proposal(worktree: str):
    """Propozycja profilu z `.tide/box.yaml` repozytorium projektu (tylko odczyt, `None` gdy brak).

    Wynik trzeba pokazać użytkownikowi i dopiero po zatwierdzeniu zapisać w Tablicy (`save_project`);
    plik w repozytorium nigdy nie jest zapisywany ani zmieniany. Dowiązanie wychodzące poza worktree jest odrzucane.
    """
    root = os.path.realpath(worktree)
    path = os.path.join(root, ".tide", "box.yaml")
    if not os.path.lexists(path):
        return None
    real = os.path.realpath(path)
    if os.path.commonpath([root, real]) != root:
        raise ProfileError(".tide/box.yaml: dowiązanie wychodzi poza repozytorium")
    if not os.path.isfile(real):
        raise ProfileError(".tide/box.yaml: to nie jest zwykły plik")
    return _read(real, "project")
