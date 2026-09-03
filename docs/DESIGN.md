# devpit — design

Status: **planning.** Nothing built yet. Written 2026-09-03.

---

## 1. What it is

A developer cockpit. Four jobs:

1. **Repos** — every repo on the machine: branch, dirty state, **time spent**,
   **disk used**, and **notes**.
2. **Sessions** — switch to a repo and get its windows back as you left them.
3. **Monitor** — the stuff a dev machine accumulates: containers, images,
   volumes, virtualenvs. With paths, sizes, and the ability to kill or restart.
4. **Notes** — per-repo notes readable and writable from the terminal, so an AI
   agent working in a repo can read them too.

**Standalone.** It came out of the Ashura rice but does not depend on it. The rice
summons it onto a special workspace; anyone else can run it as a normal app.

### Architecture

```
devpit          CLI. Emits JSON, manages notes, performs every action.
devpit-gui      Panel. Renders the CLI's JSON. Never shells out itself.
```

Every side effect lives in the CLI. That keeps actions auditable and testable
without a GUI, makes the whole thing usable over SSH, and is what lets an AI agent
drive it. The GUI is a view.

---

## 2. Repos

### Discovery
Configurable roots (default `~/Coding/Projects/*`), plus explicitly added paths.
A directory qualifies if it contains `.git`.

### Per repo
| Field | Source | Cost |
|---|---|---|
| branch, dirty, staged, untracked, ahead/behind | `git status --porcelain=v2 --branch` | 4 ms |
| last commit | `git log -1 --format=%cI%x00%s` | in the above |
| stash count | `git stash list` | ~2 ms |
| **disk used** | `du -sm`, cached | **seconds** — see below |
| **time spent** | focus log, §5 | read of a local file |
| **notes** | `~/.local/share/devpit/notes/<id>.md` | file read |

### Disk: cached, but not for the reason assumed

**Correction (built 2026-09-03).** The original claim here — that `du` is slow
because one repo is huge — is wrong. `du` cost tracks **file count, not bytes**:
gmat-pred is 29 GB but only 11.6k files and measures in **15 ms**, about the same
as a 294 MB `node_modules` with 10.6k files.

A single `du -m --max-depth=1` per repo yields the total *and* the breakdown in one
pass: **279 ms** for all ten repos, against 459 ms when subdirectories were measured
individually. (`-s` and `--max-depth` are mutually exclusive in GNU du — passing
both makes it exit silently, which briefly produced a very flattering 9 ms.)

So the cache exists to keep the panel's *open* instant, not because measuring is
expensive. Cached reads are 10 ms. That makes a background refresh cheap and
routine rather than something to avoid.

- sizes are computed in the background and **cached with a timestamp**
- the UI shows the cached number and when it was measured
- a rescan is explicit, per repo or all

Breakdown per repo of the usual space eaters, because that is the actionable part:
`.git`, `node_modules`, `.venv`/`venv`, `target`, `dist`, `build`, plus the largest
one or two other top-level directories. Real examples on this machine today:
`my-portfolio` 294 MB `node_modules`, `pcb-catcher` 329 MB `.venv`,
`gmat-pred` 28 GB `data`.

---

## 3. Sessions — switching repos and getting your windows back

The wanted behaviour: switch to a repo, and its VSCode and browser come back as
they were.

### Two separate spaces  🔒 DECIDED 2026-09-03

Cockpits are **not** special workspaces and **not** part of user space. They are a
second space with their own workspace numbers and their own keybind mode.

```
user space      workspaces   1-50    rows 1-10, five workspaces each
startup dash    workspace      99
cockpit space   workspaces 101-150   cockpit rows 1-10, five workspaces each
```

User space is untouched — `Ctrl+Super+3` still goes to row 3, workspace 11 is
reachable exactly as before, and no user workspace is consumed by a cockpit.

**Entering a cockpit switches keybind mode.** `Super+D` focuses cockpit row 1 and
enters Hyprland's `cockpit` submap, which rebinds workspace navigation to cockpit
space. While inside, `Super+1..5` and `Ctrl+Super+1..0` move around *cockpits* —
you cannot wander back into user space by accident. `Super+D` or `Super+Escape`
leaves, returning to the exact user workspace you came from.

Everything that is not workspace navigation is declared `submap_universal`, so
media keys, screenshots, the launcher and window management keep working inside a
cockpit. Verified: volume keys still respond in cockpit mode.

Each cockpit is a **row of five workspaces**, so a repo's cockpit can hold as many
windows as you want rather than being a single panel.

The bar shows `c1` in the accent colour while in cockpit space, against `r3` in
user space, so the current mode is never ambiguous.

### A repo owns a ROW: five workspaces

**Settled 2026-09-03.** A row is **five** workspaces — 1–5 is row 1, 6–10 row 2,
11–15 row 3 — which is what the overview actually draws (`rows: 2, columns: 5`), so
one overview page shows **two repos**.

This replaced an earlier reading of "row" as the old group of ten. The Hyprland
binds were reworked to match:

| Bind | Does |
|---|---|
| `Super+1..5` | workspace 1–5 *within the current row* |
| `Super+Shift+1..5` | move the window there |
| `Ctrl+Super+1..0` | **go to row 1–10** — this is the repo switch |
| `Ctrl+Super+Alt+1..0` | move the window to that row |

Going to a row **keeps your slot**: from row 1 workspace 2, `Ctrl+Super+3` lands on
workspace 12, not 11. So switching repo puts you on the equivalent workspace rather
than always the first. Verified with real keypresses.

Ten rows is ten repos, more than currently exist.

### Park, do not close

**Windows are never closed on a switch.** They stay on their row. Nothing is
snapshotted, nothing is restored, and switching is instant and lossless.

### "Isn't parking heavy?"

Fair question. The cost is real but bounded, and it is not what it first looks
like:

- Parked windows cost exactly what they would cost open anyway. Switching to a repo
  you are *actively working on* is not extra memory — it is the memory you were
  already spending.
- The cost only grows if you park **many** repos at once. With 9 repos on this
  machine, keeping all of them holding a VSCode and a browser would be genuinely
  heavy.

So parking is for the **two or three repos you are switching between**, not all of
them. devpit makes that manageable rather than implicit:

- **Report RAM per bound row**, so the cost is visible instead of mysterious. This
  is just another monitor row (§4).
- **"Close this repo's windows"** as an explicit action, returning that row to cold.
- A cold row restores via §3's cold-start path when you next enter it.

The alternative — closing and restoring on every switch — trades that memory for
seconds of latency *and* fidelity loss on every single switch. Parking is the right
default; visibility and an easy eviction are what keep it honest.

### Cold start (after a reboot) — and an honest limit

Only here does anything need restoring.

- **VSCode: essentially free.** It keeps per-folder state (7 `workspaceStorage`
  entries on this machine), so `code <repo>` reopens that folder's tabs and layout
  by itself. devpit just launches it.
- **Browser: cannot be captured reliably.** There is no supported way to read the
  tabs of a specific window from outside the browser. `sessionstore.jsonlz4` is
  whole-profile, undocumented, and rewritten on Firefox's own schedule.

  So devpit does **not** pretend to snapshot browser tabs. Instead each repo has a
  **URL list** you maintain (or add to with one command), opened as a new window on
  restore. Predictable, and it never silently loses your tabs.

  If true per-repo browser isolation is wanted later, the honest mechanism is a
  **Firefox profile per repo** (`firefox -P <repo> --new-instance`), which restores
  perfectly because Firefox does it. Heavy; parked as an option, not the default.

### What a session record holds
```jsonc
{ "repo": "gmat-pred", "row": 3,
  "launch": [ {"cmd": "code",    "args": ["<path>"]},
              {"cmd": "kitty",   "args": ["--directory", "<path>"]} ],
  "urls":   ["https://…"],
  "last_active": "2026-09-03T12:00:00Z" }
```

---

## 4. Monitor tab

Everything a dev machine accumulates, with **path and size where one exists**, and
actions with real error messages.

| Thing | Source | Path shown | Actions |
|---|---|---|---|
| containers (running + stopped) | `docker ps -a --format json` | — | start · stop · restart · remove |
| images | `docker images --format json` | — | remove |
| volumes | `docker volume ls`, `inspect` | `/var/lib/docker/volumes/…` | remove |
| **virtualenvs** | find `pyvenv.cfg` under roots | yes | delete |
| failed user units | `systemctl --user list-units --state=failed` | — | restart · view log |
| **listening ports** | `ss -tlnp` | owning process | kill owner |

Current state here: 2 images (6.9 GB), 5 volumes (3.3 GB), 2 stopped containers,
2 virtualenvs (329 MB python 3.12, 104 MB python 3.14).

### Virtualenvs deserve their own row type
Found by locating `pyvenv.cfg`. Report path, **python version** (from the file),
size, and mtime. Flag two cases worth acting on:
- **orphaned** — the parent directory is no longer a repo, or the repo is gone
- **stale** — untouched for months

### Errors are a feature
Every action reports what actually happened. `docker stop` on a container that has
already died says so; a permission failure says which permission. No silent
failures, no generic "something went wrong". This is the same rule as §6.

---

## 5. Time tracking

Where the hours actually went, per repo per day.

**Mechanism:** a small event log of *focus transitions* — timestamp, workspace,
and the repo bound to it. Append-only JSONL at
`~/.local/share/devpit/focus.jsonl`. Aggregation happens at read time.

**Who writes it:** whatever is already watching. In the rice, the shell is already
subscribed to Hyprland's event socket, so it appends — no extra process. Standalone,
`devpit watch` can do the same job.

Reported: today, this week, per repo, and a simple daily bar. Idle gaps beyond a
threshold are not counted, so leaving a workspace focused overnight does not claim
eight hours of work.

---

## 6. Notes — terminal-first, so an agent can read them

Per-repo notes, plain **Markdown**, one file per repo.

**Location:** `~/.local/share/devpit/notes/<repo-id>.md`, outside the repo by
default — notes are personal (todos, gotchas, half-thoughts) and should not land in
a commit. Opt in per repo to keep them in-repo at `.devpit/notes.md` instead.

### Structure: pinned context, then a log

One file, two parts, so it stays a single `cat`-able Markdown document:

```markdown
# gmat-pred
<!-- pinned -->
Context that stays true: what this project is, conventions, gotchas.
This is what an agent should read first.
<!-- /pinned -->

## Log
- 2026-09-03 14:02 — retrained on the new split, AUC 0.81
- 2026-09-02 09:40 — data/ is 28GB, do not add it to git
```

The pinned block is durable project context; the log is chronological and appended
to. An agent reads the whole file and knows which half is which.

### Rendered, not raw, in the GUI
Markdown on disk, but the app **renders** it — headings, checkboxes, code blocks,
links — rather than showing a text dump. The pinned block gets its own visual
treatment at the top, and the log reads as entries. Editing is inline; the file on
disk stays plain Markdown at all times.

**CLI, because an AI agent will use this more than the GUI:**
```
devpit note                    # print the current repo's notes
devpit note add "…"            # append a timestamped line
devpit note edit               # open in $EDITOR
devpit note --repo gmat-pred   # any repo, from anywhere
devpit note --json             # for a tool to consume
```
Repo is inferred from `$PWD` when not given.

Plain Markdown with no database is the whole point: an agent can `cat` it and
append to it without knowing devpit exists.

---

## 7. Integration with the rice (Ashura)

Kept deliberately thin so devpit stays standalone.

- The cockpit lives on `special:cockpit`, toggled by `Super+D` or 3-finger-up.
  Already wired 2026-09-03.
- **The bar and sidebars change on that workspace.** Ashura already has this
  mechanism — `dashboardMode` hides zones on the startup workspace — so the cockpit
  workspace gets its own variant the same way. This is Ashura's job, not devpit's.
- Ashura injects its Material You palette; devpit ships a neutral fallback and must
  render correctly with no palette supplied.
- Ashura appends to the focus log (§5).

---

## 8. Explicitly out of scope

- Anything that writes to a repo: no commit, push, stash, branch switch. That is a
  git client's job and half-building one is how this goes wrong.
- Snapshotting arbitrary browser tabs (§3 — not reliably possible; URL lists instead).
- Remote or multi-machine anything.
- Replacing btop, lazygit or `docker`. devpit surfaces and hands off.

---

## 9. Build order

Each step ends with something runnable.

1. `devpit repos --json` — discovery, git state. Useful from a terminal alone.
2. `devpit note` — the whole notes CLI. Immediately useful, agent-usable.
3. Disk sizing with its cache, and the space-eater breakdown.
4. `devpit monitor --json` — docker, venvs, units, ports.
5. Actions, non-destructive first, then the confirmed destructive ones.
6. GUI skeleton rendering the JSON. Separability check runs from here on.
7. Repo switching and workspace binding.
8. Time tracking, once the rice is appending focus events.

---

## 10. Suggestions worth folding in

Proposed 2026-09-03, not yet accepted.

### Cheap, high value
- **The bar shows the current row's repo** — name, branch, dirty count, while you
  are on a bound row. One glance tells you which repo you are in, which matters far
  more once rows are repos. Ashura's side, and small.
- **The overview labels rows with their repo.** The workspace grid already draws
  rows; naming them turns the overview into a repo switcher for free.
- **Quick-note keybind** — append a line to the current row's repo notes from
  anywhere, without opening the app. Notes only get written if capturing is
  frictionless.

### Makes parking honest
- **RAM per bound row** in the monitor tab, with "close this repo's windows" beside
  it. Turns the parking cost from invisible into a number you can act on (§3).

### Reduces setup friction
- **Suggest the binding.** When you first use a row that has a VSCode window with
  folder X open, offer to bind that row to repo X rather than asking cold.

### On switching in
- **A resume card**: pinned notes, last commit, dirty count — shown briefly when you
  enter a repo's row. Answers "where was I" without opening anything. Fits the
  notes structure directly (§6).

---

## 11. Open questions

- ~~Repo identity~~ — **RESOLVED 2026-09-03: git remote URL when there is one,
  else a hash of the path.** Survives moves and matches clones on other machines.
- ~~Workspace ownership~~ — **RESOLVED 2026-09-03. Not exclusive.** A *row* may have
  a repo bound to it; several rows may be bound to different repos at once, so two
  or more repos are open simultaneously by design. The binding is chosen when a new
  row is first used, and is editable in settings afterwards. Nothing prevents two
  rows pointing at the same repo.
- **Editor and terminal commands** — `code <path>`, `kitty --directory <path>`,
  routed through `app2unit` in the rice. Confirm.
- ~~Flag stopped containers and dangling images as reclaimable?~~ —
  **RESOLVED 2026-09-03: yes**, same treatment as stale venvs. Anything reclaimable
  is tagged with the bytes it would return.
