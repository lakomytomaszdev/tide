"""K-105 #2 (PT-411): ścisły odczyt dziennika karty pamiętany między tickami według wersji dziennika.

Pełne odczyty liczy granica `Journal._read_strict` (pełne przeczytanie i parsowanie pliku przez `read_complete` albo
`read_fresh`); `Journal.check` jej nie używa. Zmiany plików są robione tak jak przez zewnętrzne narzędzia: dopisanie,
ucięcie i zapis w miejscu (ten sam i-węzeł), atomowa podmiana (nowy i-węzeł), przywrócony mtime (`os.utime`)."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from tide_daemon.cardflow import journal as journal_module
from tide_daemon.cardflow.journal import FULL_READ_PERIOD, HistoryChanged, Journal


def _line(event):
    return json.dumps(event, ensure_ascii=False) + "\n"


def _event(seq, card="K-1", **data):
    return {"v": 1, "seq": seq, "ts": "2026-11-12T12:00:00+01:00", "card": card, "type": "flow.notice",
            "key": f"n{seq}", **data}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class JournalMemoryCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        os.makedirs(os.path.join(temp.name, "board"))
        self.clock = Clock()
        self.journal = Journal(os.path.join(temp.name, "board"), mirror=os.path.join(temp.name, "mirror"),
                               monotonic=self.clock)
        for card in ("K-1", "K-2"):
            self.journal.append(card, "card.started", project="proj")
            self.journal.append(card, "card.paused")
        self.full = []
        real = self.journal._read_strict
        patcher = mock.patch.object(self.journal, "_read_strict",
                                    side_effect=lambda card: self.full.append(card) or real(card))
        patcher.start()
        self.addCleanup(patcher.stop)

    def mirror(self, card="K-1"):
        return self.journal.mirror_path(card)

    def text(self, card="K-1"):
        with open(self.mirror(card), encoding="utf-8") as fh:
            return fh.read()

    def external_append(self, *lines, card="K-1"):
        """Dopisanie spoza procesu usługi (bez `generation`): ten sam i-węzeł, większy rozmiar."""
        with open(self.mirror(card), "a", encoding="utf-8") as fh:
            fh.write("".join(lines))

    def write_in_place(self, text, card="K-1"):
        """Zapis w miejscu: ten sam i-węzeł (ucięcie i nowa treść)."""
        with open(self.mirror(card), "r+", encoding="utf-8") as fh:
            fh.seek(0)
            fh.write(text)
            fh.truncate()

    def warm(self, card="K-1"):
        first = self.journal.read_complete(card)
        self.full.clear()
        return first

    def fresh(self, card="K-1"):
        events = self.journal.read_fresh(card)
        self.full.remove(card)       # świeży odczyt porównania nie jest odczytem badanej ścieżki
        return events


class HitAndIncrementTest(JournalMemoryCase):
    def test_unchanged_journal_costs_no_full_read_and_returns_the_same_event_objects(self):
        first = self.warm()
        again = self.journal.read_complete("K-1")
        self.assertEqual(self.full, [])
        self.assertIsNot(again, first, "nowa lista przy każdym odczycie")
        self.assertTrue(all(a is b for a, b in zip(again, first)), "te same obiekty zdarzeń, bez drugiej kopii")
        self.assertEqual(self.journal.version("K-1"), self.journal.read_complete_version("K-1")[0])
        self.assertEqual(self.full, [])

    def test_external_append_is_read_incrementally_and_other_cards_are_not_read(self):
        first = self.warm("K-1")
        self.warm("K-2")
        before = self.journal.version("K-1")
        self.external_append(_line(_event(3)), _line(_event(4)))
        events = self.journal.read_complete("K-1")
        other = self.journal.read_complete("K-2")
        self.assertEqual(self.full, [], "przyrost bez pełnego odczytu, druga karta z pamięci")
        self.assertEqual(events, self.fresh())
        self.assertEqual([e["seq"] for e in events], [1, 2, 3, 4])
        self.assertTrue(all(a is b for a, b in zip(events, first)), "przyrost dokłada tylko nowe zdarzenia")
        self.assertEqual(len(other), 2)
        after = self.journal.version("K-1")
        self.assertNotEqual(after, before)
        self.assertEqual((after.seq, after.size), (4, os.path.getsize(self.mirror())))

    def test_memory_keeps_no_line_text(self):
        self.warm()
        entry = self.journal._strict_memory["K-1"]
        self.assertEqual(set(entry.__slots__), {"version", "events", "tail", "digest", "full_at"})
        self.assertIsInstance(entry.digest, bytes)
        self.assertNotIn(entry.digest, self.text().encode())
        self.assertIsInstance(entry.tail, int)

    def test_own_write_invalidates_the_memory(self):
        self.warm()
        self.journal.append("K-1", "card.resumed")
        self.assertNotIn("K-1", self.journal._strict_memory)
        self.full.clear()      # append czyta świeżo (osobna granica, też liczona)
        events = self.journal.read_complete("K-1")
        self.assertEqual(self.full, ["K-1"])
        self.assertEqual(events[-1]["type"], "card.resumed")


class ChangesNeedFullReadTest(JournalMemoryCase):
    def check_full(self, change):
        self.warm()
        time.sleep(0.02)                 # zgrubny zegar jądra: ctime zmiany różni się od ctime pierwszego odczytu
        change()
        events = self.journal.read_complete("K-1")
        self.assertEqual(self.full, ["K-1"], "zmiana inna niż dopisanie: pełny odczyt")
        self.assertEqual(events, self.fresh())
        return events

    def test_truncation(self):
        lines = self.text().splitlines(keepends=True)
        self.assertEqual(len(self.check_full(lambda: self.write_in_place(lines[0]))), 1)

    def test_atomic_replacement_with_a_new_inode(self):
        lines = self.text().splitlines(keepends=True)

        def replace():
            tmp = self.mirror() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("".join(lines) + _line(_event(3)))
            os.replace(tmp, self.mirror())
        self.assertEqual(len(self.check_full(replace)), 3)

    def test_replaced_service_copy_shorter_and_longer(self):
        """Podmiana kopii usługi (np. rsync starszej kopii): inny i-węzeł, inna treść."""
        old = self.text()

        def older():
            tmp = self.mirror() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(old.splitlines(keepends=True)[0])
            os.replace(tmp, self.mirror())
        self.assertEqual(len(self.check_full(older)), 1)

    def test_same_size_content_change(self):
        text = self.text()
        self.assertIn('"proj"', text)
        self.check_full(lambda: self.write_in_place(text.replace('"proj"', '"prok"')))

    def test_same_size_change_with_restored_mtime(self):
        text = self.text()
        st = os.stat(self.mirror())

        def edit():
            self.write_in_place(text.replace('"proj"', '"prok"'))
            os.utime(self.mirror(), ns=(st.st_atime_ns, st.st_mtime_ns))
        events = self.check_full(edit)
        after = os.stat(self.mirror())
        self.assertEqual((after.st_size, after.st_mtime_ns, after.st_ino), (st.st_size, st.st_mtime_ns, st.st_ino))
        self.assertEqual(events[0]["project"], "prok")


class EarlierLineEditTest(JournalMemoryCase):
    """Ograniczenie przyrostu: edycja wcześniejszego wiersza w miejscu + dopisanie (ten sam i-węzeł, zachowany
    ostatni wiersz) nie jest wykrywana przyrostem; bramka czyta świeżo, a okres pełnego odczytu ją wykrywa."""

    def edit_and_append(self, old, new):
        text = self.text()
        self.assertEqual(len(old), len(new))
        with open(self.mirror(), "r+b") as fh:
            fh.seek(text.encode().index(old.encode()))
            fh.write(new.encode())
        self.external_append(_line(_event(3)))

    def test_gate_write_is_refused_at_once_and_the_period_restores_a_full_read(self):
        self.warm()
        self.edit_and_append('"proj"', '"prok"')
        basis = self.journal.read_complete("K-1")
        self.assertEqual(self.full, [], "przyrost (ograniczenie opisane w dokumentacji)")
        self.assertEqual(basis[0]["project"], "proj")
        with open(self.mirror(), encoding="utf-8") as fh:
            before = fh.read()
        with self.assertRaises(HistoryChanged):
            with self.journal.unchanged("K-1", basis):
                self.journal.append_complete("K-1", "card.resumed")
        self.assertEqual(self.text(), before, "odmowa bez zapisu")
        self.full.clear()
        self.clock.now += FULL_READ_PERIOD - 1
        self.journal.read_complete("K-1")
        self.assertEqual(self.full, [], "przed upływem okresu pamięć")
        self.clock.now += 1
        events = self.journal.read_complete("K-1")
        self.assertEqual(self.full, ["K-1"], "po upływie okresu pełny odczyt")
        self.assertEqual(events, self.fresh())
        self.assertEqual(events[0]["project"], "prok")

    def test_increment_does_not_extend_the_full_read_time(self):
        self.warm()
        self.clock.now += FULL_READ_PERIOD / 2
        self.external_append(_line(_event(3)))
        self.journal.read_complete("K-1")
        self.clock.now += FULL_READ_PERIOD / 2
        self.full.clear()
        self.journal.read_complete("K-1")
        self.assertEqual(self.full, ["K-1"], "okres liczony od ostatniego pełnego odczytu, nie od przyrostu")

    def test_edit_breaking_the_strict_rule_is_unknown_after_the_period(self):
        self.warm()
        self.edit_and_append('"card": "K-1", "type": "card.started"', '"card": "K-2", "type": "card.started"')
        self.journal.read_complete("K-1")
        self.clock.now += FULL_READ_PERIOD
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.journal.read_complete("K-1")
        self.assertNotIn("K-1", self.journal._strict_memory)


class IncrementErrorsTest(JournalMemoryCase):
    CASES = {
        "torn": '{"v": 1, "seq": 3, "card": "K-1", "type": "flow.notice"',
        "seq-gap": _line(_event(5)),
        "foreign-card": _line(_event(3, card="K-2")),
        "unknown-identity": _line({**_event(3), "type": "step.started", "step": "x"}),
    }

    def test_errors_in_the_increment_stay_unknown_on_every_read(self):
        for name, tail in self.CASES.items():
            with self.subTest(name):
                text = self.text()
                self.warm()
                self.external_append(tail)
                for _ in range(3):
                    with self.assertRaises(ValueError):
                        self.journal.read_complete("K-1")
                    self.assertNotIn("K-1", self.journal._strict_memory, "błąd nie zostaje jako historia")
                with self.assertRaises(ValueError):
                    self.journal.read_fresh("K-1")
                self.write_in_place(text)            # naprawa pliku: znów poprawna historia
                self.assertEqual(len(self.journal.read_complete("K-1")), 2)

    def test_error_of_a_full_read_is_not_remembered(self):
        self.write_in_place(self.text() + self.CASES["torn"])
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.journal.read_complete("K-1")
        self.assertEqual(self.full, ["K-1", "K-1"])
        self.assertNotIn("K-1", self.journal._strict_memory)

    def test_missing_journal_drops_the_entry(self):
        self.warm()
        os.unlink(self.mirror())
        os.unlink(self.journal.path("K-1"))
        with self.assertRaises(OSError):
            self.journal.read_complete("K-1")
        self.assertNotIn("K-1", self.journal._strict_memory)


class FreshReadTest(JournalMemoryCase):
    def test_unchanged_detects_an_external_append_despite_a_memory_hit(self):
        basis = self.warm()
        self.assertEqual(self.journal.read_complete("K-1"), basis)        # trafienie
        self.external_append(_line(_event(3)))
        before = self.text()
        with self.assertRaises(HistoryChanged):
            with self.journal.unchanged("K-1", basis):
                self.journal.append_complete("K-1", "card.resumed")
        self.assertEqual(self.text(), before)
        self.assertEqual(self.full, ["K-1"], "porównanie przed zapisem: świeży pełny odczyt")

    def test_gate_writes_read_fresh_and_bypass_the_memory(self):
        self.warm()
        for write in (lambda: self.journal.append("K-1", "card.resumed"),
                      lambda: self.journal.append_complete("K-1", "card.paused")):
            self.full.clear()
            with mock.patch.object(self.journal, "read_complete_version",
                                   side_effect=AssertionError("zapis bramki nie korzysta z pamięci")):
                write()
            self.assertEqual(self.full, ["K-1"])

    def test_fresh_read_neither_uses_nor_fills_the_memory(self):
        self.journal._strict_memory.clear()
        self.journal.read_fresh("K-1")
        self.assertNotIn("K-1", self.journal._strict_memory)
        self.warm()
        self.journal.read_fresh("K-1")
        self.journal.read_fresh("K-1")
        self.assertEqual(self.full, ["K-1", "K-1"])

    def test_strict_readers_refuse_presentation(self):
        with self.journal.presentation():
            for read in (self.journal.read_fresh, self.journal.read_complete, self.journal.version):
                with self.assertRaises(journal_module.StrictReadInPresentation):
                    read("K-1")


class ForgetTest(JournalMemoryCase):
    def test_forget_except_drops_cards_outside_the_registry(self):
        self.warm("K-1")
        self.warm("K-2")
        self.journal.forget_except(["K-2"])
        self.assertEqual(set(self.journal._strict_memory), {"K-2"})


if __name__ == "__main__":
    unittest.main()
