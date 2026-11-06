"""PT-124: zgoda na wybrane polecenia z łańcucha — `commands[]` w decyzji, `allow` i `instead` w StepResolve.

W procesie, na prawdziwych atrapach kroku (flowfakes: `zgoda-lancuch`, `zgoda-katalog-kroku`) oraz na dzienniku
złożonym ręcznie dla walidacji żądań (jak DecisionStepTargetTest w test_cardflow_decisions_flow.py).
"""
import json
import os
import unittest

from tide_daemon.cardflow import handoff

from .privatebus import wait_until
from .test_cardflow import FlowInProcess

CHAIN = "git fetch origin && git push origin | head; echo $(make install)"


class ChainPermissionFlowTest(FlowInProcess):
    def setUp(self):
        super().setUp()
        changed = json.loads(self.board.upsert_project(json.dumps({"slug": "proj", "deniedCommands": ["make install"]})))
        self.assertTrue(changed["ok"], changed)

    def decide(self, card_id, request):
        box = []
        self.board.resolve_card_step(card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), "ResolveCardStep bez odpowiedzi")
        return json.loads(box[0])

    def resumed(self, card_id, parent):
        found = wait_until(lambda: [s for s in self.of(card_id, "step.started") if s.get("parentStep") == parent],
                           timeout=20)
        self.assertTrue(found, self.events(card_id))
        # Samo istnienie pliku nie oznacza, że atrapa skończyła zapis JSON.
        self.assertTrue(wait_until(lambda: [s for s in self.of(card_id, "step.finished")
                                           if s.get("step") == found[0]["step"]], timeout=20), self.events(card_id))
        call = os.path.join(found[0]["stepDir"], "out", "fake-call.json")
        with open(call, encoding="utf-8") as fh:
            return json.load(fh)

    def chain_decision(self):
        card_id = self.card(description="Sprawdź gałęzie.\n\nATRAPA: zgoda-lancuch")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        self.assertEqual([d for d in self.of(card_id, "step.decision") if d.get("auto")], [],
                         "łańcuch z poleceniami do zgody nie jest odrzucany automatycznie")
        [decision] = card["derived"]["flow"]["decisions"]
        return card_id, card, decision

    def test_chain_is_listed_with_flags_and_own_step_reads_are_hidden(self):
        card_id, card, decision = self.chain_decision()
        self.assertFalse(card["derived"]["flow"]["decisions"][0]["technical"]["canAllow"])
        step = self.of(card_id, "step.started")[0]
        self.assertTrue(step["stepDir"])
        self.assertEqual([(c["text"], c["readOnly"], c["forbidden"], c["canAllow"], c["preselected"])
                          for c in decision["commands"]],
                         [("git fetch origin", False, False, True, False),
                          ("git push origin", False, False, True, False),
                          ("head", True, False, True, True),
                          ("make install", False, True, False, False),
                          ("echo $(make install)", False, True, False, False)])
        self.assertFalse([c for c in decision["commands"] if step["stepDir"] in c["text"]],
                         "odczyt własnego <krok>/in i <krok>/out nie jest poleceniem do zgody")
        self.assertEqual(decision["commands"][3]["reason"], "Zabronione w projekcie regułą „make install”.")
        self.assertTrue(decision["commands"][4]["reason"])
        with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            argv = json.load(fh)["argv"]
        self.assertIn(f"Bash(ls -la {step['stepDir']}/in {step['stepDir']}/out)", argv,
                      "odczyt własnego katalogu kroku jest zawsze dozwolony")

    def test_allow_selected_resumes_with_exactly_those_commands(self):
        card_id, _card, decision = self.chain_decision()
        allow = next(o for o in decision["options"] if o["choice"] == "allow")
        answer = self.decide(card_id, {**allow["params"], "allow": [0, 2]})
        self.assertTrue(answer["ok"], answer)
        project = self.store.load().projects["proj"]
        self.assertIn("git fetch origin", project.allowed_commands)
        self.assertNotIn("head", project.allowed_commands, "już dozwolone (`head *`) nie jest dopisywane drugi raz")
        self.assertNotIn("git push origin", project.allowed_commands, "niezaznaczone nie jest dopuszczone")
        self.assertNotIn("make install", project.allowed_commands)
        [event] = self.of(card_id, "step.decision")
        self.assertEqual((event["allow"], event["commands"]), ([0, 2], ["git fetch origin", "head"]))
        call = self.resumed(card_id, decision["step"])
        argv = call["argv"]
        granted = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
        self.assertIn("Bash(git fetch origin)", granted)
        self.assertIn("Bash(head)", granted)
        self.assertFalse([a for a in granted if "git push" in a or "make install" in a], granted)
        self.assertIn("Bash(make install)", argv[argv.index("--disallowedTools") + 1:])
        prompt = call["prompt"]
        self.assertIn("Użytkownik zezwolił wyłącznie na te pojedyncze polecenia", prompt)
        self.assertIn("- git fetch origin\n- head\n", prompt)
        self.assertIn("nie uruchamiaj ich i kontynuuj bez nich:\n- git push origin\n- make install\n"
                      "- echo $(make install)", prompt)

    def test_continue_without_and_instead_reach_resume_context(self):
        card_id, _card, decision = self.chain_decision()
        reply = decision["reply"]
        self.assertEqual(decision["otherTitle"], "Zrób zamiast tego…")
        answer = self.decide(card_id, {**reply["params"], reply["field"]: "Sprawdź gałęzie przez git branch -a."})
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.of(card_id, "step.decision")[0]["instead"], "Sprawdź gałęzie przez git branch -a.")
        prompt = self.resumed(card_id, decision["step"])["prompt"]
        self.assertIn("Polecenie nie zostało uruchomione: ", prompt)
        self.assertIn("prosi, żebyś zamiast tego: Sprawdź gałęzie przez git branch -a.", prompt)
        self.assertNotIn("git fetch origin", self.store.load().projects["proj"].allowed_commands)

    def test_continue_without_commands_option(self):
        card_id, _card, decision = self.chain_decision()
        deny = decision["options"][0]
        self.assertEqual((deny["title"], deny["recommended"]), ("Kontynuuj bez tych poleceń", True))
        self.assertTrue(self.decide(card_id, deny["params"])["ok"])
        prompt = self.resumed(card_id, decision["step"])["prompt"]
        self.assertIn("Użytkownik odmówił zgody na polecenie: ", prompt)
        self.assertIn("Kontynuuj bez niego.", prompt)

    def test_only_own_step_reads_are_never_a_question(self):
        card_id = self.card(description="Czytaj kartkę.\n\nATRAPA: zgoda-katalog-kroku")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "step.decision"), timeout=30), self.events(card_id))
        [event] = self.of(card_id, "step.decision")
        self.assertTrue(event["auto"])
        self.assertIn("zawsze dozwolony", event["answer"])
        [finished] = self.of(card_id, "step.finished")[:1]
        self.assertEqual(finished["commands"], [])
        self.assertIn("zawsze dozwolony", self.resumed(card_id, finished["step"])["prompt"])


class ChainPermissionRequestTest(FlowInProcess):
    """Walidacja `allow` w StepResolve na dzienniku złożonym ręcznie (bez ticków)."""

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.engine = self.flow.engine
        changed = json.loads(self.board.upsert_project(json.dumps({"slug": "proj", "deniedCommands": ["make install"]})))
        self.assertTrue(changed["ok"], changed)
        self.card_id = self.card()
        self.step = f"{self.card_id}-PT-1-1"
        journal = self.engine.journal
        journal.append(self.card_id, "card.started", entry="now", executor="Sonnet", program="claude",
                       provider="anthropic")
        journal.append(self.card_id, "step.started", step=self.step, ticket="PT-1", kind="ticket", executor="Sonnet",
                       provider="anthropic", attempt=0, stepDir="/wt/x.step")
        journal.append(self.card_id, "step.finished", step=self.step, ticket="PT-1", result="denied", command=CHAIN,
                       canAllow=False)

    def refused(self, request) -> str:
        from tide_daemon.cardflow.engine import FlowError
        with self.assertRaises(FlowError) as caught:
            self.engine.resolve_step(self.card_id, {"step": self.step, **request})
        self.assertEqual([e for e in self.engine.journal.read(self.card_id) if e["type"] == "step.decision"], [])
        self.assertEqual(self.store.load().projects["proj"].allowed_commands.count("git fetch origin"), 0)
        return caught.exception.code

    def test_index_out_of_range_is_an_error(self):
        for allow in ([5], [-1], [0, 9]):
            with self.subTest(allow=allow):
                self.assertEqual(self.refused({"choice": "allow", "allow": allow}), "bad-allow")

    def test_index_of_forbidden_command_is_an_error(self):
        for allow in ([3], [4], [0, 3]):
            with self.subTest(allow=allow):
                self.assertEqual(self.refused({"choice": "allow", "allow": allow}), "forbidden-command")

    def test_malformed_allow_is_an_error(self):
        for allow in ([], [0, 0], ["0"], [True], "0", {"0": 1}):
            with self.subTest(allow=allow):
                self.assertEqual(self.refused({"choice": "allow", "allow": allow}), "bad-allow")
        self.assertEqual(self.refused({"choice": "deny", "allow": [0]}), "bad-request")
        self.assertEqual(self.refused({"choice": "allow", "allow": [0], "instead": "x"}), "bad-request")

    def test_whole_chain_without_allow_is_still_refused(self):
        self.assertEqual(self.refused({"choice": "allow"}), "forbidden-command")

    def test_denial_added_after_the_step_still_wins(self):
        changed = json.loads(self.board.upsert_project(json.dumps({"slug": "proj",
                                                                   "deniedCommands": ["make install", "git push *"]})))
        self.assertTrue(changed["ok"], changed)
        self.assertEqual(self.refused({"choice": "allow", "allow": [1]}), "forbidden-command")

    def test_list_shown_in_window_must_match_current_split(self):
        """Review PT-124 r1 NIT-4: indeksy odnoszą się do listy zapisanej przy wyniku kroku (tej z okna)."""
        from tide_daemon.board import chain
        shown = chain.describe(CHAIN, denied=["make install"], step_dir="/wt/x.step")
        self.engine.journal.append(self.card_id, "step.finished", step=self.step, ticket="PT-1", result="denied",
                                   command=CHAIN, canAllow=False, commands=list(reversed(shown)))
        self.assertEqual(self.refused({"choice": "allow", "allow": [0]}), "stale-decision")
        self.engine.journal.append(self.card_id, "step.finished", step=self.step, ticket="PT-1", result="denied",
                                   command=CHAIN, canAllow=False, commands=shown)
        reply = self.engine.resolve_step(self.card_id, {"step": self.step, "choice": "allow", "allow": [0]})
        self.assertTrue(reply["ok"], reply)

    def test_allow_writes_only_selected_and_repeated_request_is_idempotent(self):
        reply = self.engine.resolve_step(self.card_id, {"step": self.step, "choice": "allow", "allow": [0]})
        self.assertTrue(reply["ok"], reply)
        project = self.store.load().projects["proj"]
        self.assertIn("git fetch origin", project.allowed_commands)
        self.assertNotIn("git push origin", project.allowed_commands)
        again = self.engine.resolve_step(self.card_id, {"step": self.step, "choice": "allow", "allow": [0]})
        self.assertTrue(again.get("already"), again)


class OneCommandChainDecisionTest(unittest.TestCase):
    """Review PT-124 r2 MAJOR-1: łańcuch, z którego po odjęciu własnego odczytu katalogu kroku zostaje jedno
    polecenie, jest decyzją łańcuchową z tym poleceniem na liście i na wierzchu."""

    def decision(self, rest):
        from tide_daemon.board.chain import describe
        from tide_daemon.cardflow.decisions import build_decisions
        from tide_daemon.cardflow.state import fold
        step_dir = "/wt/x.step"
        command = f"ls -la {step_dir}/in {step_dir}/out && {rest}"
        events = [
            {"v": 1, "seq": 1, "ts": "2026-11-03T10:00:00+01:00", "card": "K-1", "type": "card.started",
             "entry": "now", "executor": "Sonnet", "program": "claude", "provider": "anthropic"},
            {"v": 1, "seq": 2, "ts": "2026-11-03T10:01:00+01:00", "card": "K-1", "type": "step.started",
             "step": "K-1-PT-1-1", "ticket": "PT-1", "kind": "ticket", "executor": "Sonnet", "provider": "anthropic",
             "attempt": 0, "parentStep": "", "stepDir": step_dir},
            {"v": 1, "seq": 3, "ts": "2026-11-03T10:02:00+01:00", "card": "K-1", "type": "step.finished",
             "step": "K-1-PT-1-1", "ticket": "PT-1", "result": "denied", "command": command, "canAllow": False,
             "commands": describe(command, denied=["make install"], step_dir=step_dir)},
        ]
        [decision] = build_decisions(fold(events))
        return decision

    def test_one_command_left_is_a_chain_decision_with_the_command_visible(self):
        decision = self.decision("git fetch origin")
        self.assertEqual([(c["text"], c["canAllow"], c["preselected"]) for c in decision["commands"]],
                         [("git fetch origin", True, False)])
        self.assertIn("git fetch origin", decision["happened"])
        allow = [o for o in decision["options"] if o["choice"] == "allow"]
        self.assertEqual([o["params"]["allow"] for o in allow], [[]], "okno uzupełnia indeksy z listy")

    def test_one_read_only_command_left_is_preselected(self):
        decision = self.decision("ls docs")
        self.assertEqual([(c["text"], c["readOnly"], c["preselected"]) for c in decision["commands"]],
                         [("ls docs", True, True)])

    def test_one_forbidden_command_left_has_reason_and_no_allow(self):
        decision = self.decision("make install")
        self.assertEqual([(c["forbidden"], c["canAllow"], c["reason"]) for c in decision["commands"]],
                         [(True, False, "Zabronione w projekcie regułą „make install”.")])
        self.assertEqual([o["choice"] for o in decision["options"]], ["deny"])


class ResumeContextTest(unittest.TestCase):
    def test_texts(self):
        prior = {"result": "denied"}
        allowed = handoff.resume_context(choice="allow", prior=prior, answer="", command=CHAIN, out_dir="/o",
                                         allowed=["git fetch origin"], skipped=["git push origin"])
        self.assertIn(f"Prosiłeś o zgodę na łańcuch: {CHAIN}", allowed)
        self.assertIn("wyłącznie na te pojedyncze polecenia (dopisane do listy dozwolonych):\n- git fetch origin\n",
                      allowed)
        self.assertIn("nie uruchamiaj ich i kontynuuj bez nich:\n- git push origin\n", allowed)
        single = handoff.resume_context(choice="allow", prior=prior, answer="", command="git fetch origin", out_dir="/o")
        self.assertIn("Użytkownik zezwolił na dokładne polecenie: git fetch origin", single)
        without = handoff.resume_context(choice="deny", prior=prior, answer="", command=CHAIN, out_dir="/o")
        self.assertIn(f"Użytkownik odmówił zgody na polecenie: {CHAIN}. Kontynuuj bez niego.", without)
        instead = handoff.resume_context(choice="deny", prior=prior, answer="", command=CHAIN, out_dir="/o",
                                         instead="przeczytaj tylko docs/agent-plan-standard.md")
        self.assertIn(f"Polecenie nie zostało uruchomione: {CHAIN}\n", instead)
        self.assertIn("prosi, żebyś zamiast tego: przeczytaj tylko docs/agent-plan-standard.md", instead)
        self.assertIn("Plik wyniku: /o/result.json", instead)


if __name__ == "__main__":
    unittest.main()
