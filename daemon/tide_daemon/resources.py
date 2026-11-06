"""Zasoby należące do Tide; metodologie zewnętrzne dostarcza kontrakt skilli K-59."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


ASSETS = {"plan-standard": ("docs/agent-plan-standard.md", "1.3"),
          "next-id": ("tools/next_id.py", "1"),
          "check-plans": ("tools/check_plans.py", "1")}


class ResourceError(ValueError):
    """Niekompletna instalacja albo błędna konfiguracja zasobów."""

    code = "unavailable"


def root() -> Path:
    configured = os.environ.get("TIDE_RESOURCES_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    # Kopia usługi: tide/daemon/tide_daemon; checkout: repo/daemon/tide_daemon.
    base = Path(__file__).resolve().parents[2]
    installed = base / "resources"
    return installed if installed.is_dir() else base


def resolve(name: str) -> Path:
    path = root() / ASSETS[name][0]
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ResourceError(f"Brak zasobu Tide {name}: {path}. Zainstaluj zasoby Tide albo ustaw "
                            "TIDE_RESOURCES_DIR na katalog zawierający docs/ i tools/.")
    return path


def info() -> dict:
    """Jawna lista plików i wersji, bez konfiguracji, instrukcji ani sekretów użytkownika."""
    result = {}
    for name, (relative, version) in ASSETS.items():
        item = {"path": str(root() / relative), "version": version, "available": False}
        if name in ("next-id", "check-plans"):
            item["pythonPath"] = str(Path(__file__).resolve().parent.parent)
        try:
            data = resolve(name).read_bytes()
            item.update(available=True, sha256=hashlib.sha256(data).hexdigest())
        except (ResourceError, OSError):
            pass
        result[name] = item
    return result


def instructions() -> str:
    """Odsyłacz do czytelnego standardu; dostępność metodologii sprawdza agent według K-59."""
    return (f"\nStandard planu Tide: {resolve('plan-standard')} (wersja 1.3). Przeczytaj go.\n"
            "Metodologie zewnętrzne czytaj z pełnych SKILL.md i ich zasobów według katalogu skilli "
            "tego kroku. Jeśli wymaganej instrukcji brakuje, zakończ result=error z jej nazwą; "
            "zainstaluj ją w katalogu skilli swojego programu i ponów krok. "
            "Tide nie dystrybuuje tych metodologii ani prywatnych ustawień użytkownika.\n")
