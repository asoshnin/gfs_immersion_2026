---
template_version: "1.0.0"
google_sdlc_stage: "Write Spec"
antigravity_compatible: true
source_session: "S03"
last_updated: "2026-10-31"
---

# GCP Infrastructure Design Specification: {{PROJECT_NAME}}

> **Google SDLC Stage:** Write Spec  
> **Source:** Google for Startups Immersion EMEA 2026 — Session 03: Serverless AI on Google Cloud (Zack Akil)  
> **Prerequisites:** `gcloud` CLI, Cloud Run API, Cloud Build API, Google Secret Manager  

---

## 1. System Topology Architecture

```
┌──────────────────┐      ┌─────────────────────────┐      ┌──────────────────────┐
│  Firebase Hosting│───▶  │ Google Cloud Run        │───▶  │ Google Secret Manager│
│  (Static Edge CDN)      │ (Containerized Service) │      │ (API Keys & Config)  │
└──────────────────┘      └────────────┬────────────┘      └──────────────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │ Gemini / Vertex AI API  │
                          │ (Multi-model Inference) │
                          └─────────────────────────┘
```

---

## 2. GCP Resource Inventory

| Resource Type | Resource Name | Region | Tier / Sizing | Idle Cost |
|---|---|---|---|---|
| **Cloud Run Service** | `{{SERVICE_NAME}}` | `{{GCP_REGION}}` | 1 vCPU, 512MiB RAM | $0.00 (Scale-to-zero) |
| **Firebase Hosting** | `{{FIREBASE_SITE_ID}}` | Global Edge | Spark Free Tier | $0.00 |
| **Secret Manager** | `{{SECRET_NAME}}` | Automatic | Up to 6 active secrets | $0.00 |
| **Artifact Registry** | `{{REPO_NAME}}` | `{{GCP_REGION}}` | Docker format | < $0.10/mo |

---

## 3. IAM Least-Privilege Role Matrix

| Identity / Service Account | Assigned Roles | Justification |
|---|---|---|
| `sa-cloudrun-runner@...` | `roles/secretmanager.secretAccessor` | Fetch API keys at runtime without admin rights |
| `sa-cloudbuild-deployer@...` | `roles/run.admin`, `roles/iam.serviceAccountUser` | Deploy revisions to Cloud Run |
| Public Internet / Unauthenticated | `roles/run.invoker` | Allow HTTP requests to public endpoints |

---

## 4. Scaling, Concurrency & Cold-Start Policy

- **Min Instances:** `0` (Dev / Staging / Nightly) → `1` (Production SLA hours)
- **Max Instances:** `2` (Hard budget clamp prevents runaway spend)
- **Concurrency:** `40` (Multiple simultaneous requests handled per container instance)
- **Request Timeout:** `60s` (Fails fast on upstream model hangs)

---

## 5. Secret Manager Bindings & Environment Variables

Never commit secrets to git or store in plaintext environment variables. All secrets are referenced via Secret Manager:

```bash
# Secret Manager Reference Syntax
gcloud run deploy {{SERVICE_NAME}} \
  --set-secrets="GEMINI_API_KEY=projects/{{GCP_PROJECT}}/secrets/gemini-api-key:latest"
```

---

## 6. Antigravity `/grill-me` Security Audit

```
/grill-me
Conduct a security and IAM audit on this infrastructure design:
1. Are there any unnecessary permissions granted to the Cloud Run runtime service account?
2. Is the public ingress properly firewalled or rate-limited?
3. What is our blast radius if the GEMINI_API_KEY is leaked?
```
