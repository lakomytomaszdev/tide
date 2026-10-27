"""Wspólny model sesji i wyprowadzanie stanu (working, waiting, done, idle, ended).

Parsery dzienników (claude.py, codex.py) sprowadzają wpisy do fazy tury:
- TURN_END     — agent skończył turę (albo ją przerwano),
- MID_TURN     — tura trwa (model myśli albo pisze),
- TOOL_PENDING — agent wywołał narzędzie i nie ma jeszcze wyniku (narzędzie działa
                 albo czeka na zgodę użytkownika).
Tutaj z fazy, czasu i informacji o procesach powstaje stan widoczny dla użytkownika.

„Czeka” (waiting) znaczy wyłącznie, że agent stoi i nie ruszy bez użytkownika: zgoda na narzędzie,
pytanie (AskUserQuestion, ExitPlanMode, request_user_input), status „waiting” zgłoszony przez
Claude Code albo (PT-29) skończona tura, której ostatnia wiadomość pyta użytkownika (agents/question.py:
„Pytanie 10: …?”), a użytkownik jeszcze nie odpisał — powód `reply`. Zwykły koniec tury to „skończył”
(done) — agent oddał głos, ale niczego nie blokuje; tura zaczęta przez zaplanowane zadanie Codexa nie
czeka nawet z pytaniem (zadanie samo wyśle następną wiadomość).
"""
from dataclasses import dataclass

from .question import ending_question

# Progi (sekundy). Opisane też w docs/tide-daemon.md.
IDLE_AFTER = 30 * 60        # skończona albo czekająca sesja bez ruchu tyle czasu jest już „bezczynna”
STALL_AFTER = 30 * 60       # tura bez żadnego wpisu tyle czasu — agent raczej nie pracuje
APPROVAL_GRACE = 10         # tyle czekamy na wynik narzędzia, zanim uznamy, że czeka na zgodę
MAX_AGE = 24 * 60 * 60      # sesje nieruszane dłużej są pomijane

TURN_END = "turn_end"
MID_TURN = "mid_turn"
TOOL_PENDING = "tool_pending"

# Powody (pole reason): przy waiting — na co czeka, przy working/done/idle — dopowiedzenie.
REASON_APPROVAL = "approval"      # narzędzie czeka na zgodę
REASON_QUESTION = "question"      # agent zadał pytanie
REASON_BACKGROUND = "background"  # tura skończona, ale działa zadanie w tle, do którego agent wróci
REASON_SCHEDULED = "scheduled"    # ostatnią turę zaczęło zaplanowane zadanie (automatyzacja Codexa)
REASON_REPLY = "reply"            # tura skończona pytaniem w tekście, bez odpowiedzi użytkownika (PT-29)

WORKING = "working"
WAITING = "waiting"
DONE = "done"
IDLE = "idle"
ENDED = "ended"


@dataclass
class Pending:
    """Wywołanie narzędzia bez wyniku."""
    name: str
    since: float
    asks_user: bool = False   # narzędzie z natury czeka na użytkownika (pytanie, plan)
    long_running: bool = False  # narzędzie z natury długie (podagent, czekanie)
    text: str = ""            # na co czeka: treść pytania albo opis wywołania do zgody (waitingText)


@dataclass
class Status:
    """Stan zgłoszony przez sam program agenta (Claude Code: ~/.claude/sessions/<pid>.json)."""
    status: str               # busy | waiting | idle
    since: float
    detail: str = ""


class SessionLog:
    """Stan jednej sesji zbudowany z wpisów dziennika. Podklasy implementują feed()."""

    tool = ""

    def __init__(self, path: str):
        self.path = path
        self.session_id = ""
        self.cwd = ""
        # Katalog startu sesji — stały, także gdy agent potem wejdzie gdzie indziej (`cd`): Codex — cwd
        # z session_meta, Claude Code — pierwszy cwd wpisu zgodny z katalogiem projektu dziennika
        # (`projects/<zakodowany katalog startu>/`). Pusty, gdy dziennik tego nie mówi.
        self.launch_cwd = ""
        self.branch = ""
        self.title = ""
        self.first_activity = 0.0
        self.last_activity = 0.0
        self.phase = TURN_END
        self.phase_since = 0.0
        self.pending: dict[str, Pending] = {}
        self.last_action = ""
        self.approvals_possible = True  # Codex z approval_policy=never nigdy nie pyta
        self.hidden = False  # np. podagenci Codexa — nie są osobnymi sesjami
        self.scheduled = False  # bieżącą/ostatnią turę zaczęło zaplanowane zadanie, nie użytkownik
        self.last_user_ts = 0.0  # ostatnia wiadomość wpisana przez użytkownika (nie wynik narzędzia)
        # Ostatnia wiadomość tekstowa agenta, po której nie było już wywołania narzędzia, i jej czas —
        # z niej pytanie na końcu tury (reply_question). Parsery czyszczą ją przy wywołaniu narzędzia.
        self.last_text = ""
        self.last_text_ts = 0.0
        self._question = ("", "")  # pamięć: (tekst, pytanie)

    def feed(self, record: dict) -> None:
        raise NotImplementedError

    # Pomocnicze dla parserów.
    def touch(self, ts: float) -> None:
        if ts:
            if not self.first_activity:
                self.first_activity = ts
            self.last_activity = max(self.last_activity, ts)

    def said(self, text: str, ts: float) -> None:
        """Agent napisał wiadomość tekstową (pusta — wywołał narzędzie, wcześniejszy tekst nie kończy tury)."""
        self.last_text = text or ""
        self.last_text_ts = ts if text else 0.0

    def set_phase(self, phase: str, ts: float) -> None:
        if phase != self.phase or not self.phase_since:
            self.phase = phase
            self.phase_since = ts or self.phase_since
        if phase != TOOL_PENDING:
            self.pending.clear()


def reply_question(log: SessionLog) -> str:
    """Pytanie z ostatniej wiadomości agenta w skończonej turze, na które użytkownik jeszcze nie odpisał
    (PT-29), albo pusty napis. Liczy się tylko bieżąca tura: wiadomość użytkownika po pytaniu (także
    przerwanie) albo wywołanie narzędzia po nim je kasuje; tura zaczęta przez zaplanowane zadanie nie pyta."""
    text = getattr(log, "last_text", "")
    if not text or log.phase != TURN_END or getattr(log, "scheduled", False):
        return ""
    if getattr(log, "parent_id", "") or log.hidden:
        return ""  # podagent: raport dla koordynatora, nie pytanie do użytkownika — nikt mu nie odpisze
    if log.last_user_ts and log.last_user_ts >= log.last_text_ts:
        return ""
    cached = getattr(log, "_question", ("", ""))
    if cached[0] != text:
        cached = (text, ending_question(text))
        log._question = cached
    return cached[1]


def waiting_text(log: SessionLog) -> str:
    """Na co agent czeka teraz — ten sam wybór co derive_state: najstarsze pytanie asynchroniczne
    (Codex Desktop), inaczej najnowsze wywołanie bez wyniku (treść pytania AskUserQuestion,
    ExitPlanMode, request_user_input albo opis narzędzia czekającego na zgodę). Pusty napis, gdy
    dziennik nie mówi, na co (np. sam status „waiting” z pliku Claude Code)."""
    questions = getattr(log, "open_questions", None)
    if questions:
        oldest = min(questions, key=questions.get)
        return (getattr(log, "open_question_texts", None) or {}).get(oldest, "")
    if log.phase == TOOL_PENDING and log.pending:
        return max(log.pending.values(), key=lambda p: p.since).text
    return reply_question(log)


def finished(log: SessionLog, now: float, since: float) -> tuple[str, str, float]:
    """Tura skończona zwyczajnie: „skończył”, a po IDLE_AFTER bez ruchu — „bezczynna”. Tura skończona
    pytaniem bez odpowiedzi (reply_question) to „czeka” z powodem `reply` od chwili pytania — także po
    IDLE_AFTER, jak każde pytanie: agent nie ruszy, dopóki użytkownik nie odpisze."""
    if reply_question(log):
        return WAITING, REASON_REPLY, log.last_text_ts or since
    reason = REASON_SCHEDULED if log.scheduled else ""
    return (IDLE if now - since > IDLE_AFTER else DONE), reason, since


def derive_state(log: SessionLog, now: float, alive: bool | None, status: Status | None,
                 work_evidence) -> tuple[str, str, float]:
    """Zwraca (stan, powód, od kiedy).

    alive         — None, gdy nie wiemy nic o procesach; False, gdy procesu sesji już nie ma.
    status        — stan zgłoszony przez program agenta, jeśli jest.
    work_evidence — funkcja(ts) -> bool: czy proces agenta uruchomił coś po chwili ts.
    """
    if alive is False:
        return ENDED, "", log.last_activity

    if status is not None:
        if status.status == "busy":
            return WORKING, "", status.since
        if status.status == "shell":
            # Agent czeka na własne zadanie w tle, nie na użytkownika — wróci sam, gdy się skończy.
            return WORKING, REASON_BACKGROUND, status.since
        if status.status == "waiting":
            return WAITING, status.detail or REASON_APPROVAL, status.since
        # idle: Claude Code skończył turę i nic nie blokuje.
        return finished(log, now, max(status.since, log.last_activity))

    # Pytanie asynchroniczne (Codex Desktop) czeka na odpowiedź niezależnie od tego, czy tura trwa.
    questions = getattr(log, "open_questions", None)
    if questions:
        return WAITING, REASON_QUESTION, min(questions.values())

    age = now - log.last_activity
    if log.phase == TURN_END:
        return finished(log, now, log.last_activity)

    if log.phase == TOOL_PENDING and log.pending:
        call = max(log.pending.values(), key=lambda p: p.since)
        if call.asks_user:
            return WAITING, REASON_QUESTION, call.since
        if call.long_running or not log.approvals_possible:
            return (IDLE if age > STALL_AFTER and not work_evidence(call.since) else WORKING), "", call.since
        if now - call.since < APPROVAL_GRACE or work_evidence(call.since):
            return WORKING, "", call.since
        if age > IDLE_AFTER:
            return IDLE, "", call.since
        return WAITING, REASON_APPROVAL, call.since

    # Tura trwa: model myśli i nic nie zapisuje — długie milczenie to raczej przerwana sesja.
    if age > STALL_AFTER and not work_evidence(log.last_activity):
        return IDLE, "", log.last_activity
    return WORKING, "", log.phase_since or log.last_activity


def waits_for_user(session: dict) -> bool:
    """Czy sesja z listy /Agents stoi i czeka na użytkownika — ta sama definicja co widżet agentów,
    Meta+A, powiadomienia i tablica (kolumna „Czeka na mnie”): wyłącznie stan `waiting` (zgoda na
    narzędzie, pytanie, status „waiting” z Claude Code, a od PT-29 także `reply` — tura skończona pytaniem
    w tekście, bez odpowiedzi). Dawniej `waiting/reply` znaczyło każdy koniec tury (usługa sprzed stanu
    `done`) i było tu wykluczone; dziś usługa liczy je z bieżącej tury przy każdym odczycie dziennika, więc
    stary wpis nie może się już pojawić."""
    return isinstance(session, dict) and session.get("state") == WAITING
