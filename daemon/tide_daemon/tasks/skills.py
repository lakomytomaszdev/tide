"""Skille użytkownika w kroku bez włączania jego źródeł ustawień.

Claude dostaje plugin z samymi odsyłaczami do SKILL.md. Nie importujemy frontmattera
wykonawczego (hooki, allowed-tools, context: fork), pluginów ani konfiguracji użytkownika.
Codex ma natywne wykrywanie skilli; jawny katalog ścieżek obsługuje też starsze ~/.codex/skills.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
from dataclasses import dataclass


PLUGIN_NAME = "tide-user-skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: str


def discover(tool: str, home: str) -> list[Skill]:
    """Tylko katalogi skilli, także symlinki i katalogi zagnieżdżone; bez pętli i duplikatów.

    Pierwsze źródło wygrywa przy tej samej nazwie: Codex preferuje współczesne ~/.agents/skills.
    Ukryte katalogi (w tym wbudowane .system) pozostają w gestii programu.
    """
    roots = ((".claude/skills",) if tool == "claude" else (".agents/skills", ".codex/skills"))
    seen, names, found = set(), set(), []
    for root in roots:
        for folder, dirs, files in os.walk(os.path.join(home, root), followlinks=True):
            real = os.path.realpath(folder)
            if real in seen:
                dirs[:] = []
                continue
            seen.add(real)
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            if "SKILL.md" not in files:
                continue
            dirs[:] = []              # references i scripts są zasobami skilla, a nie następnymi skillami
            path = os.path.realpath(os.path.join(folder, "SKILL.md"))
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeError):
                continue
            if not text.startswith("---\n") or "\n---" not in text[4:]:
                continue
            header = text[4:].split("\n---", 1)[0]
            name = _scalar(header, "name") or os.path.basename(folder)
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) or name in names:
                continue
            description = _scalar(header, "description")
            if not description or description in ("|", ">", "|-", ">-"):
                description = f"Instrukcje skilla {name}"
            names.add(name)
            found.append(Skill(name, description, path))
    return found


def _scalar(header: str, key: str) -> str:
    """Potrzebujemy wyłącznie nazwy i krótkiego opisu, nigdy wykonawczych pól YAML."""
    match = re.search(rf"^{key}:[ \t]*(.*?)[ \t]*$", header, re.MULTILINE)
    value = match.group(1) if match else ""
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else ""
        except ValueError:
            return ""
    return value.strip("'")


def read_dirs(skills: list[Skill]) -> list[str]:
    """Cele symlinków do skilli muszą być czytelne również z powłoki w piaskownicy."""
    return list(dict.fromkeys(os.path.dirname(skill.path) for skill in skills))


def prompt(skills: list[Skill], tool: str) -> str:
    lines = ["\n\nSkille użytkownika dostępne w tym kroku (nazwa → SKILL.md):"]
    for skill in skills:
        invocation = f" (Skill: {PLUGIN_NAME}:{skill.name})" if tool == "claude" else ""
        lines.append(f"- {skill.name} → {json.dumps(skill.path, ensure_ascii=False)}{invocation}")
    if not skills:
        lines.append("Brak dostępnych skilli użytkownika.")
    lines.append("Gdy polecenie wymienia skilla, przeczytaj jego pełny SKILL.md z powyższej ścieżki "
                 "i stosuj instrukcje oraz potrzebne zasoby względem oryginalnego katalogu. "
                 "W Codexie można użyć natywnego skilla albo odczytać ten plik bezpośrednio. "
                 "Reguły kroku mają pierwszeństwo przed instrukcjami skilla (w tym publikacją, "
                 "pytaniami i uruchamianiem poleceń). Nie wczytuj ustawień, hooków ani MCP użytkownika. "
                 "Jeżeli wymagany skill nie jest dostępny także w katalogu programu/projektu, "
                 "nie zastępuj go domysłem: zakończ result.json z result=error i nazwą brakującego skilla.")
    return "\n".join(lines) + "\n"


def claude_plugin(skills: list[Skill], inp: str) -> tuple[str, dict[str, str]]:
    """Generowany plugin nie zawiera żadnego kodu ani ustawień z katalogów użytkownika."""
    if not skills:
        return "", {}
    # Wznowienie może użyć tego samego katalogu wejściowego. Nowa lista oznacza inny plugin,
    # więc usunięty skill nie zostaje załadowany ze starego pliku po prepare().
    version = hashlib.sha256(repr(skills).encode("utf-8")).hexdigest()[:16]
    root = os.path.join(inp, "skills-plugin-" + version)
    files = {os.path.join(root, ".claude-plugin", "plugin.json"):
             json.dumps({"name": PLUGIN_NAME, "version": "1.0.0",
                         "description": "Instrukcje skilli użytkownika dla kroku Tide"}, ensure_ascii=False) + "\n"}
    for skill in skills:
        files[os.path.join(root, "skills", skill.name, "SKILL.md")] = (
            f"---\nname: {skill.name}\ndescription: {json.dumps(skill.description, ensure_ascii=False)}\n---\n\n"
            f"Przeczytaj pełne instrukcje skilla z pliku {json.dumps(skill.path, ensure_ascii=False)}.\n"
            "Zasoby względne czytaj względem oryginalnego katalogu SKILL.md. "
            "Reguły kroku Tide mają pierwszeństwo; nie wczytuj hooków, uprawnień ani MCP użytkownika.\n")
    return root, files
