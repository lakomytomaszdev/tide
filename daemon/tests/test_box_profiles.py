"""Profile akceptacyjne projektów: rzeczywiste pliki, schemat i granica sieci."""
from pathlib import Path
import unittest

from tide_daemon.box import profile, templates


ROOT = Path(__file__).resolve().parents[2]


class toolssuiteProfileTest(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "docs/box-profiles/toolssuite.yaml").read_text(encoding="utf-8")
        self.profile = profile.parse(self.text, "project")

    def test_profile_supports_acceptance_scenarios(self):
        full = profile.with_defaults(self.profile)
        self.assertEqual(full["project"], "toolssuite")
        self.assertEqual(full["stacks"], ["node", "playwright"])
        self.assertEqual(full["node"], {"version": "20"})
        self.assertEqual(full["ports"], {"pool": [4300, 4399], "env": ["PORT", "E2E_PORT"]})
        self.assertIn("npm run lint", full["verify"])
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright npx --yes playwright@1.64.1 install chromium",
                      full["setup"])
        self.assertFalse(full["docker"]["enabled"])
        self.assertFalse(full["resources"]["swap"])

    def test_effective_network_has_no_red_items_or_deploy_access(self):
        known = templates.known_domains()
        self.assertEqual(profile.red_items(self.profile, known), [])
        full = profile.with_defaults(self.profile)
        self.assertFalse(full["network"]["full"])
        self.assertFalse(full["network"]["host-ollama"])
        for domain in templates.work_domains(full):
            with self.subTest(domain=domain):
                self.assertNotEqual(profile.classify(domain, known), profile.RED)
        self.assertIn("registry.npmjs.org", templates.work_domains(full))
        self.assertIn("cdn.playwright.dev", templates.build_domains(full))
        for forbidden in ("git.firma.example", "nuget_devhost", "rsync"):
            self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
