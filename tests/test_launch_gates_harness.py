"""Launch gates (PI 10 Sprint 1), end to end through the harness per CLAUDE.md's "Verifying the page end to end":
sprint order, parked status and no rerun on an unchanged blocker each stop every launch path (Launch, Fill a chunk,
launchHeld, hands-off retries and resumes, the session mirror, launchNow itself), say why on the card, and clear when
their condition does. The page's host() is wrapped: launch commands are recorded and answered, everything else (the
helper's blocker-check, touch) runs for real in the harness sandbox. Each drive() list is [{"goto": url}] + steps."""
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
    yield line.split()[1]
    p.terminate()
    p.wait(10)


def drive(url, steps, timeout=120):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps([{"goto": url}] + steps),
                       capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return [l.get("value") for l in lines if "step" in l]


def until(cond, ms=8000):
    return {"eval": "new Promise(r=>{const t0=Date.now();const i=setInterval(()=>{let ok=false;try{ok=!!(%s)}catch(e){}"
                    "if(ok||Date.now()-t0>%d){clearInterval(i);r(ok)}},100)})" % (cond, ms)}


class Steps:
    """Steps with names: ev("label", js) adds an eval; out["label"] is its value after the drive."""
    def __init__(self, *pre):
        self.steps, self.names = list(pre), {}

    def ev(self, name, js):
        self.names[name] = len(self.steps)
        self.steps.append({"eval": js})
        return self

    def add(self, *steps):
        self.steps.extend(steps)
        return self

    def run(self, harness):
        v = drive(harness, self.steps)  # drive() puts the goto first, so v[i + 1] is steps[i]'s value
        return {n: v[i + 1] for n, i in self.names.items()}


BOOT = [{"waitForText": "Harness project", "timeout": 15000}, {"eval": "new Promise(r=>{const i=setInterval(()=>{if(S.loaded&&helperOn()&&S.fullProjects.has('hp1')){clearInterval(i);r(1)}},100)})"}]
# launch commands are recorded and answered; every other host call (blocker-check, touch) runs for real in the sandbox
STUB = {"eval": "(window.__launches=[], window.__real=host, "
                "host = async (cmd, ms) => { if (cmd.includes('run.sh')){ window.__launches.push(cmd); return '@@LAUNCHED x' } if (cmd.includes(' preflight ')) return '@@PREFLIGHT []'; return window.__real(cmd, ms) }, 1)"}
SETTLE = {"eval": "new Promise(r=>setTimeout(r,500))"}
RUN_POLICIES = {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"}


def js(o):
    return json.dumps(o)


def put(col, doc_id, body):
    return {"eval": "put(%s, %s, %s).then(()=>1)" % (js(col), js(doc_id), body if isinstance(body, str) else js(body))}


def patch(col, doc_id, body):
    return {"eval": "patch(%s, %s, %s).then(()=>1)" % (js(col), js(doc_id), body if isinstance(body, str) else js(body))}


def plain(tid, sprint, status="todo", **extra):
    return {"projectId": "hp1", "piId": "hpi1", "sprintId": sprint, "title": "Task " + tid, "detail": "", "acceptance": "- ok", "estTokens": 50000,
            "model": "sonnet", "kind": "code", "status": status, "order": 5, "deps": [], "createdAt": 1791300000000, **extra}


def blocked_run(*blockers, status="blocked"):
    return {"chunkId": "chx-bk", "wt": "", "base": "main", "files": 0, "agentStatus": status, "outcome": {"status": status, "blockers": list(blockers)}, "result": "", "err": ""}


SP2 = put("sprints", "hsp2", {"projectId": "hp1", "piId": "hpi1", "name": "Sprint 2 · Later", "goal": "", "order": 2})
# the seed's Sprint 1 has two todo tasks (ht1, ht2): sprint 2 waits for them

HOURS_AGO = lambda h: "Date.now()-%d" % int(h * 3600e3)


def test_a_later_sprint_is_not_launched_by_any_path_while_an_earlier_one_is_unfinished(harness):
    failed = plain("x2f", "hsp2", "failed", finishedAt="NOW-20MIN", run={"chunkId": "chx-x2f", "category": "setup", "agentStatus": None, "err": "setup failed"})
    s = Steps(*BOOT, STUB, SP2)
    for tid, body in [("x2", plain("x2", "hsp2")), ("x2h", plain("x2h", "hsp2", held=True)), ("x2b", plain("x2b", "hsp2", "paused", finishedAt="NOW-1MIN", run={"chunkId": "chx-x2b", "category": "budget", "err": "Stopped at its budget guard"}))]:
        s.add(put("tasks", tid, json.dumps(body).replace('"NOW-1MIN"', "Date.now()-60000")))
    s.add(put("tasks", "x2f", json.dumps(failed).replace('"NOW-20MIN"', "Date.now()-1200000")), until("S.tasks.filter(t => /^x2/.test(t.id)).length === 4"))
    s.ev("why", "launchGate(task('x2')).msg")
    s.ev("launch", "(S.sel = new Set(['x2', 'x2f']), launch(null, { skipPreflight: true }).then(() => [task('x2').status, task('x2f').status]))")
    s.ev("fill", "(S.sel.clear(), S.projectId = 'hp1', autofill(), [...S.sel].filter(id => /^x2/.test(id)))")
    s.ev("direct", "launchNow([task('x2'), task('x2f')]).then(() => window.__launches.length)")
    s.ev("held", "(S.heldTriedAt = 0, S.launching = false, S.overlay = null, launchHeld(), new Promise(r => setTimeout(() => r([task('x2h').status, window.__launches.length]), 400)))")
    s.ev("sessions", "['x2', 'x2h', 'x2f'].map(id => readyForSessions(task(id)))")
    s.add(RUN_POLICIES, RUN_POLICIES)
    s.ev("policies", "[window.__launches.length, task('x2f').status, task('x2b').status, task('x2f').autoRetries || 0]")
    s.ev("card", "(S.view = 'board', S.projectId = 'hp1', S.piId['hp1'] = 'hpi1', S.menu = null, S.overlay = null, render(), document.body.textContent.includes('Waits for Sprint 1: 2 tasks left'))")
    # the control: finish sprint 1 and every one of those paths works
    s.add(patch("tasks", "ht1", {"status": "done"}), patch("tasks", "ht2", {"status": "dropped"}))
    s.ev("cleared", "launchGate(task('x2'))")
    s.add(RUN_POLICIES, RUN_POLICIES)
    s.ev("after", "window.__launches.length")
    s.ev("sessions2", "readyForSessions(task('x2'))")
    out = s.run(harness)
    assert out["why"] == "Waits for Sprint 1: 2 tasks left"
    assert out["launch"] == ["todo", "failed"] and out["fill"] == [] and out["direct"] == 0
    assert out["held"] == ["todo", 0] and out["sessions"] == [False, False, False]
    assert out["policies"] == [0, "failed", "paused", 0] and out["card"] is True
    assert out["cleared"] is None and out["after"] >= 1 and out["sessions2"] is True


def test_an_empty_earlier_sprint_blocks_and_independent_overrides_it(harness):
    s = Steps(*BOOT, STUB,
              put("pis", "hpi2", {"projectId": "hp1", "name": "PI 2", "objective": "", "order": 2}),
              put("sprints", "e1", {"projectId": "hp1", "piId": "hpi2", "name": "Sprint 1 · Links and action log", "goal": "", "order": 1}),
              put("sprints", "e2", {"projectId": "hp1", "piId": "hpi2", "name": "Sprint 2 · Later", "goal": "", "order": 2}),
              put("tasks", "y2", {**plain("y2", "e2"), "piId": "hpi2"}),
              until("task('y2')"))
    s.ev("why", "launchGate(task('y2')).msg")
    s.ev("launch", "(S.sel = new Set(['y2']), launch(null, { skipPreflight: true }).then(() => [task('y2').status, window.__launches.length]))")
    # the task's own flag, set from the drawer
    s.add({"eval": "(S.overlay = { kind: 'task', id: 'y2' }, render(), 1)"}, {"waitFor": "#t-indep"}, {"click": "#t-indep"}, {"click": "button[data-act=save-task]"})
    s.ev("task_flag", "[task('y2').independent, launchGate(task('y2'))]")
    # the sprint's flag, set from the sprint editor
    s.add(patch("tasks", "y2", {"independent": False}))
    s.ev("back", "launchGate(task('y2')).msg")
    s.add({"eval": "(S.overlay = { kind: 'sprint', id: 'e2' }, render(), 1)"}, {"waitFor": "#g-indep"}, {"click": "#g-indep"}, {"click": "button[data-act=save-group]"})
    s.ev("sprint_flag", "[S.sprints.find(x => x.id === 'e2').independent, launchGate(task('y2'))]")
    s.ev("launched", "(S.sel = new Set(['y2']), launch(null, { skipPreflight: true }).then(() => new Promise(r => setTimeout(() => r(window.__launches.length), 300))))")
    out = s.run(harness)
    assert out["why"] == "Waits for Sprint 1 · Links and action log: no tasks planned yet"
    assert out["launch"] == ["todo", 0]
    assert out["task_flag"] == [True, None] and out["back"].startswith("Waits for")
    assert out["sprint_flag"] == [True, None] and out["launched"] == 1


def test_parked_tasks_are_never_launched_or_in_the_inbox_and_park_unpark_work_from_drawer_inbox_and_board(harness):
    review = {**plain("pk2", "hsp1", "review", finishedAt=1791300000000, run={"chunkId": "chx-pk2", "wt": "/tmp/wt-pk2", "base": "main", "files": 2, "agentStatus": "done", "outcome": {"status": "done", "blockers": []}, "verify": {"pass": False}})}
    s = Steps(*BOOT, STUB,
              put("tasks", "pk1", plain("pk1", "hsp1", "parked", parkedReason="Austin deferred it")),
              put("tasks", "pk2", review), until("task('pk1') && task('pk2')"))
    s.ev("gate", "launchGate(task('pk1')).msg")
    s.ev("inbox", "inboxItems().filter(i => i.t && i.t.id === 'pk1').length")
    s.ev("paths", "(S.sel = new Set(['pk1']), launch(null, { skipPreflight: true }).then(() => launchNow([task('pk1')])).then(() => (S.sel.clear(), S.projectId = 'hp1', autofill(), [task('pk1').status, window.__launches.length, S.sel.has('pk1'), readyForSessions(task('pk1'))])))")
    s.add(RUN_POLICIES)
    s.ev("policies", "[window.__launches.length, task('pk1').status]")
    # drawer: Park then Unpark
    s.add({"eval": "(S.overlay = { kind: 'task', id: 'ht1' }, render(), 1)"}, {"click": ".drawer [data-act=park]"}, SETTLE)
    s.ev("drawer_park", "task('ht1').status")
    s.add({"eval": "(S.overlay = { kind: 'task', id: 'ht1' }, render(), 1)"}, {"click": ".drawer [data-act=unpark]"}, SETTLE)
    s.ev("drawer_unpark", "task('ht1').status")
    # inbox item menu
    s.ev("inbox_before", "inboxItems().some(i => i.t && i.t.id === 'pk2')")
    s.add({"eval": "(S.overlay = null, S.view = 'inbox', S.menu = 'i:pk2', render(), 1)"}, {"click": ".menu [data-act=park]"}, SETTLE)
    s.ev("inbox_after", "[task('pk2').status, inboxItems().some(i => i.t && i.t.id === 'pk2')]")
    # board card menu, and the collapsed Parked group
    s.add({"eval": "(S.view = 'board', S.projectId = 'hp1', S.piId['hp1'] = 'hpi1', S.menu = 'c:ht2', render(), 1)"}, {"click": ".card .menu [data-act=park]"}, SETTLE)
    s.ev("board", "[task('ht2').status, document.querySelectorAll('.card.parked').length, document.body.textContent.includes('Parked · '), task('ht2').parkedFrom]")
    s.add({"click": "[data-act=toggle-parked]"})
    s.ev("expanded", "document.querySelectorAll('.card.parked').length")
    s.add({"eval": "(S.menu = 'c:ht2', render(), 1)"}, {"click": ".card.parked .menu [data-act=unpark]"}, SETTLE)
    s.ev("board_unpark", "task('ht2').status")
    out = s.run(harness)
    assert out["gate"] == "Parked: Austin deferred it" and out["inbox"] == 0
    assert out["paths"] == ["parked", 0, False, False] and out["policies"] == [0, "parked"]
    assert out["drawer_park"] == "parked" and out["drawer_unpark"] == "todo"
    assert out["inbox_before"] is True and out["inbox_after"] == ["parked", False]
    assert out["board"][0] == "parked" and out["board"][1] == 0 and out["board"][2] is True and out["expanded"] >= 1
    assert out["board_unpark"] == "todo"


def blocker_task(tid, blockers, status="todo", **extra):
    return {**plain(tid, "hsp1", status, finishedAt=1791300000000, run=blocked_run(*blockers)), "blockerKey": "k:" + tid, "blockerDetail": "waiting", "blockedAt": 1791300000000, **extra}


def test_a_blocked_task_cannot_be_launched_by_any_path_until_forced(harness):
    s = Steps(*BOOT, STUB,
              put("tasks", "bk1", blocker_task("bk1", [{"type": "dependency", "detail": "Needs the login task", "needs": "Task login"}])),
              put("tasks", "bk1f", blocker_task("bk1f", [{"type": "environment", "detail": "the service is down", "command": "test -e /no/such/gate/path"}], "failed", run={**blocked_run({"type": "environment", "detail": "the service is down", "command": "test -e /no/such/gate/path"}), "category": "setup", "err": "setup"})),
              until("task('bk1') && task('bk1f')"))
    s.ev("gate", "launchGate(task('bk1')).msg")
    s.ev("launch", "(S.sel = new Set(['bk1', 'bk1f']), launch(null, { skipPreflight: true }).then(() => [task('bk1').status, task('bk1f').status]))")
    s.ev("fill", "(S.sel.clear(), S.projectId = 'hp1', autofill(), [...S.sel].filter(id => /^bk1/.test(id)))")
    s.ev("direct", "launchNow([task('bk1'), task('bk1f')]).then(() => window.__launches.length)")
    s.ev("sessions", "readyForSessions(task('bk1'))")
    s.add(RUN_POLICIES)
    s.ev("policies", "[window.__launches.length, task('bk1').status]")
    s.ev("card", "(S.view = 'board', S.projectId = 'hp1', S.piId['hp1'] = 'hpi1', S.menu = null, S.overlay = null, render(), document.body.textContent.includes('Waiting on: waiting'))")
    # Austin forces it: logged, and it launches
    s.add({"click": ".card [data-act=bk-force][data-id=bk1]"}, SETTLE)
    s.ev("forced", "[task('bk1').blockerKey, S.actions.some(a => a.taskId === 'bk1' && /anyway/.test(a.detail))]")
    s.ev("then", "(S.sel = new Set(['bk1']), launch(null, { skipPreflight: true }).then(() => new Promise(r => setTimeout(() => r(window.__launches.length), 300))))")
    out = s.run(harness)
    assert out["gate"] == "Waiting on: waiting"
    assert out["launch"] == ["todo", "failed"] and out["fill"] == [] and out["direct"] == 0 and out["sessions"] is False
    assert out["policies"] == [0, "todo"] and out["card"] is True
    assert out["forced"] == [None, True] and out["then"] == 1


def test_blockers_clear_when_resolved_and_hands_off_retry_relaunches_once(harness, tmp_path):
    need = str(tmp_path / "gate-needed.txt")
    access = {"type": "access", "detail": "Needs the notes file", "path": need}
    s = Steps(*BOOT, STUB, {"eval": "(S.bkAt = Date.now() + 3600e3, 1)"},  # the page's own poll leaves blockers alone while the test sets them up
              put("tasks", "dp1", plain("dp1", "hsp1", "todo")),
              put("tasks", "rs_access", blocker_task("rs_access", [access])),
              put("tasks", "rs_dep", blocker_task("rs_dep", [{"type": "dependency", "detail": "Needs dp1 merged", "needs": "Task dp1"}], "review")),
              put("tasks", "rs_perm", blocker_task("rs_perm", [{"type": "permission", "detail": "Needs make", "command": "make build"}], "review")),
              put("tasks", "rs_dec", blocker_task("rs_dec", [{"type": "decision", "detail": "Which db?", "options": ["a", "b"]}], "review")),
              until("task('rs_access') && task('rs_dep') && task('rs_perm') && task('rs_dec')"))
    unresolved = "checkBlockers().then(l => l.map(t => t.id).sort())"
    s.ev("none", unresolved)
    s.ev("decision", "[bkVerdicts(task('rs_dec'), [])[0], bkVerdicts(task('rs_dec'), [], { answered: new Set([0]) })[0]]")
    # access: the path appears
    s.add({"eval": "host(%s)" % js("touch " + need)})
    s.ev("access", unresolved)
    # dependency: the named task is merged
    s.add(patch("tasks", "dp1", {"status": "done"}))
    s.ev("dep", unresolved)
    # permission: the project's settings change after blockedAt
    s.add(patch("projects", "hp1", {"allowedTools": ["Bash(make:*)"]}))
    s.ev("perm", unresolved)
    # hands-off retry on (the default): cleared and relaunched exactly once, never a second time
    s.add({"eval": "(S.bkAt = 0, 1)"}, RUN_POLICIES, {"eval": "(S.bkAt = 0, 1)"}, RUN_POLICIES)
    s.ev("cleared", "['rs_access', 'rs_dep', 'rs_perm', 'rs_dec'].map(id => task(id).blockerKey || null)")
    s.ev("launches", "[window.__launches.length, ['rs_access', 'rs_dep', 'rs_perm'].map(id => task(id).status), S.tasks.filter(t => t.unblockRetried).length]")
    out = s.run(harness)
    assert out["none"] == [] and out["decision"] == [False, True]
    assert out["access"] == ["rs_access"] and out["dep"] == ["rs_access", "rs_dep"] and out["perm"] == ["rs_access", "rs_dep", "rs_perm"]
    assert out["cleared"][:3] == [None, None, None] and out["cleared"][3]  # the unanswered decision keeps its key
    assert out["launches"][1] == ["running", "running", "running"] and out["launches"][2] == 3
    assert out["launches"][0] == 3  # one launch each, and the second pass relaunches nothing


def test_resolved_blocker_with_hands_off_retry_off_only_clears_the_key(harness):
    s = Steps(*BOOT, STUB, {"eval": "(saveSettings({ handsOffRetry: false }), S.bkAt = Date.now() + 3600e3, 1)"},
              put("tasks", "off1", blocker_task("off1", [{"type": "dependency", "detail": "Needs dp2", "needs": "Task dp2"}], "review")),
              put("tasks", "dp2", plain("dp2", "hsp1", "done")), until("task('off1') && task('dp2')"),
              {"eval": "(S.bkAt = 0, 1)"}, RUN_POLICIES)
    s.ev("state", "[task('off1').blockerKey || null, task('off1').status, window.__launches.length]")
    assert s.run(harness)["state"] == [None, "review", 0]


def test_existing_blocked_reviews_get_a_key_once_on_first_load(harness):
    oc = {"type": "access", "detail": "Needs the notes", "path": "/Users/x/notes.md"}
    seeded = {**plain("old1", "hsp1", "review", finishedAt=1791300000000, run=blocked_run(oc, {"type": "decision", "detail": "Which one?"}, status="partial"))}
    clean = {**plain("old2", "hsp1", "review", finishedAt=1791300000000, run={**blocked_run(), "outcome": {"status": "done", "blockers": []}, "agentStatus": "done"})}
    s = Steps(*BOOT, put("tasks", "old1", seeded), put("tasks", "old2", clean), until("task('old1') && task('old2')"),
              {"eval": "(S.bkBackfilled = false, backfillBlockerKeys().then(() => 1))"}, SETTLE)
    s.ev("key", "[task('old1').blockerKey, task('old1').blockedAt, task('old2').blockerKey || null]")
    s.ev("expected", "blockerKeyOf(task('old1').run.outcome.blockers)")
    # idempotent: a key Austin cleared is not put back by another pass
    s.add(patch("tasks", "old1", {"blockerKey": None}), {"eval": "(S.bkBackfilled = false, backfillBlockerKeys().then(() => 1))"}, SETTLE)
    s.ev("again", "task('old1').blockerKey || null")
    out = s.run(harness)
    assert out["key"][0] == out["expected"] and "access:/users/x/notes.md" in out["expected"] and "decision:which one?" in out["expected"]
    assert out["key"][1] == 1791300000000 and out["key"][2] is None and out["again"] is None
