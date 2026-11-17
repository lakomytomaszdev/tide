---
status: accepted
date: 2026-11-12
source: owner's decision of 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-4
---

# ADR-4: State and freshness — a single card journal version

## Context

The service tick performs three strict journal reads per card. Background (diagnosis P6 of the report, only
as context, not a decision): state today exists in four copies with different invalidation boundaries — the service
journal and its mirror on the Board, `FlowState`/`Snapshot`, the plan in several places, and the memory of a single tick.

## Decision

- **The PT-411 contract unchanged:** card journal version = `(source, st_dev, st_ino, st_size, st_mtime_ns,
  st_ctime_ns, generation, last seq)` from `fstat` of the open descriptor; incremental growth with validation; a full read every
  10 min.
- **An error is never a memory.**
- Gates before an effect **read fresh** (`unchanged`, `append_complete`).
- **PT-415 and PT-416 use the same version** as PT-411 — it is already one mechanism, not three; merging the tickets
  from version 1 of the report is withdrawn.
- A card at rest outside the loop (PT-412).
- A differential snapshot (`version` already exists); **new views through separate D-Bus methods**.

## Consequences

PT-411, PT-415, and PT-416 use one version; PT-412 (a card at rest outside the loop) is a separate change.
New views go through separate D-Bus methods.

**What contradicts this today:** three strict reads per card per tick — go away with K-105 (M0).

## Executing card and milestone

K-105, milestone **M0**.

## Open questions (for K-105)

No open decision. The share of these reads in the service CPU is a `[hipoteza]` in the report — decided by the K-105
"before/after" measurement, not by this ADR.
