"""`tide box base|build|status|reset|destroy|approve|login` — pudełko projektu z wiersza poleceń.

`base` i `login` należą do obrazu bazowego (#16, `box/base.py`), `stop|start` doda #19, `run` — #28.
Kody wyjścia: 0 sukces, 1 błąd (profil, Incus, budowa), 2 zły argument, 3 odmowa (biegną kroki, pudełko niezgodne
albo nieaktualne w `status`). Opis: docs/tide-daemon.md (Budowa pudełka → CLI, Obraz bazowy Tide).
"""
import argparse
import json
import sys

from . import base as bbase
from . import builder as bb
from . import k3
from . import profile as bp
from . import settings as bset
from .incus import IncusError


def _parser():
    parser = argparse.ArgumentParser(prog="tide box", description="Pudełko projektu (Incus)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("base", help="buduje obraz bazowy Tide (agenci, proxy, czcionki) — make box-base")
    p = sub.add_parser("login", help="logowanie agentów bez K3 w pudełku projektu (K1, jednorazowo)")
    p.add_argument("slug", nargs="?")
    p = sub.add_parser("build", help="buduje pudełko z profilu w Tablicy")
    p.add_argument("slug")
    p = sub.add_parser("status", help="stan pudełka, hash i filtrowanie NIC")
    p.add_argument("slug")
    p.add_argument("--card", help="kopia pudełka dla karty (box: per-card)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("reset", help="przywraca migawkę clean (tylko bez kroków)")
    p.add_argument("slug")
    p.add_argument("--card")
    p = sub.add_parser("destroy", help="usuwa pudełko i jego kopie per karta")
    p.add_argument("slug")
    p.add_argument("--yes", action="store_true", help="potwierdzenie (wymagane)")
    p = sub.add_parser("approve", help="zatwierdza profil z pliku i zapisuje go w Tablicy")
    p.add_argument("slug")
    p.add_argument("--file", required=True)
    p.add_argument("--accept-red", action="append", default=[], metavar="POZYCJA")
    return parser


def format_status(st) -> str:
    state = st.state
    if state == "nieaktualny":
        state = "nieaktualny (do przebudowy)"
    if st.warning and st.state == "clean":
        state = "clean (zbudowane z ostrzeżeniem: verify nieudane)"
    lines = [f"pudełko: {st.box}", f"stan: {state}"]
    if st.journal_state != st.state:
        lines.append(f"stan w dzienniku: {st.journal_state}")
    lines.append(f"hash: {st.recipe_hash or '—'}")
    if st.profile_hash:
        lines.append(f"hash profilu: {st.profile_hash}")
    elif st.exists:
        lines.append("profil: brak zatwierdzonego profilu — pudełko nieużywalne (tide box approve …)")
    if st.diff:
        lines.append("różnice: " + ", ".join(st.diff))
    if st.exists:
        lines.append(f"filtrowanie NIC: {'tak' if st.nic_filtering else 'nie'}")
        if st.nic_detail:
            lines.append("  " + ", ".join(f"{k}={v or '—'}" for k, v in st.nic_detail.items()))
        lines.append(f"uruchomione: {'tak' if st.running else 'nie'}")
    if st.steps:
        lines.append("kroki w pudełku: " + ", ".join(st.steps))
    if st.error:
        lines.append(f"błąd: {st.error}")
    return "\n".join(lines)


def _settings(err):
    settings = bset.load()
    for warning in settings.warnings:
        print(f"tide box: {warning}", file=err)
    return settings


def run_base(base_builder, out) -> int:
    res = base_builder.build()
    print(f"obraz bazowy {res.image}: migawka {res.image}/clean i obraz {res.image}, czcionek {res.fonts}, "
          f"{res.duration:.1f} s", file=out)
    print(f"dziennik odmów proxy: {res.denied_log}", file=out)
    if res.login:
        # Brak logowania nie jest błędem budowy: logowanie K1 jest jednorazowe, w pudełku projektu.
        print(f"{bbase.LOGIN_HINT} <projekt> (bez K3: {', '.join(res.login)})", file=out)
    else:
        print("logowanie agentów niepotrzebne: claude i codex działają w pudełku przez K3", file=out)
    return 0


def main(argv=None, out=None, err=None, builder=None, base_builder=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if args.cmd == "base":
            if base_builder is not None:
                return run_base(base_builder, out)
            settings = _settings(err)
            service, own = bbase.ensure_proxy(settings)
            try:
                return run_base(bbase.BaseBuilder(settings, policy=service), out)
            finally:
                if own:
                    service.stop()
        if args.cmd == "login":
            settings = _settings(err)
            if not k3.login_programs():
                return bbase.login(settings, args.slug, out)
            service, own = bbase.ensure_proxy(settings)
            try:
                return bbase.login(settings, args.slug, out, policy=service)
            finally:
                if own:
                    service.stop()
    except (bb.BuildError, bp.ProfileError, IncusError, OSError, ValueError) as exc:
        print(f"tide box {args.cmd}: {exc}", file=err)
        return 1
    try:
        if args.cmd == "approve":
            with open(args.file, encoding="utf-8") as fh:
                text = fh.read(256 * 1024)
            bb.approve(args.slug, text, args.accept_red)
            print(f"profil {args.slug} zatwierdzony (box.approved source=cli): {bp.project_path(args.slug)}", file=out)
            return 0
        if builder is None:
            builder = bb.Builder(_settings(err))
        b = builder
        if args.cmd == "build":
            res = b.build(args.slug)
            print(f"pudełko {res.box}: migawka clean, hash {res.hash}, {res.duration:.1f} s", file=out)
            if res.warning:
                print("zbudowane z ostrzeżeniem — verify nieudane: " + "; ".join(res.failed_verify), file=out)
            return 0
        if args.cmd == "status":
            st = b.status(args.slug, args.card)
            print(json.dumps(st.to_dict(), ensure_ascii=False, indent=2) if args.json else format_status(st), file=out)
            return 0 if (st.usable or st.state == "brak") else 3
        if args.cmd == "reset":
            took = b.reset(args.slug, args.card)
            print(f"przywrócono migawkę clean ({took:.1f} s)", file=out)
            return 0
        if args.cmd == "destroy":
            if not args.yes:
                print("tide box destroy usuwa pudełko, jego kopie per karta i ich /home/agent — dodaj --yes", file=err)
                return 2
            gone = b.destroy(args.slug)
            print("usunięto: " + (", ".join(gone) or "nic (brak pudełek)"), file=out)
            return 0
    except bb.BoxBusy as exc:
        print(f"tide box {args.cmd}: {exc}", file=err)
        return 3
    except (bb.BuildError, bp.ProfileError, IncusError, OSError, ValueError) as exc:
        print(f"tide box {args.cmd}: {exc}", file=err)
        return 1
    return 2
