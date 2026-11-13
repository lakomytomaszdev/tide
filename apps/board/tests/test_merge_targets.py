"""PT-200, K-99 #6: wybór celu scalenia w oknie scalania osadzonym w Tablicy (przyciski „Scal…” i „Scal do <główna>”
bez domyślnego celu otwierają od razu okno z wyborem celu, nie stary MergeTargetPicker), skróty, wyszukiwanie,
klawiatura i granica zgody."""
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

    def test_choose_opens_window_with_default_target_shortcuts_recent_and_search(self):
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
                              "defaultTargetRef": base["ref"], "warnings": [], "hasMore": False})
replies.append({"ok": True, "canMerge": True, "targetRef": base["ref"], "targetBranch": "feature/base",
                "previewToken": "default-token"})
click(named("fullMergeChoose"))
assert until(lambda: obj("mergeWindow").property("opened") and not obj("mergeWindow").property("busy"))
out["oldPopup"] = obj("mergeTargetMenu") is None
out["window"] = [obj("mergeWindowTarget").property("text"), obj("mergeWindowMerge").property("enabled")]
out["defaultCalls"] = calls()
click(named("mergeWindowTargetToggle"))
engine.rootContext().setContextProperty("targetPicker", obj("mergeWindowBranchPicker"))
out["shortcuts"] = js("targetPicker.mergeShortcuts.map(b => b.ref)")
out["searchResults"] = js("targetPicker.searchBranches.map(b => b.ref)")
out["positions"] = [js(FIND + "(targetPicker, 'mergeTargetShortcut0').mapToItem(targetPicker, 0, 0).y"),
                    js(FIND + "(targetPicker, 'mergeTargetSearch').mapToItem(targetPicker, 0, 0).y")]
click(at(FIND + "(targetPicker, 'mergeTargetShortcut2')"))
out["blocked"] = [obj("mergeWindowTarget").property("text"), len(calls())]
out["rendered"] = js("(function () { const row = " + FIND + "(targetPicker, 'mergeTargetShortcut3'); "
                     "return [row.children[0].text, row.children[0].textFormat, row.children[0].truncated] })()")
replies.append({"ok": True, "canMerge": True, "targetRef": recent["ref"],
                "targetBranch": long_name, "targetCommit": "a" * 40, "previewToken": "recent-token"})
click(at(FIND + "(targetPicker, 'mergeTargetShortcut3')"))
assert until(lambda: not obj("mergeWindow").property("busy"))
out["confirmation"] = obj("mergeWindowMerge").property("text")
out["windowTarget"] = [obj("mergeWindowTarget").property("text"), obj("mergeWindow").property("targetRef")]
backend.branch_replies.extend([{"ok": True, "branches": [branch("feature/first"), branch(long_name)],
                               "mainRef": main["ref"], "recentTargets": [], "hasMore": False,
                               "warnings": []}] * len("feature"))
click(named("mergeTargetSearch"))
type_text("feature")
assert until(lambda: not obj("mergeWindowBranchPicker").property("loading"))
key(Qt.Key.Key_Down)
key(Qt.Key.Key_Return)
assert until(lambda: not obj("mergeWindow").property("busy"))
out["afterSearch"] = obj("mergeWindow").property("targetRef")
out["calls"] = calls()
out["branchCalls"] = backend.branch_calls
click(named("mergeWindowClose"))
out["defaultAfter"] = obj("fullMergeMain").property("text")
''', self.closed({"status": "known", "baseRef": "refs/heads/feature/base"}))
        self.assertEqual(out["default"], "Scal do feature/base")
        self.assertTrue(out["oldPopup"], "stary MergeTargetPicker nie jest już otwierany")
        self.assertEqual(out["window"], ["K-8 → feature/base", True])
        self.assertEqual([json.loads(c[2]) for c in out["defaultCalls"]], [{"targetRef": "refs/heads/feature/base"}])
        self.assertEqual(out["shortcuts"], ["refs/heads/feature/base", "refs/heads/master",
                                            "refs/heads/card/other", "refs/heads/" + name])
        self.assertEqual(out["searchResults"], ["refs/heads/manual"])
        self.assertLess(*out["positions"])
        self.assertEqual(out["blocked"], ["K-8 → feature/base", 1], "cel niewybieralny nie wysyła podglądu")
        self.assertEqual(out["rendered"], ["Ostatni cel · refs/heads/" + name, 0, False])
        self.assertEqual(out["windowTarget"], ["K-8 → " + name, "refs/heads/" + name])
        self.assertEqual(out["confirmation"], "Scal do " + name)
        self.assertEqual(out["afterSearch"], "refs/heads/" + name)
        self.assertEqual([json.loads(c[2])["targetRef"] for c in out["calls"]],
                         ["refs/heads/feature/base", "refs/heads/" + name, "refs/heads/" + name])
        self.assertEqual(json.loads(out["branchCalls"][-1][2]), {"purpose": "merge", "text": "feature", "limit": 50})
        self.assertEqual(out["defaultAfter"], out["default"])

    def test_unknown_or_remote_base_error_retry_cancel_and_project_change_revoke_pending_reply(self):
        for base in ({"status": "unknown", "baseRef": ""},
                     {"status": "known", "baseRef": "refs/remotes/team/feature/base"}):
            with self.subTest(base=base):
                out = self.probe(STATE_PY + r'''
click(card("K-8"))
out["default"] = obj("fullMergeMain").property("text")
backend.branch_replies.append({"ok": False, "message": "Błąd odczytu <b>gita"})
click(named("fullMergeMain"))
assert until(lambda: obj("mergeWindow").property("opened")
             and not obj("mergeWindowBranchPicker").property("loading"))
out["message"] = obj("mergeWindowBranchPicker").property("message")
out["errorEnabled"] = obj("mergeWindowMerge").property("enabled")
out["errorCalls"] = len(calls())
click(named("mergeTargetRefresh"))
assert until(lambda: not obj("mergeWindowBranchPicker").property("loading"))
wait(100)
out["afterRetry"] = [obj("mergeWindowMerge").property("enabled"), obj("mergeWindow").property("proposal"),
                     obj("mergeWindow").property("targetRef"), len(calls())]
key(Qt.Key.Key_Escape)
out["cancelled"] = not obj("mergeWindow").property("opened")
backend.branch_delay = 500
click(named("fullMergeMain"))
assert obj("mergeWindowBranchPicker").property("loading")
snap = json.loads(backend.snapshotJson)
next(c for c in snap["cards"] if c["id"] == "K-8")["project"] = "another-project"
set_snapshot(snap)
wait(600)
out["closedAfterProject"] = not obj("mergeWindow").property("opened")
out["pendingTag"] = obj("mergeWindowBranchPicker").property("pendingTag")
out["calls"] = len(calls())
''', self.closed(base))
                unknown = base["status"] == "unknown"
                self.assertEqual(out["default"], "Scal…")
                self.assertEqual(out["message"], "Błąd odczytu <b>gita")
                self.assertFalse(out["errorEnabled"])
                self.assertEqual(out["errorCalls"], 0)
                # Po ponowieniu: baza nieznana — główna jest tylko propozycją (podgląd tak, „Scal do” nie);
                # baza zdalna bez jednoznacznego śledzenia — bez celu i bez podglądu.
                self.assertEqual(out["afterRetry"], [False, unknown, "refs/heads/master" if unknown else "",
                                                     1 if unknown else 0])
                self.assertTrue(out["cancelled"])
                self.assertTrue(out["closedAfterProject"])
                self.assertEqual(out["pendingTag"], "")
                self.assertEqual(out["calls"], 1 if unknown else 0)

    def test_remote_creation_proposal_custom_name_preview_and_cancel_do_not_confirm(self):
        out = self.probe(STATE_PY + FIND_PY + r'''
click(card("K-8"))
proposal = {"ref": "refs/heads/feature/full/base", "name": "feature/full/base", "kind": "create",
            "label": "Utwórz lokalną feature/full/base od team/feature/full/base", "commit": "b" * 40,
            "selectable": True, "reason": ""}
main = {"ref": "refs/heads/master", "name": "master", "kind": "local", "commit": "a" * 40,
        "selectable": True, "reason": ""}
recent = {**main, "ref": "refs/heads/recent", "name": "recent"}
backend.branch_replies.append({"ok": True, "branches": [main, recent, proposal], "createTarget": proposal,
                              "recentTargets": [recent], "defaultTargetRef": "",
                              "mainRef": main["ref"], "warnings": []})
click(named("fullMergeMain"))
assert until(lambda: obj("mergeWindow").property("opened")
             and not obj("mergeWindowBranchPicker").property("loading"))
engine.rootContext().setContextProperty("targetPicker", obj("mergeWindowBranchPicker"))
out["noTarget"] = [obj("mergeWindow").property("targetRef"), len(calls())]
out["label"] = js(FIND + "(targetPicker, 'mergeTargetResult0').children[0].text")
click(at(FIND + "(targetPicker, 'mergeTargetResult0')"))
out["selectedProposal"] = [js("targetPicker.selected.ref"), obj("mergeWindow").property("targetRef")]
out["before"] = [json.loads(c[2]) for c in calls()]
js("(function () { const f = " + FIND + "(page, 'mergeWindowPickerScroll').contentItem; "
   "f.contentY = Math.max(0, f.contentHeight - f.height) })()")
wait(60)
click(named("mergeNewTargetName"))
type_text("alternative/full/target")
out["typed"] = obj("mergeNewTargetName").property("text")
out["afterTyping"] = len(calls())
out["ready"] = obj("mergeWindowNewTargetPreview").property("enabled")
replies.append({"ok": True, "canMerge": True, "targetRef": "refs/heads/alternative/full/target",
    "targetBranch": "alternative/full/target", "targetCommit": "b" * 40, "previewToken": "create-token",
    "targetWorktree": "/tmp/project-worktrees/merge-target-safe", "targetWorktreeExists": False,
    "targetPreparation": {"createRef": True, "createWorktree": True, "startCommit": "b" * 40,
                          "remoteRef": "refs/remotes/team/feature/full/base", "remoteCommit": "b" * 40,
                          "path": "/tmp/project-worktrees/merge-target-safe"}})
click(named("mergeWindowNewTargetPreview"))
assert until(lambda: not obj("mergeWindow").property("busy"))
click(named("mergeWindowDetailsToggle"))
out["text"] = obj("mergeWindowDetails").property("text")
out["confirm"] = obj("mergeWindowMerge").property("enabled")
key(Qt.Key.Key_Escape)
out["closed"] = not obj("mergeWindow").property("opened")
out["calls"] = calls()
''', self.closed({"status": "known", "baseRef": "refs/remotes/team/feature/full/base"}))
        self.assertTrue(out["closed"], "Escape zamyka okno scalania")
        self.assertEqual(out["noTarget"], ["", 0])
        self.assertIn("Utwórz lokalną feature/full/base od team/feature/full/base", out["label"])
        self.assertEqual(out["selectedProposal"], ["refs/heads/feature/full/base", "refs/heads/feature/full/base"])
        self.assertEqual(out["before"], [{"targetRef": "refs/heads/feature/full/base"}])
        self.assertEqual(out["typed"], "alternative/full/target")
        self.assertEqual(out["afterTyping"], 1, "wpisywanie nazwy nie wysyła podglądu")
        self.assertTrue(out["ready"], out)
        self.assertTrue(out["confirm"])
        self.assertIn("Commit startu celu: " + "b" * 40, out["text"])
        self.assertIn("refs/remotes/team/feature/full/base", out["text"])
        self.assertIn("Po zgodzie: utwórz własny worktree", out["text"])
        self.assertEqual([c[0] for c in out["calls"]], ["PreviewCardMerge"] * 2)
        self.assertEqual(json.loads(out["calls"][1][2]), {"targetRef": "refs/heads/alternative/full/target"})

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
replies.append({"ok": True, "canMerge": True, "targetRef": branches[0]["ref"], "targetBranch": "local/one",
                "previewToken": "tracker-token"})
click(named("fullMergeMain"))
assert until(lambda: obj("mergeWindow").property("opened")
             and not obj("mergeWindowBranchPicker").property("loading"))
wait(150)
out["ready"] = obj("mergeWindowMerge").property("enabled")
out["target"] = obj("mergeWindow").property("targetRef")
key(Qt.Key.Key_Escape)
out["calls"] = calls()
'''
                out = self.probe(body, self.closed({"status": "known", "baseRef": "refs/remotes/team/feature/base"}))
                self.assertEqual(out["ready"], unique)
                self.assertEqual(out["target"], "refs/heads/local/one" if unique else "")
                self.assertEqual([json.loads(c[2]) for c in out["calls"]],
                                 [{"targetRef": "refs/heads/local/one"}] if unique else [])

    def test_panel_merge_choose_opens_window_with_target_choice(self):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "review", "flow": {
                    "closed": True, "summary": "Gotowe", "mainBranch": "develop", "cardBranch": "card/k-6-eksport",
                    "base": {"status": "known", "baseRef": "refs/heads/develop"},
                    "mainMerged": False, "plan": {"status": "done", "rows": [], "waves": [], "revision": {}}}}
                card["boardColumn"] = "review"
        out = self.probe(FIND_PY + r'''
click(card("K-6"))
backend.branch_replies.append({"ok": True, "defaultTargetRef": "refs/heads/develop", "mainRef": "refs/heads/develop",
                              "branches": [{"ref": "refs/heads/develop", "name": "develop", "kind": "local",
                                            "commit": "a" * 40, "selectable": True, "reason": ""}],
                              "recentTargets": [], "warnings": []})
click(at(FIND + "(page.cardPanel, 'cardMergeChoose')"))
assert until(lambda: obj("mergeWindow").property("opened") and not obj("mergeWindow").property("busy"))
out["window"] = [obj("mergeWindow").property("opened"), obj("mergeWindowTarget").property("text"),
                 obj("mergeTargetMenu") is None]
out["calls"] = [[c[0], c[1], json.loads(c[2])] for c in calls()]
''', data=data)
        self.assertEqual(out["window"], [True, "K-6 → develop", True])
        self.assertEqual(out["calls"], [["PreviewCardMerge", "K-6", {"targetRef": "refs/heads/develop"}]])
