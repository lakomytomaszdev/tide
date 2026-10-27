"""Płaski frontmatter plików tablicy, czytany i zapisywany z zachowaniem tego, czego nie znamy.

Te same reguły co w planach koordynacji (coordination/plan.py): tylko pary `klucz: wartość`,
klucze bez rozróżniania wielkości liter, wartość w cudzysłowach albo bez, ` #` zaczyna komentarz
w wartości bez cudzysłowów. Bez biblioteki YAML.

Plik ręcznie edytowany ma przetrwać nasz zapis: zmieniamy tylko linie kluczy, które ustawiamy,
a nieznane klucze, komentarze, puste linie i treść pod frontmatterem zostają co do znaku.
Nowe klucze trafiają na koniec frontmattera.
"""
import re

# Wartości, które bez cudzysłowów czytalibyśmy inaczej (albo YAML by się na nich wyłożył).
_NEWLINES = re.compile(r"[ \t]*[\r\n]+[ \t]*")
_NEEDS_QUOTES = re.compile(r"(^\s|\s$|^[\"'#]|\s#|:\s|:$|^[-?!&*\[\]{}|>%@`,])")


class FrontmatterError(ValueError):
    """Plik bez poprawnego frontmattera (brak otwierającego albo zamykającego `---`)."""


class Document:
    """Frontmatter jako lista linii (z indeksem kluczy) plus treść pod nim."""

    def __init__(self, lines: list[str] | None = None, body: str = ""):
        self.lines = list(lines or [])
        self.body = body

    # --- odczyt --------------------------------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "Document":
        lines = text.lstrip("﻿").split("\n")
        if not lines or lines[0].strip() != "---":
            raise FrontmatterError("brak frontmattera (plik musi zaczynać się od linii ---)")
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return cls(lines[1:i], "\n".join(lines[i + 1:]))
        raise FrontmatterError("frontmatter bez zamykającej linii ---")

    def get(self, key: str, default: str = "") -> str:
        index = self._index(key)
        if index is None:
            return default
        return scalar(self.lines[index].partition(":")[2])

    def has(self, key: str) -> bool:
        return self._index(key) is not None

    def keys(self) -> list[str]:
        out = []
        for line in self.lines:
            key = _key_of(line)
            if key and key not in out:
                out.append(key)
        return out

    def as_dict(self) -> dict:
        return {key: self.get(key) for key in self.keys()}

    # --- zapis ---------------------------------------------------------------------------------

    def set(self, key: str, value) -> None:
        """Ustawia wartość: podmienia linię klucza w miejscu albo dopisuje nową na końcu."""
        line = f"{key}: {dump(value)}".rstrip()
        index = self._index(key)
        if index is None:
            self.lines.append(line)
        else:
            self.lines[index] = line

    def text(self) -> str:
        body = self.body
        return "---\n" + "".join(line + "\n" for line in self.lines) + "---\n" + body

    # --- wnętrze -------------------------------------------------------------------------------

    def _index(self, key: str) -> int | None:
        key = key.strip().lower()
        for i, line in enumerate(self.lines):
            if _key_of(line) == key:
                return i  # przy powtórzonym kluczu liczy się pierwszy (jak w odczycie planów)
        return None


def scalar(value: str) -> str:
    """Wartość z linii frontmattera (jak coordination/plan.py: _scalar)."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def dump(value) -> str:
    """Wartość do zapisu w jednej linii: bez znaków nowej linii, w cudzysłowach, gdy trzeba."""
    text = "" if value is None else str(value)
    text = _NEWLINES.sub(" ", text)
    if text and _NEEDS_QUOTES.search(text):
        # Nasz odczyt zdejmuje tylko zewnętrzne cudzysłowy — cudzysłów w środku zostaje bez zmian.
        return f"\"{text}\""
    return text


def _key_of(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    key, sep, _ = line.partition(":")
    return key.strip().lower() if sep else ""
