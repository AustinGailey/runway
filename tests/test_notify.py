"""Mac notifications: which events ping Austin, which stay silent, one per occurrence, respected when off, and a dry run
that posts nothing. mac_notify is replaced in every test, so nothing here reaches osascript or the real ~/.ai-planner."""
import json, os

import pytest

import runway


@pytest.fixture
def posted(monkeypatch):
    calls = []
    monkeypatch.setattr(runway, "mac_notify", lambda title, body: calls.append((title, body)))
    return calls


def make_chunk(ch="chunk-1", finished=True, limited=False):
    d = os.path.join(runway.RUNS, ch)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "t1.status"), "w") as f:
        f.write("done")
    with open(os.path.join(d, "t1.prompt.md"), "w") as f:
        f.write("TASK: Ship the thing\n")
    if limited:
        with open(os.path.join(d, "t2.status"), "w") as f:
            f.write("paused")
        with open(os.path.join(d, "t2.prompt.md"), "w") as f:
            f.write("TASK: Write the docs\n")
        with open(os.path.join(d, "t2.json"), "w") as f:
            json.dump({"is_error": True, "result": "Claude AI usage limit reached|1760000000"}, f)
    if finished:
        open(os.path.join(d, "_chunk.done"), "w").close()
    return d


def write_merge(mid, results, items=None, state="done"):
    os.makedirs(runway.MERGES, exist_ok=True)
    with open(os.path.join(runway.MERGES, mid + ".json"), "w") as f:
        json.dump({"id": mid, "state": state, "results": results, "items": items or []}, f)


def write_failure(ch, tid, at, state="red", reason="exit 1"):
    os.makedirs(runway.BASE, exist_ok=True)
    rec = {"at": at, "chunk": ch, "task": tid, "state": state, "check": "pytest", "reason": reason}
    with open(runway.FAILURES, "a") as f:
        f.write(json.dumps(rec) + "\n")


def test_chunk_finish_is_silent_by_default(posted):
    runway.tick()  # baseline: the first run only records
    make_chunk()
    runway.tick()
    runway.tick()
    assert posted == []
    assert os.path.exists(runway.NOTIFY_STATE)  # recorded, so turning routine on later does not replay it


def test_chunk_finish_posts_once_when_routine_is_on(posted):
    runway.main(["notify", "routine", "on"])
    runway.tick()
    make_chunk()
    runway.tick()
    assert posted == [("Runway chunk finished", "chunk-1: 1 of 1 tasks done")]
    runway.tick()
    runway.tick()
    assert len(posted) == 1


def test_first_run_records_existing_events_without_posting(posted):
    make_chunk(limited=True)
    runway.tick()
    assert posted == []
    assert os.path.exists(runway.NOTIFY_STATE)


def test_usage_limit_pause_still_notifies(posted):
    runway.tick()  # first run: nothing exists yet, state file is written
    make_chunk(limited=True)
    runway.tick()
    assert posted == [("Runway task paused at a usage limit", "Write the docs. Resume it after the window resets.")]


def test_limit_repeats_only_for_a_new_run(posted):
    runway.tick()
    d = make_chunk(limited=True)
    first = os.path.join(d, "t2.json")
    os.utime(first, (1_900_000_000, 1_900_000_000))
    runway.tick()
    runway.tick()
    assert [t for t, _ in posted].count("Runway task paused at a usage limit") == 1
    # a resumed run that hits the limit again writes a new output file, which is a new event
    os.utime(first, (2_000_000_000, 2_000_000_000))
    runway.tick()
    assert [t for t, _ in posted].count("Runway task paused at a usage limit") == 2


def test_conflict_notifies(posted):
    make_chunk()
    runway.tick()
    write_merge("m1", {"t1": {"outcome": "conflict", "msg": "Conflicts with approved work in app.py."}},
                items=[{"tid": "t1", "ch": "chunk-1"}], state="running")
    runway.tick()
    assert posted == [("Runway needs you: merge conflict", "Ship the thing: Conflicts with approved work in app.py.")]


def test_conflict_posts_once(posted):
    runway.tick()
    write_merge("m1", {"t2": {"outcome": "conflict", "msg": "Conflicts."}}, state="running")
    runway.tick()
    runway.tick()
    assert [t for t, _ in posted] == ["Runway needs you: merge conflict"]


def test_routine_merge_is_silent_by_default(posted):
    runway.tick()
    write_merge("m1", {"t1": {"outcome": "merged", "msg": ""}, "t2": {"outcome": "resolved", "msg": ""}}, state="running")
    runway.tick()
    assert posted == []


def test_routine_merge_posts_when_routine_is_on(posted):
    make_chunk()
    runway.main(["notify", "routine", "on"])
    runway.tick()
    write_merge("m1", {"t1": {"outcome": "merged", "msg": ""}}, items=[{"tid": "t1", "ch": "chunk-1"}], state="running")
    runway.tick()
    assert posted == [("Runway task merged", "Ship the thing merged into its base branch.")]


def test_a_single_red_run_is_silent(posted):
    runway.tick()
    write_failure("chunk-1", "t1", 1_900_000_000)
    runway.tick()
    assert posted == []


def test_second_red_run_of_a_task_notifies(posted):
    runway.tick()
    write_failure("chunk-1", "t1", 1_900_000_000)
    runway.tick()
    write_failure("chunk-1", "t1", 1_900_000_100, state="timeout", reason="timed out")
    runway.tick()
    runway.tick()
    assert len(posted) == 1
    assert posted[0][0] == "Runway task failed again"
    assert posted[0][1] == "t1 failed again (timed out)."


def test_repeat_failure_counts_per_task_not_per_chunk(posted):
    runway.tick()
    write_failure("chunk-1", "t1", 1_900_000_000)
    write_failure("chunk-1", "t2", 1_900_000_010)
    runway.tick()
    assert posted == []


def test_each_further_red_run_notifies_once(posted):
    runway.tick()
    for i, at in enumerate((1_900_000_000, 1_900_000_100, 1_900_000_200)):
        write_failure("chunk-1", "t1", at)
        runway.tick()
        runway.tick()
        assert len(posted) == i  # the first red is silent; the second and third each notify once
    assert [t for t, _ in posted] == ["Runway task failed again", "Runway task failed again"]


def test_run_complete_summary_has_merged_and_failed_counts(posted):
    runway.tick()
    write_merge("m1", {"t1": {"outcome": "merged", "msg": ""}, "t2": {"outcome": "conflict", "msg": "c"},
                       "t3": {"outcome": "red", "msg": "r"}, "t4": {"outcome": "resolved", "msg": ""}})
    runway.tick()
    assert ("Runway run complete", "2 merged, 2 failed (of 4 tasks).") in posted


def test_run_complete_posts_even_with_routine_off(posted):
    runway.tick()
    write_merge("m1", {"t1": {"outcome": "merged", "msg": ""}})
    runway.tick()
    assert posted == [("Runway run complete", "1 merged, 0 failed (of 1 tasks).")]


def test_run_in_progress_is_not_summarised(posted):
    runway.tick()
    write_merge("m1", {"t1": {"outcome": "merged", "msg": ""}}, state="running")
    runway.tick()
    assert posted == []


def test_merge_batch_done_posts_once(posted):
    runway.tick()
    write_merge("m1", {"t1": {"outcome": "merged"}, "t2": {"outcome": "conflict", "msg": "c"}})
    runway.tick()
    runway.tick()
    assert [t for t, _ in posted].count("Runway run complete") == 1


def test_off_switch_records_but_does_not_post(posted):
    runway.main(["notify", "routine", "on"])
    runway.tick()
    open(runway.NOTIFY_OFF, "w").close()
    make_chunk()
    write_failure("chunk-1", "t1", 1_900_000_000)
    write_failure("chunk-1", "t1", 1_900_000_100)
    runway.tick()
    assert posted == []
    os.remove(runway.NOTIFY_OFF)
    runway.tick()  # turning it back on does not replay what happened while it was off
    assert posted == []


def test_routine_off_records_but_does_not_post_when_turned_on_later(posted):
    runway.tick()
    make_chunk()
    runway.tick()
    runway.main(["notify", "routine", "on"])
    runway.tick()
    assert posted == []  # the chunk finished while routine was off: it stays recorded, not replayed


def test_cli_on_off_toggles_flag_file(capsys):
    runway.main(["notify", "off"])
    assert not runway.notify_enabled()
    runway.main(["notify", "on"])
    assert runway.notify_enabled()
    out = capsys.readouterr().out
    assert "@@NOTIFY_OFF" in out and "@@NOTIFY_ON" in out


def test_cli_routine_toggles_flag_file(capsys):
    assert not runway.routine_enabled()
    runway.main(["notify", "routine", "on"])
    assert runway.routine_enabled()
    runway.main(["notify", "routine", "off"])
    assert not runway.routine_enabled()
    out = capsys.readouterr().out
    assert "@@NOTIFY_ROUTINE_ON" in out and "@@NOTIFY_ROUTINE_OFF" in out


def test_dry_run_lists_events_and_changes_nothing(posted, capsys):
    make_chunk(limited=True)
    runway.main(["notify", "--dry-run"])
    out = capsys.readouterr().out
    assert "quiet Runway chunk finished: chunk-1: 1 of 2 tasks done" in out  # routine, and routine is off
    assert "new  Runway task paused at a usage limit" in out
    assert "@@DRYRUN 2 events, none posted" in out
    assert posted == []
    assert not os.path.exists(runway.NOTIFY_STATE)
    assert not os.path.exists(runway.STATUS)  # no status write either: it is read-only


def test_dry_run_marks_events_already_sent(posted, capsys):
    runway.tick()
    make_chunk()
    runway.tick()
    capsys.readouterr()
    runway.main(["notify", "--dry-run"])
    assert "sent Runway chunk finished" in capsys.readouterr().out
    assert posted == []


def test_dry_run_shows_routine_as_new_when_routine_is_on(posted, capsys):
    runway.main(["notify", "routine", "on"])
    make_chunk()
    runway.main(["notify", "--dry-run"])
    assert "new  Runway chunk finished" in capsys.readouterr().out


def test_mac_notify_passes_text_as_argv(monkeypatch):
    seen = {}
    monkeypatch.setattr(runway.subprocess, "run", lambda args, **kw: seen.update(args=args))
    runway.mac_notify("Title", 'Task "quoted" \\ and more')
    assert seen["args"][0] == "osascript"
    assert seen["args"][-2:] == ["Title", 'Task "quoted" \\ and more']
    assert "argv" in seen["args"][2]


def test_notify_failure_does_not_stop_status(monkeypatch):
    def boom(out):
        raise RuntimeError("no state")
    monkeypatch.setattr(runway, "notify", boom)
    out = runway.tick()
    assert out["notifyError"] == "no state"
    assert os.path.exists(runway.STATUS)
