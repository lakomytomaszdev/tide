"""Moduł odczytu gita w usłudze (K-36): utwardzony runner (`runner.GitReader`) i odczyty przez niego
(`read`). Bez interfejsu D-Bus; odbiorcy: `Git.Diff` źródła `card` (#23), mapa gałęzi i oś commitów."""
from .runner import GitReadError, GitReader

__all__ = ["GitReadError", "GitReader"]
