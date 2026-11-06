"""`stepstop` kroku w pudełku (PT-181, #18): zatrzymanie przez `BoxRunner.kill` (`systemctl --user kill` w pudełku
przez `incus exec`), commit WIP na hoście jak dziś — także po restarcie usługi, gdy krok znany jest tylko z wpisu.
Atrapa `incus` i jednostek pudełka: boxflow.py.
"""
import os
import unittest

from tide_daemon.box import state as bs

from . import flowfakes
from .boxflow import SLUG, BoxFlow
from .privatebus import wait_until


class StepStopBoxTest(BoxFlow):
    guard_minutes = 0.05   # 3 s: strażnik zatrzymuje krok bez postępu

    def unit_pid_box(self, step) -> int:
        def read():
            try:
                with open(os.path.join(self.units, step["unit"] + ".service"), encoding="utf-8") as fh:
                    return int(fh.read())
            except (OSError, ValueError):
                return 0
        return wait_until(read, timeout=10)

    def stopped(self, card_id, step, pid):
        found = wait_until(lambda: self.of(card_id, "step.finished"), timeout=40)
        self.assertTrue(found, self.events(card_id))
        finished = found[0]
        self.assertEqual((finished["result"], finished["reason"]), ("error", "no-progress"), finished)
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=10), "jednostka w pudełku zatrzymana")
        kills = [line for line in self.box_log() if line.startswith("box-systemctl kill")]
        self.assertTrue(kills, self.box_log())
        self.assertIn(step["unit"] + ".service", kills[0])
        self.assertTrue(finished["wip"]["stopped"])
        self.assert_changes_kept(finished, step, step["branch"])
        # step.finished jest utrwalane przed _release_box; po restarcie sprzątanie może jeszcze trwać.
        self.assertTrue(wait_until(lambda: not bs.running_steps(SLUG, SLUG), timeout=10),
                        bs.running_steps(SLUG, SLUG))
        # Na hoście nic nie było zatrzymywane: atrapa systemctl hosta nie dostała `kill`.
        with open(self.calls, encoding="utf-8") as fh:
            self.assertFalse([line for line in fh if line.startswith("systemctl") and " kill" in line])
        return finished

    def test_guard_stop_kills_the_box_unit_and_commits_wip_on_the_host(self):
        self.build_box()
        card_id = self.card(description="ATRAPA: czekaj")
        self.start(card_id)
        step = self.started(card_id)
        self.assertEqual(step.get("runner"), "box")
        pid = self.unit_pid_box(step)
        self.assertTrue(flowfakes.alive(pid))
        self.stopped(card_id, step, pid)

    def test_after_restart_the_box_step_is_stopped_by_its_entry(self):
        self.build_box()
        card_id = self.card(description="ATRAPA: czekaj")
        self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid_box(step)
        self.restart()
        self.assertEqual(self.flow.engine.procs, {})
        self.stopped(card_id, step, pid)


if __name__ == "__main__":
    unittest.main()
