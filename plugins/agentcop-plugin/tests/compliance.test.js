/**
 * ClawHub security scan compliance tests.
 *
 * Scans all plugin source files to verify the plugin will pass ClawHub's
 * automated security scan. Checks for patterns that trigger capability flags
 * or moderation:
 *   - crypto / wallet references (triggers: crypto, requires-wallet, can-make-purchases)
 *   - Auto-install (curl|sh, npm install in code, pip install)
 *   - Fingerprinting (navigator.userAgent, os.hostname, platform detection)
 *   - Undeclared external calls (any URL not agentcop.live)
 *   - Obfuscated install patterns
 *
 * Run: node --test tests/compliance.test.js
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, extname } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = join(__dirname, '..');

/**
 * Recursively collect all .js files in the plugin (excluding tests/).
 */
function collectSourceFiles(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules') continue;
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      // Skip the tests dir itself — we allow test utilities to reference patterns
      if (entry === 'tests') continue;
      collectSourceFiles(full, files);
    } else if (extname(entry) === '.js') {
      files.push(full);
    }
  }
  return files;
}

const sourceFiles = collectSourceFiles(PLUGIN_ROOT);
const allSource = sourceFiles.map(f => ({
  path: f.replace(PLUGIN_ROOT + '/', ''),
  src: readFileSync(f, 'utf8'),
}));

function checkAllFiles(pattern, label) {
  const violations = [];
  for (const { path, src } of allSource) {
    if (pattern.test(src)) {
      violations.push(path);
    }
  }
  return violations;
}

describe('ClawHub security scan compliance — source files', () => {
  // --- Crypto / wallet flags ---

  it('no crypto library imports (would trigger "crypto" capability tag)', () => {
    // Importing node:crypto for hashing/signing is fine; importing it for
    // encoding tricks or obfuscation is not. We allow node:crypto only if
    // it's used in comments or tests. The plugin source must not import it.
    const violations = checkAllFiles(/^import\s.*from\s+['"]node:crypto['"]/m, 'crypto import');
    assert.deepEqual(violations, [], `crypto import found in: ${violations.join(', ')}`);
  });

  it('no require("crypto") calls', () => {
    const violations = checkAllFiles(/require\s*\(\s*['"]crypto['"]\s*\)/, 'require crypto');
    assert.deepEqual(violations, [], `require('crypto') found in: ${violations.join(', ')}`);
  });

  it('no wallet or payment references (would trigger "requires-wallet" / "can-make-purchases")', () => {
    // Patterns: wallet, bitcoin, ethereum, metamask, web3, coinbase, stripe.com payment, paypal
    const violations = checkAllFiles(
      /\b(wallet|bitcoin|ethereum|metamask|web3\.eth|coinbase\s*pay|stripe\.com|paypal\.com)\b/i,
      'wallet/payment reference',
    );
    assert.deepEqual(violations, [], `Wallet/payment reference found in: ${violations.join(', ')}`);
  });

  it('no transaction signing calls (would trigger "can-sign-transactions")', () => {
    const violations = checkAllFiles(/signTransaction|sign_transaction|eth\.sign\b/i, 'sign-transaction');
    assert.deepEqual(violations, [], `Transaction signing found in: ${violations.join(', ')}`);
  });

  // --- Auto-install flags ---

  it('no curl|sh pattern (ClawHub explicit prohibition)', () => {
    const violations = checkAllFiles(/curl\s+.*\|\s*sh\b/, 'curl|sh');
    assert.deepEqual(violations, [], `curl|sh found in: ${violations.join(', ')}`);
  });

  it('no execSync("npm install") or child_process npm install', () => {
    const violations = checkAllFiles(/execSync\s*\(.*npm\s+install/i, 'execSync npm install');
    assert.deepEqual(violations, [], `execSync npm install found in: ${violations.join(', ')}`);
  });

  it('no execSync("pip install") or subprocess pip install', () => {
    const violations = checkAllFiles(/execSync\s*\(.*pip\s+install/i, 'execSync pip install');
    assert.deepEqual(violations, [], `execSync pip install found in: ${violations.join(', ')}`);
  });

  it('no dynamic require() of unreviewed remote packages', () => {
    // Remote npx @latest or similar patterns
    const violations = checkAllFiles(/npx\s+@[a-z]+\/[a-z]+@latest/i, 'npx @latest');
    assert.deepEqual(violations, [], `npx @latest found in: ${violations.join(', ')}`);
  });

  // --- Fingerprinting flags ---

  it('no navigator.userAgent (browser fingerprinting)', () => {
    const violations = checkAllFiles(/navigator\.userAgent/, 'navigator.userAgent');
    assert.deepEqual(violations, [], `navigator.userAgent found in: ${violations.join(', ')}`);
  });

  it('no os.hostname() (host fingerprinting)', () => {
    const violations = checkAllFiles(/os\.hostname\s*\(/, 'os.hostname()');
    assert.deepEqual(violations, [], `os.hostname() found in: ${violations.join(', ')}`);
  });

  it('no canvas fingerprinting', () => {
    const violations = checkAllFiles(/\.getContext\s*\(\s*['"]2d['"]\s*\).*toDataURL/, 'canvas fingerprint');
    assert.deepEqual(violations, [], `Canvas fingerprinting found in: ${violations.join(', ')}`);
  });

  it('no MAC address or hardware ID collection', () => {
    const violations = checkAllFiles(/getMac|macaddress|hardware_id|machine_id/i, 'hardware fingerprint');
    assert.deepEqual(violations, [], `Hardware fingerprinting found in: ${violations.join(', ')}`);
  });

  // --- Undeclared external calls ---

  it('no hardcoded external URLs other than agentcop.live', () => {
    // Look for https?:// strings that aren't agentcop.live
    const violations = [];
    for (const { path, src } of allSource) {
      const matches = src.match(/['"`]https?:\/\/([^'"`\s/]+)/g) ?? [];
      for (const match of matches) {
        if (!match.includes('agentcop.live')) {
          violations.push(`${path}: ${match}`);
        }
      }
    }
    assert.deepEqual(violations, [], `Undeclared external URL(s) found:\n${violations.join('\n')}`);
  });

  it('no fetch() calls (only node:https get is allowed)', () => {
    // Catches browser-style fetch() which could bypass declared external calls
    const violations = checkAllFiles(/\bfetch\s*\(/, 'fetch()');
    assert.deepEqual(violations, [], `fetch() found in: ${violations.join(', ')}`);
  });

  it('no XMLHttpRequest', () => {
    const violations = checkAllFiles(/XMLHttpRequest/, 'XMLHttpRequest');
    assert.deepEqual(violations, [], `XMLHttpRequest found in: ${violations.join(', ')}`);
  });

  // --- Obfuscated execution ---

  it('no eval() calls', () => {
    const violations = checkAllFiles(/\beval\s*\(/, 'eval()');
    assert.deepEqual(violations, [], `eval() found in: ${violations.join(', ')}`);
  });

  it('no Function() constructor (dynamic code execution)', () => {
    const violations = checkAllFiles(/new\s+Function\s*\(/, 'new Function()');
    assert.deepEqual(violations, [], `new Function() found in: ${violations.join(', ')}`);
  });

  it('no Buffer.from with base64 decode for execution (obfuscated payloads)', () => {
    // Detecting execution of base64-decoded code
    const violations = checkAllFiles(/Buffer\.from\s*\([^)]+,\s*['"]base64['"]\s*\)[.\s]*toString.*eval/s, 'base64 eval');
    assert.deepEqual(violations, [], `Base64->eval found in: ${violations.join(', ')}`);
  });

  // --- Manifest integrity ---

  it('openclaw.plugin.json has required id field', () => {
    const manifest = JSON.parse(readFileSync(join(PLUGIN_ROOT, 'openclaw.plugin.json'), 'utf8'));
    assert.ok(manifest.id, 'manifest must have id field');
    assert.equal(typeof manifest.id, 'string');
  });

  it('openclaw.plugin.json has required configSchema field', () => {
    const manifest = JSON.parse(readFileSync(join(PLUGIN_ROOT, 'openclaw.plugin.json'), 'utf8'));
    assert.ok(manifest.configSchema, 'manifest must have configSchema field');
    assert.equal(manifest.configSchema.type, 'object');
  });

  it('package.json declares openclaw.externalCalls for agentcop.live', () => {
    const pkg = JSON.parse(readFileSync(join(PLUGIN_ROOT, 'package.json'), 'utf8'));
    assert.ok(pkg.openclaw, 'package.json must have openclaw block');
    assert.ok(Array.isArray(pkg.openclaw.externalCalls), 'must declare externalCalls');
    const hasAgentcop = pkg.openclaw.externalCalls.some(c =>
      typeof c.url === 'string' && c.url.includes('agentcop.live')
    );
    assert.ok(hasAgentcop, 'agentcop.live external call must be declared');
  });

  it('package.json has no install scripts', () => {
    const pkg = JSON.parse(readFileSync(join(PLUGIN_ROOT, 'package.json'), 'utf8'));
    const scripts = pkg.scripts ?? {};
    for (const [name, cmd] of Object.entries(scripts)) {
      assert.ok(
        !/(npm|pip|curl)\s+install/.test(cmd),
        `script "${name}" runs install: ${cmd}`,
      );
    }
  });

  it('source files collected (sanity check)', () => {
    assert.ok(sourceFiles.length >= 3, `expected at least 3 source files, found ${sourceFiles.length}`);
  });
});
