"""Miejsce na dysku i porządki: kategorie z `GET /system/df` i prune tylko wskazanych kategorii.

Kategorie (id — co liczymy jako „do odzyskania”):
- `cache` — pamięć podręczna budowania: wpisy nieużywane w tej chwili (InUse false) i niewspółdzielone,
- `images` — obrazy bez żadnego kontenera (także zatrzymanego): Size − SharedSize,
- `volumes` — wolumeny bez kontenera (RefCount 0); mogą trzymać dane baz, więc `warn`,
- `stopped` — zatrzymane kontenery: ich warstwa zapisu (SizeRw); wolumeny zostają.

Nic, czego używa działający kontener, nie wchodzi do żadnej kategorii: działający kontener trzyma
swój obraz i wolumeny (Containers/RefCount > 0), a jego wpisy cache są InUse.

Prune idzie przez endpointy Dockera, które same omijają rzeczy w użyciu, w kolejności: obrazy,
wolumeny, cache, kontenery. Kontenery na końcu — inaczej usunięcie zatrzymanych odsłoniłoby ich
obrazy i wolumeny, których użytkownik nie widział na liście i nie zaznaczył.
"""
from __future__ import annotations

import json

CATEGORIES = ("cache", "images", "volumes", "stopped")
PRUNE_ORDER = ("images", "volumes", "cache", "stopped")
LABELS = {
    "cache": "Pamięć podręczna budowania",
    "images": "Nieużywane obrazy",
    "volumes": "Wolumeny bez kontenera",
    "stopped": "Zatrzymane kontenery",
}
PRUNE_TIMEOUT = 300.0  # usuwanie dużych obrazów i cache potrafi trwać


def _items(df: dict, legacy: str, usage: str) -> list:
    value = df.get(legacy)
    if isinstance(value, list):
        return value
    block = df.get(usage)
    if isinstance(block, dict) and isinstance(block.get("Items"), list):
        return block["Items"]
    return []


def _size(value) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def categories(df: dict) -> dict:
    """{categories: [...], totalReclaimableBytes}. Każda kategoria: id, label, note, count, total,
    reclaimableBytes, totalBytes, warn."""
    cache = _items(df, "BuildCache", "BuildCacheUsage")
    free_cache = [c for c in cache if not c.get("InUse")]
    cache_bytes = sum(_size(c.get("Size")) for c in free_cache if not c.get("Shared"))
    if free_cache and len(free_cache) == len(cache):
        cache_note = f"{len(cache)} {_plural(len(cache), 'wpis', 'wpisy', 'wpisów')}, żaden nie jest w użyciu"
    else:
        cache_note = f"{len(free_cache)} z {len(cache)} {_plural(len(cache), 'wpisu', 'wpisów', 'wpisów')} " \
                     "nie jest w użyciu"

    images = _items(df, "Images", "ImageUsage")
    unused = [i for i in images if int(i.get("Containers") or 0) == 0]
    image_bytes = sum(max(0, _size(i.get("Size")) - _size(i.get("SharedSize"))) for i in unused)
    image_note = f"{len(unused)} {_plural(len(unused), 'obraz', 'obrazy', 'obrazów')} bez żadnego kontenera"

    volumes = _items(df, "Volumes", "VolumeUsage")
    loose = [v for v in volumes if int((v.get("UsageData") or {}).get("RefCount") or 0) == 0]
    volume_bytes = sum(_size((v.get("UsageData") or {}).get("Size")) for v in loose)
    volume_note = f"{len(loose)} z {len(volumes)} · mogą trzymać dane baz"

    containers = _items(df, "Containers", "ContainerUsage")
    stopped = [c for c in containers if (c.get("State") or "").lower() not in ("running", "paused", "restarting")]
    stopped_bytes = sum(_size(c.get("SizeRw")) for c in stopped)
    stopped_note = f"{len(stopped)} · ich wolumeny zostają"

    rows = [
        {"id": "cache", "count": len(free_cache), "total": len(cache), "reclaimableBytes": cache_bytes,
         "totalBytes": sum(_size(c.get("Size")) for c in cache), "note": cache_note, "warn": False},
        {"id": "images", "count": len(unused), "total": len(images), "reclaimableBytes": image_bytes,
         "totalBytes": sum(_size(i.get("Size")) for i in images), "note": image_note, "warn": False},
        {"id": "volumes", "count": len(loose), "total": len(volumes), "reclaimableBytes": volume_bytes,
         "totalBytes": sum(_size((v.get("UsageData") or {}).get("Size")) for v in volumes), "note": volume_note,
         "warn": True},
        {"id": "stopped", "count": len(stopped), "total": len(containers), "reclaimableBytes": stopped_bytes,
         "totalBytes": sum(_size(c.get("SizeRw")) for c in containers), "note": stopped_note, "warn": False},
    ]
    for row in rows:
        row["label"] = LABELS[row["id"]]
    return {"categories": rows, "totalReclaimableBytes": sum(r["reclaimableBytes"] for r in rows),
            "layersBytes": _size(df.get("LayersSize"))}


def parse_selection(text: str) -> tuple[list[str], str]:
    """JSON z kategoriami: ["cache", "images"] albo {"cache": true, …}. (kategorie w kolejności prune, błąd)."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return [], "niepoprawny opis kategorii — nic nie usunięto"
    if isinstance(data, dict):
        chosen = [k for k, v in data.items() if v]
    elif isinstance(data, list):
        chosen = data
    else:
        return [], "niepoprawny opis kategorii — nic nie usunięto"
    if not all(isinstance(c, str) for c in chosen):
        return [], "niepoprawny opis kategorii — nic nie usunięto"
    unknown = sorted(set(chosen) - set(CATEGORIES))
    if unknown:
        return [], f"nieznana kategoria: {', '.join(unknown)} — nic nie usunięto"
    if not chosen:
        return [], "nic nie zaznaczono"
    return [c for c in PRUNE_ORDER if c in chosen], ""


def prune_request(category: str) -> tuple[str, str, dict]:
    """(metoda, ścieżka, zapytanie) dla kategorii."""
    if category == "images":
        # dangling=false: wszystkie obrazy bez kontenera, nie tylko „<none>”.
        return "POST", "/images/prune", {"filters": json.dumps({"dangling": ["false"]})}
    if category == "volumes":
        # all=true: od API 1.42 bez tego Docker usuwa tylko wolumeny anonimowe.
        return "POST", "/volumes/prune", {"filters": json.dumps({"all": ["true"]})}
    if category == "cache":
        return "POST", "/build/prune", {"all": "true"}
    if category == "stopped":
        return "POST", "/containers/prune", {}
    raise ValueError(category)


def prune_result(category: str, data) -> dict:
    """Szczegół jednej kategorii z odpowiedzi prune: {id, ok, reclaimedBytes, removed, error}."""
    data = data if isinstance(data, dict) else {}
    if category == "images":
        removed = len([d for d in data.get("ImagesDeleted") or [] if d.get("Deleted")])
    elif category == "volumes":
        removed = len(data.get("VolumesDeleted") or [])
    elif category == "cache":
        removed = len(data.get("CachesDeleted") or [])
    else:
        removed = len(data.get("ContainersDeleted") or [])
    return {"id": category, "label": LABELS[category], "ok": True,
            "reclaimedBytes": _size(data.get("SpaceReclaimed")), "removed": removed, "error": ""}
