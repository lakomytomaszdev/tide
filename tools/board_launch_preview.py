"""Dane do podglądu okna „Co ma zrobić agent?” na Tablicy (make preview-board-launch, PT-43).

Użycie: /usr/bin/python3 tools/board_launch_preview.py board.json [models.json] [--local] > dane.json
board.json to migawka tablicy (`tide-daemon.py --print-board` albo przykładowa z apps/board/tests/boardfix.py),
models.json — stan z `tide-daemon.py --print-models` (Status() obiektu /Models). Dokłada klucze „models”
i „tasks” (jak /Tasks Defaults: oba programy dostępne, zapamiętany model). --local: przykładowa rola `agent`
(qwen3-coder:30b na pierwszym serwerze, serwer odpowiada) i zapamiętany „Lokalny”. Tylko odczyt.
"""
import json
import sys


def main(argv: list[str]) -> int:
    local = "--local" in argv
    paths = [a for a in argv if a != "--local"]
    with open(paths[0], encoding="utf-8") as fh:
        data = json.load(fh)
    models = None
    if len(paths) > 1:
        with open(paths[1], encoding="utf-8") as fh:
            models = json.load(fh)
    if local:
        models = models if isinstance(models, dict) else {"checked": True, "endpoints": {}, "roles": {}}
        endpoints = models.setdefault("endpoints", {})
        endpoint = models.get("default") or next(iter(endpoints), "local")
        endpoints.setdefault(endpoint, {"url": "http://127.0.0.1:11434", "kind": "ollama"})["online"] = True
        models["checked"] = True
        models.setdefault("roles", {})["agent"] = {"endpoint": endpoint, "model": "qwen3-coder:30b", "ctx": None,
                                                   "installed": True, "available": True, "error": ""}
    data["models"] = models
    data["tasks"] = {"tools": {"claude": True, "codex": True}, "model": "local" if local else "cloud"}
    json.dump(data, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
