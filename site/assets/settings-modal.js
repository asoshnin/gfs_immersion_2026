/**
 * Shared Provider & API Key settings modal.
 *
 * Sprint 22: there is exactly ONE credentials UI in the whole portal. The markup
 * used to live inline in index.html and ai-coach.html carried a second, divergent
 * copy. Both pages now import this module; index.html's inline markup was removed
 * and ai-coach.html's #phase-gate was deleted.
 *
 * Element ids are deliberately unchanged (settings-modal, provider-url,
 * provider-model, llm-key, cse-key, cse-cx, settings-status) so existing tests and
 * page-level handlers keep working.
 *
 * Storage keys are the portal's shared namespace: gfs_llm_key, gfs_llm_base,
 * gfs_llm_model, gfs_llm_origin, gfs_llm_override, and the WebSearch module's
 * gfs_cse_* keys. No credential ever leaves the browser except to the provider.
 */

const MODAL_ID = 'settings-modal';

const MODAL_HTML = `<div aria-hidden="true" aria-modal="true" class="hidden fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" id="settings-modal" role="dialog">
<div class="w-full max-w-[480px] rounded-lg border border-border bg-surface shadow-ceiling p-6 space-y-5">
<div class="flex items-center justify-between border-b border-border pb-3">
<h3 class="text-[18px] font-bold text-text-primary">Provider &amp; API Key</h3>
<button class="text-text-muted hover:text-text-primary" onclick="closeSettingsModal()">
<svg class="w-5 h-5" fill="none" stroke="currentColor" viewbox="0 0 24 24"><path d="M6 18L18 6M6 6l12 12" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"></path></svg>
</button>
</div>
<div class="space-y-4 text-[13px]">
<div class="p-3 rounded bg-accent-tint border border-accent-border text-text-secondary text-[12px]">
<strong>Security guarantee:</strong> the key lives in localStorage only and is sent exclusively to your provider base URL. Never transmitted to our origin.
        </div>
<!-- Field 1: Provider Base URL -->
<div class="space-y-1">
<label class="block font-medium text-text-primary" for="provider-url">Provider Base URL</label>
<input class="w-full h-9 px-3 rounded-md border border-border bg-canvas text-text-primary font-mono text-[12px] focus:outline-none focus:border-accent" id="provider-url" type="text" value="https://openrouter.ai/api/v1"/>
</div>
<!-- Field 2: Model -->
<div class="space-y-1">
<label class="block font-medium text-text-primary" for="provider-model">Model ID</label>
<input class="w-full h-9 px-3 rounded-md border border-border bg-canvas text-text-primary font-mono text-[12px] focus:outline-none focus:border-accent" id="provider-model" type="text" value="google/gemini-2.5-flash-lite:free"/>
</div>
<!-- Field 3: API Key -->
<div class="space-y-1">
<label class="block font-medium text-text-primary" for="llm-key">API Key (Masked Password Field)</label>
<input class="w-full h-9 px-3 rounded-md border border-border bg-canvas text-text-primary font-mono text-[12px] focus:outline-none focus:border-accent" id="llm-key" placeholder="sk-or-v1-..." type="password" onkeydown="if(event.key==='Enter'){event.preventDefault();saveKeySettings();}"/>
</div>
<div class="flex items-center gap-2 pt-1">
          <input id="custom-provider-override" type="checkbox" class="rounded border-border text-accent focus:ring-accent"/>
          <label class="text-[12px] text-text-secondary" for="custom-provider-override">Allow a custom provider host (only if you trust it)</label>
        </div>
        <div class="text-[11px] text-text-muted" id="settings-status">Leave a field empty to keep its current value.</div>
</div>

<!-- BYO GOOGLE CUSTOM SEARCH (live-web fallback) -->
<div class="pt-3 mt-1 border-t border-border space-y-2">
<div class="flex items-center justify-between">
<span class="font-medium text-text-primary text-[12px]">Live-web fallback (optional)</span>
<span class="text-[10px] px-1.5 py-0.5 rounded-sm bg-accent-tint text-accent font-mono" id="cse-status">not configured</span>
</div>
<p class="text-[11px] text-text-muted">
            Bring your own Google Custom Search credentials. They are stored in this browser only and sent
            directly to <code class="font-mono">googleapis.com</code> — never to this site's origin. Free tier: 100 queries/day.
          </p>
<div class="space-y-1">
<label class="block font-medium text-text-primary text-[12px]" for="cse-key">Google API Key</label>
<input class="w-full h-9 px-3 rounded-md border border-border bg-canvas text-text-primary font-mono text-[12px] focus:outline-none focus:border-accent" id="cse-key" type="password" autocomplete="off" placeholder="AIza..."/>
</div>
<div class="space-y-1">
<label class="block font-medium text-text-primary text-[12px]" for="cse-cx">Search Engine ID (cx)</label>
<input class="w-full h-9 px-3 rounded-md border border-border bg-canvas text-text-primary font-mono text-[12px] focus:outline-none focus:border-accent" id="cse-cx" type="text" autocomplete="off" placeholder="a1b2c3d4e5f6g7h8i"/>
</div>
<div class="flex items-center gap-2 flex-wrap">
<button class="px-3 py-1.5 rounded-md border border-border text-text-secondary hover:text-text-primary text-[12px] font-medium" onclick="forgetSearchCreds()">
              Forget search credentials
            </button>
<a class="text-accent text-[11px] font-bold hover:underline" href="https://programmablesearchengine.google.com/controlpanel/all" rel="noopener noreferrer" target="_blank">
              Get a search engine ID →
            </a>
<button class="text-accent text-[11px] font-bold hover:underline" onclick="window.openWebSearchGuide && window.openWebSearchGuide()" type="button">
              📖 Step-by-step setup guide
            </button>
</div>
</div>

<div class="flex items-center justify-between pt-2 border-t border-border">
<button class="px-3 py-1.5 rounded-md border border-danger/40 text-danger hover:bg-danger/10 text-[13px] font-medium transition-colors" onclick="forgetKey()">
          Forget Key
        </button>
<div class="flex items-center gap-2">
<button class="px-3 py-1.5 rounded-md border border-border text-text-secondary hover:text-text-primary text-[13px] font-medium" onclick="closeSettingsModal()">
            Cancel
          </button>
<button class="px-4 py-1.5 rounded-md bg-accent hover:bg-accent-hover text-white text-[13px] font-medium transition-colors" onclick="saveKeySettings()">
            Save Key
          </button>
</div>
</div>
</div>
</div>`;

const LLM_STORAGE = {
  key: 'gfs_llm_key',
  base: 'gfs_llm_base',
  model: 'gfs_llm_model',
  origin: 'gfs_llm_origin',
  override: 'gfs_llm_override'
};

function el(id) { return document.getElementById(id); }

export function ensureSettingsModal() {
  if (typeof document === 'undefined') return null;
  let modal = el(MODAL_ID);
  if (!modal) {
    const host = document.createElement('div');
    host.innerHTML = MODAL_HTML;
    modal = host.firstElementChild;
    if (modal) document.body.appendChild(modal);
  }
  if (!modal) return null;

  // Persist the custom-provider consent independently of each page's save handler,
  // so the single control works everywhere.
  const override = el('custom-provider-override');
  if (override && !override.dataset.wired) {
    override.dataset.wired = '1';
    override.checked = localStorage.getItem(LLM_STORAGE.override) === 'true';
    override.addEventListener('change', () => {
      localStorage.setItem(LLM_STORAGE.override, override.checked ? 'true' : 'false');
    });
  }
  refreshCseStatus();
  return modal;
}

export function openSettingsModal() {
  const modal = ensureSettingsModal();
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  const base = el('provider-url');
  if (base) base.value = localStorage.getItem(LLM_STORAGE.base) || base.value || '';
  const model = el('provider-model');
  if (model) model.value = localStorage.getItem(LLM_STORAGE.model) || model.value || '';
  populateCseFields();
  const first = modal.querySelector('input');
  if (first) first.focus();
}

/**
 * Reflect the SAVED search-credential state in the modal.
 *
 * Sprint 22 hotfix: the fields were always emptied after saving and never
 * repopulated, so a correctly stored engine id looked like it had been lost.
 * The cx (a public engine id) is shown back; the key is a secret and is NEVER
 * painted back — its presence is signalled through the placeholder instead.
 */
export function populateCseFields() {
  let creds = null;
  try {
    creds = (window.WebSearch && window.WebSearch.getSearchCredentials && window.WebSearch.getSearchCredentials()) || null;
  } catch (e) {
    creds = null;
  }
  const cxEl = el('cse-cx');
  const keyEl = el('cse-key');
  if (cxEl) cxEl.value = creds ? creds.cx : '';
  if (keyEl) keyEl.placeholder = creds ? '•••••• saved — leave blank to keep' : 'AIza...';
  refreshCseStatus();
}

export function closeSettingsModal() {
  const modal = el(MODAL_ID);
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
}

function status(msg, tone) {
  const node = el('settings-status');
  if (!node) return;
  node.textContent = msg;
  node.className = 'text-[11px] ' + (tone === 'danger' ? 'text-danger' : 'text-success');
}

export function saveKeySettings() {
  const urlEl = el('provider-url');
  const modelEl = el('provider-model');
  const llmKey = el('llm-key');
  const url = urlEl ? urlEl.value.trim() : '';
  const model = modelEl ? modelEl.value.trim() : '';
  const keyInput = llmKey ? llmKey.value.trim() : '';

  if (url && !/^https?:\/\//i.test(url)) { status('Base URL must start with http:// or https://', 'danger'); return; }
  if (model && !/^[A-Za-z0-9._\-\/:@]+$/.test(model)) { status('Model id contains unsupported characters.', 'danger'); return; }

  if (url) localStorage.setItem(LLM_STORAGE.base, url.replace(/\/+$/, ''));
  if (model) localStorage.setItem(LLM_STORAGE.model, model);
  if (keyInput) localStorage.setItem(LLM_STORAGE.key, keyInput);
  localStorage.setItem(LLM_STORAGE.origin, location.origin);

  const override = el('custom-provider-override');
  if (override) localStorage.setItem(LLM_STORAGE.override, override.checked ? 'true' : 'false');

  const cseKeyEl = el('cse-key');
  const cseCxEl = el('cse-cx');
  const cseKey = cseKeyEl ? cseKeyEl.value.trim() : '';
  const cseCx = cseCxEl ? cseCxEl.value.trim() : '';
  if (cseKey || cseCx) {
    if (window.WebSearch) window.WebSearch.saveSearchCredentials(cseKey, cseCx);
    if (cseKeyEl) cseKeyEl.value = '';
    if (cseCxEl) cseCxEl.value = '';
  }
  refreshCseStatus();
  status('Saved — your provider settings are active in this browser.', 'success');
  setTimeout(closeSettingsModal, 700);
  window.dispatchEvent(new CustomEvent('gfs-settings-saved'));
}

export function forgetKey() {
  localStorage.removeItem(LLM_STORAGE.key);
  status('Provider key removed from this browser.', 'success');
}

export function refreshCseStatus() {
  const node = el('cse-status');
  if (!node) return;
  const ok = !!(window.WebSearch && window.WebSearch.isWebSearchConfigured());
  node.textContent = ok ? 'configured' : 'not configured';
  node.className = ok ? 'text-success' : '';
}

export function forgetSearchCreds() {
  if (window.WebSearch) window.WebSearch.clearSearchCredentials();
  const k = el('cse-key'); const c = el('cse-cx');
  if (k) { k.value = ''; k.placeholder = 'AIza...'; }
  if (c) c.value = '';
  refreshCseStatus();
  status('Search credentials removed from this browser.', 'success');
}

/**
 * Bind globals only if the host page has not defined its own. index.html keeps its
 * richer, chat-aware implementations; a sub-page like ai-coach.html gets these.
 */
export function installGlobals() {
  if (typeof window === 'undefined') return;
  if (!window.ensureSettingsModal) window.ensureSettingsModal = ensureSettingsModal;
  if (!window.openSettingsModal) window.openSettingsModal = openSettingsModal;
  if (!window.closeSettingsModal) window.closeSettingsModal = closeSettingsModal;
  if (!window.saveKeySettings) window.saveKeySettings = saveKeySettings;
  if (!window.forgetKey) window.forgetKey = forgetKey;
  if (!window.forgetSearchCreds) window.forgetSearchCreds = forgetSearchCreds;
  if (!window.refreshCseStatus) window.refreshCseStatus = refreshCseStatus;
  if (!window.populateCseFields) window.populateCseFields = populateCseFields;
}

export default {
  ensureSettingsModal, openSettingsModal, closeSettingsModal,
  saveKeySettings, forgetKey, forgetSearchCreds, refreshCseStatus, populateCseFields, installGlobals
};
