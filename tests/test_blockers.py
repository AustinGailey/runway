"""Agents' own outcome block, the pre-launch check, and merging the same task twice."""
import base64, json, os, subprocess

import runway
from test_git_flows import commit, git, make_repo, start_task, write


def b64(o):
    return base64.b64encode(json.dumps(o).encode()).decode()


def test_parse_outcome_takes_the_last_valid_block_and_cleans_it():
    report = """Did some work.
```runway
{"status": "done", "blockers": []}
```
Then more happened.
```runway
{"status": "blocked", "blockers": [
  {"type": "decision", "detail": "Which table is canonical?", "options": ["clients", "platform_clients", ""]},
  {"type": "environment", "detail": "No Docker", "command": "docker info"},
  {"type": "bogus", "detail": "dropped"}]}
```"""
    oc = runway.parse_outcome(report)
    assert oc["status"] == "blocked"
    assert [b["type"] for b in oc["blockers"]] == ["decision", "environment"]
    assert oc["blockers"][0]["options"] == ["clients", "platform_clients"]
    assert oc["blockers"][1]["command"] == "docker info"


def test_parse_outcome_ignores_reports_without_a_valid_block():
    assert runway.parse_outcome("All done, no block.") is None
    assert runway.parse_outcome("```runway\n{not json}\n```") is None
    assert runway.parse_outcome('```runway\n{"status": "weird"}\n```') is None


def test_chunk_status_reads_the_block_even_past_the_report_cut(tmp_path):
    d = os.path.join(runway.RUNS, "c1")
    os.makedirs(d)
    long = "x" * 20000 + '\n```runway\n{"status": "partial", "blockers": [{"type": "dependency", "detail": "Needs the seed task", "needs": "Seed fixtures"}]}\n```'
    with open(os.path.join(d, "t1.json"), "w") as f:
        json.dump({"type": "result", "result": long, "session_id": "s"}, f)
    for name, v in (("t1.status", "done\n"), ("t1.exit", "0\n")):
        with open(os.path.join(d, name), "w") as f:
            f.write(v)
    t = runway.chunk_status("c1")["tasks"]["t1"]
    assert t["outcome"]["status"] == "partial" and t["outcome"]["blockers"][0]["needs"] == "Seed fixtures"


def test_preflight_reports_failing_requirements_and_missing_branch(tmp_path):
    repo = make_repo(tmp_path)
    out = runway.preflight(b64([{"name": "P", "repo": repo, "start": "nope",
                                 "requires": [{"check": "true"}, {"check": "exit 3", "fix": "echo fixed"}]}]))
    res = json.loads(out.replace("@@PREFLIGHT ", ""))[0]
    kinds = [p["kind"] for p in res["problems"]]
    assert kinds == ["branch", "env"]
    assert res["problems"][1]["fix"] == "echo fixed"


def test_preflight_flags_a_checkout_mid_merge(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "side", {"a.txt": "side\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, "main", {"a.txt": "main\n"})
    subprocess.run(["git", "-C", repo, "merge", "side"], capture_output=True)
    res = json.loads(runway.preflight(b64([{"name": "P", "repo": repo}])).replace("@@PREFLIGHT ", ""))[0]
    assert [p["kind"] for p in res["problems"]] == ["checkout"]


def test_preflight_clean_repo_has_no_problems(tmp_path):
    repo = make_repo(tmp_path)
    res = json.loads(runway.preflight(b64([{"name": "P", "repo": repo, "start": "main"}])).replace("@@PREFLIGHT ", ""))[0]
    assert res["problems"] == []


def test_merging_a_task_twice_counts_the_second_as_merged(tmp_path):
    repo = make_repo(tmp_path)
    wtp = start_task(repo, "t1", title="Once")
    write(wtp, {"x.txt": "x\n"})
    iw, ibranch = runway.integration(repo, "main")
    assert runway.apply_task("chunk-1", "t1", iw, ibranch, "main", False, lambda m: None)["outcome"] == "merged"

    again = runway.apply_task("chunk-1", "t1", iw, ibranch, "main", False, lambda m: None)

    assert again["outcome"] == "merged" and "Already merged" in again["msg"]


def test_a_resolve_that_leaves_python_broken_is_refused():
    assert runway._broken_after_resolve("x.py", "VERSION = 1\n") is None
    assert "no longer compiles" in runway._broken_after_resolve("helper/runway.py", ">VERSION = 24\n")
    assert "markers" in runway._broken_after_resolve("a.txt", "<<<<<<< HEAD\nx\n")
    assert "JSON" in runway._broken_after_resolve("p.json", "{bad")
