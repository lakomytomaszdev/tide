# Box client v1 (PT-267)

Preserved entry point: `python3 daemon/tide.py box status <project> [--card K-N] [--json]`
and `python3 daemon/tide.py box stop <project> [--card K-N]`.
The daemon installer also copies `tide.py` next to `tide-daemon.py`; the installed client works without the repository.
Unifying the `tide` command remains in K-75.

The client requires a running `org.tide.Daemon` service on the host's session D-Bus and PyQt6.QtDBus.
State comes from `/Board BoxStatus(slug)` or `BoxCardStatus(slug, card)`, and stopping from
`StopBox(slug, card)`. All responses are JSON objects with `ok`; errors have `message` and `error`.
Exit code: 0 success, 1 transport/service error, 2 arguments, 3 refusal or unusable box state.
The service writes `box.stop` and the operation time. A missing bus/service ends the command without a local change.

The service checks that the card belongs and the instance ownership, and then applies the existing check of
live steps (including units of unknown state after a restart). The check and the stop hold the same
lifecycle lock as the step reservations. Unknown ownership means refusal.
The client in the box does not receive the host bus: the runner does not pass its address or the session socket.
The session D-Bus address in the step environment points to a nonexistent socket, which rules out an automatic bus start.

Until stages II/III the commands `base/build/reset/start/destroy/approve/login/run` and the former
`python3 -m tide_daemon.box.runner --demo …` are disabled with a message. The module form
`python3 -m tide_daemon.box.cli …` uses the same client, without direct Incus.

The standalone demo and staging are checked by `daemon.tests.test_box_cli_service.ServiceDemoTest`:
its own Board and an Incus dummy, a private D-Bus bus, preserved `daemon/tide.py`, allowed stop,
refusal for a reservation and a foreign instance, installed client and alternative entry points, missing service.
The trial does not use the shared Git repository or the resources of a running session.

## Batch 0: manual offline console (instructions for the owner)

This is solely the owner's manual offline console in the running `plasma-tide` container. It is not
`OpenBoxConsole`, an agent login or a console with an agent — v1 does not provide those. Ticket PT-292 does not perform
this trial; the description is instructions for going through it yourself.

**Entry condition:** the `plasma-tide` container has state `RUNNING` (`incus list plasma-tide`), the card is
paused (pause in the card panel, `SetCardPaused`), and the full card view, the journal and the `tide-*` units show
zero steps, zero outside commands and zero open consoles. Unknown state = we do not enter.

**Entry** (TTY `-t`, uid/gid 1000, `HOME=/home/agent`; `<worktree>` is the card's worktree path):

```bash
incus exec plasma-tide -t --user 1000 --group 1000 --env HOME=/home/agent --cwd <worktree> -- bash -l
```

**Four checks in the console:**

1. `id` shows `uid=1000(agent)`. If you see `uid=0(root)` or another user — **stop**: `exit`
   and nothing more in this console.
2. Trial file: `git status --porcelain > /tmp/pt0-status-before.txt` (snapshot to `/tmp` in the box, never to the
   worktree), then `touch pt0-probe.txt`. On the host `stat -c %U <worktree>/pt0-probe.txt` shows `tomek`
   (the host owner, not root or a high uid).
3. In the console `rm pt0-probe.txt`, then `git status --porcelain | diff /tmp/pt0-status-before.txt -` shows
   no differences — git status is the same as before the trial.
4. `curl -m5 -I https://example.org` ends with a connection error (no DNS/NAT outside the proxy) — a denial of
   example.org is the expected result; an HTTP response means stop and report.

**Exit:** `exit`. The snapshot `/tmp/pt0-status-before.txt` stays in the box's `/tmp`, the worktree is unchanged.

## `box` settings via SetCardFlowSettings/GetCardFlowSettings (PT-292)

The `box` section of the file `$XDG_CONFIG_HOME/tide/cardflow.json` (`daemon/tide_daemon/box/settings.py`) is written
and read by the existing pair `/Board SetCardFlowSettings(request)` / `GetCardFlowSettings()` of the `org.tide.Daemon` service
(interface `org.tide.Daemon.Board`). Signatures unchanged; there is no separate `daemon/tide.py` command for
settings, so the calls go through `gdbus`, and the box state is still shown by `python3 daemon/tide.py box status`.

Supported keys — the explicit catalog `box/settings.py` `KEYS`: `enabled` (only `true`/`false`),
`proxy-port` (an integer 1024–65535, `true`/`false` are not a number), `bridge`, `project`, `base-image`,
`pool`, `nic`, `agents-version`, `ipv6-filtering`, `ip-wait-seconds`, `idle-hours`, `agent-uid`, `agent-gid`.

- The write is partial: `{"box": {"enabled": false}}` changes only `box.enabled`; the other `box` fields,
  `limitTransfer`, `providerSlots`, thresholds, steward and other keys of the file stay.
- A bad request (`box` not an object/`null`/a list, wrong type or range, unknown key) → `{"ok": false,
  "error": "bad-settings"}` before writing. A mixed request (a correct field next to a wrong one) writes nothing.
  The service does not substitute a default value for a wrong value from the request.
- The Set response and `GetCardFlowSettings` have `box` with the effective values (the same ones the proxy, builder and runner
  read after a restart) and `boxWarnings` — warnings for values of the file that were broken by hand.

Verifiable calls (read, partial write, read back):

```bash
gdbus call --session --dest org.tide.Daemon --object-path /Board \
  --method org.tide.Daemon.Board.GetCardFlowSettings
gdbus call --session --dest org.tide.Daemon --object-path /Board \
  --method org.tide.Daemon.Board.SetCardFlowSettings '{"box": {"enabled": false}}'
gdbus call --session --dest org.tide.Daemon --object-path /Board \
  --method org.tide.Daemon.Board.GetCardFlowSettings
```

Expected: both responses contain `"box": {"enabled": false, "proxy-port": 18080, …}` and unchanged
`limitTransfer`/`providerSlots`. Refusal: `SetCardFlowSettings '{"box": {"enabled": "true"}}'` gives
`"ok": false, "error": "bad-settings"`, and `GetCardFlowSettings` returns the same as before the attempt.
This is checked automatically by `daemon.tests.test_cardflow_box_settings` (private bus and service restart).

### Port contract with nftables and changing the port

The proxy port is a shared contract of three places: `box.proxy-port`, `scripts/box/install.sh --proxy-port`
(`make box-install BOX_PROXY_PORT=…`, the same range 1024–65535) and the static table `inet tide-box`
in `/etc/tide-box/tide-box.nft` (generator `scripts/box/files.sh rules`). The service reads this file
(`box/nftcontract.py`), never runs `sudo` and does not change nftables.

- Changing `box.proxy-port` or enabling the box (`enabled: true`, also with a `bridge` change) passes only
  when the "proxy Tide" rule of the `input` chain of the `inet tide-box` table allows exactly `box.bridge` → port.
  Otherwise `{"ok": false, "error": "box-install-mismatch"}` with a description of the installed rule and the procedure;
  a missing file = contract unconfirmed, the same refusal. The settings file stays unchanged.
- The proxy start with the service additionally checks the bridge address: it listens only on the address and port from the rules,
  otherwise the proxy does not start (journal entry), and steps in the box refuse without the proxy.
- Disabling (`enabled: false`) does not depend on the installation — returning to work on the host is always possible.

Procedure for changing the port (e.g. to 18081) through an approved installation:

1. Pause the cards and confirm zero steps, outside and consoles (as in batch 0).
2. `make box-install BOX_PROXY_PORT=18081` (sudo; the plan shows the diff of the rules file), then
   `grep 'proxy Tide' /etc/tide-box/tide-box.nft` shows `tcp dport 18081`.
3. `gdbus call … SetCardFlowSettings '{"box": {"proxy-port": 18081}}'`, reading back through
   `GetCardFlowSettings` shows `"proxy-port": 18081`.
4. Restart the service (`systemctl --user restart tide-daemon`); only the restart moves the proxy listener.

### A write does not switch running runners; enabling is outside v1

Writing a setting is not an atomic switch of the running service: the proxy reads the port and bridge at start,
and new steps read `box.enabled` at step start. Steps already started keep their runner.
Enabling procedure: pause the project's cards and confirm zero steps, outside and consoles → write
`SetCardFlowSettings '{"box": {"enabled": true}}'` (requires a matching installation) → restart the service →
check the proxy listener (`ss -ltnp 'sport = :18080'` on the bridge address) → only then unblock the cards.
The v1 release stays with `enabled=false`; the activating part (receiving work in the box) is outside v1 and is not
declared here as done, just like the deferred PT-268.
