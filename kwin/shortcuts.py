"""Kto jeszcze ma przypisany dany skrót w kglobalshortcutsrc — ostrzeżenie przy instalacji.

KGlobalAccel nie przypisze domyślnego klawisza nowej akcji, jeśli zajmuje go inna, więc
skrypt KWin zostałby bez skrótu. Instalator niczego nie zmienia w cudzych skrótach — tylko
mówi, co koliduje i gdzie to zmienić.

Format wpisu: `akcja=aktywne,domyślne,opis`, a kilka klawiszy w polu oddziela tabulator.
Użycie: python3 kwin/shortcuts.py <kglobalshortcutsrc> <klawisz> <komponent/akcja-własna>
"""
import sys


def conflicts(path: str, key: str, own: str = "") -> list[str]:
    """Lista „komponent/akcja (opis)” z aktywnym skrótem `key`, poza własną akcją `own`."""
    found, group = [], ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    wanted = key.lower()
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
            continue
        name, sep, value = line.partition("=")
        if not sep or name.startswith("_k_"):
            continue
        fields = value.split(",")
        active = [k.strip().lower() for k in fields[0].split("\t")] if fields else []
        if wanted in active and f"{group}/{name}" != own:
            label = fields[2] if len(fields) > 2 and fields[2] else name
            found.append(f"{group}/{name} ({label})")
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    for item in conflicts(*argv):
        print(item)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
