"""Merge-train CLI: run, list, failures, retry, prune, through runway.main() so the dispatch and exit codes are the real ones.
Each test uses a temp repo with one task worktree; --dry-run is checked to change nothing in the repo or the Runs view."""
import json, os, shutil

import pytest

import runway
from test_git_flows import commit, git, make_repo, start_task


def fake_project(tmp_path, checks=None):
    """A repo whose main has these checks in .runway.json (or none), and task t1 in chunk c1 with its worktree."""
    repo = make_repo(tmp_path, checks=checks is not None)
    if checks is not None:
        commit(repo, "checks config", {".runway.json": json.dumps({"checks": checks})})
    start_task(repo, "t1", chunk="c1")
    return repo


def wt_path(repo, tid="t1"):
    return os.path.join(repo, ".claude", "worktrees", "wt-" + tid)


def cli(capsys, *argv):
    """Run one command. Returns (exit code, stdout, stderr)."""
    with pytest.raises(SystemExit) as e:
        runway.main(list(argv))
    out = capsys.readouterr()
    return e.value.code, out.out, out.err


def snapshot(repo):
    """Everything --dry-run must leave alone: worktree list, branches, status and the Runs view."""
    runs = os.path.join(runway.RUNS, "c1")
    return (git(repo, "worktree", "list", "--porcelain"), git(repo, "for-each-ref", "refs/heads"),
            git(repo, "status", "--porcelain"), sorted(os.listdir(runs)) if os.path.isdir(runs) else [],
            os.path.exists(runway.FAILURES))


@pytest.mark.parametrize("name", ["run", "list", "failures", "retry", "prune"])
def test_every_subcommand_has_help_with_examples_and_exit_codes(capsys, name):
    code, out, _ = cli(capsys, name, "-h")
    assert code == 0
    assert "examples:" in out and "runway.py " + name in out
    assert "exit codes:" in out and "3  nothing to run" in out


def test_run_dry_run_prints_the_git_and_test_commands_and_touches_nothing(tmp_path, capsys):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "echo unit ok"}])
    before = snapshot(repo)
    code, out, _ = cli(capsys, "run", "c1", "t1", "--dry-run")
    assert code == 0
    assert "$ (cd %s && /bin/bash -c 'echo unit ok')  # check unit, timeout 600s" % wt_path(repo) in out
    assert "@@DRYRUN 1 commands, nothing run" in out
    assert "worktree add" not in out  # the worktree already exists, so no git command is listed
    assert snapshot(repo) == before


def test_run_dry_run_lists_the_worktree_creation_when_it_is_missing(tmp_path, capsys):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "true"}])
    git(repo, "worktree", "remove", "--force", wt_path(repo))
    before = snapshot(repo)
    code, out, _ = cli(capsys, "run", "c1", "t1", "--dry-run")
    assert code == 0
    assert "$ git -C %s worktree add -q %s runway-task/t1" % (repo, wt_path(repo)) in out
    assert "@@DRYRUN 2 commands, nothing run" in out
    assert not os.path.exists(wt_path(repo))  # nothing was created
    assert snapshot(repo) == before


def test_run_dry_run_without_checks_exits_3(tmp_path, capsys):
    fake_project(tmp_path, checks=None)
    code, out, _ = cli(capsys, "run", "c1", "t1", "--dry-run")
    assert code == 3 and "nothing would run" in out


def test_run_happy_path_exits_0_when_green(tmp_path, capsys):
    fake_project(tmp_path, [{"name": "unit", "cmd": "echo unit ok"}])
    code, out, _ = cli(capsys, "run", "c1", "t1")
    assert code == 0 and "@@CHECKS green c1 t1" in out
    assert json.load(open(os.path.join(runway.RUNS, "c1", "t1.checks.json")))["state"] == "green"


def test_run_red_exits_1_and_records_the_failure(tmp_path, capsys):
    fake_project(tmp_path, [{"name": "unit", "cmd": "exit 4"}])
    code, out, _ = cli(capsys, "run", "c1", "t1")
    assert code == 1 and "@@CHECKS red c1 t1" in out and "reason: exit 4 (check unit)" in out
    assert [r["state"] for r in runway.read_check_failures()] == ["red"]


def test_run_without_checks_exits_3(tmp_path, capsys):
    fake_project(tmp_path, checks=None)
    code, out, _ = cli(capsys, "run", "c1", "t1")
    assert code == 3 and "@@CHECKS none c1 t1" in out


def test_unknown_task_is_a_usage_error_exit_2(tmp_path, capsys):
    fake_project(tmp_path, [{"name": "unit", "cmd": "true"}])
    code, _, err = cli(capsys, "run", "c1", "no-such-task")
    assert code == 2 and "@@ERR no worktree task no-such-task in chunk c1" in err


def test_unsafe_ids_are_refused_before_anything_is_read(tmp_path, capsys):
    fake_project(tmp_path, [{"name": "unit", "cmd": "true"}])
    code, _, err = cli(capsys, "run", "../c1", "t1")
    assert code == 2 and "chunk '../c1'" in err


def test_list_shows_the_active_task_worktree(tmp_path, capsys):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "true"}])
    code, out, _ = cli(capsys, "list", "--repo", repo)
    assert code == 0
    assert out.split()[:3] == ["active", "t1", wt_path(repo)]


def test_list_with_no_task_worktrees_says_so(tmp_path, capsys):
    repo = make_repo(tmp_path)
    code, out, _ = cli(capsys, "list", "--repo", repo)
    assert code == 0 and out.strip() == "No task worktrees."


def test_list_outside_a_repo_is_a_usage_error(tmp_path, capsys):
    code, _, err = cli(capsys, "list", "--repo", str(tmp_path))
    assert code == 2 and "not inside a git repository" in err


def test_prune_dry_run_reports_stale_records_and_changes_nothing(tmp_path, capsys):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "true"}])
    shutil.rmtree(wt_path(repo))
    code, out, _ = cli(capsys, "prune", "--repo", repo, "--dry-run")
    assert code == 0
    assert "# stale: t1" in out and "$ git -C %s worktree prune -v" % repo in out
    assert "@@DRYRUN 1 stale record, nothing pruned" in out
    assert "t1" in git(repo, "worktree", "list", "--porcelain")  # the record is still there


def test_prune_drops_stale_records_then_list_is_clean(tmp_path, capsys):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "true"}])
    shutil.rmtree(wt_path(repo))
    code, out, _ = cli(capsys, "list", "--repo", repo)
    assert code == 0 and out.split()[0] == "stale"
    code, out, _ = cli(capsys, "prune", "--repo", repo)
    assert code == 0 and "@@PRUNED 1" in out
    code, out, _ = cli(capsys, "list", "--repo", repo)
    assert out.strip() == "No task worktrees."


def test_failures_lists_recent_red_runs_and_filters_by_chunk(tmp_path, capsys):
    fake_project(tmp_path, [{"name": "unit", "cmd": "exit 2"}])
    code, out, _ = cli(capsys, "failures")
    assert code == 0 and "No check failures recorded." in out
    cli(capsys, "run", "c1", "t1")
    code, out, _ = cli(capsys, "failures", "-n", "5", "--chunk", "c1")
    assert code == 0 and "c1/t1  red  check unit, exit 2" in out and "log: " in out
    code, out, _ = cli(capsys, "failures", "--chunk", "other")
    assert code == 0 and "No check failures recorded." in out


def test_failures_rejects_a_zero_count(tmp_path, capsys):
    code, _, err = cli(capsys, "failures", "-n", "0")
    assert code == 2 and "-n must be at least 1" in err


def test_retry_without_a_recorded_failure_exits_2(tmp_path, capsys):
    fake_project(tmp_path, [{"name": "unit", "cmd": "true"}])
    code, _, err = cli(capsys, "retry", "c1", "t1")
    assert code == 2 and "no recorded failure for c1/t1, so nothing to retry" in err


def test_retry_runs_again_after_the_fix_and_goes_green(tmp_path, capsys):
    repo = fake_project(tmp_path, [{"name": "marker", "cmd": "test -f fixed.txt"}])
    assert cli(capsys, "run", "c1", "t1")[0] == 1
    code, out, _ = cli(capsys, "retry", "c1", "t1", "--dry-run")
    assert code == 0 and "# last failure: red" in out and "@@DRYRUN 1 commands" in out
    assert json.load(open(os.path.join(runway.RUNS, "c1", "t1.checks.json")))["state"] == "red"  # dry-run ran nothing
    open(os.path.join(wt_path(repo), "fixed.txt"), "w").write("ok\n")
    code, out, _ = cli(capsys, "retry", "c1", "t1")
    assert code == 0 and "@@CHECKS green c1 t1" in out
