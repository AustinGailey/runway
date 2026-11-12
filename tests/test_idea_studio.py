"""Idea studio data model (PI 7 groundwork): the page's own eloUpdate, nextDuel and recomputeRatings, run under node
against stored idea/duel lists (see tests/test_requirements.py for the pattern)."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is needed to run the page's JavaScript")


def page_source():
    """eloUpdate, recomputeRatings and nextDuel, exactly as the page has them."""
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    start = src.index("const ideaRating = ")
    end = src.index("const ideaInScope = ")
    return src[start:end]


def run_js(body):
    script = page_source() + "\n" + body
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_elo_update_moves_winner_up_and_loser_down():
    result = run_js('console.log(JSON.stringify([eloUpdate(1000, 1000, 1, 32), eloUpdate(1000, 1000, 0, 32)]));')
    assert result[0] > 1000 > result[1]
    assert round(result[0] - 1000, 3) == round(1000 - result[1], 3)


def test_30_consistent_duels_order_6_ideas_correctly():
    body = """
    const ids = ["i1","i2","i3","i4","i5","i6"]; // rank order: i1 best .. i6 worst
    const ideas = ids.map(id => ({ id }));
    const pairs = [];
    for (let i = 0; i < 6; i++) for (let j = i + 1; j < 6; j++) pairs.push([i, j]);
    let duels = [], at = 1;
    for (let rep = 0; rep < 2; rep++) for (const [i, j] of pairs) duels.push({ a: ids[i], b: ids[j], winner: "a", at: at++ }); // 30 duels, higher rank always wins
    const out = recomputeRatings(ideas, duels);
    console.log(JSON.stringify({
      order: [...out].sort((a, b) => b.rating - a.rating).map(o => o.id),
      duelCounts: out.map(o => o.duels),
    }));
    """
    result = run_js(body)
    assert result["order"] == ["i1", "i2", "i3", "i4", "i5", "i6"]
    assert result["duelCounts"] == [10, 10, 10, 10, 10, 10]


def test_skips_change_nothing():
    body = """
    const ideas = [{ id:"a" }, { id:"b" }];
    const withoutSkip = recomputeRatings(ideas, []);
    const withSkip = recomputeRatings(ideas, [{ a:"a", b:"b", winner:"skip", at:1 }]);
    console.log(JSON.stringify({ withoutSkip, withSkip }));
    """
    result = run_js(body)
    assert result["withSkip"] == result["withoutSkip"]
    a = next(i for i in result["withSkip"] if i["id"] == "a")
    assert a["rating"] == 1000 and a["duels"] == 0 and a["lastDueledAt"] is None


def test_ties_move_ratings_toward_each_other():
    # eloUpdate is single-sided (the new rating for a); a tie (result 0.5) pulls the higher side down and the lower side up.
    result = run_js('console.log(JSON.stringify([eloUpdate(1200, 1000, 0.5, 32), eloUpdate(1000, 1200, 0.5, 32)]));')
    hi, lo = result
    assert hi < 1200 and lo > 1000
    assert abs(hi - lo) < 200


def test_recomputed_ties_leave_an_already_even_pair_even():
    body = """
    const ideas = [{ id:"a" }, { id:"b" }];
    const out = recomputeRatings(ideas, [{ a:"a", b:"b", winner:"tie", at:1 }]);
    console.log(JSON.stringify(out.map(o => o.rating)));
    """
    assert run_js(body) == [1000, 1000]


def test_next_duel_never_repeats_the_last_pair():
    body = """
    const ideas = [{ id:"a", rating:1000, duels:0 }, { id:"b", rating:1005, duels:0 }, { id:"c", rating:1200, duels:3 }];
    const duels = [{ a:"a", b:"b", winner:"a", at:1 }];
    console.log(JSON.stringify(nextDuel(ideas, duels)));
    """
    result = run_js(body)
    assert {result["a"], result["b"]} == {"b", "c"}


def test_next_duel_prefers_ideas_sharing_an_opportunity():
    body = """
    const ideas = [
      { id:"a", rating:1000, duels:0, opportunityId:"o1" },
      { id:"b", rating:1000, duels:0, opportunityId:"o1" },
      { id:"c", rating:1001, duels:0, opportunityId:"o2" },
    ];
    console.log(JSON.stringify(nextDuel(ideas, [])));
    """
    result = run_js(body)
    assert {result["a"], result["b"]} == {"a", "b"}


def test_next_duel_prefers_least_dueled_ideas():
    body = """
    const ideas = [
      { id:"a", rating:1000, duels:0 },
      { id:"b", rating:1500, duels:0 },
      { id:"c", rating:1010, duels:20 },
      { id:"d", rating:1495, duels:20 },
    ];
    console.log(JSON.stringify(nextDuel(ideas, [])));
    """
    result = run_js(body)
    assert {result["a"], result["b"]} == {"a", "b"}


def test_next_duel_is_null_with_fewer_than_two_ideas():
    assert run_js('console.log(JSON.stringify(nextDuel([{ id:"a" }], [])));') is None
