# Work tracker: files in the repository

Specifications, plans, and card flow tickets are files in this repository. GitHub Issues in the private
repository `lakomytomaszdev/plasma-tide` remain available for other reports (the `gh` CLI), but they are not
the source of card flow tickets or of the state of its plan.

| Kind | Location |
| --- | --- |
| Specification | `docs/agents/specs/YYYY-MM-DD-<slug>.md` |
| Plan and ticket state | `docs/agents/plans/YYYY-MM-DD-<slug>.md` |
| Ticket sheets | `docs/agents/tickets/<data>-<slug>/<kolejność>-<id>-<slug>.md` |
| Ticket result | `docs/agents/tickets/<data>-<slug>/results/<id>.md` |

The plan is the source of truth for state. Update the table and `updated` when delegating, on a result, on a review, and when asking
the user a question. The rules for format, roles, and card flow are described in the [plan standard](../agent-plan-standard.md).

Local numbering is continuous across the whole repository. Before a new number, run `make check-plans`, and
reserve the number with `make next-id REPO=/home/user/DevWorkspace/plasma-tide TITLE="…"`; do not compute it by hand.
A ticket in YouTrack keeps the key assigned by YouTrack, if the project on the board has such a tracker.

When a tool's instructions say "publish to the tracker" for a card flow, it means saving the proper file,
updating the plan, and committing on the working branch. When they say "fetch the ticket", read the sheet from the
`docs/agents/tickets/` directory and the related plan row. Do not assume a GitHub Issue for the same work.
