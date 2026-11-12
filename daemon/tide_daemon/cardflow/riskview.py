"""Pamięć oceny ryzyka outside dla widoku decyzji (K-105 #4).

Publikacja migawki w wątku głównym nie rozwiązuje ścieżek (`realpath`) przy każdej publikacji: ocena ryzyka polecenia
do opisu decyzji „uruchom poza piaskownicą” pochodzi z pamięci kluczowanej (karta, wersja dziennika, polecenie,
korzenie). Pamięć jest osobna od pamięci workera i działa wyłącznie w zakresie `scope`, który otwiera odczyt
prezentacji (`Engine.derived`). Poza zakresem `outside_risk` liczy świeżo: zgoda automatyczna, kontrola przed startem,
`run-always` i decyzja merge-check wołają `outside_risky` bezpośrednio i pamięci nie widzą w ogóle.
Nowa wersja dziennika karty unieważnia jej wpisy; karta bez czekających decyzji outside traci wpisy."""
from __future__ import annotations

import contextlib
import contextvars
import threading

_ACTIVE: contextvars.ContextVar = contextvars.ContextVar("tide_outside_risk_scope", default=None)


class RiskMemo:
    def __init__(self):
        self._lock = threading.Lock()
        self._cards: dict[str, tuple] = {}      # karta → (wersja dziennika, {(polecenie, korzenie): powód})

    def entries(self, card: str) -> int:
        with self._lock:
            hit = self._cards.get(card)
            return len(hit[1]) if hit else 0

    @contextlib.contextmanager
    def scope(self, card: str, version):
        """Ocena ryzyka w bloku bierze pamięć tej karty w tej wersji dziennika (None — bez pamięci)."""
        with self._lock:
            hit = self._cards.get(card)
            if hit is None or hit[0] != version:
                self._cards[card] = (version, {})
        state = {"card": card, "version": version, "used": False}
        outer = _ACTIVE.get()
        _ACTIVE.set((self, state))
        try:
            yield
        finally:
            _ACTIVE.set(outer)
            if not state["used"]:
                with self._lock:
                    if self._cards.get(card, (None,))[0] == version:
                        self._cards.pop(card, None)    # nic outside nie czeka — bez wpisów

    def _risk(self, state: dict, command: str, roots: tuple, compute) -> str:
        key = (command, roots)
        state["used"] = True
        with self._lock:
            hit = self._cards.get(state["card"])
            if hit is not None and hit[0] == state["version"] and key in hit[1]:
                return hit[1][key]
        value = compute()
        with self._lock:
            hit = self._cards.get(state["card"])
            if hit is None:
                hit = self._cards[state["card"]] = (state["version"], {})
            if hit[0] == state["version"]:
                hit[1][key] = value
        return value


def outside_risk(command: str, roots: tuple) -> str:
    """Ocena ryzyka do opisu decyzji: z pamięci prezentacji w jej zakresie, poza nim świeżo."""
    from ..board.commands import outside_risky
    active = _ACTIVE.get()
    if active is None or active[1]["version"] is None:
        return outside_risky(command, roots)
    memo, state = active
    return memo._risk(state, command, tuple(roots), lambda: outside_risky(command, roots))
