"""20 scripted duel picks over 5 ideas must produce the expected ranking, shown on the Decide-mode cards and used
to sort the List view (PI 7 Sprint 1). "Expected" comes from running the page's own nextDuel/eloUpdate in node
with the same fixed rule (lower id always wins) that the harness script uses, so this doesn't hardcode a result
that depends on tuning nextDuel's matchmaking — see tests/test_idea_studio.py for the same node-driving pattern."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get("RUNWAY_CHROME") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(not (NODE and os.path.exists(CHROME)), reason="needs node and Chrome")

IDS = ["rk1", "rk2", "rk3", "rk4", "rk5"]
TITLES = {d: f"Rank idea {d[-1]}" for d in IDS}


def page_source():
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    start = src.index("const ideaRating = ")
    end = src.index("const ideaInScope = ")
    return src[start:end]


def expected_order():
    """Replays the same 20 duels (lower id always wins) through the page's own functions in node, independent of
    the harness, to get the ranking the UI must match."""
    script = page_source() + f"""
    const ids = {json.dumps(IDS)};
    let ideas = ids.map(id => ({{ id, rating: 1000, duels: 0 }}));
    let duels = [], at = 1;
    for (let n = 0; n < 20; n++){{
      const pair = nextDuel(ideas, duels);
      const a = ideas.find(x => x.id === pair.a), b = ideas.find(x => x.id === pair.b);
      const winner = a.id < b.id ? "a" : "b";
      duels.push({{ a: a.id, b: b.id, winner, at: at++ }});
      const resultA = winner === "a" ? 1 : 0, ca = a.duels, cb = b.duels, ra0 = a.rating, rb0 = b.rating; // both updates use the pre-duel ratings, like recordDuel
      a.rating = eloUpdate(ra0, rb0, resultA, ca < 10 ? 32 : 16); a.duels = ca + 1;
      b.rating = eloUpdate(rb0, ra0, 1 - resultA, cb < 10 ? 32 : 16); b.duels = cb + 1;
    }}
    console.log(JSON.stringify({{
      order: [...ideas].sort((x, y) => y.rating - x.rating).map(x => x.id),
      duelCounts: Object.fromEntries(ideas.map(x => [x.id, x.duels])),
    }}));
    """
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


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
    """Builds a drive.mjs step list while tracking each step's index, so marks never drift when steps are added."""
    def __init__(self):
        self.steps = []

    def add(self, step):
        self.steps.append(step)
        return len(self.steps) - 1

    def goto(self, url): return self.add({"goto": url})
    def waitForText(self, text, timeout=10000): return self.add({"waitForText": text, "timeout": timeout})
    def waitFor(self, sel, timeout=5000): return self.add({"waitFor": sel, "timeout": timeout})
    def click(self, sel): return self.add({"click": sel})
    def eval(self, expr): return self.add({"eval": expr})


def test_20_scripted_picks_over_5_ideas_rank_cards_and_list_in_the_expected_order(harness):
    expected = expected_order()
    expected_titles = [TITLES[d] for d in expected["order"]]

    seed_ideas = ", ".join(f"put('ideas', '{d}', {{ title: '{TITLES[d]}', kind: 'build', status: 'new', context: '', source: 'manual', projectId: null, createdAt: Date.now() }})" for d in IDS)
    # Business-wide scope ('__none') holds only these 5 (the seeded harness ideas belong to the Harness project).
    # A tick between picks keeps each duel's "at" timestamp distinct (real clicks always take longer than that),
    # which is what nextDuel's "never repeat the last pair" rule relies on to find the most recent duel.
    run_duels = """(async () => {
      for (let n = 0; n < 20; n++){
        const o = S.overlay; const winner = o.a < o.b ? 'a' : 'b';
        await duelPick(winner);
        await new Promise(r => setTimeout(r, 5));
      }
      return 1;
    })()"""

    s = Steps()
    s.goto(harness); s.waitForText("Harness project", 15000)
    s.eval(f"Promise.all([{seed_ideas}]).then(() => 1)")
    s.eval("(S.ideaProject='__none', S.view='ideas', S.ideaMode='decide', render(), 1)")
    s.waitForText(TITLES["rk1"], 10000)
    s.click("[data-act=open-duel][data-opp='']")
    s.waitFor(".duel-pair")
    s.eval(run_duels)
    s.eval("(S.overlay = null, render(), 1)")
    card_titles_i = s.eval("JSON.stringify([...document.querySelectorAll('.idea-loose .ideacard')].map(c => c.querySelector('.ct').textContent.trim()))")
    rank_badges_i = s.eval("JSON.stringify([...document.querySelectorAll('.idea-loose .ideacard .num')].map(c => c.textContent.trim()))")
    card_count_i = s.eval("document.querySelectorAll('.idea-loose .ideacard').length")
    s.eval("(S.ideaView='list', render(), 1)")
    list_titles_i = s.eval("JSON.stringify([...document.querySelectorAll('.table tbody tr td:nth-child(2) b')].map(b => b.textContent.trim()))")
    list_ranks_i = s.eval("JSON.stringify([...document.querySelectorAll('.table tbody tr td:first-child')].map(td => td.textContent.trim()))")

    lines = drive(s.steps)
    card_titles = json.loads(lines[card_titles_i]["value"])
    rank_badges = json.loads(lines[rank_badges_i]["value"])
    list_titles = json.loads(lines[list_titles_i]["value"])
    list_ranks = json.loads(lines[list_ranks_i]["value"])

    assert lines[card_count_i]["value"] == 5, "all 5 scripted ideas must be loose (no opportunity) and visible"
    assert all(c >= 3 for c in expected["duelCounts"].values()), "the node replay should give every idea at least 3 duels"
    assert card_titles == expected_titles, "Decide-mode card order must match the engine's own replay of the same 20 picks"
    assert rank_badges == ["#1", "#2", "#3", "#4", "#5"], "every idea has 3+ duels, so all should show a numbered rank, not 'unranked'"
    assert list_titles == expected_titles, "the List view must sort by the same duel ranking"
    assert list_ranks == ["#1", "#2", "#3", "#4", "#5"]
