"""Zrzuty widoku Modele na przykładowym Status() (fixture.py) — `make preview-models-view`.

Użycie: /usr/bin/python3 apps/models/tests/preview.py <pakiet okna Tide> <katalog wyjściowy>
Wynik: <katalog>/models-view-<stan>.png. Bez usługi i bez D-Bus (--data), zapis w podglądzie jest wyłączony;
błędy usługi przy kontrolkach podajemy opcją --preview note=<tag>|<tekst> (ModelsView.qml).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixture  # noqa: E402

# (nazwa zrzutu, wariant danych, opcje --preview, szerokość×wysokość)
PREVIEWS = [
    ("local", "local", [], (1800, 1000)),
    ("mac-offline", "mac-offline", [], (1800, 1000)),
    ("mac", "mac", ["advanced=summary"], (1800, 1000)),
    ("pulling", "pulling", ["note=role:describe:pull|na punkcie „local” trwa już pobieranie gpt-oss:20b — poczekaj, "
                            "aż się skończy"], (1380, 900)),
    ("add", "local", ["add=Mac Studio", "url=http://mac-studio:11434",
                      "ok=add:test|Połączenie działa · Ollama 0.41.2 · 5 modeli"], (1800, 1000)),
    ("edit", "mac-offline", ["edit=Mac-Studio", "url=http://10.20.1.40:11434",
                             "note=server:Mac-Studio:edit-test|Model lokalny niedostępny: serwer nie odpowiada "
                             "(10.20.1.40:11434)"], (1800, 1000)),
    ("errors", "errors", ["remove=local", "note=server:local:remove|punkt „local” jest domyślny — najpierw ustaw "
                          "inny punkt jako domyślny", "note=report|raport: nie udało się zapisać report.json "
                          "(PermissionError)"], (1800, 1000)),
    ("checking", "checking", [], (1380, 900)),
]


def main(pkg: str, out: str) -> int:
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    for name, variant, options, (width, height) in PREVIEWS:
        data = os.path.join(out, f"models-view-{variant}.json")
        with open(data, "w", encoding="utf-8") as fh:
            json.dump(fixture.sample(variant), fh, ensure_ascii=False)
        png = os.path.join(out, f"models-view-{name}.png")
        argv = ["/usr/bin/python3", os.path.join(pkg, "tide.py"), "--view", "models", "--data", data,
                "--grab", png, "--width", str(width), "--height", str(height)]
        for option in options:
            argv += ["--preview", option]
        result = subprocess.run(argv, env=env)
        if result.returncode != 0:
            return result.returncode
        print(f"Podgląd: {png}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
