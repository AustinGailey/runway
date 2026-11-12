"""Idea map end to end (PI 7 Sprint 1): dragging/Move to... attaches an idea to an opportunity and the attachment
survives a reload, and Riff with Claude's ghost cards are kept or dismissed one by one, all through the harness
(harness/serve.py + drive.mjs) per CLAUDE.md's "Verifying the page end to end"."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get("RUNWAY_CHROME") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

pytestmark = pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome")


@pytest.fixture
def harness(tmp_path):
    if not os.path.exists(os.path.join(ROOT, "dist", "runway.html")):
        subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], check=True, capture_output=True)
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "harness", "serve.py"), "--port", "0", "--sandbox", str(tmp_path), "--tick", "600"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = p.stdout.readline()
    assert line.startswith("HARNESS "), line + p.stderr.read()
    url = line.split()[1]
    yield url
    p.terminate()
    p.wait(10)


def drive(steps, timeout=90):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps), capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return lines


WAIT_OPP = ("new Promise(r => { const t0 = Date.now(); (function w(){ if (S.opportunities.some(o=>o.id==='mop1') "
            "|| Date.now()-t0>10000) return r(1); setTimeout(w,150); })(); })")


def test_move_to_menu_attaches_idea_to_opportunity_and_it_persists_across_reload(harness):
    steps = [
        {"goto": harness}, {"waitForText": "Harness project", "timeout": 15000},
        {"eval": "put('opportunities', 'mop1', { title: 'Agents stall on folders', note: '', projectId: 'hp1', order: 1, createdAt: Date.now() }).then(() => 1)"},
        {"eval": WAIT_OPP},
        {"eval": "(S.ideaProject='hp1', S.view='ideas', render(), 1)"},
        {"waitForText": "Harness idea A", "timeout": 10000},
        {"eval": "S.ideas.find(x=>x.id==='hi1').opportunityId ?? null"}, # mark 6: before
        {"click": "[data-act=menu][data-m='moveto:hi1']"},
        {"waitFor": "[data-act=move-idea][data-opp=mop1]", "timeout": 5000},
        {"click": "[data-act=move-idea][data-opp=mop1]"},
        {"sleep": 300},
        {"eval": "S.ideas.find(x=>x.id==='hi1').opportunityId"}, # mark 11: right after
        {"goto": harness}, {"waitForText": "Harness project", "timeout": 15000},
        {"eval": "(S.ideaProject='hp1', S.view='ideas', render(), 1)"},
        {"eval": ("new Promise(r => { const t0 = Date.now(); (function w(){ const i = S.ideas.find(x=>x.id==='hi1'); "
                   "if ((i && i.opportunityId) || Date.now()-t0>10000) return r(i && i.opportunityId); setTimeout(w,150); })(); })")}, # mark 15: after reload
    ]
    lines = drive(steps)
    assert lines[6]["value"] is None, "the idea starts loose (no opportunity yet)"
    assert lines[11]["value"] == "mop1", "Move to... must attach the idea to the chosen opportunity"
    assert lines[15]["value"] == "mop1", "the attachment must survive a reload (it's read back from the database, not local state)"


STUB_SAMPLE = ("(S.sample = { json: async () => ({ variations: ["
               "'A cheaper, scrappier version', 'Aimed at a different customer', 'Done as a decision, not a build', "
               "'The riskiest slice first', 'A slower but much bigger version', 'Outsourced instead of agent-built', "
               "'Bundled with an existing offer', 'Killed in favor of something adjacent'] }) }, render(), 1)")


def test_riff_with_claude_shows_ghost_cards_keep_creates_dismiss_removes(harness):
    steps = [
        {"goto": harness}, {"waitForText": "Harness project", "timeout": 15000},
        {"eval": "(S.ideaProject='hp1', S.view='ideas', S.ideaMode='spark', render(), 1)"},
        {"waitForText": "Harness idea A", "timeout": 10000},
        {"eval": STUB_SAMPLE},
        {"eval": "S.ideas.length"}, # mark 5: idea count before Keep
        {"click": "[data-act=riff-idea][data-id=hi1]"},
        {"waitFor": "[data-act=ghost-keep][data-id=hi1]", "timeout": 5000},
        {"eval": "(S.ghosts['hi1'] || []).length"}, # mark 8: ghost count, up to 8
        {"click": "[data-act=ghost-keep][data-id=hi1]"},
        {"sleep": 200},
        {"eval": "S.ideas.length"}, # mark 11: idea count after Keep (one real idea added)
        {"eval": "(S.ghosts['hi1'] || []).length"}, # mark 12: one fewer ghost after Keep
        {"eval": "document.querySelectorAll('[data-act=ghost-dismiss][data-id=hi1]').length"}, # mark 13: dismiss buttons left
        # one at a time, re-querying the live DOM each time (each click re-renders and replaces the node list)
        {"eval": ("(() => { let n = 0, b; while ((b = document.querySelector('[data-act=ghost-dismiss][data-id=hi1]')) && n < 20){ b.click(); n++; } return n; })()")},
        {"sleep": 200},
        {"eval": "(S.ghosts['hi1'] || []).length"}, # mark 16: all dismissed
    ]
    lines = drive(steps)
    before = lines[5]["value"]
    ghostCount = lines[8]["value"]
    assert 1 <= ghostCount <= 8, "Riff with Claude must show up to 8 ghost cards"
    assert lines[11]["value"] == before + 1, "Keep must create exactly one real idea"
    assert lines[12]["value"] == ghostCount - 1, "Keep must remove only the kept ghost"
    assert lines[13]["value"] == ghostCount - 1
    assert lines[16]["value"] == 0, "Dismiss must remove ghosts without creating ideas"
    assert lines[11]["value"] == before + 1, "dismissed ghosts must never have been written as ideas"
