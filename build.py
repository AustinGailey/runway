#!/usr/bin/env python3
"""Build dist/runway.html from src/runway.src.html, embedding helper/runway.py, then check it.

    python3 build.py          build + checks
    python3 build.py --render build + checks + headless render check (needs Chrome; set RUNWAY_CHROME to override)
Checks: the helper compiles, the page's script parses (node), and HELPER_V matches the helper's VERSION.
The render check loads the built page in headless Chrome with a stub window.claude, fails if the page throws
or #content renders empty, and saves dist/render-check.png for review.
"""
import base64, html, json, os, py_compile, re, shutil, signal, subprocess, sys, tempfile, time
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
helper_path = os.path.join(ROOT, "helper", "runway.py")
helper = open(helper_path, "rb").read()

py_compile.compile(helper_path, doraise=True)
hv = re.search(rb"^VERSION = (\d+)", helper, re.M)
pv = re.search(r"const HELPER_V = (\d+);", src)
if not hv or not pv or int(hv.group(1)) != int(pv.group(1)):
    sys.exit("build: HELPER_V in the page (%s) must equal VERSION in helper/runway.py (%s)" % (pv and pv.group(1), hv and hv.group(1).decode()))
if src.count("__HELPER_B64__") != 1:
    sys.exit("build: expected exactly one __HELPER_B64__ placeholder in the page")

out = src.replace("__HELPER_B64__", base64.b64encode(helper).decode())
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
dist = os.path.join(ROOT, "dist", "runway.html")
tmp = "%s.%d.tmp" % (dist, os.getpid())  # write then rename: a page being served is never half-written
open(tmp, "w").write(out)
os.replace(tmp, dist)

js = out[out.index("<script>\n") + 9: out.rindex("</script>")]
check = subprocess.run(["node", "-e", "try{new Function(require('fs').readFileSync(0,'utf8'))}catch(e){console.error(e.message);process.exit(1)}"],
                       input=js, capture_output=True, text=True)
if check.returncode:
    sys.exit("build: page script does not parse: " + check.stderr.strip())
print("built %s (%d KB), helper v%s" % (os.path.relpath(dist, ROOT), len(out) // 1024, hv.group(1).decode()))

# Injected before the page's own scripts: a stub capability and a hook that records boot-time throws.
RENDER_STUB = """<script>
window.__renderErrors = [];
window.claude = { use: async () => null };
const __report = msg => { window.__renderErrors.push(msg); document.documentElement.dataset.renderErrors = JSON.stringify(window.__renderErrors); };
window.addEventListener("error", e => __report(String(e.error && e.error.stack || e.message)));
window.addEventListener("unhandledrejection", e => __report(String(e.reason && e.reason.stack || e.reason)));
</script>
"""
CHROME_CANDIDATES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "chromium", "chromium-browser", "google-chrome", "chrome"]

def find_chrome():
    if os.environ.get("RUNWAY_CHROME"):
        return os.environ["RUNWAY_CHROME"]
    for c in CHROME_CANDIDATES:
        if os.path.isabs(c) and os.path.exists(c):
            return c
        if not os.path.isabs(c) and shutil.which(c):
            return shutil.which(c)
    sys.exit("build --render: no Chrome found (set RUNWAY_CHROME to a Chrome or Chromium binary)")

def render_check(out_html):
    chrome = find_chrome()
    shot = os.path.join(ROOT, "dist", "render-check.png")
    with tempfile.TemporaryDirectory(prefix="runway-render-") as tmp:
        page = os.path.join(tmp, "render.html")
        open(page, "w").write(RENDER_STUB + out_html)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
               "--user-data-dir=" + os.path.join(tmp, "profile"), "--window-size=1280,900",
               "--host-resolver-rules=MAP * ~NOTFOUND",  # the page makes no network calls; fail fast if it tries
               "--virtual-time-budget=4000", "--screenshot=" + shot, "--dump-dom", "file://" + page]
        dump, errlog = os.path.join(tmp, "dom.html"), os.path.join(tmp, "stderr.txt")
        if os.path.exists(shot):
            os.remove(shot)
        # Chrome does not exit after --screenshot in headless=new, so wait for both outputs and then stop it.
        with open(dump, "w") as out_f, open(errlog, "w") as err_f:
            proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, start_new_session=True)
            deadline = time.time() + 90
            while time.time() < deadline and proc.poll() is None:
                if os.path.exists(shot) and os.path.getsize(shot) and open(dump).read().rstrip().endswith("</html>"):
                    break
                time.sleep(0.2)
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        dom, err = open(dump).read(), open(errlog).read()
    if "<main" not in dom:
        sys.exit("build --render: headless Chrome produced no DOM: %s" % (err.strip()[-500:] or "no stderr"))
    errs = re.search(r'<html[^>]*\bdata-render-errors="([^"]*)"', dom)
    if errs:
        sys.exit("build --render: page threw during boot:\n" + "\n".join(json.loads(html.unescape(errs.group(1)))))
    content = re.search(r'<main[^>]*\bid="content"[^>]*>(.*?)</main>', dom, re.S)
    if not content:
        sys.exit("build --render: no #content element in the rendered DOM")
    if not content.group(1).strip():
        sys.exit("build --render: #content rendered empty")
    print("render check passed (screenshot %s)" % os.path.relpath(shot, ROOT))

if "--render" in sys.argv[1:]:
    render_check(out)
