"""Kształt zapisanych danych odbioru rdzenia K-99 (PT-471): prawdziwe odpowiedzi usługi, bez ścieżek domowych.

Dane powstały skryptem `scripts/k99/capture_acceptance.py`; ten test pilnuje, żeby okna (#24) dostały to, czego
oczekują, i żeby liczby w `capture.json` zgadzały się z resztą plików."""
import json
import os
import re
import unittest

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "k99_acceptance")
FILES = ("branchmap.json", "list_card_branches.json", "preview_card_merge.json", "capture.json")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


class AcceptanceDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree, cls.listing, cls.preview, cls.capture = (load(n) for n in FILES)

    def test_files_exist_and_have_no_home_paths(self):
        for name in FILES:
            with open(os.path.join(DATA, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotRegex(text, r"/home/|/Users/", name)
        self.assertEqual(self.tree["repo"], "<HOME>/DevWorkspace/plasma-tide")

    def test_branch_map_has_at_least_40_branches(self):
        tree = self.tree
        self.assertTrue(tree["ok"])
        self.assertEqual(tree["project"], "plasma-tide")
        for key in ("nodes", "edges", "total", "truncated", "warnings"):
            self.assertIn(key, tree)
        branches = [n for n in tree["nodes"] if n["kind"] in ("card", "ticket")]
        self.assertGreaterEqual(tree["total"], 40)
        self.assertEqual(len(branches), tree["total"] if not tree["truncated"] else len(branches))
        self.assertEqual(self.capture["mapTotal"], tree["total"])
        self.assertEqual(self.capture["mapNodes"], {k: sum(1 for n in tree["nodes"] if n["kind"] == k)
                                                    for k in ("base", "card", "ticket", "unknown-base")})

    def test_list_card_branches_shape(self):
        listing = self.listing
        self.assertTrue(listing["ok"])
        for key in ("branches", "mainRef", "defaultTargetRef", "warnings", "hasMore"):
            self.assertIn(key, listing)
        for branch in listing["branches"]:
            for key in ("ref", "kind", "selectable", "reason"):
                self.assertIn(key, branch)
        self.assertEqual(listing["defaultTargetRef"], self.capture["target"])

    def test_preview_shape_and_numbers_match_capture(self):
        preview, capture = self.preview, self.capture
        self.assertTrue(preview["ok"])
        for key in ("id", "targetRef", "files", "fileCount", "commits", "commitCount", "canMerge", "blockedReasons",
                    "conflictCount", "previewToken"):
            self.assertIn(key, preview)
        self.assertIsInstance(preview["previewToken"], str)
        self.assertEqual(preview["id"], capture["card"])
        self.assertEqual(preview["targetRef"], capture["target"])
        self.assertEqual(len(preview["files"]), preview["fileCount"])
        self.assertEqual(len(preview["commits"]), preview["commitCount"])
        self.assertEqual(capture["preview"]["fileCount"], preview["fileCount"])
        self.assertEqual(capture["preview"]["commitCount"], preview["commitCount"])
        self.assertEqual(capture["preview"]["canMerge"], preview["canMerge"])
        self.assertEqual(capture["preview"]["blockedReasons"], preview["blockedReasons"])
        if not preview["canMerge"]:
            self.assertTrue(preview["blockedReasons"])

    def test_capture_metadata_and_selection_rule(self):
        capture = self.capture
        for key in ("capturedAt", "project", "card", "target", "cardBranch", "selection", "gitStatus", "dbusCalls"):
            self.assertIn(key, capture)
        self.assertRegex(capture["capturedAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertRegex(capture["cardBranch"]["sha"], r"^[0-9a-f]{40}$")
        selection = capture["selection"]
        self.assertIn(selection["step"], (1, 2, 3))
        self.assertEqual(selection["card"], capture["card"])
        self.assertEqual(selection["fileCount"], self.preview["fileCount"])
        if selection["step"] in (1, 2):
            self.assertGreater(selection["fileCount"], selection["threshold"])
        else:  # krok 3: największa spośród przejrzanych kart z podglądem; remis → nowsza
            counts = [r for r in selection["reviewed"] if "fileCount" in r]
            self.assertEqual(selection["fileCount"], max(r["fileCount"] for r in counts))
            tied = [int(r["card"][2:]) for r in counts if r["fileCount"] == selection["fileCount"]]
            self.assertEqual(int(selection["card"][2:]), max(tied))
        self.assertIn(selection["card"], [r["card"] for r in selection["reviewed"]])

    def test_only_read_calls_were_sent_to_the_service(self):
        members = {m for m, _ in self.capture["dbusCalls"]}
        self.assertLessEqual(members, {"Snapshot", "ListCardBranches", "PreviewCardMerge"})
        self.assertFalse(any(re.search("Merge(Card|CardToMain)$|Create|Update|BranchMap", m) for m in members))


if __name__ == "__main__":
    unittest.main()
