"""Appetite and pre-mortem (PI 7 Sprint 2), end to end through the harness per CLAUDE.md's "Verifying the page end
to end": appetite is set on the card in Decide mode and persists, exploreIdea's prompt and stubbed answer carry
appetiteFit per shape and a three-reason premortem, the drawer's shapes flip to "How this fails", and an idea
explored before this change (shapes, no premortem) offers "Run a pre-mortem" alone."""
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


def drive(steps, timeout=90):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps), capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return lines


# Captures the built exploreIdea prompt (so the test can assert it carries the appetite) and answers with shapes
# that include appetiteFit plus a three-reason premortem, most likely first.
STUB_EXPLORE = (
    "(window.__lastPrompt = null, S.sample = { json: async (prompt) => { window.__lastPrompt = prompt; return {"
    "summary: 'A scrappy test of the idea.',"
    "shapes: ["
    "{ name: 'Smallest test', summary: 'Try it cheaply.', estTokens: 50000, humanHours: 1, daysToSignal: 3, impact: 3, confidence: 3, moves: 'signal', risk: 'too small to learn much', firstQuestion: 'does anyone care', appetiteFit: 'Fits well inside a quick test.' },"
    "{ name: 'Solid v1', summary: 'Build it for real.', estTokens: 800000, humanHours: 4, daysToSignal: 14, impact: 4, confidence: 3, moves: 'revenue', risk: 'scope creep', firstQuestion: 'will it convert', appetiteFit: 'Needs a bigger appetite than a quick test.' },"
    "{ name: 'Ambitious', summary: 'Go all in.', estTokens: 2000000, humanHours: 10, daysToSignal: 30, impact: 5, confidence: 2, moves: 'revenue', risk: 'overbuilt', firstQuestion: 'is it worth it', appetiteFit: 'Needs a bigger appetite than a quick test.' }"
    "],"
    "recommend: 0, recommendWhy: 'Cheapest real signal.', killCase: 'Might not matter at all.',"
    "premortem: ['Nobody used it and the signal never came.', 'It shipped but took three times the appetite.', 'It worked but moved a number nobody tracks.']"
    "} } }, render(), 1)"
)


def test_appetite_set_on_card_persists_and_reaches_the_explore_prompt(harness):
    steps = [
        {"goto": harness}, {"waitForText": "Harness project", "timeout": 15000},
        {"eval": "(S.ideaProject='hp1', S.view='ideas', S.ideaMode='decide', render(), 1)"},
        {"waitForText": "Harness idea A", "timeout": 10000},
        {"eval": "S.ideas.find(x=>x.id==='hi1').appetite ?? null"},  # mark 4: unset to start
        {"eval": "document.querySelector('[data-idea-appetite=hi1]').value = 'test'; document.querySelector('[data-idea-appetite=hi1]').dispatchEvent(new Event('change', {bubbles:true})); 1"},
        {"sleep": 200},
        {"eval": "S.ideas.find(x=>x.id==='hi1').appetite"},  # mark 7: set from the card
        {"eval": "(S.overlay = null, S.overlay = { kind:'idea', id:'hi1' }, render(), 1)"},
        {"waitFor": "#i-app", "timeout": 5000},
        {"eval": "document.querySelector('#i-app').value"},  # mark 10: the drawer shows the same appetite
        {"eval": STUB_EXPLORE},
        {"click": "[data-act=explore-idea]"},
        {"waitForText": "Fits well inside a quick test.", "timeout": 10000},
        {"eval": "window.__lastPrompt.includes('Quick test') && window.__lastPrompt.includes('agent tokens')"},  # mark 14: appetite reached the prompt
        {"eval": "S.ideas.find(x=>x.id==='hi1').shapes.map(s => s.appetiteFit)"},  # mark 15
        {"eval": "S.ideas.find(x=>x.id==='hi1').premortem"},  # mark 16
    ]
    lines = drive(steps)
    assert lines[4]["value"] is None, "the idea starts with no appetite"
    assert lines[7]["value"] == "test", "the card's appetite select must patch the idea"
    assert lines[10]["value"] == "test", "the drawer must show the same appetite set from the card"
    assert lines[14]["value"] is True, "the built prompt must carry the chosen appetite and its calibrated range"
    fits = lines[15]["value"]
    assert fits[0] == "Fits well inside a quick test."
    assert fits[1] == "Needs a bigger appetite than a quick test."
    premortem = lines[16]["value"]
    assert len(premortem) == 3
    assert premortem[0] == "Nobody used it and the signal never came."


def test_drawer_flips_shapes_to_how_this_fails_with_screenshots(harness, tmp_path):
    front_png = str(tmp_path / "front.png")
    back_png = str(tmp_path / "back.png")
    steps = [
        {"goto": harness}, {"waitForText": "Harness project", "timeout": 15000},
        {"eval": "(S.view='ideas', S.overlay = { kind:'idea', id:'hi1' }, render(), 1)"},
        {"waitFor": "#i-title", "timeout": 5000},
        {"eval": STUB_EXPLORE},
        {"click": "[data-act=explore-idea]"},
        {"waitForText": "Fits well inside a quick test.", "timeout": 10000},
        {"eval": "!!document.querySelector('[data-act=flip-idea]')"},  # mark 7: a flip control exists once premortem landed
        {"eval": "!!document.querySelector('.shape')"},  # mark 8: front shows shapes
        {"screenshot": front_png},
        {"click": "[data-act=flip-idea]"},
        {"waitForText": "How this fails", "timeout": 5000},
        {"eval": "document.querySelector('.premortem').children.length"},  # mark 12: three reasons on the back
        {"eval": "!!document.querySelector('.shape')"},  # mark 13: shapes are gone from the back
        {"screenshot": back_png},
        {"key": "Tab"},  # the flip control is a real button: reachable and focusable like any other
    ]
    lines = drive(steps)
    assert lines[7]["value"] is True
    assert lines[8]["value"] is True
    assert lines[12]["value"] == 3
    assert lines[13]["value"] is False
    assert os.path.exists(front_png) and os.path.getsize(front_png) > 0
    assert os.path.exists(back_png) and os.path.getsize(back_png) > 0


def test_old_idea_with_shapes_but_no_premortem_offers_run_a_premortem_alone(harness):
    steps = [
        {"goto": harness}, {"waitForText": "Harness project", "timeout": 15000},
        # simulate an idea explored before this change: shapes and killCase, no appetite, no premortem field at all
        {"eval": ("put('ideas', 'hi3', { title:'Old explored idea', kind:'build', status:'explored', context:'', source:'manual', "
                  "projectId:'hp1', createdAt: Date.now(), summary:'An old idea.', recommend:0, chosen:null, recommendWhy:'', "
                  "killCase:'Might be a distraction.', shapes:[{ name:'Only shape', summary:'The one option.', estTokens:100000, "
                  "humanHours:1, daysToSignal:5, impact:3, confidence:3, moves:'signal', risk:'small sample', firstQuestion:'does it move anything' }] })"
                  ".then(() => 1)")},
        {"sleep": 200},
        {"eval": "(S.overlay = { kind:'idea', id:'hi3' }, render(), 1)"},
        {"waitForText": "Run a pre-mortem", "timeout": 5000},
        {"eval": "!!document.querySelector('[data-act=flip-idea]')"},  # mark 6: no flip control without a premortem yet
        {"eval": "document.querySelector('.note.bad')?.textContent || ''"},  # mark 7: the case against still shows on the front
        {"eval": "(S.sample = { json: async () => ({ premortem: ['It never got traction.', 'It cost more than planned.', 'The risk nobody checked sank it.'] }) }, render(), 1)"},
        {"click": "[data-act=run-premortem]"},
        {"waitForText": "How this fails", "timeout": 10000},
        {"eval": "S.ideas.find(x=>x.id==='hi3').premortem"},  # mark 11
        {"eval": "!!document.querySelector('[data-act=flip-idea]')"},  # mark 12: the flip control appears now that a premortem exists
    ]
    lines = drive(steps)
    assert lines[6]["value"] is False, "an idea with shapes but no premortem must offer 'Run a pre-mortem' alone, no flip"
    assert "Might be a distraction." in lines[7]["value"], "the existing case against must still show while there's no premortem"
    premortem = lines[11]["value"]
    assert len(premortem) == 3 and premortem[0] == "It never got traction."
    assert lines[12]["value"] is True
