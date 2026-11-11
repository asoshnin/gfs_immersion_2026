/**
 * provider-websearch-guide.js — shared launcher for the BYO search setup guide.
 *
 * The guide markup lives in `assets/websearch-guide.html` so the portal home and
 * the AI Venture Coach render byte-identical instructions instead of drifting.
 * This module fetches that fragment once, injects it, and wires the open/close
 * controls (button, backdrop click, Escape).
 *
 * Styling is injected as a single <style> block keyed on `data-gfs-wsg` so both
 * pages get the same look without touching their own stylesheets.
 */

const GUIDE_URL = new URL("./websearch-guide.html", import.meta.url).href;
const STYLE_ID = "gfs-wsg-style";
const MODAL_ID = "wsg-modal";

let _loading = null;

function injectStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const css = `
  #${MODAL_ID}{position:fixed;inset:0;z-index:80;display:flex;align-items:center;
    justify-content:center;padding:16px;background:rgba(0,0,0,.62);overflow-y:auto}
  #${MODAL_ID}[aria-hidden="true"]{display:none}
  #${MODAL_ID} .wsg-dialog{width:100%;max-width:660px;max-height:88vh;display:flex;
    flex-direction:column;background:#0d1117;color:#e6edf3;border:1px solid #30363d;
    border-radius:12px;box-shadow:0 24px 60px rgba(0,0,0,.55);font-size:14px;line-height:1.55}
  #${MODAL_ID} .wsg-header{display:flex;align-items:center;justify-content:space-between;
    gap:12px;padding:16px 20px;border-bottom:1px solid #30363d}
  #${MODAL_ID} .wsg-header h2{margin:0;font-size:16px;font-weight:650}
  #${MODAL_ID} .wsg-close{background:none;border:0;color:#8b949e;font-size:24px;line-height:1;
    cursor:pointer;padding:0 4px}
  #${MODAL_ID} .wsg-close:hover{color:#e6edf3}
  #${MODAL_ID} .wsg-body{padding:18px 20px;overflow-y:auto}
  #${MODAL_ID} .wsg-intro p{margin:0 0 10px}
  #${MODAL_ID} .wsg-note{margin:0 0 4px;padding:8px 10px;border-left:3px solid #2f81f7;
    background:rgba(47,129,247,.1);border-radius:4px;font-size:13px}
  #${MODAL_ID} .wsg-steps{list-style:none;margin:16px 0 0;padding:0}
  #${MODAL_ID} .wsg-steps>li{margin:0 0 20px;padding:0}
  #${MODAL_ID} .wsg-steps h3{display:flex;align-items:center;gap:8px;margin:0 0 8px;
    font-size:14px;font-weight:650}
  #${MODAL_ID} .wsg-num{display:inline-flex;align-items:center;justify-content:center;
    width:22px;height:22px;flex:0 0 22px;border-radius:50%;background:#2f81f7;color:#fff;
    font-size:12px;font-weight:700}
  #${MODAL_ID} .wsg-steps p{margin:0 0 8px}
  #${MODAL_ID} .wsg-steps ul{margin:0 0 8px;padding-left:20px}
  #${MODAL_ID} .wsg-steps li li{margin:4px 0}
  #${MODAL_ID} .wsg-hint{font-size:12.5px;color:#8b949e}
  #${MODAL_ID} .wsg-domains{margin:8px 0;padding:10px 12px;background:#161b22;
    border:1px solid #30363d;border-radius:6px;font-family:ui-monospace,SFMono-Regular,
    Menlo,monospace;font-size:12px;white-space:pre-wrap;word-break:break-all;color:#7ee787}
  #${MODAL_ID} .wsg-warning{margin:14px 0 0;padding:10px 12px;border-left:3px solid #d29922;
    background:rgba(210,153,34,.1);border-radius:4px;font-size:12.5px}
  #${MODAL_ID} .wsg-warning code{background:rgba(255,255,255,.08);padding:1px 4px;border-radius:3px}
  #${MODAL_ID} .wsg-warning--legacy{border-left-color:#f85149;background:rgba(248,81,73,.1)}
  #${MODAL_ID} .wsg-footer{display:flex;justify-content:flex-end;gap:8px;
    padding:14px 20px;border-top:1px solid #30363d}
  #${MODAL_ID} .wsg-btn{padding:8px 16px;border-radius:7px;font-size:13px;font-weight:600;
    cursor:pointer;border:1px solid transparent}
  #${MODAL_ID} .wsg-btn--primary{background:#2f81f7;color:#fff}
  #${MODAL_ID} .wsg-btn--primary:hover{background:#4b93f8}
  #${MODAL_ID} a{color:#6cb6ff}
  #${MODAL_ID} code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
  @media (prefers-color-scheme:light){
    #${MODAL_ID} .wsg-dialog{background:#fff;color:#1f2328;border-color:#d0d7de}
    #${MODAL_ID} .wsg-domains{background:#f6f8fa;border-color:#d0d7de;color:#1a7f37}
    #${MODAL_ID} .wsg-hint{color:#59636e}
  }`;
  const el = document.createElement("style");
  el.id = STYLE_ID;
  el.textContent = css;
  document.head.appendChild(el);
}

async function ensureMarkup() {
  const existing = document.getElementById(MODAL_ID);
  // Return the element itself, not undefined: callers rely on the resolved value
  // to reveal the modal. Returning undefined here made every reopen a silent no-op.
  if (existing) return existing;
  if (!_loading) {
    _loading = fetch(GUIDE_URL, { credentials: "same-origin" })
      .then((res) => {
        if (!res.ok) throw new Error(`guide fetch failed: ${res.status}`);
        return res.text();
      })
      .then((html) => {
        const holder = document.createElement("div");
        holder.innerHTML = html;
        const modal = holder.querySelector(`#${MODAL_ID}`);
        if (!modal) throw new Error("guide markup missing modal root");
        document.body.appendChild(modal);
        _wire(modal);
        return modal;
      })
      .catch((err) => {
        _loading = null;
        throw err;
      });
  }
  return _loading;
}

function _wire(modal) {
  const close = () => closeWebSearchGuide();
  modal.querySelector("#wsg-close")?.addEventListener("click", close);
  modal.querySelector("#wsg-done")?.addEventListener("click", close);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) close();
  });
}

/** Open the step-by-step guide. Safe to call before the fragment has loaded. */
export async function openWebSearchGuide() {
  injectStyles();
  try {
    const modal = await ensureMarkup();
    if (!modal) return;
    modal.setAttribute("aria-hidden", "false");
    modal.querySelector("#wsg-done")?.focus();
  } catch (err) {
    // Fail visibly rather than silently: the user clicked a real control.
    window.alert(
      "Could not load the setup guide. Check your connection and try again."
    );
    console.error(err);
  }
}

export function closeWebSearchGuide() {
  const modal = document.getElementById(MODAL_ID);
  if (modal) modal.setAttribute("aria-hidden", "true");
}

/** Wire Escape once, globally. */
function _initEscape() {
  if (window.__gfsWsgEscBound) return;
  window.__gfsWsgEscBound = true;
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const modal = document.getElementById(MODAL_ID);
    if (modal && modal.getAttribute("aria-hidden") === "false") {
      closeWebSearchGuide();
    }
  });
}
_initEscape();

// Expose for inline onclick handlers used by both pages.
window.openWebSearchGuide = openWebSearchGuide;
window.closeWebSearchGuide = closeWebSearchGuide;
