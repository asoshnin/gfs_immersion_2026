---
title: "Antigravity /grill-me: Security & IAM Audit"
stage: "Write Spec → Build"
trigger: "/grill-me"
---

# Antigravity Security & IAM Audit Prompt

Copy and paste into Google Antigravity IDE before provisioning cloud infrastructure or publishing APIs:

```
/grill-me
Perform a rigorous security, IAM, and data privacy audit on our GCP architecture — review the design in 02_design/infra-design.md and the declarations in 04_infra/.

Please grill me on this setup with specific emphasis on:
1. IAM least-privilege: verify no default editor/owner roles are assigned to runtime service accounts.
2. Secret hygiene: ensure zero plaintext API keys or credentials exist in environment variables, source code, or git history.
3. Secret Manager bindings: verify all secrets are wired via Cloud Run `--set-secrets="KEY=projects/{proj}/secrets/{name}:latest"` resolved at container launch (no plaintext in env or `.env`).
4. Input sanitization & injection defense: verify Model Armor and DLP / Sensitive Data Protection (SDP) filters are placed before Gemini model invocation.
5. Outbound network egress restrictions and VPC Service Controls.

Highlight any single point of credential compromise or compliance violation (e.g. GDPR special-category health data).
```
