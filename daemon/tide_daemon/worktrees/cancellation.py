"""Anulowanie odczytu worktrees i należących do niego procesów gita (PT-253)."""
import os
import signal
import subprocess
import threading
import time
from concurrent.futures import CancelledError


class Cancellation:
    def __init__(self):
        self._closed = threading.Event()

    def close(self) -> None:
        self._closed.set()

    def check(self) -> None:
        if self._closed.is_set():
            raise CancelledError("odczyt worktrees anulowany przy zamykaniu usługi")

    def run(self, argv, *, timeout, env, input_text=None):
        self.check()
        # Osobna grupa obejmuje też filtry i inne dzieci gita. Nie obejmuje jednostek kroków.
        proc = subprocess.Popen(argv, env=env, start_new_session=True,
                                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
        deadline = time.monotonic() + timeout
        first = True
        try:
            while True:
                self.check()  # także wyścig close() z Popen
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    out, err = proc.communicate(input=input_text if first else None,
                                                timeout=min(.05, remaining))
                    return subprocess.CompletedProcess(argv, proc.returncode, out, err)
                except subprocess.TimeoutExpired:
                    first = False
        except BaseException:
            _terminate(proc)
            raise
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()


    def run_capped(self, argv, *, timeout, env, max_bytes=None, max_lines=None):
        """Jak `run`, ale stdout czytany strumieniowo z limitem bajtów i linii (PT-355): po przekroczeniu
        proces jest przerywany, a wynik to pełne linie z początku wyjścia. Nadmiar nigdy nie trafia do pamięci
        (czytamy kawałkami i przestajemy). Zwraca `(CompletedProcess, ucięto)`; ucięty proces ma kod 0."""
        self.check()
        proc = subprocess.Popen(argv, env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        buf, err = bytearray(), bytearray()
        state = {"lines": 0, "cut": False}
        done = threading.Event()

        def pump_out():
            try:
                while True:
                    chunk = proc.stdout.read1(65536)
                    if not chunk:
                        return
                    keep = _fit(chunk, len(buf), state["lines"], max_bytes, max_lines)
                    buf.extend(chunk[:keep])
                    state["lines"] += chunk[:keep].count(b"\n")
                    if keep < len(chunk) or (max_lines is not None and state["lines"] >= max_lines
                                             and _more(proc.stdout)):
                        state["cut"] = True
                        return
            except (OSError, ValueError):
                pass
            finally:
                done.set()

        def pump_err():
            try:
                while True:
                    chunk = proc.stderr.read1(65536)
                    if not chunk:
                        return
                    if len(err) < 65536:
                        err.extend(chunk[:65536 - len(err)])
            except (OSError, ValueError):
                pass

        threads = [threading.Thread(target=pump_out, daemon=True), threading.Thread(target=pump_err, daemon=True)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + timeout
        try:
            while not done.wait(.05):
                self.check()
                if deadline - time.monotonic() <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
            self.check()
            if state["cut"]:
                _terminate(proc)
                del buf[buf.rfind(b"\n") + 1:]  # bez niepełnej ostatniej linii
                code = 0
            else:
                while proc.poll() is None:
                    self.check()
                    if deadline - time.monotonic() <= 0:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    time.sleep(.01)
                code = proc.returncode
            for thread in threads:
                thread.join(1)
            return (subprocess.CompletedProcess(argv, code, bytes(buf).decode("utf-8", "replace"),
                                                bytes(err).decode("utf-8", "replace")), state["cut"])
        except BaseException:
            _terminate(proc)
            raise
        finally:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()


def _fit(chunk, used_bytes, used_lines, max_bytes, max_lines) -> int:
    """Ile bajtów `chunk` mieści się w limitach (bajtów i linii) przy już zajętych `used_*`."""
    keep = len(chunk)
    if max_bytes is not None:
        keep = min(keep, max(0, max_bytes - used_bytes))
    if max_lines is not None:
        room = max_lines - used_lines
        pos = -1
        for _ in range(max(room, 0)):
            pos = chunk.find(b"\n", pos + 1)
            if pos < 0:
                break
        else:
            keep = min(keep, pos + 1)  # dokładnie `room` linii (przy room <= 0: 0); reszta kawałka to nadmiar
    return keep


def _more(stream) -> bool:
    """Czy po osiągnięciu limitu linii wyjście ma jeszcze jakikolwiek bajt (dokładnie limit to nie ucięcie)."""
    try:
        return bool(stream.peek(1))
    except (OSError, ValueError):
        return False


def _signal_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _terminate(proc):
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=.2)
    except subprocess.TimeoutExpired:
        pass
    # Lider mógł już wyjść, ale jego dzieci nadal trzymać rury communicate().
    _signal_group(proc, signal.SIGKILL)
    proc.wait(timeout=.5)
