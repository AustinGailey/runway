"""Git flows of helper/runway.py, run against throwaway repos under pytest's tmp_path."""
import json, os, subprocess

import runway


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def write(root, files):
    for name, text in files.items():
        p = os.path.join(root, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


def commit(repo, msg, files=None):
    if files:
        write(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


def make_repo(tmp_path, name="proj", checks=True):
    """A repo on main. With checks, it has a .runway.json whose one check passes, so the merge gate lets work through."""
    repo = str(tmp_path / name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    # agents' worktrees live under .claude/ in the checkout; keep them out of the user's status
    with open(os.path.join(repo, ".git", "info", "exclude"), "a") as f:
        f.write(".claude/\n")
    commit(repo, "base", {"a.txt": "base a\n", "b.txt": "base b\n", "old.txt": "old\n", "keep.txt": "keep\n"})
    if checks:
        commit(repo, "checks", {".runway.json": '{"checks": [{"name": "ok", "cmd": "true"}]}'})
    return repo


def start_task(repo, tid, title="Do the thing", chunk="chunk-1", record_fork=True):
    """Make an agent worktree on a new branch from main, the way run.sh does, and write its .meta/.prompt.md."""
    wt_name = "wt-" + tid
    wtp = os.path.join(repo, ".claude", "worktrees", wt_name)
    fork = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "worktree", "add", "-q", "-b", "runway-task/" + tid, wtp, "main")
    meta = {"repo": repo, "wt": wt_name, "base": "main"}
    if record_fork:
        meta["fork"] = fork
    rundir = os.path.join(runway.RUNS, chunk)
    os.makedirs(rundir, exist_ok=True)
    with open(os.path.join(rundir, tid + ".meta"), "w") as f:
        json.dump(meta, f)
    with open(os.path.join(rundir, tid + ".prompt.md"), "w") as f:
        f.write("# Task\n\nTASK: " + title + "\n")
    return wtp


def staged_names(wtp, rev):
    """What the agent's own work changes relative to rev, counting uncommitted files (as _fork expects)."""
    git(wtp, "add", "-A")
    return git(wtp, "diff", "--cached", "--name-only", rev).split()


def porcelain(repo):
    return git(repo, "status", "--porcelain", "--untracked-files=all")


# ---------- _fork ----------

def test_fork_prefers_merge_base_when_agent_merged_base(tmp_path):
    repo = make_repo(tmp_path)
    wtp = start_task(repo, "t1")
    write(wtp, {"agent-committed.txt": "agent\n"})
    git(wtp, "add", "-A")
    git(wtp, "commit", "-q", "-m", "agent work")
    main_tip = commit(repo, "main moves on", {"c.txt": "main only\n"})
    git(wtp, "merge", "-q", "--no-edit", "main")  # the agent pulled main in itself
    write(wtp, {"agent.txt": "agent\n"})

    meta = json.load(open(os.path.join(runway.RUNS, "chunk-1", "t1.meta")))
    fork = runway._fork(meta, wtp, "main")

    assert fork == main_tip  # the recorded start would also drag in c.txt
    assert staged_names(wtp, fork) == ["agent-committed.txt", "agent.txt"]


def test_fork_keeps_recorded_start_when_base_was_rewritten(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "main adds f", {"f.txt": "f\n"})
    recorded = git(repo, "rev-parse", "HEAD").strip()
    wtp = start_task(repo, "t1")
    # main is rewritten under the agent: the merge-base falls back to the parent and drags f.txt in
    git(repo, "commit", "-q", "--amend", "-m", "main adds f, rewritten")
    write(wtp, {"agent.txt": "agent\n"})

    meta = json.load(open(os.path.join(runway.RUNS, "chunk-1", "t1.meta")))
    assert runway._fork(meta, wtp, "main") == recorded
    assert staged_names(wtp, recorded) == ["agent.txt"]


def test_fork_without_recorded_start_uses_first_reflog_entry(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "main adds f", {"f.txt": "f\n"})
    recorded = git(repo, "rev-parse", "HEAD").strip()
    wtp = start_task(repo, "t1", record_fork=False)
    git(repo, "commit", "-q", "--amend", "-m", "main adds f, rewritten")  # same rewrite as above, no recorded start
    write(wtp, {"agent.txt": "agent\n"})

    meta = json.load(open(os.path.join(runway.RUNS, "chunk-1", "t1.meta")))
    assert "fork" not in meta
    assert runway._fork(meta, wtp, "main") == recorded
    assert staged_names(wtp, recorded) == ["agent.txt"]


# ---------- _overlap ----------

def test_overlap_reads_first_entry_renames_and_untracked(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "base 2", {"zeta.txt": "z\n", "other.txt": "o\n"})
    git(repo, "checkout", "-q", "-b", "runway/main")
    commit(repo, "agent work", {"a.txt": "incoming a\n", "old.txt": "incoming old\n", "zeta.txt": "incoming z\n",
                                "fresh.txt": "incoming fresh\n", "other.txt": "incoming o\n"})
    git(repo, "checkout", "-q", "main")
    # checkout's dirty state: a first entry (unstaged edit), a staged rename, an entry after the rename,
    # an untracked file, and a dirty file that the incoming branch does not touch
    write(repo, {"a.txt": "mine a\n", "zeta.txt": "mine z\n", "keep.txt": "mine keep\n", "fresh.txt": "mine fresh\n"})
    git(repo, "mv", "old.txt", "renamed.txt")
    assert porcelain(repo).splitlines()[0].startswith(" M a.txt")

    assert runway._overlap(repo, "main") == ["a.txt", "fresh.txt", "old.txt", "zeta.txt"]


# ---------- apply_task ----------

def test_apply_task_applies_only_the_agents_own_changes(tmp_path):
    repo = make_repo(tmp_path)
    wtp = start_task(repo, "t1", title="Add agent file")
    write(wtp, {"agent.txt": "agent\n"})
    git(wtp, "add", "-A")
    git(wtp, "commit", "-q", "-m", "agent work")
    commit(repo, "main moves on", {"c.txt": "main only\n"})
    git(wtp, "merge", "-q", "--no-edit", "main")  # the agent pulled in main's new file
    iw, ibranch = runway.integration(repo, "main")

    result = runway.apply_task("chunk-1", "t1", iw, ibranch, "main", False, lambda m: None)

    assert result["outcome"] == "merged", result
    assert git(iw, "show", "--name-only", "--format=", "HEAD").split() == ["agent.txt"]
    assert open(os.path.join(iw, "c.txt")).read() == "main only\n"
    assert not os.path.isdir(wtp)  # the agent's worktree and branch are cleaned up after a clean merge
    assert git(repo, "branch", "--list", "runway-task/t1") == ""


def test_conflicting_second_task_is_reset_cleanly_without_resolve(tmp_path):
    repo = make_repo(tmp_path)
    wt1 = start_task(repo, "t1", title="First")
    wt2 = start_task(repo, "t2", title="Second")
    write(wt1, {"a.txt": "from task one\n"})
    write(wt2, {"a.txt": "from task two\n"})
    iw, ibranch = runway.integration(repo, "main")
    noop = lambda m: None

    first = runway.apply_task("chunk-1", "t1", iw, ibranch, "main", False, noop)
    head = git(iw, "rev-parse", "HEAD").strip()
    second = runway.apply_task("chunk-1", "t2", iw, ibranch, "main", False, noop)

    assert first["outcome"] == "merged", first
    assert second["outcome"] == "conflict" and "a.txt" in second["msg"], second
    assert git(iw, "rev-parse", "HEAD").strip() == head
    assert porcelain(iw) == ""
    assert open(os.path.join(iw, "a.txt")).read() == "from task one\n"
    assert os.path.isdir(wt2)  # a conflicting task keeps its worktree so it can be retried or discarded


# ---------- land ----------

def make_incoming(repo):
    """runway/main with one approved commit touching a.txt, as the integration worktree would hold it."""
    iw, _ = runway.integration(repo, "main")
    commit(iw, "Runway: approved", {"a.txt": "incoming a\n"})
    return iw


def test_land_lands_with_non_overlapping_dirty_files(tmp_path):
    repo = make_repo(tmp_path)
    make_incoming(repo)
    write(repo, {"b.txt": "my b\n", "notes.txt": "my notes\n"})  # dirty, but not touched by the incoming branch

    ok, msg = runway.land(repo, "main")

    assert ok and msg == "Landed in main.", msg
    assert open(os.path.join(repo, "a.txt")).read() == "incoming a\n"
    assert open(os.path.join(repo, "b.txt")).read() == "my b\n"
    assert open(os.path.join(repo, "notes.txt")).read() == "my notes\n"
    assert len(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3  # HEAD is a merge commit


def test_land_refuses_on_overlap_and_changes_nothing(tmp_path):
    repo = make_repo(tmp_path)
    make_incoming(repo)
    write(repo, {"a.txt": "my a\n"})  # uncommitted edit to a file the incoming branch changes
    head, status = git(repo, "rev-parse", "HEAD").strip(), porcelain(repo)

    ok, msg = runway.land(repo, "main")

    assert not ok
    assert msg.startswith("Saved on runway/main. Not landed in main yet") and "a.txt" in msg, msg
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert porcelain(repo) == status
    assert open(os.path.join(repo, "a.txt")).read() == "my a\n"
    assert not os.path.exists(os.path.join(repo, ".git", "MERGE_HEAD"))
    assert runway._overlap(repo, "main") == ["a.txt"]


# ---------- land_with_stash ----------

def test_land_with_stash_reapplies_edits(tmp_path):
    repo = make_repo(tmp_path)
    make_incoming(repo)
    write(repo, {"b.txt": "my b\n", "notes.txt": "my notes\n"})

    ok, msg = runway.land_with_stash(repo, "main")

    assert ok and "re-applied your uncommitted edits" in msg, msg
    assert open(os.path.join(repo, "a.txt")).read() == "incoming a\n"
    assert open(os.path.join(repo, "b.txt")).read() == "my b\n"
    assert open(os.path.join(repo, "notes.txt")).read() == "my notes\n"
    assert git(repo, "stash", "list") == ""
    backups = os.listdir(os.path.join(runway.BASE, "backups"))
    assert len(backups) == 1
    assert "+my b" in open(os.path.join(runway.BASE, "backups", backups[0])).read()
    assert os.path.join(runway.BASE, "backups", backups[0]) in msg


def test_land_with_stash_keeps_stash_and_backup_on_collision(tmp_path):
    repo = make_repo(tmp_path)
    make_incoming(repo)
    write(repo, {"a.txt": "my a\n"})  # collides with the incoming change once re-applied

    ok, msg = runway.land_with_stash(repo, "main")

    assert ok and "collided" in msg and "a.txt" in msg, msg
    assert "<<<<<<<" in open(os.path.join(repo, "a.txt")).read()
    stashes = git(repo, "stash", "list").splitlines()
    assert len(stashes) == 1 and "runway-land" in stashes[0]
    backups = os.listdir(os.path.join(runway.BASE, "backups"))
    assert len(backups) == 1
    assert "my a" in open(os.path.join(runway.BASE, "backups", backups[0])).read()
    assert "git stash list" in msg and os.path.join(runway.BASE, "backups", backups[0]) in msg


# ---------- isolation ----------

def test_helper_paths_point_at_the_temp_home(isolated_home):
    assert runway.BASE == str(isolated_home / ".ai-planner")
    assert runway.HOME == str(isolated_home)
    assert os.path.expanduser("~") == str(isolated_home)


# ---------- checkout health, guarded land, commit-wip, set-aside, lockfile sync ----------

def b64(obj):
    import base64
    return base64.b64encode((obj if isinstance(obj, str) else json.dumps(obj)).encode()).decode()


def test_health_reports_untracked_duplicates_and_land_refuses_them(tmp_path):
    repo = make_repo(tmp_path)
    iw, _ = runway.integration(repo, "main")
    commit(iw, "Runway: add serp", {"lib/serp.ts": "agent version\n"})
    write(repo, {"lib/serp.ts": "my untracked version\n"})

    h = runway.checkout_health(repo, "main")
    assert h["untrackedBlocking"] == ["lib/serp.ts"] and h["conflicted"] == []
    ok, msg = runway.land(repo, "main")
    assert not ok and "set them aside" in msg
    assert open(os.path.join(repo, "lib/serp.ts")).read() == "my untracked version\n"


def test_set_aside_moves_only_blocking_files_then_land_works(tmp_path, isolated_home):
    repo = make_repo(tmp_path)
    iw, _ = runway.integration(repo, "main")
    commit(iw, "Runway: add serp", {"lib/serp.ts": "agent version\n"})
    write(repo, {"lib/serp.ts": "mine\n", "notes.txt": "keep me\n"})

    out = runway.set_aside(repo, "main", b64(["lib/serp.ts", "notes.txt"]))  # notes.txt isn't blocking: ignored
    assert out.startswith("@@OK Set aside 1 file"), out
    assert os.path.exists(os.path.join(repo, "notes.txt"))
    saved = [os.path.join(d, f) for d, _, fs in os.walk(isolated_home / ".ai-planner" / "backups") for f in fs]
    assert any(p.endswith("lib/serp.ts") and open(p).read() == "mine\n" for p in saved)
    ok, msg = runway.land(repo, "main")
    assert ok, msg
    assert open(os.path.join(repo, "lib/serp.ts")).read() == "agent version\n"


def test_land_refuses_conflicted_checkout(tmp_path):
    repo = make_repo(tmp_path)
    make_incoming(repo)
    # leave a real unmerged path in the checkout
    git(repo, "checkout", "-q", "-b", "side"); commit(repo, "side", {"b.txt": "side b\n"})
    git(repo, "checkout", "-q", "main"); commit(repo, "main b", {"b.txt": "main b\n"})
    subprocess.run(["git", "-C", repo, "merge", "side"], capture_output=True)
    subprocess.run(["git", "-C", repo, "merge", "--quit"], capture_output=True)  # keep the conflict, drop MERGE_HEAD
    assert runway.checkout_health(repo, "main")["conflicted"] == ["b.txt"]
    for fn in (runway.land, runway.land_with_stash):
        ok, msg = fn(repo, "main")
        assert not ok and "unresolved conflicts" in msg, msg


def test_commit_wip_commits_everything_and_refuses_conflicts(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, {"b.txt": "changed\n", "new/file.txt": "new\n"})
    out = runway.commit_wip(repo, "main", b64("WIP: my work"))
    assert out.startswith("@@OK Committed 2 files"), out
    assert porcelain(repo) == ""
    assert git(repo, "log", "-1", "--format=%s").strip() == "WIP: my work"
    out = runway.commit_wip(repo, "other", b64("x"))
    assert out.startswith("@@ERR") and "not other" in out


def test_commit_wip_refuses_huge_files(tmp_path):
    repo = make_repo(tmp_path)
    with open(os.path.join(repo, "big.bin"), "wb") as f:
        f.seek(51 * 1024 * 1024); f.write(b"\0")
    out = runway.commit_wip(repo, "main", b64("x"))
    assert out.startswith("@@ERR Too large") and "big.bin" in out
    assert "big.bin" in porcelain(repo)  # nothing was staged or committed


def test_sync_regenerates_conflicting_lockfile(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    commit(repo, "lock", {"package.json": '{"name":"x","dependencies":{}}\n', "package-lock.json": '{"v":"base"}\n'})
    iw, _ = runway.integration(repo, "main")
    commit(iw, "agent lock", {"package-lock.json": '{"v":"agent"}\n'})
    commit(repo, "my lock", {"package-lock.json": '{"v":"mine"}\n'})
    # stand in for npm: "regenerate" writes a lockfile derived from the merged manifest
    monkeypatch.setitem(runway.LOCKFILES, "package-lock.json", ["python3", "-c", "open('package-lock.json','w').write('{\"v\":\"regenerated\"}\\n')"])
    ok, note = runway.sync_integration(repo, "main")
    assert ok and "Regenerated package-lock.json" in note, note
    assert open(os.path.join(iw, "package-lock.json")).read() == '{"v":"regenerated"}\n'
    assert git(iw, "status", "--porcelain").strip() == ""


def test_sync_aborts_on_real_conflicts(tmp_path):
    repo = make_repo(tmp_path)
    iw, _ = runway.integration(repo, "main")
    commit(iw, "agent a", {"a.txt": "agent a\n"})
    commit(repo, "my a", {"a.txt": "my a\n"})
    ok, note = runway.sync_integration(repo, "main")
    assert not ok and "a.txt" in note
    assert not os.path.exists(os.path.join(iw, ".git")) or git(iw, "status", "--porcelain").strip() == ""


def _diverge_on_a(repo):
    """Approved work on runway/main and the user's own newer commit on main both change a.txt."""
    make_incoming(repo)
    commit(repo, "my edit", {"a.txt": "my a\n"})


def test_land_reports_a_resolvable_conflict_and_changes_nothing(tmp_path):
    repo = make_repo(tmp_path)
    _diverge_on_a(repo)
    before = git(repo, "rev-parse", "HEAD", "runway/main")

    ok, msg = runway.land(repo, "main")

    assert ok is None and "a.txt" in msg, msg
    assert git(repo, "rev-parse", "HEAD", "runway/main") == before
    iw, _ = runway.integration(repo, "main")
    assert not git(iw, "status", "--porcelain").strip()  # merge aborted cleanly


def test_land_with_resolve_combines_and_lands(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _diverge_on_a(repo)
    seen = {}

    def fake_resolve(iw, files, ch, tid, log, intro=None):
        seen["files"], seen["intro"] = files, intro
        write(iw, {"a.txt": "my a\nincoming a\n"})
        git(iw, "add", "a.txt")
        return True, 10, 0.01, "kept both"
    monkeypatch.setattr(runway, "_resolve", fake_resolve)

    ok, msg = runway.land(repo, "main", resolve=True)

    # Claude's combine waits for the owner: nothing lands until they confirm
    assert ok is False and "Review what Claude combined" in msg and "kept both" in msg, msg
    assert seen["files"] == ["a.txt"] and "owner's own" in seen["intro"]
    assert open(os.path.join(repo, "a.txt")).read() == "my a\n"
    review = runway.pending_review(repo, "main")
    assert review["files"] == ["a.txt"] and "+incoming a" in review["diff"]

    ok, msg = runway.land(repo, "main", confirm=True)

    assert ok and "Landed in main." in msg, msg
    assert open(os.path.join(repo, "a.txt")).read() == "my a\nincoming a\n"
    assert runway.pending_review(repo, "main") is None


def test_land_undo_takes_back_the_combine(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _diverge_on_a(repo)
    iw, _ = runway.integration(repo, "main")
    approved = git(iw, "rev-parse", "HEAD").strip()

    def fake_resolve(iw, files, ch, tid, log, intro=None):
        write(iw, {"a.txt": "combined\n"}); git(iw, "add", "a.txt")
        return True, 1, 0, "combined"
    monkeypatch.setattr(runway, "_resolve", fake_resolve)
    runway.land(repo, "main", resolve=True)

    ok, msg = runway.land_undo(repo, "main")

    assert ok, msg
    assert git(iw, "rev-parse", "HEAD").strip() == approved
    assert runway.pending_review(repo, "main") is None
    assert open(os.path.join(repo, "a.txt")).read() == "my a\n"


def test_land_fast_forwards_a_branch_that_is_not_checked_out(tmp_path):
    repo = make_repo(tmp_path)
    make_incoming(repo)
    git(repo, "checkout", "-q", "-b", "other")
    write(repo, {"mine.txt": "untouched\n"})

    ok, msg = runway.land(repo, "main")

    assert ok and "Landed in main" in msg and "stays on other" in msg, msg
    assert git(repo, "rev-parse", "main") == git(repo, "rev-parse", "runway/main")
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "other"
    assert open(os.path.join(repo, "mine.txt")).read() == "untouched\n"


def test_land_with_resolve_that_fails_changes_nothing(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _diverge_on_a(repo)
    before = git(repo, "rev-parse", "HEAD", "runway/main")
    monkeypatch.setattr(runway, "_resolve", lambda *a, **k: (False, 0, 0, "markers left in a.txt"))

    ok, msg = runway.land(repo, "main", resolve=True)

    assert ok is False and "couldn't combine" in msg, msg
    assert git(repo, "rev-parse", "HEAD", "runway/main") == before


def test_land_merges_in_the_worktree_that_has_the_branch(tmp_path):
    repo = make_repo(tmp_path)
    make_incoming(repo)
    git(repo, "checkout", "-q", "-b", "other")
    elsewhere = str(tmp_path / "main-wt")
    git(repo, "worktree", "add", "-q", elsewhere, "main")
    write(elsewhere, {"b.txt": "my b\n"})  # uncommitted, untouched by the incoming work

    ok, msg = runway.land(repo, "main")

    assert ok and "checked out at" in msg, msg
    assert open(os.path.join(elsewhere, "a.txt")).read() == "incoming a\n"
    assert open(os.path.join(elsewhere, "b.txt")).read() == "my b\n"
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "other"
    assert runway.checkout_for(repo, "main") == os.path.realpath(elsewhere) or runway.checkout_for(repo, "main") == elsewhere


def test_two_tasks_adding_the_same_new_file_conflict_and_resolve(tmp_path, monkeypatch):
    """Was 'patch does not apply' with nothing to resolve; now an ordinary add/add conflict."""
    repo = make_repo(tmp_path)
    wt1 = start_task(repo, "t1", title="First spec")
    wt2 = start_task(repo, "t2", title="Second spec")
    write(wt1, {"e2e/spec.ts": "one\n", "README.md": "readme one\n"})
    write(wt2, {"e2e/spec.ts": "two\n", "README.md": "readme two\n", "new.ts": "fine\n"})
    iw, ibranch = runway.integration(repo, "main")
    assert runway.apply_task("chunk-1", "t1", iw, ibranch, "main", True, lambda m: None)["outcome"] == "merged"
    seen = {}

    def fake_resolve(iw, files, ch, tid, log, intro=None, deleted=None):
        seen["files"] = files
        write(iw, {f: "both\n" for f in files})
        return True, 5, 0.01, "kept both"
    monkeypatch.setattr(runway, "_resolve", fake_resolve)

    result = runway.apply_task("chunk-1", "t2", iw, ibranch, "main", True, lambda m: None)

    assert result["outcome"] == "resolved", result
    assert seen["files"] == ["README.md", "e2e/spec.ts"]
    assert open(os.path.join(iw, "e2e/spec.ts")).read() == "both\n"
    assert open(os.path.join(iw, "new.ts")).read() == "fine\n"
    assert not git(iw, "status", "--porcelain").strip()


def test_task_deleting_a_file_approved_work_changed_is_a_resolvable_conflict(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wt1 = start_task(repo, "t1", title="Edit old")
    wt2 = start_task(repo, "t2", title="Fold old into journey")
    write(wt1, {"old.txt": "edited by approved work\n"})
    git(wt2, "rm", "-q", "old.txt")
    write(wt2, {"journey.txt": "old folded in\n"})
    iw, ibranch = runway.integration(repo, "main")
    runway.apply_task("chunk-1", "t1", iw, ibranch, "main", True, lambda m: None)
    seen = {}

    def fake_resolve(iw, files, ch, tid, log, intro=None, deleted=None):
        seen["deleted"] = deleted
        os.remove(os.path.join(iw, "old.txt"))  # the replacement wins
        return True, 1, 0, "folded into journey.txt"
    monkeypatch.setattr(runway, "_resolve", fake_resolve)

    result = runway.apply_task("chunk-1", "t2", iw, ibranch, "main", True, lambda m: None)

    assert result["outcome"] == "resolved", result
    assert seen["deleted"] == {"old.txt": "UD"}
    assert not os.path.exists(os.path.join(iw, "old.txt"))
    assert os.path.exists(os.path.join(iw, "journey.txt"))
    assert not git(iw, "status", "--porcelain").strip()


def test_failed_resolve_leaves_the_integration_branch_untouched(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wt1 = start_task(repo, "t1", title="First")
    wt2 = start_task(repo, "t2", title="Second")
    write(wt1, {"a.txt": "one\n"})
    write(wt2, {"a.txt": "two\n", "extra.txt": "x\n"})
    iw, ibranch = runway.integration(repo, "main")
    runway.apply_task("chunk-1", "t1", iw, ibranch, "main", True, lambda m: None)
    head = git(iw, "rev-parse", "HEAD")
    monkeypatch.setattr(runway, "_resolve", lambda *a, **k: (False, 0, 0, "markers left in a.txt"))

    result = runway.apply_task("chunk-1", "t2", iw, ibranch, "main", True, lambda m: None)

    assert result["outcome"] == "conflict"
    assert git(iw, "rev-parse", "HEAD") == head
    assert not git(iw, "status", "--porcelain").strip()
    assert not os.path.exists(os.path.join(iw, ".git", "CHERRY_PICK_HEAD")) and "CHERRY_PICK_HEAD" not in git(iw, "status")
    assert os.path.isdir(wt2)  # the agent's worktree is kept for another try


def test_integration_branch_catches_up_when_the_base_moved_on_its_own(tmp_path):
    """Commits made straight on the base (not through Runway) reach runway/<base> by fast-forward, so the next merge
    and its checks run on current code; a runway/<base> with work of its own is left for the normal sync."""
    import subprocess as sp
    repo = tmp_path / "r"; repo.mkdir()
    g = lambda *a, cwd=repo: sp.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
    g("init", "-q", "-b", "main"); (repo / "a").write_text("1"); g("add", "-A"); g("commit", "-qm", "one")
    wtp, branch = runway.integration(str(repo), "main")
    (repo / "a").write_text("2"); g("add", "-A"); g("commit", "-qm", "two")
    runway.integration(str(repo), "main")
    assert g("rev-parse", "runway/main") == g("rev-parse", "main")
    import pathlib; (pathlib.Path(wtp) / "b").write_text("x"); g("add", "-A", cwd=wtp); g("commit", "-qm", "runway work", cwd=wtp)
    (repo / "a").write_text("3"); g("add", "-A"); g("commit", "-qm", "three")
    before = g("rev-parse", "runway/main")
    runway.integration(str(repo), "main")
    assert g("rev-parse", "runway/main") == before
