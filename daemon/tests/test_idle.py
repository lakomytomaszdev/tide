"""Bezczynność użytkownika dla tapety z kotem: klient Wayland, stan, monitor Qt i kontrakt D-Bus.

Kompozytor to atrapa (fake_wayland.py) na prawdziwym gnieździe uniksowym, z zamrożonym zegarem:
czas płynie tylko przez advance(), wejście użytkownika to input(). Usługa w teście kontraktu
działa na prywatnej magistrali i łączy się wyłącznie z atrapą — nigdy z kompozytorem pulpitu.
"""
import json
import os
import select
import shutil
import tempfile
import time
import unittest

from . import DAEMON_DIR
from .fake_wayland import FakeCompositor
from .privatebus import SERVICE, PrivateBus, app, requires_dbus, wait_until
from tide_daemon import wayland_idle
from tide_daemon.idle import DEFAULT_DOZE_AFTER_S, IdleMonitor, IdleState, doze_after_s
from tide_daemon.wayland_idle import IdleClient, Unsupported, WaylandError

try:
    from PyQt6.QtDBus import QDBusMessage
except ImportError:  # pragma: no cover
    pass

PALETTE = os.path.join(os.path.dirname(DAEMON_DIR), "theme", "palette", "mist.json")
IDLE_PATH, IDLE_IFACE = "/Idle", "org.tide.Daemon.Idle"


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Events:
    def __init__(self):
        self.items = []

    def idle(self):
        self.items.append("idle")

    def resume(self):
        self.items.append("resume")


def pump(client: IdleClient, wait: float = 0.3) -> None:
    """Obsługuje zdarzenia, które zdążyły przyjść w ciągu `wait` sekund."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        ready, _, _ = select.select([client], [], [], 0.02)
        if ready:
            client.dispatch()


class WireFormatTest(unittest.TestCase):
    def test_string_is_padded_to_four_bytes_with_terminator(self):
        data = wayland_idle.string("wl_seat")
        self.assertEqual(len(data) % 4, 0)
        self.assertEqual(wayland_idle.read_string(data, 0), ("wl_seat", len(data)))

    def test_partial_message_stays_in_the_buffer(self):
        full = wayland_idle.message(5, 1, wayland_idle.uint(7))
        buffer = bytearray(full + full[:6])
        self.assertEqual(wayland_idle.split_messages(buffer), [(5, 1, wayland_idle.uint(7))])
        self.assertEqual(len(buffer), 6)
        buffer += full[6:]
        self.assertEqual(len(wayland_idle.split_messages(buffer)), 1)

    def test_socket_path_follows_libwayland(self):
        self.assertEqual(wayland_idle.socket_path({"XDG_RUNTIME_DIR": "/run/user/1", "WAYLAND_DISPLAY": "wayland-1"}),
                         "/run/user/1/wayland-1")
        self.assertEqual(wayland_idle.socket_path({"XDG_RUNTIME_DIR": "/run/user/1"}), "/run/user/1/wayland-0")
        self.assertEqual(wayland_idle.socket_path({"WAYLAND_DISPLAY": "/tmp/x"}), "/tmp/x")
        self.assertIsNone(wayland_idle.socket_path({}))


class CompositorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-wl-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "wayland-test")

    def compositor(self, **kwargs) -> FakeCompositor:
        fake = FakeCompositor(self.path, **kwargs)
        self.addCleanup(fake.stop)
        return fake

    def client(self, timeout_ms=30_000):
        events = Events()
        client = IdleClient(timeout_ms, events.idle, events.resume, path=self.path)
        self.addCleanup(client.close)
        return client, events


class IdleClientTest(CompositorCase):
    """Klient protokołu na atrapie kompozytora: próg, powrót wejścia, wybór protokołu, błędy."""

    def test_input_idle_notification_with_the_threshold(self):
        fake = self.compositor()
        client, events = self.client(30_000)
        client.connect()
        [note] = fake.wait_for_notifications()
        # ext_idle_notifier_v1 w wersji 2: tylko wejście użytkownika, blokady bezczynności się nie liczą.
        self.assertEqual((note["kind"], note["timeout_ms"]), ("ext-input", 30_000))
        self.assertEqual(note["versions"]["ext_idle_notifier_v1"], 2)
        self.assertEqual(client.source, wayland_idle.SOURCE_EXT_INPUT)

        fake.advance(29.9)
        pump(client)
        self.assertEqual(events.items, [])  # przed progiem nic
        fake.advance(0.1)
        pump(client)
        self.assertEqual(events.items, ["idle"])
        fake.advance(100)
        pump(client)
        self.assertEqual(events.items, ["idle"])  # jedno zdarzenie, nie co chwilę
        fake.input()
        pump(client)
        self.assertEqual(events.items, ["idle", "resume"])

    def test_activity_resets_the_countdown(self):
        fake = self.compositor()
        client, events = self.client(30_000)
        client.connect()
        fake.wait_for_notifications()
        fake.advance(20)
        fake.input()  # ruch myszy w 20. sekundzie
        fake.advance(20)
        pump(client)
        self.assertEqual(events.items, [])  # 20 s od ostatniego wejścia — jeszcze czuwa
        fake.advance(10)
        pump(client)
        self.assertEqual(events.items, ["idle"])

    def test_version_one_falls_back_to_plain_idle_notification(self):
        fake = self.compositor(ext_version=1)
        client, _ = self.client(5_000)
        client.connect()
        [note] = fake.wait_for_notifications()
        self.assertEqual((note["kind"], note["timeout_ms"]), ("ext", 5_000))
        self.assertEqual(client.source, wayland_idle.SOURCE_EXT)

    def test_kde_protocol_is_the_last_resort(self):
        fake = self.compositor(ext_version=None, kde=True)
        client, events = self.client(5_000)
        client.connect()
        [note] = fake.wait_for_notifications()
        self.assertEqual((note["kind"], note["timeout_ms"]), ("kde", 5_000))
        fake.advance(5)
        pump(client)
        fake.input()
        pump(client)
        self.assertEqual(events.items, ["idle", "resume"])

    def test_compositor_without_idle_protocol_is_unsupported(self):
        self.compositor(ext_version=None, kde=False)
        client, _ = self.client()
        with self.assertRaises(Unsupported):
            client.connect()
        self.assertEqual(client.fileno(), -1)

    def test_missing_compositor_is_an_error(self):
        client, _ = self.client()
        with self.assertRaises(WaylandError):
            client.connect()

    def test_protocol_error_is_reported(self):
        self.compositor(fail_notification=True)
        client, _ = self.client()
        with self.assertRaisesRegex(WaylandError, "odmowa"):
            client.connect()

    def test_closed_connection_is_reported_by_dispatch(self):
        fake = self.compositor()
        client, _ = self.client()
        client.connect()
        fake.wait_for_notifications()
        fake.drop_clients()
        with self.assertRaises(WaylandError):
            pump(client, 1.0)


class IdleStateTest(unittest.TestCase):
    """Czysta logika stanu na zamrożonym zegarze."""

    def setUp(self):
        self.clock = FakeClock()
        self.state = IdleState(30_000, self.clock)
        self.state.connected("atrapa")

    def test_idle_counts_from_the_last_input_not_from_the_event(self):
        self.clock.now += 30
        self.assertTrue(self.state.idled())
        self.assertEqual(self.state.idle_for_ms(), 30_000)
        self.clock.now += 15
        self.assertEqual(self.state.idle_for_ms(), 45_000)

    def test_activity_resets(self):
        self.state.idled()
        self.assertTrue(self.state.resumed())
        self.assertFalse(self.state.idle)
        self.assertEqual(self.state.idle_for_ms(), 0)
        self.assertFalse(self.state.resumed())  # kolejne wejście bez zmiany stanu

    def test_repeated_idled_is_not_a_change(self):
        self.assertTrue(self.state.idled())
        self.assertFalse(self.state.idled())

    def test_lost_source_means_awake(self):
        self.state.idled()
        self.assertTrue(self.state.lost())
        self.assertEqual(self.state.snapshot(), {"available": False, "idle": False, "thresholdMs": 30_000,
                                                 "idleForMs": 0, "source": ""})
        # Spóźnione zdarzenie po utracie źródła nie usypia kota.
        self.assertFalse(self.state.idled())

    def test_threshold_from_the_palette(self):
        self.assertEqual(doze_after_s([PALETTE]), 30)
        with open(PALETTE, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["wallpaper"]["dozeAfterS"], 30)
        self.assertEqual(doze_after_s(["/nie/ma/takiego/pliku.json"]), DEFAULT_DOZE_AFTER_S)
        broken = os.path.join(tempfile.mkdtemp(prefix="tide-pal-"), "p.json")
        self.addCleanup(shutil.rmtree, os.path.dirname(broken))
        with open(broken, "w", encoding="utf-8") as fh:
            json.dump({"wallpaper": {"dozeAfterS": 0}}, fh)
        self.assertEqual(doze_after_s([broken, PALETTE]), 30)  # zły próg — następna paleta


class IdleMonitorTest(CompositorCase):
    """Monitor Qt: sygnały, utrata kompozytora i ponowne połączenie."""

    def setUp(self):
        super().setUp()
        app()

    def monitor(self, threshold_ms=30_000):
        mon = IdleMonitor(threshold_ms, client_factory=lambda t, i, r: IdleClient(t, i, r, path=self.path),
                          retry_ms=(50, 200))
        self.addCleanup(mon.stop)
        self.changes = []
        mon.idleChanged.connect(self.changes.append)
        return mon

    def test_signals_follow_the_compositor(self):
        fake = self.compositor()
        mon = self.monitor()
        mon.start()
        self.assertTrue(mon.available)
        fake.wait_for_notifications()
        fake.advance(30)
        self.assertTrue(wait_until(lambda: self.changes == [True]))
        self.assertTrue(mon.idle)
        fake.input()
        self.assertTrue(wait_until(lambda: self.changes == [True, False]))
        self.assertEqual(json.loads(mon.state_json())["source"], wayland_idle.SOURCE_EXT_INPUT)

    def test_lost_compositor_wakes_the_cat_and_reconnects(self):
        fake = self.compositor()
        mon = self.monitor()
        mon.start()
        fake.wait_for_notifications()
        fake.advance(30)
        self.assertTrue(wait_until(lambda: mon.idle))
        fake.drop_clients()  # restart KWina
        self.assertTrue(wait_until(lambda: self.changes == [True, False]))
        self.assertFalse(mon.available)
        # Kompozytor wciąż nasłuchuje — monitor sam łączy się ponownie z tym samym progiem.
        self.assertTrue(wait_until(lambda: mon.available, timeout=3))
        self.assertEqual(fake.wait_for_notifications()[0]["timeout_ms"], 30_000)
        self.assertEqual(fake.connections, 2)

    def test_no_compositor_keeps_trying_quietly(self):
        mon = self.monitor()
        mon.start()
        self.assertFalse(mon.available)
        self.assertFalse(mon.idle)
        fake = self.compositor()  # kompozytor pojawia się później
        self.assertTrue(wait_until(lambda: mon.available, timeout=3))
        fake.wait_for_notifications()


@requires_dbus
class IdleDBusTest(CompositorCase):
    """Obiekt /Idle usługi na prywatnej magistrali, z atrapą kompozytora."""

    def start(self, *args, wayland=True):
        bus = PrivateBus("tide-idle-test")
        self.addCleanup(bus.stop)
        empty = tempfile.mkdtemp(prefix="tide-empty-")
        self.addCleanup(shutil.rmtree, empty, True)
        extra = {"WAYLAND_DISPLAY": self.path} if wayland else {}
        bus.spawn_daemon("--claude-dir", empty, "--codex-dir", empty, "--workspace", empty, "--no-processes",
                         "--no-krunner", "--no-notifications", *args, extra_env=extra)
        return bus

    def call(self, bus, member, iface=IDLE_IFACE, *args):
        reply = bus.call(SERVICE, IDLE_PATH, iface, member, *args)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return reply.arguments()[0]

    def test_idle_property_and_signal(self):
        fake = self.compositor()
        bus = self.start("--idle-after", "30")
        [note] = fake.wait_for_notifications()
        self.assertEqual(note["timeout_ms"], 30_000)
        received = []
        from PyQt6.QtCore import QObject, pyqtSlot

        class Receiver(QObject):
            @pyqtSlot(bool)
            def changed(self, value):
                received.append(value)

        receiver = Receiver()
        self.assertTrue(bus.bus.connect(SERVICE, IDLE_PATH, IDLE_IFACE, "IdleChanged", receiver.changed))
        self.assertIs(self.call(bus, "IsIdle"), False)
        self.assertTrue(json.loads(self.call(bus, "State"))["available"])

        fake.advance(30)
        self.assertTrue(wait_until(lambda: received == [True]))
        self.assertIs(self.call(bus, "IsIdle"), True)
        idle = self.call(bus, "Get", "org.freedesktop.DBus.Properties", IDLE_IFACE, "Idle")
        self.assertIs(idle, True)
        fake.input()
        self.assertTrue(wait_until(lambda: received == [True, False]))
        self.assertIs(self.call(bus, "IsIdle"), False)

    def test_threshold_comes_from_the_installed_palette(self):
        fake = self.compositor()
        bus = self.start()
        self.assertEqual(fake.wait_for_notifications()[0]["timeout_ms"], 30_000)
        self.assertEqual(self.call(bus, "Get", "org.freedesktop.DBus.Properties", IDLE_IFACE, "ThresholdMs"), 30_000)

    def test_without_compositor_the_object_says_awake(self):
        bus = self.start(wayland=False)
        self.assertIs(self.call(bus, "IsIdle"), False)
        self.assertIs(self.call(bus, "Get", "org.freedesktop.DBus.Properties", IDLE_IFACE, "Available"), False)

    def test_no_idle_flag_drops_the_object(self):
        bus = self.start("--no-idle")
        reply = bus.call(SERVICE, IDLE_PATH, IDLE_IFACE, "IsIdle")
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ErrorMessage)


if __name__ == "__main__":
    unittest.main()
