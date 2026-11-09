"""Wspólna baza testów kroku w pudełku w silniku (PT-181, #18): FlowInProcess + atrapa `incus` (fake_incus.py).

- pudełko projektu `proj` powstaje w stanie atrapy jak po budowie (#4): filtrowanie NIC, odcisk przepisu z `Builder.stamp`,
  migawka `clean`, `box.ready` w dzienniku pudełka — bez szablonów stosów i bez proxy budowy,
- `incus exec` atrapy wykonuje polecenie lokalnie, więc krok naprawdę biegnie: `env -i` → atrapa `systemd-run --user`
  pudełka (pid jednostki w `<base>/box-units/`) → prawdziwe `tide-step` → atrapa `claude` z flowfakes.py,
- `systemctl --user` pudełka (`is-active`, `kill`, `show-environment`) czyta ten sam katalog jednostek,
- proxy to atrapa zapisująca `set_box_policy`/`clear_box_policy`.
Prawdziwy Incus, systemd ani sieć nie są potrzebne.
"""
import json
import os
import shlex
from unittest import mock

from tide_daemon.box import builder as bb
from tide_daemon.box import home as boxhome
from tide_daemon.box import k3
from tide_daemon.box import profile as bp
from tide_daemon.box import runner as br
from tide_daemon.box import state as bs
from tide_daemon.box.incus import Incus
from tide_daemon.box.settings import BoxSettings
from tide_daemon.cardflow_monitor import CardFlowMonitor

from .fakebin import BOX_FAKES, BOX_LOG_ONLY, FAKE_INCUS
from .test_cardflow import FlowInProcess

SLUG = "proj"
PROJECT = "user-1000"
PROXY_LISTEN, PROXY_PORT = "10.173.184.1", 18080

BOX_SYSTEMD_RUN = r'''#!/usr/bin/python3
import os, sys
args = sys.argv[1:]
unit = next(a.split("=", 1)[1] for a in args if a.startswith("--unit="))
cut = args.index("--")
os.setsid()
os.makedirs(UNITS, exist_ok=True)
with open(os.path.join(UNITS, unit), "w") as fh:
    fh.write(str(os.getpid()))
with open(LOG, "a") as fh:
    fh.write("box-systemd-run " + " ".join(args[:cut]) + "\n")
os.execv(args[cut + 1], args[cut + 1:])
'''

BOX_SYSTEMCTL = r'''#!/usr/bin/python3
import os, signal, sys
args = [a for a in sys.argv[1:] if a not in ("--user", "--quiet")]
with open(LOG, "a") as fh:
    fh.write("box-systemctl " + " ".join(args) + "\n")
if args[0] not in ("is-active", "kill"):
    sys.exit(0)
try:
    pid = int(open(os.path.join(UNITS, args[-1])).read())
except (OSError, ValueError):
    pid = 0
def alive():
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        return False
if args[0] == "is-active":
    sys.exit(0 if pid and alive() else 3)
name = next((a.split("=", 1)[1] for a in args if a.startswith("--signal=")), "SIGTERM")
if pid and alive():
    try:
        os.killpg(pid, getattr(signal, name))
    except ProcessLookupError:
        pass
'''


class FakeProxy:
    listen, port = PROXY_LISTEN, PROXY_PORT

    def __init__(self):
        self.calls = []
        self.grants = k3.GrantRegistry()
        self.steps = {}

    def register_step(self, ip, step, key, denied_log):
        self.steps[step] = (ip, key, denied_log)

    def revoke_step(self, step):
        self.steps.pop(step, None)
        self.grants.revoke_step(step)

    def set_box_policy(self, ip, domains, ollama, denied_log_path, *, full=False, box=""):
        self.calls.append(("set", ip, list(domains), ollama, denied_log_path, full, box))

    def clear_box_policy(self, ip):
        self.calls.append(("clear", ip))

    def sets(self):
        return [c for c in self.calls if c[0] == "set"]


class BoxFlow(FlowInProcess):
    """Silnik z atrapami pudełka podpiętymi przed pierwszym `tick`."""

    def setUp(self):
        self.proxy = FakeProxy()
        self.box_enabled = True
        self.prepare_box_fakes()
        super().setUp()
        self.engine = self.flow.engine

    def prepare_box_fakes(self):
        memory = mock.patch('tide_daemon.box.admission.read_memory', return_value=(78 * 1024**3, 60 * 1024**3))
        memory.start()
        self.addCleanup(memory.stop)
        # Przed FlowInProcess.setUp: katalog bazowy znamy dopiero po nim, więc atrapy leżą w osobnym katalogu.
        import tempfile, shutil
        self.boxtmp = os.path.realpath(tempfile.mkdtemp(prefix="tide-boxflow-"))
        self.addCleanup(shutil.rmtree, self.boxtmp, True)
        fake = os.path.join(self.boxtmp, "bin")
        os.makedirs(os.path.join(fake, "box"))
        # Inicjalizacja wolumenu kopii karty też biegnie przez atrapę. Nigdy install /home/agent hosta.
        install = os.path.join(fake, "box", "install")
        with open(install, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(install, 0o755)
        self.units = os.path.join(self.boxtmp, "box-units")
        self.boxlog = os.path.join(self.boxtmp, "box.log")
        self.fake_incus = os.path.join(fake, "incus")
        with open(self.fake_incus, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/bash\nexport TIDE_FAKE_BOX_BIN={shlex.quote(os.path.join(fake, "box"))}\n'
                     f'exec /usr/bin/python3 -s {shlex.quote(FAKE_INCUS)} "$@"\n')
        self.box_run = os.path.join(fake, "box-systemd-run")
        self.box_ctl = os.path.join(fake, "box-systemctl")
        consts = f"UNITS = {self.units!r}\nLOG = {self.boxlog!r}\n"
        scripts = [(path, body.replace("\n", "\n" + consts, 1))
                   for path, body in ((self.box_run, BOX_SYSTEMD_RUN), (self.box_ctl, BOX_SYSTEMCTL))]
        # Kopia per-card inicjalizuje wolumen przez install; polecenia pudełka muszą pozostać atrapami.
        scripts.extend((os.path.join(fake, "box", name), BOX_LOG_ONLY) for name in BOX_FAKES)
        for path, body in scripts:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(path, 0o755)
        for path in (self.fake_incus,):
            os.chmod(path, 0o755)
        patcher = mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": os.path.join(self.boxtmp, "boxboard"),
                                               "TIDE_FAKE_INCUS_STATE": os.path.join(self.boxtmp, "incus-state"),
                                               "XDG_DATA_HOME": os.path.join(self.boxtmp, "data")})
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- silnik z atrapami ------------------------------------------------------------------------

    def box_settings(self) -> BoxSettings:
        return BoxSettings(enabled=self.box_enabled, project=PROJECT, ip_wait_seconds=5)

    def incus(self) -> Incus:
        return Incus(PROJECT, binary=self.fake_incus)

    def builder(self) -> bb.Builder:
        return bb.Builder(self.box_settings(), incus=self.incus(), policy=self.proxy, sleep=lambda s: None)

    def box_runner(self, settings, slug, card, per_card):
        test = self

        class Runner(br.BoxRunner):
            def base_env(self):
                # Atrapa `claude` pisze dziennik sesji w $HOME i commituje — katalog domowy testu, nie /home/agent.
                env = super().base_env()
                env.update(HOME=boxhome.path(slug, card if per_card else ""), GIT_CONFIG_GLOBAL=os.environ["GIT_CONFIG_GLOBAL"],
                           GIT_CONFIG_NOSYSTEM="1")
                return env

        return Runner(settings, slug, card=card, per_card=per_card, incus=self.incus(),
                      proxy_base=f"http://{PROXY_LISTEN}:{PROXY_PORT}", systemd_run=self.box_run,
                      systemctl=self.box_ctl, sleep=lambda s: None)

    def wire(self, engine):
        engine.box_settings_source = self.box_settings
        engine.box_builder = self.builder
        engine.box_proxy = self.proxy
        engine.box_runner_factory = self.box_runner
        engine.box_sleep = lambda s: None

    def make_flow(self):
        with mock.patch.object(CardFlowMonitor, "start", lambda self: None):
            flow = super().make_flow()
        self.wire(flow.engine)
        flow.start()
        return flow

    # --- pudełko jak po budowie ---------------------------------------------------------------------

    def approve(self, profile=None) -> dict:
        profile = profile or {"tide-box": 1, "network": {"domains": ["api.example.org"]}}
        bp.save_project(SLUG, profile)
        return profile

    def build_box(self, profile=None, *, running=True, nic=True, stamp_profile=None):
        """Pudełko `proj` w atrapie: kopia obrazu, NIC, odcisk przepisu, migawka `clean`, `box.ready`."""
        profile = self.approve(profile)
        builder = self.builder()
        incus = builder.incus
        # Tak jak Builder.build: historia powstaje przed instancją i montowaniami.
        bs.record(SLUG, "box.build", SLUG, phase="start")
        incus.init("tide-base", SLUG)
        builder._attach_home(SLUG)
        if nic:
            builder.apply_nic_filter(SLUG)
        stamp = builder.stamp(bp.with_defaults(stamp_profile or profile))
        incus.config_set(SLUG, {bs.RECIPE_KEY: json.dumps(stamp, sort_keys=True, separators=(",", ":"))})
        incus.snapshot_create(SLUG, bs.SNAPSHOT)
        bs.record(SLUG, "box.ready", SLUG, hash=stamp["hash"], warning=False, duration=0.1)
        if running:
            incus.start(SLUG)
        return profile

    def incus_calls(self, *words) -> list:
        out = []
        if not os.path.exists(os.environ["TIDE_FAKE_LOG"]):
            return out
        with open(os.environ["TIDE_FAKE_LOG"], encoding="utf-8") as fh:
            for line in fh:
                argv = shlex.split(line)
                if argv and argv[0] == "incus" and argv[1:1 + len(words)] == list(words):
                    out.append(argv)
        return out

    def box_log(self) -> list:
        try:
            with open(self.boxlog, encoding="utf-8") as fh:
                return fh.read().splitlines()
        except OSError:
            return []
