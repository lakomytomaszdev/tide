# PT-186 — acceptance of a real project

The profile next to this one describes the public subset approved by the coordinator's decision in step PT-186.
It is not proof of a green build. Do not copy it into the project repository.
The decision on the criteria of the local source and on the input state: `docs/agents/pt-186/decisions.md`.

## v1 scope after the real trial

A full restore of the solution from nuget.org ended with NU1101/NU1102 errors.
The coordinator limited the PT-186 acceptance to `src/Commons/Commons.csproj` and
`tools/UploadSmokeTest/UploadSmokeTest.csproj`, whose public restore passed.
The profile lists them in `dotnet.restore-projects`: a non-empty list of relative .csproj paths
inside the source copy, included in the recipe hash. Without the list the builder keeps the restore of the whole solution.
Every project on the list gets the same generated config with nuget.org as the only source;
a failed restore blocks verify. Verify builds both projects with `--no-restore`.

The API tests and the projects requiring `Firma.Core`, `Firma.Core.Files`,
`Firma.App5.Core`, `Firma.APP5.DataAccess`, `Firma.MenuSynchronization`, `PdfGenDp5`
and `PdfGen >= 1.1.1` were moved to K-75: their required versions are missing from nuget.org. This applies to, among others, Infrastructure,
Dp5PostgresIntegration, PdfServices, ApiService, Web, PdfE2eTest, RefactoredInfrastructureTests
and ApiServiceTests. The local source indicated in the repo is nuget_devhost.config (`/samba/nuget/`);
the project profile does not expose it. The chosen public subset contains no test project;
a green build must not be described as passed API tests.

## Reconnaissance

On resuming, run through the step's command contract:

```text
python3 scripts/box/inspect-infracore.py --repo /home/user/DevWorkspace/infracore --output <katalog-wyniku-kroku>/inspection.json
```

The script does not change the project repository, does not run package managers, does not read agent settings or MCP.
It records the StackReport, the proposal and its items, the status, and fingerprints of the working-tree and index diffs. The indicated result directory
must exist. Do not interpret `detectedSdkOutsideCandidate` as consent to an additional SDK: e.g. an old net5.0 project
may lie outside the solution. Check the solution's composition before changing the profile.

## Acceptance conditions outside the sandbox

1. Confirm the existing Board project and its repo through the service. Do not create a second entry or edit the
   Board by hand. Record the service's response and the commit. Do not overwrite earlier project settings.
2. Before approval show the user the real profile with pinned SDKs. Do not treat approval of the
   criteria correction as approval of the profile. The private source does not go into `network.domains`, `setup` or mounts.
3. Build the box with `tide box build infracore`, record the time, the JSON status and the logs. The `clean`
   state with a warning alone does not satisfy the green-verify criterion. For a .NET project from the Board the builder prepares
   an archive of the approved HEAD without .git and without working changes. It checks the archive (no symlinks, paths escaping
   the root, or special files; limit 256 MiB), sends the copy to the box, does an explicit restore from nuget.org,
   and runs verify as the agent in the source copy. Before clean it removes the copy; a cleanup error aborts the build.
   `box.verify.source_revision` points to the checked commit. Do not replace the build with an empty project or with just
   `dotnet --list-sdks`. `dotnet build --no-restore` after an explicit restore does not reach for the project configuration again.
4. Restore of the indicated projects of the public subset in an isolated worktree is done explicitly with `--source https://api.nuget.org/v3/index.json`
   and `--configfile NuGet.Config`. During verify the Builder uses a separate, generated file with `<clear />`
   and nuget.org as the only source. Never use `nuget_devhost.config`. A missing private package means a real
   dependency mismatch, not a reason to expose the private source yourself.
5. Tests (moved to K-75 according to the decision above) are run with `tide box run infracore --worktree <project-worktree> -- dotnet test ...`.
   Choose tests without a database after reading their initialization, not by the project name alone. Describe Firebird tests without a
   detected image as "do sprawdzenia na pulpicie" (to be checked on the desktop). Do not add a guessed image.
6. In the same box check reading `/samba/nuget/`: a missing directory or denied access counts as a pass,
   a readable directory counts as a fail. A tool start-up error is not a denial of access. Do not mount the host
   directory for the purposes of the trial. Record the result separately from the proxy table — it is not an HTTP 403.
7. Run `make box-check BOX=infracore`; record the whole table. "Not applicable" is not evidence
   of a denial and needs to be supplemented to reach the required 100%. A push attempt requires a separate allowance for the push ban
   in this step — do not run it yourself from the sandbox.
8. One real ticket on the test card must end `done` without `envAnswer`, `outside`, `denied`.
   It must be a flow of the real service, not a synthetic unit-test dummy.
9. Compare the state of the target repository with the input state (status and diff fingerprints); keep earlier
   user changes. Tide's results and files are written in the Tide worktree and the step directory.

Before handing in the implementation: `make test-changed BASE=4073e42490f1c73168163f1ee7e50614918c9dfd`.
If the selection picks the full suite, run that suite; do not add a separate `make test`.
