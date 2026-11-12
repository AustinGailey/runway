"""Brief lint end to end: the preflight gate (off by default, opt-in via settings.briefCheck), and the Rewrite with
Claude / Dismiss flow in the task drawer's brief tab, all through the harness (harness/serve.py + drive.mjs) per
CLAUDE.md's "Verifying the page end to end"."""
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


class Steps:
    """Builds a steps list and remembers the index of each step as it's appended, so assertions below don't have
    to count by hand."""
    def __init__(self):
        self.steps = []
        self.marks = {}

    def add(self, step, mark=None):
        self.steps.append(step)
        if mark:
            self.marks[mark] = len(self.steps) - 1
        return self

    def goto_and_wait(self, url):
        return self.add({"goto": url}).add({"waitForText": "Harness project", "timeout": 15000})

    def subscribe(self):
        return self.add({"eval": "(S.projectId='hp1', subscribeTasks('hp1'), 1)"}).add({"sleep": 500})

    def give_bad_brief(self):
        self.subscribe()
        return self.add({"eval": "patch('tasks','ht2',{acceptance:%s}).then(()=>1)" % json.dumps(BAD_ACCEPTANCE)}).add({"sleep": 300})

    def at(self, lines, mark):
        return lines[self.marks[mark]]["value"]


BAD_ACCEPTANCE = "- The UI looks clean and professional"
WAIT_RUNNING = "new Promise(r=>{(function w(){const t=S.tasks.find(x=>x.id==='ht2'); if(t.status==='running'||Date.now()-window.__t0>10000) return r(t.status); setTimeout(w,200)})()})"


def test_brief_check_off_by_default_launches_exactly_as_before(harness):
    """With settings.briefCheck off (the default), a task with an untestable criterion launches with no preflight
    modal at all, same as before this task existed."""
    s = Steps()
    s.goto_and_wait(harness)
    s.give_bad_brief()
    s.add({"eval": "S.settings.briefCheck"}, "briefCheck")
    s.add({"eval": "(window.__t0=Date.now(), S.sel=new Set(['ht2']), launch(), 1)"})
    s.add({"eval": WAIT_RUNNING}, "status")
    s.add({"eval": "!!S.overlay"}, "overlay")
    lines = drive(s.steps)
    assert s.at(lines, "briefCheck") is False, "briefCheck must default off"
    assert s.at(lines, "status") == "running", "launch must proceed with no preflight stop"
    assert s.at(lines, "overlay") is False, "no modal should have appeared"


def test_brief_check_on_shows_the_issue_in_preflight_and_launch_anyway_still_launches(harness):
    wait_preflight = ("(window.__t0=Date.now(), new Promise(r=>{(function w(){ if(S.overlay?.kind==='preflight'||Date.now()-window.__t0>10000)"
                       " return r(S.overlay&&S.overlay.kind); setTimeout(w,200)})()}))")
    s = Steps()
    s.goto_and_wait(harness)
    s.give_bad_brief()
    s.add({"eval": "saveSettings({briefCheck:true})"})
    s.add({"eval": "(window.__t0=Date.now(), S.sel=new Set(['ht2']), launch(), 1)"})
    s.add({"eval": wait_preflight}, "modalKind")
    s.add({"eval": "S.overlay.probs.map(p=>p.kind)"}, "probKinds")
    s.add({"eval": "S.overlay.probs.find(p=>p.kind==='brief').detail"}, "briefDetail")
    s.add({"eval": "(window.__t0=Date.now(), document.querySelector('[data-act=pf-anyway]').click(), 1)"})
    s.add({"eval": WAIT_RUNNING}, "status")
    lines = drive(s.steps)
    assert s.at(lines, "modalKind") == "preflight", "a brief issue must stop the launch when briefCheck is on"
    assert "brief" in s.at(lines, "probKinds")
    assert "Harness task B" in s.at(lines, "briefDetail") and "issue" in s.at(lines, "briefDetail")
    assert s.at(lines, "status") == "running", "Launch anyway must still launch"


def test_rewrite_with_claude_replaces_only_acceptance(harness):
    stub_sample = "(S.sample={json: async () => ({acceptance: '- src/app.js has the new widget\\n- `npm test` passes'})}, 1)"
    s = Steps()
    s.goto_and_wait(harness)
    s.give_bad_brief()
    s.add({"eval": stub_sample})
    s.add({"eval": "(S.overlay={kind:'task', id:'ht2', tab:'brief'}, render(), 1)"})
    s.add({"waitFor": "[data-act=brief-rewrite]", "timeout": 5000})
    s.add({"eval": "document.querySelector('[data-act=brief-rewrite]').click()"})
    s.add({"waitFor": "[data-act=brief-rewrite-apply]", "timeout": 5000})
    s.add({"eval": "document.querySelector('.drawer pre').textContent"}, "before")
    s.add({"eval": "document.querySelector('[data-act=brief-rewrite-apply]').click()"})
    s.add({"sleep": 300})
    s.add({"eval": "[task('ht2').acceptance, task('ht2').detail]"}, "afterFields")
    s.add({"eval": "briefLint(task('ht2'), proj('hp1')).length"}, "issuesLeft")
    lines = drive(s.steps)
    assert BAD_ACCEPTANCE in s.at(lines, "before"), "the before pane must show the original acceptance"
    acceptance, detail = s.at(lines, "afterFields")
    assert acceptance == "- src/app.js has the new widget\n- `npm test` passes"
    assert detail == "Add another line.", "Apply must touch only the acceptance field, not the brief"
    assert s.at(lines, "issuesLeft") == 0, "the rewritten acceptance should clear the lint issues"


def test_dismiss_keeps_the_issue_from_returning(harness):
    s = Steps()
    s.goto_and_wait(harness)
    s.give_bad_brief()
    s.add({"eval": "briefLint(task('ht2'), proj('hp1')).map(x=>x.kind)"}, "before")
    s.add({"eval": "(S.overlay={kind:'task', id:'ht2', tab:'brief'}, render(), 1)"})
    s.add({"waitFor": "[data-act=brief-dismiss]", "timeout": 5000})
    s.add({"eval": "document.querySelector('[data-act=brief-dismiss]').click()"})
    s.add({"sleep": 300})
    s.add({"eval": "task('ht2').briefLintDismissed"}, "dismissed")
    s.add({"eval": "briefLint(task('ht2'), proj('hp1')).length"}, "rightAway")
    s.goto_and_wait(harness)
    s.subscribe()
    s.add({"eval": "briefLint(task('ht2'), proj('hp1')).length"}, "afterReload")
    lines = drive(s.steps)
    assert s.at(lines, "before") == ["untestable"]
    assert len(s.at(lines, "dismissed")) == 1, "the dismissal must be stored on the task"
    assert s.at(lines, "rightAway") == 0, "the dismissed issue must not show right away"
    assert s.at(lines, "afterReload") == 0, "and must not come back after a reload"
