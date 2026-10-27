"""Koordynator Codexa: wątek główny i jego podagenci (wątki thread_spawn).

Co wiemy o strukturze (z obserwacji prawdziwych dzienników, format nie jest publiczny):
- pierwsza linia wątku podagenta to session_meta z `source.subagent.thread_spawn`:
  parent_thread_id, depth, agent_path („/root/la029_adapter” — nazwa zadania), agent_nickname,
  agent_role (default, worker, explorer, code-reviewer); `subagent.other = "guardian"` to
  recenzent zgód — szum, pomijamy,
- w wątku rodzica zlecenie to function_call `spawn_agent` (arguments: task_name, agent_type, model,
  message), a kontynuacje to `followup_task` i `send_message`; `request_user_input` bez wyniku
  to pytanie do użytkownika,
- każda tura wątku (także podagenta) to event_msg task_started → task_complete
  (`last_agent_message` to raport) albo turn_aborted.

Koordynator to wątek główny (bez `source.subagent`), który ma co najmniej jednego podagenta
(dowolnej głębokości — wnuki należą do tego samego koordynatora). Przebieg (Run) to wątek
podagenta, a runda to jego tura.
"""
import json
import os
import re

from ..agents.codex import describe_call
from ..agents.timeutil import parse_ts
from .model import COMPLETED, KILLED, PROMPT_HEAD, Coordinator, Question, Run

LINE_MARKERS = (b'"task_started"', b'"task_complete"', b'"turn_aborted"', b'"spawn_agent"',
                b'"request_user_input"', b'"function_call_output"', b'"user_message"', b'"turn_context"')
TIMESTAMP = re.compile(rb'"timestamp":\s*"([^"]+)"')
CALL_ID = re.compile(rb'"call_id":\s*"([^"]+)"')
CHUNK = 8 * 1024 * 1024


class CodexThreadLog:
    """Przyrostowy odczyt jednego wątku Codexa: metadane, tury, zlecenia podagentów i pytania."""

    def __init__(self, path: str):
        self.path = path
        self.offset = 0
        self.size = 0
        self.mtime = 0.0
        self._rest = b""
        self.meta_read = False
        self.id = ""
        self.cwd = ""
        self.branch = ""
        self.parent_id = ""
        self.hidden = False         # recenzent zgód i inne rodzaje bez rodzica
        self.spawn: dict = {}       # thread_spawn z session_meta
        self.first_ts = 0.0
        self.last_ts = 0.0
        self.model = ""
        self.turns: list[dict] = []  # {start, end, status, result}
        self.first_message = ""
        self.spawns: dict[str, dict] = {}        # task_name → {ts, agent_type, model, message}
        self._calls: dict[str, dict] = {}        # call_id → wywołanie request_user_input
        self.questions: dict[str, Question] = {}

    def read_meta(self) -> bool:
        """Sama pierwsza linia (session_meta) — tanio, do zbudowania drzewa wątków."""
        if self.meta_read:
            return True
        try:
            with open(self.path, "rb") as fh:
                line = fh.readline()
        except OSError:
            return False
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return False
        if isinstance(record, dict) and record.get("type") == "session_meta":
            self._meta(record.get("payload") or {})
        self.meta_read = True
        return True

    def update(self) -> bool:
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        if st.st_size < self.offset:
            self.__init__(self.path)
        if st.st_size == self.offset:
            return False
        self.size, self.mtime = st.st_size, st.st_mtime
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    lines = (self._rest + chunk).split(b"\n")
                    self._rest = lines.pop()
                    for line in lines:
                        self._line(line)
                self.offset = fh.tell()
        except OSError:
            return False
        return True

    def _line(self, line: bytes) -> None:
        if not line:
            return
        stamp = TIMESTAMP.search(line)
        if stamp:
            ts = parse_ts(stamp.group(1).decode("ascii", "replace"))
            self.first_ts = self.first_ts or ts
            self.last_ts = max(self.last_ts, ts)
        if not self.meta_read and b'"session_meta"' in line[:200]:
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                return
            if isinstance(record, dict) and record.get("type") == "session_meta":
                self._meta(record.get("payload") or {})
                self.meta_read = True
                return
        if not any(marker in line for marker in LINE_MARKERS):
            return
        if b'"function_call_output"' in line and not (self._calls and any(
                m.group(1).decode("ascii", "replace") in self._calls for m in CALL_ID.finditer(line))):
            return
        if b'"user_message"' in line and self.first_message:
            return
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(record, dict):
            self.feed(record)

    def feed(self, record: dict) -> None:
        kind = record.get("type")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        ts = parse_ts(record.get("timestamp"))
        if kind == "session_meta":
            self._meta(payload)
        elif kind == "turn_context":
            if payload.get("model"):
                self.model = str(payload["model"])
            if payload.get("cwd"):
                self.cwd = str(payload["cwd"])
        elif kind == "event_msg":
            event = payload.get("type")
            if event == "task_started":
                if self.turns and self.turns[-1]["status"] == "running":
                    self.turns[-1].update(status="interrupted", end=ts)
                self.turns.append({"start": ts, "end": 0.0, "status": "running", "result": ""})
            elif event in ("task_complete", "turn_aborted"):
                if not self.turns or self.turns[-1]["status"] != "running":
                    self.turns.append({"start": ts, "end": 0.0, "status": "running", "result": ""})
                turn = self.turns[-1]
                turn["end"] = ts
                turn["status"] = COMPLETED if event == "task_complete" else KILLED
                message = payload.get("last_agent_message")
                if isinstance(message, str):
                    turn["result"] = message[:24 * 1024]
            elif event == "user_message" and not self.first_message:
                message = payload.get("message")
                if isinstance(message, str):
                    self.first_message = message[:PROMPT_HEAD]
        elif kind == "response_item":
            item = payload.get("type")
            if item == "function_call":
                name = payload.get("name")
                args = _args(payload.get("arguments"))
                if name == "spawn_agent":
                    task = str(args.get("task_name") or "")
                    self.spawns[task or str(payload.get("call_id"))] = {
                        "ts": ts, "agent_type": str(args.get("agent_type") or ""), "model": str(args.get("model") or ""),
                        "message": str(args.get("message") or "")[:PROMPT_HEAD]}
                elif name == "request_user_input":
                    call_id = str(payload.get("call_id") or "")
                    self._calls[call_id] = {"ts": ts}
                    self.questions[call_id] = Question(call_id, ts, _question_text(args) or describe_call(name, payload))
            elif item == "function_call_output":
                call_id = str(payload.get("call_id") or "")
                if call_id in self._calls:
                    self._calls.pop(call_id, None)
                    self.questions.pop(call_id, None)

    def _meta(self, payload: dict) -> None:
        self.id = str(payload.get("id") or self.id)
        self.cwd = str(payload.get("cwd") or self.cwd)
        git = payload.get("git")
        if isinstance(git, dict) and git.get("branch"):
            self.branch = str(git["branch"])
        self.first_ts = self.first_ts or parse_ts(payload.get("timestamp"))
        source = payload.get("source")
        sub = source.get("subagent") if isinstance(source, dict) else None
        if isinstance(sub, dict):
            spawn = sub.get("thread_spawn")
            if isinstance(spawn, dict):
                self.spawn = spawn
                self.parent_id = str(spawn.get("parent_thread_id") or payload.get("parent_thread_id") or "")
            else:
                self.hidden = True  # guardian (recenzent zgód) i inne — szum


def first_message_title(message: str, limit: int = 80) -> str:
    """Tytuł wątku bez nazwy (session_index, Codex Desktop): pierwsza linia pierwszej wiadomości
    użytkownika, przycięta — lepsza niż nazwa gałęzi. Pusty, gdy wiadomość to nie zwykły tekst."""
    for line in (message or "").splitlines():
        line = " ".join(line.strip(" #*>`-").split())
        if len(line) >= 3 and not line.startswith("<"):
            if len(line) > limit:
                cut = line[: limit - 1]
                line = (cut[: cut.rfind(" ")] if cut.rfind(" ") > limit * 0.6 else cut).rstrip(" ,;:") + "…"
            return line
    return ""


def task_name(spawn: dict) -> str:
    """„/root/la029_adapter” → „la029_adapter”."""
    return os.path.basename(str(spawn.get("agent_path") or "").rstrip("/"))


def build(root: CodexThreadLog, children: list[CodexThreadLog], title: str = "") -> Coordinator:
    """Koordynator z wątku głównego i wszystkich jego potomków (już doczytanych)."""
    coord = Coordinator(id=f"codex:{root.id}", tool="codex", session_id=root.id, path=root.path, cwd=root.cwd,
                        branch=root.branch, title=title or first_message_title(root.first_message),
                        model=root.model, first_ts=root.first_ts,
                        last_ts=root.last_ts)
    spawns = dict(root.spawns)
    for child in children:
        spawns.update(child.spawns)  # wnuki zlecają podagenci
    for child in sorted(children, key=lambda c: c.first_ts):
        name = task_name(child.spawn)
        order = spawns.get(name, {})
        run = Run(id=child.id, tool="codex")
        run.description = _humanize(name) or child.spawn.get("agent_nickname") or child.id[:8]
        run.nickname = str(child.spawn.get("agent_nickname") or "")
        run.agent_type = str(child.spawn.get("agent_role") or order.get("agent_type") or "default")
        run.model = child.model or order.get("model", "")
        run.background = True
        run.prompt_head = order.get("message") or child.first_message
        run.branch, run.cwd = child.branch, child.cwd
        run.transcript = child.path
        for i, turn in enumerate(child.turns):
            round_ = run.begin(turn["start"], "launch" if i == 0 else "continue")
            if turn["status"] != "running":
                run.finish(turn["end"] or turn["start"], turn["status"], result=turn["result"])
            elif turn["status"] == "running":
                round_.status = "running"
        if not child.turns:
            run.begin(order.get("ts") or child.first_ts)
        run.last_ts = max(run.last_ts, child.last_ts)
        coord.runs[run.id] = run
        coord.last_ts = max(coord.last_ts, child.last_ts)
    coord.questions = dict(root.questions)
    return coord


def excerpt(path: str, limit: int = 12) -> list[dict]:
    """Ostatnie wypowiedzi i wywołania narzędzi podagenta Codexa (do „Pokaż wynik”)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 512 * 1024))
            data = fh.read()
    except OSError:
        return []
    items = []
    lines = data.split(b"\n")[1 if size > 512 * 1024 else 0:]
    for line in lines:
        if b'"response_item"' not in line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        payload = record.get("payload") if isinstance(record, dict) else None
        if not isinstance(payload, dict):
            continue
        ts = parse_ts(record.get("timestamp"))
        if payload.get("type") == "message" and payload.get("role") == "assistant":
            text = "\n".join(str(c.get("text") or "") for c in payload.get("content") or [] if isinstance(c, dict))
            if text.strip():
                items.append({"ts": ts, "kind": "text", "text": text.strip()[:1600]})
        elif payload.get("type") in ("function_call", "custom_tool_call"):
            items.append({"ts": ts, "kind": "tool", "text": describe_call(str(payload.get("name") or "?"), payload)})
    return items[-limit:]


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _question_text(args: dict) -> str:
    questions = args.get("questions")
    if isinstance(questions, list):
        return "\n".join(str(q.get("question") or q.get("prompt") or "") for q in questions if isinstance(q, dict))
    return str(args.get("question") or args.get("prompt") or "")


def _humanize(name: str) -> str:
    """„la029_adapter” → „la029 adapter”."""
    return re.sub(r"[_]+", " ", name or "").strip()
