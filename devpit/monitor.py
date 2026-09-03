"""What a dev machine accumulates: containers, images, volumes, virtualenvs,
failed units and listening ports.

Every source is independently failable. Docker being absent, stopped, or refusing
permission degrades that one section and lands in errors[] - it never takes the
document down, because the rest is still worth showing.
"""
from __future__ import annotations
import glob
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg_mod

# Processes whose loopback sockets are their own business, not a service you ran.
# Without this the port list is unreadable: measured here, five of the first five
# entries from `ss -tlnp` were VSCode's own.
NOISE_PROCS = {"code", "code-oss", "codium", "firefox", "chrome", "chromium",
               "brave", "zen", "electron", "discord", "spotify", "obsidian",
               "steam", "dbus-daemon", "pipewire", "wireplumber"}
# Ports a person actually starts things on
DEV_PORTS = {3000, 3001, 4000, 4200, 5000, 5173, 5432, 6379, 7860, 8000, 8001,
             8080, 8081, 8443, 8888, 9000, 27017}


def _run(cmd: list[str], timeout: float = 15) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out"
    except OSError as e:
        return 1, "", str(e)


def _docker_json_lines(out: str) -> list[dict]:
    """docker --format json emits one JSON object per line."""
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def docker(errors: list) -> dict:
    if shutil.which("docker") is None:
        errors.append({"source": "docker", "message": "docker is not installed"})
        return {"available": False}

    code, out, err = _run(["docker", "ps", "-a", "--format", "json"])
    if code != 0:
        msg = err.strip().splitlines()[-1] if err.strip() else "docker is not responding"
        errors.append({"source": "docker", "message": msg})
        return {"available": False, "reason": msg}

    containers = []
    for c in _docker_json_lines(out):
        state = (c.get("State") or "").lower()
        containers.append({
            "id": c.get("ID", "")[:12], "name": c.get("Names", ""),
            "image": c.get("Image", ""), "state": state,
            "status": c.get("Status", ""), "ports": c.get("Ports", ""),
            # Anything not running is space you have forgotten about
            "reclaimable": state in ("exited", "created", "dead"),
        })

    images = []
    code, out, _ = _run(["docker", "images", "--format", "json"])
    if code == 0:
        for i in _docker_json_lines(out):
            dangling = i.get("Repository") == "<none>"
            images.append({
                "id": i.get("ID", "")[:12],
                "repo": i.get("Repository", ""), "tag": i.get("Tag", ""),
                "size": i.get("Size", ""), "dangling": dangling,
                "reclaimable": dangling,
            })

    volumes = []
    code, out, _ = _run(["docker", "volume", "ls", "--format", "json"])
    if code == 0:
        for v in _docker_json_lines(out):
            volumes.append({
                "name": v.get("Name", ""), "driver": v.get("Driver", ""),
                # The one thing here with a real path on disk
                "path": v.get("Mountpoint") or f"/var/lib/docker/volumes/{v.get('Name','')}/_data",
            })

    usage = {}
    code, out, _ = _run(["docker", "system", "df", "--format", "json"], timeout=30)
    if code == 0:
        for row in _docker_json_lines(out):
            key = (row.get("Type") or "").lower().replace(" ", "_")
            if key:
                usage[key] = {"size": row.get("Size"), "reclaimable": row.get("Reclaimable")}

    return {
        "available": True,
        "containers": containers,
        "running": sum(1 for c in containers if c["state"] == "running"),
        "stopped": sum(1 for c in containers if c["reclaimable"]),
        "images": images,
        "volumes": volumes,
        "usage": usage,
    }


# Conventional virtualenv directory names. Checked explicitly rather than globbed,
# because glob('*') does not match dotfiles - which silently found zero venvs on a
# machine that had two, both named .venv.
VENV_DIRS = (".venv", "venv", "env", ".env")


def venvs(cfg: dict, errors: list) -> list[dict]:
    """Virtualenvs under the configured roots, found by their pyvenv.cfg."""
    found = []
    candidates: list[Path] = []
    for root in cfg["roots"]:
        for project in glob.glob(str(cfg_mod.expand(root))):
            candidates += [Path(project) / n for n in VENV_DIRS]
    for extra in cfg["extra_repos"]:
        candidates += [cfg_mod.expand(extra) / n for n in VENV_DIRS]

    now = time.time()
    for d in candidates:
        cfgfile = d / "pyvenv.cfg"
        if not cfgfile.is_file():
            continue
        version = ""
        try:
            for line in cfgfile.read_text().splitlines():
                if line.lower().startswith("version"):
                    version = line.split("=", 1)[1].strip()
                    break
        except OSError:
            pass
        code, out, _ = _run(["du", "-sm", str(d)], timeout=60)
        mb = 0
        if code == 0 and out.strip():
            try:
                mb = int(out.split("\t", 1)[0])
            except ValueError:
                pass
        try:
            mtime = d.stat().st_mtime
        except OSError:
            mtime = now
        parent = d.parent
        orphaned = not (parent / ".git").exists()
        stale_days = int((now - mtime) / 86400)
        found.append({
            "path": str(d), "project": parent.name, "python": version,
            "mb": mb, "stale_days": stale_days,
            "orphaned": orphaned,
            "stale": stale_days > 90,
            "reclaimable": orphaned or stale_days > 90,
        })
    return sorted(found, key=lambda v: v["mb"], reverse=True)


def failed_units(errors: list) -> list[dict]:
    code, out, _ = _run(["systemctl", "--user", "list-units", "--state=failed",
                         "--no-legend", "--plain"])
    if code != 0:
        return []
    units = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if parts and parts[0].endswith(".service") or (parts and "." in parts[0]):
            units.append({"unit": parts[0],
                          "description": parts[4] if len(parts) > 4 else ""})
    return units


def ports(errors: list) -> list[dict]:
    """Listening TCP sockets, classified so the useful ones are not buried."""
    if shutil.which("ss") is None:
        errors.append({"source": "ports", "message": "ss is not installed (iproute2)"})
        return []
    code, out, err = _run(["ss", "-tlnpH"])
    if code != 0:
        errors.append({"source": "ports", "message": err.strip() or "ss failed"})
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        addr, _, port_s = local.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            continue
        m = re.search(r'\("([^"]+)",pid=(\d+)', line)
        proc, pid = (m.group(1), int(m.group(2))) if m else ("", None)
        loopback = addr.strip("[]") in ("127.0.0.1", "::1")
        noise = loopback and proc in NOISE_PROCS
        rows.append({
            "port": port, "addr": addr.strip("[]"), "proc": proc, "pid": pid,
            "loopback": loopback,
            "interesting": (not loopback) or port in DEV_PORTS,
            "noise": noise,
        })
    return sorted(rows, key=lambda r: (not r["interesting"], r["port"]))


def scan(cfg: dict | None = None) -> dict:
    cfg = cfg or cfg_mod.load()
    started = time.time()
    errors: list[dict] = []
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "docker": docker(errors),
        "venvs": venvs(cfg, errors),
        "units_failed": failed_units(errors),
        "ports": ports(errors),
        "errors": errors,
    }
    data["duration_ms"] = int((time.time() - started) * 1000)
    return data
