"""Amma Care — Challenge 4: publish both agents, wire them into a Foundry workflow."""
import json
import sys
import time
from pathlib import Path

from amma_agents import (check_values, CHECK_VALUES_TOOL, ANALYSER_INSTRUCTIONS,
                         PLANNER_INSTRUCTIONS, PROJECT, MODEL, REPORTS_PATH)

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, WorkflowAgentDefinition
from azure.identity import DefaultAzureCredential

ANALYSER = "amma-analyser"
PLANNER = "amma-planner"
WORKFLOW = "amma-care-workflow"


def ensure_agents_deployed():
    """Both agents already exist from Sep 17 — reuse them, create only if missing."""
    print("=== Step 1: Ensure agents are deployed ===")
    client = AIProjectClient(endpoint=PROJECT, credential=DefaultAzureCredential())
    existing = {a.name for a in client.agents.list()}

    if ANALYSER not in existing:
        client.agents.create_version(agent_name=ANALYSER, definition=PromptAgentDefinition(
            model=MODEL, instructions=ANALYSER_INSTRUCTIONS, tools=[CHECK_VALUES_TOOL]))
        print(f"  Deployed: {ANALYSER}")
    else:
        print(f"  Found existing: {ANALYSER}")

    if PLANNER not in existing:
        client.agents.create_version(agent_name=PLANNER, definition=PromptAgentDefinition(
            model=MODEL, instructions=PLANNER_INSTRUCTIONS))
        print(f"  Deployed: {PLANNER}")
    else:
        print(f"  Found existing: {PLANNER}")

    client.close()


def create_workflow_agent():
    """Create the [Analyser] -> [Planner] -> [End] workflow. Appears in the portal."""
    print("\n=== Step 2: Create the workflow agent ===")
    client = AIProjectClient(endpoint=PROJECT, credential=DefaultAzureCredential(),
                             allow_preview=True)
    workflow_yaml = (
        "kind: Workflow\n"
        f"name: {WORKFLOW}\n"
        "description: Amma Care - analyse a blood report, then plan doctor questions\n"
        "trigger:\n"
        "  kind: OnConversationStart\n"
        "  id: trigger_start\n"
        "  actions:\n"
        "    - kind: InvokeAzureAgent\n"
        "      id: step_analyse\n"
        "      agent:\n"
        f"        name: {ANALYSER}\n"
        "      conversationId: =System.ConversationId\n"
        "      input:\n"
        '        messages: ""\n'
        "      output:\n"
        "        autoSend: true\n"
        "    - kind: InvokeAzureAgent\n"
        "      id: step_plan\n"
        "      agent:\n"
        f"        name: {PLANNER}\n"
        "      conversationId: =System.ConversationId\n"
        "      input:\n"
        '        messages: ""\n'
        "      output:\n"
        "        autoSend: true\n"
        "    - kind: EndConversation\n"
        "      id: step_end\n"
    )
    result = client.agents.create_version(
        agent_name=WORKFLOW,
        definition=WorkflowAgentDefinition(workflow=workflow_yaml),
        description="Amma Care workflow: Analyser -> Planner -> End")
    print(f"  Created: {result.name} (version {result.version})")
    print("  Visible in the portal: Build -> Agents -> Workflows")
    client.close()
    return result.name


def build_input():
    """Run check_values in PYTHON first, then hand the tool's exact output to the workflow.

    The portal cannot run Python. The lab works around that by telling the agent to
    analyse raw numbers itself - we do NOT do that, because then the model would be
    deciding the ranges. Instead the tool runs here and the agents only read its result.
    """
    reports = json.loads(REPORTS_PATH.read_text())["reports"]
    blocks = []
    for r in reports:
        tool_output = check_values(r["values"], r["week"])       # TOOL RUNS HERE, IN PYTHON
        blocks.append(f"Report {r['report_id']} for {r['mother']}, week {r['week']}.\n"
                      f"check_values() returned:\n{tool_output}")
    return ("The check_values tool has ALREADY been run for each report below and its exact "
            "output is included. Do NOT call the tool again. Use only these numbers and ranges - "
            "never state a range of your own.\n\n" + "\n\n".join(blocks))


def run_workflow(workflow_name):
    """Invoke the workflow and wait for both agents to finish."""
    print(f"\n=== Step 3: Run the workflow: {workflow_name} ===")
    print("  Steps:  1. amma-analyser (explains the tool's flags)")
    print("          2. amma-planner  (three questions for the doctor)")

    client = AIProjectClient(endpoint=PROJECT, credential=DefaultAzureCredential(),
                             allow_preview=True)
    openai_client = client.get_openai_client()
    conversation = openai_client.conversations.create()

    print("\n  Submitting run...")
    resp = openai_client.responses.create(
        conversation=conversation.id,
        extra_body={"agent_reference": {"name": workflow_name, "type": "agent_reference"}},
        input=build_input(),
        background=True)
    print(f"  Response ID: {resp.id}")

    output_text = ""
    for attempt in range(12):
        time.sleep(8)
        r = openai_client.responses.retrieve(resp.id)
        print(f"  [{attempt + 1}] status={r.status}")
        if r.status in ("completed", "failed", "cancelled"):
            output_text = r.output_text
            break

    if output_text:
        print("\n" + "=" * 60)
        print("WORKFLOW OUTPUT")
        print("=" * 60)
        print(output_text)
    else:
        print("\n  No text returned via the API, but the workflow is deployed.")
        print("  Open the portal -> Build -> Agents -> Workflows to run it there.")

    openai_client.conversations.delete(conversation_id=conversation.id)
    client.close()
    return output_text


def main():
    if not PROJECT:
        print("PROJECT_CONNECTION_STRING not set - run Challenge 0 first")
        sys.exit(1)
    ensure_agents_deployed()
    name = create_workflow_agent()
    run_workflow(name)
    print("\n" + "=" * 60)
    print("CHALLENGE 4 COMPLETE — Amma Care")
    print("=" * 60)
    print(f"  Both agents published          OK")
    print(f"  Workflow deployed              OK  ({name})")
    print("  Portal -> Build -> Agents -> Workflows")


if __name__ == "__main__":
    main()
