"""Mruganie w tle w BoardCard (PT-325, PT-327, karta K-92): segmenty „pracuje” mrugają z jednej wspólnej fazy strony
(Timer co 500 ms, trzy poziomy krycia), tylko gdy widok jest aktywny, a okno widoczne (nieukryte i niezminimalizowane).

Sprawdzamy stan jawnie (visibility okna, view.active), a nie przez `root.visible`, które po minimalizacji zostaje true.
Żadna nieskończona `Animation` nie może biec w drzewie strony (ciągła animacja wymusza klatki z pełną częstotliwością
i przerysowuje całe okno: ≈ 206% CPU w pomiarze #5); faza nie rośnie z liczbą pracujących segmentów.
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
anim = lambda: js("page.workPulseTimer.running")
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
        self.assertEqual(out["manySeg"][1], 0, "mruganie to Timer, nie ciągła animacja wymuszająca klatki")

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
        self.assertGreater(max(out["samples"]) - min(out["samples"]), 0.05, "faza pracuje mruga przy widocznym oknie")
        self.assertLessEqual(len({round(s, 2) for s in out["samples"]}), 3, "krycie skacze po co najwyżej trzech poziomach")
        for phase, (_, opacity) in colors.items():
            if phase != "pracuje":
                self.assertEqual(opacity, 1, "tylko pracuje pulsuje")
        self.assertEqual(out["still"]["pracuje"][1], 1, "w tle pracuje ma pełne krycie i własny kolor")
        self.assertNotEqual(out["still"]["pracuje"][0], out["still"]["todo"][0])

    def test_blink_is_slow_timer_not_per_frame(self):
        out = self.run_scenario("""
working(cards[0], ["pracuje"])
set_snapshot(snap)
until(anim)
out["interval"] = js("page.workPulseTimer.interval")
out["repeat"] = js("page.workPulseTimer.repeat")
""")
        self.assertTrue(out["repeat"])
        self.assertGreaterEqual(out["interval"], 400, "przełączanie rzadziej niż ~2,5 razy na sekundę, nie co klatkę")

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


FULL_PREP = """
def with_phases(card, states):
    card["derived"] = {"column": card.get("boardColumn", card["column"]), "flow": {"state": "running"}, "tickets": [],
                       "phases": [{"id": f"p{i}", "label": f"Etap {i}", "state": s} for i, s in enumerate(states)]}
def bars():
    return js("(function () { const out = []; function walk(it) { if (!it) return; "
              "if (it.objectName === 'fullPhaseBar') out.push([it.parent.phaseState, it.opacity]); "
              "for (const ch of it.children) walk(ch) } walk(page.cardFullView); return out })()")
def now_opacity():
    return [o for s, o in bars() if s == "now"]
def open_full(card):
    set_snapshot(snap)
    js("page.openFull('" + card["id"] + "')")
"""


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class FullCardPulseTest(ProbeCase):
    """Karta K-97 (PT-336): pasek etapu `now` pełnej karty mruga wspólną fazą strony."""

    def run_scenario(self, body: str) -> dict:
        return self.probe(PREP + FULL_PREP + body)

    def test_now_stage_blinks_others_stand_and_close_stops_timer(self):
        out = self.run_scenario("""
with_phases(cards[0], ["done", "you", "hold", "now", "todo", "skipped"])
open_full(cards[0])
out["running"] = until(anim)
samples = []
for _ in range(10):
    samples.append([now_opacity()[0], [o for s, o in bars() if s != "now"]]); wait(120)
out["samples"] = samples
out["levels"] = js("page.workPulseLevels")
js("page.closeFull()")
wait(100)
out["closedRunning"] = anim()
out["closedPulse"] = pulse()
""")
        self.assertTrue(out["running"], "pełna karta z etapem now uruchamia Timer bez pracujących segmentów")
        nows = [s[0] for s in out["samples"]]
        self.assertTrue(all(any(abs(n - lv) < 0.01 for lv in out["levels"]) for n in nows), "krycie z wspólnych poziomów")
        self.assertGreater(max(nows) - min(nows), 0.05, "pasek now mruga")
        for _, others in out["samples"]:
            self.assertEqual(others, [1] * 5, "you, hold, done, todo, skipped stoją na 1.0 także przy biegnącym Timerze")
        self.assertFalse(out["closedRunning"], "zamknięcie pełnej karty zatrzymuje Timer")
        self.assertEqual(out["closedPulse"], 1.0)

    def test_inactive_view_and_hidden_window_stop_pulse(self):
        out = self.run_scenario("""
with_phases(cards[0], ["done", "now", "todo"])
open_full(cards[0])
out["running"] = until(anim)
js("active = false")
wait(50)
out["inactive"] = [anim(), now_opacity()]
js("active = true")
out["back"] = until(anim)
window.setProperty("visibility", 3)
wait(100)
out["minimized"] = [anim(), now_opacity()]
window.setProperty("visibility", 2)
out["shown"] = until(anim)
window.hide()
wait(100)
out["hidden"] = [anim(), now_opacity()]
""")
        self.assertTrue(out["running"])
        self.assertEqual(out["inactive"], [False, [1]])
        self.assertTrue(out["back"], "aktywacja wznawia Timer")
        self.assertEqual(out["minimized"], [False, [1]])
        self.assertTrue(out["shown"])
        self.assertEqual(out["hidden"], [False, [1]])

    def test_no_now_stage_no_timer_and_no_infinite_animation(self):
        out = self.run_scenario("""
with_phases(cards[0], ["done", "you", "todo"])
open_full(cards[0])
wait(100)
out["noNow"] = [anim(), now_opacity()]
with_phases(cards[0], ["done", "now", "todo"])
set_snapshot(snap)
out["now"] = until(anim)
out["infinite"] = js("(function () { let n = 0; function walk(o) { if (!o) return; "
                     "if (o.running === true && o.loops === Animation.Infinite) n++; "
                     "for (const ch of (o.resources || [])) walk(ch); for (const ch of (o.children || [])) walk(ch) } "
                     "walk(page); return n })()")
out["timers"] = js("(function () { let n = 0; function walk(o) { if (!o) return; "
                   "if (o.objectName === 'workPulseTimer') n++; "
                   "for (const ch of (o.resources || [])) walk(ch); for (const ch of (o.children || [])) walk(ch) } "
                   "walk(page); return n })()")
""")
        self.assertEqual(out["noNow"], [False, []], "bez etapu now nie biegnie Timer")
        self.assertTrue(out["now"])
        self.assertEqual(out["infinite"], 0)
        self.assertEqual(out["timers"], 1, "nadal jeden Timer fazy")


if __name__ == "__main__":
    unittest.main()
