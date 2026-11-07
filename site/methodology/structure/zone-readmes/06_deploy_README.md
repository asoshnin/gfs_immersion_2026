# 06_deploy/ — Build & Staged Review

**Google SDLC Stage:** Build & Staged Review
**Antigravity Checkpoint:** `/grill-me` — Deployment Readiness Check
**Source Session:** S03 — Cloud Run Codelab

## What Goes Here
- `runbooks/` — Deployment & rollback procedures
- `cloud-run.yaml` — Cloud Run service specification
- `firebase.json` — Firebase Hosting configuration
- `docker/` — Dockerfiles & container configs

## GCP-Specific Notes
- Cloud Run free-tier flags: `--min-instances 0`, `--memory 512Mi`, `--cpu 1`, `--max-instances 2`.
- Firebase Hosting with Cloud Run rewrite: proxy `/api/*` to Cloud Run service.
- Use Cloud Build for container builds — not local Docker.
- Smoke test: `curl $SERVICE_URL/api/health` after deploy.
- Rollback: `gcloud run services update-traffic --to-revisions=LATEST=0,<PREVIOUS>=100`.

## Next Zone
After production deployment → maintain docs in `07_docs/`.