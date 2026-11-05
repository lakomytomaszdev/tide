"""Tekst planu wokół tabeli ticketów przeżywa aktualizację tabeli (05.11, K-50: znikał odnośnik do szkicu)."""
import unittest

from tide_daemon.cardflow.engine import FlowEngine

PLAN = """---
tide-plan: 1
title: Plan karty K-1
---

Wstęp planu.

| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PT-1 | A | todo | ticket/pt-1-a | Codex | | standard | Opus | |

Zakres poza v1: [szkic](followups/replay-code.md).
"""


class PlanExtraTest(unittest.TestCase):
    def test_text_before_and_after_the_table_is_kept(self):
        self.assertEqual(FlowEngine._plan_extra(PLAN),
                         ("Wstęp planu.", "Zakres poza v1: [szkic](followups/replay-code.md)."))

    def test_plan_without_extra_text_or_table(self):
        self.assertEqual(FlowEngine._plan_extra(PLAN.split("Wstęp")[0] + "| Ticket | x |\n| --- |\n"), ("", ""))
        self.assertEqual(FlowEngine._plan_extra("bez nagłówka"), ("", ""))


if __name__ == "__main__":
    unittest.main()
