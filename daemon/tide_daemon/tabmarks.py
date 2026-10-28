"""Znacznik stanu agenta na karcie Konsole: tytuł „projekt · gałąź” ze znakiem stanu i kolor karty.

Kolor paska tytułu okna według stanu nie jest możliwy przy dekoracji Tide (Aurorae v2 rysuje
kolory z SVG i nie czyta schematu kolorów okna — docs/decisions.md, #11), więc stan pokazujemy
na samej karcie terminala, w którym działa agent (#26, jak kafelki Tide):

- pracuje   → „● projekt · gałąź” i kolor karty teal (pasek pod tytułem),
  (sesja na modelu lokalnym ma w etykiecie dopisek „· lokalnie”, np. „◆ projekt · gałąź · lokalnie · czeka”),
- czeka     → „◆ projekt · gałąź · czeka” i kolor karty brzoskwiniowy,
- skończył  → „○ projekt · gałąź” bez koloru (puste kółko jak w widżetach, informacja, nie wezwanie),
- bezczynna, zakończona albo zniknęła → karta wraca do stanu sprzed oznaczenia.

Konsole nie umie pokolorować znaku ani tekstu pojedynczej karty (arkusz stylów paska kart nie ma
selektora karty), więc stan niosą kształt znaku, słowo „czeka” i pasek koloru, który Konsole rysuje
pod tytułem karty (`setTabColor`). Bez nazwy projektu (sesja bez katalogu) zostaje sam znak przed
dotychczasowym tytułem.

Tytuł karty ze znakiem trafia też do tytułu okna (gdy to bieżąca karta), a więc na pasek
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
DONE = "done"
PREFIXES = {WORKING: "● ", WAITING: "◆ ", DONE: "○ "}
# Dopisek za etykietą „projekt · gałąź” — samo słowo, bo kolor tekstu karty jest wspólny.
SUFFIXES = {WAITING: " · czeka"}
LOCAL = "lokalnie"  # sesja na modelu lokalnym (tasks/core.py: LOCAL_SUFFIX)
SEPARATOR = " · "
# Znaki z wcześniejszych wersji (◐ pracuje) — zdejmowane przy sprzątaniu po starej usłudze.
LEGACY_PREFIXES = ("◐ ",)
# Kolejność ważności, gdy w jednej karcie jest kilka sesji (np. agent uruchomiony z agenta).
PRIORITY = {DONE: 1, WORKING: 2, WAITING: 3}
NO_COLOR = ""  # Konsole zwraca "#000000", gdy karta nie ma koloru; pusty napis go zdejmuje


def tab_key(konsole: dict) -> str:
    """Klucz karty: usługa Konsole (z PID procesu) i ścieżka sesji, np. „org.kde.konsole-12 /Sessions/3”."""
    return f"{konsole['service']} {konsole['session']}"


def split_key(key: str) -> tuple[str, str]:
    service, _, path = key.partition(" ")
    return service, path


def session_label(session: dict) -> str:
    """„projekt · gałąź” sesji (sam projekt bez gałęzi), z modelem lokalnym „projekt · gałąź · lokalnie”
    — jak tytuł karty z okna „Nowe zadanie”; pusty, gdy projekt nie jest znany."""
    project = (session.get("project") or "").strip()
    branch = (session.get("branch") or "").strip()
    if not project:
        return ""
    label = f"{project}{SEPARATOR}{branch}" if branch and branch != project else project
    return label + SEPARATOR + LOCAL if session.get("localModel") is True else label


def tab_targets(sessions: list[dict], bound_pids: dict, procs) -> dict[str, tuple[str, str]]:
    """Karta Konsole → (stan, etykieta). Sesje pracujące, czekające i skończone z ustaloną kartą.

    W jednej karcie wygrywa „czeka” — to ono wymaga reakcji użytkownika — potem „pracuje”.
    """
    if procs is None:
        return {}
    out: dict[str, tuple[str, str]] = {}
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
        current = out.get(key)
        if PRIORITY[state] > PRIORITY.get(current[0] if current else None, 0):
            out[key] = (state, session_label(session))
    return out


def desired_tabs(sessions: list[dict], bound_pids: dict, procs) -> dict[str, str]:
    """Karta Konsole → sam stan (bez etykiety), np. do diagnostyki."""
    return {key: state for key, (state, _label) in tab_targets(sessions, bound_pids, procs).items()}


def strip_marker(text: str) -> str:
    """Tekst bez naszych prefiksów (także zdublowanych, np. po awarii usługi)."""
    changed = True
    while changed:
        changed = False
        for prefix in (*PREFIXES.values(), *LEGACY_PREFIXES):
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
        # Karty, których tytuł chwilowo należy do przejścia do okna (windows.Activator, PT-33):
        # nie oznaczamy ich ani nie przywracamy, dopóki go nie oddadzą.
        self.held: set[str] = set()
        self._desired: dict = {}

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

    def update(self, desired: dict) -> None:
        """Doprowadza karty do stanu `desired`: klucz karty → stan albo (stan, etykieta)."""
        self._desired = dict(desired)
        changed = False
        for key in sorted(set(self.records) - set(desired) - self.held):
            self._restore(key)
            changed = True
        for key, target in sorted(desired.items()):
            if key in self.held:
                continue
            state, label = (target, "") if isinstance(target, str) else target
            record = self.records.get(key)
            if record and record.get("state") == state and record.get("label", "") == label:
                continue  # bez zmian — nie pytamy Konsoli przy każdym odświeżeniu listy
            self._mark(key, state, label)
            changed = True
        if changed:
            self._save()

    def clear(self) -> None:
        """Przywraca wszystkie oznaczone karty (zamknięcie usługi)."""
        self.held.clear()
        self.update({})

    def hold(self, key: str) -> None:
        """Wstrzymuje znacznik karty: jej wyświetlany tytuł chwilowo zmienia ktoś inny."""
        self.held.add(key)

    def release(self, key: str) -> None:
        """Oddaje kartę i doprowadza ją do ostatniego żądanego stanu (mógł się zmienić w międzyczasie)."""
        if key in self.held:
            self.held.discard(key)
            self.update(self._desired)

    def _mark(self, key: str, state: str, label: str = "") -> None:
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
        if label:
            # Etykieta zastępuje tytuł (np. tytuł zadania, który ustawia sam agent) — jak na mocku.
            # „%” w nazwie gałęzi Konsole wzięłaby za zmienną formatu, więc go podwajamy.
            text = prefix + label + SUFFIXES.get(state, "")
            local = remote = text.replace("%", "%%")
            title = text
        else:
            local, remote = prefix + original["local"], prefix + original["remote"]
            title = prefix + strip_marker(current["title"])
        marked = {"local": local, "remote": remote, "color": self.colors.get(state, original["color"])}
        # Wyświetlany tytuł ustawiamy od razu: Konsole sama przelicza go z formatu dopiero przy
        # następnej aktywności w terminalu, a czekający agent nic już nie wypisuje. setTitle
        # nadpisuje przy tym oba formaty, więc zaraz po nim ustawiamy formaty ze znacznikiem.
        ok = self.konsole.apply(key, title=title, local=marked["local"], remote=marked["remote"],
                                color=marked["color"])
        if not ok:
            self.records.pop(key, None)
            return
        self.records[key] = {"state": state, "label": label, **original, "markedLocal": marked["local"],
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
        # Przy etykiecie wyświetlany tytuł był w całości nasz — Konsole przeliczy go z przywróconego
        # formatu przy najbliższej aktywności, a do tego czasu pokazujemy sam format bez zmiennych.
        restored_title = strip_marker(current["title"])
        if record.get("label"):
            restored_title = record["local"] if "%" not in record["local"] else record.get("label", "")
        self.konsole.apply(
            key,
            title=restored_title if formats_ours else None,
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
