"""Przygotowanie zależności po montowaniach BoxRunnera i nadaniu tożsamości proxy kroku."""
import os

from ..box.runner import StepDir, worktrees_root
from . import commands, dotnet, node


def prepare(runner, bg):
    # Runner pochodzi z Placement; nie wybieramy środowiska drugi raz.
    if not commands.in_box(runner):
        return
    fs = StepDir(worktrees_root(os.path.realpath(bg.repo)), bg.step_dir)
    home = runner.base_env()["HOME"]
    notes = []
    if dotnet.is_dotnet(bg.cwd):
        bg.env.update(dotnet.step_env(home, dotnet.packages_dir(home, runner=runner)))
        ok, tail = dotnet.restore(bg.cwd, runner=runner, background=bg)
        notes.append(("dotnet restore", ok, tail))
    if node.is_node(bg.cwd):
        prefix = node.node_home(home, runner=runner, background=bg)
        if prefix:
            bg.env["PATH"] = os.path.join(prefix, "bin") + os.pathsep + bg.env["PATH"]
        chrome = node.chrome_bin(home, runner=runner, background=bg)
        if chrome:
            bg.env["CHROME_BIN"] = chrome
        ok, tail = node.install(bg.cwd, home, runner=runner, background=bg)
        notes.append(("npm ci/install", ok, tail))
    if notes:
        text = "\n".join(f"{name}: {'OK' if ok else 'BŁĄD'}\n{tail}" for name, ok, tail in notes)
        fs.write(os.path.join(bg.step_dir, "out", "prepare.log"), text + "\n")
        fs.write(bg.prompt_file, bg.stdin + "\nPrzygotowanie zależności przez Tide w pudełku:\n" + text + "\n")
