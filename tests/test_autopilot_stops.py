"""The autopilot's stop conditions and resume, in helper/runway.py: the dollar cap, consecutive failures, a stop request
between tasks, clean worktrees on every stop, and resuming from persisted state. Real git runs in a temp repo; the agent is
a fake launcher (as in test_autopilot.py), and its usage is written where a headless run would write it."""
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


def task(key, deps=(), status="todo", est=0, model=""):
    return {"key": key, "title": "Task " + key, "detail": "Do " + key, "acceptance": "It works", "dependsOn": list(deps),
            "status": status, "estTokens": est, "model": model}


def save(repo, *tasks):
    runway.save_roadmap(repo, {"version": 1, "project": "site", "title": "Site",
                               "pis": [{"key": "pi-1", "title": "PI", "sprints": [{"key": "s-1", "title": "S",
                                                                                   "tasks": list(tasks)}]}]})


def statuses(repo):
    return {t.key: t.status for t in runway.roadmap_tasks(runway.load_roadmap(repo))}


def worktree(repo, key):
    return os.path.join(repo, ".claude", "worktrees", "runway-" + key)


def state(repo):
    return json.loads(open(runway._ap_paths(repo)[0]).read())


def finish(repo, key, code=0, usage=None):
    """What a headless run leaves behind: the agent's change committed (on exit 0), the exit code, its usage, status done."""
    d = os.path.join(runway.RUNS, "ap-" + key)
    if code == 0:
        wtp = worktree(repo, key)
        with open(os.path.join(wtp, key + ".txt"), "w") as f:
            f.write("done " + key + "\n")
        git(wtp, "add", "-A")
        git(wtp, "commit", "-q", "-m", "agent " + key)
    if usage is not None:
        with open(os.path.join(d, key + ".json"), "w") as f:
            json.dump({"usage": usage, "cost": 0}, f)
    open(os.path.join(d, key + ".exit"), "w").write(str(code) + "\n")
    open(os.path.join(d, key + ".status"), "w").write("done\n")


class Agent:
    """A fake launcher: each launch is held until the loop sleeps, then the oldest one finishes. fail keys exit 1, and
    before_finish(agent, key) runs first, the way a test can leave files behind in a run's worktree."""

    def __init__(self, repo, fail=(), crash_on=None, usage=None, before_finish=None):
        self.repo, self.fail, self.crash_on, self.usage, self.before_finish = repo, set(fail), crash_on, usage, before_finish
        self.pending, self.launched = [], []

    def launch(self, ch):
        key = ch[len("ap-"):]
        if key == self.crash_on:
            raise KeyboardInterrupt
        self.launched.append(key)
        d = os.path.join(runway.RUNS, ch)
        open(os.path.join(d, "_pgid"), "w").write(str(os.getpgrp()))  # our own group: alive while the test runs
        self.pending.append(key)
        return "launched"

    def sleep(self, _seconds):
        if self.pending:
            key = self.pending.pop(0)
            if self.before_finish:
                self.before_finish(self, key)
            finish(self.repo, key, code=1 if key in self.fail else 0, usage=self.usage)


def run_loop(repo, agent, sleep=None, **kw):
    return runway.autopilot(repo, launcher=agent.launch, poll=0, sleep=sleep or agent.sleep, **kw)


def seed_run(repo, run, cap=None, stop=None):
    """A state file as an earlier run would have left it, so a resume has something to carry on."""
    runway.write_json_atomic(runway._ap_paths(repo)[0], {"run": run, "cap": cap, "maxFailures": None, "streak": 0,
                                                         "startedAt": 0, "stop": stop})


SONNET = {"input_tokens": 300000, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def test_the_cap_stops_before_a_task_that_would_overrun_it(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1", est=100000, model="claude-sonnet-5"), task("t-2", est=100000, model="claude-sonnet-5"))
    seed_run(repo, "r-1", cap=1.0)
    runway.budget_append("r-1", "ap-old", "old", "claude-sonnet-5", {"input": 300000, "output": 0, "cache_write": 0, "cache_read": 0})
    agent = Agent(repo)
    run_loop(repo, agent, resume=True)  # $0.90 spent; t-1 projects to $0.30, more than the $0.10 left
    assert agent.launched == []
    assert statuses(repo) == {"t-1": "todo", "t-2": "todo"}
    assert not os.path.exists(worktree(repo, "t-1"))  # never started, so no worktree was made
    assert state(repo)["stop"]["reason"] == "cap"
    assert "t-1" in state(repo)["stop"]["detail"] and "0.10" in state(repo)["stop"]["detail"]


def test_a_task_that_fits_under_the_cap_runs(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1", est=100000, model="claude-sonnet-5"))
    seed_run(repo, "r-1", cap=1.0)
    runway.budget_append("r-1", "ap-old", "old", "claude-sonnet-5", {"input": 300000, "output": 0, "cache_write": 0, "cache_read": 0})
    agent = Agent(repo)
    r = run_loop(repo, agent, resume=True, cap=2.0)  # the resumed run's cap is the one given now: $1.10 left, $0.30 projected
    assert agent.launched == ["t-1"] and r["done"] == ["t-1"]
    assert state(repo)["stop"]["reason"] == "roadmap"


def test_spend_is_recorded_and_the_cap_ends_the_run_once_reached(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1", est=1000, model="claude-sonnet-5"), task("t-2", est=1000, model="claude-sonnet-5"))
    agent = Agent(repo, usage=SONNET)
    run_loop(repo, agent, cap=0.5)  # nothing measured yet, so t-1 starts; its $0.90 then leaves nothing for t-2
    run = state(repo)["run"]
    assert agent.launched == ["t-1"]
    assert statuses(repo) == {"t-1": "done", "t-2": "todo"}
    assert sum(r["usd"] for r in runway.budget_rows(run)) == pytest.approx(0.9)
    assert state(repo)["stop"]["reason"] == "cap"


def test_consecutive_failures_stop_the_run_at_the_limit(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"), task("t-3"))
    agent = Agent(repo, fail={"t-1", "t-2", "t-3"})
    r = run_loop(repo, agent, max_failures=2)
    assert agent.launched == ["t-1", "t-2"]
    assert statuses(repo) == {"t-1": "blocked", "t-2": "blocked", "t-3": "todo"}
    assert sorted(r["blocked"]) == ["t-1", "t-2"]
    assert state(repo)["stop"]["reason"] == "failures"


def test_a_success_resets_the_failure_count(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"), task("t-3"))
    agent = Agent(repo, fail={"t-1", "t-3"})
    run_loop(repo, agent, max_failures=2)
    assert statuses(repo) == {"t-1": "blocked", "t-2": "done", "t-3": "blocked"}
    assert state(repo)["stop"]["reason"] == "roadmap"


def test_without_a_limit_failures_do_not_stop_the_run(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"), task("t-3"))
    agent = Agent(repo, fail={"t-1", "t-2", "t-3"})
    run_loop(repo, agent)
    assert agent.launched == ["t-1", "t-2", "t-3"]
    assert state(repo)["stop"]["reason"] == "roadmap"


def test_a_stop_request_is_honoured_between_tasks(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"), task("t-3"))
    agent = Agent(repo)

    def sleep_once_stop_is_asked(seconds):
        if agent.launched and not os.path.exists(runway._ap_paths(repo)[1]):
            open(runway._ap_paths(repo)[1], "w").write("stop\n")  # asked while t-1 is in flight
        agent.sleep(seconds)

    r = run_loop(repo, agent, sleep=sleep_once_stop_is_asked)
    assert agent.launched == ["t-1"]  # the task in flight finishes and merges; nothing more starts
    assert r["done"] == ["t-1"] and statuses(repo) == {"t-1": "done", "t-2": "todo", "t-3": "todo"}
    assert state(repo)["stop"]["reason"] == "signal"
    assert not os.path.exists(runway._ap_paths(repo)[1])  # the request is used up


def test_autopilot_stop_only_asks_a_running_loop(tmp_path):
    repo = make_repo(tmp_path)
    stop_path = runway._ap_paths(repo)[1]
    assert runway.autopilot_stop(repo) is False and not os.path.exists(stop_path)
    lock = runway._lock(os.path.join(runway.BASE, "autopilot-%s.lock" % runway._slug(repo, "autopilot")), 0)
    try:
        assert runway.autopilot_stop(repo) is True and os.path.exists(stop_path)
    finally:
        lock.close()


def test_a_stop_removes_the_worktrees_of_tasks_not_running_and_keeps_their_branches(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"))
    agent = Agent(repo, fail={"t-1"})
    run_loop(repo, agent, max_failures=1)
    assert statuses(repo) == {"t-1": "blocked", "t-2": "todo"}
    assert not os.path.exists(worktree(repo, "t-1"))
    assert "runway-task/t-1" in git(repo, "branch", "--list", "runway-task/t-1")  # kept, so a retry can reuse it
    assert "runway-task/t-1" not in git(repo, "worktree", "list")
    assert state(repo)["kept"] == {}


def test_a_worktree_with_uncommitted_work_is_kept_and_named(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"))

    def leave_edits(agent, key):
        open(os.path.join(worktree(repo, key), "unfinished.txt"), "w").write("half done\n")

    agent = Agent(repo, fail={"t-1"}, before_finish=leave_edits)
    run_loop(repo, agent, max_failures=1)
    assert os.path.exists(os.path.join(worktree(repo, "t-1"), "unfinished.txt"))  # nobody's edits are removed
    assert list(state(repo)["kept"]) == ["t-1"]


def test_a_crash_keeps_the_running_task_and_a_resume_carries_on_without_rerunning_done_work(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2", deps=["t-1"]))
    agent = Agent(repo, crash_on="t-2")
    with pytest.raises(KeyboardInterrupt):
        run_loop(repo, agent)
    run = state(repo)["run"]
    assert state(repo)["stop"]["reason"] == "interrupted"
    assert statuses(repo) == {"t-1": "done", "t-2": "running"}
    assert os.path.isdir(worktree(repo, "t-2"))  # in flight: its worktree stays for the resume
    agent = Agent(repo)
    r = run_loop(repo, agent, resume=True)
    assert agent.launched == ["t-2"]  # t-1 is done and is not run again
    assert r["done"] == ["t-2"] and statuses(repo) == {"t-1": "done", "t-2": "done"}
    assert state(repo)["run"] == run  # the same budget ledger
    assert state(repo)["resumedAt"] and state(repo)["stop"]["reason"] == "roadmap"


def test_a_resume_needs_an_earlier_run(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"))
    with pytest.raises(runway.AutopilotError, match="Nothing to resume"):
        run_loop(repo, Agent(repo), resume=True)
    assert statuses(repo) == {"t-1": "todo"}


def test_a_fresh_run_is_refused_after_an_interrupted_one_that_recorded_no_reason(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"))
    seed_run(repo, "r-1", stop=None)  # what a loop killed without a chance to write its reason leaves behind
    with pytest.raises(runway.AutopilotError, match="--resume"):
        run_loop(repo, Agent(repo))
    assert statuses(repo) == {"t-1": "todo"}


def test_the_cap_and_failure_limit_must_be_positive(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"))
    with pytest.raises(runway.AutopilotError, match="dollar cap"):
        run_loop(repo, Agent(repo), cap=0)
    with pytest.raises(runway.AutopilotError, match="max_failures"):
        run_loop(repo, Agent(repo), max_failures=0)
    assert statuses(repo) == {"t-1": "todo"}


def test_a_stop_request_cleans_up_a_blocked_worktree_too(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"), task("t-3"))
    agent = Agent(repo, fail={"t-1"})

    def ask_to_stop_while_t2_runs(seconds):
        if len(agent.launched) == 2 and not os.path.exists(runway._ap_paths(repo)[1]):
            open(runway._ap_paths(repo)[1], "w").write("stop\n")
        agent.sleep(seconds)

    run_loop(repo, agent, sleep=ask_to_stop_while_t2_runs)
    assert agent.launched == ["t-1", "t-2"]
    assert statuses(repo) == {"t-1": "blocked", "t-2": "done", "t-3": "todo"}
    assert not os.path.exists(worktree(repo, "t-1"))  # blocked and not running: removed on the signal stop
    assert state(repo)["stop"]["reason"] == "signal"


def test_a_crash_cleans_up_blocked_worktrees_and_keeps_the_one_in_flight(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2"))
    agent = Agent(repo, fail={"t-1"}, crash_on="t-2")
    with pytest.raises(KeyboardInterrupt):
        run_loop(repo, agent)
    assert statuses(repo) == {"t-1": "blocked", "t-2": "running"}
    assert not os.path.exists(worktree(repo, "t-1"))
    assert os.path.isdir(worktree(repo, "t-2"))
    assert state(repo)["stop"]["reason"] == "interrupted"
