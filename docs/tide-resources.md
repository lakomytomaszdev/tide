# Resources of the installed Tide

`make install` makes a backup and then installs the service, the CLI, and the Tide resources.
`tide info --json` returns `resources` over the public D-Bus: for the plan standard,
the number reservation tool, and the plan checks these are `path`, `version`, `available`,
and the SHA-256 of the available content. The response contains no skill instructions, settings, or secrets.
The existing `apiVersion` and the issue API capabilities remain available even when resources are incomplete.

Tide files live in `$XDG_DATA_HOME/tide/resources` (by default `~/.local/share/tide/resources`):
`docs/agent-plan-standard.md`, `tools/next_id.py`, `tools/check_plans.py`.
The CLI runs the tools from the location returned by the service, passing arguments without a shell:

```sh
tide info --json
tide next-id "/ścieżka ze spacjami/projekt" --title "Opis ticketu" --dry-run
tide check-plans "/katalog projektów" --no-youtrack --json
```

The tool entries in `info` also contain `pythonPath`: the module directory of the running service.
The client passes it as `PYTHONPATH` of the tool process, regardless of the resources location.
The tools require the service modules to be installed; the client itself still requires the public D-Bus
and `python3-pyqt6.qtdbus`. You do not need the author's checkout. In a checkout the resolver uses
its own `docs/` and `tools/`; running a standalone copy of the service picks the neighboring `resources`
directory. You can point to your own bundle with `TIDE_RESOURCES_DIR` in the service environment
(a directory containing `docs/` and `tools/`). An explicit setting has no silent fallback.
A missing standard or tool in a step gives a message with the resource name and how to configure it;
`info` shows `available: false` to allow diagnosis.

## Methodologies and templates

Tide distributes its own standard and tools. It does not copy external methodologies.
The K-59 mechanism delivers Claude a plugin with references to the original SKILL.md files,
and Codex a catalog and an instruction for direct reading. Both programs read resources
relative to the original skill directory. The instructions must be installed separately,
in accordance with their distribution rights, in `~/.claude/skills` or `~/.agents/skills`
(Codex also supports the older `~/.codex/skills`). The agent reports a missing required instruction
as an explicit step error. It is not a refusal before the model. Private settings,
hooks, and MCP configuration are not part of the Tide package or of the resource information.

The `flows/` seeds use the `{tide_standard}` field, resolved at render time,
and CLI commands. Migration recognizes the SHA-256 of the old unmodified seeds;
a single user change rules out automatic overwriting. Project overrides
remain the user's property. A custom template containing an unavailable reference
to the author's checkout stays byte-for-byte unchanged and gets a warning in the Board errors.
You can fix it by hand, using `{tide_standard}`, `tide next-id`, and `tide check-plans`.

## Uninstalling

`make uninstall` removes the client, the service, and the entire Tide resources directory.
`uninstall-cli` removes only the client: the resources remain for the running service.
Since PT-224 the CLI component also manages the [own package of issue adapters](issue-skills.md)
in `tide/agents` and the native skill `tide-issue`. It removes only the files of that package covered
by the manifest; local changes and foreign files are neither overwritten nor removed. Package backups
remain. The `tide/resources` resources still remain for the running service.
`uninstall-daemon` also removes `tide/resources`. A custom directory pointed to by
`TIDE_RESOURCES_DIR`, user skills, projects, and modified Board templates
are not removed. Installing or uninstalling should not be done by manual
edits of files in the user configuration directories.

The installation tests use their own HOME/XDG, a different user name, paths with spaces,
and mocks of systemd and the model. They do not run the installation on a real desktop.
