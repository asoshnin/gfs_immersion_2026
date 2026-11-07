# GFS Immersion 2026 — Knowledge Hub
# Deployment to Google Cloud Run (free tier)

# Prerequisites:
#   1. gcloud CLI installed and authenticated
#   2. gcloud config set project YOUR_PROJECT_ID
#   3. Required APIs enabled: run.googleapis.com, cloudbuild.googleapis.com

set -e

PROJECT_ID="${GCP_PROJECT:-$(gcloud config get-value project)}"
REGION="${GCP_REGION:-us-central1}"   # R6: us-central1 is mandatory for Always-Free
SERVICE_NAME="gfs-platform"          # the ACTUAL live service
IMAGE="us-central1-docker.pkg.dev/$PROJECT_ID/$SERVICE_NAME/app:latest"

# PAID-REGION GUARD (R6): a non-free region bills silently on an otherwise free project.
if [ "${ALLOW_PAID_REGION:-0}" != "1" ] && [ "$REGION" != "us-central1" ]; then
  echo "REFUSING: REGION=$REGION is not us-central1. Set ALLOW_PAID_REGION=1 to override." >&2
  exit 78
fi

echo "🚀 Deploying GFS Immersion Hub to Cloud Run"
echo "   Project: $PROJECT_ID"
echo "   Region:  $REGION"
echo "   Service: $SERVICE_NAME"
echo ""

# Step 1: Enable required APIs
echo "📡 Enabling GCP APIs..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com --project "$PROJECT_ID"

# Step 2: Build and push container image
echo "🔨 Building container with Cloud Build..."
# Explicit -f: 02_public_hub/Dockerfile needs the REPO ROOT as build context.
gcloud builds submit --config 02_public_hub/scripts/cloudbuild.deploy.yaml --project "$PROJECT_ID" .

# Step 3: Deploy to Cloud Run (free-tier compatible: 1 vCPU, 1 GiB, min-instances=0)
# Design §11 mandates --memory 1Gi for the fastembed + onnxruntime stack.
echo "☁️ Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --platform managed \
  --region "$REGION" \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 2 \
  --concurrency 40 \
  --timeout 60 \
  --allow-unauthenticated \
  --set-env-vars "GFS_DB_PATH=/app/data/gfs_immersion_2026.sqlite" \
  --project "$PROJECT_ID"

# Step 4: Show deployed URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format 'value(status.url)' --project "$PROJECT_ID")
echo ""
echo "✅ Deployment complete!"
echo "   URL: $SERVICE_URL"
echo ""
echo "📋 Next steps:"
echo "   1. Test: curl $SERVICE_URL/api/health"
echo "   2. Open: $SERVICE_URL"
echo "   3. Set up custom domain: gcloud beta run domain-mappings create --service=$SERVICE_NAME --domain=gfs-immersion.vwoosh.com --region=$REGION"