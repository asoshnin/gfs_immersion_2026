---
template_version: "1.0.0"
google_sdlc_stage: "Write Spec"
antigravity_compatible: true
source_session: "S02"
last_updated: "2026-10-31"
---

# Architecture Decision Record (ADR): ADR-{{NUMBER}}: {{TITLE}}

> **Status:** {{PROPOSED | ACCEPTED | DEPRECATED | SUPERSEDED}}  
> **Date:** {{YYYY-MM-DD}}  
> **Deciders:** {{NAMES_AND_ROLES}}  
> **Google SDLC Stage:** Write Spec  
> **Source Session:** Google for Startups Immersion EMEA 2026 — Session 02: Building Agents with ADK  

---

## 1. Context & Problem Statement
{{DESCRIBE_THE_CONTEXT_AND_FORCES_AT_PLAY_SUCH_AS_TECHNICAL_CONSTRAINTS_LATENCY_COST_OR_TEAM_SKILLS}}

---

## 2. Decision Drivers
- **Driver 1:** {{E_G_MINIMAL_OPERATIONAL_OVERHEAD_SERVERLESS}}
- **Driver 2:** {{E_G_ZERO_IDLE_COST_AT_STARTUP_PRE_SEED_SCALE}}
- **Driver 3:** {{E_G_NATIVE_INTEGRATION_WITH_GEMINI_AND_SECRET_MANAGER}}
- **Driver 4:** {{E_G_SUB_50MS_INTERNAL_ROUTING_AND_CONTAINER_STARTUP}}

---

## 3. Considered Alternatives

### GCP Service / Architecture Comparison

| Evaluation Criterion | Option A: {{OPTION_A_NAME}} (Chosen) | Option B: {{OPTION_B_NAME}} | Option C: {{OPTION_C_NAME}} |
|---|---|---|---|
| **GCP Primitive** | {{E_G_CLOUD_RUN}} | {{E_G_GKE_AUTOPILOT}} | {{E_G_APP_ENGINE}} |
| **Pricing Model** | Pay-per-request / Scale-to-zero | Minimum node pool fee | Instance hour billing |
| **Operational Burden** | Minimal (Container only) | Cluster lifecycle, K8s manifests | Proprietary runtime constraints |
| **Concurrency & Scaling** | Auto-scales 0 to N in seconds | Pod auto-scaler (slower cold start) | Traditional auto-scaling |
| **Ecosystem Fit** | Direct Pub/Sub push, Eventarc | Custom ingress, ingress controller | Integrated services |

---

## 4. Decision Outcome
Chosen Option: **Option A: {{OPTION_A_NAME}}**, because {{EXPLAIN_JUSTIFICATION_GROUNDED_IN_DECISION_DRIVERS}}.

### Positive Consequences
- {{BENEFIT_1}}
- {{BENEFIT_2}}
- {{BENEFIT_3}}

### Negative Consequences / Trade-offs
- {{DRAWBACK_1_AND_MITIGATION}}
- {{DRAWBACK_2_AND_MITIGATION}}

---

## 5. Antigravity `/grill-me` Validation

Run in Antigravity to stress-test this decision:

```
/grill-me
Evaluate ADR-{{NUMBER}} ({{TITLE}}). 
Critique our choice of {{OPTION_A_NAME}} versus {{OPTION_B_NAME}} focusing on:
1. Long-term cost scaling beyond the startup free tier.
2. Latency implications and cold-start mitigations (e.g. min-instances=1).
3. Vendor lock-in versus portable container standards.
```

---

## Filled-In Example

<details>
<summary>Example: ADR-001: Cloud Run vs GKE Autopilot for Agentic Microservices</summary>

### 1. Context
Medixspace B.V. requires a serverless hosting environment for hosting Python ADK agent orchestrators and MCP endpoints. Budget is capped at startup grant credits with zero tolerance for idle compute costs.

### 2. Decision Outcome
Chosen Option: **Cloud Run**, because it scales to zero when no clinical queries are processed, natively supports containerized FastMCP and FastAPI services, and integrates directly with Google Secret Manager without managing Kubernetes cluster control planes.

### 3. Trade-offs
- *Cold start latency:* First request on idle containers may take ~1.5s. Mitigated by setting `--min-instances 0` during dev and `--min-instances 1` during clinic operating hours (08:00–18:00).

</details>
