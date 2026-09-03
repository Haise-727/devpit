"""devpit command line.

Every side effect lives here or below - the GUI is a renderer and never shells out
itself. That keeps actions auditable, testable without a compositor, usable over
SSH, and drivable by an agent.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from . import config as cfg_mod
from . import notes as notes_mod
from . import repos as repos_mod
from . import sizes as sizes_mod

C = {"reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
     "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
     "blue": "\033[34m", "cyan": "\033[36m"}


def paint(s: str, colour: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"{C.get(colour, '')}{s}{C['reset']}"


FLAG_COLOUR = {
    "unpushed-stale": "yellow", "dirty-stale": "yellow", "stash-pile": "yellow",
    "detached": "red", "conflicted": "red", "no-remote": "dim",
}


def cmd_repos(args) -> int:
    cfg = cfg_mod.load()
    data = repos_mod.scan(cfg)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    for r in data["repos"]:
        if r.get("error"):
            print(f"  {paint(r['name'], 'red'):<28} {r['error']}")
            continue
        dirty = r["dirty"] + r["staged"] + r["untracked"]
        bits = []
        if dirty:
            bits.append(paint(f"{dirty}~", "yellow"))
        if r["ahead"]:
            bits.append(paint(f"{r['ahead']}^", "cyan"))
        if r["behind"]:
            bits.append(paint(f"{r['behind']}v", "blue"))
        if r["stashes"]:
            bits.append(paint(f"{r['stashes']}s", "dim"))
        flags = " ".join(paint(f, FLAG_COLOUR.get(f, "dim")) for f in r["flags"])
        print(f"  {paint(r['name'], 'bold'):<26} {(r.get('branch') or '?')[:26]:<28}"
              f"{' '.join(bits):<26} {flags}")
    print(f"\n  {len(data['repos'])} repos, {data['duration_ms']} ms")
    return 0


def _mb(n: int) -> str:
    return f"{n/1024:.1f}G" if n >= 1024 else f"{n}M"


def cmd_size(args) -> int:
    cfg = cfg_mod.load()
    paths = repos_mod.discover(cfg)
    if args.repo:
        paths = [p for p in paths if p.name == args.repo or str(p) == args.repo]
        if not paths:
            print(f"devpit: no repo named {args.repo}", file=sys.stderr)
            return 1
    data = sizes_mod.scan(paths, cfg, rescan=args.rescan)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    rows = sorted(data["sizes"].values(), key=lambda v: v["total_mb"], reverse=True)
    for v in rows:
        name = Path(v.get("path", "?")).name
        eaters = "  ".join(
            paint(f"{e['name']} {_mb(e['mb'])}", "yellow" if e["known"] else "dim")
            for e in v["eaters"][:3])
        recl = paint(f"  {_mb(v['reclaimable_mb'])} reclaimable", "cyan") if v["reclaimable_mb"] else ""
        print(f"  {paint(name, 'bold'):<26} {_mb(v['total_mb']):>8}   {eaters}{recl}")
    total = sum(v["total_mb"] for v in rows)
    recl = sum(v["reclaimable_mb"] for v in rows)
    print(f"\n  {len(rows)} repos, {_mb(total)} total, {paint(_mb(recl) + ' reclaimable', 'cyan')}"
          f"  ({data['measured']} measured, {data['from_cache']} cached, {data['duration_ms']} ms)")
    return 0


def cmd_note(args) -> int:
    cfg = cfg_mod.load()
    found = notes_mod.resolve(args.repo, cfg)
    if not found:
        target = args.repo or "the current directory"
        print(f"devpit: no repo found for {target}", file=sys.stderr)
        return 1
    path, rid = found

    if args.action == "add":
        p = notes_mod.add(rid, " ".join(args.message), path.name, cfg)
        if not args.json:
            print(f"  noted in {path.name}  ({p})")
        return 0

    if args.action == "edit":
        return notes_mod.edit(rid, path.name, cfg)

    if args.action == "pin":
        notes_mod.set_pinned(rid, " ".join(args.message), path.name, cfg)
        if not args.json:
            print(f"  pinned context updated for {path.name}")
        return 0

    text = notes_mod.read(rid, path.name, cfg)
    if args.json:
        print(json.dumps({"repo": path.name, "id": rid,
                          "path": str(notes_mod.path_for(rid, cfg)),
                          "raw": text, **notes_mod.parse(text)}, indent=2))
        return 0
    parsed = notes_mod.parse(text)
    print(f"  {paint(path.name, 'bold')}  {paint(str(notes_mod.path_for(rid, cfg)), 'dim')}")
    if parsed["pinned"]:
        print(f"\n  {paint('pinned', 'cyan')}")
        for line in parsed["pinned"].splitlines():
            print(f"    {line}")
    if parsed["entries"]:
        print(f"\n  {paint('log', 'cyan')}")
        for e in parsed["entries"][: args.limit]:
            print(f"    {e}")
        if len(parsed["entries"]) > args.limit:
            print(paint(f"    … {len(parsed['entries']) - args.limit} older", "dim"))
    if not parsed["pinned"] and not parsed["entries"]:
        print(paint("\n  no notes yet - devpit note add \"…\"", "dim"))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="devpit",
                                 description="a developer cockpit for your repos and clutter")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("repos", help="every repo, with git state")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_repos)

    p = sub.add_parser("size", help="disk used per repo, and what is eating it")
    p.add_argument("--repo", help="a single repo")
    p.add_argument("--rescan", action="store_true", help="ignore cached measurements")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_size)

    p = sub.add_parser("note", help="per-repo notes (defaults to the current repo)")
    p.add_argument("action", nargs="?", default="show",
                   choices=["show", "add", "edit", "pin"])
    p.add_argument("message", nargs="*", help="text for add/pin")
    p.add_argument("--repo", help="repo name, instead of using the current directory")
    p.add_argument("--limit", type=int, default=20, help="log entries to show")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_note)

    args = ap.parse_args(argv)
    return args.func(args)
