#!/usr/bin/python3
"""Czerwona lista w repozytorium projektu bez zapisywania tam plików Tide."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "daemon"))
from tide_daemon.box import cli
from tide_daemon.box.builder import BuildError
from tide_daemon.box.command import project_repo


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or not args[0] or args[0].startswith("-"):
        print("Podaj BOX=<slug>", file=sys.stderr)
        return 2
    slug, *check_args = args
    try:
        repo = project_repo(slug)
    except BuildError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # Osobny argument bash -c, bez interpolacji slug, ścieżki repo ani CHECK_ARGS w kodzie.
    # CLI tworzy własny testowy worktree z HEAD projektu i zachowuje wszystkie granice BoxRunnera.
    script = (ROOT / "scripts/box/check.sh").read_text(encoding="utf-8")
    return cli.main(["run", slug, "--worktree", repo, "--", "bash", "-c", script,
                     "tide-box-check", *check_args])


if __name__ == "__main__":
    sys.exit(main())
