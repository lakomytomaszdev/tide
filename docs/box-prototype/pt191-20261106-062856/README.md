# PT-191: interrupted third trial

Command: `python3 scripts/box/boundary-trial.py --enable-snapshots --retry-failed-build`.
Result passed on by Tide: timeout exceeded, `Terminated`.

Last events of the assignment:

```text
PRÓBA snapshots-before: ['incus', 'project', 'get', 'user-1000', 'restricted.snapshots']
WYNIK snapshots-before: 0
PRÓBA snapshots-enable: ['sudo', '-A', 'incus', 'project', 'set', 'user-1000', 'restricted.snapshots=allow']
Terminated
```

The saved files contain only the read from before the change: an empty property value and no stderr.
There is no result of the command that changes the policy and no read after the change. We do not confirm the state after the interruption.
The build, the full tests, the environment/argv evidence and the red list were not carried out in this round.
