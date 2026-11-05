"""Podpowiedzi są danymi z lokalnego spisu refów, nigdy wyrażeniami rewizji gita."""
import re


MAX_CANDIDATES = 32
MAX_EVIDENCE = 5
MAX_EXCERPT = 512
MAX_WARNINGS = 20
MAX_WARNING_NAME = 256
COMMAND_CONTEXT = 80


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
    warning_keys = set()
    saturated_names = set()
    truncated = False
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
                if name in saturated_names:
                    continue
                for branch in names[name]:
                    ref = branch["ref"]
                    if ref not in evidence and len(evidence) >= MAX_CANDIDATES:
                        truncated = True
                        continue
                    items = evidence.setdefault(ref, [])
                    if len(items) >= MAX_EVIDENCE:
                        continue
                    # Zakres dotyczy wycinka, nie całego wiersza. Pełna nazwa zostaje w kandydacie.
                    start = max(0, match.start() - 80)
                    end = min(len(line), start + MAX_EXCERPT)
                    start = max(0, end - MAX_EXCERPT)
                    visible_end = min(match.end(), end)
                    items.append({"field": field, "line": number, "text": line[start:end],
                                  "match": line[match.start():visible_end],
                                  "start": match.start() - start, "end": visible_end - start})
                if all(len(evidence.get(b["ref"], ())) >= MAX_EVIDENCE
                       or b["ref"] not in evidence and len(evidence) >= MAX_CANDIDATES
                       for b in names[name]):
                    saturated_names.add(name)
            for match in tokens.finditer(line):
                name = match.group().rstrip(".")
                found = names.get(name, [])
                # Informacja o wskazaniu, bez prób rozwiązywania go przez rev-parse.
                before = line[max(0, match.start() - COMMAND_CONTEXT):match.start()]
                quoted_branch = ("/" in name and before.endswith("`")
                                 and line[match.end():match.end() + 1] == "`")
                command_target = re.search(r"\b(?:git\s+(?:checkout|switch)|branch)\s+[`'\"]?$", before)
                # Sam backtick ani polskie słowo „baza” nie dowodzą wskazania gałęzi.
                indicated = name.startswith("refs/") or quoted_branch or command_target
                if name and not name.startswith("-") and not found and indicated:
                    label = "Opis" if field == "description" else "Polecenie"
                    shown_name = name[:MAX_WARNING_NAME] + ("…" if len(name) > MAX_WARNING_NAME else "")
                    warning = f"Nieznana nazwa — {label}, wiersz {number}: {shown_name}"
                    if warning in warning_keys:
                        continue
                    if len(warnings) < MAX_WARNINGS:
                        warning_keys.add(warning)
                        warnings.append(warning)
                    else:
                        truncated = True
    candidates = [{**branch, "source": "text", "strength": "explicit", "evidence": evidence[branch["ref"]]}
                  for branch in branches if branch["ref"] in evidence]
    if len(candidates) > 1:
        warnings.append("Tekst wskazuje kilka baz. Wybierz jawnie pełny ref; podpowiedzi nie rozstrzygają konfliktu.")
    if truncated:
        warnings.append("Ograniczono listę podpowiedzi lub ostrzeżeń. Wybierz bazę ręcznie, jeśli jej tutaj brakuje.")
    return {"candidates": candidates, "warnings": warnings}
