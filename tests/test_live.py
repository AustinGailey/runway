"""live.json: running chunks, plus finished or stopped ones for LIVE_KEEP seconds, so the page always sees how a chunk
ended (a final write that dropped finished chunks left cards on Running until the next minute tick)."""
import json, os, time

import runway


def chunk(name, done=None, stopped=None):
    d = os.path.join(runway.RUNS, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "_pgid"), "w").write("1")
    open(os.path.join(d, "a.status"), "w").write("done\n" if done or stopped else "running\n")
    for marker, age in (("_chunk.done", done), ("_chunk.stopped", stopped)):
        if age is not None:
            p = os.path.join(d, marker)
            open(p, "w").close()
            os.utime(p, (time.time() - age, time.time() - age))
    return d


def test_running_and_recently_ended_chunks_are_kept_and_old_ones_dropped():
    chunk("chrun"); chunk("chdone", done=5); chunk("chstop", stopped=5); chunk("chold", done=runway.LIVE_KEEP + 60)
    out = runway.live_write()
    assert set(out) == {"chrun", "chdone", "chstop"}
    saved = json.load(open(runway.LIVE))
    assert set(saved["chunks"]) == {"chrun", "chdone", "chstop"} and saved["chunks"]["chdone"]["tasks"]["a"]["state"] == "done"
