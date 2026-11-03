"""Drugi stopień rozpoznawania pytań (PT-42): model ocenia końcówki, w których reguły wątpią.

Reguły (`question.py`) są pierwszym stopniem. Gdy `question.doubtful(text)` wskazuje wątpliwą końcówkę („uruchom ten
select i wklej mi wynik”, „Daj znać.”, pytanie retoryczne), `agents/state.py` pyta tu o werdykt (`Classifier.judge`):

- werdykt zapamiętany → zwracamy go od ręki; nieznany → `None` (reguły liczą same) i ocena idzie do kolejki,
- ocena działa w osobnym wątku, przez kolejkę modeli (`models.queue`, rola `classify`, priorytet nad raportem,
  jedno wywołanie naraz): odczyt stanu sesji nigdy nie czeka na model; po wyniku wołamy `on_result`
  (monitor przelicza stan),
- werdykt pamiętamy na uuid wpisu z wiadomością (a bez niego — sesja i skrót tekstu): w pamięci i w pliku
  `$XDG_STATE_HOME/tide/classify.json` (przycinanym do MAX_RESULTS najnowszych), więc ta sama wiadomość jest
  oceniana najwyżej raz, także po restarcie usługi,
- rola `classify` z models.json (domyślnie `qwen3:8b` na punkcie `default`; `think: false`, JSON, `temperature: 0`,
  limit 20 s): model chmurowy (`punkt:model-cloud`) działa tylko wtedy, gdy rola przypina go jawnie do punktu —
  wtedy ocena idzie tam i tylko tam, a bez jawnego punktu model chmurowy nie jest wołany nigdy,
- rola bez modelu, z nieznanym lub pominiętym punktem albo niedostępna → ocena wyłączona, liczą same reguły,
  ostrzeżenie w dzienniku usługi; błąd modelu (brak serwera, zły JSON, przekroczony czas) → tak samo, błąd
  w dzienniku. Nigdy zastępstwa innym punktem: ten moduł woła `chat(role="classify")` i nic więcej (decyzja 70).

Treść wiadomości (jej końcówka, najwyżej TEXT_MAX znaków) wychodzi tylko do punktu roli `classify`.
"""
from __future__ import annotations

import collections
import json
import os
import threading
import time

from ..models import config as models_config
from ..models import queue as queue_mod
from ..models.transport import Cancel, ModelError

ROLE = "classify"
TIMEOUT_S = 20.0           # limit samego wywołania
WAIT_S = 120.0             # najwyżej tyle w kolejce modeli (serwer zajęty raportem)
TEXT_MAX = 1200            # znaków końcówki wiadomości wysyłanych do modelu
MAX_RESULTS = 500          # werdykty w pliku stanu (najnowsze)
RETRY_S = 300.0            # po błędzie modelu ta sama wiadomość wraca do oceny najwcześniej po tylu sekundach
DISABLED_RETRY_S = 60.0    # po wyłączeniu oceny (zła rola) — sprawdzenie ustawień od nowa
YIELD_S = 30.0             # po ocenie ustępujemy innym czekającym w kolejce (raportowi) najwyżej tyle sekund

SCHEMA = {"type": "object", "properties": {"waits": {"type": "boolean"}}, "required": ["waits"]}
PROMPT = ("Oceń końcówkę wiadomości asystenta AI do użytkownika. waits=true TYLKO gdy asystent nie może iść dalej bez "
          "użytkownika: zadał mu pytanie, prosi o decyzję, wybór, wklejenie wyniku albo wykonanie kroku. waits=false, gdy "
          "raportuje skończoną pracę, zapowiada, że sam da znać, proponuje coś opcjonalnie ('jeśli chcesz, mogę…') albo "
          "zadaje pytanie retoryczne i sam na nie odpowiada.\n"
          "Przykłady: 'Scalone i zainstalowane.' -> false; 'Agent pracuje, dam znać.' -> false; "
          "'Gotowe. Jeśli chcesz, dodam też X.' -> false; 'Czy warto X? Raczej nie.' -> false; "
          "'Który wariant wolisz?' -> true; 'Wklej mi wynik tego polecenia.' -> true.\n"
          "Odpowiedz JSON: {\"waits\": true|false}.\n\nWiadomość:\n")


def state_path(env=None) -> str:
    """$TIDE_CLASSIFY_STATE (testy), inaczej $XDG_STATE_HOME/tide/classify.json."""
    env = os.environ if env is None else env
    if env.get("TIDE_CLASSIFY_STATE"):
        return env["TIDE_CLASSIFY_STATE"]
    base = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "tide", "classify.json")


def tail_of(text: str, limit: int = TEXT_MAX) -> str:
    """Końcówka wiadomości dla modelu: ostatnie `limit` znaków, cięte na granicy słowa."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    space = cut.find(" ")
    return "…" + (cut[space + 1:] if 0 <= space < 80 else cut)


class Classifier:
    """Werdykty modelu o końcówkach tur; `judge(key, text)` nie blokuje, ocena idzie w osobnym wątku.

    config   — funkcja() → ModelsConfig (domyślnie models.json czytany teraz),
    chat     — funkcja jak models.chat (testy podmieniają; domyślnie models.client.chat),
    on_result — funkcja() wołana w wątku oceny po nowym werdykcie (monitor przelicza stan w wątku Qt),
    queue    — kolejka modeli (domyślnie wspólna dla procesu)."""

    def __init__(self, config=None, chat=None, path: str | None = None, on_result=None, queue=None,
                 clock=time.time, monotonic=time.monotonic, log=None):
        self._config = config or models_config.load
        self._chat = chat
        self.path = path or state_path()
        self.on_result = on_result
        self.queue = queue
        self._clock = clock
        self._monotonic = monotonic
        self._log = log or (lambda text: print(f"tide-daemon: rozpoznawanie pytań: {text}", flush=True))
        self.cancel = Cancel()
        self._cond = threading.Condition()
        self._results: dict[str, dict] = self._load()
        self._pending: collections.OrderedDict[str, str] = collections.OrderedDict()
        self._inflight: str | None = None
        self._retry_at: dict[str, float] = {}   # klucz → najwcześniej wtedy ocena wraca (zegar monotoniczny)
        self._warned: set[str] = set()
        self._thread: threading.Thread | None = None
        self._closed = False
        self.calls = 0   # ile razy poszło wywołanie modelu (testy)

    # --- odczyt: nigdy nie czeka na model ------------------------------------------------------------------

    def judge(self, key: str, text: str) -> bool | None:
        """Werdykt dla wiadomości: True — agent czeka na użytkownika, False — nie, None — jeszcze nie wiadomo
        (ocena zaplanowana albo wyłączona). Nie blokuje; nigdy nie woła modelu we własnym wątku."""
        with self._cond:
            found = self._results.get(key)
            if found is not None:
                return found["waits"]
            if self._closed or key in self._pending or key == self._inflight:
                return None
            if self._retry_at.get(key, 0.0) > self._monotonic():
                return None
            self._pending[key] = text
            self._start()
            self._cond.notify_all()
        return None

    def verdict(self, key: str) -> bool | None:
        """Zapamiętany werdykt albo None (bez planowania oceny)."""
        with self._cond:
            found = self._results.get(key)
            return None if found is None else found["waits"]

    def idle(self) -> bool:
        """Nic nie czeka na ocenę ani nie jest oceniane (testy)."""
        with self._cond:
            return not self._pending and self._inflight is None

    def close(self) -> None:
        """Zamknięcie usługi: czekające oceny są porzucane, trwające tracą gniazdo (transport.Cancel)."""
        with self._cond:
            self._closed = True
            self._pending.clear()
            self._cond.notify_all()
        self.cancel.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(2.0)

    # --- wątek oceny ---------------------------------------------------------------------------------------

    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="tide-classify", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._pending and not self._closed:
                    if not self._cond.wait(60.0) and not self._pending:
                        self._thread = None   # nic do roboty — wątek kończy się, następna ocena zacznie nowy
                        return
                if self._closed:
                    return
                key, text = self._pending.popitem(last=False)
                self._inflight = key
            try:
                self._evaluate(key, text)
            except Exception as exc:  # błąd oceny nie może zatrzymać wątku ani usługi
                self._fail(key, f"nieoczekiwany błąd ({type(exc).__name__}) — liczą same reguły")
            finally:
                with self._cond:
                    self._inflight = None
            self._yield_to_others()

    def _evaluate(self, key: str, text: str) -> None:
        problem = self.unavailable(self._config())
        if problem:
            self._disabled(key, problem)
            return
        chat = self._chat or _default_chat()
        try:
            self.calls += 1
            answer = chat(PROMPT + tail_of(text), role=ROLE, schema=SCHEMA, timeout=TIMEOUT_S, wait=WAIT_S,
                          temperature=0, cancel=self.cancel, queue=self.queue, allow_cloud=True)
            data = answer.json()
        except ModelError as exc:
            self._fail(key, f"{exc} — liczą same reguły")
            return
        waits = data.get("waits") if isinstance(data, dict) else None
        if not isinstance(waits, bool):
            self._fail(key, "odpowiedź modelu bez pola waits (true/false) — liczą same reguły")
            return
        with self._cond:
            self._results[key] = {"waits": waits, "at": round(self._clock(), 3)}
            self._retry_at.pop(key, None)
            self._prune()
            self._save()
        if self.on_result is not None:
            try:
                self.on_result()
            except Exception as exc:  # odbiorca (monitor) nie może zepsuć oceny
                self._log(f"powiadomienie o wyniku: {exc!r}")

    @staticmethod
    def unavailable(cfg) -> str:
        """Powód po polsku, gdy rola `classify` nie nadaje się do oceny ("" — można oceniać): brak roli, brak
        modelu, nieznany albo pominięty punkt, rola niedostępna (np. model chmurowy bez jawnego punktu)."""
        role = cfg.roles.get(ROLE)
        if role is None:
            return "brak roli classify w models.json"
        if role.error:
            return f"rola classify niedostępna: {role.error}"
        if not role.model.strip():
            return "rola classify bez modelu"
        if role.endpoint not in cfg.endpoints:
            return f"rola classify: nieznany serwer „{role.endpoint[:40]}”"
        return ""

    def _disabled(self, key: str, reason: str) -> None:
        self._retry_at[key] = self._monotonic() + DISABLED_RETRY_S
        if reason not in self._warned:   # raz na powód, nie co minutę
            self._warned = {reason}
            self._log(f"ocena modelem wyłączona — {reason}; liczą same reguły")

    def _fail(self, key: str, reason: str) -> None:
        self._retry_at[key] = self._monotonic() + RETRY_S
        self._log(reason)

    def _yield_to_others(self) -> None:
        """Ocena ma priorytet nad raportem, ale kolejna nie może go zagłodzić: gdy w kolejce czeka coś innego niż
        ocena, nie dokładamy następnej, dopóki tamto nie ruszy (najwyżej YIELD_S)."""
        lane = self.queue if self.queue is not None else queue_mod.shared()
        deadline = self._monotonic() + YIELD_S
        while not self._closed and self._monotonic() < deadline:
            others = any(item["role"] != ROLE for lane_state in lane.snapshot().values()
                         for item in lane_state["waiting"])
            if not others:
                return
            time.sleep(0.05)

    # --- plik stanu ----------------------------------------------------------------------------------------

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            raw = data.get("results") if isinstance(data, dict) else None
        except (OSError, ValueError):
            return {}
        out = {}
        for key, value in (raw.items() if isinstance(raw, dict) else ()):
            if isinstance(key, str) and isinstance(value, dict) and isinstance(value.get("waits"), bool):
                at = value.get("at")
                out[key] = {"waits": value["waits"], "at": at if isinstance(at, (int, float)) else 0.0}
        return out

    def _prune(self) -> None:
        if len(self._results) > MAX_RESULTS:
            newest = sorted(self._results.items(), key=lambda item: item[1]["at"], reverse=True)[:MAX_RESULTS]
            self._results = dict(newest)

    def _save(self) -> None:
        """Atomowo (plik tymczasowy i rename); błąd zapisu to tylko dziennik — werdykt zostaje w pamięci."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "results": self._results}, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as exc:
            self._log(f"nie da się zapisać {self.path} ({type(exc).__name__})")


def _default_chat():
    from ..models import client
    return client.chat
