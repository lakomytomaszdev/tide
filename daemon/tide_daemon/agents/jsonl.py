"""Przyrostowe czytanie dzienników JSONL.

Dzienniki Codexa potrafią mieć dziesiątki megabajtów, więc przy pierwszym odczycie bierzemy
pierwszą linię (metadane sesji) i tylko ogon pliku. Każdy kolejny odczyt zaczyna się tam,
gdzie skończył się poprzedni.
"""
import json
import os

TAIL_BYTES = 1024 * 1024  # stan sesji wynika z ostatnich wpisów, starsze nie są potrzebne


class JsonlTail:
    def __init__(self, path: str, tail_bytes: int | None = TAIL_BYTES):
        """tail_bytes=None: pierwszy odczyt bierze cały plik (małe pliki, np. indeks nazw wątków)."""
        self.path = path
        self.tail_bytes = tail_bytes
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

        records: list[dict] = []
        try:
            with open(self.path, "rb") as fh:
                if self.offset == 0 and self.tail_bytes is not None and st.st_size > self.tail_bytes:
                    head = fh.readline()
                    records.extend(_parse_lines([head]))
                    fh.seek(max(fh.tell(), st.st_size - self.tail_bytes))
                    fh.readline()  # pierwsza linia ogona jest zwykle ucięta
                else:
                    fh.seek(self.offset)
                data = self._rest + fh.read(st.st_size - fh.tell())
                self.offset = st.st_size
        except OSError:
            return records

        lines = data.split(b"\n")
        self._rest = lines.pop()  # pusty, jeśli plik kończy się znakiem nowej linii
        records.extend(_parse_lines(lines))
        return records


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
