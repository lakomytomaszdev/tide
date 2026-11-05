"""PT-209: formularz w Qt offscreen oraz kontrakt żądania w QJSEngine."""
import json

from . import test_view as view


class BranchPickerTest(view.ProbeCase):
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
js("page.launchDialog.entry = 'plan'")
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
