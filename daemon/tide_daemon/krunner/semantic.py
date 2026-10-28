"""Wyszukiwanie po znaczeniu w KRunnerze (PT-19): embeddingi metadanych sesji liczone modelem lokalnym.

Słowa w KRunnerze (search.py) znajdują sesję tylko wtedy, gdy każde słowo zapytania trafia w projekt, gałąź
albo tytuł. Tu dokładamy dopasowanie po znaczeniu: „ta sesja o dockerze i portach” znajdzie sesję
„Kontenery i mapowanie portów”, choć słowa się nie pokrywają. Wszystko liczy serwer modeli lokalnych
(rola `embed` w models.json, domyślnie bge-m3 na Ollamie tego komputera; przełączenie na Maca przenosi ją
razem z innymi rolami). Model chmurowy Ollamy jest odrzucany jak w każdym wywołaniu (models.client).

Co trafia do modelu — tylko metadane, które usługa i tak trzyma w indeksie KRunnera (index.py): tytuł,
początek pierwszej wiadomości (do 100 znaków), projekt, gałąź, a dla sesji koordynatora tytuły ticketów
z jego planu (tablica „Tide Sesje”). Nigdy treść rozmowy. Tekst przechodzi przez strip_secrets z raportu
porannego (klucze, tokeny, hasła w adresach, katalog domowy → „~”) i jest przycinany do DOC_LIMIT znaków.

Indeks (`Store`): $XDG_DATA_HOME/tide/search/sessions.json, plik 0600 w katalogu 0700, zapis atomowy.
Wektory są znormalizowane i zapisane jako float16 (base64) — 1024 wymiary bge-m3 to ok. 2,7 KB na sesję.
Bez tekstu dokumentów: przy każdej pozycji tylko skrót tekstu (zmiana tytułu = nowy wektor), czas
ostatniej aktywności i wektor. Nagłówek ma model i liczbę wymiarów: inny model (zmiana roli `embed`) albo
inna liczba wymiarów — indeks od nowa, bo wektory różnych modeli są nieporównywalne. Rozmiar ograniczony
oknem KRunnera (sesje z 30 dni) i MAX_ITEMS najświeższych; reszta wypada przy kolejnym przebiegu.

Indeksowanie (`sync`) w osobnym wątku: pierwszy przebieg FIRST_DELAY_S po starcie usługi, potem co
INDEX_EVERY_S i od razu po zmianie ustawień modeli (wake). Przyrostowe — liczy tylko nowe i zmienione
pozycje, partiami po BATCH, z najniższym priorytetem kolejki modeli (queue.IDLE): rusza dopiero, gdy na
serwerze nic innego nie czeka, więc nie opóźnia raportu ani opisów kart. Jeden model naraz na karcie 8 GB:
załadowanie bge-m3 wypycha z karty qwen3:8b, więc przebieg z czymś do policzenia czeka też na QUIET_S ciszy
po ostatnim wywołaniu innym modelem (inaczej następny opis karty ładowałby swój model od nowa).
Wyłączone w ustawieniach (`semanticSearch: false`) — nic nie jest liczone, a plik indeksu znika.

Zapytanie (`query_vector`): tylko od MIN_WORDS słów i MIN_CHARS znaków, po DEBOUNCE_S ciszy (KRunner pyta przy
każdym naciśnięciu klawisza — nowsze zapytanie unieważnia starsze, zanim cokolwiek pójdzie do serwera), z pamięcią
ostatnich wektorów. Wywołanie ma najwyższy priorytet kolejki, ale KRunner czeka na nie najwyżej QUERY_BUDGET_S:
po tym czasie dostaje same wyniki słowne, a wywołanie kończy się w tle (zimny model się ładuje, wektor trafia
do pamięci). Serwer nie odpowiada — przez BACKOFF_S nie próbujemy wcale. Żaden błąd nie trafia do KRunnera.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import operator
import os
import re
import struct
import threading
import time
from collections import OrderedDict
from datetime import datetime

from ..models import client as models_client
from ..models import config as models_config
from ..models import queue as models_queue
from ..models.settings import atomic_write
from ..models.status import model_key
from ..models.transport import Cancel, ModelError
from ..reports.summarize import strip_secrets
from .index import HISTORY_AGE

STORE_VERSION = 1
MAX_ITEMS = 2000
MAX_AGE = HISTORY_AGE          # to samo okno co sesje w KRunnerze (30 dni)
DOC_LIMIT = 700                # znaków tekstu na sesję (tytuł, początek rozmowy, projekt, gałąź, tickety)
TICKETS_MAX = 12
BATCH = 16                     # tekstów w jednym wywołaniu — krótko zajmuje serwer, zapytanie KRunnera nie czeka
FIRST_DELAY_S = 60.0
INDEX_EVERY_S = 300.0
SAVE_EVERY_S = 15.0
INDEX_WAIT_S = 300.0           # najwyżej tyle partia czeka w kolejce (serwer zajęty raportem) — potem następny przebieg
INDEX_TIMEOUT_S = 120.0        # pierwsza partia może ładować model
# Indeks rusza dopiero po tylu sekundach ciszy na serwerze po wywołaniu innym modelem: na karcie 8 GB
# załadowanie bge-m3 wypycha qwen3:8b (sprawdzone na Ollamie tego komputera), a raport i opisy kart wołają
# go seriami. Po ciszy dłuższej niż keep_alive Ollamy (5 min) model i tak by zniknął.
QUIET_S = 180.0
RETRY_S = 60.0                 # odłożony przebieg (serwer zajęty) — następna próba po tylu sekundach

MIN_WORDS = 3
MIN_CHARS = 12
DEBOUNCE_S = 0.3
QUERY_BUDGET_S = 1.5           # tyle KRunner najwyżej czeka na wektor zapytania
QUERY_WAIT_S = 2.0             # zapytanie w kolejce modeli (zajęty serwer — bez wyników znaczeniowych)
QUERY_TIMEOUT_S = 20.0         # samo wywołanie w tle (zimny model ładuje się kilka sekund)
BACKOFF_S = 30.0
CACHE_MAX = 64

# Ranking (podobieństwo kosinusowe). bge-m3 daje niezwiązanym tekstom ok. 0,3–0,4, więc sam próg byłby
# kruchy: wynik musi przekroczyć MIN_SIMILARITY, średnią wszystkich sesji o MARGIN (tło tego zapytania)
# i być najwyżej SPREAD poniżej najlepszego. Skalibrowane na bge-m3 (build/semantic_check.py, PT-19):
# „ta sesja o dockerze i portach” → dwie sesje o Dockerze (0,47 i 0,45 przy średniej 0,39), zapytanie
# bez pasującej sesji („coś o tapecie i kolorach okna”, najlepsze 0,42 przy średniej 0,37) → nic.
MIN_SIMILARITY = 0.42
MARGIN = 0.06
SPREAD = 0.08
LIMIT = 5

STATES = ("off", "waiting", "indexing", "ready", "error")


def store_path(env=None) -> str:
    """$TIDE_SEARCH_INDEX (testy, diagnostyka), inaczej $XDG_DATA_HOME/tide/search/sessions.json."""
    env = os.environ if env is None else env
    if env.get("TIDE_SEARCH_INDEX"):
        return env["TIDE_SEARCH_INDEX"]
    data = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "tide", "search", "sessions.json")


# --- dokument i wektory ----------------------------------------------------------------------------

def document(entry: dict, tickets=(), home: str = "") -> str:
    """Tekst sesji dla modelu: tytuł, początek pierwszej wiadomości (gdy inny niż tytuł), projekt, gałąź
    (słowami) i tytuły ticketów planu. Po strip_secrets, najwyżej DOC_LIMIT znaków."""
    title = " ".join(str(entry.get("title") or "").split())
    prompt = " ".join(str(entry.get("prompt") or "").split())
    branch = re.sub(r"[/_.\-]+", " ", str(entry.get("branch") or "")).strip()
    parts = [title]
    if prompt and prompt != title:
        parts.append(prompt)
    parts.append(" · ".join(p for p in (str(entry.get("project") or ""), branch) if p))
    items = [" ".join(str(t).split()) for t in list(tickets or ())[:TICKETS_MAX]]
    if any(items):
        parts.append("; ".join(t for t in items if t))
    text = strip_secrets("\n".join(p for p in parts if p), home)
    return text[:DOC_LIMIT].strip()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def normalize(vector) -> tuple:
    norm = math.sqrt(sum(x * x for x in vector))
    return tuple(x / norm for x in vector) if norm > 0 else tuple(0.0 for _ in vector)


def dot(a, b) -> float:
    return sum(map(operator.mul, a, b))


def pack(vector) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode("ascii")


def unpack(text: str, dims: int) -> tuple | None:
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError, AttributeError):
        return None
    if dims <= 0 or len(raw) != 2 * dims:
        return None
    return struct.unpack(f"<{dims}e", raw)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") if ts else ""


# --- plik indeksu ----------------------------------------------------------------------------------

class Store:
    """Wektory sesji na dysku: {version, model, dims, updated, items: {id: {hash, ts, vec}}}. Bez tekstów."""

    def __init__(self, path: str):
        self.path = path
        self.model = ""
        self.dims = 0
        self.updated = 0.0
        self.items: dict[str, dict] = {}   # id → {"hash", "ts", "vec": tuple}

    def reset(self, model: str = "") -> None:
        self.model, self.dims, self.items = model, 0, {}

    def load(self) -> str:
        """Czyta plik; brak — pusty indeks, zepsuty albo innej wersji — pusty z opisem (plik nadpisze zapis)."""
        self.reset()
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return ""
        except (OSError, ValueError) as exc:
            return f"indeks wyszukiwania nieczytelny ({type(exc).__name__}) — liczę od nowa"
        if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
            return "indeks wyszukiwania w innej wersji — liczę od nowa"
        model, dims, items = data.get("model"), data.get("dims"), data.get("items")
        if not isinstance(model, str) or not isinstance(dims, int) or isinstance(dims, bool) or not isinstance(items, dict):
            return "indeks wyszukiwania nieczytelny — liczę od nowa"
        self.model, self.dims = model, dims
        updated = data.get("updated")
        self.updated = float(updated) if isinstance(updated, (int, float)) and not isinstance(updated, bool) else 0.0
        for key, item in items.items():
            if not isinstance(item, dict) or not isinstance(item.get("hash"), str):
                continue
            vec = unpack(item.get("vec"), dims)
            ts = item.get("ts")
            if vec is None or not isinstance(ts, (int, float)) or isinstance(ts, bool):
                continue
            self.items[str(key)] = {"hash": item["hash"], "ts": float(ts), "vec": vec}
        return ""

    def save(self) -> None:
        data = {"version": STORE_VERSION, "model": self.model, "dims": self.dims, "updated": self.updated,
                "items": {k: {"hash": v["hash"], "ts": round(v["ts"], 3), "vec": pack(v["vec"])}
                          for k, v in self.items.items()}}
        atomic_write(self.path, json.dumps(data, separators=(",", ":")).encode("utf-8"), 0o600)
        try:  # katalog z wektorami tylko dla właściciela, także gdy istniał wcześniej
            os.chmod(os.path.dirname(os.path.realpath(self.path)), 0o700)
        except OSError:
            pass

    def remove(self) -> None:
        self.reset()
        self.updated = 0.0
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


# --- indeks i zapytania ----------------------------------------------------------------------------

class SemanticSearch:
    """Indeks znaczeniowy sesji KRunnera i wektory zapytań. Bez Qt; indeksowanie w osobnym wątku (start/stop),
    zapytania w wątkach wtyczki KRunnera.

    index    — SessionIndex KRunnera (entries() z polem `prompt`),
    tickets  — funkcja() → {id sesji: [„PT-19 tytuł”, …]} (tickety planu koordynatora) albo None,
    config   — funkcja() → ModelsConfig (models.json czytany przy każdym przebiegu),
    embed    — models.embed (testy podają atrapę)."""

    def __init__(self, index, path: str | None = None, tickets=None, config=None, embed=None, clock=time.time,
                 home: str | None = None, every: float = INDEX_EVERY_S, first_delay: float = FIRST_DELAY_S,
                 debounce: float = DEBOUNCE_S, budget: float = QUERY_BUDGET_S, monotonic=time.monotonic,
                 queue=None):
        self.index = index
        self.store = Store(path or store_path())
        self.tickets = tickets
        self.config = config or models_config.load
        self.embed = embed or models_client.embed
        self.clock = clock
        self.monotonic = monotonic
        self.home = home if home is not None else os.path.expanduser("~")
        self.every = every
        self.first_delay = first_delay
        self.debounce = debounce
        self.budget = budget
        # Kolejka modeli (cisza na serwerze przed indeksowaniem); None przy atrapie `embed` bez kolejki.
        self.queue = queue if queue is not None else (models_queue.shared() if embed is None else None)
        self._retry = False
        self.cancel = Cancel()
        self._lock = threading.Lock()
        self._snapshot: tuple[str, int, dict] = ("", 0, {})   # (model, wymiary, {id: wektor}) dla zapytań
        self._state = {"state": "waiting", "items": 0, "pending": 0, "updated": "", "error": "", "model": "",
                       "dims": 0}
        self._listeners: list = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loaded = False
        self._seq = 0
        self._cache: OrderedDict = OrderedDict()
        self._down_until = 0.0

    # --- stan -----------------------------------------------------------------------------------

    def state(self) -> dict:
        """{state, items, pending, updated, error, model, dims} — bez tekstów i wektorów."""
        with self._lock:
            return dict(self._state)

    def add_listener(self, listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def _set(self, **changes) -> None:
        with self._lock:
            new = dict(self._state, **changes)
            if new == self._state:
                return
            self._state = new
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener()
            except Exception as exc:  # słuchacz nie może zatrzymać indeksowania
                print(f"tide-daemon: wyszukiwanie: błąd słuchacza ({type(exc).__name__})", flush=True)

    def _publish(self) -> None:
        vectors = {k: v["vec"] for k, v in self.store.items.items()}
        with self._lock:
            self._snapshot = (self.store.model, self.store.dims, vectors)

    # --- wątek ----------------------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="tide-search-index", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.cancel.set()  # trwające i czekające wywołania modelu kończą się od razu

    def wake(self) -> None:
        """Przebieg zaraz (zmiana ustawień modeli: włączenie, wyłączenie, inny model roli `embed`)."""
        self._wake.set()

    def _run(self) -> None:
        self._load()
        self._wake.wait(self.first_delay)
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.sync()
            except Exception as exc:  # przeoczony przypadek: stan z błędem, wątek działa dalej
                print(f"tide-daemon: wyszukiwanie: {exc!r}", flush=True)
                self._set(state="error", error=f"błąd indeksowania ({type(exc).__name__})")
            retry, self._retry = self._retry, False
            self._wake.wait(min(self.every, RETRY_S) if retry else self.every)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        problem = self.store.load()
        if problem:
            print(f"tide-daemon: wyszukiwanie: {problem}", flush=True)
        self._publish()
        self._set(items=len(self.store.items), model=self.store.model, dims=self.store.dims,
                  updated=_iso(self.store.updated))

    # --- indeksowanie ---------------------------------------------------------------------------

    def _enabled(self, cfg=None) -> bool:
        try:
            return bool((cfg or self.config()).semantic_search)
        except Exception:  # nieczytelne ustawienia — models.config i tak daje wartości domyślne
            return True

    def sync(self) -> None:
        """Jeden przebieg: wyłączone — usunięcie indeksu; włączone — nowe i zmienione sesje do modelu."""
        self._load()
        cfg = self.config()
        if not self._enabled(cfg):
            if self.store.items or os.path.exists(self.store.path):
                self.store.remove()
                self._publish()
            self._set(state="off", items=0, pending=0, error="", updated="", dims=0)
            return
        try:
            role = cfg.role("embed")
        except ModelError as exc:
            self._set(state="error", error=str(exc), pending=0)
            return
        identity = model_key(role.model)
        if self.store.model != identity:
            if self.store.items:
                print(f"tide-daemon: wyszukiwanie: model {identity} zamiast {self.store.model} — indeks od nowa",
                      flush=True)
            self.store.reset(identity)
            self._publish()
        self.index.refresh()
        wanted = self._documents()
        stale = sum(1 for k, (h, _ts, _t) in wanted.items() if (self.store.items.get(k) or {}).get("hash") != h)
        if stale and self._busy_for(cfg, role, identity):
            self._set(state="waiting", pending=stale, items=len(self.store.items), model=identity)
            self._retry = True
            return
        changed = False
        for key in [k for k in self.store.items if k not in wanted]:
            del self.store.items[key]
            changed = True
        for key, (hash_, ts, _text) in wanted.items():
            item = self.store.items.get(key)
            if item is not None and item["hash"] == hash_:
                item["ts"] = ts
        todo = [k for k, (hash_, _ts, _text) in wanted.items()
                if (self.store.items.get(k) or {}).get("hash") != hash_]
        if changed:
            self._publish()
        self._set(state="indexing" if todo else self._state_after(), items=len(self.store.items), pending=len(todo),
                  model=identity, error="" if todo else self._state["error"])
        saved = self.monotonic()
        error = ""
        for start in range(0, len(todo), BATCH):
            if self._stop.is_set() or not self._enabled():
                break
            keys = todo[start:start + BATCH]
            try:
                result = self.embed([wanted[k][2] for k in keys], role="embed", priority=models_queue.IDLE,
                                    wait=INDEX_WAIT_S, timeout=INDEX_TIMEOUT_S, cancel=self.cancel)
            except models_queue.Busy:
                self._retry = True
                break  # serwer zajęty dłużej niż INDEX_WAIT_S — reszta w następnym przebiegu
            except ModelError as exc:
                error = str(exc)
                break
            if model_key(result.model) != self.store.model:
                break  # rola zmieniona w trakcie — następny przebieg zacznie od nowa
            if self.store.dims and result.dims != self.store.dims:
                self.store.reset(self.store.model)
                self._publish()
                changed = True
                break
            self.store.dims = result.dims
            for key, vector in zip(keys, result.vectors):
                hash_, ts, _text = wanted[key]
                self.store.items[key] = {"hash": hash_, "ts": ts, "vec": normalize(vector)}
            changed = True
            self._publish()
            self._set(items=len(self.store.items), pending=max(0, len(todo) - start - len(keys)),
                      dims=self.store.dims)
            if self.monotonic() - saved >= SAVE_EVERY_S:
                self._save()
                saved = self.monotonic()
        pending = sum(1 for k, (hash_, _ts, _text) in wanted.items()
                      if (self.store.items.get(k) or {}).get("hash") != hash_)
        if not pending:
            self.store.updated = self.clock()
        if changed or not pending:
            self._save()
        self._set(state="error" if error else ("ready" if not pending else "waiting"), error=error,
                  items=len(self.store.items), pending=pending, updated=_iso(self.store.updated),
                  dims=self.store.dims)

    def _busy_for(self, cfg, role, identity) -> bool:
        """Serwer roli pracuje albo niedawno pracował innym modelem (QUIET_S) — przebieg odłożony."""
        if self.queue is None:
            return False
        try:
            lane = models_queue.lane_of(cfg.endpoint(role.endpoint).url)
        except ModelError:
            return False
        quiet, last = self.queue.quiet(lane)
        if quiet is None:
            return True
        return quiet < QUIET_S and bool(last) and model_key(last) != identity

    def _state_after(self) -> str:
        return "ready" if self.store.items or self.store.updated else "waiting"

    def _save(self) -> None:
        try:
            self.store.save()
        except OSError as exc:
            print(f"tide-daemon: wyszukiwanie: nie udało się zapisać indeksu ({type(exc).__name__})", flush=True)

    def _documents(self) -> dict:
        """{id sesji: (skrót, czas, tekst)} — sesje z okna MAX_AGE, najwyżej MAX_ITEMS najświeższych."""
        try:
            tickets = (self.tickets() if self.tickets else None) or {}
        except Exception:  # tablica koordynacji nie może zatrzymać indeksu
            tickets = {}
        cutoff = self.clock() - MAX_AGE
        out = {}
        for entry in sorted(self.index.entries(), key=lambda e: -(e.get("lastActivity") or 0)):
            ts = entry.get("lastActivity") or 0
            if ts < cutoff or not entry.get("id"):
                continue
            text = document(entry, tickets.get(entry["id"]) or (), self.home)
            if text:
                out[entry["id"]] = (digest(text), float(ts), text)
            if len(out) >= MAX_ITEMS:
                break
        return out

    # --- zapytania (wątki wtyczki KRunnera) --------------------------------------------------------

    def ready(self) -> bool:
        with self._lock:
            return bool(self._snapshot[2])

    def wants(self, text: str) -> bool:
        """Czy zapytanie nadaje się na wyszukiwanie po znaczeniu: dość słów i znaków, a indeks coś ma."""
        text = " ".join(str(text or "").split())
        return len(text.split()) >= MIN_WORDS and len(text) >= MIN_CHARS and self.ready()

    def query_vector(self, text: str):
        """Wektor zapytania albo None (nowsze zapytanie, wyłączone, serwer nie odpowiada, minął budżet).
        Blokuje najwyżej debounce + budget — wołać poza wątkiem głównym."""
        key = " ".join(str(text or "").casefold().split())
        with self._lock:
            self._seq += 1
            seq = self._seq
            model = self._snapshot[0]
            cached = self._cache.get((model, key))
            if cached is not None:
                self._cache.move_to_end((model, key))
                return cached
        if self.debounce > 0:
            time.sleep(self.debounce)
        with self._lock:
            if seq != self._seq:
                return None  # użytkownik pisze dalej — to zapytanie KRunner i tak odrzuci
        if self.monotonic() < self._down_until or not self._enabled():
            return None
        done = threading.Event()
        box: dict = {}

        def work():
            try:
                result = self.embed([key], role="embed", priority=models_queue.HIGH, wait=QUERY_WAIT_S,
                                    timeout=QUERY_TIMEOUT_S, cancel=self.cancel)
                if model_key(result.model) != model or not result.vectors:
                    return  # indeks z innego modelu — porównanie nie miałoby sensu
                vector = normalize(result.vectors[0])
                with self._lock:
                    self._cache[(model, key)] = vector
                    while len(self._cache) > CACHE_MAX:
                        self._cache.popitem(last=False)
                box["vector"] = vector
            except models_queue.Busy:
                pass  # serwer zajęty (raport) — same słowa, bez wstrzymywania kolejnych prób
            except ModelError:
                self._down_until = self.monotonic() + BACKOFF_S
            except Exception as exc:  # przeoczony przypadek — same słowa
                print(f"tide-daemon: wyszukiwanie: zapytanie: {exc!r}", flush=True)
            finally:
                done.set()

        threading.Thread(target=work, name="tide-search-query", daemon=True).start()
        done.wait(self.budget)
        return box.get("vector")

    def similar(self, vector, entries, limit: int = LIMIT) -> list:
        """[(podobieństwo, sesja)] od najbardziej podobnej: co najmniej MIN_SIMILARITY, MARGIN ponad średnią
        i najwyżej SPREAD poniżej najlepszej, najwyżej `limit`. Sesje bez wektora (jeszcze nie policzone) są
        pomijane."""
        with self._lock:
            _model, dims, vectors = self._snapshot
        if not vector or not vectors or len(vector) != dims:
            return []
        scored = [(dot(vector, vectors[e["id"]]), e) for e in entries if e.get("id") in vectors]
        if not scored:
            return []
        best = max(s for s, _e in scored)
        mean = sum(s for s, _e in scored) / len(scored)
        floor = max(MIN_SIMILARITY, best - SPREAD, mean + MARGIN if len(scored) > 1 else MIN_SIMILARITY)
        hits = [(s, e) for s, e in scored if s >= floor]
        hits.sort(key=lambda h: (-h[0], -(h[1].get("lastActivity") or 0)))
        return hits[:limit]
