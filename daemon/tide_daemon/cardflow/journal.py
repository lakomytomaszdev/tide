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
import itertools
import io
import json
import os
import re
import stat
import threading
import time
from datetime import datetime, timezone
from typing import NamedTuple

from .processidentity import CARD_STEP_KINDS, TICKET_STEP_KINDS, start_error
from .state import FlowState, copy_state, fold

SCHEMA = 1
FLOW_DIR = "flow"
JOURNAL = "journal.jsonl"
CARD_DIR_RE = re.compile(r"^K-[1-9][0-9]{0,8}$")
# K-105 #2: najdłuższy czas, przez który pamięć ścisłego odczytu karty (trafienie, przyrost) zastępuje pełny odczyt
# pliku. Ta sama stała i zegar (`Journal.monotonic`) co okres kontroli pełnej kart w spoczynku (K-105 #3).
FULL_READ_PERIOD = 600.0
# Numery luźnych wersji (`Journal._loose_version`): każda jest różna od każdej innej wersji w procesie.
_LOOSE_VERSIONS = itertools.count(1)


class JournalVersion(NamedTuple):
    """Wersja dziennika karty ze ścisłego odczytu (K-105 #2): stan otwartego pliku (`fstat`), licznik zapisów tego
    procesu i ostatni `seq`. Wartość do porównania — równa wersja oznacza tę samą historię dla odczytów bez skutku;
    bramki przed skutkiem i tak czytają świeżo (`Journal.read_fresh`)."""
    source: str
    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int
    generation: int
    seq: int


class _Remembered:
    """Wpis pamięci ścisłego odczytu karty. Bez tekstu wierszy: ostatni wiersz pamiętany jako położenie i skrót."""
    __slots__ = ("version", "events", "tail", "digest", "full_at")

    def __init__(self, version, events, tail, digest, full_at):
        self.version = version     # JournalVersion odczytanych bajtów (rozmiar = liczba przeczytanych bajtów)
        self.events = events       # zdarzenia tylko do odczytu; wywołujący dostają nową listę tych samych obiektów
        self.tail = tail           # początek ostatniego wiersza w bajtach albo None (przyrost niedozwolony)
        self.digest = digest       # sha256 ostatniego wiersza (b"" przy pustym pliku)
        self.full_at = full_at     # chwila ostatniego pełnego odczytu (`Journal.monotonic`); przyrost jej nie zmienia


class StrictReadInPresentation(RuntimeError):
    """Ścisły czytnik bramki wywołany w odczycie prezentacji (K-98 #2). Celowo nie OSError/ValueError: wołający
    bramki traktują je jako „unknown”, a to jest błąd programu, który ma być widoczny."""


class HistoryChanged(ValueError):
    """Historia karty zmieniła się (dopisanie, ucięcie, podmiana kopii) między odczytem, na którym oparto decyzję,
    a zapisem (K-98 #3). Zapis jest odmówiony bez skutku; jako ValueError oznacza dla bramek stan unknown."""


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
    def __init__(self, board_root: str, mirror: str | None = None, clock=None, monotonic=None):
        self.root = os.path.abspath(board_root)
        self.mirror = mirror or mirror_root(self.root)
        self.clock = clock
        # K-105 #2: zegar okresu pełnego odczytu (wstrzykiwalny w testach); pamięć ścisłego odczytu: karta → wpis.
        self.monotonic = monotonic or time.monotonic
        self._strict_memory: dict[str, _Remembered] = {}
        # K-105 PT-415: wartość wyliczona z historii ścisłej karty (`derive`): karta → (wpis pamięci ścisłego odczytu,
        # jego wersja, wartość). Bez własnej kopii zdarzeń — ważna, dopóki pamięć ścisła ma ten sam wpis i wersję.
        self._derived: dict[str, tuple] = {}
        self.lock = threading.RLock()
        self._dirty: set[str] = set()
        self._cache: dict[str, tuple] = {}     # karta → (rozmiar i mtime kopii, zdarzenia)
        self._states: dict[str, tuple] = {}    # karta → (wersja dziennika, projekcja)
        # K-98 #3: licznik zapisów karty w tym procesie (każde unieważnienie pamięci karty). Faza odczytu historii
        # ticku (`trialhold.HistoryPhase`) po zmianie licznika czyta historię karty ponownie. Zapisów spoza procesu
        # licznik nie widzi — przed skutkiem chroni je świeży pełny odczyt (`unchanged`, `HistoryPhase.unchanged`).
        self._generations: dict[str, int] = {}
        # PT-416: kontrola zgodności (`check`) — karta → para stanów plików (Tablica, kopia) po ostatnim potwierdzeniu
        # zgodności albo odtworzeniu. Służy wyłącznie do pominięcia porównania treści, nigdy jako zgoda na skutek.
        self._check_memory: dict[str, tuple] = {}
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

    def presenting(self) -> bool:
        """Czy bieżący wątek czyta w trybie prezentacji (`presentation()`)."""
        return self._presenting()

    def _guard(self):
        return contextlib.nullcontext() if self._presenting() else self.lock

    def _strict(self):
        """Blokada ścisłych czytników bramek (`cards_complete`, `read_complete`). Nie mają trybu prezentacji: ich
        wynik decyduje o skutku, więc zawsze czytają pod blokadą dziennika. Wywołanie z odczytu prezentacji to błąd
        programu — zgłaszamy go od razu (`StrictReadInPresentation`) zamiast czekać w wątku głównym na mutację albo
        czytać bez blokady."""
        if self._presenting():
            raise StrictReadInPresentation("ścisły odczyt dziennika w odczycie prezentacji")
        return self.lock

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
        with self._strict():
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
            # Silnik lokalnie zmienia pola i kontenery projekcji. Wartości zagnieżdżone
            # (także wytworzone przez fold) tylko czyta; deepcopy jest droższy od fold.
            return copy_state(hit[1])

    def view_version(self, card: str) -> tuple:
        """Wersja dziennika karty do porównań (ten sam klucz, który unieważnia pamięć prezentacji lub workera,
        zależnie od trybu bieżącego wątku). Zmienia się przy każdej zmianie pliku dziennika, także spoza procesu."""
        with self._guard():
            return self._read_version(card)[0]

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
        """Ścisły odczyt historii karty: błąd lub brak historii nie jest pustą historią.

        Bez tolerancyjnego parsera i bez naprawiania plików. Istniejąca kopia hostowa jest autorytatywna także wtedy,
        gdy nie da się jej odczytać. OSError/ValueError oznaczają dla wywołującego stan unknown.

        K-105 #2: wynik jest pamiętany dla wersji dziennika (`read_complete_version`). Zdarzenia są tylko do odczytu
        (wspólne z pamięcią) — wywołujący dostaje nową listę tych samych obiektów. Do odczytów bez skutku; porównania
        przed zapisem i zapisy bramek czytają świeżo (`read_fresh`)."""
        return self.read_complete_version(card)[1]

    def version(self, card: str) -> JournalVersion:
        """Wersja dziennika karty ze ścisłego odczytu (K-105 #2); błąd odczytu propaguje unknown."""
        return self.read_complete_version(card)[0]

    def read_fresh(self, card: str) -> list[dict]:
        """Świeży pełny ścisły odczyt dla bramek przed skutkiem (K-105 #2): ta sama reguła co `read_complete`, ale
        z pominięciem pamięci — ani jej nie czyta, ani nie zapisuje. Porównania przed zapisem (`unchanged`) i zapisy
        (`append`, `append_complete`) czytają historię tylko tak."""
        with self._strict():
            return self._read_strict(card)[0]

    def read_complete_version(self, card: str) -> tuple[JournalVersion, list[dict]]:
        """Ścisły odczyt z pamięcią według wersji dziennika (K-105 #2).

        - trafienie: to samo źródło i i-węzeł, ten sam rozmiar, `st_mtime_ns`, `st_ctime_ns` (z `fstat` otwartego
          deskryptora) i `generation` — bez czytania pliku, nowa lista tych samych zdarzeń,
        - przyrost: ten sam i-węzeł, większy rozmiar i niezmieniony ostatni zapamiętany wiersz (skrót bajtów w tym
          samym miejscu) — doczytuje tylko nowe bajty i waliduje je tą samą regułą co pełny odczyt,
        - każda inna zmiana, zapis tego procesu (`generation`) albo upływ `FULL_READ_PERIOD` od ostatniego pełnego
          odczytu wpisu — pełny odczyt (`_read_strict`).

        Ograniczenie przyrostu: edycja wcześniejszych wierszy w miejscu połączona z dopisaniem nie jest wykrywana do
        najbliższego pełnego odczytu; bramki przed skutkiem czytają świeżo, więc zapis oparty na takiej historii jest
        odmówiony (`HistoryChanged`). Błąd odczytu (pełnego albo przyrostu) usuwa wpis — nie zostaje jako historia."""
        with self._strict():
            entry = self._strict_memory.pop(card, None)
            if entry is not None and (entry.version.generation != self.generation(card)
                                      or self.monotonic() - entry.full_at >= FULL_READ_PERIOD):
                entry = None
            if entry is not None:
                done = self._remembered(card, entry)
                if done is not None:
                    return done
            events, entry = self._read_strict(card)
            if entry is not None:
                self._strict_memory[card] = entry
                return entry.version, list(entry.events)
            return self._loose_version(card, events), events

    def derive(self, card: str, compute):
        """Ścisły odczyt (`read_complete`) i wartość `compute(zdarzenia)` pamiętana dla wersji dziennika (K-105 PT-415).

        Klucz to wpis pamięci ścisłego odczytu i jego `JournalVersion` (z `st_ctime_ns` i `generation`): trafienie
        wymaga, by odczyt zwrócił zdarzenia tego samego wpisu (te same obiekty), więc nowa wersja, przyrost, okresowy
        pełny odczyt, własny zapis albo luźna wersja (plik zmieniany w trakcie czytania) dają nowe wyliczenie. Błąd
        odczytu albo `compute` usuwa wpis karty i propaguje — nie zostaje jako wartość ani jako „brak zmian”.
        Zwraca (zdarzenia, wartość); wartość jest wspólna — wołający jej nie zmienia (kopiuje u siebie)."""
        with self._strict():
            hit = self._derived.pop(card, None)
            events = self.read_complete(card)
            entry = self._strict_memory.get(card)
            same = (entry is not None and len(events) == len(entry.events)
                    and (not events or events[-1] is entry.events[-1]))
            if hit is not None and same and hit[0] is entry and hit[1] == entry.version:
                self._derived[card] = hit
                return events, hit[2]
            value = compute(events)
            if same:
                self._derived[card] = (entry, entry.version, value)
            return events, value

    def forget_except(self, cards) -> None:
        """Usuwa wpisy pamięci ścisłego odczytu, kontroli zgodności (PT-416) i wartości wyliczonych z pamięci ścisłej
        (`derive`, PT-415) kart spoza `cards` (K-105 #2: początek ticku, karta usunięta)."""
        keep = set(cards)
        with self.lock:
            for memory in (self._strict_memory, self._check_memory, self._derived):
                for card in [c for c in memory if c not in keep]:
                    del memory[card]

    def _strict_open(self, card: str):
        source = self.mirror_path(card)
        try:
            os.lstat(source)
        except FileNotFoundError:
            source = self.path(card)
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError("dziennik nie jest zwykłym plikiem")
        except BaseException:
            os.close(fd)
            raise
        return source, fd, st

    def _read_strict(self, card: str):
        """Pełny ścisły odczyt historii karty — granica liczenia pełnych odczytów (K-105 #2). Zwraca zdarzenia i wpis
        pamięci albo None, gdy plik zmienił się w trakcie czytania (wynik jest poprawny, ale bez stałej wersji)."""
        generation = self.generation(card)
        source, fd, before = self._strict_open(card)
        try:
            data = _pread_all(fd, 0)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        events = _parse_strict(data, card, 0)
        if not _same_state(before, after) or len(data) != after.st_size:
            return events, None
        tail, digest = _last_line(data, 0)
        return events, _Remembered(_version(source, after, generation, events), events, tail, digest,
                                   self.monotonic())

    def _remembered(self, card: str, entry: _Remembered):
        """Trafienie albo przyrost dla wpisu pamięci; None, gdy potrzebny jest pełny odczyt. Błąd przyrostu
        propaguje (wpis jest już usunięty z pamięci przez wywołującego)."""
        old = entry.version
        source, fd, st = self._strict_open(card)
        try:
            if source != old.source or st.st_dev != old.dev or st.st_ino != old.ino:
                return None
            if (st.st_size == old.size and st.st_mtime_ns == old.mtime_ns
                    and st.st_ctime_ns == old.ctime_ns):
                self._strict_memory[card] = entry
                return old, list(entry.events)
            if entry.tail is None or st.st_size <= old.size:
                return None
            if hashlib.sha256(os.pread(fd, old.size - entry.tail, entry.tail)).digest() != entry.digest:
                return None
            data = _pread_all(fd, old.size)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if not data or b"\r" in data:
            return None          # wyścig z ucięciem albo inne końce wierszy: rozstrzyga pełny odczyt
        added = _parse_strict(data, card, len(entry.events))
        if not _same_state(st, after) or old.size + len(data) != after.st_size:
            events = entry.events + added        # poprawny wynik bez stałej wersji: bez pamięci
            return self._loose_version(card, events), events
        tail, digest = _last_line(data, old.size)
        entry.events.extend(added)       # wywołujący mają własne listy; pamięć dokłada tylko nowe zdarzenia
        entry.version = _version(source, after, old.generation, entry.events)
        entry.tail, entry.digest = tail, digest
        self._strict_memory[card] = entry
        return entry.version, list(entry.events)

    def _loose_version(self, card, events) -> JournalVersion:
        """Wersja wyniku czytanego w trakcie zmiany pliku: nie pasuje do żadnego stanu pliku (rozmiar -1) ani do
        żadnej innej wersji — każda luźna wersja ma własny numer (`ctime_ns` ujemny, niepowtarzalny w procesie),
        bo dwa takie odczyty mogą mieć ten sam `seq` i `generation` przy różnej treści."""
        seq = events[-1]["seq"] if events else 0
        return JournalVersion("", 0, 0, -1, 0, -next(_LOOSE_VERSIONS), self.generation(card), seq)

    # --- zapis -----------------------------------------------------------------------------------

    def generation(self, card: str) -> int:
        """Numer wersji historii karty według zapisów tego procesu (K-98 #3); zmienia się przy każdym zapisie."""
        return self._generations.get(card, 0)

    def _invalidate(self, card: str) -> None:
        self._generations[card] = self._generations.get(card, 0) + 1
        self._strict_memory.pop(card, None)
        self._check_memory.pop(card, None)
        self._derived.pop(card, None)
        self._cache.pop(card, None)
        self._states.pop(card, None)

    def append(self, card: str, kind: str, /, **data) -> dict:
        with self.lock:
            # Tolerancyjny odczyt służy prezentacji, nigdy odbudowie kopii przy
            # zapisie. Inaczej dowolne późniejsze zdarzenie usuwa dowód unknown.
            exists = os.path.lexists(self.mirror_path(card)) or os.path.lexists(self.path(card))
            events = self.read_fresh(card) if exists else []
            return self._append(card, kind, events, data)

    def append_complete(self, card: str, kind: str, /, **data) -> dict:
        """Zapis bramki tylko na pełnej historii (świeży ścisły odczyt), bez tolerancyjnego odtworzenia."""
        with self.lock:
            return self._append(card, kind, self.read_fresh(card), data)

    @contextlib.contextmanager
    def unchanged(self, card: str, basis: list):
        """Zapis decyzji podjętej na wcześniejszym odczycie `basis` (K-98 #3): blok trzyma blokadę dziennika, a na
        wejściu świeży pełny ścisły odczyt musi być równy `basis`. Dopisanie, ucięcie albo podmiana dziennika lub
        kopii po odczycie daje `HistoryChanged` przed blokiem — bez zapisu. Zapisy w bloku (`append_complete`)
        czytają historię ponownie pod tą samą blokadą. Porównanie zawsze z pominięciem pamięci (`read_fresh`)."""
        with self.lock:
            if self.read_fresh(card) != list(basis):
                raise HistoryChanged(f"{card}: historia zmieniła się po odczycie — zapis odmówiony")
            yield

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
        """Plik w tablicy różny od kopii (revert, ręczna zmiana, usunięcie) — odtworzenie z kopii; ostrzeżenie albo "".

        PT-416: para stanów obu plików (`st_dev`, `st_ino`, `st_size`, `st_mtime_ns`, `st_ctime_ns` — reguła klucza
        wersji z K-105 #2) jest pamiętana po potwierdzonej zgodności albo po udanym odtworzeniu. Ta sama para w kolejnym
        wywołaniu pomija porównanie treści (sam `stat`) i zwraca "" bez żadnego skutku. Brak kopii usługi: odbudowa
        kopii z Tablicy jak dotąd (bez porównania i bez pary). W pozostałych przypadkach (inna para, błąd `stat`, brak
        pliku w Tablicy): pełne porównanie treści (`_check_read` obu plików) w tym wywołaniu — odtworzenie wyłącznie
        po nim, nigdy na podstawie samej różnicy stanu ani pamięci. Wpis jest zdejmowany na wejściu i wraca tylko po
        udanym porównaniu bez błędu (stan obu plików bez zmian w trakcie) albo po odtworzeniu potwierdzonym
        obramowanym świeżym odczytem Tablicy (`_confirm_restored`); zapis tego procesu (`_invalidate`)
        i `forget_except` go usuwają.

        Ograniczenie (jak klucz wersji K-105 #2): zmiana w miejscu bez zmiany rozmiaru wykonana tuż po porównaniu
        w obrębie jednego kwantu zegara znaczników czasu jądra nie zmienia żadnego z pięciu pól."""
        with self.lock:
            board, mirror = self.path(card), self.mirror_path(card)
            remembered = self._check_memory.pop(card, None)
            before = _pair(board, mirror)
            if remembered is not None and before == remembered:
                self._check_memory[card] = remembered
                return ""
            if not os.path.isfile(mirror):
                if os.path.isfile(board):
                    self._invalidate(card)
                    os.makedirs(self.mirror, mode=0o700, exist_ok=True)
                    _write(mirror, _read(board))
                return ""
            board_text, board_ok = self._check_read(board)
            mirror_text, mirror_ok = self._check_read(mirror)
            readable = before is not None and board_ok and mirror_ok
            if board_text == mirror_text:
                if readable and _pair(board, mirror) == before:
                    self._check_memory[card] = before
                return ""
            self._restore(card)
            if readable:
                self._confirm_restored(card, before, mirror_text)
            return (f"{card}: dziennik karty w repozytorium tablicy był cofnięty albo zmieniony z zewnątrz — "
                    "odtworzony z kopii usługi")

    def _confirm_restored(self, card: str, before, mirror_text: str) -> None:
        """Para po odtworzeniu (PT-416, review M1): zapamiętana tylko po obramowanym potwierdzeniu w tym samym
        wywołaniu — `stat` obu plików, świeży odczyt treści Tablicy równy przeczytanej kopii, ponowny `stat` równy
        pierwszemu, kopia w stanie sprzed porównania. Zmiana Tablicy z zewnątrz po `os.replace` w `_restore` (przed
        `stat`) nie trafia do pamięci jako zgodna: następne wywołanie porównuje pełną treść. Samo potwierdzenie nie
        wywołuje żadnego zapisu."""
        board, mirror = self.path(card), self.mirror_path(card)
        first = _pair(board, mirror)
        if first is None or first[1] != before[1]:
            return
        text, ok = self._check_read(board)
        if ok and text == mirror_text and _pair(board, mirror) == first:
            self._check_memory[card] = first

    def _check_read(self, path: str) -> tuple[str, bool]:
        """Odczyt treści pliku dziennika w kontroli zgodności (PT-416: granica liczenia odczytów). Błąd odczytu daje
        pustą treść jak dotąd (`_read`) i `False` — taki wynik nigdy nie trafia do pamięci kontroli."""
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read(), True
        except OSError:
            return "", False

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


def _pread_all(fd: int, offset: int) -> bytes:
    chunks = []
    while True:
        chunk = os.pread(fd, 1 << 20, offset)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        offset += len(chunk)


def _parse_strict(data: bytes, card: str, before: int) -> list[dict]:
    """Ścisła reguła odczytu historii (pełny odczyt i przyrost): pełne wiersze UTF-8 (końce wierszy jak w trybie
    tekstowym), schemat, kolejny `seq` od `before + 1`, ta sama karta, znana tożsamość startu procesu."""
    events = []
    for line in io.StringIO(data.decode("utf-8"), newline=None):
        if not line.endswith("\n"):
            raise ValueError("niepełny zapis zdarzenia")
        event = json.loads(line)
        if (not isinstance(event, dict) or type(event.get("v")) is not int
                or event["v"] != SCHEMA or type(event.get("seq")) is not int
                or event["seq"] != before + len(events) + 1 or event.get("card") != card
                or not isinstance(event.get("type"), str) or not event["type"]):
            raise ValueError("niepełna lub nieznana historia zdarzeń")
        _validate_process_identity(event)
        events.append(event)
    return events


def _same_state(a, b) -> bool:
    return (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns, a.st_ctime_ns) == \
        (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns, b.st_ctime_ns)


def _file_state(path: str) -> tuple:
    st = os.stat(path)
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _pair(board: str, mirror: str):
    """Para stanów plików kontroli zgodności (PT-416): (Tablica, kopia) albo None przy błędzie `stat`/braku pliku."""
    try:
        return _file_state(board), _file_state(mirror)
    except OSError:
        return None


def _version(source, st, generation, events) -> JournalVersion:
    return JournalVersion(source, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, generation,
                          events[-1]["seq"] if events else 0)


def _last_line(data: bytes, offset: int):
    """Położenie (od `offset`) i skrót ostatniego wiersza przeczytanych bajtów; (None, None), gdy przyrost nie jest
    bezpieczny (znak \\r: końce wierszy trybu tekstowego mogą łączyć się przez granicę przyrostu)."""
    if b"\r" in data:
        return None, None
    start = data.rfind(b"\n", 0, len(data) - 1) + 1 if data else 0
    return offset + start, hashlib.sha256(data[start:]).digest()


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
