/**
 * agentcop — OWASP LLM Top 10 runtime monitor
 *
 * Zero config. Automatic from install.
 * Registers five event hooks covering the full LLM attack surface:
 *   LLM01 — prompt injection (messages received)
 *   LLM02 — insecure output (messages sent)
 *   LLM05 — supply chain / SSRF (HTTP requests)
 *   LLM06 — credential exfiltration (tool results)
 *   LLM08 — excessive agency (tool calls)
 *
 * External calls: agentcop.live/badge — on explicit /security badge command only.
 * No auto-install. No fingerprinting. No crypto. No payment/chain references.
 */

import {
  detectInjection,
  detectInsecureOutput,
  detectExcessiveAgency,
  detectCredentialExfiltration,
  detectSupplyChain,
} from './lib/detectors.js';
import { formatAlert, formatBadgeStatus } from './lib/alerts.js';
import { fetchBadgeData } from './lib/badge.js';

/**
 * Plugin entry point. Called by OpenClaw with the plugin context.
 *
 * @param {object} ctx - OpenClaw plugin context
 * @param {object} ctx.config - resolved plugin config (may be undefined)
 * @param {object} ctx.hooks - hook registration API
 * @param {object} ctx.commands - command registration API
 * @param {object} ctx.log - logger
 */
export default function agentcopPlugin(ctx) {
  const config = ctx.config ?? {};
  const silent = config.silentMode === true;

  /**
   * Central violation handler. Always logs. Sends alert unless silentMode.
   */
  function onViolation(violation, channel) {
    ctx.log.warn('[agentcop]', JSON.stringify(violation));
    if (!silent && channel) {
      channel.send(formatAlert(violation));
    }
  }

  // LLM01 — Prompt injection: every message received
  ctx.hooks.onMessageReceived((message, channel) => {
    const v = detectInjection(message.content, 'message_received');
    if (v) onViolation(v, channel);
    return message;
  });

  // LLM02 — Insecure output: every message sent
  ctx.hooks.onMessageSend((message, channel) => {
    const v = detectInsecureOutput(message.content, 'message_send');
    if (v) onViolation(v, channel);
    return message;
  });

  // LLM08 — Excessive agency: every tool call + arguments
  ctx.hooks.onToolCall((toolCall, channel) => {
    const v = detectExcessiveAgency(toolCall, 'tool_call');
    if (v) onViolation(v, channel);
    return toolCall;
  });

  // LLM06 — Credential exfiltration: every tool result
  ctx.hooks.onToolResult((result, channel) => {
    const v = detectCredentialExfiltration(result, 'tool_result');
    if (v) onViolation(v, channel);
    return result;
  });

  // LLM05 — Supply chain: every external HTTP request
  ctx.hooks.onHttpRequest((request, channel) => {
    const v = detectSupplyChain(request, 'http_request');
    if (v) onViolation(v, channel);
    return request;
  });

  // /security badge — explicit command, the only trigger for external network I/O
  ctx.commands.register('security badge', async (_args, channel) => {
    const { json, error } = await fetchBadgeData();
    channel.send(formatBadgeStatus(json, error));
  });
}
