#!/usr/bin/python3
"""Pakiety minimalnej instalacji Tide (usługa, okno Tablicy, CLI) i strażnik symulacji menedżera pakietów.

Używa go scripts/bootstrap-machine.sh; macierz z opisem jest też w docs/install-new-machine.md.

Użycie: bootstrap_packages.py list apt|dnf [required|optional]   nazwy pakietów, po jednej w wierszu
        bootstrap_packages.py qmldirs                             „pakiet ścieżka-qmldir” modułów QML (apt)
        bootstrap_packages.py table                               macierz: apt / dnf / po co / obowiązkowy
        bootstrap_packages.py guard apt|dnf < wyjście             symulacja `apt-get -s install …` albo
                                                                  `dnf install --assumeno …`: kod 0 — można
                                                                  instalować, 3 — symulacja podnosi, obniża
                                                                  albo usuwa pakiety Qt 6 lub KDE Frameworks 6
                                                                  (zasada projektu), 4 — nie rozpoznano wyjścia

Zależności wzięte z kodu: importy Pythona usługi i okna (PyQt6: QtCore, QtDBus, QtGui, QtNetwork, QtQml,
QtQuick), importy QML okna (QtQuick, Controls, Layouts, Shapes, Window, Templates, Dialogs) i programy
wywoływane przez usługę w przepływie karty (git, systemd-run, bwrap, socat dla piaskownicy Claude Code).
"""
from __future__ import annotations

import re
import sys

# (apt, dnf, po co, obowiązkowy). Pusta nazwa — w tej dystrybucji niepotrzebny osobny pakiet.
PACKAGES = (
    ("python3", "python3", "usługa, okno i CLI uruchamiają /usr/bin/python3 (min. 3.10)", True),
    ("python3-pyqt6", "python3-pyqt6",
     "PyQt6: QtCore, QtDBus (usługa i CLI na D-Bus), QtGui, QtNetwork; w Fedorze także QtQml i QtQuick", True),
    ("python3-pyqt6.qtqml", "", "PyQt6.QtQml — silnik QML okna Tide", True),
    ("python3-pyqt6.qtquick", "", "PyQt6.QtQuick — okno Tide, zrzuty raportu spraw", True),
    ("qml6-module-qtquick", "qt6-qtdeclarative", "QML: import QtQuick", True),
    ("qml6-module-qtquick-controls", "", "QML: QtQuick.Controls", True),
    ("qml6-module-qtquick-layouts", "", "QML: QtQuick.Layouts", True),
    ("qml6-module-qtquick-shapes", "", "QML: QtQuick.Shapes (ikony i logo rysowane ścieżkami)", True),
    ("qml6-module-qtquick-window", "", "QML: QtQuick.Window", True),
    ("qml6-module-qtquick-templates", "", "QML: QtQuick.Templates", True),
    ("qml6-module-qtquick-dialogs", "", "QML: QtQuick.Dialogs (widok Sprawy)", True),
    ("qml6-module-qtqml-workerscript", "", "QML: zależność QtQuick.Controls", True),
    ("git", "git", "Tablica to repozytorium git; worktree i gałęzie kart", True),
    ("make", "make", "instalacja przez make install-*", True),
    ("bubblewrap", "bubblewrap", "piaskownica kroków (Claude Code) i git usługi w piaskownicy (/usr/bin/bwrap)", True),
    ("socat", "socat", "pośrednik sieci w piaskownicy Claude Code", True),
    ("zstd", "zstd", "kopia zapasowa wyglądu przed instalacją (make pre-install-backup)", True),
    ("python3-dbus", "python3-dbus", "wtyczka KRunnera w usłudze (bez niej usługa działa, KRunner nie)", False),
    ("python3-gi", "python3-gobject", "pętla GLib wtyczki KRunnera", False),
    ("ripgrep", "ripgrep", "rg na liście poleceń tylko do odczytu kroku opiekuna (zapasowo grep)", False),
    ("python3-pil", "python3-pillow", "ikony wpisów menu „Tide Sesje” i „Tide Worktrees” (bez niej — bez ikon)", False),
    ("librsvg2-bin", "librsvg2-tools", "rsvg-convert do tych samych ikon", False),
    ("desktop-file-utils", "desktop-file-utils", "odświeżenie bazy plików .desktop po instalacji okna", False),
)

# Qt 6 i KDE Frameworks 6 — w Ubuntu/neon (libqt6*, qt6-*, qml6-module-*, libkf6*, kf6-*) i Fedorze
# (qt6-*, kf6-*). Pakiety z „-qt6” w środku (np. libpolkit-qt6-1-1) to biblioteki budowane na Qt, nie Qt.
GUARDED = re.compile(r"^(lib)?(qt6|kf6)|^qml6-module-")


# Moduł QML, który daje pakiet apt — w KDE neon te moduły są w qt6-declarative, a nie w qml6-module-*, więc
# o brakach rozstrzyga plik qmldir w katalogu modułów QML, a nie nazwa pakietu.
QMLDIRS = {
    "qml6-module-qtquick": "QtQuick/qmldir",
    "qml6-module-qtquick-controls": "QtQuick/Controls/qmldir",
    "qml6-module-qtquick-layouts": "QtQuick/Layouts/qmldir",
    "qml6-module-qtquick-shapes": "QtQuick/Shapes/qmldir",
    "qml6-module-qtquick-window": "QtQuick/Window/qmldir",
    "qml6-module-qtquick-templates": "QtQuick/Templates/qmldir",
    "qml6-module-qtquick-dialogs": "QtQuick/Dialogs/qmldir",
    "qml6-module-qtqml-workerscript": "QtQml/WorkerScript/qmldir",
}


def names(manager: str, which: str = "") -> list[str]:
    index = 0 if manager == "apt" else 1
    out = []
    for row in PACKAGES:
        if which == "required" and not row[3] or which == "optional" and row[3]:
            continue
        if row[index] and row[index] not in out:
            out.append(row[index])
    return out


def table() -> str:
    lines = ["| Kubuntu (apt) | Fedora (dnf) | Po co | Obowiązkowy |", "| --- | --- | --- | --- |"]
    for apt, dnf, why, required in PACKAGES:
        lines.append(f"| `{apt}` | {f'`{dnf}`' if dnf else '(w pakiecie wyżej)'} | {why} | "
                     f"{'tak' if required else 'nie'} |")
    return "\n".join(lines)


APT_INST = re.compile(r"^Inst (\S+)(?: \[([^\]]*)\])?(?: \((\S+))?")
APT_REMV = re.compile(r"^Remv (\S+)")


def parse_apt(text: str) -> dict:
    """Symulacja apt-get -s: {"install": [...], "upgrade": [(nazwa, stara, nowa)], "remove": [...]}.
    „Inst x [stara] (nowa …)” to zmiana wersji zainstalowanego pakietu (w górę albo w dół)."""
    out = {"install": [], "upgrade": [], "remove": []}
    for line in text.splitlines():
        found = APT_INST.match(line)
        if found:
            name = found.group(1).split(":")[0]
            if found.group(2) is not None:
                out["upgrade"].append((name, found.group(2), found.group(3) or ""))
            else:
                out["install"].append(name)
            continue
        found = APT_REMV.match(line)
        if found:
            out["remove"].append(found.group(1).split(":")[0])
    return out


# Nagłówki sekcji transakcji dnf4 i dnf5 („Upgrading:”, „Installing dependencies:”, „Removing:” …).
DNF_SECTION = re.compile(r"^([A-Z][A-Za-z ]+):\s*$")
DNF_CHANGE = ("Upgrading", "Downgrading", "Removing", "Reinstalling", "Replacing")


def parse_dnf(text: str) -> dict:
    """Transakcja z `dnf install --assumeno`: {"install": [...], "upgrade": [(nazwa, "", nowa)], "remove": [...]}."""
    out = {"install": [], "upgrade": [], "remove": []}
    section = ""
    for line in text.splitlines():
        header = DNF_SECTION.match(line)
        if header:
            section = header.group(1).strip()
            continue
        if not line.startswith(" ") or not section:
            if line.strip() and not line.startswith(" "):
                section = ""
            continue
        parts = line.split()
        if not parts or parts[0] in ("replacing", "replaced"):
            continue
        name = parts[0]
        version = parts[2] if len(parts) > 2 else ""
        if section.startswith("Installing"):
            out["install"].append(name)
        elif section.startswith(("Upgrading", "Downgrading", "Reinstalling", "Replacing")):
            out["upgrade"].append((name, "", version))
        elif section.startswith("Removing"):
            out["remove"].append(name)
    return out


def blocked(plan: dict) -> list[str]:
    """Zmiany pakietów Qt 6 i KDE Frameworks 6, których instalacja Tide nie może zrobić."""
    found = [f"{name} {old} → {new}".replace("  ", " ") for name, old, new in plan["upgrade"] if GUARDED.search(name)]
    found += [f"{name} (usunięcie)" for name in plan["remove"] if GUARDED.search(name)]
    return found


def recognized(manager: str, text: str, plan: dict) -> bool:
    """Czy wyjście wygląda na symulację: coś zaplanowano albo menedżer mówi, że nic nie trzeba."""
    if any(plan.values()):
        return True
    if manager == "apt":
        return bool(re.search(r"^0 upgraded, 0 newly installed|^0 aktualizowanych, 0 nowo instalowanych",
                              text, re.M))
    return bool(re.search(r"Nothing to do|Nie ma nic do zrobienia", text))


def guard(manager: str, text: str) -> int:
    plan = parse_apt(text) if manager == "apt" else parse_dnf(text)
    if not recognized(manager, text, plan):
        print("Nie rozpoznano wyjścia symulacji — przerywam, nic nie zainstalowano.", file=sys.stderr)
        return 4
    bad = blocked(plan)
    if bad:
        print("Symulacja zmienia pakiety Qt 6 albo KDE Frameworks 6 — instalacja Tide tego nie robi:", file=sys.stderr)
        for line in bad:
            print("  " + line, file=sys.stderr)
        print("Najpierw zaktualizuj cały system (sudo apt full-upgrade / sudo dnf upgrade), uruchom komputer "
              "ponownie i spróbuj jeszcze raz.", file=sys.stderr)
        return 3
    print(f"Symulacja: {len(plan['install'])} do instalacji, {len(plan['upgrade'])} do aktualizacji "
          f"(bez Qt 6 i KDE Frameworks 6), {len(plan['remove'])} do usunięcia.")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "list" and argv[1] in ("apt", "dnf"):
        print("\n".join(names(argv[1], argv[2] if len(argv) > 2 else "")))
        return 0
    if argv == ["qmldirs"]:
        print("\n".join(f"{name} {path}" for name, path in QMLDIRS.items()))
        return 0
    if argv == ["table"]:
        print(table())
        return 0
    if len(argv) == 2 and argv[0] == "guard" and argv[1] in ("apt", "dnf"):
        return guard(argv[1], sys.stdin.read())
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
