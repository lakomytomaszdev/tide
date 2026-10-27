"""Kiedy powstaje raport poranny, jaki okres obejmuje i jak długo go trzymamy — bez Qt i bez I/O
poza plikami stanu i katalogiem raportów.

Doba raportu zaczyna się o `dayStart` (domyślnie 04:00), a nie o północy: praca po północy należy
jeszcze do poprzedniego dnia, więc logowanie o 01:00 nie zabiera porannego raportu. Raport doby jest
„należny”, dopóki w stanie nie ma raportu tej doby, a powstaje:

- przy pierwszym logowaniu w tej dobie (start usługi), o dowolnej porze po `dayStart`,
- o `at` (domyślnie 07:00), jeśli użytkownik jest zalogowany — sprawdzane co minutę według zegara
  ściennego, więc wybudzenie po uśpieniu też go złapie.

Okres: od końca poprzedniego raportu do teraz, najwyżej MAX_PERIOD (7 dni); pierwszy raport —
ostatnia doba. Ponowne wygenerowanie w tej samej dobie (Generate() z D-Bus) zachowuje początek
okresu i tylko przesuwa koniec, więc nie gubi niczego z porannego raportu.

Przeczytanie: raport doby jest „nieprzeczytany”, dopóki w stanie nie ma tej doby w `opened`
(otwarcie z widżetu, powiadomienia albo KRunnera). Nowa doba to nowy, nieprzeczytany raport;
ponowne wygenerowanie tej samej doby nie wraca do „nieprzeczytany”.
"""
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

DEFAULT_AT = "07:00"
DEFAULT_DAY_START = "04:00"
KEEP_DAYS = 30
MAX_PERIOD = 7 * 86400
FIRST_PERIOD = 86400
LOGIN_DELAY = 120          # s po starcie usługi — logowanie nie czeka na gita i dzienniki
CHECK_INTERVAL = 60        # s między sprawdzeniami zegara

DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUMMARY_MODES = ("claude", "local", "rules")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,63}$")


@dataclass
class Settings:
    at: int = 7 * 60          # minuty od północy
    day_start: int = 4 * 60
    keep_days: int = KEEP_DAYS
    enabled: bool = True
    summary: str = "claude"   # „Na daily” i opisy kart: "claude" (`claude -p`, decyzja 52), "local" (models.json,
    #                           rola summary/describe, bez Claude — PT-15) albo "rules"
    summary_model: str = "sonnet"  # decyzja 53 (wcześniej haiku — za płytkie opisy)


@dataclass
class State:
    """Plik $XDG_STATE_HOME/tide/report-state.json."""
    day: str = ""             # doba ostatniego raportu (RRRR-MM-DD)
    start: float = 0.0        # początek okresu ostatniego raportu
    end: float = 0.0          # koniec okresu ostatniego raportu (chwila wygenerowania)
    notified: str = ""        # doba, dla której poszło już powiadomienie
    opened: str = ""          # doba raportu, który użytkownik już otworzył
    projects: int | None = None   # projekty z ruchem w ostatnim raporcie (None — nieznane)
    waiting: int | None = None    # „czeka na ciebie” w ostatnim raporcie (None — nieznane)


def parse_hhmm(text) -> int:
    """„07:00” → 420. ValueError dla czegokolwiek innego."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text or ""))
    if not match:
        raise ValueError(f"niepoprawna godzina: {text!r} (oczekiwano GG:MM)")
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        raise ValueError(f"niepoprawna godzina: {text!r}")
    return hours * 60 + minutes


def config_path(env=None) -> str:
    env = os.environ if env is None else env
    config = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(config, "tide", "report.json")


def state_path(env=None) -> str:
    env = os.environ if env is None else env
    state = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local/state")
    return os.path.join(state, "tide", "report-state.json")


def reports_root(env=None) -> str:
    env = os.environ if env is None else env
    data = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local/share")
    return os.path.join(data, "tide", "reports")


def load_settings(path: str | None = None, at: str | None = None, enabled: bool | None = None) -> tuple[Settings, list[str]]:
    """Ustawienia z pliku (opcjonalnego) i opcji wiersza poleceń. Zwraca (ustawienia, ostrzeżenia).

    Plik JSON: {"at": "07:00", "dayStart": "04:00", "keepDays": 30, "enabled": true, "summary": "claude",
    "summaryModel": "sonnet"}. `summary`: "claude", "local" albo "rules"; `summaryModel` liczy się tylko
    w trybie "claude" — w "local" model bierzemy z ról `summary` i `describe` w models.json. Błędna wartość
    nie zatrzymuje usługi — zostaje domyślna, a ostrzeżenie idzie do dziennika; wyjątek: nieznany `summary`
    (np. literówka „Local”) to "rules", żeby pomyłka nie wysyłała faktów do Anthropic. Bez klucza — "claude"."""
    settings, warnings = Settings(), []
    data = {}
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("oczekiwano obiektu JSON")
        except (OSError, ValueError) as exc:
            warnings.append(f"raport: pomijam {path}: {exc}")
            data = {}
    for key, attr in (("at", "at"), ("dayStart", "day_start")):
        if key in data:
            try:
                setattr(settings, attr, parse_hhmm(data[key]))
            except ValueError as exc:
                warnings.append(f"raport: {exc}")
    if "keepDays" in data:
        value = data["keepDays"]
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            settings.keep_days = value
        else:
            warnings.append(f"raport: keepDays musi być liczbą dni ≥ 1, jest {value!r}")
    if isinstance(data.get("enabled"), bool):
        settings.enabled = data["enabled"]
    if "summary" in data:
        if data["summary"] in SUMMARY_MODES:
            settings.summary = data["summary"]
        else:  # literówka („Local”) nie może wysłać faktów do Anthropic — bez modelu, z ostrzeżeniem
            settings.summary = "rules"
            warnings.append(f"raport: summary musi być jednym z {', '.join(SUMMARY_MODES)}, jest {data['summary']!r} "
                            f"— opis z reguł")
    if "summaryModel" in data:
        value = data["summaryModel"]
        if isinstance(value, str) and MODEL_RE.match(value):
            settings.summary_model = value
        else:
            warnings.append(f"raport: summaryModel to nazwa modelu (np. sonnet), jest {value!r}")
    if at:
        settings.at = parse_hhmm(at)  # opcja wiersza poleceń: błąd ma być głośny
    if enabled is not None:
        settings.enabled = enabled
    if settings.day_start > settings.at:
        # Doba nie może zaczynać się po godzinie raportu — inaczej raport „o 07:00” należałby do wczoraj.
        warnings.append("raport: dayStart po godzinie raportu — doba zaczyna się o godzinie raportu")
        settings.day_start = settings.at
    return settings, warnings


# --- doba i termin ------------------------------------------------------------------------

def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def report_day(now: float, settings: Settings) -> date:
    """Doba raportu: data lokalna przesunięta o dayStart (01:00 należy jeszcze do wczoraj)."""
    return (local(now) - timedelta(minutes=settings.day_start)).date()


def due(now: float, state: State, settings: Settings, at_login: bool) -> bool:
    """Czy teraz trzeba wygenerować raport doby.

    at_login=True: pierwsze sprawdzenie po starcie usługi (logowanie) — wystarczy, że doba nie ma
    jeszcze raportu. at_login=False: sprawdzenie zegarem — dodatkowo musi minąć godzina `at`."""
    if not settings.enabled:
        return False
    day = report_day(now, settings).isoformat()
    if state.day == day:
        return False
    if at_login:
        return True
    since_start = local(now) - timedelta(minutes=settings.day_start)
    minutes = since_start.hour * 60 + since_start.minute
    return minutes >= settings.at - settings.day_start


def period(now: float, state: State, day: str) -> tuple[float, float]:
    """(początek, koniec) okresu raportu doby `day` wygenerowanego teraz."""
    if state.day == day and state.start:
        start = state.start                 # ponowne wygenerowanie tej samej doby
    elif state.end:
        start = state.end                   # od poprzedniego raportu
    else:
        start = now - FIRST_PERIOD          # pierwszy raport
    start = max(start, now - MAX_PERIOD)
    return min(start, now), now


def should_notify(state: State, day: str) -> bool:
    return state.notified != day


# --- stan ----------------------------------------------------------------------------------

def load_state(path: str) -> State:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return State()
    if not isinstance(data, dict):
        return State()
    state = State()
    for key in ("day", "notified", "opened"):
        if isinstance(data.get(key), str):
            setattr(state, key, data[key])
    for key in ("start", "end"):
        if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
            setattr(state, key, float(data[key]))
    for key in ("projects", "waiting"):
        if _count(data.get(key)):
            setattr(state, key, data[key])
    if "opened" not in data:
        # Stan sprzed śledzenia przeczytania: raportu, który już był, nie oznaczamy jako nowego.
        state.opened = state.day
    return state


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def save_state(path: str, state: State) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(asdict(state), fh, indent=2)
    os.replace(tmp, path)


# --- przeczytanie -------------------------------------------------------------------------

def day_of(index: str) -> str:
    """Doba raportu ze ścieżki …/RRRR-MM-DD/index.html (pusty napis, gdy to nie raport)."""
    name = os.path.basename(os.path.dirname(index or ""))
    return name if DAY_DIR.match(name) else ""


def status(root: str, state: State) -> dict:
    """Najnowszy raport dla widżetów (D-Bus Status()).

    {path, day, generatedAt, projects, waiting, unread}; bez raportu path i day są puste,
    a unread jest False. Liczby i chwila wygenerowania pochodzą ze stanu, gdy dotyczy tej samej
    doby (raport z `--report` stanu nie zapisuje — wtedy czas pliku i liczby nieznane)."""
    path = latest_index(root)
    day = day_of(path)
    if not day:
        return {"path": "", "day": "", "generatedAt": None, "projects": None, "waiting": None, "unread": False}
    same = state.day == day
    generated = state.end if same and state.end else None
    if generated is None:
        try:
            generated = os.path.getmtime(path)
        except OSError:
            generated = None
    return {"path": path, "day": day, "generatedAt": generated,
            "projects": state.projects if same else None, "waiting": state.waiting if same else None,
            "unread": state.opened != day}


def mark_opened(state: State, day: str) -> bool:
    """Oznacza raport doby jako przeczytany. True, gdy stan się zmienił."""
    if not day or state.opened == day:
        return False
    state.opened = day
    return True


# --- katalog raportów ----------------------------------------------------------------------

def report_days(root: str) -> list[str]:
    """Doby z raportem (katalogi RRRR-MM-DD z index.html), od najstarszej."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(root, name)
        if DAY_DIR.match(name) and os.path.isdir(path) and not os.path.islink(path) \
                and os.path.isfile(os.path.join(path, "index.html")):
            out.append(name)
    return sorted(out)


def latest_index(root: str) -> str:
    days = report_days(root)
    return os.path.join(root, days[-1], "index.html") if days else ""


def expired(root: str, today: date, keep_days: int) -> list[str]:
    """Katalogi raportów starsze niż keep_days dób. Tylko RRRR-MM-DD, bez dowiązań."""
    cutoff = today - timedelta(days=keep_days)
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name)
        if not DAY_DIR.match(name) or os.path.islink(path) or not os.path.isdir(path):
            continue
        try:
            day = date.fromisoformat(name)
        except ValueError:
            continue
        if day < cutoff:
            out.append(path)
    return out


def apply_retention(root: str, today: date, keep_days: int) -> list[str]:
    removed = []
    for path in expired(root, today, keep_days):
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed
