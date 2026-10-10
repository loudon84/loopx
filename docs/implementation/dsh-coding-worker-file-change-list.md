# DSH Coding Worker — 文件级改造清单与调用链

> 状态：Phase 0 产出，待评审
> 基线：LoopX `1.3.1`，Git base `2643d4ff79acfb5ae8730b11c329d7c6287e3262`，分支 `feature/dsh-coding-worker`
> 配套：[`dsh-coding-worker-phase0.md`](./dsh-coding-worker-phase0.md)、[`dsh-coding-worker-architecture.md`](./dsh-coding-worker-architecture.md)
> 标注：`[实测]` = 本次已读源码/执行确认；`[推断]` = 依据既有模式推断，Phase 1 需复核。

---

## 1. 现状调用链（文件:行）

### 1.1 Goal 创建（Chat 路线，即当前 B-06 报错点）

```
Web typed action
  → loopx/chat_actions.py:1146   if action_kind == "goal.create":
  → loopx/chat_actions.py:1147-1151  _project_for_goal_create(...)
  → loopx/chat_actions.py:1150   agent_id = normalized.get("agent_id") or "codex"
  → loopx/chat_actions.py:1152   eligibility = self._agent_eligibility(agent_id, project=project)   ← 【插入点 A】
  → loopx/chat_actions.py:271-316  _agent_eligibility()
        :298 available is not True        → "selected Agent endpoint is not healthy"
        :300 requires_tools and tool_calls is not True → "selected Agent endpoint cannot execute Goal tools"  ← 【B-06 报错】
  → loopx/chat_actions.py:560    _apply_goal_create()  (apply 阶段)
        :586-600 goal_id_conflict gate / creation_operation_id == proposal_id / workspace_digest
  → loopx/chat_actions.py:783    runtime_controller.open_session(...)      ← 【插入点 B】
  → loopx/chat_actions.py:800    runtime_controller.submit_turn(...)      ← 【插入点 B】
  → loopx/chat_actions.py:1540,1550,1599  另外三条 open_session/submit_turn 调用点
```
`[实测]`

### 1.2 Endpoint / Runtime

- `loopx/chat_endpoint_catalog.py:42-67` — 内建 `DeepSeek Harness (managed)`：`agent_id=MANAGED_TURN_HOST`、`adapter_kind="deepseek_harness_segment"`、`tool_calls:False`、`trust_scope:"read_only"`、`sandbox_mode=STEWARD_SEGMENT_ENV["DSH_PERMISSION_MODE"]`。`[实测]`
- `loopx/chat_endpoint_catalog.py:79-149` — `builtin_chat_endpoints()`，codex / claude-code / kiro 等行（`tool_calls:True`，`trust_scope:"read_only"`）。`[实测]`
- `loopx/chat_actions.py:337-374` `_resolve_goal_agent()` — 先 `_agent_eligibility(endpoint_id)`（:340），再做 endpoint↔durable agent 家族匹配，多重匹配抛 `agent_identity_selection_required`。`[实测]`
- `loopx/chat_runtime.py` — Chat Runtime Controller 与各 Chat Adapter（`DshChatAdapter`）。`[实测-存在]`

### 1.3 Governed Turn（既有，可复用）

- `loopx/dsh_goal_mode/turn_host_adapter.py:1-19` — **契约核心**：stdin 收一个 `loopx_turn_host_request_v0` JSON，stdout 吐**恰好一个** `loopx_turn_result_v0` JSON；自述为 “dumb translation layer”，不读 goal/todo、不花 quota、不自验证。`[实测]`
- 同文件 `:33-47` 从 `control_plane/turn_driver/host_candidate.py` 复用 `LOOPX_TURN_HOST_REQUEST_SCHEMA`、`LOOPX_TURN_RESULT_SCHEMA`、`ACCEPTED_RESULT_KINDS`、`COMPLETED_PHASES`、`MATERIAL_KINDS`、`TEXT_LIMITS`、`extract_action_text`、`extract_turn_authority`、`render_prompt`、`parse_model_json`、`build_result`。`[实测]`
- `:360-394` `DshHostConfig` — 已有 `workspace / provider / model / reasoning_effort / max_tokens / dsh_home / cordis / runtime_bin / request_timeout_seconds / dsh_runner / env`。`[实测]` **`dsh_home` 字段已存在**，与 Phase 0 发现的 `DSH_HOME` 可写性要求天然对齐。
- `:566` `def run_dsh_host(...)`；`:660` `adapter: stdin is not a loopx_turn_host_request_v0 object`。`[实测]`
- `loopx/cli_commands/turn_dsh_host.py:8,33` → `DshHostConfig` / `run_dsh_host`。`[实测]`
- `pyproject.toml:30` `deepseek-harness-sdk==0.1.5rc1`；`loopx/dsh_goal_mode/README.md:125` 明确 “validated `deepseek-harness-sdk==0.1.5rc1` API, or a compatible runner”。`[实测]`

### 1.4 turn_driver 既有能力（大多数“新功能”其实已存在）

`loopx/control_plane/turn_driver/` 已包含：`driver.py`、`executor.py`、`loop_controler`/`loop_controller.py`、`managed_step.py`、`transaction.py`、`journal_store.py`、`turn_journal_runtime.py` + 一批 `turn_journal*.ts`、`settlement.py` / `settlement.ts` / `settlement_provider.ts` / `post_settlement.py`、`recovery.py` / `session_recovery.py`、`lane_fence.py`、`host_binding.py`、`host_candidate.py`、`host_failure.py`、`host_process.ts` / `host_process_group.ts` / `host_process_transport.py` / `leased_host_process.ts`、`execution_readback.py`、`command_validation.py`、`execution_profile.py`、`turn_contract_generated.py|.ts`。`[实测-文件清单]`
`turn_contract_generated.py` 与大量 `*.ts` 并存 → **LoopX 控制面是 Python + TypeScript 双实现**，任何协议改动需两侧同步。`[实测]`

### 1.5 准入 / Quota / 写回

- Quota 准入实测：`quota should-run --todo-id` **必须**同时给 `--turn-instance-id`，否则 `QUOTA_VALIDATION_FAILED` / `state=blocked_validation`；绑定后返回 `decision=run`、`spend_allowed_now=false`、`spend_after_validation=true`。`[实测]`
- Todo 写回：`loopx todo add ... --task-class advancement_task --action-kind ... --validation-command ...`；幂等键 `--operation-id` / `--turn-instance-id` / `--completion-identity-key`。`[实测-help]`
- 结算/回执：`loopx refresh-state --goal-id ... --agent-id ... --progress-scope agent_lane`；本机实测写 `ACTIVE_GOAL_STATE.md` 并同步 global registry。`[实测]`

---

## 2. 拟新增 / 修改文件（按 Phase）

> 落点遵循 `docs/reference/extensions.md:161-171` 的所有权边界：`loopx/capabilities/`=契约与核心 provider；`loopx/extensions/`=随 wheel 的扩展生命周期与 bundled provider；`packages/<id>/`=可独立安装单元。

### Phase 1 — Adapter 核心（无 Web）

| 文件 | 动作 | 说明 | 置信 |
|---|---|---|---|
| `loopx/dsh_goal_mode/dsh_cli_turn_host.py` | 新增 | DSH 0.2 CLI host adapter：stdin 收 `loopx_turn_host_request_v0` → argv 数组启动 `dsh --profile headless --json -` → prompt 走 stdin → 逐行解析 NDJSON → stdout 吐一个 `loopx_turn_result_v0`。复用 `host_candidate.py` 的 schema/render/parse/build 助手 | 高 `[推断,基于:566 同构]` |
| `loopx/dsh_goal_mode/dsh_cli_process.py` | 新增 | 受控子进程：进程组、超时、SIGTERM→SIGKILL 升级、空闲心跳、孤儿回收；**启动前校验 `DSH_HOME` 可写** | 高 `[推断,基于 host_process_group.ts]` |
| `loopx/dsh_goal_mode/dsh_cli_ndjson.py` | 新增 | NDJSON codec：事件判别 `type`；`status.phase`；完成判据 = `exit==0 AND turn_end.reason.kind=="completed"`；`tool_call.callId ↔ tool_result.callId` 配对；`usage` 累加；未知 `type` fail closed；8 KiB/32 KiB 截断 | 高（schema 已实测） |
| `loopx/dsh_goal_mode/dsh_cli_probe.py` | 新增 | 运行时探测：`dsh` 存在性、`--version == 0.2.0-rc.2` 兼容区间、`--profile headless --help` 成功、`DSH_HOME` 可写、凭据可达、composition 工具清单 | 高 |
| `loopx/dsh_goal_mode/dsh_cli_errors.py` | 新增 | DSH 退出原因/`error` 事件 → LoopX `BuiltInHostError` / host_failure 映射（fail closed） | 中 `[推断]` |
| `loopx/control_plane/turn_driver/host_binding.py` | 修改 | 增加 dsh-CLI host route 的绑定与探测分支（现有 `:277-278` 的 `configured_runner` / `probing_interpreter` 二选一逻辑是接入点） | 中 |
| `loopx/cli_commands/turn_dsh_host.py` | 修改 | 增加 `--host dsh-cli`（或 `--dsh-runtime cli-0.2`）选择新 adapter，保留旧 SDK 路径 | 中 |
| `tests/test_dsh_cli_turn_host.py` | 新增 | Contract 测试：请求→进程→结果 的序列化快照；错误闭合 | 高 |
| `tests/test_dsh_cli_ndjson.py` | 新增 | Keyless unit：事件解析、配对、截断、未知 type、缺 final、无效 JSON | 高 |
| `docs/integrations/dsh-coding-worker.md` | 新增 | 安装/授权/排障/回滚 | 高 |

### Phase 2 — 注册与 Web 双路线

| 文件 | 动作 | 说明 | 置信 |
|---|---|---|---|
| `packages/loopx-dsh-coding-worker/extension.toml` | 新增 | `schema_version="loopx_extension_manifest_v0"`、`id`、`requires_loopx_api`、`permissions`、`[runtime]`（protocol/python_module/doctor_args/required_permissions/timeout_seconds）、`[[implements]]` 绑定既有核心能力（**待定稿，见 architecture §8.2**） | 中 |
| `packages/loopx-dsh-coding-worker/` | 新增 | 用 `loopx extension init loopx-dsh-coding-worker --execute` 脚手架生成 provider + 版本化 request/response JSON Schema | 高 `[实测:extensions.md:218-226]` |
| `loopx/chat_actions.py:1152` | 修改 | 【插入点 A】`goal.create` 按 `executor_route` 分流：`chat_agent` 保持现逻辑；`governed_turn_worker` 改走 Worker 能力准入，**不调用** `_agent_eligibility(requires_tools=True)` | 高 |
| `loopx/chat_actions.py:783,800` | 修改 | 【插入点 B】执行型 Worker 不经 `runtime_controller.open_session()/submit_turn()` | 高 |
| `loopx/chat_actions.py:271-316` | **不改** | `_agent_eligibility()` 的 tool 检查保持原样（不得放宽） | 高 |
| `loopx/chat_endpoint_catalog.py:42-67` | **不改** | 只读 managed Chat 原样保留 | 高 |
| `apps/presentation/dashboard/src/views/dashboard-page.tsx` | 修改 | Goal 向导增加执行器类型（Chat Agent / Coding Worker）与运行配置、执行计划预览 | 高 `[实测-存在]` |
| `apps/presentation/dashboard/src/data/*` | 修改/新增 | 复用既有 API client/类型；新增只读 Worker capability / run readback 投影 | 中 |

### Phase 3 — 续跑、对账、界面

| 文件 | 动作 | 说明 |
|---|---|---|
| `loopx/control_plane/turn_driver/recovery.py` / `session_recovery.py` | 修改 | DSH 进程退出/服务重启后的对账；未知副作用停在待核对 |
| `loopx/control_plane/turn_driver/settlement.py` / `post_settlement.py` | 复用/小改 | 独立 Validator 结算；Turn 身份重放幂等 |
| Dashboard run detail | 修改 | Worker/版本/健康/队列/Turn 起止/脱敏事件/验证/Git diff/Gate |

---

## 3. 复用 vs 新增（关键判断）

**必须复用、不得重建**（AD-03）：TurnEnvelope schema、Turn Journal、lease/lane fence、host process group、settlement、recovery、quota、Todo 权威。

**必须新增**：只有“把 TurnEnvelope 翻译成 `dsh` 0.2 CLI 子进程并翻译回来”这一层，以及它的探测/进程/编解码/错误映射。

即：新增的是一个 **host adapter**，不是第二套调度器。

---

## 4. 受影响测试清单

| 测试文件 | 为什么受影响 | 必须回归 |
|---|---|---|
| `tests/test_chat_dsh_adapter.py` | 只读 managed DSH Chat 回归基线（AC-03/FR-12） | ✅ |
| `tests/test_chat_actions.py`（若存在 goal.create 覆盖） | 插入点 A 的分流 | ✅ |
| `tests/test_dsh_goal_mode.py` | 现有 SDK host adapter 契约 | ✅ |
| `tests/test_loopx_turn_driver.py` | Turn driver / journal / settlement | ✅ |
| `tests/dsh_goal_mode_fake_runner.py` | 测试用 fake runner —— 新 adapter 可照此建 fake CLI runner | ✅ |
| `tests/dsh_goal_mode_transport_runner.py` | transport 层 | ✅ |
| `tests/dsh_goal_mode_raising_runner.py` | 失败路径 | ✅ |
| `tests/dsh_goal_mode_capacity_runner.py` | 容量/准入 | ✅ |
| `apps/presentation/dashboard/**` 前端测试 | Goal 向导与 run readback | ✅ |

`tests/` 下共 286 个 `*.py`；上表为直接相关子集。`[实测-计数]`

---

## 5. 明确不得改动（回归红线）

1. `loopx/chat_endpoint_catalog.py:59` `tool_calls: False` 与 `:60` `trust_scope: "read_only"`。
2. `loopx/chat_actions.py:300-301` 的 `cannot execute Goal tools` 分支语义。
3. `packages/dsh-loopx-plugin/` 默认不改（PRD §6）。
4. `~/.local/share/loopx/releases/<snapshot>` 安装快照不直接编辑；`loopx` 实际解析到 `releases/20261010T030536Z`，而 `/data/loopx` 是 canary，二者需区分。`[实测]`

---

## 6. 依赖方向（新增部分）

```
Web dashboard
  → chat_actions.py (goal.create, executor_route)         [改]
  → Worker capability registry / extension runtime         [新]
  → turn_driver (复用 journal/lease/settlement/recovery)
  → dsh_cli_turn_host.py                                   [新]
      → dsh_cli_probe.py (DSH_HOME 可写 / 版本 / profile)  [新]
      → dsh_cli_process.py (进程组/取消/超时/心跳)          [新]
      → dsh_cli_ndjson.py (事件编解码/完成判据)             [新]
      → dsh_cli_errors.py (fail closed 映射)               [新]
  → 独立 Validator → Journal → Quota/Todo 写回             [复用]
```

---

## 7. Phase 1 前需复核的推断项

| 项 | 复核方式 |
|---|---|
| `host_binding.py` 的 `configured_runner` 语义与 dsh-CLI route 的最佳接入方式 | 读全文 + 现有 runner 测试 |
| 新 adapter 应放 `loopx/dsh_goal_mode/` 还是作为 `packages/` 扩展运行时 | 对齐 capability/provider registry |
| `turn_contract_generated.ts` 是否需同步改动 | 读 TS 侧 host route 选择逻辑 |
| Web `executor_route` 的 typed action schema 落点 | 读 `chat_action_normalization.py` + 前端 action packet |
