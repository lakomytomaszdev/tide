#!/usr/bin/python3
"""Strażnik treści przed push do repozytorium klienta (K-91, PT-322).

Użycie: /usr/bin/python3 tools/push_guard.py REPOZYTORIUM GAŁĄŹ-KARTY GAŁĄŹ-DOCELOWA
            [--project NAZWA] [--prefix PREFIKS ...] [--plans KATALOG ...] [--config PLIK] [--json]

Sprawdza to, co trafiłoby do repozytorium klienta, czyli commity z zakresu DOCELOWA..KARTY:
temat i treść każdego commita (także scalenia) oraz dodane linie w diffie każdego commita; dla
scaleń są to zmiany wprowadzone przy scalaniu (rozwiązanie konfliktu, `git show --remerge-diff`,
wymaga git >= 2.36). Szuka śladów wewnętrznych: Tide, YouTrack, numerów kart `K-*`, spraw `S-*`,
ticketów (`PT-*`, `LOG-*` i prefiksy planów projektu, np. `DLE-*`), stopek `Co-Authored-By`,
„Generated with” i wzmianek o AI.

Wynik: lista trafień (commit, miejsce, reguła, tekst) i raport commitów do przeredagowania
(trafienie w temacie albo treści; w diffie trzeba poprawić kod, nie opis) albo „czysto”.
Kod wyjścia: 0 — czysto, 1 — są trafienia (wysyłka wstrzymana), 2 — błąd (zła gałąź, brak git,
zła konfiguracja).

Prefiksy planów: domyślnie z `docs/agents/plans/` repozytorium klienta; projekty `ai-docs: tide` trzymają plany
na Tablicy Tide, więc ich prefiksy trzeba wpisać do konfiguracji (`prefixes`) albo podać `--prefix`/`--plans`
(czytanie planów z Tablicy: K-57).

Wzorce: jedna lista `RULES` poniżej + prefiksy ticketów: stałe `LOG`, `PT`, prefiksy z `--prefix`,
z `ticket-prefix:` planów (`--plans`, domyślnie `docs/agents/plans/` sprawdzanego repozytorium, jeśli istnieje)
i z konfiguracji projektu. Numery `K-*` i ticketów rozpoznawane są wielkimi literami, a małymi tylko w nazwach
gałęzi (po `/` albo cudzysłowie, np. `ticket/dle-1-…`).
Konfiguracja per projekt (`tools/push_guard.json`, klucz = nazwa repozytorium małymi literami wyznaczona
z katalogu głównego `.git`, więc worktree `<repo>-worktrees/<slug>` daje nazwę repozytorium, albo `--project`;
użyty projekt i prefiksy są wypisywane, a nieznany projekt daje ostrzeżenie):`prefixes` (lista) i `allow` — wyjątki dla słów, które w kodzie klienta są nazwą
domenową (np. „tide” jako pływ w aplikacji o morzu): `{"rule": "tide", "path": "src/Domain/*",
"line": "regex"}`; pola `rule`, `path` (fnmatch; dla commitów bez pliku nie pasuje) i `line`
(regex na całej linii) są opcjonalne i wszystkie podane muszą pasować. Wyjątek bez `path`
obejmuje też temat i treść commita.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "push_guard.json")
FIXED_PREFIXES = ("PT", "LOG")
_L = r"(?<![A-Za-z0-9_])"  # początek identyfikatora (K-12 nie w „SHA-12”)
_BRANCH = r"(?<=[/'\"])"  # nazwa gałęzi (`ticket/dle-1-…`, `'dle-1'`): tu wielkość liter nie ma znaczenia

# Jedna lista stałych wzorców: (nazwa reguły, opis, regex). Reguła `ticket` jest budowana z prefiksów.
RULES = (
    ("tide", "Tide", re.compile(r"\btide\b", re.I)),
    ("youtrack", "YouTrack", re.compile(r"you\s?track", re.I)),
    ("card", "numer karty K-*", re.compile(_L + r"K-\d+\b|" + _BRANCH + r"(?i:k-\d+\b)")),
    ("case", "numer sprawy S-*", re.compile(_L + r"S-\d+\b")),
    ("trailer", "stopka Co-Authored-By", re.compile(r"co-authored-by\s*:", re.I)),
    ("generated", "„Generated with”", re.compile(r"generated\s+with\b", re.I)),
    ("ai", "wzmianka o AI", None),  # dwa wzorce niżej: wielkość liter ma znaczenie tylko dla „AI”
)
# „AI” jako słowo wielkimi literami; pozostałe nazwy bez względu na wielkość liter.
_AI_CASE_SENSITIVE = re.compile(r"\bAI\b|\bA\.I\.|\bLLMs?\b")
_AI_NAMES = re.compile(
    r"\bClaude\b|\bAnthropic\b|\bCodex\b|\bOpenAI\b|\bChatGPT\b|\bGPT-?\d|\bCopilot\b|\bGemini\b|"
    r"\bagent(?:a|em|owi|y|ów)?\s+AI\b|\U0001F916", re.I)


class GuardError(Exception):
    pass


def run_git(repo, *args) -> str:
    try:
        done = subprocess.run(["git", "-c", "core.quotepath=off", "-C", repo, *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise GuardError(f"nie można uruchomić git: {exc}") from exc
    if done.returncode != 0:
        raise GuardError(f"git {' '.join(args)}: {done.stderr.strip() or done.returncode}")
    return done.stdout


def load_config(path, project, explicit=False):
    """(konfiguracja projektu, czy projekt jest w pliku). Błąd pliku, regexu albo jawna brakująca ścieżka → GuardError."""
    if not path or not os.path.isfile(path):
        if explicit:
            raise GuardError(f"brak pliku konfiguracji: {path}")
        return {}, False
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise GuardError(f"konfiguracja {path}: {exc}") from exc
    projects = data.get("projects") or {}
    config = projects.get(project) or {}
    for entry in config.get("allow") or []:
        if entry.get("line"):
            try:
                re.compile(entry["line"])
            except re.error as exc:
                raise GuardError(f"konfiguracja {path}: zły regex w allow ({entry['line']!r}): {exc}") from exc
    return config, project in projects


def project_name(repo) -> str:
    """Nazwa repozytorium z katalogu głównego `.git` (worktree karty daje nazwę repozytorium, nie slug karty)."""
    common = run_git(repo, "rev-parse", "--git-common-dir").strip()
    common = os.path.normpath(os.path.join(repo, common))
    name = os.path.basename(common)
    if name == ".git":
        name = os.path.basename(os.path.dirname(common))
    elif name.endswith(".git"):
        name = name[:-4]
    return name.lower()


def plan_prefixes(directories) -> list:
    found = []
    for directory in directories:
        if not os.path.isdir(directory):
            raise GuardError(f"brak katalogu planów: {directory}")
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".md"):
                continue
            try:
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    head = fh.read(2000)
            except OSError:
                continue
            match = re.search(r"^ticket-prefix:\s*([A-Za-z][A-Za-z0-9]*)\s*$", head, re.M)
            if match and head.startswith("---"):
                found.append(match.group(1).upper())
    return found


def all_prefixes(prefixes) -> list:
    return sorted({p.upper() for p in (*FIXED_PREFIXES, *prefixes) if p})


def build_rules(prefixes) -> list:
    unique = all_prefixes(prefixes)
    alt = "|".join(re.escape(p) for p in unique)
    ticket = re.compile(_L + "(?:" + alt + r")-\d+\b|" + _BRANCH + "(?i:(?:" + alt + r")-\d+\b)")
    rules = list(RULES)
    rules.insert(4, ("ticket", "numer ticketu " + "/".join(f"{p}-*" for p in unique), ticket))
    return rules


def line_matches(rules, text):
    """Zwraca (reguła, opis, dopasowany tekst) dla każdej reguły pasującej do linii."""
    for name, label, rx in rules:
        if rx is None:
            match = _AI_CASE_SENSITIVE.search(text) or _AI_NAMES.search(text)
        else:
            match = rx.search(text)
        if match:
            yield name, label, match.group(0)


def allowed(allow, rule, path, line) -> bool:
    for entry in allow:
        if entry.get("rule") and entry["rule"] != rule:
            continue
        if entry.get("path") and not (path and fnmatch.fnmatch(path, entry["path"])):
            continue
        if entry.get("line") and not re.search(entry["line"], line):
            continue
        if not any(entry.get(k) for k in ("rule", "path", "line")):
            continue
        return True
    return False


def commits(repo, target, branch) -> list:
    raw = run_git(repo, "log", "--reverse", "--format=%H%x1f%P%x1f%s%x1f%b%x1e", f"{target}..{branch}")
    result = []
    for chunk in raw.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, parents, subject, body = chunk.split("\x1f", 3)
        result.append({"sha": sha, "merge": len(parents.split()) > 1, "subject": subject, "body": body})
    return result


def _diff_path(raw):
    """Ścieżka z nagłówka `+++ ` (git ujmuje w cudzysłów ścieżki z `"`, `\\` i znakami sterującymi); /dev/null → None."""
    if len(raw) > 1 and raw.startswith('"') and raw.endswith('"'):
        body = raw[1:-1]
        simple = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}
        out, i = bytearray(), 0
        while i < len(body):
            ch = body[i]
            if ch != "\\" or i + 1 >= len(body):
                out += ch.encode("utf-8")
                i += 1
            elif body[i + 1].isdigit():
                out.append(int(body[i + 1:i + 4], 8) & 255)
                i += 4
            else:
                nxt = body[i + 1]
                out.append(simple[nxt] if nxt in simple else ord(nxt))
                i += 2
        raw = out.decode("utf-8", errors="replace")
    return raw[2:] if raw.startswith("b/") else None


def added_lines(repo, sha, merge=False):
    """(plik, numer linii, tekst) dla dodanych linii commita; dla scalenia: zmiany względem automatycznego scalenia."""
    args = ["show", "--format=", "-U0", "--no-color", "--no-ext-diff"]
    patch = run_git(repo, *args, *(["--remerge-diff"] if merge else []), sha)
    path, number, header = None, 0, False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("+++ "):
            path = _diff_path(line[4:])
        elif line.startswith("@@"):
            header = False
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            number = int(match.group(1)) if match else 0
        elif not header and line.startswith("+") and path:
            yield path, number, line[1:]
            number += 1


def scan(repo, branch, target, prefixes=(), allow=()) -> list:
    rules = build_rules(prefixes)
    hits = []

    def add(commit, where, path, number, text):
        for rule, label, found in line_matches(rules, text):
            if allowed(allow, rule, path, text):
                continue
            hits.append({"commit": commit["sha"], "subject": commit["subject"], "merge": commit["merge"],
                         "where": where, "file": path, "line": number, "rule": rule, "label": label,
                         "match": found, "text": text.strip()[:160]})

    for commit in commits(repo, target, branch):
        add(commit, "temat", None, None, commit["subject"])
        for number, text in enumerate(commit["body"].splitlines(), 1):
            add(commit, "treść", None, number, text)
        for path, number, text in added_lines(repo, commit["sha"], commit["merge"]):
            add(commit, "diff", path, number, text)
    return hits


def format_report(hits) -> str:
    if not hits:
        return "czysto: brak śladów wewnętrznych w historii i diffie"
    out = [f"TRAFIENIA: {len(hits)}"]
    for h in hits:
        place = h["where"] if h["where"] != "diff" else f"{h['file']}:{h['line']}"
        if h["where"] == "treść":
            place = f"treść:{h['line']}"
        out.append(f"  {h['commit'][:8]} {place} [{h['rule']}] „{h['match']}” — {h['text']}")
    reword = {}
    for h in hits:
        if h["where"] in ("temat", "treść"):
            reword.setdefault(h["commit"], h)
    out.append("")
    if reword:
        out.append(f"DO PRZEREDAGOWANIA (opis commita): {len(reword)}")
        for sha, h in reword.items():
            kind = "scalenie" if h["merge"] else "commit"
            out.append(f"  {sha[:8]} ({kind}) {h['subject']}")
    diff_commits = sorted({h["commit"][:8] for h in hits if h["where"] == "diff"})
    if diff_commits:
        out.append("DO POPRAWIENIA W KODZIE (diff; commit trzeba przepisać): " + ", ".join(diff_commits))
    out.append("Wysyłka wstrzymana.")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Strażnik treści przed push do repozytorium klienta.")
    parser.add_argument("repo", help="katalog repozytorium klienta (albo jego worktree)")
    parser.add_argument("branch", help="gałąź karty")
    parser.add_argument("target", help="gałąź docelowa")
    parser.add_argument("--project", default="", help="klucz projektu w konfiguracji (domyślnie nazwa katalogu)")
    parser.add_argument("--prefix", action="append", default=[], help="dodatkowy prefiks ticketów (np. DLE)")
    parser.add_argument("--plans", action="append", default=[], help="katalog planów z `ticket-prefix:`")
    parser.add_argument("--config", default=None, help="plik konfiguracji (domyślnie tools/push_guard.json)")
    parser.add_argument("--json", action="store_true", help="wynik jako JSON")
    args = parser.parse_args(argv)
    try:
        repo = os.path.abspath(args.repo)
        for ref in (args.branch, args.target):
            run_git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
        project = (args.project or project_name(repo)).lower()
        config, known = load_config(args.config or DEFAULT_CONFIG, project, explicit=bool(args.config))
        plans = args.plans or [d for d in (os.path.join(repo, "docs", "agents", "plans"),) if os.path.isdir(d)]
        prefixes = [*config.get("prefixes", []), *args.prefix, *plan_prefixes(plans)]
        warnings = [] if known else [f"projekt „{project}” nie ma wpisu w konfiguracji: brak jego prefiksów i wyjątków "
                                      f"(użyj --project albo --prefix)"]
        hits = scan(repo, args.branch, args.target, prefixes, config.get("allow", []))
    except GuardError as exc:
        print(f"push_guard: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"push_guard: OSTRZEŻENIE: {warning}", file=sys.stderr)
    if args.json:
        print(json.dumps({"clean": not hits, "project": project, "prefixes": all_prefixes(prefixes),
                          "warnings": warnings, "hits": hits}, ensure_ascii=False, indent=2))
    else:
        print(f"projekt: {project}; prefiksy ticketów: " + ", ".join(f"{p}-*" for p in all_prefixes(prefixes)))
        print(format_report(hits))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
