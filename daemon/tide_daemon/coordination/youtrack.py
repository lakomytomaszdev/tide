"""YouTrack: tytuły, początki opisów i stany ticketów projektu z planu (tracker: youtrack) oraz klient
synchronizacji stanu z tablicą (PT-7, board/ytsync.py).

Odczyt dla koordynacji — jedno zapytanie na projekt: `GET {url}/api/issues?query=project: {klucz}&fields=
idReadable,summary,description,customFields(name,value(name))` z tokenem stałym w nagłówku
`Authorization: Bearer`. Pamięć podręczna YouTrackCache robi wyłącznie GET.

Zapis (PT-7, zmiana decyzji „YouTrack tylko do odczytu”) robi wyłącznie synchronizacja tablicy
(board/ytsync.py) i wyłącznie przez `set_state`: jedno pole stanu jednego zgłoszenia
(`POST /api/issues/{id}` z `customFields: [{name, $type, value: {name}}]`). Przed zapisem synchronizacja
czyta zgłoszenie (`fetch_issue`), a import kart bierze zgłoszenia użytkownika (`fetch_mine`, GET).

Zapis dla przepływu karty (PT-72): `create_issue` (założenie zgłoszenia), `link_issues` (powiązanie)
i `add_comment` (komentarz). Założenie i komentarz są idempotentne — znacznik w treści (`issue_marker`,
`note_marker`) i sprawdzenie przed zapisem, więc ponowienie po zgubionej odpowiedzi nie daje duplikatu.
Błąd ma `transient` (serwer nieosiągalny, przeciążony, brak dostępu — ponów) albo `permanent` (odmowa
z walidacji, zły klucz — nie ponawiaj).

Token (decyzja użytkownika: ten sam co serwer MCP YouTracka w LogViewerze) czytamy z pliku przy
każdym zapytaniu, tylko w pamięci. Nie trafia do dziennika, komunikatów błędów, pamięci podręcznej,
odpowiedzi D-Bus ani plików. Ustawienia (opcjonalne) w `$XDG_CONFIG_HOME/tide/youtrack.json`:
{"url": "http://10.20.1.159:8111", "tokenFile": "~/.config/youtrack/token", "tokenVar": ""} — `tokenVar`
pusty: cały plik to token; podany: linia `ZMIENNA=wartość` z pliku .env. Plik ustawień jest opcjonalny:
token szukamy po kolei w `tokenFile` z ustawień, ~/.config/youtrack/token i ~/.config/logviewer/youtrack-mcp-token.
Ten sam plik niesie ustawienia synchronizacji (load_sync_settings): `sync`, `states`, `stateField`,
`projects`, `importQueries`, `importCheckMinutes`, `stableMinutes`.

Token idzie tylko do serwera z ustawień (`url`): plan w repozytorium może wskazać inny
`tracker-url`, ale wtedy nie pytamy wcale — taki plan dostaje komunikat „inny serwer niż w ustawieniach”.
Lista kluczy projektów (kontrola numerów ticketów, numbering.py): `GET {url}/api/admin/projects?fields=
shortName` z serwera z ustawień, odświeżana raz na godzinę (po błędzie — po 10 min).

Wyniki trzymamy 5 min; gdy serwer (w sieci lokalnej) nie odpowiada, tablica działa dalej na
ostatnich danych albo bez nich, z krótkim komunikatem.
"""
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://10.20.1.159:8111"
# Gdzie szukamy tokenu, gdy ustawienia go nie podają (albo podany plik nie istnieje), po kolei: token
# serwera MCP YouTracka i dawne miejsce z LogViewera.
DEFAULT_TOKEN_FILES = ("~/.config/youtrack/token", "~/.config/logviewer/youtrack-mcp-token")
DEFAULT_TOKEN_FILE = DEFAULT_TOKEN_FILES[0]
TTL = 300.0
TIMEOUT = 4.0
FIELDS = "idReadable,summary,description,customFields(name,value(name))"
DESCRIPTION_MAX = 1200     # tyle znaków opisu trzymamy (do raportu porannego idzie jeszcze mniej)
STATE_FIELDS = ("State", "Stan", "Status")
MAX_BYTES = 8 * 1024 * 1024
PROJECTS_TTL = 3600.0
PROJECTS_RETRY = 600.0
ISSUE_FIELDS = "idReadable,summary,updated,resolved,project(shortName),customFields(name,$type,value(name))"
MINE_FIELDS = "idReadable,summary,description,created,updated,resolved,project(shortName),customFields(name,value(name))"
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
DEFAULT_STABLE_MINUTES = 10   # stany rozwiązane z pracy agentów i „czeka” — zapis dopiero, gdy tyle się trzymają


def config_path(env=None) -> str:
    """Plik ustawień: $TIDE_YOUTRACK_CONFIG (testy usługi — nigdy prawdziwy serwer), inaczej
    $XDG_CONFIG_HOME/tide/youtrack.json."""
    env = os.environ if env is None else env
    if env.get("TIDE_YOUTRACK_CONFIG"):
        return env["TIDE_YOUTRACK_CONFIG"]
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "tide", "youtrack.json")


def load_config(path: str | None = None, env=None) -> dict:
    """{"url", "tokenFile", "tokenVar", "tokenFiles"} — z pliku ustawień albo wartości domyślne.

    `tokenFiles`: kolejne miejsca tokenu [[ścieżka, zmienna], …] — najpierw `tokenFile` z ustawień (jeśli jest,
    z `tokenVar`), potem DEFAULT_TOKEN_FILES (cały plik to token). `tokenFile` to pierwsze istniejące z nich
    (bez żadnego — pierwsze z listy). Plik ustawień jest opcjonalny: bez niego działa domyślny serwer
    i token z ~/.config/youtrack/token."""
    env = os.environ if env is None else env
    config = {"url": DEFAULT_URL, "tokenFile": "", "tokenVar": ""}
    try:
        with open(path or config_path(env), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for key in config:
                if isinstance(data.get(key), str):
                    config[key] = data[key]
    except (OSError, ValueError):
        pass
    config["url"] = (config["url"].strip() or DEFAULT_URL).rstrip("/")
    home = env.get("HOME") or os.path.expanduser("~")

    def expand(name: str) -> str:
        return os.path.join(home, name[2:]) if name.startswith("~/") else name

    candidates = []
    if config["tokenFile"].strip():
        candidates.append([expand(config["tokenFile"].strip()), config["tokenVar"]])
    for name in DEFAULT_TOKEN_FILES:
        if expand(name) not in [c[0] for c in candidates]:
            candidates.append([expand(name), ""])
    found = next((c for c in candidates if os.path.isfile(c[0])), candidates[0])
    config["tokenFile"], config["tokenVar"] = found
    config["tokenFiles"] = candidates
    return config


def load_sync_settings(path: str | None = None, env=None) -> dict:
    """Ustawienia synchronizacji tablicy z YouTrackiem (PT-7) z tego samego pliku co `load_config`:
    {"sync": true, "states": {klucz: nazwa}, "stateField": "State", "projects": {"LOG": {"states": {…}}},
    "importQueries": [...], "importCheckMinutes": 15}. Brak pliku albo zła wartość — domyślne."""
    env = os.environ if env is None else env
    settings = {"sync": True, "states": dict(DEFAULT_STATES), "stateField": "State", "projects": {},
                "importQueries": list(DEFAULT_IMPORT_QUERIES), "importCheckMinutes": DEFAULT_IMPORT_MINUTES,
                "stableMinutes": DEFAULT_STABLE_MINUTES}
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
    stable = data.get("stableMinutes")
    if isinstance(stable, (int, float)) and not isinstance(stable, bool):
        settings["stableMinutes"] = max(0.0, min(float(stable), 24 * 60.0))   # 0 — jak zwykłe odliczanie
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
    """Token z pierwszego miejsca, które go ma (`tokenFiles`, inaczej `tokenFile` z `tokenVar`): cały plik
    albo zmienna z pliku .env. Pusty napis, gdy nigdzie go nie ma. Ani token, ani treść pliku nie trafiają
    nigdzie poza nagłówek zapytania."""
    candidates = config.get("tokenFiles") or [[config.get("tokenFile") or "", config.get("tokenVar") or ""]]
    for path, var in candidates:
        token = _token_from(path, var)
        if token:
            return token
    return ""


def missing_token(config: dict) -> str:
    """Komunikat „brak tokenu” z listą sprawdzonych plików (same ścieżki)."""
    places = token_places(config)
    return "brak tokenu YouTracka" + (f" (sprawdzone: {places})" if places else "")


def token_places(config: dict) -> str:
    """Sprawdzone miejsca tokenu do komunikatu błędu (same ścieżki, nigdy treść)."""
    return ", ".join(path for path, _var in config.get("tokenFiles") or [[config.get("tokenFile") or "", ""]] if path)


def _token_from(path: str, var: str) -> str:
    try:
        with open(path or "", encoding="utf-8") as fh:
            text = fh.read(64 * 1024)
    except (OSError, UnicodeDecodeError):
        return ""
    var = (var or "").strip()
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
    brak tokenu albo nieznana odpowiedź). `permanent`: błąd po naszej stronie (zła wartość, sprawdzona
    przed wysłaniem) — ponawianie nic nie da."""

    def __init__(self, message: str, status: int = 0, permanent: bool = False):
        super().__init__(message)
        self.status = status
        self._permanent = permanent

    @property
    def offline(self) -> bool:
        """Serwer nieosiągalny, przeciążony albo bez dostępu (token) — ponawiamy później, to nie odmowa zmiany."""
        return not self._permanent and (self.status == 0 or self.status >= 500 or self.status in (401, 403, 408, 429))

    @property
    def transient(self) -> bool:
        """Błąd przejściowy — ponów później (to samo co `offline`)."""
        return self.offline

    @property
    def permanent(self) -> bool:
        """Odmowa (np. 400/404/422 z walidacji) albo zła wartość — ponowienie da to samo."""
        return not self.offline


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
    "description", "project", "state", "created", "updated", "resolved"}] od najświeższej zmiany."""
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


# --- zapis dla przepływu karty (PT-72) ---------------------------------------------------------------

SUMMARY_MAX = 255            # limit tytułu zgłoszenia w YouTracku
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")     # nazwa karty, ticketu, zdarzenia w znaczniku
LINK_TYPE_RE = re.compile(r"^[a-z][a-z ]{0,39}$")
# Znacznik karty w opisie to linia definicji odnośnika Markdowna `[//]: # (…)`: nie wyświetla się, a gdy człowiek
# przepisze opis, zwykle zostaje (to osobna linia). Szukamy go gdziekolwiek w opisie, tolerując odstępy.
ISSUE_MARKER_RE = re.compile(r"\[//\]:\s*#\s*\(\s*tide-ref\s+card=([A-Za-z0-9][A-Za-z0-9._-]*)\s+n=(\d{1,9})\s*\)")
NOTE_MARKER_RE = re.compile(r"\[//\]:\s*#\s*\(\s*tide-note\s+card=([A-Za-z0-9][A-Za-z0-9._-]*)\s+"
                            r"ticket=([A-Za-z0-9][A-Za-z0-9._-]*)\s+event=([A-Za-z0-9][A-Za-z0-9._-]*)\s*\)")


def _name(value, what: str) -> str:
    value = str(value)
    if not NAME_RE.match(value):
        raise YouTrackError(f"niepoprawna nazwa ({what}) w znaczniku", permanent=True)
    return value


def _number(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 10**9:
        raise YouTrackError("niepoprawny numer roboczy w znaczniku", permanent=True)
    return value


def issue_marker(card: str, number: int) -> str:
    """Znacznik zgłoszenia zakładanego dla numeru roboczego `number` karty `card`:
    `[//]: # (tide-ref card=<karta> n=<numer>)`."""
    return f"[//]: # (tide-ref card={_name(card, 'karta')} n={_number(number)})"


def marker_of(description: str):
    """(karta, numer) z pierwszego znacznika w opisie albo None."""
    found = ISSUE_MARKER_RE.search(description or "")
    return (found.group(1), int(found.group(2))) if found else None


def note_marker(card: str, ticket: str, event: str) -> str:
    """Znacznik komentarza: `[//]: # (tide-note card=<karta> ticket=<ticket> event=<zdarzenie>)`."""
    return f"[//]: # (tide-note card={_name(card, 'karta')} ticket={_name(ticket, 'ticket')} event={_name(event, 'zdarzenie')})"


def find_issue(url: str, project: str, card: str, number: int, token: str, timeout: float = TIMEOUT,
               opener=None) -> dict | None:
    """Zgłoszenie projektu ze znacznikiem (karta, numer) w opisie albo None. GET; wynik wyszukiwania pełnotekstowego
    sprawdzamy jeszcze po stronie klienta, więc zgłoszenie z podobnym tekstem (inny numer, karta o dłuższej nazwie)
    nie pasuje."""
    if not PROJECT_RE.match(project or ""):
        raise YouTrackError("niepoprawny klucz projektu", permanent=True)
    phrase = f"tide-ref card={_name(card, 'karta')} n={_number(number)}"
    query = urllib.parse.urlencode({"query": f'project: {project} "{phrase}"',
                                    "fields": "idReadable,summary,description", "$top": "50"})
    for item in _get_list(f"{url}/api/issues?{query}", token, timeout, opener):
        if isinstance(item, dict) and issue_key(str(item.get("idReadable") or "")):
            if any((m.group(1), int(m.group(2))) == (card, number)
                   for m in ISSUE_MARKER_RE.finditer(str(item.get("description") or ""))):
                ident = issue_key(str(item["idReadable"]))
                return {"id": ident, "summary": str(item.get("summary") or "")[:500], "url": f"{url}/issue/{ident}"}
    return None


def create_issue(url: str, project: str, summary: str, description: str, card: str, number: int, token: str,
                 timeout: float = TIMEOUT, opener=None) -> dict:
    """Zgłoszenie dla numeru roboczego `number` karty `card` w projekcie `project`: {"id", "summary", "url",
    "created"}. Idempotentne: opis dostaje znacznik `issue_marker`, a przed założeniem szukamy zgłoszenia z tym
    znacznikiem — jest: zwracamy je (`created: False`), nie ma: zakładamy (POST /api/issues). Ponowienie po
    zgubionej odpowiedzi znajduje więc to, co serwer już założył."""
    summary = " ".join(str(summary or "").split())
    if not summary:
        raise YouTrackError("brak tytułu zgłoszenia", permanent=True)
    marker = issue_marker(card, number)
    if not PROJECT_RE.match(project or ""):
        raise YouTrackError("niepoprawny klucz projektu", permanent=True)
    existing = find_issue(url, project, card, number, token, timeout, opener)
    if existing:
        return {**existing, "created": False}
    body = {"project": {"shortName": project}, "summary": summary[:SUMMARY_MAX],
            "description": (str(description or "").rstrip() + "\n\n" + marker).lstrip()}
    data = _request("POST", f"{url}/api/issues?" + urllib.parse.urlencode({"fields": "idReadable,summary"}),
                    token, timeout, opener, body)
    ident = issue_key(str(data.get("idReadable") or "")) if isinstance(data, dict) else ""
    if not ident:
        raise YouTrackError("nieznana odpowiedź YouTracka")
    return {"id": ident, "summary": str(data.get("summary") or summary)[:500], "url": f"{url}/issue/{ident}",
            "created": True}


def link_issues(url: str, ident: str, target: str, token: str, link_type: str = "relates to",
                timeout: float = TIMEOUT, opener=None) -> dict:
    """Powiązanie zgłoszenia `ident` ze zgłoszeniem `target` poleceniem YouTracka (`POST /api/commands`, np.
    „relates to DEMO-1”, „subtask of DEMO-1”). Powtórzenie niczego nie zmienia (YouTrack nie dubluje powiązań)."""
    ident, target = _checked(ident, True), _checked(target, True)
    link_type = " ".join(str(link_type or "").lower().split())
    if not LINK_TYPE_RE.match(link_type):
        raise YouTrackError("niepoprawny rodzaj powiązania", permanent=True)
    _request("POST", f"{url}/api/commands", token, timeout, opener,
             {"query": f"{link_type} {target}", "issues": [{"idReadable": ident}]})
    return {"id": ident, "target": target, "linkType": link_type}


def add_comment(url: str, ident: str, text: str, card: str, ticket: str, event: str, token: str,
                timeout: float = TIMEOUT, opener=None) -> dict:
    """Komentarz do zgłoszenia `ident` z notką wyniku ticketu `ticket` karty `card` (zdarzenie `event`, np.
    „merged”): {"commentId", "created"}. Idempotentny: komentarz niesie `note_marker`, a przed dodaniem czytamy
    komentarze zgłoszenia (GET) — jest taki ze znacznikiem: zwracamy go (`created: False`)."""
    ident = _checked(ident, True)
    text = str(text or "").strip()
    if not text:
        raise YouTrackError("pusty komentarz", permanent=True)
    marker = note_marker(card, ticket, event)
    path = f"{url}/api/issues/{urllib.parse.quote(ident)}/comments"
    comments = _get_list(path + "?" + urllib.parse.urlencode({"fields": "id,text", "$top": "500"}),
                         token, timeout, opener)
    for item in comments:
        if isinstance(item, dict) and marker in str(item.get("text") or ""):
            return {"commentId": str(item.get("id") or ""), "created": False}
    data = _request("POST", path + "?" + urllib.parse.urlencode({"fields": "id"}), token, timeout, opener,
                    {"text": f"{text}\n\n{marker}"})
    return {"commentId": str(data.get("id") or "") if isinstance(data, dict) else "", "created": True}


def _checked(ident: str, permanent: bool = False) -> str:
    key = issue_key(ident)
    if not key:
        raise YouTrackError("niepoprawny klucz zgłoszenia", permanent=permanent)
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
            "updated": _millis(data.get("updated")), "resolved": _millis(data.get("resolved")),
            "created": _millis(data.get("created"))}


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
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
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
                token = read_token(config)
                if not token:
                    raise YouTrackError(missing_token(config))
                issues = self.fetch(url, project, token)
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
                token = read_token(config)
                if not token:
                    raise YouTrackError(missing_token(config))
                keys = self.fetch_projects(url, token)
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
