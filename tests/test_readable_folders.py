"""Readable-folder suggestions end to end: the project form's 'Agents were blocked reading these' list and Run
health's 'Add N suggested folders', driven through the harness with seeded run rows carrying access blockers."""
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
    yield url, str(tmp_path)
    p.terminate()
    p.wait(10)


def drive(steps, shots_dir=None):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps),
                        capture_output=True, text=True, timeout=90)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return lines


def test_the_project_form_and_run_health_suggest_and_add_or_ignore_folders(harness, tmp_path):
    url, sandbox = harness
    folder_a = os.path.join(sandbox, "vault-notes")  # ignored through the project form
    folder_b = os.path.join(sandbox, "sibling-repo")  # added through Run health
    disp_a, disp_b = "~" + folder_a[len(sandbox):], "~" + folder_b[len(sandbox):]  # shown home-relative, as folderSuggestions returns it
    seed = (
        "Promise.all(["
        "put('estimates', 'ht1-c1', {taskId:'ht1', projectId:'hp1', category:'blocked:access', agentStatus:'blocked',"
        " blockers:[{type:'access', detail:'Cannot read the plan', path:%s}], readPaths:[], denials:1, deniedTools:[],"
        " checks:null, verifyPass:null, exitCode:null, startFallback:false, errLine:'', attempt:1, timing:null, base:null,"
        " dirty:null, costUsd:0.10, at:Date.now()}),"
        "put('estimates', 'ht2-c1', {taskId:'ht2', projectId:'hp1', category:'blocked:access', agentStatus:'blocked',"
        " blockers:[], readPaths:[%s], denials:1, deniedTools:[], checks:null, verifyPass:null, exitCode:null,"
        " startFallback:false, errLine:'', attempt:1, timing:null, base:null, dirty:null, costUsd:0.20, at:Date.now()})"
        "]).then(()=>1)"
    ) % (json.dumps(folder_a + "/plan.md"), json.dumps(folder_b + "/readme.md"))
    steps = [
        {"goto": url}, {"waitForText": "Harness project", "timeout": 15000},
        {"eval": seed}, {"sleep": 300},
        # the project form shows both suggestions, with their run and task counts
        {"eval": "(S.projectId='hp1', S.overlay={kind:'project', id:'hp1'}, render(), 1)"},
        {"waitFor": "[data-act=dirs-suggest-add]", "timeout": 5000},
        {"eval": "document.body.innerText.includes('Agents were blocked reading these')"},
        {"eval": "folderSuggestions('hp1').map(s => [s.folder, s.runs, s.tasks.length]).sort()"},
        {"eval": "(document.querySelector('[data-act=dirs-suggest-add]').scrollIntoView({block:'center'}), 1)"},
        {"screenshot": os.path.join(str(tmp_path), "form-suggestions.png")},
        # click, re-clicking on every poll tick in case the first click lands before a stray render; harmless, since
        # Ignore is idempotent
        {"eval": "new Promise(r=>{const t0=Date.now();(function w(){const b=document.querySelector(%s); if(b) b.click(); const n=document.querySelectorAll('[data-act=dirs-suggest-add]').length; if(n<2||Date.now()>t0+4000) return r(n); setTimeout(w,150)})()})" % json.dumps("[data-act=dirs-suggest-ignore][data-folder='%s']" % disp_a)},
        {"eval": "proj('hp1').readDirsIgnored"},
        {"eval": "(S.overlay=null, render(), 1)"},
        # Run health offers to add what is left (folder_b only, since folder_a was ignored)
        {"eval": "(S.view='estimates', render(), 1)"},
        {"waitForText": "Run health", "timeout": 5000},
        {"waitFor": "[data-act=health-add-dirs]", "timeout": 5000},
        {"eval": "document.querySelector('[data-act=health-add-dirs]').innerText"},
        {"screenshot": os.path.join(str(tmp_path), "run-health.png")},
        {"eval": "new Promise(r=>{const t0=Date.now();(function w(){const b=document.querySelector('[data-act=health-add-dirs]'); if(b) b.click(); const d=proj('hp1').readDirs||[]; if(d.length||Date.now()>t0+4000) return r(d); setTimeout(w,150)})()})"},
        {"eval": "document.querySelector('[data-act=health-add-dirs]')"},
    ]
    lines = drive(steps)
    assert lines[6]["value"] is True  # the suggestions block is shown
    assert lines[7]["value"] == sorted([[disp_a, 1, 1], [disp_b, 1, 1]])
    assert os.path.exists(os.path.join(str(tmp_path), "form-suggestions.png"))
    assert lines[10]["value"] == 1  # only folder_b's Add button is left after Ignore
    assert lines[11]["value"] == [disp_a]
    assert lines[16]["value"] == "Add 1 suggested folder"
    assert os.path.exists(os.path.join(str(tmp_path), "run-health.png"))
    assert lines[18]["value"] == [disp_b]
    assert lines[19]["value"] is None  # the button is gone once there is nothing left to add


def test_a_project_with_no_access_blockers_gets_no_suggestions_and_no_add_button(harness):
    url, _ = harness
    steps = [
        {"goto": url}, {"waitForText": "Harness project", "timeout": 15000},
        {"eval": "put('estimates', 'ht1-c1', {taskId:'ht1', projectId:'hp1', category:'ok', agentStatus:'done',"
                  " blockers:[], readPaths:[], denials:0, deniedTools:[], checks:null, verifyPass:null, exitCode:0,"
                  " startFallback:false, errLine:'', attempt:1, timing:null, base:null, dirty:null, costUsd:0.10, at:Date.now()})"},
        {"sleep": 300},
        {"eval": "folderSuggestions('hp1')"},
        {"eval": "proj('hp1').readDirs || null"},
        {"eval": "(S.view='estimates', render(), 1)"},
        {"waitForText": "Run health", "timeout": 5000},
        {"eval": "document.querySelector('[data-act=health-add-dirs]')"},
    ]
    lines = drive(steps)
    assert lines[4]["value"] == []
    assert lines[5]["value"] is None
    assert lines[8]["value"] is None
