"""Pytanie na końcu tury jako „czeka na odpowiedź” (PT-29).

Przypadek z życia: sesja Twitch prowadzi grilling i kończy turę zwykłym tekstem („Pytanie 10: skąd
biorą się czat i alerty? … czy donacje … są w planach na start?”). Użytkownik nie odpisał, a /Agents
mówił `done` — tablica, widżet i raport nie pokazywały, że agent czeka. Teraz: `waiting`, powód `reply`,
`waitingText` z pytaniem, dopóki użytkownik nie napisze.

Do tego karta K-20 założona i połączona (`session`) ze skończoną już sesją nie może od razu dostać
„przeniesiona ręcznie po skończonej pracy” — to reguła tylko dla przeniesienia z Do sprawdzenia albo Gotowe.

Kompaktowanie (przegląd PT-29): sesja Twitch skończyła turę pytaniem („…Zgadzasz się?”), a godzinę później
Claude Code sam ją skompaktował („Compacted while idle”): compact_boundary, wpis „user” ze streszczeniem
(isCompactSummary), załączniki. Streszczenie liczyło się jako odpowiedź użytkownika — sesja `idle`,
`waitingText` pusty, a karta K-20 spadła do „gotowe” (koordynacja 1/1 scalona). Teraz kompaktowanie, echo
poleceń lokalnych i wstawki isMeta to nie odpowiedź; pytanie czeka, dopóki użytkownik naprawdę nie napisze.

Dzienniki syntetyczne, tablica w katalogu tymczasowym.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from tide_daemon import attention
from tide_daemon.agents import AgentScanner
from tide_daemon.agents.claude import ClaudeLog, escape_cwd, tail_has_turn
from tide_daemon.agents.jsonl import TAIL_BYTES, JsonlTail
from tide_daemon.agents.question import ending_question
from tide_daemon.agents.state import Status, derive_state
from tide_daemon.board.derive import CardDeriver
from tide_daemon.board.store import BoardStore
from . import test_board_derive as bd
from .test_board_derive import FakeBranches, board, ticket

QUESTION = ("Zapisałem odpowiedź w specyfikacji.\n\nPytanie 10: skąd biorą się czat i alerty? "
            "Czy donacje albo subskrypcje są w planach na start?")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class EndingQuestionTest(unittest.TestCase):
    def test_questions_at_the_end(self):
        cases = {
            QUESTION: "Pytanie 10: skąd biorą się czat i alerty? Czy donacje albo subskrypcje są w planach na start?",
            "Gotowe.\n\nPushować?": "Pushować?",
            "**Scalić teraz?** 🙂": "Scalić teraz?",
            "Którą opcję wybierasz?\n\n1. Jedna kolumna\n2. Dwie kolumny": "Którą opcję wybierasz?",
            "Mam dwa pytania:\n\n1. Czy X ma być domyślne?\n2. Czy Y?": "Czy X ma być domyślne?",
            "Zrobiłem A.\n\nPytanie 3:\nJak ma wyglądać ekran?": "Pytanie 3: Jak ma wyglądać ekran?",
            "Którą wolisz? Mogę też przygotować obie.": "Którą wolisz?",
            "Spec gotowa. Czekam na Twoją odpowiedź.": "Spec gotowa. Czekam na Twoją odpowiedź.",
            "Wszystko gotowe. Daj znać, czy scalić.": "Wszystko gotowe. Daj znać, czy scalić.",
        }
        for text, expected in cases.items():
            self.assertEqual(ending_question(text), expected, text)

    def test_not_questions(self):
        for text in ("Gotowe. Testy przechodzą.", "",
                     "Co się zmieniło? Dodałem pole daty. Testy przechodzą, review APPROVE.",
                     "Wynik:\n\n```\nczy działa?\n```",
                     "Zmiany:\n\n| Plik | Czy zmieniony? |\n| --- | --- |\n| a.py | tak |",
                     "Czy to było trudne? Trochę. Ale ostatecznie wszystkie testy przechodzą i jest scalone."):
            self.assertEqual(ending_question(text), "", text)
        self.assertTrue(ending_question("x? " + "a" * 400 + "?").endswith("…"), "przycięte")


class ClaudeTurnTest(unittest.TestCase):
    """Stan sesji Claude Code z dziennika: pytanie na końcu tury, odpowiedź, narzędzie po tekście."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-reply-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cwd = os.path.join(self.tmp, "twitch")
        os.makedirs(self.cwd)
        folder = os.path.join(self.tmp, "claude", "projects", escape_cwd(self.cwd))
        os.makedirs(folder)
        self.sid = "1233d658-0000-4000-8000-000000000001"
        self.path = os.path.join(folder, f"{self.sid}.jsonl")
        self.now = time.time()

    def write(self, *records):
        with open(self.path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(dict(record, cwd=self.cwd, gitBranch="claude/grilling"), ensure_ascii=False) + "\n")

    def user(self, ts, text):
        return {"type": "user", "timestamp": iso(ts), "message": {"role": "user", "content": text}}

    def said(self, ts, text, stop="end_turn"):
        return {"type": "assistant", "timestamp": iso(ts),
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}

    def tool(self, ts):
        return {"type": "assistant", "timestamp": iso(ts),
                "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                                                  "input": {"file_path": "/x/spec.md"}}]}}

    def end(self, ts):
        return {"type": "system", "subtype": "turn_duration", "timestamp": iso(ts)}

    def session(self):
        scanner = AgentScanner(os.path.join(self.tmp, "claude"), os.path.join(self.tmp, "codex"))
        scanner.discover(self.now)
        scanner.update()
        return scanner.sessions(self.now)[0]

    def test_question_at_end_of_turn_waits_until_user_replies(self):
        asked = self.now - 2 * 3600  # dawno — pytanie nie robi się „bezczynne”
        self.write(self.user(asked - 60, "Zacznij grilling"), self.said(asked, QUESTION), self.end(asked + 1))
        s = self.session()
        self.assertEqual((s["state"], s["reason"]), ("waiting", "reply"))
        self.assertEqual(s["waitingText"],
                         "Pytanie 10: skąd biorą się czat i alerty? Czy donacje albo subskrypcje są w planach na start?")
        self.assertAlmostEqual(s["sinceTs"], asked, places=0)
        self.assertIs(attention.longest_waiting([dict(s, canActivate=True)])["id"], s["id"], "Meta+A też tam skacze")
        self.write(self.user(self.now - 30, "Czat z IRC, donacje później."))
        s = self.session()
        self.assertEqual((s["state"], s["waitingText"]), ("working", ""), "odpowiedź kasuje czekanie")
        self.write(self.said(self.now - 10, "Zapisałem. Specyfikacja gotowa."), self.end(self.now - 9))
        self.assertEqual((self.session()["state"], self.session()["reason"]), ("done", ""))

    def test_tool_call_after_question_text_is_not_waiting(self):
        self.write(self.user(self.now - 100, "Start"), self.said(self.now - 6, "Czy mogę przeczytać spec?", stop=None),
                   self.tool(self.now - 5))
        self.assertEqual(self.session()["state"], "working")
        self.write({"type": "user", "timestamp": iso(self.now - 4),
                    "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1"}]}},
                   self.said(self.now - 3, "Przeczytałem i poprawiłem."), self.end(self.now - 2))
        self.assertEqual(self.session()["state"], "done", "pytanie sprzed narzędzia nie kończy tury")

    def test_interrupted_question_is_not_waiting(self):
        self.write(self.user(self.now - 100, "Start"), self.said(self.now - 90, "Pushować?"),
                   self.user(self.now - 80, "[Request interrupted by user]"))
        self.assertEqual(self.session()["state"], "done")


class CompactionTest(ClaudeTurnTest):
    """Kompaktowanie po pytaniu na końcu tury nie jest odpowiedzią (sesja Twitch, 27.10, 20:09)."""

    ASK = ("Zapisane w `mocks/scenes-v1/DECISIONS.md`. Zamówienia grafik w `asset-requests.md`.\n\n"
           "Proponuję zostać przy wariancie B na start i dopiero potem dorobić sceny przerw. Zgadzasz się?")

    def compaction(self, ts, attachments=3, attachment_size=50_000):
        """Wpisy dopisywane przez Claude Code przy kompaktowaniu bezczynnej sesji — w kolejności z dziennika."""
        out = [{"type": "system", "subtype": "away_summary", "timestamp": iso(ts - 3000), "content": "Byłeś z dala…"},
               {"type": "last-prompt", "lastPrompt": "x"}, {"type": "permission-mode", "permissionMode": "default"},
               {"type": "frame-link", "timestamp": iso(ts)},
               {"type": "system", "subtype": "compact_boundary", "timestamp": iso(ts), "content": "Conversation compacted",
                "level": "info", "isMeta": False, "compactMetadata": {"trigger": "auto", "preTokens": 330857}},
               {"type": "user", "timestamp": iso(ts), "isVisibleInTranscriptOnly": True, "isCompactSummary": True,
                "message": {"role": "user", "content": "This session is being continued from a previous conversation "
                                                       "that ran out of context.\n\n" + "Streszczenie. " * 3000}}]
        out += [{"type": "attachment", "timestamp": iso(ts + 0.2),
                 "attachment": {"type": "file", "content": "x" * attachment_size}} for _ in range(attachments)]
        out += [{"type": "system", "subtype": "informational", "timestamp": iso(ts + 0.3), "level": "notice",
                 "content": "Compacted while idle, before the prompt cache expired"},
                {"type": "artifact-autoreact-ledger", "entries": []}, {"type": "mode", "mode": "normal"}]
        return out

    def scanned(self):
        scanner = AgentScanner(os.path.join(self.tmp, "claude"), os.path.join(self.tmp, "codex"))
        scanner.discover(self.now)
        scanner.update()
        return scanner.sessions(self.now)[0], next(log for log, _ in scanner.logs.values())

    def test_question_survives_idle_compaction(self):
        asked = self.now - 3 * 3600
        self.write(self.user(asked - 600, "Zrób sceny"), self.said(asked, self.ASK), self.end(asked + 0.05),
                   *self.compaction(asked + 3300))
        s, log = self.scanned()
        self.assertEqual((s["state"], s["reason"], s["waitingText"]),
                         ("waiting", "reply", "Zgadzasz się?"))
        self.assertAlmostEqual(s["sinceTs"], asked, places=0)
        # Claude Code zgłasza w sessions/<pid>.json „idle” — pytanie i tak wygrywa.
        self.assertEqual(derive_state(log, self.now, True, Status("idle", asked + 3300), lambda _ts: False)[:2],
                         ("waiting", "reply"))

    def test_question_survives_compaction_bigger_than_tail(self):
        asked = self.now - 3 * 3600
        self.write(self.user(asked - 600, "Zrób sceny"), self.said(asked, self.ASK), self.end(asked + 0.05),
                   *self.compaction(asked + 3300, attachments=30))
        self.assertGreater(os.path.getsize(self.path), TAIL_BYTES + 400_000, "streszczenie i załączniki > ogon")
        s, _ = self.scanned()
        self.assertEqual((s["state"], s["reason"]), ("waiting", "reply"))

    def test_real_user_message_after_compaction_clears_the_wait(self):
        asked = self.now - 3 * 3600
        self.write(self.user(asked - 600, "Zrób sceny"), self.said(asked, self.ASK), self.end(asked + 0.05),
                   *self.compaction(asked + 3300), self.user(self.now - 20, "Tak, zostańmy przy B."))
        s, _ = self.scanned()
        self.assertEqual((s["state"], s["waitingText"]), ("working", ""))

    def test_local_command_echo_is_not_a_reply_but_skill_command_is(self):
        asked = self.now - 3 * 3600
        caveat = {"type": "user", "timestamp": iso(asked + 100), "isMeta": True,
                  "message": {"role": "user", "content": "<local-command-caveat>Caveat: The messages below were "
                                                         "generated by the user while running local commands."
                                                         "</local-command-caveat>"}}
        self.write(self.user(asked - 600, "Zrób sceny"), self.said(asked, self.ASK), self.end(asked + 0.05), caveat,
                   self.user(asked + 101, "<command-name>/model</command-name>\n<command-message>model"
                                          "</command-message>\n<command-args></command-args>"),
                   self.user(asked + 102, "<local-command-stdout>Set model to Opus</local-command-stdout>"))
        self.assertEqual((self.session()["state"], self.session()["reason"]), ("waiting", "reply"))
        self.write(self.user(self.now - 20, "<command-message>grilling</command-message>\n"
                                            "<command-name>/grilling</command-name>"),
                   {"type": "user", "timestamp": iso(self.now - 19), "isMeta": True,
                    "message": {"role": "user", "content": [{"type": "text", "text": "Base directory for this skill"}]}})
        self.assertEqual(self.session()["state"], "working", "skill wywołany poleceniem to wiadomość użytkownika")

    def test_ask_user_question_survives_compaction(self):
        asked = self.now - 3 * 3600
        ask = {"type": "assistant", "timestamp": iso(asked),
               "message": {"role": "assistant", "content": [
                   {"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
                    "input": {"questions": [{"question": "Który wariant scen?"}]}}]}}
        self.write(self.user(asked - 600, "Zrób sceny"), ask, *self.compaction(asked + 3300))
        s, _ = self.scanned()
        self.assertEqual((s["state"], s["reason"], s["waitingText"]), ("waiting", "question", "Który wariant scen?"))

    def test_tail_grows_until_it_reaches_the_last_turn(self):
        records = [self.user(1, "Start"), self.said(2, "Pushować?")] + \
                  [{"type": "attachment", "timestamp": iso(3), "attachment": {"content": "x" * 1000}}] * 3000
        self.write(*records)
        plain = [r.get("type") for r in JsonlTail(self.path, 64 * 1024).read_new()]
        self.assertNotIn("assistant", plain, "bez warunku ogon nie sięga pytania")
        grown = JsonlTail(self.path, 64 * 1024, enough=tail_has_turn).read_new()
        self.assertEqual(sum(r.get("type") == "assistant" for r in grown), 1)
        self.assertEqual(sum(r.get("type") == "user" for r in grown), 1, "nagłówek tylko raz")
        self.assertIs(ClaudeLog.tail_enough, tail_has_turn)
        capped = JsonlTail(self.path, 64 * 1024, enough=lambda _r: False, max_tail_bytes=256 * 1024).read_new()
        self.assertLess(len(capped), 300, "okno ma górną granicę")


class CompactedCoordinatorCardTest(unittest.TestCase):
    """K-20: koordynacja 1/1 scalona, a sesja koordynatora czeka na odpowiedź — „Czeka na mnie”, nie Gotowe."""

    def test_waiting_session_beats_merged_coordination(self):
        coord = bd.board("claude:koord", [bd.ticket("TW-1", "gotowe", branch="feat/scenes")], state="idle",
                         branch="feat/scenes")
        card = bd.card(20, session="claude:koord")
        merged = FakeBranches(merged={"feat/scenes": True})
        idle = bd.session("claude:koord", state="idle", branch="feat/scenes", since=bd.NOW - 3600)
        self.assertEqual(bd.derive(card, [idle], [coord], merged)["column"], "done")
        asked = dict(idle, state="waiting", reason="reply", waitingText="Zgadzasz się?")
        out = bd.derive(card, [asked], [coord], FakeBranches(merged={"feat/scenes": True}))
        self.assertEqual((out["column"], out["note"]), ("waiting", "czeka na odpowiedź: „Zgadzasz się?”"))


class CodexTurnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-reply-codex-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.folder = os.path.join(self.tmp, "codex", "sessions", "2026", "10", "27")
        os.makedirs(self.folder)
        self.now = time.time()

    def session(self, records, tid="0000000a-0000-4000-8000-000000000029"):
        meta = {"type": "session_meta", "timestamp": iso(self.now - 600),
                "payload": {"id": tid, "cwd": self.tmp, "originator": "codex_cli_rs", "timestamp": iso(self.now - 600)}}
        with open(os.path.join(self.folder, f"rollout-2026-10-27T10-00-00-{tid}.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r, ensure_ascii=False) for r in [meta] + records) + "\n")
        scanner = AgentScanner(os.path.join(self.tmp, "claude"), os.path.join(self.tmp, "codex"))
        scanner.discover(self.now)
        scanner.update()
        return scanner.sessions(self.now)[0]

    def turn(self, ts, text, trigger="composer"):
        return [{"type": "event_msg", "timestamp": iso(ts - 5),
                 "payload": {"type": "task_started", "turn_attribution": {"turn_trigger": trigger}}},
                {"type": "response_item", "timestamp": iso(ts - 1),
                 "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}},
                {"type": "event_msg", "timestamp": iso(ts), "payload": {"type": "task_complete", "last_agent_message": text}}]

    def test_task_complete_with_question(self):
        s = self.session(self.turn(self.now - 60, "Spec gotowa.\n\nCzy logi mają trafiać do pliku?"))
        self.assertEqual((s["state"], s["reason"], s["waitingText"]),
                         ("waiting", "reply", "Czy logi mają trafiać do pliku?"))

    def test_scheduled_turn_with_question_does_not_wait(self):
        s = self.session(self.turn(self.now - 60, "Sprawdziłem CI. Czy coś jeszcze?", trigger="automation_heartbeat_scheduled"))
        self.assertEqual((s["state"], s["reason"]), ("done", "scheduled"))

    def test_user_message_after_question_clears_it(self):
        reply = {"type": "response_item", "timestamp": iso(self.now - 30),
                 "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Do pliku."}]}}
        done = {"type": "event_msg", "timestamp": iso(self.now - 20), "payload": {"type": "turn_aborted"}}
        s = self.session(self.turn(self.now - 60, "Czy logi mają trafiać do pliku?") + [reply, done])
        self.assertEqual(s["state"], "done")


class CodexSubagentTest(CodexTurnTest):
    """Przegląd PT-29: podagent, którego raport kończy się pytaniem, nie czeka (nikt mu nie odpisze) i nie
    robi „czeka” z pracującego koordynatora."""

    def test_subagent_report_question_does_not_make_parent_wait(self):
        parent, child = "0000000a-0000-4000-8000-000000000101", "0000000a-0000-4000-8000-000000000102"
        self.session([{"type": "event_msg", "timestamp": iso(self.now - 30), "payload": {"type": "task_started"}},
                      {"type": "response_item", "timestamp": iso(self.now - 3),
                       "payload": {"type": "function_call", "name": "shell", "call_id": "x", "arguments": "{}"}}],
                     tid=parent)
        text = "Raport: zrobione. Testy przechodzą.\n\nCzy mam też poprawić README?"
        meta = {"type": "session_meta", "timestamp": iso(self.now - 600),
                "payload": {"id": child, "cwd": self.tmp, "originator": "codex_cli_rs", "timestamp": iso(self.now - 600),
                            "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}},
                            "parent_thread_id": parent}}
        with open(os.path.join(self.folder, f"rollout-2026-10-27T10-00-01-{child}.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r, ensure_ascii=False) for r in [meta] + self.turn(self.now - 60, text)) + "\n")
        scanner = AgentScanner(os.path.join(self.tmp, "claude"), os.path.join(self.tmp, "codex"))
        scanner.discover(self.now)
        scanner.update()
        sessions = scanner.sessions(self.now)
        self.assertEqual([(s["id"][-3:], s["state"], s["reason"], s["subagents"]) for s in sessions],
                         [("101", "working", "", 1)])
        log = next(log for log, _ in scanner.logs.values() if log.session_id == child)
        from tide_daemon.agents.state import reply_question
        self.assertEqual(reply_question(log), "")

    def test_aggregate_never_promotes_a_reply(self):
        from tide_daemon.agents.scanner import _aggregate
        entry = {"state": "working", "reason": "", "since": "", "sinceTs": 1.0, "lastActivityTs": 1.0,
                 "lastAction": "", "waitingText": ""}
        child = dict(entry, state="waiting", reason="reply", waitingText="Czy README?")
        _aggregate(entry, [child])
        self.assertEqual((entry["state"], entry["waitingText"]), ("working", ""))


class NotificationTest(unittest.TestCase):
    def test_one_notification_per_new_question(self):
        tracker = attention.WaitingTracker()
        done = {"id": "claude:t", "state": "done", "project": "twitch", "tool": "claude"}
        waiting = dict(done, state="waiting", reason="reply", sinceTs=10,
                       waitingText="Pytanie 10: skąd biorą się czat i alerty?", lastAction="Read · spec.md")
        tracker.update([done])
        started, _ = tracker.update([waiting])
        self.assertEqual([s["id"] for s in started], ["claude:t"])
        for _ in range(3):
            self.assertEqual(tracker.update([waiting])[0], [], "to samo pytanie — bez powtórki")
        tracker.update([dict(done, state="working")])
        self.assertEqual(len(tracker.update([dict(waiting, sinceTs=20)])[0]), 1, "nowe pytanie — nowe powiadomienie")
        title, body = attention.notification_text(waiting)
        self.assertEqual(title, "Agent czeka: twitch")
        self.assertTrue(body.startswith("Zadał pytanie: Pytanie 10: skąd biorą się czat i alerty?"), body)


class CardOfFinishedSessionTest(unittest.TestCase):
    """K-20: karta założona i połączona ze skończoną sesją — nie „przeniesiona ręcznie”."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-k20-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config = os.path.join(self.tmp, "gitconfig")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = time.time()
        self.store = BoardStore(os.path.join(self.tmp, "board"), clock=time.time)
        self.store.upsert_project({"name": "twitch"})
        self.store.create_card({"title": "Twitch: grilling", "project": "twitch", "column": "todo"})
        self.store.update_card("K-1", {"session": "claude:1233d658"})  # połączenie po pracy

    def card(self, sessions=(), boards=()):
        deriver = CardDeriver(lambda: list(sessions), lambda: list(boards), branches=FakeBranches(merged={}),
                              clock=lambda: self.now)
        return next(c for c in self.store.snapshot(derive=deriver)["cards"] if c["id"] == "K-1")

    def session(self, **kw):
        out = {"id": "claude:1233d658", "tool": "claude", "sessionId": "1233d658", "project": "twitch", "branch": "",
               "cwd": "/srv/twitch", "state": "done", "reason": "", "sinceTs": self.now - 3600,
               "lastActivityTs": self.now - 3600, "lastAction": "", "waitingText": ""}
        out.update(kw)
        return out

    def test_plain_finished_session_is_in_progress_and_waiting_with_question(self):
        c = self.card([self.session()])
        self.assertEqual(c["boardColumn"], "progress")
        self.assertNotIn("przeniesiona ręcznie", c["derived"]["note"])
        asked = self.session(state="waiting", reason="reply", sinceTs=self.now - 3600,
                             waitingText="Pytanie 10: skąd biorą się czat i alerty?")
        c = self.card([asked])
        self.assertEqual((c["boardColumn"], c["derived"]["note"], c["derived"]["session"]),
                         ("waiting", "czeka na odpowiedź: „Pytanie 10: skąd biorą się czat i alerty?”", "claude:1233d658"))

    def test_finished_coordinator_card_created_after_work_goes_to_review_not_manual(self):
        coord = board("claude:1233d658", [ticket("TW-1", "gotowe", activity=self.now - 3600)], state="ended", live=False,
                      activity=self.now - 3600, branch="claude/twitch")
        c = self.card(boards=[coord])
        self.assertEqual(c["boardColumn"], "review")
        self.assertNotIn("przeniesiona ręcznie", c["derived"]["note"])
        # Przeniesienie z Do sprawdzenia („do poprawy”) — zostaje w Do zrobienia.
        self.store.move_card("K-1", "todo", 0, "review")
        c = self.card(boards=[coord])
        self.assertEqual((c["boardColumn"], c["movedFrom"]), ("todo", "review"))
        self.assertIn("przeniesiona ręcznie", c["derived"]["note"])
        # Przegląd PT-28: zmiana kolejności w Do zrobienia (karta widoczna w todo) nie gubi „do poprawy”.
        self.store.create_card({"title": "sąsiadka", "column": "todo"})
        self.store.move_card("K-1", "todo", 1, "todo")
        c = self.card(boards=[coord])
        self.assertEqual((c["boardColumn"], c["movedFrom"]), ("todo", "review"))
        # Koordynator z pytaniem na końcu tury — Czeka na mnie, mimo skończonych ticketów.
        live = dict(coord, live=True, state="waiting")
        asked = self.session(state="waiting", reason="reply", waitingText="Czy donacje są w planach?")
        self.assertEqual(self.card([asked], [live])["boardColumn"], "waiting")

    def test_board_monitor_records_the_shown_column_on_move(self):
        from .privatebus import app
        from tide_daemon.board_monitor import BoardMonitor
        app()
        shown = {"column": "review", "note": "do sprawdzenia ręcznie"}
        monitor = BoardMonitor(self.store, derive=lambda card: dict(shown))
        self.addCleanup(monitor.stop)
        monitor.publish()
        reply = json.loads(monitor.move_card("K-1", "todo", 0))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(next(c for c in self.store.snapshot()["cards"] if c["id"] == "K-1")["movedFrom"], "review",
                         "w pliku zapisane todo, a karta była widoczna w Do sprawdzenia")


if __name__ == "__main__":
    unittest.main()
