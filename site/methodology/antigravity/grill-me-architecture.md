---
title: "Antigravity /grill-me: Architecture & Spec Review"
stage: "Plan & Design → Write Spec"
trigger: "/grill-me"
---

# Antigravity Architecture & Spec Review Prompt

Copy and paste the following prompt into Google Antigravity IDE after drafting `01_specs/prd.md` and `02_design/infra-design.md`:

```
/grill-me
I have completed the initial PRD and System Architecture design for our project. 
Review 01_specs/prd.md and 02_design/infra-design.md. 

Please grill me on this architecture by asking 8 targeted questions focusing on:
1. Microservice vs monolith boundaries and container modularity on Cloud Run.
2. State management: ephemeral container filesystem vs Firestore / Cloud Storage persistence.
3. Event-driven decoupling: Pub/Sub push vs direct synchronous HTTP REST calls.
4. Token budget estimation and potential rate-limit cliffs on Gemini 2.5 Flash.
5. Ingress security, IAM least-privilege, and Secret Manager access patterns.
6. Cold-start mitigation strategy and CPU allocation policies.
7. Graceful degradation when external APIs or inference services time out.
8. Testability and local emulation support without deploying to GCP.

Provide recommended architectural decisions for each question based on Google Cloud best practices.
```
