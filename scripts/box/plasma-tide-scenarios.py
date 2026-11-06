#!/usr/bin/python3
"""PT-176: próby wewnątrz pudełka; skrypt nie zatwierdza ani nie buduje profilu."""
import argparse
import base64
import json
import os
from pathlib import Path
import urllib.request
from urllib.parse import urlsplit, urlunsplit


def require_box():
    if not os.environ.get("TIDE_BOX_NAME") or Path.home() != Path("/home/agent"):
        raise RuntimeError("Próby wymagają tide box run jako agent")


def qt_ollama(model=None):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtGui import QFontDatabase, QGuiApplication
    from PyQt6.QtQml import QQmlComponent, QQmlEngine
    from PyQt6.QtCore import QUrl
    app = QGuiApplication([])
    fonts = QFontDatabase.families()
    if not fonts:
        raise RuntimeError("Brak czcionek Qt")
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(b'import QtQuick\nItem { width: 32; height: 32; Text { text: "Tide" } }', QUrl())
    item = component.create()
    if item is None:
        raise RuntimeError(str([error.toString() for error in component.errors()]))
    print(json.dumps({"platform": app.platformName(), "fonts": len(fonts), "qml": "ok"}), flush=True)
    # Bezpośrednia trasa Ollamy w proxy Tide; nie łączymy się z portem hosta.
    proxy = urlsplit(os.environ["HTTP_PROXY"])
    if proxy.scheme != "http" or not proxy.hostname or not proxy.port:
        raise RuntimeError("Niepoprawny adres proxy Tide")
    base = urlunsplit((proxy.scheme, f"{proxy.hostname}:{proxy.port}", "", "", ""))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Content-Type": "application/json"}
    if proxy.username is not None:
        credentials = f"{proxy.username}:{proxy.password or ''}".encode()
        headers["Proxy-Authorization"] = "Basic " + base64.b64encode(credentials).decode()

    def request(path, data=None):
        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data else None,
                                     headers=headers)
        with opener.open(req, timeout=180) as response:
            return json.load(response)

    models = request("/api/tags").get("models", [])
    names = [entry["name"] for entry in models]
    if not names:
        raise RuntimeError("Ollama hosta nie udostępnia lokalnych modeli")
    selected = model or names[0]
    if selected not in names:
        raise RuntimeError("Wybrany model nie jest lokalny; próba nie pobiera modeli")
    result = request("/api/generate", {"model": selected, "prompt": "Reply with OK.", "stream": False,
                                       "options": {"num_predict": 8}})
    if not result.get("done") or not result.get("response", "").strip():
        raise RuntimeError("Brak zakończonej odpowiedzi modelu")
    print(json.dumps({"ollama": "ok", "model": selected}), flush=True)


def pack_probe():
    root = Path("/packs/session-logs")
    files = [path for path in root.rglob("*") if path.is_file()]
    if not files:
        raise RuntimeError("Paczka dzienników jest pusta albo nie istnieje")
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    if not any(line.split()[4] == str(root) and "ro" in line.split()[5].split(",") for line in mounts):
        raise RuntimeError("Brak montowania ro paczki")
    print(json.dumps({"pack": str(root), "files": len(files), "mount": "ro"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("qt-ollama", "pack"))
    parser.add_argument("--model")
    args = parser.parse_args()
    require_box()
    if args.scenario == "qt-ollama":
        qt_ollama(args.model)
    else:
        pack_probe()


if __name__ == "__main__":
    main()
