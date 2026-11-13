"""K-113 #3 (PT-467): pamięć wczytania Tablicy według odcisku plików w kontekście ticku.

Kontekst ticku (`BoardRead` z `invalidate_on_write=True`) bierze `Board` z pamięci magazynu, gdy odcisk wejść `load()`
się nie zmienił; każda zmiana Tablicy daje świeże wczytanie. Testy liczą wywołania `BoardStore.load` i `_read_doc`;
czasu nie mierzą. `settle()` czeka (krótko, z limitem), aż wpisy Tablicy wyjdą poza okno `_RACY_NS` — dopiero wtedy
wczytanie wolno zapamiętać.
"""
import contextlib
import os
import time
import unittest
from unittest import mock

from tide_daemon.board import contract_approval, store as store_module
from tide_daemon.board.readcontext import BoardRead
from tide_daemon.board.store import BoardStore

from .test_board_core import BoardCase

CONTRACT = {"changed": "make test-changed BASE={base}", "full": "make test"}
OUTSIDE = "dowiązanie wskazuje poza katalog tablicy"


class TickMemoryCase(BoardCase):
    def setUp(self):
        super().setUp()
        self.approvals = os.path.join(self.tmp, "state", "tide", "test-contracts.json")
        self.store = BoardStore(self.root, clock=lambda: self.clock[0], approvals_path=self.approvals)
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.store.upsert_tag({"name": "pilne"})
        self.card_id = self.store.create_card({"title": "Alpha", "project": "app"})["id"]

    def path(self, rel):
        return os.path.join(self.root, rel)

    def settle(self):
        """Czeka, aż odcisk da się zapamiętać (wpisy starsze niż `_RACY_NS`); najwyżej 5 s."""
        deadline = time.monotonic() + 5
        while self.store._load_fingerprint() is None:
            self.assertLess(time.monotonic(), deadline, "odcisk Tablicy nie ustalił się")
            time.sleep(0.01)

    def tick_board(self):
        return BoardRead(self.store, invalidate_on_write=True).board

    @contextlib.contextmanager
    def counted(self):
        """Wywołania `BoardStore.load` i `_read_doc` w bloku."""
        calls = {"load": 0, "read_doc": 0}
        real_load, real_doc = self.store.load, self.store._read_doc

        def load(*args, **kwargs):
            calls["load"] += 1
            return real_load(*args, **kwargs)

        def read_doc(*args, **kwargs):
            calls["read_doc"] += 1
            return real_doc(*args, **kwargs)
        with mock.patch.object(self.store, "load", side_effect=load), \
                mock.patch.object(self.store, "_read_doc", side_effect=read_doc):
            yield calls

    def warm(self):
        """Ciepła pamięć: wczytanie po ustaleniu odcisku i kontrola, że drugi kontekst nie czyta."""
        self.settle()
        board = self.tick_board()
        with self.counted() as calls:
            again = self.tick_board()
        self.assertEqual(calls, {"load": 0, "read_doc": 0}, "drugi kontekst ticku bez wczytania")
        self.assertIs(again, board)
        return board

    def card_title(self, board):
        return board.cards[int(self.card_id.partition("-")[2])].title


class WarmTickContextTest(TickMemoryCase):
    def test_second_tick_context_neither_loads_nor_reads_documents(self):
        board = self.warm()
        with self.counted() as calls:
            for _ in range(3):
                self.assertIs(self.tick_board(), board)
        self.assertEqual(calls, {"load": 0, "read_doc": 0})

    def test_change_within_the_racy_window_is_not_memorized(self):
        self.settle()
        with mock.patch.object(store_module, "_RACY_NS", 10 ** 18), self.counted() as calls:
            self.tick_board()
            self.tick_board()
        self.assertEqual(calls["load"], 2, "świeża zmiana (ta sama ziarnistość czasu) — bez pamięci")


class InvalidationTest(TickMemoryCase):
    def test_same_size_in_place_edit_with_restored_mtime(self):
        self.warm()
        path = self.path(f"cards/{self.card_id}.md")
        before = os.lstat(path)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "r+", encoding="utf-8") as fh:     # zapis w miejscu: ten sam i-węzeł i rozmiar
            fh.write(text.replace("title: Alpha", "title: Bravo"))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.lstat(path)
        self.assertEqual((after.st_ino, after.st_size, after.st_mtime_ns),
                         (before.st_ino, before.st_size, before.st_mtime_ns))
        self.assertEqual(self.card_title(self.tick_board()), "Bravo")

    def test_same_size_replacement_by_rename_with_restored_mtime(self):
        self.warm()
        path = self.path(f"cards/{self.card_id}.md")
        before = os.lstat(path)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        tmp = self.path("cards/.tmp-edit")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text.replace("title: Alpha", "title: Gamma"))
        os.utime(tmp, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(tmp, path)                               # git, edytor: nowy i-węzeł, ten sam rozmiar i mtime
        self.assertEqual(self.card_title(self.tick_board()), "Gamma")

    def test_added_and_removed_card_project_and_tag_files(self):
        files = {
            "cards/K-77.md": "---\nid: K-77\ntitle: Nowa\ncolumn: backlog\n---\n",
            "projects/nowy.md": "---\nname: Nowy\n---\n",
            "tags/inny.md": "---\nname: inny\n---\n",
        }
        seen = {
            "cards/K-77.md": lambda b: 77 in b.cards,
            "projects/nowy.md": lambda b: "nowy" in b.projects,
            "tags/inny.md": lambda b: "inny" in b.tags,
        }
        for rel, text in files.items():
            with self.subTest(rel=rel):
                self.assertFalse(seen[rel](self.warm()))
                self.write(rel, text)
                self.assertTrue(seen[rel](self.tick_board()), "dodany plik widać w następnym ticku")
                self.assertTrue(seen[rel](self.warm()))
                os.unlink(self.path(rel))
                self.assertFalse(seen[rel](self.tick_board()), "usunięty plik znika w następnym ticku")

    def test_box_profile_change(self):
        self.assertEqual(self.warm().projects["app"].tests.get("full", ""), "")
        self.write("projects/app/box.yaml", "tide-box: 1\ntests:\n  full: \"make test\"\n")
        self.assertEqual(self.tick_board().projects["app"].tests["full"], "make test")
        self.warm()
        os.unlink(self.path("projects/app/box.yaml"))
        self.assertEqual(self.tick_board().projects["app"].tests.get("full", ""), "")

    def test_contract_approval_change_without_board_file_change(self):
        self.store.upsert_project({"slug": "app", "tests": CONTRACT})
        self.assertFalse(self.warm().projects["app"].tests_approved)
        contract_approval.record(self.root, "app", self.store.load().projects["app"].tests, self.approvals)
        self.assertTrue(self.tick_board().projects["app"].tests_approved, "zatwierdzenie w magazynie poza Tablicą")
        self.assertTrue(self.warm().projects["app"].tests_approved)
        os.unlink(self.approvals)
        self.assertFalse(self.tick_board().projects["app"].tests_approved, "usunięty magazyn zatwierdzeń")

    def test_in_process_write_invalidates_memory(self):
        self.warm()
        generation = self.store.generation
        self.store._write("notatka.md", "x\n")             # plik spoza odcisku: unieważnia sam `generation`
        self.assertEqual(self.store.generation, generation + 1)
        with self.counted() as calls:
            self.tick_board()
        self.assertEqual(calls["load"], 1)

    def test_board_write_through_the_store_is_seen(self):
        self.warm()
        self.store.update_card(self.card_id, {"title": "Delta"})
        self.assertEqual(self.card_title(self.tick_board()), "Delta")

    def test_project_repository_appearing_outside_the_board(self):
        repo = os.path.join(self.ws, "pozniej")
        self.store.upsert_project({"slug": "app", "repo": self.repo("tymczasowe")})
        text = self.store.load().projects["app"].doc.text().replace(os.path.join(self.ws, "tymczasowe"), repo)
        self.write("projects/app.md", "".join(line for line in text.splitlines(keepends=True)
                                              if not line.startswith("ai-docs:")))   # domyślne ai-docs z repozytorium
        project = self.warm().projects["app"]
        self.assertTrue(project.repo_missing)
        self.assertEqual((project.ai_docs, project.ai_docs_set), ("tide", False))
        os.makedirs(os.path.join(repo, "docs", "agents"))
        project = self.tick_board().projects["app"]
        self.assertFalse(project.repo_missing, "repozytorium projektu poza Tablicą — pochodne sprawdzane")
        self.assertEqual(project.ai_docs, "repo")


class OutsideLinkTest(TickMemoryCase):
    def setUp(self):
        super().setUp()
        self.outside_dir = os.path.join(self.tmp, "poza")
        os.makedirs(self.outside_dir)
        self.write("data/k5.md", "---\nid: K-5\ntitle: Dowiązana\ncolumn: backlog\n---\n")
        self.inside = self.path("data/k5.md")
        self.outside = os.path.join(self.outside_dir, "k5.md")
        os.link(self.inside, self.outside)                  # ten sam i-węzeł: `stat` celu się nie zmienia
        os.symlink(self.inside, self.path("data/hop"))
        os.symlink(self.path("data/hop"), self.path("cards/K-5.md"))

    def outside_errors(self, board):
        return [e["message"] for e in board.errors if OUTSIDE in e["message"]]

    def test_retargeted_link_inside_the_chain_gives_the_same_error_as_without_memory(self):
        board = self.warm()
        self.assertIn(5, board.cards)
        self.assertEqual(self.outside_errors(board), [])
        hop = self.path("data/hop")
        os.unlink(hop)
        os.symlink(self.outside, hop)                        # cel K-5.md i jego `stat` bez zmian, inny `realpath`
        board = self.tick_board()
        fresh = self.store.load()
        self.assertNotIn(5, board.cards)
        self.assertEqual(self.outside_errors(board), self.outside_errors(fresh))
        self.assertEqual(len(self.outside_errors(board)), 1)
        warm = self.warm()                                   # po ciepłym kontekście — ten sam błąd
        self.assertEqual(self.outside_errors(warm), self.outside_errors(fresh))
        os.unlink(hop)
        os.symlink(self.inside, hop)
        self.assertIn(5, self.tick_board().cards, "powrót celu do Tablicy też zmienia odcisk")

    def test_link_directly_outside(self):
        os.unlink(self.path("cards/K-5.md"))
        os.symlink(self.outside, self.path("cards/K-5.md"))
        board = self.warm()
        self.assertNotIn(5, board.cards)
        self.assertEqual(self.outside_errors(board), self.outside_errors(self.store.load()))
        self.assertEqual(len(self.outside_errors(board)), 1)


class LoadErrorTest(TickMemoryCase):
    def test_failed_load_is_not_memorized(self):
        self.warm()
        path = self.path(f"cards/{self.card_id}.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.write(f"cards/{self.card_id}.md", text.replace("title: Alpha", "title: Epsilon"))
        with mock.patch.object(self.store, "_load", side_effect=OSError("awaria wczytania")):
            with self.assertRaises(OSError):
                self.tick_board()
        self.assertIsNone(self.store._tick_memory, "błąd wczytania czyści pamięć i nie jest pamiętany")
        with self.counted() as calls:
            board = self.tick_board()
        self.assertEqual(calls["load"], 1)
        self.assertEqual(self.card_title(board), "Epsilon")

    def test_broken_acceptance_intent_fails_every_tick_until_removed(self):
        self.warm()
        intents = self.path(".git/tide-acceptances")
        os.makedirs(intents, exist_ok=True)
        broken = os.path.join(intents, "zepsuta.json")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("{")
        for _ in range(2):
            with self.assertRaises(Exception):
                self.tick_board()
        os.unlink(broken)
        with self.counted() as calls:
            board = self.tick_board()
        self.assertEqual(calls["load"], 1)
        self.assertEqual(self.card_title(board), "Alpha")


class OtherReadersTest(TickMemoryCase):
    def test_public_load_transactions_and_refresh_context_do_not_use_memory(self):
        memory = self.warm()
        with self.counted() as calls:
            fresh = self.store.load()
        self.assertEqual(calls["load"], 1)
        self.assertGreater(calls["read_doc"], 0, "publiczne load() czyta pliki")
        self.assertIsNot(fresh, memory)
        with mock.patch.object(BoardStore, "load_for_tick", side_effect=AssertionError("pamięć ticku")):
            with self.counted() as calls:
                refresh = BoardRead(self.store).board
            self.assertEqual(calls["load"], 1)
            self.assertIsNot(refresh, memory)
            with self.counted() as calls:
                self.store.update_card(self.card_id, {"title": "Zeta"})   # transakcja zapisu: świeży odczyt
            self.assertGreater(calls["read_doc"], 0)
            self.assertEqual(self.card_title(self.store.load()), "Zeta")
        self.assertEqual(self.card_title(memory), "Alpha", "Board z pamięci nie jest zmieniany przez zapis")


if __name__ == "__main__":
    unittest.main()
