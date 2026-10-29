"""Obszar tablicy w usłudze: D-Bus /Board, zapis kart z commitem i zmiany z zewnątrz.

- zapisy z D-Bus idą od razu, w wątku głównym (kilka małych plików i jeden commit w repozytorium
  z kilkudziesięcioma plikami — milisekundy), więc kolejne wywołanie widzi już nowy stan,
- migawka (JSON) jest gotowa w pamięci i odświeżana po każdej zmianie; Changed tylko wtedy,
  gdy się zmieniła,
- zmiany z zewnątrz (agent pisze plan w docs/, ręczna edycja karty): QFileSystemWatcher na
  katalogach i plikach tablicy, po DEBOUNCE_MS ciszy — commit „sync external changes” i Changed;
  dodatkowo co POLL_MS to samo sprawdzenie (zapis w miejscu bez zdarzenia, nowe podkatalogi,
  tablica założona poza usługą).
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
"""
import contextlib
import copy
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from .board import work as work_list
from .board.docs import CardDocs
from .board.model import CARD_PREFIX, BoardError, card_num
from .board import ytsync
from .coordination.youtrack import issue_key

LAUNCH_PREFIX = "launch:"  # ShowWindow("launch:K-12") — okno Tide od razu z „Co ma zrobić agent?”

DEBOUNCE_MS = 2_000
POLL_MS = 60_000
REDERIVE_MS = 500  # zmiany sesji przychodzą seriami (co 250 ms przy pracy agenta) — przeliczenie najwyżej tak często
MAX_WATCHED_FILES = 2_000  # powyżej — tylko katalogi i odpytywanie co POLL_MS
WORK_VOLATILE_MS = 10_000  # zmiana samej ostatniej czynności agenta na liście „Praca” — WorkChanged najwyżej tak często
LINK_RETRY_S = 60.0  # nieudane utrwalenie sesji w karcie ponawiamy najwyżej tak często


class BoardMonitor(QObject):
    changed = pyqtSignal()
    work_changed = pyqtSignal()           # lista „Praca” (Work()) się zmieniła — sygnał WorkChanged
    _rederive_requested = pyqtSignal()   # z dowolnego wątku (np. wynik gita w tle) — kolejka Qt do wątku głównego
    _docs_ready = pyqtSignal(object, object)   # (ID karty, future) — wynik CardDocs z wątku roboczego

    def __init__(self, store, repos_source=None, derive=None, debounce_ms: int = DEBOUNCE_MS,
                 poll_ms: int = POLL_MS, launchers=None, rederive_ms: int = REDERIVE_MS, coordinators=None,
                 docs=None, youtrack=None, parent=None):
        super().__init__(parent)
        self.store = store
        # Synchronizacja z YouTrackiem (ytsync_monitor.YouTrackSyncMonitor) albo None (--no-youtrack).
        self.youtrack = youtrack
        # CardDocs: tablice koordynatorów z /Coordination (funkcja() → lista) i czytnik dokumentów kart.
        self.coordinators = coordinators or (lambda: [])
        self.docs = docs or CardDocs(store.root)
        self._docs_pool: ThreadPoolExecutor | None = None
        self._docs_waiting: dict[str, list] = {}     # ID karty → odpowiedzi czekające na trwający odczyt
        self._docs_ready.connect(self._deliver_docs)
        self._current: dict | None = None                 # migawka z `derived` (to, co oddaje Snapshot())
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
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._touched)
        self._watcher.fileChanged.connect(self._touched)
        self._debounce = QTimer(self, singleShot=True, interval=debounce_ms)
        self._debounce.timeout.connect(self.sync)
        self._poll = QTimer(self, interval=poll_ms)
        self._poll.timeout.connect(self.sync)
        self._rederive = QTimer(self, singleShot=True, interval=rederive_ms)
        self._rederive.timeout.connect(self._apply)
        self._rederive_requested.connect(self.rederive)
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
        if self.youtrack is not None:
            self.youtrack.stop()
        self._poll.stop()
        self._debounce.stop()
        self._rederive.stop()
        self._work_later.stop()
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
        if self.derive is not None and not self._rederive.isActive():
            self._rederive.start()

    # --- D-Bus ---------------------------------------------------------------------------------

    def snapshot_json(self) -> str:
        if not self._json:
            self.publish()
        return self._json

    def work_json(self) -> str:
        """Work(): lista „Praca” (board/work.py) — {exists, items, counts}."""
        if not self._work_json:
            self.publish()
        return self._work_json

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

    def resolve_card_review(self, card_id: str, choice: str, reply) -> None:
        if self.flow is None:
            reply(json.dumps(_failure("unavailable", "przepływ karty jest wyłączony w usłudze"), ensure_ascii=False))
            return
        self.flow.resolve_review(card_id, choice, reply)

    def _shown_column(self, card_id: str) -> str:
        """Kolumna, w której karta jest teraz wyświetlana (boardColumn z ostatniej migawki) — do `moved-from`."""
        try:
            wanted = f"{CARD_PREFIX}{card_num(card_id)}"
            cards = json.loads(self._json or "{}").get("cards") or []
        except (BoardError, ValueError, AttributeError):
            return ""
        return next((str(c.get("boardColumn") or "") for c in cards if isinstance(c, dict) and c.get("id") == wanted), "")

    def upsert_project(self, request: str) -> str:
        return self._mutate(lambda: self.store.upsert_project(_object(request)))

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
        snapshot = self._current or {}
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

    def publish(self) -> None:
        try:
            repos = list(self.repos_source() or [])
        except Exception as exc:  # lista repozytoriów to dodatek — tablica działa bez niej
            print(f"tide-daemon: tablica: repozytoria: {exc!r}", flush=True)
            repos = []
        try:
            snapshot = self.store.snapshot(repos=repos)
        except Exception as exc:  # odczyt nie może położyć usługi
            print(f"tide-daemon: tablica: odczyt: {exc!r}", flush=True)
            snapshot = {"boardDir": self.store.root, "exists": False, "cards": [], "projects": [], "tags": [],
                        "repos": [], "errors": [{"path": self.store.root, "message": f"błąd odczytu: {exc}"}]}
        self._base = snapshot
        self._apply()

    def _apply(self) -> None:
        """Migawka z plików + stan kart z pracy agentów → JSON; Changed tylko przy zmianie."""
        if self._base is None:
            self.publish()
            return
        snapshot, context = self._derived(self._base)
        work = self._work(snapshot, context)
        if self.youtrack is not None:
            try:
                self.youtrack.observe(snapshot)
                snapshot = self.youtrack.decorate(snapshot)
            except Exception as exc:  # synchronizacja to dodatek — tablica działa bez niej
                print(f"tide-daemon: tablica: YouTrack: {exc!r}", flush=True)
        self._current = snapshot
        text = json.dumps(snapshot, ensure_ascii=False)
        if text != self._json:
            self._json = text
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

    def _derived(self, base: dict) -> tuple[dict, object]:
        """(migawka z `derived` i `boardColumn`, kontekst przeliczenia derive albo None) — kontekst dostaje
        też lista „Praca” (board/work.py), więc sesje i połączenia są te same co w kolumnach kart."""
        if self.derive is None and self.flow is None:
            return base, None
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
                    return base, None
        if self.flow is not None:
            try:
                snapshot["errors"].extend(self.flow.warnings())   # dziennik karty odtworzony z kopii usługi
            except Exception as exc:
                print(f"tide-daemon: tablica: przepływ karty: {exc!r}", flush=True)
        repos = {p.get("slug"): p.get("repo") or "" for p in base.get("projects") or [] if isinstance(p, dict)}
        cards = []
        links = []
        for card in base.get("cards") or []:
            card = copy.copy(card)
            derived = None
            try:
                if self.flow is not None and self.flow.has_journal(card.get("id") or ""):
                    # Karta w przepływie karty: stan tylko z dziennika (dziennik wygrywa z planem i sesjami).
                    derived = self.flow.derived(card)
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
            card["derived"] = derived
            card["boardColumn"] = (derived or {}).get("column") or card.get("column") or "backlog"
            cards.append(card)
        snapshot["cards"] = cards
        self._queue_links(links)
        return snapshot, (derive if hasattr(derive, "sessions") else None)

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
                result = self.store.link_session(card_id, session, how, branch)
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

    def sync(self) -> None:
        """Commit zmian z zewnątrz, nowe ścieżki do obserwacji i świeża migawka. Zmiany dziennika kart (przepływ
        karty) najpierw własnym commitem, pod blokadą dziennika — nie trafiają do „sync external changes”."""
        try:
            with self.flow.lock if self.flow is not None else contextlib.nullcontext():
                if self.flow is not None:
                    self.flow.commit()
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
