# 05_tests/ — Build & Staged Review

**Google SDLC Stage:** Build & Staged Review
**Antigravity Checkpoint:** N/A — CI/CD automated
**Source Session:** S02 — ADK Evals

## What Goes Here
- `unit/` — Unit tests (pytest for Python, vitest for Node.js)
- `integration/` — Integration tests (Cloud Run emulator, Pub/Sub emulator)
- `e2e/` — End-to-end tests (Playwright for UI)
- `evals/` — ADK eval test cases for CI/CD

## GCP-Specific Notes
- Run tests in Cloud Build as part of CI/CD pipeline.
- ADK evals: use `adk eval` CLI to run test cases, measure tool-calling accuracy.
- Use `gcloud beta emulators` for local Pub/Sub and Firestore testing.
- Golden dataset: curate production-representative test cases, not toy examples.

## Next Zone
All tests green → deploy from `06_deploy/`.