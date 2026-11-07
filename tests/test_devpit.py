"""devpit tests. Every test builds its own throwaway git repos and data dir under
pytest's tmp_path, so nothing here reads or writes the real ~/Coding or notes.

Run:  /usr/bin/python3 -m pytest -q   (from the repo root)
"""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path

import pytest

from devpit import cli, cockpit, identity, notes, repos


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def make_repo(root: Path, name: str, remote: str | None = None, commit: bool = True) -> Path:
    p = root / name
    p.mkdir(parents=True)
    git(p, "init", "-q", "-b", "main")
    if remote:
        git(p, "remote", "add", "origin", remote)
    if commit:
        (p / "README").write_text("hi\n")
        git(p, "add", "README")
        git(p, "commit", "-q", "-m", "first")
    return p


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A config pointing at tmp roots, also installed as the CLI's config file."""
    projects = tmp_path / "Coding" / "Projects"
    projects.mkdir(parents=True)
    cfg = {
        "roots": [str(tmp_path / "Coding" / "*"), str(tmp_path / "Coding" / "*" / "*")],
        "extra_repos": [],
        "data_dir": str(tmp_path / "data"),
        "stale_unpushed_days": 3, "stale_dirty_days": 7, "stash_pile": 3,
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))
    monkeypatch.setattr("devpit.config.CONFIG_PATH", cfg_file)
    return {"cfg": cfg, "projects": projects, "tmp": tmp_path}


# ------------------------------------------------------------------ identity
@pytest.mark.parametrize("url,expected", [
    ("git@github.com:Haise-727/rice.git", "github.com/Haise-727/rice"),
    ("https://github.com/end-4/dots-hyprland.git", "github.com/end-4/dots-hyprland"),
    ("ssh://git@host:2222/team/app.git", "host/team/app"),
    ("https://user@gitlab.com/a/b/", "gitlab.com/a/b"),
])
def test_normalise_remote(url, expected):
    assert identity.normalise_remote(url) == expected


# --------------------------------------------------------------------- repos
def test_parse_status_counts():
    out = "\n".join([
        "# branch.oid abc", "# branch.head main", "# branch.upstream origin/main",
        "# branch.ab +2 -1",
        "1 .M N... 100644 100644 100644 a b src/x.py",
        "1 M. N... 100644 100644 100644 a b src/y.py",
        "? new.txt", "u UU N... 1 2 3 4 h1 h2 h3 conflict.txt",
    ])
    st = repos._parse_status(out)
    assert (st["branch"], st["ahead"], st["behind"]) == ("main", 2, 1)
    assert (st["dirty"], st["staged"], st["untracked"], st["conflicted"]) == (1, 1, 1, 1)


def test_discover_two_levels(env):
    make_repo(env["projects"], "alpha")
    make_repo(env["tmp"] / "Coding" / "college", "beta")
    (env["projects"] / "not-a-repo").mkdir()
    names = [p.name for p in repos.discover(env["cfg"])]
    assert names == ["alpha", "beta"]


def test_inspect_dirty_and_no_remote(env):
    p = make_repo(env["projects"], "alpha")
    (p / "README").write_text("changed\n")
    (p / "extra").write_text("x\n")
    st = repos.inspect(p, env["cfg"])
    assert st["dirty"] == 1 and st["untracked"] == 1
    assert "no-remote" in st["flags"]


# ------------------------------------------------------------------- cockpit
def test_add_by_name_path_and_cwd(env, monkeypatch):
    a = make_repo(env["projects"], "alpha")
    b = make_repo(env["projects"], "beta", remote="git@github.com:me/beta.git")
    c = make_repo(env["projects"], "gamma")
    assert cockpit.add("alpha", env["cfg"])["added"]
    assert cockpit.add(str(b), env["cfg"])["added"]
    (c / "sub").mkdir()
    monkeypatch.chdir(c / "sub")
    assert cockpit.add(None, env["cfg"])["cockpit"]["name"] == "gamma"
    listed = cockpit.load(env["cfg"])["cockpits"]
    assert [x["name"] for x in listed] == ["alpha", "beta", "gamma"]
    assert listed[1]["id"] == "github.com_me_beta"


def test_add_twice_is_a_no_op_even_through_a_symlink(env):
    a = make_repo(env["projects"], "alpha")
    link = env["tmp"] / "link-to-alpha"
    link.symlink_to(a)
    assert cockpit.add("alpha", env["cfg"])["added"]
    again = cockpit.add(str(link), env["cfg"])
    assert again["added"] is False
    assert len(cockpit.load(env["cfg"])["cockpits"]) == 1


def test_root_reached_through_a_symlink_still_dedupes(env):
    """Discovery returns paths as globbed. When a root sits behind a symlink, a repo
    added by name must still match the same repo added by its real path."""
    make_repo(env["projects"], "alpha")
    linked = env["tmp"] / "linked-coding"
    linked.symlink_to(env["tmp"] / "Coding")
    cfg = {**env["cfg"], "roots": [str(linked / "*" / "*")]}
    by_name = cockpit.add("alpha", cfg)
    assert by_name["cockpit"]["path"] == str((env["projects"] / "alpha").resolve())
    assert cockpit.add(str(env["projects"] / "alpha"), cfg)["added"] is False


def test_default_launch_is_code_kitty_firefox(env):
    a = make_repo(env["projects"], "alpha")
    entry = cockpit.add("alpha", env["cfg"])["cockpit"]
    cmds = [x["cmd"][0] for x in entry["launch"]]
    assert cmds == ["code", "kitty", "firefox"]
    assert entry["launch"][0]["cmd"][1] == str(a.resolve())


def test_spec_appends_urls_to_firefox_only(env):
    make_repo(env["projects"], "alpha")
    cockpit.add("alpha", env["cfg"])
    cockpit.set_urls("alpha", ["https://a.example", "https://b.example"], env["cfg"])
    launch = cockpit.spec("alpha", env["cfg"])["launch"]
    assert launch[2]["cmd"][-2:] == ["https://a.example", "https://b.example"]
    assert "https://a.example" not in launch[0]["cmd"] + launch[1]["cmd"]


def test_spec_shell_form_is_quoted(env):
    p = make_repo(env["projects"], "with space")
    cockpit.add(str(p), env["cfg"])
    item = cockpit.spec("with space", env["cfg"])["launch"][0]
    import shlex
    assert shlex.split(item["shell"]) == item["cmd"]
    assert "'" in item["shell"]


def test_remove_by_name_id_and_path(env):
    for n in ("alpha", "beta", "gamma"):
        make_repo(env["projects"], n, remote=f"git@github.com:me/{n}.git" if n == "beta" else None)
        cockpit.add(n, env["cfg"])
    cockpit.remove("alpha", env["cfg"])
    cockpit.remove("github.com_me_beta", env["cfg"])
    cockpit.remove(str(env["projects"] / "gamma"), env["cfg"])
    assert cockpit.load(env["cfg"])["cockpits"] == []
    with pytest.raises(cockpit.CockpitError):
        cockpit.remove("alpha", env["cfg"])


def test_missing_repo_still_listed_and_removable(env):
    import shutil
    a = make_repo(env["projects"], "alpha")
    cockpit.add("alpha", env["cfg"])
    shutil.rmtree(a)
    row = cockpit.listing(env["cfg"])["cockpits"][0]
    assert row["exists"] is False and "git" not in row
    cockpit.remove("alpha", env["cfg"])


def test_ambiguous_name_asks_for_a_path(env):
    make_repo(env["projects"], "same")
    make_repo(env["tmp"] / "Coding" / "college", "same")
    with pytest.raises(cockpit.CockpitError, match="more than one"):
        cockpit.add("same", env["cfg"])


def test_not_a_repo_is_refused(env):
    d = env["projects"] / "plain"
    d.mkdir()
    with pytest.raises(cockpit.CockpitError, match="not a git repo"):
        cockpit.add(str(d), env["cfg"])


def test_candidates_exclude_added(env):
    make_repo(env["projects"], "alpha")
    make_repo(env["projects"], "beta")
    cockpit.add("alpha", env["cfg"])
    assert [c["name"] for c in cockpit.candidates(env["cfg"])["candidates"]] == ["beta"]


def test_corrupt_state_is_refused_not_overwritten(env):
    make_repo(env["projects"], "alpha")
    state = Path(env["cfg"]["data_dir"]) / "cockpits.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{not json")
    with pytest.raises(cockpit.CockpitError, match="will not overwrite"):
        cockpit.add("alpha", env["cfg"])
    assert state.read_text() == "{not json"


def test_save_leaves_no_temp_files(env):
    make_repo(env["projects"], "alpha")
    cockpit.add("alpha", env["cfg"])
    left = [p.name for p in Path(env["cfg"]["data_dir"]).iterdir()]
    assert left == ["cockpits.json"]


def test_listing_carries_git_state(env):
    a = make_repo(env["projects"], "alpha")
    (a / "README").write_text("changed\n")
    cockpit.add("alpha", env["cfg"])
    g = cockpit.listing(env["cfg"])["cockpits"][0]["git"]
    assert g["branch"] == "main" and g["dirty"] == 1


# ----------------------------------------------------------------------- CLI
def test_cli_json_round_trip(env, capsys):
    make_repo(env["projects"], "alpha")
    assert cli.main(["cockpit", "add", "alpha", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["added"] and out["cockpit"]["name"] == "alpha"
    assert cli.main(["cockpit", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cockpits"][0]["name"] == "alpha"


def test_cli_error_is_json_and_nonzero(env, capsys):
    assert cli.main(["cockpit", "remove", "nope", "--json"]) == 1
    assert "no cockpit" in json.loads(capsys.readouterr().out)["error"]


# --------------------------------------------------------------------- notes
def test_notes_add_and_parse(env):
    cfg = env["cfg"]
    notes.add("rid-1", "first thought", "alpha", cfg)
    notes.add("rid-1", "second thought", "alpha", cfg)
    notes.set_pinned("rid-1", "always run tests", "alpha", cfg)
    parsed = notes.parse(notes.read("rid-1", "alpha", cfg))
    assert parsed["pinned"].strip() == "always run tests"
    assert any("second thought" in e for e in parsed["entries"])
    assert any("first thought" in e for e in parsed["entries"])
