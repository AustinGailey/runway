#!/usr/bin/env python3
"""A stand-in for `claude` inside the harness sandbox: no network, no tokens.

As an agent (-p with stream-json) it writes harness-agent.txt in its working folder, then prints a few stream events
and a final result with usage and a session id, so runs, progress, merges and spend all have something real to show.
HARNESS_AGENT=fail makes it exit 1; HARNESS_AGENT_SLEEP=<seconds> makes it take that long (to watch a run live);
HARNESS_AGENT_GATE=<path> makes it wait until that file exists (deterministic tests).
As a one-shot call (--output-format json) it prints a result whose text is "{}" (an empty JSON answer);
HARNESS_STARTCHECK=<json> makes the can-this-start check's call answer with that JSON instead.
"""
import json, os, sys, time, uuid

args = sys.argv[1:]
stream = "stream-json" in args
sid = str(uuid.uuid4())
usage = {"input_tokens": 1200, "output_tokens": 800, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 20000}
if stream:
    print(json.dumps({"type": "system", "subtype": "init", "session_id": sid}), flush=True)
    print(json.dumps({"type": "assistant", "message": {"id": "m1", "usage": usage, "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "true", "description": "Harness agent working"}}]}}), flush=True)
    gate = os.environ.get("HARNESS_AGENT_GATE")
    if gate:  # wait until the test says go (it creates this file), so nothing depends on timing; give up after 120 s
        end = time.time() + 120
        while not os.path.exists(gate) and time.time() < end:
            time.sleep(0.2)
    else:
        time.sleep(float(os.environ.get("HARNESS_AGENT_SLEEP", "2")))
    if os.environ.get("HARNESS_AGENT") != "fail":
        with open("harness-agent.txt", "a") as f:
            f.write("harness agent ran at %d\n" % time.time())
text = "Harness agent finished.\n\n```runway\n{\"status\": \"done\", \"blockers\": []}\n```" if stream else "{}"
if not stream and os.environ.get("HARNESS_STARTCHECK") and any("pre-launch check" in a for a in args):
    text = os.environ["HARNESS_STARTCHECK"]  # opt-in: the can-this-start check's canned answer (a JSON {ok, missing})
failed = os.environ.get("HARNESS_AGENT") == "fail"
print(json.dumps({"type": "result", "subtype": "error" if failed else "success", "is_error": failed, "result": text,
                  "session_id": sid, "num_turns": 2, "duration_ms": 2000, "total_cost_usd": 0.02, "usage": usage}), flush=True)
sys.exit(1 if failed else 0)
