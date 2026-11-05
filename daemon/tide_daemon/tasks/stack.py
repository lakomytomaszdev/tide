"""Wykrywanie stosu i CI w worktree karty (#30): `detect(worktree)` → `StackReport` (fakty, nie decyzje).

Moduł tylko czyta pliki: nie uruchamia `npm`, `dotnet`, `make` ani żadnego innego programu, nie otwiera sieci.
Wynik jest deterministyczny — katalogi i pliki przeglądamy w porządku alfabetycznym, listy w raporcie są
bez powtórzeń i w stałej kolejności. `propose_profile(report)` buduje z raportu i szablonów stosów
(`box/stacks/*.yaml`) propozycję profilu w kształcie `tide-box: 1`; walidację schematem robi #27 przy użyciu.

Zasady propozycji: nigdy nie ma `network.full`; pozycja czerwona nigdy nie jest włączona; adres wewnętrzny
(rejestr NuGet w sieci lokalnej) trafia jako wyłączona pozycja czerwona z opisem; projekt bez rozpoznanego
stosu daje propozycję pustą z polem `question` (pytanie do okna planu, nie do kroków).
"""
import fnmatch
import glob
import ipaddress
import json
import os
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from ..box import templates

STACK_ORDER = ("node", "playwright", "dotnet", "docker", "python-qt")
_SKIP_DIRS = {".git", "node_modules", "bin", "obj", "dist", "build", ".angular", "__pycache__", ".venv", "venv",
              "packages", ".idea", ".vs", "out", "coverage"}
_MAX_DEPTH = 6
_MAX_FILES = 4000
_MAX_BYTES = 512 * 1024
_INTERNAL_SUFFIXES = (".local", ".lan", ".internal", ".corp", ".home", ".intranet", ".biz")

_IMAGE = r"(?:[a-z0-9][a-z0-9.\-]*(?::\d+)?/)*[a-z0-9][a-z0-9._\-]*:[A-Za-z0-9][A-Za-z0-9._\-]*"
_IMAGE_WITH_PATH = re.compile(r"[\"'](" + r"(?:[a-z0-9][a-z0-9.\-]*(?::\d+)?/)+[a-z0-9][a-z0-9._\-]*:"
                              r"[A-Za-z0-9][A-Za-z0-9._\-]*" + r")[\"']")
_IMAGE_IN_CALL = re.compile(r"(?:Builder|WithImage|GenericContainer|Container)\(\s*[\"'](" + _IMAGE + r")[\"']")
_TESTCONTAINERS_SOURCES = ("*.cs", "*.ts")


@dataclass
class StackReport:
    """Fakty o projekcie. Pola puste/`False`, gdy nic nie wykryto."""
    stacks: list = field(default_factory=list)          # nazwy szablonów, w stałej kolejności
    node: dict = field(default_factory=dict)            # version, angular, karma, playwright, protractor, ports
    dotnet: dict = field(default_factory=dict)          # tfms, sdk, global_json_sdk, testcontainers, playwright
    python: dict = field(default_factory=dict)          # markers, make_targets, qt_offscreen, qt
    docker: dict = field(default_factory=dict)          # compose_files, compose_images, ports
    testcontainers_images: list = field(default_factory=list)
    nuget_sources: list = field(default_factory=list)   # {source, url, host, internal, known}
    ci: dict = field(default_factory=dict)              # files, images, commands, deploy_ignored
    internal_addresses: list = field(default_factory=list)  # {url, host, source}

    def to_dict(self) -> dict:
        return asdict(self)


# --- odczyt plików ------------------------------------------------------------------------------------------


def _read(path: str) -> str:
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return ""
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def _json(path: str) -> dict:
    try:
        data = json.loads(_read(path) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _walk(root: str, patterns: tuple) -> list:
    """Ścieżki plików pasujących do wzorców nazw, alfabetycznie, bez katalogów zależności i artefaktów."""
    found, seen = [], 0
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        if base[len(root):].count(os.sep) >= _MAX_DEPTH:
            dirs[:] = []
        for name in sorted(files):
            seen += 1
            if seen > _MAX_FILES:
                return found
            if any(fnmatch.fnmatchcase(name.lower(), p.lower()) for p in patterns):
                found.append(os.path.join(base, name))
    return found


def _first(root: str, *names: str) -> str:
    for name in names:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return ""


def _unique(items) -> list:
    return list(dict.fromkeys(items))


# --- Node ---------------------------------------------------------------------------------------------------


def _major(text: str) -> str:
    found = re.search(r"\d+", text or "")
    return found.group(0) if found else ""


def _node(root: str, ci_images: list) -> dict:
    package = os.path.join(root, "package.json")
    if not os.path.isfile(package):
        return {}
    data = _json(package)
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    version = ""
    for name in (".nvmrc", ".node-version"):
        version = _major(_read(os.path.join(root, name)))
        if version:
            break
    if not version:
        version = _major(str((data.get("engines") or {}).get("node", "")))
    if not version:
        version = next((_major(i.split(":", 1)[1]) for i in ci_images if i.startswith("node:")), "")
    info = {"version": version, "angular": "", "karma": {}, "playwright": {}, "protractor": False,
            "scripts": sorted((data.get("scripts") or {}).keys())}
    if "@angular/core" in deps or os.path.isfile(os.path.join(root, "angular.json")):
        info["angular"] = _major(str(deps.get("@angular/core", ""))) or "tak"
    karma = _first(root, "karma.conf.js", "karma.conf.ts", "src/karma.conf.js")
    if karma or "karma" in deps:
        text = _read(karma) if karma else ""
        browsers = re.search(r"browsers\s*:\s*\[([^\]]*)\]", text)
        port = re.search(r"\bport\s*:\s*(\d+)", text)
        info["karma"] = {
            "browsers": [b.strip(" \"'") for b in browsers.group(1).split(",") if b.strip()] if browsers else [],
            "port": int(port.group(1)) if port else 9876,
        }
    configs = sorted(glob.glob(os.path.join(root, "playwright.config.*")))
    if configs or "@playwright/test" in deps or "playwright" in deps:
        text = "\n".join(_read(path) for path in configs)
        envs = sorted(set(re.findall(r"process\.env\.([A-Z0-9_]*PORT[A-Z0-9_]*)", text)))
        info["playwright"] = {"port_env": envs, "web_server": "webServer" in text,
                              "configs": [os.path.basename(p) for p in configs]}
    info["protractor"] = os.path.isfile(os.path.join(root, "e2e", "protractor.conf.js"))
    return info


# --- .NET ---------------------------------------------------------------------------------------------------


def _is_internal(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
        return address.is_private or address.is_loopback or address.is_link_local
    except ValueError:
        pass
    return "." not in host or host.endswith(_INTERNAL_SUFFIXES)


def _nuget_sources(root: str) -> list:
    known = templates.known_domains()
    result = []
    for path in sorted(glob.glob(os.path.join(root, "*"))):
        name = os.path.basename(path)
        if not (os.path.isfile(path) and name.lower().startswith("nuget") and name.lower().endswith(".config")):
            continue
        for block in re.findall(r"<packageSources>(.*?)</packageSources>", _read(path), re.S | re.I):
            for key, url in re.findall(r"<add\s+key=\"([^\"]*)\"\s+value=\"([^\"]*)\"", block, re.I):
                host = (urlsplit(url).hostname or "").lower()
                if not host:
                    continue
                result.append({"source": name, "key": key, "url": url, "host": host,
                               "internal": _is_internal(host), "known": host in known})
    return result


def _tfm_sdk(tfm: str) -> str:
    found = re.fullmatch(r"net(\d+\.\d+)", tfm)
    return found.group(1) if found else ""


def _dotnet(root: str) -> dict:
    csprojs = _walk(root, ("*.csproj",))
    solutions = sorted(glob.glob(os.path.join(root, "*.sln")) + glob.glob(os.path.join(root, "*.slnx")))
    if not csprojs and not solutions:
        return {}
    tfms = []
    for path in csprojs:
        text = _read(path)
        for block in re.findall(r"<TargetFrameworks?>([^<]*)</TargetFrameworks?>", text):
            tfms += [t.strip() for t in block.split(";") if t.strip()]
    tfms = sorted(set(tfms), key=lambda t: (_tfm_sdk(t) == "", t))
    packages = "\n".join(_read(p) for p in [*csprojs, *sorted(glob.glob(os.path.join(root, "Directory.*.props")))])
    versions = {}
    for name, version in re.findall(r"Include=\"([^\"]+)\"[^>]*?Version=\"([^\"]+)\"", packages):
        versions.setdefault(name, version)
    global_sdk = ""
    if os.path.isfile(os.path.join(root, "global.json")):
        global_sdk = str((_json(os.path.join(root, "global.json")).get("sdk") or {}).get("version", ""))
    sdk = sorted({s for s in map(_tfm_sdk, tfms) if s}, key=lambda s: [int(p) for p in s.split(".")], reverse=True)
    if global_sdk and not sdk:
        sdk = [".".join(global_sdk.split(".")[:2])]
    containers = sorted(n for n in versions if n.lower().startswith("testcontainers"))
    return {"tfms": tfms, "sdk": sdk, "global_json_sdk": global_sdk,
            "solutions": [os.path.basename(s) for s in solutions],
            "testcontainers": {n: versions[n] for n in containers},
            "playwright": versions.get("Microsoft.Playwright", "")}


# --- Docker i obrazy Testcontainers -------------------------------------------------------------------------


def _docker(root: str) -> dict:
    files = [os.path.basename(p) for p in sorted(
        glob.glob(os.path.join(root, "docker-compose*.yml")) + glob.glob(os.path.join(root, "docker-compose*.yaml"))
        + glob.glob(os.path.join(root, "compose.yml")) + glob.glob(os.path.join(root, "compose.yaml")))]
    if not files:
        return {}
    images, ports = [], []
    for name in files:
        text = _read(os.path.join(root, name))
        images += re.findall(r"^\s*image:\s*[\"']?([^\s\"'#]+)", text, re.M)
        ports += [int(p) for p in re.findall(r"^\s*-\s*[\"']?(?:[\d.]+:)?(\d+):\d+", text, re.M)]
    return {"compose_files": files, "compose_images": _unique(images), "ports": sorted(set(ports))}


def _testcontainers_images(root: str) -> list:
    images = []
    for path in _walk(root, _TESTCONTAINERS_SOURCES):
        text = _read(path)
        if "testcontainers" not in text.lower():
            continue
        images += _IMAGE_WITH_PATH.findall(text) + _IMAGE_IN_CALL.findall(text)
    return sorted(set(images))


# --- CI -----------------------------------------------------------------------------------------------------

_CI_COMMAND = re.compile(r"^\s*-?\s*(?:run:\s*)?((?:npm|npx|yarn|dotnet|make|pytest|python3?\s+-m)\s+[^\n#]*)", re.M)
_DEPLOY = re.compile(r"\b(rsync|scp|ssh)\b|\bsudo\b")


def _ci(root: str) -> dict:
    files = [p for p in [os.path.join(root, ".gitlab-ci.yml"),
                         *sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.yml"))),
                         *sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.yaml")))]
             if os.path.isfile(p)]
    if not files:
        return {}
    images, commands, deploy = [], [], False
    for path in files:
        text = _read(path)
        images += re.findall(r"^\s*image:\s*[\"']?([^\s\"'#]+)", text, re.M)
        commands += [c.strip() for c in _CI_COMMAND.findall(text)]
        deploy = deploy or bool(_DEPLOY.search(text))
    return {"files": [os.path.relpath(p, root) for p in files], "images": _unique(images),
            "commands": _unique(commands), "deploy_ignored": deploy}


# --- Python -------------------------------------------------------------------------------------------------


def _python(root: str) -> dict:
    markers = [n for n in ("pyproject.toml", "requirements.txt", "setup.py") if os.path.isfile(os.path.join(root, n))]
    make_files = [p for p in [os.path.join(root, "Makefile"), *sorted(glob.glob(os.path.join(root, "mk", "*.mk")))]
                  if os.path.isfile(p)]
    make_text = "\n".join(_read(p) for p in make_files)
    if make_files:
        markers.append("Makefile")
    targets = {t for t in re.findall(r"^([A-Za-z][\w\-]*)\s*:(?!=)", make_text, re.M)}
    if not markers or not (set(markers) - {"Makefile"} or "python3" in make_text or "python " in make_text):
        return {}
    text = make_text + "\n" + "\n".join(_read(os.path.join(root, n)) for n in markers if n != "Makefile")
    qt = bool(re.search(r"pyqt|pyside|\bqml\b|QT_QPA_PLATFORM", text, re.I))
    return {"markers": markers, "make_targets": sorted(targets & {"test", "lint"}),
            "qt": qt, "qt_offscreen": "offscreen" in text}


# --- całość -------------------------------------------------------------------------------------------------


def detect(worktree: str) -> StackReport:
    """Raport faktów o worktree. Tylko odczyt plików; brak katalogu daje raport pusty."""
    root = os.path.abspath(worktree)
    if not os.path.isdir(root):
        return StackReport()
    ci = _ci(root)
    node = _node(root, ci.get("images", []))
    dotnet = _dotnet(root)
    python = _python(root)
    docker = _docker(root)
    images = _testcontainers_images(root)
    sources = _nuget_sources(root) if dotnet else []
    stacks = []
    if node:
        stacks.append("node")
    if node.get("playwright") or dotnet.get("playwright"):
        stacks.append("playwright")
    if dotnet:
        stacks.append("dotnet")
    if docker or images or dotnet.get("testcontainers"):
        stacks.append("docker")
    if python.get("qt"):
        stacks.append("python-qt")
    internal = [{"url": s["url"], "host": s["host"], "source": s["source"]} for s in sources if s["internal"]]
    return StackReport(stacks=[s for s in STACK_ORDER if s in stacks], node=node, dotnet=dotnet, python=python,
                       docker=docker, testcontainers_images=images, nuget_sources=sources, ci=ci,
                       internal_addresses=internal)


# --- propozycja profilu -------------------------------------------------------------------------------------

QUESTION = ("Nie rozpoznano stosu projektu {project}. Jakich pakietów apt (`apt`), poleceń budowy (`setup`), "
            "weryfikacji (`verify`) i domen sieci (`network.domains`) potrzebuje pudełko? Zaproponuj je z README/CI; "
            "gdy nie wiesz, zostaw puste i zapytaj użytkownika w oknie planu.")


def _item(kind: str, value: str, risk: str, description: str, source: str) -> dict:
    # Pozycja czerwona jest zawsze wyłączona — to niezmiennik propozycji, nie ustawienie.
    return {"kind": kind, "value": value, "risk": risk, "enabled": risk != "red", "description": description,
            "source": source}


def propose_profile(report: StackReport, project: str = "") -> dict:
    """Propozycja profilu `tide-box: 1` z raportu i szablonów. Nigdy `network.full`, nigdy włączona pozycja czerwona."""
    base = {"tide-box": 1, "project": project, "type": "container", "base": "images:ubuntu/24.04"}
    if not report.stacks:
        return {**base, "stacks": [], "items": [], "question": QUESTION.format(project=project or "(bez nazwy)")}
    profile = {**base, "stacks": list(report.stacks)}
    items = []
    for stack in ["base", *report.stacks]:
        template = templates.load(stack)
        for entry in template.get("domains", []):
            items.append(_item("domain", entry["name"], entry["risk"], entry.get("note", ""), f"stack:{stack}"))
    extra_domains = []
    if report.python and not report.python.get("qt"):
        extra_domains += list(templates.registry_domains("python-qt"))
    for source in report.nuget_sources:
        if source["host"] in templates.known_domains():
            continue
        what = ("adres wewnętrzny (sieć lokalna) — wymaga jawnej zgody, domyślnie wyłączony" if source["internal"]
                else "rejestr spoza listy znanych domen — wymaga jawnej zgody, domyślnie wyłączony")
        items.append(_item("domain", source["host"], "red", f"Rejestr NuGet z {source['source']} ({source['url']}): "
                           f"{what}", f"nuget:{source['source']}"))
    images = list(dict.fromkeys([*templates.load("docker")["images"], *report.docker.get("compose_images", []),
                                 *report.testcontainers_images])) if "docker" in report.stacks else []
    notes = {n["image"]: n for n in templates.load("docker").get("image_notes", [])} if images else {}
    for image in images:
        note = notes.get(image, {})
        items.append(_item("image", image, note.get("risk", "yellow"),
                           " ".join([templates.DOCKER_RISK_NOTE, note.get("note", "obraz wykryty w projekcie")]),
                           "docker"))
    node = report.node
    if node:
        profile["node"] = {"version": node.get("version") or "20"}
    if report.dotnet and report.dotnet.get("sdk"):
        sdk = report.dotnet["sdk"]
        profile["dotnet"] = {"sdk": sdk[0] if len(sdk) == 1 else list(sdk)}
    profile["apt"] = []
    profile["setup"] = []
    profile["network"] = {"domains": _unique(extra_domains), "host-ollama": False}
    ports = node.get("playwright", {}).get("port_env") or []
    if ports:
        profile["ports"] = {"pool": [4300, 4399], "env": list(ports)}
    profile["docker"] = {"enabled": "docker" in report.stacks, "images": images}
    profile["verify"] = _verify(report)
    profile["items"] = items
    return profile


def _verify(report: StackReport) -> list:
    verify = []
    if "node" in report.stacks and "build" in report.node.get("scripts", []):
        verify.append("npm run build")
    if report.node.get("karma"):
        verify.append("npm test -- --watch=false --browsers=ChromeHeadless")
    if "dotnet" in report.stacks:
        verify.append("dotnet build --no-restore")
    if "test" in report.python.get("make_targets", []):
        verify.append("make test")
    if "lint" in report.python.get("make_targets", []):
        verify.append("make lint")
    return verify
