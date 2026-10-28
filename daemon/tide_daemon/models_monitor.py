"""Obszar Modeli lokalnych w usłudze: odczyt stanu serwerów, zapis ustawień z okna Tide i pobieranie modeli.

Odczyt: co POLL_MS stan serwerów modeli w wątku roboczym. models.json i zapytania HTTP (krótkie
limity, models/status.py) idą poza wątkiem głównym, więc serwer, który nie odpowiada, nie blokuje
D-Bus. Wynik wraca sygnałem Qt; Changed tylko wtedy, gdy JSON stanu się zmienił. Refresh() w trakcie
odczytu zleca jeszcze jeden zaraz po nim — żeby po zmianie models.json albo załadowaniu modelu wynik
był świeży, a nie sprzed chwili.

Zapis (SetEndpoint, RemoveEndpoint, SetDefault, SetRole, SetAgentProgram, SetSemanticSearch, SetReportSummary;
models/settings.py) idzie
w wątku głównym — to zapis małego pliku. Po udanym zapisie stan składamy od razu z nowych ustawień
i ostatnich stanów punktów (punkt nowy albo ze zmienionym adresem jest „nie sprawdzony”: online false
bez błędu), wysyłamy Changed i zlecamy odczyt. Odczyt, który trwał w chwili zapisu, jest odrzucany
(numer pokolenia) — nie cofnie widoku do starych ustawień.

TestEndpoint idzie w osobnej puli (odpowiedź D-Bus później), PullModel — w osobnym wątku na pobieranie
(jedno naraz na punkt), postęp w `pulls` stanu, Changed najwyżej raz na PULL_PUBLISH_MS. stop() przerywa
pobierania i próby (models.Cancel).

Pole `queue` w stanie: kolejka wywołań modeli (models/queue.py) na każdy punkt — co trwa i co czeka
(punkt, rola, model, od kiedy; bez treści poleceń). Zmiany kolejki przychodzą z wątków roboczych (raport,
opisy kart) i trafiają do wątku głównego sygnałem Qt; Changed z ich powodu najwyżej co QUEUE_GAP_MS.
Postęp pobierania i kolejka dzielą jedno dławienie (`_publish_soon`): jeden zegar ostatniej publikacji
i jeden odłożony zapis — oba źródła razem dają Changed najwyżej raz na odstęp.

Pole `search`: wyszukiwanie po znaczeniu w KRunnerze (PT-19) — `enabled` z models.json i stan indeksu
(krunner/semantic.py: state, items, pending, updated, error, model, dims). Zmiany stanu indeksu przychodzą
z jego wątku i idą tym samym dławieniem co kolejka; każdy udany zapis ustawień budzi indeks (włączenie,
wyłączenie, inny model roli `embed`). Usługa bez KRunnera — `state: "inactive"`.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .agents import localmodel
from .models import config as models_config
from .models import pull as models_pull
from .models import queue as models_queue
from .models import settings as models_settings
from .models import status as models_status
from .models.transport import Cancel, ModelError

POLL_MS = 15_000
FIRST_POLL_MS = 1000
PULL_PUBLISH_MS = 1000     # postęp pobierania: Changed najwyżej raz na sekundę
PULL_KEEP_MS = 15_000      # zakończone pobieranie zostaje w `pulls` jeszcze chwilę („pobrano”)
PULL_ERROR_KEEP_MS = 60_000
TESTS_MAX = 4              # próby połączenia w toku naraz; więcej — odmowa od razu, bez rosnącej kolejki
QUEUE_GAP_MS = 1000        # zmiany kolejki modeli: Changed najwyżej raz na sekundę
IDLE = {"running": None, "waiting": []}


def _dump(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True)


def _reply(ok: bool, error: str = "", **extra) -> str:
    return json.dumps({"ok": ok, "error": error, **extra}, ensure_ascii=False)


def _request(text) -> dict | None:
    try:
        value = json.loads(text) if isinstance(text, str) else None
    except (ValueError, RecursionError):  # RecursionError: „[[[[…” bardzo głęboko
        return None
    return value if isinstance(value, dict) else None


class ReportLink:
    """Tryb „Na daily” (report.json, `summary`) w działającej usłudze: odczyt dla Status() i przełączenie.

    `live()` — tryb, którego raport i opisy kart używają teraz; `apply(tryb)` — przełączenie bez restartu
    usługi; `pinned` — tryb wymuszony opcją --report-summary (zapis zmienia wtedy tylko plik)."""

    def __init__(self, path: str | None = None, live=None, apply=None, pinned: bool = False):
        self.path = path
        self.live = live
        self.apply = apply
        self.pinned = pinned

    def state(self) -> dict:
        return models_settings.report_state(self.path, self.live() if self.live else None, self.pinned)

    def set(self, mode: str) -> None:
        models_settings.set_report_summary(mode, self.path)
        if self.apply is not None and not self.pinned:
            self.apply(mode)


class ModelsMonitor(QObject):
    changed = pyqtSignal()
    _fetched = pyqtSignal(object, object, int)   # (stan z models/status.py, stan raportu, pokolenie) — z wątku
    _tested = pyqtSignal(object, object)         # (odpowiedź D-Bus, wynik próby) — z puli prób
    _pulled = pyqtSignal(str, int, object)       # (klucz, numer pobierania, stan) — z wątku pobierania
    _queued = pyqtSignal()                       # zmiana w kolejce modeli albo w indeksie wyszukiwania — z wątków

    def __init__(self, read=models_status.read, poll_ms: int | None = None, report: ReportLink | None = None,
                 pull_keep_ms: int = PULL_KEEP_MS, pull_error_keep_ms: int = PULL_ERROR_KEEP_MS, queue=None,
                 queue_gap_ms: int = QUEUE_GAP_MS, search=None, parent=None):
        super().__init__(parent)
        self.read = read
        self.report = report
        self.search = search      # krunner.semantic.SemanticSearch albo None (usługa bez KRunnera)
        self.queue = queue if queue is not None else models_queue.shared()
        self._queue_gap_ms = max(0, queue_gap_ms)
        try:
            cfg = models_config.load()
        except Exception as exc:  # nieczytelne ustawienia nie mogą zatrzymać startu usługi
            cfg = models_config.defaults(error=f"models.json: nieoczekiwany błąd ({type(exc).__name__}) — "
                                               f"ustawienia domyślne")
        self._base = models_status.pending(cfg)
        self._report = self._report_state()   # report.json to mały plik — od startu, bez czekania na odczyt
        self._pulls: dict[str, dict] = {}
        self._pull_ids: dict[str, int] = {}
        self._pull_seq = 0
        self._known = localmodel.known_models(self._base)
        self._json = _dump(self._compose())
        self._busy = False
        self._again = False
        self._stopped = False
        self._generation = 0
        self._cancel = Cancel()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-models")
        self._tests = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tide-models-test")
        self.pull_keep_ms = pull_keep_ms
        self.pull_error_keep_ms = pull_error_keep_ms
        self._fetched.connect(self._deliver)
        self._tests_running = 0
        self._tested.connect(self._test_done)
        self._pulled.connect(self._pull_progress)
        self._timer = QTimer(self, interval=max(50, poll_ms if poll_ms is not None else POLL_MS))
        self._timer.timeout.connect(self.poll)
        self._throttle = QTimer(self, singleShot=True)   # odłożona publikacja: postęp pobierania i kolejka
        self._throttle.timeout.connect(self._publish)
        self._published = 0.0
        self._logged: dict[str, str] = {}
        self._queued.connect(lambda: self._publish_soon(self._queue_gap_ms))
        self._listener = self._queued.emit  # ten sam obiekt do remove_listener w stop()
        self.queue.add_listener(self._listener)
        if self.search is not None:
            self.search.add_listener(self._listener)

    def start(self) -> None:
        QTimer.singleShot(min(FIRST_POLL_MS, self._timer.interval()), self.poll)
        self._timer.start()

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()
        self._throttle.stop()
        self.queue.remove_listener(self._listener)
        self._cancel.set()  # trwające pobierania i próby połączenia kończą się od razu
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._tests.shutdown(wait=False, cancel_futures=True)

    def known_models(self) -> set:
        """Modele z ról i serwerów z ostatniego odczytu — znacznik „lokalny” sesji Claude Code (agents/)."""
        return set(self._known)

    def status_json(self) -> str:
        return self._json

    # --- odczyt stanu ----------------------------------------------------------------------------

    def refresh(self) -> None:
        if self._busy:
            self._again = True
            return
        self.poll()

    def poll(self) -> None:
        if self._busy or self._stopped:
            return  # po stop() odczyt, który właśnie wrócił, nie zleca kolejnego do zamkniętej puli
        self._busy = True
        self._pool.submit(self._work, self._generation)

    def _work(self, generation: int) -> None:
        try:
            state = self.read()
        except Exception as exc:  # nieoczekiwany błąd nie może zatrzymać odpytywania
            print(f"tide-daemon: Modele lokalne: błąd odczytu stanu ({type(exc).__name__}: {exc})", flush=True)
            state = None
        self._fetched.emit(state, self._report_state(), generation)

    def _report_state(self):
        if self.report is None:
            return None
        try:
            return self.report.state()
        except Exception as exc:  # zły report.json nie może zatrzymać odczytu stanu modeli
            return {"summary": "", "configured": "", "summaryModel": "", "raw": None, "pinned": self.report.pinned,
                    "path": "", "warnings": [f"report.json: nieoczekiwany błąd ({type(exc).__name__})"]}

    def _deliver(self, state, report, generation: int) -> None:
        self._busy = False
        if generation != self._generation:
            self._again = True  # odczyt sprzed zapisu ustawień — wynik nieaktualny, czytamy jeszcze raz
        else:
            if state is not None:
                self._log(state)
                self._base = state
            if report is not None:
                self._report = report
            self._publish()
        if self._again:
            self._again = False
            self.poll()

    def _compose(self) -> dict:
        state = dict(self._base)
        if self.report is not None and self._report is not None:
            state["report"] = self._report
        state["pulls"] = {key: dict(entry) for key, entry in self._pulls.items()}
        state["queue"] = self._queue_view()
        state["search"] = self._search_view()
        return state

    def _search_view(self) -> dict:
        enabled = (self._base.get("search") or {}).get("enabled", True)
        if self.search is None:
            return {"enabled": enabled, "state": "inactive", "items": 0, "pending": 0, "updated": "",
                    "error": "", "model": "", "dims": 0}
        try:
            index = self.search.state()
        except Exception as exc:  # stan indeksu nie może zepsuć Status()
            index = {"state": "error", "error": f"błąd stanu indeksu ({type(exc).__name__})"}
        return {"enabled": enabled, **index}

    def _queue_view(self) -> dict:
        """Kolejka po nazwie punktu. Kolejka ma tory po serwerach (`queue.lane_of(url)`), więc punkty z tym
        samym adresem pokazują ten sam stan toru (wpisy mają `endpoint` — punkt, z którego przyszło
        wywołanie). Każdy punkt z konfiguracji jest w polu (bezczynny — running null, waiting []). Tor bez
        punktu w konfiguracji (wywołanie sprzed zmiany ustawień, np. na starym adresie punktu) nie znika:
        pod nazwą punktu z jego wpisów — jako cały wpis, gdy tej nazwy jeszcze nie ma, a obok bieżącego
        stanu w liście `other` ({lane, running, waiting}), gdy punkt ma już nowy adres."""
        snapshot = self.queue.snapshot()
        endpoints = self._base.get("endpoints") or {}
        out, used = {}, set()
        for name, endpoint in endpoints.items():
            lane = models_queue.lane_of((endpoint or {}).get("url") or "") or name
            used.add(lane)
            out[name] = snapshot.get(lane) or IDLE
        for lane, state in snapshot.items():
            if lane in used:
                continue
            entries = ([state["running"]] if state.get("running") else []) + list(state.get("waiting") or ())
            for name in dict.fromkeys(entry.get("endpoint") or lane for entry in entries):
                if name not in out:
                    out[name] = state
                else:
                    current = dict(out[name])
                    current["other"] = list(current.get("other") or ()) + [dict(state, lane=lane)]
                    out[name] = current
        return out

    def _publish(self, force: bool = False) -> None:
        self._throttle.stop()
        # Modele z ról i serwerów (znacznik „lokalny” sesji, agents/localmodel.py) — z każdego nowego stanu:
        # odczytu i zapisu ustawień z okna.
        self._known = localmodel.known_models(self._base)
        self._published = time.monotonic()
        text = _dump(self._compose())
        if text != self._json or force:
            self._json = text
            self.changed.emit()

    def _publish_soon(self, gap_ms: float) -> None:
        """Publikacja najwyżej raz na `gap_ms` od poprzedniej (dowolnego źródła): od razu, gdy odstęp minął,
        inaczej jedna odłożona — stan bierzemy z chwili publikacji, więc nic nie ginie."""
        if self._throttle.isActive():
            return
        wait = gap_ms - (time.monotonic() - self._published) * 1000
        if wait <= 0:
            self._publish()
        else:
            self._throttle.start(int(wait) + 1)

    # --- zapis ustawień --------------------------------------------------------------------------

    def _write(self, action, *args, reset=()) -> str:
        """Zapis i stan od razu z nowych ustawień. `reset` — punkty, których ostatni stan już nie obowiązuje
        (zmieniony punkt: np. nowy token przy tym samym pliku tokenu) — „niesprawdzone” do odczytu."""
        try:
            action(*args)
        except models_settings.SettingsError as exc:
            return _reply(False, str(exc))
        except Exception as exc:  # przeoczony przypadek: odmowa z komunikatem, usługa działa dalej
            print(f"tide-daemon: Modele lokalne: błąd zapisu ustawień ({type(exc).__name__})", flush=True)
            return _reply(False, f"nieoczekiwany błąd zapisu ustawień ({type(exc).__name__})")
        self._generation += 1
        try:
            cfg = models_config.load()
            known = {k: v for k, v in (self._base.get("endpoints") or {}).items() if k not in reset}
            self._base = models_status.assemble(cfg, known, checked=bool(self._base.get("checked")))
        except Exception as exc:  # stan złożymy przy najbliższym odczycie
            print(f"tide-daemon: Modele lokalne: nie da się złożyć stanu po zapisie ({type(exc).__name__})",
                  flush=True)
        report = self._report_state()
        if report is not None:
            self._report = report
        self._publish(force=True)
        self.refresh()
        if self.search is not None:
            self.search.wake()  # włączenie, wyłączenie albo inny model roli `embed` — indeks zaraz
        return _reply(True)

    def _pulling(self, name) -> str:
        """Odmowa, gdy na punkcie trwa pobieranie (zmiana adresu albo usunięcie zostawiłoby je bez punktu)."""
        busy = [e for e in self._pulls.values() if e["endpoint"] == name and e["running"]]
        if not busy:
            return ""
        return f"na serwerze „{name}” trwa pobieranie modelu {busy[0]['model']} — poczekaj, aż się skończy"

    def set_endpoint(self, text: str) -> str:
        req = _request(text)
        if req is None:
            return _reply(False, "niepoprawne żądanie: oczekiwano obiektu JSON")
        name = req.get("name")
        if isinstance(name, str) and self._pulling(name):
            current = models_config.load().endpoints.get(name)
            wanted, _error = models_config.check_endpoint(name, {
                "url": req.get("url").strip() if isinstance(req.get("url"), str) else req.get("url"),
                "kind": req.get("kind") or (current.kind if current else "ollama")})
            if current is None or wanted is None or (wanted.url, wanted.kind) != (current.url, current.kind):
                return _reply(False, self._pulling(name))
        return self._write(models_settings.set_endpoint, req, reset=(name,) if isinstance(name, str) else ())

    def remove_endpoint(self, name: str) -> str:
        if self._pulling(name):
            return _reply(False, self._pulling(name))
        return self._write(models_settings.remove_endpoint, name)

    def set_default(self, name: str) -> str:
        return self._write(models_settings.set_default, name)

    def set_role(self, text: str) -> str:
        req = _request(text)
        if req is None:
            return _reply(False, "niepoprawne żądanie: oczekiwano obiektu JSON")
        return self._write(models_settings.set_role, req)

    def set_agent_program(self, program: str) -> str:
        return self._write(models_settings.set_agent_program, program)

    def set_semantic_search(self, value: str) -> str:
        return self._write(models_settings.set_semantic_search, value)

    def set_report_summary(self, mode: str) -> str:
        if self.report is None:
            return _reply(False, "raport poranny jest wyłączony w tej usłudze")
        return self._write(self.report.set, mode)

    # --- próba połączenia ------------------------------------------------------------------------

    def test_endpoint(self, text: str, reply) -> None:
        """Jeden odczyt (wersja, liczba modeli) bez zapisu; `reply(json)` w wątku głównym po odczycie."""
        req = _request(text)
        try:
            if req is None:
                raise models_settings.SettingsError("niepoprawne żądanie: oczekiwano obiektu JSON")
            endpoint = models_settings.endpoint_for_test(req)
        except models_settings.SettingsError as exc:
            reply(_reply(False, str(exc), version=None, models=None))
            return

        if self._tests_running >= TESTS_MAX:
            reply(_reply(False, "za dużo prób połączenia naraz — spróbuj za chwilę", version=None, models=None))
            return

        def work():
            result = models_status.probe(endpoint, cancel=self._cancel)
            self._tested.emit(reply, result)

        try:
            self._tests.submit(work)
        except RuntimeError:  # usługa się zamyka
            reply(_reply(False, "usługa się zamyka", version=None, models=None))
            return
        self._tests_running += 1

    def _test_done(self, reply, result: dict) -> None:
        self._tests_running -= 1
        reply(_reply(**result))

    # --- pobieranie modeli -----------------------------------------------------------------------

    def pull_model(self, text: str) -> str:
        """Zaczyna pobieranie w osobnym wątku; od razu {ok, error, key}, postęp w `pulls` stanu."""
        req = _request(text)
        try:
            if req is None:
                raise models_settings.SettingsError("niepoprawne żądanie: oczekiwano obiektu JSON")
            endpoint, model = models_settings.endpoint_for_pull(req)
        except models_settings.SettingsError as exc:
            return _reply(False, str(exc), key="")
        if self._cancel.is_set():
            return _reply(False, "usługa się zamyka", key="")
        busy = [e for e in self._pulls.values() if e["endpoint"] == endpoint.name and e["running"]]
        if busy:
            return _reply(False, f"na serwerze „{endpoint.name}” trwa już pobieranie {busy[0]['model']} — poczekaj, "
                                 f"aż się skończy", key="")
        key = f"{endpoint.name}/{model}"
        self._pull_seq += 1
        number = self._pull_seq
        self._pull_ids[key] = number
        self._pulls[key] = {"endpoint": endpoint.name, "model": model, "status": "start", "completed": 0, "total": 0,
                            "running": True, "error": ""}
        threading.Thread(target=self._pull_work, args=(key, number, endpoint, model), name="tide-models-pull",
                         daemon=True).start()
        self._publish()
        return _reply(True, key=key)

    def _pull_work(self, key: str, number: int, endpoint, model: str) -> None:
        base = {"endpoint": endpoint.name, "model": model}

        def progress(p: dict) -> None:
            if p["status"] != "success":  # koniec przychodzi niżej, już z running: false
                self._pulled.emit(key, number, {**base, **p, "running": True, "error": ""})

        try:
            models_pull.pull(endpoint, model, progress, cancel=self._cancel)
            final = {**base, "status": "success", "running": False, "error": ""}
        except ModelError as exc:
            final = {**base, "status": "error", "running": False, "error": str(exc)}
        except Exception as exc:  # przeoczony przypadek: błąd w stanie, wątek kończy się normalnie
            final = {**base, "status": "error", "running": False,
                     "error": f"Model lokalny: błąd pobierania ({type(exc).__name__})"}
        self._pulled.emit(key, number, final)

    def _pull_progress(self, key: str, number: int, entry: dict) -> None:
        if self._pull_ids.get(key) != number:
            return
        previous = self._pulls.get(key) or {}
        if not entry["running"]:
            # Koniec: postęp z ostatniego odczytu zostaje (przy sukcesie — całość pobrana).
            completed, total = previous.get("completed", 0), previous.get("total", 0)
            if entry["status"] == "success":
                completed = total
            self._pulls[key] = {**entry, "completed": completed, "total": total}
            self._publish()
            keep = self.pull_keep_ms if entry["status"] == "success" else self.pull_error_keep_ms
            QTimer.singleShot(keep, lambda: self._pull_expire(key, number))
            if entry["status"] == "success":
                print(f"tide-daemon: Modele lokalne: pobrano {entry['model']} na {entry['endpoint']}", flush=True)
                self.refresh()  # nowy model na liście punktu
            else:
                print(f"tide-daemon: Modele lokalne: {entry['error']}", flush=True)
            return
        self._pulls[key] = entry
        self._publish_soon(PULL_PUBLISH_MS)

    def _pull_expire(self, key: str, number: int) -> None:
        if self._pull_ids.get(key) == number and not self._pulls.get(key, {}).get("running"):
            self._pulls.pop(key, None)
            self._pull_ids.pop(key, None)
            self._publish()

    # --- dziennik --------------------------------------------------------------------------------

    def _log(self, state: dict) -> None:
        """Do dziennika tylko zmiany: punkt zaczął albo przestał odpowiadać, błąd albo ostrzeżenia w models.json."""
        config = state.get("config") or {}
        lines = {"config": config.get("error", ""), "warnings": " · ".join(config.get("warnings") or ())}
        for name, endpoint in (state.get("endpoints") or {}).items():
            lines[f"endpoint:{name}"] = (f"{name}: odpowiada (wersja {endpoint.get('version') or '?'})"
                                         if endpoint.get("online") else f"{name}: {endpoint.get('error', '')}")
        for key, line in lines.items():
            if line != self._logged.get(key, "" if key in ("config", "warnings") else None):
                if line:
                    print(f"tide-daemon: Modele lokalne: {line}", flush=True)
                self._logged[key] = line
