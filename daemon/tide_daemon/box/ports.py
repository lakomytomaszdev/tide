"""Pula pudełka: trwałe rezerwacje 10 portów na krok, poza montowaniami agenta.

Oddzielny plik blokady pozostaje tym samym inode przy atomowej wymianie stanu.
Rezerwacja nie sprawdza gniazd hosta: porty należą do przestrzeni sieciowej pudełka.
"""
import contextlib
import fcntl
import json
import os
import re
import tempfile

from . import profile as bp

BLOCK_SIZE = 10
DEMO_OWNER = re.compile(r"demo-[0-9]{14}-[0-9a-f]{8}")


class PoolExhausted(Exception):
    pass


class PortPool:
    def __init__(self, slug: str, box: str, root: str | None = None):
        base = root or os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        self.folder = os.path.join(base, "tide", "box", bp._check_slug(slug, "projekt"), "ports")
        self.box = bp._check_slug(box, "pudełko")

    @contextlib.contextmanager
    def _locked(self):
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        path = os.path.join(self.folder, self.box + ".json")
        with open(os.path.join(self.folder, self.box + ".lock"), "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                try:
                    with open(path, encoding="utf-8") as fh:
                        leases = json.load(fh)
                except FileNotFoundError:
                    leases = {}
                original = dict(leases)
                yield leases
                if leases == original:
                    return
                fd, tmp = tempfile.mkstemp(prefix=self.box + "-", dir=self.folder)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(leases, fh, sort_keys=True)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, path)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def acquire(self, owner: str, profile: dict) -> dict:
        ports = profile.get("ports") or {}
        pool = ports.get("pool")
        if pool is None:
            return {}
        with self._locked() as leases:
            base = leases.get(owner)
            if base is None:
                for candidate in range(pool[0], pool[1] - BLOCK_SIZE + 2, BLOCK_SIZE):
                    if all(candidate + BLOCK_SIZE <= other or other + BLOCK_SIZE <= candidate
                           for other in leases.values()):
                        base = candidate
                        leases[owner] = base
                        break
            if base is None:
                raise PoolExhausted("czeka na porty (pula pudełka wyczerpana)")
        return {key: str(base) for key in ("PORT", "E2E_PORT", "TIDE_PORT_BASE", *ports.get("env", []))}

    def release(self, owner: str) -> None:
        with self._locked() as leases:
            leases.pop(owner, None)

    @contextlib.contextmanager
    def demo_guard(self, owner: str, *, blocking: bool = True):
        """Blokada życia demo i klienta, także przed utworzeniem jednostki.

        Plik zostaje na dysku, żeby konkurujące procesy zawsze blokowały ten sam inode.
        Trwały właściciel przydziału określa jednostkę bez dodatkowego pliku metadanych.
        Zwrócony plik można przekazać klientowi przez Popen(pass_fds=…). Zamknięcie
        rodzica nie odblokowuje wtedy klienta: blokada znika po zamknięciu ostatniej kopii.
        """
        if not DEMO_OWNER.fullmatch(owner):
            raise ValueError("niepoprawny identyfikator demo")
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        path = os.path.join(self.folder, self.box + "." + owner + ".lock")
        with open(path, "a", encoding="utf-8") as guard:
            try:
                fcntl.flock(guard, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                yield False
                return
            # Bez LOCK_UN: deskryptor odziedziczony przez klienta wskazuje tę samą
            # blokadę, więc jawne odblokowanie zwolniłoby ją także żywemu klientowi.
            yield guard

    def recover_demos(self, unit_state) -> None:
        """Odzyskuje osierocone demo tylko po potwierdzonym końcu jednostki w tym pudełku."""
        with self._locked() as leases:
            owners = [owner for owner in leases if DEMO_OWNER.fullmatch(owner)]
        for owner in owners:
            with self.demo_guard(owner, blocking=False) as available:
                if available and unit_state("tide-step-" + owner + ".service") == "inactive":
                    self.release(owner)
