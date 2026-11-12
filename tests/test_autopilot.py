"""The autopilot loop in helper/runway.py: it picks ready tasks in roadmap order, runs each through the worktree and merge
gate, saves every status change, keeps going past blocked tasks, and resumes after a crash. Real git runs in a temp repo;
the agent is a fake launcher that commits in the worktree and writes the run's status files, so no claude runs here."""
import json, os, subprocess

import pytest

import runway


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "site"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return str(repo)


def task(key, deps=(), status="todo"):
    return {"key": key, "title": "Task " + key, "detail": "Do " + key, "acceptance": "It works", "dependsOn": list(deps),
            "status": status}


def save(repo, *tasks):
    runway.save_roadmap(repo, {"version": 1, "project": "site", "title": "Site",
                               "pis": [{"key": "pi-1", "title": "PI", "sprints": [{"key": "s-1", "title": "S",
                                                                                   "tasks": list(tasks)}]}]})


def statuses(repo):
    return {t.key: t.status for t in runway.roadmap_tasks(runway.load_roadmap(repo))}


def worktree(repo, key):
    return os.path.join(repo, ".claude", "worktrees", "runway-" + key)


def finish(repo, key, code=0):
    """What a headless run leaves behind: the agent's change committed, the exit code, and status done."""
    d = os.path.join(runway.RUNS, "ap-" + key)
    if code == 0:
        wtp = worktree(repo, key)
        with open(os.path.join(wtp, key + ".txt"), "w") as f:
            f.write("done " + key + "\n")
        git(wtp, "add", "-A")
        git(wtp, "commit", "-q", "-m", "agent " + key)
    open(os.path.join(d, key + ".exit"), "w").write(str(code) + "\n")
    open(os.path.join(d, key + ".status"), "w").write("done\n")


class Agent:
    """A fake launcher. Each launch is held in flight until the loop sleeps, then the oldest one finishes (exit 0 unless
    the key is in fail). crash_on makes a launch raise, the way a killed loop stops."""

    def __init__(self, repo, fail=(), crash_on=None):
        self.repo, self.fail, self.crash_on = repo, set(fail), crash_on
        self.pending, self.launched, self.max_in_flight = [], [], 0

    def launch(self, ch):
        key = ch[len("ap-"):]
        if key == self.crash_on:
            raise KeyboardInterrupt
        self.launched.append(key)
        d = os.path.join(runway.RUNS, ch)
        open(os.path.join(d, "_pgid"), "w").write(str(os.getpgrp()))  # our own group: alive while the test runs
        self.pending.append(key)
        self.max_in_flight = max(self.max_in_flight, len(self.pending))
        return "launched"

    def sleep(self, _seconds):
        if self.pending:
            key = self.pending.pop(0)
            finish(self.repo, key, code=1 if key in self.fail else 0)


def run_loop(repo, agent, parallel=1):
    return runway.autopilot(repo, parallel, launcher=agent.launch, poll=0, sleep=agent.sleep)


def test_ready_tasks_wait_for_their_dependencies_and_skip_other_statuses():
    rm = runway.parse_roadmap({"version": 1, "pis": [{"key": "pi", "title": "PI", "sprints": [{"key": "s", "title": "S", "tasks": [
        task("a", status="done"), task("b", deps=["a"]), task("c", deps=["x"]), task("x", status="blocked"),
        task("d", deps=["b"]), task("e", status="running")]}]}]})
    assert [t.key for t in runway.ready_tasks(rm)] == ["b"]


def test_a_status_change_is_saved_to_the_roadmap_file_at_once(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"))
    runway.set_task_status(repo, "t-1", "running")
    assert statuses(repo) == {"t-1": "running"}
    assert json.load(open(runway.roadmap_path(repo)))["pis"][0]["sprints"][0]["tasks"][0]["status"] == "running"


def test_a_chain_runs_in_dependency_order_and_each_task_merges(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2", deps=["t-1"]))
    agent = Agent(repo)
    r = run_loop(repo, agent)
    assert agent.launched == ["t-1", "t-2"]
    assert r == {"done": ["t-1", "t-2"], "blocked": {}, "left": 0}
    assert statuses(repo) == {"t-1": "done", "t-2": "done"}
    assert git(repo, "show", "main:t-1.txt") == "done t-1"  # the change reached the base branch through the merge gate
    assert git(repo, "show", "main:t-2.txt") == "done t-2"
    assert not os.path.exists(worktree(repo, "t-1"))  # automerge removed the worktree


def test_default_parallelism_is_one_task_in_flight(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"), task("b"), task("c"))
    agent = Agent(repo)
    run_loop(repo, agent)
    assert agent.max_in_flight == 1
    assert statuses(repo) == {"a": "done", "b": "done", "c": "done"}


def test_parallel_runs_that_many_independent_tasks_at_once(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"), task("b"), task("c"))
    agent = Agent(repo)
    run_loop(repo, agent, parallel=2)
    assert agent.max_in_flight == 2
    final = statuses(repo)
    assert final["a"] == "done"  # the first to finish merges
    # b and c forked before a merged. With no checks configured, automerge does not count the base's sync as green, so
    # they may block (existing gate behaviour, reported separately). What matters here: nothing is left running or ready.
    assert set(final.values()) <= {"done", "blocked"}
    assert not runway.ready_tasks(runway.load_roadmap(repo))


def test_parallel_must_be_a_whole_number_of_at_least_one(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"))
    with pytest.raises(runway.AutopilotError):
        runway.autopilot(repo, 0, launcher=lambda ch: "launched", poll=0, sleep=lambda s: None)


def test_a_failed_run_blocks_its_task_and_its_dependents_but_not_independent_work(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"), task("b", deps=["a"]), task("c"))
    agent = Agent(repo, fail={"a"})
    r = run_loop(repo, agent)
    assert statuses(repo) == {"a": "blocked", "b": "todo", "c": "done"}
    assert set(r["blocked"]) == {"a"}
    assert r["done"] == ["c"] and r["left"] == 1
    note = json.load(open(os.path.join(runway.RUNS, "ap-a", "a.autopilot.json")))
    assert note["outcome"] == "error" and "exited 1" in note["msg"]


def test_a_conflict_blocks_the_task_and_leaves_the_base_alone(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"))
    agent = Agent(repo)
    orig = agent.launch

    def launch(ch):
        # the task edits README in its worktree, and the base edits the same line, so the sync cannot merge cleanly
        wtp = worktree(repo, "a")
        open(os.path.join(wtp, "README.md"), "w").write("from the task\n")
        git(wtp, "add", "-A")
        git(wtp, "commit", "-q", "-m", "task edits README")
        open(os.path.join(repo, "README.md"), "w").write("from the base\n")
        git(repo, "commit", "-q", "-am", "base edits README")
        return orig(ch)

    r = runway.autopilot(repo, 1, launcher=launch, poll=0, sleep=agent.sleep)
    assert statuses(repo) == {"a": "blocked"}
    assert list(r["blocked"]) == ["a"]
    assert git(repo, "show", "main:README.md") == "from the base"  # nothing from the task reached the base
    note = json.load(open(os.path.join(runway.RUNS, "ap-a", "a.autopilot.json")))
    assert note["outcome"] == "conflict", note["msg"]


def test_a_killed_loop_resumes_without_losing_progress(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2", deps=["t-1"]))
    agent = Agent(repo, crash_on="t-2")
    with pytest.raises(KeyboardInterrupt):
        run_loop(repo, agent)
    assert statuses(repo) == {"t-1": "done", "t-2": "running"}  # t-1 merged and saved before the crash
    assert git(repo, "show", "main:t-1.txt") == "done t-1"
    agent = Agent(repo)
    r = run_loop(repo, agent)
    assert agent.launched == ["t-2"]  # t-1 is not run again; t-2 was saved as running but never launched, so it launches now
    assert r["done"] == ["t-2"] and statuses(repo) == {"t-1": "done", "t-2": "done"}


def test_a_run_whose_process_is_gone_without_finishing_is_blocked(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"))
    runway.set_task_status(repo, "a", "running")
    d = os.path.join(runway.RUNS, "ap-a")
    os.makedirs(d)
    import subprocess as sp
    gone = sp.Popen(["true"])
    gone.wait()
    open(os.path.join(d, "_pgid"), "w").write(str(gone.pid))  # a group that no longer exists
    agent = Agent(repo)
    r = run_loop(repo, agent)
    assert agent.launched == []
    assert statuses(repo) == {"a": "blocked"} and "stopped before it finished" in r["blocked"]["a"]


def test_a_second_loop_on_the_same_roadmap_is_refused(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"))
    lock = runway._lock(os.path.join(runway.BASE, "autopilot-%s.lock" % runway._slug(repo, "autopilot")), 0)
    try:
        with pytest.raises(runway.AutopilotError):
            run_loop(repo, Agent(repo))
    finally:
        lock.close()
    assert statuses(repo) == {"a": "todo"}


def test_the_run_script_runs_one_agent_then_the_checks_through_this_helper(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"))
    agent = Agent(repo)
    run_loop(repo, agent)
    script = open(os.path.join(runway.RUNS, "ap-a", "run.sh")).read()
    assert script.startswith("#!/bin/bash")
    assert "--permission-mode acceptEdits" in script and "--disallowedTools" in script and "'Bash(git push:*)'" in script
    assert "checks ap-a a" in script and runway.os.path.abspath(runway.__file__) in script
