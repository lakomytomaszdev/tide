"""Animacje w tle w BoardCard (PT-325, karta K-92): pulsowanie segmentów „pracuje” z jednej wspólnej fazy strony,
tylko gdy widok jest aktywny, a okno widoczne (nieukryte i niezminimalizowane).

Sprawdzamy stan jawnie (visibility okna, view.active), a nie przez `root.visible`, które po minimalizacji zostaje true.
Liczba działających animacji nieskończonych to liczba biegnących `Animation` w drzewie strony (obiekt wspólnej fazy
plus ewentualne inne); nie rośnie z liczbą pracujących segmentów.
"""
import os
import shutil
import unittest

from .test_view import ProbeCase

PREP = """
snap = json.loads(backend.snapshotJson)
def working(card, phases):
    card["derived"] = {"column": card.get("boardColumn", card["column"]), "flow": {"state": "running", "waveLabel": "Fala 1"},
                       "tickets": [{"id": f"PT-{i}", "phase": p, "wave": 1} for i, p in enumerate(phases)]}
cards = [c for c in snap["cards"] if c.get("boardColumn", c["column"]) != "archived"]
anim = lambda: js("page.workPulseAnimation.running")
pulse = lambda: js("page.workPulse")
def seg_opacities():
    return js("(function () { const out = []; function walk(it) { if (!it) return; "
              "if (it.objectName === 'ticketSegment') out.push(it.opacity); "
              "for (const ch of it.children) walk(ch) } walk(page); return out })()")
def seg_count():
    return len(seg_opacities())
"""


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class AnimationsTest(ProbeCase):
    def run_scenario(self, body: str) -> dict:
        return self.probe(PREP + body)

    def test_view_inactive_stops_and_activation_restarts(self):
        out = self.run_scenario("""
working(cards[0], ["pracuje", "pracuje", "todo"])
set_snapshot(snap)
out["running"] = until(anim)
js("active = false")
wait(50)
out["inactiveRunning"] = anim()
out["inactivePulse"] = pulse()
js("active = true")
out["backRunning"] = until(anim)
""")
        self.assertTrue(out["running"])
        self.assertFalse(out["inactiveRunning"], "view.active=false zatrzymuje animację")
        self.assertEqual(out["inactivePulse"], 1.0, "w tle faza stoi na pełnym kryciu")
        self.assertTrue(out["backRunning"], "po aktywacji pulsowanie wraca")

    def test_hidden_and_minimized_window_stop_and_show_restarts(self):
        out = self.run_scenario("""
working(cards[0], ["pracuje"])
set_snapshot(snap)
out["running"] = until(anim)
window.setProperty("visibility", 3)  # Window.Minimized
wait(100)
out["minVisibility"] = str(window.property("visibility"))
out["minRootVisible"] = js("visible")
out["minRunning"] = anim()
window.setProperty("visibility", 2)  # Windowed
wait(100)
out["shownRunning"] = until(anim)
window.hide()
wait(100)
out["hideVisibility"] = str(window.property("visibility"))
out["hideRunning"] = anim()
out["hidePulse"] = pulse()
window.show()
wait(100)
out["reshownRunning"] = until(anim)
""")
        self.assertTrue(out["running"])
        self.assertIn("Minimized", out["minVisibility"], "okno offscreen odwzorowało minimalizację")
        self.assertFalse(out["minRunning"], "zminimalizowane okno zatrzymuje animację")
        self.assertTrue(out["shownRunning"])
        self.assertIn("Hidden", out["hideVisibility"])
        self.assertFalse(out["hideRunning"], "window.hide() zatrzymuje animację")
        self.assertEqual(out["hidePulse"], 1.0)
        self.assertTrue(out["reshownRunning"], "po ponownym pokazaniu pulsowanie wraca")

    def test_single_phase_regardless_of_working_segment_count(self):
        out = self.run_scenario("""
working(cards[0], ["pracuje"])
set_snapshot(snap)
until(anim)
def running_animations():
    return js("(function () { let n = 0; function walk(o) { if (!o) return; "
              "if (o.running === true && o.loops === Animation.Infinite) n++; "
              "for (const ch of (o.resources || [])) walk(ch); for (const ch of (o.children || [])) walk(ch) } "
              "walk(page); return n })()")
out["oneSeg"] = [seg_count(), running_animations()]
for c in cards[:6]:
    working(c, ["pracuje", "pracuje", "pracuje", "review", "kolejka", "ty"])
set_snapshot(snap)
wait(100)
out["manySeg"] = [seg_count(), running_animations()]
out["working"] = js("page.hasWorking")
""")
        self.assertTrue(out["working"])
        self.assertGreater(out["manySeg"][0], out["oneSeg"][0])
        self.assertEqual(out["oneSeg"][1], out["manySeg"][1], "liczba animacji nie rośnie z liczbą segmentów")
        self.assertLessEqual(out["manySeg"][1], 1)

    def test_working_phase_stays_distinguishable(self):
        out = self.run_scenario("""
working(cards[0], ["pracuje", "gotowe", "review", "kolejka", "ty", "todo"])
set_snapshot(snap)
out["running"] = until(anim)
read = lambda: js("(function () { const out = {}; function walk(it) { if (!it) return; "
                  "if (it.objectName === 'ticketSegment') out[it.parent.phase] = [String(it.color), it.opacity]; "
                  "for (const ch of it.children) walk(ch) } walk(page.findCard('" + cards[0]["id"] + "')); return out })()")
out["colors"] = read()
samples = []
for _ in range(10):
    samples.append(read()["pracuje"][1]); wait(120)
out["samples"] = samples
js("active = false")
wait(50)
out["still"] = read()
""")
        colors = out["colors"]
        self.assertEqual(len({v[0] for v in colors.values()}), len(colors), "każda faza ma inny kolor")
        self.assertTrue(all(0.44 <= s <= 1.0 for s in out["samples"]))
        self.assertGreater(max(out["samples"]) - min(out["samples"]), 0.05, "faza pracuje pulsuje przy widocznym oknie")
        for phase, (_, opacity) in colors.items():
            if phase != "pracuje":
                self.assertEqual(opacity, 1, "tylko pracuje pulsuje")
        self.assertEqual(out["still"]["pracuje"][1], 1, "w tle pracuje ma pełne krycie i własny kolor")
        self.assertNotEqual(out["still"]["pracuje"][0], out["still"]["todo"][0])

    def test_tickets_changes_switch_phase_on_and_off_cleanly(self):
        out = self.run_scenario("""
set_snapshot(snap)
out["idle"] = anim()
working(cards[0], ["pracuje", "todo"])
set_snapshot(snap)
out["enter"] = until(anim)
cards[0]["derived"]["tickets"].append({"id": "PT-9", "phase": "pracuje", "wave": 1})
set_snapshot(snap)
out["added"] = anim()
cards[0]["derived"]["tickets"] = [{"id": "PT-0", "phase": "review", "wave": 1}]
set_snapshot(snap)
wait(100)
out["leave"] = anim()
out["leavePulse"] = pulse()
cards[0]["derived"] = None
set_snapshot(snap)
out["noFlow"] = anim()
working(cards[1], ["pracuje"])
set_snapshot(snap)
out["otherCard"] = until(anim)
snap["cards"] = [c for c in snap["cards"] if c["id"] != cards[1]["id"]]
set_snapshot(snap)
wait(100)
out["removed"] = anim()
out["removedPulse"] = pulse()
""")
        self.assertFalse(out["idle"])
        self.assertTrue(out["enter"])
        self.assertTrue(out["added"])
        self.assertFalse(out["leave"], "po wyjściu ostatniego segmentu z „pracuje” faza staje")
        self.assertEqual(out["leavePulse"], 1.0)
        self.assertFalse(out["noFlow"])
        self.assertTrue(out["otherCard"])
        self.assertFalse(out["removed"], "usunięcie karty zatrzymuje fazę bez wiszącej animacji")
        self.assertEqual(out["removedPulse"], 1.0)


if __name__ == "__main__":
    unittest.main()
