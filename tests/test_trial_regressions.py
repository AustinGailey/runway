"""Regression tests for the merge-train trial defects (docs/reports/2026-10-04-merge-train-trial.md): D1 and D2 (what
merge-log shows), D4 (what a retry is), D5 (the conflict's next step). Temp repos only, via conftest's isolated HOME."""
import json, os

import runway
from test_checks import fake_project, wt_path
from test_git_flows import make_repo, start_task, write


def two_tasks_conflicting_on_a(tmp_path):
    repo = make_repo(tmp_path)
    wt1 = start_task(repo, "t1", title="First")
    wt2 = start_task(repo, "t2", title="Second")
    write(wt1, {"a.txt": "from task one\n"})
    write(wt2, {"a.txt": "from task two\n"})
    iw, ibranch = runway.integration(repo, "main")
    noop = lambda m: None
    assert runway.apply_task("chunk-1", "t1", iw, ibranch, "main", False, noop)["outcome"] == "merged"
    return runway.apply_task("chunk-1", "t2", iw, ibranch, "main", False, noop), wt2, ibranch


def test_d1_conflict_with_approved_work_is_logged_so_merge_log_shows_it(tmp_path, capsys):
    result, _, _ = two_tasks_conflicting_on_a(tmp_path)
    assert result["outcome"] == "conflict"
    recs = runway.read_failures()
    assert [(r["chunk"], r["branch"], r["phase"], r["paths"]) for r in recs] == [("chunk-1", "runway-task/t2", "merge", ["a.txt"])]
    runway.main(["merge-log"])
    out = capsys.readouterr().out
    assert "chunk-1  (1 failure)" in out and "a.txt" in out
    assert "No merge-train failures logged." not in out


def test_d2_red_check_is_in_merge_log_as_well_as_failures(tmp_path, capsys):
    fake_project(tmp_path, [{"name": "unit", "cmd": "exit 1"}])
    assert runway.run_checks("c1", "t1")["verdict"] == "red"
    recs = runway.read_failures()
    assert [(r["chunk"], r["branch"], r["phase"], r["exit_code"]) for r in recs] == [("c1", "runway-task/t1", "test", 1)]
    assert "$ exit 1" in recs[0]["stderr_tail"]  # the check log's tail travels with the entry
    assert len(runway.read_check_failures()) == 1  # failures.log still has it, for `failures`
    runway.main(["merge-log"])
    assert "c1  (1 failure)" in capsys.readouterr().out


def test_d2_green_run_writes_no_merge_log_entry(tmp_path):
    fake_project(tmp_path, [{"name": "unit", "cmd": "exit 0"}])
    assert runway.run_checks("c1", "t1")["verdict"] == "green"
    assert runway.read_failures() == []


def test_d4_a_retry_is_a_rerun_after_a_red_result_not_any_second_run(tmp_path):
    repo = fake_project(tmp_path, [{"name": "flag", "cmd": "test -f ok.flag"}])
    assert runway.run_checks("c1", "t1")["verdict"] == "red"  # the first run: nothing to retry
    open(os.path.join(wt_path(repo), "ok.flag"), "w").close()
    assert runway.run_checks("c1", "t1")["verdict"] == "green"  # fixed after red: one retry
    assert runway.run_checks("c1", "t1")["verdict"] == "green"  # green again with no change: not a retry
    rec = json.load(open(runway._timing_path("c1", "t1")))
    assert rec["test_runs"] == 3 and rec["retries"] == 1
    assert runway.task_row("c1", "t1", "merged", rec)["retries"] == 1


def test_d4_a_run_after_green_is_not_a_retry(tmp_path):
    fake_project(tmp_path, [{"name": "unit", "cmd": "exit 0"}])
    runway.run_checks("c1", "t1")
    runway.run_checks("c1", "t1")  # the t5 case in the trial: a manual re-run after a green check
    rec = json.load(open(runway._timing_path("c1", "t1")))
    assert rec["test_runs"] == 2 and rec.get("retries", 0) == 0


def test_d5_conflict_message_names_the_task_branch_worktree_and_the_next_command(tmp_path):
    result, wt2, ibranch = two_tasks_conflicting_on_a(tmp_path)
    msg = result["msg"]
    assert msg.startswith("Conflicts with approved work in a.txt.")
    assert "runway-task/t2" in msg and wt2 in msg
    assert "git -C %s merge %s" % (wt2, ibranch) in msg
    assert "then merge again" in msg
    assert os.path.isdir(wt2)  # the task is really kept where the message says
