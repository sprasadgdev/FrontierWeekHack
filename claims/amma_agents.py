"""Amma Care — Analyser + Planner agents on Microsoft Foundry."""
import json
import os
import re
import sys
from pathlib import Path

# FIX: the tool now lives in its own module with zero AI imports (tests prove it).
from amma_tool import RANGES, check_values, normalise, trimester  # noqa: F401  (re-exported)

W = 66  # terminal width for banners

# ---------------------------------------------------------------------------
# Language follows the mother, not the developer.
#
# Ordered by anaemia burden in pregnancy, heaviest first. Percentages appear
# only where a published figure exists (WHO global estimates; NFHS-5 for Indian
# states). Everywhere else a band is used rather than an invented number.
# ---------------------------------------------------------------------------
REGION_LANGUAGE = {
    # --- Africa: the heaviest burden anywhere. Sub-Saharan Africa ~57% ---
    "Mali":          {"language": "French",     "burden": "about 59% - highest worldwide (2019)"},
    "Burkina Faso":  {"language": "French",     "burden": "very high"},
    "Niger":         {"language": "French",     "burden": "very high"},
    "Senegal":       {"language": "French",     "burden": "very high"},
    "Chad":          {"language": "French",     "burden": "very high"},
    "DR Congo":      {"language": "French",     "burden": "very high"},
    "Nigeria":       {"language": "Hausa",      "burden": "very high"},
    "Ghana":         {"language": "English",    "burden": "very high"},
    "Ethiopia":      {"language": "Amharic",    "burden": "very high"},
    "Tanzania":      {"language": "Swahili",    "burden": "very high"},
    "Kenya":         {"language": "Swahili",    "burden": "high"},
    "Uganda":        {"language": "Swahili",    "burden": "high"},
    "Sudan":         {"language": "Arabic",     "burden": "very high"},
    # --- South Asia: largest absolute numbers. India 52.2% and rising (NFHS-5) ---
    "India":         {"language": "Hindi",      "burden": "52.2% - largest absolute burden"},
    # FIX: NFHS-5 wording. Bihar and Kerala are the extremes among LARGE states.
    "Bihar":         {"language": "Hindi",      "burden": "63.1% - highest of the large states"},
    "Gujarat":       {"language": "Gujarati",   "burden": "above 60%"},
    "West Bengal":   {"language": "Bengali",    "burden": "above 60%"},
    "Odisha":        {"language": "Odia",       "burden": "above 60%"},
    "Tripura":       {"language": "Bengali",    "burden": "above 60%"},
    "Assam":         {"language": "Assamese",   "burden": "above national average"},
    "Uttar Pradesh": {"language": "Hindi",      "burden": "above national average"},
    "Madhya Pradesh": {"language": "Hindi",     "burden": "above national average"},
    "Rajasthan":     {"language": "Hindi",      "burden": "above national average"},
    "Jharkhand":     {"language": "Hindi",      "burden": "above national average"},
    "Maharashtra":   {"language": "Marathi",    "burden": "high"},
    "Telangana":     {"language": "Telugu",     "burden": "high"},
    "Andhra Pradesh": {"language": "Telugu",    "burden": "high"},
    "Karnataka":     {"language": "Kannada",    "burden": "high"},
    "Tamil Nadu":    {"language": "Tamil",      "burden": "moderate"},
    "Punjab":        {"language": "Punjabi",    "burden": "moderate"},
    "Kerala":        {"language": "Malayalam",  "burden": "31.4% - lowest of the large states"},
    "Bangladesh":    {"language": "Bengali",    "burden": "very high"},
    "Pakistan":      {"language": "Urdu",       "burden": "very high"},
    "Afghanistan":   {"language": "Dari",       "burden": "very high"},
    "Nepal":         {"language": "Nepali",     "burden": "high"},
    "Sri Lanka":     {"language": "Sinhala",    "burden": "moderate"},
    # --- Middle East ---
    "Yemen":         {"language": "Arabic",     "burden": "very high"},
    "Egypt":         {"language": "Arabic",     "burden": "high"},
    # --- Southeast Asia ---
    "Indonesia":     {"language": "Indonesian", "burden": "high"},
    "Myanmar":       {"language": "Burmese",    "burden": "high"},
    "Cambodia":      {"language": "Khmer",      "burden": "high"},
    "Laos":          {"language": "Lao",        "burden": "high"},
    "Philippines":   {"language": "Filipino",   "burden": "moderate"},
    "Vietnam":       {"language": "Vietnamese", "burden": "around 25%"},
    # --- Latin America and the Caribbean ---
    "Haiti":         {"language": "French",     "burden": "very high"},
    "Bolivia":       {"language": "Spanish",    "burden": "high"},
    "Guatemala":     {"language": "Spanish",    "burden": "high"},
    "Peru":          {"language": "Spanish",    "burden": "high"},
    "Brazil":        {"language": "Portuguese", "burden": "moderate"},
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

DEFAULT_LANGUAGE = "English"  # safest fallback when no signal is available


def detect_language(chosen: str = "", locale: str = "", phone: str = "",
                    region: str = "", clinic_region: str = "") -> tuple:
    """
    Resolve the mother's language from the strongest signal available.
    Returns (language, which_signal_decided, certain) so the choice is always explainable.

    Only her own choice and her device's language setting are certain. A phone country
    code or a region is a guess: +91 alone covers more than twenty languages. When the
    language is a guess, her document opens by telling her so and inviting her to change it.

    In production these arrive from the channel: the web request's Accept-Language
    header, or the WhatsApp sender's number. Here they come from the record so the
    behaviour can be tested. IP geolocation is deliberately not used - it is less
    accurate than the device locale and it is personal data under DPDP and GDPR.
    """
    if chosen:
        return chosen, "she chose it", True
    if locale:
        code = locale.split("-")[0].lower()
        if code in LOCALE_LANGUAGE:
            return LOCALE_LANGUAGE[code], f"device locale ({locale})", True
    if phone:
        digits = phone.replace(" ", "")
        for code in sorted(PHONE_CODE_LANGUAGE, key=len, reverse=True):
            if digits.startswith(code):
                lang = PHONE_CODE_LANGUAGE[code]
                # FIX: a country code is coarser than her region. In a multilingual
                # country (+91 covers many languages) her region wins when they disagree,
                # so a Kerala mother with an Indian number gets Malayalam, not Hindi.
                if region in REGION_LANGUAGE and REGION_LANGUAGE[region]["language"] != lang:
                    return (REGION_LANGUAGE[region]["language"],
                            f"her region ({region}), more specific than {code}", False)
                return lang, f"phone country code ({code})", False
    if region in REGION_LANGUAGE:
        return REGION_LANGUAGE[region]["language"], f"her region ({region})", False
    if clinic_region in REGION_LANGUAGE:
        return REGION_LANGUAGE[clinic_region]["language"], f"clinic region ({clinic_region})", False
    return DEFAULT_LANGUAGE, "fallback - no signal available", False


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
    print(f"\n  {len(REGION_LANGUAGE)} regions  ·  {len(langs)} languages")
    print(f"  {', '.join(langs)}")
    print("\n  Signals used to pick one, strongest first:")
    print("    1. she chose it   2. device locale   3. phone country code")
    print("    4. her region     5. clinic region   6. English")
    print("    (her region overrides a country code when the two disagree)")
    print("    1 and 2 are certain. 3 to 6 are a guess, so her document asks her to confirm.\n")


def print_routing() -> None:
    """Show the language decision for each report. Pure Python - no Azure, no cost.
    Her name is printed here, on this machine only. It is never sent to the model."""
    banner("MOTHERWELL  ·  routing",
           "language follows the mother, not the developer")
    reports = json.loads(REPORTS_PATH.read_text())["reports"]
    print(f"\n  {'REPORT':<9}{'MOTHER':<11}{'REGION':<16}{'LANGUAGE':<13}DECIDED BY")
    print("  " + "-" * (W - 4))
    for r in reports:
        lang, why, certain = detect_language(chosen=r.get("language", ""),
                                             locale=r.get("locale", ""),
                                             phone=r.get("phone", ""),
                                             region=r.get("region", ""),
                                             clinic_region=r.get("clinic_region", ""))
        print(f"  {r['report_id']:<9}{r['mother']:<11}{r.get('region', '-'):<16}{lang:<13}{why}"
              + ("" if certain else "  a guess - she is asked to confirm"))
    print("  " + "-" * (W - 4))
    print("\n  Signals, strongest first:")
    print("    1. she chose it     2. device locale     3. phone country code")
    print("    4. her region       5. clinic region     6. English")
    print("    1 and 2 are certain. 3 to 6 are a guess, so her document asks her to confirm.")
    print("\n  IP geolocation is deliberately not used: less accurate than the")
    print("  device locale, and personal data under DPDP and GDPR.\n")


# ------------------------------- Azure wiring -------------------------------
# FIX: Azure imports are optional, so --tool-only and --languages run with no SDK.
try:
    from dotenv import load_dotenv
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
    from azure.identity import DefaultAzureCredential
    AZURE_READY = True
except ImportError:
    AZURE_READY = False

if AZURE_READY:
    load_dotenv(Path(__file__).resolve().parent / ".env")
PROJECT = os.getenv("PROJECT_CONNECTION_STRING")
MODEL = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-5.4")
REPORTS_PATH = Path(__file__).resolve().parent / "blood_reports.json"
MAX_TOOL_ROUNDS = 5  # FIX: the tool loop can no longer spin forever

CHECK_VALUES_TOOL = FunctionTool(
    name="check_values",
    description="Compare a pregnant woman's blood values with the expected range for her trimester. "
                "Returns flags for values outside range. Always call this before commenting on any number.",
    parameters={
        "type": "object",
        "properties": {
            "values": {"type": "object", "description":
                       "metric name -> value copied exactly as printed, WITH its unit when the report shows one. "
                       "Use these names: haemoglobin, fasting_glucose, tsh, ferritin, vitamin_d. "
                       "Example: {\"haemoglobin\": \"98 g/L\", \"fasting_glucose\": \"105 mg/dL\"}. "
                       "Pass any other test under its own name; it will be reported as unchecked."},
            "week": {"type": "integer", "description": "pregnancy week, 1-42"},
        },
        "required": ["values", "week"],
        "additionalProperties": False,
    },
    strict=False,
) if AZURE_READY else None

ANALYSER_INSTRUCTIONS = """
You are the Analyser for MotherWell, a helper for pregnant women reading a blood report.
When given blood values and a pregnancy week, ALWAYS call the check_values tool first.
Copy every value exactly as printed on the report, including its unit.
Report only what the tool returns: for each flagged value give the value, the expected range for this
trimester, and whether it is LOW or HIGH, in plain words a worried mother can understand.
If the tool returns anything under "unchecked", or an "error", say clearly that those values were NOT checked.
If any flag has "urgent": true, say that first, plainly: she should contact her doctor or health worker today.
If anything is listed under "week_sensitive", say the doctor should confirm her pregnancy week for that value.
Do not mention ranges_version; it is for the audit trail, not for her.
Never state a range from your own memory. Never diagnose a condition. Never recommend medicine.
If all values are in range, say so kindly. Be short and structured.
"""

PLANNER_INSTRUCTIONS = """
You are the Planner for MotherWell. You receive only what the check_values tool returned:
the pregnancy week, the trimester, each flagged value with its unit, range and LOW or HIGH,
anything the tool could not check, and any week-sensitive result.
You never see the blood report itself, any value that was in range, or the mother's name.
Never introduce a number the tool did not produce. That includes time: never say when to re-test
or how many days or weeks to wait. Only her doctor decides that.
Write ONE plan, entirely in the language named in the request. Use no other language anywhere.
If any flag has "urgent": true, begin with this sentence, translated into that language:
  "Please contact your doctor or health worker today. Do not wait for your next appointment."
If the request says her language was guessed, next write one short line saying this language was
chosen for her, and she can ask for a different one.
Then the plan has exactly four parts, in this order, with the part names written in that same language:
  Questions for your doctor - exactly three specific questions based on the flags
  Food note - one general sentence about everyday food. Never name a supplement, medicine, dose or amount.
  Next check - one sentence asking her doctor when these tests should be repeated. No number of days or weeks.
  Then this sentence, translated into that language:
  "This is information, not medical advice. Please discuss with your doctor."
If anything is listed as unchecked, one question must ask the doctor to look at those values.
If anything is listed as week_sensitive, one question must ask the doctor to confirm her pregnancy week.
Write it as plain text a worried mother can read on a phone. Do not use markdown headings.
Never print words taken from these instructions, such as "closing line" or "plan".
If the flags list is empty, say so kindly and still give one general question and the final sentence.
Never diagnose. If asked "do I have ..." or similar, say that only her doctor can answer and turn it into a question for the doctor.
"""

FAIL_CLOSED = ("MotherWell could not check these values safely, so it will not comment on them. "
               "Please show the report to your doctor.")


def make_client():
    client = AIProjectClient(endpoint=PROJECT, credential=DefaultAzureCredential())
    return client, client.get_openai_client()


def create_agent(client, name, instructions, tools=None):
    definition = PromptAgentDefinition(model=MODEL, instructions=instructions,
                                       **({"tools": tools} if tools else {}))
    return client.agents.create_version(agent_name=name, definition=definition)


def run_agent(openai, agent, text):
    """Run one agent. Returns (text, tool_outputs) so the caller can verify the tool was used."""
    conv = openai.conversations.create()
    ref = {"agent_reference": {"name": agent.name, "type": "agent_reference"}}
    response = openai.responses.create(input=text, conversation=conv.id, extra_body=ref)
    tool_outputs = []
    for _ in range(MAX_TOOL_ROUNDS):
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            if call.name == "check_values":
                try:  # FIX: bad arguments return an error to the model instead of crashing
                    args = json.loads(call.arguments)
                    result = check_values(args.get("values", {}), args.get("week"))  # TOOL RUNS HERE
                except Exception as exc:
                    result = json.dumps({"error": f"could not read the tool arguments: {exc}"})
                tool_outputs.append(result)
            else:
                result = json.dumps({"error": f"unknown tool {call.name}"})
            outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": result})
        response = openai.responses.create(input=outputs, conversation=conv.id, extra_body=ref)
    openai.conversations.delete(conversation_id=conv.id)
    return response.output_text, tool_outputs


def ungrounded_numbers(text, tool_outputs):
    """Numbers in the Analyser's text that the tool never produced (groundedness check)."""
    allowed = set(re.findall(r"\d+(?:\.\d+)?", " ".join(tool_outputs)))
    allowed |= {n.rstrip("0").rstrip(".") for n in allowed if "." in n}
    text = re.sub(r"\bR-\d+\b", "", text)   # a report ID such as R-001 is not a clinical number
    found = re.findall(r"\d+(?:\.\d+)?", text)
    def norm(n):
        return n.rstrip("0").rstrip(".") if "." in n else n
    return sorted({n for n in found if n not in allowed and norm(n) not in allowed})


def analyse(openai, analyser, text):
    """FIX: the tool call is ENFORCED by code, not just requested in the prompt.

    If the Analyser answers without calling check_values, the answer is discarded and
    retried once. If it still refuses, MotherWell fails closed and says nothing about numbers.
    Returns (analyser_text, tool_json or None, ungrounded_numbers).
    """
    for attempt in range(2):
        prompt = text if attempt == 0 else (
            text + "\n\nYou answered without calling check_values. Call it now. Do not answer from memory.")
        out, tool_outputs = run_agent(openai, analyser, prompt)
        if tool_outputs:
            return out, tool_outputs[-1], ungrounded_numbers(out, tool_outputs)
    return FAIL_CLOSED, None, []


# The data contract between the tool and the Planner: these fields cross, nothing else.
# Flags carry the flagged value itself (so a question can quote "9.8 g/dL"), but in-range
# values ("all_values"), the report, and her name never cross. tests/test_contract.py enforces it.
PLANNER_FIELDS = ("week", "trimester", "flags", "unchecked", "week_sensitive", "error")


def planner_input(tool_json: str) -> str:
    """FIX: the Planner gets exactly the PLANNER_FIELDS of what the tool returned,
    not the Analyser's prose and not the raw report."""
    data = json.loads(tool_json)
    return json.dumps({k: data.get(k) for k in PLANNER_FIELDS if k in data}, indent=2)


def workflow_input(reports) -> str:
    """The input for the deployed portal workflow.

    The portal cannot run Python, so the tool runs here first. In the portal both agents read
    the same conversation, so only the Planner's view of each result is sent: never the full
    report, never an in-range value, never her name.
    """
    blocks = [f"Report {r['report_id']}.\ncheck_values() returned:\n"
              f"{planner_input(check_values(r['values'], r['week']))}" for r in reports]
    return ("The check_values tool has ALREADY been run for each report below and its exact "
            "output is included. Do NOT call the tool again. Use only these numbers and ranges - "
            "never state a range of your own.\n\n" + "\n\n".join(blocks))


def main():
    if "--languages" in sys.argv:
        print_language_table()
        return

    if "--routing" in sys.argv:
        print_routing()
        return

    if "--tool-only" in sys.argv:
        banner("MOTHERWELL  ·  the tool alone",
               "plain Python. No AI. Same numbers in, same flags out.")
        # Messy names on purpose: proves normalisation works and that an unknown
        # metric is reported, not silently dropped.
        print(check_values({"haemoglobin": 9.8, "fasting glucose": 105,
                            "Ferritin": 18, "platelets": 150}, 24))
        return

    if not AZURE_READY:
        print("Azure SDK not installed - run: pip install -r requirements.txt")
        sys.exit(1)
    if not PROJECT:
        print("PROJECT_CONNECTION_STRING not set - run Challenge 0 first")
        sys.exit(1)

    banner("MOTHERWELL  ·  Amma Care",
           "a blood report in  ·  three questions for her doctor out")
    client, openai = make_client()
    reports = json.loads(REPORTS_PATH.read_text())["reports"]

    step("1", "ANALYSER", "must call the tool. An answer without it is rejected.")
    analyser = create_agent(client, "amma-analyser", ANALYSER_INSTRUCTIONS, tools=[CHECK_VALUES_TOOL])
    print(f"  created {analyser.name} v{analyser.version}")

    analyses = []
    for r in reports:
        # The model is given the report ID, never her name. Her name stays on this
        # machine, in the printed document below. A name adds nothing to a blood
        # range, so sending it would be collecting personal data for no purpose.
        text = (f"Report {r['report_id']}, week {r['week']}. "
                f"Values: {json.dumps(r['values'])}. Use check_values and explain the flags.")
        out, tool_json, stray = analyse(openai, analyser, text)
        document(f"{r['report_id']}  ·  {r['mother']}",
                 f"week {r['week']}  ·  {r.get('region', 'unknown region')}", out)
        print(f"  tool called: {'yes' if tool_json else 'NO - failed closed'}"
              f"   groundedness: {'OK' if not stray else 'numbers not from the tool: ' + ', '.join(stray)}")
        lang, why, certain = detect_language(chosen=r.get("language", ""),
                                             locale=r.get("locale", ""),
                                             phone=r.get("phone", ""),
                                             region=r.get("region", ""),
                                             clinic_region=r.get("clinic_region", ""))
        analyses.append((r["report_id"], r.get("region", ""), lang, why, certain, tool_json))

    step("2", "ROUTING", "language follows the mother, not the developer.")
    for report_id, region, lang, why, certain, _ in analyses:
        print(f"  {report_id}   {region:<15} ->  {lang:<12}  [{why}]"
              + ("" if certain else "  a guess - she is asked to confirm"))

    step("3", "PLANNER", "three questions for her doctor. One document per reader.")
    planner = create_agent(client, "amma-planner", PLANNER_INSTRUCTIONS)
    print(f"  created {planner.name} v{planner.version}")

    # One clean document per reader. She never scrolls past a language she cannot read.
    for report_id, region, lang, why, certain, tool_json in analyses:
        if tool_json is None:
            document("FOR THE MOTHER", f"{report_id}  ·  not checked", FAIL_CLOSED)
            continue
        flags = planner_input(tool_json)
        guessed = "" if certain else " Her language was guessed, not chosen by her."
        mother, _ = run_agent(openai, planner, f"Write the plan in {lang}.{guessed}\n\nTool flags:\n{flags}")
        document("FOR THE MOTHER", f"{report_id}  ·  {region}  ·  {lang}  ·  {why}", mother)
        doctor, _ = run_agent(openai, planner, f"Write the plan in English.\n\nTool flags:\n{flags}")
        document("FOR HER DOCTOR", f"{report_id}  ·  English", doctor)

    banner("DONE", "the AI explained. The Python decided.")
    client.close()


if __name__ == "__main__":
    main()
