#!/usr/bin/env python3
"""
ADK Starter Recipe: SequentialAgent (Linear Pipeline Topology)
Source: Google for Startups Immersion EMEA 2026 — Session 02 (Building Agents with ADK)
Compatible with: google-adk >= 2.12.0, Python 3.10+
"""

# In production, import from google.adk.agents:
# from google.adk.agents.sequential_agent import SequentialAgent
# from google.adk.agents.llm_agent import LlmAgent

GEMINI_MODEL = "gemini-2.5-flash"

class SequentialAgentDemo:
    """
    Demonstrates deterministic linear progression of agent turns.
    Sub-agents execute in strict list order, passing shared InvocationContext.
    """
    def __init__(self, name: str, stages: list):
        self.name = name
        self.stages = stages

    def run(self, initial_input: dict) -> dict:
        context = dict(initial_input)
        print(f"[{self.name}] Initiating sequential pipeline with {len(self.stages)} stages.")
        for idx, stage in enumerate(self.stages, 1):
            stage_name = stage.get("name", f"Stage_{idx}")
            print(f"  -> Executing {stage_name}...")
            # Simulate stage transformation
            output_key = stage.get("output_key", f"out_{idx}")
            context[output_key] = f"Result of {stage_name} applied to {context.get('input', '')}"
        return context

if __name__ == "__main__":
    spec_stage = {"name": "SpecWriter", "output_key": "spec"}
    code_stage = {"name": "CodeWriter", "output_key": "code"}
    audit_stage = {"name": "SecurityAuditor", "output_key": "audit"}

    pipeline = SequentialAgentDemo(
        name="SDLC_Pipeline",
        stages=[spec_stage, code_stage, audit_stage]
    )
    result = pipeline.run({"input": "Clinical Triage Feature Request"})
    print("Pipeline result keys:", list(result.keys()))
