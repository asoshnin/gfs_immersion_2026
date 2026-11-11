# MCP server — use the hub from any agent

`mcp_server.py` exposes the Immersion Companion knowledge base as
[Model Context Protocol](https://modelcontextprotocol.io) tools, so Claude Desktop, Cursor,
OpenClaw — any MCP client — can search the cohort corpus and pull the exact original text behind a
citation.

**Retrieval-only by design.** The server never calls an LLM: your agent does the reasoning, so there
is no key to configure, nothing to bill, and no key to leak. It runs as a **local stdio process** —
no hosting, no cost.

## Install

```bash
pip install -r requirements-mcp.txt
python mcp_server.py --selftest     # -> lists the tools and the four sessions
```

## Client configuration

Claude Desktop / Cursor style (`mcpServers`):

```json
{
  "mcpServers": {
    "immersion": {
      "command": "python",
      "args": ["/absolute/path/to/mcp_server.py"],
      "cwd": "/absolute/path/to/the/repo"
    }
  }
}
```

## Tools

| Tool | What it does | Needs network |
|---|---|---|
| `list_sessions` | The four cohort session syntheses (title, date, speakers, summary, link) | no |
| `search_immersion_knowledge(query, limit=5)` | Hybrid (BM25 + dense) search across the corpus | yes |
| `get_excerpt(key)` | The original indexed text behind a citation key, plus its `[start, end)` span | yes |
| `get_adk_topology_recipe(type)` | Static ADK recipes: `sequential` \| `parallel` \| `loop` \| `hierarchical` | no |

Search and excerpts are served by the public hub API (`/api/search`, `/api/excerpt`); the catalog is
read from the `data/catalog.json` shipped in this repository. **No local SQLite index is required.**

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `GFS_HUB_URL` | `https://gfs-immersion.vwoosh.com` | the hub origin to query |
| `GFS_MCP_TIMEOUT` | `30` | per-request timeout in seconds |
| `GFS_CATALOG_PATH` | `data/catalog.json` | override the session catalog |

## Example agent exchange

```text
user      : what does the cohort say about deploying to Cloud Run?
agent     : search_immersion_knowledge("cloud run deployment")
            -> [session:session_03::chunk_003] "GCS -> Pub/Sub -> Cloud Run -> BigQuery ..."
            get_excerpt("session:session_03::chunk_003")
            -> the full indexed section, with the cited span located in it
```

The excerpt endpoint deliberately returns the text **as indexed** (not a fresh web fetch), with
internal filesystem paths redacted, so a reader can verify exactly what an answer was grounded in.

## Limitations

- The SQLite corpus is **not published** (it embeds third-party documentation text). Search therefore
  requires the hub to be reachable; there is no offline mode.
- The transport is stdio. Hosting the server is intentionally out of scope for this repository — it
  would add an auth surface for no benefit here.