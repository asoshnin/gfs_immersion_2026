#!/usr/bin/env python3
"""
ADK Starter Recipe: ParallelAgent (Concurrent Fan-Out / Fan-In Topology)
Source: Google for Startups Immersion EMEA 2026 — Session 02 (Building Agents with ADK)
Compatible with: google-adk >= 2.12.0, Python 3.10+
"""

class ParallelAgentDemo:
    """
    Demonstrates concurrent fan-out across independent domain specialists,
    followed by downstream synthesis into a unified decision artifact.
    """
    def __init__(self, name: str, branches: list):
        self.name = name
        self.branches = branches

    def run(self, query: str) -> dict:
        print(f"[{self.name}] Concurrent fan-out across {len(self.branches)} specialists...")
        branch_results = {}
        for b in self.branches:
            b_name = b["name"]
            print(f"  -> Branch '{b_name}' processing query concurrently...")
            branch_results[b_name] = f"Specialist assessment from {b_name} regarding '{query}'"
        
        # Downstream merger phase
        print("  -> Downstream MergerAgent synthesizing findings...")
        synthesis = {
            "query": query,
            "branch_findings": branch_results,
            "consensus": "Unified recommendation compiled from all concurrent branches."
        }
        return synthesis

if __name__ == "__main__":
    branches = [
        {"name": "ClinicalSpecialist"},
        {"name": "RegulatorySpecialist"},
        {"name": "SecuritySpecialist"}
    ]
    orchestrator = ParallelAgentDemo(name="TriageSynthesisTeam", branches=branches)
    output = orchestrator.run("Deploying Ambient CDS on Vertex AI")
    print("Synthesis consensus:", output["consensus"])
