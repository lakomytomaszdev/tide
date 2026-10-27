"""Czy sesja pracuje na modelu lokalnym — z tego, co zapisuje sam program agenta.

- Codex: `session_meta.model_provider` (`ollama`, `lmstudio`, `oss` albo własny dostawca `tide-<punkt>`
  z okna „Nowe zadanie”) — chmurą są tylko znani dostawcy z CLOUD_PROVIDERS; model z `turn_context.model`,
- Claude Code: `message.model` odpowiedzi modelu (np. `qwen3-coder:30b`) — lokalny, gdy to nie Claude
  (`claude-…`, `us.anthropic.claude-…`, aliasy) ani `<synthetic>` (wiadomość od samego Claude Code).

Wynik trafia do listy /Agents jako `model` i `localModel`, a widżet agentów dopisuje „lokalny · model”.
"""
CLOUD_PROVIDERS = {"openai", "azure", "openrouter", "gemini", "mistral", "anthropic", "groq", "deepseek", "xai"}
CLAUDE_ALIASES = {"opus", "sonnet", "haiku", "fable", "opusplan", "default", "best"}


def is_local_codex_provider(provider: str) -> bool:
    return isinstance(provider, str) and bool(provider) and provider.lower() not in CLOUD_PROVIDERS


def is_local_claude_model(model: str) -> bool:
    name = model.lower() if isinstance(model, str) else ""
    return bool(name) and not name.startswith("<") and "claude" not in name and name not in CLAUDE_ALIASES
