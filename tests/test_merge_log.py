"""Merge-train failure log: append-only JSON lines, truncated stderr with a marker, last-N reader grouped by chunk.
Every path is a temp file (or the isolated BASE from conftest), never the real ~/.ai-planner."""
import json, os

import pytest

import runway


def test_append_writes_one_line_per_failure_with_documented_fields(tmp_path):
    path = str(tmp_path / "merge-train.log")
    runway.log_failure("chunk-1", "runway/task-a", "test", 1, "boom\nsecond line\n", path=path, now=0)
    lines = open(path, encoding="utf-8").read().split("\n")
    assert lines[-1] == ""  # trailing newline, nothing after it
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec == {"ts": "1970-01-01T00:00:00Z", "chunk": "chunk-1", "branch": "runway/task-a", "phase": "test",
                   "exit_code": 1, "stderr_tail": "boom\nsecond line\n"}


def test_append_only_keeps_earlier_lines_byte_for_byte(tmp_path):
    path = str(tmp_path / "merge-train.log")
    runway.log_failure("chunk-1", "b1", "create", 128, "fatal: a", path=path, now=1)
    first = open(path, "rb").read()
    runway.log_failure("chunk-2", "b2", "merge", 1, "CONFLICT", path=path, now=2)
    assert open(path, "rb").read().startswith(first)
    assert len(open(path, encoding="utf-8").read().splitlines()) == 2


def test_rejects_unknown_phase(tmp_path):
    with pytest.raises(ValueError):
        runway.log_failure("chunk-1", "b", "deploy", 1, "", path=str(tmp_path / "x.log"))


def test_huge_stderr_is_truncated_with_marker_and_stays_one_line(tmp_path):
    path = str(tmp_path / "merge-train.log")
    big = "x" * 500000 + "THE-END"
    runway.log_failure("chunk-1", "b", "test", 2, big + "\n" + "y" * 10, path=path, now=0)
    raw = open(path, encoding="utf-8").read()
    assert raw.count("\n") == 1  # JSON escapes the embedded newlines, so the entry is one line
    tail = json.loads(raw)["stderr_tail"]
    assert tail.startswith("[...truncated, ")
    assert "of %d chars dropped]" % len(big + "\n" + "y" * 10) in tail
    assert tail.endswith("THE-END\n" + "y" * 10)
    assert len(tail) < runway.MERGE_LOG_TAIL + 100


def test_short_stderr_is_not_marked(tmp_path):
    rec = runway.log_failure("chunk-1", "b", "test", 1, "small", path=str(tmp_path / "l.log"), now=0)
    assert rec["stderr_tail"] == "small"


def test_reader_skips_malformed_lines_and_returns_last_n(tmp_path):
    path = str(tmp_path / "merge-train.log")
    for i in range(5):
        runway.log_failure("chunk-%d" % i, "b%d" % i, "test", i, "", path=path, now=i)
    with open(path, "a", encoding="utf-8") as f:
        f.write("not json\n{\"phase\": \"other\"}\n")
    got = runway.read_failures(path, n=2)
    assert [r["chunk"] for r in got] == ["chunk-3", "chunk-4"]
    assert len(runway.read_failures(path)) == 5
    assert runway.read_failures(path, n=0) == []
    assert runway.read_failures(str(tmp_path / "missing.log")) == []


def test_formatter_groups_last_n_failures_by_chunk(tmp_path):
    path = str(tmp_path / "merge-train.log")
    runway.log_failure("chunk-a", "runway/t1", "create", 128, "fatal: no branch", path=path, now=0)
    runway.log_failure("chunk-b", "runway/t2", "test", 1, "FAIL test_x\nAssertionError", path=path, now=60)
    runway.log_failure("chunk-a", "runway/t3", "merge", 1, "CONFLICT in app.js", path=path, now=120)
    text = runway.format_failures(runway.read_failures(path, n=3))
    assert text.index("chunk-a  (2 failures)") < text.index("chunk-b  (1 failure)")
    assert text.count("chunk-a") == 1  # grouped, not repeated per failure
    assert "1970-01-01T00:01:00Z  test   exit 1  runway/t2" in text
    assert "      AssertionError" in text
    assert "      CONFLICT in app.js" in text


def test_formatter_window_limits_to_last_n(tmp_path):
    path = str(tmp_path / "merge-train.log")
    for i in range(4):
        runway.log_failure("chunk-%d" % i, "b", "test", 1, "", path=path, now=i)
    text = runway.format_failures(runway.read_failures(path, n=2))
    assert "chunk-0" not in text and "chunk-1" not in text
    assert "chunk-2  (1 failure)" in text and "chunk-3  (1 failure)" in text


def test_formatter_empty_state():
    assert runway.format_failures([]) == "No merge-train failures logged."


def test_default_path_follows_base_and_env_overrides_it(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNWAY_MERGE_LOG", raising=False)
    assert runway.merge_log_path() == os.path.join(runway.BASE, "merge-train.log")
    custom = str(tmp_path / "elsewhere" / "log.jsonl")
    monkeypatch.setenv("RUNWAY_MERGE_LOG", custom)
    assert runway.merge_log_path() == custom
    runway.log_failure("chunk-1", "b", "merge", 1, "x")
    assert os.path.exists(custom)


def test_cli_prints_last_n_grouped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNWAY_MERGE_LOG", str(tmp_path / "m.log"))
    runway.log_failure("chunk-1", "runway/t1", "test", 1, "nope", now=0)
    runway.main(["merge-log", "5"])
    out = capsys.readouterr().out
    assert "chunk-1  (1 failure)" in out and "nope" in out
    runway.main(["merge-log"])
    assert "chunk-1" in capsys.readouterr().out
