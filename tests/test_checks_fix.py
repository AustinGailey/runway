"""Checks-fix rounds: run.sh's run_task sends red project checks back to the agent (same session), and the helper's
checks-fix-prompt / checks-fix-gate decide and word it. run_task runs for real here, against a stand-in claude."""
import json, os, shutil, subprocess, sys

import pytest

import runway
from test_runner_start import EXTRACT, ROOT, git

# The stand-in agent: call 1 breaks the project (broken.txt); later calls ("fix rounds") act by AGENT_MODE.
CLAUDE = r"""#!/bin/bash
n=$(cat "$CALLS/n" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$CALLS/n"
printf '%s\n' "$*" > "$CALLS/args.$n"
if [ $n = 1 ]; then echo "broken" > broken.txt
else case "$AGENT_MODE" in
  fix) rm -f broken.txt;;
  vary) echo "still broken, attempt $n of $(sed -n ${n}p "$CALLS/words")" > broken.txt;;
  same) echo "still broken in the same way" > broken.txt;;
esac; fi
echo '{"type":"result","result":"ok","session_id":"sess-1","usage":{"input_tokens":1,"output_tokens":1}}'
"""
UNIT = {"name": "unit", "cmd": '[ ! -f broken.txt ] || { echo "FAIL: $(cat broken.txt)"; exit 1; }'}

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def run(tmp_path, mode, rounds, checks=(UNIT,), claude=CLAUDE, files=None):
    """Run run_task t1 in chunk c1 on a scratch repo whose .runway.json has `checks`. Returns (run dir, calls dir, repo)."""
    func = subprocess.run(["node", "-e", EXTRACT, os.path.join(ROOT, "src", "runway.src.html")],
                          check=True, capture_output=True, text=True).stdout
    home, binp, repo, calls = tmp_path / "home", tmp_path / "bin", tmp_path / "repo", tmp_path / "calls"
    runs = home / ".ai-planner" / "runs" / "c1"
    for p in (binp, repo, runs, calls):
        p.mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "helper", "runway.py"), home / ".ai-planner" / "runway.py")
    (calls / "words").write_text("zero\none\ntwo\nthree\nfour\nfive\n")
    (binp / "claude").write_text(claude)
    (binp / "claude").chmod(0o755)
    git(repo, "init", "-q", "-b", "main")
    (repo / ".runway.json").write_text(json.dumps({"checks": list(checks)}))
    (repo / ".gitignore").write_text(".claude/\n")
    for name, text in (files or {}).items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (runs / "t1.prompt.md").write_text("do it")
    script = "\n".join(["#!/bin/bash", 'export PATH="%s:$PATH"' % binp, 'D="%s"' % runs, 'H="$HOME/.ai-planner/runway.py"', "FIX_ROUNDS=0", "DENY=('Bash(rm:*)')",
                        "" if rounds is None else "CHECKS_FIX_ROUNDS=%d" % rounds, func,
                        "run_task t1 %s '' acceptEdits runway-t1 '' 0 '' ''" % repo])
    (tmp_path / "run.sh").write_text(script)
    env = dict(os.environ, HOME=str(home), CALLS=str(calls), AGENT_MODE=mode, RUNWAY_NO_KEYCHAIN="1")
    subprocess.run(["bash", str(tmp_path / "run.sh")], capture_output=True, env=env, timeout=120)
    return runs, calls, repo


def calls_made(calls):
    return int((calls / "n").read_text())


def checks_state(runs):
    return json.loads((runs / "t1.checks.json").read_text())["state"]


def fixfile(runs):
    return json.loads((runs / "t1.checksfix.json").read_text())


def test_checks_red_once_then_green_after_one_round(tmp_path):
    runs, calls, _ = run(tmp_path, "fix", 2)
    assert calls_made(calls) == 2 and checks_state(runs) == "green"
    assert (runs / "t1.checksrounds").read_text().strip() == "1"
    assert "--resume sess-1" in (calls / "args.2").read_text()  # the same session
    prompt = (runs / "t1.checksfix1.md").read_text()
    assert '"unit"' in prompt and "FAIL: broken" in prompt and "not the test" in prompt
    assert "stop" not in fixfile(runs)
    t = runway.chunk_status("c1")["tasks"]["t1"]
    assert t["checksRounds"] == 1 and t["checks"]["state"] == "green"


def test_always_red_stops_at_the_cap(tmp_path):
    runs, calls, _ = run(tmp_path, "vary", 2)
    assert calls_made(calls) == 3  # the agent, then two fix rounds
    assert checks_state(runs) == "red"
    assert (runs / "t1.checksrounds").read_text().strip() == "2"
    assert fixfile(runs)["stop"] == "limit" and len(fixfile(runs)["rounds"]) == 2


def test_same_failure_twice_stops_early(tmp_path):
    runs, calls, _ = run(tmp_path, "same", 3)
    # round 1 changes the output (broken -> "still broken..."), round 2 would repeat round 1's failure
    assert calls_made(calls) == 3 and (runs / "t1.checksrounds").read_text().strip() == "2"
    assert fixfile(runs)["stop"] == "repeated" and checks_state(runs) == "red"


def test_base_already_red_starts_no_round(tmp_path):
    runs, calls, _ = run(tmp_path, "fix", 2, checks=[{"name": "unit", "cmd": "echo the base is broken; exit 1"}])
    assert calls_made(calls) == 1 and checks_state(runs) == "red"
    assert not (runs / "t1.checksrounds").exists()
    f = fixfile(runs)
    assert f["baseRed"] is True and f["stop"] == "base-red" and f["baseStep"] == "unit"
    t = runway.chunk_status("c1")["tasks"]["t1"]
    assert t["checksFix"]["baseRed"] is True and "checksRounds" not in t


def test_unconfigured_rounds_keep_todays_behaviour(tmp_path):
    runs, calls, _ = run(tmp_path, "fix", None)
    assert calls_made(calls) == 1 and checks_state(runs) == "red"
    assert not (runs / "t1.checksfix.json").exists()


def test_zero_rounds_means_none(tmp_path):
    runs, calls, _ = run(tmp_path, "fix", 0)
    assert calls_made(calls) == 1 and checks_state(runs) == "red"


# ---- a pytest check is judged per test: base-only and flaky failures are not the task's ----
# The stand-in agent's first pass adds tests/test_new.py (red while broken.txt exists) when ADD_TEST=1, and
# tests/test_flaky.py (red on its first run only, counted in $CALLS) when ADD_FLAKY=1; a fix round removes broken.txt.
PY_CLAUDE = r"""#!/bin/bash
n=$(cat "$CALLS/n" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$CALLS/n"
printf '%s\n' "$*" > "$CALLS/args.$n"
if [ $n = 1 ]; then
  echo "broken" > broken.txt
  if [ "$ADD_TEST" = 1 ]; then printf 'import os\ndef test_mine():\n    assert not os.path.exists("broken.txt"), "MINE_BROKEN"\n' > tests/test_new.py; fi
  if [ "$ADD_FLAKY" = 1 ]; then printf 'import os\ndef test_wobbly():\n    p = os.path.join(os.environ["CALLS"], "wobbly")\n    k = int(open(p).read()) + 1 if os.path.exists(p) else 1\n    open(p, "w").write(str(k))\n    assert k > 1, "WOBBLY"\n' > tests/test_flaky.py; fi
  git add -A >/dev/null 2>&1; git -c user.email=a@a -c user.name=a commit -qm work >/dev/null 2>&1
else rm -f broken.txt; git add -A >/dev/null 2>&1; git -c user.email=a@a -c user.name=a commit -qm fix >/dev/null 2>&1
fi
echo '{"type":"result","result":"ok","session_id":"sess-1","usage":{"input_tokens":1,"output_tokens":1}}'
"""
OLD_RED = "def test_old():\n    assert False, 'OLD_BROKEN_ON_MAIN'\n"
OK = "def test_fine():\n    pass\n"


def py_run(tmp_path, base_red, add_test, add_flaky=False):
    pytest_check = {"name": "tests", "cmd": "%s -m pytest -q -p no:cacheprovider tests" % sys.executable, "timeout": 120}
    files = {"tests/test_old.py": OLD_RED if base_red else OK}
    os.environ["ADD_TEST"], os.environ["ADD_FLAKY"] = "1" if add_test else "0", "1" if add_flaky else "0"
    try:
        return run(tmp_path, "fix", 2, checks=[pytest_check], claude=PY_CLAUDE, files=files)
    finally:
        del os.environ["ADD_TEST"], os.environ["ADD_FLAKY"]


def test_base_only_failure_plus_own_failure_sends_only_the_own_one(tmp_path):
    runs, calls, _ = py_run(tmp_path, base_red=True, add_test=True)
    assert calls_made(calls) == 2  # the agent, then exactly one round
    assert (runs / "t1.checksrounds").read_text().strip() == "1"
    prompt = (runs / "t1.checksfix1.md").read_text()
    mine = prompt.split("already failing")[0]
    assert "tests/test_new.py::test_mine" in mine and "MINE_BROKEN" in prompt
    assert "test_old" not in mine and "OLD_BROKEN_ON_MAIN" not in prompt  # named as not yours, its output left out
    assert "already failing on main, not yours" in prompt and "tests/test_old.py::test_old" in prompt
    f = fixfile(runs)
    # after the round only the base's failure is left: it stops there instead of spending another round on it
    assert f["stop"] == "base-red" and f["baseRed"] is True and f["split"]["mine"] == []
    assert f["baseIds"] == {"tests/test_old.py::test_old": True, "tests/test_new.py::test_mine": False}
    assert "OLD_BROKEN_ON_MAIN" not in f["rounds"][0]["tail"]
    t = runway.chunk_status("c1")["tasks"]["t1"]
    assert t["checksFix"]["baseOnly"] == ["tests/test_old.py::test_old"]


def test_only_base_failures_start_no_round(tmp_path):
    runs, calls, _ = py_run(tmp_path, base_red=True, add_test=False)
    assert calls_made(calls) == 1 and checks_state(runs) == "red"
    assert not (runs / "t1.checksrounds").exists()
    f = fixfile(runs)
    assert f["stop"] == "base-red" and f["baseRed"] is True and f["baseStep"] == "tests"
    assert f["split"]["baseOnly"] == ["tests/test_old.py::test_old"] and f["split"]["mine"] == []


def test_flaky_own_failure_starts_no_round(tmp_path):
    runs, calls, _ = py_run(tmp_path, base_red=False, add_test=False, add_flaky=True)
    assert calls_made(calls) == 1 and checks_state(runs) == "red"
    assert not (runs / "t1.checksrounds").exists()
    f = fixfile(runs)
    assert f["stop"] == "flaky" and not f.get("baseRed")
    assert f["split"]["flaky"] == ["tests/test_flaky.py::test_wobbly"] and f["split"]["mine"] == []


# ---- helper unit tests ----
def put(d, name, text):
    with open(os.path.join(d, name), "w") as f:
        f.write(text)


def red_run(tmp_path, tail_lines=300, step="unit"):
    d = runway.RUNS + "/c1"
    os.makedirs(d, exist_ok=True)
    put(d, "t1.checks.json", json.dumps({"state": "red", "step": step, "rc": 1}))
    put(d, "t1.checks.log", "".join("line %d\n" % i for i in range(tail_lines)))
    put(d, "t1.json", json.dumps({"session_id": "sess-7"}))
    return d


def test_checks_fix_prompt_names_the_step_and_carries_the_log_tail(tmp_path):
    d = red_run(tmp_path)
    assert runway.checks_fix_prompt(d, "t1", 1) == "sess-7"
    text = open(os.path.join(d, "t1.checksfix1.md")).read()
    assert '"unit"' in text and "line 299" in text and "line 180" in text and "line 179" not in text  # last 120 lines
    assert "not the test" in text and "Commit" in text
    rec = json.load(open(os.path.join(d, "t1.checksfix.json")))
    assert rec["rounds"][0]["round"] == 1 and rec["rounds"][0]["step"] == "unit" and "line 299" in rec["rounds"][0]["tail"]


def test_checks_fix_prompt_without_a_session_prints_nothing_to_resume(tmp_path):
    d = red_run(tmp_path)
    os.remove(os.path.join(d, "t1.json"))
    assert runway.checks_fix_prompt(d, "t1", 1) == ""


def test_gate_stops_when_checks_are_not_red(tmp_path):
    d = red_run(tmp_path)
    put(d, "t1.checks.json", json.dumps({"state": "green"}))
    assert runway.checks_fix_gate(d, "t1", 1, 2) == "stop"
    assert not os.path.exists(os.path.join(d, "t1.checksfix.json"))


def test_gate_failure_signature_ignores_numbers():
    assert runway._failure_sig("12 failed in 3.4s at 0xdeadbeef1") == runway._failure_sig("7 failed in 9.1s at 0xcafebabe2")
