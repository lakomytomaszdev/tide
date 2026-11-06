"""Cienki adapter wiersza poleceń `incus` — bez logiki budowy.

Każde polecenie dostaje `--project <projekt z ustawień>` (projekt ograniczony `user-<uid>`, P-5) i stdin
z `/dev/null` (nic nie czeka na odpowiedź). Program: `$TIDE_INCUS_BIN` albo `incus` z PATH; testy podają atrapę
z `daemon/tests/fakebin.py`. Błąd polecenia zarządzającego → `IncusError` z argv i końcem stderr; `exec`
zwraca kod wyjścia polecenia w pudełku.

Formaty: `list` i `snapshot list` czytają `--format json`; `config show` i `config device show` wypisują YAML
(atrapa: JSON, który też jest YAML-em) — parsujemy najpierw JSON, potem podzbiór YAML z `yamlmini`.
"""
import json
import os
import subprocess

from . import yamlmini


class IncusError(RuntimeError):
    def __init__(self, argv, code, stderr):
        self.argv, self.code, self.stderr = list(argv), code, stderr or ""
        tail = self.stderr.strip().splitlines()[-1:] or [""]
        super().__init__(f"{' '.join(argv[:4])}…: kod {code}: {tail[0]}")


def _kv(values: dict) -> list:
    return [f"{k}={_text(v)}" for k, v in values.items()]


def _text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _structured(text: str):
    text = text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return yamlmini.loads(text)


class Incus:
    def __init__(self, project: str, binary: str | None = None, run=subprocess.run, log=None):
        if not project:
            raise ValueError("projekt Incusa jest wymagany")
        self.project = project
        self.binary = binary or os.environ.get("TIDE_INCUS_BIN") or "incus"
        self._run = run
        self.log = log            # opcjonalny plik (obiekt z write) na wyjście `exec`

    # --- wywołanie ---------------------------------------------------------------------------------

    def call(self, *args, check=True) -> subprocess.CompletedProcess:
        argv = self._argv(args)
        proc = self._run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise IncusError(argv, proc.returncode, proc.stderr)
        return proc

    def _argv(self, args) -> list:
        # `--project` zaraz po podpoleceniu (dwuczłonowym: `snapshot create`, `config device set`, …).
        args = list(args)
        head = 1
        if args and args[0] in ("snapshot", "config", "storage", "file") and len(args) > 1:
            head = 2
            if args[0] == "config" and args[1] == "device" or args[0] == "storage" and args[1] == "volume":
                head = 3
        return [self.binary, *args[:head], "--project", self.project, *args[head:]]

    # --- instancje ---------------------------------------------------------------------------------

    def init(self, image: str, name: str, config: dict | None = None):
        """`incus init` (launch bez startu): pudełko powstaje zatrzymane, żeby karta sieciowa dostała
        filtrowanie i limity, zanim wyśle pierwszy pakiet."""
        args = ["init", image, name]
        for item in _kv(config or {}):
            args += ["-c", item]
        self.call(*args)

    def launch(self, image: str, name: str, config: dict | None = None):
        args = ["launch", image, name]
        for item in _kv(config or {}):
            args += ["-c", item]
        self.call(*args)

    def copy(self, source: str, target: str):
        self.call("copy", source, target)

    def start(self, name: str):
        self.call("start", name)

    def stop(self, name: str, force: bool = False):
        self.call("stop", name, *(["--force"] if force else []))

    def delete(self, name: str, force: bool = True):
        self.call("delete", name, *(["--force"] if force else []))

    def list(self) -> list:
        data = json.loads(self.call("list", "--format", "json").stdout or "[]")
        return data if isinstance(data, list) else []

    def instance(self, name: str):
        """Instancja o **dokładnie** tej nazwie (filtr `incus list` dopasowuje też przedrostki — P-5)."""
        for inst in self.list():
            if inst.get("name") == name:
                return inst
        return None

    def info(self, name: str):
        return _structured(self.call("info", name).stdout)

    # --- migawki -----------------------------------------------------------------------------------

    def snapshot_create(self, name: str, snapshot: str, reuse: bool = True):
        self.call("snapshot", "create", name, snapshot, *(["--reuse"] if reuse else []))

    def snapshot_restore(self, name: str, snapshot: str):
        self.call("snapshot", "restore", name, snapshot)

    def publish(self, source: str, alias: str):
        """`incus publish <instancja>/<migawka> --alias <alias> --reuse` — obraz z migawki (obraz bazowy, #16);
        `--reuse` zastępuje poprzedni obraz o tym aliasie."""
        self.call("publish", source, "--alias", alias, "--reuse")

    def snapshot_list(self, name: str) -> list:
        data = json.loads(self.call("snapshot", "list", name, "--format", "json").stdout or "[]")
        return data if isinstance(data, list) else []

    # --- konfiguracja ------------------------------------------------------------------------------

    def config_set(self, name: str, values: dict):
        self.call("config", "set", name, *_kv(values))

    def config_get(self, name: str, key: str) -> str:
        return self.call("config", "get", name, key).stdout.strip()

    def config_show(self, name: str) -> dict:
        data = _structured(self.call("config", "show", name).stdout)
        return data if isinstance(data, dict) else {}

    def device_show(self, name: str) -> dict:
        """Urządzenia **lokalne** instancji (bez dziedziczonych z profilu)."""
        data = _structured(self.call("config", "device", "show", name).stdout)
        return data if isinstance(data, dict) else {}

    def device_set(self, name: str, device: str, values: dict):
        self.call("config", "device", "set", name, device, *_kv(values))

    def device_override(self, name: str, device: str, values: dict):
        self.call("config", "device", "override", name, device, *_kv(values))

    def device_add(self, name: str, device: str, kind: str, values: dict):
        self.call("config", "device", "add", name, device, kind, *_kv(values))

    def device_remove(self, name: str, device: str):
        self.call("config", "device", "remove", name, device)

    def device_replace(self, name: str, device: str, kind: str, values: dict):
        """Atomowa zmiana całego urządzenia; błąd nie zostawia instancji bez starego domu."""
        # `config show` zwraca YAML, m.in. blok raw.idmap nieobsługiwany przez yamlmini.
        # Lista JSON niesie pełną konfigurację; do edit przekazujemy pola zapisywalne,
        # bez statusu i expanded_devices odziedziczonych z profili.
        instance = self.instance(name)
        if instance is None:
            raise ValueError(f"brak instancji Incusa: {name}")
        data = {key: instance[key] for key in
                ("architecture", "config", "devices", "ephemeral", "profiles", "stateful", "description")
                if key in instance}
        data.setdefault("devices", {})[device] = {"type": kind, **values}
        argv = self._argv(["config", "edit", name])
        result = self._run(argv, input=json.dumps(data), capture_output=True, text=True)
        if result.returncode:
            raise IncusError(argv, result.returncode, result.stderr)

    # --- wolumeny i pliki --------------------------------------------------------------------------

    def volume_create(self, pool: str, volume: str, config: dict | None = None):
        self.call("storage", "volume", "create", pool, volume, *_kv(config or {}))

    def volume_get(self, pool: str, volume: str, key: str) -> str:
        return self.call("storage", "volume", "get", pool, volume, key).stdout.strip()

    def volume_set(self, pool: str, volume: str, values: dict):
        self.call("storage", "volume", "set", pool, volume, *_kv(values))

    def volume_delete(self, pool: str, volume: str, check=True):
        return self.call("storage", "volume", "delete", pool, volume, check=check)

    def volume_exists(self, pool: str, volume: str) -> bool:
        return self.call("storage", "volume", "show", pool, volume, check=False).returncode == 0

    def volume_list(self, pool: str) -> list:
        data = json.loads(self.call("storage", "volume", "list", pool, "--format", "json").stdout)
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("niepoprawna lista wolumenów Incusa")
        return data

    def volume_export(self, pool: str, volume: str, path: str):
        """Przenośne archiwum plików bez migawek ani formatu zoptymalizowanego btrfs."""
        self.call("storage", "volume", "export", pool, volume, path, "--volume-only", "--compression=none")

    def file_push(self, source: str, name: str, path: str):
        self.call("file", "push", source, f"{name}{path}", "--create-dirs")

    # --- polecenia w pudełku -----------------------------------------------------------------------

    def exec_argv(self, name: str, command: list, env: dict | None = None, user: int | None = None,
                  cwd: str | None = None, tty: bool = False, group: int | None = None) -> list:
        """argv `incus exec`; `tty=True` (`-t`) tylko dla poleceń interaktywnych użytkownika (`tide box login`)."""
        args = ["exec", name, "-t" if tty else "-T"]
        if user is not None:
            args += ["--user", str(user), "--group", str(user if group is None else group)]
        if cwd:
            args += ["--cwd", cwd]
        for key, value in (env or {}).items():
            args += ["--env", f"{key}={value}"]
        return self._argv([*args, "--", *command])

    def exec(self, name: str, command: list, env: dict | None = None, user: int | None = None,
             cwd: str | None = None, group: int | None = None) -> int:
        argv = self.exec_argv(name, command, env, user, cwd, group=group)
        if self.log is None:
            return self._run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True).returncode
        # Wyjście strumieniowane do dziennika budowy na bieżąco (długie `apt-get install` widać w trakcie).
        self.log.write(f"$ {' '.join(command)}\n")
        self.log.flush()
        proc = self._run(argv, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT)
        self.log.write(f"[kod {proc.returncode}]\n")
        self.log.flush()
        return proc.returncode
