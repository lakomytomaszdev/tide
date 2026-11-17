# Maintenance of test journal copies (PT-252 → PT-255)

The tool `tools/cardflow_cleanup.py` does not read the user's settings or default directories.
It requires `--source`: a directory of **copies** of `cardflow`, containing directories of
12-character board hashes, and in them `K-<numer>.jsonl` files. Do not point to the Board repository,
`flow`, the whole state directory, or worktrees.

PT-252 delivers a tool verified on synthetic data. The real execution
belongs to PT-255 (#12), after the review and merge of PT-251, PT-252, and PT-254 into the card branch.
Delivering the commands below does not mean that the K-60 cleanup was performed.

## Qualification

Each file is qualified separately. Accepted are regular files without hard
or symbolic links, in a recognized path layout. Every line must contain
a valid JSON object with schema `v: 1`, consecutive `seq` from 1, a matching `card`,
a non-empty `type`, and a `ts` date with a time zone. An unknown schema, a truncated or empty
line, a repeated key, and an invalid JSON number rule out deleting the whole file.

At least one `step.started` is required. **All** starts must have no
`repo` or exactly an empty string, and an absolute `worktree` whose `realpath`
is a descendant of `/tmp/`. `/tmp` itself, a relative path, `null`, whitespace in `repo`,
an escape through `..`, or a link outside `/tmp` mean skipping. The number
of historical copies is not checked. One test start does not qualify other files or
the whole board hash. New files after the preview are not automatically added.

A missing historical worktree directory is allowed. Path resolution tolerates
only a missing file (`realpath` with `strict=os.path.ALLOW_MISSING`, required also
in the interpreter running the tool). A symlink loop, denied access, and a component
that is not a directory mean `unknown-worktree` and keeping the copy. A link to
a nonexistent target is still resolved, so a target outside `/tmp` rules out deletion.

## A window without writes

The preview changes nothing unless `--plan` is explicitly given: then it creates only
a new metadata file outside the source. The apply step rechecks the qualification,
the SHA-256, and the file identity (device, inode, mode, link count, size, mtime,
ctime). Changed or missing candidates are skipped.
If a regular file kept in the source cannot be read and its checksum computed,
the apply step refuses before deleting: immutability could not be demonstrated.

The `--writers-stopped` flag alone **does not stop processes**. It is a confirmation of the
operator's duty: throughout the apply, the check, and the repeated run no one
may append to or replace the copies. The `Journal.lock` lock is inside the service
process and does not synchronize this tool. Additional reads and checks do not replace
stopping the writing processes; the tool does not guarantee protection against a process
that deliberately modifies data between the last check and `unlink`.

In PT-255 use `tools/cardflow_cleanup_host.py`. It requires the service `tide-daemon.service`
to be initially active, stops it, checks `ActiveState=inactive`, performs the
apply, the immutability check, a new preview, and a repeated apply with no
candidates. In `finally` it resumes the service and checks `ActiveState=active`, also after
a backup, check, or repeated run error. A non-zero result means the reports must be
checked; it is not a confirmation that the cleanup finished.

Before this command, finish old unprotected tests, close the Tide clients,
and rule out automatic resumption of the service and other processes writing the copies. This is confirmed
by `--other-writers-stopped`. Do not run clients or old tests in this window.
The procedure does not stop arbitrary user processes or change the systemd
configuration. Do not use it if these conditions cannot be ensured. `finally` does not
work after SIGKILL, a power loss, or an interpreter crash; then the operator must
resume the service. Ordinary command errors have a resume path.

## Single commands for PT-255

These are concrete examples for the default state of the user `tomek`. PT-255 must first
confirm, using **configuration metadata**, the real state directory (in particular
`XDG_STATE_HOME`) and the path of the accepted tool in its worktree. If they differ,
insert the appropriate absolute paths. The model does not read the contents of the real
journals. The tool is run outside the sandbox, as a separate `outside` result.

The directory `/home/user/DevWorkspace/tide-maintenance-k60` is an explicit artifact
directory, **outside** `/home/user/.local/state/tide/cardflow`. It must already exist,
be private to the user, and not contain the file names given below or a backup.
Creating it is a separate host command:

```sh
/usr/bin/mkdir -m 700 /home/user/DevWorkspace/tide-maintenance-k60
```

1. The default preview with an explicit save of the verified set (the first `outside`):

```sh
/usr/bin/python3 tools/cardflow_cleanup.py --source /home/user/.local/state/tide/cardflow --plan /home/user/DevWorkspace/tide-maintenance-k60/preview.json
```

After resuming, PT-255 checks the exit code and the counters. Only then does it hand over
the second `outside` command, separately from the preview:

```sh
/usr/bin/python3 tools/cardflow_cleanup_host.py --source /home/user/.local/state/tide/cardflow --plan /home/user/DevWorkspace/tide-maintenance-k60/preview.json --backup-dir /home/user/DevWorkspace/tide-maintenance-k60/backup --report /home/user/DevWorkspace/tide-maintenance-k60/apply.json --repeat-plan /home/user/DevWorkspace/tide-maintenance-k60/repeat-preview.json --repeat-backup-dir /home/user/DevWorkspace/tide-maintenance-k60/repeat-backup --repeat-report /home/user/DevWorkspace/tide-maintenance-k60/repeat-apply.json --other-writers-stopped
```

This command includes the check and the repeated run in one stopped window. If
the new preview finds candidates (for example changed since the first preview), the procedure
refuses further deletion and resumes the service; a new preview and evaluation are needed.
It does not delete new data just to get zero.

For an operator maintaining the stopped window on their own, the same actions are
available separately. Each line below is one command:

```sh
/usr/bin/python3 tools/cardflow_cleanup.py --source /home/user/.local/state/tide/cardflow --apply --plan /home/user/DevWorkspace/tide-maintenance-k60/preview.json --backup-dir /home/user/DevWorkspace/tide-maintenance-k60/backup --report /home/user/DevWorkspace/tide-maintenance-k60/apply.json --writers-stopped
/usr/bin/python3 tools/cardflow_cleanup.py --verify /home/user/DevWorkspace/tide-maintenance-k60/apply.json
/usr/bin/python3 tools/cardflow_cleanup.py --source /home/user/.local/state/tide/cardflow --plan /home/user/DevWorkspace/tide-maintenance-k60/repeat-preview.json
/usr/bin/python3 tools/cardflow_cleanup.py --source /home/user/.local/state/tide/cardflow --apply --plan /home/user/DevWorkspace/tide-maintenance-k60/repeat-preview.json --backup-dir /home/user/DevWorkspace/tide-maintenance-k60/repeat-backup --report /home/user/DevWorkspace/tide-maintenance-k60/repeat-apply.json --writers-stopped
```

`--verify` after resuming the legitimate service may show its normal changes. The proof
of immutability comes from the check **before** resuming in the host procedure, not
from a later comparison. Code 0 means success, 1 an error or refusal, 2 an argument error.
With zero candidates a report is created, but no fictitious backup.

## Backup, report, and recovery

The backup is created under a new name, with private permissions. In `files/` it keeps
the relative paths of the copies. `manifest.json` contains their paths, identities, and SHA-256.
Files and directories are synced to disk; the whole manifest and all checksums are
read again before the first deletion. A failure while creating or verifying
the copy leaves all originals and the partial backup for diagnosis. Do not resume
the operation by overwriting such a backup — use new names after assessing the error.

The report records the number of qualified files, the list and number of deleted/skipped,
the skip reasons, the absolute backup path, the result of its verification, and the check of the set of
paths and checksums of the kept copies. It is opened before the backup and updated after
the deletions. It contains only metadata; the journal content is only in the private
backup. Do not publish the backup or the journal content. The preview's stdout contains only
counters; JSON errors do not print fragments of the input data.

Only a specific candidate is deleted, then its hash directory if it is empty.
There is no recursive deletion. The backup does not lie in the active tree and is not a source
of future statistics. The Board repository, worktrees, and other directories are outside the operation.

Recovery is performed by the operator in the stopped window: first checking the manifest
and the checksum of the chosen file in `backup/files/<skrót>/K-<numer>.jsonl`, then restoring
**only the missing** file at the same relative path of the active copies. It creates
the missing hash directory with mode 0700 and the file with mode 0600, without overwriting an existing
copy and without following links. It compares the SHA-256 of the restored file with the manifest,
and then resumes the service also after an error. A synthetic test restores a deleted file
and shows identical content; recovering the user's data itself requires a separate
controlled host operation. Do not copy the backup into the Board repository.
