"""Ruch myszy w zagnieżdżonym KWinie — „użytkownik wrócił” na potrzeby zrzutów (desktop_cat_preview.sh).

Użycie: WAYLAND_DISPLAY=<gniazdo zagnieżdżonego KWina> python3 preview_input.py [dx dy]

Wysyła jeden ruch wskaźnika przez protokół KWina org_kde_kwin_fake_input, tym samym małym
klientem Wayland co tide-daemon (daemon/tide_daemon/wayland_idle.py). KWin udostępnia ten protokół
tylko zaufanym programom, więc działa wyłącznie w KWinie uruchomionym do podglądu
z KWIN_WAYLAND_NO_PERMISSION_CHECKS=1 — nigdy w sesji pulpitu.
"""
from __future__ import annotations

import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))
from tide_daemon import wayland_idle as wl  # noqa: E402

FAKE_INPUT = "org_kde_kwin_fake_input"
AUTHENTICATE, POINTER_MOTION = 0, 1


def fixed(value: float) -> bytes:
    """Liczba stałoprzecinkowa Wayland (24.8)."""
    return struct.pack("=i", int(round(value * 256)))


class FakePointer(wl.IdleClient):
    """Połączenie z rejestrem jak w IdleClient, ale zamiast bezczynności — sztuczne wejście."""

    def __init__(self, path: str | None = None):
        super().__init__(1, lambda: None, lambda: None, path)

    def move(self, dx: float, dy: float) -> None:
        path = self.path or wl.socket_path()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        self.sock.connect(path)
        try:
            registry = self._new("wl_registry")
            self._send(wl.message(wl.DISPLAY_ID, wl.GET_REGISTRY, wl.uint(registry)))
            self._roundtrip(5)
            if FAKE_INPUT not in self._globals:
                raise wl.Unsupported(f"KWin nie udostępnia {FAKE_INPUT} (brak KWIN_WAYLAND_NO_PERMISSION_CHECKS=1?)")
            fake = self._bind(registry, FAKE_INPUT, 1)
            self._send(wl.message(fake, AUTHENTICATE, wl.string("Tide preview") + wl.string("zrzuty tapety")))
            self._send(wl.message(fake, POINTER_MOTION, fixed(dx) + fixed(dy)))
            self._roundtrip(5)
        finally:
            self.close()


if __name__ == "__main__":
    dx, dy = (float(v) for v in sys.argv[1:3]) if len(sys.argv) > 2 else (40.0, 25.0)
    FakePointer().move(dx, dy)
