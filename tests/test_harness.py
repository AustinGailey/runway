"""The harness (harness/serve.py + shim.js + drive.mjs): the built page against a sandboxed Desktop Commander, so agents
can verify page behaviour end to end without the claude.ai app, real agents or the real ~/.ai-planner."""
import json, os, shutil, subprocess, sys, urllib.request, urllib.error

import pytest

pytestmark = pytest.mark.usefixtures("chrome_lock")  # one Chrome-driven test at a time on this Mac (conftest)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get("RUNWAY_CHROME") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.fixture(scope="module", autouse=True)
def built_page():
    """Every test here serves dist/runway.html: build it from this checkout first, so a page left over from an older
    build never stands in for the code under test (checks ran the tests before the build)."""
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


def post(url, path, body):
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_is_served_with_the_shim(harness):
    url, _ = harness
    page = urllib.request.urlopen(url, timeout=10).read().decode()
    assert page.index("Runway harness shim") < page.index("const HOST")


def test_start_process_runs_in_the_sandbox_with_the_fake_claude(harness):
    url, sandbox = harness
    code, out = post(url, "/__mcp", {"tool": "start_process", "input": {"command": 'echo "$HOME"; echo "$RUNWAY_CLAUDE"; python3 "$HOME/.ai-planner/runway.py" version'}})
    home, claude, version = out["text"].split("\n")[:3]
    assert code == 200 and os.path.realpath(home) == os.path.realpath(sandbox)
    assert claude.startswith(sandbox) and claude.endswith("/bin/claude")
    assert version.strip().endswith(open(os.path.join(ROOT, "helper", "runway.py")).read().split("VERSION = ")[1].split("\n")[0])


def test_read_file_outside_the_sandbox_is_refused(harness):
    url, _ = harness
    code, out = post(url, "/__mcp", {"tool": "read_file", "input": {"path": os.path.expanduser("~/.ai-planner/status.json")}})
    assert code == 403 and "refused" in out["error"]


def test_only_the_two_desktop_commander_tools(harness):
    url, _ = harness
    code, _ = post(url, "/__mcp", {"tool": "write_file", "input": {"path": "/tmp/x", "content": "x"}})
    assert code == 400


def test_the_helper_uses_runway_claude_when_set(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), RUNWAY_CLAUDE="/opt/fake/claude")
    out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import runway; print(runway.CLAUDE)" % os.path.join(ROOT, "helper")],
                         env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert out == "/opt/fake/claude"
    env.pop("RUNWAY_CLAUDE")
    out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import runway; print(runway.CLAUDE)" % os.path.join(ROOT, "helper")],
                         env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert out == "claude"


@pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome")
def test_launch_merge_and_land_through_the_page(harness):
    url, sandbox = harness
    wait = lambda cond, ms: "new Promise(r=>{const t0=Date.now();(function w(){const t=S.tasks.find(x=>x.id==='ht1');if((%s)||Date.now()-t0>%d)return r(t&&{status:t.status,note:t.run&&t.run.mergeNote});setTimeout(w,400)})()})" % (cond, ms)
    steps = [{"goto": url}, {"waitForText": "Harness project", "timeout": 15000},
             {"eval": "(S.projectId='hp1', subscribeTasks('hp1'), 1)"}, {"sleep": 500},
             {"eval": "launchNow(S.tasks.filter(t=>t.id==='ht1')).then(()=>1)"},
             {"eval": wait("t&&t.status!=='running'", 40000)},
             {"eval": "mergeSelected(['ht1']).then(()=>1)"},
             {"eval": wait("t.run&&t.run.merged&&!t.run.merging", 40000)}]
    try:
        p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps), capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired as e:
        raise AssertionError("driver timed out: %s" % (e.stdout,))
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    assert lines[5]["value"]["status"] == "review"
    assert lines[7]["value"] == {"status": "done", "note": "Merged onto runway/main Landed in main."}
    assert os.path.exists(os.path.join(sandbox, "site", "harness-agent.txt"))


@pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome")
def test_the_card_updates_live_from_launch_to_review(tmp_path):
    """The card changes the moment Launch is pressed, shows the agent's step while it works (live.json, not the minute
    tick), and reaches Review within seconds of the agent finishing, all while staying on the board. The fake agent waits
    on a gate file, so the test never races a timer."""
    gate = tmp_path / "go"
    env = dict(os.environ, HARNESS_AGENT_GATE=str(gate))
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "harness", "serve.py"), "--port", "0", "--sandbox", str(tmp_path), "--tick", "600"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        url = p.stdout.readline().split()[1]
        # Every change to the card is recorded as it happens (a MutationObserver, not a timer): in the harness the
        # preflight answers in a few ms, so "Checking before launch" can be on screen for less than any sampling interval.
        record = ("(window.__log=[], S.sel=new Set(['ht1']), window.__t0=Date.now(),"
                  " window.__rec=()=>{const c=document.querySelector('article[data-id=ht1]'); const x=c?c.innerText.replace(/\\s+/g,' '):'none'; if(__log[__log.length-1]?.[1]!==x) __log.push([Date.now()-__t0,x])},"
                  " new MutationObserver(__rec).observe(document.body,{subtree:true,childList:true,characterData:true}), __rec(), launch(), 1)")
        # waits for a state, not a moment; on timeout the step fails naming what it waited for (and the card's history)
        until = lambda cond, ms: ("new Promise((r,j)=>{(function w(){if(%s) return r(Date.now()-__t0); if(Date.now()-__t0>%d) return j(new Error(%s+' | card so far: '+JSON.stringify(__log))); setTimeout(w,150)})()})"
                                  % (cond, ms, json.dumps("timed out after %d ms waiting for: %s" % (ms, cond))))
        card = "(document.querySelector('article[data-id=ht1]')||{innerText:''}).innerText"
        steps = [{"goto": url}, {"waitForText": "Harness project", "timeout": 15000},
                 {"eval": "(S.view='board', S.boardMode='lanes', S.projectId='hp1', subscribeTasks('hp1'), render(), 1)"}, {"sleep": 600},
                 {"eval": record},
                 {"eval": until(card + ".includes('Harness agent working')", 30000)},  # the agent is parked on the gate here
                 {"eval": "(window.__open=Date.now()-__t0, S.mcp.callTool(HOST,'start_process',{command:%s}).then(()=>window.__open))" % json.dumps("touch " + str(gate))},
                 # wait for the card itself to read Review (the status flips a render before the card does); generous for a loaded Mac
                 {"eval": until("__log.some(([, x]) => x.includes('Review'))", 90000)},
                 {"eval": "[__log, S.view, S.tasks.find(x=>x.id==='ht1').status]"}]
        out = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps), capture_output=True, text=True, timeout=200)
        lines = [json.loads(l) for l in out.stdout.splitlines() if l.startswith("{")]
        assert out.returncode == 0, out.stdout + out.stderr
        log, view, status = lines[8]["value"]
        first = lambda txt: next((t for t, x in log if txt in x), None)
        opened = lines[6]["value"]
        assert status == "review" and view == "board", (status, view, log)
        assert first("Checking before launch") is not None and first("Checking before launch") < 1500, log
        assert first("Running") is not None, log
        assert first("Harness agent working") is not None and first("Harness agent working") < opened, log  # live, while it works
        assert first("Review") is not None and first("Review") - opened < 60000, log  # seconds on an idle Mac; the bound only catches 'never' 
    finally:
        p.terminate()
        p.wait(10)


@pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome")
def test_worktree_setup_command_field_persists_through_the_page(harness):
    """Edit project's "Worktree setup command" field must reach .runway.json through the real Save button (data-act
    save-project), not just through saveSetupCmd called directly: that is the only way a human clicking Save in the
    Claude app actually sets it. A full page reload after saving rules out anything living only in page memory."""
    url, sandbox = harness
    steps = [{"goto": url}, {"waitForText": "Harness project", "timeout": 15000},
              {"eval": "S.projectId = 'hp1'; S.overlay = { kind: 'project', id: 'hp1' }; render(); true"},
              # the field appears once the page has seen the helper and the helper answered config-get: two
              # round trips through a Python process, slow when other suites share the Mac
              {"waitFor": "#p-setup", "timeout": 30000}, {"sleep": 1200},
              {"eval": "document.querySelector('#p-setup').value"},
              {"type": {"selector": "#p-setup", "text": "npm ci"}},
              {"click": "[data-act=save-project]"},
              # wait for the save to reach .runway.json through the helper, not a fixed time (slow under a full test run)
              {"eval": "new Promise(r => { const t0 = Date.now(); (async function w(){ let txt = ''; try { const res = await S.mcp.callTool(HOST, 'read_file', { path: S.settings.home + '/site/.runway.json' }); txt = resultText(res); } catch {} if ((txt.includes('npm ci') && !S.overlay) || Date.now() - t0 > 20000) return r(S.overlay); setTimeout(w, 200); })(); })"},
              {"goto": url}, {"waitForText": "Harness project", "timeout": 15000},
              {"eval": "S.projectId = 'hp1'; S.overlay = { kind: 'project', id: 'hp1' }; render(); true"},
              {"waitFor": "#p-setup", "timeout": 30000},
              {"eval": "new Promise(r => { const t0 = Date.now(); (function w(){ const v = document.querySelector('#p-setup')?.value || ''; if (v || Date.now() - t0 > 20000) return r(v); setTimeout(w, 150); })(); })"}]
    try:
        p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps), capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired as e:
        raise AssertionError("driver timed out: %s" % (e.stdout,))
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    assert lines[5]["value"] == "", "field should start empty"  # step index 5: the eval before typing
    assert lines[8]["value"] is None, "the modal should close after Save"  # step index 8: S.overlay, once saved
    assert lines[13]["value"] == "npm ci", "the field must still show the saved command after a full reload"  # step index 13: the last eval
    with open(os.path.join(sandbox, "site", ".runway.json")) as f:
        assert json.load(f)["setup"] == "npm ci"


def test_the_project_form_shows_the_setup_field_when_the_helper_comes_online_after_it_opened(harness):
    """Opening a project's form before the page has heard from the helper used to leave the setup field out for
    good: the overlay only redrew when its own data changed, not when the helper came online."""
    url, _ = harness
    steps = [{"goto": url}, {"waitForText": "Harness project", "timeout": 15000},
             # the form opens while the page believes the helper is off, as in the first moment after a load
             {"eval": "S.helper = null; S.helperState = 'off'; S.projectId = 'hp1'; S.overlay = { kind: 'project', id: 'hp1' }; render(); !!document.querySelector('#p-setup')"},
             {"waitFor": "#p-setup", "timeout": 20000}]  # the helper watch answers within seconds; the form must redraw
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps), capture_output=True, text=True, timeout=120)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert lines[2]["value"] is False  # really opened without the field
    assert p.returncode == 0, p.stdout + p.stderr
