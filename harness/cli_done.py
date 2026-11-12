#!/usr/bin/env python3
"""Build the tasks/<id> patch that marks a task merged and done after a CLI merge (`runway.py merge CH TID`).

    python3 harness/cli_done.py TASK_JSON OUT_JSON "merge note"

The page only learns of merges it started itself, so every CLI merge needs this patch applied with ArtifactData
(update, pinned to the version TASK_JSON was read at); otherwise a stale Review card offers a Merge that fails.
"""
import json, sys, time


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    src, out, note = argv
    d = json.load(open(src))
    run = dict(d.get("run") or {})
    run.update(merging=False, err="", merged=True, mergeOutcome="merged", mergeErrCount=0, mergeNote=note)
    json.dump({"status": "done", "doneAt": int(time.time() * 1000), "prevStatus": d.get("status"), "run": run}, open(out, "w"))
    print("ok", out)


if __name__ == "__main__":
    main(sys.argv[1:])
