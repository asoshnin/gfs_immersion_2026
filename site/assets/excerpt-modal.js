// Sprint 24 — clickable citations.
//
// The assistant cites chunks as [session:session_03::chunk_003] or [doc:<id>#0000]
// (and their excerpt number, e.g. [1]). This module turns those tokens into
// buttons that open the ORIGINAL indexed text with the cited chunk highlighted.
// It fetches /api/excerpt, which reconstructs the text from the corpus.
//
// Self-contained: injects its own styles and dialog, so both the hub page and
// any other page can import it without extra markup.

let dialog = null;
let bodyEl = null;
let titleEl = null;
let linkEl = null;
let noteEl = null;
let lastFocus = null;

function injectStyles() {
  if (document.getElementById('excerpt-modal-styles')) return;
  const css = `
  .cite-link{cursor:pointer;color:#7c9cff;text-decoration:none;border-bottom:1px dotted currentColor;
    font-size:.92em;padding:0 1px;border-radius:3px}
  .cite-link:hover,.cite-link:focus{background:rgba(124,156,255,.18);outline:none}
  #excerpt-modal{position:fixed;inset:0;z-index:80;display:flex;align-items:center;justify-content:center;
    background:rgba(0,0,0,.55);padding:16px}
  #excerpt-modal[hidden]{display:none}
  #excerpt-modal .ex-card{background:#fff;color:#111;border-radius:12px;width:min(880px,96vw);
    max-height:88vh;display:flex;flex-direction:column;overflow:hidden;
    box-shadow:0 18px 50px rgba(0,0,0,.35);font-size:14px}
  @media (prefers-color-scheme:dark){#excerpt-modal .ex-card{background:#15181d;color:#e8eaed}}
  #excerpt-modal .ex-head{display:flex;align-items:flex-start;gap:12px;padding:14px 16px;
    border-bottom:1px solid rgba(128,128,128,.28)}
  #excerpt-modal .ex-title{font-weight:700;font-size:15px;line-height:1.35;flex:1}
  #excerpt-modal .ex-note{font-size:11.5px;opacity:.7;margin-top:3px}
  #excerpt-modal .ex-x{border:0;background:transparent;font-size:22px;line-height:1;cursor:pointer;
    color:inherit;opacity:.6;padding:0 4px}
  #excerpt-modal .ex-x:hover{opacity:1}
  #excerpt-modal .ex-body{padding:14px 18px;overflow:auto;white-space:pre-wrap;line-height:1.55;
    font-size:13.5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  #excerpt-modal mark{background:#ffe066;color:#111;border-radius:3px;padding:1px 2px;
    box-shadow:0 0 0 2px rgba(255,224,102,.35)}
  #excerpt-modal .ex-src{display:inline-block;font-size:12px;margin-top:4px;color:#7c9cff}
  `;
  const s = document.createElement('style');
  s.id = 'excerpt-modal-styles';
  s.textContent = css;
  document.head.appendChild(s);
}

function ensureDialog() {
  if (dialog) return;
  injectStyles();
  dialog = document.createElement('div');
  dialog.id = 'excerpt-modal';
  dialog.hidden = true;
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', 'Original source excerpt');
  dialog.innerHTML =
    '<div class="ex-card" role="document">' +
    '<div class="ex-head">' +
    '<div style="flex:1"><div class="ex-title"></div>' +
    '<div class="ex-note">Indexed text as retrieved by the hub — the cited passage is highlighted.</div>' +
    '<a class="ex-src" target="_blank" rel="noopener" hidden></a></div>' +
    '<button class="ex-x" type="button" aria-label="Close">&times;</button>' +
    '</div>' +
    '<div class="ex-body"></div>' +
    '</div>';
  document.body.appendChild(dialog);
  titleEl = dialog.querySelector('.ex-title');
  linkEl = dialog.querySelector('.ex-src');
  noteEl = dialog.querySelector('.ex-note');
  bodyEl = dialog.querySelector('.ex-body');
  dialog.querySelector('.ex-x').addEventListener('click', close);
  dialog.addEventListener('click', (e) => { if (e.target === dialog) close(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !dialog.hidden) close();
  });
}

function close() {
  if (!dialog) return;
  dialog.hidden = true;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

async function openKey(key) {
  if (!key) return;
  ensureDialog();
  lastFocus = document.activeElement;
  titleEl.textContent = 'Loading…';
  noteEl.hidden = false;
  linkEl.hidden = true;
  bodyEl.textContent = '';
  dialog.hidden = false;
  try {
    const r = await fetch('/api/excerpt?key=' + encodeURIComponent(key));
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    titleEl.textContent = d.title || key;
    noteEl.textContent = d.kind === 'session_summary'
      ? 'Authored cohort summary from the hub catalog.'
      : 'Indexed text as retrieved by the hub — the cited passage is highlighted.';
    if (d.url && /^https?:\/\//i.test(d.url)) {
      linkEl.href = d.url;
      linkEl.textContent = d.url.length > 90 ? d.url.slice(0, 88) + '…' : d.url;
      linkEl.hidden = false;
    }
    const text = d.doc_text || '';
    const start = Math.max(0, Math.min(Number(d.start) || 0, text.length));
    const end = Math.max(start, Math.min(Number(d.end) || 0, text.length));
    bodyEl.textContent = '';
    bodyEl.appendChild(document.createTextNode(text.slice(0, start)));
    const mark = document.createElement('mark');
    mark.textContent = text.slice(start, end) || '';
    bodyEl.appendChild(mark);
    bodyEl.appendChild(document.createTextNode(text.slice(end)));
    bodyEl.scrollTop = Math.max(0, (mark.offsetTop || 0) - 60);
  } catch (err) {
    titleEl.textContent = 'Could not load this excerpt';
    noteEl.hidden = true;
    bodyEl.textContent = 'The citation could not be resolved right now (' + (err && err.message) + ').';
  }
}

/** Turn citation tokens inside `container`'s text nodes into clickable buttons. */
export function wire(container, excerpts) {
  if (!container) return;
  const list = Array.isArray(excerpts) ? excerpts : [];
  const tokenRe = /\[([a-z]+:[^\][\s]+)\]|\[(\d+)\]/g;

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
  const targets = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement && node.parentElement.classList.contains('cite-link')) continue;
    if (node.nodeValue && tokenRe.test(node.nodeValue)) targets.push(node);
    tokenRe.lastIndex = 0;
  }

  targets.forEach((textNode) => {
    const frag = document.createDocumentFragment();
    const text = textNode.nodeValue;
    let last = 0;
    let m;
    tokenRe.lastIndex = 0;
    while ((m = tokenRe.exec(text)) !== null) {
      const key = m[1] || null;
      const num = m[2] ? Number(m[2]) : null;
      let resolved = key;
      let label = key;
      if (!resolved && num) {
        const ex = list[num - 1];
        resolved = ex && ex.fusion_key;
        label = num + '';
      }
      if (last < m.index) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      if (resolved) {
        const a = document.createElement('a');
        a.href = '#';
        a.className = 'cite-link';
        a.dataset.key = resolved;
        a.textContent = '[' + (label === null || label === undefined ? resolved : label) + ']';
        a.title = 'Show ' + resolved + ' in the original';
        frag.appendChild(a);
      } else {
        frag.appendChild(document.createTextNode(m[0]));
      }
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    textNode.parentNode.replaceChild(frag, textNode);
  });
}

/** Delegated click handling: call once, e.g. on the chat messages container. */
export function bind(container) {
  if (!container || container.dataset.citeBound === '1') return;
  container.dataset.citeBound = '1';
  container.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('.cite-link');
    if (!a) return;
    e.preventDefault();
    openKey(a.dataset.key);
  });
}

export { openKey };