"""The autopilot's run report, in helper/runway.py: written on every stop path, one row per attempted task with its estimate
and dollars, totals that reconcile with the budget ledger, a list of what needs a person, and the latest reports for the
Runs page. The rendering is pinned by a golden file. Real git runs in temp repos; the agent is the fake from
test_autopilot_stops.py."""
import json, os

import pytest

import runway
from test_autopilot_stops import Agent, make_repo, run_loop, save, seed_run, task, worktree, SONNET

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "autopilot-report")


def report_of(repo, run):
    md, js = runway.ap_report_paths(run)
    return open(md).read(), json.loads(open(js).read())


def run_id_of(repo):
    return runway._ap_read(runway._ap_paths(repo)[0])["run"]


def assert_reconciled(repo, run, rep):
    rows = runway.budget_rows(run)
    assert rep["reconcile"]["ledgerRows"] == len(rows)
    assert rep["reconcile"]["ok"] is True
    assert rep["totals"]["usd"] == pytest.approx(sum(r["usd"] for r in rows if r["tid"] in {t["key"] for t in rep["tasks"]}), abs=1e-4)
    assert rep["spend"] == pytest.approx(sum(r["usd"] for r in rows), abs=1e-4)


def test_the_report_renders_to_the_golden_file():
    rep = json.load(open(os.path.join(FIXTURES, "report.json")))
    golden = open(os.path.join(FIXTURES, "report.golden.md")).read()
    assert runway.ap_report_md(rep) == golden


def test_a_cap_stop_mid_run_writes_a_report_with_each_task_estimate_and_dollars(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1", est=100000, model="claude-sonnet-5"), task("t-2", est=100000, model="claude-sonnet-5"))
    seed_run(repo, "r-cap", cap=0.5)  # t-1 has no projection, so it starts; its $0.90 then puts t-2 over the $0.50 cap
    agent = Agent(repo, usage=SONNET)
    run_loop(repo, agent, resume=True)
    md, rep = report_of(repo, "r-cap")
    assert rep["stop"]["reason"] == "cap"
    assert rep["counts"] == {"attempted": 1, "merged": 1, "blocked": 0, "review": 0, "running": 0, "left": 1}
    (row,) = rep["tasks"]
    assert (row["key"], row["outcome"], row["est"], row["eff"]) == ("t-1", "merged", 100000, 300000)
    assert row["ratio"] == 3.0 and row["usd"] == pytest.approx(0.9, abs=1e-4)
    assert rep["totals"]["est"] == 100000 and rep["totals"]["eff"] == 300000
    assert rep["capPct"] == pytest.approx(180.0, abs=0.1)
    assert any("Not started (1): t-2" in a["text"] for a in rep["attention"])
    assert "| t-1: Task t-1 | merged | 100,000 | 300,000 | ×3.00 |" in md
    assert "Stopped: **dollar cap reached**" in md
    assert_reconciled(repo, "r-cap", rep)


def test_a_failure_halt_writes_a_report_that_says_why_and_what_is_blocked(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1", est=100000, model="claude-sonnet-5"), task("t-2", est=100000, model="claude-sonnet-5"))
    agent = Agent(repo, fail={"t-1"}, usage=SONNET)
    run_loop(repo, agent, max_failures=1)
    md, rep = report_of(repo, run_id_of(repo))
    assert rep["stop"]["reason"] == "failures"
    assert rep["counts"]["blocked"] == 1 and rep["counts"]["attempted"] == 1
    assert [a["text"] for a in rep["attention"]][0].startswith("The run halted after repeated blocks:")
    assert any(a["task"] == "t-1" and a["text"].startswith("Blocked: The agent run exited 1") for a in rep["attention"])
    assert "Stopped: **halted after repeated blocks**" in md
    assert "| t-1: Task t-1 | blocked (error) |" in md
    assert_reconciled(repo, run_id_of(repo), rep)


def test_a_clean_finish_writes_a_report_with_nothing_for_a_person(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1", est=100000, model="claude-sonnet-5"))
    agent = Agent(repo, usage=SONNET)
    run_loop(repo, agent)
    md, rep = report_of(repo, run_id_of(repo))
    assert rep["stop"]["reason"] == "roadmap"
    assert rep["attention"] == []
    assert "Nothing needs a person." in md
    assert rep["resumed"] is False and rep["wallMin"] >= 0
    assert_reconciled(repo, run_id_of(repo), rep)


def test_an_interrupted_run_writes_a_report_and_the_interruption_propagates(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1", est=100000, model="claude-sonnet-5"))
    agent = Agent(repo, crash_on="t-1", usage=SONNET)
    with pytest.raises(KeyboardInterrupt):
        run_loop(repo, agent)
    md, rep = report_of(repo, run_id_of(repo))
    assert rep["stop"]["reason"] == "interrupted"
    assert "The run was interrupted" in rep["attention"][0]["text"]
    assert rep["counts"]["running"] == 1  # the task in flight is said so, and keeps its worktree for a resume
    assert os.path.isdir(worktree(repo, "t-1"))


def test_merge_time_is_recorded_for_the_merge_train_buckets(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1", est=100000, model="claude-sonnet-5"))
    run_loop(repo, Agent(repo, usage=SONNET))
    timing = json.loads(open(runway._timing_path("ap-t-1", "t-1")).read())
    assert "merge_ms" in timing
    _, rep = report_of(repo, run_id_of(repo))
    assert rep["totals"]["merge_min"] == pytest.approx(timing["merge_ms"] / 60000, abs=0.005)


def test_the_latest_reports_are_listed_for_the_runs_page(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("t-1", est=100000, model="claude-sonnet-5"))
    run_loop(repo, Agent(repo, usage=SONNET))
    run = run_id_of(repo)
    (listed,) = runway.ap_reports_status()
    assert listed["run"] == run and listed["stop"] == "roadmap"
    assert listed["md"] == runway.ap_report_paths(run)[0] and os.path.isfile(listed["md"])
    assert listed["counts"]["merged"] == 1
