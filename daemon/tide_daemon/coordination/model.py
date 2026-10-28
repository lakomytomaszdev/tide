"""Model koordynacji: koordynator (sesja, która deleguje pracę), przebiegi podagentów i ich rundy.

- Run   — jeden podagent (Claude Code: agentId z subagents/agent-<id>.jsonl; Codex: wątek
          thread_spawn). Ma opis, typ (implementer, code-reviewer…), model i rundy.
- Round — jedno zlecenie dla podagenta: pierwsze uruchomienie albo kontynuacja (SendMessage,
          followup_task/send_message Codexa, nowa tura wątku). Kończy się wynikiem (raport
          podagenta), błędem albo zatrzymaniem; bez końca — trwa albo została przerwana.
- Coordinator — sesja z co najmniej jednym podagentem, pytania do użytkownika bez odpowiedzi.

Z modelu powstaje tablica ticketów (board.py). W pamięci trzymamy z rozmów tylko to, co tablica
pokazuje: opisy, początek polecenia (do szukania ID ticketu), wynik ostatnich rund (raport
podagenta, przycięty) i treść pytań bez odpowiedzi. Nic z tego nie trafia do dziennika usługi.
"""
from dataclasses import dataclass, field

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
KILLED = "killed"
INTERRUPTED = "interrupted"   # bez wyniku, a koordynator już nie działa
FINISHED = (COMPLETED, FAILED, KILLED, INTERRUPTED)

ROLE_IMPLEMENTER = "implementer"
ROLE_REVIEWER = "reviewer"
ROLE_HELPER = "helper"        # badanie, plan, eksploracja — nie zmienia stanu ticketu na „review”

RESULT_LIMIT = 24 * 1024      # tyle raportu podagenta trzymamy w pamięci (review, „Pokaż wynik”)
PROMPT_HEAD = 2400            # początek polecenia — do szukania ID ticketu


@dataclass
class Round:
    start: float
    kind: str = "launch"          # launch | continue
    status: str = RUNNING
    end: float = 0.0
    result: str = ""
    summary: str = ""
    duration_ms: int = 0
    note: str = ""                # np. krótki opis kontynuacji

    @property
    def finished(self) -> bool:
        return self.status in FINISHED


@dataclass
class Run:
    id: str
    tool: str
    description: str = ""
    agent_type: str = ""
    model: str = ""
    background: bool = False
    prompt_head: str = ""
    tool_use_id: str = ""
    nickname: str = ""
    branch: str = ""
    cwd: str = ""
    transcript: str = ""          # dziennik podagenta (do „Pokaż wynik” i ostatniej czynności)
    rounds: list = field(default_factory=list)
    last_action: str = ""
    last_ts: float = 0.0

    @property
    def current(self) -> Round | None:
        return self.rounds[-1] if self.rounds else None

    @property
    def state(self) -> str:
        current = self.current
        return current.status if current else RUNNING

    @property
    def start_ts(self) -> float:
        return self.rounds[0].start if self.rounds else 0.0

    @property
    def end_ts(self) -> float:
        current = self.current
        return current.end if current and current.finished else 0.0

    def begin(self, ts: float, kind: str = "launch", note: str = "") -> Round:
        current = self.current
        if current is not None and not current.finished:
            if kind == "continue":
                return current  # wiadomość do działającego podagenta — ta sama runda
            current.status, current.end = INTERRUPTED, ts
        round_ = Round(start=ts, kind=kind, note=note)
        self.rounds.append(round_)
        self.last_ts = max(self.last_ts, ts)
        return round_

    def finish(self, ts: float, status: str, result: str = "", summary: str = "", duration_ms: int = 0) -> None:
        current = self.current
        if current is None:
            current = self.begin(ts)
        elif current.finished and status == current.status and (not result or result == current.result):
            return  # to samo powiadomienie drugi raz (np. kolejka i wiadomość)
        elif current.finished:
            current = self.begin(ts, "continue")
        current.status = status
        current.end = ts
        if result:
            current.result = result[:RESULT_LIMIT]
        if summary:
            current.summary = summary[:400]
        if duration_ms:
            current.duration_ms = duration_ms
        self.last_ts = max(self.last_ts, ts)

    def deliver(self, ts: float, text: str) -> None:
        """Raport podagenta dostarczony osobną wiadomością (SubagentHandback) — do ostatniej rundy."""
        current = self.current or self.begin(ts)
        current.result = (text or "")[:RESULT_LIMIT]
        self.last_ts = max(self.last_ts, ts)


@dataclass
class Question:
    id: str
    ts: float
    text: str


@dataclass
class Coordinator:
    id: str                       # claude:<sesja> albo codex:<wątek>
    tool: str
    session_id: str
    path: str
    cwd: str = ""
    launch_cwd: str = ""          # katalog startu (pierwszy cwd) — stały, także po `cd` agenta (PT-28)
    branch: str = ""
    title: str = ""
    model: str = ""
    first_ts: float = 0.0
    last_ts: float = 0.0
    runs: dict = field(default_factory=dict)        # id → Run (kolejność dodania)
    questions: dict = field(default_factory=dict)   # id → Question (bez odpowiedzi)
    # Pliki planów, które koordynator sam zapisał (Write/Edit, apply_patch): nazwa pliku → (liczba zapisów,
    # czas ostatniego). Dowód, czyj jest plan bez `session` (plan.pick) — dwa plany w jednym repozytorium.
    plan_writes: dict = field(default_factory=dict)

    @property
    def spawned(self) -> int:
        return len(self.runs)


def add_plan_write(writes: dict, name: str, ts: float) -> None:
    """Jeden zapis pliku planu do `plan_writes` ({nazwa: (liczba zapisów, czas ostatniego)})."""
    count, last = writes.get(name, (0, 0.0))
    writes[name] = (count + 1, max(last, ts))
