"""PT-196: jawne użycie oraz granice aktualności odpowiedzi w prawdziwym oknie Qt."""
import json

from . import test_view as view


class BaseSuggestionsTest(view.ProbeCase):
    def test_late_links_reply_preserves_manual_base_and_start_sha(self):
        candidate = {"ref": "refs/remotes/fork/source", "name": "fork/source", "commit": "b" * 40,
                     "source": "links", "strength": "explicit", "selectable": True, "evidence": []}
        out = self.probe(view.FIND_PY + f'''
js("page.openLaunch('K-15', false); page.launchDialog.entry = 'now'")
wait(400)
backend.suggestion_delay = 100
backend.suggestion_replies.append({dict(candidates=[candidate], warnings=[])!r})
js(FIND + "(page.launchDialog, 'checkBaseLinks').clicked()")
out["scope"] = json.loads(backend.suggestion_calls[-1][2])["scope"]
out["loading"] = js("page.launchDialog.baseSuggestions.loading")
js("page.launchDialog.basePicker.selected = ({{ref: 'refs/heads/manual', commit: '" + 'c' * 40 + "'}})")
wait(150)
out["base"] = js("[page.launchDialog.launchState.baseRef, page.launchDialog.launchState.baseCommit]")
out["candidates"] = js("page.launchDialog.baseSuggestions.candidates.length")
replies.append({{"ok": False, "message": "Koniec po żądaniu"}})
js("page.launchChoice = page.launchDialog.launchState; page.continueLaunch()")
wait(50)
out["request"] = json.loads(next(c[2] for c in calls() if c[0] == 'StartCard'))
''')
        self.assertEqual(out["scope"], "links")
        self.assertTrue(out["loading"])
        self.assertEqual(out["base"], ["refs/heads/manual", "c" * 40])
        self.assertEqual(out["candidates"], 0)
        self.assertEqual((out["request"]["baseRef"], out["request"]["baseCommit"]), tuple(out["base"]))

    def test_files_probe_shows_touch_evidence_and_requires_explicit_use(self):
        candidate = {"ref": "refs/heads/feature/files", "name": "feature/files", "commit": "b" * 40,
                     "source": "files", "strength": "heuristic", "selectable": True, "reason": "",
                     "rank": 1, "matchedPaths": 1,
                     "evidence": [{"path": "src/only-feature.qml", "commit": "c" * 40,
                                   "time": "2026-01-01T12:00:00+00:00"}]}
        out = self.probe(view.FIND_PY + f'''
js("page.openLaunch('K-15', false); page.launchDialog.entry = 'now'")
wait(400)
backend.suggestion_replies.append({dict(candidates=[candidate], warnings=["Wynik częściowy; wybierz jawnie."])!r})
js(FIND + "(page.launchDialog, 'checkBaseFiles').clicked()")
out["canStartDuringAnalysis"] = js("page.launchDialog.canStart")
wait(100)
out["scope"] = json.loads(backend.suggestion_calls[-1][2])["scope"]
out["before"] = js("page.launchDialog.basePicker.selected.ref")
out["evidence"] = js(FIND + "(page.launchDialog, 'baseEvidence0').text")
out["message"] = js("page.launchDialog.baseSuggestions.message")
js(FIND + "(page.launchDialog, 'useBaseSuggestion0').clicked()")
replies.append({{"ok": False, "message": "Koniec sondy"}})
js("page.launchChoice = page.launchDialog.launchState; page.continueLaunch()")
wait(50)
out["request"] = json.loads(next(c[2] for c in calls() if c[0] == 'StartCard'))
''')
        self.assertEqual(out["scope"], "files")
        self.assertEqual(out["before"], "refs/heads/master")
        self.assertTrue(out["canStartDuringAnalysis"])
        for value in ("heuristic", "src/only-feature.qml", "c" * 40, "2026-01-01", "Pozycja 1"):
            self.assertIn(value, out["evidence"])
        self.assertIn("częściowy", out["message"])
        self.assertEqual((out["request"]["baseRef"], out["request"]["baseCommit"]),
                         (candidate["ref"], candidate["commit"]))

    def test_links_are_on_demand_stale_local_choice_and_late_manual_selection(self):
        candidate = {"ref": "refs/remotes/fork/feature/source", "name": "fork/feature/source", "commit": "b" * 40,
                     "source": "links", "strength": "explicit", "selectable": True, "reason": "Nieaktualny ref",
                     "stale": True, "localCommit": "b" * 40, "sourceCommit": "c" * 40, "evidence": [],
                     "host": "git.example.org", "project": "team/proj", "sourceProject": "user/proj",
                     "sourceBranch": "feature/source", "targetBranch": "main",
                     "link": "https://git.example.org/team/proj/-/merge_requests/17"}
        out = self.probe(view.FIND_PY + f'''
js("page.openLaunch('K-15', false); page.launchDialog.entry = 'now'")
wait(400)
out["autoScopes"] = [json.loads(c[2])["scope"] for c in backend.suggestion_calls]
backend.suggestion_replies.append({dict(candidates=[candidate], warnings=[])!r})
js(FIND + "(page.launchDialog, 'checkBaseLinks').clicked()")
wait(100)
out["scope"] = json.loads(backend.suggestion_calls[-1][2])["scope"]
out["before"] = js("page.launchDialog.basePicker.selected.ref")
out["label"] = js(FIND + "(page.launchDialog, 'useBaseSuggestion0').children[0].text")
out["evidence"] = js(FIND + "(page.launchDialog, 'baseEvidence0').text")
js(FIND + "(page.launchDialog, 'useBaseSuggestion0').clicked()")
out["picked"] = js("[page.launchDialog.launchState.baseRef, page.launchDialog.launchState.baseCommit]")
replies.append({{"ok": False, "message": "Koniec po żądaniu"}})
js("page.launchChoice = page.launchDialog.launchState; page.continueLaunch()")
wait(50)
out["request"] = json.loads(next(c[2] for c in calls() if c[0] == 'StartCard'))
''')
        self.assertEqual(set(out["autoScopes"]), {"text"})
        self.assertEqual(out["scope"], "links")
        self.assertEqual(out["before"], "refs/heads/master")
        self.assertEqual(out["label"], "Użyj stanu lokalnego " + candidate["ref"])
        self.assertIn("SHA API: " + candidate["sourceCommit"], out["evidence"])
        self.assertIn("SHA lokalny: " + candidate["localCommit"], out["evidence"])
        self.assertEqual(out["picked"], [candidate["ref"], candidate["commit"]])
        self.assertEqual((out["request"]["baseRef"], out["request"]["baseCommit"]), tuple(out["picked"]))

    def test_explicit_use_full_name_plain_evidence_and_start(self):
        name = "feature/" + "bardzo-dluga-nazwa-" * 18 + "<tag>"
        candidate = {"ref": "refs/heads/" + name, "name": name, "commit": "b" * 40,
                     "source": "text", "strength": "explicit", "selectable": True, "reason": "",
                     "evidence": [{"field": "text", "line": 1, "text": "<b>dowód</b> git switch " + name}]}
        out = self.probe(view.FIND_PY + f'''
js("page.openLaunch('K-15', false); page.launchDialog.entry = 'now'")
wait(100)
backend.suggestion_replies.append({dict(candidates=[candidate], warnings=["Sprawdź wybór"])!r})
js("page.launchDialog.taskEdit.text = 'Nowe polecenie'")
wait(400)
out["before"] = js("page.launchDialog.basePicker.selected.ref")
out["label"] = js(FIND + "(page.launchDialog, 'useBaseSuggestion0').children[0].text")
out["plain"] = js(FIND + "(page.launchDialog, 'baseEvidence0').textFormat")
out["wrap"] = js(FIND + "(page.launchDialog, 'useBaseSuggestion0').children[0].wrapMode")
out["evidence"] = js(FIND + "(page.launchDialog, 'baseEvidence0').text")
js(FIND + "(page.launchDialog, 'useBaseSuggestion0').clicked()")
out["picked"] = js("[page.launchDialog.launchState.baseRef, page.launchDialog.launchState.baseCommit]")
js("page.launchDialog.taskEdit.text = 'Kolejna zmiana'")
wait(400)
out["kept"] = js("page.launchDialog.basePicker.selected.ref")
replies.append({{"ok": False, "message": "Koniec testu po żądaniu"}})
js("page.launchChoice = page.launchDialog.launchState; page.continueLaunch()")
wait(50)
out["request"] = json.loads(next(c[2] for c in calls() if c[0] == 'StartCard'))
''')
        self.assertEqual(out["before"], "refs/heads/master")
        self.assertEqual(out["label"], "Użyj " + candidate["ref"])
        self.assertEqual((out["plain"], out["wrap"]), (0, 3))
        self.assertIn("<b>dowód</b>", out["evidence"])
        self.assertEqual(out["picked"], [candidate["ref"], candidate["commit"]])
        self.assertEqual(out["kept"], candidate["ref"])
        self.assertEqual((out["request"]["baseRef"], out["request"]["baseCommit"]), tuple(out["picked"]))

    def test_stale_revision_card_project_manual_choice_and_error(self):
        out = self.probe('''
js("page.openLaunch('K-15', false); page.launchDialog.entry = 'now'")
wait(400)
base = {"ok": True, "revision": js("page.launchDialog.baseSuggestions.revision"), "card": 'K-15',
        "project": js("page.launchDialog.workProject.slug"), "repo": js("page.launchDialog.workProject.repo"),
        "candidates": [{"ref": "refs/heads/stale", "commit": "b" * 40, "source": "text", "strength": "explicit", "evidence": [], "selectable": True}]}
out["rejected"] = []
for key, value in (("revision", -1), ("card", "K-10"), ("project", "other"), ("repo", "/other")):
    js("page.launchDialog.baseSuggestions.pendingTag = 'test'")
    wrong = dict(base, **{key: value})
    js("page.launchDialog.baseSuggestions.receive('test', " + json.dumps(wrong) + ")")
    out["rejected"].append(js("page.launchDialog.baseSuggestions.candidates.length"))
js("page.launchDialog.baseSuggestions.pendingTag = 'manual-late'")
js("page.launchDialog.basePicker.selected = ({ref: 'refs/heads/manual', commit: '" + 'c' * 40 + "'})")
js("page.launchDialog.baseSuggestions.receive('manual-late', " + json.dumps(base) + ")")
out["manual"] = js("[page.launchDialog.basePicker.selected.ref, page.launchDialog.baseSuggestions.candidates.length]")
js("page.launchDialog.baseSuggestions.pendingTag = 'text-late'; page.launchDialog.taskEdit.text = 'Inne polecenie'")
js("page.launchDialog.baseSuggestions.receive('text-late', " + json.dumps(base) + ")")
out["textLate"] = js("page.launchDialog.baseSuggestions.candidates.length")
js("page.launchDialog.baseSuggestions.pendingTag = 'project-late'")
changed = json.loads(js("JSON.stringify(page.snapshot)"))
next(c for c in changed["cards"] if c["id"] == 'K-15')["project"] = 'mobileapp'
backend.set_snapshot(changed)
js("page.launchDialog.baseSuggestions.receive('project-late', " + json.dumps(base) + ")")
out["projectLate"] = js("page.launchDialog.baseSuggestions.candidates.length")
wait(400)
js("page.launchDialog.baseSuggestions.pendingTag = 'failure'; page.launchDialog.baseSuggestions.receive('failure', {ok: false})")
out["analysisError"] = js("page.launchDialog.baseSuggestions.message")
out["canStart"] = js("page.launchDialog.canStart")
out["baseReady"] = js("page.launchDialog.basePicker.ready")
''')
        self.assertEqual(out["rejected"], [0, 0, 0, 0])
        self.assertEqual(out["manual"], ["refs/heads/manual", 0])
        self.assertEqual((out["textLate"], out["projectLate"]), (0, 0))
        self.assertIn("ręcznie", out["analysisError"])
        self.assertTrue(out["baseReady"])
        self.assertTrue(out["canStart"])
