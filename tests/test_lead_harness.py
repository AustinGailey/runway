"""The AI lead through the page (PI 10 Sprint 2), end to end in the harness per CLAUDE.md: a decision blocker is decided
by the lead (recorded in project.decisions and the vault log as ai-derived, same session resumed), an escalated one
stays an Inbox card with a note, and Austin can overturn a decision before or after merge. The helper's `lead` call is
answered from window.__leadAnswer (the stand-in claude behind it is covered by test_lead.py); launches are recorded;
everything else (lead-info, export, touch) runs for real in the harness sandbox."""
import json
import os
import shutil

import pytest

from test_launch_gates_harness import (BOOT, CHROME, RUN_POLICIES, SETTLE, Steps, blocked_run, harness, patch, plain, put, until)  # noqa: F401

pytestmark = [pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome"),
              pytest.mark.usefixtures("chrome_lock")]

# the lead is on by default (Austin, 2026-10-19); saved through the page so the settings snapshot that follows keeps it
ENABLE = {"eval": "(saveSettings({ aiLead: true }), new Promise(r => setTimeout(r, 600)))"}
DISABLE = {"eval": "(saveSettings({ aiLead: false }), new Promise(r => setTimeout(r, 600)))"}
STUB = {"eval": "(window.__launches=[], window.__leadCalls=0, window.__leadAnswer=null, window.__exports=[], window.__rp=[], window.__real=host, window.__patch=patch, "
                "patch = async (c, i, b) => { if (c === 'tasks' && b && b.resumePrompt) window.__rp.push([i, b.resumePrompt]); return window.__patch(c, i, b) }, "
                "host = async (cmd, ms) => { if (cmd.includes('run.sh')){ window.__launches.push(cmd); return '@@LAUNCHED x' } if (cmd.includes(' preflight ')) return '@@PREFLIGHT []'; "
                "if (cmd.includes(' lead ')){ window.__leadCalls++; return '@@LEAD ' + JSON.stringify(window.__leadAnswer) } "
                "if (cmd.includes(' export ')) window.__exports.push(atob(cmd.split(' export ')[1].replace(/'/g, ''))); return window.__real(cmd, ms) }, 1)"}
DECIDE = {"decision": "pytest", "why": "The project rules say pytest.", "confidence": 0.9, "escalate": False, "categories": [], "reason": "", "costUsd": 0.03}
DECISION = {"type": "decision", "detail": "Which test runner should the new module use?", "options": ["pytest", "unittest"]}
ANSWER = lambda a: {"eval": "(window.__leadAnswer = %s, 1)" % json.dumps(a)}  # noqa: E731


def lead_task(tid):
    """A run that ended on a decision blocker a minute ago, with a session to resume."""
    body = plain(tid, "hsp1", "review", finishedAt="NOW-1MIN", run={**blocked_run(DECISION), "session": "sess-" + tid, "result": "I stopped: the test runner is open."})
    return put("tasks", tid, json.dumps(body).replace('"NOW-1MIN"', "Date.now()-60000"))


def finished_task(tid):
    """Finished work waiting in review, not blocked and not auto-mergeable (no worktree)."""
    return put("tasks", tid, plain(tid, "hsp1", "review", finishedAt=1791300000000, run={"chunkId": "chx-" + tid, "session": "sess-" + tid, "wt": "", "files": 3, "agentStatus": "done"}))


def test_a_decision_blocker_is_decided_logged_and_the_same_session_resumes(harness):
    s = Steps(*BOOT, ENABLE, STUB, ANSWER(DECIDE), lead_task("l1"), until("task('l1')"))
    s.ev("inbox_before", "inboxItems().filter(i => i.t && i.t.id === 'l1').length")
    s.add(RUN_POLICIES, RUN_POLICIES)
    s.ev("calls", "window.__leadCalls")
    s.ev("task", "(t => ({ status: t.status, key: t.blockerKey || null, note: t.leadNote || null }))(task('l1'))")
    s.ev("resume", "window.__rp.filter(x => x[0] === 'l1').map(x => x[1])")
    s.ev("launches", "window.__launches.length")
    s.ev("decisions", "(proj('hp1').decisions || []).map(d => ({ by: d.by, answer: d.answer, q: d.question, conf: d.confidence, tasks: d.tasks, piId: d.piId }))")
    s.ev("vault", "window.__exports.map(x => JSON.parse(x).markdown).join('\\n')")
    s.ev("action", "S.actions.filter(a => a.kind === 'lead').map(a => a.detail)")
    s.ev("inbox_after", "inboxItems().filter(i => i.t && i.t.id === 'l1').length")
    s.ev("prompt", "agentPrompt(task('l1')).includes('Which test runner should the new module use? → pytest')")
    out = s.run(harness)
    assert out["inbox_before"] == 0  # the lead is handling it: no card while it works
    assert out["calls"] == 1  # once for the one blocker, not on every policy pass
    assert out["task"]["status"] == "running" and out["task"]["key"] is None and out["task"]["note"] is None
    assert len(out["resume"]) == 1 and out["resume"][0].startswith("Decision (made by the AI lead, Austin can overturn it): Which test runner should the new module use? → pytest")
    assert out["launches"] == 1
    assert out["decisions"] == [{"by": "ai-lead", "answer": "pytest", "q": DECISION["detail"], "conf": 0.9, "tasks": ["l1"], "piId": "hpi1"}]
    assert "Which test runner" in out["vault"] and "ai-derived" in out["vault"] and "AI lead" in out["vault"]
    assert len(out["action"]) == 1 and "decided" in out["action"][0]
    assert out["inbox_after"] == 0 and out["prompt"] is True


def test_an_escalated_decision_goes_to_the_inbox_with_a_note_and_nothing_is_recorded(harness):
    declined = {**DECIDE, "escalate": True, "categories": ["money"], "reason": "it mentions money"}
    s = Steps(*BOOT, ENABLE, STUB, ANSWER(declined), lead_task("l2"), until("task('l2')"))
    s.add(RUN_POLICIES, RUN_POLICIES)
    s.ev("calls", "window.__leadCalls")
    s.ev("task", "(t => ({ status: t.status, note: t.leadNote && t.leadNote.why }))(task('l2'))")
    s.ev("card", "inboxItems().filter(i => i.t && i.t.id === 'l2').map(i => [i.kind, i.why])")
    s.ev("launches", "window.__launches.length")
    s.ev("decisions", "(proj('hp1').decisions || []).length")
    s.ev("action", "S.actions.filter(a => a.kind === 'lead').map(a => a.declined)")
    s.ev("shown", "(S.view = 'inbox', S.overlay = null, render(), document.body.textContent.includes('The AI lead declined this decision'))")
    out = s.run(harness)
    assert out["calls"] == 1
    assert out["task"] == {"status": "review", "note": "it mentions money"}
    assert out["card"] == [["blocked", "The AI lead declined this decision and left it for you: it mentions money."]]
    assert out["launches"] == 0 and out["decisions"] == 0 and out["action"] == [True] and out["shown"] is True


def test_an_unreachable_lead_and_the_off_switch_leave_the_card_for_austin(harness):
    s = Steps(*BOOT, STUB, ANSWER(None), DISABLE, lead_task("l3"), until("task('l3')"))
    s.add(RUN_POLICIES)
    s.ev("off_calls", "window.__leadCalls")
    s.ev("off_card", "inboxItems().filter(i => i.t && i.t.id === 'l3').length")
    s.add({"eval": "(S.settings.aiLead = true, 1)"}, RUN_POLICIES, RUN_POLICIES)
    s.ev("calls", "window.__leadCalls")
    s.ev("status", "[task('l3').status, task('l3').leadNote && task('l3').leadNote.why]")
    out = s.run(harness)
    assert out["off_calls"] == 0 and out["off_card"] == 1
    assert out["calls"] == 1 and out["status"] == ["review", "the AI lead couldn't be reached"]  # a lead that can't answer escalates


def test_overturn_before_merge_resumes_the_same_session_with_the_correction(harness):
    entry = {"id": "lead-o1", "question": DECISION["detail"], "answer": "pytest", "why": "rules", "by": "ai-lead", "confidence": 0.9, "piId": "hpi1", "at": 1791300000000, "tasks": ["o1"]}
    s = Steps(*BOOT, STUB, patch("projects", "hp1", {"decisions": [entry]}), finished_task("o1"), until("task('o1')"))
    s.add({"eval": "(S.view = 'journal', S.overlay = null, render(), 1)"}, {"waitFor": "[data-act=lead-overturn]"},
          {"click": "[data-act=lead-overturn]"}, {"waitFor": "#ov-text"}, {"type": {"selector": "#ov-text", "text": "unittest, we avoid new dependencies"}},
          {"click": "[data-act=lead-overturn-save]"}, {"eval": "new Promise(r => setTimeout(r, 1200))"})
    s.ev("decisions", "(proj('hp1').decisions || []).map(d => ({ id: d.id, by: d.by, answer: d.answer, supersedes: d.supersedes || null, supersededBy: d.supersededBy || null }))")
    s.ev("resume", "window.__rp.filter(x => x[0] === 'o1').map(x => x[1])")
    s.ev("status", "[task('o1').status, window.__launches.length]")
    s.ev("prompt", "agentPrompt(task('o1'))")
    s.ev("vault", "window.__exports.map(x => JSON.parse(x).markdown).join('\\n')")
    s.ev("journal", "(S.view = 'journal', S.overlay = null, render(), document.body.textContent.includes('No AI-lead decisions yet'))")
    out = s.run(harness)
    new = [d for d in out["decisions"] if d["by"] == "austin"]
    assert len(new) == 1 and new[0]["answer"] == "unittest, we avoid new dependencies" and new[0]["supersedes"] == "lead-o1"
    assert [d for d in out["decisions"] if d["id"] == "lead-o1"][0]["supersededBy"] == new[0]["id"]
    assert len(out["resume"]) == 1 and out["resume"][0].startswith("Correction from Austin") and "unittest, we avoid new dependencies" in out["resume"][0]
    assert out["status"] == ["running", 1]
    assert "unittest, we avoid new dependencies" in out["prompt"] and "→ pytest" not in out["prompt"]  # the agent now sees only the correction
    assert "confirmed-with-austin" in out["vault"] and out["journal"] is True


def test_overturn_after_merge_files_a_follow_up_task(harness):
    entry = {"id": "lead-m1", "question": DECISION["detail"], "answer": "pytest", "why": "rules", "by": "ai-lead", "confidence": 0.8, "piId": "hpi1", "at": 1791300000000, "tasks": ["m1"]}
    s = Steps(*BOOT, STUB, patch("projects", "hp1", {"decisions": [entry]}), put("tasks", "m1", plain("m1", "hsp1", "done", doneAt=1791300000000, run={"chunkId": "chx-m1", "session": "s"})), until("task('m1')"))
    s.add({"eval": "(S.overlay = { kind: 'task', id: 'm1' }, render(), 1)"}, {"waitFor": "[data-lead-editor] [data-act=lead-overturn]"},
          {"click": "[data-lead-editor] [data-act=lead-overturn]"}, {"waitFor": "#ov-text"}, {"type": {"selector": "#ov-text", "text": "Use unittest"}},
          {"click": "[data-act=lead-overturn-save]"}, {"eval": "new Promise(r => setTimeout(r, 1200))"})
    s.ev("follow", "S.tasks.filter(t => t.followupOf === 'm1').map(t => ({ title: t.title, status: t.status, detail: t.detail, deps: t.deps }))")
    s.ev("m1", "[task('m1').status, window.__launches.length, window.__rp.length]")
    s.ev("decisions", "(proj('hp1').decisions || []).map(d => [d.by, d.tasks.length, !!d.supersededBy])")
    s.ev("prompt", "agentPrompt(S.tasks.find(t => t.followupOf === 'm1')).includes('Use unittest')")
    out = s.run(harness)
    [f] = out["follow"]
    assert f["status"] == "todo" and f["title"].startswith("Apply the corrected decision") and "Use unittest" in f["detail"] and "pytest" in f["detail"] and f["deps"] == []
    assert out["m1"] == ["done", 0, 0]  # a merged task is left alone; nothing is relaunched
    assert out["decisions"] == [["ai-lead", 1, True], ["austin", 2, False]] and out["prompt"] is True


def test_settings_shows_the_line_the_spend_and_the_switch(harness):
    s = Steps(*BOOT, {"eval": "(S.overlay = { kind: 'settings' }, render(), refreshLeadInfo().then(() => new Promise(r => setTimeout(r, 300))))"})
    s.ev("cats", "[...document.querySelectorAll('[data-lead-cat]')].map(e => [e.dataset.leadCat, e.textContent])")
    s.ev("spend", "document.querySelector('[data-lead-spend]').textContent")
    s.ev("sel", "document.querySelector('#s-ailead').value")
    out = s.run(harness)
    assert [c[0] for c in out["cats"]] == ["money", "client", "external", "brand", "irreversible"]
    assert "pric" in out["cats"][0][1] and "delet" in out["cats"][4][1]
    assert out["spend"].startswith("$0.00 across 0 calls") and out["sel"] == "true"  # on by default; the switch turns it off


def test_on_by_default_the_lead_decides_with_no_setup(harness):
    s = Steps(*BOOT, STUB, ANSWER(DECIDE), lead_task("d1"), until("task('d1')"))
    s.add(RUN_POLICIES, RUN_POLICIES)
    s.ev("out", "[window.__leadCalls, task('d1').status, window.__launches.length, inboxItems().filter(i => i.t && i.t.id === 'd1').length, S.settings.aiLead]")
    assert s.run(harness)["out"] == [1, "running", 1, 0, True]


def test_switched_off_the_lead_never_runs(harness):
    s = Steps(*BOOT, STUB, ANSWER(DECIDE), DISABLE, lead_task("d2"), until("task('d2')"))
    s.add(RUN_POLICIES, RUN_POLICIES)
    s.ev("out", "[window.__leadCalls, task('d2').status, window.__launches.length, inboxItems().filter(i => i.t && i.t.id === 'd2').length, S.settings.aiLead]")
    assert s.run(harness)["out"] == [0, "review", 0, 1, False]
