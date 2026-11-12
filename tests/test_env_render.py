"""The environment UI, rendered in headless Chrome: the Runs section, the failed card and its Retry setup click, the task drawer
and the project form. tests/env_render_check.js drives the page over the DevTools protocol with a fixtured helper status.
Skips when Chrome or Node is missing (set RUNWAY_CHROME to a Chrome or Chromium binary)."""
import base64, os, shutil, subprocess

import pytest

pytestmark = pytest.mark.usefixtures("chrome_lock")  # one Chrome-driven test at a time on this Mac (conftest)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the same stub build.py injects for --render: a capability stub, and a record of boot-time errors
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
    return None


def test_the_environment_ui_renders_and_retry_setup_is_clicked_in_chrome(tmp_path):
    chrome = find_chrome()
    if not chrome:
        pytest.skip("no Chrome: set RUNWAY_CHROME to a Chrome or Chromium binary")
    if not shutil.which("node"):
        pytest.skip("no node to drive Chrome")
    with open(os.path.join(ROOT, "src", "runway.src.html")) as f:
        src = f.read()
    with open(os.path.join(ROOT, "helper", "runway.py"), "rb") as f:  # embedded as build.py embeds it
        src = src.replace("__HELPER_B64__", base64.b64encode(f.read()).decode())
    page = tmp_path / "page.html"
    page.write_text(RENDER_STUB + src)
    r = subprocess.run(["node", os.path.join(ROOT, "tests", "env_render_check.js"), chrome, str(page)],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
