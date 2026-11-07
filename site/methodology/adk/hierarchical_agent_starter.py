#!/usr/bin/env python3
"""
ADK Starter Recipe: Hierarchical Coordinator (Root Router Topology)
Source: Google for Startups Immersion EMEA 2026 — Session 02 (Building Agents with ADK)
Compatible with: google-adk >= 2.12.0, Python 3.10+
"""

class HierarchicalCoordinatorDemo:
    """
    Demonstrates root_agent router delegating tasks to sub-agents
    based on declarative agent descriptions rather than monolithic tool lists.
    """
    def __init__(self, name: str, sub_agents: dict):
        self.name = name
        self.sub_agents = sub_agents

    def route_and_execute(self, user_intent: str) -> str:
        intent_lower = user_intent.lower()
        print(f"[{self.name}] Routing user intent: '{user_intent}'")
        
        selected_agent = None
        for agent_name, spec in self.sub_agents.items():
            for kw in spec["keywords"]:
                if kw in intent_lower:
                    selected_agent = agent_name
                    break
            if selected_agent:
                break
                
        if not selected_agent:
            selected_agent = "GeneralAssistant"
            
        print(f"  -> Delegating task to specialized sub-agent: [{selected_agent}]")
        return f"Response from [{selected_agent}]: Handled intent '{user_intent}'."

if __name__ == "__main__":
    agents = {
        "DataAnalyst": {"keywords": ["metrics", "analytics", "csv", "query"], "desc": "Analyzes metrics"},
        "CloudArchitect": {"keywords": ["cloud run", "gcp", "deploy", "infra"], "desc": "Plans GCP infrastructure"},
        "ComplianceAuditor": {"keywords": ["gdpr", "hipaa", "privacy", "security"], "desc": "Audits compliance"}
    }
    
    coordinator = HierarchicalCoordinatorDemo(name="RootCoordinator", sub_agents=agents)
    res = coordinator.route_and_execute("How do we scale Cloud Run with min-instances?")
    print(res)
