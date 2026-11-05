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
