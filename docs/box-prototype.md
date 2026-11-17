# Card box prototype — trial results (K-38)

Proof that the Incus container from `docs/card-box-design.md` (sections 4.1, 4.3, 4.8, 10) works on this computer, before
the service code exists. Trials P-1, P-4, P-5, P-9 and measurements: PT-164; trials P-2, P-3, P-6, P-7 (proxy and agents): PT-178
adds rows to the same table. Decisions from the trials: `docs/box-decisions.md` (not `docs/decisions.md`).

Each trial is a script `scripts/box/prototype/<name>.sh` run by the user outside the step sandbox
(one trial = one `outside` round). The script writes a log `docs/box-prototype/<name>.log`: every command, exit
code, time and shortened output, at the end rows `WYNIK <key>: ok|fail|skip|info` and a summary. The scripts
ask nothing (apart from the sudo password for nftables), do not use `security.privileged`, do not touch
`~/.local/share` or `~/.config` and do not mount the projects' main worktree.

## Results

### Final state (round 6, 04.11.2026)

| Trial | Result | How (variants and decisions in `docs/box-decisions.md`) |
| --- | --- | --- |
| P-1 | **works** | Docker 29.1.3 in an unprivileged container with `security.nesting=true`, without intercept, with **runc 1.2.6** as the default runtime (`runc-1-2-6-w-pudelku`); `docker run --rm hello-world` OK; LegalApp `dotnet test`: Testcontainers starts `pgvector/pgvector:pg16` and Ryuk, unit tests 5124/5137 (13 skipped), integration 1186/1238 — 51 failures from the project's test configuration, not from the box (below) |
| P-4 | **works** | `raw.idmap "both 1000 1000"` instead of `shift=true` (`raw-idmap-zamiast-shift`), mounts added while the box is stopped; git status/commit, npm ci, ro overlays, host visibility — round 4 |
| P-5 | **works** | nesting yes; intercept only after changing the project (unnecessary); NIC filtering mac + ipv4 rejects spoofing (`filtrowanie-nic-bez-ipv6`) — round 4 |
| P-9 | **works** | step as a service `systemd-run --user … --wait` (`krok-jako-usluga-user`): survives the death of the `incus exec` client and a `tide-daemon` restart; a scope survives a `tide-daemon` restart, but not the death of the client — round 4 |
| P-2 | **works with the AppArmor bwrap profile in the box** (PT-178) | Claude Code bwrap yes; Codex: the sandbox starts, the write block outside the directory is unverified — "PT-178 trials" below |
| P-3 | **K3 possible** (PT-178) | Claude Code through `ANTHROPIC_BASE_URL`, Codex only with its own model provider — "PT-178 trials" below |
| P-6 | **works** (PT-178) | clients through CONNECT without DNS, `NO_PROXY` honored; exception Node `fetch` 18 — "PT-178 trials" below |
| P-7 | **works** (PT-178) | Incus 6.0 from Ubuntu, `images:ubuntu/24.04` — "PT-178 trials" below |
| Measurements | **within thresholds** | overhead 0.066 s, start 2.3 s, reset 3.9 s (the reset requires `restricted.snapshots=allow`, `migawki-w-projekcie`) — round 4 |

**LegalApp `dotnet test` in the box — 51 integration failures, none from Docker or Testcontainers**
(`docs/box-prototype/p1-dotnet-test.log`): 36× `Connection string is not configured. Set DATABASE_CONNECTION env var
or ConnectionStrings:DefaultConnection in appsettings` (some tests read the connection from an environment variable the
box does not have — on the host the user sets it), 4× missing files `EvalSet/Cases/*/expected-classifier-acts.json`
(they are not in `git archive HEAD` — untracked or generated), 11× application assertions (localization texts,
HTTP 405 instead of 404, the number of scheduler runs, the order of search results). The explanation and the LegalApp
environment profile (variables, files, culture) belong to PT-184; no comparison with the result on the host was made.
**The "works" row concerns Docker and Testcontainers, not the test result:** 11 assertion failures (localization texts,
result order) are locale- and collation-dependent things, i.e. possibly dependent on the box environment. PT-184 has to
check them on the host with the same `dotnet test --filter` without `DATABASE_CONNECTION` — only that will settle whether
the box does not change the test result.

Times: installing .NET SDK 10.0 in the box 26.8 s, `dotnet test` (restore, build, tests) 1183 s; targets through the proxy:
`api.nuget.org`, `builds.dotnet.microsoft.com`, `dot.net`, `aka.ms`, the Docker registry, CRL servers (`crl3/crl4.digicert.com`,
`crl.sectigo.com`, `ts-crl.ws.symantec.com`, `s.symcb.com`, `www.microsoft.com`) — material for the domain list (P-6, #4).

### History of rounds

State: round 1 (04.11.2026, 11:29–11:39, Incus 6.0.0 after `make box-install`) gave partial results and revealed
script errors; round 2 (12:20–12:26, `all.sh`, logs `docs/box-prototype/*.log`) settled P-5 and P-1,
P-4 stopped at project restrictions, and its `raw.idmap` variant left a container that does not start, so
P-9 and the measurements did not run (script error fixed). The "Result" column is the state after round 2.

Round 3 (decisions `projekt-ograniczony-zostaje`, `raw-idmap-zamiast-shift` in `docs/box-decisions.md`):
`bash scripts/box/prototype/all.sh --install` — first `sudo bash scripts/box/install.sh --yes` (appends
`root:<uid>:1` to `/etc/subuid` and `/etc/subgid`, restarts `incus`, restores `restricted.networks.access=incusbr0`),
then the trials; P-1 checks in turn the runtimes runc, crun and the oldest runc from Ubuntu 24.04. If Docker
in the container does not start with any of them, the recommendation is `type: vm` for projects with Docker (LegalApp, toolssuite),
without Podman.

Result of round 3 (13:56–13:59, logs `docs/box-prototype/*.log`, `install.log`): the installation appended `root:1000:1`
and restored `restricted.networks.access=incusbr0`; **P-9 works in the `--user` service variant**, **measurements
within thresholds**, P-5 works (after fixing the address read), P-4 got to the mounts (a single file cannot be attached to a
running container — fixed), P-1 still does not work (runc, crun from Ubuntu, runc 1.1). Round 4:
`bash scripts/box/prototype/all.sh` (without `--install`) — P-4 with mounts while the container is stopped, P-1
with the newest crun and runc 1.2.6 from GitHub releases. On the host there is also a container `tide-proto-net` from outside the scripts
(the PT-178 trial box; after merging with PT-178 the shared `cleanup.sh` removes both, `--keep-box` keeps the containers).

Result of round 4 (14:45–14:48): **P-4, P-5, P-9 and the measurements work**; P-1 unresolved — both new runtimes
(crun 1.30.1, runc 1.2.6) were not checked because dockerd did not come up after the third restart within a minute (unit start
limit, script error — fixed). Round 5: only `bash scripts/box/prototype/p1.sh`.

| Trial | What it checks | Command | Result | Time | Fallback variant (when it does not work) |
| --- | --- | --- | --- | --- | --- |
| P-1 | Docker in an unprivileged container (`security.nesting`, intercept `mknod`/`setxattr`): `docker run --rm hello-world`, LegalApp `dotnet test` with Testcontainers (`pgvector/pgvector:pg16`) | `bash scripts/box/prototype/p1.sh` | **Round 6: works.** runc 1.2.6: hello-world OK; LegalApp `dotnet test` (SDK 10.0 from `TargetFramework`): Testcontainers with `pgvector/pgvector:pg16` and Ryuk works, unit 5124 OK / 0 errors, integration 1186 OK / 51 test-configuration errors (final state above), code 1 | SDK 26.8 s, dotnet test 1183 s | — |
| P-1 (history) | as above | as above | **Round 5: `docker run --rm hello-world` works with runc 1.2.6** (static from GitHub releases, SHA-256 `0774f49d…2ebc`, dockerd default runtime), without intercept, in the restricted project. runc 1.3.4 from apt: code 126 (`open sysctl net.ipv4.ip_unprivileged_port_start … reopen fd 8: permission denied`); crun 1.30.1: code 125 (`open /proc/sys/net/ipv4/ip_unprivileged_port_start: Permission denied`) — both new runtimes open `/proc/sys` in a way that the AppArmor profile of the Incus 6.0.0 container does not let through. LegalApp `dotnet test` did not start: the script failed (code 128) reading `global.json`, which is not in the repository — a script error, fixed; round 6: again `bash scripts/box/prototype/p1.sh`. **Round 4: unresolved.** runc 1.3.4 from apt: the same AppArmor/procfs error (code 126). crun 1.30.1 and runc 1.2.6 from GitHub releases downloaded and work (`--version`, SHA-256 sums in the log), but dockerd did not come up after the third restart within a minute ("Job for docker.service failed") — most likely the unit start limit (`StartLimitBurst`), a script error, not the runtime's; the same error may have falsified the runc 1.1 of round 3. Fixed (`systemctl reset-failed` before the restart, the unit journal in the log); round 5: only `bash scripts/box/prototype/p1.sh`. **Round 3: does not work.** runc 1.3.4 from apt: the same error `open sysctl net.ipv4.ip_unprivileged_port_start … reopen fd 8: permission denied` (code 126); crun 1.14 from Ubuntu 24.04: `OCI runtime create failed: unknown version specified` (too old for the OCI spec of Docker 29); runc 1.1.12 from apt (`--allow-downgrades`): dockerd does not come up (`See "systemctl status docker.service"`). Round 4 (script ready): the newest crun and runc 1.2.6 from GitHub releases (static). Recommendation in case of further failure: `type: vm` (decision `docker-w-vm`). **Round 2: does not work.** Network through a temporary proxy works (apt, `docker pull` from `registry-1.docker.io`), `docker.io` 29.1.3 with runc 1.3.4 installs, `docker info`: overlayfs, apparmor, seccomp. `docker run --rm hello-world` ends with code 126 — without intercept and with a temporary intercept the same: `OCI runtime create failed: runc create failed: unable to start container process: error during container init: open sysctl net.ipv4.ip_unprivileged_port_start file: reopen fd 8: permission denied`. `dotnet test` skipped. Probable cause: new runc (security fixes of 2025, reopening of `/proc/sys` files through `/proc/thread-self/fd`) versus the AppArmor profile of the container in Incus 6.0.0 from Ubuntu — **hypothesis, unverified**. Round 1 (rules `tide-proto`): forward was lost, the table before/after identical — `p1.log` is overwritten every round, so the evidence of the rules mode (the `inet tide-box` table before and after, `WYNIK nft-przed-po: ok`) is in the version from commit `5b6977a`: `git show 5b6977a:docs/box-prototype/p1.log` | 4.4 s (pull + error) | Podman in the box with a Docker API socket (`podman system service`) or `type: vm` for projects with Docker |
| P-2 (PT-178) | bwrap of Claude Code and landlock of Codex in a container | `bash scripts/box/prototype/p2.sh` | in "PT-178 trials" below | — | — |
| P-3 (PT-178) | OAuth of the agents through another base URL (K3) | `bash scripts/box/prototype/p3.sh` | in "PT-178 trials" below | — | — |
| P-4 | `<proj>/.git` and `<proj>-worktrees` from ext4 `/home` through `shift=true` under identical paths, ro overlays on `.git/hooks`, `.git/config`, `.git/info`; `git status`, `git commit` on `proto/k-38`, `npm ci` (container and host) | `bash scripts/box/prototype/p4.sh` | **Round 4: works (`raw.idmap` variant).** `shift=true` rejected by the restricted project; with `raw.idmap "both 1000 1000"` (decision `raw-idmap-zamiast-shift`) and mounts added while the container is stopped: uid 1000 ↔ 1000 under identical paths, `git status` and `git commit` on `proto/k-38` from the container (the commit is visible on the host, the file belongs to `tomek`), a write to `.git/hooks`, `.git/config`, `.git/info` rejected ("Read-only file system"), `npm ci --ignore-scripts` in toolssuite: host 8.6 s, container 11.6 s (cold cache) and 10.8 s (warm). The trial worktree and branch removed | npm ci: 8.6 / 11.6 / 10.8 s | — |
| P-4 (history) | as above | as above | **Round 3: partially.** After `make box-install` with `root:1000:1` the container with `raw.idmap "both 1000 1000"` starts; `.git`, `<proj>-worktrees` and `.git/hooks` (ro) attach to the running container, but the single file `.git/config` (ro) does not: `Failed to add mount for device inside container: … incusd forkmount lxc-mount … exit status 1`. Round 4: mounts added while the container is stopped (Incus sets them up at start). Git, npm and host visibility not yet checked. **Round 2: does not work in the restricted project.** `incus config device add … shift=true` rejected: "The "shift" property cannot be used with a restricted source path". The `raw.idmap "both 1000 1000"` variant accepted by the project (`restricted.idmap.uid/gid=1000` from PT-166), but the container did not start (`incusd forkstart … exit status 1`) — most likely a missing `root:1000:1` in the host's `/etc/subuid` and `/etc/subgid` (hypothesis). The trial worktree and branch `proto/k-38` removed; git, npm and host visibility not checked | — | `raw.idmap "both 1000 1000"` without `shift` (the script tries it itself and records the variant) |
| P-5 | Restricted project `user-1000` (`incus-user`, group `incus`): nesting, intercept, NIC filtering (`security.mac_filtering`, `security.ipv4_filtering`, `security.ipv6_filtering` on `eth0`), an attempt to spoof a foreign address | `bash scripts/box/prototype/p5.sh` | **Round 4: works.** Nesting in the restricted project works; intercept only with `restricted.containers.interception=allow` (blocked by default; Docker does not need it — it changed nothing in P-1). NIC filtering (mac + ipv4, decision `filtrowanie-nic-bez-ipv6`): the own address reaches `10.173.184.1:18080`, spoofing `10.173.184.250` does not (control without filtering: it reaches). **Round 3: works (after manual log analysis).** The script reported "not even the own address reaches", because `incus list tide-proto` also matched the second container `tide-proto-net` (10.173.184.91) and compared the wrong address; the test server recorded `10.173.184.164` (tide-proto) before and after enabling filtering, and `10.173.184.250` only before — spoofing with filtering (mac + ipv4) did not reach (`nc` code 1). Script error fixed (`box_ip` by exact name). **Round 2:** **Nesting works** (the project accepted `security.nesting=true`, `unshare --user --map-root-user --pid --fork --mount-proc true` OK). **Intercept does not work in the restricted project**: "Container syscall interception is forbidden" (`restricted.containers.interception=block`, the default); with a temporary `allow`: `mknod c 1 3` and `setxattr trusted.overlay.opaque` work. **NIC filtering works (mac + ipv4)**: control A (own address `10.173.184.164`) and B (spoofing `10.173.184.250` without filtering) reached `10.173.184.1:18080`; after `security.mac_filtering=true security.ipv4_filtering=true` the own address reaches, spoofing does not (`nc` code 1, no entry in the server journal). `security.ipv6_filtering=true` — the container does not start without `br_netfilter`; `false` was used (decision `filtrowanie-nic-bez-ipv6`) | 0.05 s (nc) | nesting/intercept: `--project-mode admin` in `scripts/box/install.sh` (group `incus-admin`, D9) with an entry `tryb-admin` or `restricted.containers.interception=allow` in the project (decision after round 2); NIC filtering: no variant — a necessary condition (`question`) |
| P-6 (PT-178) | Clients (npm, NuGet, pip, Docker, agents) through a CONNECT proxy and `NO_PROXY` | `bash scripts/box/prototype/p6.sh` | in "PT-178 trials" below | — | — |
| P-7 (PT-178) | Incus images (Tide base) | `bash scripts/box/prototype/p7.sh` | in "PT-178 trials" below | — | — |
| P-9 | the agent's `systemd --user` (lingering), `systemd-run --user --scope` through `incus exec`; the process survives the death of the `incus exec` client and `systemctl --user restart tide-daemon` | `bash scripts/box/prototype/p9.sh` | **Round 3: works in the service variant.** Lingering, `systemd-run --user --scope true` and `systemd-run --user --wait true` through `incus exec --user 1000` work. Death of the `incus exec` client (`systemctl --user stop tide-proto-client`): a process in a **scope dies**, in a **`--user` service** (`systemd-run --user --unit=… --collect --wait sleep 30`) and in a root service with `--uid=1000` it **survives**. `systemctl --user restart tide-daemon` does not kill the process (the client was not a child of the service). Decision `krok-jako-usluga-user` | 0.06 s | — |
| P-9 (history) | as above | as above | Round 1: **lingering and `systemd-run --user --scope` work; a process in a scope does NOT survive the death of the `incus exec` client** (`systemctl --user stop tide-proto-client` → `pgrep sleep` empty). The `tide-daemon` restart did not kill the process, but the client was not a child of the service — the test does not settle it. Round 2 did not run (container broken by `raw.idmap` from p4); the `--user --wait` service and root-service-with-`--uid` variants are waiting | 0.06 s | `systemd-run` as the box root with `--uid=agent` (a service instead of a scope; tokens only in `in/env`); closer to the project: `systemd-run --user` as a service (without `--scope`) |

### Conclusions from round 2

- **The restricted project from PT-166 in its current shape will not carry the box**: it blocks intercept, snapshots
  (`restricted.snapshots`) and `shift=true` on paths from `restricted.devices.disk.paths`. The first two are project
  settings (`allow`), the third is bypassed by `raw.idmap` (the project already has `restricted.idmap.uid/gid=1000`), which requires
  `root:1000:1` in `/etc/subuid` and `/etc/subgid` — all of it changes in the #3 scripts or on the host, so they require
  a decision (a question in PT-164).
- **Docker in the container fails regardless of the project and intercept** on runc 1.3.4 (`open sysctl
  net.ipv4.ip_unprivileged_port_start … permission denied`). To check: an older runc in the box, Docker CE
  from the Docker repository, a newer Incus (AppArmor profile) or Podman / `type: vm`.
- **NIC filtering (mac + ipv4) works in the restricted project** and rejects spoofing on the host — the per-IP proxy
  policy (#5) has its necessary condition.
- **The network through the proxy on the Tide proxy port works** for apt and `docker pull` (targets in the p1 log: `archive.ubuntu.com`,
  `security.ubuntu.com`, `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com`,
  `motd.ubuntu.com`) — material for P-6 and the domain list of the docker template (`production.cloudfront.docker.com`, not
  `cloudflare` as in section 4.8 of the design).

### Conclusions from round 1 (for subsequent tickets)

- **The host's Docker blocks forward from `incusbr0`.** `accept` rules in `inet tide-box` are not enough when another
  `hook forward` chain (most likely Docker CE's `ip filter FORWARD` with policy DROP) drops the packet — in nftables a drop
  in any base chain is final. The target architecture (box → only the proxy on the host, no forward)
  does not need this; that is why round 2 runs the trial network through a temporary proxy on the Tide proxy port, with no changes
  to the firewall. The rules mode (`PROTO_NET=rules`) stays and logs the `ip filter FORWARD` chain for diagnosis.
- **The restricted project from PT-166 blocks intercept and snapshots** (`restricted.containers.interception=block`,
  `restricted.snapshots=block` — Incus defaults). The reset from a snapshot (US-4) requires `restricted.snapshots=allow`;
  Docker perhaps works without intercept (kernel 7.0 allows overlayfs and whiteout in a user namespace) —
  round 2 settles it (p1 tries without intercept first).
- **`incus-user` appended its own bridge to the project:** `restricted.networks.access: incusbr0,incusbr-1000`, although the
  installation set only `incusbr0`. The `incusbr-1000` bridge is not covered by the `inet tide-box` table — to check
  in PT-166 (p5 logs `incus network list`).
- **`security.ipv6_filtering` requires the `br_netfilter` module** (Incus 6.0); loading it changes the filtering of all
  host bridges (Docker's too), so the trials do not load it. The bridge has no IPv6, and `inet tide-box` drops all traffic
  from `incusbr0` except the proxy and DHCP (IPv4) — round 2 checks mac + ipv4.
- **A process in `systemd-run --user --scope` dies together with the `incus exec` client** — a scope changes only the cgroup,
  and the process is executed by `incus exec`. BoxRunner (#6) cannot rely on a scope for surviving a service restart.

## Measurements (US-4)

`bash scripts/box/prototype/measure.sh` — three repetitions, median; exceeding a threshold is described with the cause
(it does not block PT-164, it blocks only #24).

| Measurement | Threshold | Measurements | Median | Within threshold |
| --- | --- | --- | --- | --- |
| Step overhead: `incus exec … --user 1000 -- systemd-run --user --scope true` | ≤ 3 s | round 4: 0.064 / 0.066 / 0.066 s (round 3: 0.056 / 0.052 / 0.060 s) | 0.066 s | yes |
| Step overhead (service): `incus exec … --user 1000 -- systemd-run --user --wait true` | ≤ 3 s | round 4: 0.070 / 0.073 / 0.077 s (round 3: 0.064 / 0.061 / 0.059 s) | 0.073 s | yes |
| Cold start of a stopped container (`incus start` until systemd finishes starting) | ≤ 30 s | round 4: 2.828 / 2.301 / 1.909 s (round 3: 3.811 / 1.813 / 2.395 s) | 2.301 s | yes |
| Reset: `incus snapshot restore` (until systemd finishes starting) | ≤ 60 s | round 4: 3.982 / 3.498 / 3.877 s (round 3: 3.962 / 3.304 / 3.291 s) — only with a temporary `restricted.snapshots=allow` (the PT-166 project blocks snapshots: "Project user-1000 doesn't allow for snapshot creation") | 3.877 s | yes, but requires `restricted.snapshots=allow` in the project |

### First build of the stack templates (PT-192)

Trial `bash scripts/box/stacks-trial.sh` from **2026-11-05 19:27:52**, commit `aab9dcad`, Incus 6.0.0:
all checks passed (code 0). The production builder and the proxy with the domain policy were used; the trial profiles
were approved with `tide box approve`. `STACKS_VERSION` and the directory checksum matched:
`7a15d6f7613824463834189090b264fd54eb34302d6929a76e2769c3f7ac39d5`.

| Measurement / check | Result |
| --- | --- |
| `tide box build plasma-tide`, python-qt | **58.8 s**, `clean`, threshold ≤ 900 s |
| `tide box reset plasma-tide` | **1.5 s**, `clean`, threshold ≤ 60 s; the file in `/home/agent` kept, the file in `/root` rolled back |
| Build of `tide-stacks-test`, dotnet + docker | **76.5 s**, `clean` |
| Status of both boxes | hash matches the profile, NIC filtering: yes (mac and IPv4 on, IPv6 off per the settings) |
| Proxy denial logs of both builds | empty; earlier `motd.ubuntu.com` denials were explained and the domain was added solely to `base.yaml: build-domains` |
| Programs as `agent` | Claude Code 2.1.317 and codex-cli 0.166.0 work |
| Qt | the PyQt6 imports required by the project and QGuiApplication passed; platform `offscreen` |
| .NET | SDK 8.0.427, `verify` and the NuGet.Config check passed |
| Docker as `agent` | `docker info` works: Docker 29.1.3, runtime `runc-126`; the local image `testcontainers/ryuk:0.11.0` available |
| dockerd environment | HTTP_PROXY and HTTPS_PROXY at `http://10.173.184.1:18080`, full `NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal`, no DOCKER_HOST |

python-qt hash: `cb1e5ea3cb315a07fc08de8a6585ea0e14f02c8180d716ab510ed038d9643a6c`;
dotnet + docker hash: `83b835c92db25fab340bc25876e22ba589d729c57e4fc5362a93f95e92b71925`.
The Ryuk image was pulled by the builder while the proxy was running; the check after the build reads it locally.
The new home volumes were initialized inside the boxes, and the no-swap check passed before the
templates (decisions `home-init-bez-initial` and `swap-zero-bez-lowlevel`). Both instances and the trial profiles
were removed, the temporary `restricted.snapshots=allow` was reverted to no value.

Sources of the measurements in the worktree: `docs/box-prototype/pt192-2026-11-05T192752.log` and a directory of the same
name without the extension (detailed build and denial logs). The full command output is also in the result
of step `pt-192-szablony-stosow-node-playwright.resume-K-38-PT-192-6.step/outside/outside.log` next to the worktree.
After the test isolation fixes in `7be29619` a separate **`make test` ended with code 0**: all groups
OK, including 2438 daemon tests (2 skipped). The result is in step `resume-K-38-PT-192-7.step/outside/outside.log`.
Shellcheck was unavailable; that check was skipped per the card's criterion. Measurements of a single
build do not replace the final performance assessment from #24.

## What is visible from the container (P-4)

| Check | Command | Result |
| --- | --- | --- |
| `~/.ssh` | `incus exec tide-proto -T -- test -e /home/user/.ssh` | not visible (code 1), round 4 |
| `~/.git-credentials` | `incus exec tide-proto -T -- test -e /home/user/.git-credentials` | not visible (code 1), round 4 |
| the project's main worktree | `incus exec tide-proto -T -- ls -A /home/user/DevWorkspace/toolssuite` (only `.git`) | only `.git` visible, round 4 |
| the host's Docker socket | `incus config show tide-proto --expanded` (no `docker.sock`) | no device with `docker.sock`, round 4 |
| the host's Postgres | `incus exec tide-proto -T -- timeout 10 nc -z -w 3 <host ip> 5432` for 10.20.1.159, 100.87.229.94, 10.173.184.1, 10.60.187.1 | all not visible (code 1), round 4 — the host has Postgres on `0.0.0.0:5432` |

## How the trials work

**The container.** `tide-proto` from `images:ubuntu/24.04` in the restricted project from PT-166 (the user's `incus` from the group
`incus` goes through `incus-user` to the project `user-<uid>`), `security.nesting=true`,
`security.syscalls.intercept.mknod=true`, `security.syscalls.intercept.setxattr=true`. When the project rejects
intercept, the script creates the container with nesting only and records this in the log (it evaluates P-5). The `agent` user has uid 1000
(as on the host). The container stays on the host only with result `done`; otherwise `bash scripts/box/prototype/cleanup.sh`.

**Network for the trials (P-1, P-4, packages in P-5) — since round 2 by default through a temporary proxy.** The `inet tide-box` table
from PT-166 is a static default-deny that lets through from the bridge only the Tide proxy port. The PT-178 proxy does not yet
work, and the forward from the temporary rules in round 1 was lost in the host's Docker chain (conclusions above), so `lib.sh`
(`net_open`/`net_close`) runs for the duration of the trial `scripts/box/prototype/proxy-plain.py` (CONNECT and plain HTTP to any target, no MITM,
a target journal) on `<bridge address>:<Tide proxy port>` and gives the container `HTTP(S)_PROXY` (`incus exec`
commands), `Acquire::http(s)::Proxy` (apt) and a `dockerd` drop-in — exactly as in the target architecture
(section 4.5). No sudo and no change in nftables; the list of targets goes to the log (material for P-6). It is a different file
than `proxy.py` from PT-178 (domain list, injecting routes): the PT-164 trial proxy lets any target through, because P-1 and P-4
check Docker and the mounts, not the domain list.

The `PROTO_NET=rules` mode (round 1) inserts with sudo exactly three temporary rules with the comment `tide-proto`
**into the `inet tide-box` table** (`nft insert`, i.e. before the drop rules):

```
nft insert rule inet tide-box forward iifname "incusbr0" ip saddr <IP kontenera> accept comment "tide-proto"
nft insert rule inet tide-box forward oifname "incusbr0" ip daddr <IP kontenera> ct state established,related accept comment "tide-proto"
nft insert rule inet tide-box input iifname "incusbr0" ip saddr <IP kontenera> meta l4proto { tcp, udp } th dport 53 accept comment "tide-proto"
```

and removes them by handles (`nft -a list table inet tide-box` → rows with `comment "tide-proto"` →
`nft delete rule inet tide-box <chain> handle <n>`) in an `EXIT` trap — also after an error and after a signal. NAT is done by
the `incus` table of the bridge. The log contains `nft list table inet tide-box` before and after and the result `nft-przed-po` (identical,
no `tide-proto`).

**P-4.** The trial worktree `<proj>-worktrees/proto-k-38` on a new branch `proto/k-38` (by default the project
`~/DevWorkspace/toolssuite`, variable `PROTO_NODE_PROJECT`) is created on the host and removed with the branch at the end.
Mounts (`incus config device add tide-proto <name> disk source=<path> path=<the same path> shift=true`):
`.git` and `<proj>-worktrees` rw, `.git/hooks`, `.git/config`, `.git/info` ro. A write to the ro overlays is attempted
without changing content (`mkdir` of a trial directory, `: >> config`). `npm ci --ignore-scripts` on the host, then in the container
with a cold and a warm cache, with the same node version as the host.

**P-5.** On the host a small TCP server listens on the bridge address and the Tide proxy port (the only port let through by
`inet tide-box`) and records the source address of connections. Control A: `nc` from the container's own address; control B (without
filtering): `ip addr add <x.y.z.250>/24 dev eth0` and `nc -s <x.y.z.250> -z <bridge address> <port>` — it must reach, otherwise the
test is invalid; then `incus config device override tide-proto eth0 security.mac_filtering=true
security.ipv4_filtering=true security.ipv6_filtering=true`, a restart and the same spoofing — it must not reach.
When the start with `security.ipv6_filtering` fails (no `br_netfilter`), the trial goes with `security.ipv6_filtering=false`
(mac + ipv4) and records it in the log. When the project rejects intercept, the script temporarily checks
`restricted.containers.interception=allow` (sudo, reverted). For the same reason the measurements temporarily set
`restricted.snapshots=allow` (sudo, reverted after the snapshot is removed).

**P-9.** The `incus exec` client runs on the host in the unit `tide-proto-client` (like a child of `tide-daemon`);
`systemctl --user stop tide-proto-client` emulates the death of the service; after each one `incus exec tide-proto -T -- pgrep -u 1000
-x sleep`. Three variants in the container: `systemd-run --user --scope sleep 30`, `systemd-run --user --unit=… --collect
--wait sleep 30` (the agent's user service) and `systemd-run --uid=1000 … --wait sleep 30` (the box's root service).
Then `systemctl --user restart tide-daemon` with the scope variant (the ticket's criterion).

## Run order

Everything at once: `bash scripts/box/prototype/all.sh` — the PT-164 trials (p5, p1, p4, p9, measure), then PT-178 (p7, p6,
p3, p2); each writes its own log, a failure of one does not stop the next ones; `TRIALS="p1 p9"` selects the trials.
Cleanup: `bash scripts/box/prototype/cleanup.sh` (prototype proxy, containers `tide-proto`, `tide-proto-net`,
`tide-proto-p7`, worktree `proto-k-38`; `--keep-box` keeps the containers, `--rules` with sudo removes the remaining
`tide-proto` rules). Individually:

1. `bash scripts/box/prototype/p5.sh` — project, nesting, intercept, NIC filtering (creates the container).
2. `bash scripts/box/prototype/p1.sh` — Docker and LegalApp.
3. `bash scripts/box/prototype/p4.sh` — mounts, git, npm, host visibility.
4. `bash scripts/box/prototype/p9.sh` — systemd --user and surviving a restart.
5. `bash scripts/box/prototype/measure.sh` — measurements.

## PT-178 trials (P-2, P-3, P-6, P-7)

Trials from `docs/card-box-design.md`, section 10. Each trial is a script `scripts/box/prototype/p<N>.sh` run
by the user outside the agent's sandbox; the full journal in `docs/box-prototype/p<N>-<time>.log` (rows
`WYNIK …`), proxy journals (denials, access — without headers and tokens) in the directory `p<N>-<time>/` next to it.
P-1, P-4, P-5, P-9 are added by PT-164 (#1); P-2, P-3, P-6, P-7 — PT-178 (#15). Decisions from the trials:
`docs/box-decisions.md`.

PT-178 trial box: `tide-proto-net` (`images:ubuntu/24.04`, `security.nesting=true`) in the user's project,
network only through the nftables from PT-166 (from the bridge only to `<bridge address>:18080` and DHCP is allowed). The prototype proxy
`scripts/box/prototype/proxy.py` listens on the bridge address, on port 18080: CONNECT to domains from the list, plain HTTP to
domains from the list (apt fallback variant) and injecting routes `/anthropic`, `/chatgpt` (K3). Cleanup:
`scripts/box/prototype/cleanup.sh`.

Runs (2026-11-04): 1 — 12:33 (P-6 failed on the apt sources); 2 — 13:07 (full P-6 without Playwright and NO_PROXY,
P-3 Claude, P-2 variant A); 3 — 13:51 (`P6_ONLY=playwright,noproxy`: Playwright, NO_PROXY, P-3 Codex, P-2 variants A
and B); 4 — 14:27 (`TRIALS="p3 p2"`: Codex with its own model provider and the host's account identifier in the dummy, P-2
with Codex through the model).

### Table of PT-178 trials

| Trial | Question | Script | Result | Journal |
| --- | --- | --- | --- | --- |
| P-2 | bwrap (Claude Code) and landlock (Codex) in a container with `apparmor_restrict_unprivileged_userns=1` | `p2.sh` | **bwrap (Claude Code): works after loading the AppArmor profile in the box (variant B); from the bare image (A) it does not.** A: `bwrap --unshare-all` → `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`, kernel: `apparmor="DENIED" operation="capable" namespace="root//incus-user-1000_tide-proto-net_<var-lib-incus>" profile="unprivileged_userns"` (setpcap, net_admin) — the host's restriction applies in the box's AppArmor namespace; `claude -p` with `failIfUnavailable` refuses to start ("Ubuntu's AppArmor restriction on user namespaces stops bubblewrap here"). B: the profile `profile bwrap /usr/bin/bwrap flags=(unconfined) { userns, }` (the same as on the host, `scripts/sandbox/check.py profile`) loaded by `apparmor_parser -r` **inside** the box (box root, host unchanged) → bare bwrap `bwrap-ok`; `claude -p` with `sandbox.enabled=true` code 0: reading `~/.claude/p2-marker` → `No such file or directory` (denyRead masks the directory; the `cat` error recorded in `claude-read.txt`, so the command really ran), writing in the working directory yes, outside it `Read-only file system` (code and message from the model's answer in `--output-format text`; after the review `p2.sh` logs `stream-json` with the Bash tool result). `unshare --user` without the profile is still denied — the restriction stays for the other processes. Decision `bwrap-profil-w-pudelku`. **Codex: the sandbox starts after loading the profile (B), from the bare image (A) it does not; the write restriction is unverified.** Codex 0.166 on Linux uses bubblewrap, not landlock alone ("Codex's Linux sandbox uses bubblewrap and needs access to create user namespaces"), so it shares Claude Code's fate. A: `codex exec --sandbox workspace-write` — the commands did not run, "the sandbox failed to start with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`", no file was created. B (run 4): `codex exec --sandbox workspace-write` (model through the proxy, `provider` variant) code 0 — in Codex's record there is **one** `exec` block (`echo ok > ./codex-model-inside`, "succeeded"), the file exists. Codex did not execute the second command (`echo bad > ~/p2-outside-codex-model`) — "code 1, read-only file system" is only the content of the model's answer, so the missing file does not prove a sandbox denial. The trial without the model (`codex sandbox …`) gave no result because of the CLI 0.166 syntax (`linux` as the command, then no `--full-auto`). Proven: with the profile the Codex sandbox starts and executes commands in the working directory; not proven: that it blocks writing outside it — the `sandbox.enabled=false` variant/K1 risk for Codex stays open until #16 (decision `bwrap-profil-w-pudelku`). `p2.sh` after the review: `codex sandbox -- sh -c …` without the model with a verdict distinguishing a syntax error from the sandbox, and in the path with the model one command per call and the number of `exec` blocks (no `exec` → "unverified"); repeat: `TRIALS=p2 bash scripts/box/prototype/all.sh` | `p2-2026-11-04T142904.log`, `p2-2026-11-04T135550.log`, `p2-2026-11-04T132223.log` |
| P-3 | Claude Code (OAuth) through `ANTHROPIC_BASE_URL` and Codex through `chatgpt_base_url` on the injecting proxy (K3) | `p3.sh` | **Claude Code: K3 possible** — `ANTHROPIC_BASE_URL=http://<bridge>:18080/anthropic CLAUDE_CODE_OAUTH_TOKEN=<dummy> claude -p 'hi'` 9/9 code 0 in three runs, proxy `INJECT POST /anthropic/v1/messages` → **200**, only a dummy in the box. OAuth refresh: 0 requests through the proxy (neither base URL nor CONNECT) — the box does not refresh, the host refreshes the token, the proxy reads the file on every request. Besides the base URL, Claude Code tries `CONNECT api.anthropic.com:443` (denied, no effect). **Codex: K3 possible — only with its own model provider** (run 4): `codex exec --skip-git-repo-check -c 'chatgpt_base_url="http://<bridge>:18080/chatgpt/backend-api/"' -c 'model_provider="tide"' -c 'model_providers.tide={ name = "tide", base_url = "http://<bridge>:18080/chatgpt/backend-api/codex", wire_api = "responses", requires_openai_auth = true }' 'Reply with the single word: hi'` code 0, proxy `INJECT POST /chatgpt/backend-api/codex/responses` → **200** (plus `codex/models`, `wham/accounts/check`, `ps/plugins/*`, `ps/mcp`, `analytics-events` → 200/204). In the box `~/.codex/auth.json` with dummy tokens (unsigned JWT); from the host file only the account identifier and the plan type pass through — with an invented account Codex ends with "selected workspace missing from routing discovery". `chatgpt_base_url` alone or `+OPENAI_BASE_URL` **is not enough** (runs 3 and 4): the model call goes through CONNECT straight to `chatgpt.com:443` (denied), code 1. Codex OAuth refresh: 0 requests (`auth.openai.com` does not appear) | `p3-2026-11-04T142744.log`, `p3-2026-11-04T135419.log`, `p3-2026-11-04T132151.log` |
| P-6 | clients through CONNECT without DNS, `NO_PROXY` for localhost | `p6.sh` | **works**: apt (sources rewritten to `https://`, decision `apt-przez-https`), npm, NuGet, pip, `docker pull`, Playwright (`playwright@1.49.1`), the Claude Code installer and `npm i -g @openai/codex` go through CONNECT without DNS with the list from the denial journal. **NO_PROXY is honored by all examined clients** (curl, Node `fetch`, dotnet `HttpClient`, Playwright/Chromium) for `localhost` and `127.0.0.1`: the server in the box got the request, the proxy 0 entries. Exceptions: **Node `fetch`** (18.19) does not use the proxy at all — outbound traffic fails (`ConnectTimeoutError`, the direct connection is blocked by nftables); **Playwright ≥ 1.50 requires Node ≥ 20** (Ubuntu 24.04 has 18.19) — the base image needs a newer Node (#16/#29) | `p6-2026-11-04T135145.log`, `p6-2026-11-04T130727.log`, run 1: `p6-2026-11-04T123348.log` |
| P-7 | Incus 6.0 from Ubuntu universe: `images:` and `images:ubuntu/24.04` | `p7.sh` | **works**: `incus 6.0.0-1ubuntu0.3` from noble-security/universe, `incus image list images: ubuntu/24.04` code 0, `incus launch images:ubuntu/24.04` code 0 in 1–2 s (Ubuntu 24.04.5 LTS) in project `user-1000`, three runs. Zabbly unnecessary (decision `incus-z-ubuntu`) | `p7-2026-11-04T135140.log`, `p7-2026-11-04T130723.log`, `p7-2026-11-04T123343.log` |

#### Measurement of `claude -p 'hi'` (P-3, three repetitions per run)

| Where | Run 2 [s] | Run 3 [s] | Run 4 [s] | Median of 9 [s] |
| --- | --- | --- | --- | --- |
| box, through the injecting proxy | 4.26 · 5.73 · 3.52 (median 4.26) | 5.81 · 5.45 · 12.18 (median 5.81) | 3.09 · 2.22 · 2.14 (median 2.22) | **4.26** |
| host | 6.93 · 4.42 · 5.65 (median 5.65) | 4.32 · 4.08 · 5.09 (median 4.32) | 5.33 · 5.55 · 4.28 (median 5.33) | **5.09** |

No proxy overhead is visible: in two runs out of three the box was faster, the median of nine trials is 4.26 s
in the box vs 5.09 s on the host. The single 12.18 s is an outlier (API latency — one POST goes through the proxy).
The host runs the user's full configuration (CLAUDE.md, plugins), the box a bare `claude`.

### Domains from the denial journal

A source for the stack templates (#16/PT-192, #29, #30/PT-193). Round 1 of each client runs with an empty list; subsequent ones
add domains from the denials until the client passes (`docs/box-prototype/p6-*/domains-*.txt`). The P-6 table from the card's
criterion: the columns "Through CONNECT" and "NO_PROXY for localhost" are given for each client in the table below.

| Client | Through CONNECT | NO_PROXY for localhost | Domains from the denial journal |
| --- | --- | --- | --- |
| apt | yes | not applicable | `archive.ubuntu.com`, `security.ubuntu.com` |
| npm | yes | not applicable | `registry.npmjs.org` |
| NuGet / dotnet (`HttpClient`) | yes | yes | `api.nuget.org`, `crl3.digicert.com`, `ocsp.digicert.com` |
| pip | yes | not applicable | `pypi.org`, `files.pythonhosted.org` |
| docker pull | yes | not applicable | `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com` |
| Playwright | yes | yes | `registry.npmjs.org`, mirrors `playwright*.azureedge.net`, `playwright.download.prss.microsoft.com` |
| Node `fetch` (18.19) | **no** (ignores the proxy) | yes | — (see "NO_PROXY per client") |
| curl / Claude Code installer | yes | yes | `claude.ai`, `downloads.claude.ai` |
| Codex (`npm i -g`) | yes | not applicable | `registry.npmjs.org` |

Details of rounds and commands:

| Client | Command | Empty list | Domains needed | Rounds |
| --- | --- | --- | --- | --- |
| apt | `apt-get update` (`https://` sources) | code 1 | `archive.ubuntu.com`, `security.ubuntu.com`; additionally `motd.ubuntu.com` (motd-news, not needed for updates) | 3 |
| apt (install) | `apt-get install … dotnet-sdk-8.0 docker.io bubblewrap …` | code 100 | `archive.ubuntu.com`, `security.ubuntu.com` | 2 |
| npm | `npm install --package-lock-only && npm ci` | code 1 (E403) | `registry.npmjs.org` | 2 |
| NuGet / dotnet | `dotnet restore` | code 1 | `api.nuget.org`, `crl3.digicert.com`, `ocsp.digicert.com` (certificate revocation checking of package signatures) | 3 |
| pip | `pip download requests==2.32.3` | code 1 | `pypi.org`, `files.pythonhosted.org` | 3 |
| docker pull | `docker pull hello-world` (dockerd with a proxy drop-in) | code 1 | `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com` | 4 |
| Playwright | `npm i playwright@1.49.1 && npx playwright install chromium` | code 1 | `registry.npmjs.org`, `playwright.azureedge.net`, `playwright-akamai.azureedge.net`, `playwright-verizon.azureedge.net`, `playwright.download.prss.microsoft.com` (browser download mirrors, tried in turn) | 4 |
| Claude Code installer | `curl -fsSL https://claude.ai/install.sh \| bash -s 2.1.317` | code 22 | `claude.ai`, `downloads.claude.ai`; `api.anthropic.com` after installation (`claude --version`), not needed for installation | 4 |
| Codex | `npm i -g @openai/codex` (0.166.0) | code 1 | `registry.npmjs.org` | 2 |
| Claude Code (work, P-3) | `claude -p` through the base URL | — | none through CONNECT; denials of `api.anthropic.com` do not affect the result | — |
| Codex (work, P-3) | `codex exec` (`provider` variant) | — | none through CONNECT — the model and account services through the base URL; denials of `ab.chatgpt.com` (telemetry), `sdmntprcentralus.oaiusercontent.com`, `sdmntprsouthcentralus.oaiusercontent.com` (plugin files/images) do not affect the result. Without the `provider` variant the model goes to `chatgpt.com` — with K3 this domain is **not** to be on the list (it would go with the dummy) | — |

### NO_PROXY per client

`NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal,<bridge address>`; an HTTP server in the box
(`python3 -m http.server 8765 --bind ::`) on the loopback. "Yes" = the server got the request, and the proxy journal has no
entry (run 3, `docs/box-prototype/p6-2026-11-04T135145.log`).

| Client | Through CONNECT | `localhost` without the proxy | `127.0.0.1` without the proxy | Workaround |
| --- | --- | --- | --- | --- |
| curl | yes (Claude Code installer) | yes | yes | — |
| Node `fetch` (18.19) | **no** — ignores `HTTP(S)_PROXY` | yes (does not use the proxy at all) | yes | outbound traffic: Node ≥ 24 with `NODE_USE_ENV_PROXY=1` or `undici` `EnvHttpProxyAgent` in code; Node 18 with `NODE_USE_ENV_PROXY=1` — no change (`ConnectTimeoutError`) |
| dotnet `HttpClient` | yes (NuGet) | yes | yes | — |
| Playwright (Chromium 1.49.1) | yes (browser download) | yes | yes | — |
| apt, npm, pip, dockerd, Codex installer | yes | not applicable (no requests to localhost) | | — |
## First step in the box


PT-191 delivers the tools for the proof, but a result from dummies does not replace a trial on Incus.
`python3 daemon/tide.py box run plasma-tide -- env` runs a synthetic step through `BoxRunner.start`,
with the profile's domains, the `PortPool` ports of PT-171 and the step's mounts. It does not pass agent tokens to `in/env`.
From an existing worktree it works in it; from the main repository it creates a separate branch `box-run/demo-…` and a worktree,
which remain for inspection. The log, `in/env` (0600) and the exit code are in the step directory given on stderr.

Red list trial: `make box-check BOX=plasma-tide`. The script runs as `agent` and prints a table
"punkt → odmowa/przeszło/nie dotyczy" (item → denied/passed/not applicable; the script's output is in Polish). Code 1 means at least one "przeszło" (passed); "nie dotyczy" (not applicable) does not change the code,
but requires separate evidence. The spoofing trial requires a checked free IP in the bridge subnet:
`make box-check BOX=plasma-tide CHECK_ARGS="--spoof-ip <wolny-IP>"` (optionally `--prefix`, `--interface`,
`--host-ip`, `--spoof-image <local-image-with-ip-and-curl>`). The script assigns the IP through `sudo -n` or Docker
`--privileged --net=host`, sends `curl --interface` and removes the address in a trap. Inability = "nie dotyczy"
with a reason. A proxy response, even 403, means "przeszło"; we expect no connection **and no entry
from the trial IP in the proxy journal on the host**.

`/proc/1/environ` in the box is the environment of its init. The invisibility of the host's init is shown by comparing the PID
namespace with the value supplied by the CLI, not by the success/failure of `cat` alone.
The write trial outside the allowed directories uses the existing `/etc` in the box; it does not prove protection of the whole
filesystem, e.g. the box's temporary `/tmp`. A missing `origin` means the push trial is skipped, not denied.

`cat ~/.claude/.credentials.json` must additionally be run with the **real agent's Bash tool**:
P-2 and no Docker without K3 → expected sandbox denial; Docker without K3 → "residual K1 risk".
A plain shell started by the CLI does not confirm that the agent's internal sandbox works.

The final PT-191 proof includes a plasma-tide test card with a plan approved by the user: one ticket
runs `make test` in the box and records `done` without `envAnswer`/`outside`. The note must include:
the card and ticket ID, the runner entry from `procs`/the journal, the step time, the agent's Bash result, the red list table,
the CLI environment and a check of process argv on the host. Until these results are collected the manual trial remains
not passed. The script `scripts/box/boundary-trial.py` collects the CLI/Incus part and the full tests outside the sandbox;
it does not create or approve a test card on the user's behalf.

First PT-191 round (6.11.2026, `pt191-20261106-055810`): `make test` ran 4149 tests in 227 modules
(4 skipped, 332.94 s) with one `JSONDecodeError` error in `test_continue_without_commands_option`.
A deterministic regression confirmed reading an incomplete `fake-call.json` from the dummy; the fix publishes it
atomically after the write finishes. The CLI and red list tests in this round were green, but the preflight showed
`exists=false` for the box `plasma-tide`, so **the trials on a real box were not carried out**.
Logs: `docs/box-prototype/pt191-20261106-055810/`. The full tests after the fix need to be repeated.

When the environment is missing: `python3 scripts/box/boundary-trial.py --prepare-missing` keeps an existing box
and profile; it builds only the missing box, and when the profile is missing it approves the explicit trial profile
`docs/agents/pt-191/plasma-tide-proof.box.yaml` (python-qt, ports, trial tools and the npm domain).
Packages go into the box, without installing anything on the host. The profile and the box remain for the proofs.
This environment preparation does not approve the plan of the real test card.

Second PT-191 round (`pt191-20261106-061234`): the trial profile was approved. The build reached the clean snapshot,
but Incus refused: `Project "user-1000" doesn't allow for snapshot creation`. The box stayed in state
"błąd budowy" (build error), with correct NIC filtering and a matching hash; **the environment/argv and red list trials were not carried out**.
`make test`: 4156 tests, 229 modules, 4 skipped, 318.59 s; the earlier JSON publishing error is gone,
one error remained, of manually sending the card back for rework during close-out. The `fold` fix keeps the manual todo
after a late result of an older close-out; the regression test reproduces the order from the journal, without changing the original
integration test. Local checks after the fix: 100 tests OK; the full tests remain to be repeated.

Next command: `python3 scripts/box/boundary-trial.py --enable-snapshots --retry-failed-build`.
The first flag explicitly and permanently sets `restricted.snapshots=allow` in the configured Incus project
through sudo, as in the PT-182 installer, and checks the result. It does not change other permissions or host packages.
The second retries through the builder only a failed build with a hash matching the profile and without steps; the profile is not
replaced. The other existing boxes are kept. Then the full tests and the boundary trials are run.

The third round (`pt191-20261106-062856`) was interrupted by a timeout on `sudo -A`.
Reading `restricted.snapshots` returned an empty value; there is no result of the change and no read after it, so the state
after the interruption remains unconfirmed. There was no build, full tests or boundary trials.
The script now uses only `sudo -n`, without a password window, and re-reads the policy before the change.
When there is no authentication it gives the specific administrator command to run in a terminal
(`sudo incus project set user-1000 restricted.snapshots=allow` for this trial's project), skips the build
and continues with the full tests. Their result can be collected even when the Incus environment is still blocked.
Do not retry an identical operation waiting for a password in a background step.

Fourth round (`pt191-20261106-102554`): the snapshot policy was already `allow`, so sudo was not needed.
The build retry finished correctly in 67.3 s; the status confirms `clean`, matching hashes and NIC filtering.
Full `make test`: 4166 tests, 230 modules, 4 skipped, 516.42 s, two errors in the `/Tasks` module.
The earlier JSON publishing and manual send-back regressions passed; in this round a test read
the program's journal earlier than the program wrote it. The scenarios now wait for the actual calls
instead of equating taking over the task with the program's execution.

The environment CLI and `make box-check` ended with code 2 before writing `step.log`, so in this round
**there is no environment evidence or denial table**. The sources revealed inconsistent wrapper contracts in the image
and the runner. The runner now delivers its own wrapper to `in/bin/tide-step` of each step through the protected
`StepDir`; the default argv uses `/bin/sh <step>/in/bin/tide-step`. This does not change the image, the profile,
the runner choice in the engine or the stack templates. The CLI tests no longer replace this default path.
Confirming the fix on a real Incus requires another round:
`python3 scripts/box/boundary-trial.py` — without the preparation and policy-change options, because the box is ready.
An additional local run check also revealed a premature reservation read in the stop-after-restart test:
`step.finished` precedes the box release. The test now waits for the release to finish
with a bounded time; this does not change the production stop code.

The fifth round (`pt191-20261106-111049`) confirmed the CLI environment (6.344 s): the proxy, the full
`NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal,10.173.184.1`, port 49100,
no agent tokens, `in/env` mode 0600 and no proxy key and port in the host argv.
In the saved `env.stdout.log` the temporary proxy key was replaced with `<revoked>`; its absence from argv was
checked before the redaction. The step wrapper from the runner worked on a real box.

Red list: 0 "przeszło" (passed), 3 "nie dotyczy" (not applicable) (no Docker client, no checked free IP,
no trial of a real agent Bash). The full table is in `box-check.stdout.log`. The whole box-check
remains **not passed**: the ref guard rolled back parallel plan writes of other cards. Cause,
reflogs and OIDs: [guard incident](agents/pt-191/refguard-incident.md). The installed plan write has no
PT-170 shield, which is present in this branch. The script now blocks CLI trials on the shared repository
given this mismatch. A file check does not confirm the version of code already loaded in the process.

Full tests: 4168 tests, 230 modules, 4 skipped, 456.58 s; one error in the local empty-queue test.
The review column appears before the end of the close-out step; the test now waits for `card.closed` of both cards
before stopping the monitor and checking the queue. A full `make test` after the fix needs a separate outside run
that does not start the CLI or the ref guard on the user's repository.

Independent `make test` after the fifth round: 4172 tests, 230 modules, 4 skipped, 495.73 s.
Log: [full tests after the fifth round](box-prototype/pt191-make-test-resume-9.log). The queue test after waiting
for the close-outs passed. Two test errors remained: counting the close step as another execution
after a ticket rejection, and the statusline median of 152.29 ms with 14 parallel modules (threshold 150 ms).
The first test now skips close in the comparison of step counts, keeping the assertions of rejection and no merge.
The statusline module uses the existing serial mode after all the parallel modules;
the 150 ms threshold and the number of measurements stay unchanged. Locally the 14 statusline and runner scheduler tests
pass. The next outside run concerns only `make test`; trials on the shared `.git` remain blocked
after the guard incident, and the first real agent step still requires an approved test card.

Second independent `make test`: 4172 tests, 230 modules, 4 skipped, 494.59 s; review and statusline
passed after the fixes. [The full log](box-prototype/pt191-make-test-resume-10.log) contains one error of the
test of reversing the lock-screen background animation: a fixed timer read an already finished cross-fade.
A controlled sampling delay of 400 ms reproduced exactly the error `1 not less than 1`.
The harness now reverses the animation on the opacity change signal, at a value between 0.25 and 0.75,
keeping the continuity assertion. The production animation code stays unchanged. 18 tests of the module
pass locally; the full tests after the fix need an outside run. The state of the Incus evidence did not change.

The third independent `make test` after the fifth round ended with **code 0**: 4172 tests,
230 modules, 4 skipped, 382.65 s. [The full green run](box-prototype/pt191-make-test-resume-11.log)
confirms the earlier fixes, including the animation reversal test. The automatic PT-191 criteria are passed.
The Incus trials were not repeated: re-reading the installed `commit_plan` still shows no
`host_operation` shield. The final box-check and a real agent step remain. The prepared
[test card plan proposal](agents/pt-191/first-step-proposal.md) was passed on for approval;
the approval itself is not proof of execution and does not remove the block after the guard incident.

User decision of 6.11.2026 (PT-191 resume no. 13): the plan `first-step-proposal.md` is
**approved**, and the real trial on the shared repository is **deferred** until the
installed Tide writes plans through `host_operation`, after K-38 is merged. The implementation goes to
review; the final manual criteria remain not passed. No further trial was run.

Coordinator's finding for the K-38 review: the guard is to restore only the step's refs. **It must not
restore refs of other cards or branches outside the scope of the step/box.** The K-55/K-60 incident from 11:18
is evidence of a risk to be solved in K-38. Host version compatibility does not replace limiting the scope
of restoring. The requirement, reflogs and regression scenarios are described in the
[incident note](agents/pt-191/refguard-incident.md); the guard was not changed in this resume.
