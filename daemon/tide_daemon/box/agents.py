"""Wersje agentów w obrazie bazowym Tide (#16) — przypięte, nigdy `latest`.

Claude Code: instalator natywny z `claude.ai/install.sh` z dokładną wersją (wersja hosta z analizy 4.7, sprawdzona
w P-6). Codex: `@openai/codex` z npm (sprawdzony w P-3/P-6; konfiguracja dostawcy K3 zależy od wewnętrznych ścieżek
Codexa, więc podbicie = powtórka P-3). Zmiana wersji zmienia `AGENTS_VERSION`, a ten wchodzi do hasha przepisu
pudełek projektów (`box.agents-version` w ustawieniach tylko go nadpisuje) — pudełka stają się nieaktualne.
"""
import re

CLAUDE_VERSION = "2.1.317"
CODEX_VERSION = "0.166.0"
AGENTS_VERSION = f"claude-code {CLAUDE_VERSION}; codex {CODEX_VERSION}"

_PINNED = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

# `tide box login` (K1): polecenie logowania programu w pudełku, jako użytkownik `agent`.
LOGIN_ARGV = {"claude": ["claude", "login"], "codex": ["codex", "login"]}


def pinned(version: str) -> bool:
    """Dokładna wersja `x.y.z` (bez `latest`, `stable`, zakresów i znaczników)."""
    return bool(_PINNED.match(str(version)))
