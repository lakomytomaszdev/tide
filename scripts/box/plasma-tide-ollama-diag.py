#!/usr/bin/python3
"""PT-176: zawężona diagnostyka odpowiedzi Ollamy wewnątrz pudełka."""
import base64
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def main():
    if not os.environ.get("TIDE_BOX_NAME") or Path.home() != Path("/home/agent"):
        raise RuntimeError("Próba wymaga tide box run jako agent")
    proxy = urlsplit(os.environ["HTTP_PROXY"])
    if proxy.scheme != "http" or not proxy.hostname or not proxy.port:
        raise RuntimeError("Niepoprawny adres proxy Tide")
    base = f"http://{proxy.hostname}:{proxy.port}"
    headers = {"Content-Type": "application/json"}
    if proxy.username is not None:
        credentials = f"{proxy.username}:{proxy.password or ''}".encode()
        headers["Proxy-Authorization"] = "Basic " + base64.b64encode(credentials).decode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, data=None):
        req = urllib.request.Request(
            base + path, data=json.dumps(data).encode() if data else None, headers=headers)
        with opener.open(req, timeout=180) as response:
            return json.load(response)

    names = [entry["name"] for entry in request("/api/tags").get("models", [])]
    print(json.dumps({"local_models": names}), flush=True)
    if not names:
        raise RuntimeError("Brak lokalnych modeli")
    selected = names[0]
    print(json.dumps({"selected_model": selected}), flush=True)
    try:
        result = request("/api/generate", {
            "model": selected, "prompt": "Reply with OK.", "stream": False,
            "options": {"num_predict": 8}})
    except urllib.error.HTTPError as exc:
        print(json.dumps({
            "http_status": exc.code,
            "error_body": exc.read(8192).decode("utf-8", "replace")}), flush=True)
        raise SystemExit(1)
    print(json.dumps({
        "model": selected, "done": result.get("done"),
        "response": result.get("response")}), flush=True)
    if not result.get("done") or not result.get("response", "").strip():
        raise RuntimeError("Brak zakończonej odpowiedzi modelu")


if __name__ == "__main__":
    main()

