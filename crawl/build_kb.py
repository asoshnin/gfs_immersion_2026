"""Orchestrator for offline knowledge pipeline build, incremental re-indexing, and report.

Conforms to Sprint 08 / 08b:
- Reads tiered seed registry (Tier A > B > C).
- Sitemap discovery and robots.txt caching.
- Incremental re-indexing: skips unchanged documents based on content_hash (0 embed calls).
- Resume (--resume): fast TTL skip based on registry cadence (0 fetch, 0 embed).
- Batch budget (--max-docs-this-run): stops cleanly with status 'batch_complete' (exit 0).
- Progress visibility: per-document status and unconditional heartbeat with ETA.
- Signal handling: traps SIGINT/SIGTERM, completes current doc, writes report, exits 130.
- Counter contract: docs_this_run == added + updated + unchanged + failed.
- Corpus cap enforcement (default 2,000 docs). Tier A exceeding cap HALTS (exit 1).
- Fail-closed model unavailability (exit 78).
- Preserves existing immersion_chunks (80 contractual rows).
- Writes machine-readable crawl_report.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
import yaml

if __name__ == "__main__" or __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawl.chunk import chunk_document
from crawl.discovery import (
    DEFAULT_USER_AGENT,
    RobotsCache,
    discover_source_urls,
)
from crawl.embed import (
    EMBED_DIM,
    EmbeddingEngine,
    ModelLoadError,
    get_embedding_engine_or_halt,
)
from crawl.fetch import Fetcher, FetchResult
from crawl.kb import (
    check_immersion_chunks,
    compute_content_hash,
    compute_doc_id,
    get_connection,
    get_crawl_state,
    init_knowledge_base,
    update_crawl_state,
    upsert_document,
)

logger = logging.getLogger(__name__)

DEFAULT_CORPUS_CAP = 2000
DEFAULT_BATCH_BUDGET = 100
DEFAULT_PROGRESS_EVERY = 25

# Cadence TTL mappings in seconds
CADENCE_TTL_SECONDS = {
    "daily": 24 * 3600,        # 24 hours
    "weekly": 7 * 24 * 3600,   # 7 days
    "monthly": 30 * 24 * 3600, # 30 days
}


def format_duration(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS string."""
    s = int(max(0, seconds))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class CorpusCapExceededError(RuntimeError):
    """Raised when Tier A documents exceed the corpus cap, requiring run halt."""
    pass


class KnowledgeBaseBuilder:
    """Orchestrates document discovery, fetch, extraction, embedding, and indexing."""

    def __init__(
        self,
        registry_path: Path | str,
        db_path: Path | str,
        report_path: Path | str,
        corpus_cap: int = DEFAULT_CORPUS_CAP,
        user_agent: str = DEFAULT_USER_AGENT,
        embedding_engine: Optional[EmbeddingEngine] = None,
        fetcher: Optional[Fetcher] = None,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.db_path = Path(db_path)
        self.report_path = Path(report_path)
        self.corpus_cap = corpus_cap
        self.user_agent = user_agent
        self.embedding_engine = embedding_engine
        self.fetcher = fetcher or Fetcher(user_agent=self.user_agent)
        self.robots_cache = RobotsCache(user_agent=self.user_agent)

    def load_registry(self) -> Dict[str, Any]:
        """Load and parse the tiered seed registry."""
        if not self.registry_path.exists():
            raise FileNotFoundError(f"Registry not found at {self.registry_path}")
        with open(self.registry_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if "tiers" not in data or len(data["tiers"]) < 3:
            raise ValueError("Registry must contain at least 3 tiers (A, B, C)")
        return data

    def run(
        self,
        max_urls_per_source: int = 50,
        max_docs_this_run: Optional[int] = None,
        resume: bool = False,
        progress_every: int = DEFAULT_PROGRESS_EVERY,
        quiet: bool = False,
        mock_docs: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Execute pipeline build and emit crawl_report.json.

        Supports bounded batches, resume skipping, progress output, and clean interruption.
        """
        start_time = time.time()
        registry = self.load_registry()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Open DB connection and ensure additive schema + immersion_chunks preserved
        conn = get_connection(self.db_path, load_vec=True)
        init_knowledge_base(conn, embed_dim=EMBED_DIM)

        immersion_count = check_immersion_chunks(conn)
        if immersion_count not in (0, 80):
            conn.close()
            raise RuntimeError(
                f"Contractual check failed: immersion_chunks has {immersion_count} rows, expected 80"
            )

        # 2. Lazy load embedding engine if not injected
        if self.embedding_engine is None:
            self.embedding_engine = get_embedding_engine_or_halt()

        # 3. Discover candidates grouped by tier
        tier_sources: Dict[str, List[Dict[str, Any]]] = {
            "A": registry["tiers"].get("A", {}).get("sources", []),
            "B": registry["tiers"].get("B", {}).get("sources", []),
            "C": registry["tiers"].get("C", {}).get("sources", []),
        }

        # Gather target URLs per tier, with higher tiers taking deduplication precedence
        seen_urls: Set[str] = set()
        candidates_by_tier: Dict[str, List[Dict[str, Any]]] = {"A": [], "B": [], "C": []}

        with httpx.Client(
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
            timeout=10.0,
        ) as shared_client:
            for tier in ("A", "B", "C"):
                for src in tier_sources.get(tier, []):
                    prod_name = src.get("product", "Unknown")
                    license_name = src.get("license", "unknown")
                    cadence = src.get("cadence", "daily")
                    base_url = src.get("base_url", "")

                    if mock_docs:
                        # Synthetic / test mode
                        discovered_urls = [
                            u for u, d in mock_docs.items()
                            if d.get("tier") == tier and d.get("product") == prod_name
                        ]
                        if not discovered_urls and base_url in mock_docs:
                            discovered_urls = [base_url]
                    else:
                        discovered_urls = discover_source_urls(
                            source=src,
                            client=shared_client,
                            robots_cache=self.robots_cache,
                            max_urls=max_urls_per_source,
                        )

                    for u in discovered_urls:
                        if u not in seen_urls:
                            seen_urls.add(u)
                            candidates_by_tier[tier].append({
                                "url": u,
                                "product": prod_name,
                                "tier": tier,
                                "license": license_name,
                                "cadence": cadence,
                            })

        # 4. Enforce Corpus Cap (FR-10)
        tier_a_count = len(candidates_by_tier["A"])
        if tier_a_count > self.corpus_cap:
            msg = (
                f"Corpus cap HALT: Tier A alone contains {tier_a_count} documents, "
                f"exceeding the hard ceiling of {self.corpus_cap}."
            )
            logger.critical(msg)
            conn.close()
            # Write failing report
            report = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "halted_cap_exceeded",
                "error": msg,
                "corpus_cap": self.corpus_cap,
                "tier_A_count": tier_a_count,
            }
            with open(self.report_path, "w", encoding="utf-8") as rf:
                json.dump(report, rf, indent=2)
            raise CorpusCapExceededError(msg)

        # Truncate lower tiers if necessary to respect corpus cap
        active_candidates: List[Dict[str, Any]] = list(candidates_by_tier["A"])
        tier_b_truncated = 0
        tier_c_truncated = 0

        for item in candidates_by_tier["B"]:
            if len(active_candidates) < self.corpus_cap:
                active_candidates.append(item)
            else:
                tier_b_truncated += 1

        for item in candidates_by_tier["C"]:
            if len(active_candidates) < self.corpus_cap:
                active_candidates.append(item)
            else:
                tier_c_truncated += 1

        # 5. Process candidate documents
        counters = {
            "added": 0,
            "updated": 0,
            "unchanged": 0,
            "failed": 0,
            "resumed_skipped": 0,
        }
        failure_reasons: List[Dict[str, str]] = []
        embedding_calls = 0
        docs_processed = 0

        target_batch = (
            min(len(active_candidates), max_docs_this_run)
            if max_docs_this_run is not None
            else len(active_candidates)
        )

        # Set up signal handling
        interrupted = False

        def handle_signal(signum: int, frame: Any) -> None:
            nonlocal interrupted
            interrupted = True
            sys.stderr.write(f"\n[SIGNAL] Received signal {signum}; finishing current document and saving state...\n")

        old_sigint = None
        old_sigterm = None
        try:
            old_sigint = signal.signal(signal.SIGINT, handle_signal)
            old_sigterm = signal.signal(signal.SIGTERM, handle_signal)
        except (ValueError, AttributeError):
            # Happens if not main thread (e.g. in some test fixtures)
            pass

        def emit_heartbeat() -> None:
            now_t = time.time()
            elapsed = now_t - start_time
            if docs_processed > 0 and docs_processed < target_batch:
                avg = elapsed / docs_processed
                eta = avg * (target_batch - docs_processed)
            else:
                eta = 0.0
            elapsed_str = format_duration(elapsed)
            eta_str = format_duration(eta)
            print(
                f"[heartbeat] {docs_processed} processed · {counters['added']} added · "
                f"{counters['unchanged']} unchanged · {counters['failed']} failed · "
                f"elapsed {elapsed_str} · eta {eta_str}",
                flush=True,
            )

        status = "success"

        try:
            for item in active_candidates:
                if interrupted:
                    status = "interrupted"
                    break

                if max_docs_this_run is not None and docs_processed >= max_docs_this_run:
                    status = "batch_complete"
                    break

                url = item["url"]
                tier = item["tier"]
                product = item["product"]
                license_name = item["license"]
                cadence = item.get("cadence", "daily")

                prev_state = get_crawl_state(conn, url)

                # Resume check (TTL skip): skip known fresh documents without fetch and without embed
                if resume and prev_state and prev_state.get("last_status") == "ok":
                    last_seen_str = prev_state.get("last_seen")
                    if last_seen_str:
                        try:
                            last_seen_dt = datetime.fromisoformat(last_seen_str)
                            if last_seen_dt.tzinfo is None:
                                last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
                            now_dt = datetime.now(timezone.utc)
                            ttl_sec = CADENCE_TTL_SECONDS.get(cadence, 24 * 3600)
                            if (now_dt - last_seen_dt).total_seconds() < ttl_sec:
                                counters["unchanged"] += 1
                                counters["resumed_skipped"] += 1
                                docs_processed += 1
                                if not quiet:
                                    print(f"[docs] {docs_processed:03d}/{target_batch:03d}  skip  {url}  (resumed)", flush=True)
                                if docs_processed % progress_every == 0 or docs_processed == target_batch:
                                    emit_heartbeat()
                                continue
                        except Exception as e:
                            logger.debug("Failed parsing last_seen timestamp for %s: %s", url, e)

                # Fetch document
                if mock_docs and url in mock_docs:
                    mock_data = mock_docs[url]
                    fetch_res = FetchResult(
                        url=url,
                        status="ok",
                        http_status=200,
                        title=mock_data.get("title", "Mock Title"),
                        text=mock_data.get("text", "Mock document content body text."),
                        canonical_url=url,
                        headings=mock_data.get("headings", ["Mock Title"]),
                        extraction_method="http",
                    )
                else:
                    fetch_res = self.fetcher.fetch_url(url=url, allow_browser_fallback=True)

                docs_processed += 1

                if fetch_res.status != "ok" or not fetch_res.text:
                    counters["failed"] += 1
                    err_msg = fetch_res.error_message or "Fetch or extraction failed"
                    failure_reasons.append({
                        "url": url,
                        "reason": err_msg,
                    })
                    update_crawl_state(conn, url, content_hash="", status="failed")
                    if not quiet:
                        print(f"[docs] {docs_processed:03d}/{target_batch:03d}  FAIL  {url}  ({err_msg})", flush=True)
                    if docs_processed % progress_every == 0 or docs_processed == target_batch:
                        emit_heartbeat()
                    continue

                # Compute content hash for incremental detection
                content_h = compute_content_hash(fetch_res.text)

                # Incremental check: if previous content_hash matches and was OK -> skip embedding!
                if prev_state and prev_state.get("content_hash") == content_h and prev_state.get("last_status") == "ok":
                    counters["unchanged"] += 1
                    update_crawl_state(conn, url, content_hash=content_h, status="ok")
                    if not quiet:
                        print(f"[docs] {docs_processed:03d}/{target_batch:03d}  skip  {url}  (unchanged)", flush=True)
                    if docs_processed % progress_every == 0 or docs_processed == target_batch:
                        emit_heartbeat()
                    continue

                # Semantic chunking
                doc_id = compute_doc_id(url)
                chunks = chunk_document(
                    doc_id=doc_id,
                    text=fetch_res.text,
                    title=fetch_res.title,
                )

                if not chunks:
                    counters["failed"] += 1
                    failure_reasons.append({
                        "url": url,
                        "reason": "Document chunking produced zero chunks",
                    })
                    update_crawl_state(conn, url, content_hash=content_h, status="failed")
                    if not quiet:
                        print(f"[docs] {docs_processed:03d}/{target_batch:03d}  FAIL  {url}  (0 chunks)", flush=True)
                    if docs_processed % progress_every == 0 or docs_processed == target_batch:
                        emit_heartbeat()
                    continue

                # Dense embeddings (batched)
                chunk_texts = [c.text for c in chunks]
                vectors = self.embedding_engine.embed_texts(chunk_texts)
                embedding_calls += len(chunks)

                # Idempotent upsert
                upsert_document(
                    conn=conn,
                    source_url=url,
                    title=fetch_res.title,
                    product=product,
                    tier=tier,
                    license_name=license_name,
                    text=fetch_res.text,
                    chunks=chunks,
                    embeddings=vectors,
                    status="ok",
                )

                if prev_state:
                    counters["updated"] += 1
                    if not quiet:
                        print(f"[docs] {docs_processed:03d}/{target_batch:03d}  ok    {url}  (updated)", flush=True)
                else:
                    counters["added"] += 1
                    if not quiet:
                        print(f"[docs] {docs_processed:03d}/{target_batch:03d}  ok    {url}", flush=True)

                if docs_processed % progress_every == 0 or docs_processed == target_batch:
                    emit_heartbeat()

            if max_docs_this_run is not None and docs_processed >= max_docs_this_run and status != "interrupted":
                status = "batch_complete"

        finally:
            # Restore signal handlers
            if old_sigint is not None:
                signal.signal(signal.SIGINT, old_sigint)
            if old_sigterm is not None:
                signal.signal(signal.SIGTERM, old_sigterm)

        # 6. Verify post-conditions
        final_immersion_count = check_immersion_chunks(conn)
        conn.close()

        if final_immersion_count not in (0, 80):
            raise RuntimeError(
                f"Post-condition failed: immersion_chunks count {final_immersion_count} != 80"
            )

        duration_s = round(time.time() - start_time, 2)
        db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        # Counter Contract Assertion:
        # docs_this_run == added + updated + unchanged + failed
        # resumed_skipped is a subset of unchanged
        docs_this_run = docs_processed
        expected_docs = counters["added"] + counters["updated"] + counters["unchanged"] + counters["failed"]
        if docs_this_run != expected_docs:
            raise RuntimeError(
                f"Counter contract failure: docs_this_run ({docs_this_run}) != "
                f"added({counters['added']}) + updated({counters['updated']}) + "
                f"unchanged({counters['unchanged']}) + failed({counters['failed']})"
            )
        if counters["resumed_skipped"] > counters["unchanged"]:
            raise RuntimeError(
                f"Counter contract failure: resumed_skipped ({counters['resumed_skipped']}) > "
                f"unchanged ({counters['unchanged']})"
            )

        ratio = self.fetcher.metrics.http_ratio

        # 7. Emit Crawl Report (Extended Schema)
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "total_candidates": len(active_candidates),
            "docs_this_run": docs_this_run,
            "resumed_skipped": counters["resumed_skipped"],
            "added": counters["added"],
            "updated": counters["updated"],
            "unchanged": counters["unchanged"],
            "failed": counters["failed"],
            "embedding_calls": embedding_calls,
            "duration_s": duration_s,
            "failure_reasons": failure_reasons,
            "tier_counts": {
                "A": len(candidates_by_tier["A"]),
                "B": len(candidates_by_tier["B"]) - tier_b_truncated,
                "C": len(candidates_by_tier["C"]) - tier_c_truncated,
            },
            "truncations": {
                "tier_B": tier_b_truncated,
                "tier_C": tier_c_truncated,
            },
            "metrics": {
                "http_extracted": self.fetcher.metrics.http_extracted,
                "browser_fallback_invoked": self.fetcher.metrics.browser_fallback_invoked,
                "browser_fallback_succeeded": self.fetcher.metrics.browser_fallback_succeeded,
                "http_ratio": round(ratio, 4) if ratio is not None else None,
                "http_ratio_status": "ok" if ratio is not None else "insufficient_data",
            },
            "db_size_bytes": db_size_bytes,
        }

        with open(self.report_path, "w", encoding="utf-8") as rf:
            json.dump(report, rf, indent=2)

        if status == "interrupted":
            print(f"\n[INTERRUPTED] Progress saved. Run with --resume to continue from checkpoint.\n", flush=True)

        return report


def main() -> None:
    """CLI entrypoint for knowledge pipeline build."""
    parser = argparse.ArgumentParser(description="GFS Immersion Hub Knowledge Pipeline Build (P1 / P1.5)")
    parser.add_argument(
        "--registry",
        default="01_harvester_pipeline/crawl/seed_registry.yaml",
        help="Path to seed_registry.yaml",
    )
    parser.add_argument(
        "--db",
        default="data/gfs_immersion_2026.sqlite",
        help="Path to SQLite knowledge base",
    )
    parser.add_argument(
        "--report",
        default="01_harvester_pipeline/work/crawl_report.json",
        help="Path to output crawl_report.json",
    )
    parser.add_argument(
        "--corpus-cap",
        type=int,
        default=DEFAULT_CORPUS_CAP,
        help="Corpus document hard cap (global ceiling)",
    )
    parser.add_argument(
        "--max-docs-this-run",
        type=int,
        default=DEFAULT_BATCH_BUDGET,
        help="Per-run batch budget: stop cleanly after N documents processed",
    )
    parser.add_argument(
        "--max-urls-per-source",
        type=int,
        default=25,
        help="Max URLs to discover per source",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip fresh documents recorded in crawl_state without fetch and without embed",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="Heartbeat progress interval (number of docs)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-document log lines (heartbeats remain active)",
    )

    args = parser.parse_args()
    builder = KnowledgeBaseBuilder(
        registry_path=args.registry,
        db_path=args.db,
        report_path=args.report,
        corpus_cap=args.corpus_cap,
    )

    try:
        report = builder.run(
            max_urls_per_source=args.max_urls_per_source,
            max_docs_this_run=args.max_docs_this_run,
            resume=args.resume,
            progress_every=args.progress_every,
            quiet=args.quiet,
        )
        print("\n=== Crawl Report Summary ===")
        print(f"Status:             {report['status']}")
        print(f"Docs this run:      {report['docs_this_run']}")
        print(f"Resumed skipped:    {report['resumed_skipped']}")
        print(f"Added:              {report['added']}")
        print(f"Updated:            {report['updated']}")
        print(f"Unchanged:          {report['unchanged']}")
        print(f"Failed:             {report['failed']}")
        print(f"Embeddings generated: {report['embedding_calls']}")
        print(f"Duration:           {report['duration_s']}s")
        print(f"Report:             {args.report}")

        if report["status"] == "interrupted":
            sys.exit(130)
        sys.exit(0)
    except CorpusCapExceededError as e:
        sys.stderr.write(f"\n[FATAL] {e}\n")
        sys.exit(1)
    except ModelLoadError as e:
        sys.stderr.write(f"\n[FATAL] Model Load Failure: {e}\n")
        sys.exit(78)
    except Exception as e:
        sys.stderr.write(f"\n[ERROR] Pipeline run failed: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
