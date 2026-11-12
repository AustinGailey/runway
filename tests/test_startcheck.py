"""start-check: one cached Haiku read of a brief against the repo, before a launch. A stand-in claude answers with
canned JSON (and counts its calls), so nothing here spends tokens; a broken check must never block a launch."""
import base64, json, os, stat

import runway
from test_git_flows import commit, make_repo


def fake_claude(tmp_path, result, cost=0.004, exit_code=0, raw=None):
    """A claude that records its arguments and prompt, then prints a --output-format json envelope."""
    script = tmp_path / "fake_claude.py"
    env = raw if raw is not None else json.dumps({"type": "result", "is_error": False, "result": result, "total_cost_usd": cost,
                                                   "usage": {"input_tokens": 900, "output_tokens": 60, "cache_read_input_tokens": 0}})
    script.write_text("#!/usr/bin/env python3\nimport sys, os\nd = %r\nopen(d + '/calls', 'a').write('1')\n"
                      "open(d + '/args', 'w').write(repr(sys.argv))\nprint(%r)\nsys.exit(%d)\n" % (str(tmp_path), env, exit_code))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def calls(tmp_path):
    p = tmp_path / "calls"
    return len(p.read_text()) if p.exists() else 0


def task(repo, **kw):
    t = {"id": "t1", "title": "Add export", "detail": "Extend src/exporter.py with a csv option.", "acceptance": "- csv works",
         "deps": [], "repo": repo, "base": ""}
    t.update(kw)
    return t


def run(tasks, claude):
    spec = base64.b64encode(json.dumps(tasks).encode()).decode()
    out = runway.start_check(spec, claude=claude)
    assert out.startswith("@@STARTCHECK ")
    return json.loads(out[len("@@STARTCHECK "):])


OK = json.dumps({"ok": True, "missing": []})
FOUNDATION = json.dumps({"ok": False, "missing": [{"type": "foundation", "detail": "src/exporter.py does not exist", "needs": "the exporter module"}]})


def test_ok_answer(tmp_path):
    repo = make_repo(tmp_path)
    [r] = run([task(repo)], fake_claude(tmp_path, OK))
    assert r["ok"] is True and r["missing"] == [] and r["cached"] is False and "warning" not in r


def test_foundation_missing(tmp_path):
    repo = make_repo(tmp_path)
    [r] = run([task(repo)], fake_claude(tmp_path, FOUNDATION))
    assert r["ok"] is False
    assert r["missing"] == [{"type": "foundation", "detail": "src/exporter.py does not exist", "needs": "the exporter module"}]


def test_needs_you_and_decision_pass_through_and_unknown_types_are_dropped(tmp_path):
    repo = make_repo(tmp_path)
    ans = json.dumps({"ok": False, "missing": [{"type": "needs_you", "detail": "Needs a Stripe key", "needs": "STRIPE_KEY"},
                                               {"type": "decision", "detail": "Which database?", "needs": "pick one"},
                                               {"type": "vibes", "detail": "ignore me"}, {"type": "decision", "detail": ""}]})
    [r] = run([task(repo)], fake_claude(tmp_path, ans))
    assert [m["type"] for m in r["missing"]] == ["needs_you", "decision"] and r["ok"] is False


def test_answer_wrapped_in_prose_or_fences_is_read(tmp_path):
    repo = make_repo(tmp_path)
    [r] = run([task(repo)], fake_claude(tmp_path, "Here you go:\n```json\n" + FOUNDATION + "\n```"))
    assert r["ok"] is False and r["missing"][0]["type"] == "foundation"


def test_malformed_answer_is_ok_with_a_warning_and_not_cached(tmp_path):
    repo = make_repo(tmp_path)
    c = fake_claude(tmp_path, "I think it is probably fine {oops")
    [r] = run([task(repo)], c)
    assert r["ok"] is True and r["missing"] == [] and r["warning"]
    run([task(repo)], c)
    assert calls(tmp_path) == 2  # a broken answer is asked again, not remembered


def test_failing_claude_and_garbage_output_never_block(tmp_path):
    repo = make_repo(tmp_path)
    for c in (fake_claude(tmp_path, "", exit_code=1, raw="not json at all"),
              fake_claude(tmp_path, "", raw=json.dumps({"is_error": True, "result": "Not logged in"})),
              str(tmp_path / "does-not-exist")):
        [r] = run([task(repo)], c)
        assert r["ok"] is True and r["missing"] == [] and r["warning"]


def test_missing_repo_never_blocks(tmp_path):
    [r] = run([task(str(tmp_path / "nowhere"))], fake_claude(tmp_path, OK))
    assert r["id"] == "t1" and r["ok"] is True  # no crash; a repo-less check just sees an empty map


def test_cache_hit_on_same_brief_and_head(tmp_path):
    repo = make_repo(tmp_path)
    c = fake_claude(tmp_path, FOUNDATION)
    first = run([task(repo)], c)[0]
    second = run([task(repo)], c)[0]
    assert calls(tmp_path) == 1
    assert second["cached"] is True and second["missing"] == first["missing"] and second["ok"] is False
    assert os.listdir(os.path.join(runway.BASE, "startcheck"))


def test_cache_miss_when_the_brief_changes(tmp_path):
    repo = make_repo(tmp_path)
    c = fake_claude(tmp_path, OK)
    run([task(repo)], c)
    run([task(repo, acceptance="- csv and json work")], c)
    run([task(repo, id="t2")], c)
    assert calls(tmp_path) == 3


def test_cache_miss_when_the_repo_head_moves(tmp_path):
    repo = make_repo(tmp_path)
    c = fake_claude(tmp_path, OK)
    run([task(repo)], c)
    commit(repo, "new work", {"src/exporter.py": "x = 1\n"})
    run([task(repo)], c)
    assert calls(tmp_path) == 2


def test_cache_follows_the_base_branch_not_the_checkout(tmp_path):
    repo = make_repo(tmp_path)
    runway.git(repo, "branch", "feature")
    c = fake_claude(tmp_path, OK)
    run([task(repo, base="feature")], c)
    commit(repo, "main moves on", {"z.txt": "z\n"})
    run([task(repo, base="feature")], c)
    assert calls(tmp_path) == 1


def test_args_and_prompt_are_built_as_specified(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "readme", {"README.md": "# Proj\nA tiny project.\n", "src/exporter.py": "x = 1\n"})
    c = fake_claude(tmp_path, OK)
    t = task(repo, detail="Extend src/exporter.py and add src/csvout.py.", deps=[{"title": "Build the schema", "status": "done"}])
    run([t], c)
    argv = eval((tmp_path / "args").read_text())
    assert argv[1] == "-p"
    for pair in (("--model", "haiku"), ("--output-format", "json"), ("--tools", ""), ("--mcp-config", '{"mcpServers":{}}')):
        assert argv[argv.index(pair[0]) + 1] == pair[1]
    assert "--strict-mcp-config" in argv
    prompt = argv[2]
    assert "TASK: Add export" in prompt and "- Build the schema [done]" in prompt
    assert "README (head):\n# Proj" in prompt and "a.txt" in prompt
    assert "THAT EXIST: src/exporter.py" in prompt and "THAT DO NOT EXIST YET: src/csvout.py" in prompt


def test_repo_map_caps_files_at_300(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "many", {"gen/f%03d.txt" % i: "x\n" for i in range(320)})
    text, head = runway.repo_map(repo, "", "nothing named")
    assert head and text.startswith("REPO FILES (300 of ")
    listed = [l for l in text.split("\n") if l.endswith(".txt") or l.endswith(".json")]
    assert len(listed) == 300 and "gen/f319.txt" not in listed


def test_usage_is_logged(tmp_path):
    repo = make_repo(tmp_path)
    r = run([task(repo)], fake_claude(tmp_path, OK, cost=0.0123))[0]
    assert r["costUsd"] == 0.0123
    rec = json.loads(open(os.path.join(runway.BASE, "startcheck", "log.jsonl")).read().splitlines()[-1])
    assert rec["task"] == "t1" and rec["tokens"]["in"] == 900 and rec["tokens"]["out"] == 60 and rec["costUsd"] == 0.0123


def test_bad_spec_is_an_error_line_not_a_crash():
    assert runway.start_check("%%%not base64").startswith("@@ERR")
    assert runway.start_check(base64.b64encode(b'{"a": 1}').decode()).startswith("@@ERR")


def test_cli_dispatch(tmp_path, capsys, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(runway, "CLAUDE", fake_claude(tmp_path, FOUNDATION))
    runway.main(["start-check", base64.b64encode(json.dumps([task(repo)]).encode()).decode()])
    out = capsys.readouterr().out
    assert out.startswith("@@STARTCHECK ") and json.loads(out[13:])[0]["missing"][0]["type"] == "foundation"
