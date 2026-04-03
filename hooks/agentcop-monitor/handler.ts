/**
 * AgentCop Monitor — OpenClaw internal hook
 *
 * Events handled:
 *   message:received     — LLM01 taint check (prompt injection) — awaited before agent acts
 *   message:sent         — LLM02 output check (insecure output in agent reply)
 *   tool_result_persist  — LLM02 output check on raw tool results before transcript write
 *
 * Violations are pushed onto event.messages so the user sees them in their
 * active channel. All checks are awaited inline so messages are guaranteed
 * to be delivered before the handler returns.
 */

import { execFile } from "node:child_process";
import * as os from "node:os";
import * as path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const SKILL_PY = path.join(os.homedir(), ".openclaw", "skills", "agentcop", "skill.py");
const TIMEOUT_MS = 3_000;

// Resolve python binary: prefer python3, fall back to python (Windows)
const PYTHON_BIN = await (async () => {
  for (const bin of ["python3", "python"]) {
    try {
      await execFileAsync(bin, ["--version"], { timeout: 1_000 });
      return bin;
    } catch {
      // not found, try next
    }
  }
  return null;
})();

// ---------------------------------------------------------------------------
// Types (subset of OpenClaw event shape)
// ---------------------------------------------------------------------------

interface OpenClawEvent {
  type: string;
  action?: string;
  messages: string[];
  context: {
    content?: string;
    bodyForAgent?: string;
    channelId?: string;
    toolResult?: unknown;
    sessionKey?: string;
    [key: string]: unknown;
  };
}

interface CheckResult {
  tainted?: boolean;
  unsafe?: boolean;
  violations?: Array<{
    violation_type: string;
    severity: string;
    detail: Record<string, unknown>;
  }>;
}

// ---------------------------------------------------------------------------
// Helper: run skill.py subcommand via stdin to avoid ARG_MAX limits
// ---------------------------------------------------------------------------

async function runSkill(subcmd: string, text: string): Promise<CheckResult | null> {
  if (!PYTHON_BIN) return null;
  try {
    // Pass text via stdin to avoid OS ARG_MAX limits on long messages
    const child = execFile(
      PYTHON_BIN,
      [SKILL_PY, subcmd, "--stdin"],
      { timeout: TIMEOUT_MS, env: { ...process.env } },
    );
    const { stdout } = await new Promise<{ stdout: string; stderr: string }>(
      (resolve, reject) => {
        let stdout = "";
        let stderr = "";
        child.stdout?.on("data", (d: Buffer) => { stdout += d.toString(); });
        child.stderr?.on("data", (d: Buffer) => { stderr += d.toString(); });
        child.stdin?.end(text);
        child.on("close", (code) => {
          if (code === 0 || code === null) resolve({ stdout, stderr });
          else reject(new Error(`exit ${code}: ${stderr.slice(0, 200)}`));
        });
        child.on("error", reject);
      },
    );
    return JSON.parse(stdout.trim()) as CheckResult;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Helper: format a violation alert for the user's channel
// (CommonMark: ** = bold, ` = code — OpenClaw IR handles per-channel rendering)
// ---------------------------------------------------------------------------

function formatAlert(
  v: NonNullable<CheckResult["violations"]>[0],
  context: string,
): string {
  const owasp = (v.detail?.owasp as string) ?? "LLM??";
  const patterns = (v.detail?.matched_patterns as string[])?.slice(0, 3).join(", ") ?? "";
  return (
    `🚨 **AgentCop [${v.severity}]** — ${owasp} ${v.violation_type}\n` +
    (patterns ? `Matched: \`${patterns}\`\n` : "") +
    `Context: ${context}`
  );
}

// ---------------------------------------------------------------------------
// Main handler — all async checks awaited inline
// ---------------------------------------------------------------------------

const handler = async (event: OpenClawEvent): Promise<void> => {
  // LLM01 — taint-check inbound messages before the agent sees them
  if (event.type === "message" && event.action === "received") {
    const body = event.context.bodyForAgent ?? event.context.content ?? "";
    if (!body.trim()) return;

    const result = await runSkill("taint-check", body);
    if (!result) {
      event.messages.push(
        "⚠️ **AgentCop**: security monitor unavailable — " +
        "run `pip install agentcop` then `openclaw hooks enable agentcop-monitor`.",
      );
      return;
    }
    if (result.tainted && result.violations?.length) {
      for (const v of result.violations) {
        event.messages.push(formatAlert(v, "inbound message"));
      }
    }
    return;
  }

  // LLM02 — check outbound content for insecure output patterns
  if (event.type === "message" && event.action === "sent") {
    const content = event.context.content ?? "";
    if (!content.trim()) return;

    const result = await runSkill("output-check", content);
    if (!result || !result.unsafe || !result.violations?.length) return;
    for (const v of result.violations) {
      event.messages.push(formatAlert(v, "agent response"));
    }
    return;
  }

  // LLM02 — check raw tool results before they are written to the transcript
  if (event.type === "tool_result_persist") {
    const toolResult = event.context.toolResult;
    const content =
      typeof toolResult === "string" ? toolResult : JSON.stringify(toolResult ?? "");
    if (!content.trim()) return;

    const result = await runSkill("output-check", content);
    if (!result || !result.unsafe || !result.violations?.length) return;
    for (const v of result.violations) {
      event.messages.push(formatAlert(v, "tool result"));
    }
  }
};

export default handler;
