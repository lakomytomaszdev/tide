# Tide Mist — design system

As of 26.10.2026. A description of the look of the **Mist** variant of the Tide theme for KDE Plasma 6: principles, tokens, components and a wallpaper brief. All numbers come from `theme/palette/mist.json`, which is the single source of truth — if this document and the palette differ, the palette wins.

## Character

Mist is a soft, dark graphite with teal as its signature and pastels for statuses, in a layout inspired by macOS. It is a theme for daily work with AI agents, so a calm background and legible states matter more than effect.

- **A single source of truth.** Colors, radii, opacities and fonts are in the palette. Generators read it; values are not hard-coded.
- **Graphite, not steel.** Backgrounds are neutral, with no blue tint (unlike Nord/Nordic).
- **One accent.** Teal is the accent and the "agent working" state. Pastels have fixed meanings; they are not decoration.
- **Softness.** Large rounding, thin low-contrast borders, transparency with blur instead of heavy shadows.
- **Legibility over style.** Every text has a contrast of at least 4.5:1 against its background (the tests check this).
- **No emoji and no gradients as ornament.** The only soft color transitions are on the wallpaper (waves behind the cat, or blobs).

## Palette

### Backgrounds and text

| Token | Color | Where |
| --- | --- | --- |
| `background.desktop` | `#181b1e` | Desktop, the wallpaper base, text on a selection |
| `background.view` | `#1a1d20` | Lists, text fields, view interiors |
| `background.window` | `#1d2124` | Window background, inactive title bar |
| `background.titlebar` | `#23272b` | Active title bar, headers |
| `background.panel` | `#24282c` | Plasma bars and the dock (with transparency) |
| `background.card` | `#262a2e` | Cards, buttons, tooltips, fields on startup screens |
| `text.primary` | `#eceff1` | Main text |
| `text.secondary` | `#c9ced2` | Supporting text, labels |
| `text.muted` | `#a9b0b5` | Descriptions, times, inactive elements |

### Accent and pastels

| Token | Color | Meaning |
| --- | --- | --- |
| `accent` = `pastel.teal` | `#6fd6c6` | The accent: selection, focus, the dot of a running application. The "agent working" state |
| `pastel.lavender` | `#b9a7f5` | The second brand color: logo, worktrees, RAM |
| `pastel.peach` | `#f5b99a` | "Waiting for you", disk. The AI limit will run out before the reset (`semantic.runout`) |
| `pastel.butter` | `#ecd58c` | Attention: changes, unmerged commits, 16–40% of the limit. The minimize button |
| `pastel.rose` | `#f2a0b3` | Error, little limit left (≤15%), deletion. The close button |
| `pastel.mint` | `#9fe3b8` | Good: merged, plenty of limit (>40%). The maximize button |
| `pastel.sky` | `#a6c8f0` | Links in the terminal, Codex. The "agent finished" state (`semantic.done`, an empty circle) — information, not a call to act |

The diff view (`apps/common/qml/git/DiffView.qml`) colors lines through `semantic.added` (mint, added) and `semantic.removed` (rose, removed); the row background is that color with low opacity.

On a teal selection the text is dark (`background.desktop`) — light text on teal fails the contrast test.

## Typography

| Role | Font | Size | Weight |
| --- | --- | --- | --- |
| Interface, menus, bars | Geist | 10 pt (about 13 px) | 400 |
| Smallest legible text | Geist | 8 pt | 400 |
| Window title | Geist | 10 pt | 500 |
| Headings in popups | Geist | 15 px | 600 |
| Numbers in widgets | Geist Mono | 11–18 px | 600 |
| Clock on the lock and login screens | Geist | about 96 px | 600 |

Geist v1.7.2 (OFL license), installed into the user directory. Rider and the terminal keep their own fonts. The interface text is in Polish.

## Shapes and depth

| Token | Value | Where |
| --- | --- | --- |
| `radius.small` | 9 px | Tooltips, small buttons |
| `radius.frame` | 10 px | Top corners of windows (bottom ones square — an Aurorae limitation) |
| `radius.bar` | 14 px | The top bar as a pill |
| `radius.card` | 18 px | Popups, cards, the password field |
| `radius.window` | 22 px | Large surfaces in mockups |
| `radius.dock` | 28 px | The dock and side panels |
| `size.bar` / `size.barSlim` | 34 / 30 px | Main bar / left monitor bar |
| `size.sidebar` | 420 px | Width of the sidebar on the left monitor |
| `opacity.panel` | 0.7 | Bars with blur turned on |
| `opacity.popup` | 0.85 | Popups |
| `opacity.withoutBlur` | 0.95 | When KWin does not blur the background |
| `opacity.border` | 0.07 | A 1 px border in the text color |
| `shadow.size` / `shadow.opacity` | 12 px / 0.3 | Shadow of panels and popups |

With a maximized window Plasma snaps floating panels to the edge and makes them opaque — this is system behavior.

## Components

- **Top bar (main monitor):** a 34 px pill — the logo as a menu button, the global menu, a clock with the date on one line in the middle ("pon. 26 paź 17:05" (Mon, 26 Oct 17:05)), user shortcuts, the system tray.
- **Left monitor bar:** 30 px — the global menu and a clock.
- **Sidebar (left monitor):** 420 px, floating, at the left outer edge, from the bottom of the slim bar to the bottom of the screen, visible until a window covers it (then the edge or Meta+S). The panel frame has the prefix `west` and the `dock` radius (content margin 10 px). From the top: the "Tide" header with a shortcut badge (Geist Mono 11 px on a `card` background, the text in line with the tile content), then a calm column of separate **sidebar tiles** (`SidebarTile`): a `card` background, a 1 px border in the text color with `opacity.border` opacity, the `card` radius, 14 px of padding, a small header (9 pt, 600, `text.secondary`) with room for short information or a link button on the right, about 14 px of gap between tiles (10 px in the tile and 4 px of the panel). Free space stays at the bottom (a stretching spacer at the end of the panel); the whole thing fits in about 1040 px without scrolling (`make preview-widgets` → `sidebar-*.png`).
  - **Agents:** one line per session, 30 px — a state dot (teal working, peach waiting, an empty sky circle finished), the project, a short state word in the state color ("czeka" (waiting), "pracuje" (working), "skończył" (finished), "bezczynna" (idle)), the time ("3 min", "1 h") in Geist Mono. A waiting row has a light peach background and a small solid "Przejdź" (Go) button (a Codex Desktop session: "Otwórz w Codex" (Open in Codex)); other rows have no button (the column is reserved so that words and times stand in even columns). Finished (opacity 0.72) and idle (0.55) are dimmed. Waiting at the top, at most 6 rows, the rest under a "+N więcej" (+N more) link, which opens the full list in a small window next to the bar; finished ones hidden behind "Pokaż zakończone (N)" (Show finished (N)). In the header, counters in words as in the dock. The same rows (`SessionRows`, data from `AgentRows`) are drawn by the agents tile popup in the dock and the "+N więcej" window — there without a limit (scrollable) and with a second, muted line (8 pt): what it is waiting for and on what, or the tool, "+N subagents", the branch, the title. A coordinator (a session that assigns work to subagents) has ticket progress in sky next to the project ("TOM-20 · 8/13", a link to the Sessions view of the Tide window with that coordinator); the "Sesje" (Sessions) link stands in the popup header, and in the bar — in the bottom row when some coordinator is running.
  - **System:** gauge rows "label | 4 px bar | value" (CPU teal, RAM lavender, "Dysk" (Disk) peach — the fullest partition) with a shared width of labels and values, under CPU a history of the last minute (28 px), the graphics card in one sentence ("GPU 31% · VRAM 0,6/8 GB"), partitions one line each with a bar ("/ 9,5 GB wolne" (free); with a single partition only a note under the Disk bar), at occupancy above 85% one muted line "Dysk prawie pełny" (Disk almost full) (with "worktrees zwolnią X" (worktrees will free X) only when there is something to clean up) and an "Analiza dysku" (Disk analysis) link. "Monitor systemu" (System Monitor) as a link in the header.
  - **Docker:** "● 9 działa · ● 1 padł · 6 projektów" (● 9 running · ● 1 crashed · 6 projects) — in the same language as the dock (teal, rose only for crashed ones; the number of running ones always shown, zero muted), below it at most two containers that crashed, in rose ("translations-slack-bot — padł 21 h temu · kod 137" (crashed 21 h ago · code 137), a click opens the Tide window on it), an "Otwórz w Tide" (Open in Tide) link in the header. Without Docker or the service — one muted line with the reason. It stands right under System.
  - **Worktrees:** "● 2 w pracy · ● 0 do sprzątnięcia (16 GB)" (● 2 in use · ● 0 to clean up (16 GB)) — in the same language as the dock, but always with both numbers (zero muted), an "Otwórz okno" (Open window) link in the header.
  - **Board:** a header "Tablica · 11 kart" (Board · 11 cards) with an "Otwórz w Tide" link, below it parts as in the dock ("● 2 w toku · ● 1 czeka · ● 1 do sprawdzenia" (● 2 in progress · ● 1 waiting · ● 1 to check), without agent work "○ 3 do zrobienia · ○ 12 w backlogu" (○ 3 to do · ○ 12 in backlog), without cards "brak kart" (no cards)) and up to three 26 px rows: a state dot, the ID in sky (Geist Mono), the description, progress or a state word; a waiting row on a peach background ~0.10. A click opens the Tide window on the card.
  - **AI Limits:** two gauge rows (GPT, Claude) with the lower of the remaining limits in the level color and a muted "tydzień · reset poniedziałek 20:00" (week · reset Monday 20:00) under the bar; a service problem is one muted line.
  - **Buttons** are `TextButton` (Qt Quick Templates `AbstractButton`: the Button role, Tab, space and Enter): a link in the accent color or a solid one in a state pastel ("Przejdź"). Mockup: the "Sidebar" artboard on the canvas. In the Tide window the buttons are `Tide.Button` (the section "Tide window interface standard").
- **Dock:** the full width of the main monitor, 64 px. Applications in the middle; on the left system tiles: System and right after it Docker, on the right the AI matters: board, agents, worktrees, AI Limits, local models.
- **Widget tile:** a separate card 48 px high (the dock 64 px) with the `tile` radius (12 px), a background in the widget's pastel with opacity ~0.11 (on hover ~0.2) with a border in that pastel (~0.24), 12 px of gap between tiles. Always two lines: a label in `text.secondary` (8 pt: "Agenci" (Agents), "Worktrees · 5 w pracy" (5 in use), "System", "Limity AI" (AI Limits)) and a value in words (10 pt), numbers in Geist Mono. The color tells the state. The shared components `DockTile` and `TileMeter`, texts and plurals in `Texts`, preview: `make preview-widgets`.
  - **Agents** (teal, peach when someone is really waiting): "● 2 pracują · ● 1 czeka · ○ 2 gotowe" (● 2 working · ● 1 waiting · ○ 2 done), only nonzero parts; finished sessions have an empty sky circle (`semantic.done`) and do not change the tile color; without sessions a muted "brak sesji" (no sessions).
  - **Worktrees** (lavender): "15 do sprzątnięcia · 16 GB" (15 to clean up · 16 GB), how many are in use next to the label; with nothing to clean up "nic do sprzątnięcia" (nothing to clean up).
  - **System** (sky): "CPU 34%  RAM 61%  Dysk 88%" with a thin bar under each gauge (teal, lavender, peach). Rings at 48 px came out about 26 px with an illegible number inside, so the tile shows the percentage as text and a bar.
  - **AI Limits** (the color of the lower limit): "GPT 91%  Claude 86%" with bars in the level color.
  - **Board** (mint, before agents): "Tablica · 11 kart" and "● 2 w toku · ● 1 czeka · ● 1 do sprawdzenia" (teal, peach, lavender), only nonzero parts; without agent work (today, before PT-5) "○ 3 do zrobienia · ○ 12 w backlogu", without cards a muted "brak kart". The popup is 23 grid units (~420 px): a board mark in mint, "Tablica" 11 pt/600, "Otwórz w Tide ↗" in the accent, a summary "11 kart w 7 projektach · własne 6 · Firma 5" (11 cards in 7 projects · own 6 · Company 5), rows (a dot, "K-13 project" with the ID in sky, a muted second line, a state word in the state color, on the right "Przejdź" in peach, progress "3/10" in Geist Mono, "Uruchom" (Run) in the accent for a card to do — the Tide window straight to "Co ma zrobić agent?" (What should the agent do?) — or a "Pokaż" (Show) link) — waiting first (peach background ~0.10), then in progress, to check, to do, and when there are few of those — the top of Backlog; at the bottom a field "Dopisz do backlogu i Enter" (Add to backlog and Enter) on `background.view` (38 px, the `frame` radius, a "＋" in the accent) with a project pill "◆ plasma-tide ▾" (remembered in the `quickProject` setting). Under the field the result: "Dopisano K-23 do backlogu" (Added K-23 to the backlog) in mint or a service error in rose.
  - **Task** (teal, right after agents): "＋ Zadanie" (＋ Task) and "nowe dla agenta" (new for an agent) — the entry to the new task window.
  - **Docker** (neutral `text.secondary`, background ~0.20 as in the mockup — color only in the dots): "Docker · 6 projektów" and "● 9 działa · ● 1 padł" (teal; rose only when something crashed), with nothing running "nic nie działa" (nothing running), without Docker "Docker nie działa" (Docker not running), without the service "usługa nie działa" (service not running) (muted). Projects are compose projects with something running. A 400 px popup (the mockup "Tablica and Docker tiles"): a header with a cube mark in sky and an "Otwórz w Tide ↗" link, three small numbers on `background.window` (CPU of the whole processor, RAM of containers, "Do odzyskania" (Reclaimable) in butter), project rows (a teal dot, the name, HTTP ports in teal Geist Mono, "3/3", a square 26 px stop button; a single container without compose under its own name), containers that crashed, on a rose background ~0.09 with "padł 21 h temu · kod 137", a "Logi" (Logs) link (the Tide window on the container) and "Uruchom" in mint ~0.16, a footer "Zatrzymane: 3 · testcontainers ukryte: 1" (Stopped: 3 · testcontainers hidden: 1). The action result under the row (mint or rose) fades after 12 s.
  - **Models** (neutral `text.secondary` like Docker, color only in the state dot of the default server): "Modele" (with the endpoint name when the default is not this computer: "Modele · mac") and "● qwen3:8b · na karcie 100%" (on card 100%; mint, butter when part of the model is on the processor), "● gotowe" (ready), "● pracuje: opis kart · 2 w kolejce" (working: card description · 2 queued) and "● pobieram qwen3:14b 41%" (downloading; teal, pulsing), "● brak modelu qwen3:14b" (model missing; butter), "● serwer nie odpowiada" (server not responding; rose); without the service a muted "usługa nie działa" (in the popup the command `systemctl --user start tide-daemon` on a separate line in Geist Mono), before the first reading "łączę się…" (connecting…). Butter for a missing model only for background roles (report, card descriptions); optional roles (agent, search) are visible only in the popup. The value up to 210 px, long model names shortened in the middle. A 400 px popup: a header with a layout mark in lavender, "Odśwież" (Refresh) in `text.secondary` and "Otwórz w Tide ↗"; the report mode on lavender ~0.09 ("Raport: lokalnie" (Report: local)); a card per server on `background.window` (the `tile` radius, a border of `opacity.border`): a dot, the name 600 ("Ten komputer" (This computer) for a local address), "domyślny" (default), the state on the right (mint/rose), the address and version in Geist Mono `text.muted`, the reason for unavailability in rose, the queue in teal ("Pracuje: opis kart (qwen3:8b) od 2 min · 2 w kolejce: raport" (Working: card description (qwen3:8b) for 2 min · 2 queued: report) — pending ones in the same form as on the tile), a download with a bar in the accent ("Pobieram qwen3:14b · 42%", then for 15 s "Pobrano" (Downloaded) in mint or for 60 s an error in rose), the model in memory (the name in Geist Mono, "na karcie 100%" with a 4 px bar in mint or butter, the size, the context and "zwolni pamięć za 4 min" (will free memory in 4 min)), "Pobrane: 5 modeli" (Downloaded: 5 models); roles (a dot, the name, the model in Geist Mono, the reason in rose); settings warnings in butter.
  - Every tile has a tooltip with a one-sentence explanation, e.g. "Sesje agentów AI: 2 pracują, 1 czeka na ciebie" (AI agent sessions: 2 working, 1 waiting for you). Four tiles with gaps take up to ~850 px (agents with three parts up to 240 px). On the right side of the dock there are five tiles (Board, Agents, Worktrees, AI Limits, Models) — together with gaps up to ~1220 px: Board up to ~280, Agents up to ~270, Worktrees up to ~225, AI Limits ~155, Models up to ~245 px (measured from `make preview-widgets`).
- **The "Nowe zadanie" (New task) window** (mockup #22, `apps/newtask`): a `background.window` background, the header "Co ma zrobić agent?" (21 px, 600) with a shortcut badge in Geist Mono, fields on `background.view` with a 1 px text border at ~0.09 opacity (focus: teal ~0.75), the `frame` radius + 2. The repository as a drop-down list (a teal diamond, the name, "zmiany 12 min temu" (changes 12 min ago)), next to it a "Claude Code / Codex" switch (selected: teal ~0.16 with an outline). Under the task a muted key hint and a character counter. Screenshots as 150×92 thumbnails with the file name in Geist Mono and an ×, "Zrób zrzut" (Take a screenshot) in a dashed frame. The "Nowy worktree" (New worktree) card on `background.card` with a teal switch and the branch in a lavender tag (like branches in Worktrees) with "✎ zmień" (✎ change); warnings (changes on master) in butter, errors in rose in a frame. Footer: what will happen, "Anuluj" (Cancel) with an outline and a solid teal "Uruchom agenta Ctrl+↵" (Run agent Ctrl+↵). The "nowe" (new) marker on a fresh session: teal ~0.16, text in teal, 8 pt.
- **The "Tide" window** (mockup approved 27.10.2026, `apps/tide`): one window with a view bar on the left and the view on top; the title "Tide", a `background.window` background. A 200 px bar on `background.view` with a 1 px border on the right (text ~0.06), 18/12 px of padding, 4 px between items. At the top the "three waves" logo without a tile (26 px, a 2.4 px stroke: teal, lavender, peach) and "Tide" 17 px/600, 16 px of spacing. Items (Tablica (Board), Sesje (Sessions), Worktrees, Docker, Raporty (Reports), Modele (Models) — the order from `views.json`): 38 px, the `frame` radius, 10 px of indent, an 18 px mark with a 2 px stroke on a 24×24 grid (`RailIcon`), the name 14 px; selected — a teal background ~0.10, the name `text.primary` 600, the mark in teal; the others — `text.secondary` 500, the mark `text.muted`; hover — text ~0.04; keyboard focus — a teal outline ~0.6. An 11 px badge on the right: a number in Geist Mono `text.muted` (Worktrees — how many in use, Docker — how many running), "● N" in peach (Sessions — how many agents are waiting) or in rose (Docker — how many containers crashed, tone `negative`), a word in peach ("nowy" (new) in Reports), "● N" in peach in Models (how many things that Tide uses do not work); hidden without data or at zero. At the bottom of the bar an optional view frame (`RailInfo`: a `background.window` background, a ~0.06 border, the `tile` radius, a 11 px label `text.muted`, a value in Geist Mono 12 px `text.secondary`, a 11 px note — e.g. "Dane tablicy" (Board data)). A view that does not exist yet shows a header like other views (21 px/600, "W przygotowaniu" (In preparation)) and in the middle an empty state: a 460 px card on `background.view` with the `card` radius, a 26 px view mark in teal on a teal ~0.10 square, a 15 px/600 sentence and a 13 px `text.muted` description. Keys: Ctrl+1…6 views, Ctrl+PgUp/PgDown previous and next, arrows in the bar, Ctrl+W closes. Above the `RailInfo` frame stands the "Projekty i tagi" (Projects and tags) item (K-128, PT-507): an ordinary `RailItem` (38 px, the `projects` folder icon, the same style and focus as the others), selected like them — a teal background ~0.10 when the Projects and tags page is open in the Board view (then "Tablica" is not selected, and a click on it returns to the board). A click opens that page (`showItem("projekty")`). The green Board link is no longer supplied (`RailInfo` would still show `link` if some view supplied it); the "Dane tablicy" frame (`railInfo`) stays. **Projects and tags** (`ManagePage.qml`) has `Tide.TopBar`: "← Tablica" first from the left (objectName `manageBack`), then the title; a click and Esc return to the board. The Sessions and Worktrees views are the former windows with no change in content. There are three entries in the menu — "Tide" (with view actions) and "Tide Sesje" and "Tide Worktrees" opening the window straight on a view; all have `StartupWMClass=tide`, so the old shortcuts pinned in the dock group under `tide.desktop` and show the same single window.
- **The "Tablica" (Board) view** (mockup approved 27.10.2026, `apps/board`, the default view of the Tide window): 20/24 px of padding, 14 px between rows. The header "Tablica" 21 px/600 with a 13 px `text.muted` summary ("11 kart · 2 w toku · 1 czeka na ciebie · 1 do sprawdzenia" (11 cards · 2 in progress · 1 waiting for you · 1 to check), with a filter "· filtr" (· filter)), a 220×36 px search on `background.view` (a text border ~0.08, the `frame` radius) and "＋ Karta" (＋ Card; accent, `background.desktop` text, 13 px/600). The filter row: 28 px tag pills (Wszystkie (All) + tags with a 7 px dot; selected — accent, text and dot `background.desktop`; the others `card`), a dashed "＋ Tag" (a popup with a name, a palette color and a description), on the right "Projekt: wszystkie ▾" (Project: all ▾; a list with card counts) and a "Razem | Wiersz na projekt" (Together | Row per project) switch (selected: teal ~0.16, text in the accent). Columns: six equal ones (Done too — at a narrow 150 px titles broke one word per line; at least 150 px per column, so at the smallest window of 1380 px it comes to about 180 px), 10 px gap; a column — a `panel` background ~0.55 (Done ~0.35), radius 16, 10 px of padding, a header with a 9 px dot (Backlog empty `text.muted`, To do `text.secondary`, In progress teal, Waiting for me peach, To check lavender, Done empty sky), the name 13 px/600 and a count in Geist Mono. A card (details and states: the section "A card on the Board"): a `card` background, a text border ~0.07, the `tile` radius, 10/11 px of padding, 6 px between lines — the ID 11 px/600 in sky (Geist Mono), the project 11 px `text.muted`, a "YT LOG-131" marker (Geist Mono 10 px on text ~0.06), the title 13 px/600 (up to 4 lines), tags 11 px on text ~0.05 with a 6 px dot in the tag color, a note 12 px ("✓ …" in mint, waiting in peach, queued muted). The state from the coordinator (PT-5): a 4 px bar (teal, peach for a waiting one), "● Claude · koordynator · 3 z 10" (● Claude · coordinator · 3 of 10), column actions ("Otwórz w Sesjach →" (Open in Sessions →), "Przejdź do agenta" (Go to the agent) in peach, in To check the branch in lavender and "Gotowe" toned with mint). A waiting card — a peach background ~0.08 and a border ~0.32; selected — a teal outline ~0.6 with a glow. Done — compact cards ("K-10 · project", the title 12 px `text.secondary`), the last six and "+ N starszych" (+ N older). To do ends with a dashed teal ~0.45 hint "Upuść kartę z Backlogu" (Drop a card from Backlog) / "zapytam, czy uruchomić agenta" (I will ask whether to run an agent), and a card in To do has "Uruchom agenta…" (Run agent…; accent, after launching toned "Uruchom ponownie…" (Run again…); the tile buttons are `Tide.Button`, described in "A card on the Board" below), and the manual columns "＋ Dodaj kartę" (＋ Add card) 12 px `text.muted`. A card from "Do sprawdzenia" (and from Done computed from the coordinator) can be dragged only to Done — confirming the check or the merge — or back to To do ("do poprawy" (to fix)), also with Ctrl+→/←; other columns refuse with a message. "Otwórz w Sesjach →" and "Przejdź do agenta" lead to the card's coordinator (the Tide window on Sessions, `/Coordination ShowWindow`), and without a coordinator — to the session window (`/Agents ActivateSession`). Dragging: a ghost of the card under the cursor (scale 1.02, a teal outline), the source dimmed to 0.35, a 2 px teal line at the drop place, the target column with a teal outline ~0.45, computed columns dimmed. The card panel (K-128, PT-504; described in the section "The drawer and the card panel"): a large `Tide.Sheet` drawer on the right (about 64% of the window, at least 640 px) with the modes Read and Edit; the form (Edit, the default for a new card) has fields like in the New task window (the title, the project with a list of projects and repositories, tags as switches — project tags fixed with the note "z projektu" (from the project), the description, a note), when editing the column (Backlog / To do / Done) and "Archiwizuj" (Archive) in rose, a service error in a rose frame ~0.45, a waiting commit in butter. The card's plan and specification (PT-5): under the panel header a switch "Opis | Plan · 2/6 | Specyfikacja" (Description | Plan · 2/6 | Specification; like "Razem | Wiersz na projekt", 28 px; document tabs only when they exist; the selected tab stays for subsequent cards while the window runs, and a card without such a document shows "Opis"), the drawer has the same width with and without a document, and "Rozwiń" (Expand; the ⋯ menu) gives almost the whole window. The document: the title 17 px/600, a 22 px status pill on a color ~0.16 (plan: active teal, paused butter, finished mint; specification: ready mint, draft butter), "zmieniony 11:48 · plan karty · w repozytorium" (changed 11:48 · card plan · in the repository) 12 px `text.muted`, a Geist Mono 11 px path with "Otwórz plik" (Open file; outlined) and "Pokaż w katalogu" (Show in folder; flat); the ticket table on `background.view` with a ~0.07 border and the `tile` radius — the ID in sky (Geist Mono 12 px/600), the title 13 px with remarks 11 px `text.muted` below, the state as a word in the state color as in Sessions (in progress teal, waiting peach, review lavender, done mint, rejected rose, to do `text.muted`), the branch in Geist Mono 11 px; a ticket from outside the card dimmed to 0.5. "Co przetestować ręcznie" (What to test manually) — a butter frame ~0.38 on butter ~0.08 with a header in butter; the rest of the document as Markdown 13 px `text.secondary` (headings with spacing above, links in sky underlined, images in italics as "(obraz: opis)" ((image: description)), HTML from the source as plain text), at the bottom "Inne dokumenty karty" (Other card documents) with "Otwórz" (Open) in the accent. "Projekty i tagi" (the link at the bottom of the bar): a list of projects with tags, the repository path (Geist Mono), the location of AI documents, the tool and the mode — a form under the row; repositories without a project with "＋ Dodaj jako projekt" (＋ Add as a project); tags with a color, a description and a card count, a form with palette colors. YouTrack (PT-7): the "YT LOG-131" marker on a card has after the key a 10 px/600 sync badge — "✓" in mint (in sync), "⟳" in sky (waiting to be written) or `text.muted` (YouTrack unavailable), "⚠" in butter (YouTrack refused the state) or in rose (a conflict — then the marker background is also rose ~0.14); the tooltip says the state in words. Next to the board header, before the search, discreet 26 px pills without a background, with a ~0.4 border and 11 px text: "1 konflikt z YouTrackiem" (1 conflict with YouTrack; rose), "YouTrack niedostępny · 2 czeka" (YouTrack unavailable · 2 waiting; `text.muted`, not clickable), "5 nowych zgłoszeń w YouTracku" (5 new issues in YouTrack; peach, opens the import). The card panel has no "Zgłoszenie w YouTracku" (YouTrack issue) field (it disappeared from the form in PT-504); in Read mode it shows a state frame on text ~0.04 (on a conflict rose ~0.10 with a ~0.45 border): the badge, "LOG-142 · In Progress" in Geist Mono 12 px, a flat "Otwórz w YouTracku ↗", an 11 px sentence and on a conflict two outlined buttons "Weź z YouTracka" / "Nadpisz z Tide". "Projekty i tagi" starts with a "YOUTRACK" section: a state line with the column-to-state map 12 px `text.muted` (unavailable — butter), an outlined "Importuj z YouTracka · 2 nowe" (Import from YouTrack · 2 new), conflicts (also of tickets without a card) in rose frames with the same two buttons and an import panel (a filter, 12 px rows: a 16 px checkbox in the accent, the key in sky Geist Mono, the title, "nowe" in peach, "→ project" or "już jest: K-13" (already exists: K-13) muted and without a checkbox, the state; at the bottom "Zaznacz wszystkie" (Select all) and "Importuj zaznaczone (N)" (Import selected (N)) in the accent, the result in mint). The project form has a "YouTrack" row: a 110 px key field in Geist Mono, a sync switch (inactive without a key) and a sentence on what it means; a project row shows "YouTrack LOG ⟲" in sky (without sync — muted). The empty state (without a board repository): a 480 px card on `background.view` with a board mark, "Tu będzie twój backlog" (Your backlog will be here) and "＋ Pierwsza karta" (＋ First card). Bad files and a revert in progress — a butter bar ~0.06 above the columns (like the number warnings in Sessions). Keys: Ctrl+N new card, Ctrl+F search, arrows across cards, Enter opens, Ctrl+←/→ moves to the neighboring manual column, Ctrl+↑/↓ within a column, Esc closes the panel or the page. There are three entries in the menu — "Tide" (with view actions) and "Tide Sesje" and "Tide Worktrees" opening the window straight on a view; all have `StartupWMClass=tide`, so the old shortcuts pinned in the dock group under `tide.desktop` and show the same single window.
  - **"Co ma zrobić agent?"** (What should the agent do?; the mockup "Card launch" approved 27.10.2026, `LaunchDialog.qml`): modal over the board dimmed with `background.desktop` ~0.62; a window up to 900 px on `background.window`, a text border ~0.08, the `card` radius, 20/28 px of padding, 16 px between sections; the content scrolls when the window is short (smallest 620 px), the footer stays. The header "Co ma zrobić agent?" 21 px/600, on the right "Backlog → Do zrobienia" (after moving) or "Do zrobienia" 12 px `text.muted`. A card on `background.view` (the `tile` radius, a ~0.08 border): the ID in sky Geist Mono 12 px/600, a "◆" in the accent and the project 14 px/600, tags, on the right "tytuł z karty: …" (title from the card: …); "Zadanie" (Task) — a 96 px field like the description in the card panel, under it "Opis z karty. Zmiany tutaj zapiszą się też w karcie." (Description from the card. Changes here will also be saved in the card.) 11 px. A row: "Narzędzie" (Tool) with a "Claude Code | Codex" switch (30 px, 13 px) and on the right "Dokumenty AI" (AI documents) with a Geist Mono pill ("w repozytorium · docs/agents/" (in the repository · docs/agents/) or "w Tide · tide-board/docs/<project>/" (in Tide · tide-board/docs/<project>/), `card`, radius 7) and a "zmień w projekcie" (change in the project) link in the accent (the Projects and tags page; after returning the window comes back). "Tryb pracy" (Work mode) — three equal cards (`card`, the `tile` radius; selected teal ~0.09 with a ~0.6 outline) with a 14 px circle (selected: a 4 px outline in the accent), a 15 px/600 title, a 13 px `text.secondary` description and an 11 px note; under them a step bar — `card` pills with a number in the accent Geist Mono and "→". At the bottom two `card` cards: "Nowy worktree" (a 34×20 px switch, `Toggle.qml`; the branch in lavender ~0.10 Geist Mono 12 px, shortened in the middle, "✎ zmień" in the accent opens the field; in spec mode dimmed to 0.5 and "bez gałęzi" (no branch)) and "Test ręczny po zakończeniu" (Manual test at the end; 280 px, off by default, a sentence about the list "Co przetestować ręcznie i jak" or about the AI review). The footer on `background.view` with a ~0.06 line: a "what will happen" sentence 12 px `text.muted` (changes with the tool, the mode, the worktree and the test; during the start — which step is running), an error in rose above it, the buttons "Anuluj" and "Tylko przenieś" (Only move; outlined, only after the move) and "Uruchom agenta  Ctrl+↵" in the accent. A card without a project with a repository: a butter frame ~0.07 with an explanation and a project choice, the start button disabled. Keys: Ctrl+Enter runs, Esc — Cancel (first closes branch editing), ←/→ on the mode cards.
- **The "Sesje" (Sessions) view** of the Tide window (the former "Tide Sesje" window, mockup approved 26.10.2026, `apps/sessions`): a `background.window` background, three columns — coordinators (290 px: a header "KOORDYNATORZY" (COORDINATORS) 11.5 px in capitals in `text.muted`, an item with a state dot, the project 14 px/600, a ticket label in sky Geist Mono and counters ● in progress teal, ● waiting peach, ○ done; selected — teal ~0.08 with a ~0.30 outline; finished ones dimmed; below "POJEDYNCZE SESJE" (SINGLE SESSIONS)), the board (the title 21 px/600, the branch in a lavender Geist Mono tag, "koordynator · Claude Opus" (coordinator · Claude Opus), a 6 px progress bar made of mint, lavender, teal and peach; five columns on `background.panel` ~0.55 with a 16 px radius: To do, In progress teal, Waiting peach, Review lavender, Done an empty sky circle; `background.card` cards, a 12 px radius, the ticket ID in sky Geist Mono (only real ones), the title in plain language up to three lines, one–two lines "what was done"/"what now" in `text.secondary`, running subagents with a dot, a role in Polish and a time, the state in words at the bottom — "✓ review OK" in mint, "gotowe, czeka na review" (done, waiting for review) in lavender, "po review — do poprawy" (after review — to fix) in peach, interrupted in rose, "w trakcie" (in progress) in `text.muted`; a waiting one in peach with a question, done ones dimmed, selected — a teal outline) and details (380 px: the ID, branch and state tags, sections with 11 px capital headers in `text.muted` — "Co to jest" (What it is) with a 17 px/600 title and a small source note, "Co zrobiono" (What was done), "Teraz / review" (Now / review) 13 px — then "Przebieg" (Course): a timeline with dots on a rail, the English assignment description as an 11 px note, a MAJOR quote on a peach background with a bar, pill buttons at the bottom). The main action "Przejdź do koordynatora" (Go to coordinator) in full teal. "Pokaż wynik" (Show result) replaces the board with the subagent's report (Markdown) and the last steps of its work.
- **The "Raporty" (Reports) view** of the Tide window (mockup approved 27.10.2026, `apps/reports`): a `background.window` background, 20/24 px of padding, three columns. Days (186 px): "RAPORTY" 11 px in capitals in `text.muted`, an item with a 13 px/600 label ("Dziś · wt 27.10" (Today · Tue 27.10), others "pon 26.10") and a 7 px peach dot at the newest unopened report, under it 11 px `text.muted` "07:00 · 5 projektów" (07:00 · 5 projects); selected — a teal background ~0.10, the `frame` radius; under the list "Trzymamy 30 dni" (We keep 30 days). The middle: the day title 21 px/600 without the current year, a 13 px `text.muted` line "Od poniedziałku 07:00 · 5 projektów w raporcie · " (Since Monday 07:00 · 5 projects in the report ·) and what is waiting, in peach ("2 rzeczy czekają na ciebie" (2 things are waiting for you)); buttons "Wygeneruj teraz" (Generate now; during it "Składam raport…" (Assembling the report…) and an endless 3 px teal bar with a description) and "Otwórz w przeglądarce ↗" (Open in browser ↗) on `background.card`. Project cards on `background.view` with a ~0.06 text border, the `bar` radius (14 px), 10 px of gap: the name 15 px/600, board tags as pills (text ~0.05, a 6 px dot in the tag's pastel), 12 px counters — "N zrobione" (N done) mint, "N w toku" (N in progress) teal, "N czeka" (N waiting) peach (zero muted), "· N uwaga" (· N attention) in `butter` (only when nonzero), a 13 px `text.secondary` headline, "Czeka na ciebie: …" (Waiting for you: …) 12 px in peach on peach ~0.08; the selected card — teal ~0.07 with a teal ~0.45 outline. At the top of the middle column "Dziś w skrócie" (Today in brief; PT-24) on `background.card` with a ~0.06 border, the `bar` radius: "DZIŚ W SKRÓCIE" 11 px in capitals in `text.muted`, 3–5 sentences 14 px `text.primary` (line height 1.3), under them — live, like the agents widget — "Czeka teraz · N" (Waiting now · N) 12 px/600 in peach with a pulsing 8 px dot and rows "project — pyta: "…"" (asks) / "— czeka na zgodę (…)" (waiting for approval) 13 px `text.secondary` with a 15 px indent (older days without this part). On a project card "Skończone dziś: <note sentence>" (Finished today) 12 px in mint (with "· i N więcej" (· and N more)), above "Czeka na ciebie"; "Czeka na ciebie" omits items the agent is no longer waiting on. Under the cards, projects with only housekeeping on one 12 px `text.muted` line "▸ Porządki: QuotaApp, BO…" (▸ Housekeeping: QuotaApp, BO…) (after a click "▾" and a list "project: items" with a 15 px indent). At the end "Poza projektami" (Outside projects) in a text frame ~0.14 (the `tile` radius), collapsed to one row with "Rozwiń" (Expand) in the accent. The "Na daily" (For the daily) panel (360 px) on `background.card`, the `card` radius: "NA DAILY" 11 px, the project name 17 px/600, "Na czym polega" (What it is about), sections with an 8 px dot (Finished today mint — only when there are notes after a ticket, with a 12 px note in peach "Sprawdź ręcznie: …" (Check manually: …) or "Wymaga sprawdzenia ręcznego" (Needs manual checking); Done mint, In progress teal, Waiting for me peach, Attention `butter` — failing tests and number conflicts, only when present, Next an empty `text.muted` disc), items 13 px `text.primary` with a 15 px indent, "dalej: …" (next: …) in teal, empty ones — "nic" (nothing); a "Czeka na mnie" item the agent is no longer waiting on is struck through in `text.muted` with an 11 px note "już nieaktualne — agent na to nie czeka" (no longer current — the agent is not waiting for it); a collapsed "Porządki" (Housekeeping); at the bottom a full teal "Kopiuj jako tekst" (Copy as text; after a click "Skopiowano ✓" (Copied ✓)) and a "Pełna strona ↗" (Full page ↗) link in the accent. A report from before `report.json`: cards without counters with a muted sentence, a panel without sections and without "Kopiuj". The empty state: "Nie ma jeszcze raportów" (There are no reports yet) 19 px/600 with a description and "Wygeneruj teraz". All report texts as plain text; preview: `make preview-reports-view`.
- **The "Docker" view** of the Tide window (the mockup "Tide Docker", approved 27.10.2026, `apps/docker`): the header "Docker" 21 px/600 with a 13 px `text.muted` summary ("9 kontenerów działa w 6 projektach i 1 bez compose · 4 zatrzymane, 1 z nich padł · CPU 3% · RAM 2,2 GB" (9 containers running in 6 projects and 1 without compose · 4 stopped, 1 of them crashed · CPU 3% · RAM 2.2 GB)), on the right a pill "testcontainers: 1 ukryty" (testcontainers: 1 hidden; on — teal ~0.16) and a 230 px search. On the left groups every 14 px: a header (a dot, the project 14 px/600, "◆ CookApp" — a diamond in teal, the repository in `text.secondary`, "3 z 3 działa" (3 of 3 running) 12 px, the project action result, 12 px links in the accent: compose logs, Restart, Uruchom/Uruchom resztę (Run/Run the rest), Zatrzymaj (Stop)), rows every 6 px on `background.view` with the `tile` radius: a dot (teal running, rose crashed, an empty circle stopped), the name 13 px/600, the image and state 12 px (rose for a failure, butter for "niezdrowy" (unhealthy)), ports (HTTP as a link ":8080 ↗" in teal on a teal ~0.10 background, others "5434→5432" on text ~0.05, Geist Mono 12 px), CPU and RAM in Geist Mono, 28 px buttons with a radius of 8 on `background.card` (Logi (Logs), Powłoka (Shell), Restart, Zatrzymaj; stopped: Logi, Uruchom (Run) in mint, Usuń (Remove) with a rose mark). Open logs — a row in sky ~0.07 with a ~0.30 outline; opening on an element — teal ~0.10 with an outline for 2.6 s. "Zatrzymane" (Stopped) at the end (crashed first, full names), collapsed above 6 — crashed ones are always visible. The log drawer under the list on a `background.desktop` background darkened ×1.18, "Logi", the name in sky Geist Mono, "Otwórz w Konsoli (docker logs -f)" (Open in Konsole), lines in Geist Mono 12 px in `text.secondary`, stderr in butter. On the right 300 px: a "Zasoby" (Resources) card (CPU teal, RAM lavender — 5 px bars, values in Geist Mono, below them the largest memory use and disk space) and "Porządki" (Cleanups; categories with an 18 px checkbox — checked: teal with a ✓ and a mint ~0.08 background with a ~0.25 outline — an 11 px description, in butter with a warning, the size in Geist Mono; "Wyczyść zaznaczone · X GB" (Clean selected · X GB) in rose; during it a sliding rose bar, then the result on a mint or rose background). Removing a container and cleanups go through a confirmation window as in Worktrees (a card with a rose outline, a list in removal order, ⚠ warnings in butter, "Anuluj" and a rose button). Docker unavailable — an empty state card like in views that are not ready, with the service message. A badge in the bar: "● 1" in rose when something crashed, otherwise the number running.
- **The "Modele" (Models) view** of the Tide window (PT-26, no mockup — in the language of the Docker and Reports views, `apps/models`): the header "Modele" 21 px/600 with a 13 px `text.muted` line ("Używany serwer: Ten komputer · działa · Ollama 0.40.11 · 5 modeli" (Server in use: This computer · running · Ollama 0.40.11 · 5 models)) and "Sprawdź serwery teraz" (Check servers now) on the right; below it a "what needs attention" frame (peach ~0.08, a ~0.30 outline, the `tile` radius; warnings from `models.json` in butter). On the left "SERWERY MODELI" (MODEL SERVERS; 11 px in capitals): cards in a grid (up to 3 columns, min. 330 px) on `background.view`, the `bar` radius, a ~0.06 text border — a chip mark 20 px on a 38 px square, the name 15 px/600 (as in the settings, a local one — "Ten komputer"), the full address in Geist Mono 12 px `text.muted` and under it "z tokenem" (with a token) 11 px, a state dot (teal running, rose not responding, an empty circle checking…), the model in memory 12 px (butter when part is on the processor), 12 px links "Sprawdź połączenie · Edytuj · Usuń" (Check connection · Edit · Remove; remove in rose). The server in use: a teal ~0.06 background, a teal ~0.45 outline and a "✓ Używany" (✓ In use) pill (teal ~0.16); the others — a 34 px "Używaj tego serwera" (Use this server) button in teal ~0.16. Editing and removal confirmation are in the card; "Dodaj serwer" (Add server) is a dashed frame (the `bar` radius) with a hint about the Mac, after a click a form up to 560 px. "MODELE NA SERWERZE" (MODELS ON THE SERVER) with a server switch (a pill like "Razem | Wiersz na projekt"): rows on `background.view` with the `tile` radius, a dot (teal in memory, butter partly on the processor, an empty circle), the name in Geist Mono 13 px, parameters 11 px, roles that use the model as lavender ~0.14 pills, the size in Geist Mono; a download with a 5 px teal bar; "Pobierz model" (Download model) — a field in Geist Mono and hints as pills on `background.card` (the model in teal, the description `text.muted`). On the right a 420 px card "DO CZEGO UŻYWAMY MODELI" (WHAT WE USE MODELS FOR) on `background.card`, the `card` radius: "Raport poranny i opisy kart" (Morning report and card descriptions) 15 px/600, a Claude / Lokalnie (Local) / Bez modelu (No model) switch (30 px) and a 12 px sentence for each mode (the selected one — `text.primary`), then roles separated by a text line ~0.07: the name 14 px/600, "na: Ten komputer · za domyślnym" (on: This computer · follows the default) 12 px, a model choice like the project choice (a list with the size and "w pamięci" (in memory); a model missing on the server — an outline and text in butter), "Zaawansowane ▸" (Advanced ▸) expands pinning to a server and context; a service without `roles.*.pinned` — roles read-only with a butter note. Under the roles (PT-60) the same card has "GDZIE IDZIE PRACA" (WHERE WORK GOES; rows: the word Lokalnie in mint, Chmura (Cloud) in teal, Nie używany (Not used) muted; the name 13 px, the detail 12 px `text.muted`, unavailable — a butter note), "WYKONAWCY" (EXECUTORS; the name 13 px/600, a program and model line, classes, Zmień (Change) and Usuń (Remove; in rose with confirmation) links, a form with provider and program switches and class buttons) and "INNI DOSTAWCY" (OTHER PROVIDERS; the address, API, "token zapisany" (token saved); a form with a password in the token field). Action results 12 px next to the control: an error in rose, success in mint, a remark in butter. Without the service — an empty state card as in Docker. Preview: `make preview-models-view`.
- **Popup:** a header with an icon and a title (15 px, 600), cards under it; a 18 px radius.
- **Card:** a `card` background or a darkened background of the state pastel (opacity ~0.08) with a border in that pastel (~0.25–0.35). A card that needs attention is in peach. (Agent sessions no longer have cards — the popup uses the compact rows from the sidebar, decision 44.)
- **Gauge:** a 4–6 px bar on a text background of opacity 0.1; the fill: mint >40%, butter 16–40%, rose ≤15%. In dock tiles the bar is 3 px; System and AI Limits paint it in the gauge or level color.
- **Window buttons:** on the right, minimize · maximize · close — 13 px pastel circles (butter, mint, rose) every 8 px; in an inactive window gray, icons only on hover.
- **Loading screen:** a dimmed wallpaper (`splash.dim` 0.35 of the desktop color; with the cat — a cat with open eyes in the monitor's frame), an entry by crossfade `splash.fadeMs` (400 ms), in the middle the full 128 px logo, 44 px lower a 220×4 px bar — the track in the text color with `opacity.outline` opacity, the fill `accent`, the `radius.small` radius capped at half the height. The logo "breathes" (opacity 1 → 0.78, cycle `splash.pulseMs`), with no text.
- **Boot screen (Plymouth):** a flat `background.desktop` background, in the middle the full logo and a bar as on the loading screen (128 px, a 220×4 px bar 44 px lower), without a wallpaper — in the initramfs every kilobyte counts. An entry by crossfade `splash.fadeMs`: the tile, then the waves every `plymouth.waveDelayMs` (300 ms); then the waves breathe one after another (opacity 1 → `plymouth.breatheTo` 0.6 → 1, cycle `splash.pulseMs`), the tile stands still. On shutdown the logo alone. The disk password: a hint from the system in `text.secondary`, a field like on the lock screen (`lockscreen.fieldWidth` × `fieldHeight`, the `card` radius, the border and focus outline in the accent), dots in `text.primary`, "Hasło" (Password) in `text.muted`, "Caps Lock jest włączony" (Caps Lock is on) in `semantic.neutral` (butter); system messages lower in `text.muted`. The scale follows the smallest screen: 0.75 / 1 / 4/3 / 2 relative to 1920×1080.
- **Lock screen:** a 96 px clock and the date at the top, the password field in the middle; the background dimmed once (`lockscreen.dim` 0.12). With the cat: it is awake only while the screen is actively used — it wakes by crossfade `lockscreen.wakeMs` (650 ms), after `lockscreen.dozeAfterMs` (3 s) without activity it falls asleep more slowly and gently (`lockscreen.sleepMs`, 1100 ms), also with the form visible; it does not fall asleep while the password is checked and during an error message; without animations instantly.
- **Desktop with the cat:** the same monitor frame and the same crossfade as on the lock screen (`lockscreen.wakeMs` / `lockscreen.sleepMs`), but the cat closes its eyes only after `wallpaper.dozeAfterS` (15 s) without keyboard and mouse, and opens them at the first movement. The threshold is much longer than on the lock screen, because the desktop is usually covered by windows and is visible in short breaks — at 3 s the cat would blink at every hesitation. Without tide-daemon the cat stays awake; without animations the change is instant.
- **Login screen (SDDM):** a clock and date at the top center, an avatar in a circle, the name, the password field with a teal button, "Zmień użytkownika" (Change user); account selection as cards (an avatar, the name, the account name, selection with a teal outline) and an "Inny użytkownik" (Other user) card. The background is dimmed once (`opacity.loginDim` 0.12) and stays still when the view changes.
- **Terminal (Konsole, #26):** tabs as tile pills (Geist, a `card` background, the `tile` radius, a border in the text color with `opacity.outline` opacity, the active one with a teal outline on a teal ~0.11 background); an agent tab "● project · branch" with a state bar under the title (teal working, peach "czeka" (waiting), "○" finished without color). The terminal area with `opacity.panel` opacity and blur, Geist Mono `terminal.fontPt`, the margin `terminal.marginPx`, without a scroll bar and menu, in the bottom right corner three waves with `terminal.watermark.opacity` opacity. The prompt: chips with a pastel background shifted 84% toward the window background and the text in the pastel (the project on `card`, the branch lavender, the worktree sky, changes butter, the agent in the state color), the time on the right, "›" teal (rose after an error). The Claude Code status bar in the same language, limits in level colors as in the AI Limits widget.
- **The "three waves" logo:** waves teal, lavender, peach. The full version on a `card` tile (radius 20/64), a version without a tile with waves thicker from 18 px, a symbolic single-color version.

## Tide window interface standard (K-128)

A shared set of components of the Tide window (`widgets/lib`, imported in views as `Tide`). The look pattern is the S-9 mockup
in `docs/agents/k-128/makieta/index.html` (description and how to open it: `docs/agents/k-128/makieta/README.md`);
colors, radii and fonts come from the palette. Subsequent K-128 card tickets add their components here.

### Text

Every longer text (a card description, "Uwaga" (Note) and "Powód kroku" (Step reason) in a decision, agent and service comments) is shown by
**`Tide.Prose`**: a read-only `TextEdit` with RichText, with mouse selection. Copying (Ctrl+C) gives plain text
without markup. A paragraph is at most about 80 characters wide (`measure`), with line height 1.6 and 8 px of spacing below.
`collapsedLines` collapses longer text to the given number of lines with "więcej"/"mniej" (more/less). HTML is computed only when the text,
the mode, the renderer or the background changes (`renders`), so long lists do not render it at every rebuild.

HTML is made by **`prose.js`** (`render(text, {markdown, colors})`, colors from `colors(Tokens, background)`):

- **Blocks:** paragraphs (a blank line separates, a single line end stays a break), lists `-`/`*`/`1.`, quotes `>`
  (lavender ~0.06 with an indent), code in three backticks (text ~0.04, Geist Mono). A sentence that after a colon has
  at least three items separated by `; ` becomes a list; two items stay a paragraph.
- **Inline emphasis:** `` `command` `` — butter on butter ~0.09 (`code.cmd`); a path in backticks or without them
  (`apps/board/qml/CardPanel.qml`, `~/.docker/config.json`, `docs/x/`, `plik.qml`) — sky on sky ~0.10 (`code.p`);
  an address and a domain (`https://…`, `cdn.jsdelivr.net`) — lavender (`code.url`); keys (`Ctrl+Enter`, `Esc`, `F5`) —
  `kbd` on text ~0.07, each key separately; a card number `K-128` — sky, Geist Mono, 600 (`.cref`); a ticket number
  `PT-501`, `RECI-38` — sky, Geist Mono (`.tref`). The emphasis backgrounds are blended with the text background; each pair has a contrast
  of at least 4.5:1 on the `view`, `window`, `card`, `titlebar` and `panel` backgrounds (the test `widgets/tests/test_prose.py`).
- **Markdown mode** (`markdown: true`): additionally headings `#`–`######` (without literal hash marks), **bold**,
  *italics*, the `---` line and links `[text](https://…)` as text with the address in lavender next to it. When the host has its own Markdown
  renderer (`renderer`, e.g. `backend.renderMarkdown` on the Board), `Tide.Prose` uses it; without it
  (Sessions) — `prose.js` in Markdown mode.

**An agent's text never injects HTML or links.** `prose.js` first escapes the whole text (`&`, `<`, `>`, `"`, `'`),
and only then adds markup with fixed attributes (`class`, `style` from the palette); matched text never
reaches an attribute. The result has no `href` or `<img>` — including a Markdown link (`[x](javascript:…)` stays as plain text,
`[x](http://…)` — as text with the address), so nothing is clickable and the view makes no network requests. The scheme
`tide-card:` is reserved for a card preview by number (`Tide.CardRef`, stage A2) and is not produced today.
RichText occurs only in `Tide.Prose`, in `Tide.CodeBlock` (script lines from `shell.js`, each piece escaped —
below) and in the Board's `MarkdownBlock.qml` (HTML from `backend.renderMarkdown`); the only
exception from before the standard — the report in the Sprawy (Issues) window (`IssuesView.qml`, `backend.renderReport` with the issue's images) —
moves to `Tide.Prose` in the review of the remaining texts (stage A2). A static test guards this.

The first consumer: the "dlaczego" (why) field of a card decision (`DecisionForm.qml`, the lines "Uwaga:" and "Powód kroku:") in the decision
window and in "Twój ruch" (Your move) of the full card.

### Approval requests and decisions (K-128, PT-506)

The content of a decision (`apps/common/qml/card/DecisionForm.qml`, shared by the decision window and "Twój ruch" of the full card) according to the
S-9 mockup, the "Approval request" area, variant A. The change concerns **only formatting and layout** (the K-109 boundary): the same
methods, parameters, approval groups and answers; the window preselects nothing (D10).

- **The title as a question.** An approval request has in its title a question composed from existing fields (`decision.js`,
  `questionTitle`): "Uruchomić `<command>` poza piaskownicą?" (Run `<command>` outside the sandbox?), "Zezwolić na `<command>`?" (Allow `<command>`?), "Zezwolić na polecenia
  z łańcucha?" (Allow the commands from the chain?) (a long command — "…polecenie…" (…command…)). The service sentence (`happened`) stands under it in `text.secondary`.
  Other decisions, an approval group and a domain refusal have today's title (`happened`).
- **A single recommendation.** The large "Opiekun poleca" (The steward recommends) block disappears. The pure function `advice(decision)` in `decision.js` reads only
  `options[].recommended` and `caretaker{optionId, recommendation, summary, failed, tried}` and gives a mode:
  - the **"polecane"** (recommended) badge (teal on teal ~0.10, an outline ~0.35, the mockup `.chip.teal`) only on an option
    with `recommended: true`; the recommended option has a teal outline ~0.35;
  - `caretaker.optionId` = the recommended option (`agree`) — under its description "✓ Polecane — opiekun karty też to poleca" (✓ Recommended — the card steward recommends it too)
    (teal) and a "uzasadnienie opiekuna" (steward's justification) button that expands a comment (the steward's recommendation and summary);
  - `caretaker.optionId` = another option (`differ`) — at that option the **"opiekun"** (steward) badge (lavender, `.chip.lav`)
    and "◆ Opiekun poleca tę opcję" (◆ The steward recommends this option; butter) with "dlaczego?" (why?). **A temporary branch** (`TODO(K-128 etap B)` in the code,
    below "What needs stage B");
  - an empty or unknown `caretaker.optionId`, while there is a `recommendation`, `summary` or `failed` (`note`) — one sentence
    under the options ("Opiekun poleca: …" (The steward recommends: …) in teal or "Opiekun nie poradził sobie z problemem i oddaje decyzję
    Tobie." (The steward could not handle the problem and hands the decision to you.) in peach) and "Szczegóły opiekuna" (Steward details) expanding the summary and the list of attempts `tried` ("Co próbowano" (What was tried)).

  The comment (`CaretakerDetail.qml`, the mockup `.comment`) has a `background.view` background and an `opacity.border` outline; the steward's summary
  is shown by `Tide.Prose`. Expanding selects nothing.
- **The command** from `technical.command` stands in a one-line `Tide.CodeBlock` with "Kopiuj" (Copy; like the former field), likewise
  commands to copy from the question text (`copyCommands`).
- **Script preview:** when a decision has the field `decision.script = {path, language, text, lineCount, truncated}`, under the
  command stands a collapsed `Tide.CodeBlock` with the content; without the field — no block.
- **Buttons** are `Tide.Button`: in the decision window "Otwórz całą kartę" (Open the whole card; `ghost`), "Później" (Later; `ghost`, `Esc`)
  and the single main **"Wybierz ↵"** (Choose ↵; `primary`; in "Twój ruch" — the same single main one in the card view); toggles
  ("Szczegóły techniczne" (Technical details), "Tickety i kroki" (Tickets and steps), "Co próbowano") — `ghost` `small`; previous/next and ✕ — `ghost`
  icons. Next to "Wybierz" a key hint "↑↓ wybór · Enter zatwierdza · Esc później" (↑↓ choose · Enter confirms · Esc later) (`text.muted`); ↑↓ select
  the neighboring option (`DecisionForm.moveChoice`) — without an arrow or a click nothing is selected.
- The contrast of the new colors (the "polecane" and "opiekun" badges, notes, the toned button, the steward sentence) on their backgrounds
  laid over `background.window` and `background.card` is at least 4.5:1 (test
  `apps/board/tests/test_decision_advice.py`).

#### What needs stage B

Two parts of the view are ready, but **will not appear in production after stage A** — they work only on test data,
because the service does not supply the needed data today ([stage B sketch](agents/plans/followups/etap-b-silnik.md)):

- **Script content preview** — the service does not supply the `decision.script` field, so there is no script block in the approval request.
- **The "opiekun" divergence marker** — `caretaker.annotate_escalation` (`daemon/tide_daemon/cardflow/caretaker.py`)
  overwrites `recommended` with the steward's choice, so today `caretaker.optionId` always points to the recommended option (mode
  `agree`) and divergence cannot be reliably detected. The `differ` branch has `TODO(K-128 etap B)` in the code, and its tests
  are marked as a temporary contract; stage B may change the meaning of "polecane" and this branch, or remove it.

The risk pills from the mockup (`decision.risks`) will appear together with the data in stage B (or A2).

### Code block (`Tide.CodeBlock`)

`widgets/lib/CodeBlock.qml` (the S-9 mockup `.code`) — the Tide window's code block in two variants:

- **Collapsible** (default): a header on a text background ~0.04 above `background.view` — the path (`path`, sky, Geist Mono,
  shortened in the middle), "· 34 linie · bash" (· 34 lines · bash; `text.muted`; the number from `lineCount` or from the text), "pokaż treść skryptu" (show the script) /
  "zwiń" (collapse) and "Kopiuj" (`Tide.Button` `ghost` `small`). **Collapsed by default** (`expanded: false`); a click on the header, Space
  or Enter expands. After expanding, lines with numbers (`text.muted`, right-aligned) and shell coloring in a frame
  of at most `maxBodyHeight` (420 px) with its own scrolling (`Tide.ScrollBar`); `truncated` appends "Treść skrócona —
  pokazano początek pliku." (Content truncated — the beginning of the file is shown.) Lines are counted only after expanding.
- **One-line** (`oneLine: true`): a command as plain text (Geist Mono, selectable) in a `background.view` field
  with "Kopiuj" — the decision's command field.

The coloring is done by `widgets/lib/shell.js` — a simple line tokenizer (`tokens(line)` → `{kind, text}`): a comment
(`#` at the start of a word — `text.muted`, italics), a keyword in the command position (`if`, `then`, `for`, `export`,
`echo`, `set`… — lavender), a string `'…'` and `"…"` (butter; variables inside `"…"` separately), a variable `$X`, `${X}`, `$1`, `$@` (sky),
a command — the first word at the start of a line and after `;`, `|`, `&`, `(`, `$(` (teal); the rest `text.primary`. Colors
from `shell.colors(Tokens)`; each pair has a contrast of at least 4.5:1 on the block background (test `widgets/tests/test_codeblock.py`).

**The content is untrusted text.** `shell.html(line, colors)` escapes every piece (`&`, `<`, `>`, `"`, `'`) and only
then wraps it in a `<span>` with a fixed style from the palette — text never reaches an attribute, no links and images. "Kopiuj"
copies the raw text. A test checks that `<` and `&` from a script are visible as text.

### Scroll bars

`Tide.ScrollBar` (`widgets/lib/ScrollBar.qml`) is the only scroll bar in the Tide window (the S-9 mockup, variant A).

- **Look:** a track in the text color with `scrollbar.track` opacity (0.04), a handle in the text color with `scrollbar.thumb` opacity
  (0.30), width `scrollbar.width` (10 px), rounded to half the width. On hover and while dragging
  the handle takes the accent with `scrollbar.thumbActive` opacity (0.75). The values are in `theme/palette/mist.json`, and the generator
  `widgets/stage.py` exposes them as `Tokens.scrollbarTrack`, `scrollbarThumb`, `scrollbarThumbActive` and
  `scrollbarWidth` (a separate block at the end of `Tokens.qml`).
- **Visibility:** the bar is visible right away when there is something to scroll (`size < 1`), without prior scrolling and
  hovering, and hidden when the content fits in the view. We use neither the `AsNeeded` nor the `AlwaysOff` policy: visibility is
  decided by the component itself, so the policy does not depend on the content height (no binding loop).
- **Usage:** vertical and horizontal like the built-in bar, on `Flickable`, `ListView`, `GridView` and `ScrollView`:

  ```qml
  Flickable {
      QQC2.ScrollBar.vertical: Tide.ScrollBar {}
      QQC2.ScrollBar.horizontal: Tide.ScrollBar {}
  }
  ```

  We do not add a bare `QQC2.ScrollBar {}`, `ScrollIndicator {}` or a `ScrollView` without `Tide.ScrollBar` in `apps/`; this is guarded by
  the test `apps/tide/tests/test_scrollbar_static.py`.
- **Placement:** the bar lies on the edge of the scrolled view and overlaps the content. When it should not cover it, reduce
  the content width by `Tide.Tokens.scrollbarWidth`.
- **Exception (K-104):** the changes view (`apps/common/qml/git/DiffView.qml`, `SyntaxText.qml`, `ModuleHighlighter.qml`)
  stays unchanged; aligning its bar with `Tide.ScrollBar` belongs to stage A2 of the K-128 card.

### Buttons

`Tide.Button` (`widgets/lib/Button.qml`, the S-9 mockup `.btn`) is the Tide window button: a Qt Quick Templates `AbstractButton`
with the Button role, keyboard focus (a 2 px teal outline outside the border), Space, Enter and Return; when disabled it has
opacity 0.45 and does not react to the mouse, keyboard or a screen reader action. The `radius.small` radius, height 32 px,
`small` — 26 px, text 13 px/500 (small 12 px).

| Variant (`variant`) | Background | Text | Outline | Use |
| --- | --- | --- | --- | --- |
| `primary` | accent (on hover 7% lighter) | `background.window`, 600 | none | the main action of a view — **at most one** per view |
| `normal` (default) | text ~0.06 (on hover ~0.11) | `text.primary` | text with `opacity.outline` opacity | ordinary actions |
| `ghost` | none (on hover text ~0.06) | `text.secondary` (on hover `text.primary`) | none | supporting actions, menu items |
| `danger` | rose ~0.14 | rose | rose ~0.35 | destructive actions (archiving, deletion) |

- `iconOnly` — an icon button (the glyph alone, e.g. "⋯", narrower margins), `kbd` — a shortcut next to the text ("Wybierz ↵",
  "Nie zmieniaj Esc" (Do not change Esc), Geist Mono, opacity 0.7), `toolTip` — a tooltip (`PlainToolTip`) and a description for the screen reader,
  `tone` — a button toned with a palette color (background ~0.12, outline ~0.45, text in that color; the back button in the top bar).
- The text of each variant has a contrast of at least 4.5:1 against its background laid over `background.window`
  and `background.card` (test `widgets/tests/test_button.py`).
- `TextButton` and `IconButton` stay for Plasma widgets.
- **One set of buttons (K-128, PT-507).** Every button of the Tide window and the "Nowe zadanie" window is a `Tide.Button`; local
  copies (`PillButton.qml` in `apps/board`, `docker`, `models`, `sessions`, `reports`, `worktrees`, `newtask` and
  `apps/issues/qml/Action.qml`) no longer exist. The exception: `PillAction` in `apps/common/qml/git/DiffView.qml` (the changes view, K-104).
  The variant follows from the role: **at most one `primary` per view or dialog** (the action the user will almost certainly
  choose: "Zleć…" (Assign…), "Zapisz projekt" (Save project), "Uruchom agenta" (Run agent)), `danger` for destructive actions (removing a container, server,
  worktrees, rejecting an issue, detaching evidence, cleaning Docker — with today's confirmation), `ghost` for
  supporting ones ("Anuluj" in narrow forms, "Zamknij" (Close), "Odśwież" (Refresh)), `normal` for the rest; a button without a variant
  is `normal`. A selected state (a filter, an executor class, a view mode) is a `tone` in the accent, not `primary`. Size: `small`
  (26 px) where the former button was ≤ 30 px; a glyph in a square (`RD`, `›_`, ✕) is `iconOnly`. The object name, text,
  tooltip, `enabled` and the call stay as they were. The static test: `apps/tide/tests/test_buttons_static.py`.

### The drawer and the card panel

`Tide.Sheet` (`widgets/lib/Sheet.qml`, the S-9 mockup "A card from the Board", variant A) is the frame of a drawer on the right. The host
stretches it over the view (`anchors.fill: parent`). The frame draws, from the bottom: a dimming (`background.desktop` ~0.55; a click
outside the drawer closes it), the drawer on `background.panel` with an `opacity.outline` line on the left, in it a header with ✕
(`Tide.Button` `ghost`, 56 px), an optional step bar (`steps`, `currentStep`), scrollable content with `Tide.ScrollBar`
and a fixed footer. The width is `widthFraction` (0.64 by default) of **the Tide window width**, not the view (a 1380 px window
with a sidebar gives the view 1180 px, and the drawer is 883 px), not less than `minSheetWidth` (640 px) and not more than
the view.

- Children of a Sheet go into the scrollable column (`scrolls: false` gives it the whole height — for content with its own
  scrolling, e.g. a document), `header` and `footer` accept header and footer elements (`showFooter` hides the footer).
- Esc closes the drawer (`closeRequested()`) unless the content takes it over: `contentTakesEscape: true` — the Sheet does not close
  and does not accept the event, so the host gets it.
- In stage A the only consumer is the card panel; merging and the other dialogs move to this frame in stage A2.
  Test: `widgets/tests/test_sheet.py`.

**The card panel on the Board** (`apps/board/qml/CardPanel.qml`, test `apps/board/tests/test_card_panel.py`) stands in this
frame and has two modes (`mode`), switched by a "Czytaj | Edytuj" (Read | Edit) `Segmented` in the header. The header: the number (sky, Geist Mono),
the column, the project with a dot, the switch, "Zleć agentowi…" (Assign to an agent…; `Tide.Button`), ⋯ ("Rozwiń" (Expand), "Archiwizuj" (Archive) in rose) and ✕.

- **Read** (the default after clicking a card): the title 21 px, tags, the creation date and a link to the issue, the YouTrack state frame,
  tabs Opis | Plan | Specyfikacja (Description | Plan | Specification; as before; the selected one stays for subsequent cards), the description through `Tide.Prose`
  with the Board renderer (`renderMarkdown`), the card's course in `HistoryBlock` (a fixed 340 px frame — the panel does not grow
  with the number of events), and for a card with a coordinator also steps, review decisions, a pause and a plan proposal.
- **Edit** (the default for "＋ Karta"): the title, project, tags, description, note, column and "Archiwizuj". The footer:
  "Ctrl+Enter zapisuje · Esc wraca do czytania bez zapisu" (Ctrl+Enter saves · Esc returns to reading without saving), "Anuluj" (quiet) and "Zapisz" (Save; main). Saving an existing
  card returns to reading, and a new card closes the panel. Entering edit fills the form with the current card; Esc, "Anuluj"
  and returning to Read discard changes without asking.
- **Esc:** in editing an existing card returns to reading (without calling the service), in reading closes the panel; the description drawer
  from the full card (`descriptionOnly`) and a new card close the panel.
- **YouTrack:** **the "Zgłoszenie w YouTracku" (YouTrack issue) field is no longer in the form**, and the form does not send the `youtrack` key
  (an empty `youtrack` clears the key in the service). The state frame (`cardYouTrackState`, `cardYouTrackText`, "Otwórz w YouTracku ↗"
  `cardYouTrackOpen`) and, on a conflict, "Weź z YouTracka" (`cardYouTrackTake`) and "Nadpisz z Tide"
  (`cardYouTrackOverwrite`) are **in Read mode** — today the only place where a sync conflict is resolved
  (`youtrackResolveRequested(cardId, "youtrack" | "tide")`). The key is set by the card view; the marker on the tile, the YOUTRACK section
  in Projects and tags and the import stay.
- A click on the dimming closes the panel (it does not open the card underneath).

### Top bar

`Tide.TopBar` (`widgets/lib/TopBar.qml`, the S-9 mockup "Frame and bar", variant B) is the fixed bar of a view. It stands **outside** the
scrolled content: the host anchors the `Flickable` under it, so scrolling does not move it. A `background.window` background,
under it a text line with `opacity.border` opacity. Height 52 px, with tabs 52 + 38 px.

- **From the left:** back (`backText` — the name of the target, e.g. "Tablica"; empty = no back) always first: a `Tide.Button`
  toned with the accent, 36 px, the text "← Tablica", the tooltip "Wróć (Esc)" (Back (Esc)), the signal `back()`; breadcrumbs (`crumbs`, `text.muted`,
  "›" with opacity 0.6); the number (`number`, sky, Geist Mono, 600 — never shortened) and the title (`title`, `text.primary`, 600,
  shortened with an ellipsis).
- **On the right:** at most one main action (`actionText`, `actionVariant` — `normal` by default, the signal
  `actionTriggered()`) and a ⋯ menu (`menuItems: [{id, label, danger, enabled, hint, name}]`, the signal `menuTriggered(id)`,
  `menuOpen`). The menu list is 300 px on `background.panel`, the `card` radius, an `opacity.outline` outline; items are
  `Tide.Button` `ghost` (destructive ones — `danger`), left-aligned; a disabled item shows `hint`. The menu is closed by a click on
  an item, a click outside and Esc.
- **Tabs** (`tabs: [{id, label, enabled, hint}]`, `currentTab`, the signal `tabTriggered(id)`): text `text.muted`,
  the current one `text.primary`/600 with a 2 px accent underline; disabled — opacity 0.45 and a tooltip. The bar only reports
  the choice: the current tab is set by the host, and a tab may open another view instead of content.
- **Keys:** Esc with an open menu closes the menu; otherwise it passes to the host, which goes back when there is no
  open dialog.

### The card view

The full card (`apps/common/qml/card/CardFullView.qml`, shared by the Board and Sessions) has a `Tide.TopBar` with a back to the
host ("← Tablica" from the Board, "← Sesje" from Sessions — `backText` is set by `BoardPage` and `SessionsPage`), the project
in the breadcrumbs, and the card's number and title. The bar's main action is "Porozmawiaj z opiekunem" (Talk to the steward) when the steward has a card problem.
The ⋯ menu: "Zmiany gałęzi" (Branch changes; when the card has a branch), "Edytuj opis" (Edit description), "Raport i dowody" (Report and evidence; a card from an issue) and all
card settings from the same list as the "Ustawienia karty" (Card settings) section — with the same confirmations (completion,
archiving, branch change) and with the field that gets focus. Esc closes in turn: the confirmation, the menu, the card.

Tabs: **Przegląd** (Overview; the whole card page: title, branch, phases, start, "Twój ruch", the plan with "Teraz w tle" (Now in the background), remarks,
history, settings), **Plan** (the plan table alone across the full view height) and **Historia** (History; the history block alone across the full
height), **Opis** (Description; the card description in `Tide.Prose` with `markdown: true`; the Board passes
`markdownRenderer` = `backend.renderMarkdown`, Sessions have no renderer, so the `prose.js` Markdown mode works) and
**Zmiany** (Changes; opens the same branch changes view as "Zmiany gałęzi", `DiffView`; without a card branch it is disabled
with a tooltip). There is at most one `primary` in the view: "Wybierz ↵" in "Twój ruch", and with an open confirmation
question — the question's button (archiving is `danger`).

#### The plan as a table (`PlanTable.qml`)

The S-9 mockup "Card plan", variant C. One ticket per row; columns: **Fala** (Wave), **Ticket** (sky, mono, a fixed column with
shortening — the number does not run into neighboring elements), **Tytuł** (Title; full, wrapped; under it remarks from the plan row
`flow.plan.rows[].note` and the question of a waiting ticket from `flow.decisions[]` in `text.muted`), **Stan** (State; a badge in the state
color, the label from the service), **Wykonawca → recenzent** (Executor → reviewer) and **⋯** (a `Tide.Button` icon with ticket settings, only when
`ticketSettingsEnabled`, i.e. on the Board; opens `TicketSettingsDialog`). A ticket from outside the card is dimmed.
The data is from the same fields as the former wave tiles (`derived.tickets[]`, `flow.plan.waves`).

- **The current wave pinned.** The pure function `currentWave` (`ticket.js`) picks the lowest wave with a ticket in a state
  other than done/rejected. Its rows lie in a block under the table header, with a "teraz" (now) badge and a peach background ~0.07,
  are not repeated below, and the block is at most 40% of the table height (a longer one scrolls by itself; when the rest of the table would
  not fill the space anyway, it gets all of it). When all waves are finished, nothing is pinned, and the table starts
  from the first wave.
- **Size.** Minimum width 720 px; a narrower view scrolls the table horizontally (`Tide.ScrollBar`). In the Overview
  the table is at most 420 px with its own vertical scrolling, in the Plan tab it takes the whole height.
- Wave tiles disappeared from the plan; `TicketTile` stays for an "Od razu" (Immediate) card (one ticket without waves).

#### History in a fixed block (`HistoryBlock.qml`)

The S-9 mockup "Card history", variant A. A `ListView` in a frame of a fixed 340 px height (in the History tab — the whole
height) with `Tide.ScrollBar`: the card page does not grow with the number of events. The newest are at the top, the event counter stands
in the section header, and "Pokaż starsze" (Show older) expands earlier assignments inside the block (dimmed, without "Wznów rozmowę" (Resume conversation)).
A row: the time (`history.js`), the kind, a sentence in `Tide.Prose`, a "Ty" (You) or "Opiekun" (Steward) marker and — only where the service
gives `canResume` — "Wznów rozmowę". The same component will show the history in the card panel on the Board.

### A card on the Board

The card tile (`apps/board/qml/BoardCard.qml`, the S-9 mockup `.bcard`, the "A card from the Board" area). A `background.card` background,
the `radius.tile` radius, 10/11 px of padding, a text border with `opacity.border` opacity (on hover `opacity.outline` and a background
6% lighter), 6 px between lines. From the top:

1. **The meta row:** the number in Geist Mono 11 px/600 in sky, the project 11 px `text.muted` (shortened with an ellipsis), pills
   of additional projects ("+ mobileapp", text ~0.06) and the YouTrack marker "YT LOG-131" with a sync badge:
   "✓" mint, "⟳" sky (in progress) or `text.muted` (YouTrack unavailable), "⚠" butter (refusal) or rose (a conflict,
   then the marker background is rose ~0.14); the tooltip says the state in words. The row wraps to a second line in a narrow column
   instead of squeezing the project.
2. **The title** 13 px/600, up to 4 lines. **Tags** as 11 px pills with a 6 px dot in the tag color.
3. **The wave marker** (`flow.waveLabel`, 11 px) and **the ticket bar**: a segment per ticket by `ticketPhase` — done mint,
   review lavender, working sky (pulses from the page's shared phase, without a timer per tile), queue sky ~0.18 with an outline,
   you (waiting for you) peach, todo and rejected `text.muted` ~0.3; a gap between waves; the tooltip
   "ID · phase: why".
4. **Coordinator progress:** a 4 px bar (teal, peach for a waiting one) and "● Claude · koordynator · 3 z 10".
5. **The note** 12 px in the `noteTint` color: "✓ …" (including "✓ specyfikacja gotowa" (✓ specification ready)) mint, waiting peach,
   queued muted, the rest `text.secondary`; **the branch** in To check in lavender (Geist Mono 11 px, a lavender ~0.10
   background).
6. **Actions** in `Tide.Button` 26 px (`small`), names for the screen reader unchanged: "Uruchom agenta…" and "Uruchom
   ponownie…" (both toned with the accent, never `primary` — the action stands on many tiles at once), "Przejdź do agenta" (toned with peach, in Waiting for me),
   "Zmiany gałęzi" (`normal`) and "Gotowe" (toned with mint, the tooltip and name "Przenieś kartę do Gotowych" (Move the card to Done)) in To
   check — side by side, and when there is not enough room one under the other. The links "Przejdź do agenta" and "Otwórz w Sesjach →"
   12 px in the accent wrap the same way.

States: **waiting** — a peach background ~0.08 and a border ~0.32; **selected** and focused — an accent outline ~0.6
with a 3 px glow; **hovered** — as above; **dragged** — opacity 0.35; the drag **ghost** has no buttons
and links. **Done** — a compact variant: "K-10 · project" and a 12 px `text.secondary` title up to 3 lines, without a border.

Nothing that the tile showed disappears when the look changes: the inventory test
(`apps/board/tests/test_card_inventory.py`, the pattern `tests/data/card_inventory.json`) records for every card
from the `boardfix` data extended with cards from the whole list (the object name, text, meaning color, tooltip,
`Accessible.name`, `Accessible.description` and role — including empty ones) and compares with the pattern from before the refresh. The same test
checks the text contrast on the tile (at least 4.5:1, also on toned buttons and the waiting card background).

## Icons

Application icons are round (base: McMojave-circle-dark). The "Tide" overlay inherits from the base and adds only differences: aliases (JetBrains Toolbox, the wrong `code`), tiles from original icons (round ones stay, full graphics cut into a circle, transparent shapes on a background in their own color), folders in teal, glyphs in pastels for applications without an icon. `tide-icons audit` shows coverage, `tide-icons sync` fills in the overlay.

Rules for new icons: a circle, no text inside, colors from the palette or the application's own brand colors.

**Dock applications (#8)** have muted glyphs instead of colorful icons: a rounded square (radius 26% of the side) in the color `pastel × 0.16 + 24` per channel and the application mark in the pastel itself, taking up 46% of the side, centered. Full square marks (JetBrains) are smaller (40%) so they do not outweigh stroke-based marks. Colors: Konsole teal, Dolphin and WebStorm sky, Firefox peach, Rider and Discord lavender, PyCharm and ChatGPT mint, Spectacle rose, T3 Code butter, Settings and Zed `text.secondary`, Steam, Docker and VS Code sky.

**Other programs (decision 40)** have glyphs in the same style. The color is the pastel closest to the brand color (Telegram, Writer, Chromium sky; Brave, VLC, Blender, Toolbox peach; Android Studio, Calc, NVIDIA mint; FileZilla, Redis, dotMemory rose; DBeaver, GIMP, Gnote, Ark butter; mpv, Kvantum, KeepSecret lavender; Tide Worktrees, Tide Sesje, Kate, KDE Connect teal), and system tools without their own color (Menu Editor, Info Center and Help, LibreOffice Math, OBS) get `text.secondary` like Settings. Hand-drawn marks (`theme/icons/glyphs/`) are drawn with a 2 px stroke on a 24×24 grid with rounded ends, without fills and text, like Breeze symbolic marks. The list and sources of marks: `theme/icons/glyphs.json`, a preview of all glyphs next to the current icons: `make icons-preview`.

## Brief: wallpaper

A task for a designer or a UI agent: design a wallpaper for the Mist variant (or its alternatives to choose from).

### Monitors

| Connector | Resolution | Position | Role |
| --- | --- | --- | --- |
| `DP-2` | 2560×1440 | x=1920, y=0 (right) | Primary: a 34 px top bar, a 64 px dock at the bottom |
| `HDMI-A-1` | 1920×1080 | x=0, y=0 (left, aligned to the top) | Secondary: a slim 30 px bar at the top |

The shared canvas is 4480×1440. The left screen occupies rows 0–1079, so the bottom 360 px of the canvas is never displayed. Scale 1, no rotation.

### Requirements

1. **Continuity at the seam between screens** (column x=1920, rows 0–1079) — the image should look like one stretched across both monitors.
2. **Dark and calm.** The base `#181b1e`, low average brightness; semi-transparent bars and popups lie on the wallpaper, and the text on them must stay legible.
3. **Quiet zones:** the top ~50 px of both screens (bars) and the bottom ~90 px of DP-2 (the dock) — no bright or high-contrast elements.
4. **Only palette colors** (plus their darker/lighter shades): backgrounds, teal, lavender, peach; optionally mint and the blue `sky` as a minor accent.
5. **No text, logos or recognizable motifs of other brands.**
6. **No banding on gradients** — grain or dithering, PNG 8 bits per channel.
7. The same artwork (the DP-2 version, dimmed) also goes to the lock and login screens, so the center of the frame will be covered by the clock and the password field — no strong details there either.

### Current version

Mist uses the **`cat`** variant: an illustration of a cat (cream-beige fur, a chocolate mask, ears and paws, blue eyes) lying on waves in graphite, teal and lavender colors, with a closed-eyes version for the lock screen and the idle desktop (the `org.tide.cat` wallpaper plugin). The cat's coloring is a deliberate exception to the "palette colors only" rule. Files, origin and checksums: `theme/wallpaper/assets/cat/README.md`. The cat lies on DP-2 (about 72% of the screen width), the center of DP-2 stays calm for the clock and the password field.

The **`blobs`** alternative (`wallpaper.variant` in the palette): the generator `theme/wallpaper/generate.py` draws a canvas of 4 soft blobs (lavender at the seam between screens at the top, teal in the top left corner of the left screen and in the bottom right corner of DP-2, a faint peach in the middle of DP-2) on a `#181b1e` background, with grain of 1.1 levels and a fixed random seed. The blob parameters (color, position as a fraction of the canvas, size, stretch, angle, opacity) are in the `wallpaper` section of the palette. A preview without changing the desktop: `make wallpaper-preview` (the result in `build/wallpaper`).

### How to deliver

- **The parametric variant (preferred):** change the `wallpaper` section in `theme/palette/mist.json` or extend the generator with new shapes — the wallpaper then adapts by itself if the monitor layout changes.
- **The ready-made images variant:** two PNGs named after the connectors (`DP-2.png` 2560×1440, `HDMI-A-1.png` 1920×1080), cut from a single 4480×1440 canvas according to the table above.
- Installing on the desktop: `make install-wallpaper FORCE=1` (with a backup), undoing: `make uninstall-wallpaper` or `make restore`.

## Where things are

| What | Where |
| --- | --- |
| The palette (source of truth) | `theme/palette/mist.json` |
| The KDE color scheme | `theme/colors/generate.py` |
| The Plasma style (frames of panels, popups) | `theme/plasma/` |
| Window decoration (Aurorae) | `theme/decoration/` |
| Logo | `theme/logo/generate.py` |
| Boot screen (Plymouth) | `theme/plymouth/` (assembly, script logic, stand-in), `scripts/plymouth_system.py` (installation) |
| Wallpaper | `theme/wallpaper/generate.py`, `theme/wallpaper/variant.py` |
| The wallpaper plugin with a dozing cat | `theme/wallpaper/plugin.py`, `theme/wallpaper/org.tide.cat/`, the shared `theme/wallpaper/qml/CatBackdrop.qml` |
| Icons | `theme/icons/`, `tools/make_tile.py` |
| Dock application glyphs | `theme/icons/glyphs.json`, `theme/icons/glyphs.py`, `theme/icons/glyphs/` |
| Terminal: Konsole, prompt, Claude Code status bar, tool themes | `theme/konsole/generate.py`, `theme/terminal/` |
| Widgets and the shared QML library | `widgets/`, `widgets/lib/` |
| The Tide window: view bar, view registry, views | `apps/tide/` (`views.json`, `qml/Rail.qml`), `apps/board/`, `apps/sessions/`, `apps/worktrees/`, `apps/docker/`, `apps/reports/`, `apps/models/` |
| Decisions and their rationale | `docs/decisions.md` |
| Mockups (canvas) | https://claude.ai/artifact/1h17Au71mmdEX5sNBSaAgC |
