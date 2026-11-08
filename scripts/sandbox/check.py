"""Profil AppArmor tylko dla bwrap (PT-75): treść profilu i stan piaskownicy, bez sudo.

Użycie: python3 scripts/sandbox/check.py            stan: czy bwrap działa i czy reszta systemu
                                                    zostaje pod ograniczeniem (kod 0 — tak)
        python3 scripts/sandbox/check.py profile    treść profilu (czyta ją install.sh)

Na KDE neon 24.04 (jak na Ubuntu 24.04) kernel.apparmor_restrict_unprivileged_userns = 1: proces
bez własnego profilu, który zakłada przestrzeń użytkownika, dostaje profil unprivileged_userns bez
uprawnień, więc bwrap kończy się „setting up uid map: Permission denied”, a piaskownica Claude Code
wyłącza się (albo odmawia startu przy sandbox.failIfUnavailable).

Profil to ten z dokumentacji Claude Code: `profile bwrap /usr/bin/bwrap flags=(unconfined) { userns, }`, jak
profile rootlesskit, chrome czy code już obecne w /etc/apparmor.d. Sysctl i profil unprivileged_userns dla reszty
systemu zostają bez zmian. Koszt: dzieci bwrap mają uprawnienia w swojej przestrzeni użytkownika, więc każdy
program może przez bwrap obejść ograniczenie (tak samo jak dziś przez /usr/bin/rootlesskit).

Pierwsza wersja (28.10.2026) instalowała bwrap-userns-restrict z pakietu Ubuntu apparmor-profiles
(4.0.1really4.0.1-0ubuntu0.24.04.9): dzieci bwrap w stosie z unpriv_bwrap bez uprawnień. Claude Code 2.1.312
nie działa pod nim — jego pomocnik apply-seccomp zakłada w bwrap zagnieżdżoną przestrzeń użytkownika
i potrzebuje CAP_SYS_ADMIN („apply-seccomp: write /proc/self/setgroups (nested userns is capability-restricted;
caller must provide CAP_SYS_ADMIN)”, w dzienniku jądra DENIED capable sys_admin w profilu unpriv_bwrap),
a ustawienia allowNestedUserNamespaces i enableWeakerNestedSandbox nie pomagają.
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
# Profil z dokumentacji Claude Code (code.claude.com/docs/en/sandboxing, „Ubuntu 24.04 AppArmor and bubblewrap”):
# tylko /usr/bin/bwrap może zakładać przestrzenie użytkownika; reszta systemu zostaje pod ograniczeniem.

abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,

  # Site-specific additions and overrides. See local/README for details.
  include if exists <local/bwrap>
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
    base = [program, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-net", "--"]
    works, out = probe(base + ["cat", "/proc/self/attr/current"])
    # Zagnieżdżona przestrzeń z mapą użytkownika — jak apply-seccomp w piaskownicy Claude Code.
    nested, nested_out = probe(base + ["unshare", "-U", "-r", "true"]) if works else (False, "bwrap nie działa")
    return {"works": works, "label": out if works else None, "error": None if works else out,
            "nested": nested, "nested_error": None if nested else nested_out}


def others_restricted(restrict: int | None) -> bool | None:
    """Czy zwykły program zakładający przestrzeń użytkownika nadal trafia do unprivileged_userns.

    None także wtedy, gdy jądro w ogóle nie ma ograniczenia AppArmor (np. Fedora z SELinuksem): reguła
    „reszta systemu zostaje pod ograniczeniem” dotyczy tylko systemów, które je mają (Ubuntu, KDE neon)."""
    if restrict == 0:
        return False
    if restrict is None:
        return None
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
        "ok": bwrap["works"] and bwrap["nested"] and restricted is not False,
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
    if bwrap["works"]:
        lines.append("Zagnieżdżona przestrzeń użytkownika w bwrap (potrzebna piaskownicy Claude Code): "
                     + ("działa" if bwrap["nested"] else f"nie działa — {bwrap['nested_error']}"))
    lines.append({True: "Reszta systemu: nadal pod ograniczeniem (unshare -U → unprivileged_userns)",
                  False: "Reszta systemu: BEZ ograniczenia",
                  None: "Reszta systemu: nie udało się sprawdzić" if restrict is not None else
                  "Reszta systemu: nie dotyczy — jądro bez ograniczenia AppArmor (np. Fedora), profil niepotrzebny"}[
                     state["others_restricted"]])
    for path in state["conflicts"]:
        lines.append(f"Inny profil dla /usr/bin/bwrap: {path}")
    if state["ok"]:
        lines.append("Piaskownica może działać.")
    elif state["others_restricted"] is False:
        lines.append("Ograniczenie wyłączono dla całego systemu (sysctl) — Tide tego nie robi. Przywrócenie: "
                     "sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=1")
    elif state["profile"] == "brak" and not state["conflicts"]:
        lines.append("Dalej: sudo bash scripts/sandbox/install.sh")
    elif state["profile"] == "tide" and bwrap["works"]:
        lines.append("Profil Tide jest starszy albo nie pozwala dzieciom bwrap na zagnieżdżoną przestrzeń — "
                     "podmiana: sudo bash scripts/sandbox/install.sh")
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
