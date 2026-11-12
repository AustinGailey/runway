"""Calibration: a multiplier per (model, kind) from completed runs (actual ÷ estimate in effective tokens), ×1 until three
usable runs exist, never outside ×0.5–×2. Pure maths on rows, plus the ledger reader. Files live under conftest's temp HOME."""
import pytest

import runway


def run(model, kind, est, eff, at=0):
    return {"model": model, "kind": kind, "est": est, "eff": eff, "at": at}


def row(table, model, kind):
    return next(x for x in table if x["model"] == model and x["kind"] == kind)


def test_empty_history_has_no_table_and_every_pair_is_one():
    assert runway.calibration_table([]) == []
    assert runway.calibration_factor([], "sonnet", "code") == {"model": "sonnet", "kind": "code", "n": 0, "factor": 1.0}


def test_runs_without_an_estimate_or_tokens_are_not_usable():
    rows = [run("sonnet", "code", 0, 1000), run("sonnet", "code", 1000, 0), run("sonnet", "code", None, 500)]
    assert runway.calibration_table(rows) == []


def test_sparse_history_falls_back_to_one_until_three_usable_runs():
    two = [run("sonnet", "code", 1000, 3000), run("sonnet", "code", 1000, 3000)]
    assert row(runway.calibration_table(two), "sonnet", "code") == {"model": "sonnet", "kind": "code", "n": 2, "factor": 1.0}
    three = two + [run("sonnet", "code", 1000, 3000)]
    assert row(runway.calibration_table(three), "sonnet", "code")["factor"] == 2.0


def test_rich_history_is_computed_per_model_and_kind_with_sample_counts():
    rows = [run("sonnet", "code", 1000, 1500, i) for i in range(4)]
    rows += [run("sonnet", "docs", 1000, 800, i) for i in range(3)]
    rows += [run("opus", "code", 1000, 1000, i) for i in range(5)]
    rows += [run("haiku", "ops", 1000, 4000, i) for i in range(2)]
    table = runway.calibration_table(rows)
    assert row(table, "sonnet", "code") == {"model": "sonnet", "kind": "code", "n": 4, "factor": 1.5}
    assert row(table, "sonnet", "docs") == {"model": "sonnet", "kind": "docs", "n": 3, "factor": 0.8}
    assert row(table, "opus", "code") == {"model": "opus", "kind": "code", "n": 5, "factor": 1.0}
    assert row(table, "haiku", "ops")["factor"] == 1.0 and row(table, "haiku", "ops")["n"] == 2
    assert [x["n"] for x in table] == sorted([x["n"] for x in table], reverse=True)


def test_geometric_mean_is_used_not_the_arithmetic_mean():
    rows = [run("sonnet", "code", 1000, 500), run("sonnet", "code", 1000, 2000), run("sonnet", "code", 1000, 1000)]
    assert row(runway.calibration_table(rows), "sonnet", "code")["factor"] == 1.0


def test_multipliers_are_clamped_to_half_and_two():
    high = [run("sonnet", "code", 1000, 50000)] * 3
    low = [run("sonnet", "code", 50000, 1000)] * 3
    assert row(runway.calibration_table(high), "sonnet", "code")["factor"] == 2.0 == runway.CALIB_HI
    assert row(runway.calibration_table(low), "sonnet", "code")["factor"] == 0.5 == runway.CALIB_LO


def test_only_the_latest_twenty_usable_runs_count():
    old = [run("sonnet", "code", 1000, 4000)] * 30  # ×4 but clamped; the latest 20 are what matter
    recent = [run("sonnet", "code", 1000, 1000)] * 20
    got = row(runway.calibration_table(old + recent), "sonnet", "code")
    assert got == {"model": "sonnet", "kind": "code", "n": 20, "factor": 1.0}


def test_calibrate_applies_factor_and_lookup_defaults_to_one():
    table = runway.calibration_table([run("sonnet", "code", 1000, 1500)] * 3)
    assert runway.calibration_factor(table, "sonnet", "code")["factor"] == 1.5
    assert runway.calibration_factor(table, "sonnet", "docs") == {"model": "sonnet", "kind": "docs", "n": 0, "factor": 1.0}
    assert runway.calibration_factor(table, "opus", "code")["n"] == 0


def test_rows_from_every_ledger_on_this_mac_are_read_oldest_first():
    runway.budget_append("r1", "c1", "t1", "sonnet", {"input": 1000, "output": 500, "cache_write": 0, "cache_read": 0}, est=1000, kind="code")
    runway.budget_append("r2", "c1", "t2", "sonnet", {"input": 2000, "output": 0, "cache_write": 0, "cache_read": 0}, est=1000, kind="code")
    runway.budget_append("r2", "c1", "t3", "haiku", {"input": 900, "output": 0, "cache_write": 0, "cache_read": 0}, est=1000, kind="docs")
    rows = runway.calibration_rows()
    assert len(rows) == 3 and [r["at"] for r in rows] == sorted(r["at"] for r in rows)
    assert {(r["model"], r["kind"]) for r in rows} == {("sonnet", "code"), ("haiku", "docs")}


def test_budget_record_task_stores_the_kind_and_the_report_has_the_table(tmp_path):
    import json, os
    rec = {"usage": {"input_tokens": 2000, "output_tokens": 500}}
    os.makedirs(os.path.join(runway.RUNS, "c1"), exist_ok=True)
    json.dump(rec, open(os.path.join(runway.RUNS, "c1", "t1.json"), "w"))
    row_ = runway.budget_record_task("r1", "c1", "t1", "sonnet", est=1000, kind="code")
    assert row_["kind"] == "code"
    for n in range(2):
        runway.budget_append("r1", "c1", "x%d" % n, "sonnet", {"input": 2000, "output": 500, "cache_write": 0, "cache_read": 0}, est=1000, kind="code")
    rep = runway.calibration_report()
    assert row(rep["table"], "sonnet", "code") == {"model": "sonnet", "kind": "code", "n": 3, "factor": 2.0}
    assert rep["min"] == 3 and rep["lo"] == 0.5 and rep["hi"] == 2.0


def test_no_ledgers_gives_an_empty_report():
    assert runway.calibration_report()["table"] == []
