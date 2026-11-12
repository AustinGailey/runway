"""Shared fixtures. Every test runs with HOME, the ~/.ai-planner paths and git identity pointed at a temp dir,
so nothing here reads or writes the real ~/.ai-planner, the user's git config or the user's real repos."""
import atexit, fcntl, os, shutil, sys, tempfile, time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "helper"))

# The helper computes its paths from HOME when it is imported, so HOME must be disposable before that happens
_import_home = tempfile.mkdtemp(prefix="runway-test-home-")
atexit.register(shutil.rmtree, _import_home, ignore_errors=True)
os.environ["HOME"] = _import_home
import runway  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    base = home / ".ai-planner"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("RUNWAY_NO_KEYCHAIN", "1")
    # no test makes a real sign-in call; tests of auth_check itself pass their own runner
    monkeypatch.setattr(runway, "auth_check", lambda *a, **k: {"ok": True, "via": "test", "detail": ""})
    for k, v in {"GIT_AUTHOR_NAME": "Runway Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                 "GIT_COMMITTER_NAME": "Runway Test", "GIT_COMMITTER_EMAIL": "test@example.com"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(runway, "HOME", str(home))
    monkeypatch.setattr(runway, "BASE", str(base))
    monkeypatch.setattr(runway, "RUNS", str(base / "runs"))
    monkeypatch.setattr(runway, "QUEUE", str(base / "queue"))
    monkeypatch.setattr(runway, "STATUS", str(base / "status.json"))
    monkeypatch.setattr(runway, "CACHE", str(base / "usage-cache.json"))
    monkeypatch.setattr(runway, "PROJECTS", str(home / ".claude" / "projects"))
    monkeypatch.setattr(runway, "INTEG", str(base / "integration"))
    monkeypatch.setattr(runway, "MERGES", str(base / "merges"))
    monkeypatch.setattr(runway, "FAILURES", str(base / "failures.log"))
    monkeypatch.setattr(runway, "NOTIFY_STATE", str(base / "notify-state.json"))
    monkeypatch.setattr(runway, "NOTIFY_OFF", str(base / "notify.off"))
    monkeypatch.setattr(runway, "NOTIFY_ROUTINE", str(base / "notify.routine"))
    return home


@pytest.fixture
def chrome_lock():
    """One Chrome-driven test at a time on this Mac, across every test run (agents' checks run the suite in parallel,
    and several headless Chromes plus builds starve each other until timing-based steps miss). Waits up to 10 minutes."""
    f = open("/tmp/runway-chrome-tests.lock", "w")
    deadline = time.time() + 600
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() > deadline:
                raise
            time.sleep(0.5)
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
