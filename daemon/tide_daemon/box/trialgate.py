"""Bramka K-38 v1. Brak adaptera chronionego kanału oznacza zamknięcie.

Nie czyta zgód z plików projektu, stdout, ustawień, dziennika ani result.json.
Adapter PT-271 i procedura PT-273 muszą być instalowane przez kod usługi,
po ich osobnym odbiorze. Nie ma przełącznika CLI, zmiennej środowiska ani sekretu
zwalniającego stare wejścia. Historyczne próby nigdy nie otrzymują nowej zgody.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import threading

from ..trialpolicy import (LEGACY, EXCLUDED, MESSAGE, TrialDenied, blocked, blocked_event,
                           require_ticket, require_step, require_public)

DEPENDENCIES = frozenset(f"PT-{n}" for n in (*range(261, 274), 276))


@dataclass(frozen=True)
class Binding:
    """Tożsamość z hostowego rejestru, nigdy z żądania agenta."""
    procedure: str
    version: str
    target: str
    attempt: str
    persistence: str
    manifest: str
    sha: str
    dependencies: tuple[tuple[str, str], ...]
    review_instance: str = ""

    def complete(self):
        return (all(isinstance(v, str) and v for v in (self.procedure, self.version, self.target,
                self.attempt, self.persistence, self.manifest, self.sha))
                and type(self.dependencies) is tuple
                and all(type(pair) is tuple and len(pair) == 2
                        and all(isinstance(v, str) and v for v in pair) for pair in self.dependencies)
                and len(dict(self.dependencies)) == len(self.dependencies)
                and DEPENDENCIES <= dict(self.dependencies).keys()
                and isinstance(self.review_instance, str))


@dataclass(frozen=True)
class HostRecord:
    """Wynik adaptera chronionego kanału PT-271, a nie format JSON do importu.

    Adapter odpowiada za pochodzenie rekordu z hostowego recenzenta na utrwalonym
    wejściu PT-270. Sam podpis skopiowanego wyniku pudełka nie spełnia kontraktu.
    """
    review_ticket: str
    review_instance: str
    binding: Binding


class ClosedStore:
    @contextmanager
    def locked_record(self, binding):
        yield None


class TrialGate:
    """Wąski interfejs usługi do przyszłej procedury, bez wykonywania argv.

    `procedure` jest zaufanym adapterem PT-273: snapshot() czyta aktualny stan
    hosta, bootstrap_reviewed() potwierdza osobny odbiór #13, start() sam tworzy
    własny jednorazowy cel. Żaden z nich nie przyjmuje polecenia/ścieżki agenta.
    `locked_record` musi trzymać blokadę utrwalenia i wersji aż do końca start().
    Produkcja nie instaluje jeszcze tych adapterów: magazyn pozostaje zamknięty.
    """
    def __init__(self, *, store=None, procedure=None):
        self._store = store if store is not None else ClosedStore()
        self._procedure = procedure
        self._lock = threading.RLock()
        self._pending = {}
        self._used = set()
        self._attempts = set()

    def register_bootstrap(self, step):
        with self._lock:
            if self._procedure is None or self._procedure.bootstrap_reviewed() is not True:
                raise TrialDenied("K-38 v1: brak odebranej hostowej procedury PT-273")
            binding = self._procedure.snapshot()
            if type(binding) is not Binding or not binding.complete() or step in self._pending:
                raise TrialDenied("K-38 v1: nieznana wersja procedury diagnostycznej")
            if any(binding.target == target or binding.attempt == attempt for target, attempt in self._attempts):
                raise TrialDenied("K-38 v1: wymagany nowy jednorazowy cel i próba")
            self._attempts.add((binding.target, binding.attempt))
            self._pending[step] = binding
            return binding

    def start_registered(self, step, *, consent, inventory, bootstrap=False):
        with self._lock:
            binding = self._pending.get(step)
            if (consent is not True or binding is None or step in self._used
                    or not callable(inventory) or self._procedure.snapshot() != binding):
                raise TrialDenied("K-38 v1: brak aktualnej zgody lub nieznany stan procesów")
            with self._store.locked_record(binding) as record:
                # Odczyt procesów także pod blokadą, nie wcześniejsza migawka bool.
                if inventory() is not True or self._procedure.snapshot() != binding:
                    raise TrialDenied("K-38 v1: zmieniono wersję podczas kontroli odbioru")
                if bootstrap:
                    approved = self._procedure.bootstrap_reviewed() is True
                else:
                    approved = (type(record) is HostRecord and record.review_ticket == "PT-274"
                                and bool(binding.review_instance) and record.review_instance == binding.review_instance
                                and record.binding == binding)
                if not approved:
                    raise TrialDenied("K-38 v1: brak chronionego rekordu odbioru PT-274")
                # Rezerwacja przed procesem; błąd/nieznany wynik nie daje automatycznego powtórzenia.
                self._used.add(step)
                return self._procedure.start(binding)
