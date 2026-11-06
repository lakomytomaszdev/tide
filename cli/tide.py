#!/usr/bin/python3
"""CLI Tide: wyłącznie klient publicznego D-Bus, bez zależności od checkoutu."""
import argparse
import json
import os
import stat
import subprocess
import sys

SERVICE = "org.tide.Daemon"
PATH = "/Issues"
IFACE = "org.tide.Daemon.Issues"
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
    for command in (info, issue, create, listing, show):
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
            result = json.loads(values[0]) if len(values) == 1 and isinstance(values[0], str) else None
            if (not isinstance(result, dict) or type(result.get("apiVersion")) is not int or
                    result["apiVersion"] != 1 or type(result.get("ok")) is not bool):
                raise ValueError()
        except (ValueError, TypeError):
            raise CliError("api-incompatible", "Nieprawidłowa odpowiedź API spraw v1.") from None
        return result


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
            if f"issue.{args.action}" not in result.get("capabilities", []):
                raise CliError("api-incompatible", "Usługa nie udostępnia wymaganego polecenia API v1.")
            if args.action == "create":
                data = {"report": read_report(args.report), "title": args.title}
                if args.project is not None:
                    data["project"] = args.project
                if args.request_id is not None:
                    data["requestId"] = args.request_id
                result = api.call("Create", json.dumps(data, ensure_ascii=False))
            elif args.action == "list":
                result = api.call("List")
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
    else:
        print(f"Tide API v{result['apiVersion']}")
    if result.get("commitError") and not as_json:
        print("Zapisano sprawę; commit czeka w kolejce: " + result["commitError"], file=sys.stderr)
    return 0 if result["ok"] else EXIT_CODES.get(result.get("error"), 1)


if __name__ == "__main__":
    sys.exit(main())
