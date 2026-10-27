"""YouTrack: tytuły, początki opisów i stany ticketów projektu z planu (tracker: youtrack) oraz klient
synchronizacji stanu z tablicą (PT-7, board/ytsync.py).

Odczyt dla koordynacji — jedno zapytanie na projekt: `GET {url}/api/issues?query=project: {klucz}&fields=
idReadable,summary,description,customFields(name,value(name))` z tokenem stałym w nagłówku
`Authorization: Bearer`. Pamięć podręczna YouTrackCache robi wyłącznie GET.

Zapis (PT-7, zmiana decyzji „YouTrack tylko do odczytu”) robi wyłącznie synchronizacja tablicy
(board/ytsync.py) i wyłącznie przez `set_state`: jedno pole stanu jednego zgłoszenia
(`POST /api/issues/{id}` z `customFields: [{name, $type, value: {name}}]`). Przed zapisem synchronizacja
czyta zgłoszenie (`fetch_issue`), a import kart bierze zgłoszenia użytkownika (`fetch_mine`, GET).

Token (decyzja użytkownika: ten sam co serwer MCP YouTracka w LogViewerze) czytamy z pliku przy
każdym zapytaniu, tylko w pamięci. Nie trafia do dziennika, komunikatów błędów, pamięci podręcznej,
odpowiedzi D-Bus ani plików. Ustawienia (opcjonalne) w `$XDG_CONFIG_HOME/tide/youtrack.json`:
{"url": "http://10.20.1.159:8111", "tokenFile": "~/.config/logviewer/youtrack-mcp-token",
 "tokenVar": ""} — `tokenVar` pusty: cały plik to token; podany: linia `ZMIENNA=wartość` z pliku .env.
Ten sam plik niesie ustawienia synchronizacji (load_sync_settings): `sync`, `states`, `stateField`,
`projects`, `importQueries`, `importCheckMinutes`.

Token idzie tylko do serwera z ustawień (`url`): plan w repozytorium może wskazać inny
`tracker-url`, ale wtedy nie pytamy wcale — taki plan dostaje komunikat „inny serwer niż w ustawieniach”.
Lista kluczy projektów (kontrola numerów ticketów, numbering.py): `GET {url}/api/admin/projects?fields=
shortName` z serwera z ustawień, odświeżana raz na godzinę (po błędzie — po 10 min).

Wyniki trzymamy 5 min; gdy serwer (w sieci lokalnej) nie odpowiada, tablica działa dalej na
ostatnich danych albo bez nich, z krótkim komunikatem.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://10.20.1.159:8111"
DEFAULT_TOKEN_FILE = "~/.config/logviewer/youtrack-mcp-token"
TTL = 300.0
TIMEOUT = 4.0
FIELDS = "idReadable,summary,description,customFields(name,value(name))"
DESCRIPTION_MAX = 1200     # tyle znaków opisu trzymamy (do raportu porannego idzie jeszcze mniej)
STATE_FIELDS = ("State", "Stan", "Status")
MAX_BYTES = 8 * 1024 * 1024
PROJECTS_TTL = 3600.0
PROJECTS_RETRY = 600.0
ISSUE_FIELDS = "idReadable,summary,updated,resolved,project(shortName),customFields(name,$type,value(name))"
MINE_FIELDS = "idReadable,summary,description,updated,resolved,project(shortName),customFields(name,value(name))"
MINE_DESCRIPTION_MAX = 16 * 1024   # opis importowanej karty (plik karty przyjmuje do 64 KB)
ISSUE_RE = re.compile(r"^([A-Z][A-Z0-9_]{0,19})-([1-9][0-9]{0,8})$")
PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,19}$")
# Klucze mapy stanów (stany tablicy i ticketów → nazwy stanów w YouTracku). Domyślne nazwy to
# domyślny workflow YouTracka (projekty LOG i LEGAL); `states` w ustawieniach je zmienia.
STATE_KEYS = ("open", "reopened", "progress", "waiting", "review", "done", "rejected")
DEFAULT_STATES = {"open": "Open", "reopened": "Reopened", "progress": "In Progress", "waiting": "To be discussed",
                  "review": "Fixed", "done": "Verified", "rejected": "Won't fix"}
DEFAULT_IMPORT_QUERIES = ("for: me #Unresolved", "reporter: me Assignee: Unassigned #Unresolved")
DEFAULT_IMPORT_MINUTES = 15


def config_path(env=None) -> str:
    """Plik ustawień: $TIDE_YOUTRACK_CONFIG (testy usługi — nigdy prawdziwy serwer), inaczej
    $XDG_CONFIG_HOME/tide/youtrack.json."""
    env = os.environ if env is None else env
    if env.get("TIDE_YOUTRACK_CONFIG"):
        return env["TIDE_YOUTRACK_CONFIG"]
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "tide", "youtrack.json")


def load_config(path: str | None = None, env=None) -> dict:
    """{"url", "tokenFile", "tokenVar"} — z pliku ustawień albo wartości domyślne."""
    env = os.environ if env is None else env
    config = {"url": DEFAULT_URL, "tokenFile": DEFAULT_TOKEN_FILE, "tokenVar": ""}
    try:
        with open(path or config_path(env), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for key in config:
                if isinstance(data.get(key), str):
                    config[key] = data[key]
    except (OSError, ValueError):
        pass
    config["url"] = config["url"].strip().rstrip("/")
    home = env.get("HOME") or os.path.expanduser("~")
    token_file = config["tokenFile"]
    if token_file.startswith("~/"):
        token_file = os.path.join(home, token_file[2:])
    config["tokenFile"] = token_file
    return config


def load_sync_settings(path: str | None = None, env=None) -> dict:
    """Ustawienia synchronizacji tablicy z YouTrackiem (PT-7) z tego samego pliku co `load_config`:
    {"sync": true, "states": {klucz: nazwa}, "stateField": "State", "projects": {"LOG": {"states": {…}}},
    "importQueries": [...], "importCheckMinutes": 15}. Brak pliku albo zła wartość — domyślne."""
    env = os.environ if env is None else env
    settings = {"sync": True, "states": dict(DEFAULT_STATES), "stateField": "State", "projects": {},
                "importQueries": list(DEFAULT_IMPORT_QUERIES), "importCheckMinutes": DEFAULT_IMPORT_MINUTES}
    try:
        with open(path or config_path(env), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        return settings
    if isinstance(data.get("sync"), bool):
        settings["sync"] = data["sync"]
    settings["states"] = _states(data.get("states"), settings["states"])
    if isinstance(data.get("stateField"), str) and data["stateField"].strip():
        settings["stateField"] = data["stateField"].strip()
    projects = data.get("projects")
    if isinstance(projects, dict):
        for key, value in projects.items():
            key = str(key).strip().upper()
            if PROJECT_RE.match(key) and isinstance(value, dict):
                settings["projects"][key] = {"states": _states(value.get("states"), settings["states"])}
    queries = data.get("importQueries")
    if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
        settings["importQueries"] = [q.strip() for q in queries if q.strip()][:5]
    minutes = data.get("importCheckMinutes")
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool):
        settings["importCheckMinutes"] = max(0, min(int(minutes), 24 * 60))   # 0 — bez sprawdzania w tle
    return settings


def states_for(settings: dict, project: str) -> dict:
    """Mapa stanów dla projektu YouTracka (nadpisanie z `projects`, inaczej wspólna)."""
    return (settings.get("projects") or {}).get(project, {}).get("states") or settings.get("states") or DEFAULT_STATES


def _states(value, base: dict) -> dict:
    out = dict(base)
    if isinstance(value, dict):
        for key in STATE_KEYS:
            name = value.get(key)
            if isinstance(name, str) and name.strip() and len(name) <= 80:
                out[key] = " ".join(name.split())
    return out


def issue_key(text: str) -> str:
    """Klucz zgłoszenia („legal-35”, „LEGAL-35”, adres …/issue/LEGAL-35) → „LEGAL-35”; pusty, gdy to nie klucz."""
    text = (text or "").strip()
    if "/issue/" in text:
        text = text.rsplit("/issue/", 1)[1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    text = text.upper()
    return text if ISSUE_RE.match(text) else ""


def read_token(config: dict) -> str:
    """Token z pliku (cały plik albo zmienna tokenVar z pliku .env). Pusty napis, gdy go nie ma."""
    try:
        with open(config.get("tokenFile") or "", encoding="utf-8") as fh:
            text = fh.read(64 * 1024)
    except (OSError, UnicodeDecodeError):
        return ""
    var = (config.get("tokenVar") or "").strip()
    if not var:
        return text.strip()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if sep and key.strip() == var:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ""


class YouTrackError(Exception):
    """Błąd zapytania — komunikat po polsku, nigdy z tokenem. `status`: kod HTTP (0 — brak odpowiedzi,
    brak tokenu albo nieznana odpowiedź)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status

    @property
    def offline(self) -> bool:
        """Serwer nieosiągalny, przeciążony albo bez dostępu (token) — ponawiamy później, to nie odmowa zmiany."""
        return self.status == 0 or self.status >= 500 or self.status in (401, 403, 408, 429)


def same_server(a: str, b: str) -> bool:
    pa, pb = urllib.parse.urlsplit(a or ""), urllib.parse.urlsplit(b or "")
    return bool(pa.netloc) and (pa.scheme, pa.netloc.lower()) == (pb.scheme, pb.netloc.lower())


def fetch_issues(url: str, project: str, token: str, timeout: float = TIMEOUT, opener=None) -> dict:
    """{klucz: {"id", "summary", "description", "state", "url"}} dla projektu. Tylko GET."""
    query = urllib.parse.urlencode({"query": f"project: {project}", "fields": FIELDS, "$top": "2000"})
    data = _get_list(f"{url}/api/issues?{query}", token, timeout, opener)
    out = {}
    for item in data:
        if not isinstance(item, dict) or not item.get("idReadable"):
            continue
        ident = str(item["idReadable"])
        state = ""
        for field in item.get("customFields") or []:
            if isinstance(field, dict) and field.get("name") in STATE_FIELDS:
                value = field.get("value")
                if isinstance(value, dict):
                    state = str(value.get("name") or "")
        key = _key(ident)
        if key:
            description = item.get("description")
            out[key] = {"id": ident, "summary": str(item.get("summary") or ""), "state": state,
                        "url": f"{url}/issue/{ident}",
                        "description": description[:DESCRIPTION_MAX] if isinstance(description, str) else ""}
    return out


def fetch_projects(url: str, token: str, timeout: float = TIMEOUT, opener=None) -> set[str]:
    """Klucze wszystkich projektów (shortName), np. {"LOG", "LEGAL", "TIDE"}. Tylko GET."""
    query = urllib.parse.urlencode({"fields": "shortName", "$top": "1000"})
    data = _get_list(f"{url}/api/admin/projects?{query}", token, timeout, opener)
    return {str(item["shortName"]).upper() for item in data if isinstance(item, dict) and item.get("shortName")}


def fetch_issue(url: str, ident: str, token: str, state_field: str = "State", timeout: float = TIMEOUT,
                opener=None) -> dict:
    """Jedno zgłoszenie (GET): {"id", "summary", "project", "state", "stateField", "stateType", "updated",
    "resolved"}. `updated` i `resolved` w milisekundach (resolved 0 — nierozwiązane)."""
    ident = _checked(ident)
    query = urllib.parse.urlencode({"fields": ISSUE_FIELDS})
    data = _request("GET", f"{url}/api/issues/{urllib.parse.quote(ident)}?{query}", token, timeout, opener)
    return _issue(data, state_field, ident)


def set_state(url: str, ident: str, state: str, token: str, state_field: str = "State",
              field_type: str = "StateIssueCustomField", timeout: float = TIMEOUT, opener=None) -> dict:
    """Zmiana stanu jednego zgłoszenia — jedyny zapis do YouTracka (POST z jednym polem). Wynik jak fetch_issue."""
    ident = _checked(ident)
    if not state:
        raise YouTrackError("brak nazwy stanu")
    body = {"customFields": [{"name": state_field, "$type": field_type or "StateIssueCustomField",
                              "value": {"name": state}}]}
    query = urllib.parse.urlencode({"fields": ISSUE_FIELDS})
    data = _request("POST", f"{url}/api/issues/{urllib.parse.quote(ident)}?{query}", token, timeout, opener, body)
    return _issue(data, state_field, ident)


def fetch_mine(url: str, token: str, queries=DEFAULT_IMPORT_QUERIES, timeout: float = TIMEOUT, opener=None) -> list:
    """Zgłoszenia użytkownika do importu (GET, kolejne zapytania sklejone bez powtórzeń): [{"id", "summary",
    "description", "project", "state", "updated", "resolved"}] od najświeższej zmiany."""
    out: dict[str, dict] = {}
    for text in queries or ():
        query = urllib.parse.urlencode({"query": text, "fields": MINE_FIELDS, "$top": "500"})
        for item in _get_list(f"{url}/api/issues?{query}", token, timeout, opener):
            if not isinstance(item, dict) or not issue_key(str(item.get("idReadable") or "")):
                continue
            issue = _issue(item, "State", issue_key(str(item["idReadable"])))
            if issue["resolved"]:
                continue
            description = item.get("description")
            issue["description"] = description[:MINE_DESCRIPTION_MAX] if isinstance(description, str) else ""
            out.setdefault(issue["id"], issue)
    return sorted(out.values(), key=lambda i: (-i["updated"], i["id"]))


def _checked(ident: str) -> str:
    key = issue_key(ident)
    if not key:
        raise YouTrackError("niepoprawny klucz zgłoszenia")
    return key


def _issue(data, state_field: str, ident: str) -> dict:
    if not isinstance(data, dict):
        raise YouTrackError("nieznana odpowiedź YouTracka")
    state = field_type = ""
    field_name = state_field
    fields = [f for f in data.get("customFields") or [] if isinstance(f, dict)]
    # Najpierw pole z ustawień, potem znane nazwy pola stanu.
    for wanted in (state_field, *STATE_FIELDS):
        found = next((f for f in fields if f.get("name") == wanted), None)
        if found is not None:
            field_name = wanted
            field_type = str(found.get("$type") or "")
            value = found.get("value")
            state = str(value.get("name") or "") if isinstance(value, dict) else ""
            break
    project = data.get("project")
    return {"id": issue_key(str(data.get("idReadable") or "")) or ident,
            "summary": str(data.get("summary") or "")[:500],
            "project": str(project.get("shortName") or "").upper() if isinstance(project, dict) else "",
            "state": state, "stateField": field_name, "stateType": field_type,
            "updated": _millis(data.get("updated")), "resolved": _millis(data.get("resolved"))}


def _millis(value) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def _get_list(address: str, token: str, timeout: float, opener) -> list:
    """GET z tokenem w nagłówku; lista z JSON albo YouTrackError (komunikat nigdy z tokenem)."""
    data = _request("GET", address, token, timeout, opener)
    if not isinstance(data, list):
        raise YouTrackError("nieznana odpowiedź YouTracka")
    return data


def _request(method: str, address: str, token: str, timeout: float, opener, body=None):
    """Zapytanie z tokenem w nagłówku (bez pośrednika); JSON albo YouTrackError (komunikat nigdy z tokenem)."""
    if not token:
        raise YouTrackError("brak tokenu YouTracka")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Cache-Control": "no-cache"}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(address, data=payload, method=method, headers=headers)
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read(MAX_BYTES).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise YouTrackError(f"YouTrack odpowiedział {exc.code}{_hint(exc)}", exc.code) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise YouTrackError("YouTrack nie odpowiada") from None
    except (ValueError, UnicodeDecodeError):
        raise YouTrackError("nieznana odpowiedź YouTracka") from None


def _hint(exc) -> str:
    """Krótki opis błędu z odpowiedzi YouTracka (`error_description`, np. stan spoza workflow) — bez nagłówków."""
    try:
        data = json.loads(exc.read(8192).decode("utf-8"))
    except Exception:
        return ""
    text = data.get("error_description") or data.get("error") if isinstance(data, dict) else ""
    text = " ".join(str(text or "").split())[:160]
    return f": {text}" if text else ""


class YouTrackCache:
    """Wyniki na (serwer, projekt) przez TTL; przy błędzie — ostatnie dobre dane i komunikat.

    Także po błędzie następna próba dopiero po TTL, żeby nieosiągalny serwer w sieci lokalnej
    nie spowalniał każdego odświeżenia tablicy."""

    def __init__(self, config_loader=load_config, fetch=fetch_issues, clock=time.time, ttl: float = TTL,
                 fetch_projects=fetch_projects):
        self.config_loader = config_loader
        self.fetch = fetch
        self.fetch_projects = fetch_projects
        self._projects: dict = {}           # serwer → {"keys", "checkedTs", "error"}
        self.clock = clock
        self.ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[tuple, dict] = {}

    def get(self, url: str, project: str, force: bool = False) -> tuple[dict | None, dict]:
        """(zgłoszenia albo None, stan {kind, url, project, available, error, fetchedTs, checkedTs})."""
        config = self.config_loader()
        url = (url or config["url"]).rstrip("/")
        info = {"kind": "youtrack", "url": url, "project": project, "available": False, "error": "",
                "fetchedTs": 0, "checkedTs": 0}
        if not url or not project:
            info["error"] = "plan bez tracker-url albo tracker-project"
            return None, info
        if not same_server(url, config["url"]):
            info["error"] = "inny serwer niż w ustawieniach YouTracka — tokenu tam nie wysyłamy"
            return None, info
        key = (url, project)
        now = self.clock()
        with self._lock:
            entry = self._entries.get(key)
            fresh = entry is not None and now - entry["checkedTs"] < self.ttl
        if not fresh or force:
            issues, error = None, ""
            try:
                issues = self.fetch(url, project, read_token(config))
            except YouTrackError as exc:
                error = str(exc)
            except Exception:  # klient nie może położyć tablicy (i nie może zdradzić tokenu w komunikacie)
                error = "błąd zapytania do YouTracka"
            with self._lock:
                entry = self._entries.get(key) or {"issues": None, "fetchedTs": 0}
                entry["checkedTs"] = now
                entry["error"] = error
                if issues is not None:
                    entry["issues"], entry["fetchedTs"] = issues, now
                self._entries[key] = entry
        info.update(available=not entry["error"], error=entry["error"], fetchedTs=round(entry["fetchedTs"], 3),
                    checkedTs=round(entry["checkedTs"], 3))
        return entry["issues"], info

    def projects(self, force: bool = False) -> tuple[set | None, str]:
        """(klucze projektów albo None, komunikat błędu) z serwera z ustawień — raz na godzinę,
        po błędzie najwcześniej po PROJECTS_RETRY; przy błędzie ostatnia dobra lista."""
        config = self.config_loader()
        url = config["url"]
        if not url:
            return None, "brak adresu YouTracka w ustawieniach"
        now = self.clock()
        with self._lock:
            entry = self._projects.get(url)
            wait = PROJECTS_RETRY if entry and entry["error"] else PROJECTS_TTL
            fresh = entry is not None and now - entry["checkedTs"] < wait
        if not fresh or force:
            keys, error = None, ""
            try:
                keys = self.fetch_projects(url, read_token(config))
            except YouTrackError as exc:
                error = str(exc)
            except Exception:  # klient nie może położyć tablicy (i nie może zdradzić tokenu w komunikacie)
                error = "błąd zapytania do YouTracka"
            with self._lock:
                entry = self._projects.get(url) or {"keys": None}
                entry["checkedTs"], entry["error"] = now, error
                if keys is not None:
                    entry["keys"] = set(keys)
                self._projects[url] = entry
        return (set(entry["keys"]) if entry["keys"] is not None else None), entry["error"]

    def invalidate(self) -> None:
        with self._lock:
            for entry in self._entries.values():
                entry["checkedTs"] = 0


def _key(ident: str) -> str:
    prefix, sep, number = ident.rpartition("-")
    if not sep or not number.isdigit():
        return ""
    return f"{prefix.upper()}-{int(number)}"
