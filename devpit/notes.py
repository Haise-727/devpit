"""Per-repo notes.

Plain Markdown, one file per repo, deliberately outside the repository: notes are
personal (todos, gotchas, half-thoughts) and should not end up in a commit.

The file has two halves - a pinned block of durable context, then a chronological
log. An agent reading the file gets the project's standing context first without
needing to know devpit exists; nothing here is a database, and the file is always
valid Markdown on disk even mid-edit.
"""
from __future__ import annotations
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from . import config as cfg_mod
from .identity import remote_of, repo_id

PIN_OPEN, PIN_CLOSE = "<!-- pinned -->", "<!-- /pinned -->"
LOG_HEADING = "## Log"

TEMPLATE = """# {name}

{open}
{close}

{log}
"""


def notes_dir(cfg: dict | None = None) -> Path:
    d = cfg_mod.data_dir(cfg) / "notes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def repo_root(start: Path | None = None) -> Path | None:
    """The repo containing a path - so `devpit note` works from anywhere inside it."""
    start = start or Path.cwd()
    try:
        r = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def resolve(repo: str | None, cfg: dict | None = None) -> tuple[Path, str] | None:
    """Map a --repo name (or the cwd) to (path, id)."""
    cfg = cfg or cfg_mod.load()
    if repo:
        from .repos import discover
        for p in discover(cfg):
            if p.name == repo or str(p) == repo:
                return p, repo_id(p, remote_of(p))
        return None
    root = repo_root()
    if not root:
        return None
    return root, repo_id(root, remote_of(root))


def path_for(rid: str, cfg: dict | None = None) -> Path:
    return notes_dir(cfg) / f"{rid}.md"


def read(rid: str, name: str = "", cfg: dict | None = None) -> str:
    p = path_for(rid, cfg)
    if not p.exists():
        return TEMPLATE.format(name=name or rid, open=PIN_OPEN, close=PIN_CLOSE,
                               log=LOG_HEADING)
    return p.read_text()


def parse(text: str) -> dict:
    """Split into pinned context and log entries, for a renderer to treat differently."""
    pinned = ""
    if PIN_OPEN in text and PIN_CLOSE in text:
        pinned = text.split(PIN_OPEN, 1)[1].split(PIN_CLOSE, 1)[0].strip()
    entries = []
    if LOG_HEADING in text:
        for line in text.split(LOG_HEADING, 1)[1].splitlines():
            line = line.strip()
            if line.startswith("- "):
                entries.append(line[2:])
    return {"pinned": pinned, "entries": entries}


def add(rid: str, message: str, name: str = "", cfg: dict | None = None) -> Path:
    """Append a timestamped line under the log heading."""
    p = path_for(rid, cfg)
    text = read(rid, name, cfg)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- {stamp} — {message.strip()}\n"
    if LOG_HEADING in text:
        head, _, tail = text.partition(LOG_HEADING)
        # newest first: entries read top-down as a changelog
        text = f"{head}{LOG_HEADING}\n{entry}{tail.lstrip(chr(10))}"
    else:
        text = text.rstrip() + f"\n\n{LOG_HEADING}\n{entry}"
    p.write_text(text)
    return p


def set_pinned(rid: str, body: str, name: str = "", cfg: dict | None = None) -> Path:
    p = path_for(rid, cfg)
    text = read(rid, name, cfg)
    if PIN_OPEN in text and PIN_CLOSE in text:
        before = text.split(PIN_OPEN, 1)[0]
        after = text.split(PIN_CLOSE, 1)[1]
        text = f"{before}{PIN_OPEN}\n{body.strip()}\n{PIN_CLOSE}{after}"
    else:
        text = f"{PIN_OPEN}\n{body.strip()}\n{PIN_CLOSE}\n\n" + text
    p.write_text(text)
    return p


def edit(rid: str, name: str = "", cfg: dict | None = None) -> int:
    p = path_for(rid, cfg)
    if not p.exists():
        p.write_text(read(rid, name, cfg))
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    return subprocess.call([editor, str(p)])
