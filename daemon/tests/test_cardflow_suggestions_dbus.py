"""PT-196: odczytowe podpowiedzi przez publiczny obiekt /Board."""
import json
import os

from .test_cardflow_dbus import FlowCase, git


class SuggestBaseDbusTest(FlowCase):
    def suggest(self, card, text, revision=7, scope="text"):
        return self.call("SuggestCardBase", card, json.dumps({"text": text, "revision": revision, "scope": scope}))

    def test_evidence_boundaries_conflicts_unknown_and_no_mutation(self):
        for name in ("feature/base", "team/topic", "release/next", "żółć/nowa"):
            git(self.repo, "branch", name, env=self.git_env)
        git(self.repo, "update-ref", "refs/remotes/origin/team/topic", self.master, env=self.git_env)
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/team/topic", env=self.git_env)
        card = self.card(description="Baza `feature/base`, także team/topic.\nPrzykład: git checkout release/next")
        before = {"refs": git(self.repo, "show-ref"), "status": git(self.repo, "status", "--porcelain"),
                  "head": git(self.repo, "symbolic-ref", "HEAD"), "worktrees": git(self.repo, "worktree", "list"),
                  "board": git(self.board, "rev-parse", "HEAD"), "shown": self.shown(card)}
        text = "git switch żółć/nowa\nUżyj `feature/base` <b>dowód</b>; baza refs/heads/brak~1"
        result = self.suggest(card, text)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["revision"], result["card"], result["repo"]), (7, card, self.repo))
        candidates = {c["ref"]: c for c in result["candidates"]}
        self.assertEqual(set(candidates), {"refs/heads/feature/base", "refs/heads/team/topic",
                                          "refs/remotes/origin/team/topic", "refs/heads/release/next", "refs/heads/żółć/nowa"})
        feature = candidates["refs/heads/feature/base"]
        self.assertEqual({e["field"] for e in feature["evidence"]}, {"description", "text"})
        self.assertIn("<b>dowód</b>", feature["evidence"][1]["text"])
        for candidate in candidates.values():
            self.assertEqual((candidate["commit"], candidate["source"], candidate["strength"],
                              candidate["selectable"], candidate["reason"]), (self.master, "text", "explicit", True, ""))
            for evidence in candidate["evidence"]:
                self.assertEqual(evidence["text"][evidence["start"]:evidence["end"]], evidence["match"])
        self.assertEqual(candidates["refs/heads/team/topic"]["evidence"], candidates["refs/remotes/origin/team/topic"]["evidence"])
        self.assertEqual(candidates["refs/remotes/origin/team/topic"]["kind"], "remote")
        self.assertTrue(any("kilka baz" in w for w in result["warnings"]))
        self.assertTrue(any("refs/heads/brak~1" in w for w in result["warnings"]))
        self.assertEqual(before, {"refs": git(self.repo, "show-ref"), "status": git(self.repo, "status", "--porcelain"),
                                  "head": git(self.repo, "symbolic-ref", "HEAD"), "worktrees": git(self.repo, "worktree", "list"),
                                  "board": git(self.board, "rev-parse", "HEAD"), "shown": self.shown(card)})
        self.assertFalse(os.path.exists(os.path.join(self.board, "flow", card)))

    def test_remote_only_short_name_and_missing_name_without_prose_noise(self):
        ref = "refs/remotes/origin/feat/message-expiration-date"
        git(self.repo, "update-ref", ref, self.master, env=self.git_env)
        card = self.card(description="Komentarze dotyczyły gałęzi `feat/message-expiration-date`")
        before = {"refs": git(self.repo, "show-ref"), "card": self.shown(card),
                  "board": git(self.board, "rev-parse", "HEAD")}
        result = self.suggest(card, "Gałąź bazowa. Scal do gałęzi głównej. Baza danych.\n"
                                    "Zapis w `card.started`, plik `LaunchDialog.qml`.\n"
                                    "git checkout -b nowa\ngit switch -c nowa\n`feat/missing`")
        self.assertTrue(result["ok"], result)
        self.assertEqual([c["ref"] for c in result["candidates"]], [ref])
        candidate = result["candidates"][0]
        self.assertEqual((candidate["kind"], candidate["name"]),
                         ("remote", "origin/feat/message-expiration-date"))
        self.assertEqual(candidate["evidence"][0]["match"], "feat/message-expiration-date")
        self.assertEqual(candidate["evidence"][0]["field"], "description")
        self.assertEqual(result["warnings"], ["Nieznana nazwa — Polecenie, wiersz 5: feat/missing"])
        self.assertEqual(before, {"refs": git(self.repo, "show-ref"), "card": self.shown(card),
                                  "board": git(self.board, "rev-parse", "HEAD")})
        self.assertFalse(os.path.exists(os.path.join(self.board, "flow", card)))

    def test_whole_names_validation_and_reserved_sources(self):
        git(self.repo, "branch", "feature/base", env=self.git_env)
        card = self.card(description="Bez wskazań")
        result = self.suggest(card, "xfeature/base feature/base-extra feature/base/file feature/base~1 feature/base@{1} FEATURE/base")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["candidates"], [])
        for text in ("`feature/base`", "git switch feature/base", "refs/heads/feature/base", "Baza feature/base."):
            self.assertEqual(len(self.suggest(card, text)["candidates"]), 1, text)
        for scope in ("files", "links"):
            result = self.suggest(card, "feature/base", scope=scope)
            self.assertEqual(result["candidates"], [])
            self.assertTrue(result["warnings"])
        for request in ({"text": "master", "revision": True, "scope": "text"},
                        {"text": "master", "revision": -1, "scope": "text"},
                        {"text": "master", "revision": 1, "scope": "shell"},
                        {"text": "master", "revision": 1, "scope": "text", "description": "master"}):
            result = self.call("SuggestCardBase", card, json.dumps(request))
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error"], "bad-request")
