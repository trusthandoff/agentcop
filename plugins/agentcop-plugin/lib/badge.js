/**
 * agentcop badge lookup.
 *
 * The ONLY external network call in this plugin.
 * Uses node:https (stdlib) — no third-party HTTP client.
 * Called only on explicit /security badge command, never automatically.
 *
 * External call declared in package.json openclaw.externalCalls.
 */

import { get } from 'node:https';

const BADGE_API_HOST = 'agentcop.live';
const BADGE_API_PATH = '/badge';

/**
 * Fetch badge JSON from agentcop.live/badge.
 * Resolves to { json, error } — never rejects.
 *
 * @returns {Promise<{ json: object|null, error: string|null }>}
 */
export function fetchBadgeData() {
  return new Promise((resolve) => {
    const options = {
      hostname: BADGE_API_HOST,
      path: BADGE_API_PATH,
      method: 'GET',
      headers: { Accept: 'application/json' },
      timeout: 5000,
    };

    const req = get(options, (res) => {
      let raw = '';
      res.on('data', (chunk) => { raw += chunk; });
      res.on('end', () => {
        if (res.statusCode !== 200) {
          resolve({ json: null, error: `HTTP ${res.statusCode}` });
          return;
        }
        try {
          resolve({ json: JSON.parse(raw), error: null });
        } catch {
          resolve({ json: null, error: 'Invalid JSON response' });
        }
      });
    });

    req.on('timeout', () => {
      req.destroy();
      resolve({ json: null, error: 'Request timed out' });
    });

    req.on('error', (err) => {
      resolve({ json: null, error: err.message });
    });
  });
}
