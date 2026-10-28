"""Widżety Tide, których nowy kod czeka na restart Plasmy (PT-32).

plasmashell wczytuje kod widżetu raz, przy starcie — `make install-widget-*` podmienia pliki
na dysku, ale działający pasek dalej używa starej wersji, dopóki powłoka się nie zrestartuje
(28.10: kafelek Agenci liczył pytania jako „gotowe” jeszcze dzień po poprawce PT-29).
Skrypt porównuje czas startu plasmashell z najnowszym plikiem każdego zainstalowanego
widżetu org.tide.* i wypisuje te, które są nowsze od powłoki.

Użycie: /usr/bin/python3 widgets/stale.py   (kod 0 zawsze; brak wydruku, gdy wszystko aktualne)
"""
import os
import sys

PREFIX = "org.tide."


def plasmoids_dir() -> str:
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "plasma", "plasmoids")


def newest_mtime(path: str) -> float:
    newest = 0.0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            try:
                newest = max(newest, os.stat(os.path.join(folder, name)).st_mtime)
            except OSError:
                pass
    return newest


def shell_started(proc: str = "/proc") -> float | None:
    """Czas startu (epoch) najstarszego procesu plasmashell albo None, gdy powłoka nie działa."""
    try:
        with open(os.path.join(proc, "stat")) as fh:
            boot = next(float(line.split()[1]) for line in fh if line.startswith("btime "))
    except (OSError, StopIteration, ValueError):
        return None
    ticks = os.sysconf("SC_CLK_TCK")
    started = None
    for pid in os.listdir(proc):
        if not pid.isdigit():
            continue
        try:
            with open(os.path.join(proc, pid, "stat")) as fh:
                stat = fh.read()
        except OSError:
            continue
        name = stat[stat.find("(") + 1:stat.rfind(")")]
        if name != "plasmashell":
            continue
        when = boot + int(stat[stat.rfind(")") + 2:].split()[19]) / ticks
        started = when if started is None else min(started, when)
    return started


def stale_widgets(root: str, started: float | None) -> list[str]:
    """Identyfikatory widżetów zainstalowanych po starcie powłoki (posortowane)."""
    if started is None or not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root)
                  if name.startswith(PREFIX) and newest_mtime(os.path.join(root, name)) > started)


def message(stale: list[str]) -> str:
    if not stale:
        return ""
    names = ", ".join(name[len(PREFIX):] for name in stale)
    return (f"UWAGA: Plasma działa ze starszym kodem widżetów: {names}.\n"
            "Nowa wersja zadziała dopiero po: systemctl --user restart plasma-plasmashell")


def main() -> int:
    text = message(stale_widgets(plasmoids_dir(), shell_started()))
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
