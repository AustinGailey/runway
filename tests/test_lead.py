"""The AI lead: one Sonnet call per decision blocker, with a stand-in claude (no tokens). It decides routine calls,
escalates low confidence, and a deterministic keyword guard forces escalation even when the model says no."""
import base64, json

import pytest

import runway
from test_startcheck import calls, fake_claude


def spec(detail="Which test runner should the new module use?", options=("pytest", "unittest"), **kw):
    s = {"project": {"goal": "A reliable planner", "agentNotes": "Use pytest."}, "pi": {"name": "PI 10", "objective": "Nothing launches that can't finish"},
         "task": {"id": "t1", "title": "Add export", "detail": "Extend exporter.py", "acceptance": "- csv works"},
         "report": "I stopped: the brief leaves the test runner open.", "blocker": {"detail": detail, "options": list(options)},
         "log": [{"question": "Which formatter?", "answer": "ruff"}]}
    s.update(kw)
    return s


def answer(**kw):
    d = {"category": "none", "decision": "pytest", "why": "The project rules say pytest.", "confidence": 0.9, "escalate": False}
    d.update(kw)
    return json.dumps(d)


@pytest.fixture(autouse=True)
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(runway, "BASE", str(tmp_path / "base"))
    monkeypatch.setenv("RUNWAY_NO_KEYCHAIN", "1")


def test_routine_decision_is_decided(tmp_path):
    c = fake_claude(tmp_path, answer(), cost=0.03)
    r = runway.lead_decide(spec(), claude=c)
    assert r["escalate"] is False and r["decision"] == "pytest" and r["confidence"] == 0.9 and r["reason"] == "" and r["categories"] == []
    assert calls(tmp_path) == 1
    args = eval((tmp_path / "args").read_text())
    assert args[1] == "-p" and args[args.index("--model") + 1] == "sonnet" and args[args.index("--tools") + 1] == ""
    assert args[args.index("--max-budget-usd") + 1] == "0.25"
    p = args[2]  # goal, rules, PI objective, brief, report, blocker, and the decision log all reach the lead
    for needle in ("A reliable planner", "Use pytest.", "Nothing launches", "Extend exporter.py", "I stopped: the brief", "Which test runner", "ruff"):
        assert needle in p


def test_low_confidence_escalates(tmp_path):
    r = runway.lead_decide(spec(), claude=fake_claude(tmp_path, answer(confidence=0.59)))
    assert r["escalate"] is True and "confidence 0.59" in r["reason"]
    assert runway.lead_decide(spec(), claude=fake_claude(tmp_path, answer(confidence=0.6)))["escalate"] is False


def test_model_escalation_is_kept(tmp_path):
    r = runway.lead_decide(spec(), claude=fake_claude(tmp_path, answer(escalate=True, category="brand")))
    assert r["escalate"] is True and r["categories"] == ["brand"]


@pytest.mark.parametrize("detail,cat", [
    ("Which price should the paid tier use?", "money"),
    ("What do we tell the client about the outage?", "client"),
    ("Should we deploy to Vercel now?", "external"),
    ("Which tagline goes on the page?", "brand"),
    ("Delete the old table or keep it?", "irreversible"),
])
def test_guard_forces_escalation_when_the_model_says_no(tmp_path, detail, cat):
    r = runway.lead_decide(spec(detail=detail, options=("yes", "no")), claude=fake_claude(tmp_path, answer(decision="yes")))
    assert r["escalate"] is True and cat in r["categories"] and r["decision"] == "yes"


def test_guard_reads_the_models_own_answer_too(tmp_path):
    r = runway.lead_decide(spec(), claude=fake_claude(tmp_path, answer(decision="Email the customers first")))
    assert r["escalate"] is True and {"client", "external"} <= set(r["categories"])


def test_guard_never_removes_escalation():
    assert runway.lead_guard("rename the helper function; pick pytest") == []
    res = {"decision": "pytest", "why": "", "confidence": 0.1, "category": "none", "escalate": True}
    assert runway.lead_verdict(spec(), res)["escalate"] is True
    clean = {"decision": "pytest", "why": "", "confidence": 0.99, "category": "none", "escalate": False}
    assert runway.lead_verdict(spec(), clean)["escalate"] is False
    assert runway.lead_verdict(spec(detail="Spend more on the budget?"), clean)["escalate"] is True


def test_failures_and_garbage_escalate(tmp_path):
    assert runway.lead_decide(spec(), claude=fake_claude(tmp_path, "no json here"))["escalate"] is True
    r = runway.lead_decide(spec(), claude=fake_claude(tmp_path, "{}", exit_code=1))
    assert r["escalate"] is True and "couldn't decide" in r["reason"]
    assert runway.lead_decide(spec(), claude=str(tmp_path / "missing"))["escalate"] is True


def test_answer_in_fences_is_read(tmp_path):
    r = runway.lead_decide(spec(), claude=fake_claude(tmp_path, "Here:\n```json\n" + answer() + "\n```"))
    assert r["escalate"] is False and r["decision"] == "pytest"


def test_spend_is_logged_and_summed_for_the_month(tmp_path):
    c = fake_claude(tmp_path, answer(), cost=0.03)
    runway.lead_decide(spec(), claude=c)
    runway.lead_decide(spec(), claude=c)
    s = runway.lead_spend()
    assert s["calls"] == 2 and abs(s["usd"] - 0.06) < 1e-9
    info = runway.lead_info()
    assert info["line"]["money"] and set(info["line"]) == {"money", "client", "external", "brand", "irreversible"} and info["minConfidence"] == 0.6


def test_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runway, "CLAUDE", fake_claude(tmp_path, answer()))
    runway.main(["lead", base64.b64encode(json.dumps(spec()).encode()).decode()])
    out = capsys.readouterr().out
    assert out.startswith("@@LEAD ") and json.loads(out[7:])["decision"] == "pytest"
    runway.main(["lead", "not-base64!"])
    assert capsys.readouterr().out.startswith("@@ERR")
    runway.main(["lead-info"])
    assert capsys.readouterr().out.startswith("@@LEADINFO ")
