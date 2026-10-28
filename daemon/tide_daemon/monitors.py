"""Układ monitorów PC ⇄ Xbox dla aktywności Stream (PT-38, decyzja 81).

Xbox: prawy monitor (DP-2) przechodzi przez DDC/CI na wejście HDMI, w które wpięty jest passthrough
HD60 X, a lewy (HDMI-A-1) staje się główny; DP-2 jest wyłączany w KWin, żeby okna i kursor nie zostały
na ekranie, którego nie widać. PC: powrót do układu sprzed przełączenia — dokładnie (włączenie, pozycja,
tryb z częstotliwością, skala, obrót, priorytety) z kopii `kscreen-doctor -j` zapisanej przy przejściu
na Xbox, a nie z wartości wpisanych na sztywno — i wejście DP-2 z powrotem na DisplayPort.

Wartości (monitory, kody wejść VCP 0x60) są w monitors.toml obok tego pliku, z komentarzem, skąd się wzięły.
Numer magistrali I²C jest ustalany przy każdym przełączeniu z `ddcutil detect` (złącze DRM → /dev/i2c-N).

Przełączenie jest idempotentne: układ, który już jest, zostaje, a kopia układu PC nie jest nadpisywana
układem Xbox. Brak `ddcutil` albo monitor, który nie odpowiada na DDC, nie blokuje zmiany w KWin —
wynik ma wtedy uwagę „przełącz wejście monitora przyciskiem”. Brak `kscreen-doctor` albo jego błąd
to błąd przełączenia. Stan (bieżący układ, kopia układu PC, wynik DDC, uwagi) leży w
$XDG_STATE_HOME/tide/monitors.json, a przełączenia z usługi i z wiersza poleceń idą po kolei pod
blokadą monitors.lock.

Użycie (bez usługi, np. do sprawdzenia): python3 monitors.py status|pc|xbox [--json]
Kody wyjścia: 0 — gotowe, 1 — błąd przełączenia, 2 — złe wywołanie.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("monitors.toml")
LAYOUTS = ("pc", "xbox")
LABELS = {"pc": "PC", "xbox": "Xbox"}
VCP_INPUT = "60"
ROTATIONS = {1: "none", 2: "left", 4: "inverted", 8: "right"}  # nazwy z kscreen-doctor --help
REFRESH_TOLERANCE = 0.05  # Hz — ten sam tryb po ponownym odczycie ma tę samą częstotliwość


class MonitorError(Exception):
    """Przełączenie się nie udało; opis po polsku do pokazania w widżecie."""


@dataclass
class Config:
    monitor: str = "DP-2"
    primary: str = "HDMI-A-1"
    disable_monitor: bool = True
    input_pc: int = 0x0F
    input_xbox: int = 0x11
    attempts: int = 3
    retry_s: float = 1.0
    settle_s: float = 1.5


def load_config(path: Path = CONFIG_PATH) -> Config:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    xbox, ddc = data.get("xbox", {}), data.get("ddc", {})
    return Config(monitor=xbox["monitor"], primary=xbox["primary"], disable_monitor=bool(xbox["disable_monitor"]),
                  input_pc=int(ddc["input_pc"]), input_xbox=int(ddc["input_xbox"]),
                  attempts=max(1, int(ddc.get("attempts", 3))), retry_s=float(ddc.get("retry_s", 1.0)),
                  settle_s=float(ddc.get("settle_s", 1.5)))


class Tools:
    """Uruchamianie kscreen-doctor i ddcutil. bin_dir ($TIDE_MONITORS_BIN) zamiast PATH — testy wskazują
    katalog atrap, a usługa w testach nieistniejący katalog (daemon/tests/__init__.py)."""

    def __init__(self, bin_dir: str | None = None, env: dict | None = None, sleep=time.sleep):
        self.bin_dir = bin_dir if bin_dir is not None else os.environ.get("TIDE_MONITORS_BIN")
        self.env = dict(os.environ, **(env or {}))
        self.sleep = sleep

    def which(self, name: str) -> str | None:
        if self.bin_dir is not None:
            path = os.path.join(self.bin_dir, name)
            return path if os.access(path, os.X_OK) else None
        return shutil.which(name)

    def run(self, name: str, *args: str, timeout: float = 30) -> tuple[int, str, str]:
        path = self.which(name)
        if path is None:
            return 127, "", f"brak programu {name}"
        try:
            result = subprocess.run([path, *args], capture_output=True, text=True, env=self.env, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", f"{name} nie odpowiedział w {timeout:g} s"
        return result.returncode, result.stdout, result.stderr


# --- układ w KWin -----------------------------------------------------------------------------

def _current_mode(item: dict) -> dict | None:
    for mode in item.get("modes", []):
        if str(mode.get("id")) == str(item.get("currentModeId")):
            return mode
    return None


def snapshot(outputs: list[dict]) -> list[dict]:
    """Kopia układu z `kscreen-doctor -j`: to, co trzeba, żeby odtworzyć go dokładnie."""
    out = []
    for item in outputs:
        mode = _current_mode(item) or {}
        size = mode.get("size") or item.get("size") or {}
        out.append({"name": item["name"], "connected": bool(item.get("connected", True)),
                    "enabled": bool(item.get("enabled")), "pos": [item.get("pos", {}).get("x", 0),
                                                                  item.get("pos", {}).get("y", 0)],
                    "mode": {"width": size.get("width"), "height": size.get("height"),
                             "refresh": mode.get("refreshRate"), "name": mode.get("name", "")},
                    "priority": item.get("priority", 0), "scale": item.get("scale", 1),
                    "rotation": item.get("rotation", 1)})
    return out


def detect(outputs: list[dict], cfg: Config) -> str:
    """„xbox”, gdy główny jest monitor z [xbox] primary, a przełączany monitor nie pokazuje pulpitu."""
    by_name = {o["name"]: o for o in outputs}
    primary, monitor = by_name.get(cfg.primary), by_name.get(cfg.monitor)
    if primary is None or not primary.get("enabled") or primary.get("priority") != 1:
        return "pc"
    shows_desktop = monitor is not None and monitor.get("connected", True) and monitor.get("enabled")
    if cfg.disable_monitor and shows_desktop:
        return "pc"
    return "xbox"


def _fmt_scale(value) -> str:
    return f"{float(value):g}"


def _find_mode(item: dict, wanted: dict) -> dict | None:
    """Tryb o tym samym rozmiarze i najbliższej częstotliwości (identyfikatory trybów są tylko na sesję KWin,
    a nazwa „1920x1080@60” bywa dwuznaczna: 60 i 59,94 Hz)."""
    same = [m for m in item.get("modes", []) if (m.get("size") or {}).get("width") == wanted.get("width")
            and (m.get("size") or {}).get("height") == wanted.get("height")]
    if not same or wanted.get("refresh") is None:
        return None
    best = min(same, key=lambda m: abs(float(m.get("refreshRate", 0)) - float(wanted["refresh"])))
    return best if abs(float(best.get("refreshRate", 0)) - float(wanted["refresh"])) <= REFRESH_TOLERANCE else None


def restore_args(saved: list[dict], outputs: list[dict]) -> tuple[list[str], list[str]]:
    """Argumenty kscreen-doctor, które z bieżącego układu robią zapisany; (argumenty, uwagi)."""
    by_name = {o["name"]: o for o in outputs}
    enable, details, priorities, notices = [], [], [], []
    for want in saved:
        item = by_name.get(want["name"])
        if item is None or not item.get("connected", True):
            if want.get("enabled"):
                notices.append(f"{want['name']} nie jest podłączony — zostaje poza układem")
            continue
        prefix = f"output.{want['name']}."
        if not want.get("enabled"):
            if item.get("enabled"):
                enable.append(prefix + "disable")
            continue
        if not item.get("enabled"):
            enable.append(prefix + "enable")
        mode = _find_mode(item, want["mode"])
        if mode is None:
            notices.append(f"{want['name']}: brak trybu {want['mode'].get('name') or want['mode']} — zostaje bieżący")
        elif str(mode["id"]) != str(item.get("currentModeId")) or not item.get("enabled"):
            details.append(prefix + f"mode.{mode['id']}")
        pos = [item.get("pos", {}).get("x"), item.get("pos", {}).get("y")]
        if pos != list(want["pos"]) or not item.get("enabled"):
            details.append(prefix + f"position.{want['pos'][0]},{want['pos'][1]}")
        if float(item.get("scale", 1)) != float(want.get("scale", 1)):
            details.append(prefix + f"scale.{_fmt_scale(want['scale'])}")
        if item.get("rotation", 1) != want.get("rotation", 1) and want.get("rotation") in ROTATIONS:
            details.append(prefix + f"rotation.{ROTATIONS[want['rotation']]}")
        if item.get("priority") != want.get("priority") or not item.get("enabled"):
            priorities.append(prefix + f"priority.{want['priority']}")
    return enable + details + priorities, notices


def differences(saved: list[dict], outputs: list[dict]) -> list[str]:
    """Czym bieżący układ różni się od zapisanego (tylko podłączone monitory)."""
    now = {o["name"]: o for o in snapshot(outputs)}
    out = []
    for want in saved:
        item = now.get(want["name"])
        if item is None or not item["connected"]:
            continue
        if item["enabled"] != want["enabled"]:
            out.append(f"{want['name']}: {'włączony' if item['enabled'] else 'wyłączony'}")
            continue
        if not want["enabled"]:
            continue
        if item["pos"] != list(want["pos"]):
            out.append(f"{want['name']}: pozycja {item['pos'][0]},{item['pos'][1]}")
        a, b = item["mode"], want["mode"]
        if (a["width"], a["height"]) != (b["width"], b["height"]) or \
                abs(float(a["refresh"] or 0) - float(b["refresh"] or 0)) > REFRESH_TOLERANCE:
            out.append(f"{want['name']}: tryb {a['name']}")
        if item["priority"] != want["priority"]:
            out.append(f"{want['name']}: priorytet {item['priority']}")
        if float(item["scale"]) != float(want["scale"]) or item["rotation"] != want["rotation"]:
            out.append(f"{want['name']}: skala albo obrót")
    return out


def xbox_args(outputs: list[dict], cfg: Config) -> list[str]:
    by_name = {o["name"]: o for o in outputs}
    primary, monitor = by_name.get(cfg.primary), by_name.get(cfg.monitor)
    if primary is None or not primary.get("connected", True):
        raise MonitorError(f"{cfg.primary} nie jest podłączony — nie ma na co przenieść pulpitu")
    args = []
    if not primary.get("enabled"):
        args.append(f"output.{cfg.primary}.enable")
    if primary.get("priority") != 1 or not primary.get("enabled"):
        args.append(f"output.{cfg.primary}.priority.1")
    if cfg.disable_monitor and monitor is not None and monitor.get("connected", True) and monitor.get("enabled"):
        args.append(f"output.{cfg.monitor}.disable")
    return args


def fallback_args(outputs: list[dict], cfg: Config) -> list[str]:
    """Powrót do PC bez zapisanego układu: przełączany monitor włączony w preferowanym trybie, z prawej
    strony pozostałych i jako główny — z danych KWin, nie z wartości na sztywno."""
    by_name = {o["name"]: o for o in outputs}
    monitor = by_name.get(cfg.monitor)
    if monitor is None or not monitor.get("connected", True):
        return []
    prefix = f"output.{cfg.monitor}."
    args = []
    if not monitor.get("enabled"):
        others = [o for o in outputs if o["name"] != cfg.monitor and o.get("enabled")]
        right = max((o.get("pos", {}).get("x", 0) + (o.get("size") or {}).get("width", 0) for o in others),
                    default=0)
        args.append(prefix + "enable")
        preferred = (monitor.get("preferredModes") or [None])[0]
        if preferred is not None:
            args.append(prefix + f"mode.{preferred}")
        args.append(prefix + f"position.{right},0")
    if monitor.get("priority") != 1 or not monitor.get("enabled"):
        args.append(prefix + "priority.1")
    return args


# --- przełącznik --------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "tide"


class Switcher:
    def __init__(self, cfg: Config | None = None, tools: Tools | None = None, state_dir: str | Path | None = None):
        self.cfg = cfg or load_config()
        self.tools = tools or Tools()
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()

    # stan ----------------------------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.state_dir / "monitors.json"

    def load_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.state_path)

    class _Lock:
        def __init__(self, path: Path):
            self.path = path

        def __enter__(self):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(self.path, "w")
            fcntl.flock(self.fh, fcntl.LOCK_EX)
            return self

        def __exit__(self, *exc):
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()

    # KWin -----------------------------------------------------------------------------------

    def read_outputs(self) -> list[dict]:
        if self.tools.which("kscreen-doctor") is None:
            raise MonitorError("brak kscreen-doctor (pakiet libkscreen) — układu ekranów nie da się zmienić")
        code, out, err = self.tools.run("kscreen-doctor", "-j")
        if code != 0:
            raise MonitorError(f"kscreen-doctor nie odczytał układu: {(err or out).strip() or f'kod {code}'}")
        try:
            outputs = json.loads(out)["outputs"]
        except (ValueError, KeyError, TypeError) as exc:
            raise MonitorError(f"kscreen-doctor zwrócił nieczytelny układ: {exc}") from exc
        return [o for o in outputs if isinstance(o, dict) and "name" in o]

    def apply(self, args: list[str]) -> None:
        if not args:
            return
        code, out, err = self.tools.run("kscreen-doctor", *args)
        if code != 0:
            raise MonitorError(f"kscreen-doctor nie zmienił układu: {(err or out).strip() or f'kod {code}'}")

    # DDC -------------------------------------------------------------------------------------

    def ddc_bus(self) -> int | None:
        code, out, _err = self.tools.run("ddcutil", "detect")
        if code != 0:
            return None
        bus = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith(("Display ", "Invalid display")):
                bus = None
            match = re.match(r"I2C bus:\s*/dev/i2c-(\d+)", line)
            if match:
                bus = int(match.group(1))
            match = re.match(r"DRM connector:\s*(\S+)", line)
            if match and bus is not None and (match.group(1) == self.cfg.monitor
                                              or match.group(1).endswith("-" + self.cfg.monitor)):
                return bus
        return None

    def read_input(self, bus: int) -> int | None:
        code, out, _err = self.tools.run("ddcutil", "--bus", str(bus), "getvcp", VCP_INPUT, "--terse")
        match = re.search(r"VCP\s+60\s+SNC\s+x([0-9a-fA-F]+)", out) if code == 0 else None
        return int(match.group(1), 16) if match else None

    def set_input(self, value: int) -> tuple[str, list[str]]:
        """Wejście przełączanego monitora przez DDC/CI; ("ok" | "missing" | "unreachable", uwagi)."""
        name = f"{self.cfg.monitor} ({'DisplayPort' if value == self.cfg.input_pc else 'HDMI'})"
        by_hand = f"przełącz wejście monitora {name} przyciskiem na monitorze"
        if self.tools.which("ddcutil") is None:
            return "missing", [f"brak ddcutil — {by_hand}"]
        bus = self.ddc_bus()
        if bus is None:
            return "unreachable", [f"{self.cfg.monitor} nie odpowiada na DDC (ddcutil detect) — {by_hand}"]
        for attempt in range(self.cfg.attempts):
            if attempt:
                self.tools.sleep(self.cfg.retry_s)
            if self.read_input(bus) == value:
                return "ok", []
            # Bez sprawdzenia po zapisie: po zmianie wejścia monitor może przestać odpowiadać na tym złączu.
            code, _out, _err = self.tools.run("ddcutil", "--bus", str(bus), "setvcp", VCP_INPUT, f"0x{value:02x}",
                                              "--noverify")
            if code == 0:
                return "ok", []
        return "unreachable", [f"{self.cfg.monitor} nie odpowiada na DDC — {by_hand}"]

    # przełączenie ----------------------------------------------------------------------------

    def switch(self, target: str) -> dict:
        if target not in LAYOUTS:
            raise ValueError(f"nieznany układ „{target}” (pc albo xbox)")
        try:
            with self._Lock(self.state_dir / "monitors.lock"):
                result = self._switch_xbox() if target == "xbox" else self._switch_pc()
        except MonitorError as exc:
            result = {"ok": False, "layout": target, "changed": False, "notices": [], "error": str(exc)}
        return result

    def _done(self, layout: str, changed: bool, notices: list[str], **state) -> dict:
        return {"ok": True, "layout": layout, "changed": changed, "notices": notices, "error": "", **state}

    def _switch_xbox(self) -> dict:
        outputs = self.read_outputs()
        state = self.load_state()
        current = detect(outputs, self.cfg)
        if current == "xbox" and state.get("layout") == "xbox":
            return self._done("xbox", False, ["układ Xbox już jest"])
        saved = state.get("saved") if current == "xbox" else snapshot(outputs)
        # Kopia układu PC na dysku, zanim cokolwiek się zmieni — przerwane przełączenie da się odwrócić.
        self._write_state({**state, "layout": current, "pending": "xbox", "saved": saved,
                           "savedAt": _now() if current != "xbox" else state.get("savedAt")})
        ddc, notices = self.set_input(self.cfg.input_xbox)
        if ddc == "ok":
            self.tools.sleep(self.cfg.settle_s)
        outputs = self.read_outputs()
        try:
            self.apply(xbox_args(outputs, self.cfg))
        except MonitorError:
            if ddc == "ok":
                self.set_input(self.cfg.input_pc)  # monitor wraca na pulpit, skoro KWin został przy PC
            self._write_state({**self.load_state(), "layout": "pc", "pending": None})
            raise
        outputs = self.read_outputs()
        if detect(outputs, self.cfg) != "xbox":
            raise MonitorError("po zmianie układ w KWin nadal nie jest układem Xbox")
        self._write_state({"layout": "xbox", "pending": None, "saved": saved,
                           "savedAt": self.load_state().get("savedAt"), "since": _now(), "ddc": ddc,
                           "notices": notices})
        return self._done("xbox", True, notices)

    def _switch_pc(self) -> dict:
        outputs = self.read_outputs()
        state = self.load_state()
        current = detect(outputs, self.cfg)
        if current == "pc" and state.get("layout") != "xbox" and not state.get("pending"):
            ddc, notices = self.set_input(self.cfg.input_pc) if self.tools.which("ddcutil") else ("missing", [])
            return self._done("pc", False, ["układ PC już jest"] + (notices if ddc == "unreachable" else []))
        saved = state.get("saved")
        notices: list[str] = []
        if saved:
            args, notices = restore_args(saved, outputs)
        else:
            args = fallback_args(outputs, self.cfg)
            notices.append(f"brak zapisanego układu PC — {self.cfg.monitor} włączony w preferowanym trybie jako główny")
        monitor = next((o for o in outputs if o["name"] == self.cfg.monitor), None)
        if monitor is None or not monitor.get("connected", True):
            notices.append(f"{self.cfg.monitor} nie jest widoczny w KWin (monitor na innym wejściu?) — "
                           f"przełącz jego wejście na DisplayPort przyciskiem i wybierz PC jeszcze raz")
        self.apply(args)
        outputs = self.read_outputs()
        if saved:
            notices += [f"układ różni się od zapisanego: {d}" for d in differences(saved, outputs)]
        ddc, ddc_notices = self.set_input(self.cfg.input_pc)
        notices += ddc_notices
        layout = detect(outputs, self.cfg)
        keep = layout == "xbox"   # monitor niewidoczny — kopia zostaje na następną próbę
        self._write_state({"layout": layout, "pending": None, "saved": saved if keep else None,
                           "savedAt": state.get("savedAt") if keep else None, "since": _now(), "ddc": ddc,
                           "notices": notices})
        return self._done(layout, True, notices)

    # stan dla widżetu ------------------------------------------------------------------------

    def status(self) -> dict:
        """Bieżący układ (odczyt KWin) i to, co zostało z ostatniego przełączenia. Tylko odczyt."""
        state = self.load_state()
        error = ""
        try:
            layout = detect(self.read_outputs(), self.cfg)
        except MonitorError as exc:
            layout, error = state.get("layout") or "pc", str(exc)
        return {"layout": layout, "target": "pc" if layout == "xbox" else "xbox", "available": not error,
                "error": error, "since": state.get("since") or "", "saved": bool(state.get("saved")),
                "savedAt": state.get("savedAt") or "", "ddc": state.get("ddc") or "",
                "notices": list(state.get("notices") or []), "monitor": self.cfg.monitor,
                "primary": self.cfg.primary, "disableMonitor": self.cfg.disable_monitor}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monitors.py", description="Układ monitorów PC ⇄ Xbox (Tide, PT-38)")
    parser.add_argument("command", choices=("status", *LAYOUTS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    switcher = Switcher()
    result = switcher.status() if args.command == "status" else switcher.switch(args.command)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "status":
        print(f"Układ: {LABELS.get(result['layout'], result['layout'])}" + (f" ({result['error']})" if result["error"] else ""))
        for notice in result["notices"]:
            print(f"  uwaga: {notice}")
    elif result["ok"]:
        print(f"Układ {LABELS[result['layout']] if result['layout'] in LABELS else result['layout']}"
              + ("" if result["changed"] else " — bez zmian"))
        for notice in result["notices"]:
            print(f"  uwaga: {notice}")
    else:
        print(f"Błąd: {result['error']}", file=sys.stderr)
    return 0 if result.get("ok", True) and not (args.command == "status" and result["error"]) else 1


if __name__ == "__main__":
    sys.exit(main())
