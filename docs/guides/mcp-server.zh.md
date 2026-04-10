# agentcop MCP 服务器

无需离开 Claude 或 Cursor，即可扫描 Agent、验证信任链、检查 CVE、监控可靠性。
agentcop MCP 服务器通过模型上下文协议（stdio 传输）对外暴露 6 个安全工具，
使其成为任何兼容 MCP 的 AI 助手中的一等工具。

---

## 什么是 agentcop MCP 服务器？

模型上下文协议（MCP）允许 AI 助手以结构化的输入和输出调用外部工具。
`agentcop-mcp` 是一个包装了 agentcop 安全库的 MCP 服务器——因此 Claude 或 Cursor
可以直接用自然语言扫描你的 Agent 代码中的漏洞、在信任第三方 Agent 之前检查其徽章、
拉取 CVE 报告，或验证密码学信任链。

无需 HTTP 服务器，无需 API 密钥，无需基础设施。以本地子进程的形式通过 stdio 运行。

---

## 安装

```bash
pip install agentcop[mcp]
```

这将安装 `agentcop-mcp` 入口点和 `mcp>=1.0` 依赖。

---

## Claude Desktop 配置

在 `~/.claude/claude_desktop_config.json` 中添加以下内容（如文件不存在则创建）：

```json
{
  "mcpServers": {
    "agentcop": {
      "command": "agentcop-mcp"
    }
  }
}
```

重启 Claude Desktop。你将在工具面板中看到 **agentcop** 部分。

### 使用虚拟环境

如果 `agentcop-mcp` 安装在虚拟环境而非全局环境中：

```json
{
  "mcpServers": {
    "agentcop": {
      "command": "/path/to/your/.venv/bin/agentcop-mcp"
    }
  }
}
```

---

## Cursor 配置

在项目根目录的 `.cursor/mcp.json` 中添加以下内容（或全局的 `~/.cursor/mcp.json`）：

```json
{
  "mcpServers": {
    "agentcop": {
      "command": "agentcop-mcp"
    }
  }
}
```

重启 Cursor。agentcop 工具将在 Composer 和 Chat 中可用。

---

## Docker 用法

构建一个最小镜像并在容器内运行服务器。使用 `--init` 以确保 stdio 进程能正确接收信号。

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir agentcop[mcp]
ENTRYPOINT ["agentcop-mcp"]
```

```bash
docker build -t agentcop-mcp .
```

Claude Desktop / Cursor 配置：

```json
{
  "mcpServers": {
    "agentcop": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "--init", "agentcop-mcp"]
    }
  }
}
```

---

## 可用工具

### `scan_agent` — 完整的 OWASP LLM Top 10 漏洞扫描

对 Agent 源代码进行六个 OWASP LLM Top 10 类别的安全扫描。

**询问 Claude：**

> "扫描这个 Agent 的漏洞"

> "检查我的 LangGraph Agent 是否存在提示注入问题"

> "这个 CrewAI 团队有哪些安全问题？"

**检测内容：**

| 类别 | OWASP | 严重程度 |
|---|---|---|
| 通过 f-string、`.format()` 或直接变量替换的提示注入 | LLM01 | CRITICAL |
| 系统提示变更（`system_prompt +=`） | LLM01 | CRITICAL |
| 越狱 / 角色覆盖短语 | LLM01 | CRITICAL |
| 硬编码的 API 密钥、密码、供应商密钥 | LLM06 | CRITICAL |
| 对 LLM 输出使用 `eval()` / `exec()` | LLM02 | ERROR |
| 无沙盒的 `subprocess.run()` / `os.system()` | LLM02 | ERROR |
| 未经验证的工具结果传入执行 | LLM07 | WARN |
| 未经清理的原始用户输入用于字符串操作 | LLM07 | WARN |
| `allow_dangerous_requests=True` / 无限迭代 | LLM08 | WARN |

**返回：** 0–100 分、等级（SECURED / MONITORED / AT_RISK）、每条发现的行号、
发现的 OWASP 类别，以及每条违规的可操作修复建议。

---

### `quick_check` — 即时 5 模式检查（毫秒级延迟）

对代码片段进行 5 个最高信号模式的检查——无 API 调用，无 I/O。

**询问 Claude：**

> "快速检查这个函数是否有安全问题"

> "这个代码片段可以安全部署吗？"

**检测内容：**

| 模式 | 严重程度 |
|---|---|
| `ignore previous instructions` 等提示注入短语 | CRITICAL |
| 硬编码凭据（`api_key = "..."`, `password = "..."`） | CRITICAL |
| `eval()` / `exec()` 使用 | ERROR |
| 执行中使用了未经验证的工具结果 | WARN |
| 未经清理的用户输入用于字符串操作 | WARN |

**返回：** `clean: true/false`、带严重程度的问题列表，以及 `scan_time_ms`。

---

### `check_badge` — 验证 Agent 的安全徽章

在信任多 Agent 流水线中的某个 Agent 之前，检查其是否持有有效的、未过期的
agentcop 安全徽章。

**询问 Claude：**

> "检查 Agent my-orchestrator 的徽章"

> "这个 Agent 的徽章还有效吗？https://agentcop.live/badge/abc123"

> "在委托给这个 Agent 之前，验证它的信任徽章"

**返回字段：**

| 字段 | 描述 |
|---|---|
| `valid` | 徽章有效且未被撤销 |
| `tier` | SECURED / MONITORED / AT_RISK |
| `score` | 信任分数 0–100 |
| `issued_at` | ISO 时间戳 |
| `expires_at` | ISO 时间戳（徽章 30 天后过期） |
| `runtime_protected` | Agent 已启用运行时执行保护 |
| `chain_verified` | SECURED 等级且未被撤销 |

本地徽章查找需要 `pip install agentcop[badge]`。未安装时会优雅降级（返回说明性提示）。

---

### `get_cve_report` — AI Agent 框架的 CVE 报告

返回影响 LangChain、CrewAI、AutoGen 和 OpenClaw 的精选 CVE。

**询问 Claude：**

> "有哪些 CVE 影响 LangChain？"

> "CrewAI 有哪些已知漏洞我需要了解？"

> "给我所有 Agent 框架的完整 CVE 报告"

**覆盖框架：**

| 框架 | 包含的 CVE |
|---|---|
| `langchain` | CVE-2023-46229（PALChain RCE，CVSS 9.8）、CVE-2023-36189（SQL 注入，CVSS 8.8）、CVE-2024-3095（SSRF，CVSS 7.5） |
| `crewai` | CVE-2024-27259（任务描述中的提示注入，CVSS 8.1） |
| `autogen` | CVE-2024-45014（通过 CodeExecutor 的任意代码执行，CVSS 9.1） |
| `openclaw` | CVE-2024-39908（通过注入指令的工具滥用，CVSS 7.8） |

可按 `framework` 和 `days`（1–30）过滤。使用 `framework: "all"` 获取全部。

---

### `reliability_report` — 行为一致性指标

从本地 `ReliabilityStore` 获取 Agent 的行为可靠性报告，评估 Agent 在多次运行中
是否表现一致。

**询问 Claude：**

> "过去 24 小时内，我的数据流水线 Agent 可靠性如何？"

> "agent-orchestrator 是否出现了漂移或重试爆炸？"

> "给我 Fleet 中所有 Agent 的可靠性概览"

**返回指标：**

| 指标 | 描述 |
|---|---|
| `reliability_score` | 加权综合分 0–100 |
| `tier` | STABLE（≥80）/ VARIABLE（60–79）/ UNSTABLE（40–59）/ CRITICAL（<40） |
| `path_entropy` | 执行路径的 Shannon 熵 |
| `tool_variance` | 多次运行中工具使用的变异系数 |
| `retry_explosion_score` | 归一化重试爆发分数 |
| `branch_instability` | 相同输入下执行路径的 Hamming 距离 |
| `tokens_per_run_avg` | 每次运行的平均 token 消耗 |
| `trend` | IMPROVING / STABLE / DEGRADING |
| `top_issues` | 检测到的最重要问题 |
| `runs_analyzed` | 时间窗口内分析的运行次数 |

需要 `agentcop.reliability`（包含在基础安装中）。当请求的 Agent 无数据时优雅降级。

---

### `trust_chain_status` — 密码学链验证

验证已注册的 `TrustChainBuilder`，确认多 Agent 流水线中没有节点被篡改。

**询问 Claude：**

> "检查我的信任链是否已验证"

> "流水线中有 Agent 被篡改了吗？"

> "显示 chain-id abc-123 的信任链状态"

**返回字段：**

| 字段 | 描述 |
|---|---|
| `verified` | 所有哈希值验证通过 |
| `broken_at` | 第一条断开链接所在的节点 ID（未断开则为 `null`） |
| `claims_count` | 链中已签名声明的数量 |
| `nodes` | 节点 ID 的有序列表 |
| `hierarchy_violations` | 检测到的委托违规 |
| `unsigned_handoffs` | 无 Ed25519 签名的交接数量 |
| `exported_compact` | 人类可读的链摘要，如 `A→B [hash:a1b2] [verified:true]` |

**从 Agent 代码中注册链：**

```python
from agentcop.trust import TrustChainBuilder, ExecutionNode
from agentcop.mcp_server import register_chain

with TrustChainBuilder(agent_id="orchestrator") as chain:
    chain.add_node(ExecutionNode(
        node_id="step-1",
        agent_id="orchestrator",
        tool_calls=["web_search"],
        context_hash="abc123",
        output_hash="def456",
        duration_ms=320,
    ))

register_chain("my-pipeline-run-001", chain)
# 现在 Claude 可以查询：trust_chain_status(chain_id="my-pipeline-run-001")
```

---

## 自然语言使用示例

配置完成后，你可以用中文或英文向 Claude 或 Cursor 提问：

```
"扫描这个 Agent 的漏洞"——内联粘贴代码或引用文件

"部署前快速检查这个函数"

"在委托给 my-orchestrator 之前检查它的徽章"

"LangChain 现在有哪些 CVE？"

"过去一周，我的数据流水线 Agent 可靠性如何？"

"流水线运行 abc-123 的信任链是否已验证？"

"找出这个代码库中所有提示注入问题并修复它们"

"我的 Fleet 中哪些 Agent 持有 AT_RISK 徽章？"
```

---

## 故障排查

**找不到 `agentcop-mcp`**

```bash
which agentcop-mcp         # 检查 PATH
pip show agentcop          # 验证安装
pip install agentcop[mcp]  # 重新安装并包含 mcp extra
```

**徽章查找返回"agentcop[badge] not installed"**

```bash
pip install agentcop[badge]
```

**可靠性报告返回零数据**

`ReliabilityStore` 只有在你使用 `ReliabilityTracer` 或 `wrap_for_reliability`
对 Agent 进行埋点后才会有数据。参见[可靠性指南](reliability.md)。

**找不到信任链**

`TrustChainBuilder` 使用内存存储——必须在同一进程中通过 `register_chain()` 注册后才能查询。
MCP 服务器本身在重启后是无状态的。

**30 秒后超时**

每个工具调用有 30 秒超时。对于非常大的代码文件，请在扫描前只截取相关部分。
