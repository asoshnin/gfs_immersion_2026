---
template_version: "1.0.0"
google_sdlc_stage: "Plan & Design"
antigravity_compatible: true
source_session: "S01"
last_updated: "2026-10-31"
---

# User Stories & Acceptance Criteria: {{PROJECT_NAME}}

> **Google SDLC Stage:** Plan & Design (Feeds Stage 2: Browser Mockup)  
> **Source:** Google for Startups Immersion EMEA 2026 — Session 01: From Vibe to Code  
> **Antigravity Hook:** Use stories to guide `/mockup` visual wireframing  

---

## 1. MoSCoW Prioritization Matrix

- **Must Have (P0):** Core workflows without which the prototype is non-functional.
- **Should Have (P1):** High-value capabilities expected in a complete MVP.
- **Could Have (P2):** Enhancements that improve experience but can defer past initial release.
- **Won't Have (P3):** Explicitly out of scope for current sprint/iteration.

---

## 2. Story Hierarchy

### Epic 1: {{EPIC_1_TITLE}}

#### Story 1.1: {{STORY_TITLE}} (Priority: {{MUST | SHOULD | COULD}})
- **As a:** {{ROLE_OR_PERSONA}}
- **I want to:** {{ACTION_OR_CAPABILITY}}
- **So that:** {{BUSINESS_OR_CLINICAL_BENEFIT}}

**Acceptance Criteria (Gherkin format):**
```gherkin
Scenario: Successful {{ACTION_NAME}}
  Given {{INITIAL_SYSTEM_OR_USER_STATE}}
  When {{USER_PERFORMS_SPECIFIC_ACTION}}
  Then {{SYSTEM_RESPONDS_WITH_EXPECTED_OUTPUT}}
  And {{DATA_OR_METRIC_IS_UPDATED}}
```

**Antigravity Mockup Directive:**
> *`/mockup Generate an interactive browser wireframe showing Story 1.1: {{UI_ELEMENTS_TO_RENDER}}`*

---

#### Story 1.2: {{STORY_TITLE}} (Priority: {{MUST | SHOULD | COULD}})
- **As a:** {{ROLE_OR_PERSONA}}
- **I want to:** {{ACTION_OR_CAPABILITY}}
- **So that:** {{BENEFIT}}

**Acceptance Criteria:**
```gherkin
Scenario: {{SCENARIO_NAME}}
  Given {{STATE}}
  When {{ACTION}}
  Then {{OUTCOME}}
```

---

## Filled-In Example

<details>
<summary>Example: Session Synthesis Search & Citation Inspection</summary>

### Story 1.1: BM25 Lexical Keyword Search
- **As a:** Cohort Founder
- **I want to:** Search for "Cloud Run min-instances" across session transcripts
- **So that:** I can instantly locate the exact quote and deployment recommendation without watching video replays.

**Acceptance Criteria:**
```gherkin
Scenario: Search returns ranked session chunks
  Given the user enters "min-instances" into the portal search box
  When the search executes against /api/search
  Then at least 2 relevant snippets from Session 03 are displayed within 50ms
  And each snippet shows the session badge, title, and file locus
```

</details>
