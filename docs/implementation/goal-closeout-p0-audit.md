# LoopX Goal Closeout v1.0 — P0 代码基线与写入口审计

| 项 | 值 |
|---|---|
| 分支 | `feature/dsh-coding-worker` |
| 基线 HEAD | `1224cbcc9`（`docs: record the no-follow-up / refresh-state ordering deadlock and its exit`） |
| 工作区 | 干净（`git status --porcelain` 为空） |
| 审计日期 | 2026-10-11 |
| 依据 | 《PRD｜LoopX Goal Closeout v1.0》§8/§9/§11/§14 |
| 本文件性质 | P0 门禁交付物。**不代表任何功能已实现**；下文所有“EXISTS/MISSING”与行号均为本次对 `/data/loopx` 的实际检索结果 |

> PRD §8 明确要求：具体 Effect Runtime 注册表和 Goal 注册写入点必须本地 `rg` 复核，不可凭文件名写入。本审计即该复核结果。

---

## 1. 架构事实（实测）

| 项 | 实测结果 |
|---|---|
| Effect RPC 注册表 | `loopx/control_plane/effect_runtime_handlers.ts`，表项形如 `["goal.acceptance.inspect", lazyHandler(() => import("./goals/acceptance_authority.ts"), ({inspectLocalGoalAcceptance}) => inspectLocalGoalAcceptance)]`，分布在 325–373 行 |
| 既有 `goal.*` RPC | 22 个方法；其中 `goal.acceptance.{inspect,configure,verify.commit,lifecycle.transition}` 4 个已存在 |
| Python facade 范式 | `loopx/control_plane/goals/acceptance.py`：模块级 `_INSPECT_METHOD`；`_routing()` 解析注册 Goal + `effective_runtime_root()` + `local_authority_is_promoted()` 前置；`_result()` 校验返回 `status ∈ {loaded,applied,recovered,replayed,planned,no_change}`，否则抛 `reason_code` |
| Canonical head 形态 | **每个 Goal 一个 authority 库文件**（`~/.loopx/authority/sqlite-v0/authority-<sha>.sqlite`）；head 为 **per-goal 对象**，顶层直接含 `goal_id`、`goal_acceptance`、`goal_acceptance_lifecycle`（`acceptance_contract.ts:320-345`） |
| 原子提交机制 | `CoordinationCommandReceipt` + `command.receipt.commit(store, {operation_id, expected_provider_revision, next_projection})`，CAS + 幂等 receipt（`acceptance_authority.ts:131-140`）；dry-run 走 `status:"planned"` 分支不入库 |
| head 键白名单 | 未在 head 顶层检索到 goal 级键白名单；各对象用自身精确键校验（如 `acceptanceKeys(state, [...])`）。因此 `goal_closeout` 必须自带同风格严格 codec + 单一写入 RPC |
| 生成物 | `coordination_state_contract.generated.ts` 首行 `// Generated from coordination_state_contract_v0.json; do not edit.` → 若需新增 wire schema 常量，须改源 JSON 后重新生成，不得手改生成文件 |

---

## 2. 复用面 vs 新增面（PRD §8 逐项复核）

| PRD 清单文件 | 实测 | 字节 |
|---|---|---|
| `loopx/control_plane/goals/closeout_contract.ts` | **MISSING（新增）** | — |
| `loopx/control_plane/goals/closeout_authority.ts` | **MISSING（新增）** | — |
| `loopx/control_plane/effect_runtime_handlers.ts` | EXISTS（改：注册 4 行） | 62574 |
| `loopx/control_plane/coordination/local_authority_provider.ts` | EXISTS（复用/最小调整） | 17350 |
| `loopx/control_plane/goals/acceptance_authority.ts` | EXISTS（复用） | 26608 |
| `loopx/control_plane/goals/acceptance_contract.ts` | EXISTS（复用/扩展） | 33040 |
| `loopx/control_plane/goals/activation_service.py` | EXISTS（改：completed 禁止 resume/stop 产生执行资格） | 19670 |
| `loopx/control_plane/goals/closeout.py` | **MISSING（新增 Python facade）** | — |
| `loopx/cli_commands/goal_closeout.py` | **MISSING（新增 CLI）** | — |
| `loopx/cli_commands/goal_lifecycle.py` | EXISTS（改：区分 stop/resume 与 completed） | 2875 |
| `loopx/dsh_goal_mode/governed_turn_loop.py` | EXISTS（改：终态启动检查/停止） | 29219 |
| `loopx/cli_commands/turn_run_once.py` | EXISTS（改：关闭后 Fail Closed） | 38906 |
| `loopx/control_plane/status/collection.py` | EXISTS（改：独立终态投影） | 15372 |
| `loopx/chat_actions.py` / `loopx/chat_server.py` | EXISTS（改） | 80214 / 80352 |
| `apps/presentation/dashboard/src/...`（chat.ts/status.ts/dashboard-page.tsx/goal-overview.tsx/personal-workspace-model.ts/goal-create-request.ts） | EXISTS（改） | 92196 / 52502 / 4327 |

---

## 3. AC-01 基础：Goal 创建/注册写入口清单（实测）

| # | 入口 | 实际调用链 | 统一化落点 |
|---|---|---|---|
| 1 | LoopX Web `goal.create` | `loopx/chat_actions.py`（`"goal.create"` 出现于 45/598/611/649/742/1223/1456/1487） | 服务端 typed action 内调用统一策略层 |
| 2 | CLI `project register` | `loopx/cli_commands/project.py:31-42` → `loopx/control_plane/projects/registry.py:138 register_project_goal()` | `register_project_goal()` |
| 3 | CLI `start-goal --guided` | `loopx/cli_commands/start_goal.py`（+ `loopx/bootstrap_command_pack.py`） | 经 registry 注册路径 |
| 4 | CLI `bootstrap` | `loopx/bootstrap.py:294`（`action = "appended"`） | 经 registry 注册路径 |
| 5 | 其他 registry 写入者 | `loopx/cli_commands/goal_actions.py`、`goal_lifecycle.py`、`goal_amendment_proposal.py`、`goal_channel_operation.py` | 只做状态/通道变更，不得创建 Goal |

**统一层结论**：`register_project_goal()` 是唯一应当初始化 Source Goal Instance 与 closeout 策略的写入点；Web `goal.create` 与 `start-goal`/`bootstrap` 必须经由它（或与其等价的单一入口）。P1 需用 hook test 固化“任何创建路径都得到 `closeout_policy=strict_v1`”，并在 P4 补“直接写 source registry”的负向用例（AC-20）。

---

## 4. AC-09 基础：终态硬门禁点清单（实测）

| 门禁点 | 文件 | 锚点 |
|---|---|---|
| Quota 决策 | `loopx/quota.py`、`loopx/cli_commands/quota_context.py` | `should-run` 决策构造；`quota.py:813` 已处理 `stale_latest_run_warning` |
| Turn 执行 | `loopx/cli_commands/turn_run_once.py:67 execute_turn_run_once()` | 执行前准入 + `execute=` 分支（866–883） |
| Host Loop | `loopx/dsh_goal_mode/governed_turn_loop.py:348 run_host_loop()` | `:403` 取 `should_run`、`:418` `should_run is not True` 即停止 |
| Todo 写 | `loopx/control_plane/coordination/todo_claim.ts` / `todo_update.ts` / `todo_terminal_lifecycle.ts` | claim / update / terminal 三条写入路径 |
| Registry 写 | `loopx/control_plane/projects/registry.py` | 源注册表写入 |

> 结论：终态拒绝必须在 **Provider 层**（TS canonical authority）实现，Python 侧门禁只是纵深防御，否则旧版 Host 可绕过（PRD §5 不变量）。

---

## 5. AC-10 基础：投影与状态改造点（实测）

| 点 | 文件 | 锚点 |
|---|---|---|
| stale 快照告警 | `loopx/review_packet.py:211`；`loopx/quota.py:813`；`loopx/goal_portfolio.py:246-247` | `stale_latest_run_warning` 的产生与聚合 |
| Web 状态数据层 | `apps/presentation/dashboard/src/data/status.ts` | Closeout 投影 schema |
| Web 主视图 | `apps/presentation/dashboard/src/views/dashboard-page.tsx` | Canonical completed 需优先于历史 Run |
| Web 历史泳道 | `apps/presentation/dashboard/src/features/personal-workspace/personal-workspace-model.ts` | PRD 指出 `workspaceHomeLaneForGoal` 把 `activation_state=stopped` 判定置于 `goal.state=已完成` 之前 → P3 需实测该函数并调整优先级 |
| Goal 概览 | `apps/presentation/dashboard/src/features/personal-workspace/goal-overview.tsx` | 结束目标区域 |

（`workspaceHomeLaneForGoal` 的确切位置与调用点留待 P3 实测定位，本审计不做文件名假设。）

---

## 6. 既有同名概念必须区分（防止语义混用）

| 概念 | 现状位置 | 与 Goal Closeout 的关系 |
|---|---|---|
| `terminal_closeout` | `turn_run_once.py:455/674/882`、`host_adapter_settlement.py:32`、`todo_event.py:87` | **Turn 级结算**（把一个 Turn 结算到终态），**不是** Goal completed。实现时禁止复用该名字承载 Goal 语义，避免审计混淆 |
| `activation_state` stop/resume | `activation_service.py`、`cli_commands/goal_lifecycle.py`（仅 `stop|resume`） | 可逆暂停；`completed` 时必须拒绝二者产生新执行资格（AC-11） |
| `vision_closed` | `loopx/control_plane/goals/goal_vision_state.py`、`capabilities/periodic_report/*` | Agent Vision 生命周期，**不等于** completed（AC-18） |
| `goal.acceptance.*` | 已具备 contract/verify/CAS/receipt | **复用**，不得重造验收引擎（D04） |

---

## 7. AC 冻结表（AC-01 – AC-20）

| AC | 断言要点 | 计划测试落点 |
|---|---|---|
| AC-01 | 三种入口均得 `strict_v1`；无新增绕过接口 | `tests/control_plane/test_goal_closeout.py` + 入口 hook test |
| AC-02 | 必需 Todo 有 open → `closeout_open_required_todos` | 同上 |
| AC-03 | done 但无有效 Contract/Receipt → 拒绝 | 同上 |
| AC-04 | Validator 非零 / Pin 变化 / Digest 过期分别阻断 | 同上 + `tests/control_plane_ts/` |
| AC-05 | Owner 前不可完成；Agent/Hermes confirm → `closeout_owner_required/mismatch` | 同上 |
| AC-06 | 基线变化后旧 `basis_digest` → `closeout_basis_stale` | 同上 |
| AC-07 | 并发 confirm 至多一个写入；同 operation_id 幂等回放原 Receipt | `tests/control_plane_ts/` |
| AC-08 | completed + 签字 + Evidence Receipt 同事务提交，异常无半完成 | 同上 |
| AC-09 | 完成后 `quota should-run=false`；旧 Host / claim / todo create / writeback 被 Provider 拒 | Python + TS 双向 |
| AC-10 | 完成后 Web 显示已完成并入历史；历史失败 Run 不回退为“需修复” | Web 组件测试 + 投影测试 |
| AC-11 | `goal-lifecycle resume` 不能恢复 completed；stop 不制造 completed | `tests/control_plane/` |
| AC-12 | acceptance disabled 的旧 Goal 必须显式补建 + 真实验证 + Owner 确认；不得回填伪历史 | `tests/control_plane/` |
| AC-13 | 两批同 Phase 任务不按标题自动去重；历史证据可查且引用原 Todo ID | 同上 |
| AC-14 | Preview 与 Commit 对齐同 Provider Revision/Goal Instance/Task Set；断线可恢复 | `tests/control_plane_ts/` |
| AC-15 | Global Registry 失败后 Canonical completed 仍生效，投影幂等补偿 | 同上 |
| AC-16 | 未结算 Turn / 阻塞 Gate / 摘要高严重度不一致 → 禁止 Closeout | 同上 |
| AC-17 | `continuous_monitor` 必须先停用/移交并有证据 | `tests/control_plane/` |
| AC-18 | `vision_closed` 不自动置 `goal_closeout.state=completed` | 同上 |
| AC-19 | 真实 GES Goal 补录只引用当前工程验证结果；历史 Run/Todo/快照零篡改 | P4 迁移测试 + 只读审计 |
| AC-20 | 前后端绕过新 API、旧 Agent 走旧写入路径的负向回归 | 负向测试套件 |

> PRD §10 要求：不以 pytest 退出码 0 代替行为断言；每条 AC 需独立可复现结果与审计 ID。

---

## 8. 分阶段落地计划（文件级）

| Phase | 交付 | 关键文件 |
|---|---|---|
| **P0**（本文件） | 代码路径审计、写入口清单、AC 冻结 | `docs/implementation/goal-closeout-p0-audit.md` |
| **P1** | Closeout Contract + Prepare/Confirm 原子服务 + 接口与负向测试 | 新增 `control_plane/goals/closeout_contract.ts`、`closeout_authority.ts`；改 `effect_runtime_handlers.ts`（注册 `goal.closeout.{inspect,prepare,confirm,history}`）；复用 `acceptance_authority.ts`/`acceptance_contract.ts`；测试 `tests/control_plane_ts/goal_closeout*.test.ts` |
| **P2** | CLI + Hermes 接入 + Worker/Quota/Turn/Writer 硬门禁 | 新增 `control_plane/goals/closeout.py`、`cli_commands/goal_closeout.py`；改 `cli_commands/goal_lifecycle.py`、`turn_run_once.py`、`dsh_goal_mode/governed_turn_loop.py`、`quota.py`、TS todo 写路径 |
| **P3** | Web Owner 确认 + 历史投影 + 可观测 | 改 `chat_actions.py`、`chat_server.py`、`data/chat.ts`、`data/status.ts`、`views/dashboard-page.tsx`、`features/personal-workspace/{goal-overview.tsx,personal-workspace-model.ts,goal-create-request.ts}` |
| **P4** | 老 Goal 迁移（含真实 GES Goal）+ 文档 + 回归 | 迁移脚本/命令 + `docs/integrations/` 文档 + AC-12/13/17/19/20 用例 |
| **P5** | Canary → 正式 Release → Web 重启验收 | `scripts/install-local.sh` 受控安装、记录新 Release ID、重启 dashboard/proxy、回归探测 |

---

## 9. 风险与待澄清（进入 P1 前必须收敛）

| # | 风险/待定 | 处理规则 |
|---|---|---|
| R1 | head 顶层无 goal 级键白名单 | 新对象必须自带严格 codec + 唯一写入 RPC；P1 首个 PR 用针对性测试固化“未版本化字段被拒” |
| R2 | `coordination_state_contract.generated.ts` 为生成物 | 如需新 wire 常量，改源 `coordination_state_contract_v0.json` 后重新生成；严禁手改生成文件 |
| R3 | `terminal_closeout` 与 Goal Closeout 同名 | 代码与 API 层面显式命名区分；审计文档标注，避免后续误用 |
| R4 | Local CLI 无认证能力 | `confirm` 只能发起授权请求，最终由 Web Owner 登录审批或受信 OS/Service 策略完成（PRD §6.1）；P2 需给出 owner principal 的落地方案 |
| R5 | Owner Principal 与 agent_id 的映射 | `owner_principal_id ≠ agent_id`；P1 需定义其来源与校验，禁止用 registered_agent 冒充 |
| R6 | 迁移不得篡改历史 | AC-19：只能引用当前工程验证结果，禁止回填历史验证；历史 Todo/Receipt 只读 |
| R7 | P5 发布不得改已安装 release | 按 `scripts/install-local.sh` 受控安装；记录新 Release ID；已完成的 Closeout Receipt 不可被软件回滚清除 |

---

## 10. 下一步

P1：实现 `closeout_contract.ts` / `closeout_authority.ts` 并注册 4 个 RPC，落 AC-02..AC-09、AC-14..AC-16 的测试，再进 P2。
