"""folderSuggestions and its helpers (nearestReadFolder, isSecretPath, accessSuggestions), the page's own definitions,
run under node against a small in-memory S."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is needed to run the page's JavaScript")


def page_source():
    """DEFAULTS, proj, task, and readDirs/folderSuggestions/accessSuggestions, exactly as the page has them."""
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    def grab(start_marker, end_marker):
        start = src.index(start_marker)
        end = src.index(end_marker, start)
        return src[start:end]
    defaults = grab("const DEFAULTS = ", "\nconst DEFAULT_ALLOWED")
    projtask = grab("const proj = id =>", "\nconst pisOf = pid =>")
    readdirs = grab("const readDirs = p =>", "\nconst NEVER_RULES")
    return defaults + "\n" + projtask + "\n" + readdirs


def run_js(S, body):
    script = page_source() + "\nconst S = %s;\n" % json.dumps(S) + body
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    return json.loads(out.stdout)


def base_state(project=None, estimates=None):
    return {"projects": [project] if project else [], "tasks": [], "settings": {"home": "/Users/dev", "globalReadDirs": []}, "estimates": estimates or []}


def test_no_access_blockers_suggests_nothing():
    S = base_state(project={"id": "p1", "readDirs": []}, estimates=[
        {"id": "e1", "projectId": "p1", "taskId": "t1", "category": "ok", "blockers": [], "readPaths": []}])
    assert run_js(S, "console.log(JSON.stringify(folderSuggestions('p1')));") == []


def test_groups_a_denied_read_path_to_the_vault_claude_folder():
    S = base_state(project={"id": "p1", "readDirs": []}, estimates=[
        {"id": "e1", "projectId": "p1", "taskId": "t1", "category": "blocked:access", "blockers": [],
         "readPaths": ["/Users/dev/vault/_Claude/plans/roadmap.md"]}])
    got = run_js(S, "console.log(JSON.stringify(folderSuggestions('p1')));")
    assert got == [{"folder": "~/vault/_Claude", "runs": 1, "tasks": ["t1"]}]


def test_groups_a_sibling_repo_path_to_its_root_and_counts_runs_and_tasks():
    S = base_state(project={"id": "p1", "readDirs": []}, estimates=[
        {"id": "e1", "projectId": "p1", "taskId": "t1", "category": "blocked:access", "blockers": [],
         "readPaths": ["/Users/dev/Projects/sibling-repo/docs/notes.md"]},
        {"id": "e2", "projectId": "p1", "taskId": "t2", "category": "blocked:access",
         "blockers": [{"type": "access", "path": "/Users/dev/Projects/sibling-repo/README.md"}], "readPaths": []}])
    got = run_js(S, "console.log(JSON.stringify(folderSuggestions('p1')));")
    assert got == [{"folder": "~/Projects/sibling-repo", "runs": 2, "tasks": ["t1", "t2"]}]


def test_a_folder_already_covered_by_readdirs_is_skipped():
    S = base_state(project={"id": "p1", "readDirs": ["~/Projects/sibling-repo"]}, estimates=[
        {"id": "e1", "projectId": "p1", "taskId": "t1", "category": "blocked:access", "blockers": [],
         "readPaths": ["/Users/dev/Projects/sibling-repo/docs/notes.md"]}])
    assert run_js(S, "console.log(JSON.stringify(folderSuggestions('p1')));") == []


def test_a_folder_already_covered_by_a_global_read_dir_is_skipped():
    S = base_state(project={"id": "p1", "readDirs": []}, estimates=[
        {"id": "e1", "projectId": "p1", "taskId": "t1", "category": "blocked:access", "blockers": [],
         "readPaths": ["/Users/dev/Projects/sibling-repo/docs/notes.md"]}])
    S["settings"]["globalReadDirs"] = ["~/Projects/sibling-repo"]
    assert run_js(S, "console.log(JSON.stringify(folderSuggestions('p1')));") == []


def test_an_ignored_folder_stays_hidden():
    S = base_state(project={"id": "p1", "readDirs": [], "readDirsIgnored": ["~/Projects/sibling-repo"]}, estimates=[
        {"id": "e1", "projectId": "p1", "taskId": "t1", "category": "blocked:access", "blockers": [],
         "readPaths": ["/Users/dev/Projects/sibling-repo/docs/notes.md"]}])
    assert run_js(S, "console.log(JSON.stringify(folderSuggestions('p1')));") == []


@pytest.mark.parametrize("path", [
    "/Users/dev/.ssh/id_rsa",
    "/Users/dev/.aws/credentials",
    "/Users/dev/.gnupg/secring.gpg",
    "/Users/dev/.config/some-tool/config.json",
    "/Users/dev/.ai-planner/status.json",
    "/Users/dev/Library/Keychains/login.keychain",
    "/Users/dev/Projects/sibling-repo/.env",
    "/Users/dev/Projects/sibling-repo/.env.local",
    "/Users/dev/vault/secrets/api-keys.md",
])
def test_secret_and_system_locations_are_never_suggested(path):
    S = base_state(project={"id": "p1", "readDirs": []}, estimates=[
        {"id": "e1", "projectId": "p1", "taskId": "t1", "category": "blocked:access", "blockers": [], "readPaths": [path]}])
    assert run_js(S, "console.log(JSON.stringify(folderSuggestions('p1')));") == []


def test_access_suggestions_groups_by_project_for_run_health():
    S = base_state(estimates=[
        {"id": "e1", "projectId": "p1", "taskId": "t1", "category": "blocked:access", "blockers": [],
         "readPaths": ["/Users/dev/Projects/sibling-repo/docs/notes.md"]},
        {"id": "e2", "projectId": "p2", "taskId": "t2", "category": "ok", "blockers": [], "readPaths": []}])
    S["projects"] = [{"id": "p1", "readDirs": []}, {"id": "p2", "readDirs": []}]
    got = run_js(S, "const m = accessSuggestions(S.estimates); console.log(JSON.stringify([...m.keys()]));")
    assert got == ["p1"]
