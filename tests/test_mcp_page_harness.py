"""Runway as an MCP server, the page side (PI 8 Sprint 1), end to end through the harness: the page mirrors its ready tasks
to ~/.ai-planner/board.json with board-sync, and applies the claims the real helper writes when a session claims,
finishes or releases a task. The helper side is the real `runway.py mcp`, run in the harness sandbox from inside the page
(a session's stdio server is just a process on the Mac); nothing is stubbed. Each drive() step list is
[{"goto": url}] + steps, so v[i] is steps[i-1]'s value."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get("RUNWAY_CHROME") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

pytestmark = [pytest.mark.skipif(not (shutil.which("node") and os.path.exists(CHROME)), reason="needs node and Chrome"),
              pytest.mark.usefixtures("chrome_lock")]  # one Chrome-driven test at a time on this Mac (conftest)


@pytest.fixture
def harness(tmp_path):
    # always build: the harness serves dist/runway.html, and a page left over from an older build tests old code
    subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], check=True, capture_output=True)
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "harness", "serve.py"), "--port", "0", "--sandbox", str(tmp_path), "--tick", "600"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = p.stdout.readline()
    assert line.startswith("HARNESS "), line + p.stderr.read()
    yield line.split()[1], str(tmp_path)
    p.terminate()
    p.wait(10)


def drive(url, steps, timeout=120):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps([{"goto": url}] + steps),
                       capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return [l.get("value") for l in lines if "step" in l]


def until(cond, ms=8000):
    return {"eval": "new Promise(r=>{const t0=Date.now();const i=setInterval(()=>{let ok=false;try{ok=!!(%s)}catch(e){}"
                    "if(ok||Date.now()-t0>%d){clearInterval(i);r(ok)}},100)})" % (cond, ms)}


BOOT = [{"waitForText": "Harness project", "timeout": 15000}, until("S.loaded && helperOn()", 15000),
        {"eval": "(S.view = 'board', render(), 1)"}]  # the cards are on the board; the page opens on the Inbox
# __mcp(tool, args): one call to the real `runway.py mcp` over stdio, in the sandbox; resolves to the tool's text
MCP = {"eval": """(window.__mcp = async (tool, args) => {
  const L = [{jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'t',version:'1'}}},
    {jsonrpc:'2.0',method:'notifications/initialized'}, {jsonrpc:'2.0',id:2,method:'tools/call',params:{name:tool,arguments:args}}];
  const out = await host("printf '%s\\\\n' " + L.map(l => sq(JSON.stringify(l))).join(' ') + ' | python3 "$HOME/.ai-planner/runway.py" mcp', 90000);
  const o = out.split('\\n').map(l => l.trim()).filter(l => l.startsWith('{')).map(l => JSON.parse(l)).find(x => x.id === 2);
  return o.result.content[0].text }, 1)"""}
# count board-sync calls and every write the page makes to its database
COUNT = {"eval": "(window.__bs = 0, window.__w = 0, (h => { host = async (c, t) => { if (c.includes('board-sync')) window.__bs++; return h(c, t) } })(host), "
                 "(p => { patch = async (...a) => { window.__w++; return p(...a) } })(patch), (u => { put = async (...a) => { window.__w++; return u(...a) } })(put), 1)"}
SYNCED = until("S.boardSyncedAt", 20000)
CARD = "document.querySelector('article.card[data-id=\"%s\"]')?.innerText || ''"


def claim(tid="ht1", client="Claude Code"):
    return {"eval": "__mcp('claim_task', {taskId: %s, client: %s}).then(t => JSON.parse(t))" % (json.dumps(tid), json.dumps(client))}


def refresh():
    return {"eval": "checkNow().then(()=>1)"}


def test_ready_tasks_are_mirrored_with_agent_prompts_and_not_resent_when_unchanged(harness):
    url, sandbox = harness
    v = drive(url, BOOT + [COUNT, SYNCED, {"eval": "window.__bs"},
                           {"eval": "boardSync().then(() => render()).then(() => boardSync()).then(() => window.__bs)"},
                           {"eval": "[agentPrompt(task('ht1')), agentPrompt(task('ht2'))]"},
                           {"eval": "patch('tasks', 'ht2', { title: 'Harness task B, renamed' }).then(() => 1)"},
                           until("window.__bs >= 2", 20000), {"eval": "S.boardSent.includes('renamed')"},
                           {"eval": "patch('tasks', 'ht2', { held: true }).then(() => 1)"}, until("window.__bs >= 3", 20000)])
    v = v[4:]
    assert v[2] == 1 and v[3] == 1  # one call for the first mirror, none for a redraw or a second boardSync with nothing changed
    board = json.load(open(os.path.join(sandbox, ".ai-planner", "board.json")))
    prompts = v[4]
    assert board["v"] == 1 and board["at"] > 0 and [t["id"] for t in board["tasks"]] == ["ht1"]  # ht2 became held: only ready tasks
    assert board["tasks"][0]["prompt"] == prompts[0] and "Harness task A" in prompts[0] and "Acceptance criteria" in prompts[0]
    assert board["tasks"][0]["repo"].endswith("/site") and board["tasks"][0]["projectName"] == "Harness project"
    assert v[7] is True and v[9] is True


def test_a_claim_becomes_a_running_card_applies_once_and_finishing_offers_merge(harness):
    url, _ = harness
    v = drive(url, BOOT + [MCP, SYNCED, claim(), refresh(),
                           until("task('ht1').status === 'running' && task('ht1').run?.source === 'mcp'", 15000),
                           {"eval": "task('ht1').run.claimedBy"}, {"eval": "S.chunks.find(c => c.id === task('ht1').run.chunkId)"},
                           until("(document.querySelector('article.card[data-id=\"ht1\"]')?.innerText || '').includes('Claimed by Claude Code')"),
                           {"eval": CARD % "ht1"}, COUNT,
                           {"eval": "applyHelper().then(applyHelper).then(() => window.__w)"},
                           {"eval": "checkNow().then(() => window.__w)"},
                           {"eval": "document.querySelector('article.card[data-id=\"ht1\"] [data-act=copy-text]')?.dataset.text"},
                           # the session works in the worktree and finishes: the existing done path moves the card to Review
                           {"eval": "(async () => { const wt = task('ht1').run.wt; "
                                    "await host(`cd ${sq(wt)} && echo w > w.txt && git add -A && git -c user.name=t -c user.email=t@t commit -qm work`, 30000); "
                                    "return JSON.parse(await __mcp('finish_task', {taskId: 'ht1', note: 'done'})) })()"}, refresh(),
                           until("task('ht1').status === 'review'", 20000),
                           {"eval": "[task('ht1').run.claimedBy, task('ht1').run.source, mergeable().some(x => x.id === 'ht1')]"}])
    v = v[3:]
    c = v[3]
    assert v[5] is True and v[6] == "Claude Code" and v[8] is True
    chunk = v[7]
    assert chunk["source"] == "mcp" and chunk["client"] == "Claude Code" and chunk["name"] == "Session \u00b7 Claude Code" and chunk["taskIds"] == ["ht1"]
    assert "Claimed by Claude Code" in v[9] and c["worktree"] in v[9]
    assert v[11] == 0 and v[12] == 0, "re-applying the same claim wrote to the database"
    assert v[13] == "cd " + c["worktree"]
    assert v[14]["state"] == "finished" and v[14]["checks"] == "green"
    assert v[16] is True and v[17] == ["Claude Code", "mcp", True]


def test_a_released_claim_returns_the_task_to_todo(harness):
    url, _ = harness
    v = drive(url, BOOT + [MCP, SYNCED, claim("ht2", "Codex"), refresh(), until("task('ht2').status === 'running'", 15000),
                           {"eval": "__mcp('release_task', {taskId: 'ht2', reason: 'changed my mind'}).then(t => JSON.parse(t))"}, refresh(),
                           until("task('ht2').status === 'todo' && S.chunks.some(c => c.source === 'mcp' && c.status === 'stopped')", 15000),
                           {"eval": "task('ht2').run.claimedBy || null"}, {"eval": CARD % "ht2"}])
    v = v[4:]
    assert v[4] is True and v[7] is True and v[8] is None and "Claimed by" not in v[9]


def test_red_checks_and_unknown_claims(harness):
    url, _ = harness
    red = {"v": 1, "taskId": "ht1", "chunkId": "mc202610160000abc", "client": "Claude Code", "state": "claimed", "at": 1791300000000,
           "worktree": "/tmp/wt-ht1", "branch": "runway-task/ht1", "checks": {"state": "red", "log": "FAIL: tests\nboom-line"}}
    ghost = dict(red, taskId="nope", chunkId="mc202610160000zzz")
    v = drive(url, BOOT + [{"eval": "(S.helper.claims = [%s, %s], applyHelper().then(() => 1))" % (json.dumps(red), json.dumps(ghost))},
                           until("task('ht1').status === 'running'"), {"eval": "applyHelper().then(() => task('ht1').run)"},
                           until("(document.querySelector('article.card[data-id=\"ht1\"]')?.innerText || '').includes('checks red')"),
                           {"eval": "S.chunks.filter(c => c.source === 'mcp').map(c => c.id)"}])
    v = v[3:]
    assert v[3]["checksState"] == "red" and "boom-line" in v[3]["checksLog"] and v[3]["wt"] == "/tmp/wt-ht1"
    assert v[4] is True and v[5] == ["mc202610160000abc"]  # the claim for a task the page doesn't have is ignored


def test_settings_shows_sessions_with_both_registration_commands(harness):
    url, sandbox = harness
    v = drive(url, BOOT + [{"eval": "(S.overlay = { kind: 'settings' }, render(), 1)"}, {"waitForText": "Sessions (MCP)", "timeout": 10000},
                           {"eval": "[...document.querySelectorAll('#s-sessions [data-act=copy-text]')].map(b => b.dataset.text)"},
                           {"eval": "document.querySelector('#s-sessions').innerText"}])
    claude_cmd, toml = v[-2]
    assert claude_cmd == "claude mcp add --scope user runway -- python3 ~/.ai-planner/runway.py mcp"
    assert toml == '[mcp_servers.runway]\ncommand = "python3"\nargs = ["%s/.ai-planner/runway.py", "mcp"]' % sandbox
    assert "Supported" in v[-1]
