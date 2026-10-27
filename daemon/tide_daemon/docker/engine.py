"""Obszar Docker bez Qt: odczyt migawki, statystyk i miejsca na dysku, akcje i strumień zdarzeń.

Wszystko tu jest blokujące i woła Engine API (client.py) — docker_monitor.py uruchamia to w wątkach.
Akcje przyjmują tylko kontenery znane z ostatniej migawki (pełne ID, początek ID od 12 znaków albo
nazwa), a każdy wynik to słownik `{ok, error, …}` z błędem po polsku.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, wait

from . import disk as disk_mod
from . import snapshot as snap
from . import stats as stats_mod
from .client import DockerError, DockerUnavailable, EngineClient, container_path, log_lines
from .repos import RepoResolver

STOP_TIMEOUT_S = 10          # `docker stop -t 10` — po tym czasie Docker zabija kontener
STATS_TIMEOUT = 8.0          # całe Stats(): kontenery, które nie zdążyły, po prostu nie mają wiersza
STATS_WORKERS = 8
LOGS_MAX = 5000
LOGS_DEFAULT = 200
# Zdarzenia kontenerów, które nie zmieniają migawki (exec_* to np. każde sprawdzenie zdrowia).
QUIET_EVENTS = ("exec_create", "exec_start", "exec_die", "exec_detach", "attach", "detach", "top", "resize",
                "export", "commit", "copy", "archive-path", "extract-to-dir")
ACTIONS = ("start", "stop", "restart")
VERB = {"start": "uruchomić", "stop": "zatrzymać", "restart": "zrestartować", "remove": "usunąć"}


def unavailable(state: str, message: str, host: str = "") -> dict:
    return {"available": False, "state": state, "error": message, "host": host, "engine": {}, "live": False,
            "summary": snap.empty_summary(), "groups": []}


def _ok(**extra) -> dict:
    return {"ok": True, "error": "", **extra}


def _fail(message: str, **extra) -> dict:
    return {"ok": False, "error": message, **extra}


def _describe(exc: Exception, verb: str = "") -> str:
    if isinstance(exc, DockerUnavailable):
        return exc.message
    if isinstance(exc, DockerError):
        if exc.status == 404:
            return "nie ma takiego kontenera (już usunięty?)"
        prefix = f"Docker nie pozwolił {verb}: " if verb else "Docker odmówił: "
        return prefix + exc.message
    return f"błąd usługi ({type(exc).__name__})"


class DockerEngine:
    def __init__(self, client: EngineClient | None = None, workspace: str = "", clock=time.time):
        self.client = client or EngineClient()
        self.workspace = workspace
        self.clock = clock
        self.engine: dict = {}
        self._inspect: dict[str, tuple[tuple, dict]] = {}  # id → (klucz zmian, inspect)
        self.last: dict = unavailable("starting", "sprawdzanie Dockera…", self.client.host)
        self.containers: list[dict] = []  # z ostatniej udanej migawki

    # --- migawka ---------------------------------------------------------------------------

    def refresh(self) -> dict:
        """Nowa migawka. Niedostępny Docker to stan migawki, nie wyjątek."""
        try:
            if not self.engine:
                version = self.client.version()
                self.engine = {"version": version.get("Version", ""), "apiVersion": version.get("ApiVersion", "")}
            items = self.client.containers()
            details = self._details(items)
        except DockerUnavailable as exc:
            self.engine = {}
            self.last = unavailable(exc.state, exc.message, self.client.host)
            self.containers = []
            return self.last
        except DockerError as exc:
            self.last = unavailable("error", f"Docker odpowiedział błędem: {exc.message}", self.client.host)
            return self.last
        built = snap.build(items, details, RepoResolver(self.workspace))
        self.containers = [c for g in built["groups"] for c in g["containers"]]
        self.last = {"available": True, "state": "ok", "error": "", "host": self.client.host,
                     "engine": dict(self.engine), "live": False, **built}
        return self.last

    def _details(self, items: list) -> dict:
        """Inspect każdego kontenera — z pamięci, dopóki nie zmienił się jego stan z listy."""
        fresh: dict[str, tuple[tuple, dict]] = {}
        for item in items:
            ident = item.get("Id") or ""
            if not ident:
                continue
            health = (item.get("Health") or {}).get("Status") if isinstance(item.get("Health"), dict) else ""
            key = (item.get("State"), item.get("Status"), health)
            cached = self._inspect.get(ident)
            if cached and cached[0] == key:
                fresh[ident] = cached
                continue
            try:
                fresh[ident] = (key, self.client.inspect(ident))
            except DockerError:
                fresh[ident] = (key, {})  # kontener zniknął między listą a inspect — następne odświeżenie
        self._inspect = fresh
        return {ident: value for ident, (_key, value) in fresh.items()}

    def forget(self, ident: str = "") -> None:
        """Zdarzenie dla kontenera: jego inspect trzeba pobrać od nowa (np. zdrowie bez zmiany listy)."""
        if ident:
            self._inspect.pop(ident, None)
        else:
            self._inspect.clear()

    def find(self, ident: str) -> dict | None:
        if not ident:
            return None
        for c in self.containers:
            if c["id"] == ident or c["name"] == ident:
                return c
        if len(ident) >= 12:
            matches = [c for c in self.containers if c["id"].startswith(ident)]
            if len(matches) == 1:
                return matches[0]
        return None

    # --- zdarzenia -------------------------------------------------------------------------

    def events(self, stop, on_event, on_connected=None) -> None:
        """Jedno połączenie z /events (tylko kontenery). Wraca, gdy strumień się skończy albo stop;
        DockerUnavailable przy braku połączenia."""
        response = self.client.open("GET", "/events", {"filters": json.dumps({"type": ["container"]})},
                                    timeout=1.0, stop=stop)
        try:
            if response.status >= 400:
                raise DockerUnavailable("unavailable", f"Docker odmówił strumienia zdarzeń (HTTP {response.status})")
            if on_connected:
                on_connected()
            for line in response.lines():
                if stop.is_set():
                    return
                try:
                    event = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                action = (event.get("Action") or event.get("status") or "").split(":", 1)[0].strip()
                if action in QUIET_EVENTS:
                    continue
                on_event(event.get("id") or (event.get("Actor") or {}).get("ID") or "", action)
        except OSError as exc:
            if not stop.is_set():
                raise DockerUnavailable("unavailable", f"strumień zdarzeń zerwany ({exc})") from None
        finally:
            response.close()

    # --- statystyki ------------------------------------------------------------------------

    def stats(self, timeout: float = STATS_TIMEOUT) -> dict:
        running = [c for c in self.containers if c["running"]]
        result = {"ok": True, "error": "", "generatedTs": int(self.clock()), "containers": {},
                  "total": stats_mod.total([]), "missing": []}
        if not self.last.get("available"):
            result.update(ok=False, error=self.last.get("error") or "Docker niedostępny")
            return result
        if not running:
            return result

        def one(c):
            data = self.client.get_json(container_path(c["id"], "stats"), {"stream": "false"}, timeout=timeout)
            return stats_mod.entry(c, data or {})

        pool = ThreadPoolExecutor(max_workers=min(STATS_WORKERS, len(running)), thread_name_prefix="tide-docker-stats")
        futures = {pool.submit(one, c): c for c in running}
        done, _pending = wait(futures, timeout=timeout)
        pool.shutdown(wait=False, cancel_futures=True)
        entries = []
        for future, c in futures.items():
            if future in done and future.exception() is None:
                entries.append(future.result())
            else:
                result["missing"].append(c["id"])
        result["containers"] = {e["id"]: e for e in entries}
        result["total"] = stats_mod.total(entries)
        if running and not entries:
            result.update(ok=False, error="Docker nie podał statystyk w czasie")
        return result

    # --- dysk ------------------------------------------------------------------------------

    def disk(self) -> dict:
        try:
            df = self.client.get_json("/system/df", timeout=60.0) or {}
        except (DockerUnavailable, DockerError) as exc:
            return _fail(_describe(exc), generatedTs=int(self.clock()), categories=[], totalReclaimableBytes=0,
                         layersBytes=0)
        return _ok(generatedTs=int(self.clock()), **disk_mod.categories(df))

    def prune(self, selection_json: str) -> dict:
        chosen, problem = disk_mod.parse_selection(selection_json)
        if problem:
            return _fail(problem, reclaimedBytes=0, details=[])
        details = []
        for category in chosen:
            method, path, query = disk_mod.prune_request(category)
            try:
                _status, body = self.client.request(method, path, query, timeout=disk_mod.PRUNE_TIMEOUT)
                data = json.loads(body.decode("utf-8", "replace")) if body else {}
                details.append(disk_mod.prune_result(category, data))
            except (DockerUnavailable, DockerError, ValueError) as exc:
                details.append({"id": category, "label": disk_mod.LABELS[category], "ok": False, "reclaimedBytes": 0,
                                "removed": 0, "error": _describe(exc, "wyczyścić")})
        failed = [d for d in details if not d["ok"]]
        reclaimed = sum(d["reclaimedBytes"] for d in details)
        error = "; ".join(f"{d['label']}: {d['error']}" for d in failed)
        return {"ok": not failed, "error": error, "reclaimedBytes": reclaimed, "details": details}

    # --- akcje -----------------------------------------------------------------------------

    def action(self, ident: str, action: str) -> dict:
        if action not in ACTIONS:
            return _fail(f"nieznana akcja: {action}")
        target = self.find(ident)
        if target is None:
            return _fail("nie ma takiego kontenera", id=ident)
        return self._act(target, action)

    def _act(self, target: dict, action: str) -> dict:
        query = {"t": str(STOP_TIMEOUT_S)} if action in ("stop", "restart") else None
        timeout = STOP_TIMEOUT_S + 30 if action in ("stop", "restart") else 60
        try:
            status, _body = self.client.post(container_path(target["id"], action), query, timeout=timeout)
        except (DockerUnavailable, DockerError) as exc:
            return _fail(_describe(exc, VERB[action]), id=target["id"], name=target["name"])
        note = ""
        if status == 304:
            note = "już działał" if action == "start" else "już był zatrzymany"
        self.forget(target["id"])
        return _ok(id=target["id"], name=target["name"], note=note)

    def remove(self, ident: str) -> dict:
        target = self.find(ident)
        if target is None:
            return _fail("nie ma takiego kontenera", id=ident)
        try:
            state = ((self.client.inspect(target["id"]).get("State") or {}).get("Status") or "").lower()
        except (DockerUnavailable, DockerError) as exc:
            return _fail(_describe(exc), id=target["id"], name=target["name"])
        if state in ("running", "paused", "restarting"):
            return _fail("kontener działa — najpierw go zatrzymaj", id=target["id"], name=target["name"])
        try:
            # Bez force i bez v: działającego Docker i tak nie usunie, a wolumeny zostają.
            self.client.request("DELETE", container_path(target["id"]))
        except (DockerUnavailable, DockerError) as exc:
            return _fail(_describe(exc, VERB["remove"]), id=target["id"], name=target["name"])
        self.forget(target["id"])
        return _ok(id=target["id"], name=target["name"])

    def group_action(self, project: str, action: str) -> dict:
        """start/stop/restart wszystkich kontenerów projektu compose przez API (bez docker compose).

        start — niedziałające (bez jednorazowych `run`) w kolejności zależności; stop — działające
        w odwrotnej; restart — działające: zatrzymanie od końca, potem start od początku."""
        if action not in ACTIONS:
            return _fail(f"nieznana akcja: {action}", project=project, done=[], failed=[])
        if not project:
            return _fail("brak nazwy projektu", project=project, done=[], failed=[])
        try:
            items = self.client.containers({"label": [f"{snap.PROJECT_LABEL}={project}"]})
        except (DockerUnavailable, DockerError) as exc:
            return _fail(_describe(exc), project=project, done=[], failed=[])
        members = [snap.container(i, None) for i in items if (i.get("Labels") or {}).get(snap.PROJECT_LABEL) == project]
        if not members:
            return _fail("nie ma takiego projektu compose", project=project, done=[], failed=[])
        depends = {i.get("Id"): (i.get("Labels") or {}).get(snap.DEPENDS_LABEL, "") for i in items}
        ordered = snap.dependency_order(members, depends)
        if action == "start":
            steps = [(c, "start") for c in ordered if not c["running"] and not c["oneoff"]]
        elif action == "stop":
            steps = [(c, "stop") for c in reversed(ordered) if c["running"]]
        else:
            running = [c for c in ordered if c["running"]]
            steps = [(c, "stop") for c in reversed(running)] + [(c, "start") for c in running]
        done, failed = [], []
        for c, step in steps:
            result = self._act(c, step)
            if result["ok"]:
                if c["name"] not in done:
                    done.append(c["name"])
            else:
                failed.append({"name": c["name"], "action": step, "error": result["error"]})
        if not steps:
            note = {"start": "wszystko już działa", "stop": "nic nie działa", "restart": "nic nie działa"}[action]
            return _ok(project=project, done=[], failed=[], note=note)
        error = "; ".join(f"{f['name']}: {f['error']}" for f in failed)
        return {"ok": not failed, "error": error, "project": project, "done": done, "failed": failed, "note": ""}

    def logs(self, ident: str, n: int) -> dict:
        target = self.find(ident)
        if target is None:
            return _fail("nie ma takiego kontenera", id=ident, lines=[])
        n = LOGS_DEFAULT if n <= 0 else min(n, LOGS_MAX)
        try:
            _status, body = self.client.request("GET", container_path(target["id"], "logs"),
                                                {"stdout": "1", "stderr": "1", "tail": str(n)}, timeout=20.0)
        except (DockerUnavailable, DockerError) as exc:
            return _fail(_describe(exc), id=target["id"], name=target["name"], lines=[])
        tty = target.get("tty") or None
        return _ok(id=target["id"], name=target["name"], lines=log_lines(body, tty=tty, limit=n))
