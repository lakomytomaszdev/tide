"""Profil środowiska pudełka (PT-165): box/profile.py i box/yamlmini.py.

Schemat i granica zgody, klasyfikacja ryzyka, scalanie, hash przepisu, zapis w Tablicy i propozycja
z repozytorium. Tablica i repozytoria są tymczasowe — nigdy ~/DevWorkspace/tide-board.
"""
import hashlib
import os
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import profile as bp
from tide_daemon.box import yamlmini
from tide_daemon.box.profile import ProfileError

KNOWN = {
    "registry.npmjs.org": "green",
    "api.nuget.org": "green",
    "storage.googleapis.com": "yellow",
    "objects.githubusercontent.com": "yellow",
}

DOC = """\
tide-box: 1
project: toolssuite
type: container            # container | vm
base: images:ubuntu/24.04
stacks: [node, playwright]
node: { version: "20" }
apt: [xvfb]
setup:
  - npx playwright install chromium
network:
  domains: [registry.npmjs.org, fonts.googleapis.com]
  host-ollama: false
ports: { pool: [4300, 4399], env: [PORT, E2E_PORT] }
docker: { enabled: false, images: [] }
resources: { cpu: 8, memory: 24GiB, disk: 60GB }
verify:
  - npm run lint
  - npm test -- --watch=false --browsers=ChromeHeadless
box: per-project
"""


def project(**extra):
    base = {"tide-box": 1, "stacks": ["node"], "apt": ["xvfb"], "setup": ["npm ci"],
            "network": {"domains": ["registry.npmjs.org"]}}
    base.update(extra)
    return base


def card(**extra):
    base = {"tide-box": 1}
    base.update(extra)
    return base


def stamp(prof, tv="t1", av="a1"):
    return bp.recipe_hash(prof, tv, av)


class SchemaTest(unittest.TestCase):
    def test_example_from_spec_is_valid(self):
        prof = bp.parse(DOC)
        self.assertEqual(prof["type"], "container")
        self.assertEqual(prof["node"], {"version": "20"})
        self.assertEqual(prof["ports"]["pool"], [4300, 4399])
        self.assertEqual(prof["verify"][1], "npm test -- --watch=false --browsers=ChromeHeadless")

    def test_defaults_for_resources(self):
        full = bp.with_defaults(bp.validate(card()))
        self.assertEqual(full["resources"], {"cpu": 8, "memory": "24GiB", "disk": "60GB"})
        self.assertEqual(full["type"], "container")
        self.assertEqual(full["box"], "per-project")

    def assertRejected(self, raw, scope="project", fragment=""):
        with self.assertRaises(ProfileError) as ctx:
            bp.validate(raw, scope)
        self.assertIn(fragment, str(ctx.exception))

    def test_rejects_unknown_keys_at_any_level(self):
        self.assertRejected(project(zzz=1), fragment="zzz: nieznany klucz")
        self.assertRejected(project(network={"domains": [], "proxy": 1}), fragment="network.proxy")
        self.assertRejected(project(docker={"enabled": True, "gpu": True}), fragment="docker.gpu")
        self.assertRejected(project(resources={"gpu": 1}), fragment="resources.gpu")

    def test_rejects_wrong_types(self):
        self.assertRejected(project(stacks="node"), fragment="stacks")
        self.assertRejected(project(apt=[1]), fragment="apt")
        self.assertRejected(project(docker={"enabled": "tak"}), fragment="docker.enabled")
        self.assertRejected(project(resources={"cpu": "8"}), fragment="resources.cpu")
        self.assertRejected(project(resources={"memory": "dużo"}), fragment="resources.memory")
        self.assertRejected(project(ports={"pool": [4400, 4300]}), fragment="ports.pool")
        self.assertRejected(project(box="per-user"), fragment="box")
        self.assertRejected({"tide-box": 2}, fragment="tide-box")
        self.assertRejected({"stacks": []}, fragment="tide-box")
        self.assertRejected([], fragment="mapą")

    def test_rejects_type_outside_container_vm(self):
        self.assertRejected(project(type="lxc"), fragment="type")

    def test_vm_passes_validation(self):
        self.assertEqual(bp.validate(project(type="vm"))["type"], "vm")

    def test_packs_only_in_card_profile(self):
        self.assertRejected(project(packs=["fonts"]), fragment="packs: nieznany klucz (tylko w profilu karty)")
        self.assertEqual(bp.validate(card(packs=["fonts"]), "card")["packs"], ["fonts"])
        self.assertRejected(project(accepted_red=["x"]), fragment="accepted_red")

    def test_internal_address_only_in_card_profile(self):
        for host in ("10.20.1.10", "10.0.0.5", "172.16.0.1", "172.31.255.1", "nuget.devhost.local",
                     "git.firma.example"):
            self.assertRejected(project(network={"domains": [host]}), fragment="adres wewnętrzny")
            self.assertEqual(bp.validate(card(network={"domains": [host]}), "card")["network"]["domains"], [host])

    def test_internal_address_in_other_notations(self):
        for host in ("::ffff:10.20.1.10", "::ffff:10.0.0.5", "10.1", "3232235786", "0xc0a8010a",
                     "010.0.0.5", "fd00::1", "fe80::1", "::1", "127.0.0.1", "localhost",
                     "169.254.169.254", "100.64.0.1", "x.localhost",
                     "nuget", "intranet", "build.lan", "x.internal", "router.home.arpa", "a.localdomain",
                     "x.corp", "*.lan", "::c0a8:10a", "64:ff9b::c0a8:10a"):
            self.assertRejected(project(network={"domains": [host]}), fragment="adres wewnętrzny")
            self.assertEqual(bp.classify(host), "red", host)
            self.assertEqual(bp.classify(host, {host: "green"}), "red", host)

    def test_project_red_reported_and_slug_mismatch_rejected(self):
        got = bp.merge(project(network={"domains": ["github.com"]}), card())
        self.assertEqual(got.project_red, ["github.com"])
        with self.assertRaises(bp.ProfileError):
            bp.save_project("a", project(project="b"))

    def test_broad_wildcard_rejected_in_project_and_red(self):
        for host in ("*.com", "*.io"):
            self.assertRejected(project(network={"domains": [host]}), fragment="zbyt szeroki")
            self.assertEqual(bp.classify(host, {host: "green"}), "red")
        self.assertEqual(bp.validate(project(network={"domains": ["*.npmjs.org"]}))["network"]["domains"],
                         ["*.npmjs.org"])

    def test_known_keys_and_accepted_red_are_normalized(self):
        self.assertEqual(bp.classify("api.sejm.gov.pl", {"API.Sejm.GOV.pl.": "green"}), "green")
        got = bp.merge(project(), card(network={"domains": ["GitHub.com"]}, accepted_red=["GitHub.com"]))
        self.assertIn("github.com", got.profile["network"]["domains"])
        self.assertEqual(got.dropped_red, [])

    def test_ports_env_is_unioned_but_pool_is_replaced(self):
        got = bp.merge(project(ports={"env": ["A"], "pool": [4000, 4100]}),
                       card(ports={"env": ["B"], "pool": [5000, 5100]})).profile
        self.assertEqual(got["ports"]["env"], ["A", "B"])
        self.assertEqual(got["ports"]["pool"], [5000, 5100])

    def test_dotnet_sdk_order_does_not_change_hash(self):
        a = bp.recipe_stamp(project(dotnet={"sdk": ["8.0", "6.0"]}), "t1", "a1")["hash"]
        b = bp.recipe_stamp(project(dotnet={"sdk": ["6.0", "8.0"]}), "t1", "a1")["hash"]
        self.assertEqual(a, b)

    def test_public_172_outside_private_range_is_not_internal(self):
        self.assertEqual(bp.validate(project(network={"domains": ["172.32.0.1"]}))["network"]["domains"],
                         ["172.32.0.1"])

    def test_network_full_never_in_project_profile(self):
        self.assertRejected(project(network={"full": True}), fragment="niedozwolone w profilu projektu")
        self.assertRejected(project(network={"full": True, "full-consent": True}),
                            fragment="niedozwolone w profilu projektu")
        self.assertEqual(bp.validate(project(network={"full": False}))["network"]["full"], False)

    def test_network_full_in_card_needs_explicit_consent(self):
        self.assertRejected(card(network={"full": True}), "card", "full-consent")
        got = bp.validate(card(network={"full": True, "full-consent": True}), "card")
        self.assertTrue(got["network"]["full"])

    def test_rejects_url_instead_of_host(self):
        self.assertRejected(project(network={"domains": ["https://github.com/x/y"]}), fragment="nazwą hosta")
        self.assertRejected(project(network={"domains": ["git@github.com:x/y"]}), fragment="nazwą hosta")


class ClassifyTest(unittest.TestCase):
    def test_known_classes_from_templates(self):
        self.assertEqual(bp.classify("registry.npmjs.org", KNOWN), "green")
        self.assertEqual(bp.classify("storage.googleapis.com", KNOWN), "yellow")
        self.assertEqual(bp.classify("objects.githubusercontent.com", KNOWN), "yellow")

    def test_red_items(self):
        for item in ("github.com", "git.firma.example", "10.20.1.10", "10.0.0.5", "172.20.1.1",
                     "nuget.devhost.local", "network.full", "packs", "pack:fonts", "api.github.com",
                     "gitlab.com", "firma.example"):
            self.assertEqual(bp.classify(item, KNOWN), "red", item)

    def test_unknown_public_domain_is_yellow(self):
        self.assertEqual(bp.classify("api.sejm.gov.pl", KNOWN), "yellow")
        self.assertEqual(bp.classify("api.sejm.gov.pl"), "yellow")

    def test_host_ollama_is_yellow(self):
        self.assertEqual(bp.classify("host-ollama", KNOWN), "yellow")

    def test_per_card_is_green(self):
        self.assertEqual(bp.classify("box:per-card"), "green")

    def test_template_cannot_make_red_item_green(self):
        hostile = {"github.com": "green", "10.20.1.10": "green", "firma.example": "yellow",
                   "network.full": "green", "packs": "green"}
        for item in hostile:
            self.assertEqual(bp.classify(item, hostile), "red", item)

    def test_template_may_tighten(self):
        self.assertEqual(bp.classify("example.org", {"example.org": "red"}), "red")

    def test_case_and_trailing_dot_normalised(self):
        self.assertEqual(bp.classify("GitHub.COM.", KNOWN), "red")

    def test_items_of_profile(self):
        prof = card(network={"domains": ["registry.npmjs.org", "github.com"], "host-ollama": True,
                             "full": True, "full-consent": True}, packs=["fonts"], box="per-card")
        got = dict(bp.items(prof, KNOWN))
        self.assertEqual(got, {"network.full": "red", "host-ollama": "yellow", "registry.npmjs.org": "green",
                               "github.com": "red", "pack:fonts": "red", "box:per-card": "green"})
        self.assertEqual(bp.red_items(prof, KNOWN), ["network.full", "github.com", "pack:fonts"])


class MergeTest(unittest.TestCase):
    def test_lists_summed_without_duplicates(self):
        res = bp.merge(project(), card(apt=["xvfb", "jq"], setup=["npm ci", "npm run gen"], stacks=["playwright"],
                                       network={"domains": ["registry.npmjs.org", "api.sejm.gov.pl"]}))
        prof = res.profile
        self.assertEqual(prof["apt"], ["xvfb", "jq"])
        self.assertEqual(prof["setup"], ["npm ci", "npm run gen"])
        self.assertEqual(prof["stacks"], ["node", "playwright"])
        self.assertEqual(prof["network"]["domains"], ["registry.npmjs.org", "api.sejm.gov.pl"])
        self.assertEqual(res.dropped_red, [])

    def test_per_card_from_card_wins(self):
        res = bp.merge(project(box="per-project"), card(box="per-card"))
        self.assertEqual(res.profile["box"], "per-card")
        res = bp.merge(project(box="per-card"), card())
        self.assertEqual(res.profile["box"], "per-card")

    def test_scalars_and_nested_from_card_override(self):
        res = bp.merge(project(resources={"cpu": 4, "disk": "10GB"}, node={"version": "18"}),
                       card(resources={"cpu": 12}, node={"version": "20"}))
        self.assertEqual(res.profile["resources"], {"cpu": 12, "disk": "10GB"})
        self.assertEqual(res.profile["node"], {"version": "20"})

    def test_red_items_dropped_without_accepted_red(self):
        res = bp.merge(project(), card(network={"domains": ["10.20.1.10", "github.com", "api.sejm.gov.pl"],
                                               "full": True, "full-consent": True}, packs=["fonts"]))
        self.assertEqual(res.profile["network"]["domains"], ["registry.npmjs.org", "api.sejm.gov.pl"])
        self.assertFalse(res.profile["network"].get("full"))
        self.assertNotIn("packs", res.profile)
        self.assertEqual(res.dropped_red, ["network.full", "10.20.1.10", "github.com", "pack:fonts"])

    def test_red_items_carried_when_accepted(self):
        res = bp.merge(project(), card(network={"domains": ["10.20.1.10", "github.com"], "full": True,
                                               "full-consent": True}, packs=["fonts"],
                                       accepted_red=["10.20.1.10", "network.full", "pack:fonts"]))
        self.assertIn("10.20.1.10", res.profile["network"]["domains"])
        self.assertNotIn("github.com", res.profile["network"]["domains"])
        self.assertTrue(res.profile["network"]["full"])
        self.assertEqual(res.profile["packs"], ["fonts"])
        self.assertEqual(res.dropped_red, ["github.com"])

    def test_accepted_red_naming_missing_item_is_error(self):
        with self.assertRaises(ProfileError) as ctx:
            bp.merge(project(), card(accepted_red=["10.20.9.9"]))
        self.assertIn("10.20.9.9", str(ctx.exception))

    def test_accepted_red_naming_non_red_item_is_error(self):
        with self.assertRaises(ProfileError):
            bp.merge(project(), card(network={"domains": ["api.sejm.gov.pl"]}, accepted_red=["api.sejm.gov.pl"]))

    def test_project_profile_is_validated_in_merge(self):
        with self.assertRaises(ProfileError):
            bp.merge(project(network={"full": True}), card())


class HashTest(unittest.TestCase):
    def test_hash_keys_and_non_hash_keys_cover_schema(self):
        schema = {"tide-box", "project", "type", "base", "stacks", "node", "dotnet", "apt", "setup",
                  "network.domains", "network.host-ollama", "network.full", "network.full-consent",
                  "ports.pool", "ports.env", "docker.enabled", "docker.images",
                  "resources.cpu", "resources.memory", "resources.disk", "verify", "packs", "box", "notes",
                  "accepted_red"}
        self.assertEqual(set(bp.HASH_KEYS) | set(bp.NON_HASH_KEYS), schema)
        self.assertFalse(set(bp.HASH_KEYS) & set(bp.NON_HASH_KEYS))

    def test_stable_against_key_order_and_whitespace(self):
        a = bp.parse(DOC)
        shuffled = """\
box:    per-project
verify: [ npm run lint ,   "npm test -- --watch=false --browsers=ChromeHeadless" ]
resources:
    memory: 24GiB
    cpu: 8
    disk: 60GB
docker: {images: [], enabled: false}   # bez Dockera
ports:
  env: [PORT, E2E_PORT]
  pool: [4300, 4399]
network: {host-ollama: false, domains: [registry.npmjs.org, fonts.googleapis.com]}
apt:
  - xvfb
setup: [npx playwright install chromium]
node:
  version: 20
stacks:
  - node
  - playwright
base: images:ubuntu/24.04
type: container
project: toolssuite
tide-box: 1
"""
        b = bp.parse(shuffled)
        self.assertEqual(stamp(a), stamp(b))

    def test_defaults_do_not_change_hash(self):
        self.assertEqual(stamp(bp.validate(card())),
                         stamp(bp.validate(card(type="container", box="per-project", stacks=[], apt=[]), "card")))

    def test_apt_order_irrelevant_setup_order_relevant(self):
        base = project(apt=["a", "b"], setup=["x", "y"])
        self.assertEqual(stamp(bp.validate(base)), stamp(bp.validate(project(apt=["b", "a"], setup=["x", "y"]))))
        self.assertNotEqual(stamp(bp.validate(base)), stamp(bp.validate(project(apt=["a", "b"], setup=["y", "x"]))))

    def test_changes_when_image_changes(self):
        base = bp.validate(project(node={"version": "20"}, dotnet={"sdk": ["8.0"]}, docker={"images": ["a:1"]}))
        ref = stamp(base)
        for key, val in (("apt", ["xvfb", "jq"]), ("setup", ["npm ci", "x"]), ("stacks", ["node", "dotnet"]),
                         ("node", {"version": "22"}), ("dotnet", {"sdk": ["10.0"]}),
                         ("docker", {"images": ["a:2"]}), ("docker", {"enabled": True, "images": ["a:1"]}),
                         ("base", "images:debian/12"), ("type", "vm")):
            other = bp.validate(project(**{**{"node": {"version": "20"}, "dotnet": {"sdk": ["8.0"]},
                                              "docker": {"images": ["a:1"]}}, key: val}))
            self.assertNotEqual(ref, stamp(other), key)

    def test_changes_with_templates_and_agents_version(self):
        prof = bp.validate(project())
        self.assertNotEqual(stamp(prof, "t1", "a1"), stamp(prof, "t2", "a1"))
        self.assertNotEqual(stamp(prof, "t1", "a1"), stamp(prof, "t1", "a2"))

    def test_unchanged_when_runtime_policy_changes(self):
        base = bp.validate(project(resources={"cpu": 8}, ports={"pool": [4300, 4399]}))
        ref = stamp(base)
        for key, val in (("resources", {"cpu": 2, "memory": "4GiB"}), ("ports", {"pool": [5000, 5099], "env": ["P"]}),
                         ("network", {"domains": ["registry.npmjs.org", "pypi.org"], "host-ollama": True}),
                         ("verify", ["make test"]), ("box", "per-card"), ("notes", "inne")):
            other = bp.validate(project(**{**{"resources": {"cpu": 8}, "ports": {"pool": [4300, 4399]}}, key: val}))
            self.assertEqual(ref, stamp(other), key)

    def test_hash_is_sha256_hex(self):
        h = stamp(bp.validate(project()))
        self.assertEqual(len(h), 64)
        int(h, 16)

    def test_diff_lists_changed_keys(self):
        old = bp.validate(project(apt=["xvfb"], stacks=["node"]))
        saved = bp.recipe_stamp(old, "t1", "a1")
        self.assertEqual(bp.diff(old, saved, "t1", "a1"), [])
        new = bp.validate(project(apt=["xvfb", "jq"], stacks=["node", "playwright"]))
        self.assertEqual(sorted(bp.diff(new, saved, "t1", "a1")), ["apt", "stacks"])

    def test_diff_reports_versions_and_plain_hash(self):
        prof = bp.validate(project())
        saved = bp.recipe_stamp(prof, "t1", "a1")
        self.assertEqual(bp.diff(prof, saved, "t2", "a1"), ["templates"])
        self.assertEqual(bp.diff(prof, saved["hash"], "t1", "a1"), [])
        self.assertEqual(bp.diff(prof, "0" * 64, "t1", "a1"), ["recipe"])

    def test_diff_ignores_non_hash_keys(self):
        old = bp.validate(project())
        saved = bp.recipe_stamp(old, "t1", "a1")
        new = bp.validate(project(resources={"cpu": 2}, network={"domains": ["pypi.org"]}))
        self.assertEqual(bp.diff(new, saved, "t1", "a1"), [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.board = os.path.join(tmp.name, "tide-board")
        os.makedirs(self.board)
        self.repo = os.path.join(tmp.name, "repo")
        os.makedirs(self.repo)
        patcher = mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": self.board})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_project_roundtrip_under_board_dir(self):
        prof = bp.parse(DOC)
        path = bp.save_project("toolssuite", prof)
        self.assertEqual(path, os.path.join(self.board, "projects", "toolssuite", "box.yaml"))
        self.assertEqual(bp.load_project("toolssuite"), prof)

    def test_load_missing_returns_none(self):
        self.assertIsNone(bp.load_project("nie-ma"))
        self.assertIsNone(bp.load_card("nie-ma", "K-1"))

    def test_card_roundtrip(self):
        prof = bp.validate(card(network={"domains": ["10.20.1.10"]}, packs=["fonts"], accepted_red=["pack:fonts"]),
                           "card")
        path = bp.save_card("plasma-tide", "K-38", prof)
        self.assertEqual(path, os.path.join(self.board, "projects", "plasma-tide", "cards", "k-38", "box.yaml"))
        self.assertEqual(bp.load_card("plasma-tide", "K-38"), prof)

    def test_roundtrip_of_awkward_strings(self):
        prof = bp.validate(project(project="p", setup=["echo 'a: b' # c", "true", "20", "@angular/cli", "~/x", "a\"b"],
                                   notes="linia: z dwukropkiem"))
        bp.save_project("p", prof)
        self.assertEqual(bp.load_project("p"), prof)

    def test_write_is_atomic_via_temp_file_and_rename(self):
        calls = []
        real = os.replace
        with mock.patch("os.replace", side_effect=lambda a, b: (calls.append((a, b)), real(a, b))[1]):
            path = bp.save_project("p", bp.validate(project()))
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0][0], path)
        self.assertEqual(calls[0][1], path)
        self.assertEqual(os.listdir(os.path.dirname(path)), ["box.yaml"])

    def test_failed_write_keeps_old_file_and_leaves_no_temp(self):
        path = bp.save_project("p", bp.validate(project(apt=["old"])))
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                bp.save_project("p", bp.validate(project(apt=["new"])))
        self.assertEqual(bp.load_project("p")["apt"], ["old"])
        self.assertEqual(os.listdir(os.path.dirname(path)), ["box.yaml"])

    def test_invalid_profile_is_not_written(self):
        with self.assertRaises(ProfileError):
            bp.save_project("p", {"tide-box": 1, "network": {"full": True, "full-consent": True}})
        self.assertFalse(os.path.exists(os.path.join(self.board, "projects")))

    def test_slug_cannot_escape_board(self):
        for slug in ("../x", "a/b", "", ".."):
            with self.assertRaises(ProfileError):
                bp.project_path(slug)
        with self.assertRaises(ProfileError):
            bp.card_path("p", "../../x")

    def test_stored_file_with_internal_address_is_rejected_on_read(self):
        path = bp.project_path("p")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            fh.write("tide-box: 1\nnetwork:\n  domains: [10.0.0.5]\n")
        with self.assertRaises(ProfileError):
            bp.load_project("p")

    def _repo_snapshot(self):
        out = {}
        for root, dirs, files in os.walk(self.repo):
            for name in dirs + files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, self.repo)
                if os.path.isfile(full):
                    with open(full, "rb") as fh:
                        out[rel] = hashlib.sha256(fh.read()).hexdigest()
                else:
                    out[rel] = "dir"
        return out

    def test_repo_proposal_reads_and_never_writes(self):
        os.makedirs(os.path.join(self.repo, ".tide"))
        with open(os.path.join(self.repo, ".tide", "box.yaml"), "w") as fh:
            fh.write(DOC)
        before = self._repo_snapshot()
        prof = bp.repo_proposal(self.repo)
        self.assertEqual(prof, bp.parse(DOC))
        self.assertEqual(self._repo_snapshot(), before)
        bp.save_project("toolssuite", prof)
        self.assertEqual(self._repo_snapshot(), before)
        self.assertEqual(os.listdir(self.repo), [".tide"])

    def test_repo_proposal_missing_is_none_and_creates_nothing(self):
        self.assertIsNone(bp.repo_proposal(self.repo))
        self.assertEqual(os.listdir(self.repo), [])

    def test_repo_proposal_is_validated_as_project_profile(self):
        os.makedirs(os.path.join(self.repo, ".tide"))
        with open(os.path.join(self.repo, ".tide", "box.yaml"), "w") as fh:
            fh.write("tide-box: 1\nnetwork: {full: true, full-consent: true}\n")
        with self.assertRaises(ProfileError):
            bp.repo_proposal(self.repo)

    def test_repo_proposal_rejects_symlink_leaving_repo(self):
        outside = os.path.join(os.path.dirname(self.repo), "outside.yaml")
        with open(outside, "w") as fh:
            fh.write(DOC)
        os.makedirs(os.path.join(self.repo, ".tide"))
        os.symlink(outside, os.path.join(self.repo, ".tide", "box.yaml"))
        with self.assertRaises(ProfileError):
            bp.repo_proposal(self.repo)

    def test_no_profile_file_lands_in_project_repository(self):
        before = self._repo_snapshot()
        bp.save_project("p", bp.validate(project()))
        bp.save_card("p", "K-1", bp.validate(card(), "card"))
        self.assertEqual(self._repo_snapshot(), before)


class YamlMiniTest(unittest.TestCase):
    def test_comments_and_quotes(self):
        self.assertEqual(yamlmini.loads('a: "x # y"  # komentarz\nb: \'it\'\'s\'\nc: x#y\n'),
                         {"a": "x # y", "b": "it's", "c": "x#y"})

    def test_list_at_key_indent(self):
        self.assertEqual(yamlmini.loads("a:\n- x\n- y\nb: 1\n"), {"a": ["x", "y"], "b": 1})

    def test_rejects_unsupported(self):
        for text in ("a: |\n  x\n", "a:\n\t- x\n", "a: 1\na: 2\n", "a:\n  - b: 1\n", "a: [1, 2\n", "a: &x 1\n",
                     "  a: 1\nb: 2\n"):
            with self.assertRaises(yamlmini.YamlError, msg=text):
                yamlmini.loads(text)

    def test_bad_yaml_becomes_profile_error(self):
        with self.assertRaises(ProfileError):
            bp.parse("tide-box: 1\nstacks: [a\n")


if __name__ == "__main__":
    unittest.main()
