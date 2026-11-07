---
title: "Antigravity /grill-me: Deployment Readiness Check"
stage: "Build & Staged Review"
trigger: "/grill-me"
---

# Antigravity Deployment Readiness Check

Copy and paste into Google Antigravity IDE before initiating your first production rollout:

```
/grill-me
Evaluate our deployment readiness runbook in 06_deploy/runbooks/production-deployment.md and container configurations.

Please grill me on this deployment plan covering:
1. Free-tier compliance: verify Cloud Run flags enforce --memory 512Mi, --cpu 1, --min-instances 0, and --max-instances 2.
2. Health check endpoints: verify /api/health probe behavior under traffic spikes and cold starts.
3. Automated rollback trigger criteria: what metric threshold triggers an immediate traffic shift back to the prior revision?
4. Cloud Build pipeline determinism: ensure all dependencies are pinned and unit tests block image pushing on failure.
5. Zero-downtime traffic migration on Cloud Run revisions.
```
