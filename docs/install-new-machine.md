# Tide on a new machine — minimal installation

Instructions for a person who does not know Tide and wants to work with cards on the Board on a new computer.
We install only what the card flow needs:

- the `tide-daemon` service (a systemd user service, talks over D-Bus),
- the **Tide** window with the Board (application menu → Tide),
- the `tide` command (CLI) and the card flow resources (the plan standard, ticket numbering tools).

We do **not** install the Tide theme (login screen, boot screen, panels, dock, wallpapers, colors, Konsole, Vivaldi).
Tide works on a plain KDE with the default Breeze. The Tide window always has its own dark colors (the Mist palette written
into the window at installation), independent of the system theme — this is intended.

Everything is done by the script `scripts/bootstrap-machine.sh`. It can be run many times; the `--check` mode only
checks and changes nothing.

## 1. Preparing the system

1. The system: **Kubuntu 26.04 LTS** (or newer) or **Fedora KDE Plasma Desktop 40+** (verified on 44).
   **Plasma 6** is required — Kubuntu 24.04 still has Plasma 5; the script refuses to install on Plasma 5,
   on Kubuntu older than 26.04 and on Fedora older than 40, changing nothing.
2. A regular user account with `sudo` rights (during system installation: "administrator"). We do not run the script
   as root.
3. A network (packages, logging in to Claude and Codex, cloning repositories).
4. A full update and a restart **before** you run the script — then installing the Tide packages does not upgrade anything in Qt
   or KDE (the script checks this anyway and refuses if necessary):
   - Kubuntu: `sudo apt update && sudo apt full-upgrade`, then restart,
   - Fedora: `sudo dnf upgrade --refresh`, then restart.
5. Log in to a **Plasma (Wayland)** session. The Tide service starts together with the graphical session.
6. Tools for fetching repositories: `sudo apt install git curl` or `sudo dnf install git curl`.
   Access to repositories: an SSH key or a token for the Git server that hosts Tide and your projects.

## 2. The Tide repository

```bash
mkdir -p ~/DevWorkspace
git clone git@github.com:lakomytomaszdev/plasma-tide.git ~/DevWorkspace/plasma-tide
cd ~/DevWorkspace/plasma-tide
```

The repository is private — you need access to it (or a copy from another source).

Projects must lie in `~/DevWorkspace` (Tide looks for repositories there). This machine's Board will be created
in `~/DevWorkspace/tide-board` — **do not copy** the Board from another computer, this machine has its own, empty one.

## 3. Claude Code and Codex — before the script

Install both programs **before** the script: the Tide CLI installation adds the Tide skills to Codex only if
it sees the `codex` program. (If you install it later — run the script again.)

Claude Code (installs to `~/.local/bin/claude`):

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Codex (through npm into `~/.local`, without sudo for Codex itself):

```bash
sudo apt install nodejs npm        # Fedora: sudo dnf install nodejs npm
npm install -g --prefix ~/.local @openai/codex
```

Close and open the terminal (so that `~/.local/bin` is in `PATH`), then log in to your accounts
(or the accounts indicated by the person who looks after this machine):

```bash
claude          # in the program: /login → your Claude account; then /exit
codex login     # opens a browser → your ChatGPT account
```

Set the git identity (commits in projects):

```bash
git config --global user.name "Imię Nazwisko"
git config --global user.email "adres@example.com"
```

## 4. The installation script

First the diagnosis alone (it changes nothing, does not ask for a password):

```bash
cd ~/DevWorkspace/plasma-tide
bash scripts/bootstrap-machine.sh --check
```

Then the installation:

```bash
bash scripts/bootstrap-machine.sh
```

What it does, in order (before each command with `sudo` it shows it and asks "[t/N]"):

1. Checks the system: the version (Kubuntu ≥ 26.04, Fedora ≥ 40), Plasma 6, systemd user instance, the amount of memory.
   On an unsupported system it ends with exit code 2, changing nothing.
2. Packages (the table below). On Kubuntu: `sudo apt-get update`, then a **simulation** `apt-get -s install …`;
   if the simulation upgrades, downgrades or removes Qt 6 or KDE Frameworks 6 packages, the script aborts and installs
   nothing (then: the full system update from step 1.4 and again). On Fedora the same through
   `dnf install --assumeno …`. Only then `sudo apt-get install …` / `sudo dnf install …`.
3. Checks `claude` and `codex`, the login and the git identity.
4. The step sandbox (bwrap) — below, section 5.
5. `make pre-install-backup install-daemon install-tide-window install-cli` — a backup of the Plasma look
   (just in case; nothing in the look changes), the service, the window, the CLI and resources.
6. Checks that the service is running and answers on D-Bus (`tide info --json`). On a weaker machine it turns off
   semantic search (section 6).
7. Creates an **empty Board** `~/DevWorkspace/tide-board` (a local git repository, without a remote).
8. Loads the window off screen (all views, on a weaker machine only the Board) — checks that QML and PyQt6
   are complete.

At the end it prints "Uwagi" (Notes; they do not block) and "Brakuje" (Missing; to do). Exit code: 0 — done, 2 — something
is missing, 1 — an error. After the first successful installation, **log out and log in again**.

Options: `--yes` (no questions), `--no-packages` (only checks packages), `--no-board` (does not create the Board),
`--low-power` (a weaker machine — section 6; with less than 20 GB of RAM it turns on by itself).

## 5. The sandbox (bwrap) on Kubuntu

Background card steps run in a sandbox: Claude Code uses `bwrap`, and Tide also runs git in it
on the step's worktree. Ubuntu restricts user namespaces through AppArmor, so `bwrap` needs a profile.
The script shows the state (`python3 scripts/sandbox/check.py`). There are three possible situations:

- **"Piaskownica może działać."** (The sandbox can work.) — do nothing.
- **Profile: missing** — the script will suggest `sudo bash scripts/sandbox/install.sh` (a profile for `/usr/bin/bwrap` only,
  the rest of the system stays restricted). Answer "t" (yes).
- **Profile: foreign** and "Zagnieżdżona przestrzeń … nie działa" (The nested namespace … does not work) — Kubuntu 25.04 and newer ship their own
  profile `bwrap-userns-restrict`, under which the Claude Code sandbox does not start (the card step ends with a
  sandbox error). Swapping for the Tide profile, with a copy of the original:

  ```bash
  sudo mv /etc/apparmor.d/bwrap-userns-restrict /root/bwrap-userns-restrict.dist
  sudo bash scripts/sandbox/install.sh
  python3 scripts/sandbox/check.py        # should say "Piaskownica może działać."
  ```

  Undoing: `sudo bash scripts/sandbox/uninstall.sh && sudo mv /root/bwrap-userns-restrict.dist
  /etc/apparmor.d/bwrap-userns-restrict && sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict`.
  When the `apparmor` package is updated, the system may ask about this file — choose to keep the current version.
  **This variant could not be verified on a real Kubuntu 26.04** (the tests ran in containers that
  inherit the host computer's AppArmor) — if after the swap `check.py` still says "nie działa" (does not work), report the
  result (section 10) and until then use Codex executors.

On Fedora nothing is needed (no AppArmor restriction; `check.py` says "nie dotyczy" (not applicable)).

## 6. A weaker machine

A computer with 8–16 GB of RAM or a weak processor and graphics card can handle Tide if it does not run
things that Tide can do but that are not needed for working with cards:

- **Semantic search turned off.** The agent session index for KRunner scans sessions from 30 days every 5 minutes
  and computes vectors with a local model. With less than 20 GB of RAM (or with `--low-power`) the script turns
  it off itself; by hand: `busctl --user call org.tide.Daemon /Models org.tide.Daemon.Models SetSemanticSearch s off`
  or the Tide window → Modele (Models) → Wyszukiwanie (Search). KRunner still searches by words.
- **No Ollama and local models.** The minimal installation does not set them up. In the Tide window → Modele the server
  "nie odpowiada" (not responding) — that is normal. Do not switch the morning report description to a local model.
- **Do not turn on boxes (Incus)**, if your version of Tide has them — the card flow setting `box.enabled`
  stays at the default. It is a feature for workstations with spare memory and processor.
- **One Tide window.** A later launch only shows the running window; do not leave open views
  you do not use (Docker, Sesje (Sessions)). A large Board (hundreds of cards) refreshes more slowly — if it stutters, report
  it together with `tide info --json`.
- **Repository tests** (if you develop Tide on this machine): by default they run in as many processes as
  cores minus two, and each process loads Qt. With ≤ 16 GB of RAM: `make test-changed BASE=<base> JOBS=2`,
  with 8 GB: `JOBS=1`. The full `make test` only when it is really needed.

## 7. The first project on the Board

1. Clone the project repository into `~/DevWorkspace`, e.g.:
   `git clone git@<your-git-server>:<group>/<project>.git ~/DevWorkspace/<project>`.
2. Open the window: application menu → **Tide** (the **Tablica** (Board) view).
3. The bottom left corner: **Projekty i tagi** (Projects and tags). The cloned repository is in the section "REPOZYTORIA BEZ PROJEKTU" (REPOSITORIES WITHOUT A PROJECT) →
   **＋ Dodaj jako projekt** (＋ Add as a project) (or **Dodaj projekt** (Add project) and point to the directory). Give it a name, add a tag (e.g. the starter
   **własne** (own) or your own), **Zapisz projekt** (Save project).

The same without the window (e.g. over SSH):
```bash
busctl --user call org.tide.Daemon /Board org.tide.Daemon.Board UpsertProject s \
  '{"slug": "projekt", "name": "Projekt", "repo": "/home/<użytkownik>/DevWorkspace/projekt", "tags": ["własne"]}'
```

## 8. The first card

1. Board → **＋ Karta** (＋ Card): a title ("what to do"), a project, a short description. A new card lands in Backlog.
2. Drag the card to **Do zrobienia** (To do; the "Co ma zrobić agent?" (What should the agent do?) window opens) or click
   **Uruchom agenta…** (Run agent…) on it.
3. The entry:
   - **Od razu** (Immediate) — one ticket in the background, without planning; you choose the executor (e.g. Sonnet) and the reviewer,
   - **Planowanie → Lekki** (Planning → Light) — a light plan in the background, which you approve in the card panel, then tickets in the background,
   - **Planowanie → Pełny** (Planning → Full) and **Tylko specyfikacja** (Specification only) — a conversation in Konsole (questions to you); they use
     the `grilling` and `tdd` skills in `~/.claude/skills`. The methodology skills are not part of Tide — install
     them separately before you use these entries (see [resource portability](tide-resources.md)),
   - **Mam plan** (I have a plan) — taking over an existing plan from the project repository.
4. **Uruchom agenta (Ctrl+Enter)** (Run agent). The card moves to "W toku" (In progress); the agent's questions and approvals appear
   in the card panel, and the result lands on the card branch `card/k-…` in the project repository.
5. **Do not** merge the card branch into the main branch or `push` without a decision from the person responsible for the project.

Make the first card simple (e.g. a fix in the README), to check the whole cycle: step → review → card branch
→ "Do sprawdzenia" (To check).

## 9. What to check

```bash
bash scripts/bootstrap-machine.sh --check   # everything "ok", code 0
systemctl --user status tide-daemon         # active (running)
tide info --json                            # "ok": true, resources "available": true
python3 scripts/sandbox/check.py            # "Piaskownica może działać."
```

In the Tide window: the Board shows the project; a card after "Od razu" goes through In progress to To check.
A step ending in "login" means the agent program is not logged in (`claude` → `/login`, `codex login`).

## 10. What to report on an error

Collect the files and report the problem in the Tide repository Issues (or to the person who looks after the installation).
The files contain project names and fragments of work — do not publish them, attach only where it is safe:

```bash
cd ~/DevWorkspace/plasma-tide
bash scripts/bootstrap-machine.sh --check > ~/tide-check.txt 2>&1
journalctl --user -u tide-daemon --since today > ~/tide-daemon.log
tide info --json > ~/tide-info.json
python3 scripts/sandbox/check.py > ~/tide-sandbox.txt 2>&1
```

For a problem with a specific card, attach its journal: `~/DevWorkspace/tide-board/flow/K-<number>/journal.jsonl`
and write what was clicked and what appeared in the card panel.

The "Opisz problem i ponów" (Describe the problem and retry) button in the card panel saves a report in the `plasma-tide` project on the Board.
It works only when such a project exists — add `~/DevWorkspace/plasma-tide` as a project
(section 7) if you want to use it.

## Dependencies

From the code: Python imports of the service, the window and the CLI (PyQt6: QtCore, QtDBus, QtGui, QtNetwork, QtQml, QtQuick), the QML
imports of the window (QtQuick, Controls, Layouts, Shapes, Window, Templates, Dialogs) and programs called by the service.
The source of the list: `scripts/bootstrap_packages.py` (`python3 scripts/bootstrap_packages.py table`).

| Kubuntu (apt) | Fedora (dnf) | What for | Required |
| --- | --- | --- | --- |
| `python3` | `python3` | the service, window and CLI run /usr/bin/python3 (min. 3.10) | yes |
| `python3-pyqt6` | `python3-pyqt6` | PyQt6: QtCore, QtDBus (the service and CLI over D-Bus), QtGui, QtNetwork; in Fedora also QtQml and QtQuick | yes |
| `python3-pyqt6.qtqml` | (in the package above) | PyQt6.QtQml — the QML engine of the Tide window | yes |
| `python3-pyqt6.qtquick` | (in the package above) | PyQt6.QtQuick — the Tide window, issue report screenshots | yes |
| `qml6-module-qtquick` | `qt6-qtdeclarative` | QML: the QtQuick import | yes |
| `qml6-module-qtquick-controls` | (in the package above) | QML: QtQuick.Controls | yes |
| `qml6-module-qtquick-layouts` | (in the package above) | QML: QtQuick.Layouts | yes |
| `qml6-module-qtquick-shapes` | (in the package above) | QML: QtQuick.Shapes (icons and logo drawn with paths) | yes |
| `qml6-module-qtquick-window` | (in the package above) | QML: QtQuick.Window | yes |
| `qml6-module-qtquick-templates` | (in the package above) | QML: QtQuick.Templates | yes |
| `qml6-module-qtquick-dialogs` | (in the package above) | QML: QtQuick.Dialogs (the Sprawy (Issues) view) | yes |
| `qml6-module-qtqml-workerscript` | (in the package above) | QML: a QtQuick.Controls dependency | yes |
| `git` | `git` | the Board is a git repository; worktrees and card branches | yes |
| `make` | `make` | installation through make install-* | yes |
| `bubblewrap` | `bubblewrap` | the step sandbox (Claude Code) and the service's git in the sandbox (/usr/bin/bwrap) | yes |
| `socat` | `socat` | the network intermediary in the Claude Code sandbox | yes |
| `zstd` | `zstd` | a backup of the look before installation (make pre-install-backup) | yes |
| `python3-dbus` | `python3-dbus` | the KRunner plugin in the service (without it the service works, KRunner does not) | no |
| `python3-gi` | `python3-gobject` | the GLib loop of the KRunner plugin | no |
| `ripgrep` | `ripgrep` | rg on the read-only command list of the steward step (grep as a fallback) | no |
| `python3-pil` | `python3-pillow` | icons of the menu entries "Tide Sesje" and "Tide Worktrees" (without it — no icons) | no |
| `librsvg2-bin` | `librsvg2-tools` | rsvg-convert for the same icons | no |
| `desktop-file-utils` | `desktop-file-utils` | refreshing the .desktop file database after installing the window | no |

Besides packages, present in every KDE Plasma 6 installation: systemd (`systemctl`, `systemd-run` — every card
step is a separate `--user --scope` unit, `busctl`), Konsole (the Full/Specification-only conversations,
"entering" a step), `xdg-open`, Spectacle (screenshots in the launch window), the Plasma notification server.
Optionally, only for their own views: Docker (the Docker view), Ollama (local models), Incus (card boxes),
`gh`/`glab` (base hints from PRs/MRs).

Version requirements: Python ≥ 3.10 (verified: 3.12 neon, 3.14 Ubuntu 26.04 and Fedora 44), Qt 6 (verified:
6.10 Ubuntu 26.04, 6.11 Fedora 44 and neon), Plasma 6. KDE neon has the QML modules in `qt6-declarative` instead of
`qml6-module-*` — the script recognizes a module by its `qmldir` file, not by the package name.

## Fedora — known differences

- **Packages**: `python3-pyqt6` contains QtQml and QtQuick (in Ubuntu the separate `python3-pyqt6.qtqml` and `.qtquick`);
  all QML modules are in `qt6-qtdeclarative`; `python3-gobject` instead of `python3-gi`, `python3-pillow`
  instead of `python3-pil`, `librsvg2-tools` instead of `librsvg2-bin`. QML modules in `/usr/lib64/qt6/qml`.
- **Python**: Fedora 44 has Python 3.14 as `/usr/bin/python3` — the service, window and CLI work on it.
  A minimal Fedora image may lack `python3`; the script then suggests `sudo dnf install python3`.
- **Simulation**: instead of `apt-get -s` — `dnf install --assumeno` (works without root, code 1 for a correct
  transaction). The same Qt 6 / KDE Frameworks 6 guard.
- **Sandbox**: no AppArmor and its user namespace restriction — `bwrap` works without a profile,
  `scripts/sandbox/install.sh` is unnecessary (it will refuse: "ograniczenia nie ma" (there is no restriction)). SELinux was not tested
  on a real Fedora (a container inherits the host kernel).
- **Codex**: Fedora has no snap — Codex through npm into `~/.local` (section 3).
- **PATH**: `~/.local/bin` is added by `~/.bashrc`, not by the systemd session. The Tide service itself looks for `claude` and `codex`
  in `~/.local/bin` when they are not in its `PATH`.
- **The systemd user instance, D-Bus, `systemd-run --user --scope`, `busctl`**: no differences.
- **Theme**: do **not** install the boot screen (`make install-plymouth`) on Fedora — it uses `update-initramfs`
  (Debian/Ubuntu), Fedora has `dracut`.

## Optional: the Tide theme

Not needed for working with cards; it changes the desktop look. First a backup, then the whole thing:

```bash
make backup NAME=przed-tide
make install            # look, panels, dock, widgets, Konsole, KRunner… (it makes a backup first by itself)
make restore NAME=przed-tide   # return to the previous look
```

The login screen and the boot screen are separate and need sudo: `make install-sddm`, `make install-plymouth`
(Kubuntu only). The theme was developed on KDE neon (Plasma 6.8, two monitors); on Kubuntu and Fedora
it was not tested. A description of the components: `README.md`.

## For an AI agent helping with the installation

This section is for an agent (Claude Code, Codex) that on a new machine helps the user install
Tide or diagnose a problem.

**The order of checks** (all read-only):

1. `bash scripts/bootstrap-machine.sh --check` — code 0: done, 2: something is missing (the "Brakuje" list at the end),
   1: a script error. Start with the "Brakuje" list, then "Uwagi".
2. `systemctl --user status tide-daemon` and `journalctl --user -u tide-daemon -n 100` — whether the service is running
   and what it reports at startup.
3. `tide info --json` (or `busctl --user call org.tide.Daemon /Issues org.tide.Daemon.Issues Info`) —
   the service's answer and the availability of resources.
4. `python3 scripts/sandbox/check.py` — the state of the bwrap sandbox.
5. For a card problem: the journal `~/DevWorkspace/tide-board/flow/K-<number>/journal.jsonl` (the last
   `step.finished` entries: `result`, `reason`, `fault`) and
   `busctl --user call org.tide.Daemon /Board org.tide.Daemon.Board Snapshot` (read-only).

**Typical causes:**

- A step ends with `reason: login` — the agent program is not logged in: `claude` → `/login`, `codex login`.
- "nie znaleziono programu claude/codex" (program claude/codex not found) — the program is missing from `PATH` and from `~/.local/bin` (section 3); after installing,
  run the script again so that `install-cli` adds the Tide skills for Codex.
- Sandbox: on Kubuntu 25.04+ a "foreign" AppArmor profile without a nested namespace (section 5) — swapping the
  profile needs sudo and the user's decision; on older Ubuntu a missing profile (`scripts/sandbox/install.sh`).
- "KDE Plasma 5…" or "Tide wymaga Kubuntu 26.04" (Tide requires Kubuntu 26.04) — the system is not suitable; do not bypass this check.
- The package guard refuses (Qt 6 / KDE Frameworks 6 in the simulation) — suggest a full system update
  and a restart, not an installation that bypasses the guard.
- A Board with a remote repository (`remote`) — probably a copy of the Board from another machine.
- A weak machine (stutters, swap) — section 6; tests with `JOBS=2` or `JOBS=1`.

**What not to do without the user's explicit consent:**

- `sudo` in any form (packages, an AppArmor profile, `apt`/`dnf`) — show the command and wait for the decision,
- `make install` or other theme targets (look, panels, SDDM, Plymouth) — the minimal set does not need them,
- manual edits of `~/.config` and `~/.local/share` (installation goes only through the script and `make install-*`),
- deleting, overwriting or copying the Board (`~/DevWorkspace/tide-board`) and changing its repository,
- merging card branches into the main branch and `git push`.

To report a problem, collect the files from section 10 and tell the user what they contain.
