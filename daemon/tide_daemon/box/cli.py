"""Klient usługi: `daemon/tide.py box status|stop`; bez lokalnego zapisu i Incusa.

Kody: 0 sukces, 1 błąd usługi/transportu, 2 argumenty, 3 odmowa lub nieużywalne pudełko.
Pozostałe polecenia czekają na etapy II/III (PT-268/PT-276).
"""
import argparse
import json
import sys

from .client import ServiceError, call

DISABLED = {"base", "build", "reset", "start", "destroy", "approve", "login", "run"}
DISABLED_MESSAGE = ("polecenie wyłączone do wdrożenia przez usługę (etapy II/III, PT-268/PT-276); "
                    "dostępne: daemon/tide.py box status|stop. Brak lokalnego wykonania.")


def _parser():
    parser = argparse.ArgumentParser(prog="tide box", description="Pudełko projektu przez usługę D-Bus Tide")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for command, help_text in (("status", "stan pudełka z usługi"), ("stop", "zatrzymuje bezczynne pudełko przez usługę")):
        p = sub.add_parser(command, help=help_text)
        p.add_argument("slug")
        p.add_argument("--card", default="", help="kopia pudełka dla karty K-N")
        if command == "status":
            p.add_argument("--json", action="store_true")
    return parser


def format_status(st) -> str:
    state = st["state"]
    if state == "nieaktualny":
        state = "nieaktualny (do przebudowy)"
    if st.get("warning") and state == "clean":
        state = "clean (zbudowane z ostrzeżeniem: verify nieudane)"
    lines = [f"pudełko: {st['box']}", f"stan: {state}"]
    if st.get("journalState") != st["state"]:
        lines.append(f"stan w dzienniku: {st.get('journalState', '—')}")
    lines.append(f"hash: {st.get('recipeHash') or '—'}")
    if st.get("profileHash"):
        lines.append(f"hash profilu: {st['profileHash']}")
    elif st.get("exists"):
        lines.append("profil: brak zatwierdzonego profilu — pudełko nieużywalne")
    if st.get("diff"):
        lines.append("różnice: " + ", ".join(st["diff"]))
    if st.get("exists"):
        lines.append("filtrowanie NIC: " + ("tak" if st.get("nicFiltering") else "nie"))
        if st.get("nic"):
            lines.append("  " + ", ".join(f"{k}={v or '—'}" for k, v in st["nic"].items()))
        lines.append("uruchomione: " + ("tak" if st.get("running") else "nie"))
    if st.get("steps"):
        lines.append("kroki w pudełku: " + ", ".join(st["steps"]))
    if st.get("error"):
        lines.append(f"błąd: {st['error']}")
    for event in st.get("timings", []):
        lines.append(f"{event['type']}: {event['duration']:.3f} s ({event.get('reason') or 'użytkownik'})")
    return "\n".join(lines)


def main(argv=None, out=None, err=None, request=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in DISABLED:
        print(f"tide box {argv[0]}: {DISABLED_MESSAGE}", file=err)
        return 3
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if args.cmd == "status":
            member, params = ("BoxCardStatus", (args.slug, args.card)) if args.card else ("BoxStatus", (args.slug,))
        else:
            member, params = "StopBox", (args.slug, args.card)
        result = (request or call)(member, *params)
        if result.get("ok") is not True:
            print(f"tide box {args.cmd}: {result.get('message') or 'usługa odmówiła operacji'}", file=err)
            return 3 if result.get("error") in ("box-busy", "unknown-box-owner", "box-scope") else 1
        if args.cmd == "status":
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else format_status(result), file=out)
            if result.get("error"):
                return 1
            return 0 if result.get("usable") or result["state"] == "brak" else 3
        print(f"stop: {result['slug']} ({result['duration']:.3f} s)", file=out)
        return 0
    except (ServiceError, ValueError, KeyError, TypeError) as exc:
        print(f"tide box {args.cmd}: {exc}", file=err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
