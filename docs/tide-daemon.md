# tide-daemon

A systemd user service (Python 3, PyQt6) that reads the Claude Code and Codex session journals, derives the state of each session and exposes it over D-Bus. It does not change the Claude Code or Codex configuration — it only reads their files. The second area is repository worktrees from the projects directory: their state and safe cleanup of merged ones (described below, [Worktrees](#worktrees)). The third is a KRunner plugin with sessions from the last 30 days, projects and worktrees ([KRunner](#krunner)). The fourth is user idle time for the wallpaper with the dozing cat ([Idle](#idle)). The fifth is the morning report: once a day, an HTML page for every project with activity ([Morning report](#morning-report)). The sixth is the AI Limits history, the forecast of when they run out, and threshold notifications ([AI Limits](#ai-limits)). The seventh is coordination: sessions that assign work to subagents, as a ticket board with a plan and YouTrack ([Coordination](#coordination)). The eighth is the work board: "to do" cards from all projects in a local git repository ([Board](#board)). The ninth is Docker containers: compose groups with repositories, actions, logs and cleanups instead of Docker Desktop ([Docker](#docker)). The tenth is local models: access points to a model server (Ollama, later Mac Studio), calling a model and server state ([Local models](#local-models)).

## D-Bus contract

| Element | Value |
| --- | --- |
| Bus | user session |
| Service name | `org.tide.Daemon` |
| Object | `/Agents` |
| Interface | `org.tide.Daemon.Agents` |

| Member | Signature | Description |
| --- | --- | --- |
| `Sessions()` | `→ s` | List of sessions as JSON (an array of objects, described below) |
| `ActivateSession(s id)` | `→ b` | Switches to the session: a window (Konsole — a tab too) or a thread in Codex Desktop. `false` when there is nowhere to switch to |
| `ActivateLongestWaiting()` | `→ b` | Switches to the window of the session that has been waiting the longest (the Meta+A shortcut). `false` and nothing happens when nothing is waiting. `done` sessions are not considered |
| `Changed` | signal without arguments | The session list changed — fetch `Sessions()` again |

List order: waiting first (the longest-waiting on top), then working, done, idle and ended (by most recent activity).

Session fields:

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | `claude:<session-id>` or `codex:<session-id>` — the argument of `ActivateSession` |
| `tool` | string | `claude` or `codex` |
| `sessionId` | string | session identifier in the tool |
| `project` | string | repository name (for a worktree — the main repository) or directory name |
| `branch` | string | current branch from `.git/HEAD`, falling back to the journal |
| `worktree` | string | worktree directory name or an empty string |
| `cwd` | string | session working directory: the current working directory, and when the agent entered (`cd`) the Tide board repository (e.g. writes a plan in `<board>/docs/<project>/`) — the session launch directory, or failing that the project repository `docs/<slug>/` (`board/place.py`, PT-28). `project`, `branch` and `worktree` also come from it — the session does not become a tide-board "project" on master |
| `launchCwd` | string | session launch directory (constant, also after `cd`): Codex — `cwd` from `session_meta`, Claude Code — the first `cwd` of an entry matching the journal directory (`projects/<encoded directory>/`); empty when the journal does not say (e.g. the tail of a large file) |
| `launchKey` | string | launch directory encoded like a Claude Code project directory (`/a/b.c` → `-a-b-c`): Claude Code — the journal directory name (always), Codex — from `launchCwd`. The board uses it to link a card to a session launched from the card when `launchCwd` is empty |
| `startedTs` | epoch seconds | first entry of the session (from the part of the journal that was read) |
| `title` | string | short conversation title (Claude Code only: `custom-title` set by the user, otherwise `ai-title`) |
| `state` | string | `working`, `waiting`, `done`, `idle`, `ended` |
| `reason` | string | `waiting`: `approval` (permission for a tool), `question` (a question to the user), `reply` (turn ended with a question in the text, with no user answer — PT-29) or the `waitingFor` value reported by Claude Code. `working`: `background` (turn finished, but a background task is running that the agent will return to — only when the turn did not end with a question to the user and at most 2 h without a journal entry) or empty. `done` and `idle`: `scheduled` (the last turn was started by a Codex Desktop scheduled task) or empty. `ended`: empty |
| `since`, `sinceTs` | ISO 8601 / epoch seconds | since when the current state has lasted (for `waiting` — since when it has been waiting, for `done` — since the end of the turn) |
| `lastActivity`, `lastActivityTs` | ISO 8601 / epoch seconds | last agent entry in the journal |
| `lastAction` | string | last action, e.g. `Edit · main.py`, `shell · make test` |
| `waitingText` | string | `waiting` only: what the agent is waiting for — the text of the question (`AskUserQuestion` `input.questions[].question`, `ExitPlanMode` "Plan do zatwierdzenia: …" (Plan awaiting approval), `request_user_input` and the Codex Desktop asynchronous question, for `reply` — the question from the end of the agent's last message, at most 240 characters) or a description of the call awaiting permission (like `lastAction`, e.g. `Bash · Push na serwer`); empty when the journal does not say what for (just the `waiting` status from the Claude Code file). Empty in the other states. The report and the coordination board take the "waiting" text from here, never from the agent's last text (PT-23) |
| `pid` | int | agent process or 0 |
| `processes` | string | state of the process snapshot from which `pid`, `canActivate` and liveness were computed (K-98 #4, PT-345): `none` — before the first probe result (no data: the session is not `ended` because of it), `fresh`, `stale` — the last result is older than three ticks (a hung probe; the last full snapshot stays), `error` — the last probe threw an exception. Absent with `--no-processes` |
| `canActivate` | bool | whether `ActivateSession` has somewhere to switch to. A Codex Desktop session: always when the app is installed (even without a process and after it ended — the conversation can be opened) |
| `terminal` | string | name of the target window, e.g. `Konsole`, `Codex` |
| `newTask` | bool | only `true`, and only for 10 min: the session was just launched by the "Nowe zadanie" (New task) window (same tool and directory, the session did not exist before the launch). The widget shows a "nowe" (new) marker next to it. The field is absent in other sessions |
| `activation` | string | how `ActivateSession` will switch to the session: `deeplink` (Codex Desktop — thread `codex://threads/<id>` in the app, then its window), `window` (a terminal or app window, Konsole with tab switching) or empty |
| `subagents` | int | how many Codex subagents were folded into this session and are still running (state other than `ended`) |
| `coordination` | object | only for a coordinator (a session with subagents, [Coordination](#coordination)): `label`, `done`, `total`, `text` ("TOM-20 · 8/13"), `working`, `waiting`, `running`. The field is absent in other sessions |
| `subagent` | bool | the session is a Codex subagent whose parent we do not know (e.g. a main thread from over a day ago) — shown separately with a "podagent" (subagent) note |
| `agentNickname` | string | subagent nickname (`agent_nickname`) or empty |
| `model` | string | last model from the session journal (Codex: `turn_context.model`, Claude Code: the response's `message.model`; without `<synthetic>`) or empty |
| `localModel` | bool | the session runs on a local model (`agents/localmodel.py`), an allowlist — when in doubt `false`: Codex with `session_meta.model_provider` equal to `ollama`, `lmstudio`, `oss` or with the `tide-` prefix (providers from the "Nowe zadanie" window); Claude Code with a model that is not a Claude model and is known from the current `/Models` state (roles and models on servers; in `--print`, roles from `models.json`) or has an Ollama-style label `name:tag` without "/" (e.g. `qwen3-coder:30b`; `provider/model:free` from a gateway — only when it is in `/Models`). An Ollama Cloud model (`…:cloud`, `…:120b-cloud`, `modelnames.is_cloud_model`) is never local, also in Codex with the `ollama` provider — local Ollama only forwards the conversation to ollama.com. Bedrock, Azure, cloud gateways and unknown names without a tag stay unmarked. The agents widget then writes "lokalny · model" (local · model) on the second line, and the Konsole card with a state marker gets a "· lokalnie" (· locally) suffix |

`widgets/lib/DaemonSource.qml` uses this from QML (module `org.kde.plasma.workspace.dbus`).

Backward compatibility: the service from before the `done` state reported every end of a turn as `waiting` with `reason: reply`, so the widget, the lock screen and the Sessions badge treated such an entry like `done`. Since PT-29, `waiting/reply` means a real question with no answer (computed from the current turn on every journal read) and is "waiting" everywhere; the widgets and the service are installed together (`make install`), so the old meaning no longer exists.

## States and thresholds

Sessions whose journal has had no entry for over **24 h** are skipped.

| State | When | Widget |
| --- | --- | --- |
| `working` | a turn is in progress: the model is thinking, writing or a tool is running | "pracuje" (working), teal |
| `waiting` | the agent **is stopped and will not move without the user**: a tool awaits permission, the agent asked a question (`AskUserQuestion`, `ExitPlanMode`, `request_user_input`), Claude Code reported the `waiting` status, or the turn ended with a question in the text that the user has not answered yet (`reply`, PT-29 — also after 30 min, like any question) | "czeka na ciebie" (waiting for you), peach; notification, Meta+A, "◆ " on the Konsole tab, counter on the lock screen |
| `done` | the turn ended normally (Claude Code: status `idle`, `end_turn`; Codex: `task_complete`) or was interrupted, within the last **30 min** | "skończył" (finished), calm blue (`semantic.done`), empty circle; no notification, no Meta+A, no tab marker |
| `idle` | `done` or `waiting` with no activity for longer than **30 min**; also a turn with no entry at all for 30 min and no trace of a running process | "bezczynna" (idle), dimmed |
| `ended` | the session process is no longer running | "zakończona" (ended), dimmed |

**A question at the end of a turn means "waiting"** (PT-29, reverses part of decision 36). An agent that ends a turn with a question in the text — grilling ("Pytanie 10: skąd biorą się czat i alerty? …" (Question 10: where do chat and alerts come from?)), "Pushować?" (Push?) — is stopped until the user replies: state `waiting`, reason `reply`, `waitingText` with that question, `sinceTs` — the moment of the question. The heuristic (`agents/question.py`): the agent's last text message in the turn (Claude Code — the last `assistant` entry with text, with no tool call after it; Codex — `last_agent_message` from `task_complete` or the last `assistant` message), without code blocks and tables; the last paragraph that is not a list, and its last sentences: a sentence with "?" (followed by at most one short sentence — otherwise it is a rhetorical question in a summary), a "Pytanie N: …" line, "Czekam na twoją odpowiedź" (I am waiting for your answer), "Daj znać, czy…" (Let me know whether…) or a list of questions under a sentence with a colon; a trailing note under the question that asks the user for a word ("…jeśli napiszesz "przywróć".", "jeśli potwierdzisz", "napisz "…"") does not hide the question from the paragraph above (without it, the note alone counts), and a plain offer ("Jeśli chcesz, mogę też…") is not a question. Waiting is cleared by a user message after the question (without tool results and `<task-notification>` inserts), an interrupted turn and a tool call after the text. Claude Code compaction (also automatic, "Compacted while idle": `system/compact_boundary`, a `user` entry with a summary — `isCompactSummary`, `isVisibleInTranscriptOnly` — attachments, `system/informational`), `isMeta` inserts and the echo of a local command (`<local-command-caveat>`, then `<command-name>/model…`, `<local-command-stdout>`) are not an answer — the question and a pending `AskUserQuestion` keep waiting; a skill invoked by a command (`<command-message>…`) is an ordinary message. When the summary and attachments do not fit in the tail (1 MB), the first journal read goes back (×4, at most 16 MB) until it reaches the agent's last entry or a user message (`agents/jsonl.py`, `enough`). On compaction Codex appends a `compacted` record (with `replacement_history`) that the reader does not treat as a message — it does not clear the question; only the current turn counts, and a working session (status `busy`) never "waits for an answer". A turn started by a Codex Desktop scheduled task does not wait even with a question (the task will send the next message itself) — it stays `done` with `scheduled`. A subagent (a Codex thread with a parent) never waits for an answer — its report is read by the coordinator — and a folded subagent does not carry `reply` over to its ancestor. Everything follows the state: the widget ("czeka"), Meta+A, the notification "Agent czeka: <projekt>" (Agent waiting: <project>) with "Zadał pytanie: <pytanie>" (Asked a question: <question>) (once per entry into "waiting" — later refreshes of the same question send nothing), the lock screen, "Czeka na mnie" (Waiting for me) in the report (with `waitingText`), the question on the coordination board and the "Czeka na mnie" column on the board with "Przejdź do agenta" (Go to agent).

**Second tier: a model judges doubtful endings** (PT-42, decision 93). The rules miss requests without a "?" ("**Krok 1:** uruchom ten select i wklej mi wynik" (Step 1: run this select and paste me the result), "Daj znać." (Let me know.)), so `question.doubtful(text)` flags endings where the rules see no question (`ending_question` empty) but the last three sentences (and list items under them) contain a sentence with "?", a sentence starting with an imperative verb from a list (`uruchom`, `wklej`, `sprawdź`, `podaj`, `potwierdź`… also after "Krok N:", "Teraz", "Proszę"), or "daj znać"/`wklej`/`prześlij`/`odpisz`. "Dam znać" (I will let you know), "Jeśli chcesz, mogę też…", "Gotowe." (Done.) and every message with a "yes" rule are not doubtful and cost no model call. A doubtful ending is judged by `agents/classify.py`: `reply_question` asks `Classifier.judge(key, text)`, which **does not wait for the model** — it returns a remembered verdict or `None` (then the rules alone count) and schedules the judgment in a separate `tide-classify` thread; after the result, `AgentMonitor.classified` recomputes the state on the Qt thread. A state from a "yes" verdict has the same shape as from the rules: `waiting`, `reason: reply`, `sinceTs` — the moment of the message, `waitingText` — the last two sentences of the ending (at most 240 characters). The call: `models.chat(role="classify")` through the model queue (priority like card descriptions, before the report; one call at a time per server, the judgment yields to a waiting report for at most 30 s), the last 1200 characters of the message, schema `{"waits": bool}`, `think: false`, `temperature: 0`, a 20 s limit from the start of the call, 120 s of waiting in the queue. The verdict is remembered by entry uuid (Claude Code; Codex — session and text digest): in memory and in `$XDG_STATE_HOME/tide/classify.json` (`{"version": 1, "results": {key: {"waits": bool, "at": ts}}}`, the 500 newest, atomic write), so the same message is judged at most once even after a service restart. An error (no server, bad JSON, wrong shape, timeout) → the verdict is not saved, the rules alone count, the cause is in the service log, and the same message returns for judgment after 5 min at the earliest. The `classify` role with no model, with an unknown or skipped point, or unavailable (e.g. a cloud model without an explicit point) turns the judgment off: a warning in the log (once per reason), settings re-checked after a minute. A cloud model works in this role only with an explicit point in `models.json` (`"classify": "punkt:gpt-oss:120b-cloud"`): then the judgment goes there and only there, and when it fails the rules alone count — no fallback to another point. With a local role, a cloud point is never called (decision 70). With `--no-models` the judgment does not run (`--print-once` and `scan()` also count the rules alone).

An ordinary end of a turn without a question is still `done`: an agent that has answered blocks nothing, and a notification, peach and Meta+A on every finished turn taught people to ignore "waiting".

**Meta+A only for waiting sessions.** A considered fallback target (the freshest `done` session when nobody is waiting) was rejected: a shortcut that sometimes leads to a finished conversation stops meaning "someone needs you", and finished sessions are reached through the widget list ("Przejdź" (Go) button) and KRunner.

How we know:

- **Claude Code** itself writes the state of a running process to `~/.claude/sessions/<pid>.json` (`status`: `busy`, `waiting`, `idle`, `shell`, plus `waitingFor`). This is the main source — it also covers waiting for permission: `waiting` → `waiting` with the reason from `waitingFor`, `idle` → `done`, `busy` → `working`, `shell` → `working` with reason `background`. With `shell` a question wins: a turn ended with a question in the text (`reply`) or a pending `AskUserQuestion`/`ExitPlanMode` (`question`) is `waiting` — a background task will not answer for the user. `shell` with no journal entry at all for `BACKGROUND_STALE` (2 h, counted from the later of: the status change and the last entry) is considered a hung background task (e.g. an `until … pgrep -f` loop that finds itself) and counted like an ordinary end of a turn (`done`, `idle` after 30 min without activity). Process evidence says nothing here — a background loop runs `sleep` all the time. When the file is absent (older versions), the state comes from the journal `~/.claude/projects/<directory>/<session>.jsonl`: `stop_reason: end_turn` or `system/turn_duration` ends the turn (`done`), `tool_use` without `tool_result` is a tool call in progress, and `AskUserQuestion`/`ExitPlanMode` without a result is a question (`waiting`, `question`).
- **Codex** — `<data directory>/sessions/YYYY/MM/DD/rollout-*.jsonl` in every Codex data directory (below): `task_started` starts a turn, `task_complete` and `turn_aborted` end it (`done`), `function_call`/`custom_tool_call` without `*_output` is a tool in progress, and `request_user_input` without a result is a question (`waiting`, `question`, also with `approval_policy: never`). Subagent and permission-reviewer threads (`source.subagent`) are not separate sessions (below, "Codex subagents").
- **Codex data directories** (`codex_homes.py`, decision 65). Codex Desktop writes to `~/.codex`, and Codex CLI from the snap (`/snap/bin/codex`) to `~/snap/codex/<revision>` — the snap sets `CODEX_HOME: $SNAP_USER_DATA` for it and gives it no access to `~/.codex`. So the service reads `~/.codex` and `~/snap/codex/current` (the current revision; older revisions only hold copies from before the snap refresh — except for `snap revert` and a process that survived a forced refresh and keeps writing to the old revision), and when it has `$CODEX_HOME` — only that directory. `--codex-dir` can be repeated; the ones given replace the defaults. The directories are resolved without symlinks on every scan (after a snap refresh `current` points to the new revision, and the process holds the journal under the path with its number). The same applies to KRunner, worktrees, the report and coordination: everywhere `<codex>` appears below, it means each of these directories. A copy of the same file in the second directory (same path relative to `sessions/`; the name `rollout-<time>-<thread>[_<segment>].jsonl` carries the thread id and the segment) is read once — the newer file wins; this is the only merging across directories. New files in the snap directory are seen as quickly as in `~/.codex` — the watched directories are `sessions/`, the current year, month and the last two days of each of them. Codex from the snap launched in a directory the snap cannot see (e.g. `/tmp`, a hidden directory in `~`) records `cwd` as `/var/lib/snapd/void`.
- **Codex thread segments.** Codex Desktop splits a long thread into several files with the same id (`rollout-…-<thread>.jsonl`, then `rollout-…-<thread>_<segment>.jsonl`, consecutive time ranges). The Agents area shows the thread as one session with the state, directory and activity of the newest segment (earlier every segment was a separate entry with the same `id`, so the list had e.g. four cards for one thread); KRunner does the same. The morning report and worktrees see segments separately, as before — the report does not lose the work and results of earlier segments, and a thread is linked to the worktrees of all its segments.
- **Codex subagents.** A subagent launched by an agent (`source.subagent.thread_spawn`, with `parent_thread_id`; `payload.session_id` is the main thread id) is folded into the highest known ancestor: the ancestor gets a `subagents` counter (running subagents) and the state of the most urgent one in the group — `waiting` > `working` > `done` > `idle` > `ended`, the ancestor wins ties — together with its reason, `since` and `lastAction`; `lastActivity` is the newest in the group. So a waiting subagent makes the main thread waiting: the notification, Meta+A and the Konsole tab marker concern the main thread, and the switch leads to it. A subagent whose parent we do not know (parent older than a day) stays a separate entry with `subagent: true`. A subagent lives only while a process holds its journal (Codex Desktop and CLI keep the journals of active subagents open) — no matching by working directory. The permission reviewer (`subagent.other = "guardian"`) and other kinds without a parent are skipped entirely.
- **Codex Desktop scheduled tasks.** An automation (`~/.codex/automations/<id>/automation.toml`, `kind = "heartbeat"`, `rrule`, `target_thread_id`) periodically sends a message to the given thread by itself. We recognize such a turn by `event_msg/task_started` with `turn_attribution.turn_trigger = "automation_heartbeat_scheduled"` (newer versions; a hand-written one has `composer`) or by a user message (`response_item` `message`, `role: user`) with `internal_chat_message_metadata_passthrough.content_item_kinds` containing `user.heartbeat` (hand-written: `user.text`). After such a turn ends, the session is `done` with reason `scheduled` (the widget appends "zaplanowane" (scheduled)), and the next turn started by the user removes that reason. A question or permission during an automation turn is still `waiting` — the agent really is stopped. The format is undocumented: when the markers disappear, an automation turn will be an ordinary `done`, not `waiting`.
- **Tool in progress.** For **10 s** after the call the session is "working". After that it keeps working only if the agent process started something after the call (e.g. a shell command), the tool is inherently long (a subagent, waiting for a subagent) or Codex has `approval_policy: never`. In other cases we assume it is waiting for permission. A long MCP call without its own process can therefore briefly look like waiting for permission.
- **Session process.** Claude Code: `pid` from the file `sessions/<pid>.json`. Codex: the `codex` process that holds the journal file open (paths compared without symlinks — for Codex from the snap `~/snap/codex/<revision>/…`; in the Codex Desktop app it is `codex app-server`; a thread that the app does not hold open is shown as ended). As a fallback: a tool process with the same working directory.

## Refreshing

- `QFileSystemWatcher` on the session files from the last day and on the directories where new sessions appear; a file change → reading only the appended entries after 250 ms,
- every 5 s, recomputing states without reading journals (time thresholds, processes from `/proc`, about 15 ms),
- every 30 s, a directory scan (e.g. an old Codex session resumed in a directory from a week ago).

On the first read of a large file we read only the first line (metadata) and the last 1 MB.

## Switching to a window

Best-effort. From the agent process we walk up the process tree to a known terminal (Konsole, kitty, Alacritty, foot, WezTerm, Ghostty…) or app (Codex Desktop, VS Code). For Konsole we first switch the tab (`org.kde.konsole.Window.setCurrentSession` with the number from `KONSOLE_DBUS_SESSION`), then a KWin script loaded through `org.kde.KWin /Scripting` (`loadScript`, `start`) activates the window that belongs to that process. When the terminal cannot be determined (e.g. tmux, ssh), the session is not clickable.

**Several Konsole windows (PT-33, decision 71).** One Konsole process can have several windows (a tab dragged out of a window, "Nowe okno" (New window)) — they then share the same PID, and `KONSOLE_DBUS_WINDOW` from the agent's environment points to the window where the tab was created, not the one it is in now.

- Tab window: introspection of the Konsole service's `/Windows` and the window whose `sessionList()` contains the tab number; the window from the environment only when no tab window reports it or the windows cannot be enumerated. `setCurrentSession` goes to that window.
- Window in KWin: the current tab passes its displayed title to the window (`title(1)`; Qt appends " — Konsola"), so the script gets the tab title and the titles of the current tabs of the process's other windows. A window showing a different tab is never chosen; a window with the tab title takes precedence over a window with an unknown title, then process proximity decides (as before). We compare whitespace collapsed, as KWin does.
- An identical title in another window of the process (e.g. two "● tide · master"): the displayed tab title gets a " ⌖" suffix for `RESTORE_MS` (1 s) (`setTitle(1, …)`, title formats immediately back), the script looks for the window with that suffix, and then the title returns — unless the user managed to change it. The tab state marker (`TabMarker.hold`/`release`) leaves that tab alone in the meantime and brings it to the current state after release; when the service shuts down, the title returns immediately.
- KWin receives the new window title asynchronously, so when no window is unambiguous at the moment the script starts, the script activates the best available one and corrects the choice once when one of the process's windows gets an unambiguous title (`captionChanged`). With the marker, none of the windows with an identical title is raised blindly — the script waits for the title with the marker (a window with an unknown title is raised at once when no identical ones are visible). KWin scripts have no timer, so after `FINISH_MS` (0.6 s) the service loads a finishing script: if the active window is not the best candidate, it raises the best one — a click never ends in nothing. The script is removed after 2 s; the timer of an older script does not remove a newer one, and the timers of an earlier switch to the same tab do not shorten the newer marker.
- We set the restore timers before loading the script, so a script file write error does not leave a marker; errors in the timers go to the log, do not stop the service, and the tab always returns to the state marker. When appending the marker succeeds only partially, the tab title and formats return immediately. `strip_marker` also removes the trailing " ⌖", so after a crash during a switch the suffix does not end up in the card's saved original.

The logic is in `tide_daemon/windows.py` (`Activator`, `kwin_script`), the Konsole calls in `konsole.py`.

**Codex Desktop (`activation: deeplink`).** Merely raising the app window used to show the most recently viewed conversation, not the one from the list. A session from the app (`originator` containing "desktop", e.g. `Codex Desktop`, or a `ChatGPT` window among the process ancestors) therefore opens its thread: the service passes `codex://threads/<id>` to the app and then raises its window with a KWin script (the window from the session process's ancestors, and without a process — the main `ChatGPT` process, i.e. the one whose parent is not `ChatGPT`; when the app is not running, it shows a new window by itself). `<id>` is the main thread: a subagent has no conversation of its own in the app ("Nie znaleziono konwersacji" (Conversation not found)), so we follow `parent_thread_id` through known sessions (any depth, with loop protection); when the parent of the highest known thread is unknown, we take its `payload.session_id` (the main thread id according to Codex), and without it — the parent itself. Only the UUID goes into the link.

- We take the program from the `Exec` line of the `.desktop` file handling `x-scheme-handler/codex`: first `chatgpt.desktop` from the XDG directories (`$XDG_DATA_HOME`, then `$XDG_DATA_DIRS`), as a fallback any file with that type in `MimeType` (skipping `Hidden=true`). The link replaces `%U`/`%u`/`%F`/`%f`, other field codes disappear, with no code — it goes at the end; launched with an argument list, without a shell. Today: `chatgpt codex://threads/<id>` (`/usr/bin/chatgpt` → `/usr/lib/chatgpt/codex-launcher`), which the running instance switches to that thread. `$TIDE_CODEX_DESKTOP` points to the `.desktop` file directly (empty = no app; this is how the tests launch the service).
- Started through `systemd-run --user --scope` (like Konsole from worktrees), so that a service restart does not close an app we launched.
- A systemd service may lack the display variables (it depends on whether the session imported the environment). When both `WAYLAND_DISPLAY` and `DISPLAY` are missing, we pick the missing graphical variables (`WAYLAND_DISPLAY`, `DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`, `XDG_CURRENT_DESKTOP`, `XDG_SESSION_TYPE`…) from `systemctl --user show-environment`; we do not touch the rest of the environment.
- Without the app installed (no file or no program from `Exec`), Codex Desktop sessions behave as before: clickable only with a determined window, which raises itself.
- Codex from the command line (`codex_cli_rs`) in Konsole is unchanged — switching to the terminal tab.

## Jump to a waiting agent

**The Meta+A shortcut.** The KWin script `tide-agent-jump` (`kwin/agent-jump`, installed with `make install-agent-jump`) registers the shortcut through `registerShortcut` and calls `ActivateLongestWaiting` on `/Agents`. The service refreshes the state and processes (the process probe started after the keypress, off the main thread — below, "Process probe off the main thread"), picks the waiting session with the earliest `sinceTs` among those whose window can be switched to (one waiting in tmux or over ssh is skipped), and activates the window the way `ActivateSession` does (Konsole: the tab first). When nothing is waiting — it returns `false` with no message; `done` sessions are not a fallback target (above, "Meta+A only for waiting sessions"). If the service is not running, D-Bus will start it on the first press.

**Process probe off the main thread (K-98 #4, PT-345).** `processes.probe` (the whole `/proc`, and for agents also `fd`)
runs in one persistent `tide-process-probe` thread (`procprobe.ProcessProber`), not in the main thread, which handles
D-Bus. The session monitor tick (every 5 s), a journal change and the result of a turn evaluation recompute the list on the last published
process snapshot and request a new probe without waiting; its result returns to the main thread as a signal, replaces the snapshot
with a single assignment (the old or the new table, never a partial one) and recomputes the list again. Requests during a probe
merge into one next probe; a probe exception yields the `error` state and does not stop the next one, and a hung probe holds
only its own thread (reads get the last snapshot with the `stale` state). User actions work solely on a probe
started after the call, not on the published snapshot nor on a probe in progress at the time of the call: `ActivateSession`
and `ActivateLongestWaiting` reply with a delay (delayed reply) when such a probe arrives, and after 2 s without it —
`false` with no effect. Calls from inside the service (the "Przejdź" button of a notification, `/Coordination Activate`, KRunner,
opening a Codex Desktop thread) wait for it explicitly for at most `PROBE_WAIT_S` (2 s) and then refuse. The same goes for
worktree cleanup (`/Worktrees Remove`, `RemoveForced`): it counts the directories of live agents on a probe newer than
the call (`AgentMonitor.refresh_fresh`), and without it refuses every path (`unavailable`) with no effect. The probe result is
purely presentation: the card flow (step start, recovery, approval) does not read it — a fresh, full read of the
journal in the mutation queue decides, so a corrupted history and an unknown identity refuse with no effect even when the probe
sees a live process (`daemon/tests/test_monitor_probe.py`).

Why a KWin script and not a KGlobalAccel registration straight from the service: on Wayland in Plasma 6, KWin handles global shortcuts, and `registerShortcut` is a documented, stable script API. The `org.kde.kglobalaccel` D-Bus protocol is internal (`doRegister`, `setShortcutKeys` with `a(ai)` key structures that changed between KF5 and KF6, `setInactive`, a separate `/component/<name>` object with a signal) — it is easy to break with a Frameworks update and hard to build correctly from PyQt. A shortcut in a KWin script does not disappear when the service restarts, it is visible in System Settings → Shortcuts → KWin ("Tide: przejdź do agenta, który czeka najdłużej" (Tide: go to the agent that has been waiting the longest)) and can be changed there. Window switching goes through KWin scripts anyway.

**Meta+A collision.** KDE will not assign a default key that another action already has. The installer checks `kglobalshortcutsrc` and prints collisions (e.g. "Przełączaj między aktywnościami" (Switch between activities) in plasmashell) without changing anything in them — you then have to free Meta+A in System Settings and assign it to the Tide action.

**Notifications.** Only entering the `waiting` state — the end of a turn (`done`) does not notify. When a session enters the `waiting` state, the service sends, through `org.freedesktop.Notifications`, a notification "Agent czeka: <projekt>" with the reason (e.g. "Czeka na zgodę: shell · make test" (Waiting for permission: shell · make test)), the tool and the branch, and a "Przejdź" button (also a click on the notification) that activates that session's window (`ActionInvoked` → `ActivateSession`). A session with no determined window gets a notification without the button.

- The same session does not get a second notification until it stops waiting (works, finished, idle, ended or vanished from the list) and starts waiting again.
- When a session stops waiting, its notification is closed (`CloseNotification`) so that it does not hang around stale.
- Sessions already waiting when the service starts get no notifications — a service restart does not flood the screen.
- `--no-notifications` turns notifications off.

The logic (choosing sessions, when to notify, the text) is in `tide_daemon/attention.py` without Qt, the bus binding in `notifications.py`.

## Agent state on the Konsole tab

The Konsole tab where a session runs shows its state (decision 29 — the Tide decoration cannot switch the title bar color per window):

| Session state | Tab title | Tab color |
| --- | --- | --- |
| `working` | `● project · branch` | `pastel[semantic.working]` (teal) |
| `waiting` | `◆ project · branch · czeka` | `pastel[semantic.waiting]` (peach) |
| `done` | `○ project · branch` | unchanged (empty circle, informational — #26) |
| `idle`, `ended`, no session | as before marking | as before marking |

The project and branch come from the session (`project`, `branch`); a session without a project gets just the symbol before the existing title. Konsole draws the tab color as a bar under the title; the Tide tab bar stylesheet (`make install-konsole`, decision 48) lays the pill out so that the bar falls under the text. A single tab cannot be given a different text or background color, so the state is carried by the shape of the symbol, the word "czeka" and the bar.

- The tab is identified by `KONSOLE_DBUS_SERVICE` and `KONSOLE_DBUS_SESSION` from the agent process environment (as with switching to a window; the window is not needed, so moving a tab to another window does not bother the marker). When switching to a window temporarily appends a marker to the tab title (above, "Several Konsole windows"), that tab's state marker waits. When one tab holds two sessions, "waiting" wins. Other terminals and apps have no tab API — they stay unmarked; windows without an agent are not touched.
- The change goes through `org.kde.konsole.Session`: the prefix goes into both title formats (`setTabTitleFormat` 0 and 1), so it survives Konsole refreshes, and the displayed title is set immediately (`setTitle(1, …)`, then the formats), because Konsole recomputes it only after activity in the terminal. Color: `setTabColor`. The current tab passes the title to the window, so the marker is also visible on the title bar, in the taskbar and in the window switcher.
- The original formats and color are in `$XDG_STATE_HOME/tide/konsole-tabs.json` (the file disappears when nothing is marked). The tab returns to them when the state passes, when the service shuts down and after its restart. If the user changed the tab title or color in the meantime, their change stays.
- We query Konsole only when the state or the tab label changes, with calls limited to 1 s. `--no-tab-marks` (or `--no-processes`) turns the marker off.

The logic is in `tide_daemon/tabmarks.py` without Qt, the D-Bus backend in `konsole.py`. The colors come from the palette: `palette.json` next to the installed service, `theme/palette/mist.json` in the repository, or `$TIDE_PALETTE`.

## Agent state for the shell prompt

The Tide prompt (`make install-terminal`) shows a "claude pracuje" (claude is working) chip for the shell directory. To avoid querying the service at every prompt, the service writes `$XDG_CACHE_HOME/tide/agents-by-cwd.tsv` on every change of the session list: one row `<state>\t<tool>\t<directory>` per directory (waiting > working > finished; idle and ended omitted). The write is atomic and only when the content changes; when the service shuts down, the file disappears. Code: `tide_daemon/cwdstate.py`.

## Worktrees

| Element | Value |
| --- | --- |
| Object | `/Worktrees` |
| Interface | `org.tide.Daemon.Worktrees` |

| Member | Signature | Description |
| --- | --- | --- |
| `List()` | `→ s` | List of worktrees as JSON (fields below) |
| `Remove(as paths)` | `→ s` | Cleanup of the given worktrees; JSON with a result for each path, in input order. The reply arrives on completion (background work) |
| `RemoveForced(as paths, b deleteUnmergedBranches, s confirmed)` | `→ s` | Removal after the "Usuń mimo to" (Remove anyway) confirmation in the window: also worktrees with changes, unmerged ones and ones without a main branch (`git worktree remove --force`). The branch goes away with `-d` only if merged; an unmerged one stays unless `deleteUnmergedBranches` (then `-D`). The hard blockers (below) apply without exception. `confirmed`: JSON `{path: {dirty, unmerged, unpushed, branch}}` with the state the user saw — when the fresh state is worse, the refusal is `changed`; an empty string = no comparison; invalid JSON = a `bad-request` refusal for all paths. The result is the same as for `Remove` |
| `Refresh()` | `→` | Refresh on demand; the result arrives as a `Changed` signal |
| `Open(s path, s app)` | `→ b` | Opens a worktree: `app` = `terminal` (`konsole --workdir`) or `rider`. Only paths from the list; for `rider`, an existing regular file inside a listed worktree is also accepted (after `realpath`; PT-361, "Otwórz w edytorze" (Open in editor) from the diff view), `terminal` still only a directory |
| `Launchers()` | `→ s` | JSON `{"terminal": bool, "rider": bool, "window": bool}` — which buttons to show; `window`: whether the Worktrees window is installed |
| `ShowWindow()` | `→ b` | Opens the "Tide Worktrees" window (a second launch only shows it). `false` when the window is not installed |
| `Prune(s repoPath)` | `→ s` | `git worktree prune` in a known repository that has worktrees without a directory. JSON: `repoPath`, `ok`, `pruned` (paths), `message`. The reply comes on completion |
| `Fetch(s repoPath)` | `→ s` | "Pobierz" (Fetch) on a repository: `git fetch <remote>` for each remote from the configuration. JSON `{ok, repoPath, remotes, newCommits, message}` or `{ok: false, reason, message, …}` (description and errors below). The reply comes on completion (up to 120 s), then the service refreshes the list (new ahead/behind numbers, `lastFetch`) |
| `PullFastForward(s path)` | `→ s` | "Zaciągnij" (Pull) on a worktree: moving its branch to the upstream **fast-forward only** (`merge --ff-only`), under the card merge repository lock. JSON `{ok, path, branch, upstream, from, to, commits, message}` or `{ok: false, reason, message, path, …}` (description and refusal codes below). The reply comes on completion (up to 10 s waiting for the lock and 60 s of git), then the service refreshes the list |
| `Changed` | signal | The list changed — fetch `List()` again |

List order: by repository name, within a repository the main directory first, then by most recently changed.

Worktree fields:

| Field | Type | Description |
| --- | --- | --- |
| `repo` | string | repository name (the main directory, for a bare one without `.git`) |
| `repoPath` | string | repository directory in which git runs |
| `path`, `name` | string | worktree path and its directory name |
| `branch` | string | branch, or an empty string with a detached HEAD |
| `detached`, `head` | bool, string | detached HEAD and the commit abbreviation |
| `isMain` | bool | the repository's main directory — never removed |
| `hasAgent` | bool | an agent is working in it (the working directory of a live session from the Agents area or of a `claude`/`codex` process; with nested worktrees the deepest one counts) |
| `flowBranch` | bool | the worktree branch is a card branch or a card ticket branch in the card flow (card journal: the intent or result of `card-branch`, `ticket-branch`, `review-branch`, `step-reopen` and the base-change generation branches; a card after its start, before `card.finished` and the merge to the main branch). Computed on list refresh; a journal read error → `false` (a hint for the window — `PullFastForward` reads the journal again and then refuses) |
| `dirty` | int | number of entries in `git status --porcelain` (an untracked directory is one entry); `-1` when the status could not be read (treated like changes) |
| `mainBranch` | string | local main branch: `master`, `main` or `develop` (the one the main directory is on, otherwise the first existing in that order); empty when there is none |
| `merged` | bool | the worktree tip is an ancestor of the local main branch (`unmerged == 0`) |
| `unmerged` | int / null | `git rev-list --count <main>..HEAD`; `null` without a main branch |
| `unpushed` | int | commits only in this worktree: reachable from HEAD, and from no other local branch or remote branch (`rev-list --count HEAD --not --exclude=<branch> --branches --remotes`, state from the last fetch, no network). These are the ones lost after `branch -D` or with a detached HEAD. Computed only for unmerged ones (otherwise `0`); `-1` when it could not be computed |
| `upstream` | string | the branch's upstream abbreviation (`origin/master`, from `for-each-ref`, one call per repository); empty: a branch without an upstream ("tylko lokalnie" (local only)) or a detached HEAD |
| `upstreamAhead`, `upstreamBehind` | int / null | `git rev-list --left-right --count HEAD...<upstream>` (one call per branch): branch commits not in the upstream and upstream commits not in the branch, state from the last fetch (no network). `null` without an upstream or when its ref vanished (upstream deleted locally) |
| `mainAhead`, `mainBehind` | int / null | the same relative to the local main branch (`mainBranch`, chosen as before); `null` without a main branch. Also computed for a detached HEAD and a missing directory (from the tip of the worktree entry); `mainAhead` equals `unmerged` |
| `remotes` | string[] | repository fields (repeated in every worktree of that repository): remote names from the local configuration (`config --local --list --name-only`, not `git remote`); empty list: a repository without a remote |
| `lastFetch` | ISO 8601 / empty | repository field: mtime of `FETCH_HEAD` in the common git directory; an empty string when never fetched (or the repository has no `.git` directory of its own) |
| `lastChange`, `lastChangeTs` | ISO 8601 / epoch seconds | the newest of: the time of the last HEAD commit, the mtime of files pointed to by `git status` |
| `size` | int | bytes on disk like `du -s` (without nested worktrees); `-1` until computed |
| `locked`, `missing` | bool | `git worktree lock` / the directory no longer exists (`prunable`) |
| `removable`, `blocker` | bool, string | whether cleanup will remove it; if not — the first reason (below) |
| `forceRemovable`, `forceBlocker` | bool, string | whether `RemoveForced` will remove it; if not — a hard blocker: `main`, `protected`, `missing`, `locked`, `agent` or `nested` |
| `group` | string | group in the Worktrees window: `active`, `clean`, `decide`, `main`, `missing` (rules below) |
| `title`, `tag` | string | a readable title from the branch name and a muted marker from the prefix: `codex/summary-tabs-03-version-axis` → `summary-tabs 03 · version axis` and `codex`. Detached HEAD: `odłączony HEAD <abbrev>` (detached HEAD <abbrev>) |
| `subject` | string | subject of the last HEAD commit (up to 120 characters) |
| `tool` | string | `claude`, `codex` or empty: from the linked session, otherwise from the location (`~/.codex/worktrees`, `.claude/worktrees`) or the branch prefix |
| `sessionId`, `sessionTitle`, `sessionLastActivityTs` | string, string, epoch seconds | a Claude Code or Codex session from the last 30 days whose working directory lies in the worktree: a live one from the Agents area before an ended one, then the freshest (empty / 0 when none) |
| `lastActivity`, `lastActivityTs` | ISO 8601 / epoch seconds | last activity: the newest of `lastChangeTs` and the last entry of the linked session |

Groups (`group`, the first matching rule):

| Group | When | In the window |
| --- | --- | --- |
| `missing` | the directory no longer exists | a line with `git worktree prune` for the repository |
| `main` | the repository's main directory or a worktree on the main branch (`protected`) | a collapsed "Gałęzie główne" (Main branches) section (the main directories alone are skipped) |
| `active` | an agent is working | "W pracy" (In progress): opening, no checkbox |
| `clean` | `removable` and the branch has a commit of its own (work finished and merged) — immediately, without waiting a day | "Do sprzątnięcia" (To clean up): checkboxes, "Zaznacz wszystkie" (Select all), removing the selected ones |
| `active` | last activity less than 24 h ago | "W pracy": opening and a checkbox (forced removal if it is not `removable`) |
| `clean` | `removable`: merged into the local main branch, clean, no agent, not locked | "Do sprzątnięcia": as above |
| `decide` | the rest: unmerged commits, changes, a lock, no main branch | "Do decyzji" (To decide), with a reason; opening and a checkbox (forced removal), locked ones without a checkbox |

The 24 h window stays for fresh worktrees: one with no commits of its own also looks merged (0 commits outside the main branch), and that is only the start of work. "A commit of its own" is evidence from the reflog, read straight from the files (with no extra git call, only for merged and clean ones): `<.git>/logs/refs/heads/<branch>` has a `commit…`, `cherry-pick`, `revert` or `rebase (pick|reword|edit)` entry after the last "branch: Created from" / "branch: Reset to" / "reset: moving to"; a detached HEAD — `logs/HEAD` from the worktree's private directory after the last `checkout:` or `reset: moving to`. A fast-forward ("merge master: Fast-forward", `pull`) and a reset do not count, and a reset (`reset: moving to`, e.g. `reset --hard master` after a commit) zeroes the count — such a worktree returns to the 24 h window. Without a reflog (disabled, bare repository, reftable, expired) the 24 h window alone applies.

The result of `Remove` and `RemoveForced` for each path: `path`, `removed`, `status` (`removed` — the worktree and its branch, if there was one, are gone; `branch-kept` — the worktree is removed, the branch stayed, the reason in `branchMessage`; `refused` — nothing was removed, the reason in `reason` and `message`), `reason` (empty or a reason), `message` (in Polish), `branch`, `branchDeleted`, `branchMessage`, `freed` (bytes from the last measurement), `forced` (whether it was `RemoveForced`).

Skip reasons (in this order): `main`, `protected`, `missing`, `locked`, `agent`, `nested` (other worktrees lie in the directory, e.g. the ignored `.claude/worktrees/` — `git worktree remove` without `--force` would delete them together with their uncommitted files), `dirty`, `no-main` (no local main branch), `unmerged`, and during the removal itself also `unknown` (a path outside the list), `git` (git refused — the message in `message`), `error` (an exception in the service), and for `RemoveForced` also `changed` (a state worse than in the confirmation) and `bad-request` (invalid `confirmed`). `RemoveForced` skips only the hard blockers: `main`, `protected`, `missing`, `locked`, `agent`, `nested`.

**Fetching (`Fetch`, PT-358).** The only method of this area that uses the network and the only writing command of this area apart from cleanup: a plain `git fetch --no-prune --no-recurse-submodules -- <remote>` for each remote from the local configuration (`remotes`, in turn). It writes `refs/remotes/…` and `FETCH_HEAD` (from which `lastFetch` comes); there is no `--prune` (a remotely deleted branch stays in `refs/remotes`; also when the user has `fetch.prune=true`), `--force`, `--tags` or a refspec from Tide, it does not touch HEAD, the index, local branches or worktrees, no push, rebase, reset, stash. Other rules:

- **Only a repository from the list.** We compare `repoPath` by canonical path (`realpath`: symlinks and `..`) with the repositories from `List()`; another path, an empty string, a relative path → `bad-request` without running git. The path from the list goes to git, not the one from the request. A repository without a remote → `no-remote` (the window then shows no button).
- **No engine lock (review K-36, M1).** Fetch writes only `refs/remotes/*` and `FETCH_HEAD`, which the card flow engine only reads, so it does not take the card merge repository lock (`cardflow.gitops.lock_for`). The engine takes it blocking in the main thread (`StartCard`, `MergeCardToMain`, the base-change preview) — holding it during a network transfer (up to 120 s) would stop the whole service. What remains is a "one fetch per repository" lock (`fetch.fetch_lock`, key: canonical path, no waiting): two requests for the same repository at once (also given through a symlink) — the second gets `busy`. The work runs in a separate thread, so list refresh and cleanup do not wait for the network.
- **No prompts.** `GIT_TERMINAL_PROMPT=0`, and `GIT_ASKPASS` and `SSH_ASKPASS` set to an empty string (`GIT_TERMINAL_PROMPT=0` alone would not stop git from running a program from the user's environment), SSH in batch mode (`GIT_SSH_COMMAND` = the existing SSH command — from the environment, `core.sshCommand` or `ssh` — plus `-o BatchMode=yes`). A missing key or password ends in an `auth` error, not a prompt. The credentials are the user's (the SSH agent, the git credential helper from their configuration — the service runs outside the sandbox). No shell on our side, hooks and `fsmonitor` disabled as in other git calls, `protocol.ext.allow=never`, no background maintenance (`maintenance.auto=false`, `gc.auto=0`). A repository covered by the Git boundary (`gitcommand.boundary_active`: box mode enabled or the repository marked/exposed to a box) is not fetched: git runs there in a sandbox with no network and keys, and `.git/config` is writable from the box (`core.sshCommand`, `credential.helper`, `uploadpack`), so a fetch with the user's credentials would execute someone else's configuration. The result is `boundary` without running `git fetch`; fetching in such repositories requires a separate owner decision and hardening.
- **A 120 s limit** on the whole fetch (`FETCH_TIMEOUT`); after it the git process is killed, and the result is `timeout`.
- **Credentials from URLs do not leave.** Messages, the result and the service log go through `mask_credentials`: `https://user:token@host/…` → `https://***@host/…`. The error message is one sentence in Polish with the remote name and the first (masked) git line.

The result: `ok`, `repoPath`, `remotes` (one per remote: `name`, `ok`, `reason` on error, `newCommits`, `branches`: `{branch, ref, new, commits}` — changed remote branches; `commits` are the commits reachable from the new tip that were in no branch or remote branch before the fetch, `null` when it could not be computed), `newCommits` (the sum), `message` (e.g. `pobrano: origin — 3 nowe commity na master` (fetched: origin — 3 new commits on master), `pobrano: origin — brak nowych commitów` (fetched: origin — no new commits)). When any remote fails: `ok: false`, the `reason` of the first error and a `message` with its description (the remaining remotes are fetched anyway, and their names appended in parentheses).

| `reason` | Meaning |
| --- | --- |
| `bad-request` | a path outside the Worktrees list (symlink, `..`, empty, relative) — git was not run |
| `no-remote` | the repository has no remote |
| `boundary` | the repository is covered by the Git boundary (box mode or a repository exposed to a box) — git was not run |
| `busy` | a fetch of this repository is already in progress |
| `auth` | authentication, SSH key, host key — no password prompt |
| `network` | cannot connect to the remote (address, DNS, connection) |
| `timeout` | 120 s exceeded |
| `git` | another git error (e.g. the remote points to a nonexistent repository) |
| `unavailable`, `error` | the service is shutting down / an exception in the service |

In the Worktrees window the repository bar at the top of the list has a row per repository (the remotes and "pobrano …" (fetched …)) with a "Pobierz" button — only for a repository with a remote; during "Pobieram…" (Fetching…) (disabled), the result or error (one sentence) stays under the row until the next fetch.

**Pulling (`PullFastForward`, PT-353).** The only HEAD write in the area: `git merge --ff-only --no-autostash --no-stat <SHA>` in the given worktree, where `<SHA>` is the `@{upstream}` of the current branch read under the lock right before the merge (the checked state = the merged state, even when a parallel "Pobierz" moves `refs/remotes/…` in the meantime). Tide never merges history or rebases for the user (histories 27–29): when a fast-forward is not possible, it answers with a refusal with a code and one sentence, and no refusal changes HEAD or the tree. There is no `--force`, `reset`, `rebase`, `pull`, `push`, `stash`, `prune`, `checkout`; `merge.autoStash=false`, `submodule.recurse=false`, no background maintenance. There is no network (what is pulled is the state from the last "Pobierz"). Other rules:

- **Only a worktree from the list.** We compare `path` by canonical path (`realpath`: symlinks and `..`) with the worktrees from `List()` (without missing directories); another path, an empty one, a relative one, a non-string → `bad-request` without running git. The path from the list goes to git. The main branch in the main directory is allowed if it is clean.
- **A lock shared with the card merge.** The same repository lock as the card merge (`cardflow.gitops.lock_for`, imported unchanged): a short wait (`pull.LOCK_WAIT` = 10 s, then `locked`) and only in the area's worker thread ("Zaciągnij" has its own thread — it does not stand behind a fetch or a list refresh; the service main thread waits for nothing). The lock is held only for local reads and `merge` (no network), released in `finally` also after an exception and after the git time limit (`pull.PULL_TIMEOUT` = 60 s for a `merge` with a file checkout). Fetch does not take this lock, so there is no nesting or deadlock with "Pobierz". A second request for a worktree we are just pulling gets `locked` without waiting.
- **Fresh state under the lock, right before `merge`.** The branch (`symbolic-ref`), an ongoing git operation, the upstream and its SHA, HEAD, `git status` (also untracked files), ahead/behind (`rev-list --left-right --count`) and the file filters are read after taking the lock — the list data serve only the window.
- **The card journal before the lock.** A card branch or a card ticket branch in the flow (`flowBranch`, the same rules) is recognized with the existing journal reads (`Journal.cards`, `Journal.state`) **before** taking the repository lock: the engine takes `journal.lock` even when it then waits for `lock_for` in the main thread, so reading the journal under `lock_for` in the worker thread would reverse the lock order. The recognition is conservative: card branches after close-out (until merged to the main branch), of merged tickets and of intents with no result also count, an entry with no repository matches every one, and a journal read error is a `card-step` refusal. No events in the card journal.
- **File filters (checkout).** A fast-forward performs a checkout of the changed files; a `filter.<name>.smudge` or `.process` driver (e.g. git-lfs, which reaches for the network, or any program from the configuration) would then run under the lock. When the repository has such a driver in its configuration and one of the changed files has that `filter` (`check-attr` in the worktree) or `.gitattributes` changes, a `git-operation` refusal with the driver name — pulling is left to the user. An interrupted `merge` (time limit, error) is not repaired: a `timeout` or `git` result describes the state after it (HEAD, the number of changes in the directory — consistency after the interruption) and advises `git status`.
- **Environment as for "Pobierz".** No prompts (`GIT_TERMINAL_PROMPT=0`, empty `GIT_ASKPASS`/`SSH_ASKPASS`, SSH in batch mode), hooks and `fsmonitor` disabled as in every git call, `GIT_NO_LAZY_FETCH=1` (a partial clone does not fetch objects from the network). A repository covered by the Git boundary → `boundary` without git.

A successful result: `ok: true`, `path`, `branch`, `upstream` (the abbreviation, e.g. `origin/master`), `from`, `to` (SHA before and after), `commits` (how many commits were pulled), `message` (e.g. `zaciągnięto 3 commity z origin/master (fast-forward)` (pulled 3 commits from origin/master (fast-forward))). A refusal: `ok: false`, `reason`, `message`, `path`, and when already known — `branch`, `upstream`, `ahead`, `behind`; after an interrupted `merge` also `head` and `dirty`.

| `reason` | Meaning (HEAD and tree unchanged unless stated otherwise below) |
| --- | --- |
| `bad-request` | a path outside the Worktrees list (symlink, `..`, empty, relative) — git was not run |
| `agent` | an agent is working in the worktree or a card step/work is in progress (a snapshot from the time of the request) |
| `card-step` | a card branch or card ticket branch in the flow ("tą gałęzią zarządza Tide" (Tide manages this branch)) or an unreadable card journal |
| `git-operation` | a git operation is in progress (`MERGE_HEAD`, `rebase-merge`/`rebase-apply`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `sequencer`, `BISECT_LOG`, `index.lock`, `HEAD.lock`, a branch ref lock) or the changes pass through a file filter |
| `no-upstream` | a detached HEAD, a branch without an upstream or its ref vanished |
| `dirty` | changes in the directory (also untracked files) or the status cannot be read |
| `diverged` | the branch has commits outside the upstream and the upstream has commits outside the branch (ahead and behind) — Tide does not merge or rebase |
| `up-to-date` | nothing to pull (behind 0, also when the branch is only ahead of the upstream) |
| `locked` | the repository lock is held longer than 10 s (a card merge) or pulling this worktree is already in progress |
| `boundary` | the repository is covered by the Git boundary — git was not run |
| `timeout`, `git` | `merge` exceeded 60 s or git refused; the message describes the state after it (HEAD, changes) |
| `unavailable`, `error` | the service is shutting down / an exception in the service |

In the Worktrees window, a worktree row with an upstream has "Zaciągnij ↓N" (Pull ↓N) (the main branch of the main directory — in the repository bar, "Zaciągnij ↓N master"). When the list data imply a refusal, the button is disabled and the reason stands next to it: "pracuje agent" (an agent is working), "trwa krok karty" (a card step is in progress), "tą gałęzią zarządza Tide", "brak upstreamu" (no upstream), "zmiany w katalogu" (changes in the directory), "rozjechana z … — scal albo zrób rebase sam" (diverged from … — merge or rebase yourself), "gałąź aktualna" (branch up to date). During "Zaciągam…" (Pulling…) (disabled, no second request); the result or refusal (one sentence) stays under the row, and the list refreshes after the reply.

Cleanup rules (`Remove`):

- **No network.** No `fetch`/`pull` (`Fetch` above is a separate, user-invoked action) — "merged" means merged into the *local* main branch. A branch pushed and merged only on the server counts as unmerged.
- **No forcing.** Only `git worktree remove <path>` without `--force`; git itself refuses to remove a worktree with changes or untracked files (ignored files, e.g. `node_modules`, disappear together with the directory).
- **Fresh state.** Before removal the repository is read anew (status, merge state, agents as of the call), so the decision does not rest on a list from a minute ago.
- **Branch only safely.** After the worktree is removed, the branch goes away only through `git branch -d` (never `-D`). Git then compares with the main directory's HEAD or with the tracked branch; when it refuses, the branch stays and the result says why.

Forced removal (`RemoveForced`, decision 41):

- **Only after confirmation.** The window calls it only after a stronger confirmation that lists, for each worktree, what will be lost, and has a red "Usuń mimo to" (Remove anyway) button.
- **Hard blockers without exceptions.** The repository's main directory, a worktree on the main branch (`master`, `main`, `develop` or the local main), a missing directory, a locked one (`git worktree lock`), one where an agent is working, and one in whose directory other worktrees lie (`nested`; the nesting state is read again right before removal) — a refusal regardless of flags. Neither `-d` nor `-D` deletes main branches (a second line of defense by branch name).
- **One `--force`.** `git worktree remove --force <path>`: changes and untracked files go with the directory; we do not remove the lock (`-f -f`).
- **Branch.** Merged into the local main branch → `git branch -d`; unmerged or without a main branch → it stays (`status: branch-kept`, `branchMessage` e.g. "gałąź feat/x zostaje (2 commity poza master)" (branch feat/x stays (2 commits outside master))), unless `deleteUnmergedBranches` → `git branch -D`.
- **Fresh state and confirmation.** The repository is read anew as for cleanup; if since the confirmation more changes, unmerged commits or commits only here have appeared, or the branch changed — a `changed` refusal, nothing is removed.

Where the list comes from:

- repositories: directories with `.git` (or bare repositories) in the projects directory (`--workspace`, `$TIDE_WORKSPACE`, by default `~/DevWorkspace`), up to 3 levels deep (`--worktree-depth`), skipping hidden directories and dependencies (`node_modules`, `vendor`, `bin`, `obj`, `target`, `build`, `dist`…); we do not search further inside a repository,
- worktrees: `git worktree list --porcelain` of each repository — also those lying outside the projects directory (e.g. `~/.codex/worktrees`),
- git runs with `GIT_OPTIONAL_LOCKS=0` (status does not create `index.lock`), `GIT_NO_LAZY_FETCH=1` and `GIT_TERMINAL_PROMPT=0`.

Linking with sessions (`worktrees/sessions.py`, best effort, read only): files from the last 30 days — Claude Code `<claude>/projects/<directory>/<session>.jsonl` (the directory name is the path with every character outside `[A-Za-z0-9]` replaced by `-`, so it is irreversible — we take the directory from the `cwd` field of the entries) and Codex `<codex>/sessions/**/rollout-*.jsonl` (`session_meta.payload.cwd`, `turn_context.payload.cwd`; subagent threads are skipped, as are threads imported by Codex Desktop from Claude Code — `external-import-turn-*` turns without any `turn_context`, a copy of a Claude session with artificial import times, because of which a Claude worktree used to get a Codex badge; one imported and continued in Codex counts normally). Title: Claude — `custom-title`, `ai-title`, `summary`, otherwise the first user command; Codex — `thread_name` from `<codex>/session_index.jsonl`, otherwise the first command; at most 80 characters. From a file we read only the beginning and the end (192 KB each), and keep the result until the size or mtime changes (the first scan about 0.2 s, later ones a few ms). Only metadata is in memory (tool, identifier, title, directories, time), nothing from conversations goes to the service log. A session launched in the main directory that creates a worktree with a shell command will not be linked — the working directory counts.

The "Worktrees" view of the Tide window (`apps/worktrees`, window `apps/tide`): `ShowWindow()` launches `/usr/bin/python3 -s $XDG_DATA_HOME/tide/tide-window/tide.py --view worktrees` (or `$TIDE_WINDOW`) through `systemd-run --user --scope`; without the Tide window or with `$TIDE_WORKTREES_WINDOW` — the former launcher `tide/worktrees-window/tide-worktrees.py` (after the Tide window is installed it is a redirect to it). The window registers `org.tide.Window` (object `/Window`, `Show(s view, s item) → b`), so the next launch only shows the one already open on that view; the former `org.tide.WorktreesWindow` `Raise()` works too.

Refreshing: every **60 s** in the background (one worker thread: reading, then sizes), on demand through `Refresh()` (the window calls it at startup and with the "Odśwież" (Refresh) button) and after cleanup. Sizes are cached and recomputed at most every **30 min**. An agent session change recomputes only the `hasAgent` flag, without git. The main thread (D-Bus) never waits for git or the disk.

Opening: Konsole through `konsole --workdir`, Rider through the JetBrains Toolbox script (`~/.local/share/JetBrains/Toolbox/scripts/rider`), `rider` from `PATH` or `$TIDE_RIDER`; without Rider the button disappears. The programs start through `systemd-run --user --scope`, so that a service restart does not close open windows.

## Git interface

Git reads as data (K-36): "what did this card change" without a UI. The `/Git` object next to `/Worktrees`
(`dbus_service.GitAdaptor`, `git_monitor.py`, module `daemon/tide_daemon/gitread/`).

| Element | Value |
| --- | --- |
| Object | `/Git` |
| Interface | `org.tide.Daemon.Git` |

| Member | Signature | Description |
| --- | --- | --- |
| `Diff(s request)` | `→ s` | A diff as data. `request` is a JSON object with a source (below). The reply is `{ok: true, …DiffData}` or `{ok: false, error, message}`; delayed — git runs in the read runner's thread pool (`GitReader.submit`), and the service loop handles other calls in the meantime |
| `BranchMap(s request)` | `→ s` | A map of the project's card and ticket branches as data (K-99, PT-387; described below, "Branch map"). The reply is delayed: the projection of the card journals runs in the same read runner thread pool, not in the main thread |

**The `card` source** (PT-357): `{"source": "card", "card": "K-N", "targetRef": "refs/heads/master"}` (`targetRef`
optional; an empty string = none). It works in every state of a card that has a branch (also after close-out and merge,
as long as the card branch exists).

- Without `targetRef` the range starts at the pinned `baseCommit` from the card journal and ends at the card branch
  ("changes since the start"). A move of the start branch after the start (e.g. new commits on `master`) does not change the start of the range.
- With `targetRef` the range starts at the merge-base of the target and the card branch. The ref can be full (`refs/heads/main`) or
  short (`main`); the read runner validates it (a ref starting with `-` is `bad-request`, git does not run).
- The card's repository comes solely from the journal (`card.started.repo`, written at assignment start; a card from a card
  group — its project's repository). The request gives no paths. The card state is read by the existing read functions
  (`Journal.has`, `Journal.state`, `state.base_of`, `state.ticket_of`); the read does not append events to the journal
  and does not change refs.
- All git commands (`rev-parse`, `merge-base`, `diff`, `log`) go through the hardened read runner
  (`gitread/runner.py`: no hooks, filters, textconv, external diff, pager or gpg from the repository configuration,
  no network); the diff is computed between two SHAs (`--no-renames`, 3 lines of context), the text is parsed by
  `gitread/diffparse.py`.
- `CardBranchDiff` on `/Board` stays unchanged (the diff text for the close-out assessment).

**The `ticket` source** (PT-349): `{"source": "ticket", "card": "K-N", "ticket": "PT-N"}`. Changes of a single card ticket.

- Range: from the merge-base of the card branch and the ticket branch to the ticket branch (`from.ref` — the card branch, `to.ref` —
  `refs/heads/<ticket branch>`), so the second ticket does not contain the first one's files. Title: `PT-12 · zmiany ticketu
  względem gałęzi karty` (PT-12 · ticket changes relative to the card branch); the reply has an additional `ticket` field.
- The repository comes solely from the card journal (as in the `card` source); `repo` in the request is ignored.
  The ticket must be known to the card journal (a reservation, branch, step, assignment or merge), otherwise `bad-request`.
- **Ticket merged, branch deleted.** When the ticket branch does not exist — or does, but the ticket is already in the card branch
  (the range would be empty) — the range is taken from the intent/result pair of the `merge` action in the journal, read with the existing
  state read functions: the `intent` with `expectedTip` (the approved ticket tip) and `cardTip` (the card branch tip
  before the merge) and the `result` with `ok: true`. Range: merge-base(`cardTip`, `expectedTip`) → `expectedTip`
  (`from.ref`/`to.ref` are then SHAs).
  Without such a record (an unmerged ticket without a branch, a ticket taken over from a plan,
  an old journal), with a failed merge or with a SHA that is not in the repository, the reply is
  `{ok: false, error: "no-ticket-range", message}` with one sentence saying what to do next ("Gałąź ticketu PT-12 nie istnieje,
  a dziennik karty nie zapisał zakresu jego scalenia." (The branch of ticket PT-12 does not exist,
  and the card journal did not record the range of its merge.)).

**The `commit` source** (PT-349): `{"source": "commit", "repo": "/path", "sha": "58a3f26…"}`. Range: first parent →
commit (a merge commit relative to its first parent, an initial commit relative to the empty tree: `from.ref` is
`empty-tree`, `from.sha` — the SHA of the empty tree, `from.date` empty). Title: `commit 58a3f26 — subject`. `sha` is a SHA or
any ref that resolves to a commit.

**The `range` source** (PT-349): `{"source": "range", "repo": "/path", "from": "master", "to": "feat/x"}`. A range from
merge-base(`from`, `to`) to `to`, like `git diff from...to` (changes on `from` after the divergence do not enter the result);
`from`/`to` are full refs, short names or SHAs. `from.sha` in the reply is the merge-base. Title: `<to> względem
<from>` (e.g. `feat/x względem master`) (`<to>` relative to `<from>`). No common history is `bad-request`. It is used by "Zmiany względem głównej" (Changes against main)
in Worktrees (PT-360): `repo` is the project repository, `from` — `refs/heads/<the project's local main>`, `to` —
`refs/heads/<worktree branch>`.

**The `worktree` source** (PT-360): `{"source": "worktree", "path": "/path/worktree"}` — the uncommitted work of a directory,
without a terminal. `path` goes to the same validation function as `repo` (below): it must be a worktree from the Worktrees list
(or the Board project repository), by canonical path, without symlinks and `..`; otherwise `bad-request` without
running git on that path. The reply is `DiffData` with two file groups and a list of untracked ones:

- `groups[]` — `{id, name, totals}` in a fixed order: `staged` ("W indeksie" (Staged), `git diff --cached`: the index relative to
  HEAD) and `unstaged` ("Poza indeksem" (Unstaged), `git diff`: the working tree relative to the index); `totals` is the group's `{files, additions,
  deletions}`. Every `files[]` entry has a `group` field (`staged` or `unstaged`); the files are arranged in groups
  (`staged` first), and the same file changed in both places appears twice — once in each group, with different
  hunks. Instead of copying the files into `groups[].files` (which would double the reply), a group points to its files through
  `group`.
- `untracked[]` — paths of untracked files (one per file, also in untracked directories; `status
  --porcelain=v1 -z --untracked-files=all`), **without content**. The list is limited to 2000 files and 1 MB of git output; when
  exceeded, a warning appears in `warnings[]`. Files from `.gitignore` are not listed.
- `title` — `Niezacommitowane zmiany: <canonical path>` (Uncommitted changes: …); `repo` — that path; `from` — `{ref: "HEAD", sha, date}`
  (`sha` and `date` empty for a branch without commits, in which case the `staged` group is computed relative to the empty tree), `to` —
  `{ref: "worktree", sha: "", date: ""}`. `totals` is the sum of the files of both groups (a file in both groups counts twice).
- The "Duże diffy" (Large diffs) limits work separately for each group; warnings have a group name prefix (`W indeksie: …`),
  and `truncated` is true when either was trimmed. Binary file sizes (`oldSize`/`newSize`) are not
  computed for this source — the view then shows a message without numbers.
- `options.path` with an optional `options.group` (`staged`/`unstaged`) returns the content of one file of one group (without
  `group` — of the first group in which the file has changes: `staged` first); the reply has a `group` field, and the file entry
  has `group`. A bad `options.group` is `bad-request`. The other sources ignore `group`.
- The read **does not change** the worktree: the runner runs git with `GIT_OPTIONAL_LOCKS=0` and `diff.autoRefreshIndex=false`,
  so `status` and `diff` neither refresh nor write the index, refs and files stay unchanged, and nothing goes to the card
  journal. The source does not read the card state or the journal — the `path` from the Worktrees list suffices.
- A worktree directory that no longer exists (removed after the list was read — story 17) is a `no-worktree` error with the message
  "Katalog worktree nie istnieje (został usunięty?): <path>" (The worktree directory does not exist (was it removed?): <path>), without running git.

Recipients without group support (the `card`, `ticket`, `commit` and `range` sources) do not get the `groups[]` and `untracked[]` fields
and work unchanged; the diff view treats a missing `groups[]` as before (just a list of files).

**Validation of the `commit`, `range` and `worktree` input** (a permission boundary; one module function, `gitread/validate.py`
— `known_repo` — which the `worktree` source's `path` (PT-360) calls the same way as `repo`, with no second copy; K-99 will
use it too for `Git.Commits`; `Git.BranchMap` takes no paths — it takes the repository from the Board project):

- `repo` (for `worktree` — `path`) must be an absolute path with no `..` components and no control characters, and after resolution to a canonical
  path (`realpath`, without symlinks) equal to the Board project repository (`repo` from the project) or a worktree
  from the Worktrees list (also compared after resolution). Git then runs on the canonical path. A symlink
  pointing outside the list, a path with `..` and any other directory give `bad-request` — without running git.
  The list is built on every request (`GitMonitor.repos_source`); when it cannot be determined, no `repo` is known.
- `sha`, `from`, `to` must be non-empty strings with no control characters, must not start with `-` (they are not
  passed to git as options) and must resolve to a commit
  (`rev-parse --verify --end-of-options <x>^{commit}`). Otherwise `bad-request`.
- All three sources accept `options.path` and `options.context` (like `card`), have the same limits and `DiffData`
  fields; `source`, `title`, `repo`, `from`, `to` mean the same as above, and the `ticket` field appears only
  in the `ticket` source (an additive change).

**`DiffData`** (a contract shared by the following sources and the diff view; additive changes only):

| Field | Type | Description |
| --- | --- | --- |
| `source` | string | the request source (`card`, `ticket`, `commit`, `range`, `worktree`) |
| `card` | string | `K-N` (normalized card identifier); only the `card` and `ticket` sources |
| `ticket` | string | `PT-N`; only the `ticket` source (PT-349) |
| `repo` | string | repository path: from the card journal (`card`, `ticket`) or the canonical validated one (`commit`, `range`, `worktree`) |
| `title` | string | a short description of the range, e.g. `K-36: zmiany gałęzi card/k-36-… od startu` (K-36: changes of branch card/k-36-… since the start) |
| `from`, `to` | object | `{ref, sha, date}`: `ref` — `baseRef` from the journal or `targetRef` (for `to` — `refs/heads/<card branch>`), `sha` — the full SHA of the start/end of the range, `date` — the committer date in ISO 8601 UTC (`2026-11-11T10:00:00Z`). Raw data: the UI formats the "Start od…" (Start from…) label (`startLabel`) |
| `files` | array | files from the diff parser: `path`, `oldPath`, `status` (`A`, `D`, `R`, `C`, `T`, `M`), `additions`, `deletions`, `binary`, `oldMode`, `newMode`, `warnings`, `hunks` (`oldStart`, `oldLines`, `newStart`, `newLines`, `header`, `lines` — `kind` ` `/`-`/`+`, `oldNo`, `newNo`, `text`); the fields from "Large diffs" below appear only on the files they concern |
| `totals` | object | `{files, additions, deletions}` — for the whole range, also after trimming (computed from the full file list) |
| `truncated` | bool | `true` when the whole exceeded the limit and the content of some files is omitted (below); otherwise `false` |
| `warnings` | array | warnings: from the parser (text before the first header) and from the limits (trimming) |
| `groups` | array | `worktree` source only (PT-360): file groups `{id, name, totals}` (`staged` "W indeksie", `unstaged` "Poza indeksem"); a `files[]` entry points to its group with the `group` field |
| `untracked` | array | `worktree` source only (PT-360): paths of untracked files, without content |

**Large diffs** (PT-355; fields only added to `DiffData`):

- **Total limit:** 2 MB of diff text or 20,000 lines — whichever comes first. The limit is counted while reading git's output
  (streaming, `GitReader.run(max_bytes, max_lines)`): when exceeded, the git process is interrupted, and the excess
  never reaches memory. The result is `truncated: true` and a warning in `warnings[]` ("Diff przekracza limit …:
  pokazano część zmian (N z M plików z treścią). Wybierz plik, żeby doczytać jego zmiany." (The diff exceeds the limit …:
  showing part of the changes (N of M files with content). Select a file to load its changes.)). **The list of all files
  is always complete** — after trimming, the service adds it from `git diff --raw` and `--numstat` (without content), so
  `files[]` and `totals` cover the whole range. A file whose content did not fit in the limit in full has
  `truncated: true` and `hunks: []`; files read in full keep their hunks.
- **A `large` file:** a file with more than 3,000 changed lines (`additions + deletions`) has `large: true`, `lines` (the number of
  changed lines) and `hunks: []` — also when the diff fits in the total limit. `large` takes precedence over `truncated`.
- **A binary file:** `binary: true` (without hunks) and `oldSize` and `newSize` — sizes in bytes from the trees of both commits
  (`git ls-tree -l`); `null` on the side where it does not exist (an added file: `oldSize: null`, a deleted one: `newSize: null`).
- **`options.path`** (a file path relative to the repository): the reply has the same `DiffData` shape (the same
  `from`/`to`), `files` contains exactly one file **with hunks** — also a `large` and a trimmed one — and `totals` concern
  that file; the additional `path` field is an echo of the request. A file with no changes in the range is an `unknown-path` error. A single file
  has its own, looser limit of 10 MB or 100,000 lines; when exceeded, the file has `truncated: true`, the reply
  `truncated: true` and a warning, and the hunks are the beginning of the changes. The view (#13) inserts such a file in place of the list entry.
- **`options.context`** (an integer 0–100000, 3 by default): the number of hunk context lines of the file from `options.path`
  (`git diff --unified=N`), e.g. 20 to expand the context. It requires `options.path` (without it `bad-request`), because it concerns
  a single file. Unknown `options` keys are ignored.
- A request with `options` has the same source selection (`source` and its fields) as the list; the contract is identical for the sources
  `card`, `ticket`, `commit`, `range` (PT-349) and `worktree` (PT-360; here `options.group` points to the file's group).

**Syntax highlighting in the diff view** (K-36, PT-351): code lines of `.py`, `.qml`, `.js`, `.cs`, `.md`, `.yml`/`.yaml`, `.json`
and `.sh` (language from the extension, `Diff.languageFor`) get syntax colors if the system has the QML module
`org.kde.syntaxhighlighting`. The line delegate has `SyntaxText.qml`: a Loader with a light, read-only `TextEdit`, created
only for visible lines of a known language (not for a `large` or binary file, not for hunk headers). The highlighter
is a `Component` injected at a single point (`DiffView.highlighter`, a mock in tests); by default the view loads
`ModuleHighlighter.qml` dynamically through `Qt.createComponent` and without the module falls back to a plain `Text`, and the cheat sheet
(`?`) says "Podświetlanie składni niedostępne (brak modułu KSyntaxHighlighting)" (Syntax highlighting unavailable (KSyntaxHighlighting module missing)). Theme: "Tide", generated from the palette
(`theme/syntax/generate.py`, installed by `make install-appearance` to `$XDG_DATA_HOME/org.kde.syntax-highlighting/themes/`);
every text color has a contrast >= 4.5:1 (WCAG 2.x) on the window background and on the added and removed line backgrounds, styles have no format
background. Without the theme installed, `ModuleHighlighter` uses the module's dark theme. The backgrounds of added, removed and
context lines do not change; `DiffView.readableColor` replaces the format color with palette text for highlighters that
use it (a mock in tests). Limitations: every line is a separate document, so multi-line constructs (block comments,
multi-line strings) are not recognized; the module's format colors cannot be replaced from QML, hence the separate theme; the theme
is derived from Mist (the palette variants have none of their own).

**The diff view** (`apps/common/qml/git/DiffView.qml`, `diff.js`): a `large` file is collapsed with a "duży plik —
N linii, pokaż" (large file — N lines, show) button (a click sends `Git.Diff` with `options.path` and inserts the content); a binary file shows "plik binarny (rozmiar
przed → po)" (binary file (size before → after)) without content and loads nothing more; with `truncated: true` there is a bar above the content "Pokazano część zmian;
wybierz plik, żeby doczytać" (Showing part of the changes; select a file to load more), and selecting a file without content (`truncated`) loads it by itself through `options.path`. A load
error shows a message and "Spróbuj ponownie" (Try again); "Odśwież" (`r`) reads everything anew and discards late
load replies.

**The merge window** (`apps/common/qml/git/MergeWindow.qml`, K-99 PT-390): a shared overlay (it replaced the former Board
preview). A read-only target zone ("K-12 → master", the full ref in a tooltip, "Start od: …" from `start.js`), a minimal
preview of the `PreviewCardMerge` reply (the state in a sentence: "Można scalić" (Can merge) / "Konflikty w N plikach" (Conflicts in N files) / "Scalenie zablokowane" (Merge blocked),
the conflicting files, `message` and `blockedReasons[].message`, warnings, a collapsible "Szczegóły" (Details) with the SHA, the result tree,
the codes and the previous preview content) and always-visible buttons "Scal do <cel>" (Merge into <target>), "Zmiany gałęzi" (Branch changes), "Odśwież",
"Zamknij" (Close). The component does not call the service: the host receives `previewRequested(targetRef)`,
`mergeRequested({targetRef, previewToken})`, `changesRequested(cardId)` and `closed()`, pins the tag of the sent request
(`track(tag)`) and hands back the reply through `receive(tag, error, reply)`; a reply with a different tag is skipped.
Consent is solely `mergeRequested` with the fields of the current preview (`canMerge === true` and a non-empty `previewToken`);
closing, Esc and refresh do not send it, and a `MergeCard` error blocks the button until a new successful preview.
The Board closes the window on a card context change (`mergeContext`) and after success shows "Scalono K-12 do master." (Merged K-12 into master.).
The card panel and the full card have a single "Scal…" (Merge…) (`cardMerge`, `fullMerge`; K-99 #16) for a closed and unmerged card:
it opens the merge window (`BoardPage.openMergeWindow`) and immediately computes the preview for the card base when it is known as a local
branch, and without it opens the target selection. A card in progress has no button; a merged card shows instead of it the text
"Scalono do master (11.11 19:02)" (Merged into master (11.11 19:02)) (`start.js` `mergedLine`: the target from `flow.merge`, the event time `ts`).
The former Board merge components and the merge path without a token from the windows (the `mergeMainRequested` signal, the backend slot,
the entry on the writes list) were removed; the service method `/Board MergeCardToMain` stays.

**Target selection in the merge window** (K-99 #6): the target zone is a selector. A window with no given target (`open(card, "", "")`)
asks the host for `ListCardBranches` (`branchesRequested(text)` → `purpose: merge`, `trackBranches(tag)`; the reply
`receiveBranches(tag, reply)`) and takes from it only `defaultTargetRef`, `mainRef` and `recentTargets` — the window's QML
has no `master` or `origin` names, and a reply without `mainRef` hides the "Główna" (Main) shortcut and guesses nothing. A default
selectable target gets a preview at once; with an unknown base (empty `baseRef`) and no `defaultTargetRef`, the named main
is only a suggestion: the preview runs, but "Scal do" (Merge into) waits for a conscious choice of target (a click on the suggestion or on another
target). The shortcut (the card base, the main, recent targets) and the local branch search result are a `BranchPicker`
(`apps/common/qml/git/BranchPicker.qml`, shared with the Board assignment window and the future Worktrees window); every target
change clears the preview (`previewRequested`, "Scal do" disabled until the reply), and a `selectable: false` target shows
the reason and sends no preview. The name of a new local target (a remote base without a tracking one) has a separate "Podgląd
nowego celu" (Preview of the new target) button. Choosing a target does not change the card base and is not consent.

**The merge window in Worktrees** (K-99 #10, PT-395): in the "Gałęzie" (Branches) mode, a selected branch of a closed and unmerged card
(`state: closed` from `Git.BranchMap`) has a "Scal…" button; a card in progress, a merged one, a ticket and a base do not have it. "Scal…"
opens the same window (`MergeWindow.qml`) with no given target: the known card base is the node's `started.ref` (empty — the base is
unknown, the target is a suggestion), and "Start od: …" is computed by `cardStartLine` from `start.js` on the node's `started` fields. The page
(`apps/worktrees/qml/BranchesPage.qml`) only translates the window's signals into the backend slots `listCardBranches`,
`previewCardMerge`, `mergeCard` (tags `merge-targets:`, `preview-merge:`, `merge-card:` pinned by
`track`/`trackBranches`) and hands back the replies from the `boardReplied(tag, json)` signal. The Worktrees backend
(`apps/worktrees/backend.py`) has in its list of allowed `/Board` calls (`BOARD_CALLS`) exactly `ListCardBranches`,
`PreviewCardMerge` and `MergeCard` — any other name gives a `not-allowed` reply without calling the service; it does not create cards
and has no old merge path without a token. The consent request passes unchanged (one click — one `MergeCard`),
the backend does not remember tokens and retries nothing; a refusal (e.g. `stale-preview`, when the same card was merged by the Board
window) shows the reason and blocks "Scal do" until a new preview. A project change and the card vanishing from the map close
the window, a map refresh does not close it. After success the page shows "Scalono K-5 do main." (with `already: true` —
"gałąź karty była już w celu, bez nowego commita" (the card branch was already in the target, no new commit)) and reads the map anew, so the node has "scalona do <cel>" (merged into <target>).
There is no "Zleć rozwiązanie konfliktów" (Assign conflict resolution) in Worktrees (the conflict card — from the Board). A preview from a file (`--data`)
answers `preview` with no calls.

**File groups and untracked files in the view** (PT-360): for a reply with `groups[]`, the file list has a header for each
non-empty group with the file count and the total (`W indeksie · 2 pliki · +3 −1`), and under the files — a header "Nieśledzone · N plików
(bez treści)" (Untracked · N files (no content)) and the paths alone (plain text, no selection and no preview). A file in both groups is two rows; `]`/`[`
walk the files in display order, and loading a file (`options.path`) also sends `options.group`. A list of untracked files alone
(with no files in the groups) is not "no changes"; a clean worktree shows "Brak niezacommitowanych
zmian." (No uncommitted changes.). A reply without `groups[]` looks as before.

**Worktrees** (PT-360): for a worktree with uncommitted changes (`dirty` ≠ 0) the window has "Zmiany w katalogu" (Changes in the directory) (the `worktree`
source, the worktree `path`), and for a worktree on a branch other than the project's local main — "Zmiany względem głównej"
(the `range` source). The view opens over the whole window above the list; Esc or "Zamknij" return to the list. The window backend calls
`Git.Diff` (`/Git`) with the method `gitDiff(tag, request)` and hands back the reply with the signal `replied(tag, json)`.

**Error codes** (`{ok: false, error, message}`, `message` in Polish):

| `error` | When |
| --- | --- |
| `bad-request` | the request is not a JSON object, an unknown `source`, a missing or bad card identifier, `options` is not an object, a bad `options.path`/`options.context` (also `context` without `path`), `targetRef` is not a string, does not exist or has no common history with the card branch; the `ticket` source: a bad ticket or one unknown to the card; `commit`/`range`: `repo` outside the Board projects and the Worktrees list (also through a symlink and `..`), a ref starting with `-` or not being a commit, `from` and `to` without common history (PT-349); `worktree`: `path` outside the Worktrees list (the same rules as `repo`) or a bad `options.group` (PT-360) |
| `unknown-card` | the card has no flow journal or was not assigned (no `card.started`) |
| `no-branch` | the card has no branch (no `card-branch` result, no repository in the journal, the repository directory or the card branch does not exist) |
| `unknown-base` | without `targetRef`, when the start base in the journal is unknown or its commit is not in the repository; the message asks for an explicit comparison ref (`targetRef`) |
| `unknown-path` | `options.path` points to a file with no changes in the diff range (PT-355) |
| `no-ticket-range` | the `ticket` source: the ticket branch does not exist, and the card journal did not record the range of its merge (or the recorded SHA does not exist in the repository); `message` says in one sentence what to do next (PT-349) |
| `no-worktree` | the `worktree` source: `path` is in the Worktrees list, but the directory no longer exists (removed after the list was read); `message`: "Katalog worktree nie istnieje (został usunięty?): <path>" (PT-360) |
| `unavailable` | the card flow is disabled in the service (no card journal) or git cannot be run |
| `timeout`, `cancelled`, `unsafe-config`, `failed` | read runner errors: the time limit (30 s), the service shutting down, a `filter.*` driver with a name the runner cannot disable, another git error |

### Branch map (`Git.BranchMap`, K-99, PT-387)

How the project's card and ticket branches hang on each other — data for the "Gałęzie" mode of the Worktrees window. This part is a **pure
projection of the card journals and the Board, without running git** (`gitread/branchmap.py`); git metrics (branch
existence, old start, worktree, upstream) arrive in #11 as new node fields, without changing the existing ones.

- `GitMonitor` gets, next to the journal source, a Board source (`board_source`: projects `slug`, `name`, `repo` and cards
  `project`, `title`, `column`; read only). The Board source is called in the main thread, like the `Diff` repository list,
  and the projection of many journals — in the read runner pool. **The map's repository comes from the Board project,
  never from the request.** The card state is read only by the existing functions (`Journal.state` → `fold`, `base_of`,
  `ticket_of`, `merge_of`); the read does not append events to the journal, does not write the Board and does not change refs.
- **No `mainRef` field** (also after #11): the map does not need the main branch — the tree hangs under the base refs from the journals.
  The "named main" for the merge window is supplied by `ListCardBranches` (`mainRef`), and the card has `flow.mainBranch` in the snapshot.

**Request `{}`** — the Board projects with a repository (the selection list in Worktrees), by name:
`{ok: true, projects: [{slug, name, repo, cards}]}`; `cards` is the number of Board cards with that main project. A project
without a repository is not on the list.

**Request `{"project": "<slug>", "limit"?: N}`** — the project map:
`{ok: true, project, repo, nodes, edges, total, truncated, warnings}`.

- A card belongs to the map when its last assignment (`card.started`) concerns this project (`project`) or its
  repository (canonical path). A card without a branch (no successful `card-branch`) has no node.
- `nodes[]` — first the `base` nodes (by ref) and `unknown-base`, then the cards from the newest, each with its tickets:

| Field | Description |
| --- | --- |
| `id` | `base:<ref>`, `unknown-base`, `card:K-N`, `ticket:K-N:PT-M` |
| `kind` | `base` — a ref used in the journals as a start base or a card merge target (e.g. `refs/heads/develop`, `refs/remotes/origin/main`; not created for `master`, which the journals do not mention), `card` — the card branch from the `card-branch` result, `ticket` — the ticket branch from the `ticket-branch` result, `unknown-base` — one group of cards with an `unknown` base |
| `ref`, `branch` | the full ref and the short name (`refs/remotes/origin/develop` → `origin/develop`); empty for `unknown-base` |
| `card`, `ticket` | `K-N` (card and ticket), `PT-M` (ticket) |
| `title` | card — the title from the Board; ticket — the title from the approved plan or the reservation; `unknown-base` — "Nieznana baza" (Unknown base) (the field is omitted when absent) |
| `column` | card only: the column from the Board card file |
| `state` | `active`; `closed` — a card that is closed out (`card.finished`/`card.closed`) or in Gotowe/Archiwum (Done/Archive), an unmerged ticket of such a card; `merged` — a card merged into its target (`card.merged`/`card.main-merged`), a ticket merged into the card branch (the `merge` result); `rejected` — a rejected ticket (`ticket.rejected`); `base` and `unknown-base` nodes — `active` |
| `started` | `{ref, sha, date}`: card — the base from `base_of` (after an effective base correction `card.base-changed` the new base, and the original one in `started.initial: {ref, sha}`; an `unknown` base — empty `ref` and `sha`), `date` — the time of the assignment or correction; ticket — the card branch, the start SHA from the `ticket-branch` result and its time |
| `merged` | `{targetRef, sha, date}`: card — the target, `resultCommit` and time from `card.merged` (the old `card.main-merged` through `merge_of`); ticket — the card branch, the commit and the time of the `merge` result |

- `edges[]` — `{kind, from, to}`. `start`: from the parent to the branch — a card from its base node, from another card's branch
  on the map (matched by full ref: a card started from `refs/heads/card/k-12-…` hangs under `card:K-12`) or from
  `unknown-base`; a ticket from the card branch. `merge`: from the merged branch to the target — ticket → card (from the `merge` result,
  also when the ticket branch was deleted: the data come from the journal), card → the target from `card.merged` (another card's branch
  on the map or a `base` node). Every end of an edge is a node of the reply.
- **Limit:** `limit` is the number of card and ticket branches, 200 by default, at most 500 (a larger one is cut to 500;
  zero, a negative number or a non-number is `bad-request`). The newest cards are selected — by the last event of the card
  or one of its tickets — together with their newest tickets, so a ticket never stays without its card.
  `total` is the number of all card and ticket branches of the project, `truncated: true` — some were omitted. When a card
  from which another started was omitted, the other hangs under a `base` node with the ref of the omitted card's branch.
- `warnings[]` — sentences in Polish, e.g. a card journal that could not be read (the card is skipped, the rest of
  the map stays), or a card merge without a recorded target.

**Errors** (`{ok: false, error, message}`; an unexpected exception does not go into the reply — only into the service log):

| `error` | When |
| --- | --- |
| `bad-request` | the request is not a JSON object, `project` is not a non-empty string, a bad `limit` |
| `unknown-project` | there is no Board project with that slug |
| `no-repo` | the project has no repository |
| `unavailable` | the Board or the card flow (the card journal) is disabled in the service, or the Board could not be read |
| `cancelled`, `failed` | the service shutting down; an unexpected projection error (a generic message) |

## New task

The "Tide: nowe zadanie" (Tide: new task) window (`apps/newtask`, shortcut Meta+Alt+A, the "＋ Zadanie" (+ Task) tile in the dock, "＋ Nowe zadanie" (+ New task) in the agents widget) collects the repository, the model (cloud or local), the tool (in the cloud Claude Code or Codex; with a local model the program from Tide → Modele (Models)), the command, screenshots and the "Nowy worktree" (New worktree) decision, and the service creates the worktree and opens a Konsole tab with the agent. Rationale: decisions 47, 70 and 72 in `docs/decisions.md`.

| Element | Value |
| --- | --- |
| Object | `/Tasks` |
| Interface | `org.tide.Daemon.Tasks` |

| Member | Signature | Description |
| --- | --- | --- |
| `Repos()` | `→ s` | Repositories from the projects directory as JSON, from most recently active (the freshest activity of any worktree: changes, commits, agent sessions). From the Worktrees area list; right after the service starts — a one-off background scan (the reply comes on completion) |
| `Defaults()` | `→ s` | JSON: the remembered choice of `repoPath`, `tool` (the cloud switch), `newWorktree`, `model` (`cloud` or `local`), the available programs `tools` (`{"claude": bool, "codex": bool}`), `terminal` (whether Konsole exists), `imagesDir`, `promptLimit`, `imageLimit` |
| `SuggestBranch(s repoPath, s tool, s prompt)` | `→ s` | JSON `{ok, branch, path, base, slug}` or `{ok: false, error, message}`: the branch from the command and the worktree directory; a taken name gets `-2`, `-3`… |
| `Start(s request)` | `→ s` | A request `{repoPath, tool, prompt, newWorktree, branch, images, addDirs, model}` (`model`: `cloud` — the default, or `local`; with `local` the service skips `tool` and takes the program from `agentProgram` in `models.json`), the result `{ok, error, message, cwd, branch, worktree, base, title, repoPath, tool, mode, model, local}` (`mode`: `tab` or `window`; `local`: `{endpoint, model, server}` or `null`). The reply comes after the agent starts or after rollback |
| `ShowDialog()` | `→ b` | Opens the "Tide: nowe zadanie" window (a second call only shows it). `false` when the window is not installed |

Repository fields: `name`, `path`, `branch` (the main directory's branch), `mainBranch` (the local master/main/develop as in Worktrees), `dirty`, `lastActivityTs`, `worktreeDir` (where the new worktree will be created).

**Branch name** (`worktrees/naming.py`, `branch_from_prompt`): `<tool>/<description>` — the first six significant words of the command, without Polish characters and function words, at most 48 characters ("Popraw liczenie VIN-ów w wyszukiwarce" (Fix counting VINs in the search) → `codex/popraw-liczenie-vin-ow-wyszukiwarce`). The same convention as the agent branches the Worktrees window already knows: the tool marker from the prefix, the tool guessed first from the prefix (`guess_tool`). The name can be changed in the window.

**Worktree.** The directory: `<repo>-worktrees/<name>` if such a directory already exists next to the repository, otherwise `<repo>/.claude/worktrees/<name>` (the `claude --worktree` convention, the same for both tools); the directory name is the branch without the tool prefix. When git does not ignore `.claude/worktrees`, the service appends `/.claude/worktrees/` to the local `.git/info/exclude` (not to the tracked `.gitignore`), so that the main directory does not turn "dirty". The command: `git worktree add -b <branch> <directory> <local main branch commit>` — the main branch is not changed or switched, no `fetch` (local state, as in the whole Worktrees area). With "Nowy worktree" off — the agent starts in the main directory, on whatever is there.

**Additional directories** (`addDirs`, launched from a board card): the card project's AI documents directory
with `ai-docs: tide` and the repositories of the card's additional projects (PT-28), which the agent receives through
`--add-dir` (both tools). At most 6, checked after resolving symlinks: an existing directory under
`<board>/docs/` (the board from `--board-dir`, otherwise `$TIDE_BOARD_DIR` or `~/DevWorkspace/tide-board`)
or the main directory of a repository from the projects directory that is a board project (`projects/*.md`,
`repo`) — nothing else from the board, no repository from outside the projects directory and none that is not a project
(otherwise `bad-add-dir`). The task's own repository and duplicates are skipped.

**Local model** (`"model": "local"`, `tasks/local.py`). The model comes from the `agent` role in `models.json` (`point:model` or `{"model": "point:model", "ctx": N}`; set by the "Modele" (Models) view in the Tide window). Next to the repository, the window shows a "Chmura" (Cloud) / "Lokalny" (Local) choice from the `Status()` state of the `/Models` object (the `agent` role: `endpoint`, `model`, `available`, `error`; `agentProgram`; refreshed after `Changed`), and below it "Agent": in the cloud a Claude Code / Codex switch, with "Lokalny" — instead of it a single line "Codex na qwen3-coder:30b · Ten komputer" (Codex on qwen3-coder:30b · This computer) (the program, the model and the server named as in the "Modele" view: "Ten komputer" (This computer) for a local address, otherwise the name from the settings, e.g. "Mac-Studio") with a "zmień w Tide → Modele" (change in Tide → Models) link. **The local agent program** (PT-39, decision 72) is `agentProgram` in `models.json` (`codex` — the default — or `claude`), set once in the "Modele" view at the Agent role: with `local` the service takes the program from there and skips the `tool` from the request (the window sends the same one), and a local start remembers "Lokalny" but does not overwrite the remembered choice of the cloud switch (`tool` in `new-task.json`). Without a role, with a server that does not respond, or without a model on the server the option is disabled with a short reason and a "ustaw w Tide → Modele" (set in Tide → Models) link (the Tide window on the `models` view; with "Lokalny" selected the link is in the agent line). A remembered "Lokalny" always stays selected — the window never switches to the cloud by itself: until the first `Status()` reply (the state `{"pending": true}`, different from `null` — "the service gives no state") or until the service has read the server, the start is blocked with "Sprawdzam serwer modeli…" (Checking the model server…), and when the model is unavailable (also on a `Status()` error) — with "Model lokalny niedostępny: <powód>" (Local model unavailable: <reason>); only the user switches to the cloud. An `agent` role with an Ollama Cloud model (`…:cloud`, `…:120b-cloud`) disables the option ("działa w chmurze Ollamy" (runs in Ollama's cloud)), and the service refuses it at start (`local-cloud`). At start the service reads `models.json` and checks the point once more (online, model pulled), and takes the context from the role (`ctx`), otherwise from the server when the model is loaded.

| Tool, server | argv (before `--add-dir`, `--image`, `--`) | Environment |
| --- | --- | --- |
| Codex, Ollama on this computer, no token | `--oss --local-provider ollama --model=<model> [-c model_context_window=<ctx>]` | `CODEX_OSS_BASE_URL=<address>/v1` |
| Codex, another server (Mac, `openai`) or with a token | `-c model_provider="tide-<point>" -c model_providers.tide-<point>.name="Tide: <point>" -c ….base_url="<address>/v1" -c ….wire_api="responses"`, with a token `-c ….env_key="TIDE_MODEL_TOKEN" -c shell_environment_policy.exclude=["TIDE_MODEL_TOKEN"] -c features.shell_snapshot=false`, then `--model=<model> [-c model_context_window=<ctx>]` | with a token `TIDE_MODEL_TOKEN` from the file (for Codex only — the commands it runs do not get it) |
| Claude Code, any server | `--model=<model>`, with a token `--settings {"apiKeyHelper": "cat -- <tokenFile>"}` | `ANTHROPIC_BASE_URL=<address>`, without a token `ANTHROPIC_AUTH_TOKEN=ollama` (with a token the variable is removed — it takes precedence over apiKeyHelper), `ANTHROPIC_API_KEY=` (empty), `ANTHROPIC_MODEL` and `ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS,FABLE}_MODEL=<model>`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, with `ctx` ≥ 32768 `CLAUDE_CODE_MAX_CONTEXT_TOKENS=<ctx>` and `CLAUDE_CODE_MAX_OUTPUT_TOKENS=<ctx/4, at most 32000>` |

The model goes as a single argument (`--model=<model>`), so a name starting with "-" cannot become an option. Codex 0.114 supports only the Responses API (it rejects `wire_api = "chat"`), so a server of a kind other than Ollama must have `/v1/responses`; Claude Code needs `/v1/messages` (Ollama has both). Claude Code does not get a window smaller than 32k: at 16k (this computer) Claude Code's own prompt (about 11k) with a reserve for the answer does not fit and Claude Code compacts endlessly or refuses — without the setting it assumes 200k and short tasks work, while a longer conversation is trimmed by the server. The card title: "projekt · gałąź · lokalnie" (project · branch · locally).

**The token** (a point's `tokenFile`) does not go into argv, the card title, the log, the task file or the commands the agent runs (verified: `env` in a session of both tools does not show the token, and the server receives `Authorization: Bearer`). The task file (0600) has only paths: `secretEnv` (`{"TIDE_MODEL_TOKEN": "<tokenFile>"}` — `launch.py` reads the token at start time and puts it into Codex's environment), `tokenFiles` (checked before the start) and `unsetEnv` (variables removed from the agent environment). Codex excludes the token from commands through `shell_environment_policy.exclude` (this `-c` option replaces the user's `exclude` list from `config.toml` for the duration of that session), and the shell snapshot (`shell_snapshot`) is disabled, because without it `env` in a 0.114 session showed the token despite the exclusion. Claude Code gets the token from `apiKeyHelper`, so it is not an environment variable; `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` is out — on this computer it launches Bash through `bwrap` with a user namespace, which AppArmor forbids (`kernel.apparmor_restrict_unprivileged_userns=1`), and every command ends in "bwrap: setting up uid map: Permission denied" (it also forces the `default` permission mode). An unreadable or empty token file or a relative path — the agent does not start, and the card keeps a shell with a message without the token content. A task with a local model is launched by `launch.py` itself (a child process with the agent environment), and then it turns into a shell with the card's original environment: the variables added for the agent disappear, and those the user had before return with their values — a `claude` typed later in that card will not silently go to the local server.

**Refusals** (the `error` field, in Polish in `message`; nothing is created): `bad-model` (a model other than `cloud`/`local`), `local-unset` (no `agent` role — "ustaw go w Tide → Modele" (set it in Tide → Models)), `local-cloud` (an `agent` role with an Ollama Cloud model — "model … działa w chmurze Ollamy — wybierz model lokalny w Tide → Modele" (model … runs in Ollama's cloud — choose a local model in Tide → Models)), `local-unavailable` (a role with an error, the server does not respond, the model is not on the server), `bad-request`, `bad-tool`, `missing-cli` (the program is not in the service's PATH nor in `~/.local/bin`), `no-terminal`, `unknown-repo` (only repositories from the projects directory, no bare ones), `empty-prompt`, `one-word` (a single word could be taken for a subcommand, e.g. `claude doctor`), `prompt-too-long` (32,000 characters), `bad-prompt` (NUL), `bad-image`, `bad-add-dir`, `no-base` (no master/main/develop), `bad-branch` (`git check-ref-format --branch`), `protected-branch` (master, main, develop, the main branch, HEAD), `branch-exists`, `path-exists`, `git`, `launch` (Konsole did not launch the task — the worktree and the branch rolled back).

**The agent command** (`tasks/agentcmd.py`: `interactive()` and `agent_argv()`; `tasks/core.py` only calls them) — an argument list, never a string for the shell; the command is a single element, after `--`:

- Claude Code: `claude [--add-dir <directory from addDirs>] [--add-dir <screenshots directory>] -- "<command>[\n\nZałączone zrzuty ekranu (przeczytaj je narzędziem Read):\n<paths>]"` (the appended text is "Attached screenshots (read them with the Read tool):") — Claude Code has no option for images, so the paths go at the end of the command, and `--add-dir` lets it read them without asking for permission,
- Codex: `codex [--add-dir <directory from addDirs>] --image <file> … -- "<command>"` (`-i/--image` attaches images to the first message, `--add-dir` grants write access to a directory outside the working directory).

**One place, two variants** (`tasks/agentcmd.py`, PT-76). The command and environment of Claude Code and Codex (cloud, local model, another point) are assembled by one module: `interactive()` — a session in Konsole, described above (the "Nowe zadanie" window and an assignment from the Board go the same way, `Planner.prepare`), and `background()` — a background step, `claude -p` or `codex exec`, following the PT-75 pattern (the card flow specification, "Claude Code sandbox settings pattern for a background step"). The background step:

- the command goes through stdin (the file `<step>/in/prompt.txt` opened as the program's stdin), never in argv; Claude Code: `-p --setting-sources= --settings <step>/in/settings.json --permission-prompts none --strict-mcp-config --output-format stream-json --verbose --session-id <uuid>` (or `--resume`), sandbox settings (`failIfUnavailable`, `allowUnsandboxedCommands: false`, `denyRead: ["~"]` with `allowRead` for the worktree, the shared `.git`, `in` and `out`, `allowWrite` only `out`, `allowedDomains: []`); Codex: `exec --json --cd <worktree> --sandbox workspace-write -c sandbox_workspace_write.writable_roots=[<.git/worktrees/wt>, objects, refs, logs, <step>/out] -c approval_policy="never" -`,
- the environment is built from scratch (nothing is inherited): `HOME`, `USER`, `PATH`, `LANG`, `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS` and `SSH_ASKPASS` set to `/bin/false`, `GIT_SSH_COMMAND` without an agent and keys, an empty `credential.helper` and `core.askPass`, `GH_CONFIG_DIR` pointing to an empty directory, a dead `DBUS_SESSION_BUS_ADDRESS`, `GIT_CONFIG_GLOBAL=<step>/in/gitconfig` (author "Krok" (Step), `core.excludesFile` with sandbox dummies), Claude: `CLAUDE_CODE_TMPDIR` outside `~`, Codex: `GIT_CONFIG_NOSYSTEM=1`. The step directory does not lie in `/tmp` or in `~/.local/share` (Codex from the snap does not see them), Codex: nor in a hidden home directory; the worktree not under `/tmp/claude-<uid>`. This is the second layer — the hard boundary is the sandbox,
- **user skills (PT-216):** `tasks/skills.py` looks for `SKILL.md` in `~/.claude/skills` for Claude
  and `~/.agents/skills` and `~/.codex/skills` for Codex (the first source wins on a repeated name).
  It handles symlinks, also when the whole skills directory points outside `~/.claude`, and nested directories.
  `in/skills.json` records the list of names and real paths. The same list goes at the end of the step
  command, also on resume. A missing skill required by the command ends in an explicit `result=error`
  with its name, instead of pretending the procedure was performed,
- Claude keeps `--setting-sources=` and `--strict-mcp-config`. `--add-dir` alone does not restore skills:
  loading them from additional directories depends on the `project` settings source.
  Tide generates a local plugin under `in/skills-plugin-<fingerprint>` and passes it through `--plugin-dir`.
  It contains only the name, the description and an instruction to read the original `SKILL.md`; it does not copy
  hooks, `allowed-tools`, `context: fork`, settings files, MCP or user plugins.
  In `init`/the command list the skills have the names `tide-user-skills:to-spec`, `tide-user-skills:tdd` and so on;
  the step command points to this mapping. The symlink targets get `sandbox.filesystem.allowRead`,
  `denyWrite` and the `Read` tool rules/an `Edit` ban; they are not added to the session's writable directories.
  Relative skill resources should be read next to the original `SKILL.md`, and the step rules take precedence,
- Codex discovers skills natively from `~/.agents/skills`; compatibility with the older `~/.codex/skills` is provided by
  an explicit list of paths in the command. A version without native discovery is to read the given `SKILL.md`
  and its resources directly. We do not add skill directories to `writable_roots`, do not enable MCP
  or additional configuration sources. We do not assume an `init` event with a skill list in `codex exec --json`:
  the evidence of the passed list is `in/skills.json` and `in/prompt.txt`.
  Native loading in Claude is checked by an optional test
  `TIDE_SKILLS_CLAUDE_BIN=/path/to/claude python3 -m unittest daemon.tests.test_background_skills_cli.NativeSkillsTest -v`:
  it uses HOME, hook and MCP dummies, does not connect to a model, ends the process after `init`,
  writes the raw `step.log` to `build/test-results/skills-init/` and checks five required skills.
  The default `test_background_skills.py` tests need neither the program nor a model.
  Sources of the mechanisms: [Claude skill sources](https://code.claude.com/docs/en/skills#load-skills-from-a-directory-outside-the-project),
  [a local plugin in the Claude CLI](https://code.claude.com/docs/en/cli-reference),
  [Codex skill directories](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).
  On this base there is no K-38 test box yet: the change covers the current step sandbox;
  any later box must preserve reading the indicated instructions and resources,
- the point token (decision 70) as in the task window: Claude Code through `apiKeyHelper` appended to the step settings file, Codex through `secret_env` (variable → file), which `Background.start()` reads only at start and puts solely into the program's environment; the assembled command (`argv`, `env`, files) does not contain the token,
- `Background.prepare()` creates the step directories and files, `start()` launches the program in a `systemd-run --user --scope --unit=tide-step-<id>` unit (`/usr/bin/env -u XDG_RUNTIME_DIR -u INVOCATION_ID <program>`; `XDG_RUNTIME_DIR` only for `systemd-run`, D-Bus is not needed): the step survives a service restart, the token stays in the process environment (not in the unit properties), the output goes to `<step>/out/step.log`, not to the service pipe. `tasks/stepstop.py`: `stop_step()` stops the unit (`systemctl --user kill` SIGTERM, after `grace` SIGKILL of the whole cgroup — grandchildren after `setsid` too), makes a WIP commit on the step branch and returns the last messages from the session journal (Claude Code `projects/*/<id>.jsonl`, Codex the newest `rollout-*.jsonl` with the step's `cwd`). The service's git works on content written by the agent, so **in a `bwrap` sandbox** (decision 8): `--ro-bind / /`, an empty `tmpfs` on `$HOME` and `/tmp`, write only in the worktree and in `.git/worktrees/<wt>`, `objects`, `refs`, `logs` (the worktree's git directory is determined by the service before the step from the repository, not from the `.git` file in the worktree), the rest of `<repo>/.git` read-only, no network, IPC or other processes, a clean environment; without `bwrap`, `git_snapshot` refuses (`no-sandbox`) and the changes stay in the worktree. Additionally `--git-dir`/`--work-tree`/`GIT_COMMON_DIR`, `core.hooksPath=/dev/null`, no system and user attributes, the worktree's `commondir` file restored (git takes the refs path from it despite `GIT_COMMON_DIR`); it also refuses when HEAD is not the step branch (`wrong-branch`), is detached or is the main branch. Nested repositories in the worktree are skipped with a note, the `assume-unchanged`/`skip-worktree` bits on existing files are cleared, after a killed step its locks (`index.lock`, `HEAD.lock`, `refs/heads/<branch>.lock`) are removed, files from `.gitignore` go to `notes`. A stop without certainty (`systemctl` does not respond) is `not-stopped`, without touching the worktree. `step_id`: `[A-Za-z0-9_-]{1,64}`. A Codex step: all MCP servers from `codex mcp list --json` disabled (`-c mcp_servers.<name>.enabled=false`; a list read error refuses the start), options before `resume <id>`. No D-Bus and no Board — PT-58 will connect that.

The program is an absolute path from the service's PATH (without resolving symlinks: `/snap/bin/codex` points to `/usr/bin/snap`, which recognizes the program by name). Codex from the snap writes the session to `~/snap/codex/<revision>`, not to `~/.codex` — the service reads both directories (Agents, "Codex data directories"), so a task from this window has its state in the widget, notifications and Meta+A.

**The Konsole tab** (`tasks/terminal.py`, `tasks/launch.py`). Konsole 26.08 launches every window as a separate process (`org.kde.konsole-<pid>`), so `konsole --new-tab` opens a new window, and `runCommand`/`sendText` are blocked by default ("API DBus zagrażające bezpieczeństwu" (a D-Bus API endangering security); verified on an isolated bus). Therefore:

1. the service writes a task file (`argv`, directory, title) to `$XDG_RUNTIME_DIR/tide/tasks/<time>-<random>.task` (directory 0700, file 0600),
2. a new tab: the Konsole window where a tab was last opened (the youngest child process of the `konsole` process, with its `KONSOLE_DBUS_SERVICE` and `KONSOLE_DBUS_WINDOW`), `org.kde.konsole.Window.newSession("Tide: zadanie agenta", directory)` — a profile installed by `make install-new-task` (`TideTask.profile`, `Command=` the service's launch.py, appearance after the default profile from `konsolerc`); the tab title "projekt · gałąź" through `setTitle`/`setTabTitleFormat` (like the state marker), then the switch to the window,
3. without the profile, without Konsole on the bus, or when Konsole refuses — a new window: `konsole --workdir <directory> -p tabtitle=<title> -e python3 launch.py --task <file>` (through `systemd-run --user --scope`),
4. `launch.py` in the tab takes over the task file by renaming it to `.claimed` (without `--task`: the oldest task for the tab's directory), removes it and runs `/bin/sh -c '"$0" "$@"; exec "${SHELL:-/bin/sh}"' <agent argv>` — after the agent ends an ordinary shell remains,
5. every 100 ms the service checks the takeover; after 15 s (`TIDE_TASK_CLAIM_TIMEOUT`) it cancels the file by renaming it to `.cancelled` — if the launcher was not faster, the worktree is removed (`git worktree remove`, `--force` on refusal — it is a fresh directory with no agent work), and the branch with `git branch -D` only when it still points to the commit it started from. An atomic rename guarantees that a withdrawn task will not start later.

A successful start remembers the choice (`$XDG_STATE_HOME/tide/new-task.json`: the repository, the cloud switch tool — a local start leaves the previous one, "Nowy worktree", the model), refreshes the worktree list, and the new session has the `newTask` field in `/Agents` for 10 min. The window saves screenshots to `$XDG_DATA_HOME/tide/tasks/<time>-<random>/` (`imagesDir`); `Start` accepts only image files from that directory (at most 10). A window closed without a start removes its directory; after a start it stays for the agent.

The main thread (D-Bus) does not wait for git: `Repos`, `SuggestBranch` and `Start` work in one worker thread and reply with a delay. `--no-tasks` turns the object off.

## Shutdown and recovery after a restart (PT-253)

SIGTERM asks Qt to end the loop; a Python alarm gives the signal handler control every 500 ms.
A return from `app.exec()` and even from `app.main()` does not mean the process has ended: on exit Python
joins `ThreadPoolExecutor` threads, also after `shutdown(wait=False)`. A controlled slow
`git worktree list --porcelain` reproduced exceeding 5 s: the `tide-worktrees` thread waited in
`subprocess.run/communicate/select`, and the main one in `_python_exit/threading.join`, already after returning
from `app.main()`. The limit of each command was 60 s, and the scanner could move on to the next
repository. This reproduces the mechanism of reaching the systemd stop limit; it does not identify
a specific slow repository from an earlier stop on the desktop.

After shutdown begins, the worktree monitor rejects new work, cancels the queue and signals
cancellation to the scanner. The current git has its own process group; cancellation is checked every 50 ms,
then the group gets SIGTERM, and at the latest after 200 ms SIGKILL. This applies only to the git
launched by the scanner, together with its helpers. The thread is joined before the service ends.
The directory scan and the disk usage measurement check the same cancellation marker. The remaining uses of the
`Git` class keep their existing limits; there is no `TimeoutStopSec` change or forced exit of the
interpreter. A kernel-blocking read from an unavailable file system remains a limitation of
cooperative cancellation.

Closing the Board also stops its flow monitor: it accepts no new actions, removes
pending actions from the queue, lets the current operation leave a durable intent/result and commits
the journal changes without waiting for a Qt callback. Flow operations keep their existing
limits. The journal has a service copy written with `fsync`; after start, cards and pending decisions are
a projection of that journal. An ordinary restart does not write a manual move-to-Backlog event.
The processes of steps and of commands outside the sandbox run in separate systemd units; stopping
the service does not stop them. The new service process takes over the same units and result files.

The Stream notification filter receives each `Inhibit` reply only once: `waitForFinished()` during
shutdown can also deliver the `finished` callback. On closing, the two receipts must not separately
release the same cookie. `stop()` keeps a single synchronous `UnInhibit` and rejects late
activity signals and attempts to inhibit notifications again. An ordinary exit from Stream still
releases the inhibition asynchronously, also when the reply arrived only after the exit.

`python3 -m unittest -v daemon.tests.test_daemon_lifecycle` measures monotonically the end of its own
process after SIGTERM (limit 5 s), the restart to a working D-Bus and a current snapshot (limit 10 s),
the preservation of decisions, results and branches, and the absence of repeated steps/merges. The systemd scenarios
use only `tide-pt253-test-<random>.service` units and scopes with a separate random prefix,
a private bus and temporary HOME/XDG/repositories. They check a live step
and a command outside the sandbox at the same time: cgroups, PIDs, the progress of both processes after the restart and a single
takeover of both results. A missing private D-Bus or user manager means the scenario is skipped,
not proof of a correct restart.

A manual check after deployment to the desktop:

1. Start a controlled, longer background step and, on a second card, a controlled command
   outside the sandbox. Record their PIDs from the process logs, the columns and the branch data of both cards.
2. While both processes are working, run the measurement below. It calls exactly
   `systemctl --user restart tide-daemon` and checks the identity of both live processes after the restart:

   ```python
   import subprocess, time
   from pathlib import Path
   pids = [int(input("PID kroku: ")), int(input("PID polecenia poza piaskownicą: "))]
   def identity(pid):
       fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
       assert fields[0] != "Z", f"proces {pid} zakończony"
       return fields[19]  # starttime, so a reused PID is not mistaken for the original
   before = [identity(pid) for pid in pids]
   started = time.monotonic()
   subprocess.run(["systemctl", "--user", "restart", "tide-daemon"], check=True)
   elapsed = time.monotonic() - started
   assert elapsed < 10, elapsed
   assert [identity(pid) for pid in pids] == before
   print(f"restart: {elapsed:.3f} s; oba procesy żyją")
   ```

3. Confirm the journal growth/progress of both processes. Read the current snapshot through
   `qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.Snapshot`; check the columns and the branch data,
   also for a card waiting for a decision and a "Do sprawdzenia" (To review) card. Wait for the natural end of both
   controlled jobs: the result of each is to be taken over once, with no additional step, merge or decision.
   The automated tests do not run this command on the target service.

## KRunner

A KRunner plugin: after typing a project name, a branch or a fragment of a conversation title, it resumes a Claude Code or Codex session in its directory, and for a branch name it opens the worktree.

| Element | Value |
| --- | --- |
| Service name | `org.tide.Daemon.KRunner` (a separate connection of the same service, why below) |
| Object | `/KRunner` |
| Interface | `org.kde.krunner1` (the KRunner 6 "DBus" API) |
| Plugin file | `$XDG_DATA_HOME/krunner/dbusplugins/tide.desktop` (`make install-krunner`) |

| Member | Signature | Description |
| --- | --- | --- |
| `Match(s query)` | `→ a(sssida{sv})` | Matches: id, text, icon, category relevance (70/50/30 — whole field, beginning, further), relevance 0..1, properties `subtext`, `category`, `actions` |
| `Actions()` | `→ a(sss)` | Additional actions: `terminal` ("Otwórz katalog w terminalu" (Open directory in terminal)) and `rider` ("Otwórz w Riderze" (Open in Rider)), only the available ones |
| `Run(s matchId, s actionId)` | `→` | Launch; an empty `actionId` is the default action |

What it shows:

| Kind | Text / caption | Run |
| --- | --- | --- |
| Session (`session:<tool>:<id>`) | `Codex · LogViewer · <title>` / `<branch> · 3 dni temu` (3 days ago) | the session window, if it is still running (like `ActivateSession`); a Codex Desktop thread — opened in the app (`codex://threads/<id>`, tooltip "otwórz w Codex" (open in Codex)); otherwise a new Konsole window in the session directory with `claude --resume <id>` or `codex resume <id>`, and after the agent exits a shell remains. The Codex used to resume is the one that sees the session's data directory: a session from `~/snap/codex/…` — the first `codex` from a `…/snap/bin` directory in PATH, falling back to `/snap/bin/codex`; any other — the first `codex` from outside the snap (PATH, then `~/.local/bin`); with no match — `codex` from PATH |
| Project (`project:<path>`) | repository name / `Otwórz projekt · ~/DevWorkspace/…` (Open project · …) | Konsole in the directory; the `rider` action |
| Worktree (`worktree:<path>`) | branch / `Worktree · <repository> · <path>` | Konsole in the directory; the `rider` action |
| Tide Sesje (Tide Sessions) (`sessions:window`, `coordination:<id>`) | "sesje" (sessions) (from "ses"): `Tide Sesje` / the number of coordinators; "sesje <words>": `Sesje · <project> · <title>` / `TOM-20 · 8/13 · 1 czeka · 3 w toku · 5 min temu` (1 waiting · 3 in progress · 5 min ago) | the Tide Sesje window (with the chosen coordinator); only when the window is installed |

- The query must have at least 3 characters. Every word must hit one of the fields (session: project, branch, directory name, title, tool; project: name; worktree: branch, directory, repository), ignoring case and Polish characters. Sessions are additionally weighted by freshness.
- `ai <words>` shows only sessions (up to 20); an ordinary query — up to 8 sessions, 5 projects and 6 worktrees.
- Sessions whose directory no longer exists (e.g. a removed worktree) are not shown — they cannot be resumed where they were. `Run` accepts only ids from the current index and paths from the current worktree list.
- Icons from the theme: `utilities-terminal` (Claude Code), `openai` (Codex), `folder-git` (project), `vcs-branch` (worktree), `jetbrains-rider` (action).

**The session index** (`krunner/index.py`): sessions from the last **30 days** (the file modification time as a preliminary filter, then the last journal entry). For each session only: id, tool, directory, project, branch (from the journal, falling back to the current one), the title shortened to 100 characters and the time of the last activity. The title:

- Claude Code: `custom-title` (set by the user), otherwise the last `ai-title`, otherwise the user's first message (without command inserts and `isMeta` entries),
- Codex: `thread_name` from `session_index.jsonl`, otherwise `threads.title` from the state database `state_<n>.sqlite` — from every Codex data directory (`~/.codex`, `~/snap/codex/current`) (opened read-only, `mode=ro`; another schema or an error — we skip it), otherwise the user's first message (`user_message`, the app's `UserMessage` or a `user` message without the environment context and `AGENTS.md`). Subagent threads are skipped.

The index is built lazily: the first `Match` after the service starts waits for it to be built (about 2 s for us with 0.6 GB of Claude journals and 1.9 GB of Codex), in a worker thread, without blocking D-Bus. Later it refreshes in the background at most every 10 s and only on queries — it reads only files with a changed size or modification time, from where it stopped (the same readers as the Agents area: `ClaudeLog`, `CodexLog`, `JsonlTail`). On the first read of a large file: the beginning (256 KB, the first message) and the last 1 MB (the latest title, branch). A query result comes back in a few milliseconds.

**Privacy.** The session index lives only in the service's memory: there is no cache file, and of the conversations only the shortened title and the beginning of the first message (100 characters) remain. Semantic search (below) sends this metadata — the title, the beginning of the first message, the project, the branch, the plan's ticket titles — and longer KRunner queries to the server of the `embed` role: by default Ollama on this computer, but after switching to the Mac (or pinning the role to another server) they go to that server on the network; never to the cloud (an Ollama cloud model is rejected). Only vectors stay on disk, without text; turning it off in Tide → Modele (Models) removes them and sends nothing anymore. The tests use only synthetic journals (`daemon/tests/fixtures/history`).

**Semantic search** (PT-19, `krunner/semantic.py`, decision 75). A query of 3 words and 12 characters or more (without `ai `) gets, besides word hits, sessions similar in meaning: "ta sesja o dockerze i portach" (that session about docker and ports) will find "Kontenery i mapowanie portów" (Containers and port mapping), although the words do not overlap.

- **Model**: the `embed` role in `models.json` (by default `bge-m3` without a point — it follows `default`, so switching to the Mac moves it), the call `models.embed` (Ollama `POST /api/embed` with `truncate` and `keep_alive: 2m`, an OpenAI-compatible server `POST /v1/embeddings`) through the same queue as the report and the cards; an Ollama cloud model is rejected as everywhere.
- **What goes to the model**: only metadata from the KRunner index — the title, the beginning of the first message (when different from the title), the project, the branch as words, and for a coordinator session the titles of its plan's tickets (the "Tide Sesje" board, at most 12). Never the conversation content. The text goes through `strip_secrets` from the morning report (keys, tokens, `.env` lines, passwords in addresses, the home directory → `~`) and is trimmed to 700 characters.
- **The index**: `$XDG_DATA_HOME/tide/search/sessions.json` (`$TIDE_SEARCH_INDEX` in tests), a 0600 file in a 0700 directory. Format 2 (K-98 #5, PT-346) is a JSON Lines journal: a header, then change records (an entry, just the activity time, a removal, the index time) appended on change — a change of one session appends one line instead of rewriting the file; a full atomic write only for a new file, a change of model or dimensions, after an unreadable file and when the journal grows beyond 2× the number of entries + 64 lines. The file is a reproducible cache: an old format, a corrupted line or a cut-off write is one full recomputation without an error. Without texts: for each session the text digest, the time of the last activity and a normalized vector (float16, base64 — about 2.7 KB per session at 1024 dimensions). The header has the model (`bge-m3:latest`) and the number of dimensions — another `embed` role model or other dimensions means an index from scratch. Bounds: sessions from the KRunner window (30 days), at most the 2000 freshest.
- **When it computes**: the `tide-search-index` thread — the first pass 60 s after the start (`--search-delay`), then every 5 min and immediately after every model settings write (enabling, disabling, another model). Incrementally: the document text and digest are computed only for sessions with changed metadata (a pass with no changes builds no document and writes no file), to the model only new and changed sessions, in batches of 16, with priority `queue.IDLE` (the lowest — it goes when nothing else waits on the server). On an 8 GB card, loading bge-m3 pushes out qwen3:8b (verified on this computer's Ollama), so a pass with something to compute — and each of its next batches — also waits for 3 min of silence after the last call to another model — otherwise the next card description would load its model anew. A batch that waits in the queue longer than 5 min — the next attempt in a minute.
- **The query**: only from 3 words and 12 characters and not looking like a shell command, an address or a path (`://`, `/…`, `~/…`, `./…`, `-flag`, the shell characters `|`, `<`, `>`, `$`, `=` and a backtick) — KRunner is sometimes also a terminal and an address bar. It goes through `strip_secrets`; when something in it looks like a secret or a home path, it does not go to the model at all. After 0.3 s of silence (KRunner asks on every keypress; a newer query invalidates the older one before anything goes to the server), with a memory of the 64 last query vectors, with priority `queue.HIGH` and a queue wait limit of 2 s. KRunner waits for the vector for at most 1.5 s — after that it gets word matches only, and the call finishes in the background (a cold model loads for a few seconds; the next query hits an already loaded one, about 20 ms). The server does not respond or the model is missing — no attempts for 30 s; a busy server (the report) — no pause. No error reaches KRunner.
- **Ranking**: cosine similarity. The score must be at least 0.42, 0.06 above the average of all sessions (the background of this query) and at most 0.08 below the best; at most 5 (within the session limit: 8, with `ai ` 20). The thresholds come from measuring bge-m3 on sample sessions: unrelated texts score 0.3–0.4, matching ones 0.45–0.6.
- **Merging**: words take precedence — a session found by words does not repeat, and the semantic ones have category 30 (LOW), a relevance below the weakest word-matched session (at most 0.5) and a note in the caption: `feat/login · 3 dni temu · podobne znaczeniowo` (similar in meaning). The launch is like any session.
- **Turning it off**: `semanticSearch: false` in `models.json` (Tide → Modele, the "Wyszukiwanie" (Search) row, `SetSemanticSearch("off")`) — nothing is computed, a batch waiting in the queue or in progress is interrupted, and the index file disappears at once, at the moment the setting is written (also in a service with `--no-krunner`, which does not maintain the index but may have left it earlier). The index state: `Status().search` of the `/Models` object (below).

**Why a separate name and dbus-python.** KRunner expects a reply of `a(sssida{sv})` — an array of structs. PyQt6 cannot build it: `QDBusArgument.beginArray` requires a type registered in C++ (`qDBusRegisterMetaType`), and trying to work around it kills the process in libdbus. So the `/KRunner` object is served by dbus-python (`python3-dbus`, with the GLib loop from `python3-gi`), which has full typing. On Linux Qt runs on the same default GLib loop (`QEventDispatcherGlib`), so one main thread serves both libraries; with `QT_NO_GLIB` a 50 ms timer pushes the GLib loop. dbus-python has its own connection to the bus, and a name belongs to one connection — hence `org.tide.Daemon.KRunner` next to `org.tide.Daemon`. The D-Bus activation file of this name starts the same systemd unit, so KRunner queried before the service starts will start it. Without `python3-dbus` or `python3-gi` the service keeps running, only without the plugin (`--no-krunner` turns it off explicitly).

**Installation.** `make install-daemon` (the code and the activation file `org.tide.Daemon.KRunner.service`) and `make install-krunner` (the `.desktop` file; both are also in `make install`). KRunner reads D-Bus plugins at startup, so the installer and uninstaller ask the running KRunner to quit (`qdbus6 org.kde.krunner /MainApplication quit`) — it returns by itself on the next Alt+Space. Search in the Plasma menu (Kickoff) has its own instance and will see the change after logging in again or `systemctl --user restart plasma-plasmashell`. `make uninstall-krunner` removes the plugin file, so it disappears from KRunner. A quick check: `qdbus6 org.kde.krunner /App querySingleRunner tide plasma` opens KRunner with the results of this plugin only.

## Idle performance (K-105, PT-458)

The readiness criterion "Gotowe (M0)" (Done (M0)): the service at idle uses no more than 30% of one core, `Snapshot` replies
at p95 under 1 s, and approving an 18-row plan takes under 10 s. It is measured with two tools using the same
CPU measure: the reproducer (before installation, the coordinator) and the production script (after installation, the owner).

### The 10-window CPU tool for the whole process

Code: `daemon/tests/perf_cpu_windows.py` (the measure and the assessment), the reproducer `daemon/tests/perf_idle_cpu.py --scale prod
--windows 10`, the production script `scripts/measure_idle_prod.py`.

- **The deciding measure: the CPU of the whole service process.** The "process" is, in the reproducer, the PID of the service launched by
  the reproducer, and in production the unit's `MainPID` (`systemctl --user show -p MainPID`). What counts is the increase of
  `utime + stime` from `/proc/<pid>/stat` — **all threads of the process, without child processes** (`cutime`/`cstime` are not
  read). 10 consecutive windows of 60 s, in % of one core per window; **threshold: every window ≤ 30%** (an average does not
  rescue a window above the threshold). The result gives the PID, the raw `utime` and `stime` increases (ticks) and the % of a core of each window,
  the average, the maximum, `box.enabled`, the loadavg before and after, and the SHA.
- **Clean idle.** The warmup (`--warmup`, in the reproducer and in production 60 s by default after the start and the validity check;
  there is no way to detect it without requests, so it is a parameter) and the 10 CPU windows **send no requests to the service**:
  no sampler, `Snapshot` or other D-Bus calls. The service sampler (60 s: the share of `engine.tick`, `engine.tick` samples/s,
  the heaviest frames) and a series of 60 `Snapshot` calls every 0.5 s (p95 nearest-rank) run in a **separate window after** the CPU windows,
  because the sampler itself uses CPU.
- **Validity.** A PID change (a service restart) or the process disappearing invalidates the attempt; so does a 1-min loadavg ≥ cores/2
  before the measurement or after the CPU windows (the PT-362 rule), an incomplete series of windows and exceeding the time limit. In production
  additionally steps in progress (the `tide-step-*` and `tide-outside-*` units) — with a single read before the warmup, not during it.
- **Diagnostics without a threshold** (does not change the assessment): the share of `engine.tick`, `engine.tick` samples/s, the CPU of the flow worker thread
  (`flowThread` per the PT-410 rule: the only `tide-cardflow*` thread, otherwise the largest `cpuS` excluding the main thread
  and the sampler), `Snapshot` p95 (in the reproducer; in production the script assesses the `< 1 s` threshold), and memory before and after the CPU windows:
  `RssAnon`, `VmRSS`, `VmHWM` from `/proc/<pid>/status` and `MemoryCurrent` — in the reproducer the process cgroup's `memory.current`
  (with the saved cgroup path and a flag whether the cgroup contains other processes), in production the unit's `MemoryCurrent`.
- **The PT-410 single-window assessment** (`perf_idle_scale.py evaluate`, the PT-346 mode unchanged) no longer has thresholds or a verdict:
  the flow thread CPU, the share of `engine.tick` and the `VmRSS` growth are reported with the label "tryb jednego okna — bez oceny
  progu; rozstrzyga tryb 10 okien" (single-window mode — no threshold assessment; the 10-window mode decides). Only validity (loadavg) remains.
- **Comparability.** The reproducer lacks, among others, `coordination_monitor._scan`, `describe.facts_hash`, `boardplans` and
  `scanner` (~15% of production samples), so it is an indicator. The production measurement decides.
- **Production states (K-125, PT-486).** Besides finished and active cards, the scale data (`perf_idle_scale.assign_roles`/`build_card`/`populate`) also have:
  4 **paused** cards with 7 outside requests without a decision (3, 2, 1, 1 — a step
  `denied` + `outside`, the command is a shell script in the worktree, so consent is always the human's), a card **with a held
  K-38 process** (protected tickets, steps finished, units for the `systemctl` dummy active; from 12 cards
  `PROD_HELD_STEPS` = 135 steps as in production — `fix` steps return to 7 protected tickets, so the tick asks the dummy
  about every unit) and a card
  **with an approved plan at rest** (`card-branch` and `plan-commit` results, tickets waiting for a blocker from another
  card). All are unfinished, so on every tick they go through a full card pass and run `_consent_recheck`,
  `_plan_next` and `trialhold.inventory`. `populate` checks every role (`check_roles`: `permission`, `paused`, `held`,
  an approved plan, no steps in progress) and rejects data that do not yield the state. The generator's ticket numbers start
  at `PT-1011`, so that an accidental ticket of the protected trial does not turn a card into a card with a held process.
- **The `systemctl` dummy.** `perf_idle_cpu.isolated_env` puts into the reproducer's `bin` (next to the `incus` dummy, first in the
  service's `PATH`) a short `/bin/sh` script: `is-active` of the held process's unit → `active` (code 0), another unit →
  `inactive` (code 3), other subcommands → code 0 with no output; every call appends a line to `systemctl.log`.
  Without it `stepstop.unit_state` in the isolated environment would return `unknown`. The measurement result has `fakeSystemctl` (calls
  in the CPU window, ticks, calls per tick). The dummy does not work in the engine tests or in production.
- **A profile with the production paths.** The sample breakdown (`parse_profile`) has a `components` field: `outside`
  (`engine.py:_consent_recheck`), `plan` (`engine.py:_plan_next`), `inventory` (`trialhold.py:inventory`) — samples
  and share **relative to the `engine.py:tick` samples**; these are component frames of the tick, so they do not change `rest`. The CPU measure (a window without
  the sampler) stays unchanged. The "before" production-order criteria (`perf_idle_scale.profile_check`): `tick` ≥ 40%
  of work samples, `outside` 30–50%, `plan` and `inventory` ≥ 5% of tick samples, the `systemctl` dummy at least once per tick.
  The "before" report is assembled by `perf_k125_report.py` from the queue results into
  `docs/agents/masterplan/2026-11-15/pomiar-k125-przed.json`. The **work** samples (`work`) do not count the idle frames
  of the thread pool's own and the event loop (`thread.py:_worker`, `app.py:main`); the `tick` share in the criteria is computed
  relative to them (`share` relative to all samples except waiting stays in the result).
- **The production shape (`--shape`).** `perf_prod_shape.py` reads **read-only** the Board's card journals
  (`~/DevWorkspace/tide-board/flow/K-*/journal.jsonl`) and writes to `build/prod-shape.json` only numbers and markers
  of each card (size, number of events and steps, finished/paused/held process and the number of held steps/
  approved plan/outside requests without a decision, computed by the service's `state.fold`) — without content, titles and commands; the file does not
  enter the repository. `perf_k105_queue.py build-data --shape build/prod-shape.json` (and `perf_idle_cpu.py --shape`)
  generates synthetic data with the same number of cards, journal sizes and roles (`shape_plan`: the number of requests
  of paused cards and held steps from production, up to 7 protected K-38 tickets). The `systemctl` dummy treats as
  active the units of all K-38 trial tickets.

### Reproducer: the coordinator's queue script

`daemon/tests/perf_k105_queue.py` — each subcommand is **one `outside` command**; synthetic data, nothing
from the owner's data.

- `/usr/bin/python3 daemon/tests/perf_k105_queue.py build-data` — builds data of 46 cards / ~60 MB (the PT-410 generator) **once**
  into `build/k105-data/` in the worktree (a directory ignored by git) with a manifest `manifest.json` (parameters, the digest of
  the generator files, the repository SHA, the size on disk, the build time). A repeated call with a matching manifest does
  nothing. The build time is recorded in the manifest (`buildSeconds`); the build is a separate command so that it does not count against the measurement limit.
- `/usr/bin/python3 daemon/tests/perf_k105_queue.py measure <before|after> <false|true> [--base-sha SHA]` —
  **one configuration (side × `box.enabled`) and one series of 10 windows per command**. It copies the data from `build/` into a fresh
  temporary directory (the paths in the data and the journal copy key are rewritten to the new directory; `box.enabled`
  is set by the `cardflow.json` file). The `before` side runs the service code from the pinned card base SHA (`--base-sha` or
  `K105_BASE_SHA`) from its own `git worktree add --detach` in a temporary directory and removes it after the measurement
  (`git worktree remove` of that directory only, no `prune`, no change of the card or ticket branch); `after` — the code of the current
  worktree.
- **The result** of a configuration: `build/k105-measure/<side>-box-<false|true>.json` with a list of attempts; written incrementally after each window
  (an attempt `running` with the windows so far).
- **Limit and resumption.** An attempt has an internal time limit (`--limit-min`, 13 min by default: a 60 s warmup + 10 windows
  + a 60 s diagnostics window + the start ≈ 12.5 min). When exceeded in the warmup or in the windows the attempt is invalid; a limit
  before the diagnostics window skips only the diagnostics (the CPU windows stay valid). Re-running **the same command**
  skips a configuration with a valid result (also one assessed "niespełniony" (unmet) — that is a valid result) or with all 3 attempts used up
  and keeps counting attempts; an attempt saved as `running` (the command killed, e.g. with code 143) counts as invalid.
  One run at a time (the file lock `build/k105-measure/.lock`), at most 3 attempts per configuration, the first valid one decides.
- `measure` exit codes: 0 — a valid result or the configuration already decided; 1 — 3 attempts without a valid one; 2 — this attempt
  invalid (report the same command again); 3 — another run is in progress; 4 — no data or a bad base SHA.

The order for the coordinator: `build-data`, then four `measure` commands (`before false`, `before true`, `after false`,
`after true`), each separately.

### Service changes measured by K-105

The full descriptions are in the section "Card flow: "Od razu" (Immediate) (PT-58)" (the strict-read cache, the consistency check and the process
history, the replay loop, resting cards); here only a short list with pointers:

- **PT-411 — a history cache between ticks.** The strict read of the card history (`Journal.read_complete`) is remembered
  between ticks and invalidated by the journal version; a periodic full read verifies the cache, and the cache growth is
  bounded (the paragraph "Strict-read cache (K-105 #2, PT-411)").
- **PT-415 — a process history and card process state cache** on top of the PT-411 cache (the paragraph "Process history
  and card process state cache (K-105 PT-415)"): reads with no effect do not run a new `fold` every tick.
- **PT-416 — a light consistency check with the copy** (`Journal.check`, the journal in the Board ↔ the service copy; the paragraph "Consistency check
  cache (K-105, PT-416)"): on every tick without a full read.
- **PT-459 (work "#2") — the replay loop on the cache** (`FlowEngine._replay_starts`, the paragraph "Replay loop at the
  start of a tick (K-105 PT-459)"): it uses the PT-411/PT-415 cache instead of its own full reads.
- **PT-412 — resting cards**: a registry of rest factors, exclusion by queue operations and the period of the full
  check; the description and the list of factors are in the paragraph "Resting cards (K-105 PT-412)".
- **PT-413 — a risk assessment cache in the presentation** (`cardflow/riskview.py`): publishing a snapshot in the main thread does not
  resolve paths (`realpath`) on every publication; the risk assessment of a "run outside the sandbox" command comes from a keyed
  cache (card, journal version, command, roots), limited to 256 cards and working only within the
  presentation read scope (`Engine.derived`). Automatic consent, the pre-start check, `run-always` and the merge-check decision
  compute the risk fresh and do not see the cache.

### Before/after results in the reproducer (PT-414, 13.11.2026)

The reproducer result is an **indicator**: "Gotowe (M0)" is decided by the production measurement (below). The "before" side is the card base
code `efeb3e87929318196386a9c62b22c099542f0ac3`, the "after" side — the ticket branch `05868ada43eba1e6d2c43e5f2bfb519763f74882`
(PT-411, PT-412, PT-413, PT-415, PT-416, PT-459 merged into the card branch). Data: 46 cards, 59.5 MB of journals, build 52.5 s.
Each configuration is a separate `perf_k105_queue.py measure` command; the first valid attempt decides. The threshold: **every window
≤ 30%** of a core (16 cores, loadavg limit 8.0).

| Configuration | Attempt | Validity | CPU windows (% of a core) | Average / max. | Assessment |
|---|---|---|---|---|---|
| before, `box.enabled=false` | 1–3 | **invalid** (loadavg 12.2–12.8 ≥ 8.0, also before the start) | 74–94 | 85.5 / 94.5; 85.4 / 91.3; 74.4 / 75.6 | no valid attempt (3 of 3) |
| before, `box.enabled=true` | 1 | valid | 83.62; 83.05; 83.75; 82.75; 82.78; 83.32; 82.97; 82.97; 82.92; 82.80 | 83.09 / 83.75 | **unmet** (10 of 10 windows) |
| after, `box.enabled=false` | 1 | valid | 45.08; 45.30; 45.17; 45.73; 45.42; 45.22; 44.98; 45.48; 45.07; 48.02 | 45.55 / 48.02 | **unmet** (10 of 10 windows) |
| after, `box.enabled=true` | 1 | valid | 45.85; 45.83; 45.98; 45.30; 45.00; 45.45; 45.40; 45.20; 44.90; 44.35 | 45.33 / 45.98 | **unmet** (10 of 10 windows) |

Diagnostics without a threshold (a separate window after the CPU windows):
| Configuration | `engine.tick` (share / samples per s) | Flow thread CPU | `Snapshot` p95 | `RssAnon` before → after | `MemoryCurrent` before → after |
|---|---|---|---|---|---|
| before, `true` | 0.198 / 55.1 | 82.3% | 280 ms | 544.2 → 546.1 MB | 744.0 → 728.6 MB |
| after, `false` | 0.096 / 33.0 | 45.0% | 239 ms | 539.3 → 584.4 MB | 633.3 → 678.6 MB |
| after, `true` | 0.102 / 32.8 | 44.8% | 242 ms | 550.2 → 551.8 MB | 644.3 → 645.7 MB |

Conclusions from the reproducer (not an assessment of production):

- **An improvement, but not down to the threshold.** The ticket branch lowers the CPU of the whole process from about 83% to about 45% of a core with
  `box.enabled=true` (by about 38 points) — still 15 points above the threshold in every window. For `box.enabled=false` there is no
  valid "before", so there is no comparison; the "after" (45.6%) is equally above the threshold.
- **Almost all the CPU is taken by the flow thread** (`tide-cardflow`: about 45 of 45.3% in the "after"). The heaviest path in the "after"
  sampler is `engine.tick › boxstep.recover_network › trialhold.held › trialpolicy.blocked_event` (about 1400 of about 1970
  `engine.tick` samples, i.e. about 71%) with a regular expression (`re.search`/`_compile`) executed on every tick,
  also with `box.enabled=false`; behind it comes reading the cards `store.load › _read_doc › realpath` (about 370 samples). This points
  to a place for further optimization; ticket PT-414 does not change the service code.
- Memory (diagnostics without a threshold) is constant within the windows (± about 2 MB of `RssAnon`), except for "after, `false`"
  (+ about 45 MB of `RssAnon`, `VmHWM` 674 MB) — one attempt is not enough to conclude a trend.
- **A missed threshold is a valid result, not a measurement error**: it does not allow close-out with acceptance and is an open M0 blocker
  until the production measurement shows ≤ 30% in every window in both `box.enabled` settings.

### K-113 changes and K-113 before/after results (PT-468, 13.11.2026)

K-113 removes from the warm tick two costs pointed out by PT-414 (the network trial filter and the Board load). A short list,
full descriptions in the paragraphs of "Card flow: "Od razu" (Immediate) (PT-58)" and in the section on reads in the tick:

- **PT-466 (K-113 #2) — a network handshake trial filter cache** (`trialhold.network_state`, `Journal.derive_events(…,
  slot="network")`): `boxstep.recover_network` does not recompute `trialpolicy.blocked_event` every tick; the paragraph "Network handshake
  trial filter cache (K-113 PT-466)".
- **PT-467 (K-113 #3) — a Board load cache in the tick context** (`BoardStore.load_for_tick`, an input fingerprint): the same
  `Board` with unchanged inputs, with no transaction and no parsing; the paragraph "Board load cache
  in the tick context (K-113 #3, PT-467)".
- **PT-469 (K-113 #5) — tick reads on the load cache**: reads with no effect read from the tick context, the gates
  and effect inputs stay fresh; the paragraph "Board reads in the tick: the tick context and fresh gates (K-113 #5, PT-469)".

The "after" measurement (PT-468) is the same procedure as in PT-414: `build-data`, then `measure after false` and `measure after true`,
each as a separate command in a quiet window. The "after" side: SHA `e399342c7381684afb2dba10927c6f2bbd1687eb` (the card branch with PT-466,
PT-467 and PT-469). Data: 46 cards, 63.2 MB, build 52.9 s. The "before" comes from `docs/agents/masterplan/2026-11-13/pomiar-k113-przed.json`
(PT-465, the "before" side `b6e65ce030f9a930d72f856bddf111b403a617cc`, the first valid attempt): it was not measured again.
The threshold: **every window ≤ 30%** of a core (16 cores, loadavg limit 8.0). The reproducer result is an indicator.

| Configuration | Attempt | Validity | CPU windows (% of a core) | Average / max. | Assessment |
|---|---|---|---|---|---|
| before K-113, `box.enabled=false` | 1 | valid | 45.12; 44.87; 53.48; 44.02; 44.68; 44.52; 53.30; 45.33; 46.13; 46.65 | 46.81 / 53.48 | **unmet** (10 of 10 windows) |
| before K-113, `box.enabled=true` | 2 (1 invalid: loadavg after 16.55) | valid | 48.60; 48.55; 46.67; 48.47; 49.45; 49.92; 46.17; 48.30; 49.40; 46.55 | 48.21 / 49.92 | **unmet** (10 of 10 windows) |
| after K-113, `box.enabled=false` | 1 | valid | 10.48; 10.68; 10.23; 10.47; 10.48; 10.03; 10.83; 10.05; 10.12; 12.70 | 10.61 / 12.70 | **met** (0 of 10 windows above 30%) |
| after K-113, `box.enabled=true` | 2 (1 invalid: loadavg after 21.57) | valid | 10.73; 10.45; 10.13; 10.72; 10.70; 10.23; 10.15; 10.22; 10.43; 13.02 | 10.68 / 13.02 | **met** (0 of 10 windows above 30%) |

The drop in CPU of the whole process: `false` 46.81 → 10.61% (−36.2 pts, about −77%), `true` 48.21 → 10.68% (−37.5 pts, about −78%).
Relative to the K-105 "after" from the PT-414 table (45.55% and 45.33%) the difference is about −35 pts in both settings.

Diagnostics without a threshold (a separate window after the CPU windows):

| Configuration | `engine.tick` (share / samples per s) | Flow thread CPU | `Snapshot` p95 | `RssAnon` before → after | `VmHWM` |
|---|---|---|---|---|---|
| before K-113, `false` | 0.090 / 31.0 | 46.1% | 190 ms | 559.7 → 615.2 MB | 694.9 MB |
| before K-113, `true` | 0.105 / 34.5 | 47.4% | 273 ms | 571.9 → 566.1 MB | 602.4 MB |
| after K-113, `false` | 0.024 / 9.3 | 10.1% | 10.6 ms | 538.4 → 578.7 MB | 654.4 MB |
| after K-113, `true` | 0.026 / 9.3 | 10.2% | 11.6 ms | 553.0 → 624.0 MB | 679.4 MB |

- **`blocked_event` disappeared from the warm tick.** Before K-113 `boxstep.recover_network › trialhold.held ›
  trialpolicy.blocked_event` took 1330 (`false`) and 1484 (`true`) of 1860 and 2071 `engine.tick` samples (about 72%);
  after K-113 `recover_network` has 14 and 13 samples of 560 and 558 (about 2.5%), and `trialpolicy.blocked_event` and `re.search` do not
  appear among the heaviest frames of the sampler report (the self-time and total-time lists, the most frequent stacks).
- **`store.load` did not disappear.** The number of samples is the same as before K-113 (373 and 362 versus 360 and 407), but after the drop of
  the whole tick it is now its main path: about 67% (`false`) and 65% (`true`) of `engine.tick` samples versus about 19–20%.
  This agrees with the PT-469 description: the gates `_plan_next`, `_planning_branch` and `_plan_on_board` read the Board fresh.
  Further optimization of this path is not needed for the threshold in the reproducer; it is a candidate for a separate ticket
  if the production measurement shows a cost.
- **The process sampler** (`processes.probe`, with `true` about 5% of samples before K-113) does not appear among the heaviest "after" frames.
- **Memory** (no threshold): in the diagnostics window `RssAnon` grows by 40 MB (`false`) and 71 MB (`true`); one attempt is not
  enough to conclude a trend, and memory is not a threshold.
- **The reproducer does not replace production.** A met threshold in the reproducer does not mean "Gotowe (M0)".

### Production procedure after the K-113 install (owner)

After `make install` of the K-113 card branch, the owner repeats the production measurement the same way as after K-105 (steps 1–5 below):
`/usr/bin/python3 scripts/measure_idle_prod.py --out ~/pomiar-k113-box-<false|true>.json` **for both values of
`box.enabled`**, switched by the owner (the script does not change them). **Closing out card K-113 ends with a question
to the owner about the production result.** A result over the threshold (window over 30% in either setting, `Snapshot` p95
≥ 1 s, or plan approval ≥ 10 s) is an **open M0 blocker**, even when the reproducer showed ≤ 30%. The reproducer's
assessment (above) does not replace this result.

### Production procedure after the install (owner)

The script `scripts/measure_idle_prod.py` **only reads**: it installs nothing, changes no configuration (including
`box.enabled`), and writes nothing in `~/.local/share`, `~/.config` or on the Board — except the result (`--out`, which refuses paths
in those directories), the sampler request file in the runtime directory, and the sampler report written by the service.

1. After `make install` and a service restart (not while agents are working): `/usr/bin/python3 scripts/measure_idle_prod.py
   --out ~/pomiar-k105-box-<false|true>.json`. The script checks loadavg and **requires continuous idle with no running steps for
   10 min** (`--wait-idle-min`, the M0 criterion) before the warm-up: it polls only step reads (no requests to the service), and every
   step resets the counter. It looks for steps on the host (`systemctl --user`) and — with `box.enabled=true` — only in running
   **Tide boxes** (`incus exec … systemctl --user`), that is, instances with a non-empty recipe fingerprint `user.tide.recipe`
   (the same as in the service). Foreign running instances (without a fingerprint) are skipped without `incus exec` and listed in the result
   (`idle.ignoredInstances`). A Tide box without a user bus (`Failed to connect to bus`) has no steps: the script
   records a note in `idle.boxNotes` (once per box), and the measurement stays valid. Any other error (`incus list`, `incus exec`,
   timeout, read error) invalidates the measurement; it does not mean "no steps".
   The idle time given by the owner (`--owner-idle-min`, recorded in the result as unverified) counts toward the
   required 10 min (it shortens the wait only by that much, it does not replace it). After `--max-wait-min` (30 by default) without reaching
   idle, the result is invalid. Then the warm-up, 10 CPU windows of `MainPID` on clean idle, then separately a 60 s sampler (through the service's request file) and a series of
   `Snapshot` calls. The JSON result: windows, average, maximum, memory before and after, the current `box.enabled` read from the configuration,
   and the assessment (CPU threshold of each window ≤ 30%, `Snapshot` p95 < 1 s). Exit code 0 — within thresholds, 1 — over the threshold, 2 — invalid.
2. **Both `box.enabled` settings.** The owner switches them personally (through the service's usual settings, restarts the service
   and repeats the measurement from step 1): once `false`, once `true`. The script never switches them.
3. **Approving an 18-row plan (M0 criterion, < 10 s).** A separate, explicitly named mode:
   `/usr/bin/python3 scripts/measure_idle_prod.py --mode approve-plan --card K-<numer> --out ~/pomiar-k105-zatwierdzenie.json`.
   This is the **only mode that calls a service action** (`ApproveCardPlan`); only the owner runs it, for a card with a plan of
   ≥ 18 rows that the owner is approving anyway. It measures the time from the call to the `plan.approved` event in the card journal
   (`<Board>/flow/<card>/journal.jsonl`, read only) and records the number of plan rows. It refuses for a plan of
   < 18 rows unless the owner passes `--allow-small-plan` — then the result carries an "outside the criterion" mark.
4. **Where to store the result.** The JSON files stay with the owner, outside the repository and outside the Board; the card gets a summary
   (windows, maximum, `Snapshot` p95, approval time, `box.enabled`, SHA) entered by the coordinator in the close-out note.
5. **Card close-out ends with a question to the owner about the production measurement result.** A result above the threshold —
   CPU of any window in either `box.enabled` setting, `Snapshot` p95 ≥ 1 s, or plan approval ≥ 10 s —
   is an **open M0 blocker**. The same measurement is repeated after the next install (regression).

### K-125 changes and before/after K-125 results (PT-489, 15.11.2026)

K-125 removes from idle a cost that on production took about 62.7% of the process CPU with pending "run outside the
sandbox" requests in paused cards. The list of changes (full descriptions in the section "Card flow: "Od razu" (Immediate) (PT-58)"):

- **#1 (PT-486) — reproducer with production states.** 7 outside requests without a decision in 4 paused cards (3, 2, 1, 1),
  1 active outside request, a card with a held K-38 process, a card with an approved plan at idle, a `systemctl` stub
  in the reproducer's `PATH` (returns `active`, counts calls) and a profile with the categories `outside`, `plan`, `inventory`.
- **#2 (PT-487) — pause before evaluating requests.** The tick checks the card pause on the tick state before evaluating an outside request
  and planning: no Board read, no write. The request stays decidable in the panel, and evaluation starts in the first
  tick after resume.
- **#3 (PT-488) — unit state cache of 30 s and plan commit from the journal.** The K-38 inventory remembers a unit's state
  for 30 s, only `active`/`inactive` (`unknown` is never cached). A plan commit at idle takes the result
  from the journal and does not read the Board when the result is recorded.
- **#5 (negative-result cache for an active request) is conditional.** It starts only if the reproducer under
  `threshold: fail` shows an `outside` share ≥ 10% of tick samples in either `box.enabled` mode. A result below that leaves
  #5 and #6 rejected, and a failed threshold without a significant `outside` share ends with a question about the next step with no code changes.

The reports are produced by `perf_k125_report.py` ("before") and `perf_k125_after.py` ("after", with the verdicts `threshold`, `outsideShare`
and `recommendation`); each window is judged separately (the average does not matter). Files: `docs/agents/masterplan/2026-11-15/pomiar-k125-przed.json`
(base SHA `ec94a8188ccdf0423a7f670a83f16b6032518510`) and `pomiar-k125-po.json` (SHA `59f97ca0211ce0cd927df838b036432996d91e64`:
service code with #1–#3 unchanged in PT-489, only the "after" report added). The same data (46 cards; journals 58.25 MB "before" and 58.3 MB "after" per `scale.disk.totalMb`, the same roles), the same
`systemctl` stub, `--scale prod --windows 10`, a separate `outside` command with `"exclusive": true` per mode. The reproducer result
is an indicator: "Gotowe (M0)" (Done (M0)) is decided by the production measurement (below).

| Configuration | Attempt | Validity | CPU windows (% of a core) | Average / max | Assessment |
|---|---|---|---|---|---|
| before K-125, `box.enabled=false` | 1 | valid | 16.4; 16.23; 15.98; 16.28; 16.27; 24.63; 18.9; 17.15; 16.3; 18.6 | 17.67 / 24.63 | met (0 of 10 windows over 30%) |
| before K-125, `box.enabled=true` | 1 | valid | 16.98; 16.7; 16.87; 16.92; 16.78; 17.08; 16.58; 16.8; 16.88; 18.83 | 17.04 / 18.83 | met (0 of 10 windows over 30%) |
| after K-125, `box.enabled=false` | 2 (1 invalid: loadavg after 27.28) | valid | 6.7; 6.3; 6.57; 6.47; 6.38; 6.28; 6.3; 6.05; 6.32; 9.28 | 6.67 / 9.28 | **met** (0 of 10 windows over 30%) |
| after K-125, `box.enabled=true` | 1 | valid | 7.08; 6.7; 6.65; 6.68; 6.32; 5.72; 5.97; 6.3; 5.73; 8.47 | 6.56 / 8.47 | **met** (0 of 10 windows over 30%) |

Verdict: **`threshold: pass`** (every window in both modes ≤ 30%), `outsideShare` 0.4071 (`false`) and 0.415 (`true`),
recommendation "reject #5 and #6". The rule "#5 is needed" applies only to `fail`, so a high `outside` share of the tick does not trigger it
by itself: the tick taken as a whole shrank from 886 to 312 samples (`false`) and from 895 to 294 (`true`).
A note on the "before" measure: the "before" reproducer gave about 17%, not the over 30% seen on production (62.7%), so the improvement is visible, but
the reproducer does not fully reproduce the production cost (among other things the number and size of journals, the load on the boxes).

Diagnostics without a threshold:

| Metric | `false` before → after | `true` before → after |
|---|---|---|
| tick share of work samples | 0.9662 → 0.9043 | 0.9501 → 0.8776 |
| `engine.tick` samples | 886 → 312 | 895 → 294 |
| `outside` (`_consent_recheck`), samples / tick share | 390 / 0.4402 → 127 / 0.4071 | 376 / 0.4201 → 122 / 0.415 |
| `plan` (`_plan_next`), samples / tick share | 326 / 0.3679 → 0 / 0 | 334 / 0.3732 → 0 / 0 |
| `inventory` (`trialhold.inventory`), samples / tick share | 38 / 0.0429 → 0 / 0 | 37 / 0.0413 → 0 / 0 |
| `_resume_step` / `store._inside` (samples, "after") | 0 / 50 | 0 / 62 |
| `systemctl` stub calls in the CPU window (per tick) | 16065 (133.9) → 2430 (20.25) | 16200 (135.0) → 2565 (21.4) |
| `Snapshot` p95 | 10.4 → 10.4 ms | 18.8 → 10.0 ms |
| `VmRSS` start → end of windows | 566.2 → 594.4 → "after" 550.1 → 572.9 MB | 586.5 → 618.0 → "after" 580.4 → 614.4 MB |

The frames `_resume_step` and `store._inside` are not among the 15 heaviest "before" frames, so "before" has no samples for them (fewer
than the smallest of those 15); "after" records them from the sampler report (`resumeStoreFrames`).

**Decision on #5 and #6 (PT-490, owner, 15.11.2026): rejected.** The start condition for #5 on `pomiar-k125-po.json`:
`threshold: pass` in both modes (17.67 → 6.67% and 17.04 → 6.56%), and #5 would give about 2–3 pp of CPU in the reproducer (the tick is
about 1.3% of process samples, of which about 41% is `outside`). PT-490 and PT-491 end with no code changes. A reservation (the owner's
decision from PT-486): the reproducer is not an acceptance criterion. Card acceptance is decided by the production measurement
(`scripts/measure_idle_prod.py`, both `box.enabled` modes) after the merge and install, against the production "before"
of 62.7%. On production `_consent_recheck` was about 3× more expensive than in the reproducer, so if the production measurement exceeds
the threshold, the initial evaluation of outside requests with a negative-result cache (scope of #5, ticket sheet `docs/agents/tickets/k-125-flow/pt-490.md`)
returns as a **new card**.

### Production procedure after the K-125 install (owner)

After `make install` of the K-125 card branch (installed by the owner, not an agent), the production measurement is repeated as in the step
"Procedura produkcji po instalacji (właściciel)" above: `/usr/bin/python3 scripts/measure_idle_prod.py
--out ~/pomiar-k125-box-<false|true>.json` **for both values of `box.enabled`** (10 CPU windows of 60 s each, continuous idle
with no running steps, warm-up, sampler and a series of `Snapshot` calls). The measurement must take place **with 7 outside requests pending in
paused cards** (like K-104, K-107, K-44, K-100 in the production state), because that is the state K-125 changes; the requests must not be
declined or decided for the sake of the measurement. A result over the threshold (window over 30% in either setting,
`Snapshot` p95 ≥ 1 s, or plan approval ≥ 10 s) is an **open M0 blocker**, even when the reproducer showed
≤ 30%. With such a result, an `outside` share ≥ 10% of tick samples is a reason for #5; a cost elsewhere
(`_resume_step`, `realpath` in `store._inside`) requires a separate decision with the samples broken down.

## Idle

The wallpaper "Tide: kot" (Tide: cat) (`org.tide.cat`) closes the cat's eyes when nobody is using the computer. It learns this from the service:

| Element | Value |
| --- | --- |
| Object | `/Idle` |
| Interface | `org.tide.Daemon.Idle` |

| Member | Signature | Description |
| --- | --- | --- |
| `Idle` | property `b` | no keyboard, mouse or touch since the threshold; `false` when the source is not working |
| `Available` | property `b` | whether the compositor reports idle |
| `ThresholdMs` | property `i` | threshold in ms (`wallpaper.dozeAfterS` from the palette, 15 s by default) |
| `IsIdle()` | `→ b` | the same as `Idle` in a single call (QML) |
| `State()` | `→ s` | JSON `{available, idle, thresholdMs, idleForMs, source}` — diagnostics |
| `IdleChanged` | signal `(b idle)` | idle state changed |

**Source.** The service connects to the Wayland compositor (KWin) and requests a notification from the `ext_idle_notifier_v1` protocol with the threshold from the palette: in version 2 `get_input_idle_notification` (only user input counts, idle inhibitors — e.g. a playing video — do not interfere), with version 1 and the older `org_kde_kwin_idle` as fallbacks. The compositor does the counting, and the service receives only two events: "idle" after the threshold and "resumed" on the first input — no polling and no CPU use at rest. The protocol client is pure Python (`tide_daemon/wayland_idle.py`); the state and the connection to the Qt loop are in `tide_daemon/idle.py`. The compositor socket works as in libwayland: `$WAYLAND_DISPLAY` in `$XDG_RUNTIME_DIR` (Plasma exports both variables to the user's systemd services environment). When there is no compositor or the connection breaks (KWin restart), `Idle` = `false`, `Available` = `false`, and the service retries every 2 s, then less and less often, up to 60 s; messages go to the journal only on change.

The service reads the threshold from the copy of the palette next to it (`palette.json`), so after changing `wallpaper.dozeAfterS` you need `make install-daemon`. Options: `--idle-after SECONDS` (overrides the threshold), `--no-idle` (no `/Idle` object). Rationale and rejected sources: decision 39 in `docs/decisions.md`.

```bash
qdbus6 org.tide.Daemon /Idle org.tide.Daemon.Idle.State
dbus-monitor "type='signal',interface='org.tide.Daemon.Idle'"   # IdleChanged on doze and wake-up
```

## Morning report

Once a day (at 07:00) the service compiles a report of what happened since the previous one: the state of work for the day — "Dziś w skrócie" (Today in brief) (3–5 plain sentences about the whole day), an HTML page for each project repository with activity or work in progress — with its own "Na daily" (For the daily) at the top (what was done, what is ongoing, what is waiting for you, what's next, chores separately) — and an index, which is the overview of projects. The report for the current day is live: it refreshes itself after changes in the work, "waiting" is recalculated live, and after every finished ticket a "Skończone dziś" (Finished today) note arrives with a notification ([Live report](#live-report)). It is a report for people: no agent-work jargon, file paths or ticket numbers in the text. The data is local — git, agent journals, the coordination board file, plans in the repositories, AI limits from `127.0.0.1` and (read only) ticket descriptions from YouTrack on the local network; the only thing that can leave the computer is concise facts for the "Na daily" description, through a local Claude Code model (by default, `"summary": "claude"`). With `"summary": "local"` the description is written by a local model from `models.json` (section "Local models") and nothing leaves the computer — unless the `summary` role points to a server on the network (e.g. a future Mac Studio), in which case the facts go only to it; Claude is then never called, even when the local server is not running. `"summary": "rules"` — no model at all. Rationale: decisions 42, 52, 53 and 64 in `docs/decisions.md`.

| Element | Value |
| --- | --- |
| Object | `/Reports` |
| Interface | `org.tide.Daemon.Reports` |
| Files | `$XDG_DATA_HOME/tide/reports/YYYY-MM-DD/index.html`, `<project>.html` and `report.json` — data for the "Raporty" (Reports) view of the Tide window (directory 0700, files 0600) |
| State | `$XDG_STATE_HOME/tide/report-state.json` (day, period, notification day, day of the opened report, and the numbers of projects/waiting items of the last report) |
| Settings | `$XDG_CONFIG_HOME/tide/report.json` (optional): `{"at": "07:00", "dayStart": "04:00", "keepDays": 30, "enabled": true, "summary": "claude", "summaryModel": "sonnet"}`; `summary`: `claude`, `local` or `rules` (also for the Board card descriptions); `summaryModel` matters only with `claude` — in `local` mode the model is taken from the `summary` and `describe` roles in `models.json`. A missing `summary` key means `claude`; an unknown value (e.g. the typo `"Local"`) means `rules` with a warning in the journal, so that a mistake does not send facts to Anthropic. The file is read at service start; `summary` can also be switched from the Tide window (`/Models SetReportSummary`, section "Local models") — written to the file and applied at once in the running service |
| Description cache | `<report>/summary.json` (0600, version 2): the model identity (`identity`), name and kind (`claude`/`local`), per project the key of its facts and the model's answer (or `null` — rules), and the "Dziś w skrócie" sentences with the key of all projects (`claude` only) |
| Notes | `$XDG_STATE_HOME/tide/report-notes.json` (0600): described tickets (`seen`, 21 days) and "Skończone dziś" notes per day (`days`, as many days as there are reports) |

| Member | Signature | Description |
| --- | --- | --- |
| `Generate()` | `→ s` | Compiles the report of the current day now (the same period start as the morning one, a new end) and returns the `index.html` path; an empty string on error. No notification. Replies when done (background work) |
| `Latest()` | `→ s` | `index.html` of the latest report or an empty string |
| `Status()` | `→ s` | JSON of the latest report for widgets: `{path, day, generatedAt, projects, waiting, unread}` — `generatedAt` in epoch seconds, `projects` are the projects in the report (with activity or work in progress), `waiting` — "waiting for you", `unread` — the report of this day has not been opened yet. The report of the current day has `waiting` recalculated live (without items the agent is no longer waiting on) and additionally `waitingNow` (sessions waiting right now) and `finished` ("Skończone dziś" notes). Without a report `path` and `day` are empty and `unread` is false; the numbers are `null` when the report did not pass through the service (e.g. `--report`) |
| `Open()` | `→ b` | Opens the latest report in the browser (`xdg-open` through `systemd-run --user --scope`) and marks it as read; False when there is no report or it cannot be opened |
| `Days()` | `→ s` | JSON of the days with a report for the Tide window: `{root, today, keepDays, latest, unread, days: [{day, generatedAt, projects, waiting, data}]}` from the newest; `today` is the report day according to `dayStart`, `waiting` — the "Czeka na mnie" (Waiting for me) items from all projects (the current day — only current ones), `data` is false for a report from before `report.json` (then `projects` is the number of project pages and `waiting` is `null`) |
| `Day(s day)` | `→ s` | The day's `report.json` with the fields `ok`, `latest`, `unread`, `legacy` and `index`, with the "Skończone dziś" notes (`finished` on a project) and — for the current day — the live state: `stale` for outdated "Czeka na mnie" items, `now` (what is waiting now: `id`, `project`, `label` — the name from the work board, `title`, `text`, `reason`, `since`), `nowText`, recalculated counters and clipboard texts; a report from before `report.json` — `legacy: true` with the project page names; a bad or nonexistent day — `{ok: false, error: "missing"}` |
| `OpenDay(s day, s project)` | `→ b` | The day's page in the browser: `index.html` (empty `project`) or `<slug>.html` (only `[a-z0-9._-]`, an existing file, not a symlink). The page of the latest report marks it as read; for an older one the dot does not change |
| `MarkRead(s day)` | `→ b` | The latest report was viewed in the Tide window — read as after `Open()` (with `Changed`); False for an older day or when it was already read |
| `Ready` | signal `(s path)` | A new report was created |
| `Changed` | signal | A new (also live) report, a new "Skończone dziś" note, a change in what is waiting, or a change in read state — widgets then fetch `Status()`, the Tide window `Days()` and `Day()` |

```bash
qdbus6 org.tide.Daemon /Reports org.tide.Daemon.Reports.Generate   # report now
xdg-open "$(qdbus6 org.tide.Daemon /Reports org.tide.Daemon.Reports.Latest)"
```

**When.** The report day starts at `dayStart` (04:00) — work after midnight still belongs to the previous day. A day's report is created once: at the first login on that day (service start; the check runs after 2 min so as not to slow down the login) or at `at` (07:00) if the user is already logged in. We check the wall clock every minute, so waking up from sleep also catches an overdue report. A login at 05:30 gives a report at once, at 01:00 — nothing (the day already has a report), and at 07:00 the next one comes. `--report-at HH:MM` overrides `at`; `--no-report` or `"enabled": false` turns off the schedule (`Generate()` keeps working).

**Period.** From the anchor of the previous report to now, at most 7 days; the first report — the last day. A report's anchor is its schedule time (day + `at`, e.g. Tue 07:00), and when it was created earlier (login at 05:30) — the end of its period; never later than the schedule time. That is why `Generate()` in the evening (e.g. at 23:54, still on the same day) does not shift the next day's boundary: Wednesday's report covers Tue 07:00 → Wed 07:00, not the empty night 23:54 → 07:00. A weekend and several days of break are covered in full — from the anchor of the last report. `Generate()` on the same day keeps the period start, so it loses nothing from the morning report. The period start of day D never falls later than the schedule time of day D-1 — this also repairs state saved before this rule (`report-state.json` with a start of 27.10 23:54 for day 28.10: regenerating covers 27.10 07:00 → now). `generatedAt` in `Status()` is still the real moment of generation.

**State of work for the day.** The report says not only what is new in the period but also what is going on now — even after a quiet night. A project repository is in the report when it has a commit or a session in the period, **or** work in progress: a session that, according to the Agents area, is working or waiting for you (even without a journal entry in the period — then from the Agents area state alone), a worktree with unmerged commits changed in the last 7 days ("changes waiting for a merge"; at most the 5 freshest per project), a plan updated in the last 7 days with a ticket in progress, in review or waiting (or an active plan started and not finished) or a working coordinator, a work board card in "W toku" (In progress), "Czeka na mnie" or "Do sprawdzenia" (To check) (in the card's main project, when no other work from the report already describes it; cards — with no age limit, because the board is your list). An abandoned branch or a plan from weeks ago does not hang in "W toku" every day (`STANDING_MAX` in `reports/collect.py`). Such a project gets a *W toku* line in "Na daily" (and *Czeka na mnie* when an agent is stopped now — a card in "Czeka na mnie" without a waiting agent is *W toku* with a remark, decision 36). The number in the header is "N projektów w raporcie" (N projects in the report). The sentence "W projektach nic się nie dzieje — nie ma nowej pracy ani pracy w toku." (Nothing is going on in the projects — there is no new work or work in progress.) appears only when nothing is really going on. The live report during the day (below) recompiles this after changes in the work.

**Notification.** After the scheduled report, at most one per day (the day is in the state file, so a service restart does not repeat it): "Raport poranny gotowy — 3 projekty" (Morning report ready — 3 projects), with the number of commits, sessions and waiting items in the body, and an "Otwórz" (Open) button (also a click) → `xdg-open index.html` through `systemd-run --user --scope`. `--no-notifications` turns it off.

**Read.** A day's report is unread until you open it: the "Raport" (Report) link in the widget (`Open()`), "Otwórz" in the notification, or "raport" in KRunner — all three go through the service and record the day in `opened` of the state file, and then send `Changed`. A new day is a new, unread report; `Generate()` on the same day does not light the dot again. The Tide window ("Raporty" view) counts the same way: the latest unopened report on top of the active window for 1.5 s → `MarkRead(day)`, while "Otwórz w przeglądarce" (Open in browser) and "Pełna strona" (Full page) go through `OpenDay()`; browsing older days clears nothing. "Otwórz" in an old notification (a report from before a newer one) opens the old report but does not clear the dot for the new one. A state file from before this change (without `opened`) is treated as read, so that the update does not light the dot for a report viewed long ago. Widgets: the sidebar header — "Raport · dziś 07:00" (or "wczoraj", "24.10") with a peach dot next to "Tide"; the agents popup in the dock — the same link in the bottom row (the popup header has no free space, and the popup has a fixed width); the dock tile unchanged. Without a report the link disappears. Decision 51.

**Notification after a ticket.** A "Skończone dziś" note arrives with the notification "LogTool: skończone — <one sentence>" (application "Tide", icon `view-calendar-day`), with 2–3 sentences of the note in the body and possibly "Wymaga sprawdzenia ręcznego" (Needs a manual check); "Otwórz" (also a click) → the Tide window on the "Raporty" view with the day and project (`--view reports --select YYYY-MM-DD/<slug>`, a second launch asks the running window to `Show`). Several tickets of one project within 5 min make one notification that updates itself (`replaces_id`: "LogTool: skończone 2 zadania" and the sentences as bullet points). A ticket described once gets no second note or notification — also after a service restart (the notes file); the first run only remembers what is already done. `--no-notifications` turns the notifications off (the notes stay).

**Retention.** After each report, the `YYYY-MM-DD` directories older than `keepDays` (30) days disappear. We delete only directories with such a name, not symlinks.

**What is in the report.**

- **Dziś w skrócie** (at the top of the index and of the view in the Tide window): 3–5 sentences about all projects together, which can be said at the daily — what is finished and why it matters, what is ongoing and how far along it is. Ticket numbers only as small links, without jargon ("coordinator", "subagent", "session … asks") and paths. The model writes them in the same call as "Na daily"; the fallback from rules: one sentence per project with work ("LogTool — skończone: …; w toku: …") and one line "Same porządki: …". What is waiting for you is added by the live view ("Teraz czeka na ciebie: …"), not by the model.
- **Index — project overview**: a card per project repository with 1–2 sentences of "what happened" (`headline`), counters *zrobione* / *w toku* / *czeka* / *porządki* (done / in progress / waiting / chores), plan progress ("TOM-20 · 8/13"), "Czeka na mnie" items and a link "Na daily i szczegóły" (For the daily and details). Projects with something waiting come first and are highlighted (a peach card). Projects with only chores (agent instructions, tracker, configuration) are not cards — one collapsed line "Porządki: QuotaApp, BO, dev-dashboard" with their items on expanding (`choreProjects` in `report.json`). "Kopiuj wszystko jako tekst" (Copy everything as text) copies the standup of all projects. Below the cards is a collapsed section **"Poza projektami"** (Outside projects): sessions from outside git repositories (e.g. in the workspace root or in a temporary directory) — state, title, task, result or question, "Wznów" (Resume); with no "Na daily" and no pages of their own (expanded when any of them is waiting).
- **Na daily** (at the top of the project page): *Skończone dziś* (notes per ticket, below), *Praca* (Work) — *Zrobione*, *W toku*, *Czeka na mnie*, *Uwaga* (Attention) (only when present), *Dalej* (Next) and "Na czym polega" (What it is about) — and a collapsed **"Porządki"** (tracker, plans and agent instructions, configuration, tools with no product changes), with a "Kopiuj jako tekst" (Copy as text) button for this project only (chores at the end of the text). Everything else below is collapsed in "Szczegóły" (Details).
- Index (in "Szczegóły"): numbers (waiting for you, projects in the report, commits with lines, sessions, runs of scheduled tasks), the "Czeka na ciebie" (Waiting for you) list with links to sessions (a session from outside repositories — to an anchor in "Poza projektami"), a collapsed list of projects without activity with what is left in them (worktrees to clean up, changes), a snapshot of AI limits (levels in colors as in the widget) and a place for a forecast (`reports/limits.py: forecast` — returns nothing for now), a link to the previous report.
- Project page: agent sessions from the period (waiting / working / finished / interrupted) with title, branch, worktree, duration, last activity and the **agent's last message** from the journal (that is what says what it did; an excerpt up to a paragraph boundary, the whole under "Cała wiadomość" (Full message)), with a "Wznów" button (copies `cd … && claude --resume …`, `codex resume …`, and for a Codex session from a snap `/snap/bin/codex resume …`); for Codex threads with a scheduled task — the task's name and rhythm (`automation.toml`) and the runs from the period with results. Commits from the period by branch (the main branch, branches with commits outside the main one, detached worktree HEADs; each commit once) with author, time, subject, number of files and lines and an "agent: Claude/Codex" mark when indicated by a Co-Authored-By footer or the author address, an agent branch/worktree, or the commit was made during an agent session in that directory. Worktrees to clean up, worktrees with work only here (commits without another branch and server, changes) and uncommitted changes in the main directory.
- A project is a repository from the projects directory together with its worktrees (including those in `~/.codex/worktrees`). Only a repository with a commit or a session in the period, or with work in progress (above, "State of work for the day"), gets its own page; sessions from outside repositories go to "Poza projektami". `Status()` and the notification count only project repositories.

**Reading the journals.** Files changed since the start of the period; the state, title and last message from the file tail (2 MB) through the same parsers as the Agents area, the first entry and first command from the start (256 KB). Runs of scheduled tasks: from the place in the file where the period starts (binary search by timestamps), with JSON parsed only in lines with the start or end of a turn. The state of sessions from the last day we take from the Agents area; older ones — from the journal (a finished turn is "finished"). Worktrees from the Worktrees area (with sizes), and before its first read the report scans the repositories itself.

**Pages.** Self-contained files: CSS from the palette (`palette.json` next to the service, in the repository `theme/palette/mist.json`), Geist and Geist Mono from the system (`@font-face` from `local()`, with a system font as fallback), no network and no external files, dark Mist, a layout from a narrow window (one column, 16 px margin) up to 2560 px (two columns on a project page). The only script is "Kopiuj" (Copy), and the `Content-Security-Policy` allows only its SHA-256 hash. All text from git and the journals is escaped (also in attributes). Instead of a `tide://` scheme there are paths with "Kopiuj", commands to copy (Konsole, Rider, session resume) and a plain `file://` link to the directory.

**KRunner.** "raport" (or "rap…", "raport poranny") shows the latest report; Enter opens it in the browser.

**Tide window.** The "Raporty" view (at the top of the day "Dziś w skrócie" with "Czeka teraz" (Waiting now) — live, like the agents widget; on the cards "Skończone dziś: …"; in the "Na daily" panel notes with a manual check, and outdated "Czeka na mnie" items struck through with the remark "już nieaktualne" (no longer current); projects with only chores in one line; it refreshes itself after `Changed`, "Wygeneruj teraz" (Generate now) stays) (`apps/reports`, `--view reports`, `--select YYYY-MM-DD[/slug]`) shows the reports without a browser: days on the left, the project overview and the selected project's "Na daily" with "Kopiuj jako tekst". It neither reads nor renders HTML: with each report `reports/data.py` writes a `report.json` next to the pages — the order of cards as in the index (waiting first, chores-only last), the "Na daily" of every project (`done`, `doing`, `waiting`, `next`, `chores`, `theme`, `headline`, counters), `since` ("Od poniedziałku 07:00" (Since Monday 07:00)), `period`, `source`, the numbers, "Poza projektami" (state, title, task, result or question) and ready clipboard texts (`copyText` per project, `copyAll`) from the same generator as the buttons on the page (`render.daily_text`). The strings in `report.json` are raw — the view shows them as plain text. The window reads it through `Days()` and `Day()` (the service checks the day name and the file size), and takes project tags from `/Board Snapshot()` if the Board is running. The "nowy" (new) badge in the window bar comes from `Status()`.

**Na daily** (`reports/standup.py`, `reports/summarize.py`).

- *Facts* (`standup.facts`, project repositories only): tickets from the coordination board file and the plans in the repositories (ID, title from the plan or YouTrack, the start of the YouTrack description without Markdown and migration metadata, the start of the first subagent's command, notes from the plan, state, note, review result in numbers, the first line of the work result, an unanswered question) — finished ones only from the period, `todo` for "Dalej"; the plan's goal (the paragraph under the heading "Cel…"/"Zakres"/"Kontekst" (Goal/Scope/Context), otherwise the first paragraph); work units: a merge into the main branch (commits of the second parent, `git rev-list P1..P2`), direct commits on the main branch grouped by ticket ID or by the scope from `feat(scope):`, a branch merged into an integration branch (all its commits from the period are in a larger branch other than the main one — with a chain of branches we point to the largest), a branch other than the main one, a bare session; for each unit the commit subjects, areas of change (the top-level directory with the file count from `--numstat`, two segments for `src/`, `tests/`, `apps/` and the like) and sessions with the **user's first command** (the task, up to 1500 characters from the start of the journal). Internal subagent branches `worktree-agent-*` are skipped (also in the list of unmerged ones). A session goes to a unit by branch or worktree directory, a ticket goes to a unit by the branch from the board or the ID in the branch name (also `la035` with a known `LA-035`). A coordinator's session is not separate work — its command goes in as project context.
- *Work or chores* (`category`: `work` or `chores`, a hint from deterministic signals; the model may correct it): a commit is a chore when it has the type `chore`/`ci`/`build`, `docs(plan|agents|tracker…)`, a subject about the tracker (YouTrack, Jira, Linear, tracker, CLAUDE.md, AGENTS.md, MyAI, agent instructions/work plan) or changes only `CLAUDE.md`, `AGENTS.md`, `docs/agents/**`, `.claude/**`, `.codex/**`, `.agents/**`, `.mcp.json` and tracker files; a unit — when all its commits are chores, and without commits — when the command or the session title talks about the tracker or instructions; a ticket — when its title or plan talks about the tracker or all of its work is chores. A question to the user and a failing test always go to "Czeka na mnie".
- *Description from rules* (`standup.rules`, the fallback and `"summary": "rules"`): tickets by state (done → Zrobione with the result or a description, `w-toku`/`review` and review "needs fixes" → W toku with progress from the note, `czeka` → Czeka na mnie — only when the coordinator's session is in the report and is stopped according to the Agents area; without a coordinator (a plan the board does not know) or without its waiting session → W toku "a question was left unanswered" (likewise a board question without a coordinator), `todo` → Dalej, chores → Porządki), then units without a ticket: a merged branch (also into an integration branch) → Zrobione (the first sentences of the agent's message or "Zmiany są już w głównej wersji, m.in.: …" (The changes are already in the main version, among others: …)), an unmerged branch → W toku ("zmiany czekają na scalenie" (changes waiting for a merge), next: "przejrzeć i scalić" (review and merge)), more than two such branches without a session — one summary item ("N rozpoczętych zmian czeka na przejrzenie" (N started changes await review)); chores → Porządki; a session that is waiting now → Czeka na mnie with its `waitingText` ("agent pyta: "…"" (the agent asks) — also `reply`, a question at the end of the turn, PT-29; "agent czeka na twoją zgodę (…)" (the agent is waiting for your approval)); a question left in the text of a finished turn that is not waiting (e.g. a scheduled task's turn) → W toku ("agent zostawił pytanie: …" (the agent left a question), only without a user reply); failing tests (a heuristic on the agent's message) and number conflicts → a separate small section **"Uwaga"** (`attention`, `kind: attention`) — nobody is waiting on them, so they are not "Czeka na mnie" and do not count toward the badges, `Status().waiting` or notifications; the section is in the HTML (after "Czeka na mnie"), in `report.json` (`attention`, counter `counts.attention`), in the Tide window (only when non-empty) and in the clipboard text ("Uwaga:"), always from rules (the model does not write it); a scheduled task → the number of runs and the result of the last one. The number from the branch name ("tide-9 · export") goes to `ref`, not into the text. The sentence for the card in the index: "Zrobione: …; W toku: …". At most 8 items per group.
- *Waiting for me — one definition* (PT-23): the items are always compiled by rules from the Agents area (the `waiting` state and `waitingText`, the coordinator's question from the board file with `questionTs`), with `kind: blocked`, `sessionId` and `since` (tests and number conflicts are in "Uwaga", not here). The model may only rephrase an item with the same `ref`; its other "Czeka na mnie" items disappear, and the facts for the model contain neither the last text of a waiting session (`agentSaid`, `said`) nor the question from the ticket note.
- *Description by a model* (`"summary": "claude"`, the default): one call per report, in the service's worker thread:
  `claude -p --model <summaryModel> --output-format json --no-session-persistence --safe-mode --tools "" --disallowedTools <built-in tools, mcp__*> --strict-mcp-config --mcp-config '{"mcpServers":{}}' --permission-mode dontAsk --disable-slash-commands --system-prompt <instructions>`,
  facts on stdin, the working directory is an empty temporary directory, its own process group (after 180 s `SIGKILL` of the whole group). `--safe-mode` instead of `--bare`, because `--bare` reads only `ANTHROPIC_API_KEY` and skips the subscription login. Program: `$TIDE_CLAUDE_BIN`, `PATH`, `~/.local/bin/claude`, `~/.claude/local/claude` (the systemd service has a short `PATH`).
- *Local model* (`"summary": "local"`, PT-15): the same model choice as for cards (`summarize.Model` — the one place that chooses between `claude -p` and `models.chat`), the same facts after `strip_secrets`, the same instructions and validation, plus a response schema derived from the example at the end of the instructions (`format` in Ollama, `response_format` on an `openai` server). The model from the `summary` role in `models.json` (by default `qwen3:8b` with a context of 8192), `summaryModel` is ignored. One call **per project**, not per report: a context of 8192 tokens does not fit the instructions, the facts of all projects (in a real report about 41 thousand characters) and the answer at once. A project's facts are trimmed as for Claude, only to a smaller limit derived from the role's context (about 2.5 characters per token, minus the instructions and 2048 tokens for the answer — at 8192 about 11 thousand characters; with a larger context, e.g. on a Mac, up to the usual 16,000). The calls go through the model queue (section "Local models", "Queue") at low priority — card descriptions slip in between the report's projects instead of waiting for the whole. The 180 s limit per call counts from its start (not counting the wait in the queue), and 600 s for the whole report — including waiting (projects after the deadline — rules; when the deadline passes in the queue: "Model lokalny niedostępny — opis bez modelu (kolejka: czekał N s — serwer zajęty: opisy kart, 127.0.0.1:11434)" (Local model unavailable — description without a model (queue: waited N s — server busy: card descriptions, 127.0.0.1:11434))). A role with a context too small for the instructions, the answer and at least 1000 characters of facts is not asked at all ("kontekst roli "summary" za mały (2048 tokenów) — potrzeba co najmniej …" (the context of the "summary" role is too small (2048 tokens) — at least … needed)), because Ollama would truncate the instructions. In the report's worker thread, like a Claude call; shutting down the service (`ReportMonitor.stop()`) interrupts an ongoing call at once (`models.Cancel` closes the socket) and does not ask about further projects.
  **When the local model fails** (the server does not respond, the role is unavailable, a timeout, a server error, a bad answer), the report takes the rules and shows the `ModelError` message in Polish in the section header (and also in `report.json` → the "Raporty" view) and in the service journal: "Model lokalny niedostępny — opis bez modelu (serwer nie odpowiada, 127.0.0.1:11434)" (Local model unavailable — description without a model (the server is not responding, 127.0.0.1:11434)), and for a bad answer "Model lokalny dał złą odpowiedź — opis bez modelu (…)" (The local model gave a bad answer — description without a model (…)). After a server error we do not ask about further projects; a bad answer about one project does not stop the others. When some projects have a model description, the header says "opis: model lokalny qwen3:8b · część projektów z reguł: … — Model lokalny niedostępny — …" (description: local model qwen3:8b · some projects from rules: … — Local model unavailable — …). We never fall back to Claude — there is no setting that would enable it (the plan decision of 27.10). The page footer: "model lokalny qwen3:8b (127.0.0.1:11434) — nic nie wychodzi z tego komputera" (local model qwen3:8b (127.0.0.1:11434) — nothing leaves this computer) or, with a server on the network, "…idą tylko do tego serwera modeli" (…go only to this model server).
- *Input*: concise JSON per project, at most 16,000 characters (ever shorter excerpts of messages, commands and descriptions and fewer commits, until it fits) and 90,000 in total — projects over the limit get rules. Commit subjects in the order feat, fix, refactor, test, docs; chores (`docs(plan)`, `chore`) skipped. The review note on a `gotowe` ticket is not sent (done is the end), and neither are the numbers of unmerged branches. Before sending, anything that looks like a secret is removed from every string: `sk-…`, `ghp_…`, `github_pat_…`, `glpat-…`, `xox?-…`, `AKIA…`, `AIza…` keys, JWT, YouTrack `perm:…` tokens, `Bearer …`, PEM private keys, passwords in URLs, `token=`/`password:`/`API_KEY=…` assignments, `.env`-style lines, 40+ hexadecimal characters and long random strings; the home directory → `~`. The instructions tell the model to treat the strings as data, not commands.
- *Instructions*: a report for people, not for AI — in Polish only, without jargon ("koordynator", "podagent", "sesja", "worktree", "commit", "gałąź", "merge", "prompt" (coordinator, subagent, session, worktree, commit, branch, merge, prompt)), without paths and file names, the ticket number only in `ref`; every "Zrobione" item says WHAT was created or changed, in language that can be said at the daily to someone who does not know the code (not "ticket 01 gotowy", not "kroki V3b1–V3b10 scalone" (steps V3b1–V3b10 merged)); `about` — what for, concretely, empty when the facts say nothing; `brief` — 3–5 sentences about the whole day. The best sources of content are the user's command, the ticket description and the plan goal, feat/fix subjects and areas of change. The ban on inventing facts stays. Input: `{"projects": [facts of the projects to describe], "others": [{"name", "headline"}]}` — "others" are projects described earlier (from the cache), only for "Dziś w skrócie".
- *Answer*: an object `{"brief": [sentences], "projects": [{"name", "headline", "theme", "done": [{"ref", "text", "about"}], "doing": [{"ref", "text", "about", "next"}], "next": [{"ref", "text"}], "chores": [{"ref", "text"}]}]}`. Readability (`standup.human_problem`): a string with jargon, a path or a file name, in English (a word heuristic) or consisting only of numbers ("LA-035 gotowe", "ticket 01 i ticket 02 …", "kroki V3b1–V3b10") is rejected; a ticket ID at the start or in parentheses moves to `ref` ("LA-035: bazowa wersja…" → `ref` LA-035, "Bazowa wersja…"). Rejected `about`/`next` disappear, a rejected item counts in `rejected` — a group from which the model lost everything gets the items from rules; a `headline` and `theme` with a problem are replaced by a sentence from the items. "Dziś w skrócie" from the model passes when at least two good sentences remain (otherwise rules). Validation (`standup.validate`): only projects from the input, plain strings without control characters, trimmed lengths (text 260, `about` 320, paragraph 600, `headline` 320), at most 8 items per group, `ref` only from known IDs (a different spelling returns to the form from the plan, an invented ID disappears); junk items are rejected ("nic nie czeka" (nothing is waiting), "nie ma decyzji" (there is no decision), "N gałęzi nie jest scalonych" (N branches are not merged), "bez review", "czeka na review", `worktree-agent-*`), and a Zrobione/W toku item that itself talks about the tracker or agent instructions moves to Porządki. The old field `why` is read as `about`. Markdown in a field (PT-13: gpt-oss put whole lists into a single string) is turned into plain text: bold, code, headings and links disappear, lines join like sentences, and a bullet list in the `text` field becomes separate items with the same `ref` (`about`/`next` on the first); a plain one-sentence string without Markdown stays unchanged. One line is not a list: "2. etap migracji gotowy" stays with its number. Only in the local model's answer (qwen3:8b made these mistakes on real data) two fixes are added: the same item in several groups stays only the first time (the order Zrobione, W toku, Czeka na mnie, Dalej, Porządki), and a "Czeka na mnie" item with the ID of a ticket in the `todo` state moves to "Dalej" (of a done one — disappears), unless the ticket has a question or a waiting session in the facts; a "Dalej" item with the ID of a done ticket disappears. Claude's answer does not get these fixes. A project the model did not describe gets rules; "Czeka na mnie" always from rules (above) — also after the fixes to the local model's answer. Every string is escaped when the HTML is assembled (the CSP blocks foreign scripts anyway). An error at any stage → rules with a note in the section header and in the service journal.
- *Per-project cache*: `summary.json` keeps the model's answer for each project with the SHA-256 key of its concise facts (with the model identity and the instructions; the identity is the Claude model name or `local|point|address|model|ctx`, so switching `claude` ↔ `local` or changing the model in `models.json` does not pick up someone else's answer) and carries over to the new version of the report directory. Recompiling on the same day (`Generate()`, the live report) calls the model only with the projects whose facts changed; unchanged ones take the description from the cache and go in as "others". A project the model did not describe stays on rules until its facts change (without asking again). No change in facts — no call. Claude: one call with the changed projects (plus "others" for "Dziś w skrócie"); local model: one call per changed project (a 600 s budget, interrupted when the service shuts down as above), and "Dziś w skrócie" from rules — a small model sees one project at a time.
- *Cost*: the morning report — one Sonnet call (in a real report with 9 projects about 41 thousand characters of input, the answer in about 27 s; Haiku from decision 52 needed about 75 s for a smaller input). During the day: at most one live compilation per 12 min, and in it at most one call — only with the changed projects (usually 1–2, that is a few thousand characters); with continuous work that is up to 5 calls an hour, with a few finished tickets a day — a few to a dozen or so. A note after a ticket: one small call per batch of tickets from one Board refresh (about 1–3 thousand characters, a 90 s limit). Everything counts toward Claude limits like ordinary work with Claude Code; the data goes to Anthropic on the same terms. `"summary": "rules"` — zero calls, notes and description from rules.

**Names, cards and lengths** (readability after PT-24 — a report "for people, not for AI, without gibberish").

- *Project names from the work board* (`reports/cards.py`, `collect.assign_labels`): the report gets a snapshot of the Board (in the service `/Board Snapshot()` from the main thread, with `--report` — the Board from `--board-dir`; `--no-board` or an error — without it) and names a project the way the Board does: the project's `repo` → `name` ("LegalApp" instead of `agent-team`, "LogTool" instead of `LogViewer`). The Board name is on the index and view cards, in the header of the project page, in "Dziś w skrócie" from rules, in "Teraz czeka na ciebie", in clipboard texts and in the model's input (`"name"`; an answer under this name or under the directory name goes back to the project — `standup.project_of`). The directory name stays the key (page files, notes, description cache) and a small gray note next to the Board name (`folder` in `report.json`) when they differ. A repository outside the Board — the directory name; two report projects with the same Board name get the directory in parentheses.
- *A card instead of a branch name*: a unit of work belongs to a card when the card's `branch` is its branch, the card's `session` is one of its sessions (`claude:<id>` or the bare id), the ticket's plan has the key `card:`, or the branch name has the token `k-12` — the last one only in the repository of the card's project (main or additional), so that a foreign branch "k-1-…" does not pretend to be a card. The item then names the work by the card's number and title: "W toku: K-1 Data ważności wiadomości — zmiany czekają na scalenie." instead of "k-1 · data waznosci wiadomosci"; several units of the same card in a project are one item. Without a card the title from the branch name is plain text (`standup.humane_branch_title`: without a word like "fix ·", the number in capitals, the first letter capitalized — Polish characters cannot be reconstructed from a branch name). The model gets the card in the unit's `card` field.
- *A card in several projects* (PT-28, the card's `projects`): described once — in the card's main project (or, when it is not in the report, in the first project with its work) with the remark "(pracuje też w: legacyinfra, mobileapp)" (also works in: legacyinfra, mobileapp); in the other projects only a mention "Zmiany w ramach K-1 Data ważności wiadomości" (Changes within K-1 …), which does not get its own sentence in "Dziś w skrócie". The model sees this as `cardAlsoIn` and `cardDescribedIn`.
- *No cutting off mid-sentence*: text for people never ends with "…has a clear meaning in the model and in…". `standup.fit` takes whole sentences from the start as long as they fit in the limit; a first sentence longer than the limit gives an empty string. The model's answer: a "Dziś w skrócie" sentence that is too long drops out (of two sentences the first stays), an item that is too long is rejected (a group without the model's items takes the rules), a `headline` that is too long — a sentence from the items, `theme`, `about` and `next` — whole sentences or nothing. Rules: the header and the "Dziś w skrócie" sentence lose parts (the second done one, then "W toku"), and at the end the numbers remain ("Zrobione: 3 rzeczy, w toku: 1 rzecz."); the remarks from the agent's message, descriptions and ticket results are whole sentences; a session title cut by the session index we rebuild from the full command; a question that is too long in "Teraz czeka na ciebie" is replaced by "agent czeka na odpowiedź" (the agent is waiting for an answer). The limits are safety bounds (an item 360 characters, a remark 400, a header 400, "Na czym polega" 600, a "Dziś w skrócie" sentence 320), and the model's instructions give shorter, hard lengths (a brief sentence up to 200 characters, a header up to 250, an item up to 160, a remark up to 200) and say that a sentence that is too long is rejected as a whole.

**Plans and tickets** (`reports/plans.py`, an add-on — without it the report works as before): on the project page a card of every coordinator from the period (from the board file `coordination.json`: title, label, progress bar, counts by state, tickets starting with waiting ones with notes and links to YouTrack) and of every plan from `docs/agents/plans` (active or changed in the period) that the Board does not know — then the states come from the plan alone, with its errors. A project card in the index gets the mark "TOM-20 · 8/13". For "Na daily" a plan gives the goal and notes, and a plan with `tracker: youtrack` that the Board does not know gets the starts of ticket descriptions from YouTrack (the same GET query, server and token as the Board; IDs through the migration map, e.g. `LA-035` → `LEGAL-35`; `--no-youtrack` turns it off). All texts are escaped like the rest of the report.

### Live report

A day's report is created at 07:00 (or at the first login) and then refreshes itself (`reports/live.py`, `report_monitor.py`):

- **Events**: a change of a ticket's state on the coordination board (every new version of the boards from the Coordination area; the first read after start is the baseline) and an agent in a repository that stopped working (`working` → `done`/`idle`/`ended`). A change in what is waiting does not compile a report: Day/Days/Status recalculate "waiting" on every read, and the service only sends `Changed`.
- **In batches and cheaply**: an event marks the report as outdated; we compile it at the earliest 90 s after the last event of a series (with continuous events at the latest 12 min after the first) and at most once per 12 min, in the same thread as the morning report, without a notification and without lighting the "unread" dot (the same day). Before the day's morning report nothing is compiled. The model — only the changed projects (the per-project cache, above).
- **The work board is not a project**: the Board repository (the `board.md` marker from `tide-board`, `board/marker.py`) is not a report project — `collect.build` skips it (sessions from its directory are in "Poza projektami"), and the "Skończone dziś" notes assigned to it (from before PT-28: a coordinator whose session entered the Board's documents directory, e.g. DPT-1…3 of card K-1) do not make a "tide-board" card. With every new version of the coordination boards and before compiling the report, `notes.owner_of` looks for the right project: the coordination board of that coordinator (the id from the note's id), and when that also points to the Board or there is none — the work board card tied to its session or plan → the repository of the card's project. The fix goes once into `report-notes.json`; a note without a resolution is hidden (report, view, notifications) until the next attempt.
- **Names and cards live**: every compilation takes a fresh snapshot of the work board, so a new card or a changed project name is in the report at the nearest refresh; a project without a card in the morning report (only with a "Skończone dziś" note) has the directory name until the next compilation.
- **Live** (`data.apply_live`, the current day): a "Czeka na mnie" item with `sessionId` whose session is no longer waiting or has been waiting since later (for something else; 90 s of slack) gets `stale` — the view strikes it through ("już nieaktualne"), it does not count toward the badges (`Status().waiting`, `Days()`), and disappears from cards and clipboard texts. `now` are the sessions waiting right now (like the agents widget), `nowText` — the sentence "Teraz czeka na ciebie: …" for "Dziś w skrócie". Older days are snapshots.

**Skończone dziś** (`reports/notes.py`): a ticket on the coordination board moves to `gotowe` (after a review OK or without a review) → a note in the day's report under the project: 2–3 sentences — what was done, what it gives the user and whether it needs a manual check. A ticket finished without a review (the board note "bez review", no review result) says so outright: "Skończone bez przeglądu." (Finished without review.) — in the note (from rules and from the model, which gets `reviewed: false`) and in the notification ("(bez przeglądu)" next to the sentence in the combined one). Manual check: `check: manual` (or "test ręczny" (manual test)) in the notes of a plan row, the `check: manual` field of a work board card (the `card_check` hook — `ReportMonitor.card_check`, attached in `app.main` after the work board starts as `app.board_card_check`: a function `(board, ticket) → {"check": "manual", "spec": path} | None`, where `board`/`ticket` are the coordination board from `Board()` and its ticket. The card is found by `notes.card_for` in the `/Board Snapshot()` snapshot: first the plan's `card` key, then the card's `session` equal to the coordinator, then the card's `branch` equal to the ticket's or the coordinator's branch; a relative `spec` — from the repository. The hook is called in the main thread, the notes thread gets the ready results; the result is read by `notes.manual_check`) and the points from the section "Co przetestować ręcznie" (What to test manually) of the plan or the card's specification (points with the ticket ID or under a subheading with the ID; a file without IDs — all of them). The content comes from the model (the same safe call, instructions `notes.NOTE_PROMPT`, readability validation) or from rules: the card description ("co zrobiono" (what was done)) or the first sentence of the work result, the first sentence of the ticket description, "Do sprawdzenia ręcznie: …" (To check manually: …). A ticket must stay in `gotowe` for 90 s before it gets a note ("gotowe bez review" followed at once by a review gives no note). At most one note per ticket per day: a ticket finished again (e.g. after a review) replaces its note (after an earlier "bez przeglądu" with the remark "Teraz już po przeglądzie." (Now after review.)) and updates the same notification (`replaces_id`) instead of sending a second; a notification goes only for notes really added or changed, so the report and the notification say the same. A ticket finished while the service was not running gets a note if its last move is from the current report day. The notes are in the notes file (not in the report directory), so they are visible at once — `Day()` adds them to `report.json`, and the next compilation of the report also to the HTML pages and clipboard texts.

## AI limits

The AI limits widget reads the remaining limits straight from the QuotaApp service (decision 21). The Tide service adds memory to this: it stores the readings, computes the pace and the exhaustion forecast from them, and sends notifications at thresholds. The widget shows the forecast when the service is running; without it everything stays as before.

| Element | Value |
| --- | --- |
| Object | `/Limits` |
| Interface | `org.tide.Daemon.Limits` |

| Member | Signature | Description |
| --- | --- | --- |
| `Forecast()` | `→ s` | JSON `{available, error, services}` (described below) |
| `Changed` | signal without arguments | the forecast or the availability of QuotaApp changed — fetch `Forecast()` again |

`available` says whether the last `GET /status` succeeded, `error` is a short reason in Polish (without the token), and `services` is `{"claude": {"session": {…}, "weekly": {…}}, "chatgpt": {"weekly": {…}}}` — only the windows that have history. A forecast of one window:

| Field | Type | Description |
| --- | --- | --- |
| `state` | string | `runs-out` (will run out before the reset), `lasts` (will last until the reset), `exhausted` (100%), `unknown` (no forecast) |
| `reason` | string | for `unknown`: `noData`, `tooLittleData`, `noReset`, `resetPassed`, `stale` |
| `text` | string | "wyczerpie się ok. 17:40" (will run out around 17:40), "starczy do resetu" (will last until the reset) or empty |
| `short` | string | "koniec ok. 17:40" (end around 17:40), "starczy do resetu" or empty — for narrow rows |
| `whenText` | string | the exhaustion moment alone: "17:40", "jutro 09:30" (tomorrow 09:30), "czwartek 11:00" (Thursday 11:00) |
| `used`, `remaining` | int | used and remaining percent from the last reading |
| `readTs`, `resetTs`, `exhaustTs`, `windowStartTs` | int or null | last reading, window reset, forecast exhaustion, first reading of the window (epoch) |
| `marginS` | int or null | how many seconds before the reset the limit runs out (`runs-out` only) |
| `ratePerHour` | float or null | pace in percentage points per hour |
| `points` | int | how many readings went into the forecast |

**Readings.** Every `limits.pollS` (120 s) the service does `GET /status` on the local QuotaApp API. This is only a read of that service's memory — it reads the pages itself every 5 min (and not at all in full screen), so we add no page load and no `POST /refresh`. We recognize a new reading by a change of `lastRead`, and the same reading does not enter the history twice. The port and token are in `~/.config/limity-ai-widget/config.json` (or `$LIMITY_CONFIG`) — the service reads them on every request, in memory only; the token does not reach the journal, the history, the state or a D-Bus reply. The request runs in a worker thread with a 5 s limit, so a hanging API does not block D-Bus.

**History.** `$XDG_STATE_HOME/tide/limits-history.jsonl`: one line per reading of one window, numbers only — `{"t":1793016060,"s":"claude","w":"session","u":62,"r":1793030400}` (reading time, service, window, used percent, reset moment). The reset label from QuotaApp ("poniedziałek 18:30" (Monday 18:30), "~czwartek 11:51" (~Thursday 11:51) — computed from the moment of the reading, in the Europe/Warsaw zone as in that service) is converted to the nearest such moment after the reading. The file covers `limits.historyDays` (14) days: older lines drop out at start and at most once an hour (rewriting through a temporary file). A day is about 1150 lines, 14 days about 1 MB.

**Windows.** We do not store window boundaries — they follow from the readings, so after a restart they are the same. A new window starts when: the reset of the previous reading has passed, the reset moved further than `resetJumpMin` (session 30 min, week 6 h — the "~" jitters by a minute), usage dropped by more than 3 points, or the gap between readings is longer than the whole window. The forecast and notifications take only the current window.

**Forecast.** The pace is the slope of a line (least squares) through the current window's readings from the last `lookbackMin` — session 90 min, week a day. The time axis is "working time": going back from the newest reading, a flat stretch (usage does not change) counts at most as `idleGapMin`:

- **Session (15 min).** A lunch break does not dilute the pace: after an 80 min break the forecast says "at your working pace", not "on average over the last 90 min". A short plateau between consecutive percents (the page gives whole percents) counts in full as long as the pace exceeds about 4 points/h — a slower one will not exhaust a 5-hour window anyway.
- **Week (0 = no shortening).** Nights and breaks are the rhythm of the week: the pace from the last day together with the night predicts the following days better than the pace from working hours alone, which would assume work without a break.

From the last reading: exhaustion = reading time + (100 − usage) / pace, rounded to `roundMin` (session 5 min, week 30 min). Before the reset → `runs-out`, "wyczerpie się ok. 17:40"; after the reset or at zero pace → `lasts`, "starczy do resetu". No forecast (empty text): fewer than 3 readings or shorter than `minSpanMin` (session 15 min, week 3 h), an unknown reset (e.g. ChatGPT without a session window), the reset has already passed or the last reading is older than `staleMin` (session 30 min, week 6 h — e.g. readings paused in full screen).

**Notifications.** Checked only on a new reading, in the style of "Agent czeka" (Agent is waiting) (application "Tide", icon `view-statistics`, no button); a new notification for the same window replaces the previous one.

| When | Title | Body |
| --- | --- | --- |
| usage crossed a threshold from `alertUsedPercent` (80, 95) | "Claude: zużyto 82% limitu sesji" (Claude: 82% of the session limit used) | "Zostało 18% · reset 18:30" (18% left · reset 18:30) |
| forecast: exhaustion more than `alertRunoutMarginMin` (30 min) before the reset | "Claude: limit sesji skończy się przed resetem" (Claude: the session limit will run out before the reset) | "Przy obecnym tempie wyczerpie się ok. 17:40, a reset dopiero 18:30 · zostało 38%" (At the current pace it will run out around 17:40, and the reset is only at 18:30 · 38% left) |

- Each threshold and the exhaustion — once per limit window. A jump over several thresholds at once (70 → 96%) gives one notification, for the highest. A threshold and the exhaustion in the same reading go together (the threshold's body gets the line "Przy obecnym tempie …").
- What has already gone out is in `$XDG_STATE_HOME/tide/limits-alerts.json` (we recognize the window by its first reading or by the reset moment within the tolerance `resetJumpMin`), so a service restart repeats nothing. A first start with a limit already above a threshold gives one notification — and not again after that.
- A reading from a window whose reset has already passed (e.g. an old `lastRead` after readings were paused) does not notify.
- `--no-notifications` turns off these notifications too.

**Thresholds and parameters** are in the palette, the `limits` section in `theme/palette/mist.json` (the service reads the copy `palette.json` next to it, so after a change: `make install-daemon`). A missing section or key = the default values from `tide_daemon/limits/config.py` (the same as in the palette).

| Key | Default | Meaning |
| --- | --- | --- |
| `pollS` | 120 | interval between `GET /status` calls in seconds |
| `historyDays` | 14 | how many days of history to keep |
| `alertUsedPercent` | `[80, 95]` | usage thresholds for notifications |
| `alertRunoutMarginMin` | 30 | notify when the limit will run out at least this many minutes before the reset |
| `session.*`, `weekly.*` | | `lengthMin` (300 / 10080), `lookbackMin` (90 / 1440), `minSpanMin` (15 / 180), `idleGapMin` (15 / 0), `staleMin` (30 / 360), `resetJumpMin` (30 / 360), `roundMin` (5 / 30) |

The color of the "before the reset" forecast in the widget is `semantic.runout` (peach).

The logic without Qt is in `tide_daemon/limits/` (`readings.py`, `history.py`, `forecast.py`, `alerts.py`, `tracker.py`, `backend.py`, `config.py`), the polling in `limits_monitor.py`, the notifications in `notifications.py` (`LimitsNotifier`). Rationale: decision 46 in `docs/decisions.md`.

```bash
qdbus6 org.tide.Daemon /Limits org.tide.Daemon.Limits.Forecast
/usr/bin/python3 daemon/tide-daemon.py --print-limits   # history + one GET /status, writes nothing
```

## Coordination

A session that assigns work to subagents (a coordinator) gets a ticket board: the **Tide Sesje** window
(`apps/sessions`), progress in the agents widget ("TOM-20 · 8/13"), the word "sesje" in KRunner and the section
"Plany i tickety" (Plans and tickets) in the morning report. All of it from journals on disk and from the plan in the repository —
the service writes nothing in repositories and journals, and the coordination queries YouTrack only through GET.
The state of ticket issues from `tracker: youtrack` plans is changed only by the Board's synchronization (PT-7, below
in "Board" — "Synchronization with YouTrack"), only in projects with a YouTrack key on the Board.
Plan standard: [agent-plan-standard.md](agent-plan-standard.md), rationale: decision 50.

**Where we look for plans** (standard 1.1, `coordination/plan.py: plan_folders`, `coordination/boardplans.py`):
`docs/agents/plans/` of the coordinator's worktree and of the main directory, and for a Board project with `ai-docs: tide`
whose `repo` is this repository — also `<board>/docs/<project>/plans/` (the Board: `$TIDE_BOARD_DIR`,
otherwise `~/DevWorkspace/tide-board`; project files are re-read only after a change). The same is read by
the morning report, the number check, `tools/check_plans.py` and `tools/next_id.py`, so the tickets of such
projects are visible and numbered together with the rest of the repository. A coordinator on a branch with a card ID
(`claude/k-12-…`, launched from the Board) gets the plan with `card: K-12` (also a finished one), and the plans of other
cards drop out — the project directory on the Board holds the plans of many cards at once; without a card in the branch the choice is as
before (`session`, otherwise the freshest active plan of that tool).

| Element | Value |
| --- | --- |
| Object | `/Coordination` |
| Interface | `org.tide.Daemon.Coordination` |
| File for the report | `$XDG_STATE_HOME/tide/coordination.json` (0600) |
| Card descriptions (cache) | `$XDG_CACHE_HOME/tide/coordination-descriptions.json` (0600) |
| YouTrack settings | `$XDG_CONFIG_HOME/tide/youtrack.json` (optional; `$TIDE_YOUTRACK_CONFIG` — another file, service tests) |

| Member | Signature | Description |
| --- | --- | --- |
| `Coordinators()` | `→ s` | Coordinators from the last 7 days as JSON (the running one first, then from the freshest activity) — board fields without tickets and runs |
| `Board(s id)` | `→ s` | A coordinator's board (fields below); `{}` for an unknown id |
| `RunDetail(s id, s run)` | `→ s` | A subagent's result ("Pokaż wynik" (Show result)): `description`, `label`, `model`, `role`, `rounds` (`kind`, `status`, `startTs`, `endTs`, `summary`, `durationMs`), `result` (the report), `excerpt` (the last statements and tool calls from its journal: `ts`, `kind` `text`/`tool`, `text`), `transcript` |
| `Activate(s id)` | `→ b` | Go to the coordinator: the window of the running session (like `ActivateSession`), otherwise resume in a new Konsole window (`claude --resume` / `codex resume`) |
| `OpenDiff(s id, s ticket)` | `→ b` | `git diff --stat --patch <main>...<ticket branch>` in Konsole in the repository's main directory (the branch must exist locally); without Konsole — the ticket's worktree in Rider |
| `OpenWorktree(s id, s ticket)` | `→ b` | The worktree with the ticket's branch in Konsole (the "Worktree" button) |
| `ShowWindow(s id)` | `→ b` | The "Tide Sesje" window, with the coordinator selected (an empty id — no selection); `false` when the window is not installed |
| `Refresh(b force)` | `→` | Refresh now; `force` skips the YouTrack cache |
| `Changed` | signal | The list or one of the boards changed |

**Board** (`Board`): `id` (`claude:<session>` or `codex:<thread>`, as in `/Agents`), `tool`, `sessionId`,
`project`, `repoPath` (the repository's main directory), `cwd`, `branch`, `title` (from the plan, otherwise the title
of the conversation), `sessionTitle`, `label` (the ID from the plan title or the branch, only with a known ticket prefix —
"TOM-20"), `model` ("Opus"), `startedTs`, `lastActivityTs`, `state` and `reason` (the session state from `/Agents`,
`ended` when it is not there), `live`, `canActivate`, `question` (the last unanswered question: `text`, `ts`,
`kind` `question`; when the journal has no question and the Agents area says the coordinator is waiting — e.g. for approval —
its `waitingText` with `kind` `approval`, so that the board says the same as the agents widget; a question without a ticket ID
stays here and does not go to any ticket),
`plan` (`path`, `title`, `tracker`, `trackerUrl`, `trackerProject`, `prefix`, `coordinator`, `session`,
`status`, `updated`, `updatedTs`, `errors`, `goal`, `card`) or `null`, `card` (the Board card from the plan —
`K-12` or empty; also in `Coordinators()`, for PT-5), `tracker` (`kind`, `url`, `project`, `available`,
`error`, `fetchedTs`, `checkedTs`), `counts` (by state, `total` without rejected ones, `done`), `progress`
(`done`, `total`, `text`: "TOM-20 · 8/13"), `spawned`, `running`, `columns`, `tickets`, `runs`,
`warnings` (the check of ticket numbers in the repository, below; also in `Coordinators()`).

**Ticket:** `key` (the comparison key: `TOM-140`, `#22` or `~description` for work without an ID), `id` (for display,
from the plan or the first mention), `title` (what the task is, in Polish — the card description), `done` (what was actually
done), `now` (what is going on now / what is missing, the review in words), `describedBy` (`model`, `model-stale` —
the title and "done" from the previous model description, "now" from rules — or `rules`), `describeNote` (only
in `local` mode after a failed call: "Model lokalny niedostępny — opis bez modelu (…)"; the Sesje window
shows it in the ticket details instead of "opis z reguł" (description from rules)), `officialTitle` and
`officialFrom` (`plan`, `tracker`, `backlog`), `officialNote` (the "Status:" line from the backlog), `taskTitle`
(from the assignment description, e.g. "Implement ticket 05 value chain"), `state` (`todo`, `w-toku`, `czeka`, `review`, `gotowe`, `odrzucone`),
`column`, `word`, `source` (`runs`, `plan`, `tracker`), `derived` (a state from the subagents' work alone),
`planState`, `planNote`, `planAgent`, `inPlan`, `trackerState`, `url` (YouTrack), `branch`, `worktree`,
`note` ("review OK", "gotowe, czeka na review" (done, waiting for review), "rozpoznanie skończone" (reconnaissance finished), "po review: 1 MAJOR — do poprawy" (after review: 1 MAJOR — needs fixing),
"czeka na ciebie: "…"" (waiting for you: "…")), `review` (`blocker`, `major`, `nit`, `ok`, `verdict`, `top`, `text`) or `null`,
`runs`, `agents` (the last three rounds per card: `label`, `role`, `word` — the role in words: implementation,
fixes, review, re-review, reconnaissance —, `state`, `startTs`, `endTs`, `note` "poprawki" (fixes)), `running`,
`question`, `lastActivityTs`, `events` (a timeline in Polish: `ts`, `kind` `launch`/`continue`/`finished`/
`question`, `state`, `title` ("Koordynator zlecił implementację" (The coordinator assigned an implementation), "Implementacja skończona" (Implementation finished),
"Review: 1 poważna uwaga i 2 drobne" (Review: 1 serious remark and 2 minor ones)), `text` (the start of the report — two sentences, up to 300 characters — or "do
poprawy" (needs fixing)), `meta` (a small remark on the assignment: ""Implement ticket 05 value chain" · implementer · Opus ·
tło" (background)), `word`, `durationS`, `quote` — the first BLOCKER/MAJOR from the review, `review`).

**Run** (`runs`): `id` (the Claude Code agentId or the Codex thread id), `ticket`, `role` (`implementer`,
`reviewer`, `helper`), `agentType`, `label`, `description`, `nickname`, `model`, `background`, `state`
(`running`, `completed`, `failed`, `killed`, `interrupted`), `startTs`, `endTs`, `rounds`, `lastAction`,
`branch`, `summary`, `hasResult`, `review`.

**Where the coordinators come from** (`coordination/claude.py`, `codex.py`, read only):

- Claude Code: `<claude>/projects/<directory>/<session>.jsonl` changed in the last 7 days
  (`--coordination-days`) with a `<session>/subagents/` directory. An assignment is a `tool_use` block `Agent`/`Task`
  (`description`, `subagent_type`, `prompt`, `model`, `run_in_background`); the result of the first plan
  arrives in a `tool_result`, and background work ends with a `<task-notification>` block (`task-id` =
  agentId, `status`, `summary`, `result`) in a `queued_command` attachment or a user message —
  the copies in `queue-operation` and `api-request-blob` are skipped. A report handed over through SubagentHandback
  arrives as a separate message with `origin.kind = "peer"` (`handbackReportText`). `SendMessage`
  with `resumedAgentId` starts another round of the subagent, `TaskStop` stops it, an `AskUserQuestion` without
  a result is the coordinator's question. `subagents/agent-<id>.meta.json` supplies the type, description and model, and the subagent's
  journal — the branch, the last action and "Pokaż wynik".
- Codex: a main thread (without `source.subagent`) with at least one `thread_spawn` subagent — grandchildren
  belong to it too; the approval reviewer (`subagent.other = "guardian"`) is noise. A run is the subagent's thread
  (`agent_path` → task name, `agent_nickname`, `agent_role`), a round is its turn
  (`task_started` → `task_complete` with `last_agent_message`), the command comes from `spawn_agent` in the parent,
  a question is a `request_user_input` without a result.
- The first read reads the whole journal (a real coordinator has 50–100 MB, about 0.8 s), parsing JSON only
  in lines that may change something; after that only the appended bytes. What stays in memory are the descriptions, the start of the
  command (2.4 KB — for finding the ID), the subagents' reports (up to 24 KB) and unanswered questions; none of this
  reaches the service journal or the file for the report.

**Tickets** (`coordination/ids.py`, `board.py`): the ID from the subagent's description, then from its branch (when different
from the coordinator's branch), from the nickname, and from the start of the command only with the prefix from the plan (the command quotes
decisions, invariants and requirements — "ADR-0016", "INV-001" — which are not tickets); the prefix from the plan
(`ticket-prefix`, also `tom142`, `la_029`), without a plan — the most common one in descriptions and task names
(`[A-Z]{2,6}-\d+`, without abbreviations like UTF-8, ADR, INV, REQ, NFR, AC) or `#22`. The ID of the coordinator
itself (TOM-20 from the RC branch) is not a ticket unless it is in the plan.
The migration map from the repository (`docs/agents/youtrack-migration/*.csv`) converts old keys to new ones
(TOM-10 → LOG-128, LA-001 → LEGAL-1). Work without an ID is grouped by description without role words, and a review joins
the earlier task whose words it contains ("Review anon key sync change" → "Implement anon key
sync tool").

**Ticket state:** from the subagents' work — a coordinator's question with a ticket ID → `czeka`; a reviewer is working →
`review`; another subagent is working → `w-toku`; the last review without BLOCKER/MAJOR and without a request for changes →
`gotowe`; with them → `w-toku` ("do poprawy" (needs fixing)); fixes finished after a review → `gotowe` ("bez ponownego
review" (without a re-review)); an implementer finished without a review → `review` ("gotowe, czeka na review" (done, waiting for review)) when the coordinator is working
and assigns reviews to other tickets — unless more than 2 h have passed since the work ended and the coordinator in that time
assigned reviews to others (review skipped → `gotowe`, "bez review"); reconnaissance alone (Explore, explorer)
→ `gotowe` ("rozpoznanie skończone" (reconnaissance finished)); an error, a stop or an interruption (the coordinator is not working
and the subagent's journal has been idle for 2 min) → `w-toku` with a reason. The plan wins for `odrzucone`, for tickets
without subagent work, and when its `updated` is newer than the ticket's last event. YouTrack gives the title
(when the plan has none) and the address; its state counts without a plan and work or when the ticket is closed there
(Fixed, Done, Verified… → `gotowe`; Won't fix, Duplicate, Obsolete… → `odrzucone`) and nobody is working on it.
The local backlog `docs/agents/backlog/<ID>.md` (the heading "# LA-040: title", the line "**Status:** …") gives
the title when neither the plan nor YouTrack has it. A coordinator whose directory no longer exists (a removed worktree)
looks for the repository (plan, migration map, backlog) in the directories of its subagents. A Codex thread without a name
gets its title from the user's first message.

**Card description** (`coordination/describe.py`, decision 54): `title`, `done`, `now` in Polish. The model — the same
call as "Na daily" (`summarize.Model`; `claude -p` without tools, `--safe-mode`, section "Na daily" below),
with its own instructions and response schema, the mode from `report.json` (`summary`, `--report-summary`
takes precedence): `claude` — the `summaryModel` model; `local` — the local model from the `describe` role in `models.json`
(never Claude), at most 8 tickets per call and as many facts as the role's context fits (the rest in the next
call), through the model queue at high priority (before the report; section "Local models"), 90 s
per call counted from its start, up to 5 min of waiting in the queue (then rules with an honest reason: "Model
lokalny niedostępny — opis bez modelu (kolejka: czekał 300 s — serwer zajęty: raport poranny, …)" (Local model unavailable — description without a model (queue: waited 300 s — server busy: morning report, …))),
and shutting down the service (`Describer.stop()`) interrupts an ongoing call and removes waiting ones from the queue
at once; `rules` — rules only. When the local model fails, the coordinator's cards
have rules and `describeNote` with the `ModelError` message ("Model lokalny niedostępny — opis bez modelu
(serwer nie odpowiada, 127.0.0.1:11434)"), the same text goes to the service journal (once, not on
every attempt), and after 15 min the call is repeated; a successful call clears the information. The hash of the facts
contains the model identity (as in "Na daily"). The previous description as `model-stale` we show within Claude
even after `summaryModel` changes (as before PT-15), and for a local model — only from the same endpoint, address,
model and context; after switching `claude` ↔ `local` the cards do not show the other one's description. Input: `{"coordinator": {"project", "title"}, "tickets": [{"key",
"id", "state", "status" (the state from rules), "officialTitle", "trackerStatus", "trackerDescription", "brief"
(up to 400 characters), "planNote", "tasks" (assignment descriptions), "reports" ([{"role", "said"}], up to 300 characters, the last
6), "review", "question"}]}` after `strip_secrets`; the answer `{"tickets": [{"key", "title", "done", "now"}]}`
is validated (only known keys, plain strings, no Markdown, title 110, the rest 260 characters). When:
in a separate worker thread, one call at a time, batched per coordinator (up to 20 tickets), only for
tickets whose facts (a SHA-256 hash with the model and the instructions version) have no description and have not changed for 2 min;
at most once per 5 min per coordinator, after an error a 15 min pause, coordinators with no activity for
two days — rules only. A ticket the model did not describe gets rules (without retrying). The fallback
from rules: the title from the plan/YouTrack/backlog, otherwise the first Polish sentence of the command without boilerplate
("Jesteś wykonawcą…" (You are the executor…), "Rola: …" (Role: …), "Model: …", "Implementujesz zadanie 05 funkcji "X" …: reszta" (You are implementing task 05 of feature "X" …: the rest) → "X:
reszta"), otherwise the assignment description; `done` — the start of the last implementation report; `now` — the state, the review
in words, the question, a sentence about failing tests. `--print-coordination` shows descriptions from the cache
or rules, and never calls a model.

**Review** (`coordination/review.py`): a reviewer is a subagent with "review" in the type or description (but not
"review fix round"). From the report: explicit numbers ("1 MAJOR, 2 NIT", "BLOCKER: brak" (none), "nie ma BLOCKER-ów ani
MAJOR-ów" (there are no BLOCKERs or MAJORs)), tagged findings (`MAJOR-1`, `NIT-2`, `MAJOR B`), lines and points under level headings;
sentences about closed findings from the previous round do not count. The verdict from the "Werdykt/Verdict" line
(`REQUEST CHANGES`, `CHANGES REQUIRED` or `APPROVE`, `ACCEPT`). This is a heuristic checked against the shapes of
real reports; the state can always be overridden by the plan.

**YouTrack** (`coordination/youtrack.py`): only for a plan with `tracker: youtrack`. The coordination does
GET only (writing state is only the Board's synchronization, PT-7). One query per project: `GET {url}/api/issues?query=project: {key}&fields=idReadable,summary,description,customFields(name,value(name))`
(of the description we keep 1200 characters; the Board shows 600, the morning report takes 400)
with the header `Authorization: Bearer <token>`, no proxy, with a 4 s limit, in a worker thread. The result is
kept in memory for 5 min; on an error the last good data stays, and the next attempt is also only after 5 min (the server
is on the local network — when it is not there, the Board works without YouTrack titles, with a message in the button's
tooltip). The token (PT-7: the settings file is optional), in order, the first place that has it: `tokenFile`
from `youtrack.json` (`tokenVar` empty: the whole file, given: a variable from an `.env` file), `~/.config/youtrack/token`
(the YouTrack MCP server's token), `~/.config/logviewer/youtrack-mcp-token` (LogViewer's former location);
without a token the message lists the paths checked (never the content). Read on every request, in memory only — it does not reach the journal, errors, D-Bus
replies or files. The token goes only to the server from the settings (`url`, by default
`http://10.20.1.159:8111`); a plan with a different `tracker-url` gets a message instead of a query.
`--no-youtrack` turns the queries off.

```json
{"url": "http://10.20.1.159:8111", "tokenFile": "~/.config/youtrack/token", "tokenVar": ""}
```

The same file carries the Board synchronization settings (described in "Board" — "Synchronization with YouTrack"):
`sync`, `states`, `stateField`, `projects`, `importQueries`, `importCheckMinutes`.

**Write for the card flow** (PT-72, part 1; a client not yet connected to plan approval — that is part 2). Three operations
in `coordination/youtrack.py`, with the same token, no proxy and the same 4 s limit as the rest:
`create_issue` (`POST /api/issues`), `link_issues` (`POST /api/commands`, e.g. `relates to DEMO-1`) and `add_comment`
(`POST /api/issues/{id}/comments`). Creation is idempotent: the description ends with the marker `[//]: # (tide-ref card=<card> n=<number>)`
(a Markdown link-definition line — invisible once rendered, resistant to the description being rewritten, because it is a separate line
searched for across the whole description with whitespace tolerance); before creating, the client looks for an issue of the project with this marker
(a full-text query plus an exact check of the card and number on its side) and returns the existing one
(`created: false`). A comment with a result note carries `[//]: # (tide-note card=… ticket=… event=…)` and before adding
checks the issue's comments — exactly one per (card, ticket, event). An error has `transient` (server
unreachable, 5xx, 401/403/408/429 — retry) or `permanent` (400/404/422 from validation, a bad value checked before
sending — do not retry). The card, ticket and event names in the marker must match `[A-Za-z0-9._-]`.

**Ticket number check** (`coordination/numbering.py`, decision 56; rules: the plan standard, "Ticket
numbers: a single source"). Plans from all places in the repository (`coordination/repoplans.py`): the main
directory and every worktree (`git worktree list`, also uncommitted files) and every local branch
(`git for-each-ref`, `ls-tree` only for new commits, `cat-file --batch` only for new blobs;
the repository result is kept in memory for 60 s), the declaration from CLAUDE.md/AGENTS.md (the section "Plan pracy agentów (Tide)" (Agent work plan (Tide)):
prefix, tracker, project), number reservations and — when the repository has local plans — the YouTrack project
keys (`GET /api/admin/projects?fields=shortName`, once an hour, after an error after 10 min; without YouTrack
this point is skipped). A warning: `kind`, `severity` (`hard`, `soft`), `ticket`, `prefix`, `plans`
(plan names), `text` (in Polish, the window shows it as plain text). Kinds: `ticket-title` — the same
key with different titles in two different plans, also finished ones (copies of one plan in several worktrees
and branches, by file name or plan title, are one plan; a pair of `youtrack` plans does not count);
`local-prefix-tracker` — a local prefix is a project key in YouTrack; `prefix-alias` — a `youtrack`
plan with `ticket-prefix` ≠ `tracker-project` without a migration map; `reserved-elsewhere` — a number
reserved for a different title; `numbering-restart` (mild) — an unfinished local plan whose new
numbers start no higher than the largest number of earlier plans. The window shows a bar above the
columns (the pastel `butter`), the morning report — an item "Konflikt numerów: …" (Number conflict: …) in the project's "Uwaga"
(always from rules, also with a model description). From the command line: `make check-plans` (`tools/check_plans.py
[workspace]`, exit code 1 on a hard collision).

**Number reservation** (`coordination/reservations.py`, `make next-id REPO=… [PREFIX=] [COUNT=] [TITLE=]
[DRY_RUN=1]`, `tools/next_id.py`): under `flock` on `$XDG_STATE_HOME/tide/ticket-ids.json` (0600)
the largest number from the registry (the repository's main directory + prefix) and from all plans, the next N
numbers written with a title and a time. An empty registry is seeded from the plans; a YouTrack prefix — a refusal
("numer nadaje YouTrack — załóż ticket tam" (the number is assigned by YouTrack — create the ticket there)); `--dry-run` writes nothing.
Approving a card plan with local numbering (`ApproveCardPlan`, K-102) assigns numbers to all `#n` rows
in one combined reservation (`reserve_many`): one lock, one read of the repository's plans, one write of the registry.
The entries have the shape of a single reservation with a key (`<card>:plan:<seq>:#n`), so a retry and recovery of the intent
after a restart return the same numbers. More than 50 new numbers in one call is an error without a write (without
splitting); the card journal still has the intent and the `reserve` result for each row.

**Refresh** (`coordination_monitor.py`): the first read 1.5 s after start, then every 20 s, after a change
in `/Agents` (at most every 4 s) and on demand. The work runs in one worker thread, D-Bus answers from ready
data. After a change: the `Changed` signal, the `coordination` field on the coordinator's session in `/Agents`
(`label`, `done`, `total`, `text`, `working`, `waiting`, `running`) and the file for the morning report
(coordinators with the plan goal, tickets with state, title, note, address and branch, the review result
in numbers, the first line of the work result up to 220 characters, an unanswered question up to 300, notes from the plan
up to 300, the start of the YouTrack description up to 400 and the start of the first subagent's command up to 300 —
encrypted Codex commands are skipped —, the card description `done` and `now` up to 300 characters — "Na daily" is built from them;
without full commands and subagent reports). The card descriptions are added by the same thread from the cache or rules, and when
the model has to be asked (decision 54), a separate worker thread does it and, after the answer, requests a refresh.

**The "Sesje" view of the Tide window** (`apps/sessions`, window `apps/tide`, `make install-tide-window`): `ShowWindow()`
runs `/usr/bin/python3 -s $XDG_DATA_HOME/tide/tide-window/tide.py --view sessions [--select id]` (or
`$TIDE_WINDOW`) through `systemd-run --user --scope`; without the Tide window or with `$TIDE_SESSIONS_WINDOW` — the former
launcher `tide/sessions-window/tide-sessions.py [--select id]`. The window registers `org.tide.Window` (object
`/Window`, `Show(s view, s item) → b`), so a subsequent launch only shows it on the Sesje view
with the coordinator selected; the former `org.tide.SessionsWindow` `Raise(s id)` also works.
A card: the real ID, a human-readable title, a "what was done" or "what now" line, running subagents with a role
in Polish and the time, the state in words; details: "Co to jest" (What it is), "Co zrobiono" (What was done), "Teraz / review" (Now / review) and a timeline.
Texts from the data are always plain text (`Text.PlainText`).

```bash
qdbus6 org.tide.Daemon /Coordination org.tide.Daemon.Coordination.Coordinators
/usr/bin/python3 daemon/tide-daemon.py --print-coordination   # the window data as JSON, read only
make preview-sessions-window SANITIZE=1                        # a window screenshot without the work content: build/sessions-window.png
```

## Board

A personal backlog across all projects: a card is a "thing to do" (a feature, a fix, an idea),
not a ticket. The data is in a local git repository, which the service creates on the first valid
write and commits after every change, so history and undo come for free. No remote — the service never
pushes or adds a remote repository. The Board view in the Tide window (`apps/board`) and the
`org.tide.board` tile (`widgets/board`) read `Snapshot()`, the "Praca" (Work) tile `org.tide.work` (`widgets/work`, PT-40) reads
`Work()`; everyone writes only through the methods below;
the columns "W toku", "Czeka na mnie" and "Do sprawdzenia" result from the agents' real work (below, "Card state
from agent work", PT-5), launching an agent from a card (PT-4) is described by "Launching from a card" below, and the state
of YouTrack issues and importing issues as cards — "Synchronization with YouTrack" (PT-7). Plan: `docs/agents/plans/2026-10-27-board-and-docker.md`.

| Element | Value |
| --- | --- |
| Object | `/Board` |
| Interface | `org.tide.Daemon.Board` |
| Repository | `--board-dir`, otherwise `$TIDE_BOARD_DIR`, otherwise `~/DevWorkspace/tide-board` |

| Member | Signature | Description |
| --- | --- | --- |
| `Snapshot()` | `→ s` | JSON of the whole Board (described below); read only — creates nothing before the first write |
| `Work()` | `→ s` | the "Praca" list (PT-40, below "The Work list"): `{exists, items, counts}` — active cards and live agent sessions without a card, from the same recalculation as the card columns; read only |
| `CreateCard(s request)` | `→ s` | a new card from JSON `{title, description, project \| repo, projects, tags, column, note, youtrack, spec, session, branch, check, startRef}` (`startRef`: a full ref `refs/heads/…`/`refs/remotes/…`, below in the card fields) → `{ok, id, commitError}` |
| `UpdateCard(s id, s request)` | `→ s` | a change of the given fields (the same as above without `column`) → `{ok, id, changed, commitError}` |
| `MoveCard(s id, s column, i order)` | `→ s` | a manual column (`backlog`, `todo`, `done`, `archived`) and a position in it from 0; `-1` or out of range — to the end → `{ok, id, changed, commitError}`. Every call is an explicit move: it stamps `moved` even in the same column and at the same place (then `changed: true` and a commit "K-12: move to todo") — a card shown in "Do sprawdzenia" usually has `todo` stored, and "needs fixing" to "Do zrobienia" (To do) must be visible |
| `UpsertProject(s request)` | `→ s` | a project `{slug, name, repo, tags, aiDocs, tool, entry, mode, description, youtrack, youtrackSync, reviewIndependence, reviewIndependenceClasses, assignmentRules, tests}` (`reviewIndependence`: `free|other-model|other-family`, any other value — an error; described in the project file section; `tests`: the test contract, below "Project test contract") or a separate write of the shared rules `{globalAssignmentRules}` (`entry`: `planning` or `spec`; `mode: full|light` alone without `entry` means Planning, and `mode: spec` — the `spec` entry, the same in UpdateCard, CreateCard, RenderLaunch and RenderFlow): an existing one (by `slug`, `repo` or the same name) — a change of the given fields, otherwise a new one → `{ok, slug, changed, commitError}`; with the `tests` field it is also an approval of the contract (below "Approving the test contract") and the reply additionally has `testsApproved` and `approvalError`; a sender from a `tide-step-*` step unit or an undetermined one → `{ok: false, error: "forbidden"}` without changes (below "Protecting approval from an agent step") |
| `SuggestProjectTests(s request)` | `→ s` | a test contract suggestion `{slug}` or `{repo}` → `{ok, changed, full, source, facts, message}`, for `slug` also `effectiveSource` (`profile`/`detected`/`rules`), `unapproved`, `testsApproved`; read only (below "Suggestion and the source of the contract") |
| `UpsertTag(s request)` | `→ s` | a tag `{name, color, description, newName}`; `newName` renames also in cards and projects (also a tag without a file — then its file is created) → `{ok, name, changed, commitError}` |
| `RenderFlow(s id, s mode)` | `→ s` | the command for a card (an empty `mode` — the project's default entry and mode; `spec` is the old notation of the Tylko specyfikacja (Specification only) entry) → `{ok, text, entry, mode, tool, branch, check, docsDir, aiDocs, repo, repoMissing, project, projectName, specFile, template}`; the `docsDir` directory is created on the Board at once |
| `RenderLaunch(s id, s request)` | `→ s` | the same with the choice from the "Co ma zrobić agent?" (What should the agent do?) window: `{entry, mode, tool, branch, check, preferredProvider}` (everything optional; `entry`: `planning` or `spec` — Specification only, the project's default entry; `mode`: `full` or `light`, applies only to Planning and is ignored with `spec`; `mode: spec` without `entry` is the old request for the Specification only entry; `check`: `auto` — the default, or `manual`); the command from every entry and mode is the same as before PT-77; the reply has `entry` and `mode` (empty with `spec`); the reply also has `projects` (additional projects), `extraRepos` (their existing repositories) and `addDirs` (directories for `/Tasks Start`: the documents on the Board with `ai-docs: tide` and `extraRepos`) |
| `Badge()` | `→ s` | the badge of the Board view in the Tide window: JSON `{text, tone}` — "● N" (`waiting`) when cards are waiting for you, otherwise the number of cards in play without Gotowe (Done) and Archiwum (Archive) (`muted`); zero — an empty text (badge hidden). Read only, creates nothing; the same rule as `badge()` in `apps/board/backend.py` |
| `ShowWindow(s id)` | `→ b` | the Tide window on the Board view (`tide.py --view board`), with a card ID (`K-12`, case-insensitive) straight on it (`--select K-12`), with `launch:K-12` — on it with the "Co ma zrobić agent?" window (`--select launch:K-12`, "Uruchom" (Run) in the tile popup); a string that is not a card ID is ignored (just the window); `false` when there is no window — the Board tile in the dock |
| `CardDocs(s id)` | `→ s` | the card's plan and specification for the card panel (below, "Plan and specification on the card"): `{ok, card, plan, spec, others, warnings}`; read only, the reply after reading the files in a worker thread |
| `YouTrackIssues(b refresh)` | `→ s` | the user's issues for import (PT-7): `{ok, issues: [{id, summary, project, state, updated, url, card, boardProject, boardProjectName, new}], error}`; the reply after fetching in a worker thread (`refresh: false` — the last list from memory); the issues shown count as seen |
| `ImportYouTrack(s request)` | `→ s` | cards in the Backlog from the list `{issues: ["LOG-1", …]}` (at most 200, only from the last `YouTrackIssues` list) → `{ok, created: [{issue, card, project}], skipped: [{issue, card, reason}], commitError}`; an issue with a card — skipped |
| `ResolveYouTrack(s id, s choice)` | `→ s` | a conflict with a manual change in YouTrack: a card (`K-16`) or an issue key, `choice`: `youtrack` (Tide accepts the state from YouTrack) or `tide` (write the Tide state despite the change) → `{ok, issue, choice}`; without a conflict — `not-found` |
| `ListCardBranches(s id, s request)` | `→ s` | local bases and locally known remote ones, or local-only merge targets: `{purpose: "base"\|"merge", text?: "", limit?: 50}`; limit 1–100, a filter over the whole set before the limit; the result `{ok, branches, mainRef, recentBases, recentTargets, warnings, hasMore}`; no cursor, no network and no arbitrary repository path from the client |
| `SuggestCardBase(s id, s request)` | `→ s` | base suggestions: `{text, revision: int, scope: "text"\|"files"\|"links"}`; the result `{ok, revision, card, project, repo, candidates, warnings}`; text and file history locally, PR/MR links through the CLI; files/links only on request |
| `StartCard(s id, s request)` | `→ s` | an assignment in the card flow: the "Od razu" (Immediate) entry `{entry: "now", executor, reviewer?, preferredProvider?, images?, baseRef?, baseCommit?, cardBranch?}` or Planning `{entry: "planning", mode: "light"\|"full", executor, preferredProvider?, images?, check?, projects?}` (`projects` — the assignment's projects, PT-115), or a plan proposal `{entry: "plan", planPath}`; the result contains `ok` and the assignment data or `error`/`errors`; a successful start of a card from the Backlog moves it to "Do zrobienia" (to the end, like `MoveCard`, under the same lock as the assignment write; the reply then has `column: "todo"`) — also from `tide go` and coordinator scripts, and a card in another column stays in place; validation and flow described below |
| `ResolveCardReview(s id, s choice)` | `→ s` | an answer to the question after the third or later failed review round: `retry` starts further fixes and review, `reject` rejects the ticket without a merge, keeping the branch and worktree for inspection; the decision goes to the journal. `choice` is a bare string (`retry`, `reject`, `accept`) or JSON `{"choice", "ticket", "round"?}`. A bare string: with one open review question it works as before; with several it returns `{ok: false, error: "ambiguous-review", reviews: [{ticket, round}, …]}` and writes nothing. With `ticket` it resolves that ticket's question (the latest round or the given `round`); no such question or an outdated round → `no-review-question`, no write. Invalid JSON, a wrong field type or an unknown `choice` → `bad-choice`, no write. A success reply contains the `ticket` and `round` of the resolved question. The Board and Sessions windows send the ticket and round of the question shown |
| `SetCardPaused(s id, b paused)` | `→ s` | pauses or resumes new steps of the card; ongoing ones finish normally; the state in the journal as `card.paused` / `card.resumed` |
| `EditCardPlan(s id, s request)` | `→ s` | a change of an untouched ticket (`action: edit`), a proposal of one new ticket (`action: append`), sending back with remarks (`action: return`) or entering the planner in Konsole (`action: enter`) |
| `ApproveCardPlan(s id)` | `→ s` | approves a valid card plan proposal: reserves local IDs, writes the `#n → ID` map to the journal and launches tickets after blockers |
| `ApproveCardEnvironment(s id, s yaml, b onlyPlan)` | `→ s` | the card environment consent pack (PT-172, below): the service validates `yaml` (the card profile) and the consent to red items before any write; `onlyPlan` approves only the plan and sets `boxWait: environment` (PT-290: only with `box.enabled: true`; with the box disabled, only the decision is written, without a build) |
| `BoxStatus(s slug)` | `→ s` | the project box state, the recipe hash, "filtering NOTHING: yes/no" and the `boxWait` of the project's cards (PT-172, below) |
| `CardBranchDiff(s id)` / `CardBranchDiff(s id, s targetRef)` | `→ s` | after close-out, a diff against the current ref of the stored base or an explicit full local/known remote ref; unknown requires an explicit target. Without writing the base and without consent to merge |
| `PreviewCardMerge(s id, s request)` | `→ s` | a read of the merge tree and local collisions; `{}` chooses the local base, the unambiguous local upstream of a remote base, or the main one for an unknown base; an optional `targetRef` is a full `refs/heads/…`, also a new one from a known remote base; returns `previewToken` and the resource plan, without consent to execute |
| `MergeCard(s id, s request)` | `→ s` | an explicit confirmation of the current preview (`targetRef`, `previewToken`); a local target, if needed the ref/worktree created after consent; uses the durable PT-212 operation |
| `MergeCardToMain(s id)` | `→ s` | on the user's click starts a durable merge of the closed-out card into the main branch and moves it to Gotowe; refuses on local collisions, a retry resumes the same intent |
| `Changed` | signal without arguments | the Board snapshot changed (a write through D-Bus, an external change, card state from agent work) — fetch `Snapshot()` again |
| `WorkChanged` | signal without arguments | the "Praca" list changed — fetch `Work()` again. At once on a change of groups, questions and numbers; when only the agent's last action (`note`) changes, at most every 10 s (`WORK_VOLATILE_MS`) |

`PreviewCardMerge` (PT-199) replies asynchronously. The result contains `sourceRef`, `sourceCommit`,
`targetRef`, `targetCommit` (full refs and pinned SHAs), `sourceBranch`, `targetBranch`, `targetWorktree`
and `targetWorktreeExists`. The full lists `commits: [{sha, subject}]`,
`files: [{status, oldPath, newPath}]`, `conflicts: [path]` have the counters `commitCount`, `fileCount`,
`conflictCount`. The commits are exactly `targetCommit..sourceCommit`; the files are the tree difference between the target
and the `resultTree` from `merge-tree`, with rename detection and both paths. Closing the view records
no decision and no column change.

A conflict is a valid read (`ok: true`, `canMerge: false`, `reason: conflict`), and likewise the refusals
`unrelated-history`, `git-capability`, `git-error`, `no-sandbox` have their own `reason` and `message`.
An invalid request or a missing branch gives the standard `ok: false` error. PT-210 extends `canMerge`
with close-out, the cleanliness of the card checkout, no active steps (also Konsole and commands outside the sandbox),
unfinished actions, plan changes and a base correction after close-out. The target is only a local ref;
card branches and the branches/worktrees of other cards' active steps are excluded. The default target and an explicit `targetRef`
use the same qualification. A remote ref, a SHA or an abbreviated name are not targets of this method.

`localChanges` and `localCollisions` contain separate lists `staged`, `unstaged`, `untracked`.
A change entry is `{status, paths}` with the raw porcelain code and both rename paths; a collision entry
adds `mergePaths`. `localCollisionCount` counts entries across all three collision lists.
A collision covers deletions and the file–directory relation in both directions, with a path component boundary.
`blockedReasons: [{reason, message}]` shows all restrictions, and `reason`/`message` the first of them.
A tree conflict (`conflict`) and a local work collision (`local-collision`) stay separate, also when
they occur at the same time. Unrelated staged, unstaged and untracked files do not block the preview (K-43, PT-207).
A file with both staged and unstaged changes keeps both levels of work after the merge.
A target without a checkout has a plan for its own worktree; the method creates nothing. A registered, unavailable
checkout has `no-target-worktree`. A checkout read error blocks
the qualification instead of pretending to be a clean state. With an unprepared tree `localCollisions` may stay `unassessed`.

`previewToken` is an HMAC over the card, the assignment, `baseVersion` (also a stable 0 for legacy/unknown),
the pinned SHAs and refs, the worktrees read, HEAD, the raw and logical index, the content of locally changed
files and the flow locks. A missing checkout has a separate representation. The secret lives only in the engine:
after a restart a new preview has a different token. The token is neither a durable consent nor a started operation.
`MergeCard` prepares the preview again and checks the token is current under the repository lock;
the lock also covers preparing and writing the PT-212 intent. The journal lock protects the assignment state,
the close-out and the base version from other service writes during that time.
The read of checkouts and refs is repeated after the tree is prepared; a change during the read gives
`state-changed`, `canMerge: false` and no token. A failed checkout read also issues no token.
The historical field `readyToMerge: false` stays for compatibility; merely receiving a preview is never a consent.
The window uses `canMerge` and `previewToken` to enable a separate "Scal do <full name>" (Merge into <full name>) button.
The preview creates no worktree and writes no index, HEAD, refs or files;
it allows the objects of the trial tree. Merge drivers also run in the StepGit sandbox with write access
to `objects` only. The existing explicit `MergeCardToMain` keeps its signature and behavior.
A custom merge driver that needs `.merge_file_*` files in the working directory is rejected in the preview
as `git-error`: the read-only mount already blocks preparing those files, before the driver
starts. We do not turn this tool refusal into a conflict and do not open a write in the worktree.

PT-204: `MergeCard` requires an object `{targetRef, previewToken}`. A wrong or outdated token (a change of refs,
checkouts, the index, local content, the assignment or `baseVersion`) gives `stale-preview` without starting an operation.
After a restart the token does not allow starting a new operation. A current preview with a block gives its `reason`.
PT-200 allows an explicitly chosen local target through the same operation. PT-206 adds the preparation
of a missing local ref and checkout after consent, described below.

A success returns `{ok, id, targetRef, targetBranch, sourceCommit, targetCommitBefore, resultCommit,
baseVersion, operationId, already}` from the existing operation. `already` means the source is already contained in the target.
An optional `operationId` points only to a started operation of the same assignment, ref and token;
it is not a consent to start a new one. A retry of the original request also finds the stored ID
by the assigned token, so a lost reply and a restart do not cause a second merge. A resume
uses the PT-212 recovery checks. Both APIs end the card with a `card.merged` event with the actual target (PT-205);
the old `MergeCardToMain(id)` still chooses the main one and returns `main`, also when it has to open its own checkout.

On the Board both the panel and the full card open the preview after "Scal do <full name>". The confirmation
shows the full ref and SHA of the source and the target. Closing, Escape, a refresh and `check: auto` do not call
`MergeCard`. After an error the old button stays disabled; a refresh requires another confirmation.

PT-200: with a known local base the button shows "Scal do <full name of the base>". The "Scal…" (Merge…) menu
uses `ListCardBranches(purpose=merge)`: only full local refs, without duplicates, with the base
and the named main one at the start of matching results. The search covers the whole set before the limit
of 50 (max. 100); `hasMore` means the search must be narrowed. The list shares with the preview the check
of the card branch and of the protected refs/worktrees of other cards (`selectable: false`, `reason`).
The content collision assessment stays in the preview.
PT-214: `recentTargets` returns up to 10 different existing local full refs, from the latest
successful `card.merged` or an old `card.main-merged` with a known `main`. An unknown target is not guessed.
The list is rebuilt from the Board journals after a restart, shared by the worktrees of the actual repository
and separate from other repositories; no new store. A deleted ref disappears from the shortcuts, while the history
stays. The shortcuts have the current SHA and the same `selectable` and `reason` as the search results;
they do not depend on the filter and the result limit. A history error gives a warning and leaves manual search.
The menu shows the base, the named main one, the recent targets and a search, without repeating refs. Clicking
a shortcut only opens the preview, changes neither the base nor the default target and gives no consent to merge.
For an unknown base the main one is a suggestion that requires a choice. The choice changes only the preview context,
without writing the base or a consent. A change of the card's project or
repository closes the choice and the preview and discards their late replies.

PT-206: the target list additionally has `defaultTargetRef`, `trackingTargets` and `createTarget` (an object or null).
For a remote base only one local branch with the exact full upstream of that base is the default;
a match of the SHA or of a short name is not enough. Several candidates require an explicit choice (`target-required`
on a preview without a target). With no tracking target the menu suggests "Utwórz lokalną <full name>
od <remote/name>" (Create local <full name> from <remote/name>). A taken name is never overwritten: the menu lets you type another one or explicitly choose
an existing local target. The new name arrives as a full `targetRef`; the start point comes only from the known
remote base of this assignment, never from an arbitrary ref supplied by the client.

The preview's `targetPreparation` describes `createRef`, `createWorktree`, `path`, `startCommit`,
`remoteRef`, `remoteCommit`, the availability of the name and the directory. The token covers the whole plan, the nonexistence of the ref
and the checkout, and the remote SHA. A remote move, a taken name/directory or a new checkout after the preview gives
`stale-preview` before the intent is written and anything is created. An existing checkout takes precedence; a foreign directory
is not switched. A missing worktree has the name `merge-target-<hash>` independent of the characters of the ref.
The preview, the list and cancellation create no resources; there is no fetch, push, change of refs/remotes or of the remote configuration.

The full PT-212 intent is created before the resources. The additional checkpoints are `target-ref-created`,
`target-directory` (device/inode) and `target-worktree-created`. An atomic refs transaction creates the target
and the ownership proof `refs/tide/merge-targets/<operationId>`; this proof stays with the stored operation.
During preparation the worktree has a lock described by the operation ID. The checkout of the initial tree runs
in StepGit; after git finishes, its own lock is released. Recovery recognizes the resources also after a crash
before the ref/worktree checkpoint and continues the same result without a second merge. A foreign ref (even with a matching SHA),
a changed inode, checkout or commit give waiting without overwriting. A crash between `mkdir` and persisting the
inode also waits safely. The resources are not removed automatically when the intent is abandoned.
`card.merged` points to the actual local ref; the projections and the recent targets use this result.

Every write method replies at once (a few small files and one commit). The replies have the same shape
as in `/Tasks`: a success is `{"ok": true, …}`, an error — `{"ok": false, "error": "<code>", "message": "…"}`
with a message in Polish for a human, e.g. "do kolumny "progress" nie przenosi się ręcznie" (a card is not moved manually to the "progress" column), "nie ma
projektu "app5"" (there is no project "app5"). An unknown field in a request is an error, not a silent mistake.

| Code | When |
| --- | --- |
| `bad-request` | bad data: an unknown field, an empty title, a bad column, color, mode, path, invalid JSON |
| `not-found` | no such card, project or tag (to rename) |
| `exists` | the repository already has the project, a tag with the new name already exists |
| `broken-file` | the card, project or tag file is in `errors` (it cannot be read) — fix it by hand |
| `git-busy` | a revert, merge, cherry-pick or rebase with conflicts is in progress in the Board repository |
| `not-a-board` | the Board directory exists and is not a Board (we do not touch foreign files) |
| `git-error`, `io-error`, `internal` | `git init` or the disk failed; a service error (details in the journal) |

Validation first, then the write: a request with an error (also on an empty directory) leaves no file,
directory or commit — the Board is created only at the first valid write, and a project
created together with a card — only when the whole card is valid. After the files are written the file is the source of
truth: if the commit itself fails (e.g. a busy index), the reply is still `ok: true` with a description
in `commitError`, and a commit with the same message waits and goes out at the next write or
synchronization (snapshot: `git.pending`, an entry in `errors`). So retrying the request is not needed
and creates no duplicate. The queue lives only in the service's memory: after a restart these changes go into the
commit "sync external changes", like an entry that fails for a permanent reason (e.g. the file is gone) —
the queue is stopped only by a busy index or a hung git.

**Repository layout.** All files are Markdown with flat frontmatter (the same rules as
in plans: `key: value` pairs, no YAML), so they can be read and fixed by hand.

```
tide-board/
  board.md                   the `tide-board: 1` marker and the `next-card` counter
  .gitignore                 temporary editor and write files
  projects/<slug>.md         project
  tags/<slug>.md             tag with a color
  cards/K-<n>.md             card
  flows/full.md, light.md, spec.md     command templates (full, light — Planning; spec — Specification only)
  flows/<project>/<key>.md  an override of a template for one project (optional)
  docs/<project>/            plans and specifications of the agents of projects with `ai-docs: tide`
```

A card — `cards/K-12.md`, the content under the frontmatter is the description:

```markdown
---
id: K-12
title: "Hotfix: unikalne wyszukiwanie fraz"
project: logviewer
tags: pilne
column: todo
order: 20
note: bez agenta, robię sam
created: 2026-10-27T14:00:00+01:00
updated: 2026-10-27T14:05:12+01:00
moved: 2026-10-27T14:00:00+01:00
youtrack: TOM-142
spec:
session:
branch:
check: auto
---

Wyszukiwanie zwraca tę samą frazę kilka razy…
```

| Key | Meaning |
| --- | --- |
| `id` | `K-<number>`; the file name is authoritative (a mismatch is an error in the snapshot). A number never comes back: the next = the max of existing files + 1, `next-card` from `board.md` and the marker outside the history `.git/tide-next-card`, which survives a `git revert` of the commit that created the card (`k-N` branches link a card with a coordinator). When the marker is missing (e.g. a copy of the repository), the first card creation rebuilds it once from the highest number ever added in git history; the snapshot does not read history |
| `title` | one line, up to 200 characters |
| `project` | the project slug or empty (a card without a project) |
| `tags` | the card's own tags, comma-separated (`[a, b]` also works); in the snapshot `resolvedTags` = the project's tags ∪ its own. Block YAML lists (`tags:` and below `  - ui`) are not supported — they give an entry in `errors`, and a tag write replaces such a list with one line |
| `column` | manual columns only: `backlog`, `todo`, `done`, `archived` |
| `order` | the order in the column (smaller is higher); a new card goes to the top of the column. `MoveCard` gives the card the midpoint between its neighbors (one file changes), and renumbers the whole column by 10 only when there is no room between the neighbors |
| `note` | a short note visible on the card (e.g. "bez agenta, robię sam" (no agent, I'm doing it myself)) |
| `created`, `updated` | ISO 8601 with local time; `updated` changes on every card write |
| `moved` | the last explicit move (ISO 8601): set only by creating the card and by every `MoveCard` (also within a column); editing fields does not. A card without the key (from before PT-5) counts from `created`. From it and from `moved-from` the state from agent work recognizes "needs fixing" |
| `column-moved` | the last real column change through `MoveCard` (PT-7, ISO 8601): a target column different from the displayed one; a reorder within a column does not stamp it. In the file only after such a move; in the snapshot `columnMoved` and `columnMovedTs` |
| `moved-from` | the column in which the card was displayed at the last `MoveCard` (PT-29): `boardColumn` from the service snapshot (from agent work, e.g. `review`, although the file has `todo`), without it — the stored column. "Needs fixing" only after a move from `review` or `done`; a card created for work that is already finished (or linked to it through `session`) does not "return manually". A reorder in the column where the card is visible (e.g. "needs fixing" in Do zrobienia) keeps the existing `moved-from`. Cards moved before PT-29 have no key — the rule does not apply to them |
| `youtrack`, `spec` | `youtrack` — the issue key in YouTrack (PT-7): a write accepts a key or an issue address (`…/issue/legal-35` → `LEGAL-35`), any other text — `bad-request`; the card state goes to the issue when the YouTrack project has synchronization (below); `spec` — the path of a ready specification from the "tylko spec" (spec only) mode |
| `session` | the session or coordinator that runs the card — an id as in `/Agents` and `/Coordination` (`claude:<id>`, `codex:<id>`, a bare `<id>` also works); the first source of the card state |
| `branch` | the work branch in the project repository; replaces the branch suggestion in `RenderFlow`, links the card with work on this branch and is the branch to merge |
| `check` | the check after the agent's finished work: `auto` (the default — tests and the AI review are enough) or `manual` (the user's manual test: the card waits in Do sprawdzenia until you move it to Gotowe). A bad value in the file — an error and `auto`; a missing key — `auto`. It is set, among others, by the "Test ręczny po zakończeniu" (Manual test after completion) switch when launching an agent (PT-4) |
| `entry`, `mode`, `tool`, `worktree`, `launched` | the last agent launch from the card: the entry (`planning`, `spec`), the mode (`full`, `light`; empty with `spec`), the tool, the worktree directory (absolute or empty — work in the project directory) and the moment (ISO 8601); in the file only after a launch. A card with the old `mode: spec` is the `spec` entry (read, the file untouched); a request with `mode: spec` writes `entry: spec` |
| `max-tickets` | the limit of tickets in progress per card (PT-375): a number 1–6; a missing key — the flow setting `ticketsPerCard`. Write: `UpdateCard {maxTickets}` (an empty value removes the key), snapshot: `maxTickets`. A bad value in the file — the default limit and an entry in `errors` |
| `priority` | the card priority (PT-377): `high`, `normal` or `low`; a missing key — `normal`. It sets the order of steps waiting for a slot at the provider (section "Order of steps waiting for a slot at the provider (PT-377)"). Write: `UpdateCard {priority}` and `CreateCard {priority}` (only these three values, another → `{ok: false}` without changes; `normal` removes the key from the file), snapshot: `priority`. A bad value in the file — `normal` and an entry in `errors` ("niepoprawny priorytet" (invalid priority)), without a card read error. The window: "Priorytet" (Priority) in the full card settings (Wysoki / Normalny / Niski (High / Normal / Low)) |
| `startRef` | an optional "start from…" hint (PT-392), in the file `start-ref`: a full `refs/heads/…` or `refs/remotes/…`, checked by the same syntax function as the card base (`FlowGit.ref_syntax_error`: the prefix and `git check-ref-format`; no guessing of names, an abbreviation, `..`, a space, `-x` and other refs are a `bad-request` error without writing the card file). The service does not check whether the ref exists. `CreateCard`/`UpdateCard` accept `startRef`, an empty string clears the field, and the card snapshot has `startRef` (`""` without the field). A card without the field does not change the file (`start-ref` appears only after the first setting); after clearing, the key `start-ref:` stays in the file with an empty value, like the launch keys. It is a hint, not a consent: it does not replace the `base-ref`/`base-commit` projections from the journal, does not pin the base and does not change `StartCard` or the pinning on resume; it is used by "Zleć rozwiązanie konfliktów" (Assign conflict resolution) and the "Start od…" (Start from…) UI |
| `projects` | the card's additional projects (PT-28) — slugs, comma-separated, the main one stays in `project`; the task changes several repositories at once. In the file only when set. `CreateCard`/`UpdateCard` accept a list (or a string "a, b") of slugs of existing projects or absolute repository paths — a repository without a project gets a project in a separate commit (like `repo`); the main project and repetitions drop out, at most 5, an unknown slug — `not-found`. Changing `project` to one of the additional ones removes it from `projects`. An unknown slug in the file — an entry in `errors`, the card works |

A project — `projects/mobileapp.md`, the content is an optional description:

```markdown
---
name: mobileapp
repo: /home/user/DevWorkspace/mobileapp
tags: Firma
ai-docs: tide
tool: claude
mode: full
---
```

- `repo`: an absolute path to the repository or empty (a project without code). The Board repository cannot be a project.
- `ai-docs`: `repo` (agent plans and specifications in the repository's `docs/agents/`) or `tide` (in `tide-board/docs/<project>/`, nothing goes into the project repository). On creation: `repo` when the repository already has `docs/agents/`, otherwise `tide`; without the key in the file — the same computed on read (`aiDocsDefault: true`).
- `tool`: `claude` or `codex`, `entry`: `planning` or `spec` (Specification only), `mode`: `full` or `light` — defaults for launches from the project's cards (by default `claude`, `planning` and `full`; the mode applies only to Planning). PT-77: "Specification only" is an entry, not a mode — a project with the old `mode: spec` is read as `entry: spec` (mode `full`), without an error and without rewriting the file; the old notation disappears only when the user changes the project's entry or mode. Further entries (Mam plan (I have a plan), Od razu) will arrive in `entry` without a format change.
- `youtrack`: the YouTrack project key (`LOG`, `LEGAL`; capital letters and digits, unique on the Board — a second project with the same key is `exists`), `youtrack-sync`: `on` (the default, without the key in the file) or `off` — PT-7, "Synchronization with YouTrack" below. A project without a key synchronizes nothing. A key from an import also assigns the imported cards to the project.
- `review-independence` (JSON `reviewIndependence`, K-88/PT-330, decision 96 of 10.11): the project's review independence policy —
  `free` (the default, also without the key in the file; writing `free` does not add the key), `other-model` (a different model or a different
  thinking strength than the author) or `other-family` (a different family: a provider/program pair from the executor catalog, not a name).
  `review-independence-classes` (JSON `reviewIndependenceClasses`, a list of the classes `lekki`, `standard`, `mocny`, `lokalny`,
  comma-separated in the file) narrows `other-family` to tickets of those classes; the rest get `free`, and the plan review
  applies the policy without narrowing. `UpsertProject` rejects an unknown value (`bad-request`); an unknown value typed
  by hand into the file is shown unchanged by the snapshot, and the service applies `free` with a warning in the service journal
  (`cardflow.reviewgate`, once per value and file). The service reads the setting from the current Board on every check,
  so a change works without a restart. The Board window sets it in the project form ("Recenzent" (Reviewer): Dowolny (Any), Inny model (Other model),
  Inna rodzina (Other family), with a classes field). Regardless of the policy the reviewer always starts in a new, separate session.
- Projects are created explicitly (`UpsertProject`; the same name a second time is the same project, not `foo-2`) or at the first card for a repository (`CreateCard` with `repo`) — in a separate commit. The service does not create entries for all repositories from the projects directory; the snapshot lists them in `repos` as candidates.

### Project test contract (K-69, PT-278)

A Board project has a test contract: the command "tests selected for the changes" (`tests.changed`, with the parameter `{base}`)
and "full tests at close-out" (`tests.full`). It lives in the project profile `projects/<slug>/box.yaml` as the key
`tests` of the schema `tide-box: 1` — in the same file as the K-38 box profile, **not** in the frontmatter of
`projects/<slug>.md`. The user's approval is described by "Approving the test contract" below (PT-285),
and the protection against an agent step by "Protecting approval from an agent step" (PT-286).

```yaml
tide-box: 1
tests:
  changed: "make test-changed BASE={base}"
  full: "make test"
```

- Canonical notation: double-quoted strings encoded by `json.dumps`, indented by two spaces.
  The reader recognizes the whole string token with a JSON decoder; only after its end does it allow whitespace and a
  `#` comment. YAML apostrophes and unquoted strings are still supported for hand-editing the profile. The edge
  whitespace of the input is normalized by `validate_tests`; whitespace inside the command stays unchanged.
- `UpsertProject` accepts `tests: {"changed": "…", "full": "…"}`. An empty string clears the field, a missing key does not
  change it, a missing `tests` in the request does not touch `box.yaml`. When both fields are empty, the `tests` key disappears from the file
  (the file stays, e.g. with `tide-box: 1` alone). Without a file the write creates it with `tide-box: 1`.
- The write replaces or adds (at the end of the file) only the `tests` block at indent 0 together with its indented lines;
  the rest of the file (K-38 keys, comments, an indented `tests` under another key) stays byte for byte. The `box.yaml` file
  goes into the same Board commit as the change to `projects/<slug>.md`; a `tests` write alone does not change the `.md` file.
- The rules (`validate_tests`): one line, no NUL, at most 300 characters; `changed` must contain `{base}` and does not
  accept other parameters in braces; `full` accepts none. A rejection is a `bad-request` error without changing
  files and without a commit. A command with `*` or one that is not a single command (`single_command`) is valid, but
  `grant_note(command)` returns a message that Tide will not give it an automatic consent.
- The project JSON (snapshot, project list): `tests` `{changed, full}` (always both keys), `testsError` (a string,
  empty without an error), `testsApproved` (below) and `testsGrantNote` `{changed, full}` from `grant_note`. A damaged block (a wrong type, bad YAML, an unknown
  key, a rule violation) gives an empty contract, `testsError` and an entry in the Board errors; the rest of the list keeps working.
  A `tests` write repairs such a block.
- The module: `daemon/tide_daemon/board/project_tests.py` (`validate_tests`, `load_tests`, `save_tests`, `grant_note`),
  outside the K-38 `box/` directory.

#### Approving the test contract (PT-285)

The contract lives in Board files, so an agent step, a manual edit or a commit revert can change it.
An approval is a **fingerprint** of the contract stored outside the Board, in the service state:
`$XDG_STATE_HOME/tide/test-contracts.json` (by default `~/.local/state/tide/`).

```json
{"boards": {"/home/…/tide-board": {"mobileapp": "<sha256 hex>"}}, "version": 1}
```

- **Fingerprint** = SHA-256 of the canonical representation `{"changed":"…","full":"…"}` (a fixed field order, JSON without
  whitespace, UTF-8). A missing field ≡ an empty string; only the whitespace at the edges of each command is stripped —
  whitespace inside (also in a quoted argument) is significant. The key: the Board path after `realpath` and the slug.
- **The approval rule.** A fingerprint is created only by `UpsertProject` with the `tests` field (the project window):
  `board_monitor` calls `BoardStore.approve_project_tests`, which writes the contract like `upsert_project`,
  checks under the same Board lock that the file has exactly this contract, and stores the fingerprint of the whole contract
  after the write (a partial write, e.g. `full` alone, approves both fields). An empty contract removes the fingerprint. A bare
  `BoardStore.upsert_project` (e.g. adding allowed commands by the card engine), reading the list, the snapshot
  and the commands for a card (`RenderFlow`, `RenderLaunch`) neither create nor change the fingerprint. Re-approving
  identical values (after the fingerprint was lost or the content was restored) stores the fingerprint without changing
  Board files and without a commit (`changed: false`, `testsApproved: true`).
- **`testsApproved`** in the project JSON: `true` only when the contract is non-empty, the block valid, and its fingerprint
  equal to the stored one. A contract different from the fingerprint (or without a fingerprint) has `false` and an entry in the Board errors
  "`projects/<slug>/box.yaml`: kontrakt testów zmieniony poza oknem projektu — sprawdź i zatwierdź" (the test contract was changed outside the project window — check and approve); a write
  in the project window approves it. An empty contract and a damaged block have `false` without this entry (the block has its own error).
  The `UpsertProject` reply with `tests` has `testsApproved` and `approvalError` (empty without an error); a fingerprint write
  error does not roll back the stored contract — it stays unapproved.
- **The fingerprint file.** The `tide` directory 0700, the file 0600, the owner = the service user. A read opens the directory
  and the file with `O_NOFOLLOW` (the file also with `O_NONBLOCK`), checks `fstat` and the 1 MiB limit. A missing file or directory,
  a symlink, a wrong owner, wrong permissions, a non-regular file, damaged JSON or a wrong shape give `false`,
  never `true` and never an error in the project list. The write: a temporary file in the same directory (`O_EXCL|O_NOFOLLOW`,
  0600), `fsync`, `rename` relative to the directory handle, `fsync` of the directory; an interruption halfway leaves the old
  file and removes the temporary one. Writes are serialized by `flock` on the directory handle. A write fixes the `tide` directory permissions
  to 0700, and replaces an unreadable file with a new one.
- The module: `daemon/tide_daemon/board/contract_approval.py` — one read entry point `approved(board, slug, contract)`
  and one write `record(board, slug, contract)`; PT-286 uses them.
- **The boundary.** A step uses the approval only through `approval_for_step` (below, PT-286); integration
  with the card engine is PT-287. Until then `testsApproved` is only information in the window.

#### Protecting approval from an agent step (PT-286)

0600 permissions do not tell the service from an agent step of the same user, and a dead `DBUS_SESSION_BUS_ADDRESS`
from the step environment can be overwritten by the agent. That is why the approval is protected by two conditions and by tests in a real sandbox.

- **Path collision — `approval_for_step(project, step, board)` → `(approved, reason)`**
  (`contract_approval.py`; in the service `BoardStore.approval_for_step(project, step)`). Before the contract is
  allowed for a step, the function compares the fingerprint file and each of its parent directories — in the given form
  and after `realpath` (with symlinks resolved) — against **every write area of the step**, also after `realpath`. Store
  paths are not normalized lexically before symlinks are resolved: `link/..` (also in `XDG_STATE_HOME`) is the
  parent directory of the symlink target, as in the file system, so the same file is checked that is later
  read. The path is resolved component by component as in the kernel: every directory entry along the way of
  the resolution is checked (the real directory + the name), also entries from the content of encountered symlinks — an intermediate symlink
  in a write area (`/safe/alias → /worktree/pivot → /safe/target`) is a collision, because the step could repoint it
  between the check and the read — and the target of every prefix. A symlink loop or an error reading them is
  uncertainty, that is, a collision. A collision is:
  the file or a parent directory lies in a write area, or a write area lies in the fingerprint directory. Then
  `approved: false`, the reason "magazyn zatwierdzeń leży w obszarze zapisu kroku: …" (the approval store lies in the step's write area: …) and the same entry in the Board errors
  (with the slug prefix), until the next check of this project passes without a collision. Without a collision the result is given by
  a read of the store (`approved`). Any uncertainty — wrong step settings (`StepError`), a worktree unknown to
  the repository, an empty or relative area path, the area `/`, any exception — means `approved: false`.
- **Write areas** are given by one function in `tasks/agentcmd.py`: `write_areas(step, env, git_dir, skills)` →
  `WriteAreas`; from it `_claude` builds `allowWrite`, `--add-dir` and `denyWrite`, and `_codex` — `--cd`,
  `writable_roots` and the `/tmp` exclusions. `step_write_areas(step)` determines its inputs the same way as `background()`
  (without creating files), so the check and the step's arguments do not have two copies. `WriteAreas.writable()`:
  - Claude: the session working directory (the worktree or the steward step's `out`), `allowWrite` (`out`), every `--add-dir`
    not covered by `denyWrite` (today `out`; read-only directories, results of previous steps and skills have
    `denyWrite`), `CLAUDE_CODE_TMPDIR` and the write the program grants itself: `/tmp/claude`, `/private/tmp/claude`,
    `/tmp/claude-<uid>` and the repository's shared `.git`,
  - Codex: `--cd`, `writable_roots` (the worktree directory in `.git/worktrees`, `.git/objects|refs|logs`, `out`) and
    the program's `/tmp` and `$TMPDIR` — only when the step does not exclude them (`exclude_slash_tmp`, `exclude_tmpdir_env_var`:
    a steward step, read-only directories, a read profile); a collision is computed from the step's actual settings,
  - always the Board repository (AI documents and Konsole write in it; `approval_for_step` adds it).
- **The step path manifest (PT-480, K-109).** `WriteAreas` is part of the `PathManifest`, which is built by one function
  `agentcmd.path_manifest` (in `background()` and `step_path_manifest`/`step_write_areas`). From the manifest, and only from it,
  the step's sandbox arguments are built: write (`read_write`), an explicit write ban (`read_only` → Claude's `denyWrite`;
  for Codex no write directory covers them) and hiding (`hidden` → Claude's `denyRead` next to `~`). The content:
  - write: the step's worktree, its `out`, technical tmp (`tmp`: `CLAUDE_CODE_TMPDIR`, program directories, Codex's `/tmp`
    and `$TMPDIR`), its own `.git/worktrees/<wt>`; `.git/objects|refs|logs` (Codex) or the shared `.git`
    granted by the Claude CLI itself (without the `.git/worktrees` of other steps) — as before PT-480,
  - read only: the `out` of earlier steps of the ticket and of the executor for the reviewer (`context_dirs`), read
    directories, skills, `.git/hooks` and `.git/config` (new `denyWrite` in the Claude settings, also in the box,
    which inherits the host's `denyWrite`),
  - hidden: `.git/worktrees/<others>` (Claude). Codex reads the whole disk — the manifest's `limits` records this explicitly;
    hiding for Codex is provided only by the host unit or the box (K-109 stage 2),
  - a technical `HOME` (`home`) without write; project tools write caches in `out`.
  The manifest is undeterminable (`ManifestError`, a subclass of `StepError`: the step does not start) when the write boundary (without
  the tmp directories) covers the step's `in`, the shared worktree directory or the steps directory (that would open the `out` of other steps),
  `.git/worktrees`, the Board, the card journal (`$XDG_STATE_HOME/tide`) or `~/.claude`, `~/.codex`, `~/.config`,
  `~/.local/share/tide`, and when any level — the shared worktree directory, the worktree, the steps directory, the step
  directory, `out`, `in`, `.git`, `.git/worktrees`, `.git/worktrees/<wt>`, Codex's write directories in `.git` — is
  a symlink (an `lstat` check, without following) or is not a directory. Every level is pinned to an inode;
  `Background.prepare()` and `Background.start()` check them again right before writing the step files and before
  `Popen`: a swap (a symlink or a different directory under the same name) is a refusal to start. The window between this check
  and mounting in the program's sandbox (bwrap of the Claude CLI/Codex) remains; only the stage 2 executor closes it.
  A step in a box (`BoxRunner`) does not call `prepare()`/`start()` (it has its own symlink checks in `StepDir`), so
  there the manifest is checked only at build time; a re-check in the box comes with stage 2.
  In a step `gc.auto=0` and `maintenance.auto=false` (`GIT_CONFIG_*`), and `git gc`, `git worktree prune`,
  `git maintenance` (the `gc` task runs without `--auto`, so `gc.auto=0` does not stop it), `git prune`
  and `git repack` are forbidden in every project (`board.commands.STEP_DENIED`, the whole subcommand, also with git
  options before it and outside the sandbox): the entries of other steps are hidden in the step, so they and their objects would look
  abandoned.
- **Call origin.** `UpsertProject` with the `tests` field determines the sender's PID from the bus
  (`GetConnectionUnixProcessID`, `QDBusConnectionInterface.servicePid`) and reads `/proc/<pid>/cgroup`
  (`daemon/tide_daemon/call_origin.py`). A process in a step unit (a `tide-step-*` segment, also a subgroup of the unit
  and cgroup v1 hybrid entries), no PID, PID 0, no process, an empty, unreadable or incomprehensible file →
  a rejection `{ok: false, error: "forbidden", message}` without changing `box.yaml`, the fingerprint and without a commit (also on
  re-approval of identical values). Without the `tests` field `UpsertProject` works as before and does not check
  the sender. Writing the contract in the file by a step does not change `testsApproved` (PT-285). Commands outside the sandbox
  (`tide-outside-*`) and the project window are senders from outside a step. The service option `--proc-root` (hidden) is for
  tests only (a `/proc` stub). A limitation: a process moved out of the step unit (e.g. `systemd-run --user`
  from inside a step) would not be recognized — this route must be closed by the sandbox, which the trials below check.
- **Real sandbox trials (a condition).** The protection applies only when, in the real sandboxes
  of Claude and Codex (`workspace-write`) with the settings from `tasks/agentcmd.py`, each of the trials ends with a sandbox
  refusal or a rejection by the origin check, and the fingerprint and `box.yaml` stay unchanged:
  (a) a write, a swap (`rename`), deleting the fingerprint file, moving its directory, a symlink in the state
  directory, a write in the real store directory, a write to `box.yaml`; (b) `UpsertProject` with `tests` with a dead
  bus address, with the service's real bus address given, and with an overwritten
  `DBUS_SESSION_BUS_ADDRESS`; (c) a process outside the step unit through `systemd-run --user` (also with
  `XDG_RUNTIME_DIR` and the real bus) and `UpsertProject` from such a process. A successful trial means that
  the approval must not be allowed for steps. The script: `scripts/sandbox/approval_trials.py <result directory>`
  (outside the sandbox; the service from the repository code on a separate bus, a trial store in
  `~/.local/state/tide-pt286-trials`, the report `report.md`), trials in a step: `scripts/sandbox/approval_probe.py`.
  Every change to `tasks/agentcmd.py` concerning write, network or the step environment requires a re-run.
  The script chooses the program and the skipped Codex MCP servers the way the service does: Codex from the ChatGPT app instead of the snap,
  because the snap does not work in a `tide-step-*` unit, and `codexMcpSkip` with a retry.
  **Result for PT-286 (8.11.2026, HEAD `b2b79238`):** in Claude and in Codex (`workspace-write`) all trials
  ended with a refusal. Claude: the home directory is invisible in the sandbox, and creating AF_UNIX sockets is forbidden by
  seccomp, so the service bus, the desktop bus and `systemd-run --user` all fail. Codex: the file system outside
  the write areas is read-only (`Read-only file system`), and a connection to any bus socket ends
  with "Operation not permitted", so `systemd-run --user` does not work either. The fingerprint, `box.yaml` and the Board HEAD stayed
  unchanged. The origin check had nothing to reject, because no step connected to the bus; it protects
  against a future loosening of the sandbox. The details of each trial are in the ticket note.

#### Suggestion and the source of the contract (PT-282)

`SuggestProjectTests(s json) -> s` — read only; the input `{slug}` or `{repo}`, the reply
`{ok, changed, full, source, facts, message}`, for `slug` additionally `effectiveSource`, `unapproved`, `testsApproved`.

- **The suggestion** is computed by `suggest_tests(facts)` (`cardflow/testpolicy.py`) on the `repo_facts` facts, which it takes from the
  shared `make_targets` (the same rule and the same safeguards as in the ticket sheets: it runs nothing, it skips symlinks
  outside the repository). The target `test-changed` → `make test-changed BASE={base}`, the target `test` → `make test`
  (source `make`); targets are examined independently, so `test` alone gives `full` without `changed`
  (`facts.legacy: true`). Without targets or without a `Makefile` the result is empty, `source` = `""`, and `message` says "brak
  podpowiedzi — kartki odeślą do zasad projektu" (no suggestion — the ticket sheets will refer to the project rules). For now the facts are only `make` targets; switching to
  the K-38 `StackReport` waits for the condition from the k38-tests-schema sketch.
- **The source** (`effectiveSource`: `profile`, `detected`, `rules`) and `unapproved` are the same `effective_tests` decision
  as in the ticket sheets, computed on the project repository with the contract from `box.yaml` and `testsApproved` from the fingerprint store.
  The window is not a step, so the path collision with the approval protection does not apply to it.
- **`repo`** goes through the same validation as a project's `repo` (`git_repo_path` in `board/store.py`, shared with `_repo`):
  an absolute path of an existing Git directory, also a worktree (a `.git` file); otherwise `bad-request` with a message.
  An unknown `slug` → `not-found`. A project with a missing repository (`repoMissing`) or a repository without targets is
  an empty result with a message, without an error.
- The suggestion writes and approves nothing; the contract is created only when the project is saved from the window.

#### Compatibility with the K-38 box profile

A reference profile file with the K-38 keys (`apt`, `network`, `verify`) and a `tests` block:
`daemon/tests/fixtures/box-profile/box.yaml`. The test `daemon/tests/test_board_project_tests.py` reads it through
`load_tests` and compares it with the result of `save_tests`. A sketch of changes on the K-38 side:
[k38-tests-schema](agents/plans/followups/k38-tests-schema.md). The conditions for merging the second of the K-38/K-69 cards
into the main one (the met conditions ticked off by PT-321; tests: `daemon/tests/test_box_profile_tests_key.py`):

- [x] `tests` in the closed list of project profile keys, in `KEY_ORDER` and in `NON_HASH_KEYS` (as `tests.changed`,
  `tests.full`): changing only the test commands does not change the recipe hash. The card profile rejects `tests`
  (`validate(..., "card")`), and `merge(project, card)` carries the contract over from the project unchanged;
- [x] the `tests` validation delegates to `validate_tests` (one rule, no changes on the K-69 side);
- [x] the `yamlmini` reader recognizes canonical JSON strings also with trailing backslashes and a comment outside
  the string: the end of a quoted string is determined by a full pass over the escapes (a backslash always escapes the next character),
  and the value and its end by the JSON decoder (`raw_decode`); in apostrophes `''` is an apostrophe in the string. Regressions
  of the commands generated by `test_board_project_tests.py` are also read by `bp.load_project`, also after a K-38 write
  and a partial update by `BoardStore`;
- [ ] `make` targets (`test-changed`, `test`, `lint`) available in the report regardless of Python detection
  (outside PT-321: `followups/stack-hints.md`);
- [x] the reference file is read without error by `box/profile.py` (`load_project`; the result contains `tests`);
- the changed step launch paths (start, resume, conflict, close-out, box steps) checked for
  test policy calls (after #10, #7 and #3 of the K-69 plan): each of them still computes one `TestPolicy` for the ticket sheet
  and the consents (`step_test_policy`), as proven by the flow tests with a process stub, not by `box.yaml` compatibility alone. The general
  rule on which this condition rests: [testing.md](testing.md#general-rule-and-the-project-test-contract).
  PT-321 does not change the step launch paths (only reading and writing the profile).

The shared file `projects/<slug>/box.yaml` (PT-321):

- **Read.** `bp.read_project(slug)` returns a `ProjectFile`: the `tests` block is read by the K-69 rule (`load_tests`), the rest of the
  file by `yamlmini` and the K-38 schema. A damaged `tests` block does not invalidate the K-38 profile: the profile is without `tests`, and
  the error (`tests_error`, the same string as `testsError` in `BoardStore`, with the `validate_tests` message) goes into the notes
  of the "Środowisko karty" (Card environment) section. `bp.load_project` returns the profile with a valid, non-empty contract in `tests`.
- **A profile without K-38 settings is new.** A file with only `tide-box`, `project` and `tests` (e.g. created by
  `save_tests`) is not an approved K-38 profile: `load_project` returns None, and the environment consent shows
  a proposal and a consent pack like a project without a profile. Approving an empty profile writes `box: per-project`
  (a default value, outside the hash), so that the next read does not consider it new again.
- **The K-38 write.** `bp.save_project` (the only path for writing a project profile: `envconsent` with `box.enabled` true and
  false, `tide box approve … --accept-red`) skips `tests` from the input and carries the `tests` block over from the file byte for byte,
  also a damaged one. It does not write `testsApproved` and does not touch the fingerprint store (PT-285/PT-286): approving
  the contract is counted only by `BoardStore`. An existing file that cannot be read (YAML or schema outside the
  `tests` block, a different `project`, `tests` outside the top-level block, size, encoding) the write does not
  overwrite — `ProfileError`, and the environment consent answers with an error without a write.
- **A shared lock.** `save_project` reads and writes the file under the same lock as `BoardStore.transaction`
  (`board/lockfile.py`; reentrant within a thread): `flock` on the Board directory descriptor — it works also on a
  valid Board without `.git` (e.g. a copy of the files), which `BoardStore.ensure()` only then initializes — and when the
  repository exists, additionally `flock` on `<board>/.git/tide-board.lock` (compatibility with K-55). A fixed order:
  the directory, then `.git`. An interleaving of a K-38 write and a contract write in any order, also before the git
  initialization, gives the latest contract and the latest K-38 settings. Without the Board directory a read alone creates nothing
  and takes no lock; a write that creates the Board (`ensure()`) creates the directory, takes the lock before
  the initialization and holds it to the end of the outer transaction — a fresh read, plan and contract write after the Board is created
  do not interleave with a K-38 write (review PT-321 r2).
- **A known limitation: comments.** A K-38 write (`yamlmini.dumps`) rewrites the file outside the `tests` block, so
  comments outside this block (e.g. above `tests:`) disappear; comments inside the block stay. A contract write
  (`save_tests`) still preserves the whole file, comments included — the history promise of the 10 specifications concerns the contract
  write, not the K-38 profile write.

#### The effective test contract in step sheets (PT-279)

The sheets for a ticket, fixes, review and close-out (`cardflow/handoff.py`) take the test commands from `TestPolicy`
(`cardflow/testpolicy.py`). The decision `effective_tests(contract, worktree, approved, base, error=…)`:

1. an approved contract (`approved` — a boolean from outside; the decision does not compute the fingerprint) → source `profile`;
   a missing field is filled in by the general rule, not by detection;
2. without an applied contract (missing, unapproved, a damaged profile `testsError`) — detection
   `make_targets(worktree)`: the target `test-changed` → `make test-changed BASE={base}`, the target `test` → `make test`
   (targets examined independently) → source `detected`;
3. the rest → the general rule (`rules`): "testy dobrane do zmian według zasad projektu (CLAUDE.md/AGENTS.md)" (tests selected for the changes according to the project rules (CLAUDE.md/AGENTS.md)), and at
   close-out — the full tests according to the project rules.

`legacy` — a branch with `test` without `test-changed`: a full `make test` in the ticket, fixes and review, and the reviewer
checks in the note for a successful full `make test` (the only exception to the ban on requesting a full run). An approved
`make test-changed …` on a branch with `test` alone also gives `legacy`; on a branch without both targets `changed` falls back to
the general rule with a line about the missing target (likewise an approved `make test` without a `test` target at close-out).
The target check applies only to a plain `make <target> [VARIABLE=…]`: a command with a `make` option (`-C tests`,
`-f tests/Makefile`, `-j4`) stays an approved command without a check, because the targets of the main `Makefile`
and `mk/*.mk` do not decide about it. The source line of the general rule with an applied contract gives the actual
reason: the contract does not give this command or the branch lacks the target from the contract.
`{base}` is replaced by the ticket's pinned start point (`shlex.quote`); without a base it stays unexpanded
(`base_pending`), and the sheet describes it as the ticket's pinned start point. The sheet has a command source line
("wykryte z Makefile" (detected from the Makefile), "zatwierdzony kontrakt…" (approved contract…), "zasady projektu…" (project rules…)) and a line about an unapproved contract
that is not applied. The retry recipe through `tools/test_runner.py` is given only to the commands `make test`
and `make test-changed`; other ones (e.g. `dotnet test …`) get the general retry rule.

`make_targets(directory)` is the only place of the card flow that reads the `Makefile` and `mk/*.mk`: read only, without
running programs, skipping files and the `mk` directory that leave the directory through a symlink. Until
integration with the engine (#10) the engine calls the sheets without a policy, and the sheets compute it by `master_policy(worktree, base)`
— with the rule from before K-69 (without a `Makefile` still `make test-changed` and `make test`, without a source line). The reference
sheet fragments from master: `MASTER_FRAGMENTS` in `daemon/tests/test_cardflow_effective_tests.py`.

**The conflict sheet and the planning line (PT-284).** The merge conflict resolution step (`conflict`) computes
the policy through `step_test_policy` at start and at every resume, like the ticket, fixes, review and close-out
(the step event has `testPolicy` and `testPolicyCard`, the resume gets the section "Obowiązujące testy" (Applicable tests) in the conflict
variant). The base is the pinned SHA from the branch creation result of the ticket being merged (`ticket_of(st, ticket)["base"]`),
not the tip of the card branch; without a known base `{base}` stays unexpanded and described. The conflict sheet
(`conflict_tests`) tells you, after writing the merge commit, to run in the card worktree: `tests.changed` of the approved
contract, the detected `make test-changed BASE=…`, in `legacy` a full `make test`, and without a command — tests selected for
the changes according to the project rules; in every variant the command and the result go into the note, and `done` requires a successful run.
The former "uruchom potrzebne testy" (run the needed tests) without a command is gone. The steps `planning`, `plan-fix` and `plan-review` get a single
information line `Kontrakt testów projektu (informacja dla planu): …` (Project test contract (information for the plan): …) (`plan_tests_line`) with the source
and the commands of the effective contract (`changed` with a description of `{base}`, `full`) or with the information that the sheets will refer to
the project rules. The planning policy has no base and no `testPolicy` in the event (it is neither a requirement of the step nor a source
of consents); a resume replaces the line, and the `plan-review.json` format and the approval rules do not change.

#### Consent for the contract commands (PT-280)

The ticket author, the fix author, the reviewer, the close-out step and the conflict resolution step may run a command
of the approved test contract without a `denied` result and without a prompt. The rule lives in one function,
`testpolicy.contract_grants(policy, denied)` → `ContractGrants(commands, notes, fix)`; `_launch` calls it at every
start and resume with **the same** `TestPolicy` from which the step sheet was built (`step_test_policy`), so the consent
text is exactly `policy.changed` / `policy.full` from the test rule of that sheet.

- **Where the consent comes from.** A field goes on the list only with the source `profile` (`changed_source` / `full_source`),
  that is, when `approval_for_step` for this step gave `approved` while building the policy: the fingerprint matches the content
  of the file at that moment and there is no store path collision. A contract changed by the planner, the author, another step,
  by hand or by a commit revert (a different fingerprint), a missing or damaged fingerprint store and a path collision give
  a list without the contract commands, and the sheet says the contract is not approved; the run goes the ordinary
  `denied` way. We do not remember consents between steps — every resume computes it anew. Commands
  detected from the `Makefile` (source `detected`, also `legacy`) and the general rule give no consents.
- **A literal command.** Consent goes only to a command for which `project_tests.grant_note` is empty:
  a single one (`single_command` — without `;`, `&&`, `|`, `$`, a backtick, a subshell, `env`, assignments
  other than language/time zone) and without the `*` character after the base substitution. In Claude's rule this gives `Bash(<command>)` without the
  `… *` variant, and in `is_allowed` — an exact comparison of arguments, so neither extra arguments nor another
  command (e.g. `pytest -x tests/other` with `full: pytest *`) pass. A command with `*` stays
  a requirement of the sheet without consent, with the same message as `testsGrantNote` in the project JSON (the project window).
- **The base.** A `changed` with an unexpanded `{base}` (no pinned base) does not go on the list; `full` does.
  The base SHA is inserted by `shlex.quote` in `effective_tests`, the conflict step has the base of the ticket being merged.
- **Precedence of bans.** A command matching a project ban (`denied-commands`) or a Tide one
  (`TIDE_DENIED`, `make install`) — also with an `LC_ALL=…` prefix (`forbidding_rule`) — is not added,
  and the sheet warns that the test contract needs fixing.
- **Where the consents are visible.** The commands reach the step's list (`_background_step(contract_commands=…)` →
  `claude_tools([*allowed-commands, *consents], …)`), not the list stored in the project (`allowed-commands` does not
  change). The sheet has them in the section "Polecenia projektu w tym kroku" (Project commands in this step), in the block
  `<TIDE_CONTRACT_COMMANDS>…</TIDE_CONTRACT_COMMANDS>` (consents, commands without consent with a message, a warning
  about fixing); a resume removes the previous attempt's block and adds the current one. Without consents and notes the block is not created
  (the master text). The step start event has `contractCommands`. The sandbox and the `outside` contract do not change
  (network and Docker still through `outside`); a step in a box gets the same block in the sheet.

### Read-only project data directories (PT-246)

In "Projekty i tagi" (Projects and tags), the "Katalogi tylko do odczytu" (Read-only directories) field takes one directory per line,
e.g. `~/.cache/hud-timeline`. `UpsertProject` accepts `readDirs: ["/full/path", "~/directory"]`;
the project snapshot returns `readDirs` as a list of canonical paths. A missing field in an old project
means an empty list. An empty list in a request removes the sharing; omitting the field keeps the setting.
Stored in the project file as `read-dirs` (JSON); the internal `read-dir-bindings` keeps the symlink bindings.

The limit is 32 entries. Tide removes duplicates, expands `~/` relative to the service user, and resolves
symlinks. It does not create missing directories. It refuses system roots, mount points,
the home directory and its ancestors, secret directories (among others `.ssh`, `.gnupg`, `.aws`, `.azure`,
`.kube`, `.codex`, `.claude`, `.config`, the key wallet and Tide state), and overlap with the step's
write area. Paths containing permission pattern characters are rejected. The token of the current model entry also
must not lie in a shared directory. A setting error is never silently turned into a different permission.

Every start and resume re-checks that the directories exist and the remembered symlink targets.
A swapped target produces a clear refusal; you have to remove the old entry and deliberately share the new target.
Only steps of the project, and steps already allowed to read that project as an
additional project of the card/assignment, get the directories. They are not added globally to other cards or to host commands.
A change of the setting takes effect from the next launch, including when entering that work in the Console.
A process that is already running keeps the previous profile; removing an entry does not stop it.

Claude gets `allowRead` and the file system profile's **`denyWrite`**, alongside the ban on Edit tools.
Codex keeps `workspace-write`: the data does not go into `--add-dir` or `writable_roots`, and the default
write areas `/tmp` and `TMPDIR` are disabled for this profile. This preserves Codex's existing general
read scope; `readDirs` is not a new mechanism for hiding the rest of the disk. Entering the Console for
this work uses the shared profile and keeps it even after the last entry is removed. No
new K-38 box and no new path for running commands outside the sandbox is created.

Proof that the boundary works: `daemon/tests/test_project_read_dirs_native.py`, enabled by
`TIDE_READ_DIRS_NATIVE=1`. The test uses the native Codex `sandbox linux` and Claude's native Bash
with a local API stub (no model, real keys, hooks or MCP). Reports in
`build/test-results/read-dirs-native/{claude,codex}-proof.json` separately show a successful read and the
non-zero exit codes of attempts to write, create, delete and rename, both directly and through a symlink
from the worktree. A sandbox being unavailable after the test is enabled is an error, not a passed test or a skip.
Attempts that require nesting the sandbox must be reported in the step as `outside`.
If Codex is installed as a Snap, the attempt uses the native program from the same
installed package (`/snap/codex/<version>/bin/codex`). The `snap run` wrapper may refuse to start
in the `tide-outside` unit before the profile is created. The test checks the `workspace-write` boundary
of the program itself; it is not a test of additional Snap restrictions or of its wrapper's compatibility with systemd.
This does not change the production choice of program. The `{claude,codex}-command.json` artifacts record
the exact argv of the executed test next to the operation proof.

Tag — `tags/firma.md`: `name` (no comma, up to 40 characters) and `color` — the name of a pastel from the palette
(`teal`, `lavender`, `peach`, `butter`, `rose`, `mint`, `sky`; the list comes from `pastel` in `theme/palette/`),
the body is the description. A new board gets "Firma" (Company; `sky`, commercial client) and "własne" (own; `lavender`).
A tag used in a card or project without its own file is also in the snapshot (`defined: false`) with a fixed
color derived from the name. Letter case in tag names does not matter when comparing.

**Mode templates.** `RenderFlow` creates the documents directory if it lies in the board (`ai-docs: tide`,
also `docs/_inbox` for a card without a project) — the agent gets it through `--add-dir`; in the project's
repository (`ai-docs: repo`) it creates nothing. An empty directory does not go into git, the agent's files are picked up by
sync. The template is taken from `flows/<project>/<key>.md`, then `flows/<key>.md` (the key is the Planning mode, or `spec` for the Spec only entry; `flows.flow_key`), and without a
board — the built-in template (`board/flows.py`, the same contents that go into a new board).
The template's frontmatter does not go into the prompt. Only the fields `{card_id}`, `{title}`,
`{description}`, `{project}`, `{repo}`, `{docs_dir}`, `{branch}`, `{tool}`, `{docs_note}`, `{spec}`,
`{spec_file}`, `{check}`, `{check_label}`, `{finish}`, `{spec_commit}`, `{projects}` are substituted — other braces stay, and the card description is not
a template. `{projects}` (PT-28, in the header right after "Plany i specyfikacje" (Plans and specifications)) is empty for a single project;
with additional ones — a list of "name: path" of the repositories and the rules: plan and specification only in the `{docs_dir}`
of the main project, in every additional repository the agent changes, a branch with the same name `{branch}`
from the local main branch (a worktree only when the repository rules require it; for changes or a different
branch in the main directory — a question to the user; no push), files by absolute paths and git through
`git -C`, no `cd`, separate commits in each repository, and in the plan's Notes on the ticket — which repository
it changes; for the Spec only entry the repositories are read-only. A custom template without `{projects}` gets this list at the
end of the prompt. Branch: from the window, otherwise the card's `branch` or the proposal `<tool>/k-12-<description-from-title>`
(thanks to the card ID in the branch the daemon will link the card to the coordinator; for the Spec only entry the prompt says "no branch").
`{docs_dir}` is `docs/agents` (relative — the agent's working directory, also a worktree) with `ai-docs: repo`
and an absolute path in the board with `ai-docs: tide`; `{docs_note}` says who commits these files (in the
board — only the service), and in the board also: files by absolute paths, no `cd` to the documents
directory (the working directory stays in the project repository — PT-28). `{spec_file}`: `{docs_dir}/specs/k-12-<description>.md`. `{finish}`: with `manual`
the "Co przetestować ręcznie i jak" (What to test manually and how) list in the plan, no merge, and a note that the work is waiting for manual
testing; with `auto` it ends after an AI review with APPROVE, merging only if the project rules
allow it.

Mode contents (`board/flows.py`, prompts in Polish): **full** — grilling (the `grilling` skill in Claude
Code, in Codex the same questions described in words), a specification in `{spec_file}` with frontmatter
`card`/`status: szkic`→`gotowa` (draft→ready), a plan per standard 1.1 with `card: {card_id}` and `coordinator: {tool}`,
ticket numbers from the project rules (CLAUDE.md/AGENTS.md, `make -C ~/DevWorkspace/plasma-tide next-id`,
`check-plans`; without rules — `tracker: local` with the prefix recorded in the plan), TDD (the `tdd` skill),
delegation and mandatory AI review per the standard, questions with the ticket ID; **light** — a short
specification, a plan with 1–3 tickets, TDD, AI review; **spec only** — questions and a specification
with AI review, no code, branch or plan, ending with `status: gotowa`; `{spec_commit}`: in the board it commits nothing
(the service does), and with `ai-docs: repo` it commits only the specification file on the current
branch (`docs(spec): K-12 <short English title>`, in English, no signatures or mentions of AI, no push) —
otherwise a new worktree, which starts from the commit, would not contain the specification.

**Template migration.** A board created before PT-4 has old seeds in `flows/` (and one from before PT-28 —
`tide-flow: 2` without `{projects}`; both are in `flows.PREVIOUS_SEEDS`). On service start
(`migrate_flows`) a file `flows/<key>.md` that is byte for byte an old seed (SHA-256 hashes
in `flows.PREVIOUS_SEEDS`) gets the new content in a single commit `flows: update templates`; a file changed
by the user, project overrides (`flows/<project>/…`) and missing files are left alone. PT-77: the header of the `spec` seed changed from `mode: spec` to `entry: spec` (the prompt content is the same), so the existing seed is in `PREVIOUS_SEEDS` and gets the new header; a `spec` template changed by the user (also a project override) that still has `mode: spec` in its header is left untouched, and the snapshot (`errors`) carries a warning asking for `entry: spec`. During
a revert or merge the migration waits, and `RenderFlow` never puts an old seed into the prompt
anyway (it then takes the built-in content).

**Ready specification** (`mark_ready_specs`, on every sync — 2 s after a change in the board,
otherwise every 60 s, so for a specification in the project repository within a minute): a card with the Spec only entry (`entry: spec`, also the old `mode: spec`)
in To do without `spec`, whose `<docs>/specs/` has a Markdown file named after the card ID (`k-12.md`,
`k-12-…`) or with `card: K-12` in the frontmatter and with `status: gotowa` (also `gotowy`, `ready`, `done`),
gets `spec: <path>` and returns to the top of the Backlog — commit `K-12: specification ready`. On the card
in the Backlog "✓ specyfikacja gotowa" (specification ready) is shown. A draft (`status: szkic`), a different mode and a card moved by hand
— unchanged. The agent calls nothing over D-Bus: changing the `status` line in the file is enough.

**Commits.** Every change from D-Bus is a separate commit with only the files the service changed, e.g.
`board: create`, `K-21: create`, `K-21: update title, tags`, `K-12: move to todo`,
`K-12: reorder in todo`, `project mobileapp: create`, `tag pilne: update`,
`tag własne: rename to Własne`. Author from the user's git configuration (without it "Tide
<tide@localhost>"), no GPG signature and no hooks (`--no-verify`, `core.hooksPath=/dev/null`)
— this is recording board state, not work on code.

**Git: locks and interrupted operations** (`board/repo.py`). Git gets 5 s. Killed after the timeout it leaves
`.git/index.lock` — the service removes it immediately if no other `git` process is running in the board
directory; someone else's lock is removed only when it is older than 10 s and there is no live git process with its directory
in the board. When a revert, merge, cherry-pick or rebase is in progress in the repository (the files `REVERT_HEAD`,
`MERGE_HEAD`, `CHERRY_PICK_HEAD`, `rebase-*` or files with conflicts in `git status`), the service commits nothing
— a "sync" commit would persist conflict markers — D-Bus writes end with the error
`git-busy` ("finish it (git revert --continue) or abort it (git revert --abort)"), and the snapshot has
`git.busy` and an entry in `errors`. Frontmatter with conflict markers (`<<<<<<<`, `>>>>>>>`) is a file
error; in the body the markers are plain text (the description may contain pasted git output), and a conflict
in progress in the body is visible in the unmerged files in `git status`.

**Changes from outside.** An agent writing a plan in `docs/`, a manual card edit, `git revert` — the service
watches the board's directories and files (`QFileSystemWatcher`), and after 2 s of quiet makes a commit
`sync external changes` with everything that changed, and sends `Changed`. The same check runs
at start (changes from the time when the service was not running) and every 60 s (a write with no event, a board
created outside the service), together with overdue commits of our writes (those with their own messages).
Our own writes are already in a commit, so they do not produce a second one.

**File errors.** A bad file does not stop the board: it goes into `errors` in the snapshot (`{path, message}`),
and the rest keeps working. A file is skipped if it has no frontmatter, has conflict markers in the frontmatter, is not UTF-8, is larger
than 256 KB (we read at most that much, never the whole), is not a regular file (FIFO, device), or is a
symlink pointing outside the board directory (a symlink inside the board works); the card number of such a
file is taken anyway. Frontmatter lines that are not a `key: value` pair produce an entry in `errors`; a bad value (column, order, tool, mode, `ai-docs`, color)
produces an error and the default value; a card with an unknown project — an error. A card from a skipped file cannot
be changed over D-Bus ("fix it by hand"). A write changes only the lines of the keys being set:
unknown keys, comments and body stay character for character.

**Board repository outside project lists.** The repository scanner (`worktrees/scanner.py:
find_repositories`) skips a directory with a `board.md` containing `tide-board` in the frontmatter — by the
marker, not by name — so the board is not in Worktrees, KRunner, the "Nowe zadanie" (New task) window or
the morning report, wherever it lies.

**Snapshot** (`Snapshot()`, `--print-board`):

| Field | Description |
| --- | --- |
| `boardDir`, `exists` | board directory and whether the repository already exists |
| `lastCommit` | `{hash, short, ts, time, subject}` or `null` |
| `git` | `{busy, pending, error}`: the operation in progress (`revert`, `merge`, `cherry-pick`, `rebase` or empty), the number of overdue commits and the last commit error |
| `nextId` | the ID the next card will get |
| `columns` | `[{id, label, manual, hidden?}]`: `backlog` "Backlog", `todo` "Do zrobienia" (To do), `progress` "W toku" (In progress), `waiting` "Czeka na mnie" (Waiting for me), `review` "Do sprawdzenia" (To check), `done` "Gotowe" (Done), `archived` "Archiwum" (Archive; hidden); `manual: false` — columns computed from the coordinator |
| `colors` | pastels from the palette `{name: "#rrggbb"}` |
| `tools`, `entries`, `modes` | allowed tools, entries (`planning`, `spec`) and Planning modes (`full`, `light`) |
| `tags` | `[{name, slug, color, description, defined, cards}]` (`cards` — cards with this tag after inheritance) |
| `projects` | `[{slug, name, repo, repoMissing, tags, aiDocs, aiDocsDefault, tool, entry, mode, description, docsDir, cards, path, youtrack, youtrackSync, reviewIndependence, reviewIndependenceClasses}]` (`youtrackSync` — the key is set and sync is enabled; `reviewIndependence` — the raw value from the file, an unknown one read as `free`) |
| `cards` | `[{id, num, title, project, projectName, tags, resolvedTags, column, boardColumn, order, note, created, createdTs, updated, updatedTs, moved, movedTs, youtrack, spec, session, branch, check, entry, mode, tool, worktree, launched, launchedTs, branchSlug, projects, extraProjects, description, derived, path}]` in column and `order` order; `branchSlug` — `k-12-<description>`, the part of the proposed branch without the tool; `extraProjects` — resolved additional projects `[{slug, name, repo, repoMissing}]` (without unknown slugs) |
| `repos` | repositories from the projects directory that are not yet projects: `[{name, path, aiDocs}]` (default `ai-docs`) |
| `errors` | `[{path, message}]` — bad files; also conflicts with YouTrack (PT-7, with an empty `path`) |
| `queue` | state of the queue of commands outside the sandbox (K-39 #4, PT-374; only a service with card flow, after the first tick — an old service has no such field): `heavy` `{limit, running: [{step, card, ticket, command, since, kind, exclusive}], waiting: [{…, reason}], load: {avg1, cores, threshold, error}}` (`kind`: `outside` or `merge-check`; `exclusive` — a command that requires quiet, K-39 #6; `waiting` in start order, `reason` is a service sentence, also about quiet; `load` — one machine load reading per tick, `error` non-empty on a read error), `outside` (light) `{perProject, total, occupied: {total, projects: {slug: n}}, running: […], waiting: […]}` (`occupied` counts running ones together with the heavy ones, because they count toward the limits; light entries have `project`), `limits` `{heavyLimit, providerSlots, providerSlotsDefault}`. The `steps` part comes in #10 |
| `youtrack` | sync state (PT-7, only a service with YouTrack — without `--no-youtrack`): `{enabled, sync, url, available, error, pending, conflicts, conflictItems: [{issue, label, cards, youtrack, tide, url}], newIssues, importError, states}` |

A card also has `youtrackSync` (PT-7): `null` without a `youtrack` field, otherwise `{status, text, issue, issueState, url,
via, label, conflict}` — `status`: `ok`, `pending`, `offline`, `error`, `conflict`, `off` (a project without
sync); `via`: `card` or `ticket` (the state taken from a plan ticket, `label` says which one); `conflict`:
`{youtrack, tide}`.

`derived` is the card state from agent work (described below) or `null`; `boardColumn` is the column to
display: `derived.column`, and without it (or when empty) the stored `column`. Card files do not
change with agent state — a manual column is solely the user's decision.

**Card state from agent work** (`board/derive.py`, PT-5). A card in To do (in the Backlog — only with
an explicit link) gets a column from what the agents are actually doing; Done and Archive never.

Linking a card to work, the first match wins:

1. the card's `session` — a session from `/Agents` or a coordinator from `/Coordination` (the same id), regardless of
   where the session is now (cwd, project, branch). A pinned session that is no longer running yields to
   newer, live work of the card from rules 2, 4, 5 (a relaunch, a new session on the card's branch),
2. a launch from the card (PT-28): a session of the same tool as the card's `tool`, whose start directory
   (`launchCwd`, without it `launchKey`) is the card's `worktree` — without a worktree, the project repository — and which
   started (`startedTs`) from 2 min before to 5 min after `launched` (`LAUNCH_SLACK`, `LAUNCH_WINDOW`); if several —
   the closest to the moment of launch; subagents and sessions pinned to other cards do not count,
3. `card: K-12` in the frontmatter of the coordinator's plan (the key from PT-6, read carefully),
4. the card ID in the name of the coordinator's branch, its ticket's or the session's (`claude/k-12-eksport` — the
   `RenderFlow` proposal), in the repository of the card's project or its additional project (`projects`),
5. the card's `branch` equal to the branch of the coordinator, ticket or session in those repositories — also
   from their worktrees (the repository's main directory from `.git` files, `agents/gitinfo.py: main_root`);
   main branches (master, main, develop, HEAD) are not matched.

With several matches of the same rule the freshest activity wins (live before finished).
The Backlog is "someday": it gets a state only from rules 1–3, because a branch may match by accident or an
old piece of work.

**Persistent link** (PT-28, `Context.sticky`, `BoardMonitor`). A card without `session` that the recomputation
linked by rule 2, or unambiguously (one match — a coordinator with tickets counts as one) by rule 4 or 5,
gets that session in the file: `store.link_session`, commit `K-1: link session claude:<id> (launch)` (or
`(card-branch)`, `(branch)`). The write goes after the recomputation (`QTimer`), the same card–session pair at most once
a minute. Careful with a fresh launch: for 30 s after `launched` nothing goes into the file
(`PERSIST_DELAY`), "launch" is persisted only for a session that started at most 15 s before `launched`
(an earlier one is more likely a different session in the same directory — in memory the card will show it), and links by branch
never within the launch window (5 min — e.g. an old, finished session of that branch after a relaunch). A card
linked to some of the coordinator's tickets gets together with `session` the branch of those tickets in `branch` (when it
did not have one), so that rule 1 still covers only those tickets; tickets from several branches — we do not persist. A set `session` (also by hand) is never overwritten by the service — only an explicit relaunch
from the window clears it (`UpdateCard` after the start sends `session: ""`), and a new session is pinned by rule 2 (the old one
started outside the new `launched` window). Later the session may move
elsewhere — the card will not lose it. A real-life case: the K-1 agent did `cd` into `<board>/docs/<project>/plans`
to write a plan, and the card went back to To do with "Uruchom ponownie…" (Relaunch…), although the agent kept working. A coordinator from a card that is no longer running yields to a newer, live coordinator of the same
plan (a new session after the context ran out). When the card's branch differs from the coordinator's branch, and its
tickets have that branch, the card covers only those tickets (`tickets`) — a coordinator runs several cards at once.

| Column | When |
| --- | --- |
| `waiting` — Waiting for me | the agent stands mid-work and will not move without you: a session in the `waiting` state (the same definition as the agents widget, `agents/state.py: waits_for_user` — also `reply`: end of turn with a question in the last message with no answer, note "czeka na odpowiedź: „<question>”" (waiting for a reply), PT-29), a ticket in `czeka` with an unanswered question, or a ticket in `czeka` from the plan alone when the agent is not working (idle, finished its turn, session ended — note "czeka na twoją decyzję według planu: <Notes from the plan>" (waiting for your decision per the plan)). A question or `czeka` of a ticket from outside the card does not concern the card |
| `progress` — In progress | work is in progress or stands unfinished: tickets not done — including AI review and fixes (`review`, `w-toku`, `todo`; `czeka` from the plan while the agent is working) —, the agent is working or idle, finished its turn, the session ended (note: "agent bezczynny od 16:46, w toku: LEGAL-6" (agent idle since 16:46, in progress: LEGAL-6), "sesja zakończona" (session ended)) |
| `review` — To check | the agents' work is finished (all tickets done or rejected, plan `done` without tickets; the agent is no longer working), but a human step is needed: `check: manual` ("do sprawdzenia ręcznie" — to check manually) or an unmerged branch ("do scalenia: feat/x" — to merge: feat/x) |
| `done` — Done | finished, `check: auto` and nothing to merge ("✓ gotowe i scalone" — done and merged); the card file stays in To do — dragging to Done (or choosing it in the card panel) records it |

A plain session without tickets is never "finished" — without tickets and a plan it is not known whether the work
is done, so such a card stays In progress with a note since when the agent has been standing. A card moved by hand after
finished work from To check or Done (`moved` later than the last ticket activity and `moved-from`
`review` or `done`, e.g. "to fix" — from the panel back to To do; merely editing the note, creating
the card, setting `session` and moving from another column do not count — PT-29) stays in its column (`derived.column` empty, note "wróci do W
toku…" (will return to In progress…)) until the agent works on it again. A manual Done always wins and confirms the check.

**Merged branch**: the branch tip is an ancestor of the local main branch (`git merge-base --is-ancestor`,
as in Worktrees). A branch that does not exist locally is merged only if among the last 1000 commits
of main there is a merge "Merge branch 'feat/x'" of it (deleted after merging); otherwise — it never
existed here or the merge was fast-forward/squash — "unknown". Unknown (also a missing repository or main
branch) — "to merge", so that a human takes a look. We check only finished work: the card's branch, and without
one the branches of the tickets and the coordinator. A card with additional projects (PT-28): the card's branch (without it — the first
work branch) is also checked in every additional repository; a branch that is not there and whose merge is not visible
(`MISSING` — the agent did not change that repository) is not needed, and an unmerged one gives "do scalenia:
mobileapp feat/x" (with the repository name on every branch, the main one too). Git runs in one worker thread (`BranchState(background=True)`),
the recomputation answers from memory (30 s; a branch with no trace — 10 min, so as not to check it over and over), and a new
result triggers another recomputation; until the first result exists, a finished card stands in To check.

`derived`: `column`, `done` and `total` (the card's tickets: done / all without rejected; a plain session —
0), `tool` (`claude`, `codex`), `note` (one sentence in Polish, up to 160 characters: what is happening now, the question, since when
it has stood, what is missing), `nextStep` ("odpowiedz agentowi" (answer the agent), "przetestuj ręcznie, potem scal gałąź feat/x" (test manually, then merge branch feat/x),
"przenieś do Gotowe" (move to Done); empty in In progress), `check`,
`branch` (the card's or work branch; in To check — the branch to merge), `session` (id for "Otwórz
w Sesjach" (Open in Sessions) / "Przejdź do agenta" (Go to agent): the coordinator or session), `coordinator` (the coordinator id or empty —
a plain session), `link` (`session`, `plan`, `card-branch`, `branch`), `agentState` (session state from `/Agents`),
`tickets` (IDs of the card's tickets when it covers part of the coordinator; an empty list — all), `question` (the question this card
is waiting for: the question of its ticket, the coordinator or the session; empty for tool approval, waiting per the plan
and outside "Czeka" — the "Praca" (Work) list takes the question from here, not from an arbitrary coordinator question, PT-40).

**Live.** `BoardMonitor` keeps the snapshot from files in memory; a change in `/Agents` (`AgentMonitor.changed`)
or `/Coordination` (`CoordinationMonitor.changed`) recomputes only `derived` and `boardColumn`, at most every
500 ms (a series of session changes is one recomputation), without reading the board repository, and sends `Changed`
when the snapshot changed. The indexes (repositories without symlinks, `(repository, branch)` → work, card number
from the branch name) are built once per recomputation, so a card is a few dictionary lookups (80 cards,
60 coordinators with 15 tickets each and 40 sessions — a few ms; the `SpeedTest` test). The badge (`Badge()`) and the tile count from `boardColumn`, so they follow it.
`--print-board` shows the snapshot without agent state (without scanning logs).

**The "Praca" (Work) list** (PT-40, `board/work.py`, `Work()`, decision 76). One truth for the Praca tile, which replaced
the Tablica and Agenci tiles (earlier each counted its own way — the board "5 waiting", the agents "1 waiting · 4 done").
`BoardMonitor` computes it in the same recomputation as `derived` and `boardColumn`, from the same derive context
(the same sessions from `/Agents`, the same coordinators), so the numbers cannot diverge from the Board. A separate method
and a separate `WorkChanged` signal instead of a field in `Snapshot()`: the tile does not parse the whole snapshot with card descriptions on every session change,
and the Tide window and the report do not fetch the snapshot when only the list changed (e.g. a session without
a card started waiting). The agent's last activity changes with every tool, so its change alone gives
`WorkChanged` at most every 10 s; `Work()` always returns the current state. An error while computing the list never breaks
the board (at worst an empty list).

- **Cards:** the displayed column (`boardColumn`) `waiting`, `progress`, `review` or `todo` — without the Backlog, Done
  and Archive; group = that column. The link to a session comes only from the card's `derived` — `work.py` has no rules of its own.
- **Sessions without a card:** a live session (state other than `ended`) that no card points to — neither `derived.session` /
  `derived.coordinator`, nor the card's `session` field (also in Done and Archive, which get no state derivation;
  the bare session id counts too). `waiting` → `waiting`, `working` → `progress`, `done` and `idle` → `finished`.
  One session running two cards is two card items and no session item; Codex subagents are already collapsed.
- **A waiting session is always in `waiting`:** a session in the `waiting` state that no card from the `waiting` group covers
  (its card is in Done and the session was resumed; the coordinator asks about a ticket from outside the card, so the card is In progress),
  gets a session item with that card's `card` and the note "karta K-2 w Gotowych" (card K-2 in Done) / "karta K-1 w toku" (card K-1 in progress) (without `cardRequest`).
  The tile must not show "0 waiting" when someone in `/Agents` is waiting.
- **Order:** groups `waiting`, `progress`, `review`, `todo`, `finished`; within a group cards first (board
  order), then sessions — waiting ones from the longest waiting (like Meta+A), the rest from the freshest state change (`sinceTs`).

```json
{"exists": true,
 "counts": {"waiting": 2, "progress": 3, "review": 2, "todo": 3, "finished": 2, "total": 10, "sessions": 2},
 "items": [
  {"kind": "card", "id": "K-20", "card": "K-20", "title": "Twitch: czat i alerty", "project": "twitch",
   "projectName": "twitch", "group": "waiting", "note": "czeka na odpowiedź: „Czy taki podział…?”",
   "question": "Czy taki podział…?", "nextStep": "odpowiedz agentowi", "agentState": "waiting",
   "session": "claude:6b1f…", "canActivate": true, "sinceTs": 1793191980,
   "done": 0, "total": 0, "tool": "claude", "branch": "feat/k-20-moduly"},
  {"kind": "session", "id": "codex:019a…", "card": "", "title": "pricingapp · feat/rabaty", "project": "",
   "projectName": "pricingapp", "group": "waiting", "note": "shell · git push", "question": "Mogę zrobić push…?",
   "nextStep": "odpowiedz agentowi", "agentState": "waiting", "reason": "reply", "session": "codex:019a…",
   "canActivate": true, "sinceTs": 1793191500, "done": 0, "total": 0, "tool": "codex",
   "branch": "feat/rabaty", "subagents": 0,
   "cardRequest": {"title": "pricingapp · feat/rabaty", "column": "todo", "session": "codex:019a…"}}]}
```

| Item field | Description |
| --- | --- |
| `kind`, `id`, `card` | `card` (the card ID) or `session` (the session ID from `/Agents`; `card` empty — a session without a card, otherwise the card of a waiting session from outside "Czeka") |
| `group` | `waiting`, `progress`, `review`, `todo`, `finished` (only sessions without a card, outside `counts.total`) |
| `title`, `project`, `projectName` | the card title or session title (Claude Code), otherwise "project · branch"; the board project (session: the project of its repository, if it is on the board) |
| `note`, `nextStep` | the note and next step from `derived` (session: the last activity; the session of a card from outside "Czeka" — where the card is) |
| `question` | `waiting` only: for a card — `derived.question` (this card's question; for tool approval or a plan it is empty, the description is in `note`), for a session — its `waitingText` |
| `agentState`, `sinceTs` | the agent state (`derived.agentState` or the session state) and since when it has lasted (session; a coordinator without a live session — its last activity) |
| `session`, `canActivate` | the target of "Przejdź" (Go; `/Agents ActivateSession`; for a card — `derived.session` or the coordinator) and whether it is possible to go (a live session with a known window — the tile then shows "Przejdź" also for cards In progress and To check) |
| `done`, `total`, `tool`, `branch` | ticket progress, tool, branch |
| `cardRequest` | only a session without a card (not an orphaned subagent): a ready `CreateCard` request for "+ karta" (+ card) — title, the To do column, `session` (derive links the card at once, rule 1) and `project` when the session's repository is a board project; otherwise a card without a project (no silent project creation). `CreateCard` with a session that an active card already has (the `session` field, not in Done or Archive) does not create a second one — it returns `{ok: true, id, existing: true}` |

`counts.total` is everything in play except `finished` ("Praca · 10"), `counts.sessions` — how many of it are sessions without a card.
Without a board (`exists: false`) the list has only sessions without a card; a service without `/Agents` — only cards.

**Synchronization with YouTrack** (PT-7, `board/ytsync.py`, `ytsync_monitor.py`; replaces the old "YouTrack read-only"). The user: "it should change dynamically, update youtrack". Tide is the source of state,
YouTrack (private: the user and a household member) receives the state of cards and tickets.

- **What:** a card with the `youtrack` field and a plan ticket with `tracker: youtrack` whose ID is the issue key —
  only in YouTrack projects that have a board project with a key (`youtrack: LOG`) and sync
  enabled, and only with `sync: true` in the settings (the default). Archive — no changes in YouTrack.
- **Map** (keys in `states`, default names from the LOG and LEGAL workflows): card — Backlog and To do →
  `open` (Open), In progress → `progress` (In Progress), Waiting for me → `waiting` (To be discussed), To check →
  `review` (Fixed — a human step: manual test or merge), Done → `done` (Verified); ticket — `todo` →
  Open, `w-toku` and `review` (AI review is still In progress) → In Progress, `czeka` → To be discussed, `gotowe` →
  Fixed (Verified when the card that runs it stands in Done — also moved there by hand: the card's coordinator
  is looked up by `derived`, the card's `session` and the plan's `card` key), `odrzucone` → `rejected` (Won't fix).
  A `gotowe` ticket from subagent work alone without passing review (fixes after review finished, re-review
  not yet started) with a running coordinator that uses review is still In Progress. A return to
  `open` after a resolved state is `reopened` (Reopened), not Open.
- **Precedence:** the ticket before the card of the same issue (more precise), unless the card stands in Done
  (a manual Done wins) or was moved by hand (`moved-from`) after the last work on the ticket
  and plan change. A card linked to an umbrella issue synchronizes by itself. The same ticket with several
  coordinators — the freshest work. A ticket state taken from YouTrack itself (`source: tracker`) does not count,
  so nothing that came from YouTrack goes back to it (no ping-pong).
- **Transitions only:** the state file `$XDG_STATE_HOME/tide/youtrack-sync.json` (0600; no token) remembers for
  an issue the last state written or accepted by Tide (`state`), its key (`key`), the issue's `updated`
  from that moment, the time (`ts`) and a possible conflict; plus `seen` — issues already shown in the import — and `importBaseline` (the reference point of the new-issues counter). A write
  goes when the key Tide wants differs from the remembered one and holds for 5 s (a series of changes is one write).
  Momentary states from agent work — resolved from tickets and from computed columns (To check, Done after
  merge) and To be discussed (every approval question) — must hold for `stableMinutes` (10 min); the
  card then shows ⟳ "zapisze 'Fixed' w YouTracku, gdy stan utrzyma się 10 min" (will write 'Fixed' in YouTrack when the state holds for 10 min). A manual move to
  Done and a return "do poprawy" (to fix) go after 5 s.
  A card that dropped to To do or the Backlog only because its work disappeared (a coordinator outside the
  7-day window, service start — `derived` without a column) does not roll the issue back: only an explicit move counts
  (`column-moved` no earlier than the last Tide write, to the second). `column-moved` is stamped by
  `MoveCard` only on a real column change (the target other than the displayed one); a reorder in To
  do does not change it, so it does not release the hold.
- **Before a write** `GET /api/issues/{id}?fields=idReadable,summary,updated,resolved,project(shortName),customFields(name,$type,value(name))`:
  the state is already what Tide wants — only remembering; a state other than the remembered one — a **conflict** (someone changed
  it by hand): no write, a note on the card (⚠, "YouTrack: stan zmieniony ręcznie — w YouTracku '…', Tide: '…'" (state changed manually — in YouTrack '…', Tide: '…')),
  an entry in `errors` and in "Projekty i tagi", until the user chooses "Weź z YouTracka" (Take from YouTrack; Tide accepts the state, the write
  only on the next card change) or "Nadpisz z Tide" (Overwrite from Tide; `ResolveYouTrack`), or YouTrack itself reaches
  Tide's state (checked every 5 min and on every new change). The write:
  `POST /api/issues/{id}?fields=…` with `{"customFields": [{"name": "State", "$type": "StateIssueCustomField",
  "value": {"name": "In Progress"}}]}` — the field's name and `$type` from the read (the shape verified by reading a real
  issue), the result must have the new state.
- **First encounter** of an issue (no entry in the file): a write only if Tide knows something about the work (not
  `open`), the issue is unresolved (`resolved` empty), in a different state and nobody changed it after linking
  (`updated` at most a minute after the card was created or the first ticket event); otherwise Tide accepts the state
  from YouTrack. An imported card in the Backlog thus does not roll "In Progress" back to "Open".
- **Without network:** YouTrack on the local network is sometimes off — no response, 5xx, 401/403/408/429 mean a retry after
  5, 15, 60 and every 300 s (the ⟳ badge and the note "YouTrack niedostępny · N czeka" (YouTrack unavailable · N waiting) by the board header); the transition
  waits in the state file, also across a service restart. A refusal (other 4xx, e.g. a state outside the workflow) — ⚠ with YouTrack's
  description and a retry after 30 min or on a new state.
- **When:** a 1 s clock asks the engine what has matured; the network runs in one worker thread (`tide-youtrack`), the main thread
  never waits. The first write after start only after the first coordination read and 20 s
  (`TIDE_YOUTRACK_START_GRACE` in tests) — before that the cards have no state from agent work yet. The sync
  clock is real also with `--now`.
- **Import** (`YouTrackIssues`, `ImportYouTrack`; "Importuj z YouTracka" (Import from YouTrack) in "Projekty i tagi" and the note "N nowych
  zgłoszeń w YouTracku" (N new issues in YouTrack) by the header): the user's issues — unresolved, `for: me` and those reported by
  them without an assignee (`reporter: me Assignee: Unassigned`; in this YouTrack issues have no assignee),
  both queries in `importQueries` — the field `fields=idReadable,summary,description,updated,resolved,project(shortName),customFields(name,value(name))`.
  A list with selection (key, title, board project by the YouTrack project key, state, "new", "already there: K-13");
  card: Backlog, `youtrack`, title (one line, up to 200 characters), description as plain text (up to 60 KB, without control
  characters), project by key. No duplicates: an issue with a card (by `youtrack`, also in Archive) is
  skipped. The list shows everything that has no card, with a text
  filter and a switch for YouTrack projects. Every `importCheckMinutes` (15, 0 — disabled) the service counts
  "new" issues in the background — it creates nothing by itself. The counter has a reference point: the first successful fetch records
  `importBaseline` in the state file (the newest `created` from that list, server time), and new ones are only issues
  created later, without a card and not yet shown in the list (showing the list marks them as seen).
- **Token** as in coordination: from a file on every request, only in a header, never in the journal, errors,
  state file or D-Bus responses. `--no-youtrack` disables sync and import (the methods answer
  `unavailable`).

```json
{"url": "http://10.20.1.159:8111", "sync": true,
 "states": {"open": "Open", "reopened": "Reopened", "progress": "In Progress", "waiting": "To be discussed",
            "review": "Fixed", "done": "Verified", "rejected": "Won't fix"},
 "stateField": "State", "projects": {"APP5": {"states": {"review": "In Review"}}}, "stableMinutes": 10,
 "importQueries": ["for: me #Unresolved", "reporter: me Assignee: Unassigned #Unresolved"], "importCheckMinutes": 15}
```

Going from a card to the work: "Otwórz w Sesjach" (Open in Sessions) calls `/Coordination ShowWindow(id)`, and "Przejdź do agenta" (Go to agent)
calls `/Agents ActivateSession(id)`. `BoardView.qml` and the board backend route these two actions to the service.

**Launch from a card** (the Board view, `apps/board/qml/LaunchDialog.qml`). The window "Co ma zrobić agent?" (What should the agent do?)
asks "Jak zacząć?" (How to start?): Planowanie (Planning; Full or Light), Mam plan (I have a plan), Tylko specyfikacja (Spec only) or Od razu (Immediate).
It opens after a card is moved from the Backlog to To do (with the mouse or Ctrl+→), from "Uruchom agenta…" (Launch agent…)
on a card in To do and from "Uruchom" (Launch) in the tile popup (`ShowWindow("launch:K-12")`).

"Uruchom agenta" (Ctrl+Enter) first saves the changed description, additional projects and the chosen plan file
through `UpdateCard`, only if the data differ from what is saved. Then Light Planning, I have a plan and Immediate
call `/Board StartCard`: the step runs in the background in the card flow, without opening the Console. "Mam plan"
passes `entry: import` and `planPath`; the service saves a snapshot of the source without editing the project file, keeps
existing IDs and ticket states, completes an incomplete plan in the background and routes the proposal to review and approval.
Spec only (PT-203) passes to `RenderLaunch` the pair `baseRef` (full
`refs/heads/…` or `refs/remotes/…`) and `baseCommit` (the expected SHA) of the card's project.
The shared base policy refuses after a ref is moved or deleted and for symbolic refs;
the window requires the choice to be refreshed. The prompt contains the ref/SHA and `git show`
and `git ls-tree` instructions for reading also files absent from the checkout. Additional projects
keep their bases and reads. The card stores separate `spec-revision-repo`,
`spec-revision-ref`, `spec-revision-commit`, visible in the snapshot and the response as
`specRevision: {repo, ref, commit}`. This is entry context, without initialBase,
`card.started`, ticket numbering, branch/worktree and checkout switching.
A repeated RenderLaunch without an explicit choice validates the saved context; older calls
without context keep the existing behavior. The way the specification is saved stays the same.

Full planning and Spec only use the existing Console path: `RenderLaunch` → `/Tasks Start`
→ saving the branch, worktree and session on the card through `UpdateCard`. The full planning process in the card flow
belongs to the second stage. A card launched from the Backlog through the popup goes to To do; a response error
stays in the window, and a retry does not save the description a second time.
"Anuluj" (Cancel) after the move puts the card back in its place in the Backlog (`MoveCard`), "Tylko przenieś" (Only move)
leaves it in To do without an agent. A card without a project with a repository — an explanation and project choice
(`UpdateCard {project}` or `{repo}`, which creates a project).

**Screenshots and model in the launch window** (PT-43, decision 73) — the same as in the "Nowe zadanie" (New task) window.
Under the task a screenshot strip (`Tide.ScreenshotStrip` from `widgets/lib`): an image from the clipboard (Ctrl+V in the task
field or outside the window's text fields), files dragged onto the window, "Zrób zrzut" (Take screenshot; Spectacle; the Tide window
hides while selecting), "×" removes. The view backend (`apps/board/backend.py`, `ImageTray`
from `apps/common/taskimages.py`) saves them in the task directory in `imagesDir` from `/Tasks Defaults`, and the paths go
in `images` of the `/Tasks Start` request for the Console or `/Board StartCard` for the card flow.
A successful start leaves the directory to the agent; Cancel, "Tylko przenieś" and closing the Tide window remove it. Next to the
tool, a choice "☁ Chmura / ⌂ Lokalny" (Cloud / Local) from `Status()` of the `/Models` object (refreshed after `Changed`; by default
the remembered `model` from `Defaults()`): with "Lokalny", instead of the tool switch, the line "Codex na
qwen3-coder:30b · Ten komputer" (Codex on qwen3-coder:30b · This computer) with a link to the Models view, `RenderLaunch` gets the local agent program
as `tool` (branch `codex/…`), and `Start` — `model: "local"`; for a card step `StartCard` gets
the chosen executor from the model catalog. An unavailable model or a missing program
(`tools` from `Defaults()`) blocks the start with the reason in the footer. Screenshots are not saved in the card.

**Additional projects** (PT-28). The "Dodatkowe projekty" (Additional projects) row in the window: chips of the selected ones (✕ removes) and the field
"+ dodaj projekt albo repozytorium" (+ add project or repository) with a list of board projects with an existing repository (excluding the main one
and those already selected), then repositories from the projects directory that are not yet projects (choosing one
creates a project). The window starts with the card's `projects`. When the selection differs from the card, the first write is
`UpdateCard {description?, projects}` (repository paths create projects). For Console entries `Start`
gets `addDirs` from the `RenderLaunch` response; in the card flow additional projects are read-only.
The footer appends "Pracuje też w: …" (Also works in: …). On a board card additional
projects are small chips "+ mobileapp" next to the project name. On the Console path the agent works in the worktree
of the main project, and in the additional repositories it creates a branch with the same name.

**Plan and specification on a card** (`CardDocs(id)`, `board/docs.py`, PT-5). The card panel in the Board view
has the tabs "Opis" (Description) | "Plan" | "Specyfikacja" (Specification) — the last two only when the service found a document.

- **Plan** (first match): a plan with `card: K-12` in the frontmatter (standard 1.1; with several —
  an unfinished one before a finished one, then the fresher `updated`), otherwise the plan of the coordinator linked
  to the card (`derived.coordinator` → the `plan.path` of its board in `/Coordination`, also without the `card` key).
  We search in `docs/agents/plans/` of the card's project repository and each of its worktrees (the list from the
  `.git/worktrees/*/gitdir` files, without calling git) and in `<board>/docs/<project>/plans/` (a card without a
  project — `docs/_inbox`; the board from `--board-dir`, otherwise `$TIDE_BOARD_DIR`). The same file in several
  worktrees is one plan — the copy with the newer `updated` wins, then the newer file. A card without a repository
  linked to a coordinator (e.g. through `session`) uses the coordinator's repository.
- **Specification**: the card's `spec` field (absolute or relative to the repository, its worktree, the board
  directory or `docs/<project>` in the board), otherwise a file in `docs/agents/specs/` of the repository and worktrees
  or `<board>/docs/<project>/specs/` named after the card ID (`k-12.md`, `k-12-…`, `k-12_…`) or
  with `card: K-12` — a ready one (`status` as in "Ready specification") before a draft, then the newest.
- **Security**: only regular `.md`, `.markdown`, `.txt` files whose path after realpath lies
  in the project repository, its worktree or in `<board>/docs` — a symlink leading outside them,
  FIFOs and other extensions are dropped (the `spec` field and the coordinator's plan — with a warning in `warnings`).
  From a file at most 512 KB (a longer one — `truncated: true` and a note at the end of the content), from a directory at most
  400 files. A file is re-read only after a change of size or time, the directory listing — after a change of
  the directory's time, so refreshing the panel is a few `stat()` calls. The read runs in one worker thread,
  the D-Bus response is delayed (as in `/Docker`).
- **Content to show** (`markdown`, `manualTest`): without frontmatter, without the first `# …` heading
  (the title is in the panel header), for a plan without the ticket table (it is in `tickets`), with the
  "Co przetestować ręcznie…" (What to test manually…) section extracted into `manualTest` (up to a heading of the same or a higher level) —
  otherwise **source, unsanitized** Markdown. Only the window (below) shows it safely: the same
  parser that renders it (md4c in Qt) decides what is an image or HTML — regular
  expressions on the service side diverged from it (unclosed backticks, fences in lists,
  an image description spanning several lines). The service runs on `QCoreApplication`, and `QTextDocument` needs
  `QGuiApplication`, so rendering is in the window. The patterns in `docs.py` (and `_plain` in `plan.py`) are
  linear — a 512 KB line with thousands of `![`, `[`, `<!--` takes milliseconds; the content to show is computed once per
  file version. Further requests for the same card during a read wait for the same result.

The response: `{ok: true, card, plan, spec, others, warnings}`; a bad ID — `bad-request`, an unknown card —
`not-found` (error shape as in writes).

| Field | Description |
| --- | --- |
| `plan` | `{path, title, status, updated, updatedTs, card, coordinator, link, where, repo, mtime, tickets, cardTickets, errors, markdown, manualTest, truncated}` or `null`; `link`: `card` (the `card` key) or `coordinator`; `where`: `repo`, `worktree`, `board`; `repo` — the main directory of the project repository (empty without a repository); `tickets`: `[{id, title, state, branch, agent, notes}]` from the plan table; `cardTickets` — `derived.tickets` (the card covers part of the coordinator's tickets; empty — all); `errors` — plan errors from `coordination/plan.py` |
| `spec` | `{path, title, status, ready, updated, card, link, where, mtime, markdown, manualTest, truncated}` or `null`; `status` from the frontmatter in lowercase, `ready` — ready; `link`: `field`, `card`, `name` |
| `others` | up to 8 further card documents (older plans with `card`, remaining specifications), newest first: `[{kind: plan\|spec, path, title, status, updated}]` |
| `warnings` | sentences in Polish: the file from the `spec` field does not exist or lies outside the allowed directories, the coordinator's plan outside them |

`--print-card-docs K-12` prints the same without D-Bus (without coordinators — a plan only from the `card` key).

The panel (`apps/board/qml/CardPanel.qml`, `CardDocView.qml`, `MarkdownBlock.qml`, logic in `cardDocs.js`):
the last selected tab stays for subsequent cards as long as the Tide window runs (a card without such a document
shows "Opis"); with a document the panel is 860 px wide (with a narrower window — as wide as there is), "Rozwiń" (Expand) — the window width without
margins. CardDocs goes out when a card is opened, after every new snapshot (`Changed` — also a file
in the board's `docs/`) and every 5 s while the panel is open (a plan in the project repository does not change
the snapshot); the content is swapped only on a different response, so the scroll position stays. "Otwórz plik" (Open file)
(`QDesktopServices`) and "Pokaż w katalogu" (Show in folder; `org.freedesktop.FileManager1.ShowItems`, without it —
the directory) accept in the backend only paths from the CardDocs response; a link from Markdown opens
only as http(s) (other schemes and paths — just a tooltip). The panel does not ask for CardDocs until the
previous response for the card has returned.

Rendering (`apps/board/backend.py: render_markdown`, `renderMarkdown` for QML, memory of the last 24
documents): `QTextDocument.setMarkdown(…, MarkdownDialectGitHub | MarkdownNoHTML)` — HTML from the source
stays as text; every fragment with `QTextImageFormat` (also an image from a link definition and a description spanning several
lines) → in italics "(obraz: opis)" (image: description); a link other than http(s) → plain text; headings with spacing above them,
links in sky; `toHtml()` without the `<body>` style (the typeface from TextEdit) goes to `MarkdownBlock.qml` as
`RichText` — the only place in the view with RichText. A second layer: the Tide window's QML engine has a network manager
that refuses every request except `file:`, `qrc:`, `data:` (`apps/tide/tide.py: guard_network`), so
even an overlooked image will fetch nothing.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.CardDocs K-12
/usr/bin/python3 daemon/tide-daemon.py --print-card-docs K-12 --board-dir /tmp/kopia-tablicy
```

The logic without Qt is in `tide_daemon/board/` (`marker.py`, `frontmatter.py`, `model.py`, `flows.py`,
`store.py`, `derive.py`, `docs.py`, git in `repo.py`), directory watching, recomputing card state and D-Bus responses
in `board_monitor.py`.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.Snapshot
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.CreateCard '{"title": "Eksport do PDF", "repo": "/home/user/DevWorkspace/LogViewer"}'
/usr/bin/python3 daemon/tide-daemon.py --print-board            # snapshot without D-Bus, creates nothing
/usr/bin/python3 daemon/tide-daemon.py --print-board --board-dir /tmp/tablica-testowa
```

### Approving a card plan (PT-78)

`StartCard(K-12, {"entry":"plan","planPath":"/path/to/plan.md"})` accepts a proposal
from the project repository, the board documents or the output directory of this card's step. The ticket sheets lie
in `tickets/1.md`, `tickets/2.md` next to the plan. The service checks standard 1.2, executors, reviewers,
sheets with criteria, blockers and cycles. It records `plan.checked` with a list of errors or `plan.proposed`
with a snapshot in the card journal; a correct plan gives the "Czeka na mnie" (Waiting for me) column and waves in the panel.
At this stage `PT-n` numbers are not created.

`ApproveCardPlan(K-12)` reserves numbers under durable request keys, records `plan.approved`
with the `#n → PT-n` map and renames the sheets in the snapshot. The approved 1.2 plan and sheets are committed
on the card branch under the repository lock, through the git sandbox. Thanks to this the existing reading of
plans from all worktrees sees the new IDs in Sessions, the report and the number check. A ticket starts
only after all its blockers are merged; each goes through review and merge since PT-59.
An interruption between the reservation, the map entry and the plan commit restores the same plan and numbers.

**Second approval in progress (PT-407, K-102).** The flow monitor keeps a set of cards with plan approval
in the change queue or in progress (main thread, from acceptance to delivery of the response; cleared on success,
`FlowError`, an exception and a "service is shutting down" refusal). It is entered by `ApproveCardPlan` (the plan and the plan diff —
the same method) and `ApproveCardEnvironment`, which would approve the plan. A duplicate of any of these methods for the same
card immediately gets `{"ok": true, "id": "K-n", "approving": true, "message": "Zatwierdzanie trwa…"}` (Approval in progress…)
and does not wait in the queue. The snapshot: `derived.flow.plan.approving` is `plan.approval_requested` of the current proposal without
`plan.approved`, without a newer failed `reserve` result of that proposal and without a suspended base decision (PT-208);
`approvalError` is the text of the last reservation error or the reason "Zatwierdzanie wstrzymane: czeka decyzja o bazie
karty" (Approval suspended: waiting for a card base decision). `status` gets no new values. Approving a plan diff does not record `plan.approval_requested`,
so the snapshot does not show `approving` for it; the client learns its end from the call's response. During it
the card phase is "zatwierdzanie planu w tle" (approving the plan in the background), and the `plan-approval` decision is not offered. A known limitation:
`approve_plan` errors after the request is saved that do not reach the journal as a `reserve` result (a project removed
from the configuration, `OSError` when moving ticket sheets) do not end "in progress" — the tick retries, and the card
shows "zatwierdzanie planu w tle" without `approvalError` and without `plan-approval` until the cause goes away.

### Light Planning and assignment rules (PT-79)

`StartCard(K-12, {"entry":"planning","mode":"light","executor":"Opus","preferredProvider":"mixed"})`
launches the `planning` step in the background. The assignment window has a choice of planner from the catalog, independent of the preferred
provider of future tickets; with "Tylko lokalnie" (Local only) it shows only local planners. The step uses
the card's branch and worktree, and saves results in its `out` directory. It does not open the Console or assign numbers.

The input contains the card, screenshots, the executor catalog with classes, the provider preference and a snapshot of the assignment rules.
Additional projects are available read-only only; they do not widen the sandbox write directories. The template leads
through the full `to-spec` sections and the vertical slices of `to-tickets`, with blockers and a separate "Dla recenzenta" (For the reviewer) sheet per ticket.
It overrides publishing from the skills: `spec.md`, `plan.md`, `tickets/1.md` etc. and `result.json` go to `out`, without GitHub
Issues, a tracker or a local `.scratch`. The proposal is read by the same validator as in PT-78, with an additional restriction of the
source to the result directory of the specific step. A correct result gives a plan awaiting approval; approval leads on
through the existing reservations, ticket review and merging. Review of the plan itself before showing it is added by PT-80.

**Assignment projects (K-33 #4, PT-115, decision 94).** A Planning request (Light and Full) may have
`projects` — additional assignment projects (slugs or repository paths, like `projects` in UpdateCard); those that differ
from the card go into the card's `projects`. The service checks every assignment project before the first event: a project without
a repository or with an unavailable one ends the assignment with a `no-repo` error naming the project. `card.started` has `projects`:
`[{slug, name, repo, prefix, tracker, trackerProject, configured, source, main}]` (the first is the card's project;
`configured: false` — the repository numbers in YouTrack, and the Board project has no YouTrack key). The planning step
works in the card's worktree, and gets the repositories of the other projects read-only: Claude through `--add-dir`
with `Edit(//repo/**)` rules in `--disallowedTools` (in the background also the sandbox's `allowRead`, `allowWrite` only `out`),
Codex without `--add-dir`. The prompt lists the projects with repository, prefix, tracker and numbering source and demands a
standard 1.3 plan with a `Projekt` (Project) column. Proposal checking rejects a project outside the assignment, a ready ID with the prefix
of another project, a blocker cycle, more than 6 projects and a YouTrack project without a tracker on the Board; the message starts
with the row ID. `plan.proposed` and `derived.flow.plan` then have `projects` (with the numbering source), and the rows a `project`
field. `ApproveCardPlan` of a plan with tickets in several projects answers `group-approval` ("zatwierdzanie grupy kart
— w następnej wersji", approving a group of cards — in the next version) without writing to the journal. The `now` and `import` entries reject a non-empty `projects` (`single-project`).

A wrong format records `plan.checked` and launches `plan-fix` with the errors and the previous proposal, at most two fixes.
After two failed fixes the card waits for the user with the errors in the panel; a new planning can be assigned. A program
failure, a question and a missing consent use the existing step contract (one retry of a failure, then a user
decision). The saved `card.started` and "intent → result" pairs allow finishing a start after a restart without duplicates.

The assignment rules are a short text in the classes `lekki` (light), `standard`, `mocny` (strong), `lokalny` (local). The shared ones fit in
`board.md`, a project override in `projects/<slug>.md`, under the key `assignment-rules` (a JSON string in flat
frontmatter, so as to keep many lines). A new board gets a seed, an older one uses the same seed
until the user saves it. `Snapshot.assignmentRules` gives the shared rules, the project's `assignmentRules`
gives only the override; an empty text means inheritance. "Projekty i tagi" edits both scopes through `UpsertProject`:
`{globalAssignmentRules:"…"}` saves only the shared ones, `{slug:"…",assignmentRules:"…"}` a project override.
The text limit is 8000 characters. Clearing an override restores inheritance of the shared rules.

### Waves and step parallelism (PT-62)

A wave is a level of the blocker graph and always follows from the plan table; the journal does not record the wave number.
Tickets ready for work start in parallel when **all their blockers have been merged** into the card branch.
Review, fixes and retries also use provider slots, shared by all cards.
Git operations are still performed under the single repository lock from PT-59.

By default Anthropic and OpenAI (and other providers) have three slots each. The optional field
`providerSlots` in `$XDG_CONFIG_HOME/tide/cardflow.json` allows overriding a positive number of slots per provider,
e.g. `{"providerSlots":{"anthropic":3,"openai":2}}`. Invalid values keep the default limit.
Locally at most one step always runs: it occupies the model queue lane for the whole session.
Waiting report and card description calls start before the next step; a step in progress is not interrupted.
After a restart the occupied slot is restored from the journal.

A missing slot records `step.waiting` with a named reason; the step start removes that reason from the projection.
`derived.tickets` gives the mini-bar states and waves, `derived.flow.waveLabel` — the "fala N z M" (wave N of M) tag,
`derived.flow.waiting` — the waiting reasons, `derived.flow.steps` — the course with executors,
and `derived.flow.timeline` — an ordered axis of events. The axis contains presentation fields, without point configuration.
One ticket's question is visible and can be answered while other tickets are working.

The panel calls `/Board SetCardPaused(id, true)` for "Wstrzymaj kartę" (Pause card) and the same method with `false` for "Wznów" (Resume).
Pausing does not change the manual column or interrupt steps in progress; it blocks the start of new steps.

### Card flow: "Od razu" (Immediate) (PT-58)

The first slice of the card flow (`docs/agents/specs/2026-10-28-przeplyw-kart.md`): the "Od razu" entry in the assignment
window with an executor from the catalog (Claude, Codex, local or another provider). The window calls
`/Board StartCard(id, {"entry": "now", "executor": "Sonnet", "preferredProvider": "anthropic", "images": [...]})` instead of RenderLaunch → /Tasks Start;
the response comes after the step starts (delayed, work in the `cardflow_monitor.py` worker thread).

Assignment (`cardflow/engine.py`): first a check without writing (a card with a project and repository, an executor
from the catalog, an available program and model, for a local one also the current server state and the presence of the model,
the project's ticket prefix through `reserve(dry_run=True)`, the main branch, no step of the card in progress
— an error here leaves nothing behind), then events in the journal: `card.started`, "intent → result" pairs for reserving
a number (`coordination/reservations.py` — the number check sees the ticket), the card's branch and worktree
(`card/k-12-<description>` from the main branch), the ticket's branch and worktree (`ticket/pt-7-<description>` from the card branch), the step
start, finally `step.started`. Worktrees lie in `<repo>-worktrees/`, the step directory next to the worktree (`<worktree>.step`,
`in` — sheet, settings, gitconfig; `out` — the result file and notes). A step is (through `Runner`, below) `agentcmd.background()` +
`Background.start()` (PT-76): `claude -p` or `codex exec` in a `systemd-run --user --scope` unit
`tide-step-<K-12>-<PT-7>-<n>`, with a sandbox and the sheet on stdin. Claude gets
`--permission-mode acceptEdits`, Codex an optional `model_reasoning_effort`. Another provider gets the token
from a file outside the program arguments and the journal; a local one does not go to the cloud. Without the Console.

**Runner** (`tasks/runner.py`, PT-180). The engine does not call `agentcmd` or `stepstop` to start and stop a step,
only an injected `Runner` (`FlowEngine(..., runner=)`, by default `HostRunner(systemd_run)`): `start(step,
before_start=)` assembles the step (`agentcmd.background`), prepares directories and files, calls `before_start` (copying
screenshots) and launches the program — it returns `Started(background, proc)`, and `proc` (Popen) goes into the engine's `procs`;
`kill(step, proc=, grace=)` stops the unit from the journal entry (`stop_step` gets it as `kill=`, so the WIP
commit and messages stay as before); `env(step)` returns the environment of the step program (`agentcmd.background(step).env` — the same assembly path as in `start`, with
local-point substitutions, without secrets from `secret_env`). A Console step (`_launch_full_planning`, `resume_interactive`:
`agentcmd.interactive` + task file) does not fit the `start → Background` contract and stays outside `Runner`. `HostRunner`
is today's way unchanged: argv and environment are guarded by the golden file `tests/fixtures/runner/host_argv.json`
(a record from before the refactor). The format of step entries (`unit`, `gitDir`, `stepDir`) and reading the state after a service restart
(`stepstop.unit_state`, `read_exit_code`) are unchanged, so steps launched before an update are still recognized.
There is no runner choice — this is the place for the next runner (the card box).

The sheet (`cardflow/handoff.py`): the card's title, description and screenshots, branch and working directory, prohibitions (push, merge, writing
outside the working and output directories) and the result file `<step>/out/result.json`:
`{"result": "done"|"question"|"denied"|"error", "question", "command", "reason", "notes": ["note.md"]}`.

Every 5 s `tick()`: the step's unit inactive → the step ends from the result file (no file → `no-result` error,
a bad one → `bad-result`; the service does not guess the result from the conversation), the result and notes go to `flow/<K-12>/steps/<step>/` in the board;
"done" → an automatic review step by a different model (PT-59), and only an APPROVE verdict → merging the ticket into the card branch (an "intent → result" pair, `cardflow/gitops.py`: `merge-tree --write-tree`,
`commit-tree`, `update-ref` with comparison — without executing the agent's content; the card worktree gets the new tree through
`read-tree -m -u`). The step guard: no file changes in the worktree (without `.git`) and in the session journal for
`stepGuardMinutes` (default 15, `$XDG_CONFIG_HOME/tide/cardflow.json`) → `stepstop.stop_step` (a WIP commit by git
in the sandbox, and without `bwrap` the changes stay in the worktree) and a `no-progress` step error.

The card journal (`cardflow/journal.py`): `flow/<K-12>/journal.jsonl` in the board repository, one line per event
(`v` — schema version, `seq`, `ts`, `card`, `type`), appended only by the service. A copy in
`$XDG_STATE_HOME/tide/cardflow/<hash>/<K-12>.jsonl` is written first: a journal rolled back by a revert or changed
from outside comes back from the copy with a warning in the snapshot's `errors`. Changes from one moment form one commit "K-12: card flow";
`BoardMonitor.sync()` commits them before "sync external changes".

**Strict-read memory (K-105 #2, PT-411).** The strict read of a card's history (`Journal.read_complete`: whole lines,
schema, consecutive `seq`, the same card, a known process start identity; an error = unknown) remembers events for a journal
version (`Journal.version`, `JournalVersion`): the source (the service copy or the file in the Board), `st_dev`, `st_ino`,
`st_size`, `st_mtime_ns`, `st_ctime_ns` from `fstat` of the open descriptor, this process's write counter (`generation`)
and the last `seq`. An unchanged journal costs `open` and `fstat`; a hit returns a new list of the same event objects
(events are read-only, also in the tick phase `HistoryPhase`). With the same inode, a larger size
and an unchanged last remembered line (a hash of the bytes compared in the same place; the memory does not hold the text)
the read reads only the new bytes and checks them by the same rule. Any other change — truncation, file replacement (a new
inode), a content change without a size change, also with a restored mtime (`st_ctime_ns`), a write by this process — gives a
full read. An error (full or incremental) removes the entry, so unknown stays on every subsequent read as long as the file
is damaged. At its start the tick removes entries of cards outside the registry (`cards_complete`).

*Incremental limitation.* The incremental read checks only the file state and the last line: an in-place edit of earlier lines
combined with an append (the same inode, a larger size, the last line preserved) is not detected by it. Therefore
an entry remembers the time of the last full read, and after `FULL_READ_PERIOD` (10 min, the `Journal.monotonic` clock) a read
reads the whole file instead of a hit or an increment; an increment does not move that time. Until then the limitation
applies only to reads without effect: comparisons before a write (`Journal.unchanged`, `HistoryPhase.unchanged`), writes
(`append`, `append_complete`) and rollback checks (`rollback`) read fresh (`Journal.read_fresh` — a full strict
read bypassing the memory), so a write based on such history is refused (`HistoryChanged`). Full reads
are counted by the boundary `Journal._read_strict` (the full read `read_complete` and `read_fresh`); `Journal.check` does not use it —
it has its own memory described below.

**Consistency-check memory (K-105, PT-416).** `Journal.check` (the journal in the Board ↔ the service copy, on every tick for
every card) remembers a pair of states of both files by the same five-field rule (`st_dev`, `st_ino`, `st_size`,
`st_mtime_ns`, `st_ctime_ns`). The same pair costs two `stat` calls and gives a result with no warning, no content read
and no effect. Any change of any field of either file (a revert with a new inode, an in-place edit
with a restored mtime, `chmod`, `touch`) gives a full content comparison of both files in the same call;
restoring from the copy happens only after it — the memory only skips the comparison and authorizes nothing. A pair goes into the memory
after a matching comparison bracketed by two equal `stat` calls, or after a restore confirmed the same way (a fresh read of the
Board equal to the copy between two equal `stat` calls, the copy unchanged since the comparison); a change to the Board from outside right after
a restore is not remembered as matching. A `stat` or read error, a missing file and a failed restore remove
the card's entry, and a missing copy gives, as before, rebuilding the copy from the Board without a pair. A write by this process to the journal and the start
of the tick for cards outside the registry (`forget_except`) remove entries. Content reads are counted by the boundary `Journal._check_read`.
A limitation as with the version key: an in-place change with no size change right after a comparison, within one quantum
of the kernel timestamp clock, changes none of the five fields.

**Process history and card process state memory (K-105 PT-415).** On top of the strict-read memory `Journal.derive`
remembers a value computed from the card's history: the result of the process identity check and the card's process state
(`trialhold._checked_state` — one `fold` with the `process_identities` model; an unknown or inconsistent identity =
ValueError). The key is the strict-read memory entry and its `JournalVersion` (with `st_ctime_ns` and `generation`);
a hit requires that the read return events of the same entry, so a new version, an increment, a periodic full read,
a write by this process and a loose version give a new computation. The memory holds no text and no copy of events of its own — only
a reference to the PT-411 entry and the state. A read, `stat` or check error removes the card's entry and is remembered neither as
history nor as "no change". Entries disappear together with the strict memory (`_invalidate`, `forget_except`).

`trialhold.read_process_history(journal, card)` returns the history from this memory, and `trialhold.process_state` —
(the history, a copy of the state with its own field containers; nested values shared and read-only, as in
`Journal.state`, the shared function `state.copy_state`). Only reads without effect use the memory: the tick phase
(`HistoryPhase` is a view of one tick over the memory; a remembered phase error is still thrown within the phase),
the `_outside_history(card)` barrier, the K-38 inventory, `_recover_started_steps` from the phase (the write through `Journal.unchanged`),
`boxstep.recover_network` from the phase (identity model and state from memory, mutations after `HistoryPhase.unchanged`; the filter of K-38
attempts from memory by journal version since K-113 PT-466, paragraph below; without a phase — recovering the start intent — fresh), the box lifecycle
with `box.enabled=true` (`box/lifecycle.tick`, `active_steps`, `packs.cleanup` — the same strict memory PT-411 as before
PT-415, now without a new `fold` per tick), observing a step every tick (`_poll`, `_guard`, `_advance`,
`_recover_console_openings`) and waiting for an outside command and a post-merge test attempt (`_outside_ready`,
`_merge_check_run`, `_outside_recheck`, the `_resume_step` entry). The variant `fresh=True` (a fresh full read
`Journal.read_fresh` and a new fold, without reading or writing the memory) is used by gates before an effect: `_process_check`
before starting, stopping and resuming a process (by default), `_outside_history` with a step or command (consent,
start, result and writing `outside.finished`, writes of `_outside_recheck` and of automatic consent — `consent.record_automatic`
with `gate`), a post-merge test attempt (the result from the recorded finish, the start under the lock before Popen, writing
`outside.finished`/`merge.check-finished`), stopping the outside unit after the end or a timeout
(`_outside_collect` with `confirm` — a fresh read only after the run finished, not while waiting), Console entry,
handoff, manual close-out, K-38 diagnostics and `public_inventory` before writing.
The K-38 inventory counts items from memory, and when something has to be appended (`k38.handoff`, `k38.inventory`), it repeats the whole
inventory on a fresh read and only that one writes. Recovering a start without a phase reads fresh. A warmed-up tick without
changes in the journals calls neither the identity model nor `fold` (`_pending_main_merge` reads the `Journal.state` projection).
The service memory is measured by PT-414 (a reference point without a threshold).

**K-38 unit state memory and the plan commit at rest (K-125 PT-488).** The K-38 inventory in observation (without
`fresh`) takes the state of the held process's unit (`stepstop.unit_state` — a `systemctl` process, or the box
runner's `step_state` through `FlowEngine._unit_state`) from the engine memory `unitstate.UnitStates` (`FlowEngine.unit_states`),
at most `unitstate.TTL` = 30 s after the read, by the monotonic clock (injected in tests). Only the returned
`active` and `inactive` are remembered; `unknown` (a `systemctl`/`incus exec` error, no bus, a timeout) and an exception from the read
are never memory and remove an earlier entry, so the next tick reads again. `fresh=True` (the inventory
repeated before writing `k38.inventory`/`k38.handoff`, K-38 diagnostics) reads every unit fresh and refreshes
the memory — a write always follows from a fresh read. The rule `inactive` → `unknown` (no protected host evidence) works
unchanged on the raw state. The box resource protection (`box/lifecycle.active_steps`) and `_poll` do not use the memory;
`public_inventory` does not read unit state. The `unitstate` module does not import the engine (ADR-6).
In `_plan_next` the Board comes from the tick's read context (`_tick_board_read`: only on the tick thread; outside a tick —
close-out, D-Bus — a fresh read as before). `_planning_branch` returns the recorded successful `card-branch` result of that branch
before `git rev-parse`, and `_plan_commit` ends without loading the Board (`_plan_on_board`) and without `_plan_files` when the
journal has a successful `plan-commit` for the current key (the plan's `seq` : `plan_change_seq`) — in both cases it is the result
that `_act` would return anyway (`_recorded_ok`; with a base decision or a failed result the path is as before, so a
failed commit is retried every tick). Starting a ticket (limit, executors, the ticket branch, `_launch`) reads the Board
fresh, once per pass, as before the change.

**Network reconciliation attempt filter memory (K-113 PT-466).** `boxstep.recover_network` takes from
`trialhold.network_state` the result of the card's K-38 attempt filter: events without held attempts (`trialhold.held`) and without
protected identities and the state folded from them (a new `fold` only when something was skipped). The result is remembered in a separate
memory of computed values (`Journal.derive_events(…, slot="network")`) by the same journal version as the memory
PT-411/PT-415 (a strict-read memory entry and its `JournalVersion`: dev/ino/size/mtime/ctime/`generation`/seq) —
not by the process write counter alone. The filter is always computed from exactly the events the tick phase gave,
without an additional journal read; events of a version other than the current strict memory entry give a computation without a write.
Invalidation boundaries: an append (also from outside the process, e.g. another service copy or the Board), truncation and file replacement
(a new inode, a different size or `ctime`), a write by this process, a periodic full read and removal of the card from the registry
(`forget_except`) give a new computation only for that card. A read error and an unknown identity do not go into the memory
(in the phase they stay an error of that phase): the card gets no releases, refguard or network policies in this and subsequent ticks
as long as the history is bad. The memory authorizes nothing — every mutation (ports, refguard, step resources, network policy)
still requires a fresh `HistoryPhase.unchanged` comparison; a call without a phase (recovering the start intent) reads
fresh without the memory. Callers get their own lists and a copy of the state (`copy_state`). Reconciliation is not disabled by the
`box.enabled` flag: box steps from before the disabling get resource release and refguard as before. Recognizing an attempt
(`trialpolicy.blocked_event`) is a single expression compiled at module load (an alternation of numbers
`LEGACY | EXCLUDED`, the same boundaries as the old rule); the module still does not import the service package, because the Console launcher
loads it through `runpy`. A warmed-up tick without changes in the journals evaluates no attempts in network reconciliation for any card.

**Replay loop at the start of a tick (K-105 PT-459).** `FlowEngine._replay_starts` covers every card with a journal
(also a finished one) and takes its history and the card's process state from the tick phase (`trialhold.process_state` from
`HistoryPhase` — the PT-411/PT-415 memory), not from its own full reads: the strict read rule replaces the old scan of
`order.` over `journal.read` and the `startorder.events` check (whole lines, consecutive `seq`; outside the loop `startorder` at
the start of an assignment reads the file fresh as before), and `BaseGeneration.recover` and `_project_base` get the same read
(`observed`). A read error or an unknown card identity suspends replay only for that card (the tick skips it
further on too). The memory does not authorize a write: a finished base generation (activation and result in history) is not
resumed, every other generation replay starts with `Journal.unchanged` with a read from memory (an append after the
read → `HistoryChanged` before git operations and the write; a generation in progress thus pays one fresh read per tick),
and the base projection onto the Board compares history fresh only before actually writing fields (`project_card_base(guard=…)`).
After a replay write the phase reads the card again (the write counter), so the projection sees its own write.

**Cards at rest (K-105 PT-412).** A finished card with no work does not go through a full card pass every 5 s
(`_tick_card`, the stall guard `_guard_card`, `_queue_notices`); for it the tick does only a light check. Module:
`cardflow/rest.py`.

- *Definition.* The rest condition is **one function** `rest.at_rest(probe)` over a **registry of excluding
  factors** `rest.FACTORS`. Each factor has a name and answers the question "does this card have work"; a card is
  at rest only when no factor excludes it. An exception or a read error in a factor excludes rest
  (`Verdict.error`). Factors read the card state (`Journal.state`), the tick's Board read context and the engine memory;
  they write nothing.
- *Registry factors* (order of checking):
  `card-unfinished` — the card has no `card.finished` or does not stand in Done/Archive (also a card removed from the Board);
  `steps` — a step or a steward step is in progress, or the Console of an interactive step is open;
  `intents` — an action intent without a result, also in the background (`youtrack-note`);
  `held` — a protected process identity (`held`, K-38);
  `k38-public` — a K-38 card (the public process inventory runs only in its full pass);
  `main-merge` — a merge to main is waiting (`main-merge.waiting`) or an intent without the `projected` checkpoint;
  `base` — a card base proposal, request or change in progress (`baseproposal.blocked`, `card.base-requested`);
  `outside` — this card's command outside the sandbox is running or waiting (also a post-merge test attempt and an entry in the live
  engine queue);
  `caretaker` — the steward is working or its result waits for an action;
  `stage-errors` — a stage error (`flow.error`) waits for a retry;
  `decisions` — a decision is waiting (`caretaker._waiting_decisions`: question, consent, plan, review — they feed `_caretake`);
  `plan` — a plan waits for review, approval or a fix;
  `reviews` — a review verdict or a ticket rejection waits for replay (`_recover_verdicts`,
  `_recover_review_rejections`);
  `tracker` — a YouTrack note after a ticket merge waits to be sent (a project with YouTrack);
  `notices` — a notification waits to be sent (in the engine memory or a step result without `step.notice` and a decision);
  `queues` — the card has an operation in a queue (below).
- *Queues.* A named entry point `rest.has_queued_operation` ("the card has an operation in a queue") over a registry of sources
  `rest.QUEUES`: `heavyqueue` (the queue of commands outside the sandbox with the heavy queue K-39 PT-373 — running and waiting,
  heavy and light, with post-merge test attempts; sources like the snapshot's `queue` field, `queueview.operations`, and the live
  engine queue) and `stepqueue` (the provider step queue K-39 PT-377 — every waiting entry). Every waiting or
  running operation of any queue excludes rest of **every** card, even when it belongs to another card; a queue state
  read error too. The queues are read once per tick, on the first question. Both queues are in the ticket base
  and are hooked in by PT-412; **further queues (e.g. the later engine part of K-39, PT-378/381) hook in
  only through `rest.register_queue`**, not through a separate condition in the tick. A limitation: a command outside the sandbox
  with `outside.started` without `outside.finished` on a finished card is in the queue exactly as in the snapshot's `queue` field,
  so it keeps all cards out of rest until it disappears from the queue.
- *A rule for successors.* **Every new work factor must exclude rest.** A factor is added in one
  place — `rest.register(name, question, has_work)` or an entry in `rest.FACTORS` (and the name in the list above) — and not as a
  scattered condition in the tick. K-108 (invalidating rest after a blocker change), K-109
  (`command.*`), K-107 (open stages and the Console) and the engine part of K-39 hook in here. Work without a factor in the registry is not
  handled while the card is at rest (at most until the full check period).
- *The light check of a card at rest* (every tick): the journal version (`Journal.version`, the PT-411 memory),
  the consistency check with the copy (`Journal.check`, PT-416 — unchanged, so a journal revert in the Board comes back from the copy
  with a warning in the nearest tick), the column and `move_id` from the tick's Board read context, `at_rest`, registering the
  card's repository in the worktree guard (`worktree_guard`) and in `_main_branches` (as before, before the decision
  on rest — the guard works also for a repository all of whose cards are at rest) and busy worktrees
  and hidden sessions (`busy`/`hidden`) from the card state from memory. The replay loop at the start of a tick (PT-459) still
  covers every card.
- *Return of the full pass.* The `rest.RestMemory` memory holds, after a successful full pass of a card that was then
  at rest, a marker (journal version, column, `move_id`, moment). The full pass returns on: a change of the journal
  version (also our own write and a write from outside the process), a change of column or a card move on the Board (a move is handled by the
  full pass as before), the first tick of a new engine (empty memory), the full check period elapsing
  (`rest.PERIOD` = `FULL_READ_PERIOD`, 10 min, the `Journal.monotonic` clock), an exception in the previous full pass
  (no marker) and when any factor excludes rest. The marker is created only after a full pass without an
  exception in which no factor excluded rest — a card excluded e.g. by a queue operation returns to rest
  only after the next full pass.
- *Full-pass paths that a finished card does not need* (coverage proof: the PT-412 note):
  `_complete_manual_finish`, `start(…, _resume=True)`, `_advance_card`, `_caretake`, `_queue_notices`, `_guard_card`
  and `_recover_transfer_requests` themselves skip a card with `card.finished`; `_recover_started_steps` is also performed by the replay
  loop (every card); `_outside_history(card)` is only a read; `_sync_plan_cards` restores the projection of plan
  sheets from the journal — after a change of files from outside it returns on the nearest journal change or full check.

Card state (`cardflow/state.py`): a card with a journal gets `derived` only from the journal (with a `flow` field: ticket,
step, executor, result, card branch), a card without a journal — as before from `board/derive.py`. Manual columns win:
Done and Archive without state, Backlog — "wstrzymana" (paused). In To do: a step in progress or a merge waiting → In progress; a question,
missing consent, a step error, a failed merge → Waiting for me; a ticket on the card branch → To check. MoveCard of a card
with a journal: Backlog appends `card.paused` (a step in progress finishes), Done and Archive stop the step with saving
changes, reject an unmerged ticket (`ticket.rejected`) and finish the card (`card.finished`). The view lets you drag
a working card with a journal to the Backlog and Done.

Service restart: steps live in their own units; after the start `tick()` reads the journal, checks the units
(`systemctl --user is-active`), finishes started actions by the "intent → result" pairs (merge — idempotently: a ticket
already in the card branch is a success without a second merge) and never appends an event a second time.

A step session is not an agent row: `AgentMonitor.hidden` (`engine.is_hidden` — the session identifier or a directory
in the step worktree) removes it from /Agents Sessions(), the Praca tile and notifications; KRunner has its own index and finds it.
Worktrees of steps in progress and cards in progress are "with an agent" for the Worktrees window (not for deletion).

Card preview: the Praca tile shows the number of steps in progress, and Tide Sessions expands a card into tickets, steps and a timeline of
events. The card panel gives the start of every step in progress and the last noted change of its files or session
journal (during work most often once a minute). Every five seconds the service checks cards; after three minutes without a step,
an open action and a named waiting reason it records `card.stalled`, shows "stoi" (stalled) and notifies once. A question, consent,
a limit, a blocker, plan approval and a card to check are named waiting states. Flow notifications
cover these decisions and a repeated step error; a question finished earlier does not notify again after a restart.
The morning report reads card journals, including cards moved to Done, and lists tickets merged in the period
together with the executor.

### Review and the card branch (PT-59)

`StartCard` accepts the name of a reviewer from the catalog (`reviewer`); the "Od razu" window shows executors of a different model.

**List of local bases (PT-209).** `ListCardBranches` returns only `refs/heads/` with records
`{ref, name, kind: "local", remote: "", commit, selectable, reason}`. `name` keeps the full name,
`commit` is the full SHA, `reason` contains the reason for a possible refusal. The `text` filter matches any
fragment case-insensitively. `mainRef` is a full ref per the shared main
policy or an empty string with a warning. The main one is first if it matches the filter.
`recentBases` contains shortcuts of successful starts and corrections, and `recentTargets` for the merge target — of successful merges
(the PT-214 description above). `hasMore` requires narrowing the query, with no further pages.
The start window lets you choose a base in "Od razu", "Mam plan" and Planning, sends the full ref and the SHA from the list result and a separate
name of the new card branch `cardBranch`. An invalid or taken name is rejected. A missing field keeps
the automatic name of older clients. The choice concerns only the card's project; additional projects keep their
existing bases and reads. "Tylko specyfikacja" has an inactive choice until the chosen revision is handled.
Review, fixes and plan approval use the same card branch; the next wave starts from its current
state after the blockers are integrated. Taking over a plan copies the artifacts to the journal, without changing the source and its numbers.

**Text suggestions for the base (PT-196).** `SuggestCardBase` reads the description of the saved card and, separately, the form
text (up to 200000 characters). `revision` is a non-negative form version number. The analysis recognizes full,
existing names and refs from the local git listing, distinguishing letter case and name boundaries, also
in backticks and `git switch`/`checkout` examples. It does not resolve the text as a git revision.
A pointed-to name that does not exist is a warning. A local and a remote ref with the same name remain
separate candidates; many pointers give a warning, without deciding for the user.

A candidate contains `{ref, name, commit, source, evidence, strength, selectable, reason}` (and the branch
metadata `kind`, `remote`). For text `source: text`, `strength: explicit`, and `evidence` is a list of
`{field: description|text, line, text, match, start, end}`: the line number from 1, a snippet around the hit
(up to 512 characters) and the range of the visible hit within that snippet from 0, with an exclusive end. A very long
name may be trimmed in the evidence; the candidate keeps the full name and ref. The response contains at most
32 candidates, 5 pieces of evidence per ref in total from both fields, and 20 warnings about unknown names (a name up to
256 characters), besides the message about a conflict and a list limit. The command context covers at most
80 characters before the token. Evidence is plain text. File history and PR/MR links
have separate buttons and require an explicit user request.

The assignment window analyzes the text after a short pause. It accepts a response only for the current tag,
revision, card, project and repository. A change of text, context or choice invalidates the pending
response. A suggestion never changes the choice on its own. "Użyj <full ref>" (Use <full ref>) fills the existing base
field with the SHA; only `StartCard` persists the decision. An analysis error does not block a manual start. For a persisted
base suggestions are disabled. The read itself writes neither the card nor the journal and does not mutate git.

**Local file history (PT-197).** The "Sprawdź lokalną historię plików" (Check local file history) button calls the same
contract with `scope: files`. The analysis runs in the existing worker thread and does not block a manual start.
Paths come from the card description and the prompt: names with a slash or a dot, and in quotes/backticks
also names without an extension and with spaces. Paths are relative to the repository directory;
absolute ones are accepted only inside that directory. Traversal and globs are skipped with a warning.
We do not read the pointed-to files from disk, we do not follow symlinks; a file may exist only
in the feature branch tree. A bare basename is used only after an unambiguous match in the whole
analyzed set of trees. An ambiguous shorthand requires giving the path from the repository directory.
A file in the root directory can be pointed to exactly as `./config.py`, also when a namesake exists elsewhere.

The scope is at most 20 distinct paths, 100 locally available branch refs (local and remote,
without symbolic ones), ordered by the date of the tip commit, the 200 newest reachable commits
per branch and 5 seconds in total. Each git call gets the remaining time. First we limit the
set of commits without a path filter, then we read file changes only in that set, without
walking the history further. Paths go after `--` with literal pathspecs, without a shell.
There is no fetch, checkout or indexing of the whole history. At a limit or an error the result stays
partial; a warning explains that the absence of a pointer does not exclude a branch. The response also contains
`analysis: {partial, paths, branches, limits: {paths, branches, commitsPerBranch, seconds}}`.

A candidate always has `source: files`, `strength: heuristic`, the tip ref/SHA and evidence
`{path, commit, timestamp, time}` (committer time in UTC). Evidence requires the path to be present in the tree
of the tip SHA and touched in the limited history. Ranking: the number of proven paths
(`matchedPaths`), then the latest time they were touched (`lastTouch`); the date of an unrelated tip
commit does not raise the score. An identical score gives an identical `rank`, also for a common ancestor
of several branches; alphabetical order is only a presentation order. The shared list shows
the position, path, touching commit and time. Only "Użyj <full ref>" changes the manual base, and the start
passes the chosen SHA to the existing base persistence mechanism.

**PR/MR suggestions (PT-198).** "Sprawdź linki PR/MR" (Check PR/MR links) sends `scope: links`; the automatic text analysis
does not query the hosting. The `gh api` and `glab api` adapters use the existing CLI login, without configuring
an integration in Tide. They recognize only HTTPS `/owner/repo/pull/N` and `/group/subgroup/repo/-/merge_requests/N`
on a host and in a project matching the configured remote (SSH/scp/HTTPS mean the same identity).
Userinfo, query, foreign projects/hosts and other kinds of URL are rejected before the CLI is run.
Remote aliases through `url.<base>.insteadOf` are not expanded: e.g. `gh:team/proj` requires a remote
with a full SSH/HTTPS address, otherwise the link gets a warning about a mismatch with the remote.
At most 4 distinct links per analysis, a shared 10-second deadline for all reads (also of the GitLab
source/target projects) and 1 MiB of total stdout/stderr per CLI call. After the deadline
the existing result comes back with a warning. The snapshot of the card, branches and remote is made in the serial thread
of the flow; the hosting is read in a separate pool, without git and journal locks. Two hosting analyses
can run at the same time; further ones get a busy error without a network queue. StartCard and the tick do not wait for the CLI.

A `source: links` candidate adds `{host, project, sourceProject, sourceBranch, sourceCommit, targetBranch,
link, localCommit, stale}`. The target is never a proposed base. The source repository must have a
matching remote; the local ref must follow from its fetch refspec or an explicit upstream configuration of the local
branch. Overlapping ref mappings from foreign repositories are ambiguous. A mere match of
a local branch name with a fork is not enough. A missing source gives a non-selectable item with a `reason`.
No fetch is performed. A differing API SHA and local ref SHA are shown together; "Użyj stanu lokalnego
<full ref>" (Use the local state) passes the local SHA to the existing base choice and `StartCard`.

The `gh` gate performs one fixed HTTPS GET per call and rejects all redirects.
The CLI uses the `http_unix_socket` key from a temporary `GH_CONFIG_DIR/config.yml`; `hosts.yml` is only
a symlink to the user's existing configuration file (without Tide copying or reading secrets).
The temporary `config.yml` also contains `version: "1"`: without it gh starts a configuration migration
and writes `hosts.yml` through the symlink. When `hosts.yml` exists and the user's `config.yml` does not have
`version: "1"` (an unmigrated store), Tide does not run gh and returns a warning; Tide reads from
`config.yml` only the `version` key.
An unsupported transport ends with an error through a substitute proxy without access to the hosting.
`glab` gets an explicit `GLAB_PROXY` pointing to a local TCP proxy and `GLAB_API_PROTOCOL=https`
and `API_PROTOCOL=https`. The proxy accepts only `CONNECT` to the fixed host on port 443; it rejects a foreign
host, another port and plain HTTP without an outgoing connection. It does not decrypt TLS or see the token.
An HTTPS redirect on the same host may be handled by `glab` inside the tunnel; a redirect
to a foreign host or HTTP is stopped by the proxy. TLS is verified by the CLI (`skip_tls_verify=false` enforced).
The transport has a limit of 2 MiB per tunnel including TLS overhead, independent of the 1 MiB stdout/stderr limit and the analysis deadline.
Credentials are not read through `auth token`, saved or returned. The result contains only
allowed metadata fields; stderr and the raw
CLI JSON go neither to the response nor to the journal. The description and prompt serve only to extract links,
and are not sent to the hosting. A missing CLI, login, network, PR/MR, invalid JSON, a limit or a timeout gives a warning;
a manual local start remains available. For hosts requiring an external proxy, choose the base locally;
the adapter does not take over the proxy configuration or login.

**Card start base (PT-194, PT-211).** Od razu, Mam plan (`entry: import`), a proposal from the Console
(`entry: plan`) and both existing Planning modes use one base resolution.
The request of each of these entries may contain `baseRef` (only a full local
`refs/heads/…`) and `baseCommit` (the expected full SHA). On the first start without `baseRef` the existing
`FlowGit.main_branch` works: the existing `tide.mainBranch`, then the master/main/develop policy.
This does not change the project configuration. Under the repository lock the ref is resolved to a SHA before
reserving a number and `card.started`. A missing branch, an invalid ref or a mismatched expected SHA
refuses the start without an assignment and a number; the user refreshes the choice and begins a new start. A tag, SHA,
revision expression and `HEAD` are not a local branch ref. There is no substitution through the user's checkout.
The card's branch and worktree are created from the pinned SHA, without an upstream; a later ref move does not change it.
A restart uses the existing card branch. Without `baseRef` it inherits the base recorded
in the journal, even after the ref is moved or deleted; it does not resolve main again. A ticket starts
from the current state of the card branch, keeping the existing work. For a legacy card with an unknown base
a start without a base choice is allowed if the card branch exists; the base and `initialBase` stay
unknown, without guessing a historical SHA. A missing branch gives `no-base` with instructions to restore it
or to create a new card with the chosen base. A new assignment has its own `baseVersion: 1`
also for unknown; historical events and their version 0 stay unchanged.
An explicit different `baseRef` requires the resolved ref and SHA to match the journal's base. The resume form
shows the persisted ref and SHA without querying the list; passing the same ref keeps the saved SHA,
even after the ref is moved or deleted. A bare `baseCommit`
without a ref must correspond to the saved SHA of a known base. A different or unknown base with an explicit choice
causes `base-mismatch` before reserving a number and writing the assignment. Changing the base belongs to a separate correction.

The base source is the journal: `card.started` records `baseRef`, `baseBranch`, `baseCommit` and
`baseVersion: 1`. The snapshot exposes these three fields on the card, and in `derived.flow` the objects `base`
and `initialBase` of the shape `{status: "known"|"unknown", baseRef, baseBranch, baseCommit}` and
the number `baseVersion` (also on the card). For unknown the three values are empty. The card model and file
have only the projection `base-ref`, `base-branch`, `base-commit`; we do not store a flat version field.
UpdateCard rejects the base and version fields. The snapshot takes values from the journal also when the projection
write fails; a retried projection write does not start the assignment or git again.
The projection does not add missing empty fields or change card files in Done and Archive.
When a card has no computed state (e.g. it went back to To do by hand or is in Done/Archive),
it keeps `derived: null`. Its fields `baseRef`, `baseBranch`, `baseCommit` and `baseVersion` on the card
in the Snapshot still come from the journal, regardless of whether the projection write succeeded.

A historical assignment without a version always has `baseVersion: 0`. A known historical base requires an intent
to create a branch pointing at the ref and a successful result with the SHA and `existing: false`, from that assignment.
The result of a pre-existing branch does not prove the SHA of the original start. Without evidence the base is unknown; we do not read
today's main and do not add fields to old events.

The correction contract: a single successful `card.base-changed` atomically records `base`
(the same three-field shape), `generation` (the full resource map of the PT-202 correction), `previousBaseVersion` and
`baseVersion: previousBaseVersion + 1`. Replay accepts a transition only from the current version and with a known
new base; repeating the same event does not increase the counter. `initialBase` keeps the start.
A new assignment has its own 1. A preview, refusal, error, ref move and projection repair do not change the version.
Tokens and future merge/correction operations use only this `baseVersion`. The execution of the
correction and the resource mapping are described below. Historical events with an empty `map` keep their reading.

**Empty generation (PT-202).** `PreviewCardBaseChange` remains a read. Execution requires
`ResolveCardStep(id, {choice: "change-base", baseRef, baseCommit?, previewId, step?})`, where `baseRef`
is a full local ref or a locally known remote, `previewId` comes from the current preview,
and `step` points to the agent's question or the `RequestCardBaseChange` decision, if one exists.
The choice `baseDecision: "change"` alone still only opens the preview. The answer and the option label are not
a Git command. The Board and Sessions panel offers a separate confirmation only for `canExecute: true`,
passing the exact ref/SHA and the identifier of the displayed preview. A refusal or waiting
for resource recovery leaves the evidence and the link to the draft in the open panel.

Under the repository lock the service repeats the PT-201 qualification: the exact SHAs of both bases, the version, the full
map, the proven start points of all branches, no contribution, no local changes and no processes.
A change gives `stale-preview` without an intent or mutation. The `intent(action: change-base)` intent stores
a fixed `operationId`, a fingerprint, the journal boundary, `oldBase`/`newBase`, both versions and the complete `generation`.
A map row contains `action`, `key`, `ticket`, `branch`, `worktree`, `base`, `oldBranch`, `oldWorktree`,
`oldCommit`, `ownerRef`, `archiveRef` and the full list of ignored paths (directories in aggregate).

The new generation's branches/worktrees are created outside the old directories from the pinned SHA. A CAS
transaction creates the branch, the owner ref and a ref preserving the old SHA; a foreign name means waiting.
Old worktrees are locked, and new ones are created with `--lock`. Ignored data stays in the old
directories. `base-change.checkpoint` records the full map after preparing each resource and before
activation. Requalification and a check of the whole new map precede a single `card.base-changed`.
The fold selects the full map and increases the version once; a partial/invalid map changes no field.
`initialBase`, IDs, contents, the plan and blockers remain. No empty merges or replay commits are created.

A restart finds the same intent. Before activation it finishes its own resources or records
`base-change.waiting` with the cause and a list of preserved resources, leaving the old map.
After activation it retries only the result and the base projection, without git and without another increment.
A retry with the same ref/fingerprint returns the same intent. The snapshot exposes `flow.generation`,
`flow.preservedGenerations`, `flow.generationWaiting` and `flow.baseValidationRequired`.
Old refs, archive SHAs and directories are not subject to ordinary cleanup; cleanup does not carry over their data.

**Effects of an empty correction (PT-213).** Activation keeps the field `validationRequired: true` as part of the
PT-202 contract. The fold of a proven complete map reproduces the invalidations and treats the validation of an empty
contribution as satisfied (`flow.baseValidationRequired: false`). It does not require review of a fictitious commit.
Old reviews, decisions and close-out remain in the journal and history, but do not authorize the new
SHA. The snapshot exposes the current `flow.approvals` and a separate `flow.historicalApprovals` with the original
SHAs and versions. Historical steps have `historical: true`, with no EnterStep and no transfer.

A non-rejected ticket begins a new step in a new directory with the current ref/SHA/baseVersion, a correction
note, access to earlier results and a fresh CLI session. A rejected ticket stays rejected.
New work passes the existing tests and the review of its actual SHA before integration; generation
resources stay preserved also during cleanup. An old consent to commands until the end of the ticket
does not carry over to the new generation. Old steps and consents do not return through recovery.

The card requires a new summary on the new base, also when it was closed earlier after a ticket rejection
with no changes. MergeCard and MergeCardToMain apply the same close-out gate. The default target
in the menu and the comparison uses the current base, and a token from before the correction gives `stale-preview`.
A remote still requires an explicit existing local target per the shared target policy.
A repeated fold and a restart do not start generation preparation or another increment.

Without a choice the service picks a default reviewer from the catalog. In `preferredProvider: "mixed"` mode the reviewer's provider
must differ from the author's provider. The reviewer's name, program and model go into `card.started`.

After a `done` result of a ticket step the service checks the cleanliness of its worktree and that the ref is unchanged, computes the diff against
the card branch and launches the reviewer in the same worktree under step supervision. The review sheet contains the card description,
the diff, the ticket's result note and AGENTS.md/CLAUDE.md from the repository. The reviewer writes `review.md` and JSON with the verdict
`APPROVE` or `REQUEST_CHANGES` and the counts `blockers`, `majors`, `nits`. The service accepts APPROVE only with zero
BLOCKER and MAJOR, checks the worktree and ref again, and records the verdict as `review.verdict` in the journal. The verdict and
the counts are visible in the card panel.

On REQUEST_CHANGES the author gets a fix step with the comments and a re-review follows. After the third failed round
the card records `review.question` and waits for the user's decision in the panel. `retry` records `review.decision` and allows
one more round; the next failed round asks again. `reject` records the decision and `ticket.rejected`, without a merge.
If a restart separates these two events, the service appends the missing rejection on the next pass; the same
`reject` response can be safely repeated.

The executor of fixes (K-88/PT-312) is chosen in order: a manual author change from the panel (`executorReason: override`),
escalation from round `ESCALATE_FIX_ROUND` (3; `escalated`) and the existing author (`author`). Escalation applies only to a
ticket whose class in the approved plan is not `mocny` or `lokalny` (no class = `standard`). Its target is the
first available executor of class `mocny` of the assignment's preferred provider (in mixed mode — the author's provider),
different from the reviewer of the reviewed and the next review and different from the author; without such a one the author does the fixes, and a change of
the author in the catalog still ends the step with a `changed-executor` error. The author is the ticket author's last step that was not
an escalation or the reviewer's work, so the reviewer does not review fixes written by themselves. A manual author change
from the panel (`author.reassigned`) applies in all subsequent fix rounds of the ticket, until the next such
change. Resuming a `fix` step: a manual executor change (`change-executor`, `transfer`) or a newer reassigned author
records `override`. Automatic decisions (`auto`, `automatic`, `caretaker`), also after a retry error or a
provider refusal, are subject to the fix rule; the choice `change-executor` alone is not a manual consent. If the rule
rejects the proposed executor, the step returns to an ordinary resume. A resume recomputes the same rule —
the same executor keeps the session and the reason, and when the
rule points to a different one (e.g. the old step was written by the reviewer), the resume starts a fresh session with the reason `author` or
`escalated`. An automatic return to the author goes through the same `changed-executor` check as ordinary fixes. The review
question threshold (`reviewRoundsBeforeQuestion`) does not change.

The review start gate (PT-312, owner's addition of 10.11): `_guard_review_launch` works before
reserving a step in `_launch` and before launching the Console in `_launch_full_planning`. Entering the Console
(`EnterStep`) in a review that ended without a verdict (error, question, stop) does not resume the old session: it is a new
review through `_launch_full_planning` with this gate and a fresh session (PT-330); taking over a review in progress and a conversation
after the verdict (`interactive`) work as before. `fix` steps in the Console (handoff and entry) record
`executorReason` like background steps, so a retry after a manual handoff keeps `override`. Every launch of
review, also a resume, begins a separate session. `reviewgate.authors` takes from the journal the ticket's authors
and all fix attempts since the previous review round; a failed fix may contain WIP, and resuming
the review of the same round does not remove these authors from the check.

The model condition is concentrated in `reviewgate.permits(reviewer, authors, policy)`: `free` allows any
model, `other-model` applies the existing `plans.independent_reviewer` (a different model or effort),
`other-family` compares the provider/program pair from the catalog and recorded steps. The policy comes from the setting
of the ticket's project `reviewIndependence` (PT-330, decision 96 of 10.11; the plan's Project column, otherwise the card's project; the ticket's
class from the plan, Od razu — `standard`), by default `free`. It is not an overriding Tide rule. The same policy is applied by:
plan checking (`plans.read`, also the default reviewer of an empty Review column — with `other-family`
an executor of a different family), ticket editing in the window (`EditCardPlan edit`), the Od razu start (`StartCard`; with `free`
and no other executor the reviewer may be the author themselves, with `other-family` a reviewer from a different provider is allowed
also with the assignment's provider chosen), a ticket start from the plan, choosing and changing the plan's reviewer and the fix
escalation target. The `ESCALATE_FIX_ROUND` rule does not change; with `other-family` the escalation target does not belong to the family
of the reviewer of the reviewed or the next review (the family from the step journal, assignment or catalog).
When the chosen reviewer does not meet the policy, the gate looks for an executor of the ticket's class or a stronger one,
keeping the local/cloud scope. Without a candidate it records an idempotent `review.question`
with `reason: independence`, `policy`, `ticket`, `round` and `step.waiting` with `cause: review-independence`.
The question gives the reason (a policy requirement, a rejected reviewer, no matching executor) and three ways out: choose
another reviewer, wait or relax the project policy. Relaxing takes effect in the next pass without a restart.
The panel shows the question and the reviewer choice through the existing `ResolveCardStep reassign-reviewer`.
The appearance of a matching reviewer closes the question with a `review.decision` event with `choice: review-ready`;
the review start removes it from the projection, so that a later question after the round threshold works as before.

APPROVE launches a merge of only the approved ticket commit (the ref is checked under the repository's git lock)
and removes the ticket worktree
under the same lock. The step input and output directories lie next to the worktree and stay in the board repository as copies of the
result. An error preparing the next stage is a `flow.error` event and shows the card as waiting; the service
retries the stage in subsequent passes.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.StartCard K-12 '{"entry": "now", "executor": "Sonnet"}'
```

### Decisions after a background step (PT-65, PT-66, PT-68)

A `question` result from `result.json` shows the question in "Czeka na mnie". An answer from the panel records a `step.decision` event
and resumes the ticket in the same worktree and session. The step gets a sheet with the question, the answer and a new
result path. A notification is created once per step ended with a question (`step.notice` in the journal); a click opens
the board. After a resume the commits and changes from the earlier attempt remain.

A `denied` result contains the exact command. "Zezwól i wznów" (Allow and resume) appends this form of the command to the project list and resumes
the step; "Odmów" (Deny) resumes it with information about the refusal. The `allowed-commands` and `denied-commands` lists are stored in the header
of the board project file as JSON arrays and are editable in "Projekty i tagi". Claude gets from them the rules
`--allowedTools` and `--disallowedTools`, and a Bash refusal from the CLI's final result becomes `denied` when there is no own
result file. Codex works in the `workspace-write` sandbox, but gets the command lists only in the sheet as a hint:
the CLI does not automatically block every command outside the list. The default list allows frequent reads
and local work, and for the Tide project forbids `make install`. A forbidden rule takes precedence; the panel cannot
approve such a command. A consent applies to the full command, without extending to the whole program; the panel refuses
syntax composed with shell operators and commands longer than 300 characters, which it shows without truncation.

A step error (`error`, no result, an exceeded time or no progress) records `step.retry` and causes one
automatic retry. A second error shows "Ponów" (Retry) and "Zmień wykonawcę" (Change executor). Both decisions keep the worktree;
changing the executor starts a new session and is also used in subsequent fixes after review. A local step does not
switch to the cloud by itself. `stepTimeoutMinutes` in `$XDG_CONFIG_HOME/tide/cardflow.json` limits a single attempt
(default 240 minutes); after the limit the service stops the unit through `stepstop.stop_step` and keeps the changes.
The no-progress guard gives in the card the time since which the step has stood.

Consent to a command chain (PT-124): on a `denied` result the service decomposes the command (`board/chain.py`) into
single commands — after `&&`, `||`, `|`, `|&`, `;`, `&`, a newline, groups `( … )` and `{ …; }`, with the contents of
`$( … )`, backticks, `<( … )` (also in double quotes) and a `sh -c`/`bash -c`/`eval` script as
separate commands. The list goes into `step.finished.commands` and into the `permission` decision in `flow.decisions[]` as
`commands[] = {text, readOnly, stepDir, forbidden, reason, canAllow, preselected}`. `forbidden` follows solely
from `is_forbidden` and the project's `denied-commands` list (without built-in rules), and `reason` gives the matching rule or the
reason why the command cannot be approved. Reading its own `<step>/in` and `<step>/out` is always allowed
(Claude gets exact `ls`/`cat` patterns for these directories) and does not go on the list. "Zezwól na wybrane i wznów" (Allow the selected and resume)
sends `allow: [indexes]`; the service recomputes the list with the current prohibitions and rejects an index out of range
(`bad-allow`) or pointing at a forbidden command (`forbidden-command`), appends to the project list exactly the
selected commands, and the resumed step gets a list of allowed and skipped ones. "Kontynuuj bez tych poleceń" (Continue without these commands) is `deny`,
"Zrób zamiast tego…" (Do this instead…) — `deny` with `instead`. A chain is rejected automatically only when none of its
commands can be allowed.

The consent "run always for the ticket" can be revoked in the full card (Board and Sessions).
`/Board RevokeTicketConsent(id, {ticket, orderSeq, baseVersion})` takes the context from
`derived.flow.ticketConsents[]` and returns `{ok, ticket, active: false, seq}`. A repeat returns the same result.
A foreign ticket gives `bad-ticket`, and an old assignment or base version — `stale-context`.
The projection contains `active`, `revoked`, `canRevoke`, `seq`, `orderSeq` and `baseVersion`.
The source is the last `outside.ticket-consent` or `outside.ticket-consent-revoked` in the current
assignment and base version; a new start and a base correction reset the projection, keeping the history.
After revoking, automatic consents and remembered project families do not launch further commands of that
ticket. Project rules and other tickets stay valid. A one-time manual "Uruchom" (Run) works only for
its own command; a new "Uruchamiaj dla ticketu" (Run for the ticket) restores automation. Revoking invalidates
pending automatic decisions and the `run-ticket` decision; a separate manual consent to a command remains.
The check right before the queue start is subject to current prohibitions. The revoke operation and the start
run in the same serial thread; a command that has already started finishes normally.

**A refusal wins over an automatic consent (PT-311).** A human refusal (`deny` from the panel or from the coordinator,
without `auto`) is final for the same command (compared ignoring whitespace differences) in this ticket
of the current assignment and base version (`orderSeq`, `baseVersion`) — also when the steward sent the step with the refusal back
for a plan fix (this changes neither the assignment nor the base). A subsequent outside request for that command gets no
automatic consent (a remembered family, `outsideAuto`, a ticket consent) either at the step result or in a re-check
of the rules; a paused card also gets no new automatic consents (after a resume the consent arises
by the current rules). A skip records once per step with the reason `outside.auto-skipped` with `reason`
`denied-before` or `paused`. Since K-125 PT-487 the tick does not re-evaluate a ticket step's or planning's request
in a paused card: the pause is checked on the tick state before the evaluation, without reading the Board and without a write
(also without `outside.auto-skipped` with `paused`), and the evaluation starts in the first tick after a resume. `paused` is now recorded
only by a step result arriving in a paused card and by a post-merge test attempt. The request remains
resolvable in the panel. An automatic consent in the queue whose command a human refused later in another
ticket step is invalidated before start (`outside.decision-invalidated` with `reason: denied-before`).
`ResolveCardStep` with `deny` on a step with an automatic consent before `outside.started` records
`outside.decision-invalidated` (`reason: denied`) and the refusal, and the process does not start; after the start it returns
`already-started` and the command keeps running. A manual consent (`run`, `run-ticket`, `run-always`) remains
a recorded answer (`already-decided`) and works also after an earlier refusal. The decision check right before
the start, the launch and writing `outside.started` are under the same journal lock as writing the refusal.
An automatic Tide refusal and a steward refusal do not block later consents.

**Machine load in outside events (K-100, PT-423).** `outside.started` and `outside.finished` (a step request
and a post-merge test attempt), and since K-109 `command.started` and `command.finished`, have `loadavg: [l1, l5, l15]` (the average load at the moment of writing, two decimal
places) and `cores` — the number of cores available to the service (`sched_getaffinity`, otherwise `cpu_count`). A read error
gives `null` in the given field; it never stops a command or the writing of the event. The fields are diagnostic only
(a meter of failures under load): they do not change the queue, consents, resumes or the process identity model (the fields are
neutral in `cardflow/processidentity.py`), and entries from before the change, without these fields, are read as before.

**The queue of commands outside the sandbox and the heavy command queue (K-39 #3, PT-373).** A consent (manual or
automatic: a remembered project family, "for the ticket", `outsideAuto`) goes into the queue by consent time.
Commands divide into two classes (`cardflow/heavyqueue.py`, the pure `classify` and `admit`):

- **heavy** — a command from the `tests.changed` or `tests.full` family of the project's effective test contract
  (the approved contract, otherwise the worktree's `make` targets: `make test-changed BASE={base}`, `make test`) and every
  post-merge test attempt (K-88). The family is the contract command's arguments up to the first `{base}`, compared
  word by word with the beginning of the command without leading assignments: `make test-changed BASE=<any SHA> JOBS=4`
  is heavy, `make lint`, `make test-cardflow`, `ss -ltnp` — not. A project with a different contract (e.g. `dotnet test`)
  has its own commands as heavy. Rerunning only the failed modules (`rerunOf`) counts as a full run,
  as does a command matching a `heavyCommands` pattern (below);
- **light** — the remaining commands.

**`heavyCommands` patterns (PT-382).** Builds, tests of other technologies and measurements are entered by the owner in the flow
settings (e.g. `bash daemon/tests/perf_gate_k98.sh *`); such a command waits in the same queue, with the same limit.

- *Arguments* are taken by `classify` through `outside_argv(command, roots)` with `roots` = (the ticket worktree, the project
  repository, the step directory), as the consent does at start; without `roots` `bash`/`sh` and `env` would not give arguments.
- *Matching* is a single function `heavyqueue.pattern_matches` (`quietCommands`, #6, will use it too), not
  `outside_matches`. A rule has the format of "always in the project" consents: an exact command or a family with a trailing ` *`,
  and a family works as a prefix over arguments. A rule is parsed like a command (with `roots`, with normalization).
- *Normalization* (classification only; the command is run unchanged, and `outside_allowed`/`outside_risky`
  work as before): leading assignments and `env NAME=…` are skipped, an interpreter given by path
  (`/usr/bin/python3`, `.venv/bin/python`, `python3.12`) is `python3`. `env PYTHONPATH=… .venv/bin/python -m pytest …`
  thus matches `python3 -m pytest *`, and `JOBS=4 make test-changed BASE=…` the contract family. This applies to contract
  families too.
- *Scripts*: for `bash`/`sh <script> …` with a script in `roots` (`script_in_roots`) the path in the command and in the pattern is
  reduced to a form relative to the worktree or repository (`./`, an absolute path in the worktree and `sh` instead of
  `bash` give the same). A script outside `roots` (also `../`) matches no pattern.
- *Default list* (`heavyqueue.DEFAULT_PATTERNS`, families with a trailing ` *`): `make test`, `make test-changed`,
  `make test-cardflow`, `make test-daemon`, `make test-board`, `make mist-golden`, `npm test`, `npm run build`,
  `npm run e2e`, `npx playwright test`, `dotnet test`, `dotnet build`, `go test`, `cargo test`, `pytest`,
  `python3 -m pytest`, `python3 -m unittest`, `python3 tools/test_runner.py`, `docker build`, `docker run`,
  `docker compose up`, `docker compose build`. `make test*` is not matching syntax (`make test *` matches
  the target `test`, not `test-cardflow`), so the targets are enumerated.

At most `heavyLimit` heavy ones run at once per machine (a flow setting, 1..6, default 2), regardless of
project, card and kind of consent. Light and heavy together are subject to the existing limits: `OUTSIDE_PER_PROJECT = 6`
per project and `OUTSIDE_TOTAL = 7` in total (the total is not smaller than the largest `heavyLimit`), so a light
command starts despite occupied heavy slots, and an older heavy consent waiting for a heavy slot does not hold
light ones in the queue. The limit works at every start decision: reducing `heavyLimit` does not interrupt those in progress,
and new ones start only when fewer are running than the new limit. The order among heavy ones is the consent time.

The set of running ones is the engine's live `_outside_active` (after checking exit codes and after every start in the same
tick, also post-merge attempts), not a snapshot of card state from the tick's read phase: two cards checked in one tick
will not both start with one free slot. The start itself stays under the journal lock with a decision check right before
`Popen` (PT-311). A paused card does not hold a place in the queue; after a resume the consent returns to it. After a service
restart running commands (also processes held with any start) occupy slots, with the class computed
from the journal, and waiting ones return to the queue with the consent time; a queue entry has the `heavy` field.

A ticket whose heavy command waits for a slot has in the full card (`tickets[].statusLabel`) and on the tile the text
`w kolejce: <polecenie> (ciężkie polecenia: <zajęte> z <limit> zajęte)` (in queue: <command> (heavy commands: <busy> of <limit> busy)), e.g. "w kolejce: make test-changed
(ciężkie polecenia: 2 z 2 zajęte)" — derived from the engine's live queue, added when building the card state. A light
command keeps the text `w kolejce: <polecenie>`. The service journal records the reason for waiting once per change
("ciężkie polecenia: 2 z 2 zajęte (…)").

**Commands requiring quiet (K-39 #6, PT-376).** A performance measurement reported by a step as requiring quiet starts
by itself only on a calm machine, and while it runs nothing heavy starts (earlier the coordinator paused cards
by hand through `SetCardPaused`).

- *Marker*: a step result `outside` or `denied` with a command may have `"exclusive": true` (plan standard 1.5,
  "Step contract"); the field goes into `step.finished` as `exclusive` (every `denied` result; a wrong type — `false`, not a step
  error). The step sheet under the `outside` contract tells it to add this to every performance measurement and any other command
  whose result depends on machine load.
- *Patterns* `quietCommands` (a flow setting, an empty list by default): a command matching a pattern requires quiet
  even without the marker. Matching is the same function as for `heavyCommands` (`heavyqueue.pattern_matches`, arguments
  from `roots` and normalization), so `bash daemon/tests/perf_gate_k98.sh *` recognizes the measurement script in the worktree.
  A command requiring quiet is always heavy. A post-merge test attempt does not require quiet.
- *The quiet condition* (`heavyqueue.quiet`): no heavy ones running and the 1-minute load (`loadavg1`) lower than
  the number of cores / 2. The reading is injected (`heavyqueue.read_load`: `/proc/loadavg`, `os.cpu_count()`); a read
  error means the condition is not met, with a reason — never a start.
- *Start* (`heavyqueue.admit`): a command requiring quiet starts only when the condition is met; while it runs (or
  is just starting), no other heavy one starts; while it waits at the head of the heavy queue (the first entry in order
  that has not started), newer heavy ones do not start either (draining), so that it does not wait forever. An entry requiring
  quiet further down the queue holds nothing back. Light commands and agent steps are not held: they do not go
  through `admit`, and an older heavy consent held by quiet does not hold light ones or other projects in the queue.
- *Reasons* (service sentences): "czeka na ciszę: obciążenie 7,1 przy progu 6,0" (waiting for quiet: load 7.1 at threshold 6.0), "czeka na ciszę: biegnie make test
  (K-36)" (waiting for quiet: make test is running), "czeka na ciszę: nie da się odczytać obciążenia maszyny (…)" (waiting for quiet: machine load cannot be read), "wstrzymane: na czele kolejki czeka polecenie
  wymagające ciszy (K-36)" (held: a command requiring quiet waits at the head of the queue), "wstrzymane: biegnie polecenie wymagające ciszy: <polecenie> (K-36)" (held: a command requiring quiet is running). The ticket gets the
  sentence as `statusLabel` with `cause: quiet`, the tile — in parentheses after the queue text, and the snapshot — in
  `queue.heavy.waiting[].reason`. `queue.heavy.load` gives `avg1`, `cores`, `threshold` and `error`, and the
  `running`/`waiting` entries have `exclusive`.
- *Restart*: the quiet marker is restored from `step.finished.exclusive` and the current `quietCommands` (for running
  and waiting ones); the class is remembered until the command run ends, so a change of patterns applies to new submissions.
- *Classification error* (K-109 PT-482): only a class computed without an error is remembered (and the step marker `exclusive`).
  An error reading the project, the contract or settings — e.g. `BoardBusy` in the presentation read during a Board transaction
  held by a tick — gives "light" only for that call; a start in a tick computes the class anew. On 13.11 such an error
  remembered `make test-changed` of K-99-PT-395-1 as light and the command started alongside the K-113-PT-468-3 measurement
  (regression: `daemon/tests/test_cardflow_exclusive_tick.py`).

**One view of command runs** (K-109 PT-481, `cardflow/commandruns.py`, also exposed in `commands.py`).
A run of a command outside the sandbox is today recorded by the pair `outside.started` → `outside.finished`; the single-execution
protocol (ADR-1) will record it as `command.intent` → `command.started` → `command.finished` (PT-479/PT-485). All
readers of run history — `state.fold` (`outside_started`, `outside_launched`, and through them the queue, consents,
`mergecheck`, `rest`, counters in the snapshot), the `processidentity` identity model (roles `outside-start`/`outside-finish`,
the intent as a trace), the steward guard `guard.runs`, rerunning failed modules `testrerun.substitute`, the attempt inventory
`trialhold` and `tools/coordinator/metrics.py` — ask the adapter for the event phase (`phase`) or read ready runs
(`runs`), without comparing names. The shared fields have the same keys in both families (`step`, `ticket`, `command`, `unit`,
`log`, `exitFile`, `worktree`, `head`, `rerunOf`; at the end `exit`, `tail`), and `command.*` adds `attempt`, `executor`,
`isolation`, `class`, `consentVersion`, `source`. The end may be from a different family than the start (a run begun before
switching the write path). The adapter only reads: it does not rewrite the journal and writes nothing; since PT-485
it also exposes the fate of a step's last intent (`attempt_of`) and steps with an intent (`intended`). Recovering an unfinished `outside.started` after a restart works as
before: the unit `tide-outside-<step>`, the result from the `exit-code` file, no relaunch.

**A step command on the host through `Runner.run_command`** (K-109 PT-479, ADR-1; `tasks/runner.py`). A step's `outside` request
after consent no longer assembles argv in the engine: `commands.step_request` supplies the command, the step directory, the worktree,
today's environment (`outside_env`) and the time limit, and `HostRunner.run_command` launches a transient service
`systemd-run --user --wait --collect --unit=tide-cmd-<attempt>` (never `--scope`) with `-p RuntimeMaxSec=<OUTSIDE_TIMEOUT>`
and the wrapper `box/tide-step --command <exit-code> <result> <marker> -- /bin/sh <command file>`. `attempt` is a random
attempt identifier; each attempt has its own directory `<step>/outside/tide-cmd-<attempt>/` (`command`, `outside.log`,
`exit-code`), so an old result file is not the result of a new attempt. The command text lies in a file, because systemd expands
`${…}` in service arguments; the environment passes through `--setenv=NAME` without a value (values do not go into argv),
without service manager variables (`INVOCATION_ID`, `NOTIFY_SOCKET`…). Stage 1: `isolation: none`.

The wrapper writes `exit-code` atomically (`.tmp` and `mv`): the command's code on a normal end, and on SIGTERM
(unit stop, an exceeded `RuntimeMaxSec`) immediately `zatrzymane` (stopped); then it forwards the signal to the command and waits
for its end (the early write protects the result from SIGKILL after `TimeoutStopSec`). `zatrzymane` from the file is the final
result only when the unit is already gone (`gone`): as long as the command's processes live (`active`/`deactivating`) or the state
is unknown, the engine waits — it does not free the queue slot and does not resume the step (like PT-315 on the old path).
The same goes for the project/total slot occupancy and the set of running heavy commands (`outside_busy`, port `unit_gone`
= `HostRunner.command_state(…) == "gone"`): a `tide-cmd-*` unit entry with a `zatrzymane` file stays occupied
in every tick, also of other cards, until the unit disappears — the heavy queue will not let an `exclusive` one in next to it.
The result comes only from this file (`HostRunner.command_result`: a number or `zatrzymane`, `OUTSIDE_STOPPED`);
the `systemd-run --wait` client is only an observer — its exit code and death (e.g. a Tide service restart) are not
the result. A unit inactive or collected by `--collect` without a file after `OUTSIDE_STOP_GRACE` from the start →
`zatrzymane`; an unknown systemctl state → waiting. Safety net: `COMMAND_STOP_MARGIN` s after the limit with no result the engine once,
after a fresh journal read, stops the unit (`systemctl --user stop --no-block`); the result still comes
from the file.

Recording: `command.started` after obtaining the handle and `command.finished`, both with `attempt`, `executor: host-isolated`,
`isolation: none`, `class` and `consentVersion` (empty until stage 2), `unit`, `exitFile`, `loadavg`, `cores` (a load
reading from the moment of writing in the K-100 PT-423 shape: `[l1, l5, l15]` and cores, `FlowEngine.machine_load`), `source: step`. The new path does not write `outside.*`; a run begun before
the switch ends in its own family (`outside.finished`). Recovery after a restart for `command.started` without a
result: the unit lives → waiting, ended → the result from `exit-code`, no restart.

**A durable intent before start and proof of no process** (K-109 PT-485, ADR-1 points 1, 2 and 5). `_start_outside` under
the journal lock, after a fresh consent check, assigns a durable `attempt` (`runner.new_attempt`), derives from it the unit
and the attempt files (`runner.command_files`) and with **a single write** `command.intent` persists the command, `attempt`, `executor`,
`isolation`, `class`/`consentVersion` (empty until stage 2), `unit`, `log`, `exitFile`, `worktree`, `head`, `rerunOf`,
`source` and the budget reservation from the current queue decision: `heavy`, `exclusive` (the class computed at admission
in `outside_ready`) and `project` (the project slot). Only then does `run_command` start `tide-cmd-<attempt>`; there is no other
way to start a step command unit. The intent occupies the step in the card state (`outside_started`) and a slot
in `outside_busy`/the heavy queue (also after a restart: `commandruns.pending`, the class from the intent fields in `_started_heavy`)
until `command.finished`; the same decision does not get a second intent (`outside_history` refuses starting a step
with any intent, also a resolved one).

Whether the command runs at all is decided by a one-time **attempt marker** `attempt-marker` in the attempt directory
(next to `exit-code`, a `0700` service directory), created with `open(O_CREAT|O_EXCL)`: the `tide-step --command` wrapper
creates it before starting the command with the content `run` (`set -C`); when it already exists, it ends with code 125 without the command
and writes `exit-code` = `zatrzymane`. Recovering an intent without a start (`commands.outside_recover`, from `outside_ready`
in the tick, under the journal lock after a fresh read) creates the same marker with the content `abandoned`
(`HostRunner.abandon_command`): success (or an `abandoned` marker from an earlier recovery) — the command will never
start, even if the unit appears later → `command.finished` with `exit: zatrzymane`, `reason: not-started`,
releasing the reservation and resuming the step with this result; failure (`run` or an incomplete wrapper write) — the command
started → only then the state of `tide-cmd-<attempt>`: alive or finished/collected → `command.started` with `recovered:
true` and the result as usual from `exit-code` (no file after the unit ended → `zatrzymane`); an unknown state or a marker write
error → waiting until the next tick. A mere "no unit" decides nothing. The unit name and attempt directory
from `attempt` are a second safeguard: a repeated `run_command` with the same `attempt` ends in a collision. Recovery never
launches the command; a rerun requires a new decision (a new step) and a new intent. Tests: restart through
`FlowEngine.tick` in `daemon/tests/test_cardflow_command_host.py`, the wrapper and marker with real processes
in `test_runner_command.py`, a race with a real `systemd-run --user` (case `f`) in `test_runner_command_systemd.py`.

The post-merge test attempt
(merge-check) goes since PT-483 through the same protocol (`commands.merge_check_start`: an intent with `source: merge-check`,
`heavy: true`, `Runner.run_command`, start; recovery `commands.outside_recover` with the pseudo-step history). Neither the engine nor
`commands.py` assemble the start argv any more; `--scope` stays only for stopping and reading `outside.*` runs
from before the change. A step in a box still gets `box-outside`. A test with a real user manager:
`daemon/tests/test_runner_command_systemd.py` (skipped in the step sandbox).

**Queue state in the snapshot** (K-39 #4, PT-374, `cardflow/queueview.py`). The top-level `queue` field of the board
snapshot (shape in the snapshot table) is computed by the engine **once per tick** from the `FlowState` objects of the same read phase
(`journal.state`), and `snapshot_json` only attaches it — without journal reads or step file reads in the snapshot path.
Running: `outside_started` (the command and `since` from `outside.started`, the ticket from `steps` or `merge_check_steps`;
a post-merge test attempt is a `merge-check-*` pseudo-step with `kind: merge-check`), waiting: `outside_queued` and attempts
in the `queued` stage, with the command and consent time from the card state; order and reason from `heavyqueue.admit`. The command's class
is the same classification as at start (`_outside_heavy_of`). The consent decision for a command outside the sandbox has
in its technical part `queue: "heavy" | "outside"`. The decision window (`decision.js`) computes from this only the arithmetic
of the forecast "Ruszy od razu: n, poczeka w kolejce: m" (Starts at once: n, will wait in the queue: m) (heavy: `limit` minus running and waiting; light: the rest of the project
and total limit) and knows neither the limits nor the service's texts; without the `queue` field it shows just the number of tickets.

Decisions are passed by the D-Bus method `/Board ResolveCardStep(id, request_json)` with `choice` equal to `answer`, `allow`,
`deny`, `retry` or `change-executor`; correspondingly `answer` or `executor` is given, and for a chain `allow`
(with `choice=allow`) or `instead` (with `choice=deny`). Retried steps get
separate directories `.resume-<step>.step` and result files; the journal allows restoring the decision after a service restart.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.ResolveCardStep K-12 '{"choice": "answer", "answer": "ISO 8601"}'
```

### Notes for the next step resume (PT-245)

`/Board AddCardStepNote(s id, s request) → s` takes a JSON object:
`{step, orderSeq, requestId, text}`. `step` is a specific current step, `orderSeq` comes from
`derived.flow.orderSeq` (the number of the `card.started` event), and `requestId` is a non-empty client key of up to
200 characters. The text must be non-empty after trimming whitespace and at most 16 KiB UTF-8.
The response `{ok: true, id, noteId}` confirms a durable `step.note-added` write in the journal, without
stopping a process or launching a session. The same text, step and key in this assignment return
the same `noteId` (also after delivery); a different text or step gives `note-conflict`. A stale
assignment gives `stale-order`, a finished or replaced step — `stale-step`, a bad text — `bad-note`.
The write and the idempotency check are under one journal lock.

The shared resume preparation attaches pending notes in the order they were written. It covers an answer,
retry, executor change or handoff, a takeover in the Console and an automatic resume after a
command outside the sandbox. The successor points to `deliveredNoteIds` in `step.started`; the `step-start`
intent stores the same list for recovery after a crash. A failed start without a durable
successor leaves the queue. When taking over an existing step the list is written by `step.entered`.
When an unclaimed Console task is atomically cancelled, `step.console_closed.cancelledNoteIds`
reverts the confirmation only of notes attached to that attempt (`consoleNoteIds` in the step state).
A Console attempt has an empty resolution file in the step's durable input directory, under a name
composed of `processFile` and a unique `consoleUnit`. The launcher and the cancellation atomically rename
it to `.claimed` or `.cancelled` respectively; only one operation wins.
The file and the directory change are synced to disk before confirmation. A durable `.cancelled`
allows restoring the reversal after a crash before the event is written, also after the runtime is lost or
swept by `TaskFiles.sweep()`. The runtime file still serves to pass the command;
a late launcher will not start the task after a durable cancellation. Older tasks without this
trace keep handling the runtime `.cancelled` marker. A failed cancellation or merely
closing the Console does not revert delivery.
A confirmation means attached to the prompt, not read by the model. A subsequent resume
does not repeat the block of already delivered notes. The session may still remember earlier instructions.

The queue is limited to the `parentStep` chain of the same ticket and assignment. An independent
review, another ticket and a new assignment do not receive notes. A note does not extend consents to commands,
does not approve a plan and does not force a resume after the work has ended.

The full card in Board and Sessions shows a form at a specific step and
`flow.stepNotes[] = {noteId, step, text, orderSeq, requestId, status, statusLabel, successor, …}`.
The status is `pending` ("oczekuje", pending), `delivered` ("przekazana — <następca>", delivered — <successor>) or `undelivered`
("nieprzekazana — krok zakończony", undelivered — step ended). `flow.steps[].canAddNote` determines the form's availability.
An error/question/consent can still wait for a resume; ending without a resume keeps the note visible
as undelivered. The form keeps the text and the retry key after a write error.
The write response settles the draft by `requestId` even after another card is opened; returning to the first
one allows sending the next note after a success or retrying the write with the same key after an error.

### Whose fault a step error is and "Opisz problem i ponów" (Describe the problem and retry) (PT-125)

A step ended with the result `error` or `stopped` has in `step.finished` the fields `fault` (`tide`, `model`, `user`,
`provider`, `unknown`) and `faultCause`, computed at the moment of the result from the reason and the journal of that step (`out/step.log`,
`cardflow/faults.py`). `tide` is given only by a hard premise: no result file (`no-result`) and a refusal to write in this step's
output directory in a tool or command result (a `Write`/`Edit` refusal in `permission_denials`, "Read-only file
system", "Permission denied" at this path) — text written by the agent is not evidence — or the reasons set
by Tide (`start`, `lost`). A limit code (429, a limit event) gives `provider`/`limit`, an explicit API authorization error
(401, 403) or outage (5xx, overload) — `provider`/`auth` or `outage`, a bad result file or verdict — `model`.
When the journal does not settle it, `unknown`, and the window says outright that it is not known whose fault it is. A limit never creates
a decision in `flow.decisions[]` (D3); the old panel fields (`flow.action`, `flow.question`, `flow.command`,
`flow.canAllow`, `flow.error`, `flow.limit`, `flow.fault`) no longer exist (PT-135) — `derived.flow` carries decisions
only in `decisions[]`; `derived.question` and `note` stay at the card level. An error tile caused by
Tide says "błąd Tide w kroku X" (Tide error in step X).

A `tide` or `unknown` error has in the decision a field "Opisz problem i ponów" (`reply` with `method: "ReportStepFault"`,
`preview: "PreviewStepFault"`, `field: "description"`). The window first shows a card preview, then saves:

| Component | Signature | Description |
| --- | --- | --- |
| `PreviewStepFault(s id, s request)` | `→ s` | `{"step", "description"}` → `{ok, preview: true, action: "create"\|"append", card, project, title, description, signature, fault}` without any write |
| `ReportStepFault(s id, s request)` | `→ s` | creates a card in the Backlog of the `plasma-tide` project (`create_card`) or appends an occurrence to an open card with the same signature (`update_card`), records `step.reported` and retries the step through `ResolveCardStep` (`retry`); the response like `ResolveCardStep` with `reported`, `action`, `project` |

The target project is fixed (`plasma-tide`) — the request does not give it; without this project on the board the report ends
with a `no-report-project` error. The error signature is the step kind, `fault` and the cause (`plan-review/tide/write-denied`),
written in the card body as the line "Podpis błędu: `…`" (Error signature). An open card is a card not archived and not merged (its
flow without `card.main-merged`); for an archived or merged one a new one is created. The excerpt comes only
from the step journal (the last 30 events in human form) and is masked before writing: values of environment variables
(`NAME=…`), known tokens (`sk-…`, `ghp_…`, `github_pat_…`, `xox…`, `perm:…`, JWT, `Bearer …`), key–secret pairs
and values of the service environment's secret variables. The user's description also goes through masking.

### Full card: phases, waiting reasons and running steps (PT-128)

A card with a journal gets in `derived` the full card fields (`cardflow/fullcard.py`); the view
`apps/common/qml/card/CardFullView.qml` only displays them:

- `phases[7]` — `{id, label, state, executor, ts, note}` in a fixed order: `order` (Zlecenie, Assignment), `planning`,
  `plan-review`, `plan-approval`, `tickets`, `closing` (Domknięcie, Close-out), `main-merge` (Scalenie, Merge; after success the full name of the recorded target). `state`:
  `done`, `now`, `you` (waiting for a decision from `flow.decisions[]`), `hold` (waiting for a limit, not for the user), `todo`,
  `skipped` (planning with the "Od razu" entry, review of a plan approved without review). `executor` is the name of the
  executor or `user` for user phases, `ts` — the moment of the phase event (ISO 8601), `note` — a remark
  (e.g. "fala 2 z 4" (wave 2 of 4), "prosi o zmiany" (asks for changes)). A card in "Czeka na mnie" with no decision in any phase has `you` in the current phase.
- `tickets[].waitingOn` — `{kind: "user"}`, `{kind: "blocker", ticket}` (the first unmerged blocker) or
  `{kind: "limit", until}` (the renewal time, when known) or `null`; with several reasons at once the user wins,
  then the blocker, then the limit. Before plan approval always `null`.
- `tickets[].status` and `statusLabel` — a human state (`w planie` (in plan), `w kolejce` (in queue), `pracuje` (working), `review`, `poprawki` (fixes),
  `scalanie` (merging), `czeka na Ciebie` (waiting for you), `czeka na limit do 16:30` (waiting for the limit until 16:30), `czeka na PT-1` (waiting for PT-1), `scalony` (merged), `odrzucony` (rejected)), plus
  `executor` and `reviewer` from the assignment or the plan.
- `flow.running[]` — only steps in progress (`id`, `ticket`, `kind`, `kindLabel`, `executor`, `mode`, `startedTs`,
  `lastActivityTs`, `canEnter`); `flow.headline` — one sentence for "Twój ruch" (Your move), when `flow.decisions[]` is empty.

The `step.waiting` event carries `cause` (`quota` — the limit window, `slots` — provider steps at once, `local` — the local
model queue, `paused` — the card is paused) and `until` (the renewal time for `quota`). Older events without
`cause` are recognized by the text of the reason. Tickets and plan waves count also when a step stands at a limit.

### Provider limit: stopped steps and transfer to Codex (PT-130)

A limit is not a decision (D3): it is not in `flow.decisions[]`. **A change to the column rule** (the only one in this ticket):
a card with a step stopped by a limit (`step.finished` with `reason: limit`) stands in "W toku" with the marker "czeka
na limit do HH:MM" (waiting for the limit until HH:MM), not in "Czeka na mnie". An exception: when saving changes (WIP) needs repair, the card stays in "Czeka na
mnie" — that is work for the user, not a limit. Without a user choice Tide waits for the renewal by itself: after the renewal
it resumes the step with the same provider (as after choosing `wait-limit`), and the column is computed by the existing rules. The
`auto` mode with an executor set still transfers the step by itself.

Fields in `derived.flow`:

- `stoppedByLimit[]` — `{step, ticket, wipFiles, provider, until}` for each stopped step; `wipFiles` is the
  number of files in the WIP commit.
- `otherProviders[]` — `{provider, remaining}`: the remaining limit (%, the lowest fresh window) of other cloud
  providers from the AI Limits read (`cardflow/limits.py`); a provider without a fresh read does not go on the list.
- `limitTransfer` — `null` without stopped steps, otherwise `{available, provider, label, text, executor, cost,
  reason}`. Available (`available: true`) when another provider has a limit above the threshold and an executor in the catalog
  (preferred: the executor from the handoff settings, then the same class as the stopped step). `cost` is the cost
  of the transfer: "2 zatrzymane kroki i 17 plików WIP do przekazania; po przeniesieniu Codex ma jeszcze 91%
  limitu." (2 stopped steps and 17 WIP files to hand over; after the transfer Codex still has 91% of the limit.) — the numbers of steps and WIP files and the remaining limit of the target provider, with no currency conversion.
  When there is no provider with a free limit: `available: false` and one sentence in `reason`.

"Przenieś na Codex" (Move to Codex) in the full card sends the existing `ResolveCardStep` (`choice: transfer`, `step`, `executor`)
once per stopped step; the cost is visible above the button before clicking.

### Stack detection and stack templates (PT-193, card box)

`tasks/stack.py` → `detect(worktree)` returns a `StackReport`: facts only, no decisions. The module only reads files
(does not run `npm`, `dotnet` or `make`, does not use the network) and is deterministic: directories are walked
alphabetically (without `node_modules`, `bin`, `obj`, `.git`…), lists without repeats in a fixed order. The report:

- `stacks` — template names in the fixed order `node`, `playwright`, `dotnet`, `docker`, `python-qt`; `[]` when the
  project has no pattern;
- `node` (`package.json`, `.nvmrc`/`engines`, `angular.json`, `karma.conf.js`, `playwright.config.*` with `*PORT` variables),
  `dotnet` (`*.sln`, `*.csproj`: TFM, `Directory.Packages.props`: Testcontainers and `Microsoft.Playwright`, `global.json`),
  `python` (`Makefile`, `mk/*.mk`, `pyproject.toml`, `requirements.txt`: `test`/`lint` targets, Qt offscreen),
  `docker` (`docker-compose*.yml`), `testcontainers_images` (`"<image>:<tag>"` literals in `*.cs`/`*.ts` files
  using Testcontainers), `ci` (`.gitlab-ci.yml`, `.github/workflows`: images, commands; a deploy with `rsync`/`ssh`
  is ignored), `nuget_sources` and `internal_addresses` (registries from `NuGet.Config`/`nuget*.config`; an internal
  address = a private IP, a name without a dot or a suffix `.local`/`.lan`/`.internal`/`.corp`/`.biz`).

`propose_profile(report, project)` builds from the report a dictionary in the `tide-box: 1` shape (schema validation is done by the
consumer, not this module) and an `items` list (entries for the consent bundle: `kind`, `value`, `risk`, `enabled`, `description`).
Invariants: never `network.full`; a red item is never enabled; an internal address from `NuGet.Config` is a
disabled red item with a description (it does not go into `network.domains`); a Testcontainers/compose image goes into
`docker.images`; `stacks: []` gives an empty proposal with a `question` field (a question for the plan window, not for steps).

**Stack templates** are YAML data in `tide_daemon/box/stacks/<stack>.yaml` (`base`, `node`, `playwright`, `dotnet`,
`docker`, `python-qt`), not shell code. Each has `domains` (`name`, `risk`: `green`/`yellow`/`red`, `note`, optionally
`registry: true` for package registries), `apt` and `images` (the `docker` template carries Ryuk with a pinned version and a K1
risk description in `image_notes`). The templates' `domains` contain no `github.com`, `gitlab.*` or internal addresses;
the exception is the `build-domains` list (PT-192) — domains only for the template script at build time (`docker` →
`github.com` for the runc release), never for steps. The project does not use a YAML library, so `box/templates.py` reads
a subset (scalars, lists, lists of maps). Functions:

- `work_domains(profile)` — the sum of the `domains` of the `base` template + the templates from `stacks` + `network.domains`, without repeats
  (the step policy in the proxy); an unknown stack → `ValueError`;
- `build_domains(profile)` — `work_domains` + the templates' `build-domains` (the box build list for the proxy). Similarly
  `build_apt(profile)` and `build_images(profile)`;
- `known_domains()` — a dictionary domain → risk class (on a discrepancy the more dangerous wins), for `classify`;
- `registry_domains(stack)` — domains with `registry: true`; `tasks/node.REGISTRY_DOMAINS` and
  `tasks/dotnet.REGISTRY_DOMAINS` are composed from them (values as before the change).
## Scenario 03.11 — evaluation on a real model

PT-156 separates two things: a wiring test through `FlowEngine` on stubs
(`daemon/tests/test_cardflow_caretaker_0311.py`) and a manual evaluation of the diagnosis by a real
executor. The stub gets a ready answer; its success measures neither model quality nor "95%".
The test checks the journal, `engine.derived()`, the `in/context/` directory, the guard and the effects of actions.
In this suite the git boundary when writing the plan and preparing review is a stub; the git
sandbox tests remain in a separate suite. This does not mean a system isolation test.

The materials are in `tests/fixtures/caretaker-0311/`: the subdirectories `1`, `2`, `3`, `4a`, `4b`
contain a reconstructed card journal, a step excerpt and an evaluation criterion. They are a reconstruction
of the causes from the K-37 sheet, not copies of private journals. Paths are conventional,
secrets masked. Do not load them into a working Board.

Manual procedure, outside `make test`:

1. For each of the five subdirectories open a fresh session of the chosen real
   executor in the Console, with the repository read-only. Note the executor's name,
   model, reasoning settings, date and session identifier. Do not give it `expected.md`.
2. Pass `journal.jsonl`, `step.log`, `docs/agent-plan-standard.md` and
   `docs/sandbox-and-permissions.md`. For case 2 attach the rejected
   `2/plan.md` and the sheets `2/tickets/1.md`, `2/tickets/2.md` from the materials directory;
   the previous proposal and errors are in the journal. The "Krok" (Step) heading in the excerpts of
   cases 1, 2 and 4a points to a specific `step.started` event.
   In case 3 the approved base and decision A are in the journal. For 4b evaluate both
   runs, not just the last error line.
3. Use the task: "You are a Tide card steward. Based on the attached sources,
   diagnose the cause and propose one action from the steward contract. Do not perform
   actions, commands or writes in the repository or the Board. Return JSON like a step
   result: result=done, action and the required fields. The allowed ones are retry (step,
   instruction, reason), return-plan (feedback, reason), retry-stage or escalate
   (summary, recommendation); an optional report contains description. You may not
   approve a plan, give consent, merge or reject a ticket. Paths in the material
   are conventional. After the JSON explain the diagnosis, pointing to specific events and lines." (the task is given to the executor in Polish)
4. Save the raw response outside the repository before opening the `expected.md` of the given
   case. Compare separately: the accuracy of the cause, its grounding in the sources,
   the completeness of the action parameters and keeping the boundaries of the user's decision. A wrong
   explanation with an accurate action name does not pass the diagnosis.
5. In the K-37 card note record for each case: the executor/model, session, JSON
   result, an assessment of each criterion (yes/no with justification), a quoted masked
   piece of evidence and a possible Tide gap. Keep the model's response; do not add it to
   the test expected.md. Do further evaluations in new sessions, without the previous responses.

The evaluation is not a test gate and does not run automatically. The tests do not call
real models. The coordinator refinement of 06.11 keeps the PT-143 contract:
a bad result or a forbidden action immediately hand over exactly one decision
with a recommendation and a summary of one attempt (`test_bad_*`). The limit concerns errors
in executing a correct action (`test_failed_action_limit_*`), e.g. pointing to a foreign step
in a retry or a persistent stage error. Each of the five cases has both variants;
there are no tests marked as expected failures. When a bad result carries no model
recommendation, Tide adds a technical hint to check the error and choose
the next action. It is not a diagnosis attributed to the model.

## Card steward (K-37)

The card steward is a background step of kind `caretaker` ("opiekun"), which Tide launches itself on a card problem, before
asking the user (PT-143). The whole steward without Qt and without disk is in `daemon/tide_daemon/cardflow/caretaker.py`:
the public `problems()`, `owned()`, `brief()`, `parse_result()`, `problem_id()` and the registries `TRIGGERS`, `ACTIONS`,
`HISTORY_LABELS`, `BRIEF_SECTIONS`. Subsequent tickets only add entries to the registries and their own `####` in the subsections
below. The engine has one entry point: `FlowEngine._caretake(card, st)` at the end of `_tick_card` — after the existing
automations (retry, a stronger executor, an environment answer, auto-refusal) and before `_queue_notices`;
the steward step ends through the existing `_poll`/`_finish` (the `_finish_caretaker` branch).

The course: a trigger gives a problem → `caretaker.started` (`problem`, `key` = `caretaker:<problem>:<n>`, `n`, `step`,
`target` — the problem's step, `root`, `ticket`, `trigger`, `watermark` — the last journal `seq` at the moment of start) →
the existing "intent → result" pair `step-start` and `step.started` with `kind: caretaker` → the steward's result in
`<step>/out/result.json` → `step.finished` with `outcome` (the `parse_result` result) → an action from the registry or a failed
action → `caretaker.escalated`. Actions other than handing over a decision end with `caretaker.action` (`problem, step,
target, ticket, action, params, ok, error, code, label, retried, stale, detail?`) after the "intent → result" pair
`caretaker-action` — "Retry, limit and stale actions" below (PT-150).

State and view:

- Steward steps are not in `FlowState.steps` (separately `caretakers`, `caretaker_runs`, `caretaker_outcomes`,
  `caretaker_escalations`, `caretaking`), so no trigger, "ticket's last step" or decision sees them —
  no recursion. `running()` covers them (provider slots, stopping at Done/Archive, `busy_reason`).
- As long as the steward works, the problem's decision is hidden from `flow.decisions[]`, the card stands in "W toku" with the marker
  "opiekun sprawdza <krok>" (the steward is checking <step>), and `_queue_notices` does not notify (also in a pass in which the step ended with an error
  before the steward started). `ResolveCardStep` for that ticket refuses (`no-step-question`, a step of that ticket is in progress).
- `derived.flow.caretaker` — `{enabled, working[], escalated[]}`: `working[]` is `{problem, step, target, ticket,
  label, startedTs}`, `escalated[]` — problems handed to the user whose decision is waiting: `{problem, step,
  target, ticket, summary, recommendation, optionId, failed, error}`. `flow.running[]` has the steward step with
  `kind: caretaker`, `kindLabel: "opiekun"` (`labels.KIND_LABELS`), `canEnter: false`.
- Escalations are stored by problem id, also when `target` is empty. The view and the decision annotation
  use the lifecycle in `owned()`: they show only the escalation of the last action for an open problem.
- A handed-over error decision has the field `caretaker{summary, recommendation, tried[], optionId, failed}` (`tried[]` —
  actions since the last close other than the handover itself: `{step, action, ok, reason, text}`, without
  stale ones); the option from `optionId` is recommended
  and first, none is selected (D10). Rendering in the decision window is #15. An escalation notifies once
  (an ordinary `step.notice` of the problem's step).
- History (`flow.history[]`, kind `caretaker`, `actor: "caretaker"`): "Opiekun: oddaje decyzję — <streszczenie>" (Steward: hands over the decision — <summary>)
  and "Opiekun: nie udało się wykonać działania „<akcja>” — <błąd>" (Steward: failed to perform action '<action>' — <error>); a result rejected by the guard (PT-158):
  "Opiekun: wynik odrzucony, akcja nie wykonana — <powód>" (Steward: result rejected, action not performed — <reason>); PT-150: "Opiekun: ponowił <krok> z instrukcją —
  <powód>" (Steward: retried <step> with an instruction — <reason>) (without a separate "Decyzja: ponowienie kroku" (Decision: step retry) entry — a `step.decision` with a `caretaker` field has no entry),
  "Opiekun: nie udało się (ponowienie kroku) — not-problem-step: …" (Steward: failed (step retry)), "Opiekun: działanie „<akcja>” nieaktualne, nic
  nie wykonano — <powód>" (Steward: action '<action>' stale, nothing performed — <reason>) and "Opiekun: oddaje decyzję po N działaniach (limit) — <streszczenie prób>" (Steward: hands over the decision after N actions (limit) — <summary of attempts>).

**Problem id and counting actions.** `problem_id` is the only place that builds the id (without `seq`): a trigger with a step —
`<trigger>:<root of the parentStep chain>` (`error:K-37-PT-7-1`), a repeated cause `repeat:<ticket>:<cause>`,
without a step `<trigger>:<stage>` (`flow-error:<stage>`, `stalled:card`), a waiting decision `waiting:<decision id>`.
One step chain has at most one problem at a time — a subsequent trigger on it joins it (`triggers[]`). `owned()`
is the only counting: steward steps with the given id since the last close (a user decision without a `caretaker` field
or a chain step ended `done`); it returns `count`, `runs`, `next` (a number for the key, never repeats),
`open` and `handed`. Subsequent errors of a retried step are the same problem; a user decision resets the counter without
changing the id. For a problem without a step a user decision or an ordinary step ended `done` closes the problem
within the pointed ticket, and without a ticket — the whole card. A steward step result and automation decisions do not close the
problem. A recurrence has the same id, the counter from zero and the next, unique number `next`.

**Restart.** `caretaker.started` without `step.started`: the `step-start` result exists — `step.started` is appended by
`_recover_started_steps`; an intent without a result is settled by `_reconcile`; without an intent — `_caretake` starts a step with the same
id. A failed start (also "interrupted by a restart") is a failed action. A step never starts a second time.
A steward action interrupted between the intent and the `caretaker-action` result — "Retry, limit and stale actions".

**When there is no steward** (today's decision without a steward step): the card is paused, in the Backlog, Done or
Archive, a `login` error, a provider limit, a `user` fault, the steward disabled in settings (`caretakerEnabled: false`,
without a note) and a "Tylko lokalnie" (Local only) card (`preferredProvider: local`) without a local executor in the catalog (a decision
with the note "opiekun niedostępny dla pracy tylko lokalnej" (steward unavailable for local-only work)); in the last two `flow.caretaker.enabled: false`
("Ustawienia" (Settings) below, PT-151).

### Triggers

`TRIGGERS` is a list of functions `(st) -> [candidate]`, where a candidate is `{trigger, step?, ticket?, stage?, cause?, decision?}`.
An entry in the registry is enough to start a steward, including for a problem without a step (`flow-error:<stage>`, `stalled:card`):
`caretaker.started` then has an empty `target`, and the steward step works in the ticket's worktree (when the candidate has a `ticket`)
or in the card's working directory (`card-branch`); `problems()` always checks that the problem is still current before the start.

#### Ticket step error (`error`, PT-143)

The last step of a ticket (`ticket`, `fix`, `review`, `conflict`) that ended with `error` after the automatic retry
(`attempt ≥ 1`) and a possible switch to a stronger executor, with no decision, no successor step, and no
ticket rejection; without `login`, a provider limit, or a `user` fault. Id: `error:<chain root>`.

#### Same reason and no progress (`repeat`, `no-progress`, PT-148)

Two `TRIGGERS` entries (`_repeat_trigger`, `_noprogress_trigger`) after `error`. Order relative to the engine's automatic mechanisms:

1. **First failure** — unchanged: automatic retry (`attempt: 0` → `step.retry` → attempt 1), then
   (attempt 1) a switch to a stronger executor, then the steward's `error`. The trigger replaces only the next
   *blind* attempt with the same cause; the switch to a stronger executor, provider limits (D3), and `login` stay.
2. **`repeat` (trigger 2)** — two consecutive `step.finished` events in the same ticket (or the same kind of plan
   step, when the step has no ticket) with the same `result` and the same cause: `faultCause`/`reason` through
   `faults.of_finished` (a free-form agent reason counts as the same cause only when the text is identical,
   `other-<hash>`), and for `denied` — the same command after the chain's automatic denial (`_auto_deny`) or after
   the `step.retry` of that problem's steward. Further denials after its instructions stay within the same limit. Does not
   apply to `login`, a provider limit, or a `user` fault. The candidate has no decision (`decision` is empty), so `retry` goes
   through the path without a decision (`step.retry` with `caretaker` and `instruction`), and `stale` follows the `watermark` rule.
   Id: `problem_id("repeat", …)` → `repeat:<ticket>:<cause>`, and for plan steps `repeat:<step kind>:<cause>`
   (without `seq`); the action counter is `owned()`. When the chain already has a problem (e.g. `error:`), the candidate joins it
   (`triggers[]`, the id of the known problem) — the steward's retry and further errors with the same cause count toward a single
   `caretakerActionLimit` limit.
3. **Instead of a blind attempt** — `FlowEngine._caretaker_takes_over` (`caretaker.takes_over`) is asked in three
   places of the automatic retry of attempt zero (`_advance`, `_advance_planning`, `_plan_step_decision`)
   and in `_auto_deny`: for a step that is the second one with the same cause, neither `step.retry` nor an automatic denial
   is created — the step waits for the steward (`_caretake` at the end of the pass). The chain's second automatic denial with the same
   command in a ticket therefore starts the steward before `AUTO_DENY_LIMIT`. Without a steward (card paused, Backlog,
   "Tylko lokalnie" (Local only)) today's automatic mechanism stays.
4. **After handing over** — when the steward has handed the problem over (`escalate` or the limit), the automatic retry does not come back: a step
   error from attempt zero becomes an ordinary user decision (`caretaker.handed_repeat` in `decisions`, `state`,
   `resolve_step`, and notifications), with `caretaker{…}` as with `error`. The user's decision closes the problem (counter
   from zero, same id).
5. **`no-progress` (trigger 6)** — a step stopped by the guard (`reason: no-progress`, without changing
   `stepGuardMinutes` and `stepstop`) after an automatic retry, also for `planning` and `plan-review`.
   The context also covers a repeated stall of attempt zero taken over by `repeat`; the first automatic
   retry stays. It joins the problem of that chain and adds a "Krok bez postępu" (Step without progress) section to the ticket sheet: the stall time (from
   `stalledSince` to the stop) and the tail of the session events (the last 5 of the step result's `messages`, 300 characters each).
   Session messages and the denied command are masked before normalization and truncation.
   The step journal in human-readable form is in the context files.

Ticket sheet (`BRIEF_SECTIONS`, `BRIEF_DETAILS`): a `repeat` problem has a step error description and a "Ten sam powód drugi raz" (Same reason a second time) section
(both steps, the cause, the reasons or the denied command, a warning against a blind repeat); for a problem opened
by `error`, the sections of the attached triggers are appended after the error description. Everything goes through `faults.mask`.

#### Tide error and the card is stuck (`flow-error`, `stalled`, PT-147)

Two triggers without a culprit step and without a decision (`caretaker.started` with an empty `target` and `decision`):

- **`flow-error`** (trigger 4) — a Tide error (`flow.error`) that persisted. The engine records the errors of a single
  pass through `FlowEngine._flow_error`: `flow.error` once for the same stage and text, with the fields `stage`, `step`,
  `ticket`, and `at` (the engine clock); the same text in a later pass at least 10 s after the first one
  (`FLOW_ERROR_PERSIST`) appends one `flow.error-persisted` (`error`, `stage`, `since` — the `seq` of the first one).
  An error repeated with the same text after it disappeared is also a problem (a new `flow.error` with a text that already occurred since
  `card.started` in the same stage). Observations, persistence, and repetitions are counted separately for each stage:
  parallel errors A/B are two problems, even with identical text; escalating B does not hide A.
  A one-off error that did not occur in the next pass is not a problem. Stage (`stage`):
  `advance:<ticket>` — the ticket's next stage after its last step, `plan-next` — the start of the plan's next ticket,
  `advance` — the first step, `close` — card close-out, `plan-commit` — the plan commit. Id:
  `flow-error:<stage>` (e.g. `flow-error:advance:PT-7`); the steward works in the worktree of the ticket from `flow.error`, and without a
  ticket — in the card's directory. The error disappears when a step of its stage starts (`step.started`) or the stage runs without an error
  in an ordinary pass or after a retry (`flow.error-cleared` with `stage`; a successful `result` of the `plan-commit` action
  clears the commit error without starting a step). Resolving one stage does not clear the errors of other tickets.
  A skipped close-out (e.g. a steward step is running) does not erase the `close` error. The plan commit happens once per pass,
  before starting further tickets; its failure belongs only to the `plan-commit` stage, also after planning has finished.
  An ordinary pass also writes the final state of a finished plan.
- **`stalled`** (trigger 5) — the card is stuck (`card.stalled`: 3 minutes without a step, an action, or a named reason for waiting)
  and still has no named reason (`guard_reason` computed without the steward's work: its step, waiting for a slot, and "intent → result"
  pairs). A limit, a provider slot, a pause, a decision, a plan awaiting approval, a running step, or a started
  action — there is no problem. A card that is stuck on a Tide error that is already a `flow-error` problem does not get
  a second problem. Id: `stalled:card`, the card's directory.

The steward's work is not the card's work: `caretaker.*`, the steward's steps, waiting, and "intent → result" pairs,
`flow.error-persisted`, and `stage.retry` do not move `last_activity_ts` and do not clear "stuck"
(`state._bookkeeping`; the exception is the user's `caretaker.decision`, which starts a new idle period).
While the steward is working, the card has the tag "opiekun sprawdza kartę" (steward is checking the card) (for a ticket error —
"opiekun sprawdza PT-…" (steward is checking PT-…)) instead of "stoi" (stuck) or the error text; after escalation (or after the limit) today's error
or stall tag returns, the diagnosis is in the history ("Opiekun: oddaje decyzję — …" (Steward: handing over the decision — …)) and in `derived.flow.caretaker.escalated[]`,
and `flow.decisions[]` gets a "steward" decision (PT-154). While the steward is working, the stall notification waits until
escalation; without an available steward it stays with `card.stalled`.

### Steward decision

Escalating a `flow-error:<stage>` or `stalled:card` problem, also after the action limit, yields the kind `caretaker`
in `decisions.KINDS` and `derived.flow.decisions[]` (PT-154). A non-empty `happened` and `why` describe the card's stop
and the need for a decision. The `caretaker{summary, recommendation, tried[], optionId, failed}` field feeds the shared
"Opiekun poleca" (Steward recommends) block and the collapsible "Co próbowano" (What was tried). The Board and Session windows show "Ponów etap" (Retry stage), "Zamknij" (Close),
and "Porozmawiaj z opiekunem" (Talk to the steward); they select none by default. The notification about this decision is created once, with a `flow.notice` entry
in the journal, also after a service restart. Existing step decisions still use their own methods.

`ResolveCardCaretaker(s id, s request) → s json` on `/Board` (`org.tide.Daemon.Board`) takes a JSON object:
`{"choice":"retry-stage"}` or `{"choice":"dismiss"}`. The optional `problem` gives the problem id (the window passes
it from the decision's options); without it the method chooses the first steward decision. An unknown choice gives `bad-choice`,
no pending decision or an outdated problem — `not-waiting`, invalid fields or format — `bad-request`.

Both choices write `caretaker.decision` and close the problem. `retry-stage` additionally retries the stage with a single
`_advance_card` pass through the existing `stage.retry` path; `dismiss` performs no action. The next
occurrence of the error or stall counts actions from zero. Closing a stall starts a new idle period;
closing an error removes its current marker, so the next occurrence is a new observation of the stage.

The `caretaker` field is solely an internal marker of a steward action: `ResolveCardStep` with this field
in a D-Bus request returns `bad-request`. The signatures of existing methods stay unchanged.

### Talking to the steward

`/Board TalkToCaretaker(s id, s request) → s json` takes `{}` or `{"problem":"<problem id>"}`
and returns `{ok: true, step}`. A full card with a journal, before it finishes, gives the "Porozmawiaj
z opiekunem" button (`derived.flow.canTalkToCaretaker`). The same option in a `caretaker` decision and in escalated
decisions passes the id of the specific problem. Without an id the conversation picks the first open problem;
when there is none, it serves to discuss the card's state, and actions that require a problem are outdated.

Tide opens a supervised Console tab in the context of the card's worktree. The `caretaker-chat` ("conversation
with the steward") step stays separate from ticket steps. It has a ticket sheet in conversation mode, masked
`in/context/` (the same 400 kB limit), the same sandbox, and the same cleaned environment as the background steward.
The program works in its own `out/`, writing only there; worktrees and branches are under the PT-158 guard.
The steward's executor choice and "Tylko lokalnie" apply. No Console gives `no-terminal`, no flow
or a finished card — `no-step`, a second open conversation of this card — `console-open`.

The conversation writes successive `out/action-1.json`, `action-2.json`, and so on, without overwriting earlier
files. Each file applies the background steward's step result contract (`result: done`, `action`, and parameters).
The whole JSON must be written in a single call of the write tool. An incomplete JSON is rejected only when the
file metadata does not change between two passes; a read during a write does not consume the action name.
`_poll_interactive` reads the files in number order, persists the validated result together with its context
in `caretaker.chat-action`, and executes it through the shared PT-150 path. The key of the "intent → result" pair contains
the conversation id and the file name. A restart between the intent, the effect, and the result does not execute the action a second time;
replacing or deleting a handled file does not change the persisted assignment.

The same registries, prohibitions, and `stale` check protect every action. An attempt at `approve-plan`, consent, or a merge
is rejected with the reason in the history. A write in the worktree or a branch move ends the conversation
with `caretaker-wrote`; until the stop is confirmed, no further action files are executed.
The history uses the signature "Opiekun (rozmowa): …" (Steward (conversation): …) and `mode: chat`. The conversation and its actions do not increase the
problem's action counter. Closing the Console ends the conversation; it does not create an ordinary decision to resume the ticket.

Ticket sheet (the `flow-error` and `stalled` sections in `BRIEF_SECTIONS`): the error text, stage, step, and ticket, Tide's hint for
the user (`flow_error_hint`) or since when the card has been stuck and the last step, the last 15 card events (without the steward's
work), and open "intent → result" pairs.

### Tide error report

The steward's result may contain, for every action and every trigger, an optional
`"report": {"description": "opis błędu Tide"}` (1–4000 characters). The report saves a card in the Backlog
of the `plasma-tide` project or appends an occurrence to an open, unmerged card with the same signature.
An archived or merged card does not accept another occurrence. Code shared with `ReportStepFault`
computes the signature, picks the card, and masks the description and a limited journal excerpt through `faults.mask`.
For a problem without a step, the signature has the form `flow/tide/<cause>`: the cause is the `flow.error` stage
(e.g. `flow/tide/advance`), and for a stall, `stalled`. The error text stays masked in the body.

`report` alone does not retry a step or a stage (A7). A separate `caretaker.reported` entry gives the history
"Opiekun: zgłosił błąd Tide (K-<n>) — <powód>" (Steward: reported a Tide error (K-<n>) — <reason>). A missing project gives a failure entry
`no-report-project`; the main action still runs. The occurrence contains a durable marker computed
from the source card and the steward step, saved together with the report body. Reapplying a saved
result does not add the report a second time, also after an interruption before the `caretaker.reported` entry and after
archiving or merging the report card. A new steward step has a separate occurrence identifier.

The `caretaker` decision offers a "Zgłoś błąd Tide" (Report a Tide error) field. `PreviewStepFault` with `problem`, `description`,
and an optional `choice` shows the card without saving. `ResolveCardCaretaker` with
`{"choice":"report","problem":"flow-error:advance","description":"…"}` saves the report and closes the
problem without retrying the stage. An empty or too long description gives `bad-request`. Existing `PreviewStepFault`
and `ReportStepFault` requests with `step` keep their behavior, including the retry after a manual
`ReportStepFault`.

### Actions

`ACTIONS` is `name → Action(kinds, params, apply, description)`; `kinds` — the triggers at which the action is
allowed (`"*"` — any), `params` — the result fields (`Param(required, minimum, maximum, hint, choices)`), `apply(port,
problem, params)` works through the engine port (`engine._CaretakerPort`). Validation is in Tide (`parse_result`), not
only in the ticket sheet: an unknown name (`approve-plan`, `allow`, `merge`, `push`, `reject`, …) → `bad-caretaker-action`;
a missing file or a bad format → `bad-caretaker-result`; a `result` other than `done` → a failed action (`question` carries
the question text as the summary, like `escalate`; others — `caretaker-result`); a stopped or not started step —
`caretaker-stopped`, `caretaker-not-started`; a step that wrote outside `out` — `caretaker-wrote` (the guard, before
result validation, "Piaskownica i strażnik" (Sandbox and guard)). Lengths: instruction/answer/note up to 4000 characters, reason 1–600,
summary up to 1200. A failed steward step result (all the codes above) hands the problem to the user right away;
a failed execution of a valid action (e.g. `not-problem-step`) is an action counted toward the limit, after which the
next steward step starts (PT-150, "Ponowienie, limit i nieaktualne akcje" (Retry, limit, and outdated actions)).

#### `escalate` (PT-143)

Hands the decision to the user: `summary` (required, 1–1200 characters), `recommendation` (required, 1–600), `optionId`
(optional, the id of a decision option of the problem). The registry has no action for approving a plan, consent, running outside the
sandbox, merging, pushing, or rejecting a ticket.

#### `retry` (PT-150)

Retries the problem's step with an instruction: `step` (required, the step id from the "Kroki do ponowienia" (Steps to retry) line of the ticket sheet), `instruction`
(required, 1–4000 characters), `reason` (required, 1–600). The instruction goes into the resumed step's ticket sheet as
"Instrukcja od opiekuna karty (… ma pierwszeństwo przed wcześniejszym planem kroku)" (Instruction from the card steward (… takes precedence over the step's earlier plan)) (`handoff.resume_context(…,
caretaker=True)`); the steward derives it from `step-log.md`, the history, and the journal in the context directory. Two paths:

- a problem with a decision (`decision` in `caretaker.started`, e.g. `error:`) — the existing `resolve_step` `retry` with the instruction
  as `answer`; `step.decision` gets `auto: true` and `caretaker: <problem>` (the engine parameter
  `caretaker_problem`, not available from D-Bus), and the resume is done by `_advance` as with a user decision,
- a problem without a decision (e.g. `repeat:` from #6) — the internal automatic retry path: `step.retry` with `caretaker:
  <problem>`, `caretakerStep`, and `instruction`, without `step.decision`; `_advance` resumes the step with `parentStep` and
  `attempt: 1`, with the instruction in the ticket sheet. Until the resume, the step's decision is hidden, and `problems()` skips that step.

Only a step from `caretaker.problem_steps(problem, st)` can be retried: with a decision — the step of that decision, without a decision —
the step of the problem's chain with no successor, ended with a result other than `done`, with no decision and no retry. Any other step
(including one from outside the card) is a failed action `not-problem-step` with the reason in `error`, counted toward the limit.

#### `retry-stage` (PT-147)

Retries the card's stage right away, once; no fields; allowed only for `flow-error` and `stalled` (for other problems
`bad-caretaker-action`). It goes through the shared action path (the `caretaker-action` pair, `caretaker.action`, the limit,
staleness): `apply` → `_CaretakerPort.retry_stage` → `FlowEngine._retry_stage` writes `stage.retry`
(`problem`, `stage`, `caretaker`, `caretakerStep`) and runs a single `_advance_card` pass — the same function through which
`_tick_card` moves the stages in every pass (the tickets' last steps, the plan's next ticket, card close-out,
plan commit), with its own steward action pair excluded from the card's "started actions". The result:

- a Tide error of the retried stage (for `stalled`: of any stage) — a failed action `stage-failed` with the error
  text (the error is recorded by the ordinary `_flow_error`; the same stage and text do not produce a new event),
- a pass without any event other than the steward's work — a failed action `stage-no-change`,
- a successful pass — `caretaker.action` with `ok: true` and the stage `label` ("Opiekun: ponowił etap "advance:PT-7"" (Steward: retried stage "advance:PT-7")); for
  `flow-error`, resolving the stage clears its error the same way as an ordinary pass; errors of other stages remain,
- a `stage.retry` of this steward step already exists (a restart between the intent and the result) — `stage-interrupted` without a second
  pass: the stage is retried exactly once, and further on an ordinary pass retries it anyway.

Failed actions count toward the limit: a `flow-error`/`stalled` problem has an id without `seq`, so further occurrences of the
same stage are the same problem; after three actions, `caretaker.escalated` with `limit: true` and the next steward does not
start. Staleness — the rule without a decision (below): the problem disappeared (the error disappeared, the card moved) or after
`watermark` there is a `step.started`, `step.retry`, or `step.decision` in the problem's ticket (without a ticket — in the card)
or a card event from the list. Resolving the given stage after `watermark` (`flow.error-cleared` or a successful `plan-commit`
result) also invalidates the action, even if the error then came back; in that case `caretaker.action` has
`ok: false, error: "stale"`, with no `stage.retry` and no increase of the action counter.

#### `return-plan` (PT-146)

Sends the plan back to the planner with the steward's note; allowed for the `plan-fix` trigger (see "Returning the plan") and for a
ticket problem `error`, `repeat`, or `waiting` for a question (see "A gap in the plan during execution").
Fields: `feedback` (required, 1–4000 characters — a plan row, a blocker, and a plan standard 1.2 error; the request "defer this
note to ticket #n" is passed here verbatim by the steward, without a separate rule for recognizing deferrals), `findings`
(optional, the numbers of the last review's notes from 1, comma-separated) and `reason` (required, 1–600, for the history).

### Returning the plan (PT-146)

**Trigger 3** (`caretaker._plan_fix_trigger`, problem id `plan-fix:<root of the fix step chain>`):

- `plan.revision-failed` — the `plan-amend` step finished, but the amended plan did not pass the format check.
  The problem has a decision `plan-revision-failed:<seq>`, which `build_decisions` hides while the steward works (like a step
  decision); after its result the decision returns with `caretaker{summary, recommendation, tried[]}` (`annotate(st, plan, target)`),
- a second failed plan format fix (`plan-fix`, round ≥ 2, `plan.checked` with `valid: false`, no proposal):
  a problem without a decision; the steward's `retry` on the `plan-fix` step (`problem_steps` allows a step ended with `done`, once)
  gives another fix step with an instruction — `_planning_result` resumes it through `_caretaker_resume`. When the steward
  hands over the problem (`escalate`, a failed result, or the action limit), `build_decisions` shows a `plan-fix-failed` decision
  (`decisions._plan_fix_decisions`) with `caretaker{summary, recommendation, tried[]}`, the format errors in `technical`,
  and the option "Ponów poprawę z instrukcją" (Retry the fix with an instruction): `ResolveCardStep retry` on the `plan-fix` step writes `step.retry`
  with `user: true` and the user's instruction, and `_caretaker_resume` resumes the step as after a steward `retry`. The decision does not
  appear while the steward is working or has not yet handed over the problem (unless the card is local and the steward is not running).
  A `plan-fix` step with a saved `step.retry` (from the user or the steward) is not a trigger until its
  successor starts (PT-475): a user retry written in the tick after `_advance_card` and before `_caretake` does not start
  a steward step that would be outdated (`stale`, outside the action limit) once the successor starts.

A plan waiting for approval after the review rounds is not a trigger (the recommendation after N minutes — #3). The steward
does not approve the plan or the diff: `ACTIONS` has no such
action (`approve-plan` is `bad-caretaker-action`), and `return-plan` does not call `approve_plan`.

**Action.** `_CaretakerPort.return_plan` calls the existing `FlowEngine.edit_plan` with `action: return` (the same validation,
idempotence, and protection of tickets that have started; `no-plan` when there is no proposal). The parameters `caretaker_problem`,
`caretaker_step`, and `caretaker_from` exist only on the engine side (they are not in D-Bus): `plan.returned` gets
`caretaker`, `caretakerStep`, and `caretakerFrom`, and the `plan-amend` step starts in the next pass of the loop
(`_advance_user_revision`), with the steward's note verbatim in the ticket sheet ("Uwagi: …" (Notes: …)). A number in `findings` outside the list of review
notes (`caretaker.review_items`, numbered from 1 — the same in the ticket sheet and in `plan-fix/review-findings.md`) is a failed
action `bad-findings` with a reason; characters that cannot be read as an index
(e.g. `²`) are treated the same way, without leaving an open action intent. The result in `caretaker.action` (`ok: false`) counts toward the limit.
A repeat after a restart recognizes its own entry by `caretakerStep` (a second `plan.returned` is not created).

**Limit.** Every action (successful and failed) counts toward the limit from #8 (`ACTION_LIMIT`). So that successive returns do not
start the limit from zero, `plan-amend` after a steward return inherits the chain of the failed fix
(`caretaker.chain_root` → `caretakerFrom`), and a plan fix step ended with `done` does not close the problem. The problem is closed
only by a user decision: returning the plan without the `caretaker` field or discarding the fix (`plan.revision-discarded`)
through `FlowState.plan_user_seq`, as well as an explicit retry of a step from this chain (`step.retry` with `user: true`, without
`caretaker`). After a user retry, the next format rejection starts the steward again with a new action limit;
its step numbers stay continuous. After the limit, the decision returns to the user with a summary.

**Staleness.** A user returning or editing the plan while the steward is working ends its action as
`stale` (`plan.returned` without `caretaker`, `plan.ticket-edited`, `plan.revision-discarded`): nothing is executed,
a second `plan.returned` is not created, and the action does not count toward the limit.

**Context and ticket sheet.** The `BRIEF_SECTIONS["plan-fix"]` section gives the format check errors, the notes returned earlier,
and the review notes with numbers, and requires the `feedback` to name a row, a blocker, and a standard error. The source `SOURCES["plan-fix"]`
adds to `in/context/` the files `plan-fix/errors.md`, `previous-proposal.md`, `failed-plan.md` (the plan.md of the rejected
attempt), `returned-feedback.md`, and `review-findings.md`; the plan standard is `docs/agent-plan-standard.md` from the document
snapshots. The planner from the proposal indicates the provider of a card steward whose `card.started` has no provider ("Mam plan" (I have a plan)).

**History.** "Opiekun: odesłał plan z uwagą — <powód>" (Steward: sent the plan back with a note — <reason>) from `caretaker.action`; `plan.returned` itself has no entry
in the history, so nothing is duplicated.

#### A gap in the plan during execution

When a ticket in an approved plan errors, the steward may recognize a missing piece of separate work that the ticket should not
do (PT-155, the K-16 case from 03.11). It uses the existing triggers: `error`, `repeat`, or `waiting`
for a question; the ticket and review steps keep their sheets and `result.json` contracts. There is no separate gap signal.

`return-plan` calls the same `EditCardPlan` / `edit_plan` with `action: return`. The base of `plan.returned` comes
from the approved plan and has the ticket IDs assigned, and the event records `caretaker`, `caretakerStep`, `caretakerFrom`,
and the steward's verbatim note, e.g. "dodaj ticket na migrację formatu, wynika z decyzji A; tickety, które ruszyły,
przepisz bez zmian" (add a ticket for the format migration, it follows from decision A; copy the tickets that have started
unchanged). Then the existing path `plan-amend` → independent review → the diff waits for the user.
The steward never writes `plan.revision-approved`; added tickets get numbers only after the diff is approved.
Tickets that have started cannot be changed or removed: the existing fix check and the `ticket-started` refusal
when approving the diff apply. The steward has no action that bypasses these gates.

The return resolves the ticket step's problem: the `plan.returned` projection marks the step as replaced by the plan fix
(`superseded`), so its error or question does not return to a decision, an automatic retry, or another steward.
The step's result and sheet stay unchanged, and the ticket waits for the plan diff. After the user approves it,
it also waits for the added work to be merged into the card branch (or for the user to discard it). It does not wait for new
tickets that depend on it, even indirectly. With successive fixes, the projection checks the shared graph
of explicit blockers and the waits of all resumes. First it keeps the earlier waits, then adds
new ones in the order of the journal's steps and ticket IDs; it skips a wait that would close a cycle. For example,
when A waits for X that depends on B, adding Y that depends on A will not hold B back from Y: B continues, allowing
in turn the merge of X, the resume of A, and the execution of Y. Then the engine resumes the step through the existing `_resume_step`,
in the same worktree, with an instruction to take the card branch's changes into account and continue the original scope.
The wait is rebuilt from the journal after a restart, and the start of the successor removes it from the `plan_resumes` projection;
the old error does not return to a decision. An explicit user retry still uses the existing path.
A plan fix has its own problem chain; it does not inherit the ticket's error.

If the plan fix fails, the user's choice "Odrzuć poprawkę" (Discard the fix) (`discard-revision`) ends the ticket's wait
for that fix. The engine resumes its original scope according to the preserved plan, without restoring the old
error or question. Blockers from earlier approved diffs still have to be merged or discarded.
This continuation is also rebuilt after a restart from the existing `plan.revision-discarded` event.

A second gap during a fix already in progress gives a failed action `revision-active`, counted toward the limit, also
during the independent review of the new proposal (before the `review-pending` gate). When the user
sends the plan back or retries the step after the steward starts, the steward's result is `stale`: without another `plan.returned`
and without using up the limit, also for a problem with a decision. The history shows a single entry
"Opiekun: odesłał plan z uwagą — <powód>".

`in/context/plan/approved.md` contains a masked snapshot of the approved plan from the journal (ticket IDs and sheet text),
and `plan/spec.md` — the proposal's specification. The existing sources add the sheets `plan/tickets/<ID>.md`,
the step's sheet, its notes, and the tail of `step.log` in `steps/<step>/`. The approved plan snapshot distinguishes it
from the original `plan.md` with working numbers. All files are subject to the shared masking and the 400 kB limit.

### Retry, limit, and outdated actions (PT-150)

**Shared action path** (`FlowEngine._caretaker_act`, also from `_reconcile`): the steward step's result (`outcome`
in `step.finished`) → the staleness check → `escalate` as a single `caretaker.escalated` event or, for
every other action, an "intent → result" pair `caretaker-action` with the steward step's key (`_act`) and finally
`caretaker.action`. A saved pair result does not execute the action a second time; a restart between the intent and the result completes it
through the same path, and `apply` is safe to repeat (a `step.decision` or `step.retry` with this steward
step's `caretakerStep` is not created a second time). An action that fails
on execution returns `{failed, error}` from `apply`: the pair result is saved (`ok: true`), and `caretaker.action` has
`ok: false`, `code`, and `error`.

**Limit** `caretakerActionLimit` (1–10, default 3 — `caretaker.ACTION_LIMIT`; the value from settings through
`caretaker.setup`, "Settings" below, PT-151) — steward steps per problem, successful and failed, counted solely
by `owned()` (`count`).
A retried step that fails again is the same problem (the id from the chain root), so the next steward step belongs to
it. At `count ≥ limit`, `_caretake` does not start another steward step but writes `caretaker.escalated`
with `limit: true`, `failed: true`, `code: caretaker-limit`, `count`, the `step` of the last action, and a summary of the attempts
(`caretaker.limit_summary`: the action, reason, and effect of each action). The state keeps it in `caretaker_limits` (it is not
a steward step result), and `owned()` then gives `handed: true`; the problem's decision has `caretaker.tried[]` with entries
`{step, action, ok, reason, text}` (the reason from the result's `reason` field, the effect as in the history). A user decision (without
the `caretaker` field) closes the problem and resets the counter — the next error of this step starts from zero.

**Staleness** (`caretaker.stale(st, run, events)`, one function for both kinds of problems) — checked before
the steward step starts and before every effect of its result (also before handing over a failed result), but not after a
started `caretaker-action` intent:

- a problem with a decision (`decision` in `caretaker.started`; an entry from before PT-150 — the current problem's decision): there is
  a `step.decision` of the problem's step without the `caretaker` field ("the user has already decided") or `problems()` does not return
  a problem with the same decision id ("the problem's decision is no longer waiting"),
- a problem without a decision: `problems()` no longer returns the problem ("the problem no longer exists"),
- in both cases: after the `watermark` from `caretaker.started` there is an event from outside the steward — in the problem's chain
  (a problem without a step: in its ticket, without a ticket: in the whole card) `step.started`, `step.retry`, or
  `step.decision`, and in the whole card `card.paused`, `card.moved`, `plan.returned`, or `plan.edited` ("after the steward
  started: <event>"). The steward's own events
  do not count: `caretaker.*`, events with `kind: caretaker` (start, waiting for a slot), events with the `caretaker`
  field (records of its actions), and the results and activity of the steward step (they are not on the list).

A stale action writes `caretaker.action` with `ok: false, error: "stale", stale: true` and the reason in `detail`, executes
nothing (no `caretaker-action` pair), does not hand the problem over, and does not count toward the limit (`owned()` skips such
steps). When the result was also rejected by the guard (a step resumed by the user wrote in the same worktree), its reason
is appended to `detail`. Triggers #5, #6, and #13 test the rule on their own problems.

**Slot priority** (US 37): a steward step takes a provider slot like any step (`providerSlots`,
`_wait_reason`, `_admit`, `step.waiting` with the reason "limit kroków dostawcy …" (provider step limit …)). A ready card steward step
(`caretaker.started` with no start and no result) at the same provider reserves a place ahead of any other step of that
card (`_caretaker_reserved`: the reservation counts in `_wait_reason` like a running step), so the card's new ticket waits
and the steward starts in the same pass. The reservation does not apply to other cards, and does not bypass the slot limit or the provider
limit window (D3 — it waits like other steps). A steward step does not overtake the steps of other cards (`_earlier_waiters`):
steps of other cards waiting for a place at the same provider (`step.waiting` with `cause: slots`) earlier than it
(`since` — the start of the wait, kept when the reason changes) occupy places in its `_wait_reason` (the counter
in the reason at most `limit/limit`). The order is a single ordering shared by all cards: (`since`, card
identifier, ticket, step kind) — a tie within a second is decided by the card identifier, so two stewards
waiting since the same moment do not wait for each other. A freed place is therefore taken by the card that waited earlier,
regardless of the loop order (`journal.cards()`). Cards that are finished, paused, moved to
Done, Archive, or Backlog, and rejected tickets are skipped. A steward finished before it starts (e.g. `stale` after a user
decision while it waited for a slot) loses its wait: the result (`caretaker.action`/`caretaker.escalated`) removes its
`step.waiting` in `fold`, so it does not block the steps of other cards. Approving a plan diff
(`plan.revision-approved`) removes the wait of removed tickets in `fold`, also after a service restart.
Ordinary steps whose start
is blocked by a reservation for the steward of their own card according to `_wait_reason` are also skipped. Thanks to this the waiting order
ticket A → steward B → steward A does not create a cycle: steward B starts, then steward A, and then ticket A.
The steward does not take over its ticket's earlier place. When there are still places beside the reservation for
ordinary steps, their earlier wait still counts in the queue of other cards.
Since PT-377 the same queue covers all steps, and the ordering key gets the card priority — see the section "Order
of steps waiting for a place at the provider (PT-377)".

**Retry without a decision in plan steps:** a steward `retry` for a problem without a decision (`step.retry` with `caretaker`
and `instruction`) resumes the step through the shared `_caretaker_resume` function — in ordinary steps (`_advance`), in planning
(`_advance_planning`), and in plan review/fixes (`_plan_step_decision`), with the instruction in the resume sheet, also
for attempt zero (instead of an automatic retry without an instruction).

**User decision while the steward is working:** a running steward step does not make the ticket "busy" in
`resolve_step`, so the ordinary API (StepResolve) accepts the decision; `_advance` resumes the step, and the steward's result is then
stale. A decision written by a steward action also has `caretakerStep`: completion after a restart recognizes its own
`step.decision` (like `step.retry`) before validating `focus`, so another pending decision of the card does not turn
a retry that was already done into a failure.

**Step retry on a tracker error:** `resolve_step` with an explicit `step` (also from the steward) records the decision
of that step even when there are no running steps and a persistent YouTrack comment error is waiting. A global `retry` without
`step` and without a steward problem still resets the tracker errors. The steward port checks the `resolve_step` result:
a tracker reset or a response without confirmation of retrying the indicated step gives a failed action
`retry-not-applied`, without a history entry about a successful retry.

### Context

The ticket sheet (`brief`) begins with "Jesteś opiekunem karty <K-…> w Tide." (You are the steward of card <K-…> in Tide.) and contains: the premise from `BRIEF_SECTIONS`
by trigger (masked by `faults.mask`), the list of context files, the list of allowed actions with fields, the prohibitions, and the result path
("Plik wyniku: …/out/result.json" (Result file: …/out/result.json)). The steward step works in the worktree of the problem's step (read-only) and has its own
directory `<worktree>.caretaker-<step>.step`.

#### Problem description from the decision (PT-143)

The `error` section: the step, the executor, the attempt, "what happened" and "why" from the error decision, the reason the step ended, and whose
fault it was according to the step journal. The step journal and the remaining files are in the context directory (below).

#### The `in/context/` directory (PT-153)

When a steward step starts (in the `step-start` "intent → result" pair, after `_admit` accepts it, so not on every
turn of the loop in the provider queue), Tide builds `<step>/in/context/` with the module
`daemon/tide_daemon/cardflow/caretakerctx.py` (`build(problem, st, events, …)`; `collect` — the same in memory, for
the conversation with the steward in #7). The sheet gets a "## Kontekst" (Context) section with an absolute path and a description of each file
("(przycięty do limitu)" (trimmed to the limit) for trimmed ones). Files common to every trigger:

- `history.md` — the card's history in human-readable form (`history.build`, from the oldest entry, with step attempts),
- `journal.jsonl` — the last 200 events of the card's journal (JSON Lines, from the oldest),
- `plan/plan.md`, `plan/source.md`, `plan/tickets/<id>.md` from the card's directory on the board (without a plan — `plan/README.md`
  with a note) and `steps/<problem step>/prompt.txt` — the problem step's sheet,
- for each step of the problem (the problem's `parentStep` chain and all the steps of its ticket): `steps/<step>/note.md`,
  `review.md`, `handoff.md`, `result.json`, and additional `*.md` notes named in `notes`/`kept` of the result or of the finish
  event (from the step archive in the card's directory), `plan-review.json` (from `<step>/out`), and `step-log.md` —
  the last 300 events of `out/step.log` in human-readable form (`faults.excerpt(…, limit=300)`, like the PT-125 excerpt),
- `attempts.md` — the problem steps' attempts (executor, result, reason, whose fault, decision) and the steward's previous
  actions for the same problem id (result, summary, recommendation, error, `note.md`, `result.json`, and additional
  notes from `notes`/`kept` of the steward step) — without the current step. Additional names must point to a Markdown file
  without path components; reading does not follow links,
- `docs/agent-plan-standard.md`, `docs/sandbox-and-permissions.md`, and `docs/AGENTS-przeplyw-karty.md` (the "Przepływ karty" (Card flow)
  section of `AGENTS.md`) — snapshots of the running Tide service's documents: `tide-docs/` next to the package
  (`daemon/install.sh` copies these three files there) or the Tide repository when run from source
  (`caretakerctx.tide_docs_dir()`), so cards of other projects get them too; only without them — from the steward step's
  worktree, and without the file there too — a note "(pominięto: …)" (skipped: …). The documents directory is a separate root only for these
  fixed names (other sources do not read from it through `ctx.read_text`).

**Source registry.** `caretakerctx.SOURCES` — trigger → list of `Source(name, build)`; `"*"` — the sources of every
problem. A problem gets `"*"` and the sources of all its triggers (`triggers[]`). `build(ctx)` returns
`File(path, title, text=…)` or `File(path, title, items=[…], head=…, trim=n)` (events from the oldest,
trimmed) and reads files only through `ctx.read_text` / `ctx.read_tail`. Later tickets add their own entries
(e.g. plan format check errors) without editing the shared sources; an exception in a source gives `errors/<source>.md`
instead of aborting the start.

**Boundaries.** Masking solely by `faults.mask`: each file as a whole, each event separately before the size is counted.
File titles are masked too. A path containing a secret (checked also before
sanitizing and shortening the name) gets a unique alias `masked-N.*`, with the remaining names reserved. The alias is the name
of the saved file, so the reference in the sheet still works. In `journal.jsonl` and `*.json` files every decoded key
and every value are masked separately, before re-
serialization (this also applies to a result embedded in `attempts.md`). Key collisions after masking get a unique
suffix ` [N]`, skipping the names of other fields, so as not to overwrite data. A field that `faults.mask`
considers secret on the sample `name=x` (`token`, `password`, `apiKey`…) loses its entire value — including a string with spaces
and a number in arbitrarily nested objects and lists under that field; the remaining strings go through
`faults.mask`, and a number changed by `faults.mask` (e.g. equal to the value
of a secret environment variable) becomes `•••`. An invalid or incomplete JSON file is replaced by the note
"(pominięto: niepoprawny lub niepełny JSON)" (skipped: invalid or incomplete JSON) — masking raw escape sequences could reveal part of a secret.
Likewise a damaged event in `out/step.log` (a line starting with `{`, also at EOF after a crash) is replaced by
the note without its content. A valid event without a trailing newline character still goes into the excerpt after decoding;
the event's decoded values go through masking before joining and whitespace normalization,
so that a multi-line environment secret still matches its value. Before splitting stderr and events,
`faults.mask(…, multiline_only=True)` masks matches that cross line boundaries: valid JSON inside
a secret does not cut it into plain fragments. Single-line serializations stay intact until decoding.
Raw stderr is then masked in whole blocks before the split into lines, whitespace removal,
and shortening; reading keeps the original line-ending characters (CRLF too). The `AGENTS.md` document goes through
`faults.mask` with the same environment before the "Przepływ karty" section is cut out and CRLF is normalized, both
from the copy of the Tide documents and from the worktree. The history and the steward's earlier diagnoses get masked copies of
events before formatting by `caretaker.history_text`, without changing the card's source state.
The tail of `out/step.log` read from the middle of the file starts at
the first full line — a secret cut by the read boundary does not lose its variable name or token prefix before
masking. Likewise a read that ends before the end of the file (the start of a file longer than `FILE_READ_MAX` or a file
being appended to during the read) drops the last, incomplete line and appends "dalsza część pliku pominięta" (rest of the file skipped) — the truncated
remainder of a token does not reach `faults.mask` (PT-153 review, round 2). After dropping the truncated line, both boundaries
also drop the remaining lines of a cut multi-line environment value whose secrecy is decided by
`faults.mask`. The check keeps the original whitespace and bytes (lossless `surrogateescape`), before
formatting; the moved boundary is checked again in case of overlapping values (PT-153
review, round 10). `faults.trim_secret_boundary` uses the same patterns as `faults.mask` to drop
also fragments of quoted assignments without a value in the environment: at the start of the tail, the first quote mark of each
kind may close a lost value, and at a truncated end a temporary closing reveals the beginning of the assignment.
The dropped fragment expands to whole lines and complete secrets cut by the new boundary; it may also include
plain text that cannot be told apart from a continuation of a secret. No additional reads beyond the limit. If
an appended journal has both boundaries cut (the start is missing and EOF was not reached), the whole read is replaced by a note
about the omission — the absence of both quote marks then does not decide whether the fragment is the middle of a secret (round 11).
Reading only from roots: the card's directory on the board (`flow/<K-…>`), the steward step's worktree, and the worktrees
and step directories of the problem from the journal. A root that is itself a link (e.g. a whole `<worktree>.step` replaced with
a link to `$HOME`) is dropped (`lstat`); on reading the root is opened with `O_NOFOLLOW` and compared
with the identity (`st_dev`, `st_ino`) from the time of the check, and each path component — relative to the previous one (`dir_fd`)
with `O_NOFOLLOW`; the file must be a regular file. A link at any level gives the note "(pominięto: dowiązanie
symboliczne / plik spoza katalogu karty i worktree)" (skipped: symbolic link / file outside the card directory and worktree), not the content. The whole has at most 400 kB (`MAX_BYTES = 400 000`
bytes): first the oldest `journal.jsonl` events are trimmed, then the step journals, then the history,
with a first line "(przycięto N najstarszych zdarzeń — limit kontekstu opiekuna 400 kB)" (trimmed the N oldest events — steward context limit 400 kB); if that is not enough,
the tails of the largest files with the note "… (koniec pliku przycięty …)" (end of file trimmed), and a file that would be left with only the note
is dropped entirely — replaced by a single `przycieto.md` ("(pominięto N plików kontekstu w całości …)" (skipped N context files entirely …)), for which the
budget is reserved up front, so the limit holds for any number of files. The context directory is built
anew at every start.

### Changing the executor

The steward action `change-executor` (PT-145), for an error, a repeated reason, or no progress, takes
`step`, `executor` (a name from the catalog), `reason`, and an optional `instruction` of up to 4000 characters. It uses the existing
`resolve_step` and its `_resume_executor`, without a second implementation of the assignment rules. A local step or a
"Tylko lokalnie" card does not move to the cloud. The steward must keep the author and reviewer models different, and in
mixed mode also different providers. The existing exception for a deliberate user choice remains available
only to the user. An executor from outside the catalog or a rule violation gives a failed action with a reason,
counted toward the limit; the step is not changed.

The resume keeps the worktree and the changes but starts a new session. The optional instruction goes into the new
executor's sheet. History: "Opiekun: zmienił wykonawcę <krok> na <nazwa> — <powód>" (Steward: changed the executor of <step> to <name> — <reason>). The action uses the shared `caretaker-action`
`intent`/`result` pair; its own `step.decision.caretakerStep` entry protects against a second decision on a restart between
the effect and the pair's result.

### Answering a question

The `answer` action, for a waiting question, takes `step`, `answer`, `reason`, and `category`: `technical` or
`environment`. It answers a question of a ticket, review, fix, or conflict step through the existing `resolve_step`,
and the resumed sheet marks the answer as coming from the steward. History: "Opiekun: odpowiedział na pytanie
<krok> — <powód>" (Steward: answered the question of <step> — <reason>). The answer is 1 to 4000 characters; the action is subject to the shared staleness, the limit, and the
"intent → result" pair.

Tide rejects answers for `planning` and all `plan-*` steps, regardless of the declared category. The question
stays with the user with a recommendation, and the failed action counts toward the limit. The sheet tells the steward to escalate questions
about scope, requirements, business choices, and questions with product variant options. The category does not prove the content
is technical — recognizing the content remains the steward's task; the ban on answers during planning is enforced by Tide.

### Decision waiting

The `waiting` trigger (PT-145) checks the engine clock and the recorded start of the decision's wait. After a time **longer**
than `caretakerDecisionMinutes` it creates the problem `waiting:<decision id>`. Exactly N minutes does not yet start the
steward. For a question, `answer`, `escalate`, and `return-plan` for a plan gap (PT-155) are available;
for consent to a command, plan approval (also of a plan diff), and review after the last round — only
`escalate`. The list of actions is enforced by Tide,
also when executing actions. The steward does not create consent, approve a plan, or settle a review.

The decision stays visible the whole time the steward works; `escalate` only appends `caretaker.recommendation`
and a summary. After escalation the same problem does not start the steward again, also after a restart. The decision's
identity prevents carrying the recommendation over to the next decision of the same step. Changing N does not invalidate the work
of a steward that has already started. If the user settles the decision while it works, the steward's result is
`stale`, changes nothing, and does not count toward the limit.

### Settings

#### Switch, executor, action limit, and waiting minutes (PT-151)

Four keys in `$XDG_CONFIG_HOME/tide/cardflow.json`, read and written by the existing `GetCardFlowSettings`
and `SetCardFlowSettings` (signatures unchanged; a write changes only the given keys, the rest of the file stays):

| Key | Value | Default |
| --- | --- | --- |
| `caretakerEnabled` | `true`/`false` | `true` |
| `caretakerExecutor` | an executor name from the catalog or `""` ("default") | `""` |
| `caretakerActionLimit` | an integer 1–10 — steward steps per problem | 3 |
| `caretakerDecisionMinutes` | an integer 1–1440 — minutes a decision waits before the steward takes it up (#3) | 10 |

`GetCardFlowSettings` always returns all four (a value of the wrong type or out of range in the file — the default,
`caretaker.settings_of`). Validation is in the service (`caretaker.setting_problem`), before the whole request is saved:
a wrong type or range (e.g. `caretakerActionLimit` 0, 11, or `"3"`, `caretakerDecisionMinutes` 0 or 1441, `true`
instead of a number) → `bad-request`; an executor from outside the catalog → `bad-executor`; an unknown key → `bad-settings` (as
before). `limitTransfer`, `limitThresholdPercent`, and `providerSlots` work unchanged.

`heavyLimit` (PT-373) — an integer 1–6, how many heavy commands outside the sandbox run at once on the machine
(the section "Queue of commands outside the sandbox and the heavy command queue"); default 2. `GetCardFlowSettings` always returns
it (a bad value in the file — 2), `SetCardFlowSettings` rejects an out-of-range value, a fraction, a string, and `true`
(`bad-settings`), and a write changes only this key and keeps the unknown keys of the file. The field in the window: "Ciężkie polecenia
naraz" (Heavy commands at once) on the "Zarządzaj" (Manage) page (PT-375, the section "PRZEPŁYW KARTY · RÓWNOLEGŁOŚĆ" (CARD FLOW · PARALLELISM)).

`ticketsPerCard` (PT-375) — an integer 1–6, how many tickets of one card are in progress at once (the section "Limit of
tickets in progress per card"); default 2. The same rules as `heavyLimit`: `GetCardFlowSettings` always returns it (a bad
value in the file — 2), `SetCardFlowSettings` rejects an out-of-range value, a fraction, a string, and `true` with the code
`bad-settings`, and a write keeps the other keys of the file. The field in the window: "Tickety naraz na kartę" (Tickets at once per card) next to "Ciężkie
polecenia naraz" (a single write `SetCardFlowSettings {heavyLimit, ticketsPerCard}`).

`heavyCommands` (PT-382) — a list of heavy command pattern strings (syntax and default list: the section "Queue of
commands outside the sandbox and the heavy command queue"). `GetCardFlowSettings` always returns it: a missing key (or an
invalid value in the file) — the default list; a saved empty list `[]` means no patterns (the contract
commands and the post-merge attempts stay heavy). `SetCardFlowSettings` rejects a value that is not a list of strings,
and an empty pattern (`""`, only spaces, `*`) with the code `bad-settings` before saving; a write keeps unknown keys.

`quietCommands` (PT-376) — a list of pattern strings for commands that require quiet (syntax like `heavyCommands`; the section
"Commands that require quiet"). `GetCardFlowSettings` always returns it: a missing key or an invalid value in the file —
an empty list. `SetCardFlowSettings` rejects a value that is not a list of strings, and an empty pattern with the code
`bad-settings` before saving; a write keeps unknown keys.

**Executor choice — a single place** (`caretaker.executor_name(catalog, st, preferred)`, called only by
`caretaker.setup` and `FlowEngine._caretaker_setup`; used by the steward step start, the slot reservation,
and also the conversation with the steward (#7) and the executor change by the steward (#3)):

- a "Tylko lokalnie" card — a local executor from the catalog: `caretakerExecutor` when it is itself local, otherwise
  the first local one in catalog order; a cloud `caretakerExecutor` is skipped, so the card's context does not
  leave the computer. Without a local executor the steward does not start, and the decision has the note "opiekun niedostępny dla
  pracy tylko lokalnej" (steward unavailable for local-only work),
- any other card — `caretakerExecutor` if it is in the catalog (an executor removed from the catalog after saving — the default),
  otherwise the strongest class of the card's preferred provider (in mixed mode — the provider of the card's executor; for
  Anthropic Opus, for OpenAI strong Codex).

`caretaker.setup(st, settings, catalog)` gives `{enabled, executor, reason, note, limit, decisionMinutes}`: `reason`
— why the steward will not start (disabled or no local executor; `caretaker.unavailable` returns it), `note` —
a note for the decision (only the missing local executor). The engine adds the result to the state before the views
(`FlowState.caretaker_setup`, not from the journal), so `flow.caretaker.enabled` and the decision note are computed from the same
settings as the start. A steward that is disabled or has no local executor while its step was already waiting to start (e.g. for a
slot) does not start: `caretaker.action` with `stale: true` and the reason in `detail` (no escalation, does not count toward the limit),
and the decision returns as it does today. A steward step that is already working ends normally.

**Window.** The "Zarządzaj" page (`ManagePage.qml`), the section "PRZEPŁYW KARTY · OPIEKUN" (CARD FLOW · STEWARD): the "Opiekun karty" (Card steward) switch,
the executor choice ("Domyślny — najmocniejszy u dostawcy karty" (Default — the strongest at the card's provider) and the whole catalog, local ones too;
`board.js caretakerExecutorOptions`), the action limit (1–10), and the waiting minutes (1–1440); "Zapisz" (Save) sends a single
`SetCardFlowSettings` with the four keys, and a service error shows under the section.

The sandbox — "Sandbox and guard" below. A steward waiting for a provider slot (`step.waiting` of kind
`caretaker`) gives no limit notification — the problem still belongs to the steward, only escalation notifies.

### Limit of tickets in progress per card (PT-375)

A card does not start more tickets at once than the limit allows (`cardflow/ticketlimit.py`). The master plan allows 2
tickets per card, and K-36 started 3: until now the only limit was the number of steps at the provider.

- **A ticket in progress** is a ticket of an approved plan with at least one step, not merged (`merge` without `ok`, other than
  `imported_done`) and not rejected. A ticket waiting for a user decision (a question, an error), for review,
  fixes, and merge also counts.
- **Effective limit:** `max-tickets` in the card file (1–6), and without it the flow setting `ticketsPerCard` (default
  2). A bad value in the card file (`max-tickets: 9`, a string) gives the default limit and an entry in the Board's `errors`
  ("niepoprawny limit ticketów" (invalid ticket limit)), without a card read error. `UpdateCard {maxTickets}` accepts a number 1–6 (also a string
  of digits), and an empty value (`""`, `null`) removes the key from the file; any other (0, 7, a fraction, `true`, a string) → `{ok: false}` without
  changes. `CreateCard` accepts `maxTickets` the same way. The card snapshot carries `maxTickets` (a number or `null`), and
  `derived.flow.ticketLimit` — `{limit, running, override}` (the effective limit, the number of tickets in progress, the card's override).
- **Enforcement.** `_plan_next` counts tickets in progress before starting each ticket (after each start from the refreshed
  state). At `in progress >= limit` the ticket does not start (no branch or worktree is created), and the engine writes
  `step.waiting` with `kind: ticket`, `cause: card-limit`, and the reason "czeka na miejsce w karcie (<n> z <limit>)" (waiting for a place in the card (<n> of <limit>)) — **once per
  reason change** (subsequent ticks with the same reason add nothing) — and moves on to the next ticket.
  A freed place (a merge or the rejection of another ticket) starts the waiting ticket on the nearest tick;
  `step.started` removes the wait entry.
- **Scope.** The limit applies only to starting a new ticket in `_plan_next`. The next steps of a ticket in progress (review,
  fixes, merge, conflict, steward) do not pass through this gate, so a ticket in progress does not block its own
  steps. Lowering the limit stops nothing: a new ticket starts only when the number in progress drops below
  the limit. An "Od razu" (Immediate) card (one ticket, no `_plan_next`) works unchanged even with a limit of 1. The limit does not change the
  start order between cards, the heavy command queue, or the post-merge test gate.
- **Status.** `derived.tickets[]` for a ticket waiting for a place has `status: card-limit` and
  `statusLabel: "czeka na miejsce w karcie (<n> z <limit>)"` with the current number and limit (a limit change refreshes the label without a
  new event; `fullcard.card_limit`). A card tile without running steps shows the same reason from `step.waiting`.
- **Window.** The "Zarządzaj" page: "Ciężkie polecenia naraz" and "Tickety naraz na kartę" (1–6, a single write of
  `SetCardFlowSettings`, a service error under the section). Full card settings: "Tickety naraz" (Tickets at once) (an empty field — the
  default setting; a write `UpdateCard {maxTickets}`).

### Order of steps waiting for a place at the provider (PT-377)

A freed place at the provider (`providerSlots`) is taken by a waiting step of a card with a higher priority, and with equal
priority by the step that has waited longer — regardless of which card the tick checks first (`journal.cards()`).
This generalizes the steward queue from PT-150 ("Slot priority") to all steps. The ordering is computed solely by
`cardflow/stepqueue.py`; the engine only calls it.

- **Card priority** — the `priority` key in the card file (`high`, `normal`, `low`; missing or a bad value — `normal`).
  A card on the engine's track gets precedence when the owner gives it high priority.
- **Ordering key** (the smaller starts first, shared by all cards and total):
  `(boost, card priority, since, card, ticket, kind)`. `since` is the start of the wait (`step.waiting.since`,
  kept when the reason changes); a step that is not yet waiting comes after all waiting ones with the same
  priority. A tie within a second is decided by a fixed order (card, ticket, kind), so two steps never
  wait for each other. The boost is always false today (a manual boost will set it, K-39 #11).
- **Step start.** `_admit` → `_earlier_waiters` builds this step's entry and counts the entries of other cards in the queue ahead of it
  at the same provider (`stepqueue.order` + `stepqueue.ahead`). They count like running steps (in the reason at most
  `limit/limit`), so a step starts only when there are enough places for all the entries ahead of it. Steps of the same
  card do not wait for each other in this queue (their order is set further by the card's pass and the steward reservation
  `_caretaker_reserved`, unchanged). A local model step has its own queue (`model_queue`) and is not subject to this one.
- **Current cause, not the saved `cause`.** The queue holds `step.waiting` entries with a saved `cause` of `slots` or
  `quota`, but the place is decided by the state at the moment of the decision: the card state from the tick's read phase, the weekly limit and the provider
  windows (`_low_limit`), a pause, a base decision, a place in the card. An entry with `cause: slots` whose provider has now
  exceeded its limit waits for the limit and does not block others; an entry with `cause: quota` whose limit has renewed returns to
  the queue for a place. A step that cannot start for another reason does not take a place: a card waiting for a base decision,
  a new ticket (without a step) waiting for a place in the card (`max-tickets`/`ticketsPerCard`), for post-merge tests, or
  a running card operation, and an ordinary step blocked by its own steward's reservation (PT-150, round 3 — no waiting
  cycle). Finished, paused, and moved to Done/Archive/Backlog cards, rejected and merged tickets, and
  running steps drop out of the queue.
- **Cost.** The ordering is computed from the card states of a single read phase of the tick (`journal.state`, with no additional
  journal reads per step). Card priorities and limits come from the Board read context of the current tick
  (`BoardRead`, K-98 #3); outside a tick (a start from D-Bus) — from one fresh read, and only when a step of another
  card is waiting at the provider. The weekly limit and the steward reservation are computed once per provider/card in a single decision.
- **One ordering function.** `stepqueue.order` returns the waiting steps in start order with the current cause and reason;
  the `queue.steps` part of the snapshot will use it too (K-39 #10). The heavy command queue (`heavyqueue`) and the
  `providerSlots` limit do not change.
- **Limitation.** An entry belongs to the queue of the provider saved in `step.waiting`. Handing work to another provider after the
  limit (`limitTransfer`) changes the provider only at the next attempt to start this step.

### Sandbox and guard (PT-158)

The safety boundary of the steward step, required before actions that change the card's state (#8): the sandbox allows
writing only to its own `<step>/out`, and the guard, independently of the sandbox, checks that the step changed nothing outside it.
The sandboxes and command lists of ordinary steps are unchanged.

**Sandbox** (`agentcmd.Step.write_out_only`, set by the engine for the `caretaker` kind):

- The program's working directory is `<step>/out` (`Background.workdir`; `Background.cwd` — the worktree — stays for
  stopping and the session journal). The Claude CLI sandbox gives write access to the working directory, so only `out` is
  writable (`allowWrite: [out]`), and its empty stub files (`.bashrc`…) land in `out`, not in the worktree.
- Claude: a fixed read-only list `agentcmd.read_only_tools` instead of the step's list — `rg`, `grep`, `cat`, `ls`,
  `find` (`READ_ONLY_COMMANDS`, also with patterns naming the worktree), `git -C <worktree> log|show|diff
  --no-ext-diff --no-textconv --ignore-submodules=all …` (mandatory options: they disable `diff.external`,
  `diff.<d>.command`, `textconv`, and submodule inspection) and `git -C <worktree> status --ignore-submodules=all`
  (`READ_ONLY_GIT`),
  `Read(//<worktree>/**)` for Read/Grep/Glob and reading its own `in`/`out`
  (`board.commands.step_reads`). The list is in Tide, independent of the project's settings: the engine does not give the steward step
  the project's list (`claude_tools`), and `agentcmd` skips it anyway. `--disallowedTools` (a ban wins over
  consent): `READ_ONLY_BANS` — `find` with `-delete`, `-exec`/`-execdir`, `-ok`/`-okdir`, `-fprint*`, `-fls`; `rg`
  with `--pre`, `--hostname-bin`, `--search-zip`/`-z` (also combined, e.g. `-az`, `-iz`); `git` with `--output`,
  `--ext-diff`, `--textconv`, `--show-signature`; every `>` redirection — and a ban on editing the worktree and the repository.
  `autoAllowBashIfSandboxed: false`: a command outside the list will not run.
  The `rg *-*z*` ban is conservative: the wildcard includes spaces, so it also rejects safe invocations with a `z`
  in a later argument after `-` (e.g. `rg -n pattern file.txt`); you can then use `rg` without options or `grep`.
- Git helper programs (review M3): the steward step's environment (`agentcmd.read_only_git_config`, appended to
  `GIT_CONFIG_COUNT`, wins over the repository configuration) has `core.fsmonitor=false`, `core.pager=cat`,
  `log.showSignature=false`, `gpg.program`/`gpg.ssh.program`/`gpg.x509.program` = `/bin/false`, and empty programs
  of all filters from the worktree configuration (`filter.<f>.clean|smudge|process`, `required=false`; the list is read by the
  service with `git --git-dir <gitDir> config` before the start, a read error — the step does not start), plus
  `GIT_CONFIG_NOSYSTEM=1`, `GIT_PAGER=cat`. A test on a real repository with `diff.external`, `textconv`,
  `diff.<d>.command`, a filter, `core.fsmonitor`, and `gpg.program` checks that no allowed git command
  runs them (while an ordinary `git diff` does).
- Submodules (review round 4/M1): their configurations may name different filters than the worktree configuration.
  Every allowed git subcommand has a mandatory `--ignore-submodules=all`, which also wins over the setting
  `submodule.<name>.ignore=none`; the environment additionally forces `status.submoduleSummary=false`.
  The bans `git *--ignore-submodules=all *--i*`, `git *--no-i*`, `git *--sub*`, `git *--no-sub*` rule out a later
  override of the option, its abbreviations (`--ign`, `--i`), negation, and requests for a submodule view. The rules are conservative:
  they also reject e.g. `--ignore-space-change`. A test with a real submodule and a filter configured only
  in it confirms that the helper runs in the M1 reproduction, that the rules reject it, and that there is no helper with
  the allowed `status`, `diff`, `log`, `show` invocations.
- Codex has no allowed-command list, so a steward at Codex gets an empty subset of it: the command and code
  tools are disabled (`-c features.shell_tool=false`, `unified_exec`, `code_mode`, `js_repl` —
  `agentcmd.CODEX_NO_COMMANDS`), the working directory and the only writable directory are `out` (the result file through
  `apply_patch`), `/tmp` and `$TMPDIR` are explicitly excluded (`sandbox_workspace_write.exclude_slash_tmp=true`,
  `exclude_tmpdir_env_var=true`, because `writable_roots` only appends directories), and `--skip-git-repo-check` (also
  on resume) — because `out` lies outside the repository. The sheet then says the step has no commands.
- The sheet (`caretaker.brief(…, commands=)`) lists the list, the bans, and the guard.
- Stopping a steward step (`_stop`) does not make a WIP commit (`stepstop.stop_step(commit_wip=False)`): the step's worktree
  and branch stay as they are, and any write by the step is detected by the guard.

**Guard** (`cardflow/guard.py`, in the engine `_guard_baseline`, `_guard_check`, `_guard_freeze`, `_guard_rebase`,
`_guard_thaw`, `_guard_verdict`):

- State under guard: the step's worktree tree and — for a ticket problem, when it is a different directory — the card's worktree tree
  (all files, directories, and links — tracked, untracked, and ignored — without the `.git` directory; the record:
  path, type and permissions, size, `mtime`, `ctime`, inode, link target; a sha256 digest split by top-level
  entries), the content of `<gitDir>/HEAD` of each of these worktrees, and the sha of the step's worktree branch and the card
  branch. `ctime` is changed by every write and cannot be rolled back without root, so it also detects
  a write of the same content and a replacement with a restored `mtime` — more strongly than `git status` or a content sum, without
  reading the files. Git is not run in the worktree (hooks, `core.fsmonitor`): Python reads the tree without
  following links, `HEAD` — a file in the git directory determined by the service, branches — `FlowGit.sha`
  in the repository.
- The only exception (PT-317): `.claude/.cc-writes` in the worktree root — a file or a directory with contents
  written by Claude Code itself, not the model. The guard computes the tree as if this path did not exist: it skips it,
  and the `.claude` directory itself has no record (its size, `mtime`, and `ctime` are changed by the creation of `.cc-writes`) — the
  `.claude` entry in the tree is created only by the other entries in it. Thanks to this, the creation of `.claude` together with the first
  `.cc-writes` (a fresh worktree, e.g. a Codex-authored ticket with a Claude steward) is not a change; only
  an empty `.claude` directory and a change of its permissions are invisible. Every other entry in `.claude` has a full record, so a change to
  `settings.local.json`, or a new,
  deleted, or moved file next to `.cc-writes`, still gives `caretaker-wrote`. A link named
  `.cc-writes` is not an exception (a record with a target), and a write through it to a worktree file shows in the target's record;
  `.cc-writes` in a subdirectory is not an exception either. A new `.claude` with anything besides `.cc-writes` is a change.
- Baseline state: `caretaker.guard` with `cause: start` right before the program starts (after the step directories are prepared,
  before `step.started`) — `{step, repo, worktrees: [{worktree, gitDir, tree, head}], branches, refs, cause,
  wrote}`.
- Tide's actions during a steward step (every `_act` of this card with work done: a merge, a plan commit,
  branches, the start of another step with dependency restoration, cleanup…) are not a write by the step: before the action
  the guard first freezes the step's unit (`stepstop.freeze_unit`, `systemctl --user freeze
  <unit>.scope` — the cgroup freezer stops the process with its descendants), only then compares the state and records the changes
  as `caretaker.guard` with `cause: check` and `wrote: [reason]` (Tide's write will not cover them; a write by the step
  right before a successful freeze also lands here — review M1 of round 2), performs the action, takes the baseline state
  anew (`cause: <action>`), and only then thaws the step (also after a check or action error). A change
  in the action window is therefore a Tide change (review M1). When the freeze fails and the step's process is alive or its state cannot be
  determined, changes from the window go into `wrote` ("zmiana w czasie działania Tide `<działanie>`, gdy kroku opiekuna
  nie dało się zamrozić — …" (a change during Tide action `<action>` when the steward step could not be frozen — …)) — the guard rejects instead of letting it through; a process that has already ended writes nothing.
  `FlowState.caretaker_guards` — the last baseline state, `caretaker_wrote` — the collected reasons.
- End of the step (`_finish_caretaker`): the guard runs first, before `caretaker.parse_result` and regardless of
  the stop or the exit code. A change from the baseline state, an earlier recorded `wrote`, or a missing baseline state →
  the result is rejected with the code `caretaker-wrote` (`step.finished.outcome.code`, without reading the result file), the reason e.g.
  "krok opiekuna pisał poza swoim katalogiem wyjściowym: zmienione pliki w worktree <ścieżka>: opiekun-byl-tu.txt;
  przesunięta gałąź ticket/pt-1" (the steward step wrote outside its output directory: changed files in worktree <path>: opiekun-byl-tu.txt; moved branch ticket/pt-1). Further as with any failed action: `caretaker.escalated` with `failed: true`,
  the action is not executed, the action counts in `owned()`, and the card history has the entry "Opiekun: wynik odrzucony,
  akcja nie wykonana — <powód>" (Steward: result rejected, action not executed — <reason>).
- Exception: a Tide command in a worktree under guard (K-39, PT-372). During a steward step a run outside the sandbox may be going in the worktree,
  on the consent of a ticket step (`make test-changed`, a measurement script) or a post-merge test attempt
  (`merge-check-*` in the card branch's worktree, K-88). Its `build/`, `__pycache__`, and test results are not
  a steward write (previously a false `caretaker-wrote`, e.g. K-98-care-10 and K-98-care-13 from 11.11). The logic is
  pure in `cardflow/guard` (`runs`, `active`, `skipped`, `excuse`); the engine supplies only the journal events
  (`_guard_outside`):
  - a run is `outside.started` → `outside.finished` of the same step; the worktree from the `worktree` field of the start,
    and for a post-merge test attempt from its pseudo-step `merge.check-step`. A start without `worktree` excludes nothing;
  - it counts when the start is in the journal before the check (the journal is read before the snapshot) and the end is after the
    baseline state (the `seq` of the last `caretaker.guard`) or missing — also a command that started and finished
    during the steward step;
  - worktrees are compared by `os.path.normpath(os.path.abspath(…))`, without following links (a trailing `/`
    and `./` match, a link to the worktree does not);
  - only the tree changes (`tree`) of that worktree are excluded: instead of `wrote` Tide records `caretaker.guard`
    with `cause: outside`, `wrote: []`, and `skipped: [{run, worktree, entries}]` (the run name, the worktree,
    the changed top-level entries) and takes that worktree's tree anew. The worktree's `HEAD`, branch refs,
    the disappearance of a worktree, and a write in another worktree under guard or in a worktree without a command are detected as
    before;
  - the same rule works in `_guard_check`, in `_guard_rebase` (an unfrozen step; for a frozen one the whole action
    window belongs to Tide anyway, `_guard_freeze` unchanged), and in `_guard_verdict`, so a change skipped
    in one place does not come back as `wrote` in another. Old journals without `skipped` read unchanged;
  - limits: the steward has no `outside` contract, so it cannot open the exception itself — commands in a worktree are run
    only by Tide (the consent of a ticket step, the post-merge test gate). A steward write in the tree of a worktree
    with a running command is indistinguishable from the command's write in this window and passes. A run without
    `outside.finished` (e.g. lost after a service crash) excludes that worktree's tree until the end of the steward step.
    Commands run manually by the coordinator (Tide does not know them) still give `caretaker-wrote`.
- Limitation: a card branch moved during a steward step by another step (e.g. resolving a conflict
  of another ticket in the card's worktree) is not a Tide action, so it rejects the steward's result (a safe mistake —
  the problem returns to the user with the reason). The same goes for a Tide action during a step that could not be frozen
  (e.g. systemd without `freeze`).

## Docker

Viewing and managing containers instead of Docker Desktop — the data for the "Docker" view in the Tide window (`apps/docker`) and the `org.tide.docker` tile in the dock (`widgets/docker`). The service talks to the Docker Engine API through a local socket (the standard library only, no `docker` CLI and no `docker compose`). The socket comes from `$DOCKER_HOST` (`unix:///path`), by default `/var/run/docker.sock`; `tcp://`, `ssh://`, and others → the `unsupported` state, with no network connection.

| Item | Value |
| --- | --- |
| Object | `/Docker` |
| Interface | `org.tide.Daemon.Docker` |

| Member | Signature | Description |
| --- | --- | --- |
| `Snapshot()` | `→ s` | JSON snapshot of the containers (described below); immediately, from memory |
| `Stats()` | `→ s` | CPU and memory of running containers; computed only on request (the window calls it when visible), remembered for 5 s; the response comes after the read (at most 8 s in total) |
| `Disk()` | `→ s` | cleanup categories with reclaimable space; remembered for 30 s, cleared after an action and after an event that changes what can be reclaimed (container `create`/`destroy`/`start`/`die`, volume `create`/`destroy`/`prune`); the response comes after the read (`/system/df` at most 20 s) |
| `DiskNow()` | `→ s` | the same, but always a fresh read: no memory and no read that started before the call. The cleanup confirmation calls it — `volumes/prune all=true` removes all volumes without a container, so the list must be from this moment (e.g. after `docker compose down` elsewhere) |
| `Start(s id)`, `Stop(s id)`, `Restart(s id)` | `→ s` | `{ok, error, id, name, note}`; `stop` and `restart` with `t=10` (then Docker kills); `note`: "już działał" (was already running), "już był zatrzymany" (was already stopped). The request to Docker takes at most 20 s |
| `Remove(s id)` | `→ s` | removes only a stopped container, without `force` and without volumes; a state check (≤ 5 s) and the removal (≤ 15 s) |
| `GroupAction(s project, s action)` | `→ s` | **a background job**: `start`/`stop`/`restart` of a whole compose project through the API. Immediately `{ok: true, error: "", job}` or a refusal `{ok: false, error, job: ""}` (unknown action, empty name); the result `{ok, error, job, project, done, failed, note}` comes in the `ActionFinished` signal |
| `Prune(s categories)` | `→ s` | **a background job** for a JSON list of category ids, e.g. `["cache", "images"]`. Immediately `{ok: true, error: "", job}`; anything other than a list of known ids (an object, a string, `null`, an unknown id, an empty list) → `{ok: false, error, job: ""}` and no call at all. The result `{ok, error, job, reclaimedBytes, details}` comes in the `ActionFinished` signal |
| `Logs(s id, i lines)` | `→ s` | the last log lines `{ok, error, id, name, lines: [{text, stderr}]}`; `lines ≤ 0` → 200, at most 5000; at most 15 s |
| `OpenLogs(s id)` | `→ b` | a Console tab with `docker -H <service socket> logs -f --tail 200 <full ID>` |
| `OpenShell(s id)` | `→ b` | a Console tab with `docker -H <service socket> exec -it <full ID> sh -c 'command -v bash >/dev/null && exec bash \|\| exec sh'`; `false` for a stopped one |
| `Badge()` | `→ s` | the Docker view badge in the Tide window bar: `{text, tone}` — containers that crashed, as `"● N"` with the `negative` tone (pink), otherwise the number of running ones with the `muted` tone; zero, no Docker, and the state before the first read — empty text (badge hidden). Testcontainers do not count. The view's backend computes the same rules from the snapshot (`apps/docker/backend.py`) |
| `ShowWindow(s id)` | `→ b` | the Tide window on the Docker view (`tide.py --view docker [--select <element>]`); `id` — a container from the last snapshot (full ID, an ID prefix of 12 characters, a name — the window always gets the full ID; the view opens its log drawer) or a group (`compose:<project>`, `standalone`, `testcontainers`), which the view reveals, scrolls to, and highlights. An unknown element does not go into the command — the window opens on the view alone. `false` when there is no window |
| `Changed` | signal with no arguments | the snapshot changed — fetch `Snapshot()` again |
| `ActionFinished(s job, s json)` | signal | the end of a `Prune` or `GroupAction` job; `json` is the result with the same `job` |

`id` in actions is the full ID, an ID prefix of 12 characters, or a name — only of a container from the last snapshot (any other → `{ok: false, error: "nie ma takiego kontenera"}` (no such container) with no API call at all). Errors are in Polish; Docker's message (in English) follows after a colon: "Docker nie pozwolił zatrzymać: …" (Docker would not allow stopping: …).

**Responses and jobs.** Methods with a response "after the read" respond with a delay (the work is in a thread, and the D-Bus loop handles other calls in the meantime). `Start`, `Stop`, `Restart`, `Remove`, and `Logs` have a single deadline for the whole action: 20 s from the moment of the D-Bus call, and within it the wait in the thread queue (four at once), connecting with retries, and all of the action's requests (`Remove` is a state check and a removal); the deadline limits every read, not only individual `recv` calls. An action whose turn came after the deadline does not call Docker and responds "Tide nie zdążyło — za dużo akcji naraz, spróbuj ponownie" (Tide did not make it — too many actions at once, try again); when Docker does not make it in time — "Docker nie odpowiada na czas" (Docker is not responding in time) (the operation in Docker may have completed anyway — the next snapshot will show it). The worst case is `Stop`/`Restart`: Docker waits up to 10 s for shutdown, then kills. `Stats` has a total limit of 8 s, `Disk` 20 s for `/system/df` (both at most one read at a time). The response therefore comes after about 20 s at the latest — below the D-Bus client's default limit (25 s), provided the service loop is not blocked by something else. `Prune` (large images and caches can take minutes to remove, a limit of 300 s per category) and `GroupAction` (successive `stop`s after 10 s) are jobs: the call immediately returns a `job` (`prune-<n>-<random>`, `group-<n>-<random>`), and the result arrives in the `ActionFinished(job, json)` signal. Jobs go one after another in a separate thread, so they do not block individual actions or refreshing. The client connects to the signal before the call and matches the result by `job`.

**Snapshot.**

```json
{"available": true, "state": "ok", "error": "", "host": "unix:///var/run/docker.sock",
 "engine": {"version": "29.9.2", "apiVersion": "1.56"}, "live": true,
 "summary": {"total": 16, "running": 9, "stopped": 4, "failed": 1, "projects": 6, "projectsTotal": 10,
             "testcontainers": 3, "testcontainersRunning": 3},
 "groups": [{"id": "compose:cookapp", "kind": "compose", "project": "cookapp", "label": "cookapp",
             "workingDir": "/home/…/CookApp", "repo": {"name": "CookApp", "path": "/home/…/CookApp", "worktree": ""},
             "running": 3, "total": 3, "stopped": 0, "failed": 0, "containers": [{…}]}]}
```

| Field | Description |
| --- | --- |
| `available`, `state`, `error` | `state`: `ok`, `starting` (before the first read), `unavailable` (no socket, connection refused, no response), `denied` (no permission for the socket), `unsupported` (`DOCKER_HOST` other than unix), `error` (Docker answered with an error); `error` is the reason in Polish, e.g. "Docker nie działa — brak socketu /var/run/docker.sock" (Docker is not running — no socket /var/run/docker.sock), "brak uprawnień do /var/run/docker.sock — dodaj się do grupy docker i zaloguj ponownie" (no permission for /var/run/docker.sock — add yourself to the docker group and log in again). Without Docker `groups` is empty, and the service keeps trying |
| `live` | the event stream is connected (changes arrive immediately) |
| `summary` | `running`, `stopped`, `failed` without testcontainers; `projects` — compose projects with at least one running container, `projectsTotal` — all of them |
| group `kind` | `compose` (`id` = `compose:<project>`), `standalone` (`label` "bez compose" (without compose)), `testcontainers` (the `org.testcontainers` label; the window hides them by default). Order: compose with running ones (alphabetically), stopped compose, "bez compose", testcontainers |
| group `repo` | the repository from the `com.docker.compose.project.working_dir` label: from the directory up to `.git`; for a worktree (a `.git` file) — the main repository, and the worktree directory in `worktree`. Only a repository from the projects directory (`--workspace`), otherwise `null` |

Container fields:

| Field | Description |
| --- | --- |
| `id`, `shortId`, `name` | full ID, 12 characters, full name (the action argument) |
| `displayName` | the name generated by compose without the project prefix ("app4-release-localhost-web-1" → "web-1"); a custom `container_name` is unchanged |
| `image`, `project`, `service` | the image and the compose labels |
| `state`, `running`, `health` | Docker's state (`running`, `exited`, `created`, `paused`, `restarting`, `dead`), health `healthy`/`unhealthy`/`starting` or empty |

| `stateText` | the state in words: "działa · zdrowy" (running · healthy), "zakończony" (finished), and for a crash (and for a stop from Tide that ended with a kill) the reason. The snapshot does not have Docker's "Up 14 hours" text — it changes with time by itself, so it would give `Changed` with no change; the window computes the age from the times |
| `startedTs`, `finishedTs`, `createdTs` | epoch seconds (0 — never) — the window computes "13 h", "2 dni temu" (2 days ago) from them |
| `exitCode`, `failed`, `reason` | `failed`: ended with a code other than 0/143 (143 is an ordinary `docker stop`), killed by OOM, `dead`, restarting after an error, or `created` after a failed first start (Docker leaves `State.Error` and code 128: "nie wystartował: port is already allocated" (did not start: port is already allocated)). `reason`: "zabity (kod 137), możliwy brak pamięci" (killed (code 137), possible out of memory), "zabity przez brak pamięci (OOM, kod 137)" (killed by out of memory (OOM, code 137)), "padł (kod 1)" (crashed (code 1)), "padł z naruszeniem pamięci (kod 139)" (crashed with a memory violation (code 139))… Code 137 after a `Stop` from Tide (a kill after waiting 10 s) is not a crash: `failed: false` and a neutral `reason` "zatrzymany z Tide — nie zamknął się w 10 s, więc Docker go zabił (kod 137)" (stopped from Tide — it did not shut down in 10 s, so Docker killed it (code 137)); Tide remembers the moment `stop` was sent and removes the marker only when the container started after it (a later `StartedAt`) or disappeared — a refresh during the stop (Docker reports `kill` immediately) does not erase it; the marker lives only in the service's memory |
| `oomKilled`, `restartCount`, `tty`, `oneoff` | from the container details; `oneoff` — `docker compose run` |
| `ports` | published ports without IPv4/IPv6 duplicates: `{hostIp, hostPort, containerPort, proto, likelyHttp, url}`; `likelyHttp` is false for UDP, known ports of databases, queues, and caches (5432, 3306, 6379, 27017, 5672…) and database images (postgres, pgvector, redis, mongo, rabbitmq, testcontainers/ryuk…) other than their web panels (ports 15672… and panel images: mongo-express, kafka-ui, redis-commander, redisinsight, pgadmin, adminer, phpmyadmin, names with `-ui`, `-admin`, `commander`, `insight`, `-exporter`…). `url` — `http(s)://localhost:<port>` or empty |
| `testcontainers` | the `org.testcontainers` label |

**Refreshing.** A separate thread holds `GET /events` (containers and volumes; volume events — only `create`, `destroy`, `prune` — clear the `Disk` memory without refreshing the snapshot). An event (without `exec_*` — that is, among others, every health check) → a snapshot refresh after 300 ms → `Changed` when the JSON changed. A broken stream → reconnecting every 1, 2, 4… up to 30 s, and in the meantime a refresh every 10 s; with a working stream there is still a full refresh every 5 min. A refresh is a container list and an `inspect` of only those whose state or health from the list changed (an event for a container also forces its `inspect`); without the stream and on the full refresh every 5 min — an `inspect` of all of them, because a quick restart does not change the state from the list but changes the start time. A `Stats`/`Disk` result computed before the end of an action or before an event does not go into memory (a change counter).

**Statistics.** `{ok, error, generatedTs, containers: {id: {id, name, cpuPercent, cpus, memBytes, memLimitBytes, memPercent}}, total: {cpuPercent, hostCpuPercent, cpus, memBytes, containers}, missing: [id]}`. `GET /containers/{id}/stats?stream=false` for all running ones in parallel, at most 8 s in total — whoever does not make it goes into `missing`. CPU as in `docker stats` (100% = one core), `hostCpuPercent` — the share of the whole processor (for the tile); memory is usage without inactive cache (`inactive_file`).

**Cleanups.** `Disk()` → `{ok, error, generatedTs, categories, totalReclaimableBytes, layersBytes}` from `GET /system/df`; a category is `{id, label, note, count, total, reclaimableBytes, totalBytes, warn}`:

| `id` | What | Reclaimable |
| --- | --- | --- |
| `cache` | "Pamięć podręczna budowania" (Build cache) | entries not in use at the moment and not shared |
| `images` | "Nieużywane obrazy" (Unused images) | images without any container (even a stopped one): the size minus the shared part |
| `volumes` | "Wolumeny bez kontenera" (Volumes without a container) | volumes with `RefCount` 0; `warn: true`, the note "mogą trzymać dane baz" (may hold database data) |
| `stopped` | "Zatrzymane kontenery" (Stopped containers) | the write layer of stopped ones; their volumes stay. `warn` and `anonymousVolumes` when one has an anonymous volume (the `com.docker.volume.anonymous` label or a 64-character hex name) — after the container is removed, data, e.g. of databases with `VOLUME` in the image, will be left without a name |

Nothing used by a running container goes into any category. `Prune` calls the Docker endpoints that themselves skip things in use — `images/prune` with `dangling=false`, `volumes/prune` with `all=true` (since API 1.42 only anonymous ones without it), `build/prune?all=true`, `containers/prune` — only for the listed categories, always in the order images, volumes, cache, containers. Containers last: removing stopped ones before images and volumes would expose things that were not on the list. A JSON list is the only accepted shape (an object `{"cache": true}` deliberately not — earlier every "truthy" value, e.g. the string `"false"`, selected a category); an empty list, an unknown id, or any other shape → a refusal with no call at all. A 404 from the prune endpoint is "ten Docker nie zna tej operacji porządków (za stary silnik?)" (this Docker does not know this cleanup operation (engine too old?)), 409 — "Docker właśnie porządkuje" (Docker is cleaning up right now). Result: `{ok, error, reclaimedBytes, details: [{id, label, ok, reclaimedBytes, removed, error}]}`.

**Compose project.** `GroupAction` takes the containers with the project label (`GET /containers/json?filters={"label":["com.docker.compose.project=…"]}`) and orders them by `com.docker.compose.depends_on`: `start` — the not running ones (without one-off `run`s) from the dependencies, `stop` — the running ones from the end, `restart` — the running ones: a stop from the end, then a start from the dependencies. A container that could not be stopped on `restart` does not get a new start and goes into `failed`; `done` is the containers for which all steps succeeded.

**Logs and a shell in the Console** go through the same mechanism as "New task": a task file with argv in `$XDG_RUNTIME_DIR/tide/tasks` and a tab with the "Tide: zadanie agenta" (Tide: agent task) profile in the most recently used Console window or a new window with `launch.py --task` ([New task](#new-task)). The tab title is "docker · <name>". The argv always has the full ID (a name can be given to another container) and `-H unix://<service socket>` (the tab does not inherit the service's `DOCKER_HOST`, and the shell may have a different `docker context`); no gluing of commands. Each tab opens in its own empty directory in `$XDG_RUNTIME_DIR/tide/docker-tabs/` — a tab from the profile takes over the oldest task for its directory, so with a shared directory two quick `OpenShell` calls could swap containers. The task file also has `workdir` (the home directory): after taking over, `launch.py` goes there, so the command and the shell that remains after `docker logs -f` are in `~`, and the tab's directory is free right away. Tab directories older than 10 min disappear at the next tab, and an untaken task file is withdrawn after 20 s.

The logic without Qt is in `tide_daemon/docker/` (`client.py`, `snapshot.py`, `repos.py`, `disk.py`, `stats.py`, `engine.py`), the threads and cache in `docker_monitor.py`.

**Clients.** The "Docker" view in the Tide window takes only `Snapshot()` at startup (the badge in the window bar), calls `Stats()` every 4 s only when it is on top, the window is not minimized, and something is running, `Disk()` — on first display and every 2 min (also only on top and not minimized), and `Logs(id, 200)` every 3 s as long as the log drawer is open. The cleanup confirmation calls `DiskNow()` and until a response newer than its opening (`generatedTs`) the "Wyczyść" (Clean up) button is disabled ("liczę…" (counting…)); the list shows only a fresh read. The dock tile calls `Stats()` every 5 s only with the popup open and `Disk()` when it opens. Both connect to `ActionFinished` before calling a job and match the result by `job`; a job without a result (the service disappeared) releases the buttons after 10 min (in the view: project 10 min, cleanups 30 min), and the result of a job nobody is waiting for (someone else's or one that arrives ahead of the response with a `job`) is kept for at most a minute.

```bash
qdbus6 org.tide.Daemon /Docker org.tide.Daemon.Docker.Snapshot
/usr/bin/python3 daemon/tide-daemon.py --print-docker   # snapshot and disk space, GET only to the Engine API
```

## Card box: environment profile

The module `tide_daemon/box/profile.py` (PT-165, card K-38) describes the box environment: the `tide-box: 1` schema,
risk classification, merging the project profile with the card profile, the recipe hash, and saving and reading on the Board.
Without stack detection and templates (#30) and without building the box (#4). YAML is read by its own subset
(`box/yamlmini.py`: block and bracketed maps and lists, quotes, comments), without a library.

### The `tide-box: 1` schema

| Key | Type | Notes |
| --- | --- | --- |
| `tide-box` | `1` | required |
| `project` | string | the project name (`[a-z0-9._-]`) |
| `type` | `container` \| `vm` | `vm` only passes validation, the build refuses (#4) |
| `base` | string | the base image, e.g. `images:ubuntu/24.04` |
| `stacks` | list | template names (the template list belongs to #30) |
| `node.version`, `dotnet.sdk` | string/number, a list for `dotnet.sdk` | |
| `dotnet.restore-projects` | an optional non-empty list of relative `.csproj` paths | a public restore of only the indicated projects on a HEAD copy; without the list a restore of the whole solution; the list goes into the recipe hash |
| `apt`, `setup`, `verify` | lists of strings | |
| `network.domains` | a list of hosts | without URLs; internal addresses (in every notation: IPv4 mapped into IPv6, `10.1`, numeric/hexadecimal, loopback, link-local, `100.64/10`, private IPv6 also with embedded IPv4 — mapped, backward-compatible, and NAT64, `localhost`, single-label names, and the suffixes `.local`, `.lan`, `.internal`, `.home.arpa`, `.localdomain`, `.intranet`, `.corp`) only in the card profile; the `*.` symbol requires at least two labels after it (`*.com` rejected in the project profile) |
| `network.host-ollama` | bool | |
| `network.full`, `network.full-consent` | bool | card profile only; `full: true` requires `full-consent: true` |
| `ports.pool` / `ports.env` | `[from, to]` (1024–65535) / a list of variable names | |
| `docker.enabled` / `docker.images` | bool / list | |
| `resources.cpu/memory/disk` | number / `24GiB` / `60GB` | default 8 / 24GiB / 60GB |
| `resources.swap` | bool | default false; true refuses to start in a restricted project, outside the recipe hash |
| `packs`, `accepted_red` | lists | card profile only |
| `box` | `per-project` \| `per-card` | default `per-project` |
| `notes` | string | |

An unknown key at any level, a wrong type, `packs`/`accepted_red`/`network.full`/an internal address in the project
profile, and a `type` outside `container|vm` are a `ProfileError` (a list of all the problems).

### Risk classes

`classify(item, known)`; `known` is a domain → class dictionary from the stack templates (#30).

- **red**: `network.full`, `packs` (`pack:<name>`), the addresses `192.168.*`, `10.*`, `172.16–31.*`, `*.local`, `*.lan`, `*.internal`, `*.home.arpa`,
  `*.localdomain`, `*.intranet`, `*.corp`, single-label names, `*.firma.example`, `localhost`, loopback, link-local (`169.254.*`), `100.64/10`, private IPv6 and other notations of
  these addresses, the symbols `*.<one label>` (e.g. `*.com`), remote repositories (`github.com`, `*.github.com`, a host with a `gitlab` label),
- **yellow**: `host-ollama`, domains marked yellow in the templates, domains outside the templates without red features,
- **green**: domains marked green in the templates, `box: per-card`.

Red rules are checked before `known`, so a template cannot "un-red" an item (it can at most
tighten it with a `red` entry).

### Merging

`merge(project, card)` → `MergeResult(profile, dropped_red, project_red)`. Lists are summed without duplicates (the project first; `ports.env` too),
scalars and map fields from the card win (including `box`); the exception: `ports.pool` is a range `[from, to]` and the card replaces it.
Domain entries in `accepted_red` and the `known` keys are normalized like domains (lowercase, no trailing dot). The card's red items go into the result only when
the card's `accepted_red` lists them; the rest land in `dropped_red`. An `accepted_red` with an item that is not in the
card profile (or is not red) is an error. Red items of the project profile (e.g. `github.com`) pass, but go into `project_red`, so that the consent pack (#9) shows them. `project` in the profile must match the slug (`save_project`/`load_project`). A project profile never contains `network.full` or internal addresses.

### Recipe hash

`recipe_hash(profile, templates_version, agents_version)` = SHA-256 of the normalized keys (after filling in
the defaults, independent of key order and YAML whitespace) together with the templates and agents version.
Included: `type`, `base`, `stacks` (order), `node`, `dotnet` (SDK versions as a set), `apt` (as a set), `setup` (order),
`docker.enabled`, `docker.images` (as a set). Not included: `resources`, `ports`, `network.*`, `verify`, `packs`,
`box`, `notes`, `project`, `tests.changed`, `tests.full` — the limits and policy are read at start or after the build,
and the test contract (K-69) does not concern the image. The list: `HASH_KEYS` and
`NON_HASH_KEYS`, a test ensures that together they cover the whole schema. `recipe_stamp(...)` additionally returns the digests of the
keys; the build saves it with the snapshot, and `diff(profile, stamp, templates_version, agents_version)` returns the list of
keys that differ from the saved stamp (`apt`, `stacks`, `templates`, `agents`…) — the source of the "needs rebuild" state.

### Saving

The Board directory: `$TIDE_BOARD_DIR` (default `~/DevWorkspace/tide-board`). Project profile:
`projects/<slug>/box.yaml`, card profile: `projects/<slug>/cards/<card id>/box.yaml`. The write is atomic (a
temporary file in the same directory, `fsync`, `rename`) and preceded by validation; path names are limited to
`[a-z0-9._-]`. The project profile shares a file with the K-69 test contract (`tests`); the rules of the shared file — "Zgodność
z profilem pudełka K-38" (Compatibility with the K-38 box profile) in the test contract section. `repo_proposal(worktree)` reads `.tide/box.yaml` from the repository only as a proposal
(validated like a project profile, a link outside the repository rejected) and writes nothing there — the profile
does not go into the projects' repositories (CLAUDE.md firma).

### Environment section in the planning prompt (PT-190)

`cardflow/environment.py` assembles the section that `planning.prompt` (Lightweight) and `planning.full_prompt` (Full) insert
before the step "Poznaj repozytorium" (Get to know the repository) / "Pracuj w tej kolejności" (Work in this order) (the `environment` parameter; empty = the prompt unchanged).
The section is computed by `engine` when building the planning prompt (also for plan fixes); it does not change `_launch`, `agentcmd`, or
the starting of steps. A file read error does not stop planning — the section then disappears.

- **Project environment (detected)**: the `StackReport` facts (`tasks/stack.detect`, YAML without empty fields) and a profile proposal
  in `tide-box: 1`. The proposal source: the repository's `.tide/box.yaml` (`repo_proposal`, marked "z repozytorium
  (.tide/box.yaml)" (from the repository (.tide/box.yaml)); an invalid file → templates with a note) or the stack templates (`propose_profile`, "z szablonów Tide" (from Tide templates),
  with a list of network and image entries; red entries are disabled). Data from the repository is in the prompt as quoted
  data, not commands. Each block is truncated to 8 KB, the instructions at the end never.
- **Project environment (approved)**: when the Board has `projects/<slug>/box.yaml` — the profile and the recipe hash
  (`recipe_hash` without the templates and agents versions; the box build adds those). The "detected" section appears only when
  the detected stacks differ from the approved ones.
- **Instruction for the planner**: it may fix the proposal in `out/box.yaml`; it does not enable red entries and does not
  add them to `accepted_red`; it settles the environment in the plan and does not ask about it during the steps. For a project without a pattern
  (`stacks: []`, an empty proposal) it adds to the plan (spec.md, Further Notes) exactly one question "Czego potrzebuje
  środowisko?" (What does the environment need?).

### `out/box.yaml` in a plan proposal

`plans.read` reads `box.yaml` next to `plan.md` (only for a file named `plan.md`; plans in the repository share a directory)
and validates it with the card profile schema from `box/profile.py` (`parse(..., "card")` + `merge`), like the ticket table.
Format errors go into `Proposal.errors` with the prefix `box.yaml:`: an unknown key, a wrong type, `network.full` without
`full-consent`, a red entry (an internal address, `github.com`, `packs`) without an explicit `accepted_red`, a link
outside the plan directory, a file larger than 64 KB. A missing file is not an error (`Proposal.box` is `None`); a valid file
comes back in `Proposal.box`. The file is not saved or copied to the Board — the user approves it.

### Consent pack: card environment (PT-172)

With `box.enabled: false` there is no section (the environment consent does not apply, PT-290; 09.11: computing it on every
refresh loaded the Board over a hundred times) — the exception is a card that is already waiting for the environment.
A plan awaiting approval (and a plan approved with "Tylko plan" (Plan only)) has in `derived.flow.plan.environment` a "Środowisko
karty" (Card environment) section, computed by the service (`cardflow/envconsent.py`) from the project profile (approved or the proposal from PT-190)
and the `out/box.yaml` proposal (saved in `plan.proposed.box`). The panel only shows it — it does not know the list of keys or the
risk rules. The section's fields:

- `state` / `stateLabel`: `clean` (with the snapshot date), `build` ("do zbudowania" (to be built)), `rebuild` ("do przebudowy" (to be rebuilt)), and `diff`
  `{added, removed}` — the difference of entries relative to the approved profile at the `clean` snapshot hash (the box journal),
- `items[]`: `id`, `label`, `group`, `risk` (`green|yellow|red` from `box/profile.py`, `display_items`), `riskLabel`, `token`
  (the theme color name), `scope` (`projekt|karta`), `enabled`, `needsConsent` (red: disabled by default, enabled
  only by an explicit selection), `locked`, `note`; the `docker` entry carries the K1 risk description from the proposal (the docker template)
  and a note "K3 aktywne/nieaktywne" (K3 active/inactive) according to `box/k3.py`; the `box:per-card` entry has `toggle {on, off}` (by default as
  in the project profile),
- `yaml` (the card profile without `box` and consents, for the "Zmień" (Change) field), `acceptKey` (`accepted_red` — the consent key that the panel appends
  to the YAML from the selected entries), `notes[]` (e.g. a rejected internal address in `.tide/box.yaml`, D13),
- `question`: "Czego potrzebuje środowisko?" for a project without a profile and without a pattern — a plan item, not a question in a step.

**Refresh cost (K-98 #1, PT-342).** A single BoardMonitor refresh loads the Board at most once: `publish`
creates a short-lived context `board/readcontext.BoardRead` and passes it explicitly to the file snapshot
(`BoardStore.snapshot(board=…)`) and the card projections (`flow.derived(card, read)` → `engine._environment_view` →
`envconsent`/`projectconsent`; also `_project`, `_review_policy`, `_plan_review_policy`). The context is not
global, does not go to the mutation worker, and dies with the end of the refresh; functions without a context read the Board fresh
as before (a consent approval, a step start; the tick has its own context — K-98 #3 below). The section has a cache `engine._env_cache` shared by all
projects, plasma-tide too, keyed by a fingerprint of the inputs (`envconsent.view_key`: the card's project and repository, the repository
registry, the card journal and the plan version, `boxWait`, the switch and the settings file, the project and card profiles, the box
journal, `.tide/box.yaml`, the project's consent store and the pack copies, the K3/K1 mode) — any change of an input from this list
invalidates the entry at the next refresh. **Limitation:** the key consists of file and directory metadata
(inode, size, mtime, ctime, mode), without reading the content. It does not cover the content of the pack sources (`packs.inspect`: glob,
file count, size, scanner hits) or the content of repository files read by `stack.detect`
(e.g. `package.json`), other than the repository root directory and `.tide/box.yaml`. A change of such a file without a change of
the repository root directory's metadata does not refresh the preview until the nearest change of another input. The pack
copy store is walked once per project in a single refresh (`BoardRead.once`), at most 4096 entries; above that
the key never hits and the section is computed on every refresh. The cache serves presentation only:
`ApproveCardEnvironment` and the box barriers compute the fingerprint, the pack content (`blocked`), and permissions fresh, so an old
preview (`exposure-expected`) is rejected after an input changes. Measurement: `daemon/tests/perf_board_refresh.py`
(the methods Snapshot, Work, Badge, GetCardFlowSettings, RenderFlow, and BoxStatus with an `incus` stub;
comparing the base with HEAD with the same reproducer: `daemon/tests/perf_board_refresh_compare.sh`).

**K-98 response time gate (PT-345).** `bash daemon/tests/perf_gate_k98.sh <directory>` measures one merged
candidate (HEAD): the PT-342 reproducer unchanged (the card base `a95d7a31` vs HEAD, without load and with a busy worker
`--busy-ms 2000`), 5 separate cold starts, and the p95 of the first call of a D-Bus series on a real service with a process
probe (`daemon/tests/perf_dbus_first_call.py`, 20 series, the ticket start vs HEAD). A trial is valid only when the
1-minute `/proc/loadavg` < the number of cores / 2 before and after; at most 3 trials, the first valid one decides. Then the PT-342…PT-345
regressions and `box.enabled=false` on the same HEAD, and `perf_gate_k98_eval.py` evaluates the thresholds (each refresh
≤ 500 ms and ≤ 1 Board load, p95 < 1 s for Snapshot, GetCardFlowSettings, BoxStatus, and the first call of a series).

**Tick and recovery without duplicated reads (K-98 #3, PT-344).** `engine.tick` has a single immutable history read phase
(`trialhold.HistoryPhase`): recognizing an incomplete history after a restart, recovering `step.started`
(`_recover_started_steps`), and reconciling the box network (`boxstep.recover_network`) share one full strict read
of the card with an identity check (`read_process_history(journal, card, phase)`; an error and the unknown state are remembered in the phase
and do not disappear). A write of this process to the card history (`Journal.generation`) invalidates its read — the next use
reads again. The phase does not replace a read before an effect: the write of a recovered start goes through `Journal.unchanged`
(under the lock a fresh full read must equal the decision's read, otherwise `HistoryChanged` without a write), and before the
first mutation of the card in `recover_network` (releasing ports, the refguard and the step's resources, the network policy) a fresh
read compares `HistoryPhase.unchanged`; on a change the mutations of this card are refused, the network state is not
remembered, and the next tick reconciles it anew. The phase lasts the whole tick: `_recover_started_steps` called from `_tick_card`
also uses it. The other card barriers (inventory, process check, outside) read the history outside the phase;
since K-105 PT-415 reads without an effect use the process history memory and the card process state, and the gates before
an effect read fresh (the card journal section, "Process history and card process state memory"). The phase is
a view of one tick over that memory. Comparisons before a write (`Journal.unchanged`, `HistoryPhase.unchanged`)
and gate writes (`append`, `append_complete`) read fresh (`Journal.read_fresh`). The Board:
the tick creates one `BoardRead` (the context from #1) and passes it explicitly to `_project_base` (without changing the base fields — without
a transaction and a load), `_tick_card` (detecting a card move, `_project`, `_tracker_notes`), `_queue_notices`,
`_caretake`, and `_guard_card` (the card's column). A Board file write in this process (`BoardStore.generation`) invalidates
the tick context (`BoardRead(store, invalidate_on_write=True)`); the BoardMonitor refresh context (the default) does not
do this — it keeps one version of the Board through the whole refresh with a single load, even when the tick writes
the Board in parallel (contract #1). Paths with a side effect (`moved`, a step start and resume, a merge, a steward start) read the Board
fresh as before. Measurement: `daemon/tests/perf_tick_recovery.py` (a restart with a live owner, a dead process,
and the unknown state; counters of full reads and Board loads; comparing the base with HEAD:
`daemon/tests/perf_tick_recovery_compare.sh`).

**Board load memory in the tick context (K-113 #3, PT-467).** The tick context loads the Board through
`BoardStore.load_for_tick`: when the **input fingerprint** of `load()` equals the fingerprint of the last successful load by this route,
it returns the same `Board` (read-only, shared by successive ticks) without a transaction, without reading file contents,
and without parsing. The fingerprint (`_load_fingerprint`) covers `store.generation` (a write in this process), the `realpath` of the Board
directory, the `board.md` marker file, the names in `tags/`, `projects/`, and `cards/`, the `lstat` of these directories and of every
file read (`st_dev`, `st_ino`, type and permissions, owner, `st_size`, `st_mtime_ns`, `st_ctime_ns`; for a link
also the `realpath` and the target's `stat` — the "only files inside the Board" boundary depends on them), the directory and
`projects/<slug>/box.yaml` of every project, the path and metadata of the test contract approval store (the file
and the directory), and the approval store errors from steps (`approval_for_step`, in-memory state). The fingerprint does not read contents
and does not do `realpath` for ordinary files. Replacing a file with the same size and the mtime set back
changes `st_ctime_ns` (an in-place write) or `st_ino` (a replacement by rename). The memory is skipped (an ordinary `load()`
on every tick) when an unrecovered report acceptance intent is waiting (`.git/tide-acceptances/*.json` — recovered by the
`load()` transaction), when `lstat` gives an error other than a missing file, or when any entry changed (ctime) in the last
50 ms (`_RACY_NS`): another change in the same timestamp granularity might not change the fingerprint — like
"racily clean" in git. Project fields computed from state outside the Board (no repository, the default `ai-docs`
from `docs/agents`) are checked on every hit; a difference gives a fresh load. A different fingerprint or no memory
is an ordinary `load()`: the same transaction (with intent recovery), the same errors and the `_inside` boundary on every
fresh load; the memory entry gets the fingerprint from before the load (a change during the load gives another load),
and a load error is not remembered. Every Board change — the GUI, `git`, a manual edit, a write in the process — gives a
fresh load in the next tick context. The public `load()`, write transactions, and the `Snapshot` refresh context (#1)
do not use the memory.

**Board reads in the tick: the tick context and fresh gates (K-113 #5, PT-469).** The reads in `tick`/`_tick_card` without an effect
read from the tick context (memory #3): detecting a card move (the `moved` write itself reads fresh), `_project_read` before
resuming a start, `_project_base` (when the base fields are already there), `_queue_notices`, `_guard_card`, `_caretake`,
`_tracker_notes`, `RestMemory.begin`, and the step order (`_order_board`). Fresh `store.load()` calls (through `_project` without a
context) stay as gates or inputs of an effect: `_plan_next` (the ticket limit, the repository for the plan commit,
the ticket branch, and `_launch`), `_planning_branch` (the card branch name for `card-branch`), `_plan_on_board` (`ai-docs`
decides on the plan commit), `_outside_recheck_rules` and `consent.automatic` (consents), as well as the card move write, the step
start, and `upsert_project`. A warm tick of a card with an approved plan therefore still does three full loads (`_plan_next`,
`_planning_branch`, `_plan_on_board`; the profiler frames `pomiar-k113-przed.json`). Moving them to the tick context
requires changing several engine methods (deciding "nothing to do" from the context, a fresh read only before an effect);
the decision on a separate ticket will be made after the "after" measurement (PT-468).

**Reads while the mutation queue is busy (K-98 #2, PT-343).** The mutation queue (the single-threaded `tide-cardflow` worker)
remains the only place for card flow writes; simple `/Board` reads do not wait for it:

- `Snapshot()` (also the Card panel — `cards[].derived.flow`) returns the last **published** version. Publication
  (`BoardMonitor.publish`/recomputation) goes under the `CardFlowMonitor.read_gate()` gate and **does not take the journal
  lock**: it reads card journals in presentation mode (`Journal.presentation()` — its own memory, the service's copy is
  only appended to, the file on the board is replaced atomically, a truncated last line of an append in progress is skipped).
  Consistency with respect to actions is provided by an epoch counter (a seqlock): an action from the queue (everything except the tick) bumps it for the duration of
  the execution; a publication during an action, or one during which an action started or finished, is rejected
  before any effect and comes back every `PUBLISH_RETRY_MS` (50 ms) — no version shows an action halfway
  (e.g. `card.moved` without `card.paused`), and the read gets the previous version. Writes of the tick and recovery show up write by
  write right away (each one complete), as before the change — the tick does not hold publication, so a Snapshot after a tick write
  sees it deterministically, even when the tick still holds the lock. The consistency boundary: "the whole action or nothing" applies to
  queue actions. The tick is not an action, so one version may compose the Board from the moment after one tick write
  (e.g. the card's column) with the journal from before its next write (or the reverse) — each of them complete, as before
  K-98 #2; the atomicity of a whole tick is the scope of #3. Syncing changes from outside (`sync`) takes
  the lock without waiting and defers the commit to the next opportunity.
- Loading the Board during publication (`store.snapshot` and the card projections: the environment view, the review policy) also **does not
  wait**: it goes in `BoardStore.without_waiting()`, where a Board transaction held by another thread (the worker:
  StartCard in `git worktree add`, a merge, the tick) or a flock of another process (a write of the K-38 box profile) gives `BoardBusy` right away.
  Such a publication is rejected as a whole (also when the exception was caught lower down and a placeholder content was supplied;
  the environment view memory does not hold it back) and comes back after `PUBLISH_RETRY_MS`; Snapshot returns the previous version.
  Background publications (the recomputation clock, delivering results, retries) do not wait at all; an explicit `Snapshot()` waits
  for the Board transaction at most `SNAPSHOT_WAIT_S` (250 ms, one deadline per call), so a short tick transaction
  (e.g. `project_card_base`) does not leave a read after a tick write with the version from before the write, and a long mutation gives
  the previous version after the deadline. Board writes from the clock in the main thread also go without waiting: syncing
  changes from outside (`store.sync`, `mark_ready_specs` — with a busy Board they come back in the next sync)
  and persisting the links of cards to sessions (`link_session` — comes back at the next recomputation).
- An action's result is sent back after the publication of its effect and the journal commit (FIFO; a commit with the lock busy is
  retried every 20 ms without blocking the main thread, the snapshot is published right away and again after the commit), and the worker
  takes the next task only after this delivery (at most `DELIVERY_WAIT_S` = 5 s of waiting, without a journal
  lock). `Snapshot()` first delivers the finished ones, and the undelivered results (without waiting).
- The response has `version` at the start (the publication number; it grows on every change of the content or of the cards' `boxWait`, the same
  state keeps the number) and `readState: "ready"`. Before the first publication: `{"version": 0, "readState": "preparing",
  "cards": [], …}` — not an empty, ready Board; `Work()` then has `readState: "preparing"`, and `CardDocs` returns
  `error: "preparing"`. The Board window (`apps/board/backend.py`) does not replace a snapshot it holds with such a response,
  and without a snapshot does not save it as the Board (the window stays in the "ładowanie" (loading) state until the first `Changed`).
  The other `Snapshot` consumers do the same (PT-385): the Board widget (`widgets/board`, `BoardSnapshot.qml`), the
  "Karty w przepływie" (Cards in flow) list in the Sessions view (`apps/sessions/backend.py`, `flowCardsLoading`), and the projects in the Issues window
  (`apps/issues/backend.py`, `projectsLoading`) keep the previous snapshot, and without one show "wczytywanie" (loading) until the
  nearest `Changed` (sent at the first publication), after which they read `Snapshot()` again.
- `GetCardFlowSettings` reads the settings file right away (an atomic write) and adds `version` (a digest of the bytes of the file from which
  the response was built; `missing` — no file) and `readState: "ready"`.
- `BoxStatus`/`BoxCardStatus` run in a separate read pool `tide-board-read` (not in the mutation queue) on the published
  version (`BoardMonitor.published()`: projects, cards, and the cards' `boxWait` from one publication) and carry its `version`.
  The box and slot state is a current read of the host; the slot through `admission.view` — the same reconciliation with Incus as
  `admission.snapshot`, but **without writing** `state.json` (box jobs and the tick write it). Before the first
  publication: `{ok: false, error: "preparing", readState: "preparing", state: "unknown"}`.
- Read data serves presentation only: writes (consent, start, merge) go through the queue and check the
  history and permissions fresh before an effect. Heavy Git previews (`CardBranchDiff`, `PreviewCardMerge`, `PreviewCardBaseChange`, …)
  keep their own contract in the queue and are not simple reads.
- **Limitation:** Board writes from the main thread (`CreateCard`, `UpdateCard`, `MoveCard`, …) still wait for the
  Board transaction if a long mutation holds it (e.g. StartCard). `Worktrees` (`busy_places`) reads the journal under the
  lock as before. A presentation read skips only the lock; `Journal.read_complete` and its parser are unchanged
  (a truncated line still gives a strict read error), and writes and gates read under the lock as before. The strict readers
  of gates (`read_complete`, `cards_complete`) have no presentation mode: called in it they raise
  `StrictReadInPresentation` (a program error, not "unknown") instead of reading without the lock or waiting for a mutation.

`ApproveCardEnvironment(card, yaml, onlyPlan)` (JSON; errors like other /Board methods):

1. `prepare`: `yaml` is a card profile (`profile.parse(…, "card")`). An unknown key, a wrong type, a red entry
   (`network.full`, an internal address, `github.com`/`gitlab`, `packs`) without an entry in `accepted_red`, an `accepted_red` entry
   without such an item → `{ok: false, error: "invalid-environment", message, errors[], items[]}` **without any write,
   without plan approval, and without a build** (also when called from outside the panel). A project without a profile yet: the service itself
   builds the proposal (`.tide/box.yaml` or the stack templates), and its red entries also require consent; an internal
   address in the project profile is rejected (D13).
2. A plan or a plan fix awaiting approval is approved (`ApproveCardPlan`; PT-290: also the diff
   of an approved plan from the window in which the environment section replaces the plan button); `review-pending` interrupts
   before the write.
3. `onlyPlan = true`: nothing is written to the Board; with `box.enabled: true` the `box.wait` event sets
   `boxWait: environment`, and with the box disabled the consent is only deferred (D1 below).
4. `onlyPlan = false`, `box.enabled: true`: saving the project profile (if it was not approved) and the card profile to the Board
   (`projects/<slug>/box.yaml`, `…/cards/k-n/box.yaml`), the event `box.approved source=ui` with `accepted_red`, `items`,
   and `recipe`, clearing `boxWait`, and when the box has no `clean` snapshot with this hash — a build (`build`) or
   rebuild (`rebuild`) by the `Builder` from PT-167 in a separate thread (a stub: `FlowEngine.box_launcher`). The response:
   `{ok, saved[], acceptedRed[], build: "build"|"rebuild"|"", boxWait: "", boxEnabled: true, decision: "recorded"}`.
5. `onlyPlan = false`, `box.enabled: false` (PT-290): after the same validation, writing the decision alone — the profiles on the Board
   and `box.approved` — without pack copies, a build, a start, Incus, and exposure; `boxWait` unchanged (also the old
   `environment`), the consent deferred. The response: `{ok, saved[], acceptedRed[], build: "", boxEnabled: false,
   decision: "recorded", boxWait, message}`; `message` says that no build was ordered and the environment is not running.

**D1 (PT-290, K-38 F2): the environment consent only with `box.enabled: true`.** Approving a new plan
(`engine.approve_plan`, `ApproveCardPlan`) and a plan fix ("Tylko plan") with the box disabled does not create
an `environment` wait and does not block the host step (the PT-289 switch picks the host anyway). In the journal there remains
`box.wait` with an empty `value` and `deferred: environment` (`FlowState.box_env_deferred`, `boxwait.require_environment`).
After the box is enabled again, the first attempt at an executor step turns the deferral into `boxWait: environment`
(`source: box-enabled`) and waits — neither the plan approval itself nor a decision saved while the box was disabled is consent
to a build and exposure. Only `ApproveCardEnvironment` with the box enabled clears the wait and orders the build.
The plasma-tide project consent (`projectconsent`) with the box disabled records the decision without `environment`
(the response `boxEnabled: false`); with it enabled, the v1 exposure barrier applies as before. A restart with an old
`environment` builds nothing and migrates nothing; existing consents, proposals, and history are not removed.

The Board is committed by Tide; the profile is written only by the service.

`BoxStatus(slug)` returns `Builder.status` (`state`, `recipeHash`, `profileHash`, `diff`, `exists`, `usable`,
`nicFiltering`) plus `nicLabel` ("filtrowanie NIC: tak/nie" (NIC filtering: yes/no)) and `cards[]` = `{card, boxWait}` for the project's cards with a journal,
`admission` (the slot state, read-only), and the `version` and `readState` of the published snapshot (K-98 #2).
Errors: `bad-project`, `no-project`, `preparing` (before the first snapshot publication).

#### The `boxWait` field

A card state field (journal: the `box.wait` event with `value`, empty = cleared; `FlowState.box_wait`;
`derived.flow.boxWait` and `boxWaitLabel`; `BoxStatus.cards[].boxWait`). The only reader: `cardflow/boxwait.py`,
`card_box_wait(card)` (accepts a `FlowState`, a card dictionary, or `derived.flow`); #18 uses it and does not read
the journal itself. No field = nothing blocks.

| Value | Panel tag | Set by | Cleared by |
|---|---|---|---|
| `environment` | "czeka na środowisko" (waiting for the environment) | PT-172: "Tylko plan" and every plan approval (`engine.approve_plan`, also `ApproveCardPlan` from the decision window and the plan view) — a plan does not bypass the consent pack; PT-290: only with `box.enabled: true`, and a deferred consent turns into `environment` after it is enabled again | PT-172: `ApproveCardEnvironment` without `onlyPlan` with the box enabled |
| `start` | "czeka na pudełko" (waiting for the box) | #18 (engine, PT-181): the box is stopped, a start is in progress | #18, when the box responds |
| `rebuild` | "do przebudowy" | #18: the recipe hash does not match or NIC filtering is missing, the rebuild waits for approval with the plan | #18, when the box is compatible again |

`set_box_wait(journal, card, value)` records the change (without an event when the value and the deferral do not change;
clearing also deletes the PT-290 deferral). `environment_pending(card)` — the card is waiting for the environment or has a
deferred consent; `require_environment(engine, card, source)` — D1 after plan approval.

#### Domain question (`denied` step result)

A result `{"result": "denied", "command": "network: <domain>"}` (a refusal by the box proxy) is not consent to a command:
`step.finished` gets a `network` field, and the card decision (`kind: permission`) has two options with no recommended one:
`box-allow` ("Dopisz do profilu karty i przebuduj" (Add to the card profile and rebuild)) and `deny` ("Nie, wznów bez" (No, resume without)). `box-allow` adds the domain to the
card profile (`profile.save_card`, a red domain is rejected — it is decided in the plan), appends the event
`box.rebuild-requested` (for engine #18), and resumes the step with a note; `deny` resumes the step with a note about the refusal
and writes nothing.

### Migrating `cardflow.json` to a profile proposal (PT-190)

At service start `FlowEngine.migrate_environment()` (`box/migration.py`) creates, for every project with an entry
in `networkDomains` or `outsideCommands` that has neither `box.yaml` nor `box.proposed.yaml`, a file in the Board
`projects/<slug>/box.proposed.yaml`: `networkDomains[slug]` → `network.domains`, `outsideCommands[slug]` → `setup`
(`install`/`ci`/`restore`/`pull`… commands) or `verify` (the rest; the family's trailing ` *` is removed).
Domains that the project profile schema does not accept (an internal address, `*.com`) go into `notes`. This is only a
proposal for approval — it is not a profile, nothing is approved or overwritten; the old keys stay
in `cardflow.json` (a separate ticket will remove them). The file is created once: deleting the proposal without approving it makes
the next start create it again. A Board write error does not stop the service.

## Local models

A single place in the service through which Tide talks to the local model server: now Ollama on this computer (`http://127.0.0.1:11434`), later a Mac Studio on the network — by changing the address in the settings, with no code changes (plan `docs/agents/plans/2026-10-27-local-models.md`, PT-14). The service reads the servers' state and exposes it over D-Bus; through the model call (`tide_daemon.models.chat`) go "Na daily" (For the daily) and card descriptions in `"summary": "local"` mode (PT-15, the sections "Morning report" and "Coordination"), always through the shared queue (PT-16, "Queue" below).

| Item | Value |
| --- | --- |
| Object | `/Models` |
| Interface | `org.tide.Daemon.Models` |

| Member | Signature | Description |
| --- | --- | --- |
| `Status()` | `→ s` | JSON `{checked, default, config, endpoints, roles, agentProgram, search, report, pulls, queue}` (described below), without tokens or command contents |
| `Refresh()` | | read the state now, without waiting for the next cycle (the result arrives in the `Changed` signal if something changed) |
| `SetEndpoint(s json)` | `→ s` | a new or changed endpoint `{name, url, kind, token?, clearToken?}` → `{ok, error}` (saving settings below) |
| `RemoveEndpoint(s name)` | `→ s` | remove an endpoint → `{ok, error}`; refused for the default one, the only one, and one with pinned roles |
| `SetDefault(s name)` | `→ s` | the default endpoint — the "this computer / Mac" switch: roles without an endpoint follow it → `{ok, error}` |
| `SetRole(s json)` | `→ s` | a role `{role, model, endpoint?, ctx?}` → `{ok, error}` |
| `SetAgentProgram(s program)` | `→ s` | the agent program on a local model: `codex` or `claude` (`agentProgram` in `models.json`) → `{ok, error}` |
| `SetSemanticSearch(s state)` | `→ s` | semantic search in KRunner: `on` or `off` (`semanticSearch` in `models.json`; disabled — the index is deleted) → `{ok, error}` |
| `SetReportSummary(s mode)` | `→ s` | `summary` in `report.json` (`claude`, `local`, `rules`), takes effect right away in the service → `{ok, error}` |
| `SetExecutor(s json)` | `→ s` | an executor in the catalog `{name, program, model, provider, endpoint?, classes, effort?, oldName?}` → `{ok, error}` (the executor catalog below) |
| `RemoveExecutor(s name)` | `→ s` | remove an executor → `{ok, error}` |
| `SetProvider(s json)` | `→ s` | another provider `{name, label?, url, api, token?, clearToken?}`, `api` is `openai` or `anthropic`; the token goes to `tokens/providers/<name>` (0600), never in a response → `{ok, error}` |
| `RemoveProvider(s name)` | `→ s` | remove a provider and its token file; refused when it has executors → `{ok, error}` |
| `ResolveExecutor(s json)` | `→ s` | a class to an executor `{provider, class[, author]}` → `{ok, error, executor, provider, class, local, suggested, candidates}`; with `author` — a reviewer |
| `TestEndpoint(s json)` | `→ s` | a connection test without saving: `{name}` or `{url, kind, token?}` → `{ok, error, version, models}`; the response comes after the read (up to 2 × 3 s), the service loop does not wait |
| `PullModel(s json)` | `→ s` | pulling a model to an Ollama endpoint `{endpoint, model}` → immediately `{ok, error, key}`; progress in `Status().pulls[key]` |
| `Changed` | signal with no arguments | the state of servers, models, settings, pulls, or the queue changed — fetch `Status()` again; it also comes after every successful write (because of pull progress and the queue together at most once a second) |

```json
{
  "checked": true,
  "default": "local",
  "config": {"path": "/home/user/.config/tide/models.json", "exists": false, "error": "", "warnings": []},
  "endpoints": {
    "local": {"name": "local", "url": "http://127.0.0.1:11434", "kind": "ollama", "auth": false,
              "online": true, "error": "", "version": "0.40.11",
              "models": [{"name": "qwen3:8b", "size": 5225388164, "family": "qwen3", "parameterSize": "8.2B",
                          "quantization": "Q4_K_M"}],
              "loaded": [{"name": "qwen3:8b", "size": 6000000000, "sizeVram": 6000000000, "gpuPercent": 100,
                          "contextLength": 8192, "expiresAt": "2026-10-27T17:05:00+01:00"}]}
  },
  "roles": {
    "summary": {"endpoint": "local", "model": "qwen3:8b", "ctx": 8192, "installed": true, "available": true,
                "error": "", "pinned": false}
  },
  "agentProgram": "codex",
  "search": {"enabled": true, "state": "ready", "items": 412, "pending": 0, "updated": "2026-10-28T10:02:41+01:00",
             "error": "", "model": "bge-m3:latest", "dims": 1024},
  "report": {"summary": "local", "configured": "local", "summaryModel": "sonnet", "raw": null, "pinned": false,
             "path": "/home/user/.config/tide/report.json", "warnings": []},
  "pulls": {"local/bge-m3": {"endpoint": "local", "model": "bge-m3", "status": "pulling 24d7…", "completed": 512000000,
                             "total": 1157672605, "running": true, "error": ""}},
  "queue": {
    "local": {"running": {"endpoint": "local", "role": "summary", "model": "qwen3:8b",
                          "since": "2026-10-28T07:00:12+01:00"},
              "waiting": [{"endpoint": "local", "role": "describe", "model": "qwen3:8b",
                           "since": "2026-10-28T07:00:15+01:00"}]}
  }
}
```

| Field | Description |
| --- | --- |
| `checked` | `false` only before the first read after the service starts (then all endpoints are `online: false` without an error) |
| `config` | the settings file: the path, whether it exists, `error` (the file is unusable — everything is default), `warnings` (skipped endpoints and roles) |
| `endpoints.*.online`, `error` | whether the server responds; with `false` a short reason in Polish, e.g. "Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:11434)" (Local model unavailable: the server is not responding (127.0.0.1:11434)) |
| `endpoints.*.auth` | the endpoint sends a token (`tokenFile`) — the token itself never leaves the service |
| `endpoints.*.version` | the Ollama version; `null` for an `openai` server |
| `endpoints.*.models` | pulled models: `name`, and for Ollama also `size`, `family`, `parameterSize`, `quantization`; an Ollama cloud model (`…:cloud`, `…:120b-cloud`) additionally has `cloud: true` (local ones — without this field) |
| `endpoints.*.loaded` | models in memory (Ollama `/api/ps`): the size, `sizeVram`, `gpuPercent` (the share on the card — below 100 the model writes ten or more times slower), `contextLength`, `expiresAt`; `null` for an `openai` server, which does not provide this |
| `roles.*` | the endpoint, the model, `ctx` (`null` — the server's context), `installed` (the model is on the server; `null` when the server does not respond), `available` (the server responds and the model is there), `error` (the role is unavailable, e.g. its endpoint is skipped; usually empty), `pinned` (the role is pinned explicitly — `"endpoint:model"` in the file — also when it is the current `default`; `false` — it follows `default`, so `SetRole` without `endpoint` unpins a pinned role) |
| `agentProgram` | the agent program on a local model (the "Nowe zadanie" (New task) window, the "Lokalny" (Local) mode): `codex` or `claude`; a missing key or a bad value in the file — `codex` (a bad one with a warning in `config.warnings`) |
| `search` | semantic search in KRunner (the "KRunner" section): `enabled` — `semanticSearch` from `models.json`; the index state: `state` — `off` (disabled), `waiting` (waiting for the first pass or for a free server), `indexing`, `ready`, `error` (with `error`, e.g. "serwer odpowiedział 404: model "bge-m3" not found" (the server responded 404: model "bge-m3" not found)), `inactive` (a service without KRunner, `--no-krunner`); `items` — sessions with a vector, `pending` — to be computed, `updated` — the last full pass (ISO 8601), `model`, `dims`. Without texts and vectors. An index state change gives `Changed` (at most once a second, like the queue) |
| `report` | the "Na daily" mode and the card descriptions (`report.json`, the "Morning report" section): `summary` — the mode the service uses now, `configured` — from the file after validation (an unknown value → `rules`), `raw` — the unknown value that was entered (otherwise `null`), `pinned` — the mode forced by the `--report-summary` option (then `SetReportSummary` changes only the file), `summaryModel` (counts in `claude` mode), `warnings` from reading the file |
| `pulls` | model pulls (`PullModel`) by the key `"<endpoint>/<model>"`: `status` (the stage from Ollama: `start`, `pulling manifest`, `pulling <digest>`, `verifying sha256 digest`, …, finally `success` or `error`), `completed` and `total` in bytes (the sum of layers; `total` is 0 until the server gives it), `running`, `error` (a message in Polish). Finished ones disappear after 15 s, failed ones after 60 s; an empty object when nothing is being pulled |
| `queue` | the model call queue (PT-16) for each endpoint from the configuration (and an endpoint that is no longer in `models.json` but still has something running on it): `running` — the running call `{endpoint, role, model, since}` (`endpoint` — the endpoint it came from; `since` — the start of the call, ISO 8601) or `null`; `waiting` — the waiting ones `{endpoint, role, model, since}` (`since` — joining the queue) in the order in which they will start. The queue counts places per server, not per name, so endpoints with the same address show the same state (with the entries of both). A call on an endpoint's old address (the address changed in the meantime) does not disappear: it sits next to the endpoint's current state in the `other` list — `[{lane, running, waiting}]`, `lane` is the old `scheme://host:port` address; the field exists only when there is such a call. Without command contents. An optional field: the client (the "Modele" (Models) tile) works without it |

**Settings.** `~/.config/tide/models.json` (`$XDG_CONFIG_HOME/tide/models.json`; `$TIDE_MODELS_CONFIG` overrides the path — tests, diagnostics), read on every read and call — a change takes effect without restarting the service (`Refresh()` right away). The file is optional; without it there is one `local` endpoint (`http://127.0.0.1:11434`, `ollama`), the roles `summary`, `describe` → `qwen3:8b` with a context of 8192 and `embed` → `bge-m3` (the server's context; semantic search in KRunner).

```json
{
  "default": "local",
  "endpoints": {
    "local": {"url": "http://127.0.0.1:11434", "kind": "ollama"},
    "mac":   {"url": "http://mac-studio:11434", "kind": "ollama", "tokenFile": "~/.config/tide/mac.token"},
    "lm":    {"url": "http://mac-studio:1234", "kind": "openai"}
  },
  "roles": {
    "summary":  "local:qwen3:8b",
    "describe": {"model": "local:qwen3:8b", "ctx": 8192},
    "embed":    "local:bge-m3",
    "agent":    "local:qwen3-coder:30b",
    "polish":   "local:hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M"
  },
  "agentProgram": "codex",
  "semanticSearch": true
}
```

- `kind`: `ollama` (the default) or `openai` (MLX, LM Studio, llama.cpp — any server with `/v1/chat/completions`); the address without `/v1` (given with `/v1` it also works).
- A role is `"endpoint:model"` or `{"model": "endpoint:model", "ctx": N}`. Model names contain colons, so the string is split only at the first one — and only when a known endpoint's name precedes it. Otherwise the whole string is a model on the `default` endpoint (`"qwen3:8b"` = `default:qwen3:8b`). The missing roles `summary`, `describe`, and `embed` are added on the `default` endpoint, so switching to the Mac is a change of `default` or of the prefix in the roles.
- `ctx` is the context (`num_ctx` in Ollama). The `summary` and `describe` roles without `ctx` get 8192: with 16k an 8B model does not fit on an 8 GB card and the response takes 165 s instead of 8 s (PT-13 measurements). Other roles without `ctx` and `ctx: 0` — the server's context (16k in the Ollama service overlay, for agents).
- The `classify` role (PT-42, default `qwen3:8b` on `default`) judges doubtful endings of agent turns (the section on the `waiting/reply` state). As the only role it may have an Ollama cloud model (`…:cloud`, `…-cloud`), but only with an explicit endpoint (`"classify": "endpoint:gpt-oss:120b-cloud"`); without an endpoint the role is unavailable like any other with a cloud model.
- **The `classify` role in the "Modele" view and in saving** (PT-112, user story 11). The view shows it next to `summary` and `describe` under the name "Rozpoznawanie pytań" (Recognizing questions), with the same model choice, the "Zaawansowane" (Advanced) options (pinning to a server), and `SetRole`; saving and `Status().roles.classify` work like for the other roles (the same code in `models/settings.py`, no change to the judging logic). An old `models.json` without a `classify` key gives the default value from `DEFAULT_ROLES` (`qwen3:8b` on `default`, locally). Changing the role's model works without a restart: judging reads `models.json` on every call, so the next judgment uses the new model. The view says explicitly: when the role has no model — "ocena wyłączona — liczą reguły" (judging disabled — rules apply); when the model is in the cloud — the tag "☁ w chmurze" (in the cloud) (the tail of the agent's last message leaves the computer). The model list of this role also shows cloud models (with "☁"), the other roles do not see them. Choosing a cloud model is an explicit user action, and the view sends `SetRole` with the role's server (the service refuses without an explicit server); no default or fallback choice points to the cloud (decision 70).
- `agentProgram`: the program in which the agent on a local model from the "Nowe zadanie" window runs — `codex` (the default; Ollama supports it directly) or `claude` (Claude Code: without claude.ai connectors, it copes worse with a small context). The program is not the model: the model comes from the `agent` role, the program from this key, one for all servers, so switching to the Mac does not change it (PT-39, decision 72). An unknown value (e.g. `"Claude"`) — the warning "agentProgram: nieznany program "Claude" (codex albo claude) — agent lokalny działa w Codexie" (agentProgram: unknown program "Claude" (codex or claude) — the local agent runs in Codex) and Codex; the rest of the file works.
- `semanticSearch`: semantic search in KRunner (PT-19) — `true` or `false`; a missing key — enabled (it works when the `embed` role's model is on the server). A bad value — the warning "semanticSearch: oczekiwano true albo false, jest "nie" — wyszukiwanie po znaczeniu włączone" (semanticSearch: expected true or false, got "nie" — semantic search enabled). A top-level key, like `agentProgram`: switching to the Mac does not change it.
- `tokenFile` (an absolute path or one with `~`; a relative one — the server is skipped with a warning, because it would depend on the directory the service started from): a file with a token, sent as `Authorization: Bearer …` — to the Mac through a proxy with a token (PT-21). Read on every request, in memory only; it does not go into the journal, messages, or `Status()`.
- A broken file (not JSON or not an object) → default settings and `config.error`; a bad endpoint or a bad role is skipped with a warning in `config.warnings`, the rest works. An unknown, skipped, or non-string `default` does not move roles without an endpoint to another server: those roles (`summary` and `describe` too) are unavailable with a reason in `roles.*.error`, and the `default` in the state is a fallback `local` (or the first endpoint) — for reading the state only; roles with a valid endpoint prefix work. No file shape stops the service or `--print-models`.
- A bad endpoint is: an address other than `http(s)://`, an address that cannot be split into a host and a port (a non-numeric port, 0 or above 65535, IPv6 without brackets — correctly `http://[fd7a:115c::1]:11434`), an address with credentials (`http://user:password@…` — the password would end up in the state; that is what `tokenFile` is for), an unknown `kind`.
- A role with the prefix of a skipped endpoint (`"agent": "mac:qwen3-coder:30b"` when `mac` has a bad address) does not silently move to `default`: it stays with `mac` as unavailable (`roles.agent.error`, a warning), and a call of this role ends with `ModelError`.

**Saving settings from the Tide window** (PT-25, `tide_daemon/models/settings.py`). Settings are changed in the Tide window (the "Modele" view, PT-26) — without editing files; switching to the Mac is `SetDefault("mac")`. Every write method answers `{"ok": true, "error": ""}` or `{"ok": false, "error": "<message in Polish>"}`; after a successful write `Status()` immediately shows the new settings (an endpoint that is new or changed by `SetEndpoint` — also just its token — is "not checked": `online: false` without `error`), `Changed` arrives, and the server read starts right after it (a second `Changed` when the state changes). We write only on a D-Bus call — never at service start.

- **The word "server".** User-facing messages (write refusals, `config.warnings` warnings, role errors, and `ModelError`) say "serwer" (server), like the "Modele" view — in code and JSON `endpoint`/`endpoints` stay, and in this description "endpoint".
- **One validation.** An endpoint and a role go through the same code as reading the file (`config.check_endpoint`, `config.check_role`), so a refusal has the same message as a warning in `config.warnings`, without the suffix "— pominięty" (— skipped), e.g. "serwer "lm": nieznany rodzaj "llamacpp" (ollama albo openai)" (server "lm": unknown kind "llamacpp" (ollama or openai)). Finally the whole file after the change is read once more: a change that would produce a new warning is not saved; earlier warnings from a manually edited file do not block a write (and stay in the file). An address and a model name — at most 512 characters, a token — 4096; values entered are truncated in messages.
- **The file.** An atomic write (a temporary file in the same directory, `fsync`, `rename`, a directory `fsync`), `models.json` always 0600, unknown keys and the other settings stay (the file is rewritten with an indent of 2), a symbolic link stays a link. Without `models.json` the first write starts from the defaults (`default: local`, the `local` endpoint, the roles `summary` and `describe` → `qwen3:8b` without an endpoint), so it changes nothing except the change itself; a missing `default` in the file is written explicitly (the fallback endpoint will not shift after another one is added). `null` in `endpoints` or `roles` is a missing key, as on read. A file we cannot read (not JSON, not an object, `endpoints` or `roles` of a kind other than an object) we do not overwrite: a refusal asking to fix or delete it. Writes are serialized by a lock.
- **`SetEndpoint`** creates or changes an endpoint (renaming is a new endpoint and removing the old one). A new endpoint must not change the meaning of existing roles: an endpoint `qwen3` would make a role without an endpoint `"qwen3:8b"` read as the endpoint `qwen3` and the model `8b` — the refusal "nazwa "qwen3" zmieniłaby role describe, summary (model "qwen3:8b" czytany jako serwer "qwen3") — wybierz inną nazwę serwera" (the name "qwen3" would change the roles describe, summary (the model "qwen3:8b" read as server "qwen3") — choose another server name) (pinned roles, `"local:qwen3:8b"`, are immune). Changing the address or kind of an endpoint on which a model pull is in progress, and removing it — the refusal "na serwerze "local" trwa pobieranie modelu qwen3:8b — poczekaj, aż się skończy" (a pull of model qwen3:8b is in progress on server "local" — wait until it finishes). `kind` without a value — the existing one or `ollama`. The token (`token`) goes only to the file `tokens/<endpoint>` next to `models.json` (`$XDG_CONFIG_HOME/tide/tokens/`, directory 0700, file 0600), and into `models.json` as `tokenFile` (a full path). An empty or missing `token` — the token stays (also someone else's `tokenFile`), `clearToken: true` — the endpoint without a token (our `tokens/<endpoint>` file disappears); both at once — a refusal. The token must be printable ASCII without spaces. It never comes back in a response, message, or journal; when `models.json` cannot be written, the token file goes back to its previous content.
- **`RemoveEndpoint`** refuses for the default endpoint ("najpierw ustaw inny serwer jako domyślny" (first set another server as the default)), the only valid one, and one to which roles are pinned ("serwer "mac" jest używany przez role: agent, embed — przepnij je na inny serwer albo na domyślny, zanim go usuniesz" (server "mac" is used by the roles: agent, embed — move them to another server or to the default before you remove it)). We chose a refusal instead of roles with an error: a role `"mac:model"` after removing `mac` would already be a model name on the `default` endpoint — it would silently go to another server. Our token file of the removed endpoint disappears.
- **`SetDefault`** — only a valid endpoint ("default: nieznany serwer "x"" (default: unknown server "x"), "default: serwer "x" jest pominięty w models.json — popraw go najpierw" (default: server "x" is skipped in models.json — fix it first)).
- **`SetRole`**: `model` — the model name without an endpoint prefix; `endpoint` empty or missing — the role is saved without an endpoint (`"agent": "qwen3-coder:30b"`), so it follows `default` and switching the default endpoint moves it; an endpoint name — a pinned role (`"local:qwen3:8b"`), it stays with it. A model whose name starts with an endpoint name and a colon (`local:x`), without `endpoint` — a refusal ("wybierz serwer jawnie" (choose the server explicitly)), because the file would read it as pinned. `ctx`: a missing key — no change, `null` — the default (8192 for `summary` and `describe`, otherwise the server's context; it disappears from the file), a number 0…1048576 (0 — the server's context). A role may have any valid name; `summary`, `describe`, `agent` (the agent session model, PT-17), and `embed` (semantic search in KRunner, PT-19) are the ones Tide uses.
- **`SetAgentProgram`** saves `agentProgram` (`codex` or `claude`) — a top-level key, independent of the `agent` role, so it can be set even before choosing a model; other writes do not touch it. Any other value — a refusal with the same message as the warning on read ("agentProgram: nieznany program "opencode" (codex albo claude)" (agentProgram: unknown program "opencode" (codex or claude))).
- **`SetSemanticSearch`** saves `semanticSearch` (`on` → `true`, `off` → `false`) and wakes the KRunner index: disabling interrupts the batch waiting in the queue and deletes the vector file right away (a service with `--no-krunner` deletes it itself). Any other value — the refusal "semanticSearch: oczekiwano on albo off, jest "tak"" (semanticSearch: expected on or off, got "tak"). Every other successful write also wakes the index (e.g. a different model of the `embed` role — the index starts anew).
- **`SetReportSummary`** saves `summary` in `$XDG_CONFIG_HOME/tide/report.json` (other keys and the file's permissions stay; without the file — a new one with `summary` alone) and immediately switches the report and the card descriptions in the running service, without a restart. The exception: a service started with `--report-summary` — then only the file changes, and `report.pinned` is `true`. An unknown mode — a refusal with a message as on read ("raport: summary musi być jednym z claude, local, rules, jest 'Local'" (report: summary must be one of claude, local, rules, got 'Local')).
- **`TestEndpoint`** — a single read without saving: Ollama `GET /api/version` and `/api/tags`, an `openai` server — `GET /v1/models`, 3 s per request. `{name}` checks a saved endpoint; `{name, url?, kind?, token?}` — a saved endpoint with fields replaced (editing before saving; without a new token the saved one is used); `{url, kind?, token?}` — a new endpoint. A token given here exists only in the memory of this trial. At most 4 trials at once — the next one immediately gets "za dużo prób połączenia naraz — spróbuj za chwilę" (too many connection tests at once — try in a moment). The D-Bus response comes after the read (a separate pool, the service loop does not wait); the result `{ok, error, version, models}` — `models` is the number of models on the server, `version` is `null` for `openai`, on an error `error` is the `ModelError` message, and `version` and `models` are `null`.
- **`PullModel`** — only an `ollama` endpoint (an OpenAI-compatible server pulls models itself), a model name of letters, digits, and `._:/@+-`. Immediately `{ok, error, key}` (`key` = `"<endpoint>/<model>"`), the pull itself (`POST /api/pull` with `stream: true`) runs in a separate thread. One pull at a time per endpoint ("na serwerze "local" trwa już pobieranie qwen3:8b — poczekaj, aż się skończy" (a pull of qwen3:8b is already in progress on server "local" — wait until it finishes)). Progress in `Status().pulls`, `Changed` at most once a second (the end — right away). No deadline for the whole (a large model takes up to an hour), but with a silence limit of 300 s and a single 10 s deadline for the connection and response headers; shutting down the service interrupts the pull right away (`models.Cancel`). After success a state read (the new model in the list). There is no `DeleteModel`.

**Executor catalog (PT-60).** An executor is a program (`claude` or `codex`) with a model and an endpoint under one name; the catalog lives in `models.json` (the keys `executors` — a list of `{name, program, model, provider, endpoint, classes, effort}` — `effort` is Codex's optional reasoning effort (`low`, `medium`, `high`, `xhigh`; empty for Claude) — and `providers` — `{label, url, api, tokenFile}`), backward compatible: without `executors` the default catalog works (Haiku, Sonnet, Opus, Codex light / standard / strong, Codex Astra without classes, Local), and the first write from the window saves the whole catalog. A provider is `anthropic`, `openai`, `local`, or another one from `providers`; the Anthropic API supports only the `claude` program, the OpenAI API only `codex`, and local both. A local executor with an empty `model`, `program`, and `endpoint` follows the `agent` role and `agentProgram`. Another provider's token lies in `tokens/providers/<name>` (0600, a separate subdirectory, so it does not collide with an endpoint's token; the endpoint name `providers` is reserved); in `models.json` there is only the path, in `Status()` only `auth`, never in D-Bus responses, journals, or agent commands (decision 70). `Status()` additionally has `executors` (a list with `where` = `local`/`cloud`, `available` for local ones, `error`), `providers`, and `catalog` (`stored`, `warnings`). The module `models/executors.py` (no Qt and no network): `resolve(ctx, provider, class)` turns the preferred provider (`anthropic`, `openai`, `local` — local only, `mixed`, or the name of another provider) and the class (`lekki` (light), `standard`, `mocny` (strong), `lokalny` (local)) into an executor according to the table from the specification; in mixed mode the result is a hint (`suggested`) with a `candidates` list from the whole catalog. `reviewer(ctx, provider, class, author)` gives a reviewer of a different model than the author (the same model with a different effort is the same model), in mixed mode at a different provider. Local work never goes to the cloud: the class `lokalny`, "tylko lokalnie" (local only), and a local author give a local executor, and when its server does not respond or the model is not on it, the result is `ok: false, local: true` with the message "… Praca lokalna nie przejdzie do chmury." (… Local work will not go to the cloud.) — with no replacement. `StartCard` resolves the executor from the catalog before writing the journal, and the assignment window filters the "Od razu" list by the chosen provider. `RenderLaunch` passes the preference to the planner. Converting a class to an executor and choosing a reviewer remain available catalog functions and the `ResolveExecutor` method.

**State.** Every 15 s (`--models-poll`) in a worker thread, with a limit of 3 s for the whole request, so a server that hangs, trickles bytes, or does not exist blocks neither D-Bus nor the subsequent reads. A missed error at one endpoint gives that endpoint `online: false` ("błąd odczytu stanu" (state read error)), and the others read normally. For Ollama `GET /api/version`, `/api/tags`, and `/api/ps` (none loads a model or extends how long it is held in memory), for `openai` — `GET /v1/models`. An endpoint that does not respond to the first request does not get the following ones. The JSON has no read time, so `Changed` arrives only on a real change (a server came up or went down, a model was loaded or released, a new model, a settings change). Only changes go into the service journal: "Modele lokalne: local: odpowiada (wersja 0.40.11)" (Local models: local: responding (version 0.40.11)), the reason for unavailability, errors, and warnings from `models.json`.

**Model call** (`tide_daemon/models/client.py`, no Qt, for use in a worker thread): `chat(prompt, role="summary", system=…, schema=…, timeout=180)` or `chat(…, endpoint="mac", model="qwen3:8b")`. The result is text with the endpoint, address (`url`), model, and context (`ctx`) that actually answered, with metrics (`promptTokens`, `outputTokens`, `wallS` — the call itself, `queueS` — waiting in the queue, and from Ollama also `loadS`, `promptS`, `outputS`) and `json()` for a response according to a schema. `cancel=models.Cancel()` lets you interrupt a call from another thread (service shutdown): `set()` closes the socket of the request in progress and blocks new ones — "Model lokalny: wywołanie przerwane — usługa się zamyka" (Local model: call interrupted — the service is shutting down). The adapter by `kind`:

- `ollama` — native `POST /api/chat` with `stream: false`, `think: false`, `options.num_ctx` from the role, and `format` = a JSON schema. Not the OpenAI-compatible `/v1/chat/completions` of Ollama, because that one skips `num_ctx`, and the context decides whether the model fits on the card. A model without a thinking switch (a 400 response mentioning "think") — a retry without `think`.
- `openai` — `POST /v1/chat/completions` with `response_format: {type: "json_schema", json_schema: {name, strict, schema}}`; the server sets the context.

**Queue** (`tide_daemon/models/queue.py`, PT-16). Ollama on this computer serves one request at a time (`OLLAMA_NUM_PARALLEL=1`, an 8 GB card, one model in memory). Without a queue the report and the card descriptions waited for each other inside the server, and the wait time counted toward the call limit — a card got "did not respond in 90 s" even though the model was merely busy with the report. The queue sits in `chat()`, so every model call in the service goes through it, future ones too (PT-17, PT-19) — without having to remember it in every place. One per process, a separate lane for each server — by the normalized `scheme://host:port` address (`queue.lane_of`), so two endpoint names for the same server share one place:
- at most one call at a time on a server (the number of places per lane is in the code — `Queue(slots=…)`; a setting for the Mac later), different servers work in parallel;
- order: priority (`queue.HIGH` — card descriptions the user is looking at and the KRunner semantic query; `queue.NORMAL` — the default; `queue.LOW` — the report; `queue.IDLE` — the semantic search index, when nothing else is waiting), and within it the order of arrival. The wait limit in the queue ends with an exception `queue.Busy` (a subclass of `ModelError`) — the caller tells a busy server from one that is not working. The default priority follows from the role (`describe` high, `summary` low, the rest normal), the report and the cards also pass it explicitly. Nothing interrupts a call in progress;
- model grouping: within the same priority, a call with the same model the server last worked with has precedence (without shuffling models between the card and memory), as long as the oldest waiting call does not wait longer than 60 s. Priority matters more than grouping: a card with a different model still goes ahead of the report (with the default roles both are `qwen3:8b`, so nothing gets reloaded);
- time: `timeout` counts from the moment the call actually started; waiting has its own limit `wait` (default 600 s; cards 300 s), and `deadline` (`time.monotonic()`) closes both together — this is how the report holds 600 s for the whole including the wait. After the wait limit, a `ModelError` with an honest reason: "Model lokalny: kolejka: czekał 300 s — serwer zajęty: raport poranny (127.0.0.1:11434)" (Local model: queue: waited 300 s — server busy: morning report (127.0.0.1:11434));
- the next one is chosen by one place, under a lock, on every state change (joining, start, end, leaving the queue), and the choice is recorded; waiters compare themselves with the recorded choice, and a change of the choice wakes everyone. When grouping holds back the oldest waiting call, the waiters sleep at most until the end of the grouping window and the choice is computed anew (PT-16 review: without this, at the 60 s boundary two waiters could decide that the other one is next and sleep with a free place until the wait limit);
- `chat()` determines the target (the endpoint, address, model, context) at the moment the call gets its turn — `models.json` is read then, unless the caller gave `config`. After a settings change during the wait (a different `default` or address, e.g. from the Tide window) the call goes to the new server, and when that is a different lane — it moves to its queue keeping its order of arrival, join time (`since`), and wait limit. The report pins the settings: one pass = one set of settings (`summarize.Model(pin=True)` gives `chat()` the configuration from the start of the pass), so the footer ("nic nie wychodzi z tego komputera" (nothing leaves this computer)), the `summary.json` memory, and the trimming to the context agree with where the facts actually went. Card descriptions do not pin: `chat(expect=…)` gets the endpoint, address, model, and context assumed at the start of the batch and compares them with the target from the settings at the moment of the start — on a difference the call does not go to the server (`TargetChanged`), so the facts do not reach a server the batch did not assume, and nothing is stored in memory under the wrong identity (we check the response, an `Answer` with the endpoint, address, model, and context, once more). The card has rules and "Model lokalny niedostępny — opis bez modelu (ustawienia modeli zmieniły się w trakcie wywołania)" (Local model unavailable — description without a model (the model settings changed during the call)), and the next batch (without a 15 min pause after an error) already takes the new settings;
- a lane is an address without name resolution: `http://localhost:11434` and `http://127.0.0.1:11434` are two separate lanes (two places on the same server) — in `models.json` it is better to keep one spelling of the address;
- starvation: the report (`LOW`) waits as long as someone with a higher priority is in the queue, but in practice that is at most one batch of cards at a time (card descriptions go from one thread, one call at a time, with gaps), so the report starts right after it; if anything, the 600 s budget holds it;
- `cancel` (`models.Cancel`) removes a waiting call from the queue right away (without reaching the server), and a call in progress loses its socket as before — shutting down the service does not wait for a turn;
- the queue has no threads of its own: a call waits and runs in the caller's thread (the report thread, the card description thread), and the place is also released after an error.
Outside the queue: the state read (`/api/version`, `/api/tags`, `/api/ps` — short GETs that do not load a model), model pulls (`PullModel` from PT-25 — a long transfer to disk, no GPU), and agent sessions with a local model (Codex `--oss`, Claude Code through `ANTHROPIC_BASE_URL` — they talk to the server directly, and Ollama queues them itself; a Tide call may then wait in the server and get the ordinary "did not respond in N s"). For later: heavy batch work (the report) could wait for the user to be idle (`idle.py`) — today the report starts at its own time.

**Ollama cloud models.** Ollama has models in the cloud (`kimi-k2:cloud`, `gpt-oss:120b-cloud` — the tag after the last ":" ends in `cloud` after ":" or "-", regardless of letter case): after `pull` they are in `/api/tags` like local ones, but the local server only forwards the call to ollama.com. Local mode does not use them — otherwise the report's facts would leave the computer, while the footer would say "nothing leaves this computer". One place recognizes such a model (`tide_daemon/modelnames.py`: `is_cloud_model`, shared with the agents from PT-17; in windows and the tile the same rule is in `widgets/lib/modelnames.js`): a role with it is unavailable (`roles.*.available: false`, `error`: "model "gpt-oss:120b-cloud" działa w chmurze Ollamy, nie lokalnie — wybierz model lokalny w Tide → Modele" (model "gpt-oss:120b-cloud" runs in the Ollama cloud, not locally — choose a local model in Tide → Models), a warning in `config.warnings`), `chat()` also refuses an explicit model and checks this once more at the start of the call (settings changed during the wait), so the report and the cards get rules with this information, and nothing goes out to the server; `SetRole` refuses with the same message; in `Status()` such models have `cloud: true`. The service accepts a pull (`PullModel`) — the window warns.

**When the server is not working.** Every error — the server does not respond, no such name, a timeout, an HTTP error (with the server's error text, e.g. "model 'x' not found"), an unreadable or empty response, a redirect, an unreadable token file (the token must be printable ASCII), a role on a skipped endpoint — is a single `ModelError` exception with a short message in Polish to show to the user. Local mode never goes to the cloud: the client knows neither Claude nor any fallback call, and the caller shows a message instead of a description (the decision from 27.10 in the plan). Requests go without intermediaries from `$http_proxy`. The timeout (the call's `timeout`, 3 s for the state) covers the whole request: name lookup, connecting, sending, and the whole response read — after the deadline the connection is closed. We do not follow redirects (3xx): "Model lokalny: serwer odpowiedział 302 — przekierowanie nie jest obsługiwane" (Local model: the server responded 302 — a redirect is not supported), because a redirect would take the token to another host, and a POST after a 302 would become a GET.

The logic without Qt is in `tide_daemon/models/` (`config.py`, `transport.py`, `client.py`, `queue.py` — the call queue, `status.py`, `settings.py` — saving settings, `pull.py` — pulling), the polling, saving, pulling, and the `queue` field in threads — in `models_monitor.py`. Embeddings (`models.embed`, PT-19) — in `client.py` next to `chat()`, with the same queue. Rationale: decisions 60, 66, 67, 72, and 75 in `docs/decisions.md`.

```bash
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.Status
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.Refresh
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.TestEndpoint '{"url": "http://127.0.0.1:11434", "kind": "ollama"}'
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.SetDefault mac      # switch roles without an endpoint to the Mac
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.SetAgentProgram claude   # local agent in Claude Code
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.SetSemanticSearch off     # KRunner without semantic search
/usr/bin/python3 daemon/tide-daemon.py --print-models   # a single state read, GET only, writes nothing
```

## Activity look

After every activity change the service applies the whole look of the target activity (PT-35, decision 78), publishes the look of the current activity for widgets and filters notifications in Stream (PT-36, decision 80). The service listens by itself:

| Element | Value |
| --- | --- |
| Service | `org.kde.ActivityManager` |
| Object | `/ActivityManager/Activities` |
| Signal | `org.kde.ActivityManager.Activities.CurrentActivityChanged(s activity)` |

Every way of switching (shortcut, `Meta+Q`, widget, KDE Connect) ends with this signal. `tide_daemon/activity_look_monitor.py` then starts the engine in a separate process: `python3 -s $XDG_DATA_HOME/tide/activity-look/scripts/activity_look.py apply --activity ID --source daemon` (a copy installed by `make install-activity-look`). The engine compares the color scheme, the Plasma style, the wallpaper of that activity's desktops, the lock screen variant and the panel layout with the description in `theme/activities/<look>.json` and applies only the differences, in that order. The `stream` look goes to the activity assigned in `$XDG_CONFIG_HOME/tide/activities.json`, every other activity gets `work`.

- **Queue.** One apply at a time. Events from a 400 ms window and those that arrive during an apply are merged into one: the last activity wins, with no parallel applies. Installers (`make install-*`) wait for the service and vice versa (`flock` on `$XDG_STATE_HOME/tide/activity-look.lock`).
- **Service start (login).** `apply` without an activity: the engine asks for the current one, compares and repairs the differences. This also finishes a switch interrupted before logout (the journal `$XDG_STATE_HOME/tide/activity-look.json` with state `applying`). **Settle delay (PT-495):** the service watches `org.kde.plasmashell` on the session bus, and the first apply after plasmashell registers starts only after `SHELL_SETTLE_MS = 5000` (5 s). When plasmashell is not registered, the apply at start waits for the registration and the delay; when it already is, it starts after the delay alone, counted from the service start. When plasmashell unregisters, launching is held back (an apply in progress is not killed), and re-registration re-arms the delay, after which the pending request starts. Exception: with a stored circuit breaker (below) the start does not apply anything.
- **Engine exit codes.** 0 — matching or applied; 1 — differences (`check`) or an error; 75 — plasmashell or ActivityManager does not answer yet (the D-Bus service is missing: "does not exist", "ServiceUnknown", "Cannot find", "not activatable"; also every unknown `qdbus6` error to ActivityManager); 76 — plasmashell interrupted the call (NoReply, Disconnected, "Remote peer disconnected", "Message recipient disconnected" and every other `qdbus6` error to `org.kde.plasmashell` other than "service missing"; PT-494). Losing plasmashell is a separate engine exception (`PlasmashellLost`), never tolerated: even in the work look it aborts the whole apply at once (later components are not called), the `activity-look.json` journal gets `state: failed` and the error "plasmashell przerwał wywołanie …" (plasmashell interrupted the call …), and `check` returns 76 with precedence over 75 and 1. The service reads code 76 as turning on the circuit breaker (the "Errors" item).
- **Errors.** Code 75 — the D-Bus service is not there yet — means a retry after 2 s, then twice as long, up to 60 s (`RETRY_MS`), with no limit on attempts; it does not count toward the failure threshold and does not reset its counter (a normal login produces several 75 codes within a dozen or so seconds). Any other error (e.g. "wariant niezainstalowany" (variant not installed)) goes to the service log and waits for the next event. The engine output is in `journalctl --user -u tide-daemon` with the prefix "wygląd aktywności:" (activity look:).
  - **Circuit breaker (PT-495).** Protects against a Plasma crash loop (the engine calls plasmashell, it crashes and restarts, and its start wakes the service again). **It turns on** after code 76 or after `FAILURE_LIMIT = 3` exits with a code other than 0 and 75 within a `FAILURE_WINDOW = 120 s` window (counted from the first failure in the window; code 0 resets the counter). **When on:** it does not arm a retry, clears the pending request and ignores the apply at start, retries and plasmashell appearing.
  - **Where to see it.** In the service log (`journalctl --user -u tide-daemon`): "wygląd aktywności: wyłącznik awaryjny — <powód>; do następnej zmiany aktywności" (activity look: circuit breaker — <reason>; until the next activity change); in `$XDG_STATE_HOME/tide/activity-look.json` under the `breaker` key (`reason`, `since`, `code`, `failures`; the other engine keys stay, the write is atomic, and the monitor writes only when the engine is not running); in `make activity-look-status` (the journal of the last switch).
  - **How to clear it.** An activity change (`CurrentActivityChanged`) resets the counter, removes `breaker` from the journal and queues an apply of the new activity. Or a manual full `apply` of the engine (it rewrites the journal without `breaker`). A new service process reads `breaker` from the journal and, with a stored breaker, does not apply at start: neither a service restart nor D-Bus activation clears it.
- **Work and Stream.** In the work look, an error in one component does not block the others (errors at the end, code 1); Stream is checked as a whole before the first write. A late event for an activity that is no longer current changes only its desktops; the shared components stay with the current one.
- **Panels and desktops without a view.** Reading panels (`check`, `stream-read.js`, `dump.js`) and wallpaper skips panels and desktops without a view: a panel with a `screen` outside `0..screenCount-1` (e.g. `lastScreen=1` with DP-2 turned off) gets `orphan: true` in the read, with no view settings, and a desktop without a screen gets `geometry: null`; look comparisons do not count them, and the report says "bez widoku" (without a view). Touching the view properties of such a panel or the geometry of a nonexistent screen crashes plasmashell. Tests: the plasmashell fake (`scripts/tests/fake_plasmashell.js`) throws an exception for nine view properties (`hiding`, `height`, `minimumLength`, `maximumLength`, `alignment`, `lengthMode`, `floating`, `offset`, `opacity`) of such a panel and for `screenGeometry` outside `screenCount`, and `desktop_like_15_11()` yields a state with panels 103 and 118 on `screen=1`. `scripts/tests/test_panels_js.py` runs every script from `scripts/panels/` (the list comes from the directory, without `common.js`) on this state; the test has no exception list, so a new script without the guard fails it at once. **Install scripts** (`sidebar.js`, `install.js`, `dock.js`; `make install-panels`, `install-sidebar`, `install-dock`) have the same guard (`tidePanelHasView` from `common.js`): they skip a panel without a view, neither read nor write its view properties, and report its identifier in the `orphans` list in the `TIDE_JSON` result (the list is always present, even when empty). A sidebar or a slim bar of the left monitor without a view, found by role, does not cause a `new Panel(...)` of the same role, and `tideTopReserved` and the dock choice count only panels with a view. The installers (`panels.py`, `sidebar.py`, `dock.py`, the `install` path) read `orphans` and print a note, e.g. "Panele bez widoku pominięte: 103, 118" (Panels without a view skipped: 103, 118); the result of an old script without the `orphans` key or with an empty list gives no note and is not an error. **Ordering and uninstalling** (`order.js`, `uninstall.js`, `dock-uninstall.js`, `sidebar-uninstall.js`; `make uninstall-panels`, `uninstall-sidebar`, `uninstall-dock`) have the same guard: `order.js` skips a panel without a view entirely (sets neither its thickness nor its order), `uninstall.js` and `dock-uninstall.js` do not restore its view properties (a `panels-previous.json` dump with an `orphan` panel without view keys is accepted, and `height`/`floating` from an entry without those keys are not written). The main bar or dock without a view is not restored as a whole: the result has `skipped`, and `panels.py`/`dock.py uninstall` keep `panels-previous.json` and `*-installed.json` and print "włącz monitor i ponów uninstall" (turn on the monitor and repeat uninstall) instead of "przywrócono" (restored) (the Tide dock widgets disappear at once, the rest returns on the repeat), and all four report it in `orphans` (the list is always present, even when empty). Removing Tide panels and widgets keeps working for panels without a view too: a sidebar without a view disappears as well. The installers in the `uninstall` path print the same note as on install. **Stream** (`stream.js`, `stream-leave.js`, `stream-console.js`; PT-497) has the same guard. Entering (`stream.js`) neither narrows nor hides a sidebar without a view and does not create a replacement Stream dock when the only Stream dock has no view; both are in `orphans`. No bar value (hiding, height, lengths) then goes into `stream-panels.json`, and the step entry says there is nothing to save. `stream-console.js` counts the space taken at the top and bottom (`tideReserved`) only from panels with a view. Leaving (`stream-leave.js`) removes the Stream dock even without a view (`remove()` does not touch the view), and leaves a sidebar without a view alone and reports it in `orphans`. When `orphans` contains a bar that `stream-panels.json` concerns, `leave_stream_panels` (`scripts/activity_look.py`) does not delete that file (the pre-Stream values will return when the bar's view is restored) and the step entry gets the note "przywrócenie paska bocznego … czeka na widok paska" (restoring the sidebar … is waiting for the bar's view). `check` does not treat such a record as stale. With a bar that has a view, the file is deleted as before.
- **One monitor (output layout guard, PT-493).** When an output from the Tide layout, either the main `TIDE_MAIN_OUTPUT` (default DP-2) or the side `TIDE_SIDE_OUTPUT` (default HDMI-A-1), is connected but turned off, the `panels` step does not run any plasmashell script. The disabled state is read from `kscreen-doctor -j` (`activity_panels.disabled_outputs`, a pure function with the read function passed as an argument: `Reader` with an allowlist in the engine, `run` in the installers), so reading does not call plasmashell. `check` and `apply` show the step as "pominięte" (skipped) with the note "wyłączone wyjście z układu Tide: DP-2 — panele pominięte" (output from the Tide layout turned off: DP-2 — panels skipped), write nothing and finish without an error (the rest of the look — color scheme, style, wallpaper, lock screen — is applied as usual). `make install-panels`, `install-sidebar` and `install-dock` (also `uninstall-*`) print "Panele pominięte: wyłączone wyjście DP-2 (włącz monitor i uruchom ponownie)" (Panels skipped: output DP-2 turned off (turn on the monitor and run again)), finish with code 0 so that `make install` goes on, and take neither a panel dump (`panels-previous.json`) nor an install state record (`*-installed.json`). A disconnected connector (a new machine with one monitor) does not bother the panels. **Return:** turn on the monitor and repeat `make install-panels install-sidebar install-dock` (or switch the activity — the engine will check the layout anew). A `kscreen-doctor` error or bad JSON gives the note "nie udało się odczytać układu wyjść" (could not read the output layout) and normal operation; the guards in the panel scripts then protect. Tests: `scripts/tests/test_panel_output_guard.py` (the function on three layouts, installers with the `fake_kscreen_doctor.py` fake in `PATH`), `test_activity_look.py` (the `panels` step without a plasmashell call); every installer test has a `kscreen-doctor` fake in `PATH` (`fake_plasmashell.link_kscreen_doctor`).
- **Engine copy.** The service checks that the copy exists on every start (missing: a message once, the request is dropped until the next event). Installers refresh a stale copy by themselves, `make activity-look-check` and `make activity-look-status` report it. Stopping the service kills an apply in progress; the next start finishes it.
- **Disabling.** `--no-activity-look` or an empty `$TIDE_ACTIVITY_LOOK_ENGINE` (the service tests do this). Without an installed engine the service only reports that. Without a running service, an activity switch does not change the look until its next start.

```bash
make activity-look-check     # desktop vs. description of the current activity, read-only (code 1 on differences)
make activity-look-status    # activity assignments, available looks, journal of the last switch
```

### Look of the current activity for widgets (/ActivityLook)

Plasma panels are shared by all activities, so the work tiles in Stream hide themselves
(`Plasmoid.status = HiddenStatus`, `widgets/lib/ActivityLook.qml`). The service tells them whether it is Stream
(`tide_daemon/activity_state.py`): the current activity comes from `CurrentActivity()` at start and from the
`CurrentActivityChanged` signal, its look from `$XDG_CONFIG_HOME/tide/activities.json` (read on every change, so
`make install-stream-activity` works without a service restart), the description from `$TIDE_ACTIVITY_LOOKS` (default
`$XDG_DATA_HOME/tide/activity-look/theme/activities`, the engine copy). Without a description or an assignment — work.

| Element | Value |
| --- | --- |
| Object | `/ActivityLook` |
| Interface | `org.tide.Daemon.ActivityLook` |
| `State() -> s` | JSON `{"activity", "look", "workPanels", "notificationsFiltered"}`; `workPanels` is false when the look description has `"panels"` other than `"work"` (Stream) |
| `Refresh()` | re-reads assignments and descriptions (called by `make uninstall-activity-look` after removing the engine copy — the service then returns to work) |
| signal `Changed()` | the current activity or its look changed |

Without the service (or before its first answer) widgets stay on work — the work tiles are visible.

### Stream notification filter

When the look description has `notifications.allow` (`stream.json`: applications `org.kde.kdeconnect.daemon`, services `kdeconnect`
and `streamer`), the service:

1. calls `org.freedesktop.Notifications.Inhibit("tide", "Aktywność Stream", {})` ("Aktywność Stream" = Stream activity) and holds the cookie — this is the same
   "Do Not Disturb" state as from the notifications applet: only critical ones (`CriticalInDndMode`, on by default)
   and allowed ones pop up, the rest go to history;
2. sets `ShowPopupsInDndMode=true` in `plasmanotifyrc` under `[Applications][<desktop entry>]` and
   `[Services][<x-kde-appname>]` of the allowed ones (`kwriteconfig6 --notify`, Plasma reads the change at once). The previous
   values — also "no key" — are saved beforehand in `$XDG_STATE_HOME/tide/stream-notifications.json`.

On leaving Stream and on stopping the service the values return exactly (the raw string from before Stream, a missing
key is deleted, the file returns byte for byte; the record disappears after a verification read), then `UnInhibit(cookie)`.
The inhibition lives as long as the notification server: the service watches the name `org.freedesktop.Notifications` — after a restart of
plasmashell it asks for a new one and checks the exceptions, and a failed `Inhibit` is retried every 2 s, ever less often, up to 60 s. When the service dies, Plasma releases the inhibition itself (it watches the caller's
name on the bus), and the settings are restored by the next service start, if the current activity is not Stream.
The `ShowPopupsInDndMode` key is global — it works with every "Do Not Disturb", also one turned on in work — so
we hold it only for the duration of Stream (decision 80). The Streamer core today sends `notify-send -a Streamer` without hints,
so Plasma counts it as "@other" (together with agent notifications) — to let it through it needs
`-h string:x-kde-appname:streamer` (reported to the streamer repository).

## PC ⇄ Xbox monitors

Input of the right monitor for the Stream activity (PT-38, decision 81): `tide_daemon/monitors.py` (logic and the
`monitors.py status|pc|xbox [--json]` command, codes 0/1/2), `monitors.toml` (connector and VCP 0x60 input codes),
`monitors_service.py` (D-Bus object, worker thread, return to PC after leaving Stream). The screen layout in KWin
does not change — screen numbering in Plasma follows priority, and changing it moved the Stream dock.

| Element | Value |
| --- | --- |
| Object | `/Monitors` |
| Interface | `org.tide.Daemon.Monitors` |

| Method / signal | Signature | Description |
| --- | --- | --- |
| `Status()` | `→ s` | `{layout, target, available, error, interrupted, since, monitor, switching, result}` from the state file (no DDC); `layout` — `pc` or `xbox` (an interrupted switch counts as `xbox`), `available` — `ddcutil` is present, `result` — the last switch `{ok, layout, changed, error, note, auto, at}` or `null` (`auto`: return to PC after leaving Stream) |
| `SetLayout(s layout)` | `→ s` | `pc` or `xbox`; immediately `{accepted, error}` (refusal: unknown layout, a switch already in progress), the switch itself runs in the worker thread |
| signal `Changed()` | | a switch is in progress or its result changed |

- **Switch:** `ddcutil detect` (I²C bus by DRM connector, "Invalid display" blocks skipped), reading
  `getvcp 60` and — when the input differs — `setvcp 60 0x11|0x0f --noverify`, up to three attempts 1 s apart. Before the write
  the state gets `pending`; the state on disk (`$XDG_STATE_HOME/tide/monitors.json`) is the last confirmed input,
  because on HDMI the monitor usually does not answer over DisplayPort.
- **Errors:** no `ddcutil` or no DDC response — an error asking to switch the input with the button on the monitor,
  the state stays at the previous input. Switches from the service and from the command run one by one under `monitors.lock`.
- **Work:** leaving Stream and a service start in work with state `xbox` (or interrupted) switch to PC
  (the source of "is it Stream" — [Stream shortcuts](#stream-shortcuts)); leaving during a switch to Xbox — after it ends.
  A failed automatic return is a notification "Prawy monitor został na Xboxie" (The right monitor stayed on the Xbox) (without `--no-notifications`).
- **Result:** `{ok, layout, changed, error, note}`; `note` when the input could not be read and the last
  confirmed one counts.
- **Tests and disabling:** `--no-monitors`; `$TIDE_MONITORS_BIN` points to a directory with `ddcutil` instead of PATH (tests:
  fakes from `tests/fake_screens.py`, the service in tests — a nonexistent directory).

## Stream shortcuts

Pause, Meta+Pause and Meta+B work only in the Stream activity (PT-38, decision 81, `tide_daemon/stream_shortcuts.py`).
The service exposes no object. "Is it Stream" is answered by `tide_daemon/current_activity.py`: it listens to
`org.kde.ActivityManager.Activities.CurrentActivityChanged`, at start asks for `CurrentActivity` (an answer
older than the last signal is dropped) and emits `changed(activity, stream)`; the receivers are hooked up in `app.py`.
Stream is the activity assigned to the `stream` look in `$XDG_CONFIG_HOME/tide/activities.json`.

- **Entering Stream:** first the state file with an `entered` marker (always, even when we take nothing away),
  then a key taken by another action (today Meta+B — `org_kde_powerdevil/powerProfile`) is
  taken from it by `setForeignShortcut`, and the full list of its keys goes earlier to
  `$XDG_STATE_HOME/tide/stream-shortcuts.json`; then `doRegister` and `setShortcut(…, SetPresent|NoAutoloading)` for
  `tide_stream/mic` (Pause), `tide_stream/privacy` (Meta+Pause) and `tide_stream/brb` (Meta+B).
- **Press:** the signal `org.kde.kglobalaccel.Component.globalShortcutPressed` on `/component/tide_stream` →
  `kamp mic.toggle`, `kamp privacy.toggle`, `kamp brb.toggle` in a separate process (`$TIDE_KAMP`, PATH or
  `~/.local/bin/kamp`); code 2 is "the Streamer core is not running" in the log.
- **Leaving Stream, stopping the service, a start in work with a leftover file:** `unregister` of the `tide_stream` actions
  and giving back the taken keys (the saved list, and when the action changed in the meantime — only the added key). Only
  when there is no file and our shortcuts are in the registry — the key returns to the action that has it in `defaultShortcut`
  (candidates from `allMainComponents` and `allActionsForComponent`); with the file, a key removed before Stream stays removed. In work with nothing to give back the service only reads the registry.
- **Thread and retries:** KGlobalAccel calls go one by one in a single worker thread (the service loop does not wait),
  each task takes the last requested state; a KGlobalAccel error means a retry every 5 s (at most 12 times per event).
- **Crash in Stream:** when the service dies in the Stream activity, Pause, Meta+Pause and Meta+B stay captured by
  `tide_stream` (with no reaction) until its restart — systemd brings it back after 5 s, and a start in work gives the keys back.
- **Backup during Stream:** `make install` (and `make backup`) copies `kglobalshortcutsrc`; a copy
  made in Stream has Meta+B under `tide_stream`, and the power profile only with Battery. After restoring such a copy
  (`make restore`, effective from the next login) the service at a start in work sees `tide_stream` shortcuts without a state
  file: it removes them and gives Meta+B back to the action that has it among its defaults. It is safer, though, to make backups in work.
- **Disabling:** `--no-stream-shortcuts`; an empty `$TIDE_KAMP` — presses call nothing (the tests do this).

## Box

An isolated project environment in an Incus container (card K-38, design: `docs/card-box-design.md`, working
decisions: `docs/box-decisions.md`).

### Installation

Incus, the pool, the project and the network rules are set up **only** by `scripts/box/install.sh` run with sudo —
the service runs as a regular user and changes nothing from this list. The theme's `make install` calls no
`box-*` target (the test `scripts/tests/test_box_install.py` guards this).

```bash
make box-state OUT=docs/box-prototype/host-before.txt   # snapshot of the host state before installation
make box-install                                         # sudo asks for the password once; then no questions
bash scripts/box/install.sh --dry-run                    # plan without changes (with sudo it sees the whole Incus state)
make box-uninstall                                       # undo (asks, removes all boxes)
make box-state OUT=docs/box-prototype/host-after.txt
diff docs/box-prototype/host-before.txt docs/box-prototype/host-after.txt   # empty
```

`make box-install` accepts `BOX_PROXY_PORT=18080` (the Tide proxy port, the same value as the service's `box.proxy-port`;
changing it requires running `make box-install` again, because the rules are static) and `BOX_PROJECT_MODE=restricted|admin`.
The steps — each first checks the state, so a second run changes nothing and prints "bez zmian" (no changes):

1. **apt simulation** `apt-get -s install incus btrfs-progs` (plus `nftables` when `nft` is missing): refusal when the list
   contains a package with `qt6`/`kf6`/`libkf`/`plasma` in its name (the script prints those packages) or when apt upgrades or
   removes an installed package — uninstalling would not undo that. The `incus` package from Ubuntu universe (6.0 LTS);
   the Zabbly repository only as a separate variant with separate consent (P-7). An Incus installed earlier, not by
   this script, is not taken over.
2. **Installation** of the same packages; the list of new packages (from the simulation) goes to the state file
   `/var/lib/tide-box/install.state` before installing, together with the groups that existed earlier and copies of
   `/etc/subuid`, `/etc/subgid`.
3. **Preseed** `incus admin init --preseed`:
   - a btrfs **pool** `tide` in a sparse file `/home/tide-incus-pool.img` (200 GiB, decision D8). Incus creates a loop
     file only in `/var/lib/incus/disks/tide.img`, so the script makes a link there to the file on `/home` and after
     the preseed checks that the file is on `/home`; it does not format an existing file without a pool;
   - **bridge** `incusbr0`: IPv4 with NAT and DHCP (`ipv4.address: auto`), no IPv6;
   - profile `default`: a disk on the `tide` pool, a `eth0` NIC in `incusbr0`.
4. **Group**: the user from `SUDO_USER` goes into the `incus` group (access through `incus-user`, only to their own project);
   it works after logging in again (the `tide-daemon` service also needs a new session).
5. **Restricted project** `user-<uid>` — the one `incus-user` uses; the script creates it itself before `incus-user`
   does (it could give the project its own bridge without our rules): `restricted=true`,
   `restricted.containers.nesting=allow` (Docker in the box), `restricted.devices.disk=allow` only under the home
   directory (`restricted.devices.disk.paths`), the user's `restricted.idmap.uid/gid` (it only allows an instance to set `raw.idmap`
   with this uid/gid — the mapping is created only by the box, PT-169), `restricted.networks.access=incusbr0`; its own `default` profile as above; `incus-user.socket`
   enabled. In addition `root:<uid>:1` in `/etc/subuid` and `root:<gid>:1` in `/etc/subgid` with an `incus` restart —
   without them a container with `raw.idmap "both <uid> <uid>"` does not start, and the restricted project rejects `shift=true`
   on paths from the home directory (PT-164, P-4); `make box-uninstall` removes these lines like the rest of what was appended. The script accepts `--project-mode admin` (group `incus-admin` = host root) only after trial P-5
   from PT-164 and with a `tryb-admin` row (admin mode) in `docs/box-decisions.md`.
6. **nftables**: the file `/etc/tide-box/tide-box.nft` (content: `scripts/box/files.sh rules <bridge address> <port>`)
   checked with `nft -c`, then loaded with `nft -f`.
7. **Persistence**: the unit `tide-box-nft.service` loads the file at boot (before `network-pre.target`
   and Incus); the drop-in `/etc/systemd/system/incus.service.d/tide-box.conf` gives `incus.service` a `Requires=` on it
   — without the rules Incus does not come up. **`systemctl stop` and `systemctl restart tide-box-nft` stop (restart:
   stop and start again) Incus together with all boxes and `incus exec` sessions** —
   `Requires=` propagates stop and restart; with stop this is intended (boxes go down before Incus). Reloading the rules
   without touching Incus: `systemctl reload tide-box-nft` (`ExecReload=nft -f …`; reload does not
   pass through `Requires=`) or directly `sudo nft -f /etc/tide-box/tide-box.nft`. The unit has no
   `ExecStop`: stopping it does not remove the rules (the table is removed explicitly by `uninstall.sh`), so the filter stays
   in the kernel even when incusd is killed after the stop timeout is exceeded, and the boxes survive.
   We do not use `nftables.service`:
   `/etc/nftables.conf` starts with `flush ruleset` and would wipe the Docker and Incus rules.

At the end the script prints a report: the new packages from the simulation, `incus list`, `incus project show user-<uid>`
and `nft list table inet tide-box`.

**Network rules — the decision "static nftables, policy in the proxy".** The rules are static and set up only by the
script with sudo; the service never modifies nftables (no sudoers and no privileged helper, no Incus network ACLs).
The per-box policy — domain list, host Ollama — lives in the Tide proxy, which is the only thing reachable
from the bridge. A box spoofing another IP address is stopped by Incus NIC filtering (PT-167), not by nftables.
The `inet tide-box` table has two named base chains:

- `input` (hook input) and `forward` (hook forward), both with priority `filter + 10` — after the chains of the `incus`
  table (priority `filter`), so an Incus `accept` (DNS and DHCP of its dnsmasq, forward with NAT) is not enough,
  and a `drop` here is final;
- the chains have policy `accept`, because the hook sees the traffic of all host interfaces; the "drop policy" for traffic
  from `incusbr0` is the last rule of each chain (`iifname "incusbr0" drop`);
- `input`: accept only `ip daddr <bridge address> tcp dport <proxy port>` and DHCP (`udp sport 68 udp dport 67`);
  DNS (53, tcp and udp) explicitly DROP, then everything else from the bridge DROP;
- `forward`: everything from `incusbr0` and to `incusbr0` DROP — the box goes nowhere through NAT, and connections
  from the host to the box get no answer (box ports are not exposed, section 4.6 of the design).

**The `tide-proto` convention for trials.** Prototypes (PT-164, PT-178) may, for the duration of a trial, insert their own
`accept` rules before the drop rules: `nft insert rule inet tide-box forward iifname "incusbr0" … accept comment
"tide-proto"` (or in the `input` chain), and after the trial remove them by handle (`nft -a list chain inet tide-box
forward`, `nft delete rule inet tide-box forward handle <n>`). Reloading the file (`sudo systemctl reload
tide-box-nft` or `sudo nft -f /etc/tide-box/tide-box.nft`) also removes them and does not touch the boxes. **Not
`systemctl restart tide-box-nft`** — through `Requires=` a restart stops Incus with all the trial containers.
The service does not create such rules.

**Uninstalling** (`make box-uninstall`, `scripts/box/uninstall.sh`, with `--dry-run` without sudo) undoes the steps
in reverse order according to the state file: the unit and drop-in, the `inet tide-box` table and `/etc/tide-box`,
`incus admin shutdown` and stopping the `incus*` units, unmounting `/var/lib/incus`, detaching the pool loop,
removing `incusbr0` and the `inet incus` table, `apt-get purge` of the packages from the state file (simulation first; refusal
when the purge would remove anything outside the list), then `/var/lib/incus`, `/var/cache/incus`, `/var/log/incus`, the pool file,
groups created by the packages, lines appended to `/etc/subuid` and `/etc/subgid`, and the state file. It refuses unit
files without the Tide marker. All boxes and their data in the pool disappear; host directories mounted into boxes
(`~/.local/share/tide/box`) stay.

**Proof of reversibility:** `scripts/box/host-state.sh` (read-only; `nft list ruleset` through sudo when needed)
records packages (`dpkg-query -W`), links (`ip -o link`: name and type), `nft list ruleset` without counters,
`getent group`, `/etc/subuid` and `/etc/subgid`, the existence of the pool file and Incus directories, and the `incus*`,
`lxcfs*`, `tide-box*` units. The snapshot before `make box-install` and after `make box-uninstall` must be identical.

### Building the box (PT-167)

Modules: `box/incus.py` (a thin CLI adapter), `box/builder.py` (build, per-card copy, status, reset,
removal, approval), `box/state.py` (journal, states, `BoxStatus`, `card_box_name`), `box/settings.py`,
`box/cli.py`; entry point `python3 daemon/tide.py box …` (installation to PATH — with the base image, #16).

**Adapter.** Every `incus` command gets `--project <project>` (default `user-<uid>`, the restricted project
from the installation) and stdin from `/dev/null`. The program: `$TIDE_INCUS_BIN` or `incus` from PATH. We look for an instance by
exact name in `incus list --format json` (the filter `incus list <name>` matches prefixes — an error from P-5).

**Settings** — the same `box` section in `$XDG_CONFIG_HOME/tide/cardflow.json` and the same module `box/settings.py`
(`BoxSettings`) as the proxy (below, "Outbound proxy"); builder keys, all optional: `project`
(`user-<uid>`), `base-image` (`tide-base`), `pool` (`tide`), `nic` (`eth0`), `ipv6-filtering` (`false`),
`agents-version`, `ip-wait-seconds` (30), `agent-uid`/`agent-gid` (1000 — the owner of the `/home/agent` home;
the final value is set by the base image #16). A bad value → a warning on stderr of `tide box` and the default value.
There is no proxy address in the settings: the builder takes the address and port from the **running proxy** (`box.proxy.running()`:
listening on the bridge address `box.bridge`, port `box.proxy-port`) — a single source for the proxy and the build.

**Build order** (`tide box build <slug>`, the project profile from the Board):

When `verify` checks project code (e.g. `npm ci`, then `npm run lint`), use
`tide box build <slug> --verify-worktree <directory>`. The CLI checks that the directory belongs to the project's repository
on the Board and that there are no changes to tracked files. It sends an archive of the tracked files of HEAD, without `.git`,
untracked files and ignored host dependencies. The builder unpacks it into `/opt/tide/verify/work`
and runs only `verify` there, as the agent user with its UID/GID and `HOME=/home/agent`;
the first failure stops the following commands of this verification.
It records the commit code in `box.verify.source_commit`. The copy and its dependencies are removed before the snapshot, also
after a failed verify; a cleanup error aborts the build. No mount of the host repository is created.
Dependency preparation commands must be explicitly approved in the profile's `verify`. `setup` keeps
its existing working directory. Without the option the builder behaves as before.

1. `incus init <base> <slug>` — a copy of the base image (this is `launch` without start: the network card gets filtering
   before it sends the first packet) with `-c limits.cpu=… -c limits.memory=…` from the profile
   (default 8 / 24GiB). Docker (`docker.enabled: true` or `docker` in `stacks`) gets
   `security.nesting=true`, `security.syscalls.intercept.mknod=true` and
   `security.syscalls.intercept.setxattr=true`, before the start and the first template.
   Without Docker these keys are skipped. **Never `security.privileged`.**
   `incus config device override <slug> root size=<resources.disk>` (default 60GB); a private host directory
   `~/.local/share/tide/box/<slug>/home` (0700) as a disk device at `/home/agent`.
   `raw.idmap` maps the host user's UID/GID to `agent-uid/gid`, the same as BoxRunner.
   The old volume is migrated first without deleting data (PT-189, below).
2. NIC filtering (below).
3. `incus start`, the box's IPv4 address, `set_box_policy(ip, build_domains(profile), host-ollama, build denial
   log)` in the Tide proxy (#5) — before the first `incus exec`. The denial log:
   `$XDG_STATE_HOME/tide/box/<slug>/build-denied.log`.
4. No-swap check: the first `incus exec` checks `/sys/fs/cgroup/memory.swap.max=0`.
   A missing file (cgroup v2 required), a read error or another value aborts the build before the templates and the snapshot.
   Then the stack templates per `stacks`: `incus file push box/stacks/lib.sh` and `box/stacks/<stack>.sh` to
   `<slug>/opt/tide/stacks/`, then `bash /opt/tide/stacks/<stack>.sh` in the order of `stacks` (below, "Stack templates
   in the box (PT-192)"; a missing script or `lib.sh` = an error before `incus`).
5. `apt-get update` and `apt-get install -y --no-install-recommends` with the packages from the templates and the profile
   (`templates.build_apt`). 6. The `setup` commands (`bash -c`). 7. `docker pull` of images (`templates.build_images`),
   only with Docker enabled. A failed pull does not abort the remaining pulls or the build: the list
   `failed_images` in `box.ready` and the state "built with a warning".
   8. `verify` — a failed command does not abort the build: the event `box.verify`
   with `result: fail` and a `failed` list, the state "built with a warning" (`clean` with `warning`).
9. The key `user.tide.recipe` = the stamp `profile.recipe_stamp(profile, templates_version, agents-version)`
   (JSON: the hash and key abbreviations for `diff`), then `incus snapshot create <slug> clean --reuse`. The snapshot carries
   the key in its config, so every copy from `<slug>/clean` has the same stamp. (Incus 6.0 does not set
   a snapshot description on `snapshot create`, so the "description" is a `user.*` key of the snapshot config.)
   `templates_version` is a hash of all `box/stacks/*.yaml` and `*.sh` (stored as `builder.STACKS_VERSION`).

### Docker and the port pool in a step (PT-171)

`docker.enabled: true` also turns on the `docker` stack; the `docker` stack alone turns Docker on regardless of an
omitted or false flag. Profile normalization is also used for the recipe hash.
The template `box/stacks/docker.sh` belongs to PT-192 (#29): it installs Docker, adds `agent` to the `docker` group
and writes the `dockerd` drop-in with the proxy and `NO_PROXY=BOX_NO_PROXY`. PT-171 changes neither this script nor the proxy.
The builder sets the Incus configuration and through `incus exec` pulls, without duplicates, the template images
(including the pinned Ryuk from `docker.yaml`) and the profile's `docker.images`, with the build's `HTTPS_PROXY`.
It does not start the host Docker. The build does not pass `DOCKER_HOST`; BoxRunner removes this variable also
from the step's environment additions. Testcontainers use the `/var/run/docker.sock` socket inside the box.

`box/ports.py` reserves 10 consecutive ports from `ports.pool` (both ends inclusive). Each next
range starts 10 ports further on. An incomplete tail of the pool is not allocated. `PORT`, `E2E_PORT`,
`TIDE_PORT_BASE` and all names from `ports.env` have the value of the first port of the range.
A card profile replaces the project pool and sums names according to `profile.merge`. Without `ports.pool` there is no
automatic allocation. A reservation does not force the application to use the ports; the application must read the environment.

Pool state: `$XDG_STATE_HOME/tide/box/<slug>/ports/<box>.json`, an `flock` lock on a separate
`.lock` file, atomic state replacement. The lock works across processes. Allocations of different cards and steps
in one box do not overlap, also after a change of the pool bounds. Separate boxes have separate pools,
because they have separate network namespaces. State files lie outside the agent's mounts.

The engine records the `box.ports` intent before the allocation and `boxPortOwner` in the step metadata.
An exhausted pool means `step.waiting` with the cause `box`; the next tick retries the allocation.
BoxRunner writes port values only in `in/env`, never in argv or in the environment of the host Incus client.
Finishing a step (also `error`) and a failed start release the allocation; recovery after a restart releases
also finished steps, failed start intents and reservations from before a start intent.
A failure to persist a successful start leaves the reservation with the open intent; recovery
finds the unit and keeps its ports until the end of the step.
The `BoxRunner --demo` mode uses the same pool of the approved profile. It releases the allocation only
after the client ends and the unit in the box is confirmed `inactive`. Interrupting the wait stops the unit;
an active or unknown state leaves the reservation in place. The next demo checks the orphaned allocations
of that box and releases only those with a confirmed unit end. The owner identifier
`demo-<time>-<random suffix>` determines the unit name; a separate demo lock is inherited
by the Incus client (`pass_fds`) and also protects the window before the unit is created. The death of the parent
or closing its descriptor does not release the lock of a live client. Only closing the last
copy of the descriptor lets the next demo check the unit and safely recover the reservation.

**Risk K1.** The `docker` group gives the `agent` user the box's root rights, including the ability to
mount the rootfs. The internal `denyRead` does not protect the agent's credentials from the test code.
NIC filtering is on the Incus side, full credential protection requires K3. This description is visible
in the profile proposal and in the consent pack; the Docker item stays yellow.

**Further scope, outside PT-171 (user decision of 6.11.2026).** Assigning real Testcontainers
sessions to cards and cleaning up their containers and images after close-out require a separate ticket.
PT-171 does not run `docker rm` or `docker image prune`. A sketch of the requirements:
`docs/agents/plans/followups/pt-171-testcontainers-cleanup.md`.

**Swap in the restricted project (PT-192).** In Incus 6.0 an explicit `limits.memory.swap=false` is a forbidden
low-level key. The builder omits this key: with a hard `limits.memory` Incus sets the swap limit to 0
also for the default value. The check from item 4 confirms the effect in the kernel; the build refuses if it cannot
confirm it. `restricted.containers.lowlevel` stays blocked. The rationale and sources:
the decision `swap-zero-bez-lowlevel` in `docs/box-decisions.md`. The lifecycle (#19/PT-182) should keep the same
rule when memory limits are applied again.

Every build `incus exec`: `-T`, as the box's root, with `HTTPS_PROXY`/`HTTP_PROXY` (and lowercase)
`http://<bridge address>:<box.proxy-port>` from the running proxy, `NO_PROXY` = `box/netenv.py` `BOX_NO_PROXY`,
`DEBIAN_FRONTEND=noninteractive`, `TIDE_BOX_PROXY` (the proxy address) and `TIDE_BOX_NO_PROXY` (= `BOX_NO_PROXY`) for
the templates, `TIDE_NODE_VERSION`/`TIDE_DOTNET_SDK` from the profile. **No running proxy (or no address for it) is an error
before the first `incus` command** (the `ProxyMissing` policy) — there is no build "without a proxy". `tide box build`,
`tide box base` and `tide box login` from the command line use the proxy running in this process, and without one they start
their own `ProxyService` for the duration of the command (decision `base-wlasne-proxy-cli`, for `build` since PT-192); while
the Tide service is running the port is taken and the command refuses (code 1). The build denial log is cleared at its start
(empty after a build = no domain was missing), and `tide box build` prints its path. After the snapshot (and on error) the builder removes the build policy
(`clear_box_policy`), so after the build the box has no policy in the proxy until a step (#18).
Command output goes live (streamed) to `$XDG_STATE_HOME/tide/box/<slug>/build.log`.
`type: vm` → the error "nieobsługiwane w tej wersji" (not supported in this version) without any `incus` call. An existing box is removed on
build and created anew (the `/home/agent` home stays); the build refuses when steps are running in the box.

**NIC filtering — why.** The Tide proxy (#5) recognizes a box by its IP address, and an agent in the `docker` group
has root rights in the box and could change its IP address or MAC to get another box's policy. On the box
NIC the builder sets `security.mac_filtering=true`, `security.ipv4_filtering=true` and
`security.ipv6_filtering=<ipv6-filtering>` — Incus drops packets with a source address other than the assigned one
(P-5: spoofing does not reach the host). `ipv6_filtering` defaults to `false` (decision `filtrowanie-nic-bez-ipv6`:
Incus 6.0 does not start a NIC with it without `br_netfilter`, and the bridge has no IPv6). A NIC from the `default` profile gets
`config device override`, a local NIC (a per-card copy) — `config device set`. This happens on every path
that creates a box: on build and on copy. `tide box status` shows "filtrowanie NIC: tak/nie" (NIC filtering: yes/no) from
`incus config device show`; "no" (missing `mac`/`ipv4`) = the state **niezgodne** (mismatched) and `BoxStatus.usable == False` — the step
does not start (#18).

**Per-card copy** (`box: per-card`): `copy_for_card(slug, card)` → `incus copy <slug>/clean <slug>-<card>`
(the name only from `card_box_name(slug, card)`, e.g. `plasma-tide-k-38`), `user.tide.copy-of=<slug>`,
`user.tide.card=K-38`, its own directory `<slug>/cards/k-38/home` at `/home/agent` (0700, the same UID/GID mapping),
NIC filtering (`device set`), the copy's **own `clean` snapshot** (`incus copy` from a snapshot yields an instance with no
snapshots; the snapshot is taken after attaching the home and filtering, so `reset --card` does not undo them) and the event `box.ready`
with `card`, `copy_of`, `hash` and `warning` carried over from the project box (built with a warning → the copy too).
A second copy for the same card is not created (it returns the existing name), unless the project recipe was
rebuilt with a new hash — then an inactive copy is recreated from the new snapshot.
Before recreating a copy, its old home is migrated without deleting data. A new copy stays stopped;
starting it for work is handled by #18/#19.

**Journal and states.** `<Board>/projects/<slug>/box/journal.jsonl` (JSON Lines, `flock`): `box.build`
(`phase: start|end`, `result`, `steps`, `hash`), `box.ready`, `box.verify` (`result`, `failed`), `box.reset`,
`box.stop`, `box.approved` (`source: cli`, `accepted_red`), `box.use`/`box.release` (a step in the box — called by
#18 through `mark_in_use`/`mark_released`) and `box.destroy`. `box.build` (end), `box.ready`, `box.reset`,
`box.stop`, `box.verify` have `duration` in seconds. States: `brak → budowa → clean → w użyciu → nieaktualny → reset →
zatrzymane` (none → building → clean → in use → stale → reset → stopped), plus `niezgodne` (mismatched) and `błąd budowy` (build error). The state from the journal is corrected by Incus: no instance →
`brak`, no filtering → `niezgodne`, snapshot hash different from the profile → `nieaktualny (do przebudowy)` (stale (needs rebuild)) with a list of
keys from `profile.diff` (e.g. `stacks, apt`; a change of resources or domains is not a recipe difference), a stopped
instance → `zatrzymane`. `BoxStatus.usable` (whether a step may enter, #18) requires an existing instance,
NIC filtering, an **approved profile** (no profile = unusable) and no recipe differences.

**CLI** `python3 daemon/tide.py box …` (codes: 0 success, 1 error, 2 bad argument, 3 refusal/mismatched/stale):

**K-38 v1, PT-267:** currently only `status` and `stop` work, through the D-Bus service.
The remaining commands from the historical list below and the modular `--demo` runner are disabled until stages II/III;
they perform no operations locally. The preserved client and the service contract: [CLI v1](box-cli-v1.md).

| Command | Action |
| --- | --- |
| `build <slug>` | build as above |
| `status <slug> [--card K-38] [--json]` | state, state in the journal, hash of the snapshot and of the profile, differences, NIC filtering, steps in the box |
| `reset <slug> [--card K-38]` | `incus snapshot restore <box> clean`; refuses (code 3) when a step is running; the host home stays, and the old snapshot gets the proper mount again |
| `stop <slug> [--card K-38]` | stop through `incus stop`; code 3 when a step is running; the event `box.stop` with its duration |
| `start <slug> [--card K-38]` | read the limits from the profile, `incus start`, wait for `incus exec … true`; the event `box.ready` with its duration |
| `destroy <slug> --yes` | removes the project's instances and copies (`user.tide.copy-of`); keeps homes, exports and old volumes for separate cleanup; refuses when a step is running |
| `approve <slug> --file box.yaml [--accept-red ITEM]…` | validation against the schema from #2, a record on the Board and `box.approved source=cli` — explicit consent from the command line, equivalent to the consent pack; a red item (e.g. `github.com`) only with `--accept-red` |
| `base` | builds the Tide base image (`make box-base`) — below, "Tide base image" |
| `login [<slug>]` | agent login without K3 (K1) in the project box; with K3 for both programs — "nic do zalogowania" (nothing to log in) |

`run` will be added by #28. The text `status` also shows the times of the `box.stop` and `box.reset` events; the JSON contract of
`BoxStatus` stays unchanged (K-36).

**Lifecycle (PT-182, D14).** `box/lifecycle.py` handles auto-stop after `box.idle-hours` without a step
(a service setting in the `box` section of the `cardflow.json` file, 4 hours by default). The check runs in the engine's `tick`
with `box.enabled` on. The last use/release/start of the box counts; an old event time alone
does not allow stopping the box. `box.use` reservations, `procs` and the state of units
`systemctl --user is-active` inside the box block stop, reset and rebuild. After a service restart the state
of units comes from the persistent step entries; an unknown state also blocks the operation. The CLI checks the same
journals and units. A step lasting longer than the idle limit is not stopped.
The activity check and the whole destructive operation run under a shared project lock (`flock`),
also used when reserving a step. Before `box.use` the step checks again the compatibility and readiness of
the box under this lock. The lock also covers rebuild, reset, copying and removing copies.

On every start of an existing box the limits are refreshed with `incus config set`: `limits.cpu`
from `resources.cpu`, `limits.memory` from `resources.memory`. This applies to a restart onto new mounts too:
the caller refreshes the limits before `ensure_devices` and checks swap after preparing the mounts.
In line with the PT-192 rule `swap-zero-bez-lowlevel` we do not send the forbidden `limits.memory.swap`,
not even false; a hard RAM limit is supposed to give `memory.swap.max=0` in cgroup v2, which we check after start
and before launching the agent. A missing confirmation refuses to run the step. `resources.swap=true`
is explicitly rejected in the restricted project. A change of resources does not change the recipe hash and requires no
rebuild. Start does not remove or disable NIC filtering. The installer sets
`restricted.snapshots=allow` also when updating the project, keeping the other restrictions.

The project box resets at the user's request or on an explicit rebuild of a changed recipe
(`box.reset`, reason `zmiana przepisu` (recipe change)). Idleness and card close-out do not reset the project box.
After `card.closed` the service removes only the card copy computed by `card_box_name` and confirmed by the
metadata `user.tide.copy-of` and `user.tide.card`. Before removal it migrates the old home;
the host directory, the export and the old volume remain for separate cleanup. It records `box.reset`
with the reason `karta domknięta` (card closed), `removed: true` and `duration`. When the copy has an active step, it records
`result: deferred` and retries the cleanup on later passes, also after a service restart.

**Tests without Incus.** `daemon/tests/fakebin.py` has an `incus` fake (`daemon/tests/fake_incus.py`): it records argv
in `$TIDE_FAKE_LOG`, keeps state in `$TIDE_FAKE_INCUS_STATE` (`list`, `snapshot list`, `config show`,
`config device show`, `info` print JSON from it; `init/launch/copy/snapshot/restore/start/stop/delete/config
set/config device set|override|add` and volumes change it), and `exec` runs the command locally (in `--cwd`)
with a PATH starting with `<fakebin>/box` — `apt-get`, `docker` and other programs "in the box" only record
the call. `daemon/tests/__init__.py` sets `TIDE_INCUS_BIN` to a nonexistent program, so no test
can reach a real Incus. Tests: `daemon/tests/test_box_builder.py`.

### Tide base image (PT-179)

The image from which the builder's `incus init` makes every project box (`box.base-image`, `tide-base` by default).
Built once per host and after every change of agent versions or scripts: `make box-base` (= `python3 daemon/tide.py box
base`, without sudo — the restricted Incus project). Modules: `box/base.py` (build and login), `box/stacks/base.sh`
(the script in the box), `box/stacks/tide-step.sh`, `box/agents.py` (versions), `box/netenv.py` (`BOX_NO_PROXY`).

**Order of `tide box base`:**

1. The Tide proxy address (#5) — before the first `incus` command. A command-line invocation uses the proxy running
   in this process, and when there is none, it starts its own instance of the same `ProxyService` (`start_service` from settings:
   bridge address, `box.proxy-port` — the only port nftables lets through) and stops it after the build. When the port
   is already held by the Tide service (`box.enabled`), the start will fail — the build refuses with this information (stop the service for
   the duration of the build). No bridge or no `incus` → refusal. There is no "no proxy" path.
2. `incus init images:ubuntu/24.04 tide-base` (without start), NIC filtering as in the builder, start, IPv4 address.
3. `set_box_policy(ip, domains of the base template, ollama=False, base-denied.log)` — the build list = `templates.build_domains({})`,
   i.e. the `base` template with the domains of the agent installers from P-6 (`claude.ai`, `downloads.claude.ai`, `registry.npmjs.org`,
   `archive.ubuntu.com`, `security.ubuntu.com`, `nodejs.org`). The denial log
   `$XDG_STATE_HOME/tide/box/tide-base/base-denied.log` (0600) is cleared at the start of the build — empty after a successful
   build means "no domain was missing"; the command output is in `base-build.log` next to it.
4. `incus file push` of the scripts to `/opt/tide/stacks/` and of the user's fonts: regular font files (`.ttf`, `.otf`,
   `.ttc`, `.woff2`, …) from the host's `$XDG_DATA_HOME/fonts` (`~/.local/share/fonts`), without links, as `fonts.tar`
   (owner root, 0644) — copied, not mounted (K-33).
5. `bash /opt/tide/stacks/base.sh <step>` as the box's root, each step in a separate `incus exec` with the proxy in the environment
   and the values from the service: `TIDE_BOX_PROXY`, `TIDE_BOX_NO_PROXY` (= `BOX_NO_PROXY`), `TIDE_AGENT_UID/GID`
   (`box.agent-uid/gid`), `TIDE_CLAUDE_VERSION`, `TIDE_CODEX_VERSION`. The steps (`base.STEPS`):

   | Step | What it does |
   | --- | --- |
   | `sources` | Ubuntu sources (`ubuntu.sources` or `sources.list`) from `http://` to `https://` (decision `apt-przez-https`; a foreign `http://` source = error), `/etc/apt/apt.conf.d/90tide-proxy` with `Acquire::http::Proxy` and `Acquire::https::Proxy` |
   | `packages` | `apt-get update --error-on=any`, `git build-essential curl ca-certificates xvfb weston fontconfig nodejs npm bubblewrap socat apparmor` |
   | `user` | `agent` with uid/gid 1000 (`ubuntu` with uid 1000 is taken over), lingering (`loginctl enable-linger`) |
   | `environment` | `/etc/environment` (`HTTP_PROXY`, `HTTPS_PROXY`, lowercase, `NO_PROXY`/`no_proxy` = `BOX_NO_PROXY`, `DISABLE_AUTOUPDATER=1`; the rest of the file, e.g. `PATH`, stays), `/etc/profile.d/tide-proxy.sh` (login shells) and `/etc/systemd/user.conf.d/90-tide-proxy.conf` (`DefaultEnvironment` of `systemd --user` services — steps) |
   | `fonts` | `fonts.tar` into `/usr/local/share/fonts/tide`, `fc-cache` |
   | `tide-step` | `/usr/local/bin/tide-step` (below) |
   | `apparmor` | the `bwrap` profile (the same as `scripts/sandbox/check.py profile` on the host) in `/etc/apparmor.d/bwrap-userns-restrict`, `apparmor_parser -r`, the `apparmor` service enabled — it loads it at every box start (decision `bwrap-profil-w-pudelku`) |
   | `claude` | the native installer `https://claude.ai/install.sh` downloaded to a file (no pipe to a shell) and run with the exact version in a temporary directory; the program goes to `/usr/local/bin/claude` (outside `/home/agent`, which in the project box covers the host home); `claude --version` as `agent` |
   | `codex` | `npm install -g @openai/codex@<version>`; `codex --version` as `agent` |
   | `cleanup` | `apt-get clean`, npm cache, apt lists, `/opt/tide` |

6. `user.tide.agents` = `agents.AGENTS_VERSION`, the **`tide-base/clean`** snapshot, `incus publish tide-base/clean --alias
   tide-base --reuse` (the image for the builder's `incus init`), `incus stop tide-base`.
7. The build policy removed from the proxy (`clear_box_policy`) — also after an error. A failed step = an error with no snapshot or image.

Without a `dockerd` drop-in (the `docker.sh` template, PT-192 — below). The script writes no value from the service (proxy address,
`NO_PROXY`, versions): it refuses to work without `TIDE_BOX_PROXY` equal to the call's `HTTP(S)_PROXY`, with an unpinned version
(`x.y.z`), or outside root.

**Agent versions** (`box/agents.py`): Claude Code `CLAUDE_VERSION` (the host version from analysis 4.7, checked in P-6),
Codex `CODEX_VERSION` (P-3/P-6) — exact, never `latest`; Claude Code auto-update in the box is disabled.
`AGENTS_VERSION` goes into the recipe hash of project boxes (`Builder.agents_version`; `box.agents-version`
in settings overrides it), and the `box/stacks/*.sh` scripts — through `templates_version`. Bumping a version or changing
`base.sh` makes project boxes stale: `make box-base`, then `tide box build <slug>`. Bumping Codex
= a repeat of P-3 (the K3 provider depends on the `backend-api/codex` paths).

**`BOX_NO_PROXY`** (`box/netenv.py`) = `localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal` (P-6, "NO_PROXY
per client"): the box's loopback and the Docker networks in the box. The same constant goes to the image's `/etc/environment`,
to every build `incus exec` (#4) and — in #6 — to the step. `netenv.proxy_env(proxy)` gives the complete set of variables.

**`tide-step`** (`/usr/local/bin/tide-step`, the BoxRunner contract #6/PT-169, which may extend it):
`tide-step <env file> <code file> <program> [arguments…]` — loads `/etc/environment`, then the step file
(`KEY=value`, as data — nothing is executed; a bad line → code 2), runs the program and writes its exit code
atomically to `<code file>` (0600), like the step's `EXIT_WRAPPER` on the host. The step runs as the agent's
`systemd --user` service (decision `krok-jako-usluga-user`), so step variables cannot come through the caller's
environment.

**Agent login (K1) — `tide box login [<slug>]`.** Outside the build, one time. The programs to log in are
`k3.login_programs()`: empty today (K3 for Claude Code and Codex), so the command ends with "nic do zalogowania"
(code 0), and `tide box base` with "logowanie agentów niepotrzebne" (agent login not needed). Were K3 turned off for a program, the build
ends with the instruction "zaloguj: `tide box login` <projekt>" (log in: `tide box login` <project>) (code 0, not an error), and `tide box login <slug>` opens
`incus exec -t <box> --user 1000 --cwd /home/agent -- claude login` (and `codex login`) with the Tide proxy and the project
policy (`compose_policy`) for the duration of the login. Credentials from the host's `~/.claude`/`~/.codex` are never copied.

**What is persistent.** The project box's `/home/agent` is the host directory `<slug>/home` (PT-189) — it survives a reset
from the `clean` snapshot and a rebuild of the box (K1 login, `~/.npm`, `~/.nuget/packages`, `~/.cache/ms-playwright`);
it also remains after `tide box destroy` for separate cleanup. The base image (the `tide-base` instance with the `clean` snapshot and the `tide-base` image) lives until the
next `make box-base`; the rest of the box's file system returns to `clean` on reset. Agent programs,
fonts and the proxy configuration are in the image, not in `/home/agent`.

Tests: `daemon/tests/test_box_base.py` (the `incus` fake from `fake_incus.py` learns `publish`; the real `base.sh`
is not executed in full — its file functions run after `source` in a temporary directory).

### Stack templates in the box (PT-192)

Shell scripts `box/stacks/<stack>.sh` (`node`, `playwright`, `dotnet`, `docker`, `python-qt`) next to the data
`<stack>.yaml` (#30). The builder (build step 4) pushes `lib.sh` and the scripts from the profile's `stacks` to `/opt/tide/stacks/`
and runs each as the box's root, in the order of `stacks`, on the base image. Every script: `set -euo pipefail`,
`source lib.sh`, refusal outside root and without a proxy (`TIDE_BOX_PROXY` must equal the call's `HTTP(S)_PROXY`),
downloading only with the `fetch` function (`curl` to a file, only `https://`, redirects too — never a pipe to a
shell) from the domains of its own template, versions pinned in the script or taken from the profile (never `latest`). No
script reads or copies the host's agent files (`~/.claude`, `~/.codex`).

**Who owns what.** `base.sh` (the base image, apt over HTTPS, `/etc/environment` with the proxy) — #16/PT-179;
the stack scripts, `lib.sh`, **the `dockerd` drop-in and `/etc/docker/daemon.json` — only `docker.sh`** (#29/PT-192;
the test `test_box_stacks.py` checks that no other repository file apart from documentation, tests and one-off
prototype scripts creates `docker.service.d`); Docker images (`docker pull` from `templates.build_images`) —
the builder (#4) and the Docker configuration in the box (#8/PT-171), not the template; domains and risk classes — `<stack>.yaml` (#30).

| Template | What it does | Input from the profile |
| --- | --- | --- |
| `node.sh` | Node from `nodejs.org/dist` (a `.tar.gz` archive with the checksum verified against `SHASUMS256.txt` of that version) into `/usr/local` (wins in PATH over the image's apt Node 18, which Codex needs); npm from this distribution; `CHROME_BIN=/usr/local/bin/tide-chrome` for Karma (a wrapper launching Chromium from `/opt/ms-playwright`, without it code 127 with the hint "dodaj stos playwright" (add the playwright stack)); `NODE_USE_ENV_PROXY=1` (Node ≥ 24 `fetch` through the proxy, P-6) | `node.version`: an exact `x.y.z` or a major number pinned in `NODE_PINS` (18, 20, 22, 24); without a version — `NODE_DEFAULT_MAJOR` (22); `x.y` = error |
| `playwright.sh` | `npx --yes playwright@<PLAYWRIGHT_VERSION> install --with-deps chromium` through the proxy (system dependencies through the image's apt), browsers in `/opt/ms-playwright` (in the `clean` snapshot, owner `agent` — a project with a different Playwright version pulls its own revision in a step), `PLAYWRIGHT_BROWSERS_PATH`; requires Node ≥ 20, i.e. the `node` stack earlier in `stacks` | — (version pinned in the script) |
| `dotnet.sh` | `dotnet-install.sh` from `dot.net` downloaded to a file, the SDK into `/usr/share/dotnet` (link `/usr/local/bin/dotnet`), dependencies (`libicu74`…) from apt, `DOTNET_ROOT`, telemetry disabled; the agent's `~/.nuget/NuGet/NuGet.Config` with `<clear />` and **only `nuget.org`** — no internal registries (written as `agent`, because `/home/agent` is the agent's volume) | `dotnet.sdk`: only exact SDK versions `x.y.z`, e.g. `['8.0.415']`; installation through `--version` and a check of the exact number; missing or the shortcuts `8`/`8.0` = error before downloading |
| `docker.sh` | `docker.io` from Ubuntu, **runc 1.2.6** from the GitHub releases (SHA-256 checksum pinned, the same as in P-1; amd64 only) as the default runtime in `/etc/docker/daemon.json` (decision `runc-1-2-6-w-pudelku`), the drop-in **`/etc/systemd/system/docker.service.d/proxy.conf`** with `HTTPS_PROXY`/`HTTP_PROXY` (and lowercase) = `TIDE_BOX_PROXY` and `NO_PROXY`/`no_proxy` = `TIDE_BOX_NO_PROXY` (`BOX_NO_PROXY` injected by the builder — the script refuses when it is empty), `agent` in the `docker` group, a `dockerd` restart and a check of `systemctl show docker -p Environment`, of the default runtime and of `docker info` as `agent`. `DOCKER_HOST` is not set | `docker.enabled` (the builder sets nesting) |
| `python-qt.sh` | Python 3, `python3-venv`, `pip`, PyQt6 with QML (`python3-pyqt6`, `python3-pyqt6.qtquick`, `qml6-module-qtquick*` modules) from the Ubuntu archive, `QT_QPA_PLATFORM=offscreen`; `xvfb` and fonts are in the base image; an offscreen `QGuiApplication` check. PySide6 from PyPI is not installed (the project adds a pinned version in `setup`) | — |

**Stack variables** (`write_stack_env` from `lib.sh`) go to the same three places as the base image's proxy:
`/etc/environment`, `/etc/profile.d/tide-<stack>.sh` and `/etc/systemd/user.conf.d/91-tide-<stack>.conf`
(`DefaultEnvironment` of `systemd --user` services, i.e. of `tide-step` steps). Values are checked (no spaces,
quotes or `$`).

**Build-only domains.** Besides `domains`, a template may have a `build-domains` list: domains for the image's scripts and services
only during the build. `templates.build_domains(profile)` (the build list for the proxy) contains them,
`templates.work_domains(profile)` (the step policy in `proxy.compose_policy`) and `known_domains()` (classification
of profile items) do not. Today: `docker` → `github.com` (red as a work domain — a remote repository)
and `release-assets.githubusercontent.com` (the runc release), `dotnet` → `aka.ms` (the `dotnet-install.sh` channels),
`base` → `motd.ubuntu.com` (the Ubuntu welcome service; explained denials from the PT-192 trial of 18:38). Work domains
added in PT-192 from the P-1/P-6 logs: `production.cloudfront.docker.com`, `crl3/crl4/ocsp.digicert.com`
(checking NuGet package signatures), the mirrors `playwright-akamai/verizon.azureedge.net`.

**`STACKS_VERSION`** (`box/builder.py`) is the stored checksum of the `box/stacks` directory (`templates_version()`: all
`*.yaml` and `*.sh`, including `lib.sh` and `base.sh`). It goes into the recipe hash as `templates`, so every change of
a template (e.g. bumping `PLAYWRIGHT_VERSION` or the Node pins) makes built boxes stale.
`test_box_stacks.py` compares the directory checksum with the stored value — a template change without bumping the constant does not
pass the tests. After a change: the new checksum into `STACKS_VERSION`, then `tide box build <slug>`.

**Limitations.** The Node archive and `dotnet-install.sh` are checked with a checksum from the same source (integrity, not
an independent pin); runc has its checksum pinned in the repository. The .NET SDK requires an exact number in the profile
(e.g. `dotnet.sdk: ['8.0.415']`); profiles with the shortcuts `8`/`8.0` must be completed and approved again through
`tide box approve`. Changing the SDK number changes the recipe hash, and changing a template — `STACKS_VERSION`.
The profile's `verify` runs after the templates, `apt`,
`setup` and `docker pull` (build step 8).

Tests: `daemon/tests/test_box_stacks.py` — the script text (the rules above, `shellcheck` if present), file functions
after `source` in a temporary directory (the `dockerd` drop-in compared with `netenv.proxy_env`, `daemon.json`,
`NuGet.Config`, stack variables, resolving Node and SDK versions), a build on the `incus` fake with the real `*.yaml`
and the real `ProxyService` from #5 (the domain list before the first `incus exec`, `TIDE_BOX_*` in every `exec`).
The first real build and measurements (build ≤ 15 min, reset ≤ 60 s, Docker with the proxy in the box): `bash
scripts/box/stacks-trial.sh` outside the sandbox — trial profiles python-qt (`plasma-tide`) and dotnet + docker
(`tide-stacks-test`), a temporary `restricted.snapshots=allow` in the project (reverted at the end), the log
`docs/box-prototype/pt192-<time>.log`; trial boxes and profiles are removed after the trial.
The script returns code 1 after a failed build, check, reset, an exceeded time threshold or a cleanup error.
When removing a box fails, it keeps its trial profile so the cleanup can be retried.
Measurements use a decimal point regardless of the host's locale settings.
If changing the snapshot setting requires a password, a trial without a terminal uses `sudo -A` with `SUDO_ASKPASS` or
a detected `ksshaskpass`/`ssh-askpass`. The password is typed in a system window; it goes neither through the script's stdin
nor to the trial log. The same mechanism works when reverting the setting, also after the sudo consent has expired.
Without a terminal and a helper only `sudo -n` remains (the lack of prior consent aborts the trial before the profiles).
A failed read of the initial snapshot setting also aborts the trial before it changes the project.
The Docker check after the build verifies `docker info` and the local `testcontainers/ryuk:0.11.0` as the agent.
The image is pulled by the builder while the proxy and the build policy are running; after the build the CLI proxy is already stopped.
The script then does not try to pull an extra `hello-world`.
`bash scripts/box/stacks-acceptance.sh` runs the trial outside the sandbox and the full `make test`, also
when the trial fails. The final result keeps the error of any part; the full output is in the log of the
command outside the sandbox.

Confirmed PT-192 results on Incus 6.0.0: python-qt `clean` in 58.8 s, dotnet + docker `clean` in 76.5 s,
reset preserving `/home/agent` in 1.5 s; both build denial logs empty. The full `make test` passed.
Hashes, versions, proxy checks and measurement sources: [first template build](box-prototype.md#first-build-of-the-stack-templates-pt-192).

### Outbound proxy (PT-168)

`tide_daemon/box/proxy.py` is the box's only way to the network and to the host's Ollama: nftables let through from `incusbr0`
only `<bridge address>:<box.proxy-port>`, the box has no DNS or NAT. The proxy runs in the service, in its own thread
with an asyncio loop, and never changes nftables or calls `sudo` (a test searches the service code).

**Start.** Settings in the `box` section of `$XDG_CONFIG_HOME/tide/cardflow.json` (`box/settings.py`):

```json
{"box": {"enabled": true, "proxy-port": 18080, "bridge": "incusbr0"}}
```

The proxy starts with the service only with `enabled: true` (off by default; a bad value — off), when the
`incus` command exists and the bridge has an address (`incus network get incusbr0 ipv4.address`; from the restricted
`incus-user` project this command is sometimes unavailable — always on this computer — then the fallback is the interface address
`ip -j -4 addr show dev incusbr0`, the same one the nftables rules let through). The fallback is weaker than reading
from Incus: it only checks that the interface named in `box.bridge` is a bridge in the kernel (`ip -j -d link show`,
`linkinfo.info_kind == "bridge"`), so `"bridge": "wlan0"` or `eth0` gives a start refusal, but another bridge
named directly in the settings (e.g. `virbr0`) will pass — then its guests get 403 for everything (no policy).
Checking that the Incus profile attaches boxes to this bridge (`incus profile show default`) is deferred: we do not know
whether `incus-user` sees it. It listens **only** on this
address: `0.0.0.0`, `::`, loopback and an address other than the bridge's → start refusal. Every obstacle (no Incus,
no bridge, a bad address, a busy port) is an entry in the service log, and the service goes on as without a box.
`--no-box` disables the proxy regardless of settings. `proxy-port` must equal `BOX_PROXY_PORT` from `make box-install`.
PT-292: before listening, `start_service` reads the installed rules `/etc/tide-box/tide-box.nft`
(`box/nftcontract.py`, read-only; `TIDE_BOX_NFT_FILE` in tests) and refuses to start when the "Tide proxy" rule
of the `inet tide-box` table does not let through exactly `box.bridge` → `<bridge address>:<box.proxy-port>`, or the file is missing.
The `box` section is also written by `SetCardFlowSettings` (strict validation, port consistency with the rules) — docs/box-cli-v1.md.

**Per-box policy, keyed by IP address.** `set_box_policy(ip, domains, ollama, denied_log_path, *,
full=False, box="")` sets (or replaces) the policy of the box with address `ip`; it takes effect from the next connection,
without a proxy restart (open tunnels are not interrupted). It is called by a step in the box (PT-181) at step start
and by the builder (PT-167) before the first build `incus exec`; `box.proxy.running()` returns the proxy started with the service
(after `stop()` — `None`).
An unknown address = no policy = every request 403 (an entry only in the service log). The IP key is safe
**only because** the builder sets `security.mac_filtering`, `ipv4_filtering`
and `ipv6_filtering` on every box's NIC (Incus drops packets with a foreign source address) — the proxy cannot check this itself;
"spoofing the IP of another box" is on the PT-191 red list.

The list is composed by `compose_policy(project, cards)`: the model domains (`MODEL_DOMAINS`: `api.anthropic.com`,
`statsig.anthropic.com`, `api.openai.com`, `chatgpt.com`, `auth.openai.com` — added by the service, not the profile)
+ `templates.work_domains` of the project profile (`base`, templates per `stacks`, `network.domains` — the build list
from the builder without the templates' `build-domains`, PT-192) + the profiles of active cards merged with the project profile by `profile.merge` (a card's red items
only with `accepted_red`). The service never adds `github.com`, `gitlab.*`, internal addresses or
`*.firma.example` on its own (a test). A card's `network.full` without `network.full-consent: true` is skipped with an entry in the service
log; with explicit consent and `accepted_red: [network.full]` it gives `full=True`: every valid name and a bare
**public** address. Full network does not open the local network or the host address on the bridge — this is a separate red class: a target
outside the list is always treated as public, so `CONNECT 10.173.184.1:443`, `10.20.3.10:443`
or `nas.lan:443` (resolved to the local network) get 403, unless that target is separately on the list. `host-ollama: true` in the project or card profile gives `ollama=True`.

**CONNECT without MITM.** Port 443 only; an exact match or `*.domain` (subdomains only:
`*.npmjs.org` does not match `npmjs.org`, `npmjs.org.attacker.com` or `evil-npmjs.org`). Names are compared
in lowercase and without a single trailing dot; a name whose last label is numeric or hexadecimal (`10.1`,
`3232235786`) is not a name. A bare IP address passes only when it is literally on the list. The name is resolved by the
proxy, which connects to the address it has just checked: loopback, link-local, unspecified and multicast — never;
private and 100.64/10 — only for a target that is on the box's list and that the profile considers internal
(an accepted red card item, `BoxPolicy.internal`), so a public name that suddenly points into the local network
does not pass (403 with kind `ADDRESS`). After `200 Connection established` the proxy only
copies bytes in both directions.

**Plain HTTP.** To a foreign domain (`GET http://…`) — always 403. There is no "plain HTTP to listed domains" variant
(`box.proxy-plain-http`): apt goes over HTTPS (decision `apt-przez-https`, `docs/box-decisions.md`).

**Host Ollama through the proxy.** A request in relative form (`GET /api/tags`, i.e. `http://<bridge>:<port>/api/tags`
from the box) or absolute to the proxy's own address goes to `127.0.0.1:11434` — only for a box with `ollama=True`
and only for the paths `/`, `/api/…`, `/v1/…` (the rest 404; `/anthropic/…` and `/chatgpt/…` are K3 routes below). The proxy sets `Host:
127.0.0.1:11434` and `Connection: close`, drops hop-by-hop headers, and passes the response byte for byte without
buffering (the `/api/generate` stream arrives in fragments). A target other than `127.0.0.1:11434` is not possible —
the proxy is not an HTTP relay. `ollama=False` → 403 and a `host-ollama` row. There are no nftables changes at runtime.
`models/boxconfig.for_box(data, bridge, port)` makes from the host's `models.json` a version for the box: host Ollama endpoints
(`127.0.0.1`/`localhost`/`[::1]` on 11434) → `http://<bridge>:<proxy port>`; other endpoints, providers and `tokenFile`
do not get into the box, and roles pinned to a skipped endpoint disappear (the default role applies).

**Denial log.** A denial is a 403 and a TSV row in `denied_log_path` (file 0600) — in a card step
`proxy-denied.log` in the directory of the current step, in a build the path from the builder (the `box.build` event in the Board
journal):

```
2026-11-04T15:02:11+01:00	tide-proto	10.173.184.23	example.com	443	CONNECT
```

Fields: time, box (the name from `box`, otherwise the address), the box's IP address, domain, port, kind (`CONNECT` — a target outside
the list, `ADDRESS` — a target from the list resolved to a disallowed address: loopback, link-local, a local network for a
public target; adding the domain will not help here, `HTTP`, `OLLAMA` with the domain `host-ollama`, `K3` and `K3PATH` with the domain
of the route's host — below). Only the host name from the request target — never a path, parameters, headers, the dummy or the body;
control characters replaced with `?`. Turning a denial into a step result (`denied` with `command: "network: <domain>"`) is done by
PT-181, not the proxy.

### Agent credentials: K3 (PT-175)

The P-3 result (`docs/box-prototype.md`): K3 is possible for Claude Code and for Codex, so the box contains no
real agent credentials — neither `~/.claude/.credentials.json` nor tokens in `~/.codex/auth.json`.
`box/k3.py` is the single place of state: `k3_active(program)` returns `True` for `claude` and `codex` (read by a step
in the box, `tide box login`, the card journal #18 and the consent pack #9); `login_programs()` — programs to log in
in the image (K1) — is empty, so `tide box login` (#4) skips both programs. The agent's internal sandbox
in the box stays unchanged (`sandbox.enabled`, `denyRead: ["~/.claude", "~/.codex"]`).

**Dummy per step.** A step in the box (PT-181) at start calls `box.proxy.running().grants.issue(step, box ip,
program)` and passes in `in/env` the result of `k3.step_credentials(grant, "http://<bridge>:<box.proxy-port>")`:

- Claude Code: `ANTHROPIC_BASE_URL=http://<bridge>:18080/anthropic` and `CLAUDE_CODE_OAUTH_TOKEN=<dummy>`
  (`sk-ant-oat01-tide-k3-<256 random bits>`);
- Codex: a `~/.codex/auth.json` file with dummies only (an unsigned JWT with a random `tide_k3`; from the host file
  only the account identifier and plan pass through — identifiers, without them Codex rejects the account) and the arguments
  `-c chatgpt_base_url=…/chatgpt/backend-api/ -c model_provider="tide" -c model_providers.tide={ … base_url =
  "…/chatgpt/backend-api/codex", wire_api = "responses", requires_openai_auth = true }`. Without its own provider
  Codex calls the model through CONNECT to `chatgpt.com` (P-3) — with a dummy that gets nothing anyway.

At the end of the step `grants.revoke_step(step)`; the dummy also expires by itself after `ttl` (24 h by default). The dummy works only
from the IP address of the box it was issued to, only on its program's route and only until revoked; the registry holds
the SHA-256 hash of the dummy, not the dummy itself.

**Injecting routes in the proxy.** A request in relative form (or absolute to the proxy address) `/anthropic/<path>`
→ `https://api.anthropic.com/<path>`, `/chatgpt/<path>` → `https://chatgpt.com/<path>`. In order:

1. the box must have a policy (an unknown address → 403 `K3`);
2. exactly one dummy in `Authorization: Bearer` or `x-api-key`, valid for this IP address and this route —
   otherwise 403 `K3` (also a dummy after `revoke_step`, after `ttl`, from another box, from the other route);
3. a path from the list from the P-3 log — otherwise 403 `K3PATH` (no general access to the account API, "confused deputy"):
   Anthropic `/v1/messages`, `/v1/messages/count_tokens`; ChatGPT `/backend-api/codex/…`,
   `/backend-api/wham/accounts/check`, `/backend-api/wham/settings/user`, `/backend-api/ps/plugins/…`,
   `/backend-api/ps/mcp`, `/backend-api/plugins/featured`. A path with `..`, `.`, `//`, `%` or characters outside
   `[A-Za-z0-9._~/-]` does not pass; a query after `?` only from the characters `[A-Za-z0-9._~!$&'()*+,;=:@/?%-]` —
   otherwise 400. Even earlier, every request other than `CONNECT` has a method that is an HTTP token, the version `HTTP/1.0`/`1.1`
   and a target of visible ASCII (`0x21–0x7E`), otherwise 400 — a bare LF/CR/TAB in the request line cannot add headers
   (`Host`, `chatgpt-account-id`, `Content-Length`) before those inserted by the proxy;
4. the body of one request by `Content-Length` or `chunked` (both at once, different lengths, another encoding → 400;
   the frame size only from hexadecimal digits, without `0x`, a sign, `_` and spaces — otherwise the connection is closed);
   nothing after it (a pipelined second request) goes further;
5. the host token read **on every request** from `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`;
   `expiresAt` in the past → error) or the host's `~/.codex/auth.json` (`tokens.access_token`, `account_id`).
   These are files of the host's home directory, not of the box (`~/.local/share/tide/box/<slug>/home`) — they are never mounted;
6. headers from the box: `Authorization`, `x-api-key`, `chatgpt-account-id`, `Cookie` and connection headers are
   dropped; the proxy adds `Authorization: Bearer <host token>` (Codex also `chatgpt-account-id`), the `Host`
   of the route's host and `Connection: close`; for Claude it adds `oauth-2025-04-20` to `anthropic-beta` (a subscription
   token does not work without this beta). The request line, the order and content of the other headers, and the body — unchanged
   (a test compares bytes);
7. the connection to the route's host as for CONNECT (the address checked after resolving, public only), the client's TLS
   with certificate verification in `k3.open_upstream`; the `proxy.py` module has no TLS — on the box side there is no
   interception. The response flows byte for byte without buffering (SSE).

**Token refresh only on the host.** The proxy does not refresh the token and neither does the box (P-3: 0 requests to the OAuth
endpoints). The token is refreshed by the host's Claude Code / Codex during ordinary work in the Console; since the proxy reads the file on every
request, a refreshed token works from the next request without a step restart. A missing file, bad JSON or an expired Claude
token → 502 with a description without the file content and an entry in the service log; **there is no silent fallback to K1** (the proxy copies
nothing to the box, and the step gets no other key).

**The token never in a log or in the box.** Error messages carry only the kind of problem; the denial log — only the
route's host and the kind; `Grant.key` does not appear in `repr`. A test searches the `box/` code: only `k3.py`
touches the host credential files.

**Residual K1 risk with Docker.** In a box with Docker the `agent` user is in the `docker` group, i.e. has
practically the box's root rights: through a container it can mount the box's rootfs and read every file
in `/home/agent`, regardless of the `denyRead` of the agent's internal sandbox and of the P-2 result. Any test code
run in such a box can do the same. With K1 (separate login) these would be `/home/agent/.claude/.credentials.json`
and `~/.codex/auth.json` with real tokens. With K3 these files do not exist: code in the box can read only the dummy
of the current step, which works only through the proxy, from this box, on the listed paths and until the end of the step — it can
therefore call the model on the user's account during the step (the same as the agent does), but it will not carry the token out or
use the dummy after the step or from outside the box. Were K3 turned off for a program (`K3_PROGRAMS`), the full K1 risk returns:
the only boundary for credentials is the box, and the proxy's domain list limits only where a leak can go.

**Model domains.** The `MODEL_DOMAINS` list does not change in PT-175 (`chatgpt.com`, `auth.openai.com` are still on it,
although with K3 Codex does not need them) — removing them is a change of the domain list, a separate decision.

### A step in the box: BoxRunner (PT-169)

`box/runner.py` — `BoxRunner`, the second `Runner` next to `HostRunner` (`tasks/runner.py`, the same `start`, `kill`,
`env` contract). It runs the step's agent program in the project box (`box_name(slug)`) or in the card copy (`per_card=True` →
`card_box_name(slug, card)`) as the `agent` user (uid/gid `box.agent-uid`/`agent-gid`) under the same paths
as on the host. The engine does not choose a runner yet (#18): the engine still gets `HostRunner`.

**Command.** The only thing on the host (visible in `ps`) is:

```
incus exec <pudełko> --project <p> -T --user 1000 --group 1000 --cwd <worktree> --
  /usr/bin/env -i TIDE_STEP=<katalog kroku> XDG_RUNTIME_DIR=/run/user/1000
  /usr/bin/systemd-run --user --unit=tide-step-<id>.service --collect --wait --quiet -p CPUWeight=40 -p IOWeight=40
    --working-directory=<worktree> --setenv=TIDE_STEP=<katalog kroku> -- /bin/sh <katalog kroku>/in/bin/tide-step <argv agenta>
```

The step is a service of the `agent` user, not a scope — decision `krok-jako-usluga-user` (P-9): a process in a scope dies
together with the `incus exec` client, i.e. with a Tide service restart; a service survives, and the client with `--wait` ends with the
unit's code. Nothing from `in/env` goes to argv or to the `--env` of `incus exec`; the `incus` client gets only the service's `HOME`,
`PATH`, `LANG`, `USER`, `XDG_CONFIG_HOME`, `XDG_RUNTIME_DIR`, `INCUS_DIR` and `INCUS_CONF` (and the fake's test hooks
`TIDE_FAKE*`/`TIDE_INCUS_BIN`). The client's output: `out/box-exec.log`.

**Writing to the step directory.** `<repo>-worktrees` is rw in the box, so the step directory, `in`, `out` and every file may
at start time be a link planted from the box (the agent, Codex, the box's root). The runner does not call
`Background.prepare()`: `StepDir` opens every component from `<repo>-worktrees` downward through `dir_fd` with `O_NOFOLLOW`
(directories are created by `mkdir` without following), opens the step files (`settings.json`, `gitconfig`, `ignore`, `prompt.txt`)
with `O_NOFOLLOW|O_NONBLOCK` and before truncating checks `fstat`: a regular file, one link, owner — the service;
`out/box-exec.log` the same, appended; `in/env` and `in/local-token` are created fresh (`unlink` + `O_EXCL`) through the same
chain; `in/exit-code` is removed through `dir_fd`. A link, directory, FIFO or hard link in the way →
step refusal, the target untouched. A bare `lstat` before writing is not enough (a race with a process in the box).

**`in/env` and `tide-step`.** `start` writes `<step>/in/env` (a fresh file `O_EXCL|O_NOFOLLOW`, 0600, owner —
the service; an old file or link is removed first) in `KEY=value` lines (a newline in a value → refusal):
the whole environment of the program from `agentcmd.background` (`HOME=/home/agent`, `USER=agent`, the box's `PATH`, `LANG`,
git variables and a dead D-Bus as on the host), the local endpoint's secrets (`secret_env`), `HTTPS_PROXY`, `HTTP_PROXY`
(and lowercase) to `http://<bridge address>:<box.proxy-port>`, `NO_PROXY`/`no_proxy` = `box_no_proxy(<bridge>)`,
`TIDE_CWD` and the variables from `step_env(step)` (ports #8, K3 dummies #18). The proxy address: an explicit `proxy_base`, otherwise the proxy
running in this process, otherwise `incus network get <box.bridge> ipv4.address`; none — the step is refused.
`env(step)` returns the same without the secrets. The Claude Code token for a local endpoint: a 0600 copy in `in/local-token`
read by `apiKeyHelper` (the host file is not in the box). Node (nvm) and the Playwright Chromium come from the box
image, not from the host: `box_step` zeroes the step's `node_home` and `chrome_bin` (the engine sets them from the host's `~`), so `PATH`
starts with `BOX_PATH`, and `CHROME_BIN` does not get into `in/env` or `env(step)`.

The wrapper `daemon/tide_daemon/box/tide-step` (POSIX sh) is copied by the runner through `StepDir` to `in/bin/tide-step`
of every step, protected against links. Thanks to this, argv and the wrapper have the same version;
the older `/usr/local/bin/tide-step` in the base image is not used by the runner's default path.
This requires neither installing the file as root nor rebuilding the image. The wrapper cleans the environment,
checks `in/env` (a regular file, mode 0600, owner = the current
user — otherwise code 125; `PATH` = `BOX_PATH` set explicitly before the first command), exports the lines
without `eval`, moves to `TIDE_CWD`, creates
`CLAUDE_CODE_TMPDIR` in the box's `/tmp`, runs the agent with `in/prompt.txt` on stdin and the output appended to
`out/step.log`, writes the code atomically to `in/exit-code` (like the host's `EXIT_WRAPPER`; it sits out SIGTERM) and exits with it.

**`BOX_NO_PROXY`** — one constant in `box/runner.py`: `localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal`,
and `box_no_proxy(address)` appends the bridge address (never `*`). It is used by: `in/env` and `env(step)` (here),
`/etc/environment` of the base image (#16) and the `dockerd` drop-in of the `docker.sh` template (#29). The bridge address alone is not
enough: Playwright e2e, Karma and test servers on `localhost` and Testcontainers containers would go through the proxy
(403). Clients without CIDR in `NO_PROXY` hit `localhost` anyway, because Testcontainers map ports to localhost (P-6).

**Mounts** (`ensure_devices`, on every start; adds only the missing ones, under the file lock
`$XDG_STATE_HOME/tide/box/<slug>/devices.lock` — the first two steps at once add them once):

| Device | Source = path in the box | Mode |
| --- | --- | --- |
| `tide-git` | `<repo>/.git` | rw |
| `tide-git-hooks`, `tide-git-config`, `tide-git-info` | `.git/hooks`, `.git/config`, `.git/info` (empty `hooks`/`info` directories created before mounting) | ro, over `tide-git` |
| `tide-wtcfg-<name>` | `.git/worktrees/<name>/config.worktree` — **only with `extensions.worktreeConfig=true`** in the host's `.git/config` | ro, over `tide-git` |
| `tide-worktrees` | `<repo>-worktrees` (worktrees and step directories next to it) | rw |
| `tide-home` | the fallback `~/.local/share/tide/box/<slug>/home` (0700) → `/home/agent` — skipped when the builder has already attached the project or card home (device `home`) | rw |

Mount order: Incus mounts `disk` devices sorted by `path` (`/` first, then lexicographically),
not in the order of `device add` — the overlays lie over `.git` because their paths have `<repo>/.git/` as a prefix. Do not
"fix" the order of adding; a test checks the path prefixes.

**`config.worktree` (D7).** Git reads `.git/worktrees/<wt>/config.worktree` only with
`extensions.worktreeConfig` on. The runner reads the flag from the **host's** `.git/config` (`git config --file`, the file is under an ro overlay —
the agent will not turn it on; a read error → step refusal). Off (the plasma-tide state): no `tide-wtcfg-*` overlays —
a new ticket worktree does not require stopping the box, project steps start in parallel. On: an overlay on every
existing file, and for the step's worktree the service first creates an empty `config.worktree` (the `<worktree>/.git` pointer
must lead directly to `.git/worktrees/<name>`; a link or directory instead of a file → refusal; the file is
created through `dir_fd` with `O_NOFOLLOW` from `<repo>/.git`, because `.git/worktrees/<name>` could be replaced
with a link after the `realpath` check). An overlay
protects only a file that exists at mount time, and every new file requires stopping the box (with other running
steps — refusal). The agent can still create the `config.worktree` of another worktree that lacks this file (e.g. with
`core.hooksPath`); this is detected by the `.git` checksum guard from #7 (PT-170), and the service's own git calls are protected by
`gitcommand.git_argv` (`core.hooksPath=/dev/null`, `core.fsmonitor=false`).

### Ref guard

`box/refguard.py` records, before starting the process in `BoxRunner.start`, a full snapshot of
`git for-each-ref` and copies and SHA-256 sums of the protected files: `.git/hooks/`, `.git/config`,
`.git/info/` (including `attributes`), `.git/config.worktree` and `.git/worktrees/*/config.worktree`.
The ref inventory is supplemented by reading loose symbolic refs, also with a nonexistent target,
which `for-each-ref` skips. Ref lock files do not belong to this inventory.
It covers new and removed files, file modes and links, without reading their targets.
Snapshots are written atomically in `$XDG_STATE_HOME/tide/refguard/<repository hash>/`
(`~/.local/state/tide/refguard/` by default), outside the box's mounts. They survive a service restart.

Start, comparison and restoring use the same repository lock as `gitops` and
the `flock` file lock; the snapshot is taken only after obtaining them. Allowed are the step's
branch and the branches of tickets of the same card active at any moment of its duration.
A new step adds its branch to the older active snapshots. The consent stays in the older
snapshot after the neighbor ends, so parallel endings do not undo each other's commits.
The branch comes from the engine's assignment (`Step.branch`), and the consistency of `HEAD` with this assignment
is checked before preparing and saving the snapshot. A replaced `HEAD` cannot
extend the set of allowed refs; an inconsistency refuses the start.
Branches of other cards, new tags and the agent's helper branches do not belong to this set.
Steps of other cards in this repository wait for the guard to be released; the final check
in the runner refuses the start if two cards nevertheless pass the check at the same time.
Trusted branch creation, merges and worktree cleanup by `gitops` update in active snapshots only
the specific ref/config changed by the service and only when the earlier value matches
the snapshot. The runner also accounts for the creation of an empty `config.worktree` of a new step.
The start of a next step refuses to persist the foreign changes of an active neighbor as a new copy.

The engine checks the guard on every end of a step in the box, also after a stop,
timeout and a program error. It first restores the configuration and files. The scope of ref repair is
only its own branch saved at step start (`snapshot.branch`), also when recovering
an older snapshot. Ordinary commits/reset of this branch are allowed; deleting it or replacing it with a symbolic
ref is reported and repaired. Changes to the other allowed branches of
neighbors are skipped. Changes outside the allowed set are reported **without restoring**:
the snapshot does not allow telling a violation from a legitimate write of another card outside `host_operation`.
The report of such a change still ends the step with an error and leaves the foreign ref untouched.
Repairing its own ref uses `git update-ref --no-deref` with the expected old value;
a change after the comparison causes a CAS error instead of overwriting a newer value.
Replacing its own symbolic ref uses the standard `<ref>.lock`
and rechecks the OID and the target under the lock, without writing to the alias target.
The check also covers the worktree's `HEAD`
and the `.git` pointer; after a violation `HEAD` again points to the step's branch.

A violation records the `refs.guard` event with the fields `refs`, `files`, `head`, `errors`
and forces the result `error` with the reason `refs.guard`, regardless of the agent's result.
A missing or damaged snapshot and a failed repair also end with an error.
A damaged entry in `worktrees/` or an error on a single file is reported with the path;
the other available files, refs, `HEAD` and the `.git` pointer are still checked and repaired.
On a repair error the snapshot stays active, keeps the copy and the list of violations.
Recording `step.finished` does not deactivate a snapshot with an unfinished repair. Recovery
(`recover_network`, also without the proxy) repeats such a check in later ticks from the original
copy, until reading and repair end without errors; it records the later results as `refs.guard`.
During this time other cards keep waiting, and a new step of the same card cannot accept foreign
changes as a restore point. Cancelling without a check concerns only a failed start
without an entry of a started step.
Only a full check without errors persists the final result,
which allows repeating the ending without undoing the refs again.

The `--demo` mode also performs the check after confirming the unit inactive and returns
code 1 after a violation. With an unknown unit state or a repair error it keeps the snapshot
and prints the trial identifier. Repeating the check alone, without rerunning the command:
`python3 -m tide_daemon.box.runner --demo <slug> --worktree <W> --branch <gałąź>
[--card K-N] --finish-guard <demo-id>` (the same project, worktree and card).
The repair uses the original copy; a violation detected earlier still gives code 1 even
after a successful repair. A clean trial releases the guard and keeps the Incus client's code.

`HostRunner` takes no snapshots and runs no check. Every git call of the service,
including `Git`, `FlowGit`, `StepGit`, coordination reads and the runner's, goes through
`gitcommand.git_argv`, which appends `-c core.hooksPath=/dev/null -c core.fsmonitor=false`.

The guard detects changes after a step; read-only overlays remain the boundary preventing
writes to hooks and configuration during the agent's work. Tests: `daemon/tests/test_refs_guard.py`.

Without `shift=true` (the restricted project rejects it): the box gets `raw.idmap "both <uid> <agent-uid>"` (P-4,
`raw-idmap-zamiast-shift`). Changing `raw.idmap` or a new file overlay requires a stopped box (a file cannot be
attached to a running container), so the runner stops it for the time of adding and starts it back — and when according to
the box journal other steps are running in it, the step is refused. From this list the runner excludes its own `step_id`, so
the engine (#18, PT-181) must record `box.use` (`state.mark_in_use`) **before** `runner.start` of every step — otherwise
a running step of a second worktree, whose entry does not exist yet, would die when the box is stopped. After such a restart the runner waits (at most
`box.ip-wait-seconds`) for the `agent` user's service manager (`systemctl --user show-environment` through `incus exec`),
because without it `systemd-run --user` ends with code 1 (in a manual trial the manager came up ~2 s after start); without
the manager the step is refused. A worktree or step directory outside `<repo>-worktrees` → refusal. A stopped box →
refusal with a readable error (the box is started earlier by the engine, below "A step in the box in the engine"). An `incus` error on mounting → `StepError`. A source
equal to the main repository (or above it), to the host's home directory (or above it), or lying in the main worktree
outside `.git` → refusal: the main worktree and the host's `~` never get into the box. A source in the repository or
in `<repo>-worktrees` whose `realpath` differs from the path (a link component, e.g. `.git/hooks` → `~/.ssh`
planted through the rw `.git`) → refusal, because Incus gets the literal `source`. A card copy gets its devices
in its own configuration, the project box stays unchanged.

**Agent settings in the box.** Claude Code: the file `<step>/in/settings.json` (the step directory — host settings
unchanged) with `sandbox.enabled` = `BOX_SANDBOX` (the P-2 result, decision `bwrap-profil-w-pudelku`: `true`, with
`failIfUnavailable`), `filesystem.denyRead: ["~/.claude", "~/.codex", "/packs", "<step>/in/env"]` (+ `in/local-token`),
`allowWrite: [<step>/out, <repo>/.git]` (the ro overlays guard hooks and configuration), `network.allowedDomains: []`
(the network boundary is the Tide proxy), `autoAllowBashIfSandboxed: true`; `--allowedTools` without command lists (`Bash(…)`
drop out, `Bash` is added; project prohibitions stay as hints in the prompt, `--disallowedTools` unchanged). The
`sandbox=False` variant: `sandbox.enabled=false` and one `box.risk` entry ("ryzyko K1" (K1 risk)) through `journal(kind, fields)`
in the card journal. Codex: `--sandbox workspace-write` unchanged, host MCP servers are not asked
(`codex_mcp=()`).

**Stopping.** `kill(step)` calls through `incus exec … --user <agent>` `systemctl --user kill --signal=SIGTERM
tide-step-<id>.service` in the box from the entry (`box`, by default the runner's box), waits `grace` for `is-active`, then
SIGKILL; the `incus exec` client ends together with the unit. The unit and box names are checked against a pattern. The unit
state (`unit_state`) has three values like `stepstop.unit_state`: `active` (code 0), `inactive` (codes 3 and 4
of `systemctl is-active`, passed through `incus exec`) and `unknown` (any other code: an `incus exec` client error,
an unavailable incusd, a stopped box, a timeout). `kill` returns True only for `inactive` — with `unknown` `stepstop`
contributes no stop and makes no WIP commit; `alive` with `unknown` returns True.

**Manually** (outside the sandbox, on a box from #1/#4): `python3 -m tide_daemon.box.runner --demo <slug> --branch <gałąź> [--worktree W]
[--card K-N] -- <command>` — a synthetic step `<worktree>.demo-<time>-<random-suffix>.step` with a command instead of an agent and the marker
`TIDE_DEMO_SECRET` in `in/env` (to check that the host's `ps -o args` does not show it); it prints `step.log` and the code.
Tests: `daemon/tests/test_box_runner.py` (the `incus` fake, `tide-step` locally through `/bin/sh`).

### Logs and dependency preparation (PT-189)

Besides the host roots, `worktrees/sessions.py` reads the project homes in
`$XDG_DATA_HOME/tide/box/<slug>/home` (`~/.local/share/tide/box/<slug>/home` by default):
`.claude/projects`, `.codex` and `.codex/steps/*` for K3 sessions. Discovery is repeated on refresh.
Per-card copies have separate homes `<slug>/cards/<card>/home`, scanned on the same rules.
The shared reader `box/logfiles.py` opens every component of a box path relative to a directory descriptor
with `O_NOFOLLOW`, also after the parent is replaced between detection and reading. The protection covers Sessions,
Agents, KRunner, coordination, reports and JSONL indexes. We do not resolve box paths through `realpath`.
We skip box SQLite databases (SQLite itself opens the WAL by name); titles come from `session_index.jsonl`
or the log. Host homes keep their handling of the snap `current` link.
The scanner joins Claude copies by identifier, and Codex ones by the relative
path of the log file, keeping separate segments. The session's working directory stays the worktree path,
so linking to the card step requires no path rewriting. The search for a log for a handoff
(`tasks/stepstop.py`) uses the same additional roots.

**Migration from the old volume.** `Builder.ensure_home`, before a step, a rebuild and a reset, checks
the home mount. An atomic device change reads the configuration from `incus list --format json`, keeping
the writable instance fields and the multiline `raw.idmap`, without the YAML parser's limits.
For the old `<box>-home` it stops the instance (with no active steps) and runs
[`incus storage volume export`](https://linuxcontainers.org/incus/docs/main/howto/storage_backup_volume/)
with `--volume-only --compression=none`. Another instance using this volume blocks the migration.
The export stays in a private `.home-migration-*` next to the home. Unpacking accepts only `backup/volume`,
rejects paths leaving the copy, links as file parents and special files.
Symlinks are created at the end, without following them. The owner of the copy is the service user;
content, symlinks, file times and ordinary permission bits are preserved, without UID/GID, suid and special attributes.
The `home-migration.json` record allows resuming publication after a crash; a published home is not overwritten by a repeated
export. A non-empty home without this record means a conflict and a refusal. The device change is atomic;
an error does not remove the old mount. On a migration error the instance may stay stopped.
The old volume, export and home also remain after `destroy`. Resetting an old snapshot attaches the host home again,
without undoing the new logs. `BoxRunner` and `engine._launch` do not take part in the migration.

`tasks/preparation.py` is called by `boxstep.configure_background`, after the mounts and proxy configuration.
`node.install` and `dotnet.restore` receive a runner from the existing `Placement`; the commands are run by `incus exec`
as the `agent` user, with the worktree directory and the box environment. Environment values, including the proxy identity,
pass through a private file `in/prepare-env` (0600), removed after the command; they do not get into the incus argv.
NuGet uses `/home/agent/.nuget/packages`, the CLI `/home/agent`, Node is looked up inside the box,
and Chromium in `/home/agent/.cache/ms-playwright`. The preparation result goes to `out/prepare.log` and the prompt.
A failure does not start a substitute installation on the host. The behavior of `HostRunner` and of the existing
`subprocess.run` fakes stays compatible with the earlier interface.

Codex K3 stores sessions in `/home/agent/.codex/steps/<hash of the original step directory>`;
resuming uses the same home. A rootfs reset does not remove the data of the home device.
The real Sessions visibility test and a screenshot remain to be done after #28.

### Command and the red list: `tide box run` (PT-191)

`python3 daemon/tide.py box run <slug> [--card K-N] [--worktree /path] -- <command>` runs
a command without an agent through `BoxRunner.start`. Splitting at the first `--` preserves every argument, including
spaces, empty arguments and options. The CLI's exit code is the command's code; preparation or the ref check
may refuse with code 1, no ports/a busy box — 3. `Ctrl+C` tries to stop the unit and gives 130.
The environment is built from scratch, without the host's agent tokens, with the proxy, the full `NO_PROXY`, ports from
`box/ports.py` of PT-171 and git safeguards. The `in/env` values (0600) are not arguments of the Incus client.
The CLI does not log in the agent, does not issue a K3 grant and does not read the host's MCP configuration.

The repository must correspond to the project slug on the Board. By default the command runs in the current worktree.
From the main directory a worktree `<repo>-worktrees/demo-…`, a branch `box-run/demo-…` without an upstream
and a temporary step directory are created. The worktree and logs remain for inspection, the paths go to stderr. The mounts
are the same as in a step, including the ro overlays on the `.git` configuration and hooks; the main worktree is not mounted.
`--card` respects `box: per-card` and attaches accepted, already prepared PT-173 pack copies as
`/packs/<name>` ro. The CLI does not copy user sources; a missing copy, a link in the path or packs of another
card mean refusal. After the unit's end is confirmed the packs are detached.

The CLI uses `base.ensure_proxy`, i.e. the same `ProxyService` instance in the process or its own proxy
on the bridge's static port, like `tide box build`. It does not control another process's proxy: when the port is held by a
running daemon, it refuses instead of choosing another port or bypassing the policy. It requires an idle box
and holds its lifecycle lock for the duration of the command. Domains come from `compose_policy` of the project and card,
and `boxaccess` gives the journal identity without the agent token. After the client is lost, an unknown unit state
keeps the reservations; the next `run` retries recovery after confirming `inactive` and releasing the
inherited life lock from PT-171. The PT-170 ref check also applies to a plain command.

`make box-check BOX=<slug>` calls `scripts/box/check.sh` through `tide box run` and prints a table
"punkt → odmowa/przeszło/nie dotyczy" (item → refused/passed/not applicable). The red list covers the host's SSH and git-credentials, the main worktree,
HTTP(S) with and without the proxy, DNS, the host's Docker and D-Bus, push, a missing gh, the host's PID namespace, writing to `/etc`,
the host's Postgres, IP spoofing and a separate proof from the agent's Bash. Every "przeszło" (passed) gives code 1. A missing program,
origin, permission to assign an IP or a real agent Bash is an explicit "nie dotyczy" (not applicable), not a counted refusal.
`CHECK_ARGS="--host-ip <host-IP> --spoof-ip <verified-free-IP>"` gives the trial addresses; the optional
`--spoof-image <local-image-with-ip-and-curl>` allows assigning an IP through Docker `--privileged --net=host`
without pulling an image. The script sends `curl --interface` only after a successful `ip addr add` and removes the address
in a trap. HTTP 403 also means "passed": the packet reached the proxy. On the host one must confirm there is no entry
from the trial IP. Docker without K3 means "residual K1 risk" for Claude credentials; the remaining variant requires
checking `cat ~/.claude/.credentials.json` with the real agent's Bash tool according to P-2.

The full proof and the rules of the first step: [First step in the box](box-prototype.md#first-step-in-the-box).
Tests with fakes do not confirm the isolation of a real Incus. `scripts/box/boundary-trial.py` collects
the CLI trial, `ps -o args`, the table and the full `make test` outside the sandbox; a separate test card still requires
an approved plan, a journal, a runner and a step time without `envAnswer`/`outside`.
The `--prepare-missing` option of this script recreates the missing environment: it leaves an existing box and profile,
builds only a missing box, and when the profile is missing approves the explicit
`docs/agents/pt-191/plasma-tide-proof.box.yaml`. The trial tool packages are installed in the box, without changes to host
packages and settings. The default invocation of the script neither approves a profile nor builds a box.
With an older configuration of the Incus project a separate, explicit option `--enable-snapshots` sets through sudo
`restricted.snapshots=allow`, in line with the PT-182 installer (persistently, after a read and with a check of the result).
`--retry-failed-build` retries only a box in the build-error state with a hash matching the profile and without steps,
keeping the builder's safeguards. It does not replace an existing profile or rebuild the clean box.

### A step in the box in the engine (PT-181)

`cardflow/boxstep.py`, called from `FlowEngine._launch`, `_poll`, `_kill` and `_finish`. The proof on a real box
(`tide box run`, the red list, the first real step) is in #28.

**Which steps go to the box.** The constant `BOX_STEP_KINDS` in `cardflow/engine.py`: `ticket`, `fix` (fixes after
review), `review` and `close` (card close-out) — steps that run project code. Planning in every variant
(`planning`, `plan-fix`, `plan-amend`, `plan-revise`, `plan-review`, full planning in the Console) and `conflict` stay
on the host in bwrap (`HostRunner`), because they read the repository and the Board. A resume (`parentStep`) does not choose anew:
it inherits the runner of the resumed step from its entry (a host step stays on the host even when the box is already ready;
a box step stays in the box, and when the box is disabled or the project lost its profile — a card error `box`, never
a silent return to the host).
The specific `box` instance is inherited too: changing the profile `per-project` ↔ `per-card` does not move
a resume. A missing stored instance means waiting, an inconsistent identity — an explicit error; the engine does not create a substitute
copy for a lost environment.
A synthetic trial ended with `local-unavailable` also keeps the runner and instance fields, so a retry after
the executor returns stays in the same box.

**Choice** (a kind from `BOX_STEP_KINDS` or a resume of a box step), in order:

|State|Result|
|---|---|
| `box.enabled: false` (PT-289) | `HostRunner` (as before K-38) before all the checks listed below: no read of `boxWait` (also `environment` and `rebuild`), of the PT-264 project exposure, profiles, repository reservations, admission and Incus. A resume of a box step (`runner: box`) does not move to the host: `BoxError`, nothing is started |
| `boxWait: environment` or a deferred consent with the box disabled (PT-290) | neither an executing step nor its resume starts: `step.waiting` "czeka na środowisko" (waiting for the environment); also without a profile or with a host parent; the deferral becomes `boxWait: environment` (`source: box-enabled`); planning stays available |
| project `plasma-tide` (exposure v1, PT-264) | an executing step waits for the project manifest and the received mounts |
| a project without an approved profile | `HostRunner` (as before K-38), unless the card is already waiting for `rebuild` — then it keeps waiting |
| box build or reset in progress | the step does not start: "czeka na pudełko (budowa)" (waiting for the box (build)) |
| there is no box (Incus), no `boxWait: rebuild` and no failed build in the box journal | `HostRunner` |
| there is no box after a build error or with `boxWait: rebuild` | still `boxWait: rebuild`; a missing instance after a failed rebuild does not move the step to the host |
| no NIC filtering, no `clean` snapshot, a mismatched hash of the instance or snapshot, or a build error | `boxWait: rebuild` (the `box.wait` event with `why`), the step does not start: "czeka na pudełko (do przebudowy)" (waiting for the box (needs rebuild)); the engine checks the `user.tide.recipe` stamp of the snapshot configuration from `incus snapshot list` also for an existing card copy; a rebuild is approved with the plan (PT-172) |
| `box: per-card` | `Builder.copy_for_card` at the card's first step (when there is no copy), the step in `card_box_name(slug, card)` |
| the box (or copy) is stopped | `boxWait: start`, `incus start`, `incus exec <box> -- true` until it works (at most `box.ip-wait-seconds`), the `agent` service manager, the IPv4 address; then `boxWait` is cleared |
| ready | `BoxRunner` |

A `step.waiting` from the box has `cause: "box"`; the engine retries the start in every `tick`. `boxWait: rebuild`/`start` is cleared by
the engine when the box is again fit for a step. A step in the box does not run `dotnet restore` or `npm ci` on the host
(packages are installed by the agent in the box through the proxy) and does not get `networkDomains`.

**Step entry.** The `intent`/`result` of the `step-start` action and `step.started` of a step in the box have the fields `runner: "box"`,
`box` (the instance), `boxSlug`, `boxIp` and `boxAccess: 1` (a required record of the network context); a host step does not have them
(the format is unchanged). After them the engine — also after
a service restart, when there is no process handle — checks the step (`BoxRunner.step_state`: `incus exec … systemctl --user
is-active tide-step-<id>.service`), finishes it with `result.json` and the code from `in/exit-code` and stops it (`stepstop`:
`BoxRunner.kill`, then a WIP commit with git in the sandbox on the host as today).
The end of the `incus exec` client does not end the step: the unit may still be working. The state `active` keeps the step,
the box occupancy and the proxy/K3 access, and `unknown` does not prove the end. Only `inactive` allows taking the result
and the code from `in/exit-code`; the client's code does not replace the step's code.

**Proxy policy.** Before `BoxRunner.start`: `set_box_policy(box ip, domains, ollama, log, full, box)`
with `proxy.compose_policy(project profile, card profiles)` — the cards are the current one and every card with a running step in this
box (a card without its own profile adds nothing). Every step has a random identifier in the
`HTTP_PROXY`/`HTTPS_PROXY` address (and the lowercase equivalents), passed by the client as
`Proxy-Authorization: Basic`. `register_step` binds the identifier to the IP address and the step's denial log:
`$XDG_STATE_HOME/tide/box/<slug>/steps/<step>/proxy-denied.log` — outside the step directory, which is rw in the box
(the proxy appends to it as the service, and a forged entry would change the step's result). Right after that `box.use`
(`state.mark_in_use`) — before the start, because BoxRunner stops the box for new mounts only when according to the box journal
nothing is running in it; a failed start records `box.release`. After the step: `box.release` and the policy set
anew for the remaining steps in the box or `clear_box_policy` when there is none. Traffic without a valid
step identifier (e.g. processes of a shared Docker) goes to the box's separate journal
`$XDG_STATE_HOME/tide/box/<slug>/proxy-denied.log` and does not change the result of any step.
No running proxy = the step does not start (`StepError`).

**K3 and restart.** Before starting a cloud program with `k3_active(program)` the engine issues a dummy
`GrantRegistry.issue` and passes `step_credentials`: Claude's variables to `in/env`, and for Codex the route arguments
and the `auth.json` file in a separate `CODEX_HOME` of the step's session (a resume keeps this directory). Keys do not go
to argv. After the end or a failed start `revoke_step` invalidates the dummy and the proxy identifier. The private file
`steps/<step>/access.json` (0600, outside the box's mounts) stores only the dummies and the approved network
context; never host credentials. A new service process restores from it the policies of active boxes and the same
dummies before monitoring steps. This also applies to a `step-start` intent without a recorded start result:
the unit is checked in the indicated runner, not on the host.
Restoring also reconciles releases on the basis of `step.finished` and a failed result of the `step-start` intent.
A crash after recording the end or before the unit starts does not leave a persistent `box.use`: the next check
retries `box.release`, removes `access.json`, invalidates the proxy identifier and the K3 dummy. A repeated `_finish` also
retries the release without a second result. A failed cleanup is retried without a new event and without a restart.
The policy after restoring comes from the saved contexts of still active steps; when none remained,
it is removed. Releasing the use entry and the context file also works with the proxy unavailable.

**Step test artifacts (PT-174).** BoxRunner supplies `in/bin/tide-test` in the agent's PATH.
The ticket and fix prompt tells it to run tests through `tide-test <command> [arguments…]`,
also the profile's `verify` commands and the families `npm test`, `npx playwright test`, `dotnet test`,
`make test`, `pytest`, `python -m unittest`. A compound command: `tide-test sh -c '…'`.
The box prompt policy allows wrapping an allowed test without a separate consent for `tide-test`:
the consent list is compared with the inner command (for `sh -c` with its body).
Prohibitions take precedence and cover the full invocation and the commands inside the wrapper and the shell.
The wrapper does not extend consent to arguments or chains; an entry in `verify` alone does not grant consent.
There is no global command interception; running without the wrapper gives no artifact.
The wrapper inherits the environment, stdin and working directory, merges stdout/stderr into a log and passes on the exit code.
Every invocation creates `out/tests/<n>.log` and atomically appends a record to the list `out/tests/summary.json`:
`command`, `argv`, `exit_code`, `started_at` (UTC), `duration` (seconds), `log` and
`passed`/`failed` when the parser knows the format (pytest, unittest, VSTest, Playwright, Jest/Vitest/Mocha).
An unknown format gives a record without counters, a parser failure does not change the exit code.
Parallel invocations reserve numbers and update the list under a lock; with no invocations the tests directory is not created.
Artifacts lie in the step directory next to the worktree. After the end the service also copies there
`out/proxy-denied.log`, if there are denials, keeping the source log outside the box.
The reviewer's sheet lists the paths and sizes of the artifacts of the author's last step and the instruction
"świeży artefakt = dowód; nie proś o ponowny przebieg" (a fresh artifact = proof; do not ask for a re-run); with no tests it says "krok bez testów" (step without tests).
The reviewer judges how current the command and time are relative to the change. HostRunner steps keep the existing prompts.

**Sheet by step runner (PT-291, K-38 F3).** The box extras are decided solely by the saved runner
of that step (`placement` in `_launch`), not by the project profile or the author's runner: `handoff.runner_policy(runner, …)`
and `handoff.review_evidence(runner, author)`. A `box` step gets the `tide-test` wrapper, the `verify` commands, the list of the
author's artifacts (only a review in the box after an author from the box) and the box restriction: no `outside` result,
because the engine rejects it anyway (`box-outside`). A host step — also with `box.enabled: true` and after an author
from the box, e.g. a review after the boxes are turned off — gets the master text: the host's `outside` contract, `test-changed`
and the full-test rules unchanged. Compatibility is guarded by `daemon/tests/test_cardflow_host_cards.py`: the
ticket/fix/review/close sheets for the three project classes from F1 are compared, after a narrow normalization (line endings,
trailing whitespace, the temporary directory), with the sheets generated by the master modules `292fb204` loaded
from git objects; patterns and provenance: `daemon/tests/fixtures/host-cards-master/`. The only allowed differences
are the closed list `hostcards.APPROVED` (exact fragments, currently PT-183: the hint "Do sprawdzenia na pulpicie" (To check on the desktop)).

**Proxy denial → step result.** In `_finish` (a step that ended by itself, not stopped) the result is cross-checked with the step's
denial log (the `CONNECT` and `HTTP` rows):

- a `question` whose text names a domain from the log → `{"result": "denied", "command": "network: <domain>"}`
  — the domain question from PT-172 (the `network` field, without `envAnswer`),
- an `error` whose `out/step.log` or `out/box-exec.log` (the tail) names a domain from the log → the same,
- `done` or `error` with entries the result does not concern → no change of the result, a `proxy-denied.md` note
  (written by the service in `flow/<card>/steps/<step>/`, appended to `notes`).

One box IP address = one domain policy, but separate logs of the identified steps. The result and its
log must name the same domain; a connection denial of another card does not change this step's result.

**`box.risk`.** The first step of a card in a box with `docker.enabled` and `k3_active(program) == False` appends to the card
journal `box.risk` (`risk: "K1"`, `box`, `step`, `program`, `text`: "poświadczenia agenta czytelne dla kodu w pudełku
(grupa docker); pełna ochrona: K3" (agent credentials readable to code in the box (docker group); full protection: K3)) — once per card.

**Limitations.** `OUTSIDE_PER_PROJECT` and `heavyLimit` concern only commands outside the sandbox on the host; steps in the box are counted
only by the provider's step limit, so two steps of a project start in parallel. An `outside` result of a box step
does not get consent to run on the host: the block covers new results, rechecking of consents, decisions
and the start of the command itself. The existing `outside*` of host steps work as before.

Tests: `daemon/tests/test_cardflow_execution_box.py` and `daemon/tests/test_stepstop_box.py` (base `boxflow.py`:
the `incus` fake with a real `tide-step`, fakes of the box's `systemd-run`/`systemctl --user` and of the proxy).

## Running

```bash
make install-daemon          # copy to $XDG_DATA_HOME/tide/daemon, systemd service, start
make uninstall-daemon        # stop and remove
journalctl --user -u tide-daemon -f

# Preview without D-Bus (one-off JSON):
/usr/bin/python3 daemon/tide-daemon.py --print
/usr/bin/python3 daemon/tide-daemon.py --print-worktrees      # read-only, removes nothing
/usr/bin/python3 daemon/tide-daemon.py --print-coordination   # coordinators and boards (YouTrack GET only)
/usr/bin/python3 daemon/tide-daemon.py --print-board          # the work board (read-only)
/usr/bin/python3 daemon/tide-daemon.py --print-docker         # containers and disk space (GET only)
/usr/bin/python3 daemon/tide-daemon.py --print-models         # local model servers and roles (GET only)
# One-off morning report (without D-Bus, notifications and the state file) into the given directory:
/usr/bin/python3 daemon/tide-daemon.py --report --report-days 1 --report-dir /tmp/raport
# Diagnostic options: --claude-dir, --codex-dir (can be repeated), --workspace, --worktree-depth, --no-processes,
#                      --no-notifications, --no-krunner, --search-delay 60, --no-tab-marks,
#                      --now 2026-10-26T12:00:00Z, --idle-after 30, --no-idle,
#                      --report-dir, --report-at 07:00, --no-report, --report-delay 120,
#                      --report-summary claude|local|rules,
#                      --limits-poll 120, --no-limits, --print-limits, --no-tasks,
#                      --coordination-days 7, --no-coordination, --no-youtrack (also without Board synchronization),
#                      --board-dir DIR, --no-board, --print-card-docs K-12, --no-docker,
#                      --models-poll 15, --no-models, --no-activity-look
```

Installation files: `$XDG_DATA_HOME/tide/daemon/` (with a copy of the palette `palette.json`, from which the morning report takes colors and fonts), `$XDG_CONFIG_HOME/systemd/user/tide-daemon.service`, `$XDG_DATA_HOME/dbus-1/services/org.tide.Daemon.service` (start on demand when a widget asks and the service is not running) and `org.tide.Daemon.KRunner.service` (the same for KRunner). The service uses `/usr/bin/python3` with the system PyQt6 — `python3` from pyenv does not have it.

## Tests

`make test` (unittest, `/usr/bin/python3`):

- `daemon/tests/test_agents_core.py` — the core on synthetic logs from `daemon/tests/fixtures/` (frozen clock, processes passed explicitly),
- `daemon/tests/test_dbus_contract.py` — the service on a private bus (its own `dbus-daemon` with a configuration without service directories, `privatebus.py`): the same data as from the core, and a state change within 3 s after an entry is appended; without `dbus-daemon` the test is skipped,
- `daemon/tests/test_agents_reply.py` — PT-29: the heuristic for a question at the end of a turn (grilling "Pytanie 10: …?" (Question 10: …?), emoji, bold, a list of variants, a list of questions, a short sentence after the question, "Czekam na odpowiedź" (Waiting for an answer); a rhetorical question in a summary, code and tables — no), Claude Code (waits also after 2 h, the user's answer, a tool after the text, interruption; compaction of an idle session, also larger than the tail, and the "idle" status — keeps waiting, a real message after it clears it; the `/model` echo does not clear it, a skill from a command does; `AskUserQuestion` after compaction; the tail grows up to the last turn, with an upper bound), card K-20 with coordination 1/1 merged and a waiting session — "Czeka na mnie" (Waiting for me), not "Gotowe" (Done), a note under the question, the `shell` status with a question (`reply`) and with `AskUserQuestion` — waits, with no question — works in the background until `BACKGROUND_STALE`, then `idle`, Codex (`task_complete`, the turn of a scheduled task, a user message), one notification per question with its text, Meta+A, card K-20 created and linked to a finished session ("W toku" (In progress), with a question — "Czeka na mnie"; a finished coordinator — "Do sprawdzenia" (To review), "do poprawy" (needs fixes) only after a move from "Do sprawdzenia", `moved-from` from `BoardMonitor`),
- `daemon/tests/test_board_derive_legacy_wait.py` — PT-110: a card of the old flow with a finished coordinator plan and an unmerged branch, whose agent ends a turn with a question with a "?" — a synthetic journal shaped like a real coordinator session (a subagent on a side thread, a queue, attachments, `turn_duration`, `away_summary`) through `AgentScanner` to the column: "Czeka na mnie" with the question (also after `IDLE_AFTER`), after the answer and the report — "Do sprawdzenia",
- `daemon/tests/test_attention.py` — choosing the longest-waiting session and notifications without repeats on fixtures; `Notifier` on a private bus with a notification server fake (`fake_notifications.py`: Notify, the "Przejdź" (Go) button, closing); the whole service: a new waiting session → a notification,
- `daemon/tests/test_tabmarks.py` — session state → card tag (with the process table), marking, state change, restoring, a title change by the user, a closed card, a restart with and without a state file — on an in-memory Konsole fake; the D-Bus backend on a Konsole fake on a private bus (`fake_konsole.py`),
- `daemon/tests/test_windows.py` — switching to a window with several Konsole windows (PT-33): the card's window from `/Windows/*` instead of a stale `KONSOLE_DBUS_WINDOW` (a nonexistent window, a card dragged to another window, a fallback from the environment), window choice by a KWin script run in `QJSEngine` with a `workspace` fake (any window order, a title that is a prefix of another, a window showing another card, a title reaching KWin after the script starts, windows of other processes and abnormal windows), a temporary marker for identical titles (restoring, a user change, a second switch and the previous switch's timers fired separately, a failed script, an error in a timer, a partially failed append, service shutdown), waiting for the marker and the finishing script, the script removal timer (`KWinScripts` on a bus fake) and cooperation with the card's state marker; the window methods of the D-Bus backend on a Konsole fake with windows (`fake_konsole.py`),
- `kwin/tests/` — the KWin script run in `QJSEngine` with `registerShortcut`/`callDBus` fakes, shortcut collision detection, installation and uninstallation with `kpackagetool6`, `kwriteconfig6`, `dbus-send` fakes,
- `daemon/tests/test_worktrees_core.py` — the worktrees core on real temporary repositories (`gitrepos.py`: merged, with changes, with unmerged commits, with an agent, detached, without a main branch, main); git wrapped in a script that records calls, to confirm there is no `--force`, `-D` and network operations,
  Plus the groups (`active`, `clean`, `decide`, `main`, `missing`) with a frozen clock, removing only selected paths and `prune` only for missing directories,
- `daemon/tests/test_worktrees_sessions.py` — titles from branch names and linking to sessions on synthetic logs (the freshest and deepest, kinds of titles, subagents, files older than 30 days, large files, the cache),
- `daemon/tests/test_tasks_core.py` — a new task without D-Bus on real temporary repositories: the argv of both tools (quotes, `$(…)`, newlines, emoji, a leading dash, screenshots) checked as it reaches the agent fake through `launch.py`, the branch name from the command, a worktree from the main branch (master untouched, an entry in `info/exclude`), the `<repo>-worktrees` directory, refusals with no changes at all (main branch, an existing one, a bad name, changes on master, no main branch, no program, a repository outside the projects directory, a screenshot outside the tasks directory, one word, `addDirs` outside `<board>/docs/` and the board's project repositories), `--add-dir` of both tools, rollback (a branch with new commits stays), taking over and cancelling the task file, choosing the Konsole window and the card/window mode on fakes, the `newTask` marker,
- `daemon/tests/test_tasks_local.py` — a new task on a local model without a real server and agents: the argv and environment of Codex and Claude Code for Ollama on this computer, on another computer and for an `openai` server, with and without a token (the token never in argv or in the task file), context (from a role, from a loaded model, too small for Claude Code), `launch.py` with an agent fake recording the environment (the token from a file only in Codex's environment, with Claude Code through apiKeyHelper — never in the environment, the shell after the agent with the user's original values, an unreadable token — the agent does not start), a model with "-" in one argument, the Planner with the `agent` role and a server-read fake (refusals `local-unset`, `local-unavailable`, `bad-model`, the title "· lokalnie" (· local), the remembered model), detecting sessions on a local model from the allowed list (Codex `model_provider`: ollama, lmstudio, oss, `tide-`; Claude Code `message.model`: known from `/Models` or `name:tag`; Azure, Bedrock, gateways — no) and the note in the card title,
- `daemon/tests/test_tasks_dbus.py` — `/Tasks` through a private bus with the real service and fakes of `konsole` (runs a command with `-e`), `systemd-run`, `claude` and `codex` (they record argv): `Repos`, `Defaults`, `SuggestBranch`, `Start` with the exact command in the agent, rollback when Konsole does not start the task, refusals and work in the main directory, `addDirs` from the board for Codex (worktree) and Claude Code (project directory) and the `bad-add-dir` refusal,
- `daemon/tests/test_worktrees_dbus.py` — `List()`, `Remove()`, `Refresh()`, `Prune()`, `Fetch()` (refusals `bad-request` and `no-remote`), `PullFastForward()` (introspection, `bad-request`, `no-upstream` without a HEAD change, the `flowBranch` field) and `ShowWindow()` through a private bus; the agent simulated by a Claude Code session in a worktree,
- `daemon/tests/test_worktrees_pull.py` (K-36, PT-353) — `Worktrees.PullFastForward` on real repositories with a local bare "remote": a fast-forward of the main branch in the main directory and of the branch of an attached worktree, only reads and one `merge --ff-only` (no `reset`, `rebase`, `stash`, `push`, `prune`, `--force`); every refusal (`no-upstream` with a detached HEAD, `dirty` with an untracked file, `agent` with the agent's directory and the card's place, `card-step` from a real card journal — intent, result, a finished card, another repository, an unreadable journal, `diverged`, `up-to-date`, `git-operation` with operation traces and a `smudge` filter, `locked`, `boundary`) with an unchanged HEAD and `git status`; paths outside the list (a link, `..`, a non-string) without git; the state read under the lock (a worktree dirty after reading the list and after taking the lock); a time limit and an exception release the lock; two threads with an interleaving forced by events: the pull waits for the card's merge and does not see its intermediate state, the card's merge waits for the pull and sees HEAD after it; M1: the engine lock is free during the fetch, a second fetch `busy`; `WorktreeMonitor.pull` returns at once (the main thread does not wait), a second request `locked`,
- `daemon/tests/test_worktrees_fetch.py` — `Worktrees.Fetch` on real repositories with a local bare "remote": `refs/remotes`, `lastFetch` and counts of new commits, a new remote branch, many remotes, no `--prune` (a branch deleted remotely stays) and an untouched HEAD/worktree, the exact argv (no `--force`, `--tags`, `+refspec`), a remote pointing to a nonexistent directory and a remote HTTP 401 with a `GIT_ASKPASS` fake not called, masking `user:token@` in the message and logs, the environment (`BatchMode`), a time limit on a hung git, paths outside the list (a link, `..`) without running git, fetching despite the repository lock held by a card merge (without `lock_for`, M1) and `busy` for a second fetch of the same repository,
- `daemon/tests/test_worktree_open.py` — the `Open` gate (a directory from the list, a file only for Rider, `..`, links, siblings with a common prefix) and `WorktreeMonitor.open` with a `Popen` fake (argv `[rider, file]`, `cwd` = the file's directory, the terminal refuses),
- `daemon/tests/test_gitread_diffdata.py` — `Git.Diff` of the `card` source on temporary repositories and a card journal: files, hunks and sums consistent with `git diff` and `git diff --numstat` from the pinned base, the range after the start branch moves, `targetRef` (merge-base, a ref without shared history, a ref with `-`), the codes `unknown-base`, `unknown-card`, `no-branch`, `bad-request`, a main branch `main` and `develop` without `origin`, a card from a card group (a repository from `card.started`), no write to the journal and refs, the configuration trap from PT-348 not triggered; large diffs (PT-355): a line and byte limit with the full file list and `truncated`, a `large` file without hunks, binary with `oldSize`/`newSize`, `options.path` (also `large` and truncated) and `options.context`, `options` validation, streaming read `Cancellation.run_capped` (infinite output cut without reading it all, exactly the limit is not a cut); `GitMonitor` — an answer from the thread pool after background work, the loop free,
- `daemon/tests/test_gitread_diffsources.py` (K-36, PT-349) — `Git.Diff` of the `ticket` source (merge-base with the card branch, a second ticket without the first's files, a ticket merged with a deleted branch: the range from the `intent`/`result` pair of the `merge` action equal to the diff from before deletion, `no-ticket-range` without a record, with a failed merge, with a SHA outside the repository and with a value starting with `-`, an unknown ticket and card), `commit` (first parent, a merge commit, an initial commit against the empty tree, a worktree from the list, `options.path`) and `range` (merge-base, changes on `from` outside the result, full refs and SHAs, no shared history); validation of `repo`/refs (`validate.py`): a repository outside the list, through a link and `..` and a ref with `-` rejected with `bad-request` without calling the runner, a link to a known repository resolved to the canonical path, no write to the journal and no changes of refs and index; `GitMonitor` passes the source of known repositories,
- `daemon/tests/test_gitread_branchmap.py` (K-99, PT-387) — `Git.BranchMap` as a projection of card journals (events shaped like engine records) and of the Board: the list of projects with a repository, nodes and edges of cards in progress, closed, merged and with a rejected ticket, a card from another card's branch, a base correction (`started.initial`) and an ineffective correction, the `unknown-base` group, a merged ticket with a `merge` edge without git, the bases `main`/`develop`/`origin/develop` without a `master` node, no `mainRef`, the limit (newest, `total`, `truncated`, 200 by default, a cut to 500), errors without an exception and without the exception text, no write to the journal and the Board; `GitMonitor.branch_map` — an answer from the pool after background work, the Board source on the main thread, a runner fake without git calls,
- `daemon/tests/test_git_dbus.py` — `/Git` `Diff()` through a private bus on a card in "Od razu" (Immediate) mode with an agent fake: introspection, a diff from the pinned base like `git diff`, `targetRef`, refusals, without new events and ref changes; `BranchMap()` (PT-387): the list of projects, the map of a card and ticket from a real journal, `unknown-project`, `bad-request`, without new events and ref changes,
- `daemon/tests/test_krunner_index.py` — the history index on synthetic logs (`history.py`, `fixtures/history`): titles of both tools (also from Codex's state database built in the test), the first message from the start of a large file, a cutoff after 30 days, reading only changed files,
- `daemon/tests/test_codex_homes.py` — several Codex data directories in the world in a temporary directory (`~/.codex`, `~/snap/codex/33`, `34`, `current` → `34`; never the real directories — `tests/__init__.py` also sets a nonexistent `$CODEX_HOME` for services started without `--codex-dir`): the default list and `$CODEX_HOME`, repeated `--codex-dir`, resolving `current` (also after a snap refresh), an older revision skipped, a copy of the same file in two directories counted once (Agents, KRunner, worktrees, report, coordination), thread segments as one item in Agents and KRunner, and separately in the report and worktrees, switching the watch to a new revision after a snap refresh, "Wznów" (Resume) in the report for a session from the snap, `is_snap` only under `~/snap/codex`, assigning a process with a path with a revision number and Meta+A to a Konsole card, new files in both directories noticed by directory watching (without a scan every 30 s), thread names and the snap's state database in KRunner, resuming with Codex from the snap or from outside it,
- `daemon/tests/test_codex_desktop.py` — Codex Desktop: a link with a UUID only, the `Exec` line (field codes, quotes, no program), searching for the `.desktop` file and `$TIDE_CODEX_DESKTOP`, the command as an argument list in `systemd-run --scope`, display variables from `systemctl --user show-environment`; a launch with a `chatgpt` fake (`fakebin.py`) — never the real application; the main thread of a subagent (several levels, an unknown parent, a loop), collapsing subagents and the most urgent state, a subagent without a parent, `AgentMonitor.activate` and Meta+A (opening the main thread, raising the `ChatGPT` window) with an application fake,
- `daemon/tests/test_krunner_core.py` — matching and ranking, `ai `, texts in Polish, the `Run` commands (resuming Claude/Codex in Konsole, switching to the window of a live session, a Codex Desktop thread in the application, a project, a worktree, Rider) with a record instead of running; lazy index building,
- `daemon/tests/test_krunner_dbus.py` — `Match`, `Actions`, `Run` through a private bus with KRunner 6 signatures; the programs are fakes (`fakebin.py`: `systemd-run`, `konsole`, `rider`, `claude`, `codex` only record the call),
- `daemon/tests/test_krunner_semantic.py` — search by meaning (PT-19) with a model fake ("concepts" instead of bge-m3): the session text (metadata, tickets, `strip_secrets`, trimming), the index file (0600/0700, without texts, broken or foreign — from scratch), incremental indexing, a 30-day window and an item limit, rebuilding after a change of model and dimensions, disabling (the file disappears at once, a waiting batch aborted), errors as state, `queue.IDLE` and silence after another model before every batch, ranking, the query (without commands, addresses, paths and secrets, `HIGH`, memory, a newer one invalidates an older one, the server does not answer, a time budget), merging in `KRunnerCore` (words first, without repeats, a note) and `models.embed` on an HTTP server fake (Ollama, OpenAI, bad responses, cloud, a role behind `default`); in `test_krunner_dbus.py` — the service with an Ollama fake (`--search-delay 0`): the index on disk, a semantic result in `Match`, and after the server is turned off only the words in under 3 s.
- `apps/newtask/tests/` — the new task window: form logic in `qml6`, window shots with `--data --grab`, screenshots (dragged, from the clipboard, a Spectacle fake, cleanup), the backend on a private bus with the real service, a second launch, installation with a Konsole profile; `kwin/tests/test_new_task_shortcut.py` — the Meta+Alt+A shortcut,
- `apps/board/tests/test_view.py` (PT-43) — the launch window from a card with what the new task window has: screenshots (dragging, Ctrl+V, "Zrób zrzut" (Take a screenshot) with a Spectacle fake, deleting, cleanup after "Anuluj" (Cancel), the directory stays after start) in the `images` of the `Start` request, a cloud/local model from `/Models` (disabled with a reason, the agent line, the branch and `tool` behind the local agent's program, blocks), `imagesDir` and the model state from the real service,
- `apps/board/tests/test_merge_window.py` (K-99, PT-390) — the merge window `apps/common/qml/git/MergeWindow.qml` on a host fake (the `diffprobe.py` probe): the merge button only with `canMerge === true` and a token, `mergeRequested` with exactly `{targetRef, previewToken}`, closing/Esc/"Odśwież" (Refresh) without consent, an answer with an old tag skipped, a block after a merge error until a new preview, a minimal preview (state as a sentence, conflicts, block reasons, "Szczegóły" (Details)) as plain text, buttons visible with long content; in the Board: the window from the panel buttons and the full card with the default target, closing on a card context change without a late message, "Zmiany gałęzi" (Branch changes) above the window and returning with Esc; the source with no dependency on the Board; in the windows (`apps/`) no merge path without a token and no old merge components, with an allowlist of the Board backend's writes (`MergeCard` yes, the path without a token no; the names in the test are split into fragments, so a literal search gives an empty result); target choice (K-99 #6, `TargetChoiceTest`): the default target gets a preview, the shortcut and search send a new preview and disable "Scal do" (Merge to) until the answer, an unknown base gives only a suggestion, a non-selectable target shows the reason without a preview, a repository without `master` and `origin` (main `main` or `develop`, without remote branches), no `mainRef` without a shortcut and without guessing, a late list answer skipped; in the Board `apps/board/tests/test_merge_targets.py`: a single "Scal…" (Merge…) opens a window with a base preview or with a target choice,
- `apps/board/tests/test_diff_view.py` (K-36, PT-354) — the shared diff view `apps/common/qml/git/DiffView.qml` on a backend fake (the `diffprobe.py` probe, offscreen): the file list with counts and a sum, hunks of the selected file, `]`/`[`, Alt+↓/↑, `n`/`p`, Esc, `r` and "Odśwież", "Czytam…" (Reading…), an error in one sentence, opening on a file from a parameter, `unknown-base` with a ref choice and `Git.Diff` with `targetRef`, names with `<b>` and unicode as plain text; `startLabel` (`start.js`: a local base, remote, unknown, a name with "/" and special characters, no date); large diffs (PT-355): a `large` file collapsed with "pokaż" (show) and `Git.Diff` with `options.path`, binary with sizes, the truncated-diff bar and automatic loading of the selected file with no content, an error with a retry, a late answer after a refresh; colors only from the palette (`semantic.added`/`removed`), the package and the `gitDiff` slot of the Board backend,
- `apps/worktrees/tests/` — the window: QML on sample data (`qml6`, offscreen), the launcher with `--data --grab`, the window backend on a private bus with the real service (Remove only for selected), a second launch, installation with `update-desktop-database` and `kbuildsycoca6` fakes,
- `apps/worktrees/tests/test_merge.py` (K-99, PT-395) — "Scal…" from the branch map: the button only for a closed, unmerged card, `PreviewCardMerge` for the default target and `MergeCard` only after "Scal do <target>" with the token of the current preview, closing/Esc/"Odśwież"/refreshing the map/changing the project without consent, an old preview (another tag) does not unblock the button, a `MergeCard` refusal (`stale-preview`) shows the reason and blocks "Scal do" until a new preview without a second `MergeCard`, after success (also `already: true`) the map is read anew with a node "scalona do <target>" (merged to <target>); the list of allowed `/Board` calls of the backend (`BOARD_CALLS`) and a refusal of other names without calling the service; on a private bus with a `/Board`, `/Git`, `/Worktrees` fake — the in-process backend and an offscreen window with the real backend (the `mergeprobe.py` probe), including history 37 (a repository without `master` and `origin`, main `main` or `develop`, a known and an unknown base); in `daemon/tests/test_cardflow_merge_confirm.py` a test of two windows of the same card (the same consent — the same operation, a preview of another target — `stale-preview`),
- `daemon/tests/test_idle.py` — idleness on a Wayland compositor fake (`fake_wayland.py`: a real socket and message format, a frozen clock): the threshold, input restarts the time count, protocol choice (v2, v1, `org_kde_kwin_idle`), no protocol, a protocol error, a broken connection and reconnecting; `IdleState` on a frozen clock; the service's `/Idle` object on a private bus (properties, `IsIdle`, the `IdleChanged` signal, the threshold from the palette, no compositor, `--no-idle`). Services in tests get a nonexistent Wayland socket, so they never connect to the desktop compositor,
- `daemon/tests/test_report_schedule.py` — the report deadline (the first login of the day, 07:00, work after midnight, a time change, disabling), the period (the first, from the previous anchor — the hour from the deadline or an earlier end, an evening `Generate()` at 23:54 does not shift the next day, a weekend, another `at` hour, at most 7 days, regenerating, repairing a state from before this rule), one notification per day, settings from a file and options, state, 30-day retention (only report directories, without links),
- `daemon/tests/test_report_generate.py` — the report from a synthetic world (`reportfix.py`: repositories with dated commits, Claude Code and Codex logs, a scheduled task, a subagent, sessions from before the period) with a frozen clock: projects with and without activity, commits by branch, the agent marker, the latest messages, task runs, worktrees, files and permissions, directory replacement, the state of work for the day (a quiet night with a waiting session, an unmerged branch and a Board card in progress — a non-empty report; really nothing — the sentence "nic się nie dzieje" (nothing is happening)), escaping (a script in a commit subject, a session title, an agent message and a file name), CSP, no resources from the network; reading AI Limits from an API fake (the token only in a header, never in an error),
- `daemon/tests/test_report_standup.py` — "Na daily" (For the daily): facts from a synthetic world with a coordination board (tickets in all states, a ticket from before the period, a branch merged in the period, a branch merged into an integration branch, a branch with agent instructions only, an internal `worktree-agent-*` branch in a separate worktree, a session outside the repositories, sessions at branches with a first command), work or housekeeping (signals of commits, paths and texts, change areas, a ticket about a tracker with a question in "Czeka na mnie"), a plan with YouTrack without a board (goal, notes, a description through the migration map), junk in the model's answer ("nie ma decyzji" (there is no decision), branch counters, "bez review" (without review), done ≠ review), rules (grouping by tickets and branches, a collective item, an item limit), the input for the model (removing secrets, `~`, limits per project and in total), answer validation (HTML in text, an invented ID, an unknown project, missing fields, control characters, lengths), a `claude` fake through `$TIDE_CLAUDE_BIN` (ready JSON and argv without tools, an error code, junk, `is_error`, a hang killed after a timeout, no program, the `summary.json` memory), pages (the index as an overview with cards, counters and highlighting of waiting ones, "Poza projektami" (Outside projects) without pages, a project page with its own "Na daily", copying only that project and collapsed housekeeping, escaping, the footer), the `summary`/`summaryModel` settings. The tests do not call the real `claude`: `daemon/tests/__init__.py` and the services in tests have an empty `TIDE_CLAUDE_BIN`,
- `daemon/tests/test_report_dbus.py` — the whole service on a private bus: a report at start, one notification "Raport poranny gotowy" (Morning report ready) with "Otwórz" (Open) (fakes of `systemd-run`, `xdg-open`; "Otwórz" marks as read), `Generate()`, `Latest()`, `Status()`, `Open()` and the `Changed` signal, `Days()`, `Day()`, `OpenDay()` and `MarkRead()` for the Tide window (an older day does not clear the dot), a new day after a restart unread again, introspection, no repeat after a restart, a disabled deadline,
- `daemon/tests/test_report_data.py` — `report.json` from a synthetic world (card order, "Na daily", counts, clipboard texts equal to `render.daily_text`, raw strings without entities, 0600), the order waiting → ordinary → housekeeping only, `since` (also through a time change), the list of days (newest first, a report from before `report.json`, a broken file, links, bad names), `Day()` (missing, bad names, legacy, the newest and unread), page paths (`index`, `../`, a link), `ReportMonitor.mark_read` and `open_day` (the newest day clears the dot, an older one and another directory do not),
- `apps/reports/tests/` — the "Raporty" (Reports) view of the Tide window: the package without hard-coded colors and with every text as plain text, the `reports.js` logic, `ReportsView` in offscreen `qml6` with a backend fake (days and the dot, cards, the panel, copying, marking as read only on top of the active window, the "Wygeneruj teraz" (Generate now) progress, the empty state, `showItem`, a report from before `report.json`, HTML in report texts literally), the backend with `--data` (Board tags, the badge, the clipboard) and on a private bus with the real service (`Days`, `Day`, `MarkRead`, `OpenDay` through an `xdg-open` fake, `Generate` with progress), shots with `--data`,
- `daemon/tests/test_report_read.py` — reading without D-Bus: `Status()` (without a report, without state, an old state file), the transitions new → read → the same day after `Generate()` → a new day, a failed open, "Otwórz" in an old notification, KRunner through `ReportMonitor`,
- `widgets/tests/test_widget_report.py` — the "Raport" (Report) link in the bar header and in the agents popup in offscreen `qml6` (new, read, no report, yesterday, older, the service is not running), shots, header height and the popup and dock tile width unchanged, a click,
- `daemon/tests/test_limits_core.py` — AI Limits without Qt: readings from `/status` and reset labels (also across a time change), windows and history (appending, restart, 14 days, broken lines), the forecast on synthetic series (even pace, in bursts, a break, a long pause, crossing a reset, a week with nights, too little data, stale data), notifications once per window and after a restart, configuration from the palette, the API client on a QuotaApp fake (the token only in a header, never in errors), `--print-limits` without a write,
- `daemon/tests/test_limits_dbus.py` — the `/Limits` object on a private bus with a QuotaApp API fake (HTTP in the test thread) and a notification server fake: `Forecast()`, the `Changed` signal, notifications, history in `$XDG_STATE_HOME`, no token in files, a restart without repeats, the service unavailable, `--no-limits`. Services in tests get a nonexistent `LIMITY_CONFIG`, so they never query the real QuotaApp,
- `daemon/tests/test_coordination_core.py` — coordination without Qt on a synthetic world shaped like real journals (`coordfix.py`): a Claude Code coordinator (assignments in the background and in the foreground, notifications from the queue and from messages, queue and API-copy noise, a report through SubagentHandback, SendMessage, TaskStop, AskUserQuestion, incremental reading), the Codex thread tree (a grandchild, a consent reviewer), correct and incorrect plans, ticket IDs (prefix, task names, the migration map, the coordinator's ID), review formats, state rules (work, plan, YouTrack, interruption, grouping without an ID), YouTrack on an HTTP server fake (GET only, the token only in a header, never in errors, 5-minute memory, another server, a variable from the `.env` file), `/Agents` annotations, the file for the report (0600, without reports and commands), the diff target and worktree,
- `daemon/tests/test_coordination_dbus.py` — `/Coordination` on a private bus with a YouTrack fake and program fakes: `Coordinators`, `Board`, `RunDetail`, the `Changed` signal after a notification is appended, `Activate` (resuming in Konsole), `ShowWindow`, the `coordination` field in `/Agents`, the file for the report without a token, only GET to YouTrack, `--no-coordination`, `--no-youtrack`,
- `daemon/tests/test_coordination_describe.py` — card descriptions: rules (command formulas, state and review in words, the implementation report), facts without secrets and without whole commands, validation of the model's answer (only known tickets, plain strings, trimming), the cache and intervals (SETTLE, GAP, RETRY, old coordinators, batches), a 0600 file and its validation, the `rules` mode without calls, a monitor with a description in the background — the model is always a fake,
- `daemon/tests/test_coordination_integration.py` — KRunner ("sesje" (sessions), a word filter, launching only known ones), "Plany i tickety" (Plans and tickets) in the report (a board from a file, plans from repositories, escaping, a missing file and errors), `Launchers.show_sessions`,
- `apps/sessions/tests/` — the Tide Sessions window: the package without hard-coded colors, the `sessions.js` logic in QJSEngine, offscreen shots with `--data` (board, Codex, a subagent result) without QML warnings, data after `tools/sessions_preview.py --sanitize` without content, the launcher on a private bus with the real service and a second launch, installation,
- `daemon/tests/test_board_core.py` — the board without Qt on temporary repositories with their own git configuration: frontmatter (unknown keys, comments and content after a write, quotes, newlines), creation on the first write (a marker, tags, templates, no remote, an author from the configuration and "Tide" without it, a foreign directory untouched), the exact format of the card file, IDs (max + 1, no reuse, a broken file takes a number), order and moves with commit messages, refusals for computed columns, validation of fields and IDs (also `../`), projects created with a card with the default `ai-docs`, tags (inheritance, colors from the palette, renaming in cards and projects), mode templates (fields, a project override, unknown braces), committing outside changes separately from ours, bad files in `errors`, skipping the board repository in the scanner by the marker,
- `daemon/tests/test_board_robustness.py` — hard cases: a failed commit (the card stays, `commitError`, no duplicate, a pending commit with its own message), an orphaned index lock (git killed after a timeout, an old lock, the lock of a live git on a `/proc` fake), a revert with a conflict (no "sync" commit, `git-busy`, entries in `errors`, conflict markers as a file error), card numbers after a revert (a counter outside the history; git history only once, when the counter is missing, never in a snapshot), conflict markers in the description as plain text, a pending commit of a deleted file and a permanent error without blocking the queue, an error without a file, directory and commit (an empty directory and an existing board), links outside the board, FIFO and `/dev/zero`, YAML lists and Windows files, ordering in the middle and renumbering without room, no hooks (post-commit too), a project by name without `foo-2`, the default `full` mode, a tag rename without a file, the documents directory with `RenderFlow`,
- `daemon/tests/test_board_dbus.py` — `BoardMonitor` in-process (a change from outside → a commit and `Changed`, our write without a second commit, errors as JSON) and `/Board` on a private bus: introspection, `Snapshot`, `CreateCard` with a repository, `UpsertProject`, `UpsertTag`, `UpdateCard`, `MoveCard`, `RenderFlow`, the `Changed` signal, commit history, editing the file from outside, errors `{ok: false, error, message}` with codes and without a commit, `--no-board`, `--print-board` without a write. Services in tests get a temporary `TIDE_BOARD_DIR` (`privatebus.py`), so they never touch the real board,
- `daemon/tests/test_cardflow_dbus.py` — the card flow "Od razu" (Immediate) (PT-58) through D-Bus on a private bus with fakes of `claude -p`, `systemd-run` and `systemctl` (`tests/flowfakes.py`): StartCard → journal, branches, the column "W toku" → "Do sprawdzenia", no result file, a question, the step guard, a service restart during a step, "Backlog" and "Gotowe", a journal deleted from outside, the step's session outside /Agents and the Work tile, the step's worktree busy in /Worktrees; the test directory outside `/tmp/claude-<uid>` (`TIDE_TEST_TMPDIR`, otherwise `build/test-tmp`),
- `daemon/tests/test_cardflow.py` — the same run in-process (`BoardMonitor` + `CardFlowMonitor`, a real journal scanner for the agent list), also where the private bus does not start; in addition a merge started before a restart ends once, refusals without a trace (executor, entry, no prefix) and a card without a journal without events,
- `daemon/tests/test_board_launch.py` — launching from a card on the board side: `RenderLaunch` with real templates (full, light, spec; documents in the board and in the repository; the tool, branch and manual test from the window; errors), template migration (the old seed byte for byte from `fixtures/flows-pt1/` replaced, one changed by the user and a project override stay, a busy repository), the card launch fields with validation, the "specyfikacja gotowa" (specification ready) marker (a draft, another mode, another card, a specification in the repository, the `card` key), `BoardMonitor` (migration at start, the marker on synchronization, `ShowWindow("launch:K-12")`, `RenderLaunch` as JSON),
- `daemon/tests/test_board_multiproject.py` — PT-28 on temporary repositories (toolssuite with a card worktree, mobileapp, legacyinfra) and synthetic journals: a session's working directory in the board (`board/place.py`: the start directory, `docs/<slug>/`, `docs/_inbox`), `/Agents` (Claude Code before and after `cd` to the board's plans directory, Claude Code without an entry with the start directory, Codex with `session_meta`), KRunner, the morning report and the coordinator in the board (the repository, branch and plan of a card), the "launch from a card" rule (the time window, the tool, a directory without a worktree, `launchKey`, the nearest start, sessions of other cards, a pinned session after a change of cwd/project/branch, a finished pinned one yields to a new one), persistence (`sticky`: a launch, an unambiguous branch, two matches — no), `BoardMonitor` with the K-1 replay (the commit "K-1: link session …", a card in "W toku" after `cd`, a service seeing the session only after `cd`, a manual `session` without an override and a commit), the `projects` field (a write, a project from a repository, a snapshot, validation, an unknown slug in a file), `RenderLaunch` (a sentence about absolute paths, a list of repositories and rules, the spec mode, a missing repository, a custom template without `{projects}`, migration of `tide-flow: 2` templates), `/Tasks` `addDirs` with project repositories (both tools, a link, refusals) and the card state by branch in all repositories,
- `daemon/tests/test_youtrack_sync.py` — synchronization with YouTrack without Qt (PT-7) on a REST API fake (`fake_youtrack.py`: GET and POST of an issue with the shape of a real State field, search, a call log): a write on every card transition (the POST shape), no write and no network without a transition, a 5 s countdown, Archive and a project without synchronization, the global switch, adopting the state at the first encounter (a change after connecting, resolved, `open`), a 0600 state file across a restart, a conflict (no overwrite, a note, `errors`, no endless polling, "take from YouTrack", "overwrite from Tide", just expiring), Reopened instead of Open and no rollback after the work disappears, the precedence of a ticket and of a manual Done without ping-pong, an umbrella, a state from YouTrack and a local plan skipped, Verified from the coordinator's card, Won't fix, the freshest coordinator, a queue without network with a retry and across a restart, 503 and a state refusal, the token never in the log, file, snapshot and address, the state map from settings and per project, issue keys, the import list (assigned, reported without an assignee, resolved and foreign ones off the list, the card already exists, "new" across a restart, the card's title and description),
- `daemon/tests/test_youtrack_board.py` — PT-7 in the board: the YouTrack project key and the switch (a file, a snapshot, uniqueness, validation), the card's `youtrack` field (address → key, validation), `BoardMonitor` with `YouTrackSyncMonitor` on a fake (Done → Verified, return → Reopened, a conflict in the snapshot and its resolution by the card, import into Backlog with a project and without duplicates, methods without YouTrack), `/Board` on a private bus with `TIDE_YOUTRACK_CONFIG` (list, import, move → Verified, `ResolveYouTrack`). Services in the other tests get `fixtures/youtrack-closed.json` (a closed port) — never the real YouTrack,
- `daemon/tests/test_boardplans.py` — plan standard 1.1: the `card` key (validation, the card ID from the branch, choosing a plan by card), plans of projects with `ai-docs: tide` in a temporary board (`TIDE_BOARD_DIR`) in `find_plans`, the scanner, the board and the coordinator summary (`card`), the number check, the morning report, `tools/next_id.py` and `tools/check_plans.py`; `ai-docs: repo` projects unchanged. `daemon/tests/__init__.py` points `TIDE_BOARD_DIR` at a nonexistent directory — the tests do not read the real board,
- `daemon/tests/test_docker_core.py` — Docker without Qt on an Engine API fake (`fake_docker.py`: a real unix socket in the test thread, response shapes as in Docker 29, chunked responses cut in the middle of characters, the `/events` stream, logs in frames, a record of every request): `DOCKER_HOST` (unix, tcp/ssh → `unsupported`), no socket, a refusal, no permissions, a hung Docker; compose groups, a repository with a worktree (also outside the projects directory), "bez compose" (without compose), testcontainers, code 137 and failure rules (0, 143, OOM, `dead`, restart, `created` after a failed start, 137 after a `Stop` from Tide), ports without repeats and `likelyHttp`, times with nanoseconds, `inspect` only after a state change (not of the "Up 3 hours" text) and a full refresh; database web panels in `likelyHttp`; disk categories (nothing in use), anonymous volumes of stopped ones, prune only from a list of ids in a safe order (a regression: `{"volumes": "false"}` without any call), 404 from prune, the exact methods, paths and queries of actions, `Remove` of a running one without a call, dependency order in a project, a partial failure of a project `restart`, limits of single actions under 25 s, one deadline per request (a response trickling in byte by byte) and per action (after the deadline without calls), the "stopped from Tide" marker on a refresh during `stop` and with concurrent appending, log demultiplexing (stderr, TTY, lines and UTF-8 characters split between frames, a cut last frame, a broken header in the middle), statistics in parallel with a total time limit, the event filter,
- `daemon/tests/test_docker_monitor.py` — `DockerMonitor` in the Qt loop on a fake: an event → `Changed`, `exec_*` events without a refresh, a series of events in one refresh, a broken stream and reconnecting, polling without Docker until it appears, Docker disappearing, the `Stats`/`Disk` memory (no result from before an action), a refresh without a change without `Changed`, `Prune`/`GroupAction` tasks with `actionFinished` and a refusal without a task, cards with logs and a shell (argv with the ID and `-H` in the task file, a separate directory for each card — two quick `OpenShell` calls do not swap containers, `workdir` in the home directory, rollback on a failed Konsole), an action waiting in a busy pool is refused in time,
- `daemon/tests/test_docker_dbus.py` — `/Docker` on a private bus with the real service and an Engine API fake: introspection, `Snapshot`, the `Changed` signal after an event, actions with exact calls, `GroupAction` and `Prune` tasks with the `ActionFinished` signal (only the indicated category, refusals without a task and without calls), `Logs`, `Stats`, `Disk`, `OpenLogs`/`OpenShell` up to the `docker` fake run by `launch.py` in a Konsole fake (in the home directory), an unavailable socket, `--no-docker`. The tests never touch the real Docker: `daemon/tests/__init__.py` and the services in tests (`privatebus.py`) get a nonexistent `DOCKER_HOST`,
- `daemon/tests/test_models_core.py` — local models without Qt on an HTTP server fake in the test thread: `models.json` (no file, the example from the plan, colons in model names and splitting only at a known endpoint, `default` for roles without an endpoint, role context, a broken file, skipped endpoints and roles with warnings, a token from `~` as `Authorization: Bearer` and nowhere outside the header, credentials in the address), the Ollama client (the system command, the schema in `format`, `num_ctx`, `think: false` and a retry without it, other 400s without a retry, 404/500/502, an unreadable and empty response, a closed port, a time limit, `$http_proxy` ignored, settings read on every call) and the `openai` server (`response_format` with a schema, without `num_ctx`), the state of both kinds (version, models, part on the card, roles `installed`/`available`, GET only, a server unavailable, hanging and failing after `/api/version`, an unexpected error of one endpoint), addresses that cannot be parsed and with credentials, `default` that is not a name or points to a skipped endpoint (roles without an endpoint unavailable, not on `local`), a role on a skipped endpoint, a non-ASCII token, a redirect (the second fake gets neither the request nor the token), one deadline for the whole request (a server trickling bytes, a slow name lookup). The tests do not query a real Ollama: `daemon/tests/__init__.py` sets `TIDE_MODELS_CONFIG` to `fixtures/models-closed.json` (a closed port),
- `daemon/tests/test_models_settings.py` — saving settings without Qt (`models/settings.py`): the first write from defaults (roles work as without a file), a change preserving unknown keys and 0600, validation of an endpoint and role with the same messages as reading, a 0600 token file in a 0700 directory and the token nowhere outside it, an empty token stays, `clearToken`, rolling back the token on a failed write (the old file untouched, no temporary files), a broken file never overwritten, removal rules (the default, the only one, pinned roles), switching `default` moves roles without an endpoint, and pinned ones stay, `ctx` (no change, `null`, 0, bad), warnings from a hand-edited file do not block the write, a symbolic link; `report.json` (other keys and permissions stay, a new file, an unknown mode, a broken file, `report` in the state); `TestEndpoint` without a write (the token only in memory, on a fake and a closed port); `PullModel` on a stream fake (progress as a sum of layers, an error in the stream, a broken stream, 500, a silence limit, `Cancel`),
- `daemon/tests/test_models_monitor.py` — `ModelsMonitor` in the Qt loop with a read fake: `Refresh()` during a read gives one read right after it, `Changed` only on a change, a read error leaves the last state, unreadable settings at start do not stop the service; writing: a read from before the write does not roll back the view, a refusal without `Changed`; pulling: `Changed` with progress at most once per second, the entry disappears after a moment, `stop()` interrupts the pull; the queue: the `queue` field (idle endpoints, running and waiting ones from worker threads, without command text) and `Changed` because of the queue at most once per interval, detaching from the queue on `stop()`,
- `daemon/tests/test_models_queue.py` — the model queue: one call at a time per endpoint and two endpoints in parallel, more slots per endpoint, priorities and arrival order without interrupting a running one, grouping models (and its limit, and the precedence of priority), the end of the grouping window between evaluations of two waiting ones (a replaced clock — nobody sleeps while a slot is free), a wait limit separate from the call limit with the reason in the message, an error releases the endpoint, `Cancel` removes waiting ones at once and interrupts running ones, no own threads and hooks in `Cancel`; `chat()` on a fake serving one request at a time (`timeout` from the start, `deadline` for waiting and the call together, `queueS`, two endpoint names on one server share a slot, a target from `models.json` changed during the wait); the report and card descriptions at once — a card waits in the queue instead of in the server and gets a model description (without the queue this test ends with a misleading "nie odpowiedział" (did not answer)), a card before the rest of the report, the report budget with waiting, honest information on the card after the wait limit, `Describer.stop()` removes waiting ones,
- `daemon/tests/test_models_dbus.py` — `/Models` on a private bus with an Ollama fake and its own `models.json`: `Status()` (a working endpoint with a token and a non-working one), `Changed` only after a change (a loaded model), `Refresh()` after a settings change without a restart, the token only in a header, introspection, `--no-models`, `--print-models` (GET only, writes nothing); writing from D-Bus in its own `XDG_CONFIG_HOME` (start writes nothing, `SetEndpoint` with a token and an endpoint checked at once, `SetRole`, `SetDefault` moves roles, `RemoveEndpoint` refusals, `SetReportSummary` works at once and keeps other keys, `--report-summary` changes only the file), `TestEndpoint` (a fake, a closed port, a slow server does not block `Status()`), `PullModel` (progress in `pulls`, one pull per endpoint, a refusal for `openai`). Services in tests get a `models.json` with a closed port (`privatebus.py`), so they never query the desktop's Ollama,
- `daemon/tests/test_activity_look.py` — the activity look (PT-35) with an engine-launch fake and clocks driven from the test (no dependence on time): a check at start without an activity, quick switches (one apply, the last activity), switches during an apply (they wait, nothing in parallel), a retry after code 75 with the same activity and a growing gap, other errors wait for an event, a new switch replaces a retry, the engine's presence at every launch, killing an apply on stop, no engine; the circuit breaker (PT-495): code 76 without a retry, three failures in a 120 s window (two no, stretched beyond the window no, the sequence 1, 75, 1, 1 yes, code 0 resets), no launch whatsoever with the breaker on (start, retry, plasmashell registration) until `activity_changed`, a line in the service output, the `breaker` key in the journal in a temporary directory (other keys stay, clearing removes it, a new monitor does not apply at start) and a time function injected instead of the clock; the settle delay with a plasmashell watcher fake and `FakeTimer` (start without plasmashell, unregistering and re-registering, a start with it registered); a login with code 75 five times with gaps of 2, 4, 8, 16 and 32 s (the default `RETRY_MS`), without the breaker and with a zeroed counter; a real `QProcess` with an engine fake (code and output); `CurrentActivityChanged` from an ActivityManager fake on a private bus (the plasmashell watcher is a fake, the journal in a temporary directory). The engine itself: `scripts/tests/test_activity_look.py`,
- `daemon/tests/test_install.py`, `daemon/tests/test_krunner_install.py`, `widgets/tests/` — installation and uninstallation on a temporary HOME with fakes of `systemctl`, `kpackagetool6` and `qdbus6`.

Fixtures are written by hand and imitate only the structure of entries — they contain no real conversations.


### Review and correcting a plan proposal (PT-80, PT-63)

After `plan.proposed` the plan waits for a `plan-review` step by a different executor and model than the planner.
The reviewer gets the full preserved specification, the card description and the sheets, and the criteria of verticality,
size, measurability of criteria, blockers and assignments. It also judges the content of the "Dla recenzenta" (For the reviewer) part.
It writes `out/plan-review.json` with `verdict` (`APPROVE` or `REQUEST_CHANGES`) and `findings`:

```json
{"verdict": "REQUEST_CHANGES",
 "findings": [{"severity": "major", "text": "#7 i #8 zmieniają to samo okno w jednej fali.", "tickets": ["#7", "#8"]}]}
```

`severity` ∈ `blocker` (the plan cannot start without a fix), `major` (an important change before the start), `nit`
(a trifle); `tickets` are the ticket numbers from the plan that the remark concerns (may be empty). The validator
(`cardflow/findings.py`) also accepts the old format — one string in `findings` (cards in progress): it becomes one
remark without a severity (`severity: ""`, no tickets). A wrong format (an unknown severity, a remark without text, `tickets` that is not
a list of strings) ends the step with the error `bad-plan-review` with a reason; the review is not saved.
`plan.reviewed` stores the list of remarks, and the card state gives `flow.plan.review{verdict, reviewer, findings[]}`
(`verdict` empty when missing or unknown).

The remarks trigger one `plan-revise` round; with no human in the loop the revision gets what the window selects
by default (`blocker` and `major`, without `nit`; when there are only minor ones — all of them). After the revision the plan is shown
with the review result, without another review in the loop. `ApproveCardPlan` before the review ends returns `review-pending`.

The decision window (`plan-approval`) shows the verdict and `findings[]` (severity, text, tickets, `selected`).
The recommended action depends on the verdict: no remarks → "Zatwierdź plan" (Approve plan); a blocker or `REQUEST_CHANGES` → "Odeślij
uwagi do poprawy" (Send the remarks back for correction); an unknown verdict → none is recommended. Approving despite remarks always works, and its
effect is described ("Mimo N blokujących uwag … uwagi nie zostaną poprawione" (Despite N blocking remarks … the remarks will not be corrected)). `EditCardPlan` with `action: return`
accepts `findings` (the selected remarks) and/or `feedback` (own text); only that goes to the planner.
After an automatic revision (`REQUEST_CHANGES` and `corrected`) the decision has `findingsApplied: true`: the remarks concern
the version from before the revision and are already applied, so none has `selected`, approval is recommended, and sending back
carries no remarks (K-130). Without a revision `findingsApplied` is `false`.

`EditCardPlan` records changes of assignment and of sheet content as `plan.ticket-edited`.
It does not accept changes to the plan's structure this way. A ticket with a started branch or step is protected
from editing also after a restart. Such a change returns `ticket-started`.
Repeating an identical edit does not append a second event.

**Appending by the user (PT-248).** In managing an approved plan the "Dopisz ticket" (Append ticket) button opens a
form for the title, a sheet with measurable criteria, the class, the executor, an independent reviewer and blockers.
This operation covers only **one new ticket in one project of the card**. Blockers point to existing
tickets of the plan; appending does not add blockers to the existing work. A finished card, an active fix,
review, close-out or a base correction exclude a new record. With a paused card the review waits for a resume.

The public `EditCardPlan` accepts `{action: "append", planFingerprint, requestKey, title, body, class,
executor, reviewer, blockers: ["PT-42"]}` and an optional `project` equal to the card's project.
`derived.flow.plan.fingerprint` is the fingerprint of the approved plan and base version, and `canAppend` reports
whether the form is available. The progress of existing steps does not change the fingerprint; editing the plan does.
A stale fingerprint gives `stale-plan`, reusing a key with different content — `request-conflict`.
Errors of the shared format check come back as `invalid-plan` with `errors[]`, without changing the plan or reserving an ID.

A successful write creates a single `plan.proposed` event with `sourceKind: append`, `appendRequest` and
`appendRevision`. The event atomically persists the proposal with a working number and the pending difference.
It starts no `planning`, `plan-amend` or `plan-revise`, also when the original planner has exhausted its limit.
The ticket's chosen reviewer checks the proposal through the existing `plan-review` path; independence of model
or thinking strength and locality are kept. A review error uses the ordinary step decisions.
The verdict `REQUEST_CHANGES` leaves the remarks to the user, with no automatic revision by the planner.

After the review the user separately chooses `ApproveCardPlan` or `EditCardPlan {action: "discard-revision"}`.
Approval assigns one persistent number and directs the ticket to ordinary execution according to blockers.
Existing rows come back unchanged, including sheet content and assignments; progress still comes from the journal.
Discarding keeps the existing plan. A corrected ticket can be saved with a new request key.
Repeating an identical write (also after approval or discarding) returns `already: true`, without a new
proposal. Reservations and recovery of approval use the existing persistent intents.

`action: return` records `plan.returned` with the remarks and a comparison base. The `plan-amend` step prepares
a new proposal; the service computes added, removed and changed tickets and the changed fields.
`plan.revision-ready` stores the difference; the panel shows it before approval. The new proposal
goes through review. `ApproveCardPlan` approves the difference (`plan.revision-approved`), keeps
existing IDs and reserves numbers only for added tickets. The service rechecks the protection
of started tickets on acceptance. Running steps are not stopped.

`derived.flow.plan` contains `review`, `revision`, `plannerStep` and `body` and `editable` for rows.
The `reviewing` state hides the proposal in the panel until the review ends and a possible single revision.
"Wejdź w planowanie" (Enter planning) uses the existing planner session and the shared Console mechanism.

A faulty plan revision gives `plan.revision-failed`; the panel offers sending back again with remarks
or `EditCardPlan` with `action: discard-revision`. Discarding records `plan.revision-discarded`
and keeps the active plan. A question, refusal or a second error of the revision/review step stays available
through `ResolveCardStep`, also when a ticket is working in another worktree. A pending difference has its own
proposed waves and keeps the card in "Czeka na mnie" (Waiting for me) also after the old plan ends.

### Conflicts and card close-out (PT-70)

A `merge-tree` conflict starts a `conflict` step in the card's worktree. The agent merges the approved ticket
commit and resolves the files; the service accepts the result only when the exact ticket commit and the earlier
card commit are ancestors of the clean card branch. A question or step error goes to the panel; the service does not
guess after a failed attempt and does not merge again on its own. A worktree with local changes is not removed.

**Tests after a merge (K-88, PT-318; `cardflow/mergecheck.py`).** When after the durable `result` of a ticket merge the tip
of the card branch from before the merge (`before`, the first parent of the merge commit) differs from the ticket's start point
(`start`), the service records `merge.check-requested {ticket, check, start,
before, after, command, source, fallback?}` with `check` = `<ticket>:<n>`. The command is a fixed Tide string with the base = the full
SHA of `start`: `tests.changed` of the approved test contract (`{base}` → `start`, `source: contract`), without a contract
the `Makefile`/`mk/*.mk` targets of the card branch read from git without running `make` (`make test-changed BASE=<start>`,
and with `test` alone — `make test` with `fallback: full`). Without a command the attempt has `unavailable: true` and gives a card
problem `merge-check-unavailable`; there is no "skipped" event. A contract that after substitution is not a single
command (a chain, a subshell, a shell, `env`, variables like `PATH=` — the criterion of the queue outside the sandbox) gives
the same unavailable attempt with `source: contract, reason: contract-not-single`, without falling back to the `Makefile`. Until a result `0` of the current attempt
(`merge.check-finished {ticket, check, exit, log, failedModules?, failedTests?}`, written: the run under consent) no new tickets start, later
merges wait, and `close` does not start; cleanup of the merged ticket's worktree runs. Any other `exit` (also
`"zatrzymane"` (stopped), `"przekroczony czas"` (time exceeded)) gives `merge-check-failed`, and the result of a stale attempt changes nothing.
The exit from the gate in this ticket: `ResolveCardStep {choice: "retry-merge-check", ticket, check}` — a new attempt
`<ticket>:<n+1>` on the current tip with the command chosen again (the second exit — acceptance — is added by PT-320,
below). The gate state is computed by `fold`; a missing attempt
after a restart (after `result`, before the attempt is recorded) is appended by the service once. Merges from before `merge.check-armed` (cards
running at the time of deployment) create no attempts. The only exception to `before != start`: when `start..before` is only
plan-state commits that Tide itself recorded on this card (the SHA from the result of the `plan-commit` action in the journal, each with
one parent, `start` an ancestor of `before`) — Tide commits the plan after the start of each ticket, so without this every
card merge with a plan would be non-FF. A manual commit (also of the sheet alone), a commit or merge of another ticket creates
an attempt; file names do not matter.

**Running an attempt under consent (K-88, PT-329).** An attempt with a command gets a pseudo-step `merge-check-<ticket>-<n>`
(`merge.check-step {step, ticket, kind: merge-check, check, command, after, branch, worktree, stepDir, baseVersion,
canAllow}`) — a process start in the identity model, so its decision and launch are an ordinary trace of an outside process;
it is not a card step (it does not work, has no review or resume). Tide does not approve the run in advance, because
the `Makefile`, the test contract and the tests on the card branch are written by the agent: consent is the pseudo-step's `step.decision` with `check`,
automatic (`auto: true`) only from the same rules as a step's `outside` request — a remembered command family of the project,
ticket consent, `outsideAuto` without a risky command — and with the PT-311 rules: a human's refusal of this command in this
ticket and a paused card take away automatic consent (`outside.auto-skipped`). Without a matching rule the card panel
shows an ordinary consent question (`permission`: "Zawsze w projekcie" (Always in the project), "Uruchom raz" (Run once), "Nie uruchamiaj" (Do not run), the answer
`ResolveCardStep {choice, step}`); consent concerns only this attempt, and a retry from PT-318 has a new pseudo-step and requires
a new decision. After consent the run goes through the outside queue (the slot limit, the order of consents, the heavy queue) in the worktree
of the card branch, when its HEAD is `after` (or `after` with only Tide plan-state commits); the command runs in full,
without retrying only the failed modules. Since K-109 PT-483 the execution is the one-execution protocol like a step request:
`command.intent` (`source: merge-check`, a slot and heavy queue reservation) → `Runner.run_command` (the service
`tide-cmd-<attempt>`, `RuntimeMaxSec`, the `exit-code` file) → `command.started` → `command.finished`. A paused card does not
start an attempt and does not hold a slot in the queue. The end records the pseudo-step's `command.finished` and
`merge.check-finished {ticket, check, exit, log, failedModules, failedTests}` with the same `exit` (a number,
`"zatrzymane"`, `"przekroczony czas"`; `log` — the full result). `failedModules` and `failedTests` (K-100, PT-423) are the modules
with a `returncode` other than 0 and the names of their tests (`failures`, `errors`, `unexpected_successes`, at most 200)
from the `summary.json` of the test runner, whose directory is indicated by the first header `Moduły: N, JOBS=…, logi: <directory>` (Modules: N, JOBS=…, logs: <directory>)
in `log`. A missing or bad log, header or report (e.g. a cut-off run, another test program) gives empty lists without an
error; the fields are diagnostic and do not change the gate, and results from before the change, without them, are read as before.
`command.started` and `command.finished` (also recovered and `not-started`) and the old `outside.finished` carry
the machine load at the time of the write: `loadavg` (1/5/15 min) and `cores` (cores available to the service), a read error gives
`null` in that field (K-100, PT-423). A refusal of consent (also after automatic consent, before the start — it invalidates it) gives
a card problem `merge-check-denied` with the same exit as a failure (a retry); after the intent is recorded a refusal is
rejected (`already-started`). After a service restart the attempt does not start a second time: an intent without a start is settled by the attempt's
marker (`O_EXCL`) — the command never started (`command.finished` with `reason: not-started`, the attempt's result
`"zatrzymane"`, i.e. a failure, not a successful attempt) or it started (a recovered start, the result from `exit-code`); the start and end
from the journal, and a result recorded between the end of the run and `merge.check-finished` is appended by the service once. Attempts started
the old way (`outside.started`, the unit `tide-outside-…` `--scope`, `<step>/outside/in/exit-code`, settling
of a stopped unit from PT-315) are after the change only recovered by the service and ended with `outside.finished`, without relaunching.
Tests: `daemon/tests/test_cardflow_merge_check_command.py` (a restart in every phase through
`FlowEngine.tick`), `test_cardflow_merge_check_run.py` (a historical attempt in progress), `test_cardflow_event_load.py`
(the K-100 fields).

**"Akceptuj mimo porażki" (Accept despite failure) (K-88, PT-320).** A card problem `merge-check-failed`, `merge-check-denied` or
`merge-check-unavailable` has two exits in the card panel (Board and Sessions): "Ponów próbę" (Retry) (recommended, PT-318) and
"Akceptuj mimo porażki". Acceptance is `ResolveCardStep {choice: "accept-merge-check", ticket, check, after}` —
the exact identifier of the attempt from the question (`after` — the full SHA of the card branch tip from `merge.check-requested`).
The service records `merge.check-accepted {ticket, check, after, by: "user"}` and opens the gate only for that attempt;
it runs nothing. A stale identifier (another `check`, another `after`, another ticket), an attempt that succeeded, is waiting for
a run or is already accepted, an open gate and unknown fields (`step`, `answer`, `auto`) give an error without a write.
The decision is made only by an explicit call: the caretaker's actions (`caretaker_problem`/`caretaker_step`) are rejected, and
automatic consent does not cover it. The next attempt of the ticket (a retry) or an attempt after the merge of another ticket
starts without acceptance and holds the gate again; `fold` counts acceptance only for the current attempt with the same `after`,
so after a service restart an accepted attempt does not close the gate again.

After all tickets are merged or rejected and the merged tickets' worktrees are cleaned up, the `close`
step writes `summary.md`. With `check: manual` it requires the section "Co przetestować ręcznie i jak" (What to test manually and how);
with `check: auto` it skips this list. **"Do sprawdzenia na pulpicie" (To check on the desktop) (PT-183):** a step note (ticket, review,
fixes) may have a heading "Do sprawdzenia na pulpicie" with a list of points (items may take several lines,
an empty section is allowed; a next paragraph after a blank line is not glued to an item, and ``` blocks are skipped;
steps of rejected tickets are skipped). With `check: manual` the service collects these points from all steps of the card and appends
to the summary in `card.closed` a subsection "Do sprawdzenia na pulpicie (z notek kroków)" (To check on the desktop (from step notes)); every item has a reference
"(krok …, ticket …)" (step …, ticket …), and the same item from several notes (regardless of case and spacing) appears once,
with all references. Without such sections the summary is unchanged. The mechanism does not depend on the project or
the box. The ticket and review prompts tell the agent to mark a test that needs a real desktop in this section
and not to ask the user for it during the work. The `card.closed` event shows the summary in "Do sprawdzenia" (To review).
`check: auto` does not merge into the main branch. The panel gives an explicit "Zmiany gałęzi" (Branch changes) (the `Git.Diff` diff view, also during work; `CardBranchDiff` stays in the service for compatibility), "Do poprawy…" (Needs fixes…) and
"Scal do gałęzi głównej" (Merge to the main branch). Sending back with remarks starts a plan correction on the same card branch;
for the "Od razu" (Immediate) entry the service creates the base of a one-ticket plan only at this request.
The merge to main runs in the git sandbox, checks local checkout collisions and records
`card.merged`, then moves the card to Done. Earlier steps do not move
the main branch.

PT-212: an explicit `MergeCardToMain` records under the repository lock an `intent`/`result` pair
with the `main-merge` action. The intent contains a stable `operationId`, the assignment (`requestSeq`), `baseVersion`,
`targetRef`/`targetBranch`, `sourceCommit`, `targetCommitBefore`, the planned `resultCommit`
and the initial materialization tree (`materializationBaseTree`). PT-207 adds `resultIndexTree`:
the expected index after the result is brought in, with the user's unrelated staged changes. `resultTree` remains
the tree of the merge commit alone; local staged changes do not go into this commit.
The shared tree preparation comes from PT-199. The commit is written
only after the intent; its content and SHA do not change on a retry. The `main-merge.checkpoint`
checkpoints distinguish `prepared`, `materialized`, `ref-moved`, `result-saved`
and `projected`. PT-205: new successes of both methods (`MergeCard` and the backward-compatible `MergeCardToMain`)
record a single `card.merged` with `operationId`, `targetRef`/`targetBranch`, `sourceCommit`,
`targetCommitBefore`, `resultCommit` and `baseVersion` from the shared intent, then `card.finished`.
The old `card.main-merged` still means completion to the saved `main`; journals are not rewritten.
A mixed completion record in one assignment gives one result, one history and report item.
The flow keeps `derived.flow` also in Done/Archive: `merged` is the completion marker,
`merge` contains the saved result (the old `main`/`commit` fields read as the target and the result).
`mainMerged` remains an alias of the marker for old clients. The snapshot, Sessions, history, the merge phase
and the report show the actual full target. The report shows a card's completion in the period of the event
and does not count it among open cards. Reporting another error does not append an occurrence to a merged card.
`MergeCardToMain` still returns `{ok, id, main, already}`; a historical success uses the saved target,
not today's main. Accepting other targets through `MergeCard` remains the scope of PT-200.

`CardBranchDiff` without a target uses the current ref of the saved base (without replacing the pinned SHA).
The overload with `targetRef` accepts `refs/heads/…` or a locally known `refs/remotes/…`;
short names, symbolic refs and nonexistent ones are rejected. With a base of `unknown` an explicit ref is required.
The response contains `targetRef`, `targetBranch`, `branch`, `diff` and the old key `main` as the name of the comparison.
The read alone records no events, does not change the base and is not consent to a merge.

A restart or a retry of the old API finds the current assignment's operation and continues it
on the saved SHAs; a tick may recreate only an already saved intent. An unresolved question about the base
blocks both an explicit merge (also a retry) and recovery from a tick until the user decides.
A change of the named main,
a foreign ref, the index or collisions with local work in the merged paths cause `main-merge.waiting`
and "Czeka na mnie" (Waiting for me), without rolling back files and without a second merge. Ignored files are checked
for name collisions, without reading and hashing their content. Unrelated edits and artifacts do not block
recovery. When the ref points to `resultCommit` (or its descendant after further user commits), git is
finished: recovery checks the SHA and the result tree, then completes the result and the projection regardless of further
work in the index, checkout and source and of a later change of `tide.mainBranch` — the main branch policy
applies only to execution before the ref is moved. Collisions with untracked and ignored files are checked
only in the merged paths and their parents (pathspec), without enumerating whole ignored directories.

Waiting does not stop the synchronization of the user's moves, plan correction or the card's other steps.
After the cause is removed, resuming uses the same ID. "Do poprawy" (Needs fixes), a plan return, a new close-out or a manual finish
after the intent records `main-merge.abandoned` if the result did not reach the target ref and no success was recorded —
also with a foreign ref (e.g. a user commit on main), with or without materialization. Abandoning changes neither files nor the index, releases the lock of a new assignment
and does not allow resuming the old intent further. The next close-out requires a new explicit merge.
Within the same assignment a new operation may take over the exact index of an abandoned materialization with the same
target and an unchanged ref; the local collision check still covers the old and new merged paths.
Without such a decision a foreign ref remains an explicit wait; it is never consent to re-execute,
reset or overwrite. Abandoning with a foreign ref touches neither the ref, the index nor files, and a materialization
left in the checkout is not inherited (it requires an unchanged ref) — a new merge will refuse as with
foreign staged changes.
Unrelated staged changes and changes outside the index remain supported (K-43, PT-207). The index copy is
prepared and refreshed in a private directory, with the worktree and the git directory read-only;
the write covers only objects and the index copy. Only after the intent is recorded does `read-tree -m -u`
materialize the difference between the initial and the resulting index. Unrelated entries (blob and mode), their delta
against the new HEAD and the file bytes stay unchanged. We use no reset, stash or untracked cleanup.
Before recording the intent we check the preview token again, also after preparing the index copy.
StepGit sets `GIT_OPTIONAL_LOCKS=0`: the source cleanliness check through `status` neither refreshes nor
converts its index to split-index, so it does not invalidate consent by its own read.
Recovery accepts only the saved initial or resulting index; new foreign staged changes cause waiting.
A partial write of files with an unchanged index and a collision stays in an explicit wait, without overwriting.
Older intents without `resultIndexTree` still use `resultTree` as the expected index.
A missing target checkout uses the PT-206 stage: the plan and the full intent before creating,
resource ownership in checkpoints and handing over to the same executor. This also applies to the old main API.
