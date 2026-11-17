# The card box: an isolated project environment without consent prompts (K-38)

Analysis and design for grilling. State as of 03.11.2026, branch `fix/step-output-dir-writable`. The document develops
`docs/sandbox-and-permissions.md` (the P1/P2/P3 model stays as plan B) in line with the direction approved on card
K-38: instead of ever smaller consents — a closed environment in which the agent can do everything, and the boundary is at the exit.

Markers: **[verified]** — a result from this computer (03.11.2026); **[assumption]** — to be confirmed by a prototype
or in conversation; **[decision]** — a question to the user (collected in section 9).

## Project boundary contract v2 — PT-264 (07.11.2026)

This section replaces the historical assumption "whole `.git` rw + ro overlays" in the rest
of the design. The scope change was approved when PT-264 was resumed, at 15:33.
The implementation requires acceptance by an independent model family; the description alone does not open
the PT-261 gate and does not replace the PT-273/PT-274 trials.

**The trust domain remains the project.** All its cards may read the shared
objects, refs, inputs, worktrees, home and working results. There is no confidentiality
boundary between cards. The host stores of results, verdicts, consents and the guard remain
outside the devices. `per-card` does not grant the right to mount the shared parent of the projects.

**PT-263 manifest.** New proposals have `mountContract: 2`. The base `.git` is
ro, and only `objects`, `refs`, `logs` have separate rw sources. The host config,
hooks, info, config.worktree and the worktree administration registry are not in
the rw source covering the whole administration. Workspaces and the project's dedicated home
are rw; only the exact `PackSnapshot.directory` of the inputs are ro. A plan change
changes the fingerprint and requires a new preview and the project's conscious consent. An existing
record is not rewritten to the new content or automatically extended.

**Administration and the index.** `box/gitview.py`, copied to `in/bin/git`, maintains
a private HEAD and index in the box's home. It uses the shared objects/refs/logs;
the internal sandboxes of both programs have explicit write access to this private view.
Commits are still immediately visible in the project. The host never executes a program
or runs Git on an administration chosen from this private directory.
The guard checks the HEAD of the canonical private view itself through descriptors without
following symlinks; it does not interpret its config or index. A detached HEAD
or another branch gives `refs.guard`, keeps the observed OID in the report and restores
the branch assignment. This works also after `/usr/bin/git` is used with this view, without
trusting the wrapper. It is a check of the canonical view's state, not proof of all
root commands in an arbitrary administration. The `.git` file
in a writable worktree is an untrusted hint: the host checks its consistency
with the registry under the base ro mount and pins its own `--git-dir`/`--work-tree`.
A substitution does not select a foreign project. We do not rely on an overlay of a single file
in a writable source or on masking this file from an ordinary user.

Initialization `init`/`init-db`/`clone` in the box does not use the view of an existing checkout.
The wrapper removes inherited `GIT_*` variables other than identity also on the path of
an ordinary repository and runs these commands without its own `GIT_DIR`,
`GIT_COMMON_DIR` and `GIT_WORK_TREE`. The guard's private host view allows only
`config`, `for-each-ref`, `check-ref-format` and `update-ref`. It refuses the other
commands, including initialization and aliases, before running Git: it serves an existing
store, and initialization could rewrite the shared config through `commondir`. Ordinary host initialization
of a separate repository keeps the closed environment and the process boundary.
This is a fix to the execution of the v2 contract, without changing the manifest and the mount permissions.

Before exposure the host writes `tide-boundary` and the points
`tide-index-tip` in the protected administration. After a ref is moved by the private view the host recreates its
index from the new HEAD before an operation requiring the index. It does not copy the index from the box's
home. Working changes stay on disk. This mechanism concerns the project worktrees
run by Tide; the user's main index is not synchronized.
Cards are still subject to the PT-266 serialization. Parallel manual work on the index of the same
worktree is not a separate domain or a supported way of preserving staging.

**Host Git operations on box data.** Every host Git call goes
through `gitcommand` (FlowGit, scanner, Board, steward, source preparation,
test retries, StepGit). The boundary — the isolated interpreter `git_sandbox.py` —
is on when (1) the service has `box.enabled` (the settings file is re-read after
every change, without a restart), (2) the call concerns a repository marked
`<common-dir>/tide-boundary` — the marker is written by `initialize_indexes` before the first
exposure of the repository to the box and stays also after the box is disabled — or
(3) it is called by a box path (`box/*`, the ref guard, a checkout snapshot in boundary
mode). An uncertain read (a stat error of the settings, an unreadable `.git` pointer
or `commondir`) selects the boundary. In the other cases — box disabled
and the repository never exposed — argv and environment are as before K-38:
`git -c core.hooksPath=/dev/null -c core.fsmonitor=false …` without a second interpreter
(owner's decision 08.11, a measurement of ~4× the sum of module times with an unconditional boundary;
[PT-264 decisions](agents/pt-264/decisions.md#granica-gita-warunkowa)). A separate bwrap rootfs contains only the
system program, its ro libraries/data and the explicit project roots.
There is no host `/tmp`, `/run`, processes, IPC or network. This also limits
metadata changes through chmod/utime and path sockets; Landlock's file policy
alone is not enough to protect against them. Landlock ABI at least 6
limits files to the explicit repository/workspaces and the explicit input/output files
chosen by the service. It decides access also after a later symlink substitution.
It does not grant execution of project files, shell, filters or helpers from config;
it executes only the system Git and its own same program in git-core. It does not grant
TCP connections or output outside the IPC scope. The system program's libraries and data are
ro; the system/global configuration, lazy fetch and Git variables from the environment are
disabled. Only the host's user.name/email are kept: a separate secured
`git config --no-includes --file` reads these two fields, and the project operation
gets a generated identity file. The host's original config is not mounted
for it; the local and explicit identity take precedence. A missing bwrap or required
ABI means a refusal without a silent return to the old Git.

Reads of a single checkout snapshot may share one bwrap start.
A trusted interpreter performs a fixed list of reads of HEAD, branch, status, index
and administration; every Git process gets the same Landlock policy and pinned
gitdir/worktree. This is not an interface for arbitrary batch argv or a persistent worker.
The results have their full format checked, and an error of any required read refuses
the snapshot. Every next snapshot repeats the reads and the boundary validation; there is no cache
of mutable state between the preview, the consent and the execution. File fingerprints, checking of the source
repository and comparisons of successive phases remain mandatory.
A snapshot is still a multi-step read, not an atomic Git transaction.
The mount plan and the PT-263/PT-266 permissions are not extended by this.

**Git cost in v1 — owner's decision 08.11.2026.** We accept a threshold of
**1.6 instead of 1.05** for the enabled/BASE ratio of means. Native40 in the order
B/E/E/B gave `37.12540199293289 / 54.42815118690487 / 54.10217254597228 /
33.05383698607329` s, after the same 31 tests OK without skips. The means are
`35.08961948950309` and `54.265161866438575` s: **1.5464733632313044 (+54.65%)**.
Enabled was a prototype of a persistent worker in disposable archives, left
in the out of step 40; it was not enabled in the branch code. This measurement does not confirm
the same cost of the delivered implementation. The owner rejected a rebuild
in C and instructed to leave the security and the product mechanisms unchanged.

The residual risk is longer operations and sensitivity to load; the BASE spread
was 12.318%, and two of the four raw ratios exceeded 1.6. Accepting
the mean is not a guarantee of the latency of every operation. Narrowing the boundary to box
data entered PT-264 (above); stage 2 / K-75 may consider further narrowing
per repository with the box enabled, or a native core. The full measurement, the source hash and the scope of the decision:
[PT-264 decisions](agents/pt-264/decisions.md#asked-akceptacja-narzutu-git-w-v1-bez-przebudowy-w-c).
The historical native40 code 1 against the 1.05 threshold and the earlier functional failures
remain recorded. A current `test-changed`, an independent review,
a new project consent on a manifest change and continued maintenance of the closed trial gate are required.

Alternates/http-alternates and commondir redirections are rejected before the start.
A race trial on the Git store still has kernel limits at file open;
an earlier path validation alone is not the only boundary. The private
`guardgit` view keeps its own-ref algorithm and CAS, but also uses this boundary.
The approved service argv is a trusted description of its operation, not an arbitrary command
accepted from the box. An explicit archive/index file does not authorize config to
choose a further target. The installed service's code must be outside the rw sources.

Host creation and removal of worktrees also keeps the convention used by the Planner,
`<repo>/.claude/worktrees`; this is still the interior of an explicit project. For
`worktree list` an existing checkout outside the typical container does not require mounting
its directory or its parent: the boundary exposes only its checked `.git` pointer
ro, with a matching common-dir and the host administration. This does not change the box device plan.

**PT-264 devices.** The order is packages → consent → devices. The consent lock
also serializes operations on devices. The adapter compares the complete effective
set of devices, including inherited ones, not just their names. It rejects additional
disks, devices/sockets, raw configurations and a privileged container. Changing
mounts requires no live step and stopping the whole box, Docker included.
Before the sources are attached, hardlinks and special files are rejected; symlinks
of ordinary data resolve in the box's namespace. An unknown mount stays
unchanged and blocks the start. The `detach_snapshot` callback detaches only its own
proven devices in all instances of the project before the PT-262 data
is removed; it does not reset other cards. A project reset also refuses with a live card copy.

**Impact on finished tickets.** PT-263 keeps the versioned consent store, but
derives a new plan and fingerprint. PT-266 keeps the serialization, its own ref and CAS;
its private Git gets an additional process boundary. PT-169 has a new v1 adapter,
it does not use the old mount path for plasma-tide. PT-262 still only copies,
scans and removes data after the callback. PT-268/PT-269 must consume the exact new
consent; they must not be activated with the old card profiles. Integration of step selection
and the project policy stays in PT-265/PT-269. The executed historical part of K-38,
WIP and review results are not rewritten.

Automatic evidence is on disposable repositories and a device dummy.
It does not mean root/Docker passed on a real Incus. That acceptance requires
the registered target PT-273 and the PT-274 gate; it does not use the user's `.git`.

## 1. Goal and success criteria

**Goal.** A background step (ticket, review, rework, planning) works in the project's box: installs packages, starts
servers, runs Docker/Testcontainers, browsers and full tests — without a single question about the environment. The box does not
see the user's home directory, their Docker, D-Bus, keys or production. The only exit is commits on the ticket
branch in the shared `.git` and the step result files; push, MR and deployment are done by Tide at the user's command (K-36).

**Success criteria (measurable).**

| # | Criterion | Measure | How to check |
|---|---|---|---|
| S1 | Zero questions about the environment | 0 `step.decision` events with `envAnswer` and 0 `outside`/`denied` results about the environment on the reference cards | repeat the scenarios of 03.11 (section 7.4): `npm install <new package>`, e2e on a port, Karma with Chrome, `dotnet test` with Testcontainers, Tide `make test`, a command > 300 characters |
| S2 | Hard boundary | 100% of the items of the "red list" (section 6.3) end in a denial in the box | `make box-check` runs the list in a real box |
| S3 | Step start time | box overhead ≤ 3 s with a running box; cold start of the box after a restart ≤ 30 s; build from the recipe ≤ 15 min; reset to the `clean` snapshot ≤ 60 s | a measurement in the card journal (`step.started` − `intent`) |
| S4 | Resilience | a service restart does not kill a step in the box (as `systemd-run --scope` today); stopping a step leaves a WIP commit (`stepstop`) | the existing tests `test_cardflow_execution`, `test_stepstop` with a box dummy |
| S5 | Responsive desktop | the box has a hard CPU/RAM limit (section 4.9); a full e2e does not freeze Plasma | `limits.cpu`, `limits.memory` in the Incus profile |
| S6 | Universality | the four reference projects (section 5.6) have a working profile; a project without a reference gets an "empty + apt from the recipe" profile and a question at plan time, not during work | profiles in the Board, a `clean` build for each |
| S7 | Tide tests without Incus | `make test` passes on a machine without `incus` (the `fake_incus` dummy) | CI/`make test` |

## 2. Threat model

### 2.1 What we protect against

| Threat | Example from 03.11 / real scenario | Today (`outside`) | In the box |
|---|---|---|---|
| Z1 Agent with a bug | `rm -rf` of a wrong path, `git checkout master`, `make install` | project prohibitions, the `OUTSIDE_RISKY` filter — soft | effects closed in the box; `make install` installs in the box, not on the host; the ref guard (4.4) detects movement of other branches |
| Z2 Prompt injection from the repository, the ticket sheet, a dependency | a file in the repo tells it to "download and run", a README of an npm package | agent in bwrap without network, but `outside` with full rights | network only to the profile's domains through the proxy; nothing outside the box |
| Z3 Malicious package (npm postinstall, NuGet build task, pytest plugin) | `npm install` of a new package runs code | in P1 in bwrap (safe), in `outside` — full rights | the code runs in the box as the `agent` user; sees only the project worktree and the box cache |
| Z4 Test code from the repository (including code written by the agent a moment earlier) | `dotnet test` reads `~/.ssh`, `npm run e2e` sends data | **the main hole** (`docs/sandbox-and-permissions.md` §2) | as above; the Docker socket in the box is the *box's* root (uid 1,000,000+ on the host) |
| Z5 Leak of tokens and keys | `~/.git-credentials`, `gh` from the wallet, `~/.claude/.credentials.json`, `~/.codex/auth.json` **[verified: all exist]** | reachable from `outside` | SSH keys, git-credentials, wallet, gh — invisible; agent credentials — residual risk (4.2) |
| Z6 Push, MR, deployment, publication | `git push`, `npm publish`, `dotnet nuget push`, `rsync` to the devhost (the infracore CI does `rsync` + `sudo`) | family filter | no credentials and no network to `github.com`/`git.firma.example`/`10.20.3.10`, unless the profile explicitly allows it (it does not by default) |
| Z7 Destruction of user data | `rm` in `~`, `docker system prune` deleting `youtrack`, `legal-agents-postgres` **[verified: running on the user's Docker]** | `OUTSIDE_RISKY` does not catch test code | host `~` and the user's Docker invisible; the box has its own Docker |
| Z8 Resource consumption | five e2e at once, `ng serve` ×3, Tide tests with timeouts failing under load | `CPUWeight=40`, queue 1/project, 2 in total | a hard box limit + a queue of heavy commands in the box; ports per box disappear as a conflict between projects |
| Z9 Poisoning of the shared `.git` | the agent moves `refs/heads/master`, replaces `gitdir` | `resolve_git_dir` before the step; Codex writes only `objects/refs/logs` | as today + the ref guard after the step (4.4) |

### 2.2 Out of scope

- Attacks on the kernel from inside the container (escape through a kernel flaw). A system container shares the kernel with the host;
  defense: an unprivileged user + AppArmor + Incus seccomp. A project we do not trust even that much
  gets `type: vm` in the profile (section 3) — an option, not the default.
- Models and providers (Anthropic, OpenAI) as an adversary; content sent to the model (project code) — as today.
- Protection against the user (deliberately disabling the box in settings is the user's decision).
- Production deployments, company CI — we do not touch them; the boundary is "nothing leaves without Tide and a click".
- The Plasma screen and compositor/KWin (lock screen, SDDM, KWin scripts): the box will not run a real Plasma;
  those tests stay on the host as an explicit user action (the test modes from `CLAUDE.md`).

## 3. Comparison of options

### 3.1 Host state [verified 03.11.2026]

- KDE neon User Edition (Ubuntu 24.04), kernel `7.0.0-38-generic`, 16 cores, 78 GiB RAM, `/` 51 GB free (ext4),
  `/home` 220 GB free (ext4). KVM available: `/dev/kvm` has the ACL `user:tomek:rw-`.
- cgroup v2 (`cgroup2fs`, controllers `cpuset cpu io memory pids …`); `/etc/subuid`/`subgid`: `tomek:100000:65536`.
- AppArmor 4.0.1, `kernel.apparmor_restrict_unprivileged_userns = 1`. Profiles with `userns` and `flags=(unconfined)`:
  `/etc/apparmor.d/bwrap` (installed by Tide, PT-75), `/etc/apparmor.d/podman` (from the `apparmor` package),
  `lxc-usernsexec`, `unprivileged_userns`.
- Installed: Docker CE 29.9.2 (`/var/run/docker.sock` root:docker, a user in the `docker` group = root on the host;
  `data-root=/home/docker-data`; containers `youtrack`, `legal-agents-postgres`, `cockpit-db`, … are running),
  `docker-ce-rootless-extras` (`dockerd-rootless-setuptool.sh` is there), `bubblewrap 0.9.0`, `uidmap`, `slirp4netns`,
  `qemu-system-x86 8.2.2` + `ovmf`. Not present: `incus`, `lxd`, `podman`, `systemd-nspawn`, `firejail`, `xvfb`, `weston`.
- apt simulations (none touches Qt or KDE Frameworks):
  - `apt-get -s install incus` → 9 packages: `incus 6.0.0-1ubuntu0.3`, `incus-client`, `incus-agent`, `liblxc1t64`,
    `liblxc-common`, `lxcfs`, `libcowsql0`, `libraft0`, `attr`.
  - `apt-get -s install podman fuse-overlayfs crun passt` → 11 packages, `podman 4.9.3`.
  - `apt-get -s install xvfb` → 1 package (unnecessary on the host if the box has its own).
- Agents: Claude Code 2.1.317 (`~/.local/bin/claude` → `~/.local/share/claude/versions/…`), Codex 0.114.0 from a snap
  with the plugins `home`, `network`, `network-bind`, `desktop`. Node only from nvm (`v20.19.6`), .NET SDK 10.0.113
  in `/usr/lib/dotnet`, Playwright: `~/.cache/ms-playwright/chromium-1193…1248`. Ollama active on `127.0.0.1:11434`.
- Occupied ports relevant to steps: `4300` (someone has `ng serve`), `5432` (the user's Postgres on `0.0.0.0`).

### 3.2 Criteria and rating

Scale: ++ very good, + good, 0 neutral, − poor, −− disqualifies.

| Criterion | A. Incus system container (unprivileged, nesting) | B. Rootless Podman (a container per project/step) | C. Light VM (Incus VM / QEMU; Firecracker) | D. Separate user + bwrap + rootless Docker | E. Today: P1/P2/P3 (bwrap) |
|---|---|---|---|---|---|
| Host file isolation | ++ (own rootfs, idmap) | ++ | ++ | + (the same fs, Unix permissions) | − (`outside` = everything) |
| Host kernel/root isolation | + (userns, AppArmor, seccomp; the Incus daemon is root) | + (no root daemon) | ++ | + | − |
| Docker / Testcontainers inside | + (`security.nesting=true`, documented "Docker inside Incus") **[assumption: prototype P-1]** | − (Docker in rootless Podman: nested userns + fuse-overlayfs, brittle; Podman-in-Podman better, but Testcontainers .NET wants the Docker API — the Podman socket works) | ++ | + (the separate user's rootless `dockerd`; Testcontainers through `DOCKER_HOST`) | −− |
| GUI/QML offscreen, headless Chrome | + (apt in the box, without the host's Qt restrictions; `xvfb`/`weston --headless` in the image) | + | + | − (libraries must be on the host — the "apt does not raise Qt" rule) | 0 (works today, because the libraries are on the host) |
| Performance, disk | + (native processes; bind-mount of the worktree = the same ext4; image ~2–6 GB + cache) | + | − (reserved RAM, virtiofs/9p for the worktree slower, VM image) | ++ | ++ |
| Step start time | ++ (the box persists; `incus exec` ~0.1 s) | + | 0 (VM 5–15 s, memory snapshots help) | ++ | ++ |
| Installation on KDE neon | + (apt from universe, 9 packages, `incus admin init`, group) | + (apt, 11 packages; `apparmor` has the `podman` profile) | 0 (Incus VM: the same packages + `qemu` is already there; Firecracker: no apt package, own kernel) | 0 (useradd, subuid, rootless Docker setup, AppArmor profiles for the second user) | ++ |
| Maintenance | + (one tool: container and VM, snapshots, limits, network) | 0 (OCI images, Containerfile; no "system" inside without `--systemd`) | 0 | − (a manual patchwork) | 0 |
| Snapshots / reset | ++ (`incus snapshot`, `restore`, `copy`; btrfs/ZFS CoW) | 0 (`podman commit`, layers) | ++ | −− (none) | −− |
| Offline work | + (local image, package cache in the box) | + | + | + | + |
| Network: domain list | + (nftables on the host + the Tide proxy; Incus network ACL) | 0 (pasta/slirp in userns — a filter only through the proxy or uid) | + | 0 (uid-based nftables + proxy) | + (Claude Code `allowedDomains`), −− for `outside` |
| Step parallelism | ++ (processes in one box, `systemd --user` inside) | + | ++ | + | + |
| Risk "root-equivalent" for the service | − (the `incus-admin` group = root; mitigated by a restricted project through `incus-user`/the `incus` group) **[assumption: P-5]** | ++ | − (as A) | 0 (sudo only at installation) | ++ |

### 3.3 Recommendation

**A. Incus, a system container, one per project** — with `type: vm` as an option in the profile for repositories
of lower trust (the same CLI, the same recipe, Incus 6.0 supports both; QEMU and OVMF are already on the host).

Justification:
1. It is the only option that combines Docker inside, own system packages (Qt/PyQt6 for Tide tests without touching the
   host), snapshots for reset and step start without overhead. Rootless Podman is safer for the service, but Docker
   inside and the lack of a "system" (systemd, the `agent` user, lingering) would cost more than we gain.
2. A system container behaves like a machine: paths identical to the host (4.3), `systemd-run --user --scope` as
   today, `stepstop` without a change of concept. The change in Tide is mainly "where we run argv", not a new step model.
3. A VM would be cleaner right away, but the worktree through virtiofs and RAM permanently reserved for every project
   (4 projects × 8–16 GiB) is a real cost on a working computer. Container to start with, VM on demand.
4. Option D (separate user + bwrap) is plan B from `docs/sandbox-and-permissions.md`; it stays as a fallback mode
   when Incus cannot be set up (section 10).

What Incus does **not** give by itself and what Tide adds: the environment profile and recipe (section 5), an outbound proxy with a list of
domains (4.5), the ref guard (4.4), the port pool (4.6), data packs (6.2), cleanup (4.10).

## 4. Recommended architecture

```
host (KDE neon)                                     project box (Incus, unprivileged, nesting)
┌──────────────────────────────────┐                ┌──────────────────────────────────────────────┐
│ tide-daemon                      │ incus exec     │ systemd; user agent (uid 1000 ↔ host 1000      │
│  cardflow/engine._launch ───────────────────────▶ │ systemd-run --user --scope tide-step-<id>    │
│  box/ (builder, runner, proxy)   │                │   claude -p … / codex exec …                 │
│  outbound proxy :3128 ◀──────────── HTTPS_PROXY ─┤   npm, dotnet, playwright, make test           │
│  (domain list from profile)      │                │ box dockerd (/var/run/docker.sock)            │
│ nftables: box → proxy only,      │                │   Testcontainers, pgvector:pg16               │
│   host Ollama; the rest DROP     │                │ /home/user/DevWorkspace/<proj>/.git   (rw)   │
│ ~/DevWorkspace/<proj>/.git ─────── disk shift=true─▶ /home/user/DevWorkspace/<proj>-worktrees (rw)│
│ ~/DevWorkspace/<proj>-worktrees ─┘                │ /packs/<karta>  (ro, data packs)              │
│ ~/.local/share/tide/box/<slug>/home ── disk ─────▶ /home/agent  (credentials, ~/.claude, cache)    │
└──────────────────────────────────┘                └──────────────────────────────────────────────┘
```

### 4.1 Where the agent runs — both variants

**Variant A: the agent on the host (bwrap as today), commands in the box.** Claude Code/Codex stay in today's
sandbox with tokens on the host; heavy commands go to the box. The problem: neither Claude Code nor Codex has a
"run Bash elsewhere". The channel would have to be indirect — today that is the `outside` result (the step ends, Tide runs the command,
resumes; every command = a new resume context) or a `tide-box` script in the sandbox's PATH that writes
a request to `<step>/out/.box/` and waits for the service's reply (the sandbox blocks sockets, so only a file queue).
Advantage: the agent's credentials never leave the host. Drawback: two worlds (what is "light", what is "heavy"), the agent
still has to know it is in a sandbox; a file queue is a brittle RPC; `npm install` in P1 on the host and in the box
must give the same `node_modules`.

**Variant B: the agent in the box (recommendation, consistent with K-38).** The whole step — the agent program and everything it runs —
works in the box as the `agent` user. One world: the agent does not know about the sandbox, does not ask, does not report `outside`.
`handoff.command_policy` and the command lists (`board/commands.py`) stop being the boundary — they stay as **hints**
(project prohibitions such as `make install` still in the prompt, but they need not be enforced; in the box `make install` installs
in the box). The price: the agent's credentials have to be in the box (4.2).

**[decision D1]** Recommendation: B. Variant A stays documented as a way back, should D2 turn out
unacceptable.

### 4.2 Agent credentials in the box and their risk

Facts [verified]: Claude Code keeps OAuth in `~/.claude/.credentials.json` (the refresh token — long-lived,
gives full access to the account/subscription); Codex in `~/.codex/auth.json`. For local models Tide passes the token through
`apiKeyHelper` in `--settings` (Claude) or an environment variable from a file (`secret_env`, `local.py`).

Threat: code from the repository run in the box (Z3/Z4) reads the agent's credentials and sends them over the network.
The worst effect: someone uses the user's Claude/ChatGPT subscription — **not** SSH keys, not production. This is
narrower than today (today the same `outside` reads `~/.claude/.credentials.json` *and* `~/.ssh`).

Options:

| Option | How | Protection | Cost |
|---|---|---|---|
| K1 Separate login in the box | `claude login` / `codex login` once in the Tide base image; `/home/agent` is the host directory `~/.local/share/tide/box/<slug>/home` (0700) | the credentials are read only by `agent`; the internal sandbox of Claude Code (`denyRead: ["~/.claude"]`) and of Codex stay on **[assumption: P-2 — bwrap/landlock in a container]**; the outbound proxy allows only the profile's domains, so a leak must go to `api.anthropic.com`/`registry.npmjs.org`… | smallest; login once per image; token refresh works by itself |
| K2 Token per step in the environment | `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` only in the agent process env | env is inherited by *all* the agent's commands (`/proc/self/environ`) — worse than a file with `denyRead` | small |
| K3 Token-injecting proxy | the box gets a dummy key per step; the Tide proxy on the host replaces the header with the real token; `ANTHROPIC_BASE_URL`/`OPENAI_BASE_URL` → proxy | there are no credentials in the box at all; a leaked dummy is worthless after the step | **[assumption: P-3]** it is not known whether the subscription login (OAuth) of Claude Code and the ChatGPT login of Codex accept another base URL; requires an own HTTPS proxy with MITM for one domain or an `http` mode to the proxy |

**[decision D2]** Recommendation: start with K1 (it works for sure, better than today), prototype K3 as the goal; reject K2.
Consequence of K1: the consent pack has an item "pudełko ma własne logowanie Claude/Codex; w razie podejrzenia wycieku —
`claude logout` w obrazie i ponowne logowanie" (the box has its own Claude/Codex login; if a leak is suspected —
`claude logout` in the image and log in again).

Local models (Ollama on the host `127.0.0.1:11434`): the box sees the host through the Incus bridge gateway (e.g.
`10.x.x.1`); Ollama listens only on loopback → either `OLLAMA_HOST=0.0.0.0` with nftables only for the bridge, or
`systemd-socket-proxyd` on the bridge address. `models.json` for the box points to the bridge address (an adapter in `models/`,
decision 60). Tokens of external endpoints: as today, from a file outside `~` — in the box that file is in the step's `in/` (0600).

### 4.3 Worktree and commits: mounting under identical paths

A Tide worktree is `~/DevWorkspace/<proj>-worktrees/<name>` with a `.git` file pointing **absolutely** to
`~/DevWorkspace/<proj>/.git/worktrees/<name>`; the step directories `<worktree>.step`, `<worktree>.review-N.step` lie next to it
in the same directory [verified: `ls ~/DevWorkspace/toolssuite-worktrees`]. Therefore:

- The box mounts (`incus config device add <box> … disk source=… path=… shift=true`) **under the same path as the host**:
  `~/DevWorkspace/<proj>/.git` (rw) and `~/DevWorkspace/<proj>-worktrees` (rw). The git pointers, the paths in the card
  journal (`stepDir`, `worktree`, `gitDir`), `resolve_git_dir` and `stepstop` work unchanged. `shift=true` is
  idmapped mounts (kernel ≥ 5.12; we have 7.0) — host uid 1000 ↔ uid 1000 `agent` in the box **[assumption: P-4, ext4 OK]**.
- The project's main worktree (the `master` files) is **not** mounted — as today (`denyRead: ["~"]` except `.git`).
- The "git bundle" alternative (a clone in the box, a bundle back) gives a full cut-off from the shared `.git`, but breaks the
  Tide worktree model (scanner, merging the ticket branch, `MergeCardToMain`, Sessions). Rejected; it stays as an option for
  `type: vm` in the future, if virtiofs fails.
- The step's `in/` directory: today read-only thanks to the sandbox; in the box the agent (the same uid) could overwrite it.
  There is nothing to protect (the prompt is already loaded; `exit-code` is written by the wrapper), but `in/env` with the local endpoint's token
  gets 0600 and is read by the wrapper before `exec`.

### 4.4 Ref guard (new)

The box has write access to the whole `<repo>/.git` (simplicity; Codex today: `objects`, `refs`, `logs`). Protection after the
step: before the start the service saves a `git for-each-ref` snapshot of the repository; after the step it compares — if
any branch other than `refs/heads/<ticket branch>` changed (or the worktree's `HEAD` is not that branch) → a
`refs.guard` event in the journal, a restore from the snapshot (`git update-ref`), the step ends with `error` and a reason. This replaces
today's "a full ban on writing outside the worktree cannot be promised" (decision 86) with something verifiable.
**[decision D7]**: whole `.git` rw + guard, or strict `objects/refs/logs` as with Codex.

**Correction after review (03.11, coordinator):** "whole `.git` rw" alone opens an escape from the box. The agent could add
a hook (`.git/hooks/*`) or a setting (`core.hooksPath`, `core.fsmonitor`, `core.sshCommand`, `include.path`,
`filter.*` in `.git/config`, and also `.git/info/attributes`) — the code would run **on the host, with the user's rights**, when
the user or Tide calls `git` in this repository. The ref guard does not see this. Therefore the recommendation:
`.git` rw **except** `.git/hooks`, `.git/config`, `.git/info` (mounted read-only or overwritten with an empty
read-only directory/file), the worktree configuration (`.git/worktrees/<name>/config.worktree`) also ro,
and the guard after the step additionally compares checksums of these paths; a change → restore and `error`. Tide on the
host calls `git` with `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, so that even an overlooked change
does not run automatically. Add to ticket #7 and to the acceptance scenarios (S1: a hook write attempt).

### 4.5 Network: Tide's outbound proxy with a domain list

- The box on the Incus bridge network (`incusbr0`, NAT). nftables on the host (a rule for the bridge, set up by
  `scripts/box/install.sh`): from the box **only** to the Tide proxy at the bridge address and (if the profile says so) to the
  host's Ollama; everything else DROP (DNS too — the proxy resolves names). We do not rely on Incus network ACLs
  (they are limited on a bridge) **[assumption]**.
- Tide proxy (`tide_daemon/box/proxy.py`, HTTP CONNECT without MITM, domain list from the profile, a denial journal per card).
  This is the same model that Claude Code uses in its own sandbox (`network.allowedDomains` = a proxy with a list).
  The box gets `HTTP_PROXY/HTTPS_PROXY/NO_PROXY` globally (`/etc/environment`, a `systemd` drop-in for `dockerd`),
  so npm, NuGet, pip, Playwright (browser download), `docker pull`, Claude Code and Codex go through it
  **[assumption: P-6 — check every client]**.
- The default list = the stack registries (today `node.REGISTRY_DOMAINS`) + model domains (`api.anthropic.com`,
  `statsig.anthropic.com`, `api.openai.com`, `chatgpt.com`…) + from the project profile (`networkDomains[slug]` from
  `cardflow.json` migrates to the profile). `github.com`, `git.firma.example`, `10.20.3.10` — **not** on the list, unless
  the profile adds them (then the consent pack shows it in red: "pudełko ma dostęp do zdalnego repozytorium" (the box has access to a remote repository)).
- A denial in the proxy journal is an artifact for the reviewer and material for "dopisz domenę do profilu" (add the domain to the profile) at the next plan.
- `localhost` in the box is the box: `ng serve`, Postgres from Testcontainers — no collision with host ports
  (today `4300` and `5432` are occupied on the host [verified]).

### 4.6 Ports

In the box ports collide only between steps of **the same project**. Tide allocates from the box pool
(e.g. 4300–4399) and passes `E2E_PORT`, `PORT`, `TIDE_PORT_BASE` (10 consecutive) in the step environment; the project profile
says which variable is the leading one (`toolssuite`: `PORT` in `playwright.config.ts` [verified], Karma: `--port`).
The "one heavy command per project" queue (`OUTSIDE_PER_PROJECT`) is no longer needed as port protection;
the box's resource limit stays. Host ports are not passed into the box (no `proxy device`), unless the profile
explicitly asks (e.g. a dev server preview from the host — an option for later).

### 4.7 Package cache, browsers, tools

In the box's `/home/agent` (the host directory `~/.local/share/tide/box/<slug>/home`, persistent across resets):
`~/.npm`, `~/.nuget/packages`, `~/.cache/ms-playwright`, `~/.cache/pip`, `~/.dotnet`. The behavior of `node.install`
and `dotnet.restore` before the step stays (now in the box, with the network through the proxy) — it still shortens the step. The box has
`xvfb` and `weston` (headless) in case of tests requiring X/Wayland; Chrome: `CHROME_BIN` from Playwright as today.
Agent tools in the Tide base image: Claude Code (native installer, version pinned to the host: `2.1.317`),
Codex from npm (`@openai/codex`, not the snap — there is no snap in the box and we do not want one), `gh`/`glab` **not** (no need).

### 4.8 Docker in the box

Incus configuration: `security.nesting=true`, `security.syscalls.intercept.mknod=true`,
`security.syscalls.intercept.setxattr=true` (per the Incus documentation "Docker inside Incus"). `docker.io` from apt in the box,
`agent` in the box's `docker` group. Testcontainers (.NET 4.15, [verified in `Directory.Packages.props`]) sees
the box's `/var/run/docker.sock`. Images from the profile (`pgvector/pgvector:pg16`) pulled at build time through the proxy
(`registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com` on the domain list). The user's Docker
on the host is invisible. Risk: nested overlay in userns with AppArmor on kernel 7.0 — **[assumption: P-1]**.

### 4.9 Resources and parallelism

- `limits.cpu=8`, `limits.memory=24GiB`, `limits.memory.swap=false`, disk: a 60 GB volume in a btrfs pool (the file
  `/home/tide-incus-pool.img`, sparse, 200 GB) — btrfs gives cheap snapshots on ext4 without rebuilding partitions.
  **[decision D8]** the values.
- Steps in the box as today: `systemd-run --user --scope -p CPUWeight=40` inside (the `agent` user with lingering).
  Provider slots and waves (PT-62) unchanged. Several steps of one card and of several cards of the same project share
  one box — this is intended (one Docker, one cache). A `clean` reset only when there are no steps in the box
  (the service knows this from `procs`/the journal).
- A box per card (`box: per-card` in the profile) as an option for cards that tinker with global state (e.g. changing the
  Node version): `incus copy <proj>/clean <proj>-<card>` is cheap on btrfs. Default: per project. **[decision D3]**

### 4.10 Cleanup and lifecycle

- Box states in the Board journal: "brak" (none) → "budowa" (build) → clean (snapshot) → "w użyciu" (in use) → "nieaktualny" (outdated; the recipe changed)
  → reset. A view in the Tide window (K-36, projects) with actions: build, reset, stop, remove.
- Stopping the box after N hours without a step (`incus stop`), start at the first step (≤ 30 s, S3).
- Cleanup in the box after a step: nothing (the worktree stays for review anyway); after the card is closed: `docker system prune`
  in the box, removal of Testcontainers containers (ryuk does this itself).
- Old worktree state (`node_modules`, `bin/obj`) as today — when the worktree is removed (K-36 Worktrees).

## 5. Project environment profile at the plan stage

### 5.1 Stack and CI detection (deterministic, in the service)

A new module `tasks/stack.py` (extends today's `dotnet.is_dotnet`, `node.is_node`, `node.registry_domains`):
reads the card's worktree and returns a `StackReport` — facts, not decisions:

| Source | What it detects | Project [verified] |
|---|---|---|
| `package.json` scripts, `angular.json`, `karma.conf.js`, `playwright.config.*`, `e2e/protractor.conf.js` | Node + version (`.nvmrc`, `engines`), Angular, Karma (`browsers: ['Chrome']`, port 9876), Playwright (`PORT`, `webServer`), Protractor (Chrome, `localhost:4200`) | toolssuite: Angular 19, Karma Chrome, Playwright in the worktree `k-31-refactor-testability`, Protractor in master |
| `*.sln`, `*.csproj`, `Directory.Packages.props`, `global.json`, `NuGet.Config` | .NET + TFM, Testcontainers (+ images from code: `PostgreSqlBuilder("pgvector/pgvector:pg16")`), Playwright .NET, additional NuGet registries | agent-team: net10.0, Testcontainers 4.15, `Microsoft.Playwright 1.52`; infracore: net6/net8/netstandard2.0, `nuget_devhost.config` (a private registry!) |
| `docker-compose.yml` | images, ports, services | agent-team: `pgvector/pgvector:pg16` on 5432 |
| `.gitlab-ci.yml`, `.github/workflows/*` | CI image, build/test commands, services | toolssuite: build in `node:20` in Docker, `rsync` to the devhost (deploy — we ignore it); infracore: `dotnet restore --configfile`, `dotnet test tests/RefactoredInfrastructureTests`, deploy through `rsync`+`sudo` (we ignore it) |
| `Makefile`, `pyproject.toml`, `requirements.txt`, `mk/*.mk` | targets `test`, `lint`; Python; `QT_QPA_PLATFORM=offscreen` | plasma-tide: `make test` = `python3 -m unittest discover` in `TEST_DIRS`; offscreen QML views; user font tests (K-33) |
| `CLAUDE.md`/`AGENTS.md` | test commands described in words (for the planner, not for a parser) | agent-team CLAUDE.md: "Testcontainers + WebApplicationFactory" |

The report goes into the planning prompt (`planning.prompt`, `fullcard`) as a section "Środowisko projektu (wykryte)" (Project environment (detected))
and is the basis of a profile proposal generated **by the service** (templates per stack, 5.4). The planner model may
correct the profile (add domains, images, apt packages, verification commands) in `out/box.yaml`; the plan validator
(`plans.read`) checks the schema the way it checks the ticket table.

### 5.2 Profile format

A YAML file (the service already reads YAML frontmatters; the schema in `board/boxprofile.py`):

```yaml
tide-box: 1
project: toolssuite
type: container            # container | vm
base: images:ubuntu/24.04  # Incus image; Tide adds the "tide-base" layer (agents, git, proxy)
stacks: [node]             # Tide templates: node, dotnet, python-qt, docker, playwright
node: { version: "20" }
apt: [xvfb]                # on top of the templates
setup:                     # build commands after the templates (in the box, with network through the proxy)
  - npx playwright install chromium
network:
  domains: [registry.npmjs.org, fonts.googleapis.com, fonts.gstatic.com, playwright.azureedge.net]
  host-ollama: false
ports: { pool: [4300, 4399], env: [PORT, E2E_PORT] }
docker: { enabled: false, images: [] }
resources: { cpu: 8, memory: 24GiB, disk: 60GB }
verify:                    # commands Tide uses to check the box after the build and which the reviewer can cite
  - npm run lint
  - npm test -- --watch=false --browsers=ChromeHeadless
packs: []                  # read-only user data packs (section 6.2)
box: per-project           # per-project | per-card
```

**Where it lives [decision D5].** Recommendation: in the Board, `~/DevWorkspace/tide-board/projects/<slug>/box.yaml`
(next to `projects/<slug>.md`), because (a) company: we do not commit Tide matters to the repo (`~/.claude/CLAUDE.md`), (b) the profile
is the user's consent, not part of the code, (c) the Board is already a repository with history. Optionally `.tide/box.yaml`
in the project repository (plasma-tide, LegalApp) as the **source of a proposal**, which is copied to the Board anyway after
approval. A card may extend the profile (`box:` in the plan frontmatter or `out/box.yaml` from planning) —
an extension is subject to the same approval.

### 5.3 How the box is created

1. **Tide base image** (once per host, `make box-base` / `tide box base`): `images:ubuntu/24.04` + git, build-essential,
   curl, ca-certificates, `xvfb`, user fonts (K-33: copied from `~/.local/share/fonts`, not mounted),
   the `agent` user (uid 1000, lingering), Claude Code (host version), Codex (npm), the proxy in `/etc/environment`,
   agent login (K1) → snapshot `tide-base/clean`. Rebuilt when the agent version changes.
2. **Project box**: `incus copy tide-base/clean <slug>` → stack templates (`box/stacks/node.sh`, `dotnet.sh`,
   `python-qt.sh`, `docker.sh`, `playwright.sh`) → `apt`, `setup` from the profile → `docker pull` of images → `verify`
   (result in the Board journal, non-blocking) → snapshot `<slug>/clean` with the profile hash in the description.
3. **Step**: if the box exists and the profile hash = the snapshot hash → `incus start` (when stopped) and `incus exec`.
   A different hash → the consent pack shows "przepis się zmienił: przebuduj przed startem" (the recipe changed: rebuild before start) (build in the background, the first step waits;
   card state "czeka na pudełko" (waiting for the box)).
4. **Reset**: `incus restore <slug> clean` when there are no steps; manually from the Tide window; automatically after a `per-card`
   card is closed.

### 5.4 Stack templates (what Tide can do without asking)

| Template | Installs | Domains | Default verify |
|---|---|---|---|
| `node` | nvm + Node from the profile (by default the version from `.nvmrc`/`engines`/the host's `20`), `npm ci` at build for the worktree? no — dependencies are per worktree (like today's `node.install` before a step) | `registry.npmjs.org`, `registry.yarnpkg.com`, `fonts.googleapis.com`, `fonts.gstatic.com` | `npm run build`, `npm test -- --watch=false --browsers=ChromeHeadless` if Karma |
| `playwright` | `npx playwright install --with-deps chromium`, `CHROME_BIN` | `playwright.azureedge.net`, `cdn.playwright.dev` | `npx playwright --version` |
| `dotnet` | SDK from `global.json` or the host (`10.0`), `dotnet restore` at build, env from `dotnet.step_env` (still without build servers, because it is more stable) | `api.nuget.org`, `globalcdn.nuget.org`, additional ones from `NuGet.Config` (e.g. `nuget_devhost.config` — a company registry on the local network: requires explicit consent in the package, because it is an internal address) | `dotnet build --no-restore` |
| `docker` | `docker.io`, `agent` in the `docker` group, `nesting` in the Incus configuration, `docker pull` of images | `registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com`, `index.docker.io` | `docker run --rm hello-world` |
| `python-qt` | `python3-pyqt6`, `python3-pyqt6.qtquick`, `qml6-module-*` from Tide's `Makefile`/`mk`, `QT_QPA_PLATFORM=offscreen` in the step env, fonts | `pypi.org`, `files.pythonhosted.org` | `make test` |

### 5.5 Approval with the plan — the consent pack

In the plan window (K-35), next to the executor assignments, a section **"Środowisko karty"** (Card environment):

- header: box `toolssuite` — state `clean` from 02.11 / `do zbudowania` (to be built) / `do przebudowy (zmienił się przepis)` (to be rebuilt (the recipe changed));
- a list with toggles and a risk color: network (domains — registries green, CDNs yellow, **red**: remote
  repositories, internal addresses `192.168.*`, `*.firma.example`, full network), Docker (yes/no + images), ports,
  data packs (6.2, always red — they are the user's data), resources, `per-card`;
- buttons: "Zatwierdź plan i środowisko" (Approve the plan and the environment) (save the profile to the Board, build in the background), "Tylko plan" (Plan only) (the card waits for the
  environment — it does not start), "Zmień" (Change) (YAML editing in the window).
- Changing the profile from the card during work: a step that needs something outside the profile ends as today with
  `denied`/`question` — but the question reads "dopisz do profilu projektu `X` i przebuduj / nie" (add to the profile of project `X` and rebuild / no), not "allow the
  command". Expectation: rare (S1), because the profile covers the stack and CI.

### 5.6 Example profiles of four projects

```yaml
# plasma-tide
tide-box: 1
project: plasma-tide
stacks: [python-qt]
apt: [qml6-module-qtquick-controls, qml6-module-qtquick-layouts, libxkbcommon-x11-0, kirigami2-dev]  # [assumption: pick from Makefile/mk]
setup: [cp -r /packs/fonts ~/.local/share/fonts]   # the user's fonts from a pack (K-33)
network: { domains: [pypi.org, files.pythonhosted.org], host-ollama: true }   # local model tests
docker: { enabled: false }
verify: [make test, make lint]
notes: "make install w pudełku instaluje w pudełku — nieszkodliwe; testy ekranu blokady/SDDM/KWin tylko na hoście"
```

```yaml
# legalapp (agent-team)
tide-box: 1
project: legalapp
stacks: [dotnet, docker]
dotnet: { sdk: "10.0" }
docker: { enabled: true, images: [pgvector/pgvector:pg16] }
network: { domains: [api.nuget.org, globalcdn.nuget.org, registry-1.docker.io, auth.docker.io, production.cloudflare.docker.com] }
ports: { pool: [5500, 5599], env: [ASPNETCORE_URLS_PORT] }
verify: [dotnet build LegalApp.sln --no-restore, dotnet test tests/LegalApp.Tests.Unit --no-restore]
# "api.sejm.gov.pl" (curl from outsideCommands 03.11) — NOT by default; a card fetching legal acts adds it to the card profile.
```

```yaml
# toolssuite
tide-box: 1
project: toolssuite
stacks: [node, playwright]
node: { version: "20" }
network: { domains: [registry.npmjs.org, fonts.googleapis.com, fonts.gstatic.com, playwright.azureedge.net] }
ports: { pool: [4300, 4399], env: [PORT, E2E_PORT] }
docker: { enabled: false }
verify: [npm run lint, npm run build, npx playwright test --workers=2]
# `nuget_devhost`/devhost/rsync from CI: deploy — outside the box, never.
```

```yaml
# infracore (if it enters the Board)
tide-box: 1
project: infracore
stacks: [dotnet]
dotnet: { sdk: ["8.0", "6.0"] }          # TFM net6/net8 [verified]
network: { domains: [api.nuget.org, globalcdn.nuget.org] }
# NuGet.Config: nuget.org; nuget_devhost.config points to an internal registry — a red item, disabled by default
verify: [dotnet restore InfraCore.sln --configfile NuGet.Config, dotnet test tests/RefactoredInfrastructureTests -c Release --no-restore]
# IntegrationEmbeddedDbTests/IntegrationTests: Firebird? [assumption: check at the first plan; the image jacobalberty/firebird is possible]
```

`LogTool` does not exist in `~/DevWorkspace` [verified] — I skip it.

### 5.7 A project without a reference

`stacks: []`, the base image, the planner gets a report with an empty detection and the instruction: "propose `apt`, `setup`,
`verify`, `network.domains` based on README/CI; when you do not know — leave empty and mark in the plan a question to the
user". The question goes to the plan window (once), not during the steps. A minimal box still gives a hard
boundary — it will not be worse than today, only more `denied` for missing tools, which the user adds to the profile.

## 6. The exit boundary

### 6.1 What leaves the box

| Channel | Content | Who controls |
|---|---|---|
| shared `.git` | commits on the ticket branch (the ref guard 4.4) | the service; push only by Tide/K-36 after a click |
| `<step>/out` | `result.json`, notes, the `step.log` journal, test artifacts (log, summary — ticket #9 from `sandbox-and-permissions.md` §6) | the service reads as today |
| outbound proxy | HTTPS only to the profile's domains (models, registries); a denial journal | the profile (consent with the plan) |
| nothing else | no D-Bus, no host sockets, no host ports, no clipboard, no `~` | — |

### 6.2 User data packs (read-only)

A need from 03.11: a card needed the user's session logs and a copy of the Board. The `packs` mechanism in the card
profile (not the project's — it is a one-off consent):

```yaml
packs:
  - name: sessions-2026-11-03
    from: ~/.claude/projects/-home-tomek-DevWorkspace-plasma-tide/*.jsonl
    since: 2026-11-03
  - name: board
    from: ~/DevWorkspace/tide-board/cards/K-3*.md
```

The service **copies** (does not mount) the files to `~/.local/share/tide/box/<slug>/packs/<card>/<name>` and mounts them in the box
under `/packs/<name>` with `readonly=true`. The consent pack shows the list of files, sizes and the count, and the result of a simple
secret scanner (patterns `sk-ant-`, `ghp_`, `BEGIN … PRIVATE KEY`, `Authorization:`) — a hit blocks the pack until
a manual choice. After the card is closed the pack is removed. Mounting the whole `~/.claude` never.

### 6.3 The red list (`make box-check`)

Run in a real box as the `agent` user; every item must **fail**:
`ls /home/user/.ssh`, `cat /home/user/.git-credentials`, `ls /home/user/DevWorkspace/plasma-tide/apps` (the main
worktree), `curl https://example.com`, `curl https://github.com`, `nslookup github.com`, `docker -H unix:///host/docker.sock ps`
(absent), `dbus-send --session …`, `git push origin HEAD` in the worktree (no network + no credentials), `gh auth status`
(no program), `cat /proc/1/environ` of the host (invisible), a write outside `~/agent`, the worktree and `.git`, `nc <host ip> 5432`
(the user's Postgres), `cat ~/.claude/.credentials.json` **from the agent's Bash command** (the internal sandbox,
if P-2 succeeds — otherwise the item is marked as residual K1 risk).

### 6.4 What still requires a user decision (P3 stays)

Push, MR, merge to main, deployment, `make install` on the host, tests requiring a real Plasma, access to
internal addresses (`10.20.3.10`, `git.firma.example`, `nuget_devhost`), data packs, full network.

## 7. Migration from the current state

### 7.1 What happens to the existing mechanisms

| Today | After the box |
|---|---|
| `engine._start_outside`, `_outside_ready`, `_outside_busy`, `OUTSIDE_*`, the `outside` result in `RESULTS`, `ENV_QUESTION`/`ENV_ANSWER` | **Stage 1:** `_start_outside` runs the command in the project's box (`incus exec`) instead of `systemd-run` on the host — it immediately closes the hole for projects with a box, before the agent enters the box. **Stage 2:** removed; `ENV_ANSWER` changes its content to "pracujesz w pudełku projektu: instaluj, uruchamiaj serwery, Docker i testy sam" (you work in the project's box: install, run servers, Docker and tests yourself). |
| `outsideAuto`, `outsideCommands`, `autoConsent` in `cardflow.json` | `outsideCommands` → seeding of the profiles' `network.domains` and `verify` (a one-off migration with a list for approval); `outsideAuto`/`autoConsent` meaningless in the box; they stay for projects in transitional mode |
| `board/commands.py`: allowed/forbidden lists, `sandbox_grant`, `claude_tools`, `outside_*` | in the box `--allowedTools` is not needed (`autoAllowBashIfSandboxed`/`permission-mode` without a list); project prohibitions stay in the prompt as a hint; `outside_*` removed in stage 2 |
| `agentcmd._claude` settings: `sandbox.filesystem.denyRead ["~"]`, `allowedDomains` | in the box: `denyRead: ["~/.claude", "~/.codex", "/packs"?]` (credential protection, P-2), `allowedDomains` empty (the network is guarded by the proxy); `failIfUnavailable` stays; when bwrap in the container does not work — `sandbox.enabled=false` and full K1 risk |
| `agentcmd._codex`: `--sandbox workspace-write`, `writable_roots`, disabling MCP | in the box `--sandbox danger-full-access`? No: `workspace-write` stays with roots as today (a second layer, cheap); MCP: Codex from npm in the box has an empty configuration — `list_codex_mcp` is redundant |
| `tasks/node.py`, `tasks/dotnet.py` (`install`, `restore` before a step on the host) | the same functions through `BoxRunner` (in the box); `node_home`, `chrome_bin`, `packages_dir` point to box paths; `REGISTRY_DOMAINS` → stack templates |
| `agentcmd.Background.start` (`systemd-run --user --scope` on the host) | `Runner` with two implementations: `HostRunner` (today; transitional mode and tests) and `BoxRunner` (`incus exec <box> --user 1000 --group 1000 --cwd <wt> -- /usr/bin/env -i … systemd-run --user --scope …`). Sensitive variables not in the `incus exec` argv (visible in the host's `ps`): the wrapper reads `in/env` (0600) |
| `tasks/stepstop.py` (`systemctl --user kill/stop`, WIP commit in bwrap on the host) | kill through `incus exec <box> -- systemctl --user …`; WIP commit: the service's git on the host in bwrap **as today** — the host sees the worktree unchanged (the same ext4) |
| `agents/scanner.py` reads `~/.claude/projects` for Sessions; `_hidden` | the step's session journal in `~/.local/share/tide/box/<slug>/home/.claude/projects` — the scanner gets a second root (as `codex_homes.default_homes` has several directories) |
| `docs/sandbox-and-permissions.md` | update: §4 the level table → P1/P2 replaced by the box, P3 unchanged |
| AppArmor profile `bwrap` on the host | stays (planning and transitional mode); in stage 2 used only by `stepstop` (WIP commit) and the Console |

### 7.2 Transitional mode

A per-project setting in the Board: `box: off | outside | full`.
- `off` — today (bwrap + `outside` with `outsideAuto`).
- `outside` — stage 1: the agent on the host, `outside` in the box. Recommended right after the first box is built.
- `full` — stage 2: the agent in the box.
A project without a box stays in `off`; the service does not switch by itself. After all projects enter `full` —
a cleanup ticket removes `off`/`outside`.

### 7.3 Tide tests (box dummy)

- `tests/fakebin.py` gets `incus` (like `systemd-run`, `konsole`): it writes argv to `TIDE_FAKE_LOG` and **executes**
  the part after `--` locally (`exec "$@"`) — thanks to this the existing `agentcmd`/`cardflow_execution` tests pass with
  `BoxRunner` without Incus. `incus list/snapshot/restore/config` return a fixed JSON from the dummy's state directory.
- `test_agentcmd.py` (1137 lines [verified]) extended with `BoxRunner`: argv has `--user 1000`, `--cwd`, no tokens
  in argv, `in/env` 0600; `HostRunner` unchanged.
- New `test_box_profile.py` (schema, detection on fixtures of the four projects — small file trees),
  `test_box_builder.py` (order of build steps, hash, rebuild), `test_box_proxy.py` (CONNECT to a domain from the list /
  outside the list), `test_refs_guard.py`.
- A real Incus only in `make box-check` (manually, not in `make test`).

### 7.4 Acceptance scenarios (S1) — a repeat of 03.11

1. toolssuite: a ticket adds a package (`npm install --save-dev angular-eslint@^19`), runs `npm run lint`,
   `npx playwright test --workers=2` on a port from the pool; two tickets in parallel (two ports) — 0 questions.
2. toolssuite: Karma `ChromeHeadless` (`CHROME_BIN`) — 0 questions.
3. LegalApp: `dotnet test tests/LegalApp.Tests.Integration` with Testcontainers/pgvector — 0 questions, the box's Docker.
4. plasma-tide: `make test` (offscreen QML with fonts) — 0 questions; `make install` — executes in the box with no
   effect on the host (a prohibition today) — to decide whether to keep the prohibition as a hint (yes).
5. A command > 300 characters and with `find -exec … \;` — no denial (the command lists are no longer the boundary).
6. A session log pack in the card profile — visible in `/packs`, approved with the plan.
7. The red list (6.3) — everything refuses.

## 8. Division into tickets

Working numbering `#n` (the plan assigns `PT-n` at approval, standard 1.2). Classes per `docs/agent-plan-standard.md`
(the `Klasa` (class) values `mocny` (strong) and `standard` and the header names `Tytuł` (title), `Blokery` (blockers) and `Ryzyka dla recenzenta` (risks for the reviewer) are kept verbatim).

| # | Tytuł | Klasa | Blokery | Ryzyka dla recenzenta |
|---|---|---|---|---|
| #1 | Incus prototype on KDE neon: installation after a simulation, an unprivileged box with `nesting`, Docker inside (`hello-world`, `pgvector` from Testcontainers), Claude Code's bwrap and Codex's landlock inside, `shift=true` on ext4, a CONNECT proxy for npm/NuGet/Docker/Claude/Codex, group `incus` vs `incus-admin`. Result: the note `docs/box-prototype.md` with the table P-1…P-7 (section 10) | mocny | — | this is the only ticket with `sudo` and a system change; the reviewer checks that the install script is reversible (`scripts/box/uninstall.sh`) and that nothing touches Qt/KDE; a "does not work" result is acceptable and switches to plan B |
| #2 | Project environment profile: the `tide-box` schema (`board/boxprofile.py`), stack and CI detection (`tasks/stack.py`), storage in the Board `projects/<slug>/box.yaml`, migration of `networkDomains`/`outsideCommands` into the profile proposal, a report in the planning prompt (light and full), validation of `out/box.yaml` in `plans.read` | standard | — | detection must be deterministic and tested on fixtures; no "full network" decision automatically; the profile does not go to the firma repo |
| #3 | Installation and pool: `scripts/box/install.sh` (apt after `-s`, group, `incus admin init --preseed` with a btrfs loop on `/home`, bridge, nftables default-deny for the bridge, restricted project), `uninstall.sh`, `make box-install`, documentation in `docs/tide-daemon.md` | standard | #1 | the script is run by the user with sudo (pattern PT-75); the reviewer checks idempotence and that the theme's `make install` does not call this script |
| #4 | Base image and building the box from the profile: `tide_daemon/box/` (`builder.py`, `stacks/*.sh`, recipe hash, `clean` snapshot, `verify`, state in the Board), CLI `tide box base|build|status|reset|destroy`, an `incus` dummy in `fakebin` | mocny | #2, #3 | the stack templates are shell code run in the box — the reviewer reads them like production code; the build must not require interaction; agent login (K1) has an instruction, not automation |
| #5 | Tide outbound proxy and the box network: `box/proxy.py` (CONNECT, domain list, a denial journal per card), nftables (from #3), `HTTP(S)_PROXY` in the image and `dockerd`, host Ollama through the bridge address (`models/` adapter), the red list `make box-check` | mocny | #4 | the proxy listens only on the bridge address; no MITM; tests: a domain outside the list → 403 and a journal entry; the reviewer checks that the model domain list contains no remote repositories |
| #6 | `BoxRunner`: a step in the box (`agentcmd.Runner`, `incus exec` + `systemd-run --user --scope` inside, `in/env` 0600, identical paths, mounting `.git` and worktrees `shift=true`), `stepstop` through the box, the session scanner with a second root, the project setting `box: off|outside|full`, stage 1 (`_start_outside` in the box) | mocny | #4 | tokens not in the `incus exec` argv; `HostRunner` with no behavior change (existing tests green); a service restart does not lose a step in the box (`procs` + a unit in the box) |
| #7 | Ref guard and the `.git` boundary: a `for-each-ref` snapshot before the step, comparison after, `refs.guard` in the journal, restore, result `error`; decision D7 | standard | #6 | it must not roll back the ticket branch; a race with parallel steps of the same card (different ticket branches) — a test |
| #8 | Docker in the box and ports: the `docker` template with `nesting`, `docker pull` of the profile images at build, the port pool (`PORT`, `E2E_PORT`, `TIDE_PORT_BASE`) allocated in `_launch`, Testcontainers cleanup after the card is closed | standard | #4, #6 | ports only in env, not in argv; a port collision of two steps — a test; `docker system prune` only in the box |
| #9 | Consent pack in the plan window (K-35): the section "Środowisko karty" (Card environment) with risk colors, approval with the plan, "do przebudowy" (to be rebuilt), the question "dopisz do profilu" (add to the profile) instead of "allow the command", the state "czeka na pudełko" (waiting for the box) in the full card | standard | #2, #4, K-35 | red items (internal addresses, remote repo, full network, packs) are never enabled by default; the `D-Bus` contract described in `tide-daemon.md` |
| #10 | Read-only data packs: `packs` in the card profile, copying with a list and a secret scanner, mounting `/packs` ro, removal after the card is closed | standard | #9 | never mounting the source directory; the secret scanner is a filter, not a guarantee — name it so in the UI |
| #11 | Stage 2 and cleanup: the default `box: full` for projects with a box, new `ENV_ANSWER` content, removal of `outside*`, `sandbox_grant`/`claude_tools` as hints, migration of `cardflow.json`, update of `docs/sandbox-and-permissions.md`, a decision in `docs/decisions.md` | standard | #6, #7, #8, #9 | deleting code with tests — the reviewer checks that the tests covering `outside` were replaced, not deleted without an equivalent |
| #12 | Lifecycle and resources in the UI (K-36): box state on the project, build/reset/stop/remove, auto-stop after idleness, CPU/RAM/disk limits from the profile, the proxy denial journal as a step artifact for the reviewer | standard | #4, #5, K-36 | reset only without steps (a lock); limits read from the profile, not hard-coded |

Order: #1 → #2 ∥ #3 → #4 → #5 ∥ #6 → #7, #8 → #9 → #10, #11 → #12. After #6 (stage 1) the `outside` hole is
closed for projects with a box; after #11 the boundary is uniform. Size estimate: #1, #4, #5, #6 — large
(mocny), the rest standard.

## 9. Questions to settle with the user (a list for grilling)

| ID | Question | Recommendation | Consequences of the choice |
|---|---|---|---|
| D1 | The agent in the box (B) or on the host with commands in the box (A)? | **B** | A: tokens stay on the host, but two worlds, a file-queue RPC, the agent still "knows about the sandbox"; B: one world, 0 questions, the D2 risk |
| D2 | Agent credentials in the box: K1 separate login in the image / K2 token in env / K3 injecting proxy | **K1 now, K3 after prototype P-3** | K1: residual risk = abuse of the subscription on a successful attack on the box (narrower than today); K3: zero secrets in the box, but uncertain OAuth support through another base URL |
| D3 | A box per project or per card? | **per project**, `per-card` as an option in the profile | per project: one Docker/cache, cheaper, cards share state (e.g. the global Node version); per card: cleanliness at the cost of disk, RAM and build time |
| D4 | Container or VM as the default? | **container**, `type: vm` for a low-trust repo | VM: stronger kernel isolation, but permanent RAM, virtiofs, a longer start |
| D5 | The profile in the Board or in the project repository? | **Board** (`projects/<slug>/box.yaml`), `.tide/box.yaml` in the repo as an optional source of a proposal (not in firma) | repo: versioned with the code, but the user's consent would mix with code and land in company repositories |
| D6 | Default network: the profile's domain list or full network in the box? | **domain list through the proxy**; "full network" as a red profile option | full network in the box = a leak is possible anywhere, but zero problems with unknown CDNs; the list = sometimes a `denied` for a domain (once per project) |
| D7 | Writing to the shared `.git`: whole rw + ref guard, or only `objects/refs/logs` (like Codex)? | **rw without `hooks`/`config`/`info` (those ro) + a guard of refs and checksums of those paths** (correction 03.11: hooks and config are an escape to the host) | strict: safer, but ro `.git` hooks/configuration are sometimes a problem (the agent's `git worktree` operations); the guard detects, does not prevent |
| D8 | Box limits: 8 vCPU / 24 GiB / 60 GB, a 200 GB btrfs pool on `/home`? | **yes** (host: 16 cores, 78 GiB, 220 GB free) | smaller limits = slower e2e/`dotnet test`; larger = the desktop will feel it |
| D9 | The service's Incus permissions: the `incus-admin` group (root-equivalent) or a restricted project (`incus` + `incus-user`) with `restricted.containers.nesting=allow`? | **restricted project**, if P-5 allows nesting; otherwise `incus-admin` with a note in the decisions | `incus-admin`: every user process (the agent in the Console too!) can create a privileged container = host root; a new surface |
| D10 | Transitional mode: keep `outsideAuto` enabled until stage 1? For which projects? | **yes**, as today per project; after #6 `outside` goes to the box and `outsideAuto` becomes safe | turning it off now = a return of questions every 2 minutes |
| D11 | Do tests requiring a real Plasma/KWin (lock screen, SDDM, KWin scripts) stay manual on the host? | **yes**; the step writes "wymaga sprawdzenia na hoście" (requires checking on the host) in the note, the reviewer does not ask for a run | the alternative (Plasma in a VM with a GPU) — out of scope |
| D12 | Tide's `make install` in the box: keep the prohibition as a prompt hint, although it is harmless? | **yes** (the agent should not waste time) | without the prohibition the agent may "install" in the box and conclude it checked on the desktop |
| D13 | Can internal addresses (`nuget_devhost`, `10.20.3.10`, `git.firma.example`) ever be in the project profile, or only in the card's? | **only the card's**, red | in the project profile = every card of that project has access to the company network |
| D14 | Reset to `clean`: automatically after the card is closed, or manually only? | **manually + automatically when the recipe is rebuilt**; `per-card` always after closing | does an automatic project reset delete the Docker image cache? no — the cache is in `/home/agent` (persistent), the reset concerns the rootfs |
| D15 | Should the box also be available to sessions in the Console (an assignment from the Board, "Nowe zadanie" (New task))? | **later** (a separate card) | now: only background steps; the Console as today |

## 10. Risks and unknowns — with prototypes

| ID | Unknown | How to check (ticket #1) | If it fails |
|---|---|---|---|
| P-1 | Docker in an unprivileged Incus on kernel 7.0 with AppArmor (overlay2 in userns, `nesting`) | `incus launch images:ubuntu/24.04 t -c security.nesting=true -c security.syscalls.intercept.mknod=true -c security.syscalls.intercept.setxattr=true`; `apt install docker.io`; `docker run hello-world`; LegalApp `dotnet test` with Testcontainers | Podman in the box with a Docker-API-compatible socket (`podman system service`) for Testcontainers; or `type: vm` for projects with Docker |
| P-2 | bwrap (the Claude Code sandbox) and landlock (Codex) inside a container: a nested userns with `apparmor_restrict_unprivileged_userns=1` (a kernel sysctl, probably global), the container's AppArmor namespace | in the box: the `bwrap` profile with `userns` as on the host; `claude -p` with `sandbox.enabled=true`; `codex exec --sandbox workspace-write` | `sandbox.enabled=false` in the box — the box itself is the boundary; the K1 risk grows (credentials readable by test code) → then K3 is more urgent |
| P-3 | Claude Code with a subscription login through `ANTHROPIC_BASE_URL` → an injecting proxy; Codex `chatgpt_base_url` | a small proxy on the host (`http`), `claude -p 'hi'` with the base URL | K1 stays; consider an API key with a budget for boxes (cost) |
| P-4 | `shift=true` (idmapped mounts) for directories on ext4 `/home`; the behavior of `git` and `npm` on such a mount; `node_modules` performance | mount the worktrees, `git status`, `npm ci`, a measurement vs the host | `raw.idmap` (a static uid 1000 mapping) — an older method, works without idmapped mounts |
| P-5 | An Incus restricted project (`incus-user`, group `incus`) with `security.nesting` for Docker | `incus project show user-1000` after `incus-user`; `restricted.containers.nesting` | `incus-admin` for the user `tomek` (decision D9) with a note in `docs/decisions.md`; alternative: a separate system user `tide` in `incus-admin`, the service through it (a bigger rebuild) |
| P-6 | A CONNECT proxy vs clients: npm, NuGet (`dotnet restore`), pip, `docker pull` (dockerd `HTTPS_PROXY`), Playwright install, `claude`, `codex`, Ollama (no proxy, directly to the bridge) | each client with the list and without; the denial journal | a client without proxy support → an nftables exception per domain resolved at box start (worse: IPs change) |
| P-7 | Incus 6.0.0 from Ubuntu universe (LTS, fixes only) vs the Zabbly repository (newer) — do the `images:` remote and `incus-agent` work with 6.0.0 | `incus image list images:` | the Zabbly repository (an additional apt source — a separate simulation and consent) |
| P-8 | Build time of the `dotnet` box (SDK ~1 GB) and `playwright` (~400 MB) through the proxy; disk space of 4 boxes | a measurement at #4 | an apt/image cache on the host (`apt-cacher-ng` — another package; rather not) |
| P-9 | `systemd --user` for `agent` in the container (lingering, `XDG_RUNTIME_DIR`), `systemd-run --user --scope` through `incus exec` | `incus exec t --user 1000 -- systemd-run --user --scope sleep 5` | `systemd-run` as the box root with `--uid=agent` (a service instead of a scope); tokens then only in `in/env`, not in the unit's properties |
| P-10 | Tide QML tests in the box: Qt6/QML packages in Ubuntu 24.04 vs versions on KDE neon (newer); offscreen with fonts | `make test` in the `python-qt` box | a base image from the KDE neon repository (`images:` has no neon; the neon repo can be added to apt in the box — it is a box, Qt may be raised) |
| P-11 | Session resume (`--resume`) after the box is rebuilt: the session journal in `/home/agent` (a host directory) survives a rootfs reset | reset + `--resume` | the session journal copied to `<step>/out` after the step (already partly: `handoff`) |

## 11. What next

1. Grilling over section 9 (D1–D15), starting with D1, D2, D9, D6, D3.
2. After the decisions: the K-38 card plan with the tickets from section 8 (`PT-n` numbers at approval), #1 first
   with the result "we go with Incus" or "plan B: a separate user + bwrap + rootless Docker" (option D from section 3).
3. Before the start: merge `fix/step-output-dir-writable` into `master` (the user's decision from the card), K-35 closed
   (the plan window for #9), K-36 in order after.
