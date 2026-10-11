# LoopX Goal Closeout v1.0 — P2 阶段报告（CLI / 创建入口统一 / 终态停止）

| 项 | 值 |
|---|---|
| 分支 | `feature/dsh-coding-worker` |
| 前置 | P0 `d389037af`、P1 `b5565a0f0` |
| 阶段 | P2（PRD §11 通过条件：AC-01、AC-09、AC-11、AC-18） |
| 日期 | 2026-10-11 |

## 1. 本阶段交付物

| 文件 | 操作 | 说明 |
|---|---|---|
| `loopx/control_plane/goals/closeout.py` | 新增 | Python facade：`inspect / prepare / confirm / history` 路由到 Effect Runtime；`goal_closeout_completed()` 只读终态助手；`ensure_goal_closeout_policy()` 服务端策略层 |
| `loopx/cli_commands/goal_closeout.py` | 新增 | CLI `goal-closeout {inspect,prepare,confirm,history}`，含 `--owner-principal` / `--expected-basis-digest` / `--operation-id` / `--execute` |
| `loopx/cli.py` | 修改 | 注册命令与分派 |
| `loopx/control_plane/goals/activation_service.py` | 修改 | **AC-11**：`completed` 时拒绝 stop/resume（`goal_already_completed`） |
| `loopx/bootstrap.py` | 修改 | **AC-01**：Goal entry 经 `ensure_goal_closeout_policy()` 统一打 `strict_v1` |
| `loopx/control_plane/projects/registry.py` | 修改 | **AC-01**：`register_project_goal` 同样经该层 |
| `loopx/control_plane/goals/closeout_authority.ts` | 修改 | applied 提交回读补 `goal_closeout` 投影与 `basis_digest`（见 §4） |
| `tests/control_plane/test_goal_closeout_cli.py` | 新增 | pytest：策略层、CLI 表面、无 authority 时终态为 False、端到端 completed + AC-11 |

## 2. 真实证据

### 2.1 端到端（Python facade → Effect Runtime → TS authority → 真实 canonical 存储）

```
== AC-01: creation policy stamping ==
  PASS  caller cannot downgrade the closeout policy
== baseline: no acceptance configured ==
  PASS  inspect is read-only and reports the missing contract
== independent acceptance through the real acceptance authority ==
  PASS  acceptance contract configured
  PASS  independent verification accepted
== prepare ==
  PASS  prepare previews without writing
  PASS  prepare reaches awaiting_owner_confirmation
== confirm ==
  PASS  stale basis digest is refused
  PASS  owner confirm preview planned
  PASS  owner confirmation commits completed
  PASS  same operation id replays
  PASS  history retains both operations
== AC-11: completed is terminal ==
  PASS  completed projection
  PASS  goal_closeout_completed helper
  PASS  stopped refused on a completed Goal
  PASS  active refused on a completed Goal

ALL 15 CHECKS PASSED
```

### 2.2 真实 Goal 上的 CLI（PRD §6.1 命令）

```
$ loopx --registry /data/github/ges-delivery/.loopx/registry.json --format json \
    goal-closeout inspect --goal-id ges-dsh-skills-integration-goal
{ "ok": true, "status": "loaded", "provider_revision": "sqlite:0ca5c44dc3b84e53bebd15e47636d909:150",
  "state": "closeout_blocked", "can_close": false,
  "blockers": [{ "reason_code": "closeout_acceptance_not_configured",
                 "detail": "goal acceptance is disabled or unbound" }] }
```

结果与 PRD §1.1 对该 Goal 的诊断完全一致（`contract=null` / `enabled=false`），且 13 条历史 Todo 全部计入 required 与 satisfied —— 这正是 AC-12/AC-19 的老 Goal 场景。

### 2.3 回归

| 套件 | 结果 |
|---|---|
| `tests/control_plane_ts/goal_closeout_authority.test.ts` | `tests 25 / pass 25 / fail 0` |
| `effect_runtime_handlers.test.ts`（P1 已跑） | `13 / 13` |
| 真实 `goal-lifecycle --operation stop`（预览） | `ok: true` —— AC-11 守卫未误伤非终态 Goal |

## 3. AC 状态（P2 范围）

| AC | 状态 | 说明 |
|---|---|---|
| AC-01 | ✅ 部分 | 两个 Goal entry 构建点统一经 `ensure_goal_closeout_policy()`；调用方无法降级/伪造策略。Web `goal.create` 的最终兜底与跨入口一致性 hook test 属剩余工作 |
| AC-09 | 🟡 部分 | **Todo 写路径已完成**：`claim / create / update / terminal lifecycle` 四个权威层入口在 completed 时一律拒绝（`closeout_already_completed`），读路径不受影响。剩余：quota should-run / turn run-once / Host Loop 的终态拒绝 —— 见 §8 |
| AC-11 | ✅ | completed 时 stop 与 resume 均被拒（`goal_already_completed`），非终态 Goal 行为不变 |
| AC-18 | ✅ | P1 已证 `vision_closed` 不产生 completed |

## 4. 本阶段由测试发现的真实缺陷（已修）

1. **applied 提交的回读缺少 `goal_closeout` 投影与 `basis_digest`** —— 调用方与前端无法渲染状态，也无法取得 confirm 所需摘要。已在 prepare/confirm 的提交结果中补齐。

（另有两处为脚本自身预期错误，未计为产品缺陷。）

## 5. 尚未完成（下一阶段，不声明通过）

1. **AC-09 完整**：终态拒绝必须落在 Provider 层。
   - Python 侧：`quota should-run`、`turn run-once`、`dsh_goal_mode/governed_turn_loop.py`
   - TS Provider 侧（权威）：Todo claim / update / terminal writeback 的终态拒绝 —— 旧版 Host 只能靠这一层挡住
2. **AC-10 / AC-15**（P3）：Web 历史投影、Global Registry 投影补偿
3. **AC-12 / AC-13 / AC-19**（P4）：老 Goal 迁移与当前产物重验
4. **AC-20 完整**：旧写入路径的负向安全回归
5. **AC-01 剩余**：Web `goal.create` 兜底与跨入口 hook test

## 6. 本机环境限制（如实记录）

- `pytest` 在本机三个解释器中均**未安装**，因此 `tests/control_plane/test_goal_closeout_cli.py` 无法在本机执行；§2.1 的证据来自一个**等价调用序列**的证据脚本（同一 facade、同一 fixture、同一断言），未冒充 pytest 结果。
- `tsc` 同样不可用（无 `node_modules`），类型检查仍未运行。
- 补齐依赖后应执行：`pnpm install` → `npm run typecheck:control-plane` → `python -m pytest tests/control_plane/test_goal_closeout_cli.py`。

## 7. 观察（非本次改动）

真实 GES Goal 当前 `activation_state=stopped`（`goal-lifecycle` 预览显示 `before_state: stopped`）。该状态不是我这两阶段写入的，仅记录事实。

## 8. P2 收尾（第二次提交）

### 8.1 AC-09 权威层（Todo 写路径）

`local_authority_runtime.ts` 新增 `completedGoalTodoWriteRejection()`，并在四个 canonical Todo 写入口的 head 读取后、任何变更前调用：

| 入口 | action |
|---|---|
| `claimLocalCoordinationTodo` | Todo claim |
| `createLocalCoordinationTodo` | Todo create |
| `updateLocalCoordinationTodo` | Todo update |
| `terminalLifecycleLocalCoordinationTodo` | Todo terminal lifecycle |

读路径（`pollLocalCoordinationMonitor` / `observeLocalCoordinationOwnership`）与归档记账（`archiveLocalCoordinationTodos`）**不设门禁** —— 历史必须仍可读、可归档。

旧版 Host 或直接 RPC 调用方即使不认识 Closeout，也无法重开已 completed Goal 的 Todo 计划；Python provider 层把它转成 `LocalCoordinationAuthorityUnavailable`。

### 8.2 证据

```
== AC-09: completed Goal refuses Todo writes at the provider layer ==
  PASS  Todo create is refused on a completed Goal
ALL 16 CHECKS PASSED
```

真实异常文本：`LocalCoordinationAuthorityUnavailable: Goal goal-a is completed; Todo create is refused on a completed Goal`

回归（10 个 TS 套件，含本地权威运行时 / provider / archive / receipt / Todo 完成与归档 / 公共更新）：

```
ℹ tests 137   ℹ pass 137   ℹ fail 0   ℹ cancelled 0
```

### 8.3 仍未完成（下一阶段）

1. **AC-09 剩余**：`quota should-run`、`turn run-once`、`dsh_goal_mode/governed_turn_loop.py` 的终态停止（Python 侧纵深防御）
2. **AC-01 剩余**：Web `goal.create` 兜底与跨入口一致性 hook test
3. **AC-10 / AC-15**（P3）、**AC-12 / AC-13 / AC-19**（P4）、**AC-20 完整**（旧写入路径负向回归）

