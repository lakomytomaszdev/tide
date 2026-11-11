"""Wspólny widok diffa (K-36, PT-354): apps/common/qml/git/DiffView.qml, start.js i diff.js.

- `startLabel`: baza lokalna, zdalna, nieznana, nazwa z „/” i znakami specjalnymi, brak daty; nagłówek źródła `card`
  go używa (bez „refs/heads/”),
- sonda offscreen na atrapie backendu (diffprobe.py): lista plików ze statusem, liczbami i sumą, wybór pliku pokazuje
  jego hunki, `]`/`[`, Alt+↓/↑, `n`/`p`, Esc, `r` i „Odśwież”, „Czytam…” i błąd, otwarcie z parametrem ścieżki,
  `unknown-base` z wyborem refu i `Git.Diff` z `targetRef`, nazwy z `<b>` i unicode jako zwykły tekst,
- kolory tylko z palety (nowe tokeny `added`/`removed`), pakiet z plikami komponentu, wywołanie `Git.Diff` w backendzie.
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
from .test_view import QML_PROBLEMS, ProbeCase, offscreen, stage

sys.path.insert(0, os.path.join(REPO, "widgets"))
import stage as widget_stage  # noqa: E402

GIT_DIR = os.path.join(REPO, "apps", "common", "qml", "git")
VIEW_QML = os.path.join(GIT_DIR, "DiffView.qml")
PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diffprobe.py")
PALETTE = os.path.join(REPO, "theme", "palette", "mist.json")

HARNESS = '''
import QtQuick
import QtQuick.Window
import "tide" as Tide
import "git" as Git
import "git/start.js" as Start

Window {
    id: root
    width: 1100
    height: 700
    visible: true
    property int closed: 0
    property var req: ({})
    property string path: ""
    readonly property var tokens: Tide.Tokens

    function label(from) { return Start.startLabel(from) }
    function open(request, initial) {
        root.req = request
        root.path = initial
        loader.active = false
        loader.active = true
    }

    QtObject {
        id: fake
        signal replied(string tag, string json)
        property var calls: []
        function gitDiff(tag, request) { calls = calls.concat([["gitDiff", tag, request]]) }
        function listCardBranches(tag, card, request) { calls = calls.concat([["listCardBranches", tag, card, request]]) }
    }

    Loader {
        id: loader
        anchors.fill: parent
        active: false
        sourceComponent: Component {
            Git.DiffView {
                request: root.req
                initialPath: root.path
                backend: fake
                onCloseRequested: root.closed += 1
            }
        }
    }
}
'''

ISO = "2026-11-05T12:00:00Z"
SHA = "abcdef0123456789abcdef0123456789abcdef01"


def line(kind, old, new, text):
    return {"kind": kind, "oldNo": old, "newNo": new, "text": text}


def hunk(old_start, lines, header=""):
    old = sum(1 for x in lines if x["kind"] != "+")
    new = sum(1 for x in lines if x["kind"] != "-")
    return {"oldStart": old_start, "oldLines": old, "newStart": old_start, "newLines": new, "header": header,
            "lines": lines}


def file_entry(path, status="M", add=0, dele=0, hunks=(), old=None, binary=False):
    return {"path": path, "oldPath": old, "status": status, "additions": add, "deletions": dele, "binary": binary,
            "oldMode": None, "newMode": None, "warnings": [], "hunks": list(hunks)}


# Pierwszy hunk ma 60 linii kontekstu, żeby drugi leżał poniżej okna i `n` musiał przewinąć widok.
FIRST = hunk(1, [line(" ", i, i, f"kontekst {i}") for i in range(1, 31)]
             + [line("-", 31, None, "stara linia"), line("+", None, 31, "nowa linia")]
             + [line(" ", i, i, f"kontekst {i}") for i in range(32, 62)], "def pierwsza():")
SECOND = hunk(100, [line(" ", 100, 100, "dalej"), line("+", None, 101, "druga zmiana"), line(" ", 101, 102, "koniec")])
FILES = [
    file_entry("apps/a.py", "M", 2, 1, [FIRST, SECOND]),
    file_entry("new.txt", "A", 1, 0, [hunk(0, [line("+", None, 1, "pierwsza")])]),
    file_entry("gone.txt", "D", 0, 2, [hunk(1, [line("-", 1, None, "a"), line("-", 2, None, "b")])]),
    file_entry("b/nowa-nazwa.py", "R", 0, 0, [], old="a/stara-nazwa.py"),
    file_entry("img.png", "M", 0, 0, [], binary=True),
    file_entry("<b>zażółć</b>.txt", "A", 1, 0, [hunk(0, [line("+", None, 1, "<i>nie pogrubiaj</i> & <b>x</b>")])]),
]
DIFF = {"ok": True, "source": "card", "card": "K-36", "repo": "/tmp/r", "title": "K-36: zmiany gałęzi",
        "from": {"ref": "refs/heads/master", "sha": SHA, "date": ISO},
        "to": {"ref": "refs/heads/card/k-36-git-tide-mapa-galezi", "sha": "f" * 40, "date": ISO},
        "files": FILES, "totals": {"files": 6, "additions": 4, "deletions": 3}, "truncated": False, "warnings": []}
REQUEST = {"source": "card", "card": "K-36"}

SCENARIO_HEAD = '''
def open_view(request=None, path=""):
    js("open(" + json.dumps(request or {"source": "card", "card": "K-36"}) + ", " + json.dumps(path) + ")")
    wait(120)

def view(expr):
    return js("loader.item." + expr)

def texts(name):
    return field(name, "it.text")

def last_tag():
    return calls()[-1][1]
'''


class QmlCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-diff-view-")
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
            fh.write(SCENARIO_HEAD + "\nDIFF = json.loads(" + repr(json.dumps(DIFF)) + ")\n" + scenario)
        env = offscreen()
        env["TZ"] = "UTC"
        result = subprocess.run(["/usr/bin/python3", PROBE, self.tmp, script], env=env, capture_output=True,
                                text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", out, out)
        return out


class StartLabelTest(QmlCase):
    def test_labels(self):
        out = self.probe('''
def label(**point):
    return js("label(" + json.dumps(point) + ")")
out["local"] = label(ref="refs/heads/master", sha="%s", date="%s")
out["remote"] = label(ref="refs/remotes/origin/master", sha="%s", date="%s")
out["unknown"] = [js("label(null)"), js("label({})"), js("label(undefined)"), js("label({ref: '', sha: '', date: ''})")]
out["slash"] = label(ref="refs/heads/card/k-36-git-tide", sha="%s", date="%s")
out["special"] = label(ref="refs/heads/fix/<b>&amp;-ś", sha="%s", date="%s")
out["noDate"] = [label(ref="refs/heads/master", sha="%s"), label(ref="refs/heads/master", sha="%s", date=""),
                 label(ref="refs/heads/master", sha="%s", date="nie data")]
out["onlySha"] = label(sha="%s")
out["padded"] = label(ref="refs/heads/x", sha="%s", date="2026-03-07T12:00:00Z")
out["short"] = label(ref="refs/heads/x", sha="abc")
out["other"] = label(ref="HEAD~2", sha="%s", date="%s")
''' % (SHA, ISO, SHA, ISO, SHA, ISO, SHA, ISO, SHA, SHA, SHA, SHA, SHA, SHA, ISO))
        self.assertEqual(out["local"], "master · 5.11 · abcdef0")
        self.assertEqual(out["remote"], "origin/master · 5.11 · abcdef0")
        self.assertEqual(out["unknown"], ["nieznany"] * 4)
        self.assertEqual(out["slash"], "card/k-36-git-tide · 5.11 · abcdef0")
        self.assertEqual(out["special"], "fix/<b>&amp;-ś · 5.11 · abcdef0")
        self.assertEqual(out["noDate"], ["master · abcdef0"] * 3)
        self.assertEqual(out["onlySha"], "abcdef0")
        self.assertEqual(out["padded"], "x · 7.03 · abcdef0")
        self.assertEqual(out["short"], "x · abc")
        self.assertEqual(out["other"], "HEAD~2 · 5.11 · abcdef0")

    def test_only_one_start_formatter_lives_in_the_tree(self):
        with open(os.path.join(GIT_DIR, "start.js"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(len(re.findall(r"function startLabel\(", source)), 1)
        self.assertNotIn("refs/heads/", re.sub(r"//.*", "", source).replace('"refs/heads/"', ""))


class ViewTest(QmlCase):
    def test_files_totals_and_selected_file_lines(self):
        out = self.probe('''
open_view()
out["calls"] = calls()
out["reading"] = [texts("diffLoading"), texts("diffReading")]
reply(last_tag(), DIFF)
out["readingAfter"] = [texts("diffLoading"), texts("diffReading")]
out["header"] = texts("diffHeader")
out["names"] = texts("diffFileName")
out["statuses"] = texts("diffFileStatus")
out["stats"] = texts("diffFileStats")
out["totals"] = texts("diffTotals")
out["selected"] = view("selectedPath")
out["lines"] = field("diffLine", "[it.kind, it.oldLabel, it.newLabel, it.content]")
out["tones"] = field("diffLine", "String(it.tone)")
out["tokens"] = [js("String(tokens.added)"), js("String(tokens.removed)"), js("String(tokens.lavender)")]
click("diffFile", 2)
out["goneLines"] = field("diffLine", "[it.kind, it.oldLabel, it.newLabel, it.content]")
click("diffFile", 3)
out["renameNote"] = texts("diffFileNote")
click("diffFile", 4)
out["binaryNote"] = texts("diffFileNote")
''')
        self.assertEqual(out["calls"], [["gitDiff", "diff:1", json.dumps(REQUEST, separators=(",", ":"))]])
        self.assertEqual(out["reading"], [["Czytam…"], ["Czytam…"]], "stan „Czytam…” w trakcie odczytu")
        self.assertEqual(out["readingAfter"], [[], []])
        self.assertEqual(out["header"], ["K-36 · zmiany od startu: master · 5.11 · abcdef0 → card/k-36-git-tide-mapa-galezi"])
        self.assertNotIn("refs/heads/", out["header"][0])
        self.assertEqual(out["names"], ["apps/a.py", "new.txt", "gone.txt", "a/stara-nazwa.py → b/nowa-nazwa.py",
                                        "img.png", "<b>zażółć</b>.txt"])
        self.assertEqual(out["statuses"], ["M", "A", "D", "R", "bin", "A"])
        self.assertEqual(out["stats"], ["+2 −1", "+1 −0", "+0 −2", "+0 −0", "", "+1 −0"], "plik binarny bez liczb")
        self.assertEqual(out["totals"], ["6 plików · +4 −3"])
        self.assertEqual(out["selected"], "apps/a.py")
        self.assertEqual(out["lines"][0], ["hunk", "", "", "@@ -1,61 +1,61 @@ def pierwsza():"])
        self.assertEqual(out["lines"][1], [" ", "1", "1", "kontekst 1"])
        changes = [row for row in out["lines"] if row[0] in "+-"]
        self.assertEqual(changes, [["-", "31", "", "stara linia"], ["+", "", "31", "nowa linia"]])
        by_kind = dict(zip((row[0] for row in out["lines"]), out["tones"]))
        self.assertEqual(by_kind["+"], out["tokens"][0], "dodane: token palety")
        self.assertEqual(by_kind["-"], out["tokens"][1], "usunięte: token palety")
        self.assertEqual(by_kind["hunk"], out["tokens"][2])
        self.assertEqual(out["goneLines"], [["hunk", "", "", "@@ -1,2 +1,0 @@"], ["-", "1", "", "a"], ["-", "2", "", "b"]])
        self.assertEqual(out["renameNote"], ["Brak zmian w treści pliku."])
        self.assertEqual(out["binaryNote"], ["Plik binarny — bez podglądu zmian."])

    def test_keyboard_navigation_close_and_refresh(self):
        out = self.probe('''
from PyQt6.QtCore import Qt
M = Qt.KeyboardModifier
open_view()
reply(last_tag(), DIFF)
out["start"] = [view("fileIndex"), view("currentHunk"), view("hunkStarts.length")]
key(Qt.Key.Key_N)
out["n"] = [view("currentHunk"), field("diffLines", "it.contentY")[0] > 0]
key(Qt.Key.Key_N)
out["nAtEnd"] = view("currentHunk")
key(Qt.Key.Key_P)
out["p"] = [view("currentHunk"), field("diffLines", "it.contentY")[0]]
key(Qt.Key.Key_BracketRight)
out["next"] = view("selectedPath")
key(Qt.Key.Key_BracketLeft)
out["prev"] = view("selectedPath")
key(Qt.Key.Key_BracketLeft)
out["prevAtStart"] = view("selectedPath")
key(Qt.Key.Key_Down, M.AltModifier)
key(Qt.Key.Key_Down, M.AltModifier)
out["altDown"] = view("selectedPath")
key(Qt.Key.Key_Up, M.AltModifier)
out["altUp"] = view("selectedPath")
out["hunkAfterFileChange"] = view("currentHunk")
for _ in range(10):
    key(Qt.Key.Key_BracketRight)
out["last"] = view("selectedPath")
out["closedBefore"] = js("closed")
key(Qt.Key.Key_Escape)
out["closed"] = js("closed")
click("diffClose")
out["closedByButton"] = js("closed")
key(Qt.Key.Key_R)
out["afterR"] = [[c[0], c[1]] for c in calls()]
out["loadingAfterR"] = texts("diffLoading")
reply(last_tag(), DIFF)
out["keptFile"] = view("selectedPath")
click("diffRefresh")
out["afterButton"] = [[c[0], c[1]] for c in calls()]
reply(last_tag(), DIFF)
reply("diff:1", DIFF)
out["staleIgnored"] = view("loading")
''')
        self.assertEqual(out["start"], [0, 0, 2])
        self.assertEqual(out["n"], [1, True])
        self.assertEqual(out["nAtEnd"], 1)
        self.assertEqual(out["p"], [0, 0])
        self.assertEqual(out["next"], "new.txt")
        self.assertEqual(out["prev"], "apps/a.py")
        self.assertEqual(out["prevAtStart"], "apps/a.py")
        self.assertEqual(out["altDown"], "gone.txt")
        self.assertEqual(out["altUp"], "new.txt")
        self.assertEqual(out["hunkAfterFileChange"], 0)
        self.assertEqual(out["last"], "<b>zażółć</b>.txt")
        self.assertEqual((out["closedBefore"], out["closed"], out["closedByButton"]), (0, 1, 2))
        self.assertEqual(out["afterR"], [["gitDiff", "diff:1"], ["gitDiff", "diff:2"]])
        self.assertEqual(out["loadingAfterR"], ["Czytam…"])
        self.assertEqual(out["keptFile"], "<b>zażółć</b>.txt", "odświeżenie zostaje na wybranym pliku")
        self.assertEqual(out["afterButton"][-1], ["gitDiff", "diff:3"])
        self.assertFalse(out["staleIgnored"])

    def test_opens_on_the_requested_path(self):
        out = self.probe('''
open_view(path="gone.txt")
reply(last_tag(), DIFF)
out["path"] = [view("selectedPath"), view("fileIndex")]
out["lines"] = field("diffLine", "it.content")
open_view(path="nie/ma/takiego.txt")
reply(last_tag(), DIFF)
out["unknown"] = view("selectedPath")
js("open({source: 'card', card: 'K-36'}, 'a/stara-nazwa.py')")
wait(120)
reply(last_tag(), DIFF)
out["renamedFromOld"] = view("selectedPath")
''')
        self.assertEqual(out["path"], ["gone.txt", 2])
        self.assertEqual(out["lines"], ["@@ -1,2 +1,0 @@", "a", "b"])
        self.assertEqual(out["unknown"], "apps/a.py")
        self.assertEqual(out["renamedFromOld"], "b/nowa-nazwa.py")

    def test_service_error_is_one_sentence_with_next_step(self):
        out = self.probe('''
open_view()
reply(last_tag(), {"ok": False, "error": "no-branch", "message": "K-36: gałąź karty x nie istnieje"})
out["errorText"] = texts("diffError")
out["picker"] = len(field("diffBasePicker", "1"))
out["calls"] = [c[0] for c in calls()]
out["files"] = texts("diffFileName")
key(Qt.Key.Key_R)
reply(last_tag(), DIFF)
out["recovered"] = [texts("diffError"), view("selectedPath")]
reply("nieznany-tag", {"ok": False, "error": "failed"})
out["stillOk"] = texts("diffError")
''')
        self.assertEqual(len(out["errorText"]), 1)
        self.assertIn("K-36: gałąź karty x nie istnieje", out["errorText"][0])
        self.assertIn("odśwież", out["errorText"][0])
        self.assertEqual(out["picker"], 0)
        self.assertEqual(out["calls"], ["gitDiff"], "inne błędy nie pytają o listę refów")
        self.assertEqual(out["files"], [])
        self.assertEqual(out["recovered"], [[], "apps/a.py"])
        self.assertEqual(out["stillOk"], [])

    def test_unknown_base_offers_refs_and_retries_with_target_ref(self):
        out = self.probe('''
open_view()
reply(last_tag(), {"ok": False, "error": "unknown-base", "message": "K-36: baza startu karty jest nieznana"})
out["errorText"] = texts("diffError")
out["calls"] = calls()
branch_tag = calls()[1][1]
reply(branch_tag, {"ok": True, "branches": [
    {"ref": "refs/heads/master", "name": "master", "selectable": True},
    {"ref": "refs/heads/zajeta", "name": "zajeta", "selectable": False},
    {"ref": "refs/heads/develop", "name": "develop", "selectable": True}]})
out["options"] = field("diffBaseOption", "it.ref")
click("diffBaseOption", 1)
out["retry"] = calls()[-1]
reply(last_tag(), dict(DIFF, **{"from": {"ref": "refs/heads/develop", "sha": "1" * 40, "date": ""}}))
out["header"] = texts("diffHeader")
out["errorGone"] = texts("diffError")
key(Qt.Key.Key_R)
out["refresh"] = calls()[-1]
''')
        self.assertIn("nieznana", out["errorText"][0])
        self.assertIn("Wybierz ref", out["errorText"][0])
        self.assertEqual(out["calls"][1][0], "listCardBranches")
        self.assertEqual(out["calls"][1][2], "K-36")
        self.assertEqual(json.loads(out["calls"][1][3]), {"purpose": "base", "limit": 100})
        self.assertEqual(out["options"], ["refs/heads/master", "refs/heads/develop"], "tylko wybieralne")
        self.assertEqual(out["retry"][0], "gitDiff")
        self.assertEqual(json.loads(out["retry"][2]), {"source": "card", "card": "K-36", "targetRef": "refs/heads/develop"})
        self.assertEqual(out["header"], ["K-36 · zmiany względem develop → card/k-36-git-tide-mapa-galezi"])
        self.assertEqual(out["errorGone"], [])
        self.assertEqual(json.loads(out["refresh"][2])["targetRef"], "refs/heads/develop", "odświeżenie zachowuje wybór")

    def test_names_from_git_are_plain_text_and_unicode_survives(self):
        out = self.probe('''
open_view()
reply(last_tag(), DIFF)
click("diffFile", 5)
out["names"] = field("diffFileName", "[it.text, it.textFormat]")
out["lines"] = field("diffLineText", "[it.text, it.textFormat]")
out["header"] = field("diffHeader", "it.textFormat")
out["statuses"] = field("diffFileStatus", "it.textFormat")
out["stats"] = field("diffFileStats", "it.textFormat")
out["totals"] = field("diffTotals", "it.textFormat")
reply_branch = {"ok": False, "error": "failed", "message": "<b>zły</b> ref"}
open_view()
reply(last_tag(), reply_branch)
out["errorText"] = field("diffError", "[it.text, it.textFormat]")
''')
        plain = 0  # Text.PlainText
        self.assertIn(["<b>zażółć</b>.txt", plain], out["names"])
        self.assertEqual(out["lines"], [["@@ -0,0 +0,1 @@", plain], ["<i>nie pogrubiaj</i> & <b>x</b>", plain]])
        for key_name in ("header", "statuses", "stats", "totals"):
            self.assertTrue(out[key_name] and all(v == plain for v in out[key_name]), key_name)
        self.assertEqual(out["errorText"][0][1], plain)
        self.assertIn("<b>zły</b> ref", out["errorText"][0][0])

    def test_empty_diff_says_so(self):
        out = self.probe('''
open_view()
reply(last_tag(), dict(DIFF, files=[], totals={"files": 0, "additions": 0, "deletions": 0}))
out["empty"] = texts("diffEmpty")
out["files"] = texts("diffFileName")
''')
        self.assertEqual(out["empty"], ["Brak zmian w tym zakresie."])
        self.assertEqual(out["files"], [])


class SourceTest(unittest.TestCase):
    def test_component_is_generic_apart_from_the_card_header(self):
        with open(VIEW_QML, encoding="utf-8") as fh:
            source = fh.read()
        code = re.sub(r"//.*", "", source)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", code), [], "kolory tylko z palety")
        self.assertEqual(re.findall(r"Qt\.rgba\(\s*[\d.]", code), [])
        self.assertEqual(re.findall(r'color:\s*"(?!transparent")[^"]*"', code), [])
        self.assertNotIn("Qt.color(", code)
        self.assertNotRegex(code, r"Repeater\s*\{[^}]*lineRows", "linie diffu tylko w ListView")
        self.assertRegex(code, r"ListView\s*\{\s*id:\s*lines")
        self.assertNotIn("RichText", code)
        self.assertNotIn("AutoText", code)
        self.assertNotRegex(code, r"textFormat:\s*Text\.(?!PlainText)")
        self.assertEqual(code.count("textFormat:"), len(re.findall(r"\bText \{", code)), "każdy Text ma jawny textFormat")

    def test_start_label_lives_in_one_file_and_diff_logic_has_no_card_code(self):
        with open(os.path.join(GIT_DIR, "diff.js"), encoding="utf-8") as fh:
            diff = fh.read()
        self.assertNotIn("function startLabel", diff)
        self.assertIn("Start.startLabel(", diff)


class PaletteTest(unittest.TestCase):
    def test_added_and_removed_tokens_come_from_the_palette_and_the_generator(self):
        for name in ("mist", "streamer"):
            palette = widget_stage.load_palette(name)
            for role in ("added", "removed"):
                self.assertIn(palette["semantic"][role], palette["pastel"], f"{name}: {role}")
            qml = widget_stage.palette_qml(palette)
            self.assertIn("readonly property color added:", qml)
            self.assertIn("readonly property color removed:", qml)
            self.assertIn(json.dumps(palette["pastel"][palette["semantic"]["added"]]), qml)


class PackageTest(unittest.TestCase):
    def test_component_is_staged_in_the_window_and_every_view(self):
        from daemon.tests.fakebin import files_under
        with tempfile.TemporaryDirectory(prefix="tide-diff-pkg-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            files = files_under(pkg)
        for name in ("qml/git/DiffView.qml", "qml/git/start.js", "qml/git/diff.js",
                     "views/board/qml/git/DiffView.qml", "views/board/qml/git/start.js",
                     "views/worktrees/qml/git/DiffView.qml"):
            self.assertIn(name, files)


class BackendCallTest(ProbeCase):
    def test_git_diff_slot_calls_the_git_interface_member(self):
        out = self.probe('''
request = '{"source":"card","card":"K-6"}'
backend.gitDiff("diff:1", request)
out["calls"] = calls()
''')
        self.assertEqual(out["calls"], [["Diff", '{"source":"card","card":"K-6"}']])


if __name__ == "__main__":
    unittest.main()
