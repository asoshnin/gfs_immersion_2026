#!/usr/bin/env bash
# ==============================================================================
# deploy-cloud-run.sh — Cloud Run Serverless Deployment Blueprint
# Source: Google for Startups Immersion EMEA 2026 — Session 03 (Zack Akil)
# Free-Tier Optimized: Scale-to-zero, 512MiB, 1 vCPU, 40 concurrency
# ==============================================================================
set -euo pipefail

GCP_PROJECT="${GCP_PROJECT:-\$(gcloud config get-value project 2>/dev/null || echo '')}"
GCP_REGION="${GCP_REGION:-europe-west1}"
SERVICE_NAME="${SERVICE_NAME:-app}"
IMAGE_TAG="gcr.io/\$GCP_PROJECT/\$SERVICE_NAME:\$(git rev-parse --short HEAD 2>/dev/null || echo 'latest')"

if [ -z "\$GCP_PROJECT" ]; then
    echo "❌ GCP_PROJECT is not set. Run: export GCP_PROJECT=your-project-id"
    exit 1
fi

echo "=================================================================="
echo "☁️  Deploying \$SERVICE_NAME to Google Cloud Run"
echo "   Project: \$GCP_PROJECT"
echo "   Region:  \$GCP_REGION"
echo "   Image:   \$IMAGE_TAG"
echo "=================================================================="

# 1. Enable Required GCP APIs
echo "📡 Verifying required GCP APIs are active..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    --project "\$GCP_PROJECT"

# 2. Build Container Image via Cloud Build (Zero local Docker required)
echo "🔨 Submitting build to Google Cloud Build..."
gcloud builds submit --tag "\$IMAGE_TAG" --project "\$GCP_PROJECT"

# 3. Deploy to Cloud Run with Free-Tier Guardrails
echo "🚀 Deploying revision to Cloud Run..."
gcloud run deploy "\$SERVICE_NAME" \
    --image "\$IMAGE_TAG" \
    --platform managed \
    --region "\$GCP_REGION" \
    --memory 512Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 2 \
    --concurrency 40 \
    --timeout 60 \
    --allow-unauthenticated \
    --project "\$GCP_PROJECT"

SERVICE_URL="\$(gcloud run services describe "\$SERVICE_NAME" --region "\$GCP_REGION" --format 'value(status.url)' --project "\$GCP_PROJECT")"

echo ""
echo "=================================================================="
echo "✅ Deployment Succeeded!"
echo "   Service URL: \$SERVICE_URL"
echo "   Health Check: curl -f \$SERVICE_URL/api/health"
echo "=================================================================="
