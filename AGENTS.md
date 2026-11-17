# Tide

A custom KDE Plasma 6 theme (KDE neon, Wayland, two monitors) with widgets for working with AI agents. Description and structure: `README.md`, rationale for decisions: `docs/decisions.md`.

## Project rules

- Commits and branch names in English, without signatures or mentions of AI. Push only on explicit request.
- Conversation, documentation and code comments in Polish.
- We change the look only through `make install`, which first makes a backup. We never edit files in `~/.local/share` or `~/.config` by hand.
- Colors, radii and fonts come from the palette in `theme/palette/`. Generators read the palette; values are not hard-coded.
- We install apt packages only after a simulation (`apt-get -s install`), which must not pull in Qt or KDE Frameworks upgrades.
- We test the lock screen and SDDM in test modes (`kscreenlocker_greet --testing`, `sddm-greeter-qt6 --test-mode`) before they reach the system.
- Installing Tide on a new machine and help with it (also for an AI agent): `docs/install-new-machine.md`, the section "Dla agenta AI pomagającego w instalacji" (For an AI agent helping with the installation).

## Tests during work

During a ticket, run the tests chosen for the change (`make test-cardflow`, `make test-daemon`,
`make test-board`). Before handing in a ticket and its fixes, run `make test-changed BASE=<the ticket's start point
on the card branch>` and record the result in the note. The reviewer checks this result; a separate full `make test`
is not a criterion of the ticket or the review. This rule replaces the requirement of a full run in the sheets of
existing plans (including K-37, K-38 and K-55), without rewriting them. Run the full `make test` once
on the card branch at its close-out, before the summary. If `test-changed` selects the full suite,
run it without an additional `make test`. By default modules run in as many processes as there are cores minus two;
`JOBS=N` changes the limit. The list of suites: `tools/test_suites.py`, instructions and isolation:
[docs/testing.md](docs/testing.md).

A retry without code changes (the same HEAD, a clean tree) runs only the modules that failed, not the whole
suite. Details: [retrying failed modules](docs/testing.md#rerunning-failed-modules).

An exception for an older branch without the `test-changed` target: the author and the reviewer run the full `make test`
and record the result. Details: [older branches](docs/testing.md#older-branches-without-the-test-changed-target).

## Agent skills

### Issue tracker

GitHub Issues in the private repository `lakomytomaszdev/plasma-tide` (the `gh` CLI) remain available for
general reports. Specifications, plans and card flow tickets are files in the repository; the source of their state
is the plan. Rules: `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` in the root directory. See `docs/agents/domain.md`.

## Agent work plan (Tide)

Standard: `~/DevWorkspace/plasma-tide/docs/agent-plan-standard.md`.

1. A coordinating session that divides work into tickets keeps a plan `docs/agents/plans/YYYY-MM-DD-<slug>.md`, committed together with the work or in a separate commit `docs(plan): …`.
2. Frontmatter: `tide-plan: 1`, `title`, `tracker: local`, `tracker-url` and `tracker-project` empty, `ticket-prefix: PT`, `coordinator` (`claude`|`codex`), `session` (the session ID, if known), `status` (`active`|`paused`|`done`), `updated` (ISO 8601 time).
3. A table `| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |`; `Stan` ∈ `todo`, `w-toku`, `review`, `czeka`, `gotowe`, `odrzucone`. Update the table and `updated` at every delegation, result, review and question to the user.
4. Delegation: the subagent's description/title starts with the ticket ID (`PT-7: implement …`, `PT-7: review`); a Codex subagent has the ticket ID in the task and in the nickname. The branch contains the ID in lowercase (`feat/pt-7-…`). A question to the user names the ticket ID.
5. A card flow plan does not use an external tracker: ticket IDs are assigned by the plan (`PT-1`, `PT-2`, …), numbering is continuous across all plans of the repository (a new number = the largest existing + 1, a new plan does not start from 1), and the plan is the source of state for the Tide board and the morning report. GitHub Issues are not the source of the plan.
6. The `TIDE` project in YouTrack is only an archive of work already done (TIDE-1…31, 26–27.10.2026), not a source of the plan. We do not use the `TIDE` prefix in plans — it is a project key in YouTrack (the "one source of numbers" rule in the standard).

## Card flow

The project vocabulary: `CONTEXT.md`. The full format of the plan and the sheet and the step contract:
`docs/agent-plan-standard.md`; how the service works: `docs/tide-daemon.md`. A Board card keeps a journal,
a card branch, separate tickets, independent review according to risk and the user's decisions in the panel. Choosing the reviewer:
`docs/agent-plan-standard.md`, the section "Recenzja według ryzyka" (Review by risk). For new work choose
"Od razu" (Immediate), "Lekkie" (Light), "Pełne" (Full) or "Mam plan" (I have a plan) as needed; Full planning in the new flow is
planned for the second stage, and until then the planning conversation stays in the Console. Before you start a ticket,
read its sheet and the plan. When you launch a background step, write the result to `result.json` according to the contract.
Do not push or merge into the main branch without the user's explicit instruction.
