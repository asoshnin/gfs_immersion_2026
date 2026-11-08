#!/usr/bin/env python3
"""
ADK Starter Recipe: Hierarchical Coordinator (Root Router + Specialist Sub-Agents)
Source: Google for Startups Immersion EMEA 2026 — Session 02 (Building Agents with ADK)
Compatible with: google-adk 2.14.0 (verified against the installed release), Python 3.10+

REAL ADK — this is runnable google-adk code, not pseudocode.
There is NO ``HierarchicalAgent`` class in google-adk. Hierarchical delegation is
done by a root ``Agent`` with ``sub_agents`` (or a ``ManagedAgent``); the model
routes intent to the matching specialist. Do not hand-roll a keyword router.
"""

#   pip install "google-adk>=2.14.0"

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

GEMINI_MODEL = "gemini-2.5-flash"  # your project's model


def build_coordinator() -> Agent:
    """Root coordinator delegates intent to specialized sub-agents."""
    return Agent(
        name="root_coordinator",
        model=GEMINI_MODEL,
        instruction=(
            "You are a coordinator. Read the user's intent and delegate to the "
            "most appropriate specialist sub-agent below. Reply with that agent's answer."
        ),
        sub_agents=[
            Agent(
                name="data_analyst",
                model=GEMINI_MODEL,
                description="Handles metrics, analytics, CSV, queries.",
                instruction="Answer analytics/metrics questions from the conversation context.",
            ),
            Agent(
                name="cloud_architect",
                model=GEMINI_MODEL,
                description="Handles Cloud Run, GCP, deployment, infrastructure.",
                instruction="Answer GCP architecture and Cloud Run deployment questions.",
            ),
            Agent(
                name="compliance_auditor",
                model=GEMINI_MODEL,
                description="Handles GDPR, HIPAA, privacy, security.",
                instruction="Answer data-privacy and compliance questions.",
            ),
        ],
    )


def main() -> None:
    coordinator = build_coordinator()
    session_service = InMemorySessionService()
    runner = Runner(
        agent=coordinator,
        app_name="coordinator_demo",
        session_service=session_service,
    )
    print("Built hierarchical coordinator:", coordinator.name)
    print("Specialists (delegation targets):", [s.name for s in coordinator.sub_agents])
    print("Run with a real API key, e.g. export GOOGLE_API_KEY=... then runner.run_async(...)")


if __name__ == "__main__":
    main()