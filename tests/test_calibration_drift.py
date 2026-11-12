"""Calibration feedback from autopilot: each finished task writes a calibration record (estimate, effective tokens, model,
kind) to its run's budget ledger, the multipliers at generation time are kept per roadmap, and a multiplier that moved more
than CALIB_DRIFT since then is flagged in the run report. Real git runs in temp repos; the agent is the fake from
test_autopilot_stops.py."""
import json

import pytest

import runway
from test_autopilot_stops import Agent, make_repo, run_loop, SONNET, task, save


def entry(model, kind, factor, n=3):
    return {"model": model, "kind": kind, "n": n, "factor": factor}


def run_of(repo):
    return runway._ap_read(runway._ap_paths(repo)[0])["run"]


def docs_task(key, est=100000):
    t = task(key, est=est, model="claude-sonnet-5")
    t["kind"] = "docs"
    return t


def save_with_provenance(repo, generated_at, *tasks):
    runway.save_roadmap(repo, {"version": 1, "project": "site", "title": "Site",
                               "provenance": {"briefHash": "h", "generatedAt": generated_at, "generatorModel": "m"},
                               "pis": [{"key": "pi-1", "title": "PI", "sprints": [{"key": "s-1", "title": "S",
                                                                                   "tasks": list(tasks)}]}]})


def test_a_finished_task_writes_a_calibration_record_with_estimate_tokens_model_and_kind(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, docs_task("t-1", est=100000))
    run_loop(repo, Agent(repo, usage=SONNET))
    (row,) = runway.budget_rows(run_of(repo))
    assert (row["tid"], row["est"], row["eff"], row["model"], row["kind"]) == ("t-1", 100000, 300000, "claude-sonnet-5", "docs")
    assert runway.calibration_rows()[-1]["kind"] == "docs"


def test_a_task_with_no_usage_writes_no_calibration_record(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, docs_task("t-1"))
    run_loop(repo, Agent(repo, usage=None))
    assert runway.budget_rows(run_of(repo)) == []


def test_drift_flags_a_multiplier_that_moved_more_than_a_quarter_either_way():
    gen = [entry("sonnet", "docs", 1.0), entry("sonnet", "code", 1.0), entry("opus", "code", 1.0)]
    now = [entry("sonnet", "docs", 1.3), entry("sonnet", "code", 0.7), entry("opus", "code", 1.2)]
    got = {(d["model"], d["kind"]): d for d in runway.calibration_drift(gen, now)}
    assert got[("sonnet", "docs")]["flagged"] and got[("sonnet", "docs")]["moved"] == pytest.approx(0.3)
    assert got[("sonnet", "code")]["flagged"] and got[("sonnet", "code")]["moved"] == pytest.approx(-0.3)
    assert not got[("opus", "code")]["flagged"] and got[("opus", "code")]["moved"] == pytest.approx(0.2)


def test_drift_is_not_flagged_at_exactly_a_quarter():
    got = runway.calibration_drift([entry("sonnet", "docs", 1.0)], [entry("sonnet", "docs", 1.25)])
    assert got[0]["moved"] == pytest.approx(0.25) and not got[0]["flagged"]


def test_a_pair_the_roadmap_had_no_row_for_was_measured_against_one():
    got = runway.calibration_drift([], [entry("sonnet", "docs", 1.6)])
    assert got[0]["generated"] == 1.0 and got[0]["flagged"]


def test_drift_is_none_without_a_generation_time_snapshot_and_sorted_largest_first():
    assert runway.calibration_drift(None, [entry("sonnet", "docs", 2.0)]) is None
    got = runway.calibration_drift([entry("a", "x", 1.0), entry("b", "x", 1.0)], [entry("a", "x", 1.1), entry("b", "x", 2.0)])
    assert [d["model"] for d in got] == ["b", "a"]


def test_snapshots_are_kept_per_generation_and_only_the_newest_are_kept(tmp_path):
    repo = make_repo(tmp_path)
    assert runway.calibration_snapshot(repo, 5) is None
    runway.calibration_snapshot_save(repo, 5, [entry("sonnet", "docs", 1.5)])
    assert runway.calibration_snapshot(repo, 5) == [{"model": "sonnet", "kind": "docs", "factor": 1.5}]
    for g in range(100, 100 + runway.CALIB_SNAPSHOTS + 5):
        runway.calibration_snapshot_save(repo, g, [])
    assert runway.calibration_snapshot(repo, 5) is None
    assert runway.calibration_snapshot(repo, 100 + runway.CALIB_SNAPSHOTS + 4) == []


def test_a_run_report_flags_drift_against_the_roadmap_generation_snapshot(tmp_path):
    repo = make_repo(tmp_path)
    save_with_provenance(repo, 1234, docs_task("t-1"), docs_task("t-2"), docs_task("t-3"))
    runway.calibration_snapshot_save(repo, 1234, [entry("claude-sonnet-5", "docs", 1.0)])
    run_loop(repo, Agent(repo, usage=SONNET))  # three runs at ×3 measured, clamped to ×2: a move of +100% from ×1
    md, js = runway.ap_report_paths(run_of(repo))
    rep = json.load(open(js))
    (d,) = rep["drift"]
    assert d["flagged"] and d["generated"] == 1.0 and d["current"] == 2.0 and d["n"] == 3
    assert any(a["text"].startswith("Calibration drift: claude-sonnet-5 · docs") for a in rep["attention"])
    text = open(md).read()
    assert "## Calibration drift" in text and "| claude-sonnet-5 | docs | ×1.00 | ×2.00 | 3 | +100% | **drift** |" in text


def test_a_run_report_without_a_snapshot_says_nothing_about_drift(tmp_path):
    repo = make_repo(tmp_path)
    save_with_provenance(repo, 1234, docs_task("t-1"))
    run_loop(repo, Agent(repo, usage=SONNET))
    md, js = runway.ap_report_paths(run_of(repo))
    assert json.load(open(js))["drift"] is None
    assert "Calibration drift" not in open(md).read()


def test_the_multipliers_command_prints_each_model_and_kind_with_its_value_and_sample_count(isolated_home):
    import subprocess, sys
    for i in range(3):
        runway.budget_append("r1", "c1", "t%d" % i, "claude-sonnet-5", {"input": 1500, "output": 0, "cache_write": 0, "cache_read": 0},
                             est=1000, kind="docs")
    runway.budget_append("r1", "c1", "t9", "haiku", {"input": 900, "output": 0, "cache_write": 0, "cache_read": 0}, est=1000, kind="code")
    out = subprocess.run([sys.executable, runway.__file__, "calibration"], env={"HOME": str(isolated_home), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert out[0].split() == ["model", "·", "kind", "multiplier", "runs"]
    assert out[1].split()[:4] == ["claude-sonnet-5", "·", "docs", "×1.50"] and out[1].split()[-1] == "3"
    assert out[2].split()[:4] == ["haiku", "·", "code", "×1.00"] and "(×1 until 3 runs)" in out[2]
    assert json.loads(subprocess.run([sys.executable, runway.__file__, "calibration", "--json"],
                                     env={"HOME": str(isolated_home), "PATH": "/usr/bin:/bin"},
                                     capture_output=True, text=True, check=True).stdout)["min"] == 3


def test_the_multipliers_text_says_so_when_there_are_no_records():
    assert "No calibration records yet" in runway.calibration_text(runway.calibration_report())
