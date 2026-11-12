"""The per-project setup step: an opt-in "setup" command in .runway.json that the autopilot runs once in each new task
worktree (for example "npm ci"). Real git runs in a temp repo; no agent runs here, the launcher only records the launch."""
import json, os, shlex

import pytest

import runway
from test_autopilot import Agent, git, make_repo, run_loop, save, statuses, task, worktree


def set_setup(repo, cmd):
    """Commit a .runway.json that sets the setup command, or no setup at all when cmd is None."""
    cfg = {"setup": cmd} if cmd is not None else {}
    with open(os.path.join(repo, ".runway.json"), "w") as f:
        f.write(json.dumps(cfg) + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "config")


def first_task(repo):
    return runway.roadmap_tasks(runway.load_roadmap(repo))[0]


def run_once(repo, launches):
    """One autopilot run over the roadmap. The launcher records each launch and reports it as started."""
    def launch(ch):
        launches.append(ch)
        return "launched"
    return runway.autopilot(repo, 1, launcher=launch, poll=0, sleep=lambda s: None)


def test_no_setup_defined_changes_nothing(tmp_path):
    repo = make_repo(tmp_path)
    save(repo, task("a"))
    assert runway.project_config(repo).get("setup") is None
    run_loop(repo, Agent(repo))
    assert statuses(repo) == {"a": "done"}
    d = runway._ap_dir("a")
    assert not os.path.exists(os.path.join(d, "a.setup.log"))
    assert not os.path.exists(os.path.join(d, "a.setup.json"))


def test_setup_succeeds_once_and_its_output_and_exit_code_reach_the_log(tmp_path):
    repo = make_repo(tmp_path)
    count = tmp_path / "setup-count.txt"
    set_setup(repo, "echo ran >> %s; echo installing-deps; echo ok > setup-marker.txt" % shlex.quote(str(count)))
    save(repo, task("a"))
    messages = []
    runway._ap_prepare(repo, first_task(repo), "main", True, messages.append)
    runway._ap_prepare(repo, first_task(repo), "main", False, messages.append)  # a second prepare reuses the worktree
    assert count.read_text().splitlines() == ["ran"]  # run once, not on every prepare
    assert os.path.exists(os.path.join(worktree(repo, "a"), "setup-marker.txt"))  # it ran in the task's worktree
    d = runway._ap_dir("a")
    log_text = open(os.path.join(d, "a.setup.log")).read()
    assert "installing-deps" in log_text and "[setup exit 0]" in log_text
    assert runway._ap_read(os.path.join(d, "a.setup.json"))["rc"] == 0
    assert any("setup a: exit 0" in m for m in messages)


def test_setup_failure_blocks_the_task_and_names_the_command(tmp_path):
    repo = make_repo(tmp_path)
    set_setup(repo, "echo npm-ci-broke; exit 3")
    save(repo, task("a"))
    launches = []
    result = run_once(repo, launches)
    assert launches == []  # the agent never starts against missing dependencies
    assert statuses(repo) == {"a": "blocked"}
    why = result["blocked"]["a"]
    assert "`echo npm-ci-broke; exit 3`" in why and "exit 3" in why
    d = runway._ap_dir("a")
    log_text = open(os.path.join(d, "a.setup.log")).read()
    assert "npm-ci-broke" in log_text and "[setup exit 3]" in log_text
    assert runway._ap_read(os.path.join(d, "a.setup.json"))["rc"] == 3


def test_a_failed_setup_is_run_again_when_the_task_is_retried(tmp_path):
    repo = make_repo(tmp_path)
    set_setup(repo, "exit 1")
    save(repo, task("a"))
    run_once(repo, [])
    assert statuses(repo) == {"a": "blocked"}
    set_setup(repo, "echo fixed")  # the project now has a working setup step
    runway._ap_prepare(repo, first_task(repo), "main", True)
    rec = runway._ap_read(os.path.join(runway._ap_dir("a"), "a.setup.json"))
    assert rec["cmd"] == "echo fixed" and rec["rc"] == 0


@pytest.mark.parametrize("bad", ["", "   ", 5, [], [""], ["npm ci", 3], [" "]])
def test_setup_must_be_one_command_a_list_of_commands_or_null(tmp_path, bad):
    with pytest.raises(runway.ConfigError, match='"setup" must be one command'):
        runway._check("project settings", {"setup": bad})


def test_setup_null_means_no_setup(tmp_path):
    assert runway._check("project settings", {"setup": None}) == {"setup": None}
    assert runway._check("project settings", {"setup": " npm ci "}) == {"setup": "npm ci"}


def test_config_get_and_set_use_the_same_key_the_worktree_setup_reads(tmp_path):
    """The project settings field in the page writes config_set(repo, "setup", ...); _ap_prepare reads that same
    "setup" key from .runway.json via project_config. Prove they agree, end to end, with no page or MCP involved."""
    repo = make_repo(tmp_path)
    assert runway.config_get(repo, "setup") == ""
    assert runway.config_set(repo, "setup", "npm ci") == {"setup": "npm ci"}
    assert runway.config_get(repo, "setup") == "npm ci"
    assert runway.project_config(repo).get("setup") == "npm ci"


def test_config_set_clears_the_key_when_given_an_empty_value(tmp_path):
    repo = make_repo(tmp_path)
    runway.config_set(repo, "setup", "npm ci")
    assert runway.config_set(repo, "setup", "") == {}
    assert runway.config_get(repo, "setup") == ""
    assert runway.project_config(repo).get("setup") is None


def test_config_set_keeps_other_keys_in_the_file(tmp_path):
    repo = make_repo(tmp_path)
    runway.config_set(repo, "baseBranch", "main")
    runway.config_set(repo, "setup", "pnpm install")
    assert json.loads((tmp_path / "site" / ".runway.json").read_text()) == {"baseBranch": "main", "setup": "pnpm install"}


def test_config_set_rejects_a_bad_command_the_same_way_the_file_would(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(runway.ConfigError, match='"setup" must be one command'):
        runway.config_set(repo, "setup", "   ")


def test_config_set_rejects_an_unknown_key():
    with pytest.raises(runway.ConfigError, match="not a project setting"):
        runway.config_set("/tmp/nope", "notAKey", "x")


def test_a_setup_command_written_through_config_set_runs_on_worktree_creation(tmp_path):
    """Acceptance: creating a new worktree for a project with a setup command runs it and leaves its result present."""
    repo = make_repo(tmp_path)
    runway.config_set(repo, "setup", "npm ci --no-audit --no-fund || true; mkdir -p node_modules; touch node_modules/.installed")
    save(repo, task("a"))
    runway._ap_prepare(repo, first_task(repo), "main", True)
    assert os.path.exists(os.path.join(worktree(repo, "a"), "node_modules", ".installed"))
    d = runway._ap_dir("a")
    assert runway._ap_read(os.path.join(d, "a.setup.json"))["rc"] == 0


def test_setup_list_runs_its_commands_in_order_and_stops_at_the_first_failure(tmp_path):
    repo = make_repo(tmp_path)
    set_setup(repo, ["echo one >> order.txt", "exit 4", "echo three >> order.txt"])
    save(repo, task("a"))
    launches = []
    result = run_once(repo, launches)
    assert launches == []
    assert statuses(repo) == {"a": "blocked"}
    assert "exit 4" in result["blocked"]["a"]
    assert open(os.path.join(worktree(repo, "a"), "order.txt")).read().splitlines() == ["one"]
