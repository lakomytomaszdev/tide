"""PT-186: profil projektu i odmowa automatycznego udostępnienia lokalnego NuGet."""
from pathlib import Path
import importlib.util
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tide_daemon.box import profile as bp, templates
from tide_daemon.cardflow import envconsent, environment
from tide_daemon.tasks import stack

ROOT = Path(__file__).resolve().parents[2]


class InspectionGitTest(unittest.TestCase):
    def test_inspection_disables_fsmonitor_external_diff_and_textconv(self):
        spec = importlib.util.spec_from_file_location("inspect_coretools", ROOT / "scripts/box/inspect-infracore.py")
        inspection = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inspection)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repo = root / "repo"
            repo.mkdir()

            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

            git("init")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "App.csproj").write_text("<Project />\n")
            (repo / ".gitattributes").write_text("*.csproj diff=probe\n")
            git("add", ".")
            git("commit", "-m", "Initial fixture")
            (repo / "App.csproj").write_text("<Project>staged</Project>\n")
            git("add", ".")
            (repo / "App.csproj").write_text("<Project>working</Project>\n")
            marker = root / "executed"
            probe = root / "probe"
            probe.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                             f"Path({str(marker)!r}).write_text('executed')\nprint('converted')\n")
            probe.chmod(0o700)
            git("config", "core.fsmonitor", str(probe))
            git("status", "--porcelain=v1")
            self.assertTrue(marker.exists(), "fixture fsmonitor nie został wykonany")
            marker.unlink()
            git("config", "core.fsmonitor", "false")
            for setting in ("diff.probe.command", "diff.probe.textconv"):
                with self.subTest(setting=setting):
                    git("config", setting, str(probe))
                    git("diff", "HEAD")
                    self.assertTrue(marker.exists(), "fixture diff nie został wykonany")
                    marker.unlink()
                    git("config", "core.fsmonitor", str(probe))
                    output = root / "inspection.json"
                    with mock.patch.object(sys, "argv", ["inspect", "--repo", str(repo), "--output", str(output)]), \
                            mock.patch("builtins.print"):
                        inspection.main()
                    self.assertFalse(marker.exists())
                    data = json.loads(output.read_text())
                    self.assertIn("App.csproj", data["status"])
                    self.assertNotEqual(data["trackedDiffSha256"], data["indexDiffSha256"])
                    git("config", "core.fsmonitor", "false")
                    git("config", "--unset", setting)


class LocalNugetTest(unittest.TestCase):
    def report(self, source="/samba/nuget/"):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "App.csproj").write_text(
            '<Project><TargetFrameworks>net8.0;net6.0</TargetFrameworks></Project>', encoding="utf-8")
        (root / "nuget_devhost.config").write_text(
            '<configuration><packageSources><add key="devhost" value="' + source
            + '" /></packageSources></configuration>', encoding="utf-8")
        return root, stack.detect(str(root))

    def test_actual_local_feed_is_reported_and_never_becomes_a_domain(self):
        root, report = self.report()
        self.assertEqual(report.stacks, ["dotnet"])
        self.assertEqual(report.dotnet["sdk"], ["8.0", "6.0"])
        self.assertEqual(report.internal_addresses, [])
        self.assertEqual(report.nuget_sources, [{"source": "nuget_devhost.config", "key": "devhost",
                         "url": "/samba/nuget/", "host": "", "internal": True, "known": False, "local": True}])
        proposal = stack.propose_profile(report, "infracore")
        items = proposal.pop("items")
        [local] = [i for i in items if i["kind"] == "local-feed"]
        self.assertEqual((local["risk"], local["enabled"]), ("red", False))
        self.assertIn("nuget_devhost.config", local["description"])
        parsed = bp.validate(proposal, "project")
        self.assertNotIn("/samba/nuget/", templates.work_domains(parsed))
        self.assertNotIn("packs", parsed)
        self.assertEqual(bp.red_items(parsed), [])
        before = (root / "nuget_devhost.config").read_bytes()
        with mock.patch.object(environment, "_approved", return_value=(None, "")):
            prompt = environment.section(str(root), "infracore", "/out")
        self.assertIn("[wyłączona] local-feed /samba/nuget/ (ryzyko: red)", prompt)
        self.assertEqual((root / "nuget_devhost.config").read_bytes(), before)

    def test_local_variants_do_not_disappear_from_the_proposal(self):
        for source in ("/samba/nuget/", "../packages", "file:///samba/nuget/", "file://serwer/nuget/", r"C:\packages"):
            with self.subTest(source=source):
                _, report = self.report(source)
                proposal = stack.propose_profile(report, "infracore")
                [item] = [i for i in proposal["items"] if i["kind"] == "local-feed"]
                self.assertEqual((item["value"], item["risk"], item["enabled"]), (source, "red", False))

    def test_consent_cannot_turn_a_local_feed_into_a_network_permission(self):
        _, report = self.report()
        proposal = stack.propose_profile(report, "infracore")
        items = proposal.pop("items")
        shown = envconsent._template_items(items, accepted=["local-feed:/samba/nuget/", "/samba/nuget/"])
        [local] = [i for i in shown if i["kind"] == "local-feed"]
        self.assertEqual((local["group"], local["risk"], local["enabled"], local["locked"], local["needsConsent"]),
                         ("packs", "red", False, True, False))
        engine = SimpleNamespace(_project=lambda _: (None, SimpleNamespace(slug="infracore", repo="")))
        with mock.patch.object(envconsent, "_project_profile", return_value=(proposal, True, items, False, [])):
            prepared = envconsent.prepare(engine, "K-1", "tide-box: 1\n", SimpleNamespace())
            self.assertEqual(prepared.accepted, [])
            self.assertNotIn("/samba/nuget/", templates.work_domains(prepared.merged))
            with self.assertRaises(envconsent.EnvError):
                envconsent.prepare(engine, "K-1", 'tide-box: 1\naccepted_red: ["local-feed:/samba/nuget/"]\n',
                                   SimpleNamespace())


class ProjectProfileTest(unittest.TestCase):
    def test_project_profile_is_valid_pinned_and_has_no_private_sources(self):
        text = (ROOT / "docs/box-profiles/infracore.yaml").read_text(encoding="utf-8")
        profile = bp.parse(text, "project")
        self.assertEqual(profile["project"], "infracore")
        self.assertEqual(profile["stacks"], ["dotnet"])
        self.assertEqual(profile["dotnet"]["sdk"], ["8.0.415", "6.0.428"])
        projects = ["src/Commons/Commons.csproj", "tools/UploadSmokeTest/UploadSmokeTest.csproj"]
        self.assertEqual(profile["dotnet"]["restore-projects"], projects)
        self.assertEqual(profile["verify"], [f"dotnet build {p} --no-restore" for p in projects])
        self.assertEqual(bp.red_items(profile, templates.known_domains()), [])
        self.assertEqual(profile["network"]["domains"], [])
        self.assertNotIn("nuget_devhost", text)
        self.assertNotIn("/samba/", text)


if __name__ == "__main__":
    unittest.main()
