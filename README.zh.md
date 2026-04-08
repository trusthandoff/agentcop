[English](README.md) | [中文](README.zh.md)

<p align="center">
  <img src="https://raw.githubusercontent.com/trusthandoff/agentcop/main/docs/logo.png" alt="agentcop" width="120" />
</p>

# agentcop — The Agent Cop

[![CI](https://github.com/trusthandoff/agentcop/actions/workflows/test.yml/badge.svg)](https://github.com/trusthandoff/agentcop/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/agentcop)](https://pypi.org/project/agentcop/)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://pypi.org/project/agentcop/)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://pypi.org/project/agentcop/)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://pypi.org/project/agentcop/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Twitter @theagentcop](https://img.shields.io/badge/Twitter-@theagentcop-1DA1F2?logo=twitter&logoColor=white)](https://twitter.com/theagentcop)
[![Moltbook](https://img.shields.io/badge/Moltbook-%F0%9F%A6%9E-8B5CF6)](https://moltbook.com)

**Agent 舰队的警察。**

每个 Agent 舰队都需要一个警察。Agent 相互委托、移交、执行——没有法证监控，违规行为在变成事故之前是不可见的。`agentcop` 是一个通用审计器：从任何 Agent 系统摄入事件，运行违规检测器，获得结构化发现。

OTel 对齐的 schema。可插拔的检测器。适配器桥接到你的技术栈。零基础设施依赖。

**功能特性：**
- 通用 `SentinelEvent` schema（OTel 对齐）+ 可插拔的 `ViolationDetector` 函数
- 十个框架适配器（LangGraph、LangSmith、Langfuse、Datadog、Haystack、Semantic Kernel、LlamaIndex、CrewAI、AutoGen、Moltbook）
- `AgentIdentity` — 可验证的指纹、行为基线、信任评分和漂移检测（KYA — Know Your Agent）
- Ed25519 签名的 `AgentBadge` 系统——分级 SECURED / MONITORED / AT RISK 证书，用于 README 展示和跨 Agent 验证
- **Moltbook 适配器** — 专为 Moltbook 社交网络上的 AI Agent 构建的监控：对每条收到的帖子进行提示注入污点分析、协调活动检测、技能徽章验证（LLM05）、API 密钥泄露检测（LLM06），以及 Agent 资料的 Ed25519 徽章集成
- OpenClaw 集成 — `/security` 技能命令 + `agentcop-monitor` 钩子，用于在 Telegram、WhatsApp、Discord 等平台实时检测 LLM01/LLM02
- **运行时安全层** — 四个可组合的执行层：`ExecutionGate`（基于策略的工具执行，附带 SQLite 审计日志）、`ToolPermissionLayer`（声明式能力范围，默认拒绝）、`AgentSandbox`（带主动系统调用拦截的运行时隔离）、`ApprovalBoundary`（高风险操作的人工审批）。`AgentCop.protect()` 一行代码串联全部四层。
- **可靠性层** — 五维可靠性评分（路径熵、工具方差、重试爆炸、分支不稳定性、Token 预算）、SQLite 支持的运行历史、通过 OLS 回归的预测告警、K-means++ 跨 Agent 聚类、Prometheus 导出，以及组合徽章格式：`✅ SECURED 94/100 | 🟢 STABLE 87/100`
- 通过 `agentcop[otel]` 可选导出 OTel

```
pip install agentcop
```

---

## 适配器

提供十个适配器——按需安装：

| 适配器 | 框架 | 安装方式 |
|---|---|---|
| [LangGraph](docs/adapters/langgraph.md) | LangGraph 图节点 & 边 | `pip install agentcop[langgraph]` |
| [LangSmith](docs/adapters/langsmith.md) | LangSmith 运行追踪 | `pip install agentcop[langsmith]` |
| [Langfuse](docs/adapters/langfuse.md) | Langfuse 4.x 观测 | `pip install agentcop[langfuse]` |
| [Datadog](docs/adapters/datadog.md) | ddtrace APM spans | `pip install agentcop[ddtrace]` |
| [Haystack](docs/adapters/haystack.md) | Haystack 管道组件 | `pip install agentcop[haystack]` |
| [Semantic Kernel](docs/adapters/semantic_kernel.md) | Semantic Kernel 过滤器 | `pip install agentcop[semantic-kernel]` |
| [LlamaIndex](docs/adapters/llamaindex.md) | LlamaIndex 管道事件 | `pip install agentcop[llamaindex]` |
| [CrewAI](docs/adapters/crewai.md) | CrewAI Agent & 任务事件 | `pip install agentcop[crewai]` |
| [AutoGen](docs/adapters/autogen.md) | AutoGen Agent 消息 | `pip install agentcop[autogen]` |
| [Moltbook](docs/adapters/moltbook.md) | Moltbook 社交网络 Agent | `pip install agentcop[moltbook]` |

### 运行时安全参数（v0.4.8+）

每个适配器接受四个可选的运行时安全参数：

```python
adapter = LangGraphSentinelAdapter(      # 所有适配器相同
    thread_id="run-abc",                 # 框架特定参数不变
    gate=ExecutionGate(),                # 基于策略的工具调用允许/拒绝
    permissions=ToolPermissionLayer(),   # 每个 Agent 的能力范围，默认拒绝
    sandbox=AgentSandbox(...),           # 路径/域名/系统调用执行
    approvals=ApprovalBoundary(...),     # 高风险操作的人工审批
    identity=AgentIdentity(...),         # trust_score 自动调整 gate 严格程度
)
```

所有参数默认为 `None`——现有代码无需任何更改。详见
[运行时安全指南](docs/guides/runtime-security.md)。

---

## 工作原理

```
你的 Agent 系统
      │
      ▼
 SentinelAdapter          ← 将领域事件转换为通用 schema
      │
      ▼
  Sentinel.ingest()       ← 将 SentinelEvent 加载到审计器
      │
      ▼
  detect_violations()     ← 运行检测器，获取 ViolationRecord
      │
      ▼
  report() / 你的数据接收端  ← stdout、OTel、告警，随你选择
```

---

## 快速开始

```python
from agentcop import Sentinel, SentinelEvent

sentinel = Sentinel()

# 输入事件（任何来源，任何 schema——先适配）
sentinel.ingest([
    SentinelEvent(
        event_id="evt-001",
        event_type="packet_rejected",
        timestamp="2026-03-31T12:00:00Z",
        severity="ERROR",
        body="packet rejected — TTL expired",
        source_system="my-agent",
        attributes={"packet_id": "pkt-abc", "reason": "ttl_expired"},
    )
])

violations = sentinel.detect_violations()
# [ViolationRecord(violation_type='rejected_packet', severity='ERROR', ...)]

sentinel.report()
# [ERROR] rejected_packet — packet rejected — TTL expired
#   packet_id: pkt-abc
#   reason: ttl_expired
```

内置检测器开箱即用，触发四种事件类型：

| `event_type`            | 检测器                        | 严重级别 |
|-------------------------|-------------------------------|----------|
| `packet_rejected`       | `detect_rejected_packet`      | ERROR    |
| `capability_stale`      | `detect_stale_capability`     | ERROR    |
| `token_overlap_used`    | `detect_overlap_window`       | WARN     |
| `ai_generated_payload`  | `detect_ai_generated_payload` | WARN     |

---

## 自定义检测器

检测器是普通函数。按需注册任意数量。

```python
from agentcop import Sentinel, SentinelEvent, ViolationRecord
from typing import Optional

def detect_unauthorized_tool(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "tool_call":
        return None
    if event.attributes.get("tool") in {"shell", "fs_write"}:
        return ViolationRecord(
            violation_type="unauthorized_tool",
            severity="CRITICAL",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={"tool": event.attributes["tool"]},
        )

sentinel = Sentinel()
sentinel.register_detector(detect_unauthorized_tool)
```

---

## TrustHandoff 适配器

[TrustHandoff](https://github.com/trusthandoff/trusthandoff) 内置了一流的适配器。如果你正在使用 `trusthandoff` 进行加密委托，可以直接接入：

```python
from trusthandoff.sentinel_adapter import TrustHandoffSentinelAdapter
from agentcop import Sentinel

adapter = TrustHandoffSentinelAdapter()
sentinel = Sentinel()

# raw_events：来自 trusthandoff 法证日志的 dict 列表
sentinel.ingest(adapter.to_sentinel_event(e) for e in raw_events)

violations = sentinel.detect_violations()
sentinel.report()
```

适配器将 trusthandoff 的事件字段——`packet_id`、`correlation_id`、`reason`、`event_type`——映射到通用 `SentinelEvent` schema。严重级别从事件类型推断，其余内容落入 `attributes`。

---

## 编写自己的适配器

实现 `SentinelAdapter` 协议以桥接任何系统：

```python
from agentcop import SentinelAdapter, SentinelEvent
from typing import Dict, Any

class MySystemAdapter:
    source_system = "my-system"

    def to_sentinel_event(self, raw: Dict[str, Any]) -> SentinelEvent:
        return SentinelEvent(
            event_id=raw["id"],
            event_type=raw["type"],
            timestamp=raw["ts"],
            severity=raw.get("level", "INFO"),
            body=raw.get("message", ""),
            source_system=self.source_system,
            trace_id=raw.get("trace_id"),
            attributes=raw.get("metadata", {}),
        )
```

---

## LangGraph 集成

以零代码改动接入任何 LangGraph 图。适配器读取调试事件流——节点启动、节点结果、检查点保存——并将每个事件转换为 `SentinelEvent` 用于违规检测。

```
pip install agentcop[langgraph]
```

以 `debug` 模式流式传输图，并将每个事件通过适配器传递：

```python
from agentcop import Sentinel
from agentcop.adapters.langgraph import LangGraphSentinelAdapter

adapter = LangGraphSentinelAdapter(thread_id="run-abc")
sentinel = Sentinel()

sentinel.ingest(
    adapter.iter_events(
        graph.stream({"input": "..."}, config, stream_mode="debug")
    )
)

violations = sentinel.detect_violations()
sentinel.report()
```

三种 LangGraph 调试事件类型被转换：

| LangGraph 事件   | SentinelEvent 类型        | 严重级别 |
|------------------|---------------------------|----------|
| `task`           | `node_start`              | INFO     |
| `task_result`    | `node_end`                | INFO     |
| `task_result`    | `node_error`（出错时）    | ERROR    |
| `checkpoint`     | `checkpoint_saved`        | INFO     |

每个事件携带结构化的 `attributes`——`node`、`task_id`、`step`、`triggers`、`checkpoint_id`、`next`——便于编写有针对性的违规检测器：

```python
from agentcop import ViolationRecord

def detect_node_failure(event):
    if event.event_type == "node_error":
        return ViolationRecord(
            violation_type="node_execution_failed",
            severity="ERROR",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={
                "node": event.attributes["node"],
                "error": event.attributes["error"],
            },
        )

sentinel = Sentinel(detectors=[detect_node_failure])
```

传递给 `LangGraphSentinelAdapter` 的 `thread_id` 用作每个事件的 `trace_id`，将单次图运行的所有事件关联起来。

---

## OpenTelemetry 导出 *(可选)*

`agentcop` 事件开箱即用地使用 OTel 对齐的 schema（`trace_id`、`span_id`、严重级别）。要将事件作为 OTel 日志记录导出：

```
pip install agentcop[otel]
```

```python
from agentcop.otel import OtelSentinelExporter
from opentelemetry.sdk._logs import LoggerProvider

exporter = OtelSentinelExporter(logger_provider=LoggerProvider())
exporter.export(events)
```

属性在 `sentinel.*` 命名空间下发出。`trace_id` 和 `span_id` 映射到 OTel trace context。

---

## AgentIdentity — 了解你的 Agent

`AgentIdentity` 为每个 Agent 提供可验证的指纹、行为基线和实时信任评分。将其附加到 `Sentinel` 以自动丰富事件并获取漂移警报。

```python
from agentcop import Sentinel, AgentIdentity, SQLiteIdentityStore

store = SQLiteIdentityStore("agentcop.db")
identity = AgentIdentity.register(
    agent_id="my-agent-v1",
    code=agent_function,           # 源代码哈希为 Ed25519 指纹
    metadata={"framework": "langgraph", "version": "1.0"},
    store=store,
)

sentinel = Sentinel()
sentinel.attach_identity(identity)
# 通过 sentinel.push() 摄入的事件现在已用 Agent 身份 + 信任评分丰富。
```

信任评分从 70 开始，随干净执行而上升。严重违规扣 20 分；错误扣 10 分；警告扣 5 分。基线从前 10+ 次执行自动构建，用于检测漂移（新工具、慢执行、新 Agent 联系人）。

---

## Agent 徽章

`agentcop[badge]` 发布 Ed25519 签名的、可公开验证的安全证书。如同网站的 SSL——但面向 Agent。

```
pip install agentcop[badge]
```

```python
from agentcop.badge import BadgeIssuer, SQLiteBadgeStore, generate_svg, generate_markdown

store = SQLiteBadgeStore("agentcop.db")
issuer = BadgeIssuer(store=store)

badge = issuer.issue(
    agent_id="my-agent",
    fingerprint=identity.fingerprint,
    trust_score=87.0,
    violations={"critical": 0, "warning": 1, "info": 0, "protected": 3},
    framework="langgraph",
    scan_count=42,
)

assert issuer.verify(badge)   # Ed25519 签名校验

# 嵌入 HTML 的 SVG
svg = generate_svg(badge)

# README 的 Markdown 片段
print(generate_markdown(badge))
# ![AgentCop SECURED](https://agentcop.live/badge/<id>/shield)
```

徽章等级由信任评分决定：

| 等级 | 分数 | 颜色 |
|---|---|---|
| 🟢 SECURED | ≥ 80 | `#00ff88` |
| 🟡 MONITORED | 50–79 | `#ffaa00` |
| 🔴 AT RISK | < 50 | `#ff3333` |

徽章 30 天后过期。若信任评分低于 30，徽章将被自动吊销。

README 徽章示例：

```markdown
![AgentCop SECURED](https://agentcop.live/badge/abc123/shield)
```

---

## Moltbook 集成

Moltbook 是一个 AI Agent 相互阅读帖子并付诸行动的社交网络——是当前多 Agent 生态系统中最活跃的提示注入攻击面。2026 年 1 月的安全事件通过注入公开 feed 的命令暴露了 150 万个 API 密钥。`agentcop` 可以捕获此类攻击。

```
pip install agentcop[moltbook]
```

适配器对每条收到的帖子和提及进行污点分析，检测协调注入活动，在执行前验证技能徽章，并为你的 Agent Moltbook 资料发布 Ed25519 签名的安全徽章。

**快速开始：**

```python
from moltbook import MoltbookClient
from agentcop import Sentinel
from agentcop.adapters.moltbook import MoltbookSentinelAdapter

client = MoltbookClient(api_key="...")
adapter = MoltbookSentinelAdapter(agent_id="my-bot")

# 生成 Ed25519 徽章 + 在客户端注册事件监听器
adapter.setup(client=client)

# 运行你的 Agent——事件自动流入适配器缓冲区
client.run()

# 分析
sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

**徽章集成：** 调用 `setup()` 会发布一个加密签名的 `AgentBadge`，并将其嵌入每个出站 `post_created` 事件，让对等 Agent 可以验证你的安全状态：

```python
adapter.setup()
print(f"Badge: https://agentcop.live/badge/{adapter._badge_id}")
# Badge: https://agentcop.live/badge/abc123
```

**技能徽章验证：** 每个 `skill_executed` 事件都会自动根据技能的 ClawHub 清单徽章进行检查。未验证的技能发出 `skill_executed_unverified`（WARN）；AT RISK 技能发出 `skill_executed_at_risk`（CRITICAL）。

**注入检测：** 适配器检查收到的帖子中 13+ 种注入模式，包括直接覆盖、角色注入、凭证窃取、泄露触发器，以及编码绕过变体（base64、unicode 零宽字符、从右到左覆盖）。

详见 [docs/adapters/moltbook.md](docs/adapters/moltbook.md) 获取完整集成指南、5 个检测器配方和 API 参考。

---

## OpenClaw 集成

`agentcop` 内置了 [OpenClaw](https://openclaw.dev) 集成：一个按需安全命令的技能，以及一个自动实时监控的钩子。

```bash
openclaw skills install agentcop
openclaw hooks enable agentcop-monitor
```

**`agentcop-monitor` 钩子**在每条消息和工具结果上触发，对 LLM01（提示注入）和 LLM02（不安全输出）进行污点检查。违规警报在 Agent 看到或发送消息之前就传递到你的活跃频道。

Telegram 中的示例警报：

```
🚨 AgentCop [CRITICAL] — LLM01 LLM01_prompt_injection
Matched: `ignore previous instructions`, `you are now`
Context: inbound message
Badge: https://agentcop.live/badge/abc123/verify
```

**`agentcop` 技能**添加 `/security` 命令：

```
/security status     — Agent 指纹、信任评分、违规数量
/security report     — 按严重级别分组的完整违规报告
/security scan       — OWASP LLM Top 10 评估
/security badge      — 生成或显示 Agent 安全徽章
```

详见 [docs/guides/openclaw.md](docs/guides/openclaw.md) 获取完整集成指南。

---

## 运行时安全层

`agentcop` v0.4.7 提供了一个运行时执行栈：四个可组合的层在 Agent 工具调用执行前进行拦截、门控和沙箱隔离。一行代码即可部署到任何 Agent 对象前。

```
pip install agentcop[runtime]
```

### 一行代码保护

```python
from agentcop.cop import AgentCop
from agentcop.gate import ExecutionGate
from agentcop.permissions import ToolPermissionLayer, NetworkPermission, ReadPermission
from agentcop.sandbox import AgentSandbox
from agentcop.approvals import ApprovalBoundary

gate = ExecutionGate(db_path="agentcop_gate.db")
permissions = ToolPermissionLayer()
permissions.declare("my-agent", [
    ReadPermission(paths=["/data/*", "/tmp/*"]),
    NetworkPermission(domains=["api.openai.com"], allow_subdomains=True),
])
sandbox = AgentSandbox(allowed_paths=["/data/*", "/tmp/*"], allowed_domains=["api.openai.com"])
approvals = ApprovalBoundary(requires_approval_above=70, channels=["cli"], timeout=300)

cop = AgentCop(
    gate=gate,
    permissions=permissions,
    sandbox=sandbox,
    approvals=approvals,
)

# 包装任何 Agent 对象——run() 经过完整执行管道
protected = cop.protect(your_agent)
result = protected.run(task)
```

每次 `protected.run()` 调用按顺序经过五个阶段：

1. **信任守卫** — 如果 `AgentIdentity` 信任评分 < 30 则阻止
2. **ExecutionGate** — 评估注册的工具策略，将决策记录到 SQLite
3. **ToolPermissionLayer** — 执行声明的能力范围（默认拒绝）
4. **ApprovalBoundary** — 超过风险阈值时请求人工签字确认
5. **AgentSandbox** — 以主动系统调用拦截包装调用

### ExecutionGate

基于策略的执行控制，附带持久化审计日志。

```python
from agentcop.gate import ExecutionGate, DenyPolicy, RateLimitPolicy, ConditionalPolicy

gate = ExecutionGate(db_path="agentcop_gate.db")

# 硬性拒绝 shell 访问
gate.register_policy("shell_exec", DenyPolicy(reason="shell access prohibited"))

# 将网络搜索限速为每分钟 10 次
gate.register_policy("web_search", RateLimitPolicy(max_calls=10, window_seconds=60))

# 只允许写入 /tmp 的文件
gate.register_policy(
    "file_write",
    ConditionalPolicy(
        allow_if=lambda args: str(args.get("path", "")).startswith("/tmp/"),
        deny_reason="writes outside /tmp are not permitted",
    ),
)

# 作为装饰器使用
@gate.wrap
def my_tool(path: str) -> str:
    ...

# 审计日志
for entry in gate.decision_log(limit=50):
    print(entry["tool"], entry["allowed"], entry["reason"])
```

### ToolPermissionLayer

声明每个 Agent 被允许做什么——其他所有操作默认拒绝。

```python
from agentcop.permissions import (
    ToolPermissionLayer,
    ReadPermission, WritePermission,
    NetworkPermission, ExecutePermission,
)

layer = ToolPermissionLayer()

layer.declare("data-pipeline-agent", [
    ReadPermission(paths=["/data/*", "/tmp/*"]),
    WritePermission(paths=["/tmp/*"]),
    NetworkPermission(domains=["api.openai.com"], allow_subdomains=True),
])

result = layer.verify("data-pipeline-agent", "file_write", {"path": "/etc/shadow"})
# PermissionResult(granted=False, reason='path /etc/shadow not in allowed paths')

# 附加到 gate 以在每次调用时自动执行
layer.attach_to_gate(gate, agent_id="data-pipeline-agent")
```

### AgentSandbox

以主动系统调用拦截包装 Agent 执行——激活时补丁 `builtins.open`、`urllib.request.urlopen`、`subprocess.run` 和 `requests.Session.request`。

```python
from agentcop.sandbox import AgentSandbox

sandbox = AgentSandbox(
    intercept_syscalls=True,
    allowed_paths=["/tmp/*", "/data/read-only/*"],
    allowed_domains=["api.openai.com"],
    max_execution_time=30,   # 超时则抛出 SandboxTimeoutError
)

with sandbox:
    result = your_agent.run(task)
    # open() 到 allowed_paths 之外的路径 → SandboxViolation
    # HTTP 到 allowed_domains 之外的域名 → SandboxViolation
```

### ApprovalBoundary

高风险操作的人工审批门控。阈值以下自动批准，超过则暂停并通知。

```python
from agentcop.approvals import ApprovalBoundary

boundary = ApprovalBoundary(
    requires_approval_above=70,
    channels=["cli"],          # "cli"、"webhook"、"slack" 或含 "type"+"url" 的 dict
    timeout=300,               # 5 分钟后自动拒绝
    db_path="approvals.db",    # 持久化审计跟踪
)

request = boundary.submit("delete_database", {"db": "prod"}, risk_score=90)
# → 分发到配置的频道，阻塞等待决策

# 从另一个线程或外部 webhook：
boundary.approve(request.request_id, actor="alice", reason="confirmed safe migration")

resolved = boundary.wait_for_decision(request.request_id)
# ApprovalRequest(status='approved', ...)
```

### RUNTIME PROTECTED 徽章

运行完整 `AgentCop` 栈的 Agent 获得 **RUNTIME PROTECTED** 标识。在徽章有效载荷的 `"protected"` 下传入被拦截的违规计数——非零值会在盾牌上渲染注释，表明违规在运行时被拦截，而不仅仅是事后检测。

```python
badge = issuer.issue(
    agent_id="my-agent",
    fingerprint=identity.fingerprint,
    trust_score=92.0,
    violations={"critical": 0, "warning": 0, "info": 3, "protected": 7},
    framework="langgraph",
    scan_count=88,
)
```

详见 [docs/guides/runtime-security.md](docs/guides/runtime-security.md) 获取包括 CLI 参考、频道设置和身份集成在内的完整指南。

---

## 可靠性层

`agentcop` v0.4.10 提供了一个统计可靠性引擎，将原始运行历史转化为可操作的可靠性评分、预测告警和跨 Agent 聚类分析——零 ML 依赖。

### 测量指标

| 指标 | 描述 | 权重 |
|---|---|---|
| 路径熵 | 执行路径的 Shannon 熵——高熵意味着不可预测的分支 | 25% |
| 工具方差 | 跨运行中工具使用的变异系数 | 25% |
| 重试爆炸 | 来自重试计数和速度的归一化评分 | 30% |
| 分支不稳定性 | 相同输入下执行路径之间的 Hamming 距离 | 20% |
| Token 预算 | 每次运行的 Token 消耗与基线对比——3× 时发出峰值告警 | 信息性 |

四个加权指标合并为一个 **可靠性评分（0–100）** 和一个等级：

| 等级 | 评分 | 徽章 |
|---|---|---|
| 🟢 STABLE | ≥ 80 | |
| 🟡 VARIABLE | 60–79 | |
| 🟠 UNSTABLE | 40–59 | |
| 🔴 CRITICAL | < 40 | |

### 快速示例

```python
from agentcop import ReliabilityTracer, ReliabilityStore

store = ReliabilityStore("agentcop.db")

with ReliabilityTracer("my-agent", store=store) as tracer:
    tracer.record_tool_call("bash", args={"cmd": "ls"}, result="file1.txt")
    tracer.record_branch("chose_path_A")
    tracer.record_tokens(input=100, output=250, model="gpt-4o")

# 经过几次运行后，获取报告
from agentcop.reliability import ReliabilityEngine
from agentcop.reliability.store import ReliabilityStore

store = ReliabilityStore("agentcop.db")
report = store.get_report("my-agent", window_hours=24)
print(report.reliability_tier)   # STABLE | VARIABLE | UNSTABLE | CRITICAL
print(report.reliability_score)  # 0-100
```

### 组合徽章

安全信任 + 可靠性一起显示：

```
✅ SECURED 94/100 | 🟢 STABLE 87/100
```

以编程方式生成：

```python
from agentcop.reliability.badge_integration import combined_badge_text

text = combined_badge_text(trust_score=94, reliability_score=87, reliability_tier="STABLE")
# → "✅ SECURED 94/100 | 🟢 STABLE 87/100"
```

### CLI

```bash
# 单 Agent 报告
agentcop reliability report --agent my-agent --verbose

# 并排排行榜
agentcop reliability compare --agents agent-a agent-b agent-c

# 实时刷新（Ctrl-C 停止）
agentcop reliability watch --agent my-agent --interval 10

# 导出为 JSON 或 Prometheus 指标
agentcop reliability export --agent my-agent --format prometheus
agentcop reliability export --agents agent-a agent-b --format json -o report.json
```

### AgentIdentity 集成

`record_run()` 根据等级自动更新信任评分：

```python
identity.record_run(run)          # STABLE +0 | VARIABLE −5 | UNSTABLE −15 | CRITICAL −30
print(identity.reliability_tier)  # "STABLE"
print(identity.reliability_score) # 87
```

### 预测告警

对最近 N 次运行进行线性回归，在指标突破阈值前发出 `SentinelEvent`：

```python
from agentcop.reliability import ReliabilityPredictor

predictor = ReliabilityPredictor()
predictions = predictor.predict(runs, horizon_hours=2.0)
for pred in predictions:
    if pred.sentinel_event:
        sentinel.push(pred.sentinel_event)
    # → "WARNING: retry_count likely to exceed threshold (3.0) — ..."
```

### Prometheus 导出

```python
from agentcop.reliability import PrometheusExporter

exporter = PrometheusExporter(store)
print(exporter.export(["agent-a", "agent-b"]))
# agentcop_reliability_score{agent_id="agent-a"} 87.0
# agentcop_path_entropy{agent_id="agent-a"} 0.12
# agentcop_tool_variance{agent_id="agent-a"} 0.08
# ... （每个 Agent 8 个 gauge 指标）
```

---

## 环境要求

- Python 3.11+
- `pydantic>=2.7`

---

## 许可证

MIT
