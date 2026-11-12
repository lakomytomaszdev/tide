"""Moduł odczytu gita w usłudze (K-36, poza `cardflow/`): czyste funkcje i utwardzony runner gita.

`diffparse.py` (PT-359) nie uruchamia gita ani nie czyta plików. `runner.GitReader` (PT-348) to utwardzony
runner, a `read` — odczyty przez niego. `diffdata.py` (PT-357) składa `DiffData` źródła `card` dla interfejsu
D-Bus `Git.Diff` (`git_monitor.py`, `dbus_service.GitAdaptor`); dalsi odbiorcy: mapa gałęzi i oś commitów.
"""
from .runner import GitReadError, GitReader

__all__ = ["GitReadError", "GitReader"]
