"""Amma Care — Analyser + Planner agents on Microsoft Foundry."""
import json
import os
import sys
from pathlib import Path

# teaching ranges (simplified; verify with a clinician before any real use)
RANGES = {
    "haemoglobin":     {"unit": "g/dL",  "min": {1: 11.0, 2: 10.5, 3: 11.0}, "max": 15.0},
    "fasting_glucose": {"unit": "mg/dL", "min": 60,                         "max": 92},
    "tsh":             {"unit": "mIU/L", "min": 0.1,                        "max": {1: 2.5, 2: 3.0, 3: 3.0}},
    "ferritin":        {"unit": "ng/mL", "min": 30,                         "max": 300},
    "vitamin_d":       {"unit": "ng/mL", "min": 20,                         "max": 100},
}

# The model writes metric names the way a human would ("Fasting Glucose", "Hb").
# Map those to our exact keys so no value is ever dropped.
ALIASES = {
    "hemoglobin": "haemoglobin", "hb": "haemoglobin", "hgb": "haemoglobin",
    "glucose": "fasting_glucose", "fasting_blood_sugar": "fasting_glucose",
    "fbs": "fasting_glucose", "blood_sugar": "fasting_glucose",
    "vit_d": "vitamin_d", "vitamin_d3": "vitamin_d", "25_oh_vitamin_d": "vitamin_d",
    "thyroid": "tsh", "serum_ferritin": "ferritin",
}

def normalise(metric: str) -> str:
    """'Fasting Glucose' / 'fasting-glucose' / 'FBS' -> 'fasting_glucose'."""
    key = "_".join(metric.strip().lower().replace("-", " ").split())
    return ALIASES.get(key, key)

def trimester(week: int) -> int:
    if week <= 13:
        return 1
    if week <= 27:
        return 2
    return 3

def _bound(b, tri):
    return b[tri] if isinstance(b, dict) else b

def check_values(values: dict, week: int) -> str:
    """Compare each blood value with the range for this trimester. Deterministic. No AI inside."""
    tri = trimester(week)
    result = {"week": week, "trimester": tri, "flags": [], "unchecked": [], "all_values": {}}
    for raw_metric, value in values.items():
        metric = normalise(raw_metric)
        if metric not in RANGES:
            # Never drop a value silently - say out loud that it was not checked.
            result["unchecked"].append({"metric": raw_metric, "value": value,
                                        "note": "NOT a known metric - this value was NOT checked"})
            continue
        low = _bound(RANGES[metric]["min"], tri)
        high = _bound(RANGES[metric]["max"], tri)
        in_range = low <= value <= high
        result["all_values"][metric] = {"value": value, "unit": RANGES[metric]["unit"],
                                        "min": low, "max": high, "in_range": in_range}
        if not in_range:
            result["flags"].append({"metric": metric, "value": value, "unit": RANGES[metric]["unit"],
                                    "min": low, "max": high,
                                    "status": "LOW" if value < low else "HIGH"})
    return json.dumps(result, indent=2)

from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from openai.types.responses.response_input_param import FunctionCallOutput

load_dotenv(Path(__file__).resolve().parent / ".env")
PROJECT = os.getenv("PROJECT_CONNECTION_STRING")
MODEL = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-5.4")
REPORTS_PATH = Path(__file__).resolve().parent / "blood_reports.json"

CHECK_VALUES_TOOL = FunctionTool(
    name="check_values",
    description="Compare a pregnant woman's blood values with the expected range for her trimester. "
                "Returns flags for values outside range. Always call this before commenting on any number.",
    parameters={
        "type": "object",
        "properties": {
            "values": {"type": "object", "description":
                       "metric name -> number. Use exactly these names: haemoglobin, fasting_glucose, "
                       "tsh, ferritin, vitamin_d. Example: {\"haemoglobin\": 9.8, \"fasting_glucose\": 105}"},
            "week": {"type": "integer", "description": "pregnancy week, 1-42"},
        },
        "required": ["values", "week"],
        "additionalProperties": False,
    },
    strict=False,
)

ANALYSER_INSTRUCTIONS = """
You are the Analyser for MotherWell, a helper for pregnant women reading a blood report.
When given blood values and a pregnancy week, ALWAYS call the check_values tool first.
Report only what the tool returns: for each flagged value give the value, the expected range for this
trimester, and whether it is LOW or HIGH, in plain words a worried mother can understand.
If the tool returns anything under "unchecked", say clearly that those values were NOT checked.
Never state a range from your own memory. Never diagnose a condition. Never recommend medicine.
If all values are in range, say so kindly. Be short and structured.
"""

PLANNER_INSTRUCTIONS = """
You are the Planner for MotherWell. You receive the Analyser's report of flagged blood values.
You never see the raw blood report, only what the Analyser received from the tool,
and you must never introduce a number the tool did not produce.
Write for the mother:
QUESTIONS FOR YOUR DOCTOR: exactly three specific questions based on the flags.
FOOD NOTE: one sentence.
NEXT CHECK: when to re-test, in weeks.
End every reply with: "This is information, not medical advice. Please discuss with your doctor."
If the flags list is empty, say so and still give one general question and the closing line.
"""

def make_client():
    client = AIProjectClient(endpoint=PROJECT, credential=DefaultAzureCredential())
    return client, client.get_openai_client()

def create_agent(client, name, instructions, tools=None):
    definition = PromptAgentDefinition(model=MODEL, instructions=instructions,
                                       **({"tools": tools} if tools else {}))
    return client.agents.create_version(agent_name=name, definition=definition)

def run_agent(openai, agent, text):
    conv = openai.conversations.create()
    ref = {"agent_reference": {"name": agent.name, "type": "agent_reference"}}
    response = openai.responses.create(input=text, conversation=conv.id, extra_body=ref)
    while True:
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            if call.name == "check_values":
                args = json.loads(call.arguments)
                result = check_values(args["values"], int(args["week"]))     # TOOL RUNS HERE
            else:
                result = json.dumps({"error": f"unknown tool {call.name}"})
            outputs.append(FunctionCallOutput(type="function_call_output", call_id=call.call_id, output=result))
        response = openai.responses.create(input=outputs, conversation=conv.id, extra_body=ref)
    openai.conversations.delete(conversation_id=conv.id)
    return response.output_text


def main():
    if "--tool-only" in sys.argv:
        # Messy names on purpose: proves normalisation works and that an unknown
        # metric is reported, not silently dropped.
        print(check_values({"haemoglobin": 9.8, "fasting glucose": 105,
                            "Ferritin": 18, "platelets": 150}, 24))
        return
    if not PROJECT:
        print("PROJECT_CONNECTION_STRING not set - run Challenge 0 first"); sys.exit(1)

    client, openai = make_client()
    reports = json.loads(REPORTS_PATH.read_text())["reports"]

    print("=== Analyser ===")
    analyser = create_agent(client, "amma-analyser", ANALYSER_INSTRUCTIONS, tools=[CHECK_VALUES_TOOL])
    print(f"created {analyser.name} v{analyser.version}")
    analyses = []
    for r in reports:
        text = (f"Report {r['report_id']} for {r['mother']}, week {r['week']}. "
                f"Values: {json.dumps(r['values'])}. Use check_values and explain the flags.")
        out = run_agent(openai, analyser, text)
        print(f"\n--- {r['report_id']} ---\n{out}")
        analyses.append(f"{r['report_id']} (week {r['week']}):\n{out}")

    print("\n=== Planner ===")
    planner = create_agent(client, "amma-planner", PLANNER_INSTRUCTIONS)
    print(f"created {planner.name} v{planner.version}")
    for a in analyses:
        out = run_agent(openai, planner, "Analyser flags:\n" + a)
        print(f"\n--- plan ---\n{out}")

    client.close()

if __name__ == "__main__":
    main()
