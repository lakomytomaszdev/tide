"""Kontrakt testów projektu: klucz `tests` profilu `projects/<slug>/box.yaml` (schemat `tide-box: 1`).

Ten sam plik czyta profil pudełka K-38 (`box/profile.py`, czytnik `yamlmini`), dlatego format jest wąski:
blok `tests:` na wcięciu 0, pod nim dwie spacje i klucze `changed` oraz `full` z napisami w cudzysłowach.
Moduł leży poza `box/` i nie zależy od kodu K-38. Funkcje działają na tekście pliku — odczyt i zapis
na dysku należą do `BoardStore`. Zapis podmienia albo dodaje wyłącznie blok `tests` najwyższego poziomu
(klucz na wcięciu 0 i jego wcięte linie); reszta pliku zostaje bajt w bajt.

To tylko format i zapis. Zatwierdzenie kontraktu (odcisk, pochodzenie zapisu) to osobna kartka.
"""
import json
import re

from . import commands

PROFILE_FILE = "box.yaml"
KEY = "tests"
FIELDS = ("changed", "full")
SCHEMA_LINE = "tide-box: 1"
COMMAND_MAX = 300
BASE_PARAM = "{base}"
GRANT_NOTE = ("polecenie zawiera `*` (albo nie jest pojedynczym poleceniem) — Tide nie nada mu automatycznej "
              "zgody; uruchomienie wymaga zgody w kroku")

_PARAM = re.compile(r"\{[^{}]*\}")
_TOP_KEY = re.compile(r"^tests[ \t]*:")
_NOT_STRING = re.compile(
    r"^(?:[-+]?(?:\d[\d_]*\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|0[xXoObB][0-9a-fA-F_]+|true|false|null|~|[-+]?\.(?:inf|nan))$",
    re.I)
_CHILD =re.compile(r"^([A-Za-z0-9_-]+)[ \t]*:(?:[ \t]+(.*)|)$")


def empty() -> dict:
    return {name: "" for name in FIELDS}


def validate_tests(data) -> dict:
    """Sprawdza (części) kontraktu: obiekt z kluczami `changed` i/lub `full`. Zwraca napisy po `strip`.
    Pusty napis jest poprawny (pole puste). ValueError z czytelnym komunikatem dla reszty."""
    if not isinstance(data, dict):
        raise ValueError("tests musi być obiektem {\"changed\": \"…\", \"full\": \"…\"}")
    unknown = [key for key in data if key not in FIELDS]
    if unknown:
        raise ValueError(f"tests: nieznane pole „{unknown[0]}”; dozwolone: {', '.join(FIELDS)}")
    out = {}
    for name in FIELDS:
        if name not in data:
            continue
        value = data[name]
        if not isinstance(value, str):
            raise ValueError(f"tests.{name} musi być napisem")
        if "\0" in value:
            raise ValueError(f"tests.{name} zawiera znak NUL")
        if "\n" in value or "\r" in value:
            raise ValueError(f"tests.{name} musi być jedną linią")
        value = value.strip()
        if len(value) > COMMAND_MAX:
            raise ValueError(f"tests.{name} ma {len(value)} znaków; najwyżej {COMMAND_MAX}")
        if value:
            params = _PARAM.findall(value)
            if name == "changed":
                if BASE_PARAM not in value:
                    raise ValueError(f"tests.changed musi zawierać parametr {BASE_PARAM} (punkt odniesienia zmian)")
                if any(p != BASE_PARAM for p in params):
                    raise ValueError(f"tests.changed dopuszcza tylko parametr {BASE_PARAM}")
            elif params:
                raise ValueError(f"tests.full nie przyjmuje parametrów w klamrach ({params[0]})")
        out[name] = value
    return out


def grant_note(command: str) -> str:
    """Jedyne źródło komunikatu o braku automatycznej zgody: polecenie z `*` albo niebędące pojedynczym
    poleceniem jest poprawnym kontraktem, ale Tide nie nada mu zgody. Puste dla poleceń literalnych."""
    command = str(command or "")
    if not command.strip():
        return ""
    if "*" in command or not commands.single_command(command):
        return GRANT_NOTE
    return ""


def render_block(tests: dict) -> str:
    """Blok `tests:` do pliku; pusty napis, gdy żadne pole nie jest ustawione."""
    lines = [f"  {name}: {json.dumps(tests[name], ensure_ascii=False)}" for name in FIELDS if tests.get(name)]
    return f"{KEY}:\n" + "\n".join(lines) + "\n" if lines else ""


def _blocks(lines: list) -> list:
    """Przedziały [od, do) bloków `tests` na wcięciu 0: linia klucza i wszystkie wcięte linie po niej
    (puste linie i komentarze bez wcięcia w środku też; takie na końcu zostają poza blokiem)."""
    out = []
    i = 0
    while i < len(lines):
        if not _TOP_KEY.match(lines[i]):
            i += 1
            continue
        end = i + 1
        k = i + 1
        while k < len(lines):
            line = lines[k]
            if line.strip() == "" or line[0] == "#":  # pusta linia albo komentarz bez wcięcia nie kończą mapy
                k += 1
            elif line[0] in " \t":
                k += 1
                end = k
            else:
                break
        out.append((i, end))
        i = end
    return out


def _strip_comment(text: str) -> str:
    out, quote, prev = [], "", " "
    for ch in text:
        if quote:
            out.append(ch)
            if ch == quote and prev != "\\":
                quote = ""
        elif ch in "\"'" and prev in " \t":
            quote = ch
            out.append(ch)
        elif ch == "#" and prev in " \t":
            break
        else:
            out.append(ch)
        prev = ch
    return "".join(out).rstrip()


def _scalar(text: str) -> str:
    text = text.strip()
    if text[:1] == '"':
        try:
            value = json.loads(text)
        except ValueError:
            raise ValueError("zły napis w cudzysłowie") from None
        if not isinstance(value, str):
            raise ValueError("oczekiwano napisu")
        return value
    if text[:1] == "'":
        out, k = [], 1
        while k < len(text):
            if text[k] == "'":
                if text[k + 1:k + 2] == "'":  # '' to apostrof w napisie
                    out.append("'")
                    k += 2
                    continue
                if text[k + 1:].strip():
                    raise ValueError(f"nadmiarowy tekst po napisie: {text[k + 1:].strip()!r}")
                return "".join(out)
            out.append(text[k])
            k += 1
        raise ValueError("niezamknięty apostrof")
    if not text or text[:1] in "[{&*!|>%@`" or text.startswith("- "):
        raise ValueError("oczekiwano napisu (najlepiej w cudzysłowie)")
    if _NOT_STRING.match(text):
        raise ValueError("oczekiwano napisu, a nie liczby, true/false ani null (użyj cudzysłowu)")
    return text


def load_tests(text) -> tuple:
    """(kontrakt, błąd) z tekstu `box.yaml` (None — brak pliku). Brak klucza: pusty kontrakt bez błędu.
    Uszkodzony blok (zły typ, zły YAML, nieznany klucz, naruszenie reguł): pusty kontrakt i opis błędu."""
    if not text:
        return empty(), ""
    lines = text.lstrip("﻿").splitlines()
    blocks = _blocks(lines)
    if not blocks:
        return empty(), ""
    if len(blocks) > 1:
        return empty(), "klucz tests powtórzony"
    start, end = blocks[0]
    try:
        if _strip_comment(lines[start].split(":", 1)[1]).strip():
            raise ValueError("tests musi być blokiem z kluczami changed i full, nie wartością w jednej linii")
        found = {}
        indent = None
        for number in range(start + 1, end):
            raw = _strip_comment(lines[number])
            if not raw.strip():
                continue
            if "\t" in raw[:len(raw) - len(raw.lstrip())]:
                raise ValueError("tabulator we wcięciu")
            width = len(raw) - len(raw.lstrip(" "))
            if indent is None:
                indent = width
            elif width != indent:
                raise ValueError(f"nieoczekiwane wcięcie w linii „{raw.strip()}”")
            match = _CHILD.match(raw.strip())
            if not match:
                raise ValueError(f"oczekiwano „klucz: wartość”, jest „{raw.strip()}”")
            name = match.group(1)
            if name not in FIELDS:
                raise ValueError(f"nieznany klucz „{name}”; dozwolone: {', '.join(FIELDS)}")
            if name in found:
                raise ValueError(f"powtórzony klucz „{name}”")
            found[name] = _scalar(match.group(2) or "")
        if not found:
            raise ValueError("pusty blok tests")
        return {**empty(), **validate_tests(found)}, ""
    except ValueError as exc:
        return empty(), f"tests: {exc}"


def save_tests(text, tests: dict) -> str:
    """Nowy tekst `box.yaml`: blok `tests` podmieniony (albo dodany na końcu, albo usunięty, gdy oba pola
    puste). Bez pliku (`text` None) zakłada go z `tide-box: 1`; bez pliku i bez poleceń — pusty napis.
    Reszta pliku, w tym komentarze i klucze K-38, zostaje bez zmian."""
    block = render_block(validate_tests({name: tests.get(name, "") for name in FIELDS}))
    if text is None:
        return f"{SCHEMA_LINE}\n{block}" if block else ""
    lines = text.splitlines(keepends=True)
    blocks = _blocks(lines)
    if not blocks:
        if not block:
            return text
        if not text:
            return f"{SCHEMA_LINE}\n{block}"
        return text + ("" if text.endswith("\n") else "\n") + block
    first = blocks[0][0]
    out = lines[:first] + ([block] if block else [])
    cursor = blocks[0][1]
    for start, end in blocks[1:]:  # powtórzone klucze: zostaje jeden, na miejscu pierwszego
        out += lines[cursor:start]
        cursor = end
    out += lines[cursor:]
    return "".join(out)
