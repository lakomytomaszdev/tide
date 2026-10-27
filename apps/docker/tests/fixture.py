"""Przykładowe dane widoku Docker na wzór pulpitu (27.10.2026): migawka, Stats(), Disk() i logi
w kształtach odpowiedzi tide-daemon (/Docker). Nazwy i obrazy jak na tym komputerze, bez ścieżek
prywatnych plików i zmiennych środowiskowych; czasy względem `now`, żeby „13 h” i „21 h temu” były
powtarzalne w testach i podglądzie.

Użycie jako podgląd: /usr/bin/python3 apps/docker/tests/fixture.py > build/docker-preview.json
"""
import hashlib
import json
import sys
import time

HOUR = 3600
DAY = 86400
GB = 1024 ** 3
MB = 1024 ** 2


def _id(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def port(host: int, container: int, http: bool = False, ip: str = "0.0.0.0") -> dict:
    return {"hostIp": ip, "hostPort": host, "containerPort": container, "proto": "tcp", "likelyHttp": http,
            "url": f"http://localhost:{host}" if http else ""}


def container(name, image, now, *, display="", project="", service="", running=True, health="", up=16 * HOUR,
              ports=(), failed=False, reason="", exit_code=0, finished_ago=0, state=None, testcontainers=False):
    state = state or ("running" if running else "exited")
    text = ("działa · zdrowy" if health == "healthy" else "działa") if running else (reason or "zakończony")
    return {
        "id": _id(name), "shortId": _id(name)[:12], "name": name, "displayName": display or name, "image": image,
        "service": service, "project": project, "state": state, "running": running, "health": health,
        "stateText": text, "startedTs": now - up if running else now - finished_ago - 3 * DAY,
        "finishedTs": 0 if running else now - finished_ago, "createdTs": now - 30 * DAY, "exitCode": exit_code,
        "failed": failed, "reason": reason, "oomKilled": False, "restartCount": 0, "ports": list(ports),
        "testcontainers": testcontainers, "oneoff": False, "tty": False,
    }


def group(gid, kind, label, containers, repo=None, project=""):
    running = sum(1 for c in containers if c["running"])
    return {"id": gid, "kind": kind, "project": project, "label": label, "workingDir": "",
            "repo": {"name": repo, "path": "/home/u/DevWorkspace/" + repo, "worktree": ""} if repo else None,
            "running": running, "total": len(containers), "stopped": len(containers) - running,
            "failed": sum(1 for c in containers if c["failed"]), "containers": containers}


def compose(project, repo, containers):
    return group(f"compose:{project}", "compose", project, containers, repo, project)


def snapshot(now: float, testcontainers: bool = True) -> dict:
    c = lambda *a, **k: container(*a, now=now, **k)  # noqa: E731
    cookapp = compose("cookapp", "CookApp", [
        c("cookapp-api", "cookapp-api", project="cookapp", service="api", health="healthy", up=13 * HOUR,
          ports=[port(8080, 8080, True)]),
        c("cookapp-docs", "cookapp-docs", project="cookapp", service="docs", up=13 * HOUR,
          ports=[port(8000, 80, True)]),
        c("cookapp-postgres", "postgres:16", project="cookapp", service="postgres", health="healthy", up=13 * HOUR,
          ports=[port(5434, 5432)]),
    ])
    app4 = compose("app4-release-localhost", "LogViewer", [
        c("app4-release-localhost-web-1", "app4-logviewer-localhost:log-value-condition", display="web-1",
          project="app4-release-localhost", service="web", up=70 * 60, ports=[port(15075, 8080, True, "127.0.0.1")]),
    ])
    youtrack = compose("youtrack", None, [
        c("youtrack", "jetbrains/youtrack:2026.2.19656", project="youtrack", service="youtrack",
          ports=[port(8111, 8080, True)]),
    ])
    legal = compose("agent-team", None, [
        c("legal-agents-postgres", "pgvector/pgvector:pg16", project="agent-team", service="postgres",
          health="healthy", ports=[port(5432, 5432)]),
    ])
    cockpit = compose("cockpit", "dev-dashboard", [
        c("cockpit-db", "postgres:16-alpine", project="cockpit", service="db", health="healthy",
          ports=[port(55432, 5432)]),
    ])
    bo = compose("bo-catalog", "BO", [
        c("bocatalog-postgres", "postgres:16", project="bo-catalog", service="postgres", ports=[port(5433, 5432)]),
    ])
    hotfix = compose("app4-hotfix-tom-142", None, [
        c("app4-hotfix-tom-142-web-1", "app4-logviewer-hotfix:tom-142", display="web-1", project="app4-hotfix-tom-142",
          service="web", running=False, finished_ago=2 * DAY + 3 * HOUR, exit_code=143),
    ])
    release_i = compose("app4-release-i", "LogViewer", [
        c("app4-release-i-web-1", "app4-logviewer-i:local-ui", display="web-1", project="app4-release-i", service="web",
          running=False, finished_ago=2 * DAY + 6 * HOUR, exit_code=143),
    ])
    release_t = compose("app4-release-t", None, [
        c("app4-release-t-postgres-1", "postgres:16-alpine", display="postgres-1", project="app4-release-t",
          service="postgres", running=False, finished_ago=15 * DAY, exit_code=0),
    ])
    bot = compose("translationsdownloader", None, [
        c("translations-slack-bot", "translationsdownloader-slack-bot", project="translationsdownloader",
          service="slack-bot", running=False, failed=True, exit_code=137, finished_ago=21 * HOUR,
          reason="zabity (kod 137), możliwy brak pamięci"),
    ])
    standalone = group("standalone", "standalone", "bez compose", [
        c("legal-agents-library-work", "pgvector/pgvector:pg16", ports=[port(55440, 5432, ip="127.0.0.1")]),
    ])
    groups = [bo, cockpit, legal, app4, cookapp, youtrack, hotfix, release_i, release_t, bot, standalone]
    if testcontainers:
        groups.append(group("testcontainers", "testcontainers", "testcontainers", [
            c("testcontainers-ryuk-c6715127", "testcontainers/ryuk:0.11.0", up=8 * 60, testcontainers=True,
              ports=[port(34137, 8080)]),
        ]))
    every = [x for g in groups for x in g["containers"]]
    visible = [x for x in every if not x["testcontainers"]]
    return {
        "available": True, "state": "ok", "error": "", "host": "unix:///var/run/docker.sock",
        "engine": {"version": "29.9.2", "apiVersion": "1.56"}, "live": True,
        "summary": {"total": len(every), "running": sum(1 for x in visible if x["running"]),
                    "stopped": sum(1 for x in visible if not x["running"]),
                    "failed": sum(1 for x in visible if x["failed"]),
                    "projects": sum(1 for g in groups if g["kind"] == "compose" and g["running"]),
                    "projectsTotal": sum(1 for g in groups if g["kind"] == "compose"),
                    "testcontainers": sum(1 for x in every if x["testcontainers"]),
                    "testcontainersRunning": sum(1 for x in every if x["testcontainers"] and x["running"])},
        "groups": groups,
    }


# CPU (jak docker stats, 100% = jeden rdzeń) i pamięć działających kontenerów.
USAGE = {
    "cookapp-api": (0.4, 212 * MB), "cookapp-docs": (0.0, 11 * MB), "cookapp-postgres": (0.1, 48 * MB),
    "app4-release-localhost-web-1": (1.8, 640 * MB), "youtrack": (0.9, int(1.1 * GB)),
    "legal-agents-postgres": (0.2, 96 * MB), "cockpit-db": (0.0, 31 * MB), "bocatalog-postgres": (0.0, 29 * MB),
    "legal-agents-library-work": (0.0, 27 * MB), "testcontainers-ryuk-c6715127": (0.0, 6 * MB),
}


def stats(snap: dict, now: float) -> dict:
    cpus, limit = 16, 31 * GB
    containers = {}
    for g in snap["groups"]:
        for c in g["containers"]:
            if c["running"] and c["name"] in USAGE:
                cpu, mem = USAGE[c["name"]]
                containers[c["id"]] = {"id": c["id"], "name": c["name"], "cpuPercent": cpu, "cpus": cpus,
                                       "memBytes": mem, "memLimitBytes": limit, "memPercent": round(mem / limit * 100, 1)}
    total_cpu = sum(v["cpuPercent"] for v in containers.values())
    total_mem = sum(v["memBytes"] for v in containers.values())
    return {"ok": True, "error": "", "generatedTs": int(now), "containers": containers,
            "total": {"cpuPercent": total_cpu, "hostCpuPercent": round(total_cpu / cpus + 2.8, 1), "cpus": cpus,
                      "memBytes": total_mem, "containers": len(containers)},
            "missing": []}


def disk(now: float) -> dict:
    return {
        "ok": True, "error": "", "generatedTs": int(now), "layersBytes": int(10.6 * GB),
        "categories": [
            {"id": "cache", "label": "Pamięć podręczna budowania", "note": "121 wpisów, żaden nie jest w użyciu",
             "count": 121, "total": 121, "reclaimableBytes": int(5.9 * GB), "totalBytes": int(6.8 * GB), "warn": False},
            {"id": "images", "label": "Nieużywane obrazy", "note": "2 obrazy bez żadnego kontenera", "count": 2,
             "total": 13, "reclaimableBytes": int(1.0 * GB), "totalBytes": int(10.4 * GB), "warn": False},
            {"id": "volumes", "label": "Wolumeny bez kontenera", "note": "10 z 20 · mogą trzymać dane baz",
             "count": 10, "total": 20, "reclaimableBytes": int(0.4 * GB), "totalBytes": int(2.4 * GB), "warn": True},
            {"id": "stopped", "label": "Zatrzymane kontenery", "note": "4 · ich wolumeny zostają", "count": 4,
             "total": 14, "reclaimableBytes": 86_016, "totalBytes": 17 * MB, "warn": False, "anonymousVolumes": 0},
        ],
        "totalReclaimableBytes": int(7.3 * GB),
    }


LOGS = {
    "cookapp-api": [
        "12:01:44 info: Now listening on: http://[::]:8080",
        "12:01:44 info: Application started. Hosting environment: Development",
        "12:03:10 info: GET /api/cookapp?page=1 → 200 w 41 ms",
        "12:03:12 info: GET /api/cookapp/118 → 200 w 12 ms",
        {"text": "12:04:02 warn: wolne zapytanie recipe_tags (612 ms)", "stderr": True},
        "12:05:30 info: GET /api/cookapp/tags → 200 w 9 ms",
    ],
    "translations-slack-bot": [
        "21:14:07 Slack socket connected",
        "21:52:30 Downloading translations batch 13/40",
        "21:58:51 Downloading translations batch 14/40",
        "21:58:53 Parsing 4812 keys…",
        {"text": "21:58:54 <html><b>not bold</b></html> — tekst z kontenera nigdy jako HTML", "stderr": True},
    ],
    "youtrack": [
        "11:02:10 [JetBrains YouTrack] Service started",
        "11:02:11 Listening on 0.0.0.0:8080",
        "12:03:41 GET /api/issues?query=for:me → 200",
    ],
}


def data(now: float | None = None, testcontainers: bool = True) -> dict:
    """Całość dla --data: {snapshot, stats, disk, logs}."""
    now = time.time() if now is None else now
    snap = snapshot(now, testcontainers)
    return {"snapshot": snap, "stats": stats(snap, now), "disk": disk(now), "logs": LOGS}


if __name__ == "__main__":
    json.dump(data(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
