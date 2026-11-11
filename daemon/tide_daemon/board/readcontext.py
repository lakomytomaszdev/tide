"""Kontekst odczytu Tablicy na jedno odświeżenie (K-98 #1, PT-342).

Jedno odświeżenie BoardMonitor (migawka plików → projekcja → `engine.derived` → widok środowiska karty) wczytuje
Tablicę najwyżej raz. `BoardRead` jest jawnie przekazywanym, krótkożyjącym obiektem: powstaje w wątku odświeżenia,
przechodzi przez wywołania jako argument i ginie razem z odświeżeniem. Nie ma globalnego „bieżącego Board”, a obiekt
nie trafia do workera mutacji ani nie jest przechowywany między odświeżeniami — decyzje ze skutkiem ubocznym
(zatwierdzenie zgody, start kroku, tick) czytają Tablicę świeżo, bez kontekstu.

Funkcje pomocnicze przyjmują kontekst opcjonalnie; bez niego zachowują dotychczasowy świeży odczyt `store.load()`.
"""


class BoardRead:
    """Leniwy, jednorazowy odczyt Tablicy dla jednego odświeżenia.

    `board` wczytuje Tablicę przy pierwszym użyciu (albo przyjmuje już wczytaną) i zwraca ten sam obiekt przy
    kolejnych. Błąd wczytania jest zapamiętany i rzucany ponownie, więc nieudany odczyt też liczy się raz.
    Obiekt `Board` traktujemy jako tylko do odczytu."""

    __slots__ = ("_store", "_board", "_error")

    def __init__(self, store, board=None):
        self._store = store
        self._board = board
        self._error: BaseException | None = None

    @property
    def board(self):
        if self._board is None:
            if self._error is not None:
                raise self._error
            try:
                self._board = self._store.load()
            except Exception as exc:
                self._error = exc
                raise
        return self._board

    @property
    def loaded(self) -> bool:
        return self._board is not None
