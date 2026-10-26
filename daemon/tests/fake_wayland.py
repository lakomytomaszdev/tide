"""Atrapa kompozytora Wayland z protokołami bezczynności, sterowana zamrożonym zegarem.

Prawdziwe gniazdo uniksowe i prawdziwy format wiadomości Wayland, ale tylko to, czego używa
tide_daemon.wayland_idle: rejestr (wl_seat, ext_idle_notifier_v1, org_kde_kwin_idle),
wl_display.sync, bind i powiadomienia o bezczynności. Czas płynie wyłącznie przez advance(),
a wejście użytkownika to input() — tak jak w KWinie: powiadomienie „idled” przychodzi po
progu bez wejścia, „resumed” przy pierwszym wejściu po nim.
"""
from __future__ import annotations

import os
import select
import socket
import struct
import threading
import time

WORD = struct.Struct("=I")
HEADER = struct.Struct("=II")


def _msg(obj: int, opcode: int, payload: bytes = b"") -> bytes:
    return HEADER.pack(obj, (HEADER.size + len(payload)) << 16 | opcode) + payload


def _string(text: str) -> bytes:
    data = text.encode() + b"\0"
    return WORD.pack(len(data)) + data + b"\0" * (-len(data) % 4)


def _read_string(payload: bytes, offset: int) -> tuple[str, int]:
    (length,) = WORD.unpack_from(payload, offset)
    start = offset + 4
    return payload[start:start + length - 1].decode(), start + length + (-length % 4)


class _Client:
    def __init__(self, conn: socket.socket):
        self.conn = conn
        self.buffer = bytearray()
        self.objects: dict[int, str] = {1: "wl_display"}


class FakeCompositor:
    def __init__(self, path: str, ext_version: int | None = 2, kde: bool = True, seat: bool = True,
                 fail_notification: bool = False):
        self.path = path
        self.globals = []
        if seat:
            self.globals.append((1, "wl_seat", 7))
        if ext_version:
            self.globals.append((2, "ext_idle_notifier_v1", ext_version))
        if kde:
            self.globals.append((3, "org_kde_kwin_idle", 1))
        self.fail_notification = fail_notification
        self.now = 0.0
        self.last_input = 0.0
        self.notifications: list[dict] = []  # {client, id, kind, timeout_ms, idled}
        self.connections = 0
        self._clients: list[_Client] = []
        self._lock = threading.RLock()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(path)
        self._server.listen(4)
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # --- sterowanie z testu ---------------------------------------------------------------------

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds
            for n in self.notifications:
                if not n["idled"] and (self.now - self.last_input) * 1000 >= n["timeout_ms"]:
                    n["idled"] = True
                    self._send(n["client"], _msg(n["id"], 0))

    def input(self) -> None:
        with self._lock:
            self.last_input = self.now
            for n in self.notifications:
                if n["idled"]:
                    n["idled"] = False
                    self._send(n["client"], _msg(n["id"], 1))

    def drop_clients(self) -> None:
        """Zrywa połączenia (jak restart KWina); gniazdo nasłuchuje dalej."""
        with self._lock:
            for client in self._clients:
                client.conn.close()
            self._clients.clear()
            self.notifications.clear()

    def wait_for_notifications(self, count: int = 1, timeout: float = 5.0) -> list[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(self.notifications) >= count:
                    return list(self.notifications)
            time.sleep(0.01)
        raise AssertionError(f"klient nie poprosił o {count} powiadomienie(a) o bezczynności")

    def stop(self) -> None:
        self._stop = True
        self._thread.join(2)
        with self._lock:
            for client in self._clients:
                client.conn.close()
        self._server.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    # --- serwer ---------------------------------------------------------------------------------

    def _send(self, client: _Client, data: bytes) -> None:
        try:
            client.conn.sendall(data)
        except OSError:
            pass

    def _loop(self) -> None:
        while not self._stop:
            with self._lock:
                conns = [c.conn for c in self._clients if c.conn.fileno() >= 0]
            try:
                ready, _, _ = select.select([self._server, *conns], [], [], 0.05)
            except (OSError, ValueError):
                continue
            for sock in ready:
                if sock is self._server:
                    conn, _ = self._server.accept()
                    with self._lock:
                        self._clients.append(_Client(conn))
                        self.connections += 1
                    continue
                with self._lock:
                    client = next((c for c in self._clients if c.conn is sock), None)
                    if client is None:
                        continue
                    try:
                        data = sock.recv(4096)
                    except OSError:
                        data = b""
                    if not data:
                        self._clients.remove(client)
                        self.notifications = [n for n in self.notifications if n["client"] is not client]
                        sock.close()
                        continue
                    client.buffer += data
                    self._handle(client)

    def _handle(self, client: _Client) -> None:
        buf = client.buffer
        while len(buf) >= 8:
            obj, word = HEADER.unpack_from(buf)
            size, opcode = word >> 16, word & 0xFFFF
            if len(buf) < size:
                return
            payload = bytes(buf[8:size])
            del buf[:size]
            self._request(client, obj, opcode, payload)

    def _request(self, client: _Client, obj: int, opcode: int, payload: bytes) -> None:
        iface = client.objects.get(obj)
        if iface == "wl_display" and opcode == 1:  # get_registry
            (registry,) = WORD.unpack_from(payload)
            client.objects[registry] = "wl_registry"
            for name, interface, version in self.globals:
                self._send(client, _msg(registry, 0, WORD.pack(name) + _string(interface) + WORD.pack(version)))
        elif iface == "wl_display" and opcode == 0:  # sync
            (callback,) = WORD.unpack_from(payload)
            self._send(client, _msg(callback, 0, WORD.pack(0)))
            self._send(client, _msg(1, 1, WORD.pack(callback)))  # delete_id
        elif iface == "wl_registry" and opcode == 0:  # bind
            (_name,) = WORD.unpack_from(payload)
            interface, offset = _read_string(payload, 4)
            version, new_id = WORD.unpack_from(payload, offset)[0], WORD.unpack_from(payload, offset + 4)[0]
            client.objects[new_id] = interface
            client.versions = getattr(client, "versions", {})
            client.versions[interface] = version
            if interface == "wl_seat":
                self._send(client, _msg(new_id, 0, WORD.pack(3)))  # capabilities: wskaźnik i klawiatura
        elif iface == "ext_idle_notifier_v1" and opcode in (1, 2):
            new_id, timeout, _seat = struct.unpack_from("=III", payload)
            self._notification(client, new_id, "ext-input" if opcode == 2 else "ext", timeout)
        elif iface == "org_kde_kwin_idle" and opcode == 0:
            new_id, _seat, timeout = struct.unpack_from("=III", payload)
            self._notification(client, new_id, "kde", timeout)

    def _notification(self, client: _Client, new_id: int, kind: str, timeout: int) -> None:
        if self.fail_notification:
            message = _string("odmowa")
            self._send(client, _msg(1, 0, WORD.pack(new_id) + WORD.pack(1) + message))
            return
        client.objects[new_id] = kind
        # Jak w KWinie: odliczanie od chwili utworzenia powiadomienia.
        self.last_input = self.now
        self.notifications.append({"client": client, "id": new_id, "kind": kind, "timeout_ms": timeout,
                                   "idled": False, "versions": dict(getattr(client, "versions", {}))})
