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
# Flagi, z którymi narzędzia z listy „tylko do czytania” (`commands.ALWAYS_ALLOWED`) piszą albo uruchamiają inny
# program: (litery krótkich flag — także w skupieniu `-ro plik`, `-oplik` — i długie flagi, także skrócone
# `--out=` i z wartością `--output=`). To nie są zakazy: polecenie z taką flagą jest tylko bez plakietki
# „tylko czyta” i bez domyślnego zaznaczenia.
WRITE_FLAGS = {
    "git branch": ("dDmMcCfu", ("--delete", "--move", "--copy", "--force", "--set-upstream-to", "--unset-upstream",
                                "--edit-description", "--track", "--no-track", "--create-reflog")),
    "git diff": ("", ("--output", "--ext-diff", "--textconv")),
    "git log": ("", ("--output", "--ext-diff", "--textconv")),
    "git show": ("", ("--output", "--ext-diff", "--textconv")),
    "git blame": ("", ("--textconv",)),
    "sort": ("o", ("--output", "--compress-program")),
    "rg": ("", ("--pre", "--pre-glob", "--hostname-bin", "--search-zip")),
    "tree": ("oR", ("--output",)),
    "file": ("Cm", ("--compile", "--magic-file")),
}
# Polecenia, które piszą przy argumencie pozycyjnym: `git branch nowa` zakłada gałąź (czyta tylko z `--list`/`-l`
# albo bez argumentów), `uniq wejście wyjście` zapisuje drugi argument.
POSITIONAL_WRITE = {"git branch": 0, "uniq": 1}
STEP_READERS = ("ls", "cat", "head", "tail", "wc", "stat", "file", "tree", "du", "realpath", "nl")
# Opakowania uruchamiające inne polecenie (`nohup make install`) i ich flagi z wartością w osobnym argumencie;
# liczba — argumenty pozycyjne przed poleceniem (`timeout 5 make install`).
WRAPPERS = {"env": ("uCS", 0), "command": ("", 0), "builtin": ("", 0), "exec": ("a", 0),
            "sudo": ("ugCDhprtU", 0), "doas": ("uC", 0), "nohup": ("", 0), "nice": ("n", 0),
            "timeout": ("sk", 1), "stdbuf": ("ioe", 0), "ionice": ("cnpPu", 0), "setsid": ("", 0),
            "xargs": ("InLPdEsa", 0), "time": ("fo", 0)}


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
    - `readOnly` — narzędzie z listy „tylko do czytania” (`commands.ALWAYS_ALLOWED`) bez flag zapisu albo
      uruchamiania innego programu (`WRITE_FLAGS`, `POSITIONAL_WRITE`),
    - `stepDir` — każdy argument polecenia (poza pojedynczą flagą) leży w katalogu kroku, co najmniej jeden,
    - opakowanie (`nohup`, `timeout`, `env`…) zabronionego polecenia nie ma `canAllow`; samo wewnętrzne polecenie
      jest na liście osobno jako `forbidden`,
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
        elif (hidden := _wrapped_forbidden(piece, denied)) is not None:
            # `nohup make install` — zakaz projektu dotyczy polecenia uruchamianego przez opakowanie; samo
            # wewnętrzne polecenie jest na liście osobno jako zabronione.
            inner, inner_rule = hidden
            reason = (f"Uruchamia polecenie „{inner}”, zabronione w projekcie regułą „{inner_rule}”." if inner_rule
                      else f"Uruchamia „{inner}”, które nie jest pojedynczym prostym poleceniem.")
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


def _wrapped_forbidden(piece: str, denied: list[str]) -> tuple[str, str] | None:
    """(polecenie wewnętrzne, reguła) — opakowanie uruchamia polecenie zabronione przez `is_forbidden`."""
    for inner in _unwrap(piece, 0)[1:]:
        if inner == piece:
            continue
        rule = commands.forbidding_rule(inner, denied)
        if rule is not None:
            return inner, rule
    return None


def own_step_read(command: str, step_dir: str) -> bool:
    """Polecenie tylko czyta własny `<krok>/in` albo `<krok>/out` (wszystkie ścieżki tam, co najmniej jedna)."""
    argv = _plain(command)
    if not argv or not step_dir or argv[0] not in STEP_READERS:
        return False
    roots = (os.path.join(step_dir, "in"), os.path.join(step_dir, "out"))
    paths = [a for a in argv[1:] if not a.startswith("-") and not a.isdigit()]
    # `tree -o …`, `file -C -m …` piszą — to nie jest odczyt, więc trafia na listę z flagami.
    return bool(paths) and all(_within(p, roots, "") for p in paths) and _read_only(command)


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
    if depth < MAX_DEPTH:
        for raw in _wrapped(piece):
            found.extend(_unwrap(raw, depth + 1))
    return found


def _strip_wrappers(argv: list[str]) -> tuple[list[str], list[str]]:
    """(polecenie bez opakowań `nohup`/`env`/`sudo`/`timeout`…, napisy z `env -S '…'` do osobnego podziału)."""
    scripts: list[str] = []
    argv = commands._without_locale(argv)
    while argv and os.path.basename(argv[0]) in WRAPPERS:
        valued, positional = WRAPPERS[os.path.basename(argv[0])]
        wrapper, argv = os.path.basename(argv[0]), argv[1:]
        while argv and (argv[0].startswith("-") or wrapper == "env" and "=" in argv[0]):
            arg, argv = argv[0], argv[1:]
            if arg == "--":
                break
            if arg.startswith("--") or "=" in arg and not arg.startswith("-"):
                if arg.startswith("--split-string"):
                    value = arg.split("=", 1)[1] if "=" in arg else (argv.pop(0) if argv else "")
                    scripts.append(value)
                elif arg.startswith("--") and "=" not in arg and argv:
                    # Długa flaga może brać wartość z następnego argumentu (`sudo --user ja make install`) —
                    # podział jest nadgorliwy, więc sprawdzane są oba odczyty.
                    scripts.append(shlex.join(argv[1 + positional:]) if len(argv) > 1 + positional else "")
                continue
            letters = arg[1:]
            if wrapper == "nice" and letters.isdigit():          # `nice -10 polecenie`
                continue
            for at, letter in enumerate(letters):
                if letter in valued:
                    value = letters[at + 1:] or (argv.pop(0) if argv else "")
                    if wrapper == "env" and letter == "S":
                        scripts.append(value)
                    break
        argv = argv[positional:]
    return argv, scripts


def _wrapped(piece: str) -> list[str]:
    """Polecenia uruchamiane przez opakowanie (`nohup make install` → `make install`); pusto bez opakowania."""
    try:
        argv = shlex.split(piece, posix=True)
    except ValueError:
        return []
    bare = commands._without_locale(argv)
    if not bare or os.path.basename(bare[0]) not in WRAPPERS:
        return []
    inner, scripts = _strip_wrappers(argv)
    found = [shlex.join(inner)] if inner else []
    for script in scripts:
        try:
            pieces: list[str] = []
            _level(script, 0, "", pieces, 1)
        except _Unbalanced:
            pieces = [script]
        found.extend(pieces)
    return found


def _script(piece: str) -> str | None:
    """Skrypt podany powłoce (`bash -c '…'`, `sh -lc "…"`, `eval …`), None — to nie jest wywołanie powłoki."""
    try:
        argv = shlex.split(piece, posix=True)
    except ValueError:
        return None
    argv, _ = _strip_wrappers(argv)
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
    """Narzędzie z `commands.ALWAYS_ALLOWED` bez flag zapisu/uruchamiania z `WRITE_FLAGS` i bez argumentu
    pozycyjnego, który by zapisywał (`POSITIONAL_WRITE`)."""
    argv = _plain(piece)
    if not argv or not commands.is_allowed(piece, []):
        return False
    for program in {*WRITE_FLAGS, *POSITIONAL_WRITE}:
        words = program.split()
        if argv[:len(words)] != words:
            continue
        args = argv[len(words):]
        short, long = WRITE_FLAGS.get(program, ("", ()))
        if any(_has_flag(a, short, long) for a in _options(args)):
            return False
        if program in POSITIONAL_WRITE:
            positional = [a for a in _positional(args)]
            if program == "git branch" and any(a in ("-l", "--list") or a.startswith("--list=") for a in args):
                continue
            if len(positional) > POSITIONAL_WRITE[program]:
                return False
    return True


def _options(args: list[str]) -> list[str]:
    out = []
    for arg in args:
        if arg == "--":
            break
        if arg.startswith("-") and arg != "-":
            out.append(arg)
    return out


def _positional(args: list[str]) -> list[str]:
    out, ended = [], False
    for arg in args:
        if not ended and arg == "--":
            ended = True
        elif ended or not arg.startswith("-") or arg == "-":
            out.append(arg)
    return out


def _has_flag(arg: str, short: str, long: tuple) -> bool:
    """Flaga z listy: długa także skrócona (`--out=…`, getopt przyjmuje jednoznaczny przedrostek), krótka także
    w skupieniu (`-ro plik`, `-oplik`)."""
    if arg.startswith("--"):
        name = arg[2:].split("=", 1)[0]
        return bool(name) and any(flag[2:].startswith(name) for flag in long)
    return any(letter in short for letter in arg[1:])


def _in_step_dir(piece: str, step_dir: str) -> bool:
    """Każdy argument polecenia (poza flagą bez wartości) leży w katalogu kroku, co najmniej jeden.

    Argument, którego nie da się uznać za ścieżkę w kroku (`build`, `wynik.txt`, wartość `-o plik`), odbiera flagę.
    Krótka flaga liczy się tylko jako pojedyncza litera (`-p`, `-r`); skupienie (`-rf`, `-oplik`) może nieść
    wartość, więc też odbiera flagę. Wartość długiej flagi (`--output=…`) musi leżeć w kroku."""
    argv = _plain(piece)
    if not argv or not step_dir:
        return False
    paths, ended = [], False
    for arg in argv[1:]:
        if not ended and arg == "--":
            ended = True
            continue
        if not ended and arg.startswith("--"):
            if "=" in arg:
                paths.append(arg.split("=", 1)[1])
            continue
        if not ended and arg.startswith("-") and arg != "-":
            if len(arg) == 2 and arg[1].isalpha():
                continue
            return False
        paths.append(arg)
    return bool(paths) and all(_within(p, (step_dir,), "") for p in paths)


def _within(path: str, roots: tuple, base: str) -> bool:
    if path.startswith("~") or not os.path.isabs(path) and not base:
        return False
    full = os.path.normpath(os.path.join(base, path) if base else path)
    return any(full == os.path.normpath(r) or full.startswith(os.path.normpath(r) + os.sep) for r in roots)
