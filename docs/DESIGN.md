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

### Disk: cached, never on the hot path
Measured here: `du` across the project roots is slow because one repo is huge —
**gmat-pred is 29.3 GB** (28 GB of it `data/`, 1 GB `models/`, and a 13 MB `.git`).
So:

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

### How this actually works: park, do not close

**Warm switch (the normal case): windows are never closed.** Each repo is bound to
a Hyprland workspace. Switching repos switches workspace. The VSCode window and the
browser window simply stay where they are.

This is lossless, instant, and needs no session files at all. It is strictly better
than saving and restoring state, and it is why this is the primary mechanism.

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
{ "repo": "gmat-pred", "workspace": 3,
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

## 10. Open questions

- **Repo identity.** Path, or git remote URL? Path is simpler; remote survives a
  move and matches clones on other machines. Leaning: remote when there is one,
  else path hash.
- **Does a repo own a workspace, or does a workspace own a repo?** Affects what
  happens when you open two repos at once. Suggest: a repo may claim at most one
  workspace, and claiming is explicit.
- **Editor and terminal commands** — `code <path>`, `kitty --directory <path>`,
  routed through `app2unit` in the rice. Confirm.
- **Do stopped containers and dangling images get flagged as reclaimable**, the way
  stale venvs do? Leaning yes — same "space you forgot about" idea.
