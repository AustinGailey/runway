#!/usr/bin/env python3
"""Generate a real Runway launch for tasks from the command line, with the page's own code.

    python3 harness/cli_launch.py RECORDS_DIR OUT_DIR TASK_JSON [TASK_JSON ...]

RECORDS_DIR holds the database records the page needs, as saved by ArtifactData reads: settings/main.json,
projects/<id>.json, pis/*.json, sprints/*.json. Each TASK_JSON is a task record (file name = task id). The tasks'
dependencies are seeded as done so the brief reads right.

It serves the built page in the harness seeded with those records, evaluates the page's planWaves, runnerScript and
launchCommand (so the run is exactly what the Launch button would start: sandbox settings, prompts, verifier), and
writes into OUT_DIR:
    chunk.json      the chunks/<id> record to set
    <task>.json     the tasks/<task> patch (status running, run fields) to update, one per task
    launch.sh       the command that writes run.sh under ~/.ai-planner/runs/<chunk>/ and starts it
Prints "<chunkId> ok". Nothing is written to the database or started: apply the records, then run launch.sh.
"""
import glob, json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    rec, out, task_paths = argv[0], argv[1], argv[2:]
    os.makedirs(out, exist_ok=True)
    load = lambda p: json.load(open(p))
    tasks = {os.path.basename(p)[:-5]: load(p) for p in task_paths}
    proj = next(iter(tasks.values()))["projectId"]
    seed = {"settings/main": load(os.path.join(rec, "settings", "main.json")),
            "projects/" + proj: load(os.path.join(rec, "projects", proj + ".json"))}
    for kind in ("pis", "sprints"):
        for f in glob.glob(os.path.join(rec, kind, "*.json")):
            seed[kind + "/" + os.path.basename(f)[:-5]] = load(f)
    for tid, t in tasks.items():
        for dep in t.get("deps") or []:
            if dep not in tasks:
                seed["tasks/" + dep] = {"projectId": proj, "title": dep, "status": "done", "piId": t.get("piId"),
                                        "sprintId": t.get("sprintId"), "order": 0, "deps": []}
        t = dict(t)
        t.pop("held", None)
        t.pop("run", None)  # a fresh launch: no previous attempt's run fields, and it starts from todo
        if t.get("status") != "paused":
            t["status"] = "todo"
        seed["tasks/" + tid] = t
    seedf = os.path.join(out, "seed.json")
    json.dump(seed, open(seedf, "w"))
    sandbox = os.path.join(out, "sandbox")
    os.makedirs(sandbox, exist_ok=True)
    for f in glob.glob(os.path.join(sandbox, "harness-db.json")):
        os.remove(f)
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "harness", "serve.py"), "--port", "0", "--sandbox", sandbox,
                            "--seed", seedf, "--tick", "0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        url = srv.stdout.readline().split()[1]
        js = ("(()=>{const sel=S.tasks.filter(t=>%s.includes(t.id)); const waves=planWaves(sel);"
              " const chunkId='ch'+new Date().toISOString().slice(0,16).replace(/[-:T]/g,'')+Math.random().toString(36).slice(2,5);"
              " const script=runnerScript(chunkId,waves); const at=now();"
              " return {chunkId, n: sel.length, cmd: launchCommand(chunkId, script), script,"
              " chunk:{name:'Runway · '+sel.length+' agent'+(sel.length>1?'s':''), createdAt: at, taskIds: sel.map(t=>t.id), estTotal: adjSum(sel),"
              " status:'running', launchedAt: at, at:null, waves: waves.map(w=>({ids:w.map(t=>t.id)})), updatedAt: at},"
              " patch: Object.fromEntries(sel.map(t=>[t.id,{status:'running', prevStatus:t.status, resumePrompt:null, held:false, updatedAt: at,"
              " run:{...RUN_RESET, chunkId, startedAt: at, estAtLaunch: t.estTokens||0, adjAtLaunch: Math.round(adjT(t)),"
              " factorAtLaunch: +factorFor(t).toFixed(3), profile: S.settings.leanAgents?'lean':'full', guardUsd: +guardUsd(t).toFixed(2),"
              " resumed:false, session:null, wt:'', prevActual:0, prevCost:0}}]))}})()") % json.dumps(list(tasks))
        steps = [{"goto": url}, {"waitForText": "Runway", "timeout": 15000},
                 {"eval": "(S.projectId=%s, subscribeTasks(%s), 1)" % (json.dumps(proj), json.dumps(proj))}, {"sleep": 900}, {"eval": js}]
        drv = os.path.join(out, "drive.out")  # a file, not a pipe: node's process.exit truncates large piped output
        with open(drv, "w") as fo:
            r = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps(steps),
                               stdout=fo, stderr=subprocess.PIPE, text=True, timeout=120)
    finally:
        srv.terminate()
    lines = [json.loads(l) for l in open(drv) if l.startswith("{")]
    if r.returncode or any(l.get("ok") is False for l in lines):
        sys.exit("launch generation failed:\n" + open(drv).read()[-2000:] + r.stderr[-500:])
    v = lines[4]["value"]
    if v["n"] != len(tasks):
        sys.exit("only %d of %d tasks were found in the seeded page" % (v["n"], len(tasks)))
    s = v["script"]
    if "/tmp/" in v["cmd"] or "runway-harness" in s or "$HOME/.ai-planner" not in s:
        sys.exit("sandbox paths leaked into the script")
    json.dump(v["chunk"], open(os.path.join(out, "chunk.json"), "w"))
    for tid, p in v["patch"].items():
        json.dump(p, open(os.path.join(out, tid + ".json"), "w"))
    open(os.path.join(out, "launch.sh"), "w").write(v["cmd"])
    print(v["chunkId"], "ok", "| sandboxed:", s.count(".settings.json") > 0, "| keychain sign-in:", "runway-claude-oauth" in s)


if __name__ == "__main__":
    main(sys.argv[1:])
