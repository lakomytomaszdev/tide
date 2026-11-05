"""PT-215: monitor testu kończy pracę przed usunięciem środowiska i danych fixture."""
import threading

from .test_cardflow import FlowInProcess


class FixtureIsolationTest(FlowInProcess):
    def test_stop_joins_running_worker_and_delivers_pending_callback(self):
        entered, release, finished, delivered = (threading.Event() for _ in range(4))

        def work():
            entered.set()
            release.wait(5)
            finished.set()

        self.assertTrue(self.flow._submit(work, lambda future: delivered.set()))
        self.assertTrue(entered.wait(5))
        timer = threading.Timer(0.05, release.set)
        timer.start()
        self.addCleanup(timer.join)
        self.flow.stop()
        self.assertTrue(finished.is_set(), "fixture nie może usuwać danych działającego wątku")
        self.assertTrue(delivered.is_set(), "callback nie może przejść do następnego testu")
