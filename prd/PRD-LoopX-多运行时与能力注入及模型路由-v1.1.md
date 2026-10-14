---
document_type: Product Requirements Document
title: LoopX 多 Coding Runtime、GES 能力注入与 Jev 模型路由
version: v1.1
status: DRAFT_FOR_REVIEW
prepared_on: 2026-10-14
repository: https://github.com/loudon84/loopx
baseline_branch: feature/dsh-coding-worker
baseline_commit: 1224cbcc9e02e68593e9eea2a40b6f301d410d5c
source_prd: PRD-LoopX-Coding-Runtime-Router-GES-Jev-MCP-v1.0.md
---

# LoopX 多 Coding Runtime、GES 能力注入与 Jev 模型路由 PRD

## 1. 文档定位与结论

本 PRD 在用户提供的 v1.0 需求基础上，结合指定分支源码重写，供产品、架构、开发与测试共同评审。目标是让 LoopX 在既有治理链路内，根据显式配置或受控策略选择 DSH、Codex、Cursor，并将工程能力与模型选择结果纳入可追溯的执行快照。

**本次是需求澄清与源码静态分析，不是功能实现或测试验收。所有验收条目初始状态均为“未执行”。**

核心原则：

1. 扩展现有 Goal / Todo / Turn、Worker Catalog、配额、租约、验证与恢复链路，不新建第二套任务系统。
2. “运行时选择”“模型选择”“能力注入”“代码执行”“结果验收”职责分离。
3. 模型选择必须真正作用于执行参数；只存一个 model 字段不算实现。
4. GES 负责工程能力内容及 Manifest；LoopX 负责受控装配、调用与证据记录。
5. Jev 提供结构化建议；LoopX Model Policy 校验并决定是否采纳；真正的模型调用由 Runtime 执行。
6. MCP 是集成协议，不能被当作自动实现模型切换、权限隔离或跨 Runtime 会话迁移的能力。

本文中的 MUST / 必须是验收要求；“建议”是实现方向；明确标记的待决事项不能默认为已确认。

## 2. 当前源码基线与证据口径

| 项目 | 固定基线 |
|---|---|
| 仓库 | `loudon84/loopx` |
| 分支 | `feature/dsh-coding-worker` |
| 本次读取时 HEAD | `1224cbcc9e02e68593e9eea2a40b6f301d410d5c` |
| Git 提交时间 | 2026-10-10 16:39:26 UTC，即北京时间 2026-10-11 00:39:26 |
| 本次复核日期 | 2026-10-14，北京时间 |
| 原始 PRD | `PRD-LoopX-Coding-Runtime-Router-GES-Jev-MCP-v1.0.md` |
| 原始文件 SHA-256 | `6446993f5e7d267173eb2a49b640f495505ca1c6061ad0ff06aa2bdd568ee83b` |
| 检查方法 | GitHub 指定提交源码、已有测试定义、相关官方接口文档；未运行项目与真实模型 |

固定提交：[源码基线](https://github.com/loudon84/loopx/commit/1224cbcc9e02e68593e9eea2a40b6f301d410d5c)。分支之后可能变化，实施前必须重新记录 HEAD 并复核差异，不能把本报告当作永久的“最新源码”。

### 2.1 已有能力与待补缺口

| 能力 | 当前源码事实 | 本期需求方向 |
|---|---|---|
| Goal / Todo / Turn | 已有治理对象、执行与恢复链路 | 复用，不新建 Task 主权 |
| DSH Worker | Worker Catalog 与 governed loop 的部分关键路径绑定 DSH | 提取可扩展运行时分派，同时保持原路径兼容 |
| Codex CLI | 已有 headless 执行、模型参数与结果处理 | 接入统一 Worker 路由和证据视图，不从零重写 |
| generic-cli | 已有 adapter 命令入口和 LoopX typed result 契约 | 可作为 Cursor 首版接入位置 |
| Cursor | 已有 Host 表面集成、安装/启动相关路径及测试 | 补齐受治理 Coding Worker 的执行闭环 |
| Goal 创建界面 | 可选择执行路由和 Worker endpoint，默认 DSH；尚未贯通任务级模型选择 | 增加 Profile / 模型配置和执行回显 |
| 模型配置 | Codex、DSH SDK、子代理等已有各自配置；DSH CLI 有接收却未应用的字段 | 建立统一解析、能力校验与实际应用证据 |
| Jev | 已有 loopx-jev off/shadow 观察组件及 HTTP transport；Core progress_review 另有受约束 assist | 保留两者原职责；新增模型路由不能与现有评估用途/模式混淆 |
| 工程技能与上下文 | 已有技能交付、安装及上下文装配能力 | 接入 GES 契约，复用现有设施而非重复建设 |
| 实验模型路由 | 存在实验 planner-worker 路由能力 | 不能直接等同于生产主调度器的统一模型策略 |
| 验证和恢复 | 已有 journal、验证与结算恢复边界 | 全运行时复用；防止恢复时重复执行外部任务 |

具体代码证据与搜索范围见文末“源码核查附录”。“已有源码”不代表已通过本次运行验证。

### 2.2 原 PRD 中需要修正的表述

| 原稿表述 | 修订后的需求口径 |
|---|---|
| 当前仅有 Hermes → LoopX → DSH | DSH 是当前 Worker 关键路径，但已有 Codex / generic-cli / Cursor Host 等能力，不从零设计 |
| 当前无法选择模型 | 已有局部配置与实验路由，缺的是统一、受治理、可核验的任务级模型选择 |
| 新增一套 Runtime 目录及框架 | 提供统一能力边界；实际目录安排由设计确定，优先复用既有控制面 |
| Cursor 固定调用 `cursor-agent` | 配置并探测安装命令；当前官方文档使用 `agent` |
| Jev 负责多模型调用 | Jev 只建议；LoopX 决策和调度，Runtime 执行模型调用 |
| Runtime 不得保存任何任务状态 | 禁止成为第二业务事实源；允许内部 session、缓存和运行诊断，由 LoopX 关联 |
| SUCCESS → FAILED | 成功与失败是不同结果，不是顺序步骤；复用已有 Todo/Turn 状态 |
| 每次执行必须产出 Commit | 允许审查、只读、无改动任务；保存 baseline、差异、状态及可空结果 commit |
| 测试矩阵预填 PASS | 全部改为未执行，必须附实际命令、环境和证据才可通过 |

## 3. 背景、用户与业务目标

### 3.1 目标用户

- 任务发起者：通过 LoopX / Hermes 提交编码、修复、测试或评审任务。
- 平台维护者：配置可用 Worker、模型、权限、工程能力来源和路由策略。
- 审核者：查看执行证据、风险、失败原因与交付是否符合验收。

### 3.2 产品目标

| ID | 目标 | 可验证结果 |
|---|---|---|
| G-01 | 三类 Runtime 统一接入治理链路 | DSH、Codex、Cursor 均可完成获准的任务并回写统一证据 |
| G-02 | 不同任务可选择不同 Runtime 与模型 | 同一 Goal 下不同 Todo 的实际执行参数与配置一致 |
| G-03 | 工程能力按仓库与任务注入 | 可还原 GES Manifest、技能版本、最终装配内容与哈希 |
| G-04 | 路由可解释、可控、可回退 | 能区分显式配置、规则和 Jev 建议，异常不会静默扩大权限或费用 |
| G-05 | 保持恢复与状态一致性 | 结算恢复不重复执行；跨 Runtime 交接不产生双重任务主权 |
| G-06 | 为质量、成本与速度评测提供数据 | 可记录质量、耗时、用量及未知项，不预设节省比例 |

### 3.3 典型用户故事

1. 作为开发者，我希望为前端任务选择 Cursor、后端任务选择 DSH、审查任务选择 Codex，并能改写默认选择。
2. 作为开发者，我希望在同一 Runtime 内为不同任务指定不同可用模型，且界面能显示模型实际生效情况。
3. 作为维护者，我希望受信任的 GES Manifest 告诉执行器应该装配哪些工程技能，而无需 LoopX 管理技能内容编辑。
4. 作为审核者，我希望失败、重试和交接后的代码状态、模型、技能与测试证据可追溯。
5. 作为维护者，我希望先观察 Jev 的建议，再有控制地开启自动路由，随时可恢复显式策略。

## 4. 范围、阶段与非目标

### 4.1 交付分期

| 阶段 | 必须交付 | 发布条件 |
|---|---|---|
| Phase 1：多 Runtime 与确定性配置 | DSH/Codex 复用适配、Cursor 新适配、Worker Catalog 分派、Profile、显式模型配置、快照、恢复与证据、必要配置及回显入口 | 不依赖 GES 或 Jev 才能运行；所有 Runtime 基础验收通过 |
| Phase 2A：GES 能力注入 | 外部 GES 契约、Manifest 校验、能力装配、版本与来源证据、失败门控 | 实际 GES 契约与权限确认，集成验收通过 |
| Phase 2B：Jev MCP 模型建议与策略 | MCP 服务契约、候选集、策略校验、off/shadow/enforce、超时/失效策略、决策证据 | MCP 服务来源与协议确认；shadow 评测通过后才开启 enforce |

Phase 2A 与 2B 可基于 Phase 1 并行推进；不要求启用 Jev 才能使用 GES，也不要求所有任务都调用 Jev。完整 v1.1 范围包含以上三项，分期不等于取消原需求。

### 4.2 本期非目标

- 企业项目进度、人事或人员管理；替代 GitHub、Hermes、Multica。
- 让 LoopX 直接生成或修改业务代码；实际改动仍由 Runtime 执行。
- 在 LoopX 内建设工程技能内容编辑和分发平台。
- 任意注册不受信任的远程命令、技能或 MCP 服务。
- 同一 Todo 内无限递归多代理、多模型并行执行与自动合并。
- 把某个 Runtime 的会话直接导入另一个 Runtime。
- 自动提交、推送或发布所有任务结果。
- Hatchet、分布式 Worker Pool 和企业级队列平台；保留扩展接口，另立需求。
- 承诺 Jev 一定提高速度或降低成本；需要实际评测后决策。

### 4.3 本次工作授权边界

本文只是 PRD 交付。本次未修改仓库、未创建 PR、未安装 CLI、未创建凭据、未执行付费模型任务。后续实现涉及 LoopX 的控制面、适配器、Dashboard 与测试，不继承其他项目中“只改 nodeskclaw-knowledge”的目录限制。

## 5. 领域术语、职责与唯一事实源

| 名称 | 本文定义 |
|---|---|
| Goal | LoopX 管理的目标与边界 |
| Task / Todo | 产品语言 Task 对应现有 Todo，不新增平行任务实体 |
| Turn / Attempt | 一次受治理的执行或尝试，沿用现有 Turn 标识及关联规则 |
| Worker Endpoint | 具体可执行环境及运行时入口，不等于模型供应商 |
| Runtime | DSH、Codex CLI、Cursor CLI 等执行器 |
| Runtime Profile（产品概念） | 版本化任务路由配置；需与现有 SchedulerRuntimeProfile、managed model profile、manager 权限 profile、Goal execution_profile 分开命名和映射 |
| Model Policy | 候选模型、优先级、预算、降级和采纳规则 |
| GES | 外部工程能力提供方；具体项目、版本与协议需确认 |
| Capability Manifest | GES 输出的声明及可核验技能引用 |
| Execution Package | 本次执行实际使用的不可变上下文与配置快照 |
| Handoff | LoopX 协调的执行责任交接与证据传递，不是自动复制内部会话 |

### 5.1 唯一事实源

| 数据 | 权威来源 | 其他组件允许保存的内容 |
|---|---|---|
| Goal / Todo 业务状态 | LoopX 现有控制面 | 外部只保存关联 ID 和执行诊断 |
| Turn 执行与结算状态 | LoopX journal / 生命周期机制 | Runtime session 不得自行覆盖 LoopX 完成状态 |
| 生效 Runtime / 模型选择 | LoopX 已冻结执行快照 | Jev 建议、默认策略与运行观测作为证据 |
| 工程能力声明与版本 | GES 受信任 Manifest | LoopX 保存引用、摘要与实际注入快照，不编辑源技能 |
| 已提交代码 | Git 仓库 | 执行证据记录 commit |
| 未提交改动 | 受控 workspace 的实际差异与文件状态 | LoopX 保存 diff、状态和哈希；不能仅靠 commit 表示 |
| 模型策略 | LoopX 版本化 Model Policy | Runtime 内部默认不能静默覆盖显式策略 |

### 5.2 Jev 的职责纠正

Jev 可以对受控候选集给出模型/推理档位建议和置信信息。它不拥有 Task、Workflow、Runtime 生命周期、认证、审批或最终路由权。多模型升级流程由 LoopX 创建新尝试并调度，不能通过 Jev 返回任意 URL、命令或模型字符串直接执行。

## 6. 端到端流程

```text
用户创建 Goal / Todo，指定执行边界与验收
  → 读取 Runtime Profile、模型策略、仓库与受信任能力信息
  → 确定可用 Runtime / 模型候选，执行权限和预算过滤
  → 校验路由调用的数据外发授权及预算，再按需调用 Jev MCP（off / shadow / enforce）
  → LoopX 校验并解析最终路由
  → 对有副作用的准备动作先完成权限、预算及 workspace 写租约准入
  → 在实际工作区装配 GES 能力并完成兼容性检查，记录准备后的工作区指纹
  → 冻结 Execution Package，复核执行配额与租约有效性
  → 对应 Adapter 启动 Runtime
  → 收集流事件、日志、文件差异和测试产物
  → LoopX 独立验证
  → 持久化结算并更新 Todo / Turn
```

能力声明可以在路由前进行只读分析用于适配判断；实际物化必须在执行前完成。准备阶段的安装、写文件、下载或脚本执行也属于有副作用操作，必须先取得对应授权与租约，不能因尚未启动 Runtime 而绕过准入。准备期发现 Manifest、仓库或策略变化时，重新评估并生成新快照，不修改已执行快照。

## 7. 功能需求

### FR-01：Worker 注册与能力发现（Phase 1）

必须为 DSH、Codex、Cursor 提供可识别的 endpoint 与 capability descriptor，至少包含：运行时类型、命令/适配器版本、工作区支持、结构化输出、会话恢复、模型指定能力、推理档位、权限模式和健康状态。

准入检查区分“未安装”“未认证”“无效工作区”“模型不可用”“权限不满足”“版本不兼容”，不笼统显示“执行失败”。模型目录来源必须记录；若 CLI 没有机器可读发现接口，允许受控配置目录，但必须标明配置来源并完成最小实测。

已支持的 Runtime 通过配置切换无需修改业务代码；新增另一种未知 Runtime 仍需要 adapter 与兼容测试，不能承诺仅填名称即可支持。

### FR-02：统一 Adapter 契约（Phase 1）

必须覆盖准备、执行、取消、结果收集和恢复探测。沿用已有 LoopX 请求/结果契约或版本化兼容扩展。不得用一组同名空方法替代真实能力验证。

最少输入：Goal/Todo/Turn 标识、冻结配置、workspace、任务目标、边界、验收标准、能力清单、超时与预算策略。

最少输出：执行状态、Runtime/session 标识、退出码、结构化错误、实际模型观测、变更与测试证据引用、用量可用性。

不支持的能力明确返回 unsupported，不可伪造 resume/handoff 成功。业务交接由 LoopX 协调，adapter 只提供本地会话定位、停止和执行材料。

### FR-03：DSH 兼容与参数真实生效（Phase 1）

保留已有 DSH Worker 的准入、profile、patch、会话及恢复行为。区分 CLI runner 和 SDK runner 的模型能力。当前 CLI 中未应用的模型/effort 字段必须：

- 通过隔离的每次执行配置真正生效并提供证据；或
- 在能力描述中明确不支持，并在显式请求该能力时拒绝。

禁止 UI 接受模型覆盖后继续使用默认模型却显示“已切换”。本 PRD 的 Phase 1 目标包含现有 DSH CLI Worker 的逐任务模型选择，必须补齐实际配置应用并验证；对尚未实现的中间版本明确拒绝只是安全措施，不能算该发布目标通过。DSH SDK 可作为独立受支持路径，但不能悄悄替换既有 CLI endpoint 后宣称其问题已修复。

### FR-04：Cursor Coding Worker（Phase 1）

必须支持受控 workspace、任务 prompt、非交互执行、流式输出、明确 session ID 的恢复，以及逐任务模型指定。

首版建议经既有 generic-cli adapter seam 接入，再根据需要增设专用 Host，不另建绕过现有验证器的路径。

兼容约束：
- 探测实际命令与版本；当前官方文档使用 `agent`，不硬编码旧名称。
- 显式指定输出格式并转换 Cursor 事件为 LoopX 协议；不能原样输出不同协议的 NDJSON。
- 同时检查退出码、stderr、终止结果与独立验证。缺失终止事件不能当作成功。
- 禁止并发任务使用“恢复最近会话”的默认行为。
- 不默认开启 force/yolo、自动信任工作区或自动批准全部 MCP。
- 只支持目标环境已验证的权限模式；不能以提示词承诺代替实际隔离。
- 不假设旧会话恢复时指定另一个模型一定生效。跨模型首版默认新会话。

### FR-05：Codex 复用适配（Phase 1）

将现有 Codex CLI 执行、结构化事件、模型参数及证据能力纳入统一 Worker 路由。架构分析、审查、安全分析是推荐 Profile，不将 Codex 的产品能力硬限制为仅这些任务。

保留原有权限/审批映射与 resume 行为。发现统一层与原有参数冲突时拒绝或显式解释，不静默覆盖。

### 7.1 Phase 1 必验支持矩阵

| 运行组合 | 配置指定模型 | 结构化结果/验证 | 恢复 | 发布要求 |
|---|---|---|---|---|
| 既有 DSH CLI Worker | 必须补齐并验证 | 保持既有协议与独立验证 | 保持原身份/租约/结算 | 不可用 unsupported 作为最终交付 |
| Codex CLI Worker | 复用并验证现有参数 | 复用既有结果与验证 | 验证明确 session/Turn 绑定 | 纳入统一 catalog |
| Cursor CLI Worker | 新增并验证 | 转换协议并独立验证 | 验证明确 session；不支持组合须阻塞 | 新增闭环能力 |
| DSH SDK（若作为可选 endpoint 暴露） | 复用既有接线并真实验证 | 验证其对应 runner 契约 | 按该 endpoint 的实际能力声明 | 不与 DSH CLI 混记证据 |

部署平台和 CLI 版本按 D-03 确认，不承诺未经测试的所有操作系统组合。

### FR-06：Runtime Profile（Phase 1）

支持版本化 Profile：ID、版本、说明、Runtime 偏好、模型策略引用、能力要求、权限上限、验证要求、超时和预算配置。

默认示例可保留 frontend-coding / backend-coding / architecture-review，但它们是可配置产品策略，不是对模型能力优劣的实测结论。技能 ID 和模型 ID 必须在已授权目录中解析，不能把原稿中的 react、claude、gpt 等示意标签当作真实可执行标识。

### FR-07：任务级 Runtime 与模型解析（Phase 1 / 2B）

先过滤权限、账号可用性、任务能力、预算和 endpoint 健康状态，再处理偏好。建议优先顺序：

```text
Todo 明确覆盖
  > Goal 明确默认
  > Profile / Model Policy 的路由结果（其中可包含 Jev 建议）
  > 明确配置的 endpoint 默认
```

显式配置无效时返回错误，不默默选择下一个模型。自动策略中允许的 fallback 必须事先配置，记录触发原因、原候选和有效候选，不得突破审批和预算。

同一执行尝试只能绑定一个生效 Runtime/model 配置。中途换 Runtime 或模型视为新的受治理尝试，并关联原尝试，不能原地改写审计记录。

推理档位只在对应 Runtime 支持时提供，不能把一个供应商的 effort 枚举无条件映射给另一供应商。

新策略解析后必须进入既有 managed execution profile 的单一参数解析与来源记录链路。保留既有显式参数、环境及默认值兼容逻辑，不另造相互覆盖的第二默认系统；新旧配置冲突必须拒绝或明确消解。

### FR-08：不可变 Execution Package（Phase 1）

启动前冻结任务、边界、Runtime、模型、Profile、策略版本、workspace、基线代码、能力清单、会话策略与验收配置。身份至少绑定精确的 goal_id + goal_instance_id、todo_id、turn_key 和 turn_instance_id（按现有契约），不只记录可复用的 Goal 名称或别名。launcher 必须使用快照启动，而不是重新读取后来改变的全局默认值。

每个运行使用独立 argv/env/session；禁止通过修改共享全局环境、用户级模型配置或共享 DSH profile 实现临时切换。

重试/恢复默认沿用快照。发生明确策略升级、能力变化或用户改选时创建新尝试，并说明差异。

### FR-09：GES 外部契约（Phase 2A）

GES 接口、版本、命令/服务地址、Manifest schema 和安装协议是发布前置项。原 PRD 没有提供对应外部实现，本次不假定已有可运行 GES 服务。

Manifest 至少表达：schema 版本、生成器版本、仓库身份和基线、技术栈、技能 ID/版本/来源/内容哈希、Runtime 兼容性、必需与可选能力、生成时间。

`.ges/context.yaml` 可作为兼容文件位置，但文件名本身不代表可信。可执行安装源、路径和运行时指令必须经过信任及权限检查。

### FR-10：能力安装与注入（Phase 2A）

GES 拥有技能内容管理职责；LoopX 可以编排已授权的 GES 安装/物化动作，并记录结果，不复制技能编辑平台。复用既有 preview/apply、幂等安装、readback 与回滚边界；不得覆盖 unmanaged 或 locally_modified 的用户技能。

“GES 不执行代码任务”是指不承担业务任务、测试和 Workflow。安装过程若包含脚本、包管理或外部下载，必须单列为准备动作，不能用“仅安装技能”绕过执行与供应链权限。

注入要求：
- 复用已有技能交付与上下文设施，区分读取已有 Manifest、生成新 Manifest、安装技能三类操作。
- 按 Runtime 使用已验证方式注入；文件存在不等于被执行器读取。
- 保存实际装配清单、顺序、版本、哈希与上下文预算。
- 未知路径、越界 symlink、重复冲突 ID、哈希不符、无授权来源必须拒绝。
- 必需能力失败阻止启动；可选能力允许按预先配置降级并提示。
- Manifest 与仓库基线不匹配时重新生成/验证，不能静默使用陈旧能力。
- GES 关闭的 Profile 应继续可用，不能让全局 GES 故障阻塞所有 Phase 1 任务。

### FR-11：Jev MCP 契约（Phase 2B）

MCP 客户端调用发生在目标 Runtime 启动之前，由 LoopX 发起，不能依赖目标 Runtime 先调用一个工具再改变自己的启动模型。

服务至少有一个版本化的“建议模型”能力，具体工具名待集成设计确认。输入包含脱敏任务摘要、必要上下文、策略版本和已过滤候选 ID；输出包含所选候选 ID、结构化理由/原因码、置信信息、模型版本、请求关联与用量可用性。

MCP 是 Jev 服务包装层还是已有第三方服务，必须明确责任方、版本、认证、协议与错误语义。若暂时只能使用 HTTP 适配，可作为接口实验，但不能宣称“Jev MCP 集成完成”。

禁止发送密钥、完整凭据文件、无必要私有代码或业务敏感内容。调用范围和接收方必须符合组织的数据策略。

### FR-12：Jev 采纳与失败策略（Phase 2B）

此处是新增“模型路由用途”的独立模式配置，不得覆盖既有 loopx-jev observer 的 off/shadow，也不得改写 Core progress_review 的 assist/replan 语义。支持 off / shadow / enforce：
- off：不调用 Jev，不产生相关费用。
- shadow：记录建议和偏差，不改变执行配置。
- enforce：只有校验通过的建议才由 Model Policy 采纳。

必须处理超时、MCP 服务不可用、缺字段、候选外 ID、模型下线、低置信、策略版本冲突。fallback 可选择预授权固定配置或阻塞等待，必须可配置且可追溯。不得把认证/权限错误当作理由升级为更大权限。

置信度不是正确率，阈值需基于保留任务集评估，不直接把示例数值作为生产保证。缓存必须包含任务摘要、候选能力、策略版本、Manifest 指纹、Jev/服务版本和权限范围；变更后不能复用旧决策。

若未来增加运行后再评估、升级模型或多模型调用，该动作由 LoopX 创建受限新尝试并重新准入，不由 Jev 自行执行。

### FR-13：执行、交接与恢复（Phase 1）

LoopX 保持 Todo 主权；Runtime 可以返回进展、session 与完成声明，但完成声明必须经过现有验证和结算流程。

Handoff 必须明确目标 Runtime、交接原因、当前 workspace 状态、已完成/未完成事项、证据和权限。先使原执行停止或进入可证明安全的交接点，再移交租约与执行责任。不同 Runtime 默认新 session，不直接复用彼此内部会话。

Host 已执行、只缺 writeback/settlement 时，恢复只完成原 Turn 结算，不再调用模型或重复修改文件。外部结果未知时进入核对/待恢复状态，不盲目重试。

### FR-14：状态展示、证据与审计（Phase 1 / 2）

统一展示配置来源、推荐值、最终解析值、实际应用参数和可观测执行值，不能将它们合并成一个含糊 model 字段。

证据至少包括：

| 分类 | 必需内容 |
|---|---|
| 身份 | Goal/Todo/Turn/attempt 关联、幂等键、父尝试/交接关系 |
| 运行时 | endpoint、Runtime、adapter 与 CLI 版本、session ID（可空且说明） |
| 模型 | requested/resolved/applied/observed、能力验证结果与未知原因 |
| 决策 | 显式/规则/Jev 来源、策略版本、候选快照、fallback 原因 |
| 工程能力 | Manifest 哈希、技能 ID/版本/内容哈希、实际注入结果 |
| 代码状态 | baseline commit、workspace dirty 状态、diff/文件清单、结果 commit（可空） |
| 运行结果 | 时间、退出码、错误类别、取消/超时、脱敏日志位置 |
| 验证 | 测试命令、退出码、产物、独立验证器结果；未执行要明确 |
| 用量 | Runtime/Jev/重试用量、费用来源、未知字段，不把 unknown 写成 0 |

只读审查任务不要求产生 commit 或文件变更；写任务没有提交时也能以差异和产物验收。Git 推送、PR、部署不是隐式副作用。

这些字段应扩展既有 journal/receipt 的版本化证据结构。现有 host result 使用严格字段校验，不得直接塞入未声明字段，亦不得建立第二套可写业务状态。UI worker_run_readback 仍是只读投影，不是权威证据；公共投影不直接暴露 session、原始工具输出或本地敏感路径。真实 start/end 必须采集，不能用 journal 文件 mtime 代替执行时间。

## 8. 状态与错误语义

### 8.1 不新建平行 Task 状态机

当前 Todo 使用 `open/done/blocked/deferred`；Turn journal 使用 `in_progress/scheduler_action_required/committed/stopped/failed`。Goal activation 的 `active/stopped` 与 acceptance lifecycle 的 `active/retiring` 又是不同层级。`committed` 不等于 Goal 完成，`deferred` 不等于成功；不得做简单一对一转换。需要展示更细进度时，采用执行阶段/read model，不随意改变现有状态枚举与语义。

建议面向用户展示的阶段（不是新增持久化 enum 承诺）：

```text
待准入 → 准备中 → 执行中 → 验证中 → 结算中 → 完成
              ↘ 待审批 / 阻塞 / 失败 / 已取消
```

失败后重试创建或恢复符合原控制面语义的尝试，不把 success 串行转换成 failed。执行已完成但验证失败、执行已完成但结算未完成，必须是可区分的情况。

### 8.2 错误处理矩阵

| 情况 | 必须行为 |
|---|---|
| CLI/认证/模型不可用 | 启动前拒绝，保留可行动原因 |
| Jev 候选不合法或低置信 | 按预授权 fallback 或阻塞；不得执行任意输出 |
| GES 必需能力缺失 | 阻止对应 Profile 启动，列出具体能力；不一律要求重装所有环境 |
| GES 可选能力失败 | 按明确降级策略执行，保存缺失与影响 |
| Runtime 非零退出/协议异常 | 记录运行失败、已有副作用与日志，不伪造业务成功 |
| 运行超时/取消 | 请求停止并核验进程与子进程状态，核对 workspace，停止新增调用 |
| 未能确认外部进程已结束 | 保留待核对状态/租约控制，不直接发起可能重叠的新执行 |
| 独立验证失败 | 任务不标完成；保留修复路径和失败证据 |
| 结算失败 | 重放原结算，不重放已经发生的 Host 执行 |
| 策略、模型或 Manifest 在运行中变化 | 已启动尝试沿用快照；新尝试使用新版本并记录差异 |

## 9. 配置与界面需求

### 9.1 任务创建/执行前

保留 Chat agent 与 governed Worker 路由的既有互斥关系。选 Worker 后可配置 endpoint、Profile、模型默认及 Todo 覆盖；仅展示该环境实际支持或已验证的选项。

执行前提供解析结果预览：Runtime、模型、能力、权限、策略来源、是否调用 Jev、fallback 与预算约束。预览到执行之间若关键版本变化，重新确认/准入，不能执行与预览不符的配置。

### 9.2 执行中与完成后

显示准备/执行/验证/结算阶段、阻塞原因、可否恢复、模型建议是否采纳、实际执行证据。恢复、取消、重试和改选入口必须遵循现有权限与状态。

### 9.3 配置示意

下面仅为产品字段示意，不是当前代码已支持的 YAML，也不是最终接口 schema：

```yaml
profile_id: frontend-coding
profile_version: 1
runtime_id: cursor
worker_endpoint_id: configured-cursor-endpoint
model_policy:
  mode: explicit
  model_id: VALIDATED_ACCOUNT_MODEL_ID
capabilities:
  required: [APPROVED_FRONTEND_SKILL_ID]
jev:
  mode: off
ges:
  required: false
session_policy: new_per_attempt
```

实际模型 ID、技能 ID 与 endpoint 必须在部署时校验。不得把这些占位符直接当作生产配置。

## 10. 非功能与安全要求

1. 同一 Todo 不得被两个未协调的 Runtime 同时持有写执行责任；复用现有租约与幂等规则。
2. 并发任务使用独立 argv/env/session；写任务依照已有 workspace/worktree 策略隔离，不交叉污染配置。
3. 权限先于路由；Jev、GES 或 Profile 都不能扩大用户原始授权。
4. 密钥不进入命令行、版本库、公开证据或日志；凭据由受控机制提供，轮换与撤销不依赖改代码。
5. 解析外部 Manifest、MCP 和 Runtime 输出时按不可信输入处理，实施 schema、大小、路径与超时限制。
6. 每次执行记录版本、耗时与错误；日志遵循既有保留与访问策略。
7. 路由/准备超时、执行超时、最大重试次数和预算必须可配置且有有限上限，具体默认值由试验与部署规模决定。
8. 未取得供应商真实用量时不估造“实际费用”；套餐额度、缓存、失败重试与模型调用都应正确区分。
9. 性能验收基线包含关闭 GES/Jev 的 DSH/Codex 老路径。新增可选模块关闭后不得仍发起外部调用。

## 11. 验收矩阵

所有条目当前均为“未执行”。通过必须附测试环境、源码提交、CLI/SDK 版本、测试命令、日志与必要产物。静态代码存在和 mock 测试通过不能替代真实集成验收。

| ID | 关联需求 | 场景与通过标准 |
|---|---|---|
| AC-01 | FR-01 | 注册 DSH/Codex/Cursor；列出类型、能力、健康及可用/不可用原因 |
| AC-02 | FR-01 | CLI 缺失、未登录、模型无效、workspace 不可访问均在启动前明确拒绝 |
| AC-03 | FR-02/03/05 | 现有 DSH/Codex 正常执行、验证、证据与恢复回归通过 |
| AC-04 | FR-04 | Cursor 完成合成仓库中的受限修改与测试，经 LoopX 验证/结算后完成 |
| AC-05 | FR-04 | Cursor 非零退出、stderr-only、坏 JSON、缺 result、重复分片均不能错误完成 |
| AC-06 | FR-04/13 | Cursor 明确 session 恢复关联正确；不同任务不使用最近会话误恢复 |
| AC-07 | FR-06/07 | 同一 Goal 的不同 Todo 依据不同 Profile 调用指定 Runtime，配置来源可追溯 |
| AC-08 | FR-07 | 同 Runtime 的两个 Todo 使用不同有效模型，实际应用参数与观测证据一致；无法观测时明确证据边界 |
| AC-09 | FR-03/07 | DSH CLI 模型配置不再静默忽略；声称支持的字段有实际生效测试 |
| AC-10 | FR-07 | 显式无效模型、无效推理档位被拒绝，不静默使用默认 |
| AC-11 | FR-08 | 启动后修改 Goal 默认/策略，原尝试参数不变；新尝试记录新版本 |
| AC-12 | FR-08 | 两个并发任务的 argv/env/session/workspace 不串扰 |
| AC-13 | FR-09 | 给定受控 React/TypeScript 合成仓库，GES 输出符合已确认 schema 且带来源与版本的 Manifest |
| AC-14 | FR-09/10 | Manifest 过期、哈希不符、越界路径或未知技能源被拒绝或进入规定的重新验证流程 |
| AC-15 | FR-10 | 三类 Runtime 均有证据证明收到预期能力；不只检查技能文件存在 |
| AC-16 | FR-10 | 必需能力失败阻塞；可选能力按配置降级；不依赖 GES 的 Profile 不受影响 |
| AC-17 | FR-11 | 经实际 MCP 工具协商与调用取得 Jev 结构化建议，保存服务版本和关联 ID；只有 HTTP mock 不算通过 |
| AC-18 | FR-11/12 | Jev 输入候选均在授权与预算内；候选外输出、畸形结果不进入执行参数 |
| AC-19 | FR-12 | off 无调用；shadow 只记证据；enforce 仅按策略采纳，三种模式可对照 |
| AC-20 | FR-12 | 超时、未认证、低置信、模型下线分别命中指定 fallback/阻塞规则，有原因且不越权 |
| AC-21 | FR-12 | 策略、Manifest、候选目录或模型版本变化后旧路由缓存不被错误复用 |
| AC-22 | FR-13 | 同一幂等请求/重复事件不重复启动 Runtime，不重复结算 |
| AC-23 | FR-13 | 分别在 Host 后、validation 后、writeback 后、quota 后、scheduler pending 注入故障；恢复只补所缺阶段，不重复 Host/writeback/quota；损坏 journal 拒绝恢复，已 committed/stopped 返回已有结果 |
| AC-24 | FR-13 | 取消/超时后子进程与 workspace 状态可核查，未确认停止前不并发重跑 |
| AC-25 | FR-13 | DSH → Cursor 等显式交接保留目标、边界、代码状态和证据，新旧执行不同时写入 |
| AC-26 | FR-14 | Runtime 自述完成但独立验证失败时，Todo 不标 done；完成必须满足现有 durable readback、matching receipt、completion fence，不能信 Host 自报的 successor |
| AC-27 | FR-14 | 只读审查无 commit 也能正确验收，证据明确无改动；写任务能追溯未提交 diff |
| AC-28 | FR-14 | 身份、模型、能力、Git、测试、失败与用量证据完整；未知项带原因而非伪造 |
| AC-29 | 第9节 | UI/接口展示 Profile、模型覆盖及执行来源；Chat/Worker 原有路由互斥保持 |
| AC-30 | 第10节 | 非授权候选、敏感数据、越界安装/写入不能借 Jev/GES 获得授权；日志无凭据 |
| AC-31 | 第12节 | 新能力关闭后旧 DSH/Codex 路径可用，历史 journal 可读且恢复不被破坏 |
| AC-32 | 第13节 | 代表性任务完成质量、耗时和用量对照报告；不以置信度替代真实验收 |

## 12. 迁移、灰度与回滚

1. 新配置字段提供兼容默认值，旧 Goal/Task 不强制开启 GES 或 Jev。
2. 先接入确定性 Runtime 路由，再开放 GES，最后将 Jev 从 off → shadow → 小范围 enforce。
3. 旧 journal 缺少新字段时采用明确兼容读取策略，不能伪造历史模型或技能快照。
4. 运行中任务继续使用已有快照与相容 adapter。升级不强行替换活跃会话；必要时先排空。
5. Runtime/Profile/GES/Jev 功能开关可独立关闭。回滚不得删除历史证据或重放已经执行的任务。
6. 任何 schema 迁移、CLI 安装、凭据配置与真实任务测试必须在明确的测试环境先验证。

## 13. 质量、成本与效率评测

该功能的首要目标是可控调度和可验证交付。成本与速度是否改善必须独立评测，不把“可以选不同模型”当作“已经降本”。

至少比较：
- 固定 Runtime/模型的当前基线。
- 确定性 Profile/规则路由。
- Jev 建议参与的同候选集路由。

使用相同任务、相同代码初始状态、相同权限与验收，固定工具版本，交错执行顺序并记录重复运行波动。包括前端修改、后端修复、审查、测试补充与失败恢复，不只选容易体现收益的样本。

指标：验收通过率、总耗时及 P50/P95、模型和工具调用次数、重试/升级次数、Jev 成本、主模型成本、总成本、每成功任务成本、错误路由率、fallback 率。缺乏实际价格或用量时报告未知，不补零。

成本/延迟目标和样本规模由项目在上线前确定并冻结；未确定前不能写入未经验证的“节省 50%”等承诺。

## 14. Release Gate 与 Definition of Done

### Phase 1 Gate

- 三个 Runtime 的注册、准入、受控执行、模型配置、验证、证据和恢复验收通过。
- DSH/Codex 原有路径回归通过。
- 已支持 Runtime 之间的 Profile/显式选择无需修改业务代码，所有字段真实生效或明确不可选；声明支持逐任务模型选择的路径必须完成真实验证。
- 不依赖 Jev/GES 即可正常执行。

### Phase 2A Gate

- GES 外部协议与来源获得确认。
- Manifest 生命周期、供应链校验、三 Runtime 注入及失败门控验收通过。

### Phase 2B Gate

- MCP 集成实测通过，Jev 建议与 LoopX 最终执行职责明确。
- off/shadow/enforce、候选校验、预算、fallback、审计与缓存验收通过。
- shadow 保留集评测达到项目预先确定的质量门槛，才可开启 enforce。

### 完整 DoD

必须交付：实现变更、配置与协议 schema、迁移/兼容说明、自动化测试、真实环境集成记录、可复现示例、故障与恢复手册、证据字段说明、回滚方案及开放问题清单。任何 Gate 未通过只能标注阶段完成，不得宣称完整功能已交付。

## 15. 待决事项与发布阻塞

| ID | 待确认事项 | 建议方向 | 阻塞范围 |
|---|---|---|---|
| D-01 | GES 指向哪个项目/服务、哪个版本、由谁维护？ | 提供仓库或接口、Manifest schema 与安装安全契约 | Phase 2A |
| D-02 | Jev MCP 是现有服务还是要新建包装层？ | 提供服务来源、transport、工具 schema、认证与许可 | Phase 2B |
| D-03 | 首批部署平台及 CLI 版本是什么？ | 固定 OS/CLI/运行环境矩阵，先验证有限组合 | 各 Runtime 真集成 |
| D-04 | 各 Runtime 账号可用模型与实际计费是什么？ | 使用账号目录与真实费率，不写泛化供应商名称 | 模型选择与成本评测 |
| D-05 | Runtime 路由由明确 Profile 还是也让 Jev 选择？ | Phase 1 明确 Profile；Phase 2B 默认 Jev 仅在 Runtime 可用模型中建议 | 自动 Runtime 路由如需增加 |
| D-06 | 哪些任务/技能允许下载、shell、联网、提交？ | 用既有权限模型逐项授权，不默认为无限制 | 安全准入与 GES 安装 |
| D-07 | Handoff 是人工触发还是自动失败后切换？ | 首版显式触发；自动切换另设次数、预算与状态条件 | 自动交接 |
| D-08 | Profile 名称与现有 execution_profile 如何兼容？ | 新配置边界清晰命名，不改变旧字段含义 | 配置 schema 冻结 |

待决项并非全部阻塞 Phase 1；应按阶段分别推进。本文提出的是建议默认，不替代产品负责人确认。

## 16. 原 PRD 需求追踪

| 原稿主题 | 本版对应 | 处理 |
|---|---|---|
| 背景、产品边界与 SOT（2–4） | 2–5 | 保留目标，纠正已有能力与状态主权 |
| Runtime Framework / Interface（5–6） | FR-01/02 | 要求统一契约，避免重复框架与强制目录 |
| DSH/Cursor/Codex（7–9） | FR-03/04/05 | DSH兼容、Cursor补齐、Codex复用 |
| Runtime Profile（10） | FR-06/07 | 补版本、优先级、实际模型校验 |
| GES / Manifest / Context（11–13） | FR-08/09/10 | 补外部依赖、信任和注入证据 |
| Jev MCP / Model Policy / Router（14–17） | FR-07/11/12 | 保留集成目标，纠正执行职责并补失败策略 |
| 状态与错误（18–19） | 8、FR-13 | 复用源码状态，补取消、恢复与交接 |
| AC、测试、证据、Release（20–23） | 11–14 | 转为可执行标准，移除无证据 PASS |
| Hatchet 与 DoD（24–25） | 4、14 | Hatchet 后续；阶段与完整完成分开 |

## 17. 源码核查附录

以下链接统一固定到本次 SHA。附录用于说明现状，不表示对应测试已执行通过。

| 编号 | 核查点 | 固定源码位置 | 对本 PRD 的影响 |
|---|---|---|---|
| B-01 | Worker Catalog 与准入 | [loopx/control_plane/worker_catalog.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/control_plane/worker_catalog.py#L42-L104) | 内建 DSH endpoint；保留 worker/chat 分离和 fail-closed 准入；注册展示不代表已通过 live probe。 |
| B-02 | 实际可执行 Host | [loopx/cli_commands/turn_registration.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/cli_commands/turn_registration.py#L18-L21) | run-once 为 codex-cli/dsh/generic-cli；planning 中的 claude-code 不能当作已具备同等执行入口。 |
| B-03 | 硬编码 DSH 启动 | [loopx/dsh_goal_mode/governed_turn_loop.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/dsh_goal_mode/governed_turn_loop.py#L483-L506) | 循环构造 --host dsh / --dsh-runner；多 Runtime 不能只增加 catalog 条目。 |
| B-04 | 现有 Codex 调用 | [loopx/cli_commands/turn_run_once.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/cli_commands/turn_run_once.py#L766-L821) | 已有模型、effort、sandbox、MCP 及 session 绑定，不从零重写。 |
| B-05 | DSH CLI 控制缺口 | [loopx/dsh_goal_mode/dsh_cli_runner.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/dsh_goal_mode/dsh_cli_runner.py#L234-L261) | model/provider/effort/max_tokens 记为 unsupported_argv_controls；不能仅据选择记录宣称已应用。 |
| B-06 | DSH SDK 配置 | [loopx/dsh_goal_mode/turn_host_adapter.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/dsh_goal_mode/turn_host_adapter.py#L93-L125) | 模型/effort 有配置接线；不等于本轮已验证真实 provider 生效。 |
| B-07 | Codex 模型参数 | [loopx/control_plane/turn_driver/codex_cli.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/control_plane/turn_driver/codex_cli.py#L600-L641) | 模型和 effort 进入 argv；实际观测仍需区分。 |
| B-08 | 现有模型解析 owner | [loopx/control_plane/turn_driver/execution_profile.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/control_plane/turn_driver/execution_profile.py#L82-L162) | 显式、环境与默认值优先级及来源记录已存在，新增策略需映射到此。 |
| B-09 | Todo 状态 | [loopx/control_plane/todos/contract.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/control_plane/todos/contract.py#L128-L139) | open/done/blocked/deferred，原稿线性状态不适用。 |
| B-10 | Turn 状态及恢复 | [loopx/control_plane/turn_driver/turn_journal.ts](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/control_plane/turn_driver/turn_journal.ts#L409-L525) | 按阶段恢复，已结束返回既有结果，不一致阻断；不能泛化为重跑模型。 |
| B-11 | 结算依赖 | [loopx/control_plane/turn_driver/transaction.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/control_plane/turn_driver/transaction.py#L130-L185) | validation、durable writeback、quota spend 等已有回执依赖。 |
| B-12 | 严格结果契约 | [loopx/control_plane/turn_driver/executor.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/control_plane/turn_driver/executor.py#L90-L122) | 新增证据须版本化扩展，不能随意给 strict host result 添加字段。 |
| B-13 | 只读运行投影 | [loopx/control_plane/worker_run_readback.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/control_plane/worker_run_readback.py#L1-L20) | readback 非权威证据；不公开 session、本地路径及原始工具输出。 |
| B-14 | Goal 创建入口 | [apps/presentation/dashboard/src/features/personal-workspace/goal-create-request.ts](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/apps/presentation/dashboard/src/features/personal-workspace/goal-create-request.ts#L25-L49) | Worker/Chat 路由互斥，默认 DSH；模型字段尚未贯通。 |
| B-15 | Cursor 既有 Host 表面 | [tests/test_gemini_cursor_host_surfaces.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/tests/test_gemini_cursor_host_surfaces.py#L31-L130) | 已有表面集成测试，不能等同 governed Cursor Coding Worker 完成。 |
| B-16 | SchedulerRuntimeProfile | [loopx/control_plane/scheduler/execution_context.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/control_plane/scheduler/execution_context.py#L14-L51) | 既有调度运行语境 profile 与新产品 Profile 不是同一概念。 |
| B-17 | 技能目标 surface | [loopx/capabilities/project_skill_delivery/core.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/capabilities/project_skill_delivery/core.py#L14-L26) | 已有 surface 列表不包含 DSH/Cursor；须分别验证注入路径。 |
| B-18 | 技能受控安装 | [loopx/capabilities/project_skill_delivery/core.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/capabilities/project_skill_delivery/core.py#L311-L396) | preview、拒绝覆盖用户修改、staged copy 和 digest 校验可复用。 |
| B-19 | 现有技能 readback | [loopx/skill_install_readback.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/skill_install_readback.py#L417-L475) | 安装回执与实际消费证据需分别处理。 |
| B-20 | 上下文证据装配 | [loopx/capabilities/decision_context/assembler.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/capabilities/decision_context/assembler.py#L586-L610) | 已有决策证据装配，不自动等同新 Execution Package。 |
| B-21 | Jev observer 配置 | [packages/loopx-jev/src/loopx_jev/config.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/packages/loopx-jev/src/loopx_jev/config.py#L36-L81) | 现有 off/shadow、pinned model 与独立 egress；用途是 progress_review。 |
| B-22 | Jev HTTP transport | [packages/loopx-jev/src/loopx_jev/transport.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/packages/loopx-jev/src/loopx_jev/transport.py#L1-L43) | 现有独立 HTTP worker，不是本需求中的 MCP 模型执行器。 |
| B-23 | Core progress_review | [loopx/capabilities/progress_review/README.md](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/capabilities/progress_review/README.md#L5-L51) | 另有受约束 assist 触发现有 replan obligation，不能混为新增路由模式。 |
| B-24 | 已有 provider routing 扩展 | [packages/loopx-codex-provider-routing/CONTRACT.md](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/packages/loopx-codex-provider-routing/CONTRACT.md#L27-L94) | 已有确定性资格/路由契约，未授权其直接改变执行和任务状态。 |
| B-25 | 实验 planner-worker | [loopx/experiments/planner_worker/runtime.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/experiments/planner_worker/runtime.py#L211-L238) | 已有 model_routes；是实验有界路径，不是默认生产调度器。 |
| B-26 | 既有 MCP 控制面 | [loopx/goal_mode_mcp.py](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/loopx/goal_mode_mcp.py#L297-L329) | 已有 FastMCP 工具，不代表 Jev MCP 模型路由契约已存在。 |

### 17.1 搜索范围与负面结论限制

本轮读取固定 SHA 的完整路径树（6572 条，未截断），并针对上述控制面、执行、模型、技能、MCP 与 Jev 文件逐项静态核查。路径盘点未发现独立 GES / `.ges` 实现，已读核心文件也未建立原稿所述 GES provider 契约；未全量阅读所有 blob，亦未得到外部 GES 仓库，故结论是“外部契约未提供、已核查范围未确认”，不是断言任何地方都不存在。

TypeSafe 官方文档索引、API、coding-agents 与 intent-routing 本次未确认可直接采用的 Jev MCP 服务契约。MCP 服务来源与版本仍属待决项。

### 17.2 可复用测试定义（本轮未运行）

- [准入、默认关闭及伪启动器测试](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/tests/test_worker_catalog.py#L130-L276)。
- [未支持模型控制、session、NDJSON 和错误路径](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/tests/test_dsh_cli_runner.py#L159-L304)。
- [模型/effort 与执行绑定](https://github.com/loudon84/loopx/blob/1224cbcc9e02e68593e9eea2a40b6f301d410d5c/tests/test_turn_managed_executor_binding.py#L418-L538)。


## 18. 外部接口资料与适用限制

- [Cursor 参数参考](https://cursor.com/docs/cli/reference/parameters)：当前命令、模型与 workspace 参数。必须以目标机器安装版本验证。
- [Cursor 输出格式](https://cursor.com/docs/cli/reference/output-format)：事件协议与结果解析；运行退出及 LoopX 独立验收仍需额外检查。
- [Cursor 权限](https://cursor.com/docs/cli/reference/permissions)：不将 CLI 默认行为当作 LoopX 权限保证。
- [TypeSafe 与 coding agents 的职责](https://docs.typesafe.ai/introduction/coding-agents)：Jev 不替代 coding agent 的生成模型。
- [TypeSafe API](https://docs.typesafe.ai/api)：结构化决策 API；不代表现成 MCP 模型执行服务。
- [TypeSafe confidence](https://docs.typesafe.ai/confidence)：置信信息的用途；不等于任务正确率。

外部文档核对日期为 2026-10-14；升级后需重新运行兼容性与权限测试。本文未验证实际账号模型目录、GES 服务、Jev MCP 服务或真实 Runtime 执行。
