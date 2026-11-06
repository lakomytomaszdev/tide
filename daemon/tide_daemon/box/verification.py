"""Verify .NET na kopii HEAD projektu z Tablicy, bez montowania repozytorium hosta.

Kopia nie zawiera .git ani zmian roboczych użytkownika. Restore ma jedno jawne źródło nuget.org.
Źródła i archiwum są usuwane przed clean; rewizję zapisuje zdarzenie box.verify.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import tarfile
import tempfile

from ..board.store import BoardStore
from ..gitcommand import git_argv
from . import profile as bp

PUBLIC_NUGET = "https://api.nuget.org/v3/index.json"
NUGET_CONFIG = ('<configuration><packageSources><clear />'
                f'<add key="nuget.org" value="{PUBLIC_NUGET}" />'
                '</packageSources></configuration>\n')


class VerificationError(ValueError):
    pass


def project_repo(slug):
    project = BoardStore(bp.board_dir()).load().projects.get(slug)
    return project.repo if project and project.repo else ""


def archive_head(repo, destination):
    try:
        revision = subprocess.run(git_argv("-C", repo, "rev-parse", "--verify", "HEAD^{commit}"),
                                  check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        subprocess.run(git_argv("-C", repo, "archive", "--format=tar", f"--output={destination}", revision),
                       check=True, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError("nie można przygotować kopii HEAD projektu do verify") from exc
    return revision


def validate_archive(path):
    if Path(path).stat().st_size > 256 * 1024 * 1024:
        raise VerificationError("kopia źródeł verify przekracza 256 MiB")
    # Brak dowiązań także wewnętrznych: ekstrakcja nigdy nie dostaje drogi do domu agenta.
    try:
        with tarfile.open(path, "r:") as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if (name.is_absolute() or ".." in name.parts or ".git" in name.parts
                        or not (member.isfile() or member.isdir())):
                    raise VerificationError(f"niebezpieczna pozycja kopii verify: {member.name!r}")
    except tarfile.TarError as exc:
        raise VerificationError("nieczytelne archiwum źródeł verify") from exc


@dataclass
class Source:
    incus: object
    box: str
    cwd: str
    config: str
    revision: str
    env: dict
    projects: tuple = ()

    def run(self, command):
        return self.incus.exec(self.box, command, self.env, user=1000, cwd=self.cwd)

    def restore(self):
        for project in self.projects or (None,):
            code = self.run(["dotnet", "restore", *([project] if project else []),
                             "--configfile", self.config, "--source", PUBLIC_NUGET])
            if code != 0:
                return code
        return 0


def checked(incus, box, command, env, user=None):
    if incus.exec(box, command, env, user=user) != 0:
        raise VerificationError("nie udało się przygotować lub usunąć kopii verify: " + command[0])


@contextmanager
def project_source(incus, slug, box, profile, env):
    if "dotnet" not in profile.get("stacks", []):
        yield None
        return
    repo = project_repo(slug)
    if not repo:
        # Profile narzędziowe bez projektu (np. próba samych SDK) zachowują zwykłe verify.
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="tide-verify-") as folder:
        archive, config = Path(folder) / "source.tar", Path(folder) / "nuget.config"
        revision = archive_head(repo, archive)
        validate_archive(archive)
        config.write_text(NUGET_CONFIG, encoding="utf-8")
        remote = "/tmp/tide-verify-" + secrets.token_hex(16)
        remote_archive = remote + ".tar"
        agent_env = {**env, "HOME": "/home/agent", "DOTNET_CLI_HOME": "/home/agent",
                     "DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                     "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1"}
        try:
            checked(incus, box, ["mkdir", "-p", remote + "/src"], agent_env, user=1000)
            incus.file_push(str(archive), box, remote_archive)
            incus.file_push(str(config), box, remote + "/nuget.config")
            checked(incus, box, ["chmod", "0644", remote_archive, remote + "/nuget.config"], env)
            checked(incus, box, ["tar", "-xf", remote_archive, "-C", remote + "/src",
                                 "--no-same-owner", "--no-same-permissions"], agent_env, user=1000)
            yield Source(incus, box, remote + "/src", remote + "/nuget.config", revision, agent_env,
                         tuple(profile.get("dotnet", {}).get("restore-projects", [])))
        finally:
            # Nie tworzymy migawki zawierającej źródła, nawet jeśli build lub sprzątanie zawiedzie.
            try:
                checked(incus, box, ["rm", "-rf", "--", remote], agent_env, user=1000)
            finally:
                checked(incus, box, ["rm", "-f", "--", remote_archive], env)
