"""Stable identity for a repository.

Decided 2026-09-03: use the git remote when there is one, else a hash of the path.
A remote survives the directory being moved or renamed, and matches the same repo
cloned on another machine - which is what makes notes and time tracking follow the
project rather than the folder.

The id doubles as a filename (notes are one .md per repo), so it has to be
filesystem-safe and ideally still readable by a human browsing the directory.
"""
from __future__ import annotations
import hashlib, re, subprocess
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def normalise_remote(url: str) -> str:
    """git@github.com:Haise-727/rice.git -> github.com/Haise-727/rice"""
    url = url.strip()
    url = re.sub(r"^[a-z+]+://", "", url)          # https://, ssh://
    url = re.sub(r"^[^@/]+@", "", url)             # user@
    # scp-style "host:path" becomes "host/path"; a real ":port" is dropped, so the
    # same repo reached with and without an explicit port gets one identity.
    head, sep, tail = url.partition("/")
    if ":" in head:
        host, _, after = head.partition(":")
        url = f"{host}/{after}" if after and not after.isdigit() else host
        url = url + sep + tail if tail else url
    url = re.sub(r"\.git$", "", url)
    return url.strip("/")


def remote_of(path: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return normalise_remote(r.stdout)


def repo_id(path: Path, remote: str | None = None) -> str:
    """Filesystem-safe, stable, and readable where possible."""
    if remote:
        return _SAFE.sub("_", remote)
    # No remote: fall back to the path, hashed so a move is at least detectable
    # rather than silently merging two different repos.
    h = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:10]
    return f"{_SAFE.sub('_', path.name)}-{h}"
