"""YouTrack — tylko odczyt: tytuły, początki opisów i stany ticketów projektu z planu (tracker: youtrack).

Jedno zapytanie na projekt: `GET {url}/api/issues?query=project: {klucz}&fields=idReadable,summary,
description,customFields(name,value(name))` z tokenem stałym w nagłówku `Authorization: Bearer`. Nigdy nie
wysyłamy niczego innego niż GET.

Token (decyzja użytkownika: ten sam co serwer MCP YouTracka w LogViewerze) czytamy z pliku przy
każdym zapytaniu, tylko w pamięci. Nie trafia do dziennika, komunikatów błędów, pamięci podręcznej,
odpowiedzi D-Bus ani plików. Ustawienia (opcjonalne) w `$XDG_CONFIG_HOME/tide/youtrack.json`:
{"url": "http://10.20.1.159:8111", "tokenFile": "~/.config/logviewer/youtrack-mcp-token",
 "tokenVar": ""} — `tokenVar` pusty: cały plik to token; podany: linia `ZMIENNA=wartość` z pliku .env.

Token idzie tylko do serwera z ustawień (`url`): plan w repozytorium może wskazać inny
`tracker-url`, ale wtedy nie pytamy wcale — taki plan dostaje komunikat „inny serwer niż w ustawieniach”.
Lista kluczy projektów (kontrola numerów ticketów, numbering.py): `GET {url}/api/admin/projects?fields=
shortName` z serwera z ustawień, odświeżana raz na godzinę (po błędzie — po 10 min).

Wyniki trzymamy 5 min; gdy serwer (w sieci lokalnej) nie odpowiada, tablica działa dalej na
ostatnich danych albo bez nich, z krótkim komunikatem.
"""
import json
import os
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


def config_path(env=None) -> str:
    env = os.environ if env is None else env
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
    """Błąd zapytania — komunikat po polsku, nigdy z tokenem."""


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


def _get_list(address: str, token: str, timeout: float, opener) -> list:
    """GET z tokenem w nagłówku; lista z JSON albo YouTrackError (komunikat nigdy z tokenem)."""
    if not token:
        raise YouTrackError("brak tokenu YouTracka")
    request = urllib.request.Request(address, method="GET", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json", "Cache-Control": "no-cache"})
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read(MAX_BYTES).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise YouTrackError(f"YouTrack odpowiedział {exc.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise YouTrackError("YouTrack nie odpowiada") from None
    except (ValueError, UnicodeDecodeError):
        raise YouTrackError("nieznana odpowiedź YouTracka") from None
    if not isinstance(data, list):
        raise YouTrackError("nieznana odpowiedź YouTracka")
    return data


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
