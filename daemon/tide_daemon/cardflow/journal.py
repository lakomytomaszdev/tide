"""Dziennik karty: zdarzenia przepływu karty dopisywane linia po linii (JSON Lines), jedyne źródło stanu karty.

- plik w repozytorium tablicy: `flow/<K-12>/journal.jsonl`; obok wyjście kroków (`flow/<K-12>/steps/<krok>/`),
- kopia usługi poza repozytorium tablicy: `$XDG_STATE_HOME/tide/cardflow/<skrót ścieżki tablicy>/<K-12>.jsonl`.
  Revert w repozytorium tablicy (albo ręczne usunięcie) nie cofa dziennika: `check()` odtwarza plik z kopii
  i zwraca ostrzeżenie. Kopia jest zapisywana pierwsza, więc to ona jest prawdą; brak kopii (np. usunięty katalog
  stanu) — kopia powstaje z pliku w tablicy,
- zdarzenie: `v` (wersja schematu), `seq` (kolejny numer w karcie, od 1), `ts`, `card`, `type` i pola typu,
- dopisuje wyłącznie usługa, pod blokadą (`lock`) — z wątku roboczego przepływu i z wątku głównego. Ścieżki
  zmienione od ostatniego commita zbiera `take_dirty()` (commit robi wątek główny, cardflow_monitor.py).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import threading
from copy import copy
from datetime import datetime, timezone

from .processidentity import CARD_STEP_KINDS, TICKET_STEP_KINDS, start_error
from .state import FlowState, fold

SCHEMA = 1
FLOW_DIR = "flow"
JOURNAL = "journal.jsonl"
CARD_DIR_RE = re.compile(r"^K-[1-9][0-9]{0,8}$")


def _validate_process_identity(event):
    """Nieznana tożsamość nie jest dowodem, że proces nie podlega ochronie.

    Starszy zapis może nie mieć kind/branch, lecz musi nazywać krok i ticket.
    Brak ticketu jest legalny wyłącznie dla rozpoznanego rodzaju kroku karty.
    Nie wymagamy jednostki: jej brak ma pozostać nierozstrzygniętym procesem.
    """
    if not (event["type"] == "step.started"
            or (event["type"] == "intent" and event.get("action") == "step-start")):
        return
    error = start_error(event)
    if error:
        raise ValueError(error)


def mirror_root(board_root: str, env=None) -> str:
    env = os.environ if env is None else env
    state = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    key = hashlib.sha1(os.path.realpath(board_root).encode("utf-8")).hexdigest()[:12]
    return os.path.join(state, "tide", "cardflow", key)


class Journal:
    def __init__(self, board_root: str, mirror: str | None = None, clock=None):
        self.root = os.path.abspath(board_root)
        self.mirror = mirror or mirror_root(self.root)
        self.clock = clock
        self.lock = threading.RLock()
        self._dirty: set[str] = set()
        self._cache: dict[str, tuple] = {}     # karta → (rozmiar i mtime kopii, zdarzenia)
        self._states: dict[str, tuple] = {}    # karta → (wersja dziennika, projekcja)
        # K-98 #2: odczyt prezentacji (publikacja migawki w wątku głównym) — bez blokady, z własną pamięcią.
        self._view = threading.local()
        self._view_cache: dict[str, tuple] = {}
        self._view_states: dict[str, tuple] = {}

    # --- odczyt prezentacji ------------------------------------------------------------------------

    @contextlib.contextmanager
    def presentation(self):
        """Odczyt do prezentacji (K-98 #2) w bieżącym wątku: bez blokady dziennika, więc publikacja migawki nigdy
        nie czeka na mutację. Kopia usługi jest tylko dopisywana, a plik w tablicy podmieniany atomowo, więc
        czytamy zawsze pełne zdarzenia; urwany ostatni wiersz trwającego dopisania pomijamy (jeszcze go nie ma).
        Pamięć jest osobna od pamięci workera. Wyłącznie do wyświetlania — zapisy i bramki czytają pod blokadą."""
        before = getattr(self._view, "on", False)
        self._view.on = True
        try:
            yield
        finally:
            self._view.on = before

    def _presenting(self) -> bool:
        return getattr(self._view, "on", False)

    def _guard(self):
        return contextlib.nullcontext() if self._presenting() else self.lock

    # --- ścieżki ---------------------------------------------------------------------------------

    def rel_dir(self, card: str) -> str:
        return os.path.join(FLOW_DIR, card)

    def rel_path(self, card: str) -> str:
        return os.path.join(FLOW_DIR, card, JOURNAL)

    def path(self, card: str) -> str:
        return os.path.join(self.root, self.rel_path(card))

    def mirror_path(self, card: str) -> str:
        return os.path.join(self.mirror, card + ".jsonl")

    # --- odczyt ----------------------------------------------------------------------------------

    def cards(self) -> list[str]:
        """Karty z dziennikiem (z kopii i z tablicy)."""
        found = set()
        try:
            found |= {n[:-6] for n in os.listdir(self.mirror) if n.endswith(".jsonl")}
        except OSError:
            pass
        try:
            found |= {n for n in os.listdir(os.path.join(self.root, FLOW_DIR))
                      if os.path.isfile(os.path.join(self.root, FLOW_DIR, n, JOURNAL))}
        except OSError:
            pass
        return sorted(n for n in found if CARD_DIR_RE.match(n))

    def has(self, card: str) -> bool:
        return os.path.isfile(self.mirror_path(card)) or os.path.isfile(self.path(card))

    def cards_complete(self) -> list[str]:
        """Rejestr dla kontroli bezpieczeństwa; błąd listowania propaguje unknown.

        Brak katalogu jest prawidłowy przed pierwszym zapisem. Istniejący katalog
        musi być czytelny i nie może być dowiązaniem. Katalog karty bez dziennika
        też trafia do wyniku, aby read_complete odmówił zamiast pominąć historię.
        """
        with self._guard():
            found = set()
            for root, mirror in ((self.mirror, True), (os.path.join(self.root, FLOW_DIR), False)):
                try:
                    mode = os.lstat(root).st_mode
                except FileNotFoundError:
                    continue
                if not stat.S_ISDIR(mode):
                    raise ValueError("rejestr kart nie jest katalogiem")
                for name in os.listdir(root):
                    card = name[:-6] if mirror and name.endswith(".jsonl") else name
                    if mirror and not name.endswith(".jsonl"):
                        continue
                    if not CARD_DIR_RE.fullmatch(card):
                        continue
                    if not mirror and not stat.S_ISDIR(os.lstat(os.path.join(root, name)).st_mode):
                        raise ValueError("rejestr karty nie jest katalogiem")
                    found.add(card)
            return sorted(found)

    def read(self, card: str) -> list[dict]:
        """Zdarzenia karty z kopii usługi (albo z tablicy, gdy kopii nie ma). Pamięć po rozmiarze i mtime."""
        with self._guard():
            return list(self._read_version(card)[1])

    def state(self, card: str) -> FlowState:
        """Projekcja z własnymi kontenerami pól. Zagnieżdżone wartości służą tylko do odczytu."""
        with self._guard():
            key, events = self._read_version(card)
            states = self._view_states if self._presenting() else self._states
            hit = states.get(card)
            if hit is None or hit[0] != key:
                hit = (key, fold(events))
                states[card] = hit
            state = copy(hit[1])
            # Silnik lokalnie zmienia pola i kontenery projekcji. Wartości zagnieżdżone
            # (także wytworzone przez fold) tylko czyta; deepcopy jest droższy od fold.
            for name, value in vars(state).items():
                if isinstance(value, (dict, list, set)):
                    setattr(state, name, value.copy())
            return state

    def _read_version(self, card: str) -> tuple:
        cache = self._view_cache if self._presenting() else self._cache
        source = self.mirror_path(card) if os.path.isfile(self.mirror_path(card)) else self.path(card)
        try:
            st = os.stat(source)
        except OSError:
            cache.pop(card, None)
            return (source, None, None), []
        key = (source, st.st_size, st.st_mtime_ns)
        hit = cache.get(card)
        if hit is not None and hit[0] == key:
            return hit
        text = _read(source)
        if self._presenting() and not text.endswith("\n"):
            # Dopisanie w toku: bez urwanego wiersza i bez pamięci (klucz nie opisuje pełnej treści).
            return key, _parse(text[:text.rfind("\n") + 1])
        events = _parse(text)
        cache[card] = (key, events)
        return key, events

    def read_complete(self, card: str) -> list[dict]:
        """Odczyt dla bramki: błąd lub brak historii nie jest pustą historią.

        Bez tolerancyjnego parsera/cache i bez naprawiania plików. Istniejąca
        kopia hostowa jest autorytatywna także wtedy, gdy nie da się jej odczytać.
        OSError/ValueError oznaczają dla wywołującego stan unknown.
        """
        with self._guard():
            source = self.mirror_path(card)
            try:
                os.lstat(source)
            except FileNotFoundError:
                source = self.path(card)
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, encoding="utf-8") as fh:
                if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                    raise ValueError("dziennik nie jest zwykłym plikiem")
                events = []
                for line in fh:
                    if not line.endswith("\n"):
                        raise ValueError("niepełny zapis zdarzenia")
                    event = json.loads(line)
                    if (not isinstance(event, dict) or type(event.get("v")) is not int
                            or event["v"] != SCHEMA or type(event.get("seq")) is not int
                            or event["seq"] != len(events) + 1 or event.get("card") != card
                            or not isinstance(event.get("type"), str) or not event["type"]):
                        raise ValueError("niepełna lub nieznana historia zdarzeń")
                    _validate_process_identity(event)
                    events.append(event)
                return events

    # --- zapis -----------------------------------------------------------------------------------

    def _invalidate(self, card: str) -> None:
        self._cache.pop(card, None)
        self._states.pop(card, None)

    def append(self, card: str, kind: str, /, **data) -> dict:
        with self.lock:
            # Tolerancyjny odczyt służy prezentacji, nigdy odbudowie kopii przy
            # zapisie. Inaczej dowolne późniejsze zdarzenie usuwa dowód unknown.
            exists = os.path.lexists(self.mirror_path(card)) or os.path.lexists(self.path(card))
            events = self.read_complete(card) if exists else []
            return self._append(card, kind, events, data)

    def append_complete(self, card: str, kind: str, /, **data) -> dict:
        """Zapis bramki tylko na pełnej historii, bez tolerancyjnego odtworzenia."""
        with self.lock:
            return self._append(card, kind, self.read_complete(card), data)

    def _append(self, card, kind, events, data):
        # Wywołujący trzyma lock od odczytu aż do końca zapisu.
        event = {"v": SCHEMA, "seq": len(events) + 1, "ts": self._now(), "card": card, "type": kind, **data}
        line = json.dumps(event, ensure_ascii=False) + "\n"
        self._invalidate(card)
        _mkdir(self.mirror)
        if not os.path.isfile(self.mirror_path(card)) and events:
            _write(self.mirror_path(card), "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
        with open(self.mirror_path(card), "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        _sync_dir(self.mirror)
        self._restore(card)
        return event

    def write_file(self, card: str, rel: str, data: bytes) -> str:
        """Plik w katalogu karty w tablicy (wyjście kroku); ścieżka względna do commita."""
        with self.lock:
            target = os.path.join(self.root, self.rel_dir(card), rel)
            if not os.path.realpath(target).startswith(os.path.realpath(os.path.join(self.root, FLOW_DIR)) + os.sep):
                raise ValueError(f"ścieżka poza katalogiem karty: {rel}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.abspath(target) == self.path(card):
                self._invalidate(card)
            with open(target, "wb") as fh:
                fh.write(data)
            rel_path = os.path.relpath(target, self.root)
            self._dirty.add(rel_path)
            return rel_path

    def remove_file(self, card: str, rel: str) -> None:
        """Usuwa plik migawki po zmianie numeru i zgłasza usunięcie do commita tablicy."""
        with self.lock:
            target = os.path.join(self.root, self.rel_dir(card), rel)
            if not os.path.realpath(target).startswith(os.path.realpath(os.path.join(self.root, FLOW_DIR)) + os.sep):
                raise ValueError(f"ścieżka poza katalogiem karty: {rel}")
            if os.path.isfile(target):
                if os.path.abspath(target) == self.path(card):
                    self._invalidate(card)
                os.unlink(target)
                self._dirty.add(os.path.relpath(target, self.root))

    def check(self, card: str) -> str:
        """Plik w tablicy różny od kopii (revert, ręczna zmiana, usunięcie) — odtworzenie z kopii; ostrzeżenie albo ""."""
        with self.lock:
            if not os.path.isfile(self.mirror_path(card)):
                if os.path.isfile(self.path(card)):
                    self._invalidate(card)
                    os.makedirs(self.mirror, mode=0o700, exist_ok=True)
                    _write(self.mirror_path(card), _read(self.path(card)))
                return ""
            if _read(self.path(card)) == _read(self.mirror_path(card)):
                return ""
            self._restore(card)
            return (f"{card}: dziennik karty w repozytorium tablicy był cofnięty albo zmieniony z zewnątrz — "
                    "odtworzony z kopii usługi")

    def has_dirty(self) -> bool:
        """Czy są zmiany dziennika do commita (odczyt bez blokady, wątek główny — K-98 #2)."""
        return bool(self._dirty)

    def take_dirty(self) -> list[str]:
        with self.lock:
            paths, self._dirty = sorted(self._dirty), set()
            return paths

    def _restore(self, card: str) -> None:
        self._invalidate(card)
        text = _read(self.mirror_path(card))
        _mkdir(os.path.dirname(self.path(card)))
        _write(self.path(card), text)
        self._dirty.add(self.rel_path(card))

    def _now(self) -> str:
        ts = self.clock() if self.clock else datetime.now(timezone.utc).timestamp()
        return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="microseconds")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _write(path: str, text: str) -> None:
    # Prefiks `.tide-tmp-` jest w .gitignore tablicy — plik w połowie zapisu nie trafi do commita.
    tmp = os.path.join(os.path.dirname(path), ".tide-tmp-" + os.path.basename(path))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _sync_dir(os.path.dirname(path))


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir(path):
    if os.path.isdir(path):
        return
    parent = os.path.dirname(path)
    _mkdir(parent)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    _sync_dir(parent)


def _parse(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue    # urwana ostatnia linia (awaria w trakcie zapisu) — pomijamy
        if isinstance(event, dict) and event.get("type"):
            out.append(event)
    return out
