"""Launch checks offered from a blocker must be quick and side-effect free: never test suites, e2e journeys, project
scripts, seeds or database resets (an agent's 'run the e2e suite' blocker once became a launch check on a whole project)."""
import json, os, re, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def good(cmds, requires=()):
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    fn = re.search(r"const goodCheck = [\s\S]*?\};", src).group(0).replace("const goodCheck", "globalThis.goodCheck")
    js = fn + "\nconsole.log(JSON.stringify(%s.map(c => goodCheck(c, { requires: %s }))))" % (json.dumps(cmds), json.dumps([{"check": r} for r in requires]))
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)


def test_heavy_or_stateful_commands_are_never_launch_checks():
    assert good(["pnpm exec tsx scripts/e2e-platform-journey.ts --no-reset", "supabase db reset", "pnpm test:e2e",
                 "npx playwright test", "pnpm seed:fixtures", "python3 -m pytest -q", "npm install"]) == [False] * 7


def test_quick_checks_are_allowed_once():
    assert good(["pnpm build", "docker info", "npm run build"]) == [True, True, True]
    assert good(["docker info"], requires=["docker info"]) == [False]
