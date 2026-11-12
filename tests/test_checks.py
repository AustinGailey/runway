"""Checks: a chunk task's .runway.json commands run in its worktree, output streams to the Runs view,
red and timed-out runs go to the failure log and keep their worktree. Runs against a fake project in a temp repo."""
import json, os, time

import runway
from test_git_flows import commit, git, make_repo, start_task


def fake_project(tmp_path, checks):
    """A repo whose main has a .runway.json with these checks, and a task worktree forked from it."""
    repo = make_repo(tmp_path)
    commit(repo, "checks config", {".runway.json": json.dumps({"checks": checks})})
    start_task(repo, "t1", chunk="c1")
    return repo


def failures():
    lines = open(runway.FAILURES).read().splitlines() if os.path.exists(runway.FAILURES) else []
    return [json.loads(x) for x in lines]


def state(tid="t1", ch="c1"):
    return json.load(open(os.path.join(runway.RUNS, ch, tid + ".checks.json")))


def wt_path(repo, tid="t1"):
    return os.path.join(repo, ".claude", "worktrees", "wt-" + tid)


def test_green_runs_every_check_in_the_worktree_and_logs_nothing_as_failed(tmp_path):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "echo unit ok"},
                                   {"name": "build", "cmd": "echo built > built.txt"}])
    v = runway.run_checks("c1", "t1")
    assert v == {"verdict": "green", "ok": True, "check": None, "rc": None, "reason": None}
    assert state()["state"] == "green"
    assert os.path.exists(os.path.join(wt_path(repo), "built.txt"))  # ran inside the worktree
    assert failures() == []
    log = open(os.path.join(runway.RUNS, "c1", "t1.checks.log")).read()
    assert "$ echo unit ok" in log and "unit ok" in log


def test_red_logs_the_failure_stops_and_keeps_the_worktree(tmp_path):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "echo going; echo broken >&2; exit 3"},
                                   {"name": "build", "cmd": "echo never > never.txt"}])
    v = runway.run_checks("c1", "t1")
    assert v["verdict"] == "red" and v["ok"] is False and v["rc"] == 3 and v["check"] == "unit"
    recs = failures()
    assert len(recs) == 1
    assert recs[0]["state"] == "red" and recs[0]["task"] == "t1" and recs[0]["check"] == "unit" and recs[0]["rc"] == 3
    assert not os.path.exists(os.path.join(wt_path(repo), "never.txt"))  # later checks do not run after a red one
    assert os.path.isdir(wt_path(repo))
    assert "broken" in open(recs[0]["log"]).read()


def test_timeout_is_logged_as_timeout_and_keeps_the_worktree(tmp_path):
    repo = fake_project(tmp_path, [{"name": "slow", "cmd": "echo starting; sleep 30", "timeout": 1}])
    started = time.time()
    v = runway.run_checks("c1", "t1")
    assert time.time() - started < 15  # the sleep was killed, not waited out
    assert v["verdict"] == "timeout" and v["ok"] is False and v["reason"] == "timed out"
    recs = failures()
    assert len(recs) == 1 and recs[0]["state"] == "timeout" and recs[0]["check"] == "slow"
    assert os.path.isdir(wt_path(repo))
    log = open(recs[0]["log"]).read()
    assert "starting" in log and "timed out after 1s" in log


def test_no_config_is_none_not_green_or_red(tmp_path):
    repo = make_repo(tmp_path, checks=False)
    start_task(repo, "t1", chunk="c1")
    v = runway.run_checks("c1", "t1")
    assert v["verdict"] == "none" and v["ok"] is False
    assert failures() == []


def test_bad_config_is_red_with_the_reason(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "bad config", {".runway.json": '{"checks": [{"name": "x"}]}'})
    start_task(repo, "t1", chunk="c1")
    v = runway.run_checks("c1", "t1")
    assert v["verdict"] == "red" and "checks" in v["reason"]
    assert failures()[0]["state"] == "red"


def test_older_worktree_takes_the_checks_from_the_merge_target(tmp_path):
    # the worktree is cut before .runway.json exists in main; main gains it afterwards
    repo = make_repo(tmp_path, checks=False)
    start_task(repo, "t1", chunk="c1")
    commit(repo, "checks config", {".runway.json": json.dumps({"checks": [{"name": "unit", "cmd": "echo ok"}]})})
    assert not os.path.exists(os.path.join(wt_path(repo), ".runway.json"))
    v = runway.run_checks("c1", "t1", "main")
    assert v["verdict"] == "green"
    write_agent_change(repo)
    out = runway.merge("c1", "t1")
    assert out.startswith("@@OK"), out
    assert os.path.exists(os.path.join(repo, "feature.txt"))


def test_checks_are_read_from_the_target_before_the_worktree(tmp_path):
    repo = fake_project(tmp_path, [{"name": "main-unit", "cmd": "echo ok"}])
    with open(os.path.join(wt_path(repo), ".runway.json"), "w") as f:  # the worktree's own copy, which must lose
        json.dump({"checks": [{"name": "branch-unit", "cmd": "exit 1"}]}, f)
    v = runway.run_checks("c1", "t1", "main")
    assert v["verdict"] == "green"
    assert "branch-unit" not in open(os.path.join(runway.RUNS, "c1", "t1.checks.log")).read()


def test_no_checks_means_no_gate(tmp_path):
    """The behaviour contract: a repo without .runway.json merges as before (see tests/test_contract.py)."""
    repo = make_repo(tmp_path, checks=False)
    start_task(repo, "t1", chunk="c1")
    write_agent_change(repo)
    out = runway.merge("c1", "t1")
    assert out.startswith("@@OK"), out


def test_worktree_is_created_when_missing(tmp_path):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "echo ok"}])
    git(repo, "branch", "runway-task/t2", "main")
    rundir = os.path.join(runway.RUNS, "c1")
    with open(os.path.join(rundir, "t2.meta"), "w") as f:
        json.dump({"repo": repo, "wt": "wt-t2", "base": "main"}, f)
    v = runway.run_checks("c1", "t2")
    assert v["verdict"] == "green"
    assert os.path.isdir(wt_path(repo, "t2"))
    assert git(wt_path(repo, "t2"), "rev-parse", "--abbrev-ref", "HEAD").strip() == "runway-task/t2"


def test_chunk_status_shows_running_output_and_the_result(tmp_path):
    fake_project(tmp_path, [{"name": "unit", "cmd": "echo line one; echo line two; exit 1"}])
    runway.run_checks("c1", "t1")
    open(os.path.join(runway.RUNS, "c1", "t1.status"), "w").write("done")
    c = runway.chunk_status("c1")
    checks = c["tasks"]["t1"]["checks"]
    assert checks["state"] == "red" and checks["step"] == "unit"
    assert "line one" in checks["tail"] and "line two" in checks["tail"]


def test_checks_cli_prints_the_verdict(tmp_path, capsys):
    fake_project(tmp_path, [{"name": "unit", "cmd": "exit 0"}])
    runway.main(["checks", "c1", "t1"])
    assert "@@CHECKS green c1 t1" in capsys.readouterr().out


def write_agent_change(repo):
    """The agent's own change in its worktree, as the run leaves it."""
    with open(os.path.join(wt_path(repo), "feature.txt"), "w") as f:
        f.write("new\n")


def test_merge_is_refused_when_checks_are_red(tmp_path):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "exit 1"}])
    write_agent_change(repo)
    out = runway.merge("c1", "t1")
    assert out.startswith("@@ERR Its checks did not pass (red: unit)")
    assert not os.path.exists(os.path.join(repo, "feature.txt"))


def test_merge_goes_ahead_when_the_project_has_no_checks(tmp_path):
    """No .runway.json means no gate: projects that never opted in merge exactly as before."""
    repo = make_repo(tmp_path, checks=False)
    start_task(repo, "t1", chunk="c1")
    write_agent_change(repo)
    out = runway.merge("c1", "t1")
    assert out.startswith("@@OK"), out
    assert os.path.exists(os.path.join(repo, "feature.txt"))


def test_merge_runs_the_checks_first_and_lands_when_green(tmp_path):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "echo ok"}])
    write_agent_change(repo)
    assert not os.path.exists(os.path.join(runway.RUNS, "c1", "t1.checks.json"))  # no verdict yet
    out = runway.merge("c1", "t1")
    assert out.startswith("@@OK"), out
    assert state()["state"] == "green"
    assert os.path.exists(os.path.join(repo, "feature.txt"))


def test_checks_run_on_the_combined_result_and_blame_the_task(tmp_path):
    """Green alone, red with the task: the task caused it, so nothing merges and the card sends it back."""
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "test ! -f feature.txt"}])
    write_agent_change(repo)
    iw, ibranch = runway.integration(repo, "main")
    head = git(iw, "rev-parse", "HEAD")
    res = runway.apply_task("c1", "t1", iw, ibranch, "main", False, lambda m: None)
    assert res["outcome"] == "checks" and res["baseRed"] is False, res
    assert "this task caused it" in res["msg"]
    assert git(iw, "rev-parse", "HEAD") == head  # taken back off
    assert state()["state"] == "red"


def test_a_failure_already_on_the_target_is_named_and_force_merges(tmp_path):
    """Red with and without the task: the failure was already there; Merge anyway (force) lands the task."""
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "exit 1"}])
    write_agent_change(repo)
    iw, ibranch = runway.integration(repo, "main")
    res = runway.apply_task("c1", "t1", iw, ibranch, "main", False, lambda m: None)
    assert res["outcome"] == "checks" and res["baseRed"] is True, res
    assert "already there" in res["msg"]
    forced = runway.apply_task("c1", "t1", iw, ibranch, "main", False, lambda m: None, force=True)
    assert forced["outcome"] == "merged", forced
    assert os.path.exists(os.path.join(iw, "feature.txt"))


def test_a_check_that_fails_once_then_passes_merges_and_is_logged_as_flaky(tmp_path):
    """Red with the task, green without it, green with it again: an intermittent test, not the task's fault. It merges,
    says so, and goes in the failures log as flaky, instead of sending sound work back to its agent."""
    mark = tmp_path / "ran-once"
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": f"[ -f '{mark}' ] || {{ touch '{mark}'; exit 1; }}"}])
    write_agent_change(repo)
    iw, ibranch = runway.integration(repo, "main")
    res = runway.apply_task("c1", "t1", iw, ibranch, "main", False, lambda m: None)
    assert res["outcome"] == "merged" and res.get("flaky") == "unit", res
    assert "green on a rerun" in res["msg"]
    assert os.path.exists(os.path.join(iw, "feature.txt"))  # the task is on the integration branch
    assert state()["state"] == "green"
    assert any(f.get("phase") == "test" and "flaky" in f.get("stderr_tail", "") for f in runway.read_failures(n=20))


def test_a_task_that_breaks_the_checks_every_time_is_still_blamed_after_the_rerun(tmp_path):
    """The rerun only rescues intermittent failures: red twice with the task (and green without it) still sends it back."""
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "test ! -f feature.txt"}])
    write_agent_change(repo)
    iw, ibranch = runway.integration(repo, "main")
    head = git(iw, "rev-parse", "HEAD")
    res = runway.apply_task("c1", "t1", iw, ibranch, "main", False, lambda m: None)
    assert res["outcome"] == "checks" and "this task caused it" in res["msg"] and not res.get("flaky")
    assert git(iw, "rev-parse", "HEAD") == head
    assert os.path.exists(os.path.join(runway.RUNS, "c1", "t1.checks-retry.log"))  # the rerun happened
