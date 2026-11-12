"""Merge-train ledger: per-task phase times (setup, test, merge), the run's totals in the baseline's buckets, and
the intervention count. Git runs only in temp repos from test_git_flows, and the paths come from conftest's isolated HOME."""
import json, os, subprocess

import runway
from test_git_flows import commit, make_repo, start_task, write
from test_checks import fake_project


def ledger(bid):
    return json.load(open(os.path.join(runway.BASE, "ledgers", bid + ".json")))


def test_task_row_maps_timing_into_the_ledger_fields():
    row = runway.task_row("c1", "t1", "resolved", {"setup_ms": 5, "test_ms": 100, "merge_ms": 40, "test_runs": 3, "retries": 2, "conflicts_resolved": 1})
    assert row == {"ch": "c1", "tid": "t1", "outcome": "resolved", "setup_ms": 5, "test_ms": 100, "merge_ms": 40,
                   "retries": 2, "conflicts_resolved": 1, "human_touches": 0}


def test_task_without_timing_record_is_zeros_and_a_stopped_merge_is_a_human_touch():
    row = runway.task_row("c1", "t9", "conflict", None)
    assert (row["setup_ms"], row["test_ms"], row["merge_ms"], row["retries"]) == (0, 0, 0, 0)
    assert row["human_touches"] == 1


def test_totals_sum_across_chunks_into_the_baseline_buckets():
    rows = [
        runway.task_row("c1", "t1", "resolved", {"setup_ms": 1000, "test_ms": 120000, "merge_ms": 60000, "test_runs": 2, "retries": 1, "conflicts_resolved": 1}),
        runway.task_row("c1", "t2", "merged", {"setup_ms": 2000, "test_ms": 60000, "merge_ms": 30000, "test_runs": 1}),
        runway.task_row("c2", "t3", "conflict", None),
    ]
    t = runway.ledger_totals(rows, land_ms=60000, lands_failed=1)
    assert t["tasks"] == 3
    assert t["setup_min"] == 0.05
    assert t["test_min"] == 3.0
    assert t["merge_min"] == 2.5  # 90 s of merges plus the 60 s land
    assert t["git_merge_min"] == 2.55  # setup + merge + land: the part of automated time that is git
    assert t["machine_min"] == 5.55  # every automated minute
    assert t["retries"] == 1  # only t1 re-ran its checks after a red result
    assert t["conflicts_resolved"] == 1
    assert t["human_touches"] == 2  # the stopped merge on c2, and the failed land
    assert t["review_min"] is None and t["client_min"] is None  # the helper does not measure these


def test_empty_run_totals_are_zero():
    t = runway.ledger_totals([])
    assert (t["tasks"], t["machine_min"], t["git_merge_min"], t["human_touches"]) == (0, 0, 0, 0)


def test_timing_add_sums_every_run_of_a_task(tmp_path):
    runway.timing_add("c1", "t1", test_ms=100, test_runs=1)
    rec = runway.timing_add("c1", "t1", test_ms=50, test_runs=1)
    assert rec == {"test_ms": 150, "test_runs": 2}
    assert json.load(open(runway._timing_path("c1", "t1"))) == rec
    assert not os.path.exists(runway._timing_path("c1", "t1") + ".tmp")


def test_run_checks_records_setup_and_test_time_and_counts_reruns(tmp_path):
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "echo ok"}])
    runway.run_checks("c1", "t1")
    runway.run_checks("c1", "t1")
    rec = json.load(open(runway._timing_path("c1", "t1")))
    assert rec["test_runs"] == 2
    assert rec["setup_ms"] >= 0 and rec["test_ms"] >= 0
    assert "merge_ms" not in rec  # merge timing belongs to the merge run


def test_merge_run_writes_a_ledger_with_one_row_per_task(tmp_path, monkeypatch):
    monkeypatch.setattr(runway, "tick", lambda *a, **k: None)
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "echo ok"}])
    start_task(repo, "t2", chunk="c1")
    write(os.path.join(repo, ".claude", "worktrees", "wt-t1"), {"one.txt": "one\n"})
    write(os.path.join(repo, ".claude", "worktrees", "wt-t2"), {"two.txt": "two\n"})
    os.makedirs(runway.MERGES, exist_ok=True)
    spec = {"id": "m1", "state": "queued", "resolve": False, "kind": "merge", "lands": [], "results": {}, "createdAt": 0,
            "items": [{"ch": "c1", "tid": "t1"}, {"ch": "c1", "tid": "t2"}]}
    with open(os.path.join(runway.MERGES, "m1.json"), "w") as f:
        json.dump(spec, f)
    runway.merge_batch_run("m1")
    state = json.load(open(os.path.join(runway.MERGES, "m1.json")))
    assert state["state"] == "done" and "ledgerError" not in state
    led = ledger("m1")
    assert [r["tid"] for r in led["tasks"]] == ["t1", "t2"]
    assert [r["outcome"] for r in led["tasks"]] == ["merged", "merged"]
    assert all(r["setup_ms"] >= 0 and r["test_ms"] >= 0 and r["merge_ms"] >= 0 for r in led["tasks"])
    assert led["totals"]["tasks"] == 2
    assert led["totals"]["human_touches"] == 0
    assert led["totals"]["machine_min"] >= led["totals"]["git_merge_min"] >= 0
    assert led["startedAt"] and led["doneAt"] >= led["startedAt"]


def test_merging_again_after_the_worktree_is_gone_reports_already_merged(tmp_path, monkeypatch):
    """A card that missed its merge (it was merged from the command line) can be merged again: the worktree is gone,
    but the run record still names the repo and base, so the batch recognises the work instead of 'Worktree not found'."""
    monkeypatch.setattr(runway, "tick", lambda *a, **k: None)
    repo = fake_project(tmp_path, [{"name": "unit", "cmd": "echo ok"}])
    wt = os.path.join(repo, ".claude", "worktrees", "wt-t1")
    write(wt, {"one.txt": "one\n"})
    os.makedirs(runway.MERGES, exist_ok=True)

    def batch(bid):
        spec = {"id": bid, "state": "queued", "resolve": False, "kind": "merge", "lands": [], "results": {}, "createdAt": 0,
                "items": [{"ch": "c1", "tid": "t1"}]}
        with open(os.path.join(runway.MERGES, bid + ".json"), "w") as f:
            json.dump(spec, f)
        runway.merge_batch_run(bid)
        return json.load(open(os.path.join(runway.MERGES, bid + ".json")))["results"]["t1"]

    assert batch("m1")["outcome"] == "merged"
    if os.path.isdir(wt):  # a successful merge normally removes it already
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], check=True, capture_output=True)
    assert not os.path.isdir(wt)
    again = batch("m2")
    assert again["outcome"] == "merged" and "Already merged" in again["msg"]
