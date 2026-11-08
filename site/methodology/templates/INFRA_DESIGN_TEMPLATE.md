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
| **Cloud Run Service** | `{{SERVICE_NAME}}` | `{{GCP_REGION}}` (us-central1 = Always-Free) | 1 vCPU, 1 GiB RAM | $0.00 at rest (scale-to-zero; per-request/GB costs apply) |
| **Firebase Hosting** | `{{FIREBASE_SITE_ID}}` | Global Edge | Spark Free Tier | $0.00 |
| **Secret Manager** | `{{SECRET_NAME}}` | Automatic | Up to 6 active secret *versions* /mo (free tier) | $0.00* |
| **Artifact Registry** | `{{REPO_NAME}}` | `{{GCP_REGION}}` | Docker format | ~$0 (storage; estimates subject to current pricing) |

---

## 3. IAM Least-Privilege Role Matrix

| Identity / Service Account | Assigned Roles | Justification |
|---|---|---|
| `sa-cloudrun-runner@...` | `roles/secretmanager.secretAccessor` | Fetch API keys at runtime without admin rights |
| `sa-cloudbuild-deployer@...` | `roles/run.admin`, `roles/iam.serviceAccountUser`, `roles/artifactregistry.writer` | Build + deploy revisions and push images to Artifact Registry |
| Cloud Build default SA | `roles/cloudbuild.builds.builder` | Run build steps |
| Public Internet / Unauthenticated | `roles/run.invoker` | Allow HTTP requests to public endpoints — **only if** the API is meant to be public; otherwise use `--no-allow-unauthenticated` + Firebase Auth / IAP, and add rate-limiting / Model Armor before GA |

---

## 4. Scaling, Concurrency & Cold-Start Policy

- **Min Instances:** `0` (Dev / Staging / Nightly) → `1` (Production SLA hours)
- **Max Instances:** `2` (Hard budget clamp prevents runaway spend)
- **Memory:** `1 GiB` (fits the fastembed + onnxruntime stack; 512Mi risks OOM on first dense search)
- **Concurrency:** `40` (Multiple simultaneous requests handled per container instance)
- **Request Timeout:** `60s` (Fails fast on upstream model hangs)

> These are **cost-control / scale-to-zero** flags, not “free-tier flags”: Cloud Run’s free tier is a
> monthly usage allowance (vCPU-seconds / GB-seconds / requests), not a CLI flag.

---

## 5. Secret Manager Bindings & Environment Variables

Never commit secrets to git or store them in plaintext environment variables. Cloud Run resolves
secrets natively at container start. **The authoritative mechanism is the `--set-secrets` deploy
flag** (`.env` reference strings are illustrative only and are not resolved by Cloud Run):

```bash
# Authoritative: Cloud Run --set-secrets
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
