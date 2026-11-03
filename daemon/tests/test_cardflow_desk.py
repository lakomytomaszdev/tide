"""PT-183: sekcja „Do sprawdzenia na pulpicie” w notkach kroków i jej zebranie w domknięciu karty."""
import unittest

from tide_daemon.cardflow import handoff


class DeskItemsTest(unittest.TestCase):
    def test_two_items(self):
        note = "# Notka\n\nZrobione.\n\n## Do sprawdzenia na pulpicie\n\n- Pierwsza\n- [ ] Druga\n\n## Ograniczenia\n- nie to\n"
        self.assertEqual(handoff.desk_items(note), ["Pierwsza", "Druga"])

    def test_empty_or_missing_section(self):
        self.assertEqual(handoff.desk_items("# Notka\n\n## Do sprawdzenia na pulpicie\n\n## Dalej\n- x\n"), [])
        self.assertEqual(handoff.desk_items("# Notka\n- x\n"), [])
        self.assertEqual(handoff.desk_items(""), [])

    def test_multiline_item(self):
        note = "### Do sprawdzenia na pulpicie:\n1. Otwórz okno\n   i sprawdź ramkę\n2. Drugi\n"
        self.assertEqual(handoff.desk_items(note), ["Otwórz okno i sprawdź ramkę", "Drugi"])

    def test_same_item_twice_appears_once_with_both_steps(self):
        text = handoff.desk_section([("s1", "PT-1", ["Otwórz  okno"]), ("s2", "PT-2", ["otwórz okno", "Inne"])])
        self.assertEqual(text.count("Otwórz okno"), 1)
        self.assertIn("Otwórz okno (krok s1, ticket PT-1; krok s2, ticket PT-2)", text)
        self.assertIn("- Inne (krok s2, ticket PT-2)", text)

    def test_no_items_no_section(self):
        self.assertEqual(handoff.desk_section([]), "")


class DeskHintTest(unittest.TestCase):
    def test_ticket_and_review_prompts_carry_hint(self):
        ticket = handoff.ticket_card(card_id="K-1", title="t", description="d", project="p", ticket="PT-1",
                                     branch="b", card_branch="c", worktree="w", out_dir="o")
        review = handoff.review_card(card_id="K-1", title="t", description="d", ticket="PT-1", round_no=1,
                                     card_branch="c", ticket_branch="b", worktree="w", out_dir="o",
                                     diff="", note="", rules="")
        for prompt in (ticket, review):
            self.assertIn("Do sprawdzenia na pulpicie", prompt)
            self.assertIn("nie proś o to użytkownika w trakcie", prompt)
