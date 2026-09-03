"""Disk usage per repo, with a cache.

A single `du -m --max-depth=1` per repo yields the total *and* the per-directory
breakdown in one pass - measured at 254 ms across all repos here, against 459 ms
when each subdirectory was measured separately.

Note for anyone revisiting this: `du` cost tracks the number of files, not bytes.
gmat-pred is 29 GB but only 11.6k files and measures in 15 ms; node_modules is
294 MB across 10.6k files and costs about the same. So the cache exists to keep
the panel's open instant, not because measuring is expensive.

`-s` and `--max-depth` are mutually exclusive in GNU du - passing both makes it
exit immediately and report nothing.
"""
from __future__ import annotations
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg_mod
from .identity import remote_of, repo_id

# Directories worth calling out by name, because they are the ones you can act on
KNOWN_EATERS = {"node_modules", ".venv", "venv", "env", "target", "dist", "build",
                ".next", ".cache", "__pycache__", ".tox", "vendor"}


def cache_path(cfg: dict | None = None) -> Path:
    return cfg_mod.data_dir(cfg) / "sizes.json"


def load_cache(cfg: dict | None = None) -> dict:
    try:
        return json.loads(cache_path(cfg).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict, cfg: dict | None = None) -> None:
    cache_path(cfg).write_text(json.dumps(cache, indent=2))


def measure(path: Path) -> dict | None:
    """Total and per-top-level-directory size, in MB, from one du pass."""
    try:
        r = subprocess.run(["du", "-m", "--max-depth=1", str(path)],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if not r.stdout.strip():
        return None

    total = 0
    children: dict[str, int] = {}
    root = str(path)
    for line in r.stdout.splitlines():
        try:
            mb_s, name = line.split("\t", 1)
            mb = int(mb_s)
        except ValueError:
            continue
        if name.rstrip("/") == root.rstrip("/"):
            total = mb
        else:
            children[Path(name).name] = mb

    # Biggest first, and keep it short - the point is what to act on, not an inventory
    ranked = sorted(children.items(), key=lambda kv: kv[1], reverse=True)
    eaters = [{"name": n, "mb": m, "known": n in KNOWN_EATERS}
              for n, m in ranked[:6] if m > 0]
    reclaimable = sum(m for n, m in ranked if n in KNOWN_EATERS and n != ".git")

    return {
        "total_mb": total,
        "eaters": eaters,
        "reclaimable_mb": reclaimable,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def scan(paths: list[Path], cfg: dict | None = None, rescan: bool = False) -> dict:
    """Measure the given repos, reusing cached values unless rescan is asked for."""
    cfg = cfg or cfg_mod.load()
    cache = load_cache(cfg)
    started = time.time()
    measured = 0
    out: dict[str, dict] = {}
    for p in paths:
        rid = repo_id(p, remote_of(p))
        if not rescan and rid in cache:
            out[rid] = cache[rid]
            continue
        got = measure(p)
        if got:
            got["path"] = str(p)
            out[rid] = cache[rid] = got
            measured += 1
    save_cache(cache, cfg)
    return {
        "sizes": out,
        "measured": measured,
        "from_cache": len(out) - measured,
        "duration_ms": int((time.time() - started) * 1000),
    }
