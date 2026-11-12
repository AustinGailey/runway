"""Run signals and run history: every finished run as countable fields (why it ended, the agent's verdict, blockers,
denials, checks, timing), so Runway can be optimised from run data."""
import json, os, time

import runway


def run_dir(ch="ch1"):
    d = os.path.join(runway.RUNS, ch)
    os.makedirs(d, exist_ok=True)
    return d


def finish(d, tid, result="", exit_=0, files=1, extra=None, err=""):
    put = lambda name, text: open(os.path.join(d, tid + name), "w").write(text)
    put(".status", "done\n"); put(".exit", "%d\n" % exit_); put(".start", str(int(time.time()) - 60))
    put(".json", json.dumps({"type": "result", "result": result, "usage": {"input_tokens": 100, "output_tokens": 50}, "total_cost_usd": 0.4,
                             "num_turns": 3, "duration_ms": 1000, "is_error": exit_ != 0, **(extra or {})}))
    put(".patch", "".join("diff --git a/f%d b/f%d\n+x\n" % (i, i) for i in range(files)))
    if err:
        put(".err", err)


def outcome(status, blockers=()):
    return "Report.\n```runway\n" + json.dumps({"status": status, "blockers": list(blockers)}) + "\n```"


def signals(d, tid, ch="ch1"):
    return runway.chunk_status(ch)["tasks"][tid]["signals"]


def test_a_clean_run_is_ok():
    d = run_dir(); finish(d, "a", outcome("done"))
    s = signals(d, "a")
    assert s["category"] == "ok" and s["agentStatus"] == "done" and s["blockers"] == []


def test_exit_zero_but_agent_said_blocked_is_counted_as_blocked_with_its_command():
    d = run_dir(); finish(d, "a", outcome("partial", [{"type": "environment", "detail": "no node_modules", "command": "npm run build"}]))
    s = signals(d, "a")
    assert s["category"] == "blocked:environment" and s["agentStatus"] == "partial"
    assert s["blockers"][0]["command"] == "npm run build" and s["blockerTypes"] == ["environment"]


def test_no_changes_checks_red_verify_fail_crash_and_limit():
    d = run_dir()
    finish(d, "nochange", outcome("done"), files=0)
    finish(d, "red", outcome("done")); open(os.path.join(d, "red.checks.json"), "w").write(json.dumps({"state": "red"}))
    finish(d, "unmet", outcome("done")); open(os.path.join(d, "unmet.verify.json"), "w").write(json.dumps({"result": '{"pass": false, "notes": "x"}'}))
    finish(d, "unread", outcome("done")); open(os.path.join(d, "unread.verify.json"), "w").write(json.dumps({"result": '{"pass": false, "notes": "x"}'}))
    open(os.path.join(d, "unread.verify.meta.json"), "w").write(json.dumps({"unreadable": ["dist/app.html"]}))
    finish(d, "crash", "", exit_=1)
    finish(d, "limit", "You've hit your limit", exit_=1)
    got = {t: signals(d, t)["category"] for t in ("nochange", "red", "unmet", "unread", "crash", "limit")}
    assert got == {"nochange": "no-output", "red": "checks-red", "unmet": "verify-fail", "unread": "verify-unverifiable", "crash": "crash", "limit": "limit"}


def test_denials_are_counted_by_command_even_when_the_input_was_cut_short():
    d = run_dir()
    long_cmd = "cd /x && npm install " + "y" * 400
    finish(d, "a", outcome("done"), extra={"permission_denials": [
        {"tool_name": "Bash", "tool_input": {"command": long_cmd}}, {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x"}}]})
    s = signals(d, "a")
    assert s["denials"] == 2 and s["deniedTools"] == ["Bash(npm install)", "Write"]


def test_a_denied_read_outside_the_repo_is_kept_as_a_read_path():
    d = run_dir()
    open(os.path.join(d, "a.meta"), "w").write(json.dumps({"repo": "/Users/dev/Projects/runway"}))
    finish(d, "a", outcome("done"), extra={"permission_denials": [
        {"tool_name": "Read", "tool_input": {"file_path": "/Users/dev/vault/_Claude/plan.md"}}]})
    s = signals(d, "a")
    assert s["readPaths"] == ["/Users/dev/vault/_Claude/plan.md"]


def test_a_cut_short_bash_read_outside_the_repo_is_still_parsed():
    d = run_dir()
    open(os.path.join(d, "a.meta"), "w").write(json.dumps({"repo": "/Users/dev/Projects/runway"}))
    long_cmd = "cat /Users/dev/sibling-repo/notes.md && " + "y" * 400
    finish(d, "a", outcome("done"), extra={"permission_denials": [
        {"tool_name": "Bash", "tool_input": {"command": long_cmd}}]})
    s = signals(d, "a")
    assert s["readPaths"] == ["/Users/dev/sibling-repo/notes.md"]


def test_a_read_path_inside_the_repo_is_dropped():
    d = run_dir()
    open(os.path.join(d, "a.meta"), "w").write(json.dumps({"repo": "/Users/dev/Projects/runway"}))
    finish(d, "a", outcome("done"), extra={"permission_denials": [
        {"tool_name": "Read", "tool_input": {"file_path": "/Users/dev/Projects/runway/src/x.html"}}]})
    assert signals(d, "a")["readPaths"] == []


def test_an_access_blocker_keeps_its_path_and_feeds_read_paths():
    d = run_dir()
    finish(d, "a", outcome("blocked", [{"type": "access", "detail": "Can't read the plan", "path": "/Users/dev/vault/_Claude/roadmap.md"}]))
    s = signals(d, "a")
    assert s["blockers"][0]["path"] == "/Users/dev/vault/_Claude/roadmap.md"
    assert s["readPaths"] == ["/Users/dev/vault/_Claude/roadmap.md"]


def test_a_missing_start_branch_is_flagged():
    d = run_dir(); finish(d, "a", outcome("done"), err="Start branch Some task title was not found in /r; started from main instead.\n")
    assert signals(d, "a")["startFallback"] is True


def test_run_history_lists_finished_runs_newest_first_with_timing_and_skips_running_ones():
    d1, d2 = run_dir("ch1"), run_dir("ch2")
    finish(d1, "a", outcome("done")); open(os.path.join(d1, "a.timing.json"), "w").write(json.dumps({"merge_ms": 5000}))
    time.sleep(1.1)
    finish(d2, "a", outcome("blocked", [{"type": "access", "detail": "x"}]))
    open(os.path.join(d2, "b.status"), "w").write("running\n")
    h = runway.run_history(0)
    assert [(r["chunk"], r["task"]) for r in h] == [("ch2", "a"), ("ch1", "a")]
    assert h[0]["category"] == "blocked:access" and h[1]["timing"] == {"merge_ms": 5000}
    assert h[1]["effTokens"] > 0 and h[1]["cost"] == 0.4 and h[1]["wallMs"] is not None
