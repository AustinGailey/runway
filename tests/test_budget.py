"""Budget ledger: pricing maths from the one price table, one appended line per run, cumulative spend and remaining
budget, and the next task's projection from its calibrated estimate. Only temp dirs: conftest isolates HOME and the paths."""
import json, os

import pytest

import runway


def tok(inp=0, out=0, cw=0, cr=0):
    return {"input": inp, "output": out, "cache_write": cw, "cache_read": cr}


def test_price_table_is_per_million_tokens_for_every_token_class():
    assert set(runway.PRICES) == {"opus", "sonnet", "haiku"}
    for fam, p in runway.PRICES.items():
        assert set(p) == set(runway.TOKEN_CLASSES), fam
        # cache reads are the cheap class, output the dear one, in every family
        assert p["cache_read"] < p["input"] < p["output"], fam


def test_usd_is_each_class_at_its_per_million_price():
    # 1M of every class on sonnet is the sum of its four prices
    p = runway.PRICES["sonnet"]
    assert runway.usd_for("sonnet", tok(1e6, 1e6, 1e6, 1e6)) == pytest.approx(sum(p.values()))
    # 2,000 input and 500 output on sonnet: 0.006 + 0.0075
    assert runway.usd_for("sonnet", tok(2000, 500)) == pytest.approx(0.0135)


def test_model_names_match_their_family_and_unknown_names_price_as_the_default():
    assert runway.price_family("claude-sonnet-5") == "sonnet"
    assert runway.price_family("opus") == "opus"
    assert runway.price_family("claude-haiku-4-5-20251001") == "haiku"
    assert runway.price_family("some-future-model") == runway.PRICE_DEFAULT
    assert runway.price_family(None) == runway.PRICE_DEFAULT
    assert runway.usd_for("some-future-model", tok(1000)) == runway.usd_for("sonnet", tok(1000))


def test_usage_block_maps_to_the_four_token_classes_and_effective_tokens():
    u = {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 30, "cache_read_input_tokens": 400}
    t = runway.usage_tokens(u)
    assert t == tok(10, 20, 30, 400)
    assert runway.eff_tokens(t) == pytest.approx(10 + 20 + 30 + 40)  # a tenth of cache reads, as the page counts


def test_budget_append_writes_one_line_per_run_with_tokens_dollars_and_model():
    row = runway.budget_append("r1", "c1", "t1", "sonnet", tok(2000, 500), est=900)
    rows = runway.budget_rows("r1")
    assert rows == [row]
    assert row["model"] == "sonnet" and row["family"] == "sonnet"
    assert row["tokens"] == tok(2000, 500)
    assert row["usd"] == pytest.approx(0.0135)
    assert row["est"] == 900 and row["eff"] == 2500
    assert row["ch"] == "c1" and row["tid"] == "t1"


def test_budget_append_only_adds_lines_and_keeps_the_earlier_ones():
    runway.budget_append("r1", "c1", "t1", "sonnet", tok(1000))
    runway.budget_append("r1", "c1", "t2", "opus", tok(1000))
    lines = open(runway.budget_ledger_path("r1")).read().splitlines()
    assert len(lines) == 2
    assert [json.loads(x)["tid"] for x in lines] == ["t1", "t2"]
    assert json.loads(lines[1])["model"] == "opus"


def test_each_run_has_its_own_ledger_and_a_missing_one_is_empty():
    runway.budget_append("r1", "c1", "t1", "sonnet", tok(1000))
    assert runway.budget_rows("r2") == []
    assert len(runway.budget_rows("r1")) == 1


def test_record_appends_once_and_a_resumed_run_appends_only_what_it_added(tmp_path):
    first = {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1000}
    assert runway.budget_record("r1", "c1", "t1", "sonnet", first)["tokens"] == tok(100, 50, 0, 1000)
    assert runway.budget_record("r1", "c1", "t1", "sonnet", first) is None  # same totals again: nothing new
    more = dict(first, output_tokens=80)  # the resumed run added 30 output tokens
    row = runway.budget_record("r1", "c1", "t1", "sonnet", more)
    assert row["tokens"] == tok(0, 30, 0, 0)
    assert [r["tokens"]["output"] for r in runway.budget_rows("r1")] == [50, 30]
    assert sum(r["usd"] for r in runway.budget_rows("r1")) == pytest.approx(runway.usd_for("sonnet", tok(100, 80, 0, 1000)))


def test_record_task_reads_the_run_record_and_skips_a_task_with_no_usage(tmp_path):
    assert runway.budget_record_task("r1", "c1", "t9", "sonnet") is None
    os.makedirs(os.path.join(runway.RUNS, "c1"), exist_ok=True)
    with open(os.path.join(runway.RUNS, "c1", "t1.json"), "w") as f:
        json.dump({"usage": {"input_tokens": 1000, "output_tokens": 100}, "cost": 0.0042}, f)
    row = runway.budget_record_task("r1", "c1", "t1", "haiku", est=800)
    assert row["usd"] == pytest.approx(runway.usd_for("haiku", tok(1000, 100)))
    assert row["reportedUsd"] == 0.0042 and row["est"] == 800


def test_report_gives_cumulative_spend_and_what_is_left_under_the_cap():
    runway.budget_append("r1", "c1", "t1", "sonnet", tok(10000, 2000))
    runway.budget_append("r1", "c1", "t2", "sonnet", tok(20000, 4000))
    rep = runway.budget_report("r1", cap=1.0)
    spend = runway.usd_for("sonnet", tok(10000, 2000)) + runway.usd_for("sonnet", tok(20000, 4000))
    assert rep["runs"] == 2
    assert rep["spend"] == pytest.approx(spend, abs=1e-4)
    assert rep["remaining"] == pytest.approx(1.0 - spend, abs=1e-4)
    assert rep["tokens"] == tok(30000, 6000)


def test_no_cap_means_no_remaining_figure():
    runway.budget_append("r1", "c1", "t1", "sonnet", tok(1000))
    rep = runway.budget_report("r1")
    assert rep["cap"] is None and rep["remaining"] is None and rep["spend"] > 0


def test_projection_is_none_until_there_is_a_measured_run():
    rep = runway.budget_report("r1", cap=5, est=50000, model="sonnet")
    assert rep["next"]["usd"] is None and rep["remaining"] == 5


def test_projection_is_the_estimate_times_calibration_times_dollars_per_effective_token():
    # three measured runs that each used twice what they were estimated at: calibration ×2
    for i in range(3):
        runway.budget_append("r1", "c1", "t%d" % i, "sonnet", tok(4000, 0), est=2000)
    rows = runway.budget_rows("r1")
    assert runway.budget_calibration(rows) == {"factor": 2.0, "n": 3}
    rate = sum(r["usd"] for r in rows) / sum(r["eff"] for r in rows)
    rep = runway.budget_report("r1", est=10000, model="sonnet")
    assert rep["next"]["factor"] == 2.0 and rep["next"]["calibrated"] == 3
    assert rep["next"]["usd"] == pytest.approx(10000 * 2.0 * rate, abs=1e-4)


def test_calibration_is_one_until_three_runs_and_is_clamped():
    assert runway.budget_calibration([{"est": 100, "eff": 1000}, {"est": 100, "eff": 1000}]) == {"factor": 1.0, "n": 2}
    wild = [{"est": 100, "eff": 100000}] * 3
    assert runway.budget_calibration(wild)["factor"] == runway.CAL_MAX
    assert runway.budget_calibration([{"est": 100000, "eff": 100}] * 3)["factor"] == runway.CAL_MIN


def test_runs_without_an_estimate_do_not_calibrate_but_still_count_for_spend():
    runway.budget_append("r1", "c1", "t1", "sonnet", tok(1000))
    rep = runway.budget_report("r1", est=0)
    assert rep["runs"] == 1 and rep["spend"] > 0
    assert rep["next"]["usd"] is None and rep["next"]["calibrated"] == 0


def test_cli_prints_the_report_and_records_from_a_run_record(capsys, tmp_path):
    runway.budget_append("r1", "c1", "t1", "sonnet", tok(1000))
    runway.main(["budget", "r1", "2.5", "0", "sonnet"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["cap"] == 2.5 and rep["runs"] == 1 and rep["remaining"] == pytest.approx(2.5 - rep["spend"], abs=1e-4)
    os.makedirs(os.path.join(runway.RUNS, "c1"), exist_ok=True)
    with open(os.path.join(runway.RUNS, "c1", "t2.json"), "w") as f:
        json.dump({"usage": {"input_tokens": 500}}, f)
    runway.main(["budget-record", "r1", "c1", "t2", "opus"])
    assert json.loads(capsys.readouterr().out)["model"] == "opus"
    assert len(runway.budget_rows("r1")) == 2
