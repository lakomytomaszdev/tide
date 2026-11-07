"""Kolejka poleceń poza piaskownicą według czasu zgody (05.11: K-38/PT-192 czekał ponad godzinę za K-37)."""
import types
import unittest

from tide_daemon.cardflow import engine as flow_engine


def stub(projects, busy=()):
    eng = flow_engine.FlowEngine.__new__(flow_engine.FlowEngine)
    eng._outside_waiting = {}
    eng._outside_seeded = True
    eng._project = lambda card: (None, types.SimpleNamespace(slug=projects[card], repo="", denied_commands=[]))
    eng._outside_busy = lambda card: card in busy
    return eng


class OutsideQueueTest(unittest.TestCase):
    def test_older_consent_in_same_project_goes_first(self):
        eng = stub({"K-37": "plasma-tide", "K-38": "plasma-tide"})
        self.assertFalse(eng._outside_behind("K-38", "K-38-PT-1-4", {"ts": "2026-11-05T15:14:00+01:00"}))
        self.assertTrue(eng._outside_behind("K-37", "K-37-PT-2-1", {"ts": "2026-11-05T15:42:00+01:00"}))
        eng._outside_waiting.pop("K-38-PT-1-4")      # starsze ruszyło
        self.assertFalse(eng._outside_behind("K-37", "K-37-PT-2-1", {"ts": "2026-11-05T15:42:00+01:00"}))

    def test_other_project_waits_only_for_older_consent_that_can_start(self):
        eng = stub({"K-37": "plasma-tide", "K-46": "streamer"}, busy={"K-37"})
        self.assertFalse(eng._outside_behind("K-37", "K-37-PT-1-1", {"ts": "2026-11-05T15:00:00+01:00"}))
        # starsza zgoda K-37 nie może ruszyć (jej projekt zajęty), więc nie blokuje streamer
        self.assertFalse(eng._outside_behind("K-46", "K-46-KAMP-1-1", {"ts": "2026-11-05T15:10:00+01:00"}))

    def test_stale_consent_leaves_the_queue(self):
        eng = stub({"K-37": "plasma-tide", "K-38": "plasma-tide"})
        eng._outside_waiting["K-38-PT-1-4"] = ("K-38", "plasma-tide", 0.0, 0.0)
        self.assertFalse(eng._outside_behind("K-37", "K-37-PT-2-1", {"ts": "2026-11-05T15:42:00+01:00"}))


    def test_queue_is_rebuilt_from_journals_after_restart(self):
        import time
        from datetime import datetime, timedelta
        ts = lambda minutes: (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        journals = {
            "K-38": [{"type": "step.finished", "step": "K-38-PT-1-4", "outside": True, "ts": ts(70)},
                     {"type": "step.decision", "step": "K-38-PT-1-4", "choice": "run", "ts": ts(70)}],
            "K-37": [{"type": "step.finished", "step": "K-37-PT-2-1", "outside": True, "ts": ts(5)},
                     {"type": "step.decision", "step": "K-37-PT-2-1", "choice": "run", "ts": ts(5)}],
        }
        eng = stub({"K-37": "plasma-tide", "K-38": "plasma-tide"})
        eng._outside_seeded = False
        eng.journal = types.SimpleNamespace(cards=lambda: list(journals), read=lambda card: journals[card])
        # K-37 jest sprawdzana pierwsza po restarcie, ale starsza zgoda K-38 z dziennika idzie przed nią
        self.assertTrue(eng._outside_behind("K-37", "K-37-PT-2-1", {"ts": ts(5)}))
        self.assertFalse(eng._outside_behind("K-38", "K-38-PT-1-4", {"ts": ts(70)}))

    def test_consent_waiting_for_a_busy_slot_keeps_its_place(self):
        # 05.11: przy zajętym miejscu zgody nie trafiały do kolejki, więc po zwolnieniu wskakiwała nowsza.
        eng = stub({"K-37": "plasma-tide", "K-38": "plasma-tide"}, busy={"K-37", "K-38"})
        eng._outside_active = {"K-37-PT-9-1": ("K-37", "unit")}
        eng._start_outside = lambda *a: self.fail("miejsce zajęte")
        eng.journal = types.SimpleNamespace(read=lambda card: [])
        step, done = {"step": "K-38-PT-1-4"}, {"command": "echo trial"}
        self.assertIsNone(eng._outside_ready("K-38", step, done, {"choice": "run", "ts": "2026-11-05T17:18:00+01:00"}))
        self.assertIn("K-38-PT-1-4", eng._outside_waiting)
        eng._outside_busy = lambda card: False
        self.assertTrue(eng._outside_behind("K-37", "K-37-PT-2-5", {"ts": "2026-11-05T17:24:00+01:00"}))

if __name__ == "__main__":
    unittest.main()
