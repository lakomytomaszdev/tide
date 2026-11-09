"""PT-184: instalacja przeglądarki z paczki .NET bez Node/npm hosta i projektu użytkownika."""
from pathlib import Path
import shlex
import tempfile
import unittest
import xml.etree.ElementTree as ET

from .test_box_stacks import bash, sourced


class PlaywrightDotnetTest(unittest.TestCase):
    def test_installer_project_pins_package_and_targets_installed_runtime(self):
        for sdk, target in (("10.0.100", "net10.0"), ("8.0.415 10.0.100", "net8.0")):
            proc = bash(sourced("playwright", "dotnet_project"), env={"TIDE_DOTNET_SDK": sdk})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            root = ET.fromstring(proc.stdout)
            self.assertEqual(root.findtext("PropertyGroup/TargetFramework"), target)
            self.assertEqual(root.find("ItemGroup/PackageReference").attrib,
                             {"Include": "Microsoft.Playwright", "Version": "1.52.0"})

    def test_missing_or_channel_sdk_is_rejected(self):
        for sdk in ("", "10.0", "10.0.100; echo bad"):
            proc = bash(sourced("playwright", "dotnet_project"), env={"TIDE_DOTNET_SDK": sdk})
            self.assertNotEqual(proc.returncode, 0)

    def test_dotnet_profile_needs_no_node_and_mixed_profile_keeps_node_path(self):
        body = """
check_node() { echo node-check; }
install_browsers() { echo node-install; }
install_dotnet_browsers() { echo dotnet-install; }
install_playwright
"""
        for env, expected in (({"TIDE_DOTNET_SDK": "10.0.100"}, "dotnet-install\n"),
                              ({"TIDE_DOTNET_SDK": "10.0.100", "TIDE_NODE_VERSION": "22"},
                               "node-check\nnode-install\n"),
                              ({}, "node-check\nnode-install\n")):
            proc = bash(sourced("playwright", body), env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, expected)

    def install(self, root, restore_code=0, run_code=0, browser=True):
        body = f"""
PLAYWRIGHT_BROWSERS={shlex.quote(str(root / 'browsers'))}
dotnet() {{
    printf 'dotnet %s\\n' "$*"
    if [[ $1 == restore ]]; then
        cat "${{2%/*}}/Program.cs"
        return {restore_code}
    fi
    [[ $PLAYWRIGHT_BROWSERS_PATH == "$PLAYWRIGHT_BROWSERS" ]] || return 99
    {'mkdir -p "$PLAYWRIGHT_BROWSERS/chromium-1169/chrome-linux"; touch "$PLAYWRIGHT_BROWSERS/chromium-1169/chrome-linux/chrome"' if browser else ':'}
    return {run_code}
}}
chown() {{ echo chown-ok; }}
install_dotnet_browsers
"""
        return bash(sourced("playwright", body), env={"TIDE_DOTNET_SDK": "10.0.100"})

    def test_install_runs_pinned_dotnet_entrypoint_and_cleans_temp_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.install(Path(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('return Microsoft.Playwright.Program.Main(args);', proc.stdout)
            self.assertIn('--source https://api.nuget.org/v3/index.json', proc.stdout)
            self.assertIn('--configuration Release --no-restore -- install --with-deps chromium', proc.stdout)
            self.assertIn('chown-ok', proc.stdout)
            project = proc.stdout.splitlines()[0].split()[2]
            self.assertFalse(Path(project).parent.exists())

    def test_restore_or_install_failure_cannot_be_reported_as_success(self):
        for restore, run, browser in ((23, 0, True), (0, 24, True), (0, 0, False)):
            with tempfile.TemporaryDirectory() as tmp:
                proc = self.install(Path(tmp), restore, run, browser)
                self.assertNotEqual(proc.returncode, 0)
                self.assertNotIn('chown-ok', proc.stdout)
                if restore:
                    self.assertNotIn('dotnet run', proc.stdout)
                project = proc.stdout.splitlines()[0].split()[2]
                self.assertFalse(Path(project).parent.exists())


if __name__ == "__main__":
    unittest.main()
