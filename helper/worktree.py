"""Worktree lifecycle for agent chunks: one git worktree and branch per chunk, named from the chunk id.

    create(repo, chunk)            worktree at .claude/worktrees/<chunk> on branch runway/<chunk>; idempotent
    list_worktrees(repo, base)     runway/* worktrees with their state (active, merged, missing)
    find_orphans(repo)             leftovers: unregistered directories and runway/* branches with no worktree
    remove(repo, chunk, base)      remove the worktree; delete its branch only when it is merged into base
    prune(repo)                    drop git's records of worktrees whose directories are gone

Nothing here touches the user's checkout beyond creating and removing worktrees and runway/* branches.
A dirty base repo (tracked or untracked changes, outside .claude/worktrees) makes create() raise DirtyRepoError.
"""
import os, re, subprocess
from dataclasses import dataclass

BRANCH_PREFIX = "runway/"
WT_DIR = os.path.join(".claude", "worktrees")
CHUNK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class WorktreeError(Exception):
    pass


class DirtyRepoError(WorktreeError):
    def __init__(self, repo, files):
        self.repo, self.files = repo, files
        shown = ", ".join(files[:5]) + (" and %d more" % (len(files) - 5) if len(files) > 5 else "")
        super().__init__("%s has uncommitted changes (%s); commit or stash them before creating a chunk worktree" % (repo, shown))


@dataclass
class Worktree:
    chunk: str
    path: str
    branch: str
    head: str
    exists: bool = True
    merged: bool = False

    @property
    def stale(self):
        """Its directory is gone, so git's record is leftover. (A branch already in base is not stale: a
        fresh chunk with no commits yet is also 'in base', and its worktree is still live.)"""
        return not self.exists


@dataclass
class Orphan:
    kind: str  # "unregistered-dir" or "branch-without-worktree"
    chunk: str
    path: str = ""
    branch: str = ""
    merged: bool = False


@dataclass
class Removed:
    chunk: str
    path: str
    branch: str
    branch_deleted: bool = False
    kept_reason: str = ""


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def _check_chunk(chunk):
    if not isinstance(chunk, str) or not CHUNK_RE.match(chunk):
        raise WorktreeError("invalid chunk id %r: use letters, digits, dot, dash or underscore" % (chunk,))
    return chunk


def branch_name(chunk):
    """Deterministic branch for a chunk id: runway/<chunk>."""
    return BRANCH_PREFIX + _check_chunk(chunk)


def worktree_path(repo, chunk):
    """Deterministic path for a chunk id: <repo>/.claude/worktrees/<chunk>."""
    return os.path.join(repo, WT_DIR, _check_chunk(chunk))


def _chunk_of_branch(branch):
    return branch[len(BRANCH_PREFIX):] if branch.startswith(BRANCH_PREFIX) else None


def _registered(repo):
    """git's worktree records as dicts: path, head, branch (short name or ''), prunable (bool)."""
    rc, out = _git(repo, "worktree", "list", "--porcelain")
    if rc:
        raise WorktreeError("git worktree list failed: " + out[-300:])
    entries, cur = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = {"path": os.path.realpath(line[len("worktree "):]), "head": "", "branch": "", "prunable": False}
            entries.append(cur)
        elif cur is None or not line:
            continue
        elif line.startswith("HEAD "):
            cur["head"] = line[5:]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].replace("refs/heads/", "", 1)
        elif line.startswith("prunable"):
            cur["prunable"] = True
    return entries


def _is_merged(repo, branch, base):
    rc, _ = _git(repo, "merge-base", "--is-ancestor", "refs/heads/" + branch, base)
    return rc == 0


def _dirty_files(repo):
    """Changed paths in repo, excluding the chunk worktrees directory (agents' worktrees live there)."""
    r = subprocess.run(["git", "-C", repo, "status", "--porcelain", "-z", "--untracked-files=all"], capture_output=True, text=True)
    if r.returncode:
        raise WorktreeError("git status failed: " + r.stderr.strip()[-300:])
    files, parts, i = [], r.stdout.split("\0"), 0
    while i < len(parts):
        e = parts[i]
        if len(e) > 3:
            path = e[3:]
            if e[0] in "RC" and i + 1 < len(parts):  # rename: the old path is a change too
                i += 1
            if not path.startswith(WT_DIR + os.sep):
                files.append(path)
        i += 1
    return files


def create(repo, chunk, start="HEAD"):
    """Create runway/<chunk> and its worktree from start. Idempotent: an existing, registered worktree
    for the chunk is returned as it is. Raises DirtyRepoError when the base repo has uncommitted changes."""
    branch, path = branch_name(chunk), worktree_path(repo, chunk)
    _git(repo, "worktree", "prune")
    for wt in _registered(repo):
        if wt["path"] == os.path.realpath(path) and wt["branch"] == branch and not wt["prunable"]:
            return Worktree(chunk, path, branch, wt["head"])
    dirty = _dirty_files(repo)
    if dirty:
        raise DirtyRepoError(repo, dirty)
    if os.path.lexists(path):
        raise WorktreeError("%s exists but is not a registered worktree on %s; remove it by hand" % (path, branch))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rc, _ = _git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + branch)
    args = ["worktree", "add", "-q", path, branch] if rc == 0 else ["worktree", "add", "-q", "-b", branch, path, start]
    rc, out = _git(repo, *args)
    if rc:
        raise WorktreeError("could not create worktree for %s: %s" % (chunk, out[-300:]))
    _, head = _git(path, "rev-parse", "HEAD")
    return Worktree(chunk, path, branch, head)


def list_worktrees(repo, base="HEAD"):
    """Every registered runway/* worktree, with whether it still exists and whether its branch is merged into base."""
    out = []
    for wt in _registered(repo):
        chunk = _chunk_of_branch(wt["branch"])
        if chunk is None:
            continue
        exists = not wt["prunable"] and os.path.isdir(wt["path"])
        out.append(Worktree(chunk, wt["path"], wt["branch"], wt["head"], exists, _is_merged(repo, wt["branch"], base)))
    return out


def find_orphans(repo, base="HEAD"):
    """Things no active worktree accounts for: a directory under .claude/worktrees that git does not know,
    and a runway/* branch with no worktree. Nothing is changed; callers decide what to remove."""
    registered = _registered(repo)
    known = {wt["path"] for wt in registered}
    checked_out = {wt["branch"] for wt in registered if wt["branch"]}
    orphans = []
    root = os.path.join(repo, WT_DIR)
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            p = os.path.realpath(os.path.join(root, name))
            if os.path.isdir(p) and p not in known and CHUNK_RE.match(name):
                orphans.append(Orphan("unregistered-dir", name, path=os.path.join(root, name)))
    rc, refs = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/" + BRANCH_PREFIX)
    for ref in (refs.splitlines() if rc == 0 else []):
        if ref in checked_out:
            continue
        orphans.append(Orphan("branch-without-worktree", _chunk_of_branch(ref), branch=ref, merged=_is_merged(repo, ref, base)))
    return orphans


def remove(repo, chunk, base="HEAD"):
    """Remove the chunk's worktree and, if its branch is merged into base, the branch too.
    Refuses (WorktreeError) a worktree with uncommitted changes. An unmerged branch is kept and says why."""
    branch, path = branch_name(chunk), worktree_path(repo, chunk)
    res = Removed(chunk, path, branch)
    registered = [wt for wt in _registered(repo) if wt["branch"] == branch]
    if registered and not registered[0]["prunable"] and os.path.isdir(registered[0]["path"]):
        wt_path = registered[0]["path"]
        r = subprocess.run(["git", "-C", wt_path, "status", "--porcelain"], capture_output=True, text=True)
        if r.stdout.strip():
            raise WorktreeError("%s has uncommitted changes; the worktree was kept" % wt_path)
        rc, out = _git(repo, "worktree", "remove", wt_path)
        if rc:
            raise WorktreeError("could not remove %s: %s" % (wt_path, out[-300:]))
    _git(repo, "worktree", "prune")
    rc, _ = _git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + branch)
    if rc != 0:
        return res
    if not _is_merged(repo, branch, base):
        res.kept_reason = "not merged into %s" % base
        return res
    rc, out = _git(repo, "branch", "-D", branch)
    if rc:
        raise WorktreeError("could not delete branch %s: %s" % (branch, out[-300:]))
    res.branch_deleted = True
    return res


def prune(repo):
    """Drop git's records of worktrees whose directories are gone. Returns the number of records dropped."""
    before = sum(1 for wt in _registered(repo) if wt["prunable"])
    rc, out = _git(repo, "worktree", "prune", "-v")
    if rc:
        raise WorktreeError("git worktree prune failed: " + out[-300:])
    return before
