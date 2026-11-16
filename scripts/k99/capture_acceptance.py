#!/usr/bin/env python3
"""Zapis prawdziwych danych do odbioru rdzenia K-99 (PT-471) — wyłącznie odczyt.

Co robi:

- mapa gałęzi projektu liczona kodem TEGO worktree: `GitMonitor.branch_map` (ta sama pula co obsługa D-Bus) nad
  dziennikiem kart i Tablicą otwartymi tylko do odczytu (`Journal` bez zapisu, Tablica z migawki usługi `Snapshot`;
  `BoardStore.load` nie jest używany, bo zakłada blokadę i może odtwarzać pliki). Zainstalowana usługa nie ma
  `Git.BranchMap` i nigdy nie jest o nią pytana;
- z działającej usługi `/Board` wołane są wyłącznie `Snapshot`, `ListCardBranches` i `PreviewCardMerge`
  (`BusClient` odrzuca każdą inną metodę, zanim cokolwiek wyśle); brak metody w introspekcji to błąd z opisem;
- karta do okna scalania wybierana regułą ustaloną z góry (`choose_card`), wynik reguły w `capture.json.selection`;
- zapis do `--out`: `branchmap.json`, `list_card_branches.json`, `preview_card_merge.json`, `capture.json`;
  ścieżki z katalogu domowego zamienione na `<HOME>` i `<WORKTREES>`.

Skrypt nie woła fetch, nie zapisuje Tablicy, dzienników ani repozytoriów (`git status` z `--no-optional-locks`
przed i po). Kody wyjścia: 0 — zapisano; 1 — błąd (usługa, brak metody, zła odpowiedź); 4 — mapa ma mniej gałęzi
niż wymagane (nic nie zapisano, decyzja o innym projekcie należy do planu).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

SERVICE, BOARD_PATH, BOARD_IFACE = "org.tide.Daemon", "/Board", "org.tide.Daemon.Board"
READ_ONLY_CALLS = frozenset({"Snapshot", "ListCardBranches", "PreviewCardMerge"})
PREFERRED_CARD = "K-3"
FILE_THRESHOLD = 100
MIN_BRANCHES = 40
DEFAULT_PROJECT = "plasma-tide"
DEFAULT_CARD_BRANCH = "card/k-99-git-tide-mapa-galezi"
REPLY_TIMEOUT = 300.0
EXIT_TOO_SMALL = 4


class CaptureError(Exception):
    pass


class TooSmall(CaptureError):
    pass


class ForbiddenCall(CaptureError):
    pass


# --- klient D-Bus (tylko odczyt) -------------------------------------------------------------------


class BusClient:
    """`busctl --user` z białą listą metod. `runner(argv) -> (kod, stdout, stderr)` jest podmieniane w testach."""

    def __init__(self, runner=None, timeout=120):
        self.runner = runner or self._run
        self.timeout = timeout
        self.calls = []  # [(metoda, karta albo "")] — dowód, co poszło do usługi

    @staticmethod
    def _run(argv):
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
        return done.returncode, done.stdout, done.stderr

    def _busctl(self, *args, json_output=True):
        flags = ["--json=short"] if json_output else []
        code, out, err = self.runner(["busctl", "--user", *flags, f"--timeout={self.timeout}", *args])
        if code != 0:
            raise CaptureError(f"busctl {args[0]} nie powiodło się ({code}): {err.strip()[:300]}")
        return out

    def methods(self) -> set:
        """Nazwy metod `/Board` z introspekcji. `busctl introspect` nie ma wyjścia JSON — czytamy tabelę:
        `.NazwaMetody  method  <sygnatura>  <wynik>  <flagi>`."""
        out = self._busctl("introspect", SERVICE, BOARD_PATH, BOARD_IFACE, json_output=False)
        found = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith(".") and parts[1] == "method":
                found.add(parts[0][1:])
        return found

    def call(self, member: str, *args: str) -> dict:
        if member not in READ_ONLY_CALLS:
            raise ForbiddenCall(f"metoda {member} nie jest na liście odczytów tego skryptu")
        self.calls.append((member, args[0] if args else ""))
        signature = "s" * len(args)
        out = self._busctl("call", SERVICE, BOARD_PATH, BOARD_IFACE, member, *([signature, *args] if args else []))
        try:
            text = json.loads(out)["data"][0]
            reply = json.loads(text)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise CaptureError(f"{member}: odpowiedź usługi nie ma oczekiwanego kształtu ({exc})") from None
        if not isinstance(reply, dict):
            raise CaptureError(f"{member}: odpowiedź usługi nie jest obiektem JSON")
        return reply


def require_methods(client) -> None:
    missing = sorted(READ_ONLY_CALLS - client.methods())
    if missing:
        raise CaptureError("usługa nie ma metod /Board: " + ", ".join(missing) + " (brak obejścia; sprawdź, "
                           "czy działa usługa Tide i czy jest zainstalowana wersja z K-50)")


# --- mapa liczona kodem worktree ----------------------------------------------------------------------


def board_view_from_snapshot(snapshot: dict) -> dict:
    """Ten sam kształt co `branchmap.board_view`, ale z migawki usługi (bez dotykania Tablicy)."""
    return {"projects": [{"slug": p["slug"], "name": p.get("name") or p["slug"], "repo": p.get("repo") or ""}
                         for p in snapshot.get("projects", [])],
            "cards": {c["id"]: {"project": c.get("project", ""), "title": c.get("title", ""),
                                "column": c.get("column", "")} for c in snapshot.get("cards", [])}}


class Host:
    """`GitMonitor` z kodu worktree z żywą pętlą Qt; `compute_threads` zbiera wątki, w których liczona jest mapa."""

    def __init__(self, journal, board):
        sys.path.insert(0, os.path.join(REPO_ROOT, "daemon"))
        from PyQt6.QtCore import QCoreApplication, QEventLoop
        from tide_daemon.git_monitor import GitMonitor
        self._loop_flags = QEventLoop.ProcessEventsFlag.AllEvents
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.compute_threads = []
        self.monitor = GitMonitor(lambda: journal, board_source=lambda: board, map_compute=self._compute)

    def _compute(self, reader, journal, board, text):
        self.compute_threads.append(threading.get_ident())
        return self.monitor._branch_map(reader, journal, board, text)

    def pump(self, done, timeout=REPLY_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while not done():
            if time.monotonic() > deadline:
                return False
            self.app.processEvents(self._loop_flags, 5)
            time.sleep(0.0005)
        return True

    def close(self):
        self.monitor.stop()


def time_branch_map(host, request: str, clock=time.perf_counter) -> dict:
    """Jedno wywołanie `monitor.branch_map`: czas od wywołania do odpowiedzi z puli, czy wywołanie wróciło przed
    odpowiedzią, w jakim wątku przyszła odpowiedź i w jakim liczono mapę."""
    box = {}
    caller = threading.get_ident()
    del host.compute_threads[:]

    def reply(text):
        box.update(text=text, at=clock(), thread=threading.get_ident())

    start = clock()
    host.monitor.branch_map(request, reply)
    returned = clock()
    returned_first = "text" not in box
    if not host.pump(lambda: "text" in box):
        raise CaptureError("brak odpowiedzi mapy w limicie czasu")
    return {"seconds": box["at"] - start, "returnSeconds": returned - start, "returnedBeforeReply": returned_first,
            "replyOnCallingThread": box["thread"] == caller,
            "computedOffCallingThread": bool(host.compute_threads) and caller not in host.compute_threads,
            "response": json.loads(box["text"])}


# --- reguła wyboru karty -------------------------------------------------------------------------------


def _num(card: str) -> int:
    return int(card.split("-", 1)[1])


def _info(card, reply):
    """Skrót podglądu do listy „przejrzane”: liczba plików i `canMerge`, albo kod błędu."""
    if not reply.get("ok"):
        return {"card": card, "error": reply.get("error") or "no-preview"}
    return {"card": card, "fileCount": reply.get("fileCount", 0), "canMerge": bool(reply.get("canMerge"))}


def choose_card(cards, probe, preferred=PREFERRED_CARD, threshold=FILE_THRESHOLD) -> dict | None:
    """Reguła z karty K-99/PT-471, bez pytania:

    1. `preferred` (K-3), jeśli jej podgląd ma > `threshold` plików;
    2. inaczej pierwsza karta z > `threshold` plikami, z pierwszeństwem domkniętych (`state: closed`), potem
       aktywnych, potem reszty, w obrębie grupy rosnąco po numerze;
    3. inaczej karta o największej liczbie plików spośród tych, dla których usługa zwraca podgląd (także
       `canMerge: false`); remis → nowsza karta (wyższy numer).

    `cards` — `[{"card", "state"}]` z mapy; `probe(card)` → `{"card", "fileCount", ...}` albo `{"card", "error"}`.
    Zwraca `{"step", "card", "fileCount", "threshold", "reviewed", "result"}` albo None, gdy żadna karta nie ma
    podglądu."""
    seen = {}

    def look(card):
        if card not in seen:
            seen[card] = probe(card)
        return seen[card]

    def done(step, card):
        return {"step": step, "card": card, "fileCount": seen[card]["fileCount"], "threshold": threshold,
                "reviewed": [{k: v for k, v in r.items() if k in ("card", "fileCount", "canMerge", "error")}
                             for r in seen.values()], "result": seen[card]}

    def big(card):
        info = look(card)
        return "fileCount" in info and info["fileCount"] > threshold

    if any(c["card"] == preferred for c in cards) and big(preferred):
        return done(1, preferred)
    rank = {"closed": 0, "active": 1}
    for item in sorted(cards, key=lambda c: (rank.get(c.get("state"), 2), _num(c["card"]))):
        if item["card"] != preferred and big(item["card"]):
            return done(2, item["card"])
    available = [r for r in seen.values() if "fileCount" in r]
    if not available:
        return None
    best = max(available, key=lambda r: (r["fileCount"], _num(r["card"])))
    return done(3, best["card"])


def make_probe(client):
    """Dla karty: `ListCardBranches` (`purpose: merge`) → `defaultTargetRef` → `PreviewCardMerge` tego celu."""
    def probe(card):
        listing = client.call("ListCardBranches", card, json.dumps({"purpose": "merge", "limit": 100}))
        target = listing.get("defaultTargetRef") if listing.get("ok") else ""
        request = {"targetRef": target} if target else {}
        preview = client.call("PreviewCardMerge", card, json.dumps(request))
        info = _info(card, preview)
        info.update(listing=listing, preview=preview, request=request)
        return info
    return probe


# --- zapis ------------------------------------------------------------------------------------------------


def sanitizer(home: str):
    """Zamienia katalog worktrees na `<WORKTREES>` i katalog domowy na `<HOME>` w każdym napisie (także w kluczach)."""
    worktrees = re.compile(re.escape(home) + r"/DevWorkspace/[^/\s\"]+-worktrees")
    home_re = re.compile(re.escape(home))

    def clean_text(text):
        return home_re.sub("<HOME>", worktrees.sub("<WORKTREES>", text))

    def clean(value):
        if isinstance(value, str):
            return clean_text(value)
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {clean_text(k) if isinstance(k, str) else k: clean(v) for k, v in value.items()}
        return value
    return clean


def git_status(repo: str, runner=None) -> list:
    """`git status --short` bez opcjonalnych blokad (nie odświeża indeksu); tylko odczyt."""
    runner = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True, check=False))
    done = runner(["git", "--no-optional-locks", "-C", repo, "status", "--short"])
    if done.returncode != 0:
        return [f"<błąd git status: {done.returncode}>"]
    return sorted(done.stdout.splitlines())


def git_rev(repo: str, ref: str, runner=None) -> str:
    runner = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True, check=False))
    done = runner(["git", "-C", repo, "rev-parse", "--verify", "--quiet", ref])
    return done.stdout.strip() if done.returncode == 0 else ""


def write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def capture(*, client, branch_map, status, rev, now, out, project=DEFAULT_PROJECT, limit=500,
            min_branches=MIN_BRANCHES, card_branch=DEFAULT_CARD_BRANCH, home=None, board_dir="", repo="",
            worktree="") -> dict:
    """Całość zapisu na wstrzykiwanych zależnościach: `branch_map(tekst)` → odpowiedź mapy, `status(repo)` → linie,
    `rev(repo, ref)` → SHA. Nic nie jest zapisane, gdy mapa ma mniej gałęzi niż `min_branches`."""
    clean = sanitizer(home or os.path.expanduser("~"))
    before = {"board": status(board_dir) if board_dir else [], "repo": status(repo) if repo else []}
    request = {"project": project, "limit": limit}
    tree = branch_map(json.dumps(request))
    if not tree.get("ok"):
        raise CaptureError(f"mapa gałęzi: {tree.get('error')}: {tree.get('message')}")
    if tree["total"] < min_branches:
        raise TooSmall(f"mapa projektu {project} ma {tree['total']} gałęzi kart i ticketów (wymagane ≥ {min_branches})")
    repo = repo or tree["repo"]
    cards = [{"card": n["card"], "state": n["state"]} for n in tree["nodes"] if n["kind"] == "card"]
    chosen = choose_card(cards, make_probe(client))
    if chosen is None:
        raise CaptureError("żadna karta projektu nie ma podglądu scalenia — nie ma czego zapisać")
    picked = chosen["result"]
    preview, listing = picked["preview"], picked["listing"]
    after = {"board": status(board_dir) if board_dir else [], "repo": status(repo)}
    meta = {
        "capturedAt": now, "project": project, "request": request, "card": chosen["card"],
        "target": preview.get("targetRef", ""), "cardBranch": {"name": card_branch, "sha": rev(repo, card_branch)},
        "worktreeHead": rev(worktree, "HEAD") if worktree else "",
        "mapTotal": tree["total"], "mapNodes": {k: sum(1 for n in tree["nodes"] if n["kind"] == k)
                                                 for k in ("base", "card", "ticket", "unknown-base")},
        "mapTruncated": tree["truncated"], "selection": {k: chosen[k] for k in
                                                         ("step", "card", "fileCount", "threshold", "reviewed")},
        "preview": {"fileCount": preview["fileCount"], "commitCount": preview["commitCount"],
                    "canMerge": preview["canMerge"], "blockedReasons": preview.get("blockedReasons", []),
                    "conflictCount": preview.get("conflictCount", 0)},
        "listCardBranches": {"request": {"purpose": "merge", "limit": 100}, "branches": len(listing.get("branches", [])),
                             "defaultTargetRef": listing.get("defaultTargetRef", "")},
        "previewRequest": picked["request"],
        "dbusCalls": [list(c) for c in client.calls],
        "gitStatus": {"board": {"before": before["board"], "after": after["board"],
                                "unchanged": before["board"] == after["board"]},
                      "repo": {"before": before["repo"], "after": after["repo"],
                               "unchanged": before["repo"] == after["repo"]}},
    }
    os.makedirs(out, exist_ok=True)
    for name, data in (("branchmap.json", tree), ("list_card_branches.json", listing),
                       ("preview_card_merge.json", preview), ("capture.json", meta)):
        write_json(os.path.join(out, name), clean(data))
    return clean(meta)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="katalog zapisu, np. apps/board/tests/data/k99_acceptance")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--card-branch", default=DEFAULT_CARD_BRANCH)
    parser.add_argument("--board-dir", default="", help="katalog Tablicy (domyślnie boardDir z migawki usługi)")
    args = parser.parse_args(argv)
    client = BusClient()
    try:
        require_methods(client)
        snapshot = client.call("Snapshot")
        board_dir = args.board_dir or snapshot.get("boardDir") or ""
        if not board_dir:
            raise CaptureError("migawka usługi nie podaje katalogu Tablicy; podaj --board-dir")
        sys.path.insert(0, os.path.join(REPO_ROOT, "daemon"))
        from tide_daemon.cardflow.journal import Journal
        board = board_view_from_snapshot(snapshot)
        repo = next((p["repo"] for p in board["projects"] if p["slug"] == args.project), "")
        host = Host(Journal(board_dir), board)
        try:
            def tree(text):
                return time_branch_map(host, text)["response"]
            meta = capture(client=client, branch_map=tree, status=git_status, rev=git_rev,
                           now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), out=args.out,
                           project=args.project, limit=args.limit, card_branch=args.card_branch,
                           board_dir=board_dir, repo=repo, worktree=REPO_ROOT)
        finally:
            host.close()
    except TooSmall as exc:
        print(f"ZA MAŁO GAŁĘZI: {exc}", file=sys.stderr)
        return EXIT_TOO_SMALL
    except CaptureError as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: meta[k] for k in ("card", "target", "mapTotal", "mapNodes", "selection", "preview",
                                           "cardBranch", "gitStatus", "dbusCalls")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
