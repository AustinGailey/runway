"""run.sh's run_task: a Runway branch is never a start branch, and a fresh worktree gets its locked dependencies."""
import os, shlex, shutil, subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pull run_task out of runnerScript's array of single-quoted lines, exactly as the page writes it.
EXTRACT = r"""
const fs=require('fs');const src=fs.readFileSync(process.argv[1],'utf8');
const lines=src.slice(src.indexOf("    'run_task() {',")).split('\n');const out=[];const bin='claude';
for(const l of lines){const t=l.trim().replace(/,$/,'');if(!t)continue;let v;try{v=eval(t)}catch(e){break}
  if(typeof v!=='string')break;out.push(v);if(v==='}')break;}
process.stdout.write(out.join('\n')+'\n');
"""

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def run(tmp_path, start_from, lock=True):
    func = subprocess.run(["node", "-e", EXTRACT, os.path.join(ROOT, "src", "runway.src.html")],
                          check=True, capture_output=True, text=True).stdout
    assert func.startswith("run_task() {") and func.rstrip().endswith("}")
    binp, repo, runs = tmp_path / "bin", tmp_path / "repo", tmp_path / "runs"
    for p in (binp, repo, runs):
        p.mkdir()
    (binp / "claude").write_text('#!/bin/bash\necho \'{"type":"result","result":"ok"}\'\n')
    # a stand-in npm that records the call and makes node_modules, so the test needs no network
    (binp / "npm").write_text('#!/bin/bash\necho "npm $*" >> "%s/npm.calls"; mkdir -p node_modules\n' % tmp_path)
    for f in ("claude", "npm"):
        (binp / f).chmod(0o755)
    git(repo, "init", "-q", "-b", "main")
    (repo / "package.json").write_text('{"name":"x"}\n')
    if lock:
        (repo / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    git(repo, "branch", "runway-task/t1")
    git(repo, "branch", "runway/main")
    script = "\n".join([
        "#!/bin/bash", 'export PATH="%s:$PATH"' % binp, 'D="%s"' % runs, "FIX_ROUNDS=0", "DENY=('Bash(rm:*)')",
        func.replace("$HOME/.ai-planner/runway.py", "/nonexistent"),
        "%srun_task t1 %s '' acceptEdits runway-t1 '' 0 '' ''" % ("START_FROM=%s " % shlex.quote(start_from) if start_from else "", repo)])
    (tmp_path / "run.sh").write_text(script)
    subprocess.run(["bash", str(tmp_path / "run.sh")], capture_output=True)  # the last line exits 1 when there is no helper
    return runs, repo


def test_task_branch_as_start_branch_is_ignored(tmp_path):
    runs, _ = run(tmp_path, "runway-task/t1")
    meta = (runs / "t1.meta").read_text()
    assert '"base":"main"' in meta, meta
    assert "Ignored start branch runway-task/t1" in (runs / "t1.err").read_text()


def test_integration_branch_as_start_branch_is_ignored(tmp_path):
    runs, _ = run(tmp_path, "runway/main")
    assert '"base":"main"' in (runs / "t1.meta").read_text()


def test_fresh_worktree_installs_locked_deps(tmp_path):
    runs, repo = run(tmp_path, None)
    assert (tmp_path / "npm.calls").read_text().strip() == "npm ci --no-audit --no-fund"
    assert (repo / ".claude" / "worktrees" / "runway-t1" / "node_modules").is_dir()


def test_no_lockfile_installs_nothing(tmp_path):
    run(tmp_path, None, lock=False)
    assert not (tmp_path / "npm.calls").exists()


def test_missing_start_branch_falls_back_to_current_branch(tmp_path):
    runs, _ = run(tmp_path, "Client flow graph: a task title")
    assert '"base":"main"' in (runs / "t1.meta").read_text()
    assert "was not found" in (runs / "t1.err").read_text()
    assert (runs / "t1.exit").read_text().strip() == "0"


def _args_run(tmp_path, with_settings):
    func = subprocess.run(["node", "-e", EXTRACT, os.path.join(ROOT, "src", "runway.src.html")],
                          check=True, capture_output=True, text=True).stdout
    binp, repo, runs = tmp_path / "bin", tmp_path / "repo", tmp_path / "runs"
    for p in (binp, repo, runs):
        p.mkdir()
    (binp / "claude").write_text('#!/bin/bash\nprintf "%%s\\n" "$@" > "%s/claude.args"\necho \'{"type":"result","result":"ok"}\'\n' % tmp_path)
    (binp / "claude").chmod(0o755)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    (runs / "t1.prompt.md").write_text("do it")
    if with_settings:
        (runs / "t1.settings.json").write_text('{"sandbox":{"enabled":true}}')
    script = "\n".join(["#!/bin/bash", 'export PATH="%s:$PATH"' % binp, 'D="%s"' % runs, "FIX_ROUNDS=0", "DENY=('Bash(rm:*)')",
                        func.replace("$HOME/.ai-planner/runway.py", "/nonexistent"),
                        "run_task t1 %s '' dontAsk '' '' 0 '' ''" % repo])
    (tmp_path / "run.sh").write_text(script)
    subprocess.run(["bash", str(tmp_path / "run.sh")], capture_output=True)
    return (tmp_path / "claude.args").read_text().split("\n")


def test_sandboxed_task_gets_its_settings_file_and_no_old_deny_list(tmp_path):
    a = _args_run(tmp_path, True)
    assert a[a.index("--settings") + 1].endswith("t1.settings.json")
    assert "--disallowedTools" not in a and a[a.index("--permission-mode") + 1] == "dontAsk"


def test_unsandboxed_task_keeps_the_deny_list(tmp_path):
    a = _args_run(tmp_path, False)
    assert "--settings" not in a and "--disallowedTools" in a


def test_agent_edit_rule_is_relative_to_the_working_folder():
    # "Edit(/**)" anchors to the settings file's folder (~/.ai-planner/runs/<chunk>), which denied every edit in the
    # worktree. The rule must be cwd-relative.
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    body = src[src.index("function agentSettings(p){"):]
    body = body[:body.index("\n}\n")]
    assert '"Edit(./**)"' in body and '"Edit(/**)"' not in body
