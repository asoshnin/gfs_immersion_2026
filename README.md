# Immersion Companion — EMEA 2026

An **unofficial** companion hub for the Google for Startups Immersion EMEA 2026 cohort.

> **Not affiliated with Google LLC or Antler.** This is an independent community project. All
> trademarks belong to their owners. No proprietary slides, recordings or transcriptions are
> hosted here; the portal links to official material.

---

## What this is

A small, self-hosted web portal that:

- summarises the four immersion sessions and lets you search the whole cohort knowledge base
  (session syntheses + bundled Google Cloud documentation),
- bundles the methodology artefacts (ADK recipes, scripts, checklists),
- runs a **bring-your-own-key** assistant that answers questions using the visitor's own LLM
  provider key (the key never leaves the browser),
- makes every claim **clickable**: each citation opens the original indexed text with the cited
  passage highlighted,
- ships the **AI Venture Coach** (client-side five-lens self-assessment) and the **VC Readiness
  Scorecard** for founders working through session 04,
- can fall back to **live web search** when the local corpus has nothing relevant — searched
  directly from the browser, with an optional key of your own (see *Live web search* below).

The retrieval core is a plain Python package (`crawl/`), so it can be embedded in other tools —
see [`docs/MCP.md`](docs/MCP.md) for the bundled MCP server.

## Use it from an agent (MCP)

`mcp_server.py` exposes the knowledge base as MCP tools for any agent client:

```bash
pip install -r requirements-mcp.txt
python mcp_server.py --selftest     # list the tools
```

Retrieval-only (no key, nothing billed), local stdio (no hosting), and no local index needed.
Tools: `list_sessions`, `search_immersion_knowledge`, `get_excerpt`, `get_adk_topology_recipe`.
Full setup and client configs: [`docs/MCP.md`](docs/MCP.md).

## See it without a key

Want to know what it looks like before configuring anything? See
[`docs/EXAMPLES.md`](docs/EXAMPLES.md) for screenshots, and open the static, self-contained
[`docs/demo/answer-example.html`](docs/demo/answer-example.html) — a frozen grounded answer whose
citations open with the cited chunk highlighted. No API key and no server required.

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

Any OpenAI-compatible endpoint works (OpenAI, OpenRouter, Azure AI Foundry, a local server on
loopback). One caveat worth knowing: **Azure AI Foundry and Azure OpenAI do not accept
`Authorization: Bearer`** — they want an `api-key` header, and an Azure API Management gateway
wants `Ocp-Apim-Subscription-Key`. All three return the same misleading
`invalid subscription key or wrong API endpoint` 401, so the header cannot be inferred from the
error. The app therefore tries the schemes in order on an auth failure (401/403 only, so a retry
never double-bills tokens), remembers the one that worked, and exposes a manual override under
*Settings → Auth header*.

## Live web search

The optional live-web fallback uses [Keenable](https://keenable.ai) (`api.keenable.ai`), queried
directly from the browser — same BYO model as the LLM key, so your search key never reaches this
server.

- **No key needed to try it.** Keenable answers keyless on a shared public tier (1,000 requests/hour
  per IP), so the fallback works before you configure anything.
- **A key raises your limits** (10 requests/second, no hourly cap). Create one at
  <https://app.keenable.ai/console/api-keys>; it starts with `keen_`. There is no second identifier
  to configure — no engine ID, no Cloud project.
- **Settings → Test credentials** runs one real query and reports exactly what the provider said, so
  a failure names its own cause instead of failing silently.

Answers drawn from the web are always labelled unverified, carry their source URLs, and are wrapped
in an explicit untrusted-content boundary before reaching a model.

> This replaced Google Custom Search JSON API, which is closed to new Google Cloud projects and is
> retired for existing customers on 1 January 2027.

## Data

The searchable corpus is **not committed**. The SQLite index is built locally from public
documentation plus the project's own session syntheses; it is excluded because it embeds
third-party documentation text. `data/catalog.json` is a small public manifest and is committed.

To rebuild locally, run the harvester pipeline in the private development tree, or run the portal with
an empty index — it still serves its static content and the assistant abstains cleanly.

## Layout

```
serve.py            HTTP server: static site + /api/* (retrieval, grounding, feedback)
mcp_server.py       MCP (stdio) tools for agent clients (no key, retrieval-only)
site/               the portal (index.html, compiled.css, methodology/ artefacts, assets/)
                      assets/provider-websearch.js  BYO Keenable search (browser-side)
                      assets/provider-config.js     provider-adaptive params + auth schemes
                      assets/settings-modal.js      the single shared credentials UI
docs/               EXAMPLES.md, MCP.md + screenshots + the key-free static demo
crawl/              vendored retrieval core (BM25 + dense, RRF fusion)
prompts/            the assistant system prompt
scripts/            local + Cloud Run deploy helpers
data/               catalog.json (committed); SQLite index (not committed)
requirements.txt
requirements-mcp.txt
Dockerfile
firebase.json       Firebase Hosting rewrite of /api/** to the Cloud Run service
```

## Licence

MIT — see [LICENSE](LICENSE).

## Takedown / contact

If you believe content here infringes your rights, contact **alexey@vwoosh.com** and it will be
removed promptly.