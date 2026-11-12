"""A task's environment ends with the task: its teardown command runs with the task's RUNWAY_* variables when it is merged,
discarded or stopped, and when env-gc finds it orphaned or old, and its port block goes back to the registry. Real git
runs in temp repos, and nothing here reads the real ~/.ai-planner (conftest points BASE at a temp dir)."""
import os, shlex, time

import runway
from test_autopilot import make_repo, save, task, worktree
from test_git_flows import commit, git
from test_task_env import registry_blocks, set_config


def prepare(repo, key="a", fresh=True):
    """Prepare task key the way the autopilot does: its worktree, its environment and its run folder. Returns the run folder."""
    t = next(t for t in runway.roadmap_tasks(runway.load_roadmap(repo)) if t.key == key)
    runway._ap_prepare(repo, t, "main", fresh)
    return runway._ap_dir(key)


def finish_work(repo, key="a"):
    """The agent's commit in its worktree, so there is something to merge."""
    commit(worktree(repo, key), "Runway: work", {"feature-%s.txt" % key: "new\n"})


def block_of(repo_task_key):
    return next((b for b in registry_blocks() if b["task"] == repo_task_key), None)


def test_a_merge_runs_the_teardown_with_the_task_variables_and_frees_its_ports(tmp_path):
    repo = make_repo(tmp_path)
    log = tmp_path / "teardown.txt"
    set_config(repo, {"teardown": 'echo "down $RUNWAY_TASK $RUNWAY_PORT_BASE" >> %s' % shlex.quote(str(log)), "ports": 2})
    save(repo, task("a"))
    d = prepare(repo)
    base = runway._ap_read(os.path.join(d, runway.ENV_SPEC))["ports"]["base"]
    finish_work(repo)

    out = runway.automerge("ap-a", "a")
    assert out["outcome"] == "merged", out
    assert log.read_text() == "down a %d\n" % base  # it ran before the worktree went, with the task's block
    assert block_of("a") is None
    assert not os.path.exists(worktree(repo, "a"))
    assert runway._ap_read(os.path.join(d, "a.teardown.json"))["rc"] == 0


def test_a_discard_runs_the_teardown_and_frees_its_ports(tmp_path):
    repo = make_repo(tmp_path)
    log = tmp_path / "teardown.txt"
    set_config(repo, {"teardown": 'echo "down $RUNWAY_TASK" >> %s' % shlex.quote(str(log)), "ports": 1})
    save(repo, task("a"))
    prepare(repo)
    assert block_of("a") is not None

    assert runway.discard("ap-a", "a").startswith("@@OK")
    assert log.read_text() == "down a\n"
    assert block_of("a") is None
    assert not os.path.exists(worktree(repo, "a"))


def test_stop_runs_the_teardown_frees_its_ports_and_keeps_the_worktree_so_setup_runs_again(tmp_path):
    repo = make_repo(tmp_path)
    log, setups = tmp_path / "teardown.txt", tmp_path / "setups.txt"
    set_config(repo, {"setup": "echo setup >> %s" % shlex.quote(str(setups)),
                      "teardown": "echo down >> %s" % shlex.quote(str(log)), "ports": 1})
    save(repo, task("a"))
    d = prepare(repo)

    assert runway.stop("ap-a") == "stopped"
    assert log.read_text() == "down\n"
    assert block_of("a") is None
    assert os.path.isdir(worktree(repo, "a"))  # the agent's work stays where it is

    prepare(repo, fresh=False)  # a new environment: a new block, and setup runs again
    assert setups.read_text() == "setup\nsetup\n"
    assert block_of("a") is not None
    assert not os.path.exists(os.path.join(d, runway.ENV_TORN))


def test_a_failing_teardown_is_logged_and_never_blocks_the_merge(tmp_path):
    repo = make_repo(tmp_path)
    set_config(repo, {"teardown": "echo stopping-db; exit 3", "ports": 1})
    save(repo, task("a"))
    d = prepare(repo)
    finish_work(repo)

    out = runway.automerge("ap-a", "a")
    assert out["outcome"] == "merged", out
    rec = runway._ap_read(os.path.join(d, "a.teardown.json"))
    assert rec["rc"] == 3 and not rec["timedOut"]
    assert "stopping-db" in open(os.path.join(d, "a.teardown.log")).read()
    assert "[teardown exit 3]" in open(os.path.join(d, "a.teardown.log")).read()
    assert block_of("a") is None


def test_a_project_without_environment_keys_is_left_alone_by_teardown_and_env_gc(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"))
    d = prepare(repo)
    finish_work(repo)

    assert runway.automerge("ap-a", "a")["outcome"] == "merged"
    assert not os.path.exists(os.path.join(d, runway.ENV_SPEC))
    assert not os.path.exists(os.path.join(d, "a.teardown.json"))
    assert not os.path.exists(os.path.join(runway.BASE, runway.PORTS_FILE))
    assert runway.env_gc() == []


def test_env_gc_tears_down_an_orphaned_environment_and_leaves_a_live_one_alone(tmp_path):
    repo = make_repo(tmp_path)
    log = tmp_path / "teardown.txt"
    set_config(repo, {"teardown": 'echo "down $RUNWAY_TASK" >> %s' % shlex.quote(str(log)), "ports": 1})
    save(repo, task("a"), task("b"))
    prepare(repo, "a")
    db = prepare(repo, "b")
    git(repo, "worktree", "remove", "--force", worktree(repo, "a"))  # a's worktree is gone; b's is live

    assert runway.env_gc() == ["ap-a"]
    assert log.read_text() == "down a\n"  # torn down in the repo, since its worktree is gone
    assert [b["task"] for b in registry_blocks()] == ["b"]
    assert not os.path.exists(os.path.join(db, runway.ENV_TORN))
    assert os.path.exists(os.path.join(db, runway.ENV_SPEC))
    assert runway.env_gc() == []  # a torn-down environment is not torn down again


def test_env_gc_collects_a_chunk_that_ended_over_a_day_ago_and_keeps_a_recent_one(tmp_path):
    repo = make_repo(tmp_path)
    set_config(repo, {"teardown": "true", "ports": 1})
    save(repo, task("a"), task("b"))
    da, db = prepare(repo, "a"), prepare(repo, "b")
    now = time.time()
    for d, k, ago in ((da, "a", 2 * 86400), (db, "b", 3600)):  # a ended two days ago, b an hour ago
        path = os.path.join(d, k + ".exit")
        with open(path, "w") as f:
            f.write("0\n")
        os.utime(path, (now - ago, now - ago))

    assert runway.env_gc(now) == ["ap-a"]
    assert os.path.exists(os.path.join(db, runway.ENV_SPEC)) and not os.path.exists(os.path.join(db, runway.ENV_TORN))
    assert runway._ap_read(runway._env_gc_state())["tornDown"] == ["ap-a"]
    assert [b["task"] for b in registry_blocks()] == ["b"]


def test_tick_runs_env_gc_at_most_once_an_hour(tmp_path):
    repo = make_repo(tmp_path)
    set_config(repo, {"teardown": "true", "ports": 1})
    save(repo, task("a"), task("b"), task("c"))
    for k in ("a", "b", "c"):
        prepare(repo, k)
    git(repo, "worktree", "remove", "--force", worktree(repo, "a"))
    runway.tick()  # the first pass of the hour: a is orphaned, so it goes
    assert block_of("a") is None and block_of("c") is not None

    git(repo, "worktree", "remove", "--force", worktree(repo, "c"))
    runway.tick()  # within the hour: c is left until the next pass
    assert block_of("c") is not None

    runway.write_json_atomic(runway._env_gc_state(), {"at": int(time.time()) - runway.ENV_GC_EVERY - 1, "tornDown": []})
    runway.tick()  # an hour later: c goes
    assert block_of("c") is None
