"""Per-project test and merge config (.runway.json + overrides) in helper/runway.py. Throwaway repos under tmp_path only."""
import json, os, subprocess

import pytest

import runway


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def write_config(repo, obj):
    with open(os.path.join(repo, runway.CONFIG_FILE), "w") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


@pytest.fixture
def site(tmp_path):
    repo = tmp_path / "painter-site"
    repo.mkdir()
    (repo / "index.html").write_text("<h1>Painter</h1>\n")
    return str(repo)


@pytest.fixture
def helper(tmp_path):
    repo = tmp_path / "helper-project"
    (repo / "helper").mkdir(parents=True)
    (repo / "helper" / "runway.py").write_text("print('hi')\n")
    return str(repo)


# ---------- defaults ----------
def test_site_defaults_have_no_test_suite_and_squash(site):
    cfg = runway.project_config(site)
    assert cfg == {"type": "site", "test": [], "build": None, "baseBranch": None, "mergeStrategy": "squash"}


def test_python_defaults_run_pytest_and_merge_commit(helper):
    cfg = runway.project_config(helper)
    assert cfg == {"type": "python", "test": ["python3 -m pytest -q"], "build": None, "baseBranch": None, "mergeStrategy": "merge"}


def test_site_and_python_defaults_differ(site, helper):
    a, b = runway.project_config(site), runway.project_config(helper)
    assert a["test"] != b["test"] and a["mergeStrategy"] != b["mergeStrategy"]


def test_detection_reads_packaging_files_and_root_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    assert runway.detect_type(str(tmp_path)) == "python"
    other = tmp_path / "other"
    other.mkdir()
    (other / "build.py").write_text("")  # a Python file at the root counts too
    assert runway.detect_type(str(other)) == "python"
    plain = tmp_path / "plain"
    plain.mkdir()
    assert runway.detect_type(str(plain)) == "site"


def test_no_config_file_and_no_overrides_is_just_defaults(site):
    assert not os.path.exists(os.path.join(site, runway.CONFIG_FILE))
    assert runway.project_config(site)["type"] == "site"


# ---------- valid and partial configs ----------
def test_full_valid_config_is_read(site):
    write_config(site, {"type": "site", "test": ["npx playwright test"], "build": "node render.js",
                        "baseBranch": "gh-pages", "mergeStrategy": "merge"})
    assert runway.project_config(site) == {"type": "site", "test": ["npx playwright test"], "build": "node render.js",
                                           "baseBranch": "gh-pages", "mergeStrategy": "merge"}


def test_partial_config_fills_the_rest_from_the_type_defaults(helper):
    write_config(helper, {"test": ["python3 -m unittest"]})
    cfg = runway.project_config(helper)
    assert cfg["type"] == "python"  # detected
    assert cfg["test"] == ["python3 -m unittest"]
    assert cfg["mergeStrategy"] == "merge"  # still the python default


def test_a_single_test_command_string_becomes_a_list(site):
    write_config(site, {"test": "npm test"})
    assert runway.project_config(site)["test"] == ["npm test"]


def test_empty_test_list_means_no_tests_even_for_python(helper):
    write_config(helper, {"test": []})
    assert runway.project_config(helper)["test"] == []


def test_null_build_and_null_base_branch_are_accepted(site):
    write_config(site, {"build": None, "baseBranch": None})
    cfg = runway.project_config(site)
    assert cfg["build"] is None and cfg["baseBranch"] is None


# ---------- invalid configs ----------
@pytest.mark.parametrize("text, message", [
    ("{not json", "is not valid JSON"),
    ("[]", "must be a JSON object"),
    ('{"tests": ["npm test"]}', 'unknown key "tests" (did you mean "test"?)'),
    ('{"mergeStrategy": "rebase"}', '"mergeStrategy" must be "squash" or "merge", not "rebase"'),
    ('{"type": "static"}', '"type" must be "site" or "python", not "static"'),
    ('{"test": 3}', '"test" must be a command or a list of commands, not 3'),
    ('{"test": ["npm test", ""]}', '"test" must be a command or a list of commands'),
    ('{"build": ["a", "b"]}', '"build" must be one command, or null for no build check'),
    ('{"build": ""}', '"build" must be one command'),
    ('{"baseBranch": "main branch"}', '"baseBranch" must be a branch name'),
    ('{"baseBranch": "-main"}', '"baseBranch" must be a branch name'),
])
def test_bad_config_raises_a_readable_error(site, text, message):
    write_config(site, text)
    with pytest.raises(runway.ConfigError) as e:
        runway.project_config(site)
    assert message in str(e.value)
    assert runway.CONFIG_FILE in str(e.value)


def test_unknown_key_without_a_close_match_lists_the_known_keys(site):
    write_config(site, {"banana": 1})
    with pytest.raises(runway.ConfigError, match="Known keys: type, test, build, baseBranch, mergeStrategy"):
        runway.project_config(site)


def test_bad_override_names_the_project_settings_as_its_source(site):
    with pytest.raises(runway.ConfigError, match="project settings: \"mergeStrategy\""):
        runway.project_config(site, {"mergeStrategy": "fast-forward"})


# ---------- override precedence ----------
def test_file_beats_defaults_and_override_beats_file(helper):
    write_config(helper, {"test": ["make test"], "mergeStrategy": "squash"})
    cfg = runway.project_config(helper, {"test": ["pytest tests/test_x.py"]})
    assert cfg["test"] == ["pytest tests/test_x.py"]  # override wins
    assert cfg["mergeStrategy"] == "squash"  # file wins over the python default
    assert cfg["build"] is None  # untouched key falls back to the default


def test_override_type_picks_the_defaults_over_the_file_and_detection(site):
    write_config(site, {"type": "site"})
    cfg = runway.project_config(site, {"type": "python"})
    assert cfg["type"] == "python"
    assert cfg["test"] == ["python3 -m pytest -q"]
    assert cfg["mergeStrategy"] == "merge"


def test_file_type_beats_detection(helper):
    write_config(helper, {"type": "site"})
    assert runway.project_config(helper)["type"] == "site"


def test_override_only_none_values_still_win(site):
    write_config(site, {"baseBranch": "main"})
    assert runway.project_config(site, {"baseBranch": None})["baseBranch"] is None


# ---------- the merge path uses the loader ----------
def test_apply_task_stops_with_the_config_message_and_changes_nothing(tmp_path):
    repo = str(tmp_path / "proj")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    write_config(repo, {"mergeStrategy": "rebase"})
    wt_name = "wt-t1"
    wtp = os.path.join(repo, ".claude", "worktrees", wt_name)
    git(repo, "worktree", "add", "-q", "-b", "runway-task/t1", wtp, "main")
    rundir = os.path.join(runway.RUNS, "chunk-1")
    os.makedirs(rundir)
    with open(os.path.join(rundir, "t1.meta"), "w") as f:
        json.dump({"repo": repo, "wt": wt_name, "base": "main"}, f)
    iw, ibranch = runway.integration(repo, "main")
    before = git(iw, "rev-parse", "HEAD")

    result = runway.apply_task("chunk-1", "t1", iw, ibranch, "main", False, lambda m: None)

    assert result["outcome"] == "error"
    assert '"mergeStrategy" must be "squash" or "merge", not "rebase"' in result["msg"]
    assert git(iw, "rev-parse", "HEAD") == before
    assert os.path.isdir(wtp)  # the agent's work is kept


def test_checks_are_a_known_key_and_must_be_named_commands(site):
    write_config(site, {"checks": [{"name": "unit", "cmd": "pytest -q"}]})
    assert runway.project_config(site)["checks"] == [{"name": "unit", "cmd": "pytest -q"}]
    write_config(site, {"checks": "pytest -q"})
    with pytest.raises(runway.ConfigError, match='"checks" must be a list'):
        runway.project_config(site)
    write_config(site, {"checks": [{"name": "empty", "cmd": "  "}]})
    with pytest.raises(runway.ConfigError, match='"checks" must be a list'):
        runway.project_config(site)
