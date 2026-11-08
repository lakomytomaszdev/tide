"""Próby z wnętrza piaskownicy kroku (K-69, PT-286) — uruchamia je agent kroku na polecenie approval_trials.py.

Wyłącznie biblioteka standardowa i programy systemu (`dbus-send`, `systemd-run`). Każda próba kończy się jednym
z wyników: `denied` (piaskownica nie pozwoliła), `refused` (usługa odrzuciła wywołanie — sprawdzenie pochodzenia),
`succeeded` (próba się udała — ticketu nie można odebrać), `reached` (wywołanie dotarło do usługi bez
odrzucenia pochodzenia, ale bez zmiany danych) albo `error` (próba nie dała jednoznacznego wyniku).

Prawdziwy magazyn odcisków i prawdziwa magistrala sesji są sprawdzane bez zmiany danych: otwarcie do zapisu bez
zapisu, plik próbny usuwany od razu, na prawdziwej magistrali tylko `Ping`. Dane próbne (magazyn, Tablica i usługa
z kodem tego worktree na osobnej magistrali z approval_trials.py) próby naprawdę zapisują i podmieniają.

Użycie (w kroku): /usr/bin/python3 approval_probe.py <trial.json>
"""
import json
import os
import subprocess
import sys

TIMEOUT = 20
SERVICE, PATH, IFACE = "org.tide.Daemon", "/Board", "org.tide.Daemon.Board"


def errno_text(exc: OSError) -> str:
    return f"{type(exc).__name__} [{os.strerror(exc.errno) if exc.errno else exc}]"


class Skip(Exception):
    """Próby nie da się wykonać bez zmiany prawdziwych danych (np. brak prawdziwego magazynu)."""


def fs_attempt(results, name, action):
    try:
        detail = action()
        results.append({"name": name, "outcome": "succeeded", "detail": detail or "udało się"})
    except Skip as exc:
        results.append({"name": name, "outcome": "skipped", "detail": str(exc)})
    except FileNotFoundError as exc:
        # approval_trials.py sprawdza przed krokiem, że pliki próbne istnieją: brak = ścieżka ukryta przez piaskownicę.
        results.append({"name": name, "outcome": "denied", "detail": errno_text(exc) + " — niewidoczne w piaskownicy"})
    except OSError as exc:
        results.append({"name": name, "outcome": "denied", "detail": errno_text(exc)})
    except Exception as exc:  # noqa: BLE001 — każda inna porażka też jest wynikiem próby
        results.append({"name": name, "outcome": "error", "detail": f"{type(exc).__name__}: {exc}"})


def run(argv, env=None):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, env=env,
                              stdin=subprocess.DEVNULL)
        return proc.returncode, (proc.stdout + proc.stderr).strip()[-1500:]
    except FileNotFoundError as exc:
        return 127, f"brak programu: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "przekroczony czas"
    except OSError as exc:
        return 126, errno_text(exc)


def upsert_argv(bus, payload):
    where = ["--session"] if bus is None else [f"--bus={bus}"]
    return ["/usr/bin/dbus-send", *where, "--print-reply=literal", f"--dest={SERVICE}", PATH,
            f"{IFACE}.UpsertProject", "string:" + json.dumps(payload, ensure_ascii=False)]


def classify_upsert(code, out):
    if code != 0:
        return "denied", out
    text = out.replace(" ", "")
    if '"error":"forbidden"' in text:
        return "refused", out
    if '"ok":true' in text:
        return "succeeded", out
    if '"ok":false' in text:
        return "reached", out
    return "error", out


def dbus_attempt(results, name, argv, env=None):
    code, out = run(argv, env)
    outcome, detail = classify_upsert(code, out)
    results.append({"name": name, "outcome": outcome, "detail": f"kod {code}: {detail}"})


def main(config_path):
    with open(config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    results = []
    store, real_store = cfg["trialStore"], cfg["realStore"]
    store_dir, real_dir = os.path.dirname(store), os.path.dirname(real_store)
    # Co proces w piaskownicy w ogóle widzi (przed próbami, które mogą coś przenieść).
    visible = {name: os.path.exists(path) for name, path in (("trialStore", store), ("trialStoreDir", store_dir),
                                                              ("realStoreDir", real_dir), ("boardBox", cfg["boardBox"]))}

    # (a) zapis i podmiana pliku odcisków
    def overwrite():
        fd = os.open(store, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        try:
            os.write(fd, b'{"version": 1, "boards": {}}\n')
        finally:
            os.close(fd)
        return "plik odcisków nadpisany"

    def replace_by_rename():
        temporary = os.path.join(store_dir, f".probe-{os.getpid()}.tmp")
        with open(temporary, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"version": 1, "boards": {cfg["boardKey"]: {cfg["slug"]: cfg["evilFingerprint"]}}}))
        os.rename(temporary, store)
        return "plik odcisków podmieniony przez rename"

    def unlink_store():
        os.unlink(store)
        return "plik odcisków usunięty"

    def move_store_dir():
        os.rename(store_dir, store_dir + f".probe-{os.getpid()}")
        return "katalog magazynu przeniesiony"

    def plant_in_state_base():
        target = os.path.join(os.path.dirname(store_dir), f"probe-{os.getpid()}")
        os.symlink("/tmp", target)
        os.unlink(target)
        return "utworzono dowiązanie w katalogu stanu (i usunięto)"

    def real_store_dir_write():
        probe = os.path.join(real_dir, f".pt286-probe-{os.getpid()}")
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.unlink(probe)
        return "utworzono plik próbny w prawdziwym katalogu magazynu (i usunięto)"

    def real_store_open_for_write():
        if not cfg.get("realStoreExists"):
            raise Skip("prawdziwy magazyn nie istnieje — zapis w jego katalogu sprawdza a6")
        fd = os.open(real_store, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)   # bez zapisu danych
        os.close(fd)
        return "prawdziwy plik odcisków otwarty do zapisu (bez zapisu danych)"

    def board_box_write():
        fd = os.open(cfg["boardBox"], os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            os.write(fd, b"\n# probe\n")
        finally:
            os.close(fd)
        return "dopisano do box.yaml Tablicy"

    fs_attempt(results, "a1 nadpisanie pliku odcisków", overwrite)
    fs_attempt(results, "a2 podmiana pliku odcisków (rename)", replace_by_rename)
    fs_attempt(results, "a3 usunięcie pliku odcisków", unlink_store)
    fs_attempt(results, "a4 przeniesienie katalogu magazynu", move_store_dir)
    fs_attempt(results, "a5 dowiązanie w katalogu stanu", plant_in_state_base)
    fs_attempt(results, "a6 zapis w prawdziwym katalogu magazynu", real_store_dir_write)
    fs_attempt(results, "a7 prawdziwy plik odcisków otwarty do zapisu", real_store_open_for_write)
    fs_attempt(results, "a8 zapis box.yaml Tablicy", board_box_write)

    # (b) UpsertProject z `tests`
    evil = {"slug": cfg["slug"], "tests": cfg["evilContract"]}
    dbus_attempt(results, "b1 UpsertProject, magistrala ze środowiska kroku (martwa)", upsert_argv(None, evil))
    dbus_attempt(results, "b2 UpsertProject, podany prawdziwy adres magistrali usługi Tablicy",
                 upsert_argv(cfg["serviceBus"], evil))
    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=cfg["serviceBus"])
    dbus_attempt(results, "b3 UpsertProject, DBUS_SESSION_BUS_ADDRESS nadpisany adresem usługi",
                 upsert_argv(None, evil), env)
    code, out = run(["/usr/bin/dbus-send", f"--bus={cfg['sessionBus']}", "--print-reply",
                     "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.Peer.Ping"])
    # Na magistrali pulpitu działa zainstalowana usługa (może być sprzed PT-286) i prawdziwa Tablica: tylko Ping.
    results.append({"name": "b4 połączenie z magistralą sesji pulpitu (Ping)",
                    "outcome": "denied" if code != 0 else "reached", "detail": f"kod {code}: {out}"})

    # (c) proces poza jednostką kroku
    code, out = run(["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--", "/bin/true"])
    results.append({"name": "c1 systemd-run --user (środowisko kroku)",
                    "outcome": "succeeded" if code == 0 else "denied", "detail": f"kod {code}: {out}"})
    uid = os.getuid()
    escape_env = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{uid}", DBUS_SESSION_BUS_ADDRESS=cfg["sessionBus"])
    code, out = run(["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--", "/bin/true"], escape_env)
    results.append({"name": "c2 systemd-run --user z XDG_RUNTIME_DIR i prawdziwą magistralą",
                    "outcome": "succeeded" if code == 0 else "denied", "detail": f"kod {code}: {out}"})
    code, out = run(["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--",
                     *upsert_argv(cfg["serviceBus"], evil)], escape_env)
    outcome = "denied" if code != 0 and "Failed to" in out else classify_upsert(code, out)[0]
    results.append({"name": "c3 UpsertProject z procesu uruchomionego przez systemd-run --user",
                    "outcome": outcome, "detail": f"kod {code}: {out}"})

    report = {"pid": os.getpid(), "cgroup": _read("/proc/self/cgroup"), "visible": visible, "results": results}
    with open(cfg["output"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    for item in results:
        print(f"{item['outcome']:>9}  {item['name']}")
    return 0


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError as exc:
        return errno_text(exc)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
