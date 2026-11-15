"""Okno Worktrees, tryb „Gałęzie” (K-99, PT-388): przełącznik, trwałość trybu i projektu, drzewo mapy gałęzi.

- trwałość: Backend na prywatnym XDG_CONFIG_HOME (nic nie trafia do prawdziwego katalogu ustawień) — tryb i projekt
  wracają po ponownym utworzeniu backendu; ten sam plik `window.json` co tryb diffa (PT-350),
- logika (branches.js, qml6): teksty stanu, kolejność i zagnieżdżenie drzewa, „pokazano N z M”,
- okno (sonda offscreen apps/board/tests/diffprobe.py) z atrapą backendu: przełącznik, wybór projektu →
  `Git.BranchMap({project})`, drzewo z grupą „baza nieznana”, pusty stan, błąd, „Otwórz kartę”, „Zmiany gałęzi”.
Tryb Katalogi zachowuje dotychczasowe testy (test_window.py, test_diff_actions.py) bez zmian oczekiwań.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import REPO
from .test_diff_actions import PROBE, rows
from .test_window import NOW, QML_PROBLEMS, load_launcher, stage

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
        property string viewMode: "%(mode)s"
        property string branchProject: "%(project)s"
        property var calls: []
        signal replied(string tag, string json)
        signal branchMapReplied(string tag, string json)
        signal boardChanged()
        signal removeFinished(string text)
        signal pruneFinished(string text)
        signal fetchFinished(string repoPath, string text)
        signal pullFinished(string path, string text)
        function gitDiff(tag, request) { calls = calls.concat([[tag, request]]) }
        function branchMap(tag, request) { calls = calls.concat([[tag, request]]) }
        function setViewMode(value) { calls = calls.concat([["setViewMode", value]]); viewMode = value; return "" }
        function setBranchProject(slug) {
            calls = calls.concat([["setBranchProject", slug]]); branchProject = slug; return "%(note)s"
        }
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
        onNavigateRequested: (viewId, item) => fake.calls = fake.calls.concat([["navigate", viewId, item]])
    }
    // diffprobe.py czyta widoczne elementy spod `loader.item`.
    QtObject { id: loader; property var item: win }
}
'''

PROJECTS = {"ok": True, "projects": [{"slug": "alfa", "name": "Alfa", "repo": "/r/alfa", "cards": 5},
                                      {"slug": "beta", "name": "Beta", "repo": "/r/beta", "cards": 0}]}


def card(num, title, state, column, ref="refs/heads/master", **extra):
    node = {"id": f"card:K-{num}", "kind": "card", "ref": f"refs/heads/card/k-{num}", "branch": f"card/k-{num}",
            "card": f"K-{num}", "title": title, "column": column, "state": state,
            "started": {"ref": ref, "sha": "a" * 40, "date": "2026-11-01T10:00:00+01:00"}}
    node.update(extra)
    return node


def ticket(num, ticket_num, title, state, **extra):
    node = {"id": f"ticket:K-{num}:PT-{ticket_num}", "kind": "ticket", "ref": f"refs/heads/ticket/pt-{ticket_num}",
            "branch": f"ticket/pt-{ticket_num}", "card": f"K-{num}", "ticket": f"PT-{ticket_num}", "title": title,
            "state": state, "started": {"ref": f"refs/heads/card/k-{num}", "sha": "b" * 40, "date": ""}}
    node.update(extra)
    return node


def map_reply(**extra):
    """Mapa z atrapy: baza master z kartami K-11 (w toku, z ticketem i kartą K-12 zagnieżdżoną), K-10 (scalona,
    ticket scalony i odrzucony) oraz grupa „baza nieznana” z kartą K-5 (domknięta)."""
    nodes = [
        {"id": "base:refs/heads/master", "kind": "base", "ref": "refs/heads/master", "branch": "master",
         "state": "active"},
        {"id": "unknown-base", "kind": "unknown-base", "ref": "", "branch": "", "title": "Nieznana baza",
         "state": "active"},
        card(11, "Aktywna karta", "active", "progress"),
        ticket(11, 3, "Ticket trzeci", "active"),
        card(12, "Karta z karty", "active", "todo", ref="refs/heads/card/k-11"),
        card(10, "Scalona karta", "merged", "done",
             merged={"targetRef": "refs/heads/master", "sha": "c" * 40, "date": "2026-11-11T09:30:00+01:00"}),
        ticket(10, 1, "Ticket pierwszy", "merged",
               merged={"targetRef": "refs/heads/card/k-10", "sha": "d" * 40, "date": "2026-11-05T09:30:00+01:00"}),
        ticket(10, 2, "Ticket drugi", "rejected"),
        card(5, "Stara karta", "closed", "archived", ref=""),
    ]
    edges = [
        {"kind": "start", "from": "base:refs/heads/master", "to": "card:K-11"},
        {"kind": "start", "from": "card:K-11", "to": "ticket:K-11:PT-3"},
        {"kind": "start", "from": "card:K-11", "to": "card:K-12"},
        {"kind": "start", "from": "base:refs/heads/master", "to": "card:K-10"},
        {"kind": "start", "from": "card:K-10", "to": "ticket:K-10:PT-1"},
        {"kind": "start", "from": "card:K-10", "to": "ticket:K-10:PT-2"},
        {"kind": "merge", "from": "ticket:K-10:PT-1", "to": "card:K-10"},
        {"kind": "merge", "from": "card:K-10", "to": "base:refs/heads/master"},
        {"kind": "start", "from": "unknown-base", "to": "card:K-5"},
    ]
    reply = {"ok": True, "project": "alfa", "repo": "/r/alfa", "nodes": nodes, "edges": edges, "total": 7,
             "truncated": False, "warnings": []}
    reply.update(extra)
    return reply


class ConfigIsolation(unittest.TestCase):
    """Prywatny XDG_CONFIG_HOME: testy nie dotykają prawdziwego katalogu ustawień użytkownika."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-wt-branches-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        self.addCleanup(lambda: os.environ.pop("XDG_CONFIG_HOME") if old is None
                        else os.environ.__setitem__("XDG_CONFIG_HOME", old))
        self.file = os.path.join(self.tmp, "tide", "window.json")


class PersistenceTest(ConfigIsolation):
    def test_defaults_are_directories_and_no_project(self):
        backend = load_launcher().Backend(None)
        self.assertEqual((backend.viewMode, backend.branchProject), ("dirs", ""))
        self.assertFalse(os.path.exists(self.file))

    def test_mode_and_project_survive_a_new_backend_on_the_same_settings_file(self):
        module = load_launcher()
        backend = module.Backend(None)
        modes, projects = [], []
        backend.viewModeChanged.connect(lambda: modes.append(backend.viewMode))
        backend.branchProjectChanged.connect(lambda: projects.append(backend.branchProject))
        self.assertEqual(backend.setViewMode("branches"), "")
        self.assertEqual(backend.setBranchProject("alfa"), "")
        self.assertEqual((modes, projects), (["branches"], ["alfa"]))
        again = module.Backend(None)
        self.assertEqual((again.viewMode, again.branchProject), ("branches", "alfa"))
        with open(self.file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"worktreesMode": "branches", "worktreesProject": "alfa"})
        self.assertTrue(self.file.startswith(self.tmp), "ustawienia poszły do prywatnego katalogu testu")

    def test_it_is_the_same_file_and_keys_stay_next_to_the_diff_mode(self):
        backend = load_launcher().Backend(None)
        backend.setDiffMode("split")
        backend.setViewMode("branches")
        backend.setBranchProject("beta")
        with open(self.file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"diffMode": "split", "worktreesMode": "branches",
                                             "worktreesProject": "beta"})
        self.assertEqual(backend.diffMode, "split")

    def test_unknown_values_are_refused_and_nothing_is_written(self):
        backend = load_launcher().Backend(None)
        self.assertNotEqual(backend.setViewMode("tree"), "")
        self.assertNotEqual(backend.setBranchProject(""), "")
        self.assertNotEqual(backend.setBranchProject("x" * 500), "")
        self.assertEqual((backend.viewMode, backend.branchProject), ("dirs", ""))
        self.assertFalse(os.path.exists(self.file))

    def test_damaged_file_gives_defaults(self):
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"worktreesMode": "tree", "worktreesProject": 7}))
        backend = load_launcher().Backend(None)
        self.assertEqual((backend.viewMode, backend.branchProject), ("dirs", ""))

    def test_write_failure_is_reported_and_the_choice_lasts_for_the_session(self):
        backend = load_launcher().Backend(None)
        os.makedirs(self.file)  # katalog w miejscu pliku: zapis się nie uda
        self.assertTrue(backend.setViewMode("branches").startswith("Nie zapisano ustawienia"))
        self.assertEqual(backend.viewMode, "branches")

    def test_preview_keeps_both_in_memory_only(self):
        backend = load_launcher().Backend(None, [])
        self.assertEqual(backend.setViewMode("branches"), "")
        self.assertEqual(backend.setBranchProject("alfa"), "")
        self.assertEqual((backend.viewMode, backend.branchProject), ("branches", "alfa"))
        self.assertFalse(os.path.exists(self.file))


class BackendBranchMapTest(unittest.TestCase):
    def test_without_the_service_the_reply_says_so(self):
        module = load_launcher()
        replies = []
        for backend in (module.create(None, data=[]), module.Backend(None)):
            backend.branchMapReplied.connect(lambda tag, text: replies.append((tag, json.loads(text))))
            backend.branchMap("map:1", '{"project":"alfa"}')
        self.assertEqual([(tag, reply["ok"], reply["error"]) for tag, reply in replies],
                         [("map:1", False, "preview"), ("map:1", False, "no-service")])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class LogicTest(unittest.TestCase):
    """branches.js: teksty stanu, drzewo i napisy — bez okna."""

    def run_logic(self, body: str):
        tmp = tempfile.mkdtemp(prefix="tide-wt-branches-logic-")
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
        const reply = %s
        console.warn("OUT " + JSON.stringify(%s))
        Qt.quit()
    }
}
''' % (json.dumps(map_reply()), body))
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ="Europe/Warsaw")
        log = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60).stderr
        match = re.search(r"OUT (.*)$", log, re.M)
        self.assertIsNotNone(match, log[-2000:])
        return json.loads(match.group(1))

    def test_state_texts(self):
        texts = self.run_logic('reply.nodes.filter(n => n.kind === "card" || n.kind === "ticket")'
                               '.map(n => [n.id, B.stateText(n)])')
        self.assertEqual(dict(texts), {
            "card:K-11": "w toku", "ticket:K-11:PT-3": "w toku", "card:K-12": "w toku",
            "card:K-10": "scalona do master (11.11)", "ticket:K-10:PT-1": "scalony",
            "ticket:K-10:PT-2": "odrzucony", "card:K-5": "domknięta"})

    def test_closed_ticket_and_merged_card_without_target_or_date(self):
        out = self.run_logic('[B.stateText({kind: "ticket", state: "closed"}), '
                             'B.stateText({kind: "card", state: "merged"}), '
                             'B.stateText({kind: "card", state: "merged", merged: {targetRef: "refs/remotes/origin/main", '
                             'date: ""}}), B.stateText({kind: "card", state: "merged", merged: {targetRef: "", '
                             'date: "2026-02-03T00:00:00Z"}})]')
        self.assertEqual(out, ["zamknięty", "scalona", "scalona do origin/main", "scalona (3.02)"])

    def test_detail_adds_the_board_column_to_cards_only(self):
        out = self.run_logic('reply.nodes.map(n => B.detailText(n))')
        self.assertEqual(out[:2], ["", ""])
        self.assertIn("W toku · w toku", out)
        self.assertIn("Gotowe · scalona do master (11.11)", out)
        self.assertIn("Archiwum · domknięta", out)

    def test_tree_order_and_nesting(self):
        out = self.run_logic('B.rows(reply.nodes, reply.edges).map(r => [r.depth, B.titleText(r.node)])')
        self.assertEqual(out, [
            [0, "master"],
            [1, "K-11 · Aktywna karta"], [2, "PT-3 · Ticket trzeci"], [2, "K-12 · Karta z karty"],
            [1, "K-10 · Scalona karta"], [2, "PT-1 · Ticket pierwszy"], [2, "PT-2 · Ticket drugi"],
            [0, "Nieznana baza"], [1, "K-5 · Stara karta"]])

    def test_tree_survives_cycles_duplicates_and_orphans(self):
        out = self.run_logic('B.rows([{id: "a", kind: "card", card: "K-1"}, {id: "b", kind: "card", card: "K-2"}, '
                             '{id: "c", kind: "ticket", card: "K-3", ticket: "PT-3"}], '
                             '[{kind: "start", from: "a", to: "b"}, {kind: "start", from: "b", to: "a"}, '
                             '{kind: "start", from: "x", to: "c"}]).map(r => [r.depth, r.id])')
        self.assertEqual(out, [[0, "c"]], "cykl bez korzenia i brak rodzica nie psują wyniku (cykl jest pomijany)")
        self.assertEqual(self.run_logic('B.rows(null, null).length'), 0)

    def test_shown_text_only_when_truncated(self):
        out = self.run_logic('[B.shownText(reply), B.shownText(Object.assign({}, reply, {truncated: true, total: 400})), '
                             'B.shownText(null)]')
        self.assertEqual(out, ["", "pokazano 7 z 400", ""])

    def test_requests_and_helpers(self):
        out = self.run_logic('[B.mapRequest("alfa"), B.cardDiffRequest("K-11"), B.errorText({message: "Źle"}), '
                             'B.errorText({}), B.hasBranches(reply), B.hasBranches({nodes: []}), '
                             'B.pickProject([{slug: "a"}, {slug: "b"}], "b"), B.pickProject([{slug: "a"}], "z"), '
                             'B.pickProject([], "z"), B.stateText({kind: "card", state: "merged", merged: {date: "zły"}})]')
        self.assertEqual(out, [{"project": "alfa"}, {"source": "card", "card": "K-11"}, "Źle",
                               "Nie udało się odczytać mapy gałęzi.", True, False, "b", "a", "", "scalona"])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class WindowTest(unittest.TestCase):
    def probe(self, scenario: str, mode="dirs", project="", note="") -> dict:
        with tempfile.TemporaryDirectory(prefix="tide-wt-branches-win-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            qml = os.path.join(pkg, "views", "worktrees", "qml")
            with open(os.path.join(qml, "Harness.qml"), "w", encoding="utf-8") as fh:
                fh.write(HARNESS % {"list": json.dumps(json.dumps(rows(), ensure_ascii=False)), "mode": mode,
                                    "project": project, "note": note})
            script = os.path.join(tmp, "scenario.py")
            head = ("START = %r\n" % NOW
                    + "def texts(name):\n    return field(name, 'it.text')\n"
                    + "def last():\n    return calls()[-1]\n"
                    + "def answer(tag, payload):\n"
                    + "    js('fake.branchMapReplied(' + json.dumps(tag) + ', ' + json.dumps(json.dumps(payload)) + ')')\n"
                    + "    wait(80)\n"
                    + "def titles():\n    return field('branchNode', 'it.titleText')\n"
                    + "PROJECTS = json.loads(%r)\nTREE = json.loads(%r)\n" % (json.dumps(PROJECTS),
                                                                             json.dumps(map_reply())))
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

    def test_switch_changes_the_mode_and_directories_stay_as_before(self):
        out = self.probe('''
out["dirs"] = [len(texts("worktreeChanges")), len(titles()), texts("mode_dirs"), texts("mode_branches")]
click("mode_branches")
out["branches"] = [len(texts("worktreeChanges")), calls()]
click("mode_dirs")
out["back"] = [len(texts("worktreeChanges")), len(titles()), calls()[-1]]
''')
        self.assertEqual(out["dirs"][:2], [2, 0], "Katalogi: dotychczasowe okno z akcjami wierszy")
        self.assertEqual(out["branches"], [0, [["setViewMode", "branches"], ["projects:1", "{}"]]])
        self.assertEqual(out["back"], [2, 0, ["setViewMode", "dirs"]])

    def test_starts_in_the_remembered_mode_and_project_and_asks_for_the_map(self):
        out = self.probe('''
out["first"] = calls()
answer("projects:1", PROJECTS)
out["chips"] = field("branchProject", "it.label")
out["calls"] = calls()
''', mode="branches", project="beta")
        self.assertEqual(out["first"], [["projects:1", "{}"]])
        self.assertEqual(out["chips"], ["Alfa · 5", "Beta · 0"])
        self.assertEqual(out["calls"][-1], ["map:2", '{"project":"beta"}'], "zapamiętany projekt, bez zapisu")
        self.assertEqual(len(out["calls"]), 2)

    def test_remembered_project_that_disappeared_falls_back_to_the_first_without_saving(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
out["calls"] = calls()
''', mode="branches", project="zniknal")
        self.assertEqual(out["calls"][-1], ["map:2", '{"project":"alfa"}'])
        self.assertNotIn("setBranchProject", [c[0] for c in out["calls"]])

    def test_choosing_a_project_saves_it_and_asks_for_its_map(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
answer("map:2", TREE)
click("branchProject", 1)
out["calls"] = calls()[2:]
out["tree"] = titles()
''', mode="branches", project="alfa", note="Nie zapisano ustawienia: brak miejsca.")
        self.assertEqual(out["calls"], [["setBranchProject", "beta"], ["map:3", '{"project":"beta"}']])
        self.assertEqual(out["tree"], [], "stara mapa znika do czasu odpowiedzi")

    def test_a_late_reply_for_the_previous_project_is_ignored(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
click("branchProject", 1)
answer("map:2", TREE)
out["tree"] = titles()
answer("map:3", dict(TREE, nodes=[], edges=[]))
out["empty"] = texts("branchEmpty")
''', mode="branches", project="alfa")
        self.assertEqual(out["tree"], [])
        self.assertEqual(out["empty"], ["Ten projekt nie ma jeszcze kart w przepływie."])

    def test_tree_shows_bases_cards_and_tickets_in_order_with_the_unknown_base_group(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
answer("map:2", TREE)
out["titles"] = titles()
out["states"] = field("branchNode", "it.detailText")
out["indent"] = field("branchNode", "it.x")
out["truncated"] = texts("branchTruncated")
out["empty"] = texts("branchEmpty") + texts("branchError")
''', mode="branches", project="alfa")
        self.assertEqual(out["titles"], ["master", "K-11 · Aktywna karta", "PT-3 · Ticket trzeci", "K-12 · Karta z karty",
                                         "K-10 · Scalona karta", "PT-1 · Ticket pierwszy", "PT-2 · Ticket drugi",
                                         "Nieznana baza", "K-5 · Stara karta"])
        self.assertEqual(out["states"], ["", "W toku · w toku", "w toku", "Do zrobienia · w toku",
                                         "Gotowe · scalona do master (11.11)", "scalony", "odrzucony", "",
                                         "Archiwum · domknięta"])
        self.assertEqual(out["indent"], [0, 24, 48, 48, 24, 48, 48, 0, 24])
        self.assertEqual((out["truncated"], out["empty"]), ([], []))

    def test_truncated_map_says_how_many_are_shown(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
answer("map:2", dict(TREE, truncated=True, total=480))
out["truncated"] = texts("branchTruncated")
''', mode="branches", project="alfa")
        self.assertEqual(out["truncated"], ["pokazano 7 z 480"])

    def test_project_without_cards_shows_the_empty_state(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
answer("map:2", {"ok": True, "project": "alfa", "repo": "/r/alfa", "nodes": [], "edges": [], "total": 0,
                 "truncated": False, "warnings": []})
out["empty"] = texts("branchEmpty")
out["titles"] = titles()
''', mode="branches", project="alfa")
        self.assertEqual(out["empty"], ["Ten projekt nie ma jeszcze kart w przepływie."])
        self.assertEqual(out["titles"], [])

    def test_no_projects_with_a_repository_shows_the_empty_state(self):
        out = self.probe('''
answer("projects:1", {"ok": True, "projects": []})
out["empty"] = texts("branchEmpty")
out["calls"] = calls()
''', mode="branches")
        self.assertEqual(out["empty"], ["Żaden projekt Tablicy nie ma repozytorium."])
        self.assertEqual(out["calls"], [["projects:1", "{}"]])

    def test_read_error_shows_the_service_message_as_plain_text(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
answer("map:2", {"ok": False, "error": "unknown-project", "message": "nieznany projekt Tablicy: <b>alfa</b>"})
out["message"] = texts("branchError")
out["format"] = field("branchError", "it.textFormat")
out["titles"] = titles()
''', mode="branches", project="alfa")
        self.assertEqual(out["message"], ["nieznany projekt Tablicy: <b>alfa</b>"])
        self.assertEqual(out["titles"], [])

    def test_project_list_error_shows_the_message(self):
        out = self.probe('''
answer("projects:1", {"ok": False, "error": "unavailable", "message": "Tablica jest wyłączona w usłudze"})
out["message"] = texts("branchError")
out["chips"] = field("branchProject", "it.label")
''', mode="branches")
        self.assertEqual((out["message"], out["chips"]), (["Tablica jest wyłączona w usłudze"], []))

    def test_titles_from_data_are_plain_text(self):
        out = self.probe('''
tree = dict(TREE)
tree["nodes"] = [dict(n, title="<b>pogrubione</b>") if n["id"] == "card:K-11" else n for n in TREE["nodes"]]
answer("projects:1", PROJECTS)
answer("map:2", tree)
out["format"] = field("branchTitle", "it.textFormat")
out["titles"] = titles()[1]
''', mode="branches", project="alfa")
        self.assertEqual(set(out["format"]), {0}, "Text.PlainText")  # Qt::PlainText == 0
        self.assertEqual(out["titles"], "K-11 · <b>pogrubione</b>")

    def test_open_card_emits_navigation_to_the_board_with_the_card_id(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
answer("map:2", TREE)
out["disabled"] = field("branchOpenCard", "it.enabled")
click("branchNode", 4)   # K-10
out["enabled"] = field("branchOpenCard", "it.enabled")
click("branchOpenCard")
out["calls"] = calls()[2:]
''', mode="branches", project="alfa")
        self.assertEqual((out["disabled"], out["enabled"]), ([False], [True]))
        self.assertEqual(out["calls"], [["navigate", "board", "K-10"]])

    def test_branch_changes_open_the_card_diff_source_and_escape_returns_to_the_map(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
answer("map:2", TREE)
click("branchNode", 1)   # K-11
click("branchChanges")
out["call"] = calls()[2:]
out["open"] = len(field("branchDiffView", "it.objectName"))
from PyQt6.QtCore import Qt
key(Qt.Key.Key_Escape)
out["after"] = [len(field("branchDiffView", "it.objectName")), len(titles())]
''', mode="branches", project="alfa")
        self.assertEqual(out["call"][0][1], json.dumps({"source": "card", "card": "K-11"}, separators=(",", ":")))
        self.assertEqual(out["open"], 1)
        self.assertEqual(out["after"], [0, 9], "Esc wraca do mapy z zachowanym drzewem")

    def test_only_card_branches_can_be_selected(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
answer("map:2", TREE)
click("branchNode", 2)   # ticket PT-3
click("branchNode", 0)   # baza
out["enabled"] = field("branchOpenCard", "it.enabled") + field("branchChanges", "it.enabled")
click("branchNode", 1)
click("branchNode", 1)   # drugi klik zdejmuje zaznaczenie
out["again"] = field("branchOpenCard", "it.enabled")
''', mode="branches", project="alfa")
        self.assertEqual(out["enabled"], [False, False])
        self.assertEqual(out["again"], [False])

    def test_board_change_refreshes_the_map_once_and_a_stale_selection_is_dropped(self):
        out = self.probe('''
answer("projects:1", PROJECTS)
answer("map:2", TREE)
click("branchNode", 1)
js("fake.boardChanged()"); js("fake.boardChanged()"); js("fake.boardChanged()")
wait(600)
out["calls"] = calls()[2:]
answer("projects:3", PROJECTS)
answer("map:4", dict(TREE, nodes=[n for n in TREE["nodes"] if n.get("card") != "K-11"]))
out["selected"] = field("branchOpenCard", "it.enabled")
''', mode="branches", project="alfa")
        self.assertEqual(out["calls"], [["projects:3", "{}"]], "trzy sygnały pod rząd to jedno odświeżenie")
        self.assertEqual(out["selected"], [False])

    def test_directories_mode_ignores_board_changes(self):
        out = self.probe('''
js("fake.boardChanged()")
wait(500)
out["calls"] = calls()
''')
        self.assertEqual(out["calls"], [])


class PackageTest(unittest.TestCase):
    def test_package_ships_the_branches_page_and_logic(self):
        from daemon.tests.fakebin import files_under
        with tempfile.TemporaryDirectory(prefix="tide-wt-branches-pkg-") as tmp:
            files = files_under(os.path.join(stage(os.path.join(tmp, "pkg")), "views", "worktrees"))
        for name in ("qml/BranchesPage.qml", "qml/branches.js", "windowconfig.py"):
            self.assertIn(name, files)


if __name__ == "__main__":
    unittest.main()
