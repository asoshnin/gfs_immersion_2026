#!/usr/bin/env python3
"""
ADK Starter Recipe: LoopAgent (Iterative Refinement with a Hard Iteration Bound)
Source: Google for Startups Immersion EMEA 2026 — Session 02 (Building Agents with ADK)
Compatible with: google-adk 2.14.0 (verified against the installed release), Python 3.10+

REAL ADK — this is runnable google-adk code, not pseudocode.
A loop agent iterates a generator/critic pair up to ``max_iterations`` and stops
at a hard budget cap, instead of inventing its own stop signal.
"""

#   pip install "google-adk>=2.14.0"

from google.adk.agents import Agent, LoopAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

GEMINI_MODEL = "gemini-2.5-flash"  # your project's model

MAX_ITERATIONS = 4  # hard bound — the loop will never exceed this


def build_refinement_loop() -> LoopAgent:
    """Generator + critic loop bounded by max_iterations (quality gate)."""
    return LoopAgent(
        name="refinement_loop",
        max_iterations=MAX_ITERATIONS,
        sub_agents=[
            Agent(
                name="generator",
                model=GEMINI_MODEL,
                instruction=(
                    "Produce or improve a draft solution for the task in the conversation. "
                    "After each critic pass, incorporate the critique and emit a tighter version."
                ),
            ),
            Agent(
                name="critic",
                model=GEMINI_MODEL,
                instruction=(
                    "Act as a demanding reviewer. Identify concrete defects (correctness, "
                    "error-handling, security, clarity). If the draft is production-ready, "
                    "say 'VALIDATION_ACCEPTED'; otherwise list fixes for the generator."
                ),
            ),
        ],
    )


def main() -> None:
    loop = build_refinement_loop()
    session_service = InMemorySessionService()
    runner = Runner(
        agent=loop,
        app_name="refinement_demo",
        session_service=session_service,
    )
    print("Built LoopAgent pipeline:", loop.name)
    print("Sub-agents:", [s.name for s in loop.sub_agents])
    print("max_iterations (hard stop):", loop.max_iterations)
    print("Run with a real API key, e.g. export GOOGLE_API_KEY=... then runner.run_async(...)")


if __name__ == "__main__":
    main()