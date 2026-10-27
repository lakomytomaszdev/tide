"""Pytanie na końcu skończonej tury (PT-29): czy ostatnia wiadomość agenta pyta użytkownika.

Agent prowadzący grilling kończy turę zwykłym tekstem („Pytanie 10: skąd biorą się czat i alerty?”)
— bez AskUserQuestion. Taka sesja naprawdę czeka na odpowiedź, więc agents/state.py daje jej stan
`waiting` z powodem `reply` i tym pytaniem w `waitingText`, dopóki użytkownik nie napisze.

Heurystyka (`ending_question`): z tekstu usuwamy bloki kodu i tabele, bierzemy ostatni akapit,
który nie jest listą (lista odpowiedzi do wyboru pod pytaniem go nie zasłania), i jego ostatnie
zdania (najwyżej QUESTION_SENTENCES). Pytanie to zdanie kończące się „?” (także przed `**`, `)`,
cudzysłowem albo emoji), po którym jest najwyżej jedno krótkie zdanie, albo linia w stylu „Pytanie 10: …”,
„Czekam na twoją odpowiedź”, „Daj znać, czy…”, albo lista pytań pod zdaniem z dwukropkiem. Pytanie
retoryczne w środku podsumowania nie liczy się — patrzymy tylko na koniec wiadomości.
"""
import re

QUESTION_SENTENCES = 3
QUESTION_MAX = 240
CLOSING_MAX = 60     # krótkie zdanie po pytaniu, które go nie unieważnia

_FENCE = re.compile(r"```.*?(?:```|$)", re.S)
_TABLE_LINE = re.compile(r"^\s*\|")
_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[a-z][.)])\s+", re.I)
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
_TRAILING = " \t*_`'\"”“»«)]>~🙂😊🙏👍"
_ASKS = re.compile(r"^(?:\*\*)?\s*pytanie(?:\s+(?:nr\s*)?\d+)?\s*[:.]", re.I)
_WAITS = re.compile(r"\b(?:czekam na (?:twoj\w* |twoją |twoich |)(?:odpowied\w*|decyzj\w*|potwierdzeni\w*)|"
                    r"daj (?:mi )?znać, (?:czy|które|którą|jak)|"
                    r"waiting for your (?:answer|reply|decision))\b", re.I)


def ending_question(text: str, limit: int = QUESTION_MAX) -> str:
    """Pytanie z końca wiadomości agenta (jedno–dwa zdania, przycięte) albo pusty napis."""
    body = _FENCE.sub("\n", str(text or ""))
    lines = [line.rstrip() for line in body.splitlines() if not _TABLE_LINE.match(line)]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
    if not paragraphs:
        return ""
    # Ostatni akapit, który nie jest samą listą; lista tuż pod nim (warianty odpowiedzi) zostaje w kontekście.
    tail_lists = []
    while paragraphs and all(_LIST_LINE.match(line) for line in paragraphs[-1].splitlines() if line.strip()):
        tail_lists.insert(0, paragraphs.pop())
        if len(tail_lists) > 2:
            return ""
    if not paragraphs:
        return _from_list(tail_lists, limit)
    last = paragraphs[-1]
    if tail_lists and last.rstrip(_TRAILING).endswith(":"):
        return _from_list(tail_lists, limit)  # „Mam dwa pytania:” i lista pytań
    sentences = [s.strip() for line in last.splitlines() for s in _SENTENCE.split(line) if s.strip()]
    window = sentences[-QUESTION_SENTENCES:]
    asked = [i for i, s in enumerate(window) if _is_question(s)]
    # Po pytaniu najwyżej jedno krótkie zdanie („Mogę też zrobić obie.”) — inaczej to pytanie retoryczne
    # w środku podsumowania („Co się zmieniło? Dodałem pole. Wszystko działa.”).
    after = window[asked[-1] + 1:] if asked else []
    if asked and (len(after) > 1 or any(len(a) > CLOSING_MAX for a in after)):
        asked = []
    if asked:
        start = asked[0]
        # „Pytanie 10:” w osobnej linii albo zdaniu tuż przed pytaniem — razem z nim.
        if start > 0 and _ASKS.match(window[start - 1]):
            start -= 1
        return _clip(" ".join(window[start:asked[-1] + 1]), limit)
    marker = next((line.strip() for line in last.splitlines() if _ASKS.match(line.strip())), "")
    if marker:
        return _clip(marker, limit)
    if any(_WAITS.search(s) for s in window):
        return _clip(" ".join(window[-2:]), limit)
    return ""


def _from_list(lists: list, limit: int) -> str:
    """Wiadomość kończąca się samą listą pytań („1. Czy X?”, „2. Czy Y?”) — pierwsze z nich."""
    for block in lists:
        for line in block.splitlines():
            item = _LIST_LINE.sub("", line).strip()
            if _is_question(item):
                return _clip(item, limit)
    return ""


def _is_question(sentence: str) -> bool:
    return sentence.rstrip(_TRAILING).endswith("?")


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.replace("**", "").split()).rstrip(" 🙂😊🙏👍")
    return text if len(text) <= limit else text[: limit - 1] + "…"
