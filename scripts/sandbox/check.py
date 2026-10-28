"""Profil AppArmor tylko dla bwrap (PT-75): treść profilu i stan piaskownicy, bez sudo.

Użycie: python3 scripts/sandbox/check.py            stan: czy bwrap działa i czy reszta systemu
                                                    zostaje pod ograniczeniem (kod 0 — tak)
        python3 scripts/sandbox/check.py profile    treść profilu (czyta ją install.sh)

Na KDE neon 24.04 (jak na Ubuntu 24.04) kernel.apparmor_restrict_unprivileged_userns = 1: proces
bez własnego profilu, który zakłada przestrzeń użytkownika, dostaje profil unprivileged_userns bez
uprawnień, więc bwrap kończy się „setting up uid map: Permission denied”, a piaskownica Claude Code
wyłącza się (albo odmawia startu przy sandbox.failIfUnavailable).

Profil to bwrap-userns-restrict z pakietu Ubuntu apparmor-profiles (4.0.1really4.0.1-0ubuntu0.24.04.9,
/usr/share/apparmor/extra-profiles/, w pakiecie wyłączony): bwrap może założyć przestrzeń
użytkownika i ma w niej uprawnienia, a programy uruchomione przez bwrap dostają stos z unpriv_bwrap,
który uprawnienia odbiera. Dzięki temu bwrap nie jest furtką do obejścia ograniczenia, a sysctl
i profil unprivileged_userns dla reszty systemu zostają bez zmian. Prostszy profil z dokumentacji
Claude Code (flags=(unconfined) + userns) zostawiłby dzieciom bwrap pełne uprawnienia w przestrzeni.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Zmienne dla testów (sudo czyści środowisko, więc przy instalacji nie działają):
# TIDE_SANDBOX_ROOT — korzeń z etc/apparmor.d i proc/sys, TIDE_SANDBOX_BWRAP i TIDE_SANDBOX_UNSHARE —
# programy prób zamiast /usr/bin/bwrap i unshare.
ROOT = Path(os.environ.get("TIDE_SANDBOX_ROOT") or "/")
BWRAP = "/usr/bin/bwrap"
TARGET = "etc/apparmor.d/bwrap-userns-restrict"
SYSCTL = "proc/sys/kernel/apparmor_restrict_unprivileged_userns"
MARKER = "scripts/sandbox/install.sh"
PROBE_TIMEOUT_S = 10

PROFILE = """\
# Tide: piaskownica kroków w tle (scripts/sandbox/install.sh, PT-75). Usunięcie: scripts/sandbox/uninstall.sh
# Źródło: Ubuntu apparmor-profiles 4.0.1really4.0.1-0ubuntu0.24.04.9,
# /usr/share/apparmor/extra-profiles/bwrap-userns-restrict — reguły bez zmian.
#
# This profile allows almost everything and only exists to allow
# bwrap to work on a system with user namespace restrictions
# being enforced.
# bwrap is allowed access to user namespaces and capabilities
# within the user namespace, but its children do not have
# capabilities, blocking bwrap from being able to be used to
# arbitrarily by-pass the user namespace restrictions.
#
# Note: the bwrap child is stacked against the bwrap profile due to
# bwraps use of no-new-privs

abi <abi/4.0>,

include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(attach_disconnected) {
  allow capability,
  # not allow all, to allow for pix stack
  # sadly we have to allow  m every where to allow children to work under
  # stacking.
  allow file rwlkm /{**,},
  allow network,
  allow unix,
  allow ptrace,
  allow signal,
  allow mqueue,
  allow io_uring,
  allow userns,
  allow mount,
  allow umount,
  allow pivot_root,
  allow dbus,
  allow px /** -> bwrap//&unpriv_bwrap,

  # the local include should not be used without understanding the userns
  # restriction.
  # Site-specific additions and overrides. See local/README for details.
  include if exists <local/bwrap-userns-restrict>
}

profile unpriv_bwrap flags=(attach_disconnected) {
  # not allow all, to allow for pix stack
  allow file rwlkm /{**,},
  allow network,
  allow unix,
  allow ptrace,
  allow signal,
  allow mqueue,
  allow io_uring,
  allow userns,
  allow mount,
  allow umount,
  allow pivot_root,
  allow dbus,

  allow pix /** -> &unpriv_bwrap,

  audit deny capability,

  # the local include should not be used without understanding the userns
  # restriction.
  # Site-specific additions and overrides. See local/README for details.
  include if exists <local/unpriv_bwrap>
}
"""


def restrict_userns(root: Path = ROOT) -> int | None:
    """kernel.apparmor_restrict_unprivileged_userns: 1, 0 albo None, gdy jądro nie ma tego ustawienia."""
    try:
        return int((root / SYSCTL).read_text().strip())
    except (OSError, ValueError):
        return None


def profile_owner(root: Path = ROOT) -> str:
    """Plik /etc/apparmor.d/bwrap-userns-restrict: „brak”, „tide” (nasz znacznik) albo „obcy”."""
    try:
        text = (root / TARGET).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "brak"
    return "tide" if MARKER in text.split("\n", 1)[0] else "obcy"


ATTACH_BWRAP = re.compile(r"^\s*(?:profile\s+\S+\s+)?\"?/usr/bin/bwrap\"?[\s{]", re.M)


def conflicts(root: Path = ROOT) -> list[str]:
    """Inne pliki w /etc/apparmor.d z profilem dla /usr/bin/bwrap (np. /etc/apparmor.d/bwrap z dokumentacji
    Claude Code). Dwa profile dla tej samej ścieżki to niejasne, który wygra — instalacja wtedy przerywa."""
    found = []
    base = root / "etc/apparmor.d"
    for path in sorted(base.iterdir()) if base.is_dir() else []:
        if not path.is_file() or path == root / TARGET:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if ATTACH_BWRAP.search(text):
            found.append("/" + str(path.relative_to(root)))
    return found


def probe(argv: list[str]) -> tuple[bool, str]:
    """Uruchamia próbę; (udana, wyjście albo błąd)."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, (result.stderr.strip() or result.stdout.strip() or f"kod {result.returncode}")


def bwrap_probe() -> dict:
    """bwrap tak, jak uruchamia go piaskownica Claude Code: z własną siecią (loopback w nowej przestrzeni)."""
    program = os.environ.get("TIDE_SANDBOX_BWRAP") or BWRAP
    works, out = probe([program, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-net",
                        "--", "cat", "/proc/self/attr/current"])
    return {"works": works, "label": out if works else None, "error": None if works else out}


def others_restricted(restrict: int | None) -> bool | None:
    """Czy zwykły program zakładający przestrzeń użytkownika nadal trafia do unprivileged_userns."""
    if restrict == 0:
        return False
    program = os.environ.get("TIDE_SANDBOX_UNSHARE") or "unshare"
    works, out = probe([program, "-U", "--", "cat", "/proc/self/attr/current"])
    if works:
        return out.startswith("unprivileged_userns")
    return None if restrict is None else True


def read_state(root: Path = ROOT) -> dict:
    restrict = restrict_userns(root)
    bwrap = bwrap_probe()
    restricted = others_restricted(restrict)
    return {
        "ok": bwrap["works"] and restricted is not False,
        "restrict_userns": restrict,
        "profile": profile_owner(root),
        "bwrap": bwrap,
        "others_restricted": restricted,
        "conflicts": conflicts(root),
    }


def report(state: dict) -> str:
    """Stan po polsku, z następnym krokiem."""
    restrict = state["restrict_userns"]
    lines = [
        "Ograniczenie przestrzeni użytkownika (kernel.apparmor_restrict_unprivileged_userns): "
        + {1: "włączone", 0: "WYŁĄCZONE dla całego systemu", None: "brak w tym jądrze"}[restrict],
        "Profil /" + TARGET + ": " + {"brak": "brak", "tide": "Tide",
                                       "obcy": "obcy (bez znacznika Tide — nie ruszamy go)"}[state["profile"]],
    ]
    bwrap = state["bwrap"]
    lines.append(f"bwrap: działa, programy w piaskownicy mają etykietę „{bwrap['label']}”" if bwrap["works"]
                 else f"bwrap: nie działa — {bwrap['error']}")
    lines.append({True: "Reszta systemu: nadal pod ograniczeniem (unshare -U → unprivileged_userns)",
                  False: "Reszta systemu: BEZ ograniczenia",
                  None: "Reszta systemu: nie udało się sprawdzić"}[state["others_restricted"]])
    for path in state["conflicts"]:
        lines.append(f"Inny profil dla /usr/bin/bwrap: {path}")
    if state["ok"]:
        lines.append("Piaskownica może działać.")
    elif state["others_restricted"] is False:
        lines.append("Ograniczenie wyłączono dla całego systemu (sysctl) — Tide tego nie robi. Przywrócenie: "
                     "sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=1")
    elif state["profile"] == "brak" and not state["conflicts"]:
        lines.append("Dalej: sudo bash scripts/sandbox/install.sh")
    elif state["profile"] == "tide":
        lines.append("Profil Tide jest na dysku, ale bwrap nie działa — czy jest wczytany? "
                     "sudo apparmor_parser -r /" + TARGET + "; dziennik: journalctl -k -g apparmor")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv[:1] == ["profile"]:
        sys.stdout.write(PROFILE)
        return 0
    if argv[:1] == ["--json"]:
        state = read_state()
        print(json.dumps(state, ensure_ascii=False, indent=1))
        return 0 if state["ok"] else 1
    if argv:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    state = read_state()
    print(report(state))
    return 0 if state["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
