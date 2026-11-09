"""Sterownik odczytu historii kart dla audytu F7 (PT-295): jeden proces na jedną wersję kodu.

Uruchamia go wyłącznie historyaudit.py: `/usr/bin/python3 -I -B historyaudit_driver.py` z konfiguracją JSON
na stdin, raport JSON na stdout. Sterownik importuje `tide_daemon` tylko z drzewa `tree` (kod z `git archive`
dokładnego SHA), nie z repozytorium narzędzia; `-I` nie dokłada katalogu skryptu ani bieżącego do sys.path,
`-B` nie zapisuje `__pycache__` w drzewie.

Tryby:
- `candidate` — `trialhold.read_process_history` (ścisły odczyt bramki) na dzienniku tylko do odczytu i `fold`
  dokładnego SHA kandydata; dodatkowo `Journal.cards_complete()` do porównania z niezależną inwentaryzacją,
- `reference` — ścisłe parsowanie autorytatywnej kopii w tym pliku (wersja odniesienia może nie mieć
  `read_complete`) i `fold` wersji odniesienia.

Strażnik (audit hook) przed pierwszym importem kodu kart odrzuca każdą próbę zapisu pliku, zmiany systemu plików,
procesu potomnego, połączenia gniazda (D-Bus, Incus, sieć) i importu bibliotek D-Bus/Qt. Próba trafia do raportu
(`forbidden`) i kończy odczyt tej karty błędem. Metody zapisu dziennika są dodatkowo zastąpione atrapami.

Moduł nie importuje `tide_daemon` na poziomie modułu: `plain()` i `strict_events()` importuje też historyaudit.py.
"""
import dataclasses
import hashlib
import json
import os
import stat
import sys

SCHEMA = 1
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
BLOCKED_EVENTS = ("os.remove", "os.rename", "os.replace", "os.mkdir", "os.rmdir", "os.chmod", "os.chown",
                  "os.utime", "os.truncate", "os.symlink", "os.link", "os.chflags", "os.lchflags", "os.lchmod",
                  "os.lchown", "shutil.rmtree", "shutil.move", "shutil.copyfile", "shutil.copytree",
                  "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
                  "os.forkpty", "pty.spawn", "socket.connect", "socket.bind", "socket.sendto", "os.kill",
                  "os.killpg", "signal.pthread_kill", "ctypes.dlopen", "os.putenv", "os.unsetenv",
                  "os.setxattr", "os.removexattr", "os.mkfifo", "os.mknod")
BLOCKED_IMPORTS = ("dbus", "jeepney", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "pydbus", "sdbus")


def plain(value):
    """Wartość stanu jako JSON bez utraty rozróżnień: klucze niebędące tekstem jako `json:<…>`, zbiory posortowane,
    dataclass jako słownik pól (z `held`, gdy obiekt ma tę właściwość). Nieznany typ jest oznaczony, nie pomijany."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
        if isinstance(getattr(type(value), "held", None), property):
            out["held"] = plain(value.held)
        return out
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            name = key if isinstance(key, str) else "json:" + json.dumps(plain(key), sort_keys=True,
                                                                         ensure_ascii=False)
            out[name] = plain(item)
        return dict(sorted(out.items()))
    if isinstance(value, (set, frozenset)):
        items = [plain(v) for v in value]
        return {"__set__": sorted(items, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return {"__unknown__": type(value).__name__, "repr": repr(value)[:200]}


def digest(events):
    """Odcisk zdarzeń w kolejności (kanoniczny JSON pełnych pól); ten sam co rollback.digest."""
    h = hashlib.sha256()
    for e in events:
        h.update(json.dumps(e, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
        h.update(b"\n")
    return h.hexdigest()


def authoritative(board, mirror, card):
    """Ścieżka kopii, którą czyta usługa: istniejący wpis mirrora wygrywa (także nieczytelny)."""
    path = os.path.join(mirror, card + ".jsonl")
    try:
        os.lstat(path)
        return path, "mirror"
    except FileNotFoundError:
        return os.path.join(board, "flow", card, "journal.jsonl"), "board"


def strict_events(data, card):
    """Ścisłe zdarzenia z bajtów pliku: każda linia pełna, JSON-obiekt, v=1, seq 1..n, ta sama karta, typ."""
    text = data.decode("utf-8")
    events = []
    for line in text.splitlines(keepends=True):
        if not line.endswith("\n"):
            raise ValueError("niepełny zapis zdarzenia")
        event = json.loads(line)
        if (not isinstance(event, dict) or type(event.get("v")) is not int or event["v"] != SCHEMA
                or type(event.get("seq")) is not int or event["seq"] != len(events) + 1
                or event.get("card") != card or not isinstance(event.get("type"), str) or not event["type"]):
            raise ValueError("niepełna lub nieznana historia zdarzeń")
        events.append(event)
    return events


def read_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise ValueError("dziennik nie jest zwykłym plikiem")
        return fh.read()


class Forbidden(PermissionError):
    pass


def install_guard(forbidden):
    """Audit hook procesu sterownika. Od tej chwili jedyne dozwolone skutki to odczyt i stdout."""
    def hook(event, args):
        reason = ""
        if event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                    (isinstance(flags, int) and flags & WRITE_FLAGS):
                reason = f"open do zapisu: {path}"
        elif event == "import":
            name = str(args[0] if args else "")
            if name.split(".")[0] in BLOCKED_IMPORTS:
                reason = f"import {name}"
        elif event in BLOCKED_EVENTS or event.startswith(("os.exec", "os.spawn", "os.posix_spawn")):
            reason = f"{event}: {repr(args)[:200]}"
        if reason:
            forbidden.append(reason)
            raise Forbidden("operacja zabroniona w audycie tylko do odczytu: " + reason)
    sys.addaudithook(hook)


def _deny_writes(cls, forbidden, names):
    for name in names:
        if hasattr(cls, name):
            def denied(*_args, _name=name, **_kwargs):
                forbidden.append(f"{cls.__name__}.{_name}")
                raise Forbidden(f"zapis dziennika zabroniony w audycie: {_name}")
            setattr(cls, name, denied)


class ReadOnlyJournal:
    """Dziennik dla read_process_history: tylko `read_complete` i `cards_complete`, reszta to zgłoszona odmowa."""

    def __init__(self, journal, forbidden):
        self._journal, self._forbidden = journal, forbidden
        self.root, self.mirror, self.lock = journal.root, journal.mirror, journal.lock

    def read_complete(self, card):
        return self._journal.read_complete(card)

    def cards_complete(self):
        return self._journal.cards_complete()

    def __getattr__(self, name):
        self._forbidden.append(f"journal.{name}")
        raise Forbidden(f"dziennik audytu udostępnia tylko odczyt, nie {name}")


DERIVED = ("running", "open_consoles", "open_intents", "merge_pending", "busy_reason", "flow_error",
           "tracker_failures")


def derived(st):
    """Widoki liczone metodami stanu: kroki bez końca, otwarte Konsole i akcje, scalenia, powód zajętości."""
    calls = {
        "running": lambda: sorted(s.get("step") or "" for s in st.running()),
        "open_consoles": lambda: sorted(s.get("step") or "" for s in st.open_consoles()),
        "open_intents": lambda: sorted([i.get("action") or "", str(i.get("key") or "")]
                                       for i in st.open_intents(include_background=True)),
        "merge_pending": lambda: sorted(s.get("step") or "" for s in st.merge_pending()),
        "busy_reason": lambda: st.busy_reason(),
        "flow_error": lambda: st.flow_error,
        "tracker_failures": lambda: len(st.tracker_failures()),
    }
    out = {}
    for name in DERIVED:
        try:
            out[name] = plain(calls[name]())
        except Exception as exc:  # brak metody w starszej wersji też jest wynikiem do porównania
            out[name] = {"__error__": f"{type(exc).__name__}: {exc}"}
    return out


def run(cfg):
    forbidden = []
    tree = os.path.realpath(cfg["tree"])
    sys.path.insert(0, os.path.join(tree, "daemon"))
    for name in [m for m in sys.modules if m == "tide_daemon" or m.startswith("tide_daemon.")]:
        del sys.modules[name]
    install_guard(forbidden)
    report = {"mode": cfg["mode"], "tree": tree, "cards": {}, "forbidden": forbidden,
              "python": sys.version.split()[0]}
    import tide_daemon
    from tide_daemon.cardflow import state as state_module
    report["module"] = os.path.realpath(tide_daemon.__file__)
    report["fields"] = [f.name for f in dataclasses.fields(state_module.FlowState)]
    journal = None
    if cfg["mode"] == "candidate":
        from tide_daemon.cardflow import journal as journal_module
        from tide_daemon.cardflow import trialhold
        from tide_daemon.cardflow import processidentity
        report["startFields"] = list(processidentity.START_FIELDS)
        _deny_writes(journal_module.Journal, forbidden,
                     ("append", "append_complete", "_append", "check", "_restore", "write_file", "remove_file",
                      "take_dirty"))
        if hasattr(journal_module, "_write"):
            def denied_write(*_args, **_kwargs):
                forbidden.append("journal._write")
                raise Forbidden("zapis pliku dziennika zabroniony w audycie")
            journal_module._write = denied_write
        journal = ReadOnlyJournal(journal_module.Journal(cfg["board"], mirror=cfg["mirror"]), forbidden)
        try:
            report["registry"] = journal.cards_complete()
        except Exception as exc:
            report["registry"] = {"__error__": f"{type(exc).__name__}: {exc}"}
    for card in cfg["cards"]:
        before = len(forbidden)
        item = {"ok": False}
        try:
            if journal is not None:
                events = trialhold.read_process_history(journal, card)
            else:
                path, _source = authoritative(cfg["board"], cfg["mirror"], card)
                events = strict_events(read_bytes(path), card)
            item["events"], item["digest"] = len(events), digest(events)
        except Exception as exc:
            item["read"] = f"{type(exc).__name__}: {exc}"
            item["forbidden"] = forbidden[before:]
            report["cards"][card] = item
            continue
        try:
            st = state_module.fold(events)
            item["state"] = {f.name: plain(getattr(st, f.name)) for f in dataclasses.fields(st)}
            item["derived"] = derived(st)
            item["ok"] = True
        except Exception as exc:
            item["fold"] = f"{type(exc).__name__}: {exc}"
        item["forbidden"] = forbidden[before:]
        if item["forbidden"]:
            item["ok"] = False
        report["cards"][card] = item
    return report


def main():
    cfg = json.loads(sys.stdin.read())
    report = run(cfg)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
