"""Runway's contract: behaviour Austin's real projects depend on. These run against PLAIN repos (no .runway.json,
no Runway config of any kind), like his real ones. Agents must never edit this file to make a change pass;
a change that breaks one of these needs Austin's decision first (see CLAUDE.md)."""
import base64, json, os, re, subprocess, sys

import runway
from test_git_flows import commit, git, make_repo, start_task, write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plain_repo(tmp_path, name="proj"):
    return make_repo(tmp_path, name, checks=False)


def test_a_task_in_a_plain_repo_merges_and_lands(tmp_path):
    repo = plain_repo(tmp_path)
    wtp = start_task(repo, "t1", chunk="c1")
    write(wtp, {"feature.txt": "new\n"})
    out = runway.merge("c1", "t1")
    assert out.startswith("@@OK"), out
    assert open(os.path.join(repo, "feature.txt")).read() == "new\n"


def test_a_merge_batch_in_a_plain_repo_merges(tmp_path):
    repo = plain_repo(tmp_path)
    wtp = start_task(repo, "t1", chunk="c1")
    write(wtp, {"feature.txt": "new\n"})
    iw, ibranch = runway.integration(repo, "main")
    res = runway.apply_task("c1", "t1", iw, ibranch, "main", False, lambda m: None)
    assert res["outcome"] == "merged", res


def test_landing_a_branch_that_is_not_checked_out_fast_forwards(tmp_path):
    repo = plain_repo(tmp_path)
    iw, _ = runway.integration(repo, "main")
    commit(iw, "Runway: approved", {"a.txt": "incoming\n"})
    git(repo, "checkout", "-q", "-b", "other")
    ok, msg = runway.land(repo, "main")
    assert ok, msg
    assert git(repo, "rev-parse", "main") == git(repo, "rev-parse", "runway/main")


def test_preflight_passes_a_clean_plain_repo(tmp_path):
    repo = plain_repo(tmp_path)
    spec = base64.b64encode(json.dumps([{"name": "P", "repo": repo}]).encode()).decode()
    res = json.loads(runway.preflight(spec).replace("@@PREFLIGHT ", ""))[0]
    assert res["problems"] == []


def test_the_helper_compiles_and_matches_the_page_version():
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    page_v = int(re.search(r"const HELPER_V = (\d+);", src).group(1))
    assert runway.VERSION == page_v
    r = subprocess.run([sys.executable, os.path.join(ROOT, "helper", "runway.py"), "version"], capture_output=True, text=True)
    assert r.stdout.strip() == "@@VERSION %d" % page_v
