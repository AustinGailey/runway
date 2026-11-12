"""This-or-that duels (PI 7 Sprint 1): the Duel overlay opens from Decide mode, picks by click/arrow/tie/skip
write a duel row and update both ideas' ratings, Esc stops, and the Scoring (weights) menu is gone. Driven through
the harness (harness/serve.py + drive.mjs) per CLAUDE.md's "Verifying the page end to end"."""
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


class Steps:
    """Builds a drive.mjs step list while tracking each step's index, so marks never drift when steps are added."""
    def __init__(self):
        self.steps = []

    def add(self, step):
        self.steps.append(step)
        return len(self.steps) - 1  # the index drive.mjs will report this step at

    def goto(self, url): return self.add({"goto": url})
    def waitForText(self, text, timeout=10000): return self.add({"waitForText": text, "timeout": timeout})
    def waitFor(self, sel, timeout=5000): return self.add({"waitFor": sel, "timeout": timeout})
    def click(self, sel): return self.add({"click": sel})
    def key(self, k): return self.add({"key": k})
    def sleep(self, ms): return self.add({"sleep": ms})
    def eval(self, expr): return self.add({"eval": expr})


def drive(steps, timeout=90):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps), capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return lines


ENTER_DECIDE = "(S.ideaProject='hp1', S.view='ideas', S.ideaMode='decide', render(), 1)"
IDEA_STATE = ("(() => ({ hi1: { r: ideaRating(S.ideas.find(x=>x.id==='hi1')), d: ideaDuelCount(S.ideas.find(x=>x.id==='hi1')) }, "
              "hi2: { r: ideaRating(S.ideas.find(x=>x.id==='hi2')), d: ideaDuelCount(S.ideas.find(x=>x.id==='hi2')) }, duels: S.duels.length }))()")


def test_scoring_menu_is_gone_and_unranked_shows_under_3_duels(harness):
    s = Steps()
    s.goto(harness); s.waitForText("Harness project", 15000)
    s.eval(ENTER_DECIDE)
    s.waitForText("Harness idea A", 10000)
    no_menu = s.eval("document.querySelector('[data-m=weights]') ? 1 : 0")
    no_scoring_btn = s.eval("[...document.querySelectorAll('button,.btn')].some(b => b.textContent.trim() === 'Scoring') ? 1 : 0")
    unranked = s.eval("document.body.textContent.includes('unranked') ? 1 : 0")
    lines = drive(s.steps)
    assert lines[no_menu]["value"] == 0, "the Scoring menu trigger must be gone"
    assert lines[no_scoring_btn]["value"] == 0, "no button should say Scoring anymore"
    assert lines[unranked]["value"] == 1, "ideas with fewer than 3 duels must read 'unranked'"


def test_duel_overlay_click_arrow_tie_skip_and_escape(harness):
    s = Steps()
    s.goto(harness); s.waitForText("Harness project", 15000)
    s.eval(ENTER_DECIDE)
    s.waitForText("Harness idea A", 10000)
    s.click("[data-act=open-duel][data-opp='']")
    s.waitFor(".duel-pair")
    sides = s.eval("(() => ({ a: S.overlay.a, b: S.overlay.b, kind: S.overlay.kind }))()")
    before = s.eval(IDEA_STATE)
    s.key("ArrowLeft"); s.sleep(150)
    after_arrow = s.eval(IDEA_STATE)
    s.key("="); s.sleep(150)
    tie_winner = s.eval("S.duels[S.duels.length-1].winner")
    after_tie = s.eval(IDEA_STATE)
    s.key("s"); s.sleep(150)
    skip_winner = s.eval("S.duels[S.duels.length-1].winner")
    after_skip = s.eval(IDEA_STATE)
    has_card = s.eval("document.querySelector('[data-act=duel-pick][data-side=a]') ? 1 : 0")
    s.click("[data-act=duel-pick][data-side=a]"); s.sleep(150)
    click_winner = s.eval("S.duels[S.duels.length-1].winner")
    s.key("Escape")
    overlay_after_escape = s.eval("S.overlay")

    lines = drive(s.steps)
    sides_v = lines[sides]["value"]
    assert sides_v["kind"] == "duel" and {sides_v["a"], sides_v["b"]} == {"hi1", "hi2"}
    before_v, after_arrow_v, after_tie_v, after_skip_v = lines[before]["value"], lines[after_arrow]["value"], lines[after_tie]["value"], lines[after_skip]["value"]
    assert after_arrow_v["duels"] == before_v["duels"] + 1, "ArrowLeft must record a duel"
    assert after_arrow_v["hi1"]["d"] + after_arrow_v["hi2"]["d"] > before_v["hi1"]["d"] + before_v["hi2"]["d"], "a pick must update both ideas' duel counts"
    assert lines[tie_winner]["value"] == "tie", "= must record a tie"
    assert after_tie_v["duels"] == before_v["duels"] + 2
    assert lines[skip_winner]["value"] == "skip", "s must record a skip"
    assert after_skip_v["hi1"] == after_tie_v["hi1"] and after_skip_v["hi2"] == after_tie_v["hi2"], "a skip must not change ratings or duel counts"
    assert lines[has_card]["value"] == 1, "duel cards must be clickable"
    assert lines[click_winner]["value"] == "a", "clicking the left card must record side a as the winner"
    assert lines[overlay_after_escape]["value"] is None, "Escape must close the duel overlay"


def test_duel_scoped_to_an_opportunity_only_offers_its_own_ideas(harness):
    s = Steps()
    s.goto(harness); s.waitForText("Harness project", 15000)
    s.eval("put('opportunities', 'dop1', { title: 'Scoped opportunity', note: '', projectId: 'hp1', order: 1, createdAt: Date.now() })"
           ".then(() => patch('ideas', 'hi1', { opportunityId: 'dop1' }))")
    s.sleep(300)
    s.eval(ENTER_DECIDE)
    s.waitForText("Harness idea A", 10000)
    disabled_with_one = s.eval("document.querySelector('[data-act=open-duel][data-opp=dop1]').disabled ? 1 : 0")
    s.eval("patch('ideas', 'hi2', { opportunityId: 'dop1' }).then(() => 1)")
    s.sleep(300)
    enabled_with_two = s.eval("document.querySelector('[data-act=open-duel][data-opp=dop1]').disabled ? 1 : 0")
    s.click("[data-act=open-duel][data-opp=dop1]")
    s.waitFor(".duel-pair")
    pair = s.eval("(() => ({ a: S.overlay.a, b: S.overlay.b }))()")

    lines = drive(s.steps)
    assert lines[disabled_with_one]["value"] == 1, "a duel scoped to an opportunity with only 1 idea must stay disabled"
    assert lines[enabled_with_two]["value"] == 0, "it must enable once a second idea joins the opportunity"
    pair_v = lines[pair]["value"]
    assert {pair_v["a"], pair_v["b"]} == {"hi1", "hi2"}, "the scoped duel must only ever pair ideas from that opportunity"
