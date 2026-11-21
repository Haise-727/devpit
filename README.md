# devpit

A developer cockpit for the command line. It finds every git repo on your
machine and tells you what state each one is in, where your disk is going,
what a dev machine has piled up (containers, images, volumes, virtualenvs,
listening ports), and keeps notes per project. Every command can print JSON,
so a desktop panel can show the same thing.

It runs on plain Python 3.11 with no dependencies; it shells out to `git`,
`docker` and `ss` when they are there.

## Install

```sh
git clone https://github.com/Haise-727/devpit
ln -s "$PWD/devpit/bin/devpit" ~/.local/bin/devpit
```

`bin/devpit` runs the checkout with the system Python, so an active project
virtualenv never gets in the way. To install it as a package instead:
`pipx install ./devpit`.

## Use

```sh
devpit repos                # every repo: branch, unpushed and uncommitted work, stashes, flags
devpit size                 # disk used per repo, and what is eating it
devpit monitor              # containers, images, volumes, virtualenvs, user units, ports
devpit do list              # actions on what the monitor showed
devpit do container.stop web          # shows what it would do
devpit do container.stop web --yes    # does it
devpit note add "try the new schema"  # a note on the repo you are in
devpit note                 # notes for this repo
devpit cockpit add          # opt this repo in to cockpits
devpit cockpit spec myapp   # what opening that repo's cockpit launches
```

Add `--json` to any command for machine readable output.

### Actions

Nothing is changed without `--yes`: without it, `devpit do` only shows what it
would do.

| Action | Does |
|---|---|
| `container.start`, `container.stop`, `container.restart` | as named |
| `container.remove` | delete a container; named volumes are kept |
| `port.kill` | SIGTERM whatever listens on a port |
| `venv.remove` | delete a virtualenv directory |
| `docker.prune` | remove stopped containers, dangling images and unused networks |

### Flags in `devpit repos`

A repo is flagged when it has commits not pushed for 3 days, uncommitted work
older than 7 days, 3 or more stashes, or no remote at all.

## Cockpits

A cockpit is a repo you have opted in to. devpit keeps the list
(`devpit cockpit list`) and, for each one, what to launch when it opens: by
default VS Code and a kitty terminal in the repo, plus a Firefox window with
any URLs you saved (`devpit cockpit urls <repo> <url>...`). The terminal gets
its own window class, `kitty-cockpit`, so a window manager can tell it apart.

devpit itself never touches windows. Putting them on a workspace, parking
them and bringing them back is the window manager's side:
[Ashura](https://github.com/Haise-727/ashura) does it in Hyprland, with a
cockpit space of its own and a picker in the shell.

## Configuration

Optional. `~/.config/devpit/config.json` (or `$DEVPIT_CONFIG`) only needs the
keys you want to change:

```json
{
  "roots": ["~/Coding/*", "~/Coding/*/*"],
  "extra_repos": ["~/.config"],
  "stale_unpushed_days": 3,
  "stale_dirty_days": 7,
  "stash_pile": 3
}
```

`roots` are globs searched for directories that contain `.git`. The default
covers `~/Coding` (two levels deep), `~/Projects`, `~/projects`, `~/src`,
`~/code` and `~/dev`. Notes, cockpits and caches live in
`~/.local/share/devpit`.

## Tests

```sh
python3 -m pytest
```

The tests build throwaway repos in a temporary folder and never touch yours.

## Design

[docs/DESIGN.md](docs/DESIGN.md) has the reasoning behind each part, including
the time tracking and panel that are planned but not built yet.

## Licence

MIT. See [LICENSE](LICENSE).
