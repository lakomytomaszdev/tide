"""Budowa pudełka (PT-167): atrapa incus, adapter, builder, filtrowanie NIC, stan, kopia per karta i CLI tide box.

Wszystko na atrapie `incus` z fakebin.py — prawdziwy Incus nie jest potrzebny (ani dotykany). Tablica, stan
i ustawienia są tymczasowe.
"""
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import agents
from tide_daemon.box import builder as bb
from tide_daemon.box import cli
from tide_daemon.box import netenv
from tide_daemon.box import profile as bp
from tide_daemon.box import state as bs
from tide_daemon.box import templates
from tide_daemon.box.incus import Incus
from tide_daemon.box import settings as bset
from tide_daemon.box.settings import BoxSettings

from .fakebin import calls, make_env

DAEMON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = "http://10.173.184.1:18080"
NIC_ON = ["security.mac_filtering=true", "security.ipv4_filtering=true", "security.ipv6_filtering=false"]

PROFILE = {
    "tide-box": 1, "project": "plasma-tide", "stacks": ["node"], "node": {"version": "20"},
    "apt": ["xvfb"], "setup": ["echo setup-ran > setup.txt"],
    "network": {"domains": ["fonts.example.org"], "host-ollama": True},
    "docker": {"enabled": True, "images": ["postgres:16"]},
    "verify": ["true"],
}

BASE_YAML = """stack: base
apt: [git]
domains:
  - name: archive.ubuntu.com
    risk: green
images: []
"""
NODE_YAML = """stack: node
apt: [nodejs]
domains:
  - name: registry.npmjs.org
    risk: green
images: [node:20]
"""


class RecordingPolicy:
    """Atrapa uruchomionego proxy (#5, `ProxyService`): adres nasłuchu i port jak z `proxy.running()`, wywołania
    zapisane do dziennika atrap (kolejność względem incus)."""

    def __init__(self, listen="10.173.184.1", port=18080):
        self.listen, self.port = listen, port
        self.calls = []
        self.cleared = []

    def _log(self, line):
        with open(os.environ["TIDE_FAKE_LOG"], "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def set_box_policy(self, ip, domains, ollama, denied_log_path):
        self.calls.append((ip, list(domains), ollama, denied_log_path))
        self._log(f"set_box_policy {ip} {','.join(domains)} {ollama} {denied_log_path}")

    def clear_box_policy(self, ip):
        self.cleared.append(ip)
        self._log(f"clear_box_policy {ip}")


class BoxCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-box-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = make_env(self.tmp)
        env.update({"TIDE_BOARD_DIR": os.path.join(self.tmp, "board"),
                    "XDG_STATE_HOME": os.path.join(self.tmp, "state")})
        self.env = env
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Szablony stosów z fixture'a (dane i skrypty powłoki) zamiast box/stacks.
        self.stacks = os.path.join(self.tmp, "stacks")
        os.makedirs(self.stacks)
        for name, body in (("base.yaml", BASE_YAML), ("node.yaml", NODE_YAML),
                           ("docker.yaml", "stack: docker\nimages: [testcontainers/ryuk:0.11.0]\n")):
            with open(os.path.join(self.stacks, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        with open(os.path.join(self.stacks, "node.sh"), "w", encoding="utf-8") as fh:
            fh.write('echo "box[$TIDE_FAKE_BOX] node.sh HTTPS_PROXY=$HTTPS_PROXY node=$TIDE_NODE_VERSION" '
                     '>> "$TIDE_FAKE_LOG"\n')
        with open(os.path.join(self.stacks, "lib.sh"), "w", encoding="utf-8") as fh:
            fh.write("# atrapa wspólnych funkcji szablonów\n")
        with open(os.path.join(self.stacks, "docker.sh"), "w", encoding="utf-8") as fh:
            fh.write("true\n")
        p = mock.patch.object(templates, "STACKS_DIR", self.stacks)
        p.start()
        self.addCleanup(p.stop)
        templates._cache.clear()
        self.addCleanup(templates._cache.clear)
        self.settings = BoxSettings(project="user-1000", ip_wait_seconds=0)
        self.policy = RecordingPolicy()
        self.fake_incus = os.path.join(self.tmp, "fakebin", "incus")

    def builder(self, settings=None, policy="default"):
        settings = settings or self.settings
        return bb.Builder(settings, Incus(settings.project, binary=self.fake_incus),
                          policy=self.policy if policy == "default" else policy, scripts_dir=self.stacks)

    def approve(self, profile=None):
        bp.save_project("plasma-tide", dict(profile or PROFILE))

    def incus_calls(self):
        return [shlex.split(line)[1:] for line in calls(self.env) if line.startswith("incus ")]

    def fake_state(self):
        with open(os.path.join(self.env["TIDE_FAKE_INCUS_STATE"], "state.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def write_state(self, data):
        with open(os.path.join(self.env["TIDE_FAKE_INCUS_STATE"], "state.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def run_cli(self, *argv, builder=None):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(list(argv), out=out, err=err, builder=builder)
        return code, out.getvalue(), err.getvalue()


class FakeIncusTest(BoxCase):
    def test_fake_records_argv_keeps_state_and_runs_exec_locally(self):
        inc = Incus("user-1000", binary=self.fake_incus)
        inc.launch("tide-base", "t1", {"limits.cpu": 2})
        self.assertEqual(inc.instance("t1")["status"], "Running")
        self.assertIsNone(inc.instance("t"))               # dokładna nazwa, nie przedrostek
        inc.config_set("t1", {"user.x": "1"})
        self.assertEqual(inc.config_show("t1")["config"]["user.x"], "1")
        inc.device_override("t1", "eth0", {"security.mac_filtering": True})
        self.assertEqual(inc.device_show("t1")["eth0"]["security.mac_filtering"], "true")
        inc.snapshot_create("t1", "clean")
        self.assertEqual([s["name"] for s in inc.snapshot_list("t1")], ["clean"])
        self.assertEqual(inc.info("t1")["name"], "t1")
        work = os.path.join(self.tmp, "work")
        os.makedirs(work)
        code = inc.exec("t1", ["bash", "-c", "echo $FOO > out.txt; pwd >> out.txt"], {"FOO": "bar"}, cwd=work)
        self.assertEqual(code, 0)
        with open(os.path.join(work, "out.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().split(), ["bar", work])
        inc.stop("t1")
        self.assertNotEqual(inc.exec("t1", ["true"]), 0)    # zatrzymane pudełko nie wykonuje poleceń
        inc.copy("t1/clean", "t2")
        self.assertEqual(inc.instance("t2")["status"], "Stopped")
        inc.delete("t1")
        self.assertIsNone(inc.instance("t1"))
        for argv in self.incus_calls():
            self.assertIn("--project", argv)
            self.assertEqual(argv[argv.index("--project") + 1], "user-1000")

    def test_in_box_programs_are_fakes_never_host_tools(self):
        inc = Incus("user-1000", binary=self.fake_incus)
        inc.launch("tide-base", "t1")
        self.assertEqual(inc.exec("t1", ["apt-get", "install", "-y", "x"], {"HTTPS_PROXY": PROXY}), 0)
        self.assertIn(f"box[t1] apt-get HTTPS_PROXY={PROXY} install -y x", calls(self.env))

    def test_adapter_passes_devnull_stdin_and_project(self):
        seen = []

        def run(argv, **kw):
            seen.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, "[]", "")

        inc = Incus("user-1000", binary="incus", run=run)
        inc.list()
        inc.exec("b", ["true"], {"A": "1"})
        inc.device_set("b", "eth0", {"k": True})
        for argv, kw in seen:
            self.assertIs(kw["stdin"], subprocess.DEVNULL)
            self.assertEqual(argv[argv.index("--project") + 1], "user-1000")
        self.assertEqual(seen[2][0][:6], ["incus", "config", "device", "set", "--project", "user-1000"])

    def test_tests_never_reach_real_incus(self):
        # Poza atrapą adapter wskazuje nieistniejący program (daemon/tests/__init__.py).
        with mock.patch.dict(os.environ, {"TIDE_INCUS_BIN": "/nonexistent/tide-tests/incus"}):
            self.assertEqual(Incus("p").binary, "/nonexistent/tide-tests/incus")
        self.assertEqual(shutil.which("incus", path=os.environ["PATH"]), self.fake_incus)


class BuildTest(BoxCase):
    def test_restricted_build_checks_zero_swap_before_stack(self):
        self.approve()
        self.builder().build("plasma-tide")
        argvs = self.incus_calls()
        init = next(a for a in argvs if a[0] == "init")
        self.assertFalse(any(a.startswith("limits.memory.swap=") for a in init))
        execs = [a for a in argvs if a[0] == "exec"]
        self.assertIn("/sys/fs/cgroup/memory.swap.max", execs[0])
        log = calls(self.env)
        first_exec = next(i for i, line in enumerate(log) if line.startswith("incus exec"))
        policy = next(i for i, line in enumerate(log) if line.startswith("set_box_policy"))
        stack = next(i for i, line in enumerate(log) if "node.sh HTTPS_PROXY=" in line)
        self.assertLess(policy, first_exec)
        self.assertLess(first_exec, stack)

    def test_swap_enabled_or_unreadable_aborts_before_provisioning(self):
        self.approve()
        for value in ("max", "1024", "missing", "", "invalid"):
            with self.subTest(swap=value), mock.patch.dict(os.environ, {"TIDE_FAKE_SWAP_MAX": value}):
                with self.assertRaisesRegex(bb.BuildError, "swap"):
                    self.builder().build("plasma-tide")
                state = self.fake_state()
                self.assertFalse(state["instances"]["plasma-tide"]["snapshots"])
                self.assertFalse(state.get("pushed"))
                self.assertEqual(len(self.policy.cleared), len(self.policy.calls))
                self.assertNotEqual(bs.journal_state("plasma-tide", "plasma-tide")[0], bs.CLEAN)

    def test_build_order_argv_and_proxy_on_every_exec(self):
        self.approve()
        res = self.builder().build("plasma-tide")
        log = calls(self.env)

        def index(pred, what):
            for i, line in enumerate(log):
                if pred(line):
                    return i
            self.fail(f"brak w dzienniku: {what}\n" + "\n".join(log))

        order = [
            index(lambda l: l.startswith("incus init --project user-1000 tide-base plasma-tide"), "init"),
            index(lambda l: "config device override --project user-1000 plasma-tide eth0" in l, "nic"),
            index(lambda l: l.startswith("incus start"), "start"),
            index(lambda l: l.startswith("set_box_policy"), "policy"),
            index(lambda l: "node.sh HTTPS_PROXY=" + PROXY + " node=20" in l, "stack node"),
            index(lambda l: "apt-get HTTPS_PROXY=" + PROXY + " update" in l, "apt update"),
            index(lambda l: "apt-get HTTPS_PROXY=" + PROXY + " install" in l, "apt install"),
            index(lambda l: "echo setup-ran" in l, "setup"),
            index(lambda l: "docker HTTPS_PROXY=" + PROXY + " pull node:20" in l, "docker pull"),
            index(lambda l: l.startswith("incus exec") and l.endswith("bash -c true"), "verify"),
            index(lambda l: "config set --project user-1000 plasma-tide 'user.tide.recipe=" in l, "stamp"),
            index(lambda l: "snapshot create --project user-1000 plasma-tide clean" in l, "snapshot"),
        ]
        self.assertEqual(order, sorted(order), "\n".join(log))
        self.assertIn("box[plasma-tide] docker HTTPS_PROXY=" + PROXY + " pull postgres:16", log)
        # apt: pakiety szablonów (base, node) i profilu
        apt = [l for l in log if "apt-get HTTPS_PROXY" in l and " install " in l][0]
        for pkg in ("git", "nodejs", "xvfb"):
            self.assertIn(pkg, apt.split())
        execs = [a for a in self.incus_calls() if a[0] == "exec"]
        self.assertGreaterEqual(len(execs), 7)
        for argv in execs:
            self.assertIn(f"HTTPS_PROXY={PROXY}", argv)
            self.assertIn("-T", argv)
            self.assertNotIn("--user", argv)              # budowa jako root pudełka
        # hash w migawce clean = profile.recipe_stamp (jedno źródło)
        snap = self.fake_state()["instances"]["plasma-tide"]["snapshots"][0]
        stamp = json.loads(snap["config"][bs.RECIPE_KEY])
        want = bp.recipe_stamp(PROFILE, bb.templates_version(self.stacks), agents.AGENTS_VERSION)
        self.assertEqual(stamp, want)
        self.assertEqual(res.hash, want["hash"])
        self.assertFalse(res.warning)
        self.assertEqual(bs.journal_state("plasma-tide", "plasma-tide"), (bs.CLEAN, False))

    def test_nic_filtering_set_before_start_and_never_privileged(self):
        self.approve()
        self.builder().build("plasma-tide")
        argvs = self.incus_calls()
        nic = [a for a in argvs if a[:3] == ["config", "device", "override"] and "eth0" in a]
        self.assertEqual(len(nic), 1)
        self.assertEqual(nic[0][-3:], NIC_ON)
        self.assertLess(argvs.index(nic[0]), argvs.index(["start", "--project", "user-1000", "plasma-tide"]))
        self.assertFalse(any("security.privileged" in " ".join(a) for a in argvs))
        dev = self.fake_state()["instances"]["plasma-tide"]["devices"]
        self.assertEqual(dev["eth0"]["security.mac_filtering"], "true")
        self.assertEqual(dev["home"], {"type": "disk", "pool": "tide", "source": "plasma-tide-home",
                                       "path": "/home/agent"})

    def test_ipv6_filtering_follows_settings(self):
        self.approve()
        settings = BoxSettings(project="user-1000", ipv6_filtering=True, ip_wait_seconds=0)
        self.builder(settings).build("plasma-tide")
        nic = [a for a in self.incus_calls() if a[:3] == ["config", "device", "override"] and "eth0" in a][0]
        self.assertIn("security.ipv6_filtering=true", nic)

    def test_policy_gets_build_domains_before_first_exec(self):
        self.approve()
        self.builder().build("plasma-tide")
        self.assertEqual(len(self.policy.calls), 1)
        ip, domains, ollama, denied = self.policy.calls[0]
        self.assertEqual(domains, list(templates.build_domains(PROFILE)))
        self.assertEqual(domains, ["archive.ubuntu.com", "registry.npmjs.org", "fonts.example.org"])
        self.assertTrue(ip.startswith("10.173.184."))
        self.assertTrue(ollama)
        self.assertEqual(denied, os.path.join(self.tmp, "state", "tide", "box", "plasma-tide", "build-denied.log"))
        log = calls(self.env)
        first_exec = min(i for i, l in enumerate(log) if l.startswith("incus exec"))
        self.assertLess(log.index([l for l in log if l.startswith("set_box_policy")][0]), first_exec)

    def test_proxy_without_bridge_address_fails_before_any_incus_call(self):
        self.approve()
        with self.assertRaisesRegex(bb.BuildError, "proxy"):
            self.builder(policy=RecordingPolicy(listen="")).build("plasma-tide")
        self.assertEqual(self.incus_calls(), [])

    def test_missing_proxy_service_fails_before_any_incus_call(self):
        self.approve()
        with mock.patch("tide_daemon.box.proxy.running", return_value=None):
            b = bb.Builder(self.settings, Incus("user-1000", binary=self.fake_incus), scripts_dir=self.stacks)
        self.assertIsInstance(b.policy, bb.ProxyMissing)
        with self.assertRaisesRegex(bb.BuildError, "proxy Tide \\(#5\\) nie jest uruchomione"):
            b.build("plasma-tide")
        self.assertEqual(self.incus_calls(), [])

    def test_running_proxy_is_the_single_source_of_address_and_policy(self):
        self.approve()
        service = RecordingPolicy(listen="10.99.0.1", port=18555)
        with mock.patch("tide_daemon.box.proxy.running", return_value=service):
            b = bb.Builder(self.settings, Incus("user-1000", binary=self.fake_incus), scripts_dir=self.stacks)
        b.build("plasma-tide")
        self.assertEqual(len(service.calls), 1)
        for argv in (a for a in self.incus_calls() if a[0] == "exec"):
            self.assertIn("HTTPS_PROXY=http://10.99.0.1:18555", argv)

    def test_build_policy_is_cleared_after_snapshot_and_on_failure(self):
        self.approve()
        self.builder().build("plasma-tide")
        self.assertEqual(self.policy.cleared, [self.policy.calls[0][0]])
        log = calls(self.env)
        snap = max(i for i, l in enumerate(log) if "snapshot create" in l)
        self.assertGreater(log.index(f"clear_box_policy {self.policy.cleared[0]}"), snap)
        self.approve({**PROFILE, "setup": ["exit 7"]})
        with self.assertRaises(bb.BuildError):
            self.builder().build("plasma-tide")
        self.assertEqual(len(self.policy.cleared), 2)

    def test_home_volume_belongs_to_agent(self):
        self.approve()
        self.builder(BoxSettings(project="user-1000", ip_wait_seconds=0, agent_uid=1001, agent_gid=1002)) \
            .build("plasma-tide")
        create = [a for a in self.incus_calls() if a[:3] == ["storage", "volume", "create"]]
        self.assertEqual(create, [["storage", "volume", "create", "--project", "user-1000", "tide", "plasma-tide-home",
                                   f"{bb.HOME_INIT_KEY}=pending"]])
        self.assertEqual(self.fake_state()["volume_config"]["tide/plasma-tide-home"],
                         {bb.HOME_INIT_KEY: "ready"})
        argvs = self.incus_calls()
        prepare = next(a for a in argvs if a[0] == "exec" and a[a.index("--") + 1] == "install")
        self.assertEqual(prepare[prepare.index("--") + 1:],
                         ["install", "-d", "-m", "0750", "-o", "1001", "-g", "1002", "/home/agent"])
        stack = next(a for a in argvs if a[0] == "exec" and f"{bb.REMOTE_STACKS}/node.sh" in a)
        self.assertLess(argvs.index(prepare), argvs.index(stack))

    def test_rebuild_keeps_existing_home_permissions(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        before = len(self.incus_calls())
        b.build("plasma-tide")
        after = self.incus_calls()[before:]
        self.assertFalse([a for a in after if a[:3] == ["storage", "volume", "create"]
                          or a[0] == "exec" and a[a.index("--") + 1] == "install"])

    def test_unmarked_existing_home_is_not_changed(self):
        self.approve()
        b = self.builder()
        b.incus.volume_create("tide", "plasma-tide-home")
        b.build("plasma-tide")
        self.assertFalse([a for a in self.incus_calls() if a[0] == "exec" and a[a.index("--") + 1] == "install"])
        self.assertEqual(self.fake_state()["volume_config"]["tide/plasma-tide-home"], {})

    def test_home_initialization_failure_is_retried_without_snapshot(self):
        self.approve()
        b = self.builder()
        with mock.patch.dict(os.environ, {"TIDE_FAKE_BOX_FAIL": "install"}):
            with self.assertRaisesRegex(bb.BuildError, "inicjalizacja /home/agent"):
                b.build("plasma-tide")
        state = self.fake_state()
        self.assertEqual(state["volume_config"]["tide/plasma-tide-home"][bb.HOME_INIT_KEY], "pending")
        self.assertFalse(state["instances"]["plasma-tide"]["snapshots"])
        self.assertFalse(state.get("pushed"))
        self.assertEqual(len(self.policy.calls), len(self.policy.cleared))
        b.build("plasma-tide")
        self.assertEqual(self.fake_state()["volume_config"]["tide/plasma-tide-home"][bb.HOME_INIT_KEY], "ready")

    def test_failed_verify_still_snapshots_with_warning(self):
        self.approve({**PROFILE, "verify": ["true", "exit 3"]})
        res = self.builder().build("plasma-tide")
        self.assertTrue(res.warning)
        self.assertEqual(res.failed_verify, ["exit 3"])
        self.assertEqual([s["name"] for s in self.fake_state()["instances"]["plasma-tide"]["snapshots"]], ["clean"])
        verify = [e for e in bs.events("plasma-tide") if e["type"] == "box.verify"]
        self.assertEqual(len(verify), 1)
        self.assertEqual((verify[0]["result"], verify[0]["failed"]), ("fail", ["exit 3"]))
        self.assertEqual(bs.journal_state("plasma-tide", "plasma-tide"), (bs.CLEAN, True))
        st = self.builder().status("plasma-tide")
        self.assertTrue(st.warning)
        self.assertIn("zbudowane z ostrzeżeniem", cli.format_status(st))

    def test_failing_build_step_is_an_error(self):
        self.approve({**PROFILE, "setup": ["exit 7"]})
        with self.assertRaisesRegex(bb.BuildError, "kod 7"):
            self.builder().build("plasma-tide")
        self.assertFalse(self.fake_state()["instances"]["plasma-tide"]["snapshots"])

    def test_build_does_not_read_stdin(self):
        # `cat` w setup czytałby stdin w nieskończoność, gdyby budowa go przekazywała.
        self.approve({**PROFILE, "setup": ["cat"]})
        r, w = os.pipe()
        self.addCleanup(os.close, w)
        saved = os.dup(0)
        os.dup2(r, 0)
        os.close(r)
        try:
            self.builder().build("plasma-tide")
        finally:
            os.dup2(saved, 0)
            os.close(saved)

    def test_limits_and_disk_come_from_profile(self):
        self.approve({**PROFILE, "resources": {"cpu": 3, "memory": "6GiB", "disk": "25GB"}})
        self.builder().build("plasma-tide")
        init = [a for a in self.incus_calls() if a[0] == "init"][0]
        self.assertEqual(init, ["init", "--project", "user-1000", "tide-base", "plasma-tide",
                                "-c", "limits.cpu=3", "-c", "limits.memory=6GiB",
                                "-c", "security.nesting=true", "-c", "security.syscalls.intercept.mknod=true",
                                "-c", "security.syscalls.intercept.setxattr=true"])
        self.assertIn(["config", "device", "override", "--project", "user-1000", "plasma-tide", "root", "size=25GB"],
                      self.incus_calls())

    def test_default_limits_and_no_nesting_without_docker(self):
        self.approve({k: v for k, v in PROFILE.items() if k != "docker"})
        self.builder().build("plasma-tide")
        init = [a for a in self.incus_calls() if a[0] == "init"][0]
        self.assertEqual(init[5:], ["-c", "limits.cpu=8", "-c", "limits.memory=24GiB"])
        self.assertIn("size=60GB", [a for a in self.incus_calls() if "root" in a][0])

    def test_vm_is_refused_without_incus(self):
        self.approve({**PROFILE, "type": "vm"})
        with self.assertRaisesRegex(bb.BuildError, "nieobsługiwane w tej wersji"):
            self.builder().build("plasma-tide")
        self.assertEqual(self.incus_calls(), [])

    def test_missing_stack_script_is_an_error(self):
        self.approve({**PROFILE, "stacks": ["node", "dotnet"]})
        with self.assertRaisesRegex(bb.BuildError, "dotnet.sh"):
            self.builder().build("plasma-tide")
        self.assertEqual(self.incus_calls(), [])

    def test_timed_events_have_duration(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        b.reset("plasma-tide")
        kinds = {}
        for e in bs.events("plasma-tide"):
            kinds.setdefault(e["type"], []).append(e)
        for kind in ("box.build", "box.ready", "box.reset", "box.verify"):
            self.assertIn(kind, kinds)
            for e in kinds[kind]:
                if kind == "box.build" and e.get("phase") == "start":
                    continue
                self.assertIsInstance(e["duration"], (int, float), e)
        with self.assertRaises(ValueError):
            bs.record("plasma-tide", "box.reset", "plasma-tide")


class CardCopyTest(BoxCase):
    def test_copy_for_card_from_clean_with_hash_nic_and_idempotent(self):
        self.approve()
        b = self.builder()
        res = b.build("plasma-tide")
        name = b.copy_for_card("plasma-tide", "K-38")
        self.assertEqual(name, bs.card_box_name("plasma-tide", "K-38"))
        self.assertEqual(name, "plasma-tide-k-38")
        argvs = self.incus_calls()
        self.assertIn(["copy", "--project", "user-1000", "plasma-tide/clean", "plasma-tide-k-38"], argvs)
        self.assertIn(["config", "device", "set", "--project", "user-1000", "plasma-tide-k-38", "eth0", *NIC_ON],
                      argvs)
        inst = self.fake_state()["instances"]["plasma-tide-k-38"]
        self.assertEqual(json.loads(inst["config"][bs.RECIPE_KEY])["hash"], res.hash)
        self.assertEqual(inst["devices"]["home"]["source"], "plasma-tide-k-38-home")
        ready = [e for e in bs.events("plasma-tide", name) if e["type"] == "box.ready"]
        self.assertEqual((ready[0]["card"], ready[0]["hash"], ready[0]["copy_of"]), ("K-38", res.hash, "plasma-tide"))
        before = len(self.incus_calls())
        self.assertEqual(b.copy_for_card("plasma-tide", "K-38"), name)
        self.assertFalse([a for a in self.incus_calls()[before:] if a[0] == "copy"])
        st = b.status("plasma-tide", "K-38")
        self.assertTrue(st.nic_filtering)
        self.assertEqual(st.recipe_hash, res.hash)
        self.assertEqual(st.diff, [])

    def test_card_copy_has_own_clean_snapshot_and_reset_card_restores_it(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        name = b.copy_for_card("plasma-tide", "K-38")
        inst = self.fake_state()["instances"][name]
        self.assertEqual([s["name"] for s in inst["snapshots"]], ["clean"])
        snap = inst["snapshots"][0]
        # migawka kopii już ma własny /home/agent i filtrowanie NIC — reset ich nie cofa
        self.assertEqual(snap["devices"]["home"]["source"], "plasma-tide-k-38-home")
        self.assertEqual(snap["devices"]["eth0"]["security.mac_filtering"], "true")
        create = [a for a in self.incus_calls() if a[:3] == ["storage", "volume", "create"] and name + "-home" in a]
        self.assertIn(f"{bb.HOME_INIT_KEY}=pending", create[0])
        self.assertEqual(self.fake_state()["volume_config"][f"tide/{name}-home"][bb.HOME_INIT_KEY], "ready")
        self.assertEqual(inst["status"], "Stopped")
        argvs = self.incus_calls()
        prepare = next(a for a in argvs if a[0] == "exec" and a[a.index("--") + 1] == "install" and name in a)
        stop = ["stop", "--project", "user-1000", name, "--force"]
        snap_argv = next(a for a in argvs if a[:2] == ["snapshot", "create"] and name in a)
        self.assertLess(argvs.index(prepare), argvs.index(stop))
        self.assertLess(argvs.index(stop), argvs.index(snap_argv))
        bs.mark_in_use("plasma-tide", name, "PT-2")
        code, _, err = self.run_cli("reset", "plasma-tide", "--card", "K-38", builder=b)
        self.assertEqual(code, 3, err)
        bs.mark_released("plasma-tide", name, "PT-2")
        code, out, err = self.run_cli("reset", "plasma-tide", "--card", "K-38", builder=b)
        self.assertEqual(code, 0, err)
        self.assertIn(["snapshot", "restore", "--project", "user-1000", name, "clean"], self.incus_calls())
        st = b.status("plasma-tide", "K-38")
        self.assertTrue(st.nic_filtering)
        self.assertEqual(self.fake_state()["instances"][name]["devices"]["home"]["source"], "plasma-tide-k-38-home")
        reset = [e for e in bs.events("plasma-tide", name) if e["type"] == "box.reset"]
        self.assertIsInstance(reset[0]["duration"], (int, float))

    def test_card_copy_keeps_verify_warning(self):
        self.approve({**PROFILE, "verify": ["exit 3"]})
        b = self.builder()
        b.build("plasma-tide")
        name = b.copy_for_card("plasma-tide", "K-38")
        ready = [e for e in bs.events("plasma-tide", name) if e["type"] == "box.ready"]
        self.assertTrue(ready[0]["warning"])
        self.assertTrue(b.status("plasma-tide", "K-38").warning)

    def test_copy_without_clean_snapshot_is_refused(self):
        with self.assertRaisesRegex(bb.BuildError, "clean"):
            self.builder().copy_for_card("plasma-tide", "K-38")

    def test_failed_home_initialization_removes_partial_copy_and_can_retry(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        with mock.patch.dict(os.environ, {"TIDE_FAKE_BOX_FAIL": "install"}):
            with self.assertRaisesRegex(bb.BuildError, "inicjalizacja /home/agent"):
                b.copy_for_card("plasma-tide", "K-38")
        self.assertNotIn("plasma-tide-k-38", self.fake_state()["instances"])
        self.assertEqual(self.fake_state()["volume_config"]["tide/plasma-tide-k-38-home"][bb.HOME_INIT_KEY], "pending")
        name = b.copy_for_card("plasma-tide", "K-38")
        self.assertEqual(self.fake_state()["instances"][name]["status"], "Stopped")
        self.assertEqual(self.fake_state()["volume_config"][f"tide/{name}-home"][bb.HOME_INIT_KEY], "ready")

    def test_card_box_name(self):
        self.assertEqual(bs.card_box_name("toolssuite", "k-7"), "toolssuite-k-7")
        self.assertEqual(bs.box_name("my_proj.x"), "my-proj-x")
        with self.assertRaises(ValueError):
            bs.card_box_name("plasma-tide", "../x")


class StatusResetDestroyTest(BoxCase):
    def test_status_shows_state_hash_and_nic(self):
        self.approve()
        b = self.builder()
        res = b.build("plasma-tide")
        code, out, _ = self.run_cli("status", "plasma-tide", builder=b)
        self.assertEqual(code, 0, out)
        self.assertIn("stan: clean", out)
        self.assertIn(f"hash: {res.hash}", out)
        self.assertIn("filtrowanie NIC: tak", out)

    def test_missing_nic_filtering_is_noncompliant(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        data = self.fake_state()
        for key in ("security.mac_filtering", "security.ipv4_filtering", "security.ipv6_filtering"):
            data["instances"]["plasma-tide"]["devices"]["eth0"].pop(key)
        self.write_state(data)
        st = b.status("plasma-tide")
        self.assertEqual(st.state, bs.NONCOMPLIANT)
        self.assertFalse(st.usable)
        code, out, _ = self.run_cli("status", "plasma-tide", builder=b)
        self.assertEqual(code, 3)
        self.assertIn("filtrowanie NIC: nie", out)
        self.assertIn("stan: niezgodne", out)
        # karta z profilu (bez lokalnego eth0) też jest niezgodna
        del data["instances"]["plasma-tide"]["devices"]["eth0"]
        self.write_state(data)
        self.assertEqual(b.status("plasma-tide").state, bs.NONCOMPLIANT)

    def test_changed_profile_makes_box_stale_with_diff(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        self.approve({**PROFILE, "apt": ["xvfb", "jq"], "stacks": []})
        st = b.status("plasma-tide")
        self.assertEqual(st.state, bs.STALE)
        self.assertEqual(st.diff, ["stacks", "apt"])
        self.assertFalse(st.usable)
        code, out, _ = self.run_cli("status", "plasma-tide", builder=b)
        self.assertEqual(code, 3)
        self.assertIn("stan: nieaktualny (do przebudowy)", out)
        self.assertIn("różnice: stacks, apt", out)
        # zmiana poza hashem (zasoby) nie robi pudełka nieaktualnym
        self.approve({**PROFILE, "resources": {"cpu": 2}})
        self.assertEqual(b.status("plasma-tide").state, bs.CLEAN)

    def test_box_without_approved_profile_is_not_usable(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        os.remove(bp.project_path("plasma-tide"))
        st = b.status("plasma-tide")
        self.assertTrue(st.nic_filtering)
        self.assertFalse(st.usable)
        code, out, _ = self.run_cli("status", "plasma-tide", builder=b)
        self.assertEqual(code, 3)
        self.assertIn("brak zatwierdzonego profilu", out)

    def test_status_without_box(self):
        self.approve()
        code, out, _ = self.run_cli("status", "plasma-tide", builder=self.builder())
        self.assertEqual(code, 0)
        self.assertIn("stan: brak", out)
        code, out, _ = self.run_cli("status", "plasma-tide", "--json", builder=self.builder())
        self.assertEqual(json.loads(out)["state"], "brak")

    def test_reset_refuses_with_running_step_then_restores_clean(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        bs.mark_in_use("plasma-tide", "plasma-tide", "PT-1")
        self.assertEqual(b.status("plasma-tide").state, bs.IN_USE)
        code, _, err = self.run_cli("reset", "plasma-tide", builder=b)
        self.assertEqual(code, 3)
        self.assertIn("PT-1", err)
        self.assertNotIn("restore", " ".join(" ".join(a) for a in self.incus_calls()))
        bs.mark_released("plasma-tide", "plasma-tide", "PT-1")
        code, out, _ = self.run_cli("reset", "plasma-tide", builder=b)
        self.assertEqual(code, 0, out)
        self.assertIn(["snapshot", "restore", "--project", "user-1000", "plasma-tide", "clean"], self.incus_calls())
        # reset przywraca rootfs; /home/agent to osobny wolumen (urządzenie dyskowe) — nie jest ruszany
        self.assertFalse([a for a in self.incus_calls() if a[:2] == ["storage", "volume"] and "delete" in a])
        self.assertIn("tide/plasma-tide-home", self.fake_state()["volumes"])

    def test_destroy_removes_box_and_card_copies(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        b.copy_for_card("plasma-tide", "K-38")
        b.copy_for_card("plasma-tide", "K-40")
        Incus("user-1000", binary=self.fake_incus).launch("tide-base", "plasma-tide-other")   # obce pudełko
        code, _, err = self.run_cli("destroy", "plasma-tide", builder=b)
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        code, out, _ = self.run_cli("destroy", "plasma-tide", "--yes", builder=b)
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(self.fake_state()["instances"]), ["plasma-tide-other"])
        self.assertEqual(self.fake_state()["volumes"], [])
        self.assertEqual(bs.journal_state("plasma-tide", "plasma-tide")[0], bs.ABSENT)

    def test_destroy_refuses_when_card_copy_busy(self):
        self.approve()
        b = self.builder()
        b.build("plasma-tide")
        b.copy_for_card("plasma-tide", "K-38")
        bs.mark_in_use("plasma-tide", "plasma-tide-k-38", "PT-9")
        with self.assertRaises(bb.BoxBusy):
            b.destroy("plasma-tide")
        self.assertIn("plasma-tide-k-38", self.fake_state()["instances"])


class ApproveTest(BoxCase):
    def write_profile(self, text):
        path = os.path.join(self.tmp, "box.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_approve_validates_saves_and_records_cli_consent(self):
        path = self.write_profile("tide-box: 1\nstacks: [node]\nnetwork: { domains: [registry.npmjs.org] }\n")
        code, out, err = self.run_cli("approve", "plasma-tide", "--file", path)
        self.assertEqual(code, 0, err)
        self.assertEqual(bp.load_project("plasma-tide")["stacks"], ["node"])
        ev = [e for e in bs.events("plasma-tide") if e["type"] == "box.approved"]
        self.assertEqual((ev[0]["source"], ev[0]["accepted_red"]), ("cli", []))

    def test_approve_rejects_schema_errors(self):
        path = self.write_profile("tide-box: 1\nnetwork: { full: true }\n")
        code, _, err = self.run_cli("approve", "plasma-tide", "--file", path)
        self.assertEqual(code, 1)
        self.assertIn("network.full", err)
        self.assertIsNone(bp.load_project("plasma-tide"))

    def test_red_item_requires_explicit_flag(self):
        path = self.write_profile("tide-box: 1\nnetwork: { domains: [github.com, registry.npmjs.org] }\n")
        code, _, err = self.run_cli("approve", "plasma-tide", "--file", path)
        self.assertEqual(code, 1)
        self.assertIn("--accept-red github.com", err)
        self.assertIsNone(bp.load_project("plasma-tide"))
        code, _, err = self.run_cli("approve", "plasma-tide", "--file", path, "--accept-red", "registry.npmjs.org")
        self.assertEqual(code, 1)
        self.assertIsNone(bp.load_project("plasma-tide"))
        code, _, err = self.run_cli("approve", "plasma-tide", "--file", path, "--accept-red", "github.com")
        self.assertEqual(code, 0, err)
        ev = [e for e in bs.events("plasma-tide") if e["type"] == "box.approved"][-1]
        self.assertEqual((ev["source"], ev["accepted_red"]), ("cli", ["github.com"]))


class CliTest(BoxCase):
    def test_agents_version_enters_the_recipe_and_settings_override_it(self):
        self.approve()
        self.assertEqual(self.builder().agents_version, agents.AGENTS_VERSION)
        own = BoxSettings(project="user-1000", ip_wait_seconds=0, agents_version="claude-code 9.9.9")
        self.assertEqual(self.builder(own).agents_version, "claude-code 9.9.9")
        self.builder().build("plasma-tide")
        st = self.builder(own).status("plasma-tide")
        self.assertEqual((st.state, st.diff), (bs.STALE, ["agents"]))

    def test_build_uses_the_shared_no_proxy(self):
        self.approve()
        self.builder().build("plasma-tide")
        for argv in (a for a in self.incus_calls() if a[0] == "exec"):
            self.assertIn(f"NO_PROXY={netenv.BOX_NO_PROXY}", argv)

    def test_tide_entry_point_build_without_running_proxy_fails_before_incus(self):
        # Osobny proces czyta prawdziwe box/stacks; profil bez stosów, żeby nic z nich nie biegło na atrapie.
        # Ustawienia z sekcji `box` w cardflow.json (ten sam plik co proxy PT-168); proxy w tym procesie nie biegnie,
        # a własne proxy polecenia (PT-192, base-wlasne-proxy-cli) nie wstaje bez mostka — odmowa przed incus.
        self.approve({**PROFILE, "stacks": []})
        path = bset.settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"box": {"project": "user-1000", "ip-wait-seconds": 0, "proxy-port": 18555}}, fh)
        # Host z zainstalowanym Incusem ma prawdziwy mostek. Także zapasowy odczyt przez ip musi być atrapą.
        ip = os.path.join(self.tmp, "fakebin", "ip")
        with open(ip, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(ip, 0o755)
        env = {**os.environ, "PYTHONPATH": DAEMON}
        proc = subprocess.run([sys.executable, os.path.join(DAEMON, "tide.py"), "box", "build", "plasma-tide"],
                              env=env, stdin=subprocess.PIPE, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("tide box build: proxy Tide", proc.stderr)
        self.assertFalse([a for a in self.incus_calls() if a[0] != "network"])


class SettingsTest(unittest.TestCase):
    def test_builder_keys_in_box_section_with_defaults(self):
        got = bset.from_dict({})
        self.assertEqual((got.project, got.base_image, got.pool, got.nic, got.ipv6_filtering, got.agent_uid),
                         (f"user-{os.getuid()}", "tide-base", "tide", "eth0", False, 1000))
        got = bset.from_dict({"box": {"enabled": True, "project": "user-7", "base-image": "tide-base-2",
                                      "pool": "fast", "nic": "eth1", "ipv6-filtering": True, "agents-version": "1.2",
                                      "ip-wait-seconds": 5, "agent-uid": 1001, "agent-gid": 1002}})
        self.assertEqual((got.project, got.base_image, got.pool, got.nic, got.ipv6_filtering, got.agents_version,
                          got.ip_wait_seconds, got.agent_uid, got.agent_gid, got.warnings),
                         ("user-7", "tide-base-2", "fast", "eth1", True, "1.2", 5.0, 1001, 1002, ()))
        self.assertTrue(got.enabled)

    def test_bad_builder_values_warn_and_fall_back(self):
        got = bset.from_dict({"box": {"project": "a b", "pool": "", "ipv6-filtering": "tak",
                                      "ip-wait-seconds": -1, "agent-uid": 0}})
        self.assertEqual((got.project, got.pool, got.ipv6_filtering, got.ip_wait_seconds, got.agent_uid),
                         (f"user-{os.getuid()}", "tide", False, 30.0, 1000))
        self.assertEqual(len(got.warnings), 5)

    def test_tide_entry_point_usage(self):
        proc = subprocess.run([sys.executable, os.path.join(DAEMON, "tide.py")], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("tide box", proc.stderr)


if __name__ == "__main__":
    unittest.main()
