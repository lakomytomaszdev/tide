# Sandbox, permissions and the test environment of steps

State as of 03.11.2026 and a target proposal. The document answers the user's question: "we tightened things recently, today we
are loosening them — where is the boundary?". The decisions to be made are at the end, each with a recommendation.

## 1. Goal

Background steps should work without questions about the environment (installs, ports, tests, Docker) and at the same time
be unable to do anything the user did not anticipate: push code, deploy, read secrets, change the system. A question to the
user should concern the scope of work and business decisions, not a fight with the environment.

## 2. Current state (03.11.2026) — honestly

| Layer | What enforces it | What it allows | What it blocks | Strength |
|---|---|---|---|---|
| **Step sandbox** (Claude Code `bwrap`, Codex from a snap) | kernel (namespaces), AppArmor profile for `bwrap` | write: the step result directory and the worktree; read: the worktree, the shared `.git`, step directories, fonts, the Node/.NET installation read-only | reading `~` (SSH keys, `~/.git-credentials`, tokens), network (`allowedDomains: []`), Unix sockets (D-Bus, Docker, SSH agent) | **hard** |
| **Step environment** | `agentcmd._env` | `HOME`, `PATH`, `LANG`, tool variables | SSH agent, askpass, GitHub tokens, D-Bus | second layer (the agent can override it) |
| **Project command list** (allowed / forbidden) | Claude Code `--allowedTools`, policy in the step command | commands from the list without a prompt | forbidden commands (in Tide `make install`) | protects against a mistake, **not against malicious code** |
| **Automatic consents in the sandbox** (since 03.11) | `engine._auto_consent` | every single command outside the list, unless it is forbidden | chains, subshells, forbidden commands | safe — it runs in the sandbox anyway |
| **Command outside the sandbox** (`outside`, since 03.11) | `systemd-run --user --scope` with user rights | **everything the user can do**: network, Docker, the whole `~`, D-Bus, keys | only the policy filter: chains and operators, a list of risky families (push, publish, ssh, curl, rm…), paths outside the worktree, project prohibitions, one command at a time in a project | **soft** |
| **Automatic consents outside the sandbox** (since 03.11) | `outside_risky` | tests, builds, lint, package installation | as above | **soft** |

### Where the hole is today

A command outside the sandbox that looks innocent (`dotnet test`, `npm run e2e`, `make test`) **runs code
from the repository — including code the agent itself just wrote** — with the user's full rights. A test can
read `~/.ssh`, push a branch with `~/.git-credentials`, use the `gh` token from the wallet or start a container
with `/` mounted (the Docker socket gives root rights). The list of risky families will not stop this, because the risk sits
in the test code, not in the command name.

Conclusion: **automatic consents in the sandbox are safe; automatic consents outside the sandbox are today a convenience
bought at the cost of the boundary.** With a cooperating agent and trusted repositories this is acceptable for a short time — it is the same
as working in a terminal with Claude or Codex in automatic mode — but it is not a boundary we would call
"hard".

## 3. The boundary principle (proposal)

1. **Code from the repository never runs with the user's full rights** — neither in a step nor in an "outside" command.
2. Steps get **resources** (network to package registries, ports, Docker, caches), not **user rights**.
3. What a step needs can mostly be **predicted at planning time** — the user approves it once,
   together with the plan, as the **card consent pack**.
4. A question during work appears only when a step goes beyond the package — and then it says what is missing and what
   will happen after consent.
5. Everything that goes outside (push, merge request, deployment, publication) is done by **Tide at the user's
   command**, never by the agent — consistent with K-36 (repositories in Tide).

## 4. Target model: three execution levels

| Level | What for | Network | Files | Docker | Who approves |
|---|---|---|---|---|---|
| **P1 — step sandbox** (today + registries) | writing code, building, unit tests, `npm install`, `dotnet restore` | only the registry domains from the profile (`registry.npmjs.org`, `api.nuget.org`, `pypi.org`, `fonts.googleapis.com`…) — the Claude Code sandbox `allowedDomains` | write: worktree, step result, the project's package cache | no | card consent pack |
| **P2 — test sandbox** (new, replaces "outside the sandbox") | e2e, integration tests with Testcontainers, development servers, Playwright | project profile (e.g. full network or registries + localhost) | write: worktree and tool caches (`~/.npm`, `~/.nuget/packages`, `~/.cache/ms-playwright`); **reading `~` forbidden** except tool installations; no D-Bus, SSH agent, wallet | **a separate, rootless Tide Docker/Podman** — not the user's Docker socket | card consent pack |
| **P3 — user rights** | push, merge request, deployment, publication, system changes | full | full | full | always the user, one-off, in Tide (K-36); the agent does not get this level |

Technically P2 is `bwrap` (the same AppArmor profile as today) started by the service, not by the agent: a mount
namespace with a view of only the worktree and caches, its own `/tmp`, network according to the profile, an allocated port for the development
server (`E2E_PORT`/`PORT` from a pool, no more conflicts on 4300), the socket of Tide's rootless Docker
(`dockerd-rootless` or `podman system service` under a separate user). Testcontainers see only this Docker.

## 5. Card consent pack (approved together with the plan)

Planning (light and full) detects the stack and proposes the **card environment profile**:

- **Detection:** `package.json` (scripts, Playwright, Angular/Vite — server port), `*.csproj`/`*.sln`
  (Testcontainers, xUnit, databases), `pyproject.toml`, `Makefile` (targets `test`, `lint`), `docker-compose.yml`,
  CI configurations (`.gitlab-ci.yml`, `.github/workflows`) — CI is the best inventory of what the tests need.
- **Proposal** (example for toolssuite):
  - P1: network to `registry.npmjs.org`, `fonts.googleapis.com`; commands `npm ci`, `npm install *`, `npm run lint *`,
    `npm run build *`;
  - P2: `npm run e2e *`, `npx playwright *` with an allocated port; Docker: no;
  - P3: none.
- For LegalApp: P2 `dotnet test *` with Tide's Docker (Testcontainers/PostgreSQL), network: `api.nuget.org`.
- **Approval:** in the plan window, next to the model assignments — a list of "what the card needs" with toggles.
  The approved profile is saved in the project and is the default for subsequent cards (a change = a new consent).
- **During work:** a command that fits the profile starts by itself; one outside the profile — a single question with the options
  "raz / dopisz do profilu projektu / nie" (once / add to the project profile / no), with information on which level it will run.

## 6. A test environment that "just works"

- **Preparation before a step by the service** (already there for .NET and Node): restore/`npm ci`, Playwright
  browsers, Docker images from the profile (`docker pull` on Tide's Docker), test database migrations.
- **Ports from a pool** per step, passed in a variable; one development server per worktree.
- **A queue of heavy tasks** (exists since 03.11: one P2 command at a time in a project, two in total) — eventually
  configured in the UI (K-36).
- **Test result as a step artifact:** the log and a summary (counts, time, exit code) in the step directory; the reviewer
  reads the artifact instead of asking for another run ("no run = no evidence" satisfied without questions).
- **Tests unstable under load** (today: Tide tests with timeouts) — timeouts scaled by load
  or a run in the heavy-task queue.

## 7. Decisions to make (with a recommendation)

1. **What to do with automatic consents outside the sandbox until P2?** Recommendation: leave them enabled for the test
   and build families (work stalls without them), but restrict to repositories you trust — a per-project toggle
   instead of a global `autoConsent`.
2. **Docker for steps:** a separate rootless Tide Docker (recommendation; isolation from your containers and from root)
   or your Docker (simpler, but the socket = root)?
3. **A separate system user for P2** (strongest file isolation, more configuration) or `bwrap` on your
   account with a view of only the worktree and caches (recommendation for a start)?
4. **Network in P1:** only the registry domains from the profile (recommendation) or full network?
5. **Consent pack:** for every card (clearer) or once per project with inheritance (recommendation: once per
   project, the card shows it for review and may extend it).

## 8. Preliminary plan (to be broken down on the card)

| # | Ticket | Blockers |
|---|---|---|
| 1 | Project environment profile: model, storage in the project, stack and CI detection, proposal at planning time | — |
| 2 | P1: network to the profile's domains (`allowedDomains`), project package cache in the step | #1 |
| 3 | P2: test sandbox (`bwrap` through the service: worktree and cache view, own `/tmp`, network from the profile, ports from a pool) instead of `outside` with user rights | #1 |
| 4 | Tide Docker: rootless Docker/Podman, socket only for P2, preparation of images from the profile | #3, decision 2 |
| 5 | Consent pack in the plan window: a list of the card's needs with toggles, saved to the profile, a question only outside the profile | #1, K-35 |
| 6 | Step test artifacts (log, summary) for the reviewer; the reviewer does not ask for a run when the artifact is fresh | #3 |
| 7 | Per-project toggle for automatic consents in the UI, review of the automatic consent log | K-36 |

Order: #1 → #2 and #3 in parallel → #4 → #5, #6, #7. Until #3 the state from section 2 applies.
