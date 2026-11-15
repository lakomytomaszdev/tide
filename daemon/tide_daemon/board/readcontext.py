"""Kontekst odczytu Tablicy na jedno odświeżenie (K-98 #1, PT-342).

Jedno odświeżenie BoardMonitor (migawka plików → projekcja → `engine.derived` → widok środowiska karty) wczytuje
Tablicę najwyżej raz. `BoardRead` jest jawnie przekazywanym, krótkożyjącym obiektem: powstaje w wątku odświeżenia,
przechodzi przez wywołania jako argument i ginie razem z odświeżeniem. Nie ma globalnego „bieżącego Board”, a obiekt
odświeżenia nie trafia do workera mutacji ani nie jest przechowywany między odświeżeniami — decyzje ze skutkiem
ubocznym (zatwierdzenie zgody, start kroku) czytają Tablicę świeżo, bez kontekstu.

Funkcje pomocnicze przyjmują kontekst opcjonalnie; bez niego zachowują dotychczasowy świeży odczyt `store.load()`.

K-98 #3 (PT-344): ten sam kontekst służy jednemu tickowi przepływu (`engine.tick` tworzy go na początku ticku
i przekazuje jawnie do `_project_base`, `_tick_card`, `_project`, `_tracker_notes`, `_queue_notices`, `_caretake`,
`_guard_card`). Tylko kontekst ticku (`invalidate_on_write=True`) jest unieważniany zapisem Tablicy w tym procesie
(`BoardStore.generation`) — kolejne użycie wczytuje Tablicę ponownie (osobny odczyt po zapisie). Kontekst
odświeżenia zachowuje kontrakt z #1: najwyżej jedno wczytanie na odświeżenie i jedna wersja Tablicy w opublikowanym
wyniku, także gdy tick równolegle zapisuje Tablicę. Ścieżki ze skutkiem ubocznym (start kroku, ruch karty, scalenie)
nadal czytają świeżo, bez kontekstu.

K-113 #3 (PT-467): kontekst ticku wczytuje Tablicę przez `BoardStore.load_for_tick` — pamięć ostatniego udanego
wczytania według odcisku plików Tablicy (bez czytania treści). Niezmieniona Tablica nie jest wczytywana ani parsowana
ponownie w kolejnym ticku; każda zmiana (GUI, git, edycja ręczna, zapis w procesie) daje świeże `load()`. Kontekst
odświeżenia nadal wczytuje przez publiczne `load()`, bez pamięci.
"""


class BoardRead:
    """Leniwy, jednorazowy odczyt Tablicy dla jednego odświeżenia albo jednego ticku.

    `board` wczytuje Tablicę przy pierwszym użyciu (albo przyjmuje już wczytaną) i zwraca ten sam obiekt przy
    kolejnych. Z `invalidate_on_write=True` (kontekst ticku, K-98 #3) zapis Tablicy w tym procesie
    (`store.generation`) unieważnia kontekst i następne użycie wczytuje ją ponownie; domyślnie (odświeżenie, #1)
    kontekst trzyma jedną wczytaną wersję do końca odświeżenia.
    Kontekst ticku wczytuje przez `store.load_for_tick` (pamięć według odcisku plików, K-113 #3), odświeżenie przez
    `store.load()`.
    Błąd wczytania jest zapamiętany i rzucany ponownie, więc nieudany odczyt też liczy się raz.
    Obiekt `Board` traktujemy jako tylko do odczytu."""

    __slots__ = ("_store", "_board", "_error", "_memo", "_generation", "_invalidate_on_write")

    def __init__(self, store, board=None, *, invalidate_on_write: bool = False):
        self._store = store
        self._board = board
        self._error: BaseException | None = None
        self._memo: dict = {}
        self._invalidate_on_write = invalidate_on_write
        self._generation = self._current()

    def _current(self):
        value = getattr(self._store, "generation", None)
        return value if isinstance(value, int) else None

    def _drop_if_stale(self) -> None:
        """Zapis Tablicy po wczytaniu: kontekst ticku nieaktualny — także jego wartości pomocnicze i zapamiętany
        błąd. Kontekst odświeżenia (bez `invalidate_on_write`) nie reaguje na zapisy."""
        if not self._invalidate_on_write:
            return
        current = self._current()
        if current != self._generation:
            self._board, self._error, self._memo, self._generation = None, None, {}, current

    def once(self, key, compute):
        """Wartość pomocnicza liczona raz na odświeżenie (np. metadane magazynu kopii paczek projektu w kluczu
        pamięci widoku). Ginie razem z kontekstem; służy wyłącznie prezentacji, nigdy decyzji ze skutkiem."""
        self._drop_if_stale()
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    @property
    def board(self):
        self._drop_if_stale()
        if self._board is None:
            if self._error is not None:
                raise self._error
            # K-113 #3: tylko kontekst ticku korzysta z pamięci wczytania magazynu (odcisk plików Tablicy);
            # kontekst odświeżenia i magazyn bez tej metody (atrapy w testach) wczytują świeżo przez `load()`.
            load = getattr(type(self._store), "load_for_tick", None) if self._invalidate_on_write else None
            try:
                self._board = load(self._store) if callable(load) else self._store.load()
            except Exception as exc:
                self._error = exc
                raise
        return self._board

    @property
    def loaded(self) -> bool:
        return self._board is not None
