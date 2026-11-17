---
status: accepted
date: 2026-11-12
source: owner's decision of 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-5
---

# ADR-5: Authorization of step results and Git operations

## Context

The review verdict is already pinned to a commit (`reviewTip` → `expected_tip`), but the verdict is carried by a writable
`result.json`, and the git lock is keyed by path, not by repository. This ADR is the contract with the executing cards, point by point.

## Decision

| Point | Decision | Executing card and milestone |
|---|---|---|
| (a) | **A verdict pinned to the SHA and the attempt** ("attempt" in the report's wording; in the glossary an attempt is a step retry) — exists (`reviewTip` → `expected_tip`); formally record it as a merge condition. | K-109 stage 2, **M2** |
| (b) | **The `source` field** (panel / D-Bus / steward / rule) in the `step.decision` and `review.decision` events. | K-108, **M2** |
| (c) | **A protected verdict channel and an immutable review input**: the review result is written by the service from the reviewer's session to a directory outside the step's write access; the review input hash in the event. The hole is the same on the host and in the box. | **K-109 stage 2, M2 — for the host**; K-83 (v1.1) only proves it for `executor: box` |
| (d) | **A single git lock by `git-common-dir`** (`lock_for(repository_key(repo))`). K-66a and K-107 #15 use it. | K-110 ticket 1, **M3** (K-110 adds operations under this lock) |
| (e) | **`merge-tree` through the existing `gitops.prepare_merge_tree`**, with no new path. | K-110, **M3** |

## Consequences

- The verdict channel does not wait for the box: K-83 is not its executing card, only the proof for `executor: box`.
- Cards adding git operations (K-110, K-66a, K-107 #15) use the same lock and the existing
  `prepare_merge_tree`.

**What contradicts this today** (in plan order):

1. a writable `result.json` as the verdict carrier — goes away with the protected channel (K-109 stage 2, M2);
2. a git lock by worktree path — goes away with K-110 ticket 1 (M3).

## Open questions

No open decision. The form of the protected channel write (directory, format, reviewer session) is set by the K-109 stage 2 plan
within point (c).
