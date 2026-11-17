# Tests while working on a ticket

## General rule and the project test contract

The K-62 rule applies in every technology: in a ticket and after fixes, the author runs the tests selected for the changes
since the ticket's pinned start point, the reviewer checks that run, and the project's full tests are run once
at card close-out. The commands are not hard-coded in Tide, but come from the **project test contract**:
`tests.changed` (tests selected for the changes, with the `{base}` parameter — the pinned SHA of the ticket start) and `tests.full`
(full tests at close-out) in the Board project profile (`projects/<slug>/box.yaml`, description:
[tide-daemon.md](tide-daemon.md#project-test-contract-k-69-pt-278)). `make test-changed BASE=…` and `make test`
below are **plasma-tide** commands, that is, an example of a contract, not a requirement for other projects (for example `dotnet test …`
or `npm test` in their contract). `CLAUDE.md` and `AGENTS.md` of plasma-tide stay with the `make` commands.

Nine rules for selection and consents (the K-69 numbering, to which the [plan standard](agent-plan-standard.md#the-card-flow-run-by-tide-12) refers, sections "Testy ticketu i review" (Ticket tests and review) and "Domknięcie" (Close-out)):

1. **Order of sources.** Test commands come from the approved contract, and when there is none, from detection
   in the `Makefile` (with `mk/*.mk`), and when there is no detection, from the project rules (`CLAUDE.md`/`AGENTS.md`).
2. **A field missing from the contract.** A missing field of an approved contract is filled in by the general rule (the project rules),
   not by detection from the `Makefile`.
3. **Detection.** Detection checks the targets independently: the `test-changed` target gives `changed`, the `test` target gives `full`,
   and when there is no `test` target, the full tests are run according to the project rules.
4. **Approval.** A contract is approved only by the project window or by `UpsertProject` called from outside
   an agent step; merely writing the `box.yaml` file (by a step, by hand, by reverting a commit) is not approval, and an unapproved
   contract — also when the fingerprint store collides with the step's write area — is not applied and gives no
   automatic consents
   ([approval](tide-daemon.md#approving-the-test-contract-pt-285),
   [protection](tide-daemon.md#protecting-approval-from-an-agent-step-pt-286)).
5. **Consent to a command.** Only a literal single command of the approved contract without a `*` character, with the pinned base SHA
   substituted, gets automatic consent; `changed` without a known base gets no consent
   ([consent](tide-daemon.md#consent-for-the-contract-commands-pt-280)).
6. **Precedence of prohibitions.** The project's prohibitions (`denied-commands`) and Tide's (for example `make install`) take precedence
   over the contract command: a prohibited command gets no consent.
7. **The `legacy` condition.** Without an applied (approved) contract, `legacy` mode applies when the branch has a `test` target
   without `test-changed`. With an approved contract it applies only when the approved `changed` is a plain
   `make test-changed …` (without `make` options, for example `-C`) and the branch has a `test` target without `test-changed`; other approved
   `changed` values (for example `dotnet test …`, `make -C … test-changed`, or empty) do not give `legacy`, and the contract
   command or the general rule applies. In `legacy` the full `make test` is the test of the ticket and fixes, and the reviewer checks its
   result in the note (see [older branches](#older-branches-without-the-test-changed-target)).
8. **Close-out.** The `close` step runs the full tests once (`tests.full` of the approved contract, the detected `make test`,
   or the full tests according to the project rules), and their failure ends the step with the result `error`, not `done`.
9. **Rerun.** Rerunning failed modules limits only what is run: it does not turn an earlier
   failure into a success, and settling it requires [test exceptions](#test-exceptions)
   (see [rerun](#rerunning-failed-modules)).

These rules do not weaken the test exception rules or the requirement of a full run at close-out.

## plasma-tide commands (an example contract)

In plasma-tide the contract corresponds to `make` targets: `make test-changed BASE=<SHA>` (`changed`) and `make test` (`full`).

During implementation, run the suites selected for the change. Before handing in a ticket and after
fixes, run `make test-changed BASE=<punkt startu ticketu na gałęzi karty>`.
Use the pinned SHA from the step sheet, not the moving tip of the card branch or the
default `master`. Record the command and the result in the note. The reviewer checks that this run
passed for the submitted changes; they do not demand a separate full `make test`.

Run the full `make test` once at card close-out (the `close` step), on the card branch after merging the
tickets, before the summary. A failed run means a close-out problem: the result `error`
with a description of the failures instead of `done`. An exception for close-out requires explicit settlement and a user decision
according to the rules below; merging into the main branch still requires a separate command.
For a lack of consent or the need to run
outside the sandbox, use the ordinary `denied`/`outside` contract; after resuming, use the result
of the run. Repeat a successful full run only after new changes.

A background step (ticket, fixes, close-out) runs only targeted tests in the step sandbox
(`make test-cardflow`, `make test-daemon`, `make test-board`, or `tools/test_runner.py --modules`).
It reports the required `make test-changed BASE=…` (and at close-out the full `make test`) right away as an
`outside` command, without an earlier attempt in the sandbox. On 08.11.2026 steps ran the full run
first in the sandbox (modules failed without D-Bus, writing to /tmp, and nested bwrap), and then a second time
outside it — a machine load of 40–58 on 16 cores. The text in the sheets: `sandbox_tests`
in `daemon/tide_daemon/cardflow/handoff.py`.

This rule replaces the requirement of the full `make test` before handing in a ticket written in the sheets
of existing plans, including K-37, K-38, and K-55. We do not rewrite those plans.

```sh
make test-cardflow           # test_cardflow*.py and the basesuggestions and hostingcli tests
make test-daemon             # all service tests
make test-board              # the Board window (apps/board/tests)
make test-issues             # the issue store and the CLI process on a private D-Bus
make test-changed BASE=<SHA-startu-ticketu>
make test                   # once at card close-out, on the card branch
make test JOBS=4             # limit the number of simultaneous processes
make test JOBS=1             # diagnostics without parallelism
```

The names of all suites are printed by `make help`. Their only registry is
[`tools/test_suites.py`](../tools/test_suites.py): `SUITES` defines the `test-<nazwa>` targets,
`CHANGE_MAP` ties paths to suites, `IMPORT_ROOTS` defines the code analyzed by AST,
`SERVICE_CONTRACT_MODULES` lists the small service process contract, and `SERIAL_MODULES`
marks exceptions to parallelism.
Every `TEST_DIRS` directory must have a suite; the registry test enforces this. `make test` still takes
all `TEST_DIRS` directories from the `mk/*.mk` files, including newly added components.

`make test-changed` compares the working tree with the merge-base of `BASE` and `HEAD` (by default `master`).
It includes the branch commits, the index, uncommitted changes, and untracked files other than ignored ones.
Deletions and moves of code or data also include the old path and select a full run.

For `daemon/tide_daemon/**/*.py` the selector reads the AST of the service code and of all test directories,
without importing or executing those files. It follows `import`, `from ... import ...` (including
relative ones, aliases, and imports inside functions) and constant names in `importlib.import_module` and
`__import__`, also through a module or function alias (`import importlib as lib`,
`from importlib import import_module as load`, equivalents from `builtins`). It recognizes
both `tide_daemon` and `daemon.tide_daemon`. The graph takes into account the existing
`__init__.py` of all parent packages of the imported module, also for a constant
name of a dynamic import. It selects a test
that imports the changed module directly or through intermediate code and test helpers,
also outside `daemon/tests`. A changed standalone test module selects itself.
No recognized imports of the changed source means the old directory suite.

The graph keeps all known indirect consumers, also through large aggregators
and fixtures (for example `engine`, `FlowInProcess`). It does not cut chains by depth or by
the number of dependencies/consumers: the previous thresholds could skip known dependent tests.
The set of visited modules ends cycles and limits the cost of the traversal to the size of the graph.
A local change without many consumers still selects a small suite; a shared module may select a wide one.

**A deliberate cardflow limitation (coordinator's decision, 06.11.2026, PT-258/K-68):**
for changes to `daemon/tide_daemon/cardflow/*` the selection stays wide. An audit of 31 modules
showed that each leads to the shared `engine.py` (or is the engine itself).
Example selections: `basesuggestions.py` — 79 modules, `hostingcli.py` — 80,
`engine.py` — 78, `state.py` — 130, against 75 modules of the `cardflow` suite.
We keep the module graph and all known consumers. A reduction for local cardflow
is explicitly deferred because of the work-in-progress limit; it is not a result of this ticket.
The narrowing works for the other components and explicitly assigned utility tools.

When a recognized dynamic importer call cannot be resolved (for example a name
in a variable, a relative import, or `__import__` with `fromlist`), its module is treated
as a potential consumer of every changed service source. That test module and all tests that indirectly import such a module are selected.
The widening also happens
when the source has an ordinary static test; the reason contains the path and line of the uncertain import.
We do not execute code to determine the values of variables.

Every change of code or data in `daemon/` additionally selects `test_dbus_contract.py` (process start and /Agents)
and `test_service_smoke.py` (ticket → review → close-out through /Board). Thanks to this,
running the service in a separate process has coverage despite the lack of an import edge.
This is static analysis: arbitrary passing of importer functions as values or reflection
may go beyond the recognized calls; data dependencies and all D-Bus scenarios
do not follow from the import graph. The full test at close-out remains required.

A change to shared data or helpers in the test directories, to `__init__.py`, and to a test module
imported by other tests selects a full run. `SHARED_TEST_MODULES` additionally
marks the `FlowCase` and `FlowInProcess` fixtures. A read/syntax error during AST analysis
also switches to the full suite. A missing declared contract module is an error.
Markdown documents (`*.md`) in the root directory, `docs/`, and components do not select code
tests, even when a document is deleted. In a mixed diff the selection follows from the other files.
In `docs/`, `.rst`, `.txt`, and illustrations (`.png`, `.jpg`, `.jpeg`, `.svg`, `.gif`, `.webp`) are also skipped.
The exception does not cover Markdown in `tools/`, `mk/`, test directories, or `fixtures/`: shared fixtures
may contain documents used as data. Code and configuration in `docs/` are not skipped.
Coordination files under `COORDINATION_PATHS` in `tools/test_suites.py` (`docs/agents/masterplan/`,
`docs/agents/interwencje/`, `docs/agents/plans/`; literal prefixes, without the rest of `docs/`) do not select
tests in any form: neither `.md`, nor `.json`, `.html`, `.tsv`, nor when a file is deleted. A file that
some test reads from the real repository gets a key in `FILE_TEST_MODULES`; the key takes precedence
over the list and selects its modules. The consistency test in `scripts/tests/test_test_dependencies.py` enforces
this mechanically: it scans the AST of all `.py` files in the test directories of `SUITES` and for each
path of an existing, tracked file under the coordination prefix requires a key with this module among the
consumers. It recognizes constant strings (also parts of an f-string and the text of shell commands, for example
`cp "$src/../docs/agents/plans/x.md"`) and sequences of constant segments after any base: `X / "docs/…"`,
`X / "docs" / "agents" / …`, `os.path.join(X, "docs", …)`, `posixpath.join`, `Path("docs", …)`,
and `X.joinpath("docs", …)`. False hits (the name of an existing plan used in data or in a temporary
repository) go into the tuple `COORDINATION_SCAN_EXCEPTIONS` keyed by the pair (test module, path),
with a comment at each entry; an entry that stopped being a hit fails the test. The scan does not
detect a directory read through a variable (a glob over `ROOT`) or a read through a product module constant
(for example `basechange.FOLLOWUP`): such receivers need a manual entry in `FILE_TEST_MODULES`.
The utility tools in `FILE_TEST_MODULES` in `tools/test_suites.py` have explicit contracts:
`board_compat_check.py`, `question_corpus.py`, `next_id.py`, `check_plans.py`, `sessions_preview.py`.
They select only the declared test modules; a missing such module is a configuration error.
The remaining `tools/` (including the whole runner, the registry, and the benchmarks) keep a full run.
A path outside the map (for example `Makefile`, `mk/`, unknown `tools/`, or a shared theme module)
triggers a full run. The other components keep the directory map: the longest
prefix wins, for example `apps/board/` selects `board`. Their tests often load code by path
or test QML, so we do not narrow them on the basis of incomplete imports.

The selector prints the count and every selected module together with the changed file and the reason
(AST chain, contract, directory map, or full run). It also records the reasons in the `selection` field of
`summary.json`. `tools/test_runner.py --changed --base <SHA> --list`
shows the same selection without running the tests. No changes ends in success without tests;
a nonexistent base ends in an error asking for `BASE`, without silently skipping the check.
When the change map selects the full suite in a ticket, run that run; do not run an additional
`make test` just to hand in the ticket. Close-out still checks the integrated card branch.

Plan approval time (K-102 #5): `python3 tools/approval_benchmark.py` builds a temporary repository
(300 plans, 40 branches, 8 worktrees), a card with 18 tickets, and a private number registry, and then prints a JSON line
with `seconds`, `planReads` (the count of full plan reads — a regression criterion, not time), `tickets`, and `tideRoot`.
`--base-ref <SHA>` measures the code of that revision in a temporary worktree (removed after the measurement), `--tide-root <ścieżka>`
the code of another tree. Targets outside the temporary directory end with code 2 before writing.

Measuring both selectors: `python3 tools/test_changed_benchmark.py --base <SHA-startu-ticketu>`.
The procedure compares the diffs of the last five non-merge commits from this base. The option
`--history-ref <SHA>` picks another point of history **for measurements only**: it allows taking into account
for example the documentation card K-71 created after the ticket start, without changing its base or checkout.
Then it runs the public `make test-changed BASE=<SHA-startu-ticketu>`.
It runs the tests of both selectors on one current code tree; it measures identical benchmark
suites once. The public validation remains an independent run. It gives the number of
modules and the real execution time, and separately the AST selection time. `--output <katalog>` keeps
the logs, `comparison.json`, and the `report.md` table, including the SHA of HEAD and its tree, the base, the command,
and the fingerprint of the working tree; resuming in the same directory reuses
completed measurements, provided the code tree, JOBS, base, and history range did not change. This is a full test procedure requiring
an environment in which private sockets and test sandboxes work.
A failed validation does not delete the collected comparison, but stays recorded in the report and causes
a non-zero result of the whole procedure. A finished timing table then does not mean a successful `test-changed`.

## Test exceptions

### Waiting budgets at the host Git boundary

The host Git boundary (PT-264) works only for box data: with `box.enabled`,
for a repository marked `tide-boundary`, and in the box path modules. Tests
with the box disabled therefore run Git as before PT-264 and have the original waiting
budgets: 30 s for the base correction callback (15 s for `BaseChangeInProcessTest.call`),
10 s for the merge callback, and 40/30/25 s for the end of a wave plan. The earlier
raise to 120/60/120 s compensated for the boundary overhead on every call
(owner's measurement 08.11: sum of module times ~4× the base) and was reverted together
with the conditional boundary (`docs/agents/pt-264/decisions.md`, "Granica Gita warunkowa" (Conditional Git boundary)).
The boundary scenarios are checked by modules with the box enabled (`boundary_on`
in `daemon/tests/test_host_git_invocation.py`); they do not measure response time.
The short windows for checking concurrency and absence of action remain separate. The report priority test releases
the first mock only after confirming the report is present in the queue, instead of relying
on the mock's two-second delay.

The test of a parallel StartCard and a CLI read uses the `release-hosting` barrier: the real
process of the CLI mock must still be waiting when the correct StartCard response arrives.
The response budget is 60 s, and the mock and its deadline 120 s; releasing in `finally`
ends the CLI with a controlled error and allows checking the warning. This check of
operation independence replaces the old response race with a 2-second window at
a 4-second CLI timeout. The measurement showed a local start of 5.66 s with protected Git;
the stack showed the reservation working while a separate thread was still waiting for the CLI.
The test no longer confirms the old 2 s threshold. The hostingcli timeout tests remain separate.

Two attempts to edit a working ticket use the same `release-agent` barrier,
so that the natural end of the eight-second mock does not replace the stop being checked.
After release, waiting for the plan end has the original budget of 25 s. The test of credentials of successive programs waits for the previous
card to close: the end of its ticket does not yet release the lock of one repository.

A budget change requires a measured justification and review just like test code. It does not replace
fixing a wrong result, does not disable boundaries or pool/isolation checks, and is not
a waiver of earlier failed runs. After a change a new
`make test-changed` applies; earlier failures remain in the notes.

### Settling failed runs

A failed test remains a failed result. A successful retry or "passes separately" do not replace
the first result and are not a standalone justification of a waiver. Keep the logs of both runs.
A waiver in the ticket note, and at close-out also in the card summary, contains:

- the first result: the command, SHA, failed tests, and a link to the log;
- the result of the same test on the ticket's pinned base and on `master`, with the SHAs,
  commands, and comparison conditions recorded (without changing the managed branch);
- the machine load at the failure and at the comparison, including parallel runs and the process limit;
- the justification and the exact scope of the waiver: which failures it covers and which still block;
- the owner of the fix and the way of tracking its execution.

Mark missing data explicitly; do not assume without evidence that the problem already occurred on the base or resulted
from load. The reviewer evaluates the justification and the scope of the waiver. Closing a card with a failed full
run is possible only with such explicit settlement and an explicit user decision to accept
the waiver. Until then the step reports a close-out problem, keeping the failed result. Accepting
a waiver does not change the test result to success and does not replace a separate consent to merge into the main branch.

#### A shared evidence entry for a recognized defect

The owner's decision of 16.11.2026 (masterplan review, the gate stabilization card K-147). The shortcut concerns
only repeating identical evidence documentation, not the passing criterion:

> For a previously recognized defect, a reference to a shared evidence entry is allowed instead of repeating
> the comparisons on the base and master for each ticket. The entry identifies the exact test and the failure signature,
> the cause, the SHA of the comparisons, the load conditions, the owner, and the fix card. The author shows that the change does not cover
> the mechanism affected by the defect or its dependencies, and keeps the result of the first run and of the rerun
> of failed modules on the same HEAD. A change of the signature, dependencies, environment, or a lack of comparable evidence
> disables this shortcut. A failure remains a failure; an exception requires the existing settlement and the owner's decision
> where it is required.

Every shared entry has a re-evaluation deadline, at the latest before v1 acceptance (M5); after the deadline the shortcut for that entry
expires until the entry is evaluated anew. Unacceptable remain: the label "the whole module is unstable",
`skip`/`xfail` for convenience, and retrying until it works. The first qualifying failures still count toward v1 criterion 5
(`docs/agents/masterplan/CURRENT.md` §0), also when a waiver was accepted.

## Direct service tests and state isolation

From the repository root directory we support three direct entry points:

```sh
python3 -m unittest daemon.tests.test_state_isolation
python3 -m unittest discover -s daemon/tests -t . -p 'test_state_isolation.py'
python3 -m pytest -q daemon/tests/test_state_isolation.py
```

An interpreter with the dependencies of the given test is required (for the third entry point also `pytest`).
The module name or pattern may point to any service test, including a new `test_cardflow*.py`.
`daemon.tests.__init__` isolates the whole package before product imports. Running the file itself,
which skips the required package imports, is not an additional test interface.

Each direct process gets its own `tide-test-state-*` from `tempfile.mkdtemp`, even if it
already found the session's `XDG_STATE_HOME`. `HOME` stays unchanged. The default `Journal` writes copies
to the test `$XDG_STATE_HOME/tide/cardflow/`; we do not change the production `mirror_root` or disable copies.
Repositories, boards, and deliberate overrides of the test environment (also `patch.dict`) must point
only to synthetic fixture directories. The isolation of the default XDG does not replace this responsibility.

The runner and the package initialization use the shared contract `tools/test_isolation.py`:
`TIDE_TEST_ISOLATED=1` and `TIDE_TEST_STATE_DIR` point to an existing absolute directory equal to
`XDG_STATE_HOME`. XDG alone, the marker alone, or an inconsistent set does not mean inherited isolation.
Descendants of the test, including the real service, inherit the environment and write to the same directory.
A descendant importing the tests borrows it without registering its removal. `state_environment` serves the runner
for explicitly passing a new test directory; its owner must create it beforehand and remove it afterwards.
A deliberate XDG override in a fixture also requires passing a consistent contract, if a child
process importing the test package is to use that override.

Before replacing XDG we store in `TIDE_TEST_ORIGINAL_STATE_ROOTS` JSON with the fields
`xdg_state_home` (the effective original XDG, with the default HOME/.local/state) and
`default_tide_state_home` (the original HOME/.local/state/tide). Descendants inherit the same record.
These roots are used by the write guard described below.

On a normal exit of a direct process, its `atexit` removes its own directory. Descendants
must finish before the parent; they do not remove the borrowed directory (also after fork).
A sudden kill of the process without running `atexit` may leave a temporary directory.
When run by the runner, the runner stays the owner: as before, it removes the directory after
the module finishes, also a failed one, and diagnostics stay in `.log`, `.json`, and `summary.json`.
The isolation tests compare the locations and content of synthetic writes, not the mtime of user state.

## The service test write guard

`tools/test_isolation.initialize()` also enables `tools/test_guard.py`, before the product import.
This applies to the whole `daemon.tests` package, with direct unittest/pytest and through the runner,
without additional fixtures in modules. We protect the original `HOME/.local/state/tide` and
`<original XDG_STATE_HOME>/tide`. We store the original paths and those resolved by `realpath`;
we also check links to directories and files, targets that do not exist yet, paths relative to
`cwd`/`dirfd`, both sides of rename/link, and deleting or moving an ancestor of a protected directory.
We resolve links component by component in the context of the reporting process.
`/proc/self` and `/proc/thread-self` keep its process/thread identifier also when
they appear only in the target of a subsequent link, for example through `/dev/fd/N` or its own chain of
aliases. Reopening a protected file through a legitimate O_RDONLY descriptor with O_TRUNC
is blocked before zeroing, also in a child shell. Legitimate aliases outside the protection
keep the write permission; removing the external link itself is still allowed.

The guard requires Linux x86-64, seccomp user notification (with CONTINUE support), and ancestor access
to `/proc/<pid>/{mem,fd,cwd}`. It does not require a compiler, root, changing directory permissions, or mounts.
The entry process becomes the supervisor, and its child continues the test. The kernel filter covers this process,
new threads, and descendants through fork/exec, also foreign programs, static executables, and processes
with a cleared environment. The supervisor is a subreaper: an orphaned process after a double-fork/setsid
or a service stop still has it as an ancestor, keeps the protection, and has access to legitimate targets.
We do not change the system `ptrace_scope` or the permissions of other processes.
`TIDE_TEST_GUARD` gives the supervisor's PID for diagnostics only:
inherited protection is confirmed by an ineffective `prctl` attempt handled by the filter, not by the env value.

The supervisor reads the targets of system calls before they are executed. It refuses writable
open/creat, truncate, unlink/rmdir, mkdir, rename, link/symlink, and metadata changes in the protected
roots; it also checks writes through descriptors and shared file mappings.
In the protected roots it rejects every file `MAP_SHARED`/`MAP_SHARED_VALIDATE`, also
with an initial `PROT_READ` or `PROT_NONE`: a later `mprotect` or `pkey_mprotect` could
enable writing without another controlled file call. This also applies to an
inherited O_RDWR descriptor. The refusal happens already at `mmap`, gives EPERM and a persistent
scenario code of 125. This conservative restriction also covers shared mappings
used only for reading. Ordinary reads, `MAP_PRIVATE` private mappings, and anonymous
mappings remain allowed; a private change of memory does not change the file.
Python gets `PermissionError`, and for example `/bin/sh`, `rm`, or `mv` — the system `EPERM`.
We distinguish mutating the link target from changing the entry itself: unlinking/renaming an external
link is legitimate if its entry and parent directory lie outside the protection; an operation on a file
inside a linked directory is still blocked. We also handle `utimensat(fd, NULL, …)`
(futimens used by tar): the descriptor's file is evaluated, not the NULL address.
A mutating `openat2` with `RESOLVE_IN_ROOT` is always rejected with `EPERM` and causes
the scenario code 125, also for a target outside the protected roots. This mode changes the meaning of
`/`, `..`, and absolute links relative to `dirfd`; the guard does not try to replace its
semantics with an ordinary `realpath`. Reading through this mode remains allowed.
We do not rely on monkeypatching `open`, the child's argv, or observing changes after the fact.
The supervisor is not subject to the filter and keeps the information about a violation until the end of the whole tree.
Each refusal prints on its stderr `TIDE TEST GUARD:` and JSON with `scenario` (the test's PID),
the offender's `pid`, `operation`, and `targets`. After a violation the result of the whole run is **125**,
even if unittest/pytest prints success, the product catches the exception, ignores the child's error,
or calls `os._exit(0)`. The runner keeps the refusals in the module log and treats its code as an error.

An initialization failure gives `setup_error` and code 125, without continuing the unprotected product.
Losing the supervisor's access to a target also means a refusal and a scenario error. Mechanisms without
a supported path (Linux AIO, io_uring, open-by-handle) get `ENOSYS`, so that a library can
use controlled equivalents. A stale notification after a process ends is not a
violation: before reading `/proc` we check the notification identifier in the kernel.
The supervisor collects the statuses of orphaned processes. After the main test process ends it gives
the remaining descendants a second to finish (still checking their mutations), then ends
them with SIGKILL and collects their statuses before handing in the result. This applies only to its tree,
also processes in their own sessions; independent services stay outside this cleanup.
The filter's HUP signal does not replace `waitpid`: after it the supervisor collects all remaining statuses,
also when the last process ended between an earlier WNOHANG and reading the HUP.
Fixtures should still clean up processes during the test. An external SIGTERM/SIGINT is passed on
to the test process, if it is still alive.

This is protection against accidental test leaks, not a sandbox for executing hostile code.
`SECCOMP_USER_NOTIF_FLAG_CONTINUE` has a race between evaluating the path and the operation (for example a deliberate parallel
swap of a link or of syscall arguments). We do not guarantee protection against such attacks,
existing hard links outside the roots, changing the mount namespace, or an arbitrary
device ioctl. The guard does not protect the whole HOME; fixtures still point to synthetic data.
A service process started independently of the test tree keeps its permissions. Its legitimate changes
are not a test violation: we do not compare mtime or a snapshot of the shared user directory.

The control test `python3 -m unittest scripts.tests.test_state_guard` runs separate negative
scenarios under unittest and pytest (when available). It expects code 125, diagnostics, and identical
files of the synthetic state. It separately checks Python, a child with the default `Journal`, a further
descendant, the real tide-daemon on a private bus, and `/bin/sh` with real
write/delete/rename commands. The probes `daemon/tests/guard*_probe.py` are not part of automatic discover:
only the control test runs them. A missing private bus skips the full service probe,
not the remaining evidence. Handing in a ticket requires a run in an environment with this bus.

## Parallelism and the result

By default the number of processes is the number of cores available to the process minus two, at least one.
`JOBS=N` overrides this limit. All runs at once (for example several Tide tickets) share a common pool
of machine slots of the same size (`$XDG_RUNTIME_DIR/tide-test-slots`, `flock` locks): a second
`make test` does not add more processes, but waits for free module slots. `TIDE_TEST_SLOTS=N`
changes the pool size, `TIDE_TEST_SLOTS_DIR` its directory. Each `test*.py` file is a separate system Python process;
`unittest discover` uses the same top-level directory `.` and the same pattern as before.
We do not split a class or a single test. We also keep the repetitions of classes imported
by other modules that the previous `discover` ran. The package `load_tests` hook requires
a separate runner adjustment; the current runner refuses rather than lose such tests.

The processes have separate Python state and environment, but must isolate their own external resources:
`TemporaryDirectory`, a private D-Bus, and a port assigned by the system (`bind(..., 0)`). If a test
requires a shared resource, add its path to `SERIAL_MODULES` with a concrete justification.
Such modules run one at a time only after all the parallel ones. An empty list means that
no need for serialization was found; do not add exceptions solely because of using Qt or D-Bus.
The limit applies to one runner invocation; several simultaneous `make test` invocations have independent limits.

In PT-215 `test_cardflow_review.py` (the close-out tick competing with the decision
callback) and `test_cardflow_dbus.py` (restoring the journal file visible before the warning
in the snapshot) were marked separately. Both passed 3 single attempts, but under a load of 14 processes had
1/5 and 2/4 failures respectively. The justifications are next to the `SERIAL_MODULES` entries; the tests still
run and their errors go into the shared report. `FlowInProcess` additionally ends
threads and drains the Qt callback queue before restarting or removing the fixture. Results of a closed
monitor are discarded, so that the work of the old engine does not pass into the next test.

Logs go to a unique `build/test-results/run-*/`: a `.log` and `.json` file per module and
`summary.json` with time, exit codes, counters, and identifiers of executed tests.
After a module finishes you see the name, result, and time; for a failed one also its log.
The runner waits for all modules and at the end prints a full list of failures, errors,
unexpected successes, and process crashes together with module names (also subtest errors).
The exit code is non-zero if anything failed.

`JOBS=1` keeps the new split into processes; a measurement of the old run must run whole
`discover` directories in sequence. The PT-215 measurement report records both ways and the number of tests.

`make test-benchmark` performs such a comparison on the same machine: first a full
`discover` of each directory in sequence, then the public `make test`. It records times, exit codes,
and a comparison of test identifiers **with repetition counts** in `build/test-benchmark/comparison-*/`
(`comparison.json`, `report.md`, logs). It also compares errors, skips, and expected failures.
On an error it continues with the remaining directories, so that the comparison
does not break off prematurely. It requires the same environment as the full tests, including private
D-Bus sockets, local mock servers, and sandboxes launched by the tests. Do not run it
alongside another full run, because the time measurement would be disturbed. `TEST_OUTPUT=<katalog>` allows
pointing to another parent directory for results; each invocation still creates a unique subdirectory.

## A module run loop under load

We reproduce an unstable test (K-100) and prove the fix with a single command, `tools/module_loop.py`.
It repeats the module under test N times (by default 20); each run is a separate invocation of
`tools/test_runner.py --modules … --output <katalog przebiegu>` (the machine slot pool applies, the tool
does not call unittest itself). The CPU burners are pure Python processes, always stopped (`finally`, SIGINT,
SIGTERM, process groups; `PR_SET_PDEATHSIG` also kills a burner suspended after the tool's SIGKILL).

```sh
/usr/bin/python3 tools/module_loop.py daemon/tests/test_cardflow_waves.py              # proof: constrained burners
/usr/bin/python3 tools/module_loop.py daemon/tests/test_cardflow_waves.py --slow 417   # experiment: slow engine
/usr/bin/python3 tools/module_loop.py daemon/tests/test_cardflow_waves.py --reproduce  # reproduction: burners 1.5×
/usr/bin/python3 tools/module_loop.py daemon/tests/test_cardflow_waves.py --pool       # reproduction: pool
/usr/bin/python3 tools/module_loop.py <moduł> --pool --with <moduł…> --runs 2 --output <katalog>
```

Common parameters: `--runs`, `--wait` (the longest time to reach the threshold, s), `--quiet-max`, `--board`,
`--own-step`, `--own-worktree`, `--output` (by default `build/module-loop/…`); the mode parameters are below.

### The K-100 proof criterion

Proof of a test's stability should measure races in the test, not machine overload (08–12.11 20× loops in pool mode
at loadavg 50–90 lasted for hours and failed from overload). Proof of a fix of an unstable test is:

1. **20 runs with constrained burners** (the only proof mode) — N/N valid with the module under test green
   (code 0). The slow engine (`--slow`) is experimental: in the PT-417 calibration (three series in the quiet window
   12/13.11) it did not reproduce the known PT-366 failure, so it gives no proof. The calibration is a follow-up item:
   [followups/slow-engine-calibration.md](agents/plans/followups/slow-engine-calibration.md).
2. **In the quiet window:** loadavg1 before the burners < 8 and no other `outside` command is running. We run the loops
   in windows: night **23:30–06:00 from Thursday to Friday and from Friday to Saturday**, and **Saturday 06:00–09:00**.
3. **The pool (`--pool`) and burners 1.5× (`--reproduce`) only for reproducing** a failure; they do not give code 0 and are not
   proof.
4. **No lengthening of time limits as the only fix.** A longer `timeout` in `wait_until` does not remove the race;
   the fix removes the cause (for example waits for the right journal event).

### Modes

- **Proof — constrained burners (default):** only the module under test runs in the run. There are ⌈target⌉ burners
  (`--burners` changes the upper count), and a regulator every second determines how many of them are active; the rest are
  suspended (SIGSTOP, resumed with SIGCONT). We chose the number of active burners, not their duty cycle: an active
  burner is exactly one ready process, so the number of ready processes translates directly into loadavg.
  The regulation (`regulate`) reads loadavg1 and the number of currently ready processes (the 4th field of `/proc/loadavg`); ready
  processes other than the burners are the test and the rest of the machine. The goal is 0.9 × target ready processes including the test;
  within the 0.8–1.0 × target band the regulator adds no burners, and loadavg1 above the target always reduces their number,
  down to suspending all of them. The target: `--target` (by default the number of cores available to the process), the run start
  threshold: 0.8 × target (`--threshold`). The report gives the maximum loadavg1 from the samples and the range of active burners;
  a maximum above the target is a warning (when the target is exceeded by the test alone, the burners are already suspended).
- **Experiment — slow engine (`--slow [ZIARNO]`):** uncalibrated, gives neither proof nor code 0 (the report
  says so plainly; code 4 when there is no failure). It serves for searching for event orders and further calibration
  ([followups/slow-engine-calibration.md](agents/plans/followups/slow-engine-calibration.md)). Without burners. Each run gets
  `TIDE_TEST_SLOW=<profil>:<ziarno>:<numer przebiegu>` (the profile `--slow-profile`, by default `domyslny`; a seed
  without a value — from the clock), and the fixtures and mocks in `daemon/tests/` inject deterministic delays
  (`daemon/tests/slowengine.py`) that force event orders usually randomized by load. The report
  records the seed of each run; a run is repeated with the same variable value:
  `TIDE_TEST_SLOW=domyslny:417:7 /usr/bin/python3 tools/test_runner.py --modules <moduł>`. Without the variable the fixtures
  and mocks work bit for bit as before, and the service code (`daemon/tide_daemon/`) has no hooks at all.
- **Reproduction — burners 1.5× (`--reproduce`):** ⌈1.5 × cores⌉ burners without regulation, loadavg1 threshold =
  number of cores (`--burners`, `--threshold`). Loadavg1 is an exponential average approaching the number of ready
  processes from below, which is why there are 1.5× more burners than the threshold.
- **Reproduction — pool (`--pool`):** burners 1.5×, and the module under test runs in one launcher invocation together
  with companions and with its default `--jobs`, so the launcher splits modules into parallel and `[OSOBNO]` (separately) as in the full `make test`
  (14 processes, private buses, ports, child processes). Companions: `--with <moduły…>` or the constant
  `DEFAULT_COMPANIONS` in the tool — 20 modules of `daemon/tests/` with a private D-Bus bus, child processes,
  and the `FlowInProcess` fixture (including the remaining K-100 modules: `test_cardflow_youtrack`, `test_cardflow_waves`,
  `test_daemon_lifecycle`, `test_cardflow_review`, `test_cardflow_dbus`,
  `test_worktrees_shutdown`, `test_cardflow_integration_k38`), which together with the module under test gives at least
  `JOBS` parallel modules (the tool refuses when the default set is too small for this machine). The module under test
  is not duplicated in the list. The pool remains heavy — do not run several at once.
- **When pool mode:** the module is `[OSOBNO]` in `SERIAL_MODULES` (the report says so plainly), or a failure from the full
  `make test` is not reproduced by another mode.

### Run validity and the quiet window

A series is **outside the quiet window** — with no runs, all invalid, code 3 — when before starting the burners
loadavg1 ≥ 8 (`--quiet-max`) or another command outside the sandbox is running: an open `outside.started` without
`outside.finished` in the Board card journals (`<tablica>/flow/<karta>/journal.jsonl`; `--board`, by default
`$TIDE_BOARD_DIR` or `~/DevWorkspace/tide-board`; read only). The step of the loop itself does not count as another: the
tool skips `outside` commands from the worktree it lives in (Tide runs `outside` in the ticket's worktree),
from the worktree given with `--own-worktree`, and steps given with `--own-step` or `$TIDE_MODULE_LOOP_STEP`.

A run is **invalid** when: loadavg1 at the start > 1.5 × cores; during the run (read at the start,
every 3 s, and at the end) another `outside` or another `tools/test_runner.py` process outside the tool's process tree
was running; in the burner modes loadavg1 did not reach the threshold before the run (the tool waits before each
run; before the first one a timeout is an error without runs, code 2) or a burner died. A drop in
loadavg1 below the threshold during or at the end (a short module, launcher transitions) gives only the warning
"spadek obciążenia" (load drop) with the minimum from the samples.

### The report and exit codes

The report (`summary.json`, `report.md`): SHA, command, mode (`dowód: palniki-ograniczone`,
`eksperyment: powolny-silnik`, `odtworzenie: palniki-1,5x | pula`), the module under test and companions, `[OSOBNO]`, cores, burners, the loadavg1 target and threshold,
`JOBS`, the slow engine profile and seed, the quiet window (loadavg before the burners, other detected
`outside`: card, step, command), and for a run: the seed, loadavg 1/5/15 at the start and end, the minimum
and maximum loadavg1 from the samples, the range of active burners, validity and reasons for invalidity (with other `outside`:
card, step, command), warnings, the launcher code, time, and the names of failed tests separately for the module under test
and the companions. Companion failures do not change the code, but the report counts them — the executor settles them in the note.

The codes: **0** — proof: N/N valid runs with the module under test green in proof mode; **1** — failure
of the module under test or an invalid run; **2** — an error (threshold not reached before the first run,
interruption); **3** — outside the quiet window; **4** — reproduction or experimental mode without a failure (the report says
plainly that this is not proof). Tests: `daemon/tests/test_module_loop.py` (the tool), `daemon/tests/test_slowengine.py` (the slow engine).

### The slow engine delay point

The points and their parameters (probability, the longest delay in seconds) are in `PROFILES`
in `daemon/tests/slowengine.py`. The current points: `fixture.tick` and `fixture.callback` (the monitor tick in
`FlowInProcess` and the response after it), `fixture.observe` (after `wait_column` the test thread waits with a running event
loop, so the monitor timer gives further ticks and the engine gets ahead of the test as under load; a `sleep` in the test
thread alone is not enough, because it also stops the ticks started from the timer of its event loop), `claude.start`
and `claude.result` (the agent mock: step process start, writing `result.json`), `systemd-run.start`, `systemctl`,
and `service.poll` (an addition to the service's `TIDE_FLOW_POLL_MS` on the private bus in `FlowCase`). A new point:

1. Add it to the profile in `PROFILES` with a small maximum relative to the `wait_until` limits of the tests that pass through it.
2. In the fixture (the test process) call `slowengine.sleep("<punkt>")` at the place whose order is to
   shift. The call counters are separate for each test (`slowengine.begin(self.id())` in `setUp`).
3. In the mock from `flowfakes.py` add the point to `SLOW_POINTS[<mock>]` together with the call `_slow("<punkt>")`
   in the code pasted at installation; the mock counters live in `<katalog atrap>/.slow-counters/`.
4. Without `TIDE_TEST_SLOW` nothing may change: `sleep` returns 0, `install` writes the mock constants unchanged
   (test `test_without_variable_fakes_are_bit_for_bit_the_constants`). We add no hooks in `daemon/tide_daemon/`.

The delay is a pure function of the key (profile, seed, run), the point name, and the call number. The number of calls
of a point in a test, however, depends on the thread interleaving (for example the number of ticks), so a repeat with the same seed gives
the same delays in the same order of calls, but does not guarantee an identical interleaving.

## Report of test dependencies on the engine

`python3 tools/test_dependencies.py` (`--json`, `--root <drzewo>`) prints for `engine.py` and each file of
`daemon/tide_daemon/cardflow/` the reach (test modules reachable in the AST import graph, certain / with potential ones),
"sięga silnika" (reaches the engine) with the shortest chain, "testy własne" (own tests; reachability without the `engine.py` node), and the selection markers
`pełny` / `fallback` / `kontrakt` (full / fallback / contract). Under the table: `cardflow/` modules (without `engine.py`) with no path to the engine and the test
modules of the cardflow suite that do not reach the engine. The reach **is not** the selection of `make test-changed` and today is almost constant
(the engine reaches almost everything), so the rule "a module does not import the engine" is measured by the "sięga silnika" column and its sum.
The tool does not import the analyzed code; it takes suite definitions from its own tree, files from `--root`.
The rule is enforced by `daemon/tests/test_cardflow_engine_boundary.py` (an explicit list of modules, a failure shows the import chain).

## Rerunning failed modules

Stage A of saving test time (06.11.2026: 31% of ticket test time was reruns of the whole suite on
unchanged code). When a ticket run failed and the author changes no file (a suspicion of instability
or load), they do not repeat the whole `make test-changed`/`make test`. They run only the modules from the list
"Wszystkie nieudane testy" (All failed tests) (or from `summary.json`, `returncode` other than 0), each separately:

```sh
/usr/bin/python3 tools/test_runner.py --modules <ścieżki modułów>
```

`--modules` runs exactly those modules with the same machine pool, a separate state directory for each module,
and a `summary.json` report like a full run. A card branch from before this option reruns modules one by one:
`d="$(mktemp -d)" && XDG_STATE_HOME="$d" /usr/bin/python3 -m unittest discover -t . -s <katalog> -p <plik>`.

In the note they record the full run (command, `git rev-parse HEAD`, result, report) and the rerun (the same HEAD,
a clean `git status`, the list of modules, the result). The reviewer accepts the rerun only with the same HEAD as the full run.
Every file change restores the full command of the test rule. The full `make test` at card close-out stays;
if it fails, the close-out agent also reruns only the failed modules on the same HEAD.

A rerun limits what is run, and does not change the assessment: the failure of the first run remains the result,
and a successful rerun serves as evidence for settlement according to [test exceptions](#test-exceptions).
Projects in other technologies apply the same rule using the project's test tool with a filter for failed tests.
The text of the rule in the step sheets: `rerun_failed` in `daemon/tide_daemon/cardflow/handoff.py`.

Tide enforces this rule itself (`daemon/tide_daemon/cardflow/testrerun.py`): when a command outside the sandbox is
a full run (`make test`, `make test-changed …`), and the last such run in the same worktree failed on the same
HEAD with a clean tree and the same command (target, BASE, JOBS), Tide runs instead of it
`tools/test_runner.py --modules` with the modules that failed according to the `summary.json` of that run, and records
`rerunOf` in the journal. A missing report, a missing list of failed modules, a code change, or `TIDE_FULL_RUN=1` in the command
mean an ordinary full run. This applies to branches whose launcher has the `--modules` option.

`TEST_OUTPUT=<katalog>` only points to the report location: a command with it is still a full run, when comparing
runs the directory is ignored, and the rerun writes the report in the directory from the current command (08.11: through
`TEST_OUTPUT` of the step directory the rerun did not turn on in any of the 8 truncated PT-264 runs).

A run cut off by Tide (the command time limit outside the sandbox or a unit stop) has no `summary.json`.
The launcher writes `modules.json` at the start with the list of the run's modules, and each module leaves a report with the process
exit code, so the rerun covers failed modules and those without a result (in progress or not started), and skips only
those finished with code 0 without failures. An older launcher (without `modules.json`): a full run, because modules not started
cannot be determined. An ordinary failure with an exit code still requires a complete `summary.json`.

## Older branches without the test-changed target

The exception applies only to a branch whose Makefile (with `mk/*.mk`) does not yet provide
`test-changed`: the author and the reviewer run the full `make test` there and record the command and the result
in the note. Tide checks the availability of the target in the ticket's worktree when creating the handoff sheet
(`cardflow/handoff.py`). It does not require moving the test infrastructure from the main branch into an old
card. When the target is available, the pinned `BASE` and the ordinary `make test-changed` apply.
This exception does not remove the full test of the card branch at close-out.
