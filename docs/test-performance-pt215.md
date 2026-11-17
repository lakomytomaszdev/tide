# PT-215 — measurement of the full tests

A measurement of 5 November 2026, started at 19:27:55 +01:00, outside the sandbox,
in the ticket's worktree on commit `e118500081023bb7c7efa3e78fb9cce63e19207d`.
Command: `make test-benchmark`. The machine provides 16 cores; the parallelism limit: 14.

| Run | Time | Tests | Skipped |
| --- | ---: | ---: | ---: |
| Old `discover`, whole directories in sequence | 2447.59 s (40 min 47.59 s) | 4163 | 2 |
| Parallel `make test JOBS=14` | 368.98 s (6 min 8.98 s) | 4163 | 2 |
| Final `make test`, with isolation and exceptions | 373.25 s (6 min 13.25 s) | 4164 | 2 |

Speedup: **6.63×**. 236 modules, 27 directories; the test identifiers along with
repetition counts are identical. No executed tests were lost or added
between these two ways of running. The result includes 12 new runner tests.

Source: `build/test-benchmark/comparison-q4q0ub19/comparison.json`, `report.md`,
the directory logs, and `parallel.log`. The original full log of the command is also located
in the step directory `pt-215-szybkie-testy-rownolegly-make.step/outside/outside.log`.

## Failures already present in the sequential run

Both runs reported the same three failures:

- `test_board_derive.LiveTest.test_agents_and_coordination_changes_move_card_live`:
  after creating the card, the `review` column instead of `todo`.
- `test_card_branches.CardBranchesTest.test_no_main_deleted_ref_stale_sha_and_branch_collision`:
  a generic message instead of the expected one containing "tylko" (only).
- `test_cardflow_base_change.FollowupArtifactTest.test_approved_sketch_scope_links_and_installation_copy`:
  the K-50 plan does not contain the required link `followups/replay-code.md`.

All three were also reproduced individually in the current worktree via `python3 -m unittest`
(3 tests, 0.605 s, 3 failures). We do not change these tests or the behavior of other cards in PT-215.
A missing link in an existing plan is not proof of an incorrect base.

## Differences in the first measurement

Sequentially, additionally failed
`test_cardflow_dbus.JournalTest.test_journal_removed_from_outside_comes_back_with_a_warning`:
the journal was restored, but the error list read did not yet have the warning.

In parallel, additionally failed:

- `test_cardflow_review.ReviewFlowTest.test_reject_after_three_rounds_records_decision_without_merging`:
  the number of `step.started` rose from 6 to 7.
- `test_cardflow_youtrack.YouTrackFlowTest.test_offline_result_comment_does_not_block_next_ticket`:
  `LOG-1`, `LOG-3` started instead of `LOG-1`, `LOG-2`.

There are no differences in skips or expected failures. The full benchmark correctly returned a
non-zero code and a final list of five failures of the parallel run. The following steps checked
the differing cases separately and under load; the results are described below. The first measurement
confirms performance and completeness, not the flawlessness of the whole suite.

## Narrowed probe and isolation

The probe `build/pt215/probe.py`, run outside the sandbox, saved its results in
`build/pt215/probe-3xyoh3bt/`. The three cases run 3 times each individually passed
all 9 attempts. With 14 simultaneous processes, the review rejection had 1/5 failures,
the D-Bus warning read 2/4, and the YouTrack comment passed 5/5 attempts.

The saved journal confirmed that the seventh step after a review rejection is the card
close-out. The test counts the steps before the next tick manages to start them. The warning test reads
the snapshot right after the restored file appears — publishing the warning is separate.
Both modules were marked to run individually **after the parallel part finishes**;
`tools/test_suites.py` contains the justifications and the probe results. Their assertions were not changed.

The `FlowInProcess` fixture used the production `stop()`, which closes the pool with `wait=False`.
An old thread could still change the journal during an engine restart, rolling back the test environment,
or deleting the repository. In the monitor variant used only by the fixture, waiting
for the pool to finish and for Qt callbacks to be delivered before returning from `stop()` was added.
The new test `test_cardflow_fixture` showed the bug before the fix and passed after it.
The production monitor and the expected behavior of the flow were not changed.

Local check: `python3 -m unittest daemon.tests.test_cardflow_fixture scripts.tests.test_test_runner`
— 13 tests, 0.975 s, OK.

## Final full run after isolation

`make test` was run outside the sandbox on commit
`f7c7679b7b5120c89067d85ce3dc30658f513b82`: **373.25 s**, JOBS=14,
**4164 tests, 237 modules, 2 skips**. The increase in the number of tests by one comes solely
from the new fixture isolation regression. Compared with the original sequential measurement it means
a **6.56× shorter time**, taking into account the final exceptions to parallelism.

The log confirms the execution of both `[OSOBNO]` (separately) modules: `test_cardflow_dbus.py` (15 tests,
28.50 s, OK), then `test_cardflow_review.py` (14 tests, 11.37 s, OK).
`test_cardflow_youtrack.py` ran in parallel (14 tests, 33.48 s, OK), and the new
fixture regression also passed (1 test, 0.45 s).

At the end the report contains only the three failures already present in the sequential measurement,
listed above: `test_board_derive`, `test_card_branches`, and `FollowupArtifactTest`.
There are no new failures. `make test` correctly returned code **2** because of these three
existing failures — they are not skipped or turned into success.

Sources of the final check: `build/test-results/run-sk6n19ac/summary.json`, the module logs
in this directory, and
`pt-215-szybkie-testy-rownolegly-make.resume-K-54-PT-215-3.step/outside/outside.log`
next to the worktree. Fixing the three existing failures is not within the scope of speeding up
the tests in PT-215; they remain an explicit limitation of the full suite on the pinned base.
