#!/usr/bin/env bash
# ==============================================================================
# gcp-project-init.sh — GCP-Native Project Bootstrapper
# Source: Google for Startups Immersion EMEA 2026 (Sessions 01-03)
# ==============================================================================
set -euo pipefail

PROJECT_NAME="${1:-gcp-startup-project}"
TARGET_DIR="${2:-./\$PROJECT_NAME}"

echo "=================================================================="
echo "🚀 Bootstrapping GCP-Native Project: \$PROJECT_NAME"
echo "   Target directory: \$TARGET_DIR"
echo "=================================================================="

# 1. Prerequisite Checks
echo "🔍 Checking local prerequisites..."
command -v git >/dev/null 2>&1 || { echo "❌ git is required but not installed."; exit 1; }
command -v gcloud >/dev/null 2>&1 || { echo "⚠️  gcloud CLI not found. Install from https://cloud.google.com/sdk"; }
command -v firebase >/dev/null 2>&1 || { echo "⚠️  firebase-tools not found. Install via npm install -g firebase-tools"; }

# 2. Scaffold 7-Zone Directory Layout
echo "📁 Scaffolding 7-Zone Google 4-Stage SDLC layout..."
mkdir -p "\$TARGET_DIR"/{01_specs,02_design/{adr,mockups},03_src/{app,agents,antigravity},04_infra/terraform,05_tests/{unit,integration,evals},06_deploy/{runbooks,docker},07_docs/api}

# Copy zone READMEs if available
SCRIPT_DIR="$(cd "$(dirname "\$0")" && pwd)"
ZONE_README_DIR="\$SCRIPT_DIR/../structure/zone-readmes"

if [ -d "\$ZONE_README_DIR" ]; then
    cp "\$ZONE_README_DIR/01_specs_README.md" "\$TARGET_DIR/01_specs/README.md" 2>/dev/null || true
    cp "\$ZONE_README_DIR/02_design_README.md" "\$TARGET_DIR/02_design/README.md" 2>/dev/null || true
    cp "\$ZONE_README_DIR/03_src_README.md" "\$TARGET_DIR/03_src/README.md" 2>/dev/null || true
    cp "\$ZONE_README_DIR/04_infra_README.md" "\$TARGET_DIR/04_infra/README.md" 2>/dev/null || true
    cp "\$ZONE_README_DIR/05_tests_README.md" "\$TARGET_DIR/05_tests/README.md" 2>/dev/null || true
    cp "\$ZONE_README_DIR/06_deploy_README.md" "\$TARGET_DIR/06_deploy/README.md" 2>/dev/null || true
    cp "\$ZONE_README_DIR/07_docs_README.md" "\$TARGET_DIR/07_docs/README.md" 2>/dev/null || true
fi

# 3. Copy Templates
TEMPLATES_DIR="\$SCRIPT_DIR/../templates"
if [ -d "\$TEMPLATES_DIR" ]; then
    echo "📝 Copying Google-flavored document templates..."
    cp "\$TEMPLATES_DIR/PRD_TEMPLATE.md" "\$TARGET_DIR/01_specs/prd.md" 2>/dev/null || true
    cp "\$TEMPLATES_DIR/USER_STORIES_TEMPLATE.md" "\$TARGET_DIR/01_specs/user-stories.md" 2>/dev/null || true
    cp "\$TEMPLATES_DIR/VISION_DOC_TEMPLATE.md" "\$TARGET_DIR/01_specs/vision.md" 2>/dev/null || true
    cp "\$TEMPLATES_DIR/ADR_TEMPLATE.md" "\$TARGET_DIR/02_design/adr/001-initial-architecture.md" 2>/dev/null || true
    cp "\$TEMPLATES_DIR/INFRA_DESIGN_TEMPLATE.md" "\$TARGET_DIR/02_design/infra-design.md" 2>/dev/null || true
    cp "\$TEMPLATES_DIR/DEPLOYMENT_RUNBOOK_TEMPLATE.md" "\$TARGET_DIR/06_deploy/runbooks/production-deployment.md" 2>/dev/null || true
fi

# 4. Generate .env.example with Secret Manager reference syntax
cat << 'EOF' > "\$TARGET_DIR/.env.example"
# GCP Environment Configuration (Zero Plaintext Secrets)
GCP_PROJECT=your-project-id
GCP_REGION=europe-west1
ENVIRONMENT=development

# Google Secret Manager Reference Syntax
# Cloud Run resolves secrets natively at container startup:
# gcloud run deploy ... --set-secrets="GEMINI_API_KEY=projects/${GCP_PROJECT}/secrets/gemini-api-key:latest"
GEMINI_API_KEY=gcp:secretmanager:${GCP_PROJECT}/gemini-api-key:latest
EOF

# 5. Generate basic Cloud Build config
cat << 'EOF' > "\$TARGET_DIR/04_infra/cloudbuild.yaml"
steps:
  # 1. Run Tests
  - name: 'python:3.12-slim'
    entrypoint: 'bash'
    args: ['-c', 'pip install -r 03_src/requirements.txt pytest && pytest 05_tests/unit']

  # 2. Build Container Image
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', 'gcr.io/\$PROJECT_ID/app:\$COMMIT_SHA', '-f', '06_deploy/docker/Dockerfile', '.']

  # 3. Push to Artifact Registry / Container Registry
  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', 'gcr.io/\$PROJECT_ID/app:\$COMMIT_SHA']

images:
  - 'gcr.io/\$PROJECT_ID/app:\$COMMIT_SHA'
EOF

# 6. Copy Cloud Run Deployment Blueprint
if [ -f "\$SCRIPT_DIR/deploy-cloud-run.sh" ]; then
    cp "\$SCRIPT_DIR/deploy-cloud-run.sh" "\$TARGET_DIR/06_deploy/deploy.sh"
    chmod +x "\$TARGET_DIR/06_deploy/deploy.sh"
fi

echo ""
echo "✅ Project successfully bootstrapped at \$TARGET_DIR"
echo ""
echo "=================================================================="
echo "🎯 NEXT STEP: ANTIGRAVITY /grill-me ARCHITECTURAL REVIEW"
echo "=================================================================="
echo "Open \$TARGET_DIR in Google Antigravity IDE and run:"
echo ""
echo "  /grill-me"
echo "  I have scaffolded \$PROJECT_NAME using the GCP-Native Methodology Kit."
echo "  Review 01_specs/prd.md and 02_design/infra-design.md."
echo "  Grill me on my architecture choices, token cost assumptions, and Cloud Run scaling limits."
echo "=================================================================="
