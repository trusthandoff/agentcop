/**
 * agentcop alert formatting.
 * No I/O. Returns formatted strings only.
 */

const BADGE_URL = 'https://agentcop.live/badge';

const SEVERITY_ICON = {
  CRITICAL: '\u{1F6A8}', // 🚨
  ERROR:    '\u26A0\uFE0F', // ⚠️
  WARN:     '\u26A1',    // ⚡
};

/**
 * Format a violation object into a human-readable alert string.
 *
 * @param {object} violation - from any detector function
 * @returns {string}
 */
export function formatAlert(violation) {
  const icon = SEVERITY_ICON[violation.severity] ?? SEVERITY_ICON.WARN;
  const lines = [
    `${icon} VIOLATION DETECTED \u2014 ${violation.type} ${violation.severity}`,
    violation.explanation,
    `${violation.owasp} \u00B7 ${violation.cwe} \u00B7 confidence ${violation.confidence}`,
    `\u2192 ${BADGE_URL}`,
  ];
  return lines.join('\n');
}

/**
 * Format a badge API response for the /security badge command.
 *
 * @param {object|null} json - parsed badge API response, or null on error
 * @param {string|null} errorMsg - error message if fetch failed
 * @returns {string}
 */
export function formatBadgeStatus(json, errorMsg) {
  if (errorMsg) {
    return `agentcop badge (unavailable): ${BADGE_URL}\n${errorMsg}`;
  }
  const status = (json && json.status) ? String(json.status) : 'unknown';
  const score  = (json && json.score  != null) ? ` \u00B7 score ${json.score}` : '';
  const ts     = (json && json.updatedAt) ? ` \u00B7 updated ${json.updatedAt}` : '';
  return `agentcop security badge: ${status}${score}${ts}\n${BADGE_URL}`;
}
