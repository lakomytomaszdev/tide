"""Kafelki widżetów w docku: teksty (liczba mnoga, stany puste), wymiary i podpowiedzi;
popup kafelka agentów (SessionsView: zwarte wiersze, jak w pasku bocznym) we wszystkich stanach.

Kafelki (CompactAgents, CompactWorktrees, CompactSystem, CompactLimits) nie zależą od
PlasmoidItem, więc qml6 offscreen ładuje je z przykładowymi danymi — tym samym kodem,
który rysuje podgląd (widgets/preview.py). Bez Plasmy, tide-daemon i usługi Limity AI.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

from . import WIDGETS_DIR
from widgets import preview


def scenario(**changes):
    """Scenariusz „busy” z podmienionymi częściami, np. agents={"working": 1, ...}."""
    data = json.loads(json.dumps(preview.SCENARIOS["busy"]))
    for key, value in changes.items():
        data[key].update(value)
    return data


def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TileTextsTest(unittest.TestCase):
    def test_busy_day(self):
        result = preview.render(preview.SCENARIOS["busy"])
        self.assertEqual(result["agents"], {"label": "Agenci", "summary": "2 pracują · 1 czeka · 2 gotowe"})
        # Ten sam układ co agenci: sam podpis, a w wartości niezerowe części z kropkami.
        self.assertEqual(result["worktrees"], {"label": "Worktrees", "detail": "",
                                               "summary": "5 w pracy · 15 do sprzątnięcia"})
        self.assertEqual(result["system"]["lines"], ["CPU 34%", "RAM 61%", "Dysk 88%"])
        self.assertEqual(result["limits"]["lines"], ["GPT 91%", "Claude 86%"])
        # Dwie linie mieszczą się w 48 px kafelka, a cztery kafelki z odstępami 12 px — w 900 px
        # (dock ma 2560 px, aplikacje są pośrodku). Było 800 px, zanim agenci dostali trzecią
        # część („○ 2 gotowe”), potem 900 px; „＋” nowego zadania siedzi na kafelku agentów, bez osobnego kafelka.
        # Kafelek agentów w najszerszym wariancie (z przyciskiem „＋”) mieści się w 275 px.
        self.assertEqual(result["fits"], [True] * 4)
        self.assertLess(result["total"], 900, result["widths"])
        self.assertLessEqual(result["widths"][0], 275)

    def test_plural_forms(self):
        # Skończone liczymy jako sesje (rodzaj żeński): 1 gotowa, 2–4 gotowe, 5+ gotowych.
        cases = [((1, 0, 0), "1 pracuje"), ((2, 0, 0), "2 pracują"), ((5, 0, 0), "5 pracują"),
                 ((0, 1, 0), "1 czeka"), ((0, 3, 0), "3 czekają"), ((0, 12, 0), "12 czekają"),
                 ((0, 0, 1), "1 gotowa"), ((0, 0, 2), "2 gotowe"), ((0, 0, 5), "5 gotowych"),
                 ((0, 0, 12), "12 gotowych"), ((0, 0, 22), "22 gotowe"),
                 ((1, 1, 0), "1 pracuje · 1 czeka"), ((22, 4, 0), "22 pracują · 4 czekają"),
                 ((1, 0, 3), "1 pracuje · 3 gotowe"), ((1, 1, 2), "1 pracuje · 1 czeka · 2 gotowe")]
        for (working, waiting, done), expected in cases:
            with self.subTest(working=working, waiting=waiting, done=done):
                result = preview.render(scenario(agents={"working": working, "waiting": waiting, "done": done}))
                self.assertEqual(result["agents"]["summary"], expected)

    def test_only_finished_sessions(self):
        result = preview.render(preview.SCENARIOS["finished"])
        self.assertEqual(result["agents"]["summary"], "3 gotowe")

    def test_empty_states(self):
        quiet = preview.render(preview.SCENARIOS["quiet"])
        self.assertEqual(quiet["agents"]["summary"], "brak sesji")
        self.assertEqual(quiet["worktrees"]["summary"], "nic do sprzątnięcia")
        self.assertEqual(quiet["worktrees"]["detail"], "")
        self.assertEqual(quiet["system"]["lines"], ["CPU 7%", "RAM 100%", "Dysk —"])
        self.assertEqual(quiet["limits"]["lines"], ["GPT —", "Claude 12%"])

        offline = preview.render(preview.SCENARIOS["offline"])
        self.assertEqual(offline["agents"]["summary"], "usługa nie działa")
        self.assertEqual(offline["worktrees"]["summary"], "usługa nie działa")
        self.assertEqual(offline["system"]["lines"], ["CPU —", "RAM —", "Dysk —"])
        self.assertEqual(offline["limits"]["lines"], ["GPT —", "Claude —"])

    def test_screenshot_has_dock_height(self):
        with tempfile.TemporaryDirectory(prefix="tide-dock-png-") as tmp:
            png = os.path.join(tmp, "tiles.png")
            result = preview.render(preview.SCENARIOS["busy"], png)
            width, height = png_size(png)
        self.assertEqual(height, preview.DOCK_HEIGHT)
        self.assertEqual(width, result["total"] + 2 * preview.MARGIN)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class ToolTipTest(unittest.TestCase):
    """Podpowiedzi kafelków (Tide.Texts) — jedna linia wyjaśnienia po polsku."""

    def test_tooltips(self):
        with tempfile.TemporaryDirectory(prefix="tide-texts-") as tmp:
            pkg = preview.stage_all(tmp)
            harness = os.path.join(pkg, "agents", "contents", "ui", "Texts.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("""import QtQuick
import "tide" as Tide
Item {
    Component.onCompleted: {
        const T = Tide.Texts
        console.warn("RESULT " + JSON.stringify({
            agents: [T.agentsToolTip(true, 2, 1), T.agentsToolTip(true, 1, 3), T.agentsToolTip(true, 0, 0),
                     T.agentsToolTip(true, 0, 2), T.agentsToolTip(false, 2, 1), T.agentsToolTip(true, 1, 1, 2),
                     T.agentsToolTip(true, 0, 0, 1), T.agentsToolTip(true, 0, 0, 0)],
            summary: [T.agentsSummary(1, 0), T.agentsSummary(0, 0, 5), T.agentsSummary(0, 0)],
            worktrees: [T.worktreesToolTip(true, 15, 17179869184, 5, 48), T.worktreesToolTip(true, 0, 0, 2, 3),
                        T.worktreesToolTip(false, 1, 1, 1, 1)],
            limits: [T.limitsToolTip(true, 91, 86), T.limitsToolTip(true, null, 12.4), T.limitsToolTip(false, 1, 1)]
        }))
        Qt.quit()
    }
}
""")
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1",
                       XDG_CONFIG_DIRS="/etc/xdg")
            run = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30)
        result = json.loads(re.search(r"RESULT (\{.*\})", run.stderr).group(1))
        self.assertEqual(result["agents"], [
            "Sesje agentów AI: 2 pracują, 1 czeka na ciebie",
            "Sesje agentów AI: 1 pracuje, 3 czekają na ciebie",
            "Sesje agentów AI: żadna nie pracuje ani nie czeka",
            "Sesje agentów AI: 2 czekają na ciebie",
            "Usługa tide-daemon nie działa",
            "Sesje agentów AI: 1 pracuje, 1 czeka na ciebie, 2 gotowe",
            "Sesje agentów AI: 1 gotowa",
            "Sesje agentów AI: żadna nie pracuje ani nie czeka"])
        # Dwie liczby (starsze wywołania) działają jak dotąd.
        self.assertEqual(result["summary"], ["1 pracuje", "5 gotowych", "brak sesji"])
        self.assertEqual(result["worktrees"], [
            "Dodatkowe worktrees git: 15 do sprzątnięcia (16 GB), 5 w pracy, razem 48"
            "\nKliknij, aby otworzyć okno Worktrees",
            "Dodatkowe worktrees git: nic do sprzątnięcia, 2 w pracy, razem 3\nKliknij, aby otworzyć okno Worktrees",
            "Usługa tide-daemon nie działa"])
        self.assertEqual(result["limits"], ["Pozostały limit AI: GPT 91%, Claude 86%",
                                            "Pozostały limit AI: GPT —, Claude 12%",
                                            "Usługa Limity AI nie działa"])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class AgentsPopupTest(unittest.TestCase):
    """Popup kafelka agentów: te same zwarte wiersze co w pasku bocznym, z drugą linią i bez limitu."""

    def rows(self, sessions=None, **kwargs):
        return preview.render_popup(sessions, **kwargs)["rows"]

    def test_rows_in_every_state(self):
        rows = self.rows()
        # Czekający na górze, potem pracujący, skończeni i bezczynni; zakończona sesja ukryta.
        self.assertEqual([(r["id"], r["state"], r["word"], r["ago"]) for r in rows], [
            ("codex:f", "waiting", "czeka", "1 min"),
            ("claude:a", "waiting", "czeka", "2 min"),
            ("claude:b", "working", "pracuje", "teraz"),
            ("codex:c", "done", "skończył", "5 min"),
            ("claude:d", "done", "skończył", "12 min"),
            ("codex:e", "idle", "bezczynna", "1 h"),
        ])
        self.assertEqual([r["detail"] for r in rows], [
            "pyta o decyzję · +1 podagent · request_user_input",
            "czeka na zgodę · Bash · make test",
            "Claude Code · feat/login",
            "Codex · +2 podagentów · zaplanowane · main · Kontynuacja",
            "Claude Code · fix/report · Raport miesięczny",
            "Codex · main",
        ])
        # Przycisk tylko u czekających; Codex Desktop otwiera wątek w aplikacji.
        self.assertEqual([(r["button"], r["buttonText"]) for r in rows if r["button"]],
                         [(True, "Otwórz w Codex"), (True, "Przejdź")])
        self.assertEqual([r["activateText"] for r in rows[:2]], ["Otwórz w Codex", "Przejdź do terminala"])
        self.assertEqual([r["clickable"] for r in rows], [True, True, True, True, True, False])

    def test_ended_sessions_on_request(self):
        report = preview.render_popup(show_ended=True)
        self.assertEqual(report["ended"], 1)
        self.assertEqual(report["rows"][-1]["state"], "ended")

    def test_no_row_limit_in_the_popup(self):
        many = [dict(preview.SESSIONS[1], id=f"claude:{n}", project=f"p{n}") for n in range(12)]
        self.assertEqual(len(self.rows(many)), 12)

    def test_codex_desktop_and_subagent_texts(self):
        base = dict(preview.SESSIONS[2], reason="", title="", branch="")
        rows = self.rows([
            dict(base, id="codex:1", subagents=1, sinceTs=preview.NOW_TS - 10),
            dict(base, id="codex:2", subagents=0, subagent=True, agentNickname="Pascal", sinceTs=preview.NOW_TS - 20),
            dict(base, id="codex:3", subagents=0, subagent=True, agentNickname="", sinceTs=preview.NOW_TS - 30),
            # Starsza usługa (bez pola activation) i okno aplikacji — samo podniesienie okna.
            dict(base, id="codex:4", activation=None, subagents=0, sinceTs=preview.NOW_TS - 40),
            # Codex z wiersza poleceń w Konsoli — przejście do karty terminala, jak dotąd.
            dict(base, id="codex:5", activation="window", terminal="Konsole", subagents=0,
                 sinceTs=preview.NOW_TS - 50),
        ])
        self.assertEqual([r["detail"] for r in rows], [
            "Codex · +1 podagent", "Codex · podagent Pascal", "Codex · podagent", "Codex", "Codex"])
        self.assertEqual([r["activateText"] for r in rows], [
            "Otwórz w Codex", "Otwórz w Codex", "Otwórz w Codex", "Przejdź do okna Codex", "Przejdź do terminala"])

    def test_older_daemon_reply_is_shown_as_done(self):
        # Starsza usługa zgłaszała koniec tury jako waiting/reply — wiersz pokazuje go jako „skończył”.
        old = dict(preview.SESSIONS[3], state="waiting", reason="reply")
        row = self.rows([old])[0]
        self.assertEqual((row["state"], row["word"], row["button"]), ("done", "skończył", False))

    def test_screenshot(self):
        with tempfile.TemporaryDirectory(prefix="tide-popup-png-") as tmp:
            png = os.path.join(tmp, "popup.png")
            preview.render_popup(png=png)
            width, height = png_size(png)
        self.assertEqual((width, height), (preview.POPUP_WIDTH + 2 * preview.MARGIN,
                                           preview.POPUP_HEIGHT + 2 * preview.MARGIN))


class SourceTest(unittest.TestCase):
    def test_tiles_use_shared_tile_and_texts(self):
        for widget, name in (("agents", "CompactAgents"), ("worktrees", "CompactWorktrees"),
                             ("system", "CompactSystem"), ("limits", "CompactLimits")):
            with self.subTest(widget=widget):
                with open(os.path.join(WIDGETS_DIR, widget, "contents/ui", name + ".qml"), encoding="utf-8") as fh:
                    self.assertIn("Tide.DockTile", fh.read())
                with open(os.path.join(WIDGETS_DIR, widget, "contents/ui/main.qml"), encoding="utf-8") as fh:
                    main = fh.read()
                self.assertIn(name + " {", main)
                self.assertIn("toolTipSubText", main)


if __name__ == "__main__":
    unittest.main()
