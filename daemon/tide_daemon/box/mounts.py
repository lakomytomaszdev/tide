"""Jedyny właściciel urządzeń wejść v1; paczki → zgoda → urządzenia.

Nie naprawia nieznanych urządzeń i nie kopiuje paczek. Cały dysk projektu .git
jest ro; tylko objects/refs/logs mają osobne źródła rw. Nazwa urządzenia nie
stanowi dowodu jego pochodzenia: porównujemy pełny efektywny zestaw Incusa.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import stat
from types import SimpleNamespace

from . import exposure, home, packs, state as bs
from ..tasks import agentcmd

CONTRACT = 2


def plan(repositories, project, env=None):
    from .runner import host_home_dir
    result = []
    for repo in repositories:
        for suffix, mode, kind in (("/.git", "ro", "git"),
                                   ("/.git/objects", "rw", "git-objects"),
                                   ("/.git/refs", "rw", "git-refs"),
                                   ("/.git/logs", "rw", "git-logs"),
                                   ("-worktrees", "rw", "workspaces")):
            result.append({"source": repo + suffix, "target": repo + suffix, "mode": mode, "kind": kind})
    result.append({"source": host_home_dir(project, env), "target": "/home/agent", "mode": "rw", "kind": "home"})
    return result


def device_name(mount):
    return "tide-v2-" + hashlib.sha256((mount["source"] + "\0" + mount["target"]).encode()).hexdigest()[:24]


def values(mount):
    return {"type": "disk", "source": mount["source"], "path": mount["target"],
            **({"readonly": "true"} if mount["mode"] == "ro" else {})}


def equal(actual, wanted):
    actual = dict(actual)
    if actual.get("readonly") in (False, "false"):
        actual.pop("readonly")
    if actual.get("readonly") is True:
        actual["readonly"] = "true"
    return actual == wanted


def check_tree(source):
    """Hardlink do hosta ujawniałby inode mimo poprawnej ścieżki mounta."""
    for root, folders, files in os.walk(source, followlinks=False):
        for name in folders + files:
            info = os.lstat(os.path.join(root, name))
            if stat.S_ISLNK(info.st_mode):
                continue  # W pudełku rozwiązuje się w jego przestrzeni, nie w hoście.
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise agentcmd.StepError("hardlink w źródle urządzenia")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise agentcmd.StepError("plik specjalny w źródle urządzenia")


class ProjectMounts:
    def __init__(self, runner):
        self.runner = runner
        self.env = runner.host_env
        self.project = runner.slug
        self.incus = runner.incus

    @contextmanager
    def access(self, step):
        from ..tasks.inputs import Selection
        selection = step.exposure_inputs
        if (type(selection) is not Selection or selection.project != self.project or
                type(selection.snapshot_ids) is not tuple or
                any(not isinstance(i, str) for i in selection.snapshot_ids) or
                len(set(selection.snapshot_ids)) != len(selection.snapshot_ids)):
            raise agentcmd.StepError("PT-264: wymagany hostowy wybór wersji manifestu projektu")
        try:
            record = exposure.current(self.project, self.env)
            if not record or record["version"] != selection.version:
                raise exposure.ExposureError("brak aktualnej zgody projektu")
            ids = tuple(i["snapshot"]["id"] for i in record["manifest"]["inputs"])
            if not set(selection.snapshot_ids) <= set(ids):
                raise exposure.ExposureError("wybór wejść poza manifestem")
            with packs.snapshots_access(self.project, ids, self.env) as copies:
                with exposure.approved_access(self.project, selection.version, self.env) as manifest:
                    if manifest.get("mountContract") != CONTRACT:
                        raise exposure.ExposureError("PT-264: stary kontrakt montowań; wymagana nowa zgoda projektu")
                    if (step.repo not in manifest["repositories"] or os.path.realpath(step.repo) != step.repo or
                            not step.worktree.startswith(step.repo + "-worktrees/") or
                            not step.step_dir.startswith(step.repo + "-worktrees/")):
                        raise exposure.ExposureError("krok poza jawnym projektem")
                    from ..git_sandbox import repository
                    exposure._path(step.worktree)
                    exposure._path(step.step_dir)
                    if not os.path.isdir(step.worktree):
                        raise exposure.ExposureError("PT-264: brak istniejącego worktree kroku")
                    location = repository(step.worktree)
                    if not location or location[0] != step.repo:
                        raise exposure.ExposureError("PT-264: worktree nie odpowiada administracji projektu")
                    actual = sorted((json.loads(json.dumps(p.to_dict())) for p in copies), key=lambda p: p["id"])
                    approved = sorted((i["snapshot"] for i in manifest["inputs"]), key=lambda p: p["id"])
                    if actual != approved:
                        raise exposure.ExposureError("zmienione kopie wejść")
                    expected = plan(manifest["repositories"], self.project, self.env)
                    expected += [i["mount"] for i in manifest["inputs"]]
                    if expected != manifest["mounts"]:
                        raise exposure.ExposureError("niezgodny plan urządzeń manifestu")
                    if self.runner.extra_devices or step.read_dirs or step.context_dirs:
                        raise exposure.ExposureError("dodatkowe urządzenia/katalogi wymagają manifestu projektu")
                    state = self.env.get("XDG_STATE_HOME") or os.path.join(self.env.get("HOME", ""), ".local/state")
                    protected = os.path.join(state, "tide")
                    hosthome = self.env.get("HOME") or os.path.expanduser("~")
                    runtime = os.path.dirname(os.path.dirname(__file__))
                    for mount in expected:
                        source = exposure._path(mount["source"])
                        if exposure._within(hosthome, source):
                            raise exposure.ExposureError("montowanie odsłoniłoby home hosta")
                        if mount["mode"] == "rw" and exposure._within(runtime, source):
                            raise exposure.ExposureError("zapisywalny kod usługi; wymagane zasoby instalacji poza projektem")
                        if mount["kind"] != "pack" and (exposure._within(protected, source) or
                                                         exposure._within(source, protected)):
                            raise exposure.ExposureError("montowanie odsłoniłoby chroniony stan hosta")
                    # Blokada zgody szereguje także urządzenia. Nie otwieramy
                    # drugi raz tego samego flock (samozakleszczenie).
                    yield manifest
        except agentcmd.StepError:
            raise
        except (exposure.ExposureError, packs.PackError, OSError, ValueError) as exc:
            raise agentcmd.StepError("manifest projektu: " + str(exc)) from exc

    def devices(self, step):
        from .runner import Device
        with self.access(step) as manifest:
            return [Device(device_name(m), m["source"], m["mode"] == "ro", path=m["target"])
                    for m in manifest["mounts"]]

    def _busy(self, box):
        if self.runner.mount_steps is not None:
            return self.runner.mount_steps(box)
        from .lifecycle import active_steps
        return active_steps(SimpleNamespace(incus=self.incus, settings=self.runner.settings), self.project, box)

    def inspect(self, box, wanted):
        inst = self.incus.instance(box)
        if inst is None:
            raise agentcmd.StepError("brak konkretnego pudełka projektu")
        config = inst.get("expanded_config", inst.get("config", {}))
        if any(value for key, value in config.items() if key.startswith("raw.") and key != "raw.idmap") or str(
                config.get("security.privileged", "false")).lower() != "false":
            raise agentcmd.StepError("nieznane uprawnienia lub surowe montowania pudełka")
        devices = inst.get("expanded_devices")
        if not isinstance(devices, dict):
            raise agentcmd.StepError("brak pełnej listy urządzeń pudełka")
        missing = dict(wanted)
        for name, entry in devices.items():
            if entry.get("type") == "disk" and entry.get("path") == "/":
                if entry.get("source") or entry.get("pool") != self.runner.settings.pool:
                    raise agentcmd.StepError("nieznany rootfs pudełka")
                continue
            if entry.get("type") == "nic" and name == self.runner.settings.nic:
                # Szczegółowy odbiór sieci jest w PT-265; żadne dodatkowe NIC nie są tolerowane.
                if entry.get("parent", entry.get("network")) != self.runner.settings.bridge:
                    raise agentcmd.StepError("obcy mostek pudełka")
                if not all(str(entry.get(key, "")).lower() == "true"
                           for key, required in bs.nic_flags(self.runner.settings).items() if required):
                    raise agentcmd.StepError("brak wymaganego filtrowania NIC")
                continue
            expected_name = name
            if name in ("home", "tide-home") and entry.get("path") == "/home/agent":
                expected_name = next((n for n, d in wanted.items() if d["path"] == "/home/agent"), name)
            if expected_name not in missing or not equal(entry, wanted[expected_name]):
                raise agentcmd.StepError("obce lub zmienione urządzenie pudełka: " + name)
            missing.pop(expected_name)
        return inst, missing

    def ensure(self, step, check_only=False):
        with bs.lifecycle_lock(self.project):
            return self._ensure(step, check_only)

    def _ensure(self, step, check_only=False):
        from . import admission
        from .runner import MountsBusy
        with self.access(step) as manifest:
            wanted = {device_name(m): values(m) for m in manifest["mounts"]}
            inst, missing = self.inspect(self.runner.box, wanted)
            idmap_ok = self.incus.config_get(self.runner.box, "raw.idmap") == self.runner.idmap()
            running = str(inst.get("status", "")).lower() == "running"
            if str(inst.get("status", "")).lower() not in ("running", "stopped"):
                raise agentcmd.StepError("nieznany stan pudełka")
            if missing or not idmap_ok:
                current = {step.step_id, self.runner.card + "/" + step.step_id}
                if any(s not in current for s in self._busy(self.runner.box)):
                    raise MountsBusy("zmiana manifestu wymaga pudełka bez żywego kroku")
            if check_only:
                return []
            req = admission.request(self.incus, "start", self.runner.box, slug=self.project,
                                    card=self.runner.card if self.runner.box != bs.box_name(self.project) else None)
            with admission.operation(self.incus, req):
                # Stop usuwa także potomków Dockera, których koniec jednostki agenta nie dowodzi.
                if (missing or not idmap_ok) and running:
                    self.incus.stop(self.runner.box)
                if missing or not idmap_ok:
                    if str((self.incus.instance(self.runner.box) or {}).get("status", "")).lower() != "stopped":
                        raise agentcmd.StepError("nie potwierdzono zatrzymania przed montowaniem")
                    for mount in manifest["mounts"]:
                        if mount["kind"] == "home":
                            home.directory(mount["source"])
                        if exposure._path(mount["source"]) != mount["source"] or not os.path.isdir(mount["source"]):
                            raise agentcmd.StepError("niekanoniczne lub brakujące źródło urządzenia")
                        check_tree(mount["source"])
                    for repo in manifest["repositories"]:
                        from ..git_sandbox import repository, initialize_indexes
                        repository(repo)
                        initialize_indexes(repo)
                    self.incus.config_set(self.runner.box, {"raw.idmap": self.runner.idmap(), **admission.limits()})
                    for name, entry in missing.items():
                        self.incus.device_add(self.runner.box, name, "disk", {k: v for k, v in entry.items() if k != "type"})
                    if self.inspect(self.runner.box, wanted)[1]:
                        raise agentcmd.StepError("niepełne urządzenia po montowaniu")
                if not running or missing or not idmap_ok:
                    admission.start(self.incus, self.runner.box)
                    from .lifecycle import require_no_swap
                    require_no_swap(self.incus, self.runner.box)
                    self.runner.wait_user_manager()
            return list(missing)

    def detach_snapshot(self, project, ident):
        """Callback PT-262 pod blokadą paczek. Nie wchodzi ponownie do ich magazynu."""
        from .builder import BoxBusy
        try:
            # Nie czekamy z blokadą paczek na start, który sam na nią czeka.
            with bs.lifecycle_lock(self.project, blocking=False):
                return self._detach_snapshot(project, ident)
        except BoxBusy:
            return False

    def _detach_snapshot(self, project, ident):
        if project != self.project:
            return False
        try:
            with exposure.snapshot_mount_access(project, ident, self.env) as mount:
                name, expected = device_name(mount), values(mount)
                instances = self.incus.list()
                for inst in instances:
                    box = inst.get("name", "")
                    if box != bs.box_name(project) and (inst.get("config") or {}).get(bs.COPY_OF_KEY) != bs.box_name(project):
                        # Nieznane użycie źródła w obcej instancji również uniemożliwia usunięcie danych.
                        if any(d.get("source") == mount["source"] for d in inst.get("expanded_devices", {}).values()):
                            return False
                        continue
                    matching = [(n, d) for n, d in inst.get("expanded_devices", {}).items()
                                if d.get("source") == mount["source"]]
                    if not matching:
                        continue
                    if matching != [(name, expected)] or self._busy(box):
                        return False
                    self.incus.device_remove(box, name)
                for inst in self.incus.list():
                    if any(d.get("source") == mount["source"] for d in inst.get("expanded_devices", {}).values()):
                        return False
                return True
        except (OSError, ValueError):
            return False
