/**
 * Tests for lib/alerts.js
 * Run: node --test tests/alerts.test.js
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { formatAlert, formatBadgeStatus } from '../lib/alerts.js';

const BADGE_URL = 'https://agentcop.live/badge';

function makeViolation(overrides = {}) {
  return {
    type: 'LLM01_prompt_injection',
    owasp: 'LLM01',
    cwe: 'CWE-77',
    severity: 'ERROR',
    confidence: 3,
    source: 'message_received',
    matched: ['direct_injection'],
    explanation: 'Prompt injection signals detected (direct_injection)',
    ...overrides,
  };
}

describe('formatAlert', () => {
  it('includes violation type in output', () => {
    const out = formatAlert(makeViolation());
    assert.ok(out.includes('LLM01_prompt_injection'));
  });

  it('includes severity in output', () => {
    const out = formatAlert(makeViolation({ severity: 'ERROR' }));
    assert.ok(out.includes('ERROR'));
  });

  it('includes explanation in output', () => {
    const out = formatAlert(makeViolation());
    assert.ok(out.includes('Prompt injection signals detected'));
  });

  it('includes OWASP tag in output', () => {
    const out = formatAlert(makeViolation());
    assert.ok(out.includes('LLM01'));
  });

  it('includes CWE in output', () => {
    const out = formatAlert(makeViolation());
    assert.ok(out.includes('CWE-77'));
  });

  it('includes confidence score in output', () => {
    const out = formatAlert(makeViolation({ confidence: 4 }));
    assert.ok(out.includes('confidence 4'));
  });

  it('includes badge URL in output', () => {
    const out = formatAlert(makeViolation());
    assert.ok(out.includes(BADGE_URL));
  });

  it('includes VIOLATION DETECTED in output', () => {
    const out = formatAlert(makeViolation());
    assert.ok(out.includes('VIOLATION DETECTED'));
  });

  it('uses different icon for CRITICAL severity', () => {
    const critical = formatAlert(makeViolation({ severity: 'CRITICAL' }));
    const warn = formatAlert(makeViolation({ severity: 'WARN' }));
    // Different leading icon
    assert.notEqual(critical.charAt(0), warn.charAt(0));
  });

  it('handles CRITICAL severity', () => {
    const out = formatAlert(makeViolation({ severity: 'CRITICAL' }));
    assert.ok(out.includes('CRITICAL'));
  });

  it('handles WARN severity', () => {
    const out = formatAlert(makeViolation({ severity: 'WARN' }));
    assert.ok(out.includes('WARN'));
  });

  it('handles LLM02 violation', () => {
    const out = formatAlert(makeViolation({
      type: 'LLM02_insecure_output',
      owasp: 'LLM02',
      cwe: 'CWE-116',
    }));
    assert.ok(out.includes('LLM02_insecure_output'));
    assert.ok(out.includes('CWE-116'));
  });

  it('handles LLM05 violation', () => {
    const out = formatAlert(makeViolation({
      type: 'LLM05_supply_chain',
      owasp: 'LLM05',
      cwe: 'CWE-494',
    }));
    assert.ok(out.includes('LLM05_supply_chain'));
  });

  it('handles LLM06 violation', () => {
    const out = formatAlert(makeViolation({
      type: 'LLM06_credential_exfiltration',
      owasp: 'LLM06',
      cwe: 'CWE-522',
    }));
    assert.ok(out.includes('LLM06_credential_exfiltration'));
  });

  it('handles LLM08 violation', () => {
    const out = formatAlert(makeViolation({
      type: 'LLM08_excessive_agency',
      owasp: 'LLM08',
      cwe: 'CWE-250',
    }));
    assert.ok(out.includes('LLM08_excessive_agency'));
  });

  it('is a non-empty string', () => {
    const out = formatAlert(makeViolation());
    assert.ok(typeof out === 'string');
    assert.ok(out.length > 0);
  });
});

describe('formatBadgeStatus', () => {
  it('includes badge URL', () => {
    const out = formatBadgeStatus({ status: 'clean', score: 98 }, null);
    assert.ok(out.includes(BADGE_URL));
  });

  it('includes status from json', () => {
    const out = formatBadgeStatus({ status: 'clean' }, null);
    assert.ok(out.includes('clean'));
  });

  it('includes score when present', () => {
    const out = formatBadgeStatus({ status: 'clean', score: 98 }, null);
    assert.ok(out.includes('98'));
  });

  it('includes updatedAt when present', () => {
    const out = formatBadgeStatus({ status: 'clean', updatedAt: '2026-04-04' }, null);
    assert.ok(out.includes('2026-04-04'));
  });

  it('handles missing score gracefully', () => {
    const out = formatBadgeStatus({ status: 'clean' }, null);
    assert.ok(out.includes('clean'));
    assert.ok(!out.includes('score'));
  });

  it('handles error state', () => {
    const out = formatBadgeStatus(null, 'Request timed out');
    assert.ok(out.includes('Request timed out'));
    assert.ok(out.includes(BADGE_URL));
  });

  it('handles null json with no error', () => {
    const out = formatBadgeStatus(null, null);
    assert.ok(out.includes('unknown'));
    assert.ok(out.includes(BADGE_URL));
  });

  it('handles HTTP error string', () => {
    const out = formatBadgeStatus(null, 'HTTP 503');
    assert.ok(out.includes('HTTP 503'));
  });
});
