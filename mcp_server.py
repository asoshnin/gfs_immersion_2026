#!/usr/bin/env python3
"""Immersion Companion — public MCP server (stdio).

A **retrieval-only** Model Context Protocol server for the Google for Startups
Immersion EMEA 2026 companion hub. It gives any MCP client (Claude Desktop, Cursor,
OpenClaw, ...) tools to search the cohort knowledge base and pull the exact original
text behind a citation.

Design choices, kept deliberately small:

* **No API key.** The client agent does the reasoning; this server only retrieves.
  Nothing is generated server-side, so there is nothing to bill and no key to store.
* **No local index needed.** Search and excerpts are served by the already-public hub
  API (`/api/search`, `/api/excerpt`). The SQLite corpus is intentionally not
  published (it embeds third-party documentation text).
* **Zero hosting cost.** It runs as a local stdio process on the caller's machine.
  Set `GFS_HUB_URL` to point at your own deployment.

Tools
-----
``list_sessions``                 the four cohort sessions (from the shipped catalog)
``search_immersion_knowledge``    hybrid search over the corpus
``get_excerpt``                   the indexed text behind a citation key, + span
``get_adk_topology_recipe``       static ADK multi-agent topology snippets

Usage
-----
    pip install -r requirements-mcp.txt
    python mcp_server.py                 # stdio (what MCP clients spawn)

Claude Desktop / Cursor style config::

    {"mcpServers": {"immersion": {"command": "python",
                                  "args": ["/abs/path/to/mcp_server.py"]}}}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ── Configuration ───────────────────────────────────────────────────────────
HUB_URL = os.environ.get("GFS_HUB_URL", "https://gfs-immersion.vwoosh.com").rstrip("/")
HERE = Path(__file__).resolve().parent
CATALOG_PATH = Path(os.environ.get("GFS_CATALOG_PATH", HERE / "data" / "catalog.json"))
HTTP_TIMEOUT = float(os.environ.get("GFS_MCP_TIMEOUT", "30"))

TOOL_NAMES = (
    "list_sessions",
    "search_immersion_knowledge",
    "get_excerpt",
    "get_adk_topology_recipe",
)


# ── Pure helpers (importable without the MCP SDK) ───────────────────────────
def _get_json(url: str) -> dict:
    """GET a JSON document. Raises RuntimeError with an actionable message."""
    req = urllib.request.Request(url, headers={"User-Agent": "gfs-immersion-mcp/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 404 = unknown citation key
        if exc.code == 404:
            raise LookupError(f"not found: {url}") from None
        raise RuntimeError(f"hub returned HTTP {exc.code} for {url}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"cannot reach the hub at {HUB_URL} ({exc.reason}). "
            "Set GFS_HUB_URL to your deployment, or check your network."
        ) from None


def list_sessions() -> list[dict]:
    """The four cohort sessions, from the catalog shipped in this repository."""
    if not CATALOG_PATH.exists():
        raise RuntimeError(f"catalog not found at {CATALOG_PATH}")
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    out = []
    for s in catalog.get("sessions", []):
        url = s.get("on_demand_url") or f"{HUB_URL}/api/page?type=source&name={s.get('id')}"
        if url.startswith("/"):
            url = HUB_URL + url
        out.append(
            {
                "id": s.get("id"),
                "title": s.get("title"),
                "date": s.get("date"),
                "speakers": s.get("speakers", []),
                "summary": s.get("summary"),
                "url": url,
            }
        )
    return out


def search_knowledge(query: str, limit: int = 5) -> dict:
    """Hybrid (BM25 + dense) search over the cohort corpus via the hub API."""
    params = urllib.parse.urlencode({"q": query, "top_k": int(limit)})
    data = _get_json(f"{HUB_URL}/api/search?{params}")
    results = []
    for r in data.get("results", [])[: int(limit)]:
        results.append(
            {
                "fusion_key": r.get("fusion_key"),
                "title": r.get("title"),
                "url": r.get("url") or r.get("source_url"),
                "snippet": (r.get("snippet") or r.get("text") or "").strip(),
            }
        )
    return {
        "query": data.get("query", query),
        "answerable": data.get("answerable"),
        "abstention_reason": data.get("abstention_reason"),
        "total": len(results),
        "results": results,
    }


def get_excerpt(key: str) -> dict:
    """The original indexed text behind a citation key, with the cited span.

    ``key`` is a fusion key from an answer, e.g. ``session:session_03::chunk_003``
    or ``doc:<doc_id>#0000``. The returned ``doc_text`` is the text as indexed by
    the hub and ``[start, end)`` locates the cited chunk inside it.
    """
    data = _get_json(f"{HUB_URL}/api/excerpt?key={urllib.parse.quote(key, safe='')}")
    return {
        "kind": data.get("kind"),
        "title": data.get("title"),
        "url": data.get("url"),
        "chunk_id": data.get("chunk_id"),
        "start": data.get("start"),
        "end": data.get("end"),
        "doc_text": data.get("doc_text", ""),
    }


_ADK_RECIPES: dict[str, str] = {
    "sequential": """```python
from google.adk.agents import LlmAgent, SequentialAgent

spec_writer = LlmAgent(name="SpecWriter", model="gemini-2.5-flash",
                       instruction="Write a PRD from the brief.")
code_writer = LlmAgent(name="CodeWriter", model="gemini-2.5-flash",
                       instruction="Implement the spec as GCP-native code.")
pipeline = SequentialAgent(name="SpecToCode", sub_agents=[spec_writer, code_writer])
```
Use when each step strictly consumes the previous step's output (spec -> code -> deploy).""",
    "parallel": """```python
from google.adk.agents import LlmAgent, ParallelAgent

a = LlmAgent(name="ResearcherA", model="gemini-2.5-flash", output_key="res_a",
             instruction="Investigate angle A.")
b = LlmAgent(name="ResearcherB", model="gemini-2.5-flash", output_key="res_b",
             instruction="Investigate angle B.")
fanout = ParallelAgent(name="Fanout", sub_agents=[a, b])
```
Use for independent sub-investigations whose results are merged later.""",
    "loop": """```python
from google.adk.agents import LlmAgent, LoopAgent

writer = LlmAgent(name="Writer", model="gemini-2.5-flash", instruction="Draft.")
critic = LlmAgent(name="Critic", model="gemini-2.5-flash", instruction="Critique the draft.")
refiner = LoopAgent(name="Refine", max_iterations=3, sub_agents=[writer, critic])
```
Use when an agent must iterate until a quality bar is met (draft -> critique -> revise).""",
    "hierarchical": """```python
from google.adk.agents import LlmAgent, SequentialAgent, LoopAgent

researcher = LlmAgent(name="Researcher", model="gemini-2.5-flash")
review = LoopAgent(name="Review", max_iterations=2, sub_agents=[writer, critic])
orchestrator = SequentialAgent(name="Orchestrator", sub_agents=[researcher, review])
```
Use when a top-level agent delegates to sub-teams (research -> build -> review).""",
}


def get_adk_topology_recipe(topology_type: str) -> str:
    """Static ADK multi-agent topology snippet (no network, no key)."""
    t = (topology_type or "").strip().lower()
    if t in _ADK_RECIPES:
        return _ADK_RECIPES[t]
    return (
        f"Unknown topology '{topology_type}'. Available: "
        + ", ".join(sorted(_ADK_RECIPES))
    )


# ── MCP surface ─────────────────────────────────────────────────────────────
def build_server():
    """Construct the FastMCP server. Imported lazily so helpers stay testable."""
    try:  # SDK moved the class across releases
        from mcp.server.mcpserver import MCPServer as FastMCP  # type: ignore
    except Exception:  # pragma: no cover - depends on installed SDK version
        from mcp.server.fastmcp import FastMCP  # type: ignore

    mcp = FastMCP("gfs-immersion-companion")

    def _tool(name):
        """Register under the documented public name across SDK versions."""
        try:
            return mcp.tool(name=name)
        except TypeError:  # older SDK: falls back to the function name
            return mcp.tool()

    @_tool("list_sessions")
    def _list_sessions_tool() -> str:
        """List the four cohort session syntheses (no key, no network)."""
        try:
            return json.dumps(list_sessions(), indent=2)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the client
            return json.dumps({"error": str(exc)})

    @_tool("search_immersion_knowledge")
    def _search_tool(query: str, limit: int = 5) -> str:
        """Search the cohort knowledge base (session syntheses + bundled GCP docs)."""
        try:
            return json.dumps(search_knowledge(query, limit), indent=2)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc), "query": query})

    @_tool("get_excerpt")
    def _get_excerpt_tool(key: str) -> str:
        """Fetch the original indexed text behind a citation key, with its span."""
        try:
            return json.dumps(get_excerpt(key), indent=2)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc), "key": key})

    @_tool("get_adk_topology_recipe")
    def _recipe_tool(topology_type: str) -> str:
        """Static ADK topology recipe: sequential | parallel | loop | hierarchical."""
        return get_adk_topology_recipe(topology_type)

    return mcp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--transport",
        default=os.environ.get("GFS_MCP_TRANSPORT", "stdio"),
        choices=["stdio", "streamable-http", "sse"],
    )
    ap.add_argument("--port", type=int, default=int(os.environ.get("GFS_MCP_PORT", "8900")))
    ap.add_argument("--hub-url", default=None, help="hub origin (overrides GFS_HUB_URL)")
    ap.add_argument("--selftest", action="store_true", help="print tool status and exit")
    args = ap.parse_args(argv)

    if args.hub_url:
        global HUB_URL
        HUB_URL = args.hub_url.rstrip("/")

    if args.selftest:
        status = {"hub_url": HUB_URL, "tools": list(TOOL_NAMES)}
        try:
            status["sessions"] = len(list_sessions())
        except Exception as exc:  # noqa: BLE001
            status["sessions_error"] = str(exc)
        print(json.dumps(status, indent=2))
        return 0

    mcp = build_server()
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.settings.port = args.port
        mcp.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())