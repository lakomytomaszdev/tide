"""Stan pudełka: dziennik pudełek projektu w Tablicy, nazwy pudełek i `BoxStatus`.

Dziennik: `<Tablica>/projects/<slug>/box/journal.jsonl` (JSON Lines, tylko dopisywanie pod blokadą `flock`).
Zdarzenie: `v`, `ts`, `type`, `box` (nazwa instancji Incusa) i pola typu; `box.build`, `box.ready`, `box.reset`,
`box.stop`, `box.verify` mają `duration` (sekundy). Stan pudełka wynika z ostatniego zdarzenia, a potem jest korygowany tym,
co mówi Incus (brak instancji, brak filtrowania NIC, hash migawki różny od profilu, zatrzymane).

Stany: `brak → budowa → clean → w użyciu → nieaktualny → reset → zatrzymane`, plus `niezgodne` (karta sieciowa
bez filtrowania — krok nie startuje) i `błąd budowy`. „Zbudowane z ostrzeżeniem” to `clean` z flagą
`warning` (nieudane `verify`).
"""
import fcntl
import json
import os
import re
import time
import threading
from contextlib import contextmanager
from functools import wraps
from dataclasses import dataclass, field

from . import profile as bp

SCHEMA = 1

ABSENT = "brak"
BUILDING = "budowa"
CLEAN = "clean"
IN_USE = "w użyciu"
STALE = "nieaktualny"
RESETTING = "reset"
STOPPED = "zatrzymane"
NONCOMPLIANT = "niezgodne"
FAILED = "błąd budowy"

EVENTS = ("box.build", "box.ready", "box.reset", "box.stop", "box.verify", "box.approved",
          "box.use", "box.release", "box.destroy")
TIMED = ("box.build", "box.ready", "box.reset", "box.stop", "box.verify")

RECIPE_KEY = "user.tide.recipe"      # odcisk przepisu (JSON z profile.recipe_stamp) — zapisany w migawce clean
COPY_OF_KEY = "user.tide.copy-of"    # kopia per karta: nazwa pudełka projektu
CARD_KEY = "user.tide.card"
SNAPSHOT = "clean"
HOME_PATH = "/home/agent"
HOME_MODE = "0750"                   # `initial.mode` wolumenu `/home/agent` (właściciel: agent-uid/gid z ustawień)

_NAME_BAD = re.compile(r"[^a-z0-9-]+")


def box_name(slug: str) -> str:
    """Nazwa pudełka projektu w Incusie: slug (Incus przyjmuje litery, cyfry i `-`)."""
    bp._check_slug(slug, "projekt")
    name = _NAME_BAD.sub("-", slug.lower()).strip("-")
    if not name or not name[0].isalpha():
        name = "p-" + name
    return name[:63]


def card_box_name(slug: str, card: str) -> str:
    """Nazwa kopii pudełka dla karty (`box: per-card`): `<slug>-<karta>`, np. `plasma-tide-k-38`.

    Jedno źródło nazwy dla buildera, silnika (#18), cyklu życia (#19), BoxRunnera (#6) i domknięcia (#10).
    """
    card_id = str(card).strip().lower()
    if not re.match(r"^k-[1-9][0-9]{0,8}$", card_id):
        raise ValueError(f"niepoprawny identyfikator karty: {card!r}")
    return f"{box_name(slug)[:62 - len(card_id)]}-{card_id}"


def home_volume(name: str) -> str:
    return f"{name}-home"


# ------------------------------------------------------------ dziennik

_held = threading.local()


@contextmanager
def lifecycle_lock(slug):
    """Wspólna blokada projektu dla CLI, demona i rezerwacji kroku; zagnieżdżalna w wątku.

    Osobny plik od blokady montowań i dziennika: kolejność to cykl życia → montowania/dziennik.
    Blokada projektu obejmuje też kopiowanie migawki oraz destroy wszystkich kopii.
    """
    path = journal_path(slug) + ".lifecycle.lock"
    held = getattr(_held, "paths", set())
    if path in held:
        yield
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        _held.paths = held | {path}
        try:
            yield
        finally:
            _held.paths = held
            fcntl.flock(fh, fcntl.LOCK_UN)


def serialized(method):
    @wraps(method)
    def run(self, slug, *args, **kwargs):
        with lifecycle_lock(slug):
            return method(self, slug, *args, **kwargs)
    return run


def journal_path(slug: str) -> str:
    return os.path.join(bp.board_dir(), "projects", bp._check_slug(slug, "projekt"), "box", "journal.jsonl")


def record(slug: str, kind: str, box: str, clock=time.time, **fields) -> dict:
    if kind not in EVENTS:
        raise ValueError(f"nieznane zdarzenie pudełka: {kind}")
    event = {"v": SCHEMA, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(clock())),
             "type": kind, "box": box}
    event.update(fields)
    if kind in TIMED and "duration" not in event and event.get("phase") != "start":
        raise ValueError(f"{kind}: brak pola duration")
    path = journal_path(slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    return event


def events(slug: str, box: str | None = None) -> list:
    try:
        with open(journal_path(slug), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and (box is None or event.get("box") == box):
            out.append(event)
    return out


def mark_in_use(slug: str, box: str, step: str, clock=time.time) -> dict:
    """Krok wszedł do pudełka (woła #18); `reset` i `destroy` odmawiają do `mark_released`."""
    with lifecycle_lock(slug):
        return record(slug, "box.use", box, clock, step=step)


def mark_released(slug: str, box: str, step: str, clock=time.time) -> dict:
    return record(slug, "box.release", box, clock, step=step)


def running_steps(slug: str, box: str) -> list:
    """Kroki, które według dziennika są w pudełku (`box.use` bez `box.release`)."""
    steps: list = []
    for event in events(slug, box):
        step = event.get("step")
        if event["type"] == "box.use" and step and step not in steps:
            steps.append(step)
        elif event["type"] == "box.release" and step in steps:
            steps.remove(step)
        elif event["type"] in ("box.destroy", "box.build") and event.get("phase") != "end":
            steps = []
    return steps


def journal_state(slug: str, box: str) -> tuple:
    """(stan, ostrzeżenie) z dziennika, bez pytania Incusa."""
    state, warning = ABSENT, False
    for event in events(slug, box):
        kind = event["type"]
        if kind == "box.build":
            if event.get("phase") == "start":
                state, warning = BUILDING, False
            elif event.get("result") == "error":
                state = FAILED
        elif kind == "box.ready":
            state, warning = CLEAN, bool(event.get("warning"))
        elif kind == "box.reset":
            if event.get("removed"):
                state = ABSENT
            elif event.get("result") != "deferred":
                state = CLEAN if event.get("result", "ok") == "ok" else RESETTING
        elif kind == "box.stop":
            state = STOPPED
        elif kind == "box.destroy":
            state, warning = ABSENT, False
    if state in (CLEAN, STOPPED) and running_steps(slug, box):
        state = IN_USE
    return state, warning


# ------------------------------------------------------------ status

@dataclass
class BoxStatus:
    """Stan pudełka do pokazania i do decyzji „czy krok może wejść” (#18)."""
    slug: str
    box: str
    state: str
    exists: bool = False
    running: bool = False
    journal_state: str = ABSENT
    warning: bool = False
    nic_filtering: bool = False
    nic_detail: dict = field(default_factory=dict)
    recipe_hash: str = ""
    profile_hash: str = ""
    diff: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    error: str = ""

    @property
    def usable(self) -> bool:
        """Krok może wejść: pudełko istnieje, ma filtrowanie NIC, jest zgodne z zatwierdzonym profilem (brak
        profilu = nieużywalne) i nie trwa budowa/reset."""
        return (self.exists and self.nic_filtering and bool(self.profile_hash) and not self.diff
                and self.state in (CLEAN, IN_USE, STOPPED))

    def to_dict(self) -> dict:
        return {"slug": self.slug, "box": self.box, "state": self.state, "exists": self.exists,
                "running": self.running, "journalState": self.journal_state, "warning": self.warning,
                "nicFiltering": self.nic_filtering, "nic": self.nic_detail, "recipeHash": self.recipe_hash,
                "profileHash": self.profile_hash, "diff": list(self.diff), "steps": list(self.steps),
                "usable": self.usable, "error": self.error}


def nic_flags(settings) -> dict:
    """Wymagane ustawienia karty pudełka (filtrowanie NIC): adres źródłowy = przydzielony (#5 kluczuje IP)."""
    return {"security.mac_filtering": True, "security.ipv4_filtering": True,
            "security.ipv6_filtering": bool(settings.ipv6_filtering)}


def nic_compliant(devices: dict, settings) -> tuple:
    """(zgodne, ustawienia karty) — z lokalnych urządzeń instancji (`config device show`)."""
    nic = devices.get(settings.nic) if isinstance(devices, dict) else None
    if not isinstance(nic, dict):
        return False, {}
    detail = {k: str(nic.get(k, "")).lower() for k in nic_flags(settings)}
    ok = all(detail[k] == "true" for k, want in nic_flags(settings).items() if want)
    return ok, detail


def parse_stamp(text: str):
    if not text:
        return None
    try:
        stamp = json.loads(text)
    except ValueError:
        return text            # sam hash
    return stamp if isinstance(stamp, dict) else None
