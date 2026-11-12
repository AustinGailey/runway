"""Hands-off policies (PI 9 Sprint 1), end to end through the harness per CLAUDE.md's "Verifying the page end to end":
each policy acts once on a seeded task, writes one `actions` row and leaves the Inbox alone; with its toggle off the old
behaviour (an Inbox item, no action) returns; the give-up paths leave one Inbox item that says why; the daily digest is
one dismissible item; Activity lists the rows with Undo where one exists.

The page's own launch and merge functions are replaced in the page (mergeSelected, launchNow, host) so a policy's
decision is observed without starting an agent; the real Merge/launch paths are covered by test_harness.py. Each
drive() step list is [{"goto": url}] + steps, so v[i] is steps[i-1]'s value."""
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


def drive(url, steps, timeout=90):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps([{"goto": url}] + steps),
                       capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return [l.get("value") for l in lines if "step" in l]  # drop the trailing {"console", "exceptions"} summary line


def until(cond, ms=8000):
    """An eval step that resolves true as soon as cond is truthy in the page, false after ms."""
    return {"eval": "new Promise(r=>{const t0=Date.now();const i=setInterval(()=>{let ok=false;try{ok=!!(%s)}catch(e){}"
                    "if(ok||Date.now()-t0>%d){clearInterval(i);r(ok)}},100)})" % (cond, ms)}


# the five policies are on by default (Austin, 2026-10-16); auto-land is opt-in. Tests run on the defaults.
BOOT = [{"waitForText": "Harness project", "timeout": 15000}, until("S.loaded && helperOn()", 15000)]
ALL_OFF = {"eval": "(saveSettings({ handsOffMerge: false, handsOffRetry: false, handsOffBudget: false, handsOffDone: false, handsOffClose: false }), 1)"}
# replace the page's merge, launch and host calls with recorders
STUBS = {"eval": "(window.__merged=[], window.__launched=[], window.__host=[], "
                 "mergeSelected = async ids => { for (const id of ids) await patch('tasks', id, { run: { ...task(id).run, merging: true } }); window.__merged.push(...ids) }, "
                 "launchNow = async sel => { for (const t of sel) await patch('tasks', t.id, { status: 'running' }); window.__launched.push(...sel.map(t => t.id)) }, "
                 "host = async cmd => { window.__host.push(cmd); return cmd.includes('auth-check') ? '@@AUTH {\"ok\":true}' : '@@OK Landed in main.' }, 1)"}


def seed_task(tid, status="review", sprint="hsp1", run=None, **extra):
    """A finished, green, verified run that the policies can read, then whatever the test overrides."""
    base_run = {"chunkId": "chx-" + tid, "wt": "/tmp/wt-" + tid, "base": "main", "files": 2, "agentStatus": "done", "checksState": "green",
                "verify": {"pass": True}, "outcome": {"status": "done", "blockers": []}, "result": "Done.", "err": ""}
    t = {"projectId": "hp1", "piId": "hpi1", "sprintId": sprint, "title": "Task " + tid, "detail": "", "acceptance": "- ok", "estTokens": 50000,
         "model": "sonnet", "kind": "code", "status": status, "order": 5, "deps": [], "createdAt": 1791300000000, "finishedAt": "NOW-60000",
         "run": {**base_run, **(run or {})}, **extra}
    return json.dumps(t).replace('"NOW-60000"', "Date.now()-60000")


def put(col, doc_id, body_json):
    return {"eval": "put(%s, %s, %s).then(()=>1)" % (json.dumps(col), json.dumps(doc_id), body_json)}


def patch(col, doc_id, body_json):
    return {"eval": "patch(%s, %s, %s).then(()=>1)" % (json.dumps(col), json.dumps(doc_id), body_json)}


def ago(ms):
    """JSON for a body whose "at" is ms in the past, as a JS object literal."""
    return {"at": "AGO%d" % ms}


def put_ago(doc_id, ms, **fields):
    body = json.dumps({**ago(ms), **fields}).replace('"AGO%d"' % ms, "Date.now()-%d" % ms)
    return put("actions", doc_id, body)


def settings(**kw):
    return {"eval": "(saveSettings(%s), 1)" % json.dumps(kw)}


COUNT_ACTIONS = "S.actions.filter(a => a.taskId === '%s' && a.kind === '%s').length"
TASK_ITEMS = "inboxItems().filter(i => i.t && i.t.id === '%s').map(i => i.kind)"


def test_auto_merge_merges_once_logs_one_row_and_leaves_the_inbox_alone(harness):
    v = drive(harness, BOOT + [STUBS, put("tasks", "am1", seed_task("am1")),
                                until("window.__merged.length >= 1"), until("S.actions.some(a => a.taskId === 'am1')"),
                                {"eval": "applyHelper().then(applyHelper).then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__merged"}, {"eval": COUNT_ACTIONS % ("am1", "merge")}, {"eval": TASK_ITEMS % "am1"},
                                {"eval": "S.actions.find(a => a.taskId === 'am1').undo || null"}])
    assert v[-4] == ["am1"] and v[-3] == 1 and v[-2] == [] and v[-1] is None  # merges have no undo; the Activity list says so


def test_auto_merge_off_returns_the_old_review_item(harness):
    v = drive(harness, BOOT + [STUBS, settings(handsOffMerge=False), put("tasks", "am2", seed_task("am2")),
                                until("S.tasks.some(t => t.id === 'am2')"), {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__merged"}, {"eval": "S.actions.length"}, {"eval": TASK_ITEMS % "am2"}])
    assert v[-3] == [] and v[-2] == 0 and v[-1] == ["review"]


def test_project_can_opt_out_of_auto_merge(harness):
    v = drive(harness, BOOT + [STUBS, patch("projects", "hp1", json.dumps({"autoMerge": False})), put("tasks", "am3", seed_task("am3")),
                                until("S.tasks.some(t => t.id === 'am3')"), {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__merged"}, {"eval": TASK_ITEMS % "am3"}])
    assert v[-2] == [] and v[-1] == ["review"]


def test_red_checks_unverified_blocked_or_empty_work_does_not_auto_merge(harness):
    seeds = [put("tasks", "r1", seed_task("r1", run={"checksState": "red"})), put("tasks", "r2", seed_task("r2", run={"verify": {"pass": False}})),
             put("tasks", "r3", seed_task("r3", run={"agentStatus": "partial"})), put("tasks", "r4", seed_task("r4", run={"outcome": {"status": "done", "blockers": [{"type": "permission"}]}}))]
    v = drive(harness, BOOT + [STUBS, *seeds, until("S.tasks.filter(t => /^r[1-4]$/.test(t.id)).length === 4"),
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__merged"}, {"eval": "inboxItems().filter(i => i.t && /^r[1-4]$/.test(i.t.id)).length"}])
    assert v[-2] == [] and v[-1] == 4


def test_merge_conflict_after_an_automatic_attempt_goes_to_the_inbox_once_with_why(harness):
    v = drive(harness, BOOT + [STUBS,
                                put("actions", "am-mc1-chx-mc1", json.dumps({"at": 1791300000000, "taskId": "mc1", "projectId": "hp1", "kind": "merge", "detail": "Merged Task mc1 automatically"})),
                                put("tasks", "mc1", seed_task("mc1", run={"mergeOutcome": "conflict", "err": "Conflicts with approved work in app.py."})),
                                until("S.tasks.some(t => t.id === 'mc1')"), {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__merged"}, {"eval": "inboxItems().filter(i => i.t && i.t.id === 'mc1').map(i => i.why)"}])
    assert v[-2] == [] and len(v[-1]) == 1 and "merge conflict" in v[-1][0] and "app.py" in v[-1][0]


def test_infra_failures_retry_once_the_cause_clears_and_are_not_inbox_items(harness):
    now_ago = lambda ms: "Date.now()-%d" % ms
    auth = seed_task("ia1", "failed", run={"category": "auth", "agentStatus": None}, autoRetries=0)
    limit = seed_task("il1", "paused", run={"category": "limit"}).replace("Date.now()-60000", now_ago(2 * 3600e3))
    setup = seed_task("is1", "failed", run={"category": "setup"}).replace("Date.now()-60000", now_ago(20 * 60e3))
    young = seed_task("is2", "failed", run={"category": "setup"})  # failed a minute ago: still waiting
    v = drive(harness, BOOT + [STUBS,
                                put("tasks", "is2", young), until("S.tasks.some(t => t.id === 'is2')"),
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,300)))"},
                                {"eval": "waitingRetry(task('is2')).label"}, {"eval": TASK_ITEMS % "is2"},
                                {"eval": "(S.view = 'board', S.projectId = 'hp1', render(), document.body.textContent.includes('Waiting to retry: setup'))"},
                                put("tasks", "ia1", auth), put("tasks", "il1", limit), put("tasks", "is1", setup),
                                {"eval": "(saveSettings({ usageCal: { sessionResetAt: Date.now() - 3600e3 } }), 1)"},
                                until("S.tasks.filter(t => /^i[als]1$/.test(t.id)).length === 3"),
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "[...window.__launched].sort()"}, {"eval": "S.actions.filter(a => a.kind === 'retry').length"},
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,300)))"}, {"eval": "window.__launched.length"},
                                {"eval": "window.__host.filter(c => c.includes('auth-check')).length"},
                                {"eval": "S.tasks.filter(t => /^i[als]1$/.test(t.id)).map(t => t.autoRetries)"}])
    assert v[7] == "setup" and v[8] == [] and v[9] is True  # waiting: labelled on the card, not an Inbox item
    assert v[-6] == ["ia1", "il1", "is1"] and v[-5] == 3
    assert v[-3] == 3 and v[-2] == 1  # a second pass relaunches nothing; sign-in was checked once
    assert v[-1] == [1, 1, 1]


def test_retries_give_up_after_the_cap_with_one_inbox_item_that_says_why(harness):
    cases = [put("tasks", "g1", seed_task("g1", "failed", run={"category": "auth"}, autoRetries=2)),
             put("tasks", "g2", seed_task("g2", "failed", run={"category": "setup"}, autoRetries=1))]
    v = drive(harness, BOOT + [STUBS, *cases, until("S.tasks.filter(t => /^g[12]$/.test(t.id)).length === 2"),
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__launched"}, {"eval": "inboxItems().filter(i => i.t && /^g[12]$/.test(i.t.id)).map(i => [i.t.id, i.kind, i.why]).sort()"}])
    assert v[-2] == [] and [r[:2] for r in v[-1]] == [["g1", "failed"], ["g2", "failed"]]
    assert "2 times" in v[-1][0][2] and "sign-in" in v[-1][0][2] and "1 time" in v[-1][1][2]


def test_retry_off_returns_the_failed_inbox_item(harness):
    v = drive(harness, BOOT + [STUBS, settings(handsOffRetry=False), put("tasks", "o1", seed_task("o1", "failed", run={"category": "auth"})),
                                until("S.tasks.some(t => t.id === 'o1')"), {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__launched"}, {"eval": TASK_ITEMS % "o1"}])
    assert v[-2] == [] and v[-1] == ["failed"]


def test_budget_stop_resumes_once_with_a_doubled_cap_and_the_second_stop_goes_to_the_inbox(harness):
    v = drive(harness, BOOT + [STUBS, put("tasks", "b1", seed_task("b1", "paused", run={"category": "budget", "err": "Stopped at its budget guard"}, estTokens=400000)),
                                until("S.tasks.some(t => t.id === 'b1')"), {"eval": "guardUsd({ ...task('b1'), guardX: 1 })"},  # the base cap (the poll may already have resumed it)
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__launched"}, {"eval": "task('b1').guardX"}, {"eval": COUNT_ACTIONS % ("b1", "budget")}, {"eval": TASK_ITEMS % "b1"},
                                {"eval": "guardUsd(task('b1')) / 1"},
                                # it stopped again: a new run of the same task on the same chunk id is a second stop
                                {"eval": "patch('tasks', 'b1', { status: 'paused', finishedAt: Date.now(), run: { ...task('b1').run, chunkId: 'chx-b1-2', category: 'budget', err: 'Stopped at its budget guard' } }).then(()=>1)"},
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__launched.length"}, {"eval": "inboxItems().filter(i => i.t && i.t.id === 'b1').map(i => [i.kind, i.why])"}])
    assert v[-9] == ["b1"] and v[-8] == 2 and v[-7] == 1 and v[-6] == []  # resumed once, cap doubled, logged, not an Inbox item
    assert v[-5] == pytest.approx(2 * v[-11])
    assert v[-2] == 1 and v[-1][0][0] == "paused" and "again" in v[-1][0][1]  # the second stop is not resumed


def test_budget_stop_without_week_room_and_with_the_toggle_off_stays_in_the_inbox(harness):
    v = drive(harness, BOOT + [STUBS, settings(handsOffBudget=False), put("tasks", "b2", seed_task("b2", "paused", run={"category": "budget", "err": "Stopped at its budget guard"})),
                                until("S.tasks.some(t => t.id === 'b2')"), {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__launched"}, {"eval": TASK_ITEMS % "b2"}])
    assert v[-2] == [] and v[-1] == ["paused"]


def test_already_done_empty_run_is_marked_done_with_an_undo(harness):
    empty = seed_task("d1", run={"files": 0})
    unverified = seed_task("d2", run={"files": 0, "verify": {"pass": False}})
    v = drive(harness, BOOT + [STUBS, put("tasks", "d1", empty), put("tasks", "d2", unverified), until("S.tasks.filter(t => /^d[12]$/.test(t.id)).length === 2"),
                                until("task('d1').status === 'done'"), {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,300)))"},
                                {"eval": "task('d1').run.mergeNote"}, {"eval": COUNT_ACTIONS % ("d1", "done")}, {"eval": TASK_ITEMS % "d1"},
                                {"eval": "task('d2').status"}, {"eval": TASK_ITEMS % "d2"},
                                {"eval": "(S.view = 'runs', S.runsTab = 'activity', render(), 1)"}, {"waitForText": "Marked \"Task d1\" done"},
                                {"clickText": "Undo"}, until("task('d1').status === 'review'"), {"eval": TASK_ITEMS % "d1"},
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,300)))"}, {"eval": "task('d1').status"}])
    assert v[9] == "Already done (no change needed)" and v[10] == 1 and v[11] == []
    assert v[12] == "review" and v[13] == ["empty"]  # the verifier did not pass: Inbox as today
    assert v[18] == ["empty"] and v[20] == "review"  # undone: back in the Inbox, and not marked done again


def test_already_done_off_keeps_the_empty_inbox_item(harness):
    v = drive(harness, BOOT + [STUBS, settings(handsOffDone=False), put("tasks", "d3", seed_task("d3", run={"files": 0})), until("S.tasks.some(t => t.id === 'd3')"),
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,300)))"}, {"eval": "task('d3').status"}, {"eval": TASK_ITEMS % "d3"}])
    assert v[-2] == "review" and v[-1] == ["empty"]


def test_finished_sprint_closes_itself_unless_its_goal_names_a_human_step(harness):
    seeds = [put("sprints", "spa", json.dumps({"projectId": "hp1", "piId": "hpi1", "name": "Sprint A", "goal": "Ship it", "order": 5})),
             put("sprints", "spb", json.dumps({"projectId": "hp1", "piId": "hpi1", "name": "Sprint B", "goal": "Ship it. Human step: Austin reviews the log.", "order": 6})),
             put("tasks", "sa1", seed_task("sa1", "done", "spa", run={})), put("tasks", "sa2", seed_task("sa2", "dropped", "spa", run={})),
             put("tasks", "sb1", seed_task("sb1", "done", "spb", run={}))]
    v = drive(harness, BOOT + [STUBS, *seeds, until("S.tasks.filter(t => /^s[ab]\\d$/.test(t.id)).length === 3"),
                                until("S.sprints.find(s => s.id === 'spa').status === 'closed'"), {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,300)))"},
                                {"eval": "S.actions.filter(a => a.kind === 'sprint').length"}, {"eval": "S.sprints.find(s => s.id === 'spb').status || null"},
                                {"eval": "inboxItems().filter(i => i.kind === 'close').map(i => i.sp.id)"},
                                {"eval": "S.actions.find(a => a.kind === 'sprint').undo.op"}])
    assert v[-4] == 1 and v[-3] is None and v[-2] == ["spb"] and v[-1] == "reopen-sprint"


def test_sprint_close_off_leaves_the_close_item(harness):
    v = drive(harness, BOOT + [STUBS, settings(handsOffClose=False),
                                put("sprints", "spc", json.dumps({"projectId": "hp1", "piId": "hpi1", "name": "Sprint C", "goal": "Ship it", "order": 5})),
                                put("tasks", "sc1", seed_task("sc1", "done", "spc", run={})), until("S.tasks.some(t => t.id === 'sc1')"),
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,300)))"},
                                {"eval": "S.sprints.find(s => s.id === 'spc').status || null"}, {"eval": "inboxItems().filter(i => i.kind === 'close').map(i => i.sp.id)"}])
    assert v[-2] is None and v[-1] == ["spc"]


LANDABLE = ("S.helper = { ...S.helper, integrations: [{ repo: S.projects.find(p => p.id === 'hp1').repo, base: 'main', branch: 'runway/main', ahead: 2, current: 'main', "
            "review: null, landAt: null, overlap: [], health: {}, commits: ['Task A', 'Task B'] }] }")


def test_auto_land_is_off_by_default_and_lands_once_when_turned_on(harness):
    v = drive(harness, BOOT + [STUBS, {"eval": "(%s, 1)" % LANDABLE},
                                {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,300)))"}, {"eval": "window.__host.filter(c => c.includes(' land ')).length"},
                                {"eval": "inboxItems().filter(i => i.kind === 'land').length"},
                                settings(handsOffLand=True), {"eval": "runPolicies().then(runPolicies).then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "window.__host.filter(c => c.includes(' land ')).length"}, {"eval": "S.actions.filter(a => a.kind === 'land').length"},
                                {"eval": "inboxItems().filter(i => i.kind === 'land').length"}])
    assert v[-7] == 0 and v[-6] == 1  # old behaviour: a Land card waits for Austin
    assert v[-3] == 1 and v[-2] == 1 and v[-1] == 0


def test_a_failed_auto_land_stays_in_the_inbox_with_why(harness):
    v = drive(harness, BOOT + [STUBS, {"eval": "(host = async cmd => '@@ERR Your checkout has uncommitted edits in app.py.', 1)"}, {"eval": "(%s, 1)" % LANDABLE},
                                settings(handsOffLand=True), {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "inboxItems().filter(i => i.kind === 'land').map(i => i.why)"}])
    assert len(v[-1]) == 1 and "uncommitted edits" in v[-1][0]


def test_daily_digest_is_one_dismissible_item_replaced_each_day(harness):
    rows = [put_ago("x%d" % i, 3600000, taskId="t%d" % i, projectId="hp1", kind=k, detail=k) for i, k in enumerate(["merge", "merge", "retry", "budget", "sprint"])]
    rows.append(put_ago("old", 172800000, kind="merge", detail="old"))
    v = drive(harness, BOOT + [STUBS, *rows, until("S.actions.length === 6"),
                                {"eval": "inboxItems().filter(i => i.kind === 'digest').map(i => [i.merged, i.retried, i.closed])"},
                                {"eval": "(S.view = 'inbox', render(), 1)"}, {"waitForText": "Since yesterday: 2 merged, 2 retried, 1 closed automatically"},
                                {"click": "[data-act=digest-x]"}, until("inboxItems().every(i => i.kind !== 'digest')"),
                                {"eval": "(saveSettings({ digestDismissed: '2000-01-01' }), inboxItems().filter(i => i.kind === 'digest').length)"}])
    assert v[11] == [[2, 2, 1]] and v[-1] == 1  # yesterday's dismissal does not hide today's digest


def test_inbox_with_nothing_automatic_has_no_digest(harness):
    v = drive(harness, BOOT + [{"eval": "inboxItems().filter(i => i.kind === 'digest').length"}])
    assert v[-1] == 0


def test_settings_has_one_hands_off_section_with_auto_land_off(harness):
    v = drive(harness, BOOT + [{"eval": "(S.overlay = { kind: 'settings' }, render(), 1)"}, {"waitForText": "Hands-off"},
                                {"eval": "['handsOffMerge','handsOffRetry','handsOffBudget','handsOffDone','handsOffClose','handsOffLand'].map(k => document.querySelector('[data-sf=' + k + ']').value)"}])
    assert v[-1] == ["true"] * 5 + ["false"]  # on by default except auto-land, which stays opt-in


def test_all_toggles_off_nothing_happens(harness):
    v = drive(harness, BOOT + [ALL_OFF, STUBS, put("tasks", "u1", seed_task("u1")), put("tasks", "u2", seed_task("u2", "failed", run={"category": "auth"})),
                                until("S.tasks.filter(t => /^u[12]$/.test(t.id)).length === 2"), {"eval": "runPolicies().then(()=>new Promise(r=>setTimeout(r,500)))"},
                                {"eval": "[window.__merged.length, window.__launched.length, S.actions.length]"},
                                {"eval": "inboxItems().filter(i => i.t).map(i => i.kind).sort()"}])
    assert v[-2] == [0, 0, 0] and v[-1] == ["failed", "review"]


def test_activity_tab_lists_rows_with_undo_only_where_it_exists_and_has_an_empty_state(harness):
    v = drive(harness, BOOT + [{"eval": "(S.view = 'runs', S.runsTab = 'activity', render(), 1)"}, {"waitForText": "Nothing automatic yet"},
                                put("actions", "ac1", json.dumps({"at": 1791300000000, "taskId": "t1", "projectId": "hp1", "kind": "merge", "detail": "Merged Task one"})),
                                put("actions", "ac2", json.dumps({"at": 1791300001000, "taskId": "t2", "projectId": "hp1", "kind": "done", "detail": "Marked Task two done", "undo": {"op": "reopen-task"}})),
                                {"waitForText": "Marked Task two done"}, {"eval": "document.querySelectorAll('[data-act=action-undo]').length"},
                                {"eval": "document.body.textContent.includes('No undo')"}])
    assert v[-2] == 1 and v[-1] is True
