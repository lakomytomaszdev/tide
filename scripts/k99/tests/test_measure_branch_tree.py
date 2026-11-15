"""Skrypt pomiaru budowy drzewa mapy (K-99, PT-472): obliczenia i zestaw 500 węzłów, bez okna Qt.

- najlepszy / mediana / najgorszy (nieparzysta i parzysta liczba próbek, jedna próbka, brak próbek),
- kryteria: mediana drzewa dla `branchmap.json` + mediana usługi < 1 s, mediana 500 węzłów < 1 s, sprawdzenia budowy,
- zestaw 500 węzłów liczony projekcją PT-387 na dzienniku z prawdziwych typów zdarzeń: 500 gałęzi z 600, ticket zawsze
  z kartą, korekta bazy, scalenia (lokalne i zdalne), baza nieznana, tickety scalone / odrzucone / czynne,
- sprawdzenia drzewa (`expected_checks`) i odmowa pomiaru z < 5 powtórzeń.
Sam pomiar w oknie (Qt) robi test apps/worktrees/tests/test_k99_tree_build.py (tryb --smoke) i polecenie `outside`.
"""
import contextlib
import io
import unittest

from scripts.k99 import measure_branch_tree as tree


def result(median, passed=True):
    return {"seconds": tree.summarize([median]), "checks": {"passed": passed}}


class SummarizeTest(unittest.TestCase):
    def test_best_median_worst_odd(self):
        out = tree.summarize([0.5, 0.1, 0.9, 0.3, 0.2])
        self.assertEqual((out["best"], out["median"], out["worst"]), (0.1, 0.3, 0.9))
        self.assertEqual(out["samples"], [0.5, 0.1, 0.9, 0.3, 0.2])

    def test_median_of_an_even_count_is_the_mean_of_the_middle_two(self):
        out = tree.summarize([4.0, 1.0, 3.0, 2.0])
        self.assertEqual((out["best"], out["median"], out["worst"]), (1.0, 2.5, 4.0))

    def test_single_sample_and_no_samples(self):
        out = tree.summarize([0.25])
        self.assertEqual((out["best"], out["median"], out["worst"]), (0.25, 0.25, 0.25))
        with self.assertRaises(ValueError):
            tree.summarize([])


class VerdictTest(unittest.TestCase):
    def test_branchmap_needs_tree_plus_service_median_below_one_second(self):
        results = {"branchmap": result(0.4), "nodes500": result(0.7)}
        ok = tree.verdict(results, service_median=0.5)
        self.assertTrue(ok["branchmapSumChecked"])
        self.assertAlmostEqual(ok["branchmapSum"], 0.9)
        self.assertTrue(ok["passed"])
        over = tree.verdict(results, service_median=0.6)
        self.assertFalse(over["branchmapBelowLimit"])
        self.assertFalse(over["passed"])
        self.assertTrue(over["nodes500BelowLimit"], "500 węzłów oceniamy osobno")

    def test_without_the_service_median_only_the_tree_is_checked_and_the_budget_is_reported(self):
        out = tree.verdict({"branchmap": result(0.25), "nodes500": result(0.5)})
        self.assertFalse(out["branchmapSumChecked"])
        self.assertAlmostEqual(out["serviceBudgetSeconds"], 0.75)
        self.assertTrue(out["passed"])
        self.assertFalse(tree.verdict({"branchmap": result(1.2), "nodes500": result(0.5)})["passed"])

    def test_500_nodes_must_build_in_under_one_second_and_build_checks_must_pass(self):
        self.assertFalse(tree.verdict({"branchmap": result(0.1), "nodes500": result(1.0)})["passed"])
        self.assertFalse(tree.verdict({"branchmap": result(0.1), "nodes500": result(0.2, passed=False)})["passed"])


class SyntheticMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reply = tree.synthetic_reply()

    def test_500_branches_of_600_from_the_projection(self):
        reply = self.reply
        self.assertTrue(reply["ok"])
        self.assertEqual(tree.branch_count(reply), tree.NODE_LIMIT)
        self.assertEqual((reply["total"], reply["truncated"]), (600, True))
        self.assertEqual(reply["warnings"], [])
        ids = {n["id"] for n in reply["nodes"]}
        self.assertEqual(len(ids), len(reply["nodes"]), "id węzłów są unikalne")
        for node in reply["nodes"]:
            if node["kind"] == "ticket":
                self.assertIn("card:" + node["card"], ids, "ticket nie zostaje bez swojej karty")

    def test_real_event_types_give_every_state_and_edge_kind(self):
        nodes = self.reply["nodes"]
        cards = [n for n in nodes if n["kind"] == "card"]
        tickets = [n for n in nodes if n["kind"] == "ticket"]
        self.assertEqual({n["state"] for n in cards}, {"active", "closed", "merged"})
        self.assertEqual({n["state"] for n in tickets}, {"merged", "rejected", "active", "closed"})
        self.assertTrue(any("initial" in n["started"] for n in cards), "korekta bazy")
        self.assertTrue(any(n["kind"] == "unknown-base" for n in nodes), "baza nieznana")
        bases = {n["ref"] for n in nodes if n["kind"] == "base"}
        self.assertIn("refs/heads/master", bases)
        self.assertIn("refs/heads/develop", bases)
        self.assertIn("refs/remotes/origin/main", bases, "scalenie do celu zdalnego")
        self.assertEqual({e["kind"] for e in self.reply["edges"]}, {"start", "merge"})
        self.assertTrue(any(n["started"]["ref"].startswith("refs/heads/card/") for n in cards),
                        "karta wystartowana z gałęzi innej karty")

    def test_cards_have_titles_columns_and_five_tickets_at_most(self):
        cards = [n for n in self.reply["nodes"] if n["kind"] == "card"]
        self.assertTrue(all(n.get("title") and n.get("column") for n in cards))
        per_card = {}
        for node in self.reply["nodes"]:
            if node["kind"] == "ticket":
                per_card[node["card"]] = per_card.get(node["card"], 0) + 1
        self.assertLessEqual(max(per_card.values()), 5)

    def test_saved_reply_is_the_file_from_the_acceptance_data(self):
        saved = tree.saved_reply()
        self.assertTrue(saved["ok"])
        self.assertGreaterEqual(tree.branch_count(saved), 40)
        self.assertEqual(tree.dataset_replies()["branchmap"], saved)


class ExpectedChecksTest(unittest.TestCase):
    REPLY = {"nodes": [{"kind": "base"}, {"kind": "card"}, {"kind": "ticket"}, {"kind": "ticket"}], "total": 9,
             "truncated": True}

    def observed(self, **change):
        data = {"before": {"nodes": 0, "loading": True, "hasReply": False},
                "after": {"nodes": 4, "kinds": {"base": 1, "card": 1, "ticket": 2}, "shown": ["pokazano 3 z 9"]},
                "kinds": {"base": 1, "card": 1, "ticket": 2}, "delivered": True}
        data.update(change)
        return data

    def test_all_good(self):
        self.assertTrue(tree.expected_checks(self.REPLY, self.observed())["passed"])

    def test_each_failure_is_reported(self):
        failing = {
            "callReturnedBeforeTree": self.observed(before={"nodes": 3, "loading": False, "hasReply": True}),
            "allNodes": self.observed(after={"nodes": 3, "kinds": {}, "shown": ["pokazano 3 z 9"]}),
            "cardNodes": self.observed(kinds={"base": 1, "card": 0, "ticket": 2}),
            "ticketNodes": self.observed(kinds={"base": 1, "card": 1, "ticket": 1}),
            "shownText": self.observed(after={"nodes": 4, "kinds": {}, "shown": []}),
            "delivered": self.observed(delivered=False)}
        for name, observed in failing.items():
            with self.subTest(name):
                checks = tree.expected_checks(self.REPLY, observed)
                self.assertFalse(checks[name])
                self.assertFalse(checks["passed"])

    def test_untruncated_map_has_no_shown_text(self):
        reply = dict(self.REPLY, truncated=False)
        ok = self.observed(after={"nodes": 4, "kinds": {}, "shown": []})
        self.assertTrue(tree.expected_checks(reply, ok)["shownText"])
        self.assertFalse(tree.expected_checks(reply, self.observed())["shownText"])


class CliTest(unittest.TestCase):
    def test_fewer_than_five_repeats_are_refused_without_a_window(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(tree.main(["--repeats", "4"]), 1)
        self.assertIn("5", err.getvalue())


if __name__ == "__main__":
    unittest.main()
