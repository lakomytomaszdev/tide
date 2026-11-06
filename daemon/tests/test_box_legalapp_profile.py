"""PT-184: rzeczywisty plik propozycji profilu i granica jego uprawnień."""
from pathlib import Path
import unittest

from tide_daemon.box import profile as bp, templates


class LegalAppProfileTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[2] / "docs/box-profiles/legalapp.yaml"
        self.profile = bp.parse(path.read_text(encoding="utf-8"), "project")

    def test_profile_matches_scenario_three(self):
        self.assertEqual(self.profile["project"], "legalapp")
        self.assertEqual(self.profile["stacks"], ["dotnet", "docker", "playwright"])
        self.assertTrue(self.profile["docker"]["enabled"])
        self.assertEqual(self.profile["docker"]["images"], ["pgvector/pgvector:pg16"])
        self.assertEqual(self.profile["verify"], ["dotnet build"])
        for sdk in self.profile["dotnet"]["sdk"]:
            self.assertRegex(sdk, r"^10\.0\.\d+$")

    def test_work_policy_has_no_red_domains_or_sejm(self):
        domains = templates.work_domains(self.profile)
        self.assertNotIn("api.sejm.gov.pl", domains)
        self.assertNotIn("github.com", domains)
        for domain in domains:
            self.assertNotEqual(bp.classify(domain, templates.known_domains()), bp.RED, domain)
        for name, _group, risk in bp.display_items(self.profile, templates.known_domains()):
            self.assertNotEqual(risk, bp.RED, name)

    def test_ryuk_comes_from_docker_template(self):
        images = templates.build_images(self.profile)
        self.assertIn("pgvector/pgvector:pg16", images)
        self.assertIn("testcontainers/ryuk:0.11.0", images)
        self.assertNotIn("testcontainers/ryuk:0.11.0", self.profile["docker"]["images"])


if __name__ == "__main__":
    unittest.main()
