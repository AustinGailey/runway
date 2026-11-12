"""Roadmap CLI: generate, show, edit, approve, through runway.main() so the dispatch and exit codes are the real ones. Claude is
faked with scripted replies and $EDITOR with a script that copies a file over the roadmap, so nothing calls Claude or opens
an editor. Each test uses a temp git repo, and the ~/.ai-planner paths are the temp ones from conftest."""
import json, os, sys

import pytest

import runway
from test_git_flows import make_repo


def sprint(key, *tokens):
    """A sprint with one task per token count."""
    tasks = [{"key": "%s-t%d" % (key, i), "title": "Task %d" % i, "detail": "d", "acceptance": "a", "estTokens": n,
              "model": "claude-sonnet-5", "kind": "feature", "dependsOn": []} for i, n in enumerate(tokens, 1)]
    return {"key": key, "title": "Sprint " + key, "detail": "d", "tasks": tasks}


def roadmap(sprints, title="Painter site"):
    return {"version": 1, "project": "site", "title": title,
            "pis": [{"key": "pi-1", "title": "Launch", "detail": "d", "sprints": sprints}]}


def cli(capsys, *argv):
    """Run one command. Returns (exit code, stdout, stderr)."""
    with pytest.raises(SystemExit) as e:
        runway.main(list(argv))
    out = capsys.readouterr()
    return e.value.code, out.out, out.err


@pytest.fixture
def site(tmp_path):
    return os.path.realpath(make_repo(tmp_path))  # the path git reports, which is what the CLI keys on


@pytest.fixture
def claude(monkeypatch):
    """claude_reply stand-in: pops the scripted replies in order and records every prompt."""
    box = {"replies": [], "prompts": []}

    def ask(prompt, repo):
        box["prompts"].append(prompt)
        return box["replies"].pop(0)

    monkeypatch.setattr(runway, "claude_reply", ask)
    return box


@pytest.fixture
def editor(tmp_path, monkeypatch):
    """$EDITOR as a script that copies the file named by NEW_ROADMAP over the file it is given."""
    script = tmp_path / "editor.py"
    script.write_text("import os, shutil, sys\nshutil.copyfile(os.environ['NEW_ROADMAP'], sys.argv[1])\n")
    new = tmp_path / "new.json"
    monkeypatch.setenv("EDITOR", "%s %s" % (sys.executable, script))
    monkeypatch.setenv("NEW_ROADMAP", str(new))

    def set_next(data):
        new.write_text(data if isinstance(data, str) else json.dumps(data))
    return set_next


def generate_candidate(capsys, site, claude, title="Generated"):
    claude["replies"].append(json.dumps(roadmap([sprint("s-1", 100000)], title)))
    code, _, _ = cli(capsys, "roadmap", "generate", "--goal", "A site for a painter", "--repo", site)
    assert code == 0


@pytest.mark.parametrize("name", ["generate", "show", "edit", "approve"])
def test_every_subcommand_has_help_with_examples_and_exit_codes(capsys, name):
    code, out, _ = cli(capsys, "roadmap", name, "-h")
    assert code == 0
    assert "examples:" in out and "runway.py roadmap " + name in out
    assert "exit codes:" in out and "2  usage error" in out


def test_generate_writes_a_candidate_and_leaves_the_accepted_roadmap_alone(capsys, site, claude):
    runway.save_roadmap(site, roadmap([sprint("s-old", 1000)], "Accepted"))
    generate_candidate(capsys, site, claude)
    cand = runway.candidate_paths(site)[0]
    assert os.path.exists(cand)
    assert runway.load_roadmap(site).title == "Accepted"
    assert "Repository: " + site in claude["prompts"][0]


def test_generate_reads_a_brief_file_and_flags_override_it(capsys, site, claude, tmp_path):
    brief = tmp_path / "brief.json"
    brief.write_text(json.dumps({"goal": "From the file", "repo": site, "constraints": "No CMS"}))
    claude["replies"].append(json.dumps(roadmap([sprint("s-1", 100000)])))
    code, out, _ = cli(capsys, "roadmap", "generate", "--brief", str(brief), "--goal", "From the flag")
    assert code == 0 and "@@ROADMAP candidate " in out
    prompt = claude["prompts"][0]
    assert "Project goal: From the flag" in prompt and "Constraints: No CMS" in prompt


def test_generate_with_no_goal_is_a_usage_error_and_calls_nothing(capsys, site, claude):
    claude["replies"].append(json.dumps(roadmap([sprint("s-1", 1)])))
    code, _, err = cli(capsys, "roadmap", "generate", "--goal", "  ", "--repo", site)
    assert code == 2 and "brief.goal must not be empty" in err
    assert claude["prompts"] == []


def test_generate_with_a_missing_brief_file_is_a_usage_error(capsys, site, claude):
    code, _, err = cli(capsys, "roadmap", "generate", "--brief", os.path.join(site, "nope.json"))
    assert code == 2 and "no brief file" in err


def test_generation_that_never_validates_exits_1_and_writes_no_candidate(capsys, site, claude):
    claude["replies"] += ["not json", "still not json"]
    code, _, err = cli(capsys, "roadmap", "generate", "--goal", "A site", "--repo", site)
    assert code == 1 and "No valid roadmap after one repair retry" in err
    assert not os.path.exists(runway.candidate_paths(site)[0])


def test_show_prints_the_tree_with_per_sprint_totals_and_flags_a_sprint_over_2m(capsys, site):
    runway.save_roadmap(site, roadmap([sprint("s-big", 1200000, 1300000), sprint("s-small", 100000)]))
    code, out, _ = cli(capsys, "roadmap", "show", "--repo", site)
    assert code == 0
    assert "pi-1  Launch" in out
    big = next(l for l in out.splitlines() if l.strip().startswith("s-big"))
    assert "2,500,000 tokens in 2 tasks" in big and "over the 2,000,000 cap by 500,000" in big
    small = next(l for l in out.splitlines() if l.strip().startswith("s-small"))
    assert "100,000 tokens in 1 task" in small and "!!" not in small
    assert "warning: 1 sprint over the 2,000,000 cap: s-big" in out
    assert "Total: 2,600,000 tokens" in out


def test_show_candidate_says_it_is_not_approved(capsys, site, claude):
    generate_candidate(capsys, site, claude)
    code, out, _ = cli(capsys, "roadmap", "show", "--candidate", "--repo", site)
    assert code == 0 and "(candidate, not approved)" in out


def test_show_with_no_roadmap_is_a_usage_error(capsys, site):
    assert cli(capsys, "roadmap", "show", "--repo", site)[0] == 2
    code, _, err = cli(capsys, "roadmap", "show", "--candidate", "--repo", site)
    assert code == 2 and "no candidate roadmap" in err


def test_show_of_an_invalid_roadmap_file_names_the_field(capsys, site):
    bad = roadmap([sprint("s-1", 1)])
    bad["pis"][0]["sprints"][0]["tasks"][0]["estTokens"] = "lots"
    os.makedirs(os.path.dirname(runway.roadmap_path(site)), exist_ok=True)
    with open(runway.roadmap_path(site), "w") as f:
        json.dump(bad, f)
    code, _, err = cli(capsys, "roadmap", "show", "--repo", site)
    assert code == 1 and "estTokens must be a whole number" in err


def test_edit_saves_a_change_that_validates(capsys, site, editor):
    runway.save_roadmap(site, roadmap([sprint("s-1", 100000)]))
    editor(roadmap([sprint("s-1", 100000), sprint("s-2", 50000)], "Edited"))
    code, out, _ = cli(capsys, "roadmap", "edit", "--repo", site)
    assert code == 0 and "@@ROADMAP saved" in out
    saved = runway.load_roadmap(site)
    assert saved.title == "Edited" and [s.key for s in saved.pis[0].sprints] == ["s-1", "s-2"]


def test_edit_refuses_an_invalid_roadmap_and_keeps_the_file_and_the_edit(capsys, site, editor):
    runway.save_roadmap(site, roadmap([sprint("s-1", 100000)]))
    before = open(runway.roadmap_path(site)).read()
    broken = roadmap([sprint("s-1", 100000)])
    broken["pis"][0]["sprints"][0]["tasks"][0]["estTokens"] = "lots"
    editor(broken)
    code, _, err = cli(capsys, "roadmap", "edit", "--repo", site)
    assert code == 1 and "not saved, the roadmap does not validate" in err
    assert "estTokens must be a whole number" in err
    assert open(runway.roadmap_path(site)).read() == before
    kept = err.split("kept at ")[1].split(";")[0]
    assert os.path.exists(kept)


def test_edit_with_no_change_saves_nothing(capsys, site, monkeypatch):
    runway.save_roadmap(site, roadmap([sprint("s-1", 100000)]))
    monkeypatch.setenv("EDITOR", "true")
    code, out, _ = cli(capsys, "roadmap", "edit", "--repo", site)
    assert code == 0 and "No changes; nothing saved." in out


def test_edit_whose_editor_fails_saves_nothing(capsys, site, monkeypatch):
    runway.save_roadmap(site, roadmap([sprint("s-1", 100000)]))
    monkeypatch.setenv("EDITOR", "false")
    code, _, err = cli(capsys, "roadmap", "edit", "--repo", site)
    assert code == 1 and "the editor exited 1; nothing saved" in err


def test_edit_can_repair_a_roadmap_that_no_longer_validates(capsys, site, editor):
    os.makedirs(os.path.dirname(runway.roadmap_path(site)), exist_ok=True)
    with open(runway.roadmap_path(site), "w") as f:
        f.write("{ not json")
    editor(roadmap([sprint("s-1", 1)], "Repaired"))
    assert cli(capsys, "roadmap", "edit", "--repo", site)[0] == 0
    assert runway.load_roadmap(site).title == "Repaired"


def test_edit_candidate_works_before_approval(capsys, site, claude, editor):
    generate_candidate(capsys, site, claude)
    editor(roadmap([sprint("s-1", 100000)], "Hand edited"))
    assert cli(capsys, "roadmap", "edit", "--candidate", "--repo", site)[0] == 0
    assert runway.load_roadmap(site, runway.candidate_paths(site)[0]).title == "Hand edited"
    assert runway.load_roadmap(site) is None


def test_approve_without_a_candidate_is_a_usage_error(capsys, site):
    code, _, err = cli(capsys, "roadmap", "approve", "--repo", site)
    assert code == 2 and "no candidate roadmap" in err


def test_approve_makes_the_candidate_the_one_active_roadmap(capsys, site, claude):
    generate_candidate(capsys, site, claude)
    cand = runway.candidate_paths(site)[0]
    code, out, _ = cli(capsys, "roadmap", "approve", "--repo", site)
    assert code == 0 and "@@ROADMAP approved " + runway.roadmap_path(site) in out
    assert runway.load_roadmap(site).title == "Generated"
    assert not os.path.exists(cand)  # moved, not copied: one roadmap, not two
    assert "The roadmap it replaces" not in out  # nothing was active before


def test_approving_again_archives_the_roadmap_it_replaces(capsys, site, claude):
    generate_candidate(capsys, site, claude, title="First")
    cli(capsys, "roadmap", "approve", "--repo", site)
    generate_candidate(capsys, site, claude, title="Second")
    code, out, _ = cli(capsys, "roadmap", "approve", "--repo", site)
    assert code == 0
    assert runway.load_roadmap(site).title == "Second"
    history = os.path.join(runway.BASE, "roadmaps", "history")
    kept = [os.path.join(history, f) for f in os.listdir(history)]
    assert len(kept) == 1 and json.load(open(kept[0]))["title"] == "First"
    assert "The roadmap it replaces is kept at " + kept[0] in out


def test_approve_is_refused_while_an_autopilot_holds_the_roadmap(capsys, site, claude):
    generate_candidate(capsys, site, claude)
    lock = runway._lock(os.path.join(runway.BASE, "autopilot-%s.lock" % runway._slug(site, "autopilot")), 0)
    try:
        code, _, err = cli(capsys, "roadmap", "approve", "--repo", site)
    finally:
        lock.close()
    assert code == 1 and "an autopilot is working this roadmap" in err
    assert runway.load_roadmap(site) is None
    assert os.path.exists(runway.candidate_paths(site)[0])


def accepted_files(site):
    """The roadmap files in roadmaps/ that belong to this project (candidates and raw replies have their own slugs)."""
    d = os.path.join(runway.BASE, "roadmaps")
    return sorted(f for f in os.listdir(d) if f.startswith(runway._slug(site, "roadmap")) and f.endswith(".json"))


def test_end_to_end_generate_show_edit_approve_keeps_exactly_one_active_roadmap(capsys, site, claude, editor):
    generate_candidate(capsys, site, claude, title="Generated")
    code, out, _ = cli(capsys, "roadmap", "show", "--candidate", "--repo", site)
    assert code == 0 and "Generated" in out and "Total: 100,000 tokens" in out

    editor(roadmap([sprint("s-1", 100000), sprint("s-2", 2500000)], "Hand edited"))
    assert cli(capsys, "roadmap", "edit", "--candidate", "--repo", site)[0] == 0

    code, out, _ = cli(capsys, "roadmap", "approve", "--repo", site)
    assert code == 0 and "@@ROADMAP approved" in out
    assert accepted_files(site) == [os.path.basename(runway.roadmap_path(site))]
    code, out, _ = cli(capsys, "roadmap", "show", "--repo", site)
    assert code == 0 and "Hand edited" in out and "warning: 1 sprint over the 2,000,000 cap: s-2" in out

    generate_candidate(capsys, site, claude, title="Second")
    assert cli(capsys, "roadmap", "approve", "--repo", site)[0] == 0
    assert accepted_files(site) == [os.path.basename(runway.roadmap_path(site))]
    assert runway.load_roadmap(site).title == "Second"
    assert len(os.listdir(os.path.join(runway.BASE, "roadmaps", "history"))) == 1
    assert cli(capsys, "roadmap", "approve", "--repo", site)[0] == 2  # no candidate left to approve
    assert runway.load_roadmap(site).title == "Second"
