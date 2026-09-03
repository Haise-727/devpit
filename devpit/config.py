"""Configuration, with defaults that work on a machine that has never seen devpit.

Every key has a default, so the config file is optional and additive - the same
pattern the Ashura shell uses, and the reason a package can be dropped onto a new
machine and still run.
"""
from __future__ import annotations
import json, os
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("DEVPIT_CONFIG",
                                  Path.home() / ".config/devpit/config.json"))

DEFAULTS = {
    # Globs searched for repositories. A directory counts if it contains .git
    "roots": ["~/Coding/Projects/*"],
    # Repos outside the roots that should still be tracked
    "extra_repos": ["~/rice", "~/.config"],
    "data_dir":  "~/.local/share/devpit",
    # Thresholds for the flags in repos.py. Days.
    "stale_unpushed_days": 3,
    "stale_dirty_days": 7,
    "stash_pile": 3,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as e:
        # A broken config must not take the tool down - say so and use defaults.
        print(f"devpit: ignoring malformed {CONFIG_PATH}: {e}", flush=True)
    return cfg


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def data_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or load()
    d = expand(cfg["data_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d
