"""The environment keys in .runway.json: teardown, env and ports (setup is in test_setup.py). They are validated here;
the helper does not act on them yet. Throwaway repos under tmp_path only."""
import json, os

import pytest

import runway


@pytest.fixture
def site(tmp_path):
    repo = tmp_path / "painter-site"
    repo.mkdir()
    (repo / "index.html").write_text("<h1>Painter</h1>\n")
    return str(repo)


def write_config(repo, obj):
    with open(os.path.join(repo, runway.CONFIG_FILE), "w") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


# ---------- a file without the keys parses exactly as before ----------
def test_a_config_without_environment_keys_is_unchanged(site):
    write_config(site, {"type": "site", "test": ["./test.sh"], "checks": [{"name": "t", "cmd": "./test.sh"}]})
    assert runway.project_config(site) == {"type": "site", "test": ["./test.sh"], "build": None, "baseBranch": None,
                                           "mergeStrategy": "squash", "checks": [{"name": "t", "cmd": "./test.sh"}]}


def test_no_environment_keys_are_added_by_default(site):
    cfg = runway.project_config(site)
    assert not {"setup", "teardown", "env", "ports"} & set(cfg)


# ---------- valid values are read ----------
def test_all_four_keys_are_read(site):
    write_config(site, {"setup": ["npm ci", "npx playwright install"], "teardown": "rm -rf .cache",
                        "env": ["DATABASE_URL", "API_KEY"], "ports": 2})
    cfg = runway.project_config(site)
    assert cfg["setup"] == ["npm ci", "npx playwright install"]
    assert cfg["teardown"] == "rm -rf .cache"
    assert cfg["env"] == ["DATABASE_URL", "API_KEY"]
    assert cfg["ports"] == 2


def test_ports_bounds_are_inclusive(site):
    assert runway._check("project settings", {"ports": 0}) == {"ports": 0}
    assert runway._check("project settings", {"ports": 20}) == {"ports": 20}


def test_an_empty_env_list_is_fine(site):
    assert runway._check("project settings", {"env": []}) == {"env": []}


def test_teardown_null_and_list_are_accepted(site):
    assert runway._check("project settings", {"teardown": None}) == {"teardown": None}
    assert runway._check("project settings", {"teardown": [" make clean ", "rm -f x"]}) == {"teardown": ["make clean", "rm -f x"]}


# ---------- invalid values name the key and the fix ----------
@pytest.mark.parametrize("text, message", [
    ('{"teardown": ""}', '"teardown" must be one command or a list of commands, or null for no teardown step'),
    ('{"teardown": []}', '"teardown" must be one command or a list of commands'),
    ('{"teardown": ["make clean", 3]}', '"teardown" must be one command or a list of commands'),
    ('{"env": "DATABASE_URL"}', '"env" must be a list of variable names such as ["DATABASE_URL"], not "DATABASE_URL"'),
    ('{"env": ["DATABASE_URL=postgres://x"]}', "Names only: put the values in ~/.ai-planner/env/<repo>.env."),
    ('{"env": ["1BAD"]}', '"env" must be a list of variable names'),
    ('{"env": ["A-B"]}', '"env" must be a list of variable names'),
    ('{"env": [""]}', '"env" must be a list of variable names'),
    ('{"env": ["API_KEY", "API_KEY"]}', '"env" lists a name more than once: ["API_KEY", "API_KEY"]'),
    ('{"ports": 21}', '"ports" must be a whole number from 0 to 20, not 21'),
    ('{"ports": -1}', '"ports" must be a whole number from 0 to 20, not -1'),
    ('{"ports": 2.5}', '"ports" must be a whole number from 0 to 20, not 2.5'),
    ('{"ports": "3"}', '"ports" must be a whole number from 0 to 20, not "3"'),
    ('{"ports": true}', '"ports" must be a whole number from 0 to 20, not true'),
])
def test_bad_environment_values_raise_a_readable_error(site, text, message):
    write_config(site, text)
    with pytest.raises(runway.ConfigError) as e:
        runway.project_config(site)
    assert message in str(e.value)
    assert runway.CONFIG_FILE in str(e.value)


def test_a_typo_of_a_new_key_names_the_closest_one(site):
    write_config(site, {"port": 2})
    with pytest.raises(runway.ConfigError, match='did you mean "ports"'):
        runway.project_config(site)
