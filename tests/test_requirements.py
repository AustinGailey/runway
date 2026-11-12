"""Removing a launch check from a project: the page's own withoutRequirement, run under node against stored lists."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is needed to run the page's JavaScript")


def page_source():
    """The checkKey and withoutRequirement definitions, exactly as the page has them."""
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    start = src.index("const checkKey = ")
    end = src.index("\nasync function chatApply(a){")
    return src[start:end]


def run_js(body):
    script = page_source() + "\n" + body
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_remove_then_reread_leaves_no_check():
    # Stored as the add path writes it (untrimmed), then removed as the model names it.
    stored = [{"check": "npm run build ", "fix": ""}, {"check": "docker info", "fix": "open -a Docker"}]
    # the page writes the list back as the removal's result; a fresh read of it must not bring the check back
    body = """
    const written = withoutRequirement(%s, "npm run build");
    const reread = JSON.parse(JSON.stringify(written));
    const again = (() => { try { return withoutRequirement(reread, "npm run build"); } catch (e) { return "no match"; } })();
    console.log(JSON.stringify({ reread, again }));
    """ % json.dumps(stored)
    result = run_js(body)
    assert result["reread"] == [{"check": "docker info", "fix": "open -a Docker"}]
    assert result["again"] == "no match"


def test_removing_the_last_check_leaves_an_empty_list():
    body = """
    console.log(JSON.stringify(withoutRequirement([{ check: "npm run build", fix: "" }], "npm run build")));
    """
    assert run_js(body) == []


def test_removing_a_check_that_is_not_there_is_an_error():
    body = """
    try { withoutRequirement([{ check: "docker info", fix: "" }], "npm run build"); console.log(JSON.stringify("applied")); }
    catch (e) { console.log(JSON.stringify("error: " + e.message)); }
    """
    assert run_js(body).startswith("error: No launch check matches")


def test_add_then_remove_round_trip_keeps_other_checks():
    body = """
    let reqs = [];
    reqs = [...reqs, { check: "npm run build", fix: "" }];
    reqs = [...reqs, { check: "docker info", fix: "open -a Docker" }];
    reqs = withoutRequirement(reqs, " npm  run build");
    console.log(JSON.stringify(reqs));
    """
    assert run_js(body) == [{"check": "docker info", "fix": "open -a Docker"}]
