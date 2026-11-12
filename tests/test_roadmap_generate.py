"""Roadmap generation in helper/runway.py: a brief goes to Claude (faked here with scripted replies), the reply is validated
against the schema and the planning rules, one repair retry follows a bad reply, and the candidate and raw replies are
written beside the project's roadmap. Nothing calls Claude, and the project's accepted roadmap is never touched."""
import json, os, subprocess

import pytest

import runway
from runway import GenerationError, RoadmapError


def task(key, **over):
    t = {"key": key, "title": "Task " + key, "detail": "d", "acceptance": "a", "estTokens": 100000,
         "model": "claude-sonnet-5", "kind": "feature", "dependsOn": []}
    t.update(over)
    return t


def reply_for(tasks=None, sprints=None):
    sprints = sprints or [{"key": "s-1", "title": "Pages", "detail": "d", "tasks": tasks or [task("t-1")]}]
    return json.dumps({"version": 1, "title": "Painter site", "pis": [{"key": "pi-1", "title": "Launch", "detail": "d",
                                                                     "sprints": sprints}]})


class Scripted:
    """Stands in for claude_reply: returns the scripted replies in order and records every prompt it was sent."""
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt, repo):
        self.prompts.append(prompt)
        return self.replies.pop(0)


@pytest.fixture
def brief(tmp_path):
    repo = tmp_path / "painter-site"
    repo.mkdir()
    return {"goal": "A site for a painting business", "repo": str(repo), "constraints": "No CMS", "planned": "Logo is done"}


def raw_of(brief):
    return json.load(open(runway.candidate_paths(brief["repo"])[1]))


def test_valid_reply_returns_a_validated_roadmap_with_provenance(brief):
    rm = runway.generate_roadmap(brief, ask=Scripted(reply_for()))
    assert isinstance(rm, runway.Roadmap)
    assert rm.project == "painter-site" and rm.title == "Painter site"
    assert rm.provenance.briefHash == runway.brief_hash(brief)
    assert rm.provenance.generatorModel == runway.ROADMAP_MODEL
    assert rm.provenance.generatedAt > 0
    assert rm.pis[0].sprints[0].tasks[0].key == "t-1"


def test_candidate_and_raw_reply_are_written_beside_each_other_and_the_accepted_roadmap_is_not_touched(brief):
    runway.generate_roadmap(brief, ask=Scripted(reply_for()))
    cand, raw = runway.candidate_paths(brief["repo"])
    assert os.path.dirname(cand) == os.path.dirname(raw) == os.path.join(runway.BASE, "roadmaps")
    assert runway.load_roadmap(brief["repo"]) is None
    assert runway.parse_roadmap(json.load(open(cand))).pis[0].sprints[0].tasks[0].title == "Task t-1"
    saved = raw_of(brief)
    assert saved["outcome"] == "ok" and len(saved["attempts"]) == 1
    assert saved["attempts"][0]["response"] == reply_for() and saved["attempts"][0]["errors"] == []


def test_prompt_carries_the_brief_and_the_planning_rules(brief):
    ask = Scripted(reply_for())
    runway.generate_roadmap(brief, ask=ask)
    prompt = ask.prompts[0]
    for needle in ("A site for a painting business", brief["repo"], "No CMS", "Logo is done",
                   "at most 2000000 effective tokens", "above 1000000", "human-only step", "earlier in the roadmap"):
        assert needle in prompt


def test_reply_wrapped_in_a_code_fence_and_chatter_is_accepted(brief):
    ask = Scripted("Here is the roadmap:\n```json\n" + reply_for() + "\n```\nHope it helps.")
    assert runway.generate_roadmap(brief, ask=ask).title == "Painter site"
    assert len(ask.prompts) == 1


def test_invalid_json_gets_one_repair_retry_with_the_error_appended(brief):
    ask = Scripted("not json at all", reply_for())
    rm = runway.generate_roadmap(brief, ask=ask)
    assert rm.pis[0].sprints[0].tasks[0].key == "t-1"
    assert len(ask.prompts) == 2
    assert "Your previous reply was rejected" in ask.prompts[1] and "not valid JSON" in ask.prompts[1]
    saved = raw_of(brief)
    assert saved["outcome"] == "ok" and len(saved["attempts"]) == 2
    assert saved["attempts"][0]["response"] == "not json at all" and saved["attempts"][0]["errors"]
    assert saved["attempts"][1]["errors"] == []


def test_schema_error_is_repaired_and_names_the_field(brief):
    bad = json.loads(reply_for())
    bad["pis"][0]["sprints"][0]["tasks"][0]["estTokens"] = -5
    ask = Scripted(json.dumps(bad), reply_for())
    runway.generate_roadmap(brief, ask=ask)
    assert "pis[0].sprints[0].tasks[0].estTokens must not be negative" in ask.prompts[1]


def test_sprint_over_the_token_cap_is_rejected_then_repaired(brief):
    over = reply_for(tasks=[task("t-1", estTokens=900000), task("t-2", estTokens=900000, dependsOn=["t-1"]),
                            task("t-3", estTokens=300000)])
    ask = Scripted(over, reply_for())
    runway.generate_roadmap(brief, ask=ask)
    assert "sprint s-1 totals 2100000 estTokens, over the 2000000 cap" in ask.prompts[1]


def test_task_over_the_token_cap_is_rejected_then_repaired(brief):
    ask = Scripted(reply_for(tasks=[task("t-1", estTokens=1000001)]), reply_for())
    runway.generate_roadmap(brief, ask=ask)
    assert "task t-1 has 1000001 estTokens, over the 1000000 cap" in ask.prompts[1]


def test_a_task_at_exactly_the_cap_is_allowed(brief):
    rm = runway.generate_roadmap(brief, ask=Scripted(reply_for(tasks=[task("t-1", estTokens=1000000),
                                                                      task("t-2", estTokens=1000000)])))
    assert rm.pis[0].sprints[0].tasks[1].estTokens == 1000000


def test_human_only_task_is_rejected_then_repaired(brief):
    ask = Scripted(reply_for(tasks=[task("t-1", kind="human")]), reply_for())
    runway.generate_roadmap(brief, ask=ask)
    assert 'task t-1 has kind "human": a human-only step is not a task' in ask.prompts[1]


@pytest.mark.parametrize("tasks, message", [
    ([task("t-1", dependsOn=["t-2"]), task("t-2")], "task t-1 depends on t-2, which is not earlier"),
    ([task("t-1", dependsOn=["t-1"])], "task t-1 depends on t-1, which is not earlier"),
])
def test_dependency_on_a_later_or_own_task_is_rejected(brief, tasks, message):
    ask = Scripted(reply_for(tasks=tasks), reply_for())
    runway.generate_roadmap(brief, ask=ask)
    assert message in ask.prompts[1]


def test_dependency_across_sprints_counts_earlier_order(brief):
    sprints = [{"key": "s-1", "title": "A", "tasks": [task("t-1")]},
               {"key": "s-2", "title": "B", "tasks": [task("t-2", dependsOn=["t-1"])]}]
    rm = runway.generate_roadmap(brief, ask=Scripted(reply_for(sprints=sprints)))
    assert rm.pis[0].sprints[1].tasks[0].dependsOn == ["t-1"]


def test_two_bad_replies_raise_a_clear_error_after_exactly_one_retry(brief):
    ask = Scripted("nope", "still nope")
    with pytest.raises(GenerationError, match="No valid roadmap after one repair retry.*not valid JSON") as e:
        runway.generate_roadmap(brief, ask=ask)
    assert len(ask.prompts) == 2
    assert runway.candidate_paths(brief["repo"])[1] in str(e.value)
    saved = raw_of(brief)
    assert saved["outcome"] == "failed" and [a["response"] for a in saved["attempts"]] == ["nope", "still nope"]
    assert not os.path.exists(runway.candidate_paths(brief["repo"])[0])


def test_repair_that_still_breaks_a_rule_fails_with_that_rule(brief):
    ask = Scripted(reply_for(tasks=[task("t-1", estTokens=1000001)]), reply_for(tasks=[task("t-1", estTokens=1000001)]))
    with pytest.raises(GenerationError, match="over the 1000000 cap"):
        runway.generate_roadmap(brief, ask=ask)
    assert len(ask.prompts) == 2


def test_bad_brief_is_refused_before_any_call(brief, tmp_path):
    ask = Scripted()
    with pytest.raises(RoadmapError, match="brief.goal must not be empty"):
        runway.generate_roadmap(dict(brief, goal="  "), ask=ask)
    with pytest.raises(RoadmapError, match="brief.repo is not a folder"):
        runway.generate_roadmap(dict(brief, repo=str(tmp_path / "missing")), ask=ask)
    with pytest.raises(RoadmapError, match="brief.constraints must be a string"):
        runway.generate_roadmap(dict(brief, constraints=["x"]), ask=ask)
    with pytest.raises(RoadmapError, match="brief must be an object"):
        runway.generate_roadmap("goal", ask=ask)
    assert ask.prompts == []


def test_optional_brief_fields_may_be_left_out(brief):
    minimal = {"goal": brief["goal"], "repo": brief["repo"]}
    ask = Scripted(reply_for())
    runway.generate_roadmap(minimal, ask=ask)
    assert "Constraints: (none)" in ask.prompts[0]
    assert "Already planned (keep it, and build the roadmap around it rather than re-planning it): (none)" in ask.prompts[0]


def test_claude_reply_error_is_raised_without_a_repair_retry(brief, monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout=json.dumps({"is_error": True, "result": "usage limit reached"}), stderr="")
    monkeypatch.setattr(runway.subprocess, "run", fake_run)
    with pytest.raises(GenerationError, match="usage limit reached"):
        runway.generate_roadmap(brief)
    assert not os.path.exists(runway.candidate_paths(brief["repo"])[1])


def test_claude_reply_runs_headless_with_read_only_tools_in_the_repo(brief, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"result": reply_for()}), stderr="")
    monkeypatch.setattr(runway.subprocess, "run", fake_run)
    rm = runway.generate_roadmap(brief)
    cmd, kw = calls[0]
    assert cmd[:2] == ["claude", "-p"] and "--output-format" in cmd and "json" in cmd
    assert cmd[cmd.index("--tools") + 1: cmd.index("--tools") + 4] == ["Read", "Grep", "Glob"]
    assert "Edit" not in cmd and "Bash" not in cmd
    assert kw["cwd"] == brief["repo"] and rm.title == "Painter site"
