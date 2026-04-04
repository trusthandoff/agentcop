/**
 * agentcop detectors — pure functions, no I/O, no side effects.
 *
 * Each detector returns a violation object or null.
 * Confidence scoring: 1-2 signals=WARN, 3-4=ERROR, 5+=CRITICAL.
 *
 * External call policy: NONE. All logic is local pattern matching.
 * No crypto imports. No fingerprinting. No network I/O.
 */

// --- Pattern banks ---

// 14 direct prompt injection patterns
const DIRECT_INJECTION = [
  /ignore\s+(all\s+)?previous\s+instructions?/i,
  /disregard\s+(your\s+)?instructions?/i,
  /forget\s+(your\s+)?previous/i,
  /you\s+are\s+now\s+(a|an|the)\b/i,
  /your\s+new\s+instructions?\s+are/i,
  /system\s*prompt\s*:/i,
  /override\s+your/i,
  /bypass\s+your/i,
  /\bjailbreak\b/i,
  /do\s+anything\s+now/i,
  /unrestricted\s+mode/i,
  /developer\s+mode/i,
  /admin\s+mode/i,
  /new\s+persona\s+(is|will\s+be)/i,
];

// Token smuggling — special tokens that break context boundaries
const TOKEN_SMUGGLING = [
  /<\|im_start\|>/,
  /<\|im_end\|>/,
  /\[INST\]/,
  /\[\/INST\]/,
  /<human>/i,
  /<assistant>/i,
  /###\s*Human:/i,
  /###\s*Assistant:/i,
];

// Obfuscation — encoded payloads and invisible chars
const OBFUSCATION = [
  // Long base64-like strings (30+ chars) — possible encoded instruction
  /[A-Za-z0-9+/]{30,}={0,2}/,
  // Unicode zero-width / invisible chars used to hide text
  /[\u200B\u200C\u200D\u2060\uFEFF]/,
  // ROT13 of "instruction", "system", "prompt" — common evasion
  /\b(vafgehpgvba|flfgrz|cezcg)\b/i,
];

// Role-playing jailbreaks
const ROLEPLAY_JAILBREAK = [
  /pretend\s+(you\s+are|to\s+be)\b/i,
  /act\s+as\s+if\b/i,
  /roleplay\s+as\b/i,
  /simulate\s+being\b/i,
  /imagine\s+you\s+are\b/i,
];

// Indirect injection markers — signals content came from an untrusted external source
const INDIRECT_INJECTION = [
  /as\s+I\s+mentioned\s+(earlier|before|previously)/i,
  /continuing\s+from\s+(before|earlier|our\s+previous)/i,
  /as\s+previously\s+discussed/i,
  /based\s+on\s+our\s+earlier/i,
];

// Moltbook feed injection — unique to agentcop
const MOLTBOOK_FEED = [
  /moltbook\s+feed\s+inject/i,
  /<moltbook:/i,
  /\[moltbook\]/i,
];

// Credential patterns — for LLM06 exfiltration detection
const CREDENTIAL_PATTERNS = [
  /sk-[A-Za-z0-9]{20,}/,                   // OpenAI-style secret key
  /Bearer\s+[A-Za-z0-9\-._~+/]+=*/i,       // Bearer token in text
  /["']?password["']?\s*[:=]\s*["']?\S{4,}/i,
  /["']?secret["']?\s*[:=]\s*["']?\S{4,}/i,
  /AKIA[0-9A-Z]{16}/,                       // AWS access key ID
  /\baws_access_key_id\b/i,
  /-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----/,
  /ghp_[A-Za-z0-9]{36}/,                   // GitHub personal access token
  /xox[baprs]-[A-Za-z0-9\-]{10,}/,         // Slack token
  /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/, // JWT
];

// High-privilege tool names — LLM08 excessive agency signals
const HIGH_PRIVILEGE_TOOL_FRAGMENTS = [
  'bash', 'shell', 'exec', 'run_command', 'execute',
  'file_write', 'write_file', 'delete_file', 'remove_file',
  'send_email', 'send_message', 'post_tweet', 'post_to',
  'transfer', 'payment', 'purchase', 'checkout',
  'git_push', 'deploy', 'publish',
];

// --- Scoring ---

function scoreToSeverity(score) {
  if (score >= 5) return 'CRITICAL';
  if (score >= 3) return 'ERROR';
  return 'WARN';
}

/**
 * Test text against groups of patterns. Each group contributes at most +1
 * to the score (prevents one category from dominating).
 *
 * @param {string} text
 * @param {Array<[string, RegExp[]]>} groups - [groupName, patterns[]]
 * @returns {{ score: number, matched: string[] }}
 */
function scoreGroups(text, groups) {
  let score = 0;
  const matched = [];
  for (const [name, patterns] of groups) {
    for (const pattern of patterns) {
      if (pattern.test(text)) {
        score++;
        matched.push(name);
        break;
      }
    }
  }
  return { score, matched };
}

function toText(value) {
  if (typeof value === 'string') return value;
  if (value == null) return '';
  try { return JSON.stringify(value); } catch { return String(value); }
}

// --- Public detector functions ---

/**
 * LLM01 — Prompt injection detection.
 * Covers: direct patterns, obfuscation, token smuggling, roleplay jailbreaks,
 * indirect injection markers, Moltbook feed injection.
 */
export function detectInjection(text, source) {
  const t = toText(text);
  if (!t) return null;

  const { score, matched } = scoreGroups(t, [
    ['direct_injection',    DIRECT_INJECTION],
    ['token_smuggling',     TOKEN_SMUGGLING],
    ['obfuscation',         OBFUSCATION],
    ['roleplay_jailbreak',  ROLEPLAY_JAILBREAK],
    ['indirect_injection',  INDIRECT_INJECTION],
    ['moltbook_feed',       MOLTBOOK_FEED],
  ]);

  if (score === 0) return null;

  return {
    type: 'LLM01_prompt_injection',
    owasp: 'LLM01',
    cwe: 'CWE-77',
    severity: scoreToSeverity(score),
    confidence: score,
    source,
    matched,
    explanation: `Prompt injection signals detected (${matched.join(', ')})`,
  };
}

/**
 * LLM02 — Insecure output detection.
 * Checks outgoing messages for injection patterns leaking through and
 * credentials being echoed into output.
 */
export function detectInsecureOutput(text, source) {
  const t = toText(text);
  if (!t) return null;

  const { score, matched } = scoreGroups(t, [
    ['injection_leaked_to_output', DIRECT_INJECTION],
    ['token_smuggling_in_output',  TOKEN_SMUGGLING],
    ['credential_in_output',       CREDENTIAL_PATTERNS],
  ]);

  if (score === 0) return null;

  return {
    type: 'LLM02_insecure_output',
    owasp: 'LLM02',
    cwe: 'CWE-116',
    severity: scoreToSeverity(score),
    confidence: score,
    source,
    matched,
    explanation: `Insecure output signals detected (${matched.join(', ')})`,
  };
}

/**
 * LLM08 — Excessive agency detection.
 * Fires when a high-privilege tool is called, or when tool arguments
 * contain injection or obfuscation signals.
 */
export function detectExcessiveAgency(toolCall, source) {
  if (!toolCall || !toolCall.name) return null;

  let score = 0;
  const matched = [];

  const toolName = String(toolCall.name).toLowerCase();
  if (HIGH_PRIVILEGE_TOOL_FRAGMENTS.some(f => toolName.includes(f))) {
    score++;
    matched.push('high_privilege_tool');
  }

  const argsText = toText(toolCall.args);
  const { score: argScore, matched: argMatched } = scoreGroups(argsText, [
    ['injection_in_args',    DIRECT_INJECTION],
    ['obfuscation_in_args',  OBFUSCATION],
    ['token_smuggling_args', TOKEN_SMUGGLING],
  ]);
  score += argScore;
  matched.push(...argMatched);

  if (score === 0) return null;

  return {
    type: 'LLM08_excessive_agency',
    owasp: 'LLM08',
    cwe: 'CWE-250',
    severity: scoreToSeverity(score),
    confidence: score,
    source,
    matched,
    tool: toolCall.name,
    explanation: `Excessive agency detected for tool '${toolCall.name}' (${matched.join(', ')})`,
  };
}

/**
 * LLM06 — Credential exfiltration detection.
 * Fires when tool results contain recognizable credential patterns.
 */
export function detectCredentialExfiltration(result, source) {
  const t = toText(result);
  if (!t) return null;

  const { score, matched } = scoreGroups(t, [
    ['credential_pattern', CREDENTIAL_PATTERNS],
  ]);

  if (score === 0) return null;

  return {
    type: 'LLM06_credential_exfiltration',
    owasp: 'LLM06',
    cwe: 'CWE-522',
    severity: scoreToSeverity(score),
    confidence: score,
    source,
    matched,
    explanation: `Potential credential exposure in tool result (${matched.join(', ')})`,
  };
}

/**
 * LLM05 — Supply chain / SSRF detection.
 * Fires when an outgoing HTTP request carries injection signals or credentials.
 */
export function detectSupplyChain(request, source) {
  if (!request || !request.url) return null;

  const urlText = String(request.url);
  const bodyText = toText(request.body);
  const combined = urlText + ' ' + bodyText;

  const { score, matched } = scoreGroups(combined, [
    ['credential_in_request',  CREDENTIAL_PATTERNS],
    ['injection_in_body',      DIRECT_INJECTION],
    ['obfuscation_in_request', OBFUSCATION],
  ]);

  if (score === 0) return null;

  return {
    type: 'LLM05_supply_chain',
    owasp: 'LLM05',
    cwe: 'CWE-494',
    severity: scoreToSeverity(score),
    confidence: score,
    source,
    matched,
    url: urlText,
    explanation: `Supply chain signals in HTTP request to ${urlText} (${matched.join(', ')})`,
  };
}
