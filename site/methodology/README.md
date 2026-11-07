# GCP-Native Methodology Kit

**Version:** `1.0.0`
**Source:** Google for Startups Immersion EMEA 2026 (powered by Antler)
**Sessions:** S01 (AI Studio), S02 (ADK), S03 (Cloud Run), S04 (VC Math)

## What This Is

A **turnkey project scaffold** for founders building on Google Cloud. Clone this kit, fill in 3 project-specific values, and have a deployable GCP skeleton in under 10 minutes.

This kit codifies Google's opinionated 4-stage agentic SDLC learned during the immersion program:

```
Plan & Design ──► Browser Mockup ──► Write Spec ──► Build & Staged Review
```

## What's Inside

| Component | What You Get |
|-----------|-------------|
| **📁 Folder Layout** | Canonical 7-zone directory structure for GCP-native projects |
| **📝 Document Templates** | 6 opinionated templates: Vision Doc, PRD, ADR, User Stories, Infra Design, Deployment Runbook |
| **🔧 Bootstrapper Script** | `gcp-project-init.sh` — one command to scaffold your next GCP project |
| **🔍 Antigravity Prompts** | Pre-built `/grill-me` prompts for architecture review, security audit, and deploy readiness |
| **🤖 ADK Recipes** | Copy-paste starter code for SequentialAgent, LoopAgent, ParallelAgent, and Hierarchical patterns |
| **☁️ Deploy Blueprint** | Cloud Run deployment script (GCS → Pub/Sub → Cloud Run → BigQuery) |
| **📈 VC Scorecard** | Interactive Five Lenses self-assessment (Adam French / Antler framework) |

## Quick Start

```bash
# 1. Copy the bootstrapper to your new project directory
cp methodology/scripts/gcp-project-init.sh ~/my-new-gcp-project/
cd ~/my-new-gcp-project/

# 2. Set your project ID and region
export GCP_PROJECT="your-project-id"
export GCP_REGION="europe-west1"

# 3. Run the bootstrapper
./gcp-project-init.sh

# 4. Copy-paste the printed /grill-me prompt into Antigravity IDE
```

## Attribution

- **Program:** Google for Startups Immersion EMEA 2026, powered by Antler
- **Sessions:** Toby Knight, Fabian Desnoes, Mauricio Arrechea, Zack Akil (Google), Adam French (Antler UK)
- **Tooling:** Google Antigravity IDE, AI Studio, Cloud Run, Firebase, ADK, Gemini
- **Kit Author:** Medixspace B.V. / VWOOSH — independent community resource
- **License:** MIT — use freely, attribution appreciated

## Explore the Portal

Full session syntheses, FTS5 search, and AI Q&A assistant at **[gfs-immersion.vwoosh.com](https://gfs-immersion.vwoosh.com)**.