"""The merge gate tells flaky tests from real failures by rerunning only the tests that failed (pytest checks)."""
import json, os, sys

import runway
from test_checks import wt_path, write_agent_change
from test_git_flows import commit, git, make_repo, start_task

PYTEST = "%s -m pytest -q -p no:cacheprovider tests" % sys.executable

# every pytest session appends a line, so a test can tell which run it is in
CONFTEST = "def pytest_sessionstart(session):\n    open(%r, 'a').write('x\\n')\n"
RUN_NO = "len(open(%r).read().split())"


def project(tmp_path, tests):
    """A repo whose one check runs a pytest suite; tests maps file name -> source. Returns (repo, runs counter file)."""
    runs = str(tmp_path / "runs.txt")
    repo = make_repo(tmp_path, checks=False)
    files = {"tests/" + n: src.replace("RUN_NO", RUN_NO % runs) for n, src in tests.items()}
    files["tests/conftest.py"] = CONFTEST % runs
    files[".runway.json"] = json.dumps({"checks": [{"name": "unit", "cmd": PYTEST}]})
    commit(repo, "suite", files)
    start_task(repo, "t1", chunk="c1")
    write_agent_change(repo)
    return repo


def merge(repo):
    iw, ibranch = runway.integration(repo, "main")
    return iw, runway.apply_task("c1", "t1", iw, ibranch, "main", False, lambda m: None)


def test_a_test_that_fails_only_on_the_first_run_merges_as_flaky_and_is_counted(tmp_path):
    repo = project(tmp_path, {"test_a.py": "def test_ok():\n    assert True\n\n"
                                           "def test_flaky():\n    assert RUN_NO > 1\n"})
    iw, res = merge(repo)
    assert res["outcome"] == "merged", res
    assert res["flaky"] == ["tests/test_a.py::test_flaky"]
    assert os.path.exists(os.path.join(iw, "feature.txt"))
    assert any(f.get("phase") == "test" and "flaky" in f.get("stderr_tail", "") for f in runway.read_failures(n=20))
    ledger = json.load(open(runway.flaky_path()))
    assert ledger[repo]["tests/test_a.py::test_flaky"]["count"] == 1
    top = runway.flaky_top()
    assert top[0]["id"] == "tests/test_a.py::test_flaky" and top[0]["count"] == 1 and top[0]["last"]


def test_different_tests_failing_on_different_runs_no_longer_block_the_merge(tmp_path):
    """Run 1 fails test_a, run 2 would fail test_b: only test_a is rerun, and it passes."""
    repo = project(tmp_path, {"test_a.py": "def test_a():\n    assert RUN_NO != 1\n\n"
                                           "def test_b():\n    assert RUN_NO != 2\n"})
    iw, res = merge(repo)
    assert res["outcome"] == "merged" and res["flaky"] == ["tests/test_a.py::test_a"], res
    assert os.path.exists(os.path.join(iw, "feature.txt"))


def test_a_test_that_fails_on_every_rerun_blames_the_task_naming_only_it(tmp_path):
    repo = project(tmp_path, {"test_a.py": "import os\n\n"
                                           "def test_flaky():\n    assert RUN_NO > 1\n\n"
                                           "def test_breaks():\n    assert not os.path.exists('feature.txt')\n"})
    iw, res = merge(repo)
    assert res["outcome"] == "checks" and res["baseRed"] is False, res
    assert "this task caused it" in res["msg"]
    assert "tests/test_a.py::test_breaks" in res["msg"] and "test_flaky" not in res["msg"]
    assert not os.path.exists(os.path.join(iw, "feature.txt"))
    assert not res.get("flaky")
    assert json.load(open(runway.flaky_path()))[repo]["tests/test_a.py::test_flaky"]["count"] == 1


def test_a_failure_the_base_has_too_is_base_red(tmp_path):
    repo = project(tmp_path, {"test_a.py": "def test_broken():\n    assert False\n"})
    iw, res = merge(repo)
    assert res["outcome"] == "checks" and res["baseRed"] is True, res
    assert "already there" in res["msg"] and "tests/test_a.py::test_broken" in res["msg"]
    assert not os.path.exists(os.path.join(iw, "feature.txt"))
    assert not os.path.exists(runway.flaky_path())


def test_base_red_and_a_new_failure_blames_the_task_for_the_new_one(tmp_path):
    repo = project(tmp_path, {"test_a.py": "import os\n\n"
                                           "def test_broken():\n    assert False\n\n"
                                           "def test_breaks():\n    assert not os.path.exists('feature.txt')\n"})
    iw, res = merge(repo)
    assert res["outcome"] == "checks" and res["baseRed"] is False, res
    assert "tests/test_a.py::test_breaks" in res["msg"] and "test_broken" not in res["msg"]


def test_a_non_pytest_check_keeps_the_whole_run_recheck(tmp_path):
    repo = make_repo(tmp_path, checks=False)
    mark = tmp_path / "ran-once"
    commit(repo, "config", {".runway.json": json.dumps({"checks": [
        {"name": "unit", "cmd": f"[ -f '{mark}' ] || {{ touch '{mark}'; echo 'FAILED nothing'; exit 1; }}"}]})})
    start_task(repo, "t1", chunk="c1")
    write_agent_change(repo)
    iw, res = merge(repo)
    assert res["outcome"] == "merged" and res["flaky"] == "unit", res


def test_targeted_command_keeps_the_checks_interpreter_and_uses_test_sh_only_when_it_passes_args(tmp_path):
    iw = str(tmp_path)
    ids = ["tests/a.py::t[x y]"]
    assert runway.targeted_cmd("./.venv/bin/pytest -q tests", iw, iw, ids) == "./.venv/bin/pytest 'tests/a.py::t[x y]'"
    assert runway.targeted_cmd("cd sub && python3 -m pytest tests -x", iw, iw, ids) == "cd sub && python3 -m pytest 'tests/a.py::t[x y]'"
    assert runway.targeted_cmd("make test", iw, iw, ids) is None
    (tmp_path / "test.sh").write_text('#!/bin/bash\nexec python -m pytest -q "$@"\n')
    assert runway.targeted_cmd("./test.sh", iw, iw, ids) == "./test.sh 'tests/a.py::t[x y]'"
    (tmp_path / "test.sh").write_text('#!/bin/bash\nexec python -m pytest -q tests "$@"\n')
    assert runway.targeted_cmd("./test.sh", iw, iw, ids) is None  # it would also run the whole tests dir
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    assert runway.targeted_cmd("./test.sh", iw, iw, ids).startswith(str(tmp_path / ".venv" / "bin" / "python") + " -m pytest")


def test_failed_test_ids_reads_pytests_short_summary():
    text = "FAILED tests/a.py::test_x - assert 1 == 2\nERROR tests/b.py - ImportError\nFAILED tests/a.py::test_x - again\n1 failed"
    assert runway.failed_test_ids(text) == ["tests/a.py::test_x", "tests/b.py"]
