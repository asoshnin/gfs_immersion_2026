#!/usr/bin/env python3
"""
ADK Starter Recipe: LoopAgent (Iterative Refinement & Quality Gate Topology)
Source: Google for Startups Immersion EMEA 2026 — Session 02 (Building Agents with ADK)
Compatible with: google-adk >= 2.12.0, Python 3.10+
"""

COMPLETION_SIGNAL = "VALIDATION_PASSED"

class LoopAgentDemo:
    """
    Demonstrates iterative generation-critique loop bounded by max_iterations.
    Exits early when gatekeeper validation passes via tool escalation.
    """
    def __init__(self, name: str, max_iterations: int = 4):
        self.name = name
        self.max_iterations = max_iterations

    def run(self, draft_prompt: str) -> dict:
        print(f"[{self.name}] Initiating refinement loop (max iterations: {self.max_iterations}).")
        current_draft = f"Initial draft for: {draft_prompt}"
        
        for iteration in range(1, self.max_iterations + 1):
            print(f"  -> Iteration {iteration}: Evaluating draft...")
            # Critique logic: passes on iteration 2 in this demo
            if iteration >= 2:
                print(f"     Gatekeeper check: {COMPLETION_SIGNAL}!")
                return {
                    "status": "COMPLETED",
                    "iterations": iteration,
                    "final_output": current_draft,
                    "exit_reason": "gatekeeper_escalation"
                }
            else:
                print("     Critique: Needs tighter error handling. Refinement applied.")
                current_draft += " + [Error Handling Added]"

        return {
            "status": "MAX_ITERATIONS_REACHED",
            "iterations": self.max_iterations,
            "final_output": current_draft,
            "exit_reason": "budget_cap"
        }

if __name__ == "__main__":
    loop = LoopAgentDemo(name="DraftRefinementLoop", max_iterations=4)
    result = loop.run("Cloud Run Deployment Manifest")
    print("Loop execution result:", result)
