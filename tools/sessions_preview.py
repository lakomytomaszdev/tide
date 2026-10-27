#!/usr/bin/python3
"""Dane podglądu okna „Tide Sesje” z wyniku `tide-daemon.py --print-coordination`.

Użycie: /usr/bin/python3 tools/sessions_preview.py WEJŚCIE.json [--sanitize] > WYJŚCIE.json

--sanitize  zostawia strukturę prawdziwych danych (koordynatorzy, ID ticketów, stany, role, czasy,
            liczby BLOCKER/MAJOR/NIT), a wycina treść: tytuły rozmów i ticketów, opisy i polecenia
            podagentów, pytania, wyniki, cytaty z review, ostatnie czynności i nazwy gałęzi —
            do zrzutów, które można pokazać bez treści pracy.

Dopisuje „now” (chwila zebrania danych), żeby czasy na zrzucie były takie jak w chwili odczytu.
"""
import argparse
import json
import re
import sys
import time

ROLE_WORDS = {"implementer": "implementacja", "reviewer": "review", "helper": "badanie"}


def sanitize(data: dict) -> dict:
    boards = data.get("boards") or {}
    titles: dict[str, str] = {}
    for n, (coord_id, board) in enumerate(boards.items(), start=1):
        label = board.get("label") or ""
        titles[coord_id] = f"Koordynator {label}".strip() if label else f"Koordynator {n}"
        board["title"] = board["sessionTitle"] = titles[coord_id]
        board["branch"] = _branch(board.get("branch") or "", label)
        board["cwd"] = board["repoPath"] = f"~/DevWorkspace/{board.get('project') or 'projekt'}"
        if board.get("question"):
            board["question"]["text"] = "(treść pytania ukryta)"
        if board.get("plan"):
            board["plan"]["path"] = "docs/agents/plans/plan.md"
            board["plan"]["title"] = titles[coord_id]
            board["plan"]["goal"] = "" if not board["plan"].get("goal") else "(cel ukryty)"
        tracker = board.get("tracker") or {}
        if tracker.get("url"):
            tracker["url"] = "http://youtrack.local"
        fallback = 0
        for ticket in board.get("tickets") or []:
            if ticket.get("id"):
                ticket["title"] = f"Ticket {ticket['id']}"
            else:
                fallback += 1
                ticket["title"] = f"Zadanie bez ID {fallback}"
                ticket["key"] = f"~zadanie-{fallback}"
            ticket["branch"] = _branch(ticket.get("branch") or "", ticket.get("id") or "")
            ticket["planNote"] = "" if not ticket.get("planNote") else "(uwagi ukryte)"
            ticket["description"] = "" if not ticket.get("description") else "(opis ukryty)"
            ticket["brief"] = "" if not ticket.get("brief") else "(polecenie ukryte)"
            ticket["url"] = f"http://youtrack.local/issue/{ticket['id']}" if ticket.get("url") else ""
            ticket["worktree"] = ""
            if ticket.get("question"):
                ticket["question"]["text"] = "(treść pytania ukryta)"
            if ticket.get("state") == "czeka" or (ticket.get("note") or "").startswith("czeka na ciebie"):
                ticket["note"] = "czeka na ciebie: „(treść pytania ukryta)”"
            if ticket.get("review"):
                ticket["review"]["top"] = ""
            for event in ticket.get("events") or []:
                event.pop("quote", None)
                if event.get("kind") == "question":
                    event["text"] = "(treść pytania ukryta)"
                elif event.get("review"):
                    r = event["review"]
                    parts = [f"{r[k]} {k.upper()}" for k in ("blocker", "major", "nit") if r.get(k)]
                    event["text"] = "review — " + (", ".join(parts) if parts else "bez uwag")
                elif event.get("kind") in ("launch", "continue"):
                    event["text"] = re.sub(r"„[^”]*”", "„…”", event.get("text") or "")
                else:
                    event["text"] = ""
        for run in board.get("runs") or []:
            run["description"] = f"{ROLE_WORDS.get(run.get('role'), 'praca')} {run.get('ticket') or ''}".strip()
            run["summary"] = run["lastAction"] = ""
            run["branch"] = _branch(run.get("branch") or "", "")
    data["coordinators"] = [dict(c, title=titles.get(c["id"], c.get("title")), branch=_branch(c.get("branch") or "",
                                                                                           c.get("label") or ""),
                                 cwd="", repoPath="", planPath="", question=None)
                            for c in data.get("coordinators") or []]
    data["sessions"] = [{k: s.get(k) for k in ("id", "tool", "project", "state", "reason", "canActivate",
                                               "lastActivityTs", "sinceTs")} for s in data.get("sessions") or []]
    data.pop("details", None)
    return data


def _branch(branch: str, ticket: str) -> str:
    """Gałąź bez opisu pracy: prefiks i ID ticketu („codex/tom-20-release-candidate” → „codex/tom-20-…”)."""
    if not branch or branch == "HEAD":
        return branch
    head, _, tail = branch.rpartition("/")
    match = re.search(r"[a-z]{2,6}-?\d+", tail, re.I)
    kept = match.group(0) if match else (ticket.lower() if ticket else "praca")
    return (head + "/" if head else "") + kept + ("-…" if tail != kept else "")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Dane podglądu okna Tide Sesje")
    parser.add_argument("input")
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args(argv)
    with open(args.input, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("now", time.time())
    if args.sanitize:
        data = sanitize(data)
    json.dump(data, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
