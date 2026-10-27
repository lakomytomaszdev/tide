"""Dzienniki Codexa: <CODEX_HOME>/sessions/RRRR/MM/DD/rollout-*.jsonl.

Co wiemy o strukturze (z obserwacji, format nie jest publiczny):
- pierwsza linia to session_meta: id, cwd, git.branch, originator, source,
- source z kluczem „subagent” oznacza wątek podagenta albo recenzenta zgód — nie jest osobną
  sesją. Podagent uruchomiony przez agenta (subagent.thread_spawn) ma parent_thread_id (także
  w payload.parent_thread_id), a payload.session_id to id wątku głównego (u podagenta podagenta
  też główny, nie rodzic). Jego stan liczymy, bo zwija się do rodzica (agents/scanner.py);
  recenzent zgód (subagent.other = "guardian") i inne rodzaje bez rodzica są pomijane w całości,
- event_msg/task_started zaczyna turę, task_complete albo turn_aborted ją kończy,
- turn_context niesie cwd, model i approval_policy (przy „never” Codex nigdy nie pyta o zgodę),
- session_meta.model_provider to dostawca modelu: `openai` (chmura) albo np. `ollama` — model lokalny,
- response_item function_call / custom_tool_call to wywołanie narzędzia, a *_output jego wynik
  (powiązane przez call_id),
- prośby o zgodę nie trafiają do dziennika, więc wnioskujemy o nich z braku wyniku,
- turę zaczętą przez zaplanowane zadanie Codex Desktop (automatyzacja „heartbeat” z
  ~/.codex/automations/<id>/automation.toml, która co jakiś czas pisze do wątku) poznajemy po
  task_started.turn_attribution.turn_trigger == "automation_heartbeat_scheduled" (nowsze wersje,
  ręcznie wpisana wiadomość ma tam „composer”) albo po wiadomości użytkownika (response_item
  message, role user) z internal_chat_message_metadata_passthrough.content_item_kinds
  zawierającym „user.heartbeat” — wpisana ręcznie ma „user.text”.
"""
import json
import os
import re

from .claude import shorten
from .state import MID_TURN, TOOL_PENDING, TURN_END, Pending, SessionLog
from .timeutil import parse_ts

ASKS_USER = {"request_user_input"}
# Codex Desktop pyta też asynchronicznie: wywołanie od razu dostaje {"accepted": true}, tura idzie dalej,
# a pytanie wisi w aplikacji („Wymaga reakcji użytkownika”), aż przyjdzie wiadomość z odpowiedzią.
ASKS_USER_ASYNC = {"request_user_input_async"}
QUESTION_REPLY_TAG = "<send_user_message_question_reply"
LONG_RUNNING = {"wait_agent", "wait", "sleep", "spawn_agent"}
AUTOMATION_TRIGGER = "automation"           # przedrostek turn_trigger, np. automation_heartbeat_scheduled
SCHEDULED_KINDS = {"user.heartbeat"}         # wiadomość wysłana przez zaplanowane zadanie
TYPED_KINDS = {"user.text", "user.image"}    # wiadomość od człowieka
CALLS = ("function_call", "custom_tool_call", "local_shell_call")
OUTPUTS = ("function_call_output", "custom_tool_call_output", "local_shell_call_output")


class CodexLog(SessionLog):
    tool = "codex"

    def __init__(self, path: str):
        super().__init__(path)
        # Zapasowy identyfikator z nazwy pliku: rollout-<data>-<uuid>.jsonl
        match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path)
        self.session_id = match.group(1) if match else os.path.basename(path)
        self.originator = ""
        self.parent_id = ""     # podagent (thread_spawn): id wątku rodzica
        self.root_hint = ""     # podagent: payload.session_id — id wątku głównego według Codexa
        self.nickname = ""      # podagent: agent_nickname
        self.open_questions: dict[str, float] = {}  # pytania asynchroniczne bez odpowiedzi: call_id → czas
        self.open_question_texts: dict[str, str] = {}  # call_id → treść pytania (waitingText)

    def feed(self, record: dict) -> None:
        kind = record.get("type")
        payload = record.get("payload") or {}
        if not isinstance(payload, dict):
            return
        ts = parse_ts(record.get("timestamp"))

        if kind == "session_meta":
            self._meta(payload)
            return
        if self.hidden and not self.parent_id:
            return
        self.touch(ts)

        if kind == "turn_context":
            if payload.get("cwd"):
                self.cwd = payload["cwd"]
            if isinstance(payload.get("model"), str) and payload["model"]:
                self.model = payload["model"]
            policy = payload.get("approval_policy")
            if isinstance(policy, str):
                self.approvals_possible = policy != "never"
        elif kind == "event_msg":
            event = payload.get("type")
            if event == "task_started":
                self.scheduled = _automation_trigger(payload)
            if event in ("task_started", "user_message"):
                self.set_phase(MID_TURN, ts)
            elif event in ("task_complete", "turn_aborted"):
                self.set_phase(TURN_END, ts)
                if event == "turn_aborted":
                    self.last_action = "Przerwano"
                    self.said("", ts)
                elif isinstance(payload.get("last_agent_message"), str) and payload["last_agent_message"].strip():
                    self.said(payload["last_agent_message"].strip(), ts)
        elif kind == "response_item":
            item = payload.get("type")
            if item in CALLS:
                name = str(payload.get("name") or item)
                self.said("", ts)  # narzędzie po tekście — wcześniejsze pytanie nie kończy tury
                self.set_phase(TOOL_PENDING, ts)
                call_id = str(payload.get("call_id") or payload.get("id"))
                described = describe_call(name, payload)
                asks = name in ASKS_USER or name in ASKS_USER_ASYNC
                self.pending[call_id] = Pending(name, ts, asks_user=name in ASKS_USER, long_running=name in LONG_RUNNING,
                                                text=(question_text(payload) or described) if asks else described)
                self.last_action = described
                if name in ASKS_USER_ASYNC:
                    self.open_questions[call_id] = ts
                    self.open_question_texts[call_id] = question_text(payload) or described
            elif item == "message" and payload.get("role") == "assistant":
                text = "\n".join(str(p.get("text") or "") for p in payload.get("content") or []
                                 if isinstance(p, dict) and p.get("type") in ("output_text", "text")).strip()
                if text:
                    self.said(text, ts)
            elif item == "message" and payload.get("role") == "user":
                kinds = _content_kinds(payload)
                if self.open_questions and (_is_question_reply(payload) or kinds & TYPED_KINDS):
                    # Odpowiedź na pytanie (albo zwykła wiadomość użytkownika w wątku) zamyka
                    # najstarsze otwarte pytanie.
                    oldest = min(self.open_questions, key=self.open_questions.get)
                    del self.open_questions[oldest]
                    self.open_question_texts.pop(oldest, None)
                if kinds & TYPED_KINDS or (not kinds and not _is_question_reply(payload) and _typed_text(payload)):
                    self.last_user_ts = max(self.last_user_ts, ts)
                if kinds & SCHEDULED_KINDS:
                    self.scheduled = True
                elif kinds & TYPED_KINDS:
                    self.scheduled = False  # użytkownik dopisał się do tury
            elif item in OUTPUTS:
                self.pending.pop(str(payload.get("call_id")), None)
                if not self.pending and self.phase == TOOL_PENDING:
                    self.set_phase(MID_TURN, ts)

    def _meta(self, payload: dict) -> None:
        self.session_id = str(payload.get("id") or self.session_id)
        self.cwd = payload.get("cwd") or self.cwd
        self.launch_cwd = self.launch_cwd or str(payload.get("cwd") or "")
        git = payload.get("git")
        if isinstance(git, dict) and git.get("branch"):
            self.branch = git["branch"]
        self.originator = str(payload.get("originator") or "")
        provider = payload.get("model_provider")
        self.provider = provider if isinstance(provider, str) else ""
        source = payload.get("source")
        # hidden: nie jest osobną sesją (KRunner, worktrees). Podagent z rodzicem i tak jest
        # czytany — jego stan trafia do karty wątku głównego.
        self.hidden = isinstance(source, dict) and "subagent" in source
        spawn = _thread_spawn(source)
        if spawn is not None:
            self.parent_id = str(spawn.get("parent_thread_id") or payload.get("parent_thread_id") or "")
            self.nickname = str(spawn.get("agent_nickname") or "")
            root = payload.get("session_id")
            self.root_hint = root if isinstance(root, str) and root != self.session_id else ""
        self.touch(parse_ts(payload.get("timestamp")))


def _thread_spawn(source) -> dict | None:
    """source.subagent.thread_spawn albo None (sesja główna, recenzent zgód, inne rodzaje)."""
    sub = source.get("subagent") if isinstance(source, dict) else None
    spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
    return spawn if isinstance(spawn, dict) else None


def _is_question_reply(payload: dict) -> bool:
    for part in payload.get("content") or []:
        if isinstance(part, dict) and str(part.get("text") or "").lstrip().startswith(QUESTION_REPLY_TAG):
            return True
    return False


def _typed_text(payload: dict) -> bool:
    """Wiadomość użytkownika bez metadanych (starsze wersje): zwykły tekst, nie wstawka w znacznikach
    (<environment_context>, <user_instructions> i podobne dopisuje sam Codex)."""
    for part in payload.get("content") or []:
        if isinstance(part, dict) and part.get("type") in ("input_text", "text"):
            text = str(part.get("text") or "").lstrip()
            if text and not text.startswith("<"):
                return True
    return False


def question_text(payload: dict) -> str:
    """Treść pytania z argumentów request_user_input (questions[].question albo question/prompt)."""
    raw = payload.get("arguments") if "arguments" in payload else payload.get("input")
    args = raw
    if isinstance(raw, str):
        try:
            args = json.loads(raw)
        except ValueError:
            return ""
    if not isinstance(args, dict):
        return ""
    questions = args.get("questions")
    if isinstance(questions, list):
        texts = [str(q.get("question") or q.get("prompt") or "").strip() for q in questions if isinstance(q, dict)]
        return "\n".join(t for t in texts if t)
    return str(args.get("question") or args.get("prompt") or "").strip()


def _automation_trigger(payload: dict) -> bool:
    attribution = payload.get("turn_attribution")
    trigger = attribution.get("turn_trigger") if isinstance(attribution, dict) else None
    return isinstance(trigger, str) and trigger.startswith(AUTOMATION_TRIGGER)


def _content_kinds(payload: dict) -> set[str]:
    meta = payload.get("internal_chat_message_metadata_passthrough")
    kinds = meta.get("content_item_kinds") if isinstance(meta, dict) else None
    return {k for k in kinds if isinstance(k, str)} if isinstance(kinds, list) else set()


def describe_call(name: str, payload: dict) -> str:
    """Krótki opis wywołania, np. „shell · make test” albo „apply_patch · main.py”."""
    raw = payload.get("arguments") if "arguments" in payload else payload.get("input")
    args = raw
    if isinstance(raw, str):
        try:
            args = json.loads(raw)
        except ValueError:
            args = raw
    detail = ""
    if isinstance(args, dict):
        command = args.get("command") or args.get("cmd")
        if isinstance(command, list):
            # ["bash", "-lc", "make test"] — liczy się ostatni element
            command = command[-1] if command else ""
        detail = command or args.get("task_name") or ""
    elif isinstance(args, str) and name == "apply_patch":
        match = re.search(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", args, re.M)
        detail = os.path.basename(match.group(1).strip()) if match else ""
    return shorten(f"{name} · {detail}" if detail else name)
