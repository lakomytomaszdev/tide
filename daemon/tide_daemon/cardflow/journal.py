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

import hashlib
import json
import os
import re
import stat
import threading
from datetime import datetime, timezone

SCHEMA = 1
FLOW_DIR = "flow"
JOURNAL = "journal.jsonl"
CARD_DIR_RE = re.compile(r"^K-[1-9][0-9]{0,8}$")


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
        with self.lock:
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
        source = self.mirror_path(card) if os.path.isfile(self.mirror_path(card)) else self.path(card)
        try:
            st = os.stat(source)
        except OSError:
            return []
        key = (source, st.st_size, st.st_mtime_ns)
        hit = self._cache.get(card)
        if hit is not None and hit[0] == key:
            return list(hit[1])
        events = _parse(_read(source))
        self._cache[card] = (key, events)
        return list(events)

    def read_complete(self, card: str) -> list[dict]:
        """Odczyt dla bramki: błąd lub brak historii nie jest pustą historią.

        Bez tolerancyjnego parsera/cache i bez naprawiania plików. Istniejąca
        kopia hostowa jest autorytatywna także wtedy, gdy nie da się jej odczytać.
        OSError/ValueError oznaczają dla wywołującego stan unknown.
        """
        with self.lock:
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
                    events.append(event)
                return events

    # --- zapis -----------------------------------------------------------------------------------

    def append(self, card: str, kind: str, /, **data) -> dict:
        with self.lock:
            events = self.read(card)
            event = {"v": SCHEMA, "seq": len(events) + 1, "ts": self._now(), "card": card, "type": kind, **data}
            line = json.dumps(event, ensure_ascii=False) + "\n"
            os.makedirs(self.mirror, mode=0o700, exist_ok=True)
            if not os.path.isfile(self.mirror_path(card)) and events:
                _write(self.mirror_path(card), "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
            with open(self.mirror_path(card), "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self._restore(card)
            return event

    def write_file(self, card: str, rel: str, data: bytes) -> str:
        """Plik w katalogu karty w tablicy (wyjście kroku); ścieżka względna do commita."""
        with self.lock:
            target = os.path.join(self.root, self.rel_dir(card), rel)
            if not os.path.realpath(target).startswith(os.path.realpath(os.path.join(self.root, FLOW_DIR)) + os.sep):
                raise ValueError(f"ścieżka poza katalogiem karty: {rel}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
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
                os.unlink(target)
                self._dirty.add(os.path.relpath(target, self.root))

    def check(self, card: str) -> str:
        """Plik w tablicy różny od kopii (revert, ręczna zmiana, usunięcie) — odtworzenie z kopii; ostrzeżenie albo ""."""
        with self.lock:
            if not os.path.isfile(self.mirror_path(card)):
                if os.path.isfile(self.path(card)):
                    os.makedirs(self.mirror, mode=0o700, exist_ok=True)
                    _write(self.mirror_path(card), _read(self.path(card)))
                return ""
            if _read(self.path(card)) == _read(self.mirror_path(card)):
                return ""
            self._restore(card)
            return (f"{card}: dziennik karty w repozytorium tablicy był cofnięty albo zmieniony z zewnątrz — "
                    "odtworzony z kopii usługi")

    def take_dirty(self) -> list[str]:
        with self.lock:
            paths, self._dirty = sorted(self._dirty), set()
            return paths

    def _restore(self, card: str) -> None:
        text = _read(self.mirror_path(card))
        os.makedirs(os.path.dirname(self.path(card)), exist_ok=True)
        _write(self.path(card), text)
        self._dirty.add(self.rel_path(card))

    def _now(self) -> str:
        ts = self.clock() if self.clock else datetime.now(timezone.utc).timestamp()
        return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


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
