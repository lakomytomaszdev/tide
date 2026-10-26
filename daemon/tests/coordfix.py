"""Syntetyczny świat koordynacji dla testów: dzienniki o kształcie prawdziwych (bez ich treści).

Kształty wpisów sprawdzone tylko do odczytu na prawdziwych dziennikach (26.10.2026):
- Claude Code: zlecenie `Agent` (description, subagent_type, prompt, model, run_in_background),
  tool_result z `toolUseResult.status = "async_launched"` i `agentId`, powiadomienie
  `<task-notification>` w attachment `queued_command` albo w wiadomości user, raport przez
  SubagentHandback (`origin.kind = "peer"`, `handbackReportText`), `SendMessage` z `resumedAgentId`,
  `TaskStop`, `AskUserQuestion`, szum: queue-operation, api-request-blob, zadania powłoki w tle,
  `subagents/agent-<id>.jsonl` i `.meta.json`,
- Codex: session_meta z `source.subagent.thread_spawn` (parent_thread_id, depth, agent_path,
  agent_nickname, agent_role), recenzent zgód (`subagent.other = "guardian"`), spawn_agent,
  request_user_input, tury task_started/task_complete z `last_agent_message`.

Zegar: NOW = 2026-10-26 12:00Z. Świat (build_world):

  workspace/demo            repozytorium (sam katalog .git z HEAD na master), plan
                            docs/agents/plans/2026-10-26-release.md (prefiks TOM, tracker youtrack),
                            mapa migracji TOM-99 → LOG-7
  koordynator Claude        CLAUDE_COORD w workspace/demo, gałąź feat/tom-20-release (TOM-20 to on sam):
    TOM-139  implementer skończył, review „APPROVE”, bez uwag           → gotowe
    TOM-140  implementer skończył, review: 1 MAJOR, 2 NIT (raport przez SubagentHandback),
             SendMessage wznowił implementera (poprawki, trwa)            → w toku
    TOM-141  implementer pracuje, AskUserQuestion wymienia TOM-141        → czeka
    TOM-142  implementer skończył, recenzent pracuje                      → review
    TOM-143  podagent na pierwszym planie (wynik od razu)                → czeka na review / gotowe
    TOM-144  zatrzymany (TaskStop)                                       → w toku, zatrzymany
    TOM-99   opis „TOM-99 old key” → LOG-7 z mapy migracji
    „Survey test suites” bez ID (Explore) i „Review survey test suites” (dołącza)
    plan: TOM-145 odrzucone, TOM-146 i TOM-147 todo (bez agentów)
  koordynator Codex         CODEX_ROOT w workspace/demo: podagenci tom150_impl (worker, skończył),
                            tom150_review (code-reviewer, „Verdict: APPROVE”), wnuk tom151_probe
                            (explorer, pracuje), recenzent zgód (szum), pytanie request_user_input
  zwykła sesja Claude       bez podagentów — nie jest koordynatorem
"""
import json
import os

from tide_daemon.agents.claude import escape_cwd
from tide_daemon.agents.timeutil import iso, parse_ts

NOW = parse_ts("2026-10-26T12:00:00Z")
MIN = 60
CLAUDE_COORD = "c0000000-0000-4000-8000-00000000c001"
CLAUDE_SINGLE = "c0000000-0000-4000-8000-00000000c002"
CODEX_ROOT = "0000000d-0000-4000-8000-000000000001"
CODEX_IMPL = "0000000d-0000-4000-8000-000000000002"
CODEX_REVIEW = "0000000d-0000-4000-8000-000000000003"
CODEX_PROBE = "0000000d-0000-4000-8000-000000000004"
CODEX_GUARDIAN = "0000000d-0000-4000-8000-000000000005"
TOKEN_SENTINEL = "perm-test-token-should-never-leak"

PLAN = """---
tide-plan: 1
title: Release candidate TOM-20
tracker: youtrack
tracker-url: {url}
tracker-project: LOG
ticket-prefix: TOM
coordinator: claude
session: {session}
status: active
updated: {updated}
---

| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| TOM-139 | Unikalne filtry w wyszukiwaniu | gotowe | `tom-139-filters` | implementer | |
| TOM-140 | Distribution guard dla kompaktowej dystrybucji | w-toku | `tom-140-guard` | implementer | po review: 1 MAJOR |
| TOM-141 | Migracja epików z Lineara | w-toku | | implementer | |
| TOM-145 | Eksport do PDF | odrzucone | | | poza zakresem RC |
| TOM-146 | Eksport raportu do CSV z filtrami | todo | | | |
| TOM-147 | Release notes RC | todo | | | |

## Notatki

Wolny tekst pod tabelą nie jest czytany.
"""


def at(minutes_ago: float) -> str:
    return iso(NOW - minutes_ago * MIN)


class ClaudeLog:
    """Dziennik sesji Claude Code o kształcie prawdziwego (pola rekordów jak w wersji 2.x)."""

    def __init__(self, root: str, session: str, cwd: str, branch: str):
        self.dir = os.path.join(root, "projects", escape_cwd(cwd))
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, f"{session}.jsonl")
        self.subdir = os.path.join(self.dir, session, "subagents")
        self.session, self.cwd, self.branch = session, cwd, branch
        self.lines: list[dict] = []
        self._n = 0

    def _base(self, kind: str, minutes_ago: float, **extra) -> dict:
        self._n += 1
        record = {"parentUuid": None, "isSidechain": False, "userType": "external", "cwd": self.cwd,
                  "sessionId": self.session, "version": "2.9.0", "gitBranch": self.branch, "type": kind,
                  "uuid": f"u-{self._n:04d}", "timestamp": at(minutes_ago)}
        record.update(extra)
        self.lines.append(record)
        return record

    def user_text(self, text: str, minutes_ago: float):
        return self._base("user", minutes_ago, message={"role": "user", "content": text})

    def assistant_text(self, text: str, minutes_ago: float, stop="end_turn"):
        return self._base("assistant", minutes_ago, message={
            "model": "claude-opus-5-5", "role": "assistant", "stop_reason": stop,
            "content": [{"type": "text", "text": text}]})

    def tool_use(self, use_id: str, name: str, args: dict, minutes_ago: float):
        return self._base("assistant", minutes_ago, requestId="req", message={
            "model": "claude-opus-5-5", "role": "assistant", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": use_id, "name": name, "input": args}]})

    def tool_result(self, use_id: str, content, minutes_ago: float, extra=None, error=False):
        block = {"type": "tool_result", "tool_use_id": use_id, "content": content}
        if error:
            block["is_error"] = True
        return self._base("user", minutes_ago, message={"role": "user", "content": [block]}, toolUseResult=extra)

    def spawn(self, use_id: str, agent_id: str, description: str, kind: str, minutes_ago: float,
              background=True, prompt: str = "", model: str = "sonnet", launched=True):
        args = {"description": description, "subagent_type": kind, "prompt": prompt or f"Zadanie: {description}",
                "model": model}
        if background:
            args["run_in_background"] = True
        self.tool_use(use_id, "Agent", args, minutes_ago)
        if background and launched:
            text = (f"Async agent launched successfully.\nagentId: {agent_id} (internal ID - do not mention to user. "
                    "Use SendMessage with to: '" + agent_id + "' to continue this agent.)\nThe agent is working in the "
                    "background. You will be notified automatically when it completes.")
            self.tool_result(use_id, [{"type": "text", "text": text}], minutes_ago - 0.05, extra={
                "isAsync": True, "status": "async_launched", "agentId": agent_id, "description": description,
                "resolvedModel": "claude-sonnet-5", "prompt": args["prompt"],
                "outputFile": f"/tmp/claude-0/tasks/{agent_id}.output", "canReadOutputFile": True,
                "canContinueAgent": True})

    def fg_result(self, use_id: str, agent_id: str, text: str, minutes_ago: float):
        self.tool_result(use_id, [{"type": "text", "text": text + f"\nagentId: {agent_id} (for resuming)"}],
                         minutes_ago, extra={"status": "completed", "agentId": agent_id, "totalDurationMs": 240000,
                                             "totalTokens": 1000, "totalToolUseCount": 7, "content": []})

    def notification(self, task_id: str, status: str, minutes_ago: float, summary: str = "", result: str = "",
                     tool_use_id: str = "", queued=True, duration_ms=600000):
        body = (f"<task-notification>\n<task-id>{task_id}</task-id>\n"
                + (f"<tool-use-id>{tool_use_id}</tool-use-id>\n" if tool_use_id else "")
                + f"<output-file>/tmp/claude-0/tasks/{task_id}.output</output-file>\n<status>{status}</status>\n"
                + f"<summary>{summary}</summary>\n"
                + (f"<result>{result}</result>\n<usage><subagent_tokens>100</subagent_tokens><tool_uses>3</tool_uses>"
                   f"<duration_ms>{duration_ms}</duration_ms></usage>\n" if result or status == "completed" else "")
                + "</task-notification>")
        # Szum, który parser musi pominąć: kolejka i kopia zapytania do API.
        self.lines.append({"type": "queue-operation", "operation": "enqueue", "timestamp": at(minutes_ago),
                           "sessionId": self.session, "content": body})
        if queued:
            self._base("attachment", minutes_ago, attachment={
                "type": "queued_command", "prompt": body, "commandMode": "task-notification",
                "origin": {"kind": "task-notification"}, "delivery_id": "d", "runId": "r", "source_uuid": "s",
                "timestamp": at(minutes_ago)})
        else:
            self._base("user", minutes_ago, message={"role": "user", "content": body},
                       origin={"kind": "task-notification", "runId": "r"})
        self.lines.append({"type": "api-request-blob", "timestamp": at(minutes_ago - 0.01),
                           "message": {"content": [{"type": "text", "text": body}]}})

    def peer(self, agent_id: str, report: str, minutes_ago: float, attachment=False):
        origin = {"kind": "peer", "from": agent_id, "senderTaskId": agent_id, "name": agent_id, "handback": True,
                  "handbackReportText": report, "body": report}
        text = f'<agent-message from="{agent_id}">\n[subagent hand-back] The report follows.\n{report}\n</agent-message>'
        if attachment:
            self._base("attachment", minutes_ago, attachment={"type": "queued_command", "prompt": text,
                                                              "origin": origin, "commandMode": "prompt"})
        else:
            self._base("user", minutes_ago, message={"role": "user", "content": text}, origin=origin)

    def subagent(self, agent_id: str, agent_type: str, description: str, use_id: str, branch: str,
                 last_tool: tuple | None = None, report: str = "", minutes_ago: float = 1, background=True):
        os.makedirs(self.subdir, exist_ok=True)
        lines = [{"parentUuid": None, "isSidechain": True, "agentId": agent_id, "type": "user", "cwd": self.cwd,
                  "gitBranch": branch, "sessionId": self.session, "timestamp": at(minutes_ago + 5),
                  "message": {"role": "user", "content": f"Zadanie: {description}"}}]
        if last_tool:
            lines.append({"isSidechain": True, "agentId": agent_id, "type": "assistant", "cwd": self.cwd,
                          "gitBranch": branch, "timestamp": at(minutes_ago + 1), "message": {
                              "role": "assistant", "content": [{"type": "text", "text": "Sprawdzam strażnika."},
                                                               {"type": "tool_use", "id": "t1", "name": last_tool[0],
                                                                "input": last_tool[1]}]}})
        if report:
            lines.append({"isSidechain": True, "agentId": agent_id, "type": "assistant", "cwd": self.cwd,
                          "gitBranch": branch, "timestamp": at(minutes_ago), "message": {
                              "role": "assistant", "content": [{"type": "tool_use", "id": "t2",
                                                                "name": "SubagentHandback",
                                                                "input": {"message": report}}]}})
        path = os.path.join(self.subdir, f"agent-{agent_id}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.utime(path, (NOW - minutes_ago * MIN, NOW - minutes_ago * MIN))
        meta = {"agentType": agent_type, "description": description, "toolUseId": use_id, "spawnDepth": 1,
                "requestShape": "background" if background else "foreground", "requestNonInteractive": True,
                "model": "sonnet"}
        with open(os.path.join(self.subdir, f"agent-{agent_id}.meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh)

    def write(self, mtime_minutes_ago: float = 0.5):
        with open(self.path, "w", encoding="utf-8") as fh:
            for line in self.lines:
                fh.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.utime(self.path, (NOW - mtime_minutes_ago * MIN, NOW - mtime_minutes_ago * MIN))
        return self.path

    def append(self, records: list[dict]):
        with open(self.path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def codex_thread(root: str, thread_id: str, cwd: str, branch: str, minutes_ago: float, source=None,
                 events=(), mtime_minutes_ago: float | None = None) -> str:
    day = os.path.join(root, "sessions", "2026", "10", "26")
    os.makedirs(day, exist_ok=True)
    path = os.path.join(day, f"rollout-2026-10-26T11-00-00-{thread_id}.jsonl")
    meta = {"id": thread_id, "timestamp": at(minutes_ago), "cwd": cwd, "originator": "codex_cli_rs",
            "cli_version": "0.80.0", "source": source if source is not None else "cli",
            "git": {"branch": branch, "commit_hash": "abc"}, "base_instructions": {"text": "…"}}
    if isinstance(source, dict) and isinstance(source.get("subagent"), dict):
        spawn = source["subagent"].get("thread_spawn") or {}
        meta.update({k: v for k, v in spawn.items() if k in ("agent_nickname", "agent_role", "agent_path")})
        if spawn.get("parent_thread_id"):
            meta["parent_thread_id"] = spawn["parent_thread_id"]
            meta["session_id"] = CODEX_ROOT
    lines = [{"timestamp": at(minutes_ago), "type": "session_meta", "payload": meta},
             {"timestamp": at(minutes_ago), "type": "turn_context",
              "payload": {"cwd": cwd, "approval_policy": "never", "model": "gpt-5.3-codex"}}]
    lines += list(events)
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
    ago = minutes_ago if mtime_minutes_ago is None else mtime_minutes_ago
    os.utime(path, (NOW - ago * MIN, NOW - ago * MIN))
    return path


def ev(minutes_ago: float, kind: str, payload: dict) -> dict:
    return {"timestamp": at(minutes_ago), "type": kind, "payload": payload}


def turn(start: float, end: float | None, message: str = "") -> list[dict]:
    out = [ev(start, "event_msg", {"type": "task_started", "turn_id": f"t{start}"})]
    if end is not None:
        out.append(ev(end, "event_msg", {"type": "task_complete", "turn_id": f"t{start}", "last_agent_message": message,
                                         "duration_ms": int((start - end) * 60000)}))
    return out


def spawn_call(minutes_ago: float, call_id: str, task: str, agent_type: str, message: str) -> list[dict]:
    args = {"task_name": task, "agent_type": agent_type, "model": "gpt-5.3-codex", "reasoning_effort": "high",
            "message": message}
    return [ev(minutes_ago, "response_item", {"type": "function_call", "name": "spawn_agent", "call_id": call_id,
                                              "arguments": json.dumps(args)}),
            ev(minutes_ago - 0.05, "response_item", {"type": "function_call_output", "call_id": call_id,
                                                     "output": json.dumps({"task_name": task})})]


def spawn_source(parent: str, depth: int, task: str, nickname: str, role: str) -> dict:
    return {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": depth, "agent_path": f"/root/{task}",
                                          "agent_nickname": nickname, "agent_role": role}}}


REVIEW_MAJOR = """## Werdykt: REQUEST CHANGES

Przejrzałem 7 plików. **BLOCKER:** brak.

### MAJOR-1: guard nie obejmuje ścieżki z pustym manifestem — brak testu
- `DistributionGuard.cs:57`

### NIT-1: nazwa metody
### NIT-2: literówka w komentarzu
"""

REVIEW_OK = """**Werdykt: APPROVE.** Nie ma BLOCKER-ów ani MAJOR-ów. MAJOR-1 z poprzedniej rundy jest zamknięty.
- NIT-1: zamknięty.
"""


def build_world(tmp: str, plan_url: str = "http://127.0.0.1:9", with_plan: bool = True) -> dict:
    claude_root = os.path.join(tmp, "claude")
    codex_root = os.path.join(tmp, "codex")
    repo = os.path.join(tmp, "workspace", "demo")
    os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
    with open(os.path.join(repo, ".git", "HEAD"), "w", encoding="utf-8") as fh:
        fh.write("ref: refs/heads/master\n")
    if with_plan:
        plans = os.path.join(repo, "docs", "agents", "plans")
        os.makedirs(plans, exist_ok=True)
        with open(os.path.join(plans, "2026-10-26-release.md"), "w", encoding="utf-8") as fh:
            fh.write(PLAN.format(url=plan_url, session=CLAUDE_COORD, updated=at(200)))
    migration = os.path.join(repo, "docs", "agents", "youtrack-migration")
    os.makedirs(migration, exist_ok=True)
    with open(os.path.join(migration, "id-map.csv"), "w", encoding="utf-8") as fh:
        fh.write("source_key,youtrack_id,url\nlinear:TOM-99,LOG-7,http://x/issue/LOG-7\n")

    log = ClaudeLog(claude_root, CLAUDE_COORD, repo, "feat/tom-20-release")
    log.user_text("Prowadź release candidate TOM-20 według planu.", 180)
    log.lines.append({"type": "ai-title", "aiTitle": "Release candidate TOM-20", "sessionId": CLAUDE_COORD})
    # TOM-139: implementer + review OK
    log.spawn("toolu_139a", "a139impl0000000001", "TOM-139: implement filters", "implementer", 170)
    log.notification("a139impl0000000001", "completed", 150, "Agent finished", "Zrobione: 2 commity, testy 212 OK.")
    log.spawn("toolu_139r", "a139rev00000000001", "TOM-139: review", "code-reviewer", 148)
    log.notification("a139rev00000000001", "completed", 140, "Review finished", REVIEW_OK)
    # TOM-140: implementer, review z MAJOR (raport przez SubagentHandback), wznowienie
    log.spawn("toolu_140a", "a140impl0000000001", "TOM-140: implement distribution guard", "implementer", 120,
              prompt="Implement TOM-140 distribution guard on branch tom-140-guard.")
    log.notification("a140impl0000000001", "completed", 89, "Agent finished", "Gotowe: guard i 6 testów.",
                     tool_use_id="toolu_140a", queued=False)
    log.spawn("toolu_140r", "a140rev00000000001", "Review TOM-140 distribution guard", "code-reviewer", 88)
    log.notification("a140rev00000000001", "completed", 80, "Review finished",
                     "This agent's report was delivered to you as a message from \"a140rev00000000001\" "
                     "(its SubagentHandback call). Read it there; it is not repeated here.")
    log.peer("a140rev00000000001", REVIEW_MAJOR, 80)
    log.tool_use("toolu_140s", "SendMessage", {"to": "a140impl0000000001", "summary": "poprawki",
                                               "message": "TOM-140: popraw MAJOR-1"}, 4)
    log.tool_result("toolu_140s", [{"type": "text", "text": "Message sent; agent resumed."}], 3.9,
                    extra={"success": True, "message": "Agent resumed", "resumedAgentId": "a140impl0000000001"})
    # TOM-141: implementer pracuje, pytanie do użytkownika o ten ticket
    log.spawn("toolu_141a", "a141impl0000000001", "TOM-141 migrate epics", "implementer", 30)
    log.tool_use("toolu_ask", "AskUserQuestion", {"questions": [{
        "question": "TOM-141: przenieść lokalny backlog do osobnego projektu?", "header": "Backlog",
        "multiSelect": False, "options": [{"label": "Tak", "description": "osobny projekt"}]}]}, 6)
    # TOM-142: implementer skończył, recenzent pracuje
    log.spawn("toolu_142a", "a142impl0000000001", "TOM-142: config validation", "implementer", 60)
    log.notification("a142impl0000000001", "completed", 24, "Agent finished", "Walidacja dodana.")
    log.spawn("toolu_142r", "a142rev00000000001", "TOM-142: review", "code-reviewer", 3)
    # TOM-143: na pierwszym planie
    log.spawn("toolu_143a", "a143impl0000000001", "TOM-143 retry on dropped connection", "implementer", 50,
              background=False)
    log.fg_result("toolu_143a", "a143impl0000000001", "Retry dodany, 1 commit.", 40)
    # TOM-144: zatrzymany
    log.spawn("toolu_144a", "a144impl0000000001", "TOM-144: timeouts", "implementer", 45)
    log.tool_use("toolu_stop", "TaskStop", {"task_id": "a144impl0000000001"}, 44)
    log.tool_result("toolu_stop", "Stopped", 43.9, extra={"message": "stopped", "task_id": "a144impl0000000001",
                                                          "task_type": "local_agent"})
    # TOM-99 (stary klucz Lineara) → LOG-7 z mapy migracji
    log.spawn("toolu_99a", "a099impl0000000001", "TOM-99 old key", "implementer", 100)
    log.notification("a099impl0000000001", "failed", 95, "Agent failed", "")
    # Bez ID: badanie i jego review (dołącza do tego samego ticketu)
    log.spawn("toolu_svy", "asurvey00000000001", "Survey test suites", "Explore", 130, background=False)
    log.fg_result("toolu_svy", "asurvey00000000001", "Zestawy testów opisane.", 128)
    log.spawn("toolu_svr", "asurveyrev00000001", "Review survey test suites", "code-reviewer", 127)
    log.notification("asurveyrev00000001", "completed", 125, "ok", "Verdict: APPROVE. No BLOCKER, no MAJOR.")
    # Szum: zadanie powłoki w tle i wpis podagenta w dzienniku głównym
    log.notification("bshell001", "completed", 20, "Background command finished")
    log.lines.append({"isSidechain": True, "type": "assistant", "timestamp": at(1),
                      "message": {"content": [{"type": "tool_use", "id": "x", "name": "Agent", "input": {}}]}})
    log.assistant_text("Czekam na podagentów.", 2, stop="end_turn")
    log.subagent("a140impl0000000001", "implementer", "TOM-140: implement distribution guard", "toolu_140a",
                 "tom-140-guard", last_tool=("Read", {"file_path": "/w/DistributionGuard.cs"}), minutes_ago=0.5)
    log.subagent("a140rev00000000001", "code-reviewer", "Review TOM-140 distribution guard", "toolu_140r",
                 "feat/tom-20-release", report=REVIEW_MAJOR, minutes_ago=80)
    log.subagent("a141impl0000000001", "implementer", "TOM-141 migrate epics", "toolu_141a", "feat/tom-20-release",
                 last_tool=("Bash", {"command": "make test", "description": "Testy migracji"}), minutes_ago=1)
    log.subagent("a142rev00000000001", "code-reviewer", "TOM-142: review", "toolu_142r", "feat/tom-20-release",
                 last_tool=("Grep", {"pattern": "Validate"}), minutes_ago=0.5)
    log.write()

    single = ClaudeLog(claude_root, CLAUDE_SINGLE, repo, "master")
    single.user_text("Zwykła sesja bez podagentów.", 30)
    single.assistant_text("Gotowe.", 29)
    single.write(29)

    # Codex: wątek główny, dwóch podagentów, wnuk i recenzent zgód.
    root_events = []
    root_events += [ev(90, "event_msg", {"type": "task_started", "turn_id": "r1"}),
                    ev(90, "event_msg", {"type": "user_message", "message": "Zrób TOM-150 i TOM-151."})]
    root_events += spawn_call(85, "call_s1", "tom150_impl", "worker", "Implement TOM-150 on branch tom-150-export.")
    root_events += spawn_call(60, "call_s2", "tom150_review", "code-reviewer", "Review TOM-150.")
    root_events.append(ev(10, "response_item", {"type": "function_call", "name": "request_user_input",
                                                "call_id": "call_q", "arguments": json.dumps({"questions": [
                                                    {"question": "TOM-151: czy sonda może czytać produkcję?"}]})}))
    codex_thread(codex_root, CODEX_ROOT, repo, "codex/tom-20-rc", 90, events=root_events, mtime_minutes_ago=10)
    impl_events = [ev(84, "event_msg", {"type": "user_message", "message": "Implement TOM-150 on branch tom-150-export."})]
    impl_events += turn(84, 62, "Eksport gotowy, 3 commity.")
    impl_events += spawn_call(70, "call_p", "tom151_probe", "explorer", "Probe TOM-151 data.")
    codex_thread(codex_root, CODEX_IMPL, repo, "tom-150-export", 84,
                 source=spawn_source(CODEX_ROOT, 1, "tom150_impl", "Euler", "worker"), events=impl_events,
                 mtime_minutes_ago=62)
    codex_thread(codex_root, CODEX_REVIEW, repo, "tom-150-export", 59,
                 source=spawn_source(CODEX_ROOT, 1, "tom150_review", "Noether", "code-reviewer"),
                 events=turn(59, 50, "Verdict: APPROVE. BLOCKER: none. MAJOR: none. 1 NIT."), mtime_minutes_ago=50)
    codex_thread(codex_root, CODEX_PROBE, repo, "tom-150-export", 69,
                 source=spawn_source(CODEX_IMPL, 2, "tom151_probe", "Hopper", "explorer"),
                 events=turn(69, None), mtime_minutes_ago=0.5)
    codex_thread(codex_root, CODEX_GUARDIAN, repo, "codex/tom-20-rc", 40,
                 source={"subagent": {"other": "guardian"}}, events=turn(40, 39, "approved"), mtime_minutes_ago=39)
    return {"claude": claude_root, "codex": codex_root, "repo": repo, "log": log}
