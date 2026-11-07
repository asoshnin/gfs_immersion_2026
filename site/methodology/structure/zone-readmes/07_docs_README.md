# 07_docs/ — Continuous Documentation

**Google SDLC Stage:** Continuous (all stages)
**Source Session:** S01 — Spec-First methodology

## What Goes Here
- `api/` — API documentation (OpenAPI/Swagger specs)
- `onboarding.md` — Developer onboarding guide
- `architecture.md` — Architecture diagrams (Mermaid) & decision log
- `glossary.md` — Domain terminology (GCP services, project acronyms)

## GCP-Specific Notes
- Keep architecture diagrams as Mermaid blocks in markdown — render in GitHub/GitLab natively.
- API docs: export from Cloud Endpoints or manually maintain OpenAPI spec.
- Onboarding guide: include `gcloud auth login`, project setup, local dev environment.
- Link to the Antigravity IDE project for auto-generated documentation.

## Maintenance Rule
Update `07_docs/` whenever:
- A new GCP service is added to the project.
- An architecture decision is made (link to ADR in `02_design/adr/`).
- An API endpoint changes its contract.