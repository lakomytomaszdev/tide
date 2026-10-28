"""Koordynator Claude Code: <claude>/projects/<katalog>/<sesja>.jsonl i <sesja>/subagents/.

Co wiemy o strukturze (z obserwacji prawdziwych dzienników, format nie jest publiczny):
- zlecenie to wpis assistant z blokiem tool_use `Agent` (dawniej `Task`); input: description,
  subagent_type, prompt, model, run_in_background,
- wynik przychodzi jako user z tool_result o tym samym tool_use_id; `toolUseResult` niesie
  `agentId`, a dla pracy w tle `status: "async_launched"` — wtedy to tylko „uruchomiono”,
- koniec pracy w tle to blok `<task-notification>` z `<task-id>` (= agentId), `<status>`
  (completed, failed, killed, stopped), `<summary>`, `<result>` i `<usage>`/`<duration_ms>`.
  Gdy koordynator był zajęty, powiadomienie jest w kolejce: wpis attachment
  `queued_command` z tekstem w `attachment.prompt`; inaczej user z tekstem w `message.content`.
  Ten sam blok pojawia się też w queue-operation i api-request-blob — te pomijamy,
- nowsze wersje oddają raport przez SubagentHandback: `<result>` mówi wtedy tylko, że raport
  przyszedł wiadomością, a sama wiadomość to user/attachment z `origin: {kind: "peer", from: <agentId>}`,
- `SendMessage` {to: agentId} wznawia podagenta (`toolUseResult.resumedAgentId`) — kolejna runda,
  zakończona kolejnym powiadomieniem; `TaskStop` {task_id} go zatrzymuje,
- `AskUserQuestion` bez tool_result to pytanie, na które koordynator czeka,
- `Write`/`Edit`/`MultiEdit` z `file_path` w katalogu planów to plan zapisany przez samego koordynatora
  (Coordinator.plan_writes — czyj jest plan bez `session`, plan.pick),
- subagents/agent-<id>.meta.json: agentType, description, toolUseId, model, spawnDepth,
  requestShape (background…), a w nowszych wersjach `finished`.

Duże dzienniki (setki MB) czytamy raz od początku i potem tylko dopisane bajty. JSON parsujemy
wyłącznie w liniach, które mogą coś zmienić (szybki test bajtów przed json.loads).
"""
import json
import os
import re

from ..agents.claude import describe_tool
from ..agents.jsonl import read_head
from ..agents.timeutil import parse_ts
from .model import (COMPLETED, FAILED, KILLED, PROMPT_HEAD, Coordinator, Question, Run, add_plan_write)
from .plan import plan_file_name

SPAWN_TOOLS = ("Agent", "Task")
WATCHED_TOOLS = SPAWN_TOOLS + ("SendMessage", "AskUserQuestion", "TaskStop")
LINE_MARKERS = (b"task-notification", b'"peer"', b'"custom-title"', b'"ai-title"')
TOOL_NAME = re.compile(rb'"name":\s*"(?:Agent|Task|SendMessage|AskUserQuestion|TaskStop)"')
# Zapis pliku planu przez samego koordynatora (dowód, czyj jest plan bez `session`, plan.pick).
WRITE_TOOLS = ("Write", "Edit", "MultiEdit")
WRITE_NAME = re.compile(rb'"name":\s*"(?:Write|Edit|MultiEdit)"')
TOOL_USE_ID = re.compile(rb'"tool_use_id":\s*"([^"]+)"')
TIMESTAMP = re.compile(rb'"timestamp":\s*"([^"]+)"')
SIDECHAIN = re.compile(rb'"isSidechain":\s*true')
QUEUE = re.compile(rb'\{\s*"type":\s*"queue-operation"')
NOTIFICATION = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
STATUS_MAP = {"completed": COMPLETED, "failed": FAILED, "killed": KILLED, "stopped": KILLED, "error": FAILED}
HANDBACK_STUB = re.compile(r"^\s*This agent's report was delivered to you as a message", re.I)
CHUNK = 8 * 1024 * 1024


def is_coordinator_candidate(path: str) -> bool:
    """Tani test: sesja ma katalog subagents/ z co najmniej jednym podagentem."""
    folder = os.path.join(path[:-len(".jsonl")], "subagents")
    try:
        return any(name.startswith("agent-") and name.endswith(".jsonl") for name in os.listdir(folder))
    except OSError:
        return False


class ClaudeCoordinatorLog:
    """Przyrostowy odczyt jednego dziennika koordynatora Claude Code."""

    def __init__(self, path: str):
        self.path = path
        session = os.path.basename(path)[:-len(".jsonl")]
        self.coord = Coordinator(id=f"claude:{session}", tool="claude", session_id=session, path=path)
        self.offset = 0
        self.size = 0
        self.mtime = 0.0
        self._rest = b""
        self._uses: dict[str, str] = {}       # tool_use_id → nazwa narzędzia (obserwowane)
        self._spawns: dict[str, dict] = {}    # tool_use_id → input Agent (przed agentId)
        self._agent_by_use: dict[str, str] = {}
        self._custom_title = False
        self.subagents_dir = os.path.join(path[:-len(".jsonl")], "subagents")

    # --- odczyt ----------------------------------------------------------------------------

    def update(self) -> bool:
        """Doczytuje dopisane bajty. True, gdy plik się zmienił."""
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        if st.st_size < self.offset:
            self.__init__(self.path)  # plik podmieniony — od nowa
        if st.st_size == self.offset:
            self.mtime = st.st_mtime
            return False
        self.size, self.mtime = st.st_size, st.st_mtime
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    data = self._rest + chunk
                    lines = data.split(b"\n")
                    self._rest = lines.pop()
                    for line in lines:
                        self._line(line)
                self.offset = fh.tell()
        except OSError:
            return False
        self._read_meta()
        return True

    def _line(self, line: bytes) -> None:
        if not line:
            return
        if not self.coord.first_ts:
            stamp = TIMESTAMP.search(line)
            if stamp:
                self.coord.first_ts = parse_ts(stamp.group(1).decode("ascii", "replace"))
        stamp = TIMESTAMP.search(line)
        if stamp and not SIDECHAIN.search(line) and not QUEUE.match(line):
            ts = parse_ts(stamp.group(1).decode("ascii", "replace"))
            if ts > self.coord.last_ts:
                self.coord.last_ts = ts
        interesting = any(marker in line for marker in LINE_MARKERS) or bool(TOOL_NAME.search(line)) \
            or (b"plans/" in line and bool(WRITE_NAME.search(line)))
        if not interesting and b'"tool_result"' in line and self._uses:
            interesting = any(m.group(1).decode("ascii", "replace") in self._uses for m in TOOL_USE_ID.finditer(line))
        if not interesting:
            return
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(record, dict):
            self.feed(record)

    def feed(self, record: dict) -> None:
        kind = record.get("type")
        if kind == "custom-title" and record.get("customTitle"):
            self.coord.title, self._custom_title = str(record["customTitle"]), True
            return
        if kind == "ai-title":
            if not self._custom_title and record.get("aiTitle"):
                self.coord.title = str(record["aiTitle"])
            return
        if record.get("isSidechain"):
            return
        ts = parse_ts(record.get("timestamp"))
        if kind == "assistant":
            self._where(record)
            message = record.get("message") or {}
            if message.get("model") and not str(message.get("model")).startswith("<"):
                self.coord.model = str(message["model"])
            for block in _blocks(message.get("content")):
                if block.get("type") != "tool_use":
                    continue
                if block.get("name") in WATCHED_TOOLS:
                    self._use(block, ts)
                elif block.get("name") in WRITE_TOOLS:
                    self._plan_write(block, ts)
        elif kind == "user":
            self._where(record)
            content = (record.get("message") or {}).get("content")
            origin = record.get("origin")
            if isinstance(origin, dict) and origin.get("kind") == "peer":
                self._peer(origin, _text(content), ts)
                return
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        self._result(block, record.get("toolUseResult"), ts)
            self._notifications(_text(content), ts)
        elif kind == "attachment":
            attachment = record.get("attachment")
            if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
                return
            origin = attachment.get("origin")
            prompt = attachment.get("prompt")
            text = prompt if isinstance(prompt, str) else _text(prompt)
            if isinstance(origin, dict) and origin.get("kind") == "peer":
                self._peer(origin, text, ts)
            else:
                self._notifications(text, ts)

    # --- zdarzenia --------------------------------------------------------------------------

    def _where(self, record: dict) -> None:
        if record.get("cwd"):
            self.coord.cwd = record["cwd"]
            self.coord.launch_cwd = self.coord.launch_cwd or self.coord.cwd
        if record.get("gitBranch"):
            self.coord.branch = record["gitBranch"]

    def _use(self, block: dict, ts: float) -> None:
        name = block.get("name")
        use_id = str(block.get("id") or "")
        args = block.get("input") if isinstance(block.get("input"), dict) else {}
        self._uses[use_id] = name
        if name in SPAWN_TOOLS:
            self._spawns[use_id] = dict(args, _ts=ts)
        elif name == "SendMessage":
            # Krótki opis kontynuacji do osi czasu (summary albo początek wiadomości), bez reszty treści.
            note = args.get("summary") or args.get("message") or args.get("content") or ""
            self._spawns[use_id] = {"_note": " ".join(str(note).split())[:120]}
        elif name == "AskUserQuestion":
            self.coord.questions[use_id] = Question(use_id, ts, _questions(args))

    def _plan_write(self, block: dict, ts: float) -> None:
        args = block.get("input") if isinstance(block.get("input"), dict) else {}
        name = plan_file_name(args.get("file_path") or "")
        if name:
            add_plan_write(self.coord.plan_writes, name, ts)

    def _result(self, block: dict, extra, ts: float) -> None:
        use_id = str(block.get("tool_use_id") or "")
        name = self._uses.get(use_id)
        if name is None:
            return
        extra = extra if isinstance(extra, dict) else {}
        error = bool(block.get("is_error"))
        if name in SPAWN_TOOLS:
            args = self._spawns.get(use_id, {})
            agent_id = str(extra.get("agentId") or "") or _agent_id(_text(block.get("content"))) or use_id
            run = self._run(agent_id, args, use_id)
            run.begin(args.get("_ts") or ts)
            if error:
                run.finish(ts, FAILED, result=_text(block.get("content")))
            elif extra.get("status") != "async_launched" and not extra.get("isAsync"):
                # Podagent na pierwszym planie: wynik od razu w tool_result.
                text = _text(block.get("content"))
                status = STATUS_MAP.get(str(extra.get("status") or "completed"), COMPLETED)
                run.finish(ts, status, result=_strip_agent_line(text),
                           duration_ms=_int(extra.get("totalDurationMs")))
            self._agent_by_use[use_id] = agent_id
        elif name == "SendMessage":
            resumed = str(extra.get("resumedAgentId") or extra.get("revivedAgentId") or "")
            if resumed and resumed in self.coord.runs:
                self.coord.runs[resumed].begin(ts, "continue", note=self._spawns.get(use_id, {}).get("_note", ""))
        elif name == "TaskStop":
            task = str(extra.get("task_id") or "")
            if task in self.coord.runs:
                self.coord.runs[task].finish(ts, KILLED)
        elif name == "AskUserQuestion":
            self.coord.questions.pop(use_id, None)

    def _notifications(self, text: str, ts: float) -> None:
        if "<task-notification>" not in (text or ""):
            return
        for match in NOTIFICATION.finditer(text):
            body = match.group(1)
            task = _tag(body, "task-id")
            run = self.coord.runs.get(task)
            if run is None:
                continue  # zadanie powłoki w tle albo podagent spoza okna
            status = STATUS_MAP.get(_tag(body, "status").lower(), COMPLETED)
            result = _tag(body, "result")
            if HANDBACK_STUB.match(result):
                result = ""  # raport przyszedł osobną wiadomością (peer)
            run.finish(ts, status, result=result.strip(), summary=_tag(body, "summary"),
                       duration_ms=_int(_tag(body, "duration_ms")))

    def _peer(self, origin: dict, text: str, ts: float) -> None:
        run = self.coord.runs.get(str(origin.get("from") or ""))
        if run is None:
            return
        # origin niesie sam raport (handbackReportText) albo treść wiadomości (body).
        report = origin.get("handbackReportText") or origin.get("body")
        report = report if isinstance(report, str) and report.strip() else _unwrap_peer(text)
        if report:
            run.deliver(ts, report)

    def _run(self, agent_id: str, args: dict, use_id: str) -> Run:
        run = self.coord.runs.get(agent_id)
        if run is None:
            run = self.coord.runs[agent_id] = Run(id=agent_id, tool="claude")
            run.transcript = os.path.join(self.subagents_dir, f"agent-{agent_id}.jsonl")
        run.tool_use_id = use_id
        run.description = str(args.get("description") or run.description)
        run.agent_type = str(args.get("subagent_type") or run.agent_type or "general-purpose")
        run.model = str(args.get("model") or run.model)
        run.background = bool(args.get("run_in_background")) or run.background
        prompt = args.get("prompt")
        if isinstance(prompt, str) and prompt:
            run.prompt_head = prompt[:PROMPT_HEAD]
        return run

    def _read_meta(self) -> None:
        """subagents/agent-<id>.meta.json: typ, opis i model, gdy w dzienniku czegoś brakuje."""
        for run in self.coord.runs.values():
            if run.agent_type and run.description and run.model:
                continue
            try:
                with open(os.path.join(self.subagents_dir, f"agent-{run.id}.meta.json"), encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(meta, dict):
                run.agent_type = run.agent_type or str(meta.get("agentType") or "")
                run.description = run.description or str(meta.get("description") or "")
                run.model = run.model or str(meta.get("model") or "")
                run.background = run.background or meta.get("requestShape") == "background"


def transcript_info(path: str, cache: dict | None = None) -> dict:
    """Gałąź, katalog i ostatnia czynność podagenta z jego dziennika (początek i ogon pliku)."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, st.st_size, st.st_mtime)
    if cache is not None and key in cache:
        return cache[key]
    info = {"mtime": st.st_mtime, "branch": "", "cwd": "", "lastAction": ""}
    for record in read_head(path, 32 * 1024):
        if record.get("gitBranch") and not info["branch"]:
            info["branch"] = str(record["gitBranch"])
        if record.get("cwd") and not info["cwd"]:
            info["cwd"] = str(record["cwd"])
    for record in reversed(_tail(path, st.st_size, 96 * 1024)):
        if record.get("gitBranch"):
            info["branch"] = str(record["gitBranch"])
        content = (record.get("message") or {}).get("content") if record.get("type") == "assistant" else None
        for block in reversed(_blocks(content)):
            if block.get("type") == "tool_use":
                info["lastAction"] = describe_tool(str(block.get("name") or "?"), block.get("input") or {})
                break
        if info["lastAction"]:
            break
    if cache is not None:
        if len(cache) > 512:
            cache.clear()
        cache[key] = info
    return info


def excerpt(path: str, limit: int = 12) -> list[dict]:
    """Wycinek dziennika podagenta do „Pokaż wynik”: ostatnie wypowiedzi i wywołania narzędzi."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    items: list[dict] = []
    for record in _tail(path, size, 512 * 1024):
        if record.get("type") != "assistant":
            continue
        ts = parse_ts(record.get("timestamp"))
        for block in _blocks((record.get("message") or {}).get("content")):
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                items.append({"ts": ts, "kind": "text", "text": _clip(block["text"], 1600)})
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "?")
                args = block.get("input") or {}
                if name == "SubagentHandback":
                    items.append({"ts": ts, "kind": "text", "text": _clip(str(args.get("message") or ""), 1600)})
                else:
                    items.append({"ts": ts, "kind": "tool", "text": describe_tool(name, args)})
    return items[-limit:]


def final_report(path: str) -> str:
    """Raport podagenta z jego dziennika (SubagentHandback albo ostatni tekst) — zapasowo do „Pokaż wynik”."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    for record in reversed(_tail(path, size, 512 * 1024)):
        if record.get("type") != "assistant":
            continue
        for block in reversed(_blocks((record.get("message") or {}).get("content"))):
            if block.get("type") == "tool_use" and block.get("name") == "SubagentHandback":
                return str((block.get("input") or {}).get("message") or "")
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                return str(block["text"])
    return ""


# --- pomocnicze -------------------------------------------------------------------------------

def _tail(path: str, size: int, limit: int) -> list[dict]:
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, size - limit))
            data = fh.read(limit)
    except OSError:
        return []
    lines = data.split(b"\n")
    if size > limit:
        lines = lines[1:]
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _blocks(content) -> list[dict]:
    return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text") or "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _tag(body: str, name: str) -> str:
    match = re.search(rf"<{name}>(.*?)</{name}>", body, re.S)
    return match.group(1).strip() if match else ""


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _agent_id(text: str) -> str:
    match = re.search(r"agentId:\s*([0-9a-f]{8,})", text or "")
    return match.group(1) if match else ""


def _strip_agent_line(text: str) -> str:
    """Wynik podagenta bez dopisku Claude Code „agentId: … (for resuming …)” na końcu."""
    return re.sub(r"\n*agentId: [0-9a-f]+[^\n]*(?:\n<usage>.*)?$", "", text or "", flags=re.S).strip()


def _unwrap_peer(text: str) -> str:
    """Wiadomość od podagenta bywa opakowana w znacznik — zostawiamy samą treść."""
    match = re.search(r"<([a-z]+-message)[^>]*>(.*?)(?:</\1>|$)", text or "", re.S)
    body = match.group(2) if match else text or ""
    return re.sub(r"^\s*\[[^\]\n]{3,40}\][^\n]*\n", "", body).strip()


def _questions(args: dict) -> str:
    questions = args.get("questions")
    if isinstance(questions, list):
        texts = [str(q.get("question") or "") for q in questions if isinstance(q, dict)]
        return "\n".join(t for t in texts if t)
    return str(args.get("question") or "")


def _clip(text: str, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
