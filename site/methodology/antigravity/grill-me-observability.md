---
title: "Antigravity /grill-me: Observability & Health Monitoring"
stage: "Staged Review & Operation"
trigger: "/grill-me"
---

# Antigravity Observability & Health Monitoring Prompt

Copy and paste into Google Antigravity IDE to verify runtime observability:

```
/grill-me
Audit our production observability and telemetry setup for Cloud Run and Vertex AI / Gemini services.

Please grill me on this observability plan covering:
1. Structured JSON logging: ensuring trace and span IDs correlate across Cloud Run and downstream API requests in Cloud Logging.
2. User-facing latency metrics: tracking TTFT (Time to First Token) and total turn completion latency in Cloud Monitoring.
3. Error budgets & alerting thresholds: configuring alerts for HTTP 5xx spikes (>1% over 5m) and Secret Manager resolution failures.
4. Cost & quota dashboards: configuring alerts for 80% monthly grant burn on Gemini API quotas.
5. Synthetic canary probing and automated status page reporting.
```
