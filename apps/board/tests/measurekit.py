"""Wspólne narzędzia pomiaru Tablicy (PT-323, karta K-92): protokół „przed/po”, który uruchamiają
measure_window.py (prawdziwe okno Tide) i measure_board.py (sonda offscreen na probe.py).

To nie jest test (nazwa spoza wzorca test*.py), więc `make test-board` niczego tu nie mierzy.

Protokół pomiaru:
- dane: stałe pliki JSON z `build_fixtures` (idle.json — tablica bez aktywnych kart, active5.json — 5 kart
  z derived.flow i dwoma ticketami w fazie „pracuje” każda), ten sam generator dla okna i sondy;
- kadencja migawek w tle (Driver): zmieniona co 2 s i niezmieniona co 1 s, zawsze przez
  `Backend._got_snapshot` (prawdziwy backend, więc identyczna migawka jest odfiltrowana przed QML jak w produkcji);
  w spoczynku tylko niezmieniona; `cadence` = full | unchanged | none;
- okno pomiaru: `warmup` s rozgrzewki, potem `duration` s; CPU z /proc/<pid>/stat (utime+stime) jako % jednego rdzenia
  (może przekraczać 100%); powtórzenia i agregacja: mediana, min, max, rozrzut > 20% mediany to flaga `spread_over_20pct`;
- `loadavg` (/proc/loadavg) przed i po każdym powtórzeniu;
- snapshot_ms: od wywołania `_got_snapshot` ze zmienioną migawką do pierwszej klatki (`frameSwapped`), która
  zaczęła się po tym wywołaniu (`afterAnimating` po wywołaniu, a więc po przeliczeniu wiązań i zbudowaniu delegatów)
  i zakończyła się zamianą buforów; bez klatki w ciągu okresu następnej migawki — pomiar odrzucony (`dropped`).
"""
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
CLK_TCK = os.sysconf("SC_CLK_TCK")
SPREAD_LIMIT_PCT = 20.0
ACTIVE_CARDS = 5


# --- procesy, obciążenie, agregacja (strona uruchamiająca) -------------------------------------------------------

def cpu_seconds(pid="self") -> float:
    """utime+stime procesu z /proc/<pid>/stat w sekundach (wszystkie wątki)."""
    with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
        fields = fh.read().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / CLK_TCK


def loadavg() -> str:
    with open("/proc/loadavg", encoding="utf-8") as fh:
        return fh.read().strip()


def summarize(values: list[float]) -> dict:
    """Mediana, min, max i rozrzut (max−min względem mediany)."""
    values = [v for v in values if v is not None]
    if not values:
        return {"median": None, "min": None, "max": None, "n": 0}
    median = statistics.median(values)
    spread = (max(values) - min(values)) / median * 100 if median else 0.0
    return {"median": round(median, 2), "min": round(min(values), 2), "max": round(max(values), 2), "n": len(values),
            "spread_pct": round(spread, 1), "spread_over_20pct": spread > SPREAD_LIMIT_PCT}


def run_child(cmd: list[str], env: dict, timeout: float) -> dict:
    """Uruchamia proces pomiarowy; ostatnia linia stdout to JSON wyniku. Wokół niego /proc/loadavg."""
    before = loadavg()
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {"error": f"przekroczono limit {timeout:.0f} s", "loadavg_before": before, "loadavg_after": loadavg(),
                "stderr_tail": str(exc.stderr or "")[-1500:]}
    after = loadavg()
    lines = proc.stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except ValueError:
        result = {}
    if proc.returncode != 0 or not result or "error" in result:
        result = {**result, "error": result.get("error") or f"kod wyjścia {proc.returncode}",
                  "stderr_tail": proc.stderr[-1500:]}
    result["loadavg_before"], result["loadavg_after"] = before, after
    return result


def aggregate(runs: list[dict]) -> dict:
    """Wyniki powtórzeń → mediana/min/max CPU, mediana i maksimum snapshot_ms, liczniki."""
    good = [r for r in runs if "error" not in r]
    ms = [m for r in good for m in r.get("snapshot_ms_all", [])]
    return {
        "cpu_pct": summarize([r["cpu_pct"] for r in good]),
        "snapshot_ms": {"median": round(statistics.median(ms), 1) if ms else None,
                        "max": round(max(ms), 1) if ms else None, "n": len(ms)},
        "delegate_creations": summarize([r["delegate_creations"] for r in good]),
        "got_snapshot_calls": summarize([r["got_snapshot_calls"] for r in good]),
        "snapshots_to_qml": summarize([r["snapshots_to_qml"] for r in good]),
        "active_cards": sorted({r["active_cards"] for r in good}),
        "loadavg": [[r["loadavg_before"], r["loadavg_after"]] for r in runs],
        "errors": [r["error"] + ": " + r.get("stderr_tail", "") for r in runs if "error" in r],
        "runs": [{k: v for k, v in r.items() if k != "snapshot_ms_all"} for r in runs],
    }


def stage(out_dir: str) -> str:
    """Pakiet okna Tide (widgets/stage.py) w nowym katalogu pod out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    pkg = tempfile.mkdtemp(prefix="pkg-", dir=out_dir)
    subprocess.run(["/usr/bin/python3", os.path.join(REPO, "widgets", "stage.py"), "--app",
                    os.path.join(REPO, "apps", "tide"), pkg], check=True)
    return pkg


def strip_animations(pkg: str) -> None:
    """Eksperyment „bez animacji”: w KOPII pakietu (nie w repozytorium) animacja pracujących segmentów nie startuje."""
    path = os.path.join(pkg, "views", "board", "qml", "BoardCard.qml")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    needle = 'running: segment.phase === "pracuje" && root.visible'
    if needle not in source:
        raise SystemExit("measurekit: nie znaleziono animacji w BoardCard.qml (zmieniła się?)")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source.replace(needle, "running: false"))


# --- dane ------------------------------------------------------------------------------------------------------

def _active_derived(n: int) -> dict:
    tickets = [{"id": "PT-1", "state": "gotowe", "phase": "gotowe", "wave": 1, "why": ""},
               {"id": "PT-2", "state": "w-toku", "phase": "pracuje", "wave": 1, "why": "krok w toku"},
               {"id": "PT-3", "state": "w-toku", "phase": "pracuje", "wave": 2, "why": "krok w toku"},
               {"id": "PT-4", "state": "todo", "phase": "todo", "wave": 2, "why": ""}]
    return {"column": "progress", "note": f"fala 1 z 2: PT-2 i PT-3 w toku (karta {n})", "done": 1, "total": 4,
            "tool": "claude", "session": f"claude:m{n}", "tickets": tickets,
            "flow": {"entry": "plan", "waveLabel": "fala 1 z 2", "paused": False, "timeline": [], "steps": []}}


def build_fixtures(data_dir: str, force: bool = False) -> dict:
    """idle.json i active5.json w data_dir (istniejące zostają, chyba że force) → {nazwa: {path, sha256}}."""
    os.makedirs(data_dir, exist_ok=True)
    paths = {name: os.path.join(data_dir, name + ".json") for name in ("idle", "active5")}
    if force or not all(os.path.exists(p) for p in paths.values()):
        sys.path.insert(0, REPO)
        from apps.board.tests.boardfix import preview_data
        with tempfile.TemporaryDirectory(prefix="tide-measure-fx-") as tmp:
            data = preview_data(tmp, derived=False)
        data.pop("cardDocs", None)   # nie należy do migawki usługi, a backend i tak by je odciął
        with open(paths["idle"], "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        # active5: pięć pierwszych kart spoza Gotowych i Archiwum (kolejność migawki) dostaje stan z koordynatora.
        active = json.loads(json.dumps(data))
        chosen = [c for c in active["cards"] if c.get("column") not in ("done", "archived")][:ACTIVE_CARDS]
        for n, card in enumerate(chosen, 1):
            card["boardColumn"] = "progress"
            card["derived"] = _active_derived(n)
        with open(paths["active5"], "w", encoding="utf-8") as fh:
            json.dump(active, fh, ensure_ascii=False, indent=1)
    out = {}
    for name, path in paths.items():
        with open(path, "rb") as fh:
            out[name] = {"path": path, "sha256": hashlib.sha256(fh.read()).hexdigest()[:16]}
    return out


def qml_value(engine, obj, expression):
    """Wartość wyrażenia QML w kontekście, w którym powstał obiekt `obj` (QObject albo QJSValue z QObject)."""
    from PyQt6.QtQml import QQmlExpression
    if hasattr(obj, "toQObject"):
        obj = obj.toQObject()
    value, undefined = QQmlExpression(engine.contextForObject(obj), obj, expression).evaluate()
    if undefined:
        raise RuntimeError(f"wyrażenie QML bez wartości: {expression}")
    if hasattr(value, "toQObject") and value.isQObject():
        return value.toQObject()
    return value.toVariant() if hasattr(value, "toVariant") else value


def read_counter(engine, obj) -> int:
    """Licznik utworzonych delegatów BoardCard (board.js: cardDelegates) — obiekt z pliku QML widoku Tablicy
    (np. `page` albo korzeń BoardView), bo tylko tam biblioteka `Logic` jest w zasięgu."""
    return int(qml_value(engine, obj, "Logic.cardDelegates()"))


def active_cards(snapshot: dict) -> int:
    """Karty z derived.flow, które mają ticket w fazie „pracuje”."""
    return sum(1 for c in snapshot.get("cards", [])
               if (c.get("derived") or {}).get("flow")
               and any(t.get("phase") == "pracuje" for t in (c["derived"].get("tickets") or [])))


def mutate(snapshot: dict) -> dict:
    """Wariant B migawki: pola zmienne w czasie (lastCommit, notatki z koordynatora)."""
    snap = json.loads(json.dumps(snapshot))
    last = snap.get("lastCommit")
    if last:
        last["ts"] = (last.get("ts") or 0) + 60
        last["time"] = str(last.get("time", "")) + "*"
    for card in snap.get("cards", []):
        if card.get("derived"):
            card["derived"]["note"] = "(zmiana) " + str(card["derived"].get("note", ""))
    return snap


# --- strona mierząca (w procesie okna albo sondy) ---------------------------------------------------------------

class Driver:
    """Kadencja migawek przez Backend._got_snapshot i pomiar CPU, liczników oraz snapshot_ms w jednym procesie."""

    def __init__(self, backend, window, delegates, cadence="full", warmup=10.0, duration=60.0):
        from PyQt6 import sip
        from PyQt6.QtQuick import QQuickWindow
        self.backend = backend
        self.window = sip.cast(window, QQuickWindow)
        self.delegates = delegates
        self.cadence, self.warmup, self.duration = cadence, warmup, duration
        base = json.loads(backend.snapshotJson)
        self.variants = [backend.snapshotJson, json.dumps(mutate(base), ensure_ascii=False)]
        self.variant = 0
        self.active_cards = active_cards(base)
        self.calls = self.changed_calls = self.passes = 0
        self.ms: list[float] = []
        self.dropped = 0
        self.measuring = False
        self._state, self._t0 = 0, 0.0
        self._on_done = None

    def start(self, on_done) -> None:
        from PyQt6.QtCore import QTimer
        self._on_done = on_done
        self.backend.snapshotChanged.connect(self._passed)
        self.window.afterAnimating.connect(self._after_animating)
        self.window.frameSwapped.connect(self._frame_swapped)
        self._changed_timer, self._same_timer = QTimer(), QTimer()
        self._changed_timer.setInterval(2000)
        self._same_timer.setInterval(1000)
        self._changed_timer.timeout.connect(self._changed)
        self._same_timer.timeout.connect(self._same)
        if self.cadence == "full":
            self._changed_timer.start()
        if self.cadence in ("full", "unchanged"):
            QTimer.singleShot(500, self._same_timer.start)   # niezmieniona w połowie sekundy między zmienionymi
        QTimer.singleShot(int(self.warmup * 1000), self._mark)
        QTimer.singleShot(int((self.warmup + self.duration) * 1000), self._finish)

    def _passed(self) -> None:
        if self.measuring:
            self.passes += 1

    def _same(self) -> None:
        if self.measuring:
            self.calls += 1
        self.backend._got_snapshot(True, self.backend.snapshotJson)

    def _changed(self) -> None:
        self.variant ^= 1
        if self.measuring:
            self.calls += 1
            self.changed_calls += 1
            if self._state:
                self.dropped += 1
            self._state = 1
        self._t0 = time.perf_counter()
        self.backend._got_snapshot(True, self.variants[self.variant])

    def _after_animating(self) -> None:
        if self._state == 1:
            self._state = 2

    def _frame_swapped(self) -> None:
        if self._state == 2:
            self.ms.append((time.perf_counter() - self._t0) * 1000)
            self._state = 0

    def _mark(self) -> None:
        self.calls = self.changed_calls = self.passes = self.dropped = 0
        self.ms = []
        self._state = 0
        self.measuring = True
        self._cpu0, self._wall0 = cpu_seconds(), time.perf_counter()
        self._delegates0 = self.delegates()

    def _finish(self) -> None:
        cpu, wall = cpu_seconds() - self._cpu0, time.perf_counter() - self._wall0
        self.measuring = False
        self._changed_timer.stop()
        self._same_timer.stop()
        ms = sorted(self.ms)
        self._on_done({
            "cpu_pct": round(cpu / wall * 100, 1), "wall_s": round(wall, 1), "warmup_s": self.warmup,
            "cadence": self.cadence, "active_cards": active_cards(json.loads(self.backend.snapshotJson)),
            "got_snapshot_calls": self.calls, "changed_calls": self.changed_calls,
            "snapshots_to_qml": self.passes,
            "delegate_creations": self.delegates() - self._delegates0,
            "snapshot_ms_all": [round(v, 2) for v in ms],
            "snapshot_ms": {"median": round(statistics.median(ms), 1) if ms else None,
                            "max": round(ms[-1], 1) if ms else None, "n": len(ms), "dropped": self.dropped},
        })
