"""Fetch plane with HTTP readability primary path and Playwright fallback.

Conforms to Sprint 08 / FR-03, FR-04:
- Primary: Plain HTTP fetch + readability extraction (fast, lightweight).
- Fallback: Headless browser (Playwright) triggered ONCE only when primary extraction
  is empty or clearly incomplete (<50 characters). Never an always-on service.
- Exponential backoff: max 3 retries, base 1s, x2 growth, 30s ceiling, jitter.
- Fail-closed: Failures recorded cleanly with status, never swallowed or silently skipped.
- Metric tracking for HTTP vs browser share.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from crawl.discovery import DEFAULT_USER_AGENT
from crawl.extract import ExtractedDocument, extract_content

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_DELAY_SEC = 1.0
MAX_DELAY_SEC = 30.0


@dataclass
class FetchResult:
    url: str
    status: str  # 'ok' | 'failed' | 'skipped'
    http_status: Optional[int] = None
    title: str = ""
    text: str = ""
    canonical_url: str = ""
    headings: List[str] = field(default_factory=list)
    extraction_method: str = "none"  # 'http' | 'browser_fallback' | 'none'
    error_message: Optional[str] = None
    retries_used: int = 0


@dataclass
class FetchMetrics:
    http_attempted: int = 0
    http_extracted: int = 0
    browser_fallback_invoked: int = 0
    browser_fallback_succeeded: int = 0
    failed: int = 0

    @property
    def http_ratio(self) -> Optional[float]:
        """Share of successful extractions that came from the HTTP path.

        Returns None when nothing was extracted — never a synthetic 1.0.
        """
        total = self.http_extracted + self.browser_fallback_succeeded
        if total == 0:
            return None
        return self.http_extracted / total


class Fetcher:
    """Orchestrates HTTP fetching with exponential backoff and Playwright fallback."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT) -> None:
        self.user_agent = user_agent
        self.metrics = FetchMetrics()

    def _fetch_with_playwright(self, url: str) -> Optional[str]:
        """Trigger Playwright once to render client-side JS."""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.user_agent)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                # Short wait for hydration if needed
                page.wait_for_timeout(1000)
                content = page.content()
                browser.close()
                return content
        except Exception as e:
            logger.warning("Playwright fallback failed for %s: %s", url, e)
            return None

    def fetch_url(
        self,
        url: str,
        client: Optional[httpx.Client] = None,
        allow_browser_fallback: bool = True,
        raw_html_override: Optional[str] = None,
    ) -> FetchResult:
        """Fetch URL via HTTP, falling back to Playwright if extracted text is empty."""
        self.metrics.http_attempted += 1

        # Check if caller supplied HTML directly (e.g. fixtures / offline tests)
        if raw_html_override is not None:
            doc = extract_content(raw_html_override, source_url=url)
            if not doc.is_empty:
                self.metrics.http_extracted += 1
                return FetchResult(
                    url=url,
                    status="ok",
                    http_status=200,
                    title=doc.title,
                    text=doc.text,
                    canonical_url=doc.canonical_url,
                    headings=doc.headings,
                    extraction_method="http",
                )
            # If provided HTML was empty, allow fallback below if enabled
            http_html = raw_html_override
            last_status = 200
            retries = 0
        else:
            http_html: Optional[str] = None
            last_status: Optional[int] = None
            retries = 0

            # Bounded HTTP fetch with backoff
            local_client = client or httpx.Client(
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
                timeout=15.0,
            )
            should_close = client is None

            try:
                for attempt in range(MAX_RETRIES):
                    retries = attempt
                    try:
                        resp = local_client.get(url)
                        last_status = resp.status_code
                        if resp.status_code == 200:
                            http_html = resp.text
                            break
                        if resp.status_code in (429, 500, 502, 503, 504):
                            if attempt < MAX_RETRIES - 1:
                                backoff = min(
                                    BASE_DELAY_SEC * (2**attempt) + random.uniform(0.1, 0.5),
                                    MAX_DELAY_SEC,
                                )
                                logger.info(
                                    "HTTP %s on %s; backing off %.2fs (attempt %d/%d)",
                                    resp.status_code,
                                    url,
                                    backoff,
                                    attempt + 1,
                                    MAX_RETRIES,
                                )
                                time.sleep(backoff)
                                continue
                        # Non-retryable status (404, 401, 403) or retries exhausted
                        break
                    except (httpx.RequestError, httpx.TimeoutException) as req_err:
                        logger.warning("Request error on %s: %s", url, req_err)
                        if attempt < MAX_RETRIES - 1:
                            backoff = min(
                                BASE_DELAY_SEC * (2**attempt) + random.uniform(0.1, 0.5),
                                MAX_DELAY_SEC,
                            )
                            time.sleep(backoff)
                            continue
                        break
            finally:
                if should_close:
                    local_client.close()

        # Case 1: HTTP returned HTML -> test readability extraction
        if http_html:
            doc = extract_content(http_html, source_url=url)
            if not doc.is_empty:
                self.metrics.http_extracted += 1
                return FetchResult(
                    url=url,
                    status="ok",
                    http_status=last_status,
                    title=doc.title,
                    text=doc.text,
                    canonical_url=doc.canonical_url,
                    headings=doc.headings,
                    extraction_method="http",
                    retries_used=retries,
                )

        # Case 2: Extraction empty or HTTP failed -> trigger Playwright fallback ONCE if allowed
        if allow_browser_fallback:
            self.metrics.browser_fallback_invoked += 1
            logger.info("Triggering Playwright fallback for %s (primary extraction empty)", url)
            browser_html = self._fetch_with_playwright(url)
            if browser_html:
                doc = extract_content(browser_html, source_url=url)
                if not doc.is_empty:
                    self.metrics.browser_fallback_succeeded += 1
                    return FetchResult(
                        url=url,
                        status="ok",
                        http_status=200,
                        title=doc.title,
                        text=doc.text,
                        canonical_url=doc.canonical_url,
                        headings=doc.headings,
                        extraction_method="browser_fallback",
                        retries_used=retries,
                    )

        # Case 3: Failed completely
        self.metrics.failed += 1
        err = f"HTTP status {last_status}" if last_status else "Fetch and fallback failed"
        if http_html and not doc.text:
            err = "Extraction empty after HTTP and browser fallback"

        return FetchResult(
            url=url,
            status="failed",
            http_status=last_status,
            error_message=err,
            extraction_method="none",
            retries_used=retries,
        )

