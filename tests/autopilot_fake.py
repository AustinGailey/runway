"""Shared helpers for the autopilot loop tests (test_autopilot_loop.py, test_autopilot_resume.py): a temp repo with a project
whose checks are real, a roadmap builder, and a scripted fake agent. Not a test module: pytest does not collect it."""
import json, os, subprocess

import runway


GREEN, RED, CONFLICT, TIMEOUT = "green", "red", "conflict", "timeout"
SONNET = {"input_tokens": 300000, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
COST = 0.9  # dollars for SONNET's usage at the price table

# The project's checks. "tests" rejects a change that left broken.txt; "slow" hangs on hang.txt until its timeout stops it.
CHECKS = {"checks": [
    {"name": "tests", "cmd": "test ! -e broken.txt"},
    {"name": "slow", "cmd": "[ ! -e hang.txt ] || sleep 30", "timeout": 1},
]}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "site"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    (repo / ".runway.json").write_text(json.dumps(CHECKS))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return str(repo)


def task(key, deps=(), est=300000):
    return {"key": key, "title": "Task " + key, "detail": "Do " + key, "acceptance": "It works", "dependsOn": list(deps),
            "status": "todo", "estTokens": est, "model": "claude-sonnet-5"}


def save(repo, *tasks):
    runway.save_roadmap(repo, {"version": 1, "project": "site", "title": "Site",
                               "pis": [{"key": "pi-1", "title": "PI", "sprints": [{"key": "s-1", "title": "S",
                                                                                   "tasks": list(tasks)}]}]})


def statuses(repo):
    return {t.key: t.status for t in runway.roadmap_tasks(runway.load_roadmap(repo))}


def worktree(repo, key):
    return os.path.join(repo, ".claude", "worktrees", "runway-" + key)


def state(repo):
    return json.loads(open(runway._ap_paths(repo)[0]).read())


def report_of(repo):
    """The run report write_ap_report left behind for the run this repo's state file names, as (markdown, json)."""
    md, js = runway.ap_report_paths(state(repo)["run"])
    return open(md).read(), json.loads(open(js).read())


def run_outcome(key):
    return json.load(open(os.path.join(runway.RUNS, "ap-" + key, key + ".autopilot.json")))["outcome"]


def failure_states():
    lines = open(runway.FAILURES).read().splitlines() if os.path.exists(runway.FAILURES) else []
    return [json.loads(x)["state"] for x in lines]


def ledger_chunks(repo):
    return [r["ch"] for r in runway.budget_rows(state(repo)["run"])]


class Scripted:
    """A fake agent. script maps a task key to its outcome; keys not listed are green. A run is held until the loop sleeps,
    then the oldest one finishes: it commits its change, reports usage the way a headless run does, and writes its exit
    code and status. on_sleep(agent, seconds) runs before each finish, so a test can stop or kill the loop at a chosen point."""

    def __init__(self, repo, script=None, on_sleep=None):
        self.repo, self.script, self.on_sleep = repo, dict(script or {}), on_sleep
        self.pending, self.launched, self.seen = [], [], {}

    def launch(self, ch):
        key = ch[len("ap-"):]
        self.launched.append(key)
        self.seen[key] = statuses(self.repo)  # what the roadmap said when this task was started
        open(os.path.join(runway.RUNS, ch, "_pgid"), "w").write(str(os.getpgrp()))  # our group: alive while the test runs
        self.pending.append(key)
        return "launched"

    def sleep(self, seconds):
        if self.on_sleep:
            self.on_sleep(self, seconds)
        if self.pending:
            self.finish(self.pending.pop(0))

    def finish(self, key):
        outcome = self.script.get(key, GREEN)
        wtp = worktree(self.repo, key)
        if outcome == CONFLICT:
            with open(os.path.join(wtp, "README.md"), "w") as f:
                f.write("from the task " + key + "\n")
            git(wtp, "add", "-A")
            git(wtp, "commit", "-q", "-m", "task " + key + " edits README")
            with open(os.path.join(self.repo, "README.md"), "w") as f:  # the base edits the same line
                f.write("from the base, after " + key + "\n")
            git(self.repo, "commit", "-q", "-am", "base edits README")
        else:
            name = {RED: "broken.txt", TIMEOUT: "hang.txt"}.get(outcome, key + ".txt")
            with open(os.path.join(wtp, name), "w") as f:
                f.write(outcome + " " + key + "\n")
            git(wtp, "add", "-A")
            git(wtp, "commit", "-q", "-m", "agent " + key)
        d = os.path.join(runway.RUNS, "ap-" + key)
        with open(os.path.join(d, key + ".json"), "w") as f:
            json.dump({"usage": SONNET, "cost": 0}, f)
        open(os.path.join(d, key + ".exit"), "w").write("0\n")
        open(os.path.join(d, key + ".status"), "w").write("done\n")


def run_loop(repo, agent, **kw):
    return runway.autopilot(repo, launcher=agent.launch, poll=0, sleep=agent.sleep, **kw)


def check_no_leftovers(tmp_path):
    """After a test: only the main worktree is left, no run's worktree folder, and the only runway-task branches are those
    of blocked tasks (kept for a retry). Each test module runs this from its autouse fixture."""
    repo = str(tmp_path / "site")
    if not os.path.isdir(repo):
        return
    assert len([l for l in git(repo, "worktree", "list", "--porcelain").splitlines() if l.startswith("worktree ")]) == 1
    wt_dir = os.path.join(repo, ".claude", "worktrees")
    assert not os.path.isdir(wt_dir) or os.listdir(wt_dir) == []
    rm = runway.load_roadmap(repo)
    blocked = {"runway-task/" + t.key for t in runway.roadmap_tasks(rm) if t.status == "blocked"} if rm else set()
    branches = {b.strip() for b in git(repo, "branch", "--format=%(refname:short)").splitlines() if b.strip()}
    assert {b for b in branches if b.startswith("runway-task/")} <= blocked
