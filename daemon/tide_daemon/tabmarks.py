"""Znacznik stanu agenta na karcie Konsole: prefiks w tytule i kolor karty.

Kolor paska tytułu okna według stanu nie jest możliwy przy dekoracji Tide (Aurorae v2 rysuje
kolory z SVG i nie czyta schematu kolorów okna — docs/decisions.md, #11), więc stan pokazujemy
na samej karcie terminala, w którym działa agent:

- pracuje → „◐ ” przed tytułem karty i kolor karty teal,
- czeka   → „◆ ” przed tytułem karty i kolor karty brzoskwiniowy,
- bezczynna, zakończona albo zniknęła → karta wraca do stanu sprzed oznaczenia.

Tytuł karty z prefiksem trafia też do tytułu okna (gdy to bieżąca karta), a więc na pasek
tytułu, do paska zadań i przełącznika okien.

Czysta logika bez Qt: Konsole jest za interfejsem (`read`, `apply`), a oryginalne formaty
tytułów i kolory są zapisywane w pliku stanu, żeby restart usługi mógł po sobie posprzątać.
Wiązanie z magistralą jest w konsole.py.
"""
import json
import os

from . import windows

WORKING = "working"
WAITING = "waiting"
PREFIXES = {WORKING: "◐ ", WAITING: "◆ "}
# Kolejność ważności, gdy w jednej karcie jest kilka sesji (np. agent uruchomiony z agenta).
PRIORITY = {WORKING: 1, WAITING: 2}
NO_COLOR = ""  # Konsole zwraca "#000000", gdy karta nie ma koloru; pusty napis go zdejmuje


def tab_key(konsole: dict) -> str:
    """Klucz karty: usługa Konsole (z PID procesu) i ścieżka sesji, np. „org.kde.konsole-12 /Sessions/3”."""
    return f"{konsole['service']} {konsole['session']}"


def split_key(key: str) -> tuple[str, str]:
    service, _, path = key.partition(" ")
    return service, path


def desired_tabs(sessions: list[dict], bound_pids: dict, procs) -> dict[str, str]:
    """Karta Konsole → stan, który ma pokazywać. Tylko sesje pracujące i czekające z ustaloną kartą.

    W jednej karcie wygrywa „czeka” — to ono wymaga reakcji użytkownika.
    """
    if procs is None:
        return {}
    out: dict[str, str] = {}
    for session in sessions:
        state = session.get("state")
        if state not in PREFIXES:
            continue
        pid = bound_pids.get(session.get("id")) or session.get("pid")
        target = windows.target_for(procs, pid) if pid else None
        konsole = (target or {}).get("konsole")
        if not konsole:
            continue
        key = tab_key(konsole)
        if PRIORITY[state] > PRIORITY.get(out.get(key), 0):
            out[key] = state
    return out


def strip_marker(text: str) -> str:
    """Tekst bez naszych prefiksów (także zdublowanych, np. po awarii usługi)."""
    changed = True
    while changed:
        changed = False
        for prefix in PREFIXES.values():
            if text.startswith(prefix):
                text = text[len(prefix):]
                changed = True
    return text


def normalize_color(color: str, ours: set[str]) -> str:
    """Kolor karty jako oryginał do przywrócenia: brak koloru i nasze kolory → bez koloru."""
    color = (color or "").lower()
    if color in ("", "#000000") or color in ours:
        return NO_COLOR
    return color


class TabMarker:
    """Oznacza karty Konsole według stanu sesji i przywraca je, gdy stan mija.

    konsole: obiekt z metodami
      read(key) -> {"local": str, "remote": str, "title": str, "color": str} albo None (karty już nie ma),
      apply(key, title=None, local=None, remote=None, color=None) -> bool (None = bez zmiany).
    colors: {"working": "#rrggbb", "waiting": "#rrggbb"}; brak koloru → tylko prefiks w tytule.
    store: plik stanu z oryginałami (None = tylko w pamięci).
    """

    def __init__(self, konsole, colors: dict[str, str] | None = None, store: str | None = None):
        self.konsole = konsole
        self.colors = {k: v.lower() for k, v in (colors or {}).items() if v}
        self.store = store
        self.records: dict[str, dict] = self._load()

    # --- stan ---------------------------------------------------------------------------

    def _load(self) -> dict[str, dict]:
        if not self.store:
            return {}
        try:
            with open(self.store, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}

    def _save(self) -> None:
        if not self.store:
            return
        try:
            if not self.records:
                if os.path.exists(self.store):
                    os.remove(self.store)
                return
            os.makedirs(os.path.dirname(self.store), exist_ok=True)
            tmp = self.store + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.records, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.store)
        except OSError as exc:
            print(f"tide-daemon: nie udało się zapisać stanu kart Konsole: {exc}", flush=True)

    # --- zmiany -------------------------------------------------------------------------

    def update(self, desired: dict[str, str]) -> None:
        """Doprowadza karty do stanu `desired` (klucz karty → working/waiting)."""
        changed = False
        for key in sorted(set(self.records) - set(desired)):
            self._restore(key)
            changed = True
        for key, state in sorted(desired.items()):
            record = self.records.get(key)
            if record and record.get("state") == state:
                continue  # bez zmian — nie pytamy Konsoli przy każdym odświeżeniu listy
            self._mark(key, state)
            changed = True
        if changed:
            self._save()

    def clear(self) -> None:
        """Przywraca wszystkie oznaczone karty (zamknięcie usługi)."""
        self.update({})

    def _mark(self, key: str, state: str) -> None:
        current = self.konsole.read(key)
        if current is None:
            self.records.pop(key, None)  # karta albo cała Konsole już nie istnieje
            return
        record = self.records.get(key)
        ours = set(self.colors.values())
        if record is None:
            # Pierwsze oznaczenie: oryginał bez naszych śladów (np. po awarii usługi bez pliku stanu).
            original = {"local": strip_marker(current["local"]), "remote": strip_marker(current["remote"]),
                        "color": normalize_color(current["color"], ours)}
        else:
            original = {k: record[k] for k in ("local", "remote", "color")}
            # Użytkownik zmienił tytuł albo kolor karty w trakcie — to jest nowy oryginał.
            if current["local"] != record.get("markedLocal"):
                original["local"] = strip_marker(current["local"])
            if current["remote"] != record.get("markedRemote"):
                original["remote"] = strip_marker(current["remote"])
            if normalize_color(current["color"], set()) != record.get("markedColor", NO_COLOR):
                original["color"] = normalize_color(current["color"], ours)

        prefix = PREFIXES[state]
        marked = {"local": prefix + original["local"], "remote": prefix + original["remote"],
                  "color": self.colors.get(state, original["color"])}
        # Wyświetlany tytuł ustawiamy od razu: Konsole sama przelicza go z formatu dopiero przy
        # następnej aktywności w terminalu, a czekający agent nic już nie wypisuje. setTitle
        # nadpisuje przy tym oba formaty, więc zaraz po nim ustawiamy formaty ze znacznikiem.
        ok = self.konsole.apply(key, title=prefix + strip_marker(current["title"]),
                                local=marked["local"], remote=marked["remote"], color=marked["color"])
        if not ok:
            self.records.pop(key, None)
            return
        self.records[key] = {"state": state, **original, "markedLocal": marked["local"],
                             "markedRemote": marked["remote"], "markedColor": marked["color"]}

    def _restore(self, key: str) -> None:
        record = self.records.pop(key)
        current = self.konsole.read(key)
        if current is None:
            return
        formats_ours = (current["local"] == record.get("markedLocal")
                        and current["remote"] == record.get("markedRemote"))
        color_ours = normalize_color(current["color"], set()) == record.get("markedColor", NO_COLOR)
        if not formats_ours and not color_ours:
            return  # użytkownik przejął kartę — zostawiamy jego tytuł i kolor
        self.konsole.apply(
            key,
            title=strip_marker(current["title"]) if formats_ours else None,
            local=record["local"] if formats_ours else None,
            remote=record["remote"] if formats_ours else None,
            color=record["color"] if color_ours else None,
        )


def state_colors(palette: dict) -> dict[str, str]:
    """Kolory stanów z palety: semantic.working/waiting wskazują nazwy pasteli."""
    semantic = palette.get("semantic", {})
    pastel = palette.get("pastel", {})
    out = {}
    for state in (WORKING, WAITING):
        color = pastel.get(semantic.get(state, ""), "")
        if color:
            out[state] = color.lower()
    return out


def palette_candidates() -> list[str]:
    """Gdzie szukać palety: $TIDE_PALETTE, kopia obok zainstalowanej usługi, repozytorium."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # katalog tide-daemon.py
    out = []
    if os.environ.get("TIDE_PALETTE"):
        out.append(os.environ["TIDE_PALETTE"])
    out.append(os.path.join(base, "palette.json"))
    out.append(os.path.join(os.path.dirname(base), "theme", "palette", "mist.json"))
    return out


def load_state_colors(paths: list[str] | None = None) -> dict[str, str]:
    for path in paths if paths is not None else palette_candidates():
        try:
            with open(path, encoding="utf-8") as fh:
                return state_colors(json.load(fh))
        except (OSError, ValueError):
            continue
    return {}


def default_store() -> str:
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "tide", "konsole-tabs.json")
