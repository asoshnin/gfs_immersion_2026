# Immersion Companion — EMEA 2026

An **unofficial** companion hub for the Google for Startups Immersion EMEA 2026 cohort.

> **Not affiliated with Google LLC or Antler.** This is an independent community project. All
> trademarks belong to their owners. No proprietary slides, recordings or transcriptions are
> hosted here; the portal links to official material.

---

## What this is

A small, self-hosted web portal that:

- summarises the four immersion sessions,
- bundles the methodology artefacts (ADK recipes, scripts, checklists),
- runs a **bring-your-own-key** assistant that answers questions using the visitor's own LLM
  provider key (the key never leaves the browser),
- exposes the same retrieval core over MCP (stdio) for local use.

## Run it locally

```bash
pip install -r requirements.txt
python serve.py --port 8080
# open http://localhost:8080
```

The server needs the retrieval core (`crawl/`, vendored here) and, for grounded answers, the
SQLite index in `data/` (see *Data* below). Without the index the API degrades honestly: it reports
the knowledge service as unavailable rather than inventing an answer.

## Bring your own key

The assistant calls **your** LLM provider directly from the browser. Paste a provider base URL and
key in the settings panel; the key is stored in `localStorage` only, is never sent to this server,
and is never written back into the input field after saving. There is no server-side inference.

## Data

The searchable corpus is **not committed**. The SQLite index is built locally from public
documentation plus the project's own session syntheses; it is excluded because it embeds
third-party documentation text. `data/catalog.json` is a small public manifest and is committed.

To rebuild locally, run the harvester pipeline in the private development tree, or run the portal with
an empty index — it still serves its static content and the assistant abstains cleanly.

## Layout

```
serve.py            HTTP server: static site + /api/* (retrieval, grounding, feedback)
site/               the portal (index.html, compiled.css, methodology/ artefacts, assets/)
crawl/              vendored retrieval core (BM25 + dense, RRF fusion)
prompts/            the assistant system prompt
scripts/            local + Cloud Run deploy helpers
data/               catalog.json (committed); SQLite index (not committed)
requirements.txt
Dockerfile
firebase.json       Firebase Hosting rewrite of /api/** to the Cloud Run service
```

## Licence

MIT — see [LICENSE](LICENSE).

## Takedown / contact

If you believe content here infringes your rights, contact **alexey@vwoosh.com** and it will be
removed promptly.