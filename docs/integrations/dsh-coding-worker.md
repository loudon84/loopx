# DSH Coding Worker — 安装、运行、验收、排障与回滚

> 状态：Phase 4 交付（功能旗标默认关闭 + 回滚流程 + 操作手册）
> 基线：LoopX `1.3.1`、DSH CLI `0.2.0-rc.2`
> 配套：[`../implementation/dsh-coding-worker-phase0.md`](../implementation/dsh-coding-worker-phase0.md)、[`../implementation/dsh-coding-worker-architecture.md`](../implementation/dsh-coding-worker-architecture.md)、[`../implementation/dsh-coding-worker-test-report.md`](../implementation/dsh-coding-worker-test-report.md)

---

## 1. 它是什么

**Governed Turn Worker**：LoopX 决定做什么与是否允许，DSH 0.2 的 headless CLI 在一个授权工作区里执行**一个**受控 Turn，独立 Validator 判定产物，LoopX 再提交写回与配额。

它不是调度器，也不是第二套状态机。Goal/Todo/Quota/Gate/Journal/结算/写回全部仍归 LoopX。

```
LoopX Web → goal.create(executor_route=governed_turn_worker)
  → Worker Catalog 准入（真实探测）
  → LoopX 授予一次 Turn
  → dsh --profile headless --json -   （一个受控子进程）
  → 独立 Validator → Turn Journal → 写回/配额
```

---

## 2. 功能旗标：默认关闭

能力**默认暗**。未显式开启时，`goal.create` 走 `governed_turn_worker` 会被**拒绝**，Worker 不会出现在能力投影里。

| 环境变量 | 取值 | 效果 |
|---|---|---|
| `LOOPX_DSH_CODING_WORKER` | 未设 / `0` / `false` / `off` / `no` / 空 / 任意无法识别的值 | **关闭**（默认） |
| `LOOPX_DSH_CODING_WORKER` | `1` / `true` / `yes` / `on`（大小写不敏感） | 开启 |

无法识别的值一律按**关闭**处理 —— 回滚时清掉变量即可恢复旧行为，不需要猜。

```bash
# 开启（opt-in）
export LOOPX_DSH_CODING_WORKER=1

# 关闭（回滚）
unset LOOPX_DSH_CODING_WORKER
```

关闭状态下的拒绝信息是 typed 的，会指明 opt-in 变量，而不是笼统报错。

---

## 3. 安装与运行前提

1. **DSH CLI 0.2.x**：`dsh --version` 必须在 `0.2.` 线上（实测基线 `0.2.0-rc.2`）。
2. **`DSH_HOME` 必须可写** —— 这是硬前提。DSH 每次启动（**包括 `--help`**）都会重写
   `$DSH_HOME/profiles/<name>/cordis.yml`；不可写时**每一次 Turn 都会在引导阶段因 EROFS 失败**。
3. **凭据可达**：`DEEPSEEK_API_KEY` 或 `$DSH_HOME` 下的凭据存储。
4. **授权工作区**：Worker 只在其被显式授权的根路径内写入；未授权的工作区会在准入阶段被拒。
5. **验证命令以 argv 形式启动**（不是 shell）。声明 `cd … && …` 这类命令会因可执行文件是 shell 内建而 `command_not_run`。

```bash
# 可用性探测（只读，不消耗模型）
python3 -c "
from pathlib import Path
from loopx.dsh_goal_mode.dsh_cli_probe import DshCliRuntime, probe_dsh_cli
r = probe_dsh_cli(DshCliRuntime(dsh_bin='dsh', dsh_home=Path.home()/'.dsh', workspace=Path.cwd()))
print(r['ready'], r['reason'])
"
```

---

## 4. 执行一次受控 Turn

```bash
# 1) 建立目标与 Todo（Web 或用例：goal.create + todo add）
# 2) 让 host loop 驱动（自动续跑；LoopX 决定跑到哪里停）
PYTHONPATH=/data/loopx python3 -c "
from pathlib import Path
from loopx.dsh_goal_mode.governed_turn_loop import run_host_loop
o = run_host_loop(
    goal_id='<goal-id>', agent_id='<agent-id>', project=Path('.').resolve(),
    dsh_runner=Path('loopx/dsh_goal_mode/dsh_cli_runner.py'),
    # 每个 Todo 有自己的完成验证，绝不共用猜测
    validation_by_todo={'<todo-id>': ['python3','-c','...']},
    max_turns=4,
)
print(o.as_dict())          # stop_kind / host_invocations 是关键观测字段
"
```

`stop_kind` ∈ `paused | gate | quota | no_work | turn_failed | cancelled | dry_run | max_turns`；
`host_invocations` 是**真正到达 host 的 Turn 数**（AC-10 直接看它）。

单个 Turn 也可以直接跑：

```bash
loopx turn run-once --goal-id <goal> --agent-id <agent> --host dsh --project . \
  --todo-id <todo> --turn-instance-id turn-<unique> \
  --dsh-runner /data/loopx/loopx/dsh_goal_mode/dsh_cli_runner.py \
  --validation-command-json '["python3","-c","..."]' \
  --timeout-seconds 600 --execute
```

---

## 5. 运行可观测

只读运行历史：

```bash
curl -s "http://127.0.0.1:<port>/api/chat/worker-runs?goal_id=<goal>" | python3 -m json.tool
```

每个 Turn 给出：Turn 标签、`todo_id`、**记录时间**、终态、`result_kind`、完成阶段、独立验证结论
（`status`/`passed`/`exit_code`/`validator_kind`）、失败类别、尝试次数。

两点必须知道：

- **时间戳是 journal 文件写入时间**（`recorded_at_source=journal_file_mtime`）。journal 不记录墙钟时间，所以这里**不**伪装成 Turn 起止时间。
- **`blocked_health` 通常等于「目标被暂停」**。暂停态下 Todo 级绑定无法取得资格，配额返回
  `state=paused`，Todo 级则显示 `blocked_health`。先看 `activation.state`，不要把它当成损坏。

> **UI 呈现尚未实现**：接口与投影已就绪并验证；Dashboard 尚未渲染这份数据。

---

## 6. 排障

| 症状 | 原因 | 处理 |
|---|---|---|
| `LoopX Chat bundle is missing, stale or invalid: frontend source changed since the last build` | 前端源码改过但 bundle 未重建 | `cd apps/presentation/dashboard && npm run build:chat`（dashboard 还需 `npx vite build`） |
| `EROFS ... profiles/<name>/cordis.yml` | `DSH_HOME` 不可写 | 指向可写目录；这是硬前提 |
| `selected Agent endpoint cannot execute Goal tools` | 用了只读 Chat endpoint 走 Goal 工具路径 | **不要**改 `tool_calls`；改用 `executor_route=governed_turn_worker` |
| `the Coding Worker capability is disabled on this machine` | 功能旗标关闭（默认） | `export LOOPX_DSH_CODING_WORKER=1` |
| `selected Coding Worker is not available: <check>` | 探测的某一必需项失败 | 按 `<check>` 名排查：`binary_present`/`version`/`headless_profile`/`headless_composition`/`dsh_home_writable`/`credentials` |
| `validation command could not be launched because its executable is unavailable` | 验证命令不是以可执行文件开头（如 `cd … && …`） | 改成 argv 形式，首个 token 必须是真实可执行文件 |
| `command_not_run` | 同上 | 同上 |
| `actor_required` | 完成 `role=user` 的 Todo 却没给 `--agent-id` | 传入该 Todo 的 `bound_agent` |
| `bound_agent_mismatch` | actor 不是该 Todo 的 `bound_agent` | 用 `bound_agent` 作为 actor（UI 侧见 `attention-actions.tsx` 的 gate actor 逻辑） |
| `blocked_health` | 目标被暂停（`activation.state=stopped`） | 由 owner 执行 `goal-lifecycle --operation resume --actor-kind owner --execute` |
| `an open autonomous replan obligation requires a typed semantic delta` | 完成 Todo 时**没有**声明后继或 `no-follow-up` | 见 §7 的强制约定 |

---

### 6.1 排序死锁：`--no-follow-up` 与 turn-scoped `refresh-state` 互相等待

实测撞到过一个**真实的循环依赖**（已找到出口）：

| 命令 | 拒绝理由 |
|---|---|
| `todo complete … --no-follow-up` | `requires matching writeback and quota spend receipts: matching accountable refresh-state receipt is missing for the original settlement identity` |
| `refresh-state --turn-instance-id … --todo-id …` | `accountable refresh is blocked until controller-declared completion validation durably completes todo …` |

两者都要求对方先完成。**出口是：先做一次不带 `--no-follow-up` 的普通 `todo complete`** —— 它会把「控制器声明的完成验证」durably 落库：

```bash
# 1) 普通完成（带 --validation-command-json），不要带 --no-follow-up
loopx todo complete --goal-id <goal> --todo-id <todo> --agent-id <agent> \
  --turn-instance-id <turn> --status done \
  --task-lease-idempotency-key <lease-key> --task-lease-expected-version <v> \
  --validation-command-json '[...]'
# 2) 用已存在的 Turn 身份结算（不会重跑 host）
loopx turn run-once --goal-id <goal> --agent-id <agent> --host dsh --project . \
  --resume-turn-key <key> --dsh-runner <path> --validation-command-json '[...]' --execute
```

实测结果：`status=committed`、`validation=passed`、**`host_invoked=false`**、七阶段齐全、`commit_eligibility` 全 true。

注意 `--no-follow-up` 仍然需要，只是**不能在第一步用**：普通完成会留下一个开放的重规划义务，用 §7 的方式（typed delta）清除。

另外：`task-lease` 可能已被 host loop 持有，key 形如 `host-loop-lease-<todo_id>`。用 `loopx task-lease inspect` 查出来复用，不要另起一个 key（会得到 `todo_lease_conflict`）。

## 7. 强制约定：完成 Todo 必须声明后续

**这是踩过的坑，写进手册以免重犯。**

LoopX 在「完成一个 Todo 后没有任何可运行后继」时会立起 `autonomous_replan_required` 义务，并要求一个 typed 语义增量。因此每次完成都要显式声明其一：

```bash
# 有真实后继 → 链接它（这同时是释放 blocked 依赖方的唯一动作）
loopx todo complete ... --successor-todo-id <successor-todo>

# 确无后继 → 明确说明
loopx todo complete ... --no-follow-up
```

- **只能在完成时声明**：事后 `todo update --successor-todo-id` 会被拒（`native metadata update cannot complete a Todo`），重复完成也会被拒（`operation id already names a different coordination request`）。
- **不要**为了消掉义务而伪造 user gate 或占位 Todo —— LoopX 明确禁止（`do not invent a user gate or add a lifecycle-only filler Todo`）。
- host loop 会在结算时**自动**做这件事（`governed_turn_loop._successor_declaration`：唯一 blocked 后继则链接、无后继则 `--no-follow-up`、多候选歧义则**不猜**）。**手工完成时不要绕过它。**

依赖型 Todo 的正确形状（AC-09 裁定，见 architecture §9）：

1. 依赖方以 `--status blocked` 建立 → 它不会进入候选集（真实「不可选」，不是「备选」）。
2. 前驱完成时 `--successor-todo-id <依赖方>` → 释放它。

---

## 8. 回滚

目标：**不再调度新的 Coding Worker，同时不丢任何已有状态**。

```bash
# 1) 先拒绝新任务：关闭能力（对新建 Goal 立即生效）
unset LOOPX_DSH_CODING_WORKER
#    已有 Goal 若已选 governed_turn_worker，其 Todo 仍会在准入时被拒

# 2) 停住正在跑的 host loop：给它一个 cancel 信号（会按进程组终止在途 loopx 调用）
#    不要 SIGKILL 父进程后放任子进程；进程组会被一并清理

# 3) 让在途 Turn 收尾并对账：
loopx turn inspect-journal --goal-id <goal> --agent-id <agent> --turn-key <key>
#    recovery_decision 若为 resume_from=durable_writeback 且 reinvoke_host=false，
#    则以**已存在的 Turn 身份**结算，绝不重跑已产生副作用的 host：
loopx turn run-once --goal-id <goal> --agent-id <agent> --host dsh --project . \
  --resume-turn-key <key> --validation-command-json '[...]' --execute

# 4) 确认没有遗留 host 进程
pgrep -af "dsh --profile headless" || echo "no orphan host"

# 5) 恢复旧 Web/LoopX；所有 Goal、Todo、Turn Journal 与运行历史引用保持可读
```

回滚的**验收**（已由测试覆盖）：

- 旗标清除后，`goal.create` 走 `governed_turn_worker` 被 typed 拒绝，且**不创建 Goal**。
- 同一状态下 **Chat Agent 路线不受影响**。
- 已有 Goal/Todo/Journal 仍可读；`/api/chat/worker-runs` 仍能返回历史。

---

## 9. 已知边界（不得越级声明）

1. **argv 表达不了模型/推理强度/输出上限**。`dsh --profile headless` 只接受 `--json`、`--session-id`、任务文本。LoopX 传入的这些值被记入 `unsupported_argv_controls` 而**不会**生效；**`output_token_limit: 16384` 在 CLI 路线上未被真正施加**。按 Turn 固定模型需改 profile/`--patch`，属部署决策。
2. **`--session-id` 默认 fresh**；跨进程正路径恢复未实测。
3. **journal 无墙钟时间**，运行历史的时间戳是文件写入时间。
4. **强隔离未做**：当前是同一 Linux 用户的普通子进程。面向不可信多用户/生产需容器或独立 UID 后再验收。
5. **AC-15 的 UI 渲染未实现**；**配额耗尽态未在本机真实复现**（按类覆盖）。

---

## 10. 验收清单

```bash
# keyless 单元 + 契约（无模型费用）
env -u DSH_HOME python3 -m pytest \
  tests/test_dsh_cli_ndjson.py tests/test_dsh_cli_runner.py tests/test_dsh_cli_probe.py \
  tests/test_worker_catalog.py tests/test_governed_turn_loop.py tests/test_worker_run_readback.py -q

# 回归
env -u DSH_HOME python3 -m pytest \
  tests/test_dsh_goal_mode.py tests/test_chat_dsh_adapter.py tests/test_loopx_turn_driver.py -q
```

> `env -u DSH_HOME` 是必要的：`resolve_dsh_home` 会读取宿主 `DSH_HOME`，而若干既有测试断言工作区局部默认 home。
