"""Szablony stosów pudełka (#30): dane w `box/stacks/<stos>.yaml`, nie kod powłoki.

Szablon niesie pełną listę domen potrzebnych do budowy i pracy (klasa ryzyka `green`/`yellow`/`red`), pakiety apt
i domyślne obrazy Dockera; `build-domains` (PT-192) to domeny wyłącznie czasu budowy (skrypty i usługi obrazu).
Z szablonów wynikają: `build_domains(profile)` (lista dla proxy #5 w czasie budowy #4, z `build-domains`),
`work_domains(profile)` (polityka kroków, bez `build-domains`), `known_domains()` (słownik domena → klasa dla
`classify` z #2, tylko `domains`) i `registry_domains(stack)` (sieć kroku w tle).

Projekt nie używa biblioteki YAML, więc plik czyta mały czytnik podzbioru: skalary, listy `[a, b]`, listy
blokowe skalarów i map jednopoziomowych. Szablony nie mogą zawierać niczego poza tym podzbiorem.
"""
import os

STACKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stacks")
RISKS = ("green", "yellow", "red")
BASE = "base"
BUILD_ONLY = "build-domains"   # domeny tylko w czasie budowy (PT-192)

_cache: dict = {}


class TemplateError(ValueError):
    """Szablon stosu, którego nie umiemy przeczytać (opis po polsku, z nazwą pliku i linią)."""


def _scalar(text: str):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(part) for part in inner.split(",")] if inner else []
    return text


def _split(text: str) -> tuple[str, str]:
    key, sep, rest = text.partition(":")
    if not sep:
        raise ValueError(f"„{text}” nie jest parą klucz: wartość")
    return key.strip(), rest.strip()


def parse(text: str, name: str = "szablon") -> dict:
    """Podzbiór YAML używany przez szablony; błąd → `TemplateError`."""
    result: dict = {}
    key = None  # klucz z listą blokową, do którego trafiają pozycje `- …`
    item = None
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        body = raw.strip()
        try:
            if indent == 0:
                key, rest = _split(body)
                item = None
                result[key] = _scalar(rest) if rest else []
            elif key is None:
                raise ValueError("wcięta linia bez klucza")
            elif body.startswith("- "):
                content = body[2:].strip()
                if ":" in content and not content.startswith(("\"", "'")) and not _is_plain_colon_free(content):
                    k, v = _split(content)
                    item = {k: _scalar(v)}
                    result[key].append(item)
                else:
                    item = None
                    result[key].append(_scalar(content))
            elif item is not None:
                k, v = _split(body)
                item[k] = _scalar(v)
            else:
                raise ValueError("kontynuacja bez pozycji listy")
        except (ValueError, AttributeError) as exc:
            raise TemplateError(f"{name}, linia {number}: {exc}") from exc
    return result


def _is_plain_colon_free(content: str) -> bool:
    # Obraz `repo/nazwa:tag` jako goły skalar listy: dwukropek bez spacji po nim to nie para klucz: wartość.
    key, sep, rest = content.partition(":")
    return bool(sep) and not rest.startswith(" ") and rest != ""


def _validate(data: dict, name: str) -> dict:
    if data.get("stack") != name:
        raise TemplateError(f"{name}.yaml: pole stack musi być równe „{name}”")
    for field in ("domains", BUILD_ONLY):
        if not isinstance(data.get(field, []), list):
            raise TemplateError(f"{name}.yaml: {field} musi być listą")
        for entry in data.get(field, []):
            if not isinstance(entry, dict) or not entry.get("name") or entry.get("risk") not in RISKS:
                raise TemplateError(f"{name}.yaml: domena bez name albo z nieznaną klasą ryzyka: {entry!r}")
    for field in ("apt", "images"):
        if not isinstance(data.get(field, []), list):
            raise TemplateError(f"{name}.yaml: {field} musi być listą")
    return data


def stack_names() -> list[str]:
    """Nazwy szablonów (bez `base`), alfabetycznie."""
    names = sorted(f[:-5] for f in os.listdir(STACKS_DIR) if f.endswith(".yaml"))
    return [n for n in names if n != BASE]


def load(stack: str) -> dict:
    """Szablon stosu jako słownik (z pamięci podręcznej; nie modyfikuj wyniku)."""
    if stack not in _cache:
        path = os.path.join(STACKS_DIR, f"{stack}.yaml")
        if "/" in stack or not os.path.isfile(path):
            raise ValueError(f"nieznany szablon stosu: {stack!r}")
        with open(path, encoding="utf-8") as handle:
            _cache[stack] = _validate(parse(handle.read(), f"{stack}.yaml"), stack)
    return _cache[stack]


def _profile_stacks(profile: dict) -> list[str]:
    return [str(s) for s in (profile.get("stacks") or [])]


def work_domains(profile: dict) -> tuple:
    """Domeny pracy: `domains` szablonów `base` + wg `profile["stacks"]` + `network.domains`; bez powtórzeń,
    kolejność stabilna. To lista polityki kroków (`proxy.compose_policy`)."""
    names = [BASE, *_profile_stacks(profile)]
    domains = [d["name"] for stack in names for d in load(stack).get("domains", [])]
    domains += [str(d) for d in ((profile.get("network") or {}).get("domains") or [])]
    return tuple(dict.fromkeys(domains))


def build_domains(profile: dict) -> tuple:
    """Lista budowy (proxy #5 w czasie `tide box build`/`tide box base`): `work_domains` + `build-domains`
    szablonów wg `stacks` — np. wydanie runc z github.com i usługa motd-news obrazu Ubuntu."""
    names = [BASE, *_profile_stacks(profile)]
    extra = [d["name"] for stack in names for d in load(stack).get(BUILD_ONLY, [])]
    return tuple(dict.fromkeys([*work_domains(profile), *extra]))


def build_apt(profile: dict) -> tuple:
    """Pakiety apt z `base` i szablonów oraz `apt` profilu."""
    names = [BASE, *_profile_stacks(profile)]
    apt = [p for stack in names for p in load(stack).get("apt", [])] + [str(p) for p in profile.get("apt") or []]
    return tuple(dict.fromkeys(apt))


def build_images(profile: dict) -> tuple:
    """Domyślne obrazy Dockera szablonów wg `stacks` oraz `docker.images` profilu."""
    images = [i for stack in _profile_stacks(profile) for i in load(stack).get("images", [])]
    images += [str(i) for i in (profile.get("docker") or {}).get("images") or []]
    return tuple(dict.fromkeys(images))


def known_domains() -> dict:
    """Domena → klasa ryzyka ze wszystkich szablonów (przy rozbieżności wygrywa groźniejsza)."""
    known: dict = {}
    for stack in [BASE, *stack_names()]:
        for entry in load(stack).get("domains", []):
            old = known.get(entry["name"])
            if old is None or RISKS.index(entry["risk"]) > RISKS.index(old):
                known[entry["name"]] = entry["risk"]
    return known


def registry_domains(stack: str) -> tuple:
    """Domeny rejestrów paczek stosu (`registry: true`) — sieć kroku w tle."""
    return tuple(d["name"] for d in load(stack).get("domains", []) if d.get("registry") is True)
