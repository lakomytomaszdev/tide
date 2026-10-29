"""Wejście prawego monitora PC ⇄ Xbox dla aktywności Stream (PT-38, decyzja 81).

Xbox: prawy monitor (DP-2) przechodzi przez DDC/CI na wejście HDMI, w które wpięty jest passthrough HD60 X.
PC: wejście DP-2 wraca na DisplayPort. Układ ekranów w KWin się nie zmienia (priorytety, włączenie, pozycje):
numeracja ekranów w Plasmie idzie za priorytetem, więc jego zmiana chowała dock Stream z przyciskiem powrotu
i konsolę streamu na lewym monitorze (decyzja użytkownika 29.10.2026). Skutek: KWin dalej uważa DP-2 za
pulpit, więc kursor albo nowe okno mogą trafić na prawy ekran, którego w trybie Xbox nie widać.

Wartości (złącze, kody wejść VCP 0x60) są w monitors.toml obok tego pliku, z komentarzem, skąd się wzięły.
Numer magistrali I²C jest ustalany przy każdym przełączeniu z `ddcutil detect` (złącze DRM → /dev/i2c-N).

Stan ($XDG_STATE_HOME/tide/monitors.json) to ostatnie potwierdzone wejście („pc” albo „xbox”) i przełączenie
w toku („pending”, zapisane przed pierwszym zapisem DDC). Monitor na wejściu HDMI zwykle nie odpowiada przez
DisplayPort, więc wejścia nie da się wtedy odczytać — stąd stan na dysku, a nie odczyt. Przerwane przełączenie
(pending) liczy się jak Xbox: widżet proponuje powrót do PC, który zawsze jest bezpieczny.

Przełączenie jest idempotentne: wejście, które już jest, nie jest zapisywane drugi raz. Brak `ddcutil` albo
monitor, który nie odpowiada na DDC, to błąd z prośbą o przełączenie wejścia przyciskiem na monitorze — stan
zostaje przy poprzednim wejściu, więc ponowienie jest możliwe. Przełączenia z usługi i z wiersza poleceń idą
po kolei pod blokadą monitors.lock.

Użycie (bez usługi): python3 monitors.py status|pc|xbox [--json]
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


@dataclass
class Config:
    monitor: str = "DP-2"
    input_pc: int = 0x0F
    input_xbox: int = 0x11
    attempts: int = 3
    retry_s: float = 1.0

    def input_for(self, layout: str) -> int:
        return self.input_xbox if layout == "xbox" else self.input_pc


def load_config(path: Path = CONFIG_PATH) -> Config:
    with open(path, "rb") as fh:
        ddc = tomllib.load(fh).get("ddc", {})
    return Config(monitor=ddc["monitor"], input_pc=int(ddc["input_pc"]), input_xbox=int(ddc["input_xbox"]),
                  attempts=max(1, int(ddc.get("attempts", 3))), retry_s=float(ddc.get("retry_s", 1.0)))


class Tools:
    """Uruchamianie ddcutil. bin_dir ($TIDE_MONITORS_BIN) zamiast PATH — testy wskazują katalog atrap,
    a usługa w testach nieistniejący katalog (daemon/tests/__init__.py)."""

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


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "tide"


def parse_detect(out: str, connector: str) -> int | None:
    """Magistrala I²C monitora o złączu `connector` z wyjścia `ddcutil detect`. Bloki „Invalid display”
    (monitor bez DDC/CI albo nieczytelny EDID) są pomijane w całości."""
    bus, valid = None, False
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Display "):
            bus, valid = None, True
            continue
        if line.startswith("Invalid display"):
            bus, valid = None, False
            continue
        if not valid:
            continue
        match = re.match(r"I2C bus:\s*/dev/i2c-(\d+)", line)
        if match:
            bus = int(match.group(1))
        match = re.match(r"DRM connector:\s*(\S+)", line)
        if match and bus is not None and (match.group(1) == connector or match.group(1).endswith("-" + connector)):
            return bus
    return None


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

    def layout(self) -> str:
        """Bieżące wejście według stanu: „xbox” po potwierdzonym przełączeniu albo przy przerwanym, inaczej „pc”."""
        state = self.load_state()
        return "xbox" if state.get("pending") or state.get("layout") == "xbox" else "pc"

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

    # DDC -------------------------------------------------------------------------------------

    def ddc_bus(self) -> int | None:
        code, out, _err = self.tools.run("ddcutil", "detect")
        return parse_detect(out, self.cfg.monitor) if code == 0 else None

    def read_input(self, bus: int) -> int | None:
        code, out, _err = self.tools.run("ddcutil", "--bus", str(bus), "getvcp", VCP_INPUT, "--terse")
        match = re.search(r"VCP\s+60\s+SNC\s+x([0-9a-fA-F]+)", out) if code == 0 else None
        return int(match.group(1), 16) if match else None

    def by_hand(self, layout: str) -> str:
        source = "DisplayPort" if layout == "pc" else "HDMI z Xboxem"
        return f"przełącz wejście monitora {self.cfg.monitor} na {source} przyciskiem na monitorze"

    # przełączenie ----------------------------------------------------------------------------

    def switch(self, target: str) -> dict:
        """{ok, layout, changed, error, note}: layout to wejście według stanu po przełączeniu (także po błędzie);
        note — np. że wejścia nie dało się odczytać i liczy się ostatnio potwierdzone."""
        if target not in LAYOUTS:
            raise ValueError(f"nieznany układ „{target}” (pc albo xbox)")
        with self._Lock(self.state_dir / "monitors.lock"):
            return self._switch(target)

    def _result(self, ok: bool, changed: bool, error: str = "", note: str = "") -> dict:
        return {"ok": ok, "layout": self.layout(), "changed": changed, "error": error, "note": note}

    def _switch(self, target: str) -> dict:
        state = self.load_state()
        value = self.cfg.input_for(target)
        if self.tools.which("ddcutil") is None:
            return self._result(False, False, f"brak ddcutil — {self.by_hand(target)}")
        bus = self.ddc_bus()
        if bus is None:
            return self._result(False, False,
                                f"{self.cfg.monitor} nie odpowiada na DDC (ddcutil detect) — {self.by_hand(target)}")
        confirmed = state.get("layout") == target and not state.get("pending")
        # Przed pierwszym zapisem: przerwane przełączenie widać po „pending” (liczy się jak Xbox).
        self._write_state({**state, "pending": target})
        for attempt in range(self.cfg.attempts):
            if attempt:
                self.tools.sleep(self.cfg.retry_s)
            current = self.read_input(bus)
            if current == value or (current is None and confirmed):
                # Już jest (odczyt) albo nie da się odczytać, a ostatnio potwierdzone wejście to cel — bez zapisu.
                self._write_state({**state, "layout": target, "pending": None, "since": state.get("since") or _now()})
                note = "" if current == value else (f"wejścia {self.cfg.monitor} nie dało się odczytać przez DDC — "
                                                    f"bez zapisu, liczy się ostatnio potwierdzone ({LABELS[target]})")
                return self._result(True, False, note=note)
            # Bez sprawdzania po zapisie: po zmianie wejścia monitor może przestać odpowiadać przez DisplayPort.
            code, _out, _err = self.tools.run("ddcutil", "--bus", str(bus), "setvcp", VCP_INPUT, f"0x{value:02x}",
                                              "--noverify")
            if code == 0:
                self._write_state({"layout": target, "pending": None, "since": _now()})
                return self._result(True, True)
        self._write_state({**state, "pending": None})
        return self._result(False, False, f"{self.cfg.monitor} nie odpowiada na DDC — {self.by_hand(target)}")

    # stan dla widżetu ------------------------------------------------------------------------

    def status(self) -> dict:
        """Stan z dysku (bez DDC — wejścia na HDMI zwykle nie da się odczytać przez DisplayPort)."""
        state = self.load_state()
        layout = self.layout()
        available = self.tools.which("ddcutil") is not None
        return {"layout": layout, "target": "pc" if layout == "xbox" else "xbox", "available": available,
                "error": "" if available else f"brak ddcutil — {self.by_hand(layout)}",
                "interrupted": bool(state.get("pending")), "since": state.get("since") or "",
                "monitor": self.cfg.monitor}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monitors.py", description="Wejście prawego monitora PC ⇄ Xbox (Tide, PT-38)")
    parser.add_argument("command", choices=("status", *LAYOUTS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    switcher = Switcher()
    result = switcher.status() if args.command == "status" else switcher.switch(args.command)
    ok = result.get("ok", True)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "status":
        print(f"Wejście {switcher.cfg.monitor}: {LABELS[result['layout']]}"
              + (" (przełączenie przerwane)" if result["interrupted"] else "")
              + (f" — {result['error']}" if result["error"] else ""))
    elif ok:
        print(f"{LABELS[result['layout']]}" + ("" if result["changed"] else " — bez zmian")
              + (f" ({result['note']})" if result.get("note") else ""))
    if not ok:
        print(f"Błąd: {result['error']}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
