"""Parser diffu jednolitego (`git diff`) jako czysta funkcja: tekst → pliki, hunki i sumy.

Bez uruchamiania gita, plików, D-Bus i UI. Kształt wyniku jest częścią przyszłego kontraktu `DiffData`
(#23), dlatego zmiany po scaleniu mogą być tylko addytywne:

    {"files": [{"path", "oldPath", "status", "additions", "deletions", "binary",
                "oldMode", "newMode", "warnings": [...],
                "hunks": [{"oldStart", "oldLines", "newStart", "newLines", "header",
                           "lines": [{"kind", "oldNo", "newNo", "text"}]}]}],
     "totals": {"files", "additions", "deletions"},
     "warnings": [...]}

Ustalenia:
- `status`: A dodany, D usunięty, R przemianowany, C skopiowany, T zmiana trybu (stary i nowy tryb różne,
  bez A/D/R/C), M reszta. R i C mają pierwszeństwo przed T.
- `oldPath` jest ustawione tylko dla R i C (poza tym None); `path` to ścieżka po zmianie (dla D: usunięta).
- `oldNo` jest None dla linii `+`, `newNo` dla linii `-`. Linia `\\ No newline at end of file` nie jest
  linią treści i jest pomijana.
- `additions`/`deletions` to liczba faktycznie sparsowanych linii `+`/`-`.
- Ścieżki w cudzysłowach gita (`\\303\\251`, `\\t`, `\\"`) są dekodowane do zwykłego tekstu UTF-8.
- Błędne wejście nie rzuca wyjątku: plik dostaje wpis w `warnings`, pozostałe pliki są parsowane;
  `warnings` najwyższego poziomu dotyczy tekstu przed pierwszym `diff --git`.
- Limity, `large` i przycinanie nie należą do parsera (#14).
"""
import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
_MODE = re.compile(r"^\d{6}$")


def parse_diff(text):
    """Zwraca słownik `files`/`totals`/`warnings`; nigdy nie rzuca wyjątku."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", "replace")
    text = text if isinstance(text, str) else ""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    top = []
    chunks = []
    for line in lines:
        if line.startswith("diff --git "):
            chunks.append([line])
        elif chunks:
            chunks[-1].append(line)
        elif line.strip():
            top.append(f"tekst przed pierwszym nagłówkiem diff: {_short(line)}")
    files = []
    for chunk in chunks:
        try:
            files.append(_parse_file(chunk))
        except Exception as exc:  # parser nie może wywrócić całego wyniku
            files.append(_failed(chunk[0], exc))
    return {"files": files,
            "totals": {"files": len(files),
                       "additions": sum(f["additions"] for f in files),
                       "deletions": sum(f["deletions"] for f in files)},
            "warnings": top}


def _short(line, limit=120):
    return line if len(line) <= limit else line[:limit] + "…"


def _failed(header, exc):
    path = ""
    try:
        path = _header_paths(header[len("diff --git "):])[1]
    except Exception:
        pass
    return {"path": path, "oldPath": None, "status": "M", "additions": 0, "deletions": 0,
            "binary": False, "oldMode": None, "newMode": None, "hunks": [],
            "warnings": [f"nie udało się sparsować pliku: {exc.__class__.__name__}"]}


def _unquote(token):
    """Dekoduje ścieżkę w cudzysłowach gita (bajty UTF-8 jako kody oktalne); inne zwraca bez zmian."""
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        return token
    body = token[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            octal = re.match(r"[0-7]{1,3}", body[i + 1:i + 4])
            if octal:
                out.append(int(octal.group(0), 8) & 0xFF)
                i += 1 + len(octal.group(0))
                continue
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                i += 2
                continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", "replace")


def _split_quoted(rest):
    """Pierwszy token (w cudzysłowie albo bez) i reszta za spacją."""
    if rest.startswith('"'):
        i = 1
        while i < len(rest):
            if rest[i] == "\\":
                i += 2
                continue
            if rest[i] == '"':
                return rest[:i + 1], rest[i + 2:]
            i += 1
        return rest, ""
    head, _, tail = rest.partition(" ")
    return head, tail


def _strip_prefix(path, prefix):
    return path[len(prefix):] if path.startswith(prefix) else path


def _header_paths(rest):
    """(stara, nowa) z `a/X b/Y`; spacje w nieocytowanych ścieżkach rozstrzyga środek albo ` b/`."""
    if rest.startswith('"'):
        a, tail = _split_quoted(rest)
        return _strip_prefix(_unquote(a), "a/"), _strip_prefix(_unquote(tail), "b/")
    if ' "' in rest:
        a, _, b = rest.partition(' "')
        return _strip_prefix(a, "a/"), _strip_prefix(_unquote('"' + b), "b/")
    if len(rest) % 2 == 1:
        half = len(rest) // 2
        if rest[half] == " " and rest[:half].startswith("a/") \
                and rest[half + 1:] == "b/" + rest[2:half]:
            return rest[2:half], rest[half + 3:]
    a, sep, b = rest.partition(" b/")
    if sep:
        return _strip_prefix(a, "a/"), b
    a, _, b = rest.partition(" ")
    return _strip_prefix(a, "a/"), _strip_prefix(b, "b/")


def _name(value):
    """Ścieżka z linii `---`/`+++`: None dla /dev/null, bez prefiksu a/ b/ i ogonka tabulatora."""
    value = value.rstrip("\r")
    value = _unquote(value) if value.startswith('"') else value.split("\t")[0]
    if value == "/dev/null":
        return None
    return value[2:] if value[:2] in ("a/", "b/") else value


def _bare(value):
    return _unquote(value) if value.startswith('"') else value


def _parse_file(chunk):
    old_h, new_h = _header_paths(chunk[0][len("diff --git "):])
    info = {"path": new_h, "oldPath": None, "status": "M", "additions": 0, "deletions": 0,
            "binary": False, "oldMode": None, "newMode": None, "hunks": [], "warnings": []}
    warn = info["warnings"]
    kind = None
    old_name = new_name = None
    saw_names = False
    rename_from = rename_to = None
    n = len(chunk)
    i = 1
    while i < n and not chunk[i].startswith("@@"):
        line = chunk[i]
        i += 1
        if line.startswith("--- "):
            old_name, saw_names = _name(line[4:]), True
        elif line.startswith("+++ "):
            new_name, saw_names = _name(line[4:]), True
        elif line.startswith("new file mode "):
            kind, info["newMode"] = "A", line.split()[-1]
        elif line.startswith("deleted file mode "):
            kind, info["oldMode"] = "D", line.split()[-1]
        elif line.startswith("old mode "):
            info["oldMode"] = line[9:].strip()
        elif line.startswith("new mode "):
            info["newMode"] = line[9:].strip()
        elif line.startswith("rename from "):
            rename_from = _bare(line[12:])
        elif line.startswith("rename to "):
            rename_to = _bare(line[10:])
        elif line.startswith("copy from "):
            rename_from, kind = _bare(line[10:]), "C"
        elif line.startswith("copy to "):
            rename_to, kind = _bare(line[8:]), "C"
        elif line.startswith(("similarity index ", "dissimilarity index ", "index ")):
            pass
        elif line.startswith("Binary files ") and line.rstrip().endswith(" differ"):
            info["binary"] = True
        elif line.startswith("GIT binary patch"):
            info["binary"] = True
            i = n  # reszta to dane literal/delta, nie linie diffu
        elif line.strip() == "":
            pass
        else:
            warn.append(f"nieznany nagłówek: {_short(line)}")
    if rename_from is not None and kind != "C":
        kind = "R"
    # Ścieżki: rename/copy, potem ---/+++, na końcu nagłówek diff --git.
    if kind in ("R", "C"):
        info["oldPath"] = rename_from
        info["path"] = rename_to if rename_to is not None else (new_name or new_h)
    elif kind == "D":
        info["path"] = old_name or old_h
    elif saw_names and (new_name or old_name):
        info["path"] = new_name or old_name
    if kind is None:
        if info["oldMode"] and info["newMode"] and info["oldMode"] != info["newMode"]:
            kind = "T"
        elif saw_names and old_name is None and new_name is not None:
            kind = "A"
        elif saw_names and new_name is None and old_name is not None:
            kind = "D"
    info["status"] = kind or "M"
    for mode in ("oldMode", "newMode"):
        if info[mode] is not None and not _MODE.match(info[mode]):
            warn.append(f"nieprawidłowy tryb: {_short(info[mode])}")
            info[mode] = None
    while i < n:
        line = chunk[i]
        m = _HUNK.match(line)
        if not m:
            if not line.startswith("\\") and line.strip():
                warn.append(f"nieoczekiwana linia poza hunkiem: {_short(line)}")
            i += 1
            continue
        i = _parse_hunk(chunk, i, m, info)
    return info


def _parse_hunk(chunk, i, m, info):
    old_start, new_start = int(m.group(1)), int(m.group(3))
    old_lines = 1 if m.group(2) is None else int(m.group(2))
    new_lines = 1 if m.group(4) is None else int(m.group(4))
    hunk = {"oldStart": old_start, "oldLines": old_lines, "newStart": new_start,
            "newLines": new_lines, "header": m.group(5), "lines": []}
    info["hunks"].append(hunk)
    old_no, new_no = old_start, new_start
    old_left, new_left = old_lines, new_lines
    n = len(chunk)
    i += 1
    while i < n and (old_left > 0 or new_left > 0 or chunk[i].startswith("\\")):
        line = chunk[i]
        mark = line[:1]
        if mark == "\\":
            i += 1
            continue
        if (mark == " " or line == "") and old_left > 0 and new_left > 0:
            hunk["lines"].append({"kind": " ", "oldNo": old_no, "newNo": new_no, "text": line[1:]})
            old_no, new_no, old_left, new_left = old_no + 1, new_no + 1, old_left - 1, new_left - 1
        elif mark == "-" and old_left > 0:
            hunk["lines"].append({"kind": "-", "oldNo": old_no, "newNo": None, "text": line[1:]})
            old_no, old_left = old_no + 1, old_left - 1
            info["deletions"] += 1
        elif mark == "+" and new_left > 0:
            hunk["lines"].append({"kind": "+", "oldNo": None, "newNo": new_no, "text": line[1:]})
            new_no, new_left = new_no + 1, new_left - 1
            info["additions"] += 1
        else:
            break
        i += 1
    if old_left > 0 or new_left > 0:
        info["warnings"].append(
            f"ucięty hunk @@ -{old_start},{old_lines} +{new_start},{new_lines} @@: "
            f"brakuje {old_left} linii starych i {new_left} nowych")
    return i
