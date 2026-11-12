"""Roadmap schema and persistence in helper/runway.py: validation errors name the field, a roadmap round-trips through
its JSON file, and old versions migrate on load. Files live under conftest's temp HOME; no git is involved."""
import json, os

import pytest

import runway
from runway import PI, Provenance, Roadmap, RoadmapError, Sprint, Task


def sample():
    return Roadmap(
        project="painter-site", title="Painter site", provenance=Provenance(briefHash="abc123", generatedAt=1791158400,
                                                                           generatorModel="claude-sonnet-5"),
        pis=[PI(key="pi-1", title="Launch", detail="Ship the site", sprints=[
            Sprint(key="s-1", title="Pages", tasks=[
                Task(key="t-1", title="Home page", detail="Hero and services", acceptance="Renders on mobile",
                     estTokens=120000, model="claude-sonnet-5", kind="feature", dependsOn=[], status="todo"),
                Task(key="t-2", title="Contact form", estTokens=80000, model="claude-haiku-4-5-20251001", kind="feature",
                     dependsOn=["t-1"], status="blocked"),
            ]),
        ])])


def task_dict(**over):
    t = {"key": "t-1", "title": "Home page"}
    t.update(over)
    return t


def roadmap_dict(task=None):
    return {"version": 1, "project": "p", "title": "P", "provenance": {"briefHash": "h", "generatedAt": 1, "generatorModel": "m"},
            "pis": [{"key": "pi-1", "title": "PI", "sprints": [{"key": "s-1", "title": "S", "tasks": [task or task_dict()]}]}]}


def test_saved_roadmap_round_trips_through_its_json_file(tmp_path):
    repo = str(tmp_path / "painter-site")
    path = runway.save_roadmap(repo, sample())
    assert path == runway.roadmap_path(repo)
    assert path.startswith(os.path.join(runway.BASE, "roadmaps"))
    on_disk = json.load(open(path))
    assert on_disk["version"] == 1
    assert on_disk["pis"][0]["sprints"][0]["tasks"][1]["dependsOn"] == ["t-1"]
    assert runway.load_roadmap(repo) == sample()


def test_every_task_field_is_written_to_disk(tmp_path):
    repo = str(tmp_path / "p")
    runway.save_roadmap(repo, sample())
    task = json.load(open(runway.roadmap_path(repo)))["pis"][0]["sprints"][0]["tasks"][0]
    assert set(task) == {"key", "title", "detail", "acceptance", "estTokens", "model", "kind", "dependsOn", "status"}
    assert json.load(open(runway.roadmap_path(repo)))["provenance"] == {"briefHash": "abc123", "generatedAt": 1791158400,
                                                                       "generatorModel": "claude-sonnet-5"}


def test_save_accepts_a_plain_dict_and_load_of_a_missing_roadmap_is_none(tmp_path):
    repo = str(tmp_path / "p")
    assert runway.load_roadmap(repo) is None
    runway.save_roadmap(repo, roadmap_dict())
    assert runway.load_roadmap(repo).pis[0].sprints[0].tasks[0].title == "Home page"


def test_roadmaps_of_two_projects_do_not_share_a_file(tmp_path):
    a, b = str(tmp_path / "a" / "site"), str(tmp_path / "b" / "site")
    assert runway.roadmap_path(a) != runway.roadmap_path(b)


@pytest.mark.parametrize("task, field", [
    (task_dict(key=""), "pis[0].sprints[0].tasks[0].key"),
    (task_dict(key=7), "pis[0].sprints[0].tasks[0].key"),
    (task_dict(title="  "), "pis[0].sprints[0].tasks[0].title"),
    (task_dict(estTokens=-1), "pis[0].sprints[0].tasks[0].estTokens"),
    (task_dict(estTokens="120k"), "pis[0].sprints[0].tasks[0].estTokens"),
    (task_dict(estTokens=True), "pis[0].sprints[0].tasks[0].estTokens"),
    (task_dict(estTokens=1.5), "pis[0].sprints[0].tasks[0].estTokens"),
    (task_dict(status="done-ish"), "pis[0].sprints[0].tasks[0].status"),
    (task_dict(model=["x"]), "pis[0].sprints[0].tasks[0].model"),
    (task_dict(dependsOn="t-0"), "pis[0].sprints[0].tasks[0].dependsOn"),
    (task_dict(dependsOn=[3]), "pis[0].sprints[0].tasks[0].dependsOn[0]"),
    (task_dict(dependsOn=["nope"]), "pis[0].sprints[0].tasks[0].dependsOn[0]"),
])
def test_invalid_task_names_the_offending_field(task, field):
    with pytest.raises(RoadmapError) as e:
        runway.parse_roadmap(roadmap_dict(task))
    assert field in str(e.value)


def test_missing_required_task_key_is_named():
    t = task_dict()
    del t["title"]
    with pytest.raises(RoadmapError, match=r"pis\[0\]\.sprints\[0\]\.tasks\[0\]\.title is required"):
        runway.parse_roadmap(roadmap_dict(t))


def test_duplicate_key_names_both_places():
    d = roadmap_dict()
    d["pis"][0]["sprints"][0]["tasks"].append(task_dict())
    with pytest.raises(RoadmapError, match=r"pis\[0\]\.sprints\[0\]\.tasks\[1\]\.key .*already used at pis\[0\]\.sprints\[0\]\.tasks\[0\]\.key"):
        runway.parse_roadmap(d)


def test_pi_and_sprint_fields_are_checked_too():
    d = roadmap_dict()
    d["pis"][0]["sprints"][0]["title"] = 5
    with pytest.raises(RoadmapError, match=r"pis\[0\]\.sprints\[0\]\.title must be a string"):
        runway.parse_roadmap(d)
    d = roadmap_dict()
    d["pis"] = [5]
    with pytest.raises(RoadmapError, match=r"pis\[0\] must be an object"):
        runway.parse_roadmap(d)


def test_provenance_fields_are_checked():
    d = roadmap_dict()
    d["provenance"]["generatedAt"] = "yesterday"
    with pytest.raises(RoadmapError, match=r"provenance\.generatedAt must be a whole number"):
        runway.parse_roadmap(d)


def test_version_is_required_and_a_newer_version_is_refused():
    d = roadmap_dict()
    del d["version"]
    with pytest.raises(RoadmapError, match="version is required"):
        runway.parse_roadmap(d)
    d = roadmap_dict()
    d["version"] = 2
    with pytest.raises(RoadmapError, match="version is 2, newer than the 1"):
        runway.parse_roadmap(d)


def test_older_version_is_migrated_on_load(tmp_path, monkeypatch):
    repo = str(tmp_path / "p")
    old = roadmap_dict()
    old["version"] = 0
    old["title"] = "Old name"
    os.makedirs(os.path.dirname(runway.roadmap_path(repo)), exist_ok=True)
    json.dump(old, open(runway.roadmap_path(repo), "w"))
    with pytest.raises(RoadmapError, match="version 0 has no migration to 1"):
        runway.load_roadmap(repo)

    def migrate(d):
        d = dict(d, title=d["title"] + " (migrated)")
        return d
    monkeypatch.setitem(runway.ROADMAP_MIGRATIONS, 0, migrate)
    loaded = runway.load_roadmap(repo)
    assert loaded.version == 1 and loaded.title == "Old name (migrated)"


def test_invalid_file_on_disk_raises_with_its_path(tmp_path):
    repo = str(tmp_path / "p")
    os.makedirs(os.path.dirname(runway.roadmap_path(repo)), exist_ok=True)
    d = roadmap_dict(task_dict(estTokens=-4))
    json.dump(d, open(runway.roadmap_path(repo), "w"))
    with pytest.raises(RoadmapError, match=r"\.json: pis\[0\]\.sprints\[0\]\.tasks\[0\]\.estTokens must not be negative"):
        runway.load_roadmap(repo)


def test_file_that_is_not_json_raises(tmp_path):
    repo = str(tmp_path / "p")
    os.makedirs(os.path.dirname(runway.roadmap_path(repo)), exist_ok=True)
    open(runway.roadmap_path(repo), "w").write("{nope")
    with pytest.raises(RoadmapError, match="not valid JSON"):
        runway.load_roadmap(repo)


def test_save_refuses_an_invalid_roadmap_and_writes_nothing(tmp_path):
    repo = str(tmp_path / "p")
    bad = sample()
    bad.pis[0].sprints[0].tasks[0].status = "finished"
    with pytest.raises(RoadmapError, match="status must be one of"):
        runway.save_roadmap(repo, bad)
    assert not os.path.exists(runway.roadmap_path(repo))
