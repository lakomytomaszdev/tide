"""Prywatna magistrala D-Bus dla testów: własny dbus-daemon i procesy podłączone tylko do niego.

Konfiguracja magistrali nie ma katalogów usług, więc nic nie zostanie uruchomione przez
aktywację D-Bus (np. prawdziwy serwer powiadomień z /usr/share/dbus-1/services). Testy nigdy
nie dotykają magistrali sesji pulpitu.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from . import DAEMON_DIR

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
except ImportError:  # pragma: no cover
    QDBusConnection = None

SERVICE = "org.tide.Daemon"
_APP = None  # QCoreApplication musi żyć — bez niego Qt nie dostarcza sygnałów D-Bus ani timerów


def app():
    global _APP
    _APP = QCoreApplication.instance() or QCoreApplication([])
    return _APP

BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir={tmp}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""

requires_dbus = unittest.skipUnless(QDBusConnection is not None and shutil.which("dbus-daemon"),
                                    "brak QtDBus albo dbus-daemon")


class PrivateBus:
    """dbus-daemon z minimalną konfiguracją i połączenie testu z nim."""

    def __init__(self, name: str):
        app()
        self.name = name
        self.tmp = tempfile.mkdtemp(prefix="tide-bus-")
        config = os.path.join(self.tmp, "bus.conf")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write(BUS_CONFIG.format(tmp=self.tmp))
        self.bus_proc = subprocess.Popen(["dbus-daemon", f"--config-file={config}", "--nofork", "--nopidfile",
                                          "--print-address=1"], stdout=subprocess.PIPE, text=True)
        self.address = self.bus_proc.stdout.readline().strip()
        self.children: list[subprocess.Popen] = []
        if not self.address:
            self.stop()
            raise unittest.SkipTest("prywatny dbus-daemon nie wystartował")
        self.env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=self.address)
        self.bus = QDBusConnection.connectToBus(self.address, name)

    def spawn(self, argv, service: str, extra_env: dict | None = None, **kwargs) -> subprocess.Popen:
        """Uruchamia proces na tej magistrali i czeka, aż zarejestruje nazwę usługi.

        extra_env: dodatkowe zmienne (np. atrapy programów na początku PATH, tymczasowy HOME)."""
        kwargs.setdefault("stdout", subprocess.DEVNULL)
        kwargs.setdefault("stderr", subprocess.DEVNULL)
        proc = subprocess.Popen(argv, env=dict(self.env, **(extra_env or {})), **kwargs)
        self.children.append(proc)
        deadline = time.time() + 15
        while time.time() < deadline and proc.poll() is None:
            if self.bus.interface().isServiceRegistered(service).value():
                return proc
            time.sleep(0.05)
        self.stop()
        raise AssertionError(f"{service} nie zarejestrowała się na magistrali")

    def spawn_daemon(self, *args, **kwargs) -> subprocess.Popen:
        # Własny XDG_STATE_HOME: usługa testowa nie może czytać ani kasować stanu kart Konsole
        # zapisanego przez usługę z pulpitu (tabmarks.default_store). Własny XDG_CACHE_HOME z tego
        # samego powodu: plik stanu agentów dla promptu (cwdstate.default_path) znika przy zamknięciu.
        # Nieistniejące gniazdo Wayland: usługa testowa nie łączy się z kompozytorem pulpitu
        # (obiekt /Idle zostaje „niedostępny”), chyba że test poda własny (atrapa kompozytora).
        # Brak Codex Desktop: usługa testowa nigdy nie uruchamia prawdziwej aplikacji ChatGPT
        # (odnośnik codex://), chyba że test wskaże atrapę pliku .desktop.
        # Nieistniejąca konfiguracja Limitów AI: usługa testowa nie pyta prawdziwej QuotaApp
        # i nie zna jej tokenu, chyba że test poda własną (atrapa API w test_limits_dbus.py).
        # Pusty TIDE_CLAUDE_BIN: raport poranny nie woła prawdziwego `claude -p`.
        extra_env = {"XDG_STATE_HOME": os.path.join(self.tmp, "state"),
                     "XDG_CACHE_HOME": os.path.join(self.tmp, "cache"),
                     "WAYLAND_DISPLAY": os.path.join(self.tmp, "no-wayland"),
                     "TIDE_CODEX_DESKTOP": "",
                     "LIMITY_CONFIG": os.path.join(self.tmp, "no-limity-config.json"),
                     "TIDE_CLAUDE_BIN": "",  # raport: „Na daily” z reguł, nigdy prawdziwy claude
                     **(kwargs.pop("extra_env", None) or {})}
        return self.spawn([sys.executable, os.path.join(DAEMON_DIR, "tide-daemon.py"), *args], SERVICE,
                          extra_env=extra_env, **kwargs)

    def call(self, service, path, iface, member, *args, timeout=10000):
        msg = QDBusMessage.createMethodCall(service, path, iface, member)
        if args:
            msg.setArguments(list(args))
        return self.bus.call(msg, QDBus.CallMode.Block, timeout)

    def stop(self):
        QDBusConnection.disconnectFromBus(self.name)
        for proc in self.children + [self.bus_proc]:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self.bus_proc.stdout:
            self.bus_proc.stdout.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def wait_until(predicate, timeout=5.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        QCoreApplication.processEvents()
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()
