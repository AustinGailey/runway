"""The autopilot loop end to end, against a scripted fake agent (see autopilot_fake.py). Each task's run is told what to do
(green, red, conflict or timeout), and the real merge gate decides the outcome: a red change is one the project's "tests"
check rejects, a timeout is one the "slow" check hangs on, and a conflict is one whose README edit cannot be synced onto a
base that moved. Real git runs in a temp repo. Every test also checks, at teardown, that it left no worktree and no branch
behind, except the branch of a blocked task, which is kept on purpose so a retry can reuse it. The kill-and-resume test is in
test_autopilot_resume.py."""
import os

import pytest

import runway
from autopilot_fake import (COST, CONFLICT, RED, TIMEOUT, Scripted, check_no_leftovers, failure_states, git, make_repo,
                            report_of, run_loop, run_outcome, save, state, statuses, task, worktree)


@pytest.fixture(autouse=True)
def no_leftovers(tmp_path):
    yield
    check_no_leftovers(tmp_path)


def test_a_multi_task_run_merges_what_is_green_blocks_the_rest_and_keeps_the_ledger_straight(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"), task("b", deps=["a"]), task("c"), task("d"), task("e", deps=["c"]), task("f", deps=["b", "e"]))
    agent = Scripted(repo, {"b": RED, "d": CONFLICT, "e": TIMEOUT})
    r = run_loop(repo, agent)
    assert agent.launched == ["a", "b", "c", "d", "e"]  # f waits on b, which is blocked
    assert statuses(repo) == {"a": "done", "b": "blocked", "c": "done", "d": "blocked", "e": "blocked", "f": "todo"}
    assert r["done"] == ["a", "c"] and sorted(r["blocked"]) == ["b", "d", "e"] and r["left"] == 1
    assert state(repo)["stop"]["reason"] == "roadmap"
    assert git(repo, "show", "main:a.txt") == "green a" and git(repo, "show", "main:c.txt") == "green c"
    base_files = git(repo, "ls-tree", "--name-only", "main").split()
    assert "broken.txt" not in base_files and "hang.txt" not in base_files  # the red and timed-out changes never reached main
    assert [run_outcome(k) for k in "abcde" if k in agent.launched] == ["merged", "red", "merged", "conflict", "red"]
    assert failure_states() == ["red", "timeout"]  # the failure log has the two check failures, not the conflict
    rows = runway.budget_rows(state(repo)["run"])
    assert [r["ch"] for r in rows] == ["ap-a", "ap-b", "ap-c", "ap-d", "ap-e"]  # one row per launched task, in launch order
    assert [r["tid"] for r in rows] == agent.launched
    assert sum(r["usd"] for r in rows) == pytest.approx(5 * COST)


def test_a_task_starts_only_once_every_task_it_depends_on_is_done(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"), task("b", deps=["a"]), task("c", deps=["a"]), task("d", deps=["b", "c"]))
    agent = Scripted(repo)
    run_loop(repo, agent)
    assert agent.launched == ["a", "b", "c", "d"]  # roadmap order among the ready tasks
    for key, deps in {"b": ["a"], "c": ["a"], "d": ["b", "c"]}.items():
        assert all(agent.seen[key][dep] == "done" for dep in deps), (key, agent.seen[key])
    assert statuses(repo) == {"a": "done", "b": "done", "c": "done", "d": "done"}


def test_the_dollar_cap_halts_before_a_task_that_would_overrun_it_and_the_spend_is_close_to_it(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"), task("t-3"))
    agent = Scripted(repo)
    r = run_loop(repo, agent, cap=2.0)  # $0.90 a task: two fit, the third would need $0.90 of the $0.20 left
    assert agent.launched == ["t-1", "t-2"] and r["done"] == ["t-1", "t-2"]
    assert statuses(repo) == {"t-1": "done", "t-2": "done", "t-3": "todo"}
    assert state(repo)["stop"]["reason"] == "cap" and "t-3" in state(repo)["stop"]["detail"]
    spent = sum(row["usd"] for row in runway.budget_rows(state(repo)["run"]))
    assert spent == pytest.approx(2 * COST)
    assert abs(spent - 2.0) / 2.0 <= 0.15  # the run report's spend is within 15% of the cap
    assert not os.path.exists(worktree(repo, "t-3"))  # never started


def test_consecutive_failures_halt_the_run_with_a_failures_reason(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"), task("t-3"), task("t-4"))
    agent = Scripted(repo, {"t-1": RED, "t-2": TIMEOUT, "t-3": RED})
    r = run_loop(repo, agent, max_failures=2)
    assert agent.launched == ["t-1", "t-2"]  # the third task never starts
    assert statuses(repo) == {"t-1": "blocked", "t-2": "blocked", "t-3": "todo", "t-4": "todo"}
    assert sorted(r["blocked"]) == ["t-1", "t-2"]
    assert state(repo)["stop"]["reason"] == "failures" and "2 tasks in a row" in state(repo)["stop"]["detail"]
    assert [row["tid"] for row in runway.budget_rows(state(repo)["run"])] == ["t-1", "t-2"]  # the blocked runs' spend is kept


@pytest.mark.parametrize("exit_path", ["roadmap", "cap", "failures", "signal"])
def test_every_exit_path_removes_the_worktrees_of_tasks_that_are_not_running(tmp_path, exit_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"), task("t-3"))
    kw, script, hook = {}, {"t-1": RED}, None
    if exit_path == "cap":
        kw["cap"] = 1.0  # t-1 costs $0.90; t-2 would overrun
        script = {}
    elif exit_path == "failures":
        kw["max_failures"] = 1
        script = {"t-1": TIMEOUT}
    elif exit_path == "signal":
        def hook(agent, seconds):  # asked to stop while t-1 is in flight: its red result is still gated, then the loop stops
            if agent.launched and not os.path.exists(runway._ap_paths(repo)[1]):
                open(runway._ap_paths(repo)[1], "w").write("stop\n")
    agent = Scripted(repo, script, on_sleep=hook)
    run_loop(repo, agent, **kw)
    assert state(repo)["stop"]["reason"] == exit_path
    assert not os.path.exists(runway._ap_paths(repo)[1])
    running = [t for t in runway.roadmap_tasks(runway.load_roadmap(repo)) if t.status == "running"]
    assert running == []
    for key in ("t-1", "t-2", "t-3"):
        assert not os.path.exists(worktree(repo, key)), key
    assert state(repo)["kept"] == {}


def test_a_blocked_check_failure_reports_that_its_worktree_was_removed_not_kept(tmp_path):
    """A2: autopilot's own end-of-run cleanup removes a blocked task's worktree in the common case (the agent already
    committed by the time its checks run), so the report must not repeat _gate's shared "the worktree is kept" line,
    which is only true for merge-batch, which never cleans up."""
    repo = make_repo(tmp_path)
    save(repo, task("t-1"))
    run_loop(repo, Scripted(repo, {"t-1": RED}))
    assert state(repo)["kept"] == {} and not os.path.exists(worktree(repo, "t-1"))
    md, rep = report_of(repo)
    (row,) = [r for r in rep["tasks"] if r["key"] == "t-1"]
    assert "worktree was removed" in row["reason"] and "run the task again" in row["reason"]
    assert "is kept" not in row["reason"]
    assert "worktree was removed" in md
    assert any(a["task"] == "t-1" and "worktree was removed" in a["text"] for a in rep["attention"])


def test_a_blocked_check_failure_whose_worktree_git_refuses_to_remove_still_says_kept(tmp_path):
    """A2's other half: when git really does refuse the cleanup (uncommitted edits left behind), the message must still
    say the worktree is kept, since that part is true."""
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"))

    def leave_uncommitted_edits_once_both_are_launched(agent, seconds):
        if len(agent.launched) == 2:
            with open(os.path.join(worktree(repo, "t-1"), "unfinished.txt"), "w") as f:
                f.write("half done\n")

    run_loop(repo, Scripted(repo, {"t-1": RED}, on_sleep=leave_uncommitted_edits_once_both_are_launched))
    assert list(state(repo)["kept"]) == ["t-1"]
    assert os.path.exists(os.path.join(worktree(repo, "t-1"), "unfinished.txt"))
    md, rep = report_of(repo)
    (row,) = [r for r in rep["tasks"] if r["key"] == "t-1"]
    assert "The worktree is kept." in row["reason"]
    assert "was removed" not in row["reason"]
    git(repo, "worktree", "remove", "--force", worktree(repo, "t-1"))  # clean up this test's own leftover
    git(repo, "branch", "-D", "runway-task/t-1")
