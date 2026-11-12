"""Unattended sign-in: the auth check, the preflight problem it raises, and the run category for sign-in failures."""
import json, subprocess, types
import runway

def _runner(stdout, rc=0):
    return lambda *a, **k: types.SimpleNamespace(stdout=stdout, stderr="", returncode=rc)


def test_auth_check_ok_and_cached(monkeypatch, tmp_path):
    check = runway._REAL_AUTH_CHECK
    calls = []
    def run(*a, **k):
        calls.append(k.get("env", {}))
        return types.SimpleNamespace(stdout=json.dumps({"type": "result", "is_error": False, "result": "ok"}), stderr="", returncode=0)
    r = check(claude="claude", runner=run)
    assert r["ok"] and r["via"] == "/login session"
    assert "ANTHROPIC_API_KEY" not in calls[0]
    assert check(claude="claude", runner=run)["ok"] and len(calls) == 1  # cached


def test_auth_check_reports_expired_session(monkeypatch):
    out = json.dumps({"type": "result", "is_error": True, "result": "Failed to authenticate: OAuth session expired and could not be refreshed"})
    r = runway._REAL_AUTH_CHECK(claude="claude", runner=_runner(out, 1))
    assert not r["ok"] and "OAuth session expired" in r["detail"]


def test_preflight_names_sign_in_once(monkeypatch, tmp_path):
    monkeypatch.setattr(runway, "auth_check", lambda *a, **k: {"ok": False, "via": "/login session", "detail": "Invalid bearer token"})
    repos = []
    for n in ("a", "b"):
        r = tmp_path / n
        subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
        subprocess.run(["git", "-C", str(r), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        repos.append({"name": n, "repo": str(r)})
    import base64
    out = json.loads(runway.preflight(base64.b64encode(json.dumps(repos).encode()).decode()).split(" ", 1)[1])
    kinds = [[p["kind"] for p in o["problems"]] for o in out]
    assert kinds == [["auth"], []]
    assert "setup-token" in out[0]["problems"][0]["detail"]


def test_sign_in_failure_is_its_own_category():
    t = {"exit": 1, "is_error": True, "result": "Failed to authenticate: OAuth session expired and could not be refreshed"}
    assert runway.run_signals(t)["category"] == "auth"
    assert runway.run_signals({"exit": 1, "is_error": True, "result": "something else"})["category"] == "crash"


def test_agent_env_drops_api_key_and_skips_keychain_in_tests(monkeypatch):
    env, has = runway.agent_env({"ANTHROPIC_API_KEY": "x", "RUNWAY_NO_KEYCHAIN": "1", "PATH": "/bin"})
    assert "ANTHROPIC_API_KEY" not in env and not has and "CLAUDE_CODE_OAUTH_TOKEN" not in env
