"""PT-200: istniejący widok, menu celu, klawiatura i granica zgody."""
import json

from .test_card_full_view import STATE_PY
from .decisionfix import full_d
from .test_view import FIND_PY, ProbeCase


class MergeTargetViewTest(ProbeCase):
    def test_base_activation_changes_full_default_label_without_returning_to_initial_base(self):
        data = self.closed({"status": "known", "baseRef": "refs/heads/old-base"})
        name = "feature/nowa-baza/" + "pelna-nazwa-" * 12
        out = self.probe(STATE_PY + "\nnew_name = " + repr(name) + r'''
click(card("K-8"))
out["before"] = obj("fullMergeMain").property("text")
snap = json.loads(backend.snapshotJson)
flow = next(c for c in snap["cards"] if c["id"] == "K-8")["derived"]["flow"]
flow.update(initialBase=flow["base"], base={"status": "known", "baseRef": "refs/heads/" + new_name}, baseVersion=2)
set_snapshot(snap)
assert until(lambda: obj("fullMergeMain").property("text") == "Scal do " + new_name)
out["after"] = obj("fullMergeMain").property("text")
replies.append({"ok": True, "targetRef": "refs/heads/" + new_name, "targetBranch": new_name})
click(named("fullMergeMain"))
out["calls"] = calls()
''', data)
        self.assertEqual(out["before"], "Scal do old-base")
        self.assertEqual(out["after"], "Scal do " + name)
        self.assertEqual(json.loads(out["calls"][0][2]), {"targetRef": "refs/heads/" + name})

    def closed(self, base=None):
        data = full_d(self.data)
        card = next(c for c in data["cards"] if c["id"] == "K-8")
        card["derived"]["flow"]["base"] = base or {"status": "unknown", "baseRef": ""}
        card["derived"]["flow"]["mainBranch"] = "master"
        for phase in card["derived"]["phases"]:
            if phase["id"] == "main-merge":
                phase["state"] = "you"
        return data

    def test_recent_shortcuts_order_dedup_blocked_and_explicit_preview_keep_default(self):
        name = "feature/<b>" + "long-target/" * 5 + "end"
        out = self.probe(STATE_PY + FIND_PY + "\nlong_name = " + repr(name) + r'''
click(card("K-8"))
out["default"] = obj("fullMergeMain").property("text")
def branch(name, selectable=True):
    return {"ref": "refs/heads/" + name, "name": name, "commit": "a" * 40, "kind": "local",
            "selectable": selectable, "reason": "Cel należy do innej karty" if not selectable else ""}
base, main, blocked, recent, manual = [branch("feature/base"), branch("master"), branch("card/other", False),
                                       branch(long_name), branch("manual")]
backend.branch_replies.append({"ok": True, "branches": [base, main, blocked, recent, manual],
                              "mainRef": main["ref"], "recentTargets": [main, base, blocked, recent],
                              "warnings": [], "hasMore": False})
click(named("fullMergeChoose"))
assert until(lambda: not obj("mergeBranchPicker").property("loading"))
engine.rootContext().setContextProperty("targetPicker", obj("mergeBranchPicker"))
out["shortcuts"] = js("targetPicker.mergeShortcuts.map(b => b.ref)")
out["searchResults"] = js("targetPicker.searchBranches.map(b => b.ref)")
out["unselected"] = obj("mergeTargetPreview").property("enabled")
out["positions"] = [js(FIND + "(targetPicker, 'mergeTargetShortcut0').mapToItem(targetPicker, 0, 0).y"),
                    js(FIND + "(targetPicker, 'mergeTargetSearch').mapToItem(targetPicker, 0, 0).y")]
click(at(FIND + "(targetPicker, 'mergeTargetShortcut2')"))
out["blocked"] = [obj("mergeTargetPreview").property("enabled"), calls()]
out["rendered"] = js("(function () { const row = " + FIND + "(targetPicker, 'mergeTargetShortcut3'); "
                     "return [row.children[0].text, row.children[0].textFormat, row.children[0].truncated] })()")
replies.append({"ok": True, "canMerge": True, "targetRef": recent["ref"],
                "targetBranch": long_name, "targetCommit": "a" * 40, "previewToken": "recent-token"})
click(at(FIND + "(targetPicker, 'mergeTargetShortcut3')"))
assert until(lambda: not obj("mergePreview").property("busy"))
out["menuClosed"] = not obj("mergeTargetMenu").property("visible")
out["confirmation"] = obj("mergePreviewConfirm").property("text")
out["calls"] = calls()
click(named("mergePreviewClose"))
out["defaultAfter"] = obj("fullMergeMain").property("text")
''', self.closed({"status": "known", "baseRef": "refs/heads/feature/base"}))
        self.assertEqual(out["shortcuts"], ["refs/heads/feature/base", "refs/heads/master",
                                            "refs/heads/card/other", "refs/heads/" + name])
        self.assertEqual(out["searchResults"], ["refs/heads/manual"])
        self.assertLess(*out["positions"])
        self.assertFalse(out["unselected"])
        self.assertEqual(out["blocked"], [False, []])
        self.assertEqual(out["rendered"], ["Ostatni cel · refs/heads/" + name, 0, False])
        self.assertTrue(out["menuClosed"])
        self.assertEqual(out["confirmation"], "Scal do " + name)
        self.assertEqual(len(out["calls"]), 1)
        self.assertEqual(out["calls"][0][:2], ["PreviewCardMerge", "K-8"])
        self.assertEqual(json.loads(out["calls"][0][2]), {"targetRef": "refs/heads/" + name})
        self.assertEqual(out["defaultAfter"], "Scal do feature/base")
        self.assertEqual(out["default"], out["defaultAfter"])

    def test_local_base_default_menu_search_keyboard_long_name_and_explicit_preview(self):
        name = "feature/<b>" + "very-long-name/" * 14 + "target"
        data = self.closed({"status": "known", "baseRef": "refs/heads/feature/base"})
        out = self.probe(STATE_PY + FIND_PY + "\nlong_name = " + repr(name) + r'''
click(card("K-8"))
out["default"] = obj("fullMergeMain").property("text")
def branch(name, selectable=True):
    return {"ref": "refs/heads/" + name, "name": name, "commit": "a" * 40, "kind": "local",
            "selectable": selectable, "reason": "Cel należy do czynnego kroku" if not selectable else ""}
listing = {"ok": True, "branches": [branch("feature/base"), branch("master"), branch("card/other", False)],
           "mainRef": "refs/heads/master", "recentTargets": [], "hasMore": True, "warnings": []}
backend.branch_replies.append(listing)
click(named("fullMergeChoose"))
assert until(lambda: not obj("mergeBranchPicker").property("loading"))
out["menu"] = obj("mergeTargetMenu").property("visible")
out["unselected"] = obj("mergeTargetPreview").property("enabled")
engine.rootContext().setContextProperty("targetPicker", obj("mergeBranchPicker"))
click(at(FIND + "(targetPicker, 'mergeTargetResult0')"))
out["excluded"] = obj("mergeTargetPreview").property("enabled")
backend.branch_replies.extend([{**listing, "branches": [branch("feature/first"), branch(long_name)], "hasMore": False}] * len("feature"))
click(named("mergeTargetSearch"))
type_text("feature")
assert until(lambda: not obj("mergeBranchPicker").property("loading"))
key(Qt.Key.Key_Down)
key(Qt.Key.Key_Return)
out["selected"] = obj("mergeTargetPreview").property("enabled")
out["rendered"] = json.loads(js("(function () { const row = " + FIND + "(targetPicker, 'mergeTargetResult1'); "
                               "return JSON.stringify([row.children[0].text, row.children[0].textFormat, row.children[0].truncated]) })()"))
out["beforePreview"] = calls()
replies.append({"ok": True, "canMerge": True, "targetRef": "refs/heads/" + long_name,
                "targetBranch": long_name, "targetCommit": "a" * 40, "previewToken": "selected-token"})
click(named("mergeTargetPreview"))
assert until(lambda: not obj("mergePreview").property("busy"))
out["confirmation"] = obj("mergePreviewConfirm").property("text")
click(named("mergePreviewClose"))
out["calls"] = calls()
out["branchCalls"] = backend.branch_calls
out["defaultAfter"] = obj("fullMergeMain").property("text")
''', data)
        self.assertEqual(out["default"], "Scal do feature/base")
        self.assertTrue(out["menu"])
        self.assertFalse(out["unselected"])
        self.assertFalse(out["excluded"])
        self.assertTrue(out["selected"])
        self.assertEqual(out["rendered"], [name, 0, False])
        self.assertEqual(out["beforePreview"], [])
        self.assertEqual(out["confirmation"], "Scal do " + name)
        self.assertEqual(len(out["calls"]), 1)
        self.assertEqual(out["calls"][0][:2], ["PreviewCardMerge", "K-8"])
        self.assertEqual(json.loads(out["calls"][0][2]), {"targetRef": "refs/heads/" + name})
        self.assertEqual(json.loads(out["branchCalls"][-1][2]), {"purpose": "merge", "text": "feature", "limit": 50})
        self.assertEqual(out["defaultAfter"], out["default"])

    def test_unknown_remote_require_choice_error_retry_cancel_and_project_change_revoke_pending_reply(self):
        for base in ({"status": "unknown", "baseRef": ""},
                     {"status": "known", "baseRef": "refs/remotes/origin/feature/base"}):
            with self.subTest(base=base):
                out = self.probe(STATE_PY + r'''
click(card("K-8"))
out["default"] = obj("fullMergeMain").property("text")
backend.branch_replies.append({"ok": False, "message": "Błąd odczytu <b>gita"})
click(named("fullMergeMain"))
assert until(lambda: not obj("mergeBranchPicker").property("loading"))
out["message"] = obj("mergeBranchPicker").property("message")
out["errorEnabled"] = obj("mergeTargetPreview").property("enabled")
click(named("mergeTargetRefresh"))
assert until(lambda: not obj("mergeBranchPicker").property("loading"))
out["noImplicitMain"] = obj("mergeTargetPreview").property("enabled")
key(Qt.Key.Key_Escape)
out["cancelled"] = not obj("mergeTargetMenu").property("visible")
backend.branch_delay = 500
click(named("fullMergeMain"))
assert obj("mergeBranchPicker").property("loading")
snap = json.loads(backend.snapshotJson)
next(c for c in snap["cards"] if c["id"] == "K-8")["project"] = "another-project"
set_snapshot(snap)
wait(600)
out["closedAfterProject"] = not obj("mergeTargetMenu").property("visible")
out["pendingTag"] = obj("mergeBranchPicker").property("pendingTag")
out["calls"] = calls()
''', self.closed(base))
                self.assertEqual(out["default"], "Scal…")
                self.assertEqual(out["message"], "Błąd odczytu <b>gita")
                self.assertFalse(out["errorEnabled"])
                self.assertFalse(out["noImplicitMain"])
                self.assertTrue(out["cancelled"])
                self.assertTrue(out["closedAfterProject"])
                self.assertEqual(out["pendingTag"], "")
                self.assertEqual(out["calls"], [])

    def test_remote_creation_proposal_custom_name_preview_and_cancel_do_not_confirm(self):
        out = self.probe(STATE_PY + FIND_PY + r'''
click(card("K-8"))
proposal = {"ref": "refs/heads/feature/full/base", "name": "feature/full/base", "kind": "create",
            "label": "Utwórz lokalną feature/full/base od origin/feature/full/base", "commit": "b" * 40,
            "selectable": True, "reason": ""}
main = {"ref": "refs/heads/master", "name": "master", "kind": "local", "commit": "a" * 40,
        "selectable": True, "reason": ""}
recent = {**main, "ref": "refs/heads/recent", "name": "recent"}
backend.branch_replies.append({"ok": True, "branches": [main, recent, proposal], "createTarget": proposal,
                              "recentTargets": [recent], "defaultTargetRef": "",
                              "mainRef": main["ref"], "warnings": []})
click(named("fullMergeMain"))
assert until(lambda: not obj("mergeBranchPicker").property("loading"))
engine.rootContext().setContextProperty("targetPicker", obj("mergeBranchPicker"))
out["label"] = js(FIND + "(targetPicker, 'mergeTargetResult0').children[0].text")
click(at(FIND + "(targetPicker, 'mergeTargetResult0')"))
out["selectedProposal"] = js("targetPicker.selected.ref")
out["before"] = calls()
click(named("mergeNewTargetName"))
type_text("alternative/full/target")
out["typed"] = obj("mergeNewTargetName").property("text")
out["pickerMessage"] = obj("mergeBranchPicker").property("message")
out["pickerLoading"] = obj("mergeBranchPicker").property("loading")
out["ready"] = obj("mergeTargetPreview").property("enabled")
replies.append({"ok": True, "canMerge": True, "targetRef": "refs/heads/alternative/full/target",
    "targetBranch": "alternative/full/target", "targetCommit": "b" * 40, "previewToken": "create-token",
    "targetWorktree": "/tmp/project-worktrees/merge-target-safe", "targetWorktreeExists": False,
    "targetPreparation": {"createRef": True, "createWorktree": True, "startCommit": "b" * 40,
                          "remoteRef": "refs/remotes/origin/feature/full/base", "remoteCommit": "b" * 40,
                          "path": "/tmp/project-worktrees/merge-target-safe"}})
click(named("mergeTargetPreview"))
assert until(lambda: not obj("mergePreview").property("busy"))
out["text"] = obj("mergePreviewText").property("text")
out["confirm"] = obj("mergePreviewConfirm").property("enabled")
key(Qt.Key.Key_Escape)
out["calls"] = calls()
''', self.closed({"status": "known", "baseRef": "refs/remotes/origin/feature/full/base"}))
        self.assertIn("Utwórz lokalną feature/full/base od origin/feature/full/base", out["label"])
        self.assertEqual(out["selectedProposal"], "refs/heads/feature/full/base")
        self.assertEqual(out["before"], [])
        self.assertTrue(out["ready"], out)
        self.assertTrue(out["confirm"])
        self.assertIn("Commit startu celu: " + "b" * 40, out["text"])
        self.assertIn("refs/remotes/origin/feature/full/base", out["text"])
        self.assertIn("Po zgodzie: utwórz własny worktree", out["text"])
        self.assertEqual(len(out["calls"]), 1)
        self.assertEqual(out["calls"][0][0], "PreviewCardMerge")
        self.assertEqual(json.loads(out["calls"][0][2]), {"targetRef": "refs/heads/alternative/full/target"})

    def test_exact_unique_tracker_is_default_but_multiple_trackers_require_choice(self):
        for unique in (True, False):
            with self.subTest(unique=unique):
                body = STATE_PY + "\nunique = " + repr(unique) + r'''
click(card("K-8"))
branches = [{"ref": "refs/heads/local/one", "name": "local/one", "kind": "local", "commit": "b" * 40,
             "selectable": True, "reason": ""},
            {"ref": "refs/heads/local/two", "name": "local/two", "kind": "local", "commit": "b" * 40,
             "selectable": True, "reason": ""}]
backend.branch_replies.append({"ok": True, "branches": branches, "mainRef": "refs/heads/master",
                              "defaultTargetRef": branches[0]["ref"] if unique else "", "warnings": []})
click(named("fullMergeMain"))
assert until(lambda: not obj("mergeBranchPicker").property("loading"))
out["ready"] = obj("mergeTargetPreview").property("enabled")
key(Qt.Key.Key_Escape)
out["calls"] = calls()
'''
                out = self.probe(body, self.closed({"status": "known", "baseRef": "refs/remotes/origin/feature/base"}))
                self.assertEqual(out["ready"], unique)
                self.assertEqual(out["calls"], [])
