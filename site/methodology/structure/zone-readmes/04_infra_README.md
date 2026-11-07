# 04_infra/ — Build (Infrastructure as Code)

**Google SDLC Stage:** Build
**Antigravity Checkpoint:** `/grill-me` — Security & IAM Audit
**Source Session:** S03 — Cloud Run Deployment

## What Goes Here
- `terraform/` — Terraform manifests (alternative: Deployment Manager)
- `cloudbuild.yaml` — Cloud Build CI/CD pipeline
- `secret-manager.yaml` — Secret Manager inventory & IAM bindings
- `iam-policies.yaml` — IAM roles & service accounts

## GCP-Specific Notes
- Minimum IAM roles: `roles/run.invoker`, `roles/cloudbuild.builds.editor`, `roles/secretmanager.secretAccessor`.
- Cloud Build trigger: connected to your repository, builds on push to `main`.
- Secret Manager: never hardcode values — use reference syntax `gcp:secretmanager:PROJECT/SECRET`.
- Use `gcloud iam roles list` to verify least-privilege before applying.

## Next Zone
After infra is provisioned → tests in `05_tests/`, then deploy from `06_deploy/`.