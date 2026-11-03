"""Projekty Node w krokach w tle (03.11, K-31): krok nie widzi katalogu domowego, więc `node` z nvm nie był na PATH,
a `npm ci` potrzebuje sieci. Usługa przygotowuje zależności przed krokiem (poza piaskownicą), a krok dostaje
instalację Node tylko do odczytu i jej `bin` na początku PATH."""
import glob
import os
import re
import shutil
import subprocess

from ..box import templates

INSTALL_TIMEOUT = 1200


def is_node(worktree: str) -> bool:
    return os.path.isfile(os.path.join(worktree, "package.json"))


def node_home(home: str) -> str:
    """Katalog instalacji Node (z `bin/node`): wersja domyślna nvm, najnowsza z nvm albo `node` z PATH usługi."""
    versions = os.path.join(home, ".nvm", "versions", "node")
    found = sorted((d for d in glob.glob(os.path.join(versions, "v*")) if os.path.isfile(os.path.join(d, "bin", "node"))),
                   key=_version_key)
    wanted = ""
    try:
        with open(os.path.join(home, ".nvm", "alias", "default"), encoding="utf-8") as fh:
            wanted = fh.read().strip().lstrip("v")
    except OSError:
        pass
    if wanted:
        matching = [d for d in found if os.path.basename(d).lstrip("v").split(".")[:len(wanted.split("."))]
                    == wanted.split(".")]
        if matching:
            return os.path.realpath(matching[-1])
    if found:
        return os.path.realpath(found[-1])
    node = shutil.which("node")
    return os.path.realpath(os.path.dirname(os.path.dirname(os.path.realpath(node)))) if node else ""


def _version_key(path: str):
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.]", os.path.basename(path).lstrip("v")))


def install(worktree: str, home: str, *, runner=subprocess.run, timeout: float = INSTALL_TIMEOUT) -> tuple[bool, str]:
    """`npm ci` (z package-lock.json) albo `npm install` w worktree kroku, poza piaskownicą (z siecią usługi)."""
    prefix = node_home(home)
    if not prefix:
        return False, "nie znaleziono instalacji Node (nvm ani node na PATH)"
    lock = os.path.join(worktree, "package-lock.json")
    marker = os.path.join(worktree, "node_modules", ".package-lock.json")
    try:
        if os.path.isfile(lock) and os.path.getmtime(marker) >= os.path.getmtime(lock):
            return True, "zależności aktualne"
    except OSError:
        pass
    argv = [os.path.join(prefix, "bin", "npm"), "ci" if os.path.isfile(os.path.join(worktree, "package-lock.json"))
            else "install", "--no-audit", "--no-fund"]
    env = dict(os.environ, PATH=os.path.join(prefix, "bin") + os.pathsep + os.environ.get("PATH", ""))
    try:
        run = runner(argv, cwd=worktree, env=env, capture_output=True, text=True, timeout=timeout,
                     stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"[:600]
    tail = "\n".join(((run.stdout or "") + (run.stderr or "")).strip().splitlines()[-8:])
    return run.returncode == 0, tail[-1200:]


def prompt_note(ok: bool, tail: str, network: bool = False) -> str:
    if ok and network:
        return ("\nProjekt Node: Tide zainstalował zależności przed krokiem (`npm ci`); `node` i `npm` są na PATH. "
                "Krok ma sieć do rejestru npm, więc `npm install <paczka>` działa tutaj. Serwery, e2e i Docker "
                "zgłaszaj wynikiem `outside`.\n")
    if ok:
        return ("\nProjekt Node: Tide zainstalował zależności przed krokiem (`npm ci` poza piaskownicą); `node` i `npm` "
                "są na PATH. Sieci w kroku nie ma — nowej paczki nie doinstalujesz sam: poproś o uruchomienie polecenia "
                "poza piaskownicą (wynik `outside`).\n")
    return ("\nProjekt Node: instalacja zależności przed krokiem nie powiodła się. Ostatnie linie:\n"
            f"{tail}\nJeśli potrzebujesz instalacji, poproś o uruchomienie polecenia poza piaskownicą (wynik `outside`).\n")


# Domeny rejestrów pochodzą z szablonów stosów (box/stacks/*.yaml, pozycje `registry: true`, #30).
REGISTRY_DOMAINS = {
    "node": templates.registry_domains("node"),
    "dotnet": templates.registry_domains("dotnet"),
    "python": templates.registry_domains("python-qt"),
}


def registry_domains(worktree: str, *, dotnet: bool) -> tuple:
    """P1 (03.11, docs/sandbox-and-permissions.md): domeny rejestrów paczek według stosu worktree."""
    stacks = []
    if is_node(worktree):
        stacks.append("node")
    if dotnet:
        stacks.append("dotnet")
    if any(os.path.isfile(os.path.join(worktree, name)) for name in ("pyproject.toml", "requirements.txt", "setup.py")):
        stacks.append("python")
    return tuple(dict.fromkeys(d for stack in stacks for d in REGISTRY_DOMAINS[stack]))


def chrome_bin(home: str) -> str:
    """Chromium z pamięci Playwrighta (najnowszy) dla Karmy i innych testów przeglądarkowych (`CHROME_BIN`, 03.11
    K-31/DPT-5: „No binary for ChromeHeadless”). Pusty napis, gdy Playwright niczego nie pobrał."""
    found = sorted(glob.glob(os.path.join(home, ".cache", "ms-playwright", "chromium-*", "chrome-linux*", "chrome")),
                   key=lambda p: _version_key(os.path.basename(os.path.dirname(os.path.dirname(p))).split("-")[-1]))
    return os.path.realpath(found[-1]) if found else ""
