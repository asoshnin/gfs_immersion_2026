/**
 * Shared Provider & API Key settings modal.
 *
 * Sprint 22: there is exactly ONE credentials UI in the whole portal. The markup
 * used to live inline in index.html and ai-coach.html carried a second, divergent
 * copy. Both pages now import this module; index.html's inline markup was removed
 * and ai-coach.html's #phase-gate was deleted.
 *
 * Element ids are deliberately unchanged (settings-modal, provider-url,
 * provider-model, llm-key, cse-key, settings-status) so existing tests and
 * page-level handlers keep working.
 *
 * Storage keys are the portal's shared namespace: gfs_llm_key, gfs_llm_base,
 * gfs_llm_model, gfs_llm_origin, gfs_llm_override, and the WebSearch module's
 * gfs_keenable_key. No credential ever leaves the browser except to the provider.
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
<!-- Field 2b: Auth header (Azure/APIM differ from OpenAI) -->
<div class="space-y-1">
<label class="block font-medium text-text-primary" for="provider-auth-scheme">Auth header</label>
<select class="w-full h-9 px-3 rounded-md border border-border bg-canvas text-text-primary text-[12px] focus:outline-none focus:border-accent" id="provider-auth-scheme">
<option value="auto">Auto-detect (recommended)</option>
<option value="api-key">api-key (Azure AI Foundry / Azure OpenAI)</option>
<option value="bearer">Authorization: Bearer (OpenAI, OpenRouter)</option>
<option value="ocp-apim">Ocp-Apim-Subscription-Key (APIM gateway)</option>
</select>
<p class="text-[10px] text-text-muted">
              Azure rejects <code class="font-mono">Bearer</code> and answers with a 401 that says the key is invalid even when it is not.
              Auto-detect tries each header until one is accepted.
            </p>
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
            Bring your own Keenable search key — one key, no search-engine ID. Stored in this browser
            only and sent directly to <code class="font-mono">api.keenable.ai</code>, never to this
            site's origin. Leave it empty to use the shared, rate-limited public tier.
          </p>
          <div class="space-y-1">
            <label class="block font-medium text-text-primary text-[12px]" for="cse-key">Keenable API key</label>
            <input class="w-full h-9 px-3 rounded-md border border-border bg-canvas text-text-primary font-mono text-[12px] focus:outline-none focus:border-accent" id="cse-key" type="password" autocomplete="off" placeholder="keen_..."/>
          </div>
          <div class="flex items-center gap-2 flex-wrap">
            <button class="px-3 py-1.5 rounded-md border border-border text-text-secondary hover:text-text-primary text-[12px] font-medium" onclick="forgetSearchCreds()">
              Forget search key
            </button>
            <button class="px-3 py-1.5 rounded-md border border-accent text-accent hover:bg-accent-tint text-[12px] font-medium" id="cse-test-btn" type="button" onclick="testSearchCreds()">
              Test credentials
            </button>
            <a class="text-accent text-[11px] font-bold hover:underline" href="https://app.keenable.ai/console/api-keys" rel="noopener noreferrer" target="_blank">
              Get a Keenable key →
            </a>
            <button class="text-accent text-[11px] font-bold hover:underline" onclick="window.openWebSearchGuide && window.openWebSearchGuide()" type="button">
              📖 Step-by-step setup guide
            </button>
          </div>
<div class="text-[11px] pt-1" id="cse-test-result" role="status" aria-live="polite"></div>
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
  const keyEl = el('cse-key');
  if (keyEl) keyEl.placeholder = creds ? '•••••• saved — leave blank to keep' : 'keen_...';

  // Reflect the saved auth header so an Azure user sees what worked last time.
  const schemeEl = el('provider-auth-scheme');
  if (schemeEl) {
    let saved = 'auto';
    try { saved = localStorage.getItem('gfs_llm_auth_scheme') || 'auto'; } catch (e) {}
    schemeEl.value = saved;
  }

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

  const schemeEl = el('provider-auth-scheme');
  if (schemeEl) {
    try { localStorage.setItem('gfs_llm_auth_scheme', schemeEl.value || 'auto'); } catch (e) {}
  }

  if (url) localStorage.setItem(LLM_STORAGE.base, url.replace(/\/+$/, ''));
  if (model) localStorage.setItem(LLM_STORAGE.model, model);
  if (keyInput) localStorage.setItem(LLM_STORAGE.key, keyInput);
  localStorage.setItem(LLM_STORAGE.origin, location.origin);

  const override = el('custom-provider-override');
  if (override) localStorage.setItem(LLM_STORAGE.override, override.checked ? 'true' : 'false');

  const cseKeyEl = el('cse-key');
  const cseKey = cseKeyEl ? cseKeyEl.value.trim() : '';
  if (cseKey) {
    if (window.WebSearch) window.WebSearch.saveSearchCredentials(cseKey);
    if (cseKeyEl) cseKeyEl.value = '';
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
  node.textContent = ok ? 'configured' : 'public tier';
  node.className = ok ? 'text-success' : '';
}

export function forgetSearchCreds() {
  if (window.WebSearch) window.WebSearch.clearSearchCredentials();
  const k = el('cse-key');
  if (k) { k.value = ''; k.placeholder = 'keen_...'; }
  refreshCseStatus();
  status('Search key removed from this browser.', 'success');
}

/**
 * Run one real query against Keenable with the current credentials and report
 * exactly what the provider said. Uses the saved key when the field is empty
 * (the saved key is never echoed back into the input), and falls back to the
 * keyless public tier so the button is useful before any key exists.
 */
export async function testSearchCreds() {
  const out = el('cse-test-result');
  const keyEl = el('cse-key');
  const saved = (window.WebSearch && window.WebSearch.getSearchCredentials)
    ? window.WebSearch.getSearchCredentials() : null;
  const key = (keyEl && keyEl.value.trim()) || (saved && saved.key) || '';
  const keyless = !key;

  if (out) {
    out.className = 'text-[11px] pt-1 text-text-muted';
    out.textContent = keyless ? 'Testing the public tier (no key set)…' : 'Testing…';
  }

  const res = await window.WebSearch.searchWeb(
    'agent development kit',
    keyless ? null : { key: key },
    { num: 3, keyless: keyless }
  );

  const explain = {
    bad_key: 'Keenable rejected the API key. Check it starts with “keen_” and is active in the console.',
    no_credentials: 'Add an API key, or leave the field empty to use the public tier.',
    no_credits: 'This key is out of credits. Add credits in the Keenable console — do not retry.',
    key_rejected: 'The key was rejected or revoked. Create a new one at app.keenable.ai/console/api-keys.',
    throttled_local: 'Wait a second — the Test button runs one search at a time. Try again.',
    throttled: 'Keenable rate limit reached. Wait a moment, then retry.',
    missing_app_title: 'The keyless request needs an app identifier — this is a site bug, not your key.',
    bad_request: 'Keenable rejected the request parameters.',
    unusable_page: 'Keenable could not extract content for that query.',
    network_error: 'Could not reach Keenable (network or ad-blocker).',
    provider_error: 'Keenable had a server error. Retry in a moment.'
  }[res.reason] || ('Search failed (' + res.reason + ').');

  if (!out) return;
  if (res.ok) {
    const n = res.snippets ? res.snippets.length : 0;
    out.className = 'text-[11px] pt-1 text-success';
    out.textContent =
      '✓ Working — Keenable returned ' + n + ' result(s)'
      + (keyless ? ' on the public tier. Save a key for higher limits.' : '.')
      + (res.confidence === 'off_list' ? ' (none on the trusted-domain list — shown with a caution label)' : '');
  } else {
    out.className = 'text-[11px] pt-1 text-warning';
    out.textContent = '✗ ' + explain + (res.detail ? ' Keenable said: “' + res.detail + '”' : '');
  }
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
  if (!window.testSearchCreds) window.testSearchCreds = testSearchCreds;
  if (!window.refreshCseStatus) window.refreshCseStatus = refreshCseStatus;
  if (!window.populateCseFields) window.populateCseFields = populateCseFields;
}

export default {
  ensureSettingsModal, openSettingsModal, closeSettingsModal,
  saveKeySettings, forgetKey, forgetSearchCreds, testSearchCreds,
  refreshCseStatus, populateCseFields, installGlobals
};
