"""Normalise and clean extracted HTML documents into structured Markdown.

Conforms to Sprint 08 / FR-04:
- Strips navigation chrome, headers, footers, sidebars, forms, and scripts.
- Preserves document headings (H1-H6) as Markdown hierarchy.
- Preserves code blocks and tables.
- Extracts canonical URL if present in <link rel="canonical">.
- Preserves semantic article structure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

import lxml.html
from lxml.html.clean import Cleaner
from readability import Document

logger = logging.getLogger(__name__)

HTML_CLEANER = Cleaner(
    scripts=True,
    javascript=True,
    comments=True,
    style=True,
    links=False,
    meta=False,
    page_structure=False,
    processing_instructions=True,
    embedded=True,
    frames=True,
    forms=True,
    annoying_tags=True,
    remove_unknown_tags=False,
    kill_tags=["nav", "header", "footer", "aside", "noscript", "svg"],
)


@dataclass
class ExtractedDocument:
    title: str
    text: str
    canonical_url: str
    headings: List[str] = field(default_factory=list)
    is_empty: bool = False


def _node_to_markdown(element: lxml.html.HtmlElement) -> str:
    """Convert an lxml HTML element and its descendants into clean Markdown text."""
    tag = element.tag.lower() if isinstance(element.tag, str) else ""

    # Heading elements
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        text = " ".join((element.text_content() or "").split())
        if not text:
            return ""
        return f"\n\n{'#' * level} {text}\n\n"

    # Code blocks
    if tag == "pre":
        code_text = element.text_content() or ""
        # Check if code element has a language class
        code_elem = element.find(".//code")
        lang = ""
        if code_elem is not None:
            cls = code_elem.get("class", "")
            match = re.search(r"(?:lang|language)-([a-zA-Z0-9_-]+)", cls)
            if match:
                lang = match.group(1)
        code_clean = code_text.strip("\r\n")
        return f"\n\n```{lang}\n{code_clean}\n```\n\n"

    # Tables
    if tag == "table":
        rows = element.xpath(".//tr")
        if not rows:
            return ""
        table_md: List[str] = ["\n\n"]
        header_done = False
        for row in rows:
            cells = row.xpath("./th | ./td")
            cell_texts = [" ".join((c.text_content() or "").split()) for c in cells]
            if not any(cell_texts):
                continue
            row_line = "| " + " | ".join(cell_texts) + " |"
            table_md.append(row_line)
            if not header_done:
                sep = "| " + " | ".join(["---"] * len(cells)) + " |"
                table_md.append(sep)
                header_done = True
        table_md.append("\n\n")
        return "\n".join(table_md)

    # Paragraphs and divisions
    if tag in ("p", "div", "section", "article"):
        inner = "".join(_node_to_markdown(child) for child in element)
        text_tail = element.text or ""
        # If no children processed markdown, use text content
        if not inner.strip():
            content = " ".join((element.text_content() or "").split())
            return f"\n\n{content}\n\n" if content else ""
        return f"\n\n{inner.strip()}\n\n"

    # Lists
    if tag in ("ul", "ol"):
        items = element.xpath("./li")
        md_items = []
        for i, item in enumerate(items, 1):
            txt = " ".join((item.text_content() or "").split())
            if not txt:
                continue
            prefix = f"{i}. " if tag == "ol" else "- "
            md_items.append(f"{prefix}{txt}")
        if md_items:
            return "\n\n" + "\n".join(md_items) + "\n\n"
        return ""

    # Blockquote
    if tag == "blockquote":
        txt = " ".join((element.text_content() or "").split())
        return f"\n\n> {txt}\n\n" if txt else ""

    # Fallback: recurse into children
    pieces = []
    if element.text:
        pieces.append(element.text)
    for child in element:
        pieces.append(_node_to_markdown(child))
        if child.tail:
            pieces.append(child.tail)
    return "".join(pieces)


def clean_markdown_text(raw_text: str) -> str:
    """Consolidate extra blank lines and normalize whitespace."""
    # Collapse multiple consecutive newlines into at most two
    text = re.sub(r"\n{3,}", "\n\n", raw_text)
    # Strip trailing spaces from each line
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def extract_content(html_str: str, source_url: str = "") -> ExtractedDocument:
    """Extract clean title, markdown text, headings, and canonical URL from HTML."""
    if not html_str or not html_str.strip():
        return ExtractedDocument(
            title="",
            text="",
            canonical_url=source_url,
            headings=[],
            is_empty=True,
        )

    # 1. Parse raw HTML for metadata (title, canonical link)
    canonical_url = source_url
    title = ""
    try:
        raw_tree = lxml.html.fromstring(html_str)
        canonical_elem = raw_tree.xpath("//link[@rel='canonical']/@href")
        if canonical_elem and canonical_elem[0].strip():
            canonical_url = canonical_elem[0].strip()

        title_elem = raw_tree.xpath("//title/text()")
        if title_elem and title_elem[0].strip():
            title = " ".join(title_elem[0].split())
    except Exception as e:
        logger.debug("Failed initial metadata parse: %s", e)

    # 2. Extract readability main content
    try:
        doc = Document(html_str)
        if not title:
            title = doc.title()
        summary_html = doc.summary()
    except Exception as e:
        logger.warning("Readability extraction failed: %s; falling back to raw html", e)
        summary_html = html_str

    # 3. Clean boilerplates and unwanted elements
    try:
        content_tree = lxml.html.fromstring(summary_html)
        content_tree = HTML_CLEANER.clean_html(content_tree)
    except Exception as e:
        logger.warning("HTML cleaning failed: %s", e)
        content_tree = lxml.html.fromstring(summary_html)

    # 4. Extract headings list for structure tracking
    headings: List[str] = []
    for h in content_tree.xpath(".//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6"):
        h_text = " ".join((h.text_content() or "").split())
        if h_text and h_text not in headings:
            headings.append(h_text)

    # 5. Convert to clean Markdown
    markdown = _node_to_markdown(content_tree)
    clean_text = clean_markdown_text(markdown)

    is_empty = len(clean_text) < 50

    doc_title = title.strip() if title and title.strip() != "[no-title]" else ""
    if not doc_title:
        doc_title = headings[0] if headings else "Untitled Document"

    return ExtractedDocument(
        title=doc_title,
        text=clean_text,
        canonical_url=canonical_url,
        headings=headings,
        is_empty=is_empty,
    )
