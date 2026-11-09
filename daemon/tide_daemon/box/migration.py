"""Jednorazowa migracja `networkDomains[slug]` i `outsideCommands[slug]` z `cardflow.json` do propozycji profilu (PT-190).

Wynik to `projects/<slug>/box.proposed.yaml` w Tablicy — propozycja do zatwierdzenia przez użytkownika, nigdy
zatwierdzony `box.yaml`. Plik powstaje raz, tylko gdy projekt nie ma profilu ani wcześniejszej propozycji.
Stare klucze zostają w `cardflow.json` (usuwa je osobny ticket). Wpisy, których schemat profilu projektu nie
przyjmuje (adres wewnętrzny, zbyt szeroki symbol wieloznaczny, nieprawidłowa nazwa), nie trafiają do propozycji,
tylko do `notes`, żeby użytkownik zobaczył, co pominięto.

Opis: docs/tide-daemon.md (Migracja cardflow.json do propozycji profilu).
"""
import os
import re

from . import profile

PROPOSED = "box.proposed.yaml"

# Polecenia przygotowujące środowisko (`setup`); pozostałe sprawdzają projekt (`verify`).
_SETUP = re.compile(r"\b(install|ci|restore|setup|pull|bootstrap)\b|playwright install|apt(-get)?\s")


def proposed_path(slug: str) -> str:
    return os.path.join(os.path.dirname(profile.project_path(slug)), PROPOSED)


def _command(entry: str) -> str:
    """`npm test *` (rodzina poleceń) → `npm test`; dokładne polecenie zostaje."""
    entry = entry.strip()
    return entry[:-2].rstrip() if entry.endswith(" *") else entry


def build(slug: str, settings: dict) -> dict | None:
    """Propozycja profilu z ustawień dla jednego projektu; `None`, gdy nie ma czego migrować."""
    domains = (settings.get("networkDomains") or {}).get(slug) if isinstance(settings.get("networkDomains"), dict) else None
    commands = (settings.get("outsideCommands") or {}).get(slug) if isinstance(settings.get("outsideCommands"), dict) else None
    domains = [d for d in domains if isinstance(d, str) and d.strip()] if isinstance(domains, list) else []
    commands = [c for c in commands if isinstance(c, str) and c.strip()] if isinstance(commands, list) else []
    if not domains and not commands:
        return None
    skipped = []
    keep_domains = []
    for domain in domains:
        try:
            profile.validate({"tide-box": profile.SCHEMA_VERSION, "network": {"domains": [domain]}}, "project")
        except profile.ProfileError:
            skipped.append(domain)
        else:
            keep_domains.append(domain)
    setup, verify = [], []
    for entry in commands:
        command = _command(entry)
        target = setup if _SETUP.search(command) else verify
        if command and command not in target:
            target.append(command)
    out = {"tide-box": profile.SCHEMA_VERSION, "project": slug}
    if setup:
        out["setup"] = setup
    if keep_domains:
        out["network"] = {"domains": keep_domains}
    if verify:
        out["verify"] = verify
    notes = "Propozycja z migracji cardflow.json (networkDomains, outsideCommands); do zatwierdzenia przez użytkownika."
    if skipped:
        notes += " Pominięto domeny, których profil projektu nie przyjmuje: " + ", ".join(skipped) + "."
    out["notes"] = notes
    return profile.validate(out, "project")


def migrate(slugs, settings: dict) -> list:
    """Tworzy brakujące `box.proposed.yaml`; zwraca ścieżki utworzonych plików. Niczego nie zatwierdza."""
    created = []
    for slug in slugs:
        try:
            if os.path.lexists(profile.project_path(slug)) or os.path.lexists(proposed_path(slug)):
                continue
            proposal = build(slug, settings)
            if proposal is None:
                continue
            path = proposed_path(slug)
            profile._write_atomic(path, profile.dump(proposal))
            created.append(path)
        except (profile.ProfileError, OSError):
            continue   # zły slug albo brak zapisu w Tablicy nie zatrzymuje startu usługi
    return created


def settings_slugs(settings: dict) -> list:
    slugs = []
    for key in ("networkDomains", "outsideCommands"):
        value = settings.get(key)
        if isinstance(value, dict):
            slugs.extend(k for k in value if isinstance(k, str) and k not in slugs)
    return sorted(slugs)
