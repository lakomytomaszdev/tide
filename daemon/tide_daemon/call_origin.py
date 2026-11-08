"""Pochodzenie wywołania D-Bus (K-69, PT-286): czy nadawca jest procesem kroku agenta.

Prawa 0600 magazynu odcisków nie odróżniają usługi od kroku tego samego użytkownika, a martwy
`DBUS_SESSION_BUS_ADDRESS` z `agentcmd._env` krok może nadpisać prawdziwym adresem. Dlatego zatwierdzenie
kontraktu testów (`UpsertProject` z polem `tests`) sprawdza nadawcę:

- PID nadawcy daje magistrala (`org.freedesktop.DBus.GetConnectionUnixProcessID` — w usłudze
  `QDBusConnectionInterface.servicePid`); brak PID (0, błąd, `None`) → odmowa,
- `/proc/<pid>/cgroup` nadawcy: proces w jednostce kroku (`tide-step-<id>.scope` z `Background.start`, także
  w podgrupie tej jednostki) → odmowa; nieczytelny lub pusty plik → odmowa.

Każda niepewność to odmowa (zasada zachowawcza). `proc_root` podmieniają tylko testy (atrapa `/proc`).
Ograniczenie: PID ustalony przez magistralę dotyczy procesu, który otworzył połączenie; proces przeniesiony
poza jednostkę kroku (np. `systemd-run --user` z wnętrza kroku) nie jest tu rozpoznany — tę drogę zamyka
piaskownica kroku (próby rzeczywistej piaskownicy w notce PT-286 i w docs/tide-daemon.md).
"""
import os
import re

STEP_UNIT_PREFIX = "tide-step-"
_STEP_SEGMENT = re.compile(r"^tide-step-[^/]*$")
MAX_CGROUP_BYTES = 64 * 1024


def step_unit(pid, proc_root: str = "/proc") -> str | None:
    """Nazwa jednostki kroku z cgroup procesu `pid` (pierwszy segment `tide-step-*`), "" — proces poza krokiem,
    None — nie da się ustalić (zły PID, brak procesu, nieczytelny albo pusty plik)."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        with open(os.path.join(proc_root, str(pid), "cgroup"), "rb") as fh:
            data = fh.read(MAX_CGROUP_BYTES + 1)
    except OSError:
        return None
    if not data or len(data) > MAX_CGROUP_BYTES:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    paths = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[2].startswith("/"):
            return None   # nieznany format — niepewność
        paths.append(parts[2])
    if not paths:
        return None
    for path in paths:
        for segment in path.split("/"):
            if _STEP_SEGMENT.match(segment):
                return segment
    return ""


def refusal(pid, proc_root: str = "/proc") -> str:
    """Powód odrzucenia zatwierdzenia od nadawcy `pid` albo "" — nadawca spoza kroku agenta."""
    if type(pid) is not int or pid <= 0:
        return ("nie da się ustalić procesu nadawcy wywołania — zatwierdzenie kontraktu testów odrzucone "
                "(zatwierdź w oknie projektu Tablicy)")
    unit = step_unit(pid, proc_root)
    if unit is None:
        return (f"nie da się ustalić jednostki procesu nadawcy (PID {pid}) — zatwierdzenie kontraktu testów "
                "odrzucone (zatwierdź w oknie projektu Tablicy)")
    if unit:
        return (f"zatwierdzenie kontraktu testów z kroku agenta ({unit}) jest niedozwolone — kontrakt zatwierdza "
                "użytkownik w oknie projektu Tablicy")
    return ""
