"""briefLint (src/runway.src.html): a pure check of a task's brief before it ever reaches an agent, run under node
against real-shaped briefs. The run data behind this task was 18 of 154 runs ending reviewer-unmet, mostly briefs
the reviewer couldn't check from a diff or with too many criteria for one task; the "reviewer-unmet shaped" cases
below are modelled on that pattern (no stored history of the actual briefs was available to read)."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is needed to run the page's JavaScript")


def page_source():
    """DEFAULTS (briefLint falls back to its chunkBudget) through the end of the brief lint section, exactly as the
    page has it."""
    src = open(os.path.join(ROOT, "src", "runway.src.html")).read()
    start = src.index("const DEFAULTS = ")
    end = src.index("\n/* ---------- data layer ---------- */")
    return src[start:end]


def run_js(body):
    script = page_source() + "\n" + body
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def lint(acceptance="", detail="", estTokens=200000, dismissed=None):
    task = {"acceptance": acceptance, "detail": detail, "estTokens": estTokens, "briefLintDismissed": dismissed or []}
    body = "console.log(JSON.stringify(briefLint(%s, {name:'Project'})));" % json.dumps(task)
    return run_js(body)


def kinds(issues):
    return [i["kind"] for i in issues]


# ---- no-criteria ----

def test_empty_acceptance_is_no_criteria():
    assert kinds(lint(acceptance="")) == ["no-criteria"]


def test_whitespace_only_acceptance_is_no_criteria():
    assert kinds(lint(acceptance="   \n  \n")) == ["no-criteria"]


# ---- untestable ----

def test_works_well_with_nothing_named_is_untestable():
    issues = lint(acceptance="- The login form works well")
    assert kinds(issues) == ["untestable"]
    assert "login form works well" in issues[0]["criterion"]


def test_clean_and_intuitive_ui_is_untestable():
    assert kinds(lint(acceptance="- The dashboard is clean and intuitive")) == ["untestable"]


def test_handles_errors_properly_is_untestable():
    assert kinds(lint(acceptance="- Errors are handled properly")) == ["untestable"]


def test_vague_word_next_to_a_named_file_is_not_untestable():
    # "clean" appears, but src/app.js and the specific behaviour make it checkable from a diff.
    assert kinds(lint(acceptance="- src/app.js renders a clean empty state when there are no tasks")) == []


def test_vague_word_next_to_a_command_in_backticks_is_not_untestable():
    assert kinds(lint(acceptance="- `npm run build` finishes cleanly with no errors")) == []


def test_named_test_file_is_not_untestable():
    assert kinds(lint(acceptance="- tests/test_widget.py passes")) == []


# ---- outside-agent ----

def test_deploy_is_outside_agent():
    issues = lint(acceptance="- Deploy the site to production")
    assert kinds(issues) == ["outside-agent"]


def test_publish_is_outside_agent():
    assert kinds(lint(acceptance="- Publish the new version to npm")) == ["outside-agent"]


def test_push_to_origin_is_outside_agent():
    assert kinds(lint(acceptance="- Push the branch to origin once done")) == ["outside-agent"]


def test_real_credentials_is_outside_agent():
    assert kinds(lint(acceptance="- Log in with a real account and confirm checkout works")) == ["outside-agent"]


def test_austin_sign_off_is_outside_agent():
    assert kinds(lint(acceptance="- Get Austin's sign-off on the new layout")) == ["outside-agent"]


def test_manual_browser_check_is_outside_agent():
    assert kinds(lint(acceptance="- Manually click through the checkout flow in a browser to confirm it looks right")) == ["outside-agent"]


# ---- too-big ----

def test_more_than_six_criteria_is_too_big():
    acc = "\n".join("- file%d.js updated" % i for i in range(7))
    assert kinds(lint(acceptance=acc)) == ["too-big"]


def test_six_or_fewer_criteria_is_not_too_big_on_count_alone():
    acc = "\n".join("- file%d.js updated" % i for i in range(6))
    assert kinds(lint(acceptance=acc)) == []


def test_big_estimate_with_more_than_four_criteria_is_too_big():
    acc = "\n".join("- file%d.js updated" % i for i in range(5))
    assert kinds(lint(acceptance=acc, estTokens=1_200_000)) == ["too-big"]  # over half the 2M default chunk budget


def test_big_estimate_with_few_criteria_is_not_flagged_too_big():
    acc = "- file0.js updated\n- file1.js updated"
    assert kinds(lint(acceptance=acc, estTokens=1_200_000)) == []


# ---- ambiguous-start ----

def test_branch_named_as_starting_point_in_prose_is_ambiguous_start():
    issues = lint(acceptance="- src/app.js has the new route", detail="The starting point is the branch runway/feature-x.")
    assert kinds(issues) == ["ambiguous-start"]


def test_task_named_as_starting_point_in_prose_is_ambiguous_start():
    issues = lint(acceptance="- src/app.js has the new route", detail="Start from task \"Add login form\" once it lands.")
    assert kinds(issues) == ["ambiguous-start"]


def test_normal_brief_prose_is_not_ambiguous_start():
    issues = lint(acceptance="- src/app.js has the new route", detail="Add a route to the login page. Keep the existing tests passing.")
    assert kinds(issues) == []


# ---- good briefs: no issues at all ----

def test_a_well_specified_brief_has_no_issues():
    issues = lint(
        acceptance="- src/runway.src.html defines briefLint and it returns the five issue kinds\n"
                   "- tests/test_brief_lint.py passes\n"
                   "- python3 build.py succeeds",
        detail="Add a pure briefLint(task, project) function to src/runway.src.html.",
        estTokens=300000,
    )
    assert issues == []


def test_a_short_task_with_one_concrete_criterion_has_no_issues():
    issues = lint(acceptance="- helper/runway.py VERSION is unchanged", detail="No-op change to a comment.", estTokens=50000)
    assert issues == []


# ---- reviewer-unmet shaped briefs: modelled on the run data this task describes (18 of 154 runs, the costliest
# failure reason), where the brief had criteria the reviewer could not verify from the diff ----

def test_reviewer_unmet_shaped_brief_vague_ux_criteria():
    issues = lint(
        # "settings page" names something observable, so only the two criteria with nothing concrete are flagged.
        acceptance="- The new settings page looks clean and professional\n- Navigation feels intuitive\n- Good UX throughout",
        detail="Add a settings page.",
        estTokens=400000,
    )
    assert kinds(issues) == ["untestable", "untestable"]


def test_reviewer_unmet_shaped_brief_too_many_criteria_for_one_task():
    acc = "\n".join([
        "- Login works",
        "- Signup works",
        "- Password reset works",
        "- Profile page loads",
        "- Settings save correctly",
        "- Billing page shows the right plan",
        "- Admin panel is accessible",
    ])
    issues = lint(acceptance=acc, detail="Build out the account area.", estTokens=1_800_000)
    assert "too-big" in kinds(issues)


# ---- dismissal ----

def test_dismissed_issue_does_not_return():
    before = lint(acceptance="- The UI is clean")
    assert kinds(before) == ["untestable"]
    key = before[0]["kind"] + "|" + before[0]["criterion"]
    after = lint(acceptance="- The UI is clean", dismissed=[key])
    assert after == []


def test_dismissing_one_issue_leaves_others():
    acc = "- The UI is clean\n- Deploy it to production"
    before = lint(acceptance=acc)
    assert sorted(kinds(before)) == ["outside-agent", "untestable"]
    untestable = next(i for i in before if i["kind"] == "untestable")
    key = untestable["kind"] + "|" + untestable["criterion"]
    after = lint(acceptance=acc, dismissed=[key])
    assert kinds(after) == ["outside-agent"]


# ---- all five kinds are reachable ----

def test_all_five_kinds_are_returned_by_the_function():
    seen = set()
    seen.update(kinds(lint(acceptance="")))
    seen.update(kinds(lint(acceptance="- Looks clean and nice")))
    seen.update(kinds(lint(acceptance="- Deploy to production")))
    seen.update(kinds(lint(acceptance="\n".join("- f%d.js" % i for i in range(7)))))
    seen.update(kinds(lint(acceptance="- a.js updated", detail="Start from branch runway/x.")))
    assert seen == {"no-criteria", "untestable", "outside-agent", "too-big", "ambiguous-start"}
