"""The data contract between the agents, and the language rule. No Azure needed: runs in CI.

What crosses to the Planner is stated in amma_agents.PLANNER_FIELDS. These tests enforce it:
the Planner sees each flagged value (so a question can quote 9.8 g/dL), but never an in-range
value, never the report, and never the mother's name - in the local run and in the deployed workflow.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import amma_agents as A  # noqa: E402  (its Azure imports are optional)
from amma_tool import check_values  # noqa: E402

REPORTS = json.loads((ROOT / "blood_reports.json").read_text(encoding="utf-8"))["reports"]
IDS = [r["report_id"] for r in REPORTS]


def numbers(text):
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def tool(report):
    return json.loads(check_values(report["values"], report["week"]))


@pytest.mark.parametrize("report", REPORTS, ids=IDS)
def test_planner_receives_only_the_contract_fields(report):
    view = json.loads(A.planner_input(json.dumps(tool(report))))
    assert set(view) <= set(A.PLANNER_FIELDS)
    assert "all_values" not in view and "ranges_version" not in view


@pytest.mark.parametrize("report", REPORTS, ids=IDS)
def test_every_number_the_planner_sees_came_from_a_flag(report):
    out = tool(report)
    allowed = numbers(json.dumps([out["week"], out["trimester"], out["flags"], out["unchecked"]]))
    assert numbers(A.planner_input(json.dumps(out))) <= allowed


def test_in_range_values_never_reach_the_planner():
    out = tool(REPORTS[1])                                   # Lakshmi, R-002
    in_range = {str(v["value"]) for v in out["all_values"].values() if v["in_range"]}
    assert in_range == {"2.1", "28"}                         # TSH and vitamin D
    assert not in_range & numbers(A.planner_input(json.dumps(out)))


def test_flagged_values_do_cross_so_a_question_can_quote_them():
    view = json.loads(A.planner_input(json.dumps(tool(REPORTS[1]))))
    assert {f["metric"]: f["value"] for f in view["flags"]} == {
        "haemoglobin": 9.8, "fasting_glucose": 105, "ferritin": 18}


def test_deployed_workflow_input_has_no_names_and_no_in_range_values():
    text = A.workflow_input(REPORTS)
    assert not [r["mother"] for r in REPORTS if r["mother"] in text]
    assert "all_values" not in text
    everything_in_range = {"11.8", "84", "1.8", "45", "32", "2.1", "28"}
    assert not everything_in_range & numbers(text)


def test_deploy_script_sends_the_contract_not_the_report():
    src = (ROOT / "amma_deploy.py").read_text(encoding="utf-8")
    assert "workflow_input(" in src and "['mother']" not in src


def test_planner_is_never_asked_for_a_retest_interval():
    text = A.PLANNER_INSTRUCTIONS.lower()
    assert "in weeks" not in text
    assert "never say when to re-test" in text


@pytest.mark.parametrize("signals, language, certain", [
    ({"chosen": "Bhojpuri", "phone": "+91 98765", "region": "Bihar"}, "Bhojpuri", True),
    ({"locale": "ml-IN", "phone": "+91 98765"}, "Malayalam", True),
    ({"phone": "+91 98765"}, "Hindi", False),
    ({"phone": "+91 98765", "region": "Kerala"}, "Malayalam", False),
    ({"region": "Bihar"}, "Hindi", False),
    ({}, "English", False),
])
def test_only_her_choice_or_her_device_is_certain(signals, language, certain):
    got_language, _, got_certain = A.detect_language(**signals)
    assert (got_language, got_certain) == (language, certain)
