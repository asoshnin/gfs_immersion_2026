/**
 * markdown-lite.js — safe minimal Markdown renderer for assistant answers.
 *
 * WHY NOT a library: the answers are model output rendered into our DOM. A full
 * Markdown library adds a dependency and, more importantly, most of them allow
 * raw HTML by default — which is an XSS vector when the text comes from a
 * third-party model. This renderer escapes ALL input first, then re-introduces
 * a fixed, tiny set of formatting constructs. There is no path from input text
 * to raw HTML.
 *
 * Supported: headings (#..######), bold, italic, inline code, fenced code
 * blocks, bullet + numbered lists, blockquotes, horizontal rules, links
 * (http/https ONLY), and paragraphs with hard line breaks.
 *
 * Deliberately unsupported: raw HTML, images, tables, footnotes.
 */

const ESCAPE_MAP = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** Escape every HTML-significant character. Applied to ALL text first. */
export function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ESCAPE_MAP[c]);
}

/** Only http(s) links survive; javascript:, data:, vbscript: are dropped. */
function safeHref(url) {
  const u = String(url || "").trim();
  return /^https?:\/\//i.test(u) ? u : null;
}

/**
 * Inline formatting, applied to already-escaped text.
 * Order matters: code first (so ** inside code is not bolded), then bold, italic, links.
 */
function renderInline(escaped) {
  let out = escaped;

  // `code`  -> <code>
  out = out.replace(/`([^`\n]+)`/g, "<code>$1</code>");

  // [text](url) -> anchor, http(s) only
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
    const href = safeHref(url);
    if (!href) return match; // leave the literal text if the scheme is unsafe
    return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });

  // **bold**
  out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");

  // *italic* (not part of **)
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  // __bold__
  out = out.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");

  return out;
}

/**
 * Convert model Markdown to a safe HTML string.
 * @param {string} markdown
 * @returns {string} HTML that is safe to assign to innerHTML
 */
export function renderMarkdown(markdown) {
  const src = String(markdown == null ? "" : markdown).replace(/\r\n?/g, "\n");
  const lines = src.split("\n");
  const html = [];

  let inCodeFence = false;
  let codeLang = "";
  let codeBuf = [];
  let listType = null; // 'ul' | 'ol'
  let paragraph = [];

  const closeList = () => {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  };

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
      paragraph = [];
    }
  };

  for (const rawLine of lines) {
    // Fenced code blocks take precedence over everything.
    const fence = rawLine.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      if (inCodeFence) {
        const escapedCode = escapeHtml(codeBuf.join("\n"));
        const cls = codeLang ? ` class="language-${escapeHtml(codeLang)}"` : "";
        html.push(`<pre><code${cls}>${escapedCode}</code></pre>`);
        codeBuf = [];
        codeLang = "";
        inCodeFence = false;
      } else {
        flushParagraph();
        closeList();
        inCodeFence = true;
        codeLang = fence[1] || "";
      }
      continue;
    }
    if (inCodeFence) {
      codeBuf.push(rawLine);
      continue;
    }

    const line = rawLine;

    // Blank line ends the current block.
    if (!line.trim()) {
      flushParagraph();
      closeList();
      continue;
    }

    // Horizontal rule
    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) {
      flushParagraph();
      closeList();
      html.push("<hr>");
      continue;
    }

    // Heading
    const heading = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInline(escapeHtml(heading[2].trim()))}</h${level}>`);
      continue;
    }

    // Blockquote
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      closeList();
      html.push(`<blockquote>${renderInline(escapeHtml(quote[1]))}</blockquote>`);
      continue;
    }

    // Unordered list item
    const ulItem = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ulItem) {
      flushParagraph();
      if (listType !== "ul") {
        closeList();
        html.push("<ul>");
        listType = "ul";
      }
      html.push(`<li>${renderInline(escapeHtml(ulItem[1]))}</li>`);
      continue;
    }

    // Ordered list item
    const olItem = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (olItem) {
      flushParagraph();
      if (listType !== "ol") {
        closeList();
        html.push("<ol>");
        listType = "ol";
      }
      html.push(`<li>${renderInline(escapeHtml(olItem[2]))}</li>`);
      continue;
    }

    // Ordinary text accumulates into a paragraph (single newline = <br>).
    paragraph.push(escapeHtml(line.trim()));
  }

  if (inCodeFence) {
    // Unclosed fence: render what we have rather than swallowing it.
    const escapedCode = escapeHtml(codeBuf.join("\n"));
    html.push(`<pre><code>${escapedCode}</code></pre>`);
  }
  flushParagraph();
  closeList();

  return html.join("\n");
}

/**
 * Build the Markdown text for clipboard: answer + a Sources section.
 * @param {string} answer
 * @param {Array<{title?:string,url?:string}>} sources
 * @param {"cited"|"uncited"|"partial"} [verification] defaults to "cited"
 * @param {string[]} [fabricated] citations removed as unverifiable
 * @returns {string}
 */
export function toClipboardMarkdown(answer, sources, verification, fabricated) {
  const tier = (verification === "uncited" || verification === "partial")
    ? verification
    : "cited";
  const parts = [];
  // Sprint 21: a copied answer must carry the same warning the screen shows.
  // Copying is where these answers leak into decks, so the caveat travels with them.
  if (tier === "uncited") {
    parts.push("> ⚠ Unverified — no source citations; may contain errors");
    parts.push("");
  } else if (tier === "partial") {
    const removed = Array.isArray(fabricated) ? fabricated.length : 0;
    parts.push("> ⚠ Partially verified — " + removed + " citation(s) removed as unverifiable");
    parts.push("");
  }
  parts.push(String(answer == null ? "" : answer).trim());
  if (tier === "partial" && Array.isArray(fabricated) && fabricated.length) {
    parts.push("");
    parts.push("### Removed (unverifiable)");
    fabricated.forEach(function (item) {
      parts.push("- " + String(item));
    });
  }
  const usable = (sources || []).filter((s) => s && s.url && /^https?:\/\//i.test(s.url));
  if (usable.length) {
    parts.push("");
    parts.push("### Sources");
    usable.forEach((s, i) => {
      const title = (s.title || s.url || "").trim();
      parts.push(`${i + 1}. [${title}](${s.url})`);
    });
  }
  return parts.join("\n");
}

/** CSS for rendered answers. Injected once per page. */
export const MARKDOWN_CSS = `
.md-body { font-size: 13px; line-height: 1.6; }
.md-body > *:first-child { margin-top: 0; }
.md-body > *:last-child { margin-bottom: 0; }
.md-body p { margin: 0 0 0.6em; }
.md-body h1,.md-body h2,.md-body h3,.md-body h4,.md-body h5,.md-body h6 {
  margin: 0.9em 0 0.4em; font-weight: 650; line-height: 1.3; }
.md-body h1 { font-size: 1.25em; } .md-body h2 { font-size: 1.15em; }
.md-body h3 { font-size: 1.05em; } .md-body h4,.md-body h5,.md-body h6 { font-size: 1em; }
.md-body ul,.md-body ol { margin: 0 0 0.6em; padding-left: 1.4em; }
.md-body ul { list-style: disc; } .md-body ol { list-style: decimal; }
.md-body li { margin: 0.15em 0; }
.md-body code { font-family: ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size: 0.92em; padding: 0.1em 0.3em; border-radius: 3px;
  background: rgba(127,127,127,0.18); }
.md-body pre { margin: 0.6em 0; padding: 0.7em 0.85em; overflow-x: auto;
  border-radius: 6px; background: rgba(127,127,127,0.14); border: 1px solid rgba(127,127,127,0.22); }
.md-body pre code { background: none; padding: 0; font-size: 0.9em; }
.md-body blockquote { margin: 0.6em 0; padding-left: 0.8em;
  border-left: 3px solid rgba(127,127,127,0.4); opacity: 0.92; }
.md-body hr { margin: 0.9em 0; border: 0; border-top: 1px solid rgba(127,127,127,0.28); }
.md-body a { text-decoration: underline; }
.md-body strong { font-weight: 650; }
`;

/** Inject the stylesheet once. */
export function ensureMarkdownStyles() {
  if (document.getElementById("md-body-style")) return;
  const style = document.createElement("style");
  style.id = "md-body-style";
  style.textContent = MARKDOWN_CSS;
  document.head.appendChild(style);
}
