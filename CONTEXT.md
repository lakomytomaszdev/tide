# Tide

A custom working environment for AI agents on KDE Plasma: a Board of cards, agent sessions, models (cloud and local)
and reports. The glossary applies in code, documentation, plans and instructions for agents; the rationale for
decisions is in `docs/decisions.md`. The application UI is still in Polish, so each entry is named by its Polish
term followed by the English term used in these docs.

## Language

### Board

**Repozytorium tablicy** (Board repository):
A git repository with cards, projects, assignment templates and card journals; only Tide commits to it.
_Avoid_: tide-board in texts for the user

**Tablica** (Board):
A single place with things to do across all projects, arranged in columns.
_Avoid_: board in texts for the user, kanban

**Karta** (Card):
A thing to do on the Board (a feature, a fix, an idea); usually spans several tickets.
_Avoid_: zadanie (task), issue, task

**Kolumna** (Column):
A card's place on the Board: Backlog, "Do zrobienia" (To do), "W toku" (In progress), "Czeka na mnie" (Waiting for me), "Do sprawdzenia" (To check), "Gotowe" (Done) and the hidden "Archiwum" (Archive).
Backlog, To do, Done and Archive are moved by hand; the rest is determined by the card's state.
_Avoid_: card status

**Znacznik karty** (Card tag):
A short description of the card's state within its column, e.g. "plan do zatwierdzenia" (plan awaiting approval) or "fala 2 z 3" (wave 2 of 3).
_Avoid_: sub-column, phase

**Panel karty** (Card panel):
The card details view in the Tide window: plan awaiting approval, progress, questions, checking.
_Avoid_: card details, sidebar

**Zlecenie** (Assignment):
The user starting work on a card, always through a single entry.
_Avoid_: launching an agent, launch

**Wejście** (Entry):
The way an assignment is started: "Planowanie" (Planning), "Mam plan" (I have a plan), "Od razu" (Immediate) or "Tylko specyfikacja" (Specification only).
_Avoid_: mode (the mode is Full or Light)

**Tryb** (Mode):
The depth of planning: "Pełny" (Full; a conversation with the user) or "Lekki" (Light; no conversation).
_Avoid_: entry

### Card flow

**Przepływ karty** (Card flow):
Work on a card driven by Tide, in which AI sessions are short steps.
_Avoid_: koordynacja (coordination; this is the former model with a long coordinator session)

**Krok** (Step):
One short AI session with a single task in the card flow: planning, plan completion, plan fix, plan review,
ticket, review, fixes, conflict or close-out.
_Avoid_: subagent, run, job

**Krok w tle** (Background step):
A step without a terminal window; the user enters it only on request.
_Avoid_: headless session, background task (that means model tasks, e.g. the report)

**Wynik kroku** (Step result):
How a step ended: done, question, no approval, error, limit or stopped.
_Avoid_: step status

**Planujący** (Planner):
The executor of the planning step; proposes a plan but assigns nothing.
_Avoid_: coordinator

**Koordynator** (Coordinator):
A long AI session that divides the work itself and assigns it to subagents — the former model, kept for cards started before
the card flow.
_Avoid_: planner

**Plan do zatwierdzenia** (Plan awaiting approval):
A card plan proposal waiting for the user's decision: approve, send back with remarks or enter a conversation.
_Avoid_: plan draft

**Propozycja planu** (Plan proposal):
A plan saved by the planner (or taken over) before the user approves it; tickets have working numbers.
_Avoid_: plan draft

**Sprawdzanie formatu** (Format check):
Tide's check of a plan proposal without a model: known executors, blockers, no cycles, ticket sheets with criteria.
_Avoid_: validation in texts for the user, review

**Review planu** (Plan review):
A check of the plan proposal by an executor other than the planner, before the user sees it.
_Avoid_: format check

**Przejęcie planu** (Plan takeover):
Linking an existing plan (e.g. from a handoff from another session) to a card through the "Mam plan" entry.
_Avoid_: import, handoff

**Ticket z innego planu** (Ticket from another plan):
A ticket in the `todo` state from another plan of the same repository, which the plan proposal accepts under the same title with its number preserved and the `fromPlan` field; a different title or a state other than `todo` is rejected with a message.
_Avoid_: duplicate, ticket import

**Fala** (Wave):
A level in the card's blocker graph: tickets separated from the start by the same number of blockers; they can run in parallel.
_Avoid_: stage, sprint

**Bloker** (Blocker):
A ticket that must be done before another ticket starts.
_Avoid_: dependency in texts for the user

**Gałąź karty** (Card branch):
The branch that card tickets land on after review; only the user moves it to the main branch.
_Avoid_: coordinator branch, integration branch

**Baza karty** (Card base):
The starting point of an assignment saved in the journal: the full local or remote ref (`baseRef`, `refs/heads/…`
or `refs/remotes/…`), the branch name (for a remote one together with the remote name)
(`baseBranch`) and the pinned commit (`baseCommit`, SHA). Moving or deleting the ref does not change the saved base.
_Avoid_: current HEAD, today's main as a guess of the historical base

**Ostatnie bazy repozytorium** (Recent repository bases):
Up to 10 distinct full refs from the newest successful `card.started` or `card.base-changed`, reconstructed
from the Board journals. Worktrees of one repository share the shortcuts. A shortcut explicitly selects the current SHA of
an existing ref; it does not change the default main branch or the persisted resume base.

**Pierwotna baza** (Initial base):
The description of the base at the moment the assignment started (`initialBase`); a later correction does not change it.
_Avoid_: current base

**Ostatnie cele scalenia repozytorium** (Recent repository merge targets):
Up to 10 distinct existing local full refs from the newest `card.merged` or the old
`card.main-merged` with a known target. The projection of the Board journals is shared by the worktrees of one
repository. A shortcut opens a preview with the current SHA and the same restrictions as the target list;
it does not replace the base as the default target and does not grant consent to merge.

**Wersja bazy** (Base version):
A non-negative integer `baseVersion` from the journal, separate for each assignment. A new start has 1;
a historical one without a saved version has 0. Only an effective `card.base-changed` increases it by one.
The value 0 does not prove knowledge of the base and does not entitle to a correction.
_Avoid_: SHA as a version, separate operation counters

**Nieznana baza** (Unknown base):
No reliable ref or SHA in the journal (`status: unknown`). We do not guess it from the current git;
the historical intent and the result of creating the branch must prove both values.
_Avoid_: default historical base

**Cel scalenia karty** (Card merge target):
An explicitly chosen local branch (`targetRef`, full `refs/heads/…`) and its current commit (`targetCommit`)
read in the preview. The default target is the card's local base. For a remote base the only default is
a single local branch tracking exactly that full remote/ref; several candidates require a choice.
Without a candidate you can choose to create a local target from the current SHA of the remote base. For an unknown base
the user chooses a local target; the named main branch is then a suggestion, not the historical base.
Choosing a target does not change the base and is not consent to merge. Consent concerns the ref and SHA from the current preview.
The target list, the preview and the merge use the repository saved at the start of the assignment, also after the card's project changes.
_Avoid_: main as the name of every target, the short name of a remote base as the default local target

**Domknięcie** (Close-out):
The last step of a card: a summary of the work and a list of things for the user to check.
_Avoid_: finalization, merge

**Dziennik karty** (Card journal):
An ordered record of all card events; the only source of its state in the card flow.
_Avoid_: log, history, plan (the plan is a view of the journal)

**Zdarzenie** (Event):
One recorded change in the card journal, e.g. a step start, plan approval, a review verdict.
_Avoid_: entry, event in texts for the user

**Opiekun karty** (Card steward):
A background step of the `caretaker` kind, which Tide starts by itself on a card problem before asking the user; it checks the problem and answers with a single action from Tide's list. The steward never handles a step of its own.
_Avoid_: repair agent, supervisor, auto-fix

**Problem** (Problem):
A card situation that the steward checks (e.g. a step error after an automatic retry), with a fixed id without an event number (`error:<first step of the retry chain>`); one retry chain of a step has at most one problem at a time.
_Avoid_: incident, alarm, error (an error is one of the kinds of problem)

**Działanie opiekuna** (Steward action):
One steward step on a problem and its action (e.g. handing the decision to the user); actions are counted from the user's last decision or the resolution of the problem. An action outside Tide's list or the lack of a valid result is a failed action.
_Avoid_: attempt (an attempt is a retry of a step), intervention

### Tickets

**Ticket** (Ticket):
A unit of work in a card plan with its own ID (e.g. `PT-58`, `LOG-240`); before approval it has a working number (`#1`).
_Avoid_: task, issue, card

**Projekt ticketu** (Ticket project):
The slug of the Board project the ticket belongs to; the plan saves it in the `Projekt` column (standard 1.3), and an empty cell or a missing column means the card's project.
_Avoid_: repository, workspace

**Projekty zlecenia** (Assignment projects):
The Board projects in which the work of one assignment is to be done: the card's project and additional projects chosen in the assignment
window (the card's `projects`, at most 6 in total). Planning sees their repositories (the card's project with write access, the others
read-only) and produces one plan with a `Projekt` column; "Od razu" (Immediate) and "Mam plan" (I have a plan) work in a single project.
_Avoid_: additional repositories, workspace, multi-repo

**Grupa kart** (Card group):
Cards created from one multi-project plan: the assignment card and one card for each other assignment project
with tickets; each has its own journal, card branch and numbers, and blockers may point to tickets of another card in the group.
_Avoid_: epic, collective card, parent project

**Zgłoszenie** (Issue report):
A ticket or card in an external tracker (YouTrack), e.g. `LOG-240`.
_Avoid_: issue, task

**Kartka przekazania** (Handoff sheet):
The short input of a ticket step: what to build, acceptance criteria, blockers, the needed fragment of the specification and the
"Dla recenzenta" (For the reviewer) part.
_Avoid_: handoff, prompt, kartka (ticket sheet; without qualification when a card is meant)

**Notka wyniku** (Result note):
A description of what a ticket step did; goes to the reviewer, to blocked tickets and to the issue in the tracker.
_Avoid_: report, summary

**Przekazanie techniczne** (Technical handoff):
The state of a stopped step saved by Tide without a model (code changes and the agent's last messages), so that
another executor can finish the step.
_Avoid_: handoff (without qualification)

### Models and executors

**Program** (Program):
The agent tool that Tide starts: Claude Code or Codex CLI.
_Avoid_: agent, tool

**Punkt** (Endpoint):
The model server the program talks to: the provider's cloud, this computer, the Mac or another server compatible with the OpenAI
or Anthropic API.
_Avoid_: endpoint in texts for the user

**Dostawca** (Provider):
Who supplies the model: Anthropic, OpenAI, local or other.
_Avoid_: provider in texts for the user

**Wykonawca** (Executor):
A program with a model and an endpoint under a single name from the catalog, e.g. "Sonnet", "Codex", "Lokalny · qwen3-coder:30b" (Local · qwen3-coder:30b).
The same executor can fill different roles.
_Avoid_: agent, implementer, model (the model is part of the executor)

**Rozpoznawanie pytania** (Question detection):
Determining whether the agent at the end of a turn asks the user for anything (even without "?": "run it and paste me the result", "Let me
know."). Two stages: rules on the text, and for doubtful endings a local model from the `classify` role; until the model's result
the rules count, and without a model the rules alone. The content goes only to the endpoint of that role.
_Avoid_: question detection, question classifier in texts for the user

**Autor ticketu** (Ticket author):
The role of the executor who does the ticket (the ticket step and fixes).
_Avoid_: ticket executor, implementer

**Recenzent** (Reviewer):
The role of the executor who does an independent review of a ticket or plan in a new session. For ordinary tickets
the same model with a different thinking strength is acceptable (Codex strong ↔ standard). For the risk classes
listed in the [plan standard](docs/agent-plan-standard.md#risk-based-review) a reviewer from a different
model family is required (e.g. Astra / Claude relative to a Codex author); the lack of an available limit
for such a reviewer means waiting, without automatically lowering the requirement.
_Avoid_: reviewer in texts for the user

**Klasa** (Class):
The strength of executor needed for a ticket: light, standard, strong or local; converted into an executor of the chosen provider.
_Avoid_: level, tier

**Preferowany dostawca** (Preferred provider):
The choice at assignment: Anthropic, OpenAI, mixed or local only.
_Avoid_: default model

**Zasady przydziału** (Allocation rules):
A short text that tells the planner which class of executor fits which work.
_Avoid_: routing rules

**Lista dozwolonych poleceń** (Allowed command list):
Commands that a background step may run in the project without asking the user.
_Avoid_: allowlist in texts for the user

**Kontrakt testów projektu** (Project test contract):
Two project test commands in its Board profile (`projects/<slug>/box.yaml`, the `tests` key): `tests.changed`
(tests chosen for the changes, with the parameter `{base}` = the pinned ticket start point) and `tests.full` (the full tests at
card close-out). It applies in every technology; `make test-changed BASE=…` and `make test` are the plasma-tide contract.
It is not the `verify` of the K-38 box (an environment check after the build) nor `allowed-commands`. The general rule and nine
rules: [docs/testing.md](docs/testing.md#general-rule-and-the-project-test-contract).
_Avoid_: test commands in the profile, `verify`

**Efektywny kontrakt** (Effective contract):
The test commands that actually bind a step: the approved contract, and where it is missing or not approved —
detection from the `Makefile`, and then the project rules; the same result (`TestPolicy`) gives the step sheet and command approvals.
The source is `profile`, `detected` or `rules`.
_Avoid_: active contract

**Zatwierdzenie kontraktu** (Contract approval):
The user's decision saved as a contract fingerprint outside the Board (service state): only the project window or `UpsertProject`
from outside an agent step. Writing the `box.yaml` file is not approval; an unapproved contract is not applied and
grants no approvals. Details and conditions of merging with the K-38 box profile:
[docs/tide-daemon.md](docs/tide-daemon.md#compatibility-with-the-k-38-box-profile).
_Avoid_: consent to the write, file acceptance
