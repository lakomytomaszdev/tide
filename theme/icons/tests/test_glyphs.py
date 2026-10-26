"""Stonowane glify aplikacji z docka: rysowanie kafelka, źródła znaków i nakładka (bez sieci)."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from theme.icons import glyphs
from theme.icons.tests.fake_system import RIDER_ID, SIMPLE_ICONS, FakeSystem

ROOT = Path(__file__).resolve().parents[3]
PALETTE = json.loads((ROOT / "theme/palette/mist.json").read_text())
SQUARE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0h10v10H0z" fill="#ff0000"/></svg>\n'

FIREFOX = {"app": "Firefox", "names": ["firefox"], "glyph": {"simpleIcons": "openai"}, "color": "peach"}
RIDER = {"app": "Rider", "names": ["rider"], "glyph": [{"simpleIcons": "rider"}], "color": "lavender"}
KONSOLE = {"app": "Konsole", "names": ["utilities-terminal"],
           "glyph": [{"theme": "nie-ma-takiej-symbolic"}, {"theme": "utilities-terminal-symbolic"}],
           "color": "teal"}
CHATGPT = {"app": "ChatGPT", "names": ["chatgpt"], "glyph": {"simpleIcons": "openai"}, "color": "text.secondary"}


def rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


class TileTest(unittest.TestCase):
    """Kafelek rysowany z lokalnego SVG, bez motywów i bez pobierania."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svg = Path(self.tmp.name) / "square.svg"
        self.svg.write_text(SQUARE)

    def tearDown(self):
        self.tmp.cleanup()

    def glyph(self, color="teal", **tile):
        config = {"tile": tile, "apps": [{"app": "Test", "names": ["test"], "glyph": {"file": "x"}, "color": color}]}
        glyph = glyphs.entries(config, PALETTE)[0]
        glyph.path = self.svg
        return glyph

    def test_colors_come_from_the_palette(self):
        teal = PALETTE["pastel"]["teal"]
        image = glyphs.render(self.glyph(), 256).convert("RGBA")
        self.assertEqual(image.getpixel((128, 128))[:3], rgb(teal))  # znak w pastelu
        background = image.getpixel((128, 12))  # między krawędzią a znakiem
        self.assertEqual(background[:3], tuple(int(c * 0.16 + 24) for c in rgb(teal)))
        self.assertEqual(background[3], 255)

    def test_text_tokens_are_allowed_and_raw_hex_is_not(self):
        self.assertEqual(glyphs.palette_color(PALETTE, "text.secondary"), PALETTE["text"]["secondary"])
        self.assertEqual(glyphs.palette_color(PALETTE, "semantic.positive"), PALETTE["pastel"]["mint"])
        with self.assertRaises(ValueError):
            glyphs.palette_color(PALETTE, "#ff0000")

    def test_rounded_corners_are_transparent(self):
        image = glyphs.render(self.glyph(), 256)
        self.assertEqual(image.getchannel("A").getpixel((2, 2)), 0)
        self.assertEqual(image.getchannel("A").getpixel((128, 2)), 255)

    def test_sizes(self):
        for size in (256, 64, 22):
            self.assertEqual(glyphs.render(self.glyph(), size).size, (size, size))
        self.assertEqual(Image.open(io.BytesIO(glyphs.png(self.glyph(), 256))).size, (256, 256))

    def test_glyph_takes_its_share_of_the_tile_and_is_centered(self):
        for share, scale in ((0.46, {}), (0.4, {"glyph": 0.4})):
            image = glyphs.render(self.glyph(**scale), 256)
            ink = rgb(PALETTE["pastel"]["teal"])
            box = Image.new("L", image.size, 0)
            box.putdata([255 if px == ink else 0 for px in image.convert("RGB").getdata()])
            left, top, right, bottom = box.getbbox()
            self.assertAlmostEqual((right - left) / 256, share, delta=0.02)
            self.assertAlmostEqual((left + right) / 2, 128, delta=1.5)
            self.assertAlmostEqual((top + bottom) / 2, 128, delta=1.5)


class RepoConfigTest(unittest.TestCase):
    """glyphs.json z repozytorium: kompletne i spójne z paletą, bez pobierania."""

    def setUp(self):
        self.config = glyphs.load_config(glyphs.CONFIG)

    def test_every_app_has_a_palette_color_and_known_sources(self):
        pins = self.config["simpleIcons"]["sha256"]
        for app in self.config["apps"]:
            glyphs.palette_color(PALETTE, app["color"])
            sources = app["glyph"] if isinstance(app["glyph"], list) else [app["glyph"]]
            for source in sources:
                if "simpleIcons" in source:
                    self.assertRegex(pins.get(source["simpleIcons"], ""), "^[0-9a-f]{64}$", app["app"])
                if "file" in source:
                    self.assertTrue((glyphs.LOCAL_DIR / source["file"]).is_file(), source["file"])

    def test_names_are_unique_and_version_is_pinned(self):
        names = [n for app in self.config["apps"] for n in app["names"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertRegex(self.config["simpleIcons"]["version"], r"^\d+\.\d+\.\d+$")
        self.assertIn("{version}", self.config["simpleIcons"]["url"])

    def test_dock_apps_are_covered(self):
        names = {n for app in self.config["apps"] for n in app["names"]}
        dock = {"utilities-terminal", "org.kde.dolphin", "systemsettings", "preferences-system", "spectacle",
                "firefox", "chatgpt", "rider", "pycharm", "webstorm", "com.discordapp.DiscordPTB", "steam",
                "dev.zed.Zed", "docker-desktop", "t3code"}
        self.assertEqual(dock - names, set())

    def test_installed_apps_are_covered(self):
        """Nazwy ikon zainstalowanych programów (audyt z 26.10.2026), które mają dostać glif.

        Pominięte świadomie: gry ze Steama (okładki, nazwy steam_icon_<id>), narzędzia
        deweloperskie Plasmy i diagnostyka (ogólne nazwy ikon współdzielone z resztą pulpitu,
        np. tools-report-bug w menu Pomoc każdej aplikacji KDE)."""
        names = {n for app in self.config["apps"] for n in app["names"]}
        installed = {
            # Tide i komunikatory, przeglądarki
            "tide-worktrees", "chromium", "brave-browser", "falkon", "org.telegram.desktop",
            "com.discordapp.Discord",
            # programowanie (nazwy z Toolboksa trafiają tu przez wzorce z fixes.json)
            "android-studio", "jetbrains-toolbox", "dotmemory", "dbeaver-ce", "redisinsight", "code",
            "kate", "kwrite", "org.filezillaproject.Filezilla", "scrcpy", "krdc", "FreeRDP",
            # biuro, grafika, multimedia, gry
            "libreoffice-startcenter", "libreoffice-writer", "libreoffice-calc", "libreoffice-math", "okular",
            "org.kde.gwenview", "gimp", "blender", "org.gnome.Gnote", "kasumi", "vlc", "mpv",
            "com.obsproject.Studio", "org.ryujinx.Ryujinx",
            # narzędzia KDE i systemu
            "plasmadiscover", "kdeconnect", "ark", "kfind", "accessories-calculator",
            "preferences-desktop-emoticons", "multimedia-volume-control", "org.kde.keepsecret",
            "kwalletmanager", "kvantum", "nvidia-settings", "kmenuedit", "hwinfo", "help-browser",
        }
        self.assertEqual(installed - names, set())

    def test_skipped_generic_names_stay_with_the_theme(self):
        """Ogólnych nazw używanych poza aplikacją nie zasłaniamy glifem."""
        names = {n for app in self.config["apps"] for n in app["names"]}
        self.assertFalse(names & {"tools-report-bug", "system-search", "preferences-desktop-theme", "plasma",
                                  "vcs-branch"})

    def test_own_apps_render_without_theme_or_network(self):
        glyph = glyphs.standalone("tide-worktrees", self.config, PALETTE)
        image = glyphs.render(glyph, 64)
        self.assertEqual(image.size, (64, 64))
        with self.assertRaises(glyphs.GlyphError):
            glyphs.standalone("utilities-terminal", self.config, PALETTE)  # znak z motywu
        with self.assertRaises(glyphs.GlyphError):
            glyphs.standalone("nie-ma-takiej", self.config, PALETTE)

    def test_hand_made_glyphs_render(self):
        for path in sorted(glyphs.LOCAL_DIR.glob("*.svg")):
            mask = glyphs.glyph_mask(path)
            self.assertGreater(min(mask.size), 100, path.name)


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSystem()
        self.overlay = self.fake.data / "icons/Tide"

    def tearDown(self):
        self.fake.cleanup()

    def manifest(self):
        return json.loads((self.overlay / "tide-icons.json").read_text())["icons"]

    def test_glyphs_override_theme_icons_under_their_names(self):
        self.fake.glyphs([FIREFOX, KONSOLE, CHATGPT])
        self.fake.run("sync")
        manifest = self.manifest()
        for name in ("firefox", "utilities-terminal", "chatgpt"):
            self.assertEqual(manifest[name]["kind"], "muted", name)
            tile = Image.open(self.overlay / f"apps/256/{name}.png")
            self.assertEqual(tile.size, (256, 256))
            self.assertEqual(tile.getchannel("A").getpixel((2, 2)), 0)
        self.assertFalse((self.overlay / "apps/scalable/chatgpt.svg").exists())  # zamiast glifu-litery
        self.assertEqual(manifest["firefox"]["glyph"], "simpleIcons:openai@0.0.0-test")
        self.assertEqual(manifest["utilities-terminal"]["glyph"], "theme:utilities-terminal-symbolic")
        ink = Image.open(self.overlay / "apps/256/chatgpt.png").convert("RGB").getpixel((128, 128))
        self.assertEqual(ink, rgb(PALETTE["text"]["secondary"]))

    def test_toolbox_names_resolve_to_the_glyph(self):
        self.fake.glyphs([RIDER])
        self.fake.run("sync")
        manifest = self.manifest()
        self.assertEqual(manifest[RIDER_ID], {"kind": "muted", "target": "rider", "glyph": "simpleIcons:rider@0.0.0-test"})
        self.assertEqual(manifest["rider"]["kind"], "muted")
        self.assertEqual((self.overlay / f"apps/256/{RIDER_ID}.png").read_bytes(),
                         (self.overlay / "apps/256/rider.png").read_bytes())
        self.assertFalse((self.overlay / f"apps/scalable/{RIDER_ID}.svg").exists())

    def test_path_apps_get_the_glyph_under_their_new_name(self):
        zed = {"app": "Zed", "names": ["dev.zed.Zed"], "glyph": {"simpleIcons": "rider"}, "color": "sky"}
        code = {"app": "Code", "names": ["code"], "glyph": {"file": "code.svg"}, "color": "sky"}
        self.fake.glyphs([zed, code])
        self.fake.run("sync")
        manifest = self.manifest()
        self.assertEqual(manifest["dev.zed.Zed"]["kind"], "muted")
        self.assertEqual(manifest["code"]["glyph"], "file:code.svg")  # ręczny znak z repozytorium
        self.assertIn("Icon=dev.zed.Zed", (self.fake.data / "applications/dev.zed.Zed.desktop").read_text())
        self.assertIn("Icon=code", (self.fake.data / "applications/code_code.desktop").read_text())

    def test_audit_stays_complete(self):
        self.fake.glyphs([FIREFOX, RIDER, KONSOLE, CHATGPT])
        self.fake.run("sync")
        result = self.fake.run("audit", "--json", "--theme", "Tide", check=False)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(report["uncovered"], 0)
        details = {a["desktop_id"]: a["detail"] for a in report["apps"]}
        self.assertEqual(details["firefox.desktop"], "muted")
        self.assertEqual(details[f"{RIDER_ID}.desktop"], "muted → rider")

    def pin(self, *launchers):
        appletsrc = self.fake.config / "plasma-org.kde.plasma.desktop-appletsrc"
        appletsrc.parent.mkdir(parents=True, exist_ok=True)
        appletsrc.write_text("[Containments][1][Applets][2]\nplugin=org.kde.plasma.icontasks\n\n"
                             "[Containments][1][Applets][2][Configuration][General]\n"
                             f"launchers={','.join(launchers)}\n")

    def test_pinned_launchers_without_glyph_fail_the_audit(self):
        self.pin("applications:firefox.desktop", "preferred://filemanager", "applications:falkon.desktop",
                 f"file://{self.fake.system}/applications/{RIDER_ID}.desktop")
        self.fake.glyphs([FIREFOX, RIDER])
        self.fake.run("sync")
        result = self.fake.run("audit", "--json", "--theme", "Tide", check=False)
        report = json.loads(result.stdout)
        self.assertEqual(report["pinned"], ["firefox.desktop", "org.kde.dolphin.desktop", "falkon.desktop",
                                            f"{RIDER_ID}.desktop"])
        # Dolphina w udawanym systemie nie ma, Falkon ma kafelek, ale nie glif.
        self.assertEqual(report["pinnedWithoutGlyph"], ["org.kde.dolphin.desktop", "falkon.desktop"])
        self.assertEqual(report["uncovered"], 0)
        self.assertEqual(result.returncode, 1)
        human = self.fake.run("audit", "--theme", "Tide", check=False).stdout
        self.assertIn("Przypięte w menedżerach zadań: 4, bez glifu: 2", human)
        self.assertIn("  falkon.desktop", human)

    def test_every_pinned_launcher_with_a_glyph_passes(self):
        self.pin("applications:firefox.desktop", f"applications:{RIDER_ID}.desktop")
        self.fake.glyphs([FIREFOX, RIDER])
        self.fake.run("sync")
        result = self.fake.run("audit", "--json", "--theme", "Tide", check=False)
        report = json.loads(result.stdout)
        self.assertEqual(report["pinnedWithoutGlyph"], [])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(report["muted"], 3)  # Firefox, Rider i „Moja” z Icon=firefox

    def test_standalone_glyph_command_writes_png(self):
        out = self.fake.home / "hicolor/tide-worktrees.png"
        self.fake.env["TIDE_GLYPHS"] = str(glyphs.CONFIG)  # prawdziwa lista z ręcznymi znakami
        self.fake.run("glyph", "tide-worktrees", "--size", "128", "--out", str(out))
        image = Image.open(out)
        self.assertEqual(image.size, (128, 128))
        self.assertEqual(image.convert("RGBA").getpixel((2, 2))[3], 0)
        failed = self.fake.run("glyph", "utilities-terminal", "--out", str(out.with_name("x.png")), check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(out.with_name("x.png").exists())

    def test_summary_counts_glyphs(self):
        self.fake.glyphs([FIREFOX, RIDER])
        out = self.fake.run("sync").stdout
        self.assertIn("stonowane glify: 3", out)  # firefox, rider i nazwa z Toolboksa


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSystem()
        self.overlay = self.fake.data / "icons/Tide"
        self.cache = self.fake.home / ".cache/tide/simple-icons/0.0.0-test"

    def tearDown(self):
        self.fake.cleanup()

    def test_download_goes_to_the_cache_and_is_reused(self):
        self.fake.glyphs([FIREFOX])
        self.fake.run("sync")
        self.assertEqual((self.cache / "openai.svg").read_text(), SIMPLE_ICONS["openai"])
        (self.fake.simple_icons / "openai.svg").unlink()  # „bez sieci” — drugi raz z pamięci podręcznej
        self.fake.run("sync")
        self.assertTrue((self.overlay / "apps/256/firefox.png").is_file())

    def test_checksum_mismatch_keeps_the_original_icon(self):
        self.fake.glyphs([FIREFOX], pins={"openai": "0" * 64})
        result = self.fake.run("sync")
        self.assertIn("Firefox zostaje przy swojej ikonie", result.stderr)
        self.assertIn("SHA-256", result.stderr)
        self.assertNotIn("firefox", json.loads((self.overlay / "tide-icons.json").read_text())["icons"])
        self.assertFalse((self.cache / "openai.svg").exists())

    def test_tampered_cache_is_downloaded_again(self):
        self.fake.glyphs([FIREFOX])
        self.cache.mkdir(parents=True)
        (self.cache / "openai.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1v1z"/></svg>')
        self.fake.run("sync")
        self.assertEqual((self.cache / "openai.svg").read_text(), SIMPLE_ICONS["openai"])

    def test_unpinned_logo_is_not_used(self):
        self.fake.glyphs([FIREFOX], pins={})
        result = self.fake.run("sync")
        self.assertIn("brak sumy SHA-256 dla simpleIcons:openai", result.stderr)
        self.assertFalse((self.overlay / "apps/256/firefox.png").exists())

    def test_non_svg_download_is_rejected(self):
        (self.fake.simple_icons / "openai.svg").write_text("#!/bin/sh\necho nope\n")
        self.fake.glyphs([FIREFOX], pins={"openai": glyphs.sha256(b"#!/bin/sh\necho nope\n")})
        result = self.fake.run("sync")
        self.assertIn("nie wygląda na logo SVG", result.stderr)

    def test_missing_source_falls_back_to_the_next_one(self):
        self.fake.glyphs([KONSOLE])
        self.fake.run("sync")
        manifest = json.loads((self.overlay / "tide-icons.json").read_text())["icons"]
        self.assertEqual(manifest["utilities-terminal"]["glyph"], "theme:utilities-terminal-symbolic")

    def test_pin_glyphs_records_checksums(self):
        path = self.fake.glyphs([FIREFOX, RIDER], pins={"rider": "1" * 64})
        out = self.fake.run("pin-glyphs").stdout
        self.assertIn("openai", out)
        pins = json.loads(path.read_text())["simpleIcons"]["sha256"]
        self.assertEqual(pins["openai"], glyphs.sha256(SIMPLE_ICONS["openai"].encode()))
        self.assertEqual(pins["rider"], "1" * 64)  # zapisanej sumy nie nadpisujemy


if __name__ == "__main__":
    unittest.main()
