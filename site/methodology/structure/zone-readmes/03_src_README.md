# 03_src/ — Build (Application Code)

**Google SDLC Stage:** Build
**Antigravity Checkpoint:** `/implement` after design approved
**Source Session:** S02 — ADK, S03 — Cloud Run deployment

## What Goes Here
- `app/` — Application source code (Python/Node.js)
- `antigravity/` — Antigravity IDE project configs
- `agents/` — ADK agent definitions & topologies
- `requirements.txt` or `package.json`

## GCP-Specific Notes
- Use Antigravity IDE for all code authoring.
- ADK agents go in `agents/` — follow the topology recipes from the methodology kit.
- Reference Secret Manager for all credentials (`gcp:secretmanager:PROJECT/SECRET`).
- Use Gemini API models — specify `{{GEMINI_MODEL}}` (e.g. `gemini-2.5-flash` or a newer cohort-model) in model bindings.

## Next Zone
After implementation → proceed to `05_tests/` for verification.