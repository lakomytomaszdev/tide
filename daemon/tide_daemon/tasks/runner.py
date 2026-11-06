"""Runner (PT-180, K-38) — jedyne miejsce, przez które silnik kart uruchamia i zatrzymuje kroki w tle.

`Runner` to umowa: `start(step)`, `kill(step)`, `env(step)`. Dzisiejszy sposób — `agentcmd.background`,
`systemd-run --user --scope`, bwrap i zatrzymanie jednostki — to `HostRunner`; zachowuje się bit w bit jak przed
wprowadzeniem interfejsu (złoty plik `tests/fixtures/runner/host_argv.json`). Silnik dostaje runner w konstruktorze
(domyślnie `HostRunner`); nie wybiera go po niczym.
"""
from __future__ import annotations

from typing import Callable, NamedTuple, Protocol

from . import agentcmd, stepstop


class Started(NamedTuple):
    """Wynik `start`: złożony krok (`unit`, `git_dir`, pliki) i uchwyt procesu, gdy runner go ma (Popen; None —
    brak, silnik zna wtedy krok tylko po jednostce, jak po restarcie usługi)."""
    background: agentcmd.Background
    proc: object


class Runner(Protocol):
    def start(self, step: agentcmd.Step, *, before_start: Callable[[agentcmd.Background], None] | None = None
              ) -> Started:
        """Składa krok, przygotowuje jego katalogi i pliki, woła `before_start(background)` (np. kopiowanie
        obrazów) i uruchamia program. Błędy: `agentcmd.StepError`, `agentcmd.UnknownTool`, `OSError`."""

    def kill(self, step: dict, *, proc: object = None, grace: float = 10.0) -> bool:
        """Zatrzymuje krok opisany wpisem dziennika (`unit`) i/lub uchwytem `proc`. True, gdy nic nie żyje."""

    def env(self, step: agentcmd.Step) -> dict:
        """Środowisko programu kroku — dokładnie `Background.env` (z zamianami punktu lokalnego); sekrety
        z `secret_env` dopisuje dopiero `start`."""


class HostRunner:
    """Krok jako proces na tym komputerze: jednostka systemd (scope) + piaskownica Claude Code / Codexa."""

    def __init__(self, systemd_run: str = ""):
        self.systemd_run = systemd_run     # inny plik wykonywalny niż systemd-run (atrapa w testach usługi)

    def start(self, step: agentcmd.Step, *, before_start: Callable[[agentcmd.Background], None] | None = None
              ) -> Started:
        from ..box import trialgate
        trialgate.require_step(step)
        bg = agentcmd.background(step)
        bg.prepare()
        if before_start is not None:
            before_start(bg)
        trialgate.require_step(step)
        return Started(bg, bg.start(systemd_run=self.systemd_run))

    def kill(self, step: dict, *, proc: object = None, grace: float = 10.0) -> bool:
        return stepstop.terminate(proc, step.get("unit") or "", grace)

    def env(self, step: agentcmd.Step) -> dict:
        return agentcmd.background(step).env
