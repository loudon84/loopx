# DSH Coding Worker — 架构决策记录（Phase 0 定稿）

> 状态：Phase 0 产出，待评审
> 日期：2026-10-10
> 基线：LoopX `1.3.1`（`2643d4ff7`，分支 `feature/dsh-coding-worker`）、DSH `0.2.0-rc.2`
> 配套：[`dsh-coding-worker-phase0.md`](./dsh-coding-worker-phase0.md)

---

## 1. 决策总表

| 编号 | 决策 | 状态 | 依据 |
|---|---|---|---|
| AD-01 | 新增执行型 Worker Provider，只读 Steward Endpoint 原样保留 | 采纳 | 本文件 §3 |
| AD-02 | 首选 Headless NDJSON 子进程（Plan A） | **采纳（实测成立）** | phase0 §2/§4 |
| AD-03 | LoopX 保持唯一调度与状态权威 | 采纳 | 本文件 §6 |
| AD-04 | 安全执行与隔离（受信工作树先行） | 采纳 | 本文件 §5 |
| AD-05 | 允许最小 Core/Web 改造，不得以“纯插件”绕过检查 | 采纳 | 本文件 §4 |
| AD-06 | Worker 以 **LoopX Extension + Capability** 交付，不新建调度面 | 新增采纳 | 本文件 §4 |

---

## 2. 为什么不能复用只读 DSH managed Chat（AC-03 / FR-12）

现有 Endpoint 的事实（源码级）：

- `loopx/chat_endpoint_catalog.py:42-67`：`agent_id = MANAGED_TURN_HOST`、`adapter_kind = "deepseek_harness_segment"`、`tool_calls: False`、`trust_scope: "read_only"`、`sandbox_mode = STEWARD_SEGMENT_ENV["DSH_PERMISSION_MODE"]`。
- `loopx/chat_actions.py:271-316` `_agent_eligibility()`：`available is not True` → `"selected Agent endpoint is not healthy"`；`requires_tools and tool_calls is not True` → `"selected Agent endpoint cannot execute Goal tools"`。
- `loopx/chat_actions.py:337-340` `_resolve_goal_agent()` 也会调用 `_agent_eligibility(endpoint_id)`。

**决策**：该 Endpoint 的 `tool_calls=false` / `read_only` **必须保持原样**，作为 AC-03 回归基线；Coding Worker 不是它的别名，也不得把它改成 `tool_calls=true`。

**明确禁止的做法**（对应 PRD 一票否决条件）：

1. 把 `tool_calls=False` 改成 `True` 以通过 Web 检查；
2. 放宽 `_agent_eligibility()` 的 `cannot execute Goal tools` 分支来绕过能力检验；
3. 让 Coding Worker 冒充 `deepseek_harness_segment` 进入 `DshChatAdapter`。

正确做法是在 `goal.create` 引入**显式执行器路由**（`chat_agent` / `governed_turn_worker`），使执行型 Worker 走 Governed Turn 入口，而不是复用只读 Chat 准入逻辑。

已验证的同期事实（2026-10-10 实测）：`loopx chat-endpoint inspect-steward` 当前返回 `ok:true` 但 `available:false` / `unavailable_reason:"dsh_runtime_unavailable"` / `runtime_probe.available:false`。根因是 `loopx/control_plane/turn_driver/host_binding.py:277-278`：未配置 `configured_runner` 时回退为在**探测解释器**中 `find_spec("deepseek_harness")`，而该模块只存在于 `~/.local/share/loopx-dsh-venv`，LoopX CLI 自身解释器（`~/.local/bin/python3.12`）不可导入。这是**既有环境/配置条件**，与本次改动无关，但意味着 AC-03 的“Steward 正常对话”基线当前无法在本机复现，需在 Phase 1 前先确定评测口径（详见 §7）。

---

## 3. AD-01：新增 Worker Provider，而非修改既有 Endpoint

Coding Worker 对外身份：

```json
{
  "schema_version": "loopx_dsh_coding_worker_capability_v1",
  "worker_endpoint_id": "dsh-coding-worker-local",
  "worker_kind": "dsh_coding_worker",
  "execution_protocol": "loopx_turn_v0",
  "session_ownership": "managed_runtime",
  "capabilities": {
    "shell": true,
    "file_read": true,
    "workspace_write": true,
    "git": true,
    "session_resume": "probe_required",
    "structured_events": "probe_required"
  },
  "availability": { "ready": false, "reason": "not_probed" }
}
```

Phase 0 后对 `capabilities` 的**实测约束**（不得越级声明）：

- `shell` / `file_read` / `workspace_write` / `git`：Headless composition 已实测挂载 `tool-bash`、`tool-fs`、`tool-fs-search`、`sandbox/bash-sandbox/fs-sandbox`（`--dump-config`），可支持；但**具体到某次 Turn 的实际写权限仍由启动参数与校验决定**，不能仅凭 composition 就宣称已授权。
- `structured_events`：实测支持（`--json` 13 类事件，见 phase0 §4）。可升级，但需把成功判据固定为 `exit code + turn_end.reason`。
- `session_resume`：**必须保持 `probe_required`**。负路径已实测（未知 id → exit 1 + `error`），正路径跨进程恢复尚未验证（phase0 U-01）。

---

## 4. AD-05/AD-06：交付形态 — Extension + Capability，而非新调度面

`docs/reference/extensions.md` 的既有约束：

- `loopx/capabilities/<capability>/` = 面向调用方的契约与核心 provider；`loopx/extensions/` = 扩展生命周期与随 wheel 发行的 provider；`packages/<package-id>/` = 可独立安装的发行单元（:161-171）。
- **目录不会被扫描**：目录发现不扫描任意目录、也不导入扩展 Python 代码（:192-193）。仅放入目录不会成为 Endpoint——与 PRD §6.1 的判断一致。
- manifest 为声明式 TOML：`schema_version = "loopx_extension_manifest_v0"`、`id`、`version`、`requires_loopx_api`、`permissions`、`[runtime]`（`protocol`/`python_module`|`entrypoint`/`doctor_args`/`required_permissions`/`timeout_seconds`）、`[[provides]]`（新增能力契约）、`[[implements]]`（绑定到**既有**核心能力，不重复 capability id）、`[[hook_adapters]]`（:763-834）。
- 运行期真值唯一来源：`<runtime-root>/extensions/state.json`；重复 capability/provider id **fail closed**（:186-190）。
- 脚手架：`loopx extension init <id> --execute` → `packages/<id>`，含 manifest、JSON stdin/stdout provider、版本化 request/response JSON Schema（:218-226）。

**决策**：DSH Coding Worker 优先以 `packages/loopx-dsh-coding-worker/`（独立可安装发行单元）+ `[[implements]]` 绑定既有核心能力的方式交付；只有当契约确属新能力时才 `[[provides]]`。**不新增** Task DB、Todo 状态机、定时器或审批 Gate。

---

## 5. AD-04：安全与隔离（Phase 0 新增硬约束）

- **`DSH_HOME` 必须可写**：DSH 每次启动都重写 `$DSH_HOME/profiles/<name>/cordis.yml`（`@deepseek-ai/dsh/lib/profile-boot-BZ2ZjNWi.js:189`）。不可写时**任何 profile（含 `--help`）都会 EROFS 失败**。此项必须进入 Worker 的 `runtime_probe` 可用性检查与进程启动前置条件，否则每次 Turn 都会在引导阶段失败。
- 子进程必须用 **argv 数组** 启动，prompt 走 **stdin**，不经 shell 字符串拼接。
- 仅对显式授权工作树写入；不读任意凭据目录；不 sudo。
- 首版仅限受信本机工作树；不可信多用户/生产需独立 UID/容器后再验收。

---

## 6. AD-03：权威边界

Worker 只处理 `Turn request → DSH execution → typed result`。以下全部仍归 LoopX：

- Goal/Agent/Todo identity 与 claim/lease；
- `quota should-run` 准入（`should_run=false` 时**绝不**启动 DSH）；本机实测 LoopX 要求在绑定 Todo 时同时提供 `--turn-instance-id`，否则 `QUOTA_VALIDATION_FAILED`（`state=blocked_validation`）——这正是“Turn 身份先于执行”的既有约束，Worker 必须沿用而非绕过；
- Turn Journal、独立 Validator、结算与写回、下一次 wake。

**过程事件是观测，不是写回证据**：`--json` 的事件流只是投影（发行 README 明示其非无损日志、有 8 KiB/32 KiB 截断），因此只能用于展示与诊断；Done/Quota 事实必须由独立 Validator 依文件断言、测试退出码与 Git 基线产生。

---

## 7. 备选方案与拒绝理由

| 方案 | 结论 | 理由 |
|---|---|---|
| Plan A：`dsh --profile headless --json -` 子进程 | **采纳** | 本机 0.2.0-rc.2 实测支持全部所需选项（phase0 §2） |
| Plan B：改用受支持的 SDK/JSON-RPC/Host API | 暂不启用 | Plan A 已成立，无触发条件 |
| 复用旧 Python `deepseek-harness-sdk==0.1.5rc1` | **拒绝** | `pyproject.toml:30` 锁定该版本，且 `loopx/dsh_goal_mode/README.md:125` 明确针对该 API；以它冒充 0.2 CLI 属一票否决 |
| 把 `deepseek_harness_segment` 标为 `tool_calls=true` | **拒绝** | 欺骗 Web 准入、破坏 AC-03 基线 |
| 放宽 `_agent_eligibility` 的 tool 检查 | **拒绝** | 等于跳过真实执行能力检验 |
| 让 Worker 走 `DshChatAdapter` | **拒绝** | 混淆只读 Chat 与执行型 Worker，违反 AD-01 |

---

## 8. 待评审事项

1. **AC-03 评测口径**：Steward 当前 `available:false` 属既有环境条件。是要求先修复 `configured_runner`/venv 接线再评测回归，还是把“Steward 仍 `tool_calls=false`+`read_only`”作为回归口径而把可用性单列？
2. **Worker 能力归属**：DSH Coding Worker 应 `[[implements]]` 哪个既有核心能力（governed turn host / external connector / 其他），还是必须 `[[provides]]` 新能力？需在 Phase 1 与 capability registry 对齐后定稿。
3. **`session_resume` 升级条件**：是否批准第二次极小真实调用以验证跨进程正路径恢复（phase0 U-01 / AC-17）。
