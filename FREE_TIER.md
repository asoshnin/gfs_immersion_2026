# Free-Tier Cost Guard

This document records the cost controls and measured budgets for the GFS Immersion Hub public portal so it stays within the Google Cloud Run always-free tier and similar free-tier hosting limits.

## Cloud Run configuration guardrails

The deploy script (`scripts/deploy_cloud_run.sh`) and the `scripts/free_tier_guard.py` gate enforce:

- `--min-instances 0` (scale-to-zero; no idle spend)
- `--memory 1Gi` (≤ 1024 MiB; memory metered in GiB-seconds)
- `--cpu 1` (default vCPU; no GPU)
- Region resolved from `GCP_REGION` env/arg, defaulting to `us-central1`
- CPU reranker artifact ≤ 150 MB (verified at build time)
- `RERANK_MAX_K == 20`

> Following the recommended config (scale-to-zero, 1 vCPU, 1 GiB, us-central1, local CPU reranker <= 150 MB, `RERANK_MAX_K=20`) keeps this service within the Google Cloud Run always-free tier.

## Reranker cost/sizing

- Model repo: `Xenova/bge-reranker-base`
- Smallest available ONNX variant: `onnx/model_int8.onnx` (~265.9 MB)
- Tokenizer: `tokenizer.json` (~16.3 MB)
- **Observed total artifact: 282.2 MB**, which exceeds the 150 MB cap.
- Runtime behavior: `CPUReranker` fails closed and the portal falls back to hybrid fusion (`reranker_used=false`).
- No GPU is used; inference is CPU-only via ONNX Runtime when a compliant artifact is present.

## Outbound-call policy

- `serve.py` makes **no server-side outbound LLM / model API calls**.
- All LLM calls are issued from the browser with the founder's own key (BYOK).
- Live-web fallback queries public HTTPS endpoints only when triggered by a user query and only through the hardened `crawl.web_ground` path.
- No founder key or uploaded document content is echoed to any origin-side endpoint.

## Static egress budget

Static egress is dominated by:

- `pdf.worker.min.js` — lazy-loaded only when a PDF attachment is opened.
- `og.png` — cached social-image asset; kept under 200 KB.
- Staged SQLite database (`gfs_immersion_2026.sqlite`, ~41 MB) ships with the container image, so query-time reads are local and generate no Cloud Storage egress.
- Measured per-page-view static payload (index.html + CSS + JS + icons) is under 1 MB for the landing page.

## Load/latency guardrails

Target guardrails (to be measured under real load):

- Reranker latency at `k=20` ≤ 1600 ms per query
- Reranker latency at `k=50` ≤ 4000 ms per query
- Reranker CPU seconds per query ≤ measured budget (disabled until a compliant artifact is available)

Until a ≤150 MB artifact can be pinned, the reranker remains disabled at runtime and these latency gates are not exercised.

## Disclaimer

Every chat answer footer carries the persistent disclaimer:

> AI-generated from retrieved sources; verify against the linked source; not investment advice.
