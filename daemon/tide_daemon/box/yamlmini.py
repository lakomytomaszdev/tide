"""Podzbiór YAML na potrzeby profilu pudełka — bez biblioteki YAML (usługa jej nie zakłada).

Czyta: mapy i listy blokowe (wcięcie spacjami), listy i mapy w stylu przepływu (`[a, b]`, `{k: v}`),
napisy w cudzysłowach, liczby całkowite, `true`/`false`/`null`/`~`, komentarze `# …`.
Nie czyta (błąd `YamlError`): kotwice, tagi, bloki `|`/`>`, wiele dokumentów, tabulatory we wcięciu,
powtórzone klucze. Listy map obsługują m.in. opisy paczek danych karty.
"""
import json
import re

__all__ = ["YamlError", "loads", "dumps"]


class YamlError(ValueError):
    pass


_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:(?:\s+(.*)|)$")
_INT = re.compile(r"^-?\d+$")
_PLAIN_SAFE = re.compile(r"^[A-Za-z0-9_./+=\-][A-Za-z0-9_./@+=:\-]*$")
_RESERVED = {"true", "false", "null", "~", "yes", "no", "on", "off"}


def _quoted_end(text: str, start: int) -> int:
    """Indeks tuż za napisem zaczynającym się w `start` albo -1, gdy napis nie jest zamknięty w tej linii.

    W cudzysłowie ukośnik odwrotny zawsze ucieka następny znak (`"a\\\\"` kończy się po drugim ukośniku), w apostrofach
    nie ma ucieczek, a `''` to apostrof w napisie (PT-321: kanoniczne napisy JSON kontraktu testów K-69)."""
    quote = text[start]
    k = start + 1
    while k < len(text):
        ch = text[k]
        if quote == '"' and ch == "\\":
            k += 2
            continue
        if ch == quote:
            if quote == "'" and text[k + 1:k + 2] == "'":
                k += 2
                continue
            return k + 1
        k += 1
    return -1


def _strip_comment(text: str) -> str:
    """Linia bez komentarza `# …`; `#` wewnątrz napisu w cudzysłowie albo apostrofach nie jest komentarzem."""
    k = 0
    prev = " "
    while k < len(text):
        ch = text[k]
        if ch in "\"'" and prev in " \t[{,:":
            end = _quoted_end(text, k)
            if end < 0:
                return text.rstrip()   # niezamknięty napis: błąd zgłosi parser wartości
            prev = text[end - 1]
            k = end
            continue
        if ch == "#" and prev in " \t":
            return text[:k].rstrip()
        prev = ch
        k += 1
    return text.rstrip()


def _plain(text: str):
    low = text.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _INT.match(text):
        return int(text)
    if text[:1] in "&*!|>%@`" or text.startswith("- "):
        raise YamlError(f"nieobsługiwany zapis: {text!r}")
    return text


class _Flow:
    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def _ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def value(self):
        self._ws()
        if self.i >= len(self.s):
            raise YamlError("urwany zapis w nawiasach")
        ch = self.s[self.i]
        if ch == "[":
            return self._seq()
        if ch == "{":
            return self._map()
        return self._scalar(",]}")

    def _scalar(self, stops: str):
        self._ws()
        ch = self.s[self.i] if self.i < len(self.s) else ""
        if ch == '"':
            # Cały token rozpoznaje dekoder JSON, łącznie z jego końcem (końcowe ukośniki, PT-321).
            if _quoted_end(self.s, self.i) < 0:
                raise YamlError("niezamknięty cudzysłów")
            try:
                value, self.i = json.JSONDecoder().raw_decode(self.s, self.i)
            except ValueError as exc:
                raise YamlError(f"zły napis w cudzysłowie: {exc}") from None
            return value
        if ch == "'":
            self.i += 1
            buf = []
            while True:
                if self.i >= len(self.s):
                    raise YamlError("niezamknięty apostrof")
                c = self.s[self.i]
                if c == "'":
                    if self.s[self.i + 1:self.i + 2] == "'":
                        buf.append("'")
                        self.i += 2
                        continue
                    self.i += 1
                    return "".join(buf)
                buf.append(c)
                self.i += 1
        start = self.i
        while self.i < len(self.s) and self.s[self.i] not in stops:
            if self.s[self.i] == ":" and self.s[self.i + 1:self.i + 2] in (" ", ""):
                break
            self.i += 1
        raw = self.s[start:self.i].strip()
        if not raw:
            raise YamlError("pusta wartość w nawiasach")
        return _plain(raw)

    def _seq(self):
        self.i += 1
        items = []
        self._ws()
        if self.s[self.i:self.i + 1] == "]":
            self.i += 1
            return items
        while True:
            items.append(self.value())
            self._ws()
            c = self.s[self.i:self.i + 1]
            self.i += 1
            if c == "]":
                return items
            if c != ",":
                raise YamlError("oczekiwano , lub ] w liście")

    def _map(self):
        self.i += 1
        out = {}
        self._ws()
        if self.s[self.i:self.i + 1] == "}":
            self.i += 1
            return out
        while True:
            key = self._scalar(":,}")
            if not isinstance(key, str):
                key = str(key)
            self._ws()
            if self.s[self.i:self.i + 1] != ":":
                raise YamlError(f"brak : po kluczu {key!r}")
            self.i += 1
            if key in out:
                raise YamlError(f"powtórzony klucz {key!r}")
            out[key] = self.value()
            self._ws()
            c = self.s[self.i:self.i + 1]
            self.i += 1
            if c == "}":
                return out
            if c != ",":
                raise YamlError("oczekiwano , lub } w mapie")


def _scalar(text: str):
    text = text.strip()
    if text[:1] in "[{":
        flow = _Flow(text)
        val = flow.value()
        flow._ws()
        if flow.i != len(text):
            raise YamlError(f"nadmiarowy tekst po wartości: {text[flow.i:]!r}")
        return val
    if text[:1] in "\"'":
        flow = _Flow(text)
        val = flow._scalar("")
        flow._ws()
        if flow.i != len(text):
            raise YamlError(f"nadmiarowy tekst po napisie: {text[flow.i:]!r}")
        return val
    return _plain(text)


def loads(text: str):
    lines = []
    for number, raw in enumerate(text.lstrip("﻿").splitlines(), 1):
        body = _strip_comment(raw)
        if not body.strip():
            continue
        if raw.strip() in ("---", "...") and not lines:
            continue
        indent = len(body) - len(body.lstrip(" "))
        if body[indent:indent + 1] == "\t":
            raise YamlError(f"wiersz {number}: tabulator we wcięciu")
        lines.append((indent, body.strip(), number))
    if not lines:
        return {}
    value, pos = _block(lines, 0, lines[0][0])
    if pos != len(lines):
        raise YamlError(f"wiersz {lines[pos][2]}: nieoczekiwane wcięcie")
    return value


def _block(lines, pos, indent):
    if lines[pos][1] == "-" or lines[pos][1].startswith("- "):
        return _list(lines, pos, indent)
    return _map(lines, pos, indent)


def _list(lines, pos, indent):
    items = []
    while pos < len(lines) and lines[pos][0] == indent and (lines[pos][1] == "-" or lines[pos][1].startswith("- ")):
        rest = lines[pos][1][1:].strip()
        number = lines[pos][2]
        if not rest:
            raise YamlError(f"wiersz {number}: pusty element listy")
        if _KEY.match(rest) and rest[:1] not in "[{\"'":
            # Mapa zaczyna się po `- `; kolejne klucze mają wcięcie indent + 2.
            start = pos
            pos += 1
            while pos < len(lines) and lines[pos][0] > indent:
                pos += 1
            block = [(indent + 2, rest, number), *lines[start + 1:pos]]
            value, end = _map(block, 0, indent + 2)
            if end != len(block):
                raise YamlError(f"wiersz {number}: niepoprawna mapa listy")
            items.append(value)
            continue
        items.append(_scalar(rest))
        pos += 1
    return items, pos


def _map(lines, pos, indent):
    out = {}
    while pos < len(lines) and lines[pos][0] == indent:
        text, number = lines[pos][1], lines[pos][2]
        match = _KEY.match(text)
        if not match:
            raise YamlError(f"wiersz {number}: oczekiwano `klucz: wartość`, jest {text!r}")
        key, rest = match.group(1), (match.group(2) or "").strip()
        if key in out:
            raise YamlError(f"wiersz {number}: powtórzony klucz {key!r}")
        pos += 1
        if rest:
            if rest[:1] in "|>":
                raise YamlError(f"wiersz {number}: bloki tekstu | i > nie są obsługiwane")
            out[key] = _scalar(rest)
            continue
        nxt = lines[pos] if pos < len(lines) else None
        if nxt and nxt[0] > indent:
            out[key], pos = _block(lines, pos, nxt[0])
        elif nxt and nxt[0] == indent and (nxt[1] == "-" or nxt[1].startswith("- ")):
            out[key], pos = _list(lines, pos, indent)
        else:
            out[key] = None
    if pos < len(lines) and lines[pos][0] > indent:
        raise YamlError(f"wiersz {lines[pos][2]}: nieoczekiwane wcięcie")
    return out, pos


# ---------------------------------------------------------------- zapis

def _fmt_scalar(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if value and _PLAIN_SAFE.match(value) and value.lower() not in _RESERVED and not _INT.match(value) \
                and not value.endswith(":"):
            return value
        return json.dumps(value, ensure_ascii=False)
    raise YamlError(f"nieobsługiwany typ wartości: {type(value).__name__}")


def dumps(data, order=()) -> str:
    """Zapis blokowy; klucze mapy najwyżej w kolejności `order`, reszta alfabetycznie."""
    lines = []
    _emit(data, 0, lines, tuple(order))
    return "\n".join(lines) + "\n"


def _emit(data, indent, lines, order):
    pad = " " * indent
    if isinstance(data, dict):
        keys = [k for k in order if k in data] + sorted(k for k in data if k not in order)
        for key in keys:
            val = data[key]
            if isinstance(val, dict) and val:
                lines.append(f"{pad}{key}:")
                _emit(val, indent + 2, lines, ())
            elif isinstance(val, list) and val:
                lines.append(f"{pad}{key}:")
                for item in val:
                    if isinstance(item, dict) and item:
                        nested = []
                        _emit(item, indent + 4, nested, ())
                        lines.append(f"{pad}  - " + nested[0].lstrip())
                        lines.extend(nested[1:])
                        continue
                    if isinstance(item, (dict, list)):
                        raise YamlError("zagnieżdżone listy i mapy w liście nie są obsługiwane")
                    lines.append(f"{pad}  - {_fmt_scalar(item)}")
            elif isinstance(val, dict):
                lines.append(f"{pad}{key}: {{}}")
            elif isinstance(val, list):
                lines.append(f"{pad}{key}: []")
            else:
                lines.append(f"{pad}{key}: {_fmt_scalar(val)}")
    else:
        raise YamlError("korzeń dokumentu musi być mapą")
