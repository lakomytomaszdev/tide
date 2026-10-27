"""Dzienniki Claude Code: <root>/projects/<katalog>/<id-sesji>.jsonl i <root>/sessions/<pid>.json.

Co wiemy o strukturze (z obserwacji, format nie jest publiczny):
- każdy wpis user/assistant/system ma timestamp, cwd i gitBranch,
- odpowiedź modelu kończy turę z message.stop_reason == "end_turn", a zaraz po niej
  pojawia się system/turn_duration,
- wywołanie narzędzia to assistant z treścią tool_use, wynik przychodzi jako user z tool_result,
- przerwanie przez użytkownika to user z tekstem „[Request interrupted by user…”,
- wpisy isSidechain należą do podagentów i nie zmieniają stanu sesji głównej,
- odpowiedź modelu ma message.model (`claude-…` albo np. `qwen3-coder:30b` na serwerze lokalnym),
- ai-title niesie krótki tytuł rozmowy (dopisywany wielokrotnie, liczy się ostatni), a custom-title
  tytuł nadany przez użytkownika,
- sessions/<pid>.json opisuje działający proces: sessionId, cwd i status (busy, waiting, idle,
  shell — tura skończona, ale w tle działa uruchomione przez agenta zadanie, do którego on wróci).
"""
import json
import os
import re

from .localmodel import is_local_claude_model
from .state import MID_TURN, TOOL_PENDING, TURN_END, Pending, SessionLog, Status
from .timeutil import parse_ts

TURN_END_SYSTEM = {"turn_duration", "local_command"}
ACTIVITY_SYSTEM = TURN_END_SYSTEM | {"compact_boundary"}
ASKS_USER = {"AskUserQuestion", "ExitPlanMode"}
LONG_RUNNING = {"Agent", "Task"}
LOCAL_COMMAND_PREFIXES = ("<local-command-stdout>", "<local-command-stderr>")


def escape_cwd(cwd: str) -> str:
    """Nazwa katalogu projektu, pod którym Claude Code trzyma sesje danego katalogu roboczego."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


class ClaudeLog(SessionLog):
    tool = "claude"

    def __init__(self, path: str):
        super().__init__(path)
        self.session_id = os.path.splitext(os.path.basename(path))[0]
        self.project_dir = os.path.basename(os.path.dirname(path))
        self.custom_title = False

    def feed(self, record: dict) -> None:
        kind = record.get("type")
        if kind == "custom-title" and record.get("customTitle"):
            # Tytuł nadany przez użytkownika (/rename) wygrywa z tytułem wygenerowanym.
            self.title = str(record["customTitle"])
            self.custom_title = True
            return
        if kind == "ai-title":
            if not self.custom_title:
                self.title = str(record.get("aiTitle") or self.title)
            return
        if record.get("isSidechain"):
            return
        if kind not in ("user", "assistant", "system"):
            # Załączniki i zapytania API to też ruch w sesji, ale nie zmieniają fazy.
            if kind in ("attachment", "api-request"):
                self.touch(parse_ts(record.get("timestamp")))
            return

        ts = parse_ts(record.get("timestamp"))
        if kind == "system" and record.get("subtype") not in ACTIVITY_SYSTEM:
            return  # np. away_summary dopisywane, gdy użytkownika nie ma — to nie ruch agenta
        self.touch(ts)
        if record.get("cwd"):
            self.cwd = record["cwd"]
        if record.get("gitBranch"):
            self.branch = record["gitBranch"]

        if kind == "system":
            if record.get("subtype") in TURN_END_SYSTEM:
                self.set_phase(TURN_END, ts)
        elif kind == "assistant":
            self._assistant(record, ts)
        elif not record.get("isMeta"):
            self._user(record, ts)

    def _assistant(self, record: dict, ts: float) -> None:
        message = record.get("message") or {}
        model = message.get("model") if isinstance(message, dict) else None
        if isinstance(model, str) and model and not model.startswith("<"):
            self.model = model  # ostatni prawdziwy model (po /model liczy się nowy)
            self.local_model = is_local_claude_model(model)
        if record.get("isApiErrorMessage"):
            self.set_phase(TURN_END, ts)
            return
        uses = [c for c in _content(message) if c.get("type") == "tool_use"]
        if uses:
            self.set_phase(TOOL_PENDING, ts)
            for use in uses:
                name = str(use.get("name") or "?")
                self.pending[str(use.get("id"))] = Pending(
                    name, ts, asks_user=name in ASKS_USER, long_running=name in LONG_RUNNING)
                self.last_action = describe_tool(name, use.get("input") or {})
        elif message.get("stop_reason") in ("end_turn", "stop_sequence", "max_tokens", "refusal"):
            self.set_phase(TURN_END, ts)
        elif self.phase != TOOL_PENDING:
            self.set_phase(MID_TURN, ts)

    def _user(self, record: dict, ts: float) -> None:
        content = (record.get("message") or {}).get("content")
        if isinstance(content, list):
            results = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_result"]
            for result in results:
                self.pending.pop(str(result.get("tool_use_id")), None)
            if results:
                if not self.pending:
                    self.set_phase(MID_TURN, ts)
                return
        text = _text(content)
        if text.startswith("[Request interrupted by user"):
            self.set_phase(TURN_END, ts)
            self.last_action = "Przerwano"
        elif text.startswith(LOCAL_COMMAND_PREFIXES):
            self.set_phase(TURN_END, ts)
        else:
            self.set_phase(MID_TURN, ts)


def read_process_status(sessions_dir: str) -> dict[int, dict]:
    """Pliki sessions/<pid>.json: pid → {sessionId, cwd, status}. Błędne pliki są pomijane."""
    out: dict[int, dict] = {}
    try:
        entries = list(os.scandir(sessions_dir))
    except OSError:
        return out
    for entry in entries:
        if not entry.name.endswith(".json"):
            continue
        try:
            with open(entry.path, encoding="utf-8") as fh:
                data = json.load(fh)
            pid = int(data.get("pid") or entry.name.split(".")[0])
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("sessionId"):
            out[pid] = data
    return out


def status_from(data: dict) -> Status | None:
    status = data.get("status")
    if status not in ("busy", "shell", "waiting", "idle"):
        return None  # nieznany status — o stanie decyduje dziennik sesji
    since = data.get("statusUpdatedAt") or data.get("updatedAt") or 0
    try:
        since = float(since) / 1000.0
    except (TypeError, ValueError):
        since = 0.0
    detail = data.get("waitingFor")
    return Status(status, since, detail if isinstance(detail, str) else "")


def describe_tool(name: str, args: dict) -> str:
    """Krótki opis ostatniej czynności, np. „Edit · main.py”."""
    detail = ""
    if not isinstance(args, dict):
        args = {}
    if name == "Bash":
        detail = args.get("description") or args.get("command") or ""
    elif args.get("file_path") or args.get("notebook_path"):
        detail = os.path.basename(str(args.get("file_path") or args.get("notebook_path")))
    elif name in ("Grep", "Glob"):
        detail = args.get("pattern") or ""
    elif name in ("Agent", "Task"):
        detail = args.get("description") or ""
    elif name == "WebFetch":
        detail = re.sub(r"^https?://([^/]+).*$", r"\1", str(args.get("url") or ""))
    elif name == "WebSearch":
        detail = args.get("query") or ""
    elif name == "Skill":
        detail = args.get("skill") or ""
    return shorten(f"{name} · {detail}" if detail else name)


def shorten(text: str, limit: int = 72) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _content(message: dict) -> list[dict]:
    content = message.get("content")
    return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []


def _text(content) -> str:
    if isinstance(content, str):
        return content.lstrip()
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return str(item.get("text") or "").lstrip()
    return ""
