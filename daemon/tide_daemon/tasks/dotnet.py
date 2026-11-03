"""Projekty .NET w krokach w tle (K-15): krok nie ma sieci ani dostępu do katalogu domowego, więc `dotnet restore`
robi usługa przed startem kroku, a krok dostaje do odczytu tylko pamięć pakietów NuGet i środowisko, w którym
`dotnet build/test --no-restore` działa bez serwerów kompilacji i bez pierwszego uruchomienia w ~/.dotnet."""
import glob
import os
import subprocess

from ..box import templates

# Domeny rejestru NuGet z szablonu stosu (box/stacks/dotnet.yaml, pozycje `registry: true`, #30).
REGISTRY_DOMAINS = templates.registry_domains("dotnet")
RESTORE_TIMEOUT = 900
MARKERS = ("*.sln", "*.slnx", "*.csproj", "*/*.csproj", "src/*/*.csproj")


def is_dotnet(worktree: str) -> bool:
    return any(glob.glob(os.path.join(worktree, pattern)) for pattern in MARKERS)


def packages_dir(home: str) -> str:
    """Pamięć pakietów NuGet (NUGET_PACKAGES albo ~/.nuget/packages) — tylko do odczytu w kroku."""
    path = os.environ.get("NUGET_PACKAGES") or os.path.join(home, ".nuget", "packages")
    return os.path.realpath(path)


def restore(worktree: str, *, runner=subprocess.run, timeout: float = RESTORE_TIMEOUT) -> tuple[bool, str]:
    """`dotnet restore` w worktree kroku, poza piaskownicą (z siecią usługi). Zwraca (ok, ostatnie linie)."""
    solutions = sorted(glob.glob(os.path.join(worktree, "*.sln")) + glob.glob(os.path.join(worktree, "*.slnx")))
    argv = ["dotnet", "restore"] + ([solutions[0]] if len(solutions) == 1 else [])
    env = dict(os.environ, DOTNET_NOLOGO="1", DOTNET_CLI_TELEMETRY_OPTOUT="1",
               DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1")
    try:
        run = runner(argv, cwd=worktree, env=env, capture_output=True, text=True, timeout=timeout,
                     stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"[:600]
    tail = "\n".join(((run.stdout or "") + (run.stderr or "")).strip().splitlines()[-8:])
    return run.returncode == 0, tail[-1200:]


def step_env(tmp_dir: str, packages: str) -> dict:
    """Środowisko kroku: pakiety z pamięci, katalog CLI w katalogu tymczasowym kroku, bez serwerów kompilacji
    (ich gniazda w piaskownicy kończyły się SocketException 13)."""
    return {"NUGET_PACKAGES": packages, "DOTNET_CLI_HOME": tmp_dir, "DOTNET_NOLOGO": "1",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
            "DOTNET_CLI_USE_MSBUILD_SERVER": "0", "MSBUILDDISABLENODEREUSE": "1",
            "UseSharedCompilation": "false", "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false",
            "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "1",
            # `.gitmodules` jest w piaskownicy atrapą — zapytania SourceLink o repozytorium kończyły build błędem.
            "EnableSourceControlManagerQueries": "false", "EnableSourceLink": "false"}


def prompt_note(ok: bool, tail: str) -> str:
    if ok:
        return ("\nProjekt .NET: Tide przywrócił pakiety NuGet przed krokiem (dotnet restore poza piaskownicą). "
                "Sieci i lokalnych gniazd w kroku nie ma — uruchamiaj build i testy tak: "
                "`dotnet test <projekt> --no-restore -m:1 -nodeReuse:false --disable-build-servers` "
                "(tak samo `dotnet build`). Odmowy połączeń z api.nuget.org w wyniku są niegroźne.\n")
    return ("\nProjekt .NET: `dotnet restore` przed krokiem nie powiódł się, więc build i testy mogą nie ruszyć. "
            f"Ostatnie linie:\n{tail}\nNie próbuj restore w kroku (brak sieci); opisz to w notce.\n")
