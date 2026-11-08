---
template_version: "1.0.0"
google_sdlc_stage: "Plan & Design"
antigravity_compatible: true
source_session: "S04"
last_updated: "2026-10-31"
---

# Product Vision & Venture Scale Blueprint: {{PROJECT_NAME}}

> **Google SDLC Stage:** Plan & Design  
> **Source:** Google for Startups Immersion EMEA 2026 — Session 04: Decoding VC Math (Adam French, Antler UK)  

---

## 1. Executive Summary & Elevator Pitch
{{PROJECT_NAME}} delivers {{CORE_BENEFIT}} for {{TARGET_USER}} by {{INNOVATIVE_MECHANISM}}, eliminating {{CRITICAL_PAIN_POINT}}.

---

## 2. The Five Lenses to Make an Idea Bigger (Antler VC Framework)

### Lens 1: 10x More Places
- **Question:** How does this product expand geographically and into adjacent verticals without rebuilding the core engine?
- **Our Wedge:** {{DESCRIBE_GEOGRAPHIC_OR_VERTICAL_EXPANSION_VECTORS}}

### Lens 2: Replace Legacy
- **Question:** What existing high-budget manual workflow, legacy software, or agency spend are we displacing?
- **Our Target:** {{DISPLACED_LEGACY_BUDGET}}

### Lens 3: Market Standard
- **Question:** How does our technical specification, data schema, or workflow become the default industry standard?
- **Our Standard:** {{STANDARDS_PLAY_E_G_OPEN_SOURCE_PROTOCOL_OR_FHIR_PROFILE}}

### Lens 4: 10x More Differentiated
- **Question:** What is our defensible moat when foundational models commoditize code and basic reasoning?
- **Our Moat:** {{MOAT_TYPE_REGULATORY_PROPRIETARY_DATA_OR_NETWORK_EFFECTS}}

### Lens 5: Beat Competition
- **Question:** How does our operating leverage (e.g. solo-engineer + AI agent fleet) out-ship funded incumbents?
- **Our Velocity:** {{SHIPPING_CADENCE_AND_UNIT_ECONOMICS_ADVANTAGE}}

---

## 3. VC Math & Unit Economics Self-Assessment

- **Inference Cost vs SaaS ARR:** Gross margin target $\ge 75\%$.
- **Customer Acquisition Defensibility:** Founder-led wedge or organic developer viral loop.
- **Phase 2 In-Person Summit Alignment:** Demonstrates live shipping velocity and clear customer proof-in-motion.

---

## 4. Antigravity `/grill-me` Checkpoint

After completing sections 1–3, run in Antigravity:

```
/grill-me
Review this Vision Doc for {{PROJECT_NAME}}. Challenge my:
1. Five-Lenses expansion vectors and where they are unsupported by evidence.
2. Gross-margin and unit-economics assumptions ($\ge 75\%$ target).
3. Moat defensibility if a frontier model commoditizes the core mechanic.
```

## 5. Next Steps

1. Hand off the validated Vision Doc to the PRD (`01_specs/prd.md`).
2. Open the ADR to record the first architecture decision.
3. Move to `02_design/` for the Infra Design and mockups.

---

## Filled-In Example

<details>
<summary>Example: AssetIntel — Document Intelligence for Mid-Market Operations</summary>

### 1. Executive Summary
AssetIntel delivers instant, grounded answers from a company's own operational documents (invoices, contracts, runbooks) for \${{}}number of employees, by {{}}mechanism, eliminating manual document triage.

### 2. Five Lenses
1. **10x More Places:** From finance back-office to legal and procurement verticals without re-building the extraction engine.
2. **Replace Legacy:** Displaces a manual spreadsheet-and-email review workflow billed to operations.
3. **Market Standard:** Publishes an open extraction contract schema as the default interchange.
4. **10x More Differentiated:** MoM from proprietary document corpus + permissioned retrieval, not prompt length.
5. **Beat Competition:** A 2-engineer team + AI agent fleet out-ships a 20-person services agency.

### 3. VC Math
- Target gross margin ≥ 75% on inference per document.
- Wedge: founder-led demos on the customer's own files → proof in motion in week 1.
</details>
