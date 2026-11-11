#!/usr/bin/python3
"""Reproduktor odzyskiwania po restarcie i kosztu odczytów w ticku (K-98 #3, PT-344).

Na izolowanych danych (fixture `FlowInProcess`: osobne HOME, XDG_STATE_HOME, XDG_CONFIG_HOME, XDG_RUNTIME_DIR,
Tablica i repozytoria w katalogu tymczasowym; atrapy `claude`, `systemd-run`, `systemctl` z `flowfakes.py` — procesy
kroków są prawdziwymi procesami) buduje usługę w procesie, uruchamia trzy karty „Od razu” z żyjącym krokiem
(`ATRAPA: czekaj`) i karty-wypełniacze z historią, a potem symuluje awarię usługi:

- LIVE — awaria między wynikiem startu jednostki a `step.started` (ucięty ostatni `step.started`); proces żyje.
  Oczekiwane: jeden odtworzony `step.started`, ten sam żywy proces, bez drugiej jednostki;
- DEAD — proces kroku zabity (SIGKILL grupy) w czasie, gdy usługa nie działała. Oczekiwane: dokładnie jedno
  rozliczenie końca kroku (`step.finished`), bez nowego procesu tego kroku;
- UNKNOWN — awaria po zamiarze `step-start`, przed wynikiem; menedżer jednostek nie umie ustalić stanu (atrapa
  `unit_state` zwraca `unknown` tylko dla tej jednostki). Oczekiwane: otwarty zamiar zostaje (blokada widoczna
  w stanie karty), bez wyniku, bez `step.started`, bez drugiego procesu.

Potem nowy silnik (jak po restarcie usługi, bez procesów w pamięci) wykonuje `--ticks` ticków w wątku pomiaru:
pierwszy to tick restartu (odzyskiwanie), kolejne to ciepłe ticki. Dla każdego ticku wynik podaje:

- `readComplete` — liczbę pełnych ścisłych odczytów historii (`Journal.read_complete`), w tym `mutationReads`
  (odczyty wewnątrz `Journal.append`/`append_complete`, czyli świeży odczyt przed zapisem),
- dla `read_process_history`, `process_identities`, `_recover_started_steps`, `recover_network`: liczbę wywołań,
  czas (ms, włącznie z zagnieżdżonymi) i pełne odczyty wykonane w ich trakcie,
- `boardLoads` — wczytania Tablicy na granicy magazynu (`BoardStore.load`), `boardWrites` — zapisy plików
  Tablicy (`BoardStore._write`) oraz `loadSites` — funkcje silnika, z których padło wczytanie.

Wynik zawiera też SHA, `/proc/loadavg` przed i po, liczbę rdzeni, rozmiar historii (zdarzenia i bajty dzienników),
stan publiczny kart po restarcie (`engine.derived`) i wynik sprawdzeń (`checks`). Reproduktor nie zmienia kodu
usługi — tylko owija funkcje na czas pomiaru — więc ten sam plik działa na kodzie bazy i po zmianie.

Użycie (z katalogu repozytorium):
    /usr/bin/python3 daemon/tests/perf_tick_recovery.py --out wynik.json [--fillers 27] [--padding 40] [--ticks 4]
        [--src <katalog z daemon/>] [--sha <SHA>]

Testy jednostkowe nie mierzą czasu na sztywno; ten skrypt jest wyłącznie narzędziem pomiaru i sprawdzenia zachowania.
"""
import argparse
import collections
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.dirname(os.path.dirname(HERE))
MEASURED = ("read_process_history", "process_identities", "_recover_started_steps", "recover_network")
SCENARIOS = ("live", "dead", "unknown")


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


def _tests(name):
    """Moduł pakietu testów `daemon.tests` — z runnera albo ze skryptu (`main` dodaje katalog `--src` do sys.path)."""
    import importlib
    return importlib.import_module(f"{__package__ or 'daemon.tests'}.{name}")


def _fixture_class():
    FlowInProcess = _tests("test_cardflow").FlowInProcess

    class Harness(FlowInProcess):
        def runTest(self):        # pragma: no cover — fixture używana poza runnerem testów
            pass
    return Harness


class Counters:
    """Liczniki jednego ticku; owijki funkcji dopisują się do bieżącego obiektu."""

    def __init__(self):
        self.read_complete = 0
        self.mutation_reads = 0
        self.board_loads = 0
        self.board_writes = 0
        self.load_sites = collections.Counter()
        self.calls = {name: 0 for name in MEASURED}
        self.ms = {name: 0.0 for name in MEASURED}
        self.reads_in = {name: 0 for name in MEASURED}
        self.active = collections.Counter()
        self.in_append = 0
        self.phase = None
        self.started = time.perf_counter()

    def as_dict(self, ms):
        return {"ms": ms, "recoveryPhase": self.phase,
                "readComplete": self.read_complete, "mutationReads": self.mutation_reads,
                "boardLoads": self.board_loads, "boardWrites": self.board_writes,
                "loadSites": dict(self.load_sites.most_common()),
                "functions": {name: {"calls": self.calls[name], "ms": self.ms[name],
                                     "readComplete": self.reads_in[name]} for name in MEASURED}}


def _load_site():
    """Najbliższe funkcje silnika nad `BoardStore.load` (bez magazynu i kontekstu odczytu)."""
    names = []
    for frame in reversed(traceback.extract_stack()[:-2]):
        path = frame.filename.replace(os.sep, "/")
        if "/tide_daemon/" not in path or path.endswith(("/board/store.py", "/board/readcontext.py")):
            continue
        names.append(frame.name)
        if len(names) == 2:
            break
    return "<".join(names) or "?"


class Instrument:
    """Owija funkcje usługi na czas pomiaru (bez zmiany kodu) i przywraca je na końcu."""

    def __init__(self, engine):
        self.engine = engine
        self.current = Counters()
        self._undo = []

    def _timed(self, name, fn):
        instrument = self

        def wrapper(*args, **kwargs):
            counters = instrument.current
            counters.calls[name] += 1
            counters.active[name] += 1
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                counters.ms[name] += (time.perf_counter() - start) * 1000.0
                counters.active[name] -= 1
                if name == "recover_network" and counters.phase is None:
                    # Faza odzyskiwania ticku: od początku ticku do końca pierwszego `recover_network`.
                    counters.phase = {"readComplete": counters.read_complete,
                                      "mutationReads": counters.mutation_reads,
                                      "processIdentities": counters.calls["process_identities"],
                                      "ms": (time.perf_counter() - counters.started) * 1000.0}
        wrapper.__wrapped__ = fn
        return wrapper

    def _set(self, owner, attr, value):
        own = attr in vars(owner)        # moduł: zawsze; obiekt: atrybut instancji (nie metoda klasy)
        self._undo.append((owner, attr, vars(owner).get(attr), own))
        setattr(owner, attr, value)

    def install(self):
        from tide_daemon.cardflow import boxstep, processidentity, trialhold
        engine, journal, store = self.engine, self.engine.journal, self.engine.store
        real_read = journal.read_complete

        def read_complete(card):
            counters = self.current
            counters.read_complete += 1
            if counters.in_append:
                counters.mutation_reads += 1
            for name, depth in counters.active.items():
                if depth:
                    counters.reads_in[name] += 1
            return real_read(card)
        self._set(journal, "read_complete", read_complete)
        for attr in ("append", "append_complete"):
            real = getattr(journal, attr)

            def appending(*args, _real=real, **kwargs):
                self.current.in_append += 1
                try:
                    return _real(*args, **kwargs)
                finally:
                    self.current.in_append -= 1
            self._set(journal, attr, appending)
        real_load = store.load

        def load(*args, **kwargs):
            self.current.board_loads += 1
            self.current.load_sites[_load_site()] += 1
            return real_load(*args, **kwargs)
        self._set(store, "load", load)
        real_write = store._write

        def write(*args, **kwargs):
            self.current.board_writes += 1
            return real_write(*args, **kwargs)
        self._set(store, "_write", write)
        self._set(engine, "_recover_started_steps",
                  self._timed("_recover_started_steps", engine._recover_started_steps))
        originals = {"read_process_history": trialhold.read_process_history,
                     "process_identities": processidentity.process_identities,
                     "recover_network": boxstep.recover_network}
        wrapped = {name: self._timed(name, fn) for name, fn in originals.items()}
        for module in list(sys.modules.values()):
            if not getattr(module, "__name__", "").startswith("tide_daemon"):
                continue
            for name, fn in originals.items():
                if getattr(module, name, None) is fn:
                    self._set(module, name, wrapped[name])

    def restore(self):
        while self._undo:
            owner, attr, old, own = self._undo.pop()
            if own:
                setattr(owner, attr, old)
            else:
                delattr(owner, attr)

    def tick(self):
        self.current = Counters()
        start = time.perf_counter()
        self.engine.tick()
        return self.current.as_dict((time.perf_counter() - start) * 1000.0)


def _write_events(journal, card, events):
    text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
    for path in (journal.path(card), journal.mirror_path(card)):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


def _kill_group(pid):
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


class Scenario:
    def __init__(self, fixture, fillers, padding):
        flowfakes, wait_until = _tests("flowfakes"), _tests("privatebus").wait_until
        self.fx, self.flowfakes, self.wait_until = fixture, flowfakes, wait_until
        self.cards, self.steps, self.pids, self.fillers = {}, {}, {}, []
        journal = fixture.flow.engine.journal
        for index, name in enumerate(SCENARIOS):
            repo = os.path.join(fixture.base, "ws", f"repo-{name}")
            os.makedirs(repo)
            for args in (("init", "-q"), ("commit", "-q", "--allow-empty", "-m", "init")):
                subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)
            reply = json.loads(fixture.board.upsert_project(json.dumps({"repo": repo})))
            if not reply.get("ok"):
                raise RuntimeError(f"projekt {name}: {reply}")
            slug = reply.get("slug") or reply.get("project", {}).get("slug") or os.path.basename(repo)
            card = json.loads(fixture.board.create_card(json.dumps({
                "title": f"Odzyskiwanie {name}", "project": slug, "description": "Pomiar.\n\nATRAPA: czekaj",
                "column": "todo"})))
            if not card.get("ok"):
                raise RuntimeError(f"karta {name}: {card}")
            self.cards[name] = card["id"]
        for name, card in self.cards.items():
            reply = fixture.start(card)
            if not reply.get("ok"):
                raise RuntimeError(f"start {name}: {reply}")
        for name, card in self.cards.items():
            if not wait_until(lambda c=card: any(e["type"] == "step.started" for e in journal.read(c)), timeout=60):
                raise RuntimeError(f"{name}: krok nie wystartował: {journal.read(card)}")
            step = next(e for e in journal.read(card) if e["type"] == "step.started")
            pid = wait_until(lambda s=step: flowfakes.unit_pid(fixture.runtime, s["unit"]), timeout=10)
            if not pid:
                raise RuntimeError(f"{name}: brak pid jednostki")
            self.steps[name], self.pids[name] = step, pid
        rows = [{"workId": f"#{i}", "title": f"Ticket {i}", "blockers": [], "executor": "Sonnet",
                 "reviewer": "Opus", "class": "standard", "note": ""} for i in range(1, 4)]
        for index in range(fillers):
            card = json.loads(fixture.board.create_card(json.dumps({
                "title": f"Wypełniacz {index + 1}", "project": "proj", "description": "Historia.",
                "column": "todo"})))["id"]
            journal.append(card, "card.started", entry="plan", executor="Sonnet", program="claude",
                           provider="anthropic")
            journal.append(card, "plan.proposed", title=f"Plan {card}", prefix="PT", rows=rows, planner="Opus",
                           box=None)
            journal.append(card, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])
            for number in range(padding):
                # Rozmiar historii: znane zdarzenie metadanych bez roli procesu (nieznany typ to tożsamość unknown).
                journal.append(card, "plan.checked", valid=False, step=f"{card}-plan-{number}",
                               errors=[f"wiersz {number}: brak kryterium akceptacji " + "x" * 160])
            self.fillers.append(card)

    def crash(self):
        """Usługa pada: monitor zatrzymany, dzienniki jak po awarii w odpowiednim oknie, DEAD zabity."""
        fx = self.fx
        fx.stop_flow()
        journal = fx.flow.engine.journal
        live = self.cards["live"]
        events = journal.read_complete(live)
        cut = max(i for i, e in enumerate(events) if e["type"] == "step.started")
        _write_events(journal, live, events[:cut])
        unknown = self.cards["unknown"]
        events = journal.read_complete(unknown)
        key = self.steps["unknown"]["step"]
        cut = next(i for i, e in enumerate(events)
                   if e["type"] == "result" and e.get("action") == "step-start" and e.get("key") == key)
        _write_events(journal, unknown, events[:cut])
        _kill_group(self.pids["dead"])
        if not self.wait_until(lambda: not self.flowfakes.alive(self.pids["dead"]), timeout=10):
            raise RuntimeError("proces DEAD nadal żyje")

    def history(self, journal):
        cards = [*self.cards.values(), *self.fillers]
        events = size = 0
        for card in cards:
            events += len(journal.read(card))
            for path in (journal.path(card), journal.mirror_path(card)):
                with contextlib.suppress(OSError):
                    size += os.path.getsize(path)
        return {"cards": len(cards), "events": events, "bytes": size}

    def units(self):
        folder = os.path.join(self.fx.runtime, "fake-units")
        with contextlib.suppress(OSError):
            return sorted(os.listdir(folder))
        return []

    def checks(self, engine):
        journal = engine.journal
        out = {}
        for name, card in self.cards.items():
            events = journal.read(card)
            key = self.steps[name]["step"]
            started = [e for e in events if e["type"] == "step.started" and e.get("step") == key]
            other_started = [e for e in events if e["type"] == "step.started" and e.get("step") != key]
            finished = [e for e in events if e["type"] == "step.finished" and e.get("step") == key]
            results = [e for e in events if e["type"] == "result" and e.get("action") == "step-start"
                       and e.get("key") == key]
            st = journal.state(card)
            # Kolejna próba po rozliczonym końcu (ponowienie przepływu) to nowy krok, nie drugi proces tego kroku.
            accounted = finished[0]["seq"] if finished else None
            retries = [e for e in other_started if accounted is not None and e["seq"] > accounted]
            out[name] = {"stepStarted": len(started), "otherStepStarted": len(other_started) - len(retries),
                         "retriesAfterAccounting": [e.get("step") for e in retries],
                         "retryUnits": [e.get("unit") for e in retries],
                         "stepFinished": [f.get("result") for f in finished],
                         "startResults": [bool(r.get("ok")) for r in results],
                         "openIntent": any(i.get("action") == "step-start" and i.get("key") == key
                                           for i in st.open_intents(include_background=True)),
                         "running": [s.get("step") for s in st.running()],
                         "alive": self.flowfakes.alive(self.pids[name]),
                         "sameOwner": bool(started) and started[0].get("unit") == self.steps[name]["unit"]}
            with contextlib.suppress(Exception):
                derived = engine.derived({"id": card, "column": "progress"}) or {}
                flow = derived.get("flow") or {}
                out[name]["public"] = {"column": derived.get("column"), "note": derived.get("note"),
                                       "state": flow.get("state"), "running": flow.get("running")}
        live, dead, unknown = out["live"], out["dead"], out["unknown"]
        out["ok"] = {
            "liveOneOwner": live["stepStarted"] == 1 and live["sameOwner"] and live["alive"]
            and not live["otherStepStarted"],
            "deadAccounted": len(dead["stepFinished"]) == 1 and not dead["alive"] and not dead["otherStepStarted"],
            "unknownBlocked": unknown["openIntent"] and not unknown["startResults"] and not unknown["stepStarted"]
            and unknown["alive"] and not unknown["otherStepStarted"],
        }
        return out

    def cleanup(self):
        for pid in self.pids.values():
            _kill_group(pid)


def restart_engine(fx):
    """Nowy silnik na tej samej Tablicy, bez monitora (ticki wywołuje pomiar) — jak usługa po restarcie."""
    from tide_daemon.cardflow.engine import FlowEngine
    from tide_daemon.models import executors
    pass_merge_checks = _tests("test_cardflow").pass_merge_checks
    engine = FlowEngine(fx.store, claude_projects=os.path.join(fx.base, "home", ".claude", "projects"),
                        systemd_run=fx.fakes["systemd-run"], catalog=executors.default_catalog,
                        tmp_root=os.path.join(fx.base, "tmp"), settings=fx.settings,
                        which=lambda name: fx.fakes.get(name), grace=5.0)
    pass_merge_checks(engine)
    return engine


def measure(fillers=27, padding=40, ticks=4, sha="", src=DEFAULT_SRC):
    result = {"tool": "daemon/tests/perf_tick_recovery.py", "sha": sha or git_sha(src), "src": src,
              "params": {"fillers": fillers, "padding": padding, "ticks": ticks},
              "cpuCount": os.cpu_count(), "loadavgBefore": loadavg()}
    fx = _fixture_class()()
    scenario = None
    try:
        fx.setUp()
        result["isolatedBase"] = fx.base
        scenario = Scenario(fx, fillers, padding)
        units_before = scenario.units()
        scenario.crash()
        engine = restart_engine(fx)
        result["history"] = scenario.history(engine.journal)
        from tide_daemon.tasks import stepstop
        real_state = stepstop.unit_state
        unknown_unit = scenario.steps["unknown"]["unit"]

        def unit_state(unit, *args, **kwargs):
            return "unknown" if unit == unknown_unit else real_state(unit, *args, **kwargs)
        instrument = Instrument(engine)
        instrument.install()
        samples = []
        try:
            with mock_attr(stepstop, "unit_state", unit_state):
                for _ in range(ticks):
                    samples.append(instrument.tick())
        finally:
            instrument.restore()
        result["restartTick"] = samples[0] if samples else None
        result["warmTicks"] = samples[1:]
        result["unitsBefore"], result["unitsAfter"] = units_before, scenario.units()
        result["checks"] = scenario.checks(engine)
        retry_units = {u + ".pid" for item in SCENARIOS for u in result["checks"][item]["retryUnits"]}
        result["checks"]["ok"]["noNewUnit"] = set(result["unitsAfter"]) - retry_units == set(units_before)
        result["warnings"] = dict(getattr(engine, "_warnings", {}))
    finally:
        if scenario is not None:
            scenario.cleanup()
        with contextlib.suppress(Exception):
            fx.doCleanups()
        result["loadavgAfter"] = loadavg()
    return result


@contextlib.contextmanager
def mock_attr(owner, attr, value):
    old = getattr(owner, attr)
    setattr(owner, attr, value)
    try:
        yield
    finally:
        setattr(owner, attr, old)


def summary(result):
    def line(label, tick):
        f, phase = tick["functions"], tick.get("recoveryPhase") or {}
        return (f"{label}: {tick['ms']:.1f} ms; faza odzyskiwania: pełne odczyty {phase.get('readComplete')} "
                f"(przed zapisem {phase.get('mutationReads')}), process_identities {phase.get('processIdentities')}, "
                f"{phase.get('ms') or 0:.1f} ms; cały tick: pełne odczyty historii {tick['readComplete']} "
                f"(przed zapisem {tick['mutationReads']}); wczytania Tablicy {tick['boardLoads']} "
                f"(zapisy {tick['boardWrites']}); " +
                "; ".join(f"{n} {f[n]['calls']}× {f[n]['ms']:.1f} ms odczyty {f[n]['readComplete']}" for n in MEASURED))
    lines = [f"SHA {result['sha'] or '?'}; loadavg przed: {result['loadavgBefore']}; po: {result['loadavgAfter']}; "
             f"rdzenie: {result['cpuCount']}", f"historia: {result.get('history')}"]
    if result.get("restartTick"):
        lines.append(line("tick restartu", result["restartTick"]))
    lines += [line(f"ciepły tick {i + 1}", t) for i, t in enumerate(result.get("warmTicks") or [])]
    lines.append(f"sprawdzenia: {result.get('checks', {}).get('ok')}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="plik JSON z wynikiem (surowe liczniki i czasy)")
    parser.add_argument("--fillers", type=int, default=27, help="karty z historią bez procesów (razem 30 kart)")
    parser.add_argument("--padding", type=int, default=40, help="dodatkowe zdarzenia historii na kartę-wypełniacz")
    parser.add_argument("--ticks", type=int, default=4, help="tick restartu + ciepłe ticki")
    parser.add_argument("--src", default=DEFAULT_SRC, help="katalog z daemon/ (np. wypakowana baza do porównania)")
    parser.add_argument("--sha", default="", help="SHA kodu z --src, gdy to nie jest repozytorium git")
    args = parser.parse_args(argv)
    src = os.path.realpath(args.src)
    # Pakiet testów jak w runnerze (`daemon.tests`, z izolacją `tools.test_isolation`) i kod usługi z `--src`.
    # Katalog skryptu (domyślnie sys.path[0]) usuwamy, żeby moduły testów nie dały się zaimportować drugi raz.
    sys.path[:] = [p for p in sys.path if os.path.realpath(p or ".") != HERE]
    sys.path[:0] = [src, os.path.join(src, "daemon")]
    result = measure(args.fillers, args.padding, args.ticks, sha=args.sha, src=src)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")
    print(summary(result), flush=True)
    return 0 if all(result.get("checks", {}).get("ok", {}).values()) else 1


if __name__ == "__main__":
    sys.exit(main())
