"""Karta dawnego przepływu (koordynator) ze skończonym planem, której agent czeka na odpowiedź — „Czeka na
mnie”, nie „Do sprawdzenia” (PT-110).

Diagnoza z 28.10 (notka PT-110): karta K-1 miała plan koordynatora `status: done` i wszystkie tickety `gotowe`,
a gałąź karty nie była scalona. Wtedy `_from_board` daje „Do sprawdzenia” za każdym razem, gdy sesja
koordynatora nie jest w stanie `waiting` — a jedyną drogą do „Czeka na mnie” jest pytanie rozpoznane przez
`agents/question.py`. Agent kończył turę prośbą bez znaku zapytania („Wklej mi treść wyjątku…”), reguły jej
nie rozpoznały, sesja była `done` i karta stała w „Do sprawdzenia”, choć agent czekał. Liczenie kolumny jest
poprawne; reguł pytania nie zmieniamy (PT-42). Ten test pilnuje całego toru dla pytania ze znakiem „?”:
dziennik Claude Code w kształcie prawdziwej sesji koordynatora → /Agents (`AgentScanner`) → kolumna karty.

Dziennik syntetyczny (bez treści prawdziwych rozmów), tablica koordynatora jak z /Coordination.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone

from tide_daemon.agents import AgentScanner
from tide_daemon.agents.claude import escape_cwd
from . import test_board_derive as bd
from .test_board_derive import FakeBranches

CARD_BRANCH = "claude/k-1-data-waznosci"
QUESTION = "Czy możesz wkleić treść wyjątku z kroku 1?"
ASK = ("Wdrożenie jest na środowisku testowym. W dzienniku usługi przy pobieraniu komunikatów widać błąd "
       "logowania do bazy, a nie błąd w naszym kodzie.\n\n1. Otwórz dziennik usługi.\n2. Uruchom aplikację "
       "na tablecie.\n\n" + QUESTION)
REPORT = "Gałąź wypchnięta, merge requesty założone. Kod jest gotowy, test daty ważności poczeka na naprawę logowania."


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class LegacyCoordinatorWaitingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-pt110-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "projekt")
        self.cwd = os.path.join(self.repo, ".claude", "worktrees", "k-1-data-waznosci")
        os.makedirs(self.cwd)
        folder = os.path.join(self.tmp, "claude", "projects", escape_cwd(self.cwd))
        os.makedirs(folder)
        self.sid = "41c8e6e3-0000-4000-8000-000000000001"
        self.path = os.path.join(folder, f"{self.sid}.jsonl")
        self.now = time.time()
        self.parent = None

    # --- dziennik w kształcie prawdziwego -----------------------------------------------------

    def write(self, *records):
        with open(self.path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def entry(self, kind, ts, sidechain=False, **kw):
        """Wpis główny jak w Claude Code: uuid, parentUuid, sessionId, cwd, gitBranch, wersja."""
        own = str(uuid.uuid4())
        out = {"parentUuid": self.parent, "isSidechain": sidechain, "userType": "external", "cwd": self.cwd,
               "sessionId": self.sid, "version": "2.1.0", "gitBranch": CARD_BRANCH, "type": kind,
               "uuid": own, "timestamp": iso(ts)}
        out.update(kw)
        if not sidechain:
            self.parent = own
        return out

    def user(self, ts, text):
        return self.entry("user", ts, message={"role": "user", "content": text})

    def assistant(self, ts, content, stop):
        return self.entry("assistant", ts, requestId=f"req_{int(ts * 1000)}",
                          message={"model": "claude-opus-4-1", "id": f"msg_{int(ts * 1000)}", "type": "message",
                                   "role": "assistant", "content": content, "stop_reason": stop,
                                   "usage": {"input_tokens": 3, "output_tokens": 120}})

    def said(self, ts, text):
        """Odpowiedź w dwóch wpisach, jak w prawdziwym dzienniku: myślenie, potem tekst z end_turn."""
        return [self.assistant(ts - 0.5, [{"type": "thinking", "thinking": "", "signature": "x"}], "end_turn"),
                self.assistant(ts, [{"type": "text", "text": text}], "end_turn"),
                self.entry("system", ts + 0.02, subtype="turn_duration", durationMs=41000, isMeta=False)]

    def subagent(self, ts, call_id):
        """Koordynator zleca ticket podagentowi (Agent); wpisy podagenta są na bocznym wątku."""
        call = self.assistant(ts, [{"type": "tool_use", "id": call_id, "name": "Agent",
                                    "input": {"description": "DPT-3: implement", "prompt": "…"}}], "tool_use")
        side = [self.entry("user", ts + 1, sidechain=True, message={"role": "user", "content": "DPT-3: implement"}),
                self.entry("assistant", ts + 30, sidechain=True,
                           message={"role": "assistant", "content": [{"type": "text", "text": "Gotowe."}],
                                    "stop_reason": "end_turn"})]
        result = self.entry("user", ts + 31, message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": "DPT-3 gotowe, review APPROVE"}]},
            toolUseResult={"status": "completed"})
        return [call, *side, result]

    def noise(self, ts):
        """Wpisy bez znaczenia dla stanu: kolejka, załącznik, zapytanie API."""
        return [{"type": "queue-operation", "operation": "enqueue", "timestamp": iso(ts), "sessionId": self.sid},
                {"type": "api-request", "timestamp": iso(ts + 0.05)},
                self.entry("attachment", ts + 0.1, attachment={"type": "todo_reminder", "content": []})]

    def session(self) -> dict:
        scanner = AgentScanner(os.path.join(self.tmp, "claude"), os.path.join(self.tmp, "codex"))
        scanner.discover(self.now)
        scanner.update()
        sessions = scanner.sessions(self.now)
        self.assertEqual(len(sessions), 1)
        return sessions[0]

    # --- karta i koordynator ------------------------------------------------------------------

    def column(self, session: dict) -> dict:
        """Kolumna karty K-1 połączonej z sesją koordynatora; plan skończony, gałąź karty niescalona."""
        sid = session["id"]
        coord = bd.board(sid, [bd.ticket("DPT-1", "gotowe", branch="claude/dpt-1-backend"),
                               bd.ticket("DPT-2", "gotowe", branch="claude/dpt-2-tools"),
                               bd.ticket("DPT-3", "gotowe", branch="claude/dpt-3-app")],
                         state=session["state"], branch=CARD_BRANCH, plan={"status": "done", "path": "/plan.md"},
                         activity=self.now - 86400, repo=self.repo)
        card = bd.card(1, session=sid, branch=CARD_BRANCH, repo=self.repo, movedTs=self.now - 2 * 86400,
                       createdTs=self.now - 2 * 86400)
        return bd.derive(card, [session], [coord], FakeBranches(merged={CARD_BRANCH: False}),
                         repo_of=lambda cwd: self.repo if (cwd or "").startswith(self.repo) else "")

    def history(self, start):
        """Koordynator po skończonym planie: zlecenia, raport, potem rozmowa o wdrożeniu."""
        return [self.user(start, "Zrób kartę K-1 według planu."), *self.noise(start + 1),
                *self.subagent(start + 5, "toolu_dpt3"), *self.said(start + 60, REPORT),
                self.entry("system", start + 300, subtype="away_summary", content="Byłeś z dala…"),
                self.user(start + 3600, "Wdrożyłem, nic nie widzę w wiadomościach."), *self.noise(start + 3601)]

    # --- testy --------------------------------------------------------------------------------

    def test_finished_coordinator_waiting_with_question_is_waiting_not_review(self):
        asked = self.now - 600
        self.write(*self.history(asked - 7200), *self.said(asked, ASK))
        s = self.session()
        self.assertEqual((s["state"], s["reason"], s["waitingText"]), ("waiting", "reply", QUESTION))
        out = self.column(s)
        self.assertEqual(out["column"], "waiting", out)
        self.assertEqual(out["question"], QUESTION)
        self.assertEqual(out["note"], f"czeka na odpowiedź: „{QUESTION}”")
        self.assertEqual(out["nextStep"], "odpowiedz agentowi")
        self.assertEqual((out["link"], out["done"], out["total"]), ("session", 3, 3), "koordynator, plan skończony")

    def test_question_still_waits_after_away_summary_and_idle_time(self):
        asked = self.now - 3 * 3600  # dłużej niż IDLE_AFTER — pytanie dalej czeka
        self.write(*self.history(asked - 7200), *self.said(asked, ASK),
                   self.entry("system", asked + 180, subtype="away_summary", content="Byłeś z dala…"))
        out = self.column(self.session())
        self.assertEqual(out["column"], "waiting", out)

    def test_same_card_goes_to_review_once_the_user_answered_and_agent_reported(self):
        asked = self.now - 900
        self.write(*self.history(asked - 7200), *self.said(asked, ASK),
                   self.user(asked + 300, "Wklejam wyjątek: …"), *self.noise(asked + 301),
                   *self.said(asked + 400, "Błąd leży w ustawieniach logowania do bazy, nie w kodzie. Kod jest gotowy."))
        s = self.session()
        self.assertEqual((s["state"], s["waitingText"]), ("done", ""))
        out = self.column(s)
        self.assertEqual((out["column"], out["note"]), ("review", f"do scalenia: {CARD_BRANCH}"), out)


if __name__ == "__main__":
    unittest.main()
