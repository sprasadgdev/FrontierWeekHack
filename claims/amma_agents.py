"""Amma Care — Analyser + Planner agents on Microsoft Foundry."""
import json
import os
import sys
from pathlib import Path

W = 66   # terminal width for banners

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

# ---------------------------------------------------------------------------
# Language follows the mother, not the developer.
#
# Ordered by anaemia burden in pregnancy, heaviest first. Percentages appear
# only where a published figure exists (WHO global estimates; NFHS-5 for Indian
# states). Everywhere else a band is used rather than an invented number.
# ---------------------------------------------------------------------------
REGION_LANGUAGE = {
    # --- Africa: the heaviest burden anywhere. Sub-Saharan Africa ~57% (WHO) ---
    "Mali":           {"language": "French",     "burden": "59% - highest worldwide"},
    "Burkina Faso":   {"language": "French",     "burden": "very high"},
    "Niger":          {"language": "French",     "burden": "very high"},
    "Senegal":        {"language": "French",     "burden": "very high"},
    "Chad":           {"language": "French",     "burden": "very high"},
    "DR Congo":       {"language": "French",     "burden": "very high"},
    "Nigeria":        {"language": "Hausa",      "burden": "very high"},
    "Ghana":          {"language": "English",    "burden": "very high"},
    "Ethiopia":       {"language": "Amharic",    "burden": "very high"},
    "Tanzania":       {"language": "Swahili",    "burden": "very high"},
    "Kenya":          {"language": "Swahili",    "burden": "high"},
    "Uganda":         {"language": "Swahili",    "burden": "high"},
    "Sudan":          {"language": "Arabic",     "burden": "very high"},

    # --- South Asia: largest absolute numbers. India 52.2% and rising (NFHS-5) ---
    "India":          {"language": "Hindi",      "burden": "52.2% - largest absolute burden"},
    "Bihar":          {"language": "Hindi",      "burden": "63.1% - worst state in India"},
    "Gujarat":        {"language": "Gujarati",   "burden": "above 60%"},
    "West Bengal":    {"language": "Bengali",    "burden": "above 60%"},
    "Odisha":         {"language": "Odia",       "burden": "above 60%"},
    "Tripura":        {"language": "Bengali",    "burden": "above 60%"},
    "Assam":          {"language": "Assamese",   "burden": "above national average"},
    "Uttar Pradesh":  {"language": "Hindi",      "burden": "above national average"},
    "Madhya Pradesh": {"language": "Hindi",      "burden": "above national average"},
    "Rajasthan":      {"language": "Hindi",      "burden": "above national average"},
    "Jharkhand":      {"language": "Hindi",      "burden": "above national average"},
    "Maharashtra":    {"language": "Marathi",    "burden": "high"},
    "Telangana":      {"language": "Telugu",     "burden": "high"},
    "Andhra Pradesh": {"language": "Telugu",     "burden": "high"},
    "Karnataka":      {"language": "Kannada",    "burden": "high"},
    "Tamil Nadu":     {"language": "Tamil",      "burden": "moderate"},
    "Punjab":         {"language": "Punjabi",    "burden": "moderate"},
    "Kerala":         {"language": "Malayalam",  "burden": "lowest in India"},
    "Bangladesh":     {"language": "Bengali",    "burden": "very high"},
    "Pakistan":       {"language": "Urdu",       "burden": "very high"},
    "Afghanistan":    {"language": "Dari",       "burden": "very high"},
    "Nepal":          {"language": "Nepali",     "burden": "high"},
    "Sri Lanka":      {"language": "Sinhala",    "burden": "moderate"},

    # --- Middle East ---
    "Yemen":          {"language": "Arabic",     "burden": "very high"},
    "Egypt":          {"language": "Arabic",     "burden": "high"},

    # --- Southeast Asia ---
    "Indonesia":      {"language": "Indonesian", "burden": "high"},
    "Myanmar":        {"language": "Burmese",    "burden": "high"},
    "Cambodia":       {"language": "Khmer",      "burden": "high"},
    "Laos":           {"language": "Lao",        "burden": "high"},
    "Philippines":    {"language": "Filipino",   "burden": "moderate"},
    "Vietnam":        {"language": "Vietnamese", "burden": "around 25%"},

    # --- Latin America and the Caribbean ---
    "Haiti":          {"language": "French",     "burden": "very high"},
    "Bolivia":        {"language": "Spanish",    "burden": "high"},
    "Guatemala":      {"language": "Spanish",    "burden": "high"},
    "Peru":           {"language": "Spanish",    "burden": "high"},
    "Brazil":         {"language": "Portuguese", "burden": "moderate"},
}

# Country dialling code -> language, for the WhatsApp / SMS path.
PHONE_CODE_LANGUAGE = {
    "+91": "Hindi", "+880": "Bengali", "+92": "Urdu", "+977": "Nepali",
    "+234": "Hausa", "+223": "French", "+251": "Amharic", "+255": "Swahili",
    "+84": "Vietnamese", "+62": "Indonesian", "+63": "Filipino", "+20": "Arabic",
}

# Device / browser locale -> language, for the web path.
LOCALE_LANGUAGE = {
    "hi": "Hindi", "bn": "Bengali", "ml": "Malayalam", "ta": "Tamil",
    "te": "Telugu", "gu": "Gujarati", "mr": "Marathi", "or": "Odia",
    "pa": "Punjabi", "as": "Assamese", "kn": "Kannada", "si": "Sinhala",
    "ur": "Urdu", "ne": "Nepali", "sw": "Swahili", "am": "Amharic",
    "ar": "Arabic", "fr": "French", "es": "Spanish", "pt": "Portuguese",
    "vi": "Vietnamese", "id": "Indonesian", "my": "Burmese", "km": "Khmer",
    "lo": "Lao", "fil": "Filipino", "ha": "Hausa", "en": "English",
}

DEFAULT_LANGUAGE = "English"   # safest fallback when no signal is available


def detect_language(chosen: str = "", locale: str = "", phone: str = "",
                    region: str = "", clinic_region: str = "") -> tuple:
    """
    Resolve the mother's language from the strongest signal available.
    Returns (language, which_signal_decided) so the choice is always explainable.

    In production these arrive from the channel: the web request's Accept-Language
    header, or the WhatsApp sender's number. Here they come from the record so the
    behaviour can be tested. IP geolocation is deliberately not used - it is less
    accurate than the device locale and it is personal data under DPDP and GDPR.
    """
    if chosen:
        return chosen, "she chose it"

    if locale:
        code = locale.split("-")[0].lower()
        if code in LOCALE_LANGUAGE:
            return LOCALE_LANGUAGE[code], f"device locale ({locale})"

    if phone:
        digits = phone.replace(" ", "")
        for code in sorted(PHONE_CODE_LANGUAGE, key=len, reverse=True):
            if digits.startswith(code):
                return PHONE_CODE_LANGUAGE[code], f"phone country code ({code})"

    if region in REGION_LANGUAGE:
        return REGION_LANGUAGE[region]["language"], f"her region ({region})"

    if clinic_region in REGION_LANGUAGE:
        return REGION_LANGUAGE[clinic_region]["language"], f"clinic region ({clinic_region})"

    return DEFAULT_LANGUAGE, "fallback - no signal available"


# --------------------------- terminal presentation -------------------------

def banner(title: str, subtitle: str = "") -> None:
    print("\n" + "=" * W)
    print(f"  {title}")
    if subtitle:
        print(f"  {subtitle}")
    print("=" * W)


def step(number: str, title: str, note: str = "") -> None:
    print(f"\n\nSTEP {number}  ·  {title}")
    if note:
        print(f"           {note}")
    print("-" * W)


def document(label: str, meta: str, body: str) -> None:
    print(f"\n  {label}")
    print(f"  {meta}")
    print("  " + "·" * (W - 4))
    for line in body.splitlines():
        print(f"  {line}" if line.strip() else "")
    print("  " + "·" * (W - 4))


def print_language_table() -> None:
    """Where MotherWell can already speak, ordered as written: heaviest burden first."""
    banner("MOTHERWELL  ·  language coverage",
           "language follows the mother, not the developer")
    print(f"\n  {'REGION':<17}{'LANGUAGE':<13}ANAEMIA IN PREGNANCY")
    print("  " + "-" * (W - 4))
    for region, info in REGION_LANGUAGE.items():
        print(f"  {region:<17}{info['language']:<13}{info['burden']}")
    print("  " + "-" * (W - 4))
    langs = sorted({i["language"] for i in REGION_LANGUAGE.values()})
    print(f"\n  {len(REGION_LANGUAGE)} regions   ·   {len(langs)} languages")
    print(f"  {', '.join(langs)}")
    print("\n  Signals used to pick one, strongest first:")
    print("    1. she chose it        2. device locale       3. phone country code")
    print("    4. her region          5. clinic region       6. English\n")


# ------------------------------- the tool ----------------------------------

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

Write ONE plan, entirely in the language named in the request. Use no other language anywhere.

The plan has exactly four parts, in this order, with the part names written in that same language:

  Questions for your doctor - exactly three specific questions based on the flags
  Food note - one sentence
  Next check - when to re-test, in weeks
  Then this sentence, translated into that language:
      "This is information, not medical advice. Please discuss with your doctor."

Write it as plain text a worried mother can read on a phone. Do not use markdown headings.
Never print words taken from these instructions, such as "closing line" or "plan".
If the flags list is empty, say so kindly and still give one general question and the final sentence.
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
    if "--languages" in sys.argv:
        print_language_table()
        return

    if "--tool-only" in sys.argv:
        banner("MOTHERWELL  ·  the tool alone",
               "plain Python. No AI. Same numbers in, same flags out.")
        # Messy names on purpose: proves normalisation works and that an unknown
        # metric is reported, not silently dropped.
        print(check_values({"haemoglobin": 9.8, "fasting glucose": 105,
                            "Ferritin": 18, "platelets": 150}, 24))
        return

    if not PROJECT:
        print("PROJECT_CONNECTION_STRING not set - run Challenge 0 first")
        sys.exit(1)

    banner("MOTHERWELL  ·  Amma Care",
           "a blood report in  ·  three questions for her doctor out")

    client, openai = make_client()
    reports = json.loads(REPORTS_PATH.read_text())["reports"]

    step("1", "ANALYSER", "reads the tool. Never states a range from memory.")
    analyser = create_agent(client, "amma-analyser", ANALYSER_INSTRUCTIONS, tools=[CHECK_VALUES_TOOL])
    print(f"  created {analyser.name} v{analyser.version}")

    analyses = []
    for r in reports:
        text = (f"Report {r['report_id']} for {r['mother']}, week {r['week']}. "
                f"Values: {json.dumps(r['values'])}. Use check_values and explain the flags.")
        out = run_agent(openai, analyser, text)
        document(f"{r['report_id']}  ·  {r['mother']}",
                 f"week {r['week']}  ·  {r.get('region', 'unknown region')}", out)
        lang, why = detect_language(chosen=r.get("language", ""),
                                    locale=r.get("locale", ""),
                                    phone=r.get("phone", ""),
                                    region=r.get("region", ""),
                                    clinic_region=r.get("clinic_region", ""))
        analyses.append((r["report_id"], r.get("region", ""), lang, why,
                         f"{r['report_id']} (week {r['week']}):\n{out}"))

    step("2", "ROUTING", "language follows the mother, not the developer.")
    for report_id, region, lang, why, _ in analyses:
        print(f"  {report_id}   {region:<15} ->  {lang:<12}  [{why}]")

    step("3", "PLANNER", "three questions for her doctor. One document per reader.")
    planner = create_agent(client, "amma-planner", PLANNER_INSTRUCTIONS)
    print(f"  created {planner.name} v{planner.version}")

    # One clean document per reader. She never scrolls past a language she cannot read.
    for report_id, region, lang, why, a in analyses:
        mother = run_agent(openai, planner, f"Write the plan in {lang}.\n\nAnalyser flags:\n" + a)
        document("FOR THE MOTHER", f"{report_id}  ·  {region}  ·  {lang}  ·  {why}", mother)

        doctor = run_agent(openai, planner, f"Write the plan in English.\n\nAnalyser flags:\n" + a)
        document("FOR HER DOCTOR", f"{report_id}  ·  English", doctor)

    banner("DONE", "the AI explained. The Python decided.")
    client.close()


if __name__ == "__main__":
    main()
