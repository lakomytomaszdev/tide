"""Syntetyczny świat dla testów raportu porannego: repozytoria git z datowanymi commitami,
dzienniki Claude Code i Codexa, zaplanowane zadanie Codex Desktop i odpowiedź Limitów AI.

Zegar zamrożony: NOW = 2026-10-26 07:00 czasu Europe/Warsaw (06:00Z), okres = ostatnia doba.

  work/alpha                 master; w okresie 2 commity (jeden ze stopką Co-Authored-By: Claude),
                             commit sprzed okresu; w głównym katalogu zmiana i nieśledzony plik „<b>evil.txt”
  work/alpha-wt/search       feat/search: 2 commity w okresie, niescalone (praca tylko tutaj),
                             pracuje w nim sesja Claude „c-search” (ostatnia wiadomość z <script>)
  work/alpha-wt/old          feat/old: scalony, czysty, stary → do sprzątnięcia
  home/.codex/worktrees/b1/beta  codex/fix-upload (repozytorium beta): commit w okresie → agent Codex
  work/beta                  main; wątek Codexa z zaplanowanym zadaniem „Nocny przegląd”: 3 przebiegi
                             w okresie i 1 przed nim; plik z wypełnieniem, żeby zadziałało szukanie binarne
  work/gamma                 bez ruchu; scalony worktree gamma-wt/done → do sprzątnięcia
  sesja Claude „c-ask”       alpha, czeka na odpowiedź (stan z obszaru Agents)
  sesja Claude „c-old”       alpha, sprzed okresu → pomijana
  wątek podagenta Codexa     → pomijany
"""
import json
import os
import subprocess

from tide_daemon.agents.claude import escape_cwd
from tide_daemon.agents.timeutil import iso, parse_ts

TZ = "Europe/Warsaw"
NOW = parse_ts("2026-10-26T06:00:00Z")          # 07:00 w Warszawie (CET po zmianie czasu 25.10)
DAY = "2026-10-26"
START = NOW - 86400
H = 3600

THREAD = "0000000c-0000-4000-8000-000000000001"
CODEX_WT_THREAD = "0000000c-0000-4000-8000-000000000002"
SUBAGENT_THREAD = "0000000c-0000-4000-8000-000000000003"

INJECTION = '<script>alert("x")</script>'

STATUS = {
    "chatgpt": {"session": {"remaining": None, "reset": None}, "weekly": {"remaining": 91, "reset": "~poniedziałek 11:41"},
                "lastRead": "2026-10-26T06:58+01:00", "state": "ready"},
    "claude": {"session": {"remaining": 12, "reset": "dziś 09:00"}, "weekly": {"remaining": 34, "reset": "sobota 18:00"},
               "lastRead": "2026-10-26T06:59+01:00", "state": "ready"},
}


def _env(ts: float, name="Tomek", email="user@example.com") -> dict:
    stamp = f"@{int(ts)} +0000"
    return dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name,
                GIT_COMMITTER_EMAIL=email, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
                GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)


def git(cwd, *args, ts=NOW - 30 * 86400, **who) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=_env(ts, **who), check=True, capture_output=True,
                          text=True).stdout


def commit(cwd, name, ts, message=None, text=None, **who):
    with open(os.path.join(cwd, name), "a", encoding="utf-8") as fh:
        fh.write(text or f"{name} {ts}\nlinia\n")
    git(cwd, "add", name, ts=ts, **who)
    git(cwd, "commit", "-q", "-m", message or f"add {name}", ts=ts, **who)


def _write_jsonl(path: str, records: list[dict], pad_before: int = 0) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for i, record in enumerate(records):
            fh.write(json.dumps(record) + "\n")
            if i == 1 and pad_before:
                # Wypełnienie sprzed okresu (np. liczniki tokenów) — duży plik dla szukania binarnego.
                for n in range(pad_before):
                    fh.write(json.dumps({"timestamp": iso(START - 2 * H + n * 0.01), "type": "event_msg",
                                         "payload": {"type": "token_count", "info": {"n": n, "pad": "x" * 200}}}) + "\n")
    last = max((parse_ts(r.get("timestamp")) for r in records), default=0)
    if last:
        os.utime(path, (last, last))


def build(tmp: str) -> dict:
    p = {"tmp": tmp, "work": os.path.join(tmp, "work"), "home": os.path.join(tmp, "home"),
         "claude": os.path.join(tmp, "claude"), "codex": os.path.join(tmp, "codex"),
         "reports": os.path.join(tmp, "reports")}
    os.makedirs(p["work"])
    os.makedirs(p["home"])

    # --- alpha ---
    alpha = p["alpha"] = os.path.join(p["work"], "alpha")
    os.makedirs(alpha)
    git(alpha, "init", "-q", "-b", "master")
    commit(alpha, "README.md", NOW - 40 * 86400, "initial commit")
    p["old"] = os.path.join(p["work"], "alpha-wt", "old")
    git(alpha, "worktree", "add", "-q", "-b", "feat/old", p["old"], ts=NOW - 39 * 86400)
    commit(p["old"], "old.txt", NOW - 39 * 86400, "old feature")
    git(alpha, "merge", "-q", "--no-ff", "-m", "merge feat/old", "feat/old", ts=NOW - 38 * 86400)
    commit(alpha, "before.txt", NOW - 2 * 86400, "commit before the period")
    p["search"] = os.path.join(p["work"], "alpha-wt", "search")
    git(alpha, "worktree", "add", "-q", "-b", "feat/search", p["search"], ts=NOW - 2 * 86400)
    commit(alpha, "core.py", NOW - 20 * H, f"fix: handle {INJECTION} & \"quotes\"")
    commit(alpha, "agent.py", NOW - 10 * H, "feat: agent helper\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    commit(p["search"], "search.py", NOW - 5 * H, "feat(search): index builder")
    commit(p["search"], "search_test.py", NOW - 4 * H, "test(search): cover the index")
    with open(os.path.join(alpha, "README.md"), "a", encoding="utf-8") as fh:
        fh.write("zmiana\n")
    with open(os.path.join(alpha, "<b>evil.txt"), "w", encoding="utf-8") as fh:
        fh.write("x\n")

    # --- beta + worktree Codexa w ~/.codex/worktrees ---
    beta = p["beta"] = os.path.join(p["work"], "beta")
    os.makedirs(beta)
    git(beta, "init", "-q", "-b", "main")
    commit(beta, "app.py", NOW - 30 * 86400, "initial beta")
    p["codex_wt"] = os.path.join(p["home"], ".codex", "worktrees", "b1", "beta")
    git(beta, "worktree", "add", "-q", "-b", "codex/fix-upload", p["codex_wt"], ts=NOW - 3 * H)
    commit(p["codex_wt"], "upload.py", NOW - 2 * H, "fix(upload): retry on timeout")

    # --- gamma (bez ruchu) ---
    gamma = p["gamma"] = os.path.join(p["work"], "gamma")
    os.makedirs(gamma)
    git(gamma, "init", "-q", "-b", "master")
    commit(gamma, "g.txt", NOW - 50 * 86400, "gamma start")
    p["gamma_done"] = os.path.join(p["work"], "gamma-wt", "done")
    git(gamma, "worktree", "add", "-q", "-b", "feat/done", p["gamma_done"], ts=NOW - 50 * 86400)

    _claude_logs(p)
    _codex_logs(p)
    return p


def _claude(sid, cwd, branch, ts, kind, **extra) -> dict:
    record = {"type": kind, "isSidechain": False, "timestamp": iso(ts), "cwd": cwd, "gitBranch": branch,
              "sessionId": sid}
    record.update(extra)
    return record


def _claude_logs(p: dict) -> None:
    root = os.path.join(p["claude"], "projects")
    search, alpha = p["search"], p["alpha"]
    t = NOW - 6 * H
    records = [
        _claude("c-search", search, "feat/search", t, "user", message={"role": "user", "content": "Build the search index"}),
        _claude("c-search", search, "feat/search", t + 60, "assistant",
                message={"role": "assistant", "stop_reason": "tool_use",
                         "content": [{"type": "text", "text": "Zaczynam od testów."},
                                     {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "make test"}}]}),
        _claude("c-search", search, "feat/search", t + 70, "user",
                message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}),
        _claude("c-search", search, "feat/search", NOW - 3.5 * H, "assistant",
                message={"role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "Gotowe: indeks wyszukiwania działa.\n\n"
                                      f"- dodałem builder\n- testy przechodzą\n\nUwaga na {INJECTION} w danych."}]}),
        {"type": "system", "subtype": "turn_duration", "isSidechain": False, "timestamp": iso(NOW - 3.5 * H + 1),
         "cwd": search, "gitBranch": "feat/search", "sessionId": "c-search"},
        {"type": "ai-title", "aiTitle": "Search <b>index</b>", "sessionId": "c-search"},
    ]
    _write_jsonl(os.path.join(root, escape_cwd(search), "c-search.jsonl"), records)

    ask = [
        _claude("c-ask", alpha, "master", NOW - 40 * 60, "user", message={"role": "user", "content": "Plan the release"}),
        _claude("c-ask", alpha, "master", NOW - 30 * 60, "assistant",
                message={"role": "assistant", "stop_reason": "tool_use",
                         "content": [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": {}}]}),
        {"type": "custom-title", "customTitle": "Release plan", "sessionId": "c-ask"},
    ]
    _write_jsonl(os.path.join(root, escape_cwd(alpha), "c-ask.jsonl"), ask)

    old = [_claude("c-old", alpha, "master", NOW - 3 * 86400, "user", message={"role": "user", "content": "Old work"})]
    _write_jsonl(os.path.join(root, escape_cwd(alpha), "c-old.jsonl"), old)


def _codex_turn(turn: str, ts: float, result: str, scheduled: bool) -> list[dict]:
    trigger = "automation_heartbeat_scheduled" if scheduled else "composer"
    kinds = ["user.heartbeat"] if scheduled else ["user.text"]
    return [
        {"timestamp": iso(ts), "type": "event_msg",
         "payload": {"type": "task_started", "turn_id": turn, "turn_attribution": {"turn_id": turn, "turn_trigger": trigger}}},
        {"timestamp": iso(ts + 1), "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "check"}],
                     "internal_chat_message_metadata_passthrough": {"content_item_kinds": kinds}}},
        {"timestamp": iso(ts + 50), "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                     "content": [{"type": "output_text", "text": result}]}},
        {"timestamp": iso(ts + 60), "type": "event_msg",
         "payload": {"type": "task_complete", "turn_id": turn, "last_agent_message": result}},
    ]


def _codex_logs(p: dict) -> None:
    sessions = os.path.join(p["codex"], "sessions", "2026", "10", "20")
    meta = {"timestamp": iso(NOW - 6 * 86400), "type": "session_meta",
            "payload": {"id": THREAD, "timestamp": iso(NOW - 6 * 86400), "cwd": p["beta"], "originator": "Codex Desktop",
                        "source": "vscode", "git": {"branch": "main"}}}
    records = [meta, {"timestamp": iso(NOW - 6 * 86400 + 1), "type": "turn_context",
                      "payload": {"cwd": p["beta"], "approval_policy": "never"}}]
    records += _codex_turn("t0", START - H, "Przebieg sprzed okresu", True)
    records += _codex_turn("t1", NOW - 20 * H, "Wszystko zielone (1)", True)
    records += _codex_turn("t2", NOW - 12 * H, "Wszystko zielone (2)", True)
    records += _codex_turn("t3", NOW - 9 * H, "Ręczne pytanie — odpowiedź", False)
    records += _codex_turn("t4", NOW - 1 * H, f"Znalazłem błąd {INJECTION} w buildzie", True)
    _write_jsonl(os.path.join(sessions, f"rollout-2026-10-20T07-00-00-{THREAD}.jsonl"), records, pad_before=2000)

    day = os.path.join(p["codex"], "sessions", "2026", "10", "26")
    wt = [{"timestamp": iso(NOW - 3 * H), "type": "session_meta",
           "payload": {"id": CODEX_WT_THREAD, "timestamp": iso(NOW - 3 * H), "cwd": p["codex_wt"],
                       "originator": "codex_cli_rs", "source": "cli", "git": {"branch": "codex/fix-upload"}}}]
    wt += _codex_turn("u1", NOW - 3 * H + 10, "Poprawiłem ponawianie wysyłki.", False)
    _write_jsonl(os.path.join(day, f"rollout-2026-10-26T04-00-00-{CODEX_WT_THREAD}.jsonl"), wt)

    sub = [{"timestamp": iso(NOW - 2 * H), "type": "session_meta",
            "payload": {"id": SUBAGENT_THREAD, "cwd": p["beta"], "source": {"subagent": {"other": "guardian"}}}}]
    sub += _codex_turn("s1", NOW - 2 * H, "podagent", False)
    _write_jsonl(os.path.join(day, f"rollout-2026-10-26T05-00-00-{SUBAGENT_THREAD}.jsonl"), sub)

    with open(os.path.join(p["codex"], "session_index.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": THREAD, "thread_name": "Nocny przegląd buildu"}) + "\n")
    auto = os.path.join(p["codex"], "automations", "nocny-przeglad")
    os.makedirs(auto)
    with open(os.path.join(auto, "automation.toml"), "w", encoding="utf-8") as fh:
        fh.write('version = 1\nid = "nocny-przeglad"\nkind = "heartbeat"\nname = "Nocny przegląd"\n'
                 'prompt = "sprawdź build"\nstatus = "ACTIVE"\nrrule = "RRULE:FREQ=HOURLY;INTERVAL=4"\n'
                 f'target_thread_id = "{THREAD}"\n')


def live_sessions(p: dict) -> list[dict]:
    """Stan z obszaru Agents w chwili raportu (tak jak AgentMonitor.sessions)."""
    return [
        {"id": "claude:c-ask", "tool": "claude", "sessionId": "c-ask", "cwd": p["alpha"], "state": "waiting",
         "reason": "question", "sinceTs": NOW - 30 * 60, "lastActivityTs": NOW - 30 * 60, "canActivate": True},
        {"id": "claude:c-search", "tool": "claude", "sessionId": "c-search", "cwd": p["search"], "state": "idle",
         "reason": "", "sinceTs": NOW - 3.5 * H, "lastActivityTs": NOW - 3.5 * H},
    ]
