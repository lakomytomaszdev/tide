---
status: accepted
date: 2026-11-16
source: architecture review of 15–16.11.2026 at the owner's request; owner's decision of 16.11.2026 (docs/agents/masterplan/CURRENT.md, Change history "16.11.2026 ~14:30 — masterplan v1.1 po ADR-7"), coordinator analysis build/coordinator-notes/2026-11-16-adr7-analiza.md, consultation with Astra
---

# ADR-7: Events, index, and delivery — the direction after v1

The direction for v1.1 and v2. **It does not change the scope of v1** or any v1 card (including the K-109 plan); nothing in this ADR starts
before M5.

## Context

The card journal is an event log, and the card state is computed from it by `fold` (decision 84 in `docs/decisions.md`). The core is
therefore event-based. Around it, three things are missing:

1. **An event catalog.** 69 journals contain 66,011 events in 79 types, all with `v=1` (read on 16.11 15:55). The types are
   strings scattered across modules, with no description of fields and no type version.
2. **Delivery.** 11 of the service's 14 D-Bus signals carry no content (`dbus_service.py`), so after a signal the receiver fetches
   the whole state. The engine scans cards every 5 s (`cardflow_monitor.POLL_MS`, `cardflow_monitor.py:33`), and it detects the end of a step
   with the command `systemctl is-active` (`tasks/stepstop.unit_state`). K-105, K-113, and K-125 successively lowered the cost
   of idling: 98.7% → 70.75% → 62.7% → 19.8% of a core in production against the M0 threshold of 30%. The measurement after K-125 finished on 16.11
   at night, PASS in both modes: box off — windows 18.5–22.2% (average 19.8%), Snapshot p95 224 ms; box
   on — windows 16.1–21.1% (average 17.4%), p95 342 ms. **The 5 s scan is no longer a measured cost of idling:**
   `engine.tick` is about 1.2% of samples at night and 1.6% in the evening. The cost of idling with active sessions is described in point 4.
3. **An index for reading.** Questions across many cards (`tools/coordinator/metrics.py`, `decisions.py`, the report) read
   all journals every time (84.19 MiB of content in 69 journals, 16.11 ~14:12). The service journal **is already
   appended with `fsync`** (`cardflow/journal.py:481`, `_append`), but every append is preceded by a fresh read of the whole
   card history (`journal.py:457`, `append` → `read_fresh`), and after it `_restore` rewrites the whole journal copy
   on the Board (`journal.py:597`). The Board commit collects the set of changed paths (`cardflow_monitor.py:429`, `commit`
   → `take_dirty`), so it is not a simple "one commit per event". The Board repository: `.git` 401.38 MiB
   (`du -s -B1`, 16.11 ~14:12); 21,298 commits since 27.10 (16.11 15:55).
4. **Scans driven by changes, not by the clock.** Besides the engine, the service periodically scans sessions and coordination:
   - `monitor.py`: every 5 s a recomputation of session states and a process probe (`TICK_MS`, `monitor.py:32`), every 30 s a full
     review of session directories (`RESCAN_MS`, `monitor.py:33`);
   - `coordination_monitor`: a refresh every 20 s (`REFRESH_MS`, `coordination_monitor.py:20`), and additionally after every
     change of the session list, at most every 4 s (`MIN_GAP_MS`, `coordination_monitor.py:21`; wired in `app.py:680`);
     the refresh scans coordinator journals (`coordination/scanner.py:404`, `_claude_candidates`) and reads
     worktrees and branches with `git` commands (`coordination/repoplans.py`).

   With active sessions it is these, not the engine, that dominate the cost of idling: the evening run of 15.11 — average 32.1%,
   maximum 47.9%, 4 of 10 windows over the threshold, Snapshot p95 890 ms; in the profile, spikes from `scanner.refresh` (`realpath`
   in `~/.claude`), `coordination_monitor._scan`, and `git` subprocesses. The link to the scans is a diagnosis from the profile, not
   a controlled causal experiment. The night PASS (19.8% / 17.4%) was measured without active sessions, and the M5 window
   will have them. File watchers and incremental reading already exist (`docs/tide-daemon.md`, "Odświeżanie" (Refreshing)),
   so the task is to remove repeated scans between consumers, not to add a watcher.

Also considered were: a message broker (RabbitMQ), a database instead of files, and a separate vector database.

## Decision

- **Nothing from ADR-7 in v1.** The scope of v1 and the K-109 plan unchanged; all the cards below start after M5.
- **The source of truth unchanged.** Cards and plans in Markdown and the card journal (JSONL) remain the only source of state.
  [ADR-4](0004-stan-i-swiezosc.md) applies: gates before an effect read the journal fresh.
- **Event catalog.** A single registry of types: name, fields, type version, owner module. The first entries are `command.*`
  from [ADR-1](0001-wykonanie-i-jedno-wykonanie.md). Events from outside cards (a limit threshold, a work mode, a deadline) get a type
  in the same registry.
- **A derived index.** SQLite from the standard library, in the service state directory. It can be deleted and rebuilt
  from the sources. It serves views and summaries: a global event number, queries across many cards, full-text
  search. **It is not a source of decisions** — no gate before an effect reads it. Requirements of the first
  ticket of card A (K-134):
  - **Identity and cursor.** A deletable SQLite is not the only place of durable event identity: an event has a
    durable identifier in the source, and the receiver reads by the cursor `(index epoch, number)`. Rebuilding the index changes
    the epoch and forces receivers to resynchronize; the number is not proof of consistency.
  - **Markdown is a source of changes too.** The index covers journals and the indicated documents of cards, plans, and notes,
    with the document version and handling of external edits, deletion, and file replacement.
  - **Loss of a signal is recoverable.** The method of fetching a difference, the batch limit, recognizing an old cursor,
    and falling back to a full read belong to card A, not to later polishing.
  - **A closed list of replaced memories and receivers.** First scope: the Board and the flow, indicated clients,
    coordinator reporting — not a rebuild of all 11 signals without content.
- **Delivery: the event wakes, the state decides.**
  - A D-Bus signal carries the card and the event cursor; the receiver pulls the difference by the cursor. A lost signal does not lose data.
  - The engine moves a card when something happened in it: the end of a systemd unit (from the systemd signal on D-Bus), a user
    decision, a deadline. The full scan stays rare, as a safeguard.
  - Triggers are still computed from the card state (`caretaker.TRIGGERS`), not from a single event. Waking changes
    only when they are checked.
  - **Waking does not enter v1.** The justification for waking is reaction time during running steps (process end →
    durable result → card consideration), not idle CPU. The share of wall-clock samples is not a reliable
    bound on the gain in process CPU, so the ADR does not estimate it with a number; we measure reaction time before starting card B.
- **Triggers.** The model is the caretaker registries `TRIGGERS` and `ACTIONS` (`cardflow/caretaker.py`), developed in K-79:
  an action from a closed list, firing writes an event with the cause. Rules above cards (limit, times of day, work
  mode) get a shared registry of the same shape as a separate card R (K-137) after K-52, together with the K-53 entry
  "work profiles after v1" (13.11) and in line with [ADR-3](0003-budzet-pracy.md): K-52 answers "how much may be admitted",
  the registry "which work settings apply and why"; it creates neither a second queue nor a second authorization.
  User triggers from closed templates — conditionally in v1.1 (K-138); arbitrary expressions, scripts, and chains of
  rules — v2.
- **Journal storage.** The journal stays appended with `fsync`; we change the fresh read of the whole history before each
  write and the rewriting of the whole copy on the Board by `_restore`, and we group Board commits less often than per
  event. A rarer commit must not delay the durable write of an intent or the response to a user action.
- **Architecture gate** (masterplan §3 point 2, owner's decision of 16.11): new cards do not add another manual state cache
  or a D-Bus signal without content; a need for differential reading goes to card A (K-134).
- **What we do not do:**
  - a message broker (RabbitMQ and the like) — D-Bus is a local bus, and a durable journal with an event cursor lets
    the receiver catch up after a restart;
  - a database as the source of truth;
  - a separate vector server — vectors, if needed, in the same index (`sqlite-vec` or multiplication
    in memory as in `krunner/semantic.py`), after full-text search.

## Consequences

- Rejected:
  - **RabbitMQ:** one machine, one user, one writing process; every event would be written in two
    places and reconciled; an additional service to install, against the direction of the public release.
  - **A database instead of files:** decisions 57 and 84 in `docs/decisions.md` and the audit of 12.11.2026.
  - **A separate vector database:** decision 75; with tens of thousands of short records one file is enough.
  - **Waking in v1:** the premise (the K-125 measurement above 30%) fell away, and waking does not touch the measured cost of idling.
  - **Card B together with K-76:** K-76 is a pure refactor ("public D-Bus and journal unchanged; no new
    features along the way", `tide-board/cards/K-76.md:42–43`), and waking changes the behavior of the same `tick`/`_advance`
    ([ADR-6](0006-granice-modulow-silnika.md)). K-76 goes before B, not together.
- **The index is another copy of state** (audit diagnosis P6: state in four copies). It is therefore meant to replace the existing
  manual memories, not add a fifth, and it has a single freshness rule: the cursor `(index epoch, number)`.
- **The event catalog becomes a contract.** Changing a type's fields requires bumping the type version and reading both versions or a migration.
- **Double firing:** waking and the safeguard scan may hit the same change; the existing
  "intent → result" pair protects against it.
- **Card B has two separately measured parts:** first the write, the copy, and the commits, then the waking — otherwise it is not known
  where an improvement or a regression comes from.
- **Risks with an owner in the cards:** cursor divergence after a SQLite rebuild (A); publishing an event before the durable
  write, response delay from rarer commits, loss of waking after a D-Bus restart, re-execution after
  waking and a scan (B); confusing a work profile with a permission (R/U).

**What contradicts this today:**

1. event types as strings in modules, with no field registry;
2. signals without content and fetching the whole state by windows and widgets;
3. a scan of all cards every 5 s and `systemctl is-active` for every running step — a reaction time cost, not a
   measured idle cost;
4. scanning all journals by coordinator tools and the report;
5. a fresh read of the whole card history before every append and rewriting the whole copy on the Board by `_restore`;
6. periodic scans of sessions (`monitor.py`, 5 s and 30 s) and coordination (`coordination_monitor`, 20 s and at most every 4 s
   after a session change) with `git` subprocesses — the main idle cost with active sessions.

## Executing card and milestone

Everything after M5, tag `v1.1`; order according to masterplan §2 (order = priorities). The ADR-7 items in K-53 stopped being
a separate backlog — each has a target card.

- **Track outside the engine:**
  - **K-133 (S)** — session and coordination scans only on change; **conditional**: only after a repeated idle measurement
    with active sessions confirms the cost. First in the track, technically independent of A and B.
  - **K-134 (A)** — event catalog, a rebuildable index, differential reading, and FTS; the four requirements from "Decision" in ticket
    1. After ADR-7 is approved; alongside K-82/K-83 only after the files are separated (`dbus_service.py`, monitors — wired in
    one by one).
  - **K-63 narrowed** — collecting usage, assigning it to steps, a simple view, computed from the index; after K-134. Estimates,
    Monte Carlo, end date, and self-calibration — v2.
  - **K-140 (Q)** — search by meaning in the same index; **conditional**: only when the FTS from K-134 is not enough
    on a set of 30 questions.
- **Engine track:**
  - **K-135 (B)** — fast durable write (part 1), then waking the right cards (part 2). After K-76 is accepted (not together
    with it), after the K-134 event contract, and after a decision on whether the journal stays in the Board repository.
  - **K-136 (I)** — "Napisz do karty" (Write to the card), a single entry point; alongside ADR-7, with no dependency on K-134 and K-137; after K-79 stage 2,
    K-108, and K-107 A/B.
  - **K-137 (R)** — a registry of rules above cards and work profiles; after K-52, K-79, and K-134, in line with ADR-3.
  - **K-138 (U)** — user triggers from closed templates; **conditional**: after K-137 and 3 scenarios from the
    owner.
- **v2:** triggers as arbitrary expressions and scripts; K-63 beyond the narrowing.

## Owner's resolutions (16.11.2026)

1. **Engine waking in v1 — no.** The condition "the K-125 measurement will not go down to 30%" did not occur (PASS 19.8% / 17.4%),
   and waking does not answer the measured cost of idling. The risk for v1 is different: idling with active sessions
   (point 4 of "Context") — it is answered by the conditional K-133.
2. **Architecture gate — yes**, as a single sentence in masterplan §3 point 2 (text in "Decision").
3. **Several cards:** K-133, K-134, K-135, K-136, K-137, K-138, K-140, and the narrowed K-63, as in "Executing card and milestone".

## Open questions

- Whether the catalog is enforced by a test, or by refusing to append an event from outside the registry (a K-134 condition).
- Whether the journal stays in the Board repository (a K-135 condition). Without extrapolation: for 7 days record the occupied bytes
  of the whole `.git` in MiB (1,048,576 B), loose objects and packs separately, the growth of commits and events, and whether GC ran;
  compute the MiB growth per day at a comparable packing state. Starting point: `.git` 401.38 MiB (`du -s -B1`,
  16.11 ~14:12), including loose 95.61 MiB and packs 294.16 MiB (`git count-objects -v`).
