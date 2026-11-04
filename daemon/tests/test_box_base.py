"""Obraz bazowy Tide (PT-179, #16): base.sh, tide-step, `tide box base`, `tide box login` i `make box-base`.

Bez Incusa: `tide box base` biegnie na atrapie `incus` (fake_incus.py) ze skryptem base.sh z fixture'a, który tylko
zapisuje krok — prawdziwy base.sh nigdy nie jest wykonywany jako całość. Jego funkcje tworzące pliki są sprawdzane
po `source` w bashu, z katalogiem tymczasowym jako katalogiem głównym.
"""
import io
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import agents
from tide_daemon.box import base as bbase
from tide_daemon.box import builder as bb
from tide_daemon.box import cli
from tide_daemon.box import k3
from tide_daemon.box import netenv
from tide_daemon.box import proxy as bproxy
from tide_daemon.box import state as bs
from tide_daemon.box import templates
from tide_daemon.box.incus import Incus
from tide_daemon.box.settings import BoxSettings

from .fakebin import calls, make_env

DAEMON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(DAEMON)
STACKS = os.path.join(DAEMON, "tide_daemon", "box", "stacks")
BASE_SH = os.path.join(STACKS, "base.sh")
TIDE_STEP = os.path.join(STACKS, "tide-step.sh")
PROXY = "http://10.173.184.1:18080"

FAKE_BASE_SH = """#!/bin/bash
echo "box[$TIDE_FAKE_BOX] base.sh $1 HTTPS_PROXY=$HTTPS_PROXY NO_PROXY=$NO_PROXY box_proxy=$TIDE_BOX_PROXY \
box_no_proxy=$TIDE_BOX_NO_PROXY uid=$TIDE_AGENT_UID claude=$TIDE_CLAUDE_VERSION codex=$TIDE_CODEX_VERSION" \
>> "$TIDE_FAKE_LOG"
[[ "$1" != "${TIDE_FAIL_STEP:-}" ]]
"""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class RecordingProxy(bproxy.ProxyService):
    """Prawdziwe proxy z #5 (nieuruchomione — polityka nie wymaga gniazda) zapisujące kolejność wywołań
    do dziennika atrap, żeby dało się ją porównać z `incus exec`."""

    def __init__(self):
        super().__init__("10.173.184.1", 18080, log=lambda _m: None)
        self.seen = []

    def set_box_policy(self, ip, domains, ollama, denied_log_path, **kw):
        policy = super().set_box_policy(ip, domains, ollama, denied_log_path, **kw)
        self.seen.append((ip, list(domains), ollama, denied_log_path, policy))
        with open(os.environ["TIDE_FAKE_LOG"], "a", encoding="utf-8") as fh:
            fh.write(f"set_box_policy {ip}\n")
        return policy

    def clear_box_policy(self, ip):
        super().clear_box_policy(ip)
        with open(os.environ["TIDE_FAKE_LOG"], "a", encoding="utf-8") as fh:
            fh.write(f"clear_box_policy {ip}\n")


# ------------------------------------------------------------ base.sh: tekst skryptu

class BaseScriptTextTest(unittest.TestCase):
    def setUp(self):
        self.text = read(BASE_SH)
        self.code = "\n".join(line for line in self.text.splitlines() if not line.lstrip().startswith("#"))

    def test_strict_mode_and_syntax(self):
        self.assertIn("\nset -euo pipefail\n", self.text)
        self.assertIn("\nset -uo pipefail\n", read(TIDE_STEP))
        for script in (BASE_SH, TIDE_STEP):
            proc = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck niedostępny")
    def test_shellcheck(self):
        proc = subprocess.run(["shellcheck", "-s", "bash", BASE_SH, TIDE_STEP], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_no_pipe_to_shell_and_urls_only_to_base_template_domains(self):
        self.assertNotRegex(self.code, r"\|\s*(sudo\s+)?(ba|z|da)?sh\b")
        base = {d["name"] for d in templates.load("base")["domains"]}
        hosts = re.findall(r"https?://([A-Za-z0-9.-]+)", self.code)
        self.assertEqual(hosts, ["claude.ai"])
        for host in hosts:
            self.assertIn(host, base, f"{host}: domena spoza szablonu base")
        # źródła apt przepisywane wyłącznie dla domen apt z szablonu
        self.assertIn(r"s#http://(archive|security)\.ubuntu\.com#https://\1.ubuntu.com#g", self.code)

    def test_installer_domains_from_p6_are_in_the_base_template(self):
        base = {d["name"] for d in templates.load("base")["domains"]}
        for domain in ("claude.ai", "downloads.claude.ai", "registry.npmjs.org", "archive.ubuntu.com",
                       "security.ubuntu.com"):
            self.assertIn(domain, base)

    def test_agent_versions_are_pinned_and_injected(self):
        self.assertNotRegex(self.code.lower(), r"\blatest\b|\bstable\b")
        self.assertIn('bash "$work/install.sh" "$TIDE_CLAUDE_VERSION"', self.code)
        self.assertIn('"@openai/codex@$TIDE_CODEX_VERSION"', self.code)
        self.assertNotRegex(self.code, r"@openai/codex(?!@)")
        self.assertNotRegex(self.code, r"\b\d+\.\d+\.\d+\b", "wersje wstrzykuje usługa (box/agents.py)")
        self.assertTrue(agents.pinned(agents.CLAUDE_VERSION) and agents.pinned(agents.CODEX_VERSION))
        for bad in ("latest", "stable", "2.1", "^2.1.0", "2.1.317-beta", ""):
            self.assertFalse(agents.pinned(bad), bad)

    def test_no_host_agent_directories_and_no_dockerd(self):
        for script in (self.text, read(TIDE_STEP)):
            self.assertNotRegex(script, r"(~|\$HOME|\$\{HOME\}|/home/[A-Za-z0-9_]+|/root)/\.(claude|codex)\b")
            self.assertNotRegex(script, r"\.credentials\.json|auth\.json")
            self.assertNotIn("docker", script.lower())

    def test_no_proxy_value_is_not_written_in_the_script(self):
        self.assertNotIn(netenv.BOX_NO_PROXY, self.text)
        self.assertNotIn("172.16.0.0/12", self.text)

    def test_steps_match_the_service_order(self):
        steps = re.search(r"^STEPS=\(([^)]*)\)", self.text, re.M).group(1).split()
        self.assertEqual(tuple(steps), bbase.STEPS)
        for step in steps:
            self.assertRegex(self.text, rf"(?m)^step_{step.replace('-', '_')}\(\) \{{")


# ------------------------------------------------------------ base.sh: pliki konfiguracji po `source`

class BaseScriptFilesTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-base-root-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def bash(self, body, env=None, check=True):
        script = f"source {shlex.quote(BASE_SH)}\n{body}\n"
        full = {"PATH": "/usr/bin:/bin", "TIDE_BOX_PROXY": PROXY, "TIDE_BOX_NO_PROXY": netenv.BOX_NO_PROXY,
                "HTTPS_PROXY": PROXY, "HTTP_PROXY": PROXY, **(env or {})}
        proc = subprocess.run(["bash", "-c", script], env=full, capture_output=True, text=True)
        if check:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def path(self, rel):
        return os.path.join(self.root, rel)

    def test_environment_has_proxy_on_bridge_and_no_proxy_from_the_constant(self):
        os.makedirs(self.path("etc"))
        with open(self.path("etc/environment"), "w", encoding="utf-8") as fh:
            fh.write('PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"\nHTTP_PROXY="http://stare:1"\n')
        self.bash(f"write_environment {shlex.quote(self.root)}")
        values = {}
        for line in read(self.path("etc/environment")).splitlines():
            key, _, value = line.partition("=")
            values.setdefault(key, []).append(value.strip('"'))
        self.assertEqual(values["PATH"], ["/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"])
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.assertEqual(values[key], [PROXY], key)
        for key in ("NO_PROXY", "no_proxy"):
            self.assertEqual(values[key], [netenv.BOX_NO_PROXY], key)
        self.assertEqual(values["DISABLE_AUTOUPDATER"], ["1"])
        profile = read(self.path("etc/profile.d/tide-proxy.sh"))
        self.assertIn(f'export NO_PROXY="{netenv.BOX_NO_PROXY}"', profile)
        self.assertIn(f'export HTTPS_PROXY="{PROXY}"', profile)
        user = read(self.path("etc/systemd/user.conf.d/90-tide-proxy.conf"))
        self.assertIn("[Manager]", user)
        self.assertIn(f'"NO_PROXY={netenv.BOX_NO_PROXY}"', user)
        self.assertIn(f'"HTTPS_PROXY={PROXY}"', user)
        # drugi zapis nie dubluje wierszy
        self.bash(f"write_environment {shlex.quote(self.root)}")
        self.assertEqual(read(self.path("etc/environment")).count("NO_PROXY="), 1)

    def test_environment_matches_what_the_builder_injects(self):
        env = netenv.proxy_env(PROXY)
        self.bash(f"write_environment {shlex.quote(self.root)}", env={"TIDE_BOX_NO_PROXY": env["NO_PROXY"]})
        text = read(self.path("etc/environment"))
        for key, value in env.items():
            self.assertIn(f'{key}="{value}"', text)

    def test_apt_proxy_and_https_sources(self):
        os.makedirs(self.path("etc/apt/sources.list.d"))
        with open(self.path("etc/apt/sources.list"), "w", encoding="utf-8") as fh:
            fh.write("deb http://archive.ubuntu.com/ubuntu noble main universe\n"
                     "deb http://security.ubuntu.com/ubuntu noble-security main\n")
        with open(self.path("etc/apt/sources.list.d/ubuntu.sources"), "w", encoding="utf-8") as fh:
            fh.write("Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\nSuites: noble\n")
        self.bash(f"https_sources {shlex.quote(self.root)}; write_apt_proxy {shlex.quote(self.root)}")
        classic = read(self.path("etc/apt/sources.list"))
        self.assertNotIn("http://", classic)
        self.assertIn("deb https://security.ubuntu.com/ubuntu noble-security main", classic)
        self.assertIn("URIs: https://archive.ubuntu.com/ubuntu", read(self.path("etc/apt/sources.list.d/ubuntu.sources")))
        conf = read(self.path("etc/apt/apt.conf.d/90tide-proxy"))
        self.assertIn(f'Acquire::http::Proxy "{PROXY}";', conf)
        self.assertIn(f'Acquire::https::Proxy "{PROXY}";', conf)

    def test_foreign_http_source_and_missing_sources_are_errors(self):
        os.makedirs(self.path("etc/apt"))
        proc = self.bash(f"https_sources {shlex.quote(self.root)}", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("brak źródeł apt", proc.stderr)
        with open(self.path("etc/apt/sources.list"), "w", encoding="utf-8") as fh:
            fh.write("deb http://ppa.example.org/x noble main\n")
        proc = self.bash(f"https_sources {shlex.quote(self.root)}", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("http://", proc.stderr)

    def test_bwrap_profile_is_the_hosts_profile(self):
        self.bash(f"write_bwrap_profile {shlex.quote(self.root)}")
        import importlib.util
        spec = importlib.util.spec_from_file_location("sandbox_check", os.path.join(ROOT, "scripts", "sandbox", "check.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def rules(text):
            return [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(rules(read(self.path("etc/apparmor.d/bwrap-userns-restrict"))), rules(mod.PROFILE))

    def test_bad_inputs_are_refused(self):
        for env, what in (({"TIDE_BOX_PROXY": "http://proxy.example.org:1"}, "TIDE_BOX_PROXY"),
                          ({"TIDE_BOX_PROXY": ""}, "TIDE_BOX_PROXY"),
                          ({"TIDE_BOX_NO_PROXY": "a b"}, "TIDE_BOX_NO_PROXY"),
                          ({"TIDE_BOX_NO_PROXY": 'x";touch /tmp/zle;"'}, "TIDE_BOX_NO_PROXY"),
                          ({"HTTPS_PROXY": ""}, "bez proxy"),
                          ({"HTTP_PROXY": "http://10.0.0.9:1"}, "bez proxy")):
            proc = self.bash("check_proxy", env=env, check=False)
            self.assertNotEqual(proc.returncode, 0, env)
            self.assertIn(what, proc.stderr)
        for version in ("latest", "", "2.1"):
            proc = self.bash(f"check_pinned {shlex.quote(version)} Codex", check=False)
            self.assertNotEqual(proc.returncode, 0, version)
            self.assertIn("nie jest przypięta", proc.stderr)
        self.bash("check_pinned 0.166.0 Codex")

    def test_main_refuses_unknown_step_and_non_root(self):
        for args, code, what in ((["nic"], 2, "użycie"), (["sources", "packages"], 2, "użycie"),
                                 (["sourc.s"], 2, "użycie"), (["sources"], 1, "root pudełka")):
            env = {"PATH": "/usr/bin:/bin", "TIDE_BOX_PROXY": PROXY, "TIDE_BOX_NO_PROXY": netenv.BOX_NO_PROXY,
                   "HTTPS_PROXY": PROXY, "HTTP_PROXY": PROXY}
            if os.geteuid() == 0 and code == 1:
                continue
            proc = subprocess.run(["bash", BASE_SH, *args], env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, code, proc.stderr)
            self.assertIn(what, proc.stderr)


# ------------------------------------------------------------ tide-step

class TideStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-step-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env_file = os.path.join(self.tmp, "env")
        self.status = os.path.join(self.tmp, "exit")

    def run_step(self, env_text, *command):
        with open(self.env_file, "w", encoding="utf-8") as fh:
            fh.write(env_text)
        os.chmod(self.env_file, 0o600)
        return subprocess.run(["bash", TIDE_STEP, self.env_file, self.status, *command],
                              env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True)

    def test_env_file_is_data_and_exit_code_is_saved(self):
        marker = os.path.join(self.tmp, "nie-wolno")
        proc = self.run_step(f'# komentarz\nFOO=bar baz\nQUOTED="a b"\nEVIL=$(touch {marker})\n\n',
                             "sh", "-c", 'printf "%s|%s|%s" "$FOO" "$QUOTED" "$EVIL"; exit 3')
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout, f"bar baz|a b|$(touch {marker})")
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(read(self.status), "3\n")
        self.assertEqual(os.stat(self.status).st_mode & 0o777, 0o600)

    def test_bad_line_and_usage(self):
        proc = self.run_step("1ZLY=x\n", "true")
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(os.path.exists(self.status))
        proc = subprocess.run(["bash", TIDE_STEP, self.env_file], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("użycie", proc.stderr)


# ------------------------------------------------------------ tide box base na atrapie

class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-box-base-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = make_env(self.tmp)
        env.update({"TIDE_BOARD_DIR": os.path.join(self.tmp, "board"),
                    "XDG_STATE_HOME": os.path.join(self.tmp, "state")})
        self.env = env
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.scripts = os.path.join(self.tmp, "stacks")
        os.makedirs(self.scripts)
        with open(os.path.join(self.scripts, "base.sh"), "w", encoding="utf-8") as fh:
            fh.write(FAKE_BASE_SH)
        shutil.copy(TIDE_STEP, os.path.join(self.scripts, "tide-step.sh"))
        self.fonts = os.path.join(self.env["XDG_DATA_HOME"], "fonts")
        os.makedirs(os.path.join(self.fonts, "sub"))
        for rel in ("Inter.ttf", "sub/Mono.OTF", "readme.txt"):
            with open(os.path.join(self.fonts, rel), "wb") as fh:
                fh.write(b"font " + rel.encode())
        os.symlink("/etc/passwd", os.path.join(self.fonts, "link.ttf"))
        self.settings = BoxSettings(project="user-1000", ip_wait_seconds=0)
        self.proxy = RecordingProxy()
        self.fake_incus = os.path.join(self.tmp, "fakebin", "incus")

    def base_builder(self, policy="default"):
        return bbase.BaseBuilder(self.settings, Incus("user-1000", binary=self.fake_incus),
                                 policy=self.proxy if policy == "default" else policy, scripts_dir=self.scripts)

    def incus_calls(self):
        return [shlex.split(line)[1:] for line in calls(self.env) if line.startswith("incus ")]

    def fake_state(self):
        import json
        with open(os.path.join(self.env["TIDE_FAKE_INCUS_STATE"], "state.json"), encoding="utf-8") as fh:
            return json.load(fh)


class BaseBuildTest(BaseCase):
    def test_order_of_steps_proxy_snapshot_and_publish(self):
        res = self.base_builder().build()
        log = calls(self.env)

        def index(pred, what):
            for i, line in enumerate(log):
                if pred(line):
                    return i
            self.fail(f"brak w dzienniku: {what}\n" + "\n".join(log))

        order = [
            index(lambda l: l.startswith("incus init --project user-1000 images:ubuntu/24.04 tide-base"), "init"),
            index(lambda l: "config device override --project user-1000 tide-base eth0" in l, "nic"),
            index(lambda l: l.startswith("incus start --project user-1000 tide-base"), "start"),
            index(lambda l: l.startswith("set_box_policy"), "policy"),
            index(lambda l: "file push" in l and "tide-base/opt/tide/stacks/base.sh" in l, "push base.sh"),
            index(lambda l: "file push" in l and "tide-base/opt/tide/stacks/tide-step.sh" in l, "push tide-step"),
            index(lambda l: "file push" in l and "tide-base/opt/tide/fonts.tar" in l, "push fonts"),
            *[index(lambda l, s=s: f"base.sh {s} " in l, s) for s in bbase.STEPS],
            index(lambda l: "config set --project user-1000 tide-base 'user.tide.agents=" in l, "agents"),
            index(lambda l: "snapshot create --project user-1000 tide-base clean" in l, "snapshot"),
            index(lambda l: l.startswith("incus publish --project user-1000 tide-base/clean --alias tide-base"),
                  "publish"),
            index(lambda l: l.startswith("incus stop --project user-1000 tide-base"), "stop"),
            index(lambda l: l.startswith("clear_box_policy"), "clear"),
        ]
        self.assertEqual(order, sorted(order), "\n".join(log))
        self.assertEqual(res.steps, ["init", "nic", "push", *bbase.STEPS, "snapshot", "publish"])
        self.assertEqual(self.fake_state()["images"], {"tide-base": {"source": "tide-base/clean"}})
        self.assertEqual([s["name"] for s in self.fake_state()["instances"]["tide-base"]["snapshots"]], ["clean"])
        self.assertEqual(self.fake_state()["instances"]["tide-base"]["config"]["user.tide.agents"],
                         agents.AGENTS_VERSION)
        self.assertFalse(any("security.privileged" in " ".join(a) for a in self.incus_calls()))

    def test_every_exec_goes_through_the_proxy_as_root_with_pinned_versions(self):
        self.base_builder().build()
        execs = [a for a in self.incus_calls() if a[0] == "exec"]
        self.assertEqual(len(execs), len(bbase.STEPS))
        for argv in execs:
            self.assertIn(f"HTTPS_PROXY={PROXY}", argv)
            self.assertIn(f"HTTP_PROXY={PROXY}", argv)
            self.assertIn(f"NO_PROXY={netenv.BOX_NO_PROXY}", argv)
            self.assertIn(f"TIDE_BOX_NO_PROXY={netenv.BOX_NO_PROXY}", argv)
            self.assertIn("-T", argv)
            self.assertNotIn("--user", argv)
        for line in (l for l in calls(self.env) if "base.sh " in l and l.startswith("box[")):
            self.assertIn(f"claude={agents.CLAUDE_VERSION} codex={agents.CODEX_VERSION}", line)
            self.assertIn(f"box_proxy={PROXY} box_no_proxy={netenv.BOX_NO_PROXY} uid=1000", line)

    def test_build_domains_go_to_the_real_proxy_before_the_first_exec(self):
        res = self.base_builder().build()
        self.assertEqual(len(self.proxy.seen), 1)
        ip, domains, ollama, denied, policy = self.proxy.seen[0]
        self.assertEqual(domains, list(templates.build_domains({})))
        self.assertEqual(domains, [d["name"] for d in templates.load("base")["domains"]])
        for domain in ("claude.ai", "downloads.claude.ai", "registry.npmjs.org"):
            self.assertTrue(policy.allows(domain), domain)
        for domain in ("api.anthropic.com", "chatgpt.com", "github.com", "evil.claude.ai.example.org"):
            self.assertFalse(policy.allows(domain), domain)
        self.assertFalse(ollama)
        self.assertFalse(policy.full)
        self.assertTrue(ip.startswith("10.173.184."))
        self.assertEqual(denied, os.path.join(self.tmp, "state", "tide", "box", "tide-base", "base-denied.log"))
        self.assertEqual(res.denied_log, denied)
        self.assertEqual(os.stat(denied).st_mode & 0o777, 0o600)
        log = calls(self.env)
        first_exec = min(i for i, l in enumerate(log) if l.startswith("incus exec"))
        self.assertLess(log.index(f"set_box_policy {ip}"), first_exec)
        self.assertIsNone(self.proxy.policy(ip))        # zdjęta po budowie

    def test_fonts_tar_holds_only_regular_font_files(self):
        res = self.base_builder().build()
        self.assertEqual(res.fonts, 2)
        pushed = os.path.join(self.env["TIDE_FAKE_INCUS_STATE"], "files", "tide-base", "opt", "tide", "fonts.tar")
        with tarfile.open(pushed) as tar:
            members = tar.getmembers()
        self.assertEqual(sorted(m.name for m in members), ["Inter.ttf", "sub/Mono.OTF"])
        for m in members:
            self.assertTrue(m.isfile())
            self.assertEqual((m.uid, m.gid, m.mode), (0, 0, 0o644))

    def test_no_fonts_dir_gives_an_empty_archive(self):
        target = os.path.join(self.tmp, "x.tar")
        self.assertEqual(bbase.fonts_tar(os.path.join(self.tmp, "brak"), target), 0)
        with tarfile.open(target) as tar:
            self.assertEqual(tar.getmembers(), [])
        self.assertEqual(bbase.host_fonts_dir({"HOME": "/h"}), "/h/.local/share/fonts")
        self.assertEqual(bbase.host_fonts_dir({"HOME": "/h", "XDG_DATA_HOME": "/d"}), "/d/fonts")

    def test_rebuild_replaces_the_instance_and_reuses_the_alias(self):
        self.base_builder().build()
        self.base_builder().build()
        argvs = self.incus_calls()
        self.assertIn(["delete", "--project", "user-1000", "tide-base", "--force"], argvs)
        self.assertEqual(sum(1 for a in argvs if a[0] == "publish"), 2)

    def test_failed_step_is_an_error_without_snapshot_and_policy_is_cleared(self):
        with mock.patch.dict(os.environ, {"TIDE_FAIL_STEP": "claude"}):
            with self.assertRaisesRegex(bb.BuildError, "base.sh claude: kod 1"):
                self.base_builder().build()
        argvs = self.incus_calls()
        self.assertFalse([a for a in argvs if a[0] in ("publish", "snapshot")])
        self.assertFalse([l for l in calls(self.env) if "base.sh codex" in l])
        self.assertEqual(self.proxy._policies, {})
        self.assertEqual(argvs[-1][:2], ["stop", "--project"])   # nieudana budowa zatrzymuje tide-base
        self.assertIn("tide-base", argvs[-1])

    def test_without_proxy_nothing_reaches_incus(self):
        with self.assertRaisesRegex(bb.BuildError, "proxy"):
            self.base_builder(policy=bb.ProxyMissing()).build()
        with mock.patch("tide_daemon.box.proxy.running", return_value=None):
            b = bbase.BaseBuilder(self.settings, Incus("user-1000", binary=self.fake_incus), scripts_dir=self.scripts)
        with self.assertRaisesRegex(bb.BuildError, "budowa bez proxy"):
            b.build()
        self.assertEqual(self.incus_calls(), [])

    def test_missing_script_or_unpinned_version_fails_before_incus(self):
        os.remove(os.path.join(self.scripts, "tide-step.sh"))
        with self.assertRaisesRegex(bb.BuildError, "tide-step.sh"):
            self.base_builder().build()
        shutil.copy(TIDE_STEP, os.path.join(self.scripts, "tide-step.sh"))
        with mock.patch.object(agents, "CODEX_VERSION", "latest"):
            with self.assertRaisesRegex(bb.BuildError, "nie jest przypięta"):
                self.base_builder().build()
        self.assertEqual(self.incus_calls(), [])

    def test_real_scripts_are_in_the_recipe_hash(self):
        self.assertTrue(os.path.isfile(os.path.join(bb.SCRIPTS_DIR, "base.sh")))
        before = bb.templates_version(self.scripts)
        with open(os.path.join(self.scripts, "base.sh"), "a", encoding="utf-8") as fh:
            fh.write("# zmiana\n")
        self.assertNotEqual(bb.templates_version(self.scripts), before)


class BaseCliTest(BaseCase):
    def run_cli(self, *argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(list(argv), out=out, err=err, **kw)
        return code, out.getvalue(), err.getvalue()

    def test_base_with_k3_needs_no_login(self):
        code, out, err = self.run_cli("base", base_builder=self.base_builder())
        self.assertEqual(code, 0, err)
        self.assertIn("obraz bazowy tide-base", out)
        self.assertIn("base-denied.log", out)
        self.assertIn("niepotrzebne", out)
        self.assertNotIn(bbase.LOGIN_HINT, out)

    def test_base_without_agent_login_ends_with_the_login_hint_not_an_error(self):
        with mock.patch.dict(k3.K3_PROGRAMS, {"claude": False, "codex": False}):
            code, out, err = self.run_cli("base", base_builder=self.base_builder())
        self.assertEqual(code, 0, err)
        self.assertIn("zaloguj: tide box login", out)
        self.assertIn("claude, codex", out)

    def test_base_error_is_exit_1(self):
        code, _, err = self.run_cli("base", base_builder=self.base_builder(policy=bb.ProxyMissing()))
        self.assertEqual(code, 1)
        self.assertIn("tide box base: proxy Tide", err)

    def test_cli_base_starts_its_own_proxy_and_stops_it(self):
        started = []

        class Service:
            listen, port = "10.173.184.1", 18080
            stopped = False

            def stop(self):
                Service.stopped = True

        def start(settings):
            started.append(settings)
            return Service()

        with mock.patch("tide_daemon.box.proxy.running", return_value=None):
            service, own = bbase.ensure_proxy(BoxSettings(project="user-1000", proxy_port=18555), start=start)
        self.assertTrue(own)
        self.assertTrue(started[0].enabled)
        self.assertEqual(started[0].proxy_port, 18555)
        running = RecordingProxy()
        with mock.patch("tide_daemon.box.proxy.running", return_value=running):
            self.assertEqual(bbase.ensure_proxy(self.settings, start=start), (running, False))

        def busy(_settings):
            raise bproxy.ProxyError("port zajęty")

        with mock.patch("tide_daemon.box.proxy.running", return_value=None):
            with self.assertRaisesRegex(bb.BuildError, "port 18080 jest zajęty"):
                bbase.ensure_proxy(self.settings, start=busy)
            with self.assertRaisesRegex(bb.BuildError, "bez proxy"):
                bbase.ensure_proxy(self.settings, start=lambda _s: None)

    def test_cli_base_without_bridge_fails_before_incus(self):
        # Prawdziwy start_service: atrapa incus jest w PATH, ale mostka incusbr0 atrapa nie zna.
        with mock.patch("tide_daemon.box.proxy.bridge_address", return_value=None):
            code, _, err = self.run_cli("base")
        self.assertEqual(code, 1)
        self.assertIn("proxy Tide nie wstało", err)
        self.assertFalse([a for a in self.incus_calls() if a[0] != "network"])


class LoginTest(BaseCase):
    def test_k3_programs_are_skipped(self):
        out = io.StringIO()
        self.assertEqual(k3.login_programs(), ())
        self.assertEqual(bbase.login(self.settings, "plasma-tide", out), 0)
        self.assertIn("nic do zalogowania", out.getvalue())
        self.assertEqual(self.incus_calls(), [])
        code = cli.main(["login"], out=io.StringIO(), err=io.StringIO())
        self.assertEqual(code, 0)

    def test_k1_login_opens_incus_exec_as_agent_in_the_project_box(self):
        inc = Incus("user-1000", binary=self.fake_incus)
        inc.init("tide-base", "plasma-tide")
        seen = []

        def run(argv):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0)

        out = io.StringIO()
        code = bbase.login(self.settings, "plasma-tide", out, incus=inc, policy=self.proxy, run=run,
                           programs=("claude", "codex"))
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 2)
        for argv, program in zip(seen, ("claude", "codex")):
            self.assertEqual(argv[:6], [self.fake_incus, "exec", "--project", "user-1000", "plasma-tide", "-t"])
            self.assertEqual(argv[argv.index("--user") + 1], "1000")
            self.assertEqual(argv[argv.index("--cwd") + 1], "/home/agent")
            self.assertIn("HOME=/home/agent", argv)
            self.assertIn(f"HTTPS_PROXY={PROXY}", argv)
            self.assertEqual(argv[argv.index("--") + 1:], [program, "login"])
        self.assertIn(["start", "--project", "user-1000", "plasma-tide"], self.incus_calls())
        ip, domains = self.proxy.seen[0][0], self.proxy.seen[0][1]
        self.assertIn("api.anthropic.com", domains)
        self.assertIsNone(self.proxy.policy(ip))

    def test_k1_login_needs_a_slug_and_an_existing_box(self):
        with self.assertRaisesRegex(bb.BuildError, "podaj projekt"):
            bbase.login(self.settings, None, io.StringIO(), policy=self.proxy, programs=("claude",))
        with self.assertRaisesRegex(bb.BuildError, "nie istnieje"):
            bbase.login(self.settings, "plasma-tide", io.StringIO(), incus=Incus("user-1000", binary=self.fake_incus),
                        policy=self.proxy, programs=("claude",))

    def test_k1_login_refuses_while_steps_run_in_the_box(self):
        inc = Incus("user-1000", binary=self.fake_incus)
        inc.init("tide-base", "plasma-tide")
        before = len(self.incus_calls())
        bs.record("plasma-tide", "box.use", bs.box_name("plasma-tide"), step="K-1-PT-1-1")
        with self.assertRaisesRegex(bb.BoxBusy, "K-1-PT-1-1"):
            bbase.login(self.settings, "plasma-tide", io.StringIO(), incus=inc, policy=self.proxy,
                        run=lambda argv: self.fail("logowanie przy biegnącym kroku"), programs=("claude",))
        self.assertEqual(self.proxy.seen, [])
        self.assertEqual(len(self.incus_calls()), before)


# ------------------------------------------------------------ make box-base i dokumentacja

class MakeAndDocsTest(unittest.TestCase):
    def test_make_box_base_calls_tide_box_base(self):
        text = read(os.path.join(ROOT, "mk", "box.mk"))
        self.assertRegex(text, r"(?m)^box-base:\n\t\$\(PYTHON\) daemon/tide.py box base$")
        proc = subprocess.run(["make", "-n", "-s", "box-base"], cwd=ROOT, capture_output=True, text=True,
                              env={**os.environ, "PYTHON": "python3"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "python3 daemon/tide.py box base")
        self.assertNotRegex(text, r"(?m)^box-base:.*\n\t.*sudo")

    def test_tide_daemon_doc_describes_the_base_image(self):
        text = read(os.path.join(ROOT, "docs", "tide-daemon.md"))
        for phrase in ("### Obraz bazowy Tide (PT-179)", "make box-base", "tide box login", "tide-base/clean",
                       "BOX_NO_PROXY", "/usr/local/bin/tide-step", "base-denied.log", "trwały"):
            self.assertIn(phrase, text)
        self.assertNotIn("niedostępne do #16", text)


if __name__ == "__main__":
    unittest.main()
