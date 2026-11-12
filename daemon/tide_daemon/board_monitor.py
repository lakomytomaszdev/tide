"""Obszar tablicy w usłudze: D-Bus /Board, zapis kart z commitem i zmiany z zewnątrz.

- zapisy z D-Bus idą od razu, w wątku głównym (kilka małych plików i jeden commit w repozytorium
  z kilkudziesięcioma plikami — milisekundy), więc kolejne wywołanie widzi już nowy stan,
- migawka (JSON) jest gotowa w pamięci i odświeżana po każdej zmianie; Changed tylko wtedy,
  gdy się zmieniła,
- zmiany z zewnątrz (agent pisze plan w docs/, ręczna edycja karty): QFileSystemWatcher na
  katalogach i plikach tablicy, po DEBOUNCE_MS ciszy — commit „sync external changes” i Changed;
  dodatkowo co POLL_MS to samo sprawdzenie (zapis w miejscu bez zdarzenia, nowe podkatalogi,
  tablica założona poza usługą). K-98 #6: bez `flow/` (dziennik i wyjście kroków pisze usługa — UNWATCHED_TOP)
  i bez pełnego przejścia drzewa przy każdej synchronizacji i zapisie: zdarzenie katalogu zaznacza go do
  przejrzenia, a `_rewatch` przegląda tylko zaznaczone katalogi i nowe poddrzewa; pełne przejście — przy
  starcie, po założeniu Tablicy i co POLL_MS (także ponowienie ścieżek odrzuconych przez limit obserwacji).
- stan kart z pracy agentów (PT-5, board/derive.py): migawka z plików jest w pamięci (`_base`), a po
  zmianie w /Agents albo /Coordination (rederive, najwyżej co REDERIVE_MS) przeliczamy tylko pola
  `derived` i `boardColumn` — bez ponownego czytania repozytorium tablicy,
- Badge() i ShowWindow(id) to zaczepy okna Tide: plakietka widoku Tablica (ta sama reguła co
  apps/board/backend.py) i otwarcie okna na widoku Tablica, od razu na karcie — także z prośbą
  o okno „Co ma zrobić agent?” (`launch:K-12`, przycisk „Uruchom” w popupie kafelka),
- przy starcie dawne zaczyny szablonów trybów w flows/ dostają nową treść (migrate_flows: tylko
  pliki nietknięte przez użytkownika), a każda synchronizacja sprawdza, czy karty uruchomione
  z wejściem „Tylko specyfikacja” mają już gotową specyfikację (mark_ready_specs: karta wraca do Backlogu),
- trwałe połączenie karty z sesją (PT-28): karta bez `session`, którą przeliczenie połączyło z sesją
  uruchomioną z karty albo jednoznacznie po gałęzi (`Context.sticky`), dostaje tę sesję w pliku
  (store.link_session, commit „K-1: link session claude:…”) — później sesja może wejść do innego
  katalogu, a karta jej nie zgubi. Zapis idzie po przeliczeniu (QTimer), ta sama para karta–sesja
  najwyżej raz na LINK_RETRY_S.
- CardDocs(id) to plan i specyfikacja karty do panelu (board/docs.py): wejście (karta z migawki,
  projekt, tablica koordynatora z /Coordination) zbieramy w wątku głównym, pliki czytamy w jednym
  wątku roboczym, a odpowiedź D-Bus idzie później z wątku głównego (opóźniona odpowiedź jak w /Docker);
  kolejne prośby o tę samą kartę w czasie odczytu czekają na ten sam wynik (bez kolejki odczytów).
- lista „Praca” (PT-40, board/work.py, Work()): aktywne karty i żywe sesje agentów bez karty liczone
  w tym samym przeliczeniu i z tego samego kontekstu derive co kolumny kart — kafelek Praca i Tablica
  nie mogą się rozjechać. Ma własny sygnał (work_changed → WorkChanged), więc Changed — i odczyt całej
  migawki w oknie Tide i raporcie — idzie tylko przy zmianie migawki. WorkChanged idzie od razu, gdy zmieni
  się lista bez pól zmiennych (grupa, pytanie, liczby — work.stable_key), a sama ostatnia czynność agenta
  najwyżej co WORK_VOLATILE_MS. CreateCard z sesją, którą ma już aktywna karta (podwójne „+ karta”),
  oddaje tę kartę zamiast zakładać drugą.
- synchronizacja z YouTrackiem (PT-7, ytsync_monitor.py, board/ytsync.py): każde przeliczenie migawki
  przekazuje karty (z kolumną wyświetlaną) do `youtrack.observe`, a migawka dostaje stan synchronizacji przy
  kartach (`youtrackSync`), ogólny (`youtrack`) i konflikty w `errors`. Import zgłoszeń (ImportYouTrack)
  zakłada karty w Backlogu tą samą drogą co CreateCard, bez duplikatów (po polu `youtrack`).
- K-98 #2 — opublikowana, spójna migawka: Snapshot() (także Panel karty — `cards[].derived.flow`) oddaje ostatnią
  opublikowaną wersję z polami `version` (numer publikacji, rośnie przy każdej zmianie treści) i `readState`
  (`ready`; przed pierwszą publikacją `preparing` zamiast pustej Tablicy). Publikacja powstaje pod bramką przepływu
  karty (`CardFlowMonitor.read_gate`) i nie bierze blokady dziennika: dzienniki czyta w trybie prezentacji (pełne
  zdarzenia, własna pamięć), a licznik epok akcji (seqlock) odrzuca odczyt, w trakcie którego trwała albo zmieniła się
  akcja z kolejki — żadna wersja nie pokazuje akcji w połowie. Wczytanie Tablicy w publikacji też nie czeka: idzie
  w `store.without_waiting()`, więc transakcja Tablicy trzymana przez mutację (StartCard w `git worktree add`,
  scalenie, tick) albo inny proces odkłada publikację zamiast zatrzymać wątek główny (jawny Snapshot() czeka na nią
  najwyżej SNAPSHOT_WAIT_S — krótka transakcja ticku; publikacje w tle wcale). Zapisy Tablicy z zegara (sync,
  mark_ready_specs, link_session) też nie czekają i wracają przy następnej okazji. Odłożona publikacja wraca po
  PUBLISH_RETRY_MS, a odczyty dostają poprzednią wersję. Zapisy ticku widać zapis po zapisie (tick nie jest akcją):
  jedna wersja może złożyć Tablicę z chwili po jednym zapisie ticku z dziennikiem sprzed kolejnego — każdy z nich
  jest pełny, jak przed K-98 #2; atomowość „cała akcja albo nic” dotyczy akcji z kolejki. Dane odczytu służą
  wyłącznie prezentacji; operacje sprawdzają historię i prawa świeżo w kolejce mutacji. Zapisy Tablicy z wątku
  głównego (CreateCard, UpdateCard, MoveCard, UpsertProject, sync) nadal biorą transakcję z czekaniem.
"""
import contextlib
import copy
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import NamedTuple

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from . import call_origin
from .board import work as work_list
from .board.docs import CardDocs
from .board.model import CARD_PREFIX, BoardError, card_num
from .board.readcontext import BoardRead
from .board.store import BoardBusy
from .board import ytsync
from .coordination.youtrack import issue_key

LAUNCH_PREFIX = "launch:"  # ShowWindow("launch:K-12") — okno Tide od razu z „Co ma zrobić agent?”

DEBOUNCE_MS = 2_000
POLL_MS = 60_000
REDERIVE_MS = 500  # zmiany sesji przychodzą seriami (co 250 ms przy pracy agenta) — przeliczenie najwyżej tak często
MAX_WATCHED_FILES = 2_000  # powyżej — tylko katalogi i odpytywanie co POLL_MS
# K-98 #6 (PT-362): katalogi główne Tablicy bez obserwacji plików. `flow/` (dziennik kart i wyjście kroków, w produkcji
# ~95 % katalogów i plików Tablicy) pisze sama usługa: commit i nowa migawka idą z `CardFlowMonitor._drain` po każdej
# akcji i ticku, zmianę dziennika z zewnątrz (revert, ręczna edycja) wykrywa tick (`Journal.check`), a resztę zbiera
# odpytywanie co POLL_MS (commit „sync external changes” i migawka). Obserwacja `flow/` dawała tylko pełne
# przejście drzewa na wątku głównym przy każdym zapisie dziennika.
UNWATCHED_TOP = frozenset({".git", "flow"})
WORK_VOLATILE_MS = 10_000  # zmiana samej ostatniej czynności agenta na liście „Praca” — WorkChanged najwyżej tak często
LINK_RETRY_S = 60.0  # nieudane utrwalenie sesji w karcie ponawiamy najwyżej tak często
PUBLISH_RETRY_MS = 50  # publikacja odłożona (akcja w toku, zajęta Tablica) — kolejna próba (bez czekania)
SNAPSHOT_WAIT_S = 0.25  # Snapshot() czeka na transakcję Tablicy najwyżej tyle (krótkie transakcje ticku), potem stara wersja


class Published(NamedTuple):
    """Opublikowana wersja stanu Tablicy (K-98 #2) dla odczytów poza wątkiem głównym (BoxStatus).
    Obiekty są świeże przy każdym wywołaniu `published()` i nie są zmieniane później."""
    version: int
    snapshot: dict
    board: object      # projects: slug → projekt z migawki; cards: numer → obiekt z `project`
    box_wait: dict     # karta z dziennikiem przepływu → `boxWait`


def derived_cache_path(env=None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "tide", "board-derived.json")


def _load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:   # pamięć stanu to dodatek — tablica działa bez niej
        print(f"tide-daemon: tablica: zapis stanu kart: {exc!r}", flush=True)


class BoardMonitor(QObject):
    changed = pyqtSignal()
    work_changed = pyqtSignal()           # lista „Praca” (Work()) się zmieniła — sygnał WorkChanged
    _rederive_requested = pyqtSignal()   # z dowolnego wątku (np. wynik gita w tle) — kolejka Qt do wątku głównego
    _docs_ready = pyqtSignal(object, object)   # (ID karty, future) — wynik CardDocs z wątku roboczego

    def __init__(self, store, repos_source=None, derive=None, debounce_ms: int = DEBOUNCE_MS,
                 poll_ms: int = POLL_MS, launchers=None, rederive_ms: int = REDERIVE_MS, coordinators=None,
                 docs=None, youtrack=None, proc_root: str = "/proc", parent=None, watcher=None):
        super().__init__(parent)
        self.store = store
        # /proc do sprawdzenia nadawcy zatwierdzenia kontraktu testów (call_origin, PT-286); atrapa tylko w testach.
        self.proc_root = proc_root
        # Synchronizacja z YouTrackiem (ytsync_monitor.YouTrackSyncMonitor) albo None (--no-youtrack).
        self.youtrack = youtrack
        # CardDocs: tablice koordynatorów z /Coordination (funkcja() → lista) i czytnik dokumentów kart.
        self.coordinators = coordinators or (lambda: [])
        self.docs = docs or CardDocs(store.root)
        self._docs_pool: ThreadPoolExecutor | None = None
        self._docs_waiting: dict[str, list] = {}     # ID karty → odpowiedzi czekające na trwający odczyt
        self._docs_ready.connect(self._deliver_docs)
        self._current: dict | None = None                 # migawka z `derived` (to, co oddaje Snapshot())
        self._version = 0                                 # numer opublikowanej wersji (K-98 #2); 0 — jeszcze żadnej
        self._content = ""                                # treść ostatniej publikacji bez pól wersji
        self._box_wait: dict = {}                         # boxWait kart z dziennikiem z tej samej publikacji
        self._pending = None                              # odłożona publikacja: "publish" | "apply" | None
        self._retry = QTimer(self, singleShot=True, interval=PUBLISH_RETRY_MS)
        self._retry.timeout.connect(self._retry_pending)
        self._plans_mark = None
        self.launchers = launchers                        # worktrees.Launchers — okno Tide (ShowWindow)
        self.repos_source = repos_source or (lambda: [])  # funkcja() → ścieżki repozytoriów z katalogu projektów
        # stan karty z pracy agentów (PT-5): board/derive.CardDeriver (z context()) albo funkcja(karta) → dict|None
        self.derive = derive
        self._json = ""
        self._work_json = ""                              # lista „Praca” (Work()) z tego samego przeliczenia
        self._work_key = ""                               # ta lista bez pól zmiennych (work.stable_key)
        self._work_later = QTimer(self, singleShot=True, interval=WORK_VOLATILE_MS)
        self._work_later.timeout.connect(self.work_changed)
        self._base: dict | None = None                    # migawka z plików bez stanu z pracy agentów
        # Obserwacja plików Tablicy (K-98 #6): obserwator tylko w wątku głównym (tu powstaje; `watcher` — atrapa
        # w testach). Pamięć obserwowanych katalogów: katalog → obserwowane podkatalogi i pliki. Zdarzenie katalogu
        # zaznacza go do ponownego przejrzenia (`_dirty`); `_rewatch` przegląda tylko zaznaczone katalogi i nowe
        # poddrzewa, pełne przejście — przy pierwszym starcie, po (ponownym) założeniu katalogu Tablicy i co POLL_MS.
        self._watcher = watcher if watcher is not None else QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._dir_touched)
        self._watcher.fileChanged.connect(self._touched)
        self._tree: dict[str, set[str]] = {}       # obserwowany katalog → jego obserwowane podkatalogi
        self._tree_files: dict[str, set[str]] = {}  # obserwowany katalog → jego obserwowane pliki
        self._watched_files = 0
        self._dirty: set[str] = set()
        self._refused: set[str] = set()     # ścieżki odrzucone przez obserwatora (limit) — tylko odpytywanie
        # Liczniki dla testów i diagnozy: przejrzane katalogi (scandir), pełne przejścia, odrzucone ścieżki.
        self.watch_stats = {"visitedDirs": 0, "fullScans": 0, "refused": 0}
        self._debounce = QTimer(self, singleShot=True, interval=debounce_ms)
        self._debounce.timeout.connect(self.sync)
        self._poll = QTimer(self, interval=poll_ms)
        self._poll.timeout.connect(self._poll_sync)
        self._rederive = QTimer(self, singleShot=True, interval=rederive_ms)
        self._rederive.timeout.connect(self._apply)
        self._rederive_requested.connect(self.rederive)
        # Ostatni wyliczony stan kart spoza przepływu (plany i sesje koordynatorów) — po restarcie usługi okno
        # dostaje go od razu, zanim przeliczenie z pracy agentów będzie gotowe (04.11: K-17 skakała na kilka
        # sekund do „Do zrobienia”, bo zapisana kolumna wygrywała z brakującym stanem).
        self._derived_cache_path = derived_cache_path()
        self._derived_cache: dict = _load_cache(self._derived_cache_path)
        self._derive_ready = False
        self._links: list[tuple[str, str, str, str]] = []  # (karta, sesja, sposób, gałąź) do utrwalenia
        self._link_tried: dict[tuple[str, str], float] = {}
        # Przepływ karty (PT-58, cardflow_monitor.CardFlowMonitor) albo None: karta z dziennikiem dostaje stan
        # z dziennika, nie z pracy agentów (derive); MoveCard takiej karty wstrzymuje ją albo kończy.
        self.flow = None
        if youtrack is not None:
            youtrack.changed.connect(self._apply)  # wynik zapisu albo konflikt — nowy stan przy kartach

    def start(self) -> None:
        try:
            if self.store.migrate_flows():
                print("tide-daemon: tablica: szablony trybów zaktualizowane", flush=True)
        except Exception as exc:  # migracja to wygoda — RenderFlow i tak bierze nową treść
            print(f"tide-daemon: tablica: szablony trybów: {exc!r}", flush=True)
        self.sync()  # zmiany zrobione, gdy usługa nie działała, też trafiają do historii
        self._poll.start()
        if self.youtrack is not None:
            self.youtrack.start()

    def stop(self) -> None:
        if self.flow is not None:
            self.flow.stop()
        if self.youtrack is not None:
            self.youtrack.stop()
        self._poll.stop()
        self._debounce.stop()
        self._rederive.stop()
        self._work_later.stop()
        self._retry.stop()
        if self._docs_pool is not None:
            self._docs_pool.shutdown(wait=False, cancel_futures=True)
            self._docs_pool = None
        if hasattr(self.derive, "stop"):
            self.derive.stop()  # wątek gita (sprawdzanie scalenia) nie trzyma zamknięcia usługi

    def rederive_later(self) -> None:
        """To samo co rederive(), ale bezpieczne z innego wątku (wynik gita w tle — board/derive.py)."""
        self._rederive_requested.emit()

    def rederive(self) -> None:
        """Zmiana w /Agents albo /Coordination: przeliczenie stanu kart z migawki w pamięci, najwyżej
        co REDERIVE_MS (pierwsza zmiana z serii uruchamia odliczanie, kolejne tylko na nie czekają)."""
        if (self.derive is not None or self.flow is not None) and not self._rederive.isActive():
            self._rederive.start()

    # --- D-Bus ---------------------------------------------------------------------------------

    def snapshot_json(self) -> str:
        # Plany mogą powstać w repozytorium projektu poza tablicą; przy jawnym odświeżeniu
        # odczytaj je ponownie, aby otwarte okno zlecenia dostało aktualnych kandydatów.
        # K-98 #2: publikacja odłożona przez trwającą akcję — ostatnia opublikowana wersja albo „preparing”.
        # Zakończone, a jeszcze niedoręczone wyniki z kolejki najpierw trafiają do migawki (bez czekania — publikacja
        # nie bierze blokady dziennika), żeby odczyt po zapisie widział jego skutek.
        # Jawny odczyt czeka na transakcję Tablicy najwyżej SNAPSHOT_WAIT_S łącznie (jeden termin na wywołanie): krótka
        # transakcja ticku nie zostawia mu wersji sprzed zapisu, a długa mutacja daje poprzednią wersję po terminie.
        deadline = time.monotonic() + SNAPSHOT_WAIT_S
        left = lambda: max(0.0, deadline - time.monotonic())
        settle = getattr(self.flow, "settle", None) if self.flow is not None else None
        if settle is not None:
            settle()
        # Udane ponowienie już wczytało Tablicę i plany: znacznik planów ustawiony po nim, więc ta sama zmiana planów
        # nie publikuje drugi raz (jedno wczytanie Tablicy na Snapshot(), PT-385).
        if self._pending is not None and self._retry_pending(left()):
            self._plans_mark = self._plans_stamp()
        mark = self._plans_stamp()
        if not self._json or mark != self._plans_mark:
            if self.publish(left()):
                self._plans_mark = self._plans_stamp()
        return self._json or self._preparing_json()

    def _preparing_json(self) -> str:
        """Snapshot() przed pierwszą publikacją (K-98 #2): jawne „preparing”, nie pusta, gotowa Tablica."""
        return json.dumps({"version": 0, "readState": "preparing", "boardDir": self.store.root,
                           "exists": bool(self.store.exists()), "cards": [], "projects": [], "tags": [], "repos": [],
                           "errors": []}, ensure_ascii=False)

    def published(self) -> Published | None:
        """Ostatnia opublikowana wersja stanu dla odczytów w innym wątku (BoxStatus); None — jeszcze żadnej.
        Budowana w wątku głównym z obiektów tej publikacji, bez wczytywania Tablicy i bez blokady dziennika."""
        if self._current is None or not self._version:
            return None
        snapshot = self._current
        projects = {p["slug"]: p for p in snapshot.get("projects") or [] if isinstance(p, dict) and p.get("slug")}
        cards = {}
        for card in snapshot.get("cards") or []:
            try:
                cards[card_num(card.get("id") or "")] = SimpleNamespace(project=card.get("project") or "")
            except (BoardError, AttributeError):
                continue
        return Published(self._version, snapshot, SimpleNamespace(projects=projects, cards=cards),
                         dict(self._box_wait))

    def _plans_stamp(self):
        from .coordination import plan
        stamps = []
        for project in (self._base or {}).get("projects") or []:
            if not project.get("repo"):
                continue
            for folder in plan.plan_folders([project["repo"]], self.store.root):
                try:
                    with os.scandir(folder) as entries:
                        for entry in entries:
                            if not plan.NAME_RE.fullmatch(entry.name):
                                continue
                            st = entry.stat(follow_symlinks=False)
                            stamps.append((entry.path, st.st_size, st.st_mtime_ns))
                except OSError:
                    continue
        return tuple(sorted(stamps))

    def work_json(self) -> str:
        """Work(): lista „Praca” (board/work.py) — {exists, items, counts}."""
        if not self._work_json:
            self.publish()
        return self._work_json or json.dumps(
            {"readState": "preparing", "exists": bool(self.store.exists()), "items": [],
             "counts": {**{g: 0 for g in work_list.GROUPS}, "total": 0, "sessions": 0}}, ensure_ascii=False)

    def create_card(self, request: str) -> str:
        held = self._card_holding_session(request)
        if held:
            return json.dumps({"ok": True, "id": held, "existing": True, "commitError": ""}, ensure_ascii=False)
        return self._mutate(lambda: self.store.create_card(_object(request)))

    def _card_holding_session(self, request: str) -> str:
        """ID aktywnej karty (nie w Gotowych ani Archiwum), która ma już w polu `session` sesję z żądania —
        drugie „+ karta” (podwójne kliknięcie, stara lista w kafelku) nie zakłada duplikatu."""
        try:
            wanted = str(json.loads(request or "").get("session") or "").strip()
        except (ValueError, AttributeError):
            return ""
        if not wanted:
            return ""
        if self._current is None:
            self.snapshot_json()
        raw = wanted.partition(":")[2] if ":" in wanted else wanted
        for card in (self._current or {}).get("cards") or []:
            if not isinstance(card, dict) or (card.get("column") or "") in ("done", "archived"):
                continue
            held = str(card.get("session") or "").strip()
            if held and (held == wanted or held == raw or held.partition(":")[2] == raw):
                return str(card.get("id") or "")
        return ""

    def update_card(self, card_id: str, request: str) -> str:
        return self._mutate(lambda: self.store.update_card(card_id, _object(request)))

    def move_card(self, card_id: str, column: str, order: int) -> str:
        shown = self._shown_column(card_id)
        text = self._mutate(lambda: self.store.move_card(card_id, column, order, shown))
        if self.flow is not None:
            try:
                reply = json.loads(text)
            except ValueError:
                reply = {}
            if reply.get("ok") and reply.get("id") and self.flow.has_journal(reply["id"]):
                # Backlog wstrzymuje, Gotowe i Archiwum kończą; skąd — „do poprawy” tylko z kolumn wyliczonych.
                card = self.store.load().cards.get(card_num(reply["id"]))
                self.flow.moved(reply["id"], str(column).strip().lower(), shown, card.move_id if card else "")
        return text

    def start_card(self, card_id: str, request: str, reply) -> None:
        """StartCard(id, json): zlecenie karty w przepływie karty (wejście „Od razu”) — reply(json) później."""
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.start_card(card_id, request, reply)

    def list_card_branches(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.list_card_branches(card_id, request, reply)

    def suggest_card_base(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.suggest_card_base(card_id, request, reply)

    def enter_step(self, card_id: str, step_id: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.enter_step(card_id, step_id, reply)

    def talk_to_caretaker(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.talk_to_caretaker(card_id, request, reply)

    def abandon_step(self, card_id: str, step_id: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.abandon_step(card_id, step_id, reply)

    def resolve_card_review(self, card_id: str, choice: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.resolve_review(card_id, choice, reply)

    def resolve_card_step(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.resolve_step(card_id, request, reply)

    def revoke_ticket_consent(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.revoke_ticket_consent(card_id, request, reply)

    def resolve_card_caretaker(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.resolve_caretaker(card_id, request, reply)

    def preview_step_fault(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.preview_step_fault(card_id, request, reply)

    def add_card_step_note(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.add_step_note(card_id, request, reply)

    def report_step_fault(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.report_step_fault(card_id, request, reply)

    def set_card_paused(self, card_id: str, paused: bool, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.set_paused(card_id, paused, reply)

    def get_card_flow_settings(self, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.get_settings(reply)

    def set_card_flow_settings(self, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.set_settings(request, reply)

    def edit_card_plan(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony"), ensure_ascii=False))
            return
        self.flow.edit_plan(card_id, request, reply)

    def approve_card_plan(self, card_id: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.approve_plan(card_id, reply)

    def approve_card_environment(self, card_id: str, yaml_text: str, only_plan: bool, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.approve_environment(card_id, yaml_text, only_plan, reply)

    def box_status(self, slug: str, reply, card: str = "") -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.box_status(slug, reply, card)

    def stop_box(self, slug: str, card: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.stop_box(slug, card, reply)

    def card_branch_diff(self, card_id: str, reply, target_ref: str = "") -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony"), ensure_ascii=False))
            return
        self.flow.card_diff(card_id, reply, target_ref)

    def merge_card_to_main(self, card_id: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony"), ensure_ascii=False))
            return
        self.flow.merge_card_to_main(card_id, reply)

    def preview_card_merge(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony"), ensure_ascii=False))
            return
        self.flow.preview_card_merge(card_id, request, reply)

    def merge_card(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony"), ensure_ascii=False))
            return
        self.flow.merge_card(card_id, request, reply)

    def preview_card_base_change(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony"), ensure_ascii=False))
            return
        self.flow.preview_card_base_change(card_id, request, reply)

    def request_card_base_change(self, card_id: str, request: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony"), ensure_ascii=False))
            return
        self.flow.request_card_base_change(card_id, request, reply)

    def _shown_column(self, card_id: str) -> str:
        """Kolumna, w której karta jest teraz wyświetlana (boardColumn z ostatniej migawki) — do `moved-from`."""
        try:
            wanted = f"{CARD_PREFIX}{card_num(card_id)}"
            cards = json.loads(self._json or "{}").get("cards") or []
        except (BoardError, ValueError, AttributeError):
            return ""
        return next((str(c.get("boardColumn") or "") for c in cards if isinstance(c, dict) and c.get("id") == wanted), "")

    def upsert_project(self, request: str, sender_pid=None) -> str:
        """UpsertProject z okna projektu. Z polem `tests` to także zatwierdzenie kontraktu testów (PT-285):
        odcisk w stanie usługi tworzy wyłącznie ta ścieżka (store.approve_project_tests) — i tylko po sprawdzeniu
        nadawcy (PT-286): `sender_pid` z magistrali; brak PID albo proces w jednostce kroku `tide-step-*` →
        `{ok: false, error: "forbidden"}` bez zmiany `box.yaml` i odcisku. Bez pola `tests` — bez sprawdzenia."""
        def work():
            data = _object(request)
            if "tests" not in data:
                return self.store.upsert_project(data)
            refused = call_origin.refusal(sender_pid, self.proc_root)
            if refused:
                print(f"tide-daemon: tablica: UpsertProject z `tests` odrzucone (PID {sender_pid}): {refused}",
                      flush=True)
                raise BoardError(refused, "forbidden")
            return self.store.approve_project_tests(data)
        return self._mutate(work)

    def suggest_project_tests(self, request: str) -> str:
        """SuggestProjectTests: podpowiedź kontraktu testów i źródło efektywnego kontraktu. Tylko odczyt."""
        return _reply(lambda: self.store.suggest_project_tests(_object(request)))

    def upsert_tag(self, request: str) -> str:
        return self._mutate(lambda: self.store.upsert_tag(_object(request)))

    def render_flow(self, card_id: str, mode: str) -> str:
        return _reply(lambda: self.store.render_flow(card_id, mode))

    def render_launch(self, card_id: str, request: str) -> str:
        return _reply(lambda: self.store.render_launch(card_id, _object(request)))

    def card_docs(self, card_id: str, reply) -> None:
        """CardDocs(id): plan i specyfikacja karty (board/docs.py) — reply(json) później, z wątku głównego.
        Błędy w kształcie zapisów: zły albo nieznany identyfikator — {ok: false, error, message}."""
        try:
            ident = f"{CARD_PREFIX}{card_num(card_id)}"
        except BoardError as exc:
            reply(json.dumps(_failure(exc.code, str(exc)), ensure_ascii=False))
            return
        if ident in self._docs_waiting:
            self._docs_waiting[ident].append(reply)  # odczyt tej karty już trwa — ten sam wynik
            return
        if self._current is None:
            self.snapshot_json()
        if self._current is None:   # K-98 #2: pierwsza publikacja jeszcze trwa — nie „nie ma karty”
            reply(json.dumps(_failure("preparing", "Tablica się przygotowuje — spróbuj za chwilę"), ensure_ascii=False))
            return
        snapshot = self._current
        card = next((c for c in snapshot.get("cards") or [] if isinstance(c, dict) and c.get("id") == ident), None)
        if card is None:
            reply(json.dumps(_failure("not-found", f"nie ma karty {ident}"), ensure_ascii=False))
            return
        project = next((p for p in snapshot.get("projects") or []
                        if isinstance(p, dict) and card.get("project") and p.get("slug") == card.get("project")), None)
        coordinator = None
        wanted = (card.get("derived") or {}).get("coordinator") or ""
        if wanted:
            try:
                found = next((b for b in self.coordinators() or [] if isinstance(b, dict) and b.get("id") == wanted), None)
            except Exception as exc:  # koordynatorzy to dodatek — plan z klucza `card` działa bez nich
                print(f"tide-daemon: tablica: koordynatorzy dla CardDocs: {exc!r}", flush=True)
                found = None
            if found:
                coordinator = {"id": wanted, "cwd": found.get("cwd") or "", "repoPath": found.get("repoPath") or "",
                               "plan": {"path": ((found.get("plan") or {}).get("path") or "")}}
        card, project = copy.deepcopy(card), copy.deepcopy(project)
        if self._docs_pool is None:
            self._docs_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-board-docs")
        try:
            future = self._docs_pool.submit(self.docs.read, card, project, coordinator)
        except RuntimeError:  # pula zamknięta (koniec usługi)
            return
        self._docs_waiting[ident] = [reply]
        future.add_done_callback(lambda f: self._docs_ready.emit(ident, f))

    def youtrack_issues(self, refresh: bool, reply) -> None:
        """YouTrackIssues(refresh): zgłoszenia użytkownika do importu — reply(json) po pobraniu w wątku roboczym."""
        if self.youtrack is None:
            reply(json.dumps(_failure("unavailable", "YouTrack jest wyłączony w usłudze (--no-youtrack)"),
                             ensure_ascii=False))
            return
        if self._current is None:
            self.snapshot_json()
        self.youtrack.issues(self._current or {}, reply, refresh=bool(refresh))

    def import_youtrack(self, request: str) -> str:
        """ImportYouTrack({"issues": ["LOG-1", …]}): karty w Backlogu z pobranej listy, z polem `youtrack`,
        tytułem, opisem i projektem tablicy po kluczu projektu YouTracka. Zgłoszenie z kartą — pominięte."""
        return self._mutate(lambda: self._import(_object(request)))

    def _import(self, data: dict) -> dict:
        if self.youtrack is None:
            raise BoardError("YouTrack jest wyłączony w usłudze (--no-youtrack)", "unavailable")
        unknown = [k for k in data if k != "issues"]
        if unknown:
            raise BoardError(f"nieznane pole „{unknown[0]}”; dozwolone: issues")
        wanted = data.get("issues")
        if not isinstance(wanted, list) or not wanted or not all(isinstance(i, str) for i in wanted):
            raise BoardError("issues to niepusta lista kluczy zgłoszeń")
        if len(wanted) > 200:
            raise BoardError("najwyżej 200 zgłoszeń naraz")
        board = self.store.load()
        linked = {issue_key(c.youtrack): c.id for c in board.cards.values() if issue_key(c.youtrack)}
        projects = {p.youtrack: p.slug for p in sorted(board.projects.values(), key=lambda p: p.slug) if p.youtrack}
        created, skipped, commit_error = [], [], ""
        for ident in wanted:
            key = issue_key(ident)
            if not key:
                skipped.append({"issue": ident[:40], "card": "", "reason": "to nie jest klucz zgłoszenia"})
                continue
            if key in linked:
                skipped.append({"issue": key, "card": linked[key], "reason": "już jest na tablicy"})
                continue
            issue = self.youtrack.engine.issue_for_import(key)
            if issue is None:
                skipped.append({"issue": key, "card": "", "reason": "nie ma go na liście z YouTracka — odśwież listę"})
                continue
            request = {"title": ytsync.card_title(issue), "description": ytsync.card_description(issue),
                       "column": "backlog", "youtrack": key}
            slug = projects.get(issue.get("project") or ytsync.project_of(key))
            if slug:
                request["project"] = slug
            result = self.store.create_card(request)
            commit_error = result.get("commitError") or commit_error
            linked[key] = result["id"]
            created.append({"issue": key, "card": result["id"], "project": slug or ""})
        return {"ok": True, "created": created, "skipped": skipped, "commitError": commit_error}

    def resolve_youtrack(self, ident: str, choice: str) -> str:
        """ResolveYouTrack(karta albo zgłoszenie, youtrack|tide): rozstrzygnięcie konfliktu ze zmianą ręczną."""
        if self.youtrack is None:
            return json.dumps(_failure("unavailable", "YouTrack jest wyłączony w usłudze (--no-youtrack)"),
                              ensure_ascii=False)
        try:   # najpierw karta: „K-16” wygląda też jak klucz zgłoszenia
            wanted = f"{CARD_PREFIX}{card_num(ident)}"
        except BoardError:
            wanted = ""
        key = "" if wanted else issue_key(ident)
        if not key:
            if not wanted:
                return json.dumps(_failure("bad-request", f"„{ident}” to ani karta, ani zgłoszenie"), ensure_ascii=False)
            card = next((c for c in (self._current or {}).get("cards") or [] if c.get("id") == wanted), None)
            key = issue_key((card or {}).get("youtrack") or "")
            if not key:
                return json.dumps(_failure("not-found", f"karta {wanted} nie ma zgłoszenia w YouTracku"),
                                  ensure_ascii=False)
        result = self.youtrack.engine.resolve(key, (choice or "").strip().lower())
        if result.get("ok"):
            self._apply()
            self.youtrack.tick()
        return json.dumps(result, ensure_ascii=False)

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

    def publish(self, wait_s: float = 0.0) -> bool:
        """Świeża migawka z plików i projekcja kart. K-98 #2: pod bramką przepływu karty (bez blokady dziennika);
        gdy trwa akcja z kolejki albo zmieniła się w trakcie odczytu, publikacja jest odłożona (False), a Snapshot()
        oddaje poprzednią opublikowaną wersję. `wait_s`: najdłuższe czekanie na transakcję Tablicy (tylko jawny
        Snapshot(); publikacje w tle — 0, bez czekania)."""
        with self._gate() as ticket:
            if not ticket:
                self._defer("publish")
                return False
            try:
                repos = list(self.repos_source() or [])
            except Exception as exc:  # lista repozytoriów to dodatek — tablica działa bez niej
                print(f"tide-daemon: tablica: repozytoria: {exc!r}", flush=True)
                repos = []
            # K-98 #1: jedno odświeżenie = najwyżej jedno wczytanie Tablicy. Kontekst powstaje tutaj, idzie jawnie do
            # migawki plików i projekcji kart (`engine.derived` → widok środowiska) i ginie z końcem odświeżenia.
            # K-98 #2: wczytanie nie czeka na transakcję Tablicy trzymaną przez mutację (StartCard, scalenie, tick) —
            # najwyżej `wait_s`; zajęta transakcja odkłada publikację, a Snapshot() oddaje poprzednią wersję.
            with self._without_waiting(wait_s) as probe:
                read = BoardRead(self.store)
                try:
                    snapshot = self.store.snapshot(repos=repos, board=read.board)
                except BoardBusy:
                    self._defer("publish")
                    return False
                except Exception as exc:  # odczyt nie może położyć usługi
                    print(f"tide-daemon: tablica: odczyt: {exc!r}", flush=True)
                    snapshot = {"boardDir": self.store.root, "exists": False, "cards": [], "projects": [], "tags": [],
                                "repos": [], "errors": [{"path": self.store.root, "message": f"błąd odczytu: {exc}"}]}
                if probe.busy is True:
                    self._defer("publish")
                    return False
                # Podstawa trafia do `_base` dopiero z udaną publikacją (bilet ważny przez cały odczyt).
                return self._apply(read, base=snapshot, ticket=ticket) is not False

    def _without_waiting(self, wait_s=0.0):
        """Odczyty Tablicy z wątku głównego bez czekania na jej transakcję (K-98 #2, `BoardStore.without_waiting`);
        `wait_s` — najwyżej tyle sekund czekania (sygnał Qt może podać tu inny argument: wtedy 0)."""
        hold = getattr(self.store, "without_waiting", None)
        if hold is None:
            return contextlib.nullcontext(SimpleNamespace(busy=False))
        wait = float(wait_s) if isinstance(wait_s, (int, float)) and not isinstance(wait_s, bool) else 0.0
        return hold(wait) if wait > 0 else hold()

    def _gate(self):
        """Bramka odczytu przepływu karty (K-98 #2) albo zawsze otwarta, gdy usługa działa bez przepływu."""
        gate = getattr(self.flow, "read_gate", None) if self.flow is not None else None
        return gate() if gate is not None else contextlib.nullcontext(True)

    def _defer(self, kind: str) -> None:
        if self._pending != "publish":
            self._pending = kind
        if not self._retry.isActive():
            self._retry.start()

    def _retry_pending(self, wait_s: float = 0.0) -> bool:
        """Ponowienie odłożonej publikacji; True — wersja opublikowana (Tablica wczytana świeżo)."""
        kind, self._pending = self._pending, None
        if kind == "publish":
            return bool(self.publish(wait_s))
        if kind == "apply":
            # Ponowione przeliczenie czyta Tablicę świeżo jak publikacja; z terminem — jako publikacja (Snapshot()).
            return bool(self.publish(wait_s) if wait_s else self._apply())
        return False

    def _apply(self, read: BoardRead | None = None, base: dict | None = None, ticket=None):
        """Migawka z plików + stan kart z pracy agentów → JSON; Changed tylko przy zmianie.
        `read`: kontekst odczytu Tablicy tego odświeżenia (K-98 #1); bez niego (zegar przeliczenia, YouTrack) —
        nowy kontekst, czyli najwyżej jedno wczytanie na to przeliczenie. K-98 #2: pod bramką jak `publish`
        (`ticket` — bilet z `publish`, `base` — świeżo wczytana podstawa). False — publikacja odłożona."""
        if base is None and self._base is None:
            return self.publish()
        if ticket is not None:
            return self._publish(read, ticket, base)
        with self._gate() as ticket:
            if not ticket:
                self._defer("apply")
                return False
            return self._publish(read, ticket, base)

    def _publish(self, read: BoardRead | None, ticket, base: dict | None = None) -> bool:
        """Projekcja i publikacja jednej wersji (wywołujący trzyma bramkę). Gdy w trakcie odczytu zaczęła się albo
        skończyła akcja z kolejki (bilet nieważny), wynik jest odrzucany przed jakimkolwiek skutkiem."""
        if not isinstance(read, BoardRead):   # sygnały Qt wołają bez argumentu
            read = BoardRead(self.store)
        base = self._base if base is None else base
        # Projekcja czyta Tablicę (widok środowiska, polityka review) bez czekania na transakcję mutacji; trafienie
        # na zajętą transakcję — nawet złapane niżej i zamienione na zastępczą treść — odrzuca całą wersję.
        with self._without_waiting() as probe:
            snapshot, context, box_wait = self._derived(base, read)
            work = self._work(snapshot, context)
        if probe.busy is True or not getattr(ticket, "still_valid", lambda: True)():
            self._defer("publish")
            return False
        self._base = base
        if self.youtrack is not None:
            try:
                self.youtrack.observe(snapshot)
                snapshot = self.youtrack.decorate(snapshot)
            except Exception as exc:  # synchronizacja to dodatek — tablica działa bez niej
                print(f"tide-daemon: tablica: YouTrack: {exc!r}", flush=True)
        self._current = snapshot
        text = json.dumps(snapshot, ensure_ascii=False)
        fresh = text != self._content or box_wait != self._box_wait
        if fresh:
            # Nowa treść = nowa wersja; ta sama treść zachowuje wersję (odczyt bez zmian stanu jej nie podbija).
            self._version += 1
            self._content, self._box_wait = text, box_wait
        if fresh or not self._json:
            self._json = _versioned(text, self._version)
            self.changed.emit()
        work_text = json.dumps(work, ensure_ascii=False)
        if work_text != self._work_json:
            self._work_json = work_text
            key = work_list.stable_key(work)
            if key != self._work_key:
                self._work_key = key
                self._work_later.stop()
                self.work_changed.emit()
            elif not self._work_later.isActive():
                self._work_later.start()   # tylko ostatnia czynność agenta — kafelek dowie się za chwilę
        return True

    def _work(self, snapshot: dict, context) -> dict:
        """Lista „Praca”; błąd nigdy nie psuje tablicy — wtedy same karty, a w ostateczności pusta lista."""
        exists = snapshot.get("exists", False)
        for ctx in (context, None):
            try:
                return work_list.build(snapshot.get("cards"), ctx, snapshot.get("projects"), exists)
            except Exception as exc:  # lista „Praca” to dodatek — tablica działa bez niej
                print(f"tide-daemon: tablica: lista Praca: {exc!r}", flush=True)
        return {"exists": bool(exists), "items": [],
                "counts": {**{g: 0 for g in work_list.GROUPS}, "total": 0, "sessions": 0}}

    def _derived(self, base: dict, read: BoardRead | None = None) -> tuple[dict, object, dict]:
        """(migawka z `derived` i `boardColumn`, kontekst przeliczenia derive albo None, boxWait kart z dziennikiem)
        — kontekst dostaje też lista „Praca” (board/work.py), więc sesje i połączenia są te same co w kolumnach kart.
        `read`: odczyt Tablicy tego odświeżenia, przekazywany do `flow.derived` (K-98 #1). boxWait trafia do
        opublikowanej wersji dla BoxStatus (K-98 #2), także dla kart bez wyliczanego stanu (ręczna kolumna)."""
        read = read if read is not None else BoardRead(self.store)
        if self.derive is None and self.flow is None:
            return base, None, {}
        snapshot = dict(base)
        snapshot["errors"] = list(base.get("errors") or [])
        claimed = [c.get("session") for c in base.get("cards") or [] if isinstance(c, dict) and c.get("session")]
        derive = None
        if self.derive is not None:
            try:
                derive = self.derive.context(claimed) if hasattr(self.derive, "context") else self.derive
            except Exception as exc:  # stan z pracy agentów to dodatek — tablica działa bez niego
                print(f"tide-daemon: tablica: stan z pracy agentów: {exc!r}", flush=True)
                if self.flow is None:
                    return base, None, {}
        if self.flow is not None:
            try:
                snapshot["errors"].extend(self.flow.warnings())   # dziennik karty odtworzony z kopii usługi
            except Exception as exc:
                print(f"tide-daemon: tablica: przepływ karty: {exc!r}", flush=True)
        repos = {p.get("slug"): p.get("repo") or "" for p in base.get("projects") or [] if isinstance(p, dict)}
        cards = []
        links = []
        fresh: dict = {}
        box_wait: dict = {}
        for card in base.get("cards") or []:
            card = copy.copy(card)
            derived = None
            try:
                if self.flow is not None and self.flow.has_journal(card.get("id") or ""):
                    # Karta w przepływie karty: stan tylko z dziennika (dziennik wygrywa z planem i sesjami).
                    derived = self.flow.derived(card, read)
                    if derived is None and hasattr(self.flow, "card_base"):
                        # Ręczna kolumna ma derived: null; jej baza nadal pochodzi z dziennika.
                        card.update(self.flow.card_base(card.get("id") or ""))
                    if hasattr(self.flow, 'tracker_tickets'):
                        card['flowTickets'] = self.flow.tracker_tickets(card)
                elif derive is not None:
                    given = {**card, "repo": repos.get(card.get("project"), ""),
                             "extraRepos": [repos.get(slug) for slug in card.get("projects") or [] if repos.get(slug)]}
                    derived = derive(given)
                    sticky = derive.sticky(given) if hasattr(derive, "sticky") else None
                    if sticky:
                        links.append((card.get("id", ""), *sticky))
            except Exception as exc:
                snapshot["errors"].append({"path": card.get("path", ""),
                                           "message": f"{card.get('id', '')}: stan z pracy agentów: {exc}"})
            ident = card.get("id") or ""
            from_flow = self.flow is not None and self.flow.has_journal(ident)
            if from_flow:
                shown = ((derived or {}).get("flow") or {})
                try:
                    box_wait[ident] = shown["boxWait"] if "boxWait" in shown else \
                        self.flow.box_wait(ident) if hasattr(self.flow, "box_wait") else ""
                except Exception as exc:   # stan pudełka to dodatek migawki — BoxStatus pokaże kartę bez niego
                    print(f"tide-daemon: tablica: {ident}: boxWait: {exc!r}", flush=True)
            if derived is not None and not from_flow:
                fresh[ident] = derived
            elif derived is None and not from_flow and not self._derive_ready and ident in self._derived_cache:
                derived = {**self._derived_cache[ident], "cached": True}
            card["derived"] = derived
            flow = (derived or {}).get("flow") or {}
            if "base" in flow:
                card.update({key: flow["base"][key] for key in ("baseRef", "baseBranch", "baseCommit")})
                card["baseVersion"] = flow["baseVersion"]
            card["boardColumn"] = (derived or {}).get("column") or card.get("column") or "backlog"
            cards.append(card)
        if fresh:
            # Pierwsze świeże przeliczenie kończy okres zapamiętanego stanu; zapis tylko przy zmianie.
            self._derive_ready = True
            if fresh != self._derived_cache:
                self._derived_cache = fresh
                _save_cache(self._derived_cache_path, fresh)
        snapshot["cards"] = cards
        self._queue_links(links)
        return snapshot, (derive if hasattr(derive, "sessions") else None), box_wait

    def _queue_links(self, links: list) -> None:
        now = time.monotonic()
        fresh = [item for item in links if now - self._link_tried.get(item[:2], -LINK_RETRY_S) >= LINK_RETRY_S]
        if not fresh or not hasattr(self.store, "link_session"):
            return
        if not self._links:
            QTimer.singleShot(0, self._persist_links)  # nie w trakcie przeliczenia — po nim
        self._links.extend(i for i in fresh if i not in self._links)

    def _persist_links(self) -> None:
        """Utrwala w kartach sesje rozpoznane przy przeliczeniu (store.link_session nie nadpisuje
        `session` ustawionego wcześniej, także ręcznie), potem nowa migawka."""
        links, self._links = self._links, []
        wrote = False
        for card_id, session, how, branch in links:
            self._link_tried[(card_id, session)] = time.monotonic()
            try:
                # Zapis z zegara przeliczenia: zajęta transakcja Tablicy (mutacja) — bez czekania; para wraca przy
                # następnym przeliczeniu, bo znacznik próby jest zdejmowany.
                with self._without_waiting():
                    result = self.store.link_session(card_id, session, how, branch)
            except BoardBusy:
                self._link_tried.pop((card_id, session), None)
                continue
            except BoardError as exc:
                print(f"tide-daemon: tablica: {card_id}: połączenie z sesją {session}: {exc}", flush=True)
                continue
            except Exception as exc:  # zapis to dodatek — derive i tak łączy kartę z sesją
                print(f"tide-daemon: tablica: {card_id}: połączenie z sesją {session}: {exc!r}", flush=True)
                continue
            if result.get("changed"):
                wrote = True
                print(f"tide-daemon: tablica: {card_id}: połączona z sesją {session} ({how})", flush=True)
        if len(self._link_tried) > 1000:
            self._link_tried.clear()
        if wrote:
            self._rewatch()
            self.publish()

    def _poll_sync(self) -> None:
        """Odpytywanie co POLL_MS: synchronizacja z pełnym przejściem obserwowanych poddrzew (zdarzenie, którego nie
        było — zapis w miejscu bez obserwacji pliku, limit obserwacji, przepełniona kolejka inotify, tablica założona
        poza usługą)."""
        self.sync(full=True)

    def sync(self, full: bool = False) -> None:
        """Commit zmian z zewnątrz, nowe ścieżki do obserwacji i świeża migawka. Zmiany dziennika kart (przepływ
        karty) najpierw własnym commitem, pod blokadą dziennika — nie trafiają do „sync external changes”.
        K-98 #2: blokadę dziennika bierzemy bez czekania. Gdy trzyma ją mutacja (np. StartCard z zapisem Tablicy),
        commit i znacznik specyfikacji czekają na następną synchronizację, a wątek główny dalej odpowiada."""
        lock = getattr(self.flow, "lock", None) if self.flow is not None else None
        if lock is not None and not lock.acquire(blocking=False):
            self._debounce.start()
        else:
            # Review r1 (pełny przebieg): commit zmian z zewnątrz i znacznik specyfikacji to zapisy z zegara, nie
            # z prośby użytkownika — transakcja Tablicy trzymana przez mutację (StartCard, scalenie, tick) albo inny
            # proces odkłada je do następnej synchronizacji zamiast zatrzymać wątek główny.
            try:
                try:
                    if self.flow is not None:
                        self.flow.commit()
                    with self._without_waiting():
                        if self.store.sync():
                            print("tide-daemon: tablica: zapisano zmiany z zewnątrz", flush=True)
                except BoardBusy:
                    self._debounce.start()
                except Exception as exc:  # np. zablokowany indeks — spróbujemy przy następnej okazji
                    print(f"tide-daemon: tablica: commit zmian z zewnątrz: {exc}", flush=True)
            finally:
                if lock is not None:
                    lock.release()
            try:
                with self._without_waiting():
                    for card_id in self.store.mark_ready_specs():
                        print(f"tide-daemon: tablica: {card_id}: specyfikacja gotowa", flush=True)
            except BoardBusy:
                self._debounce.start()
            except Exception as exc:  # znacznik to dodatek — spróbujemy przy następnej synchronizacji
                print(f"tide-daemon: tablica: gotowe specyfikacje: {exc!r}", flush=True)
        self._rewatch(full=full is True)
        self.publish()

    # --- wnętrze -------------------------------------------------------------------------------

    def _mutate(self, work) -> str:
        text = _reply(work)
        self._rewatch()
        self.publish()
        return text

    def _deliver_docs(self, ident, future) -> None:
        replies = self._docs_waiting.pop(ident, [])
        if future.cancelled():
            return
        try:
            result = future.result()
        except Exception as exc:  # błąd odczytu nie może położyć usługi
            print(f"tide-daemon: tablica: CardDocs: {exc!r}", flush=True)
            result = _failure("internal", "błąd usługi — szczegóły w dzienniku (journalctl --user -u tide-daemon)")
        text = json.dumps(result, ensure_ascii=False)
        for reply in replies:
            reply(text)

    def _touched(self, _path: str) -> None:
        self._debounce.start()  # każda kolejna zmiana odsuwa commit — zapis z wielu plików to jeden commit

    def _dir_touched(self, path: str) -> None:
        """Zmiana wpisów katalogu (nowy, usunięty albo podmieniony plik lub podkatalog): katalog do przejrzenia."""
        self._dirty.add(path)
        self._debounce.start()

    # --- obserwacja plików (K-98 #6) -----------------------------------------------------------

    def _rewatch(self, full: bool = False) -> None:
        """Obserwowane ścieżki zgodne z dyskiem. Bez zaznaczonych katalogów i bez `full` — nic (żadnego przejścia
        drzewa). Inaczej przegląda tylko zaznaczone katalogi (rodzice przed dziećmi): usunięte poddrzewa znikają
        z obserwacji, nowe są obserwowane od razu — katalog dostaje obserwację PRZED odczytem swoich wpisów, więc
        zmiana w świeżym poddrzewie po odczycie daje zdarzenie, a wcześniejsza jest już w odczycie. `full`
        (pierwszy start, ponowne założenie Tablicy, odpytywanie co POLL_MS) przegląda wszystkie obserwowane
        poddrzewa. Każda ścieżka jest porównywana ze zbiorem obserwowanych raz — O(N)."""
        root = self.store.root
        if not os.path.isdir(root):
            if self._tree:
                actual = set(self._watcher.directories()) | set(self._watcher.files())
                self._unwatch([p for p in self._forget(root) if p in actual])
                self._tree.clear()
                self._tree_files.clear()
                self._watched_files = 0
            self._dirty.clear()
            return
        full = full or root not in self._tree
        if not full and not self._dirty:
            return
        dirty, self._dirty = self._dirty, set()
        actual = set(self._watcher.directories()) | set(self._watcher.files())
        add: list[str] = []
        drop: list[str] = []
        if full:
            self.watch_stats["fullScans"] += 1
            self._review(root, actual, add, drop, deep=True)
        else:
            for folder in sorted(dirty, key=lambda p: (p.count(os.sep), p)):
                if folder in self._tree:   # katalog spoza obserwacji (np. już usunięty z rodzicem) — pomijamy
                    self._review(folder, actual, add, drop, deep=False)
        self._unwatch([p for p in drop if p in actual])
        self._watch([p for p in add if p not in actual])

    def _review(self, folder: str, actual: set, add: list, drop: list, deep: bool) -> None:
        """Wpisy jednego obserwowanego katalogu; `deep` — także wszystkie jego znane podkatalogi. Katalog bez
        obserwacji (pierwszy start, katalog usunięty i założony ponownie — obserwator gubi stary i-węzeł) dostaje ją
        przed odczytem wpisów."""
        if folder not in actual:
            self._watch([folder])
            actual.add(folder)
        listed = self._entries(folder)
        if listed is None:                       # katalog zniknął — z całym poddrzewem
            drop += self._forget(folder)
            return
        subdirs, files = listed
        known = self._tree.setdefault(folder, set())
        for gone in sorted(known - set(subdirs)):
            drop += self._forget(gone)
        for sub in subdirs:
            if sub in known and sub in actual:
                if deep:
                    self._review(sub, actual, add, drop, deep=True)
            else:
                if sub in known:                 # obserwacja zgubiona (np. odrzucona) — poddrzewo od nowa
                    self._forget(sub)
                known.add(sub)
                self._enter(sub, actual, add, drop)
        self._take_files(folder, files, actual, add, drop)

    def _enter(self, folder: str, actual: set, add: list, drop: list) -> None:
        """Nowe poddrzewo: najpierw obserwacja katalogu, potem odczyt jego wpisów (bez wyścigu z zapisem obok)."""
        self._tree[folder] = set()
        self._review(folder, actual, add, drop, deep=True)

    def _take_files(self, folder: str, files: list, actual: set, add: list, drop: list) -> None:
        have = self._tree_files.setdefault(folder, set())
        present = set(files)
        for gone in sorted(have - present):
            have.discard(gone)
            self._watched_files -= 1
            drop.append(gone)
        for path in files:
            if path in have:
                if path not in actual:           # plik podmieniony (rename) — obserwator zgubił stary i-węzeł
                    add.append(path)
            elif self._watched_files < MAX_WATCHED_FILES:
                have.add(path)
                self._watched_files += 1
                add.append(path)

    def _entries(self, folder: str):
        """(podkatalogi, pliki) katalogu, posortowane; None — katalogu nie ma. Liczy przejrzane katalogi."""
        self.watch_stats["visitedDirs"] += 1
        top = folder == self.store.root
        dirs, files = [], []
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if entry.name != ".git" and not (top and entry.name in UNWATCHED_TOP):
                            dirs.append(entry.path)
                    elif not entry.name.startswith("."):
                        files.append(entry.path)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:     # brak praw itp. — katalog zostaje w obserwacji bez zmian wpisów
            print(f"tide-daemon: tablica: obserwacja {folder}: {exc!r}", flush=True)
            return sorted(self._tree.get(folder, ())), sorted(self._tree_files.get(folder, ()))
        return sorted(dirs), sorted(files)

    def _forget(self, folder: str) -> list[str]:
        """Usuwa poddrzewo z pamięci obserwacji; zwraca jego ścieżki (katalogi i pliki) do zdjęcia z obserwatora."""
        out = []
        for sub in self._tree.pop(folder, set()):
            out += self._forget(sub)
        files = self._tree_files.pop(folder, set())
        self._watched_files -= len(files)
        out += files
        out.append(folder)
        parent = self._tree.get(os.path.dirname(folder))
        if parent is not None:
            parent.discard(folder)
        return out

    def _unwatch(self, paths: list[str]) -> None:
        if paths:
            self._watcher.removePaths(paths)
            self._refused.difference_update(paths)

    def _watch(self, paths: list[str]) -> None:
        """Dodaje ścieżki do obserwatora. Odrzucone (limit inotify) zostają tylko pod odpytywaniem co POLL_MS
        (`_poll_sync` z pełnym przejściem ponawia je) — zmiana z zewnątrz trafi do migawki najpóźniej wtedy."""
        if not paths:
            return
        # Ścieżki, których już nie ma (katalog usunięty między zdarzeniem a przejściem), obserwator też odrzuca — to
        # nie limit inotify: nie liczymy ich i nie piszemy wpisu (PT-385); przejście zaraz zdejmie je z pamięci.
        refused = [p for p in (self._watcher.addPaths(paths) or []) if os.path.lexists(p)]
        if refused:
            first = not self._refused
            self._refused.update(refused)
            self.watch_stats["refused"] += len(refused)
            if first:   # jeden wpis na okres odrzuceń, nie na każdą ścieżkę
                print(f"tide-daemon: tablica: obserwator odrzuca ścieżki (limit inotify?), np. {refused[0]} — "
                      f"zmiany tam wykryje odpytywanie co {self._poll.interval() / 1000:g} s", flush=True)
        else:
            self._refused.difference_update(paths)


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


def _versioned(text: str, version: int) -> str:
    """JSON migawki z polami `version` i `readState` na początku (K-98 #2) — bez ponownej serializacji treści."""
    head = f'{{"version": {int(version)}, "readState": "ready"'
    return head + ("}" if text.strip() == "{}" else ", " + text.lstrip()[1:])


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
