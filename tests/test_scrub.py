"""scrub: remove files an agent left in its worktree and could not delete (agents never get rm), but only untracked
files inside that worktree: never committed work, never ignored files, never anything outside it."""
import json, os, subprocess

import runway


def setup(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    g = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)
    g("init", "-q", "-b", "main"); (repo / "kept.txt").write_text("x"); (repo / ".gitignore").write_text("ignored.log\n")
    g("add", "-A"); g("commit", "-qm", "init")
    wtp = repo / ".claude" / "worktrees" / "runway-t1"
    g("worktree", "add", "-q", "-b", "runway-task/t1", str(wtp))
    d = os.path.join(runway.RUNS, "ch1"); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "t1.meta"), "w").write(json.dumps({"repo": str(repo), "wt": "runway-t1", "base": "main"}))
    return repo, wtp


def test_only_untracked_files_inside_the_worktree_are_removed(tmp_path):
    repo, wtp = setup(tmp_path)
    (wtp / "oneshot").mkdir(); (wtp / "oneshot" / "_debug.png").write_text("")
    (wtp / "ignored.log").write_text("keep me")
    outside = tmp_path / "outside.txt"; outside.write_text("keep me")
    out = runway.scrub("ch1", "t1", ["oneshot/_debug.png", "kept.txt", "ignored.log", "../../../../outside.txt", "missing.txt"])
    r = json.loads(out.split("@@SCRUB ", 1)[1])
    assert r["removed"] == ["oneshot/_debug.png"]
    assert set(r["kept"]) == {"kept.txt", "ignored.log", "../../../../outside.txt", "missing.txt"}
    assert not (wtp / "oneshot" / "_debug.png").exists()
    assert (wtp / "kept.txt").exists() and (wtp / "ignored.log").exists() and outside.exists()


def test_a_task_without_a_worktree_is_refused(tmp_path):
    d = os.path.join(runway.RUNS, "ch2"); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "t2.meta"), "w").write(json.dumps({"repo": str(tmp_path), "wt": "", "base": "main"}))
    assert runway.scrub("ch2", "t2", ["x.txt"]).startswith("@@ERR")
