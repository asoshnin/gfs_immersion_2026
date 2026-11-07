"""Sitemap-driven and link-crawl document discovery with robots.txt compliance.

Conforms to Sprint 08 / FR-02:
- Sitemap-first discovery with bounded link-crawl fallback.
- Honour robots.txt per domain (cached per run).
- Per-domain crawl delay (default >= 1.0s).
- User-Agent identifying GFS Immersion Hub.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set

import httpx

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "GFS-Immersion-Hub-Crawler/1.0 (+https://gfs-immersion.vwoosh.com)"
DEFAULT_CRAWL_DELAY = 1.0  # seconds per domain


class RobotsCache:
    """Caches robots.txt parsers per domain to minimize network traffic and honor ToS."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT) -> None:
        self.user_agent = user_agent
        self._parsers: Dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_access: Dict[str, float] = {}

    def set_robots_txt(self, domain: str, content: str) -> None:
        """Register robots.txt content directly (e.g. from tests or fixtures)."""
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(content.splitlines())
        self._parsers[domain] = parser

    def get_parser(
        self, domain: str, client: Optional[httpx.Client] = None
    ) -> urllib.robotparser.RobotFileParser:
        if domain in self._parsers:
            return self._parsers[domain]

        parser = urllib.robotparser.RobotFileParser()
        robots_url = f"https://{domain}/robots.txt"
        parser.set_url(robots_url)

        try:
            if client is not None:
                resp = client.get(
                    robots_url,
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True,
                    timeout=5.0,
                )
            else:
                with httpx.Client(
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True,
                    timeout=5.0,
                ) as temp_client:
                    resp = temp_client.get(robots_url)

            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
            elif resp.status_code in (401, 403):
                # Disallow all if forbidden
                parser.parse(["User-agent: *", "Disallow: /"])
            else:
                # 404 or other 4xx/5xx: standard convention allows crawling
                parser.parse(["User-agent: *", "Disallow:"])
        except Exception as e:
            logger.warning("Failed to fetch robots.txt for %s: %s; allowing crawl", domain, e)
            parser.parse(["User-agent: *", "Disallow:"])

        self._parsers[domain] = parser
        return parser

    def can_fetch(
        self, url: str, client: Optional[httpx.Client] = None
    ) -> bool:
        """Return True if robots.txt permits fetching url with our user_agent."""
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        if not domain:
            return False
        parser = self.get_parser(domain, client=client)
        return parser.can_fetch(self.user_agent, url)

    def enforce_delay(self, domain: str, min_delay: float = DEFAULT_CRAWL_DELAY) -> None:
        """Enforce rate limit delay between successive requests to the same domain."""
        now = time.time()
        last = self._last_access.get(domain, 0.0)
        elapsed = now - last
        if elapsed < min_delay:
            time.sleep(min_delay - elapsed)
        self._last_access[domain] = time.time()


def parse_sitemap_xml(xml_content: str) -> List[str]:
    """Parse sitemap XML (urlset or sitemapindex) and extract URLs."""
    urls: List[str] = []
    if not xml_content or not xml_content.strip():
        return urls

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.warning("Failed to parse sitemap XML: %s", e)
        return urls

    # XML namespaces are common in sitemaps (e.g. xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    tag_clean = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    if tag_clean == "urlset":
        for child in root:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag == "url":
                for elem in child:
                    elem_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if elem_tag == "loc" and elem.text:
                        urls.append(elem.text.strip())
    elif tag_clean == "sitemapindex":
        for child in root:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag == "sitemap":
                for elem in child:
                    elem_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if elem_tag == "loc" and elem.text:
                        urls.append(elem.text.strip())

    return urls


def extract_links_from_html(html: str, base_url: str) -> List[str]:
    """Extract bounded in-scope hyperlink hrefs from HTML markup."""
    href_pattern = re.compile(r'<a\s+(?:[^>]*?\s+)?href=["\']([^"\']+)["\']', re.IGNORECASE)
    base_parsed = urllib.parse.urlparse(base_url)
    discovered: List[str] = []

    for match in href_pattern.finditer(html):
        href = match.group(1).strip()
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue

        resolved = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(resolved)

        # Same domain and scheme
        if parsed.netloc == base_parsed.netloc and parsed.scheme in ("http", "https"):
            # Strip fragments and queries for canonical doc URLs
            canonical = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
            if canonical not in discovered:
                discovered.append(canonical)

    return discovered


def discover_source_urls(
    source: Dict[str, Any],
    client: Optional[httpx.Client] = None,
    robots_cache: Optional[RobotsCache] = None,
    max_urls: int = 50,
) -> List[str]:
    """Discover URLs for a single registry source entry respecting robots.txt and base_url scope."""
    if robots_cache is None:
        robots_cache = RobotsCache()

    base_url = source.get("base_url", "")
    sitemap_url = source.get("sitemap_url")
    discovered: List[str] = []
    seen: Set[str] = set()

    should_close = False
    active_client = client
    if active_client is None:
        active_client = httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            follow_redirects=True,
            timeout=10.0,
        )
        should_close = True

    try:
        def add_if_allowed(u: str) -> None:
            clean = u.split("#")[0].strip()
            if not clean or clean in seen:
                return
            if not clean.startswith(base_url) and not base_url.startswith(clean):
                # Scope guard: ensure within base_url scope
                if urllib.parse.urlparse(clean).netloc != urllib.parse.urlparse(base_url).netloc:
                    return
            if robots_cache.can_fetch(clean, client=active_client):
                seen.add(clean)
                discovered.append(clean)

        # 1. Sitemap discovery if available
        candidate_sitemaps = []
        if sitemap_url:
            candidate_sitemaps.append(sitemap_url)
        else:
            # Common sitemap fallback
            parsed_base = urllib.parse.urlparse(base_url)
            candidate_sitemaps.append(f"{parsed_base.scheme}://{parsed_base.netloc}/sitemap.xml")

        for sm_url in candidate_sitemaps:
            if not robots_cache.can_fetch(sm_url, client=active_client):
                continue
            parsed_sm = urllib.parse.urlparse(sm_url)
            robots_cache.enforce_delay(parsed_sm.netloc)
            try:
                resp = active_client.get(sm_url, follow_redirects=True, timeout=10.0)
                if resp.status_code == 200:
                    found_urls = parse_sitemap_xml(resp.text)
                    for u in found_urls:
                        if len(discovered) >= max_urls:
                            break
                        if u.startswith(base_url):
                            add_if_allowed(u)
                    if discovered:
                        break
            except Exception as e:
                logger.debug("Sitemap probe failed for %s: %s", sm_url, e)

        # 2. If no URLs from sitemap, fallback to base_url and bounded HTML link crawl
        if not discovered and base_url:
            add_if_allowed(base_url)
            if discovered:
                parsed_base = urllib.parse.urlparse(base_url)
                robots_cache.enforce_delay(parsed_base.netloc)
                try:
                    resp = active_client.get(base_url, follow_redirects=True, timeout=10.0)
                    if resp.status_code == 200:
                        html_links = extract_links_from_html(resp.text, base_url)
                        for link in html_links:
                            if len(discovered) >= max_urls:
                                break
                            if link.startswith(base_url):
                                add_if_allowed(link)
                except Exception as e:
                    logger.debug("Base page link crawl failed for %s: %s", base_url, e)

        return discovered[:max_urls]
    finally:
        if should_close:
            active_client.close()
