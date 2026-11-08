"""Próbnik stosów usługi (08.11: diagnoza CPU bez ptrace)."""
import os
import tempfile
import threading
import time
import unittest

from tide_daemon import sampler


def busy_loop(stop):
    while not stop.is_set():
        sum(i * i for i in range(2000))


class SamplerTest(unittest.TestCase):
    def test_busy_thread_dominates_and_waiting_thread_is_counted_separately(self):
        stop = threading.Event()
        worker = threading.Thread(target=busy_loop, args=(stop,), name="busy-worker", daemon=True)
        sleeper = threading.Thread(target=stop.wait, name="sleeper", daemon=True)
        worker.start(); sleeper.start()
        try:
            result = sampler.sample(0.5, interval=0.005, skip=threading.get_ident())
        finally:
            stop.set(); worker.join(); sleeper.join()
        self.assertGreater(result["samples"], 20)
        self.assertTrue(any("busy_loop" in name for name in result["total"]))
        busy = sum(n for (thread, _), n in result["stacks"].items() if thread == "busy-worker")
        self.assertGreater(busy, result["samples"] // 2)
        self.assertTrue(any(thread == "sleeper" for (thread, _), _n in result["idle"].items()))
        self.assertFalse(any(thread == "sleeper" for (thread, _), _n in result["stacks"].items()))

    def test_request_file_triggers_one_report_and_is_consumed(self):
        with tempfile.TemporaryDirectory() as runtime, tempfile.TemporaryDirectory() as out:
            old = os.environ.get("XDG_RUNTIME_DIR")
            os.environ["XDG_RUNTIME_DIR"] = runtime
            stop = threading.Event()
            try:
                thread = threading.Thread(target=sampler.watch, kwargs={"check_every": 0.05, "stop": stop,
                                                                       "folder": out}, daemon=True)
                thread.start()
                with open(sampler.request_path(), "w") as fh:
                    fh.write("0.3")
                deadline = time.monotonic() + 10
                while not os.listdir(out) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(os.path.exists(sampler.request_path()), "żądanie zużyte")
                reports = os.listdir(out)
                self.assertEqual(len(reports), 1)
                text = open(os.path.join(out, reports[0]), encoding="utf-8").read()
                self.assertIn("Funkcje — czas łączny", text)
                self.assertEqual(os.stat(os.path.join(out, reports[0])).st_mode & 0o777, 0o600)
            finally:
                stop.set()
                if old is None:
                    os.environ.pop("XDG_RUNTIME_DIR", None)
                else:
                    os.environ["XDG_RUNTIME_DIR"] = old

    def test_bad_request_content_falls_back_to_default_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as runtime:
            path = os.path.join(runtime, "req")
            for text, expected in (("", 60), ("abc", 60), ("5", 5.0), ("99999", 600), ("0", 1.0)):
                with open(path, "w") as fh:
                    fh.write(text)
                self.assertEqual(sampler._requested_seconds(path), expected, text)
                self.assertFalse(os.path.exists(path))
            self.assertIsNone(sampler._requested_seconds(path))


if __name__ == "__main__":
    unittest.main()
