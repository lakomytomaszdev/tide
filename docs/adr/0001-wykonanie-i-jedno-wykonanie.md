---
status: accepted
date: 2026-11-12
source: owner's decision of 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-1
---

# ADR-1: Command execution and the single-execution guarantee

## Context

Non-agent commands (a command outside the sandbox via `_outside_launch` in the engine, merge-check, commands in the box
via `tasks/commands.run` and `box/command.py`) today go through separate paths beside the `Runner` contract, and a command's start is
written to the card journal only after `Popen`, so "single execution" is not guaranteed. The execution axis has one contract, which v1 provides with a
host implementation, v1.1 with a box implementation, and v2 with a multi-project one.

## Decision

- `Runner.run_command(request) -> CommandHandle` is the **only** path for non-agent commands.
- `HostRunner` = today's body of `_outside_launch` + isolation as a transient service
  `systemd-run --user --wait --collect -p …` with the `tide-step` wrapper and an `exit-code` file (the `BoxRunner` model).
- `BoxRunner.run_command` absorbs `tasks/commands.run` and `box/command.py`.
- `_start_outside`, `_merge_check_run`, and the CLI call the runner; the `box-outside` refusal goes away.
- The **single-execution protocol** applies to both runners (`HostRunner`, `BoxRunner`) and the CLI:
  1. a durable execution identifier (in the report: "attempt identifier") and an intent event `command.intent`
     **before** launch;
  2. a budget reservation (ADR-3) in the same write;
  3. start confirmation — `command.started` with the unit — after the handle is obtained;
  4. the result — `command.finished` with the exit code **from the `exit-code` file**, not from the client's code;
  5. recovery after a restart: an intent without a start → check the unit by identifier; never relaunch
     (`Popen`) without proof that the process is absent.
- Extracting `_outside*` and `_merge_check*` into `commands.py` is part of the `run_command` implementation, not a separate
  refactor — according to the module boundaries rule [ADR-6](0006-granice-modulow-silnika.md).

## Consequences

- The gap between launching a process and recording its start is closed; v1 criterion no. 8 ("restart during a
  command = 0 double operations") gets a mechanism.
- **Acceptance of M2 on both runners with a real step in the box** — `box.enabled=true` without steps measures performance, not
  isolation.
- v1.1 = the box through the same input; v2 = .NET/Docker profiles without engine changes.

**What contradicts this today** (will disappear in this order, in K-109 stage 1):

1. `_outside_launch` in the engine and the start record (`outside.started`) after `Popen` — replaced by a protocol with the intent
   before the start;
2. the `box-outside` refusal for a step in the box — goes away when `BoxRunner.run_command` accepts commands.

## Executing card and milestone

K-109 stage 1 (`run_command` + protocol + `commands.py`), milestone **M2**.

## Open questions (for K-109)

- The budget reservation in step 2 depends on where the module pool for the box lives — a question from
  [ADR-3](0003-budzet-pracy.md).
