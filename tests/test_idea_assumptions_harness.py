"""Assumption map (PI 7 Sprint 2), end to end through the harness: exploring returns 3-5 assumptions shown on the
importance-by-evidence grid, a chip moves by keyboard, "Test this" creates a linked test task, and once that task is
done, marking the assumption broke moves its evidence and flags the idea."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get("RUNWAY_CHROME") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

pytestmark = [pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome"),
              pytest.mark.usefixtures("chrome_lock")]  # one Chrome-driven test at a time on this Mac (conftest)


@pytest.fixture
def harness(tmp_path):
    # always build: the harness serves dist/runway.html, and a page left over from an older build tests old code
    subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], check=True, capture_output=True)
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "harness", "serve.py"), "--port", "0", "--sandbox", str(tmp_path), "--tick", "600"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = p.stdout.readline()
    assert line.startswith("HARNESS "), line + p.stderr.read()
    url = line.split()[1]
    yield url
    p.terminate()
    p.wait(10)



STUB = ("(S.sample = { json: async () => ({ summary: 'Framing.', shapes: [{ name: 'Smallest test', summary: 'Try it.', estTokens: 50000,"
        " humanHours: 1, daysToSignal: 3, impact: 3, confidence: 3, moves: 'x', risk: 'y', firstQuestion: 'z' }], recommend: 0,"
        " recommendWhy: 'r', killCase: 'k', premortem: ['a', 'b', 'c'], assumptions: ["
        " { statement: 'Painters will pay for this', type: 'viable', importance: 5, evidence: 0, test: 'Ask three painters' },"
        " { statement: 'We can build it in a week', type: 'feasible', importance: 3, evidence: 2, test: 'Spike it' },"
        " { statement: 'Owners want fewer calls', type: 'desirable', importance: 4, evidence: 1, test: 'Survey' } ] }) }, 1)")


def run(url, steps):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps([{"goto": url}] + steps),
                       capture_output=True, text=True, timeout=180)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return lines


def test_explore_maps_assumptions_moves_one_by_keyboard_tests_it_and_a_broken_one_flags_the_idea(harness):
    idea = "put('ideas', 'hi1', { title: 'Missed-call text back', kind: 'build', status: 'new', context: '', source: 'manual', projectId: 'hp1', createdAt: Date.now() })"
    a = "S.ideas.find(x=>x.id==='hi1').assumptions"
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},
        {"eval": f"{idea}.then(()=>1)"},
        {"eval": STUB},
        {"eval": "(S.view='ideas', S.ideaProject='hp1', S.overlay = { kind: 'idea', id: 'hi1' }, S._ovSig = null, render(), 1)"},
        {"waitFor": "[data-act=explore-idea]:not([disabled])", "timeout": 10000},
        {"click": "[data-act=explore-idea]"},
        {"waitFor": ".aschip", "timeout": 15000},
        {"eval": "document.querySelectorAll('.aschip').length"},                                    # step 8: three chips on the grid
        {"eval": f"{a}.map(x=>[x.statement,x.importance,x.evidence])"},                            # step 9
        {"eval": f"(document.querySelector('.aschip[data-id=\"'+{a}[1].id+'\"]').click(), 1)"},  # select the second one
        {"key": "ArrowUp"}, {"key": "ArrowRight"},
        {"eval": f"new Promise(r=>setTimeout(()=>r([{a}[1].importance,{a}[1].evidence]),600))"},  # step 13: moved by keyboard
        {"eval": f"(document.querySelector('[data-act=as-test][data-id=\"'+{a}[0].id+'\"]').click(), 1)"},
        {"eval": f"new Promise(r=>{{const t0=Date.now();(function w(){{const id={a}[0].taskId; if(id||Date.now()-t0>10000) return r(id?(({{title,kind,projectId}})=>({{title,kind,projectId}}))(S.tasks.find(t=>t.id===id)||{{}}):null); setTimeout(w,200)}})()}})"},  # step 15
        {"eval": f"patch('tasks', {a}[0].taskId, {{ status: 'done' }}).then(()=>{{S._ovSig=null; render(); return 1}})"},
        {"waitFor": "[data-act=as-resolve][data-v=broke]", "timeout": 10000},
        {"eval": f"(document.querySelector('[data-act=as-resolve][data-v=broke][data-id=\"'+{a}[0].id+'\"]').click(), 1)"},
        {"waitForText": "Flagged", "timeout": 10000},
        {"eval": f"[{a}[0].resolution, {a}[0].evidence, ideaFlagged(S.ideas.find(x=>x.id==='hi1'))]"},  # step 20
    ]
    v = [l.get("value") for l in run(harness, steps)]
    assert v[8] == 3
    assert v[9][0] == ["Painters will pay for this", 5, 0]
    assert v[13] == [4, 3]  # importance 3 -> 4 (up), evidence 2 -> 3 (right)
    assert v[15]["kind"] == "test" and v[15]["projectId"] == "hp1" and v[15]["title"].startswith("Test: Painters will pay")
    assert v[20] == ["broke", 3, True]


def test_ideas_explored_before_assumptions_show_no_grid_and_still_work(harness):
    old = ("put('ideas', 'hi2', { title: 'Old idea', kind: 'build', status: 'explored', context: '', source: 'manual', projectId: 'hp1',"
           " createdAt: Date.now(), summary: 's', recommend: 0, chosen: null, recommendWhy: '', killCase: '', shapes: [{ name: 'Only', summary: 'x',"
           " estTokens: 1000, humanHours: 1, daysToSignal: 1, impact: 3, confidence: 3, moves: '', risk: '', firstQuestion: '' }] })")
    v = [l.get("value") for l in run(harness, [
        {"waitForText": "Harness project", "timeout": 15000},
        {"eval": f"{old}.then(()=>1)"},
        {"eval": "(S.view='ideas', S.ideaProject='hp1', S.overlay = { kind: 'idea', id: 'hi2' }, S._ovSig = null, render(), 1)"},
        {"waitForText": "Only", "timeout": 10000},
        {"eval": "[document.querySelectorAll('.aschip').length, ideaFlagged(S.ideas.find(x=>x.id==='hi2'))]"},
    ])]
    assert v[5] == [0, False]
