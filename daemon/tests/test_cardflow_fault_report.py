"""PT-125: „Opisz problem i ponów” — PreviewStepFault i ReportStepFault (/Board) w procesie.

- pełna ścieżka na atrapie kroku: piaskownica odmawia zapisu pliku wyniku dwa razy (z automatycznym ponowieniem),
  `fault=tide` w zakończeniu kroku, decyzja w `flow.decisions[]`, kafelek „błąd Tide w kroku …”, podgląd karty bez
  zapisu, zgłoszenie zakłada kartę w Backlogu projektu plasma-tide (bez sekretów z dziennika kroku) i ponawia krok,
  który kończy się wynikiem,
- deduplikacja po podpisie na dzienniku składanym ręcznie: druga awaria dopisuje wystąpienie do otwartej karty;
  karta zarchiwizowana albo scalona (przepływ z `card.main-merged`) nie przyjmuje wystąpienia — powstaje nowa.
"""
import json
import os

from tide_daemon.cardflow import faults

from .gitrepos import git
from .privatebus import wait_until
from .test_cardflow import FlowInProcess

TOKEN = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
ENV_SECRET = "atrapa-sekret-z-env-42"


class FaultReportCase(FlowInProcess):
    def setUp(self):
        super().setUp()
        self.tide_repo = os.path.join(self.base, "ws", "plasma-tide")
        os.makedirs(self.tide_repo)
        git(self.tide_repo, "init", "-q")
        git(self.tide_repo, "commit", "-q", "--allow-empty", "-m", "init")
        reply = json.loads(self.board.upsert_project(json.dumps({"repo": self.tide_repo})))
        self.assertTrue(reply["ok"], reply)
        self.assertIn("plasma-tide", self.store.load().projects)
        os.environ["TIDE_FAKE_SECRET_TOKEN"] = ENV_SECRET   # cofa patch.dict z FlowInProcess

    def call(self, method, card_id, request):
        box = []
        getattr(self.board, method)(card_id, json.dumps(request, ensure_ascii=False), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), f"{method} bez odpowiedzi")
        return json.loads(box[0])

    def tide_cards(self):
        return sorted((c for c in self.store.load().cards.values() if c.project == "plasma-tide"),
                      key=lambda c: c.num)


class FullPathTest(FaultReportCase):
    def test_write_denial_is_reported_as_tide_card_and_the_step_resumes(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: odmowa-zapisu")
        self.assertTrue(self.start(card_id)["ok"])
        # Przejściowe „Czeka” po błędzie może wyprzedzać start opiekuna. Zgłoszenie testujemy dopiero,
        # gdy opiekun odda problem użytkownikowi, a nie podczas przejmowania go w tym samym przebiegu.
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.escalated"), timeout=60), self.events(card_id))
        card = self.wait_column(card_id, "waiting", timeout=60)
        tickets = {s["step"] for s in self.of(card_id, "step.started") if s["kind"] == "ticket"}
        finished = [e for e in self.of(card_id, "step.finished") if e["step"] in tickets]   # bez kroku opiekuna
        self.assertEqual([(e["reason"], e["fault"], e["faultCause"]) for e in finished],
                         [("no-result", "tide", "write-denied")] * 2, "obie próby: odmowa zapisu → tide")
        [decision] = card["derived"]["flow"]["decisions"]
        self.assertEqual((decision["kind"], decision["fault"]), ("error", "tide"))
        ticket = decision["ticket"]
        self.assertTrue(card["derived"]["note"].startswith(f"błąd Tide w kroku {ticket}"), card["derived"]["note"])
        self.assertEqual(decision["reply"]["method"], "ReportStepFault")

        request = {**decision["reply"]["params"], "description": f"Po aktualizacji Tide to samo. Token {TOKEN}"}
        preview = self.call("preview_step_fault", card_id, request)
        self.assertTrue(preview["ok"], preview)
        self.assertEqual((preview["action"], preview["project"], preview["card"]), ("create", "plasma-tide", ""))
        self.assertTrue(preview["title"].startswith("Błąd Tide w kroku ticket"), preview["title"])
        self.assertIn("> Po aktualizacji Tide to samo.", preview["description"])
        self.assertIn("Podpis błędu: `ticket/tide/write-denied`", preview["description"])
        self.assertIn("odmowa Write:", preview["description"], "wycinek z dziennika kroku")
        self.assertEqual(self.tide_cards(), [], "podgląd niczego nie zapisuje")
        self.assertEqual(self.of(card_id, "step.decision"), [])

        reported = self.call("report_step_fault", card_id, request)
        self.assertTrue(reported["ok"], reported)
        self.assertEqual((reported["action"], reported["choice"], reported["step"]),
                         ("create", "retry", decision["step"]))
        [saved] = self.tide_cards()
        self.assertEqual((saved.id, saved.column, saved.title), (reported["reported"], "backlog", preview["title"]))
        for text in (preview["description"], saved.description):
            for secret in (TOKEN, ENV_SECRET, os.environ["HOME"]):
                self.assertNotIn(secret, text)
            self.assertIn("GITHUB_TOKEN=•••", text)
            self.assertIn("TIDE_FAKE_SECRET_TOKEN=•••", text)
        [event] = self.of(card_id, "step.reported")
        self.assertEqual((event["step"], event["reported"], event["signature"]),
                         (decision["step"], saved.id, "ticket/tide/write-denied"))

        resumed = wait_until(lambda: [s for s in self.of(card_id, "step.started")
                                      if s.get("parentStep") == decision["step"]], timeout=30)
        self.assertTrue(resumed, self.events(card_id))
        self.assertTrue(wait_until(lambda: any(e["step"] == resumed[0]["step"] and e["result"] == "done"
                                               for e in self.of(card_id, "step.finished")), timeout=30),
                        self.events(card_id))
        self.assertEqual(self.of(card_id, "step.decision")[0]["choice"], "retry")
        again = self.call("report_step_fault", card_id, request)
        self.assertFalse(again["ok"], "krok już wznowiony — drugie zgłoszenie nie zakłada karty")
        self.assertEqual(len(self.tide_cards()), 1)


class DeduplicationTest(FaultReportCase):
    """Dzienniki składane ręcznie (bez ticków): błąd kroku po automatycznym ponowieniu z odmową zapisu."""

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.engine = self.flow.engine

    def failed_card(self, title, reason="no-result", fault="tide", cause="write-denied"):
        card_id = self.card(title=title)
        journal = self.engine.journal
        journal.append(card_id, "card.started", entry="now", executor="Sonnet", program="claude", provider="anthropic")
        for attempt in (0, 1):
            name = f"{card_id}-PT-1-{attempt + 1}"
            step_dir = os.path.join(self.base, "steps", name + ".step")
            os.makedirs(os.path.join(step_dir, "out"))
            with open(os.path.join(step_dir, "out", "step.log"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "result", "result": f"brak wyniku, HOME={os.environ['HOME']}",
                                     "permission_denials": [{"tool_name": "Write", "tool_input": {
                                         "file_path": os.path.join(step_dir, "out", "result.json")}}]}) + "\n")
            journal.append(card_id, "step.started", step=name, ticket="PT-1", kind="ticket", executor="Sonnet",
                           provider="anthropic", attempt=attempt, stepDir=step_dir,
                           parentStep=f"{card_id}-PT-1-1" if attempt else "")
            journal.append(card_id, "step.finished", step=name, ticket="PT-1", result="error", reason=reason,
                           fault=fault, faultCause=cause)
        return card_id, f"{card_id}-PT-1-2"

    def report(self, title):
        card_id, step = self.failed_card(title)
        reply = self.engine.report_step_fault(card_id, {"step": step, "description": f"Znowu: {title}"})
        self.assertEqual(reply["choice"], "retry")
        return reply

    def test_second_failure_with_same_signature_appends_to_the_open_card(self):
        first = self.report("Pierwsza")
        second = self.report("Druga")
        self.assertEqual((first["action"], second["action"]), ("create", "append"))
        self.assertEqual(second["reported"], first["reported"])
        [saved] = self.tide_cards()
        self.assertEqual(saved.description.count("### "), 2, "dwa wystąpienia w jednej karcie")
        self.assertIn("> Znowu: Druga", saved.description)
        self.assertNotIn(os.environ["HOME"], saved.description)

    def test_archived_card_gets_no_more_occurrences(self):
        first = self.report("Pierwsza")
        moved = json.loads(self.board.move_card(first["reported"], "archived", -1))
        self.assertTrue(moved["ok"], moved)
        second = self.report("Druga")
        self.assertEqual(second["action"], "create")
        self.assertNotEqual(second["reported"], first["reported"])
        self.assertEqual(len(self.tide_cards()), 2)

    def test_merged_card_gets_no_more_occurrences(self):
        first = self.report("Pierwsza")
        self.engine.journal.append(first["reported"], "card.started", entry="now", executor="Sonnet")
        self.engine.journal.append(first["reported"], "card.main-merged", commit="abc")
        second = self.report("Druga")
        self.assertEqual(second["action"], "create")
        self.assertNotEqual(second["reported"], first["reported"])

    def test_token_in_the_agents_reason_is_masked_and_the_signature_is_stable(self):
        """Powód z `result.json` agenta (np. z tokenem) nie trafia do karty w innym projekcie ani do podpisu."""
        cards = []
        for title, reason in (("Pierwsza", f"curl z GITHUB_TOKEN={TOKEN} nie zadziałał"),
                              ("Druga", f"znowu {TOKEN} i inny tekst")):
            # zapis kroku jak w engine: classify() przy wyniku
            found = faults.classify(reason)
            self.assertEqual(found, {"fault": "unknown", "cause": "other"})
            card_id, step = self.failed_card(title, reason=reason, fault=found["fault"], cause=found["cause"])
            preview = self.engine.preview_step_fault(card_id, {"step": step, "description": "Coś nie tak"})
            reply = self.engine.report_step_fault(card_id, {"step": step, "description": "Coś nie tak"})
            self.assertEqual(reply["choice"], "retry")
            cards.append(reply)
            [saved] = self.tide_cards()
            for text in (preview["title"], preview["description"], saved.title, saved.description):
                self.assertNotIn(TOKEN, text)
            self.assertIn("Podpis błędu: `ticket/unknown/other`", saved.description)
        self.assertEqual([c["action"] for c in cards], ["create", "append"],
                         "ta sama awaria z innym tekstem agenta dopisuje wystąpienie")

    def test_preview_and_report_need_a_waiting_error_and_a_description(self):
        card_id, step = self.failed_card("Pierwsza")
        from tide_daemon.cardflow.engine import FlowError
        with self.assertRaises(FlowError):
            self.engine.preview_step_fault(card_id, {"step": step, "description": "  "})
        with self.assertRaises(FlowError):
            self.engine.report_step_fault(card_id, {"step": f"{card_id}-PT-1-1", "description": "stary krok"})
        with self.assertRaises(FlowError):
            self.engine.report_step_fault(card_id, {"step": step, "description": "x", "project": "proj"})
        self.assertEqual(self.tide_cards(), [])
