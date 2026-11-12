"""needs-check: the helper runs a needs-you step's check in the repo, but only a plain read-only one."""
import base64
import json

import runway


def run(items):
    out = runway.needs_check(base64.b64encode(json.dumps(items).encode()).decode())
    assert out.startswith("@@NEEDSCHECK ")
    return json.loads(out[len("@@NEEDSCHECK "):])


def test_a_grep_check_passes_once_the_secret_is_in_the_env_file(tmp_path):
    item = {"id": "s1", "check": "grep -q GHL_WEBHOOK_SECRET .env.local", "repo": str(tmp_path)}
    assert run([item]) == {"s1": False}
    (tmp_path / ".env.local").write_text("OTHER=1\n")
    assert run([item]) == {"s1": False}
    (tmp_path / ".env.local").write_text("OTHER=1\nGHL_WEBHOOK_SECRET=abc\n")
    assert run([item]) == {"s1": True}


def test_test_and_and_chains_work_and_a_missing_check_is_never_done(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert run([{"id": "a", "check": "test -e a.txt && grep -q x a.txt", "repo": str(tmp_path)},
                {"id": "b", "check": "test -e nope.txt", "repo": str(tmp_path)},
                {"id": "c", "check": "", "repo": str(tmp_path)}]) == {"a": True, "b": False, "c": False}


def test_anything_that_is_not_a_plain_read_only_check_is_never_run(tmp_path):
    marker = tmp_path / "ran"
    bad = ["touch ran", "grep -q x a.txt; touch ran", "grep -q x a.txt && touch ran", "grep -q $(touch ran) a.txt", "test -e a.txt | touch ran",
           "grep -q `touch ran` a.txt", "rm -rf ."]
    (tmp_path / "a.txt").write_text("x")
    res = run([{"id": str(i), "check": c, "repo": str(tmp_path)} for i, c in enumerate(bad)])
    assert not any(res.values()) and not marker.exists()
