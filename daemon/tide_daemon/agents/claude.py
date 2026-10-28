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
  shell — tura skończona, ale w tle działa uruchomione przez agenta zadanie, do którego on wróci),
- kompaktowanie (także samo, „Compacted while idle”) dopisuje system/compact_boundary, potem wpis
  „user” ze streszczeniem (isCompactSummary, isVisibleInTranscriptOnly), załączniki i system/informational.
  To nie odpowiedź użytkownika: pytanie z końca ostatniej tury (PT-29) dalej czeka,
- polecenie lokalne (/model, /compact…) to wpis isMeta z <local-command-caveat>, echo
  <command-name>/…</command-name> jako „user” i <local-command-stdout> — też nie odpowiedź. Skill
  wywołany poleceniem („<command-message>…”) jest zwykłą wiadomością użytkownika.
"""
import json
import os
import re

from .state import MID_TURN, TOOL_PENDING, TURN_END, Pending, SessionLog, Status
from .timeutil import parse_ts

TURN_END_SYSTEM = {"turn_duration", "local_command"}
ACTIVITY_SYSTEM = TURN_END_SYSTEM | {"compact_boundary"}
ASKS_USER = {"AskUserQuestion", "ExitPlanMode"}
LONG_RUNNING = {"Agent", "Task"}
LOCAL_COMMAND_PREFIXES = ("<local-command-stdout>", "<local-command-stderr>")
INJECTED_PREFIXES = ("<task-notification", "<system-reminder")  # wiadomości „user” dopisane przez Claude Code
LOCAL_COMMAND_CAVEAT = "<local-command-caveat>"  # wpis isMeta tuż przed echem polecenia lokalnego
COMMAND_ECHO = "<command-name>"


def synthetic_user(record: dict) -> bool:
    """Wpis „user”, którego nie napisał użytkownik: wstawka isMeta (treść skilla, zastrzeżenie polecenia
    lokalnego) albo streszczenie po kompaktowaniu (isCompactSummary, isVisibleInTranscriptOnly)."""
    return bool(record.get("isMeta") or record.get("isCompactSummary") or record.get("isVisibleInTranscriptOnly"))


def tail_has_turn(records: list[dict]) -> bool:
    """Czy ogon dziennika sięga ostatniej prawdziwej tury (wpis agenta albo wiadomość użytkownika sesji
    głównej) — inaczej JsonlTail cofa się dalej, np. za streszczenie i załączniki po kompaktowaniu."""
    for record in records:
        if record.get("isSidechain"):
            continue
        if record.get("type") == "assistant":
            return True
        if record.get("type") == "user" and not synthetic_user(record):
            text = _text((record.get("message") or {}).get("content"))
            if text and not text.startswith("<"):
                return True
    return False


def escape_cwd(cwd: str) -> str:
    """Nazwa katalogu projektu, pod którym Claude Code trzyma sesje danego katalogu roboczego."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


class ClaudeLog(SessionLog):
    tool = "claude"
    tail_enough = staticmethod(tail_has_turn)  # JsonlTail: ogon musi sięgać ostatniej tury

    def __init__(self, path: str):
        super().__init__(path)
        self.session_id = os.path.splitext(os.path.basename(path))[0]
        self.project_dir = os.path.basename(os.path.dirname(path))
        self.custom_title = False
        self._local_command = False  # był <local-command-caveat> — następne echo polecenia nie jest odpowiedzią

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
            if not self.launch_cwd and escape_cwd(self.cwd) == self.project_dir:
                self.launch_cwd = self.cwd
        if record.get("gitBranch"):
            self.branch = record["gitBranch"]

        if kind == "system":
            if record.get("subtype") in TURN_END_SYSTEM:
                self.set_phase(TURN_END, ts)
        elif kind == "assistant":
            self._local_command = False
            self._assistant(record, ts)
        elif synthetic_user(record):
            # Streszczenie po kompaktowaniu i wstawki isMeta nie zmieniają fazy ani nie kasują pytania.
            if _text((record.get("message") or {}).get("content")).startswith(LOCAL_COMMAND_CAVEAT):
                self._local_command = True
        else:
            self._user(record, ts)

    def _assistant(self, record: dict, ts: float) -> None:
        message = record.get("message") or {}
        model = message.get("model") if isinstance(message, dict) else None
        if isinstance(model, str) and model and not model.startswith("<"):
            self.model = model  # ostatni prawdziwy model (po /model liczy się nowy)
        if record.get("isApiErrorMessage"):
            self.set_phase(TURN_END, ts)
            return
        uses = [c for c in _content(message) if c.get("type") == "tool_use"]
        text = "\n".join(str(c.get("text") or "") for c in _content(message) if c.get("type") == "text").strip()
        if isinstance(message.get("content"), str):
            text = message["content"].strip()
        if uses:
            self.said("", ts)  # narzędzie po tekście — wcześniejsze pytanie nie kończy tury
        elif text:
            self.said(text, ts)
        if uses:
            self.set_phase(TOOL_PENDING, ts)
            for use in uses:
                name = str(use.get("name") or "?")
                args = use.get("input") or {}
                self.pending[str(use.get("id"))] = Pending(
                    name, ts, asks_user=name in ASKS_USER, long_running=name in LONG_RUNNING,
                    text=ask_text(name, args) if name in ASKS_USER else describe_tool(name, args))
                self.last_action = describe_tool(name, args)
        elif message.get("stop_reason") in ("end_turn", "stop_sequence", "max_tokens", "refusal"):
            self.set_phase(TURN_END, ts)
        elif self.phase != TOOL_PENDING:
            self.set_phase(MID_TURN, ts)

    def _user(self, record: dict, ts: float) -> None:
        content = (record.get("message") or {}).get("content")
        local, self._local_command = self._local_command, False
        if isinstance(content, list):
            results = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_result"]
            for result in results:
                self.pending.pop(str(result.get("tool_use_id")), None)
            if results:
                if not self.pending:
                    self.set_phase(MID_TURN, ts)
                return
        text = _text(content)
        if local and text.startswith(COMMAND_ECHO):
            return  # echo polecenia lokalnego (/model, /compact…) — użytkownik nie odpisał agentowi
        if text.startswith("[Request interrupted by user"):
            self.set_phase(TURN_END, ts)
            self.last_action = "Przerwano"
            self.said("", ts)  # użytkownik przerwał — pytanie sprzed przerwania nie czeka
        elif text.startswith(LOCAL_COMMAND_PREFIXES):
            self.set_phase(TURN_END, ts)
        else:
            if not text.startswith(INJECTED_PREFIXES):
                self.last_user_ts = max(self.last_user_ts, ts)  # użytkownik napisał (odpowiedź na pytanie z tekstu)
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


def ask_text(name: str, args: dict) -> str:
    """Treść pytania do użytkownika: input.questions[].question (AskUserQuestion, jak
    coordination/claude.py) albo plan do zatwierdzenia (ExitPlanMode, pierwsza linia planu)."""
    if not isinstance(args, dict):
        args = {}
    if name == "ExitPlanMode":
        lines = [re.sub(r"^\s*#+\s*", "", line).strip() for line in str(args.get("plan") or "").splitlines()]
        first = next((line for line in lines if line), "")
        return shorten("Plan do zatwierdzenia" + (f": {first}" if first else ""), 300)
    questions = args.get("questions")
    if isinstance(questions, list):
        texts = [str(q.get("question") or "").strip() for q in questions if isinstance(q, dict)]
        return "\n".join(t for t in texts if t)
    return str(args.get("question") or "").strip()


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
