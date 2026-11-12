"""The roadmap layer end to end, replaying recorded model replies from tests/fixtures/roadmap: valid generation, the malformed
JSON repair, oversized sprints and tasks, dependsOn cycles, calibration, the saved candidate's round trip, and the autopilot
CLI through runway.main(). Nothing calls Claude: every reply is a fixture or a stub, and any claude subprocess fails the test."""
import json, os, subprocess

import pytest

import runway
from runway import GenerationError, Task

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "roadmap")


def recorded(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


class Scripted:
    """Stands in for claude_reply: returns the recorded replies in order and keeps every prompt it was sent."""
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt, repo):
        self.prompts.append(prompt)
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        if cmd and cmd[0] == "claude":
            raise AssertionError("tests must not call Claude: %r" % (cmd[:2],))
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(runway.subprocess, "run", run)


@pytest.fixture
def brief(tmp_path):
    repo = tmp_path / "painter-site"
    repo.mkdir()
    return {"goal": "A site for a painting business", "repo": str(repo), "constraints": "No CMS", "planned": "Logo is done"}


def raw_of(brief):
    return json.load(open(runway.candidate_paths(brief["repo"])[1]))


# ---------- generation from recorded replies ----------
def test_recorded_fenced_reply_generates_the_roadmap_it_recorded(brief):
    ask = Scripted(recorded("valid_fenced.txt"))
    rm = runway.generate_roadmap(brief, ask=ask)
    assert len(ask.prompts) == 1
    assert rm.title == "Painter site" and rm.project == "painter-site"
    assert [t.key for pi in rm.pis for s in pi.sprints for t in s.tasks] == ["t-1", "t-2", "t-3"]
    assert rm.pis[0].sprints[1].tasks[0].dependsOn == ["t-1"]
    assert raw_of(brief)["outcome"] == "ok" and raw_of(brief)["attempts"][0]["errors"] == []


def test_recorded_candidate_is_saved_and_loads_back_unchanged(brief):
    rm = runway.generate_roadmap(brief, ask=Scripted(recorded("valid_plain.json")))
    cand, _ = runway.candidate_paths(brief["repo"])
    assert runway.parse_roadmap(json.load(open(cand))) == rm
    assert runway.load_roadmap(brief["repo"]) is None  # the candidate is not the project's roadmap until Austin accepts it
    runway.save_roadmap(brief["repo"], rm)
    assert runway.load_roadmap(brief["repo"]) == rm


def test_truncated_json_reply_is_repaired_and_the_retry_names_the_json_error(brief):
    ask = Scripted(recorded("truncated.txt"), recorded("valid_plain.json"))
    rm = runway.generate_roadmap(brief, ask=ask)
    assert rm.pis[0].sprints[0].tasks[1].key == "t-2"
    assert len(ask.prompts) == 2
    assert "Your previous reply was rejected" in ask.prompts[1] and "not valid JSON" in ask.prompts[1]
    saved = raw_of(brief)
    assert saved["attempts"][0]["response"] == recorded("truncated.txt") and saved["attempts"][0]["errors"]
    assert saved["outcome"] == "ok" and saved["attempts"][1]["errors"] == []


def test_recorded_oversized_sprint_is_rejected_and_the_repair_names_the_cap(brief):
    ask = Scripted(recorded("oversized_sprint.json"), recorded("valid_plain.json"))
    runway.generate_roadmap(brief, ask=ask)
    assert "sprint s-1 totals 2100000 estTokens, over the 2000000 cap" in ask.prompts[1]
    assert raw_of(brief)["attempts"][0]["errors"] == ["sprint s-1 totals 2100000 estTokens, over the 2000000 cap. "
                                                       "Split it into more sprints."]


def test_recorded_oversized_task_is_rejected_and_the_repair_names_the_cap(brief):
    ask = Scripted(recorded("oversized_task.json"), recorded("valid_plain.json"))
    runway.generate_roadmap(brief, ask=ask)
    assert "task t-1 has 1000001 estTokens, over the 1000000 cap" in ask.prompts[1]


# ---------- dependsOn cycles ----------
def test_recorded_cycle_is_reported_by_its_keys_in_the_repair_prompt(brief):
    ask = Scripted(recorded("cycle.json"), recorded("valid_plain.json"))
    runway.generate_roadmap(brief, ask=ask)
    assert "dependsOn cycle: t-1 -> t-3 -> t-2 -> t-1. Remove one of these dependencies." in ask.prompts[1]
    assert "task t-1 depends on t-3, which is not earlier in the roadmap." in ask.prompts[1]


def test_a_cycle_that_survives_the_repair_fails_and_names_it(brief):
    ask = Scripted(recorded("cycle.json"), recorded("cycle.json"))
    with pytest.raises(GenerationError, match=r"dependsOn cycle: t-1 -> t-3 -> t-2 -> t-1"):
        runway.generate_roadmap(brief, ask=ask)
    assert len(ask.prompts) == 2
    assert not os.path.exists(runway.candidate_paths(brief["repo"])[0])
    assert raw_of(brief)["outcome"] == "failed"


def test_a_task_depending_on_itself_is_a_cycle_as_well_as_a_later_reference(brief):
    ask = Scripted(_self_dependency(), recorded("valid_plain.json"))
    runway.generate_roadmap(brief, ask=ask)
    assert "dependsOn cycle: t-2 -> t-2. Remove one of these dependencies." in ask.prompts[1]
    assert "task t-2 depends on t-2, which is not earlier in the roadmap." in ask.prompts[1]


def _self_dependency():
    d = json.loads(recorded("valid_plain.json"))
    d["pis"][0]["sprints"][0]["tasks"][1]["dependsOn"] = ["t-2"]
    return json.dumps(d)


def task(key, *deps):
    return Task(key=key, title="Task " + key, dependsOn=list(deps))


def test_dependency_cycles_finds_each_cycle_once_and_none_in_an_acyclic_graph():
    diamond = [task("t-1"), task("t-2", "t-1"), task("t-3", "t-1"), task("t-4", "t-2", "t-3")]
    assert runway.dependency_cycles(diamond) == []
    tangled = [task("a", "b"), task("b", "a"), task("c", "c"), task("d", "a")]
    cycles = runway.dependency_cycles(tangled)
    assert sorted(cycles) == [["a", "b", "a"], ["c", "c"]]


# ---------- calibration ----------
def test_run_history_calibrates_a_roadmap_tasks_estimate_for_its_model_and_kind(brief):
    rm = runway.generate_roadmap(brief, ask=Scripted(recorded("valid_plain.json")))
    t = rm.pis[0].sprints[0].tasks[0]
    assert (t.model, t.kind, t.estTokens) == ("claude-sonnet-5", "feature", 120000)
    history = [{"model": "claude-sonnet-5", "kind": "feature", "est": 100000, "eff": 200000, "at": i} for i in range(3)]
    table = runway.calibration_table(history)
    cal = runway.calibration_factor(table, t.model, t.kind)
    assert cal == {"model": "claude-sonnet-5", "kind": "feature", "n": 3, "factor": 2.0}
    assert round(t.estTokens * cal["factor"]) == 240000
    assert runway.calibration_factor(table, "claude-haiku-4-5-20251001", "docs")["factor"] == 1.0


def test_fewer_than_three_runs_leave_a_roadmap_estimate_at_one(brief):
    rm = runway.generate_roadmap(brief, ask=Scripted(recorded("valid_plain.json")))
    t = rm.pis[0].sprints[0].tasks[0]
    history = [{"model": "claude-sonnet-5", "kind": "feature", "est": 100000, "eff": 200000, "at": i} for i in range(2)]
    assert runway.calibration_factor(runway.calibration_table(history), t.model, t.kind)["factor"] == 1.0


# ---------- the autopilot CLI, through runway.main() ----------
def cli(capsys, *argv):
    """Run one command. Returns (exit code, stdout, stderr)."""
    with pytest.raises(SystemExit) as e:
        runway.main(list(argv))
    out = capsys.readouterr()
    return e.value.code, out.out, out.err


def make_repo(tmp_path):
    repo = tmp_path / "site"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return str(repo)


def store_raw(repo, text):
    os.makedirs(os.path.dirname(runway.roadmap_path(repo)), exist_ok=True)
    with open(runway.roadmap_path(repo), "w") as f:
        f.write(text)


def test_autopilot_help_lists_its_exit_codes(capsys):
    code, out, _ = cli(capsys, "autopilot", "-h")
    assert code == 0 and "exit codes:" in out


def test_autopilot_without_a_roadmap_exits_1_and_says_so(tmp_path, capsys):
    code, out, _ = cli(capsys, "autopilot", "--repo", make_repo(tmp_path))
    assert code == 1 and "@@ERR This project has no roadmap to work." in out


def test_autopilot_with_a_roadmap_that_breaks_the_schema_names_the_field_and_file(tmp_path, capsys):
    repo = make_repo(tmp_path)
    store_raw(repo, recorded("valid_plain.json").replace('"estTokens": 90000', '"estTokens": -90000'))
    code, out, _ = cli(capsys, "autopilot", "--repo", repo)
    assert code == 1
    assert runway.roadmap_path(repo) in out and "pis[0].sprints[0].tasks[1].estTokens must not be negative" in out


def test_autopilot_with_every_task_done_finishes_without_launching_anything(tmp_path, capsys, monkeypatch):
    repo = make_repo(tmp_path)
    done = json.loads(recorded("valid_plain.json"))
    for pi in done["pis"]:
        for s in pi["sprints"]:
            for t in s["tasks"]:
                t["status"] = "done"
    store_raw(repo, json.dumps(done))

    def no_launch(chunk):
        raise AssertionError("nothing is ready, so nothing may launch: %s" % chunk)
    monkeypatch.setattr(runway, "launch", no_launch)
    code, out, _ = cli(capsys, "autopilot", "--repo", repo)
    assert code == 0 and "@@AUTOPILOT done=0 blocked=0 left=0" in out
