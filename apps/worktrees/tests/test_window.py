"""Okno Tide Worktrees.

- pakiet: launcher, QML i biblioteka z Tokens.qml; bez kolorów wpisanych na sztywno,
- QML (qml6, offscreen) na przykładowych danych: grupy, filtry, szukanie, zaznaczanie, potwierdzenie
  i wynik usuwania; Main.qml z atrapą backendu,
- launcher: zrzut okna z --data (bez D-Bus) oraz Backend na prywatnej magistrali z prawdziwym
  tide-daemon i tymczasowymi repozytoriami — Remove() dostaje tylko zaznaczone ścieżki,
- instalacja i odinstalowanie na tymczasowym HOME z atrapami narzędzi.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import APP_DIR, REPO
from daemon.tests import gitrepos
from daemon.tests.fakebin import calls, files_under, make_env
from daemon.tests.privatebus import PrivateBus, requires_dbus, wait_until

NOW = 1_793_000_000
DAY = 86400
QML_PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|Cannot read"


def item(path, repo, group, **extra):
    """Wpis jak z List() tide-daemon (wszystkie pola, wartości domyślne dla czystego worktree)."""
    base = {
        "repo": repo, "repoPath": "/r/" + repo, "path": path, "name": os.path.basename(path), "branch": "feat/x",
        "detached": False, "head": "abcdef1", "isMain": False, "hasAgent": False, "dirty": 0, "mainBranch": "master",
        "merged": True, "unmerged": 0, "lastChange": "", "lastChangeTs": NOW - 5 * DAY, "size": 1_000_000,
        "locked": False, "missing": False, "removable": group == "clean", "blocker": "", "group": group,
        "title": os.path.basename(path), "tag": "feat", "subject": "commit " + os.path.basename(path), "tool": "",
        "sessionId": "", "sessionTitle": "", "sessionLastActivityTs": 0, "lastActivity": "",
        "lastActivityTs": NOW - 5 * DAY, "unpushed": 0,
    }
    base.update(extra)
    # Jak w usłudze: twarde blokady (main, protected, missing, locked, agent) wykluczają też RemoveForced.
    hard = base["blocker"] if base["blocker"] in ("main", "protected", "missing", "locked", "agent") else ""
    base.setdefault("forceBlocker", hard)
    base.setdefault("forceRemovable", hard == "")
    return base


def sample() -> list:
    items = [
        item("/r/alpha", "alpha", "main", isMain=True, branch="master", tag="", blocker="main"),
        item("/w/alpha/agent", "alpha", "active", hasAgent=True, tool="claude", sessionTitle="Popraw nagłówek listy",
             lastActivityTs=NOW - 120, blocker="agent", size=900_000_000),
        item("/w/alpha/vin", "alpha", "clean", branch="codex/fix-vin-count", tag="codex", tool="codex",
             title="fix · vin count", subject="test(vin-search): count limits", size=1_200_000_000),
        item("/w/beta/det", "beta", "clean", branch="", detached=True, head="e0e6181", tag="",
             title="odłączony HEAD e0e6181", size=800_000_000),
        item("/w/beta/small", "beta", "clean", size=100_000),
        item("/w/alpha/old", "alpha", "decide", unmerged=4, dirty=3, merged=False, blocker="dirty",
             lastActivityTs=NOW - 40 * DAY, lastChangeTs=NOW - 40 * DAY, unpushed=2, tool="claude",
             sessionTitle="Stara sesja"),
        item("/w/alpha/master-local", "alpha", "main", branch="master", tag="", blocker="protected"),
        item("/w/beta/gone", "beta", "missing", missing=True, size=-1, blocker="missing"),
    ]
    for i in range(12):
        items.append(item(f"/w/gamma/d{i}", "gamma", "decide", unmerged=i + 1, merged=False, blocker="unmerged",
                          lastActivityTs=NOW - (2 + i) * DAY))
    for repo in ("delta", "eps", "zeta", "eta"):
        items.append(item(f"/w/{repo}/x", repo, "decide", dirty=1, blocker="dirty"))
    return items


def stage(out: str) -> str:
    subprocess.run(["/usr/bin/python3", os.path.join(REPO, "widgets", "stage.py"), "--app", APP_DIR, out], check=True)
    return out


def run_qml(path: str) -> str:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
    result = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60)
    return result.stderr


def warned(log: str, tag: str) -> str:
    match = re.search(rf"{tag} (.*)$", log, re.M)
    return match.group(1).strip() if match else f"<brak {tag}>\n{log[-3000:]}"


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-wtwin-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = stage(os.path.join(self.tmp, "pkg"))

    def test_package_contains_launcher_qml_and_library(self):
        files = files_under(self.pkg)
        for name in ("tide-worktrees.py", "qml/Main.qml", "qml/WorktreesPage.qml", "qml/WorktreeItem.qml",
                     "qml/worktrees.js", "qml/tide/Tokens.qml", "qml/tide/Theme.qml", "qml/tide/qmldir"):
            self.assertIn(name, files)
        self.assertFalse([f for f in files if f.startswith("tests/") or f.endswith(("__init__.py", "install.sh"))])

    def test_no_hardcoded_colors(self):
        for dirpath, _dirs, names in os.walk(os.path.join(APP_DIR, "qml")):
            for name in names:
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    found = re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read())
                self.assertEqual(found, [], f"kolor wpisany na sztywno w {name}")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class QmlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-wtwin-qml-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = stage(os.path.join(self.tmp, "pkg"))

    def harness(self, name: str, body: str) -> str:
        path = os.path.join(self.pkg, "qml", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return run_qml(path)

    def test_page_groups_filters_selection_and_removal(self):
        log = self.harness("PageSmoke.qml", """import QtQuick
import "tide" as Tide
import "worktrees.js" as Logic
Item {
    id: root
    width: 1180; height: 900
    property var removed: null
    property var opened: []
    WorktreesPage {
        id: page
        anchors.fill: parent
        nowTs: %(now)d
        homeDir: "/w"
        worktrees: %(data)s
        launchers: ({ terminal: true, rider: true })
        onRemoveRequested: paths => root.removed = paths
        onOpenRequested: (path, app) => root.opened.push(app + ":" + path)
    }
    Component.onCompleted: {
        const out = []
        console.warn("GROUPS " + [page.active.length, page.clean.length, page.decide.length, page.mains.length,
                                  page.missing.length].join("|"))
        console.warn("SUMMARY " + [page.sum.count, page.sum.repos, page.sum.cleanCount, page.sum.cleanBytes,
                                   page.sum.activeCount].join("|"))
        console.warn("CHIPS " + page.chips.map(c => c.label + ":" + c.count).join(","))
        console.warn("WHY " + Logic.whyText(page.decide.filter(w => w.path === "/w/alpha/old")[0]) + "|"
                     + Logic.whyText({ unmerged: 1, dirty: 0, mainBranch: "m" }) + "|"
                     + Logic.whyText({ unmerged: 12, dirty: 22, mainBranch: "m" }))
        console.warn("OLD " + Logic.isOld(page.decide.filter(w => w.path === "/w/alpha/old")[0], page.nowTs))

        page.toggle("/w/alpha/vin")
        page.toggle("/w/beta/small")
        page.toggle("/w/alpha/master-local")  // gałąź główna — nie da się zaznaczyć
        page.toggle("/w/alpha/agent")         // pracuje agent — nie da się zaznaczyć
        console.warn("PICK " + page.picked.items.length + "|" + page.picked.bytes + "|" + page.forcing)
        page.askRemove()
        console.warn("CONFIRM " + page.confirming)
        page.confirmRemove()
        console.warn("REMOVE " + root.removed.join(",") + "|" + page.removing + "|" + page.confirming)
        page.finishRemove([
            { path: "/w/alpha/vin", removed: true, freed: 1200000000, branch: "codex/fix-vin-count",
              branchDeleted: true, branchMessage: "gałąź usunięta" },
            { path: "/w/beta/small", removed: false, reason: "dirty", message: "ma niezacommitowane zmiany (1)" }])
        console.warn("RESULT " + [page.results.removed, page.results.skipped, page.results.freed,
                                  Object.keys(page.selected).join(";"), page.removing].join("|"))
        console.warn("DETAILS " + page.results.details.join(";"))
        page.finishRemove(null)
        console.warn("NOREPLY " + page.results.skipped + "|" + page.removing)

        page.toggleAllShown()
        console.warn("ALL " + page.picked.items.length + "|" + page.allShownPicked)
        page.toggleAllShown()
        console.warn("NONE " + page.picked.items.length)

        page.query = "vin count"
        console.warn("SEARCH " + page.shown.map(w => w.path).join(","))
        page.query = "nagłówek"
        console.warn("SESSION " + page.shown.map(w => w.path).join(","))
        page.query = ""
        page.repo = "/r/beta"
        console.warn("REPO " + page.shown.length + "|" + page.clean.length)
        page.repo = "other"
        console.warn("OTHER " + page.shown.map(w => w.repo).join(","))
        page.repo = "all"
        page.showAllDecide = true
        page.showMain = true
        page.finishPrune({ ok: true, pruned: ["/w/beta/gone"], message: "" })
        console.warn("PRUNE " + page.notice)
        console.warn("PATH " + Logic.tildePath("/w/alpha/vin", "/w") + "|" + Logic.tildePath("/x/y", "/w"))
        console.warn("FATE " + Logic.branchFate({ branch: "", head: "e0e6181" }).indexOf("odłączony") + "|"
                     + Logic.branchFate({ branch: "feat/x" }).indexOf("git branch -d"))
        Qt.quit()
    }
}
""" % {"now": NOW, "data": json.dumps(sample(), ensure_ascii=False)})
        self.assertEqual(warned(log, "GROUPS"), "1|3|17|1|1")
        self.assertEqual(warned(log, "SUMMARY"), "23|7|3|2000100000|1")
        self.assertEqual(warned(log, "CHIPS"),
                         "Wszystkie:23,gamma:12,alpha:4,beta:3,delta:1,eps:1,pozostałe:2")
        self.assertEqual(warned(log, "WHY"), "4 commity · 3 zmiany|1 commit|12 commitów · 22 zmiany")
        self.assertEqual(warned(log, "OLD"), "true")
        self.assertEqual(warned(log, "PICK"), "2|1200100000|false")
        self.assertEqual(warned(log, "CONFIRM"), "true")
        self.assertEqual(warned(log, "REMOVE"), "/w/alpha/vin,/w/beta/small|true|false")
        self.assertEqual(warned(log, "RESULT"), "1|1|1200000000|/w/beta/small|false")
        self.assertEqual(warned(log, "DETAILS"), "small · beta: ma niezacommitowane zmiany (1)")
        self.assertEqual(warned(log, "NOREPLY"), "1|false")
        self.assertEqual(warned(log, "ALL"), "3|true")
        self.assertEqual(warned(log, "NONE"), "0")
        self.assertEqual(warned(log, "SEARCH"), "/w/alpha/vin")
        self.assertEqual(warned(log, "SESSION"), "/w/alpha/agent")
        self.assertEqual(warned(log, "REPO"), "3|2")
        self.assertEqual(warned(log, "OTHER"), "zeta,eta")
        self.assertEqual(warned(log, "PRUNE"), "Usunięto wpisy bez katalogu: 1")
        self.assertEqual(warned(log, "PATH"), "~/alpha/vin|/x/y")
        fate = warned(log, "FATE").split("|")
        self.assertTrue(all(int(x) >= 0 for x in fate), fate)
        self.assertEqual([l for l in log.splitlines() if re.search(QML_PROBLEMS, l)], [])

    def test_forced_confirmation_lists_what_would_be_lost(self):
        """Nie-sugerowany worktree: mocniejsze potwierdzenie wymienia, co przepadnie (zmiany,
        commity poza master i bez kopii, los gałęzi, sesję, ostatnią zmianę), pole „usuń też
        gałąź” i czerwony „Usuń mimo to”; potem RemoveForced ze stanem z potwierdzenia."""
        data = sample() + [
            item("/w/beta/topic", "beta", "decide", branch="topic", mainBranch="", unmerged=None, merged=False,
                 blocker="no-main", unpushed=3),
            item("/w/gamma/det", "gamma", "decide", branch="", detached=True, head="1234567", unmerged=2,
                 merged=False, blocker="unmerged", unpushed=2),
            item("/w/gamma/locked", "gamma", "decide", locked=True, blocker="locked"),
        ]
        log = self.harness("ForceSmoke.qml", """import QtQuick
import "tide" as Tide
import "worktrees.js" as Logic
Item {
    id: root
    width: 1180; height: 1100
    property var forced: null
    property var plain: null
    WorktreesPage {
        id: page
        anchors.fill: parent
        nowTs: %(now)d
        homeDir: "/w"
        worktrees: %(data)s
        onRemoveRequested: paths => root.plain = paths
        onRemoveForcedRequested: (paths, deleteBranches, confirmed) => root.forced = { paths: paths,
                                                                                       deleteBranches: deleteBranches,
                                                                                       confirmed: JSON.parse(confirmed) }
    }
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) {
            const hit = find(child, name)
            if (hit) return hit
        }
        return null
    }
    // Widoczne teksty pod elementem — to, co naprawdę jest w oknie potwierdzenia.
    function texts(item, out, name) {
        if (!item.visible) return out
        if (typeof item.text === "string" && item.text !== "" && (!name || item.objectName === name)) out.push(item.text)
        for (const child of item.children) texts(child, out, name)
        return out
    }
    Component.onCompleted: {
        console.warn("SELECTABLE " + ["/w/alpha/old", "/w/alpha/vin", "/w/alpha/agent", "/w/alpha/master-local",
                                      "/r/alpha", "/w/beta/gone", "/w/gamma/locked", "/w/beta/topic"]
                     .map(p => Logic.selectable(page.worktrees.find(w => w.path === p))).join("|"))
        console.warn("LOCK " + Logic.lockReason(page.worktrees.find(w => w.path === "/w/alpha/agent")))
        page.toggle("/w/alpha/old")
        page.toggle("/w/alpha/vin")
        page.toggle("/w/alpha/master-local")
        console.warn("PICKED " + [page.picked.items.length, page.picked.risky.length, page.forcing].join("|"))
        page.askRemove()
        const dialog = find(page, "confirmDialog")
        console.warn("DIALOG " + texts(dialog, []).join(" ¦ "))
        console.warn("DANGER " + texts(dialog, [], "lossDanger").length)
        console.warn("TOGGLE " + find(page, "deleteBranchesToggle").visible + "|" + page.deleteBranches)
        page.deleteBranches = true
        console.warn("DANGER2 " + texts(dialog, [], "lossDanger").join(" ¦ "))
        page.confirmRemove()
        console.warn("FORCED " + root.forced.paths.join(",") + "|" + root.forced.deleteBranches + "|"
                     + JSON.stringify(root.forced.confirmed["/w/alpha/old"]) + "|" + (root.plain === null)
                     + "|" + page.confirming + "|" + page.removing)
        page.finishRemove([
            { path: "/w/alpha/old", removed: true, forced: true, freed: 10, branch: "feat/x", branchDeleted: false,
              branchMessage: "gałąź feat/x zostaje (4 commity poza master)", status: "branch-kept" },
            { path: "/w/alpha/vin", removed: false, forced: true, reason: "changed", status: "refused",
              message: "stan zmienił się od potwierdzenia — nic nie usunięto, sprawdź jeszcze raz" }])
        console.warn("RESULTS " + [page.results.removed, page.results.branchKept, page.results.skipped].join("|"))
        console.warn("RDETAILS " + page.results.details.join(" ¦ "))

        // Bez zaznaczonego pola przy samych gałęziach bez gałęzi głównej i z odłączonym HEAD.
        page.selected = ({})
        page.toggle("/w/beta/topic")
        page.toggle("/w/gamma/det")
        page.askRemove()
        console.warn("NOMAIN " + texts(find(page, "confirmDialog"), []).join(" ¦ "))
        page.confirmRemove()
        console.warn("NOFLAG " + root.forced.deleteBranches)
        console.warn("LABEL " + Logic.deleteBranchLabel({ count: 2, commits: 5, unknown: 1 }) + " ¦ "
                     + Logic.deleteBranchLabel({ count: 1, commits: 1, unknown: 0 }))
        Qt.quit()
    }
}
""" % {"now": NOW, "data": json.dumps(data, ensure_ascii=False)})
        self.assertEqual([l for l in log.splitlines() if re.search(QML_PROBLEMS, l)], [])
        self.assertEqual(warned(log, "SELECTABLE"), "true|true|false|false|false|false|false|true")
        self.assertIn("zamknij sesję", warned(log, "LOCK"))
        self.assertEqual(warned(log, "PICKED"), "2|1|true")
        dialog = warned(log, "DIALOG")
        for expected in (
            "⚠ Usunąć mimo to 2 worktrees? 1 z nich nie jest sugerowany do usunięcia.",
            "git worktree remove --force",
            "⚠ old · alpha",
            "✕ 3 niezacommitowane zmiany przepadną (także pliki nieśledzone)",
            "· 4 commity poza master, z tego 2 bez kopii na serwerze ani w innej gałęzi",
            "· gałąź feat/x zostaje (usuniesz ją polem niżej)",
            "· sesja Claude „Stara sesja” — agent już w nim nie działa, rozmowa zostaje w historii",
            "· ostatnia zmiana ",
            "• fix · vin count · alpha",
            "· bez ryzyka — scalony z master, bez zmian",
            "Usuń też gałąź (4 niescalone commity przepadną)",
            "Nigdy nie usuwam głównego katalogu repozytorium ani gałęzi głównych",
            "Anuluj",
            "Usuń mimo to",
        ):
            self.assertIn(expected, dialog)
        self.assertEqual(warned(log, "DANGER"), "1")
        self.assertEqual(warned(log, "TOGGLE"), "true|false")
        self.assertEqual(warned(log, "DANGER2"),
                         "✕ 3 niezacommitowane zmiany przepadną (także pliki nieśledzone) ¦ "
                         "✕ 4 commity poza master, z tego 2 bez kopii na serwerze ani w innej gałęzi ¦ "
                         "✕ gałąź feat/x zostanie usunięta (git branch -D)")
        forced = warned(log, "FORCED").split("|")
        self.assertEqual(forced[0], "/w/alpha/vin,/w/alpha/old")  # kolejność listy, nie kliknięć
        self.assertEqual(forced[1], "true")
        self.assertEqual(json.loads(forced[2]), {"dirty": 3, "unmerged": 4, "unpushed": 2, "branch": "feat/x"})
        self.assertEqual(forced[3:], ["true", "false", "true"])
        self.assertEqual(warned(log, "RESULTS"), "1|1|1")
        self.assertEqual(warned(log, "RDETAILS"),
                         "fix · vin count · alpha: stan zmienił się od potwierdzenia — nic nie usunięto, sprawdź jeszcze raz"
                         " ¦ old · alpha: usunięty, gałąź feat/x zostaje (4 commity poza master)")
        nomain = warned(log, "NOMAIN")
        self.assertIn("brak lokalnej gałęzi głównej — nie wiadomo, co jest scalone; 3 commity tylko tutaj", nomain)
        self.assertIn("✕ odłączony HEAD 1234567: te commity zostaną bez gałęzi (odzyskasz je tylko z refloga)", nomain)
        self.assertIn("Usuń też gałąź (bez gałęzi głównej nie wiadomo, ile commitów przepadnie)", nomain)
        self.assertEqual(warned(log, "NOFLAG"), "false")
        self.assertEqual(warned(log, "LABEL"),
                         "Usuń też gałęzie (2) (5 niescalonych commitów przepadnie; w 1 bez gałęzi głównej nie wiadomo, "
                         "ile commitów przepadnie) ¦ Usuń też gałąź (1 niescalony commit przepadnie)")

    def test_main_window_with_stub_backend(self):
        log = self.harness("MainSmoke.qml", """import QtQuick
Item {
    QtObject {
        id: stub
        property string listJson: %(json)s
        property bool available: true
        property string launchersJson: '{"terminal": true, "rider": false, "window": true}'
        property string homeDir: "/w"
        property var removed: []
        signal removeFinished(string text)
        signal pruneFinished(string text)
        signal raiseRequested()
        property var forced: []
        function remove(paths) { removed = paths; removeFinished('[{"path": "/w/alpha/vin", "removed": true, "freed": 5}]') }
        function removeForced(paths, deleteBranches, confirmed) { forced = [paths.join(","), deleteBranches] }
        function open(path, app) {}
        function refresh() {}
        function prune(repo) { pruneFinished("null") }
    }
    Main { id: win; backend: stub; visible: false }
    Component.onCompleted: {
        const page = win.contentItem.children[0]
        page.toggle("/w/alpha/vin")
        page.confirmRemove()
        page.pruneRequested("/r/beta")
        stub.raiseRequested()
        console.warn("MAIN " + [win.worktrees.length, win.launchers.rider, stub.removed.join(","),
                                page.results.removed, page.notice].join("|"))
        page.toggle("/w/alpha/old")
        page.askRemove()
        page.confirmRemove()
        console.warn("FORCEDCALL " + stub.forced.join("|"))
        Qt.quit()
    }
}
""" % {"json": json.dumps(json.dumps(sample()))})
        self.assertEqual(warned(log, "MAIN"), "24|false|/w/alpha/vin|1|Nie udało się: brak odpowiedzi usługi")
        self.assertEqual(warned(log, "FORCEDCALL"), "/w/alpha/old|false")
        self.assertEqual([l for l in log.splitlines() if re.search(QML_PROBLEMS, l)], [])


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-wtwin-run-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = stage(os.path.join(self.tmp, "pkg"))

    def test_grab_with_static_data(self):
        data = os.path.join(self.tmp, "data.json")
        with open(data, "w", encoding="utf-8") as fh:
            json.dump(sample(), fh)
        shot = os.path.join(self.tmp, "shot.png")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)  # --data nie potrzebuje magistrali
        subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide-worktrees.py"), "--data", data,
                        "--grab", shot, "--width", "900", "--height", "700"], env=env, check=True, timeout=60,
                       capture_output=True)
        with open(shot, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertGreater(os.path.getsize(shot), 10_000)

    def test_grab_forced_confirmation_preview(self):
        """--select i --confirm: podgląd mocniejszego potwierdzenia (z --data nic nie jest usuwane)."""
        data = os.path.join(self.tmp, "data.json")
        with open(data, "w", encoding="utf-8") as fh:
            json.dump(sample(), fh)
        plain, confirm = os.path.join(self.tmp, "plain.png"), os.path.join(self.tmp, "confirm.png")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        base = ["/usr/bin/python3", os.path.join(self.pkg, "tide-worktrees.py"), "--data", data,
                "--width", "1000", "--height", "800", "--select", "/w/alpha/old", "--select", "/w/alpha/vin"]
        subprocess.run(base + ["--grab", plain], env=env, check=True, timeout=60, capture_output=True)
        subprocess.run(base + ["--confirm", "--grab", confirm], env=env, check=True, timeout=60, capture_output=True)
        with open(plain, "rb") as a, open(confirm, "rb") as b:
            self.assertNotEqual(a.read(), b.read(), "z --confirm zrzut pokazuje okno potwierdzenia")


def load_launcher():
    spec = importlib.util.spec_from_file_location("tide_worktrees_window", os.path.join(APP_DIR, "tide-worktrees.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@requires_dbus
@unittest.skipUnless(shutil.which("git"), "brak gita")
class BackendOverDbusTest(unittest.TestCase):
    """Backend okna na prywatnej magistrali z prawdziwym tide-daemon: lista, Remove() tylko dla
    zaznaczonych ścieżek, sygnał wyniku i drugie uruchomienie, które tylko prosi o pokazanie okna."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-wtwin-dbus-")
        cls.root = os.path.join(cls.tmp, "work")
        cls.p = gitrepos.build(cls.root)
        cls.service = PrivateBus("tide-wtwin-test")
        cls.service.env["TIDE_WORKTREES_WINDOW"] = os.path.join(cls.tmp, "brak-okna.py")
        cls.service.spawn_daemon("--claude-dir", os.path.join(cls.tmp, "claude"), "--codex-dir",
                                 os.path.join(cls.tmp, "codex"), "--no-processes", "--no-notifications",
                                 "--workspace", cls.root)
        cls.launcher = load_launcher()

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_list_then_remove_only_selected(self):
        backend = self.launcher.Backend(self.service.bus)
        listed = wait_until(lambda: [w for w in json.loads(backend.listJson) if w["path"] == self.p["detached"]],
                            timeout=15)
        self.assertTrue(listed and backend.available)
        self.assertEqual(listed[0]["group"], "clean")
        results = []
        backend.removeFinished.connect(results.append)
        backend.remove([self.p["detached"]])
        self.assertTrue(wait_until(lambda: results, timeout=30))
        got = json.loads(results[0])
        self.assertEqual([(r["path"], r["removed"]) for r in got], [(self.p["detached"], True)])
        self.assertFalse(os.path.exists(self.p["detached"]))
        self.assertTrue(os.path.isdir(self.p["merged"]), "niezaznaczony scalony worktree zostaje")
        gone = wait_until(lambda: self.p["detached"] not in {w["path"] for w in json.loads(backend.listJson)},
                          timeout=15)
        self.assertTrue(gone, "lista w oknie odświeża się po sygnale Changed")
        self.assertIn("rider", json.loads(backend.launchersJson))

    def test_remove_forced_through_backend(self):
        """Backend.removeForced → RemoveForced(as, b, s): worktree ze zmianami znika po potwierdzeniu,
        główny katalog nie — nawet gdy okno by o niego poprosiło."""
        backend = self.launcher.Backend(self.service.bus)
        listed = wait_until(lambda: {w["path"]: w for w in json.loads(backend.listJson)}.get(self.p["dirty"]),
                            timeout=15)
        self.assertTrue(listed)
        self.assertEqual((listed["removable"], listed["forceRemovable"]), (False, True))
        results = []
        backend.removeFinished.connect(results.append)
        confirmed = {self.p["dirty"]: {"dirty": listed["dirty"], "unmerged": listed["unmerged"],
                                       "unpushed": listed["unpushed"], "branch": listed["branch"]}}
        backend.removeForced([self.p["dirty"], self.p["alpha"]], False, json.dumps(confirmed))
        self.assertTrue(wait_until(lambda: results, timeout=30))
        got = json.loads(results[0])
        self.assertEqual([(r["path"], r["status"], r["reason"]) for r in got],
                         [(self.p["dirty"], "removed", ""), (self.p["alpha"], "refused", "main")])
        self.assertFalse(os.path.exists(self.p["dirty"]))
        self.assertTrue(os.path.isdir(self.p["alpha"]))

    def test_second_launch_only_raises_first_window(self):
        env = dict(self.service.env, QT_QPA_PLATFORM="offscreen")
        # Pierwsze okno: nazwa org.tide.WorktreesWindow zajęta przez atrapę z obiektem /Window.
        first = load_launcher()
        backend = first.Backend(None, [])
        raised = []
        backend.raiseRequested.connect(lambda: raised.append(True))
        service = first.WindowService(backend)
        from PyQt6.QtDBus import QDBusConnection
        self.assertTrue(self.service.bus.registerService(first.WINDOW_SERVICE))
        self.service.bus.registerObject(first.WINDOW_PATH, service, QDBusConnection.RegisterOption.ExportAllSlots)
        try:
            proc = subprocess.Popen(["/usr/bin/python3", os.path.join(APP_DIR, "tide-worktrees.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.assertTrue(wait_until(lambda: raised, timeout=15), "drugie uruchomienie nie poprosiło o Raise")
            self.assertEqual(proc.wait(15), 0)
        finally:
            self.service.bus.unregisterObject(first.WINDOW_PATH)
            self.service.bus.unregisterService(first.WINDOW_SERVICE)


@unittest.skipUnless(shutil.which("make"), "brak make")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-wtwin-install-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.data = self.env["XDG_DATA_HOME"]

    def make(self, target):
        subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True, text=True)

    def test_install_then_uninstall(self):
        self.make("install-worktrees-window")
        dest = os.path.join(self.data, "tide/worktrees-window")
        files = files_under(dest)
        for name in ("tide-worktrees.py", "qml/Main.qml", "qml/WorktreesPage.qml", "qml/tide/Tokens.qml"):
            self.assertIn(name, files)
        self.assertTrue(os.access(os.path.join(dest, "tide-worktrees.py"), os.X_OK))
        desktop = os.path.join(self.data, "applications/tide-worktrees.desktop")
        with open(desktop, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Name=Tide Worktrees\n", text)
        self.assertIn(f"Exec=/usr/bin/python3 -s {dest}/tide-worktrees.py\n", text)
        self.assertIn("StartupWMClass=tide-worktrees\n", text)
        self.assertIn("Icon=tide-worktrees\n", text)
        icon = os.path.join(self.data, "icons/hicolor/256x256/apps/tide-worktrees.png")
        with open(icon, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        if shutil.which("desktop-file-validate"):
            check = subprocess.run(["desktop-file-validate", desktop], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn(f"update-desktop-database {self.data}/applications", calls(self.env))
        self.assertIn("kbuildsycoca6", [c.strip() for c in calls(self.env)])

        # Reinstalacja (aktualizacja) nie zostawia starych plików.
        stale = os.path.join(dest, "qml", "Stary.qml")
        open(stale, "w").close()
        self.make("install-worktrees-window")
        self.assertFalse(os.path.exists(stale))

        self.make("uninstall-worktrees-window")
        self.assertEqual(files_under(self.data), [])

    def test_uninstall_keeps_foreign_desktop_file_and_daemon(self):
        os.makedirs(os.path.join(self.data, "applications"))
        foreign = os.path.join(self.data, "applications/tide-worktrees.desktop")
        with open(foreign, "w", encoding="utf-8") as fh:
            fh.write("[Desktop Entry]\nName=Cudze\n")
        daemon_file = os.path.join(self.data, "tide/daemon/x")
        os.makedirs(os.path.dirname(daemon_file))
        open(daemon_file, "w").close()
        self.make("uninstall-worktrees-window")
        self.assertTrue(os.path.exists(foreign))
        self.assertTrue(os.path.exists(daemon_file))


if __name__ == "__main__":
    unittest.main()
