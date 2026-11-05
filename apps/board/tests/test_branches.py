"""PT-209: formularz w Qt offscreen oraz kontrakt żądania w QJSEngine."""
import json

from . import test_view as view


class BranchPickerTest(view.ProbeCase):
    def test_remote_and_recent_are_explicit_choices_and_never_default(self):
        local = {"ref": "refs/heads/master", "name": "master", "commit": "a" * 40,
                 "kind": "local", "selectable": True}
        remote = {"ref": "refs/remotes/team/feature", "name": "team/feature", "commit": "b" * 40,
                  "kind": "remote", "remote": "team", "selectable": True}
        duplicate = dict(remote, ref="refs/heads/team/feature", kind="local", commit="c" * 40)
        reply = {"ok": True, "mainRef": local["ref"], "branches": [local, duplicate, remote],
                 "recentBases": [remote], "warnings": [], "hasMore": False}
        out = self.probe(view.FIND_PY + f'''
js("page.openLaunch('K-15', false)")
wait(100)
backend.branch_replies.append({reply!r})
js("page.launchDialog.entry = 'now'; page.launchDialog.basePicker.reset()")
wait(100)
out["default"] = js("page.launchDialog.basePicker.selected.ref")
out["recentCount"] = js("page.launchDialog.basePicker.recentBases.length")
out["remoteLabel"] = js(FIND + "(page.launchDialog, 'baseResult2').children[0].text")
js(FIND + "(page.launchDialog, 'recentBase0').clicked()")
out["picked"] = js("[page.launchDialog.basePicker.selected.ref, page.launchDialog.basePicker.selected.commit]")
replies.append({{"ok": False, "message": "Test odbioru"}})
js("page.launchChoice = page.launchDialog.launchState; page.continueLaunch()")
wait(50)
out["request"] = json.loads(next(c[2] for c in reversed(calls()) if c[0] == 'StartCard'))
''')
        self.assertEqual(out["default"], local["ref"])
        self.assertEqual(out["recentCount"], 1)
        self.assertEqual(out["remoteLabel"], remote["ref"])
        self.assertEqual(out["picked"], [remote["ref"], remote["commit"]])
        self.assertEqual(out["request"]["baseRef"], remote["ref"])
        self.assertEqual(out["request"]["baseCommit"], remote["commit"])

    def test_all_planning_entries_send_choice_and_reopen_with_persisted_base(self):
        out = self.probe('''
out["requests"] = []
for entry, mode in (("planning", "light"), ("planning", "full"), ("plan", "full")):
    js("page.openLaunch('K-15', false)")
    js("page.launchDialog.entry = " + json.dumps(entry) + "; page.launchDialog.mode = " + json.dumps(mode))
    wait(80)
    js("page.launchDialog.planPath = '/repo/plan.md'; page.launchDialog.cardBranch = 'card/chosen'")
    js("page.launchDialog.basePicker.selected = ({ref: 'refs/heads/feature/plan', name: 'feature/plan', commit: '" + 'b' * 40 + "'})")
    replies.append({"ok": False, "message": "Test odbioru"})
    js("page.launchChoice = page.launchDialog.launchState; page.continueLaunch()")
    wait(60)
    out["requests"].append(json.loads(next(c[2] for c in reversed(calls()) if c[0] == 'StartCard')))
changed = json.loads(js("JSON.stringify(page.snapshot)"))
c = next(c for c in changed["cards"] if c["id"] == 'K-15')
c.update(baseRef='refs/heads/feature/deleted', baseBranch='feature/deleted', baseCommit='c' * 40, baseVersion=1)
c['derived'] = {"flow": {"cardBranch": "card/persisted"}}
backend.set_snapshot(changed)
js("page.openLaunch('K-15', false)")
wait(80)
out["restored"] = js("[page.launchDialog.basePicker.selected.ref, page.launchDialog.basePicker.selected.commit, page.launchDialog.cardBranch]")
before = len(backend.branch_calls)
js("page.launchDialog.basePicker.refresh()")
wait(40)
out["noNewLookup"] = before == len(backend.branch_calls)
''')
        self.assertEqual([r["entry"] for r in out["requests"]], ["planning", "planning", "import"])
        for request in out["requests"]:
            self.assertEqual(request["baseRef"], "refs/heads/feature/plan")
            self.assertEqual(request["baseCommit"], "b" * 40)
            self.assertEqual(request["cardBranch"], "card/chosen")
        self.assertEqual(out["restored"], ["refs/heads/feature/deleted", "c" * 40, "card/persisted"])
        self.assertTrue(out["noNewLookup"])

    def test_search_keyboard_full_names_project_reset_and_service_error(self):
        long_name = "feature/" + "bardzo-dluga-nazwa-" * 18 + "<tag>"
        branches = [{"ref": "refs/heads/master", "name": "master", "commit": "a" * 40, "selectable": True},
                    {"ref": "refs/heads/" + long_name, "name": long_name, "commit": "b" * 40, "selectable": True}]
        reply = {"ok": True, "mainRef": "refs/heads/master", "branches": branches, "warnings": [], "hasMore": True}
        out = self.probe(view.FIND_PY + f'''
js("page.openLaunch('K-15', false)")
wait(100)
backend.branch_replies.append({reply!r})
js("page.launchDialog.entry = 'now'")
js("page.launchDialog.basePicker.refresh()")
wait(100)
out["initial"] = js("[page.launchDialog.basePicker.mainRef, page.launchDialog.basePicker.selected.ref]")
engine.rootContext().setContextProperty("searchField", obj("baseSearch"))
js("searchField.focusInput()")
key(Qt.Key.Key_Down)
key(Qt.Key.Key_Return)
wait(30)
out["selected"] = js("page.launchDialog.launchState.baseRef")
out["name"] = js("page.launchDialog.basePicker.selected.name")
out["plain"] = js(FIND + "(page.launchDialog, 'baseResult1').children[0].textFormat")
out["wrap"] = js(FIND + "(page.launchDialog, 'baseResult1').children[0].wrapMode")
out["hasMore"] = js("page.launchDialog.basePicker.children.some(c => c.text && c.text.indexOf('Zawęź') >= 0)")
backend.branch_replies.append({dict(reply, branches=[branches[1]], hasMore=False)!r})
js("searchField.text = 'nazwa'; searchField.edited()")
wait(60)
out["searchRequest"] = json.loads(backend.branch_calls[-1][2])
old_tag = js("page.launchDialog.basePicker.pendingTag")
changed = json.loads(js("JSON.stringify(page.snapshot)"))
next(c for c in changed["cards"] if c["id"] == 'K-15')["project"] = 'mobileapp'
backend.set_snapshot(changed)
out["reset"] = js("[page.launchDialog.basePicker.branches.length, page.launchDialog.basePicker.selected]")
js("page.launchDialog.basePicker.receive(" + json.dumps(old_tag) + ", " + {json.dumps(reply)!r} + ")")
out["late"] = js("page.launchDialog.basePicker.branches.length")
out["project"] = js("page.launchDialog.workProject.slug")
wait(60)
backend.branch_replies.append({{"ok": False, "message": "Usługa nie odpowiada"}})
js("page.launchDialog.basePicker.refresh()")
wait(60)
out["failure"] = js("[page.launchDialog.basePicker.message, page.launchDialog.basePicker.ready, page.launchDialog.canStart]")
js("page.launchDialog.entry = 'spec'")
out["inactive"] = js("[page.launchDialog.basePicker.active, page.launchDialog.basePicker.selected]")
''')
        self.assertEqual(out["initial"], ["refs/heads/master", "refs/heads/master"])
        self.assertEqual(out["selected"], "refs/heads/" + long_name)
        self.assertEqual(out["name"], long_name)
        self.assertEqual(out["plain"], 0)  # Text.PlainText
        self.assertEqual(out["wrap"], 3)  # Text.WrapAnywhere
        self.assertTrue(out["hasMore"])
        self.assertEqual(out["searchRequest"], {"purpose": "base", "text": "nazwa", "limit": 50})
        self.assertEqual(out["reset"], [0, None])
        self.assertEqual(out["late"], 0)
        self.assertEqual(out["project"], "mobileapp")
        self.assertEqual(out["failure"], ["Usługa nie odpowiada", False, False])
        self.assertEqual(out["inactive"], [False, None])

    def test_form_sends_exact_ref_commit_and_separate_card_branch(self):
        out = self.probe('''
js("page.openLaunch('K-15', false)")
wait(100)
js("page.launchDialog.entry = 'now'")
wait(100)
js("page.launchDialog.cardBranch = 'card/manual-choice'")
replies.append({"ok": False, "message": "Test kończy się po odebraniu żądania"})
js("page.launchChoice = page.launchDialog.launchState; page.continueLaunch()")
wait(50)
out["request"] = json.loads(next(c[2] for c in calls() if c[0] == 'StartCard'))
''')
        self.assertEqual(out["request"]["baseRef"], "refs/heads/master")
        self.assertEqual(out["request"]["baseCommit"], "a" * 40)
        self.assertEqual(out["request"]["cardBranch"], "card/manual-choice")
