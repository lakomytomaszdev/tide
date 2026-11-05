"""PT-201: sonda obu wejść do wspólnego podglądu i odmowy z czytelnymi dowodami."""
import json

from .test_card_full_view import STATE_PY
from .decisionfix import full_d, journal, decisions, scenario_f
from .test_view import ProbeCase


PREVIEW = {"ok": True, "id": "K-8", "oldBase": {"baseRef": "refs/heads/master", "baseCommit": "a" * 40},
           "newBase": {"baseRef": "refs/heads/topic", "baseCommit": "b" * 40}, "baseVersion": 1,
           "previewId": "token", "decisionStep": "base-request:5", "evidence": "<b>zwykły tekst</b>",
           "canChange": False, "canExecute": False,
           "blockedReasons": [{"reason": "contribution", "message": "Commit dokumentów planu wyklucza korektę v1."}],
           "branches": [{"ref": "refs/heads/card/k-8", "worktree": "/wt/card", "startRef": "refs/heads/master",
                         "startCommit": "a" * 40, "commit": "c" * 40, "commits": [{"sha": "c" * 40}],
                         "ignored": ["cache.bin"]}], "processes": [], "invalidations": ["review i domknięcie"],
           "instruction": "Zachowaj bazę, aby kontynuować, albo pozostaw kartę w oczekiwaniu i zaplanuj przeniesienie "
                          "z zapisanego szkicu K-50-replay-code; bez ręcznego reset/rebase i bez usuwania katalogów.",
           "scope": "Dokumenty planu i K-49 po pracy nie mają automatycznej korekty v1.",
           "ignoredPolicy": "Ignorowane dane pozostają w starym worktree.",
           "followup": {"available": True, "path": "/scope/followups/replay-code.md",
                        "message": "Zapisany szkic K-50-replay-code, bez opublikowanej karty."},
           "executionMessage": "Potwierdzenie wykonania niedostępne."}


class BaseChangeViewTest(ProbeCase):
    def test_confirmation_sends_exact_preview_and_error_keeps_evidence(self):
        preview = {**PREVIEW, "canChange": True, "canExecute": True, "blockedReasons": []}
        out = self.probe(STATE_PY + "\npreview = " + repr(preview) + r'''
click(card("K-8"))
engine.rootContext().setContextProperty("probeTarget", obj("baseChangePreview"))
js("probeTarget.show('K-8', " + json.dumps(preview) + ")")
out["enabled"] = obj("baseChangeConfirm").property("enabled")
replies.append({"ok": False, "error": "stale-preview", "message": "Podgląd jest nieaktualny."})
click(named("baseChangeConfirm"))
until(lambda: not obj("baseChangePreview").property("busy"))
out["calls"] = calls()
out["visible"] = obj("baseChangePreview").property("visible")
out["text"] = obj("baseChangeText").property("text")
''', full_d(self.data))
        self.assertTrue(out["enabled"])
        self.assertTrue(out["visible"])
        self.assertIn("Podgląd jest nieaktualny", out["text"])
        self.assertIn("cache.bin", out["text"])
        self.assertIn("K-50-replay-code", out["text"])
        self.assertEqual([c[0] for c in out["calls"]], ["ResolveCardStep"])
        self.assertEqual(json.loads(out["calls"][0][2]), {
            "choice": "change-base", "step": "base-request:5", "baseRef": "refs/heads/topic",
            "baseCommit": "b" * 40, "previewId": "token"})

    def test_user_field_selects_exact_ref_then_cancel_does_not_send_keep_or_execution(self):
        data = full_d(self.data)
        branch = {"ref": "refs/heads/topic", "name": "topic", "commit": "b" * 40, "selectable": True}
        listing = {"ok": True, "mainRef": "refs/heads/topic", "branches": [branch], "recentBases": []}
        out = self.probe(STATE_PY + "\nlisting = " + repr(listing) + "\npreview = " + repr(PREVIEW) + r'''
click(card("K-8"))
backend.branch_replies.extend([listing] * 8)
click(named("fullBaseChange"))
until(lambda: obj("baseChangeSelect").property("enabled"))
out["selectionLabel"] = obj("baseChangeSelect").property("text")
before = js("JSON.stringify(page.snapshot.cards)")
replies.clear()
replies.append({"ok": True, "waiting": True, "changed": False, "preview": preview})
click(named("baseChangeSelect"))
until(lambda: not obj("baseChangePreview").property("busy"))
out["text"] = obj("baseChangeText").property("text")
engine.rootContext().setContextProperty("probeTarget", obj("baseChangeText"))
out["plain"] = js("probeTarget.textFormat === 0")
out["confirm"] = obj("baseChangeConfirm").property("enabled")
out["calls"] = calls()
click(named("baseChangeCancel"))
out["after"] = calls()
out["same"] = before == js("JSON.stringify(page.snapshot.cards)")
''', data)
        writes = [c for c in out["calls"] if c[0] == "RequestCardBaseChange"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(json.loads(writes[0][2]), {"baseRef": "refs/heads/topic", "baseCommit": "b" * 40})
        self.assertEqual(out["after"], out["calls"])
        self.assertTrue(out["same"])
        self.assertFalse(out["confirm"])
        self.assertTrue(out["plain"])
        self.assertIn("karta czeka na decyzję", out["selectionLabel"])
        for text in ("c" * 40, "cache.bin", "Commit dokumentów", "K-49", "K-50-replay-code", "Zachowaj bazę",
                     "<b>zwykły tekst</b>", "bez opublikowanej karty"):
            self.assertIn(text, out["text"])

    def test_ignored_paths_have_display_limit_and_do_not_hide_refusal_instructions(self):
        preview = json.loads(json.dumps(PREVIEW))
        preview["branches"][0].update(ignored=[f"cache-{i:02}/" for i in range(40)], ignoredCount=4000)
        out = self.probe(STATE_PY + "\npreview = " + repr(preview) + r'''
click(card("K-8"))
engine.rootContext().setContextProperty("probeTarget", obj("baseChangePreview"))
js("probeTarget.show('K-8', " + json.dumps(preview) + ")")
out["text"] = obj("baseChangeText").property("text")
''', full_d(self.data))
        self.assertEqual(out["text"].count("Ignorowane (pozostają tutaj):"), 10)
        self.assertIn("3990 pozostałych", out["text"])
        self.assertNotIn("cache-10/", out["text"])
        self.assertIn("Odmowa korekty", out["text"])
        self.assertIn("Zachowaj bazę", out["text"])
        self.assertIn("K-50-replay-code", out["text"])

    def test_agent_change_opens_same_preview_and_escape_preserves_question(self):
        events = journal()[:-1]
        events[0].update(baseRef="refs/heads/master", baseBranch="master", baseCommit="a" * 40)
        events[-1]["baseProposal"] = {"valid": True, "ref": "refs/heads/topic", "baseCommit": "b" * 40,
                                      "evidence": "sprawdzona historia"}
        out = self.probe(STATE_PY + "\npreview = " + repr(PREVIEW) + r'''
click(card("K-8"))
before = js("JSON.stringify(page.snapshot.cards)")
replies.append({"ok": True, "waiting": True, "changed": False, "preview": preview})
js("page.sendDecision('K-8', page.decisionDialog.current.id, 'ResolveCardStep', "
   "{choice: 'answer', step: 'K-8-ticket-3-0', baseDecision: 'change'})")
until(lambda: obj("baseChangePreview").property("visible"))
out["text"] = obj("baseChangeText").property("text")
key(Qt.Key.Key_Escape)
out["same"] = before == js("JSON.stringify(page.snapshot.cards)")
out["calls"] = calls()
''', scenario_f(self.data, decisions(events)))
        self.assertTrue(out["same"])
        self.assertIn("Odmowa korekty", out["text"])
        self.assertEqual([c[0] for c in out["calls"]], ["ResolveCardStep"])
