#!/usr/bin/python3
"""CLI Tide: wyłącznie klient publicznego D-Bus, bez zależności od checkoutu."""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

SERVICE = "org.tide.Daemon"
PATH = "/Issues"
IFACE = "org.tide.Daemon.Issues"
FILE_MAX = 20 * 1024 * 1024
EXIT_CODES = {"bad-request": 2, "unavailable": 3, "api-incompatible": 3,
              "conflict": 4, "version-conflict": 4, "not-found": 5,
              "git-busy": 6, "io-error": 7, "broken-file": 7}


class CliError(Exception):
    def __init__(self, code, message):
        self.reply = {"ok": False, "apiVersion": 1, "error": code, "message": message}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError("bad-request", "Niepoprawne argumenty polecenia: " + message)


def parser():
    root = Parser(description="Tide — szkice spraw przez publiczną usługę.")
    root.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="wynik JSON")
    commands = root.add_subparsers(dest="command", required=True, parser_class=Parser)
    info = commands.add_parser("info", help="wersja i możliwości API")
    next_id = commands.add_parser("next-id", help="rezerwacja numeru ticketu przez zainstalowane narzędzie Tide")
    next_id.add_argument("repo")
    next_id.add_argument("--prefix", default="")
    next_id.add_argument("--count", type=int, default=1)
    next_id.add_argument("--title", default="")
    next_id.add_argument("--dry-run", action="store_true")
    check = commands.add_parser("check-plans", help="kontrola planów przez zainstalowane narzędzie Tide")
    check.add_argument("workspace", nargs="?", default=os.path.expanduser("~/DevWorkspace"))
    check.add_argument("--no-youtrack", action="store_true")
    check.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    issue = commands.add_parser("issue", help="sprawy")
    actions = issue.add_subparsers(dest="action", required=True, parser_class=Parser)
    create = actions.add_parser("create", help="zapisz szkic")
    create.add_argument("--report", required=True, help="plik UTF-8 Markdown bez frontmatteru")
    create.add_argument("--title", required=True, help="tytuł sprawy")
    create.add_argument("--project", help="slug projektu Tablicy")
    create.add_argument("--request-id", help="trwały klucz ponowienia")
    listing = actions.add_parser("list", help="lista spraw")
    show = actions.add_parser("show", help="treść i metadane sprawy")
    show.add_argument("id", help="S-N")
    opening = actions.add_parser("open", help="otwórz sprawę w oknie Tide")
    opening.add_argument("id", help="S-N")
    update = actions.add_parser("update", help="popraw szkic; zmiana ready wraca do draft")
    update.add_argument("id", help="S-N")
    update.add_argument("--expected-version", required=True, type=int)
    update.add_argument("--report", help="plik UTF-8 Markdown bez frontmatteru")
    update.add_argument("--title", help="tytuł sprawy")
    project = update.add_mutually_exclusive_group()
    project.add_argument("--project", help="slug projektu Tablicy")
    project.add_argument("--clear-project", action="store_true", help="usuń przypisanie projektu")
    transitions = []
    for action, help_text in (("submit", "przygotuj sprawę do przyjęcia"),
                              ("reject", "odrzuć sprawę"), ("reopen", "otwórz odrzuconą sprawę jako szkic")):
        command = actions.add_parser(action, help=help_text)
        command.add_argument("id", help="S-N")
        command.add_argument("--expected-version", required=True, type=int)
        transitions.append(command)
    attach = actions.add_parser("attach", help="dołącz trwałą kopię jawnie wskazanego pliku")
    attach.add_argument("id", help="S-N")
    attach.add_argument("--file", required=True)
    attach.add_argument("--expected-version", required=True, type=int)
    detach = actions.add_parser("detach", help="odłącz załącznik od sprawy")
    detach.add_argument("id", help="S-N")
    detach.add_argument("--attachment", required=True)
    detach.add_argument("--expected-version", required=True, type=int)
    attachment = actions.add_parser("attachment", help="zapisz bajty załącznika w nowym pliku")
    attachment.add_argument("id", help="S-N")
    attachment.add_argument("--attachment", required=True)
    attachment.add_argument("--output", required=True)
    attachment.add_argument("--expected-version", type=int, help="domyślnie wersja pobrana przez Show")
    for command in (info, issue, create, listing, show, opening, update, attach, detach, attachment, *transitions):
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="wynik JSON")
    return root


class PublicApi:
    def __init__(self):
        try:
            from PyQt6.QtCore import QCoreApplication
            from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
        except ImportError:
            raise CliError("unavailable", "CLI wymaga pakietu python3-pyqt6.qtdbus.") from None
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.bus = QDBusConnection.sessionBus()
        self.QDBus, self.QDBusMessage = QDBus, QDBusMessage
        if not self.bus.isConnected():
            raise CliError("unavailable", "Brak połączenia z magistralą sesji D-Bus.")

    def call(self, method, *args):
        message = self.QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, method)
        message.setArguments(list(args))
        reply = self.bus.call(message, self.QDBus.CallMode.Block, 30000)
        if reply.type() == self.QDBusMessage.MessageType.ErrorMessage:
            incompatible = reply.errorName().endswith(("UnknownMethod", "UnknownInterface", "UnknownObject"))
            raise CliError("api-incompatible" if incompatible else "unavailable",
                           "Usługa nie udostępnia API spraw v1." if incompatible else
                           "Usługa Tide jest niedostępna albo nie odpowiedziała; ponów z tym samym request-id.")
        try:
            values = reply.arguments()
            read = method == "ReadAttachment"
            result = json.loads(values[0]) if len(values) == (2 if read else 1) and isinstance(values[0], str) else None
            if (not isinstance(result, dict) or type(result.get("apiVersion")) is not int or
                    result["apiVersion"] != 1 or type(result.get("ok")) is not bool):
                raise ValueError()
        except (ValueError, TypeError):
            raise CliError("api-incompatible", "Nieprawidłowa odpowiedź API spraw v1.") from None
        if read:
            from PyQt6.QtCore import QByteArray
            if not isinstance(values[1], QByteArray) or len(values[1]) > FILE_MAX or (not result["ok"] and values[1]):
                raise CliError("api-incompatible", "Nieprawidłowe bajty odpowiedzi ReadAttachment.")
            return result, bytes(values[1])
        return result

    def open_window(self, ident):
        message = self.QDBusMessage.createMethodCall("org.tide.Window", "/Window", "org.tide.Window", "Show")
        message.setArguments(["issues", ident])
        reply = self.bus.call(message, self.QDBus.CallMode.Block, 5000)
        if reply.type() == self.QDBusMessage.MessageType.ReplyMessage and reply.arguments() == [True]:
            return
        # Ten sam zainstalowany launcher co pozostałe wejścia do jednego okna Tide.
        path = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "tide/tide-window/tide.py"
        if not path.is_file():
            raise CliError("unavailable", "Okno Tide nie jest zainstalowane.")
        try:
            subprocess.Popen(["/usr/bin/python3", "-s", str(path), "--view", "issues", "--select", ident],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise CliError("unavailable", "Nie można uruchomić okna Tide.") from None


def read_attachment_source(path):
    # Każdy komponent otwieramy bez dowiązań; FIFO i urządzenia nigdy nie są czytane.
    if not path or "://" in path or "\x00" in path or ".." in Path(path).parts:
        raise CliError("bad-request", "Podaj lokalny plik bez URL i przejść '..'.")
    target = Path(path).absolute()
    fd = None
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for part in target.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        source = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > FILE_MAX:
                raise CliError("bad-request", "Załącznik musi być zwykłym plikiem do 20 MiB.")
            data = stream.read(FILE_MAX + 1)
            stream.seek(0)
            digest = hashlib.sha256(stream.read(FILE_MAX + 1)).digest()
            after = os.fstat(stream.fileno())
            current = os.stat(target.name, dir_fd=fd, follow_symlinks=False)
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if (len(data) > FILE_MAX or signature(before) != signature(after) or
                    signature(before) != signature(current) or hashlib.sha256(data).digest() != digest):
                raise CliError("bad-request", "Źródło zmieniło się podczas kopiowania; ponów import.")
        return target.name, data
    except OSError as exc:
        raise CliError("bad-request", f"Nie można importować pliku: {exc.strerror}.") from None
    finally:
        if fd is not None:
            os.close(fd)


def write_attachment(path, result, data):
    entry = result.get("attachment", {})
    if len(data) != entry.get("size") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        raise CliError("api-incompatible", "Bajty odpowiedzi są niezgodne z metadanymi.")
    if not path or "\x00" in path:
        raise CliError("bad-request", "Podaj poprawną ścieżkę nowego pliku wyjściowego.")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            os.unlink(path)
            raise
    except OSError as exc:
        raise CliError("io-error", f"Nie można utworzyć nowego pliku wyjściowego: {exc.strerror}.") from None


def read_report(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CliError("bad-request", "Raport musi być zwykłym plikiem.")
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise CliError("bad-request", "Raport przekracza 64 KiB.")
        return raw.decode("utf-8")
    except UnicodeError:
        raise CliError("bad-request", "Raport nie jest poprawnym UTF-8.") from None
    except OSError as exc:
        raise CliError("io-error", f"Nie można odczytać raportu: {exc.strerror}.") from None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    as_json = "--json" in argv
    try:
        args = parser().parse_args(argv)
        api = PublicApi()
        result = api.call("Info")
        if result["ok"] and args.command in ("next-id", "check-plans"):
            asset = result.get("resources", {}).get(args.command, {})
            if not asset.get("available") or not os.path.isfile(asset.get("path", "")):
                raise CliError("unavailable", "Brak zainstalowanego narzędzia Tide; sprawdź tide info --json "
                               "i instalację zasobów (TIDE_RESOURCES_DIR w środowisku usługi).")
            module_path = asset.get("pythonPath", "")
            if not module_path or not os.path.isdir(os.path.join(module_path, "tide_daemon")):
                raise CliError("unavailable", "Brak modułów narzędzia Tide; sprawdź instalację usługi "
                               "i pythonPath w tide info --json.")
            if args.command == "next-id":
                forwarded = [args.repo, "--prefix", args.prefix, "--count", str(args.count), "--title", args.title]
                if args.dry_run:
                    forwarded.append("--dry-run")
            else:
                forwarded = [args.workspace]
                if args.no_youtrack:
                    forwarded.append("--no-youtrack")
                if as_json:
                    forwarded.append("--json")
            try:
                env = {**os.environ, "PYTHONPATH": module_path}
                return subprocess.run([sys.executable, asset["path"], *forwarded],
                                      env=env, check=False).returncode
            except OSError:
                raise CliError("unavailable", "Nie można uruchomić narzędzia ze zainstalowanych zasobów Tide.") from None
        if result["ok"] and args.command != "info":
            capability = "show" if args.action == "open" else args.action
            if f"issue.{capability}" not in result.get("capabilities", []):
                raise CliError("api-incompatible", "Usługa nie udostępnia wymaganego polecenia API v1.")
            if args.action == "open":
                if not re.fullmatch(r"S-[1-9][0-9]{0,8}", args.id):
                    raise CliError("bad-request", "Podaj numer sprawy S-N.")
                result = api.call("Show", args.id)
                if result["ok"]:
                    api.open_window(args.id)
            elif args.action == "create":
                data = {"report": read_report(args.report), "title": args.title}
                if args.project is not None:
                    data["project"] = args.project
                if args.request_id is not None:
                    data["requestId"] = args.request_id
                result = api.call("Create", json.dumps(data, ensure_ascii=False))
            elif args.action == "list":
                result = api.call("List")
            elif args.action == "attach":
                from PyQt6.QtCore import QByteArray
                name, raw = read_attachment_source(args.file)
                result = api.call("Attach", args.id, json.dumps({"name": name, "expectedVersion": args.expected_version}),
                                  QByteArray(raw))
            elif args.action == "detach":
                result = api.call("Detach", args.id, json.dumps({"attachmentId": args.attachment,
                                                                 "expectedVersion": args.expected_version}))
            elif args.action == "attachment":
                version = args.expected_version
                if version is None:
                    result = api.call("Show", args.id)
                    if result["ok"]:
                        version = result["issue"]["version"]
                if result["ok"]:
                    result, raw = api.call("ReadAttachment", args.id, json.dumps({"attachmentId": args.attachment,
                                                                                "expectedVersion": version}))
                    if result["ok"]:
                        write_attachment(args.output, result, raw)
            elif args.action in ("update", "submit", "reject", "reopen"):
                data = {"expectedVersion": args.expected_version}
                if args.action == "update":
                    if args.report is not None:
                        data["report"] = read_report(args.report)
                    if args.title is not None:
                        data["title"] = args.title
                    if args.project is not None or args.clear_project:
                        data["project"] = None if args.clear_project else args.project
                result = api.call(args.action.capitalize(), args.id, json.dumps(data, ensure_ascii=False))
            else:
                result = api.call("Show", args.id)
    except CliError as exc:
        result = exc.reply
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif not result["ok"]:
        print(result.get("message", "Błąd usługi Tide."), file=sys.stderr)
    elif "issue" in result:
        issue = result["issue"]
        print(f"{issue['id']}: {issue['title']} (wersja {issue['version']})")
        if args.action == "show":
            print(issue["report"], end="")
    elif "issues" in result:
        for issue in result["issues"]:
            print(f"{issue['id']}: {issue['title']}")
        for error in result.get("errors", []):
            print(error["message"], file=sys.stderr)
    elif "attachment" in result:
        print(f"{result['id']}: zapisano {result['attachment']['name']} do {args.output}")
    else:
        print(f"Tide API v{result['apiVersion']}")
    if result.get("commitError") and not as_json:
        print("Zapisano sprawę; commit czeka w kolejce: " + result["commitError"], file=sys.stderr)
    return 0 if result["ok"] else EXIT_CODES.get(result.get("error"), 1)


if __name__ == "__main__":
    sys.exit(main())
