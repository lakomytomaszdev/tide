"""Synchronizacja stanu zgłoszeń YouTracka z tablicą i planami agentów (PT-7) — logika bez Qt.

Co synchronizujemy: kartę z polem `youtrack` (K-16 → LEGAL-35) i ticket planu z `tracker: youtrack`, którego
ID jest kluczem zgłoszenia (LEGAL-14, LOG-232) — tylko w projektach YouTracka, które mają na tablicy projekt
z kluczem (`youtrack: LOG`) i włączoną synchronizacją, i tylko przy włączonym `sync` w ustawieniach.

Stan, którego chce Tide (klucz mapy stanów, nazwy w youtrack.json → `states`):

- karta — z kolumny wyświetlanej: Backlog i Do zrobienia → `open`, W toku → `progress`, Czeka na mnie →
  `waiting`, Do sprawdzenia → `review` (krok człowieka: test ręczny albo scalenie), Gotowe → `done`;
  Archiwum — bez zmian w YouTracku,
- ticket — ze stanu koordynacji: `todo` → `open`, `w-toku` i `review` (review AI to wciąż W toku) →
  `progress`, `czeka` → `waiting`, `gotowe` → `review`, `odrzucone` → `rejected`; ticket `gotowe`, którego
  karta stoi w Gotowe, → `done`. Stan ticketu wzięty z samego YouTracka (`source: tracker`) się nie liczy.

Pierwszeństwo, gdy karta i ticket wskazują to samo zgłoszenie: ticket (dokładniejszy), chyba że karta stoi
w Gotowe (ręczne Gotowe zawsze wygrywa) albo została ręcznie przeniesiona po ostatniej pracy nad ticketem.
Karta połączona ze zgłoszeniem-parasolem (żaden ticket go nie ma) synchronizuje się sama. Na jedno
zgłoszenie przypada jeden stan naraz, a stan z YouTracka nigdy nie wraca do wyliczenia — bez ping-ponga.

Zapis tylko przy przejściu, które Tide sam widzi: plik stanu (`$XDG_STATE_HOME/tide/youtrack-sync.json`,
0600) pamięta dla każdego zgłoszenia ostatni stan zapisany (albo przyjęty) przez Tide, jego klucz i `updated`
zgłoszenia z tej chwili. Zapis idzie, gdy klucz, którego chce Tide, różni się od zapamiętanego i trzyma się
przez DEBOUNCE_S. Przed zapisem czytamy zgłoszenie: stan inny niż zapamiętany to zmiana ręczna w YouTracku —
konflikt, bez nadpisywania, aż użytkownik wybierze „weź z YouTracka” albo „nadpisz z Tide” (albo Tide sam
dojdzie do stanu z YouTracka). Pierwsze spotkanie zgłoszenia: zapis tylko wtedy, gdy Tide ma wiedzę o pracy
(nie `open`), zgłoszenie jest nierozwiązane, w innym stanie i nikt go nie zmieniał po połączeniu; inaczej
Tide przyjmuje stan z YouTracka.

Powrót: `open` po stanie rozwiązanym (`review`, `done`, `rejected`) to `reopened`. Karta, która spadła do
Do zrobienia albo Backlogu tylko dlatego, że zniknęła jej praca (koordynator poza oknem 7 dni, start usługi),
nie cofa zgłoszenia — liczy się wyłącznie jawne przeniesienie (`moved` po ostatnim zapisie Tide).

Sieć tylko w `process` (wątek roboczy). Brak YouTracka (sieć lokalna) to kolejka: klucz w pliku stanu zostaje
stary, więc przejście czeka (także przez restart usługi) i idzie z ponowieniem co BACKOFF_S.
"""
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field

from ..coordination import youtrack as yt

COLUMN_KEYS = {"backlog": "open", "todo": "open", "progress": "progress", "waiting": "waiting",
               "review": "review", "done": "done"}
TICKET_KEYS = {"todo": "open", "w-toku": "progress", "review": "progress", "czeka": "waiting",
               "gotowe": "review", "odrzucone": "rejected"}
RESOLVED_KEYS = ("review", "done", "rejected")
OPEN_KEYS = ("open", "reopened")
# Nazwy stanów rozwiązanych w domyślnym workflow (zgłoszenie bez pola `resolved` w odpowiedzi).
RESOLVED_NAMES = ("fixed", "verified", "won't fix", "wont fix", "duplicate", "obsolete", "can't reproduce",
                  "incomplete", "done", "closed", "resolved")
DEBOUNCE_S = 5.0
BACKOFF_S = (5.0, 15.0, 60.0, 300.0)
REFUSED_RETRY_S = 1800.0      # YouTrack odmówił zmiany (4xx) — ponowienie najwcześniej po 30 min albo przy nowym stanie
CONFLICT_RECHECK_S = 300.0    # konflikt: co tyle sprawdzamy, czy YouTrack i Tide już się zgadzają
LINK_SLACK_MS = 60_000        # zmiana zgłoszenia do minuty po połączeniu to jeszcze „przed połączeniem”
SEEN_MAX = 3000
VERSION = 1


def state_path(env=None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local/state")
    return os.path.join(base, "tide", "youtrack-sync.json")


def project_of(key: str) -> str:
    return key.rpartition("-")[0]


def target_key(wanted: str, record: dict | None) -> str:
    """Klucz do zapisu: `open` po stanie rozwiązanym (albo po `reopened`) to `reopened`."""
    if wanted == "open" and record and record.get("key") in RESOLVED_KEYS + ("reopened",):
        return "reopened"
    return wanted


def same(a: str, b: str) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


@dataclass
class Want:
    """Stan, którego Tide chce dla jednego zgłoszenia, i skąd go wziął."""
    issue: str
    key: str
    via: str                        # card | ticket
    cards: list = field(default_factory=list)   # karty połączone ze zgłoszeniem (pokazują stan synchronizacji)
    coordinator: str = ""           # ticket: id koordynatora
    label: str = ""                 # „karta K-16”, „ticket LOG-232 · plan „…””
    link_ts: float = 0.0            # od kiedy połączone (reguła pierwszego spotkania); 0 — nie wiadomo
    held: bool = False              # karta bez pracy i bez jawnego przeniesienia — stan zostaje
    slow: bool = False              # stan krótkotrwały z pracy agentów: zapis dopiero po `stableMinutes`
    explicit_ts: float = 0.0        # ostatnie jawne źródło: przeniesienie karty albo praca nad ticketem


def enabled_projects(snapshot: dict, settings: dict) -> dict:
    """{klucz projektu YouTracka: slug projektu tablicy} — projekty z kluczem i włączoną synchronizacją."""
    if not settings.get("sync", True):
        return {}
    out = {}
    for project in snapshot.get("projects") or []:
        if isinstance(project, dict) and project.get("youtrack") and project.get("youtrackSync"):
            out.setdefault(str(project["youtrack"]).upper(), project.get("slug") or "")
    return out


def compute_wants(snapshot: dict, boards: list, settings: dict, records: dict) -> dict:
    """{zgłoszenie: Want} z migawki tablicy (z `boardColumn` i `derived`) i tablic koordynatorów."""
    enabled = enabled_projects(snapshot, settings)
    if not enabled:
        return {}
    cards_by_issue: dict[str, list] = {}
    for card in snapshot.get("cards") or []:
        if not isinstance(card, dict):
            continue
        key = yt.issue_key(card.get("youtrack") or "")
        if key and project_of(key) in enabled:
            cards_by_issue.setdefault(key, []).append(card)
    card_wants = {key: _card_want(key, cards, records.get(key)) for key, cards in cards_by_issue.items()}
    card_wants = {k: w for k, w in card_wants.items() if w is not None}
    ticket_wants = _ticket_wants(boards, enabled, snapshot)
    out = {}
    for key in set(card_wants) | set(ticket_wants):
        card, ticket = card_wants.get(key), ticket_wants.get(key)
        cards = [c["id"] for c in cards_by_issue.get(key, [])]
        if card and ticket:
            manual_later = card.explicit_ts > ticket.explicit_ts and not card.held
            chosen = card if card.key == "done" or manual_later else ticket
        else:
            chosen = card or ticket
        chosen.cards = cards
        out[key] = chosen
    return out


def _card_want(key: str, cards: list, record: dict | None) -> Want | None:
    wants = []
    for card in cards:
        column = card.get("boardColumn") or card.get("column") or "backlog"
        wanted = COLUMN_KEYS.get(column)
        if wanted is None:       # Archiwum — zgłoszenie zostaje, jak jest
            continue
        derived = card.get("derived") or {}
        # Jawne przeniesienie to tylko prawdziwa zmiana kolumny (`column-moved`) — zmiana kolejności w Do
        # zrobienia nie zwalnia wstrzymania i nie otwiera zgłoszenia na nowo.
        explicit = float(card.get("columnMovedTs") or 0)
        held = False
        # Czas ma dokładność sekundy — przeniesienie w tej samej sekundzie co zapis Tide jest „po nim”.
        if wanted == "open" and not derived.get("column") and record and record.get("key") not in OPEN_KEYS \
                and int(explicit) < int(float(record.get("ts") or 0)):
            # Karta wróciła do kolumny ręcznej bez przeniesienia (praca zniknęła z widoku) — nie cofamy zgłoszenia.
            wanted, held = record.get("key"), True
        # Stan z pracy agentów, który bywa chwilowy: „czeka” (każde pytanie o zgodę) i rozwiązane z kolumny
        # wyliczonej (Do sprawdzenia, Gotowe po scaleniu) — zapis dopiero, gdy się utrzyma.
        slow = not held and (wanted == "waiting" or (wanted in RESOLVED_KEYS and bool(derived.get("column"))))
        wants.append(Want(issue=key, key=wanted, via="card", label=f"karta {card.get('id', '')}",
                          link_ts=float(card.get("createdTs") or 0), held=held, explicit_ts=explicit, slow=slow))
    if not wants:
        return None
    # Kilka kart przy jednym zgłoszeniu: wygrywa ostatnio jawnie przeniesiona (bez wstrzymanych, jeśli się da).
    return max(wants, key=lambda w: (not w.held, w.explicit_ts, w.link_ts))


def _ticket_wants(boards: list, enabled: dict, snapshot: dict) -> dict:
    done_coordinators = _done_cards(snapshot, boards)
    found: dict[str, tuple] = {}
    for board in boards or []:
        if not isinstance(board, dict):
            continue
        plan = board.get("plan") or {}
        if (plan.get("tracker") or "") != "youtrack":
            continue
        plan_ts = float(plan.get("updatedTs") or 0)
        uses_review = any(isinstance(r, dict) and r.get("role") == "reviewer" for r in board.get("runs") or []) \
            or any(isinstance(t, dict) and t.get("review") for t in board.get("tickets") or [])
        live = bool(board.get("live"))
        for ticket in board.get("tickets") or []:
            if not isinstance(ticket, dict) or ticket.get("source") == "tracker":
                continue
            key = yt.issue_key(ticket.get("id") or "")
            wanted = TICKET_KEYS.get(ticket.get("state") or "")
            if not key or not wanted or project_of(key) not in enabled:
                continue
            if wanted == "review" and live and uses_review and not _passed_review(ticket):
                # Poprawki po review skończone, ponowne review jeszcze się nie zaczęło — to wciąż W toku.
                wanted = "progress"
            if wanted == "review" and _card_done(done_coordinators, board.get("id") or "", key):
                wanted = "done"
            activity = max(float(ticket.get("lastActivityTs") or 0), plan_ts)
            events = [float(e.get("ts") or 0) for e in ticket.get("events") or [] if isinstance(e, dict) and e.get("ts")]
            link = min(events) if events else 0.0
            title = (plan.get("title") or board.get("title") or "").strip()
            want = Want(issue=key, key=wanted, via="ticket", coordinator=board.get("id") or "",
                        label=f"ticket {key}" + (f" · plan „{title[:60]}”" if title else ""),
                        link_ts=link, explicit_ts=activity, slow=wanted in RESOLVED_KEYS + ("waiting",))
            rank = (activity, float(board.get("lastActivityTs") or 0))
            if key not in found or rank > found[key][0]:
                found[key] = (rank, want)
    return {key: item[1] for key, item in found.items()}


def _passed_review(ticket: dict) -> bool:
    """Ticket `gotowe` z pracy podagentów ma za sobą review bez uwag po ostatniej pracy (albo to samo rozpoznanie)."""
    if ticket.get("source") != "runs":
        return True              # stan z planu — decyzja koordynatora
    note = ticket.get("note") or ""
    if "rozpoznanie" in note:
        return True
    review = ticket.get("review") or {}
    return bool(review.get("ok")) and "bez ponownego review" not in note


def _done_cards(snapshot: dict, boards=()) -> list:
    """[(koordynator, tickety karty)] kart w Gotowe połączonych z koordynatorem — ticket gotowe → `done`.
    Karta przeniesiona do Gotowe ręcznie nie ma `derived` — jej koordynatora szukamy po `session` karty
    i po kluczu `card` planu."""
    ids = [b.get("id") or "" for b in boards or [] if isinstance(b, dict)]
    out = []
    for card in snapshot.get("cards") or []:
        if not isinstance(card, dict) or (card.get("boardColumn") or card.get("column")) != "done":
            continue
        derived = card.get("derived") or {}
        tickets = [str(t).upper() for t in derived.get("tickets") or []]
        found = {derived["coordinator"]} if derived.get("coordinator") else set()
        session = (card.get("session") or "").strip()
        if session:
            found.update(i for i in ids if i == session or i.split(":", 1)[-1] == session)
        found.update(b.get("id") or "" for b in boards or [] if isinstance(b, dict)
                     and ((b.get("plan") or {}).get("card") or "").upper() == (card.get("id") or "").upper())
        out.extend((ident, tickets) for ident in found if ident)
    return out


def _card_done(done: list, coordinator: str, key: str) -> bool:
    return any(c == coordinator and (not tickets or key in tickets) for c, tickets in done)


class Client:
    """Klient YouTracka z ustawień (adres, token z pliku przy każdym zapytaniu)."""

    def __init__(self, config_loader=yt.load_config, timeout: float = yt.TIMEOUT, opener=None):
        self.config_loader = config_loader
        self.timeout = timeout
        self.opener = opener

    def _conf(self) -> tuple[str, str]:
        config = self.config_loader()
        if not config.get("url"):
            raise yt.YouTrackError("brak adresu YouTracka w ustawieniach")
        token = yt.read_token(config)
        if not token:
            raise yt.YouTrackError(yt.missing_token(config))
        return config["url"], token

    def url(self) -> str:
        try:
            return self.config_loader().get("url") or ""
        except Exception:
            return ""

    def fetch(self, ident: str, state_field: str) -> dict:
        url, token = self._conf()
        return yt.fetch_issue(url, ident, token, state_field, timeout=self.timeout, opener=self.opener)

    def set_state(self, ident: str, state: str, state_field: str, field_type: str) -> dict:
        url, token = self._conf()
        return yt.set_state(url, ident, state, token, state_field, field_type, timeout=self.timeout,
                            opener=self.opener)

    def mine(self, queries) -> list:
        url, token = self._conf()
        return yt.fetch_mine(url, token, queries, timeout=self.timeout, opener=self.opener)


class SyncEngine:
    """Stan synchronizacji i decyzje: observe/due/resolve/decorate w wątku głównym, process i fetch_import
    w jednym wątku roboczym. Wspólne dane chroni jeden zamek (bez sieci pod zamkiem)."""

    def __init__(self, path: str | None = None, client: Client | None = None,
                 settings_loader=yt.load_sync_settings, clock=time.time, debounce: float = DEBOUNCE_S):
        self.path = path or state_path()
        self.client = client or Client()
        self.settings_loader = settings_loader
        self.clock = clock
        self.debounce = debounce
        self._lock = threading.Lock()
        self.records: dict[str, dict] = {}
        self.seen: set[str] = set()
        # Licznik „N nowych zgłoszeń”: chwila pierwszego pobrania (ms). Zgłoszenia, które już wtedy były, nie są
        # nowe — liczą się tylko założone później (pole `created`), bez karty i jeszcze niepokazane na liście.
        self.baseline = 0
        self.wants: dict[str, Want] = {}
        self.settings = settings_loader()
        self._since: dict[str, tuple] = {}       # zgłoszenie → (klucz docelowy, od kiedy)
        self._retry: dict[str, tuple] = {}       # zgłoszenie → (kiedy ponowić, liczba prób)
        self._checked: dict[str, float] = {}     # konflikt: ostatnie sprawdzenie
        self._force: set[str] = set()            # „nadpisz z Tide”
        self._errors: dict[str, str] = {}        # zgłoszenie → odmowa YouTracka (4xx) przy ostatniej próbie
        self.inflight: set[str] = set()
        self.offline = ""                        # komunikat, gdy YouTrack nie odpowiada (pusty — w porządku)
        self.ready = False
        self.imported: dict = {"issues": None, "error": "", "ts": 0.0}
        self._load()

    # --- plik stanu ----------------------------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        issues = data.get("issues")
        if isinstance(issues, dict):
            for key, record in issues.items():
                if yt.issue_key(key) == key and isinstance(record, dict) and isinstance(record.get("state"), str):
                    self.records[key] = record
        baseline = data.get("importBaseline")
        if isinstance(baseline, int) and not isinstance(baseline, bool) and baseline > 0:
            self.baseline = baseline
        seen = data.get("seen")
        if isinstance(seen, list):
            self.seen = {s for s in seen if isinstance(s, str) and yt.issue_key(s) == s}

    def save(self) -> None:
        with self._lock:
            payload = {"version": VERSION, "issues": dict(sorted(self.records.items())),
                       "seen": sorted(self.seen)[-SEEN_MAX:], "importBaseline": self.baseline}
        folder = os.path.dirname(self.path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".youtrack-sync-", dir=folder)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # --- wątek główny ----------------------------------------------------------------------------

    def observe(self, snapshot: dict, boards: list, ready: bool = True) -> None:
        """Nowe wyliczenie stanów, których chce Tide. Odliczanie DEBOUNCE_S biegnie od zmiany klucza docelowego."""
        try:
            self.settings = self.settings_loader()
        except Exception:  # zły plik ustawień — zostają poprzednie
            pass
        with self._lock:
            wants = compute_wants(snapshot, boards, self.settings, self.records)
            now = self.clock()
            if ready and not self.ready:
                self._since.clear()           # odliczanie dopiero od gotowości (start usługi)
            self.ready = ready
            since = {}
            for key, want in wants.items():
                target = target_key(want.key, self.records.get(key))
                previous = self._since.get(key)
                since[key] = previous if previous and previous[0] == target else (target, now)
            self._since = since
            self.wants = wants

    def due(self) -> list:
        """[(Want, klucz docelowy, wymuszenie)] do zapisu albo sprawdzenia teraz (bez tych w toku)."""
        out = []
        now = self.clock()
        stable = max(self.debounce, float(self.settings.get("stableMinutes", yt.DEFAULT_STABLE_MINUTES)) * 60)
        with self._lock:
            if not self.ready:
                return []
            for key, want in self.wants.items():
                if key in self.inflight:
                    continue
                record = self.records.get(key)
                target = target_key(want.key, record)
                forced = key in self._force
                wait = stable if want.slow and target != (record or {}).get("key") else self.debounce
                if not forced and now - self._since.get(key, (target, now))[1] < wait:
                    continue
                retry = self._retry.get(key)
                if retry and now < retry[0] and not forced:
                    continue
                if forced:
                    pass
                elif record is None:
                    if want.held:
                        continue
                elif record.get("conflict"):
                    conflict = record["conflict"]
                    if conflict.get("key") == target and now - self._checked.get(key, 0) < CONFLICT_RECHECK_S:
                        continue
                elif record.get("key") == target:
                    continue
                out.append((want, target, forced))
                self.inflight.add(key)
        return out

    def resolve(self, issue: str, choice: str) -> dict:
        """Wybór użytkownika przy konflikcie: `youtrack` — Tide przyjmuje stan z YouTracka (bez sieci);
        `tide` — następny krok zapisze stan Tide mimo zmiany ręcznej."""
        key = yt.issue_key(issue)
        with self._lock:
            record = self.records.get(key)
            want = self.wants.get(key)
            if choice not in ("youtrack", "tide"):
                return {"ok": False, "error": "bad-request", "message": "wybór to „youtrack” albo „tide”"}
            if not key or record is None or not record.get("conflict"):
                return {"ok": False, "error": "not-found", "message": f"zgłoszenie {issue} nie ma konfliktu z YouTrackiem"}
            conflict = record["conflict"]
            if choice == "youtrack":
                target = target_key(want.key, record) if want else conflict.get("key", record.get("key"))
                record.update(state=conflict.get("youtrack", ""), updated=conflict.get("updated", record.get("updated", 0)),
                              key=target, ts=self.clock(), conflict=None, adopted=True)
                self._retry.pop(key, None)
            else:
                self._force.add(key)
                self._retry.pop(key, None)
        if choice == "youtrack":
            self.save()
        return {"ok": True, "issue": key, "choice": choice}

    # --- wątek roboczy ---------------------------------------------------------------------------

    def process(self, items: list) -> bool:
        """Sprawdzenie i zapis zgłoszeń z `due()`. True, gdy coś się zmieniło (stan, konflikt, dostępność)."""
        changed = False
        for want, target, forced in items:
            try:
                changed = self._process_one(want, target, forced) or changed
            finally:
                with self._lock:
                    self.inflight.discard(want.issue)
        if changed:
            try:
                self.save()
            except OSError as exc:
                print(f"tide-daemon: YouTrack: nie zapisano stanu synchronizacji: {exc}", flush=True)
        return changed

    def _process_one(self, want: Want, target: str, forced: bool) -> bool:
        key = want.issue
        project = project_of(key)
        names = yt.states_for(self.settings, project)
        name = names.get(target) or yt.DEFAULT_STATES[target]
        field_name = self.settings.get("stateField") or "State"
        now = self.clock()
        try:
            issue = self.client.fetch(key, field_name)
            with self._lock:
                record = self.records.get(key)
            if forced:
                action = "write"
            elif record is None:
                first_push = (target not in OPEN_KEYS and not self._resolved(issue) and not same(issue["state"], name)
                              and want.link_ts > 0 and issue["updated"] <= want.link_ts * 1000 + LINK_SLACK_MS)
                action = "write" if first_push else "adopt"
            elif same(issue["state"], name):
                action = "agree"
            elif not same(issue["state"], record.get("state", "")):
                action = "conflict"
            else:
                action = "write"
            if action == "write":
                result = self.client.set_state(key, name, issue["stateField"], issue["stateType"])
                if not same(result["state"], name):
                    raise yt.YouTrackError(f"YouTrack zostawił stan „{result['state'] or 'bez stanu'}”", 400)
                issue = result
        except yt.YouTrackError as exc:
            return self._failed(key, exc, now)
        except Exception as exc:  # błąd klienta nie może położyć usługi ani zdradzić tokenu
            print(f"tide-daemon: YouTrack: {key}: {type(exc).__name__}", flush=True)
            return self._failed(key, yt.YouTrackError("błąd zapytania do YouTracka"), now)
        with self._lock:
            before = json.dumps(self.records.get(key), sort_keys=True)
            was_offline = self.offline
            self.offline = ""
            had_error = self._errors.pop(key, None)
            self._retry.pop(key, None)
            self._force.discard(key)
            if action == "conflict":
                self._checked[key] = now
                record = dict(self.records.get(key) or {})
                record["conflict"] = {"youtrack": issue["state"], "tide": name, "key": target,
                                      "updated": issue["updated"], "ts": (record.get("conflict") or {}).get("ts") or now}
            else:
                self._checked.pop(key, None)
                record = {"state": issue["state"], "updated": issue["updated"], "key": target, "ts": now,
                          "via": want.via, "conflict": None, "adopted": action == "adopt"}
            self.records[key] = record
            if action in ("write", "adopt", "agree") and key in self.wants:
                self._since[key] = (target, now)
            return was_offline != "" or bool(had_error) or json.dumps(record, sort_keys=True) != before

    def _failed(self, key: str, exc, now: float) -> bool:
        with self._lock:
            attempts = (self._retry.get(key) or (0, 0))[1] + 1
            if exc.offline:
                wait = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
                changed = self.offline != str(exc)
                self.offline = str(exc)
            else:
                wait = REFUSED_RETRY_S
                changed = self._errors.get(key) != str(exc)
                self._errors[key] = str(exc)
            self._retry[key] = (now + wait, attempts)
        return changed

    @staticmethod
    def _resolved(issue: dict) -> bool:
        return bool(issue.get("resolved")) or (issue.get("state") or "").strip().casefold() in RESOLVED_NAMES

    def fetch_import(self) -> dict:
        """Zgłoszenia użytkownika do importu (wątek roboczy): {"issues", "error", "ts"}."""
        try:
            issues, error = self.client.mine(self.settings.get("importQueries") or yt.DEFAULT_IMPORT_QUERIES), ""
        except yt.YouTrackError as exc:
            issues, error = None, str(exc)
        except Exception as exc:  # klient nie może położyć usługi ani zdradzić tokenu
            print(f"tide-daemon: YouTrack: import: {type(exc).__name__}", flush=True)
            issues, error = None, "błąd zapytania do YouTracka"
        first = False
        with self._lock:
            if issues is not None:
                self.imported = {"issues": issues, "error": "", "ts": self.clock()}
                if not self.baseline:   # pierwsze pobranie: wszystko, co już jest, to nie „nowe”
                    first = True
                    # Czas serwera (najnowsze `created`), nie zegar komputera — bez rozjazdu zegarów; pusta lista — teraz.
                    created = [i.get("created") or 0 for i in issues]
                    self.baseline = max(created) if any(created) else int(self.clock() * 1000)
            else:
                self.imported = {**self.imported, "error": error, "ts": self.clock()}
            result = dict(self.imported)
        if first:
            try:
                self.save()
            except OSError as exc:
                print(f"tide-daemon: YouTrack: nie zapisano stanu synchronizacji: {exc}", flush=True)
        return result

    # --- import ----------------------------------------------------------------------------------

    def import_list(self, snapshot: dict) -> list:
        """Zgłoszenia z ostatniego pobrania z kartą (jeśli już jest), projektem tablicy i znacznikiem „nowe”."""
        with self._lock:
            issues = list(self.imported.get("issues") or [])
            seen = set(self.seen)
            baseline = self.baseline
        linked = _linked_cards(snapshot)
        projects = {}
        for project in snapshot.get("projects") or []:
            if isinstance(project, dict) and project.get("youtrack"):
                projects.setdefault(str(project["youtrack"]).upper(), project)
        base = self.client.url()
        out = []
        for issue in issues:
            project = projects.get(issue.get("project") or project_of(issue["id"]))
            out.append({"id": issue["id"], "summary": issue.get("summary") or "", "project": issue.get("project") or "",
                        "state": issue.get("state") or "", "updated": issue.get("updated") or 0,
                        "url": f"{base}/issue/{issue['id']}" if base else "",
                        "card": linked.get(issue["id"], ""), "boardProject": project.get("slug", "") if project else "",
                        "boardProjectName": project.get("name", "") if project else "",
                        "new": issue["id"] not in seen and issue["id"] not in linked
                               and bool(baseline) and (issue.get("created") or 0) > baseline})
        return out

    def issue_for_import(self, ident: str) -> dict | None:
        with self._lock:
            return next((dict(i) for i in self.imported.get("issues") or [] if i.get("id") == ident), None)

    def mark_seen(self, keys) -> None:
        with self._lock:
            before = len(self.seen)
            self.seen.update(k for k in keys if yt.issue_key(k) == k)
            changed = len(self.seen) != before
        if changed:
            try:
                self.save()
            except OSError as exc:
                print(f"tide-daemon: YouTrack: nie zapisano stanu synchronizacji: {exc}", flush=True)

    def new_count(self, snapshot: dict) -> int:
        with self._lock:
            if not self.imported.get("issues"):
                return 0
        return sum(1 for item in self.import_list(snapshot) if item["new"])

    # --- migawka dla okna ------------------------------------------------------------------------

    def status(self, key: str) -> dict:
        """Stan synchronizacji zgłoszenia dla karty: {status, text, issueState, via, conflict}."""
        with self._lock:
            record = self.records.get(key)
            want = self.wants.get(key)
            offline = self.offline
            inflight = key in self.inflight
            retry = key in self._retry
            error = self._errors.get(key, "")
        names = yt.states_for(self.settings, project_of(key))
        out = {"status": "ok", "text": "", "issueState": (record or {}).get("state", ""), "via": "",
               "label": "", "conflict": None}
        if want is None:
            out.update(status="off", text="bez synchronizacji — stan karty nie zmienia zgłoszenia")
            return out
        out["via"], out["label"] = want.via, want.label
        target = target_key(want.key, record)
        name = names.get(target) or yt.DEFAULT_STATES.get(target, target)
        if record and record.get("conflict"):
            conflict = record["conflict"]
            out.update(status="conflict", conflict={"youtrack": conflict.get("youtrack", ""), "tide": name},
                       text=f"YouTrack: stan zmieniony ręcznie — w YouTracku „{conflict.get('youtrack') or 'bez stanu'}”, "
                            f"Tide: „{name}”")
            return out
        pending = record is None or record.get("key") != target or inflight
        if want.held or not pending:
            out["text"] = f"zsynchronizowane — w YouTracku „{record.get('state') or 'bez stanu'}”" if record else ""
            return out
        if error and retry:
            out.update(status="error", text=f"YouTrack nie przyjął „{name}”: {error}")
        elif offline and retry:
            out.update(status="offline", text=f"YouTrack niedostępny — zapisze „{name}”, gdy wróci")
        elif record is None:
            out.update(status="pending", text="sprawdza zgłoszenie w YouTracku")
        elif want.slow:
            minutes = float(self.settings.get("stableMinutes", yt.DEFAULT_STABLE_MINUTES))
            out.update(status="pending", text=f"zapisze „{name}” w YouTracku, gdy stan utrzyma się {minutes:g} min")
        else:
            out.update(status="pending", text=f"zapisze „{name}” w YouTracku")
        return out

    def decorate(self, snapshot: dict) -> dict:
        """Kopia migawki z `youtrackSync` przy kartach, `youtrack` (stan ogólny) i konfliktami w `errors`."""
        out = dict(snapshot)
        base = self.client.url()
        enabled = enabled_projects(snapshot, self.settings)
        cards = []
        conflicts = 0
        errors = list(snapshot.get("errors") or [])
        shown_conflicts = set()
        for card in snapshot.get("cards") or []:
            if not isinstance(card, dict):
                cards.append(card)
                continue
            card = dict(card)
            key = yt.issue_key(card.get("youtrack") or "")
            if not key:
                card["youtrackSync"] = None
            else:
                sync = self.status(key) if project_of(key) in enabled else {
                    "status": "off", "issueState": (self.records.get(key) or {}).get("state", ""), "via": "",
                    "label": "", "conflict": None,
                    "text": f"bez synchronizacji — projekt {project_of(key)} nie ma jej włączonej na tablicy"}
                sync["issue"] = key
                sync["url"] = f"{base}/issue/{key}" if base else ""
                card["youtrackSync"] = sync
                if sync["status"] == "conflict" and key not in shown_conflicts:
                    shown_conflicts.add(key)
                    errors.append({"path": "",
                                   "message": f"{card.get('id', '')} · {key}: {sync['text']} — wybierz w panelu karty"})
            cards.append(card)
        with self._lock:
            records = {k: dict(v) for k, v in self.records.items()}
            wants = dict(self.wants)
            offline = self.offline
            pending = sum(1 for k, w in wants.items() if not w.held and not (records.get(k) or {}).get("conflict")
                          and (records.get(k) is None or records[k].get("key") != target_key(w.key, records.get(k))))
        items = []
        for key, record in sorted(records.items()):
            if record.get("conflict") and key in wants:
                conflicts += 1
                want = wants[key]
                items.append({"issue": key, "label": want.label, "cards": list(want.cards),
                              "youtrack": record["conflict"].get("youtrack", ""), "tide": record["conflict"].get("tide", ""),
                              "url": f"{base}/issue/{key}" if base else ""})
                if key not in shown_conflicts:   # konflikt ticketu bez karty — też na pasku błędów
                    errors.append({"path": "", "message": f"{key} ({want.label}): YouTrack: stan zmieniony ręcznie — "
                                                          f"w YouTracku „{record['conflict'].get('youtrack') or 'bez stanu'}”, "
                                                          f"Tide: „{record['conflict'].get('tide', '')}” — wybierz w „Projekty i tagi”"})
        out["cards"] = cards
        out["errors"] = errors
        out["youtrack"] = {"enabled": bool(enabled), "sync": bool(self.settings.get("sync", True)), "url": base,
                           "available": not offline, "error": offline, "pending": pending, "conflicts": conflicts,
                           "conflictItems": items, "newIssues": self.new_count(snapshot),
                           "importError": self.imported.get("error", ""), "states": dict(self.settings.get("states") or {})}
        return out


def card_title(issue: dict) -> str:
    """Tytuł karty z tytułu zgłoszenia: jedna linia bez znaków sterujących, do 200 znaków (pusty — klucz)."""
    text = "".join(ch if ord(ch) >= 32 else " " for ch in str(issue.get("summary") or ""))
    text = " ".join(text.split())[:200].strip()
    return text or issue.get("id", "")


def card_description(issue: dict, limit: int = 60 * 1024) -> str:
    """Opis karty z opisu zgłoszenia (zwykły tekst, jak w YouTracku): bez znaków sterujących poza \n i \t,
    najwyżej `limit` bajtów UTF-8."""
    text = str(issue.get("description") or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch in "\n\t").strip("\n")
    data = text.encode("utf-8")
    if len(data) > limit:
        text = data[:limit].decode("utf-8", "ignore").rstrip() + "\n\n(opis skrócony — całość w YouTracku)"
    return text


def _linked_cards(snapshot: dict) -> dict:
    out = {}
    for card in snapshot.get("cards") or []:
        if isinstance(card, dict):
            key = yt.issue_key(card.get("youtrack") or "")
            if key:
                out.setdefault(key, card.get("id", ""))
    return out
