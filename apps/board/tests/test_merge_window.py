"""Okno scalania (K-99, PT-390): wspólny komponent apps/common/qml/git/MergeWindow.qml i jego osadzenie w Tablicy.

- kontrakt zgody na samym komponencie (sonda diffprobe.py na atrapie gospodarza): „Scal do <cel>” tylko przy
  `canMerge === true` i niepustym `previewToken`, `mergeRequested` z dokładnie `{targetRef, previewToken}` bieżącego
  podglądu; zamknięcie, Escape i „Odśwież” nie wysyłają zgody; odpowiedź starego żądania (inny tag) nie nadpisuje
  bieżącego podglądu; błąd scalenia blokuje przycisk do nowego udanego podglądu; sukces zamyka okno z komunikatem;
  minimalny podgląd (stan zdaniem, konflikty, powody blokad, ostrzeżenia, „Szczegóły”) i teksty z gita jako zwykły tekst,
- komponent bez zależności od Tablicy i bez wywołań usługi (źródło), kolory tylko z palety,
- Tablica: zmiana kontekstu karty zamyka okno i odrzuca spóźnioną odpowiedź, „Zmiany gałęzi” otwiera widok diffa
  karty (K-36) nad oknem, Esc wraca do okna; żaden przycisk nie emituje martwej drogi `mergeMainRequested`.
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
              "{text: it.text, visible: it.visible, enabled: it.enabled}) : '' })()")
    return json.loads(text) if text else None

def open_window(card="K-12", ref="refs/heads/master", branch="master"):
    js("loader.item.open(" + json.dumps(card) + ", " + json.dumps(ref) + ", " + json.dumps(branch) + ")")
    wait(60)

def last(kind):
    return [c for c in calls() if c[0] == kind][-1]

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
out["formats"] = js("(function () { const all = " + ALL.replace("it.visible", "true") + "; const names = "
    "['mergeWindowTarget', 'mergeWindowStart', 'mergeWindowState', 'mergeWindowError', 'mergeWindowConflicts', "
    "'mergeWindowBlocked', 'mergeWindowWarnings', 'mergeWindowConsent', 'mergeWindowDetails']; "
    "return JSON.stringify(names.map(n => all(loader.item, n)[0].textFormat)) })()")
''')
        self.assertEqual(out["header"], ["K-12 → release/<b>x", "Scal do release/<b>x"])
        self.assertEqual(out["loading"], "Przygotowuję podgląd…")
        clean_row, conflict_row, single_row, blocked_row = out["rows"]
        self.assertEqual(clean_row["mergeWindowState"]["text"], "Można scalić")
        self.assertFalse(clean_row["mergeWindowConflicts"]["visible"])
        self.assertFalse(clean_row["mergeWindowBlocked"]["visible"])
        self.assertEqual(clean_row["mergeWindowWarnings"]["text"], "Lokalne kolizje nie zostały ocenione.")
        self.assertFalse(clean_row["mergeWindowDetails"]["visible"], "„Szczegóły” są domyślnie zwinięte")
        for sha in ("a" * 40, "b" * 40, "c" * 40):
            self.assertIn(sha, clean_row["mergeWindowDetails"]["text"])
        self.assertIn("Nie stanowi zgody", clean_row["mergeWindowConsent"]["text"])
        self.assertNotIn("token-1", json.dumps(clean_row))
        self.assertEqual(conflict_row["mergeWindowState"]["text"], "Konflikty w 3 plikach")
        self.assertEqual(conflict_row["mergeWindowConflicts"]["text"],
                         "Pliki w konflikcie:\na/<b>x</b>.txt\nb.txt\nc.txt")
        self.assertIn("Kod: conflict", conflict_row["mergeWindowDetails"]["text"])
        self.assertEqual(single_row["mergeWindowState"]["text"], "Konflikt w 1 pliku")
        self.assertEqual(blocked_row["mergeWindowState"]["text"], "Scalenie zablokowane")
        self.assertEqual(blocked_row["mergeWindowBlocked"]["text"],
                         "Cel należy do czynnego kroku\nW worktree celu są zmiany")
        self.assertTrue(out["detailsOpen"])
        self.assertEqual(json.loads(out["formats"]), [0] * 9, "wszystkie teksty z gita jako PlainText")

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

    def test_no_button_emits_dead_merge_main_path(self):
        """K-99 #5: droga bez tokenu (`mergeMainRequested` → `MergeCardToMain`) to martwe okablowanie do #16 —
        sygnał jest zadeklarowany i obsłużony, ale nic go nie emituje (przed tą zmianą i po niej)."""
        emitted = []
        for root, _dirs, files in os.walk(os.path.join(REPO, "apps")):
            for name in files:
                if not name.endswith((".qml", ".js")):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as fh:
                    for number, line in enumerate(fh, 1):
                        code = line.split("//")[0]
                        if re.search(r"(?<![\w.])(?:\w+\.)?mergeMainRequested\(", code) and "signal " not in code:
                            emitted.append(f"{os.path.relpath(path, REPO)}:{number}")
        self.assertEqual(emitted, [])


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
for name in ("fullPreviewMerge", "fullMergeMain"):
    replies.append({"ok": True, "canMerge": True, "targetRef": "refs/heads/master", "targetBranch": "master",
                    "previewToken": "t"})
    click(named(name))
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
click(named("fullMergeMain"))
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
click(named("fullMergeMain"))
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
