"""Pytanie na końcu skończonej tury (PT-29): czy ostatnia wiadomość agenta pyta użytkownika.

Agent prowadzący grilling kończy turę zwykłym tekstem („Pytanie 10: skąd biorą się czat i alerty?”)
— bez AskUserQuestion. Taka sesja naprawdę czeka na odpowiedź, więc agents/state.py daje jej stan
`waiting` z powodem `reply` i tym pytaniem w `waitingText`, dopóki użytkownik nie napisze.

Heurystyka (`ending_question`): z tekstu usuwamy bloki kodu i tabele, bierzemy ostatni akapit,
który nie jest listą (lista odpowiedzi do wyboru pod pytaniem go nie zasłania), i jego ostatnie
zdania (najwyżej QUESTION_SENTENCES). Pytanie to zdanie kończące się „?” (także przed `**`, `)`,
cudzysłowem albo emoji), po którym jest najwyżej jedno zdanie — krótkie, albo dłuższe, gdy mówi, co wyniknie
z odpowiedzi („Wtedy…”, „Jeśli tak…”), albo gdy przed nim padły co najmniej dwa pytania (sesja Twitcha z 28.10:
„Czy taki podział pasuje? Czy bywa u Ciebie castowanie bez wydarzenia…? Wtedy Wydarzenie byłoby opcjonalne…”) — albo linia w stylu „Pytanie 10: …”,
„Czekam na twoją odpowiedź”, „Daj znać, czy…”, albo lista pytań pod zdaniem z dwukropkiem. Pytanie
retoryczne w środku podsumowania nie liczy się — patrzymy tylko na koniec wiadomości.

Dopisek pod pytaniem (sesja z 27.10: „**Mogę scalić i zainstalować?**”, a pod nim „…Mogę go przywrócić
razem z resztą, jeśli napiszesz „przywróć”.”): ostatni akapit bez pytania, który prosi o słowo od
użytkownika (`_INVITES`: „jeśli napiszesz / potwierdzisz / dasz znać”, „napisz „…””), nie zasłania pytania
z akapitu tuż nad nim; bez takiego pytania liczy się sam dopisek. Zwykła propozycja („Jeśli chcesz, mogę
też…”) prośbą o odpowiedź nie jest.
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
_INVITES = re.compile(r"\b(?:jeśli|jeżeli|gdy|kiedy)\s+(?:mi\s+)?(?:napiszesz|odpiszesz|potwierdzisz|dasz\s+znać)\b|"
                      r"\b(?:napisz|odpisz)\s+[„\"«']|\bif you (?:reply|confirm|say)\b", re.I)
_HEADING = re.compile(r"^\s*#")
_ABBREVIATION = re.compile(r"(?:^|[\s(„])(?:np|m\.in|tj|tzn|itp|itd|ok|wg|ew|por|e\.g|i\.e|cf)\.$", re.I)
# Zdanie po pytaniu, które mówi, co wyniknie z odpowiedzi — pytania nie unieważnia.
_CONSEQUENCE = re.compile(r"^(?:\*\*)?\s*(?:wtedy|wówczas|w takim razie|w przeciwnym razie|inaczej|jeśli|jeżeli|gdyby|"
                          r"then|if|otherwise)\b", re.I)


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
    found = _paragraph_question(last, limit)
    if found or tail_lists:
        return found
    window = _sentences(last)[-QUESTION_SENTENCES:]
    if any(_INVITES.search(s) for s in window):
        # Dopisek „…jeśli napiszesz „przywróć”.” pod pytaniem — pytanie z akapitu wyżej, inaczej sam dopisek.
        above = paragraphs[-2] if len(paragraphs) > 1 else ""
        earlier = _paragraph_question(above, limit) if above and not _HEADING.match(above) else ""
        return earlier or _clip(" ".join(window[-2:]), limit)
    return ""


# --- drugi stopień (PT-42): wątpliwe końcówki, które ocenia model (agents/classify.py) ---------------------------

# Czasowniki w trybie rozkazującym, od których zaczyna się prośba do użytkownika („uruchom ten select i wklej mi wynik”).
_IMPERATIVES = ("uruchom", "wklej", "wyślij", "prześlij", "podeślij", "sprawdź", "podaj", "napisz", "odpisz", "odpowiedz",
                "potwierdź", "wybierz", "zdecyduj", "daj", "otwórz", "skopiuj", "wykonaj", "odpal", "pokaż", "zaakceptuj",
                "zatwierdź", "przetestuj", "zrestartuj", "zainstaluj", "zrób", "przejrzyj", "przeczytaj", "powiedz",
                "wskaż", "ustaw", "wpisz", "kliknij", "zaloguj", "dopisz", "przekaż", "uzupełnij", "wróć", "zadzwoń")
_IMPERATIVE_START = re.compile(
    r"^(?:(?:krok|etap|step)\s*\d+\s*[:.)\-–]\s*)?(?:(?:proszę|teraz|następnie|potem|najpierw|wtedy|jeszcze)[\s,]+)?"
    r"(?:" + "|".join(_IMPERATIVES) + r")\b", re.I)
# Zwroty, które w dowolnym miejscu zdania oddają ruch użytkownikowi: prośba o wynik, decyzję albo wskazanie kroku
# („wklej”, „to Twoja decyzja”, „czeka na Ciebie”, „kliknij”, „jeśli wolisz inaczej”). Samo „Jeśli chcesz, mogę…” i „dam
# znać” tu nie należą — to propozycja opcjonalna i zapowiedź.
_ASK_ANYWHERE = re.compile(
    r"\b(?:daj (?:mi )?znać|wklej|prześlij|podeślij|odpisz|wolisz|wybierzesz|zdecydujesz|zdecyduj|wskaż|"
    r"kliknij|wpisz|zatwierdź|potrzebuję|"
    r"(?:twoja|twojej) (?:decyzj\w+|odpowiedź|odpowiedzi)|(?<!nie )czeka na (?:ciebie|twoją|twoje))\b", re.I)
_MARKUP = re.compile(r"[*_`]+")


def _end_window(text: str) -> list:
    """Zdania z końcówki wiadomości (bez kodu i tabel): ostatnie QUESTION_SENTENCES zdania ostatniego akapitu
    niebędącego listą oraz punkty listy pod nim (listy poleceń na końcu — „1. Uruchom X”)."""
    body = _FENCE.sub("\n", str(text or ""))
    lines = [line.rstrip() for line in body.splitlines() if not _TABLE_LINE.match(line)]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
    tail_lists = []
    while paragraphs and all(_LIST_LINE.match(line) for line in paragraphs[-1].splitlines() if line.strip()):
        tail_lists.insert(0, paragraphs.pop())
    window = _sentences(paragraphs[-1])[-QUESTION_SENTENCES:] if paragraphs else []
    for block in tail_lists[-2:]:
        window += [_LIST_LINE.sub("", line).strip() for line in block.splitlines() if line.strip()]
    return window


def doubtful(text: str) -> bool:
    """Czy końcówka wiadomości jest wątpliwa dla reguł: reguły nie widzą w niej pytania (`ending_question` puste),
    a jest w niej „?” (pytanie retoryczne albo bez ostatniego miejsca), polecenie w trybie rozkazującym z listy
    („uruchom ten select i wklej mi wynik”) albo „daj znać”. Wtedy decyduje model lokalny (agents/classify.py).
    Wiadomość, w której reguły widzą pytanie, nie jest wątpliwa — model jej nie ocenia. „Dam znać”, „Jeśli chcesz,
    mogę też…” i zwykły raport („Gotowe.”) też nie."""
    if ending_question(text):
        return False
    for sentence in _end_window(text):
        plain = " ".join(_MARKUP.sub("", sentence).split())
        if _is_question(plain) or _IMPERATIVE_START.match(plain.lstrip(" -•–")) or _ASK_ANYWHERE.search(plain):
            return True
    return False


def closing(text: str, limit: int = QUESTION_MAX) -> str:
    """Końcówka wiadomości do `waitingText`, gdy pytanie wskazał model, a nie reguła: ostatnie dwa zdania."""
    window = _end_window(text)
    return _clip(" ".join(window[-2:]), limit) if window else ""


def _sentences(paragraph: str) -> list:
    out = []
    for line in paragraph.splitlines():
        for part in _SENTENCE.split(line):
            part = part.strip()
            if not part:
                continue
            # „np.”, „m.in.”, „tj.” nie kończą zdania — fragment wraca do poprzedniego.
            if out and _ABBREVIATION.search(out[-1]):
                out[-1] += " " + part
            else:
                out.append(part)
    return out


def _paragraph_question(paragraph: str, limit: int) -> str:
    """Pytanie z końca akapitu (zdanie z „?”, „Pytanie N:”, „Czekam na odpowiedź”) albo pusty napis."""
    window = _sentences(paragraph)[-QUESTION_SENTENCES:]
    asked = [i for i, s in enumerate(window) if _is_question(s)]
    # Po pytaniu najwyżej jedno krótkie zdanie („Mogę też zrobić obie.”) — inaczej to pytanie retoryczne
    # w środku podsumowania („Co się zmieniło? Dodałem pole. Wszystko działa.”).
    after = window[asked[-1] + 1:] if asked else []
    if asked and (len(after) > 1 or any(len(a) > CLOSING_MAX for a in after)):
        # Jedno dłuższe zdanie zostaje, gdy mówi, co wyniknie z odpowiedzi, albo pod co najmniej dwoma pytaniami.
        if not (len(after) == 1 and (_CONSEQUENCE.match(after[0]) or len(asked) > 1)):
            asked = []
    if asked:
        start = asked[0]
        # „Pytanie 10:” w osobnej linii albo zdaniu tuż przed pytaniem — razem z nim.
        if start > 0 and _ASKS.match(window[start - 1]):
            start -= 1
        return _clip(" ".join(window[start:asked[-1] + 1]), limit)
    marker = next((line.strip() for line in paragraph.splitlines() if _ASKS.match(line.strip())), "")
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
