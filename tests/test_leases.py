"""Leases, the hard timeout and the reaper (~/Projects/video-studio/JOBS-CONTRACT.md): a task is Running only while its
run's process group lives and renews the lease; a dead run's open tasks get exactly one terminal state."""
import json, os, signal, subprocess, sys, time

import runway


def _run_dir(ch, tid, pg, state="running", started=None, meta=None, timeout=None):
    d = os.path.join(runway.RUNS, ch)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "_pgid"), "w").write(str(pg))
    open(os.path.join(d, tid + ".status"), "w").write(state)
    open(os.path.join(d, tid + ".start"), "w").write(str(int(started or time.time())))
    if meta:
        open(os.path.join(d, tid + ".meta"), "w").write(json.dumps(meta))
    if timeout:
        open(os.path.join(d, "_timeout"), "w").write(str(timeout))
    return d


def _dead_pid():
    p = subprocess.Popen(["true"], start_new_session=True)
    p.wait()
    return p.pid


def test_live_run_renews_its_lease(tmp_path):
    p = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        d = _run_dir("ch1", "t1", p.pid)
        runway.lease_reap()
        lease = json.load(open(os.path.join(d, "t1.lease.json")))
        assert lease["alive"] and lease["until"] > time.time() + 60
        assert runway.chunk_status("ch1")["tasks"]["t1"]["lease"]["until"] == lease["until"]
    finally:
        p.kill()


def test_dead_run_is_requeued_then_needs_austin():
    d = _run_dir("ch1", "t1", _dead_pid())
    acts = runway.lease_reap()
    assert [a["action"] for a in acts] == ["requeued"]
    st = runway.chunk_status("ch1")
    assert st["stopped"] and st["tasks"]["t1"]["state"] == "reaped"
    assert st["tasks"]["t1"]["terminal"]["state"] == "requeued" and st["tasks"]["t1"]["terminal"]["nextAttempt"] == 2
    assert "lease" not in st["tasks"]["t1"]
    assert runway.lease_reap() == []  # exactly once
    _run_dir("ch2", "t1", _dead_pid())  # the relaunch dies too
    assert [a["action"] for a in runway.lease_reap()] == ["needs-austin"]
    t = runway.chunk_status("ch2")["tasks"]["t1"]["terminal"]
    assert t["state"] == "needs-austin" and "died 2 times" in t["ask"]


def test_finished_runs_are_left_alone():
    d = _run_dir("ch1", "t1", _dead_pid(), state="done")
    open(os.path.join(d, "_chunk.done"), "w").close()
    assert runway.lease_reap() == []


def test_hard_timeout_interrupts_the_agent(tmp_path):
    repo, wt = tmp_path / "repo", "runway-t1"
    folder = repo / ".claude" / "worktrees" / wt
    folder.mkdir(parents=True)
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text("#!%s\nimport time\nwhile True: time.sleep(0.2)\n" % sys.executable)
    fake.chmod(0o755)
    # SIGINT back to its default in the stand-in: a test run started in the background (run.sh's checks, `cmd &` in a
    # script) inherits SIGINT as ignored, and Python then never installs its KeyboardInterrupt handler.
    p = subprocess.Popen([str(fake)], cwd=str(folder), start_new_session=True,
                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    try:
        d = _run_dir("ch1", "t1", p.pid, started=time.time() - 30, meta={"repo": str(repo), "wt": wt}, timeout=5)
        # Wait until ps and lsof see the stand-in in its worktree (slow under load) instead of a fixed sleep.
        end = time.time() + 30
        while runway._task_agent_pids(p.pid, str(folder)) != [p.pid] and time.time() < end:
            time.sleep(0.1)
        acts = runway.lease_reap()
        assert acts and acts[0]["action"] == "timeout" and acts[0]["pids"] == [p.pid]
        assert acts[0]["signal"] == int(signal.SIGINT)
        p.wait(timeout=30)  # SIGINT ended it
        assert os.path.exists(os.path.join(d, "t1.timedout"))
        open(os.path.join(d, "t1.status"), "w").write("done")
        open(os.path.join(d, "t1.exit"), "w").write("130")
        assert runway.chunk_status("ch1")["tasks"]["t1"]["terminal"]["state"] == "handed-back"
    finally:
        if p.poll() is None:
            p.kill()


def test_launch_resets_ignored_sigint_for_the_agent(tmp_path):
    """A helper started in the background has SIGINT ignored; run.sh and its agents must still stop on SIGINT."""
    d = os.path.join(runway.RUNS, "chint")
    os.makedirs(d)
    agent = os.path.join(d, "agent.py")
    open(agent, "w").write("import time\nopen('agent.up', 'w').close()\nwhile True: time.sleep(0.2)\n")
    open(os.path.join(d, "run.sh"), "w").write("#!/bin/bash\n%s agent.py\n" % sys.executable)
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent, as under nohup ... &
    try:
        assert runway.launch("chint") == "launched"
    finally:
        signal.signal(signal.SIGINT, old)
    pg = int(open(os.path.join(d, "_pgid")).read())
    try:
        end = time.time() + 30
        while not os.path.exists(os.path.join(d, "agent.up")) and time.time() < end:
            time.sleep(0.1)
        assert os.path.exists(os.path.join(d, "agent.up"))
        time.sleep(0.5)  # the interpreter is past its signal setup
        os.killpg(pg, signal.SIGINT)
        end = time.time() + 5
        while time.time() < end:
            if os.waitpid(pg, os.WNOHANG)[0] == pg:  # run.sh's caffeinate leader exited (and is reaped)
                break
            time.sleep(0.1)
        else:
            raise AssertionError("SIGINT did not stop the launched run")
    finally:
        try:
            os.killpg(pg, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # already gone (a PermissionError is a not-yet-reaped zombie)
            pass


def test_terminal_of():
    assert runway.terminal_of({"exit": 0, "is_error": False})["state"] == "done"
    t = runway.terminal_of({"exit": 0, "is_error": False, "denials": [{"tool": "Bash", "input": "{\"command\": \"gh pr\"}"}], "diffstat": {"files": 0}})
    assert t["state"] == "needs-austin" and t["blockingTool"] == "Bash"
    assert runway.terminal_of({"exit": 0, "outcome": {"status": "blocked", "blockers": [{"type": "decision", "detail": "A or B?"}]}})["ask"] == "A or B?"
    assert runway.terminal_of({"exit": 1, "is_error": True, "err": "boom"})["state"] == "failed"
    assert runway.terminal_of({"exit": 1, "budget": True})["state"] == "handed-back"
