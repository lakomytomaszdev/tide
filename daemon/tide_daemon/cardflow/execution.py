"""Wykonawca kroku karty: rozwiązanie katalogu na program, model i punkt przed zapisem zlecenia."""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..models import executors
from ..tasks.local import LocalAgent


class ExecutionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Execution:
    name: str
    tool: str
    binary: str
    model: str
    provider: str
    effort: str = ""
    route: LocalAgent | None = None
    local: bool = False


def resolve(name: str, catalog: executors.Catalog, *, config=None, probe=None, which=None) -> Execution:
    """Rozwiązuje wykonawcę bez domyślnego przejścia na inny program albo dostawcę."""
    selected = catalog.get(name)
    if selected is None:
        raise ExecutionError("unknown-executor", f"nie ma wykonawcy „{name}” w katalogu wykonawców")

    if selected.provider == executors.LOCAL:
        if config is None:
            raise ExecutionError("local-unavailable", f"„{name}”: nie ma ustawień modeli lokalnych")
        ctx = executors.context_from(config)
        info = ctx.resolved(selected)
        endpoint = config.endpoints.get(info["endpoint"])
        if endpoint is None:
            raise ExecutionError("local-unavailable", f"„{name}”: nieznany serwer „{info['endpoint']}”")
        if probe is None:
            raise ExecutionError("local-unavailable", f"„{name}”: nie można sprawdzić serwera lokalnego")
        try:
            state = probe(endpoint)
        except Exception as exc:
            raise ExecutionError("local-unavailable", f"„{name}”: nie można sprawdzić serwera lokalnego ({type(exc).__name__})") from None
        if not isinstance(state, dict) or not state.get("online"):
            reason = state.get("error") if isinstance(state, dict) else ""
            raise ExecutionError("local-unavailable", reason or f"„{name}”: serwer „{endpoint.name}” nie odpowiada")
        ctx.status = {"checked": True, "endpoints": {endpoint.name: state}}
        problem = ctx.unavailable(selected)
        if problem:
            raise ExecutionError("local-unavailable", problem)
        route = LocalAgent(endpoint.name, endpoint.url, endpoint.kind, info["model"],
                           (config.roles.get("agent").ctx if config.roles.get("agent") and
                            info["model"] == config.roles["agent"].model else None), endpoint.token_file)
        local = True
    else:
        info = {"program": selected.program, "model": selected.model}
        local = False
        route = None
        if selected.provider not in ("anthropic", "openai"):
            provider = catalog.providers.get(selected.provider)
            if provider is None:
                raise ExecutionError("unknown-provider", f"„{name}”: nieznany dostawca „{selected.provider}”")
            route = LocalAgent(provider.name, provider.url.removesuffix("/v1"), provider.api, selected.model,
                               token_file=os.path.expanduser(provider.token_file))

    tool = info["program"]
    model = info["model"]
    if tool not in executors.PROGRAMS or not model:
        raise ExecutionError("unsupported-executor", f"„{name}”: wykonawca nie ma programu albo modelu")
    binary = which(tool) if which else ""
    if not binary:
        raise ExecutionError("no-program", f"nie znaleziono programu {tool}")
    return Execution(name, tool, binary, model, selected.provider,
                     selected.effort if tool == "codex" else "", route, local)
