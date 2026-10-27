"""Obszar tablicy w usłudze: D-Bus /Board, zapis kart z commitem i zmiany z zewnątrz.

- zapisy z D-Bus idą od razu, w wątku głównym (kilka małych plików i jeden commit w repozytorium
  z kilkudziesięcioma plikami — milisekundy), więc kolejne wywołanie widzi już nowy stan,
- migawka (JSON) jest gotowa w pamięci i odświeżana po każdej zmianie; Changed tylko wtedy,
  gdy się zmieniła,
- zmiany z zewnątrz (agent pisze plan w docs/, ręczna edycja karty): QFileSystemWatcher na
  katalogach i plikach tablicy, po DEBOUNCE_MS ciszy — commit „sync external changes” i Changed;
  dodatkowo co POLL_MS to samo sprawdzenie (zapis w miejscu bez zdarzenia, nowe podkatalogi,
  tablica założona poza usługą).
- Badge() i ShowWindow(id) to zaczepy okna Tide: plakietka widoku Tablica (ta sama reguła co
  apps/board/backend.py) i otwarcie okna na widoku Tablica, od razu na karcie — także z prośbą
  o okno „Co ma zrobić agent?” (`launch:K-12`, przycisk „Uruchom” w popupie kafelka),
- przy starcie dawne zaczyny szablonów trybów w flows/ dostają nową treść (migrate_flows: tylko
  pliki nietknięte przez użytkownika), a każda synchronizacja sprawdza, czy karty uruchomione
  w trybie „tylko spec” mają już gotową specyfikację (mark_ready_specs: karta wraca do Backlogu).
"""
import json
import os

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from .board.model import CARD_PREFIX, BoardError, card_num

LAUNCH_PREFIX = "launch:"  # ShowWindow("launch:K-12") — okno Tide od razu z „Co ma zrobić agent?”

DEBOUNCE_MS = 2_000
POLL_MS = 60_000
MAX_WATCHED_FILES = 2_000  # powyżej — tylko katalogi i odpytywanie co POLL_MS


class BoardMonitor(QObject):
    changed = pyqtSignal()

    def __init__(self, store, repos_source=None, derive=None, debounce_ms: int = DEBOUNCE_MS,
                 poll_ms: int = POLL_MS, launchers=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.launchers = launchers                        # worktrees.Launchers — okno Tide (ShowWindow)
        self.repos_source = repos_source or (lambda: [])  # funkcja() → ścieżki repozytoriów z katalogu projektów
        self.derive = derive                              # stan karty z koordynatora (PT-5) albo None
        self._json = ""
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._touched)
        self._watcher.fileChanged.connect(self._touched)
        self._debounce = QTimer(self, singleShot=True, interval=debounce_ms)
        self._debounce.timeout.connect(self.sync)
        self._poll = QTimer(self, interval=poll_ms)
        self._poll.timeout.connect(self.sync)

    def start(self) -> None:
        try:
            if self.store.migrate_flows():
                print("tide-daemon: tablica: szablony trybów zaktualizowane", flush=True)
        except Exception as exc:  # migracja to wygoda — RenderFlow i tak bierze nową treść
            print(f"tide-daemon: tablica: szablony trybów: {exc!r}", flush=True)
        self.sync()  # zmiany zrobione, gdy usługa nie działała, też trafiają do historii
        self._poll.start()

    def stop(self) -> None:
        self._poll.stop()
        self._debounce.stop()

    # --- D-Bus ---------------------------------------------------------------------------------

    def snapshot_json(self) -> str:
        if not self._json:
            self.publish()
        return self._json

    def create_card(self, request: str) -> str:
        return self._mutate(lambda: self.store.create_card(_object(request)))

    def update_card(self, card_id: str, request: str) -> str:
        return self._mutate(lambda: self.store.update_card(card_id, _object(request)))

    def move_card(self, card_id: str, column: str, order: int) -> str:
        return self._mutate(lambda: self.store.move_card(card_id, column, order))

    def upsert_project(self, request: str) -> str:
        return self._mutate(lambda: self.store.upsert_project(_object(request)))

    def upsert_tag(self, request: str) -> str:
        return self._mutate(lambda: self.store.upsert_tag(_object(request)))

    def render_flow(self, card_id: str, mode: str) -> str:
        return _reply(lambda: self.store.render_flow(card_id, mode))

    def render_launch(self, card_id: str, request: str) -> str:
        return _reply(lambda: self.store.render_launch(card_id, _object(request)))

    def badge_json(self) -> str:
        """Plakietka widoku Tablica w pasku okna Tide: {"text", "tone"} (pusty tekst — ukryta)."""
        try:
            snapshot = json.loads(self.snapshot_json())
        except ValueError:
            snapshot = {}
        text, tone = badge(snapshot)
        return json.dumps({"text": text, "tone": tone}, ensure_ascii=False)

    def show_window(self, card_id: str = "") -> bool:
        """Okno Tide na widoku Tablica; z ID karty (K-12) — od razu na niej, z `launch:K-12` — na niej
        z oknem „Co ma zrobić agent?”. Napis, który nie jest ID karty, pomijamy (samo okno). False,
        gdy okna nie ma (nie zainstalowane)."""
        text = (card_id or "").strip()
        launch = text.lower().startswith(LAUNCH_PREFIX)
        if launch:
            text = text[len(LAUNCH_PREFIX):].strip()
        item = ""
        if text:
            try:
                item = f"{LAUNCH_PREFIX if launch else ''}{CARD_PREFIX}{card_num(text)}"
            except BoardError:
                item = ""
        return bool(self.launchers and self.launchers.show_view("board", item))

    # --- odświeżanie ---------------------------------------------------------------------------

    def publish(self) -> None:
        try:
            repos = list(self.repos_source() or [])
        except Exception as exc:  # lista repozytoriów to dodatek — tablica działa bez niej
            print(f"tide-daemon: tablica: repozytoria: {exc!r}", flush=True)
            repos = []
        try:
            snapshot = self.store.snapshot(repos=repos, derive=self.derive)
        except Exception as exc:  # odczyt nie może położyć usługi
            print(f"tide-daemon: tablica: odczyt: {exc!r}", flush=True)
            snapshot = {"boardDir": self.store.root, "exists": False, "cards": [], "projects": [], "tags": [],
                        "repos": [], "errors": [{"path": self.store.root, "message": f"błąd odczytu: {exc}"}]}
        text = json.dumps(snapshot, ensure_ascii=False)
        if text != self._json:
            self._json = text
            self.changed.emit()

    def sync(self) -> None:
        """Commit zmian z zewnątrz, nowe ścieżki do obserwacji i świeża migawka."""
        try:
            if self.store.sync():
                print("tide-daemon: tablica: zapisano zmiany z zewnątrz", flush=True)
        except Exception as exc:  # np. zablokowany indeks — spróbujemy przy następnej okazji
            print(f"tide-daemon: tablica: commit zmian z zewnątrz: {exc}", flush=True)
        try:
            for card_id in self.store.mark_ready_specs():
                print(f"tide-daemon: tablica: {card_id}: specyfikacja gotowa", flush=True)
        except Exception as exc:  # znacznik to dodatek — spróbujemy przy następnej synchronizacji
            print(f"tide-daemon: tablica: gotowe specyfikacje: {exc!r}", flush=True)
        self._rewatch()
        self.publish()

    # --- wnętrze -------------------------------------------------------------------------------

    def _mutate(self, work) -> str:
        text = _reply(work)
        self._rewatch()
        self.publish()
        return text

    def _touched(self, _path: str) -> None:
        self._debounce.start()  # każda kolejna zmiana odsuwa commit — zapis z wielu plików to jeden commit

    def _rewatch(self) -> None:
        root = self.store.root
        wanted: list[str] = []
        files = 0
        if os.path.isdir(root):
            for folder, dirs, names in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d != ".git")
                wanted.append(folder)
                for name in sorted(names):
                    if files < MAX_WATCHED_FILES and not name.startswith("."):
                        wanted.append(os.path.join(folder, name))
                        files += 1
        current = set(self._watcher.directories()) | set(self._watcher.files())
        stale = [p for p in current if p not in set(wanted)]
        if stale:
            self._watcher.removePaths(stale)
        fresh = [p for p in wanted if p not in current]
        if fresh:
            self._watcher.addPaths(fresh)


def badge(snapshot) -> tuple[str, str]:
    """(tekst, ton) plakietki: „● N” w brzoskwini, gdy karty czekają na ciebie (stan z koordynatora),
    inaczej liczba kart w grze (bez Gotowych i Archiwum); zero — ("", "muted"), plakietka ukryta."""
    cards = snapshot.get("cards") if isinstance(snapshot, dict) else None
    waiting = total = 0
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict):
            continue
        column = card.get("boardColumn") or card.get("column") or "backlog"
        waiting += column == "waiting"
        total += column not in ("done", "archived")
    if waiting:
        return f"● {waiting}", "waiting"
    return (str(total) if total else ""), "muted"


def _object(request: str):
    try:
        data = json.loads(request or "")
    except ValueError:
        raise BoardError("niepoprawny JSON") from None
    if not isinstance(data, dict):
        raise BoardError("oczekiwano obiektu JSON")
    return data


def _reply(work) -> str:
    """Wynik jako JSON w kształcie /Tasks: {ok: true, …} albo {ok: false, error: <kod>, message: <tekst>}."""
    try:
        result = work()
    except BoardError as exc:
        result = _failure(exc.code, str(exc))
    except OSError as exc:
        print(f"tide-daemon: tablica: {exc!r}", flush=True)
        result = _failure("io-error", f"błąd zapisu albo odczytu na dysku: {exc.strerror or exc}")
    except Exception as exc:  # błąd w kodzie nie może położyć usługi
        print(f"tide-daemon: tablica: {exc!r}", flush=True)
        result = _failure("internal", "błąd usługi — szczegóły w dzienniku (journalctl --user -u tide-daemon)")
    return json.dumps(result, ensure_ascii=False)


def _failure(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message}
