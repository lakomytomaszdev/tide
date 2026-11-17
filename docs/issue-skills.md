# A Tide issue from a conversation

## Opening a card and taking over a ticket

The plugin provides `/tide:card K-N [PT-N]`, and Codex provides `$tide-card K-N [PT-N]`.
Without a ticket, both entry points run `tide card open K-N --json` through a shared helper;
the conversation, session, and permissions belong to PT-149 (`TalkToCaretaker`). With a ticket they call
`tide card open K-N --ticket PT-N --json`. `--ticket` excludes `--problem`.

The CLI uses `Snapshot`: `derived.flow.ticketEntryVersion: 1`, `entryRevision`,
and the existing `steps` extended with `attempt` (start order in the journal), `round`,
`currentAttempt`, `available`, and `error`. A finished attempt gives way to the next start
of that ticket. The selection requires one available current attempt from that assignment and the base
version; activity dates do not take part in the selection. Without such an attempt, it gives a list of IDs,
tickets, roles/attempts, and availability, without reading arbitrary paths.

The conditional `EnterStep(K-N, JSON {ticket,step,revision})` rechecks the state
in the serial flow thread. A new attempt, assignment, or state change gives
`stale-context` and the current list; the adapter does not automatically retry the operation.
The takeover uses the existing process stop and session resume in the Console,
in the ticket's worktree. Refusals, including `worktree-busy`, `already-entered`, `console-open`,
`no-session`, `not-stopped`, and `interactive-evidence-unsafe`, remain refusals.
After success the user adds the instruction in the taken-over session.

The card adapter does not analyze code or perform steward actions. Approvals,
pausing, replies, and sending a plan back still belong to the panel and the shared path of
K-37. The installation always includes both adapters under XDG_DATA_HOME; when
Codex is available it additionally installs `~/.codex/skills/tide-card`. The existing collision
protection, backup, and uninstalling only its own files apply.

The package contains one synthesis source (`cli/agents/shared/synthesis.md`), a shared helper
for CLI operations, and two entry points: the Claude Code plugin `tide` and the Codex skill `tide-issue`.
The report describes the problem and the expectation, and accepting an issue remains the user's decision.
It does not start implementation or create a card.

## Report layout

An issue report has three sections: **Sedno** (Gist), **Pytania otwarte** (Open questions), and **Opis sprawy** (Issue description). Acceptance
(`backlog`, `go`) moves the report in its entirety into the card description, and the card description goes verbatim
into the command for the planner, executor, and reviewer. That is why what these
readers need is on top, and the evidence is underneath.

- **Sedno** says on its own what is to be done and why: the problem or need, scope,
  decisions and rejected variants, the way of checking given by the user. The synthesis does not
  add its own acceptance criteria. They would arise without insight into the project, and the planner
  lays them out in the specification anyway, after reading the code.
- **Pytania otwarte** split into two lists. "Do użytkownika" (To the user) are questions that
  only the user can answer and on which the scope depends. "Do sprawdzenia w projekcie" (To check in the project) are things to
  establish in the repository and unconfirmed hypotheses from the conversation.
- **Opis sprawy** is the user's accounts, facts with evidence, and missing evidence. It does not summarize
  the project's instructions or the repository state that the session received on its own: the planner
  reads them in the project, in the current version.

The questions go further in three ways. After saving, the session shows the "Do użytkownika" list;
an answer corrects the same S-N and goes into Sedno as a decision. Unanswered questions
stay in the report, so after acceptance they are in the card description and can be answered by
editing the description before the assignment. The section begins with a fixed sentence for the planner: check
in the project what can be checked, ask the user questions in "Pełne" (Full) planning,
and in "Lekkie" (Light) planning write an explicit assumption for each in the specification, which the user sees
before approving the plan.

## Installation and invocations

Installing the CLI component includes the agent package. During tests we run it
only in an isolated HOME/XDG. The standard project installation has its own backup
before changing the desktop; the package installer additionally protects its files with a SHA-256
manifest and makes a copy on update/uninstall. A collision with a foreign or modified
file aborts the operation without overwriting. Foreign files in the skill directory remain.
It does not copy methodologies, settings, hooks, or the user's MCP configuration.

The plugin is installed in `${XDG_DATA_HOME:-$HOME/.local/share}/tide/agents/claude/tide`.
Start Claude Code with this path, then call `/tide:issue` in the conversation:

```sh
claude --plugin-dir "${XDG_DATA_HOME:-$HOME/.local/share}/tide/agents/claude/tide"
```

Both adapters are always in the package in `tide/agents` under XDG_DATA_HOME, even without the programs.
The Codex skill additionally goes to `~/.codex/skills/tide-issue` when the program is available at
installation. In a new Codex conversation, call `$tide-issue`. Older conversations may need
the program to be restarted to detect the new installation. The `/tide:issue` syntax is
a Claude Code plugin command, not a syntax supported by Codex.
The absence of one program does not block installing the other; after installing the program
repeat the package installation. The Claude plugin requires no registration in the private configuration
or marketplace publication. Uninstalling the CLI component removes only Tide files
covered by the manifest and leaves backups and foreign files.

## Durable draft and corrections

The adapter first checks the API, saves the initial report through the CLI, and shows the S-N/draft.
Only then does the longer synthesis take place. The helper's session directory is the private state
of the conversation (request-id, original payload, and expected version), not the Board repository.
Keep it when resuming and when retrying a lost create response. Do not change the
request-id to bypass an error. To correct an existing issue, use resume with the S-N and a read of the
version. A conflict stops the operation; the decision on how to resolve it belongs to the user.

The helper has begin/resume/finish/waive operations described in the shared instructions.
All issue writes go through `tide issue ... --json` with explicit metadata.
After an open error the saved number still exists; you can retry just `tide issue open S-N`.
The report does not contain service-managed frontmatter. Missing image bytes or an attach
error leaves a described draft. The service validates the manifest, not a list of gaps read
from prose. A separate explicit waiver of the missing evidence updates the same report,
keeps the evidence restriction, and allows submitting a new version without removing attachments.

## Three separate proofs

1. Static tests check the installed entry points and the shared instruction. They do not evaluate the model's text.
2. `python3 -m unittest daemon.tests.test_issues_adapters -v` checks the installation and
   adapter → real CLI → private daemon, versions, evidence copies, no submit when the
   image is unavailable, a separate waiver, conflicts, a lost response, and process interruption.
   The CLI observer does not replace operations with mocks; an isolated window launcher records the selection.
3. `cli/agents/evaluate.py` is a separate, networked evaluation of the real synthesis of both programs.
   It is not part of `make test`. It requires an explicit run outside the step sandbox,
   may consume a paid limit. It creates an isolated Board, HOME/XDG, a private D-Bus, and
   uses the real programs. It reads only the authentication files, if they are
   available; it does not import private settings, hooks, or MCP.

The controlled input: `cli/agents/eval/conversation.txt`, `opis.txt`, and a deterministic
image generated by `fixture.py` as `filtr.png`. The second image has no file and no
bytes at all. Both programs get the same set. The evaluation protocol isolates the synthesis
stage: the helper saves the draft before the model, the model views the image and creates the report
content, the helper attaches the files and keeps the draft with the gap. This is not a test of the program's automatic
skill selection. We check the installed entry points and the helper operations separately.

An example of a deliberate run (the models correspond to the project's executor catalog):

```sh
python3 cli/agents/evaluate.py --output build/issue-synthesis/pt-224 --claude-model sonnet --codex-model gpt-6.1-sol --codex-bin /usr/lib/chatgpt/resources/codex
```

`bash scripts/evaluate-issue-synthesis.sh` first runs the deterministic test module
of the adapters on a private D-Bus. Only after success does it run the networked
evaluation above. These two results remain separate proofs; the script does not itself evaluate the quality of the reports.

The programs can be evaluated separately. `--skip-claude` does not run Claude (not even `--version`)
and does not read its credentials. Codex is launched from the explicit `--codex-bin`, by default
`/usr/lib/chatgpt/resources/codex`, as in Tide; the runner does not replace it with a snap version
found in PATH. The metadata contains the exact path of the program and its version.

```sh
bash scripts/evaluate-issue-synthesis.sh --skip-claude --output build/issue-synthesis/pt-224-codex
```

After a separate consent to a later Claude evaluation, you can use `--skip-codex` with a new
result directory. `status.json` lists the skipped program; a single run does not
meet the criterion of both syntheses. Only the credentials of the selected program are copied,
with 0600 permissions. The context manager removes the copies also after a copy/install error,
a failed process, an interruption, and SIGTERM, and then removes the whole temporary HOME.

The result directory is new, so as not to overwrite an earlier evaluation. It contains the program versions,
the requested and transcript-read models, the input, the reports, the transcripts, the CLI results,
the attachment copies verified by a CLI read, and an unfilled rubric for each program.
After the run, evaluate all nine items of `rubric.json` based on the report and the
CLI results: pass/fail with an exact quote or a pointer to a fragment. Record a pass verdict
only with nine passes; losing a condition, a rejected variant, or a fictitious piece of evidence
means fail. Headings and process-argument assertions do not replace this evaluation.
Do not declare the synthesis criterion met without both real reports and rubrics.

The saved [evaluation of the real Codex synthesis for PT-224](agents/pt-224/evidence/codex/evaluation.md)
contains the report, the transcript, the metadata, the 7/7 rubric (the version from before items 8 and 9 about the report
layout), and separate proofs of reading the attachments
through the CLI. The Claude evaluation in this ticket remains to be completed; proof from one
program does not close the criterion of both syntheses.

A separate run after the user's conscious waiver can perform waive on the preserved
session and the same S-N, with the literal answer "rezygnuję z drugiego obrazu" (I waive the second image). The first
run does not receive this decision. A deterministic test checks this sequence separately.
