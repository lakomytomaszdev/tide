"""Przykładowe odpowiedzi Status() obiektu /Models (tide-daemon, PT-25) dla podglądu i testów widoku Modele.

Warianty (nazwa → Status):
  local        — tylko ten komputer: Ollama działa, qwen3:8b w pamięci w całości na karcie, raport lokalnie,
  mac-offline  — ten komputer i Mac Studio, który nie odpowiada; agent przypięty do Maca, raport z Claude,
  mac          — Mac Studio jako używany serwer (działa, duże modele) i serwer zgodny z OpenAI na Macu;
                 agent przypięty do Maca, który jest też używanym serwerem, program agenta Claude Code
                 (`agentProgram`, PT-39; w pozostałych wariantach Codex),
  pulling      — pobieranie w toku i nieudane, rola z modelem, którego jeszcze nie ma na serwerze,
  errors       — używany serwer nie odpowiada, ostrzeżenie z models.json, tryb raportu wymuszony opcją
                 --report-summary local (w pliku claude),
                 nowy serwer jeszcze niesprawdzony,
  checking     — pierwszy odczyt po starcie usługi (checked: false),
  busy         — jak local, ale kolejka modeli (PT-16, `queue`) pracuje: raport trwa, opisy kart czekają,
                 a na starym adresie serwera (adres zmieniony w trakcie) trwa jeszcze jedno wywołanie (`other`);
                 na serwerze jest też pobrany model chmurowy Ollamy (`cloud: true`) — nie do wybrania w rolach.

Rola `embed` i pole `search` (wyszukiwanie po znaczeniu w KRunnerze, PT-19): local — indeks gotowy,
mac-offline — wyłączone, mac — indeksuje (bge-m3 na Macu), pulling — modelu bge-m3 nie ma na serwerze,
errors — usługa bez KRunnera, checking — czeka na pierwsze indeksowanie, busy — błąd indeksu.

Role mają `pinned` (PT-25 po review: przypięta do serwera czy za domyślnym); stara usługa go nie podaje
— test usuwa pole i sprawdza, że widok pokazuje role tylko do odczytu.

Rozmiary i wersje jak na tym komputerze (PT-13), bez ścieżek prywatnych plików.

Użycie: /usr/bin/python3 apps/models/tests/fixture.py [wariant] > build/models-view-<wariant>.json
"""
import copy
import json
import os
import sys

CONFIG_PATH = "/home/user/.config/tide/models.json"
REPORT_PATH = "/home/user/.config/tide/report.json"

QWEN8 = {"name": "qwen3:8b", "size": 5225388164, "family": "qwen3", "parameterSize": "8.2B", "quantization": "Q4_K_M"}
GPTOSS20 = {"name": "gpt-oss:20b", "size": 13780173839, "family": "gptoss", "parameterSize": "20.9B",
            "quantization": "MXFP4"}
BGE = {"name": "bge-m3", "size": 1157672605, "family": "bert", "parameterSize": "566.70M", "quantization": "F16"}
BIELIK = {"name": "hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M", "size": 6721570592, "family": "llama",
          "parameterSize": "11.2B", "quantization": "Q4_K_M"}
CODER30 = {"name": "qwen3-coder:30b", "size": 18556688736, "family": "qwen3moe", "parameterSize": "30.5B",
           "quantization": "Q4_K_M"}
QWEN32 = {"name": "qwen3:32b", "size": 20201253588, "family": "qwen3", "parameterSize": "32.8B", "quantization": "Q4_K_M"}
GPTOSS120 = {"name": "gpt-oss:120b", "size": 65290196070, "family": "gptoss", "parameterSize": "116.8B",
             "quantization": "MXFP4"}


def _endpoint(name, url, *, online=True, error="", version="0.40.11", models=(), loaded=(), kind="ollama",
              auth=False) -> dict:
    return {"name": name, "url": url, "kind": kind, "auth": auth, "online": online, "error": error,
            "version": (version if kind == "ollama" else None) if online else None,
            "models": [dict(m) for m in models] if online else [],
            "loaded": (None if kind == "openai" else [dict(m) for m in loaded]) if online else []}


def _loaded(model: dict, gpu: int, ctx: int = 8192) -> dict:
    size = int(model["size"] * 1.15)
    return {"name": model["name"], "size": size, "sizeVram": size * gpu // 100, "gpuPercent": gpu,
            "contextLength": ctx, "expiresAt": "2026-10-27T17:05:00+01:00"}


def _role(endpoint, model, ctx, endpoints, error="", pinned=False) -> dict:
    state = endpoints.get(endpoint) or {}
    online = bool(state.get("online"))
    installed = (model in [m["name"] for m in state.get("models") or []]) if online else None
    return {"endpoint": endpoint, "model": model, "ctx": ctx, "installed": installed,
            "available": bool(online and installed and not error), "error": error, "pinned": pinned}


def _report(summary="local", configured=None, pinned=False, warnings=()) -> dict:
    return {"summary": summary, "configured": configured or summary, "summaryModel": "sonnet", "raw": None,
            "pinned": pinned, "path": REPORT_PATH, "warnings": list(warnings)}


def _search(state="ready", enabled=True, items=412, pending=0, error="", updated="2026-10-28T10:02:41+01:00") -> dict:
    return {"enabled": enabled, "state": state, "items": items, "pending": pending,
            "updated": updated if state == "ready" else "", "error": error, "model": "bge-m3:latest",
            "dims": 1024 if items else 0}


def _executors(endpoints, roles, default, program="codex", extra=(), providers=None) -> dict:
    """Katalog wykonawców (PT-60) dokładnie taki, jaki da usługa: fixture składa konfigurację i woła prawdziwe
    `models.status.assemble` (nie odtwarza jego logiki). `extra` — dodatkowi wykonawcy (surowe obiekty
    z models.json), `providers` — inni dostawcy; z nimi katalog jest „zapisany”."""
    import sys
    daemon = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))), "daemon")
    if daemon not in sys.path:
        sys.path.insert(0, daemon)
    from tide_daemon.models import config as models_config
    from tide_daemon.models import executors as models_executors
    from tide_daemon.models import status as models_status
    data = {"default": default, "agentProgram": program,
            "endpoints": {n: dict({"url": e["url"], "kind": e["kind"]}, **({"tokenFile": f"/home/user/.config/tide/tokens/{n}"}
                                                              if e.get("auth") else {}))
                          for n, e in endpoints.items()},
            "roles": {r: f"{v['endpoint']}:{v['model']}" for r, v in roles.items() if r == "agent"}}
    if extra or providers:
        data["executors"] = [models_executors.executor_to_dict(e) for e in models_executors.DEFAULT_EXECUTORS] + \
            list(extra)
        data["providers"] = providers or {}
    state = models_status.assemble(models_config.from_dict(data), endpoints)
    return {k: state[k] for k in ("executors", "providers", "catalog")}


def _status(default, endpoints: dict, roles: dict, report=None, pulls=None, checked=True, warnings=(),
            exists=True, agent_program="codex", search=None) -> dict:
    if "embed" not in roles:
        roles = dict(roles, embed=_role(default, "bge-m3", None, endpoints))
    return {"checked": checked, "default": default,
            "config": {"path": CONFIG_PATH, "exists": exists, "error": "", "warnings": list(warnings)},
            "endpoints": endpoints, "roles": roles, "report": report if report is not None else _report(),
            "pulls": pulls or {}, "agentProgram": agent_program, "search": search or _search(),
            **_executors(endpoints, roles, default, agent_program)}


LOCAL_MODELS = (QWEN8, GPTOSS20, BGE, BIELIK, CODER30)


def local() -> dict:
    endpoints = {"local": _endpoint("local", "http://127.0.0.1:11434", models=LOCAL_MODELS,
                                    loaded=[_loaded(QWEN8, 100)])}
    roles = {"summary": _role("local", "qwen3:8b", 8192, endpoints),
             "describe": _role("local", "qwen3:8b", 8192, endpoints)}
    return _status("local", endpoints, roles, exists=False)


def mac_offline() -> dict:
    endpoints = {"local": _endpoint("local", "http://127.0.0.1:11434", models=LOCAL_MODELS,
                                    loaded=[_loaded(GPTOSS20, 83)]),
                 "Mac-Studio": _endpoint("Mac-Studio", "http://mac-studio:11434", online=False, auth=True,
                                         error="Model lokalny niedostępny: serwer nie odpowiada (mac-studio:11434)")}
    roles = {"summary": _role("local", "qwen3:8b", 8192, endpoints),
             "describe": _role("local", "qwen3:8b", 8192, endpoints),
             "agent": _role("Mac-Studio", "qwen3-coder:30b", None, endpoints, pinned=True)}
    return _status("local", endpoints, roles, report=_report("claude"), search=_search(enabled=False, state="off", items=0))


def mac() -> dict:
    endpoints = {"local": _endpoint("local", "http://127.0.0.1:11434", models=LOCAL_MODELS),
                 "Mac-Studio": _endpoint("Mac-Studio", "http://mac-studio:11434", version="0.41.2", auth=True,
                                         models=(GPTOSS120, QWEN32, CODER30, QWEN8, BGE),
                                         loaded=[_loaded(GPTOSS120, 100, 16384), _loaded(QWEN8, 100)]),
                 "lm-studio": _endpoint("lm-studio", "http://mac-studio:1234", kind="openai",
                                        models=[{"name": "qwen/qwen3-32b"}, {"name": "mistral-small-3.2"}])}
    roles = {"summary": _role("Mac-Studio", "gpt-oss:120b", 8192, endpoints),
             "describe": _role("Mac-Studio", "qwen3:8b", 8192, endpoints),
             "agent": _role("Mac-Studio", "qwen3-coder:30b", 32768, endpoints, pinned=True)}
    return _status("Mac-Studio", endpoints, roles, agent_program="claude",
                   search=_search("indexing", items=120, pending=292))


def pulling() -> dict:
    status = local()
    status["endpoints"]["local"]["models"] = [dict(QWEN8), dict(BGE)]
    endpoints = status["endpoints"]
    status["roles"]["describe"] = _role("local", "gpt-oss:20b", 8192, endpoints)
    status["endpoints"]["local"]["models"] = [dict(QWEN8)]
    status["roles"]["embed"] = _role("local", "bge-m3", None, endpoints)
    status["search"] = _search("error", items=0, error="Model lokalny: serwer odpowiedział 404: model \"bge-m3\" not "
                                                       "found, try pulling it first (127.0.0.1:11434)")
    status["pulls"] = {
        "local/gpt-oss:20b": {"endpoint": "local", "model": "gpt-oss:20b", "status": "pulling b112e727c6f1",
                              "completed": 5512069535, "total": 13780173839, "running": True, "error": ""},
        "local/llama-nie-ma:7b": {"endpoint": "local", "model": "llama-nie-ma:7b", "status": "error",
                                  "completed": 0, "total": 0, "running": False,
                                  "error": "Model lokalny: serwer odpowiedział 500: pull model manifest: file does "
                                           "not exist"}}
    return status


def errors() -> dict:
    endpoints = {"local": _endpoint("local", "http://127.0.0.1:11434", online=False,
                                    error="Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:11434)"),
                 "Mac-Studio": _endpoint("Mac-Studio", "http://mac-studio:11434", online=False, auth=True)}
    roles = {"summary": _role("local", "qwen3:8b", 8192, endpoints),
             "describe": _role("local", "qwen3:8b", 8192, endpoints)}
    warnings = ["serwer „lm”: nieznany rodzaj „llamacpp” (ollama albo openai) — pominięty"]
    return _status("local", endpoints, roles, report=_report("local", configured="claude", pinned=True),
                   warnings=warnings, search=_search("inactive", items=0))


def checking() -> dict:
    endpoints = {"local": _endpoint("local", "http://127.0.0.1:11434", online=False)}
    roles = {"summary": _role("local", "qwen3:8b", 8192, endpoints),
             "describe": _role("local", "qwen3:8b", 8192, endpoints)}
    return _status("local", endpoints, roles, checked=False, exists=False, search=_search("waiting", items=0))


CLOUD120 = {"name": "gpt-oss:120b-cloud", "size": 384, "family": "gptoss", "parameterSize": "116.8B",
            "quantization": "MXFP4", "cloud": True}


def busy() -> dict:
    status = local()
    status["endpoints"]["local"]["models"].append(dict(CLOUD120))
    status["search"] = _search("error", items=380, error="Model lokalny nie odpowiedział w 120 s (127.0.0.1:11434)")
    status["queue"] = {"local": {
        "running": {"endpoint": "local", "role": "summary", "model": "qwen3:8b", "since": "2026-10-27T16:58:00+01:00"},
        "waiting": [{"endpoint": "local", "role": "describe", "model": "qwen3:8b", "since": "2026-10-27T16:59:00+01:00"},
                    {"endpoint": "local", "role": "describe", "model": "qwen3:8b", "since": "2026-10-27T16:59:30+01:00"}],
        "other": [{"lane": "http://10.0.0.5:11434", "waiting": [],
                   "running": {"endpoint": "local", "role": "agent", "model": "qwen3-coder:30b",
                               "since": "2026-10-27T16:55:00+01:00"}}]}}
    return status


def catalog() -> dict:
    """Katalog z innym dostawcą (Groq, API OpenAI, token zapisany) i raportem w chmurze; agent na tym komputerze."""
    status = local()
    status["roles"]["agent"] = _role("local", "qwen3-coder:30b", None, status["endpoints"], pinned=True)
    status["report"] = _report("claude")
    extra = [{"name": "Groq mały", "program": "codex", "model": "llama-3.1-8b", "provider": "groq",
              "classes": ["lekki", "standard"]}]
    providers = {"groq": {"label": "Groq", "url": "https://api.groq.example", "api": "openai",
                          "tokenFile": "/home/user/.config/tide/tokens/providers/groq"}}
    status.update(_executors(status["endpoints"], status["roles"], "local", "codex", extra, providers))
    return status


VARIANTS = {"catalog": catalog, "local": local, "mac-offline": mac_offline, "mac": mac, "pulling": pulling, "errors": errors,
            "checking": checking, "busy": busy}


def sample(variant: str = "mac-offline") -> dict:
    return copy.deepcopy(VARIANTS[variant]())


if __name__ == "__main__":
    json.dump(sample(sys.argv[1] if len(sys.argv) > 1 else "mac-offline"), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
