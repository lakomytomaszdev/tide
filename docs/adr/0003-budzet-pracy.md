---
status: accepted
date: 2026-11-12
source: owner's decision of 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-3
---

# ADR-3: Work budget — a single admission gate for heavy work

## Context

The test module pool (`MachineSlots`) exists, but commands launched in a step go to a second pool
(`env -u XDG_RUNTIME_DIR` loses the lock directory), the capacity is a constant cores−2, the number of heavy runs has no
limit, and runs from outside the service are not in the `heavyqueue` registry.

## Decision

- A single admission gate for heavy work **before runner selection** (`heavyqueue.admit`) with separate budgets:
  - provider slots (`providerSlots`),
  - the number of heavy runs (`heavyLimit`),
  - test modules (`MachineSlots`).
- Three necessary changes:
  1. **a single module pool** — `TIDE_TEST_SLOTS_DIR` set by the service and **passed through `agentcmd` and into
     the box** (today `env -u XDG_RUNTIME_DIR` loses it); in the box the pool must be on the host side;
  2. **pool capacity computed from `loadavg`** and a reserve, not from the constant cores−2;
  3. **runs from outside the service visible in the registry** (the host pool already covers them; `heavyqueue` does not — until K-52).
- `OUTSIDE_PER_PROJECT` / `OUTSIDE_TOTAL` remain the limit for light work.

## Consequences

- The budget reservation is step 2 of the single-execution protocol ([ADR-1](0001-wykonanie-i-jedno-wykonanie.md)),
  in the same write as the intent.
- The heavy/light classification goes through `policy.classify` ([ADR-2](0002-uprawnienia.md)).

**What contradicts this today** (in plan order):

1. no limit on the number of heavy runs — goes away with K-39 (`heavyLimit`, M0);
2. two module pools — go away with passing `TIDE_TEST_SLOTS_DIR` through `agentcmd` (K-109 stage 1 or K-100 —
   see the questions) and with resolving the pool in the box (K-109);
3. constant capacity and runs from outside the service outside the `heavyqueue` registry — changes 2 and 3 (K-109 / K-52).

## Executing card and milestone

K-39 + K-109, milestones **M0–M2**; registering runs from outside the service in `heavyqueue` → K-52 (v1.1).

## Open questions

- **For K-109: the module pool in the box** — mounting the lock directory or reserving slots before `incus exec`; the
  K-109 plan picks a variant and proves it with a test. `[NIESPRAWDZONE wykonalność montażu]`.
- **For K-109 / K-52:** the report assigns the gap "capacity without the rest of the load" (change 2) jointly to K-109/K-52,
  without naming a single card — the K-109 plan decides.
- **For K-109 / K-100:** in its stage table the report allows passing `TIDE_TEST_SLOTS_DIR` through `agentcmd` as a
  ticket in K-100 or in K-109 stage 1, while in the card verdict it assigns it to K-109 — a discrepancy to resolve when
  planning whichever of these cards starts first.
