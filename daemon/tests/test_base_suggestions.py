"""Granice nazw i dowodów bez dostępu do powłoki, sieci i gita."""
import unittest

from tide_daemon.cardflow.basesuggestions import suggest_text


class TextSuggestionsTest(unittest.TestCase):
    def branches(self, *names):
        return [{"ref": "refs/heads/" + name, "name": name, "commit": "a" * 40,
                 "selectable": True, "reason": ""} for name in names]

    def test_full_names_punctuation_unicode_and_no_substrings(self):
        branches = self.branches("feature/base", "feature/base-more", "żółć/nowa", "topic(foo)", "topic,bar")
        for name in (b["name"] for b in branches):
            for text in (name, "`" + name + "`", "git switch " + name, "[" + name + "]", name + "."):
                result = suggest_text(branches, "", text)
                self.assertEqual([c["name"] for c in result["candidates"]], [name], text)
        for text in ("xfeature/base", "feature/base/child", "feature/base~1", "feature/base@{1}", "feature/base.extra",
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
