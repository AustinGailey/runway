"""Kill and resume the autopilot loop (helper/runway.py), against the scripted fake agent in autopilot_fake.py. The loop is
killed while a task is in flight, in two ways: the agent finished while the loop was down, or the agent died with it. A resume
must never start a task that already ran: a finished run is merged from what it left, and a dead one is blocked. Each task's
spend is counted once in the ledger. Real git runs in a temp repo, and the same teardown check as the loop tests applies."""
import os, subprocess

import pytest

import runway
from autopilot_fake import Scripted, check_no_leftovers, ledger_chunks, make_repo, run_loop, save, state, statuses, task, worktree


@pytest.fixture(autouse=True)
def no_leftovers(tmp_path):
    yield
    check_no_leftovers(tmp_path)


@pytest.mark.parametrize("finished_while_down", [True, False], ids=["finished while the loop was down", "died while the loop was down"])
def test_a_killed_loop_resumes_without_running_any_task_twice(tmp_path, finished_while_down):
    repo = make_repo(tmp_path)
    save(repo, task("t-1"), task("t-2", deps=["t-1"]), task("t-3", deps=["t-2"]))

    def kill_while_t2_runs(agent, seconds):
        if agent.pending[:1] == ["t-2"]:
            if finished_while_down:
                agent.finish(agent.pending.pop(0))  # the agent ran to the end, but nothing was there to gate it
            else:
                agent.pending.pop(0)  # the agent died with the loop
            gone = subprocess.Popen(["true"])
            gone.wait()
            open(os.path.join(runway.RUNS, "ap-t-2", "_pgid"), "w").write(str(gone.pid))  # its group no longer exists
            raise KeyboardInterrupt

    first = Scripted(repo, on_sleep=kill_while_t2_runs)
    with pytest.raises(KeyboardInterrupt):
        run_loop(repo, first)
    assert first.launched == ["t-1", "t-2"]
    assert state(repo)["stop"]["reason"] == "interrupted"
    assert statuses(repo) == {"t-1": "done", "t-2": "running", "t-3": "todo"}
    assert os.path.isdir(worktree(repo, "t-2"))  # in flight: kept for the resume

    second = Scripted(repo)
    r = run_loop(repo, second, resume=True)
    if finished_while_down:
        assert first.launched == ["t-1", "t-2"] and second.launched == ["t-3"]  # t-2 is merged from its finished run, not run again
        assert r["done"] == ["t-2", "t-3"]
        assert statuses(repo) == {"t-1": "done", "t-2": "done", "t-3": "done"}
        assert ledger_chunks(repo) == ["ap-t-1", "ap-t-2", "ap-t-3"]  # each task's spend is counted once, in launch order
    else:
        assert first.launched == ["t-1", "t-2"] and second.launched == []  # the dead run is blocked, never started again
        assert list(r["blocked"]) == ["t-2"]
        assert statuses(repo) == {"t-1": "done", "t-2": "blocked", "t-3": "todo"}
        assert ledger_chunks(repo) == ["ap-t-1"]  # the dead run reported no usage, so it has no spend
    assert state(repo)["stop"]["reason"] == "roadmap"
    assert state(repo)["resumedAt"]
