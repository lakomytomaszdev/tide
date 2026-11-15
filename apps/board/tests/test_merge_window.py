"""Okno scalania (K-99, PT-390): wspólny komponent apps/common/qml/git/MergeWindow.qml i jego osadzenie w Tablicy.

- kontrakt zgody na samym komponencie (sonda diffprobe.py na atrapie gospodarza): „Scal do <cel>” tylko przy
  `canMerge === true` i niepustym `previewToken`, `mergeRequested` z dokładnie `{targetRef, previewToken}` bieżącego
  podglądu; zamknięcie, Escape i „Odśwież” nie wysyłają zgody; odpowiedź starego żądania (inny tag) nie nadpisuje
  bieżącego podglądu; błąd scalenia blokuje przycisk do nowego udanego podglądu; sukces zamyka okno z komunikatem;
  minimalny podgląd (stan zdaniem, konflikty, powody blokad, ostrzeżenia, „Szczegóły”) i teksty z gita jako zwykły tekst,
- komponent bez zależności od Tablicy i bez wywołań usługi (źródło), kolory tylko z palety,
- Tablica: zmiana kontekstu karty zamyka okno i odrzuca spóźnioną odpowiedź, „Zmiany gałęzi” otwiera widok diffa
  karty (K-36) nad oknem, Esc wraca do okna; w oknach nie ma drogi scalenia bez tokenu ani starych komponentów.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from . import REPO
from .test_card_full_view import STATE_PY, full_d
from .test_view import QML_PROBLEMS, ProbeCase, offscreen

sys.path.insert(0, os.path.join(REPO, "widgets"))
import stage as widget_stage  # noqa: E402

GIT_DIR = os.path.join(REPO, "apps", "common", "qml", "git")
WINDOW_QML = os.path.join(GIT_DIR, "MergeWindow.qml")
PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diffprobe.py")
PALETTE = os.path.join(REPO, "theme", "palette", "mist.json")

# Atrapa gospodarza: zapisuje sygnały okna w `fake.calls`; tagi nadaje jak Tablica (`track`).
HARNESS = '''
import QtQuick
import QtQuick.Window
import "tide" as Tide
import "git" as Git

Window {
    id: root
    width: 1000
    height: 700
    visible: true

    QtObject {
        id: fake
        property var calls: []
        property var branches: []          // żądania `ListCardBranches` (osobno, żeby nie mieszać się z `calls`)
        property int serial: 0
        function add(entry) { calls = calls.concat([entry]) }
        function tag(kind) { serial += 1; return kind + ":" + serial }
    }

    Loader {
        id: loader
        anchors.fill: parent
        sourceComponent: Component {
            Git.MergeWindow {
                onPreviewRequested: targetRef => {
                    const tag = fake.tag("preview")
                    track(tag)
                    fake.add(["preview", tag, targetRef])
                }
                onBranchesRequested: text => {
                    const tag = fake.tag("branches")
                    trackBranches(tag)
                    fake.branches = fake.branches.concat([[tag, text]])
                }
                onMergeRequested: request => {
                    const tag = fake.tag("merge")
                    track(tag)
                    fake.add(["merge", tag, JSON.stringify(request)])
                }
                onChangesRequested: cardId => fake.add(["changes", cardId])
                onClosed: fake.add(["closed"])
            }
        }
    }
}
'''

SCENARIO_HEAD = '''
def win(expr):
    return js("loader.item." + expr)

def item(name):
    """Tekst, widoczność i aktywność elementu po objectName (także niewidocznego)."""
    text = js("(function () { const all = " + ALL.replace("it.visible", "true") + "; "
              "const it = all(loader.item, " + json.dumps(name) + ")[0]; return it ? JSON.stringify("
              "{text: it.text, visible: it.visible, enabled: it.enabled, title: it.title, rows: it.rows}) : '' })()")
    return json.loads(text) if text else None

def open_window(card="K-12", ref="refs/heads/master", branch="master"):
    js("loader.item.open(" + json.dumps(card) + ", " + json.dumps(ref) + ", " + json.dumps(branch) + ")")
    wait(60)

def last(kind):
    return [c for c in calls() if c[0] == kind][-1]

def previews():
    return [c[2] for c in calls() if c[0] == "preview"]

def branch(name, selectable=True, reason=""):
    return {"ref": "refs/heads/" + name, "name": name, "commit": "a" * 40, "kind": "local",
            "selectable": selectable, "reason": reason}

def listing(branches, main="", default="", recent=()):
    """Odpowiedź `ListCardBranches` (purpose merge) bez gałęzi zdalnych; `main`/`default` to pełne refy albo pusty tekst."""
    reply = {"ok": True, "branches": branches, "recentTargets": list(recent), "defaultTargetRef": default,
             "trackingTargets": [], "createTarget": None, "warnings": [], "hasMore": False}
    if main:
        reply["mainRef"] = main
    return reply

def give(reply):
    """Odpowiedź na ostatnie żądanie listy gałęzi."""
    tag = json.loads(js("JSON.stringify(fake.branches)"))[-1][0]
    return js("loader.item.receiveBranches(" + json.dumps(tag) + ", " + json.dumps(reply) + ")")

def open_choosing(base=""):
    """Okno bez wskazanego celu; `base` to pełny ref znanej bazy karty albo pusty tekst (baza nieznana)."""
    js("loader.item.baseRef = " + json.dumps(base))
    js("loader.item.contextKey = 'K-12:p:/repo'")
    open_window(ref="", branch="")
    wait(60)

def non_plain_texts():
    """objectName-y elementów tekstowych całego okna (także wierszy list), które nie są zwykłym tekstem."""
    return json.loads(js("(function () { const out = []; (function walk(it) { if (!it) return; "
                         "if (typeof it.textFormat === 'number' && it.textFormat !== 0) out.push(it.objectName || '?'); "
                         "for (const c of it.children) walk(c); if (it.contentItem) walk(it.contentItem) })(loader.item); "
                         "return JSON.stringify(out) })()"))

def section_rows(name):
    """Wiersze, tytuł i widoczność sekcji podglądu po objectName (także niewidocznej)."""
    found = item(name)
    return found and {"visible": found["visible"], "title": found["title"], "rows": found["rows"]}

def visible_texts():
    return json.loads(js("(function () { const out = []; (function walk(it) { if (!it) return; "
                         "if (it.visible && typeof it.text === 'string') out.push(it.text); "
                         "for (const c of it.children) walk(c) })(loader.item); return JSON.stringify(out) })()"))

def scroll_picker():
    """Przewija strefę wyboru celu na dół (wyniki wyszukiwania pod skrótami)."""
    js("(function () { const s = " + ALL + "(loader.item, 'mergeWindowPickerScroll')[0]; "
       "if (s) s.contentItem.contentY = Math.max(0, s.contentItem.contentHeight - s.contentItem.height) })()")
    wait(60)

def shortcuts():
    texts = []
    for i in range(10):
        found = field("mergeTargetShortcut" + str(i), "it.children[0].text")
        if not found:
            break
        texts.append(found[0])
    return texts

def ok(ref, token=""):
    return {**PREVIEW, "targetRef": ref, "targetBranch": ref.split("/", 2)[2], "previewToken": token or "t:" + ref}

def answer(tag, reply, error=""):
    return js("loader.item.receive(" + json.dumps(tag) + ", " + json.dumps(error) + ", " + json.dumps(reply) + ")")
'''

PREVIEW = {"ok": True, "canMerge": True, "previewToken": "token-1", "targetRef": "refs/heads/master",
           "targetBranch": "master", "sourceRef": "refs/heads/card/k-12", "sourceCommit": "a" * 40,
           "targetCommit": "b" * 40, "resultTree": "c" * 40, "targetWorktree": "/repo", "targetWorktreeExists": True,
           "commits": [], "commitCount": 0, "files": [], "fileCount": 0, "conflicts": [], "conflictCount": 0}


class MergeWindowCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-merge-window-")
        palette = widget_stage.load_palette(PALETTE)
        widget_stage.copy_lib(os.path.join(cls.tmp, "tide"), palette)
        shutil.copytree(GIT_DIR, os.path.join(cls.tmp, "git"))
        with open(os.path.join(cls.tmp, "Harness.qml"), "w", encoding="utf-8") as fh:
            fh.write(HARNESS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def probe(self, scenario: str) -> dict:
        script = os.path.join(self.tmp, "scenario.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(SCENARIO_HEAD + "\nPREVIEW = json.loads(" + repr(json.dumps(PREVIEW)) + ")\n" + scenario)
        result = subprocess.run(["/usr/bin/python3", PROBE, self.tmp, script], env=offscreen(), capture_output=True,
                                text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", out, out)
        return out


class ConsentTest(MergeWindowCase):
    def test_merge_button_needs_can_merge_true_and_token_and_sends_exact_current_fields(self):
        variants = [{**PREVIEW, "canMerge": False},
                    {**PREVIEW, "previewToken": ""},
                    {k: v for k, v in PREVIEW.items() if k != "previewToken"},
                    {**PREVIEW, "canMerge": "true"},
                    {**PREVIEW, "canMerge": 1},
                    {**PREVIEW, "previewToken": 7}]
        out = self.probe("variants = " + repr(variants) + r'''
open_window()
out["opened"] = [win("opened"), calls()[0]]
out["busyEnabled"] = item("mergeWindowMerge")["enabled"]
out["enabled"] = []
for variant in variants:
    win("refresh()")
    answer(last("preview")[1], variant)
    out["enabled"].append(item("mergeWindowMerge")["enabled"])
    click("mergeWindowMerge")
out["noMerge"] = [c for c in calls() if c[0] == "merge"]
win("refresh()")
answer(last("preview")[1], {**PREVIEW, "previewToken": "token-current", "targetRef": "refs/heads/master",
                            "extra": "nie wysyłaj"})
out["ready"] = item("mergeWindowMerge")["enabled"]
click("mergeWindowMerge")
out["merge"] = last("merge")
out["busyAfter"] = [win("busy"), item("mergeWindowMerge")["enabled"], item("mergeWindowRefresh")["enabled"]]
# Drugi klik w trakcie scalenia nic nie wysyła.
click("mergeWindowMerge")
out["merges"] = len([c for c in calls() if c[0] == "merge"])
''')
        self.assertEqual(out["opened"], [True, ["preview", "preview:1", "refs/heads/master"]])
        self.assertFalse(out["busyEnabled"])
        self.assertEqual(out["enabled"], [False] * 6)
        self.assertEqual(out["noMerge"], [])
        self.assertTrue(out["ready"])
        self.assertEqual(json.loads(out["merge"][2]), {"targetRef": "refs/heads/master", "previewToken": "token-current"})
        self.assertEqual(out["busyAfter"], [True, False, False])
        self.assertEqual(out["merges"], 1)

    def test_close_escape_and_refresh_never_send_consent(self):
        out = self.probe(r'''
open_window()
answer(last("preview")[1], PREVIEW)
click("mergeWindowRefresh")
answer(last("preview")[1], PREVIEW)
click("mergeWindowClose")
out["afterClose"] = win("opened")
open_window()
answer(last("preview")[1], PREVIEW)
key(Qt.Key.Key_Escape)
out["afterEscape"] = win("opened")
out["calls"] = [c[0] for c in calls()]
''')
        self.assertFalse(out["afterClose"])
        self.assertFalse(out["afterEscape"])
        self.assertEqual(out["calls"], ["preview", "preview", "closed", "preview", "closed"])

    def test_stale_tag_reply_does_not_override_current_preview(self):
        out = self.probe(r'''
open_window()
first = last("preview")[1]
click("mergeWindowRefresh")
second = last("preview")[1]
out["tags"] = [first, second]
answer(first, {**PREVIEW, "previewToken": "old"})
out["afterOld"] = [win("result === null"), win("busy"), item("mergeWindowMerge")["enabled"]]
answer(second, {**PREVIEW, "previewToken": "new"})
out["afterNew"] = [win("result.previewToken"), win("busy")]
answer(first, {**PREVIEW, "previewToken": "old"})
answer("", {**PREVIEW, "previewToken": "empty"})
out["still"] = win("result.previewToken")
click("mergeWindowMerge")
out["merge"] = json.loads(last("merge")[2])
# Odpowiedź podglądu z tagiem scalenia nie istnieje: stary tag podglądu w trakcie scalenia też jest pomijany.
answer(second, {**PREVIEW, "previewToken": "late"})
out["pending"] = win("pending")
# Odpowiedź po zamknięciu okna nie wraca do okna.
merge_tag = last("merge")[1]
click("mergeWindowClose")
out["late"] = answer(merge_tag, {"ok": True, "targetBranch": "master"})
''')
        self.assertNotEqual(out["tags"][0], out["tags"][1])
        self.assertEqual(out["afterOld"], [True, True, False])
        self.assertEqual(out["afterNew"], ["new", False])
        self.assertEqual(out["still"], "new")
        self.assertEqual(out["merge"], {"targetRef": "refs/heads/master", "previewToken": "new"})
        self.assertEqual(out["pending"], "merge")
        self.assertEqual(out["late"], "")

    def test_merge_error_locks_button_until_new_successful_preview(self):
        out = self.probe(r'''
open_window()
answer(last("preview")[1], PREVIEW)
click("mergeWindowMerge")
answer(last("merge")[1], {"ok": False, "error": "stale-preview"}, "Podgląd jest nieaktualny [stale-preview]")
out["failed"] = [item("mergeWindowState")["text"], item("mergeWindowError")["text"],
                item("mergeWindowLockHint")["visible"], item("mergeWindowMerge")["enabled"], win("opened")]
click("mergeWindowMerge")
out["merges"] = len([c for c in calls() if c[0] == "merge"])
# Nieudany podgląd nie odblokowuje.
click("mergeWindowRefresh")
answer(last("preview")[1], {"ok": False}, "Nie można odczytać drzewa")
out["afterFailedPreview"] = [win("mergeLocked"), item("mergeWindowMerge")["enabled"]]
click("mergeWindowRefresh")
answer(last("preview")[1], {**PREVIEW, "previewToken": "fresh"})
out["afterPreview"] = [win("mergeLocked"), item("mergeWindowMerge")["enabled"]]
click("mergeWindowMerge")
out["message"] = answer(last("merge")[1], {"ok": True, "targetBranch": "master"})
out["closed"] = [win("opened"), calls()[-1]]
out["sent"] = [json.loads(c[2]) for c in calls() if c[0] == "merge"]
''')
        self.assertEqual(out["failed"], ["Scalenie nie powiodło się", "Podgląd jest nieaktualny [stale-preview]",
                                        True, False, True])
        self.assertEqual(out["merges"], 1)
        self.assertEqual(out["afterFailedPreview"], [True, False])
        self.assertEqual(out["afterPreview"], [False, True])
        self.assertEqual(out["message"], "Scalono K-12 do master")
        self.assertEqual(out["closed"], [False, ["closed"]])
        self.assertEqual(out["sent"], [{"targetRef": "refs/heads/master", "previewToken": "token-1"},
                                       {"targetRef": "refs/heads/master", "previewToken": "fresh"}])


class TargetChoiceTest(MergeWindowCase):
    """K-99 #6: strefa celu jako wybór (domyślny cel, skróty, wyszukiwanie) na atrapie gospodarza."""

    def test_default_target_gets_preview_and_shortcut_and_search_choices_send_new_preview(self):
        out = self.probe(r'''
open_choosing("refs/heads/feature/x")
out["asked"] = [len(json.loads(js("JSON.stringify(fake.branches)"))), previews(), item("mergeWindowTarget")["text"]]
give(listing([branch("feature/x"), branch("main"), branch("other")], main="refs/heads/main",
             default="refs/heads/feature/x"))
out["default"] = [previews(), item("mergeWindowTarget")["text"], item("mergeWindowMerge")["enabled"]]
first = last("preview")[1]
answer(first, ok("refs/heads/feature/x"))
out["ready"] = item("mergeWindowMerge")["enabled"]
click("mergeWindowTargetToggle")
out["shortcuts"] = shortcuts()
click("mergeTargetShortcut1")
out["afterShortcut"] = [previews(), item("mergeWindowMerge")["enabled"], win("pending"), win("proposal")]
answer(first, ok("refs/heads/feature/x"))   # spóźniona odpowiedź poprzedniego celu
out["staleIgnored"] = [item("mergeWindowMerge")["enabled"], win("result === null")]
answer(last("preview")[1], ok("refs/heads/main"))
out["readyMain"] = item("mergeWindowMerge")["enabled"]
click("mergeTargetResult0")
out["afterSearch"] = [previews(), item("mergeWindowMerge")["enabled"], item("mergeWindowTarget")["text"]]
answer(last("preview")[1], ok("refs/heads/other"))
click("mergeWindowMerge")
out["merge"] = json.loads(last("merge")[2])
out["calls"] = [c[0] for c in calls()]
''')
        self.assertEqual(out["asked"], [1, [], "K-12 → wybierz cel"])
        self.assertEqual(out["default"], [["refs/heads/feature/x"], "K-12 → feature/x", False])
        self.assertTrue(out["ready"])
        self.assertEqual(out["shortcuts"], ["Baza karty · refs/heads/feature/x", "Główna · refs/heads/main"])
        self.assertEqual(out["afterShortcut"], [["refs/heads/feature/x", "refs/heads/main"], False, "preview", False])
        self.assertEqual(out["staleIgnored"], [False, True])
        self.assertTrue(out["readyMain"])
        self.assertEqual(out["afterSearch"], [["refs/heads/feature/x", "refs/heads/main", "refs/heads/other"], False,
                                              "K-12 → other"])
        self.assertEqual(out["merge"], {"targetRef": "refs/heads/other", "previewToken": "t:refs/heads/other"})
        self.assertEqual(out["calls"].count("merge"), 1, "wybór celu sam niczego nie scala")

    def test_unknown_base_default_is_only_a_proposal_until_conscious_choice(self):
        out = self.probe(r'''
open_choosing("")
give(listing([branch("trunk"), branch("other")], main="refs/heads/trunk"))
answer(last("preview")[1], ok("refs/heads/trunk"))
out["proposed"] = [previews(), win("proposal"), item("mergeWindowProposal")["visible"],
                   item("mergeWindowMerge")["enabled"], item("mergeWindowState")["text"]]
click("mergeWindowMerge")
out["noMerge"] = [c for c in calls() if c[0] == "merge"]
out["shortcuts"] = shortcuts()
click("mergeTargetShortcut0")
out["accepted"] = [previews(), win("proposal"), item("mergeWindowMerge")["enabled"]]
click("mergeWindowMerge")
out["merge"] = json.loads(last("merge")[2])
''')
        self.assertEqual(out["proposed"], [["refs/heads/trunk"], True, True, False, "Można scalić"])
        self.assertEqual(out["noMerge"], [])
        self.assertEqual(out["shortcuts"], ["Główna · refs/heads/trunk"])
        self.assertEqual(out["accepted"], [["refs/heads/trunk"], False, True])
        self.assertEqual(out["merge"], {"targetRef": "refs/heads/trunk", "previewToken": "t:refs/heads/trunk"})

    def test_unknown_base_choosing_another_target_sends_new_preview_and_disables_merge(self):
        out = self.probe(r'''
open_choosing("")
give(listing([branch("trunk"), branch("other")], main="refs/heads/trunk"))
answer(last("preview")[1], ok("refs/heads/trunk"))
scroll_picker()
click("mergeTargetResult0")
out["chosen"] = [previews(), win("proposal"), item("mergeWindowMerge")["enabled"], win("pending")]
answer(last("preview")[1], ok("refs/heads/other"))
out["ready"] = [item("mergeWindowMerge")["enabled"], item("mergeWindowProposal")["visible"]]
''')
        self.assertEqual(out["chosen"], [["refs/heads/trunk", "refs/heads/other"], False, False, "preview"])
        self.assertEqual(out["ready"], [True, False])

    def test_unselectable_target_shows_reason_and_sends_no_preview(self):
        reason = "Cel należy do innej karty <b>"
        out = self.probe("reason = " + repr(reason) + r'''
open_choosing("refs/heads/base")
give(listing([branch("base", False, reason), branch("other")], main="refs/heads/other", default="refs/heads/base"))
out["default"] = [previews(), item("mergeWindowTarget")["text"], item("mergeWindowTargetNote")["visible"],
                  item("mergeWindowTargetNote")["text"], item("mergeWindowMerge")["enabled"]]
out["shortcuts"] = shortcuts()
click("mergeTargetShortcut0")
out["afterBlockedClick"] = previews()
click("mergeTargetShortcut1")
out["afterOther"] = [previews(), item("mergeWindowTargetNote")["visible"]]
''')
        self.assertEqual(out["default"][0:3], [[], "K-12 → wybierz cel", True])
        self.assertIn("refs/heads/base", out["default"][3])
        self.assertIn("Cel należy do innej karty <b>", out["default"][3])
        self.assertFalse(out["default"][4])
        self.assertEqual(out["shortcuts"], ["Baza karty · refs/heads/base — Cel należy do innej karty <b>",
                                            "Główna · refs/heads/other"])
        self.assertEqual(out["afterBlockedClick"], [], "klik w cel niewybieralny nie wysyła podglądu")
        self.assertEqual(out["afterOther"], [["refs/heads/other"], False])

    def test_repository_without_master_and_origin_uses_only_service_answers(self):
        for main in ("main", "develop"):
            for known in (True, False):
                with self.subTest(main=main, baseKnown=known):
                    ref = "refs/heads/" + main
                    out = self.probe("ref = " + repr(ref) + "\nknown = " + repr(known) + r'''
open_choosing(ref if known else "")
give(listing([branch(ref.split("/", 2)[2]), branch("feature/a")], main=ref, default=ref if known else ""))
answer(last("preview")[1], ok(ref))
if known:
    click("mergeWindowTargetToggle")
out["previews"] = previews()
out["shortcuts"] = shortcuts()
out["target"] = item("mergeWindowTarget")["text"]
out["texts"] = visible_texts()
out["proposal"] = win("proposal")
out["merge"] = item("mergeWindowMerge")["enabled"]
''')
                    self.assertEqual(out["previews"], [ref])
                    self.assertEqual(out["shortcuts"], [("Baza karty · " if known else "Główna · ") + ref])
                    self.assertEqual(out["target"], "K-12 → " + main)
                    self.assertEqual(out["proposal"], not known)
                    self.assertEqual(out["merge"], known)
                    joined = "\n".join(out["texts"]).lower()
                    self.assertNotIn("master", joined)
                    self.assertNotIn("origin", joined)
                    self.assertNotIn("refs/remotes", joined)

    def test_answer_without_main_ref_hides_shortcut_and_guesses_no_target(self):
        out = self.probe(r'''
open_choosing("")
give(listing([branch("trunk"), branch("master")], recent=[branch("recent")]))
out["state"] = [previews(), item("mergeWindowTarget")["text"], item("mergeWindowState")["text"],
                item("mergeWindowMerge")["enabled"], win("proposal")]
out["shortcuts"] = shortcuts()
''')
        self.assertEqual(out["state"], [[], "K-12 → wybierz cel", "Wybierz cel scalenia", False, False])
        self.assertEqual(out["shortcuts"], ["Ostatni cel · refs/heads/recent"], "bez mainRef brak skrótu głównej")

    def test_branch_list_reply_with_stale_tag_is_ignored(self):
        out = self.probe(r'''
open_choosing("refs/heads/feature/x")
old = json.loads(js("JSON.stringify(fake.branches)"))[-1][0]
open_window(ref="", branch="")
out["tags"] = [old, json.loads(js("JSON.stringify(fake.branches)"))[-1][0]]
reply = listing([branch("feature/x")], main="refs/heads/feature/x", default="refs/heads/feature/x")
js("loader.item.receiveBranches(" + json.dumps(old) + ", " + json.dumps(reply) + ")")
out["afterOld"] = previews()
give(reply)
out["afterNew"] = previews()
click("mergeWindowClose")
js("loader.item.receiveBranches(" + json.dumps(out["tags"][1]) + ", " + json.dumps(reply) + ")")
out["afterClose"] = [previews(), win("opened")]
''')
        self.assertNotEqual(out["tags"][0], out["tags"][1])
        self.assertEqual(out["afterOld"], [])
        self.assertEqual(out["afterNew"], ["refs/heads/feature/x"])
        self.assertEqual(out["afterClose"], [["refs/heads/feature/x"], False])

    def test_window_sources_have_no_fixed_branch_names(self):
        for name in ("MergeWindow.qml", "BranchPicker.qml"):
            with open(os.path.join(GIT_DIR, name), encoding="utf-8") as fh:
                source = fh.read()
            self.assertIsNone(re.search(r"\bmaster\b|\borigin\b", source), name)


class PreviewTest(MergeWindowCase):
    def test_minimal_preview_for_clean_conflict_and_blocked_fixtures(self):
        clean = {**PREVIEW, "warnings": ["Lokalne kolizje nie zostały ocenione."]}
        conflict = {**PREVIEW, "canMerge": False, "previewToken": "", "reason": "conflict", "message": "Konflikty",
                    "conflicts": ["a/<b>x</b>.txt", "b.txt", "c.txt"], "conflictCount": 3}
        single = {**conflict, "conflicts": ["only.txt"], "conflictCount": 1}
        blocked = {**PREVIEW, "canMerge": False, "reason": "target-busy", "message": "",
                   "blockedReasons": [{"reason": "target-busy", "message": "Cel należy do czynnego kroku"},
                                      {"reason": "dirty", "message": "W worktree celu są zmiany"}]}
        out = self.probe("fixtures = " + repr([clean, conflict, single, blocked]) + r'''
open_window(ref="refs/heads/release/<b>x", branch="")
out["header"] = [item("mergeWindowTarget")["text"], item("mergeWindowMerge")["text"]]
out["loading"] = item("mergeWindowState")["text"]
out["rows"] = []
for fixture in fixtures:
    win("refresh()")
    answer(last("preview")[1], {**fixture, "targetBranch": "release/<b>x", "targetRef": "refs/heads/release/<b>x"})
    out["rows"].append({name: item(name) for name in ("mergeWindowState", "mergeWindowConflicts", "mergeWindowBlocked",
                                                       "mergeWindowWarnings", "mergeWindowDetails", "mergeWindowConsent")})
click("mergeWindowDetailsToggle")
out["detailsOpen"] = item("mergeWindowDetails")["visible"]
out["formats"] = non_plain_texts()
''')
        self.assertEqual(out["header"], ["K-12 → release/<b>x", "Scal do release/<b>x"])
        self.assertEqual(out["loading"], "Przygotowuję podgląd…")
        clean_row, conflict_row, single_row, blocked_row = out["rows"]
        self.assertEqual(clean_row["mergeWindowState"]["text"], "Można scalić")
        self.assertFalse(clean_row["mergeWindowConflicts"]["visible"])
        self.assertFalse(clean_row["mergeWindowBlocked"]["visible"])
        self.assertEqual(clean_row["mergeWindowWarnings"]["rows"], ["Lokalne kolizje nie zostały ocenione."])
        self.assertFalse(clean_row["mergeWindowDetails"]["visible"], "„Szczegóły” są domyślnie zwinięte")
        for sha in ("a" * 40, "b" * 40, "c" * 40):
            self.assertIn(sha, clean_row["mergeWindowDetails"]["text"])
        self.assertIn("Nie stanowi zgody", clean_row["mergeWindowConsent"]["text"])
        self.assertNotIn("token-1", json.dumps(clean_row))
        self.assertEqual(conflict_row["mergeWindowState"]["text"], "Konflikty w 3 plikach")
        self.assertEqual(conflict_row["mergeWindowConflicts"]["title"], "Konflikty (3)")
        self.assertEqual(conflict_row["mergeWindowConflicts"]["rows"], ["a/<b>x</b>.txt", "b.txt", "c.txt"])
        self.assertIn("Kod: conflict", conflict_row["mergeWindowDetails"]["text"])
        self.assertEqual(single_row["mergeWindowState"]["text"], "Konflikt w 1 pliku")
        generic = "Scalenie jest zablokowane z powodu, którego to okno nie zna — szczegóły (kod) są niżej."
        self.assertEqual(blocked_row["mergeWindowState"]["text"], "Scalenie zablokowane: " + generic)
        self.assertEqual(blocked_row["mergeWindowBlocked"]["rows"], [generic], "nieznane kody: jedno ogólne zdanie")
        self.assertTrue(out["detailsOpen"])
        self.assertEqual(out["formats"], [], "wszystkie teksty z gita jako PlainText")

    def test_start_line_target_tooltip_and_changes_signal(self):
        out = self.probe(r'''
js("loader.item.startLine = 'Start od: master (3.11, 58a3f26)'")
open_window(card="K-7", ref="refs/remotes/origin/develop", branch="")
out["start"] = item("mergeWindowStart")
out["target"] = [item("mergeWindowTarget")["text"], win("targetRef")]
click("mergeWindowChanges")
out["changes"] = last("changes")
out["opened"] = win("opened")
js("loader.item.startLine = ''")
out["noStart"] = item("mergeWindowStart")["visible"]
''')
        self.assertEqual(out["start"]["text"], "Start od: master (3.11, 58a3f26)")
        self.assertTrue(out["start"]["visible"])
        self.assertEqual(out["target"], ["K-7 → origin/develop", "refs/remotes/origin/develop"])
        self.assertEqual(out["changes"], ["changes", "K-7"])
        self.assertTrue(out["opened"], "„Zmiany gałęzi” nie zamyka okna")
        self.assertFalse(out["noStart"])

    def test_long_preview_scrolls_and_buttons_stay_visible(self):
        long = {**PREVIEW, "canMerge": False, "conflicts": ["plik-%d.txt" % i for i in range(400)],
                "conflictCount": 400}
        out = self.probe("long = " + repr(long) + r'''
open_window()
answer(last("preview")[1], long)
wait(100)
out["geometry"] = json.loads(js("(function () { const all = " + ALL + "; const w = loader.item; "
    "const f = n => { const it = all(w, n)[0]; const p = it.mapToItem(w, 0, 0); return [p.y, p.y + it.height] }; "
    "return JSON.stringify({merge: f('mergeWindowMerge'), close: f('mergeWindowClose'), height: w.height, "
    "scroll: f('mergeWindowScroll')}) })()"))
''')
        geometry = out["geometry"]
        for name in ("merge", "close"):
            self.assertGreaterEqual(geometry[name][0], geometry["scroll"][1] - 1, name)
            self.assertLessEqual(geometry[name][1], geometry["height"], name)


SECTION_NAMES = ("mergeWindowBlocked", "mergeWindowConflicts", "mergeWindowCollisions", "mergeWindowPreparation",
                 "mergeWindowCommits", "mergeWindowFiles", "mergeWindowWarnings")
GENERIC_REASON = "Scalenie jest zablokowane z powodu, którego to okno nie zna — szczegóły (kod) są niżej."
ENGINE = os.path.join(REPO, "daemon", "tide_daemon", "cardflow", "engine.py")
GITOPS = os.path.join(REPO, "daemon", "tide_daemon", "cardflow", "gitops.py")
# Kody `reason`/`blockedReasons` z `PreviewCardMerge` (docs/tide-daemon.md, „PreviewCardMerge”; PT-199, PT-204, PT-206, PT-210).
KNOWN_CODES = ("conflict", "unrelated-history", "git-capability", "git-error", "no-sandbox", "state-changed",
               "base-decision", "not-closed", "active-step", "plan-change", "base-change", "pending-action",
               "card-target", "protected-target", "no-target-worktree", "target-path-occupied", "target-name-occupied",
               "source-dirty", "git-operation", "local-collision", "local-unassessed")

RICH = {**PREVIEW, "canMerge": False, "previewToken": "", "reason": "conflict", "message": "Konflikty",
        "targetRef": "refs/heads/release/<b>x", "targetBranch": "release/<b>x",
        "blockedReasons": [{"reason": "conflict", "message": "Konflikty"},
                           {"reason": "active-step", "message": "Krok karty jest jeszcze czynny."},
                           {"reason": "local-collision", "message": "Lokalna praca koliduje."}],
        "commits": [{"sha": "1234567" + "a" * 33, "subject": "Dodaj <b>okno</b> & %s"},
                    {"sha": "7654321" + "b" * 33, "subject": "Druga zmiana"}], "commitCount": 2,
        "files": [{"status": "M", "oldPath": "a.txt", "newPath": "a.txt"},
                  {"status": "R100", "oldPath": "old/<i>n</i>.txt", "newPath": "new/n.txt"},
                  {"status": "A", "oldPath": "", "newPath": "we ird\tna\nme%s&amp;.txt"}], "fileCount": 3,
        "conflicts": ["a.txt", "<u>b</u>.txt"], "conflictCount": 2,
        "localCollisions": {"staged": [{"status": "M ", "paths": ["x.txt"], "mergePaths": ["x.txt"]}],
                            "unstaged": [],
                            "untracked": [{"status": "??", "paths": ["u\tv.txt"], "mergePaths": ["u\tv.txt", "w.txt"]}]},
        "localCollisionCount": 2,
        "targetPreparation": {"createRef": True, "createWorktree": True, "startCommit": "d" * 40,
                              "remoteRef": "refs/remotes/team/f", "remoteCommit": "d" * 40, "path": "/tmp/wt <1>"},
        "warnings": ["Uwaga <b>1</b>"]}


class SectionsTest(MergeWindowCase):
    def test_sections_show_for_fixtures_with_data_and_vanish_for_empty_lists(self):
        out = self.probe("rich = " + repr(RICH) + "\nnames = " + repr(SECTION_NAMES) + r'''
open_window(ref="refs/heads/release/<b>x", branch="")
answer(last("preview")[1], rich)
out["rich"] = {n: section_rows(n) for n in names}
out["state"] = item("mergeWindowState")["text"]
out["formats"] = non_plain_texts()
click("mergeWindowDetailsToggle")
out["details"] = item("mergeWindowDetails")["text"]
win("refresh()")
answer(last("preview")[1], {**PREVIEW, "targetRef": "refs/heads/release/<b>x"})
out["empty"] = {n: section_rows(n)["visible"] for n in names}
out["emptyState"] = item("mergeWindowState")["text"]
''')
        rich = out["rich"]
        self.assertEqual(out["state"], "Konflikty w 2 plikach")
        self.assertEqual(rich["mergeWindowCommits"]["title"], "Wnoszone commity (2)")
        self.assertEqual(rich["mergeWindowCommits"]["rows"], ["1234567  Dodaj <b>okno</b> & %s", "7654321  Druga zmiana"])
        self.assertEqual(rich["mergeWindowFiles"]["title"], "Pliki (3)")
        self.assertEqual(rich["mergeWindowFiles"]["rows"],
                         ["M  a.txt", "R100  old/<i>n</i>.txt → new/n.txt", "A  we ird\\tna\\nme%s&amp;.txt"])
        self.assertEqual(rich["mergeWindowConflicts"]["title"], "Konflikty (2)")
        self.assertEqual(rich["mergeWindowConflicts"]["rows"], ["a.txt", "<u>b</u>.txt"])
        self.assertEqual(rich["mergeWindowCollisions"]["title"], "Kolizje z lokalną pracą (2)")
        self.assertEqual(rich["mergeWindowCollisions"]["rows"],
                         ["Staged (w indeksie) (1):", "   M   x.txt · scalenie: x.txt",
                          "Untracked (nieśledzone) (1):", "   ??  u\\tv.txt · scalenie: u\\tv.txt, w.txt"])
        self.assertEqual(rich["mergeWindowPreparation"]["title"], "Po zgodzie Tide przygotuje")
        self.assertEqual(rich["mergeWindowPreparation"]["rows"],
                         ["Nowa gałąź refs/heads/release/<b>x od refs/remotes/team/f", "Nowy worktree /tmp/wt <1>"])
        self.assertEqual(rich["mergeWindowWarnings"]["rows"], ["Uwaga <b>1</b>"])
        self.assertEqual(rich["mergeWindowBlocked"]["rows"],
                         ["Zmiany karty kolidują z celem — rozwiąż konflikty w plikach z listy.",
                          "Trwa krok karty — poczekaj na jego koniec.",
                          "Lokalna praca w celu koliduje ze ścieżkami scalenia."])
        for name in SECTION_NAMES:
            self.assertTrue(rich[name]["visible"], name)
        self.assertEqual(out["formats"], [], "teksty z gita (także z <b> i znakami specjalnymi) jako PlainText")
        for expected in ("SHA źródła: " + "a" * 40, "SHA celu: " + "b" * 40, "Drzewo wyniku: " + "c" * 40,
                         "[conflict]", "[active-step]", "[local-collision]"):
            self.assertIn(expected, out["details"])
        self.assertNotIn("1234567", out["details"], "commity i pliki są w sekcjach, nie w „Szczegółach”")
        self.assertEqual(out["empty"], {name: False for name in SECTION_NAMES}, "puste listy: sekcje znikają")
        self.assertEqual(out["emptyState"], "Można scalić")

    def test_conflict_preview_has_no_conflict_task_button_or_properties(self):
        out = self.probe("rich = " + repr(RICH) + r'''
open_window()
answer(last("preview")[1], {**rich, "targetRef": "refs/heads/master"})
out["state"] = item("mergeWindowState")["text"]
out["texts"] = visible_texts()
out["props"] = json.loads(js("(function () { const out = []; for (const k in loader.item) out.push(k); "
                             "return JSON.stringify(out) })()"))
''')
        self.assertEqual(out["state"], "Konflikty w 2 plikach")
        self.assertIn("canConfirm", out["props"], "kontrola: wyliczanie właściwości komponentu działa")
        self.assertEqual([t for t in out["texts"] if "Zleć" in t or "rozwiązanie konfliktów" in t.lower()], [])
        self.assertEqual([k for k in out["props"] if k.lower().startswith("conflicttask")], [])

    def test_collision_and_preparation_sections_follow_their_own_data(self):
        out = self.probe("rich = " + repr(RICH) + r'''
open_window(ref="refs/heads/release/<b>x", branch="")
only_ref = {**rich, "localCollisions": {"staged": [], "unstaged": [], "untracked": []}, "localCollisionCount": 0,
            "targetPreparation": {"createRef": True, "createWorktree": False, "startCommit": "d" * 40,
                                  "remoteRef": "", "remoteCommit": "", "path": ""}}
answer(last("preview")[1], only_ref)
out["refOnly"] = [section_rows("mergeWindowPreparation")["rows"], section_rows("mergeWindowCollisions")["visible"]]
win("refresh()")
answer(last("preview")[1], {**only_ref, "targetPreparation": {"createRef": False, "createWorktree": True,
                                                                "path": "/tmp/own"}})
out["worktreeOnly"] = section_rows("mergeWindowPreparation")["rows"]
win("refresh()")
answer(last("preview")[1], {k: v for k, v in only_ref.items() if k not in ("localCollisions", "targetPreparation")})
out["none"] = [section_rows("mergeWindowPreparation")["visible"], section_rows("mergeWindowCollisions")["visible"]]
''')
        self.assertEqual(out["refOnly"], [["Nowa gałąź refs/heads/release/<b>x"], False])
        self.assertEqual(out["worktreeOnly"], ["Nowy worktree /tmp/own"])
        self.assertEqual(out["none"], [False, False])

    def test_every_known_blocked_code_has_a_polish_sentence_and_unknown_code_is_generic(self):
        out = self.probe("codes = " + repr(list(KNOWN_CODES) + ["nowy-kod-z-przyszlosci"]) + r'''
open_window()
out["rows"] = {}
for code in codes:
    win("refresh()")
    answer(last("preview")[1], {**PREVIEW, "canMerge": False, "previewToken": "", "reason": code, "message": "Usługa: " + code,
                                "blockedReasons": [{"reason": code, "message": "Usługa: " + code}]})
    click("mergeWindowDetailsToggle") if not item("mergeWindowDetails")["visible"] else None
    out["rows"][code] = [section_rows("mergeWindowBlocked")["rows"], item("mergeWindowState")["text"],
                         item("mergeWindowDetails")["text"]]
''')
        for code in KNOWN_CODES:
            rows, state, details = out["rows"][code]
            self.assertEqual(len(rows), 1, code)
            self.assertNotEqual(rows[0], GENERIC_REASON, code)
            self.assertTrue(rows[0].endswith("."), code)
            self.assertNotIn(code, rows[0], "kod tylko w „Szczegółach”")
            self.assertIn("[" + code + "]", details)
            if code != "conflict":
                self.assertEqual(state, "Scalenie zablokowane: " + rows[0], code)
        rows, state, details = out["rows"]["nowy-kod-z-przyszlosci"]
        self.assertEqual(rows, [GENERIC_REASON])
        self.assertNotIn("nowy-kod", rows[0] + state)
        self.assertIn("[nowy-kod-z-przyszlosci]", details)

    def test_known_codes_match_the_codes_the_service_can_return(self):
        # Docs (`tide-daemon.md`) opisują tylko 7 kodów; pełną listę gwarantuje porównanie z kodem usługi poniżej.
        found = set()
        with open(ENGINE, encoding="utf-8") as fh:
            engine = fh.read()
        with open(GITOPS, encoding="utf-8") as fh:
            gitops = fh.read()
        def between(text, start, end):
            head = text.index(start)
            return text[head:text.index(end, head + len(start))]

        # Tylko kod, który buduje podgląd scalenia: kwalifikacja domknięcia i celu, przygotowanie celu oraz merge-tree.
        engine = (between(engine, "    def _merge_target_blocks(", "    def ") + "\n"
                  + between(engine, "    def _card_merge_blocks(", "    def request_card_base_change(") + "\n"
                  + between(engine, "    def preview_card_merge(", "    def merge_card("))
        gitops = between(gitops, "    def prepare_merge_tree(", "    def repository_key(")
        found.update(re.findall(r'\bblock\("([a-z-]+)"', engine))
        found.update(re.findall(r'reasons\.append\(\("([a-z-]+)"', engine))
        found.update(re.findall(r'"reason": "([a-z-]+)"', engine))
        found.update(re.findall(r'\breason ?= ?"([a-z-]+)"', engine + gitops))
        found.update(re.findall(r'"([a-z-]+)" if \w+\.code', gitops))
        found.update(re.findall(r'else "([a-z-]+)"', gitops))
        self.assertEqual(sorted(found - set(KNOWN_CODES)), [], "kod usługi bez zdania w oknie scalania")
        for code in KNOWN_CODES:
            self.assertIn('"' + code + '"', engine + gitops, code)
        with open(os.path.join(REPO, "docs", "tide-daemon.md"), encoding="utf-8") as fh:
            docs = fh.read()
        for code in ("conflict", "unrelated-history", "git-capability", "git-error", "no-sandbox", "local-collision",
                     "no-target-worktree"):
            self.assertIn(code, docs, code)
        with open(WINDOW_QML, encoding="utf-8") as fh:
            source = fh.read()
        for code in KNOWN_CODES:
            self.assertIn('"' + code + '":', source, code)

    def test_long_file_and_commit_lists_scroll_inside_their_sections_and_buttons_stay_visible(self):
        big = {**PREVIEW, "files": [{"status": "M", "oldPath": "dir/plik-%d.txt" % i, "newPath": "dir/plik-%d.txt" % i}
                                    for i in range(500)], "fileCount": 500,
               "commits": [{"sha": ("%07x" % i) + "0" * 33, "subject": "Commit %d" % i} for i in range(500)],
               "commitCount": 500}
        out = self.probe("big = " + repr(big) + r'''
open_window()
answer(last("preview")[1], big)
wait(150)
out["geometry"] = json.loads(js("(function () { const all = " + ALL + "; const w = loader.item; "
    "const f = n => { const it = all(w, n)[0]; const p = it.mapToItem(w, 0, 0); return [p.y, p.y + it.height] }; "
    "const lists = n => { const it = all(w, n)[0]; return [it.height, it.contentHeight, it.count] }; "
    "return JSON.stringify({merge: f('mergeWindowMerge'), close: f('mergeWindowClose'), refresh: f('mergeWindowRefresh'), "
    "height: w.height, scroll: f('mergeWindowScroll'), files: lists('mergeWindowFilesList'), "
    "commits: lists('mergeWindowCommitsList')}) })()"))
out["merge"] = item("mergeWindowMerge")
out["titles"] = [section_rows("mergeWindowFiles")["title"], section_rows("mergeWindowCommits")["title"]]
''')
        geometry = out["geometry"]
        for name in ("merge", "close", "refresh"):
            self.assertGreaterEqual(geometry[name][0], geometry["scroll"][1] - 1, name)
            self.assertLessEqual(geometry[name][1], geometry["height"], name)
        self.assertTrue(out["merge"]["visible"])
        self.assertTrue(out["merge"]["enabled"], "długie listy nie zmieniają warunków aktywności „Scal do”")
        self.assertEqual(out["merge"]["text"], "Scal do master")
        self.assertEqual(out["titles"], ["Pliki (500)", "Wnoszone commity (500)"])
        for name in ("files", "commits"):
            height, content, count = geometry[name]
            self.assertLessEqual(height, 160 + 1, name + ": lista ma górną granicę wysokości")
            self.assertGreater(content, height, name + ": reszta wierszy przewija się w sekcji")
            self.assertGreater(count, 0, name)


class SourceTest(unittest.TestCase):
    def test_component_is_host_independent_plain_text_and_palette_only(self):
        with open(WINDOW_QML, encoding="utf-8") as fh:
            source = fh.read()
        code = re.sub(r"//.*", "", source)
        self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', code), [])
        self.assertNotIn("RichText", code)
        self.assertNotIn("StyledText", code)
        for word in ("backend", "Board", "page.", "CardPanel", "MergeCard", "PreviewCardMerge", "../../"):
            self.assertNotIn(word, code, word)
        imports = re.findall(r"^import (.+)$", code, re.M)
        self.assertEqual(sorted(imports), sorted(['QtQuick', 'QtQuick.Layouts', 'QtQuick.Controls as QQC2',
                                                  '"../tide" as Tide', '"start.js" as Start']))

    def test_windows_have_no_merge_without_token_path_or_old_merge_components(self):
        """K-99 #16: w oknach (`apps/`) nie ma drogi scalenia bez tokenu ani starych komponentów scalania i ich
        przycisków — żadnego odwołania, nawet w komentarzu. Nazwy w tym teście są rozbite na fragmenty, żeby
        wyszukiwanie po literalnej nazwie zwracało pusty wynik."""
        patterns = re.compile("|".join((r"MergeCard" + "ToMain", r"mergeCard" + "ToMain", r"mergeMain" + "Requested",
                                        r"Merge" + "Preview", r"MergeTarget" + "Picker", r"cardPreview" + "Merge",
                                        r"cardMerge" + "Main", r"cardMerge" + "Choose", r"fullPreview" + "Merge",
                                        r"fullMerge" + "Main", r"fullMerge" + "Choose")))
        found = []
        for root, _dirs, files in os.walk(os.path.join(REPO, "apps")):
            for name in files:
                if not name.endswith((".qml", ".js", ".py")) or name == os.path.basename(__file__):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as fh:
                    for number, line in enumerate(fh, 1):
                        if patterns.search(line):
                            found.append(f"{os.path.relpath(path, REPO)}:{number}")
        self.assertEqual(found, [])
        for gone in ("apps/board/qml/Merge" + "Preview.qml", "apps/board/qml/MergeTarget" + "Picker.qml"):
            self.assertFalse(os.path.exists(os.path.join(REPO, gone)), gone)

    def test_board_backend_does_not_allow_merge_without_token(self):
        with open(os.path.join(REPO, "apps/board/backend.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('"MergeCard"', source)
        self.assertNotIn("MergeCard" + "ToMain", source)


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class BoardMergeWindowTest(ProbeCase):
    def closed_card(self):
        data = full_d(self.data)
        for item in data["cards"]:
            if item["id"] == "K-8":
                item["derived"]["flow"]["mainBranch"] = "master"
                item["derived"]["flow"]["base"] = {"status": "known", "baseRef": "refs/heads/master",
                                                   "baseCommit": "58a3f26" + "0" * 33}
                item["derived"]["flow"].setdefault("timeline", []).append(
                    {"type": "card.started", "ts": "2026-11-03T12:00:00Z"})
                for phase in item["derived"]["phases"]:
                    if phase["id"] == "main-merge":
                        phase["state"] = "you"
        return data

    def test_panel_and_full_card_buttons_open_window_with_default_target(self):
        out = self.probe(STATE_PY + r'''
click(card("K-8"))
out["rows"] = []
for _ in range(2):   # ponowne otwarcie po Escape liczy podgląd od nowa
    replies.append({"ok": True, "canMerge": True, "targetRef": "refs/heads/master", "targetBranch": "master",
                    "previewToken": "t"})
    click(named("fullMerge"))
    until(lambda: not obj("mergeWindow").property("busy"))
    out["rows"].append([obj("mergeWindowTarget").property("text"), obj("mergeWindowStart").property("text"),
                        obj("mergeWindowMerge").property("enabled"), calls()[-1][:2], json.loads(calls()[-1][2])])
    key(Qt.Key.Key_Escape)
    out["rows"][-1].append(obj("mergeWindow").property("opened"))
    out["rows"][-1].append(js("page.fullOpen"))
out["calls"] = [c[0] for c in calls()]
''', self.closed_card())
        for row in out["rows"]:
            self.assertEqual(row[0], "K-8 → master")
            self.assertTrue(row[1].startswith("Start od: master ("), row[1])
            self.assertTrue(row[2])
            self.assertEqual(row[3], ["PreviewCardMerge", "K-8"])
            self.assertEqual(row[4], {"targetRef": "refs/heads/master"})
            self.assertEqual(row[5:], [False, True], "Escape zamyka tylko okno, pełna karta zostaje")
        self.assertEqual(out["calls"], ["PreviewCardMerge"] * 2)

    def test_card_context_change_closes_window_and_drops_late_reply(self):
        out = self.probe(STATE_PY + r'''
click(card("K-8"))
replies.append({"ok": True, "canMerge": True, "targetRef": "refs/heads/master", "targetBranch": "master",
                "previewToken": "t"})
click(named("fullMerge"))
until(lambda: not obj("mergeWindow").property("busy"))
# Odpowiedź MergeCard wstrzymana do zmiany kontekstu karty (spóźniona odpowiedź z usługi).
held = []
plain_write = backend._write
def hold(tag, member, args):
    if member != "MergeCard":
        return plain_write(tag, member, args)
    backend._calls.append([member, *args])
    held.append(tag)
backend._write = hold
click(named("mergeWindowMerge"))
out["pending"] = obj("mergeWindow").property("pending")
snap = json.loads(backend.snapshotJson)
next(c for c in snap["cards"] if c["id"] == "K-8")["project"] = "another-project"
set_snapshot(snap)
out["opened"] = obj("mergeWindow").property("opened")
backend.replied.emit(held[0], json.dumps({"ok": True, "targetBranch": "master"}))
wait(100)
out["toast"] = js("page.toast")
out["calls"] = [c[0] for c in calls()]
''', self.closed_card())
        self.assertEqual(out["pending"], "merge")
        self.assertFalse(out["opened"])
        self.assertNotIn("Scalono", out["toast"] or "")
        self.assertEqual(out["calls"], ["PreviewCardMerge", "MergeCard"])

    def test_branch_changes_opens_card_diff_over_window_and_escape_returns(self):
        out = self.probe(STATE_PY + r'''
click(card("K-8"))
replies.append({"ok": True, "canMerge": True, "targetRef": "refs/heads/master", "targetBranch": "master",
                "previewToken": "t"})
click(named("fullMerge"))
until(lambda: not obj("mergeWindow").property("busy"))
click(named("mergeWindowChanges"))
out["diff"] = [js("page.diffOpen"), js("page.diffCardId"), obj("mergeWindow").property("opened")]
out["diffCall"] = calls()[-1]
key(Qt.Key.Key_Escape)
out["back"] = [js("page.diffOpen"), obj("mergeWindow").property("opened")]
key(Qt.Key.Key_Escape)
out["closed"] = [obj("mergeWindow").property("opened"), js("page.fullOpen")]
out["calls"] = [c[0] for c in calls()]
''', self.closed_card())
        self.assertEqual(out["diff"], [True, "K-8", True])
        self.assertEqual(out["diffCall"][0], "Diff")
        self.assertEqual(json.loads(out["diffCall"][1]), {"source": "card", "card": "K-8"})
        self.assertEqual(out["back"], [False, True])
        self.assertEqual(out["closed"], [False, True])
        self.assertNotIn("MergeCard", out["calls"])


if __name__ == "__main__":
    unittest.main()
