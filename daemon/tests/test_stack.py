"""#30: wykrywanie stosu (StackReport) na fixture'ach czterech projektów, propozycja profilu i szablony stosów."""
import ast
import os
import stat
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import templates
from tide_daemon.tasks import dotnet, node, stack

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "stack")


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


class DetectTest(unittest.TestCase):
    def test_toolssuite_is_node_angular_karma_playwright(self):
        report = stack.detect(fixture("toolssuite"))
        self.assertEqual(report.stacks, ["node", "playwright"])
        self.assertEqual(report.node["version"], "20")
        self.assertEqual(report.node["angular"], "19")
        self.assertEqual(report.node["karma"], {"browsers": ["Chrome"], "port": 9876})
        self.assertEqual(report.node["playwright"]["port_env"], ["PORT"])
        self.assertTrue(report.node["playwright"]["web_server"])
        self.assertEqual(report.ci["images"], ["node:20"])
        self.assertTrue(report.ci["deploy_ignored"])
        self.assertEqual(report.dotnet, {})

    def test_legalapp_is_dotnet_with_testcontainers_image(self):
        report = stack.detect(fixture("legalapp"))
        self.assertEqual(report.stacks, ["playwright", "dotnet", "docker"])
        self.assertEqual(report.dotnet["tfms"], ["net10.0"])
        self.assertEqual(report.dotnet["sdk"], ["10.0"])
        self.assertEqual(report.dotnet["global_json_sdk"], "10.0.100")
        self.assertEqual(report.dotnet["testcontainers"], {"Testcontainers.PostgreSql": "4.15.0"})
        self.assertEqual(report.dotnet["playwright"], "1.52.0")
        self.assertEqual(report.testcontainers_images, ["pgvector/pgvector:pg16"])
        self.assertEqual(report.docker["compose_images"], ["pgvector/pgvector:pg16"])
        self.assertEqual(report.docker["ports"], [5432])

    def test_infracore_marks_the_extra_nuget_registry_as_internal(self):
        report = stack.detect(fixture("infracore"))
        self.assertEqual(report.stacks, ["dotnet"])
        self.assertEqual(report.dotnet["tfms"], ["net6.0", "net8.0", "netstandard2.0"])
        self.assertEqual(report.dotnet["sdk"], ["8.0", "6.0"])
        self.assertEqual(report.internal_addresses,
                         [{"url": "http://10.20.10.20:5000/v3/index.json", "host": "10.20.10.20",
                           "source": "nuget_devhost.config"}])
        by_host = {s["host"]: s for s in report.nuget_sources}
        self.assertFalse(by_host["api.nuget.org"]["internal"])
        self.assertTrue(by_host["api.nuget.org"]["known"])
        self.assertTrue(by_host["10.20.10.20"]["internal"])

    def test_plasma_tide_is_python_make_test_qml_offscreen(self):
        report = stack.detect(fixture("plasma-tide"))
        self.assertEqual(report.stacks, ["python-qt"])
        self.assertEqual(report.python["make_targets"], ["lint", "test"])
        self.assertTrue(report.python["qt_offscreen"])
        self.assertIn("Makefile", report.python["markers"])

    def test_project_without_pattern_has_no_stacks(self):
        self.assertEqual(stack.detect(fixture("empty-project")).stacks, [])
        self.assertEqual(stack.detect(os.path.join(FIXTURES, "nie-ma-takiego")).stacks, [])


class ReadsOnlyFilesTest(unittest.TestCase):
    def test_module_does_not_start_processes(self):
        source = os.path.join(os.path.dirname(stack.__file__), "stack.py")
        with open(source, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse(imported & {"subprocess", "socket", "urllib3", "requests"}, imported)
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse(names & {"system", "popen", "Popen", "run", "spawn"}, names)

    def test_npm_and_dotnet_on_path_are_not_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "started")
            for program in ("npm", "npx", "dotnet", "make", "node"):
                path = os.path.join(tmp, program)
                with open(path, "w") as handle:
                    handle.write(f"#!/bin/sh\necho {program} >> '{marker}'\n")
                os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
            with mock.patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ["PATH"]}):
                for name in ("toolssuite", "legalapp", "infracore", "plasma-tide"):
                    report = stack.detect(fixture(name))
                    stack.propose_profile(report, name)
            self.assertFalse(os.path.exists(marker))

    def test_two_runs_give_identical_results(self):
        for name in ("toolssuite", "legalapp", "infracore", "plasma-tide", "empty-project"):
            first, second = stack.detect(fixture(name)), stack.detect(fixture(name))
            self.assertEqual(first.to_dict(), second.to_dict())
            self.assertEqual(stack.propose_profile(first, name), stack.propose_profile(second, name))


class ProposalTest(unittest.TestCase):
    def proposals(self):
        names = ("toolssuite", "legalapp", "infracore", "plasma-tide")
        return {n: stack.propose_profile(stack.detect(fixture(n)), n) for n in names}

    def test_never_enables_a_red_item_or_full_network(self):
        for name, proposal in self.proposals().items():
            self.assertNotIn("full", proposal.get("network", {}), name)
            self.assertTrue(proposal["items"], name)
            for item in proposal["items"]:
                if item["risk"] == "red":
                    self.assertFalse(item["enabled"], (name, item))
            self.assertNotIn("10.20.10.20", proposal["network"]["domains"])

    def test_internal_nuget_address_is_a_disabled_red_item_with_description(self):
        items = self.proposals()["infracore"]["items"]
        internal = [i for i in items if i["value"] == "10.20.10.20"]
        self.assertEqual(len(internal), 1)
        self.assertEqual((internal[0]["risk"], internal[0]["enabled"]), ("red", False))
        self.assertIn("adres wewnętrzny", internal[0]["description"])
        self.assertIn("nuget_devhost.config", internal[0]["description"])

    def test_detected_testcontainers_image_goes_to_docker_images(self):
        docker = self.proposals()["legalapp"]["docker"]
        self.assertTrue(docker["enabled"])
        self.assertIn("pgvector/pgvector:pg16", docker["images"])
        self.assertIn("testcontainers/ryuk:0.11.0", docker["images"])
        self.assertEqual(self.proposals()["legalapp"]["stacks"], ["playwright", "dotnet", "docker"])

    def test_proposal_has_the_tide_box_shape(self):
        proposal = self.proposals()["toolssuite"]
        self.assertEqual(proposal["tide-box"], 1)
        self.assertEqual(proposal["node"], {"version": "20"})
        self.assertEqual(proposal["ports"]["env"], ["PORT"])
        self.assertIn("npm run build", proposal["verify"])
        self.assertFalse(proposal["docker"]["enabled"])

    def test_no_pattern_gives_empty_proposal_with_a_question(self):
        proposal = stack.propose_profile(stack.detect(fixture("empty-project")), "pusty")
        self.assertEqual(proposal["stacks"], [])
        self.assertEqual(proposal["items"], [])
        self.assertIn("pusty", proposal["question"])
        self.assertNotIn("network", proposal)
        self.assertNotIn("question", self.proposals()["plasma-tide"])


class TemplatesTest(unittest.TestCase):
    def test_every_template_has_domains_with_risk_class(self):
        for name in ["base", *templates.stack_names()]:
            data = templates.load(name)
            self.assertTrue(data["domains"], name)
            for entry in data["domains"]:
                self.assertIn(entry["risk"], templates.RISKS, (name, entry))

    def test_build_domains_dotnet_docker(self):
        domains = templates.build_domains({"stacks": ["dotnet", "docker"]})
        for expected in ("archive.ubuntu.com", "security.ubuntu.com", "api.nuget.org", "nuget.org",
                         "registry-1.docker.io", "auth.docker.io", "mcr.microsoft.com", "*.data.mcr.microsoft.com"):
            self.assertIn(expected, domains)

    def test_build_domains_node_playwright_and_profile_extras(self):
        domains = templates.build_domains({"stacks": ["node", "playwright"], "network": {"domains": ["a.example"]}})
        self.assertIn("registry.npmjs.org", domains)
        self.assertIn("cdn.playwright.dev", domains)
        self.assertIn("playwright.azureedge.net", domains)
        self.assertEqual(domains[-1], "a.example")
        self.assertEqual(len(domains), len(set(domains)))
        self.assertEqual(templates.build_domains({"stacks": ["node"]}), templates.build_domains({"stacks": ["node"]}))
        self.assertNotIn("cdn.playwright.dev", templates.build_domains({"stacks": ["node"]}))
        with self.assertRaises(ValueError):
            templates.build_domains({"stacks": ["nie-ma"]})

    def test_playwright_storage_domain_is_yellow(self):
        self.assertEqual(templates.known_domains()["storage.googleapis.com"], "yellow")
        self.assertEqual(templates.known_domains()["registry.npmjs.org"], "green")

    def test_no_repository_hosting_or_internal_address_in_any_template(self):
        for name in ["base", *templates.stack_names()]:
            for entry in templates.load(name)["domains"]:
                host = entry["name"]
                self.assertNotEqual(host, "github.com", name)
                self.assertNotIn("gitlab", host, name)
                self.assertFalse(stack._is_internal(host.replace("*.", "")), (name, host))
                self.assertNotEqual(entry["risk"], "red", (name, host))

    def test_docker_template_pins_ryuk(self):
        images = templates.load("docker")["images"]
        ryuk = [i for i in images if i.startswith("testcontainers/ryuk:")]
        self.assertEqual(len(ryuk), 1)
        self.assertNotIn(ryuk[0].split(":")[1], ("", "latest"))
        notes = {n["image"]: n for n in templates.load("docker")["image_notes"]}
        self.assertIn("K1", notes[ryuk[0]]["note"])

    def test_templates_are_yaml_data_not_shell(self):
        # Skrypty powłoki budowy (`*.sh`, #16/#29) leżą obok, ale szablonem stosu jest wyłącznie plik `.yaml`.
        for filename in os.listdir(templates.STACKS_DIR):
            self.assertTrue(filename.endswith((".yaml", ".sh")), filename)
        self.assertNotIn("tide-step", templates.stack_names())
        self.assertNotIn("base", templates.stack_names())

    def test_parser_reads_the_supported_subset_and_rejects_the_rest(self):
        data = templates.parse("stack: x\napt: [a, b]\nimages:\n  - r/i:1\ndomains:\n  - name: \"*.q\"\n    risk: red\n")
        self.assertEqual(data, {"stack": "x", "apt": ["a", "b"], "images": ["r/i:1"],
                                "domains": [{"name": "*.q", "risk": "red"}]})
        with self.assertRaises(templates.TemplateError):
            templates.parse("  wcięte: bez klucza\n", "x.yaml")


class RegistryDomainsTest(unittest.TestCase):
    def test_registry_domains_keep_todays_values(self):
        self.assertEqual(node.REGISTRY_DOMAINS, {
            "node": ("registry.npmjs.org", "registry.yarnpkg.com", "fonts.googleapis.com", "fonts.gstatic.com"),
            "dotnet": ("api.nuget.org", "globalcdn.nuget.org"),
            "python": ("pypi.org", "files.pythonhosted.org"),
        })
        self.assertEqual(dotnet.REGISTRY_DOMAINS, ("api.nuget.org", "globalcdn.nuget.org"))


if __name__ == "__main__":
    unittest.main()
