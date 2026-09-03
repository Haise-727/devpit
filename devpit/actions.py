"""Actions, and the verification that has to precede the destructive ones.

Every action is two phases of one command. Without --yes you get a *preview*: what
exactly would be affected, and nothing happens. With --yes it runs. The GUI uses
the same two calls, so the confirmation it shows is the same text the terminal
shows - there is no second, looser path to a destructive operation.

Failures report what actually went wrong. Stopping a container that already died
says so; a permission problem names the permission. No silent failures and no
generic "something went wrong".
"""
from __future__ import annotations
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import config as cfg_mod
from .monitor import _run


class ActionError(Exception):
    """Something the user should read, not a stack trace."""


def _proc_info(pid: int) -> dict:
    """Name, full command line and age - what you need to decide whether to kill it."""
    info = {"pid": pid, "name": "", "cmdline": "", "age_s": None, "user_owned": False}
    try:
        info["name"] = Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        raise ActionError(f"no process with pid {pid} (it may have already exited)")
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        info["cmdline"] = raw.replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        pass
    try:
        st = os.stat(f"/proc/{pid}")
        info["age_s"] = int(time.time() - st.st_ctime)
        info["user_owned"] = st.st_uid == os.getuid()
    except OSError:
        pass
    return info


def _human_age(seconds: int | None) -> str:
    if seconds is None:
        return "unknown age"
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


# --------------------------------------------------------------------- previews

def preview_container(action: str, name: str) -> dict:
    code, out, err = _run(["docker", "inspect", "--format",
                           "{{.Name}}\t{{.Config.Image}}\t{{.State.Status}}\t{{.State.StartedAt}}\t{{len .Mounts}}",
                           name])
    if code != 0:
        raise ActionError(err.strip().splitlines()[-1] if err.strip()
                          else f"no container named {name}")
    cname, image, status, started, mounts = (out.strip().split("\t") + [""] * 5)[:5]
    warnings = []
    if action == "stop" and status != "running":
        warnings.append(f"already {status} - stopping will do nothing")
    if action == "start" and status == "running":
        warnings.append("already running")
    if action == "remove":
        if status == "running":
            warnings.append("it is running and will be force-stopped first")
        if mounts and mounts != "0":
            warnings.append(f"has {mounts} mount(s); named volumes are NOT deleted")
    return {
        "action": f"container.{action}",
        "target": cname.lstrip("/"),
        "_running": status == "running",
        "detail": f"image {image}, currently {status}, started {started[:19] or 'never'}",
        "warnings": warnings,
        "destructive": action in ("stop", "remove", "restart"),
    }


def preview_port_kill(port: int) -> dict:
    code, out, _ = _run(["ss", "-tlnpH"])
    if code != 0:
        raise ActionError("could not read listening sockets (is iproute2 installed?)")
    import re
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        if not parts[3].rpartition(":")[2] == str(port):
            continue
        m = re.search(r'\("([^"]+)",pid=(\d+)', line)
        if not m:
            raise ActionError(f"port {port} is listening but its owner is not visible "
                              "(it belongs to another user)")
        info = _proc_info(int(m.group(2)))
        warn = []
        if not info["user_owned"]:
            warn.append("owned by another user - killing it will need privileges you do not have")
        if info["name"] in ("systemd", "init"):
            warn.append("this is a system process; refusing")
        return {
            "action": "port.kill",
            "target": f"pid {info['pid']} on port {port}",
            "detail": f"{info['name']} — {info['cmdline'][:160] or '(no cmdline)'} — "
                      f"running {_human_age(info['age_s'])}",
            "warnings": warn,
            "destructive": True,
            "_pid": info["pid"],
            "_blocked": info["name"] in ("systemd", "init"),
        }
    raise ActionError(f"nothing is listening on port {port}")


def preview_venv_remove(path: str, cfg: dict | None = None) -> dict:
    cfg = cfg or cfg_mod.load()
    d = Path(path).expanduser().resolve()
    # Two guards, because this deletes a directory tree
    if not (d / "pyvenv.cfg").is_file():
        raise ActionError(f"{d} is not a virtualenv (no pyvenv.cfg) - refusing")
    roots = [cfg_mod.expand(r).parent for r in cfg["roots"]]
    if not any(str(d).startswith(str(r)) for r in roots):
        raise ActionError(f"{d} is outside the configured roots - refusing")
    code, out, _ = _run(["du", "-sm", str(d)], timeout=60)
    mb = out.split("\t", 1)[0] if code == 0 and out.strip() else "?"
    return {
        "action": "venv.remove",
        "target": str(d),
        "detail": f"deletes {mb} MB; recreate with python -m venv",
        "warnings": ["the whole directory tree is removed"],
        "destructive": True,
    }


def preview_docker_prune() -> dict:
    code, out, err = _run(["docker", "system", "df", "--format", "json"], timeout=30)
    if code != 0:
        raise ActionError(err.strip() or "docker is not responding")
    import json as _json
    lines = []
    for raw in out.splitlines():
        if not raw.strip():
            continue
        try:
            row = _json.loads(raw)
        except _json.JSONDecodeError:
            continue
        if row.get("Reclaimable"):
            lines.append(f"{row.get('Type')}: {row.get('Reclaimable')}")
    return {
        "action": "docker.prune",
        "target": "stopped containers, dangling images, unused networks",
        "detail": "; ".join(lines) or "nothing reclaimable",
        "warnings": ["named volumes are not touched (docker prune does not remove them)"],
        "destructive": True,
    }


# --------------------------------------------------------------------- execution

def execute(plan: dict, cfg: dict | None = None) -> dict:
    action = plan["action"]

    if plan.get("_blocked"):
        raise ActionError("refusing: that is a system process")

    if action.startswith("container."):
        # docker's subcommand is not always the word we expose: "remove" is `docker rm`.
        verb = action.split(".", 1)[1]
        DOCKER_VERB = {"start": "start", "stop": "stop",
                       "restart": "restart", "remove": "rm"}
        cmd = DOCKER_VERB.get(verb)
        if cmd is None:
            raise ActionError(f"unknown container action: {verb}")
        argv = ["docker", cmd, plan["target"]]
        # The preview promises a running container is force-stopped before removal;
        # `docker rm` refuses without -f, so honour what was shown.
        if cmd == "rm" and plan.get("_running"):
            argv.insert(2, "-f")
        code, out, err = _run(argv, timeout=60)
        if code != 0:
            raise ActionError(err.strip().splitlines()[-1] if err.strip()
                              else f"docker {cmd} failed with status {code}")
        return {"ok": True, "message": f"{plan['target']}: {verb} done"}

    if action == "port.kill":
        pid = plan["_pid"]
        try:
            os.kill(pid, 15)                       # SIGTERM first
        except ProcessLookupError:
            raise ActionError(f"pid {pid} had already exited")
        except PermissionError:
            raise ActionError(f"not permitted to signal pid {pid} (owned by another user)")
        for _ in range(20):                        # up to 2s for a clean exit
            time.sleep(0.1)
            if not Path(f"/proc/{pid}").exists():
                return {"ok": True, "message": f"pid {pid} exited after SIGTERM"}
        return {"ok": True, "message": f"SIGTERM sent to pid {pid}; it is still running "
                                       "(devpit does not escalate to SIGKILL for you)"}

    if action == "venv.remove":
        d = Path(plan["target"])
        if not (d / "pyvenv.cfg").is_file():       # re-check at execution time
            raise ActionError(f"{d} no longer looks like a virtualenv - refusing")
        try:
            shutil.rmtree(d)
        except OSError as e:
            raise ActionError(f"could not remove {d}: {e}")
        return {"ok": True, "message": f"removed {d}"}

    if action == "docker.prune":
        code, out, err = _run(["docker", "system", "prune", "-f"], timeout=300)
        if code != 0:
            raise ActionError(err.strip() or "docker prune failed")
        return {"ok": True, "message": out.strip().splitlines()[-1] if out.strip() else "pruned"}

    raise ActionError(f"unknown action: {action}")


def plan_for(action: str, target: str, cfg: dict | None = None) -> dict:
    if action.startswith("container."):
        return preview_container(action.split(".", 1)[1], target)
    if action == "port.kill":
        try:
            return preview_port_kill(int(target))
        except ValueError:
            raise ActionError(f"'{target}' is not a port number")
    if action == "venv.remove":
        return preview_venv_remove(target, cfg)
    if action == "docker.prune":
        return preview_docker_prune()
    raise ActionError(f"unknown action: {action}")


ACTIONS = {
    "container.start":   "start a stopped container",
    "container.stop":    "stop a running container",
    "container.restart": "restart a container",
    "container.remove":  "delete a container (named volumes are kept)",
    "port.kill":         "SIGTERM whatever is listening on a port",
    "venv.remove":       "delete a virtualenv directory",
    "docker.prune":      "remove stopped containers, dangling images, unused networks",
}
