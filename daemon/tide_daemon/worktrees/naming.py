"""Czytelne nazwy worktrees: tytuł z nazwy gałęzi, narzędzie, które go założyło.

Gałęzie agentów mają długie, techniczne nazwy (`codex/summary-tabs-03-version-axis`). W oknie
chcemy krótki tytuł („summary-tabs 03 · version axis”) i osobno wyciszony znacznik z prefiksu
(„codex”). Reguły są heurystyczne — gdy nic nie pasuje, zostaje reszta nazwy bez zmian.
"""
import re
import unicodedata

# Prefiksy, które są tylko kategorią gałęzi, a nie jej treścią (trafiają do znacznika).
PREFIXES = ("codex", "claude", "feat", "feature", "fix", "bugfix", "hotfix", "docs", "doc", "chore",
            "refactor", "test", "tests", "perf", "ci", "build", "style", "release", "wip", "exp")
# Słowa, od których zaczyna się opis zmiany wewnątrz nazwy (summary-tabs-fix-card-list-header).
TYPE_WORDS = {"fix", "hotfix", "bugfix", "feat", "feature", "docs", "doc", "test", "tests", "chore", "refactor",
              "perf"}
TOOLS = {"codex": "codex", "claude": "claude"}

_DATE_SUFFIX = re.compile(r"-(\d{4}-\d{2}(?:-\d{2})?)$")
_CODE = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9]+$")  # la035, f10, v3, mr483 — litery z cyframi
_HEX = re.compile(r"^[0-9a-f]{12,}$")


def humanize_branch(branch: str) -> tuple[str, str]:
    """(znacznik, tytuł) z nazwy gałęzi, np. „codex/summary-tabs-03-version-axis” →
    („codex”, „summary-tabs 03 · version axis”)."""
    if not branch:
        return "", ""
    tag = ""
    rest = branch
    if "/" in rest:
        head, _, tail = rest.partition("/")
        if head.lower() in PREFIXES and tail:
            tag, rest = head.lower(), tail
    if not tag and rest.startswith("worktree-") and len(rest) > len("worktree-"):
        tag, rest = "worktree", rest[len("worktree-"):]
    rest = _DATE_SUFFIX.sub("", rest) or rest
    # Zagnieżdżone segmenty (a/b/c) czytamy jak kolejne człony tytułu.
    segments = [s for s in rest.split("/") if s]
    title = " · ".join(_humanize_segment(s) for s in segments) if segments else rest
    return tag, title


def _humanize_segment(segment: str) -> str:
    tokens = [_short_hex(t) for t in segment.split("-") if t]
    if len(tokens) < 2:
        return segment if not tokens else tokens[0]
    lower = [t.lower() for t in tokens]

    # „fix-unique-phrase-entry” → „fix · unique phrase entry”
    if lower[0] in TYPE_WORDS:
        return _join(tokens[0], tokens[1:])
    # „tom-128-index-generation-plan” → „tom-128 · index generation plan” (zgłoszenie z numerem)
    if tokens[0].isalpha() and tokens[1].isdigit():
        return _join(f"{tokens[0]}-{tokens[1]}", tokens[2:])
    # „la035-atomic-open-approval” → „la035 · atomic open approval”
    if _CODE.match(lower[0]):
        return _join(tokens[0], tokens[1:])
    # Seria: „summary-tabs-03-version-axis” → „summary-tabs 03 · version axis”,
    #        „summary-tabs-fix-card-list-header” → „summary-tabs · fix card list header”.
    for i in range(1, len(tokens)):
        if tokens[i].isdigit() or _CODE.match(lower[i]):
            return _join("-".join(tokens[:i]) + " " + tokens[i], tokens[i + 1:])
        if lower[i] in TYPE_WORDS:
            return _join("-".join(tokens[:i]), tokens[i:])
    return "-".join(tokens)


def _join(key: str, words: list[str]) -> str:
    return f"{key} · {' '.join(words)}" if words else key


def _short_hex(token: str) -> str:
    return token[:8] if _HEX.match(token) else token


def guess_tool(path: str, tag: str) -> str:
    """Narzędzie z prefiksu gałęzi albo położenia, gdy nie znamy sesji: codex, claude albo pusty.

    Prefiks wygrywa z położeniem: okno „Nowe zadanie” zakłada worktrees obu narzędzi w
    `.claude/worktrees` repozytorium, a gałąź `codex/…` mówi wprost, kto w nim pracuje."""
    if tag in TOOLS:
        return TOOLS[tag]
    if "/.codex/worktrees/" in path:
        return "codex"
    if "/.claude/worktrees/" in path:
        return "claude"
    return ""


# --- nazwa gałęzi z opisu zadania (okno „Nowe zadanie”) -------------------------------------
#
# Odwrotność humanize_branch: z polecenia dla agenta robimy gałąź w tej samej konwencji co gałęzie
# agentów, które już znamy — prefiks narzędzia i słowa po myślnikach („codex/popraw-liczenie-vin”).
# humanize_branch czyta ją potem jako znacznik „codex” i tytuł „popraw-liczenie-vin”, a guess_tool
# rozpoznaje narzędzie po prefiksie.

# Polskie litery: ł NFKD nie rozkłada, więc wszystkie zamieniamy jedną tablicą.
_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
# Słowa funkcyjne, które nic nie mówią o zadaniu (po polsku i angielsku, już bez polskich znaków).
STOP_WORDS = {
    "a", "an", "the", "to", "of", "and", "or", "for", "in", "on", "at", "by", "with", "is", "are", "be",
    "please", "pls", "i", "w", "we", "z", "ze", "na", "do", "o", "od", "po", "oraz", "sie", "jak", "czy",
    "ten", "ta", "te", "tym", "tego", "tej", "jest", "sa", "prosze", "zeby", "aby", "ktory", "ktora",
    "ktore", "tak", "u", "dla", "przy", "nad", "pod", "za",
}
SLUG_WORDS = 6
SLUG_LENGTH = 48
FALLBACK_SLUG = "zadanie"


def slug_from_prompt(prompt: str, words: int = SLUG_WORDS, length: int = SLUG_LENGTH) -> str:
    """Krótki opis zadania do nazwy gałęzi i katalogu: małe litery ASCII, cyfry i myślniki.

    „Popraw liczenie VIN-ów w wyszukiwarce, proszę” → „popraw-liczenie-vin-ow-wyszukiwarce”.
    Pierwsze `words` znaczących słów, razem najwyżej `length` znaków; bez słów — FALLBACK_SLUG."""
    text = unicodedata.normalize("NFKD", (prompt or "").translate(_FOLD))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    meaningful = [t for t in tokens if t not in STOP_WORDS] or tokens
    out: list[str] = []
    for token in meaningful:
        token = token[:length]
        if out and len("-".join(out + [token])) > length:
            break
        out.append(token)
        if len(out) >= words:
            break
    return "-".join(out) or FALLBACK_SLUG


def branch_from_prompt(prompt: str, tool: str) -> str:
    """Gałąź dla nowego zadania: `<narzędzie>/<opis>`, np. „claude/popraw-liczenie-vin”."""
    return f"{TOOLS.get(tool, 'task')}/{slug_from_prompt(prompt)}"
