#!/usr/bin/env python3
"""Runway harness: the built page, a sandboxed Desktop Commander and an in-memory database, on localhost.

    python3 harness/serve.py [--port 8765] [--sandbox DIR] [--seed FILE] [--keep]

It builds nothing: run `python3 build.py` first. It then
  - makes a sandbox HOME (a temp folder unless --sandbox) holding .ai-planner/runway.py copied from this checkout's
    helper/runway.py, a scratch git repo (site/) with checks in .runway.json, and a fake `claude` (harness/fake_claude.py);
  - serves dist/runway.html at http://127.0.0.1:PORT/ with harness/shim.js injected before the page's scripts;
  - answers the page's start_process with the command run under HOME=<sandbox>, RUNWAY_CLAUDE=<fake claude> and the
    fake first on PATH, and read_file only for paths inside the sandbox;
  - keeps the page's database in <sandbox>/harness-db.json, seeded from --seed (default harness/seed.json) with
    {{HOME}} and {{REPO}} filled in;
  - runs `runway.py tick` every few seconds, as launchd does on the real Mac.

Nothing here reads or writes the real ~/.ai-planner, LaunchAgents or launchctl, and no real agent can start: the
helper's claude is the fake. Ctrl-C stops it; the sandbox is removed unless --keep or --sandbox was given.
Prints one line, `HARNESS <url> <sandbox>`, once it is ready.
"""
import argparse, json, os, shutil, signal, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "harness")
REAL_PLANNER = os.path.realpath(os.path.expanduser("~/.ai-planner"))


def sh(cmd, cwd, env):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def make_sandbox(home):
    os.makedirs(os.path.join(home, ".ai-planner"), exist_ok=True)
    os.makedirs(os.path.join(home, "bin"), exist_ok=True)
    if os.path.realpath(os.path.join(home, ".ai-planner")) == REAL_PLANNER:
        sys.exit("harness: the sandbox resolves to the real ~/.ai-planner; pick another --sandbox")
    shutil.copy(os.path.join(ROOT, "helper", "runway.py"), os.path.join(home, ".ai-planner", "runway.py"))
    fake = os.path.join(home, "bin", "claude")
    shutil.copy(os.path.join(HERE, "fake_claude.py"), fake)
    os.chmod(fake, 0o755)
    repo = os.path.join(home, "site")
    if not os.path.isdir(os.path.join(repo, ".git")):
        os.makedirs(repo, exist_ok=True)
        env = dict(os.environ, HOME=home, GIT_AUTHOR_NAME="Harness", GIT_AUTHOR_EMAIL="harness@local",
                   GIT_COMMITTER_NAME="Harness", GIT_COMMITTER_EMAIL="harness@local")
        with open(os.path.join(repo, "README.md"), "w") as f:
            f.write("Harness scratch project.\n")
        with open(os.path.join(repo, ".runway.json"), "w") as f:
            json.dump({"checks": [{"name": "tests", "cmd": "test ! -e broken.txt"}]}, f)
        for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "init"]):
            sh(c, repo, env)
    return fake, repo


def agent_env(home, fake):
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("ANTHROPIC_") or k.startswith("CLAUDE_CODE"):
            del env[k]  # no credentials reach anything started from the sandbox
    env.update(HOME=home, RUNWAY_CLAUDE=fake, RUNWAY_NO_KEYCHAIN="1", PATH=os.path.dirname(fake) + ":" + env.get("PATH", ""),
               GIT_AUTHOR_NAME="Harness", GIT_AUTHOR_EMAIL="harness@local", GIT_COMMITTER_NAME="Harness", GIT_COMMITTER_EMAIL="harness@local")
    return env


def inside(home, path):
    p = os.path.realpath(os.path.expanduser(path.replace("$HOME", home)))
    h = os.path.realpath(home)
    return p if (p == h or p.startswith(h + os.sep)) else None


def main():
    ap = argparse.ArgumentParser(description="Serve the built Runway page against a sandbox.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--sandbox", help="use (and keep) this folder as the sandbox HOME")
    ap.add_argument("--seed", default=os.path.join(HERE, "seed.json"))
    ap.add_argument("--keep", action="store_true", help="keep the temp sandbox on exit")
    ap.add_argument("--tick", type=float, default=5, help="seconds between helper ticks (0 = none)")
    a = ap.parse_args()

    page_path = os.path.join(ROOT, "dist", "runway.html")
    if not os.path.exists(page_path):
        sys.exit("harness: dist/runway.html is missing; run python3 build.py first")
    home = os.path.abspath(a.sandbox) if a.sandbox else tempfile.mkdtemp(prefix="runway-harness-")
    fake, repo = make_sandbox(home)
    env = agent_env(home, fake)
    dbfile = os.path.join(home, "harness-db.json")
    if not os.path.exists(dbfile):
        seed = open(a.seed).read().replace("{{HOME}}", home).replace("{{REPO}}", repo).replace("{{CLAUDE}}", fake)
        with open(dbfile, "w") as f:
            f.write(json.dumps(json.loads(seed)))
    page = open(page_path).read()
    shim = "<script>" + open(os.path.join(HERE, "shim.js")).read() + "</script>"
    i = page.find("<script")
    page = (page[:i] + shim + page[i:]) if i >= 0 else page + shim
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, ctype="application/json"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path in ("/", "/index.html", "/runway.html"):
                return self.reply(200, page, "text/html; charset=utf-8")
            if self.path == "/__db":
                with lock:
                    return self.reply(200, open(dbfile).read())
            if self.path == "/__sandbox":
                return self.reply(200, json.dumps({"home": home, "repo": repo, "claude": fake}))
            self.reply(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            if self.path == "/__db":
                json.loads(body)  # refuse anything that is not JSON
                with lock, open(dbfile + ".tmp", "w") as f:
                    f.write(body)
                os.replace(dbfile + ".tmp", dbfile)
                return self.reply(200, "{}")
            if self.path != "/__mcp":
                return self.reply(404, json.dumps({"error": "not found"}))
            req = json.loads(body or "{}")
            tool, inp = req.get("tool"), req.get("input") or {}
            if tool == "start_process":
                timeout = max(1, min(600, (inp.get("timeout_ms") or 20000) / 1000))
                try:
                    p = subprocess.run(["/bin/bash", "-c", inp.get("command") or ""], cwd=home, env=env,
                                       capture_output=True, text=True, timeout=timeout)
                    text = p.stdout + (p.stderr and ("\n" + p.stderr) or "")
                except subprocess.TimeoutExpired as e:
                    text = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
                return self.reply(200, json.dumps({"text": text}))
            if tool == "read_file":
                path = inside(home, inp.get("path") or "")
                if not path:
                    return self.reply(403, json.dumps({"error": "read_file outside the sandbox is refused: %s" % inp.get("path")}))
                try:
                    with open(path, errors="replace") as f:
                        text = f.read()
                except OSError as e:
                    return self.reply(404, json.dumps({"error": str(e)}))
                off, length = inp.get("offset"), inp.get("length")
                lines = text.split("\n")
                if isinstance(off, int) and off < 0:
                    lines = lines[off:]
                elif isinstance(off, int):
                    lines = lines[off:]
                if isinstance(length, int):
                    lines = lines[:length]
                return self.reply(200, json.dumps({"text": "\n".join(lines)}))
            self.reply(400, json.dumps({"error": "the harness only serves start_process and read_file"}))

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    stop = threading.Event()

    def ticker():
        while a.tick and not stop.wait(a.tick):
            sh([sys.executable, os.path.join(home, ".ai-planner", "runway.py"), "tick"], home, env)
    threading.Thread(target=ticker, daemon=True).start()
    sh([sys.executable, os.path.join(home, ".ai-planner", "runway.py"), "tick"], home, env)

    def bye(*_):
        stop.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    print("HARNESS http://127.0.0.1:%d/ %s" % (srv.server_address[1], home), flush=True)
    try:
        srv.serve_forever()
    finally:
        if not a.sandbox and not a.keep:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
