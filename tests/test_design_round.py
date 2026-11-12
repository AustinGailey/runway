"""Design round (PI 10 Sprint 2): the decisions a PI's tasks need are listed in one sample call and answered before agents
start. briefLint's open-choice check runs under node; the rest goes through the harness (harness/serve.py + drive.mjs)
with a stand-in sample, per CLAUDE.md. Each drive() list is [{"goto": url}] + steps."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
CHROME = os.environ.get("RUNWAY_CHROME") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


# ---- briefLint open-choice (node only) ----

def lint(detail, acceptance="- `npm test` passes"):
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    page = src[src.index("const DEFAULTS = "):src.index("\n/* ---------- data layer ---------- */")]
    task = {"detail": detail, "acceptance": acceptance, "estTokens": 100000}
    out = subprocess.run([NODE, "-e", page + "\nconsole.log(JSON.stringify(briefLint(%s, {})));" % json.dumps(task)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def open_choices(detail):
    return [i for i in lint(detail) if i["kind"] == "open-choice"]


@pytest.mark.skipif(NODE is None, reason="node is needed to run the page's JavaScript")
class TestOpenChoice:
    @pytest.mark.parametrize("brief", [
        "Store the cache in Redis or Postgres.",
        "The sync format is TBD.",
        "Decide which queue library to use.",
        "Use either the REST API or a webhook.",
        "Pick the colour scheme. It is to be decided later.",
    ])
    def test_open_choices_are_flagged(self, brief):
        issues = open_choices(brief)
        assert len(issues) >= 1 and issues[0]["criterion"]
        assert "design round" in issues[0]["fix"]

    @pytest.mark.parametrize("brief", [
        "Store the cache in Postgres (decision db-choice).",
        "Use Redis or Postgres per decision queue-lib; follow it.",
        "Use the `a or b` form in the parser.",
        "Handle one or more rows per call.",
        "Add the field and/or the flag.",
        "Add a line to a file. Run npm test.",
        "",
    ])
    def test_settled_briefs_are_not_flagged(self, brief):
        assert open_choices(brief) == []

    def test_each_open_sentence_is_one_issue_and_dismissal_is_per_sentence(self):
        issues = open_choices("Use Redis or Postgres. Then wire it up. The retry count is TBD.")
        assert [i["criterion"] for i in issues] == ["Use Redis or Postgres.", "The retry count is TBD."]

    def test_a_decision_reference_only_covers_its_own_sentence(self):
        assert len(open_choices("Use Redis or Postgres (decision db-choice). The retry count is TBD.")) == 1

    def test_the_planner_prompt_asks_for_decision_ids(self):
        src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
        assert "Never leave a choice open in a brief" in src and 'name a decision id for it ("decision db-choice")' in src


# ---- harness ----

@pytest.fixture
def harness(tmp_path):
    # always build: the harness serves dist/runway.html, and a page left over from an older build tests old code
    subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], check=True, capture_output=True)
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "harness", "serve.py"), "--port", "0", "--sandbox", str(tmp_path), "--tick", "600"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = p.stdout.readline()
    assert line.startswith("HARNESS "), line + p.stderr.read()
    yield line.split()[1]
    p.terminate()
    p.wait(10)


def drive(url, steps, timeout=120):
    p = subprocess.run(["node", os.path.join(ROOT, "harness", "drive.mjs"), "-"], input=json.dumps([{"goto": url}] + steps),
                       capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    assert p.returncode == 0, p.stdout + p.stderr
    return [l.get("value") for l in lines if "step" in l]


class Steps:
    """Steps with names: ev("label", js) adds an eval; out["label"] is its value after the drive."""
    def __init__(self, *pre):
        self.steps, self.names = list(pre), {}

    def ev(self, name, js):
        self.names[name] = len(self.steps)
        self.steps.append({"eval": js})
        return self

    def add(self, *steps):
        self.steps.extend(steps)
        return self

    def run(self, harness):
        v = drive(harness, self.steps)  # drive() puts the goto first, so v[i + 1] is steps[i]'s value
        return {n: v[i + 1] for n, i in self.names.items()}


BOOT = [{"waitForText": "Harness project", "timeout": 15000},
        {"eval": "new Promise(r=>{const i=setInterval(()=>{if(S.loaded&&helperOn()&&S.fullProjects.has('hp1')){clearInterval(i);r(1)}},100)})"}]
# launch commands are recorded and answered; vault exports are recorded and still run for real in the sandbox
STUB = {"eval": "(window.__launches=[], window.__exports=[], window.__real=host, "
                "host = async (cmd, ms) => { if (cmd.includes('run.sh')){ window.__launches.push(cmd); return '@@LAUNCHED x' } if (cmd.includes(' preflight ')) return '@@PREFLIGHT []';"
                " if (cmd.includes(' export ')) window.__exports.push(JSON.parse(atob(cmd.match(/export '([^']+)'/)[1]))); return window.__real(cmd, ms) }, 1)"}

# what the stand-in sample answers: one routine and one escalated decision, plus junk and an already-logged repeat
DECISIONS = {"decisions": [
    {"id": "queue-lib", "question": "Which queue library should the worker use?", "why": "Both tasks enqueue jobs.",
     "options": [{"label": "bullmq", "consequence": "Redis required"}, {"label": "pg-boss", "consequence": "Uses the existing Postgres"}],
     "recommended": 1, "escalate": False, "category": "architecture", "tasks": ["ht1", "ht2"]},
    {"id": "domain-name", "question": "Which domain should the app be published on?", "why": "Task A writes the deploy config.",
     "options": [{"label": "app.example.com", "consequence": "Existing brand domain"}, {"label": "example.app", "consequence": "New domain to buy"}],
     "recommended": 0, "escalate": True, "category": "external", "tasks": ["ht1"]},
    {"id": "no-options", "question": "A question with a single option?", "options": [{"label": "only"}], "tasks": ["ht1"]},
    {"id": "already-logged", "question": "Already decided?", "options": [{"label": "a"}, {"label": "b"}], "recommended": 0, "escalate": False, "tasks": ["ht1"]},
]}
STUB_SAMPLE = ("(window.__prompts = [], S.sample = { json: async (p) => { window.__prompts.push(p); return %s } }, 1)" % json.dumps(DECISIONS))
LOGGED = {"eval": "patch('projects', 'hp1', { decisions: [{ id: 'already-logged', question: 'Already decided?', answer: 'a', why: '', by: 'austin', piId: 'hpi1', at: 1791300000000, tasks: ['ht1'] }] }).then(() => 1)"}
MENU_ROUND = {"eval": "(S.view = 'board', S.projectId = 'hp1', S.piId['hp1'] = 'hpi1', S.overlay = null, S.menu = null, render(), 1)"}
SETTLE = {"eval": "new Promise(r=>setTimeout(r,500))"}


def until(cond, ms=8000):
    return {"eval": "new Promise(r=>{const t0=Date.now();const i=setInterval(()=>{let ok=false;try{ok=!!(%s)}catch(e){}"
                    "if(ok||Date.now()-t0>%d){clearInterval(i);r(ok)}},100)})" % (cond, ms)}


def design_round(*after):
    """The page, a stand-in sample, one logged decision, and the Design round item clicked in the project menu."""
    return Steps(*BOOT, STUB, {"eval": STUB_SAMPLE}, LOGGED, MENU_ROUND,
                 {"click": "[data-act=menu][data-m=proj]"}, {"click": ".menu [data-act=dr-open]"},
                 until("S.overlay && S.overlay.kind === 'design' && S.overlay.phase === 'review'")).add(*after)


@pytest.mark.skipif(not (NODE and os.path.exists(CHROME)), reason="needs node and Chrome")
@pytest.mark.usefixtures("chrome_lock")
class TestHarness:
    def test_the_round_lists_the_decisions_with_recommendations_and_escalate_flags(self, harness):
        s = design_round()
        s.ev("prompts", "window.__prompts.length")
        s.ev("prompt", "window.__prompts[0]")
        s.ev("open", "S.pis.find(p => p.id === 'hpi1').openDecisions.map(d => [d.id, d.escalate, d.recommended, d.options.length, d.tasks])")
        s.ev("cards", "[...document.querySelectorAll('.dr-card')].map(c => [c.dataset.dr, c.querySelector('input[type=radio]:checked').value, !!c.querySelector('[data-act=dr-ai]'), !!c.querySelector('[data-act=dr-accept]'), !!c.querySelector('input[type=text]')])")
        s.ev("all", "!!document.querySelector('[data-act=dr-all]') && document.querySelector('[data-act=dr-all]').textContent")
        out = s.run(harness)
        assert out["prompts"] == 1
        # the one sample call reads the PI, its sprint goal, every brief, the project goal and the existing decision log
        for text in ["Exercise launch, merge and land", "Two small tasks", "task ht1", "task ht2", "Add a line to a file.", "A scratch repo in the sandbox",
                     "already-logged: Already decided? → a"]:
            assert text in out["prompt"]
        # the junk row (one option) and the already-logged decision are skipped
        assert out["open"] == [["queue-lib", False, 1, 2, ["ht1", "ht2"]], ["domain-name", True, 0, 2, ["ht1"]]]
        # escalated first; the recommendation is preselected; the AI lead is offered only for the routine one
        assert out["cards"] == [["domain-name", "0", False, True, True], ["queue-lib", "1", True, True, True]]
        assert out["all"].startswith("Accept all recommended")

    def test_accept_all_answers_only_the_routine_decisions_and_the_escalated_one_gates_its_tasks(self, harness):
        s = design_round()
        s.ev("gate_before", "[launchGate(task('ht1'))?.msg, launchGate(task('ht2'))]")
        s.add({"click": "[data-act=dr-all]"}, until("(S.pis.find(p => p.id === 'hpi1').openDecisions || []).length === 1"))
        s.ev("log", "S.projects.find(p => p.id === 'hp1').decisions.map(d => [d.id, d.answer, d.by, d.piId, d.tasks])")
        s.ev("left", "S.pis.find(p => p.id === 'hpi1').openDecisions.map(d => d.id)")
        s.ev("gates", "[launchGate(task('ht1'))?.msg, launchGate(task('ht1'))?.kind, launchGate(task('ht2'))]")
        # the gated task is not launched by any path; the other is
        s.ev("launch", "(S.sel = new Set(['ht1', 'ht2']), launch(null, { skipPreflight: true }).then(() => [task('ht1').status, task('ht2').status, window.__launches.length]))")
        s.ev("card", "(S.overlay = null, render(), document.body.textContent.includes('Waits for your decision: Which domain should the app be published on?'))")
        # Austin answers the escalated one: the gate clears and the task can launch
        s.add({"eval": "(S.overlay = { kind: 'design', piId: 'hpi1', phase: 'review', error: '', form: { picks: {}, custom: {} } }, render(), 1)"},
              {"click": ".dr-card[data-dr=domain-name] [data-act=dr-accept]"}, until("!(S.pis.find(p => p.id === 'hpi1').openDecisions || []).length"))
        s.ev("cleared", "[launchGate(task('ht1')), S.projects.find(p => p.id === 'hp1').decisions.find(d => d.id === 'domain-name').answer]")
        s.ev("empty", "document.body.textContent.includes('No open decisions')")
        out = s.run(harness)
        assert out["gate_before"] == ["Waits for your decision: Which domain should the app be published on?", None]
        assert out["log"][0][0] == "already-logged"
        assert out["log"][1] == ["queue-lib", "pg-boss", "austin", "hpi1", ["ht1", "ht2"]]
        assert out["left"] == ["domain-name"]
        assert out["gates"] == ["Waits for your decision: Which domain should the app be published on?", "decision", None]
        assert out["launch"][0] == "todo" and out["launch"][2] >= 1 and out["launch"][1] != "todo"
        assert out["card"] is True
        assert out["cleared"] == [None, "app.example.com"] and out["empty"] is True

    def test_write_my_own_pick_another_and_the_ai_lead(self, harness):
        s = design_round()
        # an empty custom answer is refused; a typed one is taken (typing selects Write my own)
        s.add({"click": ".dr-card[data-dr=domain-name] input[value=custom]"}, {"click": ".dr-card[data-dr=domain-name] [data-act=dr-accept]"})
        s.ev("refused", "[(S.pis.find(p => p.id === 'hpi1').openDecisions || []).length, S.overlay.error]")
        s.add({"type": {"selector": ".dr-card[data-dr=domain-name] input[type=text]", "text": "app.mycompany.io"}}, {"click": ".dr-card[data-dr=domain-name] [data-act=dr-accept]"},
              until("!(S.pis.find(p => p.id === 'hpi1').openDecisions || []).some(d => d.id === 'domain-name')"))
        # the routine one: let the AI lead decide
        s.add({"click": ".dr-card[data-dr=queue-lib] [data-act=dr-ai]"}, until("!(S.pis.find(p => p.id === 'hpi1').openDecisions || []).length"))
        s.ev("log", "S.projects.find(p => p.id === 'hp1').decisions.filter(d => d.id !== 'already-logged').map(d => [d.id, d.answer, d.by])")
        # an escalated decision can't be handed to the AI lead, even by calling the action directly
        s.add({"eval": "patch('pis', 'hpi1', { openDecisions: [{ id: 'spend', question: 'Buy the paid plan?', why: '', options: [{ label: 'yes', consequence: '' }, { label: 'no', consequence: '' }], recommended: 1, escalate: true, category: 'money', tasks: ['ht2'] }] }).then(() => 1)"},
              {"eval": "(S.overlay = { kind: 'design', piId: 'hpi1', phase: 'review', error: '', form: { picks: {}, custom: {} } }, render(), 1)"})
        s.ev("ai_button", "!!document.querySelector('.dr-card[data-dr=spend] [data-act=dr-ai]')")
        s.add({"eval": "(() => { const b = document.createElement('button'); b.dataset.act = 'dr-ai'; b.dataset.id = 'spend'; document.body.appendChild(b); b.click(); b.remove(); return 1 })()"}, SETTLE)
        s.ev("still", "(S.pis.find(p => p.id === 'hpi1').openDecisions || []).map(d => d.id)")
        out = s.run(harness)
        assert out["refused"][0] == 2 and "answer" in out["refused"][1]
        assert out["log"] == [["domain-name", "app.mycompany.io", "austin"], ["queue-lib", "pg-boss", "ai-lead"]]
        assert out["ai_button"] is False and out["still"] == ["spend"]

    def test_answers_reach_each_affected_tasks_agent_prompt_and_the_vault_decision_log(self, harness):
        s = design_round()
        s.ev("before", "agentPrompt(task('ht2')).includes('Decisions already made')")
        s.add({"eval": "saveSettings({ vaultPath: '~/vault' }), 1"}, {"eval": "window.__real(\"mkdir -p ~/vault\").then(() => 1)"},
              {"click": ".dr-card[data-dr=domain-name] [data-act=dr-accept]"}, until("!(S.pis.find(p => p.id === 'hpi1').openDecisions || []).some(d => d.id === 'domain-name')"),
              {"click": "[data-act=dr-all]"}, until("!(S.pis.find(p => p.id === 'hpi1').openDecisions || []).length"), SETTLE)
        s.ev("p1", "agentPrompt(task('ht1'))")
        s.ev("p2", "agentPrompt(task('ht2'))")
        s.ev("exports", "window.__exports.map(e => [e.path, e.append, e.markdown])")
        s.ev("vault", "window.__real('cat ~/vault/_Claude/Strategies/decision-log.md')")
        out = s.run(harness)
        assert out["before"] is False
        head = "Decisions already made (follow these; don't reopen them):"
        assert head in out["p1"] and "[domain-name] Which domain should the app be published on? → app.example.com" in out["p1"]
        assert "[queue-lib] Which queue library should the worker use? → pg-boss" in out["p1"]
        # ht2 is only affected by the queue decision; the other tasks' decisions don't leak into it
        assert head in out["p2"] and "pg-boss" in out["p2"] and "domain-name" not in out["p2"]
        assert out["p1"].index(head) < out["p1"].index("Token budget")
        assert len(out["exports"]) == 2 and all(e[0] == "_Claude/Strategies/decision-log.md" and e[1] is True for e in out["exports"])
        md = "\n".join(e[2] for e in out["exports"])
        assert "**Decision:** app.example.com" in md and "**Confidence:** confirmed-with-austin" in md and "**Decided by:** Austin" in md
        assert "Which queue library should the worker use?" in md and "Runway design round" in md
        assert "## " in out["vault"] and "Which domain should the app be published on?" in out["vault"]

    def test_the_ai_leads_entry_is_logged_as_ai_derived(self, harness):
        s = design_round()
        s.add({"eval": "saveSettings({ vaultPath: '~/vault' }), 1"}, {"eval": "window.__real(\"mkdir -p ~/vault\").then(() => 1)"},
              {"click": ".dr-card[data-dr=queue-lib] [data-act=dr-ai]"}, until("window.__exports.length === 1"))
        s.ev("md", "window.__exports[0].markdown")
        out = s.run(harness)
        assert "**Decided by:** AI lead" in out["md"] and "**Confidence:** ai-derived" in out["md"] and "confirmed-with-austin" not in out["md"]

    def test_planning_a_pi_runs_the_round_in_the_background_and_opens_it_when_decisions_exist(self, harness):
        plan = {"summary": "s", "pis": [{"name": "PI 2 · New", "objective": "Ship it", "sprints": [{"name": "Sprint 1 · A", "goal": "g", "tasks": [
            {"key": "T1", "title": "Build it", "detail": "Use Redis or Postgres.", "acceptance": "- `npm test` passes", "estTokens": 100000, "model": "sonnet", "kind": "code", "dependsOn": []}]}]}]}
        sample = ("(window.__calls = 0, S.sample = { json: async (p) => { window.__calls++; if (p.includes('design lead')){ const t = S.tasks.find(x => x.title === 'Build it');"
                  " return { decisions: [{ id: 'store', question: 'Where is the cache stored?', why: 'Build it', options: ['Redis', 'Postgres'], recommended: 'Postgres', escalate: false, tasks: [t.id] }] } } return %s } }, 1)" % json.dumps(plan))
        s = Steps(*BOOT, STUB, {"eval": sample}, MENU_ROUND,
                  {"eval": "(S.overlay = { kind: 'planner', extend: true, projectId: 'hp1', phase: 'form', form: { name: 'Harness project', goal: 'g', repo: '', context: '', pis: 1, subs: [] } }, render(), 1)"},
                  {"click": "[data-act=run-plan]"}, {"waitFor": "[data-lint=open-choice]", "timeout": 15000})
        s.ev("note", "document.querySelector('[data-lint=open-choice]').textContent")
        s.add({"click": "[data-act=accept-plan]"}, until("S.overlay && S.overlay.kind === 'design' && S.overlay.phase === 'review'", 15000))
        s.ev("round", "[window.__calls, document.querySelector('.dr-card input:checked').value, S.pis.find(p => p.name === 'PI 2 · New').openDecisions.map(d => [d.id, d.recommended, d.options.map(o => o.label)])]")
        out = s.run(harness)
        assert "1 brief leaves a choice open" in out["note"] and "Use Redis or Postgres." in out["note"]
        assert out["round"] == [2, "1", [["store", 1, ["Redis", "Postgres"]]]]

    def test_open_states_empty_nothing_to_ask_and_error(self, harness):
        # no decisions: an empty state; a sample that fails: an error with Try again, and nothing gated
        s = Steps(*BOOT, STUB, {"eval": "(S.sample = { json: async () => ({ decisions: [] }) }, 1)"}, MENU_ROUND,
                  {"click": "[data-act=menu][data-m=proj]"}, {"click": ".menu [data-act=dr-open]"}, until("S.overlay && S.overlay.phase === 'review'"))
        s.ev("empty", "document.body.textContent.includes('No open decisions')")
        s.add({"eval": "(S.sample = { json: async () => { throw { code: 'rate_limited' } } }, 1)"}, {"click": "[data-act=dr-more]"}, until("S.overlay.phase === 'error'"))
        s.ev("error", "[document.body.textContent.includes('usage limit'), !!document.querySelector('.modal [data-act=dr-more]'), launchGate(task('ht1'))]")
        out = s.run(harness)
        assert out["empty"] is True and out["error"] == [True, True, None]

    def test_without_a_design_round_nothing_changes(self, harness):
        s = Steps(*BOOT, MENU_ROUND)
        s.ev("same", "[launchGate(task('ht1')), agentPrompt(task('ht1')).includes('Decisions already made'), document.querySelector('[data-act=dr-open]') === null]")
        out = s.run(harness)
        assert out["same"] == [None, False, True]
