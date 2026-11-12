"""Idea studio data model, end to end through the harness (see CLAUDE.md "Verifying the page end to end"): existing
ideas still render once the page subscribes to opportunities and duels, and both collections are really read and
written through the running page, not just the pure functions in tests/test_idea_studio.py."""
import json
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.usefixtures("chrome_lock")  # one Chrome-driven test at a time on this Mac (conftest)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get("RUNWAY_CHROME") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.fixture(scope="module", autouse=True)
def built_page():
    subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], check=True, capture_output=True)


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    sandbox = tmp_path_factory.mktemp("sandbox")
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "harness", "serve.py"), "--port", "0", "--sandbox", str(sandbox), "--tick", "2"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = p.stdout.readline()
    assert line.startswith("HARNESS "), line + p.stderr.read()
    url = line.split()[1]
    yield url, str(sandbox)
    p.terminate()
    p.wait(10)


@pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome")
def test_opportunities_and_duels_are_read_and_written_and_ideas_render_unchanged(harness):
    url, _ = harness
    wait_opp = "new Promise(r => { const t0 = Date.now(); (function w(){ if (S.opportunities.some(o=>o.id==='hop1') || Date.now()-t0>10000) return r(S.opportunities.find(o=>o.id==='hop1')); setTimeout(w,150); })(); })"
    wait_duel = "new Promise(r => { const t0 = Date.now(); (function w(){ if (S.duels.length>=1 || Date.now()-t0>10000) return r(S.duels.length); setTimeout(w,150); })(); })"
    steps = [
        {"goto": url}, {"waitForText": "Harness project", "timeout": 15000},
        {"eval": "(S.projectId='hp1', S.ideaProject=null, S.view='ideas', render(), 1)"},
        {"waitForText": "Harness idea A", "timeout": 10000},
        # existing ideas render unchanged: still open (new), no rating shown yet, nothing thrown
        {"eval": "(() => { const i = S.ideas.find(x=>x.id==='hi1'); return { status: i.status, rating: ideaRating(i), open: ideaOpen(i) }; })()"},
        # opportunities: written by the page (the same generic put() every other collection uses), then read back from the subscription
        {"eval": "put('opportunities', 'hop1', { title: 'agents get blocked on folders', note: '', projectId: 'hp1', order: 1, createdAt: Date.now() }).then(() => 1)"},
        {"eval": wait_opp},
        {"eval": "opportunityOutcome(oppOf('hop1'))"},
        # duels: recordDuel writes a duel row and updates both ideas' ratings
        {"eval": "recordDuel('hi1', 'hi2', 'a', 'hp1').then(() => 1)"},
        {"eval": wait_duel},
        {"eval": "({ duel: S.duels[0], a: S.ideas.find(x=>x.id==='hi1'), b: S.ideas.find(x=>x.id==='hi2') })"},
    ]
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps), capture_output=True, text=True, timeout=90)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr

    idea_check = lines[4]["value"]
    assert idea_check == {"status": "new", "rating": 1000, "open": True}

    opp = lines[6]["value"]
    assert opp["title"] == "agents get blocked on folders" and opp["projectId"] == "hp1"

    outcome = lines[7]["value"]
    assert outcome == "Exercise launch, merge and land"  # the active PI's objective, read live, not copied

    result = lines[10]["value"]
    assert result["duel"]["a"] == "hi1" and result["duel"]["b"] == "hi2" and result["duel"]["winner"] == "a"
    assert result["a"]["rating"] > 1000 and result["a"]["duels"] == 1
    assert result["b"]["rating"] < 1000 and result["b"]["duels"] == 1
