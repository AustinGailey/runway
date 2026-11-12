"""The check-and-fix loop: what the checker sees, how its verdict is read, and how rounds add up."""
import json, os, subprocess

import runway


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def make_worktree(tmp_path, tid, content, path="src/big.txt", chunk="c1"):
    """A scratch repo with one worktree on its own branch, the file already committed there with `content`, and
    the .meta file run.sh would have written: enough for verify_input to fetch the file straight from disk."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    os.makedirs(os.path.join(repo, os.path.dirname(path)), exist_ok=True)
    with open(os.path.join(repo, path), "w") as f:
        f.write("base\n")
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "base")
    wtp = os.path.join(repo, ".claude", "worktrees", "wt-" + tid)
    git(repo, "worktree", "add", "-q", "-b", "runway-task/" + tid, wtp, "main")
    with open(os.path.join(wtp, path), "w") as f:
        f.write(content)
    git(wtp, "add", "-A"); git(wtp, "commit", "-q", "-m", "agent change")
    d = run_dir(chunk)
    with open(os.path.join(d, tid + ".meta"), "w") as f:
        json.dump({"repo": repo, "wt": "wt-" + tid, "base": "main"}, f)
    return d


def run_dir(ch="c1"):
    d = os.path.join(runway.RUNS, ch)
    os.makedirs(d, exist_ok=True)
    return d


def put(d, name, text):
    with open(os.path.join(d, name), "w") as f:
        f.write(text)


def checker(passed, notes="n"):
    return json.dumps({"result": json.dumps({"pass": passed, "notes": notes}), "usage": {"input_tokens": 100, "output_tokens": 10}, "total_cost_usd": 0.01})


def file_diff(name, kind="modified", lines=3):
    head = f"diff --git a/{name} b/{name}\n" + ("new file mode 100644\n" if kind == "added" else "deleted file mode 100644\n" if kind == "deleted" else "")
    sign = "-" if kind == "deleted" else "+"
    return head + "--- a/x\n+++ b/x\n@@ -1 +1 @@\n" + "".join(f"{sign}line {i}\n" for i in range(lines))


def test_verify_input_lists_every_file_and_names_deletions_without_showing_them():
    d = run_dir()
    put(d, "t1.verify.md", "BRIEF")
    put(d, "t1.patch", file_diff("src/a.py") + file_diff("old/gone.txt", "deleted", 50) + file_diff("package-lock.json", lines=500))
    assert runway.verify_input(d, "t1") == 3
    text = open(os.path.join(d, "t1.verify.full.md")).read()
    assert text.startswith("BRIEF")
    assert "old/gone.txt (deleted, +0/-50)" in text and "package-lock.json (modified" in text
    assert "+line 0" in text and "-line 0" not in text  # the deletion is named, not shown
    assert text.count("diff --git") == 1


def test_verify_input_keeps_small_files_when_one_is_huge():
    d = run_dir()
    put(d, "t1.verify.md", "B")
    put(d, "t1.patch", file_diff("big.js", lines=20000) + file_diff("small.py"))
    runway.verify_input(d, "t1", budget=5000)
    text = open(os.path.join(d, "t1.verify.full.md")).read()
    assert "diff --git a/small.py" in text and "Not shown for length" in text and "big.js" in text


def test_verify_input_fetches_a_cut_file_straight_from_the_worktree(tmp_path):
    """A diff that exceeds the display limit (like a big generated file) must not sink the review: the checker
    gets the file's current content from the chunk's worktree instead of just its name."""
    content = "built output\n" * 50
    d = make_worktree(tmp_path, "t1", content)
    put(d, "t1.verify.md", "BRIEF")
    put(d, "t1.patch", file_diff("src/big.txt", lines=20000) + file_diff("small.py"))
    runway.verify_input(d, "t1", budget=5000)
    text = open(os.path.join(d, "t1.verify.full.md")).read()
    assert "FULL CURRENT CONTENT of src/big.txt" in text and content in text
    assert "Not shown for length" not in text  # fetched, so nothing is left unshown
    meta = json.loads(open(os.path.join(d, "t1.verify.meta.json")).read())
    assert meta["unreadable"] == []


def test_verify_input_marks_a_file_unreadable_when_its_worktree_is_gone():
    """No .meta (or a worktree that no longer exists) means the fetch can't happen: the file is still named, and
    recorded as unreadable so the verdict can call the run unverifiable instead of failed."""
    d = run_dir()
    put(d, "t1.verify.md", "B")
    put(d, "t1.patch", file_diff("big.js", lines=20000) + file_diff("small.py"))
    runway.verify_input(d, "t1", budget=5000)
    meta = json.loads(open(os.path.join(d, "t1.verify.meta.json")).read())
    assert meta["unreadable"] == ["big.js"]


def test_verdict_is_unverifiable_not_fail_when_a_file_could_not_be_read():
    d = run_dir()
    put(d, "t1.verify.json", checker(False, "can't tell, the build output wasn't shown"))
    with open(os.path.join(d, "t1.verify.meta.json"), "w") as f:
        json.dump({"unreadable": ["dist/app.html"]}, f)
    assert runway.verify_verdict(d, "t1") == "unverifiable"


def test_verdict_stays_fail_or_pass_when_nothing_was_unreadable():
    d = run_dir()
    put(d, "t1.verify.json", checker(False, "missing tests"))
    assert runway.verify_verdict(d, "t1") == "fail"
    put(d, "t1.verify.json", checker(True, "all good"))
    assert runway.verify_verdict(d, "t1") == "pass"


def test_verdict_is_pass_even_with_unreadable_files_if_the_checker_passed_anyway():
    d = run_dir()
    put(d, "t1.verify.json", checker(True, "the rest was enough to judge this"))
    with open(os.path.join(d, "t1.verify.meta.json"), "w") as f:
        json.dump({"unreadable": ["dist/app.html"]}, f)
    assert runway.verify_verdict(d, "t1") == "pass"


def test_unreadable_checker_output_is_no_verdict():
    assert runway._verdict_of("not json")["pass"] is None
    assert runway._verdict_of(json.dumps({"result": "I think it looks fine"}))["pass"] is None
    assert runway._verdict_of(checker(False, "missing tests"))["pass"] is False


def test_fix_prompt_carries_notes_and_returns_session():
    d = run_dir()
    put(d, "t1.verify.json", checker(False, "migrations fail from zero"))
    put(d, "t1.json", json.dumps({"session_id": "sess-9"}))
    assert runway.fix_prompt(d, "t1", 1) == "sess-9"
    assert "migrations fail from zero" in open(os.path.join(d, "t1.fix1.md")).read()


def test_fix_gate_continues_while_notes_differ_and_rounds_remain():
    d = run_dir()
    put(d, "t1.verify.json", checker(False, "missing tests for the new endpoint"))
    put(d, "t1.verify.r1.json", checker(False, "the error message is wrong"))
    assert runway.fix_round_gate(d, "t1", 1, 2) == "continue"
    assert not os.path.exists(os.path.join(d, "t1.fixcap.json"))


def test_fix_gate_stops_early_when_a_note_repeats_three_rounds_running():
    """t6vrzq5odd: the checker flagged "missing tests for the edge case" three checks running (the initial check,
    then after each of two fix attempts that didn't change anything) at ~$11 a round. The gate must stop relaunching
    right after the first repeat (round 1), not run it out to round 3 like the real incident did."""
    d = run_dir()
    note = "missing tests for the edge case"
    put(d, "t1.json", json.dumps({"total_cost_usd": 1.0}))       # round 1's agent pass (current)
    put(d, "t1.r1.json", json.dumps({"total_cost_usd": 2.0}))    # round 0's agent pass, saved when round 1 started
    put(d, "t1.verify.r1.json", checker(False, note))            # round 0's verdict: the note that triggered round 1
    put(d, "t1.verify.json", checker(False, note))               # round 1's verdict: the exact same note again
    assert runway.fix_round_gate(d, "t1", 1, 5) == "stop"
    cap = json.loads(open(os.path.join(d, "t1.fixcap.json")).read())
    assert cap["reason"] == "repeated" and cap["rounds"] == 1 and note in cap["note"]
    assert abs(cap["cost"] - (1.0 + 2.0 + 0.01 + 0.01)) < 1e-9  # t1.json + t1.r1.json + both checker passes


def test_fix_gate_stops_at_the_round_cap_even_with_fresh_notes():
    d = run_dir()
    put(d, "t1.verify.r1.json", checker(False, "note A"))
    put(d, "t1.verify.r2.json", checker(False, "note B"))
    put(d, "t1.verify.json", checker(False, "note C"))
    assert runway.fix_round_gate(d, "t1", 2, 2) == "stop"
    cap = json.loads(open(os.path.join(d, "t1.fixcap.json")).read())
    assert cap["reason"] == "max_rounds" and cap["note"] == "note C"


def test_fix_gate_goes_once_the_checker_passes():
    d = run_dir()
    put(d, "t1.verify.r1.json", checker(False, "note A"))
    put(d, "t1.verify.json", checker(True, "looks good now"))
    assert runway.fix_round_gate(d, "t1", 1, 5) == "stop"
    assert not os.path.exists(os.path.join(d, "t1.fixcap.json"))  # passed: nothing to surface


def test_chunk_status_reads_fix_cap():
    d = run_dir()
    put(d, "t1.status", "done\n")
    put(d, "t1.exit", "0\n")
    runway.write_json_atomic(os.path.join(d, "t1.fixcap.json"), {"reason": "repeated", "note": "same note", "rounds": 2, "cost": 4.5, "at": 1})
    t = runway.chunk_status("c1")["tasks"]["t1"]
    assert t["fixCap"] == {"reason": "repeated", "note": "same note", "rounds": 2, "cost": 4.5, "at": 1}


def test_rounds_add_up_usage_and_keep_earlier_notes():
    d = run_dir()
    put(d, "t1.status", "done\n")
    put(d, "t1.exit", "0\n")
    res = lambda c: json.dumps({"type": "result", "result": "ok", "session_id": "s", "num_turns": 2, "duration_ms": 10,
                                "total_cost_usd": c, "usage": {"input_tokens": 1, "output_tokens": 100}})
    put(d, "t1.r1.json", res(0.25))
    put(d, "t1.json", res(0.5))
    put(d, "t1.verify.r1.json", checker(False, "first try missed X"))
    put(d, "t1.verify.json", checker(True, "all good"))
    put(d, "t1.rounds", "1\n")
    t = runway.chunk_status("c1")["tasks"]["t1"]
    assert t["usage"]["output_tokens"] == 200 and t["turns"] == 4 and abs(t["cost"] - 0.75) < 1e-9
    assert t["verify"]["pass"] is True and abs(t["verify"]["cost"] - 0.02) < 1e-9
    assert t["rounds"] == 1 and "first try missed X" in t["roundNotes"][0]


def test_a_long_new_file_is_shown_whole_not_cut_mid_file(tmp_path):
    """A 220-line new report fits the overall budget but not the 8,000-character per-file cap. It used to be cut
    with 'this file's diff continues' and counted as readable, so the checker failed criteria it couldn't see.
    Now it is shown whole from the worktree."""
    content = "".join(f"| row {i} | some table cell text for the report |\n" for i in range(220))
    d = make_worktree(tmp_path, "t1", content)
    put(d, "t1.verify.md", "BRIEF")
    put(d, "t1.patch", file_diff("src/big.txt", kind="added", lines=1200) + file_diff("small.py"))
    big = open(os.path.join(d, "t1.patch")).read().split("diff --git a/small.py")[0]
    assert 8000 < len(big) < 60000  # over the per-file cap, well under the overall budget
    runway.verify_input(d, "t1", budget=60000)
    text = open(os.path.join(d, "t1.verify.full.md")).read()
    assert "this file's diff continues" not in text
    assert "FULL CURRENT CONTENT of src/big.txt" in text and content in text
    assert json.loads(open(os.path.join(d, "t1.verify.meta.json")).read())["unreadable"] == []


def _big_file_worktree(tmp_path, tid, edits, path="src/page.html", n=12000):
    """A repo whose big file (n lines, hundreds of KB) the agent edited at the given line numbers, with the real
    patch run.sh would have written."""
    base = [f"<div class=\"row-{i}\">original line {i} with some padding text</div>\n" for i in range(n)]
    d = make_worktree(tmp_path, tid, "".join(base), path=path)
    repo = json.loads(open(os.path.join(d, tid + ".meta")).read())["repo"]
    git(repo, "checkout", "-q", "main")
    # put the big base on main too, so the agent's change is only the edits
    with open(os.path.join(repo, path), "w") as f:
        f.write("".join(base))
    git(repo, "commit", "-qam", "big base")
    wtp = os.path.join(repo, ".claude", "worktrees", "wt-" + tid)
    git(wtp, "reset", "-q", "--hard", "main")
    new = list(base)
    for i in edits:
        new[i] = f"<div class=\"row-{i}\">CHANGED line {i} for the task</div>\n"
    with open(os.path.join(wtp, path), "w") as f:
        f.write("".join(new))
    git(wtp, "commit", "-qam", "agent change")
    with open(os.path.join(d, tid + ".patch"), "w") as f:
        f.write(git(wtp, "diff", "-U3", "main", "HEAD"))
    return d, wtp


def test_a_large_file_is_shown_as_a_wide_diff_not_as_truncated_full_content(tmp_path):
    """The 400KB page case: the checker used to get the 'full current content' cut after 20,000 characters (the
    CSS at the top) and failed the task for being truncated. A large file is now a -U30 diff of its changes."""
    d, wtp = _big_file_worktree(tmp_path, "t1", edits=[5000, 9000])
    put(d, "t1.verify.md", "BRIEF")
    assert len(open(os.path.join(wtp, "src/page.html")).read()) > 400000
    runway.verify_input(d, "t1", small=1000)
    text = open(os.path.join(d, "t1.verify.full.md")).read()
    assert "FULL CURRENT CONTENT" not in text and "truncated ..." not in text
    assert "DIFF of src/page.html" in text and "CHANGED line 5000" in text and "CHANGED line 9000" in text
    assert "original line 4971 " in text and "original line 4969 " not in text  # 30 lines of context, not the file
    assert "Judging from diffs is expected" in text
    meta = json.loads(open(os.path.join(d, "t1.verify.meta.json")).read())
    assert meta["unreadable"] == [] and meta["partial"] == []


def test_a_diff_over_budget_drops_whole_hunks_and_names_them(tmp_path):
    edits = [1000 * k for k in range(1, 11)]  # ten far-apart hunks
    d, wtp = _big_file_worktree(tmp_path, "t1", edits=edits)
    put(d, "t1.verify.md", "BRIEF")
    runway.verify_input(d, "t1", budget=15000, small=1000)
    text = open(os.path.join(d, "t1.verify.full.md")).read()
    assert "FULL CURRENT CONTENT" not in text and "truncated ..." not in text
    shown = [i for i in edits if f"CHANGED line {i} " in text]
    assert 0 < len(shown) < len(edits)
    m = __import__("re").search(r"\[(\d+) hunk\(s\) of src/page.html omitted for length, not truncated mid-hunk: ([^\]]*)\]", text)
    assert m and int(m.group(1)) == len(edits) - len(shown)
    assert m.group(2).count("hunk ") == len(edits) - len(shown) and "@@" not in m.group(2)
    # every hunk that appears is complete: its @@ counts match the lines that follow it
    body = text.split("DIFF of src/page.html")[1].split("[")[0]
    for h in body.split("\n@@")[1:]:
        lines = h.split("\n")[1:]
        assert sum(1 for l in lines if l[:1] in (" ", "+", "-")) >= 61
    meta = json.loads(open(os.path.join(d, "t1.verify.meta.json")).read())
    assert meta["partial"] == ["src/page.html"] and meta["unreadable"] == []
    assert "Hunks omitted for length" in text
