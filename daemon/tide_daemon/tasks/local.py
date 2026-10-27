"""Agent na modelu lokalnym: Codex albo Claude Code na serwerze z roli `agent` w models.json.

Narzędzie zostaje to samo (wykrywanie sesji, Meta+A, powiadomienia działają bez zmian), zmienia się
tylko model: argv i środowisko programu agenta kierują go na serwer modeli. Przypadki:

- Codex, Ollama na tym komputerze (127.0.0.1/localhost/::1) bez tokenu — wbudowany dostawca:
  `codex --oss --local-provider ollama --model=<model>` i `CODEX_OSS_BASE_URL=<adres>/v1` (PT-13),
- Codex, każdy inny serwer (Ollama na Macu, serwer zgodny z OpenAI) — własny dostawca przez `-c`:
  `model_provider=tide-<punkt>`, `model_providers.tide-<punkt>.{name, base_url=<adres>/v1,
  wire_api="responses"}` i przy tokenie `env_key="TIDE_MODEL_TOKEN"` oraz
  `shell_environment_policy.exclude=["TIDE_MODEL_TOKEN"]` i `features.shell_snapshot=false` (token ma
  Codex, a nie polecenia, które uruchamia; migawka powłoki przenosiła go do poleceń mimo wykluczenia). Codex 0.114 obsługuje już tylko
  Responses API (`wire_api = "chat"` odrzuca), więc serwer musi mieć `/v1/responses` (Ollama ma),
- Claude Code (każdy serwer) — `--model=<model>` i środowisko: `ANTHROPIC_BASE_URL=<adres>`,
  `ANTHROPIC_AUTH_TOKEN=ollama` bez tokenu; z tokenem `--settings
  {"apiKeyHelper": "cat -- <tokenFile>"}` i bez `ANTHROPIC_AUTH_TOKEN` (token nie jest zmienną, więc
  polecenia Bash agenta go nie widzą; `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` odpada — na tym komputerze
  uruchamia Bash przez bwrap z przestrzenią użytkownika, której AppArmor zabrania, i Bash nie działa), pusty `ANTHROPIC_API_KEY`,
  `ANTHROPIC_MODEL` i `ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS,FABLE}_MODEL` = model (żadne zadanie
  w tle nie idzie do chmury), `CLAUDE_CODE_MAX_CONTEXT_TOKENS` = kontekst, gdy go znamy i ma co najmniej
  32k (inaczej Claude Code zakłada 200k dla nieznanego modelu i nie kompaktuje rozmowy w porę), wtedy
  też `CLAUDE_CODE_MAX_OUTPUT_TOKENS` = ćwierć kontekstu (rezerwa 32k na odpowiedź nieznanego modelu
  nie zmieści się w oknie). Okna mniejszego niż 32k nie podajemy: sprawdzone na 16k (ten komputer) —
  sam prompt Claude Code to ok. 11k, więc z prawdziwym oknem Claude Code kompaktuje bez końca
  („Autocompact is thrashing”) albo odmawia („Prompt is too long”); bez niego krótkie zadanie działa
  (PT-13), a dłuższą rozmowę serwer przytnie. Do tego
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` (bez telemetrii — tryb lokalny ma nie wysyłać danych).
  Serwer musi mieć `/v1/messages` (Ollama ma).

Model idzie jednym argumentem (`--model=<model>`), więc nazwa z „-” na początku nie stanie się opcją.
Token nigdy nie trafia do argv, tytułu karty, dziennika, pliku zadania ani do poleceń, które agent
uruchamia. Plik zadania niesie tylko ścieżki: `secretEnv` (Codex: zmienna → plik; launch.py czyta token
w chwili startu i wstawia go do środowiska Codexa) i `tokenFiles` (launch.py sprawdza je przed startem,
żeby zły plik zatrzymał start z komunikatem, a nie pierwsze zapytanie agenta). Kontekst (`ctx`): z roli `agent` w models.json, inaczej z serwera, gdy model
jest załadowany (`/api/ps`), inaczej nieznany — wtedy bez ustawienia.
"""
from __future__ import annotations

import ipaddress
import json
import re
import shlex
import urllib.parse
from dataclasses import dataclass, field

LOCAL_HOSTS = {"localhost"}
TOKEN_ENV = "TIDE_MODEL_TOKEN"          # zmienna z tokenem dla własnego dostawcy Codexa (env_key)
CLAUDE_MIN_CTX = 32768                  # mniejszego okna Claude Code nie dostaje (patrz niżej)
DEFAULT_OUTPUT_TOKENS = 32000          # domyślny limit odpowiedzi Claude Code dla nieznanego modelu
PLACEHOLDER_TOKEN = "ollama"             # Claude Code bez tokenu: dowolna niepusta wartość (PT-13)
CLAUDE_MODEL_VARS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                     "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL")


@dataclass(frozen=True)
class LocalAgent:
    """Rola `agent` po sprawdzeniu: gdzie i na jakim modelu ma pracować agent."""
    endpoint: str            # nazwa punktu z models.json („local”, „mac”)
    url: str                 # adres serwera bez „/” i bez „/v1”
    kind: str                # "ollama" albo "openai"
    model: str
    ctx: int | None = None   # kontekst serwera dla tego modelu; None — nieznany
    token_file: str = ""     # ścieżka pliku tokenu; pusta — bez tokenu

    @property
    def is_this_computer(self) -> bool:
        return is_loopback(self.url)

    @property
    def server(self) -> str:
        """„ten komputer” albo nazwa hosta („mac-studio”) — do okna i opisów."""
        return "ten komputer" if self.is_this_computer else (urllib.parse.urlsplit(self.url).hostname or self.endpoint)


@dataclass
class Command:
    """Polecenie agenta: argv, jawne zmienne środowiska, zmienne z sekretem (nazwa → plik tokenu),
    zmienne do usunięcia ze środowiska agenta i pliki tokenów do sprawdzenia przed startem."""
    argv: list
    env: dict = field(default_factory=dict)
    secret_env: dict = field(default_factory=dict)
    unset_env: list = field(default_factory=list)
    token_files: list = field(default_factory=list)

    def task_fields(self) -> dict:
        """Pola pliku zadania dla launch.py (tylko niepuste) — bez treści tokenów."""
        fields = {"env": self.env, "secretEnv": self.secret_env, "unsetEnv": self.unset_env,
                  "tokenFiles": self.token_files}
        return {key: value for key, value in fields.items() if value}


def is_loopback(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return False
    if host.lower() in LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def provider_id(endpoint: str) -> str:
    """Id własnego dostawcy Codexa z nazwy punktu; bez kropek (to separator ścieżki w `-c`)."""
    return "tide-" + re.sub(r"[^A-Za-z0-9_-]", "_", endpoint)


def _toml(value: str) -> str:
    """Napis TOML (podstawowy) — escapowanie JSON jest z nim zgodne dla napisów."""
    return json.dumps(value, ensure_ascii=False)


def codex_options(agent: LocalAgent) -> Command:
    """Opcje Codexa (przed --add-dir, --image i --) i środowisko dla modelu lokalnego."""
    options, env, secret, files = [], {}, {}, []
    if agent.kind == "ollama" and agent.is_this_computer and not agent.token_file:
        options += ["--oss", "--local-provider", "ollama"]
        env["CODEX_OSS_BASE_URL"] = agent.url + "/v1"
    else:
        pid = provider_id(agent.endpoint)
        prefix = f"model_providers.{pid}"
        options += ["-c", f"model_provider={_toml(pid)}",
                    "-c", f"{prefix}.name={_toml('Tide: ' + agent.endpoint)}",
                    "-c", f"{prefix}.base_url={_toml(agent.url + '/v1')}",
                    "-c", f'{prefix}.wire_api="responses"']
        if agent.token_file:
            # Token dla Codexa, ale nie dla poleceń, które Codex uruchamia (env, skrypty z repozytorium):
            # wykluczenie z polityki środowiska i bez migawki powłoki — ta zapisuje środowisko powłoki
            # z tokenem i odtwarza je w każdym poleceniu (sprawdzone na 0.114: `env` w sesji pokazywał token).
            options += ["-c", f"{prefix}.env_key={_toml(TOKEN_ENV)}",
                        "-c", f"shell_environment_policy.exclude=[{_toml(TOKEN_ENV)}]",
                        "-c", "features.shell_snapshot=false"]
            secret[TOKEN_ENV] = agent.token_file
            files.append(agent.token_file)
    options.append(f"--model={agent.model}")  # jeden argument: model z „-” nie stanie się opcją
    if agent.ctx:
        options += ["-c", f"model_context_window={int(agent.ctx)}"]
    return Command(options, env, secret, token_files=files)


def claude_output_tokens(ctx: int) -> int:
    """Limit odpowiedzi przy znanym kontekście: ćwierć okna (1024…32000). Bez tego Claude Code rezerwuje
    na odpowiedź nieznanego modelu 32 000 tokenów i przy oknie 16k każde zapytanie kończy się
    „Prompt is too long” (sprawdzone na qwen3-coder:30b, Ollama 16k)."""
    return max(1024, min(DEFAULT_OUTPUT_TOKENS, int(ctx) // 4))


def claude_options(agent: LocalAgent) -> Command:
    """Opcje Claude Code (`--model`) i środowisko dla modelu lokalnego."""
    env = {"ANTHROPIC_BASE_URL": agent.url, "ANTHROPIC_API_KEY": "",
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    env.update({name: agent.model for name in CLAUDE_MODEL_VARS})
    if agent.ctx and agent.ctx >= CLAUDE_MIN_CTX:
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(int(agent.ctx))
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(claude_output_tokens(agent.ctx))
    argv = [f"--model={agent.model}"]
    if not agent.token_file:
        env["ANTHROPIC_AUTH_TOKEN"] = PLACEHOLDER_TOKEN
        return Command(argv, env)
    # Token nie jest zmienną środowiska — dziedziczyłyby ją polecenia Bash agenta. Claude Code bierze go
    # z apiKeyHelper (czyta plik przy starcie i odświeżeniu), w argv jest tylko ścieżka. ANTHROPIC_AUTH_TOKEN
    # użytkownika ma pierwszeństwo przed apiKeyHelper, więc znika ze środowiska agenta.
    helper = {"apiKeyHelper": f"cat -- {shlex.quote(agent.token_file)}"}
    argv += ["--settings", json.dumps(helper, ensure_ascii=False)]
    return Command(argv, env, unset_env=["ANTHROPIC_AUTH_TOKEN"], token_files=[agent.token_file])


def options(tool: str, agent: LocalAgent) -> Command:
    return codex_options(agent) if tool == "codex" else claude_options(agent)
