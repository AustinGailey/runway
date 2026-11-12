"""Hands-off and Mac notifications: with `notify handsoff on` the helper stays quiet about what the page resolves by itself
(a usage-limit pause it will relaunch, a merge run where everything merged) and still pings for what needs Austin (a
conflict, a merge run with failures). Off, the behaviour is the one tests/test_notify.py pins."""
import pytest

import runway
from test_notify import make_chunk, posted, write_merge  # noqa: F401  (posted is a fixture)


def test_handsoff_flag_is_set_and_cleared_and_reported(tmp_path):
    assert not runway.handsoff_enabled()
    runway.main(["notify", "handsoff", "on"])
    assert runway.handsoff_enabled()
    runway.main(["notify", "handsoff", "off"])
    assert not runway.handsoff_enabled()


def test_limit_pause_is_quiet_when_handsoff_is_on(posted):
    runway.main(["notify", "handsoff", "on"])
    runway.tick()
    make_chunk(limited=True)
    runway.tick()
    assert posted == []


def test_clean_merge_run_is_quiet_but_a_failed_one_is_not(posted):
    runway.main(["notify", "handsoff", "on"])
    make_chunk()
    runway.tick()
    write_merge("m1", {"t1": {"outcome": "merged"}})
    runway.tick()
    assert posted == []
    write_merge("m2", {"t1": {"outcome": "conflict", "msg": "Conflicts with approved work in app.py."}})
    runway.tick()
    assert [t for t, _ in posted] == ["Runway needs you: merge conflict", "Runway run complete"]


def test_limit_pause_and_clean_merge_run_notify_as_before_when_handsoff_is_off(posted):
    make_chunk()
    runway.tick()
    write_merge("m1", {"t1": {"outcome": "merged"}})
    runway.tick()
    assert [t for t, _ in posted] == ["Runway run complete"]
