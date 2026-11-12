"""A task's environment, prepared before its agent starts: a port block from the registry, the named variables from the
project's env file, and setup run with both. Real git runs in temp repos; the agent is a fake claude that the run script
is pointed at (run.sh puts the real claude first on PATH, so a fake on PATH would not be found). Nothing here reads the
real ~/.ai-planner: conftest points BASE at a temp dir."""
import json, os, shlex, subprocess, time

import runway
from test_autopilot import git, make_repo, save, statuses, task, worktree


def set_config(repo, cfg):
    """Commit a .runway.json with these keys, so the task's worktree has it too."""
    with open(os.path.join(repo, ".runway.json"), "w") as f:
        f.write(json.dumps(cfg) + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "config")


def write_env_file(repo, text):
    """The project's env file, where Austin fills in its values (outside the repo)."""
    path = runway.env_file(repo)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def write_fake(tmp_path, order, expect_secret=None):
    """A fake claude: it records its task and port base in the order file, checks the secret it was given (when
    expect_secret is set), changes one file in its worktree, and ends with a result line like the real one."""
    lines = ["#!/bin/bash", 'echo "agent $RUNWAY_TASK $RUNWAY_PORT_BASE" >> %s' % shlex.quote(str(order))]
    if expect_secret is not None:
        lines.append('[ "$DEMO_SECRET" = %s ] && echo secret-seen >> %s' % (shlex.quote(expect_secret), shlex.quote(str(order))))
    lines += ['echo work > "work-$RUNWAY_TASK.txt"',
              'echo \'{"type": "result", "subtype": "success", "is_error": false, "result": "Done.", "num_turns": 1, '
              '"duration_ms": 1, "total_cost_usd": 0, "usage": {}}\'']
    path = tmp_path / "fake-claude"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return str(path)


def run_autopilot(repo, fake, parallel=1):
    """The autopilot over the roadmap, with real launches. Each run script is pointed at the fake claude first."""
    def launcher(ch):
        script = os.path.join(runway.RUNS, ch, "run.sh")
        with open(script) as f:
            text = f.read()
        with open(script, "w") as f:
            f.write(text.replace("\nclaude -p", "\n%s -p" % shlex.quote(fake), 1))
        return runway.launch(ch)
    return runway.autopilot(repo, parallel, launcher=launcher, poll=0.05, sleep=time.sleep)


def read_order(order):
    return open(order).read().splitlines() if os.path.exists(order) else []


def registry_blocks():
    return (runway._ap_read(os.path.join(runway.BASE, runway.PORTS_FILE)) or {}).get("blocks", [])


def tree_text(root):
    """Every file's text under root, except git's own folder."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [n for n in dirnames if n != ".git"]
        for name in filenames:
            try:
                out.append(open(os.path.join(dirpath, name), errors="replace").read())
            except OSError:
                pass
    return "\n".join(out)


# ---------- a fixture project with setup and ports: a port block per task, setup before the agent ----------
def test_each_task_gets_its_own_port_block_and_setup_runs_before_its_agent(tmp_path):
    repo = make_repo(tmp_path)
    order = tmp_path / "order.txt"
    set_config(repo, {"setup": 'echo "setup $RUNWAY_TASK $RUNWAY_PORT_BASE $RUNWAY_PORTS" >> %s' % shlex.quote(str(order)),
                      "ports": 2})
    save(repo, task("a"), task("b"))
    # One at a time: with no checks, a task that waits for another's merge is refused by the existing merge gate.
    # Its block is still taken while the other task's block is held, so the uniqueness check holds.
    result = run_autopilot(repo, write_fake(tmp_path, order))
    assert sorted(result["done"]) == ["a", "b"], (result, statuses(repo), read_order(order))
    # A merged task gives its block back (see test_env_teardown), so b takes the lowest block free once a is done. The
    # blocks are read from each run folder's spec, which keeps them after the registry has let go.
    bases = {k: runway._ap_read(os.path.join(runway._ap_dir(k), runway.ENV_SPEC))["ports"]["base"] for k in ("a", "b")}
    assert bases == {"a": runway.PORT_FIRST, "b": runway.PORT_FIRST}
    for k in ("a", "b"):
        base = bases[k]
        lines = read_order(order)
        setup = next(line for line in lines if line.startswith("setup %s " % k))
        agent = next(line for line in lines if line.startswith("agent %s " % k))
        assert lines.index(setup) < lines.index(agent)  # the setup ran before this task's agent
        assert setup == "setup %s %d %s" % (k, base, ",".join(str(p) for p in range(base, base + 2)))
        assert agent == "agent %s %d" % (k, base)  # the agent got the same block, through its environment
    assert os.path.exists(os.path.join(repo, "work-a.txt")) and os.path.exists(os.path.join(repo, "work-b.txt"))


def test_a_task_keeps_its_port_block_when_it_is_prepared_again(tmp_path):
    repo = make_repo(tmp_path)
    first = runway._ap_port_block(repo, "a")
    other = runway._ap_port_block(repo, "b")
    assert first != other
    assert runway._ap_port_block(repo, "a") == first  # the same task, the same block
    assert runway._ap_port_block(os.path.join(repo, "..", "other"), "a") != first  # another project's task is apart


def test_a_project_without_environment_keys_gets_no_spec_and_no_port_registry(tmp_path):
    repo = make_repo(tmp_path)
    set_config(repo, {"teardown": "echo bye"})
    assert runway.task_env_spec(repo, "a", runway.project_config(repo)) is None
    assert runway.run_env("ap-a") == {}
    assert not os.path.exists(os.path.join(runway.BASE, runway.PORTS_FILE))


# ---------- a failing setup stops the task before its agent, as an environment blocker ----------
def test_a_failing_setup_stops_the_task_before_the_agent_with_an_environment_blocker(tmp_path):
    repo = make_repo(tmp_path)
    order = tmp_path / "order.txt"
    set_config(repo, {"setup": "echo installing; echo npm-ci-broke; exit 3"})
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, order))
    assert statuses(repo) == {"a": "blocked"}
    assert read_order(order) == []  # the agent never ran, so it spent no tokens
    outcome = runway.chunk_status("ap-a")["tasks"]["a"]["outcome"]
    assert outcome["status"] == "blocked"
    [blocker] = outcome["blockers"]
    assert blocker["type"] == "environment"
    assert blocker["command"] == "echo installing; echo npm-ci-broke; exit 3"
    assert "npm-ci-broke" in blocker["detail"] and "[setup exit 3]" in blocker["detail"]  # the log tail


def test_a_timed_out_setup_is_an_environment_blocker_too(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    set_config(repo, {"setup": "sleep 30"})
    monkeypatch.setattr(runway, "SETUP_TIMEOUT", 1)
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt"))
    outcome = runway.chunk_status("ap-a")["tasks"]["a"]["outcome"]
    assert outcome["blockers"][0]["type"] == "environment" and "timed out" in outcome["blockers"][0]["detail"]


def test_a_named_variable_missing_from_the_env_file_stops_the_task_before_setup_and_agent(tmp_path):
    repo = make_repo(tmp_path)
    marker = tmp_path / "setup-ran.txt"
    set_config(repo, {"setup": "echo ran > %s" % shlex.quote(str(marker)), "env": ["DEMO_TOKEN"]})
    write_env_file(repo, "# nothing filled in yet\n")
    save(repo, task("a"))
    run_autopilot(repo, write_fake(tmp_path, tmp_path / "order.txt"))
    assert statuses(repo) == {"a": "blocked"}
    assert not marker.exists() and read_order(tmp_path / "order.txt") == []
    [blocker] = runway.chunk_status("ap-a")["tasks"]["a"]["outcome"]["blockers"]
    assert blocker["type"] == "environment" and "DEMO_TOKEN" in blocker["detail"]


# ---------- secret values: the agent and setup get them, the worktree and git never hold them ----------
def test_secret_values_reach_setup_and_the_agent_but_never_the_worktree_or_git(tmp_path):
    secret = "s3cr3t-value-41"
    repo = make_repo(tmp_path)
    order = tmp_path / "order.txt"
    set_config(repo, {"setup": '[ -n "$DEMO_SECRET" ] && echo setup-saw-it >> %s' % shlex.quote(str(order)),
                      "env": ["DEMO_SECRET"], "ports": 1})
    write_env_file(repo, "# the project's values\nexport DEMO_SECRET=\"%s\"\nOTHER_VALUE=unused\n" % secret)
    save(repo, task("a"))

    # Prepared, before any agent runs: the worktree holds no value, and the run folder keeps only names and ports.
    runway._ap_prepare(repo, runway.roadmap_tasks(runway.load_roadmap(repo))[0], "main", True)
    wt = worktree(repo, "a")
    assert secret not in tree_text(wt)
    assert secret not in open(os.path.join(runway._ap_dir("a"), runway.ENV_SPEC)).read()
    assert read_order(order) == ["setup-saw-it"]  # setup got the value through its environment

    # The whole run: the agent got the value, and the merged result holds no trace of it.
    run_autopilot(repo, write_fake(tmp_path, order, expect_secret=secret))
    assert statuses(repo) == {"a": "done"}
    assert "secret-seen" in read_order(order)
    assert secret not in tree_text(repo)
    assert secret not in git(repo, "log", "--all", "-p")
    every_object = subprocess.run(["git", "-C", repo, "cat-file", "--batch-all-objects", "--batch"], capture_output=True).stdout
    assert secret.encode() not in every_object  # unreachable objects too, not only what the branches reach


def test_run_env_gives_the_checker_the_task_variables_and_the_names_only_in_the_spec(tmp_path):
    repo = make_repo(tmp_path)
    set_config(repo, {"env": ["DEMO_SECRET"], "ports": 2})
    write_env_file(repo, "DEMO_SECRET=abc\n")
    save(repo, task("a"))
    runway._ap_prepare(repo, runway.roadmap_tasks(runway.load_roadmap(repo))[0], "main", True)
    env = runway.run_env("ap-a")
    base = runway._ap_port_block(repo, "a")
    assert env == {"DEMO_SECRET": "abc", "RUNWAY_TASK": "a", "RUNWAY_PORT_BASE": str(base),
                   "RUNWAY_PORTS": "%d,%d" % (base, base + 1)}


# ---------- the env file: NAME=value lines, as Austin writes them ----------
def test_the_env_file_reads_names_values_quotes_and_export_and_skips_the_rest(tmp_path):
    path = tmp_path / "site.env"
    path.write_text('# comment\n\nexport A=1\nB = two \nC="quoted value"\nD=\'single\'\nnot a pair\n1BAD=x\nE=a=b\n')
    assert runway.read_env_file(str(path)) == {"A": "1", "B": "two", "C": "quoted value", "D": "single", "E": "a=b"}
    assert runway.read_env_file(str(tmp_path / "missing.env")) == {}
