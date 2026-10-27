"""Odpowiedź modelu lokalnego dla roli albo pary (punkt, model): jedno wywołanie, adapter według `kind`.

- `ollama` → natywne `POST /api/chat` (`stream: false`, `think: false`, `options.num_ctx` z roli,
  `format` = schemat JSON). Nie `/v1/chat/completions`, bo zgodny z OpenAI punkt Ollamy pomija
  `num_ctx`, a kontekst decyduje, czy model mieści się na karcie 8 GB (PT-13: 8 s zamiast 165 s).
  Model bez przełącznika myślenia odpowiada 400 z „think” w treści — wtedy ponawiamy bez `think`.
- `openai` → `POST /v1/chat/completions` z `response_format` typu `json_schema` (MLX, LM Studio,
  llama.cpp na Macu); kontekst ustawia tam serwer.

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


def _ollama(endpoint, model, messages, schema, ctx, timeout, cancel=None) -> tuple[str, dict]:
    body = {"model": model, "messages": messages, "stream": False, "think": False}
    if ctx:
        body["options"] = {"num_ctx": ctx}
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


def _openai(endpoint, model, messages, schema, ctx, timeout, cancel=None) -> tuple[str, dict]:
    body = {"model": model, "messages": messages, "stream": False}
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


def chat(messages, *, role: str | None = None, endpoint: str | None = None, model: str | None = None,
         system: str | None = None, schema: dict | None = None, ctx: int | None = None,
         timeout: float = DEFAULT_TIMEOUT_S, config=None, cancel=None, priority: int | None = None,
         wait: float | None = None, deadline: float | None = None, queue=None, expect: tuple | None = None) -> Answer:
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
    starcie) → TargetChanged, zanim cokolwiek wyjdzie do serwera."""
    def resolve_target() -> tuple:
        """(punkt, model, kontekst) z ustawień czytanych teraz (albo z podanych `config`)."""
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
        if expect is not None and (target.name, target.public_url(), model_name, context) != tuple(expect):
            raise TargetChanged("Model lokalny: ustawienia modeli zmieniły się w trakcie wywołania")
        return target, model_name, context

    # Cel przy dołączeniu wybiera tor kolejki i od razu zgłasza błąd ustawień (nieznana rola, zły punkt);
    # ostateczny cel ustalamy, gdy wywołanie dostaje kolej — po zmianie ustawień w czasie czekania
    # (inny `default` albo adres, np. z okna Tide) wywołanie idzie już na nowy serwer.
    current = {"target": resolve_target()}
    body = _messages(messages, system)
    lane = queue if queue is not None else queue_mod.shared()
    wait = queue_mod.DEFAULT_WAIT_S if wait is None else wait
    queued = time.monotonic()
    if deadline is not None:
        wait = min(wait, deadline - queued)

    def resolve() -> tuple:
        current["target"] = resolve_target()
        target, model_name, _context = current["target"]
        return queue_mod.lane_of(target.url) or target.name, target.name, model_name, target.host

    def call() -> Answer:
        target, model_name, context = current["target"]
        started = time.monotonic()
        limit = timeout
        if deadline is not None and deadline - started < timeout:
            limit = _limit(deadline - started)
        text, metrics = ADAPTERS[target.kind](target, model_name, body, schema, context, limit, cancel)
        if not isinstance(text, str) or not text.strip():
            raise ModelError(f"Model lokalny: pusta odpowiedź modelu {model_name} ({target.host})")
        metrics = {k: v for k, v in metrics.items() if v is not None}
        metrics["wallS"] = round(time.monotonic() - started, 3)
        metrics["queueS"] = round(started - queued, 3)
        return Answer(text, target.name, model_name, metrics, target.public_url(), context)

    target, model_name, _context = current["target"]
    return lane.run(call, endpoint=target.name, lane=queue_mod.lane_of(target.url) or target.name,
                    role=role or "", model=model_name, priority=priority, wait=wait, cancel=cancel,
                    host=target.host, resolve=resolve)
