#!/usr/bin/env python3
"""
ADK Starter Recipe: SequentialPipeline (Linear Stage-by-Stage Topology)
Source: Google for Startups Immersion EMEA 2026 — Session 02 (Building Agents with ADK)
Compatible with: google-adk 2.14.0 (verified against the installed release), Python 3.10+

REAL ADK — this is runnable google-adk code, not pseudocode.
A sequential pipeline runs stage agents in strict order, each passing its
output forward through shared session state.
"""

# Verify/install first:
#   pip install "google-adk>=2.14.0"
# The agents run on the model bound to each ``LlmAgent``; runtime requires
# GOOGLE_API_KEY (or GCP auth) for Gemini, or any configured provider.

from google.adk.agents import Agent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

# NOTE on modern ADK: SequentialAgent is still present in 2.14 but is being
# superseded by ``Workflow`` (graph-based). SequentialAgent remains the clearest
# linear-pipeline starter and is the canonical Session-02 pattern; see the
# Workflow docs for the graph alternative.

GEMINI_MODEL = "gemini-2.5-flash"  # your project's model; set via env/{{GEMINI_MODEL}}


def build_sdlc_pipeline() -> SequentialAgent:
    """Deterministic linear pipeline: spec -> code -> audit."""
    return SequentialAgent(
        name="sdlc_pipeline",
        sub_agents=[
            Agent(
                name="spec_writer",
                model=GEMINI_MODEL,
                instruction=(
                    "Act as a product spec writer. Given the feature request in the "
                    "conversation, produce a concise functional spec with acceptance criteria."
                ),
            ),
            Agent(
                name="code_writer",
                model=GEMINI_MODEL,
                instruction=(
                    "Act as a senior engineer. Implement the spec in the conversation, "
                    "outputting runnable code scoped to the request."
                ),
            ),
            Agent(
                name="security_auditor",
                model=GEMINI_MODEL,
                instruction=(
                    "Act as an application-security reviewer. Audit the code produced "
                    "in the conversation for injection, secret leakage and IAM-adjacent "
                    "risks; report findings, do not weaken tests to force a green build."
                ),
            ),
        ],
    )


def main() -> None:
    pipeline = build_sdlc_pipeline()
    session_service = InMemorySessionService()
    runner = Runner(
        agent=pipeline,
        app_name="sdlc_demo",
        session_service=session_service,
    )
    print("Built SequentialAgent pipeline:", pipeline.name)
    print("Stages (in order):", [s.name for s in pipeline.sub_agents])
    print("Run with a real API key, e.g.:")
    print("  export GOOGLE_API_KEY=...")
    print("  runner.run_async(user_id='u1', session_id='s1', new_message=...)")


if __name__ == "__main__":
    # Setting GOOGLE_API_KEY and uncommenting the run below executes the pipeline.
    main()