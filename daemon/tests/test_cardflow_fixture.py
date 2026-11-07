"""PT-215: monitor testu kończy pracę przed usunięciem środowiska i danych fixture."""
import threading

from .test_cardflow import FlowInProcess
from .privatebus import app


class FixtureIsolationTest(FlowInProcess):
    def test_stop_joins_running_worker_and_discards_pending_callback(self):
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
        self.assertFalse(delivered.is_set(), "po zamknięciu monitor nie dostarcza wyników Qt")
        self.flow = self.make_flow()
        app().processEvents()
        self.assertFalse(delivered.is_set(), "stary callback nie może przejść do kolejnego monitora")
