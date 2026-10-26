"""Wspólny model sesji i wyprowadzanie stanu (working, waiting, idle, ended).

Parsery dzienników (claude.py, codex.py) sprowadzają wpisy do fazy tury:
- TURN_END     — agent skończył turę i czeka na odpowiedź,
- MID_TURN     — tura trwa (model myśli albo pisze),
- TOOL_PENDING — agent wywołał narzędzie i nie ma jeszcze wyniku (narzędzie działa
                 albo czeka na zgodę użytkownika).
Tutaj z fazy, czasu i informacji o procesach powstaje stan widoczny dla użytkownika.
"""
from dataclasses import dataclass

# Progi (sekundy). Opisane też w docs/tide-daemon.md.
IDLE_AFTER = 30 * 60        # czekająca sesja bez ruchu tyle czasu jest już „bezczynna”
STALL_AFTER = 30 * 60       # tura bez żadnego wpisu tyle czasu — agent raczej nie pracuje
APPROVAL_GRACE = 10         # tyle czekamy na wynik narzędzia, zanim uznamy, że czeka na zgodę
MAX_AGE = 24 * 60 * 60      # sesje nieruszane dłużej są pomijane

TURN_END = "turn_end"
MID_TURN = "mid_turn"
TOOL_PENDING = "tool_pending"

WORKING = "working"
WAITING = "waiting"
IDLE = "idle"
ENDED = "ended"


@dataclass
class Pending:
    """Wywołanie narzędzia bez wyniku."""
    name: str
    since: float
    asks_user: bool = False   # narzędzie z natury czeka na użytkownika (pytanie, plan)
    long_running: bool = False  # narzędzie z natury długie (podagent, czekanie)


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

    def feed(self, record: dict) -> None:
        raise NotImplementedError

    # Pomocnicze dla parserów.
    def touch(self, ts: float) -> None:
        if ts:
            if not self.first_activity:
                self.first_activity = ts
            self.last_activity = max(self.last_activity, ts)

    def set_phase(self, phase: str, ts: float) -> None:
        if phase != self.phase or not self.phase_since:
            self.phase = phase
            self.phase_since = ts or self.phase_since
        if phase != TOOL_PENDING:
            self.pending.clear()


def derive_state(log: SessionLog, now: float, alive: bool | None, status: Status | None,
                 work_evidence) -> tuple[str, str, float]:
    """Zwraca (stan, powód czekania, od kiedy).

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
            return WORKING, "background", status.since
        if status.status == "waiting":
            return WAITING, status.detail or "approval", status.since
        since = max(status.since, log.last_activity)
        return (IDLE if now - since > IDLE_AFTER else WAITING), "reply", since

    age = now - log.last_activity
    if log.phase == TURN_END:
        return (IDLE if age > IDLE_AFTER else WAITING), "reply", log.last_activity

    if log.phase == TOOL_PENDING and log.pending:
        call = max(log.pending.values(), key=lambda p: p.since)
        if call.asks_user:
            return WAITING, "question", call.since
        if call.long_running or not log.approvals_possible:
            return (IDLE if age > STALL_AFTER and not work_evidence(call.since) else WORKING), "", call.since
        if now - call.since < APPROVAL_GRACE or work_evidence(call.since):
            return WORKING, "", call.since
        return (IDLE if age > IDLE_AFTER else WAITING), "approval", call.since

    # Tura trwa: model myśli i nic nie zapisuje — długie milczenie to raczej przerwana sesja.
    if age > STALL_AFTER and not work_evidence(log.last_activity):
        return IDLE, "", log.last_activity
    return WORKING, "", log.phase_since or log.last_activity
