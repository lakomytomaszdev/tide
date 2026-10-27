"""Wynik review podagenta: ile BLOCKER, MAJOR i NIT, pierwsze ważne znalezisko i werdykt.

Recenzenci piszą różnie (sprawdzone na prawdziwych raportach, bez ich treści w repozytorium):
„1 MAJOR, 2 NIT”, „są 4 problemy MAJOR”, „**BLOCKER:** brak.”, „Blockerów nie ma”, „**MAJOR-1: …**”,
„### MAJOR B (nowe): …”, „### Major” z punktami albo podnagłówkami, „Werdykt: ACCEPT WITH NITS”,
„REQUEST CHANGES”, a ponowne review wymienia zamknięte znaleziska („MAJOR 1 … zamknięte”).

Dla każdego poziomu bierzemy pierwsze dostępne źródło:
1. jawna liczba w zdaniu bez słów o zamknięciu („2 NIT”, „dwa ustalenia MAJOR”, „MAJOR: 1”,
   „BLOCKER: brak”, „nie ma żadnego BLOCKER ani MAJOR”),
2. oznaczone znaleziska („MAJOR-1”, „MAJOR 2”, „MAJOR B”, „M1.” pod nagłówkiem) w liniach,
   które nie mówią o zamknięciu — liczba różnych oznaczeń,
3. linie zaczynające się od poziomu („- MAJOR: …”, „**BLOCKER** …”),
4. punkty i podnagłówki pod nagłówkiem poziomu („### Major”).
Werdykt bierzemy z linii „Werdykt/Verdict”, a bez niej z całego tekstu: „REQUEST CHANGES”,
„CHANGES REQUIRED”, „do poprawy” → zmiany; „APPROVE”, „ACCEPT”, „LGTM” → akceptacja.
`ok` = brak BLOCKER i MAJOR i nie ma prośby o zmiany.

To heurystyka — tablica pokazuje liczby jako podpowiedź, a stan zawsze można nadpisać planem.
"""
import functools
import re

LEVELS = ("blocker", "major", "minor", "nit")
SHORT = {"b": "blocker", "m": "major", "n": "nit"}
_LEVEL = r"(BLOCKER|MAJOR|MINOR|NIT)(?:-?(?:S|A|Y|ÓW|OW|ÓWA|U|EM|AMI))?"
_NONE = r"(?:none|brak|nie ma|nothing|n/?a|0|zero)\b"
_B = r"(?:\*\*|__)?"
NUMERALS = {"no": 0, "zero": 0, "bez": 0, "none": 0, "brak": 0, "jeden": 1, "jedno": 1, "jedna": 1, "one": 1,
            "dwa": 2, "dwie": 2, "two": 2, "oba": 2, "obie": 2, "trzy": 3, "three": 3, "cztery": 4, "four": 4,
            "pięć": 5, "five": 5, "sześć": 6, "six": 6}
_NUM = r"(\d{1,3}|" + "|".join(sorted(NUMERALS, key=len, reverse=True)) + r")"
COUNT_BEFORE = re.compile(rf"(?<![\w-]){_NUM}\s+(?:[^\W\d]+\s+){{0,2}}?{_B}{_LEVEL}\b", re.I)
COUNT_AFTER = re.compile(rf"\b{_LEVEL}{_B}\s*(?:[:=]|\(|×)\s*{_B}\s*(\d{{1,3}}|none|brak|zero|nie ma)\b", re.I)
ZERO_LEAD = re.compile(r"(?:żadn\w*|\bno\b|nie ma|nie znalazłem|brak\w*|\bbez\b|\bnone\b|nie wprowadza)", re.I)
ZERO_AFTER = re.compile(rf"\b{_LEVEL}\s+(?:nie ma|brak)\b", re.I)
LEVEL_WORD = re.compile(rf"\b{_LEVEL}\b", re.I)
TAGGED = re.compile(r"\b(BLOCKER|MAJOR|MINOR|NIT)(?:[-_ ](\d{1,2}|[A-H])\b)", re.I)
SHORT_TAG = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*(?:\*\*|#{1,6}\s*)?([BMN])-?(\d{1,2})[.:)]", re.I)
FINDING = re.compile(rf"^\s*(?:[-*+]|\d+[.)])?\s*(?:\*\*|__|\[|`)?\s*{_LEVEL}\b\s*(?:\*\*|__|\]|`)?\s*(.*)$", re.I)
HEADING = re.compile(rf"^\s*(#{{1,6}})\s*{_LEVEL}\b(.*)$", re.I)
ANY_HEADING = re.compile(r"^\s*(#{1,6})\s+\S")
BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+(\S.*)$")
CLOSED = re.compile(r"zamknięt|zamkniet|closed|resolved|fixed|naprawion|poprawion|zrobion|z poprzedni|previous|"
                    r"poprzedniej rundy|już nie|no longer|addressed", re.I)
CHANGES = re.compile(r"request(?:ed)?[ _-]changes|changes[ _-](?:requested|required)|do poprawy|wymaga poprawek|"
                     r"not approved|odrzucam|\breject\b|verdict\W+fail", re.I)
APPROVE = re.compile(r"\bapprove[ds]?\b|\baccept(?:ed)?\b|\blgtm\b|zatwierdzam|akceptuj[eę]|verdict\W+(?:pass|ok)",
                     re.I)
VERDICT_LINE = re.compile(r"werdykt|verdict", re.I)
FIX_WORDS = re.compile(r"\bfix(?:es|ed)?\b|poprawk|napraw|post-review|after review|po review", re.I)


def is_review(agent_type: str, description: str) -> bool:
    """Czy podagent jest recenzentem: typ (code-reviewer, renewal-reviewer) albo opis „Review …”.

    „LA-028 review fix round” to poprawki po review (implementer), nie review."""
    description = description or ""
    if re.search(r"review|recenz", agent_type or "", re.I):
        return True
    if FIX_WORDS.search(description):
        return False
    return bool(re.search(r"(?:^|[\s:·-])(?:re-?)?review\b|recenzj|przegląd kodu", description, re.I))


def parse(text: str) -> dict:
    """{"blocker", "major", "minor", "nit", "ok", "verdict", "top"} — top: pierwsze otwarte BLOCKER/MAJOR.

    Wynik dla tego samego tekstu jest zapamiętany (tablica przelicza się co kilkanaście sekund)."""
    return dict(_parse(text or ""))


@functools.lru_cache(maxsize=2048)
def _parse(text: str) -> dict:
    explicit = {level: None for level in LEVELS}
    for sentence in _sentences(text):
        if CLOSED.search(sentence):
            continue
        for match in COUNT_BEFORE.finditer(sentence):
            _max(explicit, match.group(2), _number(match.group(1)))
        for match in COUNT_AFTER.finditer(sentence):
            _max(explicit, match.group(1), _number(match.group(2)))
        for match in ZERO_AFTER.finditer(sentence):
            _max(explicit, match.group(1), 0)
        for lead in ZERO_LEAD.finditer(sentence):
            for word in LEVEL_WORD.finditer(sentence[lead.end():lead.end() + 48]):
                _max(explicit, word.group(1), 0)

    tagged = {level: set() for level in LEVELS}
    lines = {level: 0 for level in LEVELS}
    items = {level: 0 for level in LEVELS}
    top = ""
    heading, depth = "", 0
    for line in text.split("\n"):
        closed = bool(CLOSED.search(line))
        head = HEADING.match(line)
        if head:
            level = head.group(2).lower()
            rest = head.group(3) or ""
            tag = re.match(r"^[-_ ]?(\d{1,2}|[A-H])\b", rest)
            body = rest[tag.end():] if tag else rest
            body = body.strip(" :*—–-.()")
            if tag and not closed:
                tagged[level].add(tag.group(1).upper())
                top = top or _top(level, body)
                heading = ""
            elif not tag:
                heading, depth = (level, len(head.group(1))) if not re.match(_NONE, body, re.I) else ("", 0)
            continue
        any_head = ANY_HEADING.match(line)
        if any_head:
            if heading and len(any_head.group(1)) > depth:
                if not closed:
                    items[heading] += 1
                    top = top or _top(heading, line.lstrip("# "))
                continue
            heading = ""
        if not closed:
            for match in TAGGED.finditer(line):
                tagged[match.group(1).lower()].add(match.group(2).upper())
            short = SHORT_TAG.match(line)
            if short and heading and SHORT[short.group(1).lower()] == heading:
                tagged[heading].add(short.group(2))
        item = FINDING.match(line)
        if item:
            level = item.group(2 - 1).lower()
            body = re.sub(r"^[-_ ]?(?:\d{1,2}|[A-H])\b", "", item.group(2) or "").strip(" :*—–-.()")
            if body and not re.match(_NONE, body, re.I) and not closed and not COUNT_AFTER.match(line.strip(" -*")):
                lines[level] += 1
                top = top or _top(level, body)
            elif not body:
                heading, depth = level, 7  # „**NIT:**” z listą pod spodem — jak nagłówek
            continue
        bullet = BULLET.match(line)
        if heading and bullet and not closed and not re.match(_NONE, bullet.group(1), re.I):
            items[heading] += 1
            top = top or _top(heading, bullet.group(1))

    counts = {}
    for level in LEVELS:
        if explicit[level] is not None:
            counts[level] = explicit[level]
        elif tagged[level]:
            counts[level] = len(tagged[level])
        elif lines[level]:
            counts[level] = lines[level]
        else:
            counts[level] = items[level]
    verdict = _verdict(text)
    if verdict == "approve" and not (explicit["blocker"] or explicit["major"]):
        # Akceptacja bez jawnej liczby BLOCKER/MAJOR: oznaczenia w tekście to zwykle odniesienia
        # do zamkniętych znalezisk z poprzedniej rundy.
        counts["blocker"], counts["major"] = explicit["blocker"] or 0, explicit["major"] or 0
    ok = verdict != "changes" and counts["blocker"] == 0 and counts["major"] == 0
    if not top and (counts["blocker"] or counts["major"]):
        # Bez wyraźnego znaleziska: pierwsza linia z BLOCKER/MAJOR, która nie jest werdyktem ani zamknięciem.
        for line in text.split("\n"):
            word = LEVEL_WORD.search(line)
            if word and word.group(1).lower() in ("blocker", "major") and not VERDICT_LINE.search(line) \
                    and not CLOSED.search(line):
                top = line.strip(" -*#")
                break
    if not counts["blocker"] and not counts["major"]:
        top = ""
    return {**counts, "ok": ok, "verdict": verdict, "top": _clip(top, 240)}


def _verdict(text: str) -> str:
    for line in text.split("\n"):
        if VERDICT_LINE.search(line):
            if CHANGES.search(line):
                return "changes"
            if APPROVE.search(line):
                return "approve"
    return "changes" if CHANGES.search(text) else "approve" if APPROVE.search(text) else ""


def _sentences(text: str):
    for line in text.split("\n"):
        for part in re.split(r"(?<=[.;!?])\s+", line):
            if part.strip():
                yield part


def _top(level: str, body: str) -> str:
    if level not in ("blocker", "major"):
        return ""
    body = (body or "").strip(" :*—–-.")
    return f"{level.upper()}: {body}" if body else ""


def _max(explicit: dict, level: str, value) -> None:
    level = (level or "").lower()
    if value is None or level not in explicit:
        return
    current = explicit[level]
    explicit[level] = value if current is None else max(current, value)


def _number(text: str):
    text = (text or "").lower()
    if text in NUMERALS:
        return NUMERALS[text]
    if text == "nie ma":
        return 0
    try:
        return int(text)
    except ValueError:
        return None


def summary_text(review: dict) -> str:
    """„1 MAJOR, 2 NIT” albo „bez uwag”."""
    parts = [f"{review[level]} {level.upper()}" for level in ("blocker", "major", "nit") if review.get(level)]
    return ", ".join(parts) if parts else "bez uwag"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Polska odmiana po liczbie: 1 uwaga, 2 uwagi, 5 uwag, 22 uwagi, 12 uwag."""
    n = abs(n)
    if n == 1:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def human_text(review: dict) -> str:
    """Wynik review po ludzku: „1 poważna uwaga i 8 drobnych”, „2 błędy blokujące”, „bez uwag”."""
    words = (("blocker", ("błąd blokujący", "błędy blokujące", "błędów blokujących")),
             ("major", ("poważna uwaga", "poważne uwagi", "poważnych uwag")),
             ("nit", ("drobna uwaga", "drobne uwagi", "drobnych uwag")))
    parts = []
    for level, forms in words:
        n = review.get(level) or 0
        if n:
            form = plural(n, *forms)
            if parts and level == "nit":
                form = form.split()[0]  # „1 poważna uwaga i 8 drobnych” — bez powtarzania „uwag”
            parts.append(f"{n} {form}")
    if not parts:
        return "bez uwag"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " i " + parts[-1]


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.replace("**", "").replace("`", "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
