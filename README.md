# Tide

A custom KDE Plasma 6 theme with widgets for working with AI agents. The first variant is **Mist**: soft dark graphite, teal and lavender, with pastels for statuses.

- Rollout plan: https://claude.ai/artifact/FbiGQYsWQ3gWQiDHLxvKFv
- Mockups: https://claude.ai/artifact/1h17Au71mmdEX5sNBSaAgC
- Decisions and their rationale: [docs/decisions.md](docs/decisions.md)

## Card flow

A card on the Board can launch a single ticket right away, prepare a light plan in the background, or take over an
existing plan. Tide records the course of work in the card journal, runs steps in separate worktrees, checks the
plan and tickets with other executors, shows questions and approvals in the panel, and merges accepted tickets
into the card branch. The "Praca" (Work) tile shows the number of running steps, and "Sesje" (Sessions) shows
tickets and the event timeline. The guard marks a card that is standing still without a named reason; the morning
report lists completed tickets together with their executor.

`check: manual` means the list "Co przetestować ręcznie i jak" (What to test manually and how), and `check: auto`
means there is none. Merging the card branch into the main branch always requires a separate decision from the
user. Full planning in the new flow and automatic step handoff between providers belong to the second stage.
Details: [plan standard](docs/agent-plan-standard.md) and [service description](docs/tide-daemon.md).

An installed Tide reads the plan standard and tools from its own copy of the resources.
`tide info --json` shows locations, versions and availability; configuration, methodology
dependencies and template migration are described in [resource portability](docs/tide-resources.md).

## Environment

KDE neon, Plasma 6.8, Wayland, two monitors (2560×1440 primary and 1920×1080). The theme installs into the home directory. Only the login screen (SDDM) and the system boot screen (Plymouth) need sudo.

New machine (Kubuntu or Fedora with Plasma 6), service only, Board window and CLI without the theme:
`bash scripts/bootstrap-machine.sh` — step-by-step instructions: [docs/install-new-machine.md](docs/install-new-machine.md).

## Structure

| Directory | Contents |
| --- | --- |
| `theme/palette/` | Variant palettes in JSON, the only source of colors, radii, opacity and bar sizes for the generators: `mist.json` (work) and `streamer.json` (Stream activity, an overlay on Mist with the Streamer brand colors; loaded by `theme.palette.load`), checksums of the Mist files for the regression test (`outputs.py`, `make mist-golden`) and a variant comparison (`compare.py`, `make palette-compare`) |
| `theme/wallpaper/` | Wallpaper: the `cat` variant (a cat illustration in `assets/cat`, awake and asleep), `blobs` (blobs drawn from the palette), `pictures` (Stream activity: the graphic artist's images for each monitor in `assets/streamer`, desktop and lock screen) and `map` (a gradient with map lines from the palette, `map.py`, spare), always one canvas for both monitors (`variant.py`, `generate.py`); the "Tide: kot" (Tide: cat) wallpaper plugin with a dozing cat (`plugin.py`, `org.tide.cat/`) and `qml/CatBackdrop.qml`, shared with the lock screen |
| `theme/activities/` | Look descriptions of activities (`work.json` — today's Mist, `stream.json`): color scheme, Plasma style, wallpaper palette, lock screen variant, panel layout; applied by `scripts/activity_look.py` |
| `theme/plasma/` | The "Tide" Plasma style (desktoptheme): frame generator, preview (`preview.py`) and a check with the real KSvg (`ksvg_check.py`) |
| `theme/lookandfeel/` | The "Tide" global theme (`org.tide.desktop`): pointers to the Tide components, previews and the loading screen (`splash/`, screenshots from the real ksplashqml in `splash_shot.sh`) |
| `theme/lockscreen/` | The Tide lock screen as a Plasma shell package (QML based on the Plasma 6.8 screen), screenshots from the real greeter, a check of the shell copy in a nested session (`shell_copy_check.sh`) |
| `theme/icons/` | The "Tide" icon overlay and the `tide-icons` command (audit, sync, preview); tray tiles for the top bar (`tray/`, `tray.json`, `tray.py`) |
| `tray/` | The `tide-tray` service: mirrors of tray items that ship a ready-made image (ChatGPT, Discord, OBS) with a Tide tile, menus and clicks forwarded to the application, the original turned off in the tray settings (decision 95) |
| `theme/sddm/` | The SDDM login screen "tide": QML, assembly from the palette (`build.py`), a pre-install check (`check.py`, a greeter stand-in in `harness/`), a screenshot of the real greeter (`greeter_shot.sh`) |
| `theme/plymouth/` | The system boot screen, the Plymouth theme "tide": assembly from the palette (`build.py`), script logic (`logic.script`), a stand-in for the script module (`harness.py`), a pre-install check (`check.py`), frames without root (`preview.py`), a preview on the real plymouthd (`preview_real.sh`) |
| `scripts/` | Backups and restoring of the look (`backup.sh`, `restore.sh`, `paths.sh`), component installers (`sddm_system.py` and `plymouth_system.py` are the only ones run through sudo) and syncing of the shell copy with the lock screen (`shell_sync.py`) |
| `theme/konsole/` | Tide Konsole: color scheme (transparency with blur, watermark), profile, tab bar stylesheet |
| `theme/terminal/` | Bash prompt with chips (`prompt.bash`), themes for bat, delta, btop, lazygit, fzf and LS_COLORS (`generate.py`), Claude Code status bar (`statusline.py`) |
| `theme/vivaldi/` | Vivaldi Tide: profile themes and the UI CSS stylesheet from the palette (`generate.py`); installer `scripts/vivaldi.py`, preview in a nested KWin `scripts/vivaldi_preview.sh` |
| `scripts/panels/` | plasmashell scripts for the top bars, the dock and the sidebar (screenshot, install, order, uninstall) |
| `tools/` | Icon tools: `make_tile.py` (a tile from the original icon), `make_tint.py` (a recoloring experiment, rejected) |
| `daemon/` | The `tide-daemon` service: agent sessions, worktrees, morning report, idle detection, AI Limits forecast and local models (`/Models`: server status, settings for endpoints, roles and the local agent program from the Tide window, connection test, model downloads; `~/.config/tide/models.json`) over D-Bus ([description and contract](docs/tide-daemon.md)) |
| `widgets/lib/` | Shared QML widget library: card, dock tile, tile texts, gauge, ring, bar chart with sample history, status dot and counter, data source from the service, local model state and wording (`models.js`, `ModelsState.qml`) and opening the Tide window on a view (`TideWindow.qml`) |
| `widgets/work/` | The "Praca" (Work) tile `org.tide.work` (PT-40, in the right side of the dock and in the sidebar in place of the Board and Agents): "Praca · 10 · ● 3 w toku · ● 2 czeka · ● 2 do spr." (Work · 10 · 3 in progress · 2 waiting · 2 to review) with a "＋" for a new task; a popup with what waits for you ("Przejdź" (Go), for a session without a card "+ karta" (+ card)), in progress, to review, to do ("Uruchom" (Run)) and a collapsed Finished group, "Otwórz Tablicę" (Open Board), "Otwórz Sesje" (Open Sessions), the morning report — one list from the service (`/Board Work()`) |
| `widgets/agents/` | The agents widget `org.tide.agents`: the tile "Agenci · 2 pracują · 1 czeka · 2 gotowe" (Agents · 2 working · 1 waiting · 2 done), a session list in the popup; since PT-40 outside the dock and the bar (replaced by the Work tile), the package stays until removal |
| `widgets/limits/` | The "AI" tile `org.tide.limits` (in the right side of the dock): remaining ChatGPT and Claude limits from the QuotaApp service and the local model dot from tide-daemon; a popup with service cards and a "Modele lokalne" (Local models) section with a link to the Models view in the Tide window |
| `widgets/models/` | The "Modele" (Models) tile `org.tide.models` — since PT-41 outside the dock (the local model is in the "AI" tile), the package stays in case of a return: whether the local model server responds, the model in memory and how much of it is on the card, the queue; a popup with servers, roles and the report mode |
| `widgets/worktrees/` | The worktrees tile `org.tide.worktrees`: how many to clean up (and how many GB), how many are in use; a click opens the window |
| `widgets/stream/` | Stream activity widgets (PT-37) with state from the Streamer core over D-Bus, not from tide-daemon (decision 79): Stream dock tiles — Stream `org.tide.stream`, Player profile `org.tide.stream.profile`, Observer `org.tide.stream.observer`, Camera `org.tide.stream.camera` (PT-82), "?" `org.tide.stream.help` — and left-monitor desktop widgets — the stream console `org.tide.stream.scenes` (Scenes and the full Stream card), chat `org.tide.stream.chat` and the whole left monitor `org.tide.stream.desk` (the stream console or the "Granie" (Gaming) view according to OBS, PT-83); shared parts in `widgets/lib/` (`CoreSource.qml`, `stream.js`, `StreamCard.qml`, `ProfileList.qml`, `StreamConsole.qml`, `ScenesCard.qml`, `ChatView.qml`) |
| `widgets/monitors/` | The "Monitory · PC → Xbox" (Monitors · PC → Xbox) tile `org.tide.stream.monitors` (PT-38, Stream dock): switches the right monitor's input after confirmation in the popup; the switch is done by tide-daemon (`/Monitors`) |
| `widgets/newtask/` | The "＋ Zadanie · nowe dla agenta" (＋ Task · new for an agent) tile `org.tide.newtask`: opens the new task window |
| `apps/newtask/` | The "Tide: nowe zadanie" (Tide: new task) window (QML + PyQt6, a `.desktop` file, a Konsole profile): repository, Claude Code or Codex, command, screenshots, a new worktree |
| `apps/common/` | Shared application Python, copied by `widgets/stage.py` next to the application program and into the Tide window views: `taskimages.py` — task screenshots (the "Nowe zadanie" window and the launch window on the Board) |
| `apps/tide/` | The "Tide" window (QML + PyQt6, `.desktop` files): a view bar with Tablica (Board), Sprawy (Issues), Sesje (Sessions), Worktrees, Docker, Raporty (Reports), Modele (Models); a single `org.tide.Window` instance that opens straight to the given view and item |
| `apps/issues/` | The "Sprawy" (Issues) view: a Markdown report as safe HTML, images from the manifest through the public API, version-checked editing, submit/reject/reopen and explicit opening of evidence. Contract and dependencies: [docs/issues-api.md](docs/issues-api.md) |
| `apps/sessions/` | The "Sesje" (Sessions) view of the Tide window (`backend.py`, `qml/`): coordinators, a ticket board (Do zrobienia / W toku / Czeka / Review / Gotowe — To do / In progress / Waiting / Review / Done), a subagent timeline, a subagent result, a branch diff, YouTrack |
| `apps/models/` | The "Modele" (Models) view of the Tide window (`backend.py`, `qml/`): local model servers with a "Używaj tego serwera" (Use this server) switch, adding and editing a server (with a token), the report mode, roles and their models, models on the server and downloading |
| `apps/worktrees/` | The "Worktrees" view of the Tide window (`backend.py`, `qml/`): the groups W pracy / Do sprzątnięcia / Do decyzji (In use / To clean up / To decide), search, repository filters, deleting the selected ones |
| `widgets/system/` | The `org.tide.system` widget: processor, memory, graphics card and disks from ksystemstats sensors |
| `widgets/sidebar/` | The `org.tide.sidebar` sidebar header: "Tide" and the Meta+S shortcut that slides the bar out |
| `kwin/` | KWin scripts `tide-agent-jump` (Meta+A) and `tide-new-task` (Meta+Alt+A, new task) and their installer |
| `krunner/` | The KRunner plugin file "Tide: agenci i projekty" (Tide: agents and projects) and its installer (results come from tide-daemon) |
| `mk/` | `make` targets of the individual components (`install-*`, `uninstall-*`, test directories) |
| `docs/` | Design decisions, the service description, the agent work plan standard (`agent-plan-standard.md`) |

The loading screen is part of the global theme (`theme/lookandfeel/splash/`).

## AI agents

```bash
make install-daemon install-widget-work     # the service and the Work tile (no look backup)
make install-agent-jump                     # Meta+A shortcut: jump to the agent that has waited longest
make install                                # everything, backup first
make test                                   # tests of all components
make test-changed BASE=master                # tests chosen for the branch diff and working changes
make test-cardflow                           # the card flow suite (also test-daemon, test-board)
make preview-widgets                        # tiles on the dock background, the agents popup, the Work tile, the sidebar and the limits popup, the "AI" tile with a local model: build/preview/dock-tiles-*.png, agents-popup.png, work-*.png, sidebar-*.png, limits-popup-*.png, ai-*.png
```

Tests run in parallel (by default cores minus two, `JOBS=N` changes the limit) and collect all
errors. Rules for choosing suites and logs: [docs/testing.md](docs/testing.md).

When an agent starts waiting — asks for approval or asks a question — a notification "Agent czeka: <projekt>" (Agent waiting: <project>) arrives with a "Przejdź" (Go) button (once for each entry into waiting). A normal end of a turn is "skończył" (finished; counted as "gotowe" (done) in the tile): no notification and no Meta+A, also in Codex Desktop threads driven by a scheduled task ("zaplanowane" (scheduled)). Meta+A takes you to the window of the session that has waited longest (a Codex Desktop session opens its thread in the app — for a subagent, the main thread — through `codex://threads/<id>`); when nobody is waiting, nothing happens. If Meta+A is already taken (the installer prints this), you must free it in System Settings → Shortcuts and assign it to the action "Tide: przejdź do agenta, który czeka najdłużej" (Tide: jump to the agent that has waited longest).

**New task for an agent.** Meta+Alt+A, the "＋" on the Work tile in the dock, or "＋ Nowe zadanie" (＋ New task) in its popup and in the sidebar opens the window "Co ma zrobić agent?" (What should the agent do?): a repository from `~/DevWorkspace` (most recently active first; typing in the list searches), Claude Code or Codex, the task (Ctrl+Enter runs it), screenshots (Ctrl+V from the clipboard, dragging files, "Zrób zrzut" (Take a screenshot) of a selected area through Spectacle) and "Nowy worktree" (New worktree; on by default) with a branch derived from the command, e.g. `claude/popraw-naglowek-listy`, changeable in one click. tide-daemon creates the worktree from the local main branch (`git worktree add -b`, master/main/develop untouched, directory `<repo>/.claude/worktrees/<name>` or an existing `<repo>-worktrees`), opens a new tab in the most recently used Konsole window (no window — a new window) titled "project · branch" and runs `claude -- "<task>"` or `codex --image … -- "<task>"`. After the agent exits, a shell remains in the tab. The window shows errors (changes on master, an existing branch, a missing program) in place; when Konsole fails to start the task, the worktree and branch are rolled back. The window remembers the last repository, tool, model and switch, and a new session has a "nowe" (new) marker in the widget for 10 minutes. Next to the repository there is a model choice: "Chmura" (Cloud) or "Lokalny" (Local) — the model from the `agent` role (Tide → Modele), checked on the server before start; without a role, when the server does not respond or the model is missing, the option is disabled with a reason and a link "ustaw w Tide → Modele" (set in Tide → Models). With "Lokalny", instead of the Claude Code / Codex switch there is one line, e.g. "Codex na qwen3-coder:30b · Ten komputer" (Codex on qwen3-coder:30b · This computer), with a link "zmień w Tide → Modele" (change in Tide → Models): the local agent program is chosen once in the Models view (Codex by default, `agentProgram` in `models.json`), and the remembered cloud choice stays untouched (decision 72). Codex then gets `--oss --local-provider ollama --model=<model>` (Ollama on this computer) or its own provider through `-c` (Mac, an OpenAI-compatible server, token), Claude Code gets `--model=<model>` and the server's `ANTHROPIC_BASE_URL` (claude.ai connectors are then turned off); a token from `tokenFile` does not end up in argv, in the task file or in the commands the agent runs (Codex: the variable is excluded from commands, Claude Code: `apiKeyHelper`). The tab is titled "project · branch · lokalnie" (locally), and the session in the agents widget gets the note "lokalny · qwen3-coder:30b" (local · qwen3-coder:30b). On this computer (RTX 3070 8 GB) a local agent is slow and weak — it is preparation for the Mac (decision 70). Meta+Shift+A from the mockup is taken in Plasma (activities), so the shortcut is Meta+Alt+A (decision 47); to change it: System Settings → Shortcuts → KWin → "Tide: nowe zadanie dla agenta" (Tide: new task for an agent).

```bash
make install-daemon install-new-task install-new-task-shortcut install-widget-newtask
make preview-new-task          # a window screenshot on repositories from this computer (read-only): build/new-task.png
```

Task screenshots stay in `~/.local/share/tide/tasks/` (the agent reads them from there; `make uninstall` does not delete them). Details: [docs/tide-daemon.md](docs/tide-daemon.md#new-task).

**KRunner.** `make install-daemon install-krunner` adds the plugin "Tide: agenci i projekty": when you type a project name, a branch or a fragment of a conversation title into KRunner (Alt+Space), it shows Claude Code and Codex sessions from the last 30 days (`Claude · plasma-tide · <title>`, `<branch> · 3 days ago`), projects from `~/DevWorkspace` and worktrees. Enter resumes the session in a new Konsole window in its directory (or switches to the window if the session is still running), or opens the project or worktree in Konsole; the action next to it opens in Rider. `ai <words>` searches sessions only. A longer query (three words or more, e.g. "that session about docker and ports", "where I fixed the morning report") also finds sessions **by meaning**, without shared words — ranked below word matches, with the note "podobne znaczeniowo" (semantically similar). This is computed by a local model (the Search role, by default `bge-m3` on this computer's Ollama, after switching to the Mac — on the Mac) from metadata alone: the title, the start of the first message, the project, the branch and plan ticket titles, without conversation content; the vectors live in `~/.local/share/tide/search/sessions.json` (0600). When the model server does not respond or is too slow, KRunner shows word results only. It is turned on and off in Tide → Modele (decision 75). Details: [docs/tide-daemon.md](docs/tide-daemon.md#krunner).

**Tide Sessions: coordinators and tickets.** A session that assigns work to subagents (Claude Code through `Agent`, Codex through `spawn_agent`) is a coordinator. The **Tide Sesje** (Tide Sessions) window shows coordinators from the last 7 days (with ticket counters), the board of the selected one — Do zrobienia (To do), W toku (In progress), Czeka (Waiting), Review, Gotowe (Done) — with progress ("8 z 13 ticketów gotowych" (8 of 13 tickets done)) and ticket details. The card says in Polish what the task is, what was actually done and what is happening now ("Review: 9 drobnych uwag, bez błędów blokujących; poprawki wprowadzone, nie przejrzane ponownie" (Review: 9 minor remarks, no blocking errors; fixes applied, not re-reviewed)) — the description is written by a model through the local Claude Code or, with `"summary": "local"`, by a local model (the same one as in "Na daily" (For the daily), in batches, with memory and intervals), and without one, by rules. Ticket details: "Co to jest" (What it is), "Co zrobiono" (What was done), "Teraz / review" (Now / review) and a timeline: the coordinator assigned the implementation → implementation finished → review with remarks in words and the first finding → fixes. "Pokaż wynik" (Show result) opens the subagent's report and the last steps of its work (subagents run inside the coordinator's process, so there is nothing to jump to), "Przejdź do koordynatora" (Go to coordinator) — its session window or a resume in Konsole, "Diff gałęzi" (Branch diff) — `git diff` of the ticket branch in Konsole, "Otwórz w YouTracku" (Open in YouTrack) — the ticket in the browser. Tickets are grouped by the ID from the subagent description, the branch or (with a prefix from the plan) the command; the state comes from the subagents' work, the plan in the repository ([plan standard](docs/agent-plan-standard.md): `docs/agents/plans/YYYY-MM-DD-<slug>.md`) and YouTrack (coordination only reads; the token from `~/.config/youtrack/token` or the former LogViewer MCP file, optional settings in `~/.config/tide/youtrack.json`). Next to a coordinator, the agents widget shows "TOM-20 · 8/13" and a "Sesje" link, KRunner — the word "sesje", and the morning report — a "Plany i tickety" (Plans and tickets) section. Details: [docs/tide-daemon.md](docs/tide-daemon.md#coordination).

```bash
make install-daemon install-tide-window install-widget-work
make preview-tide-window SANITIZE=1   # a screenshot of every view on data from this computer without work content: build/tide-window-<view>.png
```

**The Tide window.** Sessions and Worktrees are no longer separate windows but views of a single "Tide" window with a bar on the left: Tablica (Board), Sprawy (Issues), Sesje (Sessions), Worktrees, Docker, Raporty (Reports), Modele (Models) (Ctrl+1…7 switches views). Widgets, KRunner, menus and the service open it straight to the right view and item (`tide.py --view sessions --select claude:<session>`), and a subsequent launch only shows it. The bar has badges from the service (how many agents are waiting, how many worktrees are in use); when there is no source, the badge disappears. The old entry points keep working: the menu items "Tide Sesje" and "Tide Worktrees", pinned shortcuts (they group under the single Tide window), the D-Bus names `org.tide.SessionsWindow` and `org.tide.WorktreesWindow`, the commands `install-sessions-window`, `install-worktrees-window` and `tide-worktrees.py`. The default view is the Board. Rationale: decisions 59 and 61–63.

**The work Board.** A personal backlog across all projects: a card is a "thing to do" (a feature, a fix, an idea), with a project, tags (e.g. "Firma" (Company), "własne" (own) — inherited from the project) and a description. The data lives in a local git repository `~/DevWorkspace/tide-board` (another location: `--board-dir` or `$TIDE_BOARD_DIR`), which tide-daemon creates on the first card and commits after every change — also after manual edits of files and plans written by agents in `docs/<project>/`. No remote; the board does not appear in Worktrees, KRunner or the "Nowe zadanie" window. The default work mode of a new project is the full flow. The `/Board` object (`org.tide.Daemon.Board`), preview: `/usr/bin/python3 daemon/tide-daemon.py --print-board`. The **Tablica** (Board) view in the Tide window: columns Backlog, Do zrobienia (To do), W toku (In progress), Czeka na mnie (Waiting for me), Do sprawdzenia (To check) and Gotowe (Done); cards are dragged between Backlog, To do and Done (Ctrl+←/→ from the keyboard), tag and project filters, search (Ctrl+F), the "Wiersz na projekt" (Row per project) layout, a new card with Ctrl+N or "＋ Karta" (＋ Card), a click opens editing (project, tags, description, note, archiving), "Projekty i tagi" (Projects and tags) at the bottom of the bar. In progress, Waiting and To check are filled by the coordinator state (PT-5), and the "Co ma zrobić agent?" window after a drag to To do — PT-4. The launch window from a card has what the "Nowe zadanie" window has: screenshots (Ctrl+V, dragging, "Zrób zrzut") and the "Chmura" / "Lokalny" model (PT-43, decision 73). **YouTrack both ways** (PT-7): a project with a YouTrack key (e.g. LOG, LEGAL — in "Projekty i tagi") synchronizes the issue state with the card column and the state of tickets in `tracker: youtrack` plans (To do → Open, In progress → In Progress, Waiting → To be discussed, To check → Fixed, Done → Verified, a return → Reopened), only for transitions seen by Tide, without overwriting manual changes in YouTrack (a conflict with the choice "Weź z YouTracka" (Take from YouTrack) / "Nadpisz z Tide" (Overwrite from Tide)) and with a queue when YouTrack on the local network does not respond; "Importuj z YouTracka" (Import from YouTrack) creates cards in Backlog from your issues, without duplicates. The "Praca" tile in the dock (on the right, before AI Limits) and in the sidebar (PT-40, replaced the Board and Agents tiles): board cards and agent sessions without a card in one service list — the numbers are always the same as on the Board; a session without a card is turned by "+ karta" into a card linked to the session (decision 76). The former Board tile (`org.tide.board`, with the field "Dopisz do backlogu i Enter" (Add to backlog and Enter)) stays installed until removal. Details: [docs/tide-daemon.md](docs/tide-daemon.md#board), rationale: decision 57.

**Docker.** A custom container view instead of Docker Desktop: the `/Docker` object (`org.tide.Daemon.Docker`) provides containers grouped by compose projects with an assigned repository, ports with links, containers that crashed (e.g. exit code 137 or a failed start), start, stop and restart individually or for a whole project, logs and a shell in a Konsole tab, CPU and RAM on demand and cleanups (build cache, unused images, volumes, stopped containers) — only of the selected categories. A read-only preview: `/usr/bin/python3 daemon/tide-daemon.py --print-docker`; turning it off: `--no-docker`. The **Docker** view in the Tide window: compose projects with a repository, containers with ports (HTTP as a link), CPU and RAM, logs in a drawer (stderr highlighted, "Otwórz w Konsoli" (Open in Konsole)), a shell in Konsole, start, stop, restart and removal (with confirmation) individually or for a whole project, and cleanups — the confirmation waits for a fresh disk space reading (`DiskNow`) and lists exactly what will disappear. The "Docker" tile in the dock on the left, right after System ("Docker · 6 projektów · ● 9 działa · ● 1 padł" (Docker · 6 projects · 9 running · 1 crashed)), a popup with projects, ports and containers that crashed; it is not in the sidebar (no room next to the board). Details: [docs/tide-daemon.md](docs/tide-daemon.md#docker), rationale: decision 58.

**Reports in the Tide window.** The **Raporty** (Reports) view: on the left, days from the last 30 (a peach dot for a new one), in the middle, projects with activity (first those where something waits for you) with tags from the board, on the right, the "Na daily" of the selected project with "Kopiuj jako tekst" (Copy as text); "Wygeneruj teraz" (Generate now), "Otwórz w przeglądarce ↗" (Open in browser). Viewing a new report in the window also clears the dot in the widgets. Reports from before this change are on the list, but "Na daily" is shown only by the browser. Rationale: decision 63.

**Local models in the Tide window.** The **Modele** (Models) view configures local models without editing files (the service saves them, `/Models`). *Model servers*: a card per server — "Ten komputer" (This computer) or a name from the settings (e.g. "Mac-Studio"), status (działa / nie odpowiada / sprawdzam… — running / not responding / checking…), the Ollama version, the number of models, the model in memory and how much of it fits on the graphics card, the model queue ("Pracuje: raport (qwen3:8b) · 2 w kolejce: opisy kart" (Working: report (qwen3:8b) · 2 queued: card descriptions), and after an address change also what is still running on the old one — `queue` in `Status()`); Ollama cloud models (`…:cloud`, `…:120b-cloud`) are marked "w chmurze Ollamy" (in the Ollama cloud), are not available for selection in roles, and "Pobierz model" (Download model) warns on such a name — Tide does not use them, because the facts would leave the computer; the "Używany" (In use) marker and the **"Używaj tego serwera"** (Use this server) button — this is the "this computer / Mac" switch: roles without a pin follow it. "Sprawdź połączenie" (Check connection), "Edytuj" (Edit) (address, Ollama or an OpenAI-compatible server, a token with a preview and "Usuń token" (Remove token); the window never knows the saved token — an empty field does not change it) and "Usuń" (Remove) with confirmation; "Dodaj serwer" (Add server) with "Sprawdź" (Check) before saving — this is where you add the Mac. *What we use models for*: the morning report and card descriptions — Claude, Lokalnie (Local) or Bez modelu (No model; works immediately), and for the roles Raport (Report), Opisy kart (Card descriptions), Agent (for the "Lokalny" option in the New task window; for Agent also the program: Codex — Ollama supports it directly — or Claude Code) and Wyszukiwanie (Search; searching sessions by meaning in KRunner: Włączone / Wyłączone (On / Off) and one index status line, e.g. "Gotowe: 412 sesji w indeksie · 28.10, 10:02" (Ready: 412 sessions in the index · 28.10, 10:02)) a model from the list installed on the role's server, a warning with "Pobierz teraz" (Download now) when it is not there, and "Zaawansowane" (Advanced): pinning to a server or "za domyślnym" (follow the default) and context. *Models on the server*: a list with the size and what is in memory, "Pobierz model" with suggestions from our measurements and progress. *Where work goes, Executors, Other providers* (PT-60, under the roles): in one place, what goes local and what goes to the cloud (the report and card descriptions according to the report mode, each executor from the catalog, an unavailable local executor with a reason), the executor catalog with adding, changing and removal (name, provider, program, model, local server, classes) and other providers (an OpenAI-compatible or Anthropic API address, a token in a file). Errors and refusals from the service appear next to the control that caused them. A "● N" badge in the bar when something Tide uses does not work (e.g. an agent on the Mac that does not respond). The "Modele" tile opens this view. Preview: `make preview-models-view` (`build/models-view-<state>.png`). Rationale: decision 69.

**Morning report.** `make install-daemon` (and `install-krunner` for the word "raport"): once a day — at the first login or at 07:00 if you are already logged in — the service assembles in `~/.local/share/tide/reports/YYYY-MM-DD/` an `index.html` and an HTML page for each repository project with activity or work in progress (the state of work for the day: even after a quiet night you can see what is running and what waits). The index is an overview: a project card with 1–2 sentences of "what happened", counters (done, in progress, waiting, housekeeping) and what waits for you — projects with something for you to do are on top; sessions outside repositories are in a collapsed "Poza projektami" (Outside projects) section. At the top of a project page is its **"Na daily"** (For the daily): *Zrobione* (Done; what exactly was created or changed and why — so that you can say it to someone who does not know the code), *W toku* (In progress; how far along and the next step), *Czeka na mnie* (Waiting for me; questions, decisions, failing tests), *Dalej* (Next; `todo` tickets from the plan), a paragraph "Na czym polega" (What it is about) and a collapsed *Porządki* (Housekeeping; tracker, plans and agent instructions, configuration — separately from real work), with a **"Kopiuj jako tekst"** button for reading at the daily or pasting into a chat. The raw data — commits by branch, Claude and Codex sessions with the last message, scheduled task runs, plans and tickets, worktrees, changes in the main directory, AI Limits — is lower down, collapsed under "Szczegóły" (Details). One notification "Raport poranny gotowy — N projektów" (Morning report ready — N projects) arrives with "Otwórz" (Open), and the report stays at hand in the widgets: in the sidebar header "Raport · dziś 07:00" (Report · today 07:00) (`make install-widget-sidebar`) and in the bottom row of the Work tile popup in the dock (`make install-widget-work`), with a peach dot until you open it — from a widget, a notification or KRunner. A report on demand: `qdbus6 org.tide.Daemon /Reports org.tide.Daemon.Reports.Generate`. Reports are kept for 30 days; settings in `~/.config/tide/report.json` (`{"at": "07:00", "summary": "claude", "summaryModel": "sonnet"}`). Details: [docs/tide-daemon.md](docs/tide-daemon.md#morning-report).

*The "Na daily" description and privacy.* By default (`"summary": "claude"`) the text is written by a model through your local Claude Code: one `claude -p --model sonnet` call per report (about 30 s), without tools, without CLAUDE.md, hooks, plugins and MCP, without saving the session, with a 180 s limit. The model gets only concise facts (IDs, titles and the beginnings of ticket descriptions, plan goals, states, review results, commit topics, areas of changed files, session titles and first commands, the first sentences of the agents' last messages, questions) — at most about 16 thousand characters per project, after removing everything that looks like a secret (keys, tokens, passwords, `.env` lines), with `~` in place of the home directory. This data goes to Anthropic the same way as in ordinary work with Claude Code and counts toward your limits: usually a few to a dozen or so thousand input tokens once a day (a repeated `Generate()` with the same facts takes the answer from `summary.json` in the report directory). Without the `claude` program, after an error, after a timeout or on an answer that does not match the schema — and always with `"summary": "rules"` — the report builds the description from rules, without sending anything. With `"summary": "local"`, "Na daily" and card descriptions are written by a local model from `~/.config/tide/models.json` (the roles `summary` and `describe`, by default `qwen3:8b` in Ollama on this computer; `summaryModel` is then ignored): the same facts, instructions and validation, an answer according to a JSON schema, one call per project — and nothing leaves the computer unless the role points to a server on the network (e.g. a Mac Studio), in which case the facts go only to it. Local mode never falls back to Claude: when the server is down or the model answers badly, the report and cards get a rules-based description with the note "Model lokalny niedostępny — opis bez modelu (serwer nie odpowiada, 127.0.0.1:11434)" (Local model unavailable — description without a model (server not responding, 127.0.0.1:11434)).

The "Tide · Praca" tile is placed in the dock and in the sidebar by `make install-dock` and `make install-sidebar` (reinstalling removes the Board and Agents from them; without Work installed, they put them back — rollback: `make uninstall-widget-work install-dock install-sidebar`); each widget can also be added from the widget list. After updating an already added widget, Plasma loads the new code only after `systemctl --user restart plasma-plasmashell`. The colors of the QML library are generated from the palette when the package is assembled (`widgets/stage.py` → `Tokens.qml`).

## AI limits and the local model (the "AI" tile)

The "Tide · AI" widget (`org.tide.limits`, "Limity AI" (AI Limits) until PT-41) shows in the "AI" tile the remaining percentage of ChatGPT and Claude (the lower of the session and weekly limits) and the local model dot, and in the popup both limits with resets and a "Modele lokalne" (Local models) section.

```
┌ AI · Claude do jutra 14:30 ───────┐
│ GPT 100%  Claude 30%  ● lokalny   │
└───────────────────────────────────┘
```

**Local model.** A dot and a short word in the percentage line, from the state of the default model server from tide-daemon (`/Models`, the same words and order of states as the former Models tile, decision 68): "● lokalny" (local) — running (mint), working or downloading (teal); the warning color only when the server does not respond ("● lokalny nie działa" (local not running), pink), the model does not fit on the card ("● lokalny na CPU" (local on CPU), butter) or the model of the report or card description role is missing ("● lokalny bez modelu" (local without a model), butter). Without tide-daemon, before the first reading and without a server in the settings there is no dot; when there is one, the tile has a fixed width (room for the longest word). An Ollama cloud model in memory does not change the state — Tide does not use it. The popup: a "Modele lokalne" section — the server with a state dot, the reason for unavailability, the model in memory and how much of it is on the card (with the release time), the queue and downloads, role and settings file problems — and "Otwórz Modele ↗" (Open Models; the Models view in the Tide window, where all servers, roles and the report mode are). In the sidebar, under the limits, a row "Lokalny ● qwen3:8b · na karcie 100%" (Local ● qwen3:8b · on card 100%) with "Modele ↗". Preview: `/usr/bin/python3 widgets/preview_ai.py` (`build/preview/ai-*.png`).

The tile takes limit data directly from the local API of the [QuotaApp](https://github.com/julia-lakomy/QuotaApp) service (`limity-ai-widget.service`) — the service must be installed and running.

**The forecast and notifications** come from tide-daemon: every 2 minutes it reads QuotaApp's `/status` (without loading pages), saves the reading history (`~/.local/state/tide/limits-history.jsonl`, 14 days) and from the pace within the current window computes when the limit will run out. The popup, under each limit's reset, writes "wyczerpie się ok. 17:40" (will run out around 17:40; peach) or "starczy do resetu" (will last until the reset), the sidebar appends "· koniec ok. 17:40" (ends around 17:40) / "· starczy do resetu", and the tile in the dock gets a short "· Claude do 17:40" only when the limit will run out before the reset. With too few readings there is no forecast. The notification "Claude: zużyto 82% limitu sesji" (Claude: 82% of the session limit used) arrives once per window at 80% and 95%, and "… skończy się przed resetem" (… will run out before the reset) — once, when the forecast says more than 30 min before the reset; a service restart does not repeat them. The thresholds are in the palette (`limits` in `theme/palette/mist.json`). Without tide-daemon the widget works as before, just without forecasts. Details: [docs/tide-daemon.md](docs/tide-daemon.md#ai-limits).

```bash
make install-daemon install-widget-limits   # forecast: the service and the widget (new widget code after a plasmashell restart)
qdbus6 org.tide.Daemon /Limits org.tide.Daemon.Limits.Forecast
```

```bash
make install-widget-limits     # install or update the widget
make swap-limits-widget        # replace the old QuotaApp widget in the bottom panel (backup first)
make uninstall-widget-limits   # removes only org.tide.limits
```

**Token.** The API port and token are in `~/.config/limity-ai-widget/config.json` (created by the QuotaApp installer, mode 0600). On installation `widgets/stage.py` copies them into `contents/code/Config.js` in the package, with mode 0600; `widgets/install.sh` deletes that file from the assembled copy in `build/` right after installation. The token never reaches the repository. After a token change, a repeated `make install-widget-limits` is enough.

**Replacing in the panel.** `make swap-limits-widget` makes a backup and, through Plasma's `evaluateScript`, removes `org.limityai.widget` from the bottom panel and inserts `org.tide.limits` in the same place (`TIDE_PANEL_SCREEN=n` limits it to screen n). The script without executing: `bash scripts/swap-limits-widget.sh --print`. A panel with locked widgets must be unlocked first.

## Models

Since PT-41 the local model is shown by the "AI" tile (above), and the "Tide · Modele" tile (`org.tide.models`) no longer sits in the dock: `make install` still installs the package (to bring it back, add it to `DOCK_RIGHT` in `scripts/dock.py`), and reinstalling the dock removes it from the panel — also when you added it to the dock by hand (`make install-dock` removes tiles retired from the dock as long as they are in neither `DOCK_LEFT` nor `DOCK_RIGHT`). The tile shows the state of the default local model server from tide-daemon (`/Models`, `Status()` after the `Changed` signal, [description](docs/tide-daemon.md#local-models)): "● qwen3:8b · na karcie 100%" (the model in memory; butter when part of it is on the processor), "● gotowe" (ready), "● pracuje: opis kart · 2 w kolejce" (working: card description · 2 queued; teal), "● serwer nie odpowiada" (server not responding; pink), without the service muted "usługa nie działa" (service not running). The popup: a card for each server ("Ten komputer" for a local address, the address, the version, the reason for unavailability, the queue — also calls still running on the endpoint's old address after its change ("Na starym adresie …: pracuje: raport" (On the old address …: working: report), the field `queue.<endpoint>.other`), downloads, Ollama cloud models marked "w chmurze Ollamy" ("Pobrane: 6 modeli, w tym 1 w chmurze Ollamy" (Downloaded: 6 models, including 1 in the Ollama cloud)), models in memory with the card share and the release time, the number of downloaded ones), roles with the model and availability, the report mode, "Odśwież" (Refresh; `Refresh()`) and "Otwórz w Tide ↗" (Open in Tide). The fields `report`, `pulls` (downloads by the key `"<endpoint>/<model>"`) and `queue` are optional — without them the tile does not show them.

"Otwórz w Tide" calls the window directly: `org.tide.Window /Window Show("models", "")`; when the window is not running, it starts it like the menu (`kstart --application tide`) and retries `Show`. As long as the window has no Models view, `Show` returns `false` — the window comes to the front on its own view (`Show("", "")`), and the popup says the view will arrive with a new version of the window.

```bash
make install-widget-models     # install or update the widget
make uninstall-widget-models   # removes only org.tide.models
/usr/bin/python3 widgets/preview_models.py   # the tile and popup on sample data: build/preview/models-*.png
```

## System

The "Tide · System" widget (`org.tide.system`) shows in the "System" tile three gauges with a percentage and a thin bar ("CPU 34%  RAM 61%  Dysk 88%" (Disk 88%)): processor (teal), memory (lavender) and the fuller of the / and /home partitions (peach). The popup and the sidebar have a processor chart for the last minute (a sample every 2 s), memory with the three top programs, the graphics card with VRAM and both partitions with free space.

```bash
make install-widget-system     # install or update the widget
make uninstall-widget-system   # removes only org.tide.system
/usr/bin/python3 widgets/system/contents/code/sysinfo.py mounts / /home   # mapping of partitions to sensors
```

**Data.** The numbers come from the sensors of the `ksystemstats` service (the QML module `org.kde.ksysguard.sensors`), the same ones as in System Monitor: `cpu/all/usage`, `memory/physical/used` and `total`, `gpu/gpu0/usage`, `usedVram`, `totalVram` and `name`, `disk/<UUID>/used`, `total` and `free`. We compute the memory and disk percentage from `used/total`. Without the `ksystemstats` package the widget shows "Zainstaluj pakiet ksystemstats" (Install the ksystemstats package) instead of zeros, and takes disks from `statvfs`. We install the package only after `apt-get -s install ksystemstats`.

**Partitions.** Disk sensors have the file system UUID in their name, not the mount point. A small helper `contents/code/sysinfo.py`, run from the widget by the Plasma5Support `executable` engine (every minute), reads `/proc/self/mountinfo`, finds the partition containing `/` and `/home`, and its UUID in `/dev/disk/by-uuid` — the same way ksystemstats names the sensor (without a UUID, the label, then the device name). Nothing is hard-coded, so after a partition change a Plasma restart is enough. If /home is not a separate partition, the widget shows one.

**Processes.** The same helper (`top 3`, every 4 s, only while the popup is open) sums the private memory of processes (resident minus shared, i.e. without shared libraries shared with other processes) by the program name from `argv[0]`, so all Firefox processes count together.

**A hint at a full disk.** For a partition more than 85% full, the widget asks `tide-daemon` for the `/Worktrees` object (`org.tide.Daemon.Worktrees.List`) and writes "Powyżej 85%: worktrees zajmują X, z czego Y można zwolnić" (Above 85%: worktrees take up X, of which Y can be freed) — worktrees lying on that partition count (without the main directories of repositories), Y is what cleanup would remove. When the service does not have this object or is not running, just "Dysk prawie pełny" (Disk almost full) remains.

**Buttons.** "Monitor systemu" (System Monitor; plasma-systemmonitor, KSysGuard as a fallback) and "Analiza dysku" (Disk Analysis; Filelight, opens the fullest partition) appear only when the application is installed; they start through `kstart --application`.

## Stream (Streamer core widgets)

The Stream activity widgets (PT-37, mockup accepted 28.10.2026) read the stream state from the Streamer core
(`~/DevWorkspace/streamer/core`, the user service `streamer-core`) over D-Bus: the service `org.streamer.Core`,
the object `/org/streamer/Core`, the interface `org.streamer.Core1`. This is a deliberate exception to "one daemon" (decision 79):
the stream state belongs to the core, and work data still comes only from tide-daemon.

- **Stream** (`org.tide.stream`, Stream dock): "Stream · Xbox" and "● poza anteną · ● OBS" (● off air · ● OBS), live "Stream · Xbox ·
  scena Gra" (scene Gra) and "● na żywo · ● nagrywa" (● live · ● recording). The warning color (`neutral` from the palette) only with a muted microphone
  while live ("Stream · na żywo", a crossed-out microphone, "mikrofon wyciszony" (microphone muted)), a disconnected OBS during a stream
  and a core that is not running ("rdzeń nie działa" (core not running), when the service is not on the bus). Without OBS and without a stream the tile says
  calmly "Granie · Xbox" (Gaming · Xbox; in Observer and Rozmowa (Talk) "Stream · …") and "bez streamu" (no stream) (PT-83). The popup: the Stream card — the mode and who set it (automatic or you),
  the air state, recording, the scene, OBS, modules, "Wycisz mikrofon · Pause" (Mute microphone · Pause; `mic.set` with the target state), "Panel streamera"
  (Streamer panel; `http://127.0.0.1:8765/app/`) and a note that starting and ending a stream are only in the Panel.
- **Player profile** (`org.tide.stream.profile`): the current profile with a "▾", a popup with the `profiles` list (`profile.set`).
- **Observer** (`org.tide.stream.observer`): "Mascot Cup · BO3 · gra 2" (game 2) and "A 1 : 0 B"; only in Observer mode
  (in another mode and without the core, `HiddenStatus`, the panel hides it). A click opens the Streamer panel.
- **Camera** (`org.tide.stream.camera`, PT-82, decision 83): "Kamera · automat" (Camera · automatic) or "Kamera · ustawiłeś ty" (Camera · you set it)
  and "● widoczna" (visible) or "○ ukryta" (hidden) (`core.modules.camera`). A click immediately, without a popup or confirmation, sends
  `module.set {module: "camera", visible: …}` with the inverted value (the target state, not a blind toggle);
  the core hides the camera in all scene elements that depend on the module, and a mode change restores the default
  visibility of the mode. No warning color: with a disconnected OBS "bez OBS" (no OBS; the core remembers the change and sets
  OBS after it connects), without the core "rdzeń nie działa", a command error shown briefly instead of the value in `textSecondary`.
- **"?"** (`org.tide.stream.help`): a cheat sheet with Stream shortcuts in the popup, never permanently on screen.
- **Stream console** (`org.tide.stream.scenes`, left monitor desktop, when there is no desk): Sceny (Scenes) — a preview of the current scene with "NA ŻYWO" (LIVE)
  and the mode's scene buttons (`scene.set`; BRB only with a separate button, the start and end live scenes disabled
  with a reason) — and the full Stream card: the air state, OBS with recording, the player profile with a list, module
  toggles (`module.set`), "Wycisz mikrofon", BRB (`brb.toggle`) and the Streamer panel. The scene screenshot refreshes only in the current
  activity and while the widget is visible.
- **Twitch chat** (`org.tide.stream.chat`, left monitor desktop, when there is no desk): a large font (1.5 × the palette size, about
  20 px), newest at the bottom, alerts inline, read-only; the nick in a palette pastel different from the warning
  and error colors (`semantic.neutral`, `semantic.negative`).
- **Left Stream monitor** (`org.tide.stream.desk`, PT-83, decision 82): one widget for the whole left monitor desktop
  with two views. The **Konsola streamu** (Stream console) is Scenes with the Stream card and chat (the same components as above), **Granie** (Gaming) —
  player profiles (the active one highlighted, a click on another is `profile.set`; rank, rating, form, when the core has them),
  the day's balance, "Mecz teraz" (Match now) from the `core.match` field (You and the opponent: civilization, rank, rating, win percentage,
  form) and the active profile's last matches. The view is chosen automatically: OBS connected → console, OBS off →
  Gaming, live always the console. The "Konsola streamu | Granie" switch at the top shows who chose the view
  ("automat · OBS wyłączony" (automatic · OBS off) or "ustawiłeś ty · do zmiany OBS" (you set it · until the OBS change)); a manual choice holds until the next change of OBS
  state and does not hide the live console.
  The console in the desk has the layout of variant A (PT-105, `docs/agents/specs/k-29-panel-categories.md`): the left column is the
  **Zawsze** (Always) card (microphone, BRB, privacy, hiding graphics and "Wynik u widzów" (Score for viewers) — `observer.overlay.set`) above Scenes
  in groups from `situation.scene_groups` (a padlock on a group with a locked scene), the right is a row of seven categories
  and the **Teraz** (Now) cards from `situation.cards`: an auto/pinned marker, a pin (`panel.pin`), × (`panel.hide`),
  restoring hidden ones from the header and "Dodaj kartę" (Add card) from `situation.addable`. A score card without a series has the fields
  "Nowa seria" (New series; `observer.series.start`), the Stream card — state only. A core without these fields gives flat scenes
  and cards without pinning.
  A category opens an opaque popup from the category row to the bottom of the column (PT-106; Esc, × or the same icon
  closes it, longer content scrolls inside) with the same settings and locks as on the tablet:
  **Przygotowanie** (Preparation; mode — live only in the Panel, player profile, broadcast target, recording, co-commentator,
  a pre-start check with a result), **Sceny i ekrany** (Scenes and screens; the texts Starting soon, Be right back and Thanks for watching,
  a countdown, the topic in the Browser scene), **Wynik i seria** (Score and series; +1, −1, undo, swap, a new series only without a series or after
  it ends, the card on the panel, score for viewers, event), **Na ekranie** (On screen; modules and the muted
  microphone warning), **Alerty** (Alerts; types, sound, tests), **Animacje żubra** (Mascot animations; transition, alert, peek, herd,
  frequency, places, tests) and **Urządzenia** (Devices; paired devices with disconnecting after confirmation, Twitch
  and OBS state). Text fields are saved by Enter or "Zapisz" (Save), and the state from the core does not overwrite a field in which something was typed.
  The content is computed by `Stream.categoryContent`, the popup (`CategoryPopup.qml`) only draws it. In the Streamer panel
  starting and ending a stream, changing the live mode, resetting the score and uploading the event logo remain.

**Talking to the core.** One `CoreSource` per widget: `GetState()` at startup and after every appearance of the service on the
bus, then only the `Changed` signal (it carries the whole view in `state`), plus a fallback read every 5 min. When the core
disappears, the widgets immediately say "rdzeń nie działa"; when it returns, they read the state again on their own. Commands go through
`Execute` solely from the `Stream.ALLOWED` list (among others `mic.set`, `profile.set`, `module.set`, `scene.set`,
`brb.toggle` and the console category settings) — critical actions (starting and ending a stream, ending a recording, changing the
live mode, resetting a series) are not in the widgets; `mode.set` passes only off air (`Stream.allowedNow`).

**What the core does not provide yet.** Lists of mode scenes, a preview of the current scene and chat with alerts over D-Bus,
and for the Gaming view an overview of all accounts, recent matches, the day's balance and match data in PC mode (a proposed
set of fields in decision 82: `core.player_stats`, `core.recent_matches`, `core.today`). The console, chat and Gaming then show
"jeszcze niedostępne w rdzeniu" (not yet available in the core), and `CoreSource` and `stream.js` already have room for this data
(the API proposal in decision 79): `core.mode_scenes` (and optionally `obs.scenes`) in the state, `GetSceneScreenshot(s)`,
`GetChat()` and the `Chat(s)` signal, and urgently `obs.mic`. Without mode scenes the console shows no scene buttons, only the
current scene. The tests check both versions on a core stand-in (`widgets/tests/fake_kamp_core.py`, always on a
private `dbus-run-session` bus).

**Installation.** `make install-widget-stream` installs eight packages (also in `make install`), but places nothing
on panels or the desktop — placement is done by entering the Stream activity (below: "Stream activity"). Colors come from the palette, so the widgets work
with Mist and with the Streamer variant.

```bash
make install-widget-stream     # install or update the eight Stream packages
make uninstall-widget-stream   # removes only the org.tide.stream* packages
make preview-stream            # tiles, popups, the console and Gaming at 1920×1080 on a fake state: build/preview/stream-*.png
make preview-stream PALETTE=theme/palette/streamer.json   # with the Streamer variant — the palette file is supplied by PT-34
```

## PC ⇄ Xbox monitors and Stream shortcuts

Two things from PT-38 (decision 81), both in tide-daemon:

- **The right monitor's input.** The "Monitory · PC → Xbox" (Monitors · PC → Xbox) tile (`org.tide.stream.monitors`, in the Stream dock) opens
  a confirmation. **Xbox:** the right monitor DP-2 switches through DDC/CI to the HDMI input with the HD60 X passthrough
  (`ddcutil setvcp 60 0x11`). **PC:** it returns to DisplayPort (`0x0f`). The screen layout in KWin does not change, so the
  Stream dock and the console stay on the left monitor. Consequence: in Xbox mode the cursor, a new window or a notification may
  land on the right screen, which you then cannot see — the cursor comes back by moving left. After leaving Stream the monitor
  returns to PC on its own, so work always has both screens. Without `ddcutil`, or when the monitor does not respond to DDC, the widget asks
  you to switch the input with the button on the monitor. The input codes are in `daemon/tide_daemon/monitors.toml` (the
  HDMI socket with the Xbox to be verified: HDMI-1 is `0x11`, HDMI-2 is `0x12`). No keyboard shortcut.
- **Shortcuts only in Stream.** `Pause` — microphone (`kamp mic.toggle`), `Meta+Pause` — the privacy screen
  (`kamp privacy.toggle`), `Meta+B` — BRB and return (`kamp brb.toggle`), `Meta+F9` — start/pause of the game plan clock,
  `Meta+F10`/`Meta+F11` — plan clock −/+5 s (these three are handled by the Stream desktop widget, decision 90). The service registers
  them in KGlobalAccel only after entering the Stream activity and removes them after leaving, so at work the keys behave as
  before. For the duration of Stream, `Meta+B` is taken from "Przełącz profil zasilania" (Switch power profile), and `Meta+F9` and `Meta+F10` from KWin's "Prezentacja
  okien" (Present Windows); on return they are restored exactly (saved in `$XDG_STATE_HOME/tide/stream-shortcuts.json`). `Meta+Shift+A`
  (Work ⇄ Stream), `Meta+A` and `Meta+Q` are unchanged.

```bash
make install-widget-monitors   # the tile package (its place in the Stream dock — the Stream panel layout)
make preview-monitors          # the tile and confirmation: build/preview/stream-monitors-*.png (Streamer palette)
make monitors-status           # the right monitor's input according to the service state (read-only, no DDC)
qdbus6 org.tide.Daemon /Monitors org.tide.Daemon.Monitors.SetLayout xbox   # the same as the button (pc | xbox)
```

## The Plasma style and top bars

The "Tide" style contains only frames of the new shape: the panel background (the bar with the prefix `north` and the bar radius, the dock with the prefix `south` and the sidebar with the prefix `west` with the dock radius), popups with a header and footer, and tooltips — in the regular, `translucent/`, `solid/` and `opaque/` versions, as in Breeze. Plasma takes the rest from Breeze, because "default" is always the last fallback theme (additionally `FallbackTheme=default` in `plasmarc`). Colors still come from the scheme: the style has no `colors` file, and the fills have `ColorScheme-*` classes. License and origin: `NOTICE` in the style directory (LGPL-2.0-or-later, like Breeze).

```bash
make preview-plasma-style                                      # a preview in build/preview, without installing
/usr/bin/python3 theme/plasma/ksvg_check.py theme/palette/mist.json build/ksvg.png   # the real KSvg, off screen
make show-panels                                               # the plasmashell scripts the installation will run
```

Top bars (`scripts/panels.py`): on the primary monitor (DP-2) the existing top panel becomes a floating pill — a menu with the Tide logo (the `tide-logo` icon in hicolor), the global menu, a clock with the date in the middle and the system tray. On the left monitor (HDMI-A-1) a slim bar with the global menu and a clock is created. Both menus show only windows from their own monitor. We recognize monitors by their connectors (`TIDE_MAIN_OUTPUT`, `TIDE_SIDE_OUTPUT`), and the sidebar by the marker `[Tide] role=side-bar` in the panel configuration. Before the first change the panel layout goes to `~/.local/state/tide/panels-previous.json` (together with the `.desktop` files of the "Ikona" (Icon) widgets), and `make uninstall` restores the main bar from it and removes the side one. The full layout is also in the backup made by `make install`.

### Global menu

It works through the kded module `appmenu` (the installation turns it on if it was off) and the registry `com.canonical.AppMenu.Registrar`, which appears when a global menu widget is on the panel. Applications check this at startup, so those launched before the installation must be restarted.

| Application | Global menu |
| --- | --- |
| KDE/Qt applications (Dolphin, Kate, Konsole, Okular, System Settings) | yes — the menu over D-Bus (dbusmenu) and the KWin appmenu protocol |
| Rider and other JetBrains IDEs | probably yes, as long as they run through XWayland (the default): the IDE registers the menu in `com.canonical.AppMenu.Registrar`. With native Wayland (`-Dawt.toolkit.name=WLToolkit`) the menu stays in the window |
| Firefox | no — it draws its own menu bar and does not export it; the menu (Alt) stays in the window |
| GTK 3 applications | depends on the application: through `appmenu-gtk-module` (installed) and `gmenudbusmenuproxy`, most likely only under XWayland |

To check after installation: Dolphin, Rider and Firefox on both monitors.

## Sidebar

On the left monitor (HDMI-A-1), at the left outer edge, there is a hideable Tide sidebar (`scripts/sidebar.py`, the `install-sidebar` target at the end of `make install`): a vertical, floating panel `size.sidebar` (420 px) wide, from the bottom of the slim bar to the bottom of the screen. It is visible as long as nothing covers it ("avoid windows" — it does not take space from windows); when a window moves over it, it hides, and it slides out when the mouse touches the left edge of the screen or on **Meta+S** (after the shortcut: for 8 s without the mouse over the bar, or until a second Meta+S). From the top: the "Tide" header with the shortcut and four separate tiles — Work (board cards and sessions without a card, one line per item, what is waiting first, with "Przejdź" (Go), at most 5 rows and "+N więcej" (+N more) with the full list next to the bar, "Tablica" (Board) and "Sesje" (Sessions)), System (CPU, RAM and disk with bars, a small processor history, the graphics card on one line, partitions), worktrees ("● 2 w pracy · ● 0 do sprzątnięcia" (● 2 in use · ● 0 to clean up) and "Otwórz okno" (Open window)) and AI Limits (GPT and Claude: the lower limit with a bar, reset and a forecast from tide-daemon). Free space stays at the bottom (a stretching spacer at the end of the panel), and the whole thing fits in about 1040 px without scrolling. A preview without Plasma: `make preview-widgets` (`build/preview/sidebar-*.png`, with the Work tile: `work-sidebar-whole.png`).

```bash
make install-widget-sidebar install-sidebar   # the bar itself (the Tide widgets must be installed)
make show-sidebar                             # the plasmashell script the installation will run
make sidebar-preview                          # a nested session: hidden, slid out by the shortcut and hidden again → build/sidebar
make uninstall-sidebar                        # removes the panel and releases the shortcut
```

- **Bar tiles.** Widgets in the bar have `sidebarMode=true` in their configuration (`[Configuration][General]`), so Plasma shows their full representation right away instead of a tile — in bar mode it is a compact tile (`SidebarWork`, `SidebarSystem`, `SidebarWorktrees`, `SidebarLimits` on the shared `SidebarTile`). The dock tiles and their popups do not change. Work in the bar does not report "needs attention", because a hideable panel would then never hide — the dock and the notification say when an agent is waiting.
- **Meta+S.** This is the global shortcut of the header widget `org.tide.sidebar` (in KGlobalAccel: the component `plasmashell`, the action `activate widget <id>`), changeable in its settings (right-click on the header → Keyboard Shortcuts). When Meta+S already belongs to another action, the installer prints this and does not assign the shortcut. Details in decision 34.
- **After the first installation** Plasma loads the new widget code (bar mode) only after `systemctl --user restart plasma-plasmashell` or logging in again.
- The panel has the marker `[Tide] role=sidebar`; a repeated installation uses the same panel and the same widgets, and the width also goes to `plasmashellrc` (like the thickness of the top bars).

## Worktrees

```bash
make install-daemon install-widget-worktrees install-tide-window
make preview-tide-window   # screenshots of the views, including build/tide-window-worktrees.png (read-only)
```

The "Tide · Worktrees" tile in the dock shows how many worktrees can be cleaned up without risk and how many GB that is (mint), and below it how many are in use (teal). A click opens the **Tide** window on the Worktrees view (its own icon and name in the taskbar, also in the application menu). The window divides worktrees into three groups:

- **W pracy** (In use; teal) — an agent is working in it or something changed in the last day and the work is not finished yet (not merged, changes, or a fresh worktree without its own commits); opening in Konsole or Rider, and those without a running agent can also be marked for removal (like "Do decyzji"),
- **Do sprzątnięcia** (To clean up; mint) — merged into the **local** main branch (master, main or develop, without syncing with the server), without changes and without an agent. A worktree with its own commits (visible in the branch reflog) lands here right after the merge, without waiting a day. Each has a checkbox; "Usuń zaznaczone" (Delete selected) first lists exactly the directories and branches that will disappear. Removal without `--force`, the branch only through `git branch -d`, and the service checks each worktree once more right before removal,
- **Do decyzji** (To decide; butter) — standing for more than a day, but with unmerged commits or changes; you can see why ("4 commity · 3 zmiany" (4 commits · 3 changes)). They can be opened or marked (the checkbox turns red). Those older than 30 days are muted.

When the selection includes a worktree from outside "Do sprzątnięcia", the confirmation is stronger: for each one it lists what will be lost (uncommitted changes, commits outside master and how many of them have no copy on the server or in another branch, the fate of the branch, the linked session, the last change), has a field "Usuń też gałąź (N niescalonych commitów przepadnie)" (Also delete the branch (N unmerged commits will be lost)) and a red button "Usuń mimo to" (Delete anyway). The service then removes with `git worktree remove --force` and leaves the unmerged branch, unless the field is checked (`git branch -D`); the result says for which ones the branch was kept. It never — even this way — removes the main directory of a repository, a worktree on the main branch, a locked one, one in which an agent is working (close the session first), or one in whose directory other worktrees lie (e.g. `.claude/worktrees/` — they would disappear with it; first remove those inside). A preview of the confirmation without the service: `tide-worktrees.py --data file.json --select PATH --confirm --grab screenshot.png`.

A row says what the worktree is: the tool (Codex, Claude — from the session that is working in it now, otherwise from the last one), a title from the branch name without the prefix (`codex/summary-tabs-03-version-axis` → "summary-tabs 03 · version axis"), the topic of the last commit or the title of the linked agent session, the last activity and the size. Worktrees on the main branch are in a collapsed section "Gałęzie główne" (Main branches) and are never cleaned up, and entries left by manually deleted directories are tidied by the `git worktree prune` button next to the repository. The projects directory is `~/DevWorkspace` (to change it: `TIDE_WORKSPACE` in the service environment or `--workspace`).

## Global theme and lock screen

The "Tide" global theme (`org.tide.desktop`) ties together in System Settings what the other parts install: the TideMist scheme, the Tide Plasma style, the Tide Aurorae decoration (buttons on the right: minimize, maximize, close), Tide icons, the Nordic-cursors cursor and Geist fonts. It contains no layout or wallpaper scripts — panels and wallpapers are set by `scripts/panels.py` and `scripts/wallpaper.py`.

`make install` (the `install-lookandfeel` target) copies the theme to `~/.local/share/plasma/look-and-feel/org.tide.desktop` and saves only the theme choice (`kdeglobals [KDE] LookAndFeelPackage`, automatic theme change turned off) and the lock screen wallpaper (the Tide image of the primary monitor in `kscreenlockerrc`). It **does not call `plasma-apply-lookandfeel`** and does not touch panels. The previous values are in `~/.local/state/tide/lookandfeel-previous.json`, and `make uninstall` restores them.

In System Settings → Global Theme you can click "Tide", but **do not check "Desktop and window layout"**: Plasma then wipes the whole panel configuration (`plasma-org.kde.plasma.desktop-appletsrc`) and loads the Breeze layout. The rescue: `make restore NAME=<backup>` and `make install` again.

**Lock screen.** In Plasma 6.8 the greeter (`kscreenlocker_greet`) takes the lock screen solely from the shell package (`plasma/shells/<shell>/contents/lockscreen`), and chooses the shell the same way as plasmashell (`plasmashellrc [Shell] ShellPackage`, by default `org.kde.plasma.desktop`). The `lockscreen/` directory in the global theme is ignored, and the key `kscreenlockerrc [Greeter] Theme` does not exist. That is why the Tide screen is made as the shell package `org.tide.desktop` with only `lockscreen/` (for test mode), and becomes the real lock screen through a **shell copy** (decision 27, variant a): `make install` copies the whole system `/usr/share/plasma/shells/org.kde.plasma.desktop` to `~/.local/share/plasma/shells/org.kde.plasma.desktop` and replaces in it only `contents/lockscreen` with the Tide screen. The package in the home directory wins over the system one, so the greeter takes the Tide screen from it, and plasmashell the rest — identical to the system. The shell does not change (`ShellPackage` stays `org.kde.plasma.desktop`), so neither do the panels or the file `plasma-org.kde.plasma.desktop-appletsrc`.

```bash
make lockscreen-screenshot   # the real greeter in a nested KWin: build/lockscreen-sleep.png (idle),
                             # build/lockscreen.png (after a "key"), the same at 1920×1080 and two screens (lockscreen-dual*)
make lockscreen-test         # the greeter in test mode as a window (XWayland); a password closes the window
make lockscreen-shell-check  # the shell copy in a nested session: plasmashell and greeter → build/lockscreen-shell-check
make shell-sync-status       # whether the shell copy is enabled and how the last sync ended
make lookandfeel-build       # only the global theme in build/lookandfeel
```

**Syncing the copy.** After a Plasma update plasmashell would read the stale copy, so the user unit `tide-shell-sync.service` (oneshot) runs `~/.local/share/tide/shell-sync.py` — a copy of the script `scripts/shell_sync.py` — at every login. The unit is `WantedBy=plasma-workspace.target` and `Before=plasma-plasmashell.service`: startplasma starts `plasma-workspace-wayland.target`, which requires `plasma-workspace.target`, and that requires `plasma-core.target` with plasmashell, so it is one systemd transaction and plasmashell waits for the sync to finish (plasmashell itself is not ordered after `graphical-session-pre.target`, hence the explicit `Before=`). The script computes a SHA-256 fingerprint of the files of the system package and the Tide screen; when it matches the fingerprint saved in the copy (`tide-shell-copy.json`), it ends after about 40 ms (the cat images in the lock screen add about 15 ms to computing the fingerprint). When it does not, it builds a new copy outside `plasma/shells/`, checks it with the real greeter (`kscreenlocker_greet --testing --shell`, off screen, without a session bus, about 2 s) and only then swaps it in. When the Tide screen fails to load, it **deletes the copy entirely** and the Plasma screen from the system remains; the next attempt only after the next change (a Plasma update or `make install`). The state of the last decision: `~/.local/state/tide/shell-sync.json`, the log: `journalctl --user -u tide-shell-sync`. `make uninstall` disables the unit and removes it together with the copy, the script and the state.

**Risks of the shell copy.**

- Plasmashell reads the whole shell package from the copy, not just the lock screen. If the copy were inconsistent with the system (e.g. Plasma updated without logging in again, and then a restart of plasmashell alone), the desktop may run on files of an older version until the next login. The sync runs only at login; after a Plasma update it is best to log out.
- A session started without systemd (`startkderc [General] systemdBoot=false`) will not start the unit — the copy then will not refresh itself.
- The greeter check detects QML loading errors, but not errors that appear only while typing the password. When the Tide screen breaks during a lock, the greeter falls back to the KDE emergency screen by itself.
- After a fingerprint change the sync delays the plasmashell start by about 2 s (once after each Plasma update). When something goes wrong, the unit does not block plasmashell (no `Requires=`), and on an unexpected error the script deletes the copy.
- A copy that Tide did not make (without `tide-shell-copy.json`) is left alone by the script.
- The copy also lands in backups (`.local/share/plasma`). A restored old copy has a different fingerprint, so it is rebuilt at the next login.

**Rescue.** When the lock screen or the desktop does not work because of the shell copy: removing the directory restores the Plasma screen and shell from the system, and the sync respects that removal (it will not recreate the copy until the next `make install`).

```bash
rm -r ~/.local/share/plasma/shells/org.kde.plasma.desktop
```

If the screen is locked and cannot be unlocked: Ctrl+Alt+F3, log in on the text console, the command above, then `loginctl unlock-session <number>` (the graphical session number from `loginctl list-sessions`) and return with Ctrl+Alt+F2 (or F1). Full turn-off: `make uninstall-lookandfeel` or `systemctl --user disable tide-shell-sync.service`.

**The cat on the lock screen (the `cat` wallpaper variant).** The Tide screen draws the background itself (`CatBackdrop.qml`), separately on each monitor, in its frame (the images ship in the package: `tide/backdrop/awake|sleep/<connector>.png`, frames from the monitor layout at the moment of `make install`). The cat sleeps from the moment of locking and opens its eyes only when someone is really using the screen: mouse movement, a click, a key, typing or deleting in the password field (events of the lock screen itself, without global keyboard capture) wake it with a 650 ms crossfade (`lockscreen.wakeMs`). After 3 s without activity (`lockscreen.dozeAfterMs`) it falls asleep — more slowly and gently, in 1100 ms (`lockscreen.sleepMs`) — even when the form is still visible or a password has been left in the field. It does not fall asleep while the password is being checked and while the failed-unlock message is visible; when the form disappears (10 s without activity, Escape), it sleeps right away. The greeter has one QML engine for all monitors, so the cat wakes on both screens at once. With animations turned off the change is instant. There is a single dimming — the `lockscreen.dim` overlay on the raw images — and the cat is not blurred. The sleepy version is the same image as the awake one, with only the areas around the eyes swapped, so during the crossfade only the eyes change, while the background and silhouette stay in place (`sleepRegistration` in `theme/wallpaper/assets/cat/manifest.json` is [0, 0]; the correction mechanism stays for future, separate renders). The empty PAM message that the Plasma greeter receives right after locking does not show the form, so the cat does not wake itself at the start.

**Lock screen variants.** Next to the Tide screen the shell copy has the Stream activity screen (the Streamer palette, the graphic artist's images; the source package in `~/.local/share/tide/lockscreens/streamer`): the active one in `contents/lockscreen`, the others in `contents/lockscreen-variants/`, each checked with the greeter when the copy is built. `make install` keeps the chosen variant (on a new system, Tide). Switching is one atomic swap of directories, without a rebuild, from the next lock: `scripts/lookandfeel.py` (`lock_variants()`, `lock_variant()`, `apply_lock_variant("streamer")`) or `python3 ~/.local/share/tide/shell-sync.py variant [mist|streamer]` (decision 77). Building the copy therefore takes 2–3 s longer for each additional variant, because each goes through the greeter check: once at the first `make install` after the merge (a new copy format) and at the first login after a Plasma update; an ordinary login still costs about 40 ms. Preview: `make lockscreen-screenshot PALETTE=streamer`.

The look from the mockup: a dimmed wallpaper (blurred for blobs when the password field is visible), a Geist 96 px clock and a date ("poniedziałek, 26 października" (Monday, October 26)) in the upper part, the "three waves" logo, the user name and the password field on a card with a teal outline, at the bottom the keyboard, sleep and switch user as icons only. The line "2 agenci pracują · 1 czeka" (2 agents working · 1 waiting) comes from tide-daemon over the session D-Bus (the greeter runs as the user and inherits the bus from KWin); it shows only numbers, without project names, and disappears when the service is not there or nobody is working. When the QML of the Tide screen fails to load, the greeter itself shows the KDE emergency screen (checked in tests). License and origin of the files: `NOTICE` in the package (GPL-2.0-or-later, like the Plasma screen).

## Loading screen

After login `ksplashqml` shows the Tide screen from the global theme (`contents/splash`): the wallpaper dimmed with the desktop color (the `splash.dim` dimming computed when the package is built, the only one — QML overlays nothing anymore), in the `cat` variant a cat with open eyes in the frame of each monitor (`images/background-<connector>.jpg`), in the middle the full "three waves" logo, below it a thin teal progress bar (dimensions from the `splash` section of the palette, the `radius.small` radius capped at half the bar height), the logo gently "breathes", and the whole thing enters with a short crossfade (`splash.fadeMs`, 400 ms). The QML is an adapted Breeze screen with the same stage API: ksplashqml sets `stage` to 1…6 and finishes after the sixth, so the bar shows `stage / 6`. When animations are turned off in Settings, nothing pulses and the screen appears at once. There is no artificial waiting (e.g. the 1.2 s from the demo) — progress follows the stages only. The screen does not lengthen the startup: ksplashqml disappears when the session is ready, and the images load asynchronously.

`make install` writes `ksplashrc [KSplash] Theme=org.tide.desktop` (the previous value in `lookandfeel-previous.json`), and `make uninstall` restores the previous screen if Tide is still selected. The screen also works in the System Settings → Splash Screen preview.

```bash
make splash-preview   # the real ksplashqml --test in a nested KWin → build/splash/splash-*.png
```

License: `NOTICE` in the theme (GPL-2.0-or-later, like the Breeze screen from which `Splash.qml` derives).

## Terminal

Konsole, the bash prompt and Claude Code in Tide colors (#26), all generated from the palette (the `terminal` section: font size, margins, watermark). A preview without touching the current session — a real Konsole in a nested KWin with a temporary home:

```bash
make terminal-preview      # build/terminal/preview/konsole-agent.png and konsole-waiting.png
make terminal-themes       # only the prompt and tool theme files in build/terminal
```

| Target | What it changes | Undo |
| --- | --- | --- |
| `make install-konsole` (part of `make install`) | the "Tide" profile (Geist Mono, 18 px margins, centered, no scroll bar, the tab title from the shell `%w`), the TideMist color scheme (`opacity.panel` opacity with blur, three waves in the bottom right corner), `konsolerc`: the tab bar stylesheet, the menu bar turned off | `make uninstall-konsole` — the previous values of the `konsolerc` keys |
| `make install-terminal` (part of `make install`) | a marked block in `~/.bashrc` (a copy in `~/.local/state/tide/backups`) that loads `~/.local/share/tide/terminal/tide.bash`; tool themes only for the installed ones | `make uninstall-terminal` — the block disappears, `~/.bashrc` returns to its pre-installation content |
| `make install-claude-statusline` (separately) | only the `statusLine` key in `~/.claude/settings.json` (a copy of the file in `~/.local/state/tide/backups`) | `make uninstall-claude-statusline` (also in `make uninstall`) — the previous value or no key |

**Tab bar.** Pills like Tide tiles (Geist, `card` background, `tile` radius, the active tab with a teal outline) from a stylesheet that Konsole reads from `konsolerc [TabBar] TabBarUseUserStyleSheet` / `TabBarUserStyleSheetFile` (keys verified in Konsole 26.08 and on the real Konsole in the preview). Tabs with an agent are marked by tide-daemon: "● project · branch" (working, a teal bar under the title), "◆ project · branch · czeka" (waiting; a peach bar), "○ project · branch" (finished, no color). The other tabs show the title from the prompt: "project · branch". Konsole's limitations: the stylesheet has no single-tab selector, so the dot and the word "czeka" cannot have their own color nor can a tab have a background in the state color — the state color is the bar that Konsole draws under the title (`setTabColor`); the stylesheet lays the pill out so that the bar falls under the text. Only the terminal area is transparent (the tab bar and toolbar have the window color). The toolbars ("Nowa karta" (New tab), "Podziel widok" (Split view)) are turned off in Konsole: Settings → Show Toolbars — Konsole saves this in the binary window state, which we do not generate.

**Prompt.** Pure bash (no starship): chips for the project (the name of the main repository also in a worktree), the branch, "worktree", the number of changes, ahead/behind, and the agent state from tide-daemon ("● claude pracuje" (working), "◆ codex czeka" (waiting), "○ claude skończył" (finished)), the time on the right, a second line "›" (pink after an error). The branch and worktree are read from `.git` files, the only process is `git status` with a 22 ms limit (`TIDE_PROMPT_GIT_TIMEOUT`), so the prompt fits in 30 ms (measured: 7–20 ms median in repositories from `~/DevWorkspace`); when git does not make it in time, the prompt shows the last result for that repository or "±?". The agent state comes from the file `~/.cache/tide/agents-by-cwd.tsv`, which tide-daemon writes — no D-Bus at every prompt. Geist Mono has no rounded powerline ends, so the chips are a background with spacing. `TIDE_PROMPT=off` in `~/.bashrc` before the block turns off the prompt alone (the tool colors stay).

**Claude Code status bar.** `project · branch · [worktree] · model · session ━━━ 63% · week ━━━ 89% · starczy do resetu · context 41%` from the JSON that Claude Code passes to the script (`rate_limits`, `context_window`, `workspace`, `model`). The limits show how much is left (colors as in the AI Limits widget). When Claude Code does not provide limits, the script asks QuotaApp (`GET /status`, at most every 60 s, the result without the token in `~/.cache/tide/claude-limits.json`). The forecast ("starczy do resetu" (will last until the reset), "koniec ok. 17:40" (ends around 17:40) in peach) comes from tide-daemon (`/Limits Forecast()`, #24), queried through `busctl` at most every 60 s and without starting the service; without the service or the forecast — from the usage pace in the current limit window. Codex has no status bar command (only the built-in `/statusline` items and the syntax theme choice), so we do not change it.

**Tools.** `LS_COLORS` and the fzf colors are in `tide.bash`. The themes for bat (`Tide.tmTheme` + `bat cache --build`), delta (the "tide" feature through `include.path` in `~/.gitconfig`), btop (`~/.config/btop/themes/tide.theme`) and lazygit (`LG_CONFIG_FILE`) install only when the tool is installed. None of them is on the system now. The Ubuntu 24.04 repositories have btop 1.3.0, fzf 0.44.1, bat 0.24.0 (the `batcat` command) and git-delta 0.16.5; the simulation adds only `libgit2-1.7` and `libhttp-parser2.9`, without Qt and KDE Frameworks. lazygit is not in the repositories.

```bash
apt-get -s install btop fzf bat git-delta   # simulation first
sudo apt install btop fzf bat git-delta && make install-terminal
```

## Vivaldi

Vivaldi is the main browser. `make install-vivaldi` (part of `make install`) dresses all profiles in Tide at once — Vivaldi must then be closed, because on exit it overwrites its settings (the installer refuses when the browser is running):

- **Profile theme.** Mist graphite (the address bar in the title bar color, panels and the start page in the window color, `text.primary` text, the `small` radius), and the profile's pastel on the tab bar (a dark shade, `tabBarTint`) and in highlights — profile windows can be told apart at a glance. Tomek: teal, Tomek-Firma: sky blue, lakomytomaszdev: mint, Streamer: peach (`vivaldi.profiles` in the palette; a new profile gets the next pastel from `otherColors`). The page color does not tint the bar (`accentFromPage` turned off). The theme is also set in the "as in the system" schedule (the default), so Plasma's light and dark modes give the same theme.
- **Private window.** A separate theme "Tide Mist · prywatne" (private): the whole window slightly lavender (`privateTint`), lavender highlights.
- **Interface.** The stylesheet `~/.local/share/tide/vivaldi/css/tide.css` through the "Allow CSS modifications" experiment (`vivaldi-css-mods` in `Local State`, the directory in Settings → Appearance): Geist across the whole interface and window buttons as in the Tide decoration — pastel circles butter, mint, pink, a glyph on hover (restore in a maximized window), gray in an inactive one.
- **Profile avatars** stay as you set them in Vivaldi — the installer does not change them.
- **Icon.** A muted glyph like for other programs: the Vivaldi mark from Simple Icons in pink on a dark pink tile (`make install-icons`).

Before a change the installer copies `Local State` and `Preferences` of each profile to `~/.local/state/tide/vivaldi/backup-<time>/`, and saves the previous values of the changed keys at the first installation. `make uninstall-vivaldi` (also with Vivaldi closed) restores the previous themes and the flag, removes the Tide themes from the list and the files from `~/.local/share/tide/vivaldi`. The Tide themes can then be changed in Settings → Themes like any other; a repeated `make install-vivaldi` selects them again.

```bash
make preview-vivaldi       # the real Vivaldi in a nested KWin, two profiles and a private window: build/vivaldi/*.png
```

## Backups

Before every look change a backup is made in `~/.local/share/plasma-tide/backups`.

```bash
make backup NAME=przed-zmiana   # a new backup
make backups                    # list of backups
make restore NAME=przed-tide    # return to the look from before the project
```

`restore` first makes a backup of the current state by itself, so restoring can also be undone. After restoring it is worth logging out and in again.

## Wallpaper

The kind of wallpaper is chosen by `wallpaper.variant` in `theme/palette/mist.json`:

- **`cat`** (Mist, the default): a cat illustration from `theme/wallpaper/assets/cat` (description, origin and checksums: `README.md` in that directory). On the desktop the cat closes its eyes when nobody is using the computer (below: "The cat on the desktop"), and on the lock screen it sleeps until someone uses the screen. For the layout the images were prepared for (HDMI-A-1 1920×1080 on the left, DP-2 2560×1440 on the right, aligned at the top), the installation copies the ready files to the monitor without processing. For a different layout the frames are recomputed from the 4480×1440 panorama: one scale on both axes for the whole canvas of all monitors and a crop around the cat, without stretching individual screens. Enlargement beyond 4480×1440 is plain interpolation — no detail is added.
- **`blobs`**: a wallpaper drawn from the palette (the `wallpaper` section: blob colors, their position, opacity, grain and a fixed random seed). The generator draws a single canvas covering all monitors in their real positions and cuts it into screens, so the blobs flow smoothly from the left monitor to the right. A subtle grain with dithering prevents banding on gradients.

The same variant (and the same frames) goes to the lock screen, the loading screen and the login screen. The Stream activity has the Streamer palette with the `pictures` variant: a separate image from the graphic artist for each monitor (desktop, lock, lock when idle), without a shared canvas; `make install` saves these images next to the Mist wallpaper (`wallpapers/Tide/pictures/`), and applies only the palette from `$TIDE_PALETTE` (Mist by default). A comparison of both variants without installing: `make palette-compare` (`build/compare/`), a preview of one: `make wallpaper-preview PALETTE=streamer` (decision 77). A repeated `make install` with the `cat` variant keeps the cat — blobs appear only after changing the variant to `blobs`.

`make install` reads the monitor layout from `kscreen-doctor -j` (when it does not respond, it takes the known layout), writes one image per monitor to `~/.local/share/wallpapers/Tide/<variant>/<connector>.png` and sets them on the desktops of the current activity (for the cat through the `org.tide.cat` plugin, which contains both cat states for this layout); the desktops of other activities get their wallpaper on switching (below: "Activity look"). Each variant has a separate directory, so after changing the variant the image address changes and Plasma is sure to load the new one; the images of the previous variant (and older `wallpapers/Tide/<connector>.png`) are removed if no desktop shows them anymore. After a monitor change a repeated `make install` is enough. numpy and Pillow are required, which the system `/usr/bin/python3` in KDE neon has.

```bash
make wallpaper-preview          # images only in build/wallpaper (for the cat also build/wallpaper/sleep and the org.tide.cat plugin), without changing the desktop
make install FORCE=1            # also overwrites a custom wallpaper
```

### The cat on the desktop

In the `cat` variant both desktops get the wallpaper plugin **"Tide: kot"** (Tide: cat) (`org.tide.cat`, in `~/.local/share/plasma/wallpapers`). Each monitor shows its frame of the shared canvas, as with ordinary images, and the same frame as the lock screen. When for **15 s** (`wallpaper.dozeAfterS`) there was no keyboard or mouse, the cat closes its eyes with a 1100 ms crossfade (`lockscreen.sleepMs`); the first movement or key opens them in 650 ms (`lockscreen.wakeMs`). With animations turned off the change is instant. At rest the wallpaper does nothing — two still images, no timers.

Idleness is counted by KWin (the Wayland protocol `ext_idle_notifier_v1`, only user input counts, a playing video does not keep the cat awake), and provided by `tide-daemon` over D-Bus (the `/Idle` object, [description](docs/tide-daemon.md#idle)). Without the service the cat simply stays awake. The service reads the threshold from the copy of the palette, so after changing it you need `make install-daemon`.

```bash
make install-daemon install-wallpaper   # the service (idleness) and the plugin on both desktops
make desktop-cat-preview                # a nested session: build/desktop-cat/awake.png, asleep.png, waking.png, awake-again.png
qdbus6 org.tide.Daemon /Idle org.tide.Daemon.Idle.State   # {"available": true, "idle": false, "thresholdMs": 30000, …}
```

The ordinary images in `wallpapers/Tide/cat/` stay: they are pointed to by the "Image" configuration of each desktop (choosing "Image" in the desktop settings gives the same, awake cat) and by the lock screen wallpaper, and the backup from before the plugin returns to them after `make restore`. After a repeated installation with a different monitor layout the new plugin frames appear after `systemctl --user restart plasma-plasmashell` or logging in again.

**A custom wallpaper.** It is enough to set a different image in Plasma (right-click on the desktop, Configure Desktop and Wallpaper). A later `make install` will not touch a desktop that has a wallpaper other than Tide or the one from before Tide, unless you pass `FORCE=1`. `make uninstall` restores the pre-Tide wallpapers only where the Tide wallpaper (images or the cat plugin) is still set, and removes the `wallpapers/Tide` directory and the `org.tide.cat` plugin.

## Activity look

A desktop can have several KDE activities in one session. Each has a look from a description in `theme/activities/`: **work** (`work.json`, today's Mist: `TideMist`, the `Tide` style, the cat, the Mist lock screen, the current panels) and **Stream** (`stream.json`, the Streamer palette from PT-34, panels from PT-36). The `stream` look goes to the activity assigned to it at creation (`$XDG_CONFIG_HOME/tide/activities.json`, by activity ID); every other one, including a new one, looks like work. Icons, cursor, fonts, window decoration, Konsole, Vivaldi and Claude are shared.

After every activity change (a shortcut, `Meta+Q`, a widget, KDE Connect) `tide-daemon` applies the whole look of the target activity, and at login it compares the desktop with the description and fixes differences ([description](docs/tide-daemon.md#activity-look), decision 78). Look switching depends on the service: without a running `tide-daemon` (or without `make install-activity-look`) an activity change leaves the previous scheme, style and lock screen, and a new activity gets its look only at the next service start or after `python3 scripts/activity_look.py apply`. Only differences are applied, with Plasma tools, so open windows repaint without a restart. The installers do the same: `make install` in the Stream activity will not switch it to Mist, and the wallpaper goes only to the desktops of the current activity.

```bash
make activity-look-check     # whether the desktop matches the description of the current activity — read-only
make activity-look-status    # assignments, available looks, the log of the last switch
make install-activity-look   # a copy of the engine for the service in ~/.local/share/tide/activity-look (part of make install)
```

### The Stream activity

`make install-stream-activity` creates the "Stream" activity (the `tide-stream` icon: a gamepad mark from the Streamer brand in the gold
from the palette) and assigns the Stream look to it; repeating it duplicates nothing, and the current activity does not change. Panels,
the console and the notification filter are applied on the first entry (`Meta+Q`). `make uninstall-stream-activity` removes
the assignment and deletes the activity, but not when it is the current one. Both targets are outside `make install` and `make uninstall`
(decision 80).

In Stream (Plasma panels are shared across activities, so work loses nothing):

- **left monitor**: the Stream dock at the bottom — Stream, Player profile, Observer (only in Observer mode) · tasks · Camera,
  "?" (a place for PC ⇄ Xbox from PT-38 before "?"); on the desktop one widget "Lewy monitor Stream" (Left Stream monitor) (`org.tide.stream.desk`:
  the stream console or Gaming) across the whole width and height above the dock, with a 32 px margin (added only when
  it is missing — we do not touch a custom arrangement). Scenes and chat from an earlier installation are removed from the Stream desktop on the first entry with the desk
  (only these two widgets and only there); without the desk package the pair Scenes (about 1/3
  of the width) and chat stays;
- **DP-2**: the dock without work tiles — System and tasks remain; the work tiles hide themselves
  (`widgets/lib/ActivityLook.qml` asks tide-daemon), their settings and places stay;
- **the work sidebar**: hidden and narrowed to the minimum (without tiles); touching the middle of the left edge
  does not slide the bar out, and at the top end only a small panel shadow remains (about 64 × 16 px in the preview);
  `Meta+S` does nothing; on returning to work it comes back exactly as it was;
- **notifications**: only KDE Connect, the Streamer core (when it sends `x-kde-appname=streamer`) and critical ones pop up;
  the rest, including Discord and Telegram, waits in the history. The service holds "Do Not Disturb" for the duration of Stream and releases it
  on exit; the work notification settings return byte for byte;
- **lock screen**: the Streamer variant without the line "N agents working".

After returning to work the panels are exactly as before the entry (`panels.py dump` without differences — checked in a nested
session: `make stream-activity-preview`, results in `build/stream-activity/`).

```bash
make install-stream-activity     # the Stream activity with an icon and an assigned look
make uninstall-stream-activity   # removes it (refuses when Stream is current)
make stream-activity-status      # the assigned Stream activities — read-only
make stream-activity-preview     # a nested session: work → Stream → work, screenshots and a panel comparison
```

## Icons

The "Tide" overlay inherits from the base theme (McMojave-circle-dark by default, another through `make install ICON_BASE=...`) and adds only what the base lacks: aliases for JetBrains Toolbox icons and wrong mappings (`code`), round tiles from the original icons of applications outside the theme, glyphs in palette colors for applications without an icon and folders recolored to teal. Applications that give a file path in `Icon=` (snaps, Zed, Docker) get a local copy of the `.desktop` file in `~/.local/share/applications` with the marker `X-Tide-Icons=override`. Manual fixes live in `theme/icons/fixes.json`.

The applications from the dock and all installed programs and tools have **muted glyphs** in the overlay: a rounded square in a dark shade of a palette pastel and a single-color application mark in that pastel (decisions 31 and 39). Only Steam games and Plasma developer tools with generic icon names stay without a glyph (decision 40). The same icon is in the dock, in the menu and in KRunner. The list is `theme/icons/glyphs.json`: application icon names, the mark source and the color (a pastel or a `text.*` token), optionally `scale`. The mark comes from a symbolic icon of the base theme (or Breeze), from [Simple Icons](https://simpleicons.org) (CC0, the version pinned in the file, each logo has a saved SHA-256 checksum; downloaded at build time to `~/.cache/tide/simple-icons/`) or from a hand-drawn mark in `theme/icons/glyphs/`. Names from the JetBrains Toolbox (`jetbrains-rider-<uuid>`) reach a glyph through patterns from `fixes.json`. When the source is missing or the checksum does not match, the application keeps its own icon, and `tide-icons` prints a warning. A new Simple Icons logo: add the application to `glyphs.json`, run `tide-icons pin-glyphs` (it adds the checksum), look at `make icons-preview` and approve.

The **top bar tray** icons are tiles like in the dock (decision 95): a dark shade of a pastel and a mark in the pastel, the colors of the dock applications, and the rest in family colors (sound peach, connectivity sky, power butter). The state is visible in the tile: a dot on the corner, a brighter tile with an outline, pink when muted. `theme/icons/tray.json` says under which icon names each tile lies, exactly the ones the applets ask for (otherwise KDE would match a shortened name, e.g. the KDE Connect tile from the dock). ChatGPT, Discord and OBS send ready-made images, so for them the tile is shown by the `tide-tray` service (`tray/`): it exposes a mirror with the Tide icon, forwards clicks and the menu to the application, and turns off the original in the tray settings only once the mirror is already up. On stop it gives the originals back. `make tray-status` shows what it does. A tile preview: `python3 theme/icons/tray.py preview` → `build/icons/tray-preview.png`.

```bash
tide-icons audit        # where the icons of visible applications come from, which have no glyph, whether the ones pinned in the dock have one;
                        # exit code 1 when one does not match or a pinned one has no glyph
tide-icons audit --json
tide-icons audit --theme Tide --icons-dir build/icons   # the same for the overlay from make icons-build, before installing
tide-icons glyph tide-worktrees --out x.png             # a glyph from a hand-drawn mark to PNG (without the theme and network)
tide-icons sync         # after installing a new application: refreshes the overlay and the .desktop copies
tide-icons themes       # comparison of base theme candidates
make icons-build        # only the overlay for a preview in build/icons/Tide
make icons-preview      # a sheet of all glyphs next to the current icons: build/icons/glyphs-preview.png
tide-icons pin-glyphs   # SHA-256 checksums of new Simple Icons logos into glyphs.json
```

`make install` installs the command to `~/.local/bin/tide-icons`. Uninstalling removes the overlay, only the marked `.desktop` copies (user files that we overwrote come back from the backup) and Flatpak's access to icons, if Tide granted it.

## Login screen (SDDM)

The SDDM theme "tide" in the Mist style: the Tide wallpaper (in the `cat` variant a cat with open eyes; a crop for a 2560×1440 screen and for 1920×1080, as on the desktop, dimmed once with the desktop color by `opacity.loginDim`), the "three waves" logo, a clock and date in Geist at the top center, an avatar (a photo or an initial), the name, the password field (a card from the palette, teal on focus), "Zmień użytkownika" (Change user), a session chooser, a keyboard layout ("PL"), the sleep, restart and shutdown buttons and a wrong-password message in pink. SDDM opens a separate window on each monitor and each has the full form, as in Breeze.

**User selection.** "Zmień użytkownika" (or Escape in an empty password field) shows account cards from the real SDDM `userModel` (a photo or an initial, the name, the account name) and an "Inny użytkownik" (Other user) card (typing an account name, like "Other…" in Breeze). The account that was selected is highlighted (at startup the last logged in one, `userModel.lastIndex`). Arrows, Tab/Shift+Tab, Home and End move the selection, Enter and space choose, Escape returns to the form; arrows in an empty password field still switch the account without opening the list. The background stays in place — only the middle of the screen changes.

The theme uses only QtQuick and the objects that SDDM gives every theme (`sddm`, `userModel`, `sessionModel`, `keyboard`, `config`), without `org.kde.*` modules. The Geist fonts, wallpapers, logo and icons are in the theme directory, because the login screen runs as the `sddm` user and does not see the home directory. The colors are in `Tokens.qml`, generated from the palette.

```bash
make sddm-preview      # screenshots in build/sddm/preview: the greeter stand-in (the form, user selection with two accounts,
                       # "Inny użytkownik", an error; 2560×1440 and 1920×1080) and the real greeter in a virtual KWin
make sddm-check        # the check only, without sudo
make install-sddm      # the check, then one sudo: a copy to /usr/share/sddm/themes/tide and /etc/sddm.conf.d/zz-tide.conf
make uninstall-sddm    # sudo: removes both, the pre-Tide theme returns
make sddm-status       # which theme SDDM will pick and from where
```

`make install-sddm` is not part of `make install`, because it needs sudo. Before changing anything in the system the theme is checked twice (as the user and once more on the copy in `/usr/share/sddm/themes/.tide-new`): the full set of files, the greeter stand-in in `qml6` off screen (two accounts, the initial selection, choosing an account with real keys, "Inny użytkownik", an empty password, login, the error message, fonts, every QML warning is an error) and `sddm-greeter-qt6 --test-mode` for a few seconds. When the check fails, nothing changes in the system.

**The previous theme.** Tide does not change `kde_settings.conf` (that is where System Settings save the theme), it only adds `zz-tide.conf`, read after it. The pre-Tide theme is saved in the header of that file and in `/var/lib/tide/sddm-previous.json`; after uninstalling it returns by itself. As long as Tide is installed, choosing a theme in System Settings will not work — first `make uninstall-sddm`.

**When something goes wrong.** A QML error: SDDM falls back to the built-in theme by itself ("Fallback to embedded theme"), and a missing theme directory gives the same. Should the login screen still not work, from a text console (Ctrl+Alt+F3, log in):

```bash
sudo rm /etc/sddm.conf.d/zz-tide.conf && sudo systemctl restart sddm
```

## Boot screen (Plymouth)

The Plymouth theme "tide" is the first screen after GRUB: a graphite background (`background.desktop`), in the middle the "three waves" logo on a tile and 44 px lower a thin progress bar — in the same place and size as the Plasma loading screen. The entry is a `splash.fadeMs` crossfade (the tile, then the waves every `plymouth.waveDelayMs`), then the waves "breathe" one after another (opacity 1 → `plymouth.breatheTo` → 1, cycle `splash.pulseMs`, the same curve as the logo in `Splash.qml`). On shutdown and restart there is just the logo, during system updates the bar and "Instalowanie aktualizacji · 42%" (Installing updates · 42%). An encrypted disk: instead of the bar a prompt from the system and a password field like on the lock screen (a card, a frame and an outline in teal, dots in the text color, "Hasło" (Password) when empty), below it "Caps Lock jest włączony" (Caps Lock is on) in butter; system messages (e.g. disk checking) lower in the muted color.

```bash
make plymouth-preview        # frames without root in build/plymouth/preview (boot-*.png, password-*.png, boot.gif, …)
make plymouth-check          # the check only, without sudo
make install-plymouth        # the check, then one sudo: a copy, update-alternatives, update-initramfs -u
make uninstall-plymouth      # sudo: the pre-Tide theme returns, update-initramfs -u
make plymouth-status         # which theme Plymouth will pick and whether the kernel has "quiet splash"
make plymouth-real-preview   # builds the theme for /run and prints the command for a preview on the real plymouthd
```

**The script module.** `two-step.so` is not on the system (the `plymouth-themes` package), and `script.so` is — the same one Breeze uses. The script is palette tokens (colors, times, dimensions at every scale; `theme/plymouth/build.py`) and fixed logic (`theme/plymouth/logic.script`). The images are logo layers (the tile and each wave separately, so the waves can breathe one after another), the bar track and fill, and the password field — at four scales: 0.75, 1, 4/3 and 2 relative to 1920×1080. Plymouth draws the same sprites on all screens, centering the smaller ones on the middle of the largest, so the scale follows the smallest screen: with both monitors the logo is 128 px as in Plasma, the 2560×1440 monitor alone gets 171 px, 4K without scaling 256 px. Plymouth gives the script logical dimensions, so with HiDPI scaling (DeviceScale) the images are enlarged. Text is drawn by `label-pango.so` with a font from the initramfs: the Plymouth hook copies only Ubuntu (as "Sans"), so Geist from the palette is first on the list, but Ubuntu is what actually gets drawn.

**A preview without root.** The real `plymouthd` needs root and a screen (the drm and frame-buffer renderers; there is no x11 renderer in KDE neon). `theme/plymouth/harness.py` is a stand-in for the script module: it runs the same `tide.script` on the same images (an interpreter of the Plymouth script language, the Math, Window, Sprite, Image and Plymouth libraries and a screen model as in `script-lib-sprite.c`) and draws frames in Pillow. The stand-in is stricter than Plymouth: a syntax error, calling something that is not a function, a missing image or an operation on NULL is an exception with a line number. `make plymouth-preview`, `make plymouth-check` and the tests run on it.

**A preview on the real plymouthd** (optional, run by the user): `make plymouth-real-preview`, then on a text console (Ctrl+Alt+F3) `sudo sh theme/plymouth/preview_real.sh build/plymouth/run/tide`. The theme goes only to `/run/plymouth/themes/tide` (tmpfs), is selected by a faked kernel command line (`--kernel-command-line … plymouth.splash=tide`), and the alternatives, initramfs and GRUB stay unchanged. The demo: start, a message, the password field (type anything), update mode; the log in `/run/tide-plymouth-debug.log` (script errors are "Parser error" and "Execution error"). Return to the session: Ctrl+Alt+F1 or F2.

**Installation.** `make install-plymouth` is not part of `make install`, because it needs sudo and refreshes the initramfs. KDE neon, like Ubuntu, has no `plymouth-set-default-theme`: the default theme is the `default.plymouth` alternative, which the initramfs hook also reads from. `scripts/plymouth_system.py`:

1. aborts before changing anything if another theme is forced by `plymouth.splash=` in the kernel command line or `Theme=` in `/etc/plymouth/plymouthd.conf` or `/usr/share/plymouth/plymouthd.defaults`,
2. copies the theme to `/usr/share/plymouth/themes/.tide-new` and checks that copy (`check.py`: files, paths, the script in the stand-in on seven screen sets and in all modes) — when it fails, nothing changes in the system,
3. at the first installation saves the pre-Tide theme in `/var/lib/tide/plymouth-previous.json` (the value and the alternative's mode: auto or manual) and its `.grub` file,
4. swaps in `/usr/share/plymouth/themes/tide`, registers the alternative (priority 100, lower than Breeze) and selects it manually (`update-alternatives --set`); the slave `default.grub` points to `tide.grub` — a copy of the previous theme's `.grub`, so `update-grub` gives the same menu as before,
5. runs `update-initramfs -u` (the newest kernel).

`make uninstall-plymouth` restores the remembered choice (`--auto` or `--set` of the previous theme), unregisters "tide", removes the directory and the state record, then runs `update-initramfs -u` again. The new screen is visible only at the next boot.

**The kernel command line.** Plymouth shows graphics only with `splash` in the kernel command line. On this computer it is there (`/proc/cmdline`: `… ro quiet splash resume=… nvidia.NVreg_EnableGpuFirmware=0 vt.handoff=7`, `GRUB_CMDLINE_LINUX_DEFAULT='quiet splash …'` in `/etc/default/grub`), so nothing needs to change. Tide never changes GRUB; `make plymouth-status` shows the state and suggests what to add should `splash` disappear. An NVIDIA card (the `nvidia-drm` driver with `modeset=1`): if the NVIDIA modules are not in the initramfs, Plymouth starts on a simple framebuffer (usually one screen at the firmware resolution) and switches to full screens when the driver loads — the script then recomputes the layout (`SetDisplayHotplugFunction`).

**When something goes wrong.** An error in the script gives an empty graphite screen instead of the logo, but system startup and the password prompt keep working (the password can be typed blind, Esc switches to text). Returning to Breeze from a text console:

```bash
sudo update-alternatives --auto default.plymouth && sudo update-initramfs -u
```
