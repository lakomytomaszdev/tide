"""Przyrostowe czytanie dzienników JSONL.

Dzienniki Codexa potrafią mieć dziesiątki megabajtów, więc przy pierwszym odczycie bierzemy
pierwszą linię (metadane sesji) i tylko ogon pliku. Każdy kolejny odczyt zaczyna się tam,
gdzie skończył się poprzedni.

Ogon może nie sięgać ostatniej prawdziwej tury: po kompaktowaniu Claude Code dopisuje streszczenie
(dziesiątki KB) i załączniki z ponownie wczytanymi plikami (po kilkadziesiąt KB każdy). Czytnik dostaje
wtedy warunek `enough(rekordy)` — dopóki ogon go nie spełnia, okno rośnie (×4, najwyżej MAX_TAIL_BYTES).
"""
import json
import os

TAIL_BYTES = 1024 * 1024  # stan sesji wynika z ostatnich wpisów, starsze nie są potrzebne
MAX_TAIL_BYTES = 16 * 1024 * 1024  # górna granica okna, gdy ogon nie zawiera ostatniej tury (enough)


class JsonlTail:
    def __init__(self, path: str, tail_bytes: int | None = TAIL_BYTES, enough=None,
                 max_tail_bytes: int = MAX_TAIL_BYTES):
        """tail_bytes=None: pierwszy odczyt bierze cały plik (małe pliki, np. indeks nazw wątków).
        enough: funkcja(rekordy ogona) -> bool — czy ogon wystarcza do stanu sesji; jeśli nie, pierwszy
        odczyt cofa się dalej (do max_tail_bytes)."""
        self.path = path
        self.tail_bytes = tail_bytes
        self.enough = enough
        self.max_tail_bytes = max_tail_bytes
        self.offset = 0
        self.size = 0
        self.mtime = 0.0
        self._rest = b""  # niedokończona ostatnia linia, dopisywana właśnie przez agenta

    def read_new(self) -> list[dict]:
        """Zwraca rekordy dopisane od ostatniego odczytu (przy pierwszym: nagłówek i ogon)."""
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if st.st_size < self.offset:
            # Plik skrócony albo podmieniony — czytamy od nowa.
            self.offset, self._rest = 0, b""
        self.size, self.mtime = st.st_size, st.st_mtime
        if st.st_size == self.offset:
            return []

        try:
            with open(self.path, "rb") as fh:
                if self.offset == 0 and self.tail_bytes is not None and st.st_size > self.tail_bytes:
                    records, rest = self._first_tail(fh, st.st_size)
                else:
                    fh.seek(self.offset)
                    lines = (self._rest + fh.read(st.st_size - self.offset)).split(b"\n")
                    rest = lines.pop()  # pusty, jeśli plik kończy się znakiem nowej linii
                    records = _parse_lines(lines)
        except OSError:
            return []
        self.offset, self._rest = st.st_size, rest
        return records

    def _first_tail(self, fh, size: int) -> tuple[list[dict], bytes]:
        """Nagłówek i ogon dużego pliku; ogon rośnie, dopóki nie spełnia `enough`."""
        head = _parse_lines([fh.readline()])
        head_end = fh.tell()
        window = self.tail_bytes
        while True:
            start = max(head_end, size - window)
            if start > head_end:
                fh.seek(start - 1)
                fh.readline()  # pierwsza linia ogona jest zwykle ucięta
            else:
                fh.seek(head_end)
            lines = fh.read(size - fh.tell()).split(b"\n")
            rest = lines.pop()
            records = _parse_lines(lines)
            if self.enough is None or start <= head_end or window >= self.max_tail_bytes or self.enough(records):
                return head + records, rest
            window = min(window * 4, self.max_tail_bytes)


def read_head(path: str, limit: int) -> list[dict]:
    """Rekordy z pierwszych `limit` bajtów pliku (bez uciętej ostatniej linii)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit)
    except OSError:
        return []
    lines = data.split(b"\n")
    if len(data) == limit:
        lines.pop()  # ostatnia linia może być ucięta w połowie
    return _parse_lines(lines)


def _parse_lines(lines) -> list[dict]:
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(record, dict):
            out.append(record)
    return out
