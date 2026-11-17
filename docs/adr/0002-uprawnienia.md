---
status: accepted
date: 2026-11-12
source: owner's decision of 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-2
---

# ADR-2: Permissions from the project profile and a single command classification

## Context

The only command classifier today is `OUTSIDE_RISKY` (a list of prefixes), separate from the classification of the heavy-work queue
and the runners. The auditors (Fable, Astra) differed on a command that cannot run under isolation:
"fall back to the host" or "refuse".

## Decision

- The project profile = the `permissions` key in `projects/<slug>/box.yaml`, **outside the recipe hash**, with its own **approval
  version** and a **revocation** that also applies to pending commands.
- One function `policy.classify(command, roots, profile) -> (class, reason)` for `outside_risky`,
  `heavyqueue.classify`, and both runners (`HostRunner`, `BoxRunner`).
- `OUTSIDE_RISKY` stays as the **lower layer** — an escalation rule, not isolation.
- The resolution of "fall back to the host" vs "refuse" according to binding condition W4:
  - a command that cannot run under isolation (`needs` unmet) **does not get automatic
    execution** — it returns to the existing owner decision (`OUTSIDE_RISKY`/`run`);
  - after explicit consent it runs the current host way as **the same** `HostRunner.run_command` with the profile
    `isolation: none` **recorded in the event** and counted in the measurement (K-109 point 6);
  - there is no third, permanent mode without consent — there is automation's refusal and human consent;
  - when the write boundary cannot be determined (unknown write set, unknown history) — refuse, without asking.

## Consequences

- No automatic fall back to the host without consent; every execution without isolation is visible in the card journal and in
  the measurement.
- The heavy-work queue (ADR-3) and the runners (ADR-1) classify a command with the same function, so they cannot
  diverge.

**What contradicts this today:** `OUTSIDE_RISKY` as the only classifier — it stops being one when
`policy.classify` with a profile appears (K-109 stage 2).

## Executing card and milestone

K-109 stage 2 (permissions profile), milestone **M2**.

## Open questions (for K-109)

The report leaves no open decision here. It does not enumerate the classes returned by `policy.classify` or the form of `needs` —
that belongs to the K-109 plan within the decision above.
