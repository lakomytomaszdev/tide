"""tide-icons audit, sync i build na udawanym systemie (katalogi XDG w katalogu tymczasowym)."""
import colorsys
import json
import re
import unittest

from PIL import Image

from theme.icons.tests.fake_system import RIDER_ID, FakeSystem

PALETTE_TEAL = "#6fd6c6"


def hue(hex_color: str) -> float:
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return colorsys.rgb_to_hls(*rgb)[0] * 360


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSystem()

    def tearDown(self):
        self.fake.cleanup()

    def audit(self, *args):
        result = self.fake.run("audit", "--json", *args, check=False)
        return result.returncode, json.loads(result.stdout)

    def categories(self, report):
        return {app["desktop_id"]: app["category"] for app in report["apps"]}

    def test_reports_each_category(self):
        code, report = self.audit("--theme", "Base")
        self.assertEqual(code, 1)
        self.assertEqual(self.categories(report), {
            "firefox.desktop": "themed",
            "mine.desktop": "themed",
            f"{RIDER_ID}.desktop": "hicolor",
            "game.desktop": "hicolor",
            "falkon.desktop": "fallback",
            "editor.desktop": "wrong",
            "chatgpt.desktop": "missing",
            "code_code.desktop": "path",
            "dev.zed.Zed.desktop": "path",
        })

    def test_hidden_apps_are_not_audited(self):
        _, report = self.audit("--theme", "Base")
        ids = {app["desktop_id"] for app in report["apps"]}
        self.assertFalse(ids & {"hidden.desktop", "gnome.desktop", "gone.desktop"})

    def test_human_report_is_polish_and_lists_uncovered_apps(self):
        result = self.fake.run("audit", "--theme", "Base", check=False)
        self.assertIn("ścieżka do pliku zamiast nazwy: 2", result.stdout)
        self.assertIn("Bez spójnej ikony: 7", result.stdout)

    def test_themes_ranks_base_theme_candidates(self):
        lines = self.fake.run("themes").stdout.splitlines()
        ranking = [line.split() for line in lines[1:]]
        self.assertEqual(ranking, [["2", "Base"], ["1", "Other"]])

    def test_overlay_from_build_dir_covers_names_but_not_paths(self):
        out = self.fake.home / "build/Tide"
        self.fake.run("build", "--out", str(out))
        code, report = self.audit("--theme", "Tide", "--icons-dir", str(out.parent))
        self.assertEqual(code, 1)
        uncovered = {a["desktop_id"] for a in report["apps"] if not a["covered"]}
        self.assertEqual(uncovered, {"code_code.desktop", "dev.zed.Zed.desktop"})


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSystem()
        self.overlay = self.fake.data / "icons/Tide"
        self.apps = self.fake.data / "applications"

    def tearDown(self):
        self.fake.cleanup()

    def test_after_sync_every_app_is_covered(self):
        self.fake.run("sync")
        result = self.fake.run("audit", "--theme", "Tide", check=False)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_overlay_inherits_base_and_keeps_its_sizes(self):
        self.fake.run("sync")
        index = (self.overlay / "index.theme").read_text()
        self.assertIn("Inherits=Base,breeze-dark,hicolor", index)
        self.assertIn("PanelDefault=48", index)
        self.assertNotIn("KDE-Extensions", index)  # inaczej Plasma nie widzi kafelków PNG

    def test_aliases_copy_theme_icons_under_foreign_names(self):
        self.fake.run("sync")
        base = self.fake.data / "icons/Base/apps/scalable"
        self.assertEqual((self.overlay / f"apps/scalable/{RIDER_ID}.svg").read_text(),
                         (base / "rider.svg").read_text())
        self.assertEqual((self.overlay / "apps/scalable/code.svg").read_text(),
                         (base / "visual-studio-code.svg").read_text())

    def test_own_icons_become_round_tiles(self):
        self.fake.run("sync")
        for name in ("steam_icon_42", "falkon", "dev.zed.Zed"):
            tile = Image.open(self.overlay / f"apps/256/{name}.png")
            self.assertEqual(tile.size, (256, 256))
            self.assertEqual(tile.getchannel("A").getpixel((2, 2)), 0, name)  # narożnik poza kołem
            self.assertEqual(tile.getchannel("A").getpixel((128, 128)), 255, name)

    def test_missing_icon_gets_a_palette_glyph(self):
        self.fake.run("sync")
        svg = (self.overlay / "apps/scalable/chatgpt.svg").read_text()
        self.assertIn("<circle", svg)
        self.assertNotIn("<image", svg)

    def test_folders_are_recolored_to_palette_teal(self):
        self.fake.run("sync")
        for name in ("folder.svg", "folder-documents.svg"):
            colors = re.findall(r"#[0-9a-f]{6}", (self.overlay / "places/scalable" / name).read_text())
            tinted = [c for c in colors if c != "#ffffff"]
            self.assertTrue(tinted)
            for color in tinted:
                self.assertAlmostEqual(hue(color), hue(PALETTE_TEAL), delta=1, msg=f"{name} {color}")
        self.assertIn("#ffffff", (self.overlay / "places/scalable/folder-documents.svg").read_text())
        self.assertFalse((self.overlay / "places/scalable/folder-blue.svg").exists())

    def test_path_icons_get_marked_local_desktop_copies(self):
        self.fake.run("sync")
        copy = (self.apps / "code_code.desktop").read_text()
        original = (self.fake.system / "applications/code_code.desktop").read_text()
        self.assertIn("X-Tide-Icons=override", copy)
        self.assertEqual(copy.count("Icon=visual-studio-code"), 2)  # aplikacja i akcja
        kept = [l for l in original.splitlines() if not l.startswith("Icon=")]
        self.assertTrue(all(l in copy.splitlines() for l in kept))
        self.assertFalse((self.apps / "firefox.desktop").exists())

    def test_known_startup_wm_class_is_added_when_missing(self):
        self.fake.run("sync")
        self.assertIn("StartupWMClass=dev.zed.Zed", (self.apps / "dev.zed.Zed.desktop").read_text())
        self.assertEqual((self.apps / "code_code.desktop").read_text().count("StartupWMClass="), 1)

    def test_sync_is_idempotent(self):
        self.fake.run("sync")
        first = {p: p.read_bytes() for p in sorted(self.fake.home.rglob("*")) if p.is_file()}
        self.fake.run("sync")
        second = {p: p.read_bytes() for p in sorted(self.fake.home.rglob("*")) if p.is_file()}
        self.assertEqual(first.keys(), second.keys())
        self.assertEqual(first, second)

    def test_user_desktop_file_is_kept_aside_and_restored(self):
        original = (self.apps / "dev.zed.Zed.desktop").read_text()
        self.fake.run("sync")
        self.assertIn("Icon=dev.zed.Zed", (self.apps / "dev.zed.Zed.desktop").read_text())
        self.fake.script("uninstall")
        self.assertEqual((self.apps / "dev.zed.Zed.desktop").read_text(), original)

    def test_override_disappears_when_app_ships_a_theme_name(self):
        self.fake.run("sync")
        source = self.fake.system / "applications/code_code.desktop"
        source.write_text(source.read_text().replace(str(self.fake.opt / "vscode.png"), "visual-studio-code"))
        self.fake.run("sync")
        self.assertFalse((self.apps / "code_code.desktop").exists())

    def test_sync_refreshes_menus(self):
        self.fake.run("sync")
        self.assertIn("kbuildsycoca6 ", self.fake.calls())


if __name__ == "__main__":
    unittest.main()
