"""Migawka kontenerów: grupy compose, repozytoria, porty i stany słowami — bez Qt i bez sieci.

Wejście to lista z `GET /containers/json?all=1` i szczegóły z `GET /containers/{id}/json`
(czasy startu i końca, kod wyjścia, OOM, zdrowie, TTY). Wyjście to słownik gotowy do JSON-a
(opis pól: docs/tide-daemon.md, sekcja Docker).

Grupy:
- `compose:<projekt>` — etykieta `com.docker.compose.project`; repozytorium z etykiety
  `com.docker.compose.project.working_dir` (repos.py),
- `testcontainers` — kontenery z etykietą `org.testcontainers` (okno domyślnie je chowa),
- `standalone` — pozostałe, „bez compose”.
"""
from __future__ import annotations

from datetime import datetime, timezone

PROJECT_LABEL = "com.docker.compose.project"
WORKDIR_LABEL = "com.docker.compose.project.working_dir"
SERVICE_LABEL = "com.docker.compose.service"
NUMBER_LABEL = "com.docker.compose.container-number"
ONEOFF_LABEL = "com.docker.compose.oneoff"
DEPENDS_LABEL = "com.docker.compose.depends_on"
TESTCONTAINERS_LABEL = "org.testcontainers"

# Kody wyjścia, które nie są awarią: 0 i 143 (SIGTERM — zwykłe `docker stop`).
OK_EXIT_CODES = (0, 143)

# Porty i obrazy, których nie otwieramy w przeglądarce (bazy, kolejki, pamięci podręczne).
NON_HTTP_PORTS = {
    5432, 3306, 33060, 1433, 1521, 27017, 27018, 6379, 6380, 11211, 9042, 7000, 7001, 5672, 5671,
    9092, 9093, 2181, 2888, 3888, 26257, 9300, 4369, 25672, 1883, 8883, 61616, 6650, 4222, 6222,
    3050, 389, 636, 53, 25, 587, 465, 110, 143, 993, 995, 22,
}
NON_HTTP_IMAGES = (
    "postgres", "pgvector", "postgis", "timescale", "mysql", "mariadb", "mongo", "redis", "valkey", "keydb",
    "memcached", "rabbitmq", "kafka", "zookeeper", "mssql", "sqlserver", "oracle", "cassandra", "scylla",
    "cockroach", "clickhouse", "nats", "mosquitto", "emqx", "etcd", "consul", "testcontainers/ryuk",
    "firebird", "ravendb-backup",
)
# Porty WWW obrazów z listy wyżej (panele administracyjne), które jednak są HTTP.
HTTP_PORTS_OF_NON_HTTP_IMAGES = {15672, 8222, 8161, 8500, 18083, 8123}
HTTPS_PORTS = {443, 8443, 9443}


def parse_time(value) -> float:
    """Czas Dockera (RFC 3339 z nanosekundami) → sekundy epoki; „0001-01-01…” i błędy → 0."""
    if not value or not isinstance(value, str):
        return 0.0
    text = value.replace("Z", "+00:00")
    # fromisoformat zjada najwyżej 6 cyfr ułamka — nanosekundy przycinamy.
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        while tail and tail[0].isdigit():
            digits, tail = digits + tail[0], tail[1:]
        text = f"{head}.{digits[:6]}{tail}" if digits else head + tail
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    return ts if ts > 0 else 0.0


def container_name(item: dict) -> str:
    names = item.get("Names") or []
    name = names[0] if names else item.get("Name") or ""
    return name.lstrip("/") or (item.get("Id") or "")[:12]


def display_name(name: str, labels: dict) -> str:
    """Nazwa w obrębie projektu: wygenerowana przez compose „app4-release-localhost-web-1” → „web-1”.
    Własna nazwa (`container_name: cockpit-db`) zostaje bez zmian; pełna nazwa i tak jest w `name`."""
    project = labels.get(PROJECT_LABEL, "")
    service = labels.get(SERVICE_LABEL, "")
    number = labels.get(NUMBER_LABEL, "")
    if project and service and number:
        for sep in ("-", "_"):
            if name == sep.join((project, service, number)):
                return sep.join((service, number))
    return name


def _image_base(image: str) -> str:
    """„docker.io/library/postgres:16” → „postgres”, „pgvector/pgvector:pg16” → „pgvector/pgvector”."""
    base = image.split("@", 1)[0]
    last = base.rsplit("/", 1)[-1]
    if ":" in last:
        base = base[: len(base) - len(last)] + last.split(":", 1)[0]
    for prefix in ("docker.io/", "library/"):
        if base.startswith(prefix):
            base = base[len(prefix):]
    return base.lower()


def non_http_image(image: str) -> bool:
    base = _image_base(image)
    return any(base == key or base.startswith(key) or ("/" + key) in base or base.split("/")[-1].startswith(key)
               for key in NON_HTTP_IMAGES)


def likely_http(container_port: int, proto: str, image: str) -> bool:
    """Czy port warto otworzyć w przeglądarce: nie dla UDP, znanych portów baz i kolejek ani obrazów baz
    (poza ich panelami WWW)."""
    if proto != "tcp":
        return False
    if container_port in HTTP_PORTS_OF_NON_HTTP_IMAGES:
        return True
    if container_port in NON_HTTP_PORTS:
        return False
    return not non_http_image(image)


def ports(item: dict, image: str) -> list[dict]:
    """Opublikowane porty bez powtórek IPv4/IPv6: [{hostIp, hostPort, containerPort, proto, likelyHttp, url}]."""
    seen: dict[tuple, dict] = {}
    for port in item.get("Ports") or []:
        public = port.get("PublicPort")
        if not public:
            continue  # tylko EXPOSE, bez publikacji na hoście
        private = int(port.get("PrivatePort") or 0)
        proto = port.get("Type") or "tcp"
        host_ip = port.get("IP") or "0.0.0.0"
        key = (int(public), private, proto)
        current = seen.get(key)
        if current is not None and ":" not in current["hostIp"]:
            continue  # IPv4 ma pierwszeństwo przed „::” tego samego portu
        http = likely_http(private, proto, image)
        host = "localhost" if host_ip in ("0.0.0.0", "::", "127.0.0.1", "::1", "") else (
            f"[{host_ip}]" if ":" in host_ip else host_ip)
        scheme = "https" if private in HTTPS_PORTS else "http"
        seen[key] = {"hostIp": host_ip, "hostPort": int(public), "containerPort": private, "proto": proto,
                     "likelyHttp": http, "url": f"{scheme}://{host}:{int(public)}" if http else ""}
    return sorted(seen.values(), key=lambda p: (p["hostPort"], p["containerPort"], p["proto"]))


def failure(state: str, exit_code: int, oom: bool, error: str = "") -> tuple[bool, str]:
    """(czy padł, powód po polsku). Zakończenie kodem spoza 0/143 to awaria, 137 też."""
    if state == "dead":
        return True, "martwy — Docker nie zdołał go zatrzymać ani usunąć"
    if state not in ("exited", "restarting"):
        return False, ""
    if oom:
        reason = f"zabity przez brak pamięci (OOM, kod {exit_code})"
    elif exit_code in OK_EXIT_CODES:
        return False, ""
    elif exit_code == 137:
        reason = "zabity (kod 137), możliwy brak pamięci"
    elif exit_code == 139:
        reason = "padł z naruszeniem pamięci (kod 139)"
    elif exit_code == 130:
        reason = "przerwany (kod 130)"
    elif exit_code in (126, 127):
        reason = f"nie dało się uruchomić polecenia (kod {exit_code})"
    elif exit_code < 0 or error:
        reason = f"nie wystartował: {error}" if error else f"padł (kod {exit_code})"
    else:
        reason = f"padł (kod {exit_code})"
    if state == "restarting":
        reason = f"restartuje się po błędzie — {reason}"
    return True, reason


STATE_TEXT = {
    "running": "działa",
    "exited": "zakończony",
    "created": "utworzony, nie uruchomiony",
    "paused": "wstrzymany",
    "restarting": "restartuje się",
    "removing": "usuwany",
    "dead": "martwy",
}
HEALTH_TEXT = {"healthy": "zdrowy", "unhealthy": "chory", "starting": "sprawdzanie zdrowia"}


def container(item: dict, details: dict | None) -> dict:
    details = details or {}
    labels = item.get("Labels") or {}
    state_info = details.get("State") or {}
    state = (item.get("State") or state_info.get("Status") or "").lower()
    name = container_name(item)
    project = labels.get(PROJECT_LABEL, "")
    image = item.get("Image") or (details.get("Config") or {}).get("Image") or ""
    health = ""
    health_info = state_info.get("Health") or item.get("Health") or {}
    if isinstance(health_info, dict):
        health = health_info.get("Status") or ""
    if health == "none":
        health = ""
    exit_code = int(state_info.get("ExitCode") or 0)
    if not state_info and state == "exited":
        exit_code = _exit_from_status(item.get("Status") or "")
    failed, reason = failure(state, exit_code, bool(state_info.get("OOMKilled")), state_info.get("Error") or "")
    state_text = reason if failed else STATE_TEXT.get(state, state or "nieznany")
    if state == "running" and health in HEALTH_TEXT:
        state_text = f"działa · {HEALTH_TEXT[health]}"
    tc = any(k == TESTCONTAINERS_LABEL or k.startswith(TESTCONTAINERS_LABEL + ".") for k in labels)
    return {
        "id": item.get("Id") or details.get("Id") or "",
        "shortId": (item.get("Id") or "")[:12],
        "name": name,
        "displayName": display_name(name, labels),
        "image": image,
        "service": labels.get(SERVICE_LABEL, ""),
        "project": project,
        "state": state,
        "running": state == "running",
        "health": health,
        "status": item.get("Status") or "",
        "stateText": state_text,
        "startedTs": int(parse_time(state_info.get("StartedAt"))),
        "finishedTs": int(parse_time(state_info.get("FinishedAt"))),
        "createdTs": int(item.get("Created") or parse_time(details.get("Created"))),
        "exitCode": exit_code,
        "failed": failed,
        "reason": reason,
        "oomKilled": bool(state_info.get("OOMKilled")),
        "restartCount": int(details.get("RestartCount") or 0),
        "ports": ports(item, image),
        "testcontainers": tc,
        "oneoff": labels.get(ONEOFF_LABEL, "").lower() == "true",
        "tty": bool((details.get("Config") or {}).get("Tty")),
    }


def _exit_from_status(status: str) -> int:
    """Zapasowo, bez szczegółów: „Exited (137) 22 hours ago” → 137."""
    if "(" in status and ")" in status:
        try:
            return int(status.split("(", 1)[1].split(")", 1)[0])
        except ValueError:
            return 0
    return 0


def build(items: list, details: dict, resolve_repo=None) -> dict:
    """Migawka: {summary, groups}. details: {id: inspect}; resolve_repo(working_dir) → {name, path, worktree}|None."""
    groups: dict[str, dict] = {}
    for item in items:
        labels = item.get("Labels") or {}
        entry = container(item, details.get(item.get("Id") or ""))
        project = labels.get(PROJECT_LABEL, "")
        if entry["testcontainers"]:
            key, kind = "testcontainers", "testcontainers"
        elif project:
            key, kind = f"compose:{project}", "compose"
        else:
            key, kind = "standalone", "standalone"
        group = groups.get(key)
        if group is None:
            workdir = labels.get(WORKDIR_LABEL, "") if kind == "compose" else ""
            repo = resolve_repo(workdir) if (resolve_repo and workdir) else None
            group = groups[key] = {
                "id": key, "kind": kind, "project": project if kind == "compose" else "",
                "label": project if kind == "compose" else ("testcontainers" if kind == "testcontainers"
                                                             else "bez compose"),
                "workingDir": workdir, "repo": repo, "containers": [],
            }
        elif kind == "compose" and not group["workingDir"] and labels.get(WORKDIR_LABEL):
            group["workingDir"] = labels[WORKDIR_LABEL]
            group["repo"] = resolve_repo(group["workingDir"]) if resolve_repo else None
        group["containers"].append(entry)

    for group in groups.values():
        group["containers"].sort(key=lambda c: (not c["running"], c["displayName"].lower()))
        items_ = group["containers"]
        group["running"] = sum(1 for c in items_ if c["running"])
        group["total"] = len(items_)
        group["failed"] = sum(1 for c in items_ if c["failed"])
        group["stopped"] = group["total"] - group["running"]

    order = {"compose": 0, "standalone": 1, "testcontainers": 2}
    ordered = sorted(groups.values(), key=lambda g: (order[g["kind"]], g["running"] == 0, g["label"].lower()))
    every = [c for g in ordered for c in g["containers"]]
    visible = [c for c in every if not c["testcontainers"]]
    summary = {
        "total": len(every),
        "running": sum(1 for c in visible if c["running"]),
        "stopped": sum(1 for c in visible if not c["running"]),
        "failed": sum(1 for c in visible if c["failed"]),
        "projects": sum(1 for g in ordered if g["kind"] == "compose" and g["running"]),
        "projectsTotal": sum(1 for g in ordered if g["kind"] == "compose"),
        "testcontainers": sum(1 for c in every if c["testcontainers"]),
        "testcontainersRunning": sum(1 for c in every if c["testcontainers"] and c["running"]),
    }
    return {"summary": summary, "groups": ordered}


def empty_summary() -> dict:
    return {"total": 0, "running": 0, "stopped": 0, "failed": 0, "projects": 0, "projectsTotal": 0,
            "testcontainers": 0, "testcontainersRunning": 0}


def dependency_order(containers: list[dict], depends: dict[str, str]) -> list[dict]:
    """Kontenery projektu w kolejności startu według `com.docker.compose.depends_on`
    („usługa:warunek:restart,…”); depends: {id: wartość etykiety}. Cykl albo nieznana usługa nie blokują."""
    by_service: dict[str, list[dict]] = {}
    for c in containers:
        by_service.setdefault(c.get("service") or c["id"], []).append(c)
    needs: dict[str, set[str]] = {}
    for c in containers:
        service = c.get("service") or c["id"]
        raw = depends.get(c["id"], "") or ""
        deps = {part.split(":", 1)[0].strip() for part in raw.split(",") if part.strip()}
        needs.setdefault(service, set()).update(d for d in deps if d in by_service and d != service)
    ordered: list[dict] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(service: str) -> None:
        if service in done or service in visiting:
            return
        visiting.add(service)
        for dep in sorted(needs.get(service, ())):
            visit(dep)
        visiting.discard(service)
        done.add(service)
        ordered.extend(sorted(by_service[service], key=lambda c: c["name"]))

    for service in sorted(by_service):
        visit(service)
    return ordered
