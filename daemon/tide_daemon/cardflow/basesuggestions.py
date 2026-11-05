"""Podpowiedzi są danymi z lokalnego spisu refów, nigdy wyrażeniami rewizji gita."""
import re


def suggest_text(branches: list[dict], description: str, text: str) -> dict:
    names = {}
    for branch in branches:
        aliases = {branch["name"], branch["ref"]}
        if branch["ref"].startswith("refs/remotes/"):
            # Krótka nazwa nie wybiera remote: każdy odpowiadający ref ma własny dowód.
            short_name = branch["ref"][len("refs/remotes/"):].partition("/")[2]
            if short_name:
                aliases.add(short_name)
        for name in aliases:
            names.setdefault(name, []).append(branch)
    evidence = {}
    warnings = []
    separators = r"\s`'\"(),;:|“”„\[\]?!*—<>"
    # Najdłuższa pełna nazwa ma pierwszeństwo, również gdy zawiera znak interpunkcyjny.
    known = re.compile(r"(?<![^" + separators + r"])(?:"
                       + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
                       + r")(?=$|[" + separators + r"]|\.(?=$|[" + separators + r"]))") if names else None
    # Granice obejmują całe nazwy i wyrażenia rewizji: foo nie pasuje do foo/bar ani foo~1.
    tokens = re.compile(r"[^\s`'\"(),;:|“”„]+")
    for field, value in (("description", description), ("text", text)):
        for number, line in enumerate(value.splitlines(), 1):
            for match in known.finditer(line) if known else ():
                name = match.group()
                for branch in names[name]:
                    evidence.setdefault(branch["ref"], []).append({
                        "field": field, "line": number, "text": line, "match": name,
                        "start": match.start(), "end": match.end()})
            for match in tokens.finditer(line):
                name = match.group().rstrip(".")
                found = names.get(name, [])
                # Informacja o wskazaniu, bez prób rozwiązywania go przez rev-parse.
                before = line[:match.start()]
                quoted_branch = ("/" in name and before.endswith("`")
                                 and line[match.end():].startswith("`"))
                command_target = re.search(r"\b(?:git\s+(?:checkout|switch)|branch)\s+[`'\"]?$", before)
                # Sam backtick ani polskie słowo „baza” nie dowodzą wskazania gałęzi.
                indicated = name.startswith("refs/") or quoted_branch or command_target
                if name and not name.startswith("-") and not found and indicated:
                    label = "Opis" if field == "description" else "Polecenie"
                    warning = f"Nieznana nazwa — {label}, wiersz {number}: {name}"
                    if warning not in warnings:
                        warnings.append(warning)
    candidates = [{**branch, "source": "text", "strength": "explicit", "evidence": evidence[branch["ref"]]}
                  for branch in branches if branch["ref"] in evidence]
    if len(candidates) > 1:
        warnings.append("Tekst wskazuje kilka baz. Wybierz jawnie pełny ref; podpowiedzi nie rozstrzygają konfliktu.")
    return {"candidates": candidates, "warnings": warnings}
