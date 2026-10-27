"""Pomiary modeli lokalnych dla PT-13 (bez sudo, tylko odczyt i zapytania do Ollamy).

Użycie: python3 scripts/ollama/check.py [model ...]   (bez argumentów: wszystkie pobrane)

Dla każdego modelu czatu:
- streszczenie po polsku sekcji „Agenci AI” z README (kilka tysięcy tokenów wejścia — tak wygląda
  prawdziwe zadanie raportu): czas ładowania, wczytywanie promptu i generowanie w tokenach/s,
- jaka część modelu siedzi na karcie (/api/ps),
- odpowiedź według schematu JSON (jak „Na daily”): czy wyszła poprawna.
Dla modeli embeddingów: czas i wymiar wektora.
Na koniec zgodność API: /v1/chat/completions (OpenAI, Codex) i /v1/messages (Anthropic, Claude Code).

Wyniki i pełne odpowiedzi trafiają też do build/ollama-check/<data>.md — do oceny polszczyzny.
TIDE_OLLAMA_URL podmienia adres serwera (domyślnie http://127.0.0.1:11434; później Mac Studio).
TIDE_OLLAMA_CTX ustawia kontekst zapytań (num_ctx); domyślnie bierze go serwer. Na karcie 8 GB 16k nie
mieści się razem z modelem 8B, a część na procesorze spowalnia pisanie kilkanaście razy.
"""
import datetime
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("TIDE_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
ROOT = pathlib.Path(__file__).resolve().parents[2]
TIMEOUT = 900
CTX = int(os.environ.get("TIDE_OLLAMA_CTX", "0"))

SCHEMA = {
    "type": "object",
    "properties": {
        "zrobione": {"type": "array", "items": {"type": "string"}},
        "dalej": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["zrobione", "dalej"],
}


def request(path: str, body=None, headers=None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


def readme_section() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.find("## Agenci AI")
    return text[start:start + 12000] if start >= 0 else text[:12000]


def is_embedding(name: str, details: dict) -> bool:
    family = (details.get("family") or "").lower()
    return "bert" in family or "embed" in name or name.startswith("bge")


def chat(model: str, messages: list, **extra) -> tuple[int, dict, float]:
    body = {"model": model, "messages": messages, "stream": False, "think": False, **extra}
    if CTX:
        body["options"] = {"num_ctx": CTX}
    started = time.monotonic()
    status, data = request("/api/chat", body)
    if status == 400 and "think" in json.dumps(data):
        body.pop("think")  # model bez przełącznika myślenia
        status, data = request("/api/chat", body)
    return status, data, time.monotonic() - started


def rate(count, nanos) -> str:
    return f"{count / (nanos / 1e9):.1f} tok/s" if count and nanos else "—"


def on_gpu(model: str) -> str:
    _, data = request("/api/ps")
    for entry in data.get("models", []):
        if entry.get("name") == model or entry.get("model") == model:
            size, vram = entry.get("size") or 0, entry.get("size_vram") or 0
            return f"{100 * vram / size:.0f}% na karcie ({vram / 2**30:.1f} z {size / 2**30:.1f} GB)" if size else "—"
    return "—"


def check_chat(model: str, out: list) -> None:
    prompt = ("Poniżej opis projektu Tide. Streść po polsku w trzech zdaniach, co Tide robi dla osoby "
              "pracującej z agentami AI. Pisz prostym językiem.\n\n" + readme_section())
    status, data, wall = chat(model, [{"role": "user", "content": prompt}])
    if status != 200:
        print(f"  błąd {status}: {data.get('error', '')}")
        out.append(f"## {model}\n\nBłąd {status}: {data.get('error', '')}\n")
        return
    load = (data.get("load_duration") or 0) / 1e9
    print(f"  wejście {data.get('prompt_eval_count')} tok: {rate(data.get('prompt_eval_count'), data.get('prompt_eval_duration'))}")
    print(f"  wyjście {data.get('eval_count')} tok: {rate(data.get('eval_count'), data.get('eval_duration'))}")
    print(f"  ładowanie {load:.1f} s, razem {wall:.1f} s, {on_gpu(model)}")
    answer = data.get("message", {}).get("content", "").strip()
    speed = rate(data.get("prompt_eval_count"), data.get("prompt_eval_duration"))

    status, data, _ = chat(model, [{"role": "user", "content":
                                    "Na podstawie opisu wypisz, co w Tide już działa (zrobione) i co warto "
                                    "zrobić dalej (dalej). Odpowiedz po polsku.\n\n" + readme_section()[:4000]}],
                           format=SCHEMA)
    try:
        parsed = json.loads(data.get("message", {}).get("content", ""))
        valid = isinstance(parsed.get("zrobione"), list) and isinstance(parsed.get("dalej"), list)
    except (ValueError, AttributeError):
        parsed, valid = None, False
    print(f"  schemat JSON: {'poprawny' if valid else 'NIEPOPRAWNY'}")

    out.append(f"## {model}\n\n- wejście: {speed}\n"
               f"- {on_gpu(model)}\n- schemat JSON: {'poprawny' if valid else 'niepoprawny'}\n\n"
               f"### Streszczenie\n\n{answer}\n\n### JSON\n\n```json\n"
               f"{json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else data.get('message', {}).get('content', '')}\n```\n")


def check_embed(model: str, out: list) -> None:
    started = time.monotonic()
    status, data = request("/api/embed", {"model": model, "input": ["Agent czeka na zgodę", "Raport poranny gotowy"]})
    wall = time.monotonic() - started
    vectors = data.get("embeddings") or []
    print(f"  {'błąd ' + str(status) if status != 200 else f'{len(vectors)} wektory po {len(vectors[0])} wymiarów'}, {wall:.1f} s")
    out.append(f"## {model} (embeddingi)\n\n- status {status}, {wall:.1f} s\n")


def check_compat(model: str, out: list) -> None:
    status, data = request("/v1/chat/completions", {"model": model, "messages": [{"role": "user", "content": "Powiedz: ok"}]})
    openai = "działa" if status == 200 and data.get("choices") else f"nie ({status})"
    status, data = request("/v1/messages", {"model": model, "max_tokens": 32,
                                            "messages": [{"role": "user", "content": "Powiedz: ok"}]},
                           headers={"anthropic-version": "2023-06-01", "x-api-key": "local"})
    anthropic = "działa" if status == 200 and data.get("content") else f"nie ({status})"
    print(f"\nZgodność API (na {model}):\n  OpenAI /v1/chat/completions: {openai}\n  Anthropic /v1/messages: {anthropic}")
    out.append(f"## Zgodność API ({model})\n\n- OpenAI /v1/chat/completions: {openai}\n- Anthropic /v1/messages: {anthropic}\n")


def main(argv: list[str]) -> int:
    try:
        _, version = request("/api/version")
    except OSError as exc:
        print(f"Ollama nie odpowiada na {API}: {exc}", file=sys.stderr)
        return 1
    _, tags = request("/api/tags")
    models = {m["name"]: m.get("details", {}) for m in tags.get("models", [])}
    names = argv or sorted(models)
    if not names:
        print("Brak modeli. Najpierw: bash scripts/ollama/pull-models.sh", file=sys.stderr)
        return 1

    out = [f"# Pomiary modeli lokalnych {datetime.datetime.now():%Y-%m-%d %H:%M}\n\n"
           f"Ollama {version.get('version')} na {API}, kontekst {CTX or 'serwera'}\n"]
    chat_models = []
    for name in names:
        print(f"\n== {name}")
        if is_embedding(name, models.get(name, {})):
            check_embed(name, out)
        else:
            check_chat(name, out)
            chat_models.append(name)
    if chat_models:
        check_compat(chat_models[0], out)

    target = ROOT / "build" / "ollama-check" / f"{datetime.datetime.now():%Y-%m-%d_%H%M}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"\nPełne odpowiedzi: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
