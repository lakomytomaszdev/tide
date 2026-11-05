"""Granice nazw i dowodów bez dostępu do powłoki, sieci i gita."""
import json
import unittest
from unittest.mock import patch

from tide_daemon.cardflow.basesuggestions import suggest_text


class TextSuggestionsTest(unittest.TestCase):
    def test_long_repeated_line_has_bounded_evidence_and_correct_excerpt_offsets(self):
        text = "x " * 400 + "master " * 28000
        result = suggest_text(self.branches("master"), text, text)
        evidence = result["candidates"][0]["evidence"]
        self.assertLessEqual(len(evidence), 5)
        for item in evidence:
            self.assertLessEqual(len(item["text"]), 512)
            self.assertEqual(item["text"][item["start"]:item["end"]], item["match"])
            self.assertEqual(item["match"], "master")
        self.assertLess(len(json.dumps(result)), 10000)

    def test_many_candidates_and_unknown_names_have_bounded_response(self):
        names = [f"feature/base-{i}" for i in range(100)]
        text = " ".join(names) + "\n" + "\n".join(f"git switch missing-{i}" for i in range(100))
        result = suggest_text(self.branches(*names), "", text)
        self.assertLessEqual(len(result["candidates"]), 32)
        self.assertLessEqual(len(result["warnings"]), 22)
        self.assertTrue(any("Ograniczono" in warning for warning in result["warnings"]))
        self.assertLess(len(json.dumps(result)), 50000)

    def test_command_context_scans_only_fixed_window(self):
        import tide_daemon.cardflow.basesuggestions as suggestions
        search = suggestions.re.search
        lengths = []

        def measured_search(pattern, value):
            lengths.append(len(value))
            return search(pattern, value)

        with patch.object(suggestions.re, "search", side_effect=measured_search):
            result = suggest_text([], "", "ordinary " * 2000 + "git switch missing")
        self.assertTrue(lengths)
        self.assertLessEqual(max(lengths), 80)
        self.assertTrue(any("missing" in warning for warning in result["warnings"]))

    def test_oversized_name_keeps_full_candidate_but_bounded_visible_evidence(self):
        name = "feature/" + "a" * 1000
        result = suggest_text(self.branches(name), "", "git switch " + name)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["name"], name)
        evidence = candidate["evidence"][0]
        self.assertLessEqual(len(evidence["text"]), 512)
        self.assertEqual(evidence["text"][evidence["start"]:evidence["end"]], evidence["match"])
        result = suggest_text([], "", "git switch " + name)
        self.assertLess(len(result["warnings"][0]), 320)

    def branches(self, *names):
        return [{"ref": "refs/heads/" + name, "name": name, "commit": "a" * 40,
                 "selectable": True, "reason": ""} for name in names]

    def test_full_names_punctuation_unicode_and_no_substrings(self):
        branches = self.branches("feature/base", "feature/base-more", "żółć/nowa", "topic(foo)", "topic,bar")
        for name in (b["name"] for b in branches):
            for text in (name, "`" + name + "`", "git switch " + name, "[" + name + "]", name + ".",
                         name + "?", name + "!", "**" + name + "**", "<" + name + ">", name + "—sprawdź"):
                result = suggest_text(branches, "", text)
                self.assertEqual([c["name"] for c in result["candidates"]], [name], text)
        for text in ("xfeature/base", "feature/base/child", "feature/base~1", "feature/base^", "feature/base@{1}", "feature/base.extra",
                     "FEATURE/base", "refs/remotes/feature/base"):
            self.assertEqual(suggest_text(branches, "", text)["candidates"], [], text)

    def test_evidence_conflicts_and_unknown_revision_stay_data(self):
        branches = self.branches("master", "team/topic")
        branches.append(dict(branches[1], ref="refs/remotes/team/topic", commit="b" * 40))
        result = suggest_text(branches, "Baza master", "`team/topic` <b>tekst</b>\ngit switch refs/heads/missing~1")
        self.assertEqual(len(result["candidates"]), 3)
        self.assertEqual(result["candidates"][1]["evidence"], result["candidates"][2]["evidence"])
        self.assertTrue(any("missing~1" in warning for warning in result["warnings"]))
        for candidate in result["candidates"]:
            self.assertEqual(candidate["source"], "text")
            for evidence in candidate["evidence"]:
                self.assertEqual(evidence["text"][evidence["start"]:evidence["end"]], evidence["match"])

    def test_short_name_includes_local_and_each_remote_without_resolving_conflict(self):
        local = self.branches("feat/message-expiration-date")[0]
        remotes = [dict(local, ref=f"refs/remotes/{remote}/" + local["name"],
                        name=remote + "/" + local["name"], kind="remote", remote=remote,
                        commit=sha * 40) for remote, sha in (("origin", "b"), ("upstream", "c"))]
        for branches in ([local, *remotes], remotes):
            with self.subTest(refs=[b["ref"] for b in branches]):
                result = suggest_text(branches, "Komentarze dotyczą `feat/message-expiration-date`", "")
                self.assertEqual([c["ref"] for c in result["candidates"]], [b["ref"] for b in branches])
                self.assertEqual([c["commit"] for c in result["candidates"]], [b["commit"] for b in branches])
                self.assertTrue(all(c["evidence"] == result["candidates"][0]["evidence"]
                                    for c in result["candidates"]))
                self.assertFalse(any("Nieznana" in w for w in result["warnings"]))
        for name in (remotes[0]["name"], remotes[0]["ref"]):
            self.assertEqual([c["ref"] for c in suggest_text([local, *remotes], "", name)["candidates"]],
                             [remotes[0]["ref"]])

    def test_prose_identifiers_and_creation_options_are_not_missing_branches(self):
        for text in ("Gałąź bazowa", "Scal do gałęzi głównej", "zapis w `card.started`",
                     "plik `LaunchDialog.qml`", "baza danych", "git checkout -b nowa",
                     "git switch -c nowa", "`zwykłe-słowo`", "switch widoku", "checkout koszyka"):
            with self.subTest(text=text):
                self.assertEqual(suggest_text([], text, text), {"candidates": [], "warnings": []})

    def test_missing_names_require_explicit_branch_context(self):
        for text in ("`feature/missing`", "refs/heads/missing", "git switch missing",
                     "git checkout missing", "branch missing", "git switch `missing`"):
            with self.subTest(text=text):
                result = suggest_text([], "", text)
                self.assertEqual(result["candidates"], [])
                self.assertEqual(len(result["warnings"]), 1)
                self.assertIn("missing", result["warnings"][0])
                self.assertIn("Polecenie", result["warnings"][0])
