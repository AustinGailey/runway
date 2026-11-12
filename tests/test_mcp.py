"""runway.py mcp: the stdio MCP server over Runway's queue (board mirror, claims, finish through the checks, release).
Everything runs in a sandbox HOME and scratch git repos; the server itself is also driven over a real subprocess pipe."""
import base64, json, os, subprocess, sys, time

import pytest

import runway
from test_git_flows import commit, git, make_repo

HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "helper", "runway.py")


def b64(obj):
    return base64.b64encode(json.dumps(obj).encode()).decode()


def board(repo, ids=("t1",), at=None, **extra):
    return {"v": 1, "at": at or int(time.time() * 1000), "tasks": [
        dict({"id": i, "projectId": "p1", "projectName": "Proj", "title": "Task " + i, "kind": "build", "model": "sonnet",
              "repo": repo, "base": "main", "estTokens": 1000, "acceptance": "works", "prompt": "BRIEF for " + i}, **extra)
        for i in ids]}


def sync(repo, **kw):
    ok, msg = runway.board_sync(b64(board(repo, **kw)))
    assert ok, msg


def call(name, **args):
    err, text = runway.mcp_call(name, args)
    return err, text


def set_checks(repo, checks):
    with open(os.path.join(repo, ".runway.json"), "w") as f:
        json.dump({"checks": checks}, f)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "test checks")


def rpc_session(lines, env=None):
    p = subprocess.run([sys.executable, HELPER, "mcp"], input="".join(json.dumps(l) + "\n" for l in lines),
                       capture_output=True, text=True, timeout=30, env=env)
    return p


def test_handshake_list_and_call_over_a_real_pipe(tmp_path):
    repo = make_repo(tmp_path)
    sync(repo)
    p = rpc_session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_ready_tasks", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "nope"},
    ])
    assert p.returncode == 0, p.stderr
    out = [json.loads(l) for l in p.stdout.splitlines()]  # every stdout line is JSON-RPC, nothing else
    assert [o["id"] for o in out] == [1, 2, 3, 4, 5]  # the notification got no reply
    assert all(o["jsonrpc"] == "2.0" for o in out)
    assert out[0]["result"]["protocolVersion"] == "2025-03-26" and out[0]["result"]["serverInfo"]["name"] == "runway"
    assert out[1]["result"] == {}
    assert {t["name"] for t in out[2]["result"]["tools"]} == {"list_ready_tasks", "claim_task", "task_status", "finish_task", "release_task"}
    res = out[3]["result"]
    assert res["isError"] is False and "t1" in res["content"][0]["text"] and "Board mirrored" in res["content"][0]["text"]
    assert out[4]["error"]["code"] == -32601


def test_unknown_protocol_version_gets_the_newest_and_bad_json_is_a_parse_error():
    p = rpc_session([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}])
    assert json.loads(p.stdout)["result"]["protocolVersion"] == runway.MCP_PROTOCOLS[0]
    p = subprocess.run([sys.executable, HELPER, "mcp"], input="not json\n", capture_output=True, text=True, timeout=30)
    assert json.loads(p.stdout)["error"]["code"] == -32700


def test_list_says_how_old_the_board_is(tmp_path):
    repo = make_repo(tmp_path)
    err, text = call("list_ready_tasks")
    assert err and "Open the Runway page" in text  # no board yet
    sync(repo, at=int((time.time() - 2 * 86400) * 1000))
    err, text = call("list_ready_tasks")
    assert not err and "more than a day old" in text and "open the Runway page" in text
    sync(repo, ids=("t1", "t2"))
    err, text = call("list_ready_tasks", projectId="other")
    assert text.endswith("[]")
    err, text = call("list_ready_tasks", projectId="p1")
    assert '"id": "t1"' in text and '"id": "t2"' in text and "prompt" not in text


def test_claim_creates_worktree_branch_claim_run_dir_and_status(tmp_path):
    repo = make_repo(tmp_path)
    sync(repo)
    err, text = call("claim_task", taskId="t1", client="claude-code")
    assert not err, text
    r = json.loads(text)
    wtp = os.path.join(repo, ".claude", "worktrees", "runway-t1")
    assert r["worktree"] == wtp and r["branch"] == "runway-task/t1" and r["prompt"] == "BRIEF for t1"
    assert os.path.isdir(wtp) and git(wtp, "rev-parse", "--abbrev-ref", "HEAD").strip() == "runway-task/t1"
    c = json.load(open(os.path.join(runway.BASE, "claims", "t1.json")))
    assert c["state"] == "claimed" and c["client"] == "claude-code" and c["taskId"] == "t1" and c["worktree"] == wtp
    assert c["chunkId"] == r["chunkId"] and len(c["chunkId"]) == 2 + 12 + 3 and c["chunkId"].startswith("mc")
    d = os.path.join(runway.RUNS, c["chunkId"])
    meta = json.load(open(os.path.join(d, "t1.meta")))
    assert meta["repo"] == repo and meta["wt"] == "runway-t1" and meta["base"] == "main"
    assert meta["fork"] == git(repo, "rev-parse", "main").strip()
    assert open(os.path.join(d, "t1.status")).read().strip() == "running"
    assert runway.chunk_status(c["chunkId"])["tasks"]["t1"]["state"] == "running"
    # it left the list of ready tasks
    assert call("list_ready_tasks")[1].endswith("[]")


def test_double_claim_and_a_task_runway_is_running_are_refused(tmp_path):
    repo = make_repo(tmp_path)
    sync(repo, ids=("t1", "t2", "t3"))
    assert not call("claim_task", taskId="t1", client="a")[0]
    err, text = call("claim_task", taskId="t1", client="b")
    assert err and "already claimed by a" in text
    d = os.path.join(runway.RUNS, "ch1")
    os.makedirs(d)
    open(os.path.join(d, "t2.status"), "w").write("running\n")
    err, text = call("claim_task", taskId="t2", client="b")
    assert err and "already running in Runway chunk ch1" in text
    assert not os.path.exists(os.path.join(runway.BASE, "claims", "t2.json"))
    err, text = call("claim_task", taskId="nope", client="b")
    assert err and "not on the board" in text
    assert call("claim_task", taskId="../x", client="b")[0]


def test_claim_runs_setup_with_the_task_environment(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "cfg", {".runway.json": json.dumps({"ports": 2, "setup": "echo $RUNWAY_TASK:$RUNWAY_PORT_BASE > setup.txt"})})
    sync(repo)
    err, text = call("claim_task", taskId="t1", client="codex")
    assert not err, text
    wtp = json.loads(text)["worktree"]
    task, port = open(os.path.join(wtp, "setup.txt")).read().strip().split(":")
    assert task == "t1" and int(port) >= runway.PORT_FIRST


def test_failed_setup_refuses_the_claim_and_leaves_nothing(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "cfg", {".runway.json": json.dumps({"setup": "echo nope; exit 4"})})
    sync(repo)
    err, text = call("claim_task", taskId="t1", client="codex")
    assert err and "Setup" in text and "nope" in text
    assert not os.path.exists(os.path.join(runway.BASE, "claims", "t1.json"))
    assert not os.path.isdir(os.path.join(repo, ".claude", "worktrees", "runway-t1"))
    assert git(repo, "branch", "--list", "runway-task/t1").strip() == ""


def claimed(tmp_path, checks=None):
    repo = make_repo(tmp_path)
    if checks is not None:
        set_checks(repo, checks)
    sync(repo)
    err, text = call("claim_task", taskId="t1", client="claude-code")
    assert not err, text
    return repo, json.loads(text)


def test_finish_with_a_dirty_tree_is_refused_naming_the_files(tmp_path):
    repo, r = claimed(tmp_path)
    open(os.path.join(r["worktree"], "new.txt"), "w").write("x")
    err, text = call("finish_task", taskId="t1", note="n")
    assert err and "uncommitted" in text and "new.txt" in text
    assert json.load(open(os.path.join(runway.BASE, "claims", "t1.json")))["state"] == "claimed"


def test_finish_green_marks_done_per_the_contract(tmp_path):
    repo, r = claimed(tmp_path, checks=[{"name": "ok", "cmd": "true"}])
    commit(r["worktree"], "work", {"w.txt": "w\n"})
    err, text = call("finish_task", taskId="t1", note="all good")
    assert not err, text
    assert json.loads(text)["checks"] == "green"
    c = json.load(open(os.path.join(runway.BASE, "claims", "t1.json")))
    d = os.path.join(runway.RUNS, c["chunkId"])
    assert c["state"] == "finished" and c["note"] == "all good" and c["finishedAt"] and c["checks"]["state"] == "green"
    assert open(os.path.join(d, "t1.status")).read().strip() == "done" and open(os.path.join(d, "t1.exit")).read().strip() == "0"
    st = runway.chunk_status(c["chunkId"])
    assert st["tasks"]["t1"]["state"] == "done" and st["finished"]
    # it did not merge, push or touch the integration branch
    assert git(repo, "log", "--format=%s", "main").splitlines()[0] == "test checks"
    assert git(repo, "branch", "--list", "runway/main").strip() == ""
    assert call("finish_task", taskId="t1", note="")[0]  # finished already


def test_finish_without_checks_is_done_with_checks_none(tmp_path):
    repo, r = claimed(tmp_path)
    os.remove(os.path.join(r["worktree"], ".runway.json"))
    git(r["worktree"], "commit", "-q", "-am", "drop checks")
    git(repo, "-c", "advice.detachedHead=false", "status")  # repo untouched
    err, text = call("finish_task", taskId="t1", note="")
    assert not err, text
    assert json.load(open(os.path.join(runway.BASE, "claims", "t1.json")))["state"] == "finished"


def test_finish_red_stays_claimed_with_the_log_and_can_finish_again(tmp_path):
    repo, r = claimed(tmp_path, checks=[{"name": "unit", "cmd": "echo boom-line; test -f fixed.txt"}])
    commit(r["worktree"], "work", {"w.txt": "w\n"})
    err, text = call("finish_task", taskId="t1", note="")
    assert err and "unit" in text and "boom-line" in text
    c = json.load(open(os.path.join(runway.BASE, "claims", "t1.json")))
    d = os.path.join(runway.RUNS, c["chunkId"])
    assert c["state"] == "claimed" and c["checks"]["state"] == "red" and "boom-line" in c["checks"]["log"]
    assert open(os.path.join(d, "t1.status")).read().strip() == "running" and not os.path.exists(os.path.join(d, "t1.exit"))
    ok, st = runway.task_status("t1")
    assert st["checks"]["state"] == "red" and len(st["commits"]) == 1
    commit(r["worktree"], "fix", {"fixed.txt": "y\n"})
    err, text = call("finish_task", taskId="t1", note="fixed")
    assert not err, text
    assert json.load(open(os.path.join(runway.BASE, "claims", "t1.json")))["state"] == "finished"


def test_task_status_reports_commits_and_uncommitted(tmp_path):
    repo, r = claimed(tmp_path)
    commit(r["worktree"], "one", {"w.txt": "w\n"})
    open(os.path.join(r["worktree"], "loose.txt"), "w").write("x")
    err, text = call("task_status", taskId="t1")
    s = json.loads(text)
    assert not err and s["state"] == "claimed" and len(s["commits"]) == 1 and s["uncommitted"] == ["loose.txt"]
    assert call("task_status", taskId="zzz")[0]


def test_release_removes_an_empty_worktree_and_keeps_one_with_commits(tmp_path):
    repo, r = claimed(tmp_path)
    err, text = call("release_task", taskId="t1", reason="changed my mind")
    assert not err, text
    c = json.load(open(os.path.join(runway.BASE, "claims", "t1.json")))
    assert c["state"] == "released" and c["note"] == "changed my mind"
    assert not os.path.isdir(r["worktree"])
    assert open(os.path.join(runway.RUNS, c["chunkId"], "t1.status")).read().strip() == "stopped"
    # claimable again
    err, text = call("claim_task", taskId="t1", client="codex")
    assert not err, text
    r2 = json.loads(text)
    commit(r2["worktree"], "work", {"w.txt": "w\n"})
    err, text = call("release_task", taskId="t1", reason="later")
    assert not err and "kept" in json.loads(text)["worktree"]
    assert os.path.isdir(r2["worktree"])


def test_claims_are_in_status_and_live_json(tmp_path, monkeypatch):
    monkeypatch.setattr(runway, "LIVE", os.path.join(runway.BASE, "live.json"))
    repo, r = claimed(tmp_path)
    live = json.load(open(runway.LIVE))
    assert [c["taskId"] for c in live["claims"]] == ["t1"] and live["claims"][0]["state"] == "claimed"
    assert r["chunkId"] in live["chunks"]
    monkeypatch.setattr(runway, "scan_usage", lambda: {})
    monkeypatch.setattr(runway, "summarize", lambda h: {})
    out = runway.tick()
    assert [c["taskId"] for c in out["claims"]] == ["t1"]
    assert runway.main(["status"]) is None
    # a finished claim older than LIVE_KEEP drops out; a claimed one never does
    p = os.path.join(runway.BASE, "claims", "t2.json")
    json.dump({"v": 1, "taskId": "t2", "state": "finished"}, open(p, "w"))
    old = time.time() - runway.LIVE_KEEP - 60
    os.utime(p, (old, old))
    assert [c["taskId"] for c in runway.claims_all()] == ["t1"]


def test_board_sync_validation(tmp_path):
    repo = make_repo(tmp_path)
    good = board(repo)
    assert runway.board_sync(b64(good))[0]
    assert json.load(open(os.path.join(runway.BASE, "board.json"))) == good
    assert not os.path.exists(os.path.join(runway.BASE, "board.json.tmp"))
    before = open(os.path.join(runway.BASE, "board.json")).read()
    bad = [("!!not base64!!", "base64"), (base64.b64encode(b"nope").decode(), "base64"),
           (b64([1]), '"v": 1'), (b64({"v": 2, "at": 1, "tasks": []}), '"v": 1'),
           (b64({"v": 1, "tasks": []}), '"at"'), (b64({"v": 1, "at": 1, "tasks": {}}), '"tasks"'),
           (b64({"v": 1, "at": 1, "tasks": [5]}), "not an object"),
           (b64(board(repo, ids=("../evil",))), "plain id"),
           (b64(board(repo, prompt="")), '"prompt"'), (b64(board(repo, estTokens="lots")), "estTokens")]
    for arg, want in bad:
        ok, msg = runway.board_sync(arg)
        assert not ok and want in msg, (arg, msg)
    assert open(os.path.join(runway.BASE, "board.json")).read() == before  # rejects write nothing


def test_board_sync_command_exit_codes(tmp_path):
    repo = make_repo(tmp_path)
    p = subprocess.run([sys.executable, HELPER, "board-sync", b64(board(repo))], capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.startswith("@@BOARD 1 tasks")
    p = subprocess.run([sys.executable, HELPER, "board-sync", "garbage"], capture_output=True, text=True)
    assert p.returncode == 1 and p.stdout.startswith("@@ERR")


def test_mcp_install_prints_both_registrations_and_changes_nothing(tmp_path, capsys):
    before = set(os.listdir(os.environ["HOME"])) if os.path.isdir(os.environ["HOME"]) else set()
    runway.main(["mcp-install"])
    out = capsys.readouterr().out
    assert "claude mcp add --scope user runway -- python3 ~/.ai-planner/runway.py mcp" in out
    assert "[mcp_servers.runway]" in out and 'args = ["%s", "mcp"]' % os.path.join(runway.BASE, "runway.py") in out
    assert "~/.codex/config.toml" in out
    after = set(os.listdir(os.environ["HOME"])) if os.path.isdir(os.environ["HOME"]) else set()
    assert before == after
