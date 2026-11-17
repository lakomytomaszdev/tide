# plasma-tide acceptance — PT-176 / K-38

These are prepared trials, not confirmation that they were carried out. Real results are added to the PT-176 note
after each round. Do not replace failures with the result of a test with a dummy.
Do not change the profile, domains or service mechanisms without recording the reason and the ticket number.

## Current limitations

The PT-176 hand-in covers the prepared artifacts. Scenario 4 is deferred to PT-274.
The installation and boundary trials remain blocked by the PT-261 gate. The future procedure requires
adapting to the one-off target from PT-273 and renewed approval by the user.
A separate `outside` round or the PT-170 guard alone do not lift this block.
Do not run the installation, the red list or credential-read trials now.

Validation of the changes handed in: `make test-changed BASE=8c82f1692a494a95be41704c2debf27efc492272`.

## Historical order of rounds — currently not allowed to be run

The commands below are kept as a description of past trials, not as a current instruction to run.
In particular, pointing to a real desktop file in item 7 is historical and not allowed;
before future trials it must be replaced with the procedure for the PT-273 one-off target.

Every command below requires a separate `outside` round, from the PT-176 worktree directory.
Do not run a bare `make install` on the host — it is forbidden in the step.
Before the trials the PT-170 guard of the shared repository applies, also in the running daemon,
described in `docs/tide-daemon.md`, section "Polecenie i czerwona lista" (The command and the red list).

1. Validation of the author's changes: `make test-changed BASE=8c82f1692a494a95be41704c2debf27efc492272`.
   The base comes from the PT-176 ticket sheet. Do not replace it with the current master or the card tip.
2. Consent to the profile: `python3 daemon/tide.py box approve plasma-tide --file docs/box-profiles/plasma-tide.yaml`.
   Keep the `box.approved` event, the hash and the full result. The PT-191 trial profile does not replace this file.
3. Build: `python3 daemon/tide.py box build plasma-tide`. Keep the time and the result of each `verify`.
   CLI code 0 and state `clean` are not enough: the builder may return a warning about a failed verify.
4. State: `python3 daemon/tide.py box status plasma-tide --json`.
5. Scenario 4, tests: `python3 daemon/tide.py box run plasma-tide -- make test`.
6. Scenario 4, QML/fonts/Ollama:
   `python3 daemon/tide.py box run plasma-tide -- python3 scripts/box/plasma-tide-scenarios.py qt-ollama`.
   The script requires fonts and the creation of an offscreen QML object, reads local models through the Tide proxy
   `/api/tags` route and asks the first model for a short answer. It does not download models.
   The optional `--model <name>` selects a model already available locally. The result gives the model name.
7. Scenario 4, installation in the box and host checksums:
   `python3 scripts/box/plasma-tide-host-trial.py install --host-file /home/user/.local/share/plasma/desktoptheme/Tide/metadata.json`.
   Before the trial the indicated file must exist. The script calls only `tide box run ... -- make install`,
   records both SHA-256 sums also on an installation error. Success requires installation code 0 and identical sums.
   Do not create the missing host file in order to pass the trial. A missing Plasma session/tool in the box
   is a failed result, to be accounted for as a specific error, without adding a new feature.
8. Scenario 5: `python3 scripts/box/plasma-tide-host-trial.py long-command`.
   One `sh -c` argument is over 300 characters long, contains `find -exec cat {} \;` and goes through CLI/BoxRunner.
   Success requires the exact output `PT-176-find-ok`. The length is printed before the trial.
9. Red list: `make box-check BOX=plasma-tide`.
   Keep the table in full, with the skips. Complete the IP trial with `CHECK_ARGS` per PT-191,
   after reading the addresses and checking a free address; do not guess the IP. Check there is no entry in the proxy
   from the spoofing address. Code 0 does not pass the skipped items.

## Test card and scenario 6

In the panel create a separate plasma-tide test card with the `plan.md` proposal, the `tickets/1.md` sheet
and the `box.yaml` profile next to `plan.md`. This is a user operation; the files in this directory do not mean the card was created.
Tide assigns the real card and ticket IDs. Approve the plan together with the package after viewing the scanner report
and a non-empty file list. No `accepted_red` is pre-authorized in the proposal.

Before the import copy this whole directory to the proposal directory, keeping the names `plan.md`,
`box.yaml` and `tickets/1.md`. Use the import path of an existing plan (it handles a missing `card`);
check the executor catalog for the availability of the Sonnet / Opus assignment or choose the available models.
The importer rejects a package without `accepted_red`: only after the user's explicit consent to the preview of
`session-logs` add in the copy of `box.yaml` the field `accepted_red: [pack:session-logs]` and pass the
proposal to the import and the plan approval. Without this consent do not import the profile with the package.
This is an instruction for future preparation; in PT-176 no consent was given and no card was created.

After consent, in subsequent separate rounds (replace `K-N` with the real ID):

- `python3 daemon/tide.py box run plasma-tide --card K-N -- ls /packs`
- `python3 daemon/tide.py box run plasma-tide --card K-N -- python3 scripts/box/plasma-tide-scenarios.py pack`

The `pack` trial requires a non-empty package and the `ro` option of its mount. The real ticket from the sheet must end
`done`. Keep its note and journal: count `step.decision` with `envAnswer` and the `outside`/`denied`
results about the environment — each counter must be 0. The PT-176 preparation rounds are separate
operations, not steps of this test card. List the denied domains from its `proxy-denied.log` and for each
decide "add to the template", "add to the profile" or "do not add", with a justification.

After the card is closed check the `packs.removed` event and that the copy directory computed by
`tide_daemon.box.packs.root("plasma-tide", "K-N")` does not exist. Do not delete it by hand to pass.

A historical, currently not allowed trial: `cat ~/.claude/.credentials.json` with the Bash tool of a real agent.
The PT-261 gate and the PT-273 one-off target apply; do not run it now.
Do not copy the credential content into the note; keep only the result of the read. A plain `box run` shell
does not prove P-2 works. With a non-working bwrap or Docker without K3 record
"residual K1 risk", never "denial". Every "not applicable" requires supplementary evidence.

## Result note

The step note covers the consent/build/verify state and time, scenarios 4–6 (also failed ones), both host
sums, the whole red-list table and the P-2/K3 limitations, the test card/ticket ID, the journal counters,
the domains with decisions, the package cleanup and the list of service code fixes with ticket numbers.
Waiting for consent, a test or a card must stay a wait, not a passed criterion.
