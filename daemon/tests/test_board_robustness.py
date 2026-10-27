"""Tablica w trudnych warunkach: nieudany commit, osierocona blokada indeksu, revert z konfliktem,
numery kart po revercie, błąd bez skutków ubocznych, dowiązania i pliki specjalne, listy YAML,
kolejność bez przenumerowania, hooki, idempotentne projekty, zmiana nazwy tagu bez pliku, katalog
dokumentów. Bez Qt; każdy test na własnym katalogu i własnej konfiguracji gita.
"""
import os
import stat
import subprocess
import time

from .test_board_core import BoardCase, git_out
from tide_daemon.board.frontmatter import Document, FrontmatterError
from tide_daemon.board.model import BoardError
from tide_daemon.board.repo import GIT_TIMEOUT
from tide_daemon.board.store import BoardStore
from tide_daemon.worktrees.git import Git, Result


def commits(root) -> int:
    res = subprocess.run(["git", "-C", root, "rev-list", "--count", "HEAD"], capture_output=True, text=True)
    return int(res.stdout.strip() or 0) if res.returncode == 0 else 0


class CommitFailureTest(BoardCase):
    """M1: plik jest źródłem prawdy — nieudany commit nie daje błędu ani duplikatu."""

    def lock(self, age: float = 0.0) -> str:
        path = os.path.join(self.root, ".git", "index.lock")
        open(path, "w").close()
        if age:
            past = time.time() - age
            os.utime(path, (past, past))
        return path

    def test_git_timeout_is_short(self):
        self.assertEqual(GIT_TIMEOUT, 5)
        self.assertEqual(BoardStore(self.root).git.timeout, 5)

    def test_locked_index_keeps_card_and_commits_later_with_its_message(self):
        self.store.create_card({"title": "a"})
        lock = self.lock()
        first = self.store.create_card({"title": "b"})
        self.assertEqual((first["ok"], first["id"]), (True, "K-2"))
        self.assertIn("index.lock", first["commitError"])
        second = self.store.create_card({"title": "c"})  # kolejny zapis — nowa karta, bez duplikatu
        self.assertEqual(second["id"], "K-3")
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "cards"))), ["K-1.md", "K-2.md", "K-3.md"])
        snap = self.store.snapshot()
        self.assertEqual(snap["git"]["pending"], 2)
        self.assertIn("czekają na commit", snap["errors"][0]["message"])
        self.assertTrue(self.store.sync() is False and os.path.exists(lock))  # świeża blokada — może to cudzy git
        past = time.time() - 60
        os.utime(lock, (past, past))
        self.assertTrue(self.store.sync())
        self.assertFalse(os.path.exists(lock))
        self.assertEqual(self.log()[-2:], ["K-2: create", "K-3: create"])
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")
        self.assertEqual(self.store.snapshot()["git"], {"busy": "", "pending": 0, "error": ""})

    def test_killed_git_lock_is_removed_right_away(self):
        self.store.create_card({"title": "a"})
        root = self.root

        class HangingCommit(Git):
            hung = False

            def run(self, cwd, *args, timeout=None):
                if "commit" in args and not HangingCommit.hung:
                    HangingCommit.hung = True
                    open(os.path.join(root, ".git", "index.lock"), "w").close()  # jak git zabity po czasie
                    return Result(-1, "", "Command '['git', 'commit']' timed out after 5 seconds")
                return super().run(cwd, *args, timeout=timeout)

        store = BoardStore(self.root, git=HangingCommit(timeout=5), clock=lambda: self.clock[0])
        result = store.create_card({"title": "b"})
        self.assertTrue(result["ok"] and result["commitError"])
        self.assertFalse(os.path.exists(os.path.join(self.root, ".git", "index.lock")))
        self.assertEqual(store.create_card({"title": "c"})["commitError"], "")  # zaległy commit idzie pierwszy
        self.assertEqual(self.log()[-2:], ["K-2: create", "K-3: create"])

    def test_live_git_protects_an_old_lock(self):
        self.store.create_card({"title": "a"})
        proc = os.path.join(self.tmp, "proc")
        os.makedirs(os.path.join(proc, "4242"))
        with open(os.path.join(proc, "4242", "comm"), "w", encoding="utf-8") as fh:
            fh.write("git\n")
        os.symlink(os.path.join(self.root, "cards"), os.path.join(proc, "4242", "cwd"))
        self.store.repo.proc_root = proc
        lock = self.lock(age=600)
        self.write("docs/x.md", "x\n")
        self.assertFalse(self.store.sync())
        self.assertTrue(os.path.exists(lock))
        os.remove(os.path.join(proc, "4242", "cwd"))
        self.assertTrue(self.store.sync())
        self.assertFalse(os.path.exists(lock))


class GitInProgressTest(BoardCase):
    """M2: w trakcie revertu z konfliktem nie commitujemy znaczników konfliktu."""

    def start_conflicting_revert(self):
        self.store.create_card({"title": "pierwszy"})
        self.clock[0] += 60
        self.store.update_card("K-1", {"title": "drugi"})
        self.clock[0] += 60
        self.store.move_card("K-1", "todo", 0)
        sha = git_out(self.root, "log", "--format=%H", "--grep=update title").strip()
        res = subprocess.run(["git", "-C", self.root, "revert", "--no-edit", sha], capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "revert miał się zatrzymać na konflikcie")

    def test_revert_with_conflict_blocks_sync_and_writes(self):
        self.start_conflicting_revert()
        head = git_out(self.root, "rev-parse", "HEAD")
        self.assertFalse(self.store.sync())
        with self.assertRaises(BoardError) as ctx:
            self.store.create_card({"title": "nowa"})
        self.assertEqual(ctx.exception.code, "git-busy")
        self.assertIn("git revert --abort", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, "cards", "K-2.md")))
        self.assertEqual(git_out(self.root, "rev-parse", "HEAD"), head)
        snap = self.store.snapshot()
        self.assertEqual(snap["git"]["busy"], "revert")
        messages = " | ".join(e["message"] for e in snap["errors"])
        self.assertIn("trwa revert z konfliktami", messages)
        self.assertIn("cards/K-1.md: znaczniki konfliktu gita", messages)
        git_out(self.root, "revert", "--abort")
        self.assertEqual(self.store.create_card({"title": "nowa"})["id"], "K-2")

    def test_unmerged_entries_without_head_files_also_count(self):
        self.start_conflicting_revert()
        os.remove(os.path.join(self.root, ".git", "REVERT_HEAD"))
        self.assertEqual(self.store.repo.busy(), "merge")
        self.assertFalse(self.store.sync())

    def test_conflict_markers_are_a_parse_error(self):
        with self.assertRaises(FrontmatterError):
            Document.parse("---\n<<<<<<< HEAD\ntitle: a\n=======\ntitle: b\n>>>>>>> gałąź\n---\n")
        doc = Document.parse("---\ntitle: a\n---\nNagłówek\n=======\n")  # podkreślenie Markdown to nie konflikt
        self.assertEqual(doc.get("title"), "a")


class NumbersNeverComeBackTest(BoardCase):
    """M3: numer karty cofniętej revertem nie wraca (gałęzie `k-N` łączą kartę z koordynatorem)."""

    def test_revert_of_create_does_not_reuse_number(self):
        self.store.create_card({"title": "a"})
        self.store.create_card({"title": "b"})
        git_out(self.root, "revert", "--no-edit", "HEAD")
        self.assertFalse(os.path.exists(os.path.join(self.root, "cards", "K-2.md")))
        self.assertEqual(self.store.snapshot()["nextId"], "K-3")
        self.assertEqual(self.store.create_card({"title": "c"})["id"], "K-3")

    def test_history_is_enough_without_the_high_water_file(self):
        self.store.create_card({"title": "a"})
        self.store.create_card({"title": "b"})
        git_out(self.root, "revert", "--no-edit", "HEAD")
        os.remove(os.path.join(self.root, ".git", "tide-next-card"))
        self.assertEqual(self.store.create_card({"title": "c"})["id"], "K-3")

    def test_high_water_file_is_enough_without_history(self):
        self.store.create_card({"title": "a"})
        with open(os.path.join(self.root, ".git", "tide-next-card"), "w", encoding="utf-8") as fh:
            fh.write("40\n")
        self.assertEqual(self.store.create_card({"title": "b"})["id"], "K-40")


class NoSideEffectsOnErrorTest(BoardCase):
    """M4: błąd w żądaniu nie zostawia pliku, katalogu ani commita."""

    def test_errors_on_empty_directory_create_nothing(self):
        repo = self.repo("proj")
        for call in (lambda: self.store.move_card("K-7", "todo", 0),
                     lambda: self.store.upsert_tag({"name": "x", "color": "neon"}),
                     lambda: self.store.update_card("K-1", {"title": "x"}),
                     lambda: self.store.create_card({"title": "x", "repo": repo, "note": "\x01zle"}),
                     lambda: self.store.create_card({"title": "x", "project": "nie-ma"}),
                     lambda: self.store.upsert_project({"name": "x", "mode": "turbo"}),
                     lambda: self.store.move_card("K-1", "todo", "pierwsza")):
            with self.assertRaises(BoardError):
                call()
        self.assertFalse(os.path.exists(self.root))

    def test_errors_on_existing_board_leave_no_file_and_no_commit(self):
        repo = self.repo("proj")
        self.store.create_card({"title": "a"})
        before = commits(self.root)
        files = sorted(os.listdir(os.path.join(self.root, "projects")))
        for call in (lambda: self.store.create_card({"title": "x", "repo": repo, "note": "\x01zle"}),
                     lambda: self.store.create_card({"title": "x", "repo": repo, "column": "progress"}),
                     lambda: self.store.update_card("K-1", {"repo": repo, "title": ""}),
                     lambda: self.store.upsert_project({"repo": repo, "aiDocs": "gdzies"})):
            with self.assertRaises(BoardError):
                call()
        self.assertEqual(commits(self.root), before)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "projects"))), files)
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")


class BoundedReadsTest(BoardCase):
    """N3: tylko zwykłe pliki w katalogu tablicy, najwyżej FILE_MAX bajtów."""

    def test_symlink_outside_fifo_and_device_are_reported(self):
        self.store.create_card({"title": "a"})
        outside = os.path.join(self.tmp, "sekret.md")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("---\ntitle: sekret\n---\ntajne\n")
        os.symlink(outside, os.path.join(self.root, "cards", "K-50.md"))
        os.symlink("/dev/zero", os.path.join(self.root, "cards", "K-51.md"))
        os.mkfifo(os.path.join(self.root, "cards", "K-52.md"))
        self.write("docs/k53.md", "---\ntitle: przez dowiązanie w tablicy\n---\n")
        os.symlink(os.path.join("..", "docs", "k53.md"), os.path.join(self.root, "cards", "K-53.md"))
        os.mkfifo(os.path.join(self.root, "flows", "spec.md.fifo"))
        snap = self.store.snapshot()  # nie wisi na FIFO, nie czyta /dev/zero bez końca
        cards = {c["id"]: c for c in snap["cards"]}
        self.assertEqual(set(cards), {"K-1", "K-53"})
        messages = " | ".join(e["message"] for e in snap["errors"])
        self.assertIn("K-50.md: dowiązanie wskazuje poza katalog tablicy", messages)
        self.assertIn("K-51.md: dowiązanie wskazuje poza katalog tablicy", messages)
        self.assertIn("K-52.md: to nie jest zwykły plik", messages)
        with self.assertRaises(BoardError) as ctx:
            self.store.update_card("K-50", {"title": "zmiana"})
        self.assertEqual(ctx.exception.code, "broken-file")
        with open(outside, encoding="utf-8") as fh:
            self.assertIn("title: sekret", fh.read())
        self.assertTrue(stat.S_ISFIFO(os.lstat(os.path.join(self.root, "cards", "K-52.md")).st_mode))


class YamlListsTest(BoardCase):
    """N4: `tags: [a, b]` działa, lista w bloku to błąd w migawce, a nie cicho zero tagów."""

    def test_inline_and_block_lists(self):
        self.store.create_card({"title": "a"})
        self.write("cards/K-2.md", "---\r\nid: K-2\r\ntitle: Karta z Windows\r\ntags: [ui, \"Firma\"]\r\n---\r\nOpis\r\n")
        self.write("cards/K-3.md", "---\ntitle: lista\ntags:\n  - ui\n  - backend\ncolumn: todo\n---\nb\n")
        snap = self.store.snapshot()
        cards = {c["id"]: c for c in snap["cards"]}
        self.assertEqual(cards["K-2"]["tags"], ["ui", "Firma"])
        self.assertEqual(cards["K-2"]["title"], "Karta z Windows")
        self.assertEqual((cards["K-3"]["tags"], cards["K-3"]["column"]), ([], "todo"))
        messages = [e["message"] for e in snap["errors"]]
        self.assertTrue(any("K-3.md: linia 4: „- ui”" in m and "tags: a, b" in m for m in messages), messages)
        self.store.update_card("K-3", {"tags": ["ui", "backend"]})
        with open(os.path.join(self.root, "cards", "K-3.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "---\ntitle: lista\ntags: ui, backend\ncolumn: todo\n"
                                        f"updated: {cards['K-1']['created']}\n---\nb\n")
        self.assertEqual(self.store.snapshot()["errors"], [])


class MoveOrderTest(BoardCase):
    """N5: przeniesienie zmienia jeden plik; całą kolumnę numerujemy tylko bez miejsca."""

    def changed_files(self) -> list[str]:
        return git_out(self.root, "show", "--name-only", "--format=", "HEAD").split()

    def test_midpoint_and_renumber_when_no_room(self):
        for title in ("a", "b", "c"):
            self.store.create_card({"title": title, "column": "todo"})
        self.assertEqual(self.column("todo"), ["K-3", "K-2", "K-1"])
        self.store.move_card("K-1", "todo", 1)
        self.assertEqual(self.column("todo"), ["K-3", "K-1", "K-2"])
        self.assertEqual(self.changed_files(), ["cards/K-1.md"])
        self.write("cards/K-2.md", self.card_text("K-2").replace("order: 0\n", "order: -4\n"))
        self.store.sync()
        orders = {c["id"]: c["order"] for c in self.store.snapshot()["cards"]}
        self.assertEqual(orders, {"K-3": -10, "K-1": -5, "K-2": -4})
        self.store.move_card("K-3", "todo", 1)  # między -5 i -4 nie ma miejsca
        self.assertEqual(self.column("todo"), ["K-1", "K-3", "K-2"])
        self.assertEqual(sorted(self.changed_files()), ["cards/K-1.md", "cards/K-2.md", "cards/K-3.md"])
        self.assertEqual([c["order"] for c in self.store.snapshot()["cards"]], [10, 20, 30])
        self.clock[0] += 60
        self.assertEqual(self.store.move_card("K-3", "todo", 1)["changed"], True)  # stempel moved, bez kolejności
        self.assertEqual(self.changed_files(), ["cards/K-3.md"])
        self.assertEqual([c["order"] for c in self.store.snapshot()["cards"]], [10, 20, 30])


class HooksTest(BoardCase):
    """N6: żaden hook (także post-commit) nie działa przy commitach tablicy."""

    def test_hooks_never_run(self):
        self.store.create_card({"title": "a"})
        marker = os.path.join(self.tmp, "hook-ran")
        for name in ("pre-commit", "commit-msg", "post-commit"):
            hook = os.path.join(self.root, ".git", "hooks", name)
            with open(hook, "w", encoding="utf-8") as fh:
                fh.write(f"#!/bin/sh\necho {name} >> {marker}\nexit 1\n" if name != "post-commit" else
                         f"#!/bin/sh\necho {name} >> {marker}\n")
            os.chmod(hook, 0o755)
        self.assertEqual(self.store.create_card({"title": "b"})["commitError"], "")
        self.write("docs/x.md", "x\n")
        self.assertTrue(self.store.sync())
        self.assertFalse(os.path.exists(marker))


class ProjectsAndTagsTest(BoardCase):
    def test_project_by_name_is_idempotent(self):
        first = self.store.upsert_project({"name": "Foo"})
        second = self.store.upsert_project({"name": "Foo"})
        third = self.store.upsert_project({"name": "foo", "tool": "codex"})
        self.assertEqual((first["slug"], first["changed"]), ("foo", True))
        self.assertEqual((second["slug"], second["changed"]), ("foo", False))
        self.assertEqual((third["slug"], third["changed"]), ("foo", True))
        self.assertEqual(os.listdir(os.path.join(self.root, "projects")), ["foo.md"])

    def test_default_mode_is_full(self):
        self.store.upsert_project({"name": "Dom"})
        self.assertEqual(self.store.snapshot()["projects"][0]["mode"], "full")
        self.store.create_card({"title": "bez projektu"})
        self.assertEqual(self.store.render_flow("K-1", "")["mode"], "full")

    def test_rename_tag_that_exists_only_as_usage(self):
        self.store.create_card({"title": "a", "tags": "luźny, inny"})
        result = self.store.upsert_tag({"name": "Luźny", "newName": "Porządny", "color": "mint"})
        self.assertEqual((result["ok"], result["name"]), (True, "Porządny"))
        tags = {t["name"]: t for t in self.store.snapshot()["tags"]}
        self.assertTrue(tags["Porządny"]["defined"])
        self.assertEqual(tags["Porządny"]["color"], "mint")
        self.assertNotIn("luźny", tags)
        self.assertEqual(self.cards()["K-1"]["tags"], ["Porządny", "inny"])
        self.assertEqual(self.log()[-1], "tag Luźny: rename to Porządny")
        with self.assertRaises(BoardError) as ctx:
            self.store.upsert_tag({"name": "nieużywany", "newName": "x"})
        self.assertEqual(ctx.exception.code, "not-found")


class DocsDirTest(BoardCase):
    """N9: RenderFlow zakłada katalog dokumentów w tablicy (agent dostaje go przez --add-dir)."""

    def test_docs_dir_is_created_only_inside_the_board(self):
        agents_repo = self.repo("z-docs", agents_docs=False)
        self.store.upsert_project({"name": "Dom"})
        self.store.upsert_project({"repo": agents_repo, "aiDocs": "tide"})
        os.makedirs(os.path.join(agents_repo, "docs"))
        repo_docs = self.repo("ma-agents", agents_docs=True)
        self.store.upsert_project({"repo": repo_docs})
        self.store.create_card({"title": "a", "project": "dom"})
        self.store.create_card({"title": "b"})
        self.store.create_card({"title": "c", "repo": repo_docs})
        for card_id, expected in (("K-1", os.path.join(self.root, "docs", "dom")),
                                  ("K-2", os.path.join(self.root, "docs", "_inbox"))):
            flow = self.store.render_flow(card_id, "")
            self.assertEqual(flow["docsDir"], expected)
            self.assertTrue(os.path.isdir(expected))
        flow = self.store.render_flow("K-3", "")
        self.assertEqual(flow["docsDir"], os.path.join(repo_docs, "docs", "agents"))
        self.assertEqual(os.listdir(os.path.join(repo_docs, "docs", "agents")), [])  # w repozytorium nic nie tworzymy
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")  # puste katalogi nie brudzą tablicy


class ConflictMarkersInBodyTest(BoardCase):
    """Znaczniki konfliktu w opisie (wklejone wyjście gita) to zwykły tekst, nie zepsuty plik."""

    def test_description_with_git_output(self):
        text = "Git powiedział:\n<<<<<<< HEAD\nfoo\n=======\nbar\n>>>>>>> gałąź\n"
        self.assertEqual(self.store.create_card({"title": "pomoc przy merge", "description": text})["id"], "K-1")
        snap = self.store.snapshot()
        self.assertEqual(snap["errors"], [])
        self.assertEqual(snap["cards"][0]["description"], text)
        self.assertTrue(self.store.update_card("K-1", {"title": "inny"})["changed"])
        self.assertEqual(self.cards()["K-1"]["description"], text)


class HistoryScanTest(BoardCase):
    """Historii gita (liniowej względem commitów) nie czytamy przy migawce ani przy zwykłym zapisie —
    tylko raz, gdy brakuje znacznika `.git/tide-next-card`."""

    def recording_store(self):
        calls = []

        class Recording(Git):
            def run(self, cwd, *args, timeout=None):
                if "--diff-filter=A" in args:
                    calls.append(args)
                return super().run(cwd, *args, timeout=timeout)

        return BoardStore(self.root, git=Recording(timeout=5), clock=lambda: self.clock[0]), calls

    def test_no_history_scan_while_marker_exists(self):
        self.store.create_card({"title": "a"})
        store, calls = self.recording_store()
        store.snapshot()
        store.create_card({"title": "b"})
        store.move_card("K-1", "todo", 0)
        store.snapshot()
        self.assertEqual(calls, [])

    def test_missing_marker_is_rebuilt_from_history_once(self):
        for title in ("a", "b", "c"):
            self.store.create_card({"title": title})
        git_out(self.root, "revert", "--no-edit", "HEAD")
        marker = os.path.join(self.root, ".git", "tide-next-card")
        os.remove(marker)
        store, calls = self.recording_store()
        store.snapshot()
        self.assertEqual(calls, [])  # migawka nigdy nie czyta historii
        self.assertEqual(store.create_card({"title": "d"})["id"], "K-4")
        self.assertEqual(len(calls), 1)
        with open(marker, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "5")
        store.create_card({"title": "e"})
        self.assertEqual(len(calls), 1)


class PendingPathGoneTest(BoardCase):
    """Zaległy commit pliku, którego już nie ma, nie blokuje kolejki."""

    def test_pending_entry_for_deleted_file(self):
        self.store.create_card({"title": "jeden"})
        lock = os.path.join(self.root, ".git", "index.lock")
        open(lock, "w").close()
        self.assertTrue(self.store.create_card({"title": "dwa"})["commitError"])
        os.remove(os.path.join(self.root, "cards", "K-2.md"))
        os.remove(lock)
        self.assertEqual(self.store.create_card({"title": "trzy"})["commitError"], "")
        self.assertEqual(self.store.repo.pending, [])
        self.assertEqual(self.log()[-1], "K-3: create")
        self.store.sync()
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")

    def test_permanent_failure_is_dropped_and_left_to_sync(self):
        self.store.create_card({"title": "jeden"})
        with open(os.path.join(self.ws, "poza.md"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self.store.repo.pending.append(("zly wpis", ["../poza.md"]))  # git: „outside repository” — trwały błąd
        self.write("docs/x.md", "x\n")
        self.assertTrue(self.store.sync())
        self.assertEqual(self.store.repo.pending, [])
        self.assertEqual(self.log()[-1], "sync external changes")
