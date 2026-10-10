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
