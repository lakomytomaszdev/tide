"""Ustawienie otwierania karty i trasa powiadomień (PT-133, K-35).

- konfiguracja okna Tide (apps/common/windowconfig.py): domyślnie „Okno decyzji”, zapis i odczyt, przeżywa
  restart (nowy Backend czyta plik), uszkodzony plik i nieznana wartość nie psują odczytu,
- jedno ustawienie w dwóch miejscach: menu „Widok” i stopka okna decyzji zmieniają tę samą wartość backendu,
- klik w kartę z „Czeka na mnie” otwiera okno decyzji albo pełną kartę (PT-128) zgodnie z ustawieniem;
  karta spoza „Czeka na mnie” z dziennikiem zawsze otwiera pełną kartę, a karta bez dziennika — panel karty,
- powiadomienie (showItem z ID karty, jak ShowWindow/Show z D-Bus) idzie tą samą trasą co klik,
- „Otwórz całą kartę” w oknie decyzji zawsze otwiera pełną kartę.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

from . import APP_DIR, REPO
from .decisionfix import full_card, full_d, journal, scenario_f
from .test_decision_dialog import STATE_PY as DIALOG_STATE_PY
from .test_view import ProbeCase

# Pola przełącznika to elementy Repeatera (findChild ich nie widzi) — szukamy widocznych w całym drzewie okna,
# razem z nakładką, w której żyje menu „Widok”.
STATE_PY = DIALOG_STATE_PY + r'''
ROOT = "(function () { let r = page; while (r.parent) r = r.parent; return r })()"

def pill(name):
    return at("(" + ALL + ")(" + ROOT + ", '" + name + "')[0]")
'''


def load_windowconfig():
    spec = importlib.util.spec_from_file_location("tide_windowconfig_test", os.path.join(REPO, "apps", "common",
                                                                                         "windowconfig.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowConfigTest(unittest.TestCase):
    def setUp(self):
        self.wc = load_windowconfig()
        self.tmp = tempfile.mkdtemp(prefix="tide-window-config-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "tide", "window.json")

    def test_default_is_decision_dialog_without_a_file(self):
        self.assertEqual(self.wc.card_open(self.path), "decision")
        self.assertFalse(os.path.exists(self.path), "odczyt niczego nie tworzy")

    def test_write_then_read_and_overwrite(self):
        self.wc.set_card_open("full", self.path)
        self.assertEqual(self.wc.card_open(self.path), "full")
        self.wc.set_card_open("decision", self.path)
        self.assertEqual(self.wc.card_open(self.path), "decision")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["window.json"], "bez plików tymczasowych")

    def test_other_keys_survive_a_write(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"other": [1, 2]}, fh)
        self.wc.set_card_open("full", self.path)
        self.assertEqual(self.wc.load(self.path), {"other": [1, 2], "cardOpen": "full"})

    def test_bad_input_never_breaks_reading(self):
        os.makedirs(os.path.dirname(self.path))
        for text in ("{nie json", "[1]", json.dumps({"cardOpen": "pop-up"}), json.dumps({"cardOpen": 3})):
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.assertEqual(self.wc.card_open(self.path), "decision", text)
        with self.assertRaises(ValueError):
            self.wc.set_card_open("pop-up", self.path)

    def test_diff_mode_defaults_to_unified_and_is_saved_next_to_other_settings(self):
        """PT-350: tryb widoku diffa żyje w tym samym pliku co `cardOpen` i nie rusza go."""
        self.assertEqual(self.wc.diff_mode(self.path), "unified")
        self.assertFalse(os.path.exists(self.path), "odczyt niczego nie tworzy")
        self.wc.set_card_open("full", self.path)
        self.wc.set_diff_mode("split", self.path)
        self.assertEqual(self.wc.diff_mode(self.path), "split")
        self.assertEqual(self.wc.load(self.path), {"cardOpen": "full", "diffMode": "split"})
        self.wc.set_diff_mode("unified", self.path)
        self.assertEqual(self.wc.diff_mode(self.path), "unified")
        self.assertEqual(self.wc.card_open(self.path), "full")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["window.json"], "bez plików tymczasowych")

    def test_bad_diff_mode_never_breaks_reading(self):
        os.makedirs(os.path.dirname(self.path))
        for text in ("{nie json", "[1]", json.dumps({"diffMode": "side"}), json.dumps({"diffMode": 3})):
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.assertEqual(self.wc.diff_mode(self.path), "unified", text)
        with self.assertRaises(ValueError):
            self.wc.set_diff_mode("side", self.path)

    def test_path_follows_xdg_config_home(self):
        self.assertEqual(self.wc.config_path({"XDG_CONFIG_HOME": "/x"}), "/x/tide/window.json")
        self.assertEqual(self.wc.config_path({"HOME": "/h"}), "/h/.config/tide/window.json")


class BackendSettingTest(unittest.TestCase):
    """Backend widoku Tablica: ustawienie żyje w pliku okna, nie w kopii widoku."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-card-open-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        self.addCleanup(lambda: os.environ.pop("XDG_CONFIG_HOME") if old is None
                        else os.environ.__setitem__("XDG_CONFIG_HOME", old))
        spec = importlib.util.spec_from_file_location("tide_board_backend_card_open", os.path.join(APP_DIR, "backend.py"))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.file = os.path.join(self.tmp, "tide", "window.json")

    def test_default_save_and_restart(self):
        backend = self.module.Backend(None)
        self.assertEqual(backend.cardOpen, "decision")
        seen = []
        backend.cardOpenChanged.connect(lambda: seen.append(backend.cardOpen))
        self.assertEqual(backend.setCardOpen("full"), "")
        self.assertEqual((backend.cardOpen, seen), ("full", ["full"]))
        self.assertEqual(backend.setCardOpen("full"), "")
        self.assertEqual(seen, ["full"], "ta sama wartość nie powiadamia ponownie")
        with open(self.file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"cardOpen": "full"})
        self.assertEqual(self.module.Backend(None).cardOpen, "full", "nowy start okna czyta zapisane ustawienie")
        self.assertEqual(backend.setCardOpen("decision"), "")
        self.assertEqual(self.module.Backend(None).cardOpen, "decision")

    def test_unknown_value_is_refused(self):
        backend = self.module.Backend(None)
        self.assertNotEqual(backend.setCardOpen("pop-up"), "")
        self.assertEqual(backend.cardOpen, "decision")
        self.assertFalse(os.path.exists(self.file))

    def test_unwritable_config_keeps_the_choice_for_the_session_and_says_so(self):
        with open(os.path.join(self.tmp, "tide"), "w", encoding="utf-8") as fh:   # plik zamiast katalogu
            fh.write("x")
        backend = self.module.Backend(None)
        self.assertIn("Nie zapisano", backend.setCardOpen("full"))
        self.assertEqual(backend.cardOpen, "full")

    def test_preview_never_touches_the_config_file(self):
        backend = self.module.Backend(None, {"cards": []})
        self.assertEqual(backend.cardOpen, "decision")
        self.assertEqual(backend.setCardOpen("full"), "")
        self.assertEqual(backend.cardOpen, "full")
        self.assertFalse(os.path.exists(self.file))
        self.assertEqual(self.module.Backend(None, {"cards": [], "cardOpen": "full"}).cardOpen, "full")


    def test_diff_mode_is_saved_for_the_window_and_survives_a_restart(self):
        backend = self.module.Backend(None)
        self.assertEqual(backend.diffMode, "unified")
        seen = []
        backend.diffModeChanged.connect(lambda: seen.append(backend.diffMode))
        self.assertEqual(backend.setDiffMode("split"), "")
        self.assertEqual((backend.diffMode, seen), ("split", ["split"]))
        self.assertEqual(backend.setDiffMode("split"), "")
        self.assertEqual(seen, ["split"], "ta sama wartość nie powiadamia ponownie")
        with open(self.file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"diffMode": "split"})
        self.assertEqual(self.module.Backend(None).diffMode, "split", "nowy start okna czyta zapisany tryb")
        self.assertNotEqual(backend.setDiffMode("side"), "")
        self.assertEqual(backend.diffMode, "split")

    def test_diff_mode_unwritable_config_keeps_the_choice_for_the_session(self):
        with open(os.path.join(self.tmp, "tide"), "w", encoding="utf-8") as fh:   # plik zamiast katalogu
            fh.write("x")
        backend = self.module.Backend(None)
        self.assertIn("Nie zapisano", backend.setDiffMode("split"))
        self.assertEqual(backend.diffMode, "split")

    def test_diff_mode_preview_never_touches_the_config_file(self):
        backend = self.module.Backend(None, {"cards": []})
        self.assertEqual(backend.setDiffMode("split"), "")
        self.assertEqual(backend.diffMode, "split")
        self.assertFalse(os.path.exists(self.file))


class CardOpenViewTest(ProbeCase):
    def test_click_follows_the_setting_for_waiting_cards(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["decision"] = [js("page.decisionOpen"), js("page.panelOpen")]
key(Qt.Key.Key_Escape)
click(named("viewMenuButton"))
click(pill("cardOpen-full"))
out["setting"] = [backend.cardOpen, js("page.cardOpen")]
key(Qt.Key.Key_Escape)
click(card("K-8"))
out["full"] = [js("page.decisionOpen"), js("page.fullOpen"), js("page.fullCardId"), js("page.panelOpen")]
""", full_card(self.data, journal()))
        self.assertEqual(out["decision"], [True, False], "domyślnie okno decyzji")
        self.assertEqual(out["setting"], ["full", "full"], "menu „Widok” zmienia ustawienie backendu")
        self.assertEqual(out["full"], [False, True, "K-8", False], "po zmianie klik otwiera pełną kartę")

    def test_card_outside_waiting_opens_full_card_and_card_without_journal_the_panel(self):
        out = self.probe(STATE_PY + """
col = js("page.cards.find(c => c.id === 'K-9').boardColumn")
click(card("K-9"))
out["noJournal"] = [col, js("page.decisionOpen"), js("page.fullOpen"), js("page.panelOpen"), js("page.panelCardId")]
key(Qt.Key.Key_Escape)
col = js("page.cards.find(c => c.id === 'K-8').boardColumn")
click(card("K-8"))
out["progress"] = [col, js("page.decisionOpen"), js("page.fullOpen"), js("page.panelOpen")]
""", full_d(self.data))
        self.assertNotEqual(out["noJournal"][0], "waiting")
        self.assertEqual(out["noJournal"][1:], [False, False, True, "K-9"], "bez dziennika — panel karty")
        self.assertEqual(out["progress"], ["progress", False, True, False], "karta z „W toku” — pełna karta")

    def test_footer_field_and_view_menu_change_the_same_setting(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["footerOn"] = js("page.decisionDialog.cardOpen")
click(pill("cardOpen-full"))
out["afterFooter"] = [backend.cardOpen, js("page.decisionDialog.cardOpen"), js("page.decisionOpen")]
key(Qt.Key.Key_Escape)
click(named("viewMenuButton"))
out["menuShows"] = js("page.cardOpen")
click(pill("cardOpen-decision"))
out["afterMenu"] = [backend.cardOpen, js("page.cardOpen")]
""", scenario_f(self.data))
        self.assertEqual(out["footerOn"], "decision")
        self.assertEqual(out["afterFooter"], ["full", "full", True], "stopka zmienia backend, okno zostaje otwarte")
        self.assertEqual(out["menuShows"], "full", "menu widzi wybór ze stopki")

    def test_notification_route_follows_the_setting(self):
        out = self.probe(STATE_PY + """
# Powiadomienie to Show("board", "K-8") okna Tide — BoardView.showItem z ID karty.
js("page.parent.showItem('K-8')")
wait(150)
out["decision"] = [js("page.decisionOpen"), js("page.panelOpen"), js("page.decisionCardId"), js("page.selectedId")]
key(Qt.Key.Key_Escape)
backend.setCardOpen("full")
js("page.parent.showItem('k-8')")
wait(150)
out["full"] = [js("page.decisionOpen"), js("page.fullOpen"), js("page.fullCardId")]
js("page.closeFull()")
js("page.parent.showItem('K-9')")
wait(150)
out["other"] = [js("page.decisionOpen"), js("page.fullOpen"), js("page.panelOpen"), js("page.selectedId")]
""", full_card(self.data, journal()))
        self.assertEqual(out["decision"], [True, False, "K-8", "K-8"])
        self.assertEqual(out["full"], [False, True, "K-8"], "powiadomienie respektuje „Pełna karta”")
        self.assertEqual(out["other"], [False, False, False, "K-9"], "karta bez decyzji tylko zaznaczona, jak dotąd")

    def test_open_full_card_is_always_in_the_decision_window(self):
        for setting in ("decision", "full"):
            data = full_card(self.data, journal())
            data["cardOpen"] = setting
            out = self.probe(STATE_PY + """
js("page.openDecisions('K-8')")
click(named("decisionFullCard"))
out["state"] = [js("page.decisionOpen"), js("page.fullOpen"), js("page.fullCardId"), js("page.panelOpen")]
""", data)
            self.assertEqual(out["state"], [False, True, "K-8", False], setting)


if __name__ == "__main__":
    unittest.main()
