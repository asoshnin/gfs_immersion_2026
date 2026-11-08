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

### Story 1.1: Semantic Retrieval over Session Syntheses
- **As a:** Cohort Founder
- **I want to:** ask the portal assistant about a deployment topic (e.g. "Cloud Run scaling")
- **So that:** I can get a grounded answer citing the relevant session synthesis without re-watching the recording.

**Acceptance Criteria:**
```gherkin
Scenario: Grounded answer cites a session source
  Given the user asks the assistant a deployment question
  When the retrieval returns an answerable result with a resolvable source
  Then the answer render a citation link to the source session
  And the cited excerpt appears in the retrieved evidence
```

</details>
