"""Okno Worktrees: „Zmiany w katalogu” i „Zmiany względem głównej” w widoku diffa (K-36, PT-360).

Sonda offscreen (apps/board/tests/diffprobe.py: prawdziwe okno, kliknięcia i klawisze) na pakiecie okna Tide
z atrapą backendu Worktrees: które wiersze mają które akcje, wywołanie `Git.Diff` z właściwym żądaniem (źródło
`worktree` albo `range` z lokalną główną projektu), nagłówki widoku i powrót do listy po Esc. Etykiety i akcje
z PT-352 i PT-353 (test_window.py) zostają bez zmian. Backend na prawdziwej magistrali: test_window.py.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from apps.board.tests.test_diff_groups import WORKTREE
from . import REPO
from .test_window import NOW, QML_PROBLEMS, item, stage

PROBE = os.path.join(REPO, "apps", "board", "tests", "diffprobe.py")

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
        property var calls: []
        signal replied(string tag, string json)
        signal removeFinished(string text)
        signal pruneFinished(string text)
        signal fetchFinished(string repoPath, string text)
        function gitDiff(tag, request) { calls = calls.concat([[tag, request]]) }
        function remove(paths) {}
        function removeForced(paths, deleteBranches, confirmed) {}
        function open(path, app) {}
        function refresh() {}
        function prune(repo) {}
        function fetch(repo) {}
    }
    WorktreesView { id: win; anchors.fill: parent; backend: fake }
    // diffprobe.py czyta widoczne elementy spod `loader.item`.
    QtObject { id: loader; property var item: win }
}
'''


def rows():
    return [
        item("/w/a", "alpha", "active", hasAgent=True, blocker="agent", dirty=2, branch="feat/a", title="alfa"),
        item("/w/b", "alpha", "clean", dirty=0, branch="feat/b", title="beta"),
        item("/w/c", "alpha", "decide", dirty=1, branch="", detached=True, head="abc1234", blocker="dirty",
             title="gamma", unmerged=1, merged=False),
        item("/w/gone", "alpha", "missing", missing=True, dirty=3, branch="feat/gone", blocker="missing", size=-1,
             title="gone"),
        item("/r/alpha", "alpha", "main", isMain=True, branch="master", dirty=4, tag="", blocker="main",
             title="alpha"),
    ]


class DiffActionsTest(unittest.TestCase):
    def probe(self, scenario: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="tide-wt-diff-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            qml = os.path.join(pkg, "views", "worktrees", "qml")
            with open(os.path.join(qml, "Harness.qml"), "w", encoding="utf-8") as fh:
                fh.write(HARNESS % {"list": json.dumps(json.dumps(rows(), ensure_ascii=False))})
            script = os.path.join(tmp, "scenario.py")
            head = ("WT = json.loads(%r)\nSTART = %r\n" % (json.dumps(WORKTREE), NOW)
                    + "def texts(name):\n    return field(name, 'it.text')\n"
                    + "def last_tag():\n    return calls()[-1][0]\n")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(head + scenario)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ="UTC")
            result = subprocess.run(["/usr/bin/python3", PROBE, qml, script], env=env, capture_output=True,
                                    text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", out, out)
        return out

    def test_actions_depend_on_dirty_and_branch(self):
        out = self.probe('''
out["changes"] = len(texts("worktreeChanges"))
out["changesLabel"] = texts("worktreeChanges")[0]
out["branchLabel"] = texts("worktreeBranchChanges")[0]
out["branchTips"] = field("worktreeBranchChanges", "it.toolTip")
out["branch"] = len(texts("worktreeBranchChanges"))
out["labels"] = [len(texts("worktreeSync")) > 0, len(texts("worktreeActivity")) > 0]
''')
        # A (zmiany + gałąź), B (tylko gałąź), C (tylko zmiany, odłączony HEAD); brakujący katalog i główny — bez gałęzi
        # głównej albo bez katalogu — nie mają akcji (główne grupy są zwinięte).
        self.assertEqual((out["changes"], out["branch"]), (2, 2))
        self.assertEqual(out["changesLabel"], "Zmiany w katalogu")
        self.assertEqual(out["branchLabel"], "Zmiany względem głównej")
        self.assertEqual(out["branchTips"], ["Zmiany gałęzi feat/a względem master",
                                             "Zmiany gałęzi feat/b względem master"])
        self.assertEqual(out["labels"], [True, True], "etykiety i akcje z PT-352 i PT-353 zostają")

    def test_changes_in_directory_open_worktree_source_with_groups_and_escape_returns(self):
        out = self.probe('''
click("worktreeChanges", 0)
out["call"] = calls()
out["open"] = js("win.page.diffOpen")
out["listHidden"] = len(texts("worktreeChanges"))
reply(last_tag(), WT)
out["header"] = texts("diffHeader")
out["groups"] = texts("diffGroupHeader")
out["untracked"] = texts("diffUntrackedHeader")
from PyQt6.QtCore import Qt
key(Qt.Key.Key_Escape)
out["afterEsc"] = [js("win.page.diffOpen"), texts("diffHeader"), len(texts("worktreeChanges"))]
''')
        self.assertEqual(out["call"], [["diff:1", json.dumps({"source": "worktree", "path": "/w/a"},
                                                             separators=(",", ":"))]])
        self.assertTrue(out["open"])
        self.assertEqual(out["header"], ["Niezacommitowane zmiany: /w/alpha"])
        self.assertEqual(out["groups"], ["W indeksie · 2 pliki · +2 −1", "Poza indeksem · 2 pliki · +3 −0"])
        self.assertEqual(out["untracked"], ["Nieśledzone · 2 pliki (bez treści)"])
        self.assertEqual(out["afterEsc"], [False, [], 2], "Esc wraca do listy Worktrees")

    def test_changes_against_main_open_range_source_with_the_local_main_branch(self):
        out = self.probe('''
click("worktreeBranchChanges", 1)
out["call"] = calls()
reply(last_tag(), {"ok": True, "source": "range", "repo": "/r/alpha", "title": "feat/b względem master",
                   "from": {"ref": "refs/heads/master", "sha": "a" * 40, "date": ""},
                   "to": {"ref": "refs/heads/feat/b", "sha": "b" * 40, "date": ""},
                   "files": [], "totals": {"files": 0, "additions": 0, "deletions": 0}, "truncated": False,
                   "warnings": []})
out["header"] = texts("diffHeader")
out["empty"] = texts("diffEmpty")
from PyQt6.QtCore import Qt
key(Qt.Key.Key_Escape)
out["afterEsc"] = [js("win.page.diffOpen"), len(texts("worktreeBranchChanges"))]
''')
        self.assertEqual(json.loads(out["call"][0][1]),
                         {"source": "range", "repo": "/r/alpha", "from": "refs/heads/master", "to": "refs/heads/feat/b"})
        self.assertEqual(out["header"], ["feat/b względem master"])
        self.assertEqual(out["empty"], ["Brak zmian w tym zakresie."])
        self.assertEqual(out["afterEsc"], [False, 2])

    def test_service_error_is_one_sentence_and_the_list_stays_reachable(self):
        out = self.probe('''
click("worktreeChanges", 1)
reply(last_tag(), {"ok": False, "error": "no-worktree",
                   "message": "Katalog worktree nie istnieje (został usunięty?): /w/c"})
out["shown"] = texts("diffError")
click("diffClose")
out["closed"] = js("win.page.diffOpen")
''')
        self.assertEqual(out["shown"], ["Katalog worktree nie istnieje (został usunięty?): /w/c. "
                                        "Odśwież listę Worktrees (r)."])
        self.assertFalse(out["closed"])

    def test_open_diff_ignores_unknown_paths_and_missing_actions(self):
        out = self.probe('''
js("win.page.openDiff('/nie/ma', 'worktree')")
js("win.page.openDiff('/w/gone', 'worktree')")
js("win.page.openDiff('/w/b', 'worktree')")
js("win.page.openDiff('/r/alpha', 'range')")
out["open"] = js("win.page.diffOpen")
out["calls"] = calls()
''')
        self.assertEqual((out["open"], out["calls"]), (False, []))


class BackendGitDiffTest(unittest.TestCase):
    def test_without_the_service_the_reply_says_so_and_nothing_is_called(self):
        from importlib.util import module_from_spec, spec_from_file_location
        spec = spec_from_file_location("wt_backend", os.path.join(REPO, "apps", "worktrees", "backend.py"))
        backend_module = module_from_spec(spec)
        spec.loader.exec_module(backend_module)
        replies = []
        for backend in (backend_module.create(None, data=[]), backend_module.Backend(None)):
            backend.replied.connect(lambda tag, text: replies.append((tag, json.loads(text))))
            backend.gitDiff("diff:7", '{"source":"worktree","path":"/w/a"}')
        self.assertEqual([(tag, reply["ok"], reply["error"]) for tag, reply in replies],
                         [("diff:7", False, "preview"), ("diff:7", False, "no-service")])


class RequestLogicTest(unittest.TestCase):
    """`diffRequest` z worktrees.js — te same reguły co przyciski, bez okna."""

    def test_requests(self):
        tmp = tempfile.mkdtemp(prefix="tide-wt-logic-")
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "Probe.qml")
        source = os.path.join(REPO, "apps", "worktrees", "qml")
        shutil.copy(os.path.join(source, "worktrees.js"), tmp)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('''import QtQuick
import "worktrees.js" as Logic
Item {
    Component.onCompleted: {
        const a = %s
        console.warn("OUT " + JSON.stringify([Logic.diffRequest(a, "worktree"), Logic.diffRequest(a, "range"),
            Logic.diffRequest(Object.assign({}, a, {dirty: 0}), "worktree"),
            Logic.diffRequest(Object.assign({}, a, {dirty: -1}), "worktree"),
            Logic.diffRequest(Object.assign({}, a, {branch: "master"}), "range"),
            Logic.diffRequest(Object.assign({}, a, {mainBranch: ""}), "range"),
            Logic.diffRequest(Object.assign({}, a, {missing: true}), "worktree"),
            Logic.diffRequest(a, "nope")]))
        Qt.quit()
    }
}
''' % json.dumps(item("/w/a", "alpha", "active", dirty=2, branch="feat/a")))
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        log = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60).stderr
        match = re.search(r"OUT (.*)$", log, re.M)
        self.assertIsNotNone(match, log[-2000:])
        worktree, branch, clean, unknown, on_main, no_main, missing, nope = json.loads(match.group(1))
        self.assertEqual(worktree, {"source": "worktree", "path": "/w/a"})
        self.assertEqual(branch, {"source": "range", "repo": "/r/alpha", "from": "refs/heads/master",
                                  "to": "refs/heads/feat/a"})
        self.assertIsNone(clean)
        self.assertEqual(unknown, {"source": "worktree", "path": "/w/a"}, "stan nieznany (−1) też pokazuje zmiany")
        self.assertIsNone(on_main)
        self.assertIsNone(no_main)
        self.assertIsNone(missing)
        self.assertIsNone(nope)


if __name__ == "__main__":
    unittest.main()
