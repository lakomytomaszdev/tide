"""PT-315: zatrzymana jednostka outside oddaje wynik od razu (atrapa `systemctl`), bez czekania do OUTSIDE_TIMEOUT."""
import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from tide_daemon.cardflow import engine as engine_mod
from tide_daemon.cardflow import testrerun
from tide_daemon.tasks import stepstop

from .test_cardflow import FlowInProcess

STEP = "K-1-PT-1-9"


def plain_outside_run(events, name):
    started = [e for e in events if e.get("type") == "outside.started" and e.get("step") == name]
    done = [e for e in events if e.get("type") == "outside.finished" and e.get("step") == name]
    return "ok", (started[-1] if started else None), (done[-1] if done else None)


class OutsideStoppedTest(FlowInProcess):
    def setUp(self):
        super().setUp()
        self.card_id = self.card(description="Dodaj eksport.")
        self.flow.stop()
        self.engine = self.flow.engine
        self.engine._outside_active = None
        self.dir = tempfile.mkdtemp(prefix="outside-stopped-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.dir]))
        os.makedirs(os.path.join(self.dir, "in"))
        self.log = os.path.join(self.dir, "outside.log")
        with open(self.log, "w", encoding="utf-8") as fh:
            fh.write("początek\nkoniec wyniku\n")
        self.step = {"step": STEP, "ticket": "PT-1", "stepDir": self.dir}
        self.engine.journal.append(self.card_id, "step.started", **self.step)
        self.engine.journal.append(self.card_id, "outside.started", step=STEP, ticket="PT-1", command="make test",
                                   unit="tide-outside-" + STEP, log=self.log,
                                   exitFile=os.path.join(self.dir, "in", "exit-code"))
        # Tożsamość procesu (PT-288) to osobny kontrakt z własnymi testami; tu liczy się wyłącznie stan jednostki i pliku
        # kodu, więc historia jest zwykłym odczytem dziennika, a redukcja bierze ostatni start i jego zakończenie.
        for target, name, new in ((self.engine, "_outside_history", lambda card, step=None, **kw:
                                   self.engine.journal.read(card)),
                                  (engine_mod.processidentity, "outside_run", plain_outside_run)):
            patcher = mock.patch.object(target, name, new)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls = []
        self.state_code = 3                       # `systemctl is-active`: nieaktywna
        self.state_text = {3: "inactive\n", 0: "active\n"}   # stdout `is-active` dla kodu
        patcher = mock.patch.object(stepstop, "_systemctl", self.fake_systemctl)
        patcher.start()
        self.addCleanup(patcher.stop)
        grace = mock.patch.object(engine_mod, "OUTSIDE_STOP_GRACE", 0)
        grace.start()
        self.addCleanup(grace.stop)

    def fake_systemctl(self, *args, timeout=30):
        self.calls.append((args, timeout))
        code = self.state_code if args[0] == "is-active" else 0
        out = self.state_text.get(code, "") if args[0] == "is-active" else ""
        return subprocess.CompletedProcess(args, code, out.encode(), b"")

    def put(self, name, data, mode="w"):
        path = os.path.join(self.dir, "in", name)
        with open(path, mode) as fh:
            fh.write(data)
        return path

    def ready(self):
        return self.engine._outside_ready(self.card_id, self.step, {"command": "make test"}, {"choice": "run"})

    def finished(self):
        return [e for e in self.engine.journal.read(self.card_id) if e.get("type") == "outside.finished"]

    def test_stopped_unit_with_complete_tmp_code_finishes_in_the_same_pass(self):
        self.put("exit-code.tmp", "143\n")
        answer = self.ready()
        self.assertEqual([e["exit"] for e in self.finished()], [143])
        self.assertIn("Kod wyjścia: 143", answer["answer"])
        self.assertIn("koniec wyniku", answer["answer"])

    def test_stopped_unit_without_any_code_file_is_reported_as_stopped(self):
        answer = self.ready()
        done = self.finished()
        self.assertEqual([e["exit"] for e in done], ["zatrzymane"])
        self.assertIn("koniec wyniku", done[0]["tail"])
        self.assertIn("Kod wyjścia: zatrzymane", answer["answer"])
        self.assertIn("Koniec wyniku:\npoczątek", answer["answer"])

    def test_unknown_unit_counts_as_stopped(self):
        self.state_code = 4
        self.ready()
        self.assertEqual([e["exit"] for e in self.finished()], ["zatrzymane"])

    def test_incomplete_or_suspicious_tmp_file_gives_no_code(self):
        for label, write in (("bez końca linii", lambda: self.put("exit-code.tmp", "143")),
                             ("za długi", lambda: self.put("exit-code.tmp", "1430\n")),
                             ("nie liczba", lambda: self.put("exit-code.tmp", "ab\n")),
                             ("zera wiodące", lambda: self.put("exit-code.tmp", "07\n")),
                             ("pusty", lambda: self.put("exit-code.tmp", "")),
                             ("ponad 255", lambda: self.put("exit-code.tmp", "256\n"))):
            with self.subTest(label):
                path = os.path.join(self.dir, "in", "exit-code.tmp")
                if os.path.lexists(path):
                    os.remove(path)
                write()
                self.assertEqual(stepstop.read_exit_code(self.dir, "exit-code.tmp"), None)
                self.assertEqual(self.engine._outside_stopped(
                    {"unit": "u", "ts": "2000-01-01T00:00:00+00:00"}, self.dir), "zatrzymane")

    def test_tmp_symlink_and_non_regular_file_give_no_code(self):
        target = self.put("cel", "0\n")
        os.symlink(target, os.path.join(self.dir, "in", "exit-code.tmp"))
        self.assertIsNone(stepstop.read_exit_code(self.dir, "exit-code.tmp"))
        self.ready()
        self.assertEqual([e["exit"] for e in self.finished()], ["zatrzymane"])

    def test_tmp_fifo_does_not_block_and_gives_no_code(self):
        os.mkfifo(os.path.join(self.dir, "in", "exit-code.tmp"))
        self.assertIsNone(stepstop.read_exit_code(self.dir, "exit-code.tmp"))

    def test_active_unit_without_code_keeps_waiting_until_the_timeout(self):
        self.state_code = 0
        self.put("exit-code.tmp", "143\n")
        self.assertIsNone(self.ready())
        self.assertEqual(self.finished(), [])
        # Limit czasu działa jak dotąd.
        with mock.patch.object(engine_mod, "OUTSIDE_TIMEOUT", 0):
            self.assertIn("Kod wyjścia: przekroczony czas", self.ready()["answer"])
        self.assertEqual([e["exit"] for e in self.finished()], ["przekroczony czas"])

    def test_failed_unit_counts_as_stopped(self):
        self.state_text[3] = "failed\n"
        self.ready()
        self.assertEqual([e["exit"] for e in self.finished()], ["zatrzymane"])

    def test_deactivating_unit_waits_like_an_active_one(self):
        # `systemctl stop` zostawia scope w `deactivating` (kod 3 jak `inactive`), a procesy jeszcze żyją.
        self.state_text[3] = "deactivating\n"
        self.put("exit-code.tmp", "143\n")
        self.assertIsNone(self.ready())
        self.assertEqual(self.finished(), [])
        self.assertNotIn("stop", [args[0] for args, _ in self.calls])
        self.state_text[3] = "activating\n"
        self.assertIsNone(self.ready())
        self.assertEqual(self.finished(), [])
        self.state_text[3] = ""                   # pusty stdout przy kodzie 3 to nie dowód zatrzymania
        self.assertIsNone(self.ready())
        self.assertEqual(self.finished(), [])
        self.assertNotIn("stop", [args[0] for args, _ in self.calls])

    def test_state_read_error_does_not_end_the_running_command(self):
        for code in (1, 255):
            self.state_code = code
            self.assertIsNone(self.ready())
        self.assertEqual(self.finished(), [])

    def test_state_is_read_with_a_short_timeout(self):
        self.ready()
        reads = [timeout for args, timeout in self.calls if args[0] == "is-active"]
        self.assertEqual(reads, [engine_mod.OUTSIDE_STATE_TIMEOUT])
        self.assertLessEqual(engine_mod.OUTSIDE_STATE_TIMEOUT, 5)

    def test_unit_registered_late_is_not_taken_for_stopped(self):
        with mock.patch.object(engine_mod, "OUTSIDE_STOP_GRACE", 60):
            self.assertIsNone(self.ready())
        self.assertEqual(self.finished(), [])
        self.assertFalse([c for c in self.calls if c[0][0] == "is-active"])

    def test_finished_between_reads_prefers_the_final_exit_code(self):
        # Wrapper zdążył zmienić nazwę pliku po pierwszym odczycie: bez tmp, jest exit-code.
        real = stepstop.unit_state

        def late(unit, timeout=30):
            self.put("exit-code", "0\n")
            return real(unit, timeout=timeout)

        with mock.patch.object(stepstop, "unit_state", late):
            self.ready()
        self.assertEqual([e["exit"] for e in self.finished()], [0])

    def test_restart_with_stopped_unit_settles_the_same_way(self):
        self.put("exit-code.tmp", "143\n")
        self.engine._outside_active = None          # świeży proces usługi nie pamięta aktywnych poleceń
        self.ready()
        self.assertEqual([e["exit"] for e in self.finished()], [143])
        # Kolejny przebieg nie dopisuje drugiego wyniku, a odpowiedź jest ta sama.
        again = self.ready()
        self.assertEqual(len(self.finished()), 1)
        self.assertIn("Kod wyjścia: 143", again["answer"])

    def test_stopped_result_counts_as_a_cut_run_for_the_test_rerun(self):
        self.assertTrue(testrerun.interrupted("zatrzymane"))
        self.assertTrue(testrerun.interrupted(" zatrzymane "))
        for other in ("zatrzymany", "", "stopped", 0, 1):
            self.assertFalse(testrerun.interrupted(other), other)
        # Wynik „zatrzymane” nie jest też sukcesem: `substitute` nie traktuje go jak kodu 0 (pole niepuste).
        events = [{"type": "outside.started", "step": "s", "command": "make test-changed BASE=abc",
                   "worktree": "/nonexistent", "head": "x", "log": "/nonexistent"},
                  {"type": "outside.finished", "step": "s", "exit": "zatrzymane"}]
        self.assertEqual(testrerun.substitute(events, "/nonexistent", "make test-changed BASE=abc")[0],
                         "make test-changed BASE=abc")


if __name__ == "__main__":
    unittest.main()
