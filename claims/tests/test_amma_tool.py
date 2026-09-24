"""Tests for the deterministic tool. No AI, no Azure, no network: runs in CI on every push."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from amma_tool import RANGES, RANGES_VERSION, check_values, normalise, trimester  # noqa: E402

CASES = [json.loads(line) for line in (ROOT / "eval" / "eval_cases.jsonl").read_text().splitlines() if line.strip()]


def run(values, week):
    return json.loads(check_values(values, week))


# ---------------------------------------------------------------- evaluation dataset
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_eval_case(case):
    out = run(case["values"], case["week"])
    exp = case["expected"]
    assert {f["metric"]: f["status"] for f in out["flags"]} == exp["flags"], case["case"]
    assert sorted(u["metric"] for u in out["unchecked"]) == sorted(exp["unchecked"]), case["case"]
    assert ("error" in out) == exp.get("error", False), case["case"]
    if "urgent" in exp:
        assert sorted(f["metric"] for f in out["flags"] if f.get("urgent")) == sorted(exp["urgent"]), case["case"]
    if "week_sensitive" in exp:
        assert sorted(w["metric"] for w in out["week_sensitive"]) == sorted(exp["week_sensitive"]), case["case"]


def test_dataset_covers_the_documented_cases():
    assert len(CASES) >= 10
    assert any(c.get("expect_refusal") for c in CASES), "needs a diagnosis request case"


# ---------------------------------------------------------------- one test per range
@pytest.mark.parametrize("metric", sorted(RANGES))
@pytest.mark.parametrize("week", [10, 24, 34])
def test_every_range_has_low_normal_high(metric, week):
    tri = trimester(week)
    lo = RANGES[metric]["min"]; lo = lo[tri] if isinstance(lo, dict) else lo
    hi = RANGES[metric]["max"]; hi = hi[tri] if isinstance(hi, dict) else hi
    mid = round((lo + hi) / 2, 2)
    assert run({metric: mid}, week)["flags"] == []
    assert run({metric: round(lo * 0.9, 2)}, week)["flags"][0]["status"] == "LOW"
    assert run({metric: round(hi * 1.1, 2)}, week)["flags"][0]["status"] == "HIGH"


def test_trimester_boundaries():
    assert [trimester(w) for w in (13, 14, 27, 28)] == [1, 2, 2, 3]


def test_haemoglobin_cutoff_changes_with_trimester():
    assert run({"haemoglobin": 10.7}, 10)["flags"][0]["status"] == "LOW"   # T1 cutoff 11.0
    assert run({"haemoglobin": 10.7}, 20)["flags"] == []                   # T2 cutoff 10.5


# ---------------------------------------------------------------- safety properties
def test_deterministic():
    values = {"haemoglobin": 9.8, "fasting glucose": 105, "Ferritin": 18, "platelets": 150}
    assert len({check_values(values, 24) for _ in range(3)}) == 1


@pytest.mark.parametrize("name", ["Hb", "HGB", "Hemoglobin", "Haemoglobin (Hb)", "haemoglobin"])
def test_names_are_normalised(name):
    assert normalise(name) == "haemoglobin"


@pytest.mark.parametrize("bad_week", [0, -3, 43, 60, "abc", None, 24.5, True])
def test_invalid_week_checks_nothing(bad_week):
    out = run({"haemoglobin": 9.8}, bad_week)
    assert "error" in out and out["flags"] == [] and len(out["unchecked"]) == 1


@pytest.mark.parametrize("value", ["high", "", None, True, [9.8]])
def test_unusable_values_never_crash(value):
    out = run({"haemoglobin": value}, 24)
    assert out["flags"] == [] and out["unchecked"][0]["metric"] == "haemoglobin"


def test_unknown_unit_is_not_guessed():
    out = run({"haemoglobin": "9.8 furlongs"}, 24)
    assert out["unchecked"] and not out["flags"]


def test_duplicate_metric_is_reported():
    out = run({"Hb": 9.8, "haemoglobin": 11.9}, 24)
    assert out["flags"][0]["value"] == 9.8
    assert "duplicate" in out["unchecked"][0]["note"]


def test_week_as_text():
    assert run({"haemoglobin": 9.8}, "24 weeks")["trimester"] == 2


# ---------------------------------------------------------------- escalation, week, audit
@pytest.mark.parametrize("hb, urgent", [(4.0, True), (6.9, True), (7.0, False), (9.8, False)])
def test_urgent_only_below_the_who_severe_cutoff(hb, urgent):
    flag = run({"haemoglobin": hb}, 24)["flags"][0]
    assert flag.get("urgent", False) is urgent


def test_week_sensitive_only_next_to_a_trimester_change():
    sensitive = [w for w in range(10, 32) if run({"haemoglobin": 10.7}, w)["week_sensitive"]]
    assert sensitive == [13, 14, 27, 28]


@pytest.mark.parametrize("week", [24, 60])
def test_every_output_carries_the_ranges_version(week):
    assert run({"haemoglobin": 9.8}, week)["ranges_version"] == RANGES_VERSION


def test_tool_module_has_no_ai_dependencies():
    """'No AI inside' is enforced, not claimed: importing the tool loads no model SDK."""
    code = "import sys, amma_tool; bad=[m for m in sys.modules if m.split('.')[0] in ('openai','azure')]; print(bad)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
