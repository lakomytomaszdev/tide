"""Ikony zasobnika górnego paska: kafelki, nazwy i ich miejsce w nakładce."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from theme.icons import tray
from theme.icons.audit import MANIFEST
from theme.icons.tests.fake_system import FakeSystem

ROOT = Path(__file__).resolve().parents[3]
PALETTE = json.loads((ROOT / "theme/palette/mist.json").read_text())
DOCK_TILE = json.loads((ROOT / "theme/icons/glyphs.json").read_text())["tile"]
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
CLASS = re.compile(r'class="ColorScheme-(\w+)"')
LOGO = '<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>X</title><path d="M0 0h24v24H0z"/></svg>'

# Nazwy, o które dziś proszą aplety i elementy górnego paska (sprawdzone w Plasmie 6.8).
SEEN_ON_THE_BAR = {
    "notifications-disabled-symbolic", "notification-active-symbolic", "notification-inactive-symbolic",
    "klipper-symbolic", "network-bluetooth-symbolic", "device-notifier-symbolic", "audio-volume-high",
    "brightness-high-symbolic", "kdeconnect-tray-symbolic", "microphone-sensitivity-high",
    "network-wired-activated-symbolic", "network-wired-activated-locked-symbolic",
    "system-suspend-inhibited-symbolic", "media-playback-paused-symbolic", "update-low",
}


class FakeSimple:
    """Simple Icons bez sieci: każde logo to kwadrat."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, slug):
        file = self.root / f"{slug}.svg"
        file.write_text(LOGO)
        return file


class Missing:
    def path(self, slug):
        raise tray.TrayError(f"brak {slug}")


class TrayTilesTest(unittest.TestCase):
    def setUp(self):
        self.data = tray.load()
        self.tmp = tempfile.TemporaryDirectory()
        self.simple = FakeSimple(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def icons(self, **kw):
        return tray.icons(PALETTE, simple=self.simple, palette_tile=DOCK_TILE, **kw)

    def test_every_glyph_file_is_used_and_exists(self):
        used = {e["glyph"] for e in self.data["icons"].values() if "glyph" in e}
        self.assertEqual({p.stem for p in tray.GLYPHS.glob("*.svg")}, used)

    def test_names_are_unique_and_cover_the_bar(self):
        names = tray.all_names(self.data["icons"])
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(SEEN_ON_THE_BAR, set(names))

    def test_glyph_sources_carry_no_colors(self):
        for path in tray.GLYPHS.glob("*.svg"):
            text = path.read_text()
            with self.subTest(path.name):
                self.assertIsNone(HEX.search(text), "kolor wpisany na sztywno")
                self.assertTrue(set(CLASS.findall(text)) <= set(tray.SCHEME_CLASSES))
                self.assertTrue(CLASS.search(text))

    def test_tile_uses_dock_recipe_and_palette_pastel(self):
        svg = self.icons()["kdeconnect-tray-symbolic.svg"]
        teal = PALETTE["pastel"]["teal"]
        self.assertIn(f'fill="{tray.tile_color(teal, DOCK_TILE["tint"], DOCK_TILE)}"', svg)
        self.assertIn(f".ColorScheme-Text {{ color:{teal}; }}", svg)

    def test_plasma_does_not_recolor_tiles(self):
        # KIconLoader przebarwia tylko arkusz current-color-scheme; kafelek ma zostać w pastelu.
        for name, svg in self.icons().items():
            with self.subTest(name):
                self.assertNotIn(tray.SCHEME_STYLE_ID, svg)
                self.assertIn(f'id="{tray.STYLE_ID}"', svg)

    def test_live_tile_is_brighter_and_outlined(self):
        icons = self.icons()
        peach = PALETTE["pastel"]["peach"]
        live = icons["microphone-sensitivity-high.svg"]
        self.assertIn(f'fill="{tray.tile_color(peach, self.data["tile"]["liveTint"], DOCK_TILE)}"', live)
        self.assertIn(f'stroke="{peach}"', live)
        self.assertNotIn('stroke-width="1.2"', icons["audio-volume-high.svg"])

    def test_badge_is_cut_out_of_the_tile(self):
        svg = self.icons()["update-high.svg"]
        self.assertIn('mask="url(#badge-gap)"', svg)
        self.assertIn(f'fill="{PALETTE["pastel"]["rose"]}"', svg)

    def test_app_logo_is_painted_in_its_dock_color(self):
        svg = self.icons()["tide-tray-discord.svg"]
        self.assertIn(f'fill="{PALETTE["pastel"]["lavender"]}"><path', svg)
        self.assertNotIn("<title>", svg)

    def test_missing_logo_skips_only_that_tile(self):
        warnings = []
        icons = tray.icons(PALETTE, simple=Missing(), palette_tile=DOCK_TILE, warn=warnings.append)
        self.assertNotIn("tide-tray-discord.svg", icons)
        self.assertIn("klipper-symbolic.svg", icons)
        self.assertTrue(warnings)

    def test_one_name_cannot_point_to_two_tiles(self):
        data = {"tile": self.data["tile"], "icons": {
            "a": {"glyph": "bell", "color": "teal", "names": ["x"]},
            "b": {"glyph": "phone", "color": "teal", "names": ["x"]}}}
        with self.assertRaises(tray.TrayError):
            tray.icons(PALETTE, data)

    def test_every_mirror_icon_has_a_tile(self):
        names = set(tray.all_names(self.data["icons"]))
        for rule in self.data["mirror"]:
            with self.subTest(rule["app"]):
                self.assertIn(rule["icon"], names)
                for state in rule.get("states", []):
                    self.assertIn(state["icon"], names)


class TrayOverlayTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSystem()
        self.overlay = self.fake.data / "icons/Tide"

    def tearDown(self):
        self.fake.cleanup()

    def test_overlay_carries_tray_tiles_under_their_exact_names(self):
        self.fake.run("sync")
        folder = self.overlay / tray.DIRECTORY
        built = {p.name for p in folder.iterdir()}
        system = {f"{n}.svg" for e in tray.load_config().values() if "glyph" in e for n in e["names"]}
        self.assertLessEqual(system, built)
        index = (self.overlay / "index.theme").read_text()
        self.assertIn(f"[{tray.DIRECTORY}]", index)
        self.assertRegex(index, rf"Directories=.*{re.escape(tray.DIRECTORY)}")
        manifest = json.loads((self.overlay / MANIFEST).read_text())
        self.assertEqual(manifest["tray"], len(built))
        self.assertFalse(any(n.endswith("-symbolic") for n in manifest["icons"]))


if __name__ == "__main__":
    unittest.main()
