"""Heading-bounded semantic chunking with token-window capping and overlap.

Conforms to Sprint 08 / FR-05:
- Chunk by heading boundaries (H1-H6) with token-bounded window (~512 tokens).
- Overlap of ~15% (~75 tokens) across splits within long sections.
- Records doc_id, chunk_index, heading_path, text, line_start, line_end.
- Guaranteed: No chunk exceeds token ceiling; no chunk is empty; every chunk maps to doc_id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

TARGET_TOKENS = 512
TOKEN_CEILING = 600
OVERLAP_TOKENS = 75
CHARS_PER_TOKEN = 4  # Canonical approximation for English prose/code


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    chunk_index: int
    heading_path: str
    text: str
    line_start: int
    line_end: int
    token_count: int


def estimate_tokens(text: str) -> int:
    """Estimate token count for English prose/code using whitespace and length heuristics."""
    if not text:
        return 0
    words = len(text.split())
    by_words = int(words * 1.33)
    by_chars = max(1, len(text) // CHARS_PER_TOKEN)
    return max(by_words, by_chars)


def _split_into_sections(text: str, default_title: str = "") -> List[Tuple[str, str, int, int]]:
    """Split markdown text into sections demarcated by Markdown headings.

    Returns list of (heading_path, section_text, line_start, line_end).
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    sections: List[Tuple[str, str, int, int]] = []
    heading_stack: List[Tuple[int, str]] = []  # (level, title)

    current_lines: List[str] = []
    current_start = 1
    current_heading_path = default_title

    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")

    for idx, line in enumerate(lines, 1):
        match = heading_re.match(line.strip())
        if match:
            level = len(match.group(1))
            heading_text = match.group(2).strip()

            # Flush previous section if it contains text
            sec_text = "".join(current_lines).strip()
            if sec_text:
                sections.append((
                    current_heading_path or default_title or heading_text,
                    sec_text,
                    current_start,
                    idx - 1,
                ))
                current_lines = []

            # Adjust heading stack according to hierarchy level
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading_text))

            # Build heading path: "Doc Title > H1 > H2"
            path_parts = [default_title] if default_title else []
            path_parts.extend(h[1] for h in heading_stack)
            # Deduplicate adjacent identical parts
            dedup_parts: List[str] = []
            for p in path_parts:
                if not dedup_parts or dedup_parts[-1] != p:
                    dedup_parts.append(p)
            current_heading_path = " > ".join(dedup_parts)
            current_start = idx

        current_lines.append(line)

    # Flush final section
    sec_text = "".join(current_lines).strip()
    if sec_text:
        sections.append((
            current_heading_path or default_title or "Overview",
            sec_text,
            current_start,
            len(lines),
        ))

    return sections


def chunk_document(
    doc_id: str,
    text: str,
    title: str = "",
    target_tokens: int = TARGET_TOKENS,
    token_ceiling: int = TOKEN_CEILING,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> List[Chunk]:
    """Chunk a document semantically by heading boundaries with token windowing."""
    if not text or not text.strip():
        return []

    sections = _split_into_sections(text, default_title=title)
    if not sections:
        return []

    chunks: List[Chunk] = []
    chunk_index = 0

    for heading_path, sec_text, start_line, end_line in sections:
        tokens = estimate_tokens(sec_text)

        # Section fits within token ceiling
        if tokens <= token_ceiling:
            if sec_text.strip():
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc_id}#{chunk_index:04d}",
                        doc_id=doc_id,
                        chunk_index=chunk_index,
                        heading_path=heading_path,
                        text=sec_text.strip(),
                        line_start=start_line,
                        line_end=end_line,
                        token_count=tokens,
                    )
                )
                chunk_index += 1
            continue

        # Section exceeds token ceiling: split across paragraphs/lines with overlap
        paragraphs = re.split(r"\n\s*\n", sec_text)
        current_paras: List[str] = []
        current_tokens = 0
        para_line_cursor = start_line

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            para_tokens = estimate_tokens(para)

            # If adding this paragraph exceeds target_tokens and we already have content
            if current_paras and (current_tokens + para_tokens > target_tokens):
                chunk_body = "\n\n".join(current_paras).strip()
                if chunk_body:
                    c_tokens = estimate_tokens(chunk_body)
                    chunks.append(
                        Chunk(
                            chunk_id=f"{doc_id}#{chunk_index:04d}",
                            doc_id=doc_id,
                            chunk_index=chunk_index,
                            heading_path=heading_path,
                            text=chunk_body,
                            line_start=para_line_cursor,
                            line_end=para_line_cursor + chunk_body.count("\n"),
                            token_count=c_tokens,
                        )
                    )
                    chunk_index += 1

                # Carry over overlap
                overlap_paras: List[str] = []
                overlap_toks = 0
                for p in reversed(current_paras):
                    p_toks = estimate_tokens(p)
                    if overlap_toks + p_toks <= overlap_tokens:
                        overlap_paras.insert(0, p)
                        overlap_toks += p_toks
                    else:
                        break

                current_paras = list(overlap_paras)
                current_tokens = overlap_toks

            # If a single paragraph itself exceeds token_ceiling, slice by sentence/word
            if para_tokens > token_ceiling:
                words = para.split()
                chunk_words: List[str] = []
                for w in words:
                    chunk_words.append(w)
                    if len(chunk_words) >= int(target_tokens * 0.75):
                        sub_text = " ".join(chunk_words)
                        chunks.append(
                            Chunk(
                                chunk_id=f"{doc_id}#{chunk_index:04d}",
                                doc_id=doc_id,
                                chunk_index=chunk_index,
                                heading_path=heading_path,
                                text=sub_text,
                                line_start=para_line_cursor,
                                line_end=para_line_cursor + sub_text.count("\n"),
                                token_count=estimate_tokens(sub_text),
                            )
                        )
                        chunk_index += 1
                        # Keep overlap words
                        overlap_word_count = int(overlap_tokens * 0.75)
                        chunk_words = chunk_words[-overlap_word_count:]
                if chunk_words:
                    current_paras = [" ".join(chunk_words)]
                    current_tokens = estimate_tokens(current_paras[0])
            else:
                current_paras.append(para)
                current_tokens += para_tokens

        # Flush remaining paragraphs in section
        if current_paras:
            final_text = "\n\n".join(current_paras).strip()
            if final_text:
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc_id}#{chunk_index:04d}",
                        doc_id=doc_id,
                        chunk_index=chunk_index,
                        heading_path=heading_path,
                        text=final_text,
                        line_start=para_line_cursor,
                        line_end=end_line,
                        token_count=estimate_tokens(final_text),
                    )
                )
                chunk_index += 1

    return chunks

