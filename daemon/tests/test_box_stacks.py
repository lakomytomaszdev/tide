"""Szablony stosów (PT-192, #29): `box/stacks/<stos>.sh` z `lib.sh`, `STACKS_VERSION`, drop-in dockerd i lista domen
budowy podawana prawdziwemu proxy z #5.

Prawdziwe skrypty szablonów nigdy nie są wykonywane w całości (instalują pakiety jako root pudełka): ich tekst jest
sprawdzany regułami z ticketu, a funkcje tworzące pliki biegną po `source` w bashu z katalogiem tymczasowym jako
katalogiem głównym. Budowa biegnie na atrapie `incus` z prawdziwymi szablonami danych (`*.yaml`) i atrapami skryptów.
"""
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from tide_daemon.box import base as bbase
from tide_daemon.box import builder as bb
from tide_daemon.box import cli
from tide_daemon.box import netenv
from tide_daemon.box import profile as bp
from tide_daemon.box import proxy as bproxy
from tide_daemon.box import templates
from tide_daemon.box.incus import Incus
from tide_daemon.box.settings import BoxSettings

from .fakebin import calls, make_env

DAEMON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(DAEMON)
STACKS = os.path.join(DAEMON, "tide_daemon", "box", "stacks")
LIB = os.path.join(STACKS, "lib.sh")
STACK_SCRIPTS = ("node", "playwright", "dotnet", "docker", "python-qt")
PROXY = "http://10.173.184.1:18080"
DROPIN = "/etc/systemd/system/docker.service.d/proxy.conf"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def script(stack):
    return os.path.join(STACKS, f"{stack}.sh")


def code_of(text):
    """Tekst skryptu bez komentarzy (całych wierszy i końcówek ` # …`)."""
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(lines)


def bash(body, env=None, check=True, case=None):
    full = {"PATH": "/usr/bin:/bin", "TIDE_BOX_PROXY": PROXY, "TIDE_BOX_NO_PROXY": netenv.BOX_NO_PROXY,
            "HTTPS_PROXY": PROXY, "HTTP_PROXY": PROXY, **(env or {})}
    proc = subprocess.run(["bash", "-c", body], env=full, capture_output=True, text=True)
    if check and case is not None:
        case.assertEqual(proc.returncode, 0, proc.stderr)
    return proc


def sourced(stack, body):
    return f"source {shlex.quote(script(stack))}\n{body}\n"


# ------------------------------------------------------------ tekst skryptów: reguły z ticketu

class StackScriptTextTest(unittest.TestCase):
    def test_every_template_has_a_script_and_every_script_a_template(self):
        self.assertEqual(sorted(templates.stack_names()), sorted(STACK_SCRIPTS))
        scripts = sorted(n[:-3] for n in os.listdir(STACKS) if n.endswith(".sh"))
        self.assertEqual(scripts, sorted([*STACK_SCRIPTS, "base", "lib", "tide-step"]))

    def test_strict_mode_syntax_and_lib(self):
        for stack in STACK_SCRIPTS:
            text = read(script(stack))
            self.assertIn("\nset -euo pipefail\n", text, stack)
            self.assertIn('source "${BASH_SOURCE[0]%/*}/lib.sh"', text, stack)
            self.assertRegex(text, r'(?m)^if \[\[ \$\{BASH_SOURCE\[0\]\} == "\$0" \]\]; then\n    main "\$@"\nfi$', stack)
        for path in (*map(script, STACK_SCRIPTS), LIB):
            proc = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck niedostępny")
    def test_shellcheck(self):
        proc = subprocess.run(["shellcheck", "-x", "-s", "bash", *(f"{s}.sh" for s in STACK_SCRIPTS), "lib.sh"],
                              cwd=STACKS, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_no_pipe_to_shell_and_urls_only_to_the_stacks_own_domains(self):
        self.assertNotRegex(code_of(read(LIB)), r"https?://[A-Za-z0-9]")
        for stack in STACK_SCRIPTS:
            code = code_of(read(script(stack)))
            self.assertNotRegex(code, r"\|\s*(sudo\s+)?(ba|z|da)?sh\b", stack)
            self.assertNotRegex(code, r"\b(curl|wget)\b", f"{stack}: pobieranie tylko przez fetch z lib.sh")
            data = templates.load(stack)
            allowed = {d["name"] for d in data.get("domains", []) + data.get(templates.BUILD_ONLY, [])}
            for host in re.findall(r"https?://([A-Za-z0-9.-]+)", code):
                self.assertIn(host, allowed, f"{stack}.sh: {host} spoza domen szablonu {stack}.yaml")
            self.assertNotIn("http://", code, f"{stack}.sh: pobieranie tylko przez https")

    def test_versions_are_pinned(self):
        for stack in STACK_SCRIPTS:
            code = code_of(read(script(stack))).lower()
            self.assertNotRegex(code, r"\blatest\b|\bstable\b|\bnightly\b|\bcanary\b", stack)
        pins = {"playwright": r"^PLAYWRIGHT_VERSION=(\S+)$", "docker": r"^RUNC_VERSION=(\S+)$"}
        for stack, pattern in pins.items():
            version = re.search(pattern, read(script(stack)), re.M).group(1)
            self.assertRegex(version, r"^\d+\.\d+\.\d+$", stack)
        node_pins = re.search(r"^declare -A NODE_PINS=\((.*)\)$", read(script("node")), re.M).group(1)
        values = re.findall(r"\[(\d+)\]=(\S+)", node_pins)
        self.assertGreaterEqual(len(values), 3)
        for major, version in values:
            self.assertRegex(version, rf"^{major}\.\d+\.\d+$")
        self.assertIn('npx --yes "playwright@$PLAYWRIGHT_VERSION"', read(script("playwright")))
        self.assertRegex(read(script("docker")), r"(?m)^RUNC_SHA256_AMD64=[0-9a-f]{64}$")

    def test_runc_checksum_is_the_one_from_prototype_p1(self):
        proto = re.search(r"^RUNC_126_SHA256=([0-9a-f]{64})$",
                          read(os.path.join(ROOT, "scripts", "box", "prototype", "p1.sh")), re.M).group(1)
        self.assertIn(f"RUNC_SHA256_AMD64={proto}\n", read(script("docker")))
        self.assertIn("RUNC_VERSION=1.2.6\n", read(script("docker")))

    def test_no_host_agent_directories_and_no_injected_values(self):
        for path in (*map(script, STACK_SCRIPTS), LIB):
            text = read(path)
            self.assertNotRegex(text, r"(~|\$HOME|\$\{HOME\}|/home/[A-Za-z0-9_]+|/root)/\.(claude|codex)\b", path)
            self.assertNotRegex(text, r"\.credentials\.json|auth\.json|\.claude\b|\.codex\b", path)
            self.assertNotIn(netenv.BOX_NO_PROXY, text, path)
            self.assertNotIn("172.16.0.0/12", text, path)
            self.assertNotIn("10.173.184.", text, path)
            self.assertNotRegex(code_of(text), r"DOCKER_HOST\s*=", path)

    def test_docker_template_does_not_pull_images(self):
        code = code_of(read(script("docker")))
        self.assertNotRegex(code, r"\bdocker\s+(pull|run|image)\b")
        for stack in STACK_SCRIPTS:
            self.assertNotIn("docker.service.d", read(script(stack)) if stack != "docker" else "")


# ------------------------------------------------------------ STACKS_VERSION

class StacksVersionTest(unittest.TestCase):
    def test_saved_version_is_the_checksum_of_the_stacks_directory(self):
        self.assertEqual(
            bb.templates_version(), bb.STACKS_VERSION,
            "szablony stosów się zmieniły: ustaw builder.STACKS_VERSION na nową sumę (pudełka będą do przebudowy)")

    def test_any_template_change_changes_the_version(self):
        tmp = tempfile.mkdtemp(prefix="tide-stacks-")
        self.addCleanup(shutil.rmtree, tmp, True)
        copy = os.path.join(tmp, "stacks")
        shutil.copytree(STACKS, copy, ignore=shutil.ignore_patterns("__pycache__"))
        real = bb.templates_version()
        with mock.patch.object(templates, "STACKS_DIR", copy):
            self.assertEqual(bb.templates_version(copy), real)
            seen = {bb.templates_version(copy)}
            for name in sorted(os.listdir(copy)):
                with open(os.path.join(copy, name), "a", encoding="utf-8") as fh:
                    fh.write("\n")
                version = bb.templates_version(copy)
                self.assertNotIn(version, seen, f"zmiana {name} nie zmieniła wersji szablonów")
                seen.add(version)

    def test_version_is_in_the_recipe_hash(self):
        profile = {"tide-box": 1, "stacks": ["node"]}
        a = bp.recipe_hash(profile, bb.STACKS_VERSION, "a")
        b = bp.recipe_hash(profile, "f" * 64, "a")
        self.assertNotEqual(a, b)
        self.assertEqual(bp.diff(profile, bp.recipe_stamp(profile, "f" * 64, "a"), bb.STACKS_VERSION, "a"),
                         ["templates"])


# ------------------------------------------------------------ docker.sh: drop-in dockerd

class DockerDropinTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-docker-root-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def environment(self, path):
        values = {}
        for line in read(path).splitlines():
            m = re.fullmatch(r'Environment="([A-Za-z_]+)=(.*)"', line)
            if m:
                self.assertNotIn(m.group(1), values, "zmienna dwa razy")
                values[m.group(1)] = m.group(2)
        return values

    def test_docker_sh_is_the_only_file_creating_the_dockerd_dropin(self):
        # Poza dokumentacją, testami i jednorazowymi skryptami prototypu (#15, docs/box-prototype.md).
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv"}
        skip_prefixes = (os.path.join(ROOT, "docs") + os.sep, os.path.join(ROOT, "scripts", "box", "prototype") + os.sep,
                         os.path.join(DAEMON, "tests") + os.sep, os.path.join(ROOT, "scripts", "tests") + os.sep)
        found = []
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            if (folder + os.sep).startswith(skip_prefixes):
                continue
            for name in files:
                path = os.path.join(folder, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        if "docker.service.d" in fh.read():
                            found.append(os.path.relpath(path, ROOT))
                except (UnicodeDecodeError, OSError):
                    continue
        self.assertEqual(found, [os.path.relpath(script("docker"), ROOT)])

    def test_dropin_has_the_proxy_and_the_full_no_proxy_constant(self):
        bash(sourced("docker", f"write_dockerd_proxy {shlex.quote(self.root)}"), case=self)
        path = self.root + DROPIN
        self.assertIn("[Service]", read(path))
        values = self.environment(path)
        self.assertEqual(values["HTTPS_PROXY"], PROXY)
        self.assertEqual(values["HTTP_PROXY"], PROXY)
        self.assertEqual(values["NO_PROXY"], netenv.BOX_NO_PROXY)
        self.assertEqual(values, netenv.proxy_env(PROXY))
        self.assertNotIn("DOCKER_HOST", read(path))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)

    def test_dropin_follows_what_the_builder_injects(self):
        other = "http://10.99.0.1:18555"
        env = netenv.proxy_env(other)
        bash(sourced("docker", f"write_dockerd_proxy {shlex.quote(self.root)}"),
             env={"TIDE_BOX_PROXY": other, "TIDE_BOX_NO_PROXY": env["NO_PROXY"]}, case=self)
        self.assertEqual(self.environment(self.root + DROPIN), env)

    def test_no_dropin_without_proxy_or_with_empty_no_proxy(self):
        for env in ({"TIDE_BOX_NO_PROXY": ""}, {"TIDE_BOX_PROXY": ""}):
            proc = bash(sourced("docker", f"write_dockerd_proxy {shlex.quote(self.root)}"), env=env)
            self.assertNotEqual(proc.returncode, 0, env)
            self.assertIn("NO_PROXY", proc.stderr)
            self.assertFalse(os.path.exists(self.root + DROPIN))

    def test_daemon_json_makes_runc_126_the_default_runtime(self):
        bash(sourced("docker", f"write_daemon_json {shlex.quote(self.root)}"), case=self)
        data = json.loads(read(os.path.join(self.root, "etc", "docker", "daemon.json")))
        self.assertEqual(data, {"default-runtime": "runc-126",
                                "runtimes": {"runc-126": {"path": "/usr/local/bin/runc-1.2.6"}}})

    def test_main_adds_agent_to_docker_group_and_checks_dockerd(self):
        code = code_of(read(script("docker")))
        self.assertIn('usermod -aG docker "$AGENT"', code)
        self.assertIn('check_sha256 "$RUNC_SHA256_AMD64" "$work/runc"', code)
        self.assertIn("systemctl show docker -p Environment", code)
        self.assertIn("as_agent docker info", code)
        main = code[code.index("main() {"):]
        order = [main.index(s) for s in ("stack_main", "apt_install", "install_runc", "write_daemon_json",
                                         "write_dockerd_proxy", "usermod", "daemon-reload", "restart docker",
                                         "check_dockerd")]
        self.assertEqual(order, sorted(order))


# ------------------------------------------------------------ lib.sh

class LibTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-lib-root-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def lib(self, body, **kw):
        return bash(f"source {shlex.quote(LIB)}\n{body}\n", **kw)

    def test_bad_proxy_inputs_are_refused(self):
        for env, what in (({"TIDE_BOX_PROXY": "http://proxy.example.org:1"}, "TIDE_BOX_PROXY"),
                          ({"TIDE_BOX_PROXY": ""}, "TIDE_BOX_PROXY"),
                          ({"TIDE_BOX_NO_PROXY": ""}, "TIDE_BOX_NO_PROXY"),
                          ({"TIDE_BOX_NO_PROXY": 'x";touch /tmp/zle;"'}, "TIDE_BOX_NO_PROXY"),
                          ({"HTTPS_PROXY": ""}, "bez proxy"),
                          ({"HTTP_PROXY": "http://10.0.0.9:1"}, "bez proxy")):
            proc = self.lib("check_proxy", env=env)
            self.assertNotEqual(proc.returncode, 0, env)
            self.assertIn(what, proc.stderr)
        self.lib("check_proxy", case=self)

    def test_fetch_only_https_and_checksum_mismatch_fails(self):
        proc = self.lib("fetch http://nodejs.org/x /dev/null")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("tylko https", proc.stderr)
        target = os.path.join(self.root, "f")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("abc")
        proc = self.lib(f"check_sha256 {'0' * 64} {shlex.quote(target)}")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("suma SHA-256", proc.stderr)
        good = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        self.lib(f"check_sha256 {good} {shlex.quote(target)}", case=self)
        self.assertIn("--proto '=https' --proto-redir '=https'", read(LIB))

    def test_stack_env_goes_to_environment_login_shells_and_user_services(self):
        os.makedirs(os.path.join(self.root, "etc"))
        with open(os.path.join(self.root, "etc", "environment"), "w", encoding="utf-8") as fh:
            fh.write(f'PATH="/usr/bin"\nNO_PROXY="{netenv.BOX_NO_PROXY}"\nCHROME_BIN="/stary"\n')
        root = shlex.quote(self.root)
        for _ in range(2):
            self.lib(f"write_stack_env {root} node CHROME_BIN=/usr/local/bin/tide-chrome NODE_USE_ENV_PROXY=1",
                     case=self)
        env = read(os.path.join(self.root, "etc", "environment"))
        self.assertEqual(env.splitlines(), ['PATH="/usr/bin"', f'NO_PROXY="{netenv.BOX_NO_PROXY}"',
                                            'CHROME_BIN="/usr/local/bin/tide-chrome"', 'NODE_USE_ENV_PROXY="1"'])
        self.assertIn('export CHROME_BIN="/usr/local/bin/tide-chrome"',
                      read(os.path.join(self.root, "etc", "profile.d", "tide-node.sh")))
        user = read(os.path.join(self.root, "etc", "systemd", "user.conf.d", "91-tide-node.conf"))
        self.assertIn("[Manager]", user)
        self.assertIn('DefaultEnvironment="CHROME_BIN=/usr/local/bin/tide-chrome" "NODE_USE_ENV_PROXY=1"', user)

    def test_stack_env_refuses_unsafe_values(self):
        for pair in ('X=a b', 'x=1', 'X=$(id)', 'X="a"', 'BEZ'):
            proc = self.lib(f"write_stack_env {shlex.quote(self.root)} node {shlex.quote(pair)}")
            self.assertNotEqual(proc.returncode, 0, pair)
            self.assertIn("niedozwolona zmienna", proc.stderr)

    def test_scripts_refuse_to_run_outside_the_box_root(self):
        if os.geteuid() == 0:
            self.skipTest("test jako root")
        for stack in STACK_SCRIPTS:
            proc = bash(f"bash {shlex.quote(script(stack))}")
            self.assertEqual(proc.returncode, 1, (stack, proc.stderr))
            self.assertIn("root pudełka", proc.stderr)


# ------------------------------------------------------------ node.sh, playwright.sh, dotnet.sh, python-qt.sh

class StackFunctionsTest(unittest.TestCase):
    def test_node_version_resolution(self):
        pins = dict(re.findall(r"\[(\d+)\]=(\S+)",
                               re.search(r"^declare -A NODE_PINS=\((.*)\)$", read(script("node")), re.M).group(1)))
        default = re.search(r"^NODE_DEFAULT_MAJOR=(\d+)$", read(script("node")), re.M).group(1)
        for want, got in (("", pins[default]), ("20", pins["20"]), ("22.1.0", "22.1.0")):
            proc = bash(sourced("node", f"node_version {shlex.quote(want)}"), case=self)
            self.assertEqual(proc.stdout.strip(), got)
        self.assertGreaterEqual(int(default), 20, "Playwright ≥ 1.50 wymaga Node ≥ 20 (P-6)")
        for bad in ("20.11", "19", "latest", "lts/iron", "v20"):
            proc = bash(sourced("node", f"node_version {shlex.quote(bad)}"))
            self.assertNotEqual(proc.returncode, 0, bad)
            self.assertIn("node.version", proc.stderr)

    def test_chrome_wrapper_points_karma_at_playwright_chromium(self):
        root = tempfile.mkdtemp(prefix="tide-node-root-")
        self.addCleanup(shutil.rmtree, root, True)
        bash(sourced("node", f"write_chrome_wrapper {shlex.quote(root)}"), case=self)
        wrapper = os.path.join(root, "usr", "local", "bin", "tide-chrome")
        self.assertEqual(os.stat(wrapper).st_mode & 0o777, 0o755)
        text = read(wrapper)
        self.assertIn('found=("/opt/ms-playwright"/chromium-*/chrome-linux*/chrome)', text)
        self.assertIn('exec "${found[-1]}" "$@"', text)
        self.assertIn('"CHROME_BIN=$CHROME_WRAPPER"', read(script("node")))
        if not os.path.exists("/opt/ms-playwright"):
            proc = subprocess.run(["bash", wrapper, "--version"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 127)
            self.assertIn("dodaj stos playwright", proc.stderr)

    def test_playwright_needs_node_20_from_the_node_stack(self):
        tmp = tempfile.mkdtemp(prefix="tide-pw-")
        self.addCleanup(shutil.rmtree, tmp, True)
        node = os.path.join(tmp, "node")
        for version, ok in (("v18.19.1", False), ("v20.19.5", True), ("v24.11.0", True)):
            with open(node, "w", encoding="utf-8") as fh:
                fh.write(f"#!/bin/sh\necho {version}\n")
            os.chmod(node, 0o755)
            proc = bash(sourced("playwright", "check_node"), env={"PATH": f"{tmp}:/usr/bin:/bin"})
            self.assertEqual(proc.returncode == 0, ok, (version, proc.stderr))
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS", read(script("playwright")))
        self.assertIn("install --with-deps chromium", read(script("playwright")))

    def test_dotnet_versions_from_the_profile(self):
        for want, args, prefix in (("8", "--channel 8.0", "8.0."), ("8.0", "--channel 8.0", "8.0."),
                                   ("10.0", "--channel 10.0", "10.0."), ("8.0.415", "--version 8.0.415", "8.0.415")):
            proc = bash(sourced("dotnet", f"sdk_args {want}; sdk_prefix {want}"), case=self)
            self.assertEqual(proc.stdout.split("\n")[:2], [args, prefix], want)
        for bad in ("latest", "8.0.x", "net8.0", "8.0.415.1"):
            proc = bash(sourced("dotnet", f"sdk_args {shlex.quote(bad)}"))
            self.assertNotEqual(proc.returncode, 0, bad)
            self.assertIn("dotnet.sdk", proc.stderr)

    def test_nuget_config_has_only_nuget_org(self):
        proc = bash(sourced("dotnet", "nuget_config"), case=self)
        tree = ET.fromstring(proc.stdout.strip())
        sources = tree.find("packageSources")
        self.assertEqual([child.tag for child in sources], ["clear", "add"])
        add = sources.find("add")
        self.assertEqual((add.get("key"), add.get("value")), ("nuget.org", "https://api.nuget.org/v3/index.json"))
        self.assertEqual([el.tag for el in tree], ["packageSources"])
        self.assertEqual(proc.stdout.count("<add "), 1)
        # Zapis jako agent (wolumen agenta), nie przez dowiązania jako root.
        self.assertIn("nuget_config | as_agent bash -c", read(script("dotnet")))

    def test_python_qt_is_offscreen_with_pyqt6_from_apt(self):
        text = read(script("python-qt"))
        self.assertIn("write_stack_env \"\" python-qt QT_QPA_PLATFORM=offscreen", text)
        for pkg in ("python3-pyqt6", "python3-pyqt6.qtquick", "qml6-module-qtquick"):
            self.assertRegex(text, rf"[ (]{re.escape(pkg)}[ )\n]")
        self.assertNotIn("pip install", code_of(text))


# ------------------------------------------------------------ szablony danych: domeny tylko budowy

class BuildOnlyDomainsTest(unittest.TestCase):
    def test_github_only_for_the_docker_build_never_for_steps(self):
        profile = {"tide-box": 1, "stacks": ["dotnet", "docker"]}
        build = templates.build_domains(profile)
        work = templates.work_domains(profile)
        for host in ("github.com", "release-assets.githubusercontent.com", "aka.ms"):
            self.assertIn(host, build)
            self.assertNotIn(host, work)
            self.assertNotIn(host, templates.known_domains())
        composed = bproxy.compose_policy(profile, {"K-1": {"tide-box": 1}})
        self.assertNotIn("github.com", composed.domains)
        self.assertNotIn("aka.ms", composed.domains)
        self.assertEqual(bp.classify("github.com", templates.known_domains()), bp.RED)
        self.assertEqual(build[:len(work)], work)
        self.assertNotIn("github.com", templates.build_domains({"tide-box": 1, "stacks": ["node"]}))

    def test_build_domains_entries_need_a_risk_class(self):
        with self.assertRaises(templates.TemplateError):
            templates._validate({"stack": "x", "build-domains": [{"name": "a.b"}]}, "x")
        with self.assertRaises(templates.TemplateError):
            templates._validate({"stack": "x", "build-domains": "github.com"}, "x")


# ------------------------------------------------------------ budowa: lista domen do prawdziwego proxy (#5)

FAKE_STACK = """#!/bin/bash
source "${BASH_SOURCE[0]%/*}/lib.sh"
echo "box[$TIDE_FAKE_BOX] stack ${BASH_SOURCE[0]##*/} HTTPS_PROXY=$HTTPS_PROXY box_proxy=$TIDE_BOX_PROXY \
box_no_proxy=$TIDE_BOX_NO_PROXY node=${TIDE_NODE_VERSION:-} dotnet=${TIDE_DOTNET_SDK:-}" >> "$TIDE_FAKE_LOG"
"""


class RecordingProxy(bproxy.ProxyService):
    """Prawdziwe proxy z #5 (bez gniazda — polityka go nie wymaga), z kolejnością wywołań w dzienniku atrap."""

    def __init__(self):
        super().__init__("10.173.184.1", 18080, log=lambda _m: None)
        self.seen = []
        self.stopped = False

    def _log(self, line):
        with open(os.environ["TIDE_FAKE_LOG"], "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def set_box_policy(self, ip, domains, ollama, denied_log_path, **kw):
        policy = super().set_box_policy(ip, domains, ollama, denied_log_path, **kw)
        self.seen.append((ip, list(domains), ollama, denied_log_path, policy))
        self._log(f"set_box_policy {ip}")
        return policy

    def clear_box_policy(self, ip):
        super().clear_box_policy(ip)
        self._log(f"clear_box_policy {ip}")

    def stop(self):
        self.stopped = True


class StackBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-box-stacks-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = make_env(self.tmp)
        env.update({"TIDE_BOARD_DIR": os.path.join(self.tmp, "board"),
                    "XDG_STATE_HOME": os.path.join(self.tmp, "state")})
        self.env = env
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Prawdziwe szablony danych (`*.yaml`), atrapy skryptów (prawdziwe instalują pakiety jako root pudełka).
        self.scripts = os.path.join(self.tmp, "stacks")
        os.makedirs(self.scripts)
        shutil.copy(LIB, os.path.join(self.scripts, "lib.sh"))
        for stack in STACK_SCRIPTS:
            with open(os.path.join(self.scripts, f"{stack}.sh"), "w", encoding="utf-8") as fh:
                fh.write(FAKE_STACK)
        self.settings = BoxSettings(project="user-1000", ip_wait_seconds=0)
        self.proxy = RecordingProxy()
        self.fake_incus = os.path.join(self.tmp, "fakebin", "incus")

    def builder(self):
        return bb.Builder(self.settings, Incus("user-1000", binary=self.fake_incus), policy=self.proxy,
                          scripts_dir=self.scripts)

    def build(self, profile):
        bp.save_project("plasma-tide", profile)
        return self.builder().build("plasma-tide")

    def incus_calls(self):
        return [shlex.split(line)[1:] for line in calls(self.env) if line.startswith("incus ")]

    def test_build_domains_reach_the_real_proxy_before_the_first_exec(self):
        profile = {"tide-box": 1, "stacks": ["python-qt"], "network": {"domains": ["pypi.example.org"]},
                   "verify": ["true"]}
        self.build(profile)
        self.assertEqual(len(self.proxy.seen), 1)
        ip, domains, ollama, denied, policy = self.proxy.seen[0]
        want = [d["name"] for d in templates.load("base")["domains"]]
        want += [d["name"] for d in templates.load("python-qt")["domains"] if d["name"] not in want]
        want += ["pypi.example.org"]
        self.assertEqual(domains, want)
        self.assertEqual(domains, list(templates.build_domains(profile)))
        for host in ("pypi.org", "files.pythonhosted.org", "archive.ubuntu.com", "pypi.example.org"):
            self.assertTrue(policy.allows(host), host)
        for host in ("github.com", "registry-1.docker.io", "api.nuget.org"):
            self.assertFalse(policy.allows(host), host)
        self.assertFalse(ollama)
        self.assertEqual(denied, os.path.join(self.tmp, "state", "tide", "box", "plasma-tide", "build-denied.log"))
        self.assertEqual(os.stat(denied).st_mode & 0o777, 0o600)
        log = calls(self.env)
        first_exec = min(i for i, l in enumerate(log) if l.startswith("incus exec"))
        self.assertLess(log.index(f"set_box_policy {ip}"), first_exec)
        self.assertIsNone(self.proxy.policy(ip))

    def test_dotnet_docker_build_list_has_the_build_only_domains(self):
        profile = {"tide-box": 1, "stacks": ["dotnet", "docker"], "dotnet": {"sdk": ["8.0", "10.0"]},
                   "docker": {"enabled": True}}
        self.build(profile)
        _, domains, _, _, policy = self.proxy.seen[0]
        self.assertEqual(domains, list(templates.build_domains(profile)))
        for host in ("github.com", "release-assets.githubusercontent.com", "aka.ms", "api.nuget.org",
                     "registry-1.docker.io"):
            self.assertTrue(policy.allows(host), host)

    def test_every_stack_exec_gets_the_proxy_and_the_injected_values(self):
        profile = {"tide-box": 1, "stacks": ["node", "playwright", "dotnet", "docker", "python-qt"],
                   "node": {"version": "22"}, "dotnet": {"sdk": ["8.0"]}, "docker": {"enabled": True}}
        self.build(profile)
        argvs = self.incus_calls()
        pushes = [a for a in argvs if a[:2] == ["file", "push"]]
        targets = [next(x for x in a if x.startswith("plasma-tide/")).split("/", 1)[1] for a in pushes]
        self.assertEqual(targets, ["opt/tide/stacks/lib.sh", *(f"opt/tide/stacks/{s}.sh" for s in profile["stacks"])])
        first_stack = next(a for a in argvs if a[0] == "exec" and f"{bb.REMOTE_STACKS}/node.sh" in a)
        self.assertLess(argvs.index(pushes[0]), argvs.index(first_stack))
        execs = [a for a in argvs if a[0] == "exec"]
        for argv in execs:
            self.assertIn(f"TIDE_BOX_PROXY={PROXY}", argv)
            self.assertIn(f"TIDE_BOX_NO_PROXY={netenv.BOX_NO_PROXY}", argv)
            self.assertIn(f"HTTPS_PROXY={PROXY}", argv)
            self.assertIn(f"NO_PROXY={netenv.BOX_NO_PROXY}", argv)
            self.assertNotIn("--user", argv)
            self.assertFalse([a for a in argv if a.startswith("DOCKER_HOST=")])
        ran = [l for l in calls(self.env) if " stack " in l and l.startswith("box[")]
        self.assertEqual([l.split()[2] for l in ran], [f"{s}.sh" for s in profile["stacks"]])
        for line in ran:
            self.assertIn(f"box_proxy={PROXY} box_no_proxy={netenv.BOX_NO_PROXY} node=22 dotnet=8.0", line)
        # obrazy Dockera ściąga builder (docker pull), nie szablon
        pulls = [a for a in execs if "docker" in a and "pull" in a]
        self.assertEqual([a[-1] for a in pulls], list(templates.build_images(profile)))

    def test_missing_lib_fails_before_incus(self):
        os.remove(os.path.join(self.scripts, "lib.sh"))
        with self.assertRaisesRegex(bb.BuildError, "lib.sh"):
            self.build({"tide-box": 1, "stacks": ["node"]})
        self.assertEqual(self.incus_calls(), [])
        self.assertEqual(self.proxy.seen, [])

    def test_failed_stack_stops_the_build_without_snapshot(self):
        with open(os.path.join(self.scripts, "docker.sh"), "a", encoding="utf-8") as fh:
            fh.write("exit 4\n")
        with self.assertRaisesRegex(bb.BuildError, "szablon docker: kod 4"):
            self.build({"tide-box": 1, "stacks": ["docker"], "docker": {"enabled": True}})
        self.assertFalse([a for a in self.incus_calls() if a[0] == "snapshot"])
        self.assertEqual(self.proxy._policies, {})

    def test_cli_build_starts_its_own_proxy_and_stops_it(self):
        bp.save_project("plasma-tide", {"tide-box": 1, "stacks": ["python-qt"]})
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(bbase, "ensure_proxy", return_value=(self.proxy, True)) as ensure, \
                mock.patch.object(bb, "SCRIPTS_DIR", self.scripts):
            code = cli.main(["build", "plasma-tide"], out=out, err=err)
        self.assertEqual(code, 0, err.getvalue())
        ensure.assert_called_once()
        self.assertTrue(self.proxy.stopped)
        self.assertEqual(len(self.proxy.seen), 1)
        self.assertIn("migawka clean", out.getvalue())
        self.assertIn("build-denied.log", out.getvalue())

    def test_cli_build_without_proxy_is_exit_1_before_incus(self):
        bp.save_project("plasma-tide", {"tide-box": 1, "stacks": ["python-qt"]})
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(bbase, "ensure_proxy", side_effect=bb.BuildError("proxy Tide nie wstało")):
            code = cli.main(["build", "plasma-tide"], out=out, err=err)
        self.assertEqual(code, 1)
        self.assertIn("tide box build: proxy Tide nie wstało", err.getvalue())
        self.assertEqual(self.incus_calls(), [])


# ------------------------------------------------------------ skrypt próby na prawdziwym Incusie

class TrialScriptTest(unittest.TestCase):
    TRIAL = os.path.join(ROOT, "scripts", "box", "stacks-trial.sh")

    def run_trial(self, failure=""):
        # Cały skrypt próby, ale bez hostowego Incusa, Tablicy i instalowania pakietów.
        # Atrapy zwracają też prawidłowe dane kontroli po budowie, aby kod zakończenia
        # zależał od wskazanego błędu, a nie od przypadkowego braku wyniku innej kontroli.
        with tempfile.TemporaryDirectory() as folder:
            trial = os.path.join(folder, "scripts", "box", "stacks-trial.sh")
            os.makedirs(os.path.dirname(trial))
            shutil.copyfile(self.TRIAL, trial)
            body = r'''
git() { :; }
sleep() { :; }
date() {
    case "$1" in
        +%Y-%m-%dT%H%M%S) echo stamp ;;
        +%s.%N) echo 100.0 ;;
        *) echo trial ;;
    esac
}
python3() {
    if [[ $1 == -c ]]; then echo localhost; return; fi
    case "$3" in
        approve) [[ $FAIL_STAGE != approve ]] ;;
        build) [[ $FAIL_STAGE != build ]] ;;
        reset) [[ $FAIL_STAGE != reset ]] ;;
        destroy) [[ $FAIL_STAGE != cleanup ]] ;;
        status)
            if [[ $FAIL_STAGE == warning && $4 == tide-stacks-test ]]; then
                echo 'stan: zbudowane z ostrzeżeniem'
            else
                printf 'stan: clean\nhash: abc123\nfiltrowanie NIC: tak\n'
            fi ;;
    esac
}
incus() {
    case "$1" in
        image) echo tide-base ;;
        project) echo allow ;;
        exec)
            if [[ $* == *claude* && $FAIL_STAGE == check ]]; then return 7; fi
            if [[ $* == *'/root/pt192-poza-home'* && $* == *'test -e'* ]]; then return 1; fi
            if [[ $* == *'cat /home/agent/pt192-marker'* ]]; then echo pt192-stamp; fi
            if [[ $* == *'systemctl show docker'* ]]; then
                echo 'Environment=HTTP_PROXY=http://proxy HTTPS_PROXY=http://proxy NO_PROXY=localhost'
            fi ;;
    esac
    return 0
}
export -f git sleep date python3 incus
bash "$TRIAL"
'''
            env = {**os.environ, "TRIAL": trial, "FAIL_STAGE": failure,
                   "XDG_STATE_HOME": os.path.join(folder, "state"),
                   "TIDE_BOARD_DIR": os.path.join(folder, "board")}
            return subprocess.run(["bash", "-c", body], env=env, capture_output=True, text=True, timeout=20)

    def test_trial_success_exits_zero(self):
        proc = self.run_trial()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn(": fail", proc.stdout)

    def test_trial_failure_cannot_be_hidden_by_successful_cleanup(self):
        for stage in ("approve", "build", "check", "reset", "warning", "cleanup"):
            with self.subTest(stage=stage):
                proc = self.run_trial(stage)
                self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn(": fail", proc.stdout)

    def test_trial_profiles_pass_the_schema_and_use_the_stacks(self):
        text = read(self.TRIAL)
        profiles = dict(re.findall(r'profile_file ([a-z-]+) "(tide-box: 1\n.*?)"\)', text, re.S))
        self.assertEqual(sorted(profiles), ["plasma-tide", "tide-stacks-test"])
        a = bp.parse(profiles["plasma-tide"], "project")
        self.assertEqual(a["stacks"], ["python-qt"])
        self.assertTrue(a["verify"])
        b = bp.parse(profiles["tide-stacks-test"], "project")
        self.assertEqual((b["stacks"], b["dotnet"]["sdk"], b["docker"]["enabled"]), (["dotnet", "docker"], ["8.0"], True))
        for profile in (a, b):
            self.assertEqual(bp.red_items(profile, templates.known_domains()), [])

    def test_trial_script_syntax_and_cleanup(self):
        proc = subprocess.run(["bash", "-n", self.TRIAL], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = read(self.TRIAL)
        self.assertIn("trap cleanup EXIT", text)
        self.assertIn('tide destroy "$slug" --yes', text)
        self.assertIn("restricted.snapshots", text)
        self.assertNotRegex(text, r"(~|\$HOME)/\.(claude|codex)")


# ------------------------------------------------------------ dokumentacja

class DocsTest(unittest.TestCase):
    def test_tide_daemon_doc_describes_the_stack_templates(self):
        text = read(os.path.join(ROOT, "docs", "tide-daemon.md"))
        for phrase in ("### Szablony stosów w pudełku (PT-192)", "STACKS_VERSION", "lib.sh", "build-domains",
                       "docker.service.d/proxy.conf", "runc 1.2.6", "NuGet.Config", "CHROME_BIN",
                       "QT_QPA_PLATFORM=offscreen", "właściciel"):
            self.assertIn(phrase, text)
        self.assertNotIn("dziś zawsze odmawia", text)


if __name__ == "__main__":
    unittest.main()
