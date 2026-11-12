"""Auto-merge (runway.automerge): a green task merges into its base, re-tests when the base moved, aborts a conflict
with the branch intact and logs its paths, and takes one lock shared by every merge. Each repo is a throwaway under
tmp_path; the lock file is too."""
import fcntl, json, os, threading

import pytest

import runway
from test_git_flows import commit, git, make_repo, start_task


@pytest.fixture(autouse=True)
def lock_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(runway, "AUTOMERGE_LOCK", str(tmp_path / "automerge.lock"))


def checks_json(cmd="true", **config):
    """A .runway.json with one check, plus config keys such as mergeStrategy."""
    return json.dumps(dict({"checks": [{"name": "c", "cmd": cmd}]}, **config))


def tip(repo, ref="main"):
    return git(repo, "rev-parse", ref).strip()


def task_with_work(tmp_path, cmd="true", **config):
    """A repo whose checks are cmd and config is config, plus a task branch with one agent commit."""
    repo = make_repo(tmp_path)
    commit(repo, "config", {".runway.json": checks_json(cmd, **config)})
    wtp = start_task(repo, "t1")
    commit(wtp, "agent work", {"agent.txt": "agent\n"})
    return repo, wtp


def test_clean_merge_lands_on_base_and_removes_branch_and_worktree(tmp_path):
    repo = make_repo(tmp_path)
    wtp = start_task(repo, "t1")
    commit(wtp, "agent work", {"agent.txt": "agent\n"})
    before = tip(repo)
    r = runway.automerge("chunk-1", "t1")
    assert r["outcome"] == "merged", r
    assert tip(repo) != before
    assert git(repo, "rev-list", "--parents", "-n1", "main").split()[1:] == [before]  # squash: one parent, one commit
    assert git(repo, "show", "main:agent.txt") == "agent\n"
    assert not os.path.exists(wtp)
    assert git(repo, "branch", "--list", "runway-task/t1") == ""


def test_merge_strategy_from_config_keeps_the_branch_as_a_merge_commit(tmp_path):
    repo, _ = task_with_work(tmp_path, mergeStrategy="merge")
    before = tip(repo)
    assert runway.automerge("chunk-1", "t1")["outcome"] == "merged"
    parents = git(repo, "rev-list", "--parents", "-n1", "main").split()[1:]
    assert parents[0] == before and len(parents) == 2
    assert git(repo, "show", "main:agent.txt") == "agent\n"


def test_red_checks_stop_the_merge_and_keep_the_branch(tmp_path):
    repo, wtp = task_with_work(tmp_path, cmd="false")
    before = tip(repo)
    r = runway.automerge("chunk-1", "t1")
    assert r["outcome"] == "red", r
    assert tip(repo) == before
    assert os.path.isdir(wtp)
    assert git(repo, "rev-parse", "-q", "--verify", "refs/heads/runway-task/t1")


def test_base_moving_triggers_a_second_run_of_the_checks_before_merging(tmp_path):
    counter = tmp_path / "runs.txt"
    repo, _ = task_with_work(tmp_path, cmd="echo run >> %s" % counter)
    commit(repo, "main moves on", {"main-only.txt": "main\n"})
    r = runway.automerge("chunk-1", "t1")
    assert r["outcome"] == "merged", r
    assert counter.read_text().count("run") == 2  # once for the gate, once after the base moved
    assert git(repo, "show", "main:main-only.txt") == "main\n"
    assert git(repo, "show", "main:agent.txt") == "agent\n"


def test_base_moving_into_a_red_check_stops_the_merge_and_leaves_base_alone(tmp_path):
    repo, wtp = task_with_work(tmp_path, cmd="test ! -f main-only.txt")  # green until main brings the file in
    commit(repo, "main moves on", {"main-only.txt": "main\n"})
    before = tip(repo)
    r = runway.automerge("chunk-1", "t1")
    assert r["outcome"] == "red", r
    assert tip(repo) == before
    assert os.path.isdir(wtp)


def test_base_moving_with_rebase_sync_still_merges_the_branch_on_top(tmp_path):
    repo, _ = task_with_work(tmp_path, syncStrategy="rebase")
    commit(repo, "main moves on", {"main-only.txt": "main\n"})
    assert runway.automerge("chunk-1", "t1")["outcome"] == "merged"
    assert git(repo, "show", "main:main-only.txt") == "main\n"
    assert git(repo, "show", "main:agent.txt") == "agent\n"


def test_conflict_aborts_leaves_the_branch_intact_and_logs_the_paths(tmp_path):
    repo = make_repo(tmp_path)
    wtp = start_task(repo, "t1")
    commit(wtp, "agent work", {"a.txt": "agent version\n", "agent.txt": "agent\n"})
    commit(repo, "main edits a too", {"a.txt": "main version\n"})
    branch_before, main_before = tip(repo, "runway-task/t1"), tip(repo)
    r = runway.automerge("chunk-1", "t1")
    assert r["outcome"] == "conflict", r
    assert r["paths"] == ["a.txt"]
    assert tip(repo, "runway-task/t1") == branch_before
    assert tip(repo) == main_before
    assert git(wtp, "status", "--porcelain") == ""  # no conflict markers, no half-finished merge
    assert os.path.isdir(wtp)
    entry = runway.read_failures(n=1)[0]
    assert entry["phase"] == "merge" and entry["chunk"] == "chunk-1" and entry["branch"] == "runway-task/t1"
    assert entry["paths"] == ["a.txt"]
    assert "a.txt" in entry["stderr_tail"]


def test_a_merge_that_finds_the_lock_taken_changes_nothing(tmp_path):
    repo, wtp = task_with_work(tmp_path)
    before = tip(repo)
    holder = open(runway.AUTOMERGE_LOCK, "a")
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        r = runway.automerge("chunk-1", "t1", wait=0)
        assert r["outcome"] == "locked", r
        assert tip(repo) == before
        assert os.path.isdir(wtp)
    finally:
        holder.close()
    assert runway.automerge("chunk-1", "t1")["outcome"] == "merged"


def test_a_merge_waits_for_the_lock_when_it_can(tmp_path):
    repo, _ = task_with_work(tmp_path)
    holder = open(runway.AUTOMERGE_LOCK, "a")
    fcntl.flock(holder, fcntl.LOCK_EX)
    release = threading.Timer(0.5, holder.close)  # closing the file releases the flock
    release.start()
    try:
        assert runway.automerge("chunk-1", "t1", wait=10)["outcome"] == "merged"
    finally:
        release.cancel()
        holder.close()
