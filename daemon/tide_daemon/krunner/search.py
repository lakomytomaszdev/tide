"""Dopasowanie zapytania KRunnera do sesji, projektów i worktrees — czyste funkcje, bez D-Bus.

Zapytanie dzielimy na słowa; każde musi trafić w któreś pole (np. „tide login” znajdzie sesję
w projekcie plasma-tide z tytułem o logowaniu). Porównanie bez wielkości liter i polskich
znaków. Trafienie w pole ma jakość: całe pole > początek pola > początek słowa > dowolne
miejsce, mnożoną przez wagę pola. Wynik to średnia po słowach (0..1).
"""
import re
import unicodedata

EXACT, PREFIX, WORD, INSIDE = 1.0, 0.85, 0.7, 0.5
_WORD_SPLIT = re.compile(r"[^0-9a-z]+")


def fold(text: str) -> str:
    """Małe litery bez znaków diakrytycznych: „Łódź” → „lodz”."""
    text = unicodedata.normalize("NFKD", str(text or "").casefold().replace("ł", "l"))
    return "".join(c for c in text if not unicodedata.combining(c))


def tokens(query: str) -> list[str]:
    return [t for t in fold(query).split() if t]


def quality(token: str, field: str) -> float:
    """Jak dobrze słowo trafia w pole (oba już po fold()). 0 — wcale."""
    if not field or token not in field:
        return 0.0
    if field == token:
        return EXACT
    if field.startswith(token):
        return PREFIX
    if any(word.startswith(token) for word in _WORD_SPLIT.split(field) if word):
        return WORD
    return INSIDE


def score(words: list[str], fields: list[tuple[str, float]]) -> tuple[float, float] | None:
    """(średni wynik, najlepsza jakość trafienia) albo None, gdy któreś słowo nigdzie nie trafia.

    fields: lista (tekst po fold(), waga pola).
    """
    if not words:
        return None
    total, best = 0.0, 0.0
    for word in words:
        hits = [(quality(word, text) * weight, quality(word, text)) for text, weight in fields]
        value, q = max(hits, default=(0.0, 0.0))
        if value <= 0:
            return None
        total += value
        best = max(best, q)
    return total / len(words), best


def session_fields(entry: dict) -> list[tuple[str, float]]:
    cwd = entry.get("cwd") or ""
    return [
        (fold(entry.get("project")), 1.0),
        (fold(entry.get("branch")), 0.9),
        (fold(cwd.rstrip("/").rsplit("/", 1)[-1]), 0.9),
        (fold(entry.get("title")), 0.8),
        (fold(entry.get("tool")), 0.6),
    ]


def project_fields(item: dict) -> list[tuple[str, float]]:
    return [(fold(item.get("repo")), 1.0), (fold(item.get("name")), 0.9)]


def worktree_fields(item: dict) -> list[tuple[str, float]]:
    return [(fold(item.get("branch")), 1.0), (fold(item.get("name")), 0.9), (fold(item.get("repo")), 0.6)]


def recency(ts: float, now: float, horizon: float) -> float:
    """1 dla aktywności teraz, 0 dla aktywności sprzed `horizon` sekund i starszej."""
    if not ts or horizon <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (now - ts) / horizon))


def ago_text(ts: float, now: float) -> str:
    """Czas względny po polsku, tak jak w widżetach (widgets/lib/Theme.qml, agoText)."""
    if not ts:
        return ""
    s = max(0, now - ts)
    if s < 60:
        return "teraz"
    if s < 3600:
        return f"{int(s // 60)} min temu"
    if s < 86400:
        return f"{int(s // 3600)} h temu"
    days = int(s // 86400)
    if days == 1:
        return "wczoraj"
    if days < 14:
        return f"{days} dni temu"
    if days < 60:
        return f"{days // 7} tyg. temu"
    return f"{days // 30} mies. temu"
