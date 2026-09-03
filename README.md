# devpit

A developer cockpit: manage the repos on your machine, see where your time and
disk are going, keep notes per project, and monitor the things a dev machine
accumulates — containers, images, volumes, virtualenvs.

**Standalone by design.** It grew out of the Ashura rice but does not depend on
it: `devpit` is a CLI and a GUI that run anywhere. The rice consumes it, not the
other way round.

- `devpit` — CLI. Emits JSON, manages notes, performs actions.
- `devpit-gui` — the panel. Renders what the CLI reports; never shells out itself.

See [docs/DESIGN.md](docs/DESIGN.md).
