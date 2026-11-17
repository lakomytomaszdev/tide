---
status: accepted
date: 2026-11-12
source: owner's decision of 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §3 and §7 point 2
---

# ADR-6: Engine module boundaries — extracting responsibilities with explicit inputs

A record of an existing decision from the audit, not a new decision.

## Context

`engine.py` grows with every v1 card; the existing splits (the `CaretakerChat` and `PlanEditing` bases) use a shared
`self` and do not change the import graph, and the helpers import `FlowError`/`FlowDeferred` from the engine. A mechanical engine split before v1
does not cure the causes and — measured in lines — rewards moving code, not decoupling it.

## Decision

- **We do not break up the engine before v1** and do not open a window for mixins.
- **The rule for engine cards** (K-109, K-108, K-106, K-79, K-110, K-107, K-95, K-111): each card extracts
  **the responsibility it touches into a module with explicit inputs** — data in parameters, the result as a value;
  the model is `heavyqueue.py`, `ticketlimit.py`, `stepqueue.py`. A **thin wrapper** stays in the engine (it keeps the
  tests' patch points).
- **No mixins with a shared `self`.**
- **A new module does not import `engine`.**
- **The measure: test dependencies, not lines.** The criterion in the ticket sheet: the number of test modules selected for a change in the new
  module < the number for `engine.py` — measured by `tools/test_dependencies.py` in the note (the "sięga silnika" (reaches the engine) column of the dependency
  report); we give the lines of `engine.py` after the merge for information only.
- **Who extracts what:**

  | Card | Responsibility | Module |
  |---|---|---|
  | housekeeping window (K-112) | `FlowError`, `FlowDeferred`; reading the step result (`_raw_result`, `_result`, `_review_result`, `_claude_denied_command`); the baseline dependency measurement | `cardflow/errors.py`, `cardflow/stepresults.py`, `tools/test_dependencies.py` |
  | K-109 | `_outside*` + `_merge_check*` as part of the `run_command` implementation ([ADR-1](0001-wykonanie-i-jedno-wykonanie.md)), not a separate refactor | `commands.py` |
  | K-79 | `_caretaker*` + `_guard*` | `caretaking.py` |
  | K-108 | `_recover*` (resumptions) | — |
  | K-76 (after v1 acceptance) | the rest: the `tick`/`_advance`/`_start`/`_resume` core, `plan*`, K-38 code, `coordination/` | — |

- The housekeeping window does not touch `tick`, `_advance*`, `_start*`, `_enter*`, `_resume*`, `_resolve*`, `__init__`,
  the event schema, or `FlowState`.

## Consequences

- Rejected: a line-count target (≤ 7,400 was unrealistic), "net decrease" as a criterion, and mixins with a shared `self`.
- The risk of false success (fewer lines, same dependencies) is limited by measuring dependencies.
- The "done" criterion of the housekeeping window: cardflow tests without changing assertions; zero `engine` imports from the new modules;
  the baseline dependency measurement in the note; the `make test-cardflow` time at a fixed `JOBS` no worse than 10%.

**What contradicts this today** (in plan order):

1. helpers importing `FlowError`/`FlowDeferred` and reading the step result from the engine — goes away in the housekeeping window
   (K-112);
2. `_outside*` and `_merge_check*` in the engine — K-109 (M2);
3. `_recover*` in the engine — K-108 (M2);
4. `_caretaker*` and `_guard*` in the engine — K-79 (M3);
5. the core, `plan*`, K-38 code, and `coordination/` in the engine — K-76 after v1 acceptance.

## Executing card and milestone

The housekeeping window (K-112) after M0; the rule applies to every engine card until v1; K-76 after M5.

## Open questions

- **For K-79 and K-76:** the report lists the `CaretakerChat` and `PlanEditing` bases as a flaw, but does not assign them
  explicitly to any card; the assignment follows only from the `_caretaker*` (K-79) and `plan*` (K-76) groups — to be confirmed
  in the plans of those cards.
- **For K-76:** `tools/test_suites.py` maps the whole `cardflow/` directory to the full test suite; the report does not assign
  changing this mapping to any card.
