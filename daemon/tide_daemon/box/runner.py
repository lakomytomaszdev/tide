"""BoxRunner (PT-169, #6): krok w tle w pudełku karty — drugi `Runner` obok `HostRunner` (`tasks/runner.py`).

Program agenta biegnie w pudełku jako użytkownik `agent` (uid/gid z ustawień, domyślnie 1000) pod **tymi samymi
ścieżkami co na hoście** (worktree, wspólny `.git`, katalog kroku), więc wskaźniki gita, `resolve_git_dir`,
`stepstop` i plik `exit-code` działają bez zmian. Wyboru runnera w silniku tu nie ma (to #18).

Uruchomienie (argv na hoście, widoczne w `ps` — bez żadnej wartości z `in/env`):

    incus exec <pudełko> --project <p> -T --user 1000 --group 1000 --cwd <worktree> --
      /usr/bin/env -i TIDE_STEP=<katalog kroku> XDG_RUNTIME_DIR=/run/user/1000
      /usr/bin/systemd-run --user --unit=tide-step-<id> --collect --wait --quiet -p CPUWeight=40 -p IOWeight=40
        --working-directory=<worktree> --setenv=TIDE_STEP=<katalog kroku> --
      /usr/local/bin/tide-step <argv agenta>

Krok jest **usługą** użytkownika `agent`, nie scope (decyzja `krok-jako-usluga-user` po P-9: proces w scope ginie
razem z klientem `incus exec`, czyli z restartem usługi Tide). Opakowanie `tide-step` (plik obok, instaluje je
obraz bazowy #16 w `/usr/local/bin`) czyta `<krok>/in/env` (0600, właściciel = usługa), eksportuje zmienne,
przechodzi do `TIDE_CWD`, uruchamia agenta z `in/prompt.txt` na stdin i `out/step.log` na wyjściu, zapisuje kod
w `in/exit-code` — jak `EXIT_WRAPPER` na hoście.

`in/env`: całe środowisko programu (ścieżki `/home/agent`), sekrety punktu lokalnego (`secret_env`), proxy
(`HTTPS_PROXY`, `HTTP_PROXY` i małymi literami) na adres mostka i port proxy, `NO_PROXY` = `box_no_proxy(<mostek>)`
oraz zmienne z `step_env` (porty #8, atrapy K3 #18). Żadna z tych wartości nie trafia do argv ani `--env`.

Urządzenia dyskowe (`ensure_devices`, pod blokadą plikową na projekt — dwa pierwsze kroki naraz dodają je raz):
`<repo>/.git` rw, nakładki ro na `.git/hooks`, `.git/config`, `.git/info` i istniejące
`.git/worktrees/*/config.worktree`, `<repo>-worktrees` rw (worktree i katalogi kroku), `/home/agent`
(`~/.local/share/tide/box/<slug>/home`, 0700 — chyba że builder już podpiął tam wolumen `<pudełko>-home`).
Bez `shift=true` — `raw.idmap` uid/gid usługi ↔ agent (P-4, decyzja `raw-idmap-zamiast-shift`); pojedynczego pliku
nie da się dołączyć do działającego kontenera, więc zmiana `raw.idmap` albo nowa nakładka-plik zatrzymuje pudełko
na czas dodania (odmowa, gdy biegną w nim inne kroki). Główny worktree projektu i katalog domowy hosta nigdy.
Opis: docs/tide-daemon.md („Krok w pudełku: BoxRunner”).
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from typing import Callable

from ..tasks import agentcmd, launch
from ..tasks.runner import Started
from . import state as bs
from .incus import Incus
from .settings import BoxSettings

# Pełne NO_PROXY pudełka (jedno źródło): `in/env` i `env(step)` tutaj, `/etc/environment` obrazu bazowego (#16)
# i drop-in `dockerd` szablonu `docker.sh` (#29). Adres mostka dokleja `box_no_proxy`. Testcontainers mapują porty
# na localhost, więc klienci bez CIDR w NO_PROXY i tak trafiają w `localhost`/`127.0.0.1` (P-6).
BOX_NO_PROXY = "localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal"

# Wynik P-2 (`docs/box-prototype.md`, decyzja `bwrap-profil-w-pudelku`): bwrap Claude Code działa w pudełku
# z profilem AppArmor obrazu bazowego, więc wewnętrzna piaskownica zostaje włączona. False → `sandbox.enabled=false`
# i wpis „ryzyko K1” w dzienniku karty.
BOX_SANDBOX = True
BOX_DENY_READ = ("~/.claude", "~/.codex", "/packs")

BOX_HOME = bs.HOME_PATH
BOX_USER = "agent"
BOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
TIDE_STEP = "/usr/local/bin/tide-step"
SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
ENV_FILE = "env"
LOCAL_TOKEN_FILE = "local-token"
EXEC_LOG = "box-exec.log"              # wyjście klienta `incus exec` (agent pisze do step.log w pudełku)
DEVICE_PREFIX = "tide-"
TIDE_STEP_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tide-step")

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNIT = re.compile(r"^tide-step-[A-Za-z0-9_-]{1,64}(\.service)?$")
_BOX = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def box_no_proxy(bridge: str) -> str:
    """`BOX_NO_PROXY` z adresem mostka na końcu (bez adresu — sama lista; nigdy `*`)."""
    bridge = (bridge or "").strip()
    if bridge and not re.fullmatch(r"[0-9A-Fa-f:.]+", bridge):
        raise ValueError(f"adres mostka nie jest adresem IP: {bridge!r}")
    return BOX_NO_PROXY + (f",{bridge}" if bridge else "")


def proxy_env(proxy_base: str) -> dict:
    """Zmienne proxy pudełka dla `http://<mostek>:<port>`."""
    match = re.fullmatch(r"http://([0-9.]+):([0-9]{1,5})/?", proxy_base or "")
    if not match:
        raise agentcmd.StepError(f"adres proxy pudełka musi mieć postać http://<adres mostka>:<port>: {proxy_base!r}")
    url = f"http://{match.group(1)}:{match.group(2)}"
    no_proxy = box_no_proxy(match.group(1))
    return {"HTTPS_PROXY": url, "HTTP_PROXY": url, "https_proxy": url, "http_proxy": url,
            "NO_PROXY": no_proxy, "no_proxy": no_proxy}


def host_home_dir(slug: str, env=None) -> str:
    """`~/.local/share/tide/box/<slug>/home` — katalog hosta montowany jako `/home/agent`."""
    env = os.environ if env is None else env
    base = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "tide", "box", bs.bp._check_slug(slug, "projekt"), "home")


def lock_path(slug: str, env=None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "tide", "box", bs.bp._check_slug(slug, "projekt"), "devices.lock")


def worktrees_root(repo: str) -> str:
    """`<repo>-worktrees` — ta sama reguła co `cardflow.gitops.worktrees_dir`."""
    return os.path.normpath(repo) + "-worktrees"


def _within(path: str, folder: str) -> bool:
    path, folder = os.path.normpath(path), os.path.normpath(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


@dataclass(frozen=True)
class Device:
    name: str
    source: str
    readonly: bool = False
    is_file: bool = False
    path: str = ""                        # w pudełku; pusta — ta sama co `source`

    def values(self) -> dict:
        values = {"source": self.source, "path": self.path or self.source}
        if self.readonly:
            values["readonly"] = True
        return values


def _device_name(kind: str, extra: str = "") -> str:
    name = DEVICE_PREFIX + kind + (("-" + re.sub(r"[^A-Za-z0-9-]+", "-", extra).strip("-")) if extra else "")
    if len(name) > 60:
        import hashlib
        name = name[:48] + "-" + hashlib.sha1(name.encode()).hexdigest()[:10]
    return name


def _write_secret(path: str, text: str) -> None:
    """Plik 0600 właściciela usługi; stary (albo dowiązanie podłożone przez agenta) jest najpierw usuwany."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def env_file_text(env: dict) -> str:
    """`KLUCZ=wartość` w wierszach (czyta `tide-step` bez `eval`); nowa linia albo NUL w wartości → StepError."""
    lines = []
    for key, value in env.items():
        value = str(value)
        if not _KEY.match(key):
            raise agentcmd.StepError(f"in/env: niepoprawna nazwa zmiennej {key!r}")
        if "\n" in value or "\r" in value or "\0" in value:
            raise agentcmd.StepError(f"in/env: wartość {key} zawiera znak nowej linii")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


class BoxRunner:
    """Krok w pudełku projektu (`box_name(slug)`) albo w kopii karty (`per_card` → `card_box_name(slug, card)`).

    `proxy_base` — `http://<mostek>:<port>`; pusty: proxy uruchomione w tym procesie (`proxy.running()`), a bez
    niego adres mostka z `incus network get` i `box.proxy-port`. `step_env(step)` — dodatkowe zmienne do `in/env`
    (porty #8, atrapy K3 #18). `journal(kind, fields)` — wpis w dzienniku karty (np. „ryzyko K1”)."""

    def __init__(self, settings: BoxSettings, slug: str, *, card: str = "", per_card: bool = False,
                 incus: Incus | None = None, proxy_base: str = "", sandbox: bool = BOX_SANDBOX,
                 step_env: Callable[[agentcmd.Step], dict] | None = None,
                 journal: Callable[[str, dict], None] | None = None, env: dict | None = None,
                 systemd_run: str = SYSTEMD_RUN, systemctl: str = SYSTEMCTL, tide_step=(TIDE_STEP,),
                 popen=subprocess.Popen, sleep=time.sleep):
        self.settings = settings
        self.slug = slug
        self.card = card
        self.box = bs.card_box_name(slug, card) if per_card else bs.box_name(slug)
        self.incus = incus or Incus(settings.project)
        self._proxy_base = proxy_base
        self.sandbox = bool(sandbox)
        self.step_env = step_env
        self.journal = journal
        self.host_env = dict(os.environ if env is None else env)
        self.systemd_run, self.systemctl = systemd_run, systemctl
        self.tide_step = tuple(tide_step)
        self._popen, self._sleep = popen, sleep
        self._risk_logged = False

    # --- środowisko ------------------------------------------------------------------------------

    def proxy_base(self) -> str:
        if self._proxy_base:
            return self._proxy_base
        from . import proxy as bproxy
        running = bproxy.running()
        if running is not None and running.listen and running.port:
            return f"http://{running.listen}:{running.port}"
        address = bproxy.bridge_address(self.settings.bridge)
        if not address:
            raise agentcmd.StepError(f"brak adresu mostka {self.settings.bridge} — krok w pudełku bez proxy odmówiony")
        return f"http://{address}:{self.settings.proxy_port}"

    def base_env(self) -> dict:
        return {"HOME": BOX_HOME, "USER": BOX_USER, "LOGNAME": BOX_USER, "PATH": BOX_PATH,
                "LANG": self.host_env.get("LANG") or "C.UTF-8"}

    def box_step(self, step: agentcmd.Step) -> agentcmd.Step:
        """Krok przepisany na pudełko: środowisko z `/home/agent`, bez list poleceń w `--allowedTools` (zakazy zostają
        wskazówkami w prompcie), Codex bez serwerów MCP hosta (`codex mcp list` hosta nie dotyczy pudełka)."""
        allowed = tuple(t for t in step.allowed_tools if not str(t).startswith("Bash("))
        if step.tool == "claude" and "Bash" not in allowed:
            allowed += ("Bash",)
        return replace(step, base_env=self.base_env(), allowed_tools=allowed, enforce_command_policy=False,
                       codex_mcp=() if step.codex_mcp is None else step.codex_mcp)

    def _background(self, step: agentcmd.Step) -> agentcmd.Background:
        bstep = self.box_step(step)
        bg = agentcmd.background(bstep)
        bg.env.update(proxy_env(self.proxy_base()))
        bg.env["TIDE_CWD"] = bg.cwd
        # Katalog tymczasowy Claude Code leży w /tmp pudełka (zakłada go tide-step), nie na hoście.
        tmp = bg.env.get("CLAUDE_CODE_TMPDIR")
        if tmp:
            bg.dirs = [d for d in bg.dirs if d != tmp]
        if step.tool == "claude":
            self._claude_settings(bstep, bg)
        return bg

    def _claude_settings(self, step: agentcmd.Step, bg: agentcmd.Background) -> None:
        """Ustawienia Claude Code w pudełku (plik w katalogu kroku, nie hosta): piaskownica wg P-2, `denyRead`
        poświadczeń agentów i paczek, bez listy domen (granicą sieci jest proxy Tide), zapis w `.git` (nakładki ro
        pilnują haków i konfiguracji)."""
        inp, out = os.path.join(bg.step_dir, "in"), os.path.join(bg.step_dir, "out")
        path = os.path.join(inp, "settings.json")
        host = json.loads(bg.files[path])
        deny = list(BOX_DENY_READ) + [os.path.join(inp, ENV_FILE)]
        settings = {k: v for k, v in host.items() if k != "sandbox"}
        if step.local is not None and step.local.token_file:
            # apiKeyHelper hosta czyta plik spoza pudełka — w pudełku czyta kopię 0600 w `in/` (poza zasięgiem
            # poleceń agenta przez denyRead).
            token = os.path.join(inp, LOCAL_TOKEN_FILE)
            settings["apiKeyHelper"] = f"cat -- {token}"
            deny.append(token)
        settings["sandbox"] = {
            "enabled": self.sandbox, "failIfUnavailable": self.sandbox, "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "filesystem": {"denyRead": deny, "allowWrite": [out, os.path.join(step.repo, ".git")]},
            "network": {"allowedDomains": []}}
        bg.files[path] = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"

    def env(self, step: agentcmd.Step) -> dict:
        """Środowisko programu w pudełku (bez sekretów `secret_env`): ścieżki `/home/agent`, proxy, `NO_PROXY`."""
        bg = self._background(step)
        env = dict(bg.env)
        if self.step_env is not None:
            env.update(self.step_env(step) or {})
        return env

    # --- urządzenia --------------------------------------------------------------------------------

    def idmap(self) -> str:
        uid, gid = os.getuid(), os.getgid()
        au, ag = self.settings.agent_uid, self.settings.agent_gid
        if uid == gid and au == ag:
            return f"both {uid} {au}"
        return f"uid {uid} {au}\ngid {gid} {ag}"

    def devices_for(self, step: agentcmd.Step) -> list:
        """Urządzenia dyskowe kroku w kolejności montowania: `.git` przed nakładkami, potem worktrees i dom."""
        repo = os.path.realpath(step.repo)
        git = os.path.join(repo, ".git")
        if not os.path.isdir(git):
            raise agentcmd.StepError(f"repozytorium bez katalogu .git: {repo}")
        root = worktrees_root(repo)
        worktree, step_dir = os.path.realpath(step.worktree), os.path.realpath(step.step_dir)
        if not _within(worktree, root) or worktree == root:
            raise agentcmd.StepError(f"worktree kroku w pudełku musi leżeć w {root}: {step.worktree}")
        for sub in ("hooks", "info"):
            # Pusty katalog też dostaje nakładkę ro — inaczej agent założyłby `.git/hooks/*` uruchamiane na hoście.
            os.makedirs(os.path.join(git, sub), exist_ok=True)
        devices = [Device(_device_name("git"), git),
                   Device(_device_name("git-hooks"), os.path.join(git, "hooks"), readonly=True),
                   Device(_device_name("git-config"), os.path.join(git, "config"), readonly=True, is_file=True),
                   Device(_device_name("git-info"), os.path.join(git, "info"), readonly=True)]
        for cfg in sorted(glob.glob(os.path.join(git, "worktrees", "*", "config.worktree"))):
            if os.path.isfile(cfg) and not os.path.islink(cfg):
                name = os.path.basename(os.path.dirname(cfg))
                devices.append(Device(_device_name("wtcfg", name), cfg, readonly=True, is_file=True))
        devices.append(Device(_device_name("worktrees"), root))
        devices.append(Device(_device_name("home"), host_home_dir(self.slug, self.host_env), path=BOX_HOME))
        if not _within(step_dir, root):
            devices.append(Device(_device_name("step", step.step_id), step_dir))
        self._check_sources(devices, repo)
        return devices

    def _check_sources(self, devices: list, repo: str) -> None:
        """Ani główny worktree projektu, ani katalog domowy hosta (ani nic nad nimi) nie trafiają do pudełka."""
        home = os.path.realpath(self.host_env.get("HOME") or os.path.expanduser("~"))
        for dev in devices:
            src = os.path.realpath(dev.source)
            if _within(repo, src) or _within(home, src):
                raise agentcmd.StepError(f"montowanie {src} odsłoniłoby główny worktree albo katalog domowy")
            if _within(src, repo) and not _within(src, os.path.join(repo, ".git")):
                raise agentcmd.StepError(f"montowanie {src} leży w głównym worktree projektu")

    def ensure_devices(self, step: agentcmd.Step) -> list:
        """Dodaje brakujące urządzenia (i `raw.idmap`) pod blokadą projektu; zwraca nazwy dodanych urządzeń."""
        wanted = self.devices_for(step)
        path = lock_path(self.slug, self.host_env)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return self._ensure_locked(step, wanted)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _ensure_locked(self, step: agentcmd.Step, wanted: list) -> list:
        present = self.incus.device_show(self.box)
        taken = {str(d.get("path", "")) for d in present.values() if isinstance(d, dict)}
        missing = []
        for dev in wanted:
            if dev.name in present:
                continue
            if dev.path == BOX_HOME and BOX_HOME in taken:
                continue        # builder podpiął już wolumen `<pudełko>-home` na /home/agent
            missing.append(dev)
        idmap = self.idmap()
        idmap_ok = self.incus.config_get(self.box, "raw.idmap") == idmap
        if not missing and idmap_ok:
            return []
        stop = not idmap_ok or any(d.is_file for d in missing)
        inst = self.incus.instance(self.box)
        if inst is None:
            raise agentcmd.StepError(f"pudełko {self.box} nie istnieje")
        running = str(inst.get("status", "")).lower() == "running"
        if stop and running:
            busy = [s for s in bs.running_steps(self.slug, self.box) if s != step.step_id]
            if busy:
                raise agentcmd.StepError(f"pudełko {self.box} wymaga zatrzymania na dodanie montowań, a biegną w nim "
                                         f"kroki: {', '.join(busy)}")
            self.incus.stop(self.box)
        try:
            if not idmap_ok:
                self.incus.config_set(self.box, {"raw.idmap": idmap})
            for dev in missing:
                if dev.path == BOX_HOME:
                    os.makedirs(dev.source, mode=0o700, exist_ok=True)
                    os.chmod(dev.source, 0o700)
                self.incus.device_add(self.box, dev.name, "disk", dev.values())
        finally:
            if stop and running:
                self.incus.start(self.box)
        return [d.name for d in missing]

    # --- start i zatrzymanie -----------------------------------------------------------------------

    def unit_for(self, bg: agentcmd.Background) -> str:
        return bg.unit + ".service"

    def exec_prefix(self, box: str = "", cwd: str = "") -> list:
        args = ["exec", box or self.box, "-T", "--user", str(self.settings.agent_uid),
                "--group", str(self.settings.agent_gid)]
        if cwd:
            args += ["--cwd", cwd]
        return self.incus._argv(args + ["--"])

    def argv(self, bg: agentcmd.Background) -> list:
        runtime = f"/run/user/{self.settings.agent_uid}"
        return [*self.exec_prefix(cwd=bg.cwd),
                "/usr/bin/env", "-i", f"TIDE_STEP={bg.step_dir}", f"XDG_RUNTIME_DIR={runtime}",
                self.systemd_run, "--user", f"--unit={self.unit_for(bg)}", "--collect", "--wait", "--quiet",
                "-p", "CPUWeight=40", "-p", "IOWeight=40", f"--working-directory={bg.cwd}",
                f"--setenv=TIDE_STEP={bg.step_dir}", "--",
                *self.tide_step, *bg.argv]

    def start(self, step: agentcmd.Step, *, before_start: Callable[[agentcmd.Background], None] | None = None
              ) -> Started:
        bg = self._background(step)
        self.ensure_devices(step)
        bg.prepare()
        if before_start is not None:
            before_start(bg)
        env = dict(bg.env)
        for path in bg.token_files:
            launch.read_token(path)
        for name, path in bg.secret_env.items():
            env[name] = launch.read_token(path)
        if step.tool == "claude" and step.local is not None and step.local.token_file:
            _write_secret(os.path.join(bg.step_dir, "in", LOCAL_TOKEN_FILE), launch.read_token(step.local.token_file))
        if self.step_env is not None:
            env.update(self.step_env(step) or {})
        _write_secret(os.path.join(bg.step_dir, "in", ENV_FILE), env_file_text(env))
        for path in (bg.exit_file, bg.exit_file + ".tmp"):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        if step.tool == "claude" and not self.sandbox and not self._risk_logged and self.journal is not None:
            self.journal("box.risk", {"risk": "K1", "box": self.box, "step": step.step_id,
                                      "text": "ryzyko K1: piaskownica Claude Code w pudełku wyłączona (P-2) — "
                                              "kod w pudełku czyta to, co agent"})
            self._risk_logged = True
        os.makedirs(os.path.dirname(bg.log_file), exist_ok=True)
        with open(os.path.join(bg.step_dir, "out", EXEC_LOG), "ab") as log:
            proc = self._popen(self.argv(bg), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               env=self._client_env(), start_new_session=True)
        return Started(bg, proc)

    def _client_env(self) -> dict:
        """Środowisko klienta `incus` na hoście — bez zmiennych kroku (nic z `in/env`)."""
        return {k: v for k, v in self.host_env.items() if k in ("HOME", "PATH", "LANG", "USER", "XDG_CONFIG_HOME",
                                                                "XDG_RUNTIME_DIR", "INCUS_DIR", "INCUS_CONF")
                or k.startswith("TIDE_FAKE") or k == "TIDE_INCUS_BIN"}

    def _systemctl(self, box: str, *args) -> int:
        argv = [*self.exec_prefix(box), "/usr/bin/env", "-i", f"XDG_RUNTIME_DIR=/run/user/{self.settings.agent_uid}",
                self.systemctl, "--user", *args]
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              env=self._client_env()).returncode

    def alive(self, step: dict) -> bool:
        unit, box = self._unit_box(step)
        return self._systemctl(box, "is-active", "--quiet", unit) == 0

    def _unit_box(self, step: dict) -> tuple:
        unit = str(step.get("unit") or "")
        if not unit.endswith(".service"):
            unit += ".service"
        box = str(step.get("box") or self.box)
        if not _UNIT.match(unit):
            raise agentcmd.StepError(f"niepoprawna jednostka kroku: {unit!r}")
        if not _BOX.match(box):
            raise agentcmd.StepError(f"niepoprawna nazwa pudełka: {box!r}")
        return unit, box

    def kill(self, step: dict, *, proc: object = None, grace: float = 10.0) -> bool:
        """`systemctl --user kill <jednostka>` w pudełku (SIGTERM do całej cgroup; `tide-step` zapisze kod), po
        `grace` SIGKILL. Klient `incus exec` (`proc`) kończy się razem z jednostką (`--wait`). True, gdy nic nie żyje."""
        if not step.get("unit"):
            return proc is None or _stop_client(proc, grace)
        unit, box = self._unit_box(step)
        self._systemctl(box, "kill", "--signal=SIGTERM", unit)
        deadline = time.monotonic() + max(0.0, grace)
        while True:
            if self._systemctl(box, "is-active", "--quiet", unit) != 0:
                break
            if time.monotonic() >= deadline:
                self._systemctl(box, "kill", "--signal=SIGKILL", unit)
                break
            self._sleep(0.2)
        dead = self._systemctl(box, "is-active", "--quiet", unit) != 0
        if proc is not None:
            _stop_client(proc, 2.0)
        return dead


def _stop_client(proc, grace: float) -> bool:
    wait = getattr(proc, "wait", None)
    if wait is None:
        return True
    try:
        wait(timeout=grace)
        return True
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            return False
        return True


# ------------------------------------------------------------ tryb próbny (ręcznie, poza piaskownicą)

def _demo(argv: list) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tide_daemon.box.runner --demo",
                                     description="Uruchamia polecenie w pudełku projektu tak jak krok (BoxRunner).")
    parser.add_argument("slug")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    parser.add_argument("--worktree", default=os.getcwd(), help="worktree w <repo>-worktrees (domyślnie bieżący)")
    parser.add_argument("--card", default="", help="kopia pudełka karty (box: per-card)")
    parser.add_argument("--push-tide-step", action="store_true",
                        help="wgraj tide-step do pudełka (/opt/tide/tide-step) — pudełko prototypowe bez obrazu #16")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("brak polecenia")
    from . import settings as bsettings
    worktree = os.path.realpath(args.worktree)
    common = subprocess.run(["git", "-C", worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, check=True).stdout.strip()
    repo = os.path.dirname(common)
    stamp = time.strftime("%Y%m%d%H%M%S")
    step_id = f"demo-{stamp}"
    step_dir = f"{worktree}.{step_id}.step"
    # Znacznik ze zmiennej środowiska (nie z argv — ten widać w `ps`), żeby skrypt próby mógł go szukać.
    marker = os.environ.get("TIDE_DEMO_MARKER") or "tide-demo-" + secrets.token_hex(8)
    settings = bsettings.load()
    tide_step = (TIDE_STEP,)
    if args.push_tide_step:
        remote = "/opt/tide/tide-step"
        box = bs.card_box_name(args.slug, args.card) if args.card else bs.box_name(args.slug)
        Incus(settings.project).file_push(TIDE_STEP_SCRIPT, box, remote)
        tide_step = ("/bin/sh", remote)
    runner = BoxRunner(settings, args.slug, card=args.card, per_card=bool(args.card), tide_step=tide_step,
                       step_env=lambda _s: {"TIDE_DEMO_SECRET": marker})
    step = agentcmd.Step(tool="claude", binary="claude", prompt="", worktree=worktree, repo=repo, step_dir=step_dir,
                         step_id=step_id)

    def use_command(bg):
        bg.argv = list(command)

    bg, proc = runner.start(step, before_start=use_command)
    print(f"pudełko: {runner.box}\njednostka: {runner.unit_for(bg)}\nkatalog kroku: {step_dir}\n"
          f"znacznik w in/env (nie może być w `ps -o args` hosta): TIDE_DEMO_SECRET={marker}", flush=True)
    code = proc.wait()
    try:
        with open(bg.log_file, encoding="utf-8", errors="replace") as fh:
            sys.stdout.write(fh.read())
    except OSError:
        pass
    print(f"[kod klienta incus {code}, exit-code kroku: {_read(bg.exit_file)}]")
    return code


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "brak"


if __name__ == "__main__":
    if sys.argv[1:2] != ["--demo"]:
        print("użycie: python3 -m tide_daemon.box.runner --demo <slug> [--worktree W] [--card K-N] -- <polecenie>",
              file=sys.stderr)
        sys.exit(2)
    sys.exit(_demo(sys.argv[2:]))
