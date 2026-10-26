"""Dzienniki Codexa: <CODEX_HOME>/sessions/RRRR/MM/DD/rollout-*.jsonl.

Co wiemy o strukturze (z obserwacji, format nie jest publiczny):
- pierwsza linia to session_meta: id, cwd, git.branch, originator, source,
- source z kluczem „subagent” oznacza wątek podagenta albo recenzenta zgód — nie pokazujemy go,
- event_msg/task_started zaczyna turę, task_complete albo turn_aborted ją kończy,
- turn_context niesie cwd i approval_policy (przy „never” Codex nigdy nie pyta o zgodę),
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

    def feed(self, record: dict) -> None:
        kind = record.get("type")
        payload = record.get("payload") or {}
        if not isinstance(payload, dict):
            return
        ts = parse_ts(record.get("timestamp"))

        if kind == "session_meta":
            self._meta(payload)
            return
        if self.hidden:
            return
        self.touch(ts)

        if kind == "turn_context":
            if payload.get("cwd"):
                self.cwd = payload["cwd"]
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
        elif kind == "response_item":
            item = payload.get("type")
            if item in CALLS:
                name = str(payload.get("name") or item)
                self.set_phase(TOOL_PENDING, ts)
                self.pending[str(payload.get("call_id") or payload.get("id"))] = Pending(
                    name, ts, asks_user=name in ASKS_USER, long_running=name in LONG_RUNNING)
                self.last_action = describe_call(name, payload)
            elif item == "message" and payload.get("role") == "user":
                kinds = _content_kinds(payload)
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
        git = payload.get("git")
        if isinstance(git, dict) and git.get("branch"):
            self.branch = git["branch"]
        self.originator = str(payload.get("originator") or "")
        source = payload.get("source")
        self.hidden = isinstance(source, dict) and "subagent" in source
        self.touch(parse_ts(payload.get("timestamp")))


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
