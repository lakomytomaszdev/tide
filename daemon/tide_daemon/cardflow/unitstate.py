"""Pamięć stanu jednostek trzymanych procesów K-38 w obserwacji (K-125 #3, PT-488).

Inwentaryzacja K-38 (`trialhold.inventory`) przy każdym pełnym przebiegu karty pytała o stan każdej jednostki
trzymanego procesu (`stepstop.unit_state` — proces `systemctl`, albo runner pudełka). Bez `fresh` stan jednostki
pochodzi teraz z tej pamięci, najwyżej `TTL` sekund od odczytu (zegar monotoniczny, wstrzykiwany w testach).

- Pamiętamy wyłącznie zwrócone `active` i `inactive`. `unknown` (błąd `systemctl`/`incus exec`, brak magistrali,
  timeout) i wyjątek z odczytu nigdy nie są pamięcią i usuwają wcześniejszy wpis: następny odczyt pyta ponownie
  (niepewność nie staje się pamięcią, User Story 26).
- `fresh=True` zawsze czyta świeżo i odświeża pamięć (tylko `active`/`inactive`) — inwentaryzacja powtarzana przed
  zapisem `k38.inventory`/`k38.handoff`, diagnostyka K-38. Pamięć niczego nie autoryzuje: zapis wynika tylko ze
  świeżego przebiegu (ADR-4).

Moduł nie importuje silnika (ADR-6): odczyt stanu przychodzi jako funkcja, klucz jednostki jako wartość. Importuje go
tylko silnik (`FlowEngine.unit_states`); `trialhold` korzysta z pamięci przez obiekt silnika, bez importu tego modułu,
więc zmiana tutaj nie wybiera testów wszystkich odbiorców `trialhold`."""
import threading
import time

TTL = 30.0                      # N z karty: najdłuższy wiek stanu jednostki z pamięci, w sekundach
REMEMBERED = frozenset({"active", "inactive"})


class UnitStates:
    """Pamięć stanu jednostek jednego silnika: klucz jednostki → (chwila odczytu, stan)."""

    def __init__(self, clock=time.monotonic, ttl: float = TTL):
        self.clock = clock
        self.ttl = ttl
        self._lock = threading.Lock()
        self._states: dict = {}

    def read(self, key, reader, *, fresh: bool = False) -> str:
        """Stan jednostki `key`: z pamięci (bez `fresh`, wpis młodszy niż `ttl`) albo z `reader()`.
        Wyjątek z `reader` przechodzi do wołającego, a wpis znika."""
        if not fresh:
            with self._lock:
                hit = self._states.get(key)
                if hit is not None and self.clock() - hit[0] < self.ttl:
                    return hit[1]
        try:
            state = reader()
        except BaseException:
            self.forget(key)
            raise
        if state in REMEMBERED:
            now = self.clock()
            with self._lock:
                self._states = {k: v for k, v in self._states.items() if now - v[0] < self.ttl}
                self._states[key] = (now, state)
        else:
            self.forget(key)
        return state

    def forget(self, key) -> None:
        with self._lock:
            self._states.pop(key, None)
