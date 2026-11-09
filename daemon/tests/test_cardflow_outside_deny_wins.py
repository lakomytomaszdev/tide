"""PT-311: odmowa człowieka wygrywa z automatyczną zgodą na polecenie poza piaskownicą.

08.11 18:37 krok 51 PT-264 uruchomił pełne testy poza piaskownicą mimo odmowy i wstrzymania karty: ścieżka
automatycznej zgody przy wyniku kroku nie sprawdzała wcześniejszej odmowy (ścieżka w piaskownicy to robi)."""
import json
from pathlib import Path
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess

COMMAND = "echo zewnetrzne-ok"


class OutsideDenyWinsTest(FlowInProcess):
    def call(self, request, card):
        replies = []
        self.board.resolve_card_step(card, json.dumps(request), replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=20))
        return json.loads(replies[0])

    def seeded(self):
        """Karta z trzema prośbami outside (s1, s2 w PT-1, s3 w PT-2) i zapamiętaną rodziną polecenia projektu."""
        card = self.card()
        self.flow.stop()
        self.flow = self.make_flow()
        self.flow._timer.stop()
        self.assertTrue(wait_until(lambda: not self.flow._ticking, timeout=5))
        eng = self.flow.engine
        eng.journal.append(card, "card.started", project="proj", entry="plan", repo=self.repo, baseVersion=1)
        eng.journal.append(card, "plan.approved", rows=[{"id": "PT-1"}, {"id": "PT-2"}])
        Path(self.settings).write_text(json.dumps({"autoConsent": True,
                                                   "outsideCommands": {"proj": ["echo zewnetrzne-ok *"]}}))
        steps = [self.request(card, f"s{n}", "PT-2" if n == 3 else "PT-1") for n in range(1, 4)]
        return card, eng, steps

    def request(self, card, name, ticket, command=COMMAND, *, finish=False):
        eng = self.flow.engine
        folder = str(Path(self.base, name))
        Path(folder, "out").mkdir(parents=True)
        step = eng.journal.append(card, "step.started", step=name, ticket=ticket, kind="ticket", stepDir=folder,
                                  worktree=self.repo, baseVersion=1)
        if finish:
            # Ścieżka wyniku kroku (_finish), na której powstała automatyczna zgoda z incydentu.
            eng._finish(card, step, forced={"result": "denied", "outside": True, "command": command})
        else:
            eng.journal.append(card, "step.finished", step=name, ticket=ticket, result="denied", outside=True,
                               command=command, canAllow=True)
        return step

    def state(self, card):
        from tide_daemon.cardflow.state import fold
        return fold(self.flow.engine.journal.read(card))

    def recheck(self, card, step):
        self.flow.engine._outside_recheck(card, step, self.state(card).finished[step["step"]])

    def auto(self, card, name):
        return [d for d in self.of(card, "step.decision") if d["step"] == name and d.get("auto")]

    def skipped(self, card, name):
        return [e["reason"] for e in self.of(card, "outside.auto-skipped") if e["step"] == name]

    def test_human_deny_blocks_later_auto_consent_in_the_ticket_on_step_result(self):
        card, eng, steps = self.seeded()
        self.assertTrue(self.call({"choice": "deny", "step": "s1", "answer": "nie teraz"}, card)["ok"])
        later = self.request(card, "s4", "PT-1", "  echo   zewnetrzne-ok ", finish=True)
        self.assertEqual(self.auto(card, "s4"), [])
        self.assertEqual(self.skipped(card, "s4"), ["denied-before"])
        # Recheck przy każdym przebiegu też nie zatwierdza i nie powtarza wpisu w dzienniku.
        self.recheck(card, later)
        self.recheck(card, later)
        self.assertEqual(self.auto(card, "s4"), [])
        self.assertEqual(self.skipped(card, "s4"), ["denied-before"])
        st = self.state(card)
        eng._outside_ready(card, later, st.finished["s4"], {"choice": "run", "auto": True})
        self.assertEqual(self.of(card, "outside.started"), [])
        # Prośba czeka na człowieka: ręczne „run” nadal uruchamia polecenie.
        self.assertTrue(self.call({"choice": "run", "step": "s4"}, card)["ok"])
        st = self.state(card)
        with mock.patch("tide_daemon.cardflow.engine.subprocess.Popen") as popen:
            eng._outside_ready(card, later, st.finished["s4"], st.step_decisions["s4"])
        self.assertEqual(popen.call_count, 1)
        self.assertEqual([e["step"] for e in self.of(card, "outside.started")], ["s4"])

    def test_deny_in_another_ticket_or_by_tide_does_not_block_auto_consent(self):
        card, eng, steps = self.seeded()
        self.assertTrue(self.call({"choice": "deny", "step": "s1"}, card)["ok"])
        self.recheck(card, steps[2])
        self.assertEqual([d["choice"] for d in self.auto(card, "s3")], ["run"])
        # Automatyczna odmowa Tide (bez człowieka) nie jest odmową człowieka.
        other = self.request(card, "s5", "PT-2")
        eng.journal.append(card, "step.decision", step="s5", ticket="PT-2", kind="ticket", choice="deny",
                           answer="", command=COMMAND, executor="", auto=True)
        later = self.request(card, "s6", "PT-2", finish=True)
        self.assertEqual([d["choice"] for d in self.auto(card, "s6")], ["run"])
        self.assertEqual(self.skipped(card, "s6"), [])
        self.assertTrue(other)

    def test_deny_from_previous_order_does_not_block(self):
        card, eng, steps = self.seeded()
        self.assertTrue(self.call({"choice": "deny", "step": "s1"}, card)["ok"])
        eng.journal.append(card, "card.started", project="proj", entry="plan", repo=self.repo, baseVersion=1)
        later = self.request(card, "s7", "PT-1", finish=True)
        self.assertEqual([d["choice"] for d in self.auto(card, "s7")], ["run"])
        self.assertTrue(later)

    def test_paused_card_gets_no_new_auto_consent_until_resumed(self):
        card, eng, steps = self.seeded()
        eng.journal.append(card, "card.paused")
        paused = self.request(card, "s4", "PT-1", finish=True)
        self.recheck(card, steps[0])
        self.recheck(card, steps[0])
        self.assertEqual(self.auto(card, "s4") + self.auto(card, "s1"), [])
        self.assertEqual((self.skipped(card, "s4"), self.skipped(card, "s1")), (["paused"], ["paused"]))
        eng.journal.append(card, "card.resumed")
        self.recheck(card, paused)
        self.assertEqual([d["choice"] for d in self.auto(card, "s4")], ["run"])
        # Po wznowieniu decyduje stan aktualny: odmowa zapisana w czasie pauzy wygrywa.
        eng.journal.append(card, "card.paused")
        self.assertTrue(self.call({"choice": "deny", "step": "s2"}, card)["ok"])
        eng.journal.append(card, "card.resumed")
        self.recheck(card, steps[0])
        self.assertEqual(self.auto(card, "s1"), [])
        self.assertEqual(self.skipped(card, "s1"), ["paused", "denied-before"])

    def test_deny_after_auto_consent_before_start_invalidates_it(self):
        card, eng, steps = self.seeded()
        self.recheck(card, steps[0])
        granted = self.auto(card, "s1")[0]
        st = self.state(card)
        with mock.patch.object(eng, "_outside_busy", return_value=True):
            eng._outside_ready(card, steps[0], st.finished["s1"], granted)
        self.assertIn("s1", eng._outside_waiting)
        response = self.call({"choice": "deny", "step": "s1", "answer": "nie uruchamiaj"}, card)
        self.assertTrue(response["ok"], response)
        self.assertNotIn("s1", eng._outside_waiting)
        invalidated = self.of(card, "outside.decision-invalidated")
        self.assertEqual([(e["step"], e["decisionSeq"], e["reason"]) for e in invalidated],
                         [("s1", granted["seq"], "denied")])
        self.assertEqual(self.state(card).step_decisions["s1"]["choice"], "deny")
        # Stara migawka zgody z kolejki ani start po kontroli kolejki (wyścig) nie uruchamiają procesu.
        with mock.patch("tide_daemon.cardflow.engine.subprocess.Popen") as popen:
            eng._outside_ready(card, steps[0], st.finished["s1"], granted)
            eng._start_outside(card, steps[0], COMMAND)
        self.assertEqual(popen.call_count, 0)
        self.assertEqual(self.of(card, "outside.started"), [])

    def test_auto_consent_queued_before_a_deny_in_another_step_does_not_start(self):
        card, eng, steps = self.seeded()
        self.recheck(card, steps[0])
        granted = self.auto(card, "s1")[0]
        self.assertTrue(self.call({"choice": "deny", "step": "s2"}, card)["ok"])
        st = self.state(card)
        with mock.patch("tide_daemon.cardflow.engine.subprocess.Popen") as popen:
            eng._outside_ready(card, steps[0], st.finished["s1"], granted)
        self.assertEqual(popen.call_count, 0)
        self.assertEqual([(e["step"], e["reason"]) for e in self.of(card, "outside.decision-invalidated")],
                         [("s1", "denied-before")])
        self.assertNotIn("s1", self.state(card).step_decisions)

    def test_deny_after_start_is_rejected_and_the_process_keeps_running(self):
        card, eng, steps = self.seeded()
        self.recheck(card, steps[0])
        st = self.state(card)
        eng._outside_ready(card, steps[0], st.finished["s1"], st.step_decisions["s1"])
        self.assertEqual([e["step"] for e in self.of(card, "outside.started")], ["s1"])
        self.assertEqual(self.call({"choice": "deny", "step": "s1"}, card)["error"], "already-started")
        self.assertEqual(self.of(card, "outside.decision-invalidated"), [])
        self.assertTrue(wait_until(lambda: eng._outside_ready(card, steps[0], st.finished["s1"],
                                                            st.step_decisions["s1"]) is not None, timeout=10))
        self.assertEqual(self.of(card, "outside.finished")[0]["exit"], 0)

    def test_manual_run_before_start_stays_decided(self):
        card, eng, steps = self.seeded()
        self.assertTrue(self.call({"choice": "run", "step": "s1"}, card)["ok"])
        self.assertEqual(self.call({"choice": "deny", "step": "s1"}, card)["error"], "already-decided")
        self.assertEqual(self.state(card).step_decisions["s1"]["choice"], "run")
