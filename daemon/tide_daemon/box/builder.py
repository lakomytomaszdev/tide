"""Budowa pudełka projektu z zatwierdzonego profilu, kopia per karta, status, reset, usunięcie i zatwierdzenie.

Kolejność budowy (`build`):
 1. kopia obrazu bazowego — `incus init <base> <pudełko>` (launch bez startu) z `limits.cpu`, `limits.memory`,
    `limits.memory.swap=false` z profilu i `security.nesting=true` tylko dla `docker.enabled`; nigdy
    `security.privileged`; rozmiar dysku głównego `resources.disk`; wolumen `<pudełko>-home` na `/home/agent`
    (urządzenie dyskowe, więc reset z migawki go nie rusza),
 2. filtrowanie NIC (`security.mac_filtering`, `security.ipv4_filtering`, `security.ipv6_filtering` wg
    ustawień) — zanim pudełko wystartuje i wyśle pierwszy pakiet,
 3. start, adres IP pudełka, `set_box_policy(ip, build_domains(profile), host-ollama, dziennik odmów)` w proxy
    (#5) — przed pierwszym `incus exec`,
 4. szablony stosów wg `stacks` (`box/stacks/<stos>.sh`, kopiowane `incus file push`, powstają w #29),
 5. `apt-get install` (pakiety szablonów i profilu), 6. `setup`, 7. `docker pull` (gdy `docker.enabled`),
 8. `verify` (nieudane → „zbudowane z ostrzeżeniem”), 9. migawka `clean` z odciskiem przepisu.

Każde `incus exec` budowy biegnie jako root pudełka z `HTTPS_PROXY`/`HTTP_PROXY` na proxy Tide: adres i port
pochodzą z uruchomionego proxy (`proxy.running()` — nasłuch na adresie mostka z `box.bridge`, port `box.proxy-port`),
więc proxy i builder mają jedno źródło. Brak uruchomionego proxy (albo brak jego adresu) to błąd przed pierwszym
poleceniem `incus`. Po migawce polityka budowy jest zdejmowana (`clear_box_policy`). Budowa nie czyta stdin.
Odcisk przepisu pochodzi wyłącznie z `profile.recipe_stamp` i jest zapisany w kluczu `user.tide.recipe`
instancji tuż przed migawką, więc migawka `clean` (i każda kopia z niej) niesie go w swojej konfiguracji.
Opis: docs/tide-daemon.md (Budowa pudełka).
"""
import hashlib
import json
import os
import time
from dataclasses import dataclass, field

from . import profile as bp
from . import state as bs
from . import templates
from .incus import Incus, IncusError
from . import proxy as bproxy
from .settings import BoxSettings

SCRIPTS_DIR = templates.STACKS_DIR      # `box/stacks/<stos>.sh` obok szablonów danych (#29)
REMOTE_STACKS = "/opt/tide/stacks"       # gdzie w pudełku lądują skrypty szablonów
BUILD_NO_PROXY = "localhost,127.0.0.1,::1"


class BuildError(RuntimeError):
    pass


class BoxBusy(RuntimeError):
    pass


class ProxyMissing:
    """Polityka, gdy proxy Tide (#5) nie biegnie w tym procesie: budowa bez proxy jest błędem."""
    listen = ""
    port = 0

    def set_box_policy(self, ip, domains, ollama, denied_log_path):
        raise BuildError("proxy Tide (#5) nie jest uruchomione — budowa bez proxy jest niedozwolona")

    def clear_box_policy(self, ip):
        pass


def proxy_url(policy) -> str:
    """`http://<adres mostka>:<port>` z uruchomionego proxy (`listen`, `port`); brak → `BuildError`."""
    listen, port = getattr(policy, "listen", ""), getattr(policy, "port", 0)
    if not listen or not port:
        raise BuildError("proxy Tide (#5) nie jest uruchomione (brak adresu mostka) — budowa bez proxy jest "
                         "niedozwolona")
    return f"http://{listen}:{port}"


@dataclass
class BuildResult:
    box: str
    hash: str
    warning: bool
    failed_verify: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    duration: float = 0.0


def templates_version(scripts_dir: str | None = None) -> str:
    """Skrót wszystkich szablonów stosów (dane `*.yaml` i skrypty `*.sh`) — wejście hasha przepisu."""
    digest = hashlib.sha256()
    for folder in dict.fromkeys([templates.STACKS_DIR, scripts_dir or SCRIPTS_DIR]):
        try:
            names = sorted(n for n in os.listdir(folder) if n.endswith((".yaml", ".sh")))
        except OSError:
            continue
        for name in names:
            with open(os.path.join(folder, name), "rb") as fh:
                digest.update(name.encode() + b"\0" + fh.read() + b"\0")
    return digest.hexdigest()


def state_dir(slug: str) -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "tide", "box", bp._check_slug(slug, "projekt"))


class Builder:
    def __init__(self, settings: BoxSettings, incus: Incus | None = None, policy=None, clock=time.monotonic,
                 wall=time.time, sleep=time.sleep, scripts_dir: str | None = None):
        self.settings = settings
        self.incus = incus or Incus(settings.project)
        # Polityka i adres proxy: jawnie podane (testy) albo proxy uruchomione w tym procesie (PT-168).
        self.policy = policy or bproxy.running() or ProxyMissing()
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.scripts_dir = scripts_dir or SCRIPTS_DIR

    # --- profil i hash ---------------------------------------------------------------------------

    def load_profile(self, slug: str) -> dict:
        got = bp.load_project(slug)
        if got is None:
            raise BuildError(f"projekt {slug} nie ma zatwierdzonego profilu (tide box approve {slug} --file …)")
        return got

    def stamp(self, profile: dict) -> dict:
        return bp.recipe_stamp(profile, templates_version(self.scripts_dir), self.settings.agents_version)

    # --- budowa ----------------------------------------------------------------------------------

    def build(self, slug: str, profile: dict | None = None) -> BuildResult:
        profile = self.load_profile(slug) if profile is None else bp.validate(profile, "project")
        full = bp.with_defaults(profile)
        if full["type"] == "vm":
            raise BuildError("type: vm — nieobsługiwane w tej wersji (tylko kontener)")
        proxy = proxy_url(self.policy)
        missing = [s for s in full["stacks"] if not os.path.isfile(os.path.join(self.scripts_dir, f"{s}.sh"))]
        if missing:
            raise BuildError("brak skryptów szablonów stosów (#29): " + ", ".join(f"{s}.sh" for s in missing))
        domains = list(templates.build_domains(full))
        name = bs.box_name(slug)
        if bs.running_steps(slug, name):
            raise BoxBusy(f"w pudełku {name} biegną kroki: {', '.join(bs.running_steps(slug, name))}")

        started = self.clock()
        bs.record(slug, "box.build", name, self.wall, phase="start")
        steps: list = []
        os.makedirs(state_dir(slug), exist_ok=True)
        log_path = os.path.join(state_dir(slug), "build.log")
        try:
            with open(log_path, "w", encoding="utf-8") as log:
                self.incus.log = log
                result = self._build(slug, name, full, proxy, domains, steps)
        except Exception as exc:
            bs.record(slug, "box.build", name, self.wall, phase="end", result="error", error=str(exc),
                      steps=steps, duration=round(self.clock() - started, 3))
            raise
        finally:
            self.incus.log = None
        result.duration = round(self.clock() - started, 3)
        bs.record(slug, "box.build", name, self.wall, phase="end", result="ok", steps=steps,
                  duration=result.duration, hash=result.hash)
        return result

    def _build(self, slug, name, full, proxy, domains, steps) -> BuildResult:
        if self.incus.instance(name) is not None:
            self.incus.delete(name, force=True)
        # 1. kopia obrazu bazowego z limitami z profilu
        steps.append("base")
        res = full["resources"]
        config = {"limits.cpu": res["cpu"], "limits.memory": res["memory"], "limits.memory.swap": False}
        if full["docker"]["enabled"]:
            config["security.nesting"] = True
        self.incus.init(full.get("base") or self.settings.base_image, name, config)
        self.incus.device_override(name, "root", {"size": res["disk"]})
        self._attach_home(name)
        # 2. filtrowanie NIC przed startem
        steps.append("nic")
        self.apply_nic_filter(name)
        # 3. start, adres i polityka proxy przed pierwszym exec
        self.incus.start(name)
        ip = self._wait_ip(name)
        denied = os.path.join(state_dir(slug), "build-denied.log")
        self.policy.set_box_policy(ip, domains, bool(full["network"].get("host-ollama")), denied)
        try:
            return self._provision(slug, name, full, proxy, steps)
        finally:
            # polityka budowy nie zostaje w proxy po budowie (kroki dostaną politykę karty, #18)
            self.policy.clear_box_policy(ip)

    def _provision(self, slug, name, full, proxy, steps) -> BuildResult:
        env = {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy,
               "NO_PROXY": BUILD_NO_PROXY, "no_proxy": BUILD_NO_PROXY, "DEBIAN_FRONTEND": "noninteractive"}
        if full.get("node", {}).get("version"):
            env["TIDE_NODE_VERSION"] = full["node"]["version"]
        if full.get("dotnet", {}).get("sdk"):
            env["TIDE_DOTNET_SDK"] = " ".join(full["dotnet"]["sdk"])
        # 4. szablony stosów
        for stack in full["stacks"]:
            steps.append(f"stack:{stack}")
            remote = f"{REMOTE_STACKS}/{stack}.sh"
            self.incus.file_push(os.path.join(self.scripts_dir, f"{stack}.sh"), name, remote)
            self._run(name, ["bash", remote], env, f"szablon {stack}")
        # 5. apt
        packages = list(templates.build_apt(full))
        if packages:
            steps.append("apt")
            self._run(name, ["apt-get", "update"], env, "apt-get update")
            self._run(name, ["apt-get", "install", "-y", "--no-install-recommends", *packages], env, "apt-get install")
        # 6. setup
        for command in full["setup"]:
            steps.append("setup")
            self._run(name, ["bash", "-c", command], env, f"setup: {command}")
        # 7. docker pull
        if full["docker"]["enabled"]:
            for image in templates.build_images(full):
                steps.append(f"docker:{image}")
                self._run(name, ["docker", "pull", image], env, f"docker pull {image}")
        # 8. verify
        failed = []
        if full["verify"]:
            steps.append("verify")
            v_start = self.clock()
            for command in full["verify"]:
                if self.incus.exec(name, ["bash", "-c", command], env) != 0:
                    failed.append(command)
            bs.record(slug, "box.verify", name, self.wall, result="ok" if not failed else "fail", failed=failed,
                      duration=round(self.clock() - v_start, 3))
        # 9. migawka clean z odciskiem przepisu
        steps.append("snapshot")
        stamp = self.stamp(full)
        self.incus.config_set(name, {bs.RECIPE_KEY: json.dumps(stamp, sort_keys=True, separators=(",", ":"))})
        snap_start = self.clock()
        self.incus.snapshot_create(name, bs.SNAPSHOT)
        bs.record(slug, "box.ready", name, self.wall, hash=stamp["hash"], warning=bool(failed),
                  duration=round(self.clock() - snap_start, 3))
        return BuildResult(name, stamp["hash"], bool(failed), failed, list(steps))

    def _run(self, name, command, env, what):
        code = self.incus.exec(name, command, env)
        if code != 0:
            raise BuildError(f"{what}: kod {code} (dziennik: build.log)")

    def _wait_ip(self, name) -> str:
        deadline = self.clock() + self.settings.ip_wait_seconds
        while True:
            ip = instance_ip(self.incus.instance(name), self.settings.nic)
            if ip:
                return ip
            if self.clock() >= deadline:
                raise BuildError(f"pudełko {name} nie dostało adresu IPv4 na {self.settings.nic}")
            self.sleep(0.5)

    def _attach_home(self, name):
        """Wolumen `<pudełko>-home` na `/home/agent`; nowy wolumen od razu należy do użytkownika `agent`
        (`initial.uid/gid/mode`), bo pusty wolumen w pudełku nieuprzywilejowanym byłby `root:root`."""
        volume = bs.home_volume(name)
        if not self.incus.volume_exists(self.settings.pool, volume):
            self.incus.volume_create(self.settings.pool, volume, {
                "initial.uid": self.settings.agent_uid, "initial.gid": self.settings.agent_gid,
                "initial.mode": bs.HOME_MODE})
        devices = self.incus.device_show(name)
        values = {"pool": self.settings.pool, "source": volume, "path": bs.HOME_PATH}
        if "home" in devices:
            self.incus.device_set(name, "home", values)
        else:
            self.incus.device_add(name, "home", "disk", values)

    def apply_nic_filter(self, name):
        """Filtrowanie NIC na karcie pudełka: `set`, gdy karta jest lokalna, `override`, gdy z profilu."""
        flags = bs.nic_flags(self.settings)
        if self.settings.nic in self.incus.device_show(name):
            self.incus.device_set(name, self.settings.nic, flags)
        else:
            self.incus.device_override(name, self.settings.nic, flags)

    # --- kopia per karta -------------------------------------------------------------------------

    def copy_for_card(self, slug: str, card: str) -> str:
        """Kopia `<slug>-<karta>` z migawki `clean` pudełka projektu (idempotentna).

        `incus copy` z migawki daje instancję **bez migawek**, więc po podpięciu `/home/agent` i filtrowaniu NIC
        kopia dostaje własną migawkę `clean` — `reset --card` przywraca ją razem z tymi urządzeniami."""
        source = bs.box_name(slug)
        target = bs.card_box_name(slug, card)
        if self.incus.instance(target) is not None:
            return target
        snaps = [s.get("name") for s in self.incus.snapshot_list(source)] if self.incus.instance(source) else []
        if bs.SNAPSHOT not in snaps:
            raise BuildError(f"pudełko projektu {source} nie ma migawki {bs.SNAPSHOT} (tide box build {slug})")
        started = self.clock()
        self.incus.copy(f"{source}/{bs.SNAPSHOT}", target)
        self.incus.config_set(target, {bs.COPY_OF_KEY: source, bs.CARD_KEY: str(card).upper()})
        self._attach_home(target)
        self.apply_nic_filter(target)
        self.incus.snapshot_create(target, bs.SNAPSHOT)
        stamp = bs.parse_stamp(self.incus.config_get(target, bs.RECIPE_KEY))
        recipe = stamp.get("hash", "") if isinstance(stamp, dict) else (stamp or "")
        _, warning = bs.journal_state(slug, source)     # ostrzeżenie `verify` pudełka projektu przechodzi na kopię
        bs.record(slug, "box.ready", target, self.wall, card=str(card).upper(), copy_of=source, hash=recipe,
                  warning=bool(warning), duration=round(self.clock() - started, 3))
        return target

    # --- status ----------------------------------------------------------------------------------

    def status(self, slug: str, card: str | None = None, profile: dict | None = None) -> bs.BoxStatus:
        name = bs.card_box_name(slug, card) if card else bs.box_name(slug)
        jstate, warning = bs.journal_state(slug, name)
        st = bs.BoxStatus(slug=slug, box=name, state=jstate, journal_state=jstate, warning=warning,
                          steps=bs.running_steps(slug, name))
        try:
            profile = bp.load_project(slug) if profile is None else profile
        except bp.ProfileError as exc:
            st.error = f"profil: {exc}"
            profile = None
        try:
            inst = self.incus.instance(name)
        except (IncusError, OSError, ValueError) as exc:
            st.error = f"incus: {exc}"
            return st
        if inst is None:
            st.state = bs.ABSENT
            return st
        st.exists = True
        st.running = str(inst.get("status", "")).lower() == "running"
        st.nic_filtering, st.nic_detail = bs.nic_compliant(self.incus.device_show(name), self.settings)
        stamp = bs.parse_stamp(self.incus.config_get(name, bs.RECIPE_KEY))
        st.recipe_hash = stamp.get("hash", "") if isinstance(stamp, dict) else (stamp or "")
        if profile is not None:
            current = self.stamp(profile)
            st.profile_hash = current["hash"]
            if stamp is None:
                st.diff = ["recipe"]
            else:
                st.diff = bp.diff(profile, stamp, templates_version(self.scripts_dir), self.settings.agents_version)
        if not st.nic_filtering:
            st.state = bs.NONCOMPLIANT
        elif jstate in (bs.BUILDING, bs.RESETTING, bs.FAILED):
            st.state = jstate
        elif st.diff:
            st.state = bs.STALE
        elif not st.running and jstate != bs.IN_USE:
            st.state = bs.STOPPED
        elif jstate in (bs.ABSENT, bs.STOPPED):
            st.state = bs.CLEAN
        return st

    # --- reset i usunięcie -----------------------------------------------------------------------

    def reset(self, slug: str, card: str | None = None) -> float:
        name = bs.card_box_name(slug, card) if card else bs.box_name(slug)
        steps = bs.running_steps(slug, name)
        if steps:
            raise BoxBusy(f"w pudełku {name} biegną kroki: {', '.join(steps)} — reset odmówiony")
        if self.incus.instance(name) is None:
            raise BuildError(f"pudełko {name} nie istnieje")
        started = self.clock()
        self.incus.snapshot_restore(name, bs.SNAPSHOT)
        duration = round(self.clock() - started, 3)
        bs.record(slug, "box.reset", name, self.wall, result="ok", duration=duration)
        return duration

    def destroy(self, slug: str) -> list:
        """Usuwa pudełko projektu i wszystkie jego kopie per karta (z wolumenami `/home/agent`)."""
        source = bs.box_name(slug)
        victims = [inst["name"] for inst in self.incus.list()
                   if inst.get("name") == source
                   or (inst.get("config") or {}).get(bs.COPY_OF_KEY) == source]
        busy = {v: bs.running_steps(slug, v) for v in victims if bs.running_steps(slug, v)}
        if busy:
            raise BoxBusy("biegną kroki: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in busy.items()))
        victims.sort(key=lambda v: v == source)      # najpierw kopie, potem pudełko projektu
        for victim in victims:
            self.incus.delete(victim, force=True)
            self.incus.volume_delete(self.settings.pool, bs.home_volume(victim), check=False)
            bs.record(slug, "box.destroy", victim, self.wall)
        return victims


def instance_ip(inst, nic: str) -> str:
    if not isinstance(inst, dict):
        return ""
    network = ((inst.get("state") or {}).get("network") or {}).get(nic) or {}
    for addr in network.get("addresses") or []:
        if addr.get("family") == "inet" and addr.get("scope", "global") == "global":
            return str(addr.get("address", ""))
    return ""


# ------------------------------------------------------------ zatwierdzenie z wiersza poleceń

def approve(slug: str, text: str, accept_red=(), clock=time.time) -> dict:
    """Waliduje profil (schemat #2), zapisuje go w Tablicy i dopisuje `box.approved source=cli`.

    Pozycja czerwona przechodzi wyłącznie wymieniona w `accept_red` (jawna zgoda z wiersza poleceń).
    """
    profile = bp.parse(text, "project")
    known = templates.known_domains()
    red = bp.red_items(profile, known)
    accepted = [a if a == "network.full" or a.startswith("pack:") else bp.normalize_host(a) for a in accept_red]
    extra = [a for a in accepted if a not in red]
    if extra:
        raise bp.ProfileError([f"--accept-red {a}: pozycji nie ma w profilu albo nie jest czerwona" for a in extra])
    missing = [r for r in red if r not in accepted]
    if missing:
        raise bp.ProfileError([f"pozycja czerwona {r!r} wymaga --accept-red {r}" for r in missing])
    path = bp.save_project(slug, profile)
    bs.record(slug, "box.approved", bs.box_name(slug), clock, source="cli", accepted_red=accepted, path=path)
    return profile
