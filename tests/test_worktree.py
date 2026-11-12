"""Worktree lifecycle (helper/worktree.py) against throwaway repos under pytest's tmp_path."""
import os, shutil, subprocess

import pytest

import worktree
from worktree import DirtyRepoError, WorktreeError


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def commit_file(repo, name, text, msg):
    with open(os.path.join(repo, name), "w") as f:
        f.write(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    path = str(tmp_path / "proj")
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    commit_file(path, "a.txt", "base\n", "base")
    return path


def branches(repo):
    return set(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/runway/").split())


def test_branch_and_path_are_deterministic(repo):
    assert worktree.branch_name("chunk-7") == "runway/chunk-7"
    assert worktree.branch_name("chunk-7") == worktree.branch_name("chunk-7")
    assert worktree.worktree_path(repo, "chunk-7") == os.path.join(repo, ".claude", "worktrees", "chunk-7")


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", "-dash", "has space", None])
def test_rejects_unsafe_chunk_ids(repo, bad):
    with pytest.raises(WorktreeError):
        worktree.create(repo, bad)


def test_create_makes_worktree_and_branch(repo):
    wt = worktree.create(repo, "chunk-1")
    assert wt.branch == "runway/chunk-1"
    assert os.path.isdir(wt.path)
    assert git(wt.path, "rev-parse", "--abbrev-ref", "HEAD").strip() == "runway/chunk-1"
    assert git(repo, "rev-parse", "HEAD").strip() == wt.head
    assert branches(repo) == {"runway/chunk-1"}


def test_recreate_is_idempotent(repo):
    first = worktree.create(repo, "chunk-1")
    commit_file(first.path, "w.txt", "work\n", "agent work")
    again = worktree.create(repo, "chunk-1")
    assert again.path == first.path
    assert again.head == git(first.path, "rev-parse", "HEAD").strip()
    assert branches(repo) == {"runway/chunk-1"}
    assert [w.chunk for w in worktree.list_worktrees(repo)] == ["chunk-1"]


def test_recreate_after_branch_survives_reattaches_it(repo):
    first = worktree.create(repo, "chunk-2")
    commit_file(first.path, "w.txt", "work\n", "agent work")
    tip = git(first.path, "rev-parse", "HEAD").strip()
    shutil.rmtree(first.path)
    git(repo, "worktree", "prune")
    again = worktree.create(repo, "chunk-2")
    assert again.head == tip
    assert os.path.isdir(again.path)


def test_dirty_tracked_change_refuses_create(repo):
    with open(os.path.join(repo, "a.txt"), "w") as f:
        f.write("edited\n")
    with pytest.raises(DirtyRepoError) as err:
        worktree.create(repo, "chunk-1")
    assert "a.txt" in err.value.files
    assert "uncommitted" in str(err.value)
    assert branches(repo) == set()


def test_dirty_untracked_file_refuses_create(repo):
    with open(os.path.join(repo, "notes.txt"), "w") as f:
        f.write("scratch\n")
    with pytest.raises(DirtyRepoError):
        worktree.create(repo, "chunk-1")
    assert not os.path.exists(worktree.worktree_path(repo, "chunk-1"))


def test_agent_worktrees_dir_does_not_count_as_dirty(repo):
    worktree.create(repo, "chunk-1")
    wt = worktree.create(repo, "chunk-2")
    assert os.path.isdir(wt.path)


def test_remove_deletes_merged_branch(repo):
    wt = worktree.create(repo, "chunk-1")
    commit_file(wt.path, "w.txt", "work\n", "agent work")
    git(repo, "merge", "-q", "--ff-only", "runway/chunk-1")
    res = worktree.remove(repo, "chunk-1", base="main")
    assert res.branch_deleted
    assert not os.path.exists(wt.path)
    assert branches(repo) == set()


def test_remove_keeps_unmerged_branch(repo):
    wt = worktree.create(repo, "chunk-1")
    commit_file(wt.path, "w.txt", "work\n", "agent work")
    res = worktree.remove(repo, "chunk-1", base="main")
    assert not res.branch_deleted
    assert "not merged" in res.kept_reason
    assert not os.path.exists(wt.path)
    assert branches(repo) == {"runway/chunk-1"}


def test_remove_refuses_dirty_worktree(repo):
    wt = worktree.create(repo, "chunk-1")
    with open(os.path.join(wt.path, "scratch.txt"), "w") as f:
        f.write("unsaved\n")
    with pytest.raises(WorktreeError, match="uncommitted"):
        worktree.remove(repo, "chunk-1", base="main")
    assert os.path.isdir(wt.path)
    assert branches(repo) == {"runway/chunk-1"}


def test_orphan_detection_finds_unregistered_dir_and_branch(repo):
    wt = worktree.create(repo, "chunk-1")
    git(repo, "worktree", "remove", "--force", wt.path)  # branch stays, no worktree
    os.makedirs(os.path.join(repo, ".claude", "worktrees", "chunk-9"))  # directory git does not know
    kinds = sorted((o.kind, o.chunk) for o in worktree.find_orphans(repo, base="main"))
    assert kinds == [("branch-without-worktree", "chunk-1"), ("unregistered-dir", "chunk-9")]


def test_active_worktree_is_not_an_orphan(repo):
    worktree.create(repo, "chunk-1")
    assert worktree.find_orphans(repo, base="main") == []


def test_missing_directory_is_stale_and_pruned(repo):
    wt = worktree.create(repo, "chunk-1")
    shutil.rmtree(wt.path)
    listed = worktree.list_worktrees(repo, base="main")
    assert len(listed) == 1 and not listed[0].exists and listed[0].stale
    assert worktree.prune(repo) == 1
    assert worktree.list_worktrees(repo, base="main") == []
    assert worktree.prune(repo) == 0


def test_live_worktree_is_not_stale_until_merged_or_missing(repo):
    wt = worktree.create(repo, "chunk-1")
    commit_file(wt.path, "w.txt", "work\n", "agent work")
    active = worktree.list_worktrees(repo, base="main")[0]
    assert active.exists and not active.merged and not active.stale
    git(repo, "merge", "-q", "--ff-only", "runway/chunk-1")
    merged = worktree.list_worktrees(repo, base="main")[0]
    assert merged.merged and merged.exists and not merged.stale
