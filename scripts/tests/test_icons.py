"""Instalacja i odinstalowanie ikon Tide na tymczasowym katalogu domowym.

plasma-changeicons, flatpak, kbuildsycoca6 i dbus-send są atrapami; atrapa
plasma-changeicons zapisuje motyw w kdeglobals tak jak prawdziwe narzędzie.
"""
import subprocess
import unittest

from theme.icons.tests.fake_system import FakeSystem


class IconsInstallTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSystem()
        self.write_config("kdeglobals", "Icons", "Theme", "Base")
        self.overrides = self.fake.data / "flatpak/overrides/global"
        self.launcher = self.fake.home / ".local/bin/tide-icons"

    def tearDown(self):
        self.fake.cleanup()

    def write_config(self, file, group, key, value):
        subprocess.run(["kwriteconfig6", "--file", file, "--group", group, "--key", key, value],
                       env=self.fake.env, check=True)

    def icon_theme(self):
        return subprocess.run(["kreadconfig6", "--file", "kdeglobals", "--group", "Icons", "--key", "Theme"],
                              env=self.fake.env, check=True, capture_output=True, text=True).stdout.strip()

    def test_install_activates_overlay_and_opens_icons_to_flatpak(self):
        self.fake.script("install")
        self.assertEqual(self.icon_theme(), "Tide")
        self.assertTrue((self.fake.data / "icons/Tide/index.theme").is_file())
        self.assertIn("flatpak override --user --filesystem=xdg-data/icons:ro", self.fake.calls())
        self.assertIn("kbuildsycoca6 ", self.fake.calls())

    def test_installed_command_works_without_the_repository(self):
        self.fake.script("install")
        result = subprocess.run([str(self.launcher), "audit"], env=self.fake.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Motyw ikon: Tide", result.stdout)

    def test_uninstall_restores_theme_and_leaves_nothing_behind(self):
        before = sorted(p for p in self.fake.home.rglob("*") if p.is_file())
        self.fake.script("install")
        self.fake.script("uninstall")
        self.assertEqual(self.icon_theme(), "Base")
        after = sorted(p for p in self.fake.home.rglob("*") if p.is_file()
                       and p.name not in ("kdeglobals",) and ".cache" not in p.parts)
        self.assertEqual(after, [p for p in before if p.name != "kdeglobals"])
        self.assertFalse(self.overrides.exists())

    def test_uninstall_removes_only_our_desktop_copies(self):
        mine = (self.fake.data / "applications/mine.desktop").read_text()
        self.fake.script("install")
        self.assertTrue((self.fake.data / "applications/code_code.desktop").exists())
        self.fake.script("uninstall")
        self.assertFalse((self.fake.data / "applications/code_code.desktop").exists())
        self.assertEqual((self.fake.data / "applications/mine.desktop").read_text(), mine)

    def test_existing_flatpak_icon_access_is_kept(self):
        self.overrides.parent.mkdir(parents=True)
        self.overrides.write_text("[Context]\nfilesystems=xdg-data/icons:ro;home;\n")
        self.fake.script("install")
        self.assertFalse(any(c.startswith("flatpak ") for c in self.fake.calls()))
        self.fake.script("uninstall")
        self.assertEqual(self.overrides.read_text(), "[Context]\nfilesystems=xdg-data/icons:ro;home;\n")

    def test_uninstall_drops_only_our_flatpak_entry(self):
        self.fake.script("install")
        self.overrides.write_text("[Context]\nfilesystems=xdg-data/icons:ro;xdg-music;\n")
        self.fake.script("uninstall")
        self.assertEqual(self.overrides.read_text(), "[Context]\nfilesystems=xdg-music;\n")

    def test_reinstall_keeps_the_original_previous_theme(self):
        self.fake.script("install")
        self.fake.script("install")
        self.fake.script("uninstall")
        self.assertEqual(self.icon_theme(), "Base")

    def test_uninstall_keeps_a_theme_chosen_after_install(self):
        self.fake.script("install")
        self.write_config("kdeglobals", "Icons", "Theme", "Papirus")
        self.fake.script("uninstall")
        self.assertEqual(self.icon_theme(), "Papirus")
        self.assertFalse(any(c.startswith("plasma-changeicons Base") for c in self.fake.calls()))


if __name__ == "__main__":
    unittest.main()
