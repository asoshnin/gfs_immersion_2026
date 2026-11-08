# GCP-Native Project Folder Layout

**Google SDLC Stage:** Plan & Design → Build & Staged Review
**Inspired by:** Johnny.Decimal classification + the kit's 4-stage agentic SDLC (derived from the program)
**Source:** Google for Startups Immersion EMEA 2026 — Sessions 01 & 03

---

## Canonical 7-Zone Structure

```
<project-root>/
│
├── 01_specs/              ← PLAN & DESIGN
│   ├── vision.md           # Product Vision & Elevator Pitch
│   ├── prd.md              # Product Requirements Document
│   ├── user-stories.md     # User Stories with Acceptance Criteria
│   └── nfr.md              # Non-Functional Requirements (SLAs, security, compliance)
│
├── 02_design/              ← WRITE SPEC
│   ├── adr/                # Architecture Decision Records
│   │   └── 001-use-cloud-run.md
│   ├── system-design.md    # High-level system architecture
│   ├── infra-design.md     # GCP Resource Topology & IAM Matrix
│   └── mockups/            # Antigravity browser mockups (← BROWSER MOCKUP stage)
│
├── 03_src/                 ← BUILD
│   ├── app/                # Application source code (Python/Node.js)
│   ├── antigravity/        # Antigravity IDE project configs
│   ├── agents/             # ADK agent definitions & topologies
│   └── requirements.txt    # Python dependencies (or package.json)
│
├── 04_infra/               ← BUILD (Infrastructure as Code)
│   ├── terraform/          # Terraform manifests
│   ├── cloudbuild.yaml     # Cloud Build CI/CD pipeline
│   ├── secret-manager.yaml # Secret Manager inventory & IAM bindings
│   └── iam-policies.yaml   # IAM roles & service accounts
│
├── 05_tests/               ← BUILD & STAGED REVIEW
│   ├── unit/               # Unit tests
│   ├── integration/        # Integration tests
│   ├── e2e/                # End-to-end tests
│   └── evals/              # ADK eval test cases (CI/CD)
│
├── 06_deploy/               ← BUILD & STAGED REVIEW
│   ├── runbooks/           # Deployment & rollback procedures
│   ├── cloud-run.yaml      # Cloud Run service specification
│   ├── firebase.json       # Firebase Hosting configuration
│   └── docker/             # Dockerfiles & container configs
│
└── 07_docs/                ← Continuous
    ├── api/                # API documentation (OpenAPI/Swagger)
    ├── onboarding.md       # Developer onboarding guide
    └── architecture.md     # Architecture diagrams & decision log
```

---

## Zone ↔ Google SDLC Mapping

| Zone | Google SDLC Stage | Antigravity Trigger |
|------|------------------|---------------------|
| `01_specs/` | Plan & Design | `/grill-me` after drafting vision + PRD |
| `02_design/` | Write Spec | `/grill-me` after infra design |
| `02_design/mockups/` | Browser Mockup | `/mockup` in Antigravity |
| `03_src/` | Build | `/implement` after design approved |
| `04_infra/` | Build | `/grill-me` security audit |
| `05_tests/` | Staged Review | ADK CLI `eval` in CI/CD |
| `06_deploy/` | Staged Review | `/grill-me` deploy readiness |
| `07_docs/` | Continuous | N/A — human-maintained |

---

## Why This Layout?

1. **Google-Native:** Every zone maps to a specific GCP primitive — Secret Manager in `04_infra/`, Cloud Build in `04_infra/`, Cloud Run in `06_deploy/`, Firebase in `06_deploy/`.
2. **Antigravity-Compatible:** The zone naming convention is recognized by Antigravity's project indexer. `/grill-me` knows which zones to inspect for each SDLC stage.
3. **Bootable:** Run `gcp-project-init.sh` and this entire structure is created with READMEs and `.env.example` in one command.
4. **Opinionated:** Not generic — this layout assumes Cloud Run, Firebase, Artifact Registry, and Gemini API. Swap the tools if you're on a different stack.

> **Note:** "zone naming recognized by Antigravity's indexer" is an aspirational convention this kit
> applies consistently so `/grill-me` prompts can reference stable paths; it is not a documented
> Google/Antigravity capability guarantee.

---

## Related Resources

- **Bootstrapper:** `methodology/scripts/gcp-project-init.sh`
- **Template Pack:** `methodology/templates/templates.zip`
- **Session 03 Codelab:** [Build and Deploy to GCP with Antigravity](https://codelabs.developers.google.com/build-and-deploy-gcp-with-antigravity)