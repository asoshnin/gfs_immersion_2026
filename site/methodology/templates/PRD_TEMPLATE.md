---
template_version: "1.0.0"
google_sdlc_stage: "Plan & Design"
antigravity_compatible: true
source_session: "S01"
last_updated: "2026-10-31"
---

# Product Requirements Document (PRD): {{PROJECT_NAME}}

> **Google SDLC Stage:** Plan & Design  
> **Source:** Google for Startups Immersion EMEA 2026 — Session 01: From Vibe to Code  
> **Target Toolchain:** Google Antigravity IDE, AI Studio, Cloud Run, Firebase  

## Overview
This document specifies the business objectives, core personas, functional requirements, and technical constraints for {{PROJECT_NAME}}. It serves as the primary artifact for Antigravity's initial architectural review before any prototyping or code generation begins.

---

## 1. Problem Statement & Opportunity
- **Core Problem:** {{DESCRIBE_USER_PAIN_POINT}}
- **Target Audience:** {{WHO_EXPERIENCES_THIS_PROBLEM}}
- **Why Now:** {{MARKET_OR_TECHNICAL_CATALYST_E_G_GEMINI_REASONING_MULTIMODAL}}
- **Value Proposition:** {{HOW_THIS_PRODUCT_SOLVES_IT}}

---

## 2. User Personas & Journey

| Persona | Role | Primary Goal | Key Friction Point |
|---|---|---|---|
| {{PERSONA_1_NAME}} | {{PERSONA_1_ROLE}} | {{PERSONA_1_GOAL}} | {{PERSONA_1_FRICTION}} |
| {{PERSONA_2_NAME}} | {{PERSONA_2_ROLE}} | {{PERSONA_2_GOAL}} | {{PERSONA_2_FRICTION}} |

### Core Workflow
1. **Trigger:** {{HOW_USER_INITIATES_INTERACTION}}
2. **Input:** {{USER_INPUT_DATA_OR_EVENT}}
3. **Processing:** {{AGENT_OR_SERVICE_PROCESSING_PIPELINE}}
4. **Resolution:** {{DELIVERED_VALUE_OR_OUTPUT}}

---

## 3. Functional Requirements (FR)

- **FR-01: {{FEATURE_1_TITLE}}**
  - **Description:** {{DETAILED_REQUIREMENT}}
  - **Priority:** Must Have
  - **Success Criteria:** {{MEASURABLE_OUTCOME}}

- **FR-02: {{FEATURE_2_TITLE}}**
  - **Description:** {{DETAILED_REQUIREMENT}}
  - **Priority:** Should Have
  - **Success Criteria:** {{MEASURABLE_OUTCOME}}

- **FR-03: {{FEATURE_3_TITLE}}**
  - **Description:** {{DETAILED_REQUIREMENT}}
  - **Priority:** Nice to Have
  - **Success Criteria:** {{MEASURABLE_OUTCOME}}

---

## 4. Non-Functional Requirements (NFR)

| Category | Requirement | Target Metric | GCP Implementation |
|---|---|---|---|
| **Latency** | Time to first response | < {{TARGET_LATENCY_MS}} ms | Cloud Run CPU always allocated / min-instances=1 |
| **Inference Cost** | Cost per session/request | < ${{TARGET_COST}} | {{GEMINI_MODEL}} (default `gemini-2.5-flash`) / Flash Lite cascade |
| **Availability** | Monthly uptime | 99.9% | Cloud Run managed multi-zone regional deployment |
| **Security & Privacy** | Credential & data protection | Zero plain secrets | Google Secret Manager (`--set-secrets`, Cloud Run) |
| **Compliance*** | Privacy controls (not a compliance certificate) | DPIA + DPA in place | Sensitive Data Protection (SDP) + Model Armor |

> *Security/privacy controls such as SDP and Model Armor **support** a compliance programme; they do
> not by themselves confer GDPR/HIPAA/other compliance. Complete a DPIA, sign data-processing
> agreements, set a data-retention schedule, and confirm data-residency when handling personal or
> special-category data.

---

## 5. Google 4-Stage Agentic SDLC Mapping

- **Stage 1: Plan & Design:** This PRD + Initial `/grill-me` in Antigravity.
- **Stage 2: Browser Mockup:** Interactive visual wireframes via Antigravity Chrome DevTools integration (`/mockup`).
- **Stage 3: Write Spec:** System Design, ADRs, and OpenAPI / ADK agent contracts in `02_design/`.
- **Stage 4: Build & Staged Review:** Container build via Cloud Build, test execution in Cloud Run, verification report.

---

## 6. Antigravity `/grill-me` Checkpoint

Run the following command in Antigravity after completing sections 1–5:

```
/grill-me
Review this PRD for {{PROJECT_NAME}}. Challenge my assumptions regarding:
1. Feasibility of target latencies under Cloud Run cold-starts.
2. Inference token economics and potential rate limit bottlenecks on Gemini Flash.
3. Clarity of scope for Stage 2 Browser Mockups.
```

---

## Filled-In Example

<details>
<summary>Example: CloudClinician Clinical Triage Assistant (illustrative — NOT a medical device)</summary>

> ⚠️ **Illustrative example only.** This filled example is for template-shape demonstration and must
> NOT be taken as regulatory or safety guidance. If your product surfaces diagnosis, treatment or
> clinical decision support, it may be a **medical device** and subject to EU MDR / FDA SaMD / local
> rules: obtain a clinical-data strategy, a DPIA, and legal-regulatory review (CE/clearance,
> human-in-the-loop verification, clinical validation) before building. SDP / Model Armor are
> security controls — they are **not** regulatory clearance.

### 1. Problem Statement
Primary care clinics suffer a 25-minute administrative overhead per patient encounter summarizing unstructured notes and lab reports. CloudClinician provides real-time ambient session summarization and differential diagnosis suggestions directly to physician EHR tabs.

### 2. Personas
- **Dr. Jane Miller (General Practitioner):** Needs instant, structured SOAP notes during patient interviews without manual typing.
- **Clinic Administrator:** Needs strict compliance auditing showing no PII leaked into foundational model training corpora.

### 3. Functional Requirements
- **FR-01 (Ambient Audio Transcription):** Stream audio chunks via WebSocket to Gemini multimodal API with timestamped speaker diarization.
- **FR-02 (SOAP Note Generation):** Output structured FHIR-compatible JSON summaries within 3 seconds of encounter close.
- **FR-03 (Citation Verification):** All differential diagnosis suggestions must reference approved Clinical Practice Guidelines (CPGs).

### 4. NFRs & GCP Stack
- Latency: < 2500ms end-to-end SOAP delivery.
- Compute: Cloud Run (Python FastAPI + WebSockets, 1 vCPU, 512MB RAM).
- AI: {{GEMINI_MODEL}} on Vertex AI with Zero Data Retention agreement (confirm your project's ZDR
  entitlement; not assumed).
- Security: Cloud Key Management Service (KMS) + Google Secret Manager.

</details>

## Next Steps
1. Review generated questions from Antigravity `/grill-me`.
2. Transition to `02_design/` to author `ADR_TEMPLATE.md` and `INFRA_DESIGN_TEMPLATE.md`.
