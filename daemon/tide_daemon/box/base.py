"""Obraz bazowy Tide (#16, PT-179): `tide box base` i `tide box login`.

Kolejność `tide box base` (`BaseBuilder.build`):
 1. adres proxy z uruchomionego proxy Tide (#5) — brak → błąd przed pierwszym poleceniem `incus`; skrypty
    `box/stacks/base.sh` i `tide-step.sh`, przypięte wersje agentów (`box/agents.py`),
 2. `incus init images:ubuntu/24.04 <base-image>` (bez startu), filtrowanie NIC jak w builderze (#4), start, adres IP,
 3. `set_box_policy(ip, domeny szablonu base, ollama=False, base-denied.log)` — przed pierwszym `incus exec`,
 4. `incus file push` skryptów do `/opt/tide/stacks` i czcionek użytkownika (`~/.local/share/fonts` hosta, tylko
    zwykłe pliki czcionek) jako `/opt/tide/fonts.tar`,
 5. `bash /opt/tide/stacks/base.sh <krok>` dla każdego kroku z `STEPS`, jako root pudełka, z proxy w środowisku,
 6. `user.tide.agents`, migawka `<base-image>/clean`, `incus publish <base-image>/clean --alias <base-image> --reuse`
    (z tego obrazu `incus init` buildera robi pudełka projektów), `incus stop`,
 7. polityka budowy zdjęta z proxy (`clear_box_policy`) — także po błędzie.

Logowanie agentów (K1) nie jest częścią budowy: `BaseResult.login` to programy z `k3.login_programs()` (dziś puste —
K3 dla obu), a CLI kończy budowę instrukcją „zaloguj: tide box login …”, nie błędem. `/home/agent` pudełka projektu
to trwały wolumen `<pudełko>-home` (#4), więc `tide box login <slug>` loguje w nim, raz, poza budową.

Nic z `~/.claude` ani `~/.codex` hosta nie trafia do obrazu. Opis: docs/tide-daemon.md (Obraz bazowy Tide).
"""
import dataclasses
import os
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass, field

from . import agents
from . import builder as bb
from . import k3
from . import netenv
from . import profile as bp
from . import proxy as bproxy
from . import state as bs
from . import templates
from .incus import Incus
from .settings import BoxSettings

SOURCE_IMAGE = "images:ubuntu/24.04"
STEPS = ("sources", "packages", "user", "environment", "fonts", "tide-step", "apparmor", "claude", "codex",
         "cleanup")
SCRIPTS = ("base.sh", "tide-step.sh")
REMOTE_FONTS = "/opt/tide/fonts.tar"
AGENTS_KEY = "user.tide.agents"
LOGIN_HINT = "zaloguj: tide box login"
FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2", ".pfb", ".pfa", ".pcf", ".pcf.gz", ".bdf")


def host_fonts_dir(env=None) -> str:
    """Czcionki użytkownika hosta: `$XDG_DATA_HOME/fonts` (domyślnie `~/.local/share/fonts`)."""
    env = os.environ if env is None else env
    data = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "fonts")


def fonts_tar(folder: str, target: str) -> int:
    """Archiwum zwykłych plików czcionek z `folder` (bez dowiązań, bez innych plików, właściciel root, 0644).
    Brak katalogu → puste archiwum. Zwraca liczbę czcionek."""
    count = 0
    with tarfile.open(target, "w") as tar:
        if os.path.isdir(folder):
            for dirpath, dirs, names in os.walk(folder):
                dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(dirpath, d)))
                for name in sorted(names):
                    path = os.path.join(dirpath, name)
                    if os.path.islink(path) or not os.path.isfile(path) or not name.lower().endswith(FONT_SUFFIXES):
                        continue
                    info = tar.gettarinfo(path, arcname=os.path.relpath(path, folder))
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mode = 0o644
                    with open(path, "rb") as fh:
                        tar.addfile(info, fh)
                    count += 1
    return count


def build_domains() -> tuple:
    """Lista domen budowy obrazu bazowego = szablon `base` (z domenami instalatorów agentów z P-6)."""
    return templates.build_domains({})


def ensure_proxy(settings: BoxSettings, start=bproxy.start_service):
    """Proxy Tide dla polecenia z wiersza poleceń: uruchomione w tym procesie albo start własnej instancji tego
    samego `ProxyService` na adresie mostka i porcie `box.proxy-port` (jedyny port wpuszczany przez nftables).
    Zwraca `(proxy, własne)`; brak proxy → `BuildError` (budowy bez proxy nie ma)."""
    service = bproxy.running()
    if service is not None:
        return service, False
    try:
        service = start(dataclasses.replace(settings, enabled=True))
    except (bproxy.ProxyError, OSError, ValueError) as exc:
        raise bb.BuildError(
            f"proxy Tide nie wstało: {exc} — jeśli usługa Tide już ma proxy pudełka (box.enabled), port "
            f"{settings.proxy_port} jest zajęty; zatrzymaj usługę na czas tego polecenia") from exc
    if service is None:
        raise bb.BuildError("proxy Tide nie wstało (brak polecenia incus albo mostka "
                            f"{settings.bridge}) — budowa bez proxy jest niedozwolona")
    return service, True


@dataclass
class BaseResult:
    image: str
    steps: list = field(default_factory=list)
    fonts: int = 0
    duration: float = 0.0
    login: tuple = ()
    denied_log: str = ""


class BaseBuilder:
    def __init__(self, settings: BoxSettings, incus: Incus | None = None, policy=None, clock=time.monotonic,
                 sleep=time.sleep, scripts_dir: str | None = None, fonts_dir: str | None = None):
        self.settings = settings
        self.incus = incus or Incus(settings.project)
        self.policy = policy or bproxy.running() or bb.ProxyMissing()
        self.clock, self.sleep = clock, sleep
        self.scripts_dir = scripts_dir or bb.SCRIPTS_DIR
        self.fonts_dir = fonts_dir or host_fonts_dir()
        # Filtrowanie NIC i czekanie na adres — ta sama droga co w pudełkach projektów.
        self._box = bb.Builder(settings, self.incus, policy=self.policy, clock=clock, sleep=sleep,
                               scripts_dir=self.scripts_dir)

    def build(self) -> BaseResult:
        proxy = bb.proxy_url(self.policy)
        missing = [s for s in SCRIPTS if not os.path.isfile(os.path.join(self.scripts_dir, s))]
        if missing:
            raise bb.BuildError("brak skryptów obrazu bazowego: " + ", ".join(missing))
        for program, version in (("Claude Code", agents.CLAUDE_VERSION), ("Codex", agents.CODEX_VERSION)):
            if not agents.pinned(version):
                raise bb.BuildError(f"wersja {program} nie jest przypięta: {version!r}")
        return self._execute_build()

    @bb.admission.queued('base')
    def _execute_build(self) -> BaseResult:
        proxy = bb.proxy_url(self.policy)
        name = self.settings.base_image
        folder = bb.state_dir(name)
        os.makedirs(folder, exist_ok=True)
        denied = os.path.join(folder, "base-denied.log")
        with open(denied, "w", encoding="utf-8"):
            pass                                   # dziennik odmów tej budowy zaczyna się pusty
        os.chmod(denied, 0o600)
        started = self.clock()
        result = BaseResult(name, denied_log=denied)
        with open(os.path.join(folder, "base-build.log"), "w", encoding="utf-8") as log:
            self.incus.log = log
            try:
                self._build(name, proxy, denied, result)
            finally:
                self.incus.log = None
        result.duration = round(self.clock() - started, 3)
        result.login = k3.login_programs()
        return result

    def _build(self, name, proxy, denied, result):
        if self.incus.instance(name) is not None:
            self.incus.delete(name, force=True)
        result.steps.append("init")
        self.incus.init(SOURCE_IMAGE, name, bb.admission.limits())
        result.steps.append("nic")
        self._box.apply_nic_filter(name)
        ip = None
        try:
            bb.admission.start(self.incus, name)
            ip = self._box._wait_ip(name)
            self.policy.set_box_policy(ip, list(build_domains()), False, denied)
            from .lifecycle import require_no_swap
            require_no_swap(self.incus, name, netenv.proxy_env(proxy))
            self._provision(name, proxy, result)
        except BaseException:
            self._stop_quietly(name)           # nieudana budowa nie zostawia uruchomionego tide-base
            raise
        finally:
            if ip is not None:
                self.policy.clear_box_policy(ip)

    def _stop_quietly(self, name):
        try:
            self.incus.stop(name)
        except Exception:                      # błąd zatrzymania nie może przykryć błędu budowy
            pass

    def _provision(self, name, proxy, result):
        env = {**netenv.proxy_env(proxy), "DEBIAN_FRONTEND": "noninteractive",
               "TIDE_BOX_PROXY": proxy, "TIDE_BOX_NO_PROXY": netenv.BOX_NO_PROXY,
               "TIDE_AGENT_UID": str(self.settings.agent_uid), "TIDE_AGENT_GID": str(self.settings.agent_gid),
               "TIDE_CLAUDE_VERSION": agents.CLAUDE_VERSION, "TIDE_CODEX_VERSION": agents.CODEX_VERSION}
        result.steps.append("push")
        for script in SCRIPTS:
            self.incus.file_push(os.path.join(self.scripts_dir, script), name, f"{bb.REMOTE_STACKS}/{script}")
        with tempfile.TemporaryDirectory(prefix="tide-base-") as tmp:
            tar = os.path.join(tmp, "fonts.tar")
            result.fonts = fonts_tar(self.fonts_dir, tar)
            self.incus.file_push(tar, name, REMOTE_FONTS)
        script = f"{bb.REMOTE_STACKS}/base.sh"
        for step in STEPS:
            result.steps.append(step)
            code = self.incus.exec(name, ["bash", script, step], env)
            if code != 0:
                raise bb.BuildError(f"base.sh {step}: kod {code} (dziennik: base-build.log)")
        result.steps.append("snapshot")
        self.incus.config_set(name, {AGENTS_KEY: agents.AGENTS_VERSION})
        self.incus.snapshot_create(name, bs.SNAPSHOT)
        result.steps.append("publish")
        self.incus.publish(f"{name}/{bs.SNAPSHOT}", name)
        self.incus.stop(name)


# ------------------------------------------------------------ logowanie K1

def login(settings: BoxSettings, slug: str | None, out, incus: Incus | None = None, policy=None,
          run=subprocess.run, programs=None) -> int:
    """`tide box login [<slug>]`: `incus exec -t` z `claude login`/`codex login` jako `agent` w pudełku projektu
    (trwały `/home/agent`), z proxy Tide i polityką projektu na czas logowania. Programy z K3 są pomijane —
    prawdziwe poświadczenia nie mają być w pudełku. Zwraca kod wyjścia."""
    programs = k3.login_programs() if programs is None else tuple(programs)
    if not programs:
        print("tide box login: nic do zalogowania — claude i codex działają w pudełku przez K3 (token wstawia "
              "proxy Tide na hoście, w pudełku jest tylko atrapa kroku)", file=out)
        return 0
    if not slug:
        raise bb.BuildError("podaj projekt: tide box login <slug> (logowanie trafia do trwałego /home/agent "
                            "pudełka projektu)")
    incus = incus or Incus(settings.project)
    name = bs.box_name(slug)
    steps = bs.running_steps(slug, name)
    if steps:                                  # polityka logowania zastąpiłaby politykę biegnących kroków
        raise bb.BoxBusy(f"w pudełku {name} biegną kroki: {', '.join(steps)} — logowanie odmówione")
    inst = incus.instance(name)
    if inst is None:
        raise bb.BuildError(f"pudełko {name} nie istnieje (tide box build {slug})")
    policy = policy or bproxy.running() or bb.ProxyMissing()
    proxy = bb.proxy_url(policy)
    if str(inst.get("status", "")).lower() != "running":
        bb.admission.start(incus, name, slug=slug)
    ip = bb.Builder(settings, incus, policy=policy)._wait_ip(name)
    composed = bproxy.compose_policy(bp.load_project(slug))
    policy.set_box_policy(ip, list(composed.domains), False, os.path.join(bb.state_dir(slug), "login-denied.log"))
    env = {**netenv.proxy_env(proxy), "HOME": bs.HOME_PATH}
    try:
        for program in programs:
            argv = incus.exec_argv(name, agents.LOGIN_ARGV[program], env, user=settings.agent_uid,
                                   cwd=bs.HOME_PATH, tty=True)
            print(f"tide box login: {program} w pudełku {name}", file=out)
            code = run(argv).returncode
            if code != 0:
                return code
    finally:
        policy.clear_box_policy(ip)
    return 0
