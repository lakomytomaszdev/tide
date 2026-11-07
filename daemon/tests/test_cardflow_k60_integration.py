"""Regresje styków K-60 z opiekunem na dziennikach w pamięci, bez procesów kroków."""
import unittest

from tide_daemon.cardflow import stepnotes
from tide_daemon.cardflow.state import fold

from .test_cardflow_caretaker import Journal


class CaretakerPlanTest(unittest.TestCase):
    def test_manual_append_preserves_caretaker_resume_blockers(self):
        j = Journal()
        first = {"id": "PT-1", "blockers": []}
        second = {"id": "PT-2", "blockers": []}
        third = {"id": "PT-3", "blockers": []}
        j.add("plan.approved", rows=[first])
        j.step("s1", ticket="PT-1")
        j.error("s1")
        j.add("plan.returned", caretaker="error:s1", caretakerFrom="s1")
        j.add("plan.revision-approved", plan={"rows": [first, second]})
        self.assertEqual(j.st.plan_resumes, {"s1": {"PT-2"}})

        # Ręczne dopisanie niezależnej pracy nie zmienia wcześniejszego oczekiwania opiekuna.
        j.add("plan.proposed", rows=[first, second, third],
              appendRevision={"kind": "append", "reviewer": "Opus"})
        j.add("plan.revision-approved", plan={"rows": [first, second, third]})
        self.assertEqual(fold(j.events).plan_resumes, {"s1": {"PT-2"}})

    def test_caretaker_return_plan_delivers_pending_notes_to_resumed_step(self):
        j = Journal()
        first = {"id": "PT-1", "blockers": []}
        second = {"id": "PT-2", "blockers": []}
        j.add("plan.approved", rows=[first])
        j.step("s1", ticket="PT-1")
        j.error("s1")
        j.add("step.note-added", step="s1", orderSeq=1, noteId="n1",
              requestId="r1", text="Zachowaj format daty ISO.")
        j.add("plan.returned", caretaker="error:s1", caretakerFrom="s1")
        # Odesłanie planu ukrywa dawny błąd, lecz nie zamyka kolejki uwag do wznowienia.
        self.assertIn("s1", j.st.superseded)
        with self.subTest(stage="plan-returned"):
            self.assertEqual(stepnotes.view(j.st)[0]["status"], "pending")
        j.add("plan.revision-approved", plan={"rows": [first, second]})
        j.add("result", action="merge", key="PT-2", ok=True)

        prompt, ids = stepnotes.prepare(fold(j.events), "s1", "Kontynuuj ticket.")
        self.assertEqual(ids, ["n1"])
        self.assertIn("Zachowaj format daty ISO.", prompt)
        self.assertEqual(stepnotes.view(j.st)[0]["status"], "pending")
        j.add("step.started", step="s2", kind="ticket", ticket="PT-1", parentStep="s1",
              deliveredNoteIds=ids)
        self.assertNotIn("s1", j.st.plan_resumes)
        self.assertEqual(stepnotes.view(j.st)[0]["status"], "delivered")
        self.assertEqual(stepnotes.prepare(j.st, "s2", prompt), ("Kontynuuj ticket.", []))
