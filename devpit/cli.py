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
from . import actions as actions_mod
from . import monitor as monitor_mod
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


def cmd_monitor(args) -> int:
    cfg = cfg_mod.load()
    data = monitor_mod.scan(cfg)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    d = data["docker"]
    print(paint("  containers", "bold"))
    if not d.get("available"):
        print(paint(f"    unavailable: {d.get('reason', 'docker not usable')}", "red"))
    elif not d["containers"]:
        print(paint("    none", "dim"))
    else:
        for c in d["containers"]:
            colour = "green" if c["state"] == "running" else "dim"
            tag = paint("reclaimable", "cyan") if c["reclaimable"] else ""
            print(f"    {paint(c['state'][:7], colour):<16} {c['name'][:24]:<26}"
                  f"{c['image'][:30]:<32} {tag}")

    if d.get("available"):
        dang = [i for i in d["images"] if i["dangling"]]
        print(paint(f"\n  images ({len(d['images'])})", "bold"))
        for i in d["images"][:8]:
            name = "<dangling>" if i["dangling"] else f"{i['repo']}:{i['tag']}"
            print(f"    {name[:44]:<46} {i['size']:>10}"
                  f" {paint('reclaimable', 'cyan') if i['reclaimable'] else ''}")
        print(paint(f"\n  volumes ({len(d['volumes'])})", "bold"))
        for v in d["volumes"][:8]:
            print(f"    {v['name'][:30]:<32} {paint(v['path'], 'dim')}")
        if d.get("usage"):
            bits = "  ".join(f"{k.replace('_',' ')} {v['size']}"
                             f"{paint(' (' + v['reclaimable'] + ' free-able)', 'cyan') if v.get('reclaimable') else ''}"
                             for k, v in d["usage"].items())
            print(f"\n  {paint('docker disk', 'bold')}  {bits}")

    print(paint("\n  virtualenvs", "bold"))
    if not data["venvs"]:
        print(paint("    none", "dim"))
    for v in data["venvs"]:
        flags = []
        if v["orphaned"]:
            flags.append(paint("orphaned", "red"))
        if v["stale"]:
            flags.append(paint("stale", "yellow"))
        print(f"    {v['project'][:20]:<22} python {v['python']:<9} {_mb(v['mb']):>7}"
              f"  {v['stale_days']}d  {' '.join(flags)}")
        print(f"      {paint(v['path'], 'dim')}")

    if data["units_failed"]:
        print(paint("\n  failed units", "bold"))
        for u in data["units_failed"]:
            print(f"    {paint(u['unit'], 'red')}  {u['description']}")

    interesting = [p for p in data["ports"] if p["interesting"]]
    hidden = len(data["ports"]) - len(interesting)
    print(paint("\n  listening ports", "bold"))
    shown = data["ports"] if args.all_ports else interesting
    if not shown:
        print(paint("    none worth showing", "dim"))
    for p in shown:
        print(f"    {p['port']:<8} {p['addr'][:22]:<24} {p['proc'][:18]:<20}"
              f" {paint('noise', 'dim') if p['noise'] else ''}")
    if hidden and not args.all_ports:
        print(paint(f"    … {hidden} loopback/editor sockets hidden (--all-ports)", "dim"))

    for e in data["errors"]:
        print(paint(f"\n  {e['source']}: {e['message']}", "red"))
    print(f"\n  {data['duration_ms']} ms")
    return 0


def cmd_do(args) -> int:
    cfg = cfg_mod.load()
    if args.action == "list":
        for name, desc in actions_mod.ACTIONS.items():
            print(f"  {paint(name, 'bold'):<28} {desc}")
        return 0
    try:
        plan = actions_mod.plan_for(args.action, args.target or "", cfg)
    except actions_mod.ActionError as e:
        print(paint(f"  {e}", "red"), file=sys.stderr)
        return 1

    if args.json and not args.yes:
        print(json.dumps(plan, indent=2))
        return 0

    # Preview. Nothing has happened yet.
    print(f"  {paint(plan['action'], 'bold')}  {plan['target']}")
    print(f"    {plan['detail']}")
    for w in plan["warnings"]:
        print(paint(f"    ! {w}", "yellow"))

    if not args.yes:
        if plan["destructive"]:
            print(paint("\n  nothing done. re-run with --yes to go ahead.", "cyan"))
        else:
            print(paint("\n  nothing done. re-run with --yes.", "cyan"))
        return 0

    try:
        result = actions_mod.execute(plan, cfg)
    except actions_mod.ActionError as e:
        print(paint(f"    failed: {e}", "red"), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(paint(f"    {result['message']}", "green"))
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

    p = sub.add_parser("monitor", help="containers, images, volumes, venvs, units, ports")
    p.add_argument("--all-ports", action="store_true", help="include loopback/editor sockets")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("do", help="act on something the monitor showed")
    p.add_argument("action", help="an action name, or 'list'")
    p.add_argument("target", nargs="?", help="container name, port, or path")
    p.add_argument("--yes", action="store_true",
                   help="actually do it (without this you only get the preview)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_do)

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
