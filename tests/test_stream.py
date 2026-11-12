"""Live progress: run.sh tees the agent's stream-json to <task>.stream.jsonl, the helper summarises it."""
import json, os, re, subprocess, time

import runway

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "All done", "session_id": "sess-1",
          "num_turns": 3, "duration_ms": 1234, "total_cost_usd": 0.5,
          "usage": {"input_tokens": 10, "output_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1000}}


def events(final=True):
    u = lambda o: {"input_tokens": 5, "output_tokens": o, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000}
    ev = [{"type": "system", "subtype": "init", "session_id": "sess-1"},
          # one API message split over two events: its usage must count once
          {"type": "assistant", "message": {"id": "m1", "usage": u(50), "content": [{"type": "text", "text": "Looking"}]}},
          {"type": "assistant", "message": {"id": "m1", "usage": u(50), "content": [
              {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/helper/runway.py"}}]}},
          {"type": "user", "message": {"content": [{"type": "tool_result", "content": "{\"type\":\"result\"}"}]}},
          {"type": "assistant", "message": {"id": "m2", "usage": u(30), "content": [
              {"type": "tool_use", "name": "Bash", "input": {"command": "python3 build.py", "description": "Build the page"}}]}}]
    if final:
        ev.append(RESULT)
    return "\n".join(json.dumps(e) for e in ev) + "\n"


def run_dir(ch="c1"):
    d = os.path.join(runway.RUNS, ch)
    os.makedirs(d, exist_ok=True)
    return d


def put(d, name, text):
    with open(os.path.join(d, name), "w") as f:
        f.write(text)


def test_running_task_reports_step_tokens_and_elapsed():
    d = run_dir()
    put(d, "t1.status", "running\n")
    put(d, "t1.start", str(int(time.time()) - 125))
    put(d, "t1.stream.jsonl", events(final=False) + '{"type":"assist')  # half-written last line
    p = runway.chunk_status("c1")["tasks"]["t1"]["progress"]
    assert p["step"] == "Build the page"
    assert p["turns"] == 2
    assert p["tokens"] == round(2 * (5 + 100 + 100) + 50 + 30)  # m1 counted once
    assert 120 <= p["elapsed"] <= 140


def test_running_task_without_stream_yet_shows_starting():
    d = run_dir()
    put(d, "t1.status", "running\n")
    put(d, "t1.start", str(int(time.time())))
    assert runway.chunk_status("c1")["tasks"]["t1"]["progress"]["step"] == "Starting"


def test_done_task_falls_back_to_result_in_stream():
    d = run_dir()
    put(d, "t1.status", "done\n")
    put(d, "t1.exit", "0\n")
    put(d, "t1.json", "")
    put(d, "t1.stream.jsonl", events())
    t = runway.chunk_status("c1")["tasks"]["t1"]
    assert "progress" not in t
    assert t["session"] == "sess-1" and t["result"] == "All done" and t["usage"]["output_tokens"] == 200


def test_step_summaries():
    todo = {"todos": [{"status": "completed", "content": "a"}, {"status": "in_progress", "content": "b", "activeForm": "Doing b"}]}
    assert runway._step("TodoWrite", todo) == "Doing b"
    assert runway._step("Edit", {"file_path": "/a/b/c.py"}) == "Edit c.py"
    assert runway._step("Bash", {"command": "ls -la\nmore"}) == "$ ls -la"
    assert runway._step("Grep", {"pattern": "foo"}) == "Grep foo"


def test_run_sh_pipeline_keeps_result_json_and_exit_code(tmp_path):
    """The exact agent line from runnerScript, run against a fake claude that emits a stream and exits 3."""
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    line = next(l for l in src.splitlines() if "tee -a \"$D/$id.stream.jsonl\"" in l)
    cmd = re.sub(r"^\s*`|`,\s*$", "", line).replace("${bin}", str(tmp_path / "claude")).replace("\\${", "${").rstrip().removesuffix("}")
    stream = tmp_path / "stream.txt"
    stream.write_text(events())
    fake = tmp_path / "claude"
    fake.write_text(f'#!/bin/bash\ncat "{stream}"; echo oops >&2; exit 3\n')
    fake.chmod(0o755)
    d = tmp_path / "run"
    d.mkdir()
    subprocess.run(["bash", "-c", f'D="{d}"; id=t1; a=(-p hi); {cmd}'], check=True)
    assert json.loads((d / "t1.json").read_text()) == RESULT
    assert (d / "t1.stream.jsonl").read_text() == events()
    assert (d / "t1.exit").read_text().strip() == "3"
    assert "oops" in (d / "t1.err").read_text()
