# Tide issues and CLI — API v1

Preparing a report from a conversation through the Claude Code plugin or the Codex skill:
[The Issue skills package](issue-skills.md). The CLI operation tests and the real evaluation of the synthesis
have separate procedures and evidence described there.

An issue (`S-N`) is a durable report of a conversation, independent of a card (`K-N`), a ticket, and a specification.
PT-218 delivers draft saving and reads, PT-219 editing and the lifecycle; the store and the public
read of attachments — PT-228. Creating an issue does not run a model and does not create a card or a project branch.

## The public boundary

The session bus, the service `org.tide.Daemon`, the object `/Issues`, the interface `org.tide.Daemon.Issues`.
The methods return a single JSON string; the exception is `ReadAttachment`, which returns JSON and a byte array.
Report and evidence operations do not accept paths to client files or Board read paths.
The launch settings use the existing StartCard contract, described separately below.
The CLI is a standalone client of this interface; it does not import the `tide_daemon` package.

The card steward conversation has a separate `/Board` contract from PT-149; see the description
in the section [Card steward conversation CLI](#card-steward-conversation-cli--pt-226).

| Method | D-Bus arguments | Result on success, besides the shared envelope |
| --- | --- | --- |
| `Info` | none | the issue capabilities; with the flow enabled also `issue.go`, `issue.retry-start`, `issue.preview-launch` |
| `Create` | `s request` (a JSON object described below) | `issue`, `existing: bool`, `commitError: string` |
| `List` | none | `issues: [...]` (metadata without the report content), `errors: [...]` |
| `Show` | `s id` (exactly `S-N`, N = 1…999999999) | `issue` (metadata and content), an optional current `start` |
| `Update` | `s id, s request` | `issue`, `commitError: string` |
| `Submit` | `s id, s request` | `issue`, `commitError: string` |
| `Reject` | `s id, s request` | `issue`, `commitError: string` |
| `Reopen` | `s id, s request` | `issue`, `commitError: string` |
| `AcceptIssue` | `s id, s request` | `issue`, `card`, `acceptedVersion`, `alreadyAccepted`, `commitError` |
| `RetryIssueStart` | `s id, s request` | `accepted`, `issue`, `card`, `acceptedVersion`, `started`, `queued`, `startError`, `start` |
| `PreviewIssueLaunch` | `s id, s request` | `snapshot`, the proposed `card`, `branches`, `defaults`; without writing |
| `Attach` | `s id, s request, ay data` | `issue`, `commitError: string` |
| `Detach` | `s id, s request` | `issue`, `commitError: string` |
| `ReadAttachment` | `s id, s request` | **two output arguments**: `s result, ay data`; described below |

The shared success envelope: `{"ok":true,"apiVersion":1,...}`.
An error: `{"ok":false,"apiVersion":1,"error":"bad-request","message":"…"}`.
`Info` also works with `--no-board`, in which case the capability list is empty; issue writes and reads
return `unavailable`. Before an operation the client checks for exactly version 1 and the required capability.
A missing or incompatible API ends the command without writing, local import, or starting a model.

### Card steward conversation CLI — PT-226

```text
tide card open K-N [--problem <id>] --json
```

The owner of the operation is **K-37/PT-149**: the public `/Board`, the interface
`org.tide.Daemon.Board`, `TalkToCaretaker(s id, s request) → s`.
The CLI passes the card identifier and `{}` or `{"problem":"<id>"}`. The success
`{"ok":true,"step":"…"}` means the service accepted opening the conversation, and exit code 0.
The JSON result is kept without adding the issue API envelope. The `step` field identifies the conversation step.
The same entry point is called by the existing button "Porozmawiaj z opiekunem" (Talk to the steward).

PT-149 handles a started, unfinished flow with an available card worktree.
The optional `problem` must still exist. Without it the service picks a problem or a general conversation.
A card without a started flow, a finished one, or a problem that is no longer current returns `no-step`.
Reopening while a conversation is open gives `console-open`; it promises neither activation nor resumption.
The context, the tool profile, write protection, actions, and the conversation lifecycle belong to PT-149.
The adapter changes neither the column nor the pause and does not start a local program.

| Response / situation | CLI exit code |
| --- | --- |
| `ok: true` and a non-empty `step` | 0 |
| `bad-request`, wrong CLI arguments | 2 |
| `no-terminal`, daemon unavailable, a missing method, or an incompatible contract | 3 |
| `console-open` | 4 |
| `no-step`, `not-found` | 5 |
| Other PT-149 refusals, for example `no-executor`, `local-only`, `no-worktree`, `step` | 1 |

A refusal keeps all the fields of the service response. A missing public method ends with
`api-incompatible`, code 3, and without opening a conversation. There is no fallback to `CardContext`,
`OpenCardGuardian`, or `card.session`.

`tide info --json` adds `card.open` to `capabilities` only after checking the actual
method signature in introspection. `operations["card.open"]` contains `available`,
`contract: "PT-149"`, `contractVerified: false`, and a description of the required verification.
`available` confirms the API entry point, not the working of the whole contract.
The CLI does not run a test opening a conversation during `info` and does not store an attestation
of the running daemon. The proof of the complete contract comes from a test on a private D-Bus
with a Console mock and is recorded in the ticket's execution note.

The tests of the adapter and the installed CLI: `daemon.tests.test_issues_card_cli`,
registered by the pattern `test_issues*.py` in the `issues` suite and in the `daemon` suite.
The tests compare both opening orders CLI/button through the shared D-Bus entry point,
one conversation per card, the column, the pause, and refusals. The binding of the real button
is checked by `apps/board/tests/test_card_full_view.py`,
`FullViewTest.test_talk_button_and_escalated_decision_call_the_service`.
The dependency contract test: `daemon.tests.test_cardflow_dbus.CaretakerChatDbusTest`
and `daemon.tests.test_cardflow_caretaker_chat` (including write protection).

### Create — deployed in PT-218

```json
{"report":"# Treść raportu\n", "title":"Krótki tytuł", "project":null, "requestId":"rozmowa-2026-11-06-1"}
```

The field list is closed: `report` and `title` are required, `project` and `requestId` optional.
Unknown and repeated fields are an error. `id`, `state`, `version`, dates, the card link, the manifest,
and all paths are owned by the service. Sending them does not import the metadata.

`title` is text of 1–200 characters, non-empty after `strip()`, without control characters U+0000…001F.
The save keeps the given title. `project` is an existing Board project slug (1–64 characters,
lowercase ASCII letters, digits, `.`, `_`, `-`, the first character alphanumeric). A missing field or `null`
means no assignment. An empty slug, an unknown project, and a deleted project are an error. The title and project
are not derived from the report. Validation precedes reserving the number and publishing the issue.

`report` is just the UTF-8 Markdown content, without NUL, up to and including **65536 bytes** (the limit applies to
the content after JSON decoding, not to the JSON transport). An empty report is allowed. An initial line
`---` is reserved for frontmatter and rejected, also with a BOM, CRLF, or whitespace
around the separator. Inner `---` lines are allowed. The service does not import initial metadata,
does not change the content, and generates its own frontmatter. `Show` separates the metadata from the original content.
Invalid UTF-8 in a file is rejected by the CLI; invalid Unicode sequences in JSON are also rejected by the service.

`requestId`, if given, is non-empty text of 1–200 characters without U+0000…001F. The key's scope
is the whole Board repository. The same key and identical `report`, `title`, `project` restore
the original `issue` response, also after a restart (even after the project is deleted). A missing project and
`null` are the same payload; we do not normalize the content or the title. `existing` reports a repeat.
A different payload under the same key gives `conflict`, without a new issue. Without a key each call
is a new intent. After a lost response the client should retry with the same key and payload.

### Update — deployed in PT-219

```json
{"expectedVersion":1,"report":"Poprawiona treść","title":"Nowy tytuł","project":null}
```

The exactly allowed fields: the required `expectedVersion` and the optional `report`, `title`, `project`.
At least one of these three fields must be present. An omitted field stays unchanged.
`null` clears **only the project**; `report:null` and `title:null` are errors. The rules for the content,
the title, and the project's existence are identical as for create. Unsupported/repeated fields are a
data error. `expectedVersion` must be a positive integer (not a bool or a fraction) and is
a condition of agreement with the current version, not a request to set `version`. A mismatch means
`version-conflict`, without writing. The error response contains the current version as a positive
integer `currentVersion`, for example
`{"ok":false,"apiVersion":1,"error":"version-conflict","message":"Wersja sprawy uległa zmianie.","currentVersion":2}`.
The CLI returns exit code 4 and keeps `currentVersion` in the `--json` result.
An effective change increases the version by one; `id` and `created` stay
unchanged, `updated` is set by the service. Merely resubmitting the same values does not change
the version. The service checks the condition also for such a request. It is not `requestId` idempotency.
`draft` and `ready` can be changed; an actual change of `ready` returns to `draft` and requires a new
submit. A request with identical values also keeps the `ready` state. `rejected` first requires
reopen, and `accepted` and `archived` refuse changes. An invalid state gives `bad-request`.

The command:

```text
tide issue update S-N --expected-version N [--report <plik>] [--title <tekst>] [--project <slug> | --clear-project] [--json]
```

`--project` and `--clear-project` are mutually exclusive. Neither changes the project.
The project is checked when the `project` field is given and is not `null`, also for a request
with identical values. Omitting the project keeps the assignment, also when the project was deleted;
before submit it must be fixed or explicitly cleared.

### Submit, Reject, Reopen — deployed in PT-219

All three operations accept only `{"expectedVersion":N}`. The version must be a positive
integer. Unknown and repeated fields, a bool, a fraction, and a missing version give `bad-request`.
The same version check and the `version-conflict` response with `currentVersion` apply as for update.
The version condition is checked before the state transition; every effective operation increases `version`
once and updates `updated`, keeping `id`, `created`, the title, the project, the report, and the manifest.

| Operation | Input state | State after success |
| --- | --- | --- |
| `Submit` | `draft` | `ready` |
| `Reject` | `draft`, `ready` | `rejected` |
| `Reopen` | `rejected` | `draft` |

Other transitions give `bad-request` without writing. A repeated submit on `ready` is neither a new change
nor a retry with a key; it returns a refusal. Accepting an issue is handled by AcceptIssue. `accepted` and `archived`
refuse all the operations above.

Submit rechecks the title, the UTF-8 content, and the existing manifest. The report must not be empty or
made up solely of whitespace. The project, if assigned, must still exist. The manifest has the
form `{"version":1,"attachments":[...]}` (an integer, no bool or repeated fields).
Each item must have a safe path, an existing regular copy, and a matching size and SHA-256.
A missing copy, a link, a disallowed path, a corrupted manifest, or a hash gives `broken-file`
without changing the state, version, dates, or report content. An empty manifest remains valid.

The service does not interpret the report content and does not store a list of expected, undelivered evidence.
A description of a missing file alone with an empty manifest does not block an explicit submit. Leaving `draft` after
unavailable evidence is the duty of the PT-224 skill; the user may consciously waive the image,
correct the report, and submit. There is no additional field or operation for resolving gaps.

```text
tide issue submit S-N --expected-version N [--json]
tide issue reject S-N --expected-version N [--json]
tide issue reopen S-N --expected-version N [--json]
```

### AcceptIssue — accepting into the Backlog (PT-221)

The capability `issue.accept` provides a shared operation for the **Do Backlogu** (To Backlog) button and the CLI:

```text
tide backlog S-N --expected-version N [--request-id klucz] [--json]
```

The API accepts exactly `{"expectedVersion":N,"destination":"backlog","requestId":"klucz"}`.
The version is a positive integer, the key follows the `requestId` rules from Create. A missing,
repeated, or unknown field is an error. The assignment `destination: go` is described below.
The CLI requires an explicit version; it does not fetch it through Show. Without a given key it generates a new UUID.
After a lost response it is best to retry the explicit key and identical data. Merely re-accepting
the same version of the issue, also with a different key, returns the same card.

The button passes the version of the displayed report; unsaved changes and a conflict block the action.
A retry after a transport error uses the same key for the lifetime of the window. A new read does not
replace the version of the decision being sent. The server, under a shared lock, checks the version, `ready`, the project,
and the evidence copies. An old version gives `version-conflict`, without reserving K-N. Draft/rejected,
an unknown or deleted project, and inconsistent evidence do not create a card. A missing project is allowed.

Success creates one card in the Backlog with the title, the project, and a copy of the report as the description. The issue
moves to `accepted`, gets the card number and version N+1. `acceptedVersion` and the card's protected
`sourceIssueVersion` point to **N from before the transition**. A repeat returns `alreadyAccepted:true`.
A different payload of the same key gives `conflict`; the scope of AcceptIssue keys covers the whole Board,
separately from the Create keys. Version N+1 does not replace N when retrying the decision.

The card snapshot contains `sourceIssue`, `sourceIssueVersion`, `sourceIssueHash`, `sourceIssueReport`,
and `sourceIssueManifest`. CreateCard/UpdateCard reject these fields; ordinary cards have an empty
link. They are not specification fields. The immutable report with the metadata of the ready version and the manifest
go to `issues/S-N/accepted/report.md` and `accepted/attachments.json`. The hash is SHA-256
of the UTF-8 bytes of the report alone, a NUL byte, and the exact bytes of the manifest. The manifest entries
point to the copies relative to the `issues/S-N` directory, kept after acceptance. Changing the card description
does not change the report or the hash. The link "Raport i dowody S-N · wersja N" (Report and evidence S-N · version N) in the panel and the full card
opens the issue with the accepted report and the public evidence read. The issue points to the card.
Accepted refuses edits, submit/reject/reopen, and attach/detach.

A durable intent `.git/tide-acceptances/S-N.json` precedes reserving K-N and all publication
writes; after completion it is renamed to `S-N.done.json`, so that a refresh does not load
the reports of finished acceptances. We record the operation keys separately in `.git/tide-accept-requests/<sha256>.json`.
The intent contains the ready version, the resulting card, the report and manifest content, and the publication stages.
Every write and directory creation is synced by fsync. On a restart the first Board read
or write restores unfinished intents under the shared lock `.git/tide-board.lock`.
A restore failure gives a read error, without showing a partial acceptance as complete.
The reservation keeps the number, and an existing card is not rewritten during restore
or replay. After publication completes, the commit records the history; its failure does not roll back accepted,
returns as `commitError`, and does not reserve a new number on retry. The intents and keys
are local state in `.git` and require its backup, like the Create registry.

### Go and an explicit start retry (PT-222)

```text
tide go S-N --expected-version N [--launch ustawienia.json] [--request-id klucz] --json
tide issue retry-start S-N --intent identyfikator --expected-start-version N --request-id nowy-klucz --json
```

The capabilities `issue.go`, `issue.retry-start`, and `issue.preview-launch` are available with the
flow engine enabled. Go calls AcceptIssue with `destination: go`, `expectedVersion`, `requestId`,
and an optional `launch` object with the StartCard fields. Without launch it picks Light planning,
the last executor, provider, and the project's assignment check; on the first assignment it uses
the project's tool and directory. StartCard validation pins the full ref and SHA before acceptance,
without a fallback to HEAD. The entries are `now`, `planning` (`light`/`full`), and `import` with `planPath`.
The refusal of interactive Claude with evidence, the I-have-a-plan validation, limits, and queues remain shared.
Invalid settings do not create a card. An explicit `launch: null` is an error.

The go response separates `accepted`, `card`, `started`, `queued`, `startError`, and the projection:

```json
{"intentId":"…","startVersion":3,"state":"start-error","retryable":true,"orderId":"…","attemptId":"…","error":{"code":"…","message":"…"}}
```

`accepted: true` stays true even with a later start error. The issue stays
accepted and points to the same K-N. The start has its own counter `startVersion`, independent of the report
version. The states are `prepared`, `dispatching`, `queued`, `started`, `completed`, `start-error`,
and `reconciling`. Every projection change increases startVersion. `startError` is an error
object or null; the CLI returns **5** when a startError is recorded.

An identical key and payload restore the immutable result of the operation, also the original startError
after a successful retry, after a restart, and after completed. A different payload of this key gives conflict. A replay
does not start work. The current start comes from a separate Show; the CLI adds it as `currentStart`.
The view shows separately the result of the recorded operation and the current state. A historical error does not enable retry.

RetryIssueStart accepts **only** `{intentId,expectedStartVersion,requestId}`. It does not accept
the report version or new settings and does not call AcceptIssue again. It requires a separate decision,
the current startVersion, accepted linked to K-N, the start-error state with retryable=true,
a matching card generation and the recorded base. A `start-conflict` refusal contains the current `start`
and gives CLI code 4. Two decisions on the same version have one winner. Retry keeps
the intent, K-N, launch, and orderId, creating a new durable attemptId. The retry key is separate
from the original go; its identical repetition also restores the recorded response.

Acceptance persists the startIntent together with S-N/K-N. The current state goes to
`.git/tide-issue-starts/`, operation responses to `.git/tide-issue-start-results/`; the `.git`
backup covers them. Before handover the journal persists orderId and attemptId. `card.started.orderId`
ties the existing ticket reservation, branches, and processes to this assignment. Recovery after this
event is continued by the ordinary card engine, without a second `card.started`. Only a proven
refusal before StartCard acceptance gives retryable=true. An ambiguous write or a truncated journal
line means reconciling, retryable=false, and a refusal of a new handover. An error after acceptance
may have start-error with retryable=false; it is settled by the existing recovery of the accepted assignment.
We do not delete or rewrite history. Changing launch requires a new decision outside retry;
repeated go does not change the settings of an already recorded intent.

PreviewIssueLaunch accepts `{expectedVersion,text?}`. It returns the data of the existing assignment
window and a list of locally known bases, without acceptance, reserving a number, or a branch.
The confirmation "Zleć…" (Assign…) sends exactly the version remembered when the window was opened. A new version
causes a conflict. Closing/Esc does not accept the issue. "Ponów start" (Retry start) opens a separate
confirmation and uses only the intent and startVersion from the current Show.

### Attach and Detach — deployed in PT-228 (D2/D5)

`Attach(s id, s request, ay data)` accepts JSON **only**
`{"name":"dowod.png","expectedVersion":N}` and the file bytes as a separate `ay` argument.
`name` is the display name, 1–255 characters, without `/`, `\\`, `:`, control characters, and invalid
Unicode; `.` and `..` are forbidden. The service does not open the client's source. The CLI opens the explicit `--file`,
rejecting a URL, traversal, symlinks in any path component, directories, devices, and FIFOs.
Before sending it compares the source metadata and the hash of two reads, refusing after it changes
or is replaced. Removing the source after a completed import does not affect the issue.

`Detach(s id, s request)` accepts **only** `{"attachmentId":"A-…","expectedVersion":N}`.
It removes the item from the current manifest; it does not remove the source. An absent identifier gives `not-found`.
An attachment of another issue or a detached attachment is not available through a read of this issue.

Both mutations require a positive integer `expectedVersion`, without a bool; unsupported
and repeated fields are an error. On a conflict they return `version-conflict` with `currentVersion`.
Success increases `version` exactly once and sets `draft`, also when the input state is
`ready`. `rejected` requires `Reopen`, and `accepted` and `archived` refuse mutations. Parallel
update/attach/detach of the same version have at most one winner.

The limits before publishing a version: **20 active files**, **20 MiB (20971520 bytes) per file**,
**100 MiB (104857600 bytes) in total**. Equality is allowed, exceeding gives `bad-request`.
An empty file is allowed. We recognize PNG/JPEG/WebP by signature or require them with such an
extension; a mismatch of name and content and an incomplete/corrupted image give a refusal.
The real Qt decoder checks the image, and the size **up to 40000000 pixels** is checked before
decompression. The decoder allocation limit takes into account an RGB(A) image at this limit and 16-bit images.
Working Qt decoders for PNG/JPEG/WebP are required; missing format support means an import refusal.
Animated images are rejected. SVG/HTML can be kept as ordinary evidence: they are never executed
or decoded as a preview. A GUI receiver may use a preview only for the types
`image/png`, `image/jpeg`, `image/webp`; it should not open paths or `file://` from the manifest.

An example `issue.attachments` item (the list is the manifest returned by `Show`):

```json
{"id":"A-0123456789abcdef0123456789abcdef","name":"dowod.png","path":"attachments/A-0123456789abcdef0123456789abcdef","type":"image/png","size":1234,"sha256":"<64 małe cyfry szesnastkowe>"}
```

`id` is `A-` and 32 lowercase hex digits, generated by the service. Equal names do not merge files:
each import gets a separate ID and copy. `path` is exactly `attachments/<id>`, relative to the issue
directory. It contains neither the name nor the location of the source. `type` is the MIME determined by the service, `size` is the
number of bytes, and `sha256` is the hash of exactly the imported bytes. Unknown fields are not allowed.

### ReadAttachment — the public contract for the PT-220 receiver (D4)

```text
ReadAttachment(s id, s request) → (s result, ay data)
request = {"attachmentId":"A-0123456789abcdef0123456789abcdef","expectedVersion":3}
```

The field list is closed: **only** `attachmentId` and `expectedVersion`. The ID and version rules are
the same as for detach. The method does not accept a path, URL, file type, or target name.
The success response `result` is JSON:

```json
{"ok":true,"apiVersion":1,"id":"S-1","version":3,"attachment":{"id":"A-0123456789abcdef0123456789abcdef","name":"dowod.png","path":"attachments/A-0123456789abcdef0123456789abcdef","type":"image/png","size":1234,"sha256":"<64 małe cyfry szesnastkowe>"}}
```

`data` is the original bytes, at most 20 MiB, **without base64**. On every error `result` uses
the shared JSON envelope, and `data` is **empty** (`ay`, also on a version error, a disabled service,
and a corrupted copy). This is not a D-Bus transport error. An example conflict:

```json
{"ok":false,"apiVersion":1,"error":"version-conflict","message":"Wersja sprawy uległa zmianie.","currentVersion":4}
```

Under the same lock as the mutations the service reads the report version and the manifest, compares the version,
looks up the ID, checks the realpath inside the issue, opens each component without links, and verifies
the size and hash of the whole copy. Only then does it return the metadata and bytes. This is one consistent read
boundary; a conflict and a foreign ID return no fragment of the file. `Show` checks the structure of the
manifest; checking the bytes of the copy is done by `ReadAttachment` and `Submit`.

```text
tide issue attach S-N --file <plik> --expected-version N [--json]
tide issue detach S-N --attachment <id> --expected-version N [--json]
tide issue attachment S-N --attachment <id> --output <nowy-plik> [--expected-version N] [--json]
```

Without `--expected-version` the `attachment` command fetches the current version through `Show`, and then
sends it to `ReadAttachment`. A parallel change gives a conflict; the CLI does not retry the read with the new
version on its own. Before writing it checks the hash and size of the response. It creates the target through
`O_EXCL|O_NOFOLLOW` with 0600 permissions: an existing file and a link give `io-error` (code 7),
without overwriting. The bytes are not executed and do not get the execute permission. A write error removes
the incomplete file created by this command. An API error does not create the target.

## Files and durability

An old Board requires no migration. On the first write the service adds:

```text
issues/S-1/report.md
issues/S-1/attachments.json
```

The `report.md` frontmatter is flat: the values are JSON scalars (valid also in YAML),
the keys are exactly `tide-issue: 1`, `id`, `title`, `project` (text/null), `state` (initially
`"draft"`), `version` (initially 1), `created`, `updated` (ISO 8601 UTC), `card` (initially null).
After the closing `---` line is exactly the report content. The initial manifest is
`{"version":1,"attachments":[]}`. The first attachment mutation adds an optional scalar
frontmatter key `attachmentManifest: "manifests/<sha256>.json"`; from then on this file
is the current manifest instead of the initial `attachments.json`. The SHA-256 in the name must
match the manifest bytes. The manifest's `version: 1` means the format, not the issue version.

```text
issues/S-1/attachments/A-<32 cyfry hex>
issues/S-1/manifests/<sha256>.json
```

Attach first persists the full copy (`fsync` of the file, rename, `fsync` of the directory), then
the immutable manifest in the same way, and only at the end atomically replaces `report.md`
with the new pointer and version. Detach publishes a new manifest without the detached item. A failure
leaves the old full version or the new full version; the source is not needed for recovery.
A failure after the publication point may leave a successfully recorded new version despite an error
response: after recovery use `Show` instead of retrying the mutation with the old version.
Historical and orphaned copies, manifests, and `.tide-tmp-*` files have no public read
by ID if they do not appear in the current manifest. We do not delete them automatically; the limits
of 100 MiB/20 files concern the active manifest, not the whole git history and the kept copies.

Update and state transitions use the same lock `.git/tide-issues.lock` as create. The read,
the comparison of `expectedVersion`, and the atomic replacement of `report.md` happen under the lock. The report and the whole
version metadata are one file, synced by fsync and published by rename;
the manifest stays unchanged. A parallel mutation that loses writes no field.
The cycle does not reserve the next S-N, K-N, or PT-N. A create replay with `requestId` still returns the original
response, but does not roll back later changes on disk; the current version is read through Show.

The `issue` object has the metadata fields above except `tide-issue` and the internal `attachmentManifest`,
and in addition `report`, `attachments:[...]`, and `paths:{report:"issues/S-N/report.md",manifest:"…"}`.
`paths.manifest` points to the current manifest (initial or immutable). These are standalone
paths relative to the Board, independent of the project and the card; the API is not for reading an arbitrary path.
A read rejects links, special files, excessive size, inconsistent metadata, and bad UTF-8.
`List` returns valid issues and separate errors of damaged issues; `Show` of a damaged issue
gives `broken-file`, not blocking the others.

The S-N numbers are separate from K-N and PT-N. The local registry `.git/tide-issues.json`, protected by `flock`
(`.git/tide-issues.lock`), contains the counter (`format: 2`, `next`). The durable `requestId` confirmations
are separate files `.git/tide-issue-requests/<sha256-klucza>.json`, containing the payload digest
(`fingerprint`), the original result (`issue`), and the publication marker (`published`). Reading a single
key does not load the whole history. The confirmation file limit takes into account the maximum report after
JSON escaping; the write checks the same boundary as the read, without a limit on the total size of the history.
The counter is
written atomically and synced to disk before publication. Deleting an issue or a revert does not roll it back. A missing registry causes the counter to be rebuilt from files, git history, and kept confirmations;
a corrupted registry suspends create. The registry and confirmations should not be deleted: they are the local state
of the service and must be covered by a `.git` backup if they need to be moved to another computer.

On the first create the old format 1 registry is split under the same lock, also
when it exceeded the old 128 MiB limit. Only after all confirmations are persisted does the service
atomically replace it with the format 2 counter. An interruption leaves the full format 1 registry;
a retry safely repeats the migration, also keeping unfinished intents. This one-off
read of the old registry requires memory proportional to its size; further creates read
only the counter and the confirmation of the given key.

An intent with a key contains the original result and is persisted before publishing the whole directory
by rename. After a failure during publication, retrying the same `requestId` completes this
intent with the same number. Without a key an unfinished publication may leave a gap in numbering;
the number does not return. A reservation without publication is not a valid issue. An unfinished intent
is resumed on a create retry, not by merely reading the list.

Only the daemon writes and commits issues. It uses the same `BoardRepo` instance as
the Board: a merge/revert/rebase conflict gives `git-busy` without writing, and a commit error after writing
leaves the issue and the commit queue. `commitError` reports this situation; it does not mean
the mutation failed. The Board synchronization will finish the commit, also after a restart. The CLI does not
open the Board, does not run git, and has no local write path replacing the service.

## Installation and usage

### The Issues view (PT-220)

The bar of the single Tide window contains **Sprawy** (Issues). By default the list shows drafts and issues ready
for acceptance; the filters also provide accepted, rejected, and archived ones. The badge counts `ready`.
The search covers the number, title, and project. `tide issue open S-N` checks the issue through
the public `Show` and calls `org.tide.Window /Window Show("issues", "S-N")` or starts the installed
window with `--view issues --select S-N`. An unknown issue gives an ordinary CLI error without opening.

The view edits only the title, the Markdown content, and the project assignment; on save it sends only the
changed fields and `expectedVersion`. "Bez projektu" (No project) sends `null`. Changing a ready issue returns it to
a draft. A conflict keeps the local text for the lifetime of the window, shows the current version, and blocks
saving until an explicit reload with confirmation. Changing the selection keeps the buffers of visited
issues. These are not recovery files after the window is closed. Submit/reject/reopen use the existing API;
accepting into the Backlog and "Zleć…" (Assign…) use the shared AcceptIssue.

The report goes through the Qt Markdown parser with HTML disabled. Images must point to the exact
path `attachments/A-…` from the manifest of this issue. We do not open it on disk: the public `ReadAttachment`
with the ID and version provides the bytes, which the client checks against the manifest. Only PNG/JPEG/WebP after
decoding go to the controlled provider `image://issue-evidence/` under a random identifier.
Other images, SVG, the network, arbitrary `file://`, and traversal outside the directory have no preview.
HTTP(S) links open only on click. The card document renderer stays unchanged.

Each attachment has a consistency check result. An empty manifest is valid; gaps described in the report
do not create a list of required files. "Otwórz obraz" (Open image) creates a private, re-encoded PNG copy;
other evidence opens explicitly **as text**, under the fixed extension `.txt` (binaries may
be unreadable). Neither HTML/SVG nor the evidence name selects a program executing its content.
The preview `--data issues=plik.json` performs no mutations, no disk reads of evidence, and no system openings.

The view package copies the standard `widgets/stage.py`; the Tide window installation covers it.
The system Python additionally needs the module **PyQt6.QtQuick** (`python3-pyqt6.qtquick`)
besides the already used QtQml, QtGui, and QtDBus. The `issues` suite now also covers
`apps/issues/tests`: QML on test data and the backend and CLI with a real service on a private D-Bus.

### CLI

The standard `make install` installation includes the CLI (with the existing backup before installation).
The `install-cli` component alone copies `cli/tide.py` with the execute permission to `~/.local/bin/tide`;
`uninstall-cli` removes this copy. The system Python 3 and PyQt6 QtDBus are needed, as for the service.
It requires neither a checkout at run time nor `PYTHONPATH`. The Board repository is chosen by the service,
not the client. Do not run the installation on a real HOME during a test.

```text
tide info --json
tide issue create --report raport.md --title "Opis rozmowy" --request-id rozmowa-1 --json
tide issue create --report raport.md --title "Opis projektu" --project plasma-tide --json
tide issue list --json
tide issue show S-1 --json
tide issue update S-1 --expected-version 1 --title "Poprawiony tytuł" --project plasma-tide --json
tide issue submit S-1 --expected-version 2 --json
tide issue reject S-1 --expected-version 3 --json
tide issue reopen S-1 --expected-version 4 --json
tide issue update S-1 --expected-version 5 --clear-project --json
```

In `--json` mode stdout contains a single JSON object, also for an error; in ordinary mode
an error goes to stderr in Polish. `show --json` returns the content in `issue.report`, without frontmatter.
The exit codes:

| Code | Meaning / API codes |
| --- | --- |
| 0 | success, also a write awaiting a commit and a partial list with `errors` |
| 1 | an unexpected service error (`internal`, other unknown codes) |
| 2 | arguments or data (`bad-request`), for example "Raport przekracza 64 KiB." (The report exceeds 64 KiB.) |
| 3 | no service/bus, issues disabled, or an incompatible API (`unavailable`, `api-incompatible`) |
| 4 | a key conflict (`conflict`) or a version conflict (`version-conflict`, with `currentVersion`) |
| 5 | no such issue (`not-found`), for example "Nie ma sprawy S-99." (There is no issue S-99.) |
| 6 | the repository is busy with an operation with conflicts (`git-busy`) |
| 7 | a read/write error or a damaged issue (`io-error`, `broken-file`) |

The `issues` suite in the `tools/test_suites.py` registry covers the store and public boundary tests,
the installation in a temporary HOME/XDG, and the process of the installed CLI with a real daemon on a
private D-Bus. It also verifies restart, concurrency, old cards, an incompatible API, and a missing service.
The `test_issues_attachments*` tests additionally cover identical names, removing sources,
reading bytes after a restart, the limit boundaries, real decoders, submit consistency, and refusal of
unsafe sources/targets. The test service process forces ENOSPC and interruptions at the boundaries of
the copy/manifest/publication; no failure switch is available in the production API.
