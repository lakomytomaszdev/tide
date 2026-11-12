"""„Zmiany gałęzi” na Tablicy (K-36, PT-356): panel karty, pełna karta i kafelek otwierają wspólny widok diffa
(apps/common/qml/git/DiffView.qml) źródła `card` jako nakładkę; Esc wraca do miejsca otwarcia.

- sonda offscreen: wywołanie `Git.Diff` z `{"source":"card","card":"K-N"}` z panelu (karta w toku), z pełnej karty
  (praca w toku) i z kafelka w „Do sprawdzenia”; Esc zamyka nakładkę i zostawia panel albo pełną kartę,
- karta bez gałęzi nie ma przycisku; pola tekstowego diffu w panelu nie ma; pełna karta nie odsyła do panelu.
"""
import json
import unittest

from .decisionfix import full_d
from .test_view import FIND_PY, ProbeCase


def with_branch(data: dict, card_id: str, column: str, branch: str | None = "card/k-6-eksport") -> dict:
    data = json.loads(json.dumps(data))
    for card in data["cards"]:
        if card["id"] == card_id:
            flow = dict(card.get("derived", {}).get("flow") or {})
            if branch:
                flow["cardBranch"] = branch
            card["derived"] = dict(card.get("derived") or {}, column=column, flow=flow)
            card["boardColumn"] = column
    return data


class BranchChangesTest(ProbeCase):
    def test_panel_in_progress_opens_diff_and_escape_returns(self):
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["panel"] = js("page.panelOpen")
out["button"] = obj("cardBranchDiff").property("visible")
out["label"] = obj("cardBranchDiff").property("text")
out["textField"] = js("(" + FIND + ")(page.cardPanel, 'cardBranchDiffText') === null")
click(named("cardBranchDiff"))
out["open"] = js("page.diffOpen")
out["call"] = calls()[-1]
key(Qt.Key.Key_Escape)
out["closed"] = not js("page.diffOpen")
out["panelAfter"] = js("page.panelOpen && page.panelCardId")
""", with_branch(self.data, "K-6", "progress"))
        self.assertTrue(out["panel"])
        self.assertTrue(out["button"])
        self.assertEqual(out["label"], "Zmiany gałęzi")
        self.assertTrue(out["textField"], "pole tekstowe diffu w panelu nie istnieje")
        self.assertTrue(out["open"])
        self.assertEqual(out["call"][0], "Diff")
        self.assertEqual(json.loads(out["call"][1]), {"source": "card", "card": "K-6"})
        self.assertTrue(out["closed"])
        self.assertEqual(out["panelAfter"], "K-6")

    def test_panel_without_branch_has_no_button(self):
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["button"] = obj("cardBranchDiff").property("visible")
""", with_branch(self.data, "K-6", "progress", branch=None))
        self.assertFalse(out["button"])

    def test_full_card_in_progress_opens_diff_without_panel_redirect(self):
        data = with_branch(full_d(self.data), "K-8", "progress", branch="card/k-8-praca")
        out = self.probe(FIND_PY + """
js("page.openFull('K-8')")
until(lambda: js("page.cardFullView.visible && page.cardFullView.width > 0"))
wait(400)   # układ pełnej karty się ustala
out["full"] = js("page.fullOpen")
out["button"] = obj("fullBranchDiff").property("visible")
out["label"] = obj("fullBranchDiff").property("text")
click(at("(" + FIND + ")(page.cardFullView, 'fullBranchDiff')"))
out["open"] = js("page.diffOpen")
out["call"] = calls()[-1]
out["panelOpened"] = js("page.panelOpen")
out["notice"] = js("page.cardPanel.notice")
key(Qt.Key.Key_Escape)
out["closed"] = not js("page.diffOpen")
out["fullAfter"] = js("page.fullOpen && page.fullCardId")
""", data)
        self.assertTrue(out["full"])
        self.assertTrue(out["button"])
        self.assertEqual(out["label"], "Zmiany gałęzi")
        self.assertTrue(out["open"], out)
        self.assertEqual(out["call"][0], "Diff")
        self.assertEqual(json.loads(out["call"][1]), {"source": "card", "card": "K-8"})
        self.assertFalse(out["panelOpened"])
        self.assertNotIn("Diff gałęzi", out["notice"])
        self.assertTrue(out["closed"])
        self.assertEqual(out["fullAfter"], "K-8")

    def test_tile_button_opens_same_view(self):
        data = with_branch(self.data, "K-6", "review")
        out = self.probe(FIND_PY + """
out["button"] = js("page.canDiff")
click(at("(" + FIND + ")(page.findCard('K-6'), 'cardTileDiff')"))
out["open"] = js("page.diffOpen && page.diffCardId")
out["call"] = calls()[-1]
key(Qt.Key.Key_Escape)
out["closed"] = not js("page.diffOpen")
""", data)
        self.assertEqual(out["open"], "K-6")
        self.assertEqual(out["call"][0], "Diff")
        self.assertEqual(json.loads(out["call"][1]), {"source": "card", "card": "K-6"})
        self.assertTrue(out["closed"])


if __name__ == "__main__":
    unittest.main()
