# Agent work plan standard (Tide)

Version 1.5, 12.11.2026 (backward compatible with versions 1, 1.1, 1.2, 1.3, and 1.4: the additional columns are optional,
and plans with six columns work as before). **Change 1.5 (K-39, PT-376):** the result of an `outside` or
`denied` step with a command may have an optional `"exclusive": true` — a command requiring quiet (the "Step
contract" section); old results without the field work unchanged. **Change 1.4 (K-69):** the rule for ticket tests and close-out (K-62)
is described generally, for every technology — the commands come from the project test contract (`tests.changed`
with `{base}`, `tests.full`), and `make test-changed BASE=…` and `make test` are a plasma-tide example
(the sections "Ticket tests and review" and "Close-out", rules 1–9 in [testing.md](testing.md#general-rule-and-the-project-test-contract)).
The plan format, columns, and states do not change. This document is the reference to which the `CLAUDE.md` and `AGENTS.md` files of
projects point. The plan is read by tide-daemon: the board in the **Tide Sesje** (Tide Sessions) window, the coordinator's progress in the agents
widget, and the "Plany i tickety" (Plans and tickets) section in the morning report (description: [tide-daemon.md](tide-daemon.md#coordination),
rationale: decision 50 in [decisions.md](decisions.md)).

## Why

A coordinating session (Claude Code or Codex) that splits work into tickets and assigns them to subagents
records the plan in the repository. This makes the state of the work visible outside the conversation: what is to be done,
what is in progress, what awaits a user decision, what is after review — also for the next session that takes over the work.

## File

- Path: `docs/agents/plans/RRRR-MM-DD-<slug>.md` (RRRR = year; the date the plan was created, a slug of lowercase letters,
  digits, and hyphens), for example `docs/agents/plans/2026-10-26-release-candidate.md`.
- The plan is **committed** together with the work or in a separate commit `docs(plan): …`.
- **A project with AI documents "in Tide"** (version 1.1): when a project on the Tide board has `ai-docs: tide`,
  plans and specifications do not go into the project repository, but into the board repository:
  `~/DevWorkspace/tide-board/docs/<projekt>/plans/RRRR-MM-DD-<slug>.md` and `…/specs/` (another board:
  `$TIDE_BOARD_DIR`). The agent gets this directory through `--add-dir`, writes in it, but **commits nothing
  there** — the board repository is committed by tide-daemon. Tide counts such plans toward the project
  repository (`repo` in the board's `projects/<projekt>.md`): the Sesje board, the morning report, number checking,
  `check-plans`, and `next-id` see them together with plans from `docs/agents/plans/`. The launch command
  from the card gives the agent the right directory.
- One plan per one coordinator line of work. A finished plan stays in the repository with the status `done`.

## Frontmatter

Flat `key: value` pairs (YAML without nesting and lists), the first line of the file is `---`.

| Key | Value | Description |
| --- | --- | --- |
| `tide-plan` | `1` | the standard version; a file without it is not a Tide plan |
| `title` | text | the plan title, for example `Release candidate TOM-20` |
| `tracker` | `youtrack` \| `local` \| `none` | where the tickets come from (below) |
| `tracker-url` | an address or empty | for `youtrack`, for example `http://10.20.1.159:8111` |
| `tracker-project` | a key or empty | the project in the tracker, for example `LOG`, `LEGAL`, `APP5` |
| `ticket-prefix` | uppercase letters | the ticket ID prefix in this plan, for example `LOG`, `APP5`, `PT` |
| `coordinator` | `claude` \| `codex` | the tool of the coordinating session |
| `session` | a session ID or empty | the coordinator session ID (Claude Code: the journal file name, Codex: the thread ID), if known |
| `status` | `active` \| `paused` \| `done` | the state of the whole plan |
| `updated` | ISO 8601 time | the moment of the last plan change, for example `2026-10-26T21:13:00+01:00` |
| `card` | `K-<numer>` or absent | **optional (1.1)**: the Tide board card the plan serves, for example `K-12`; a plan launched from a card always has it |

**Tracker:**

- `youtrack` — tickets live in YouTrack (`tracker-url`, `tracker-project`); the plan indicates which
  of them are in this line of work, and Tide pulls titles, states, and links from YouTrack. When the YouTrack project
  has a key with synchronization enabled on the Tide board (PT-7), Tide writes the ticket state to YouTrack
  (`w-toku` and `review` → In Progress, `czeka` → To be discussed, `gotowe` → Fixed, `odrzucone` → Won't fix) —
  only on transitions, without overwriting manual changes (docs/tide-daemon.md, "Synchronizacja z YouTrackiem" (Synchronization with YouTrack)).
- `local` — **there is no external tracker**: ticket IDs are assigned by the plan (`PT-1`, `PT-2`, …), and the plan
  is the source of state for the Tide board and the morning report. `tracker-url` and `tracker-project` are empty.
- `none` — work without ticket tracking (for example a one-off task). The table may be empty or
  contain ordinary steps; Tide shows them, but does not match anything to a tracker.

**Card (`card`).** A plan launched from a Tide board card ("Co ma zrobić agent?" (What should the agent do?)) has `card: K-12`,
and the coordinator's branch contains the card ID in lowercase (`claude/k-12-okno-dockera`). By this pair Tide
links the card with the coordinator and its plan: a coordinator on a branch `…k-12…` gets the plan with `card: K-12`
(the project directory on the board holds the plans of many cards at once), and the card gets its state from the plan. A plan with a different `card`
is not the plan of this coordinator. A key without a sensible value (for example `card: 12`) is a plan error.
Every session writing a handoff for a specific card enters `card` before saving the plan.
A project plan without `card` can only be a hint in the "Mam plan" (I have a plan) entry; the user must choose it.
The card records the path of the chosen plan on the board. The takeover copies the table to the card journal
without changing the original plan or the numbers of existing tickets.

**Card specification** (next to the plan, in `specs/`): a file `k-12-<opis>.md` with the frontmatter `card: K-12`
and `status: szkic` (draft), changed to `status: gotowa` (ready) when the user accepts it. In the "specification
only" mode this is a signal for Tide: the card gets the tag "specyfikacja gotowa" (specification ready) and returns to the Backlog.
A specification in the project repository (`docs/agents/specs/`) is committed by the agent itself in this mode — only that
file, on the current branch, `docs(spec): K-12 <short English title>`, without push; on the board it is committed by tide-daemon.

## Ticket numbers: a single source

One number is always one task — across the whole repository and in the tracker. Therefore:

1. **One source of numbers per repository.** When the repository has a project in YouTrack, the plan has
   `tracker: youtrack`, and `ticket-prefix` equals `tracker-project`: the IDs in the plan are exactly the keys
   from YouTrack. You create a ticket in YouTrack first, and only then enter it into the plan. New plans
   do not use aliases (for example `LA-035` instead of `LEGAL-35`); old aliases work only through the migration
   map (`docs/agents/youtrack-migration/`) until the transition.
2. **The local prefix is single and free.** With `tracker: local` there is one prefix for the whole
   repository (recorded in CLAUDE.md/AGENTS.md) and it cannot be the key of any project in YouTrack
   (check the project list — read only).
3. **Local numbering is continuous in the repository.** A new number = the largest number with this prefix
   in all the repository's plans + 1 — in `docs/agents/plans/` of the main directory and of **every
   worktree** (`git worktree list`, also uncommitted ones) and on all branches
   (`git grep -h "^ticket-prefix\\|^| PREFIKS-" $(git for-each-ref --format='%(refname)' refs/heads) -- docs/agents/plans`).
   A plan in another worktree is invisible from the main directory, and still occupies its numbers.
   A new plan does not start from 1, and numbers are not reused, also after `odrzucone`.
   Plans of a project with documents "in Tide" (`tide-board/docs/<projekt>/plans/`) occupy numbers in the same way.
   **You take a new local number only from** `tide next-id <repozytorium>
   [--count N] [--title "…"]` (`--title` is the title of the row in the plan, in the plan's language): it computes the same under a lock and records the reservation
   in `~/.local/state/tide/ticket-ids.json`, so two parallel sessions will not get the same number.
   For a YouTrack prefix it refuses — the number is assigned by YouTrack. Count by hand only when the tool is missing.
4. **Before you create a project in the tracker or change a prefix**, check whether the key is already
   used in the repository's plans or in the tracker. On a collision stop and ask the user.
5. Tide checks the same and shows a warning on the Sesje board and in the report: the same ID with different
   titles in two plans, a local prefix equal to a YouTrack project key, a `youtrack` plan prefix
   different from `tracker-project` without a migration map, and, more mildly: a number reserved for a different
   title (often the same ticket in another language) and a new plan that restarts numbering. Before assigning a new number
   or creating a project in the tracker, the coordinator runs `tide check-plans`
   (exit code 1 is a collision — stop and ask the user).
   The locations of the standard and the tools are given by `tide info --json`; the commands use the installation's resources.
   In a checkout the equivalents are `make next-id REPO=…` and `make check-plans`.

## The table

Under the frontmatter, one Markdown table with six required columns:

```markdown
| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| TOM-140 | Distribution guard dla kompaktowej dystrybucji | w-toku | `tom-140-guard` | implementer | po review: 1 MAJOR |
| TOM-146 | Eksport raportu do CSV z filtrami | todo | | | |
```

| Column | Description |
| --- | --- |
| `Ticket` | the ticket ID (`PREFIKS-numer`), the same as in the tracker or assigned by the plan |
| `Tytuł` | a short title (Title); with YouTrack it may differ from the title in the tracker (the plan wins) |
| `Stan` | one of (State): `todo`, `w-toku`, `review`, `czeka`, `gotowe`, `odrzucone` |
| `Gałąź` | the work branch (Branch; may be in `` `…` ``), with the ticket ID in lowercase |
| `Agent` | who is working: the subagent type (`implementer`, `code-reviewer`…) or empty |
| `Uwagi` | one sentence (Notes): the reason for waiting, the review result, a decision |

**Version 1.2:** at the end of the table, the columns `Klasa`, `Review`, `Blokery` may be added, in this order.
You can use the first, the first two, or all three. The Sesje and report parser reads both variants;
six-column plans keep their existing meaning.

| 1.2 column | Description |
| --- | --- |
| `Klasa` | `lekki`, `standard`, `mocny`, or `lokalny` (light, standard, strong, local); without the column it defaults to `standard` when approving a card |
| `Review` | the name of the reviewer from the executor catalog; when empty, Tide picks another model of the same provider (with the project policy `other-family` — an executor of another family); checking the plan applies the policy of the ticket's project (risk-based review, below) |
| `Blokery` | IDs of tickets from this plan, comma-separated; before approval they may take the form `#1`, `#2` |

**Version 1.3:** after `Blokery` an optional column `Projekt` may be added (10 columns). The parser reads plans with 6, 7, 8, 9,
and 10 columns; the meaning of the 1.2 columns does not change, and the presence of the column is decided by the header, not by an empty last
cell (a plan with 9 columns does not become a plan with a `Projekt` column).

| 1.3 column | Description |
| --- | --- |
| `Projekt` | the slug of the Board project the ticket belongs to; an empty cell or no column = the card's project. An unknown slug does not break reading by Sesje and the report; it is reported only by the proposal check |

Blockers `#n` in a plan proposal may point to any ticket of the whole shared card plan, also from another
project. In an approved plan a blocker may be an ID from another card of the group.

A card plan proposal has working numbers `#1`, `#2`, and so on, and a Markdown file `tickets/1.md`,
`tickets/2.md`, and so on next to the plan file. Each sheet contains at least one criteria item
`- [ ] …`; a separate "Kryteria akceptacji" (Acceptance criteria) heading is optional, in line with the local
`to-tickets` format. The service checks the executors, sheets, blockers, cycles, and the single table before
showing the plan for approval. The `PREFIKS-n` numbers are created only after "Zatwierdź plan" (Approve plan). The card
journal records the map `#n → ID`, and the approved plan and sheets with IDs go onto the card branch. The plan in that
worktree is read by Sesje, the report, and the number check together with the repository's other plans.
The service updates `Stan` and `updated` in the approved plan after delegating, a step result,
a review, and a ticket merge.

**Follow-up scope sketches (`followups/`).** A proposal package includes the Markdown files next to the plan,
`tickets/*.md`, and `followups/**/*.md`. The sketch subdirectories keep their structure. The service freezes
the full content before handing over the proposal; the review gets a copy of the package in `in/proposal/`, and a fix
additionally starts with the copies in its output. A later edit of the author's output does not change the snapshot.
This applies to background planning, the Console, and a proposal indicated from a file. Each proposal keeps
its own package, so a correction does not overwrite the material of the previous proposal.
The read copy keeps the original names and content. In the fix output the plan is named `plan.md`,
and the links from the sketches point to this name; when fixing an approved plan the sheets and links to them
use the already assigned IDs instead of working numbers. The sketches keep subdirectories, fragments, and URL parameters.
The existing sheets in the output of a fix of an approved plan have the current content from the base plan,
including the user's edits in the panel; the original snapshot in `in/proposal/` stays unchanged.

Regular UTF-8 files are allowed, at most 512 KiB per file and 16 MiB for the whole package.
Links to files and directories are rejected. Local Markdown links to sketches (also reference
links and targets with a fragment) must point to an existing Markdown file in the package; going outside the
artifact directory or a missing target aborts the check before publishing an incomplete proposal.
The service does not attach arbitrary files indicated by links. A freeze error gives a step error result.

After approval the sketches go into the card branch commit in `followups/` next to the plan. The links of the sheets
`../followups/…` are recomputed relative to the target sheet directory. Links from the sketches to the plan
and the sheets are recomputed according to the dated plan name and the assigned ticket IDs, also in subdirectories,
keeping fragments and URL parameters. A local sketch target must belong to the published set:
the plan, the plan's ticket sheets, or `followups/`; other targets are rejected before publishing the proposal.
Subsequent plan state commits
keep the sketches, the text outside the table, and the appended ends of the sheets. The first successful commit of an approved
fix takes the text outside the table and the sketches from the new frozen proposal. While waiting
for a decision the state commits still use the package of the previously approved version:
they do not overwrite the sketches with the content of the fix or add its new sketches. Later state updates
keep the text from the worktree, including manual additions. The threshold of the first successful write concerns the version
actually approved; rejecting an unapproved fix does not invalidate an earlier write.
A sketch describes a scope for separate
planning; saving it does not publish a card and does not confirm the availability of the described feature.

## The card flow run by Tide (1.2)

A card on the Board starts planning or a single ticket. Tide keeps the card journal, creates the card branch
and separate ticket branches, runs the steps, and shows decisions in the card panel. "Lekkie" (Light) planning creates a proposal
in the background from the card's description and specification. "Pełne" (Full) planning currently takes place in the Console; including it in this flow
is the second stage. "Mam plan" (I have a plan) takes over the indicated plan
without saving changes in its repository. A plan handed over for a specific card has `card: K-<numer>`; a plan without
this field can be a hint, not an automatically chosen card plan.

Full planning in the Console runs a `grill-with-docs` conversation (questions and recording decisions), then `to-spec`
and `to-tickets`. Light planning in the card flow prepares `spec.md`, `plan.md`, and sheets without
questions to the user during the step; after an independent review it shows the plan for approval.

**Roles.** The planner prepares the table and sheets. Another executor checks the plan before it is shown to the user;
when it finds gaps, the planner gets one fix. The ticket author performs a vertical slice in a separate worktree,
and the reviewer meets the rules of risk-based review (below) and records the verdict. The user approves the plan or the difference after sending it back with comments;
they may also change the executor or the sheet of a ticket that has not started yet. Tickets without blockers can run
in parallel, with a provider step limit; a blocker starts after the predecessors are merged into the card branch.

**The step contract.** Tide hands over a sheet with the card and ticket identifier, the branch, the working directory,
the constraints, and the path of the output directory. The author writes the note `note.md` there, and at the end `result.json`:
`{"result":"done","notes":["note.md"]}`, `{"result":"question","question":"…"}`,
`{"result":"denied","command":"…"}`, or `{"result":"error","reason":"…"}`. An `outside` or `denied` result
with a performance measurement command (or another whose result depends on the machine load) has `"exclusive": true`: Tide
runs it only on a quiet machine and does not start other heavy commands meanwhile (a wrong field type = `false`).
The reviewer in addition
writes `review.md` and a result, for example `{"result":"done","verdict":"APPROVE","blockers":0,
"majors":0,"nits":0,"notes":["review.md"]}`; `REQUEST_CHANGES` requires a positive number of `blockers`
or `majors`. A missing valid result is a step
error. A question, a consent, and a retry go through the card journal;
a resume keeps the worktree and the changes.

**Ticket tests and review.** The rule applies in every technology. Before handing in a ticket and fixes, the author
runs the "tests selected for the changes" from the project test contract (`tests.changed`, with `{base}` = the ticket start point
on the card branch) and records the command and the result in the note. Without an approved contract the command comes from detection
in the `Makefile`, and then from the project rules (`CLAUDE.md`/`AGENTS.md`); rules 1–9 (sources, approval, consents, `legacy`,
close-out, rerun) are in [testing.md](testing.md#general-rule-and-the-project-test-contract). In plasma-tide this is
`make test-changed BASE=<punkt startu ticketu na gałęzi karty>`. The sheet hands over the pinned SHA from the result of creating
the ticket branch; a later move of the card branch does not change this base. The reviewer checks the successful result for the submitted changes
and does not demand separate full project tests (in plasma-tide `make test`). A full run is not a criterion of the ticket or of
the review. The rule replaces
the requirement of the full `make test` in the sheets of existing plans (including K-37, K-38, and K-55), without rewriting the
plans. If the `test-changed` map selects the full suite, the author runs it without an additional `make test`.

The `legacy` exception (rule 7, decisive in [testing.md](testing.md#general-rule-and-the-project-test-contract)):
without an applied contract — a `test` target without `test-changed`; with an approved contract — only when `changed` is a plain
`make test-changed …` and the branch has `test` without `test-changed`. In `legacy` the full `make test` is the ticket test,
and the reviewer checks its result in the note. Details: [older branches](testing.md#older-branches-without-the-test-changed-target).

The rules for settling a failed run and a waiver are described in the
[testing documentation](testing.md#test-exceptions). A waiver does not change the result to successful.

**A question with ready options.** `{"result":"question","question":"…"}` may additionally have
`"options":[{"title":"…","effect":"…","recommended":true}]`: from 1 to 4 items, `title` non-empty (up to 120 characters),
`effect` — one sentence about the consequence of the choice (up to 400 characters, optional), `recommended` — at most one item.
The decision window shows the options as cards (the recommended one first, none selected by default), and Tide always
adds at the end "Inną odpowiedź…" (Other answer…) — the agent does not provide it. A choice resumes the step; the option's title and effect go into
`resume_context`. Invalid or missing `options` (an empty title, more than 4, several recommended, a repeated
title, a wrong type) are not a step error: the window then has just the answer field. A question without `options` works as before.
The author and the reviewer do not push or merge.

**A question about the base (PT-208).** Every step handoff contains the current full ref and the pinned base SHA
or an explicit `unknown`. A missing file alone does not prove a wrong base or allow creating a replacement,
running checkout, rebase, or reset. After checking the evidence the step may write:

```json
{"result":"question","question":"Czy zachować bazę?","baseProposal":{"ref":"refs/heads/feature/example","evidence":"Sprawdzona historia wskazanego pliku…"}}
```

`baseProposal` is optional. `ref` must be a full locally known ref `refs/heads/…` or
`refs/remotes/…` (up to 1024 characters), `evidence` non-empty plain text (up to 4000 characters).
The service checks the candidate in the card's project repository, without fetch or changing git. An invalid
proposal stays as information on an ordinary question; old questions and `options` keep working.

A valid proposal pauses new steps and the integration of this card's results. Processes may finish,
and their results stay recorded and unmerged. The question and the block return after a restart; the waiting does not
hold the repository lock and does not block other cards. `ResolveCardStep` accepts `choice: answer`,
`step`, and a structured `baseDecision: keep` or `change`, regardless of the answer text.
"Zachowaj" (Keep) records an ordinary answer and unblocks continuation. "Zmień" (Change) records waiting (`waiting: true`,
`changed: false`), leaving the question and the Keep option. It performs no git operation and does not
confirm the change; the preview is delivered by PT-201, activation by PT-202, consequences and confirmation by PT-213.

**Correction preview (PT-201).** `/Board PreviewCardBaseChange(id, request)` accepts a full `baseRef`
and an optional `baseCommit` (for a remote ref the SHA is required), or the `step` of an unresolved proposal;
an empty `{}` picks the first pending base decision. Validation is the same as at start,
without fetch and without a default HEAD. The response contains `oldBase`, `newBase`, `baseVersion`, `branches[]`
with the recorded start points and commits, `processes[]`, local changes and ignored paths,
`blockedReasons[]`, `invalidations[]`, `followup`, and an instruction. The evidence is plain text.

An explicit user choice in the full card calls `RequestCardBaseChange(id, {baseRef, baseCommit})`.
It records only the `card.base-requested` decision, blocks new steps and integration, keeps the existing
questions, and returns the same preview as the agent's proposal. `decisionStep` identifies the decision;
`ResolveCardStep` with this identifier and `baseDecision: keep` unblocks the card, and `change` only
opens the preview. Closing/Esc records no answer and does not stop processes.

`previewId` is a fingerprint of the exact SHAs, the base version, the whole journal/map, the logical content of the indexes,
the status, the metadata of local data, and the processes. Both reads are light: without hashing file contents.
We collapse ignored directories with `ls-files --others --ignored --exclude-standard --directory`;
the fingerprint includes their stat, without computing the interior. A change to a file's content inside such a
directory need not change the identifier. It is a fingerprint of the qualification of an empty generation, with no right to copy or delete
local data; PT-202 must keep the old worktrees together with their content. A limit of 4096 metadata
reads per worktree (also for untracked nested repositories) gives a refusal
`unknown-local-state` when exceeded. The response contains at most 20 ignored paths per branch,
`ignoredCount` counts the collapsed paths/directories, and `ignoredTruncated` marks that the list was shortened.
The panel shows at most 10 and the number of the remaining ones. It is not the number of files in ignored directories.
The evidence, refusal reasons, and processes of previous assignments have `previousOrder: true`; the check stays conservative.
PT-202 must recompute the preview under a lock before the operation,
require `canChange: true` and equality of the identifier; every detected difference requires a fresh decision. The identifier
by itself is not consent. `canExecute: true` makes available a separate final confirmation of the empty correction;
the panel sends `choice: change-base` with the exact ref/SHA and `previewId`. Activation invalidates old
consents, sessions, and close-out; a new step starts in a new directory and a fresh session. The new work is subject to
the usual tests and review, and the card requires a new summary. Historical data remain readable.
An unknown boundary or process state means a refusal. A finished step and a close-out on the base SHA without a new
commit are metadata, whereas every documents/code/WIP/merge commit rules out an empty correction.

The v1 correction is limited to a card without contribution commits and active processes, usually "Od razu" (Immediate) before the
first work. A plan commit after Light/I have a plan/Full, WIP, and work after review (K-49) require refusing the
correction and keeping the data. Goal point 4 and stories 35–37 remain partial. The requirements point to the sketch
[K-50-replay-code](agents/plans/followups/replay-code.md), verified in PT-201 and attached
to the approved plan. This key alone does not mean a published card or an available replay. "Zachowaj" (Keep)
lets work continue; the alternative is waiting and separately planning the transfer.

**Plan review.** The `plan-review` step writes `plan-review.json` in the output directory:
`{"verdict":"APPROVE"|"REQUEST_CHANGES","findings":[{"severity":"blocker"|"major"|"nit","text":"…","tickets":["#5"]}]}`.
`severity`: `blocker` — the plan cannot start without a fix, `major` — an important change before start, `nit` — a trifle;
`tickets` — numbers of the plan's tickets the comment concerns (may be an empty list). With `APPROVE` and no comments
`findings` is `[]`. The old format (a single string in `findings`) is still accepted as one comment with no severity.
An unknown severity, a comment without text, or `tickets` that is not a list of strings is a format error. This changes only the review
result; the plan file format and the approval rules stay unchanged.

**Close-out.** Tide merges accepted tickets into the card branch. Before writing the summary, the `close` step
runs once the "full tests at close-out" from the project test contract (`tests.full`; without an approved contract
the detected `make test`, and then the project rules — rule 8 in [testing.md](testing.md#general-rule-and-the-project-test-contract);
in plasma-tide the full `make test`) on this branch and records the result in the summary. Rerunning failed modules does not change
the result of the first run (rule 9). It handles failed tests
like other close-out problems: the result `error` with a description of the failures instead of `done`, without merging into the main branch.
Closing despite a failed full run requires an explicit settlement of the waiver and a user decision
according to the [test exception rules](testing.md#test-exceptions).
A command requiring consent or running outside the sandbox uses the ordinary `denied`/`outside` contract;
a resume uses the result and does not repeat a successful run without new changes.
`check: manual` adds a list "Co przetestować
ręcznie i jak" (What to test manually and how); `check: auto` means no such list. In both cases **merging the card branch into the main branch requires
a separate user command**. Statuses and numbering come from the journal and the approved plan; existing
six-column plans and old coordinator sessions keep their existing reading. The plan file in the project repository
is not corrected merely by choosing "Mam plan".

**States:**

| State | Meaning | Column in Tide Sesje |
| --- | --- | --- |
| `todo` | in the plan, nobody is working on it | Do zrobienia (To do) |
| `w-toku` | implementation or fixes | W toku (In progress) |
| `review` | the reviewer is checking the changes | Review |
| `czeka` | a user decision or answer is needed | Czeka (Waiting) |
| `gotowe` | done (after review or accepted) | Gotowe (Done) |
| `odrzucone` | we deliberately do not do it | Gotowe (muted, outside progress) |

Update the table and `updated` **on every delegation, subagent result, review, and question to the
user**. The sections under the table (notes, risks, decisions) are free text — Tide does not read them.

## Delegation convention

Tide links subagents' work with tickets by ID, so:

1. **The subagent description starts with the ticket ID**: `TOM-140: implement guard`, `TOM-140: review`,
   `TOM-140: review fixes`. A Codex subagent has the ID in the task name (`task_name`, for example `tom140_guard`),
   in the nickname, and in the command.
2. **The branch contains the ID in lowercase**: `tom-140-guard`, `feat/tide-7-board`, `codex/la-028-…`.
3. **A question to the user names the ticket ID** (`TOM-141: przenieść backlog do osobnego projektu?`)
   — then the ticket goes to the "Czeka" (Waiting) column with the text of the question.
4. The reviewer has the word "review" in the description or type (`code-reviewer`, `Review TOM-140 …`), and their report
   gives the numbers `BLOCKER` / `MAJOR` / `NIT` and the verdict (`APPROVE` or `REQUEST CHANGES`).

## Risk-based review

The rules agreed on 6.11.2026 (K-71, PT-259), decision 96 in the [decision log](decisions.md); since 10.11.2026
(decision 96 of 10.11, K-88/PT-330) the choice of the reviewer is **a project policy, not a Tide rule**.
The only fixed Tide condition is a new, separate reviewer session; the author does not review their own work in the same session.

The Board project policy `reviewIndependence` (project file: `review-independence`; a description of the field
in [tide-daemon.md](tide-daemon.md#board)):

- `free` — **the default** (also a project without the setting): any reviewer, also the same model and the same reasoning
  effort (for example Opus → Opus), always in a new session;
- `other-model` — another model or the same model with a different reasoning effort, for example Codex strong ↔ standard;
- `other-family` — a reviewer of another family (a provider/program from the catalog, not an executor name) than the author
  and the fix executors; optionally only for classes from a list (`review-independence-classes`, for example `mocny`).

Tide enforces only the configured policy: when checking the plan, editing a ticket in the window, the "Od razu" (Immediate) start,
choosing the fix escalation target, and in the gate before the review start. Changing the reasoning effort alone does not mean changing
the model family. When the policy requires a reviewer that does not exist (no provider, a limit), the card shows the reason
and asks the owner: change the reviewer, wait, or relax the project policy.

The list below is **a recommendation for projects with the `other-family` policy**, not a Tide rule. In such a project
a reviewer **of a different model family** than the author (for example Astra / Claude for a Codex author) is recommended for:

- permission and security boundaries;
- destructive operations;
- migrations of durable state;
- historical resumes and the validity of consents;
- concurrent git operations, locks, and crash recovery;
- changes to test selection and acceptance gates;
- integrations changing the shared card contract.

In a project with such a policy, before merging the risk class and the choice of the reviewer are checked; an automatic choice
of the executor does not release from this duty. The policy requirement does not automatically fall back to the same model with a different
reasoning effort or to no review — the owner decides. In every project the review remains mandatory
and ends with `APPROVE` without BLOCKER and MAJOR.

## Coordinator fixes outside a card

This is an exceptional path for urgently unblocking work or removing a regression. One fix removes
one cause and covers at most **150 changed lines of production code in 3 files**,
counted together for the whole fix (additions and deletions), without splitting into commits to get around the limit.
Tests and documentation do not raise the production code limit, but enter the scope of review.
Features, contract changes, and all the risk classes from the previous section go only through a card
with review **before merging**, also when the fix is urgent or small.

Before delivering the fix, a test reproducing the problem is required (failing on the pinned base,
passing after the fix) and regression tests selected for the change. The results are subject to the test exception rules.
The coordinator records an entry in the [fix register](agents/coordinator-fixes.md):

- the cause, urgency, and scope, and the delivery time;
- the SHA of the base and of the fix;
- the exact commands, the results on the base and after the fix, and the regression;
- the affected cards and the way of delivery to each of their branches (the merge or transfer SHA);
- the way of rollback and the state of the actual rollback;
- the deadline, the reviewer, the report, and the result of the independent review after the fact.

The independent review must finish **within 24 hours of delivering the fix**, and earlier
if a card relying on this fix is to be merged. An overdue review suspends further merges
through this path. A BLOCKER or MAJOR found after the fact means stopping the dependent action and fixing
or rolling back the fix; the way of settlement goes to the register. Merely ordering a review is not its result.
This path gives no consent to push or merge into the main branch without an explicit user command.

## Finishing the work (1.1)

1. **AI review is mandatory**: a ticket and the whole work end only after a review with the verdict
   `APPROVE` (without BLOCKER and MAJOR).
2. **Manual test only on request**: when the launch from the card has "Test ręczny po zakończeniu" (Manual test after completion)
   (the card `check: manual`), the coordinator after review adds to the plan, under the table, the section
   "Co przetestować ręcznie i jak" (What to test manually and how) — the steps and the expected result of each — does not merge the branch, and in the last
   message says that the work awaits the user's manual test. Without a manual test (`check: auto`,
   the default) the work ends after review. In a flow run by Tide the main branch is merged
   only by the user with a separate command, also with `check: auto`.

## How Tide determines the state

From the subagents' work (coordinator journals, read only): an unanswered question about a ticket → `czeka`;
a reviewer is working → `review`; another subagent is working → `w-toku`; the last review without BLOCKER/MAJOR →
`gotowe`; a review with BLOCKER/MAJOR → `w-toku` ("do poprawy" (to fix)); fixes finished after review → `gotowe`.
The plan wins for `odrzucone`, for tickets nobody worked on (`todo` and others), and when
its `updated` is newer than the last work on the ticket. YouTrack gives titles and addresses; its
state counts when there is neither a plan nor subagent work, or when the ticket is closed there.

## Example

```markdown
---
tide-plan: 1
card: K-12
title: Release candidate TOM-20
tracker: youtrack
tracker-url: http://10.20.1.159:8111
tracker-project: LOG
ticket-prefix: TOM
coordinator: claude
session: e367f096-eb51-4f0d-9e0a-e93893d415c4
status: active
updated: 2026-10-26T21:13:00+01:00
---

| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| TOM-139 | Unikalne filtry w wyszukiwaniu | gotowe | `tom-139-filters` | implementer | review OK |
| TOM-140 | Distribution guard dla kompaktowej dystrybucji | w-toku | `tom-140-guard` | implementer | po review: 1 MAJOR |
| TOM-141 | Migracja epików z Lineara | czeka | | implementer | pytanie o backlog LA-* |
| TOM-146 | Eksport raportu do CSV z filtrami | todo | | | |

## Notatki

Kolejność: najpierw TOM-140 (blokuje RC), potem TOM-146.
```

## ID migration map

When the repository has `docs/agents/youtrack-migration/*.csv` (columns `source_key` or `source_id`
and `youtrack_id`, optionally `kind`), Tide groups tickets by the new key: for example `TOM-10` (Linear) →
`LOG-128` (YouTrack), `LA-001` → `LEGAL-1`. Rows with a `kind` other than `ticket` are skipped.
