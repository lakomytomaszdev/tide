"""Żywy, czytelny raport (PT-24) i poprawki z diagnozy PT-23:

- A/B: „czeka” = agent stoi teraz; treść z prawdziwego pytania (AskUserQuestion, request_user_input) albo
  wywołania do zgody (waitingText), nigdy pytanie z tekstu skończonej tury; pytanie z tekstu → najwyżej
  „zostawił pytanie” w W toku i tylko bez odpowiedzi użytkownika. Syntetyczne dzienniki odtwarzają prawdziwy
  przypadek: pytanie na końcu tury → odpowiedź → AskUserQuestion → odpowiedź,
- C: „Czeka na mnie” składają reguły, model może tylko przeredagować pozycję z tym samym ref,
- D: tablica koordynacji zgodna z obszarem Agents (zgoda bez pytania w dzienniku, pytanie bez ID),
- E: nieaktualne pozycje oznaczone na żywo (Day, Days, Status), nie liczą się do plakietek,
- raport dla ludzi: złote przykłady (fakty → dobre i złe wzorce), walidacja odpowiedzi modelu, „Dziś
  w skrócie”, projekty z samymi porządkami w jednej linii,
- żywe odświeżanie: zdarzenia, zbiorczo, odstęp; notki po tickecie z powiadomieniem (zbiorczo, bez
  powtórek po restarcie usługi).

Prawdziwy `claude` nigdy nie jest wołany (atrapa przez $TIDE_CLAUDE_BIN), prawdziwe powiadomienia też nie
(atrapa serwera na prywatnej magistrali)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from types import SimpleNamespace

from . import reportfix
from .privatebus import PrivateBus, requires_dbus, wait_until
from .reportfix import H, NOW, START, _claude, _write_jsonl
from .test_report_standup import FAKE_CLAUDE
from tide_daemon import reports
from tide_daemon.agents import state as agent_state
from tide_daemon.agents.claude import ClaudeLog
from tide_daemon.agents.codex import CodexLog
from tide_daemon.agents.scanner import _aggregate, scan
from tide_daemon.agents.timeutil import iso
from tide_daemon.coordination import board as board_mod
from tide_daemon.coordination.model import Coordinator, Question
from tide_daemon.reports import data, live, notes, render, schedule, sessions, standup, summarize
from tide_daemon.report_monitor import ReportMonitor
from tide_daemon.tabmarks import palette_candidates

FAKE_NOTIFY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_notifications.py")
NOTIFY = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
_OLD_TZ = None


def setUpModule():
    global _OLD_TZ
    _OLD_TZ = os.environ.get("TZ")
    os.environ["TZ"] = reportfix.TZ
    time.tzset()


def tearDownModule():
    if _OLD_TZ is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = _OLD_TZ
    time.tzset()


def assistant_text(sid, cwd, ts, text):
    return _claude(sid, cwd, "main", ts, "assistant",
                   message={"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]})


def turn_end(sid, cwd, ts):
    return {"type": "system", "subtype": "turn_duration", "isSidechain": False, "timestamp": iso(ts), "cwd": cwd,
            "gitBranch": "main", "sessionId": sid}


def user(sid, cwd, ts, text):
    return _claude(sid, cwd, "main", ts, "user", message={"role": "user", "content": text})


def tool_use(sid, cwd, ts, use_id, name, args):
    return _claude(sid, cwd, "main", ts, "assistant",
                   message={"role": "assistant", "stop_reason": "tool_use",
                            "content": [{"type": "tool_use", "id": use_id, "name": name, "input": args}]})


def tool_result(sid, cwd, ts, use_id, text="ok"):
    return _claude(sid, cwd, "main", ts, "user",
                   message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]})


def feed(cls, records, path="/x/s.jsonl"):
    log = cls(path)
    for r in records:
        log.feed(r)
    return log


def unit_fact(session_fact, title="Wydanie"):
    return {"project": "x", "plans": [], "tickets": [], "questions": [], "conflicts": [], "coordinatorSessions": [],
            "units": [{"kind": "session", "branch": "", "title": title, "worktree": "", "commits": [], "count": 0,
                       "chores": 0, "sessions": [session_fact], "ticket": "", "category": "work", "areas": []}]}


# --- A/B: na co agent czeka ---------------------------------------------------------------------------

class WaitingTextTest(unittest.TestCase):
    """Parsery obszaru Agents: Pending.text, waiting_text i last_user_ts."""

    def test_claude_question_permission_and_plan(self):
        question = "Który wariant wybierasz?"
        log = feed(ClaudeLog, [user("s", "/w", 100, "zrób"),
                               tool_use("s", "/w", 110, "q", "AskUserQuestion",
                                        {"questions": [{"question": question, "header": "Wariant"}]})])
        self.assertEqual(agent_state.derive_state(log, 500, None, None, lambda ts: False)[:2], ("waiting", "question"))
        self.assertEqual(agent_state.waiting_text(log), question)

        log = feed(ClaudeLog, [tool_use("s", "/w", 100, "b", "Bash", {"command": "git push", "description": "Push na serwer"})])
        self.assertEqual(agent_state.derive_state(log, 200, None, None, lambda ts: False)[:2], ("waiting", "approval"))
        self.assertEqual(agent_state.waiting_text(log), "Bash · Push na serwer")

        log = feed(ClaudeLog, [tool_use("s", "/w", 100, "p", "ExitPlanMode", {"plan": "\n# Migracja bazy\n\n1. krok"})])
        self.assertEqual(agent_state.waiting_text(log), "Plan do zatwierdzenia: Migracja bazy")

        log.feed(tool_result("s", "/w", 120, "p"))
        self.assertEqual(agent_state.waiting_text(log), "", "odpowiedź zdejmuje czekanie")

    def test_claude_last_user_ts_ignores_tool_results_and_injected(self):
        log = feed(ClaudeLog, [user("s", "/w", 100, "start"), assistant_text("s", "/w", 110, "Pushować?"),
                               turn_end("s", "/w", 111)])
        self.assertEqual(log.last_user_ts, reportfix.parse_ts(iso(100)))
        log.feed(user("s", "/w", 120, "<task-notification>x</task-notification>"))
        log.feed(tool_result("s", "/w", 121, "zz"))
        self.assertEqual(log.last_user_ts, reportfix.parse_ts(iso(100)), "wstawki i wyniki narzędzi to nie użytkownik")
        log.feed(user("s", "/w", 130, "tak, pushuj"))
        self.assertEqual(log.last_user_ts, reportfix.parse_ts(iso(130)))

    def test_codex_request_user_input_and_async(self):
        meta = {"timestamp": iso(100), "type": "session_meta", "payload": {"id": "c1", "cwd": "/w"}}
        call = {"timestamp": iso(110), "type": "response_item",
                "payload": {"type": "function_call", "name": "request_user_input", "call_id": "k",
                            "arguments": json.dumps({"questions": [{"question": "Wydajemy dziś?"}]})}}
        log = feed(CodexLog, [meta, call], "/x/rollout-c1.jsonl")
        self.assertEqual(agent_state.waiting_text(log), "Wydajemy dziś?")
        async_call = dict(call, payload=dict(call["payload"], name="request_user_input_async", call_id="a"))
        out = {"timestamp": iso(111), "type": "response_item",
               "payload": {"type": "function_call_output", "call_id": "a", "output": "{\"accepted\": true}"}}
        log = feed(CodexLog, [meta, async_call, out], "/x/rollout-c1.jsonl")
        self.assertEqual(agent_state.derive_state(log, 500, None, None, lambda ts: False)[:2], ("waiting", "question"))
        self.assertEqual(agent_state.waiting_text(log), "Wydajemy dziś?")
        reply = {"timestamp": iso(120), "type": "response_item",
                 "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "tak"}],
                             "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]}}}
        log.feed(reply)
        self.assertEqual((agent_state.waiting_text(log), log.last_user_ts), ("", reportfix.parse_ts(iso(120))))

    def test_sessions_field_and_folded_subagent(self):
        tmp = tempfile.mkdtemp(prefix="tide-waiting-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "claude", "projects", "-w", "s1.jsonl")
        _write_jsonl(path, [user("s1", "/w", NOW - 60, "zrób"),
                            tool_use("s1", "/w", NOW - 50, "q", "AskUserQuestion", {"questions": [{"question": "A czy B?"}]})])
        (entry,) = scan(os.path.join(tmp, "claude"), os.path.join(tmp, "codex"), NOW)
        self.assertEqual((entry["state"], entry["waitingText"]), ("waiting", "A czy B?"))
        parent = {"state": "working", "reason": "", "since": "", "sinceTs": 1, "lastAction": "", "waitingText": "",
                  "lastActivityTs": 1, "lastActivity": ""}
        _aggregate(parent, [dict(parent, state="waiting", reason="approval", sinceTs=2, waitingText="shell · rm")])
        self.assertEqual((parent["state"], parent["waitingText"]), ("waiting", "shell · rm"))


class RealCaseTest(unittest.TestCase):
    """PT-23: „Pushować?” na końcu tury → użytkownik odpowiada → AskUserQuestion (czeka) → odpowiedź."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-pt23-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "s.jsonl")
        self.cwd = "/w/logtool"
        self.records = [
            user("s", self.cwd, NOW - 3 * H, "Zmień public_key_hash w konfiguracji"),
            assistant_text("s", self.cwd, NOW - 2.9 * H, "Zmieniłem public_key_hash.\n\nPushować?"),
            turn_end("s", self.cwd, NOW - 2.9 * H + 1),
            user("s", self.cwd, NOW - 2.5 * H, "Tak, ale najpierw zapytaj o środowisko"),
            tool_use("s", self.cwd, NOW - 2.4 * H, "q1", "AskUserQuestion",
                     {"questions": [{"question": "Na które środowisko wdrożyć: test czy produkcję?"}]}),
        ]

    def report(self, records, live=None):
        _write_jsonl(self.path, records)
        report = sessions.read_session(self.path, ClaudeLog, START, NOW)
        if live is not None:
            report.state, report.reason = live.get("state"), live.get("reason", "")
            report.waiting_text = live.get("waitingText", "") if report.state == "waiting" else ""
        return report

    def test_waiting_on_ask_user_question_not_old_text_question(self):
        s = self.report(self.records)
        self.assertEqual((s.state, s.reason), ("waiting", "question"))
        self.assertEqual(s.waiting_text, "Na które środowisko wdrożyć: test czy produkcję?")
        fact = standup._session_fact(s)
        self.assertEqual(fact["message"], s.waiting_text)
        self.assertEqual(fact["question"], "", "pytanie z tekstu ma odpowiedź użytkownika")
        waiting = standup.rules(unit_fact(fact))["waiting"]
        self.assertEqual([i["text"] for i in waiting],
                         ["Zmień public_key_hash w konfiguracji: agent pyta: „Na które środowisko wdrożyć: "
                          "test czy produkcję?”"])
        self.assertEqual((waiting[0]["sessionId"], waiting[0]["kind"]), ("claude:s", "blocked"))
        self.assertNotIn("Pushować", json.dumps(standup.rules(unit_fact(fact)), ensure_ascii=False))
        compact = summarize._compact(unit_fact(fact), summarize.LEVELS[0], "")
        self.assertNotIn("agentSaid", compact["work"][0]["sessions"][0], "sesja czeka — bez jej ostatniego tekstu")

    def test_answered_question_is_not_waiting(self):
        records = self.records + [tool_result("s", self.cwd, NOW - 2.3 * H, "q1", "test"),
                                  assistant_text("s", self.cwd, NOW - 2.2 * H, "Wdrożyłem na środowisko testowe."),
                                  turn_end("s", self.cwd, NOW - 2.2 * H + 1)]
        s = self.report(records)
        self.assertEqual((s.state, s.waiting_text), ("idle", ""))
        r = standup.rules(unit_fact(standup._session_fact(s)))
        self.assertEqual(r["waiting"], [])
        self.assertEqual(r["done"][0]["about"], "Wdrożyłem na środowisko testowe.")

    def test_question_at_end_of_turn_is_left_question_not_waiting(self):
        s = self.report(self.records[:3])
        self.assertIn(s.state, ("done", "idle"))
        fact = standup._session_fact(s)
        self.assertEqual(fact["question"], "Pushować?")
        r = standup.rules(unit_fact(fact))
        self.assertEqual(r["waiting"], [], "pytanie w tekście skończonej tury nigdy nie jest „czeka” (A)")
        self.assertEqual(r["doing"][0]["text"], "Zmień public_key_hash w konfiguracji — agent zostawił pytanie: „Pushować?”")
        self.assertIn("leftQuestion", summarize._compact(unit_fact(fact), summarize.LEVELS[0], "")["work"][0]["sessions"][0])

    def test_live_state_wins_over_log(self):
        # Obszar Agents wie lepiej (np. plik statusu Claude Code): sesja już nie czeka.
        s = self.report(self.records, live={"state": "done", "reason": ""})
        self.assertEqual(s.waiting_text, "")
        self.assertEqual(standup.rules(unit_fact(standup._session_fact(s)))["waiting"], [])
        live_world = sessions.collect  # collect bierze waitingText z obszaru Agents
        self.assertTrue(callable(live_world))


# --- C: „Czeka na mnie” z reguł -------------------------------------------------------------------------

class WaitingFromRulesTest(unittest.TestCase):
    def fact(self):
        waiting = {"id": "claude:s", "title": "Wydanie", "task": "", "tool": "claude", "state": "waiting",
                   "reason": "question", "message": "Test czy produkcja?", "waitingFor": "Test czy produkcja?",
                   "question": "", "full": "", "failing": "", "since": NOW - 600, "first": 0, "last": 0, "lastAction": ""}
        fact = unit_fact(waiting)
        fact["tickets"] = [{"id": "AB-3", "key": "AB-3", "title": "Termin", "state": "czeka", "note": "",
                            "question": "AB-3: wydajemy w piątek?", "branch": "", "category": "work", "planNote": "",
                            "description": "", "brief": "", "review": "", "result": "", "coordinator": "claude:k",
                            "questionTs": NOW - 900, "ts": NOW - 900}]
        return fact

    def test_model_can_only_reword_same_ref(self):
        fact = self.fact()
        model = {"x": {"headline": "", "theme": "", "done": [], "doing": [], "next": [], "chores": [],
                       "waiting": [{"ref": "AB-3", "text": "Termin wydania: piątek czy poniedziałek?"},
                                   {"ref": "", "text": "Sesja o zmianie public_key_hash pyta: „Pushować?”"}]}}
        merged = standup.merge(model, [fact])["x"]
        texts = [i["text"] for i in merged["waiting"]]
        self.assertEqual(texts, ["Termin wydania: piątek czy poniedziałek?", "Wydanie: agent pyta: „Test czy produkcja?”"])
        self.assertEqual(merged["waiting"][0]["sessionId"], "claude:k", "metadane z reguł zostają")

    def test_answered_coordinator_question_moves_to_doing(self):
        fact = self.fact()
        fact["coordinatorSessions"] = [{"id": "claude:k", "state": "working", "title": "Plan", "waitingFor": "",
                                        "message": "", "task": ""}]
        r = standup.rules(fact)
        self.assertNotIn("AB-3", [i["ref"] for i in r["waiting"]])
        self.assertIn("Termin — zostało pytanie bez odpowiedzi: „wydajemy w piątek?”", [i["text"] for i in r["doing"]])


# --- D: tablica koordynacji -----------------------------------------------------------------------------

class BoardWaitingTest(unittest.TestCase):
    def coord(self, questions=()):
        coord = Coordinator(id="claude:k", tool="claude", session_id="k", path="/p", cwd="/w", branch="feat/x")
        for q in questions:
            coord.questions[q.id] = q
        return coord

    def test_permission_prompt_gives_board_question(self):
        live = {"id": "claude:k", "state": "waiting", "reason": "approval", "sinceTs": NOW - 60,
                "waitingText": "Bash · git push"}
        b = board_mod.build(self.coord(), live=live, now=NOW)
        self.assertEqual(b["question"], {"text": "Bash · git push", "ts": NOW - 60, "kind": "approval"})
        self.assertEqual(b["state"], "waiting")
        self.assertEqual(b["counts"]["czeka"], 0)

    def test_question_without_ticket_id_stays_on_board(self):
        q = Question("q", NOW - 30, "Czy mogę wyczyścić bazę testową?")
        live = {"id": "claude:k", "state": "waiting", "reason": "question", "sinceTs": NOW - 30}
        b = board_mod.build(self.coord([q]), live=live, now=NOW)
        self.assertEqual(b["question"]["text"], "Czy mogę wyczyścić bazę testową?")
        self.assertEqual([t["state"] for t in b["tickets"]], [], "bez przypadkowego ticketu w „czeka”")

    def test_not_waiting_coordinator_has_no_board_question(self):
        b = board_mod.build(self.coord(), live={"state": "working"}, now=NOW)
        self.assertIsNone(b["question"])


# --- E: na żywo -------------------------------------------------------------------------------------

def day_json(day="2026-10-26"):
    return {"version": 1, "day": day, "title": "x", "counts": {"projects": 1, "waiting": 3},
            "brief": ["alpha — skończone: indeks."],
            "projects": [{"name": "alpha", "slug": "alpha", "path": "/w/alpha", "theme": "", "headline": "",
                          "counts": {"done": 1, "waiting": 3}, "hot": True,
                          "done": [{"ref": "", "text": "Indeks"}], "doing": [], "next": [], "chores": [],
                          "waiting": [{"ref": "", "text": "A: agent pyta: „X?”", "kind": "blocked",
                                       "sessionId": "claude:a", "since": NOW - 600},
                                      {"ref": "", "text": "B: agent pyta: „Y?”", "kind": "blocked",
                                       "sessionId": "claude:b", "since": NOW - 600},
                                      {"ref": "", "text": "Konflikt numerów: TIDE-3", "kind": "attention"}]}]}


class LiveDayTest(unittest.TestCase):
    def test_stale_items_now_list_counts_and_copy(self):
        sessions_now = [
            {"id": "claude:a", "state": "waiting", "reason": "question", "sinceTs": NOW - 590, "waitingText": "X?",
             "cwd": "/w/alpha/sub", "project": "alpha", "title": "A"},
            {"id": "claude:b", "state": "waiting", "reason": "approval", "sinceTs": NOW - 30,
             "waitingText": "Bash · make install", "cwd": "/w/alpha", "project": "alpha", "title": "B"},
            {"id": "claude:c", "state": "done"},
        ]
        out = data.apply_live(day_json(), "2026-10-26", sessions_now)
        waiting = out["projects"][0]["waiting"]
        self.assertEqual([i["stale"] for i in waiting], [False, True, False],
                         "B czeka już na coś innego (później) — pozycja nieaktualna; konflikt nie zależy od sesji")
        self.assertEqual(out["counts"]["waiting"], 2)
        self.assertEqual(out["counts"]["waitingNow"], 2)
        self.assertEqual([w["project"] for w in out["now"]], ["alpha", "alpha"])
        self.assertEqual(out["nowText"], "Teraz czeka na ciebie: alpha — agent pyta: „X?”; "
                                         "alpha — agent czeka na twoją zgodę (Bash · make install).")
        self.assertNotIn("Y?", out["projects"][0]["copyText"], "tekst do schowka bez nieaktualnych")
        self.assertIn("Teraz czeka na ciebie", out["copyAll"])
        # Nikt już nie czeka: wszystko z sesji nieaktualne, plakietka 1 (sam konflikt).
        quiet = data.apply_live(day_json(), "2026-10-26", [])
        self.assertEqual((quiet["counts"]["waiting"], quiet["now"], quiet["nowText"]), (1, [], ""))
        # Starsza doba to migawka — bez przeliczania.
        old = data.apply_live(day_json("2026-10-25"), "2026-10-26", [])
        self.assertNotIn("stale", json.dumps(old))
        self.assertNotIn("now", old)


class MonitorLiveTest(unittest.TestCase):
    """ReportMonitor na wygenerowanym raporcie świata reportfix: Day/Days/Status przeliczają „czeka”."""

    @classmethod
    def setUpClass(cls):
        from .privatebus import app
        app()
        cls.tmp = tempfile.mkdtemp(prefix="tide-living-")
        cls.p = reportfix.build(cls.tmp)
        cls.root = os.path.join(cls.tmp, "reports")
        cls.index, cls.report = reports.generate(
            cls.root, reportfix.DAY, START, NOW, palette=reports.load_palette(palette_candidates()),
            workspace=cls.p["work"], claude_root=cls.p["claude"], codex_root=cls.p["codex"],
            live=reportfix.live_sessions(cls.p), limits_result={"ok": True, "status": reportfix.STATUS},
            home=cls.p["home"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def monitor(self, current, notes_file=None, notifier=None, interval=live.INTERVAL, settle=live.SETTLE):
        state = os.path.join(tempfile.mkdtemp(dir=self.tmp), "state.json")
        schedule.save_state(state, schedule.State(day=reportfix.DAY, start=START, end=NOW, projects=2, waiting=1))
        settings, _ = schedule.load_settings(None)
        settings.summary = "rules"
        return ReportMonitor(settings, root=self.root, state_file=state, palette_paths=palette_candidates(),
                             workspace=self.p["work"], claude_root=self.p["claude"], codex_root=self.p["codex"],
                             sessions=lambda: current, clock=lambda: NOW + 60, fetch_limits=False,
                             notes_file=notes_file, note_notifier=notifier, live_interval=interval, live_settle=settle)

    def test_day_days_status_recompute_waiting(self):
        current = list(reportfix.live_sessions(self.p))
        m = self.monitor(current)
        day = json.loads(m.day_json(reportfix.DAY))
        alpha = next(p for p in day["projects"] if p["name"] == "alpha")
        self.assertEqual([i.get("stale") for i in alpha["waiting"]], [False])
        self.assertEqual(day["now"][0]["text"], reportfix.ASK_QUESTION)
        self.assertEqual(m.status()["waiting"], 1)
        # Użytkownik odpowiedział: sesja pracuje — pozycja nieaktualna, plakietki zero, bez nowego raportu.
        current[0] = dict(current[0], state="working", waitingText="")
        day = json.loads(m.day_json(reportfix.DAY))
        alpha = next(p for p in day["projects"] if p["name"] == "alpha")
        self.assertEqual([i.get("stale") for i in alpha["waiting"]], [True])
        self.assertEqual((day["counts"]["waiting"], day["now"]), (0, []))
        self.assertEqual(m.status()["waiting"], 0)
        days = json.loads(m.days_json())
        self.assertEqual(days["days"][0]["waiting"], 0)
        self.assertFalse(alpha["hot"])

    def test_waiting_change_emits_changed_without_regenerating(self):
        current = list(reportfix.live_sessions(self.p))
        m = self.monitor(current)
        emitted, started = [], []
        m.changed.connect(lambda: emitted.append(1))
        m._start = lambda scheduled, live=False: started.append(live)
        m.sessions_changed()
        self.assertEqual(emitted, [], "pierwszy odczyt to stan wyjściowy")
        current[0] = dict(current[0], state="working", waitingText="")
        m.sessions_changed()
        self.assertEqual(emitted, [1])
        self.assertIsNone(m.live.delay(NOW + 60), "zmiana „czeka” nie składa raportu")
        # Agent skończył pracę (pracuje → skończył) — raport do odświeżenia, zbiorczo.
        current[0] = dict(current[0], state="done")
        m.sessions_changed()
        self.assertEqual(m.live.delay(NOW + 60), live.SETTLE)

    def test_ticket_change_triggers_live_regeneration_with_batching(self):
        m = self.monitor([], interval=0, settle=0)
        started = []
        m._start = lambda scheduled, live=False: started.append((scheduled, live))
        board = {"id": "claude:k", "project": "alpha", "tickets": [{"key": "AB-1", "id": "AB-1", "state": "w-toku"}]}
        m.coordination_changed([board])
        self.assertIsNone(m.live.delay(NOW), "pierwszy odczyt tablic to stan wyjściowy")
        m.coordination_changed([dict(board, tickets=[{"key": "AB-1", "id": "AB-1", "state": "gotowe",
                                                      "lastActivityTs": NOW}])])
        self.assertEqual(m.live.delay(NOW + 60), 0)
        m._live_due()
        self.assertEqual(started, [(False, True)], "żywe złożenie bez powiadomienia")
        # Przed porannym raportem doby nic się nie składa.
        m2 = self.monitor([], interval=0, settle=0)
        schedule.save_state(m2.state_file, schedule.State(day="2026-10-25", end=NOW - 86400))
        m2._start = lambda scheduled, live=False: started.append("m2")
        m2.mark_dirty("zmiana ticketu")
        m2._live_due()
        self.assertNotIn("m2", started)
        self.assertIsNone(m2.live.delay(NOW + 60))

    def test_finished_ticket_note_notification_and_no_repeat_after_restart(self):
        notes_file = os.path.join(tempfile.mkdtemp(dir=self.tmp), "notes.json")
        sent = []
        notifier = SimpleNamespace(notify=lambda *a: sent.append(a))
        board = {"id": "claude:k", "project": "alpha", "repoPath": self.p["alpha"], "plan": None,
                 "tickets": [{"key": "AB-1", "id": "AB-1", "title": "Eksport do CSV", "state": "review",
                              "lastActivityTs": NOW, "done": "Lista przepisów da się pobrać jako plik CSV.",
                              "description": "Użytkownicy chcą otwierać listę w arkuszu."},
                             {"key": "AB-0", "id": "AB-0", "title": "Stare", "state": "gotowe", "lastActivityTs": NOW}]}
        m = self.monitor([], notes_file=notes_file, notifier=notifier)
        m.coordination_changed([board])   # pierwsze uruchomienie: stan wyjściowy, bez notek
        self.assertFalse(m._note_pool._threads, "bez notek przy pierwszym odczycie")
        done = dict(board, tickets=[dict(board["tickets"][0], state="gotowe"), board["tickets"][1]])
        captured = []
        m._write_notes = lambda day, finished, now: captured.append((day, finished))
        m.coordination_changed([done])
        self.assertEqual([(d, [t["key"] for _b, t in f]) for d, f in captured], [(reportfix.DAY, ["AB-1"])])
        written = notes.write_notes(captured[0][1], now=NOW + 60)
        future = Future()
        future.set_result(written)
        changed = []
        m.changed.connect(lambda: changed.append(1))
        m._deliver_notes(reportfix.DAY, future)
        self.assertEqual(changed, [1])
        (key, summary, body, target), = sent
        self.assertEqual(summary, "alpha: skończone — Lista przepisów da się pobrać jako plik CSV")
        self.assertEqual(target, f"{reportfix.DAY}/alpha")
        self.assertIn("Użytkownicy chcą otwierać listę w arkuszu.", body)
        # Day(): notka w „Skończone dziś” projektu i w tekście do schowka.
        day = json.loads(m.day_json(reportfix.DAY))
        alpha = next(p for p in day["projects"] if p["name"] == "alpha")
        self.assertEqual([n["ref"] for n in alpha["finished"]], ["AB-1"])
        self.assertIn("Skończone dziś:\n- AB-1 Lista przepisów da się pobrać jako plik CSV.", alpha["copyText"])
        self.assertEqual(day["counts"]["finished"], 1)
        # Restart usługi: ten sam plik stanu, te same tablice — bez drugiej notki i powiadomienia.
        m2 = self.monitor([], notes_file=notes_file, notifier=notifier)
        again = []
        m2._write_notes = lambda day, finished, now: again.append(finished)
        m2.coordination_changed([done])
        self.assertEqual(again, [])
        # Ticket wraca do pracy i kończy się znowu — nowa notka.
        m2.coordination_changed([dict(done, tickets=[dict(done["tickets"][0], state="w-toku")])])
        m2.coordination_changed([done])
        self.assertEqual(len(again), 1)


# --- raport dla ludzi: złote przykłady -----------------------------------------------------------------

UNACCEPTABLE = [
    "LA-035 gotowe",
    "Ticket 01 i ticket 02 dla wartości logów gotowe",
    "Kroki V3b1–V3b10 scalone",
    "Koordynator zlecił podagentowi review wyszukiwania",
    "Sesja o zmianie public_key_hash pyta: „Pushować?”",
    "Zmieniono plik docs/agents/plans/2026-10-27-plan.md",
    "Poprawka w main.py",
    "Added a search index to the app and fixed the tests",
    "Gałąź feat/search scalona do master, 12 commitów",
    "Tylko porządki w instrukcjach agentów w worktree",
]
ACCEPTABLE = [
    "Wyszukiwanie fraz nie zwraca już duplikatów.",
    "Można filtrować logi po części wartości i podejrzeć, jak linia się dzieli.",
    "Lista przepisów da się pobrać jako plik CSV.",
    "Karty na tablicy mówią po polsku, czym jest zadanie i co się dzieje teraz.",
]


class GoldenTest(unittest.TestCase):
    def test_patterns(self):
        for text in UNACCEPTABLE:
            self.assertTrue(standup.human_problem(text), text)
        for text in ACCEPTABLE:
            self.assertEqual(standup.human_problem(text), "", text)

    def test_validation_rejects_and_repairs_model_items(self):
        fact = {"project": "team-v2", "plans": [], "questions": [], "units": [], "conflicts": [],
                "tickets": [{"id": "LA-035", "key": "LA-35", "title": "Bazowa wersja", "state": "gotowe"}]}
        answer = {"projects": [{"name": "team-v2", "headline": "Koordynator skończył LA-035.",
                                "done": [{"ref": "", "text": "LA-035: bazową wersję można zatwierdzić z otwartym pytaniem",
                                          "about": "Plik docs/agents/plans/x.md"}]
                                + [{"ref": "LA-035", "text": t} for t in UNACCEPTABLE],
                                "doing": [{"ref": "", "text": "Import przepisu z linku jest w połowie",
                                           "next": "obsługa błędów sieci"}]}]}
        out = standup.validate(answer, [fact])["team-v2"]
        self.assertEqual(out["done"], [{"ref": "LA-035", "text": "Bazową wersję można zatwierdzić z otwartym pytaniem"}],
                         "ID z treści do ref, złe „about” znika, nieczytelne pozycje odrzucone")
        self.assertEqual(out["headline"], "Zrobione: Bazową wersję można zatwierdzić z otwartym pytaniem. "
                                          "W toku: Import przepisu z linku jest w połowie.",
                         "zdanie z żargonem zastąpione zdaniem z pozycji")
        self.assertEqual(out["rejected"], {"done": len(UNACCEPTABLE)})
        self.assertEqual(out["doing"][0]["next"], "obsługa błędów sieci")

    def test_group_lost_to_validation_takes_rules(self):
        fact = {"project": "p", "plans": [], "questions": [], "conflicts": [], "coordinatorSessions": [],
                "units": [], "tickets": [{"id": "P-1", "key": "P-1", "title": "Eksport do CSV", "state": "gotowe",
                                          "note": "review OK", "result": "", "description": "", "planNote": "",
                                          "question": "", "branch": "", "review": "", "category": "work"}]}
        model = standup.validate({"projects": [{"name": "p", "theme": "Eksport danych.",
                                                "done": [{"ref": "P-1", "text": "P-1 done"}]}]}, [fact])
        merged = standup.merge(model, [fact])["p"]
        self.assertEqual([i["text"] for i in merged["done"]], ["Eksport do CSV"], "reguły zamiast odrzuconych")

    def test_rules_output_is_readable(self):
        """Fakty jak z prawdziwego dnia → reguły bez żargonu, ścieżek i samych numerów."""
        waiting = {"id": "claude:s", "title": "Konfiguracja", "task": "", "tool": "claude", "state": "waiting",
                   "reason": "approval", "message": "Bash · make install", "waitingFor": "Bash · make install",
                   "question": "", "full": "", "failing": "", "since": 1, "first": 0, "last": 0, "lastAction": "Bash · x"}
        fact = unit_fact(waiting, "Konfiguracja")
        fact["units"] += [
            {"kind": "merged", "branch": "feat/tide-9-export", "title": "tide-9 · export", "worktree": "",
             "commits": ["feat(export): csv writer", "test(export): cover"], "count": 2, "chores": 0, "sessions": [],
             "ticket": "", "into": "master", "category": "work", "areas": []},
        ] + [{"kind": "branch", "branch": f"codex/b{i}", "title": f"zmiana {i}", "worktree": "", "commits": ["c"],
              "count": 1, "chores": 0, "sessions": [], "ticket": "", "category": "work", "areas": []} for i in range(4)]
        fact["plans"] = [{"title": "Wyszukiwanie", "label": "", "goal": "", "category": "work", "coordinator": "claude",
                          "live": True, "source": "board", "done": 3, "total": 5, "recent": 1}]
        r = standup.rules(fact)
        texts = [r["theme"], r["headline"]] + [i.get(k, "") for g in standup.ALL_KEYS for i in r[g]
                                               for k in ("text", "about", "next")]
        for text in texts:
            problem = standup.human_problem(text)
            self.assertIn(problem, ("", "angielski"), f"{text!r}: {problem}")   # angielskie tytuły z gita — bez tłumacza
        self.assertEqual(r["waiting"][0]["text"], "Konfiguracja: agent czeka na twoją zgodę (Bash · make install)")

    def test_brief_from_model_and_rules(self):
        refs = {"LA-35": "LA-035"}
        good = ["team-v2: bazową wersję można już zatwierdzić, nawet z otwartym pytaniem.",
                "LogTool: wyszukiwanie przestało zwracać duplikaty."]
        bad = ["Koordynator zlecił 5 ticketów.", "LA-035 gotowe", "Sesja o zmianie public_key_hash pyta: „Pushować?”",
               "We added search to the app and it is done."]
        self.assertEqual(standup.validate_brief(good + bad, refs),
                         ["Team-v2: bazową wersję można już zatwierdzić, nawet z otwartym pytaniem.".replace("Team", "team"),
                          "LogTool: wyszukiwanie przestało zwracać duplikaty."])
        self.assertEqual(standup.validate_brief(good[:1] + bad, refs), [], "mniej niż dwa dobre — reguły")
        summaries = {"LogTool": {"done": [{"ref": "K-12", "text": "Wyszukiwanie bez duplikatów"}],
                                  "doing": [{"ref": "K-13", "text": "Warunki na kolumnach — trwa praca"}]},
                     "QuotaApp": {"chores": [{"ref": "", "text": "Instrukcje"}]},
                     "BO": {"chores": [{"ref": "", "text": "Tracker"}]}}
        brief = standup.rules_brief(summaries, ["LogTool", "QuotaApp", "BO"])
        self.assertEqual(brief, ["LogTool — skończone: wyszukiwanie bez duplikatów; w toku: warunki na kolumnach — trwa praca.",
                                 "Same porządki: QuotaApp, BO."])
        for sentence in brief:
            self.assertNotIn("K-1", sentence, "ID ticketów nie są treścią")

    def test_model_brief_in_summary_and_per_project_cache(self):
        tmp = tempfile.mkdtemp(prefix="tide-brief-")
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = os.path.join(tmp, "claude")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(FAKE_CLAUDE)
        os.chmod(fake, 0o755)
        answer_path = os.path.join(tmp, "answer.json")
        log = os.path.join(tmp, "calls.jsonl")

        def project(name, title):
            return SimpleNamespace(name=name, slug=name, is_repo=True, plans=[], to_clean=[], unpushed=[],
                                   main_dirty_count=0, main_branch="main", branches=[], sessions=[
                                       SimpleNamespace(id=f"claude:{name}", tool="claude", title=title, prompt=title,
                                                       state="done", reason="", interrupted=False, final_message="Gotowe.",
                                                       final_ts=1, last_user_ts=0, waiting_text="", since=0, first_ts=0,
                                                       last_ts=NOW, last_action="", runs=[], automation={}, branch="",
                                                       cwd="")],
                                   active=True, conflicts=[])
        report = SimpleNamespace(start=START, active=[project("alpha", "Indeks"), project("beta", "Eksport")])
        with open(answer_path, "w", encoding="utf-8") as fh:
            json.dump({"brief": ["alpha: wyszukiwanie działa bez skanowania plików.",
                                 "beta: listę można pobrać jako plik CSV."],
                       "projects": [{"name": "alpha", "headline": "Wyszukiwanie działa.",
                                     "done": [{"ref": "", "text": "Wyszukiwanie działa bez skanowania plików."}]},
                                    {"name": "beta", "headline": "Eksport gotowy.",
                                     "done": [{"ref": "", "text": "Listę można pobrać jako plik CSV."}]}]}, fh)
        env = dict(os.environ, TIDE_CLAUDE_BIN=fake, FAKE_CLAUDE_LOG=log, FAKE_CLAUDE_ANSWER=answer_path)
        first = summarize.summarize(report, mode="claude", cache_dir=tmp, env=env)
        self.assertEqual((first["source"], first["briefSource"], first["calls"]), ("model", "model", 1))
        self.assertEqual(first["brief"][1], "beta: listę można pobrać jako plik CSV.")
        summarize.write_cache(tmp, first["cache"])
        # Ten sam dzień, zmieniła się tylko beta: model dostaje tylko betę, alpha idzie jako „others”.
        report.active[1].sessions[0].final_message = "Gotowe, teraz też z nagłówkami."
        second = summarize.summarize(report, mode="claude", cache_dir=tmp, env=env)
        with open(log, encoding="utf-8") as fh:
            calls = [json.loads(line) for line in fh]
        request = json.loads(calls[-1]["stdin"])
        self.assertEqual([p["name"] for p in request["projects"]], ["beta"])
        self.assertEqual(request["others"], [{"name": "alpha", "headline": "Wyszukiwanie działa."}])
        self.assertEqual((second["described"], second["projects"]["alpha"]["source"]), (["beta"], "model"))
        summarize.write_cache(tmp, second["cache"])
        third = summarize.summarize(report, mode="claude", cache_dir=tmp, env=env)
        self.assertEqual(third["calls"], 0, "bez zmian w faktach — bez wywołania")

    def test_chores_only_projects_collapse_in_html(self):
        repos = [SimpleNamespace(name=n, slug=n, path=f"/w/{n}", sessions=[], commits=1, waiting=[], last_ts=NOW,
                                 plans=[]) for n in ("logtool", "QuotaApp", "BO")]
        standup_ = {"source": "rules", "brief": ["logtool — skończone: wyszukiwanie."], "projects": {
            "logtool": {"done": [{"ref": "", "text": "Wyszukiwanie"}]},
            "QuotaApp": {"chores": [{"ref": "", "text": "Instrukcje dla agentów"}]},
            "BO": {"chores": [{"ref": "", "text": "Tracker"}]}}}
        report = SimpleNamespace(day="2026-10-27", standup=standup_, notes={})
        page = render.overview(report, repos)
        self.assertEqual(page.count('<article class="card ov'), 1)
        self.assertIn("<summary>Porządki: QuotaApp, BO</summary>", page)
        self.assertIn("Dziś w skrócie", render.brief_section(report))


# --- żywe odświeżanie i powiadomienia -----------------------------------------------------------------

class LiveTimingTest(unittest.TestCase):
    def test_batching_and_interval(self):
        state = live.Live(interval=720, settle=90)
        self.assertIsNone(state.delay(1000))
        state.mark("zmiana ticketu", 1000)
        self.assertEqual(state.delay(1000), 90)
        state.mark("zmiana ticketu", 1050)
        self.assertEqual(state.delay(1060), 80, "seria zdarzeń — liczy się ostatnie")
        state.ran(1140)
        self.assertIsNone(state.delay(1141))
        state.mark("agent skończył pracę", 1200)
        self.assertEqual(state.delay(1200), 1140 + 720 - 1200, "najwyżej co INTERVAL")
        self.assertEqual(state.describe(), "agent skończył pracę ×1")
        steady = live.Live(interval=720, settle=90)
        for t in range(0, 2000, 60):   # zdarzenie co minutę — bez głodzenia
            steady.mark("zmiana ticketu", t)
        self.assertEqual(steady.delay(1980), 0, "najpóźniej INTERVAL od pierwszego zdarzenia")
        state.ran(1100)   # złożenie zaczęte przed zdarzeniem nie gasi go
        self.assertIsNotNone(state.delay(1300))

    def test_transitions_and_signatures(self):
        boards = [{"id": "c", "tickets": [{"key": "A-1", "state": "review"}]}]
        first, changes = live.transitions({}, boards)
        self.assertEqual(changes, [])
        _second, changes = live.transitions(first, [{"id": "c", "tickets": [{"key": "A-1", "state": "gotowe"}]}])
        self.assertEqual(changes, [("c|A-1", "review", "gotowe")])
        states, done = live.finished_turns({"s": "working", "t": "waiting"}, [{"id": "s", "state": "done"},
                                                                              {"id": "t", "state": "done"}])
        self.assertEqual(done, ["s"])
        a = live.waiting_signature([{"id": "s", "state": "waiting", "sinceTs": 1, "waitingText": "X?"}])
        b = live.waiting_signature([{"id": "s", "state": "waiting", "sinceTs": 1, "waitingText": "Y?"}])
        self.assertNotEqual(a, b)


class NotesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-notes-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plan = os.path.join(self.tmp, "plan.md")
        with open(self.plan, "w", encoding="utf-8") as fh:
            fh.write("# Plan\n\n| Ticket | Tytuł | Stan |\n| --- | --- | --- |\n| LR-2 | Eksport | gotowe |\n\n"
                     "## Co przetestować ręcznie\n\n- LR-2: pobierz CSV i otwórz w arkuszu\n- [ ] LR-3: coś innego\n"
                     "\n### LR-2\n\n- polskie znaki w nazwach kolumn\n\n## Dalej\n\n- LR-2: nie z tej sekcji\n")
        self.board = {"id": "claude:k", "project": "LogTool", "repoPath": "/w/logtool",
                      "plan": {"path": self.plan, "goal": "Eksport danych z LogToola."}}
        self.ticket = {"key": "LR-2", "id": "LR-2", "title": "Eksport do CSV", "state": "gotowe", "lastActivityTs": NOW,
                       "done": "Listę logów da się pobrać jako plik CSV.", "planNote": "check: manual",
                       "description": "Zespół chce analizować logi w arkuszu.", "review": {"text": "bez uwag"}}

    def test_tracker_baseline_and_restart(self):
        path = os.path.join(self.tmp, "state", "report-notes.json")
        boards = [dict(self.board, tickets=[dict(self.ticket, state="review")])]
        self.assertEqual(notes.Tracker(notes.Store(path)).update(boards, NOW, START), [])
        self.assertTrue(os.path.exists(path))
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        done = [dict(self.board, tickets=[self.ticket])]
        tracker = notes.Tracker(notes.Store(path))
        self.assertEqual([t["key"] for _b, t in tracker.update(done, NOW, START)], ["LR-2"])
        self.assertEqual(notes.Tracker(notes.Store(path)).update(done, NOW + 5, START), [], "po restarcie bez powtórki")
        # Pierwsze uruchomienie z ticketem już gotowym — tylko zapamiętany.
        other = os.path.join(self.tmp, "fresh.json")
        self.assertEqual(notes.Tracker(notes.Store(other)).update(done, NOW, START), [])
        # Skończony, gdy usługa nie działała, ale przed dobą raportu — bez notki.
        old = os.path.join(self.tmp, "old.json")
        notes.Tracker(notes.Store(old)).update([], NOW, START)
        stale = [dict(self.board, tickets=[dict(self.ticket, lastActivityTs=START - 10)])]
        self.assertEqual(notes.Tracker(notes.Store(old)).update(stale, NOW, START), [])

    def test_manual_checklist_and_rules_note(self):
        manual, items = notes.manual_check(self.board, self.ticket)
        self.assertTrue(manual)
        self.assertEqual(items, ["pobierz CSV i otwórz w arkuszu", "polskie znaki w nazwach kolumn"])
        note = notes.rules_note(self.board, self.ticket, manual, items)
        self.assertEqual(note["text"], "Listę logów da się pobrać jako plik CSV. Zespół chce analizować logi w arkuszu. "
                                       "Do sprawdzenia ręcznie: pobierz CSV i otwórz w arkuszu; polskie znaki w nazwach kolumn.")
        self.assertEqual(note["summary"], "Listę logów da się pobrać jako plik CSV")
        self.assertEqual(standup.human_problem(note["text"]), "")
        plain = dict(self.ticket, planNote="", done="", description="")
        self.assertEqual(notes.manual_check({"plan": None}, plain), (False, []))
        self.assertEqual(notes.rules_note({}, plain, False, [])["text"], "Skończone: Eksport do CSV.")
        # Karta tablicy pracy z check: manual (pole z PT-5) i specyfikacją.
        spec = os.path.join(self.tmp, "spec.md")
        with open(spec, "w", encoding="utf-8") as fh:
            fh.write("# Spec\n\n## Co przetestować ręcznie\n\n- zaloguj się jako gość\n")
        manual, items = notes.manual_check({"plan": None}, plain, lambda b, t: {"check": "manual", "spec": spec})
        self.assertEqual((manual, items), (True, ["zaloguj się jako gość"]))

    def test_model_note_and_fallback(self):
        fake = os.path.join(self.tmp, "claude")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(FAKE_CLAUDE)
        os.chmod(fake, 0o755)
        answer = os.path.join(self.tmp, "answer.json")
        log = os.path.join(self.tmp, "calls.jsonl")
        env = dict(os.environ, TIDE_CLAUDE_BIN=fake, FAKE_CLAUDE_LOG=log, FAKE_CLAUDE_ANSWER=answer)
        jobs = [(self.board, self.ticket), (self.board, dict(self.ticket, key="LR-4", id="LR-4", title="Filtr"))]
        with open(answer, "w", encoding="utf-8") as fh:
            json.dump({"notes": [{"key": "claude:k|LR-2", "text": "Logi można pobrać jako CSV i otworzyć w arkuszu. "
                                                            "Sprawdź ręcznie polskie znaki w nazwach kolumn.",
                                  "summary": "Eksport logów do CSV."},
                                 {"key": "claude:k|LR-4", "text": "Koordynator zlecił podagentowi filtr.",
                                  "summary": "Filtr"}]}, fh)
        written = notes.write_notes(jobs, mode="claude", env=env, now=NOW)
        self.assertEqual([(n["ref"], n["source"]) for n in written], [("LR-2", "model"), ("LR-4", "rules")],
                         "notka z żargonem odrzucona — reguły")
        self.assertEqual(written[0]["summary"], "Eksport logów do CSV")
        with open(log, encoding="utf-8") as fh:
            (call,) = [json.loads(line) for line in fh]
        self.assertEqual(call["argv"][call["argv"].index("--system-prompt") + 1], notes.NOTE_PROMPT)
        self.assertEqual(json.loads(call["stdin"])["tickets"][0]["checklist"],
                         ["pobierz CSV i otwórz w arkuszu", "polskie znaki w nazwach kolumn"])

    def test_batched_notifications(self):
        batcher = notes.Batcher(window=300)
        one = {"summary": "Eksport do CSV", "text": "Logi da się pobrać jako CSV.", "manual": True}
        first = batcher.add("LogTool", [one], 1000)
        self.assertEqual(first["summary"], "LogTool: skończone — Eksport do CSV")
        self.assertEqual(first["body"], "Logi da się pobrać jako CSV. Wymaga sprawdzenia ręcznego.")
        second = batcher.add("LogTool", [dict(one, summary="Filtr po dacie", manual=False)], 1200)
        self.assertEqual(second["key"], first["key"], "to samo powiadomienie się aktualizuje")
        self.assertEqual(second["summary"], "LogTool: skończone 2 zadania")
        self.assertEqual(second["body"], "• Eksport do CSV\n• Filtr po dacie\nDo sprawdzenia ręcznie: 1.")
        self.assertNotEqual(batcher.add("LogTool", [one], 1600)["key"], first["key"], "po oknie — nowe")
        self.assertNotEqual(batcher.add("Tide", [one], 1210)["key"], first["key"], "inny projekt — osobne")


@requires_dbus
class NoteNotifierTest(unittest.TestCase):
    def setUp(self):
        self.bus = PrivateBus("tide-note-notify-test")
        self.addCleanup(self.bus.stop)
        self.bus.spawn([sys.executable, FAKE_NOTIFY], NOTIFY)

    def notifies(self):
        reply = self.bus.call(NOTIFY, NOTIFY_PATH, NOTIFY, "Calls")
        return [c for c in json.loads(reply.arguments()[0]) if c["method"] == "Notify"]

    def test_replace_within_batch_and_open_reports_view(self):
        from PyQt6.QtCore import QMetaType
        from PyQt6.QtDBus import QDBusArgument
        from tide_daemon.notifications import NoteNotifier
        shown = []
        notifier = NoteNotifier(lambda item: shown.append(item) or True, bus=self.bus.bus)
        notifier.notify("LogTool#1", "LogTool: skończone — Eksport do CSV", "Logi <b>CSV</b>.", "2026-10-27/logtool")
        first = wait_until(self.notifies)[0]
        self.assertEqual((first["app"], first["summary"], first["body"], first["replaces"]),
                         ("Tide", "LogTool: skończone — Eksport do CSV", "Logi &lt;b&gt;CSV&lt;/b&gt;.", 0))
        self.assertEqual(first["actions"], ["default", "Otwórz", "open", "Otwórz"])
        wait_until(lambda: notifier.notice_of.get("LogTool#1"))
        notifier.notify("LogTool#1", "LogTool: skończone 2 zadania", "• a\n• b", "2026-10-27/logtool")
        second = wait_until(lambda: len(self.notifies()) == 2 and self.notifies()[1])
        self.assertEqual(second["replaces"], first["id"], "paczka projektu zastępuje poprzednie powiadomienie")
        wait_until(lambda: notifier.target_of.get(second["id"]))
        self.bus.call(NOTIFY, NOTIFY_PATH, NOTIFY, "Invoke", QDBusArgument(second["id"], QMetaType.Type.UInt.value), "open")
        self.assertEqual(wait_until(lambda: shown), ["2026-10-27/logtool"])


if __name__ == "__main__":
    unittest.main()
