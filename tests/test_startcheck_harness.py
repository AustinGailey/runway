"""The can-this-start check through the page: a missing foundation shows as a 'start' problem in the preflight modal,
Hold writes blockerKey on the task, and a hands-off launch holds the task instead of showing the modal. Harness
(harness/serve.py + drive.mjs) per CLAUDE.md; the sandbox's fake claude answers the check from HARNESS_STARTCHECK."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get("RUNWAY_CHROME") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

pytestmark = pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome")

MISSING = json.dumps({"ok": False, "missing": [
    {"type": "foundation", "detail": "src/exporter.py does not exist yet", "needs": "the exporter module"},
    {"type": "decision", "detail": "Which file format should the export use?", "needs": "csv or json"}]})


def serve(tmp_path, answer):
    if not os.path.exists(os.path.join(ROOT, "dist", "runway.html")):
        subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], check=True, capture_output=True)
    env = {**os.environ, "HARNESS_STARTCHECK": answer}
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "harness", "serve.py"), "--port", "0", "--sandbox", str(tmp_path), "--tick", "600"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    line = p.stdout.readline()
    assert line.startswith("HARNESS "), line + p.stderr.read()
    return p, line.split()[1]


@pytest.fixture
def harness(tmp_path):
    p, url = serve(tmp_path, MISSING)
    yield url
    p.terminate()
    p.wait(10)


def drive(steps, timeout=90):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps), capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return lines


class Steps:
    def __init__(self, url):
        self.steps, self.marks = [], {}
        self.add({"goto": url}).add({"waitForText": "Harness project", "timeout": 15000})
        self.add({"eval": "(S.projectId='hp1', subscribeTasks('hp1'), 1)"}).add({"sleep": 500})
        self.add({"eval": "new Promise(r=>{(function w(){ if(helperOn()||Date.now()-(window.__h=window.__h||Date.now())>20000) return r(helperOn()); setTimeout(w,200)})()})"}, "helper")

    def add(self, step, mark=None):
        self.steps.append(step)
        if mark:
            self.marks[mark] = len(self.steps) - 1
        return self

    def at(self, lines, mark):
        return lines[self.marks[mark]]["value"]


WAIT_MODAL = ("new Promise(r=>{const t0=Date.now(); (function w(){ if(S.overlay?.kind==='preflight'||Date.now()-t0>30000) return r(S.overlay&&S.overlay.kind); setTimeout(w,200)})()})")
LAUNCHED = "new Promise(r=>{const t0=Date.now(); (function w(){ const t=task('ht2'); if(t.status==='running'||Date.now()-t0>15000) return r([t.status, !!S.overlay, !!t.blockerKey]); setTimeout(w,200)})()})"
WAIT_SETTLED = "new Promise(r=>{const t0=Date.now(); (function w(){ if(!S.launching||Date.now()-t0>30000) return r(S.launching); setTimeout(w,200)})()})"


def test_start_problems_show_in_the_modal_and_hold_writes_blockerkey(harness):
    s = Steps(harness)
    s.add({"eval": "saveSettings({startCheck:true})"})
    s.add({"eval": "(S.sel=new Set(['ht2']), launch(), 1)"})
    s.add({"eval": WAIT_MODAL}, "modal")
    s.add({"eval": "S.overlay.probs.filter(p=>p.kind==='start').map(p=>p.item.type)"}, "types")
    s.add({"eval": "document.body.innerText.includes(\"Can't start yet\") && !!document.querySelector('[data-act=pf-hold]') && !!document.querySelector('[data-act=pf-answer]') && !!document.querySelector('[data-act=pf-anyway]')"}, "buttons")
    s.add({"eval": "document.querySelector('[data-act=pf-hold]').click()"})
    s.add({"sleep": 500})
    s.add({"eval": "[task('ht2').blockerKey, task('ht2').blockedAt > 0, task('ht2').blocker.type, task('ht2').status]"}, "held")
    s.add({"eval": "S.overlay && S.overlay.probs.filter(p=>p.taskId==='ht2').length"}, "left")
    lines = drive(s.steps)
    assert s.at(lines, "helper") is True
    assert s.at(lines, "modal") == "preflight"
    assert s.at(lines, "types") == ["foundation", "decision"]
    assert s.at(lines, "buttons") is True
    key, at, typ, status = s.at(lines, "held")
    assert key.startswith("start:foundation:") and at is True and typ == "foundation" and status == "todo"
    assert s.at(lines, "left") in (None, 0)


def test_answer_opens_the_existing_blocker_form(harness):
    s = Steps(harness)
    s.add({"eval": "saveSettings({startCheck:true})"})
    s.add({"eval": "(S.sel=new Set(['ht2']), launch(), 1)"})
    s.add({"eval": WAIT_MODAL}, "modal")
    s.add({"eval": "document.querySelector('[data-act=pf-answer]').click()"})
    s.add({"waitFor": "[data-act=bk-apply]", "timeout": 8000})
    s.add({"eval": "[S.overlay.kind, task('ht2').blockerKey.startsWith('start:decision:'), document.body.innerText.includes('Which file format')]"}, "form")
    lines = drive(s.steps)
    assert s.at(lines, "form") == ["task", True, True]


def test_hands_off_launch_holds_instead_of_showing_the_modal(harness):
    s = Steps(harness)
    s.add({"eval": "saveSettings({startCheck:true})"})
    s.add({"eval": "(S.sel=new Set(['ht2']), S.autoLaunch=true, launch(), S.autoLaunch=false, 1)"})
    s.add({"eval": WAIT_SETTLED}, "launching")
    s.add({"eval": "[!!S.overlay, !!task('ht2').blockerKey, task('ht2').status]"}, "after")
    lines = drive(s.steps)
    assert s.at(lines, "launching") is False
    assert s.at(lines, "after") == [False, True, "todo"], "nothing launched, no modal, the task carries its blocker"


def test_on_by_default_start_problems_show_with_no_setup(harness):
    s = Steps(harness)
    s.add({"eval": "S.settings.startCheck"}, "setting")
    s.add({"eval": "(S.sel=new Set(['ht2']), launch(), 1)"})
    s.add({"eval": WAIT_MODAL}, "modal")
    s.add({"eval": "S.overlay.probs.filter(p=>p.kind==='start').map(p=>p.item.type)"}, "types")
    lines = drive(s.steps)
    assert s.at(lines, "setting") is True
    assert s.at(lines, "modal") == "preflight"
    assert s.at(lines, "types") == ["foundation", "decision"]


def test_switched_off_no_start_problems_and_launch_proceeds(harness):
    s = Steps(harness)
    s.add({"eval": "saveSettings({startCheck:false})"})
    s.add({"sleep": 600})
    s.add({"eval": "S.settings.startCheck"}, "setting")
    s.add({"eval": "(S.sel=new Set(['ht2']), launch(), 1)"})
    s.add({"eval": LAUNCHED}, "after")
    lines = drive(s.steps)
    assert s.at(lines, "setting") is False
    assert s.at(lines, "after") == ["running", False, False]


def test_on_by_default_a_malformed_check_answer_counts_as_ok(tmp_path):
    p, url = serve(tmp_path, "not json at all")
    try:
        s = Steps(url)
        s.add({"eval": "S.settings.startCheck"}, "setting")
        s.add({"eval": "(S.sel=new Set(['ht2']), launch(), 1)"})
        s.add({"eval": LAUNCHED}, "after")
        lines = drive(s.steps)
        assert s.at(lines, "setting") is True
        assert s.at(lines, "after") == ["running", False, False]
    finally:
        p.terminate()
        p.wait(10)
