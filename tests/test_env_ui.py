"""What the page shows of a task's environment: env_record (state, ports, names and whether each is set, the setup log with
secret values blanked), env-info for the project form, and retry-setup, which runs a failed setup again in the same worktree
and then starts the agent. Real git runs in temp repos; launch is stubbed, so no agent starts. Secret values are sentinels
that must never appear in anything the page can read."""
import json, os

import pytest

import runway
from test_autopilot import make_repo, save, statuses, task
from test_task_env import read_order, run_autopilot, set_config, write_env_file, write_fake

SECRET = "sentinel-s3cr3t-value"


def run_folder(repo, key):
    return runway._ap_dir(key)


def test_a_failed_setup_is_a_failed_environment_with_its_log_and_names_but_no_values(tmp_path):
    repo = make_repo(tmp_path)
    set_config(repo, {"setup": "echo installing; echo broke; exit 3", "env": ["API_KEY"], "ports": 2})
    write_env_file(repo, "API_KEY=%s\n" % SECRET)
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt"))
    rec = runway.env_record(run_folder(repo, "a"), "a")
    assert rec["state"] == "failed"
    assert rec["setup"]["rc"] == 3 and "[setup exit 3]" in rec["setup"]["log"] and "broke" in rec["setup"]["log"]
    assert rec["names"] == [{"name": "API_KEY", "set": True}]
    assert rec["ports"]["count"] == 2 and rec["ports"]["base"] >= runway.PORT_FIRST
    assert SECRET not in json.dumps(rec)
    assert read_order(tmp_path / "order.txt") == []  # the agent never ran


def test_a_setup_that_echoes_a_secret_shows_it_blanked_in_the_log_and_the_blocker(tmp_path):
    repo = make_repo(tmp_path)
    set_config(repo, {"setup": "echo token=$API_KEY; exit 3", "env": ["API_KEY"]})
    write_env_file(repo, "API_KEY=%s\n" % SECRET)
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt"))
    rec = runway.env_record(run_folder(repo, "a"), "a")
    assert "token=••••" in rec["setup"]["log"]
    assert SECRET not in json.dumps(rec)
    assert SECRET not in json.dumps(runway.all_envs())
    # the blocker the agent's card shows is built from the same redacted tail
    assert SECRET not in json.dumps(runway.chunk_status("ap-a")["tasks"]["a"])


def test_a_missing_variable_is_a_failed_environment_with_no_setup_log(tmp_path):
    repo = make_repo(tmp_path)
    set_config(repo, {"setup": "echo never", "env": ["NOT_SET_ANYWHERE"]})
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt"))
    rec = runway.env_record(run_folder(repo, "a"), "a")
    assert rec["state"] == "failed" and rec["setup"] is None
    assert "NOT_SET_ANYWHERE" in rec["detail"]


def test_a_setup_still_running_is_preparing(tmp_path):
    d = str(tmp_path / "run")
    os.makedirs(d)
    runway.write_json_atomic(os.path.join(d, runway.ENV_SPEC), {"task": "a", "repo": str(tmp_path / "site"), "names": [], "ports": None})
    with open(os.path.join(d, "a.setup.log"), "w") as f:
        f.write("npm ci\n")
    assert runway.env_record(d, "a")["state"] == "preparing"


def test_a_project_without_environment_has_no_record(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt"))
    assert runway.env_record(run_folder(repo, "a"), "a") is None
    assert all(e["task"] != "a" for e in runway.all_envs())


def test_a_ready_environment_with_no_setup_command_is_ready(tmp_path):
    repo = make_repo(tmp_path)
    set_config(repo, {"env": ["API_KEY"]})
    write_env_file(repo, "API_KEY=%s\n" % SECRET)
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt", expect_secret=SECRET))
    rec = runway.env_record(run_folder(repo, "a"), "a")
    assert rec["state"] == "ready" and rec["names"] == [{"name": "API_KEY", "set": True}]


def test_env_info_gives_names_and_whether_each_is_set_never_values(tmp_path, capsys):
    repo = make_repo(tmp_path)
    set_config(repo, {"setup": "true", "env": ["API_KEY", "OTHER"], "ports": 3})
    write_env_file(repo, "API_KEY=%s\n" % SECRET)
    info = runway.env_info(repo)
    assert info["names"] == [{"name": "API_KEY", "set": True}, {"name": "OTHER", "set": False}]
    assert info["ports"] == 3 and info["setup"] is True and info["exists"] is True
    assert info["file"] == runway.env_file(repo)
    assert SECRET not in json.dumps(info)
    runway.main(["env-info", repo])
    out = capsys.readouterr().out
    assert out.startswith("@@ENVINFO ") and SECRET not in out


def test_env_info_reports_a_bad_config_as_an_error_line(tmp_path, capsys):
    repo = make_repo(tmp_path)
    set_config(repo, {"env": "API_KEY"})
    runway.main(["env-info", repo])
    assert capsys.readouterr().out.startswith("@@ERR ")


def test_retry_setup_runs_the_setup_again_in_the_same_worktree_then_starts_the_agent(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    flag = tmp_path / "fixed"
    set_config(repo, {"setup": "test -f %s || { echo still-broken; exit 3; }" % flag})
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt"))
    assert runway.env_record(run_folder(repo, "a"), "a")["state"] == "failed"
    worktree = os.path.join(repo, ".claude", "worktrees", "runway-a")
    flag.write_text("ok\n")
    launched = []
    monkeypatch.setattr(runway, "launch", lambda ch: launched.append(ch) or "launched")
    assert runway.retry_setup(repo, "a") == "launched"
    rec = runway.env_record(run_folder(repo, "a"), "a")
    assert rec["state"] == "ready" and rec["setup"]["rc"] == 0
    assert launched == ["ap-a"]
    assert os.path.isdir(worktree)  # the same worktree, not a new one
    assert os.path.exists(os.path.join(run_folder(repo, "a"), "run.sh"))


def test_retry_setup_fails_again_with_the_new_log_when_setup_still_fails(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    set_config(repo, {"setup": "echo still-broken; exit 3"})
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt"))
    launched = []
    monkeypatch.setattr(runway, "launch", lambda ch: launched.append(ch) or "launched")
    with pytest.raises(runway.AutopilotError, match="failed"):
        runway.retry_setup(repo, "a")
    assert launched == []
    assert runway.env_record(run_folder(repo, "a"), "a")["state"] == "failed"


def test_retry_setup_refuses_a_task_without_a_failed_environment(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    set_config(repo, {"setup": "true"})
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt"))
    monkeypatch.setattr(runway, "launch", lambda ch: pytest.fail("must not launch"))
    with pytest.raises(runway.AutopilotError, match="no failed environment"):
        runway.retry_setup(repo, "a")


def test_retry_setup_command_prints_an_error_line_instead_of_a_traceback(tmp_path, capsys):
    repo = make_repo(tmp_path)
    runway.main(["retry-setup", repo, "nothing-here"])
    assert capsys.readouterr().out.startswith("@@ERR ")
