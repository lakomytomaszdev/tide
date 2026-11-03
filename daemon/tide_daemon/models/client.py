"""Odpowiedź modelu lokalnego dla roli albo pary (punkt, model): jedno wywołanie, adapter według `kind`.

- `ollama` → natywne `POST /api/chat` (`stream: false`, `think: false`, `options.num_ctx` z roli,
  `format` = schemat JSON). Nie `/v1/chat/completions`, bo zgodny z OpenAI punkt Ollamy pomija
  `num_ctx`, a kontekst decyduje, czy model mieści się na karcie 8 GB (PT-13: 8 s zamiast 165 s).
  Model bez przełącznika myślenia odpowiada 400 z „think” w treści — wtedy ponawiamy bez `think`.
- `openai` → `POST /v1/chat/completions` z `response_format` typu `json_schema` (MLX, LM Studio,
  llama.cpp na Macu); kontekst ustawia tam serwer.

Embeddingi (`embed()`, wyszukiwanie po znaczeniu w KRunnerze, PT-19): `ollama` → `POST /api/embed`,
`openai` → `POST /v1/embeddings` — przez tę samą kolejkę i z tym samym ustalaniem celu.

Każde wywołanie przechodzi przez kolejkę procesu (queue.py): na punkcie jedno naraz, opisy kart przed
raportem, a `timeout` biegnie dopiero od chwili, gdy wywołanie ruszyło — czekanie w kolejce ma własny
limit (`wait`), a `deadline` ogranicza oba razem (budżet raportu).

Każdy błąd (serwer nie działa, limit czasu, limit czekania w kolejce, błąd HTTP, nieczytelna albo pusta
odpowiedź) to ModelError z komunikatem po polsku. Tryb lokalny nigdy nie przechodzi do chmury: ten moduł nie zna Claude
ani żadnego innego zapasowego wywołania (zasada planu z 27.10).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from . import config as config_mod
from . import queue as queue_mod
from .transport import ModelError, request

DEFAULT_TIMEOUT_S = 180.0


class TargetChanged(ModelError):
    """Cel z ustawień czytanych przy starcie wywołania nie jest tym, którego wywołujący oczekiwał (`expect`)
    — wywołanie nie wychodzi do serwera."""


@dataclass(frozen=True)
class Answer:
    text: str
    endpoint: str
    model: str
    metrics: dict = field(default_factory=dict)  # promptTokens, outputTokens, wallS, queueS, loadS, promptS, outputS
    url: str = ""            # adres serwera, który naprawdę odpowiedział (bez danych logowania)
    ctx: int | None = None   # kontekst, z którym poszło wywołanie (None — kontekst serwera)

    def json(self):
        """Odpowiedź według schematu jako obiekt Pythona; zły JSON → ModelError."""
        try:
            return json.loads(self.text)
        except ValueError:
            raise ModelError(f"Model lokalny: odpowiedź modelu {self.model} nie jest poprawnym JSON-em") from None


def _messages(messages, system: str | None) -> list[dict]:
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    out = [{"role": "system", "content": system}] if system else []
    for message in messages or []:
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("wiadomość to {'role': ..., 'content': str}")
        out.append({"role": str(message.get("role") or "user"), "content": message["content"]})
    if not out:
        raise ValueError("brak wiadomości dla modelu")
    return out


def _seconds(nanos) -> float | None:
    return round(nanos / 1e9, 3) if isinstance(nanos, (int, float)) and not isinstance(nanos, bool) else None


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ollama(endpoint, model, messages, schema, ctx, timeout, cancel=None, temperature=None) -> tuple[str, dict]:
    body = {"model": model, "messages": messages, "stream": False, "think": False}
    options = {"num_ctx": ctx} if ctx else {}
    if temperature is not None:
        options["temperature"] = temperature
    if options:
        body["options"] = options
    if schema is not None:
        body["format"] = schema
    try:
        data = request(endpoint, "POST", "/api/chat", body, timeout, cancel)
    except ModelError as exc:
        if exc.status != 400 or "think" not in exc.detail.lower():
            raise
        body.pop("think")  # model bez przełącznika myślenia (jak w scripts/ollama/check.py)
        data = request(endpoint, "POST", "/api/chat", body, timeout, cancel)
    message = data.get("message") if isinstance(data, dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    metrics = {"promptTokens": _count(data.get("prompt_eval_count")), "outputTokens": _count(data.get("eval_count")),
               "loadS": _seconds(data.get("load_duration")), "promptS": _seconds(data.get("prompt_eval_duration")),
               "outputS": _seconds(data.get("eval_duration"))} if isinstance(data, dict) else {}
    return text, metrics


def _openai(endpoint, model, messages, schema, ctx, timeout, cancel=None, temperature=None) -> tuple[str, dict]:
    body = {"model": model, "messages": messages, "stream": False}
    if temperature is not None:
        body["temperature"] = temperature
    if schema is not None:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "answer", "strict": True, "schema": schema}}
    data = request(endpoint, "POST", "/v1/chat/completions", body, timeout, cancel)
    text = None
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        text = message.get("content") if isinstance(message, dict) else None
    usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else {}
    return text, {"promptTokens": _count(usage.get("prompt_tokens")),
                  "outputTokens": _count(usage.get("completion_tokens"))}


ADAPTERS = {"ollama": _ollama, "openai": _openai}


def _limit(value: float) -> float:
    """Limit do komunikatu „nie odpowiedział w N s”: pełne sekundy od 10 s, poniżej z jednym miejscem."""
    return max(round(value) if value >= 10 else round(value, 1), 0.1)


def _resolver(config, role, endpoint, model, ctx, expect, allow_cloud=False):
    """Funkcja () → (punkt, model, kontekst) z ustawień czytanych w chwili wywołania (albo z podanych `config`):
    rola albo jawny model, model chmurowy Ollamy odrzucony, `expect` porównane z celem (TargetChanged).
    `allow_cloud` (PT-42, rola `classify`): model chmurowy przechodzi tylko wtedy, gdy rola przypina go jawnie
    do punktu („punkt:model” w models.json) — nigdy przez `default` ani jawny model spoza roli."""
    def resolve_target() -> tuple:
        cfg = config if config is not None else config_mod.load()
        if role is not None:
            chosen = cfg.role(role)
            endpoint_name, model_name, context = chosen.endpoint, chosen.model, chosen.ctx
        elif model:
            endpoint_name, model_name, context = endpoint, model, None
        else:
            raise ValueError("podaj rolę albo model")
        if ctx is not None:
            context = ctx or None
        target = cfg.endpoint(endpoint_name)
        explicit_cloud = allow_cloud and role is not None and chosen.pinned
        if config_mod.is_cloud_model(model_name) and not explicit_cloud:   # także jawny model, nie tylko rola
            raise ModelError(f"Model lokalny niedostępny: {config_mod.cloud_refusal(model_name)}")
        if expect is not None and (target.name, target.public_url(), model_name, context) != tuple(expect):
            raise TargetChanged("Model lokalny: ustawienia modeli zmieniły się w trakcie wywołania")
        return target, model_name, context
    return resolve_target


def _queued(work, resolve_target, *, role, priority, wait, deadline, cancel, queue):
    """`work(cel, start_kolejki)` w kolejce procesu (queue.py), na torze serwera z celu. Cel przy dołączeniu wybiera
    tor i od razu zgłasza błąd ustawień (nieznana rola, zły punkt); ostateczny cel ustalamy, gdy wywołanie dostaje
    kolej — po zmianie ustawień w czasie czekania (inny `default` albo adres, np. z okna Tide) wywołanie idzie już
    na nowy serwer."""
    current = {"target": resolve_target()}
    lane = queue if queue is not None else queue_mod.shared()
    wait = queue_mod.DEFAULT_WAIT_S if wait is None else wait
    queued = time.monotonic()
    if deadline is not None:
        wait = min(wait, deadline - queued)

    def resolve() -> tuple:
        current["target"] = resolve_target()
        target, model_name, _context = current["target"]
        return queue_mod.lane_of(target.url) or target.name, target.name, model_name, target.host

    target, model_name, _context = current["target"]
    return lane.run(lambda: work(current["target"], queued), endpoint=target.name,
                    lane=queue_mod.lane_of(target.url) or target.name, role=role or "", model=model_name,
                    priority=priority, wait=wait, cancel=cancel, host=target.host, resolve=resolve)


def _call_limit(timeout: float, deadline: float | None, started: float) -> float:
    if deadline is not None and deadline - started < timeout:
        return _limit(deadline - started)
    return timeout


def chat(messages, *, role: str | None = None, endpoint: str | None = None, model: str | None = None,
         system: str | None = None, schema: dict | None = None, ctx: int | None = None,
         timeout: float = DEFAULT_TIMEOUT_S, config=None, cancel=None, priority: int | None = None,
         wait: float | None = None, deadline: float | None = None, queue=None, expect: tuple | None = None,
         temperature: float | None = None, allow_cloud: bool = False) -> Answer:
    """Odpowiedź modelu. `messages` to tekst polecenia albo lista {role, content}; `system` — polecenie
    systemowe na początku. Model z roli (`role`) albo jawnie (`endpoint` — domyślnie `default`, `model`).
    `ctx` nadpisuje kontekst roli. `schema` — schemat JSON odpowiedzi. `cancel` (transport.Cancel) — przerwanie
    z innego wątku (zamykanie usługi), także w kolejce. Błąd → ModelError.

    Kolejka (queue.py, domyślnie wspólna dla procesu): `priority` (queue.HIGH/NORMAL/LOW; domyślnie według
    roli), `wait` — najwyżej tyle sekund w kolejce (domyślnie queue.DEFAULT_WAIT_S), `timeout` — na samo
    wywołanie, liczony od jego startu; `deadline` (time.monotonic()) — czekanie i wywołanie razem kończą się
    najpóźniej wtedy. Tor kolejki to serwer (`queue.lane_of(adres)`), a punkt, model i kontekst są ustalane
    jeszcze raz, gdy wywołanie dostaje kolej (models.json czytany wtedy, chyba że podano `config`).
    `expect` — (punkt, adres, model, kontekst), którego wywołujący oczekuje: inny cel (przy dołączeniu albo przy
    starcie) → TargetChanged, zanim cokolwiek wyjdzie do serwera. `temperature` — temperatura próbkowania
    (nie podana — domyślna serwera). `allow_cloud` — patrz `_resolver` (tylko rola przypięta jawnie)."""
    resolve_target = _resolver(config, role, endpoint, model, ctx, expect, allow_cloud)
    resolve_target()  # zły cel (nieznana rola, model chmurowy) — błąd, zanim zbudujemy wiadomości
    body = _messages(messages, system)

    def call(current, queued) -> Answer:
        target, model_name, context = current
        started = time.monotonic()
        limit = _call_limit(timeout, deadline, started)
        text, metrics = ADAPTERS[target.kind](target, model_name, body, schema, context, limit, cancel,
                                              temperature)
        if not isinstance(text, str) or not text.strip():
            raise ModelError(f"Model lokalny: pusta odpowiedź modelu {model_name} ({target.host})")
        metrics = {k: v for k, v in metrics.items() if v is not None}
        metrics["wallS"] = round(time.monotonic() - started, 3)
        metrics["queueS"] = round(started - queued, 3)
        return Answer(text, target.name, model_name, metrics, target.public_url(), context)

    return _queued(call, resolve_target, role=role, priority=priority, wait=wait, deadline=deadline, cancel=cancel,
                   queue=queue)


# --- embeddingi (PT-19) --------------------------------------------------------------------------

EMBED_TIMEOUT_S = 60.0
EMBED_TEXTS_MAX = 64
# Jak długo serwer trzyma model embeddingów w pamięci po wywołaniu: dość, żeby kolejne zapytania KRunnera
# nie ładowały go od nowa, a na karcie 8 GB szybko oddał miejsce modelowi raportu i opisów kart.
EMBED_KEEP_ALIVE = "2m"


@dataclass(frozen=True)
class Embeddings:
    vectors: list            # po jednym wektorze (lista liczb) na tekst, w kolejności tekstów
    endpoint: str
    model: str
    url: str = ""
    metrics: dict = field(default_factory=dict)   # wallS, queueS, a z Ollamy też loadS, promptTokens

    @property
    def dims(self) -> int:
        return len(self.vectors[0]) if self.vectors else 0


def _vectors(raw, count: int, model: str, host: str) -> list:
    """Lista `count` wektorów tej samej długości z liczb; inaczej ModelError."""
    bad = ModelError(f"Model lokalny: nieczytelna odpowiedź serwera — embeddingi modelu {model} ({host})")
    if not isinstance(raw, list) or len(raw) != count:
        raise bad
    out = []
    for vector in raw:
        if (not isinstance(vector, list) or not vector
                or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vector)):
            raise bad
        out.append([float(x) for x in vector])
    if len({len(v) for v in out}) > 1:
        raise bad
    return out


def _ollama_embed(endpoint, model, texts, ctx, timeout, cancel=None, keep_alive=None) -> tuple[list, dict]:
    body = {"model": model, "input": texts, "truncate": True}
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    if ctx:
        body["options"] = {"num_ctx": ctx}
    data = request(endpoint, "POST", "/api/embed", body, timeout, cancel)
    raw = data.get("embeddings") if isinstance(data, dict) else None
    metrics = {"promptTokens": _count(data.get("prompt_eval_count")),
               "loadS": _seconds(data.get("load_duration"))} if isinstance(data, dict) else {}
    return _vectors(raw, len(texts), model, endpoint.host), metrics


def _openai_embed(endpoint, model, texts, ctx, timeout, cancel=None, keep_alive=None) -> tuple[list, dict]:
    data = request(endpoint, "POST", "/v1/embeddings", {"model": model, "input": texts}, timeout, cancel)
    items = data.get("data") if isinstance(data, dict) else None
    raw = None
    if isinstance(items, list) and all(isinstance(i, dict) for i in items):
        ordered = sorted(items, key=lambda i: i.get("index") if isinstance(i.get("index"), int) else 0)
        raw = [i.get("embedding") for i in ordered]
    usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else {}
    return _vectors(raw, len(texts), model, endpoint.host), {"promptTokens": _count(usage.get("prompt_tokens"))}


EMBED_ADAPTERS = {"ollama": _ollama_embed, "openai": _openai_embed}


def embed(texts, *, role: str | None = "embed", endpoint: str | None = None, model: str | None = None,
          timeout: float = EMBED_TIMEOUT_S, config=None, cancel=None, priority: int | None = None,
          wait: float | None = None, deadline: float | None = None, queue=None,
          keep_alive: str | None = EMBED_KEEP_ALIVE) -> Embeddings:
    """Wektory znaczenia tekstów (`texts` — napis albo lista, najwyżej EMBED_TEXTS_MAX) z modelu roli (domyślnie
    `embed`) albo jawnego modelu. Ollama — `POST /api/embed` (`truncate`, `keep_alive`), serwer zgodny z OpenAI —
    `POST /v1/embeddings`. Ta sama kolejka, ustalanie celu i odmowa modeli chmurowych co w chat(); priorytet
    podaje wywołujący (queue.IDLE dla indeksu w tle, queue.HIGH dla zapytania KRunnera). Błąd → ModelError."""
    if isinstance(texts, str):
        texts = [texts]
    texts = list(texts or [])
    if not texts or not all(isinstance(t, str) and t.strip() for t in texts):
        raise ValueError("embeddingi: podaj niepuste teksty")
    if len(texts) > EMBED_TEXTS_MAX:
        raise ValueError(f"embeddingi: najwyżej {EMBED_TEXTS_MAX} tekstów naraz")
    resolve_target = _resolver(config, role if model is None else None, endpoint, model, None, None)

    def call(current, queued) -> Embeddings:
        target, model_name, context = current
        started = time.monotonic()
        limit = _call_limit(timeout, deadline, started)
        adapter = EMBED_ADAPTERS[target.kind]
        vectors, metrics = adapter(target, model_name, texts, context, limit, cancel, keep_alive)
        metrics = {k: v for k, v in metrics.items() if v is not None}
        metrics["wallS"] = round(time.monotonic() - started, 3)
        metrics["queueS"] = round(started - queued, 3)
        return Embeddings(vectors, target.name, model_name, target.public_url(), metrics)

    return _queued(call, resolve_target, role=role if model is None else None, priority=priority, wait=wait,
                   deadline=deadline, cancel=cancel, queue=queue)
