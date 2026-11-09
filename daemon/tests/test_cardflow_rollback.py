"""PT-293 (K-38 F5): próba downgrade'u nowej historii K-38 → przypięty master. Opis harnessu: rollback_trial.py.

Historia powstaje kodem K-38 na atrapach (bez ręcznego JSON), stary silnik działa w osobnym procesie na kodzie
z `git archive` przypiętego mastera i czyta ten sam autorytatywny dziennik (mirror usługi).
"""
import unittest

from .rollback_trial import RollbackFlow


class CleanFoldThesisTest(RollbackFlow):
    maxDiff = None

    def test_clean_fold_of_new_history_means_safe_resume_on_pinned_master(self):
        """Teza: skoro fold przypiętego mastera czyta historię K-38 bez wyjątku, stary silnik może ją bezpiecznie
        wznowić (bez startu na hoście kroków, które K-38 trzyma w pudełku lub w oczekiwaniu)."""
        cards = self.generate()
        self.stop_flow()            # usługa K-38 zatrzymana; proces kroku w pudełku żyje dalej
        report = self.old_engine("downgrade", actions=self.user_actions(cards))
        self.write_artifact("history", self.snapshot(cards))
        self.write_artifact("old-engine", report)
        self.assertEqual({c: f["ok"] for c, f in report["fold"].items()}, {c: True for c in report["fold"]},
                         report["fold"])
        self.assertEqual(self.unsafe_effects(cards, report), [])


if __name__ == "__main__":
    unittest.main()
