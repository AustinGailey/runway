"""Launch gates in helper/runway.py (PI 10 Sprint 1): the autopilot's ready list respects sprint order, parked work and
the independent flag, and blocker-check says whether a blocker has cleared. The page side is in test_launch_gates_harness.py."""
import base64, json, os, subprocess, time

import runway


def task(key, status="todo", **kw):
    return {"key": key, "title": "Task " + key, "status": status, **kw}


def sprint(key, tasks, **kw):
    return {"key": key, "title": "Sprint " + key, "tasks": tasks, **kw}


def roadmap(*sprints):
    return runway.parse_roadmap({"version": 1, "pis": [{"key": "pi", "title": "PI", "sprints": list(sprints)}]})


def ready(rm):
    return [t.key for t in runway.ready_tasks(rm)]


def test_a_later_sprint_waits_while_an_earlier_one_is_empty():
    rm = roadmap(sprint("s1", []), sprint("s2", [task("b")]))
    assert ready(rm) == []
    assert runway.sprint_waits(rm)["b"] == "Waits for Sprint s1: no tasks planned yet"


def test_a_later_sprint_waits_for_unfinished_tasks_and_starts_when_all_are_done_or_dropped():
    rm = roadmap(sprint("s1", [task("a", "done"), task("a2", "review")]), sprint("s2", [task("b")]))
    assert ready(rm) == [] and runway.sprint_waits(rm)["b"] == "Waits for Sprint s1: 1 task left"
    rm = roadmap(sprint("s1", [task("a", "done"), task("a2", "dropped")]), sprint("s2", [task("b")]))
    assert ready(rm) == ["b"]


def test_every_earlier_sprint_counts_not_just_the_previous_one():
    rm = roadmap(sprint("s1", []), sprint("s2", [task("m", "done")]), sprint("s3", [task("c")]))
    assert ready(rm) == [] and "Sprint s1" in runway.sprint_waits(rm)["c"]


def test_independent_task_or_sprint_overrides_the_order():
    rm = roadmap(sprint("s1", [task("a")]), sprint("s2", [task("b", independent=True), task("c")]), sprint("s3", [task("d")], independent=True))
    assert ready(rm) == ["a", "b", "d"]


def test_parked_and_dropped_tasks_are_never_ready_and_a_parked_task_holds_its_sprint_open():
    rm = roadmap(sprint("s1", [task("a", "parked"), task("a2", "dropped")]), sprint("s2", [task("b")]))
    assert ready(rm) == [] and runway.sprint_waits(rm)["b"] == "Waits for Sprint s1: 1 task left"


def test_a_roadmap_without_gates_keeps_its_file_shape(tmp_path):
    repo = str(tmp_path / "p")
    runway.save_roadmap(repo, roadmap(sprint("s1", [task("a")])))
    data = json.load(open(runway.roadmap_path(repo)))["pis"][0]["sprints"][0]
    assert "independent" not in data and "independent" not in data["tasks"][0]
    runway.save_roadmap(repo, roadmap(sprint("s1", [task("a", independent=True)])))
    assert json.load(open(runway.roadmap_path(repo)))["pis"][0]["sprints"][0]["tasks"][0]["independent"] is True


def check(*items):
    out = runway.blocker_check(base64.b64encode(json.dumps(list(items)).encode()).decode())
    assert out.startswith("@@BLOCKERS ")
    return json.loads(out[len("@@BLOCKERS "):])


def test_access_blocker_clears_when_the_path_exists(tmp_path):
    f = tmp_path / "notes.md"
    assert check({"id": "a", "path": str(f)}) == {"a": False}
    f.write_text("x")
    assert check({"id": "a", "path": str(f)}) == {"a": True}


def test_command_blocker_runs_only_plain_read_only_checks(tmp_path):
    marker = tmp_path / "ran"
    res = check({"id": "ok", "command": "test -e " + str(tmp_path)}, {"id": "no", "command": "test -e " + str(tmp_path / "missing")},
                {"id": "evil", "command": "touch %s" % marker}, {"id": "chain", "command": "test -e / ; touch %s" % marker})
    assert res == {"ok": True, "no": False, "evil": False, "chain": False}
    assert not marker.exists()


def test_metacharacters_never_reach_the_shell_even_after_an_allowed_command(tmp_path):
    marker = tmp_path / "y"
    attempts = ["which x;touch${IFS}%s" % marker, "which x;touch %s" % marker, "which x && touch %s" % marker, "ls /;touch %s" % marker,
                "test -e / || touch %s" % marker, "which $(touch %s)" % marker, "which `touch %s`" % marker, "ls /*", "which x\ntouch %s" % marker,
                "test -e '/tmp' ", "pg_isready; touch %s" % marker, "command -v x>%s" % marker]
    res = check(*[{"id": str(i), "command": c} for i, c in enumerate(attempts)])
    assert not any(res.values())
    assert not marker.exists()


def test_dependency_blocker_clears_when_the_branch_gets_a_new_commit(tmp_path):
    repo = str(tmp_path / "r")
    os.makedirs(repo)
    g = lambda *a, **env: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, env={**os.environ, **env})
    g("init", "-q", "-b", "main")
    g("commit", "-q", "--allow-empty", "-m", "one", GIT_COMMITTER_DATE="2026-01-01T00:00:00", GIT_AUTHOR_DATE="2026-01-01T00:00:00",
      GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    since = int(time.time() * 1000)
    item = {"id": "d", "branch": "main", "since": since, "repo": repo}
    assert check(item) == {"d": False}
    g("commit", "-q", "--allow-empty", "-m", "two", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    assert check({**item, "since": since - 5000}) == {"d": True}
    assert check({"id": "x", "branch": "no-such-branch", "since": 0, "repo": repo}) == {"x": False}
