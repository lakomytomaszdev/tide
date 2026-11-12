#!/usr/bin/python3
"""Reproduktor kosztu odświeżenia Tablicy przy `box.enabled=true` (K-98, PT-342 #1; ten sam skrypt w #2 i #3).

Na izolowanych danych (osobne HOME, XDG_STATE_HOME, XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_RUNTIME_DIR i Tablica
w katalogu tymczasowym; bez instalacji, produkcyjnych dzienników, ustawień i refów) buduje usługę w procesie:
BoardStore + BoardMonitor + FlowEngine/CardFlowMonitor z włączonym pudełkiem, trzy projekty (plasma-tide i dwa
inne, z repozytoriami git) i N kart z dziennikiem przepływu i planem czekającym na zatwierdzenie (sekcja
„Środowisko karty” liczona dla każdej karty). Potem mierzy:

- zimny start osobno: pierwsze odświeżenie (`BoardMonitor.publish`) po złożeniu usługi. Składanie usługi
  (`create_card`/`upsert_project`) samo publikuje migawki i rozgrzewa pamięć widoku środowiska, więc przed
  zimnym startem pamięć `engine._env_cache` jest czyszczona — jak po restarcie usługi (pole `coldStart.cleared`),
- R odświeżeń Tablicy (`BoardMonitor.publish`: odczyt plików → projekcja → `engine.derived` każdej karty) — czas
  ścienny i liczba wczytań Tablicy (`BoardStore.load`, liczona na granicy magazynu) na odświeżenie,
- Q żądań na metodę odczytu `/Board` w tej samej drodze co adaptor D-Bus (Snapshot = Panel karty, Work, Badge,
  GetCardFlowSettings, RenderFlow, BoxStatus = stan pudełka) — opcjonalnie przy zajętym wątku przepływu
  (`--busy-ms`, scenariusz #2). BoxStatus używa prawdziwego `Builder` z atrapą `incus` (`fake_incus.py`, stan
  w izolowanym katalogu; pudełka nie istnieją) — wynik ma `boxStatus.builder = "fake-incus"`; koszt usługi
  (wczytania Tablicy, dzienniki kart, dziennik pudełka, slot dopuszczenia) jest prawdziwy, koszt Incusa nie.

Dane: plasma-tide ma też jedną kopię paczki w magazynie kopii (`packs.create_snapshot`), więc klucz pamięci
widoku przechodzi niepusty magazyn.

p95 to nearest-rank: k = ⌈0,95·n⌉, wynik = k-ta próbka po posortowaniu. Wynik JSON zawiera surowe próbki, SHA
kodu, `/proc/loadavg` przed i po, liczbę rdzeni, rozmiar historii (zdarzenia i bajty dzienników kart) i zimny start.

Użycie (z katalogu repozytorium):
    /usr/bin/python3 daemon/tests/perf_board_refresh.py --out wynik.json [--cards 30] [--refreshes 30]
        [--requests 100] [--busy-ms 0] [--src <katalog z daemon/>] [--sha <SHA>]

Skrypt niczego nie zapisuje poza katalogiem tymczasowym (usuwany na końcu, `--keep` go zostawia) i plikiem `--out`.
Testy jednostkowe nie mierzą czasu na sztywno; ten skrypt jest wyłącznie narzędziem pomiaru.
"""
import argparse
import contextlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.dirname(os.path.dirname(HERE))          # katalog repozytorium (zawiera daemon/)
PROJECTS = ("plasma-tide", "alpha", "beta")
METHODS = ("Snapshot", "Work", "Badge", "GetCardFlowSettings", "RenderFlow", "BoxStatus")
FAKE_INCUS = os.path.join(HERE, "fake_incus.py")
INCUS_PROJECT = "user-1000"


def nearest_rank(samples, q=0.95):
    """Percentyl nearest-rank: k = ⌈q·n⌉ (1-indeksowane) na posortowanych próbkach."""
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def loadavg():
    try:
        with open("/proc/loadavg", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def git_sha(src):
    try:
        return subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


@contextlib.contextmanager
def isolated_env(base):
    """Izolowane katalogi usługi; poprzednie zmienne wracają po pomiarze."""
    names = {"HOME": "home", "XDG_STATE_HOME": "state", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data",
             "XDG_CACHE_HOME": "cache", "XDG_RUNTIME_DIR": "run", "TIDE_BOARD_DIR": "board",
             "TIDE_FAKE_INCUS_STATE": "incus-state", "TIDE_FAKE_LOG": "incus.log"}
    files = ("TIDE_BOARD_DIR", "TIDE_FAKE_LOG")       # tworzone przez usługę / atrapę, nie katalogi
    values = {key: os.path.join(base, rel) for key, rel in names.items()}
    for key, path in values.items():
        if key not in files:
            os.makedirs(path, mode=0o700, exist_ok=True)
    gitconfig = os.path.join(base, "gitconfig")
    with open(gitconfig, "w", encoding="utf-8") as fh:
        fh.write("[user]\n\tname = Perf\n\temail = perf@example.invalid\n[init]\n\tdefaultBranch = master\n")
    values.update(GIT_CONFIG_GLOBAL=gitconfig, GIT_CONFIG_NOSYSTEM="1", QT_QPA_PLATFORM="offscreen")
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield values
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


def _repo(base, name):
    repo = os.path.join(base, "ws", name)
    os.makedirs(repo)
    _git(repo, "init", "-q")
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# {name}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


class Service:
    """Usługa w procesie: Tablica, monitor i przepływ karty z włączonym pudełkiem (bez ticku i bez kroków)."""

    def __init__(self, base, cards, box_file=True):
        from PyQt6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        from tide_daemon.board.store import BoardStore
        from tide_daemon.board_monitor import BoardMonitor
        from tide_daemon.box import profile as bp
        from tide_daemon.box.settings import BoxSettings
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.cardflow_monitor import CardFlowMonitor
        from tide_daemon.models import executors

        self.root = os.environ["TIDE_BOARD_DIR"]
        self.store = BoardStore(self.root)
        self.loads = 0
        real = self.store.load

        def counting(*args, **kwargs):     # granica magazynu Board: każde pełne wczytanie Tablicy
            self.loads += 1
            return real(*args, **kwargs)
        self.store.load = counting
        self.board = BoardMonitor(self.store, debounce_ms=60_000, poll_ms=3_600_000)
        settings = os.path.join(os.environ["XDG_CONFIG_HOME"], "tide", "cardflow.json")
        os.makedirs(os.path.dirname(settings), exist_ok=True)
        with open(settings, "w", encoding="utf-8") as fh:
            # Jak w produkcji: `box.enabled` w pliku włącza też granicę Gita (bwrap) usługi. Bez `box_file`
            # pudełko jest włączone tylko w silniku (test dymny w piaskownicy bez przestrzeni nazw).
            json.dump({"autoConsent": False, **({"box": {"enabled": True}} if box_file else {})}, fh)
        self.engine = FlowEngine(self.store, claude_projects=os.path.join(os.environ["HOME"], ".claude", "projects"),
                                 systemd_run="/bin/false", catalog=executors.default_catalog,
                                 tmp_root=os.path.join(base, "tmp"), settings=settings, which=lambda name: None)
        self.engine.box_settings_source = lambda: BoxSettings(enabled=True, project=INCUS_PROJECT)
        self.engine.box_builder = self._fake_builder(base)
        self.flow = CardFlowMonitor(self.engine, self.board, poll_ms=3_600_000)
        self.board.flow = self.flow
        slugs = []
        for name in PROJECTS:
            reply = json.loads(self.board.upsert_project(json.dumps({"repo": _repo(base, name)})))
            if not reply.get("ok"):
                raise RuntimeError(f"projekt {name}: {reply}")
            slugs.append(name)
        # Zatwierdzony profil środowiska dla części projektów; „beta” zostaje bez profilu (propozycja ze stosu).
        bp.save_project("plasma-tide", {"tide-box": 1, "project": "plasma-tide",
                                        "network": {"domains": ["pypi.org"]}})
        bp.save_project("alpha", {"tide-box": 1, "network": {"domains": ["registry.npmjs.org"]}})
        self.cards = []
        journal = self.engine.journal
        rows = [{"workId": f"#{i}", "title": f"Ticket {i}", "blockers": [], "executor": "Sonnet",
                 "reviewer": "Opus", "class": "standard", "note": ""} for i in range(1, 4)]
        for index in range(cards):
            slug = slugs[index % len(slugs)]
            reply = json.loads(self.board.create_card(json.dumps({
                "title": f"Karta {index + 1}", "project": slug, "description": "Pomiar odświeżenia.",
                "column": "todo"})))
            if not reply.get("ok"):
                raise RuntimeError(f"karta {index + 1}: {reply}")
            card = reply["id"]
            journal.append(card, "card.started", entry="plan", executor="Sonnet", program="claude",
                           provider="anthropic")
            journal.append(card, "plan.proposed", title=f"Plan {card}", prefix="PT", rows=rows, planner="Opus",
                           box=None)
            journal.append(card, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])
            self.cards.append(card)
        self._pack_snapshot(base)

    @staticmethod
    def _fake_builder(base):
        """Fabryka `Builder` dla BoxStatus: prawdziwy kod usługi, `incus` to atrapa ze stanem w izolowanym katalogu."""
        from tide_daemon.box.builder import Builder
        from tide_daemon.box.incus import Incus
        from tide_daemon.box.settings import BoxSettings
        binary = os.path.join(base, "bin", "incus")
        os.makedirs(os.path.dirname(binary), exist_ok=True)
        with open(binary, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\nexec /usr/bin/python3 -s "{FAKE_INCUS}" "$@"\n')
        os.chmod(binary, 0o755)
        settings = BoxSettings(enabled=True, project=INCUS_PROJECT)
        return lambda: Builder(settings, incus=Incus(INCUS_PROJECT, binary=binary), policy=object())

    def _pack_snapshot(self, base):
        """Jedna kopia paczki plasma-tide w magazynie kopii (wejście klucza pamięci widoku)."""
        from tide_daemon.box import packs
        source = os.path.join(base, "packsrc")
        os.makedirs(source)
        for index in range(5):
            with open(os.path.join(source, f"fixture-{index}.txt"), "w", encoding="utf-8") as fh:
                fh.write(f"dane testowe {index}\n")
        card = next(c for i, c in enumerate(self.cards) if PROJECTS[i % len(PROJECTS)] == "plasma-tide")
        packs.create_snapshot("plasma-tide", card, {"name": "fixtures", "from": os.path.join(source, "*.txt")})

    def clear_caches(self):
        """Pamięć widoku środowiska jak po restarcie usługi (zimny start); zwraca listę wyczyszczonych pamięci."""
        cleared = []
        cache = getattr(self.engine, "_env_cache", None)
        if isinstance(cache, dict):
            cache.clear()
            cleared.append("engine._env_cache")
        return cleared

    def history(self):
        events = size = 0
        for card in self.cards:
            events += len(self.engine.journal.read(card))
            for path in (self.engine.journal.path(card), self.engine.journal.mirror_path(card)):
                with contextlib.suppress(OSError):
                    size += os.path.getsize(path)
        return {"cards": len(self.cards), "events": events, "bytes": size}

    def refresh(self):
        """(ms, wczytania Tablicy) jednego odświeżenia BoardMonitor."""
        before = self.loads
        start = time.perf_counter()
        self.board.publish()
        return (time.perf_counter() - start) * 1000.0, self.loads - before

    def _wait(self, box, timeout=120.0):
        deadline = time.monotonic() + timeout
        while not box and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.0005)
        if not box:
            raise TimeoutError("brak odpowiedzi metody odczytu")

    def call(self, method):
        """Jedno żądanie metody odczytu tą samą drogą co adaptor D-Bus `/Board`; ms do odpowiedzi."""
        start = time.perf_counter()
        if method == "Snapshot":
            self.board.snapshot_json()
        elif method == "Work":
            self.board.work_json()
        elif method == "Badge":
            self.board.badge_json()
        elif method == "RenderFlow":
            self.board.render_flow(self.cards[0], "plan")
        elif method == "GetCardFlowSettings":
            box = []
            self.board.get_card_flow_settings(box.append)
            self._wait(box)
        elif method == "BoxStatus":
            box = []
            self.board.box_status("plasma-tide", box.append)
            self._wait(box)
            self.last_box_status = box[0]
        else:
            raise ValueError(method)
        return (time.perf_counter() - start) * 1000.0

    @contextlib.contextmanager
    def busy(self, ms):
        """Scenariusz #2: wątek przepływu stale zajęty pracą po `ms` (jak długi tick); 0 — bez obciążenia."""
        if ms <= 0:
            yield
            return
        stop = threading.Event()

        def work():
            time.sleep(ms / 1000.0)

        def again(_future=None):
            if not stop.is_set():
                self.flow._submit(work, again)
        again()
        try:
            yield
        finally:
            stop.set()

    def close(self):
        with contextlib.suppress(Exception):
            self.board.stop()
        with contextlib.suppress(Exception):
            self.flow.stop()


def measure(cards=30, refreshes=30, requests=100, busy_ms=0, base=None, keep=False, sha="", src=DEFAULT_SRC,
            box_file=True):
    own = base is None
    base = os.path.realpath(base or tempfile.mkdtemp(prefix="tide-perf-refresh-"))
    result = {"tool": "daemon/tests/perf_board_refresh.py", "sha": sha or git_sha(src), "src": src,
              "params": {"cards": cards, "refreshes": refreshes, "requests": requests, "busyMs": busy_ms,
                         "boxEnabledInSettingsFile": box_file},
              "cpuCount": os.cpu_count(), "loadavgBefore": loadavg(), "isolatedBase": base}
    service = None
    try:
        with isolated_env(base):
            started = time.perf_counter()
            service = Service(base, cards, box_file)
            result["setupMs"] = (time.perf_counter() - started) * 1000.0
            result["history"] = service.history()
            cleared = service.clear_caches()
            cold_ms, cold_loads = service.refresh()
            result["coldStart"] = {"ms": cold_ms, "boardLoads": cold_loads, "cleared": cleared}
            samples, loads = [], []
            for _ in range(refreshes):
                ms, count = service.refresh()
                samples.append(ms)
                loads.append(count)
            result["refresh"] = {"samplesMs": samples, "boardLoads": loads, "p95Ms": nearest_rank(samples),
                                 "maxMs": max(samples) if samples else None,
                                 "maxBoardLoads": max(loads) if loads else None}
            methods = {}
            with service.busy(busy_ms):
                for method in METHODS:
                    values = [service.call(method) for _ in range(requests)]
                    methods[method] = {"samplesMs": values, "p95Ms": nearest_rank(values)}
            result["methods"] = methods
            reply = json.loads(getattr(service, "last_box_status", "") or "{}")
            result["boxStatus"] = {"builder": "fake-incus", "slug": "plasma-tide", "ok": bool(reply.get("ok")),
                                   "state": reply.get("state"), "error": reply.get("error") or reply.get("message")}
    finally:
        if service is not None:
            service.close()
        result["loadavgAfter"] = loadavg()
        if own and not keep:
            shutil.rmtree(base, ignore_errors=True)
    return result


def summary(result):
    lines = [f"SHA {result['sha'] or '?'}; karty {result['params']['cards']}; loadavg przed: "
             f"{result['loadavgBefore']}; po: {result['loadavgAfter']}; rdzenie: {result['cpuCount']}",
             f"historia: {result['history']}",
             f"zimny start: {result['coldStart']['ms']:.1f} ms, wczytania Tablicy: {result['coldStart']['boardLoads']}",
             f"odświeżenie: p95 {result['refresh']['p95Ms']:.1f} ms, max {result['refresh']['maxMs']:.1f} ms, "
             f"wczytania Tablicy max {result['refresh']['maxBoardLoads']}"]
    lines += [f"{name}: p95 {data['p95Ms']:.2f} ms" for name, data in result["methods"].items()]
    lines.append(f"BoxStatus: {result.get('boxStatus')}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="plik JSON z wynikiem (surowe próbki)")
    parser.add_argument("--cards", type=int, default=30)
    parser.add_argument("--refreshes", type=int, default=30)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--busy-ms", type=int, default=0, help="zajęty wątek przepływu (scenariusz #2); 0 — wyłączone")
    parser.add_argument("--src", default=DEFAULT_SRC, help="katalog z daemon/ (np. wypakowana baza do porównania)")
    parser.add_argument("--sha", default="", help="SHA kodu z --src, gdy to nie jest repozytorium git")
    parser.add_argument("--keep", action="store_true", help="zostaw izolowany katalog danych")
    parser.add_argument("--engine-only-box", action="store_true",
                        help="box.enabled tylko w silniku, bez pliku ustawień (bez granicy Gita; nie do odbioru)")
    args = parser.parse_args(argv)
    src = os.path.realpath(args.src)
    sys.path.insert(0, os.path.join(src, "daemon"))
    result = measure(args.cards, args.refreshes, args.requests, args.busy_ms, keep=args.keep, sha=args.sha, src=src,
                     box_file=not args.engine_only_box)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(summary(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
