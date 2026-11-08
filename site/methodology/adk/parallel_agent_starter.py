#!/usr/bin/env python3
"""
ADK Starter Recipe: ParallelAgent (Concurrent Fan-Out / Fan-In)
Source: Google for Startups Immersion EMEA 2026 — Session 02 (Building Agents with ADK)
Compatible with: google-adk 2.14.0 (verified against the installed release), Python 3.10+

REAL ADK — this is runnable google-adk code, not pseudocode.
ParallelAgent runs independent specialist sub-agents concurrently (genuine
parallel execution), and ADK gathers their outputs; there is no invented
"MergerAgent" primitive — synthesis is the caller's job.
"""

#   pip install "google-adk>=2.14.0"

from google.adk.agents import Agent, ParallelAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

GEMINI_MODEL = "gemini-2.5-flash"  # your project's model


def build_triage_team() -> ParallelAgent:
    """Concurrent branch experts, each isolated; ADK fuses outputs post-run."""
    return ParallelAgent(
        name="triage_team",
        sub_agents=[
            Agent(
                name="clinical_specialist",
                model=GEMINI_MODEL,
                instruction=(
                    "Assess the query from a clinical-safety perspective (risk, red flags, "
                    "regulatory exposure). Do not give unsupported diagnostic advice."
                ),
            ),
            Agent(
                name="regulatory_specialist",
                model=GEMINI_MODEL,
                instruction=(
                    "Assess from a data-governance / regulatory angle (GDPR, residency, "
                    "DPIA triggers, records of processing)."
                ),
            ),
            Agent(
                name="security_specialist",
                model=GEMINI_MODEL,
                instruction=(
                    "Assess from a security angle (IAM least-privilege, secret handling, "
                    "public ingress exposure, Model Armor placement)."
                ),
            ),
        ],
    )


def main() -> None:
    team = build_triage_team()
    session_service = InMemorySessionService()
    runner = Runner(
        agent=team,
        app_name="triage_demo",
        session_service=session_service,
    )
    print("Built ParallelAgent team:", team.name)
    print("Branches (concurrent):", [s.name for s in team.sub_agents])
    print("Run with a real API key, e.g. export GOOGLE_API_KEY=... then runner.run_async(...)")
    print("Collect each branch's output from the run events and synthesize downstream.")


if __name__ == "__main__":
    main()