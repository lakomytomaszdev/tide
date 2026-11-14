"""PT-416 (K-105): kontrola zgodności dziennika w Tablicy z kopią usługi (`Journal.check`) bez czytania treści, gdy
żaden z dwóch plików się nie zmienił.

Odczyty treści liczy granica `Journal._check_read` (odczyt pliku dziennika w kontroli zgodności), odtworzenie —
`Journal._restore`; oba trafiają do jednego dziennika wywołań, więc test widzi kolejność. Zmiany plików są robione
tak jak przez zewnętrzne narzędzia: `git checkout` starszej wersji (nowy i-węzeł), zapis w miejscu z przywróconym
mtime (`os.utime`), `chmod`, `touch`."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from tide_daemon.cardflow import journal as journal_module
from tide_daemon.cardflow.journal import Journal

WARNING = "dziennik karty w repozytorium tablicy był cofnięty albo zmieniony z zewnątrz — odtworzony z kopii usługi"


def _state(path):
    st = os.stat(path)
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


class CheckCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        os.makedirs(os.path.join(temp.name, "board"))
        self.journal = Journal(os.path.join(temp.name, "board"), mirror=os.path.join(temp.name, "mirror"))
        for card in ("K-1", "K-2"):
            self.journal.append(card, "card.started", project="proj")
            self.journal.append(card, "card.paused")
        self.calls = []
        real_read, real_restore = self.journal._check_read, self.journal._restore
        self.read_error = set()

        def read(path):
            self.calls.append(("read", path))
            if path in self.read_error:
                return "", False
            return real_read(path)

        def restore(card):
            self.calls.append(("restore", card))
            return real_restore(card)
        for name, side in (("_check_read", read), ("_restore", restore)):
            patcher = mock.patch.object(self.journal, name, side_effect=side)
            patcher.start()
            self.addCleanup(patcher.stop)

    def board(self, card="K-1"):
        return self.journal.path(card)

    def mirror(self, card="K-1"):
        return self.journal.mirror_path(card)

    def text(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def reads(self):
        return [path for kind, path in self.calls if kind == "read"]

    def restores(self):
        return [card for kind, card in self.calls if kind == "restore"]

    def confirmed(self, card="K-1"):
        """Pierwsza kontrola: pełne porównanie, zgodność, zapamiętana para."""
        self.assertEqual(self.journal.check(card), "")
        self.assertIn(card, self.journal._check_memory)
        self.calls.clear()

    def full_compare(self, card="K-1"):
        self.assertEqual(sorted(self.reads()), sorted([self.board(card), self.mirror(card)]),
                         "pełne porównanie treści obu plików")

    def no_compare(self):
        self.assertEqual(self.calls, [], "bez odczytu treści i bez odtworzenia")

    def restored_after_fresh_compare(self, card="K-1"):
        """Odtworzenie dokładnie raz, po świeżym odczycie treści obu plików w tym samym wywołaniu."""
        index = self.calls.index(("restore", card))
        self.assertEqual(sorted(path for kind, path in self.calls[:index] if kind == "read"),
                         sorted([self.board(card), self.mirror(card)]))
        self.assertEqual(self.restores(), [card])
        self.assertEqual(self.text(self.board(card)), self.text(self.mirror(card)))

    def write_in_place(self, path, text):
        with open(path, "r+", encoding="utf-8") as fh:
            fh.seek(0)
            fh.write(text)
            fh.truncate()

    def checkout_older(self, card="K-1"):
        """`git checkout` starszej wersji: nowy plik podmieniony atomowo (inny i-węzeł), krótsza historia."""
        older = self.text(self.board(card)).splitlines(keepends=True)[0]
        tmp = self.board(card) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(older)
        os.replace(tmp, self.board(card))


class UnchangedFilesTest(CheckCase):
    def test_second_check_of_every_card_reads_no_content(self):
        for card in self.journal.cards():
            self.assertEqual(self.journal.check(card), "")
        self.assertEqual(len(self.reads()), 4, "pierwsza kontrola: pełne porównanie obu kart")
        self.calls.clear()
        for card in self.journal.cards():
            self.assertEqual(self.journal.check(card), "")
        self.no_compare()

    def test_remembered_pair_is_the_state_of_both_files(self):
        self.confirmed()
        self.assertEqual(self.journal._check_memory["K-1"], (_state(self.board()), _state(self.mirror())))


class ChangedStateTest(CheckCase):
    def test_revert_with_a_new_inode_is_restored_with_the_warning(self):
        self.confirmed()
        ino = os.stat(self.board()).st_ino
        self.checkout_older()
        self.assertNotEqual(os.stat(self.board()).st_ino, ino)
        self.assertEqual(self.journal.check("K-1"), "K-1: " + WARNING)
        self.restored_after_fresh_compare()
        self.assertEqual(self.journal._check_memory["K-1"], (_state(self.board()), _state(self.mirror())),
                         "po udanym odtworzeniu: stan obu plików po zapisie")
        self.calls.clear()
        self.assertEqual(self.journal.check("K-1"), "")
        self.no_compare()

    def test_same_size_in_place_edit_with_restored_mtime_changes_only_ctime_and_is_restored(self):
        self.confirmed()
        before = _state(self.board())
        st = os.stat(self.board())
        text = self.text(self.board())
        time.sleep(0.02)                 # zgrubny zegar jądra: ctime edycji różni się od ctime zapamiętanego
        self.write_in_place(self.board(), text.replace('"proj"', '"prok"'))
        os.utime(self.board(), ns=(st.st_atime_ns, st.st_mtime_ns))
        after = _state(self.board())
        self.assertEqual(after[:4], before[:4], "ten sam i-węzeł, rozmiar i mtime")
        self.assertNotEqual(after[4], before[4], "zmienia się tylko st_ctime_ns")
        self.assertEqual(self.journal.check("K-1"), "K-1: " + WARNING)
        self.restored_after_fresh_compare()
        self.assertIn('"proj"', self.text(self.board()))

    def test_metadata_only_change_compares_without_warning_and_remembers_the_new_pair(self):
        self.confirmed()
        before = _state(self.board())
        time.sleep(0.02)
        os.chmod(self.board(), 0o600 if os.stat(self.board()).st_mode & 0o777 != 0o600 else 0o644)
        after = _state(self.board())
        self.assertEqual(after[:4], before[:4])
        self.assertNotEqual(after[4], before[4], "chmod zmienia tylko st_ctime_ns")
        self.assertEqual(self.journal.check("K-1"), "")
        self.full_compare()
        self.assertEqual(self.restores(), [])
        self.assertEqual(self.journal._check_memory["K-1"], (after, _state(self.mirror())))
        self.calls.clear()
        self.journal.check("K-1")
        self.no_compare()

    def test_touch_without_content_change_is_neither_restored_nor_warned(self):
        for path in (self.board, self.mirror):
            with self.subTest(path.__name__):
                self.confirmed()
                remembered = self.journal._check_memory["K-1"]
                future = time.time_ns() + 5_000_000_000
                os.utime(path(), ns=(future, future))
                self.assertNotEqual((_state(self.board()), _state(self.mirror())), remembered)
                self.assertEqual(self.journal.check("K-1"), "")
                self.full_compare()
                self.assertEqual(self.restores(), [], "różna para bez różnej treści nie jest powodem odtworzenia")

    def test_remembered_agreement_never_authorizes_a_write_after_an_external_change(self):
        """Zapamiętana zgodna para + zewnętrzna zmiana Tablicy: kolejne wywołanie porównuje świeżo przed zapisem."""
        self.confirmed()
        board = self.text(self.board())
        time.sleep(0.02)
        self.write_in_place(self.board(), board + board.splitlines(keepends=True)[-1])
        self.assertEqual(self.journal.check("K-1"), "K-1: " + WARNING)
        self.restored_after_fresh_compare()

    def test_change_of_the_service_copy_alone_forces_a_compare(self):
        self.confirmed()
        self.journal.append("K-1", "card.resumed")       # zapis tego procesu: obie strony, para unieważniona
        self.calls.clear()
        self.assertEqual(self.journal.check("K-1"), "")
        self.full_compare()
        self.assertEqual(self.restores(), [])


class ErrorsTest(CheckCase):
    def assert_forgotten_then_full_compare(self):
        self.assertNotIn("K-1", self.journal._check_memory, "błąd usuwa poprzednią parę karty")
        self.calls.clear()
        self.read_error.clear()
        self.assertEqual(self.journal.check("K-1"), "")
        self.full_compare()
        self.assertIn("K-1", self.journal._check_memory)

    def test_stat_error_drops_the_pair_and_the_next_check_compares_in_full(self):
        self.confirmed()
        with mock.patch.object(journal_module, "_pair", return_value=None):
            self.assertEqual(self.journal.check("K-1"), "")
        self.full_compare()
        self.assert_forgotten_then_full_compare()

    def test_read_error_is_not_remembered_and_the_next_check_compares_in_full(self):
        for broken in (self.board, self.mirror):
            with self.subTest(broken.__name__):
                self.confirmed()
                time.sleep(0.02)
                os.chmod(self.board(), 0o600 if os.stat(self.board()).st_mode & 0o777 != 0o600 else 0o644)
                self.read_error.add(broken())
                self.journal.check("K-1")      # odczyt nieudany: jak dotąd pusta treść (różna) i odtworzenie z kopii
                self.assertNotIn("K-1", self.journal._check_memory, "błąd odczytu nie zapamiętuje pary")
                self.assert_forgotten_then_full_compare()

    def test_missing_board_file_is_restored_without_remembering_the_pair(self):
        self.confirmed()
        os.unlink(self.board())
        self.assertEqual(self.journal.check("K-1"), "K-1: " + WARNING)
        self.restored_after_fresh_compare()
        self.assert_forgotten_then_full_compare()

    def test_missing_service_copy_is_rebuilt_without_remembering_the_pair(self):
        self.confirmed()
        text = self.text(self.board())
        os.unlink(self.mirror())
        self.assertEqual(self.journal.check("K-1"), "")
        self.assertEqual(self.text(self.mirror()), text, "kopia odbudowana z Tablicy jak dotąd")
        self.assert_forgotten_then_full_compare()

    def test_external_change_right_after_the_restore_is_not_remembered_as_agreement(self):
        """Review M1: zapis Tablicy z zewnątrz po `os.replace` odtworzenia, przed `stat` (np. `git checkout`
        przepisujący plik ponownie) — para nie jest zapamiętana, kolejne wywołanie porównuje i odtwarza."""
        self.confirmed()
        self.checkout_older()
        real_write = journal_module._write

        def write_then_revert(path, text):
            real_write(path, text)
            if path == self.board():
                self.checkout_older()
        with mock.patch.object(journal_module, "_write", side_effect=write_then_revert):
            self.assertEqual(self.journal.check("K-1"), "K-1: " + WARNING)
        self.assertNotEqual(self.text(self.board()), self.text(self.mirror()))
        self.assertNotIn("K-1", self.journal._check_memory, "stan po zmianie z zewnątrz nie jest parą zgodną")
        self.calls.clear()
        self.assertEqual(self.journal.check("K-1"), "K-1: " + WARNING)
        self.restored_after_fresh_compare()
        self.assertIn("K-1", self.journal._check_memory)

    def test_restore_is_remembered_only_after_a_fresh_read_of_the_restored_board(self):
        self.confirmed()
        self.checkout_older()
        self.assertEqual(self.journal.check("K-1"), "K-1: " + WARNING)
        index = self.calls.index(("restore", "K-1"))
        self.assertEqual([path for kind, path in self.calls[index + 1:] if kind == "read"], [self.board()],
                         "potwierdzenie odtworzenia: świeży odczyt Tablicy")
        self.assertIn("K-1", self.journal._check_memory)

    def test_failed_restore_is_not_remembered(self):
        self.confirmed()
        self.checkout_older()
        with mock.patch.object(journal_module, "_write", side_effect=OSError("dysk pełny")):
            with self.assertRaises(OSError):
                self.journal.check("K-1")
        self.assertNotIn("K-1", self.journal._check_memory)
        self.calls.clear()
        self.assertEqual(self.journal.check("K-1"), "K-1: " + WARNING)
        self.restored_after_fresh_compare()


class OwnWritesAndForgetTest(CheckCase):
    def test_own_append_invalidates_the_pair_and_gives_no_false_warning(self):
        self.confirmed()
        self.journal.append("K-1", "card.resumed")
        self.assertNotIn("K-1", self.journal._check_memory)
        self.calls.clear()               # `append` sam zapisuje Tablicę po kopii (`_restore`) — to nie kontrola
        for _ in range(2):
            self.assertEqual(self.journal.check("K-1"), "")
        self.assertEqual(self.restores(), [])
        self.assertEqual(json.loads(self.text(self.board()).splitlines()[-1])["type"], "card.resumed")

    def test_own_write_of_the_board_journal_invalidates_the_pair(self):
        self.confirmed()
        self.journal.write_file("K-1", "journal.jsonl", self.text(self.mirror()).encode())
        self.assertNotIn("K-1", self.journal._check_memory)

    def test_forget_except_drops_pairs_of_cards_outside_the_registry(self):
        self.confirmed("K-1")
        self.confirmed("K-2")
        self.journal.forget_except(["K-2"])
        self.assertEqual(set(self.journal._check_memory), {"K-2"})


if __name__ == "__main__":
    unittest.main()
