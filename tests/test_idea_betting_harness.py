"""Betting table and idea fading (PI 7 Sprint 3), end to end through the harness per CLAUDE.md's "Verifying the page
end to end": the table lists each opportunity's top duel winners that carry an appetite, its budget bar fills as
bets are placed, Bet requires a why-now and an expected signal with a date before it promotes through the existing
flow, an idea not bet on at two tables (or untouched long enough) fades but is never deleted, and Revive is one
click. The weekly Inbox item stays off until Settings turns it on."""
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


# A plan with one PI so acceptPlan() has somewhere to land the bet. No render() here: that would wipe the bet
# form's typed-but-unsynced inputs before they're read.
STUB_PLAN = ("(S.sample = { json: async () => ({ summary: 'A small plan.', pis: [{ name: 'PI 2', objective: 'Follow up',"
             " sprints: [{ name: 'Sprint 1', goal: 'Start', tasks: [{ title: 'First task', detail: '', acceptance: '- x',"
             " estTokens: 100000, model: 'sonnet', kind: 'code' }] }] }] }) }, 1)")

SEED_TWO_IDEAS = (
    "Promise.all(["
    "put('opportunities', 'hbo1', { title: 'Betting opp', note: '', projectId: 'hp1', order: 1, createdAt: Date.now() }),"
    "put('ideas', 'hba', { title: 'Bettable idea A', kind: 'build', status: 'new', context: '', source: 'manual',"
    " projectId: 'hp1', opportunityId: 'hbo1', appetite: 'sprint', rating: 1100, createdAt: Date.now() }),"
    "put('ideas', 'hbb', { title: 'Bettable idea B', kind: 'build', status: 'new', context: '', source: 'manual',"
    " projectId: 'hp1', opportunityId: 'hbo1', appetite: 'test', rating: 900, createdAt: Date.now() }),"
    "put('ideas', 'hbc', { title: 'No appetite yet', kind: 'build', status: 'new', context: '', source: 'manual',"
    " projectId: 'hp1', opportunityId: 'hbo1', rating: 1200, createdAt: Date.now() })"
    "]).then(()=>1)"
)


def test_table_lists_top_winners_with_appetite_and_budget_bar_fills_with_each_bet(harness, tmp_path):
    png = str(tmp_path / "betting.png")
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},
        {"eval": SEED_TWO_IDEAS},
        {"eval": "(S.ideaProject='hp1', S.view='ideas', render(), 1)"},
        {"waitForText": "Harness idea A", "timeout": 10000},
        {"click": "[data-act=open-betting]"},
        {"waitForText": "Betting opp", "timeout": 5000},
        {"screenshot": png},
        # only the two appetite-bearing ideas show, best rating first, and "No appetite yet" is excluded
        {"eval": "Array.from(document.querySelectorAll('.bet-card .ct')).map(e=>e.textContent.replace('Fading','').trim())"},
        {"eval": "document.querySelector('#overlay').textContent.includes('No appetite yet')"},
        {"eval": "[S.betting.scope, S.betting.bets.length]"},  # a fresh session
        # place a bet on idea A
        {"click": "[data-act=bet-open][data-id=hba]"},
        {"waitFor": "#bet-why", "timeout": 5000},
        {"type": {"selector": "#bet-why", "text": "Painters keep asking for this."}},
        {"type": {"selector": "#bet-signal", "text": "Three signed up in two weeks"}},
        {"type": {"selector": "#bet-by", "text": "2026-11-01"}},
        {"eval": STUB_PLAN},
        {"click": "[data-act=bet-confirm]"},
        {"waitForText": "Break it down", "timeout": 10000},
        {"eval": "[S.betting.bets.length, S.betting.bets[0].id, S.pendingIdea.id, S.pendingIdea.signal]"},
        {"click": "[data-act=run-plan]"},
        {"waitForText": "Add to board", "timeout": 15000},
        {"click": "[data-act=accept-plan]"},
        {"eval": "new Promise(r=>{const t0=Date.now();(function w(){const i=S.ideas.find(x=>x.id==='hba'); if(i.status==='promoted'||Date.now()-t0>10000) return r([i.status, i.decision?.reason, i.decision?.signal, !!i.decision?.signalBy]); setTimeout(w,200);})()})"},
        # reopening the table must show the same session, with the bet's budget already counted
        {"eval": "(S.view='ideas', S.ideaProject='hp1', openBettingTable(), 1)"},
        {"waitForText": "Betting opp", "timeout": 5000},
        {"eval": "S.betting.bets.map(b=>b.id)"},
    ]
    v = drive(harness, steps)
    assert v[8] == ["Bettable idea A", "Bettable idea B"], "best rating first, and both carry an appetite"
    assert v[9] is False, "an idea with no appetite never reaches the table"
    assert v[10] == ["hp1", 0]
    assert v[19][0] == 1 and v[19][1] == "hba", "the bet is counted the moment it's confirmed, before planning finishes"
    assert v[19][2] == "hba" and v[19][3] == "Three signed up in two weeks"
    assert v[23][0] == "promoted"
    assert v[23][1] == "Painters keep asking for this."
    assert v[23][2] == "Three signed up in two weeks"
    assert v[23][3] is True
    assert v[26] == ["hba"], "reopening the table resumes the same sitting, budget already counted"


def test_bet_requires_why_now_signal_and_date(harness):
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},
        {"eval": SEED_TWO_IDEAS},
        {"eval": "(S.ideaProject='hp1', S.view='ideas', S.overlay=null, openBettingTable(), 1)"},
        {"waitForText": "Betting opp", "timeout": 5000},
        {"click": "[data-act=bet-open][data-id=hba]"},
        {"waitFor": "#bet-why", "timeout": 5000},
        {"click": "[data-act=bet-confirm]"},
        {"waitForText": "Give a why-now", "timeout": 5000},
        {"eval": "[S.overlay.kind, S.betting.bets.length]"},  # still on the table, nothing counted
    ]
    v = drive(harness, steps)
    assert v[9] == ["betting", 0]


def test_fading_follows_the_two_tables_or_weeks_rule_never_deletes_and_revive_works(harness):
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},
        {"eval": SEED_TWO_IDEAS},
        {"eval": "(S.ideaProject='hp1', openBettingTable(), 1)"},
        {"eval": "ideaFading(S.ideas.find(x=>x.id==='hba'))"},  # not fading yet
        {"eval": "closeBettingRound().then(()=>1)"},  # table 1: neither idea bet on
        {"eval": "[S.ideas.find(x=>x.id==='hba').tableMisses, S.ideas.find(x=>x.id==='hba').status, ideaFading(S.ideas.find(x=>x.id==='hba'))]"},
        {"eval": "(openBettingTable(), closeBettingRound().then(()=>1))"},  # table 2: still untouched
        {"eval": "[S.ideas.find(x=>x.id==='hba').tableMisses, S.ideas.find(x=>x.id==='hba').status, ideaFading(S.ideas.find(x=>x.id==='hba'))]"},  # two misses, now fading but still open
        {"eval": "(openBettingTable(), closeBettingRound().then(()=>1))"},  # table 3: still untouched while fading -> moves to Faded
        {"eval": "S.ideas.find(x=>x.id==='hba').status"},
        {"eval": "S.ideas.some(x=>x.id==='hba')"},  # never deleted
        {"eval": "S.bettingRounds.length >= 3"},  # the table records itself each time
        # revive, one click
        {"eval": "(S.view='ideas', S.ideaView='list', S.ideaProject='hp1', render(), 1)"},
        {"waitForText": "Faded", "timeout": 10000},
        {"click": "[data-act=reopen-idea][data-id=hba]"},
        {"eval": "new Promise(r=>setTimeout(()=>{const i=S.ideas.find(x=>x.id==='hba'); r([i.status, i.tableMisses, i.fadedAt]);},300))"},
    ]
    v = drive(harness, steps)
    assert v[4] is False
    assert v[6] == [1, "new", False], "one miss: still open, not fading yet"
    assert v[8] == [2, "new", True], "two misses: fading, but not moved to Faded until another table"
    assert v[10] == "faded", "a third table with no action moves it to Faded"
    assert v[11] is True, "fading never deletes the idea"
    assert v[12] is True
    assert v[16] == ["new", 0, None], "one click revives it and resets the fade clock"


def test_idea_untouched_for_the_fade_weeks_setting_fades_even_with_one_miss(harness):
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},
        {"eval": SEED_TWO_IDEAS},
        # back-date the idea by 5 weeks, past the default 4-week fade setting, without going through patch() (which
        # would stamp updatedAt to now and defeat the backdating). The seeding put() left an optimistic cache entry
        # that would otherwise keep echoing its own (recent) updatedAt over this older one.
        {"eval": "(optimistic.delete('ideas/hbb'), 1)"},
        {"eval": "S.db.collection('ideas').doc('hbb').update({ updatedAt: Date.now() - 5*7*86400000 }).then(()=>1)"},
        {"eval": "new Promise(r=>setTimeout(()=>r(ideaFading(S.ideas.find(x=>x.id==='hbb'))),300))"},  # mark 4
        {"eval": "(S.ideaProject='hp1', openBettingTable(), closeBettingRound().then(()=>1))"},
        {"eval": "S.ideas.find(x=>x.id==='hbb').status"},  # mark 6
    ]
    v = drive(harness, steps)
    assert v[5] is True, "untouched past the fade-weeks setting counts even with zero misses"
    assert v[7] == "faded", "already fading when the table closes, so it moves on this first close"


def test_weekly_betting_inbox_item_is_off_by_default(harness):
    # The seeded settings/main doc (harness/seed.json) never sets bettingOn, so this is DEFAULTS.bettingOn merged in.
    steps = [
        {"waitForText": "Harness project", "timeout": 15000},
        {"eval": SEED_TWO_IDEAS},
        {"eval": "(S.ideaProject='hp1', S.view='inbox', render(), 1)"},
        {"eval": "S.settings.bettingOn"},  # off unless Austin turns it on
        {"eval": "inboxItems().some(i=>i.kind==='betting')"},  # no item while off
        {"eval": "(S.settings.bettingOn = true, inboxItems().some(i=>i.kind==='betting'))"},  # appears once on
    ]
    v = drive(harness, steps)
    assert v[4] is False
    assert v[5] is False
    assert v[6] is True
