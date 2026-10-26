"""Bezczynność użytkownika prosto od kompozytora Wayland (KWin), bez dodatkowych bibliotek.

Mały klient protokołu Wayland w czystym Pythonie, który robi tylko jedno: prosi kompozytor
o powiadomienie, gdy przez `timeout_ms` nie było żadnego wejścia (klawiatura, mysz, dotyk),
i o drugie, gdy wejście wróci. Odliczanie prowadzi kompozytor, więc klient nic nie odpytuje
— w spoczynku gniazdo milczy, a proces nie zużywa procesora.

Kolejność protokołów (KWin 6.8 ma wszystkie trzy):
- `ext_idle_notifier_v1` w wersji 2: `get_input_idle_notification` — liczy się wyłącznie
  wejście użytkownika, blokady bezczynności (np. odtwarzany film) nie przeszkadzają,
- `ext_idle_notifier_v1` w wersji 1: `get_idle_notification` — to samo, ale blokady
  bezczynności wstrzymują powiadomienie,
- `org_kde_kwin_idle` (starszy protokół KDE) — zapasowo.

Python nie ma powiązań z libwayland (pywayland nie jest w systemie), a potrzebne wiadomości
są proste: rejestr, wl_seat i dwa zdarzenia (idled/resumed). Format: nagłówek z numerem
obiektu i słowem (rozmiar << 16 | kod operacji), argumenty 32-bitowe w kolejności bajtów
maszyny, napisy z długością (z zerem na końcu) wyrównane do 4 bajtów.
"""
from __future__ import annotations

import os
import select
import socket
import struct
import time

WORD = struct.Struct("=I")
HEADER = struct.Struct("=II")

DISPLAY_ID = 1
# wl_display
GET_REGISTRY, SYNC = 1, 0
EV_ERROR, EV_DELETE_ID = 0, 1
# wl_registry
BIND = 0
EV_GLOBAL, EV_GLOBAL_REMOVE = 0, 1
# wl_callback
EV_DONE = 0
# ext_idle_notifier_v1: destroy(0), get_idle_notification(1), get_input_idle_notification(2, od wersji 2)
EXT_NOTIFIER = "ext_idle_notifier_v1"
EXT_GET_IDLE, EXT_GET_INPUT_IDLE = 1, 2
# org_kde_kwin_idle: get_idle_timeout(0) — argumenty w innej kolejności (seat przed czasem)
KDE_IDLE = "org_kde_kwin_idle"
KDE_GET_IDLE_TIMEOUT = 0
# Zdarzenia powiadomienia mają te same kody w obu protokołach.
EV_IDLED, EV_RESUMED = 0, 1

SEAT = "wl_seat"

# Nazwy źródeł (do diagnostyki i opisu w D-Bus).
SOURCE_EXT_INPUT = "ext-idle-notify-v1 (wejście)"
SOURCE_EXT = "ext-idle-notify-v1"
SOURCE_KDE = "org_kde_kwin_idle"


class WaylandError(Exception):
    """Brak kompozytora, zerwane połączenie albo błąd protokołu."""


class Unsupported(WaylandError):
    """Kompozytor działa, ale nie ma protokołu bezczynności albo wl_seat."""


def socket_path(env=None) -> str | None:
    """Gniazdo kompozytora jak w libwayland: $WAYLAND_DISPLAY (domyślnie wayland-0) w $XDG_RUNTIME_DIR.

    Ścieżka bezwzględna w WAYLAND_DISPLAY jest brana wprost (libwayland ≥ 1.15).
    """
    env = os.environ if env is None else env
    display = env.get("WAYLAND_DISPLAY") or "wayland-0"
    if display.startswith("/"):
        return display
    runtime = env.get("XDG_RUNTIME_DIR")
    return os.path.join(runtime, display) if runtime else None


def message(obj: int, opcode: int, payload: bytes = b"") -> bytes:
    return HEADER.pack(obj, (HEADER.size + len(payload)) << 16 | opcode) + payload


def uint(value: int) -> bytes:
    return WORD.pack(value & 0xFFFFFFFF)


def string(text: str) -> bytes:
    data = text.encode() + b"\0"
    return WORD.pack(len(data)) + data + b"\0" * (-len(data) % 4)


def read_string(payload: bytes, offset: int) -> tuple[str, int]:
    """Napis z argumentów zdarzenia: (tekst, przesunięcie za napisem)."""
    (length,) = WORD.unpack_from(payload, offset)
    start = offset + WORD.size
    text = payload[start:start + max(length - 1, 0)].decode(errors="replace")
    return text, start + length + (-length % 4)


def split_messages(buffer: bytearray) -> list[tuple[int, int, bytes]]:
    """Wyjmuje z bufora pełne wiadomości (obiekt, kod, argumenty); niepełna reszta zostaje w buforze."""
    out = []
    while len(buffer) >= HEADER.size:
        obj, word = HEADER.unpack_from(buffer)
        size, opcode = word >> 16, word & 0xFFFF
        if size < HEADER.size:
            raise WaylandError(f"niepoprawny rozmiar wiadomości: {size}")
        if len(buffer) < size:
            break
        out.append((obj, opcode, bytes(buffer[HEADER.size:size])))
        del buffer[:size]
    return out


class IdleClient:
    """Jedno połączenie z kompozytorem i jedno powiadomienie o bezczynności.

    on_idle() — od `timeout_ms` nie było wejścia, on_resume() — wejście wróciło.
    Użycie: connect(), potem dispatch() za każdym razem, gdy gniazdo (fileno()) ma dane.
    """

    def __init__(self, timeout_ms: int, on_idle, on_resume, path: str | None = None):
        self.timeout_ms = max(1, int(timeout_ms))
        self.on_idle = on_idle
        self.on_resume = on_resume
        self.path = path
        self.source = ""
        self.sock: socket.socket | None = None
        self._buffer = bytearray()
        self._next_id = 2
        self._objects: dict[int, str] = {DISPLAY_ID: "wl_display"}
        self._globals: dict[str, tuple[int, int]] = {}  # interfejs -> (nazwa w rejestrze, wersja)
        self._done: set[int] = set()
        self._notification = 0

    # --- połączenie ----------------------------------------------------------------------------

    def connect(self, handshake_timeout: float = 5.0) -> None:
        path = self.path or socket_path()
        if not path:
            raise WaylandError("brak XDG_RUNTIME_DIR — nie wiadomo, gdzie jest kompozytor")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        try:
            sock.connect(path)
        except OSError as exc:
            sock.close()
            raise WaylandError(f"brak kompozytora pod {path}: {exc.strerror or exc}") from exc
        self.sock = sock
        try:
            registry = self._new("wl_registry")
            self._send(message(DISPLAY_ID, GET_REGISTRY, uint(registry)))
            self._roundtrip(handshake_timeout)

            if SEAT not in self._globals:
                raise Unsupported("kompozytor nie ma wl_seat")
            seat = self._bind(registry, SEAT, 1)
            if EXT_NOTIFIER in self._globals:
                version = min(self._globals[EXT_NOTIFIER][1], 2)
                notifier = self._bind(registry, EXT_NOTIFIER, version)
                self._notification = self._new("ext_idle_notification_v1")
                opcode = EXT_GET_INPUT_IDLE if version >= 2 else EXT_GET_IDLE
                self.source = SOURCE_EXT_INPUT if version >= 2 else SOURCE_EXT
                self._send(message(notifier, opcode,
                                   uint(self._notification) + uint(self.timeout_ms) + uint(seat)))
            elif KDE_IDLE in self._globals:
                idle = self._bind(registry, KDE_IDLE, 1)
                self._notification = self._new("org_kde_kwin_idle_timeout")
                self.source = SOURCE_KDE
                self._send(message(idle, KDE_GET_IDLE_TIMEOUT,
                                   uint(self._notification) + uint(seat) + uint(self.timeout_ms)))
            else:
                raise Unsupported("kompozytor nie ma protokołu bezczynności (ext_idle_notifier_v1)")
            # Drugi obieg: błąd protokołu przy tworzeniu powiadomienia wyjdzie tutaj, a nie później.
            self._roundtrip(handshake_timeout)
        except BaseException:
            self.close()
            raise
        sock.setblocking(False)

    def fileno(self) -> int:
        return self.sock.fileno() if self.sock else -1

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def dispatch(self) -> None:
        """Czyta wszystko, co czeka w gnieździe, i obsługuje zdarzenia. WaylandError = koniec połączenia."""
        if self.sock is None:
            raise WaylandError("brak połączenia")
        while True:
            try:
                chunk = self.sock.recv(4096)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as exc:
                raise WaylandError(f"połączenie z kompozytorem zerwane: {exc}") from exc
            if not chunk:
                raise WaylandError("kompozytor zamknął połączenie")
            self._buffer += chunk
        self._handle_all()

    # --- wnętrze -------------------------------------------------------------------------------

    def _new(self, interface: str) -> int:
        obj = self._next_id
        self._next_id += 1
        self._objects[obj] = interface
        return obj

    def _send(self, data: bytes) -> None:
        try:
            self.sock.sendall(data)
        except OSError as exc:
            raise WaylandError(f"nie udało się wysłać do kompozytora: {exc}") from exc

    def _bind(self, registry: int, interface: str, version: int) -> int:
        name, _ = self._globals[interface]
        obj = self._new(interface)
        self._send(message(registry, BIND, uint(name) + string(interface) + uint(version) + uint(obj)))
        return obj

    def _roundtrip(self, timeout: float) -> None:
        """wl_display.sync i czekanie na wl_callback.done — wszystkie wcześniejsze zdarzenia są już obsłużone."""
        callback = self._new("wl_callback")
        self._send(message(DISPLAY_ID, SYNC, uint(callback)))
        deadline = time.monotonic() + timeout
        while callback not in self._done:
            left = deadline - time.monotonic()
            if left <= 0:
                raise WaylandError("kompozytor nie odpowiada")
            ready, _, _ = select.select([self.sock], [], [], left)
            if not ready:
                continue
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WaylandError("kompozytor zamknął połączenie")
            self._buffer += chunk
            self._handle_all()
        self._done.discard(callback)

    def _handle_all(self) -> None:
        for obj, opcode, payload in split_messages(self._buffer):
            self._handle(obj, opcode, payload)

    def _handle(self, obj: int, opcode: int, payload: bytes) -> None:
        interface = self._objects.get(obj)
        if obj == DISPLAY_ID:
            if opcode == EV_ERROR:
                failed, code = WORD.unpack_from(payload, 0)[0], WORD.unpack_from(payload, 4)[0]
                text, _ = read_string(payload, 8)
                raise WaylandError(f"błąd protokołu Wayland (obiekt {failed}, kod {code}): {text}")
            if opcode == EV_DELETE_ID:
                (deleted,) = WORD.unpack_from(payload, 0)
                self._objects.pop(deleted, None)
            return
        if interface == "wl_registry":
            if opcode == EV_GLOBAL:
                (name,) = WORD.unpack_from(payload, 0)
                iface, offset = read_string(payload, 4)
                (version,) = WORD.unpack_from(payload, offset)
                # Pierwszy wl_seat i pierwszy egzemplarz protokołu — tak jak robi to KIdleTime.
                self._globals.setdefault(iface, (name, version))
            elif opcode == EV_GLOBAL_REMOVE:
                (name,) = WORD.unpack_from(payload, 0)
                for iface, (gname, _) in list(self._globals.items()):
                    if gname == name:
                        del self._globals[iface]
            return
        if interface == "wl_callback" and opcode == EV_DONE:
            self._done.add(obj)
            return
        if obj == self._notification and self._notification:
            if opcode == EV_IDLED:
                self.on_idle()
            elif opcode == EV_RESUMED:
                self.on_resume()
        # Pozostałe zdarzenia (np. możliwości wl_seat) nie są potrzebne.
