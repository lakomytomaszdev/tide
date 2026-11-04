"""Atrapa `incus` dla testów (uruchamiana przez skrypt `incus` z `fakebin.py`), bez prawdziwego Incusa.

- każde wywołanie: wiersz `incus <argv>` (shlex) w `$TIDE_FAKE_LOG`,
- stan: `$TIDE_FAKE_INCUS_STATE/state.json` — instancje (status, config, urządzenia lokalne, migawki, adres),
  wolumeny, urządzenia profilu `default` (`eth0`, `root`); pliki z `file push` w `files/<instancja>/…`,
- `list`, `snapshot list`, `config show`, `config device show`, `info` wypisują JSON ze stanu;
  `init/launch/copy/snapshot create/snapshot restore/start/stop/delete/config set/config device set|override|add`
  i `storage volume create|delete` zmieniają stan,
- `exec … -- polecenie` wykonuje polecenie **lokalnie** w katalogu `--cwd` (domyślnie `<stan>/cwd/<instancja>`),
  ze zmiennymi `--env`, stdin z `/dev/null` i PATH zaczynającym się od atrap programów pudełka
  (`<fakebin>/box`: `apt-get`, `docker`, `systemctl` tylko zapisują wywołanie) — nic nie trafia na hosta;
  argument równy ścieżce wypchniętej `file push` jest podmieniany na lokalną kopię,
- `$TIDE_FAKE_INCUS_FAIL` — lista podpoleceń (np. `snapshot create,start`), które kończą się kodem 1.
"""
import json
import os
import shlex
import subprocess
import sys

STATE = os.environ.get("TIDE_FAKE_INCUS_STATE") or os.path.join(os.getcwd(), "incus-state")
PROFILE_DEVICES = {"eth0": {"name": "eth0", "network": "incusbr0", "type": "nic"},
                   "root": {"path": "/", "pool": "tide", "type": "disk"}}


def load():
    try:
        with open(os.path.join(STATE, "state.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    data.setdefault("instances", {})
    data.setdefault("volumes", [])
    data.setdefault("profile_devices", PROFILE_DEVICES)
    data.setdefault("next_ip", 10)
    return data


def save(data):
    os.makedirs(STATE, exist_ok=True)
    tmp = os.path.join(STATE, "state.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(STATE, "state.json"))


def die(msg, code=1):
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def kv(items):
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            die(f"niepoprawne klucz=wartość: {item}")
        out[key] = value
    return out


def inst_or_die(data, name):
    inst = data["instances"].get(name)
    if inst is None:
        die(f"Instance not found: {name}")
    return inst


def public(name, inst):
    out = {"name": name, "status": inst["status"], "config": inst["config"], "devices": inst["devices"],
           "expanded_devices": {**PROFILE_DEVICES, **inst["devices"]},
           "snapshots": [{"name": s["name"]} for s in inst["snapshots"]], "state": None}
    if inst["status"] == "Running":
        out["state"] = {"status": "Running", "network": {"eth0": {"addresses": [
            {"family": "inet", "address": inst["ip"], "netmask": "24", "scope": "global"}]}}}
    return out


def new_instance(data, config, devices, status):
    ip = f"10.173.184.{data['next_ip']}"
    data["next_ip"] += 1
    return {"status": status, "config": dict(config), "devices": json.loads(json.dumps(devices)),
            "snapshots": [], "ip": ip}


def main(argv):
    with open(os.environ["TIDE_FAKE_LOG"], "a", encoding="utf-8") as fh:
        fh.write("incus " + shlex.join(argv) + "\n")
    args, project, i = [], None, 0
    split = argv.index("--") if "--" in argv else len(argv)
    while i < split:
        if argv[i] == "--project":
            project = argv[i + 1]
            i += 2
            continue
        args.append(argv[i])
        i += 1
    rest = argv[split + 1:]
    if not project:
        die("atrapa: brak --project", 2)
    fail = [f.strip() for f in os.environ.get("TIDE_FAKE_INCUS_FAIL", "").split(",") if f.strip()]
    sub = " ".join(args[:2]) if args and args[0] in ("snapshot", "config", "storage", "file") else (args or [""])[0]
    if sub in fail or (args and args[0] in fail):
        die(f"atrapa: wymuszony błąd {sub}")
    data = load()
    cmd = args[0] if args else ""

    if cmd in ("init", "launch"):
        image, name = args[1], args[2]
        if name in data["instances"]:
            die(f"Instance {name} already exists")
        config = {}
        opts = args[3:]
        for j, opt in enumerate(opts):
            if opt == "-c":
                config.update(kv([opts[j + 1]]))
        if config.get("security.privileged") == "true":
            die("atrapa: security.privileged jest zabronione w projekcie ograniczonym")
        config["image.source"] = image
        data["instances"][name] = new_instance(data, config, {}, "Running" if cmd == "launch" else "Stopped")
    elif cmd == "copy":
        source, target = args[1], args[2]
        base, _, snap = source.partition("/")
        src = inst_or_die(data, base)
        if snap:
            found = [s for s in src["snapshots"] if s["name"] == snap]
            if not found:
                die(f"Snapshot not found: {source}")
            src = found[0]
        if target in data["instances"]:
            die(f"Instance {target} already exists")
        data["instances"][target] = new_instance(data, src["config"], src["devices"], "Stopped")
    elif cmd in ("start", "stop"):
        inst = inst_or_die(data, args[1])
        inst["status"] = "Running" if cmd == "start" else "Stopped"
    elif cmd == "delete":
        inst_or_die(data, args[1])
        del data["instances"][args[1]]
    elif cmd == "list":
        print(json.dumps([public(n, v) for n, v in sorted(data["instances"].items())]))
    elif cmd == "info":
        inst = inst_or_die(data, args[1])
        print(json.dumps(public(args[1], inst)))
    elif cmd == "snapshot":
        action, name = args[1], args[2]
        inst = inst_or_die(data, name)
        if action == "create":
            snap = args[3]
            existing = [s for s in inst["snapshots"] if s["name"] == snap]
            if existing and "--reuse" not in args:
                die(f"Snapshot {snap} already exists")
            inst["snapshots"] = [s for s in inst["snapshots"] if s["name"] != snap]
            inst["snapshots"].append({"name": snap, "config": dict(inst["config"]),
                                      "devices": json.loads(json.dumps(inst["devices"])),
                                      "description": ""})
        elif action == "restore":
            found = [s for s in inst["snapshots"] if s["name"] == args[3]]
            if not found:
                die(f"Snapshot not found: {args[3]}")
            inst["config"] = dict(found[0]["config"])
            inst["devices"] = json.loads(json.dumps(found[0]["devices"]))
            restored = os.path.join(STATE, "restored.log")
            with open(restored, "a", encoding="utf-8") as fh:
                fh.write(f"{name}/{args[3]}\n")
        elif action == "list":
            print(json.dumps([{"name": s["name"], "config": s["config"], "description": s["description"]}
                              for s in inst["snapshots"]]))
        else:
            die(f"atrapa: nieznane snapshot {action}", 2)
    elif cmd == "config":
        if args[1] == "device":
            action, name = args[2], args[3]
            inst = inst_or_die(data, name)
            if action == "show":
                print(json.dumps(inst["devices"]))
            elif action == "set":
                dev = args[4]
                if dev not in inst["devices"]:
                    die(f"Device from profile(s) cannot be modified for individual instance: {dev}")
                inst["devices"][dev].update(kv(args[5:]))
            elif action == "override":
                dev = args[4]
                if dev in inst["devices"]:
                    die(f"The device already exists: {dev}")
                if dev not in data["profile_devices"]:
                    die(f"The profile device doesn't exist: {dev}")
                inst["devices"][dev] = {**data["profile_devices"][dev], **kv(args[5:])}
            elif action == "add":
                dev, kind = args[4], args[5]
                if dev in inst["devices"]:
                    die(f"The device already exists: {dev}")
                inst["devices"][dev] = {"type": kind, **kv(args[6:])}
            else:
                die(f"atrapa: nieznane config device {action}", 2)
        else:
            action, name = args[1], args[2]
            inst = inst_or_die(data, name)
            if action == "set":
                values = kv(args[3:])
                if values.get("security.privileged") == "true":
                    die("atrapa: security.privileged jest zabronione w projekcie ograniczonym")
                inst["config"].update(values)
            elif action == "get":
                print(inst["config"].get(args[3], ""))
            elif action == "show":
                print(json.dumps({"config": inst["config"], "devices": inst["devices"]}))
            else:
                die(f"atrapa: nieznane config {action}", 2)
    elif cmd == "storage":
        action, pool, volume = args[2], args[3], args[4]
        key = f"{pool}/{volume}"
        if action == "create":
            if key in data["volumes"]:
                die(f"Volume by that name already exists: {key}")
            data["volumes"].append(key)
            data.setdefault("volume_config", {})[key] = kv(args[5:])
        elif action == "delete":
            if key not in data["volumes"]:
                die(f"Storage volume not found: {key}")
            data["volumes"].remove(key)
            data.setdefault("volume_config", {}).pop(key, None)
        elif action == "show":
            if key not in data["volumes"]:
                die(f"Storage volume not found: {key}")
            print(json.dumps({"name": volume, "pool": pool}))
        else:
            die(f"atrapa: nieznane storage volume {action}", 2)
    elif cmd == "file":
        source, target = args[2], args[3]
        name, _, path = target.partition("/")
        inst_or_die(data, name)
        local = os.path.join(STATE, "files", name, path.lstrip("/"))
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(source, "rb") as src, open(local, "wb") as dst:
            dst.write(src.read())
        data.setdefault("pushed", {}).setdefault(name, {})["/" + path.lstrip("/")] = local
    elif cmd == "exec":
        return run_exec(data, args[1:], rest)
    else:
        die(f"atrapa: nieobsługiwane polecenie {cmd}", 2)
    save(data)
    return 0


def run_exec(data, opts, command):
    name = opts[0]
    inst = inst_or_die(data, name)
    if inst["status"] != "Running":
        die(f"Instance is not running: {name}")
    env = dict(os.environ)
    box_bin = os.environ.get("TIDE_FAKE_BOX_BIN", "")
    if not box_bin or not os.path.isdir(box_bin):
        die("atrapa: brak katalogu atrap programów pudełka (TIDE_FAKE_BOX_BIN)", 2)
    env["PATH"] = box_bin + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    cwd = os.path.join(STATE, "cwd", name)
    j = 1
    while j < len(opts):
        if opts[j] == "--env":
            env.update(kv([opts[j + 1]]))
            j += 2
        elif opts[j] == "--cwd":
            cwd = opts[j + 1]
            j += 2
        elif opts[j] in ("--user", "--group"):
            j += 2
        else:
            j += 1
    if cwd.startswith(STATE):         # katalogi spoza stanu atrapy tworzy test (atrapa nie pisze po hoście)
        os.makedirs(cwd, exist_ok=True)
    pushed = data.get("pushed", {}).get(name, {})
    command = [pushed.get(a, a) for a in command]
    env["TIDE_FAKE_BOX"] = name
    if not command:
        die("brak polecenia po --", 2)
    return subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
