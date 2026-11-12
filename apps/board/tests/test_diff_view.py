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
import "git/diff.js" as Diff

Window {
    id: root
    width: 1100
    height: 700
    visible: true
    property int closed: 0
    property var req: ({})
    property string path: ""
    property string work: ""
    readonly property var tokens: Tide.Tokens

    function label(from) { return Start.startLabel(from) }
    function cardLabel(flow) { return Start.cardStartLabel(flow) }
    function cardLine(flow) { return Start.cardStartLine(flow) }
    function cardHint(flow) { return Start.cardStartHint(flow) }
    function canChange(derived) { return Start.canChangeStart(derived) }
    function gapText(n) { return Diff.gapText(n) }
    // Wszystkie utworzone delegaty linii (także poza widokiem) — wirtualizacja ma ich trzymać garść.
    function delegates() {
        const count = it => (it.objectName === "diffLine" || it.objectName === "diffPair" ? 1 : 0)
                            + Array.from(it.children).reduce((sum, child) => sum + count(child), 0)
        return count(loader.item)
    }
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
        // Tryb diffa zapamiętany przez gospodarza (PT-350): `saved` zbiera zapisy, `diffMode` przeżywa ponowne otwarcie.
        property string diffMode: "unified"
        property var saved: []
        // Wyrzutnie Worktrees (PT-361): `open(pełnaŚcieżka, program)` zapisuje wywołania w `calls`.
        property string launchersJson: '{"terminal": true, "rider": true}'
        function open(path, app) { calls = calls.concat([["open", path, app]]) }
        function setDiffMode(value) { saved = saved.concat([value]); diffMode = value }
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
                workDir: root.work
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

    def probe(self, scenario: str, files=()) -> dict:
        """`files` — dodatkowe pliki doklejone do odpowiedzi `DIFF` (żeby nie zmieniać plików innych testów)."""
        script = os.path.join(self.tmp, "scenario.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(SCENARIO_HEAD + "\nDIFF = json.loads(" + repr(json.dumps(DIFF)) + ")\n"
                     + "DIFF['files'] = DIFF['files'] + json.loads(" + repr(json.dumps(list(files))) + ")\n"
                     + scenario)
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

    def test_card_start_sentence(self):
        """K-99, PT-389: drugie zdanie modułu startu — baza lokalna, zdalna, bez daty, bez SHA, nieznana, po korekcie."""
        out = self.probe('''
def flow(base, started="%(iso)s", **extra):
    timeline = [{"type": "step.started", "ts": "2026-11-09T12:00:00Z"}]
    if started is not None:
        timeline.insert(0, {"type": "card.started", "ts": started})
    return {"base": base, "timeline": timeline, **extra}
def known(ref, sha="%(sha)s"):
    return {"status": "known", "baseRef": ref, "baseBranch": ref, "baseCommit": sha}
def label(f):
    return js("cardLabel(" + json.dumps(f) + ")")
out["local"] = label(flow(known("refs/heads/master")))
out["remote"] = label(flow(known("refs/remotes/origin/develop")))
out["noDate"] = [label(flow(known("refs/heads/master"), started=None)), label(flow(known("refs/heads/master"), started="zła"))]
out["noSha"] = label(flow(known("refs/heads/master", "")))
out["bare"] = label(flow(known("refs/heads/master", ""), started=None))
out["unknown"] = [label(flow({"status": "unknown", "baseRef": "", "baseCommit": ""})), label({}), js("cardLabel(null)"),
                  label(flow(None)), label(flow({"status": "known", "baseRef": "", "baseCommit": "%(sha)s"}))]
out["line"] = [js("cardLine(" + json.dumps(flow(known("refs/heads/master"))) + ")"),
               js("cardLine(" + json.dumps(flow({"status": "unknown"})) + ")"),
               js("cardLine(" + json.dumps({"base": known("refs/heads/master"), "timeline": []}) + ")"),
               js("cardLine(null)")]
out["lastStart"] = label({"base": known("refs/heads/master"), "timeline": [
    {"type": "card.started", "ts": "2026-11-01T12:00:00Z"}, {"type": "card.started", "ts": "2026-11-07T12:00:00Z"}]})
# Po skutecznej korekcie bazy: nowa baza bez daty, pierwotna w podpowiedzi (z datą startu).
fixed = flow(known("refs/heads/topic", "%(new)s"), baseVersion=1, initialBase=known("refs/heads/master"))
out["corrected"] = [label(fixed), js("cardLine(" + json.dumps(fixed) + ")"), js("cardHint(" + json.dumps(fixed) + ")")]
out["hint"] = [js("cardHint(" + json.dumps(flow(known("refs/heads/master"), baseVersion=0)) + ")"), js("cardHint(null)")]
''' % {"iso": ISO, "sha": SHA, "new": "1234567" + "0" * 33})
        self.assertEqual(out["local"], "master (5.11, abcdef0)")
        self.assertEqual(out["remote"], "origin/develop (5.11, abcdef0)")
        self.assertEqual(out["noDate"], ["master (abcdef0)"] * 2)
        self.assertEqual(out["noSha"], "master (5.11)")
        self.assertEqual(out["bare"], "master")
        self.assertEqual(out["unknown"], ["nieznany"] * 5)
        self.assertEqual(out["line"], ["Start od: master (5.11, abcdef0)", "Start od: nieznany", "", ""])
        self.assertEqual(out["lastStart"], "master (7.11, abcdef0)")
        self.assertEqual(out["corrected"], ["topic (1234567)", "Start od: topic (1234567)",
                                            "Pierwotna baza: master (5.11, abcdef0)"])
        self.assertEqual(out["hint"], ["", ""])

    def test_change_start_only_on_card_without_work(self):
        out = self.probe('''
STARTED = [{"type": "card.started", "ts": "%(iso)s"}]
def derived(tickets=(), steps=(), timeline=STARTED, **flow):
    return {"flow": {"timeline": timeline, "steps": list(steps), **flow}, "tickets": [{"state": s} for s in tickets]}
def can(d):
    return js("canChange(" + json.dumps(d) + ")")
out["noWork"] = can(derived())
out["todoRows"] = can(derived(["todo", "todo"]))
out["notStarted"] = can(derived(timeline=[]))
out["inProgress"] = [can(derived([state, "todo"])) for state in ("w-toku", "review", "czeka", "gotowe", "odrzucone")]
out["step"] = can(derived(steps=[{"id": "x", "historical": False}]))
out["oldStep"] = can(derived(steps=[{"id": "x", "historical": True}]))
out["closed"] = can(derived(["todo"], closed=True))
out["merged"] = [can(derived(merged=True)), can(derived(mainMerged=True))]
out["none"] = [can(None), can({}), can({"flow": None})]
''' % {"iso": ISO})
        self.assertTrue(out["noWork"])
        self.assertTrue(out["todoRows"])
        self.assertTrue(out["oldStep"])
        self.assertFalse(out["notStarted"])
        self.assertEqual(out["inProgress"], [False] * 5)
        self.assertFalse(out["step"])
        self.assertFalse(out["closed"])
        self.assertEqual(out["merged"], [False, False])
        self.assertEqual(out["none"], [False] * 3)


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


BIG_SCENARIO = '''
SMALL = DIFF["files"][1]  # new.txt z hunkiem
LARGE = dict(SMALL, path="duzy.txt", additions=3500, large=True, lines=3500, hunks=[])
CUT = dict(SMALL, path="ucięty.txt", additions=40, truncated=True, hunks=[])
BIN = dict(SMALL, path="img.png", status="M", additions=0, binary=True, hunks=[], oldSize=30, newSize=1536)
BIN_NEW = dict(BIN, path="nowy.bin", status="A", oldSize=None, newSize=7)
BIG = dict(DIFF, truncated=True, warnings=["Diff przekracza limit"],
           files=[SMALL, LARGE, CUT, BIN, BIN_NEW], totals={"files": 5, "additions": 3541, "deletions": 0})


def loaded(path, rows):
    entry = dict(SMALL, path=path, additions=len(rows), hunks=[hunk(1, [line("+", None, i + 1, t) for i, t in enumerate(rows)])])
    return dict(DIFF, files=[entry], totals={"files": 1, "additions": len(rows), "deletions": 0}, path=path)


def hunk(start, lines):
    return {"oldStart": 0, "oldLines": 0, "newStart": start, "newLines": len(lines), "header": "", "lines": lines}


def line(kind, old, new, text):
    return {"kind": kind, "oldNo": old, "newNo": new, "text": text}


def options_of(call):
    return json.loads(call[2]).get("options")
'''


class BigDiffViewTest(QmlCase):
    """PT-355: plik `large`, binarny z rozmiarami i diff przycięty limitem całości."""

    def probe(self, scenario: str) -> dict:
        return super().probe(BIG_SCENARIO + scenario)

    def test_large_file_is_collapsed_and_click_loads_it_through_options_path(self):
        out = self.probe('''
open_view()
reply(last_tag(), BIG)
click("diffFile", 1)
out["selected"] = view("selectedPath")
out["note"] = texts("diffFileNote")
out["show"] = field("diffLargeShow", "it.label")
out["rows"] = len(field("diffLine", "1"))
out["callsBefore"] = len(calls())
click("diffLargeShow")
out["request"] = calls()[-1][2]
out["tag"] = last_tag()
out["reading"] = [texts("diffFileNote"), field("diffLargeShow", "1")]
reply(last_tag(), loaded("duzy.txt", ["pierwsza", "druga"]))
out["lines"] = field("diffLine", "[it.kind, it.content]")
out["after"] = [texts("diffFileNote"), field("diffLargeShow", "1"), view("selectedPath")]
out["names"] = texts("diffFileName")
out["stats"] = texts("diffFileStats")
click("diffFile", 0)
click("diffFile", 1)
out["noReload"] = len(calls())
''')
        self.assertEqual(out["selected"], "duzy.txt")
        self.assertEqual(out["note"], ["Plik jest duży — treść nie została wczytana."])
        self.assertEqual(out["show"], ["duży plik — 3500 linii, pokaż"])
        self.assertEqual((out["rows"], out["callsBefore"]), (0, 1), "sam wybór dużego pliku niczego nie wysyła")
        request = json.loads(out["request"])
        self.assertEqual(request, {"source": "card", "card": "K-36", "options": {"path": "duzy.txt"}})
        self.assertTrue(out["tag"].startswith("file:"))
        self.assertEqual(out["reading"], [["Doczytuję zmiany pliku…"], []])
        self.assertEqual(out["lines"][1:], [["+", "pierwsza"], ["+", "druga"]])
        self.assertEqual(out["after"], [[], [], "duzy.txt"])
        self.assertEqual(out["names"][:3], ["new.txt", "duzy.txt", "ucięty.txt"], "kolejność listy bez zmian")
        self.assertEqual(out["noReload"], 2, "doczytany plik nie jest pobierany ponownie (tylko diff i plik)")

    def test_truncated_diff_shows_the_bar_and_selecting_a_bodiless_file_loads_it(self):
        out = self.probe('''
open_view()
reply(last_tag(), DIFF)
out["barNormal"] = field("diffTruncatedBar", "1")
open_view()
reply(last_tag(), BIG)
out["bar"] = texts("diffTruncatedText")
out["names"] = texts("diffFileName")
click("diffFile", 2)
out["request"] = calls()[-1][2]
out["note"] = texts("diffFileNote")
reply(last_tag(), loaded("ucięty.txt", ["treść", "pliku"]))
out["lines"] = field("diffLine", "it.content")
out["barAfter"] = texts("diffTruncatedText")
out["calls"] = len(calls())
''')
        self.assertEqual(out["barNormal"], [], "bez ucięcia nie ma paska")
        self.assertEqual(out["bar"], ["Pokazano część zmian; wybierz plik, żeby doczytać"])
        self.assertEqual(len(out["names"]), 5, "lista plików pełna")
        self.assertEqual(json.loads(out["request"]), {"source": "card", "card": "K-36", "options": {"path": "ucięty.txt"}})
        self.assertEqual(out["note"], ["Doczytuję zmiany pliku…"])
        self.assertEqual(out["lines"][1:], ["treść", "pliku"])
        self.assertEqual(out["barAfter"], ["Pokazano część zmian; wybierz plik, żeby doczytać"], "reszta nadal ucięta")

    def test_load_error_is_shown_and_retry_asks_again(self):
        out = self.probe('''
open_view()
reply(last_tag(), BIG)
click("diffFile", 2)
reply(last_tag(), {"ok": False, "error": "timeout", "message": "git zbyt długo"})
out["note"] = texts("diffFileNote")
out["retry"] = field("diffRetryLoad", "it.label")
click("diffRetryLoad")
out["asked"] = [c[1][:5] for c in calls()]
reply(last_tag(), loaded("ucięty.txt", ["ok"]))
out["lines"] = field("diffLine", "it.content")
out["retryGone"] = field("diffRetryLoad", "1")
''')
        self.assertIn("Spróbuj ponownie", out["note"][0])
        self.assertEqual(out["retry"], ["Spróbuj ponownie"])
        self.assertEqual(out["asked"], ["diff:", "file:", "file:"])
        self.assertEqual(out["lines"][1:], ["ok"])
        self.assertEqual(out["retryGone"], [])

    def test_binary_files_show_sizes_before_and_after_without_content(self):
        out = self.probe('''
open_view()
reply(last_tag(), BIG)
click("diffFile", 3)
out["changed"] = [texts("diffFileNote"), len(field("diffLine", "1"))]
click("diffFile", 4)
out["added"] = texts("diffFileNote")
out["calls"] = len(calls())
''')
        self.assertEqual(out["changed"], [["plik binarny (30 B → 1,5 KB)"], 0])
        self.assertEqual(out["added"], ["plik binarny (— → 7 B)"])
        self.assertEqual(out["calls"], 1, "binarnego nie doczytuje się")

    def test_reload_drops_a_late_file_reply(self):
        out = self.probe('''
open_view()
reply(last_tag(), BIG)
click("diffFile", 2)
late = last_tag()
key(Qt.Key.Key_R)
reply(late, loaded("ucięty.txt", ["spóźnione"]))
out["rows"] = field("diffLine", "it.content")
reply(last_tag(), BIG)
out["afterReload"] = [view("selectedPath"), texts("diffFileNote")]
''')
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["afterReload"][0], "ucięty.txt")


# PT-350: tryb „obok siebie”, zwinięte fragmenty i rozwijanie kontekstu.
# Plik z nierówną liczbą usuniętych i dodanych linii w hunku: (x1,y1), (x2,∅), (x3,∅) i (z1,w1), (∅,w2).
PAIRS = file_entry("pairs.txt", "M", 3, 4, [hunk(10, [
    line(" ", 10, 10, "alfa"),
    line("-", 11, None, "x1"), line("-", 12, None, "x2"), line("-", 13, None, "x3"), line("+", None, 11, "<b>y1</b>"),
    line(" ", 14, 12, "beta"),
    line("-", 15, None, "z1"), line("+", None, 13, "w1"), line("+", None, 14, "w2"),
    line(" ", 16, 15, "gamma")])])
PAIR_FIELDS = "[it.leftKind, it.leftNo, it.leftContent, it.rightKind, it.rightNo, it.rightContent]"
PAIRED = [[" ", "10", "alfa", " ", "10", "alfa"],
          ["-", "11", "x1", "+", "11", "<b>y1</b>"],
          ["-", "12", "x2", "", "", ""],
          ["-", "13", "x3", "", "", ""],
          [" ", "14", "beta", " ", "12", "beta"],
          ["-", "15", "z1", "+", "13", "w1"],
          ["", "", "", "+", "14", "w2"],
          [" ", "16", "gamma", " ", "15", "gamma"]]

# Dwa hunki z przerwą 42 linii (oraz 3 linie ukryte przed pierwszym): „… 42 niezmienione linie”.
GAPS = file_entry("gaps.txt", "M", 2, 1, [
    hunk(4, [line(" ", 4, 4, "k4"), line(" ", 5, 5, "k5"), line(" ", 6, 6, "k6"), line("-", 7, None, "a"),
             line("+", None, 7, "b"), line(" ", 8, 8, "k8"), line(" ", 9, 9, "k9"), line(" ", 10, 10, "k10")]),
    hunk(53, [line(" ", 53, 53, "k53"), line("+", None, 54, "nowa"), line(" ", 54, 55, "k54")])])
# Odpowiedź na `options.context`: te same zmiany, hunki scalone (przerwa rozwinięta w linie „ukryta N”).
EXPANDED = file_entry("gaps.txt", "M", 2, 1, [
    hunk(4, [line(" ", 4, 4, "k4"), line(" ", 5, 5, "k5"), line(" ", 6, 6, "k6"), line("-", 7, None, "a"),
             line("+", None, 7, "b")]
            + [line(" ", n, n, "ukryta %d" % n) for n in range(8, 53)]
            + [line(" ", 53, 53, "k53"), line("+", None, 54, "nowa"), line(" ", 54, 55, "k54")])])
BIG = file_entry("big.txt", "M", 20, 0, [
    hunk(1 + i * 2000, [line("+", None, 1 + i * 2000 + n, "linia %d/%d" % (i, n)) for n in range(1000)])
    for i in range(20)])


class SideBySideTest(QmlCase):
    def test_u_switches_to_two_columns_with_removed_left_and_added_right(self):
        out = self.probe('''
open_view(path="pairs.txt")
reply(last_tag(), DIFF)
out["unifiedPairs"] = field("diffPair", "1")
out["unified"] = field("diffLine", "[it.kind, it.oldLabel, it.newLabel]")
key(Qt.Key.Key_U)
out["mode"] = view("mode")
out["pairs"] = field("diffPair", "%s")
out["unifiedLinesHidden"] = field("diffLine", "1")
out["hunk"] = field("diffSideHunk", "it.content")
out["oldNos"] = texts("diffSideOldNo")
out["newNos"] = texts("diffSideNewNo")
out["formats"] = field("diffSideNewText", "it.textFormat") + field("diffSideOldText", "it.textFormat")
out["oldX"] = field("diffSideOldText", "it.mapToItem(loader.item, 0, 0).x")
out["newX"] = field("diffSideNewText", "it.mapToItem(loader.item, 0, 0).x")
out["oldY"] = field("diffSideOldText", "it.mapToItem(loader.item, 0, 0).y")
out["newY"] = field("diffSideNewText", "it.mapToItem(loader.item, 0, 0).y")
out["active"] = [view("mode"), js("loader.item.activeList.objectName")]
key(Qt.Key.Key_U)
out["backMode"] = view("mode")
out["backPairs"] = field("diffPair", "1")
out["backLines"] = field("diffLine", "[it.kind, it.oldLabel, it.newLabel]")
''' % PAIR_FIELDS, files=[PAIRS])
        self.assertEqual(out["unifiedPairs"], [], "tryb jednolity nie ma par")
        self.assertEqual(out["mode"], "split")
        self.assertEqual(out["pairs"], PAIRED)
        self.assertEqual(out["unifiedLinesHidden"], [], "w trybie obok siebie lista jednolita jest ukryta")
        self.assertEqual(out["hunk"], ["@@ -10,7 +10,6 @@"])
        self.assertEqual(out["oldNos"], ["10", "11", "12", "13", "14", "15", "", "16"])
        self.assertEqual(out["newNos"], ["10", "11", "", "", "12", "13", "14", "15"])
        self.assertEqual(set(out["formats"]), {0}, "teksty plików jako Text.PlainText")
        for left, right in zip(out["oldX"], out["newX"]):
            self.assertLess(left, right, "usunięte po lewej, dodane po prawej")
        self.assertEqual(out["oldY"], out["newY"], "obie strony pary w tym samym wierszu")
        self.assertEqual(out["active"], ["split", "diffSideLines"])
        self.assertEqual(out["backMode"], "unified")
        self.assertEqual(out["backPairs"], [])
        self.assertEqual(out["backLines"], out["unified"], "powrót do trybu jednolitego daje te same wiersze")

    def test_header_switch_sets_the_mode_and_saves_it_for_the_window(self):
        out = self.probe('''
open_view()
reply(last_tag(), DIFF)
out["initial"] = [view("mode"), js("fake.saved.length"), len(field("diffModeSwitch", "1"))]
click("diffModeSplit")
out["split"] = [view("mode"), js("JSON.stringify(fake.saved)")]
click("diffModeUnified")
out["unified"] = [view("mode"), js("JSON.stringify(fake.saved)")]
key(Qt.Key.Key_U)
out["byKey"] = [view("mode"), js("JSON.stringify(fake.saved)"), js("fake.diffMode")]
''')
        self.assertEqual(out["initial"], ["unified", 0, 1], "otwarcie niczego nie zapisuje")
        self.assertEqual(out["split"], ["split", '["split"]'])
        self.assertEqual(out["unified"], ["unified", '["split","unified"]'])
        self.assertEqual(out["byKey"], ["split", '["split","unified","split"]', "split"])

    def test_chosen_mode_survives_reopening_the_view(self):
        out = self.probe('''
open_view(path="pairs.txt")
reply(last_tag(), DIFF)
key(Qt.Key.Key_U)
open_view(path="pairs.txt")
reply(last_tag(), DIFF)
out["reopened"] = view("mode")
out["pairs"] = field("diffPair", "%s")
out["savedAgain"] = js("fake.saved.length")
js("fake.diffMode = 'unified'")
wait(60)
out["followsHost"] = view("mode")
js("fake.diffMode = 'rubbish'")
wait(60)
out["ignoresRubbish"] = view("mode")
''' % PAIR_FIELDS, files=[PAIRS])
        self.assertEqual(out["reopened"], "split")
        self.assertEqual(out["pairs"], PAIRED)
        self.assertEqual(out["savedAgain"], 1, "ponowne otwarcie nie zapisuje drugi raz")
        self.assertEqual(out["followsHost"], "unified", "zmiana ustawienia w oknie dociera do otwartego widoku")
        self.assertEqual(out["ignoresRubbish"], "unified")

    def test_keys_of_the_view_work_in_split_mode(self):
        out = self.probe('''
from PyQt6.QtCore import Qt
M = Qt.KeyboardModifier
open_view()
reply(last_tag(), DIFF)
key(Qt.Key.Key_U)
out["start"] = [view("mode"), view("selectedPath"), view("hunkStarts.length"), view("currentHunk")]
key(Qt.Key.Key_N)
out["n"] = [view("currentHunk"), field("diffSideLines", "it.contentY")[0] > 0]
key(Qt.Key.Key_P)
out["p"] = [view("currentHunk"), field("diffSideLines", "it.contentY")[0]]
key(Qt.Key.Key_BracketRight)
out["next"] = [view("selectedPath"), view("mode")]
key(Qt.Key.Key_Down, M.AltModifier)
out["altDown"] = view("selectedPath")
key(Qt.Key.Key_BracketLeft)
out["prev"] = view("selectedPath")
out["pairsOfAdded"] = field("diffPair", "%s")
key(Qt.Key.Key_R)
out["reload"] = [[c[0], c[1]] for c in calls()]
key(Qt.Key.Key_Escape)
out["closed"] = js("closed")
''' % PAIR_FIELDS)
        self.assertEqual(out["start"], ["split", "apps/a.py", 2, 0])
        self.assertEqual(out["n"], [1, True])
        self.assertEqual(out["p"], [0, 0])
        self.assertEqual(out["next"], ["new.txt", "split"])
        self.assertEqual(out["altDown"], "gone.txt")
        self.assertEqual(out["prev"], "new.txt")
        self.assertEqual(out["pairsOfAdded"],[["", "", "", "+", "1", "pierwsza"]])
        self.assertEqual(out["reload"], [["gitDiff", "diff:1"], ["gitDiff", "diff:2"]])
        self.assertEqual(out["closed"], 1)

    def test_mode_switch_keeps_the_place_in_the_file(self):
        out = self.probe('''
open_view()
reply(last_tag(), DIFF)
TOP = "loader.item.activeList.indexAt(0, loader.item.activeList.contentY + 1)"
js("loader.item.activeList.positionViewAtIndex(40, ListView.Beginning)")
wait(80)
out["src"] = js("loader.item.topSource()")
key(Qt.Key.Key_U)
# wiersz trybu „obok siebie”, od którego zaczyna się ta sama linia pliku
out["target"] = js("(function () { const r = loader.item.activeRows; for (let i = 0; i < r.length; i++) "
                   "if (r[i].src >= %d) return i })()" % out["src"])
out["before"] = js("loader.item.activeRows[%d - 1].src" % out["target"])
out["splitTop"] = js(TOP)
out["splitSrc"] = js("loader.item.topSource()")
key(Qt.Key.Key_U)
out["backSrc"] = js("loader.item.topSource()")
''')
        self.assertGreater(out["src"], 30, "widok faktycznie przewinięto")
        self.assertLess(out["before"], out["src"], "wiersz docelowy to pierwszy, który nie leży przed miejscem")
        # ListView szacuje wysokości nieutworzonych wierszy, więc górny wiersz może różnić się o kilka od docelowego.
        self.assertLessEqual(abs(out["splitTop"] - out["target"]), 3)
        self.assertLessEqual(abs(out["splitSrc"] - out["src"]), 3, "po przełączeniu u góry ta sama linia pliku")
        self.assertLessEqual(abs(out["backSrc"] - out["src"]), 3)

    def test_pairing_is_computed_once_per_file_not_while_scrolling(self):
        with open(os.path.join(GIT_DIR, "DiffView.qml"), encoding="utf-8") as fh:
            code = re.sub(r"//.*", "", fh.read())
        self.assertEqual(code.count("Diff.sideRows("), 2, "tylko rebuildRows i przełączenie trybu")
        self.assertNotRegex(code, r"model:\s*Diff\.", "modele list to gotowe tablice, nie wywołania w wiązaniu")
        self.assertEqual(code.count("Repeater {"), 1, "Repeater tylko w liście refów bazy")

    def test_20000_lines_scroll_in_both_modes_with_a_bounded_number_of_delegates(self):
        out = self.probe('''
open_view(path="big.txt")
reply(last_tag(), DIFF)
def scroll_through():
    counts = [js("delegates()")]
    for y in (30000, 200000, 380000):
        js("loader.item.activeList.contentY = " + str(y))
        wait(80)
        counts.append(js("delegates()"))
    return counts
out["unifiedRows"] = js("loader.item.activeList.count")
out["unified"] = scroll_through()
key(Qt.Key.Key_U)
out["splitRows"] = js("loader.item.activeList.count")
out["split"] = scroll_through()
''', files=[BIG])
        # 20 hunków po 1000 linii: nagłówek + 1000 linii, a między hunkami zwinięty fragment (19).
        self.assertEqual(out["unifiedRows"], 20 * 1001 + 19)
        self.assertEqual(out["splitRows"], 20 * 1001 + 19, "same dodane linie: każda ma własny wiersz pary")
        for counts in (out["unified"], out["split"]):
            self.assertTrue(all(0 < n < 120 for n in counts), counts)

    def test_gap_text_uses_polish_plural_forms(self):
        out = self.probe('''
out["texts"] = [js("gapText(%d)" % n) for n in (1, 2, 5, 12, 22, 42)]
''')
        self.assertEqual(out["texts"], ["… 1 niezmieniona linia", "… 2 niezmienione linie", "… 5 niezmienionych linii",
                                        "… 12 niezmienionych linii", "… 22 niezmienione linie",
                                        "… 42 niezmienione linie"])


class ExpandContextTest(QmlCase):
    def test_collapsed_fragments_show_their_line_count_in_both_modes(self):
        out = self.probe('''
open_view(path="gaps.txt")
reply(last_tag(), DIFF)
out["unified"] = field("diffGap", "it.content")
out["unifiedLines"] = field("diffLine", "it.kind")
key(Qt.Key.Key_U)
out["split"] = field("diffGap", "it.content")
out["splitFirst"] = field("diffPair", "it.leftContent")[:3]
''', files=[GAPS])
        self.assertEqual(out["unified"], ["… 3 niezmienione linie", "… 42 niezmienione linie"])
        self.assertNotIn("gap", out["unifiedLines"], "zwinięty fragment nie jest zwykłą linią")
        self.assertEqual(out["split"], out["unified"])
        self.assertEqual(out["splitFirst"], ["k4", "k5", "k6"])

    def test_click_requests_one_file_with_a_larger_context_and_shows_the_lines(self):
        out = self.probe('''
open_view(path="gaps.txt")
reply(last_tag(), DIFF)
before = len(calls())
click("diffGap", 1)
out["calls"] = [json.loads(c[2]) for c in calls()[before:]]
out["during"] = view("loadingPath")
tag = last_tag()
expanded = dict(DIFF)
expanded["files"] = [json.loads(%s)]
reply(tag, expanded)
out["after"] = view("loadingPath")
out["gaps"] = field("diffGap", "it.content")
out["lines"] = field("diffLine", "it.content")
out["selected"] = view("selectedPath")
out["hunks"] = view("hunkStarts.length")
out["files"] = view("files.length")
''' % repr(json.dumps(EXPANDED)), files=[GAPS])
        self.assertEqual(out["calls"], [{"source": "card", "card": "K-36", "options": {"path": "gaps.txt", "context": 45}}])
        self.assertEqual(out["during"], "gaps.txt")
        self.assertEqual(out["after"], "")
        self.assertEqual(out["gaps"], ["… 3 niezmienione linie"], "rozwinięty fragment znika, drugi zostaje")
        self.assertIn("ukryta 8", out["lines"])
        self.assertIn("ukryta 20", out["lines"])
        self.assertEqual((out["selected"], out["hunks"], out["files"]), ("gaps.txt", 1, 7))

    def test_e_expands_the_fragments_of_the_selected_file_and_only_that_file(self):
        out = self.probe('''
open_view(path="gaps.txt")
reply(last_tag(), DIFF)
before = len(calls())
key(Qt.Key.Key_E)
out["calls"] = [json.loads(c[2]) for c in calls()[before:]]
key(Qt.Key.Key_E)
out["ignoredWhileLoading"] = len(calls()) - before
expanded = dict(DIFF)
expanded["files"] = [json.loads(%s)]
reply(last_tag(), expanded)
out["lines"] = field("diffLine", "it.content")
key(Qt.Key.Key_E)
out["second"] = json.loads(calls()[-1][2])["options"]
''' % repr(json.dumps(EXPANDED)), files=[GAPS])
        self.assertEqual(out["calls"], [{"source": "card", "card": "K-36", "options": {"path": "gaps.txt", "context": 45}}])
        self.assertEqual(out["ignoredWhileLoading"], 1, "drugie `e` w trakcie odczytu nie woła usługi")
        self.assertIn("ukryta 8", out["lines"])
        self.assertEqual(out["second"], {"path": "gaps.txt", "context": 48},
                         "kolejne rozwinięcie zaczyna od ostatnio użytego kontekstu")

    def test_e_without_collapsed_fragments_does_nothing(self):
        out = self.probe('''
open_view(path="new.txt")
reply(last_tag(), DIFF)
before = len(calls())
key(Qt.Key.Key_E)
out["calls"] = len(calls()) - before
''', files=[GAPS])
        self.assertEqual(out["calls"], 0)

    def test_expanding_works_in_split_mode_and_keeps_the_mode(self):
        out = self.probe('''
open_view(path="gaps.txt")
reply(last_tag(), DIFF)
key(Qt.Key.Key_U)
click("diffGap", 1)
out["call"] = json.loads(calls()[-1][2])["options"]
expanded = dict(DIFF)
expanded["files"] = [json.loads(%s)]
reply(last_tag(), expanded)
out["mode"] = view("mode")
out["gaps"] = field("diffGap", "it.content")
out["left"] = field("diffPair", "it.leftContent")
''' % repr(json.dumps(EXPANDED)), files=[GAPS])
        self.assertEqual(out["call"], {"path": "gaps.txt", "context": 45})
        self.assertEqual(out["mode"], "split")
        self.assertEqual(out["gaps"], ["… 3 niezmienione linie"])
        self.assertIn("ukryta 8", out["left"])

    def test_failed_expansion_keeps_the_lines_and_says_so(self):
        out = self.probe('''
open_view(path="gaps.txt")
reply(last_tag(), DIFF)
key(Qt.Key.Key_E)
reply(last_tag(), {"ok": False, "error": "timeout", "message": ""})
out["timeoutBar"] = texts("diffExpandErrorText")
out["gaps"] = field("diffGap", "it.content")
key(Qt.Key.Key_E)
empty = dict(DIFF)
empty["files"] = [json.loads(%s)]
reply(last_tag(), empty)
out["empty"] = texts("diffExpandErrorText")
out["after"] = field("diffGap", "it.content")
''' % repr(json.dumps(dict(EXPANDED, hunks=[]))), files=[GAPS])
        self.assertEqual(out["timeoutBar"], ["Git nie odpowiedział na czas. Spróbuj ponownie (r)."])
        self.assertEqual(out["gaps"], ["… 3 niezmienione linie", "… 42 niezmienione linie"])
        self.assertEqual(out["empty"], ["Nie udało się rozwinąć kontekstu tego pliku."])
        self.assertEqual(out["after"], out["gaps"])

    def test_reload_forgets_the_expanded_context(self):
        out = self.probe('''
open_view(path="gaps.txt")
reply(last_tag(), DIFF)
key(Qt.Key.Key_E)
expanded = dict(DIFF)
expanded["files"] = [json.loads(%s)]
reply(last_tag(), expanded)
out["contexts"] = view("contextByFile")
key(Qt.Key.Key_R)
reply(last_tag(), DIFF)
out["afterReload"] = [view("contextByFile"), field("diffGap", "it.content")]
key(Qt.Key.Key_E)
out["context"] = json.loads(calls()[-1][2])["options"]["context"]
''' % repr(json.dumps(EXPANDED)), files=[GAPS])
        self.assertEqual(list(out["contexts"].values()), [45])
        self.assertEqual(out["afterReload"], [{}, ["… 3 niezmienione linie", "… 42 niezmienione linie"]])
        self.assertEqual(out["context"], 45)


VISIBLE = ("(function () { const v = loader.item, l = v.activeList; "
           "return v.curRow >= l.indexAt(0, l.contentY + 1) && v.curRow <= l.indexAt(0, l.contentY + l.height - 1) })()")
CLIP = "QGuiApplication.clipboard()"


class LineNavigationTest(QmlCase):
    """PT-361: bieżąca linia, strony, ściągawka, zaznaczenie i kopiowanie."""

    def test_arrows_and_jk_move_the_current_line_and_keep_it_visible(self):
        out = self.probe('''
open_view()
reply(last_tag(), DIFF)
out["start"] = view("curRow")
key(Qt.Key.Key_Down)
out["down"] = view("curRow")
key(Qt.Key.Key_J)
out["j"] = view("curRow")
key(Qt.Key.Key_Up)
out["up"] = view("curRow")
key(Qt.Key.Key_K)
key(Qt.Key.Key_K)
out["top"] = view("curRow")
for _ in range(45):
    key(Qt.Key.Key_J)
out["far"] = [view("curRow"), js(%r), field("diffLines", "it.contentY")[0] > 0]
out["selection"] = [view("selFrom"), view("selTo")]
out["marks"] = len(field("diffCurrentMark", "1"))
out["file"] = view("selectedPath")
''' % VISIBLE)
        self.assertEqual([out["start"], out["down"], out["j"], out["up"], out["top"]], [0, 1, 2, 1, 0])
        self.assertEqual(out["far"], [45, True, True])
        self.assertEqual(out["selection"], [-1, -2], "samo przesuwanie nie zaznacza")
        self.assertEqual(out["marks"], 1, "jedna oznaczona bieżąca linia")
        self.assertEqual(out["file"], "apps/a.py", "strzałki nie zmieniają pliku")

    def test_page_keys_scroll_by_a_page_in_both_modes(self):
        out = self.probe('''
def pages():
    result = {}
    result["start"] = view("curRow")
    result["page"] = js("loader.item.pageRows()")
    key(Qt.Key.Key_PageDown)
    result["down"] = [view("curRow"), js(%r)]
    result["moved"] = field(view("activeList.objectName"), "it.contentY")[0]
    key(Qt.Key.Key_PageDown)
    result["down2"] = view("curRow")
    key(Qt.Key.Key_PageUp)
    result["up"] = view("curRow")
    return result
open_view()
reply(last_tag(), DIFF)
out["unified"] = pages()
for _ in range(3):
    key(Qt.Key.Key_PageUp)
out["backAtTop"] = view("curRow")
key(Qt.Key.Key_U)
out["split"] = pages()
''' % VISIBLE)
        for mode in ("unified", "split"):
            res = out[mode]
            self.assertGreater(res["page"], 5, mode)
            self.assertEqual(res["down"], [res["start"] + res["page"], True], mode)
            self.assertGreater(res["moved"], 0, mode)
            # wysokości wierszy bywają różne, więc strona liczona z widocznego okna może różnić się o jeden wiersz
            self.assertLessEqual(abs(res["down2"] - res["down"][0] - res["page"]), 1, mode)
            self.assertLessEqual(abs(res["up"] - res["down"][0]), 1, mode)

    def test_arrows_work_in_split_mode(self):
        out = self.probe('''
open_view()
reply(last_tag(), DIFF)
key(Qt.Key.Key_U)
for _ in range(3):
    key(Qt.Key.Key_Down)
out["down"] = view("curRow")
key(Qt.Key.Key_K)
out["k"] = view("curRow")
out["visible"] = js(%r)
''' % VISIBLE)
        self.assertEqual([out["down"], out["k"], out["visible"]], [3, 2, True])

    def test_question_mark_opens_the_cheat_sheet_and_esc_closes_it_before_the_view(self):
        out = self.probe('''
open_view()
reply(last_tag(), DIFF)
out["before"] = len(field("diffCheatSheet", "1"))
key(Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
out["open"] = [view("helpOpen"), len(field("diffCheatSheet", "1"))]
out["keys"] = field("diffHelpKeys", "it.text")
out["texts"] = field("diffHelpText", "it.text")
key(Qt.Key.Key_Down)
out["frozen"] = view("curRow")
key(Qt.Key.Key_Escape)
out["afterEsc"] = [view("helpOpen"), js("closed")]
key(Qt.Key.Key_Escape)
out["afterSecond"] = js("closed")
key(Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
key(Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
out["toggled"] = view("helpOpen")
''')
        self.assertEqual(out["before"], 0)
        self.assertEqual(out["open"], [True, 1])
        joined = " | ".join(out["keys"])
        for needed in ("] / [", "Alt+↓", "n / p", "r", "u", "e", "↓ / ↑", "j / k", "PgDn / PgUp", "Shift+↓", "Ctrl+C",
                       "Menu", "?", "Esc"):
            self.assertIn(needed, joined)
        self.assertEqual(len(out["keys"]), len(out["texts"]))
        self.assertEqual(out["frozen"], 0, "pod ściągawką klawisze nie ruszają widoku")
        self.assertEqual(out["afterEsc"], [False, 0], "pierwszy Esc zamyka tylko ściągawkę")
        self.assertEqual(out["afterSecond"], 1, "drugi Esc zamyka widok")
        self.assertFalse(out["toggled"])

    def test_shift_arrows_select_and_ctrl_c_copies_only_the_text(self):
        out = self.probe('''
M = Qt.KeyboardModifier
open_view()
reply(last_tag(), DIFF)
%s.setText("")
key(Qt.Key.Key_C, M.ControlModifier)
out["single"] = %s.text()
for _ in range(30):
    key(Qt.Key.Key_Down)
key(Qt.Key.Key_Down, M.ShiftModifier)
key(Qt.Key.Key_Down, M.ShiftModifier)
out["range"] = [view("selFrom"), view("selTo"), view("anchorRow"), view("curRow")]
out["shown"] = len(field("diffSelection", "1"))
key(Qt.Key.Key_C, M.ControlModifier)
out["copied"] = %s.text()
key(Qt.Key.Key_Up, M.ShiftModifier)
out["shrunk"] = [view("selFrom"), view("selTo")]
key(Qt.Key.Key_Down)
out["cleared"] = [view("selFrom"), view("selTo")]
%s.setText("")
key(Qt.Key.Key_Up, M.ShiftModifier)
key(Qt.Key.Key_Up, M.ShiftModifier)
key(Qt.Key.Key_Up, M.ShiftModifier)
out["upward"] = [view("selFrom"), view("selTo")]
key(Qt.Key.Key_C, M.ControlModifier)
out["copiedUp"] = %s.text()
''' % ((CLIP,) * 5))
        # bez zaznaczenia kopiuje się bieżąca linia — tu nagłówek hunka, czyli nic
        self.assertEqual(out["single"], "")
        self.assertEqual(out["range"], [30, 32, 30, 32])
        self.assertEqual(out["shown"], 3)
        self.assertEqual(out["copied"], "kontekst 30\nstara linia\nnowa linia")
        self.assertEqual(out["shrunk"], [30, 31])
        self.assertEqual(out["cleared"], [-1, -2])
        self.assertEqual(out["upward"], [29, 32], "kotwica to bieżąca linia z chwili pierwszego Shift")
        self.assertEqual(out["copiedUp"], "kontekst 29\nkontekst 30\nstara linia\nnowa linia")

    def test_click_and_shift_click_select_a_range_and_the_header_is_not_copied(self):
        out = self.probe('''
M = Qt.KeyboardModifier
open_view()
reply(last_tag(), DIFF)
%s.setText("")
click("diffLine", 0)
out["click"] = [view("curRow"), view("anchorRow")]
click("diffLine", 3, M.ShiftModifier)
out["shift"] = [view("selFrom"), view("selTo")]
key(Qt.Key.Key_C, M.ControlModifier)
out["copied"] = %s.text()
click("diffLine", 6)
out["reselect"] = [view("selFrom"), view("selTo")]
out["focus"] = view("activeFocus")
''' % (CLIP, CLIP))
        self.assertEqual(out["click"], [0, 0], "nagłówek hunka też da się zaznaczyć")
        self.assertEqual(out["shift"], [0, 3])
        self.assertEqual(out["copied"], "kontekst 1\nkontekst 2\nkontekst 3", "bez nagłówka hunka")
        self.assertEqual(out["reselect"][0], out["reselect"][1])
        self.assertTrue(out["focus"], "klik w linię zostawia klawiaturę widokowi")

    def test_split_mode_copies_one_side_the_one_in_the_selection(self):
        out = self.probe('''
M = Qt.KeyboardModifier
open_view()
reply(last_tag(), DIFF)
key(Qt.Key.Key_U)
for _ in range(30):
    key(Qt.Key.Key_Down)
key(Qt.Key.Key_Down, M.ShiftModifier)
out["rows"] = js("JSON.stringify(loader.item.activeRows.slice(30, 32).map(r => r.kind))")
out["side"] = view("selSide")
key(Qt.Key.Key_C, M.ControlModifier)
out["right"] = %s.text()
key(Qt.Key.Key_Left)
key(Qt.Key.Key_C, M.ControlModifier)
out["left"] = %s.text()
out["shown"] = len(field("diffSelection", "1"))
''' % (CLIP, CLIP))
        self.assertEqual(out["side"], "right")
        self.assertEqual(out["right"], "kontekst 30\nnowa linia")
        self.assertEqual(out["left"], "kontekst 30\nstara linia")
        self.assertEqual(out["shown"], 2)

    def test_selection_is_a_range_and_survives_20000_lines_without_extra_delegates(self):
        out = self.probe('''
M = Qt.KeyboardModifier
open_view(path="big.txt")
reply(last_tag(), DIFF)
%s.setText("")
key(Qt.Key.Key_Down)
counts = [js("delegates()")]
for _ in range(6):
    key(Qt.Key.Key_PageDown, M.ShiftModifier)
    counts.append(js("delegates()"))
out["counts"] = counts
out["range"] = [view("selFrom"), view("selTo"), view("curRow")]
key(Qt.Key.Key_C, M.ControlModifier)
out["copied"] = %s.text().count("\\n") + 1
out["visible"] = js(%r)
''' % (CLIP, CLIP, VISIBLE), files=[BIG])
        self.assertTrue(all(0 < n < 120 for n in out["counts"]), out["counts"])
        self.assertEqual(out["range"][2], out["range"][1])
        self.assertEqual(out["copied"], out["range"][1] - out["range"][0] + 1, "każdy wiersz zakresu to jedna dodana linia")
        self.assertTrue(out["visible"])

    def test_selection_is_cleared_when_the_file_changes(self):
        out = self.probe('''
M = Qt.KeyboardModifier
open_view()
reply(last_tag(), DIFF)
key(Qt.Key.Key_Down, M.ShiftModifier)
out["selected"] = [view("selFrom"), view("selTo")]
key(Qt.Key.Key_BracketRight)
out["after"] = [view("selFrom"), view("selTo"), view("curRow")]
''')
        self.assertEqual(out["selected"], [0, 1])
        self.assertEqual(out["after"], [-1, -2, 0])


class FileMenuTest(QmlCase):
    def menu(self, request, work="", file_index=0, extra=""):
        return self.probe('''
open_view(%s, "")
%s
reply(last_tag(), DIFF)
%s.setText("")
click("diffFile", %d, button=Qt.MouseButton.RightButton)
out["menu"] = len(field("diffFileMenu", "1"))
out["items"] = [field("diffCopyPath", "it.label"), field("diffOpenEditor", "it.label")]
out["calls"] = [c for c in calls() if c[0] == "open"]
''' % (json.dumps(request), "js(%s)" % json.dumps("root.work = " + json.dumps(work)) if work else "", CLIP,
       file_index) + extra)

    def test_copy_path_puts_the_file_path_on_the_clipboard(self):
        out = self.menu(REQUEST, extra='''
click("diffCopyPath")
out["clip"] = %s.text()
out["closed"] = len(field("diffFileMenu", "1"))
''' % CLIP)
        self.assertEqual(out["menu"], 1)
        self.assertEqual(out["items"][0], ["Kopiuj ścieżkę"])
        self.assertEqual(out["clip"], "apps/a.py")
        self.assertEqual(out["closed"], 0, "wybór pozycji zamyka menu")

    def test_copy_path_of_a_renamed_file_is_the_new_path(self):
        out = self.menu(REQUEST, file_index=3, extra='''
click("diffCopyPath")
out["clip"] = %s.text()
''' % CLIP)
        self.assertEqual(out["clip"], "b/nowa-nazwa.py")

    def test_menu_key_opens_the_menu_of_the_selected_file_and_esc_closes_it_first(self):
        out = self.probe('''
open_view()
reply(last_tag(), DIFF)
key(Qt.Key.Key_Menu)
out["open"] = len(field("diffFileMenu", "1"))
key(Qt.Key.Key_Escape)
out["afterEsc"] = [len(field("diffFileMenu", "1")), js("closed")]
''')
        self.assertEqual(out["open"], 1)
        self.assertEqual(out["afterEsc"], [0, 0])

    def test_open_in_editor_calls_the_launcher_with_the_full_path_for_a_worktree_source(self):
        out = self.menu({"source": "worktree", "path": "/work/tree"}, extra='''
click("diffOpenEditor")
out["after"] = [c for c in calls() if c[0] == "open"]
''')
        self.assertEqual(out["items"][1], ["Otwórz w edytorze"])
        self.assertEqual(out["calls"], [])
        self.assertEqual(out["after"], [["open", "/work/tree/apps/a.py", "rider"]])

    def test_open_in_editor_for_a_card_needs_the_card_worktree_from_the_host(self):
        with_work = self.menu(REQUEST, work="/work/card-wt/", extra='''
click("diffOpenEditor")
out["after"] = [c for c in calls() if c[0] == "open"]
''')
        self.assertEqual(with_work["after"], [["open", "/work/card-wt/apps/a.py", "rider"]], "bez podwójnego ukośnika")
        without = self.menu(REQUEST)
        self.assertEqual(without["items"][1], [], "karta bez znanego katalogu roboczego nie ma pozycji")

    def test_open_in_editor_is_hidden_without_a_working_directory(self):
        for request in ({"source": "commit", "repo": "/work/r", "sha": SHA},
                        {"source": "range", "repo": "/work/r", "from": "a", "to": "b"}):
            out = self.menu(request, work="/work/r")
            self.assertEqual(out["menu"], 1, request)
            self.assertEqual(out["items"], [["Kopiuj ścieżkę"], []], request)

    def test_open_in_editor_is_hidden_for_deleted_files_and_without_the_launcher(self):
        deleted = self.menu({"source": "worktree", "path": "/work/tree"}, file_index=2)
        self.assertEqual(deleted["items"][1], [], "usunięty plik nie ma czego otworzyć")
        out = self.probe('''
js("fake.launchersJson = '{\\"terminal\\": true, \\"rider\\": false}'")
open_view({"source": "worktree", "path": "/work/tree"}, "")
reply(last_tag(), DIFF)
click("diffFile", 0, button=Qt.MouseButton.RightButton)
out["items"] = field("diffOpenEditor", "it.label")
out["copy"] = field("diffCopyPath", "it.label")
''')
        self.assertEqual(out["items"], [])
        self.assertEqual(out["copy"], ["Kopiuj ścieżkę"])

    def test_open_in_editor_refuses_paths_from_git_that_leave_the_directory(self):
        out = self.probe('''
open_view({"source": "worktree", "path": "/work/tree"}, "")
reply(last_tag(), DIFF)
out["paths"] = [js("loader.item.editorPath({path: %s, status: 'M'})" % json.dumps(p))
                for p in ("../x", "a/../../x", "/etc/passwd", "", "a/b.txt")]
''')
        self.assertEqual(out["paths"], ["", "", "", "", "/work/tree/a/b.txt"])


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
                     "qml/git/SyntaxText.qml", "qml/git/ModuleHighlighter.qml",
                     "views/board/qml/git/DiffView.qml", "views/board/qml/git/start.js",
                     "views/board/qml/git/SyntaxText.qml", "views/board/qml/git/ModuleHighlighter.qml",
                     "views/worktrees/qml/git/DiffView.qml", "views/worktrees/qml/git/SyntaxText.qml",
                     "views/worktrees/qml/git/ModuleHighlighter.qml"):
            self.assertIn(name, files)


class BackendCallTest(ProbeCase):
    def test_git_diff_slot_calls_the_git_interface_member(self):
        out = self.probe('''
request = '{"source":"card","card":"K-6"}'
backend.gitDiff("diff:1", request)
out["calls"] = calls()
''')
        self.assertEqual(out["calls"], [["Diff", '{"source":"card","card":"K-6"}']])


class BackendEditorTest(unittest.TestCase):
    """PT-361: backend Tablicy woła wyrzutnię Worktrees (`Open`, `Launchers`) i zna worktree gałęzi karty."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def make(self, worktrees):
        from apps.board import backend as board_backend
        snapshot = {"cards": [{"id": "K-1", "derived": {"flow": {"cardBranch": "card/k-1"}}},
                              {"id": "K-2", "derived": {"flow": {}}}]}
        be = board_backend.Backend(None, snapshot)
        be._preview, be.bus, be.sent, be.replies = False, object(), [], []
        be._launchers = board_backend.NO_LAUNCHERS

        def call(member, args, then, timeout=0, path="", iface=""):
            be.sent.append([path, iface, member, *args])
            if then:
                then(True, json.dumps(worktrees) if member == "List" else '{"terminal": true, "rider": true}')
        be._call = call
        be.replied.connect(lambda tag, text: be.replies.append([tag, json.loads(text)]))
        return be

    def test_open_goes_to_the_worktrees_launcher(self):
        be = self.make([])
        be.open("/wt/src/a.py", "rider")
        self.assertEqual(be.sent, [["/Worktrees", "org.tide.Daemon.Worktrees", "Open", "/wt/src/a.py", "rider"]])

    def test_launchers_are_read_from_worktrees(self):
        be = self.make([])
        self.assertEqual(json.loads(be.launchersJson)["rider"], False)
        be._refresh_launchers()
        self.assertEqual(json.loads(be.launchersJson)["rider"], True)

    def test_card_worktree_is_found_by_the_card_branch(self):
        be = self.make([{"branch": "card/k-9", "path": "/wt/9", "missing": False},
                        {"branch": "card/k-1", "path": "/wt/gone", "missing": True},
                        {"branch": "card/k-1", "path": "/wt/1", "missing": False}])
        be.cardWorktree("t1", "K-1")
        self.assertEqual(be.replies, [["t1", {"ok": True, "path": "/wt/1"}]])

    def test_card_without_branch_or_worktree_gets_no_directory(self):
        be = self.make([{"branch": "card/k-1", "path": "/wt/1", "missing": True}])
        be.cardWorktree("t1", "K-2")
        be.cardWorktree("t2", "K-1")
        be.cardWorktree("t3", "K-404")
        self.assertEqual([r[1]["ok"] for r in be.replies], [False, False, False])
        self.assertEqual([c[2] for c in be.sent], ["List"], "bez gałęzi karty usługa nie jest pytana")


if __name__ == "__main__":
    unittest.main()
