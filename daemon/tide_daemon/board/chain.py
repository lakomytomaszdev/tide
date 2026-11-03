"""Łańcuch poleceń rozłożony na pojedyncze polecenia do zgody (K-35, PT-124).

Krok w tle prosi czasem o zgodę na cały łańcuch (`cd … && ls … | head; git log …`). Tide zatwierdza wyłącznie
pojedyncze polecenia, więc okno decyzji pokazuje łańcuch rozłożony: każde polecenie z flagami i powodem blokady.

Podział nie jest polityką: o zakazie decyduje wyłącznie `commands.is_forbidden` (projektowe `denied-commands`),
tu nie ma żadnych reguł wbudowanych. Podział jest celowo „nadgorliwy”: lepiej pokazać za dużo poleceń (np. treść
podpowłoki osobno i jeszcze raz w poleceniu zewnętrznym) niż ukryć zabronione. Polecenie zewnętrzne z `$(…)`,
z odwrotnym apostrofem, przekierowaniem albo wywołaniem powłoki i tak jest zabronione przez `is_forbidden`
(składnia złożona), a jego wnętrze trafia na listę osobno i jest sprawdzane tak samo.

Rozkładane są: `&&`, `||`, `|`, `|&`, `;`, `&`, nowa linia, grupy `( … )` i `{ …; }`, podpowłoki `$( … )`,
odwrotne apostrofy i `<( … )`/`>( … )` — także wewnątrz cudzysłowów podwójnych — oraz skrypt w `sh -c '…'`,
`bash -c "…"` i `eval …`. Pojedynczy apostrof jest dosłowny (bez podziału w środku), poza skryptem `sh -c`.
Nawias albo cudzysłów bez pary daje None — całość jest wtedy jednym poleceniem, którego nie da się zatwierdzić.
"""
from __future__ import annotations

import os
import shlex

from . import commands

MAX_DEPTH = 8
MAX_PIECES = 64
MAX_TEXT = 20000
SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "fish")
# Słowa powłoki przed właściwym poleceniem (`if make install; then …`) — zdejmowane, żeby polecenie było widać.
LEADING = ("{", "!", "if", "then", "else", "elif", "do", "while", "until", "time")
# Same zamknięcia konstrukcji, bez polecenia.
CLOSING = ("}", "fi", "done", "esac", "then", "else", "do")
# Konstrukcje, które nie są poleceniem do zatwierdzenia (nie są też zakazem projektu).
CONSTRUCTS = ("for", "case", "select", "function", "coproc", "[[", "((")
# Flagi, z którymi narzędzia z listy „tylko do czytania” coś zmieniają.
WRITE_FLAGS = {"git branch": ("-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force",
                              "-u", "--set-upstream-to", "--unset-upstream", "--edit-description"),
               "sort": ("-o", "--output")}
STEP_READERS = ("ls", "cat", "head", "tail", "wc", "stat", "file", "tree", "du", "realpath", "nl")


class _Unbalanced(ValueError):
    pass


def split(command: str) -> list[str] | None:
    """Pojedyncze polecenia łańcucha w kolejności wykonania (wnętrze podpowłoki przed poleceniem zewnętrznym).
    None — składnia bez pary albo łańcuch zbyt długi; takiego łańcucha nie da się rozłożyć."""
    text = str(command or "")
    if len(text) > MAX_TEXT or "\0" in text:
        return None
    out: list[str] = []
    try:
        _level(text, 0, "", out, 0)
    except _Unbalanced:
        return None
    pieces = []
    for raw in out:
        pieces.extend(_unwrap(raw, 0))
    if len(pieces) > MAX_PIECES:
        return None
    return pieces


def describe(command: str, *, denied: list[str], step_dir: str = "") -> list[dict]:
    """`commands[]` decyzji o zgodzie: `{text, readOnly, stepDir, forbidden, reason, canAllow, preselected}`.

    - `forbidden` — wyłącznie `commands.is_forbidden(text, denied)`; `reason` podaje regułę projektu
      (albo, bez reguły, że to nie jest pojedyncze proste polecenie),
    - `canAllow` — polecenie można dopisać do listy dozwolonych (niezabronione, do MAX_COMMAND znaków, nie jest
      konstrukcją powłoki); gdy nie — `reason` zawsze mówi dlaczego,
    - `readOnly` — narzędzie z listy „tylko do czytania” (`commands.ALWAYS_ALLOWED`) bez flag zapisu,
    - `stepDir` — wszystkie ścieżki polecenia leżą w katalogu kroku (`<krok>/in`, `<krok>/out`),
    - `preselected` — domyślnie zaznaczone w oknie: `canAllow` oraz `readOnly` albo `stepDir` (to zaznaczenie
      poleceń, nie możliwości decyzji — D10 dotyczy możliwości).
    Odczyt własnego `<krok>/in` i `<krok>/out` jest zawsze dozwolony i nie trafia na listę. Lista zależy tylko
    od polecenia i katalogu kroku (nie od reguł projektu), więc indeksy zgody z okna wskazują te same polecenia.
    Łańcucha, którego nie da się rozłożyć, nie da się zatwierdzić: wraca jako jedno polecenie z powodem."""
    text = str(command or "").strip()
    pieces = split(command)
    if pieces is None:
        return [{"text": text, "readOnly": False, "stepDir": False, "forbidden": commands.is_forbidden(text, denied),
                 "reason": "Łańcucha nie da się rozłożyć na pojedyncze polecenia (cudzysłów albo nawias bez pary "
                           "albo zbyt wiele poleceń), więc Tide nie może na nie zezwolić.",
                 "canAllow": False, "preselected": False}]
    items, seen = [], set()
    for piece in pieces:
        if piece in seen:
            continue
        seen.add(piece)
        if own_step_read(piece, step_dir):
            continue
        rule = commands.forbidding_rule(piece, denied)
        forbidden = rule is not None
        reason = ""
        if forbidden:
            reason = (f"Zabronione w projekcie regułą „{rule}”." if rule else
                      "To nie jest pojedyncze proste polecenie (podpowłoka, zmienna, przekierowanie albo wywołanie "
                      "powłoki), a Tide zatwierdza tylko takie.")
        elif _construct(piece):
            reason = "To konstrukcja powłoki, nie polecenie — Tide zatwierdza tylko pojedyncze polecenia."
        elif piece == "*" or piece.endswith(" *"):
            # Na liście dozwolonych końcowe „ *” znaczy „dowolne argumenty” — zgoda nie może się tak rozszerzyć.
            reason = "Polecenie kończy się „ *”, a na liście dozwolonych oznacza to dowolne argumenty."
        elif len(piece) > commands.MAX_COMMAND:
            reason = f"Polecenie ma ponad {commands.MAX_COMMAND} znaków, a Tide zatwierdza tylko krótsze."
        can_allow = not reason
        read_only = not forbidden and _read_only(piece)
        in_step = not forbidden and _in_step_dir(piece, step_dir)
        items.append({"text": piece, "readOnly": read_only, "stepDir": in_step, "forbidden": forbidden,
                      "reason": reason, "canAllow": can_allow, "preselected": can_allow and (read_only or in_step)})
    return items


def own_step_read(command: str, step_dir: str) -> bool:
    """Polecenie tylko czyta własny `<krok>/in` albo `<krok>/out` (wszystkie ścieżki tam, co najmniej jedna)."""
    argv = _plain(command)
    if not argv or not step_dir or argv[0] not in STEP_READERS:
        return False
    roots = (os.path.join(step_dir, "in"), os.path.join(step_dir, "out"))
    paths = [a for a in argv[1:] if not a.startswith("-") and not a.isdigit()]
    return bool(paths) and all(_within(p, roots, "") for p in paths)


# --- podział ---------------------------------------------------------------------------------------------------

def _level(text: str, i: int, stop: str, out: list, depth: int) -> int:
    """Czyta polecenia do `stop` (`)`, odwrotny apostrof albo koniec); wnętrza podpowłok trafiają do `out` od razu."""
    if depth > MAX_DEPTH:
        raise _Unbalanced("zbyt głęboko")
    buf: list[str] = []

    def flush():
        piece = "".join(buf).strip()
        buf.clear()
        if piece:
            out.append(piece)

    n = len(text)
    while i < n:
        c = text[i]
        if stop and c == stop:
            flush()
            return i + 1
        if c == "\\":
            if i + 1 < n and text[i + 1] == "\n":       # kontynuacja linii
                i += 2
                continue
            buf.append(text[i:i + 2])
            i += 2
            continue
        if c == "'":
            end = text.find("'", i + 1)
            if end < 0:
                raise _Unbalanced("apostrof")
            buf.append(text[i:end + 1])
            i = end + 1
            continue
        if c == '"':
            start = i
            i = _double(text, i + 1, out, depth)
            buf.append(text[start:i])
            continue
        if c == "$" and text.startswith("$(", i) or c in "<>" and text.startswith("(", i + 1):
            start = i
            i = _level(text, i + 2, ")", out, depth + 1)
            buf.append(text[start:i])
            continue
        if c == "`":
            start = i
            i = _level(text, i + 1, "`", out, depth + 1)
            buf.append(text[start:i])
            continue
        if c == "$" and text.startswith("${", i):
            end = text.find("}", i + 2)
            if end < 0:
                raise _Unbalanced("klamra")
            _double(text[i + 2:end] + '"', 0, out, depth)   # tylko podpowłoki z `${X:-$(…)}`
            buf.append(text[i:end + 1])
            i = end + 1
            continue
        if c == "(":
            flush()
            i = _level(text, i + 1, ")", out, depth + 1)
            continue
        if c == ")":
            raise _Unbalanced("nawias")
        if c == "&" and (text.startswith("&>", i) or buf and buf[-1].endswith((">", "<"))):
            buf.append(c)                               # `2>&1`, `&>plik` — przekierowanie, nie separator
            i += 1
            continue
        if c in ";|&\n\r":
            flush()
            i += 1
            while i < n and text[i] in ";|&":
                i += 1
            continue
        buf.append(c)
        i += 1
    if stop:
        raise _Unbalanced("brak zamknięcia")
    flush()
    return i


def _double(text: str, i: int, out: list, depth: int) -> int:
    """Wnętrze cudzysłowu podwójnego: `$( … )` i odwrotne apostrofy wykonują się także tutaj."""
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        if text.startswith("$(", i):
            i = _level(text, i + 2, ")", out, depth + 1)
            continue
        if c == "`":
            i = _level(text, i + 1, "`", out, depth + 1)
            continue
        i += 1
    raise _Unbalanced("cudzysłów")


def _unwrap(piece: str, depth: int) -> list[str]:
    """Polecenie bez słów powłoki na początku; skrypt `sh -c` i `eval` dochodzi jako osobne polecenia."""
    words = piece.split(None, 1)
    while words and words[0] in LEADING:
        piece = words[1].strip() if len(words) > 1 else ""
        words = piece.split(None, 1)
    if not piece or piece in CLOSING:
        return []
    found = [piece]
    script = _script(piece)
    if script is not None and depth < MAX_DEPTH:
        inner: list[str] = []
        try:
            _level(script, 0, "", inner, depth + 1)
        except _Unbalanced:
            inner = [script]
        for raw in inner:
            found.extend(_unwrap(raw, depth + 1))
    return found


def _script(piece: str) -> str | None:
    """Skrypt podany powłoce (`bash -c '…'`, `sh -lc "…"`, `eval …`), None — to nie jest wywołanie powłoki."""
    try:
        argv = shlex.split(piece, posix=True)
    except ValueError:
        return None
    argv = commands._without_locale(argv)
    while argv and argv[0] in ("env", "command", "builtin", "exec", "sudo", "doas", "nohup", "nice"):
        argv = [a for a in argv[1:]]
        while argv and (argv[0].startswith("-") or "=" in argv[0]):
            argv = argv[1:]
    if not argv:
        return None
    program = os.path.basename(argv[0])
    if program == "eval":
        return " ".join(argv[1:])
    if program in SHELLS:
        for at, arg in enumerate(argv[1:], 1):
            if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
                return argv[at + 1] if at + 1 < len(argv) else ""
    return None


# --- flagi ----------------------------------------------------------------------------------------------------

def _plain(command: str) -> list[str] | None:
    """Argumenty prostego polecenia bez ustawień języka; None dla składni złożonej."""
    if not commands.single_command(command):
        return None
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return None
    return commands._without_locale(argv) or None


def _construct(piece: str) -> bool:
    word = piece.split(None, 1)[0] if piece.split() else ""
    return word in CONSTRUCTS or word.endswith("()")


def _read_only(piece: str) -> bool:
    argv = _plain(piece)
    if not argv or not commands.is_allowed(piece, []):
        return False
    for program, flags in WRITE_FLAGS.items():
        words = program.split()
        if argv[:len(words)] == words and any(a in flags or any(a.startswith(f + "=") for f in flags if
                                                                 f.startswith("--")) for a in argv[len(words):]):
            return False
    return True


def _in_step_dir(piece: str, step_dir: str) -> bool:
    argv = _plain(piece)
    if not argv or not step_dir:
        return False
    paths = [a.split("=", 1)[1] if a.startswith("--") and "=" in a else a for a in argv[1:]]
    paths = [p for p in paths if "/" in p or p.startswith(("~", "."))]
    return bool(paths) and all(_within(p, (step_dir,), "") for p in paths)


def _within(path: str, roots: tuple, base: str) -> bool:
    if path.startswith("~") or not os.path.isabs(path) and not base:
        return False
    full = os.path.normpath(os.path.join(base, path) if base else path)
    return any(full == os.path.normpath(r) or full.startswith(os.path.normpath(r) + os.sep) for r in roots)
