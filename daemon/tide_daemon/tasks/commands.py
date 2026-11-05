"""Polecenia przygotowawcze przez runner wybrany dla kroku; bez ponownego wyboru host/pudełko."""
import os
import subprocess

from .runner import HostRunner


def in_box(runner) -> bool:
    from ..box.runner import BoxRunner
    return isinstance(runner, BoxRunner)


def run(runner, argv, *, background=None, **kwargs):
    if not in_box(runner):
        call = subprocess.run if runner is None or isinstance(runner, HostRunner) else runner
        return call(argv, **kwargs)
    from ..box.runner import StepDir, env_file_text, worktrees_root
    if background is None:
        raise ValueError("polecenie w pudełku wymaga kontekstu kroku")
    # Proxy zawiera identyfikator kroku: wartości środowiska nigdy nie trafiają do argv ani środowiska incusa.
    env = dict(background.env)
    env.update(kwargs.pop("env", {}))
    path = os.path.join(background.step_dir, "in", "prepare-env")
    fs = StepDir(worktrees_root(os.path.realpath(background.repo)), background.step_dir)
    fs.write_secret(path, env_file_text(env))
    script = ('file=$1; shift; [ -f "$file" ] && [ ! -L "$file" ] || exit 125; '
              'while IFS= read -r line; do export "$line" || exit 125; done < "$file"; exec "$@"')
    cwd = kwargs.pop("cwd")
    args = [*runner.exec_prefix(cwd=cwd), "/usr/bin/env", "-i", "/bin/sh", "-c", script, "prepare", path, *argv]
    try:
        return runner.incus._run(args, env=runner._client_env(), **kwargs)
    finally:
        fs.unlink(path)


def output(runner, background, script: str) -> str:
    result = run(runner, ["/bin/sh", "-c", script], background=background,
                 cwd=background.cwd, env={}, capture_output=True, text=True, timeout=30,
                 stdin=subprocess.DEVNULL)
    return result.stdout.strip() if result.returncode == 0 else ""
