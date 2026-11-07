# 02_design/ — Write Spec & Browser Mockup

**Google SDLC Stages:** Browser Mockup → Write Spec
**Antigravity Checkpoint:** `/mockup` for UI, `/grill-me` after infra design
**Source Session:** S02 — ADK Multi-Agent Systems, S03 — Cloud Run

## What Goes Here
- `adr/` — Architecture Decision Records (`001-use-cloud-run.md`, etc.)
- `system-design.md` — High-level system architecture
- `infra-design.md` — GCP Resource Topology & IAM Matrix
- `mockups/` — Antigravity browser mockups

## GCP-Specific Notes
- ADRs must include a GCP service comparison matrix (e.g., Cloud Run vs GKE vs Compute Engine).
- Infra design must specify: VPC layout, Cloud Run ingress (all/internal), IAM roles with least-privilege, Secret Manager inventory.
- Use `gcloud` CLI to validate the design against your actual project.

## Next Zone
After design review → proceed to `03_src/` for implementation.