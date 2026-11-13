"""Okno Worktrees, „Scal…” z mapy gałęzi (K-99, PT-395): wspólne okno scalania (apps/common/qml/git/MergeWindow.qml)
osadzone w trybie „Gałęzie”.

- logika (branches.js, qml6): kiedy „Scal…”, baza i „Start od: …” z węzła mapy, teksty odmowy i sukcesu,
- okno na atrapie backendu w QML (sonda apps/board/tests/diffprobe.py): „Scal…” tylko przy domkniętej, niescalonej
  karcie; `PreviewCardMerge` dla domyślnego celu i `MergeCard` wyłącznie po „Scal do <cel>” z tokenem bieżącego
  podglądu; zamknięcie, Escape, „Odśwież”, odświeżenie mapy i zmiana projektu nie scalają; stary podgląd (inny tag) nie
  odblokowuje przycisku; odmowa `MergeCard` pokazuje powód, blokuje „Scal do” do nowego podglądu i nie wysyła drugiego
  `MergeCard`; po sukcesie mapa czyta się od nowa, a węzeł ma „scalona do <cel>”,
- backend: lista dozwolonych wywołań `/Board` (dokładnie `ListCardBranches`, `PreviewCardMerge`, `MergeCard`), odmowa
  innych nazw bez wywołania usługi, podgląd z pliku i brak usługi bez wywołań; na prywatnej magistrali z atrapą usługi
  (`/Board`, `/Git`, `/Worktrees`) — w procesie (backend sam) i w oknie offscreen z prawdziwym backendem
  (sonda mergeprobe.py): pełna droga z mapy, historia 37 (repozytorium bez `master` i `origin`, główna `main` albo
  `develop`), odmowy `stale-preview` i sukces `already` z drugiego okna.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import APP_DIR, REPO
from .test_branches import PROJECTS, card, ticket
from .test_diff_actions import PROBE, rows
from .test_window import QML_PROBLEMS, load_launcher, stage
from daemon.tests.privatebus import PrivateBus, requires_dbus, wait_until

MERGE_PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mergeprobe.py")

HARNESS = '''
import QtQuick
import QtQuick.Window

Window {
    id: root
    width: 1180
    height: 820
    visible: true

    QtObject {
        id: fake
        property string listJson: %(list)s
        property bool available: true
        property string launchersJson: '{"terminal": true, "rider": false}'
        property string homeDir: "/w"
        property string viewMode: "branches"
        property string branchProject: "alfa"
        property var calls: []
        signal replied(string tag, string json)
        signal branchMapReplied(string tag, string json)
        signal boardReplied(string tag, string json)
        signal boardChanged()
        signal removeFinished(string text)
        signal pruneFinished(string text)
        signal fetchFinished(string repoPath, string text)
        signal pullFinished(string path, string text)
        function add(entry) { calls = calls.concat([entry]) }
        function gitDiff(tag, request) { add(["Diff", tag, request]) }
        function branchMap(tag, request) { add(["BranchMap", tag, request]) }
        function listCardBranches(tag, card, request) { add(["ListCardBranches", tag, card, request]) }
        function previewCardMerge(tag, card, request) { add(["PreviewCardMerge", tag, card, request]) }
        function mergeCard(tag, card, request) { add(["MergeCard", tag, card, request]) }
        function setViewMode(value) { viewMode = value; return "" }
        function setBranchProject(slug) { add(["setBranchProject", slug]); branchProject = slug; return "" }
        function remove(paths) {}
        function removeForced(paths, deleteBranches, confirmed) {}
        function open(path, app) {}
        function refresh() {}
        function prune(repo) {}
        function fetch(repo) {}
        function pull(path) {}
    }
    WorktreesView {
        id: win
        anchors.fill: parent
        backend: fake
    }
    QtObject { id: loader; property var item: win }
}
'''

# Wspólne pomocnicze scenariuszy (obie sondy: diffprobe.py z atrapą w QML i mergeprobe.py z prawdziwym backendem).
HELPERS = r'''
def titles():
    return field("branchNode", "it.titleText")

def states():
    return dict(zip(titles(), field("branchNode", "it.detailText")))

def select(prefix):
    """Klik w węzeł mapy, którego tytuł zaczyna się od `prefix` (np. "K-7 ")."""
    names = titles()
    index = next(i for i, t in enumerate(names) if t.startswith(prefix))
    click("branchNode", index)

def item(name):
    """Tekst, widoczność i aktywność elementu po objectName (także niewidocznego); None — brak elementu."""
    text = js("(function () { const all = " + ALL.replace("it.visible", "true") + "; "
              "const it = all(loader.item, " + json.dumps(name) + ")[0]; return it ? JSON.stringify("
              "{text: it.text, visible: it.visible, enabled: it.enabled}) : '' })()")
    return json.loads(text) if text else None

def page(expr):
    return js("(" + ALL.replace("it.visible", "true") + ")(loader.item, 'branchesPage')[0]." + expr)

def opened():
    return bool(page("mergeWindow.opened"))

def merge_enabled():
    found = item("mergeWindowMerge")
    return bool(found and found["visible"] and found["enabled"])

def shortcuts():
    texts = []
    for i in range(10):
        found = field("mergeTargetShortcut" + str(i), "it.children[0].text")
        if not found:
            break
        texts.append(found[0])
    return texts

def window_texts():
    """Widoczne teksty okna scalania (bez mapy pod spodem)."""
    return json.loads(js("(function () { const out = []; const w = (" + ALL + ")(loader.item, 'mergeWindow')[0]; "
                         "(function walk(it) { if (!it || !it.visible) return; "
                         "if (typeof it.text === 'string') out.push(it.text); "
                         "for (const c of it.children) walk(c) })(w); return JSON.stringify(out) })()"))
'''

QML_HELPERS = r'''
def answer(tag, payload):
    js("fake.branchMapReplied(" + json.dumps(tag) + ", " + json.dumps(json.dumps(payload)) + ")")
    wait(80)

def board(tag, payload):
    js("fake.boardReplied(" + json.dumps(tag) + ", " + json.dumps(json.dumps(payload)) + ")")
    wait(80)

def of(kind):
    return [c for c in calls() if c[0] == kind]

def last(kind):
    return of(kind)[-1]

def show(tree):
    answer("projects:1", PROJECTS)
    answer(of("BranchMap")[-1][1], tree)

def open_merge(prefix="K-7 "):
    select(prefix)
    click("branchMerge")
    wait(80)

def list_branches(reply):
    board(last("ListCardBranches")[1], reply)

def preview(reply):
    board(last("PreviewCardMerge")[1], reply)
'''

PREVIEW = {"ok": True, "canMerge": True, "previewToken": "tok-1", "targetRef": "refs/heads/main",
           "targetBranch": "main", "sourceRef": "refs/heads/card/k-7", "sourceCommit": "a" * 40,
           "targetCommit": "b" * 40, "resultTree": "c" * 40, "targetWorktree": "/r/alfa", "targetWorktreeExists": True,
           "commits": [], "commitCount": 0, "files": [], "fileCount": 0, "conflicts": [], "conflictCount": 0,
           "blockedReasons": [], "warnings": []}


def branch(name, selectable=True):
    return {"ref": "refs/heads/" + name, "name": name, "commit": "a" * 40, "kind": "local", "selectable": selectable,
            "reason": ""}


def listing(main="main", default=True, extra=("feature/a",)):
    """`ListCardBranches` (purpose merge) repozytorium bez gałęzi zdalnych: główna `main`/`develop`, bez `master`."""
    ref = "refs/heads/" + main
    return {"ok": True, "branches": [branch(main)] + [branch(n) for n in extra], "recentTargets": [],
            "defaultTargetRef": ref if default else "", "mainRef": ref, "trackingTargets": [], "createTarget": None,
            "warnings": [], "hasMore": False}


def merge_tree(main="main", closed_base=True, merged=False):
    """Mapa projektu bez `master` i `origin`: baza `main`/`develop` z kartami K-7 (domknięta albo scalona), K-11 (w toku),
    K-10 (scalona) oraz grupa „baza nieznana” z kartą K-5 (domknięta)."""
    base = "refs/heads/" + main
    k7 = card(7, "Domknięta karta", "merged" if merged else "closed", "done" if merged else "review",
              ref=base if closed_base else "")
    if merged:
        k7["merged"] = {"targetRef": base, "sha": "e" * 40, "date": "2026-11-13T09:30:00+01:00"}
    nodes = [
        {"id": "base:" + base, "kind": "base", "ref": base, "branch": main, "state": "active"},
        {"id": "unknown-base", "kind": "unknown-base", "ref": "", "branch": "", "title": "Nieznana baza",
         "state": "active"},
        k7,
        ticket(7, 70, "Ticket karty", "merged",
               merged={"targetRef": "refs/heads/card/k-7", "sha": "d" * 40, "date": "2026-11-12T09:30:00+01:00"}),
        card(11, "Aktywna karta", "active", "progress", ref=base),
        card(10, "Scalona karta", "merged", "done", ref=base,
             merged={"targetRef": base, "sha": "c" * 40, "date": "2026-11-11T09:30:00+01:00"}),
        card(5, "Stara karta", "closed", "archived", ref=""),
    ]
    parent = "base:" + base if closed_base else "unknown-base"
    edges = [
        {"kind": "start", "from": parent, "to": "card:K-7"},
        {"kind": "start", "from": "card:K-7", "to": "ticket:K-7:PT-70"},
        {"kind": "start", "from": "base:" + base, "to": "card:K-11"},
        {"kind": "start", "from": "base:" + base, "to": "card:K-10"},
        {"kind": "start", "from": "unknown-base", "to": "card:K-5"},
    ]
    return {"ok": True, "project": "alfa", "repo": "/r/alfa", "nodes": nodes, "edges": edges, "total": 5,
            "truncated": False, "warnings": []}


def preamble() -> str:
    return ("PROJECTS = json.loads(%r)\nTREE = json.loads(%r)\nPREVIEW = json.loads(%r)\n"
            % (json.dumps(PROJECTS), json.dumps(merge_tree()), json.dumps(PREVIEW)))


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class LogicTest(unittest.TestCase):
    """branches.js: dane okna scalania z węzła mapy — bez okna."""

    def run_logic(self, body: str):
        tmp = tempfile.mkdtemp(prefix="tide-wt-merge-logic-")
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copy(os.path.join(REPO, "apps", "worktrees", "qml", "branches.js"), tmp)
        os.mkdir(os.path.join(tmp, "git"))
        shutil.copy(os.path.join(REPO, "apps", "common", "qml", "git", "start.js"), os.path.join(tmp, "git"))
        path = os.path.join(tmp, "Probe.qml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('''import QtQuick
import "branches.js" as B
Item {
    Component.onCompleted: {
        const tree = %s
        const byId = id => tree.nodes.find(n => n.id === id)
        console.warn("OUT " + JSON.stringify(%s))
        Qt.quit()
    }
}
''' % (json.dumps(merge_tree()), body))
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ="Europe/Warsaw")
        log = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60).stderr
        match = re.search(r"OUT (.*)$", log, re.M)
        self.assertIsNotNone(match, log[-2000:])
        return json.loads(match.group(1))

    def test_merge_only_for_a_closed_unmerged_card(self):
        out = self.run_logic('tree.nodes.map(n => [n.id, B.canMerge(n)]).concat([["null", B.canMerge(null)], '
                             '["bez karty", B.canMerge({kind: "card", state: "closed"})]])')
        self.assertEqual(dict(out), {"base:refs/heads/main": False, "unknown-base": False, "card:K-7": True,
                                     "ticket:K-7:PT-70": False, "card:K-11": False, "card:K-10": False,
                                     "card:K-5": True, "null": False, "bez karty": False})

    def test_base_and_start_line_come_from_the_node(self):
        out = self.run_logic('[B.cardBaseRef(byId("card:K-7")), B.cardBaseRef(byId("card:K-5")), '
                             'B.cardStartLine(byId("card:K-7")), B.cardStartLine(byId("card:K-5")), '
                             'B.cardStartLine({kind: "card", started: {ref: "refs/heads/develop", sha: "1234567890", '
                             'date: "2026-11-05T10:00:00+01:00", initial: {ref: "refs/heads/main", sha: "abc"}}}), '
                             'B.cardStartLine({kind: "card"})]')
        self.assertEqual(out, ["refs/heads/main", "", "Start od: main (1.11, aaaaaaa)", "Start od: nieznany",
                               "Start od: develop (1234567)", ""])

    def test_refusal_and_success_texts(self):
        out = self.run_logic('[B.mergeErrorText({ok: true}), B.mergeErrorText({ok: false, error: "stale-preview", '
                             'message: "Podgląd jest nieaktualny."}), B.mergeErrorText({ok: false, error: "x"}), '
                             'B.mergeErrorText(null), B.mergedText("Scalono K-7 do main", {ok: true}), '
                             'B.mergedText("Scalono K-7 do main", {ok: true, already: true}), B.mergedText("", {})]')
        self.assertEqual(out, ["", "Podgląd jest nieaktualny.", "Usługa odmówiła (x).",
                               "Usługa tide-daemon nie odpowiedziała — spróbuj jeszcze raz.", "Scalono K-7 do main.",
                               "Scalono K-7 do main — gałąź karty była już w celu, bez nowego commita.", ""])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class WindowTest(unittest.TestCase):
    """Okno Worktrees z atrapą backendu w QML: odpowiedzi podaje scenariusz (także spóźnione i ze starym tagiem)."""

    def probe(self, scenario: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="tide-wt-merge-win-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            qml = os.path.join(pkg, "views", "worktrees", "qml")
            with open(os.path.join(qml, "Harness.qml"), "w", encoding="utf-8") as fh:
                fh.write(HARNESS % {"list": json.dumps(json.dumps(rows(), ensure_ascii=False))})
            script = os.path.join(tmp, "scenario.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(preamble() + HELPERS + QML_HELPERS + scenario)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ="UTC")
            result = subprocess.run(["/usr/bin/python3", PROBE, qml, script], env=env, capture_output=True,
                                    text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", out, out)
        return out

    def test_merge_button_only_for_a_closed_unmerged_card(self):
        out = self.probe('''
show(TREE)
out["none"] = len(field("branchMerge", "it.text"))
seen = {}
for prefix in ("K-7 ", "K-11 ", "K-10 ", "K-5 "):
    select(prefix)
    seen[prefix.strip()] = field("branchMerge", "it.text")
    select(prefix)   # drugi klik zdejmuje zaznaczenie
select("PT-70 ")     # ticket nie jest zaznaczany
seen["PT-70"] = field("branchMerge", "it.text")
out["seen"] = seen
''')
        self.assertEqual(out["none"], 0, "bez zaznaczenia nie ma „Scal…”")
        self.assertEqual(out["seen"], {"K-7": ["Scal…"], "K-11": [], "K-10": [], "K-5": ["Scal…"], "PT-70": []})

    def test_window_previews_the_default_target_and_merges_only_on_click_with_the_current_token(self):
        out = self.probe('''
show(TREE)
open_merge()
out["opened"] = opened()
out["list"] = of("ListCardBranches")
list_branches(LISTING)
out["preview"] = of("PreviewCardMerge")
out["before"] = [merge_enabled(), of("MergeCard")]
preview(PREVIEW)
out["ready"] = [merge_enabled(), of("MergeCard"), item("mergeWindowMerge")["text"], item("mergeWindowStart")["text"]]
click("mergeWindowMerge")
out["merge"] = of("MergeCard")
out["busy"] = merge_enabled()
'''.replace("LISTING", repr(listing())))
        self.assertTrue(out["opened"])
        self.assertEqual([c[2:] for c in out["list"]],
                         [["K-7", json.dumps({"purpose": "merge", "text": "", "limit": 50}, separators=(",", ":"))]])
        self.assertEqual([c[2:] for c in out["preview"]], [["K-7", '{"targetRef":"refs/heads/main"}']])
        self.assertEqual(out["before"], [False, []], "bez odpowiedzi podglądu nie ma zgody")
        self.assertEqual(out["ready"], [True, [], "Scal do main", "Start od: main (1.11, aaaaaaa)"])
        self.assertEqual([c[2:] for c in out["merge"]],
                         [["K-7", json.dumps({"targetRef": "refs/heads/main", "previewToken": "tok-1"},
                                             separators=(",", ":"))]])
        self.assertFalse(out["busy"], "w trakcie scalenia przycisk jest wyłączony")

    def test_close_escape_refresh_map_refresh_and_project_change_never_merge(self):
        out = self.probe('''
from PyQt6.QtCore import Qt
show(TREE)
open_merge()
list_branches(LISTING)
preview(PREVIEW)
click("mergeWindowClose")
out["closed"] = opened()
click("branchMerge")   # zaznaczenie zostaje po zamknięciu okna
list_branches(LISTING)
preview(PREVIEW)
key(Qt.Key.Key_Escape)
out["escaped"] = opened()
click("branchMerge")
list_branches(LISTING)
preview(PREVIEW)
click("mergeWindowRefresh")
out["refreshed"] = [opened(), len(of("PreviewCardMerge")), merge_enabled()]
preview(dict(PREVIEW, previewToken="tok-2"))
js("fake.boardChanged()")
wait(500)
answer(of("BranchMap")[-1][1], PROJECTS)   # odświeżenie mapy: projekty, potem mapa
answer(of("BranchMap")[-1][1], TREE)
out["mapRefresh"] = [opened(), merge_enabled()]
page("choose('beta')")
wait(80)
out["project"] = [opened(), of("setBranchProject")[-1][1]]
out["merges"] = of("MergeCard")
'''.replace("LISTING", repr(listing())))
        self.assertFalse(out["closed"])
        self.assertFalse(out["escaped"])
        self.assertEqual(out["refreshed"], [True, 4, False], "„Odśwież” wysyła nowy podgląd i czeka na odpowiedź")
        self.assertEqual(out["mapRefresh"], [True, True], "odświeżenie mapy nie zamyka okna i nie scala")
        self.assertEqual(out["project"], [False, "beta"])
        self.assertEqual(out["merges"], [])

    def test_card_that_disappears_from_the_map_closes_the_window(self):
        out = self.probe('''
show(TREE)
open_merge()
js("fake.boardChanged()")
wait(500)
answer(of("BranchMap")[-1][1], PROJECTS)
gone = dict(TREE, nodes=[n for n in TREE["nodes"] if n.get("card") != "K-7"])
answer(of("BranchMap")[-1][1], gone)
out["opened"] = opened()
out["merges"] = of("MergeCard")
''')
        self.assertEqual((out["opened"], out["merges"]), (False, []))

    def test_old_preview_reply_with_another_tag_does_not_unlock_merge(self):
        out = self.probe('''
show(TREE)
open_merge()
list_branches(LISTING)
first = last("PreviewCardMerge")[1]
click("mergeWindowRefresh")
second = last("PreviewCardMerge")[1]
board(first, PREVIEW)
out["stale"] = [merge_enabled(), first != second]
board(second, dict(PREVIEW, previewToken="tok-2"))
out["current"] = merge_enabled()
click("mergeWindowMerge")
out["merge"] = [json.loads(c[3]) for c in of("MergeCard")]
'''.replace("LISTING", repr(listing())))
        self.assertEqual(out["stale"], [False, True], "odpowiedź starego podglądu nie odblokowuje przycisku")
        self.assertTrue(out["current"])
        self.assertEqual(out["merge"], [{"targetRef": "refs/heads/main", "previewToken": "tok-2"}])

    def test_merge_refusal_shows_the_reason_locks_until_a_new_preview_and_sends_no_second_merge(self):
        # `stale-preview` to odmowa usługi, gdy tę samą kartę scaliło już drugie okno (Tablica) albo stan się zmienił;
        # okno traktuje tak każdą odmowę, także z kodem `already`.
        for code in ("stale-preview", "already"):
            with self.subTest(code=code):
                out = self.probe('''
show(TREE)
open_merge()
list_branches(LISTING)
preview(PREVIEW)
click("mergeWindowMerge")
board(last("MergeCard")[1], {"ok": False, "error": CODE, "message": "Podgląd jest nieaktualny (" + CODE + ")."})
out["after"] = [opened(), merge_enabled(), item("mergeWindowError")["text"], item("mergeWindowLockHint")["visible"]]
click("mergeWindowMerge")
click("mergeWindowMerge")
out["merges"] = len(of("MergeCard"))
click("mergeWindowRefresh")
out["waiting"] = merge_enabled()
preview(dict(PREVIEW, previewToken="tok-2"))
out["fresh"] = merge_enabled()
out["noticeEmpty"] = item("branchMergeNotice")["visible"]
'''.replace("LISTING", repr(listing())).replace("CODE", repr(code)))
                self.assertEqual(out["after"], [True, False, "Podgląd jest nieaktualny (%s)." % code, True])
                self.assertEqual(out["merges"], 1, "drugiego MergeCard nie ma")
                self.assertEqual((out["waiting"], out["fresh"]), (False, True))
                self.assertFalse(out["noticeEmpty"])

    def test_success_refreshes_the_map_and_the_node_is_merged(self):
        for already in (False, True):
            with self.subTest(already=already):
                out = self.probe('''
show(TREE)
open_merge()
list_branches(LISTING)
preview(PREVIEW)
maps = len(of("BranchMap"))
click("mergeWindowMerge")
board(last("MergeCard")[1], {"ok": True, "id": "K-7", "targetRef": "refs/heads/main", "targetBranch": "main",
                              "operationId": "op-1", "already": ALREADY})
out["closed"] = opened()
out["notice"] = item("branchMergeNotice")["text"]
out["reload"] = of("BranchMap")[maps:]
answer(last("BranchMap")[1], MERGED)
out["state"] = states()["K-7 · Domknięta karta"]
out["merge"] = field("branchMerge", "it.text")
out["merges"] = len(of("MergeCard"))
'''.replace("LISTING", repr(listing())).replace("ALREADY", repr(already))
                                    .replace("MERGED", repr(merge_tree(merged=True))))
                self.assertFalse(out["closed"])
                self.assertEqual(out["notice"], "Scalono K-7 do main" + (
                    " — gałąź karty była już w celu, bez nowego commita." if already else "."))
                self.assertEqual([c[2] for c in out["reload"]], ['{"project":"alfa"}'], "mapa czyta się od nowa")
                self.assertEqual(out["state"], "Gotowe · scalona do main (13.11)")
                self.assertEqual((out["merge"], out["merges"]), ([], 1))

    def test_branch_changes_from_the_window_open_the_card_diff_over_it_and_escape_returns(self):
        out = self.probe('''
from PyQt6.QtCore import Qt
show(TREE)
open_merge()
list_branches(LISTING)
preview(PREVIEW)
click("mergeWindowChanges")
out["diff"] = [last("Diff")[2], len(field("branchDiffView", "it.objectName"))]
key(Qt.Key.Key_Escape)
out["back"] = [len(field("branchDiffView", "it.objectName")), opened(), merge_enabled()]
key(Qt.Key.Key_Escape)
out["closed"] = opened()
out["merges"] = of("MergeCard")
'''.replace("LISTING", repr(listing())))
        self.assertEqual(out["diff"], ['{"source":"card","card":"K-7"}', 1])
        self.assertEqual(out["back"], [0, True, True], "Esc w diffie wraca do okna scalania")
        self.assertEqual((out["closed"], out["merges"]), (False, []))


class BackendTest(unittest.TestCase):
    def test_board_allowlist_is_exactly_the_three_merge_window_calls(self):
        module = load_launcher()
        self.assertEqual(module.BOARD_CALLS, ("ListCardBranches", "PreviewCardMerge", "MergeCard"))

    def test_window_files_name_no_other_board_writes(self):
        """Ani backend, ani QML okna nie wymieniają zakładania kart ani starej drogi scalenia bez tokenu."""
        names = []
        for folder, _dirs, files in os.walk(APP_DIR):
            if os.sep + "tests" in folder:
                continue
            for name in files:
                if name.endswith((".py", ".qml", ".js")):
                    with open(os.path.join(folder, name), encoding="utf-8") as fh:
                        text = fh.read()
                    names += [(name, word) for word in ("CreateCard", "MergeCard" + "ToMain", "createCard",
                                                        "mergeCard" + "ToMain", "conflictTaskAvailable") if word in text]
        self.assertEqual(names, [])

    def test_other_board_methods_are_refused_without_calling_the_service(self):
        module = load_launcher()
        backend = module.Backend(None)
        replies = []
        backend.boardReplied.connect(lambda tag, text: replies.append((tag, json.loads(text))))
        backend._board("x:1", "CreateCard", ["{}"])
        backend._board("x:2", "MergeCard" + "ToMain", ["K-1"])
        self.assertEqual([(t, r["error"]) for t, r in replies], [("x:1", "not-allowed"), ("x:2", "not-allowed")])

    def test_preview_and_missing_service_reply_without_calls(self):
        module = load_launcher()
        replies = []
        for backend in (module.create(None, data=[]), module.Backend(None)):
            backend.boardReplied.connect(lambda tag, text: replies.append((tag, json.loads(text)["error"])))
            backend.previewCardMerge("preview-merge:K-1:1", "K-1", "{}")
            backend.mergeCard("merge-card:K-1:2", "K-1", '{"targetRef":"refs/heads/main","previewToken":"t"}')
            backend.listCardBranches("merge-targets:K-1:3", "K-1", '{"purpose":"merge"}')
        self.assertEqual(replies, [("preview-merge:K-1:1", "preview"), ("merge-card:K-1:2", "preview"),
                                   ("merge-targets:K-1:3", "preview"), ("preview-merge:K-1:1", "no-service"),
                                   ("merge-card:K-1:2", "no-service"), ("merge-targets:K-1:3", "no-service")])


def fake_board_class():
    """Atrapa `/Board` z metodami okna i dwiema, których Worktrees wołać nie może (zapis każdego wywołania)."""
    from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtSlot

    @pyqtClassInfo("D-Bus Interface", "org.tide.Daemon.Board")
    class FakeBoard(QObject):
        def __init__(self, replies: dict):
            super().__init__()
            self.calls = []
            self.replies = replies

        def answer(self, member, *args):
            self.calls.append([member, *args])
            queue = self.replies.get(member) or [{"ok": False, "error": "unexpected"}]
            return json.dumps(queue.pop(0) if len(queue) > 1 else queue[0], ensure_ascii=False)

        @pyqtSlot(str, str, result=str)
        def ListCardBranches(self, card_id, request):
            return self.answer("ListCardBranches", card_id, request)

        @pyqtSlot(str, str, result=str)
        def PreviewCardMerge(self, card_id, request):
            return self.answer("PreviewCardMerge", card_id, request)

        @pyqtSlot(str, str, result=str)
        def MergeCard(self, card_id, request):
            return self.answer("MergeCard", card_id, request)

        @pyqtSlot(str, result=str)
        def CreateCard(self, request):
            return self.answer("CreateCard", request)

        # Nazwa rozbita na fragmenty: w `apps/` nie ma literału drogi bez tokenu (K-99 #16).
        @pyqtSlot(str, result=str, name="MergeCard" + "ToMain")
        def merge_without_token(self, card_id):
            return self.answer("MergeCard" + "ToMain", card_id)

    return FakeBoard


@requires_dbus
class BackendOverDbusTest(unittest.TestCase):
    """Backend Worktrees na prywatnej magistrali z atrapą `/Board`: żądanie okna przechodzi bez zmian, odmowa
    `MergeCard` wraca jedną odpowiedzią i nie jest ponawiana, inne metody `/Board` nie wychodzą z okna."""

    def test_calls_pass_unchanged_and_a_refusal_is_not_retried(self):
        from PyQt6.QtDBus import QDBusConnection
        bus = PrivateBus("tide-wt-merge")
        self.addCleanup(bus.stop)
        daemon = QDBusConnection.connectToBus(bus.address, "tide-wt-merge-daemon")
        self.addCleanup(QDBusConnection.disconnectFromBus, "tide-wt-merge-daemon")
        fake = fake_board_class()({
            "ListCardBranches": [listing()], "PreviewCardMerge": [PREVIEW],
            "MergeCard": [{"ok": False, "error": "stale-preview", "message": "Podgląd jest nieaktualny."}]})
        self.assertTrue(daemon.registerObject("/Board", fake, QDBusConnection.RegisterOption.ExportAllSlots))
        self.assertTrue(daemon.registerService("org.tide.Daemon"))
        backend = load_launcher().Backend(bus.bus)
        replies = []
        backend.boardReplied.connect(lambda tag, text: replies.append((tag, json.loads(text))))
        backend.listCardBranches("merge-targets:K-7:1", "K-7", '{"purpose":"merge","text":"","limit":50}')
        backend.previewCardMerge("preview-merge:K-7:2", "K-7", '{"targetRef":"refs/heads/main"}')
        self.assertTrue(wait_until(lambda: len(replies) == 2, timeout=5))
        consent = '{"targetRef":"refs/heads/main","previewToken":"tok-1"}'
        backend.mergeCard("merge-card:K-7:3", "K-7", consent)
        backend._board("x:4", "CreateCard", ['{"title":"konflikty"}'])
        backend._board("x:5", "MergeCard" + "ToMain", ["K-7"])
        self.assertTrue(wait_until(lambda: len(replies) == 5, timeout=5))
        wait_until(lambda: False, timeout=0.5)   # nic więcej nie przychodzi: brak ponowień
        self.assertEqual(fake.calls, [
            ["ListCardBranches", "K-7", '{"purpose":"merge","text":"","limit":50}'],
            ["PreviewCardMerge", "K-7", '{"targetRef":"refs/heads/main"}'],
            ["MergeCard", "K-7", consent]])
        self.assertEqual([(t, r.get("error", "")) for t, r in replies], [
            ("merge-targets:K-7:1", ""), ("preview-merge:K-7:2", ""), ("x:4", "not-allowed"), ("x:5", "not-allowed"),
            ("merge-card:K-7:3", "stale-preview")])
        self.assertEqual(replies[1][1]["previewToken"], "tok-1")


@requires_dbus
@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class WindowOverDbusTest(unittest.TestCase):
    """Okno Worktrees offscreen z prawdziwym backendem na prywatnej magistrali i atrapą usługi (sonda mergeprobe.py):
    atrapa odpowiada od razu, a scenariusz ustawia jej odpowiedzi (`REPLIES`, `MAP`) i czyta wywołania (`BOARD`)."""

    def probe(self, scenario: str, replies: dict, tree: dict) -> dict:
        bus = PrivateBus("tide-wt-merge-window")
        self.addCleanup(bus.stop)
        with tempfile.TemporaryDirectory(prefix="tide-wt-merge-dbus-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            qml = os.path.join(pkg, "views", "worktrees", "qml")
            with open(os.path.join(qml, "Harness.qml"), "w", encoding="utf-8") as fh:
                fh.write(PROBE_HARNESS)
            data = os.path.join(tmp, "fake.json")
            with open(data, "w", encoding="utf-8") as fh:
                json.dump({"replies": replies, "projects": PROJECTS, "map": tree}, fh)
            script = os.path.join(tmp, "scenario.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(preamble() + HELPERS + scenario)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ="UTC",
                       XDG_CONFIG_HOME=os.path.join(tmp, "config"), DBUS_SESSION_BUS_ADDRESS=bus.address)
            result = subprocess.run(["/usr/bin/python3", MERGE_PROBE, qml, script,
                                     os.path.join(pkg, "views", "worktrees", "backend.py"), bus.address, data],
                                    env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", out, out)
        return out

    def test_from_the_map_preview_default_then_merge_on_click_and_the_node_is_merged(self):
        """Historia 37: repozytorium bez `master` i bez `origin`, główna `main` albo `develop`; baza karty znana
        (domyślny cel = główna) albo nieznana (główna to propozycja, wybór skrótu „Główna”)."""
        for main in ("main", "develop"):
            for known in (True, False):
                with self.subTest(main=main, baseKnown=known):
                    ref = "refs/heads/" + main
                    replies = {
                        "ListCardBranches": [listing(main, default=known)],
                        "PreviewCardMerge": [dict(PREVIEW, targetRef=ref, targetBranch=main, previewToken="tok-" + main)],
                        "MergeCard": [{"ok": True, "id": "K-7", "targetRef": ref, "targetBranch": main,
                                       "operationId": "op-1", "already": False}]}
                    out = self.probe(r'''
until(lambda: titles(), 5000)
select("K-7 ")
click("branchMerge")
until(lambda: [c for c in BOARD() if c[0] == "PreviewCardMerge"], 5000)
wait(150)
out["calls"] = BOARD()
out["proposal"] = page("mergeWindow.proposal")
out["mainRef"] = page("mergeWindow.picker.mainRef")
if KNOWN:
    click("mergeWindowTargetToggle")   # cel wybrany: strefa wyboru zwinięta pod „Zmień cel”
out["shortcuts"] = shortcuts()
out["target"] = item("mergeWindowTarget")["text"]
out["texts"] = window_texts()
if not KNOWN:
    click("mergeTargetShortcut0")   # świadomy wybór propozycji „Główna”
    wait(100)
out["enabled"] = merge_enabled()
out["before"] = [c[0] for c in BOARD()]
set_map(MERGED)
click("mergeWindowMerge")
until(lambda: not opened(), 5000)
until(lambda: "scalona" in states().get("K-7 · Domknięta karta", ""), 5000)
out["after"] = BOARD()[len(out["before"]):]
out["state"] = states()["K-7 · Domknięta karta"]
out["notice"] = item("branchMergeNotice")["text"]
out["maps"] = MAPS()
'''.replace("KNOWN", repr(known)).replace("MERGED", repr(merge_tree(main, closed_base=known, merged=True))),
                        replies, merge_tree(main, closed_base=known))
                    self.assertEqual([c[0] for c in out["calls"]], ["ListCardBranches", "PreviewCardMerge"])
                    self.assertEqual(out["calls"][1][1:], ["K-7", json.dumps({"targetRef": ref}, separators=(",", ":"))])
                    self.assertEqual(out["mainRef"], ref, "„nazwana główna” z `mainRef` odpowiedzi")
                    self.assertEqual(out["proposal"], not known)
                    self.assertEqual(out["shortcuts"], [("Baza karty · " if known else "Główna · ") + ref])
                    self.assertEqual(out["target"], "K-7 → " + main)
                    joined = "\n".join(out["texts"]).lower()
                    for word in ("master", "origin", "refs/remotes"):
                        self.assertNotIn(word, joined)
                    self.assertTrue(out["enabled"])
                    self.assertNotIn("MergeCard", out["before"], "bez kliknięcia nie ma zgody")
                    self.assertEqual(out["after"], [["MergeCard", "K-7", json.dumps(
                        {"targetRef": ref, "previewToken": "tok-" + main}, separators=(",", ":"))]])
                    self.assertEqual(out["state"], "Gotowe · scalona do %s (13.11)" % main)
                    self.assertEqual(out["notice"], "Scalono K-7 do %s." % main)
                    self.assertGreaterEqual(out["maps"], 2, "mapa przeczytana po scaleniu")

    def test_second_window_refusal_stale_preview_then_already_success_without_extra_merge(self):
        """Dwa okna tej samej karty: Tablica scaliła pierwsza — `MergeCard` z Worktrees dostaje `stale-preview`
        (powód, blokada do nowego podglądu, brak drugiego `MergeCard`); po nowym podglądzie zgoda trafia do tej samej
        operacji (`already: true`, sukces bez nowego commita)."""
        replies = {
            "ListCardBranches": [listing()],
            "PreviewCardMerge": [PREVIEW, dict(PREVIEW, previewToken="tok-2")],
            "MergeCard": [{"ok": False, "error": "stale-preview",
                           "message": "Podgląd jest nieaktualny. Przygotuj nowy podgląd i potwierdź go."},
                          {"ok": True, "id": "K-7", "targetRef": "refs/heads/main", "targetBranch": "main",
                           "operationId": "op-board", "already": True}]}
        out = self.probe(r'''
until(lambda: titles(), 5000)
select("K-7 ")
click("branchMerge")
until(lambda: merge_enabled(), 5000)
click("mergeWindowMerge")
until(lambda: item("mergeWindowError")["visible"], 5000)
out["refused"] = [opened(), merge_enabled(), item("mergeWindowError")["text"]]
click("mergeWindowMerge")
click("mergeWindowMerge")
wait(300)
out["merges"] = len([c for c in BOARD() if c[0] == "MergeCard"])
click("mergeWindowRefresh")
until(lambda: merge_enabled(), 5000)
set_map(MERGED)
click("mergeWindowMerge")
until(lambda: not opened(), 5000)
until(lambda: "scalona" in states().get("K-7 · Domknięta karta", ""), 5000)
out["consents"] = [json.loads(c[2]) for c in BOARD() if c[0] == "MergeCard"]
out["notice"] = item("branchMergeNotice")["text"]
out["state"] = states()["K-7 · Domknięta karta"]
'''.replace("MERGED", repr(merge_tree(merged=True))), replies, merge_tree())
        self.assertEqual(out["refused"], [True, False, "Podgląd jest nieaktualny. Przygotuj nowy podgląd i potwierdź go."])
        self.assertEqual(out["merges"], 1, "odmowa nie daje drugiego MergeCard")
        self.assertEqual(out["consents"], [{"targetRef": "refs/heads/main", "previewToken": "tok-1"},
                                           {"targetRef": "refs/heads/main", "previewToken": "tok-2"}],
                         "druga zgoda dopiero po nowym podglądzie i z jego tokenem")
        self.assertEqual(out["notice"], "Scalono K-7 do main — gałąź karty była już w celu, bez nowego commita.")
        self.assertEqual(out["state"], "Gotowe · scalona do main (13.11)")


# Okno z prawdziwym backendem (kontekst `realBackend` ustawia mergeprobe.py).
PROBE_HARNESS = '''
import QtQuick
import QtQuick.Window

Window {
    id: root
    width: 1180
    height: 820
    visible: true

    WorktreesView {
        id: win
        anchors.fill: parent
        backend: realBackend
    }
    QtObject { id: loader; property var item: win }
}
'''


if __name__ == "__main__":
    unittest.main()
