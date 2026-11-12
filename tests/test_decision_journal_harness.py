"""Decision journal (PI 7 Sprint 3), end to end through the harness per CLAUDE.md's "Verifying the page end to
end": every bet, decide, park and kill outcome is a journal entry carrying its why, appetite, duel rank and
Claude's pick at the time; a bet gets a check-in once its tasks are all done or its signal date passes, and
answering it freezes the actual cost read from the run rows; a calibration line appears once 5 bets are answered;
and answered bets feed a short note into the explore prompt for new ideas in the same project.

v[i] lines up with the i-th entry of the combined steps list drive() actually sends, which is [{"goto": url}] +
steps — so v[0] is always the goto and v[i] for i>=1 is steps[i-1]."""
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


def drive(url, steps, timeout=90):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps([{"goto": url}] + steps),
                       capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return [l.get("value") for l in lines]


SEED_FOUR_ENTRIES = (
    "Promise.all(["
    "put('pis', 'jpi', { projectId: 'hp1', name: 'Bet PI', objective: 'Test the bet', order: 2, createdAt: Date.now() }),"
    "put('sprints', 'jsp', { projectId: 'hp1', piId: 'jpi', name: 'Sprint 1', goal: '', order: 1 }),"
    "put('tasks', 'jt1', { projectId: 'hp1', piId: 'jpi', sprintId: 'jsp', title: 'Bet task 1', estTokens: 50000,"
    " model: 'sonnet', kind: 'code', status: 'todo', order: 1, deps: [], createdAt: Date.now() }),"
    "put('tasks', 'jt2', { projectId: 'hp1', piId: 'jpi', sprintId: 'jsp', title: 'Bet task 2', estTokens: 50000,"
    " model: 'sonnet', kind: 'code', status: 'todo', order: 2, deps: [], createdAt: Date.now() }),"
    "put('ideas', 'jbet', { title: 'Journal bet', kind: 'build', status: 'promoted', context: '', source: 'manual',"
    " projectId: 'hp1', createdAt: 2000, decision: { outcome: 'promoted', choice: 'Smallest test', reason: 'Painters asked twice.',"
    " at: 4000, projectId: 'hp1', piId: 'jpi', signal: 'Three signups', signalBy: Date.now() + 30*86400000,"
    " appetite: 'test', rank: 2, pick: { recommend: 'Smallest test', chosen: 'Solid v1', followed: false } } }),"
    "put('ideas', 'jdec', { title: 'Journal decision', kind: 'decision', status: 'decided', context: '', source: 'manual',"
    " projectId: 'hp1', createdAt: 2000, decision: { outcome: 'decided', choice: 'Option B', reason: 'Because of cost.',"
    " at: 3000, appetite: null, rank: null, pick: null } }),"
    "put('ideas', 'jpark', { title: 'Journal park', kind: 'build', status: 'parked', context: '', source: 'manual',"
    " projectId: 'hp1', createdAt: 2000, decision: { outcome: 'parked', choice: '', reason: 'Not now.', at: 2000,"
    " revisitAt: Date.now() + 86400000, appetite: 'sprint', rank: 5, pick: null } }),"
    "put('ideas', 'jkill', { title: 'Journal kill', kind: 'build', status: 'killed', context: '', source: 'manual',"
    " projectId: 'hp1', createdAt: 2000, decision: { outcome: 'killed', choice: '', reason: 'Too risky.', at: 5000,"
    " appetite: 'pi', rank: 1, pick: { recommend: 'Ambitious', chosen: 'Ambitious', followed: true } } })"
    "]).then(()=>1)"
)

GO_JOURNAL = "(S.view='journal', S.ideaProject=null, render(), 1)"


def test_entries_carry_why_appetite_rank_and_pick_and_only_the_undone_bet_gets_a_checkin(harness, tmp_path):
    png = str(tmp_path / "journal.png")
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},               # v[1]
        {"eval": SEED_FOUR_ENTRIES},                                         # v[2]
        {"eval": GO_JOURNAL},                                                 # v[3]
        {"waitForText": "Journal kill", "timeout": 10000},                   # v[4]
        {"screenshot": png},                                                  # v[5]
        {"eval": "Array.from(document.querySelectorAll('.journal-entry .ct')).map(e=>e.textContent.trim())"},  # v[6]
        {"eval": "Array.from(document.querySelectorAll('.journal-entry')).map(e=>e.textContent)"},             # v[7]
        {"eval": "!!document.getElementById('ci-note-jbet')"},  # the bet's tasks aren't done and its signal date is in the future, v[8]
    ]
    v = drive(harness, steps)
    assert os.path.exists(png)
    titles = v[6]
    assert titles == ["Journal kill Killed", "Journal bet Bet", "Journal decision Decided", "Journal park Parked"], "newest decision.at first"
    bodies = v[7]
    bet_body = next(b for b in bodies if "Journal bet" in b)
    assert "Painters asked twice." in bet_body
    assert "Quick test" in bet_body and "duel rank #2" in bet_body
    assert "picked" in bet_body and "Solid v1" in bet_body and "Smallest test" in bet_body, "Claude's pick wasn't followed, so the override is named"
    assert "Three signups" in bet_body
    kill_body = next(b for b in bodies if "Journal kill" in b)
    assert "Too risky." in kill_body and "PI" in kill_body and "duel rank #1" in kill_body and "followed Claude's pick" in kill_body
    park_body = next(b for b in bodies if "Journal park" in b)
    assert "Not now." in park_body and "Sprint" in park_body and "duel rank #5" in park_body
    dec_body = next(b for b in bodies if "Journal decision" in b)
    assert "Because of cost." in dec_body and "No appetite set" in dec_body and "unranked" in dec_body
    assert v[8] is False


def test_checkin_appears_once_tasks_are_done_and_answering_freezes_actual_cost_from_run_rows(harness, tmp_path):
    png = str(tmp_path / "checkin.png")
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},  # v[1]
        {"eval": SEED_FOUR_ENTRIES},  # v[2]
        # finish the bet's two tasks with real actuals, and record three run rows behind them (Run health's own shape)
        {"eval": "Promise.all(["
                  "patch('tasks', 'jt1', { status: 'done', actualTokens: 40000, verifyTokens: 2000, costUsd: 0.5 }),"
                  "patch('tasks', 'jt2', { status: 'done', actualTokens: 60000, costUsd: 0.7 }),"
                  "put('estimates', 'je1', { taskId: 'jt1', projectId: 'hp1', category: 'ok', costUsd: 0.3, at: Date.now(), usable: true }),"
                  "put('estimates', 'je2', { taskId: 'jt1', projectId: 'hp1', category: 'verify-fail', costUsd: 0.2, at: Date.now(), usable: true }),"
                  "put('estimates', 'je3', { taskId: 'jt2', projectId: 'hp1', category: 'ok', costUsd: 0.7, at: Date.now(), usable: true })"
                  "]).then(()=>1)"},  # v[3]
        {"eval": GO_JOURNAL},  # v[4]
        {"waitFor": "#ci-note-jbet", "timeout": 10000},  # now shows: both tasks done, v[5]
        {"eval": "document.getElementById('ci-note-jbet').closest('.journal-entry').textContent"},  # v[6]
        {"screenshot": png},  # v[7]
        {"type": {"selector": "#ci-note-jbet", "text": "Three signed up within a week."}},  # v[8]
        {"type": {"selector": "#ci-hrs-jbet", "text": "2.5"}},  # v[9]
        {"click": "[data-act=checkin][data-v=yes][data-id=jbet]"},  # v[10]
        {"eval": "new Promise(r=>{const t0=Date.now();(function w(){const i=S.ideas.find(x=>x.id==='jbet'); if(i.decision.checkIn || Date.now()-t0>8000) return r(i.decision.checkIn); setTimeout(w,150);})();})"},  # v[11]
    ]
    v = drive(harness, steps)
    assert "102k tok" in v[6], "shown actual so far before answering"
    assert os.path.exists(png)
    ci = v[11]
    assert ci["verdict"] == "yes"
    assert ci["note"] == "Three signed up within a week."
    assert ci["hours"] == 2.5
    assert ci["actual"]["tokens"] == 102000  # 40000 + 2000 + 60000
    assert round(ci["actual"]["cost"], 2) == 1.2  # 0.5 + 0.7 (the tasks' own costUsd, not the run rows')
    assert ci["actual"]["runs"] == 3
    assert ci["actual"]["tasks"] == 2
    assert ci["actual"]["cats"] == {"ok": 2, "verify-fail": 1}


def test_signal_date_passing_triggers_the_checkin_even_with_open_tasks(harness):
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},  # v[1]
        {"eval": "Promise.all(["
                  "put('pis', 'kpi', { projectId: 'hp1', name: 'Bet PI', objective: '', order: 3, createdAt: Date.now() }),"
                  "put('tasks', 'kt1', { projectId: 'hp1', piId: 'kpi', sprintId: 'hsp1', title: 'Still open', estTokens: 50000,"
                  " model: 'sonnet', kind: 'code', status: 'todo', order: 1, deps: [], createdAt: Date.now() }),"
                  "put('ideas', 'kbet', { title: 'Overdue bet', kind: 'build', status: 'promoted', context: '', source: 'manual',"
                  " projectId: 'hp1', createdAt: 2000, decision: { outcome: 'promoted', choice: '', reason: 'why', at: 2000,"
                  " projectId: 'hp1', piId: 'kpi', signal: 'sig', signalBy: Date.now() - 86400000, appetite: 'test', rank: null, pick: null } })"
                  "]).then(()=>1)"},  # v[2]
        {"eval": "(S.view='journal', S.ideaProject=null, render(), 1)"},  # v[3]
        {"waitFor": "#ci-note-kbet", "timeout": 10000},  # v[4]
        {"eval": "needsCheckIn(S.ideas.find(x=>x.id==='kbet'))"},  # v[5]
    ]
    v = drive(harness, steps)
    assert v[5] is True


SEED_FIVE_ANSWERED = (
    "Promise.all(["
    "put('ideas', 'c1', { title: 'c1', kind: 'build', status: 'promoted', context: '', source: 'manual', projectId: 'hp1', createdAt: 1,"
    " decision: { outcome: 'promoted', at: 1, appetite: 'test', rank: 1, pick: { recommend: 'a', chosen: 'a', followed: true },"
    " checkIn: { verdict: 'yes', note: '', at: 1 } } }),"
    "put('ideas', 'c2', { title: 'c2', kind: 'build', status: 'promoted', context: '', source: 'manual', projectId: 'hp1', createdAt: 1,"
    " decision: { outcome: 'promoted', at: 2, appetite: 'test', rank: 2, pick: { recommend: 'a', chosen: 'a', followed: true },"
    " checkIn: { verdict: 'yes', note: '', at: 2 } } }),"
    "put('ideas', 'c3', { title: 'c3', kind: 'build', status: 'promoted', context: '', source: 'manual', projectId: 'hp1', createdAt: 1,"
    " decision: { outcome: 'promoted', at: 3, appetite: 'sprint', rank: 5, pick: { recommend: 'a', chosen: 'b', followed: false },"
    " checkIn: { verdict: 'no', note: '', at: 3 } } }),"
    "put('ideas', 'c4', { title: 'c4', kind: 'build', status: 'promoted', context: '', source: 'manual', projectId: 'hp1', createdAt: 1,"
    " decision: { outcome: 'promoted', at: 4, appetite: 'sprint', rank: 6, pick: { recommend: 'a', chosen: 'b', followed: false },"
    " checkIn: { verdict: 'no', note: '', at: 4 } } }),"
    "put('ideas', 'c5', { title: 'c5', kind: 'build', status: 'promoted', context: '', source: 'manual', projectId: 'hp1', createdAt: 1,"
    " decision: { outcome: 'promoted', at: 5, appetite: 'pi', rank: 4, pick: { recommend: 'a', chosen: 'a', followed: true },"
    " checkIn: { verdict: 'partly', note: '', at: 5 } } })"
    "]).then(()=>1)"
)


def test_calibration_line_appears_at_five_answered_bets_and_matches_seeded_data(harness):
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},  # v[1]
        # with fewer than 5 answered, no calibration line
        {"eval": "Promise.all([put('ideas','c1',{title:'c1',kind:'build',status:'promoted',context:'',source:'manual',projectId:'hp1',createdAt:1,"
                  "decision:{outcome:'promoted',at:1,appetite:'test',rank:1,pick:{recommend:'a',chosen:'a',followed:true},checkIn:{verdict:'yes',note:'',at:1}}})]).then(()=>1)"},  # v[2]
        {"eval": "(S.view='journal', S.ideaProject=null, render(), 1)"},  # v[3]
        {"waitForText": "c1", "timeout": 10000},  # v[4]
        {"eval": "calibrationLine(null)"},  # v[5]
        {"eval": SEED_FIVE_ANSWERED},  # v[6]
        {"eval": "new Promise(r=>{const t0=Date.now();(function w(){ if (S.ideas.filter(i=>i.id[0]==='c').length>=5 || Date.now()-t0>8000) return r(calibrationLine(null)); setTimeout(w,150); })();})"},  # v[7]
    ]
    v = drive(harness, steps)
    assert v[5] == "", "fewer than 5 answered bets: no calibration line yet"
    line = v[7]
    assert "5 answered bets: 40% yes" in line
    assert "Quick test 100%" in line and "Sprint 0%" in line and "PI 0%" in line
    assert "top 3 duel rank 100% vs the rest 0%" in line
    assert "Claude's pick followed 67% vs overridden 0%" in line


def test_explore_prompt_includes_past_bets_note_when_answered_entries_exist_in_the_same_project(harness):
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},  # v[1]
        {"eval": "put('ideas', 'past1', { title: 'Past bet', kind: 'build', status: 'promoted', context: '', source: 'manual',"
                  " projectId: 'hp1', createdAt: 1, decision: { outcome: 'promoted', at: 1, signal: 'Three signups in two weeks',"
                  " appetite: 'test', rank: 1, pick: null, checkIn: { verdict: 'yes', note: 'Worked better than hoped.', at: 2 } } }).then(()=>1)"},  # v[2]
        {"eval": "(window.__prompt = null, S.sample = { json: async (p) => { window.__prompt = p; return { summary: 's', shapes: [{ name: 'A', summary: '',"
                  " estTokens: 1, humanHours: 0, daysToSignal: 1, impact: 3, confidence: 3, moves: '', risk: '', firstQuestion: '', appetiteFit: '' }],"
                  " recommend: 0, recommendWhy: '', killCase: '', premortem: ['a','b','c'], assumptions: [] }; } }, 1)"},  # v[3]
        {"eval": "exploreIdea('hi1').then(()=>1)"},  # v[4]
        {"eval": "new Promise(r=>{const t0=Date.now();(function w(){ if (window.__prompt || Date.now()-t0>8000) return r(window.__prompt); setTimeout(w,150); })();})"},  # v[5]
    ]
    v = drive(harness, steps)
    prompt = v[5]
    assert "WHAT YOUR PAST BETS SAY" in prompt
    assert "Three signups in two weeks" in prompt
    assert "Worked better than hoped." in prompt
    assert "100% of 1 answered" in prompt
