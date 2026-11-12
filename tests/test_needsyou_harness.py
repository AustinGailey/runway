"""Needs-you steps (PI 10 Sprint 1), end to end through the harness per CLAUDE.md: a task with an open step or of kind
'human' is never launched by any path; steps come from the planner, human-only blockers, can-this-start holds and the
drawer; the Needs you list has Done and Check; the last step clears the blocker and (hands-off retry on) launches the
task once; start holds resolve both ways; the migration is idempotent. launch commands are recorded and answered, the
start check is answered from window.__sc, everything else (needs-check, touch) runs for real in the harness sandbox."""
import json
import os
import shutil

import pytest

from test_launch_gates_harness import (BOOT, CHROME, RUN_POLICIES, SETTLE, Steps, blocked_run, harness, patch, plain, put, until)  # noqa: F401

pytestmark = [pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome"),
              pytest.mark.usefixtures("chrome_lock")]

STUB = {"eval": "(window.__launches=[], window.__sc=[], window.__real=host, "
                "host = async (cmd, ms) => { if (cmd.includes('run.sh')){ window.__launches.push(cmd); return '@@LAUNCHED x' } if (cmd.includes(' preflight ')) return '@@PREFLIGHT []'; "
                "if (cmd.includes(' start-check ')) return '@@STARTCHECK ' + JSON.stringify(window.__sc); return window.__real(cmd, ms) }, 1)"}
NOW = 1791300000000


def step(sid, text, done=False, check=None):
    return {"id": sid, "text": text, "done": done, "doneAt": NOW if done else None, **({"check": check} if check else {})}


def ny_task(tid, steps, status="todo", **extra):
    return plain(tid, "hsp1", status, needsYou=steps, **extra)


def js(o):
    return json.dumps(o)


def test_open_steps_and_human_tasks_are_never_launched_by_any_path(harness):
    s = Steps(*BOOT, STUB,
              put("tasks", "n1", ny_task("n1", [step("a", "Get the token")])),
              put("tasks", "nf", ny_task("nf", [step("a", "Get the token")], "failed", finishedAt=NOW, run={"chunkId": "chx-nf", "category": "setup", "agentStatus": None, "err": "setup failed"})),
              put("tasks", "nh", ny_task("nh", [], kind="human")),
              put("tasks", "nheld", ny_task("nheld", [step("a", "Buy the domain")], held=True)),
              until("task('n1') && task('nf') && task('nh') && task('nheld')"))
    s.ev("why", "[launchGate(task('n1')).kind, launchGate(task('n1')).msg, launchGate(task('nh')).kind]")
    s.ev("launch", "(S.sel = new Set(['n1', 'nf', 'nh']), launch(null, { skipPreflight: true }).then(() => ['n1', 'nf', 'nh'].map(id => task(id).status)))")
    s.ev("fill", "(S.sel.clear(), S.projectId = 'hp1', autofill(), [...S.sel].filter(id => /^n/.test(id)))")
    s.ev("direct", "launchNow([task('n1'), task('nf'), task('nh'), task('nheld')]).then(() => window.__launches.length)")
    s.ev("held", "(S.heldTriedAt = 0, S.launching = false, S.overlay = null, launchHeld(), new Promise(r => setTimeout(() => r([task('nheld').status, window.__launches.length]), 400)))")
    s.ev("sessions", "['n1', 'nf', 'nh', 'nheld'].map(id => readyForSessions(task(id)))")
    s.add(RUN_POLICIES, RUN_POLICIES)
    s.ev("policies", "[window.__launches.length, task('nf').status, task('nf').autoRetries || 0]")
    s.ev("inbox", "inboxItems().filter(i => i.t && /^n/.test(i.t.id)).length")
    # the control: ticking the last step launches it (hands-off retry is on by default)
    s.add({"eval": "tickStep(task('nheld'), 'a', 'hand').then(() => 1)"}, SETTLE)
    s.ev("after", "[window.__launches.length, task('nheld').status]")
    out = s.run(harness)
    assert out["why"] == ["needsyou", "Needs you: 1 step only you can do", "human"]
    assert out["launch"] == ["todo", "failed", "todo"] and out["fill"] == [] and out["direct"] == 0
    assert out["held"] == ["todo", 0] and out["sessions"] == [False, False, False, False]
    assert out["policies"] == [0, "failed", 0] and out["inbox"] == 0
    assert out["after"] == [1, "running"]


def test_steps_come_from_the_planner_human_only_blockers_start_holds_and_by_hand(harness):
    access = {"type": "access", "detail": "Needs the M4P GHL token", "needs": "GHL API token for the M4P location"}
    env_cmd = {"type": "environment", "detail": "Docker is not running", "command": "docker info"}
    decision = {"type": "decision", "detail": "Which table is canonical?"}
    s = Steps(*BOOT, STUB, {"eval": "(S.bkAt = Date.now() + 3600e3, 1)"})
    # planner: the prompt asks for steps and kind human; cleanTask keeps safe steps, drops unsafe check commands
    s.ev("prompt", "(p => [p.includes('needsYou'), p.includes('kind \"human\"')])(planPrompt({ name: 'n', goal: 'g', repo: '', context: '', pis: 1 }, ''))")
    s.ev("planner", "(t => [t.kind, t.needsYou.map(x => x.text), t.needsYou.map(x => x.check || null), t.needsYou.every(x => x.done === false)])"
                    "(cleanTask({ title: 'Wire GHL', kind: 'human', needsYou: [{ text: 'Get the token', check: 'grep -q TOKEN .env.local' }, 'Buy the domain', { text: 'x', check: 'rm -rf /' }] }, 'sonnet'))")
    # blockers: human-only ones become steps, a mix or a decision stays a blocker card
    s.ev("blockers", "[stepsFromBlockers(task('ht1'), [%s]).length, stepsFromBlockers(task('ht1'), [%s, %s]), stepsFromBlockers(task('ht1'), [%s]), stepsFromBlockers(task('ht1'), [%s])]"
         % (js(access), js(access), js(env_cmd), js(decision), js(env_cmd)))
    # start holds: a needs_you item becomes a step on the held task
    s.add(put("tasks", "sh_ny", plain("sh_ny", "hsp1")), until("task('sh_ny')"),
          {"eval": "holdStart(task('sh_ny'), { type: 'needs_you', detail: 'Austin must create the GHL workflow', needs: 'a GHL workflow' }).then(() => 1)"})
    s.ev("hold", "[task('sh_ny').blockerKey.startsWith('start:needs_you:'), task('sh_ny').needsYou.map(x => x.text), launchGate(task('sh_ny')).kind]")
    # by hand, in the drawer
    s.add({"eval": "(S.overlay = { kind: 'task', id: 'ht1' }, render(), 1)"}, {"waitFor": "#ny-text"}, {"type": {"selector": "#ny-text", "text": "Add the Vercel env var"}}, {"type": {"selector": "#ny-check", "text": "grep -q VAR .env.local"}},
          {"click": "[data-act=ny-add]"}, SETTLE)
    s.ev("hand", "[task('ht1').needsYou.map(x => [x.text, x.check, x.done]), launchGate(task('ht1')).kind]")
    out = s.run(harness)
    assert out["prompt"] == [True, True]
    assert out["planner"] == ["human", ["Get the token", "Buy the domain", "x"], ["grep -q TOKEN .env.local", None, None], True]
    assert out["blockers"][0] == 1 and out["blockers"][1] is None and out["blockers"][2] is None and out["blockers"][3] is None
    assert out["hold"] == [True, ["Austin must create the GHL workflow"], "needsyou"]
    assert out["hand"] == [[["Add the Vercel env var", "grep -q VAR .env.local", False]], "needsyou"]


def test_the_needs_you_list_groups_by_project_and_done_check_and_the_last_step_launch_once(harness, tmp_path):
    flag = str(tmp_path / "console-ready.txt")
    steps = [step("a", "Create the workflow"), step("b", "Host the console", check="test -e " + flag)]
    s = Steps(*BOOT, STUB, {"eval": "(S.bkAt = Date.now() + 3600e3, 1)"},
              put("tasks", "ny1", {**ny_task("ny1", steps), "blockerKey": "access:ghl", "blockerDetail": "the GHL token", "blockedAt": NOW}),
              until("task('ny1')"))
    s.ev("view", "(S.view = 'needs', render(), 1)")
    s.add({"waitFor": "[data-act=ny-done]"})
    s.ev("list", "[document.querySelector('.navi[data-v=needs] .count').textContent, document.querySelectorAll('[data-ny-project]').length, "
                 "document.querySelector('[data-ny-project]').dataset.nyProject, document.querySelectorAll('[data-act=ny-done]').length, document.querySelectorAll('[data-act=ny-check]').length]")
    s.add({"click": "[data-act=ny-done][data-id=ny1][data-s=a]"}, SETTLE)
    s.ev("one", "[openSteps(task('ny1')).length, task('ny1').blockerKey, window.__launches.length]")
    s.add({"click": "[data-act=ny-check][data-id=ny1][data-s=b]"}, SETTLE)
    s.ev("failing", "[openSteps(task('ny1')).length, window.__launches.length]")
    s.add({"eval": "host(%s).then(() => 1)" % js("touch " + flag)}, {"click": "[data-act=ny-check][data-id=ny1][data-s=b]"}, until("!task('ny1').blockerKey"), SETTLE)
    s.ev("last", "[openSteps(task('ny1')).length, task('ny1').blockerKey || null, window.__launches.length, task('ny1').status]")
    s.add({"eval": "tickStep(task('ny1'), 'b', 'hand').then(() => 1)"}, SETTLE)
    s.ev("again", "window.__launches.length")
    s.ev("cleared_list", "(render(), document.body.textContent.includes('Nothing waiting on you'))")
    out = s.run(harness)
    assert out["list"] == ["2", 1, "Harness project", 2, 1]
    assert out["one"] == [1, "access:ghl", 0]
    assert out["failing"] == [1, 0]
    assert out["last"] == [0, None, 1, "running"]
    assert out["again"] == 1 and out["cleared_list"] is True


def test_the_last_step_only_clears_the_key_with_hands_off_retry_off_and_finishes_a_human_task(harness):
    s = Steps(*BOOT, STUB, {"eval": "(saveSettings({ handsOffRetry: false }), 1)"},
              put("tasks", "nr1", {**ny_task("nr1", [step("a", "Do it")]), "blockerKey": "access:x", "blockerDetail": "x", "blockedAt": NOW}),
              put("tasks", "hu1", ny_task("hu1", [step("a", "Call the client"), step("b", "Sign the form")], kind="human")),
              until("task('nr1') && task('hu1')"),
              {"eval": "tickStep(task('nr1'), 'a', 'hand').then(() => 1)"}, SETTLE)
    s.ev("off", "[task('nr1').blockerKey || null, task('nr1').status, window.__launches.length, launchGate(task('nr1'))]")
    s.add({"eval": "tickStep(task('hu1'), 'a', 'hand').then(() => 1)"}, SETTLE)
    s.ev("half", "task('hu1').status")
    s.add({"eval": "tickStep(task('hu1'), 'b', 'hand').then(() => 1)"}, SETTLE, RUN_POLICIES)
    s.ev("human", "[task('hu1').status, window.__launches.length]")
    out = s.run(harness)
    assert out["off"] == [None, "todo", 0, None]
    assert out["half"] == "todo" and out["human"] == ["done", 0]


def test_start_holds_resolve_when_the_check_says_ok_and_stay_while_it_does_not(harness):
    def hold(tid, kind):
        return put("tasks", tid, {**plain(tid, "hsp1"), "blockerKey": "start:%s:thing" % kind, "blockerDetail": "the thing", "blockedAt": NOW,
                                  "blocker": {"type": kind, "detail": "the thing", "needs": "the thing"}})
    s = Steps(*BOOT, STUB, {"eval": "(S.bkAt = Date.now() + 3600e3, 1)"}, hold("sf", "foundation"), hold("sd", "decision"), until("task('sf') && task('sd')"))
    pol = "(S.shAt = 0, policyStartHolds().then(() => ['sf', 'sd'].map(id => task(id).blockerKey || null)))"
    miss = lambda tid, typ: {"id": tid, "ok": False, "missing": [{"type": typ, "detail": "the thing", "needs": "the thing"}]}
    # still missing, and a check that couldn't run, are not ok
    s.add({"eval": "(window.__sc = %s, 1)" % js([miss("sf", "foundation"), miss("sd", "decision")])})
    s.ev("missing", pol)
    s.add({"eval": "(window.__sc = %s, 1)" % js([{"id": "sf", "ok": True, "missing": [], "warning": "The start check couldn't run"}, miss("sd", "decision")])})
    s.ev("warning", pol)
    # the foundation lands: ok clears that key only, the decision stays held
    s.add({"eval": "(window.__sc = %s, 1)" % js([{"id": "sf", "ok": True, "missing": [], "cached": False}, miss("sd", "decision")])})
    s.ev("ok", pol)
    s.ev("gate", "[launchGate(task('sf')), launchGate(task('sd')).kind, !!task('sf').blocker]")
    # a decision hold whose answer now reads needs_you turns into a step
    s.add({"eval": "(window.__sc = %s, 1)" % js([miss("sd", "needs_you")])})
    s.ev("needs", "(S.shAt = 0, policyStartHolds().then(() => [task('sd').blockerKey.startsWith('start:needs_you:'), openSteps(task('sd')).length, launchGate(task('sd')).kind]))")
    s.add({"eval": "tickStep(task('sd'), task('sd').needsYou[0].id, 'hand').then(() => 1)"}, SETTLE)
    s.ev("ticked", "[task('sd').blockerKey || null, task('sd').blocker || null]")
    out = s.run(harness)
    assert out["missing"][0].startswith("start:foundation:") and out["missing"][1].startswith("start:decision:")
    assert out["warning"][0].startswith("start:foundation:")
    assert out["ok"][0] is None and out["ok"][1].startswith("start:decision:")
    assert out["gate"] == [None, "blocker", False]
    assert out["needs"] == [True, 1, "needsyou"]
    assert out["ticked"] == [None, None]


def test_the_migration_converts_human_only_blockers_once_and_gives_ghl_its_four_steps(harness):
    human = blocked_run({"type": "access", "detail": "Needs the M4P GHL token", "needs": "M4P GHL API token"}, {"type": "environment", "detail": "No hosted console reachable from GHL"})
    mixed = blocked_run({"type": "access", "detail": "Needs the GHL token"}, {"type": "permission", "detail": "Needs make", "command": "make build"})
    def blocked(tid, run, **extra):
        return {**plain(tid, "hsp1", "review", finishedAt=NOW, run=run), "blockerKey": "k:" + tid, "blockerDetail": "waiting", "blockedAt": NOW, **extra}
    s = Steps(*BOOT, STUB, {"eval": "(S.bkAt = Date.now() + 3600e3, 1)"},
              put("tasks", "mg1", blocked("mg1", human)), put("tasks", "mg2", blocked("mg2", mixed)),
              put("tasks", "t-ghl-stage-sync", plain("t-ghl-stage-sync", "hsp1", "failed", finishedAt=NOW)),
              until("task('mg1') && task('mg2') && task('t-ghl-stage-sync')"))
    s.ev("before", "inboxItems().filter(i => i.t && i.t.id === 'mg1').length")
    mig = "(S.nyMigrated = false, migrateNeedsYou().then(() => 1))"
    s.add({"eval": mig}, SETTLE)
    s.ev("first", "[task('mg1').needsYou.length, (task('mg2').needsYou || []).length, task('t-ghl-stage-sync').needsYou.map(x => x.id), task('t-ghl-stage-sync').needsYou[1].check, "
                  "inboxItems().filter(i => i.t && /^(mg1|t-ghl)/.test(i.t.id)).length, launchGate(task('mg1')).kind]")
    s.add({"eval": mig}, SETTLE, {"eval": mig}, SETTLE)
    s.ev("second", "[task('mg1').needsYou.length, task('t-ghl-stage-sync').needsYou.length]")
    # ticking a step is not undone by another migration pass
    s.add({"eval": "tickStep(task('t-ghl-stage-sync'), 'ghl1', 'hand').then(() => 1)"}, SETTLE, {"eval": mig}, SETTLE)
    s.ev("third", "task('t-ghl-stage-sync').needsYou.map(x => x.done)")
    out = s.run(harness)
    assert out["before"] == 1
    assert out["first"] == [2, 0, ["ghl1", "ghl2", "ghl3", "ghl4"], "grep -q GHL_WEBHOOK_SECRET .env.local", 0, "needsyou"]
    assert out["second"] == [2, 4] and out["third"] == [True, False, False, False]
