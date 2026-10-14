# DSH Coding Worker — Phase 1 测试报告与证据

> 状态：Phase 1 完成，Gate P1 PASS
> 日期：2026-10-10
> 分支：`feature/dsh-coding-worker`（base `2643d4ff7`）
> 配套：[`dsh-coding-worker-phase0.md`](./dsh-coding-worker-phase0.md)、[`dsh-coding-worker-architecture.md`](./dsh-coding-worker-architecture.md)、[`dsh-coding-worker-file-change-list.md`](./dsh-coding-worker-file-change-list.md)

---

## 1. Phase 1 交付内容

在 `loopx/dsh_goal_mode/` 新增 DSH 0.2 CLI host 适配层，复用 LoopX 既有 governed-turn 契约，不新建调度面：

| 文件 | 职责 |
|---|---|
| `dsh_cli_ndjson.py` | NDJSON 编解码：事件判别、`turn_end.reason`、`callId` 配对、usage 累加、结构化解密、SDK 形状投影 |
| `dsh_cli_process.py` | 受控子进程：argv 数组、stdin 任务、独立进程组、wall/idle 双超时、取消、有界捕获、可对账 pid |
| `dsh_cli_probe.py` | 运行时探测与能力文档（含 **`DSH_HOME` 可写性**、版本线、headless profile、工具 composition、凭据可达） |
| `dsh_cli_runner.py` | `run_dsh_turn` 适配器：实现既有 `load_dsh_runner` 接缝，使 `loopx turn run-once --host dsh --dsh-runner <path>` 驱动 npm CLI 而非 Python SDK；另含独立 stdin/stdout `main()` |

新增测试：`tests/test_dsh_cli_ndjson.py`、`tests/test_dsh_cli_runner.py`、`tests/test_dsh_cli_probe.py`、`tests/dsh_cli_fake_launcher.py`（hermetic，无模型、无网络）。

**未修改任何既有文件**，因此 `deepseek_harness_segment` 的 `tool_calls=false` / `read_only` 与 `_agent_eligibility()` 语义保持原样。

---

## 2. 自动化测试结果

| 命令 | 结果 |
|---|---|
| `env -u DSH_HOME python3 -m pytest tests/test_dsh_cli_ndjson.py tests/test_dsh_cli_runner.py tests/test_dsh_cli_probe.py -q` | **54 passed** |
| `env -u DSH_HOME python3 -m pytest tests/test_dsh_goal_mode.py tests/test_chat_dsh_adapter.py tests/test_loopx_turn_driver.py -q` | **211 passed** |

覆盖的关键行为：

- **契约级**：LoopX 自己的 `turn_host_adapter.run_dsh_host` 把本模块当作 `dsh_runner` 驱动，走**真实签名 TurnEnvelope**，产出合法 `loopx_turn_result_v0`；非 in-turn 失败经 `classify_dsh_terminal_reason` 映射为 `rate_limited`；未签名请求被拒为 `contract_rejected`。
- **完成判据**：`exit != 0`、缺 `turn_end`、`turn_end.reason.kind != completed`、非 JSON 输出、stdout 截断——**全部 fail closed**，不会被当作成功。
- **边界**：prompt 只走 stdin（argv 中不出现）；argv 只使用实测存在的 `--profile/--json/--session-id/--patch/-`；工作目录不一致被拒；Session 恢复正路径需存在且匹配，缺失时 fail closed。
- **进程**：取消可终止整个进程组且无僵尸；捕获上限触发 `stdout_truncated`。
- **探测**：只读 `DSH_HOME`、版本离线、缺二进制、缺凭据均正确阻塞 `ready`；未实测能力保持 `probe_required`。

### 2.1 环境噪声说明（非缺陷）

本会话 shell 带有 `DSH_HOME=/home/cowork/.dsh`，而 `resolve_dsh_home` 会读取进程 `DSH_HOME`。若直接运行，`tests/test_dsh_goal_mode.py` 与 `tests/test_chat_dsh_adapter.py` 中两个断言 workspace 局部默认 home 的用例会失败（2 failed）。`env -u DSH_HOME` 后 211 全绿。这是**既有测试对宿主 `DSH_HOME` 的敏感性**，与本次改动无关；对实现的含义是：Coding Worker 的 `DSH_HOME` 必须显式固定，不能依赖环境泄漏。

---

## 3. 真实受控 Turn 证据（Gate P1）

夹具：`/tmp/loopx-dsh-coding-worker-phase1-goal`（独立 git 仓库，**非**用户生产仓库）
Goal：`dsh-coding-worker-phase1-goal`｜Agent：`dsh-phase1-worker-01`｜Todo：`todo_86f0afd515d9614173988383`

命令：

```bash
loopx turn run-once --goal-id dsh-coding-worker-phase1-goal --agent-id dsh-phase1-worker-01 \
  --host dsh --project . --todo-id todo_86f0afd515d9614173988383 --turn-instance-id turn-p1-… \
  --dsh-runner /data/loopx/loopx/dsh_goal_mode/dsh_cli_runner.py \
  --validation-command-json '["python3","-c","…assert hello(\"LoopX\")==\"Hello, LoopX\"…"]' \
  --execute
```

### 3.1 执行结果

| 事实 | 证据 |
|---|---|
| 真实 DSH 0.2 子进程执行 | `host_invoked: True`；产出文件时间戳与内容 |
| 真实文件改动 | `src/hello.py` 由不存在 → 存在，`sha256 f37ba66ea3a0717b7533938e4910eb4863e6070ef90c6fbc82fba1207988fdd2` |
| 内容正确 | `def hello(name): return f"Hello, {name}"` |
| 独立 Validator 实际运行并**通过** | `validation: passed`（验证器为 argv 形式，非模型自述） |
| 终局提交 | `status: committed`；`commit_eligibility: {writeback: true, quota_spend: true, scheduler_ack: true}` |
| 完整阶段序列 | `[host_execute, typed_result, validation, durable_writeback, quota_spend, scheduler_apply, scheduler_ack]` |

### 3.2 同一 Turn 的崩溃恢复（FR-14 / AC-14，附带强证据）

写回阶段最初被 LoopX 阻断：

```
error: accountable refresh is blocked until controller-declared completion
       validation durably completes todo todo_86f0afd515d9614173988383
recorded_effects: {host_invoked: true, state_written: null, quota_spent: false}
recovery_from: durable_writeback   recovery_reinvoke_host: False
```

`loopx turn inspect-journal` 的裁定：

```json
{"decision":"replay_blocked","journal_status":"in_progress",
 "completed_phases":["host_execute","typed_result","validation"],
 "violations":["journal_not_terminal"],
 "recovery_decision":{"action":"continue","can_continue":true,
   "resume_from":"durable_writeback","reinvoke_host":false,
   "reason":"resolve_prepared_effect"}}
```

在 Todo 的**控制器声明式完成验证**durably 落库后，以 `--resume-turn-key` 结算：

```
status: committed   validation: passed   host_invoked: False
state_written: True quota_spent: True
recovery_reason: resolve_prepared_effect   recovery_actual: finished
```

**这正是 PRD §4.4 要求的行为**：验证通过与持久化提交之间中断时，用**已存在的 Turn 身份**重试**结算**，而**不重跑有副作用的 DSH 进程**（`host_invoked: False`），且不重复计费。此路径不是构造的，是真实发生的。

### 3.3 真实 Turn 暴露并修复的两个缺陷

用真实执行（而非 hermetic fake）才发现，均已修复并有回归测试：

1. **`load_dsh_runner` 按路径加载时模块不可导入**：LoopX 实际执行的是安装快照（`~/.local/share/loopx/releases/…`），在 runner 内做绝对 `loopx.*` 导入会 `ModuleNotFoundError`，被记成内容为空的 `unknown` host failure，Turn 在拉起 `dsh` 之前就死掉。修复：runner 从其**自身目录**按路径加载同级模块。回归：`test_runner_loads_by_path_from_a_foreign_installation`。
2. **`retained_events` 对序列化后的 JSON 做正则+截断**：单事件序列化超过 2000 字符即被截断，`json.loads` 失败，同一 `unknown` 结局。修复：改为**结构化解密**（逐字符串处理，保持文档合法）。回归：`test_retained_events_survive_large_payloads`、`test_redaction_bounds_each_string_independently`。

---

## 4. AC 对照（Phase 1 范围）

| AC | 状态 | 证据 |
|---|---|---|
| AC-01 DSH 版本/CLI 探测 | ✅ | phase0 §2；`probe` 版本线与 `--json/--session-id` 检查 |
| AC-02 Worker 能力可查询且不伪造 | ✅ | `probe_dsh_cli()` 返回 `loopx_dsh_coding_worker_capability_v1`；未实测项保持 `probe_required` |
| AC-03 Steward 回归 | ✅（代码层） | 未改 `chat_endpoint_catalog.py`；211 项回归通过。运行期 Steward `available:false` 见 architecture §8.1 |
| AC-06 Turn-1 真实执行 | ✅ | §3.1：真实进程、真实文件、sha256、阶段序列 |
| AC-07 独立验证 | ✅ | §3.1 `validation: passed`；验证器为 argv 独立进程 |
| AC-08 写回幂等 | 🟡 部分 | §3.2 同一 Turn 身份结算且 `host_invoked: False`；显式重放/重复计费对拍留待 Phase 3 |
| AC-11 失败闭合 | ✅ | 54 项测试覆盖 exit≠0 / 缺 final / 缺 turn_end / 非法 NDJSON / 截断 |
| AC-12 安全路径 | 🟡 部分 | 无 shell 拼接、prompt 走 stdin、工作目录不一致被拒、越界 Session 复用被拒；越界写与危险命令拒绝待 Phase 2/3 |
| AC-13 回归 | ✅ | 211 passed（`env -u DSH_HOME`） |
| AC-14 暂停/重启对账 | ✅ | §3.2 unknown-effect fence 真实触发并正确结算 |
| AC-04/05/09/10/15/16/17/18 | ⬜ 未开始 | Phase 2/3 |

---

## 5. 已实测的能力边界（不得越级声明）

1. **argv 无法表达模型/推理强度/输出上限**。`dsh --profile headless` 只接受 `--json`、`--session-id`、任务文本（实测 `--help`）。LoopX 仍按既有 runner 契约传入 `provider/model/reasoning_effort/max_tokens`，适配器把它们记入 `unsupported_argv_controls` 而非假装生效；**按 Turn 固定模型需要改 profile/`--patch`，属部署决策**。相应地，LoopX 的 `output_token_limit: 16384` 在 CLI 路线上**未被真正施加**，不得对外宣称已强制。
2. **`DSH_HOME` 必须可写**，否则每次引导在重写 `cordis.yml` 时 EROFS 失败——已作为探测的必需检查。
3. **`--session-id` 默认不启用**（R-03：先 fresh）。仅当 `LOOPX_DSH_CLI_SESSION_MODE=resume`、Session 已持久化且 cwd/身份一致时才采用；跨进程正路径恢复仍未实测（phase0 U-01）。
4. **完成判据**恒为 `exit == 0 AND turn_end.reason.kind == "completed"`；`--json` 是投影不是日志。
5. **LoopX 的完成验证以 argv 形式启动**（非 shell）。声明 `cd … && …` 这类命令会因可执行文件是 shell 内建而 `command_not_run`。这是夹具层的真实坑，已记录。
6. **`per-Turn` 运行证据（pid/耗时/事件摘要）目前止于 runner 返回值**：LoopX 的 `normalize_runner_outcome` 只取 `final_response/finish_reason/events`，因此 pid/首事件耗时等尚未进入 Turn Journal。将其投影到只读 readback 属 Phase 2/3（FR-11/AC-15）。

---

## 6. 复现步骤

```bash
# 1) 单元 + 契约测试（keyless，无模型费用）
cd /data/loopx
env -u DSH_HOME python3 -m pytest tests/test_dsh_cli_ndjson.py \
  tests/test_dsh_cli_runner.py tests/test_dsh_cli_probe.py -q

# 2) 回归
env -u DSH_HOME python3 -m pytest tests/test_dsh_goal_mode.py \
  tests/test_chat_dsh_adapter.py tests/test_loopx_turn_driver.py -q

# 3) 真实受控 Turn（需要已批准的真实模型调用）
#    夹具与命令见 §3；LoopX 侧目标 dsh-coding-worker-phase1-goal
```

---

## 7. Phase 2：Worker 注册与 `goal.create` 双路线

### 7.1 为什么必须是新的读模型（AD-01/AD-05 的实证）

`loopx/chat_endpoints.py` 的自定义 Endpoint 注册面是**结构性受限**的，实测其校验：

| 字段 | 允许值 | 后果 |
|---|---|---|
| `adapter_kind` | 只允许 `"acp"`（`chat_endpoints.py:111-112`） | DSH Coding Worker 不是 ACP agent，无法注册 |
| `transport` | 只允许 `"stdio"` | — |
| `trust_scope` | 只允许 `"read_only"`（`:120-121`） | **无法表达 workspace_write** |

因此把 Coding Worker 塞进该注册表，只能靠放宽 `read_only` 保证——而那正是只读 Steward 赖以成立的东西。**结论：新增独立的 Governed Turn Worker Catalog，只读 Chat 路径一行不改。**

### 7.2 交付

| 文件 | 动作 |
|---|---|
| `loopx/control_plane/worker_catalog.py` | 新增：worker 描述符、workspace 授权、能力准入（`admit_worker`）、只读投影（含 `declaration_snapshot` 声明式快照） |
| `loopx/chat_action_normalization.py` | `goal.create` 增加 `executor_route` / `worker_endpoint_id`；未知枚举拒绝；路由互斥校验 |
| `loopx/chat_actions.py` | **三处** `_agent_eligibility` 调用点按路由分流（normalize、preview/eligibility、apply）；新增 `_admit_goal_worker` |
| `loopx/bootstrap.py` | `build_goal_entry` / `bootstrap_project` 增加可选 `executor_route` / `worker_endpoint_id`，仅设置时写入 goal |
| `loopx/chat_server.py` | `/api/chat/capabilities` 增加 `governed_turn_workers` 只读投影（声明式、不启动宿主、不泄漏路径） |

**关键设计**：Coding Worker Goal 的 `agent_id` 保持为空，因此既有的「首轮 Chat Session」分支（`:777`）自然跳过——执行型 Worker 不经 `DshChatAdapter`，与 AD-01 一致。

### 7.3 Gate P2 实测证据

```
$ loopx-canary bootstrap --project /tmp/loopx-dsh-coding-worker-p2 --goal-id p2-source-goal …
- ok: True

# 经 typed action preview/apply 创建 Coding Worker Goal
preview status: preview_ready        errors: none      ← 无 tool_calls 报错
apply status:   applied              outcome: goal_created
GOAL executor_route:    governed_turn_worker
GOAL worker_endpoint_id: dsh-coding-worker-local
GOAL agent_id:          None

# goal.create 同时建出 2 个 Todo
count: 2
 - todo_1f1c76989ef88f72327e81c7 | open | Add tests/test_hello.py …
 - todo_e6286f4a473c6692a8f8868e | open | Add src/hello.py …
```

Worker 只读投影（Web 读取路径）：

```json
{"worker_endpoint_id":"dsh-coding-worker-local","worker_kind":"dsh_coding_worker",
 "trust_scope":"workspace_write","tool_calls":true,"workspace_authorized":true,
 "capabilities":{"shell":"probe_required", …},
 "availability":{"ready":false,"reason":"not_probed"}}
```

**声明式快照不把「已配置」显示成「健康」**：能力一律 `probe_required`，`availability.reason="not_probed"`；权威判定由 `admit_worker` 在创建准入时以真实探测给出。投影经断言确认不包含任何本地绝对路径。

### 7.4 测试

```
15 passed   tests/test_worker_catalog.py
308 passed  聊天动作 / Gate 决策 / 团队计划 / Goal 生命周期 / chat CORS / DSH 三件套
```

覆盖：默认 Chat 路线行为不变；worker 路线**不触发** `cannot execute Goal tools`；未知 `executor_route` 拒绝；未注册 worker 拒绝；worker + `agent_id` 混用拒绝；**worker 不可用时不创建 Goal（fail closed）**；只读投影不泄漏路径。

### 7.5 Phase 2 完成度与剩余项

**已完成（含 UI 层）**

1. **Dashboard 执行器类型选择器**已接入：`goal-create-request.ts` 新增 `GoalExecutorRoute` 与 `DEFAULT_CODING_WORKER_ENDPOINT_ID`；`workspace-action-form.tsx` 在「执行权限」下方增加「执行器类型」选择（Chat Agent / DSH Coding Worker）并附不可用说明。契约测试断言：worker 路线**发出** `executor_route` + `worker_endpoint_id` 且**不含** `agent_id`；默认 Chat 路线 payload 形状**不变**。`tsc --noEmit` 通过，`personal-workspace-contract.test.mjs` 通过，主题/字体 token 契约通过。
   - 注：新增的 `var(--pw-muted)` 已在该样式表定义并使用 180 次；仓库根的 `scripts/check-css-custom-properties.mjs` 因**缺少根 `node_modules`（无 `typescript` 包）**而在 import 阶段即失败——这是既有环境缺口，与本次改动无关（未安装根依赖，亦未安装全局依赖）。

**剩余项**

2. `loopx/chat_server.py` 的 capabilities 投影为**声明式**；把真实探测结果作为独立只读健康投影（FR-11/AC-15）留待 Phase 3。
3. 首轮 Turn 的**异步下发**未接线：Goal + Todos 已创建，执行仍由 `loopx turn run-once` 显式驱动（Phase 1 已验证该路径）。

---

## 8. Phase 3：自动续跑与端到端

### 8.1 交付

`loopx/dsh_goal_mode/governed_turn_loop.py` —— **host loop**，不是调度器。LoopX 对于 generic CLI host 只提供 `quota should-run` 这一问询面，续跑由 host 侧提供。该循环每一步都是对权威 `loopx` CLI 的调用：

1. `quota should-run`（不带 todo）→ LoopX 决定是否有任何工作被准入；
2. 取 LoopX **发布**的候选 Todo（`action_portfolio.primary` / `suggested_actions`，或单候选时的 `agent_channel.primary_action`），不自行排序；
3. 带 `--todo-id` + `--turn-instance-id` 重新问询以**显式绑定**一次 Turn（`selection_policy.requires_explicit_turn_binding = true`）；
4. `turn run-once --host dsh --dsh-runner … --validation-command-json … --execute`；
5. 结算栅栏：若 Turn 处于 `durable_writeback` 未完成态，则声明完成验证并用**已存在的 Turn 身份** `--resume-turn-key` 结算，**绝不重跑已产生副作用的 host**（读取 Turn journal 定位身份，符合 PRD §4.4）；
6. 回到 1。

停止条件（全部来自 LoopX 判定）：`should_run=false`、无可用 Todo、Turn 非 committed、达到 `max_turns`。**不新增 Task DB、不新增定时器、不新增审批 Gate。**

### 8.2 端到端实测（隔离夹具）

夹具：`/tmp/loopx-dsh-coding-worker-p3`（独立 git 仓库）｜Goal `p3-coding-worker-goal`｜Agent `dsh-p3-worker-01`

| 项 | Todo 1 | Todo 2 |
|---|---|---|
| Todo | `todo_427704b9d2eb0570b25a72e8` | `todo_9e9e1a6367adc88a5b035bf9` |
| 任务 | 新增 `src/hello.py` | 新增 `tests/test_hello.py` |
| 产物 | `src/hello.py` sha256 `f37ba66e…` | `tests/test_hello.py` sha256 `faf8136e…` |
| 终局 | `committed` / `validated_progress` | `committed` / `validated_progress` |
| 验证 | `status: passed`, `exit_code: 0` | `status: passed`, `exit_code: 0` |
| 阶段 | `host_execute, typed_result, validation, durable_writeback, quota_spend, scheduler_apply, scheduler_ack` | 同左 |

- **两个 Todo 均 `done`**，两条 Turn journal 均为 `committed` 且阶段序列完整。
- **Turn 2 由 LoopX 续跑放行，两轮之间没有任何人工消息**：循环在第 1 轮结算后重新问询，LoopX 发布 Todo 2，循环随即执行第 2 轮；第 2 轮结算后再次问询返回 `no_eligible_todo`，循环干净退出。
- 我另行独立运行 `python3 -m unittest discover`（非模型自述）→ `Ran 1 test … OK`，exit 0。

### 8.3 真实执行暴露并修复的两个解析缺陷

均为「把可结算的 Turn 误判为 unknown 而停机」的静默故障，已修复并有回归测试（`tests/test_governed_turn_loop.py`，10 项）：

1. `turn run-once --format json` 的字段**嵌套层级与 markdown 摘要不同**，按扁平键读取得到 `status=unknown`。改为按名递归查找（`_deep_get` / `_deep_contains`）。
2. LoopX 在**只有一个**候选 Todo 时把 `action_portfolio.primary`/`suggested_actions` 置为 `null`，改由 `interaction_contract.agent_channel.primary_action`（`"<todo_id>: <text>"`）发布。补齐该来源后循环正确选中 Todo 2。

### 8.4 如实标注的发现与残留风险

1. **Todo 顺序是「推荐」不是「硬栅栏」**：`quota should-run` 会把 Todo 1 与 Todo 2 **同时**发布（`selection_role: recommended` / `alternative`），`selection_policy.decision_owner = agent`。LoopX 用推荐顺序 + 强制显式 Turn 绑定来表达依赖，而不是把 Todo 2 置为不可选。因此「Todo 2 必须等 Todo 1 结算」目前由 host loop 遵循推荐顺序来保证，而非由 LoopX 拒绝。**这是 AC-09 的一个语义缺口，需要评审是否要求 LoopX 侧硬门禁。**
2. **journal 残留非终态条目**：修复前的失败尝试在 `turns/` 留下一条 `in_progress` 记录（`c83199d3…`，阶段止于 `validation`）。结算会以新 Turn 身份记录 `committed`，原条目不被终结。当前 `_in_progress_turn_key` 取排序后第一个匹配，存在**选错身份**的风险；Phase 4 前应改为按 `turn_instance_id` 精确匹配。
3. **本次 E2E 跨两次循环调用完成**：第 1 轮由第一次调用完成，其后我修复了上述解析缺陷，第 2 轮由修复后的调用完成——它在**同一次调用内**完成了「问询 → 执行 → 结算 → 再问询 → `no_eligible_todo` 退出」，即自动续跑机制本身已单次验证，但「Turn 1 与 Turn 2 连续跑在同一次调用内」未直接观测到（因我的缺陷导致第 1 轮提前结束）。
4. **未做**：暂停/取消/重启对账（FR-10）、预算不足停止、Dashboard 执行详情回显（FR-11/AC-15）、Session resume 正路径（U-01）。

### 8.5 测试

```
10 passed   tests/test_governed_turn_loop.py
```


---

## 9. Phase 3 剩余项（停止语义与运行可观测）

### 9.1 host loop 的停止与续跑语义

| 能力 | 状态 | 证据 |
|---|---|---|
| **暂停（pause）** | ✅ 实测 | `goal-lifecycle --operation stop --actor-kind owner` 后，`quota should-run` 返回 `decision=skip, state=paused`；循环 `stop_kind=paused`、**`host_invocations=0`** |
| **人工 Gate 停止** | ✅ 实测 | 开放 user_gate 时 `stop_kind=gate`、**`host_invocations=0`** |
| **配额/预算停止** | ✅ 按类覆盖 | 暂停与 Gate 已实测；整个拒绝类（含 `quota_exhausted`/`exhausted`）由单测证明「任何拒绝都在构建 Turn 之前短路」。**本机未能真实构造出配额耗尽态**（`--slots 0` 不影响 `allowed_slots=1440`），故不谎称实测 |
| **取消（cancel）** | ✅ 实现+测试 | `cancel` 标志在问询前即停止；对进行中的 `loopx` 调用按**进程组** SIGTERM→SIGKILL 终止，不留 host |
| **重启对账** | ✅ 实现+测试 | 结算改按 `plan.transaction.turn_instance_id`（并与 Todo 交叉校验）精确匹配；**未知或歧义身份一律不结算**，避免把上一次尝试残留的非终态 journal 误当本次 Turn |
| **无工作可做** | ✅ 实测 | `stop_kind=no_work`（Phase 3 E2E 末态） |

循环输出新增 `stop_kind` 与 `host_invocations` 两个字段 —— **AC-10 直接由字段断言，不再靠文字推断**。

### 9.2 运行可观测（FR-11 / AC-15）

- **后端**：新增 `loopx/control_plane/worker_run_readback.py`，把已记录的 Turn journal 投影为可追踪的运行行：Turn 标签、`turn_instance_id`、Todo、记录时间、终态、`result_kind`、完成阶段、独立验证结论（`status`/`passed`/`exit_code`/`validator_kind`）、`host_failure_kind`、尝试次数。
- **接口**：`GET /api/chat/worker-runs?goal_id=…`（只读）。已对**三个真实 Turn** 实测：两个 `committed` 且七阶段齐全、验证 `passed`，一个被后续结算取代的 `in_progress` 尝试；缺失 `goal_id` 返回 typed error，未知 Goal 返回空历史而非报错。投影经断言确认不含任何本地路径、Session id 或 DSH home。
- **诚实标注**：journal **不记录墙钟时间**，因此唯一给出的时间戳是 journal 文件自身的写入时间，并明确标记 `recorded_at_source=journal_file_mtime`，不伪装成 Turn 起止时间。
- **未完成**：**Dashboard 尚未渲染这份数据**（AC-15 的 UI 层）。接口与投影已就绪并验证，UI 呈现是下一步。

### 9.3 测试

```
31 passed   tests/test_governed_turn_loop.py   （停止分类 / AC-10 / 取消 / 精确身份）
 8 passed   tests/test_worker_run_readback.py  （AC-15 投影与脱敏）
```

### 9.4 如实标注的残留

1. **AC-15 的 UI 呈现未做** —— 后端与接口已就绪。
2. **配额耗尽态未在本机真实复现** —— 按类覆盖 + 两个真实拒绝态。
3. **测试夹具遗留**：`/tmp/loopx-{ac09-probe,ac09-probe2,ac10-probe,p2,p3,...}` 及其在全局 registry 中的测试 Goal 仍在；如需清理请示意。

---

## 10. Phase 4：功能旗标、回滚与操作手册

### 10.1 功能旗标默认关闭

能力**随包发行时是暗的**。开关是环境变量 `LOOPX_DSH_CODING_WORKER`：

| 取值 | 效果 |
|---|---|
| 未设 / `0` / `false` / `off` / `no` / 空 / **任意无法识别的值** | 关闭（默认） |
| `1` / `true` / `yes` / `on` | 开启 |

无法识别的值一律按关闭处理 —— 回滚只需清掉变量，不必猜。

实测（`env -u LOOPX_DSH_CODING_WORKER`）：

```
enabled: False | resolves: None
featured workers: 0 | flag: {'name': 'LOOPX_DSH_CODING_WORKER', 'enabled': False, 'default': 'disabled'}
```

关闭时 `goal.create(executor_route=governed_turn_worker)` 返回 typed 拒绝并**指出 opt-in 变量**，且**不创建 Goal**。

### 10.2 回滚保证（已由测试固定）

- 旗标清除后，Worker 路线被拒且不创建 Goal（`test_worker_route_is_refused_while_the_capability_is_dark`）。
- 同一状态下 **Chat Agent 路线完全不受影响**（`test_chat_agent_route_still_works_while_the_capability_is_dark`）。
- 回滚的完整步骤、在途 Turn 的 `--resume-turn-key` 对账、孤儿进程检查写在 `docs/integrations/dsh-coding-worker.md` §8。

### 10.3 操作手册

新增 `docs/integrations/dsh-coding-worker.md`：安装前提、旗标、执行、可观测、排障（含 `blocked_health` = 目标被暂停、验证命令必须是 argv 形式、`bound_agent` actor 等真实踩过的项）、回滚、已知边界、验收清单。

其中 §7 把一条**真实教训**写成强制约定：**完成 Todo 必须声明后继或 `--no-follow-up`**，且只能在完成时声明。这条是我自己手工记账时踩到的坑（host loop 会自动做，手工绕过就留下 `autonomous_replan_required` 义务且事后无法补）。

### 10.4 测试

```
122 passed  新增测试全集（含 29 项 worker catalog：默认关闭 / 真值解析 / 回滚拒绝 / Chat 不受影响）
211 passed  既有回归（DSH 三件套）
```
