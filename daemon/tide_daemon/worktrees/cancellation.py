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
