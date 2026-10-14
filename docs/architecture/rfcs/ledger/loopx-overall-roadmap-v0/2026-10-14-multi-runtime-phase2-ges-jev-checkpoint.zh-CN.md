# 多运行时 Phase 2A/2B 放置检查点（2026-10-14）

在 Phase 1（多 Coding Worker / 任务级模型路由 / execution_package）交付之后记录。
本检查点只**放置** GES 能力注入与 Jev 模型建议；**不授权实现**。两项均被下列外部契约阻塞。

路线图归属：**S4**（运行时/宿主）与 **S8**（能力/扩展）。相关材料：
[Project Skill Delivery](../../../../loopx/capabilities/project_skill_delivery/README.md)、
[Jev 可选语义协助 RFC](../../optional-semantic-assistance-jev-v0.zh-CN.md)，
以及 `loopx/control_plane/worker_catalog.py` 中的 Phase 1 Coding Worker 目录。

## 2A — GES 能力注入（阻塞于 D-01）

| 决策 | 选择 |
| --- | --- |
| capability id | `project-skill-delivery`（复用既有 owner，不另造 skill 内核） |
| provider id | `loopx-ges` |
| 包路径 | `packages/loopx-ges/`（扩展交付，默认关闭） |
| 优先扩展 surface | `cursor`（`CURSOR_HOME/skills` 与/或项目内 `.cursor/skills`）与 `dsh`（D-01 命名宿主路径后） |
| 复用 | project-skill-delivery 既有 preview / apply / 拒绝覆盖 / digest / readback |
| D-01 前不做 | 任何写入 Cursor/DSH 的安装、GES schema 解析、远程 skill 字节自动信任 |

**D-01（阻塞）：** GES 仓库身份、skill 包 schema、安装安全契约（digest、覆盖策略、权限边界）及在范围内的宿主路径。契约未被 owner 接受前，不落地 `packages/loopx-ges` 代码。

## 2B — Jev 运行时模型建议（阻塞于 D-02）

| 决策 | 选择 |
| --- | --- |
| capability id | `runtime-model-recommendation`（新的调用方结果面，不是 progress_review 别名） |
| provider 包 | `packages/loopx-jev-routing/` |
| 传输 | 内置 stdio MCP client（参考 `packages/loopx-ark-turn`）；LoopX 核心仍不持有环境级 MCP client |
| 模式 | 独立 schema：`off` / `shadow` / `enforce`——**不**复用 progress_review 的 off/shadow 或 assist 语义 |
| enforce 输出 | 仅候选模型/effort **id**；每个候选再进入 `worker_route.py` 做端点能力校验 |
| 权限 | 建议不得改写 Goal/Todo 权威，不得绕过 managed_execution_profile，不得在未经理由解析器时自动生效 |

**D-02（阻塞）：** MCP 服务来源、工具 schema 与认证方式。既有 `packages/loopx-jev` HTTP off/shadow progress_review 观察器保持独立；Phase 2B 不得把路由并入该观察器。

既有 [Jev RFC](../../optional-semantic-assistance-jev-v0.zh-CN.md) 仍是判断/评估研究基线。**模型路由**产品采纳是 D-02 之后的独立决策；RFC 收录本身不授权本 provider。

## 明确不声称

- Phase 1 的 Codex / Cursor / DSH Worker 不代表 GES 或 Jev 就绪。
- 本检查点不新增共享词汇、CLI、Dashboard 控件或 journal 字段。
- 真实 Cursor / Codex / DSH 账号集成仍属 PRD 的 D-03 / D-04 证据门控；环境不可用记为证据缺口，不得跳过冒充通过。

## 下一步（owner）

1. 与外部契约 owner 接受或修订 D-01、D-02。
2. 仅在决策之后开聚焦 RFC（或修订既有 RFC），再以独立 PR、默认关闭 flag 与 readback 实现。
3. 在被替代前，以本 ledger 条目为放置真源。
