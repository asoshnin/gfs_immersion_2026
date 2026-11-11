/**
 * Provider-adaptive generation configuration.
 *
 * Centralises per-model capability detection so consumers never hard-code
 * ``max_tokens`` or ``max_completion_tokens``. Used by:
 *   - site/index.html (portal chat)
 *   - site/methodology/vc/ai-coach.html (AI Venture Coach)
 */

export const DEFAULT_CAPABILITY = {
  token_key: 'max_tokens',
  supports_json_object: true,
  supports_temperature: true,
  supports_reasoning_effort: false,
  is_reasoning: false,
  notes: ''
};

export const MODEL_FAMILY_RULES = [
  {
    pattern: /^(o1|o3)\b/i,
    token_key: 'max_completion_tokens',
    supports_json_object: false,
    supports_temperature: false,
    supports_reasoning_effort: true,
    is_reasoning: true,
    notes: 'OpenAI reasoning series uses max_completion_tokens; no JSON mode or temperature.'
  },
  {
    pattern: /azure|terra/i,
    token_key: 'max_completion_tokens',
    supports_json_object: false,
    supports_temperature: false,
    supports_reasoning_effort: true,
    is_reasoning: true,
    notes: 'Azure / Terra reasoning endpoints require max_completion_tokens and do not support JSON mode.'
  },
  {
    pattern: /deepseek/i,
    token_key: 'max_tokens',
    supports_json_object: true,
    supports_temperature: true,
    supports_reasoning_effort: false,
    is_reasoning: false,
    notes: 'DeepSeek supports max_tokens, JSON mode and temperature.'
  },
  {
    pattern: /claude/i,
    token_key: 'max_tokens',
    supports_json_object: false,
    supports_temperature: true,
    supports_reasoning_effort: false,
    is_reasoning: false,
    notes: 'Anthropic Claude supports max_tokens and temperature but not JSON mode.'
  },
  {
    pattern: /gemini/i,
    token_key: 'max_tokens',
    supports_json_object: true,
    supports_temperature: true,
    supports_reasoning_effort: false,
    is_reasoning: false,
    notes: 'Gemini supports max_tokens, JSON mode and temperature.'
  },
  {
    pattern: /gpt-4o|gpt-4-turbo/i,
    token_key: 'max_tokens',
    supports_json_object: true,
    supports_temperature: true,
    supports_reasoning_effort: false,
    is_reasoning: false,
    notes: 'GPT-4o / Turbo supports max_tokens, JSON mode and temperature.'
  }
];

export function providerEndpoint(base, overrides) {
  const b = String(base || '').trim();
  let u;
  try {
    u = new URL(b);
  } catch (e) {
    throw new Error('provider base URL must be absolute');
  }
  const loopback = (
    u.hostname === 'localhost' ||
    u.hostname === '127.0.0.1' ||
    u.hostname === '[::1]'
  );
  // V-2b: scheme must be https unless loopback. Overrides cannot relax this.
  if (u.protocol !== 'https:' && !(u.protocol === 'http:' && loopback)) {
    throw new Error('provider base URL must be https (http allowed for loopback only)');
  }
  // Sprint 22: the origin guard used to live only in ai-coach.html's private copy,
  // so a base URL pointing at our own deployment would have sent the visitor's key
  // to OUR origin. The shared module enforces it for every page now.
  const originHost = (typeof location !== 'undefined' && location.hostname) || '';
  if (originHost && u.hostname.toLowerCase() === originHost.toLowerCase()) {
    throw new Error('provider base URL must not be the current origin');
  }
  if (overrides && overrides.hostAllowlist && overrides.hostAllowlist.length) {
    const allowed = overrides.hostAllowlist.some(h => u.hostname.toLowerCase() === h.toLowerCase());
    if (!allowed) {
      throw new Error('provider host is not in the override allowlist');
    }
  }
  const n = b.replace(/\/+$/, '');
  return /\/chat\/completions$/.test(n) ? n : n + '/chat/completions';
}

export function getModelCapability(model, baseUrl) {
  const m = String(model || '').toLowerCase();
  const base = String(baseUrl || '').toLowerCase();
  const combined = m + ' ' + base;
  for (const rule of MODEL_FAMILY_RULES) {
    if (rule.pattern.test(combined)) {
      return {
        token_key: rule.token_key,
        supports_json_object: rule.supports_json_object,
        supports_temperature: rule.supports_temperature,
        supports_reasoning_effort: rule.supports_reasoning_effort,
        is_reasoning: rule.is_reasoning,
        notes: rule.notes || ''
      };
    }
  }
  return { ...DEFAULT_CAPABILITY };
}

export function buildGenerationParams({
  model,
  baseUrl,
  temperature,
  maxTokens,
  responseFormat,
  overrides
}) {
  const cap = getModelCapability(model, baseUrl);
  const params = {
    model: model
  };

  if (cap.token_key === 'max_completion_tokens') {
    params.max_completion_tokens = maxTokens;
  } else {
    params.max_tokens = maxTokens;
  }

  if (cap.supports_temperature && typeof temperature === 'number') {
    params.temperature = temperature;
  }

  if (cap.supports_json_object && responseFormat) {
    params.response_format = responseFormat;
  }

  if (overrides && typeof overrides === 'object') {
    for (const [key, value] of Object.entries(overrides)) {
      if (value !== undefined) {
        params[key] = value;
      }
    }
  }

  return params;
}

export function applyErrorRecovery(errorText, currentParams, status) {
  const text = String(errorText || '').toLowerCase();
  const out = { ...currentParams };
  let changed = false;
  let message = '';
  let retryable = true;

  // HTTP-level failures: surface a concrete hint, do not auto-retry blindly.
  if (status === 401 || status === 403) {
    return {
      changed: false,
      params: out,
      message: 'Provider rejected the API key. Check it in Settings.',
      retryable: false
    };
  }
  if (status === 429) {
    return {
      changed: false,
      params: out,
      message: 'Rate limited by the provider. Wait a moment and retry.',
      retryable: false
    };
  }
  if (status === 404) {
    return {
      changed: false,
      params: out,
      message: 'The provider does not recognise this model ID.',
      retryable: false
    };
  }

  // Parameter-level failures: repair once and retry.
  const hasMaxTokens = text.includes('max_tokens');
  const hasMaxCompletion = text.includes('max_completion_tokens');

  // Reasoning models reject max_tokens and ask for max_completion_tokens.
  if (hasMaxTokens && 'max_tokens' in out && (!hasMaxCompletion || text.indexOf('max_completion_tokens') > text.indexOf('max_tokens'))) {
    out.max_completion_tokens = out.max_tokens;
    delete out.max_tokens;
    changed = true;
    message = 'Switched to max_completion_tokens for this reasoning model.';
  } else if (hasMaxCompletion && 'max_completion_tokens' in out && (!hasMaxTokens || text.indexOf('max_tokens') > text.indexOf('max_completion_tokens'))) {
    out.max_tokens = out.max_completion_tokens;
    delete out.max_completion_tokens;
    changed = true;
    message = 'Switched to max_tokens for this model family.';
  }

  if (
    (text.includes('response_format') || text.includes('json_object')) &&
    out.response_format
  ) {
    delete out.response_format;
    changed = true;
    message = changed
      ? message + ' JSON mode unsupported; removed response_format.'
      : 'This model does not support JSON mode. Results may be less structured; coach validation will still catch malformed output.';
  }

  if (text.includes('temperature') && 'temperature' in out) {
    delete out.temperature;
    changed = true;
    message = changed
      ? message + ' Removed unsupported temperature.'
      : 'Temperature unsupported for this model; removed.';
  }

  return { changed, params: out, message, retryable };
}

export async function callProviderWithRecovery(cfg, body) {
  const endpoint = providerEndpoint(cfg.base, cfg.override);
  const makeRequest = (payload) => ({
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + cfg.key,
      'HTTP-Referer': (typeof location !== 'undefined' ? location.origin : ''),
      'X-Title': 'GFS Immersion Assistant'
    },
    body: JSON.stringify(payload),
    signal: cfg.signal
  });

  let res = await fetch(endpoint, makeRequest(body));
  if (!res.ok) {
    const errorText = await res.text();
    const recovered = applyErrorRecovery(errorText, body, res.status);
    if (recovered.changed && recovered.retryable) {
      res = await fetch(endpoint, makeRequest(recovered.params));
      if (!res.ok) {
        res._recoveryMessage = recovered.message;
      }
    } else {
      res._recoveryMessage = recovered.message;
    }
  }
  return res;
}
