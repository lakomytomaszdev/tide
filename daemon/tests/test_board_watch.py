"""Obserwacja plików Tablicy bez pełnego przejścia drzewa (K-98 #6, PT-362): liczniki i utrata zdarzeń.

Testy nie mierzą czasu. Liczniki: porównania ścieżek i elementy zbiorów (`_rewatch` liniowy), przejrzane katalogi
(`watch_stats["visitedDirs"]` — `sync()`/`_mutate()` bez zmiany struktury nie przechodzą drzewa). Utrata zdarzeń:
prawdziwy `QFileSystemWatcher` (inotify) dla zmian z zewnątrz w obserwowanych grupach ścieżek (karty, projekty,
docs/), poddrzewo powstałe w trakcie przejścia i atrapa obserwatora odrzucająca ścieżki (limit) — wtedy migawkę daje
odpytywanie co POLL_MS. Grupa wyłączona z obserwacji (`flow/**`): żadna ścieżka nie jest obserwowana, zapis nie budzi
synchronizacji, a commit i migawkę daje odpytywanie (dziennik i wyjście kroków commituje i publikuje sama usługa)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from PyQt6.QtCore import QObject, pyqtSignal

from .privatebus import app, wait_until


def _git_config(tmp):
    config = os.path.join(tmp, "gitconfig")
    with open(config, "w", encoding="utf-8") as fh:
        fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
    return {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1"}


def _git_log(root):
    return subprocess.run(["git", "-C", root, "log", "--format=%s"], capture_output=True, text=True).stdout


class Counted(str):
    """Ścieżka z atrapy obserwatora, która liczy porównania (hash i ==)."""
    ops = 0

    def __hash__(self):
        Counted.ops += 1
        return str.__hash__(self)

    def __eq__(self, other):
        Counted.ops += 1
        return str.__eq__(self, other)


class CountingSet(set):
    """`set` w module board_monitor: liczy elementy wszystkich budowanych zbiorów."""
    elements = 0

    def __init__(self, items=()):
        items = list(items)
        CountingSet.elements += len(items)
        super().__init__(items)


class FakeWatcher(QObject):
    """Atrapa QFileSystemWatcher: pamięta ścieżki, nie wysyła zdarzeń sama; `refuse` — odrzuca każdą ścieżkę."""
    directoryChanged = pyqtSignal(str)
    fileChanged = pyqtSignal(str)

    def __init__(self, refuse=False, counted=False):
        super().__init__()
        self.refuse, self.counted = refuse, counted
        self.dirs, self.file_paths = set(), set()
        self.listings = 0
        self.refused = 0

    def _out(self, paths):
        return [Counted(p) if self.counted else p for p in sorted(paths)]

    def directories(self):
        self.listings += 1
        return self._out(self.dirs)

    def files(self):
        return self._out(self.file_paths)

    def addPaths(self, paths):
        if self.refuse:
            self.refused += len(paths)
            return list(paths)
        for path in paths:
            (self.dirs if os.path.isdir(path) else self.file_paths).add(str(path))
        return []

    def removePaths(self, paths):
        for path in paths:
            self.dirs.discard(str(path))
            self.file_paths.discard(str(path))
        return []

    def all(self):
        return self.dirs | self.file_paths


class WatchCase(unittest.TestCase):
    def setUp(self):
        app()
        self.tmp = tempfile.mkdtemp(prefix="tide-board-watch-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, _git_config(self.tmp))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = os.path.join(self.tmp, "board")

    def monitor(self, watcher=None, debounce_ms=100, poll_ms=60_000):
        from tide_daemon.board.store import BoardStore
        from tide_daemon.board_monitor import BoardMonitor
        monitor = BoardMonitor(BoardStore(self.root), debounce_ms=debounce_ms, poll_ms=poll_ms, watcher=watcher)
        self.addCleanup(monitor.stop)
        return monitor

    def board(self, monitor):
        """Tablica z projektem i jedną kartą, zapisana przez usługę."""
        repo = os.path.join(self.tmp, "ws", "proj")
        os.makedirs(repo)
        subprocess.run(["git", "-C", repo, "init", "-q"], check=True)
        monitor.start()
        self.assertTrue(json.loads(monitor.upsert_project(json.dumps({"repo": repo})))["ok"])
        reply = json.loads(monitor.create_card(json.dumps({"title": "Pierwsza", "project": "proj"})))
        self.assertTrue(reply["ok"], reply)

    def write(self, rel, text, mode="w"):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, encoding="utf-8") as fh:
            fh.write(text)
        return path

    def read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            return fh.read()

    def snap(self, monitor):
        return json.loads(monitor.snapshot_json())

    def external_card(self, num, title):
        """Nowa karta napisana z zewnątrz (kopia formatu K-1 z innym ID i tytułem)."""
        text = self.read(os.path.join("cards", "K-1.md")).replace("K-1", f"K-{num}").replace("Pierwsza", title)
        self.write(os.path.join("cards", f"K-{num}.md"), text)


class RewatchCostTest(WatchCase):
    def tree(self, dirs):
        for index in range(dirs):
            folder = os.path.join(self.root, "docs", f"p{index // 20}", f"d{index}")
            os.makedirs(folder)
            for name in ("a.md", "b.md"):
                with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                    fh.write("x\n")

    def cost(self, dirs):
        from tide_daemon import board_monitor
        shutil.rmtree(self.root, ignore_errors=True)
        self.tree(dirs)
        watcher = FakeWatcher(counted=True)
        monitor = self.monitor(watcher)
        monitor._rewatch()
        paths = len(watcher.all())
        Counted.ops = CountingSet.elements = 0
        listings = watcher.listings
        with mock.patch.object(board_monitor, "set", CountingSet, create=True):
            monitor._rewatch(full=True)
        self.assertEqual(watcher.listings - listings, 1, "zbiór obserwowanych ścieżek budowany raz na przebieg")
        return paths, Counted.ops, CountingSet.elements

    def test_rewatch_compares_each_path_a_bounded_number_of_times(self):
        small = self.cost(100)
        large = self.cost(400)
        for paths, ops, elements in (small, large):
            self.assertGreater(paths, 300)
            self.assertLessEqual(ops, 4 * paths, (paths, ops))          # O(N) porównań z obserwowanymi
            self.assertLessEqual(elements, 4 * paths, (paths, elements))  # wszystkie zbiory razem O(N) elementów
        # Czterokrotnie więcej ścieżek — najwyżej ~czterokrotnie więcej pracy (stara pętla: kwadratowo).
        self.assertLessEqual(large[2], 5 * small[2])


class NoWalkTest(WatchCase):
    def test_sync_and_mutate_without_structure_change_visit_no_directory(self):
        watcher = FakeWatcher()
        monitor = self.monitor(watcher)
        self.board(monitor)
        self.write(os.path.join("flow", "K-1", "steps", "s1", "out", "note.md"), "x\n")
        monitor.sync(full=True)
        stats = dict(monitor.watch_stats)
        monitor.sync()
        monitor.move_card("K-1", "todo", 0)                 # _mutate
        monitor.update_card("K-1", json.dumps({"note": "uwaga"}))
        self.assertEqual(monitor.watch_stats["visitedDirs"], stats["visitedDirs"])
        self.assertEqual(monitor.watch_stats["fullScans"], stats["fullScans"])

    def test_change_reviews_only_the_changed_subtree(self):
        watcher = FakeWatcher()
        monitor = self.monitor(watcher)
        self.board(monitor)
        before = monitor.watch_stats["visitedDirs"]
        self.external_card(2, "Druga")
        watcher.directoryChanged.emit(os.path.join(self.root, "cards"))
        monitor.sync()
        self.assertEqual(monitor.watch_stats["visitedDirs"] - before, 1)
        self.assertIn(os.path.join(self.root, "cards", "K-2.md"), watcher.file_paths)
        before = monitor.watch_stats["visitedDirs"]
        self.write(os.path.join("docs", "proj", "plans", "plan.md"), "# plan\n")
        watcher.directoryChanged.emit(os.path.join(self.root, "docs"))
        monitor.sync()
        # docs/ oraz nowe docs/proj i docs/proj/plans — bez reszty drzewa
        self.assertEqual(monitor.watch_stats["visitedDirs"] - before, 3)
        self.assertIn(os.path.join(self.root, "docs", "proj", "plans", "plan.md"), watcher.file_paths)
        before = monitor.watch_stats["visitedDirs"]
        os.unlink(os.path.join(self.root, "cards", "K-2.md"))
        watcher.directoryChanged.emit(os.path.join(self.root, "cards"))
        monitor.sync()
        self.assertEqual(monitor.watch_stats["visitedDirs"] - before, 1)
        self.assertNotIn(os.path.join(self.root, "cards", "K-2.md"), watcher.file_paths)

    def test_removed_subtree_leaves_the_watcher(self):
        watcher = FakeWatcher()
        monitor = self.monitor(watcher)
        self.board(monitor)
        self.write(os.path.join("docs", "proj", "plans", "plan.md"), "# plan\n")
        monitor.sync(full=True)
        self.assertIn(os.path.join(self.root, "docs", "proj", "plans"), watcher.dirs)
        shutil.rmtree(os.path.join(self.root, "docs", "proj"))
        watcher.directoryChanged.emit(os.path.join(self.root, "docs"))
        monitor.sync()
        self.assertFalse([p for p in watcher.all() if "docs/proj" in p])


class LiveWatchTest(WatchCase):
    """Prawdziwy QFileSystemWatcher: zmiana z zewnątrz → (po DEBOUNCE_MS) synchronizacja i nowa migawka."""

    def setUp(self):
        super().setUp()
        self.mon = self.monitor()
        self.board(self.mon)
        wait_until(lambda: False, timeout=0.4)    # zdarzenia własnych zapisów usługi już obsłużone

    def tearDown(self):
        from PyQt6.QtCore import QCoreApplication, QEvent
        self.mon.stop()
        watcher = self.mon._watcher
        watcher.deleteLater()
        QCoreApplication.sendPostedEvents(watcher, QEvent.Type.DeferredDelete)

    def watched(self):
        return set(self.mon._watcher.directories()) | set(self.mon._watcher.files())

    def published(self):
        """Ostatnia opublikowana migawka (to, co niesie Changed) — bez wymuszania publikacji przez Snapshot()."""
        return json.loads(self.mon._json)

    def cards(self):
        return {c["id"]: c for c in self.published()["cards"]}

    def test_card_file_change_new_card_and_card_removal(self):
        self.write(os.path.join("cards", "K-1.md"), "\nDopisek z zewnątrz.\n", mode="a")
        self.assertTrue(wait_until(lambda: "Dopisek z zewnątrz." in self.mon._json, timeout=5))
        self.external_card(5, "Z zewnątrz")
        self.assertTrue(wait_until(lambda: "K-5" in self.cards(), timeout=5))
        self.assertTrue(wait_until(lambda: os.path.join(self.root, "cards", "K-5.md") in self.watched(), timeout=5))
        os.unlink(os.path.join(self.root, "cards", "K-5.md"))
        self.assertTrue(wait_until(lambda: "K-5" not in self.cards(), timeout=5))
        self.assertEqual(_git_log(self.root).split("\n")[0], "sync external changes")

    def test_new_project_file_and_new_card_directory_are_watched(self):
        text = self.read(os.path.join("projects", "proj.md")).replace("name: proj", "name: Inny")
        self.write(os.path.join("projects", "inny.md"), text)
        self.assertTrue(wait_until(lambda: "inny" in {p["slug"] for p in self.published()["projects"]}, timeout=5))
        self.assertTrue(wait_until(lambda: os.path.join(self.root, "projects", "inny.md") in self.watched(),
                                   timeout=5))
        # Nowy katalog karty w docs/ (kartki ticketów pisane przez agenta) — obserwowany wraz z plikami.
        folder = os.path.join(self.root, "docs", "proj", "tickets", "k-1-flow")
        self.write(os.path.join("docs", "proj", "tickets", "k-1-flow", "PT-1.md"), "# PT-1\n")
        self.assertTrue(wait_until(lambda: folder in self.watched(), timeout=5))
        self.assertIn(os.path.join(folder, "PT-1.md"), self.watched())
        self.write(os.path.join("docs", "proj", "tickets", "k-1-flow", "PT-1.md"), "\n- [ ] kryterium\n", mode="a")
        self.assertTrue(wait_until(lambda: "kryterium" in subprocess.run(
            ["git", "-C", self.root, "show", "HEAD:docs/proj/tickets/k-1-flow/PT-1.md"],
            capture_output=True, text=True).stdout, timeout=5))

    def test_change_in_subtree_created_during_a_pass_is_not_lost(self):
        """Wpis powstały po odczycie katalogu, ale w trakcie tego samego przejścia, daje zdarzenie (obserwacja
        katalogu przed odczytem), więc trafia do kolejnej synchronizacji i do obserwacji."""
        race = os.path.join(self.root, "docs", "race")
        real = self.mon._entries

        def entries(folder):
            listed = real(folder)
            if folder == race and not os.path.exists(os.path.join(race, "late")):
                os.makedirs(os.path.join(race, "late"))
                with open(os.path.join(race, "late", "note.md"), "w", encoding="utf-8") as fh:
                    fh.write("spóźniony\n")
            return listed
        with mock.patch.object(self.mon, "_entries", entries):
            self.write(os.path.join("docs", "race", "first.md"), "pierwszy\n")
            late = os.path.join(race, "late")
            self.assertTrue(wait_until(lambda: late in self.watched(), timeout=5), self.watched())
        self.assertTrue(wait_until(lambda: "spóźniony" in subprocess.run(
            ["git", "-C", self.root, "show", "HEAD:docs/race/late/note.md"], capture_output=True, text=True).stdout,
            timeout=5))
        # Zmiana w miejscu w nowym poddrzewie (plik obserwowany) — też dochodzi.
        self.write(os.path.join("docs", "race", "late", "note.md"), "drugi zapis\n", mode="a")
        self.assertTrue(wait_until(lambda: "drugi zapis" in subprocess.run(
            ["git", "-C", self.root, "show", "HEAD:docs/race/late/note.md"], capture_output=True, text=True).stdout,
            timeout=5))

    def test_flow_tree_is_not_watched_and_poll_still_commits_it(self):
        """Grupa wyłączona `flow/**`: brak obserwacji i synchronizacji po zapisie; commit i migawkę daje
        odpytywanie (POLL_MS), dziennik i wyjście kroków commituje i publikuje sama usługa (CardFlowMonitor)."""
        self.write(os.path.join("flow", "K-1", "steps", "s1", "out", "note.md"), "wynik\n")
        self.mon.sync(full=True)
        flow = os.path.join(self.root, "flow")
        self.assertFalse([p for p in self.watched() if p == flow or p.startswith(flow + os.sep)], self.watched())
        syncs = []
        real = self.mon.sync
        with mock.patch.object(self.mon, "sync", lambda *a, **k: (syncs.append(1), real(*a, **k))):
            self.write(os.path.join("flow", "K-1", "steps", "s1", "out", "note.md"), "drugi\n", mode="a")
            self.write(os.path.join("flow", "K-1", "steps", "s2", "in", "prompt.md"), "nowy\n")
            wait_until(lambda: False, timeout=0.6)
        self.assertEqual(syncs, [], "zapis w flow/ obudził synchronizację")
        self.assertFalse(self.mon._debounce.isActive())
        self.mon._poll_sync()
        shown = subprocess.run(["git", "-C", self.root, "show", "HEAD:flow/K-1/steps/s2/in/prompt.md"],
                               capture_output=True, text=True).stdout
        self.assertEqual(shown, "nowy\n")


class RefusedWatchTest(WatchCase):
    def test_watch_limit_falls_back_to_polling(self):
        watcher = FakeWatcher(refuse=True)
        monitor = self.monitor(watcher, poll_ms=300)
        self.board(monitor)
        self.assertGreater(monitor.watch_stats["refused"], 0)
        self.assertEqual(watcher.all(), set())
        self.write(os.path.join("cards", "K-1.md"), "\nZmiana bez zdarzenia.\n", mode="a")
        self.assertTrue(wait_until(lambda: "Zmiana bez zdarzenia." in monitor._json, timeout=5))
        self.assertEqual(_git_log(self.root).split("\n")[0], "sync external changes")


class InotifyLikeWatcher(FakeWatcher):
    """Jak inotify: ścieżki, której nie ma, nie da się obserwować (odrzucona), a obserwacja usuniętego katalogu
    znika sama; `refuse_existing` — odrzuca też istniejące ścieżki (limit)."""

    def __init__(self):
        super().__init__()
        self.refuse_existing = False

    def directories(self):
        self.dirs = {p for p in self.dirs if os.path.isdir(p)}
        return super().directories()

    def addPaths(self, paths):
        refused = [str(p) for p in paths if self.refuse_existing or not os.path.lexists(p)]
        super().addPaths([p for p in paths if str(p) not in refused])
        return refused


class VanishedDirectoryTest(WatchCase):
    """PT-385 (N2): katalog usunięty między zdarzeniem a przejściem nie jest „odrzuconą ścieżką (limit inotify?)” —
    bez licznika i bez wpisu; istniejąca odrzucona ścieżka dalej daje jedno i drugie."""

    def setUp(self):
        super().setUp()
        self.watcher = InotifyLikeWatcher()
        self.mon = self.monitor(self.watcher)
        self.board(self.mon)
        self.folder = os.path.join(self.root, "docs", "gone")
        os.makedirs(os.path.join(self.folder, "inner"))
        self.mon._rewatch(full=True)
        self.assertIn(os.path.join(self.folder, "inner"), self.watcher.all())

    def rewatch_after_event(self):
        """Zdarzenie na katalogu, a przed przejściem — katalog znika (z obserwacją, jak w inotify)."""
        self.mon._dirty.update({self.folder, os.path.join(self.folder, "inner")})
        shutil.rmtree(self.folder)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mon._rewatch()
        return out.getvalue()

    def test_vanished_directory_is_not_counted_as_refused(self):
        log = self.rewatch_after_event()
        self.assertEqual(self.mon.watch_stats["refused"], 0)
        self.assertNotIn("limit inotify", log)
        self.assertEqual(self.mon._refused, set())
        self.assertNotIn(self.folder, self.mon._tree, "usunięte poddrzewo zdjęte z pamięci obserwacji")

    def test_existing_refused_path_is_still_counted_and_logged(self):
        self.watcher.refuse_existing = True
        self.watcher.dirs.discard(self.folder)          # obserwacja zgubiona — przejście doda ją ponownie
        self.mon._dirty.add(self.folder)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mon._rewatch()
        self.assertGreater(self.mon.watch_stats["refused"], 0)
        self.assertIn("limit inotify", out.getvalue())
        self.assertIn(self.folder, self.mon._refused)


if __name__ == "__main__":
    unittest.main()
