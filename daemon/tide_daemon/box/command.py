"""Polecenie użytkownika jako syntetyczny krok BoxRunnera, bez uruchamiania i poświadczeń agenta."""
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace

from ..gitcommand import git_argv
from ..tasks import agentcmd
from ..board.store import BoardStore
from ..cardflow import boxaccess
from . import base, builder as bb, home, lifecycle, profile as bp, proxy as bproxy, refguard, runner as br
from . import settings as bset, state as bs, k3
from .ports import PortPool, DEMO_OWNER


def _git(cwd, *args):
    result = subprocess.run(git_argv("-C", cwd, *args), capture_output=True, text=True, timeout=60,
                            stdin=subprocess.DEVNULL)
    if result.returncode:
        raise bb.BuildError("git: " + result.stderr.strip())
    return result.stdout.strip()


def project_repo(slug):
    project = BoardStore(bp.board_dir()).load().projects.get(slug)
    if project is None or not project.repo:
        raise bb.BuildError(f"projekt {slug} nie ma repozytorium w Tablicy")
    return os.path.realpath(project.repo)


def make_step(slug, worktree, owner):
    repo = project_repo(slug)
    cwd = os.path.realpath(worktree or os.getcwd())
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if os.path.realpath(common) != os.path.join(repo, ".git"):
        raise bb.BuildError(f"{cwd} nie należy do projektu {slug}")
    cwd = _git(cwd, "rev-parse", "--show-toplevel")
    root = br.worktrees_root(repo)
    if cwd == repo:
        # Główny worktree nigdy nie trafia do pudełka. Wyniki polecenia zostają do obejrzenia.
        cwd = os.path.join(root, owner)
        _git(repo, "worktree", "add", "--no-track", "-b", "box-run/" + owner, cwd, "HEAD")
    if not br._within(cwd, root) or cwd == root:
        raise bb.BuildError(f"worktree musi leżeć w {root}")
    branch = _git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    folder = tempfile.mkdtemp(prefix=owner + ".", suffix=".step", dir=root)
    # Narzędzie w opisie jest wyłącznie formatem syntetycznego kroku: callback podmienia argv przed startem.
    # local=None i pusta lista MCP wykluczają tokeny oraz odczyt konfiguracji MCP hosta.
    return agentcmd.Step(tool="codex", binary="codex", prompt="", repo=repo, worktree=cwd, step_dir=folder,
                         step_id=owner, branch=branch, codex_mcp=(), tmp_root="/tmp/tide-command-" + owner)


def pack_devices(slug, card, profile):
    """Tylko zatwierdzone, już przygotowane kopie PT-173; CLI nigdy nie kopiuje źródeł użytkownika."""
    packs = profile.get("packs") or []
    if not packs:
        return []
    if not card:
        raise bb.BuildError("paczki wymagają --card")
    from .packs import devices, PackError
    try:
        return devices(slug, card, packs)
    except (PackError, OSError, ValueError) as exc:
        raise bb.BuildError(f"brak bezpiecznej przygotowanej paczki: {exc}") from exc


def _log_dir(slug, owner):
    return os.path.join(bb.state_dir(slug), "steps", owner)


def _save_run(slug, runner, step):
    folder = _log_dir(slug, step.step_id)
    home.directory(folder)
    path = os.path.join(folder, "command.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"repo": step.repo, "card": runner.card, "box": runner.box, "stepDir": step.step_dir,
                   "packs": {dev.name: dev.values() for dev in runner.extra_devices}}, fh)


def _finish(slug, runner, pool, owner):
    if runner.unit_state(runner.box, "tide-step-" + owner + ".service") != "inactive":
        return False, {}
    with open(os.path.join(_log_dir(slug, owner), "command.json"), encoding="utf-8") as fh:
        saved = json.load(fh)
    report = refguard.finish(saved["repo"], saved["card"], owner, active_only=True)
    if report.get("errors"):
        return False, report
    present = runner.incus.device_show(runner.box)
    for name, wanted in saved.get("packs", {}).items():
        actual = present.get(name)
        if actual:
            if any(str(actual.get(k, "")).lower() != str(v).lower() for k, v in wanted.items()):
                raise bb.BuildError("montowanie paczki zmienione — zachowano rezerwację")
            runner.incus.device_remove(runner.box, name)
    pool.release(owner)
    bs.mark_released(slug, runner.box, owner)
    return True, report


def _recover(slug, runner, pool):
    # Te same blokady życia co demo PT-171: klient dziedziczy fd, więc po śmierci CLI nie zwalniamy
    # rezerwacji przed potencjalnie opóźnionym utworzeniem jednostki przez incus exec.
    for owner in bs.running_steps(slug, runner.box):
        if not DEMO_OWNER.fullmatch(owner) or not os.path.isfile(os.path.join(_log_dir(slug, owner), "command.json")):
            continue
        with pool.demo_guard(owner, blocking=False) as available:
            if available:
                _done, report = _finish(slug, runner, pool, owner)
                if report:
                    raise bb.BuildError("refs.guard po poprzednim run: " + json.dumps(report, ensure_ascii=False))


def _ready(builder, runner, slug, card, per_card):
    if per_card:
        builder.copy_for_card(slug, card)
    st = builder.status(slug, card if per_card else None)
    if not st.usable:
        raise bb.BuildError(f"pudełko {runner.box} niegotowe: {st.error or st.state}")
    clean = next((s for s in builder.incus.snapshot_list(runner.box) if s.get("name") == bs.SNAPSHOT), {})
    stamp = bs.parse_stamp((clean.get("config") or {}).get(bs.RECIPE_KEY))
    if not stamp or (stamp.get("hash") if isinstance(stamp, dict) else stamp) != st.profile_hash:
        raise bb.BuildError("brak zgodnej migawki clean — przebuduj pudełko")
    builder.ensure_home(slug, card if per_card else "")
    lifecycle.Lifecycle(builder).start(slug, card if per_card else None)
    runner.wait_user_manager()
    deadline = time.monotonic() + max(1, builder.settings.ip_wait_seconds)
    while True:
        ip = bb.instance_ip(builder.incus.instance(runner.box), builder.settings.nic)
        if ip:
            return ip
        if time.monotonic() >= deadline:
            raise bb.BuildError("pudełko bez IPv4 po starcie")
        builder.sleep(0.2)


def _copy_log(path, out):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return
    with os.fdopen(fd, encoding="utf-8", errors="replace") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise bb.BuildError("log polecenia nie jest zwykłym plikiem")
        for line in fh:
            out.write(line)


def run_command(args, command, out, err, *, builder=None):
    project = bp.load_project(args.slug)
    if project is None:
        raise bb.BuildError("brak zatwierdzonego profilu projektu")
    profile, cards = project, {}
    if args.card:
        bs.card_box_name(args.slug, args.card)  # walidacja przed utworzeniem ścieżek
        args.card = args.card.upper()
        cards[args.card] = bp.load_card(args.slug, args.card) or {"tide-box": 1}
        profile = bp.merge(project, cards[args.card]).profile
    policy = bproxy.compose_policy(project, cards)
    per_card = bp.with_defaults(profile)["box"] == "per-card"
    if per_card and not args.card:
        raise bb.BuildError("pudełko per-card wymaga --card K-N")
    settings = builder.settings if builder is not None else bset.load()
    service, own = base.ensure_proxy(settings)
    try:
        b = builder or bb.Builder(settings, policy=service)
        runner = br.BoxRunner(settings, args.slug, card=args.card, per_card=per_card, incus=b.incus,
                              proxy_base=f"http://{service.listen}:{service.port}",
                              extra_devices=pack_devices(args.slug, args.card, profile))
        pool = PortPool(args.slug, runner.box)
        with bs.lifecycle_lock(args.slug):
            _recover(args.slug, runner, pool)
            b.require_idle(args.slug, runner.box)
            ip = _ready(b, runner, args.slug, args.card, per_card)
            wanted = {dev.name: dev.values() for dev in runner.extra_devices}
            for name, dev in b.incus.device_show(runner.box).items():
                if str(dev.get("path", "")).startswith("/packs") and (
                        name not in wanted or any(str(dev.get(k, "")).lower() != str(v).lower()
                                                  for k, v in wanted[name].items())):
                    raise bb.BuildError("pudełko ma paczki innej karty — odmowa run")
            owner = "demo-" + time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4)
            step = make_step(args.slug, args.worktree, owner)
            _save_run(args.slug, runner, step)
            with pool.demo_guard(owner) as guard:
                return _execute(b, runner, service, pool, step, ip, profile, policy, command, guard, out, err)
    except KeyboardInterrupt:
        return 130
    finally:
        if own:
            service.stop()


def _execute(builder, runner, service, pool, step, ip, profile, policy, command, guard, out, err):
    slug, owner = runner.slug, step.step_id
    log = os.path.join(_log_dir(slug, owner), "proxy-denied.log")
    placement = SimpleNamespace(box=runner.box, ip=ip)
    proc = None
    reserved = False
    registered = False
    try:
        port_env = pool.acquire(owner, profile)
        registered = True  # także częściowo nieudane register_step musi zostać cofnięte
        boxaccess.create(service, placement, owner, "command", log, policy)
        service.set_box_policy(ip, policy.domains, policy.ollama, log, full=policy.full, box=runner.box)
        reserved = True
        bs.mark_in_use(slug, runner.box, owner)

        def configure(bg):
            bg.argv = list(command)
            bg.env.update(port_env)
            boxaccess.configure(service, placement, owner, log, bg)
            bg.env.update(TIDE_BOX_NAME=runner.box, TIDE_BOX_BRIDGE=service.listen,
                          TIDE_HOST_HOME=os.path.expanduser("~"), TIDE_HOST_REPO=step.repo,
                          TIDE_HOST_MAIN_PROBE=(os.path.join(step.repo, "apps")
                                                if os.path.isdir(os.path.join(step.repo, "apps")) else step.repo),
                          TIDE_HOST_PID_NS=os.readlink("/proc/self/ns/pid"),
                          TIDE_BOX_DOCKER=str(int(bp.with_defaults(profile)["docker"]["enabled"])),
                          TIDE_BOX_K3_CLAUDE=str(int(k3.k3_active("claude"))))

        builder.incus.config_set(runner.box, lifecycle.limits(profile))
        bg, proc = runner.start(step, before_start=configure, pass_fds=(guard.fileno(),))
        print(f"pudełko: {runner.box}; jednostka: {runner.unit_for(bg)}; worktree: {step.worktree}\n"
              f"katalog kroku: {step.step_dir}\ndziennik proxy: {log}", file=err, flush=True)
        try:
            code = proc.wait()
        except BaseException:
            runner.kill({"unit": runner.unit_for(bg), "box": runner.box}, proc=proc)
            raise
        _copy_log(bg.log_file, out)
        if code:
            _copy_log(os.path.join(step.step_dir, "out", br.EXEC_LOG), err)
        return code if code >= 0 else 128 - code
    finally:
        # Koniec klienta nie dowodzi końca jednostki; nieznany stan zachowuje całą rezerwację.
        stopped = proc is None or (proc.poll() is not None and runner.unit_state(
            runner.box, "tide-step-" + owner + ".service") == "inactive")
        if stopped:
            report = {}
            if proc is not None:
                done, report = _finish(slug, runner, pool, owner)
                if not done:
                    raise bb.BuildError("refs.guard: " + json.dumps(report, ensure_ascii=False))
            else:
                present = runner.incus.device_show(runner.box)
                for dev in runner.extra_devices:
                    if dev.name in present:
                        runner.incus.device_remove(runner.box, dev.name)
                pool.release(owner)
                if reserved:
                    bs.mark_released(slug, runner.box, owner)
            if registered:
                boxaccess.revoke(service, owner, log)
                service.clear_box_policy(ip)
            if report:
                raise bb.BuildError("refs.guard: " + json.dumps(report, ensure_ascii=False))
        else:
            print(f"{owner}: rezerwacja pozostaje do potwierdzenia końca jednostki; kolejne run ponowi kontrolę",
                  file=err)
