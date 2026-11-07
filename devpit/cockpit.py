"""Cockpits: the repos Haise has opted in to, and what opening one launches.

devpit only keeps the list and the launch spec. Putting windows on a workspace,
tagging them and hiding them is the window manager's job (Ashura does it in its
Hyprland config), so devpit stays usable without any particular desktop.

State lives in `<data_dir>/cockpits.json`:

    {"version": 1, "cockpits": [
        {"id": "...", "name": "gmat-pred", "path": "/home/...", "added_at": "...",
         "launch": [{"cmd": ["code", "<path>"]}, ...], "urls": []}]}

Writes go through a temp file and rename, so a crash mid-write never leaves a
half-written list behind.
"""
from __future__ import annotations
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg_mod
from . import repos as repos_mod
from .identity import remote_of, repo_id

VERSION = 1


class CockpitError(Exception):
    """A request that cannot be satisfied, with a message fit to show the user."""


def _state_path(cfg: dict) -> Path:
    return cfg_mod.data_dir(cfg) / "cockpits.json"


def load(cfg: dict | None = None) -> dict:
    cfg = cfg or cfg_mod.load()
    p = _state_path(cfg)
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return {"version": VERSION, "cockpits": []}
    except json.JSONDecodeError as e:
        # Refuse rather than overwrite: the file may hold choices worth keeping.
        raise CockpitError(f"{p} is not valid JSON ({e}); fix or move it, devpit will not overwrite it")
    data.setdefault("cockpits", [])
    return data


def _save(data: dict, cfg: dict) -> None:
    p = _state_path(cfg)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".cockpits.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def default_launch(path: Path) -> list[dict]:
    """VS Code and kitty in the repo, and a fresh Firefox window (Haise's choice)."""
    return [
        {"cmd": ["code", str(path)]},
        {"cmd": ["kitty", "--directory", str(path)]},
        {"cmd": ["firefox", "--new-window"]},
    ]


def resolve_repo(ref: str | None, cfg: dict) -> Path:
    """A repo from a name, an id, a path, or (when ref is None) the current directory.

    Names are matched against discovered repos first. A path may point anywhere,
    even outside the configured roots, as long as it is a git repo.
    """
    if ref is None:
        here = Path.cwd().resolve()
        for cand in [here, *here.parents]:
            if (cand / ".git").exists():
                return cand
        raise CockpitError("the current directory is not inside a git repo; name one")

    as_path = Path(os.path.expanduser(ref))
    if as_path.is_dir():
        if not (as_path / ".git").exists():
            raise CockpitError(f"{as_path} is not a git repo")
        return as_path.resolve()

    found = repos_mod.discover(cfg)
    by_name = [p for p in found if p.name == ref]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise CockpitError(f"more than one repo is called {ref}: "
                           + ", ".join(str(p) for p in by_name) + "; use a path")
    for p in found:
        if repo_id(p, remote_of(p)) == ref:
            return p
    raise CockpitError(f"no repo called {ref} under the configured roots")


def _find(data: dict, path: Path) -> dict | None:
    for c in data["cockpits"]:
        if Path(c["path"]) == path:
            return c
    return None


def add(ref: str | None, cfg: dict | None = None) -> dict:
    cfg = cfg or cfg_mod.load()
    # Always store the resolved path, so the duplicate check and remove() agree
    # however the repo was reached (name, symlinked path, cwd).
    path = resolve_repo(ref, cfg).resolve()
    data = load(cfg)
    existing = _find(data, path)
    if existing:
        return {"added": False, "cockpit": existing}
    entry = {
        "id": repo_id(path, remote_of(path)),
        "name": path.name,
        "path": str(path),
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "launch": default_launch(path),
        "urls": [],
    }
    data["cockpits"].append(entry)
    data["cockpits"].sort(key=lambda c: c["name"].lower())
    _save(data, cfg)
    return {"added": True, "cockpit": entry}


def remove(ref: str, cfg: dict | None = None) -> dict:
    """Remove by name, id or path. Matches the stored list, so a repo that has been
    deleted from disk can still be removed."""
    cfg = cfg or cfg_mod.load()
    data = load(cfg)
    target = os.path.expanduser(ref)
    keep, gone = [], []
    for c in data["cockpits"]:
        hit = ref in (c["name"], c["id"]) or os.path.realpath(target) == c["path"]
        (gone if hit else keep).append(c)
    if not gone:
        raise CockpitError(f"{ref} has no cockpit")
    if len(gone) > 1:
        raise CockpitError(f"{ref} matches {len(gone)} cockpits; use the path")
    data["cockpits"] = keep
    _save(data, cfg)
    return {"removed": gone[0]}


def spec(ref: str, cfg: dict | None = None) -> dict:
    """What opening this cockpit should launch, with the URL list appended to the
    Firefox command so a fresh window opens on them."""
    cfg = cfg or cfg_mod.load()
    data = load(cfg)
    for c in data["cockpits"]:
        if ref in (c["name"], c["id"], c["path"]):
            launch = []
            for item in c["launch"]:
                cmd = list(item["cmd"])
                if cmd and cmd[0] == "firefox" and c.get("urls"):
                    cmd += c["urls"]
                launch.append({"cmd": cmd})
            return {"id": c["id"], "name": c["name"], "path": c["path"],
                    "exists": Path(c["path"]).is_dir(), "launch": launch}
    raise CockpitError(f"{ref} has no cockpit")


def listing(cfg: dict | None = None, with_state: bool = True) -> dict:
    """Every cockpit, with live git state when the repo is still on disk."""
    cfg = cfg or cfg_mod.load()
    data = load(cfg)
    out = []
    for c in data["cockpits"]:
        p = Path(c["path"])
        row = {k: c[k] for k in ("id", "name", "path", "added_at")}
        row["exists"] = p.is_dir()
        row["urls"] = c.get("urls", [])
        if with_state and row["exists"]:
            st = repos_mod.inspect(p, cfg)
            row["git"] = {k: st.get(k) for k in
                          ("branch", "dirty", "staged", "untracked", "ahead", "behind", "flags",
                           "last_commit_at", "last_commit_subject")}
        out.append(row)
    return {"cockpits": out}


def candidates(cfg: dict | None = None) -> dict:
    """Discovered repos that do not have a cockpit yet: what an "add" picker offers."""
    cfg = cfg or cfg_mod.load()
    taken = {c["path"] for c in load(cfg)["cockpits"]}
    return {"candidates": [{"name": p.name, "path": str(p)}
                           for p in repos_mod.discover(cfg) if str(p.resolve()) not in taken]}


def set_urls(ref: str, urls: list[str], cfg: dict | None = None) -> dict:
    cfg = cfg or cfg_mod.load()
    data = load(cfg)
    for c in data["cockpits"]:
        if ref in (c["name"], c["id"], c["path"]):
            c["urls"] = urls
            _save(data, cfg)
            return {"cockpit": c}
    raise CockpitError(f"{ref} has no cockpit")
