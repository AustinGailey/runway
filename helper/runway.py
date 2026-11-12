#!/usr/bin/env python3
"""Runway helper: runs on your Mac next to the Runway planner artifact.

tick                 launch due queued chunks, refresh usage and run status, write status.json
                     (the ai.runway.helper LaunchAgent runs this every minute; run.sh also calls it while agents run)
status [chunk...]    tick, then print the status JSON between @@BEGIN/@@END
launch <chunk>       start a chunk's run.sh in its own process group
queue <chunk> <at>   launch a chunk at epoch seconds <at> (needs the LaunchAgent)
unqueue <chunk>      cancel a queued launch
stop <chunk>         stop a running chunk's agents
env-gc               tear down environments whose worktree is gone or whose chunk ended over a day ago (tick does this hourly)
merge <chunk> <task> apply the agent's own changes to runway/<base>, then land them if safe
merge-batch <id> <b64 spec>  merge many tasks in order in the background, resolving conflicts with Claude
land <repo> <base>   merge runway/<base> into the checkout (git refuses on overlapping uncommitted edits)
discard <chunk> <task> delete a task's worktree and branch
automerge <chunk> <task> merge a green task into its base branch (under a lock), then delete its branch and worktree
export <b64json>     write a plan note into the vault and log it
notify on|off        turn Mac notifications on or off (a flag file in ~/.ai-planner)
notify routine on|off   also notify routine successes (chunk finished, task merged); off by default
notify handsoff on|off   Runway resolves routine outcomes itself: no ping for a usage-limit pause or a clean merge run, only for what needs Austin
lead <b64 spec>      the AI lead: one Sonnet call decides a routine decision blocker, or escalates it (money, clients, outside the repo, brand, irreversible)
lead-info            the AI lead's month spend and its escalation line (categories and keywords)
checks <chunk> <task> run the project's .runway.json checks in the task's worktree (red goes to failures.log and merge-train.log)
notify --dry-run     list the events tick would notify about, without posting or recording them
merge-log [N]        print the last N merge-train failures (default 10), grouped by chunk

Merge-train CLI (run `runway.py <command> -h` for examples):
run <chunk> <task> [--dry-run]     run a task's checks in its worktree; --dry-run prints the git and test commands
list [--repo PATH]                 task worktrees, active or stale
failures [-n N] [--chunk CHUNK]    the last N check failures (red or timed out), default 10
retry <chunk> <task> [--dry-run]   run a task again, only if it has a recorded failure
prune [--repo PATH] [--dry-run]    drop git's records of task worktrees whose directories are gone

Autopilot (opt-in; nothing starts it but this command):
autopilot [--repo PATH] [--parallel N]  work the roadmap's ready tasks through the merge gate, one at a time by default
env-info REPO                      a project's variable names and whether each is set (never the values), for the project form
retry-setup REPO TASK              run a task's failed setup again in its worktree, then start its agent
config-get REPO KEY                 one project setting's raw value from .runway.json ("" when unset), for the project form
config-set REPO KEY VALUE           set one project setting in .runway.json ("" clears it back to the default)

Roadmap CLI (run `runway.py roadmap <command> -h` for examples; --candidate works on the generated one):
roadmap generate [--brief FILE] [--goal TEXT] ...  plan the project with Claude and save a candidate for review
roadmap show [--candidate]         the roadmap as a tree, with token totals per sprint and warnings over the caps
roadmap edit [--candidate]         open the roadmap JSON in $EDITOR; it is saved only if it still validates
roadmap approve [--repo PATH]      make the candidate the project's one active roadmap (refused while autopilot runs)

Exit codes for those commands: 0 green or finished, 1 red, timed out or a git step failed,
2 usage error (bad arguments, unknown chunk or task, nothing to retry), 3 no checks configured.
"""
import argparse, base64, difflib, fcntl, glob, hashlib, json, math, os, random, re, shlex, shutil, signal, subprocess, sys, tempfile, time
from dataclasses import asdict, dataclass, field

VERSION = 60
HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".ai-planner")
# The claude command every agent and Claude call uses. RUNWAY_CLAUDE (opt-in) points it elsewhere, e.g. the harness's fake.
CLAUDE = os.environ.get("RUNWAY_CLAUDE") or "claude"
RUNS = os.path.join(BASE, "runs")
QUEUE = os.path.join(BASE, "queue")
STATUS = os.path.join(BASE, "status.json")
CACHE = os.path.join(BASE, "usage-cache.json")
PROJECTS = os.path.join(HOME, ".claude", "projects")
LIMIT_RE = re.compile(r"usage limit|hit your limit|limit reached|rate.?limit|resets? (at )?\d", re.I)


def rd(p, n=None):
    try:
        with open(p, errors="replace") as f:
            s = f.read()
        return s[-n:] if n else s
    except Exception:
        return None


def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


# ---------- usage from local Claude Code session logs ----------
def eff(u):
    return (u.get("input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0) + \
        (u.get("output_tokens", 0) or 0) + 0.1 * (u.get("cache_read_input_tokens", 0) or 0)


def scan_usage():
    try:
        cache = json.loads(rd(CACHE) or "{}")
    except Exception:
        cache = {}
    now = time.time()
    horizon = now - 8 * 86400
    seen = {}
    for path in glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mtime < horizon:
            continue
        c = cache.get(path) or {"off": 0, "ids": [], "b": {}}
        if st.st_size < c["off"]:
            c = {"off": 0, "ids": [], "b": {}}
        if st.st_size > c["off"]:
            ids = set(c["ids"])
            recent = list(c["ids"])
            with open(path, "rb") as f:
                f.seek(c["off"])
                data = f.read()
            # only consume complete lines
            end = data.rfind(b"\n") + 1
            for line in data[:end].splitlines():
                if b'"usage"' not in line or b'"assistant"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                m = d.get("message") or {}
                u = m.get("usage")
                if not u or d.get("type") != "assistant":
                    continue
                mid = (m.get("id") or "") + ":" + (d.get("requestId") or "")
                if mid in ids:
                    continue
                ids.add(mid)
                recent.append(mid)
                t = _utc(d.get("timestamp") or "")  # timestamps are UTC ISO strings
                if t is None:
                    continue
                h = str(int(t // 3600))
                c["b"][h] = c["b"].get(h, 0) + eff(u)
            c["off"] += end
            c["ids"] = recent[-400:]
        cutoff_h = int(horizon // 3600)
        c["b"] = {h: v for h, v in c["b"].items() if int(h) >= cutoff_h}
        seen[path] = c
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(seen, f)
    os.replace(tmp, CACHE)
    hours = {}
    for c in seen.values():
        for h, v in c["b"].items():
            hours[int(h)] = hours.get(int(h), 0) + v
    return hours


def _utc(ts):
    import calendar
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def summarize(hours):
    now = time.time()
    nh = int(now // 3600)
    # 5-hour blocks: a block starts at the hour of the first message after the previous block ended
    block = None
    start = None
    for h in sorted(hours):
        if hours[h] <= 0 or h < nh - 30:
            continue
        if start is None or h >= start + 5:
            start = h
    if start is not None and start + 5 > nh:
        used = sum(v for h, v in hours.items() if start <= h < start + 5)
        block = {"start": start * 3600, "end": (start + 5) * 3600, "used": round(used)}
    week = sum(v for h, v in hours.items() if h > nh - 7 * 24)
    last24 = [round(hours.get(h, 0)) for h in range(nh - 23, nh + 1)]
    # hourly effective tokens for the last 8 days, oldest first, so the page can count from your plan's real reset times
    h0 = nh - 191
    hours192 = [round(hours.get(h, 0)) for h in range(h0, nh + 1)]
    return {"block": block, "week": round(week), "last24": last24, "h0": h0, "hours": hours192, "at": int(now)}


# ---------- chunk status ----------
def _step(name, inp):
    """One line for a tool call: what the agent is doing right now."""
    inp = inp if isinstance(inp, dict) else {}
    if name == "TodoWrite":
        cur = [x for x in inp.get("todos") or [] if isinstance(x, dict) and x.get("status") == "in_progress"]
        if cur:
            return cur[0].get("activeForm") or cur[0].get("content") or "Planning"
        return "Updating its plan"
    if name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        f = inp.get("file_path") or inp.get("notebook_path") or ""
        return name + " " + os.path.basename(f)
    if name == "Bash":
        return inp.get("description") or "$ " + (inp.get("command") or "").split("\n")[0]
    if name in ("Grep", "Glob"):
        return name + " " + (inp.get("pattern") or "")
    if name in ("Task", "Agent"):
        return "Subagent: " + (inp.get("description") or "")
    if name in ("WebFetch", "WebSearch"):
        return name + " " + (inp.get("url") or inp.get("query") or "")
    return name


def stream_progress(path):
    """Live progress from a running agent's <task>.stream.jsonl: last tool step and tokens so far."""
    try:
        with open(path, errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    usage, step, msgs = {}, None, 0
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue  # the last line may still be half written
        if ev.get("type") != "assistant":
            continue
        m = ev.get("message") or {}
        # one API message arrives as several events (one per content block) carrying the same usage
        if m.get("id") and m["id"] not in usage:
            usage[m["id"]] = m.get("usage") or {}
            msgs += 1
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                step = _step(b.get("name") or "", b.get("input"))
    tot = {}
    for u in usage.values():
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            tot[k] = tot.get(k, 0) + (u.get(k, 0) or 0)
    return {"step": (step or "Starting")[:160], "tokens": round(eff(tot)), "turns": msgs,
            "at": int(os.path.getmtime(path))}


def stream_result(path):
    """The final result event from a stream, in case run.sh could not save <task>.json."""
    try:
        with open(path, errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"result"' in line:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "result":
                return line
    return None


def chunk_status(ch):
    d = os.path.join(RUNS, ch)
    c = {"exists": os.path.isdir(d), "finished": os.path.exists(os.path.join(d, "_chunk.done")),
         "stopped": os.path.exists(os.path.join(d, "_chunk.stopped")), "tasks": {}}
    q = os.path.join(QUEUE, ch)
    if os.path.exists(q):
        try:
            c["queuedAt"] = int(float(rd(q) or 0))
        except ValueError:
            pass
    for sf in glob.glob(os.path.join(d, "*.status")):
        tid = os.path.basename(sf)[:-7]
        p = lambda ext: os.path.join(d, tid + ext)
        t = {"state": (rd(sf) or "").strip()}
        ex = rd(p(".exit"))
        if ex is not None:
            try:
                t["exit"] = int(ex.strip())
            except Exception:
                t["exit"] = -1
        js = rd(p(".json"))
        if not (js or "").strip() and t["state"] != "running":
            js = stream_result(p(".stream.jsonl"))
        if t["state"] == "running":
            pr = stream_progress(p(".stream.jsonl"))
            st = rd(p(".start"))
            if pr is None and st:
                pr = {"step": "Starting", "tokens": 0, "turns": 0}
            if pr is not None:
                try:
                    pr["elapsed"] = int(time.time() - int(st.strip())) if st else None
                except ValueError:
                    pr["elapsed"] = None
                t["progress"] = pr
        if js:
            try:
                j = json.loads(js)
                u = j.get("usage") or {}
                # what the agent was blocked from doing: the key to debugging empty runs
                t["denials"] = [{"tool": d.get("tool_name"), "input": json.dumps(d.get("tool_input") or {})[:300]} for d in (j.get("permission_denials") or [])][:12]
                if j.get("subtype") == "error_max_budget_usd":
                    t["budget"] = True  # stopped by its --max-budget-usd guard; the session can be resumed
                keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
                usage, cost, turns, dur = {k: u.get(k, 0) for k in keys}, j.get("total_cost_usd") or 0, j.get("num_turns") or 0, j.get("duration_ms") or 0
                for rf in sorted(glob.glob(p(".r*.json"))):
                    try:
                        rj = json.loads(rd(rf) or "{}"); ru = rj.get("usage") or {}
                        for k in keys:
                            usage[k] += ru.get(k, 0) or 0
                        cost += rj.get("total_cost_usd") or 0; turns += rj.get("num_turns") or 0; dur += rj.get("duration_ms") or 0
                    except Exception:
                        pass
                t.update(is_error=j.get("is_error"), result=(j.get("result") or "")[:12000], cost=cost or None,
                         turns=turns, duration_ms=dur, session=j.get("session_id"), usage=usage)
                oc = parse_outcome(j.get("result") or "")  # read before truncation: the block is the report's last thing
                if oc:
                    t["outcome"] = oc
            except Exception as e:
                t["parse_error"] = str(e)[:200]
        er = rd(p(".err"), 8000)
        if er and er.strip():
            t["err"] = er
        if t.get("is_error") or (t.get("exit") not in (None, 0)):
            if LIMIT_RE.search((t.get("result") or "") + " " + (t.get("err") or "")):
                t["limited"] = True
        ck = rd(p(".checks.json"))
        if ck:
            try:
                t["checks"] = json.loads(ck)
                t["checks"]["tail"] = "\n".join((rd(p(".checks.log"), 4000) or "").splitlines()[-12:])
            except ValueError:
                pass
        diff = rd(p(".diff"), 3000)
        if diff:
            t["diff"] = diff
        vj = rd(p(".verify.json"))
        if vj:
            try:
                v = json.loads(vj)
                txt = v.get("result") or ""
                m = re.search(r"\{[\s\S]*\}", txt)
                verdict = json.loads(m.group(0)) if m else {"pass": None, "notes": txt[-400:]}
                vu = v.get("usage") or {}
                vt, vc = eff(vu), v.get("total_cost_usd") or 0
                for rf in glob.glob(p(".verify.r*.json")):
                    try:
                        rv = json.loads(rd(rf) or "{}"); vt += eff(rv.get("usage") or {}); vc += rv.get("total_cost_usd") or 0
                    except Exception:
                        pass
                vpass, unreadable = verdict.get("pass"), _unreadable_files(d, tid)
                t["verify"] = {"pass": vpass, "notes": str(verdict.get("notes", ""))[:2000], "tokens": round(vt), "cost": vc or None,
                               "unverifiable": bool(unreadable) and vpass is not True, "unreadable": unreadable[:20]}
            except Exception as e:
                t["verify"] = {"pass": None, "notes": "Verifier output unreadable: " + str(e)[:120]}
        rounds = (rd(p(".rounds")) or "").strip()
        if rounds.isdigit():
            t["rounds"] = int(rounds)
            hist = []
            for k in range(1, int(rounds) + 1):
                try:
                    hist.append(_verdict_of(rd(p(".verify.r%d.json" % k)) or "").get("notes", "")[:400])
                except Exception:
                    pass
            t["roundNotes"] = hist
        cr = (rd(p(".checksrounds")) or "").strip()
        if cr.isdigit():
            t["checksRounds"] = int(cr)
        cf = rd(p(".checksfix.json"))
        if cf:
            try:
                cfj = json.loads(cf)
                t["checksFix"] = {"stop": cfj.get("stop"), "baseRed": bool(cfj.get("baseRed")), "baseStep": cfj.get("baseStep"),
                                  "baseOnly": (cfj.get("split") or {}).get("baseOnly") or [], "flaky": (cfj.get("split") or {}).get("flaky") or [],
                                  "rounds": [{"step": r.get("step"), "tail": (r.get("tail") or "")[-1500:]} for r in cfj.get("rounds", [])]}
            except ValueError:
                pass
        fc = rd(p(".fixcap.json"))
        if fc:
            try:
                t["fixCap"] = json.loads(fc)
            except Exception:
                pass
        meta = rd(p(".meta"))
        if meta:
            try:
                t["meta"] = json.loads(meta)
            except Exception:
                pass
        patch = rd(p(".patch"))
        if patch is not None:
            # size of the agent's change, for estimate analysis
            files = add = rem = 0
            for line in patch.splitlines():
                if line.startswith("diff --git"):
                    files += 1
                elif line.startswith("+") and not line.startswith("+++"):
                    add += 1
                elif line.startswith("-") and not line.startswith("---"):
                    rem += 1
            t["diffstat"] = {"files": files, "add": add, "del": rem}
        tm = rd(p(".timing.json"))
        if tm:
            try:
                t["timing"] = json.loads(tm)
            except ValueError:
                pass
        su = rd(p(".setup.json"))
        if su:
            try:
                t["setup"] = {k: v for k, v in json.loads(su).items() if k in ("rc", "timedOut", "at")}
            except ValueError:
                pass
        if t["state"] == "done":
            t["signals"] = run_signals(t)
        lj = rd(p(".lease.json"))  # JOBS-CONTRACT.md: Running only while this lease is fresh and its process group lives
        if lj and t["state"] in LEASE_STATES:
            try:
                t["lease"] = json.loads(lj)
            except ValueError:
                pass
        if os.path.exists(p(".timedout")):
            t["timedOut"] = True
        tj = rd(p(".terminal.json"))
        if tj:
            try:
                t["terminal"] = json.loads(tj)
            except ValueError:
                pass
        elif t["state"] == "done":
            t["terminal"] = terminal_of(t)
        c["tasks"][tid] = t
    return c


# ---------- leases, hard timeout, reaper (~/Projects/video-studio/JOBS-CONTRACT.md) ----------
LEASE_S = 180              # a task's lease: renewed by every tick (60 s, LaunchAgent) while its process group lives
AGENT_TIMEOUT_S = 7200     # hard timeout of one agent run, unless the run folder's _timeout says otherwise
REAP_MAX_ATTEMPTS = 2      # a task whose run died this many times goes to needs-austin instead of back to the queue
LEASE_STATES = ("running", "verifying", "queued")


def terminal_of(t):
    """The one terminal state of a finished task (what the page shows: Done / Failed / Needs you / back in the queue)."""
    oc, den = t.get("outcome") or {}, t.get("denials") or []
    if t.get("timedOut"):
        return {"state": "handed-back", "reason": "hard timeout", "resume": "Stopped at the hard timeout; resume session %s." % t.get("session")}
    if t.get("budget") or t.get("limited"):
        return {"state": "handed-back", "reason": "budget guard" if t.get("budget") else "usage limit"}
    if oc.get("status") == "blocked":
        b = (oc.get("blockers") or [{}])[0]
        return {"state": "needs-austin", "reason": "agent blocked: " + str(b.get("type") or "?"), "ask": str(b.get("detail") or "See the agent's report.")[:400]}
    if den and oc.get("status") != "done" and (t.get("exit") not in (0, None) or t.get("is_error") or not (t.get("diffstat") or {}).get("files")):
        return {"state": "needs-austin", "reason": "permission denied: " + str(den[0].get("tool")), "blockingTool": den[0].get("tool"),
                "ask": "Needs %s (%s), which the sandbox denies: allow it for the project or run it in a chat." % (den[0].get("tool"), str(den[0].get("input"))[:120])}
    if t.get("exit") == 0 and not t.get("is_error"):
        return {"state": "done", "reason": oc.get("status") or "finished"}
    return {"state": "failed", "reason": (t.get("err") or t.get("parse_error") or "exit %s" % t.get("exit"))[-300:]}


def _pg_alive(pg):
    try:
        os.killpg(int(pg), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False


def _task_agent_pids(pg, folder):
    """The `claude` processes of one run's process group whose working folder is this task's worktree (or repo)."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,command="], capture_output=True, text=True).stdout
    want = os.path.realpath(folder) if folder else None
    pids = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or parts[1] != str(pg) or "claude" not in parts[2] or "run.sh" in parts[2] or "caffeinate" in parts[2]:
            continue
        cwd = subprocess.run(["lsof", "-a", "-p", parts[0], "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
        dirs = [os.path.realpath(l[1:]) for l in cwd.splitlines() if l.startswith("n")]
        if want is None or want in dirs:
            pids.append(int(parts[0]))
    return pids


def _write_once(path, obj):
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(obj, f)
    try:
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    finally:
        os.remove(tmp)


def lease_reap(now=None):
    """Every tick: renew the lease of each open task whose run's process group lives, stop agents past the hard timeout
    (SIGINT, then SIGTERM a minute later), and give every open task of a dead run exactly one terminal state:
    requeued (attempt + 1) or, after REAP_MAX_ATTEMPTS reaps of that task, needs-austin. Returns what it did."""
    now, acts = now or time.time(), []
    for d in glob.glob(os.path.join(RUNS, "ch*")):
        if os.path.exists(os.path.join(d, "_chunk.done")) or os.path.exists(os.path.join(d, "_chunk.stopped")):
            continue
        pg = (rd(os.path.join(d, "_pgid")) or "").strip()
        if not pg:
            continue
        alive = _pg_alive(pg)
        try:
            limit = int((rd(os.path.join(d, "_timeout")) or "").strip() or AGENT_TIMEOUT_S)
        except ValueError:
            limit = AGENT_TIMEOUT_S
        for sf in glob.glob(os.path.join(d, "*.status")):
            tid = os.path.basename(sf)[:-7]
            st = (rd(sf) or "").strip()
            if st not in LEASE_STATES:
                continue
            p = lambda ext: os.path.join(d, tid + ext)
            if alive:
                write_json_atomic(p(".lease.json"), {"until": int(now + LEASE_S), "pgid": int(pg), "at": int(now), "alive": True})
                try:
                    started = int((rd(p(".start")) or "0").strip() or 0)
                except ValueError:
                    started = 0
                if st != "running" or not started or now - started <= limit:
                    continue
                try:
                    meta = json.loads(rd(p(".meta")) or "{}")
                except ValueError:
                    meta = {}
                folder = os.path.join(meta["repo"], ".claude", "worktrees", meta["wt"]) if meta.get("repo") and meta.get("wt") else meta.get("repo")
                mark = p(".timedout")
                first = not os.path.exists(mark)
                pids = _task_agent_pids(pg, folder)
                if first:
                    with open(mark, "w") as f:
                        json.dump({"at": int(now), "limit": limit, "pids": pids}, f)
                sig = signal.SIGINT if first or now - os.path.getmtime(mark) < 60 else signal.SIGTERM
                for pid in pids:  # SIGINT first: the session stays resumable
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
                acts.append({"chunk": os.path.basename(d), "task": tid, "action": "timeout", "signal": int(sig), "pids": pids})
            else:
                prev = 0
                for f in glob.glob(os.path.join(RUNS, "*", tid + ".terminal.json")):
                    try:
                        prev += 1 if json.loads(rd(f) or "{}").get("reaped") else 0
                    except ValueError:
                        pass
                attempt = prev + 1
                cause = "its run (process group %s) died while the task was %s" % (pg, st)
                if attempt >= REAP_MAX_ATTEMPTS:
                    term = {"state": "needs-austin", "reaped": True, "attempt": attempt, "reason": "reaped: " + cause,
                            "ask": "The run died %d times (%s): relaunch it by hand or look at run %s." % (attempt, cause, os.path.basename(d))}
                else:
                    term = {"state": "requeued", "reaped": True, "attempt": attempt, "nextAttempt": attempt + 1, "reason": "reaped: " + cause}
                term["at"] = int(now)
                if _write_once(p(".terminal.json"), term):
                    with open(sf, "w") as f:
                        f.write("reaped")
                    try:
                        os.remove(p(".lease.json"))
                    except FileNotFoundError:
                        pass
                    acts.append({"chunk": os.path.basename(d), "task": tid, "action": term["state"], "attempt": attempt})
        if not alive and not any((rd(sf) or "").strip() in LEASE_STATES for sf in glob.glob(os.path.join(d, "*.status"))):
            open(os.path.join(d, "_chunk.stopped"), "w").close()  # nothing alive or open is left in this run
    if acts:
        try:
            with open(os.path.join(BASE, "reaper.log"), "a") as f:
                for a in acts:
                    f.write(json.dumps(dict(a, at=int(now))) + "\n")
        except OSError:
            pass
    return acts


# ---------- run signals: why a run ended the way it did, in fields that can be counted ----------
def _denied_command(d):
    """A denial as a short, countable key: the tool, plus the first two words of a Bash command."""
    tool = d.get("tool") or "?"
    if tool != "Bash":
        return tool
    m = re.search(r'"command":\s*"((?:[^"\\]|\\.)*)', d.get("input") or "")  # the input is cut at 300 chars: not always valid JSON
    cmd = m.group(1).encode().decode("unicode_escape", "replace") if m else ""
    words = re.sub(r"^(cd [^;&]+(&&|;)\s*)+", "", cmd.strip()).split()
    return "Bash(" + " ".join(words[:2]) + ")" if words else "Bash"


_READ_PATH_RE = re.compile(r'"(file_path|path|pattern)":\s*"((?:[^"\\]|\\.)*)')
_BASH_READ_CMDS = ("cat", "ls", "head", "find")


def _denied_read_path(d):
    """A path a denied Read/Glob/Grep call tried to reach, or a denied Bash cat/ls/head/find command read, from the
    (possibly cut-short at 300 chars) stored input."""
    tool, inp = d.get("tool") or "", d.get("input") or ""
    if tool in ("Read", "Glob", "Grep"):
        m = _READ_PATH_RE.search(inp)
        return m.group(2).encode().decode("unicode_escape", "replace") if m else None
    if tool == "Bash":
        m = re.search(r'"command":\s*"((?:[^"\\]|\\.)*)', inp)
        cmd = m.group(1).encode().decode("unicode_escape", "replace") if m else ""
        words = re.sub(r"^(cd [^;&]+(&&|;)\s*)+", "", cmd.strip()).split()
        if not words or words[0] not in _BASH_READ_CMDS:
            return None
        return next((w for w in words[1:] if not w.startswith("-")), None)
    return None


def _outside_repo(path, repo):
    """A path is worth suggesting only when it is absolute and outside the run's own repo."""
    p = os.path.expanduser(path or "").rstrip("/")
    if not p.startswith("/"):
        return False
    r = os.path.expanduser(repo or "").rstrip("/")
    return not (r and (p == r or p.startswith(r + "/")))


AUTH_FAIL_RE = re.compile(r"Failed to authenticate|OAuth session expired|Invalid bearer token|Not logged in|Please run /login", re.I)
KEYCHAIN_TOKEN = "runway-claude-oauth"  # the Keychain item run.sh reads: `claude setup-token`, stored with `security add-generic-password -U -s runway-claude-oauth -w`


def agent_env(claude_env=None):
    """The environment an agent gets: no API key (it would outrank the subscription token), and the long-lived token
    from the Keychain when one is stored. Never logs or returns the token itself anywhere else."""
    env = dict(claude_env if claude_env is not None else os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    if env.get("RUNWAY_NO_KEYCHAIN"):  # the harness and the tests never touch the real Keychain
        return env, False
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_TOKEN, "-w"], capture_output=True, text=True, timeout=10)
        tok = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        tok = ""
    if tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    return env, bool(tok)


def auth_check(claude=None, runner=subprocess.run):
    """Can an unattended agent sign in right now? One tiny haiku call with the same environment run.sh gives agents
    (a real call: `claude auth status` reports a stored token as signed in even when it is invalid). Cached for 10
    minutes in auth.json so a burst of launches makes one call. Returns {"ok", "via", "detail"}."""
    cache = os.path.join(BASE, "auth.json")
    try:
        c = json.loads(rd(cache) or "null")
        if c and time.time() - c.get("at", 0) < 600 and c.get("ok"):
            return c
    except json.JSONDecodeError:
        pass
    claude = claude or os.environ.get("RUNWAY_CLAUDE") or "claude"
    env, has_token = agent_env()
    via = "keychain token" if has_token else "/login session"
    try:
        r = runner([claude, "-p", "Reply with exactly: ok", "--model", "haiku", "--output-format", "json", "--tools", "",
                    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'], capture_output=True, text=True, timeout=90,
                   env=env, cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
        out = json.loads((r.stdout or "").strip().splitlines()[-1]) if (r.stdout or "").strip() else {}
        ok = not out.get("is_error") and r.returncode == 0
        detail = "" if ok else (out.get("result") or (r.stderr or "").strip()[-300:] or "exit %d" % r.returncode)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, IndexError) as e:
        ok, detail = False, str(e)[:300]
    res = {"ok": ok, "via": via, "detail": detail[:300], "at": time.time()}
    write_json_atomic(cache, res)
    return res


_REAL_AUTH_CHECK = auth_check  # tests stub auth_check; they reach the real one here


def run_signals(t):
    """One finished run as countable signals: the agent's own verdict, a single failure category, its blockers and
    denials, what the checks and verifier said, and readPaths: paths outside the run's repo that a denied Read,
    Glob, Grep or Bash cat/ls/head/find tried to reach, or that a blocker named. The category is the first that
    applies, most specific first: limit, budget, setup, auth, crash, blocked:<type>, partial, no-output, checks-red,
    verify-unverifiable, verify-fail, ok."""
    oc = t.get("outcome") or {}
    blockers = [{"type": b.get("type"), "command": (b.get("command") or "")[:120], "detail": (b.get("detail") or "")[:200],
                 "path": (b.get("path") or "")[:200]}
                for b in (oc.get("blockers") or [])][:5]
    denials = [_denied_command(d) for d in (t.get("denials") or [])]
    repo = (t.get("meta") or {}).get("repo") or ""
    read_paths = sorted({p for p in (
        [_denied_read_path(d) for d in (t.get("denials") or [])] + [b["path"] for b in blockers if b["path"]]
    ) if p and _outside_repo(p, repo)})[:10]
    checks = (t.get("checks") or {}).get("state")
    setup_rc = (t.get("setup") or {}).get("rc")
    exit_ = t.get("exit")
    status = oc.get("status")
    files = (t.get("diffstat") or {}).get("files")
    if t.get("limited"):
        cat = "limit"
    elif t.get("budget"):
        cat = "budget"
    elif setup_rc not in (None, 0) or (exit_ == 97 and "environment" in (t.get("err") or "").lower()):
        cat = "setup"
    elif (exit_ not in (None, 0) or t.get("is_error")) and AUTH_FAIL_RE.search(t.get("result") or ""):
        cat = "auth"
    elif exit_ not in (None, 0) or t.get("is_error"):
        cat = "crash"
    elif status in ("blocked", "partial") and blockers:
        cat = "blocked:" + (blockers[0]["type"] or "other")
    elif status == "partial":
        cat = "partial"
    elif files == 0:
        cat = "no-output"
    elif checks == "red" or checks == "timeout":
        cat = "checks-red"
    elif (t.get("verify") or {}).get("unverifiable"):
        cat = "verify-unverifiable"
    elif (t.get("verify") or {}).get("pass") is False:
        cat = "verify-fail"
    else:
        cat = "ok"
    err = (t.get("err") or "").strip()
    return {"category": cat, "agentStatus": status, "blockers": blockers, "blockerTypes": sorted({b["type"] for b in blockers if b["type"]}),
            "readPaths": read_paths,
            "denials": len(denials), "deniedTools": sorted(set(denials))[:8], "checks": checks, "verifyPass": (t.get("verify") or {}).get("pass"),
            "rounds": t.get("rounds") or 0, "exit": exit_, "startFallback": "was not found" in err,
            "err": err.splitlines()[0][:200] if err else ""}


def run_history(since=0):
    """Every finished run on this Mac, newest first, as compact records for analysis and backfill: the signals plus
    tokens, cost, turns, durations, size of change, and the timings of setup, tests and merge."""
    out = []
    for d in glob.glob(os.path.join(RUNS, "ch*")):
        if os.path.getmtime(d) < since:
            continue
        ch = os.path.basename(d)
        try:
            cs = chunk_status(ch)
        except Exception:
            continue
        for tid, t in cs["tasks"].items():
            if t.get("state") != "done":
                continue
            st = rd(os.path.join(d, tid + ".start"))
            try:
                start = int(st.strip()) if st else None
            except ValueError:
                start = None
            end = int(os.path.getmtime(os.path.join(d, tid + ".status")))
            out.append({"chunk": ch, "task": tid, "start": start, "end": end, "wallMs": (end - start) * 1000 if start else None,
                        "effTokens": round(eff(t.get("usage") or {})), "cost": t.get("cost"), "turns": t.get("turns"),
                        "apiMs": t.get("duration_ms"), "diffstat": t.get("diffstat"), "timing": t.get("timing"),
                        "base": (t.get("meta") or {}).get("base"), "dirty": (t.get("meta") or {}).get("dirty"),
                        **t.get("signals", {})})
    return sorted(out, key=lambda r: r.get("end") or 0, reverse=True)


def ensure_patches():
    """Older runs (before run.sh saved <task>.patch) get their full diff written once, for the Changes tab."""
    for mf in glob.glob(os.path.join(RUNS, "*", "*.meta")):
        pf = mf[:-5] + ".patch"
        if os.path.exists(pf):
            continue
        try:
            meta = json.loads(rd(mf) or "{}")
            if not meta.get("wt") or (rd(mf[:-5] + ".status") or "").strip() != "done":
                continue
            wtp = os.path.join(meta["repo"], ".claude", "worktrees", meta["wt"])
            if not os.path.isdir(wtp):
                continue
            git(wtp, "add", "-A")
            fork = _fork(meta, wtp, meta.get("base") or "HEAD")
            out = subprocess.run(["git", "-C", wtp, "diff", "--cached", fork], capture_output=True).stdout if fork else b""
            git(wtp, "reset", "-q")
            with open(pf, "wb") as f:
                f.write(out)
        except Exception:
            continue


def all_status(extra=()):
    chunks = {}
    cutoff = time.time() - 14 * 86400
    for d in glob.glob(os.path.join(RUNS, "*")):
        if os.path.isdir(d) and os.path.getmtime(d) >= cutoff:
            chunks[os.path.basename(d)] = None
    for ch in extra:
        chunks[ch] = None
    return {ch: chunk_status(ch) for ch in chunks}


# ---------- launching ----------
def _default_signals():
    """preexec_fn for run.sh: a helper started in the background (nohup ... &) has SIGINT ignored, and every process it
    starts inherits that; bash can't un-ignore a signal ignored on entry, so Stop and the lease reaper's SIGINT would
    never reach the agents. Start run.sh with SIGINT and SIGTERM at their defaults."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def launch(ch):
    d = os.path.join(RUNS, ch)
    if not os.path.exists(os.path.join(d, "run.sh")):
        return "missing run.sh"
    if os.path.exists(os.path.join(d, "_pgid")):
        return "already launched"
    log = open(os.path.join(d, "runner.log"), "ab")
    env = dict(os.environ, **run_env(ch)) if os.path.exists(os.path.join(d, ENV_SPEC)) else None  # a page chunk: unchanged
    p = subprocess.Popen(["/usr/bin/caffeinate", "-i", "/bin/bash", "run.sh"], cwd=d, stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True, env=env, preexec_fn=_default_signals)
    with open(os.path.join(d, "_pgid"), "w") as f:
        f.write(str(p.pid))
    try:
        os.remove(os.path.join(QUEUE, ch))
    except FileNotFoundError:
        pass
    try:  # the page sees the new chunk at once, not at the next tick
        live_write()
    except Exception:
        pass
    return "launched"


LIVE = os.path.join(BASE, "live.json")  # running chunks only, rewritten every few seconds while agents run (see live_write)
LIVE_KEEP = 600  # seconds a finished or stopped chunk stays in live.json


def live_write():
    """Write live.json: chunk_status for every chunk still running, and for those that finished or stopped in the last
    LIVE_KEEP seconds (so the page always sees how they ended, however its reads line up with this write), without the
    usage scan a tick does, so the page can show agents' progress within seconds. Cheap enough to run every few seconds."""
    out, now, cutoff = {}, time.time(), time.time() - 2 * 86400
    for d in glob.glob(os.path.join(RUNS, "ch*")):
        ended = [m for m in (os.path.join(d, "_chunk.done"), os.path.join(d, "_chunk.stopped")) if os.path.exists(m)]
        if ended and now - max(os.path.getmtime(m) for m in ended) > LIVE_KEEP:
            continue
        if not os.path.exists(os.path.join(d, "_pgid")) or os.path.getmtime(d) < cutoff:
            continue
        try:
            out[os.path.basename(d)] = chunk_status(os.path.basename(d))
        except Exception:
            pass
    for d in glob.glob(os.path.join(RUNS, "mc*")):  # a session's claim: no _pgid, and it ends with _chunk.done or _chunk.stopped
        ended = [m for m in (os.path.join(d, "_chunk.done"), os.path.join(d, "_chunk.stopped")) if os.path.exists(m)]
        if (ended and now - max(os.path.getmtime(m) for m in ended) > LIVE_KEEP) or os.path.getmtime(d) < cutoff:
            continue
        try:
            out[os.path.basename(d)] = chunk_status(os.path.basename(d))
        except Exception:
            pass
    write_json_atomic(LIVE, {"now": int(time.time()), "v": VERSION, "chunks": out, "claims": claims_all()})
    return out


def scrub(ch, tid, paths):
    """Delete files an agent left in its worktree and could not remove (agents never get rm): only paths inside that
    task's worktree, and only files git does not track (never committed work, never anything ignored or outside)."""
    try:
        _, wtp, _, _ = _wt(ch, tid)  # refuses a task that ran in the checkout itself
    except SystemExit as e:
        return str(e)
    wtp = os.path.realpath(wtp)
    untracked = set(git(wtp, "ls-files", "--others", "--exclude-standard")[1].splitlines())
    gone, kept = [], []
    for p in paths:
        rel = os.path.relpath(os.path.realpath(os.path.join(wtp, p)), wtp)
        if rel.startswith("..") or rel not in untracked or not os.path.isfile(os.path.join(wtp, rel)):
            kept.append(p)
            continue
        os.remove(os.path.join(wtp, rel))
        gone.append(rel)
    return "@@SCRUB " + json.dumps({"removed": gone, "kept": kept})


def launch_due():
    now = time.time()
    for q in glob.glob(os.path.join(QUEUE, "*")):
        try:
            at = float(rd(q) or 0)
        except ValueError:
            continue
        if at <= now:
            launch(os.path.basename(q))


def stop(ch):
    d = os.path.join(RUNS, ch)
    try:
        os.remove(os.path.join(QUEUE, ch))
    except FileNotFoundError:
        pass
    pg = rd(os.path.join(d, "_pgid"))
    if pg:
        try:
            os.killpg(int(pg), signal.SIGTERM)
            time.sleep(2)
            os.killpg(int(pg), signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            print("kill:", e)
    for sf in glob.glob(os.path.join(d, "*.status")):
        if (rd(sf) or "").strip() != "done":
            with open(sf, "w") as f:
                f.write("stopped")
    open(os.path.join(d, "_chunk.stopped"), "w").close()
    env_teardown(d)  # the agents are down, so the environment goes; the worktree stays, with the task's work
    try:
        live_write()
    except Exception:
        pass
    return "stopped"


# ---------- per-project test and merge config ----------
CONFIG_FILE = ".runway.json"  # at the repo root, committed with the project
CONFIG_KEYS = ("type", "test", "build", "baseBranch", "mergeStrategy", "syncStrategy", "checks", "setup", "teardown", "env", "ports")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # a variable name only, never a NAME=value pair
PORTS_MAX = 20  # the most ports one task may ask for
PROJECT_TYPES = {
    # a static site has no test suite by default; a helper's pytest run is its test
    "site": {"test": [], "build": None, "mergeStrategy": "squash"},
    "python": {"test": ["python3 -m pytest -q"], "build": None, "mergeStrategy": "merge"},
}
MERGE_STRATEGIES = ("squash", "merge")
SYNC_STRATEGIES = ("merge", "rebase")  # how a task's branch takes in new commits from its base before it merges


class ConfigError(ValueError):
    """A readable problem with a project's config. The message is shown to Austin as it is."""


def detect_type(repo):
    """python when the repo has Python packaging or Python files at the root or one folder down, otherwise site."""
    if any(os.path.exists(os.path.join(repo, f)) for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        return "python"
    if glob.glob(os.path.join(repo, "*.py")) or glob.glob(os.path.join(repo, "*", "*.py")):
        return "python"
    return "site"


def _check(src, values):
    """Validate the keys one source sets. Returns them cleaned up. src names the source in messages."""
    if not isinstance(values, dict):
        raise ConfigError("%s must be a JSON object such as {\"type\": \"python\"}, not %s." % (src, type(values).__name__))
    out = {}
    for key, v in values.items():
        if key not in CONFIG_KEYS:
            near = difflib.get_close_matches(key, CONFIG_KEYS, n=1)
            raise ConfigError('%s has an unknown key "%s"%s. Known keys: %s.' % (
                src, key, (' (did you mean "%s"?)' % near[0]) if near else "", ", ".join(CONFIG_KEYS)))
        if key == "type":
            if v not in PROJECT_TYPES:
                raise ConfigError('%s: "type" must be "site" or "python", not %s.' % (src, json.dumps(v)))
            out[key] = v
        elif key == "test":
            cmds = [v] if isinstance(v, str) else v
            if not isinstance(cmds, list) or not all(isinstance(c, str) and c.strip() for c in cmds):
                raise ConfigError('%s: "test" must be a command or a list of commands, not %s.' % (src, json.dumps(v)))
            out[key] = [c.strip() for c in cmds]
        elif key == "build":
            if v is not None and (not isinstance(v, str) or not v.strip()):
                raise ConfigError('%s: "build" must be one command, or null for no build check, not %s.' % (src, json.dumps(v)))
            out[key] = v.strip() if v else None
        elif key in ("setup", "teardown"):  # one command, or a list run in order; null (the default) means none
            cmds = [v] if isinstance(v, str) else v
            if v is not None and (not isinstance(cmds, list) or not cmds or not all(isinstance(c, str) and c.strip() for c in cmds)):
                raise ConfigError('%s: "%s" must be one command or a list of commands, or null for no %s step, not %s.' % (
                    src, key, key, json.dumps(v)))
            out[key] = None if v is None else v.strip() if isinstance(v, str) else [c.strip() for c in v]
        elif key == "env":  # variable NAMES the task may receive; the values live in ~/.ai-planner/env/<repo>.env
            if not isinstance(v, list) or not all(isinstance(n, str) and ENV_NAME.fullmatch(n) for n in v):
                raise ConfigError('%s: "env" must be a list of variable names such as ["DATABASE_URL"], not %s. '
                                  "Names only: put the values in ~/.ai-planner/env/<repo>.env." % (src, json.dumps(v)))
            if len(set(v)) != len(v):
                raise ConfigError('%s: "env" lists a name more than once: %s.' % (src, json.dumps(v)))
            out[key] = v
        elif key == "ports":  # how many ports the task needs; 0 (the default) means none
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= PORTS_MAX:
                raise ConfigError('%s: "ports" must be a whole number from 0 to %d, not %s.' % (src, PORTS_MAX, json.dumps(v)))
            out[key] = v
        elif key == "baseBranch":
            if v is None:
                out[key] = None
                continue
            if not isinstance(v, str) or not v.strip() or re.search(r"\s", v) or v.startswith("-"):
                raise ConfigError('%s: "baseBranch" must be a branch name such as "main", not %s.' % (src, json.dumps(v)))
            out[key] = v.strip()
        elif key == "mergeStrategy":
            if v not in MERGE_STRATEGIES:
                raise ConfigError('%s: "mergeStrategy" must be "squash" or "merge", not %s.' % (src, json.dumps(v)))
            out[key] = v
        elif key == "syncStrategy":
            if v not in SYNC_STRATEGIES:
                raise ConfigError('%s: "syncStrategy" must be "merge" or "rebase", not %s.' % (src, json.dumps(v)))
            out[key] = v
        elif key == "checks":  # the same shape load_checks reads from the worktree's copy of the file
            if not isinstance(v, list) or not all(isinstance(c, dict) and isinstance(c.get("cmd"), str) and c["cmd"].strip() for c in v):
                raise ConfigError('%s: "checks" must be a list of {"name", "cmd"} objects, not %s.' % (src, json.dumps(v)))
            out[key] = v
    return out


def _read_config_file(repo):
    path = os.path.join(repo, CONFIG_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            values = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError("%s is not valid JSON (line %d, column %d: %s)." % (CONFIG_FILE, e.lineno, e.colno, e.msg))
    except OSError as e:
        raise ConfigError("%s could not be read: %s." % (CONFIG_FILE, e.strerror))
    return _check(CONFIG_FILE, values)


def project_config(repo, overrides=None):
    """A project's test and merge settings, key by key: built-in defaults for its type, then <repo>/.runway.json,
    then overrides (the page's project record, when it sends one). Later sources win.
    The type picks the defaults, so it's settled first: an override's type beats the file's, which beats detection.
    Raises ConfigError with a message Austin can act on."""
    explicit = _read_config_file(repo)
    if overrides:
        explicit.update(_check("project settings", overrides))
    kind = explicit.get("type") or detect_type(repo)
    cfg = {"type": kind, "baseBranch": None, **PROJECT_TYPES[kind]}
    cfg.update(explicit)
    return cfg


def config_get(repo, key):
    """The raw value of one key in <repo>/.runway.json, or "" when it is not set there (never a type default: the
    project settings field shows only what is actually committed). Raises ConfigError with a message Austin can act on."""
    v = _read_config_file(repo).get(key)
    return "" if v is None else v if isinstance(v, str) else " && ".join(v)


def config_set(repo, key, value):
    """Set one key in <repo>/.runway.json, committed with the project, to the string the page's project settings sent
    (empty clears it back to the default). The same file project_config and _ap_prepare read, so a project's worktree
    setup command is configured here and nowhere else. Raises ConfigError with a message Austin can act on."""
    if key not in CONFIG_KEYS:
        raise ConfigError('"%s" is not a project setting. Known keys: %s.' % (key, ", ".join(CONFIG_KEYS)))
    explicit = _read_config_file(repo)
    if value == "":
        explicit.pop(key, None)
    else:
        explicit[key] = value
    checked = _check(CONFIG_FILE, explicit)
    with open(os.path.join(repo, CONFIG_FILE), "w") as f:
        json.dump(checked, f, indent=2)
        f.write("\n")
    return checked


# ---------- review ----------
def _wt(ch, tid):
    meta = json.loads(rd(os.path.join(RUNS, ch, tid + ".meta")) or "{}")
    repo, wt, base = meta.get("repo"), meta.get("wt"), meta.get("base")
    if not repo or not wt:
        raise SystemExit("@@ERR This task ran in the repo checkout, not a worktree. Its changes are already in " + str(repo))
    wtp = os.path.join(repo, ".claude", "worktrees", wt)
    if not os.path.isdir(wtp):
        raise SystemExit("@@ERR Worktree not found: " + wtp)
    _, branch = git(wtp, "rev-parse", "--abbrev-ref", "HEAD")
    return repo, wtp, branch, base


INTEG = os.path.join(BASE, "integration")


def _slug(repo, base):
    import hashlib
    h = hashlib.sha1((repo + "\0" + base).encode()).hexdigest()[:6]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(repo) + "-" + base) + "-" + h


def integration(repo, base):
    """The helper-owned worktree on runway/<base>, where approved agent work collects.
    It never touches the user's checkout."""
    branch = "runway/" + base
    slug = _slug(repo, base)
    wtp = os.path.join(INTEG, slug)
    if not os.path.isdir(wtp):
        os.makedirs(INTEG, exist_ok=True)
        rc, _ = git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + branch)
        args = ["worktree", "add", "-q", wtp, branch] if rc == 0 else ["worktree", "add", "-q", "-b", branch, wtp, base]
        rc, out = git(repo, *args)
        if rc:
            raise SystemExit("@@ERR Could not create the integration worktree: " + out[-300:])
        with open(os.path.join(INTEG, slug + ".json"), "w") as f:
            json.dump({"repo": repo, "base": base, "branch": branch, "wt": wtp}, f)
    _catch_up(wtp, base)
    return wtp, branch


def _catch_up(wtp, base):
    """Fast-forward runway/<base> to <base> when it is strictly behind (commits made on <base> directly) and its
    worktree is clean, so merges and their checks run on current code. Only ever a fast-forward; anything else is
    left alone for the normal sync."""
    try:
        if git(wtp, "status", "--porcelain")[1].strip():
            return
        if git(wtp, "merge-base", "--is-ancestor", "HEAD", base)[0] != 0:
            return  # runway/<base> has work of its own, or the branches diverged
        if git(wtp, "rev-parse", "HEAD")[1].strip() == git(wtp, "rev-parse", base)[1].strip():
            return
        git(wtp, "merge", "--ff-only", "-q", base)
    except Exception:
        pass


def _fork(meta, wtp, base):
    """The commit the agent's own work starts from. Candidates: the start commit recorded at launch
    (or the worktree's first reflog entry for older runs) and the merge-base with the base branch,
    which differs when the agent pulled the base branch in itself. The one leaving the smallest diff wins.
    Call after `git add -A` so uncommitted work counts."""
    cands = []
    if meta.get("fork"):
        cands.append(meta["fork"])
    else:
        _, out = git(wtp, "reflog", "--format=%H", "HEAD")
        lines = [l for l in out.splitlines() if l.strip()]
        if lines:
            cands.append(lines[-1])
    rc, mb = git(wtp, "merge-base", "HEAD", base)
    if rc == 0 and mb.strip():
        cands.append(mb.strip())
    best, best_n = None, None
    for c in dict.fromkeys(cands):
        rc, names = git(wtp, "diff", "--cached", "--name-only", c)
        if rc:
            continue
        n = len([l for l in names.splitlines() if l.strip()])
        if best_n is None or n < best_n:
            best, best_n = c, n
    return best


def _overlap(repo, base):
    """Incoming files on runway/<base> that also have uncommitted edits (or untracked files) in the checkout."""
    branch = "runway/" + base
    rc, incoming = git(repo, "diff", "--name-only", base + "..." + branch)
    if rc:
        return []
    # -z output: NUL-separated "XY path" entries, never trimmed or quoted; a rename adds one extra entry (the old path),
    # which is also dirty (its deletion is an uncommitted change), so it counts too
    raw = subprocess.run(["git", "-C", repo, "status", "--porcelain", "-z", "--untracked-files=all"], capture_output=True, text=True).stdout
    dirty, parts, i = set(), raw.split("\0"), 0
    while i < len(parts):
        e = parts[i]
        if len(e) > 3:
            dirty.add(e[3:])
            if e[0] in "RC" and i + 1 < len(parts):
                i += 1
                dirty.add(parts[i])
        i += 1
    return sorted(set(f for f in incoming.splitlines() if f.strip()) & dirty)


LOCKFILES = {"package-lock.json": ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"]}


def _porcelain(repo):
    """[(XY, path)] from git status -z, untouched by trimming."""
    raw = subprocess.run(["git", "-C", repo, "status", "--porcelain", "-z", "--untracked-files=all"], capture_output=True, text=True).stdout
    out, parts, i = [], raw.split("\0"), 0
    while i < len(parts):
        e = parts[i]
        if len(e) > 3:
            out.append((e[:2], e[3:]))
            if e[0] in "RC" and i + 1 < len(parts):
                i += 1
        i += 1
    return out


def checkout_health(repo, base):
    """Everything that decides how a land can go, in one place."""
    branch = "runway/" + base
    st = _porcelain(repo)
    conflicted = sorted(p for xy, p in st if xy in ("UU", "AA", "DD", "AU", "UA", "DU", "UD"))
    untracked = {p for xy, p in st if xy == "??"}
    tracked_dirty = {p for xy, p in st if xy != "??" and p not in conflicted}
    rc, incoming = git(repo, "diff", "--name-only", base + "..." + branch)
    inc = {f for f in incoming.splitlines() if f.strip()} if rc == 0 else set()
    _, stashes = git(repo, "stash", "list", "--format=%gd|%gs")
    leftover = [l.split("|", 1) for l in stashes.splitlines() if "runway-land" in l]
    merging = os.path.exists(os.path.join(repo, ".git", "MERGE_HEAD"))
    return {"conflicted": conflicted, "merging": merging,
            "untrackedBlocking": sorted(inc & untracked), "overlap": sorted(inc & tracked_dirty),
            "dirtyCount": len(tracked_dirty) + len(untracked), "stashes": [{"ref": a, "label": b} for a, b in leftover][:5]}


def sync_integration(repo, base, log=lambda m: None, resolve=False):
    """Bring runway/<base> up to date with <base>. Lockfile conflicts are regenerated from the merged manifest.
    Other conflicts (your own commits on <base> touching what approved work touched) go to Claude when `resolve`
    is set; otherwise the merge is aborted and (None, msg) says they can be resolved in the background."""
    iw, ibranch = integration(repo, base)
    _, pre = git(iw, "rev-parse", "HEAD")
    rc, out = git(iw, "merge", "--no-edit", "-q", base)
    if rc == 0:
        return True, ""
    _, un = git(iw, "diff", "--name-only", "--diff-filter=U")
    files = [f for f in un.splitlines() if f.strip()]
    locks = [f for f in files if os.path.basename(f) in LOCKFILES]
    other = [f for f in files if f not in locks]
    if other and not resolve:
        git(iw, "merge", "--abort")
        return None, "Your commits on %s and approved Runway work both changed %s." % (base, ", ".join(other[:6]))
    said = ""
    if other:
        log("combining " + ", ".join(other))
        ok, tokens, cost, said = _resolve(iw, other, None, None, log, intro=(
            "You are resolving a git merge conflict in this repository. The branch checked out here, " + ibranch + ", holds "
            "Claude Code agents' work that the owner has already reviewed and approved. The incoming side is the owner's own "
            "newer commits on " + base + ". Produce what the owner would want now: the approved work stays, and each of the "
            "owner's edits is kept where the thing it describes still exists after the approved work. If the approved work "
            "deliberately removed or replaced that thing (a page, a feature, a section about something that is gone), do not "
            "bring the old material back just to hold the owner's edit; leave it out. Never re-add text the approved work "
            "removed unless the owner's edit changed that exact text and it still applies. In your reply, list each owner "
            "edit as KEPT or DROPPED with a few words why. Do not touch any other file."))
        if not ok:
            git(iw, "merge", "--abort")
            return False, "Claude couldn't combine %s (%s). Nothing changed." % (", ".join(other[:6]), said or "no answer")
    if locks:
        env = dict(os.environ, NODE_ENV="development", PATH="/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", ""))
        for f in locks:
            git(iw, "checkout", "--theirs", f)
            d = os.path.join(iw, os.path.dirname(f))
            r = subprocess.run(LOCKFILES[os.path.basename(f)], cwd=d, capture_output=True, text=True, env=env, timeout=600)
            if r.returncode:
                git(iw, "merge", "--abort")
                return False, "Couldn't regenerate %s: %s" % (f, (r.stderr or r.stdout)[-2000:])
            git(iw, "add", f)
    rc, o = git(iw, "commit", "-q", "--no-edit")
    if rc == 0 and other:
        _, head = git(iw, "rev-parse", "HEAD")
        with open(_review_path(repo, base), "w") as f:  # landing waits for the owner to look at what Claude combined
            json.dump({"pre": pre.strip(), "head": head.strip(), "files": other, "said": said.strip(), "at": int(time.time())}, f)
    if rc == 0:
        notes = (["Claude combined your edits with approved work in %s: %s" % (", ".join(other), said.strip())] if other else []) \
            + (["Regenerated " + ", ".join(locks) + " from the merged package.json."] if locks else [])
        log("; ".join(notes))
        return True, " ".join(notes)
    git(iw, "merge", "--abort")
    return False, "%s and %s conflict in %s. Nothing changed." % (ibranch, base, ", ".join(files[:6]) or "some files")


def set_aside(repo, base, b64paths):
    """Move the user's untracked copies of files Runway is about to land into a backup folder (never deletes)."""
    paths = json.loads(base64.b64decode(b64paths).decode())
    h = checkout_health(repo, base)
    allowed = set(h["untrackedBlocking"])
    dest = os.path.join(BASE, "backups", time.strftime("%Y%m%d-%H%M%S") + "-set-aside-" + _slug(repo, base))
    moved = []
    for rel in paths:
        if rel not in allowed or ".." in rel.split("/"):
            continue
        src = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        os.replace(src, os.path.join(dest, rel))
        moved.append(rel)
    if not moved:
        return "@@ERR Nothing to set aside."
    return "@@OK Set aside %d file%s in %s" % (len(moved), "" if len(moved) == 1 else "s", dest)


def commit_wip(repo, base, b64msg):
    """Commit everything the user has uncommitted (git add -A, honouring .gitignore). Refuses on conflicts,
    on a different branch, and on anything large enough to bloat the repo."""
    msg = base64.b64decode(b64msg).decode().strip() or "Work in progress"
    _, cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if cur != base:
        return "@@ERR Your checkout is on '%s', not %s." % (cur, base)
    h = checkout_health(repo, base)
    if h["conflicted"]:
        return "@@ERR Resolve the conflict markers first: " + ", ".join(h["conflicted"][:6])
    _, dry = git(repo, "add", "-A", "--dry-run")
    paths = [l[5:-1] for l in dry.splitlines() if l.startswith("add '")]
    big, total = [], 0
    for rel in paths:
        try:
            sz = os.path.getsize(os.path.join(repo, rel))
        except OSError:
            continue
        total += sz
        if sz > 50 * 1024 * 1024:
            big.append("%s (%d MB)" % (rel, sz // 1048576))
    if big:
        return "@@ERR Too large to commit (over 50 MB): " + ", ".join(big[:5]) + ". Add them to .gitignore first."
    if total > 300 * 1024 * 1024:
        return "@@ERR That would add %d MB to the repo. Check .gitignore first." % (total // 1048576)
    git(repo, "add", "-A")
    rc, out = git(repo, "commit", "-q", "-m", msg)
    if rc:
        return "@@ERR " + out[-2000:]
    _, sha = git(repo, "rev-parse", "--short", "HEAD")
    return "@@OK Committed %d file%s as %s." % (len(paths), "" if len(paths) == 1 else "s", sha)


def resolve_checkout(repo, base):
    """Resolve conflict markers left in the user's checkout by an earlier land: back the files up, let Claude
    combine both sides (edits limited to those files), verify no markers remain and JSON parses, then mark resolved."""
    h = checkout_health(repo, base)
    files = h["conflicted"]
    if not files:
        return "@@OK Nothing to resolve."
    dest = os.path.join(BASE, "backups", time.strftime("%Y%m%d-%H%M%S") + "-conflicts-" + _slug(repo, base))
    for rel in files:
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        try:
            with open(os.path.join(repo, rel), "rb") as a, open(os.path.join(dest, rel), "wb") as b:
                b.write(a.read())
        except OSError:
            pass
    prompt = ("These files in this repository contain git conflict markers (<<<<<<< ======= >>>>>>>) left by a merge: "
              + ", ".join(files) + ". One side ('Updated upstream') is approved work that already landed; the other "
              "('Stashed changes') is the developer's own uncommitted work. Resolve every conflict so BOTH sides' intent "
              "is kept: keep both sets of lines, scripts, config keys or imports, and where they truly change the same "
              "thing, combine them so the code still works with what landed (read the surrounding code to check). Edit only "
              "the conflicted regions of these files. Keep JSON valid. Reply with one line per file saying what you combined.")
    env = dict(os.environ, PATH="/opt/homebrew/bin:/usr/local/bin:" + os.path.expanduser("~/.local/bin") + ":" + os.environ.get("PATH", ""))
    try:
        p = subprocess.run([CLAUDE, "-p", prompt, "--model", "sonnet", "--output-format", "json", "--permission-mode", "acceptEdits",
                            "--tools", "Read", "Edit", "Grep", "Glob", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
                           cwd=repo, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL, env=env)
        said = (json.loads(p.stdout or "{}").get("result") or "")[-600:]
    except Exception as e:
        return "@@ERR Claude couldn't run: %s. Your files are unchanged; backups in %s" % (e, dest)
    bad = []
    for rel in files:
        txt = rd(os.path.join(repo, rel)) or ""
        if CONFLICT_RE.search(txt):
            bad.append(rel + " (markers left)")
        elif rel.endswith(".json"):
            try:
                json.loads(txt)
            except Exception:
                bad.append(rel + " (invalid JSON)")
    if bad:
        return "@@ERR Not fully resolved: %s. Nothing was marked resolved; originals are in %s" % (", ".join(bad), dest)
    git(repo, "add", "--", *files)
    return "@@OK Resolved %s. %s Originals kept in %s" % (", ".join(files), said.strip().replace("\n", " ")[:300], dest)


def land_with_stash(repo, base):
    """Save the user's uncommitted work (backup patch + stash), land, then re-apply it."""
    branch = "runway/" + base
    co = checkout_for(repo, base)
    if co is None:
        return False, "%s isn't checked out anywhere, so there are no edits to re-apply. Press Land." % base
    h = checkout_health(co, base)
    if h["conflicted"] or h["merging"]:
        return False, "Your checkout still has unresolved conflicts from an earlier land (%s). Resolve those first." % ", ".join(h["conflicted"][:6])
    ok, note = sync_integration(repo, base)
    if not ok:
        return False, note
    repo_ = repo
    repo = co  # everything below works in the folder that has <base> checked out
    ts = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BASE, "backups")
    os.makedirs(bdir, exist_ok=True)
    backup = os.path.join(bdir, "%s-%s.patch" % (ts, _slug(repo_, base)))
    with open(backup, "wb") as f:
        f.write(subprocess.run(["git", "-C", repo, "diff", "--binary", "HEAD"], capture_output=True).stdout)
    rc, out = git(repo, "stash", "push", "--include-untracked", "-m", "runway-land " + ts)
    if rc:
        return False, "Could not stash your edits, nothing changed: " + out[-2000:]
    stashed = "No local changes" not in out
    rc, out = git(repo, "merge", "--no-ff", "--no-edit", "-m", "Merge Runway work from " + branch, branch)
    if rc:
        git(repo, "merge", "--abort")
        if stashed:
            git(repo, "stash", "pop")
        return False, "Landing hit a conflict, so nothing changed and your edits are back as they were. Run: git merge " + branch
    if not stashed:
        return True, "Landed in %s." % base
    rc, out = git(repo, "stash", "pop")
    if rc == 0:
        return True, "Landed in %s and re-applied your uncommitted edits. Backup: %s" % (base, backup)
    _, unmerged = git(repo, "diff", "--name-only", "--diff-filter=U")
    files = ", ".join([f for f in unmerged.splitlines() if f.strip()][:6]) or "some files"
    return True, ("Landed in %s. Your edits collided with it in %s: those files now have conflict markers to resolve. "
                  "Your edits are also kept in the stash (git stash list) and in %s." % (base, files, backup))


def _review_path(repo, base):
    os.makedirs(INTEG, exist_ok=True)
    return os.path.join(INTEG, _slug(repo, base) + ".review.json")


def pending_review(repo, base):
    """What Claude combined on runway/<base> that the owner hasn't looked at yet (None once landed or undone)."""
    r = json.loads(rd(_review_path(repo, base)) or "null")
    if not r:
        return None
    _, d = git(repo, "diff", base, "runway/" + base, "--", *r["files"])
    r["diff"] = d[:20000]
    return r


def land_undo(repo, base):
    """Take back Claude's combine on runway/<base>: the integration branch returns to before your commits were merged in."""
    r = json.loads(rd(_review_path(repo, base)) or "null")
    if not r:
        return False, "Nothing to undo."
    iw, ibranch = integration(repo, base)
    _, head = git(iw, "rev-parse", "HEAD")
    if head.strip() != r["head"]:
        return False, "More work was merged onto %s since, so the combine can't be taken back on its own." % ibranch
    git(iw, "reset", "-q", "--hard", r["pre"])  # helper-owned integration worktree only
    os.remove(_review_path(repo, base))
    return True, "Undone. %s is back to the approved work only; your %s is untouched." % (ibranch, base)


def checkout_for(repo, base):
    """The folder where <base> is checked out: the repo itself, another worktree of it, or None."""
    _, out = git(repo, "worktree", "list", "--porcelain")
    path = None
    for l in out.splitlines():
        if l.startswith("worktree "):
            path = l[9:].strip()
        elif l.strip() == "branch refs/heads/" + base and path and os.path.isdir(path):
            return path
    return None


def _checked_out(repo, base):
    return checkout_for(repo, base) is not None


def land(repo, base, resolve=False, confirm=False):
    """Merge runway/<base> into <base>. When the checkout is on <base>, git merges there and refuses on its own,
    changing nothing, when uncommitted edits overlap. When <base> isn't checked out anywhere, it is fast-forwarded."""
    branch = "runway/" + base
    _, cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    co = repo if cur == base else checkout_for(repo, base)
    where = "" if co == repo else " (checked out at %s)" % co
    if co is None:
        ok, note = sync_integration(repo, base, resolve=resolve)
        if ok is None:
            return None, note
        if not ok:
            return False, "Saved on %s. %s" % (branch, note)
        if pending_review(repo, base) and not confirm:
            return False, "Saved on %s. %s Review what Claude combined, then land." % (branch, note)
        rc, _ = git(repo, "merge-base", "--is-ancestor", base, branch)
        if rc:
            return False, "Saved on %s, but it doesn't contain all of %s, so it can't fast-forward." % (branch, base)
        _, old = git(repo, "rev-parse", base)
        _, new = git(repo, "rev-parse", branch)
        if old.strip() == new.strip():
            return True, "Already in %s." % base
        rc, out = git(repo, "update-ref", "refs/heads/" + base, new.strip(), old.strip())
        if rc:
            return False, "Saved on %s. Couldn't move %s: %s" % (branch, base, out[-2000:])
        try:
            os.remove(_review_path(repo, base))
        except OSError:
            pass
        return True, "Landed in %s. Your checkout stays on %s." % (base, cur)
    h = checkout_health(co, base)
    if h["conflicted"] or h["merging"]:
        return False, "Saved on %s. Your checkout still has unresolved conflicts from an earlier land (%s), so nothing was landed." % (branch, ", ".join(h["conflicted"][:6]))
    if h["untrackedBlocking"]:
        return False, "Saved on %s. You have your own untracked copies of %s; set them aside first." % (branch, ", ".join(h["untrackedBlocking"][:6]))
    ok, note = sync_integration(repo, base, resolve=resolve)
    if ok is None:
        return None, note
    if not ok:
        return False, "Saved on %s. %s" % (branch, note)
    synced = note
    if pending_review(repo, base) and not confirm:
        return False, "Saved on %s. %s Review what Claude combined, then land." % (branch, note or "Claude combined your edits with approved work.")
    rc, ahead = git(repo, "rev-list", "--count", base + ".." + branch)
    if rc or ahead.strip() == "0":
        return True, "Already in %s." % base
    rc, out = git(co, "merge", "--no-ff", "--no-edit", "-m", "Merge Runway work from " + branch, branch)
    if rc == 0:
        try:
            os.remove(_review_path(repo, base))
        except OSError:
            pass
        return True, ("Landed in %s%s." % (base, where)) + (" " + synced if synced else "")
    if "would be overwritten" in out or "not possible because you have unmerged" in out:
        files = [l.strip() for l in out.splitlines() if l[:1] in ("\t", " ") and l.strip() and not l.strip().startswith(("Please", "Aborting", "Merge"))][:6]
        return False, "Saved on %s. Not landed in %s%s yet: your uncommitted edits touch %s. Commit those, then press Land." % (branch, base, where, ", ".join(files) or "the same files")
    git(co, "merge", "--abort")
    return False, "Saved on %s. Landing in %s%s hit a conflict, so nothing changed in your checkout. Run: git merge %s" % (branch, base, where, branch)


MERGES = os.path.join(BASE, "merges")
CONFLICT_RE = re.compile(r"^(<{7} |={7}$|>{7} )", re.M)


def _title(ch, tid):
    t = (rd(os.path.join(RUNS, ch, tid + ".title")) or "").strip()
    if t:
        return t
    for line in (rd(os.path.join(RUNS, ch, tid + ".prompt.md")) or "").splitlines():
        if line.startswith("TASK: "):
            return line[6:].strip()
    return tid


def _broken_after_resolve(f, txt):
    """Why a file Claude resolved is still broken, or None. Markers, invalid JSON, or Python that no longer
    compiles (a stray '>' from a marker once slipped through and broke the helper)."""
    if CONFLICT_RE.search(txt):
        return "markers left in " + f
    if f.endswith(".json"):
        try:
            json.loads(txt)
        except Exception:
            return f + " is not valid JSON after resolving"
    if f.endswith(".py"):
        try:
            compile(txt, f, "exec")
        except SyntaxError as e:
            return "%s no longer compiles after resolving (line %s: %s)" % (f, e.lineno, e.msg)
    return None


def _resolve(iw, files, ch, tid, log, intro=None, deleted=None):
    """Ask Claude to resolve conflict markers in the integration worktree. Returns (ok, tokens, cost)."""
    brief = (rd(os.path.join(RUNS, ch, tid + ".prompt.md")) or "")[:4000] if ch else ""
    _, recent = git(iw, "log", "--format=- %s", "-8")
    prompt = intro + "\n\nFiles with conflicts: " + ", ".join(files) + ". Edit only the conflicted regions; remove every marker; keep JSON valid; run `git add` on each file; reply with one line saying what you combined.\n\nRecent approved work on this branch:\n" + recent if intro else (
        "You are resolving a git merge conflict in this repository (an integration branch that collects work from "
        "several Claude Code agents). The files below contain conflict markers (<<<<<<< ======= >>>>>>>). One side is "
        "work already approved on this branch; the other side is the incoming task described below.\n\n"
        "Resolve each conflict so BOTH sides' intent is kept (for example, keep both new npm scripts, both imports, "
        "both config keys). Edit ONLY the conflicted regions in these files: " + ", ".join(files) + ". "
        "Do not refactor, reformat or touch any other file. Remove every conflict marker. Make sure JSON stays valid. "
        "When done, run `git add` on each file. Reply with one line saying what you combined.\n\n"
        "Recent approved work on this branch:\n" + recent + "\n\nIncoming task brief:\n" + brief)
    if deleted:
        prompt += ("\n\nSome conflicts are about a deleted file (no markers): " + "; ".join(
            "%s (%s)" % (f, "the incoming task deleted it, approved work changed it" if c in ("UD",) else "approved work deleted it, the incoming task changed it" if c == "DU" else "deleted on both sides")
            for f, c in deleted.items()) + ". Decide from the brief and the code: if the deleting side replaced the file (moved or folded it "
            "into another file), carry the other side's changes into the replacement and run `git rm` on the old file; if the file "
            "is still needed, keep it with both sides' changes and `git add` it. Say which you did.")
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
    try:
        p = subprocess.run([CLAUDE, "-p", prompt, "--model", "sonnet", "--output-format", "json", "--permission-mode", "acceptEdits",
                            "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep", "Bash(git add:*)", "Bash(git rm:*)", "Bash(git diff:*)", "Bash(git status:*)", "Bash(git show:*)", "Bash(git log:*)"],
                           cwd=iw, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL, env=env)
        j = json.loads(p.stdout or "{}")
    except Exception as e:
        log("resolver failed: %s" % e)
        return False, 0, 0, ""
    tokens, cost, said = round(eff(j.get("usage") or {})), j.get("total_cost_usd") or 0, (j.get("result") or "")[-300:]
    for f in files:
        if not os.path.exists(os.path.join(iw, f)):
            git(iw, "rm", "-q", "--cached", "--ignore-unmatch", "--", f)  # resolved by deleting it
            continue
        bad = _broken_after_resolve(f, rd(os.path.join(iw, f)) or "")
        if bad:
            return False, tokens, cost, bad
        git(iw, "add", "--", f)
    _, unmerged = git(iw, "diff", "--name-only", "--diff-filter=U")
    if unmerged.strip():
        return False, tokens, cost, "still unmerged: " + unmerged.replace("\n", ", ")
    return True, tokens, cost, said


def _gate(ch, tid, target):
    """The merge gate: None when the task's checks are green, else why it must not merge. Runs the checks if none ran yet.
    The checks come from the merge target branch (see checks_source)."""
    p = os.path.join(RUNS, ch, tid + ".checks.json")
    if not os.path.exists(p):
        run_checks(ch, tid, target)
    st = json.loads(rd(p) or "{}")
    if st.get("state") == "green":
        return None
    if st.get("state") == "none":
        return None  # no checks configured: merge as before. Checks gate a merge only when a project defines them.
    where = ": " + st["step"] if st.get("step") else ""
    return "Its checks did not pass (%s%s). Fix it and run the task again, or run checks. The worktree is kept." % (st.get("state") or "not run", where)


def _run_checks_in(where, checks, log):
    """Run checks in a folder, appending output to log. Returns the first check that fails, or None if all pass."""
    open(log, "w").close()
    for c in checks:
        rc, timed_out = _run_check(c["cmd"], where, c["timeout"], log)
        if rc != 0:
            return dict(c, rc=rc, timedOut=timed_out)
    return None


FLAKY_RERUNS = 2  # a failed pytest test is rerun on its own up to this many times before it counts as a real failure


def flaky_path():
    return os.path.join(BASE, "flaky.json")


def failed_test_ids(text):
    """The test ids pytest's short summary names (FAILED / ERROR path::name - reason), in order, without repeats."""
    ids = []
    for m in re.finditer(r"^(?:FAILED|ERROR) (\S.*?)(?: - .*)?$", text or "", re.M):
        i = m.group(1).strip()
        if i and i not in ids:
            ids.append(i)
    return ids


def targeted_cmd(cmd, iw, repo, ids):
    """The check's command narrowed to these test ids, or None when it is not a pytest check we can narrow.
    A pytest command keeps its interpreter; ./test.sh gets the ids when it passes its arguments straight through to
    pytest, else the project's .venv python runs pytest on them."""
    quoted = " ".join(shlex.quote(i) for i in ids)
    words = cmd.split()
    if words and os.path.basename(words[0]) == "test.sh":
        script = rd(os.path.join(iw, "test.sh")) or ""
        passes = any('"$@"' in l and not re.search(r"(?<![\w./-])tests?(?![\w./-])", l) for l in script.splitlines())
        if passes:
            return "./test.sh " + quoted
        for root in (iw, repo):
            py = os.path.join(root, ".venv", "bin", "python")
            if os.path.exists(py):
                return "%s -m pytest -q %s" % (shlex.quote(py), quoted)
        return None
    m = re.search(r"(?<![\w.-])pytest(?![\w.-])", cmd)
    return "%s %s" % (cmd[:m.end()], quoted) if m else None


def flaky_record(repo, ids):
    """Count these tests in ~/.ai-planner/flaky.json: {repo: {test id: {"count", "last"}}}. Never raises."""
    try:
        data = json.loads(rd(flaky_path()) or "{}")
        for i in ids:
            row = data.setdefault(repo, {}).setdefault(i, {"count": 0})
            row["count"] += 1
            row["last"] = int(time.time())
        os.makedirs(BASE, exist_ok=True)
        tmp = flaky_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, flaky_path())
    except Exception:
        pass


def flaky_top(n=10):
    """The most often flaky tests across repos, for the status: [{repo, id, count, last}]."""
    try:
        data = json.loads(rd(flaky_path()) or "{}")
        rows = [{"repo": r, "id": i, "count": v.get("count", 0), "last": v.get("last", 0)}
                for r, tests in data.items() for i, v in tests.items()]
    except Exception:
        return []
    return sorted(rows, key=lambda x: (-x["count"], -x["last"]))[:n]


def _rerun_ids(where, repo, red, ids, d, tid, tag):
    """Rerun only these tests with the failed check's own interpreter, up to FLAKY_RERUNS times. Returns
    (passed_on_a_rerun, still_failing, last_log_tail); still_failing is None when the check cannot be narrowed."""
    cmd = targeted_cmd(red["cmd"], where, repo, ids)
    if not cmd:
        return None, None, ""
    left, passed, tail = list(ids), [], ""
    for n in range(1, FLAKY_RERUNS + 1):
        lg = os.path.join(d, "%s.checks-%s%d.log" % (tid, tag, n))
        open(lg, "w").close()
        rc, timed_out = _run_check(cmd, where, red["timeout"], lg)
        tail = (rd(lg) or "")[-4000:]
        if rc == 0:
            passed, left = passed + left, []
        else:
            now = [i for i in failed_test_ids(rd(lg)) if i in left]
            now = now or left  # a crash or timeout names no test: all of them still count as failing
            passed, left = passed + [i for i in left if i not in now], now
        if not left:
            break
        cmd = targeted_cmd(red["cmd"], where, repo, left)
    return passed, left, tail


def apply_task(ch, tid, iw, ibranch, base, resolve, log, force=False):
    """Apply one agent's own changes onto the integration worktree and commit them.
    When the project defines checks, they run on the COMBINED result (the target plus this task), which is what
    would land. If they fail, the task is taken back off and the checks run on the target alone, so the card can say
    whose failure it is: this task's (send it back to the agent) or already there (merge anyway with force)."""
    try:
        repo, wtp, wbranch, _ = _wt(ch, tid)
        project_config(repo)  # a broken .runway.json stops the merge here, with its message
    except (SystemExit, ConfigError) as e:
        # A second Merge after the first already succeeded: the worktree is gone because the work is on the branch.
        _, found = git(iw, "log", "--format=%h", "-1", "--grep", "Task %s, chunk" % tid, ibranch)
        if found.strip():
            return {"outcome": "merged", "msg": "Already merged (%s)." % found.strip(), "tokens": 0, "cost": 0}
        return {"outcome": "error", "msg": str(e).replace("@@ERR ", "")}
    raw, read = checks_source(repo, base, wtp)
    checks, cerr = parse_checks(raw)
    if cerr and not force:
        return {"outcome": "error", "msg": "%s (read %s)" % (cerr, read)}
    meta = json.loads(rd(os.path.join(RUNS, ch, tid + ".meta")) or "{}")
    git(wtp, "add", "-A")
    fork = _fork(meta, wtp, base)
    if not fork:
        return {"outcome": "error", "msg": "Could not tell which commit the agent started from."}
    patch = subprocess.run(["git", "-C", wtp, "diff", "--cached", "--binary", fork], capture_output=True).stdout
    if not patch.strip():
        return {"outcome": "empty", "msg": "The agent made no changes. Discard it and re-run the task."}
    # A real 3-way merge of the agent's own work (its tree, against the commit it started from), not a patch:
    # files both sides changed, or that one side deleted, become ordinary conflicts instead of a failed apply.
    _, tree = git(wtp, "write-tree")
    rc, squash = git(wtp, "commit-tree", tree.strip(), "-p", fork, "-m", "Runway: " + _title(ch, tid))
    if rc:
        return {"outcome": "error", "msg": "Could not snapshot the agent's work: " + squash[-2000:]}
    rc, pick = git(iw, "cherry-pick", "--no-commit", "--allow-empty", squash.strip())
    out = {"outcome": "merged", "msg": "", "tokens": 0, "cost": 0}
    def undo():
        git(iw, "cherry-pick", "--quit")
        git(iw, "reset", "-q", "--hard", "HEAD")
        git(iw, "clean", "-fdq")  # helper-owned integration worktree only
    if rc:
        _, st = git(iw, "status", "--porcelain")
        codes = {l[3:].strip(): l[:2] for l in st.splitlines() if l[:2] in ("UU", "AA", "DU", "UD", "AU", "UA", "DD")}
        files = sorted(codes)
        if not files:
            undo()
            return {"outcome": "error", "msg": "The merge failed: " + pick[-2000:]}
        # Where the task stands, and the step that unblocks it: its branch and worktree are kept, so it is fixed in place.
        next_step = "Nothing was merged, and the task is kept on %s in %s. Resolve them there (git -C %s merge %s), then merge again." % (
            wbranch, wtp, wtp, ibranch)
        if not resolve:
            undo()
            log_failure(ch, wbranch, "merge", rc, pick, paths=files)  # so merge-log shows the conflict, not just the card
            return {"outcome": "conflict", "msg": "Conflicts with approved work in " + ", ".join(files[:6]) + ". " + next_step}
        log("%s: resolving %s" % (tid, ", ".join(files)))
        ok, tokens, cost, said = _resolve(iw, files, ch, tid, log, deleted={f: c for f, c in codes.items() if "D" in c})
        out.update(tokens=tokens, cost=cost)
        if ok:  # mark each file resolved: kept files are staged, files resolved by deleting them are removed
            for f in files:
                if os.path.exists(os.path.join(iw, f)):
                    bad = _broken_after_resolve(f, rd(os.path.join(iw, f)) or "")
                    if bad:
                        ok, said = False, bad
                        break
                    git(iw, "add", "--", f)
                else:
                    git(iw, "rm", "-q", "--cached", "--ignore-unmatch", "--", f)
            _, left = git(iw, "diff", "--name-only", "--diff-filter=U")
            if ok and left.strip():
                ok, said = False, "still unmerged: " + left.replace("\n", ", ")
        if not ok:
            undo()
            log_failure(ch, wbranch, "merge", rc, said, paths=files)
            return dict(out, outcome="conflict", msg="Conflicts in %s that Claude could not resolve cleanly (%s). %s" % (", ".join(files[:6]), said, next_step))
        out.update(outcome="resolved", msg="Resolved conflicts in %s: %s" % (", ".join(files[:6]), said.strip().splitlines()[-1] if said.strip() else "combined both sides"))
    rc, o = git(iw, "commit", "-q", "--allow-empty", "-m", "Runway: " + _title(ch, tid), "-m", "Task %s, chunk %s." % (tid, ch))
    if rc:
        undo()
        return {"outcome": "error", "msg": "Could not commit on %s: %s" % (ibranch, o[-2000:])}
    if checks and not force:
        d = os.path.join(RUNS, ch)
        clog = os.path.join(d, tid + ".checks.log")
        _checks_state(d, tid, state="running", step="combined", worktree=iw)
        red = _run_checks_in(iw, checks, clog)
        if red:
            tail = (rd(clog) or "")[-4000:]
            combined = git(iw, "rev-parse", "HEAD")[1].strip()
            # A pytest check names its failing tests. Rerun only those, on the combined result: one that passes is flaky
            # (a different test failing on each run no longer blames the task), one that fails every time is real.
            real = None
            ids = [] if red["timedOut"] else failed_test_ids(rd(clog))
            if ids:
                flaky, left, rtail = _rerun_ids(iw, repo, red, ids, d, tid, "rerun")
                if flaky:
                    flaky_record(repo, flaky)
                if left:
                    real, tail = left, rtail or tail
                elif left is not None:  # every failure passed on a rerun
                    k = next((n for n, c in enumerate(checks) if c["name"] == red["name"] and c["cmd"] == red["cmd"]), len(checks) - 1)
                    rest_log = os.path.join(d, tid + ".checks-rest.log")
                    later = _run_checks_in(iw, checks[k + 1:], rest_log) if k + 1 < len(checks) else None
                    if later:  # a check after the flaky one never ran in the first pass, and it is red: the whole-run path decides
                        red, tail = later, (rd(rest_log) or "")[-4000:]
                    else:
                        log_failure(ch, wbranch, "test", red["rc"], "flaky: %s failed once and passed on a rerun:\n%s" % (", ".join(flaky), tail))
                        out["flaky"] = flaky
                        out["msg"] = ((out.get("msg") or "") + " Checks were red once (%s) and green on a rerun: merged, and logged as flaky." % ", ".join(flaky)).strip()
                        _checks_finish(ch, tid, wtp, "green")
                        red = None
            if red:
                git(iw, "reset", "-q", "--hard", "HEAD~1")  # take this task back off (helper-owned integration worktree only)
            if red and real:
                # Failing on every rerun: does the base alone fail the same tests?
                blog = os.path.join(d, tid + ".checks-base.log")
                open(blog, "w").close()
                brc, _ = _run_check(targeted_cmd(red["cmd"], iw, repo, real), iw, red["timeout"], blog)
                seen = failed_test_ids(rd(blog))
                on_base = [] if brc == 0 else ([i for i in real if i in seen] if seen else list(real))
                mine = [i for i in real if i not in on_base]
                base_red = dict(red) if not mine else None
                real = mine or on_base
            elif red:
                base_red = _run_checks_in(iw, checks, os.path.join(d, tid + ".checks-base.log"))
                if not base_red:
                    # Red with the task, green without it: before blaming the task, run the combined checks once more. An
                    # intermittent test otherwise sends sound work back to its agent. A second red is a real failure.
                    git(iw, "reset", "-q", "--hard", combined)
                    again = _run_checks_in(iw, checks, os.path.join(d, tid + ".checks-retry.log"))
                    if not again:
                        log_failure(ch, wbranch, "test", red["rc"], "flaky: %s failed once and passed on a rerun:\n%s" % (red["name"], tail))
                        out["flaky"] = red["name"]
                        out["msg"] = ((out.get("msg") or "") + " Checks were red once (%s) and green on a rerun: merged, and logged as flaky." % red["name"]).strip()
                        _checks_finish(ch, tid, wtp, "green")
                        red = None
                    else:
                        git(iw, "reset", "-q", "--hard", "HEAD~1")
        if red:
            _checks_finish(ch, tid, wtp, "timeout" if red["timedOut"] else "red", check=red, rc=red["rc"], reason="exit %s" % red["rc"])
            msg = "Its checks did not pass (red: %s) with this task applied on %s." % (red["name"], ibranch)
            if real:
                msg += " Failing on every rerun: %s." % ", ".join(real[:8])
            msg += (" They fail on %s without it too (%s), so the failure was already there: merge it anyway, and fix the shared failure separately." % (ibranch, base_red["name"])
                    if base_red else " They pass without it, so this task caused it: send the failure back to the agent.")
            return dict(out, outcome="checks", msg=msg, step=red["name"], log=tail, baseRed=bool(base_red))
        _checks_finish(ch, tid, wtp, "green")
    env_teardown(os.path.join(RUNS, ch), log)  # the worktree is about to go, so its environment goes first
    git(repo, "worktree", "unlock", wtp)
    git(repo, "worktree", "remove", "--force", "--force", wtp)
    git(repo, "branch", "-D", wbranch)
    return out


def merge_batch_run(bid):
    """Merge a batch of tasks in the given order, resolving conflicts with Claude, then land once per repo."""
    path = os.path.join(MERGES, bid + ".json")
    state = json.loads(rd(path) or "{}")
    def save():
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    def log(m):
        state.setdefault("log", []).append(m)
        save()
    state["state"] = "running"
    state["startedAt"] = int(time.time())
    save()
    groups = {}
    for item in state["items"]:
        try:
            repo, _, _, base = _wt(item["ch"], item["tid"])
        except SystemExit as e:
            # The worktree is gone, usually because this work was already merged: group it by its run record so
            # apply_task can recognise it on the integration branch instead of failing with "Worktree not found".
            meta = json.loads(rd(os.path.join(RUNS, item["ch"], item["tid"] + ".meta")) or "{}")
            repo, base = meta.get("repo"), meta.get("base")
            if not (repo and base and meta.get("wt") and os.path.isdir(repo)):
                state["results"][item["tid"]] = {"outcome": "error", "msg": str(e).replace("@@ERR ", "")}
                save()
                continue
        if not base or base == "HEAD":
            state["results"][item["tid"]] = {"outcome": "error", "msg": "The repo was not on a branch when this task ran."}
            save()
            continue
        groups.setdefault((repo, base), []).append(item)
    for l in state.get("lands") or []:  # a land that needs Claude to combine your commits runs here, in the background
        groups.setdefault((l["repo"], l["base"]), [])
    land_ms, lands_failed = 0, 0
    for (repo, base), items in groups.items():
        iw, ibranch = integration(repo, base)
        lockf = open(os.path.join(INTEG, _slug(repo, base) + ".lock"), "w")
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            ok, note = sync_integration(repo, base, log, resolve=True)
            if not ok:
                for it in items:
                    state["results"][it["tid"]] = {"outcome": "error", "msg": note}
                save()
                continue
            for it in items:
                state["current"] = it["tid"]
                save()
                if it.get("title"):
                    with open(os.path.join(RUNS, it["ch"], it["tid"] + ".title"), "w") as tf:
                        tf.write(it["title"])
                t_merge = time.perf_counter()
                r = apply_task(it["ch"], it["tid"], iw, ibranch, base, state.get("resolve", True), log, force=bool(it.get("force")))
                timing_add(it["ch"], it["tid"], merge_ms=_ms(t_merge), conflicts_resolved=1 if r.get("outcome") == "resolved" else 0)
                state["results"][it["tid"]] = r
                save()
                try:
                    tick()  # the page sees each task's outcome as it happens, not at the next minute
                except Exception:
                    pass
            t_land = time.perf_counter()
            landed, msg = land(repo, base, resolve=True)
            land_ms += _ms(t_land)
            lands_failed += 0 if landed else 1
            state.setdefault("land", {})[repo] = {"ok": landed, "msg": msg, "base": base, "branch": ibranch}
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)
    state["current"] = None
    state["state"] = "done"
    state["doneAt"] = int(time.time())
    save()
    try:  # after "done" is saved, so the ledger costs the merge nothing; a ledger problem never fails the run
        rows = [task_row(it["ch"], it["tid"], (state["results"].get(it["tid"]) or {}).get("outcome", "error"),
                         json.loads(rd(_timing_path(it["ch"], it["tid"])) or "{}")) for it in state["items"]]
        state["ledger"] = write_ledger(bid, state, rows, land_ms, lands_failed)
    except Exception as e:
        state["ledgerError"] = str(e)[:200]
    save()
    tick()


def merge_batch_start(bid, spec_b64):
    os.makedirs(MERGES, exist_ok=True)
    spec = json.loads(base64.b64decode(spec_b64).decode())
    state = {"id": bid, "state": "queued", "items": spec["items"], "resolve": spec.get("resolve", True), "results": {}, "createdAt": int(time.time()),
             "lands": spec.get("lands") or [], "kind": spec.get("kind") or "merge", "note": spec.get("note") or ""}
    with open(os.path.join(MERGES, bid + ".json"), "w") as f:
        json.dump(state, f)
    log = open(os.path.join(MERGES, bid + ".log"), "ab")
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "merge-batch-run", bid], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True, cwd=BASE)
    return "@@MERGING " + bid


def merges_status():
    out = {}
    cutoff = time.time() - 2 * 86400
    for f in glob.glob(os.path.join(MERGES, "*.json")):
        if os.path.getmtime(f) < cutoff:
            continue
        try:
            m = json.loads(rd(f) or "{}")
            out[m["id"]] = {k: m.get(k) for k in ("state", "results", "land", "current", "createdAt", "doneAt", "kind", "note")}
            out[m["id"]]["items"] = [{"tid": i.get("tid"), "ch": i.get("ch")} for i in m.get("items") or []]
        except Exception:
            continue
    return out


def merge(ch, tid):
    """Single-task merge, synchronous, without the Claude resolver (kept for the CLI)."""
    try:
        repo, _, _, base = _wt(ch, tid)
    except SystemExit as e:
        return str(e)
    iw, ibranch = integration(repo, base)
    r = apply_task(ch, tid, iw, ibranch, base, False, lambda m: None)
    if r["outcome"] not in ("merged", "resolved"):
        return "@@ERR " + r["msg"]
    return "@@OK " + land(repo, base)[1]


def discard(ch, tid):
    repo, wtp, branch, _ = _wt(ch, tid)
    env_teardown(os.path.join(RUNS, ch))  # the worktree is about to go, so its environment goes first
    git(repo, "worktree", "unlock", wtp)
    rc, out = git(repo, "worktree", "remove", "--force", "--force", wtp)
    if rc:
        return "@@ERR " + out[-2000:]
    git(repo, "branch", "-D", branch)
    return "@@OK Deleted worktree and branch " + branch + "."


# ---------- auto-merge: a green chunk task merges into its base branch itself ----------
AUTOMERGE_LOCK = os.path.join(BASE, "automerge.lock")  # one merge at a time, across every project and chunk
AUTOMERGE_WAIT = 900  # seconds a merge waits for the lock before it gives up
SYNC_ATTEMPTS = 3  # base moved again while we were testing: sync and test again, this many times at most


def _lock(path, wait):
    """An exclusive flock on path, waiting up to wait seconds. Returns the open file, or None when another holds it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "a")
    deadline = time.time() + wait
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except BlockingIOError:
            if time.time() >= deadline:
                f.close()
                return None
            time.sleep(0.2)


def _head(wtp):
    return git(wtp, "rev-parse", "HEAD")[1].strip()


def _sync_branch(ch, tid, wtp, base, base_tip, cfg):
    """Bring base_tip into the task's branch, when base has commits the branch lacks, then re-run the checks.
    Returns None when the branch is ready, else the outcome. A conflict is aborted and the branch stays as it was."""
    if git(wtp, "merge-base", "--is-ancestor", base_tip, "HEAD")[0] == 0:
        return None  # the branch already has everything base has: nothing moved, nothing to re-test
    before = _head(wtp)
    if cfg.get("syncStrategy") == "rebase":
        rc, out = git(wtp, "rebase", base_tip)
        abort = ["rebase", "--abort"]
    else:
        rc, out = git(wtp, "merge", "--no-edit", "-q", base_tip)
        abort = ["merge", "--abort"]
    if rc:
        _, unmerged = git(wtp, "diff", "--name-only", "--diff-filter=U")
        paths = [p for p in unmerged.splitlines() if p.strip()]
        git(wtp, *abort)
        if _head(wtp) != before:  # the abort should have restored the branch; make sure it did
            git(wtp, "reset", "-q", "--hard", before)
        if not paths:
            return {"outcome": "error", "msg": "Could not bring %s into the branch: %s" % (base, out[-300:])}
        log_failure(ch, "runway-task/" + tid, "merge", rc, "Conflicts with %s in %s. The branch was left as it was." % (
            base, ", ".join(paths)), paths=paths)
        return {"outcome": "conflict", "msg": "Conflicts with %s in %s. The branch is untouched; resolve them in the task, then merge again." % (
            base, ", ".join(paths[:6])), "paths": paths}
    v = run_checks(ch, tid, base)  # base moved, so the checks run again on the branch with base in it
    if not v["ok"]:
        return {"outcome": "red", "msg": "%s moved, and the checks did not pass on the branch with it in (%s). Nothing reached %s." % (
            base, v.get("verdict"), base)}
    return None


def _land_on_base(ch, tid, repo, wtp, wbranch, base, base_tip, cfg):
    """Move base to one new commit that holds the task's tree, then delete the task's branch and worktree.
    Base moves in one step: a fast-forward in the checkout that has it, else a compare-and-swap on its ref. Either
    refuses rather than half-applies. Returns the outcome, or None when base moved meanwhile (the caller syncs again)."""
    strategy = cfg["mergeStrategy"]
    tree = git(wtp, "rev-parse", "HEAD^{tree}")[1].strip()
    parents = ["-p", base_tip] + (["-p", _head(wtp)] if strategy == "merge" else [])
    rc, newc = git(wtp, "commit-tree", tree, *parents, "-m", "Runway: " + _title(ch, tid), "-m", "Task %s, chunk %s." % (tid, ch))
    if rc:
        return {"outcome": "error", "msg": "Could not write the merge commit: " + newc[-300:]}
    newc = newc.strip()
    co = checkout_for(repo, base)
    if co:
        rc, out = git(co, "merge", "--ff-only", "-q", newc)
    else:
        rc, out = git(repo, "update-ref", "-m", "Runway auto-merge " + tid, "refs/heads/" + base, newc, base_tip)
    if rc:
        if git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + base)[1].strip() != base_tip:
            return None  # base moved while we worked: nothing changed, so sync and test again
        return {"outcome": "error", "msg": "Could not move %s to the merge: %s Nothing changed." % (base, out[-300:])}
    # Base has the work now. Removing the branch and worktree comes last, so a failure here leaves a re-runnable task.
    env_teardown(os.path.join(RUNS, ch))  # the merge is in; the worktree is about to go, so its environment goes first
    git(repo, "worktree", "unlock", wtp)
    rc1, out1 = git(repo, "worktree", "remove", "--force", "--force", wtp)
    rc2, out2 = git(repo, "branch", "-D", wbranch)
    msg = "Merged into %s (%s). Removed %s and its worktree." % (base, strategy, wbranch)
    if rc1 or rc2:
        msg += " Could not remove all of it: %s" % " ".join(o[-200:] for rc, o in ((rc1, out1), (rc2, out2)) if rc)
    return {"outcome": "merged", "msg": msg, "commit": newc}


def _automerge_locked(ch, tid, repo, wtp, wbranch, base, cfg):
    if git(wtp, "merge-base", "--is-ancestor", "HEAD", base)[0] == 0:
        return {"outcome": "empty", "msg": "The task has no commits beyond %s. Discard it or re-run the task." % base}
    gate = _gate(ch, tid, base)
    if gate:
        return {"outcome": "red", "msg": gate}
    if git(wtp, "status", "--porcelain")[1].strip():
        return {"outcome": "error", "msg": "The task's worktree has uncommitted changes, so its checks no longer describe it. Commit them, then merge again."}
    for _ in range(SYNC_ATTEMPTS):
        rc, tip = git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + base)
        if rc:
            return {"outcome": "error", "msg": "The base branch %s does not exist in %s." % (base, repo)}
        tip = tip.strip()
        stop = _sync_branch(ch, tid, wtp, base, tip, cfg)
        if stop:
            return stop
        done = _land_on_base(ch, tid, repo, wtp, wbranch, base, tip, cfg)
        if done:
            return done
    return {"outcome": "error", "msg": "%s kept moving while the task was merged. Nothing changed; merge again." % base}


def automerge(ch, tid, wait=AUTOMERGE_WAIT):
    """Merge a green chunk task into its base branch, then delete its branch and worktree. Steps, under one merge lock
    shared by every project: check the checks are green; bring the base into the task's branch (merge or rebase, per
    .runway.json "syncStrategy"); re-run the checks if the base moved; merge with "mergeStrategy". A conflict aborts the
    sync, leaves the branch untouched and logs the paths. Returns {"outcome", "msg"}; outcome is merged, conflict, red,
    empty, locked or error. Nothing reaches the base branch unless the whole merge succeeds."""
    try:
        repo, wtp, wbranch, meta_base = _wt(ch, tid)
        cfg = project_config(repo)
    except (SystemExit, ConfigError) as e:
        return {"outcome": "error", "msg": str(e).replace("@@ERR ", "")}
    base = cfg.get("baseBranch") or meta_base
    if not base or base == "HEAD":
        return {"outcome": "error", "msg": "The repo was not on a branch when this task ran."}
    lock = _lock(AUTOMERGE_LOCK, wait)
    if lock is None:
        return {"outcome": "locked", "msg": "Another merge is running. Nothing changed; try again when it finishes."}
    try:
        return _automerge_locked(ch, tid, repo, wtp, wbranch, base, cfg)
    finally:
        lock.close()  # closing the file releases the flock


# ---------- merge-train timing: per-task phase times, and one ledger per merge run ----------
HUMAN_OUTCOMES = ("conflict", "error", "empty")  # the merge stopped, so a person has to touch git


def _ms(t0):
    return int(round((time.perf_counter() - t0) * 1000))


def _timing_path(ch, tid):
    return os.path.join(RUNS, ch, tid + ".timing.json")


def timing_add(ch, tid, **fields):
    """Add to a task's timing record: durations in ms and counts, summed across every run of the task."""
    os.makedirs(os.path.join(RUNS, ch), exist_ok=True)
    p = _timing_path(ch, tid)
    rec = json.loads(rd(p) or "{}")
    for k, v in fields.items():
        rec[k] = rec.get(k, 0) + v
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, p)
    return rec


def task_row(ch, tid, outcome, timing):
    """One task's line in the ledger. A task with no timing record gets zeros."""
    t = timing or {}
    return {"ch": ch, "tid": tid, "outcome": outcome,
            "setup_ms": t.get("setup_ms", 0), "test_ms": t.get("test_ms", 0), "merge_ms": t.get("merge_ms", 0),
            "retries": t.get("retries", 0), "conflicts_resolved": t.get("conflicts_resolved", 0),
            "human_touches": 1 if outcome in HUMAN_OUTCOMES else 0}


def ledger_totals(rows, land_ms=0, lands_failed=0):
    """Sum the task rows into the run totals, in the merge-train baseline buckets.
    machine_min is all automated time. git_merge_min is the part of it that is git (worktree setup, merge and
    landing), so it is a subset of machine_min. review_min and client_min are not automated, so they stay null.
    human_touches counts the tasks whose merge stopped and the lands that failed: each one needed a person in git."""
    def total(k):
        return sum(r[k] for r in rows)
    git_ms = total("setup_ms") + total("merge_ms") + land_ms
    return {
        "tasks": len(rows),
        "machine_min": round((git_ms + total("test_ms")) / 60000, 2),
        "git_merge_min": round(git_ms / 60000, 2),
        "setup_min": round(total("setup_ms") / 60000, 2),
        "test_min": round(total("test_ms") / 60000, 2),
        "merge_min": round((total("merge_ms") + land_ms) / 60000, 2),
        "review_min": None,
        "client_min": None,
        "retries": total("retries"),
        "conflicts_resolved": total("conflicts_resolved"),
        "human_touches": total("human_touches") + lands_failed,
    }


def write_ledger(bid, state, rows, land_ms=0, lands_failed=0):
    """Write the run's ledger to ledgers/<bid>.json: the totals plus each task's row. Atomic, like status.json."""
    ledger = {"run": bid, "kind": state.get("kind") or "merge", "startedAt": state.get("startedAt"),
              "doneAt": state.get("doneAt"), "totals": ledger_totals(rows, land_ms, lands_failed), "tasks": rows}
    d = os.path.join(BASE, "ledgers")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, bid + ".json")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ledger, f, indent=1)
    os.replace(tmp, p)
    return p


# ---------- roadmap: a project's stored plan, Roadmap -> PI -> Sprint -> Task, one JSON file per project ----------
ROADMAP_VERSION = 1  # bump it, and add a ROADMAP_MIGRATIONS entry from the old number, when the shape changes
ROADMAP_STATUSES = ("todo", "running", "review", "done", "blocked", "parked", "dropped")
ROADMAP_MIGRATIONS = {}  # version -> function(dict) returning the dict in the next version's shape; run in order on load
REQUIRED = object()  # the default of a field that must be present


class RoadmapError(ValueError):
    """A roadmap that does not match the schema. The message names the field that is wrong."""


@dataclass
class Task:
    key: str
    title: str
    detail: str = ""
    acceptance: str = ""
    estTokens: int = 0  # the token estimate, calibrated against Runway's run history
    model: str = ""
    kind: str = ""
    dependsOn: list = field(default_factory=list)  # keys of tasks that must be done first
    status: str = "todo"  # one of ROADMAP_STATUSES
    independent: bool = False  # true: not held back by unfinished earlier sprints


@dataclass
class Sprint:
    key: str
    title: str
    detail: str = ""
    tasks: list = field(default_factory=list)
    independent: bool = False  # true: this sprint's tasks are not held back by unfinished earlier sprints


@dataclass
class PI:
    key: str
    title: str
    detail: str = ""
    sprints: list = field(default_factory=list)


@dataclass
class Provenance:
    briefHash: str = ""  # sha256 of the project brief the roadmap was generated from
    generatedAt: int = 0  # unix seconds
    generatorModel: str = ""


@dataclass
class Roadmap:
    version: int = ROADMAP_VERSION
    project: str = ""
    title: str = ""
    provenance: Provenance = field(default_factory=Provenance)
    pis: list = field(default_factory=list)


_ARTICLE = {str: "a string", int: "a whole number", list: "a list", dict: "an object"}


def _jtype(v):
    return {bool: "a boolean", int: "a number", float: "a number", str: "a string", list: "a list",
            dict: "an object", type(None): "null"}.get(type(v), type(v).__name__)


def _join(path, key):
    return path + "." + key if path else key


def _take(obj, path, key, kind, default=REQUIRED):
    """obj[key], which must be of type kind (a bool is not a number here). Errors name the field by its full path."""
    if key not in obj:
        if default is REQUIRED:
            raise RoadmapError("%s is required." % _join(path, key))
        return default
    v = obj[key]
    if type(v) is not kind:
        raise RoadmapError("%s must be %s, not %s." % (_join(path, key), _ARTICLE[kind], _jtype(v)))
    return v


def _objects(obj, path, key):
    """obj[key] as a list of objects, each paired with its path, e.g. pis[0].sprints[1]."""
    base = _join(path, key)
    out = []
    for i, item in enumerate(_take(obj, path, key, list, [])):
        if not isinstance(item, dict):
            raise RoadmapError("%s[%d] must be an object, not %s." % (base, i, _jtype(item)))
        out.append((item, "%s[%d]" % (base, i)))
    return out


def _key(d, path, seen):
    """A key that is non-empty and unique across the whole roadmap, so dependsOn can name any PI, sprint or task."""
    k = _take(d, path, "key", str)
    if not k.strip():
        raise RoadmapError("%s must not be empty." % _join(path, "key"))
    if k in seen:
        raise RoadmapError('%s is "%s", already used at %s.' % (_join(path, "key"), k, seen[k]))
    seen[k] = _join(path, "key")
    return k


def _roadmap_title(d, path):
    t = _take(d, path, "title", str)
    if not t.strip():
        raise RoadmapError("%s must not be empty." % _join(path, "title"))
    return t


def _task(d, path, seen):
    t = Task(key=_key(d, path, seen), title=_roadmap_title(d, path), detail=_take(d, path, "detail", str, ""),
             acceptance=_take(d, path, "acceptance", str, ""), estTokens=_take(d, path, "estTokens", int, 0),
             model=_take(d, path, "model", str, ""), kind=_take(d, path, "kind", str, ""),
             status=_take(d, path, "status", str, "todo"), independent=_take(d, path, "independent", bool, False))
    if t.estTokens < 0:
        raise RoadmapError("%s must not be negative, not %d." % (_join(path, "estTokens"), t.estTokens))
    if t.status not in ROADMAP_STATUSES:
        raise RoadmapError("%s must be one of %s, not %s." % (_join(path, "status"), ", ".join(ROADMAP_STATUSES), json.dumps(t.status)))
    deps = _take(d, path, "dependsOn", list, [])
    for i, dep in enumerate(deps):
        if type(dep) is not str:
            raise RoadmapError("%s[%d] must be a task key string, not %s." % (_join(path, "dependsOn"), i, _jtype(dep)))
    t.dependsOn = list(deps)
    return t


def parse_roadmap(data):
    """Check a roadmap dict against the schema and return it as a Roadmap. Raises RoadmapError naming the field that is
    wrong. An older version is migrated up to ROADMAP_VERSION; a newer one is refused rather than guessed at."""
    if not isinstance(data, dict):
        raise RoadmapError("roadmap must be an object, not %s." % _jtype(data))
    v = _take(data, "", "version", int)
    if v > ROADMAP_VERSION:
        raise RoadmapError("version is %d, newer than the %d this Runway reads. Update Runway to read it." % (v, ROADMAP_VERSION))
    while v < ROADMAP_VERSION:
        if v not in ROADMAP_MIGRATIONS:
            raise RoadmapError("version %d has no migration to %d." % (v, v + 1))
        data = ROADMAP_MIGRATIONS[v](data)
        v += 1
    seen, tasks = {}, []
    pis = []
    for pd, pp in _objects(data, "", "pis"):
        pi = PI(key=_key(pd, pp, seen), title=_roadmap_title(pd, pp), detail=_take(pd, pp, "detail", str, ""))
        for sd, sp in _objects(pd, pp, "sprints"):
            sprint = Sprint(key=_key(sd, sp, seen), title=_roadmap_title(sd, sp), detail=_take(sd, sp, "detail", str, ""),
                            independent=_take(sd, sp, "independent", bool, False))
            for td, tp in _objects(sd, sp, "tasks"):
                t = _task(td, tp, seen)
                sprint.tasks.append(t)
                tasks.append((t, tp))
            pi.sprints.append(sprint)
        pis.append(pi)
    task_keys = {t.key for t, _ in tasks}
    for t, tp in tasks:
        for i, dep in enumerate(t.dependsOn):
            if dep not in task_keys:
                raise RoadmapError('%s[%d] names no task: "%s".' % (_join(tp, "dependsOn"), i, dep))
    prov = _take(data, "", "provenance", dict, {})
    return Roadmap(version=ROADMAP_VERSION, project=_take(data, "", "project", str, ""),
                   title=_take(data, "", "title", str, ""),
                   provenance=Provenance(briefHash=_take(prov, "provenance", "briefHash", str, ""),
                                         generatedAt=_take(prov, "provenance", "generatedAt", int, 0),
                                         generatorModel=_take(prov, "provenance", "generatorModel", str, "")),
                   pis=pis)


def roadmap_path(repo):
    """A project's roadmap file: roadmaps/<project folder>-roadmap-<hash of its path>.json under the data directory."""
    return os.path.join(BASE, "roadmaps", _slug(repo, "roadmap") + ".json")


def _write_json(p, data):
    """Write JSON to p atomically (a temp file, then rename), like the ledgers."""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, p)


def save_roadmap(repo, roadmap):
    """Validate a Roadmap (or its dict) and write it to the project's roadmap file, atomically like the ledgers.
    Returns the path written."""
    rm = parse_roadmap(asdict(roadmap) if isinstance(roadmap, Roadmap) else roadmap)
    p = roadmap_path(repo)
    data = asdict(rm)
    for pi in data["pis"]:  # `independent` is written only when set, so existing roadmap files keep their shape
        for sp in pi["sprints"]:
            for node in [sp] + sp["tasks"]:
                if not node.get("independent"):
                    node.pop("independent", None)
    _write_json(p, data)
    return p


def parse_roadmap_text(text, p):
    """parse_roadmap for the JSON text of the file at p. Errors name the file and the field that is wrong."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RoadmapError("%s is not valid JSON (line %d, column %d: %s)." % (p, e.lineno, e.colno, e.msg))
    try:
        return parse_roadmap(data)
    except RoadmapError as e:
        raise RoadmapError("%s: %s" % (p, e))


def load_roadmap(repo, path=None):
    """The project's stored roadmap (or the file at path), or None if there is none. A file that is not a valid roadmap
    raises RoadmapError with the file's path and the field that is wrong."""
    p = path or roadmap_path(repo)
    text = rd(p)
    if text is None:
        return None
    return parse_roadmap_text(text, p)


# ---------- roadmap generation: a brief -> Claude (headless) -> a validated candidate roadmap, one repair retry ----------
# A generated roadmap is a candidate for Austin's review. It is written beside the project's roadmap, never over it:
# accepting a candidate is a human step. Raw replies are kept with it, for debugging.
ROADMAP_MODEL = "sonnet"  # the --model alias the generator asks Claude for
SPRINT_TOKEN_CAP = 2_000_000  # effective tokens a sprint may total
TASK_TOKEN_CAP = 1_000_000  # effective tokens one task may use
HUMAN_KINDS = ("human", "manual")  # a human-only step is never a task
GEN_TIMEOUT = 900


class GenerationError(RuntimeError):
    """No valid roadmap after the repair retry. The message says why; the raw replies are saved beside the candidate."""


def check_brief(brief):
    """A brief is an object: goal and repo (an existing folder) are required text; constraints and planned are optional text."""
    if not isinstance(brief, dict):
        raise RoadmapError("brief must be an object, not %s." % _jtype(brief))
    for k in ("goal", "repo"):
        if not _take(brief, "brief", k, str).strip():
            raise RoadmapError("brief.%s must not be empty." % k)
    if not os.path.isdir(brief["repo"]):
        raise RoadmapError("brief.repo is not a folder: %s." % brief["repo"])
    _take(brief, "brief", "constraints", str, "")
    _take(brief, "brief", "planned", str, "")


def brief_hash(brief):
    return hashlib.sha256(json.dumps(brief, sort_keys=True).encode()).hexdigest()


def candidate_paths(repo):
    """The parsed candidate roadmap and the raw replies beside it, both under the data directory."""
    d = os.path.join(BASE, "roadmaps")
    return (os.path.join(d, _slug(repo, "roadmap-candidate") + ".json"),
            os.path.join(d, _slug(repo, "roadmap-candidate-raw") + ".json"))


def roadmap_prompt(brief, errors=None):
    """The instructions, the brief, and (on the repair retry) the errors the last reply was rejected for."""
    lines = [
        "You are planning a software project for Runway, an unattended agent sprint planner. Read the repository in the current "
        "directory (Read, Grep and Glob only) so the plan fits what is already there. Then write a roadmap for the brief below.",
        "",
        "Roadmap shape: PIs (program increments) contain sprints, sprints contain tasks. Every PI, sprint and task has a key "
        "(unique across the whole roadmap, e.g. pi-1, s-1, t-1), a title and a detail. A task also has acceptance (how to tell it "
        "is done), estTokens (a whole number: the effective tokens an agent run of the task will use), model "
        "(claude-sonnet-5 or claude-haiku-4-5-20251001), kind (one of feature, fix, test, refactor, docs, chore) and dependsOn "
        "(a list of task keys).",
        "",
        "Planning rules, all mandatory:",
        "- A sprint's estTokens must total at most %d effective tokens. Split bigger sprints into more sprints." % SPRINT_TOKEN_CAP,
        "- No task may have estTokens above %d. Split bigger work into more tasks." % TASK_TOKEN_CAP,
        "- Only work an agent does is a task. Never make a human-only step a task (approving, publishing, deciding, buying, "
        "reviewing with the owner, or installing something by hand); leave those out.",
        "- dependsOn may name only tasks that come earlier in the roadmap (earlier PI, sprint or task order). Never name a later "
        "task or the task itself.",
        "",
        "Reply with one JSON object and nothing else: no prose, no code fence. Shape:",
        '{"version": 1, "title": "...", "pis": [{"key": "pi-1", "title": "...", "detail": "...", "sprints": [{"key": "s-1", '
        '"title": "...", "detail": "...", "tasks": [{"key": "t-1", "title": "...", "detail": "...", "acceptance": "...", '
        '"estTokens": 120000, "model": "claude-sonnet-5", "kind": "feature", "dependsOn": []}]}]}]}',
        "",
        "Project goal: " + brief["goal"],
        "Repository: " + brief["repo"],
        "Constraints: " + (brief.get("constraints") or "(none)"),
        "Already planned (keep it, and build the roadmap around it rather than re-planning it): " + (brief.get("planned") or "(none)"),
    ]
    if errors:
        lines += ["", "Your previous reply was rejected for these reasons:"] + ["- " + e for e in errors]
        lines += ["Reply with the complete corrected roadmap JSON, obeying every planning rule."]
    return "\n".join(lines)


def parse_reply(text):
    """The JSON object in a model reply: the reply itself, or the text from its first '{' to its last '}' (so a code fence
    or a line of chatter around it does not matter)."""
    s = (text or "").strip()
    a, b = s.find("{"), s.rfind("}")
    try:
        return json.loads(s[a:b + 1] if a >= 0 and b > a else s)
    except json.JSONDecodeError as e:
        raise RoadmapError("the reply is not valid JSON (line %d, column %d: %s)." % (e.lineno, e.colno, e.msg))


def plan_rule_errors(rm):
    """The planning rules a schema-valid roadmap can still break. Returns every violation, each naming its place."""
    errs, flat = [], []
    for pi in rm.pis:
        for sp in pi.sprints:
            total = sum(t.estTokens for t in sp.tasks)
            if total > SPRINT_TOKEN_CAP:
                errs.append("sprint %s totals %d estTokens, over the %d cap. Split it into more sprints." % (sp.key, total, SPRINT_TOKEN_CAP))
            for t in sp.tasks:
                flat.append(t)
                if t.estTokens > TASK_TOKEN_CAP:
                    errs.append("task %s has %d estTokens, over the %d cap. Split it into smaller tasks." % (t.key, t.estTokens, TASK_TOKEN_CAP))
                if t.kind in HUMAN_KINDS:
                    errs.append('task %s has kind "%s": a human-only step is not a task. Remove it.' % (t.key, t.kind))
    pos = {t.key: i for i, t in enumerate(flat)}
    for i, t in enumerate(flat):
        for dep in t.dependsOn:
            if pos[dep] >= i:
                errs.append("task %s depends on %s, which is not earlier in the roadmap." % (t.key, dep))
    for cyc in dependency_cycles(flat):
        errs.append("dependsOn cycle: %s. Remove one of these dependencies." % " -> ".join(cyc))
    return errs


def dependency_cycles(tasks):
    """Each dependsOn cycle once, as the task keys around it with the first key repeated at the end (e.g. t-1 -> t-2 -> t-1).
    A depth-first search over dependsOn; a task that is finished is not searched again."""
    deps = {t.key: t.dependsOn for t in tasks}
    cycles, finished, path = [], set(), []

    def visit(k):
        if k in path:
            cyc = path[path.index(k):] + [k]
            if sorted(set(cyc)) not in [sorted(set(c)) for c in cycles]:
                cycles.append(cyc)
            return
        if k in finished:
            return
        path.append(k)
        for d in deps.get(k, []):
            visit(d)
        path.pop()
        finished.add(k)

    for k in deps:
        visit(k)
    return cycles


def claude_reply(prompt, repo):
    """One headless Claude call in the repo, with read-only tools and no MCP servers. Returns the reply text."""
    env = dict(os.environ, PATH="/opt/homebrew/bin:/usr/local/bin:" + os.path.expanduser("~/.local/bin") + ":" + os.environ.get("PATH", ""))
    try:
        p = subprocess.run([CLAUDE, "-p", prompt, "--model", ROADMAP_MODEL, "--output-format", "json",
                            "--tools", "Read", "Grep", "Glob", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
                           cwd=repo, capture_output=True, text=True, timeout=GEN_TIMEOUT, stdin=subprocess.DEVNULL, env=env)
        out = json.loads(p.stdout or "{}")
    except Exception as e:
        raise GenerationError("Claude couldn't run: %s." % e)
    if p.returncode or out.get("is_error"):
        raise GenerationError("Claude returned an error: %s" % ((out.get("result") or p.stderr or "no output").strip()[-300:]))
    return out.get("result") or ""


def generate_roadmap(brief, ask=None):
    """Plan a project from its brief and return the validated Roadmap. `ask(prompt, repo)` returns a reply's text; it defaults
    to a headless Claude call. A reply that is not JSON, breaks the schema or breaks a planning rule gets one repair retry with
    the errors appended. The candidate roadmap and every raw reply are written beside the project's roadmap either way.
    Raises RoadmapError for a bad brief and GenerationError when no valid roadmap came back."""
    check_brief(brief)
    repo, ask = brief["repo"], ask or claude_reply
    cand_path, raw_path = candidate_paths(repo)
    attempts, errors, rm = [], None, None
    for _ in range(2):  # the first try, and the one repair retry
        prompt = roadmap_prompt(brief, errors)
        reply = ask(prompt, repo)
        rm, errors = None, []
        try:
            rm = parse_roadmap(parse_reply(reply))
            errors = plan_rule_errors(rm)
        except ValueError as e:  # RoadmapError and the JSON error from parse_reply
            errors = [str(e)]
        attempts.append({"prompt": prompt, "response": reply, "errors": errors})
        if not errors:
            break
    ok = not errors
    _write_json(raw_path, {"briefHash": brief_hash(brief), "model": ROADMAP_MODEL, "outcome": "ok" if ok else "failed",
                           "attempts": attempts})
    if not ok:
        raise GenerationError("No valid roadmap after one repair retry. Last errors: %s Raw replies: %s"
                              % (" ".join(errors), raw_path))
    rm.project = os.path.basename(os.path.normpath(repo))
    rm.provenance = Provenance(briefHash=brief_hash(brief), generatedAt=int(time.time()), generatorModel=ROADMAP_MODEL)
    calibration_snapshot_save(repo, rm.provenance.generatedAt, calibration_report()["table"])  # the drift baseline
    _write_json(cand_path, asdict(rm))
    return rm


# ---------- budget: dollars per agent run, one append-only ledger per autopilot run ----------
# The only place prices live. USD per million tokens, by model family: a model name matches the first family it
# contains ("claude-sonnet-5" and "sonnet" both price as sonnet), and a name that matches none is priced as
# PRICE_DEFAULT. Cache writes are at the 5-minute rate. Check these against the pricing page before a real cap.
PRICES = {
    "opus": {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "sonnet": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "haiku": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
}
PRICE_DEFAULT = "sonnet"
TOKEN_CLASSES = ("input", "output", "cache_write", "cache_read")
CAL_WINDOW, CAL_MIN, CAL_MAX = 20, 0.25, 4.0  # the page's calibration: last 20 measured runs, clamped to ×0.25–×4


def budget_ledger_path(run):
    return os.path.join(BASE, "ledgers", "budget-%s.jsonl" % run)


def price_family(model):
    m = (model or "").lower()
    return next((k for k in PRICES if k in m), PRICE_DEFAULT)


def usage_tokens(u):
    """A run's usage block as the four token classes the price table has."""
    u = u or {}
    return {"input": u.get("input_tokens", 0) or 0, "output": u.get("output_tokens", 0) or 0,
            "cache_write": u.get("cache_creation_input_tokens", 0) or 0, "cache_read": u.get("cache_read_input_tokens", 0) or 0}


def eff_tokens(tokens):
    """Runway's effective-token count (the page's estimates use it): fresh input, output, cache writes, a tenth of cache reads."""
    return tokens["input"] + tokens["output"] + tokens["cache_write"] + 0.1 * tokens["cache_read"]


def usd_for(model, tokens):
    """Dollars for token counts (keyed by TOKEN_CLASSES) at the model's table prices."""
    p = PRICES[price_family(model)]
    return sum(tokens[k] * p[k] for k in TOKEN_CLASSES) / 1e6


def budget_rows(run):
    """Every line of the run's budget ledger, oldest first. A missing ledger is an empty run."""
    return [json.loads(line) for line in (rd(budget_ledger_path(run)) or "").splitlines() if line.strip()]


def budget_append(run, ch, tid, model, tokens, est=0, reported_usd=None, kind=""):
    """Append one line for a run's tokens: by class, its dollars at the table prices, its model and kind, and the task's
    estimate in effective tokens when there was one. The file is only ever appended to. Returns the row."""
    row = {"run": run, "ch": ch, "tid": tid, "model": model or "", "kind": kind or "", "family": price_family(model),
           "tokens": {k: int(tokens[k]) for k in TOKEN_CLASSES}, "eff": round(eff_tokens(tokens)),
           "usd": round(usd_for(model, tokens), 6), "est": int(est or 0), "reportedUsd": reported_usd, "at": int(time.time())}
    p = budget_ledger_path(run)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return row


def budget_record(run, ch, tid, model, usage, est=0, reported_usd=None, kind=""):
    """Record a finished task's usage in the run's ledger, once per token. The task's .budget marker holds the tokens
    already recorded, so a second call appends nothing, and a resumed run appends only what it added. Returns the row, or None."""
    tokens = usage_tokens(usage)
    mk = os.path.join(RUNS, ch, tid + ".budget")
    prev = json.loads(rd(mk) or "{}").get("tokens", {})
    delta = {k: max(0, tokens[k] - (prev.get(k, 0) or 0)) for k in TOKEN_CLASSES}
    if not any(delta.values()):
        return None
    row = budget_append(run, ch, tid, model, delta, est, reported_usd, kind)
    os.makedirs(os.path.dirname(mk), exist_ok=True)
    with open(mk + ".tmp", "w") as f:
        json.dump({"tokens": tokens}, f)
    os.replace(mk + ".tmp", mk)
    return row


def budget_record_task(run, ch, tid, model, est=0, kind=""):
    """Record a finished task from its run record (<tid>.json: usage and the reported cost). None if it has no usage yet."""
    rec = json.loads(rd(os.path.join(RUNS, ch, tid + ".json")) or "{}")
    if not rec.get("usage"):
        return None
    return budget_record(run, ch, tid, model, rec["usage"], est, rec.get("cost"), kind)


def budget_calibration(rows):
    """Actual ÷ estimate, as the page calibrates: the geometric mean over the latest measured rows, clamped; 1 until 3 exist."""
    h = [r for r in rows if r.get("est", 0) > 0 and r.get("eff", 0) > 0][-CAL_WINDOW:]
    if len(h) < 3:
        return {"factor": 1.0, "n": len(h)}
    g = math.exp(sum(math.log(r["eff"] / r["est"]) for r in h) / len(h))
    return {"factor": round(min(CAL_MAX, max(CAL_MIN, g)), 4), "n": len(h)}


def budget_report(run, cap=None, est=0, model=None):
    """The run's spend so far, what is left under the cap (None when there is no cap), and the projected dollars of a
    next task of est effective tokens: its estimate × the calibration × the run's dollars per effective token for that
    model (or for all models when none of that model has been measured). usd is None until there is something to measure."""
    rows = budget_rows(run)
    spend = sum(r["usd"] for r in rows)
    tokens = {k: sum(r["tokens"][k] for r in rows) for k in TOKEN_CLASSES}
    cal = budget_calibration(rows)
    same = [r for r in rows if r["eff"] > 0 and r["model"] == (model or "")] or [r for r in rows if r["eff"] > 0]
    rate = sum(r["usd"] for r in same) / sum(r["eff"] for r in same) if same else None
    projected = round(est * cal["factor"] * rate, 4) if rate is not None and est else None
    return {"run": run, "runs": len(rows), "spend": round(spend, 4), "tokens": tokens,
            "cap": cap, "remaining": round(cap - spend, 4) if cap is not None else None,
            "next": {"est": int(est or 0), "model": model or "", "usd": projected, "factor": cal["factor"], "calibrated": cal["n"]}}


# ---------- calibration: a multiplier per (model, kind) from completed runs, so new estimates track what runs really use ----------
CALIB_MIN_SAMPLES, CALIB_LO, CALIB_HI, CALIB_WINDOW = 3, 0.5, 2.0, 20  # fewer usable runs than the minimum is ×1; clamp; latest runs
CALIB_DRIFT = 0.25  # a multiplier that moves more than this share from its value at generation time is flagged in the run report
CALIB_SNAPSHOTS = 20  # generation-time snapshots kept per project, newest first


def calibration_rows():
    """Every completed run in the budget ledgers, all runs together, oldest first: model, kind, estimate and effective tokens."""
    d = os.path.join(BASE, "ledgers")
    rows = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if name.startswith("budget-") and name.endswith(".jsonl"):
            rows += budget_rows(name[len("budget-"):-len(".jsonl")])
    return sorted(rows, key=lambda r: r.get("at", 0))


def calibration_table(rows):
    """Per (model, kind): the multiplier actual ÷ estimate (geometric mean over the latest CALIB_WINDOW usable runs, clamped
    to CALIB_LO–CALIB_HI) and the usable runs n it rests on. A pair with fewer than CALIB_MIN_SAMPLES gets 1.0. A run is usable
    when it has an estimate and effective tokens; the rest are ignored. Sorted by n, most first."""
    groups = {}
    for r in rows:
        if (r.get("est") or 0) > 0 and (r.get("eff") or 0) > 0:
            groups.setdefault((r.get("model") or "", r.get("kind") or ""), []).append(r)
    table = []
    for (model, kind), rs in groups.items():
        h = rs[-CALIB_WINDOW:]
        factor = 1.0
        if len(h) >= CALIB_MIN_SAMPLES:
            g = math.exp(sum(math.log(r["eff"] / r["est"]) for r in h) / len(h))
            factor = round(min(CALIB_HI, max(CALIB_LO, g)), 4)
        table.append({"model": model, "kind": kind, "n": len(h), "factor": factor})
    return sorted(table, key=lambda x: (-x["n"], x["model"], x["kind"]))


def calibration_factor(table, model, kind):
    """The multiplier and sample count for one model and kind. A pair the table has no row for is ×1 with 0 runs."""
    return next((x for x in table if x["model"] == (model or "") and x["kind"] == (kind or "")),
                {"model": model or "", "kind": kind or "", "n": 0, "factor": 1.0})


def calibration_report():
    """The table over every run on this Mac, for the status file and `calibration`."""
    return {"table": calibration_table(calibration_rows()), "min": CALIB_MIN_SAMPLES, "lo": CALIB_LO, "hi": CALIB_HI}


def calibration_text(rep):
    """The multipliers as one line per model and kind: the value, the usable runs it rests on, and whether it is still ×1."""
    if not rep["table"]:
        return "No calibration records yet. A multiplier needs %d finished runs of a model and kind." % rep["min"]
    lines = ["%-34s %-12s %s" % ("model · kind", "multiplier", "runs")]
    for x in rep["table"]:
        note = "" if x["n"] >= rep["min"] else "  (×1 until %d runs)" % rep["min"]
        lines.append("%-34s ×%-11.2f %d%s" % (x["model"] + " · " + x["kind"], x["factor"], x["n"], note))
    return "\n".join(lines)


def calibration_snapshot_path(repo):
    return os.path.join(BASE, "roadmaps", _slug(repo, "roadmap-calibration") + ".json")


def calibration_snapshot_save(repo, generated_at, table):
    """Keep the multipliers a roadmap was generated under, keyed by its provenance.generatedAt, so a later report can
    measure drift from them. Only the newest CALIB_SNAPSHOTS are kept."""
    p = calibration_snapshot_path(repo)
    snaps = json.loads(rd(p) or "{}")
    snaps[str(generated_at)] = [{"model": x["model"], "kind": x["kind"], "factor": x["factor"]} for x in table]
    for k in sorted(snaps, key=int)[:-CALIB_SNAPSHOTS]:
        del snaps[k]
    _write_json(p, snaps)


def calibration_snapshot(repo, generated_at):
    """The multipliers recorded when the roadmap with this generatedAt was generated, or None when none were recorded."""
    snap = json.loads(rd(calibration_snapshot_path(repo)) or "{}").get(str(generated_at))
    return None if snap is None else [dict(x) for x in snap]


def calibration_drift(generated, current):
    """Each (model, kind) measured now (n usable runs), against its multiplier at generation time. A pair the generation
    had no row for was ×1 then, as the planner's lookup gives. flagged when it moved more than CALIB_DRIFT. Largest move
    first. None when there is no generation-time snapshot to measure against."""
    if generated is None:
        return None
    gen = {(x["model"], x["kind"]): x["factor"] for x in generated}
    out = []
    for x in current:
        was = gen.get((x["model"], x["kind"]), 1.0)
        move = x["factor"] / was - 1 if was else 0
        out.append({"model": x["model"], "kind": x["kind"], "n": x["n"], "generated": was, "current": x["factor"],
                    "moved": round(move, 4), "flagged": abs(move) > CALIB_DRIFT})
    return sorted(out, key=lambda d: (-abs(d["moved"]), d["model"], d["kind"]))


# ---------- autopilot: work the roadmap's ready tasks through the merge gate, saving each state change as it happens ----------
# The roadmap file is the queue, and its task statuses are the only state: each transition is saved before the next
# step, so killing the loop loses no progress. A task's run lives in runs/ap-<key>/ with the same files a page chunk
# uses (meta, status, exit, checks), so automerge reads it unchanged. Opt-in: only the `autopilot` command starts it.
AUTOPILOT_PARALLEL = 1  # chunks in flight at once; --parallel changes it for one run
AUTOPILOT_POLL = 5  # seconds between looks at the runs
AUTOPILOT_DENY = ["rm", "sudo", "curl", "wget", "chmod", "chown", "ssh", "scp", "dd", "mkfs", "launchctl",
                  "git push", "git reset --hard", "git clean"]  # the page's NEVER_RULES, for an agent nobody watches


class AutopilotError(Exception):
    """The loop cannot start or cannot go on: another loop holds the lock, or the roadmap is missing."""


def roadmap_tasks(rm):
    """Every task in a roadmap, in roadmap order."""
    return [t for pi in rm.pis for s in pi.sprints for t in s.tasks]


def sprint_waits(rm):
    """{task key: why it waits} for tasks held back by sprint order: every earlier sprint of the same PI must have at
    least one task and all of them done or dropped (an empty sprint is not done), unless the task or its sprint is
    independent."""
    out = {}
    for pi in rm.pis:
        for i, sp in enumerate(pi.sprints):
            for prev in pi.sprints[:i]:
                left = [t for t in prev.tasks if t.status not in ("done", "dropped")]
                if not prev.tasks:
                    why = "%s: no tasks planned yet" % prev.title
                elif left:
                    why = "%s: %d task%s left" % (prev.title, len(left), "" if len(left) == 1 else "s")
                else:
                    continue
                for t in sp.tasks:
                    if not (t.independent or sp.independent):
                        out[t.key] = "Waits for " + why
                break
    return out


def ready_tasks(rm):
    """The todo tasks whose dependsOn are all done and whose earlier sprints are finished (see sprint_waits), in
    roadmap order. A blocked or parked task holds back only its own dependents."""
    tasks = roadmap_tasks(rm)
    done = {t.key for t in tasks if t.status == "done"}
    waits = sprint_waits(rm)
    return [t for t in tasks if t.status == "todo" and t.key not in waits and all(d in done for d in t.dependsOn)]


def set_task_status(repo, key, status):
    """Save one task's new status to the roadmap file before anything else happens. Returns the roadmap."""
    rm = load_roadmap(repo)
    if rm is None:
        raise AutopilotError("This project has no roadmap to update.")
    task = next((t for t in roadmap_tasks(rm) if t.key == key), None)
    if task is None:
        raise AutopilotError("No task %s in the roadmap." % key)
    task.status = status
    save_roadmap(repo, rm)
    return rm


def _ap_chunk(key):
    if not TASK_ID_RE.fullmatch(key or ""):
        raise AutopilotError("The task key %r cannot name a run folder: use letters, digits, dot, dash or underscore." % key)
    return "ap-" + key


def _ap_dir(key):
    return os.path.join(RUNS, _ap_chunk(key))


def _ap_alive(d):
    """Whether the run launched into directory d (it has a _pgid) still has a live process group."""
    pg = rd(os.path.join(d, "_pgid"))
    if not pg:
        return False
    try:
        os.killpg(int(pg), 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True


def _ap_prompt(t):
    return ("You are making one change in this checkout, for the task below. Edit files to do it, run the project's tests if "
            "you can, and leave the change in the working tree: do not commit, push or reset anything.\n\n"
            "TASK: %s\n%s\nACCEPTANCE CRITERIA:\n%s\n") % (
        t.title, "\nBRIEF:\n" + t.detail[:2000] + "\n" if t.detail else "", t.acceptance or "(none given: finish the brief)")


def _ap_run_sh(t, ch, d, wtp, repo, base, model):
    """The run script for one task: the agent in its worktree, its changes committed, then the project's checks through
    this helper. It writes <key>.exit and <key>.status like a page chunk does; the autopilot reads those."""
    k = t.key
    agent = [shlex.quote(CLAUDE), "-p", '"$(cat "$D/%s.prompt.md")"' % k, "--output-format", "stream-json", "--verbose",
             "--permission-mode", "acceptEdits"]
    if model:
        agent += ["--model", shlex.quote(model)]
    agent += ["--disallowedTools"] + [shlex.quote("Bash(%s:*)" % c) for c in AUTOPILOT_DENY]
    return "\n".join([
        "#!/bin/bash",
        "# Runway autopilot task %s, chunk %s: one agent in its worktree, then the checks." % (k, ch),
        'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.claude/local:$PATH"',
        "D=%s" % shlex.quote(d),
        "cd %s || { echo 97 > \"$D/%s.exit\"; echo done > \"$D/%s.status\"; exit 0; }" % (shlex.quote(wtp), k, k),
        "echo running > \"$D/%s.status\"" % k,
        "%s 2>>\"$D/%s.err\" < /dev/null | tee -a \"$D/%s.stream.jsonl\" | grep -E '\"type\": ?\"result\"' | tail -1 > \"$D/%s.json\"; echo \"${PIPESTATUS[0]}\" > \"$D/%s.exit\""
        % (" ".join(agent), k, k, k, k),
        'if [ "$(cat "$D/%s.exit")" = 0 ]; then' % k,
        '  git add -A && { git diff --cached --quiet || git commit -q -m %s -m %s; }' % (
            shlex.quote("Runway: " + t.title), shlex.quote("Task %s, chunk %s." % (k, ch))),
        '  python3 %s checks %s %s >/dev/null 2>&1' % (shlex.quote(os.path.abspath(__file__)), ch, k),
        "fi",
        "echo done > \"$D/%s.status\"" % k,
        "",
    ])


ENV_LOG_CHARS = 4000  # the end of a setup log the page shows
SETUP_TIMEOUT = 900  # seconds the project's setup command may take in a new worktree

# ---------- a task's environment: its port block, its named variables, what its setup gets ----------
# Opt-in: a project that declares none of setup, env or ports gets none of this. The run folder keeps _env.json, which
# has the names and the port block only; the values are read from ~/.ai-planner/env/<repo folder>.env each time they
# are needed and go to a process's environment only, never to a file in the worktree, the run folder or git.
ENV_SPEC = "_env.json"  # in the task's run folder
PORTS_FILE = "ports.json"  # the registry of port blocks, in ~/.ai-planner
PORT_FIRST = 41000  # the first port of the first block; blocks follow it, PORTS_MAX ports each
PORT_BLOCKS = 1000  # blocks the registry can hand out: ports 41000 up to 60999


def env_file(repo):
    """The file that holds a project's variable values, outside every repo (the README says where)."""
    return os.path.join(BASE, "env", os.path.basename(os.path.normpath(repo)) + ".env")


def read_env_file(path):
    """NAME=value lines from a project's env file, as a dict. Blank lines, # comments and export are allowed; matching
    quotes around a value are removed. A missing file is an empty dict. Lines that are not NAME=value are skipped."""
    vals = {}
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return vals
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, val = line.partition("=")
        if not sep or not ENV_NAME.fullmatch(name.strip()):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        vals[name.strip()] = val
    return vals


def _ap_port_block(repo, k):
    """The base port of task k's block in the registry ~/.ai-planner/ports.json. The registry is locked while it is read
    and written. A task keeps its block when it is prepared again; a new task gets the lowest block no task holds, so
    two tasks never share one."""
    os.makedirs(BASE, exist_ok=True)
    with open(os.path.join(BASE, "ports.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reg = _ap_read(os.path.join(BASE, PORTS_FILE)) or {}
        blocks = reg.get("blocks") or []
        mine = next((b for b in blocks if b.get("repo") == repo and b.get("task") == k), None)
        if mine is None:
            held = {b.get("index") for b in blocks}
            free = [i for i in range(PORT_BLOCKS) if i not in held]
            if not free:
                raise AutopilotError("Every port block in %s is in use, so %s cannot get its ports." % (PORTS_FILE, k))
            mine = {"repo": repo, "task": k, "index": free[0], "at": int(time.time())}
            blocks.append(mine)
            write_json_atomic(os.path.join(BASE, PORTS_FILE), {"blocks": blocks})
        fcntl.flock(lock, fcntl.LOCK_UN)
    return PORT_FIRST + mine["index"] * PORTS_MAX


def task_env_spec(repo, k, cfg):
    """What task k's environment is, from the project's setup, env and ports keys. None when it declares none of them.
    Taking its port block is part of this, so a block is held from the moment its task is prepared."""
    if not (cfg.get("setup") or cfg.get("env") or cfg.get("ports")):
        return None
    spec = {"task": k, "repo": repo, "names": list(cfg.get("env") or []), "ports": None}
    if cfg.get("ports"):
        spec["ports"] = {"base": _ap_port_block(repo, k), "count": cfg["ports"]}
    return spec


def task_env_vars(spec):
    """The variables a task's setup, agent and checks run with: RUNWAY_TASK, its port block (RUNWAY_PORT_BASE and
    RUNWAY_PORTS, when it has ports) and the named variables it was given. A name missing from the env file is left out."""
    vals = read_env_file(env_file(spec["repo"]))
    out = {n: vals[n] for n in spec["names"] if n in vals}
    out["RUNWAY_TASK"] = spec["task"]
    if spec["ports"]:
        base, count = spec["ports"]["base"], spec["ports"]["count"]
        out["RUNWAY_PORT_BASE"] = str(base)
        out["RUNWAY_PORTS"] = ",".join(str(p) for p in range(base, base + count))
    return out


def run_env(ch):
    """The environment a chunk's agent and checks run with, from its run folder's _env.json. {} for a run without one."""
    spec = _ap_read(os.path.join(RUNS, ch, ENV_SPEC))
    return task_env_vars(spec) if spec else {}


TEARDOWN_TIMEOUT = 300  # seconds a project's teardown command may take
ENV_TORN = "_env.torndown"  # in the run folder: the environment was torn down, so env-gc leaves it alone until it is prepared again
ENV_GC_EVERY = 3600  # seconds between env-gc passes, which tick runs
ENV_GC_AGE = 86400  # a chunk that ended longer ago than this has its environment collected


def _env_gc_state():
    """The file where env-gc records when it last ran, and which run folders it tore down."""
    return os.path.join(BASE, "env-gc.json")


def _ap_port_release(repo, k):
    """Give task k's port block back to the registry, so a later task can take it. Locked like _ap_port_block."""
    os.makedirs(BASE, exist_ok=True)
    with open(os.path.join(BASE, "ports.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        blocks = (_ap_read(os.path.join(BASE, PORTS_FILE)) or {}).get("blocks") or []
        kept = [b for b in blocks if not (b.get("repo") == repo and b.get("task") == k)]
        if len(kept) != len(blocks):
            write_json_atomic(os.path.join(BASE, PORTS_FILE), {"blocks": kept})
        fcntl.flock(lock, fcntl.LOCK_UN)


def _teardown_cmd(cfg):
    """The project's teardown command: a list runs in order, and stops at the first failure, as setup does. None when unset."""
    cmd = cfg.get("teardown")
    if isinstance(cmd, list):
        cmd = " && ".join(cmd)
    return cmd or None


def env_teardown(d, log=lambda m: None):
    """Tear down the environment of the task whose run folder is d, once: run the project's teardown command with the task's
    variables, in its worktree (or in the repo, when the worktree is gone), release its port block, and mark the folder so
    it is not done twice. Its setup is due again if the task is prepared again. Returns False when there was nothing to do.
    Nothing here raises: a teardown that fails is logged and recorded in <key>.teardown.json, and never blocks a caller."""
    spec = _ap_read(os.path.join(d, ENV_SPEC))
    if not spec or not spec.get("repo") or not spec.get("task") or os.path.exists(os.path.join(d, ENV_TORN)):
        return False
    k, repo = spec["task"], spec["repo"]
    wtp = os.path.join(repo, ".claude", "worktrees", "runway-" + k)
    rec = {"cmd": None, "at": int(time.time())}
    try:
        rec["cmd"] = _teardown_cmd(project_config(repo))
        cwd = wtp if os.path.isdir(wtp) else repo
        if rec["cmd"] and os.path.isdir(cwd):
            path = os.path.join(d, k + ".teardown.log")
            open(path, "w").close()  # one teardown's output per file
            rc, timed_out = _run_check(rec["cmd"], cwd, TEARDOWN_TIMEOUT, path, env=task_env_vars(spec))
            with open(path, "a") as out:
                out.write("[teardown %s]\n" % ("timed out" if timed_out else "exit %s" % rc))
            rec.update(rc=rc, timedOut=timed_out, cwd=cwd, log=path)
            log("teardown %s: %s (output in %s)" % (k, "timed out" if timed_out else "exit %s" % rc, path))
    except Exception as e:  # logged below; the ports are still released and the folder still marked
        rec["error"] = str(e)[:300]
        log("teardown %s failed: %s" % (k, rec["error"]))
    try:
        _ap_port_release(repo, k)
        # the setup record stays for the page; ENV_TORN makes the next prepare run setup again, in the fresh environment
        write_json_atomic(os.path.join(d, k + ".teardown.json"), rec)
        open(os.path.join(d, ENV_TORN), "w").close()
    except Exception as e:
        log("teardown %s: could not finish: %s" % (k, str(e)[:300]))
    return True


def _ap_ended_at(d, k):
    """When the chunk in run folder d ended: the newest of its exit, status and stop marks. None while it is live or never ran."""
    if _ap_alive(d):
        return None
    marks = [os.path.join(d, k + ".exit"), os.path.join(d, k + ".status"), os.path.join(d, "_chunk.stopped")]
    times = [os.path.getmtime(p) for p in marks if os.path.exists(p)]
    return max(times) if times else None


def env_gc(now=None, log=lambda m: None):
    """Tear down the environments nothing needs any more, and record the pass in ENV_GC_STATE. An environment goes when its
    worktree is gone and its chunk is not running, or when its chunk ended over ENV_GC_AGE ago. A live one is left alone.
    Returns the run folders (chunks) whose environments were torn down."""
    now = time.time() if now is None else now
    done = []
    for spec_path in sorted(glob.glob(os.path.join(RUNS, "*", ENV_SPEC))):
        d = os.path.dirname(spec_path)
        spec = _ap_read(spec_path) or {}
        if not spec.get("repo") or not spec.get("task") or os.path.exists(os.path.join(d, ENV_TORN)) or _ap_alive(d):
            continue
        ended = _ap_ended_at(d, spec["task"])
        wt_gone = not os.path.isdir(os.path.join(spec["repo"], ".claude", "worktrees", "runway-" + spec["task"]))
        if wt_gone or (ended is not None and now - ended > ENV_GC_AGE):
            if env_teardown(d, log):
                done.append(os.path.basename(d))
    write_json_atomic(_env_gc_state(), {"at": int(now), "tornDown": done})
    return done


def _log_tail(path, lines=12, chars=400):
    """The last lines of a log, for a blocker card. Short enough that the card keeps the message."""
    text = "\n".join((rd(path) or "").splitlines()[-lines:])
    return text[-chars:]


def _ap_env_failure(d, k, command, detail):
    """Finish task k before its agent starts, because its environment is not ready. The run folder gets the report an
    agent would have ended with: a ```runway block of type environment, which the blocker card shows. The agent is not
    started, so no tokens are spent. Its exit is 97, the code a run gives when it cannot start."""
    block = {"status": "blocked", "blockers": [{"type": "environment", "detail": detail, "command": command}]}
    text = "The task's environment is not ready, so its agent was not started.\n\n```runway\n%s\n```" % json.dumps(block)
    write_json_atomic(os.path.join(d, k + ".json"), {"type": "result", "subtype": "environment", "is_error": True, "result": text,
                                                     "num_turns": 0, "duration_ms": 0, "total_cost_usd": 0, "usage": {}})
    with open(os.path.join(d, k + ".exit"), "w") as f:
        f.write("97\n")
    with open(os.path.join(d, k + ".status"), "w") as f:
        f.write("done\n")


def _secret_values(env):
    """The values a task's environment took from its env file: every variable but the RUNWAY_ ones, which are not secret."""
    return [v for n, v in (env or {}).items() if not n.startswith("RUNWAY_") and v]


def _env_redact(text, secrets):
    """The text with every secret value blanked, so a setup that echoes one cannot leak it to the page or a blocker card."""
    for v in sorted({v for v in secrets if v}, key=len, reverse=True):  # an empty value would match everywhere
        text = text.replace(v, "••••")
    return text


def _ap_setup(d, k, cmd, wtp, log, env=None):
    """Run the project's setup command in a new worktree, before its agent starts, with the task's environment (env).
    Its output and exit code go to <key>.setup.log and <key>.setup.json, and to the autopilot log. A failure finishes
    the task as an environment problem, with the command and the log tail (see _ap_env_failure), and raises
    AutopilotError, so the task is blocked here instead of its agent finding missing dependencies later."""
    path = os.path.join(d, k + ".setup.log")
    open(path, "w").close()  # one run's output per file
    try:
        os.remove(os.path.join(d, k + ".setup.json"))  # a re-run is preparing again until it writes its result
    except FileNotFoundError:
        pass
    rc, timed_out = _run_check(cmd, wtp, SETUP_TIMEOUT, path, env=env)
    state = "timed out" if timed_out else "exit %s" % rc
    with open(path, "a") as out:
        out.write("[setup %s]\n" % state)
    write_json_atomic(os.path.join(d, k + ".setup.json"), {"cmd": cmd, "rc": rc, "timedOut": timed_out, "at": int(time.time())})
    log("setup %s: %s (output in %s)" % (k, state, path))
    if timed_out or rc != 0:
        _ap_env_failure(d, k, cmd, "Setup failed (%s), so the agent was not started. Log tail:\n%s" % (
            state, _env_redact(_log_tail(path), _secret_values(env))))
        raise AutopilotError("The setup command `%s` failed in the new worktree (%s), so the task was not run. "
                             "Its output is in %s." % (cmd, state, path))


def env_record(d, k):
    """What the page shows for task k's environment, from its run folder: its state (preparing, ready or failed), its port
    block, its variable names and whether each is set, and the setup log's last part with secret values blanked. None when
    the task has no environment, which is when its project declares none of setup, env or ports."""
    spec = _ap_read(os.path.join(d, ENV_SPEC))
    setup = _ap_read(os.path.join(d, k + ".setup.json"))
    report = _ap_read(os.path.join(d, k + ".json")) or {}
    failed_before_setup = rd(os.path.join(d, k + ".exit")) == "97\n" and report.get("subtype") == "environment"
    if spec is None and setup is None and not failed_before_setup:
        return None
    repo = spec["repo"] if spec else None
    vals = read_env_file(env_file(repo)) if repo else {}
    secrets = list(vals.values())
    rec = {"task": k, "repo": repo, "names": [{"name": n, "set": n in vals} for n in (spec or {}).get("names", [])],
           "ports": {"base": spec["ports"]["base"], "count": spec["ports"]["count"]} if spec and spec.get("ports") else None,
           "setup": None, "detail": None}
    if setup is not None:
        rec["setup"] = {"cmd": setup.get("cmd"), "rc": setup.get("rc"), "timedOut": bool(setup.get("timedOut")), "at": setup.get("at"),
                        "log": _env_redact(_log_tail(os.path.join(d, k + ".setup.log"), lines=60, chars=ENV_LOG_CHARS), secrets)}
        rec["state"] = "ready" if setup.get("rc") == 0 and not setup.get("timedOut") else "failed"
    elif rd(os.path.join(d, k + ".setup.log")) is not None and not failed_before_setup:
        rec["state"] = "preparing"
    elif failed_before_setup:
        rec["state"] = "failed"
    else:
        rec["state"] = "ready"  # no setup command: its variables and ports are in place
    if rec["state"] == "failed":
        blockers = (parse_outcome(report.get("result") or "") or {}).get("blockers") or []
        rec["detail"] = _env_redact(blockers[0].get("detail") or "", secrets) if blockers else ""
    return rec


def all_envs():
    """The environments of the autopilot's task runs from the last 14 days, for status.json. A run folder that cannot be read
    is reported as an error record, so one bad folder does not hide the others."""
    out, cutoff = [], time.time() - 14 * 86400
    for d in sorted(glob.glob(os.path.join(RUNS, "ap-*"))):
        if not os.path.isdir(d) or os.path.getmtime(d) < cutoff:
            continue
        k = os.path.basename(d)[len("ap-"):]
        try:
            rec = env_record(d, k)
        except Exception as e:
            rec = {"task": k, "error": str(e)[:200]}
        if rec:
            out.append(rec)
    return out


def env_info(repo):
    """A project's environment for its form: the variable names it passes, whether each is set in its env file, the file's
    path and its port count. Names and booleans only: a value never leaves the env file."""
    cfg = project_config(repo)
    vals = read_env_file(env_file(repo))
    return {"repo": repo, "file": env_file(repo), "exists": os.path.exists(env_file(repo)), "ports": cfg.get("ports") or 0,
            "setup": bool(cfg.get("setup")), "names": [{"name": n, "set": n in vals} for n in cfg.get("env") or []]}


def retry_setup(repo, k):
    """Run task k's failed setup again in its own worktree, then start its agent. Only for a task whose environment failed.
    A blocked task's worktree may have been removed: it is made again from its branch, and setup runs in it."""
    rec = env_record(_ap_dir(k), k)
    if rec is None or rec["state"] != "failed":
        raise AutopilotError("Task %s has no failed environment to retry." % k)
    t = next((x for x in roadmap_tasks(load_roadmap(repo)) if x.key == k), None)
    if t is None:
        raise AutopilotError("Task %s is not in the project's roadmap any more, so it cannot be started." % k)
    base = project_config(repo).get("baseBranch") or git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
    d = _ap_dir(k)
    for suffix in (".status", ".exit", ".json"):  # the failure's result: the new run writes its own
        try:
            os.remove(os.path.join(d, k + suffix))
        except FileNotFoundError:
            pass
    _ap_prepare(repo, t, base, fresh=False)
    got = launch(_ap_chunk(k))
    if got not in ("launched", "already launched"):
        raise AutopilotError("Could not start its run (%s)." % got)
    return got


def _ap_setup_done(d, k, cmd):
    """Whether this task's worktree already had the setup command run and it exited 0."""
    rec = _ap_read(os.path.join(d, k + ".setup.json")) or {}
    return rec.get("cmd") == cmd and rec.get("rc") == 0


def _ap_prepare(repo, t, base, fresh, log=lambda m: None):
    """Make the task's worktree (reusing it, and its branch, when they exist), then write its meta, prompt and run script.
    fresh clears a previous run's files so a task re-run starts clean. Idempotent: a crash part-way is safe to repeat.
    A project with a "setup" command in .runway.json runs it once in a new worktree (and again if it never succeeded)."""
    k, ch, d = t.key, _ap_chunk(t.key), _ap_dir(t.key)
    os.makedirs(d, exist_ok=True)
    torn = os.path.exists(os.path.join(d, ENV_TORN))  # torn down since its last setup: set up again
    if fresh:
        for suffix in ("_pgid", ENV_SPEC, ENV_TORN, ".status", ".exit", ".json", ".checks.json"):
            try:
                os.remove(os.path.join(d, suffix if suffix.startswith("_") else k + suffix))
            except FileNotFoundError:
                pass
    wt = "runway-" + k
    wtp = os.path.join(repo, ".claude", "worktrees", wt)
    branch = "runway-task/" + k
    created = not os.path.isdir(wtp)
    if created:
        os.makedirs(os.path.dirname(wtp), exist_ok=True)
        if git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + branch)[0] == 0:
            rc, out = git(repo, "worktree", "add", "-q", wtp, branch)
        else:
            rc, out = git(repo, "worktree", "add", "-q", "-b", branch, wtp, base)
        if rc:
            raise AutopilotError("Could not create the worktree for %s: %s" % (k, out[-300:]))
    try:
        cfg = project_config(repo)
    except ConfigError as e:
        raise AutopilotError(str(e))
    cmd = cfg.get("setup")
    if isinstance(cmd, list):  # a list runs in order, and stops at the first failure
        cmd = " && ".join(cmd)
    spec = task_env_spec(repo, k, cfg)  # None unless the project declares setup, env or ports
    if spec is None and cfg.get("teardown"):  # a teardown alone still needs the task's key and folder to find its environment
        spec = {"task": k, "repo": repo, "names": [], "ports": None}
    if spec:  # the names and the port block only, before anything can fail: the page shows them; values are read again at run time
        write_json_atomic(os.path.join(d, ENV_SPEC), spec)
    values = task_env_vars(spec) if spec else {}
    missing = [n for n in (spec or {}).get("names", []) if n not in values]
    if missing:  # the agent would start without them: finish the task here, before any setup or tokens
        _ap_env_failure(d, k, "", "These variables are not set in %s, so the agent was not started: %s. Add them there, then run the task again." % (
            env_file(repo), ", ".join(missing)))
        raise AutopilotError("Variables %s are not set in %s, so the task was not run." % (", ".join(missing), env_file(repo)))
    if cmd and (created or torn or not _ap_setup_done(d, k, cmd)):
        _ap_setup(d, k, cmd, wtp, log, values)
    if spec:  # a task prepared again after its teardown has a new environment, so it is no longer torn down
        try:
            os.remove(os.path.join(d, ENV_TORN))
        except FileNotFoundError:
            pass
    fork = git(repo, "rev-parse", "--verify", base)[1].strip()
    write_json_atomic(os.path.join(d, k + ".meta"), {"repo": repo, "wt": wt, "base": base, "fork": fork, "dirty": 0})
    with open(os.path.join(d, k + ".prompt.md"), "w") as f:
        f.write(_ap_prompt(t))
    with open(os.path.join(d, "run.sh"), "w") as f:  # launch() runs a chunk's run.sh
        f.write(_ap_run_sh(t, ch, d, wtp, repo, base, t.model))


def write_json_atomic(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w") as f:
        json.dump(obj, f)
    os.replace(p + ".tmp", p)


def _ap_note(key, outcome, msg):
    """What the autopilot decided about a task, kept next to its run for the page and for reading later."""
    write_json_atomic(os.path.join(_ap_dir(key), key + ".autopilot.json"), {"outcome": outcome, "msg": msg, "at": int(time.time())})


def _ap_merge(t):
    """Gate and merge one finished task through automerge. Returns (outcome, msg), the outcomes automerge reports, or
    "error" when the agent run itself did not exit 0 (so nothing is merged)."""
    k, ch = t.key, _ap_chunk(t.key)
    exit_code = (rd(os.path.join(_ap_dir(k), k + ".exit")) or "").strip()
    if exit_code != "0":
        return "error", "The agent run exited %s, so nothing was merged." % (exit_code or "without a result")
    t0 = time.perf_counter()
    r = automerge(ch, k)
    if r["outcome"] != "locked":  # a locked merge did nothing: its time is only the wait, and the next look times the real one
        timing_add(ch, k, merge_ms=_ms(t0))  # the merge train's merge bucket, so the run report can total git time
    return r["outcome"], r["msg"]


AP_STOP_FAILED = ("failures", "error", "interrupted")  # the stops that are not a normal end: the CLI exits 1 for them


def _ap_paths(repo):
    """The per-repo files an autopilot run keeps in BASE: its state (run id, cap, failure limit, why it last stopped) and
    the stop file a person or the page writes to ask the loop to stop."""
    slug = _slug(repo, "autopilot")
    return os.path.join(BASE, "autopilot-%s.state.json" % slug), os.path.join(BASE, "autopilot-%s.stop" % slug)


def _ap_run_id(repo):
    """The budget ledger a run's spend goes to. Its name is new for each fresh run, and kept on resume."""
    return "autopilot-%s-%d" % (_slug(repo, "autopilot"), int(time.time()))


def _ap_running(repo):
    """The tasks in flight, read from the roadmap file now."""
    return [t for t in roadmap_tasks(load_roadmap(repo)) if t.status == "running"]


def _ap_spend(run, t):
    """Record a task's spend in the run's ledger once its run has reported usage. Safe to call again: a task's tokens are
    counted once."""
    budget_record_task(run, _ap_chunk(t.key), t.key, t.model, t.estTokens, t.kind)


def _ap_fits(run, t, cap, running):
    """Whether task t may start under the cap. Its projected dollars must fit in what is left after the dollars the tasks
    in flight are projected to cost. A task with no projection yet (no runs measured) starts while anything is left.
    Returns (fits, detail) where detail holds the numbers for the stop reason."""
    rep = budget_report(run, cap, t.estTokens, t.model)
    committed = sum(budget_report(run, None, r.estTokens, r.model)["next"]["usd"] or 0 for r in running)
    left = round(cap - rep["spend"] - committed, 4)
    projected = rep["next"]["usd"]
    fits = left > 0 and (projected is None or projected <= left)
    return fits, {"projected": projected, "left": left, "spend": rep["spend"]}


def _ap_clean(repo, keys):
    """Remove the worktrees of the given tasks, which are not running. Git refuses to remove a worktree with uncommitted
    changes, so one of those is kept and named in the result: nobody's edits are lost. Returns {key: why it was kept}."""
    kept = {}
    for k in keys:
        wtp = os.path.join(repo, ".claude", "worktrees", "runway-" + k)
        if not os.path.isdir(wtp):
            env_teardown(_ap_dir(k))  # no worktree left to hold an environment: it goes now
            continue
        rc, out = git(repo, "worktree", "remove", wtp)
        if rc:
            kept[k] = (out.strip() or "git would not remove it")[-200:]
        else:
            env_teardown(_ap_dir(k))  # the worktree is gone, so the teardown runs in the repo
    git(repo, "worktree", "prune")
    return kept


def _ap_save(path, st):
    write_json_atomic(path, st)


def _ap_read(path):
    try:
        return json.loads(rd(path) or "null")
    except json.JSONDecodeError:
        return None


def autopilot(repo, parallel=AUTOPILOT_PARALLEL, log=lambda m: None, launcher=None, poll=AUTOPILOT_POLL, sleep=time.sleep,
              cap=None, max_failures=None, resume=False):
    """Work the roadmap: launch ready tasks (up to `parallel` at once), gate and merge finished ones, and mark each one
    done or blocked, saving every change. launcher(chunk) starts a chunk's run.sh; it defaults to launch().

    It stops, and says why in result["stop"] as {"reason", "detail"}, when: the roadmap has nothing left to start ("roadmap");
    the next task would overrun `cap` dollars ("cap"); `max_failures` tasks in a row were blocked ("failures"); a stop file
    was written ("signal"). Once a stop is decided nothing new starts, the tasks in flight finish and are gated as usual,
    and the loop ends. Every stop path removes the worktrees of tasks that are not running. An exception ends it with reason
    "error" or "interrupted"; tasks in flight keep their worktrees so a resume can carry them on.
    resume=True carries on the last run of this roadmap: its budget ledger, and its cap and failure limit unless given here;
    its failure streak starts again at 0. Done tasks are never run again. Returns {"done", "blocked", "left"}; the run's
    stop reason and any worktree that was kept are in its state file (see _ap_paths)."""
    if type(parallel) is not int or parallel < 1:
        raise AutopilotError("parallel must be a whole number of at least 1, not %r." % (parallel,))
    if cap is not None and (type(cap) not in (int, float) or cap <= 0):
        raise AutopilotError("The dollar cap must be an amount above zero, not %r." % (cap,))
    if max_failures is not None and (type(max_failures) is not int or max_failures < 1):
        raise AutopilotError("max_failures must be a whole number of at least 1, not %r." % (max_failures,))
    launcher = launcher or launch
    state_path, stop_path = _ap_paths(repo)
    lock = _lock(os.path.join(BASE, "autopilot-%s.lock" % _slug(repo, "autopilot")), 0)
    if lock is None:
        raise AutopilotError("Another autopilot is already working this roadmap.")
    result = {"done": [], "blocked": {}, "left": 0}
    st = None  # set once this run owns the state file; only then does the run write its stop reason and clean up
    stop = None
    try:
        base = project_config(repo).get("baseBranch") or git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
        if not base or base == "HEAD":
            raise AutopilotError("The repo is not on a branch, so there is nothing to merge into.")
        prev = _ap_read(state_path)
        if resume:
            if prev is None:
                raise AutopilotError("Nothing to resume: this roadmap has no earlier autopilot run.")
            st = {"run": prev["run"], "cap": prev.get("cap") if cap is None else cap,
                  "maxFailures": prev.get("maxFailures") if max_failures is None else max_failures,
                  "streak": 0, "startedAt": prev.get("startedAt"), "resumedAt": int(time.time()), "stop": None}
        else:
            if prev is not None and prev.get("stop") is None:
                raise AutopilotError("An earlier run of this roadmap was interrupted before it recorded why it stopped. "
                                     "Resume it with --resume, so its spend carries on.")
            st = {"run": _ap_run_id(repo), "cap": cap, "maxFailures": max_failures, "streak": 0,
                  "startedAt": int(time.time()), "stop": None}
        _ap_save(state_path, st)
        cap = st["cap"]  # a resumed run keeps its cap unless one is given
        if cap is not None:
            log("dollar cap $%.2f" % cap)
        decided = None  # (reason, detail) once a stop is decided: nothing new starts after it

        def decide(reason, detail):
            nonlocal decided
            if decided is None:
                decided = (reason, detail)
                log("stopping (%s): %s" % (reason, detail))

        def outcome_seen(ok):
            st["streak"] = 0 if ok else st["streak"] + 1
            _ap_save(state_path, st)
            if st["maxFailures"] and st["streak"] >= st["maxFailures"]:
                decide("failures", "%d tasks in a row were blocked; the limit is %d." % (st["streak"], st["maxFailures"]))

        def block(key, outcome, why):
            _ap_note(key, outcome, why)
            set_task_status(repo, key, "blocked")
            result["blocked"][key] = why
            log("blocked %s: %s" % (key, why))
            outcome_seen(False)

        while True:
            rm = load_roadmap(repo)
            if rm is None:
                raise AutopilotError("This project has no roadmap to work.")
            if decided is None and os.path.exists(stop_path):
                decide("signal", "A stop was requested (%s)." % stop_path)
            tasks = roadmap_tasks(rm)
            # 1. A running task: finished goes to review, launched and alive waits, launched and dead is blocked.
            #    A running task that was never launched (the loop died between saving and launching) is launched now.
            #    Its spend is recorded as soon as its run reports usage, whatever the outcome.
            for t in [t for t in tasks if t.status == "running"]:
                _ap_spend(st["run"], t)
                d, k = _ap_dir(t.key), t.key
                if (rd(os.path.join(d, k + ".status")) or "").strip() == "done":
                    set_task_status(repo, k, "review")
                elif os.path.exists(os.path.join(d, "_pgid")):
                    if not _ap_alive(d):
                        block(k, "error", "Its run stopped before it finished. Run it again from the page.")
                else:
                    try:
                        _ap_launch(repo, t, base, launcher, fresh=False, log=log)
                    except AutopilotError as e:
                        block(k, "error", str(e))
            # 2. A finished task goes through the merge gate. Locked is not a verdict: it stays in review for the next look.
            for t in [t for t in roadmap_tasks(load_roadmap(repo)) if t.status == "review"]:
                _ap_spend(st["run"], t)  # a finished task's usage may only land after its status, so record it here too
                outcome, why = _ap_merge(t)
                if outcome == "merged":
                    _ap_note(t.key, outcome, why)
                    set_task_status(repo, t.key, "done")
                    result["done"].append(t.key)
                    log("done %s" % t.key)
                    outcome_seen(True)
                elif outcome != "locked":
                    block(t.key, outcome, why)
            # 3. Fill the free slots with ready tasks, in roadmap order, none started past a stop or past the cap. Each
            #    task has its own worktree, so none is shared. A task that does not fit the cap waits for the tasks in
            #    flight to finish, then ends the run if it still does not fit.
            for t in ready_tasks(load_roadmap(repo)):
                running = _ap_running(repo)
                if decided is not None or len(running) >= parallel:
                    break
                if cap is not None:
                    fits, info = _ap_fits(st["run"], t, cap, running)
                    if not fits:
                        if not running:
                            decide("cap", "%s would cost about $%s with $%s left under the $%.2f cap (spent $%.4f)." % (
                                t.key, "?" if info["projected"] is None else "%.4f" % info["projected"],
                                "%.4f" % info["left"], cap, info["spend"]))
                        break
                try:
                    _ap_chunk(t.key)
                except AutopilotError as e:  # no run folder for this key, so nothing to note: record it on the task alone
                    set_task_status(repo, t.key, "blocked")
                    result["blocked"][t.key] = str(e)
                    log("blocked %s: %s" % (t.key, e))
                    outcome_seen(False)
                    continue
                set_task_status(repo, t.key, "running")
                log("launch %s: %s" % (t.key, t.title))
                try:
                    _ap_launch(repo, t, base, launcher, fresh=True, log=log)
                except AutopilotError as e:
                    block(t.key, "error", str(e))
            rm = load_roadmap(repo)
            tasks = roadmap_tasks(rm)
            active = [t for t in tasks if t.status in ("running", "review")]
            if not active and (decided is not None or not ready_tasks(rm)):
                result["left"] = len([t for t in tasks if t.status == "todo"])
                if decided is None:
                    waiting = len([t for t in tasks if t.status == "todo"]) - len(ready_tasks(rm))
                    decide("roadmap", "Nothing ready is left: %d done, %d blocked, %d waiting on a blocked task." % (
                        len([t for t in tasks if t.status == "done"]), len([t for t in tasks if t.status == "blocked"]),
                        max(0, waiting)))
                stop = decided
                return result
            sleep(poll)
    except (AutopilotError, RoadmapError) as e:
        stop = ("error", str(e))
        raise
    except BaseException as e:  # a crash or a Ctrl-C: the tasks in flight keep their worktrees for a resume
        stop = ("interrupted", "The loop stopped (%s). Resume it with --resume to carry on." % type(e).__name__)
        raise
    finally:
        if st is not None:
            if stop is None:
                stop = ("interrupted", "The loop stopped without a reason.")
            try:
                rm = load_roadmap(repo)
                keys = [t.key for t in roadmap_tasks(rm) if t.status != "running"] if rm else []
            except (RoadmapError, OSError):
                keys = []
            st["kept"] = _ap_clean(repo, keys)
            if stop[0] == "signal":
                try:
                    os.remove(stop_path)  # honoured: the request is used up
                except FileNotFoundError:
                    pass
            st["stop"] = {"reason": stop[0], "detail": stop[1], "at": int(time.time())}
            _ap_save(state_path, st)
            try:
                write_ap_report(repo, st)
            except Exception as e:  # a report problem must never hide the stop it reports
                log("could not write the run report: %s" % e)
        lock.close()


def _ap_launch(repo, t, base, launcher, fresh, log=lambda m: None):
    """Prepare a task's run and start it through launcher. Raises AutopilotError when it cannot be prepared or started."""
    _ap_prepare(repo, t, base, fresh, log)
    got = launcher(_ap_chunk(t.key))
    if got not in ("launched", "already launched"):
        raise AutopilotError("Could not start its run (%s)." % got)


def autopilot_stop(repo):
    """Ask the running loop for this repo to stop, by writing its stop file. The loop honours it between tasks: it starts
    nothing more and ends once the tasks in flight finish. Returns False when no loop is running, so nothing is written."""
    _, stop_path = _ap_paths(repo)
    probe = _lock(os.path.join(BASE, "autopilot-%s.lock" % _slug(repo, "autopilot")), 0)
    if probe is not None:
        probe.close()
        return False
    with open(stop_path, "w") as f:
        f.write("stop\n")
    return True


def autopilot_cli(argv):
    p = argparse.ArgumentParser(prog="runway.py autopilot", formatter_class=argparse.RawDescriptionHelpFormatter,
                                description="Work the project's roadmap: run each ready task in its own worktree, gate it "
                                            "with the checks and merge it, marking each task done or blocked. Off unless run.",
                                epilog="examples:\n  runway.py autopilot --repo ~/Projects/site\n"
                                       "  runway.py autopilot --repo ~/Projects/site --parallel 2 --cap 5 --max-failures 3\n"
                                       "  runway.py autopilot --repo ~/Projects/site --resume\n"
                                       "  runway.py autopilot --repo ~/Projects/site --stop\n\n"
                                       "exit codes: 0 the queue is finished, or a cap, a stop request or only blocked tasks "
                                       "ended the run; 1 the loop could not go on (failures, error, interrupted); 2 usage error")
    p.add_argument("--repo", metavar="PATH", help="a checkout of the project (default: the current directory)")
    p.add_argument("--parallel", type=int, default=AUTOPILOT_PARALLEL, metavar="N",
                   help="chunks in flight at once (default %d)" % AUTOPILOT_PARALLEL)
    p.add_argument("--cap", type=float, metavar="USD", help="do not start a task whose projected cost overruns this many dollars (default: no cap)")
    p.add_argument("--max-failures", type=int, metavar="N", help="stop after N tasks in a row are blocked (default: no limit)")
    p.add_argument("--resume", action="store_true",
                   help="carry on the last run of this roadmap: same spend ledger, same cap and limit unless given, failure count reset")
    p.add_argument("--stop", action="store_true", help="ask the running loop to stop between tasks, then exit")
    a = p.parse_args(argv[1:])
    try:
        repo = _repo_root(a.repo)
        if a.stop:
            if autopilot_stop(repo):
                print("@@AUTOPILOT stop requested: the loop starts nothing more and ends when its tasks finish", flush=True)
            else:
                print("@@AUTOPILOT no autopilot is running on this roadmap, so there is nothing to stop", flush=True)
            return 0
        r = autopilot(repo, a.parallel, log=lambda m: print(m, flush=True), cap=a.cap, max_failures=a.max_failures, resume=a.resume)
        rec = _ap_read(_ap_paths(repo)[0]) or {}
    except TrainUsage as e:
        print("@@ERR " + str(e))
        return 2
    except (AutopilotError, RoadmapError) as e:
        print("@@ERR " + str(e))
        return 1
    print("@@AUTOPILOT done=%d blocked=%d left=%d" % (len(r["done"]), len(r["blocked"]), r["left"]))
    print("@@STOP %s: %s" % (rec["stop"]["reason"], rec["stop"]["detail"]))
    for k, why in sorted(rec.get("kept", {}).items()):
        print("@@KEPT worktree runway-%s kept: %s" % (k, why))
    return 1 if rec["stop"]["reason"] in AP_STOP_FAILED else 0


# ---------- autopilot run report: written on every stop, from the same ledgers the page and the merge train read ----------
# BASE/autopilot/<run>/ holds report.md (for people) and report.json (for the Runs page and later reading). Each stop
# rewrites both, so a resumed run's report covers the whole run. Dollars and effective tokens come from the run's budget
# ledger; git and merge minutes come from the merge train's buckets (ledger_totals over each task's timing record).
AP_REPORT_LIST = 10  # the Runs page lists this many of the latest reports
AP_STOP_TEXT = {"cap": "The dollar cap stopped the run", "failures": "The run halted after repeated blocks",
                "error": "The run stopped on an error", "interrupted": "The run was interrupted"}
AP_STOP_LABEL = {"roadmap": "queue finished", "cap": "dollar cap reached", "signal": "stop requested",
                 "failures": "halted after repeated blocks", "error": "stopped on an error", "interrupted": "interrupted"}


def ap_report_paths(run):
    d = os.path.join(BASE, "autopilot", run)
    return os.path.join(d, "report.md"), os.path.join(d, "report.json")


def _ap_note_since(key, started):
    """The autopilot's note on a task, if this run wrote it. An older note is a previous run's, so it counts for nothing."""
    try:
        note = _ap_read(os.path.join(_ap_dir(key), key + ".autopilot.json")) or {}
    except AutopilotError:
        return {}
    return note if (note.get("at") or 0) >= started else {}


def _ap_timing(key):
    """The task's timing record (setup, tests, merge), or {} when it has none."""
    try:
        return json.loads(rd(_timing_path(_ap_chunk(key), key)) or "{}")
    except (AutopilotError, ValueError):
        return {}


AP_GATE_KEPT_PHRASE = "The worktree is kept."
AP_GATE_REMOVED_PHRASE = "Its worktree was removed; run the task again to recreate it from its branch and retry the checks."


def _ap_blocked_reason(key, reason, kept):
    """Correct a blocked task's reason for autopilot's own cleanup. _gate's message (shared with merge-batch, which
    never cleans up) always says the worktree is kept, but autopilot's end-of-run cleanup removes a blocked task's
    worktree unless git refused (then, and only then, the key is in `kept`). A removed worktree is recreated from its
    still-existing branch the next time the task runs (see _plan), so this just tells the operator which is true."""
    if reason and AP_GATE_KEPT_PHRASE in reason and key not in kept:
        return reason.replace(AP_GATE_KEPT_PHRASE, AP_GATE_REMOVED_PHRASE)
    return reason


def ap_report(repo, st, rm, rows, drift=None):
    """The run's report as data. st is the run's state, rm its roadmap (None when it cannot be read), rows its budget
    ledger rows, drift the calibration drift from calibration_drift (None when it was not measured). Only tasks this run
    attempted are listed; the totals are the sums of those rows, so they reconcile."""
    started = st.get("startedAt") or 0
    stop = st.get("stop") or {"reason": "interrupted", "detail": "The loop stopped without a reason."}
    ended = stop.get("at") or int(time.time())
    cap = st.get("cap")
    kept = st.get("kept") or {}
    tasks = roadmap_tasks(rm) if rm else []
    by_tid = {}
    for r in rows:
        by_tid.setdefault(r["tid"], []).append(r)
    out, ledger_rows, attention = [], [], []
    for t in tasks:
        rs = by_tid.get(t.key, [])
        note = _ap_note_since(t.key, started)
        if not (rs or note or t.status in ("running", "review")):
            continue  # not attempted in this run
        outcome = {"done": "merged", "blocked": "blocked", "review": "review"}.get(t.status, "running")
        eff = round(sum(r["eff"] for r in rs)) if rs else None
        usd = round(sum(r["usd"] for r in rs), 4)
        reason = note.get("msg") if outcome in ("blocked", "review") else None
        if outcome == "blocked":
            reason = _ap_blocked_reason(t.key, reason, kept)
        row = {"key": t.key, "title": t.title, "outcome": outcome, "gate": note.get("outcome") if outcome == "blocked" else None,
               "reason": reason, "model": t.model or "", "kind": t.kind or "",
               "est": t.estTokens or 0, "eff": eff, "ratio": round(eff / t.estTokens, 2) if eff and t.estTokens else None, "usd": usd}
        out.append(row)
        ledger_rows.append(task_row(_ap_chunk(t.key), t.key, note.get("outcome") or outcome, _ap_timing(t.key)))
        if outcome == "blocked":
            attention.append({"task": t.key, "text": "Blocked: %s" % (row["reason"] or "no reason recorded")})
        elif outcome == "review":
            attention.append({"task": t.key, "text": "In review and not merged: run the autopilot again to merge it."})
        elif outcome == "running":
            attention.append({"task": t.key, "text": "Still running when the loop stopped: resume the run to carry it on."})
        elif not rs:
            attention.append({"task": t.key, "text": "Merged, but no usage was recorded, so its cost is missing from the totals."})
    spend = round(sum(r["usd"] for r in rows), 4)
    eff_all = round(sum(r["eff"] for r in rows))
    task_keys = {t.key for t in tasks}
    task_usd = round(sum(r["usd"] for r in rows if r["tid"] in task_keys), 4)
    unattributed = round(spend - task_usd, 4)
    left = [t.key for t in tasks if t.status == "todo"]
    reason, detail = stop["reason"], stop["detail"]
    if reason in AP_STOP_TEXT:
        attention.insert(0, {"task": None, "text": "%s: %s" % (AP_STOP_TEXT[reason], detail)})
    if left and reason != "roadmap":
        attention.append({"task": None, "text": "Not started (%d): %s" % (len(left), ", ".join(left[:10]) + (" ..." if len(left) > 10 else ""))})
    for k, why in sorted((st.get("kept") or {}).items()):
        attention.append({"task": k, "text": "Worktree runway-%s was kept: %s" % (k, why)})
    if unattributed:
        attention.append({"task": None, "text": "$%.4f in the budget ledger belongs to no task in the roadmap." % unattributed})
    if cap is not None and spend > cap:
        attention.append({"task": None, "text": "Spend $%.4f went over the $%.2f cap." % (spend, cap)})
    for d in (drift or []):
        if d["flagged"]:
            attention.append({"task": None, "text": "Calibration drift: %s · %s moved %+.0f%% since the roadmap was generated (×%.2f to ×%.2f, %d runs)."
                              % (d["model"], d["kind"], 100 * d["moved"], d["generated"], d["current"], d["n"])})
    counts ={k: sum(1 for r in out if r["outcome"] == k) for k in ("merged", "blocked", "review", "running")}
    counts.update({"attempted": len(out), "left": len(left)})
    totals = ledger_totals(ledger_rows)
    totals.update({"est": sum(r["est"] for r in out), "eff": sum(r["eff"] or 0 for r in out), "usd": round(sum(r["usd"] for r in out), 4)})
    return {"v": 1, "run": st["run"], "repo": repo, "project": (rm.title or rm.project) if rm else "",
            "startedAt": started, "endedAt": ended, "wallMin": round(max(0, ended - started) / 60, 2),
            "resumed": bool(st.get("resumedAt")), "stop": {"reason": reason, "detail": detail},
            "cap": cap, "spend": spend, "remaining": round(cap - spend, 4) if cap is not None else None,
            "capPct": round(100 * spend / cap, 1) if cap else None, "counts": counts, "tasks": out, "totals": totals,
            "reconcile": {"ledgerRows": len(rows), "spend": spend, "eff": eff_all, "taskSpend": task_usd,
                          "unattributed": unattributed, "ok": unattributed == 0}, "attention": attention, "drift": drift}


def _md_cell(s):
    return str(s if s is not None else "–").replace("|", "\\|").replace("\n", " ")


def ap_report_md(rep):
    """The report as markdown. Pure: the same report data always renders the same text (the golden test relies on it)."""
    def money(x):  # costs to the tenth of a cent, so the task rows add up to the total as shown
        return "–" if x is None else "$%.3f" % x

    def dollars(x):  # the cap is a whole-dollar figure
        return "–" if x is None else "$%.2f" % x

    def toks(x):
        return "–" if x is None else "{:,}".format(int(round(x)))

    def when(x):
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(x)) if x else "–"

    c, tot, rc = rep["counts"], rep["totals"], rep["reconcile"]
    L = ["# Autopilot run report: %s" % (rep["project"] or rep["run"]), "",
         "Run `%s` in `%s`. Stopped: **%s**. %s" % (rep["run"], rep["repo"], AP_STOP_LABEL.get(rep["stop"]["reason"], rep["stop"]["reason"]),
                                                  rep["stop"]["detail"]), "",
         "Started %s, ended %s: %.1f min wall-clock%s." % (when(rep["startedAt"]), when(rep["endedAt"]), rep["wallMin"],
                                                       ", resumed" if rep["resumed"] else ""), "",
         "## Summary", "",
         "| Attempted | Merged | Blocked | In review | Left to do |", "| ---: | ---: | ---: | ---: | ---: |",
         "| %d | %d | %d | %d | %d |" % (c["attempted"], c["merged"], c["blocked"], c["review"], c["left"]), ""]
    if rep["cap"] is not None:
        L.append("Spend %s of the %s cap (%.1f%%), %s left." % (money(rep["spend"]), dollars(rep["cap"]), rep["capPct"], money(rep["remaining"])))
    else:
        L.append("No cap was set. Spend %s." % money(rep["spend"]))
    L += ["", "## Tasks", "",
          "| Task | Outcome | Est. tokens | Actual tokens | Actual ÷ est. | Model | Cost |", "| --- | --- | ---: | ---: | ---: | --- | ---: |"]
    for r in rep["tasks"]:
        L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            _md_cell("%s: %s" % (r["key"], r["title"])), _md_cell(r["outcome"] + (" (%s)" % r["gate"] if r["gate"] else "")),
            toks(r["est"]), toks(r["eff"]), "–" if r["ratio"] is None else "×%.2f" % r["ratio"], _md_cell(r["model"]), money(r["usd"])))
    L.append("| **Total** | | %s | %s | | | %s |" % (toks(tot["est"]), toks(tot["eff"]), money(tot["usd"])))
    L += ["", "## Time (merge train buckets)", "",
          "| Machine min | Git and merge min | Setup min | Test min | Merge min | Retries | Conflicts resolved | Human touches |",
          "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
          "| %.2f | %.2f | %.2f | %.2f | %.2f | %d | %d | %d |" % (
              tot["machine_min"], tot["git_merge_min"], tot["setup_min"], tot["test_min"], tot["merge_min"], tot["retries"],
              tot["conflicts_resolved"], tot["human_touches"]), "",
          "Review and client minutes are not automated, so they are not counted here.", ""]
    if rep.get("drift") is not None:  # measured only when the roadmap's generation-time multipliers were recorded
        L += ["## Calibration drift", ""]
        if rep["drift"]:
            L += ["| Model | Kind | At generation | Now | Runs | Moved | Flag |", "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
            L += ["| %s | %s | ×%.2f | ×%.2f | %d | %+.0f%% | %s |" % (_md_cell(d["model"]), _md_cell(d["kind"]), d["generated"], d["current"],
                                                                 d["n"], 100 * d["moved"], "**drift**" if d["flagged"] else "ok")
                  for d in rep["drift"]]
        else:
            L.append("No multiplier has been measured yet.")
        L.append("")
    L += ["## Needs a person", ""]
    L += ["- %s%s" % ("**%s:** " % a["task"] if a["task"] else "", a["text"]) for a in rep["attention"]] or ["Nothing needs a person."]
    L += ["", "## Reconciliation", "",
          "Budget ledger: %d rows, %s, %s effective tokens. The tasks account for %s; unattributed %s. %s" % (
              rc["ledgerRows"], money(rc["spend"]), toks(rc["eff"]), money(rc["taskSpend"]), money(rc["unattributed"]),
              "Reconciled." if rc["ok"] else "**Does not reconcile.**"), ""]
    return "\n".join(L)


def write_ap_report(repo, st):
    """Write the run's report.md and report.json (both rewritten on each stop). Returns the markdown path."""
    try:
        rm = load_roadmap(repo)
    except (RoadmapError, OSError, ValueError):  # the report still goes out, with no task list, when the roadmap is unreadable
        rm = None
    gen = calibration_snapshot(repo, rm.provenance.generatedAt) if rm and rm.provenance.generatedAt else None
    drift = calibration_drift(gen, calibration_report()["table"])
    rep = ap_report(repo, st, rm, budget_rows(st["run"]), drift)
    md, js = ap_report_paths(st["run"])
    os.makedirs(os.path.dirname(md), exist_ok=True)
    write_json_atomic(js, rep)
    with open(md + ".tmp", "w") as f:
        f.write(ap_report_md(rep))
    os.replace(md + ".tmp", md)
    return md


def ap_reports_status(limit=AP_REPORT_LIST):
    """The latest run reports, newest first: where each one is and how its run ended. Read by tick() for the Runs page."""
    out = []
    for p in glob.glob(os.path.join(BASE, "autopilot", "*", "report.json")):
        rep = _ap_read(p)
        if not isinstance(rep, dict):
            continue
        out.append({"run": rep.get("run"), "md": os.path.join(os.path.dirname(p), "report.md"), "repo": rep.get("repo"),
                    "project": rep.get("project"), "endedAt": rep.get("endedAt"), "stop": (rep.get("stop") or {}).get("reason"),
                    "counts": rep.get("counts"), "spend": rep.get("spend"), "cap": rep.get("cap")})
    return sorted(out, key=lambda r: r.get("endedAt") or 0, reverse=True)[:limit]


# ---------- roadmap CLI: generate a candidate from a brief, show or edit a roadmap, approve the candidate ----------
# The accepted roadmap is roadmap_path(repo); a generated one waits as the candidate until approve moves it in. Approve
# archives the roadmap it replaces under roadmaps/history, so each project has exactly one active roadmap. show and edit
# take --candidate to work on the candidate instead. Opt-in: only the `roadmap` command touches these files.
ROADMAP_EXIT = """exit codes:
  0  done, or the roadmap is valid (show, edit with no changes)
  1  generation failed, the edit did not validate, the editor failed, or approve was refused (an autopilot is running)
  2  usage error: bad arguments, an unreadable brief, or no roadmap or candidate to act on"""


def _roadmap_parser():
    fmt = argparse.RawDescriptionHelpFormatter
    p = argparse.ArgumentParser(prog="runway.py roadmap", formatter_class=fmt, epilog=ROADMAP_EXIT,
                                description="A project's roadmap: generate a candidate from a brief, show it as a tree with "
                                            "token totals, edit it by hand, and approve it as the project's active roadmap.")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND", required=True)

    def command(name, help, examples):
        return sub.add_parser(name, help=help, description=help, formatter_class=fmt,
                              epilog="examples:\n" + "\n".join("  " + e for e in examples) + "\n\n" + ROADMAP_EXIT)

    g = command("generate", "plan a project from a brief with Claude, and save the result as a candidate for review",
                ["runway.py roadmap generate --brief brief.json          # the brief's repo is the project",
                 "runway.py roadmap generate --goal 'A site for a painter' --repo ~/Projects/site"])
    g.add_argument("--brief", metavar="FILE", help="a JSON brief: goal, repo, constraints, planned (flags below override it)")
    g.add_argument("--goal", metavar="TEXT", help="what the project is for")
    g.add_argument("--repo", metavar="PATH", help="a checkout of the project (default: the brief's repo, else the current directory)")
    g.add_argument("--constraints", metavar="TEXT", help="limits the plan must keep")
    g.add_argument("--planned", metavar="TEXT", help="work already planned, to build the roadmap around")

    for name, help in (("show", "print the roadmap as a tree, with token totals per sprint and warnings over the caps"),
                       ("edit", "open the roadmap JSON in $EDITOR; it is saved only if it still validates")):
        c = command(name, help, ["runway.py roadmap %s                    # the accepted roadmap" % name,
                                 "runway.py roadmap %s --candidate        # the generated one, before approval" % name])
        c.add_argument("--repo", metavar="PATH", help="a checkout of the project (default: the current directory)")
        c.add_argument("--candidate", action="store_true", help="work on the generated candidate, not the accepted roadmap")

    ap = command("approve", "make the candidate the project's one active roadmap; the roadmap it replaces is archived",
                 ["runway.py roadmap approve                       # the current checkout",
                  "runway.py roadmap approve --repo ~/Projects/site"])
    ap.add_argument("--repo", metavar="PATH", help="a checkout of the project (default: the current directory)")
    return p


def _fmt_tokens(n):
    return "{:,}".format(int(n))


def roadmap_tree(rm, label):
    """The roadmap as text: PIs, their sprints with token totals, and their tasks. A sprint over SPRINT_TOKEN_CAP, or a task
    over TASK_TOKEN_CAP, is flagged. Returns (lines, the keys of the sprints over the cap)."""
    lines = ["%s  (%s)" % (rm.title or "(untitled roadmap)", label)]
    over = []
    if not rm.pis:
        lines.append("  (no PIs yet)")
    for pi in rm.pis:
        lines += ["", "%s  %s" % (pi.key, pi.title)]
        if not pi.sprints:
            lines.append("  (no sprints yet)")
        for sp in pi.sprints:
            total = sum(t.estTokens for t in sp.tasks)
            flag = ""
            if total > SPRINT_TOKEN_CAP:
                over.append(sp.key)
                flag = "  !! over the %s cap by %s" % (_fmt_tokens(SPRINT_TOKEN_CAP), _fmt_tokens(total - SPRINT_TOKEN_CAP))
            lines.append("  %s  %s  %s tokens in %d task%s%s" % (sp.key, sp.title, _fmt_tokens(total), len(sp.tasks),
                                                             "" if len(sp.tasks) == 1 else "s", flag))
            for t in sp.tasks:
                tflag = "  !! over the %s task cap" % _fmt_tokens(TASK_TOKEN_CAP) if t.estTokens > TASK_TOKEN_CAP else ""
                lines.append("    %-8s %-8s %12s  %s%s" % (t.key, t.status, _fmt_tokens(t.estTokens), t.title, tflag))
    grand = sum(t.estTokens for t in roadmap_tasks(rm))
    lines += ["", "Total: %s tokens" % _fmt_tokens(grand)]
    if over:
        lines.append("warning: %d sprint%s over the %s cap: %s" % (len(over), "" if len(over) == 1 else "s",
                                                                   _fmt_tokens(SPRINT_TOKEN_CAP), ", ".join(over)))
    return lines, over


def _roadmap_target(repo, candidate):
    """The file a command works on: the accepted roadmap, or the candidate with --candidate."""
    return candidate_paths(repo)[0] if candidate else roadmap_path(repo)


def _cmd_roadmap_generate(a):
    brief = {}
    if a.brief:
        text = rd(a.brief)
        if text is None:
            raise TrainUsage("no brief file at %s" % a.brief)
        try:
            brief = json.loads(text)
        except ValueError as e:
            raise TrainUsage("brief file %s is not valid JSON (line %d: %s)" % (a.brief, e.lineno, e.msg))
        if not isinstance(brief, dict):
            raise TrainUsage("brief file %s must hold an object, not %s" % (a.brief, _jtype(brief)))
    for k, v in (("goal", a.goal), ("constraints", a.constraints), ("planned", a.planned)):
        if v is not None:
            brief[k] = v
    src = a.repo or brief.get("repo")
    if src is not None and type(src) is not str:
        raise TrainUsage("brief.repo must be a folder path, not %s" % _jtype(src))
    brief["repo"] = _repo_root(src)
    print("Generating a roadmap for %s with %s; this can take a few minutes." % (brief["repo"], ROADMAP_MODEL),
          file=sys.stderr, flush=True)
    try:
        rm = generate_roadmap(brief)
    except RoadmapError as e:
        raise TrainUsage(str(e))
    except GenerationError as e:
        print("@@ERR " + str(e), file=sys.stderr)
        return 1
    cand = candidate_paths(brief["repo"])[0]
    print("@@ROADMAP candidate " + cand)
    n_sprints = sum(len(pi.sprints) for pi in rm.pis)
    n_tasks = len(roadmap_tasks(rm))
    print("%d PI%s, %d sprint%s, %d task%s, %s estimated tokens" % (
        len(rm.pis), "" if len(rm.pis) == 1 else "s", n_sprints, "" if n_sprints == 1 else "s",
        n_tasks, "" if n_tasks == 1 else "s", _fmt_tokens(sum(t.estTokens for t in roadmap_tasks(rm)))))
    print("Review it: runway.py roadmap show --candidate; then runway.py roadmap approve")
    return 0


def _cmd_roadmap_show(repo, candidate):
    p = _roadmap_target(repo, candidate)
    rm = load_roadmap(repo, p)
    if rm is None:
        raise TrainUsage("no %s for %s; %s" % ("candidate roadmap" if candidate else "accepted roadmap", repo,
                                              "run: runway.py roadmap generate" if candidate else
                                              "generate one and approve it, or use --candidate"))
    lines, _ = roadmap_tree(rm, "candidate, not approved" if candidate else "active")
    print("\n".join(lines))
    return 0


def _cmd_roadmap_edit(repo, candidate):
    p = _roadmap_target(repo, candidate)
    text = rd(p)
    if text is None:
        raise TrainUsage("no %s for %s to edit" % ("candidate roadmap" if candidate else "accepted roadmap", repo))
    fd, tmp = tempfile.mkstemp(prefix="runway-roadmap-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    editor = os.environ.get("EDITOR") or "vi"
    try:
        rc = subprocess.call(shlex.split(editor) + [tmp])
    except OSError as e:
        print("@@ERR cannot run the editor %r: %s; nothing saved" % (editor, e), file=sys.stderr)
        os.remove(tmp)
        return 1
    if rc:
        print("@@ERR the editor exited %d; nothing saved" % rc, file=sys.stderr)
        os.remove(tmp)
        return 1
    new = rd(tmp) or ""
    if new == text:
        os.remove(tmp)
        print("No changes; nothing saved.")
        return 0
    try:
        rm = parse_roadmap_text(new, p)
    except RoadmapError as e:
        print("@@ERR not saved, the roadmap does not validate: %s" % e, file=sys.stderr)
        print("Your edit is kept at %s; fix it and run edit again." % tmp, file=sys.stderr)
        return 1
    _write_json(p, asdict(rm))
    os.remove(tmp)
    print("@@ROADMAP saved " + p)
    return 0


def _cmd_roadmap_approve(repo):
    cand = candidate_paths(repo)[0]
    text = rd(cand)
    if text is None:
        raise TrainUsage("no candidate roadmap for %s; run: runway.py roadmap generate" % repo)
    try:
        parse_roadmap_text(text, cand)
    except RoadmapError as e:
        print("@@ERR not approved, the candidate does not validate: %s" % e, file=sys.stderr)
        return 1
    lock = _lock(os.path.join(BASE, "autopilot-%s.lock" % _slug(repo, "autopilot")), 0)
    if lock is None:
        print("@@ERR not approved: an autopilot is working this roadmap. Approve once it has stopped.", file=sys.stderr)
        return 1
    try:
        acc, prev = roadmap_path(repo), None
        if os.path.exists(acc):
            hist = os.path.join(BASE, "roadmaps", "history")
            os.makedirs(hist, exist_ok=True)
            prev = os.path.join(hist, "%s-%d.json" % (_slug(repo, "roadmap"), int(time.time() * 1000)))
            shutil.copyfile(acc, prev)
        os.replace(cand, acc)  # one step: the candidate it validated is the roadmap now
    finally:
        lock.close()
    print("@@ROADMAP approved " + acc)
    if prev:
        print("The roadmap it replaces is kept at " + prev)
    return 0


def roadmap_cli(argv):
    """Parse and run one roadmap command. Returns the exit code; argparse exits itself for -h and bad arguments."""
    a = _roadmap_parser().parse_args(argv[1:])
    try:
        if a.cmd == "generate":
            return _cmd_roadmap_generate(a)
        repo = _repo_root(a.repo)
        if a.cmd == "show":
            return _cmd_roadmap_show(repo, a.candidate)
        if a.cmd == "edit":
            return _cmd_roadmap_edit(repo, a.candidate)
        return _cmd_roadmap_approve(repo)
    except TrainUsage as e:
        print("@@ERR " + str(e), file=sys.stderr)
        return 2
    except RoadmapError as e:
        print("@@ERR " + str(e), file=sys.stderr)
        return 1


# ---------- checks: a chunk task's test/build commands, run in its worktree ----------
FAILURES =os.path.join(BASE, "failures.log")  # one JSON line per red or timed-out check, for every chunk
CHECK_TIMEOUT = 600  # seconds per command, unless .runway.json says otherwise


def checks_source(repo, target, wtp):
    """The project's .runway.json text and the path it was read from. The merge target's copy comes first, so a
    worktree cut before the file existed still gets the checks; the task's worktree is the fallback. Returns (raw, read):
    raw is None when neither has one, and read then names both paths that were looked at."""
    tried = []
    if target:
        tried.append("%s:%s in %s" % (target, CONFIG_FILE, repo))
        rc, out = git(repo, "show", target + ":" + CONFIG_FILE)
        if rc == 0:
            return out, tried[-1]
    path = os.path.join(wtp, CONFIG_FILE)
    tried.append(path)
    raw = rd(path)
    return raw, path if raw is not None else " or ".join(tried)


def parse_checks(raw):
    """Checks from the text of a .runway.json (None when there is no file). Same return as load_checks."""
    if raw is None:
        return None, None
    try:
        cfg = json.loads(raw)
    except ValueError as e:
        return None, "Bad .runway.json: " + str(e)[:200]
    items = cfg.get("checks") if isinstance(cfg, dict) else None
    if not items:
        return None, None
    if not isinstance(items, list) or not all(isinstance(c, dict) and isinstance(c.get("cmd"), str) and c["cmd"].strip() for c in items):
        return None, 'Bad .runway.json: "checks" must be a list of {"name", "cmd"} objects'
    default = cfg.get("timeout", CHECK_TIMEOUT)
    return [{"name": c.get("name") or c["cmd"], "cmd": c["cmd"], "timeout": c.get("timeout", default)} for c in items], None


def _check_worktree(repo, wt, tid, base):
    """The task's worktree, created on its runway-task/<tid> branch if it is not on disk. Returns (path, error)."""
    wtp = os.path.join(repo, ".claude", "worktrees", wt)
    if os.path.isdir(wtp):
        return wtp, None
    branch = "runway-task/" + tid
    rc, _ = git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + branch)
    args = ["worktree", "add", "-q", wtp, branch] if rc == 0 else ["worktree", "add", "-q", "-b", branch, wtp, base or "HEAD"]
    rc, out = git(repo, *args)
    return (wtp, None) if rc == 0 else (None, "Could not create the worktree: " + out[-300:])


def _run_check(cmd, cwd, timeout, log, env=None):
    """Run one shell command in its own process group, appending its output to the log as it is written.
    env adds variables for this command only (a task's environment). Returns (rc, timed_out)."""
    with open(log, "a") as out:
        out.write("$ " + cmd + "\n")
        out.flush()
        p = subprocess.Popen(["/bin/bash", "-c", cmd], cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=dict(os.environ, PYTHONUNBUFFERED="1", **(env or {})),
                             start_new_session=True)
        try:
            return p.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)  # the command's children go too
            p.wait()
            out.write("[timed out after %ss, stopped]\n" % timeout)
            return None, True


def _checks_state(d, tid, **state):
    """Write <task>.checks.json, the state the Runs view reads."""
    tmp = os.path.join(d, tid + ".checks.json.tmp")
    with open(tmp, "w") as f:
        json.dump(dict(state, at=int(time.time())), f)
    os.replace(tmp, os.path.join(d, tid + ".checks.json"))


def _checks_finish(ch, tid, wtp, state, check=None, rc=None, reason=None):
    """Record the verdict. Red and timed-out runs also go to the failure log; the worktree is never removed."""
    d = os.path.join(RUNS, ch)
    _checks_state(d, tid, state=state, step=(check or {}).get("name"), rc=rc, reason=reason, worktree=wtp)
    if state in ("red", "timeout"):
        rec = {"at": int(time.time()), "chunk": ch, "task": tid, "state": state, "check": (check or {}).get("name"),
               "cmd": (check or {}).get("cmd"), "rc": rc, "reason": reason, "worktree": wtp,
               "log": os.path.join(d, tid + ".checks.log")}
        os.makedirs(BASE, exist_ok=True)
        with open(FAILURES, "a") as f:
            f.write(json.dumps(rec) + "\n")
        # merge-log reads merge-train.log, so the same failure goes there too
        log_failure(ch, "runway-task/" + tid, "test", rc, (reason or "") + "\n" + (rd(rec["log"]) or "")[-MERGE_LOG_TAIL:])
    return {"verdict": state, "ok": state == "green", "check": (check or {}).get("name"), "rc": rc, "reason": reason}


def run_checks(ch, tid, target=None):
    """Run a chunk task's checks in its worktree, streaming output to <task>.checks.log for the Runs view.
    The checks are read from the merge target (target, else the task's base branch), see checks_source.
    Green: every check exits 0. Red: a check exits non-zero, or times out (state "timeout"). A project with no
    .runway.json gets "none", which is neither green nor red. Returns the verdict dict.
    Setup (making sure the worktree exists) and test (all checks) are timed into the task's timing record."""
    d = os.path.join(RUNS, ch)
    log = os.path.join(d, tid + ".checks.log")
    meta = json.loads(rd(os.path.join(d, tid + ".meta")) or "{}")
    repo, wt, base = meta.get("repo"), meta.get("wt"), meta.get("base")
    if not repo or not wt:
        _checks_state(d, tid, state="none", reason="This task ran in the repo checkout, not a worktree.")
        return {"verdict": "none", "ok": False, "reason": "not a worktree"}
    # A retry is a re-run after a red or timed-out result. Read before the new run overwrites the verdict.
    prev = json.loads(rd(os.path.join(d, tid + ".checks.json")) or "{}").get("state")
    t_setup = time.perf_counter()
    wtp, err = _check_worktree(repo, wt, tid, base)
    timing_add(ch, tid, setup_ms=_ms(t_setup))
    if err:
        return _checks_finish(ch, tid, None, "red", reason=err)
    raw, read = checks_source(repo, target or base, wtp)
    checks, err = parse_checks(raw)
    if err:
        return _checks_finish(ch, tid, wtp, "red", reason="%s (read %s)" % (err, read))
    if checks is None:
        _checks_state(d, tid, state="none", worktree=wtp, reason="No checks in %s." % read)
        return {"verdict": "none", "ok": False, "worktree": wtp, "reason": "No checks in %s." % read}
    open(log, "w").close()  # one run's output per file
    t_test = time.perf_counter()
    env = run_env(ch)  # the task's environment, if its project declares one; the checks get it through their environment
    try:
        for c in checks:
            _checks_state(d, tid, state="running", step=c["name"], worktree=wtp)
            rc, timed_out = _run_check(c["cmd"], wtp, c["timeout"], log, env=env)
            if rc != 0:
                return _checks_finish(ch, tid, wtp, "timeout" if timed_out else "red", check=c, rc=rc,
                                      reason="timed out" if timed_out else "exit %s" % rc)
    finally:
        timing_add(ch, tid, test_ms=_ms(t_test), test_runs=1, retries=1 if prev in ("red", "timeout") else 0)
    return _checks_finish(ch, tid, wtp, "green")


def integrations():
    """Approved Runway work waiting on runway/<base> that has not landed in <base> yet."""
    out = []
    for f in glob.glob(os.path.join(INTEG, "*.json")):
        if f.endswith(".review.json"):
            continue
        try:
            r = json.loads(rd(f) or "{}")
            rc, n = git(r["repo"], "rev-list", "--count", r["base"] + ".." + r["branch"])
            if rc == 0:
                ahead = int(n.strip() or 0)
                _, cur = git(r["repo"], "rev-parse", "--abbrev-ref", "HEAD")
                _, log = git(r["repo"], "log", "--format=%s", r["base"] + ".." + r["branch"])
                co = checkout_for(r["repo"], r["base"])
                h = checkout_health(co, r["base"]) if ahead and co else {}
                out.append({"repo": r["repo"], "base": r["base"], "branch": r["branch"], "ahead": ahead, "current": cur,
                            "review": pending_review(r["repo"], r["base"]) if ahead else None, "landAt": co if co and co != r["repo"] else None,
                            "overlap": h.get("overlap", []), "health": h,
                            "commits": [l.replace("Runway: ", "") for l in log.splitlines() if l.strip()][:12]})
        except Exception:
            continue
    return out


# ---------- vault export ----------
def export(b64):
    req = json.loads(base64.b64decode(b64).decode())
    vault = os.path.expanduser(req["vault"])
    rel, log_rel = req["path"], req["log"]
    for r in (rel, log_rel):
        if ".." in r.split("/") or r.startswith("/"):
            return "@@ERR Bad path " + r
    dest = os.path.join(vault, rel)
    if not os.path.isdir(vault):
        return "@@ERR Vault not found at " + vault
    created = req["today"]
    old = rd(dest)
    if old:
        m = re.search(r"^created:\s*(\S+)", old, re.M)
        if m:
            created = m.group(1)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if req.get("append"):
        # append an entry (e.g. the decision log); create the file from its header the first time, bump updated:
        body = old if old else req.get("header", "").replace("__CREATED__", created)
        body = re.sub(r"^updated:\s*\S+", "updated: " + req["today"], body, count=1, flags=re.M)
        with open(dest, "w") as f:
            f.write(body.rstrip("\n") + "\n\n" + req["markdown"].strip("\n") + "\n")
    else:
        with open(dest, "w") as f:
            f.write(req["markdown"].replace("__CREATED__", created))
    with open(os.path.join(vault, log_rel), "a") as f:
        f.write(req["logLine"].rstrip("\n") + "\n")
    return "@@OK Wrote " + rel


# ---------- Mac notifications ----------
NOTIFY_STATE = os.path.join(BASE, "notify-state.json")
NOTIFY_OFF = os.path.join(BASE, "notify.off")
NOTIFY_ROUTINE = os.path.join(BASE, "notify.routine")  # present: also ping for routine successes (chunk finished, task merged)
NOTIFY_KEEP = 30 * 86400  # how long a sent event is remembered; longer than the 14-day status window
REPEAT_FAILS = 2  # a task's second red or timed-out check run (and each one after it) is a repeat failure


def notify_enabled():
    return not os.path.exists(NOTIFY_OFF)


def _set_flag(path, on):
    """Create or remove a flag file in ~/.ai-planner."""
    os.makedirs(BASE, exist_ok=True)
    if on:
        open(path, "w").close()
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def handsoff_enabled():
    return os.path.exists(os.path.join(BASE, "notify.handsoff"))


def routine_enabled():
    return os.path.exists(NOTIFY_ROUTINE)


def mac_notify(title, body):
    """One macOS notification. Title and body are passed as argv, so quotes in task titles cannot break the script."""
    script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
    try:
        subprocess.run(["osascript", "-e", script, title, body], capture_output=True, timeout=10)
    except OSError:
        pass  # no osascript: the event is still recorded, so it is not retried every minute


def _mtime(path):
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 0


def _name(ch, tid):
    return _title(ch, tid) if ch else tid


def events(out):
    """What Austin is told about, as (key, title, body, routine). The key names one occurrence (a chunk's finish
    time, a task's last run, a merge batch id, a failed check's time), so the same occurrence never produces a second
    notification. Routine events are successes: they are silent unless `notify routine on`. The others need Austin:
    a conflict, a task failing again, a usage-limit pause, and a merge run finishing with its counts."""
    evs = []
    for ch, c in (out.get("chunks") or {}).items():
        tasks = c.get("tasks") or {}
        if c.get("finished"):
            ok = sum(1 for t in tasks.values() if t.get("state") == "done" and not t.get("is_error"))
            evs.append(("chunk:%s:%d" % (ch, _mtime(os.path.join(RUNS, ch, "_chunk.done"))), "Runway chunk finished",
                        "%s: %d of %d tasks done" % (ch, ok, len(tasks)), True))
        for tid, t in tasks.items():
            if t.get("limited"):
                stamp = max(_mtime(os.path.join(RUNS, ch, tid + ext)) for ext in (".json", ".exit"))
                # hands-off: the page relaunches it once the window resets, so this is routine, not a request for Austin
                evs.append(("limit:%s:%s:%d" % (ch, tid, stamp), "Runway task paused at a usage limit",
                            "%s. Resume it after the window resets." % _title(ch, tid), handsoff_enabled()))
    seen = {}
    for rec in read_check_failures():  # oldest first, so the count runs in order
        ch, tid = rec.get("chunk"), rec.get("task")
        seen[(ch, tid)] = seen.get((ch, tid), 0) + 1
        if seen[(ch, tid)] >= REPEAT_FAILS:
            evs.append(("repeat:%s:%s:%d" % (ch, tid, int(rec.get("at") or 0)), "Runway task failed again",
                        "%s failed again (%s)." % (_name(ch, tid), rec.get("reason") or rec.get("state") or "red"), False))
    for mid, m in (out.get("merges") or {}).items():
        results = m.get("results") or {}
        chunk_of = {i.get("tid"): i.get("ch") for i in m.get("items") or []}
        for tid, r in results.items():
            oc, name = r.get("outcome"), _name(chunk_of.get(tid), tid)
            if oc == "conflict":
                evs.append(("conflict:%s:%s" % (mid, tid), "Runway needs you: merge conflict",
                            "%s: %s" % (name, r.get("msg") or "it conflicts with approved work."), False))
            elif oc in ("merged", "resolved"):
                evs.append(("merged:%s:%s" % (mid, tid), "Runway task merged", name + " merged into its base branch.", True))
        if m.get("state") == "done":
            good = sum(1 for r in results.values() if r.get("outcome") in ("merged", "resolved"))
            if handsoff_enabled() and good == len(results):
                continue  # a clean run is an automatic action, logged in the page, not news for Austin
            evs.append(("merge:" + mid, "Runway run complete",
                        "%d merged, %d failed (of %d tasks)." % (good, len(results) - good, len(results)), False))
    return evs


def _notify_state():
    try:
        return json.loads(rd(NOTIFY_STATE) or "{}")
    except ValueError:
        return {}


def notify(out):
    """Post one notification per event not seen before, then remember it. The first run only records what already
    exists, so installing this does not replay old history. While notifications are off, events are recorded but not posted,
    so turning them back on does not replay what happened in between."""
    first = not os.path.exists(NOTIFY_STATE)
    sent = _notify_state().get("sent", {})
    now = int(time.time())
    posted = []
    for key, title, body, routine in events(out):
        if key in sent:
            continue
        sent[key] = now
        if not first and notify_enabled() and (routine_enabled() or not routine):
            mac_notify(title, body)
            posted.append(title + ": " + body)
    sent = {k: v for k, v in sent.items() if v >= now - NOTIFY_KEEP}
    os.makedirs(BASE, exist_ok=True)
    tmp = NOTIFY_STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"sent": sent}, f)
    os.replace(tmp, NOTIFY_STATE)
    return posted


def preview(out):
    """Every event in out, marked sent, quiet (a routine event while `notify routine` is off) or new, without posting
    or recording anything."""
    sent = _notify_state().get("sent", {})
    lines = []
    for key, title, body, routine in events(out):
        state = "sent" if key in sent else ("quiet" if routine and not routine_enabled() else "new ")
        lines.append("%s %s: %s" % (state, title, body))
    return lines


# ---------- the agent's own account of how it ended ----------
OUTCOME_RE = re.compile(r"```runway\s*(\{[\s\S]*?\})\s*```")
BLOCKER_TYPES = ("decision", "permission", "environment", "dependency", "access")


def parse_outcome(result):
    """The last ```runway {...}``` block in an agent's report: {status, blockers[]}, cleaned up, or None."""
    for raw in reversed(OUTCOME_RE.findall(result or "")):
        try:
            d = json.loads(raw)
        except Exception:
            continue
        status = d.get("status") if d.get("status") in ("done", "partial", "blocked") else None
        if not status:
            continue
        out = []
        for b in (d.get("blockers") or [])[:6]:
            if not isinstance(b, dict) or b.get("type") not in BLOCKER_TYPES:
                continue
            out.append({"type": b["type"], "detail": str(b.get("detail") or "")[:600],
                        "options": [str(o)[:200] for o in (b.get("options") or []) if str(o).strip()][:5],
                        "command": str(b.get("command") or "")[:300], "path": str(b.get("path") or "")[:300],
                        "needs": str(b.get("needs") or "")[:200]})
        return {"status": status, "blockers": out}
    return None


# ---------- preflight: what would stop a launch, checked before any tokens are spent ----------
def _sh(cmd, cwd, timeout=25):
    env = dict(os.environ, PATH="/usr/local/bin:/opt/homebrew/bin:" + os.path.expanduser("~/.docker/bin") + ":" + os.environ.get("PATH", ""))
    try:
        r = subprocess.run(["/bin/bash", "-lc", cmd], cwd=cwd if os.path.isdir(cwd or "") else None, capture_output=True,
                           text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=env)
        return r.returncode, (r.stdout + r.stderr)[-600:]
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ds" % timeout


_ARG = r"[\w./@:=+-]+"  # one plain word: no whitespace, quotes, $, ;, |, &, <, >, `, ( ) or glob characters
SAFE_CHECK_RE = re.compile(r"^(docker (info|version|ps)|supabase status|pg_isready( -[hpU] ?%s)*|which %s|command -v %s|(node|python3?|npm|pnpm|yarn|go|cargo|ruby|java) (-v|--version|version)|test -[efdrx] %s|ls %s)$"
                           % (_ARG, _ARG, _ARG, _ARG, _ARG))


_GREP_CHECK = r"grep -[qiEFw]+ (?:'[^'$`\\]*'|\"[^\"$`\\]*\"|%s) %s" % (_ARG, _ARG)
NEEDS_CHECK_RE = re.compile(r"^(?:%s|%s)$" % (SAFE_CHECK_RE.pattern[1:-1], _GREP_CHECK))


def needs_check(b64):
    """Is each needs-you step done? Takes [{id, check, repo}] and answers {id: bool} as @@NEEDSCHECK. The check runs in
    the repo through the same runner as a project's requirement checks, but only if it is a plain read-only check
    (grep -q X file, test -e p, which x, docker info ... joined by &&); anything else is never run and reads false."""
    res = {}
    for b in json.loads(base64.b64decode(b64).decode()):
        c = " ".join(str(b.get("check") or "").split())
        parts = c.split(" && ")
        ok = bool(c) and len(parts) <= 4 and all(NEEDS_CHECK_RE.match(x) for x in parts) and _sh(c, os.path.expanduser(b.get("repo") or ""), 20)[0] == 0
        res[b.get("id")] = ok
    return "@@NEEDSCHECK " + json.dumps(res)


def blocker_check(b64):
    """Has each blocker cleared? Takes [{id, path, command, branch, since, repo}] (since in ms) and answers {id: bool}
    as @@BLOCKERS. A path: it exists and is readable. A command: it now succeeds, but only a plain read-only check
    (docker info, which x, test -e p ...) is ever run; a command an agent wrote is never run unless it looks like one.
    A branch: it has a commit newer than `since`."""
    res = {}
    for b in json.loads(base64.b64decode(b64).decode()):
        ok, repo = False, os.path.expanduser(b.get("repo") or "")
        if b.get("path"):
            pth = os.path.expanduser(b["path"])
            ok = os.path.exists(pth) and os.access(pth, os.R_OK)
        elif b.get("command"):
            c = " ".join(b["command"].split())
            ok = bool(SAFE_CHECK_RE.match(c)) and _sh(c, repo, 20)[0] == 0
        elif b.get("branch") and re.fullmatch(r"[\w./-]+", b["branch"]) and not b["branch"].startswith("-"):
            rc, o = git(repo, "log", "-1", "--format=%ct", b["branch"], "--")
            ok = rc == 0 and o.strip().isdigit() and int(o.strip()) * 1000 > int(b.get("since") or 0)
        res[b.get("id")] = ok
    return "@@BLOCKERS " + json.dumps(res)


def preflight(b64):
    """For each project about to launch: does the repo exist, is the checkout mid-merge, does the start branch
    exist, and do the project's own requirement checks (e.g. docker info) pass? Before any of that: can agents sign
    in at all (reported once, on the first project)."""
    out = []
    a = auth_check()
    auth_prob = [] if a["ok"] else [{"kind": "auth", "detail": "Agents can't sign in (%s): %s. Run `claude setup-token`, then store the token: "
                                     "security add-generic-password -U -a \"$USER\" -s %s -w" % (a["via"], a["detail"], KEYCHAIN_TOKEN)}]
    for p in json.loads(base64.b64decode(b64).decode()):
        repo, probs = os.path.expanduser(p.get("repo") or ""), []
        if not os.path.isdir(os.path.join(repo, ".git")) and not os.path.isfile(os.path.join(repo, ".git")):
            out.append({"project": p.get("name"), "problems": [{"kind": "repo", "detail": "No git repo at " + repo}]})
            continue
        _, cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        h = checkout_health(repo, cur)
        if h["conflicted"] or h["merging"]:
            probs.append({"kind": "checkout", "detail": "Your checkout is mid-merge (%s). Agents read it and lands are refused until it's finished." % (", ".join(h["conflicted"][:4]) or "merge in progress")})
        start = (p.get("start") or "").strip()
        if start and git(repo, "rev-parse", "-q", "--verify", start + "^{commit}")[0]:
            probs.append({"kind": "branch", "detail": "Start branch %s doesn't exist." % start})
        for r in p.get("requires") or []:
            rc, o = _sh(r.get("check") or "true", repo)
            if rc:
                probs.append({"kind": "env", "check": r.get("check"), "fix": r.get("fix") or "", "detail": "`%s` failed: %s" % (r.get("check"), o.strip().splitlines()[-1] if o.strip() else "exit %d" % rc)})
        out.append({"project": p.get("name"), "problems": auth_prob + probs})
        auth_prob = []
    return "@@PREFLIGHT " + json.dumps(out)


def run_fix(b64):
    """Run a project's own fix command for a failed requirement (e.g. open -a Docker), then report its output."""
    req = json.loads(base64.b64decode(b64).decode())
    rc, o = _sh(req["fix"], os.path.expanduser(req.get("repo") or "~"), timeout=90)
    return ("@@OK " if rc == 0 else "@@ERR ") + (o.strip()[-300:] or "done")


# ---------- review check: input, verdict, fix prompt (called from run.sh) ----------
def _verdict_of(text):
    """The checker's {"pass", "notes"} from its JSON output; pass None when it can't be read (the check didn't run)."""
    try:
        v = json.loads(text)
    except Exception:
        return {"pass": None, "notes": "The check's output couldn't be read."}
    txt = v.get("result") or ""
    m = re.search(r"\{[\s\S]*\}", txt)
    try:
        d = json.loads(m.group(0)) if m else {}
    except Exception:
        d = {}
    p = d.get("pass")
    return {"pass": p if isinstance(p, bool) else None, "notes": str(d.get("notes") or txt[-400:])}


def _worktree_meta(d, tid):
    try:
        return json.loads(rd(os.path.join(d, tid + ".meta")) or "{}")
    except Exception:
        return {}


def _worktree_repo(d, tid):
    """Where a run's own changes live: its worktree under .claude/worktrees when it used one, else its repo;
    from the .meta file run.sh writes, or None if that path isn't there any more."""
    meta = _worktree_meta(d, tid)
    repo = meta.get("repo")
    if not repo:
        return None
    path = os.path.join(repo, ".claude", "worktrees", meta["wt"]) if meta.get("wt") else repo
    return path if os.path.isdir(path) else None


def _fetch_worktree_file(repo, path, cap=20000):
    """A changed file's current content straight from the chunk's worktree: the committed blob first
    (git show HEAD:path), then the working tree (the agent may not have committed yet). None when neither can be
    read, e.g. the worktree is already gone, or when the file is longer than cap: a file is only ever shown whole,
    never cut and passed off as its full content."""
    if not repo:
        return None
    rc, out = git(repo, "show", "HEAD:" + path)
    if rc:
        out = rd(os.path.join(repo, path))
    if out is None or len(out) > cap:
        return None
    return out


VERIFY_CONTEXT = 30  # lines of context around each hunk when a large file is shown as a diff


def _wide_diff(repo, fork, path):
    """The file's diff against the run's fork point with generous context (-U30), working tree included, so the
    checker sees the surroundings of each change; None when it can't be made."""
    if not repo or not fork:
        return None
    r = subprocess.run(["git", "-C", repo, "diff", "-U%d" % VERIFY_CONTEXT, fork, "--", path], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 and "@@" in r.stdout else None


def _split_hunks(body):
    """A file's diff as (header, [hunks]); each hunk starts at its @@ line."""
    head, hunks = [], []
    for line in body.splitlines(True):
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
        else:
            head.append(line)
    return "".join(head), ["".join(h) for h in hunks]


def _is_test_path(name):
    b = os.path.basename(name)
    return "/tests/" in "/" + name or "/test/" in "/" + name or b.startswith("test_") or ".test." in b or ".spec." in b or b.endswith("_test.py")


VERIFY_GUIDE = ("\nHOW THE CHANGES ARE SHOWN: small and new files appear whole; large files appear as unified diffs with %d "
                "lines of context, not their full content. Judging from diffs is expected. Where hunks of a large diff "
                "had to be left out for length, they are named exactly below; unseen or omitted content that isn't "
                "related to a criterion is not a reason to fail. If a criterion depends only on omitted content, say so "
                "in notes rather than failing for it.\n" % VERIFY_CONTEXT)


def verify_input(d, tid, budget=60000, artifact_budget=40000, small=8000):
    """Build the checker's prompt: the brief and criteria, a list of every changed file, then the changes within
    the budget. Deletions and lockfiles are named, not shown. Diffs up to `small` characters are shown as they
    are. A larger one is shown as the file's full current content when the file itself is small (up to
    artifact_budget overall), otherwise as a -U30 diff from the chunk's worktree (or the patch), source files
    before tests and the largest changes first; when that doesn't fit, whole hunks are left out and named, never
    cut mid-text. Files of which nothing at all could be shown are recorded in tid.verify.meta.json so the verdict
    can call the run unverifiable rather than failed."""
    prompt = rd(os.path.join(d, tid + ".verify.md")) or ""
    patch = rd(os.path.join(d, tid + ".patch")) or ""
    files, cur = [], None
    for line in patch.splitlines(True):
        if line.startswith("diff --git"):
            cur = {"name": line.split(" b/", 1)[-1].strip(), "body": [line], "kind": "modified", "add": 0, "del": 0}
            files.append(cur)
        elif cur is not None:
            cur["body"].append(line)
            if line.startswith("new file"):
                cur["kind"] = "added"
            elif line.startswith("deleted file"):
                cur["kind"] = "deleted"
            elif line.startswith("Binary files"):
                cur["kind"] = "binary"
            elif line.startswith("+") and not line.startswith("+++"):
                cur["add"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                cur["del"] += 1
    lock = lambda f: os.path.basename(f["name"]) in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "poetry.lock")
    listing = "\n".join("- %s (%s, +%d/-%d)" % (f["name"], f["kind"], f["add"], f["del"]) for f in files)
    shown = [f for f in files if f["kind"] in ("added", "modified") and not lock(f)]
    # priority: source before tests, then the biggest change first
    shown.sort(key=lambda f: (_is_test_path(f["name"]), -(f["add"] + f["del"])))
    for f in shown:
        f["text"] = "".join(f["body"])
    out, used = {}, 0
    # pass 1: every small diff whole (cheap, and a big file can't crowd them out)
    for f in shown:
        if len(f["text"]) <= small and used + len(f["text"]) <= budget:
            out[f["name"]] = f["text"]; used += len(f["text"])
    big = [f for f in shown if f["name"] not in out]
    repo = _worktree_repo(d, tid) if big else None
    meta = _worktree_meta(d, tid) if big else {}
    fork = meta.get("fork") or None
    if repo and not fork and meta.get("base"):
        rc, mb = git(repo, "merge-base", meta["base"], "HEAD")
        fork = mb if rc == 0 and mb else None
    # pass 2: a big diff of a small file -> the file whole
    art_used, rest = 0, []
    for f in big:
        content = _fetch_worktree_file(repo, f["name"], cap=small * 5) if repo else None
        if content is not None and art_used + len(content) <= artifact_budget and used + len(content) <= budget:
            out[f["name"]] = ("\nFULL CURRENT CONTENT of %s (%s; its diff was large, the file is short, so here is the "
                              "whole file, not a diff):\n%s\n" % (f["name"], "new file" if f["kind"] == "added" else "modified", content))
            art_used += len(content); used += len(content)
        else:
            rest.append(f)
    # pass 3: large files as diffs, whole hunks only, sharing what's left of the budget in priority order
    unreadable, partial, omitted_notes = [], [], []
    for i, f in enumerate(rest):
        wide = _wide_diff(repo, fork, f["name"]) if f["kind"] == "modified" else None
        head, hunks = _split_hunks(wide or f["text"])
        share = (budget - used) // (len(rest) - i)
        took, skipped, part = [], [], len(head)
        for n, h in enumerate(hunks, 1):
            if part + len(h) <= share:
                took.append(h); part += len(h)
            else:
                skipped.append((n, h.splitlines()[0] if h else "@@"))
        if not took:
            unreadable.append(f["name"])
            continue
        label = ("\nDIFF of %s (large file: shown as a diff with %d lines of context, not its full content; "
                 "%d of %d hunks shown)\n" % (f["name"], VERIFY_CONTEXT if wide else 3, len(took), len(hunks)))
        txt = label + head + "".join(took)
        if skipped:
            partial.append(f["name"])
            names = "; ".join("hunk %d %s" % (n, hd.split("@@")[1].strip() if hd.count("@@") >= 2 else hd) for n, hd in skipped)
            txt += "[%d hunk(s) of %s omitted for length, not truncated mid-hunk: %s]\n" % (len(skipped), f["name"], names)
            omitted_notes.append("%s: %s" % (f["name"], names))
        out[f["name"]] = txt; used += len(txt) - len(label)
    note = ""
    if unreadable:
        note += "\nNot shown for length (judge from the list above and what you can see): " + ", ".join(unreadable[:40]) + "\n"
    if omitted_notes:
        note += "\nHunks omitted for length (everything else in these files' diffs is shown):\n" + "\n".join("- " + x for x in omitted_notes[:40]) + "\n"
    with open(os.path.join(d, tid + ".verify.meta.json"), "w") as fh:
        json.dump({"unreadable": unreadable, "partial": partial}, fh)
    body = "".join(out[f["name"]] for f in shown if f["name"] in out)
    text = (prompt + "\n" + VERIFY_GUIDE + "\nFILES CHANGED (%d):\n%s\n%s\nTHE CHANGES:\n%s" % (len(files), listing, note, body))
    with open(os.path.join(d, tid + ".verify.full.md"), "w") as fh:
        fh.write(text)
    return len(files)


def _unreadable_files(d, tid):
    try:
        return json.loads(rd(os.path.join(d, tid + ".verify.meta.json")) or "{}").get("unreadable") or []
    except Exception:
        return []


def verify_verdict(d, tid):
    """pass, fail, unverifiable or none. Unverifiable when a file the checker needed was too large to show as a
    diff and couldn't be read from the worktree either, unless the checker passed the change anyway: unreadable
    is not the same as wrong, and shouldn't burn a fix round the agent can't act on."""
    v = _verdict_of(rd(os.path.join(d, tid + ".verify.json")) or "")
    if _unreadable_files(d, tid) and v["pass"] is not True:
        return "unverifiable"
    return {True: "pass", False: "fail"}.get(v["pass"], "none")


def fix_prompt(d, tid, rnd):
    """Write the prompt that sends the checker's notes back to the agent; print the session to resume."""
    v = _verdict_of(rd(os.path.join(d, tid + ".verify.json")) or "")
    try:
        sid = json.loads(rd(os.path.join(d, tid + ".json")) or "{}").get("session_id") or ""
    except Exception:
        sid = ""
    with open(os.path.join(d, "%s.fix%d.md" % (tid, rnd)), "w") as fh:
        fh.write("An independent reviewer checked your work against the task's acceptance criteria and it did not pass. "
                 "Their notes:\n\n" + v["notes"] + "\n\nFix what is missing or wrong. Stay inside the task's scope, and restore "
                 "anything you changed outside it. If a criterion truly can't be met, say exactly why in your report instead "
                 "of working around it. Commit to your branch and end with your report and the ```runway block.")
    return sid


NOTE_REPEAT_RATIO = 0.85  # how close two rounds' checker notes must be (difflib ratio) to call the second one a repeat


def _notes_repeat(a, b):
    """Whether two checker notes are substantially the same: near-identical wording, not just both failing."""
    a, b = (a or "").strip(), (b or "").strip()
    return bool(a) and bool(b) and difflib.SequenceMatcher(None, a, b).ratio() >= NOTE_REPEAT_RATIO


def _fix_round_spend(d, tid):
    """Every dollar spent on this task's run so far: the agent's passes and the checker's, across every round."""
    total = 0.0
    paths = [os.path.join(d, tid + ".json")] + sorted(glob.glob(os.path.join(d, tid + ".r*.json"))) + \
        sorted(glob.glob(os.path.join(d, tid + ".verify*.json")))
    for path in paths:
        try:
            total += json.loads(rd(path) or "{}").get("total_cost_usd") or 0
        except Exception:
            pass
    return round(total, 4)


def fix_round_gate(d, tid, rnd, max_rounds):
    """Whether run.sh's fix loop may send round `rnd + 1` to the agent, after round `rnd` (0 for the first pass) just
    came back from the checker. Says "stop" (and writes <tid>.fixcap.json with the repeated note and the spend so
    far, for the page to surface to Austin) once the checker's note repeats round over round, or once `rnd` has
    reached max_rounds; otherwise "continue" when the checker failed the work and there is budget for another round."""
    v = _verdict_of(rd(os.path.join(d, tid + ".verify.json")) or "")
    if v.get("pass") is not False:
        return "stop"  # passed, or unreadable: nothing to fix
    reason = None
    if rnd >= max_rounds:
        reason = "max_rounds"
    elif rnd >= 1:
        prev = _verdict_of(rd(os.path.join(d, tid + ".verify.r%d.json" % rnd)) or "").get("notes", "")
        if _notes_repeat(v.get("notes", ""), prev):
            reason = "repeated"
    if reason is None:
        return "continue"
    write_json_atomic(os.path.join(d, tid + ".fixcap.json"),
                       {"reason": reason, "note": v.get("notes", ""), "rounds": rnd, "cost": _fix_round_spend(d, tid), "at": int(time.time())})
    return "stop"


# ---------- checks-fix rounds: red project checks go back to the agent ----------
CHECKS_FIX_TAIL_LINES = 120
CHECKS_REPEAT_RATIO = 0.95  # how close two rounds' failure output must be to count as "the same failure"


def _checksfix_path(d, tid):
    return os.path.join(d, tid + ".checksfix.json")


def _checksfix_read(d, tid):
    try:
        return json.loads(rd(_checksfix_path(d, tid)) or "{}")
    except ValueError:
        return {}


def _checks_log_tail(d, tid):
    return "\n".join((rd(os.path.join(d, tid + ".checks.log")) or "").splitlines()[-CHECKS_FIX_TAIL_LINES:])


def _failure_sig(text):
    """A failure's output with numbers (timings, ports, counts, hashes) and spacing taken out, so two runs of the
    same failure compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"[0-9a-f]{7,}|\d+", "#", text or "")).strip()


def _failing_cmd(d, tid, step):
    meta = json.loads(rd(os.path.join(d, tid + ".meta")) or "{}")
    repo, wt = meta.get("repo"), meta.get("wt")
    if not repo or not wt:
        return ""
    raw, _ = checks_source(repo, meta.get("base"), os.path.join(repo, ".claude", "worktrees", wt))
    checks, _ = parse_checks(raw)
    for c in checks or []:
        if c["name"] == step:
            return c["cmd"]
    return ""


def checks_fix_prompt(d, tid, rnd):
    """Write the prompt that sends the failing project check back to the agent; record the round (the failing step and
    the tail of its log); print the session to resume."""
    st = json.loads(rd(os.path.join(d, tid + ".checks.json")) or "{}")
    step, tail = st.get("step") or "checks", _checks_log_tail(d, tid)
    try:
        sid = json.loads(rd(os.path.join(d, tid + ".json")) or "{}").get("session_id") or ""
    except Exception:
        sid = ""
    rec = _checksfix_read(d, tid)
    split = rec.get("split") or {}
    if split.get("round") != rnd - 1 or not split.get("mine") or split.get("step") != step:
        split = {}
    if split:  # a pytest check the gate split per test: only the task's own failures, from their targeted rerun
        tail = split.get("tail") or tail
    rounds = [r for r in rec.get("rounds", []) if r.get("round") != rnd] + [{"round": rnd, "step": step, "tail": tail}]
    write_json_atomic(_checksfix_path(d, tid), dict(rec, rounds=sorted(rounds, key=lambda r: r["round"])))
    if split:
        head = "Runway ran the project's checks on your work and these tests in the check \"%s\" failed, every time it ran them:\n\n%s\n\n" % (
            step, "\n".join("- " + i for i in split["mine"]))
        if split.get("baseOnly"):
            head += "These also fail on %s without your work, so they are already failing on %s, not yours. Leave them alone:\n\n%s\n\n" % (
                split.get("base") or "the base", split.get("base") or "the base", "\n".join("- " + i for i in split["baseOnly"]))
        head += "Command: %s\n\nThe last %d lines of their output:\n\n%s\n\n" % (
            targeted_cmd(split.get("cmd") or "", split.get("wt") or "", split.get("repo") or "", split["mine"]) or split.get("cmd"),
            CHECKS_FIX_TAIL_LINES, tail)
    else:
        head = "Runway ran the project's checks on your work and the check \"%s\" failed.\n\nCommand: %s\n\nThe last %d lines of its output:\n\n%s\n\n" % (
            step, _failing_cmd(d, tid, step) or "(see .runway.json)", CHECKS_FIX_TAIL_LINES, tail)
    with open(os.path.join(d, "%s.checksfix%d.md" % (tid, rnd)), "w") as fh:
        fh.write(head + "Fix the cause of the failure, not the test: do not weaken, skip or "
                 "delete a test or the check to make it pass. Some checks (browser tests) could not run in your sandbox, so "
                 "work from this output. Stay inside the task's scope. Commit to your branch and end with your report and the "
                 "```runway block.")
    return sid


def _in_base_worktree(repo, base, wtp, fn):
    """Call fn(folder) in a throwaway detached worktree of base (sharing the task's node_modules). Returns
    (True, fn's result), or (False, None) when the worktree could not be made."""
    tmp = tempfile.mkdtemp(prefix="runway-base-")
    bw = os.path.join(tmp, "base")
    try:
        rc, _ = git(repo, "worktree", "add", "-q", "--detach", bw, base)
        if rc:
            return False, None
        nm = os.path.join(wtp, "node_modules")
        if os.path.isdir(nm) and not os.path.exists(os.path.join(bw, "node_modules")):
            os.symlink(nm, os.path.join(bw, "node_modules"))
        return True, fn(bw)
    finally:
        git(repo, "worktree", "remove", "--force", bw)
        shutil.rmtree(tmp, ignore_errors=True)


def checks_split(d, tid):
    """Per-test blame for a red pytest check, like the merge gate's: the failing test ids from <task>.checks.log,
    split into baseOnly (they fail on the task's base alone, in a throwaway worktree; cached per id in
    <task>.checksfix.json "baseIds"), flaky (they pass on a targeted rerun in the task's worktree) and mine (the
    task's own: failing on every rerun, and green or absent on the base). None when the check cannot be split
    (not red, timed out, not pytest, or no test ids): the caller keeps the whole-run base check."""
    st = json.loads(rd(os.path.join(d, tid + ".checks.json")) or "{}")
    if st.get("state") != "red":
        return None
    meta = json.loads(rd(os.path.join(d, tid + ".meta")) or "{}")
    repo, wt, base = meta.get("repo"), meta.get("wt"), meta.get("base")
    if not repo or not wt or not base:
        return None
    wtp = os.path.join(repo, ".claude", "worktrees", wt)
    raw, _ = checks_source(repo, base, wtp)
    checks, _ = parse_checks(raw)
    red = next((c for c in checks or [] if c["name"] == st.get("step")), None)
    ids = failed_test_ids(rd(os.path.join(d, tid + ".checks.log")))
    if not red or not ids or not targeted_cmd(red["cmd"], wtp, repo, ids):
        return None
    rec = _checksfix_read(d, tid)
    known = dict(rec.get("baseIds") or {})
    unknown = [i for i in ids if i not in known]
    if unknown:
        def probe(bw):
            there = [i for i in unknown if os.path.exists(os.path.join(bw, i.split("::")[0]))]
            fails = {i: False for i in unknown}  # a test the base does not have cannot fail there: it is the task's
            cmd = targeted_cmd(red["cmd"], bw, repo, there) if there else None
            if cmd:
                blog = os.path.join(d, tid + ".checks-base.log")
                open(blog, "w").close()
                brc, _ = _run_check(cmd, bw, red["timeout"], blog)
                seen = failed_test_ids(rd(blog))
                for i in there:  # red naming no test (a crash): all of them count as failing on the base
                    fails[i] = brc != 0 and (i in seen if seen else True)
            return fails
        made, fails = _in_base_worktree(repo, base, wtp, probe)
        known.update(fails if made else {i: False for i in unknown})  # can't tell: treat the base as green
    base_only = [i for i in ids if known.get(i)]
    cand = [i for i in ids if not known.get(i)]
    flaky, mine, tail = [], [], ""
    if cand:
        for f in glob.glob(os.path.join(d, tid + ".checks-own*.log")):
            os.remove(f)
        flaky, mine, _ = _rerun_ids(wtp, repo, red, cand, d, tid, "own")
        if mine is None:
            flaky, mine = [], cand
        if flaky:
            flaky_record(repo, flaky)
        logs = sorted(glob.glob(os.path.join(d, tid + ".checks-own*.log")), key=lambda f: int(re.sub(r"\D", "", f[-8:]) or 0))
        if mine and logs:
            tail = "\n".join((rd(logs[-1]) or "").splitlines()[-CHECKS_FIX_TAIL_LINES:])
    write_json_atomic(_checksfix_path(d, tid), dict(_checksfix_read(d, tid), baseIds=known))
    return {"step": red["name"], "cmd": red["cmd"], "base": base, "repo": repo, "wt": wtp,
            "mine": mine, "baseOnly": base_only, "flaky": flaky, "tail": tail}


def checks_base_red(ch, tid):
    """Whether the project's checks already fail on the task's base alone (so the task did not cause it). Runs them in
    a throwaway detached worktree of the base, once per task; the answer is cached in <task>.checksfix.json."""
    d = os.path.join(RUNS, ch)
    rec = _checksfix_read(d, tid)
    if "baseRed" in rec:
        return rec["baseRed"]
    meta = json.loads(rd(os.path.join(d, tid + ".meta")) or "{}")
    repo, wt, base = meta.get("repo"), meta.get("wt"), meta.get("base")
    if not repo or not wt or not base:
        return False
    wtp = os.path.join(repo, ".claude", "worktrees", wt)
    raw, _ = checks_source(repo, base, wtp)
    checks, _ = parse_checks(raw)
    if not checks:
        return False
    made, red = _in_base_worktree(repo, base, wtp, lambda bw: _run_checks_in(bw, checks, os.path.join(d, tid + ".checks-base.log")))
    if not made:
        return False  # can't tell: the task's own checks said red, so treat the base as green
    write_json_atomic(_checksfix_path(d, tid), dict(_checksfix_read(d, tid), baseRed=bool(red), baseStep=(red or {}).get("name")))
    return bool(red)


def checks_fix_gate(d, tid, rnd, max_rounds):
    """Whether run.sh may start checks-fix round rnd + 1, after the checks ran for the rnd-th time (0 = after the
    agent's own passes). "continue" only when the checks are red or timed out, the base is not red too, rounds remain
    and the failure is not the same as the one the last round was sent. Otherwise "stop"; the reason is kept in
    <task>.checksfix.json for the page."""
    st = json.loads(rd(os.path.join(d, tid + ".checks.json")) or "{}")
    if st.get("state") not in ("red", "timeout"):
        return "stop"
    reason = None
    # A pytest check is judged per test: failures that also fail on the base alone, or pass on a targeted rerun, are
    # not the task's. Only when none of its own are left does the gate stop without a round.
    split = checks_split(d, tid)
    rec = _checksfix_read(d, tid)
    now_tail = _checks_log_tail(d, tid)
    if split is not None:
        upd = {"split": dict(split, round=rnd)}
        if split["baseOnly"]:
            upd.update(baseRed=True, baseStep=split["step"])
        rec = dict(rec, **upd)
        write_json_atomic(_checksfix_path(d, tid), rec)
        now_tail = split["tail"] or now_tail
    if split is not None and not split["mine"]:
        reason = "base-red" if split["baseOnly"] else "flaky"
    elif split is None and rnd == 0 and checks_base_red(os.path.basename(d), tid):
        reason = "base-red"
    elif rnd >= max_rounds:
        reason = "limit"
    elif rnd >= 1:
        sent = [r for r in rec.get("rounds", []) if r.get("round") == rnd]
        if sent and sent[0].get("step") == st.get("step") and \
                difflib.SequenceMatcher(None, _failure_sig(sent[0].get("tail")), _failure_sig(now_tail)).ratio() >= CHECKS_REPEAT_RATIO:
            reason = "repeated"
    if reason is None:
        return "continue"
    write_json_atomic(_checksfix_path(d, tid), dict(_checksfix_read(d, tid), stop=reason, stopStep=st.get("step"),
                                                    stopTail=_checks_log_tail(d, tid)[-2000:], at=int(time.time())))
    return "stop"


# ---------- main ----------
def tick(extra=()):
    os.makedirs(RUNS, exist_ok=True)
    os.makedirs(QUEUE, exist_ok=True)
    lockf = open(os.path.join(BASE, ".lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # another tick is running (first usage scan can take a while): reuse its last output
        try:
            out = json.loads(rd(STATUS) or "{}")
        except Exception:
            out = {}
        if extra:
            out.setdefault("chunks", {}).update({ch: chunk_status(ch) for ch in extra})
        return out
    sched_file = os.path.join(BASE, ".scheduler")
    if os.environ.get("XPC_SERVICE_NAME") == "ai.runway.helper":
        with open(sched_file, "w") as f:
            f.write(str(int(time.time())))
    launch_due()
    try:  # JOBS-CONTRACT.md: leases, the hard timeout and the reaper; a problem here never stops the status
        lease_reap()
    except Exception as e:
        print("lease_reap:", e)
    ensure_patches()
    try:  # environments nobody needs any more, once an hour; a problem here never stops the status
        if time.time() - int((_ap_read(_env_gc_state()) or {}).get("at") or 0) >= ENV_GC_EVERY:
            env_gc()
    except Exception:
        pass
    try:
        usage = summarize(scan_usage())
    except Exception as e:
        usage = {"error": str(e)[:200]}
    try:
        sched = int(float(rd(sched_file) or 0))
    except ValueError:
        sched = 0
    try:
        calib = calibration_report()
    except Exception as e:
        calib = {"table": [], "error": str(e)[:200]}
    out = {"v": VERSION, "home": HOME, "now": int(time.time()), "scheduler": sched, "usage": usage, "chunks": all_status(extra),
           "integrations": integrations(), "merges": merges_status(), "calibration": calib, "autopilot": ap_reports_status(),
           "envs": all_envs(), "handsOff": handsoff_enabled(), "claims": claims_all(), "flaky": flaky_top()}
    try:
        notify(out)
    except Exception as e:  # a notification problem must never stop the status from updating
        out["notifyError"] = str(e)[:200]
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, STATUS)
    return out


# Merge-train failure log: one JSON object per line, append-only, so `grep` works on it.
# Set RUNWAY_MERGE_LOG to move it; the default sits next to status.json.
MERGE_PHASES = ("create", "test", "merge")
MERGE_LOG_TAIL = 2000  # stderr characters kept per entry


def merge_log_path():
    return os.environ.get("RUNWAY_MERGE_LOG") or os.path.join(BASE, "merge-train.log")


def truncate_tail(text, limit=MERGE_LOG_TAIL):
    text = text or ""
    if len(text) <= limit:
        return text
    return "[...truncated, %d of %d chars dropped] " % (len(text) - limit, len(text)) + text[-limit:]


def log_failure(chunk, branch, phase, exit_code, stderr, path=None, now=None, paths=None):
    if phase not in MERGE_PHASES:
        raise ValueError("phase must be one of %s" % ", ".join(MERGE_PHASES))
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "chunk": chunk, "branch": branch,
           "phase": phase, "exit_code": exit_code, "stderr_tail": truncate_tail(stderr)}
    if paths is not None:  # conflict entries name the files, so they can be read without the log's text
        rec["paths"] = paths
    path = path or merge_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def read_failures(path=None, n=None):
    try:
        with open(path or merge_log_path(), encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except FileNotFoundError:
        return []
    recs = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("phase") in MERGE_PHASES:
            recs.append(rec)
    if n is not None:
        recs = recs[-n:] if n > 0 else []
    return recs


def format_failures(recs):
    if not recs:
        return "No merge-train failures logged."
    groups = {}
    for r in recs:
        groups.setdefault(r.get("chunk") or "?", []).append(r)
    out = []
    for chunk, rows in groups.items():
        out.append("%s  (%d failure%s)" % (chunk, len(rows), "" if len(rows) == 1 else "s"))
        for r in rows:
            out.append("  %s  %-6s exit %s  %s" % (r.get("ts") or "?", r.get("phase"), r.get("exit_code"), r.get("branch") or "?"))
            for ln in (r.get("stderr_tail") or "").strip().splitlines()[-8:]:
                out.append("      " + ln)
        out.append("")
    return "\n".join(out).rstrip()


# ---------- merge-train CLI: run, list, failures, retry, prune ----------
# Thin commands over the runner (run_checks) and git. Only worktrees under .claude/worktrees on runway-task/*
# branches are touched, and --dry-run never runs a command, writes a file or changes the repo.
TRAIN_CMDS = ("run", "list", "failures", "retry", "prune")
TRAIN_EXIT = """exit codes:
  0  green, or the command finished (list, failures, prune)
  1  a check went red or timed out, or a git step failed
  2  usage error: bad arguments, unknown chunk or task, or retry with no recorded failure
  3  nothing to run: the project configures no checks (neither green nor red)"""
TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


class TrainUsage(Exception):
    """A bad argument, or a chunk or task with nothing to act on. The CLI exits 2."""


def _train_parser():
    fmt = argparse.RawDescriptionHelpFormatter
    p = argparse.ArgumentParser(prog="runway.py", formatter_class=fmt, epilog=TRAIN_EXIT,
                                description="Merge train: run a task's checks in its worktree, list task worktrees, "
                                            "show and retry check failures, and prune stale worktrees.")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND", required=True)

    def command(name, help, examples):
        return sub.add_parser(name, help=help, description=help, formatter_class=fmt,
                              epilog="examples:\n" + "\n".join("  " + e for e in examples) + "\n\n" + TRAIN_EXIT)

    r = command("run", "run a task's checks in its worktree, the same gate a merge uses",
                ["runway.py run chunk-1 task-a --dry-run   # print the git and test commands, run nothing",
                 "runway.py run chunk-1 task-a             # create the worktree if needed, then run the checks"])
    r.add_argument("chunk", help="chunk id, as shown in the page")
    r.add_argument("task", help="task id within the chunk")
    r.add_argument("--dry-run", action="store_true", help="print the exact git and test commands without running them")

    ls = command("list", "list the task worktrees under .claude/worktrees, active or stale",
                 ["runway.py list                          # the current checkout",
                  "runway.py list --repo ~/Projects/site   # another project"])
    ls.add_argument("--repo", metavar="PATH", help="a checkout of the project (default: the current directory)")

    f = command("failures", "show the most recent check failures: red or timed-out checks, newest last",
                ["runway.py failures                      # the last 10",
                 "runway.py failures -n 25 --chunk chunk-1 # the last 25 for one chunk"])
    f.add_argument("-n", "--last", type=int, default=10, metavar="N", help="how many to show (default 10)")
    f.add_argument("--chunk", metavar="CHUNK", help="only failures for this chunk")

    rt = command("retry", "run a task's checks again, but only if it has a recorded failure",
                 ["runway.py retry chunk-1 task-a --dry-run   # show what a retry would run",
                  "runway.py retry chunk-1 task-a             # run it again after fixing the problem"])
    rt.add_argument("chunk", help="chunk id")
    rt.add_argument("task", help="task id within the chunk")
    rt.add_argument("--dry-run", action="store_true", help="print the exact git and test commands without running them")

    pr = command("prune", "drop git's records of task worktrees whose directories were deleted",
                 ["runway.py prune --dry-run              # list what would be dropped, change nothing",
                  "runway.py prune --repo ~/Projects/site # drop the stale records"])
    pr.add_argument("--repo", metavar="PATH", help="a checkout of the project (default: the current directory)")
    pr.add_argument("--dry-run", action="store_true", help="print the git command and the stale records, change nothing")
    return p


def _check_id(kind, value):
    if not TASK_ID_RE.fullmatch(value or ""):
        raise TrainUsage("%s %r: use letters, digits, dot, dash or underscore, starting with a letter or digit" % (kind, value))
    return value


def _repo_root(path):
    where = os.path.abspath(path or os.getcwd())
    rc, out = git(where, "rev-parse", "--show-toplevel")
    if rc:
        raise TrainUsage("%s is not inside a git repository" % where)
    return out


def _task_meta(ch, tid):
    """The worktree a task runs in, from its .meta. Raises TrainUsage when the task has none."""
    try:
        meta = json.loads(rd(os.path.join(RUNS, ch, tid + ".meta")) or "null")
    except ValueError:
        meta = None
    if not isinstance(meta, dict) or not meta.get("repo") or not meta.get("wt"):
        raise TrainUsage("no worktree task %s in chunk %s; check the chunk and task ids" % (tid, ch))
    return meta


def _task_worktrees(repo):
    """Worktrees on runway-task/* branches, from git's own list. Nothing is changed."""
    _, out = git(repo, "worktree", "list", "--porcelain")
    rows, cur = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "branch": "", "prunable": False}
            rows.append(cur)
        elif cur is not None and line.startswith("branch refs/heads/runway-task/"):
            cur["branch"] = line[len("branch refs/heads/"):]
        elif cur is not None and line.startswith("prunable"):
            cur["prunable"] = True
    task_rows = [r for r in rows if r["branch"]]
    for r in task_rows:
        r["task"] = r["branch"][len("runway-task/"):]
        r["stale"] = r["prunable"] or not os.path.isdir(r["path"])
    return task_rows


def _plan(ch, tid):
    """The commands run_checks would run for this task, worked out from disk and git without running any.
    Returns (git_commands, checks, error, worktree_path)."""
    meta = _task_meta(ch, tid)
    repo, base = meta["repo"], meta.get("base") or "HEAD"
    branch, wtp = "runway-task/" + tid, os.path.join(repo, ".claude", "worktrees", meta["wt"])
    git_cmds = []
    if not os.path.isdir(wtp):
        has_branch = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "-q", "refs/heads/" + branch],
                                    capture_output=True).returncode == 0
        git_cmds = [["git", "-C", repo, "worktree", "add", "-q", wtp, branch] if has_branch
                    else ["git", "-C", repo, "worktree", "add", "-q", "-b", branch, wtp, base]]
    raw, _ = checks_source(repo, base, wtp)  # read-only: the checks as the run will read them
    checks, err = parse_checks(raw)
    return git_cmds, checks, err, wtp


def _cmd_run(ch, tid, dry):
    _check_id("chunk", ch)
    _check_id("task", tid)
    _task_meta(ch, tid)
    if dry:
        git_cmds, checks, err, wtp = _plan(ch, tid)
        if err:
            print("@@ERR " + err, file=sys.stderr)
            return 1
        if not checks:
            print("No checks configured for this project, so nothing would run.")
            print("@@DRYRUN 0 commands, nothing run")
            return 3
        if not git_cmds:
            print("# worktree %s already exists and is reused" % wtp)
        for g in git_cmds:
            print("$ " + shlex.join(g))
        for c in checks:
            print("$ (cd %s && /bin/bash -c %s)  # check %s, timeout %ss" % (shlex.quote(wtp), shlex.quote(c["cmd"]), c["name"], c["timeout"]))
        print("@@DRYRUN %d commands, nothing run" % (len(git_cmds) + len(checks)))
        return 0
    v = run_checks(ch, tid)
    print("@@CHECKS %s %s %s" % (v["verdict"], ch, tid))
    if v.get("reason"):
        print("reason: %s%s" % (v["reason"], " (check %s)" % v["check"] if v.get("check") else ""))
    return {"green": 0, "none": 3}.get(v["verdict"], 1)


def _cmd_retry(ch, tid, dry):
    _check_id("chunk", ch)
    _check_id("task", tid)
    last = [r for r in read_check_failures() if r.get("chunk") == ch and r.get("task") == tid]
    if not last:
        raise TrainUsage("no recorded failure for %s/%s, so nothing to retry; use: runway.py run %s %s" % (ch, tid, ch, tid))
    print("# last failure: %s, %s" % (last[-1].get("state"), last[-1].get("reason") or "see the log"))
    return _cmd_run(ch, tid, dry)


def _cmd_list(repo_arg):
    rows = _task_worktrees(_repo_root(repo_arg))
    if not rows:
        print("No task worktrees.")
        return 0
    for r in rows:
        print("%-7s %-24s %s" % ("stale" if r["stale"] else "active", r["task"], r["path"]))
    return 0


def _cmd_failures(n, chunk):
    if n < 1:
        raise TrainUsage("-n must be at least 1")
    recs = read_check_failures()
    if chunk:
        recs = [r for r in recs if r.get("chunk") == chunk]
    recs = recs[-n:]
    if not recs:
        print("No check failures recorded.")
        return 0
    for r in recs:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("at") or 0))
        print("%s  %s/%s  %s  check %s%s" % (when, r.get("chunk"), r.get("task"), r.get("state"), r.get("check") or "-",
                                             ", " + r["reason"] if r.get("reason") else ""))
        if r.get("log"):
            print("    log: " + r["log"])
    return 0


def _cmd_prune(repo_arg, dry):
    repo = _repo_root(repo_arg)
    stale = [r for r in _task_worktrees(repo) if r["stale"]]
    cmd = ["git", "-C", repo, "worktree", "prune", "-v"]
    if dry:
        for r in stale:
            print("# stale: %s  %s" % (r["task"], r["path"]))
        print("$ " + shlex.join(cmd))
        print("@@DRYRUN %d stale record%s, nothing pruned" % (len(stale), "" if len(stale) == 1 else "s"))
        return 0
    rc, out = git(repo, "worktree", "prune", "-v")
    if rc:
        print("@@ERR git worktree prune failed: " + out[-300:], file=sys.stderr)
        return 1
    print(out or "Nothing to prune.")
    print("@@PRUNED %d" % len(stale))
    return 0


def train_cli(argv):
    """Parse and run one merge-train command. Returns the exit code; argparse exits itself for -h and bad arguments."""
    args = _train_parser().parse_args(argv)
    try:
        if args.cmd == "run":
            return _cmd_run(args.chunk, args.task, args.dry_run)
        if args.cmd == "retry":
            return _cmd_retry(args.chunk, args.task, args.dry_run)
        if args.cmd == "list":
            return _cmd_list(args.repo)
        if args.cmd == "failures":
            return _cmd_failures(args.last, args.chunk)
        return _cmd_prune(args.repo, args.dry_run)
    except TrainUsage as e:
        print("@@ERR " + str(e), file=sys.stderr)
        return 2


def read_check_failures(path=None):
    """Every red or timed-out check record from failures.log, oldest first. Unreadable lines are skipped."""
    recs = []
    for line in (rd(path or FAILURES) or "").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("state") in ("red", "timeout"):
            recs.append(rec)
    return recs


# ---------- Runway as an MCP server: interactive sessions pull work from the board ----------
# No second task database. The page mirrors its ready tasks to board.json (board-sync); a session claims one, which
# creates the worktree exactly as the runner does and writes claims/<task>.json plus a run folder the existing status,
# live and merge machinery already understands; the page applies the claim to its own db. Merging is never done here:
# it stays on the page's Merge button and the merge gate.
BOARD_STALE = 86400  # seconds after which the board mirror is old enough that the page must be opened again
MCP_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
CHECKS_TAIL = 3000  # characters of a red checks log returned to the session


def board_path():
    return os.path.join(BASE, "board.json")


def claims_dir():
    return os.path.join(BASE, "claims")


def claim_path(tid):
    return os.path.join(claims_dir(), tid + ".json")


def mcp_log(msg):
    try:
        os.makedirs(BASE, exist_ok=True)
        with open(os.path.join(BASE, "helper.log"), "a") as f:
            f.write("%s mcp %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def board_validate(data):
    """The error in a board mirror's shape, or None. Strings that become paths or refs must be plain ids."""
    if not isinstance(data, dict) or data.get("v") != 1:
        return 'the board must be an object with "v": 1'
    if isinstance(data.get("at"), bool) or not isinstance(data.get("at"), (int, float)):
        return '"at" must be a number (milliseconds)'
    if not isinstance(data.get("tasks"), list):
        return '"tasks" must be a list'
    for i, t in enumerate(data["tasks"]):
        if not isinstance(t, dict):
            return "task %d is not an object" % i
        for k in ("id", "projectId", "title", "repo", "prompt"):
            if not isinstance(t.get(k), str) or not t[k].strip():
                return 'task %d needs a non-empty string "%s"' % (i, k)
        if not ID_RE.fullmatch(t["id"]):
            return "task %d has an id that is not a plain id: %r" % (i, t["id"][:40])
        for k in ("projectName", "kind", "model", "base"):
            if t.get(k) is not None and not isinstance(t[k], str):
                return 'task %d: "%s" must be a string' % (i, k)
        if t.get("estTokens") is not None and (isinstance(t["estTokens"], bool) or not isinstance(t["estTokens"], (int, float))):
            return 'task %d: "estTokens" must be a number' % i
        if t.get("acceptance") is not None and not isinstance(t["acceptance"], (str, list)):
            return 'task %d: "acceptance" must be a string or a list' % i
    return None


def board_sync(b64):
    """Validate a board mirror and write it to board.json atomically. Returns (ok, message); writes nothing when invalid."""
    try:
        data = json.loads(base64.b64decode(b64, validate=True).decode())
    except Exception:
        return False, "the board is not base64 JSON"
    err = board_validate(data)
    if err:
        return False, err
    write_json_atomic(board_path(), data)
    return True, "%d tasks" % len(data["tasks"])


def board_read():
    try:
        data = json.loads(rd(board_path()) or "")
    except ValueError:
        return None
    return data if board_validate(data) is None else None


def claim_read(tid):
    try:
        c = json.loads(rd(claim_path(tid)) or "")
    except ValueError:
        return None
    return c if isinstance(c, dict) else None


def claim_write(c):
    write_json_atomic(claim_path(c["taskId"]), c)


def claims_all():
    """Every claim file that is not finished or released, or that ended within LIVE_KEEP: what status and live.json carry,
    so the page applies a claim and then sees how it ended."""
    out, now = [], time.time()
    for f in sorted(glob.glob(os.path.join(claims_dir(), "*.json"))):
        try:
            c = json.loads(rd(f) or "")
        except ValueError:
            continue
        if isinstance(c, dict) and (c.get("state") == "claimed" or now - os.path.getmtime(f) <= LIVE_KEEP):
            out.append(c)
    return out


def _mc_chunk_id():
    return "mc" + time.strftime("%Y%m%d%H%M") + "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(3))


def _runway_running(tid):
    """The chunk that has task tid queued, running or verifying, or None."""
    for sf in glob.glob(os.path.join(RUNS, "*", tid + ".status")):
        if (rd(sf) or "").strip() in ("queued", "running", "verifying"):
            return os.path.basename(os.path.dirname(sf))
    return None


def _wt_dirty(wtp):
    rc, out = git(wtp, "status", "--porcelain")
    return [l[3:] for l in out.splitlines() if l.strip()] if rc == 0 else []


def _commits(wtp, fork):
    rc, out = git(wtp, "log", "--format=%h %s", fork + "..HEAD") if fork else (1, "")
    return [l for l in out.splitlines() if l.strip()] if rc == 0 else []


def _put(path, text):
    with open(path, "w") as f:
        f.write(text)


def _live_best_effort():
    try:
        live_write()
    except Exception:
        pass


def claim_task(tid, client):
    """Claim a ready task for an interactive session. Returns (ok, result): the claim and the brief, or the reason not."""
    if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
        return False, "taskId must be a task id from list_ready_tasks."
    client = (client or "").strip()[:60] or "session"
    board = board_read()
    task = next((t for t in (board or {}).get("tasks", []) if t["id"] == tid), None)
    if task is None:
        return False, "Task %s is not on the board. Call list_ready_tasks (open Runway if the board is stale)." % tid
    os.makedirs(BASE, exist_ok=True)
    lock = _lock(os.path.join(BASE, "claims.lock"), 30)
    if lock is None:
        return False, "Another claim is in progress; try again."
    try:
        old = claim_read(tid)
        if old and old.get("state") in ("claimed", "finished"):
            return False, "Task %s is already %s by %s." % (tid, old["state"], old.get("client") or "a session")
        busy = _runway_running(tid)
        if busy:
            return False, "Task %s is already running in Runway chunk %s." % (tid, busy)
        repo = os.path.expanduser(task["repo"])
        if git(repo, "rev-parse", "--git-dir")[0] != 0:
            return False, "The project's repo %s is not a git checkout on this Mac." % repo
        base = task.get("base") or git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
        if not base or git(repo, "rev-parse", "--verify", "-q", base + "^{commit}")[0] != 0:
            return False, "Base branch %r was not found in %s." % (base, repo)
        try:
            cfg = project_config(repo)
        except ConfigError as e:
            return False, str(e)
        wt, branch = "runway-" + tid, "runway-task/" + tid
        wtp = os.path.join(repo, ".claude", "worktrees", wt)
        created = not os.path.isdir(wtp)
        if created:
            fork = git(repo, "rev-parse", base)[1].strip()
            integ = "runway/" + base  # approved work waiting there is the start when it already contains base, as the runner does
            if git(repo, "rev-parse", "-q", "--verify", "refs/heads/" + integ)[0] == 0 and \
                    git(repo, "merge-base", "--is-ancestor", base, integ)[0] == 0:
                fork = git(repo, "rev-parse", integ)[1].strip()
            os.makedirs(os.path.dirname(wtp), exist_ok=True)
            rc, out = git(repo, "worktree", "add", "-q", "-B", branch, wtp, fork)
            if rc:
                return False, "Could not create the worktree: " + out[-300:]
        else:
            rc, first = git(wtp, "reflog", "--format=%H", "HEAD")
            fork = first.splitlines()[-1] if rc == 0 and first.strip() else git(repo, "rev-parse", base)[1].strip()
        ch = _mc_chunk_id()
        d = os.path.join(RUNS, ch)
        os.makedirs(d, exist_ok=True)
        cmd = cfg.get("setup")
        if isinstance(cmd, list):
            cmd = " && ".join(cmd)
        spec = task_env_spec(repo, tid, cfg)
        values = {}
        if spec:
            write_json_atomic(os.path.join(d, ENV_SPEC), spec)
            values = task_env_vars(spec)
        missing = [n for n in (spec or {}).get("names", []) if n not in values]
        failure = None
        if missing:
            failure = "These variables are not set in %s: %s." % (env_file(repo), ", ".join(missing))
        elif cmd and created:
            slog = os.path.join(d, tid + ".setup.log")
            open(slog, "w").close()
            rc, timed_out = _run_check(cmd, wtp, SETUP_TIMEOUT, slog, env=values)
            if timed_out or rc != 0:
                failure = "Setup `%s` failed (%s). Log tail:\n%s" % (cmd, "timed out" if timed_out else "exit %s" % rc,
                                                                    _env_redact(_log_tail(slog), _secret_values(values)))
        if failure:
            if created:  # nothing of the claim is kept: the worktree this call made goes, and its empty branch
                git(repo, "worktree", "remove", "--force", wtp)
                git(repo, "branch", "-D", branch)
            shutil.rmtree(d, ignore_errors=True)
            return False, failure
        now = int(time.time())
        write_json_atomic(os.path.join(d, tid + ".meta"), {"repo": repo, "wt": wt, "base": base, "fork": fork, "dirty": 0})
        _put(os.path.join(d, tid + ".status"), "running\n")
        _put(os.path.join(d, tid + ".start"), "%d\n" % now)
        _put(os.path.join(d, tid + ".prompt.md"), task["prompt"])
        claim_write({"v": 1, "taskId": tid, "chunkId": ch, "client": client, "state": "claimed", "at": now * 1000, "finishedAt": None,
                     "worktree": wtp, "branch": branch, "note": "", "checks": {"state": "none", "log": ""}})
    finally:
        lock.close()
    _live_best_effort()
    mcp_log("claim %s by %s in %s" % (tid, client, ch))
    return True, {"taskId": tid, "chunkId": ch, "worktree": wtp, "branch": branch, "base": base,
                  "title": task["title"], "prompt": task["prompt"],
                  "next": "Work in the worktree, commit your changes there, then call finish_task. Do not merge or push: "
                          "merging is the Merge button in Runway."}


def _checks_info(c):
    live = _ap_read(os.path.join(RUNS, c["chunkId"], c["taskId"] + ".checks.json")) or {}
    return {"state": live.get("state") or (c.get("checks") or {}).get("state") or "none", "step": live.get("step")}


def task_status(tid):
    c = claim_read(tid) if isinstance(tid, str) and ID_RE.fullmatch(tid) else None
    if not c:
        return False, "Task %s has no claim." % tid
    meta = _ap_read(os.path.join(RUNS, c["chunkId"], tid + ".meta")) or {}
    wtp, there = c["worktree"], os.path.isdir(c["worktree"])
    return True, {"taskId": tid, "state": c["state"], "client": c.get("client"), "chunkId": c["chunkId"], "worktree": wtp,
                  "branch": c["branch"], "commits": _commits(wtp, meta.get("fork")) if there else [],
                  "uncommitted": _wt_dirty(wtp) if there else [], "checks": _checks_info(c),
                  "runStatus": (rd(os.path.join(RUNS, c["chunkId"], tid + ".status")) or "").strip()}


def finish_task(tid, note):
    """Run the project's checks on a claimed task. Commits nothing. Green or none: the run is done and the page offers Merge.
    Red: the claim stays open with the failing step and log, so the session can fix and finish again."""
    c = claim_read(tid) if isinstance(tid, str) and ID_RE.fullmatch(tid) else None
    if not c:
        return False, "Task %s has no claim." % tid
    if c["state"] != "claimed":
        return False, "Task %s is %s, so it cannot be finished." % (tid, c["state"])
    wtp, ch = c["worktree"], c["chunkId"]
    if not os.path.isdir(wtp):
        return False, "The worktree %s is gone." % wtp
    dirty = _wt_dirty(wtp)
    if dirty:
        return False, "The worktree has uncommitted changes; commit them first (finish_task commits nothing):\n" + "\n".join(dirty[:30])
    d = os.path.join(RUNS, ch)
    v = run_checks(ch, tid)
    meta = _ap_read(os.path.join(d, tid + ".meta")) or {}
    commits = _commits(wtp, meta.get("fork"))
    if v["verdict"] in ("green", "none"):
        c.update(state="finished", finishedAt=int(time.time() * 1000), note=str(note or "")[:2000],
                 checks={"state": v["verdict"], "log": ""})
        _put(os.path.join(d, tid + ".diff"), "\n".join(commits[:10]) + "\n")
        _put(os.path.join(d, tid + ".patch"), git(wtp, "diff", meta.get("fork") or "HEAD")[1] + "\n")
        _put(os.path.join(d, tid + ".exit"), "0\n")
        _put(os.path.join(d, tid + ".status"), "done\n")
        _put(os.path.join(d, "_chunk.done"), "")
        claim_write(c)
        _live_best_effort()
        mcp_log("finish %s: checks %s" % (tid, v["verdict"]))
        return True, {"taskId": tid, "state": "finished", "checks": v["verdict"], "commits": commits,
                      "message": "Done. Merge it from the Merge button in Runway." + ("" if commits else " Note: the branch has no commits.")}
    log = _log_tail(os.path.join(d, tid + ".checks.log"), lines=60, chars=CHECKS_TAIL)
    c["checks"] = {"state": "red", "log": log}
    claim_write(c)
    _live_best_effort()
    mcp_log("finish %s: checks red at %s" % (tid, v.get("check")))
    return False, "Checks are red at step %r (%s); the task stays claimed. Fix it, commit, and call finish_task again.\n\n%s" % (
        v.get("check"), v.get("reason"), log)


def release_task(tid, reason):
    c = claim_read(tid) if isinstance(tid, str) and ID_RE.fullmatch(tid) else None
    if not c:
        return False, "Task %s has no claim." % tid
    if c["state"] != "claimed":
        return False, "Task %s is %s, so it cannot be released." % (tid, c["state"])
    d, wtp = os.path.join(RUNS, c["chunkId"]), c["worktree"]
    meta = _ap_read(os.path.join(d, tid + ".meta")) or {}
    kept = None
    if os.path.isdir(wtp):
        if _commits(wtp, meta.get("fork")):
            kept = "it has commits"
        else:
            rc, out = git(meta.get("repo") or wtp, "worktree", "remove", wtp)  # no --force: uncommitted work keeps it
            if rc:
                kept = "it has uncommitted changes" if _wt_dirty(wtp) else out[-200:]
            else:
                git(meta["repo"], "branch", "-D", c["branch"])
    c.update(state="released", finishedAt=int(time.time() * 1000), note=str(reason or "")[:2000])
    _put(os.path.join(d, tid + ".status"), "stopped\n")
    _put(os.path.join(d, "_chunk.stopped"), "")
    claim_write(c)
    _live_best_effort()
    mcp_log("release %s: %s" % (tid, reason))
    return True, {"taskId": tid, "state": "released", "worktree": "kept (%s): %s" % (kept, wtp) if kept else "removed"}


def list_ready_tasks(project_id=None):
    board = board_read()
    if board is None:
        return False, "Runway has not mirrored its board yet. Open the Runway page (it syncs on load), then try again."
    age = max(0, int(time.time() - board["at"] / 1000))
    note = "Board mirrored %s ago." % (("%d min" % (age // 60)) if age < 3600 else ("%.1f h" % (age / 3600)))
    if age > BOARD_STALE:
        note += " That is more than a day old: open the Runway page to refresh it before trusting this list."
    taken = {c["taskId"] for c in claims_all() if c.get("state") in ("claimed", "finished")}
    rows = [{k: t.get(k) for k in ("id", "projectId", "projectName", "title", "kind", "estTokens", "repo")} for t in board["tasks"]
            if (not project_id or t["projectId"] == project_id) and t["id"] not in taken and not _runway_running(t["id"])]
    return True, note + "\n" + json.dumps(rows, indent=1)


MCP_TOOLS = [
    {"name": "list_ready_tasks", "description": "List the tasks Runway has ready to start: id, project, title, kind, estimated tokens and repo.",
     "inputSchema": {"type": "object", "properties": {"projectId": {"type": "string", "description": "Only this project's tasks"}}}},
    {"name": "claim_task", "description": "Claim a ready task. Creates its git worktree and branch and returns the worktree path and the full brief; "
     "work there. Refuses a task that is already claimed or running.",
     "inputSchema": {"type": "object", "required": ["taskId", "client"], "properties": {
         "taskId": {"type": "string"}, "client": {"type": "string", "description": "Who is claiming, e.g. claude-code or codex"}}}},
    {"name": "task_status", "description": "A claimed task's state, its commits on the branch, uncommitted files and checks state.",
     "inputSchema": {"type": "object", "required": ["taskId"], "properties": {"taskId": {"type": "string"}}}},
    {"name": "finish_task", "description": "Finish a claimed task: commit your work first (this commits nothing), then it runs the project's checks. "
     "Green: the task is done and Runway offers Merge. Red: returns the failing step and log and the task stays claimed.",
     "inputSchema": {"type": "object", "required": ["taskId"], "properties": {"taskId": {"type": "string"}, "note": {"type": "string"}}}},
    {"name": "release_task", "description": "Give a claimed task back. The worktree is removed only if it has no commits.",
     "inputSchema": {"type": "object", "required": ["taskId"], "properties": {"taskId": {"type": "string"}, "reason": {"type": "string"}}}},
]


def mcp_call(name, a):
    """Run a tool; returns (is_error, text)."""
    a = a if isinstance(a, dict) else {}
    if name == "list_ready_tasks":
        ok, r = list_ready_tasks(a.get("projectId"))
    elif name == "claim_task":
        ok, r = claim_task(a.get("taskId"), a.get("client"))
    elif name == "task_status":
        ok, r = task_status(a.get("taskId"))
    elif name == "finish_task":
        ok, r = finish_task(a.get("taskId"), a.get("note"))
    elif name == "release_task":
        ok, r = release_task(a.get("taskId"), a.get("reason"))
    else:
        return True, "Unknown tool %s." % name
    return (not ok), (r if isinstance(r, str) else json.dumps(r, indent=1))


def mcp_handle(msg):
    """One JSON-RPC message in, the response dict out (None for a notification)."""
    if not isinstance(msg, dict) or "method" not in msg:
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in msg:
        return None  # notifications (notifications/initialized, cancelled...) get no reply
    mid, method, params = msg["id"], msg["method"], msg.get("params") or {}
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({"protocolVersion": want if want in MCP_PROTOCOLS else MCP_PROTOCOLS[0], "capabilities": {"tools": {}},
                   "serverInfo": {"name": "runway", "version": str(VERSION)},
                   "instructions": "Runway's task board: list_ready_tasks, claim_task, work in the returned worktree, commit, finish_task."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": MCP_TOOLS})
    if method == "tools/call":
        try:
            err, text = mcp_call(params.get("name"), params.get("arguments"))
        except Exception as e:
            mcp_log("tool %s crashed: %r" % (params.get("name"), e))
            err, text = True, "Runway hit an error: %s" % e
        return ok({"content": [{"type": "text", "text": text}], "isError": err})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}}


def mcp_serve(stdin=None, stdout=None):
    """The stdio MCP server: newline-delimited JSON-RPC on stdin and stdout, nothing else on stdout; logs go to helper.log."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    mcp_log("serve start (v%d)" % VERSION)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            try:
                resp = mcp_handle(msg)
            except Exception as e:
                mcp_log("handler crashed: %r" % e)
                resp = {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                        "error": {"code": -32603, "message": "Internal error"}}
        if resp is not None:
            stdout.write(json.dumps(resp, separators=(",", ":")) + "\n")
            stdout.flush()
    mcp_log("serve end")


def mcp_install_text():
    """The registration lines for Claude Code and Codex. Prints only; changes nothing."""
    return ("Claude Code (one command):\n"
            "  claude mcp add --scope user runway -- python3 ~/.ai-planner/runway.py mcp\n\n"
            "Codex: add this to ~/.codex/config.toml\n"
            "  [mcp_servers.runway]\n"
            '  command = "python3"\n'
            '  args = ["%s", "mcp"]\n' % os.path.join(BASE, "runway.py"))


# ---------- can-this-start check: one Haiku read of the brief against the repo, before a launch ----------
STARTCHECK_TYPES = ("foundation", "needs_you", "decision", "environment")
STARTCHECK_PROMPT = """You are the pre-launch check for an unattended coding agent. The agent gets ONLY the repository described below and package registries (no credentials, no accounts, no human to ask). Read the task brief and decide whether it can finish the brief.

List a problem only when you are fairly sure of it. Problem types:
- foundation: code or a feature the brief builds on that the repo map shows does not exist and that no dependency task provides.
- needs_you: credentials, outside accounts, purchases, deploys, or physical/human steps.
- decision: a choice the brief leaves open that would change the result.
- environment: a tool, service or setup the agent cannot get from this repo and package registries.
A brief that creates new files or builds new code from scratch is fine. Do not list style nits or things the agent can reasonably decide.

Answer with ONLY this JSON, nothing else:
{"ok": true|false, "missing": [{"type": "foundation|needs_you|decision|environment", "detail": "one sentence", "needs": "what would fix it"}]}
ok is false exactly when missing is not empty.

"""


def _brief_paths(text):
    """Path-like tokens a brief names (src/app.py, helper/runway.py, README.md), in order, without duplicates."""
    seen, out = set(), []
    for m in re.finditer(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z]\w{0,5})(?![\w/-])", text or ""):
        p = m.group(1).lstrip("./")
        if p and p not in seen and not p.startswith(("http", "www.")):
            seen.add(p)
            out.append(p)
    return out[:60]


def startcheck_head(repo, base):
    """The ref and commit the check is cached against: the task's base branch when it exists, else the checkout's HEAD."""
    for ref in ([base] if base else []) + ["HEAD"]:
        rc, o = git(repo, "rev-parse", "-q", "--verify", ref + "^{commit}")
        if rc == 0 and o:
            return ref, o.strip()
    return "HEAD", ""


def repo_map(repo, base, brief):
    """A compact picture of the repo for the check: the first 300 tracked paths, the README's head, and which of the
    files the brief names exist or don't. Returns (text, head sha)."""
    ref, head = startcheck_head(repo, base)
    files = []
    if head:
        rc, o = git(repo, "ls-tree", "-r", "--name-only", ref)
        files = o.splitlines() if rc == 0 else []
    have = set(files)
    readme = ""
    for f in files:
        if re.fullmatch(r"README(\.\w+)?", f, re.I):
            rc, o = git(repo, "show", "%s:%s" % (ref, f))
            readme = "\n".join(o.splitlines()[:40])[:1500] if rc == 0 else ""
            break
    named = _brief_paths(brief)
    exist = [p for p in named if p in have or any(x.endswith("/" + p) for x in files)]
    missing = [p for p in named if p not in exist]
    text = "REPO FILES (%d of %d tracked, at %s):\n%s\n" % (min(len(files), 300), len(files), (head or "no commits")[:10], "\n".join(files[:300]))
    if readme:
        text += "\nREADME (head):\n%s\n" % readme
    if exist:
        text += "\nFILES THE BRIEF NAMES THAT EXIST: %s\n" % ", ".join(exist[:15])
    if missing:
        text += "\nFILES THE BRIEF NAMES THAT DO NOT EXIST YET: %s\n" % ", ".join(missing[:15])
    return text, head


def startcheck_prompt(task, rmap):
    dep_text = "\n".join("- %s [%s]" % (d.get("title", ""), d.get("status", "")) if isinstance(d, dict) else "- %s" % d
                         for d in task.get("deps") or []) or "none"
    return (STARTCHECK_PROMPT + "TASK: %s\n\nDETAIL:\n%s\n\nACCEPTANCE:\n%s\n\nDEPENDENCY TASKS (they land before this one starts, so what they build counts as existing):\n%s\n\n%s"
            % (task.get("title", ""), (task.get("detail") or "")[:4000], (task.get("acceptance") or "")[:2000], dep_text, rmap))


def startcheck_key(task, head):
    brief = json.dumps([task.get("title", ""), task.get("detail", ""), task.get("acceptance", ""), task.get("deps") or []], sort_keys=True)
    return hashlib.sha256(("%s|%s|%s" % (task.get("id", ""), hashlib.sha256(brief.encode()).hexdigest(), head)).encode()).hexdigest()[:24]


def startcheck_args(claude, prompt):
    return [claude, "-p", prompt, "--model", "haiku", "--output-format", "json", "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']


def startcheck_parse(text):
    """The {ok, missing} object in the model's reply, normalised; None when there isn't a usable one."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("missing", []), list) or not isinstance(d.get("ok", True), bool):
        return None
    missing = [{"type": x["type"], "detail": str(x["detail"]).strip()[:400], "needs": str(x.get("needs") or "").strip()[:300]}
               for x in d.get("missing", []) if isinstance(x, dict) and x.get("type") in STARTCHECK_TYPES and str(x.get("detail") or "").strip()]
    return {"ok": not missing, "missing": missing}


def _startcheck_log(rec):
    try:
        os.makedirs(os.path.join(BASE, "startcheck"), exist_ok=True)
        with open(os.path.join(BASE, "startcheck", "log.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def start_check_one(task, runner=subprocess.run, claude=None):
    """One task: cached by (task id, brief, repo HEAD), else one Haiku call. A broken or unparseable check is ok with
    a warning, and is never cached: a check must not be able to stop a launch."""
    tid = task.get("id", "")
    try:
        brief = "\n".join([task.get("title", ""), task.get("detail", ""), task.get("acceptance", "")])
        rmap, head = repo_map(os.path.expanduser(task.get("repo") or ""), task.get("base") or "", brief)
        path = os.path.join(BASE, "startcheck", startcheck_key(task, head) + ".json")
        try:
            c = json.loads(rd(path) or "null")
        except json.JSONDecodeError:
            c = None
        if isinstance(c, dict) and isinstance(c.get("ok"), bool):
            return {"id": tid, "ok": c["ok"], "missing": c.get("missing", []), "cached": True}
        env, _ = agent_env()
        r = runner(startcheck_args(claude or CLAUDE, startcheck_prompt(task, rmap)), capture_output=True, text=True, timeout=90, env=env,
                   cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
        out = json.loads((r.stdout or "").strip().splitlines()[-1]) if (r.stdout or "").strip() else {}
        if r.returncode != 0 or out.get("is_error"):
            raise ValueError(out.get("result") or (r.stderr or "").strip()[-200:] or "exit %d" % r.returncode)
        u, cost = out.get("usage") or {}, float(out.get("total_cost_usd") or 0)
        res = startcheck_parse(out.get("result"))
        _startcheck_log({"at": time.time(), "task": tid, "costUsd": cost, "parsed": res is not None,
                         "tokens": {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
                                    "cacheRead": u.get("cache_read_input_tokens", 0), "cacheWrite": u.get("cache_creation_input_tokens", 0)}})
        if res is None:
            return {"id": tid, "ok": True, "missing": [], "warning": "The start check's answer wasn't usable, so it was skipped.", "costUsd": cost}
        write_json_atomic(path, {**res, "at": time.time(), "task": tid})
        return {"id": tid, "ok": res["ok"], "missing": res["missing"], "cached": False, "costUsd": cost}
    except Exception as e:  # never block a launch on a broken check
        return {"id": tid, "ok": True, "missing": [], "warning": "The start check couldn't run (%s), so it was skipped." % str(e)[:160]}


def start_check(b64, runner=subprocess.run, claude=None):
    """start-check <b64 spec>: spec is a list of {id, title, detail, acceptance, deps:[{title,status}], repo, base}.
    Returns @@STARTCHECK [{id, ok, missing:[{type, detail, needs}], cached | warning}]."""
    try:
        spec = json.loads(base64.b64decode(b64).decode())
        assert isinstance(spec, list)
    except Exception:
        return "@@ERR usage: start-check <base64 JSON list of tasks>"
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(lambda t: start_check_one(t, runner, claude), [t for t in spec if isinstance(t, dict)]))
    return "@@STARTCHECK " + json.dumps(res, separators=(",", ":"))


# ---------- AI lead: routine mid-run decisions, inside the escalation line ----------
# Austin's line (2026-10-19). The model classifies; this guard only ADDS escalation, never removes it. Each entry is a
# word stem matched at a word start (so "pric" catches price, pricing, priced).
LEAD_LINE = {
    "money": ["pric", "purchas", "paid", "payment", "billing", "budget", "subscription", "invoice", "refund", "stripe", "spend", "usd", "dollar", "checkout"],
    "client": ["client", "customer", "announce", "testimonial", "newsletter"],
    "external": ["account", "credential", "secret", "api key", "api token", "access token", "oauth", "password", "deploy", "dns", "domain", "publish",
                 "e-mail", "email", "webhook", "production", "vercel", "third-party", "external api", "send a message", "post to"],
    "brand": ["brand", "logo", "tagline", "slogan", "voice", "visual identity", "public copy", "marketing", "landing page", "product name", "company name", "app name"],
    "irreversible": ["delet", "drop table", "drop the", "truncat", "migrat", "force push", "force-push", "history rewrite", "rewrite history", "rebase",
                     "filter-branch", "irreversib", "destroy", "wipe", "purge", "rm -rf", "overwrite", "reset --hard", "real data", "production data"],
}
LEAD_MIN_CONFIDENCE = 0.6
LEAD_BUDGET_USD = "0.25"


def lead_guard(text):
    """The escalation categories whose keywords appear in text; [] when none do."""
    low = (text or "").lower()
    return [cat for cat, kws in LEAD_LINE.items()
            if any(re.search((r"\b" if kw[0].isalnum() else "") + re.escape(kw), low) for kw in kws)]


LEAD_PROMPT = """You are the AI lead for a solo founder's unattended coding agents. An agent stopped on a decision it could not make. You decide routine ones so the founder is not interrupted. You cannot use tools; answer from what is below.

Step 1, classify. The decision must go to the founder (escalate: true) when it touches:
- money: prices, spend, purchases, paid tiers, budgets
- clients: anything a client sees or is told, client data
- anything outside the repo: accounts, credentials, deploys, DNS, publishing, email, external APIs with side effects
- the brand: names, voice, visual identity, public copy
- irreversible actions: deletions, migrations on real data, history rewrites
Step 2, if it is routine (naming inside the code, structure, libraries, file formats, defaults, test approach), choose the option that best serves the project goal and follows the project rules and the decisions already logged. If the agent gave options, pick one of them verbatim unless none fits; if it gave none, state the call in one sentence.
Give confidence 0 to 1 that the founder would agree. If you are unsure, say so with a low confidence.

Answer with ONLY this JSON, nothing else:
{"category": "money|client|external|brand|irreversible|none", "decision": "the call", "why": "one or two sentences", "confidence": 0.0, "escalate": true|false}

"""


def lead_prompt(spec):
    p, pi, t, b = (spec.get(k) or {} for k in ("project", "pi", "task", "blocker"))
    log = "\n".join("- %s → %s" % (str(d.get("question", ""))[:200], str(d.get("answer", ""))[:300]) for d in (spec.get("log") or [])[-40:]) or "(none)"
    opts = "\n".join("- %s" % str(o)[:300] for o in (b.get("options") or [])) or "(none given)"
    return (LEAD_PROMPT + "PROJECT GOAL: %s\n\nRULES FOR AGENTS:\n%s\n\nPROGRAM INCREMENT: %s\n\nTASK: %s\n\nBRIEF:\n%s\n\nACCEPTANCE:\n%s\n\nTHE AGENT'S REPORT:\n%s\n\n"
            "THE DECISION IT NEEDS: %s\nOPTIONS:\n%s\n\nDECISION LOG:\n%s\n"
            % (str(p.get("goal", ""))[:600], str(p.get("agentNotes", ""))[:2500], ("%s — %s" % (pi.get("name", ""), pi.get("objective", "")))[:800],
               str(t.get("title", ""))[:200], str(t.get("detail", ""))[:3000], str(t.get("acceptance", ""))[:1500], str(spec.get("report", ""))[-2500:],
               str(b.get("detail", ""))[:800], opts, log))


def lead_args(claude, prompt):
    return [claude, "-p", prompt, "--model", "sonnet", "--output-format", "json", "--tools", "", "--strict-mcp-config", "--mcp-config",
            '{"mcpServers":{}}', "--max-budget-usd", LEAD_BUDGET_USD]


def lead_parse(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not str(d.get("decision") or "").strip():
        return None
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence"))))
    except (TypeError, ValueError):
        conf = 0.0
    esc = d.get("escalate")
    return {"decision": str(d["decision"]).strip()[:600], "why": str(d.get("why") or "").strip()[:600], "confidence": conf,
            "category": str(d.get("category") or "none").strip().lower(), "escalate": not (esc is False or esc == "false")}


def lead_verdict(spec, res):
    """The final call: escalate when the model said so, when its confidence is under the bar, or when the keyword guard
    finds a category in the blocker, its options or the model's own answer. The guard can only add escalation."""
    b = spec.get("blocker") or {}
    cats = lead_guard(" ".join([str(b.get("detail", "")), " ".join(str(o) for o in b.get("options") or []), res["decision"], res["why"]]))
    model_cat = res["category"] if res["category"] in LEAD_LINE else ""
    why = []
    if res["escalate"] or model_cat:
        why.append("the AI lead judged it outside the line%s" % (" (%s)" % model_cat if model_cat else ""))
    if res["confidence"] < LEAD_MIN_CONFIDENCE:
        why.append("confidence %.2f is under %.1f" % (res["confidence"], LEAD_MIN_CONFIDENCE))
    if cats:
        why.append("it mentions %s" % ", ".join(cats))
    return {**res, "escalate": bool(why), "categories": sorted(set(cats) | ({model_cat} if model_cat else set())), "reason": "; ".join(why)}


def _lead_log(rec):
    try:
        os.makedirs(os.path.join(BASE, "lead"), exist_ok=True)
        with open(os.path.join(BASE, "lead", "log.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def lead_spend(now=None):
    """What the AI lead spent this calendar month: {month, usd, calls}."""
    month = time.strftime("%Y-%m", time.localtime(now or time.time()))
    usd, calls = 0.0, 0
    for line in (rd(os.path.join(BASE, "lead", "log.jsonl")) or "").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if time.strftime("%Y-%m", time.localtime(r.get("at") or 0)) == month:
            usd += float(r.get("costUsd") or 0)
            calls += 1
    return {"month": month, "usd": round(usd, 4), "calls": calls}


def lead_info():
    return {**lead_spend(), "line": LEAD_LINE, "minConfidence": LEAD_MIN_CONFIDENCE, "budgetUsd": float(LEAD_BUDGET_USD)}


def lead_decide(spec, runner=subprocess.run, claude=None):
    """lead <b64 spec>: ONE claude -p call (Sonnet, no tools, --max-budget-usd 0.25). spec: {project:{goal,agentNotes},
    pi:{name,objective}, task:{id,title,detail,acceptance}, report, blocker:{detail,options}, log:[{question,answer}]}.
    Returns {decision, why, confidence, escalate, categories, reason, costUsd}. A call that fails or can't be read
    escalates: the lead never guesses."""
    try:
        env, _ = agent_env()
        r = runner(lead_args(claude or CLAUDE, lead_prompt(spec)), capture_output=True, text=True, timeout=180, env=env,
                   cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
        out = json.loads((r.stdout or "").strip().splitlines()[-1]) if (r.stdout or "").strip() else {}
        cost = float(out.get("total_cost_usd") or 0)
        if cost:
            _lead_log({"at": time.time(), "task": (spec.get("task") or {}).get("id", ""), "costUsd": cost})
        if r.returncode != 0 or out.get("is_error"):
            raise ValueError(out.get("result") or (r.stderr or "").strip()[-200:] or "exit %d" % r.returncode)
        res = lead_parse(out.get("result"))
        if res is None:
            raise ValueError("the answer wasn't usable")
        return {**lead_verdict(spec, res), "costUsd": cost}
    except Exception as e:
        return {"decision": "", "why": "", "confidence": 0.0, "escalate": True, "categories": [], "costUsd": 0,
                "reason": "the AI lead couldn't decide (%s)" % str(e)[:160]}


def main(argv):
    cmd = argv[0] if argv else "tick"
    if cmd in TRAIN_CMDS:
        sys.exit(train_cli(argv))
    if cmd == "autopilot":
        sys.exit(autopilot_cli(argv))
    if cmd == "roadmap":
        sys.exit(roadmap_cli(argv))
    if cmd == "tick":
        tick()
    elif cmd == "status":
        print("@@BEGIN" + json.dumps(tick(argv[1:]), separators=(",", ":")) + "@@END")
    elif cmd == "scrub":  # scrub CHUNK TASK PATH...: remove untracked files an agent left in its worktree
        print(scrub(argv[1], argv[2], argv[3:]))
    elif cmd == "live":
        live_write()
        print("@@LIVE")
    elif cmd == "launch":
        print("@@" + launch(argv[1]).upper().replace(" ", "_") + " " + argv[1])
    elif cmd == "queue":
        os.makedirs(QUEUE, exist_ok=True)
        with open(os.path.join(QUEUE, argv[1]), "w") as f:
            f.write(str(int(float(argv[2]))))
        tick()
        print("@@QUEUED " + argv[1])
    elif cmd == "unqueue":
        try:
            os.remove(os.path.join(QUEUE, argv[1]))
        except FileNotFoundError:
            pass
        print("@@UNQUEUED " + argv[1])
    elif cmd == "stop":
        print("@@" + stop(argv[1]).upper() + " " + argv[1])
    elif cmd == "env-gc":
        print("@@ENVGC " + json.dumps(env_gc(log=print)))
    elif cmd == "merge":
        print(merge(argv[1], argv[2]))
    elif cmd == "merge-batch":
        print(merge_batch_start(argv[1], argv[2]))
    elif cmd == "merge-batch-run":
        merge_batch_run(argv[1])
    elif cmd == "commit-wip":
        print(commit_wip(checkout_for(argv[1], argv[2]) or argv[1], argv[2], argv[3]))
    elif cmd == "resolve-checkout":
        print(resolve_checkout(checkout_for(argv[1], argv[2]) or argv[1], argv[2]))
    elif cmd == "set-aside":
        print(set_aside(checkout_for(argv[1], argv[2]) or argv[1], argv[2], argv[3]))
    elif cmd == "verify-input":
        print(verify_input(argv[1], argv[2]))
    elif cmd == "verdict":
        print(verify_verdict(argv[1], argv[2]))
    elif cmd == "checks":
        v = run_checks(argv[1], argv[2])
        print("@@CHECKS %s %s %s" % (v["verdict"], argv[1], argv[2]))
    elif cmd == "budget":  # budget RUN [CAP|-] [EST_EFF_TOKENS] [MODEL]
        cap = None if len(argv) < 3 or argv[2] == "-" else float(argv[2])
        print(json.dumps(budget_report(argv[1], cap, int(argv[3]) if len(argv) > 3 else 0, argv[4] if len(argv) > 4 else None), separators=(",", ":")))
    elif cmd == "budget-record":  # budget-record RUN CH TID MODEL [EST_EFF_TOKENS [KIND]]
        print(json.dumps(budget_record_task(argv[1], argv[2], argv[3], argv[4], int(argv[5]) if len(argv) > 5 else 0,
                                            argv[6] if len(argv) > 6 else ""), separators=(",", ":")))
    elif cmd == "calibration":  # calibration [--json]: the per model and kind multipliers over every run on this Mac
        rep = calibration_report()
        print(json.dumps(rep, separators=(",", ":")) if "--json" in argv else calibration_text(rep))
    elif cmd == "fix-prompt":
        print(fix_prompt(argv[1], argv[2], int(argv[3])))
    elif cmd == "checks-fix-prompt":
        print(checks_fix_prompt(argv[1], argv[2], int(argv[3])))
    elif cmd == "checks-fix-gate":
        print(checks_fix_gate(argv[1], argv[2], int(argv[3]), int(argv[4])))
    elif cmd == "fix-gate":
        print(fix_round_gate(argv[1], argv[2], int(argv[3]), int(argv[4])))
    elif cmd == "land":
        ok, msg = land_with_stash(argv[1], argv[2]) if "--stash" in argv[3:] else land(argv[1], argv[2], confirm="--confirm" in argv[3:])
        if ok is None:
            bid = "ml" + format(int(time.time() * 1000), "x")
            spec = {"items": [], "lands": [{"repo": argv[1], "base": argv[2]}], "kind": "land", "note": msg}
            merge_batch_start(bid, base64.b64encode(json.dumps(spec).encode()).decode())
            print("@@RESOLVING %s %s Claude is combining them in the background; this takes a minute or two." % (bid, msg))
        else:
            print(("@@OK " if ok else "@@ERR ") + msg)
    elif cmd == "preflight":
        print(preflight(argv[1]))
    elif cmd == "blocker-check":
        print(blocker_check(argv[1]))
    elif cmd == "needs-check":
        print(needs_check(argv[1]))
    elif cmd == "run-fix":
        print(run_fix(argv[1]))
    elif cmd == "land-undo":
        ok, msg = land_undo(argv[1], argv[2])
        print(("@@OK " if ok else "@@ERR ") + msg)
    elif cmd == "discard":
        print(discard(argv[1], argv[2]))
    elif cmd == "automerge":
        r = automerge(argv[1], argv[2])
        print(("@@OK " if r["outcome"] == "merged" else "@@ERR ") + r["msg"])
    elif cmd == "export":
        print(export(argv[1]))
    elif cmd == "notify":
        sub = argv[1] if len(argv) > 1 else ""
        if sub in ("on", "off"):
            _set_flag(NOTIFY_OFF, sub == "off")
            print("@@NOTIFY_" + sub.upper())
        elif sub == "handsoff" and len(argv) > 2 and argv[2] in ("on", "off"):
            _set_flag(os.path.join(BASE, "notify.handsoff"), argv[2] == "on")
            print("@@NOTIFY_HANDSOFF_" + argv[2].upper())
        elif sub == "routine" and len(argv) > 2 and argv[2] in ("on", "off"):
            _set_flag(NOTIFY_ROUTINE, argv[2] == "on")
            print("@@NOTIFY_ROUTINE_" + argv[2].upper())
        elif sub == "--dry-run":
            # read-only: no launches, no status write, nothing posted or recorded
            lines = preview({"chunks": all_status(), "merges": merges_status()})
            print("\n".join(lines) or "No events.")
            print("@@DRYRUN %d events, none posted" % len(lines))
        else:
            print("@@ERR usage: notify on|off|routine on|off|handsoff on|off|--dry-run")
    elif cmd == "run-history":  # run-history [DAYS [OFFSET [LIMIT]]]: finished runs' records, newest first, a page at a time
        days = float(argv[1]) if len(argv) > 1 else 90
        off = int(argv[2]) if len(argv) > 2 else 0
        lim = int(argv[3]) if len(argv) > 3 else 1000
        h = run_history(time.time() - days * 86400)
        print("@@HIST " + json.dumps({"total": len(h), "offset": off, "runs": h[off:off + lim]}, separators=(",", ":")))
    elif cmd == "merge-log":
        try:
            n = int(argv[1]) if len(argv) > 1 else 10
        except ValueError:
            sys.exit("@@ERR usage: merge-log [N]")
        print(format_failures(read_failures(n=n)))
    elif cmd == "env-info":
        try:
            print("@@ENVINFO " + json.dumps(env_info(argv[1]), separators=(",", ":")))
        except ConfigError as e:
            print("@@ERR " + str(e))
    elif cmd == "retry-setup":
        try:
            print("@@SETUP_RETRY " + retry_setup(argv[1], argv[2]) + " " + argv[2])
        except (AutopilotError, ConfigError) as e:
            print("@@ERR " + str(e))
    elif cmd == "config-get":
        try:
            print("@@CONFIG " + json.dumps(config_get(argv[1], argv[2])))
        except ConfigError as e:
            print("@@ERR " + str(e))
    elif cmd == "config-set":
        try:
            config_set(argv[1], argv[2], argv[3])
            print("@@CONFIG " + json.dumps(config_get(argv[1], argv[2])))
        except ConfigError as e:
            print("@@ERR " + str(e))
    elif cmd == "auth-check":
        print("@@AUTH " + json.dumps({k: v for k, v in auth_check().items() if k != "at"}))
    elif cmd == "start-check":
        print(start_check(argv[1]) if len(argv) > 1 else "@@ERR usage: start-check <base64 spec>")
    elif cmd == "lead":
        try:
            spec = json.loads(base64.b64decode(argv[1]).decode())
            assert isinstance(spec, dict)
        except Exception:
            print("@@ERR usage: lead <base64 JSON spec>")
        else:
            print("@@LEAD " + json.dumps(lead_decide(spec), separators=(",", ":")))
    elif cmd == "lead-info":
        print("@@LEADINFO " + json.dumps(lead_info(), separators=(",", ":")))
    elif cmd == "board-sync":
        ok, msg = board_sync(argv[1] if len(argv) > 1 else "")
        print(("@@BOARD " if ok else "@@ERR ") + msg)
        if not ok:
            sys.exit(1)
    elif cmd == "mcp":
        mcp_serve()
    elif cmd == "mcp-install":
        print(mcp_install_text())
    elif cmd == "version":
        print("@@VERSION %d" % VERSION)


if __name__ == "__main__":
    main(sys.argv[1:])
