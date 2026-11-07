---
template_version: "1.0.0"
google_sdlc_stage: "Build & Staged Review"
antigravity_compatible: true
source_session: "S03"
last_updated: "2026-10-31"
---

# Production Deployment & Rollback Runbook: {{PROJECT_NAME}}

> **Google SDLC Stage:** Build & Staged Review  
> **Source:** Google for Startups Immersion EMEA 2026 — Session 03: Deploying Serverless AI on Google Cloud  
> **Target Target:** Google Cloud Run + Firebase Hosting  

---

## 1. Pre-Flight Verification Checklist

Before deploying, verify:
- [ ] All unit and integration tests pass: `pytest tests/ -v`
- [ ] No plaintext secrets in codebase: `git diff | grep -iE 'key|secret|token'` returns 0 hits
- [ ] GCP project ID is verified: `gcloud config get-value project`
- [ ] Required APIs enabled: `run.googleapis.com`, `cloudbuild.googleapis.com`, `secretmanager.googleapis.com`

---

## 2. Automated Deployment Sequence

```bash
# Step 1: Set environmental variables
export GCP_PROJECT="{{GCP_PROJECT_ID}}"
export GCP_REGION="{{GCP_REGION:-europe-west1}}"
export SERVICE_NAME="{{SERVICE_NAME}}"

# Step 2: Build container image via Google Cloud Build
gcloud builds submit --tag "gcr.io/$GCP_PROJECT/$SERVICE_NAME" --project "$GCP_PROJECT"

# Step 3: Deploy to Cloud Run with free-tier flags
gcloud run deploy "$SERVICE_NAME" \
  --image "gcr.io/$GCP_PROJECT/$SERVICE_NAME" \
  --platform managed \
  --region "$GCP_REGION" \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 2 \
  --concurrency 40 \
  --allow-unauthenticated \
  --project "$GCP_PROJECT"

# Step 4: Deploy static assets to Firebase Hosting (if applicable)
firebase deploy --only hosting --project "$GCP_PROJECT"
```

---

## 3. Post-Deployment Verification (Smoke Tests)

```bash
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region "$GCP_REGION" --format 'value(status.url)')

# Health Check
curl -f "$SERVICE_URL/api/health" || exit 1

# Smoke test search
curl -f "$SERVICE_URL/api/search?q=test" || exit 1
```

---

## 4. Emergency Rollback Procedure

If high error rates (HTTP 5xx > 1%) or severe regressions occur:

```bash
# 1. Identify previous healthy revision
gcloud run revisions list --service "$SERVICE_NAME" --region "$GCP_REGION"

# 2. Instantly route 100% of traffic to previous revision
gcloud run services update-traffic "$SERVICE_NAME" \
  --region "$GCP_REGION" \
  --to-revisions="{{PREVIOUS_HEALTHY_REVISION}}=100"

# 3. Confirm traffic switch
gcloud run services describe "$SERVICE_NAME" --region "$GCP_REGION" --format "value(status.traffic)"
```
