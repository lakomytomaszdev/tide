"""Jedno miejsce, które składa polecenie i środowisko programu agenta (Claude Code, Codex).

Odmiany:
- `interactive()` — sesja w Konsoli (okno „Nowe zadanie”, zlecenie z Tablicy): argv z poleceniem na
  starcie i opcjami modelu lokalnego, środowisko i tokeny dla launch.py (tasks/local.py). Zachowanie
  jak przed wydzieleniem (PT-76), tylko w jednym miejscu,
- `background()` — krok w tle (`claude -p`, `codex exec`) z piaskownicą i środowiskiem bez kluczy
  (dalej w tym pliku),
- zatrzymanie kroku bez utraty zmian: tasks/stepstop.py.
"""
from __future__ import annotations

import os

from . import local as local_mod


class UnknownTool(ValueError):
    """Program inny niż `claude` i `codex`."""


def agent_argv(tool: str, binary: str, prompt: str, images: list[str] = (), add_dirs: list[str] = (),
               model_options: list[str] = ()) -> list[str]:
    """argv sesji interaktywnej z poleceniem na start; prompt to jeden element listy.

    Codex: `codex [--add-dir D] --image A --image B -- <prompt>` (zrzuty dołączone do pierwszej wiadomości).
    Claude Code nie ma opcji na obrazy: ścieżki trafiają na koniec polecenia, a katalogi zrzutów
    do `--add-dir`, żeby agent mógł je przeczytać narzędziem Read bez pytania o zgodę.
    add_dirs (oba narzędzia: `--add-dir`) — katalogi poza katalogiem roboczym, w których agent
    pisze (dokumenty AI karty w repozytorium tablicy).
    model_options — opcje modelu lokalnego zaraz po programie (tasks/local.py), np. `--model qwen3-coder:30b`.
    `--` kończy opcje — polecenie zaczynające się od „-” nie zostanie wzięte za opcję."""
    images = list(images)
    extra = list(model_options)
    for folder in add_dirs:
        extra += ["--add-dir", folder]
    if tool == "codex":
        argv = [binary] + extra
        for path in images:
            argv += ["--image", path]
        return argv + ["--", prompt]
    if tool == "claude":
        argv = [binary] + extra
        for folder in sorted({os.path.dirname(p) for p in images} - set(add_dirs)):
            argv += ["--add-dir", folder]
        text = prompt
        if images:
            text += "\n\nZałączone zrzuty ekranu (przeczytaj je narzędziem Read):\n" + "\n".join(images)
        return argv + ["--", text]
    raise UnknownTool(tool)


def interactive(tool: str, binary: str, prompt: str, images=(), add_dirs=(),
                agent: "local_mod.LocalAgent | None" = None) -> "local_mod.Command":
    """Polecenie sesji w Konsoli. Bez `agent` (chmura) samo argv, z `agent` (model lokalny, inny punkt) także
    środowisko, zmienne z tokenem (nazwa → plik) i pliki tokenów do sprawdzenia przed startem."""
    if tool not in ("claude", "codex"):
        raise UnknownTool(tool)
    model = local_mod.options(tool, agent) if agent is not None else local_mod.Command([])
    argv = agent_argv(tool, binary, prompt, images, add_dirs, model.argv)
    return local_mod.Command(argv, model.env, model.secret_env, model.unset_env, model.token_files)
