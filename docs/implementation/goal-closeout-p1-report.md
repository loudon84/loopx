# LoopX Goal Closeout v1.0 — P1 阶段报告（Canonical Closeout Contract + Authority）

| 项 | 值 |
|---|---|
| 分支 | `feature/dsh-coding-worker` |
| P0 基线 | `d389037af` |
| 阶段 | P1（PRD §11 通过条件：AC-02…09、AC-14…16） |
| 日期 | 2026-10-11 |

## 1. 本阶段交付物

| 文件 | 操作 | 说明 |
|---|---|---|
| `loopx/control_plane/goals/closeout_contract.ts` | 新增 | `loopx_goal_closeout_v1` schema、状态集、`closeout_blocked` 仅作派生投影、Basis 摘要、blockers 求值、不变量、严格键 codec、公共投影 |
| `loopx/control_plane/goals/closeout_authority.ts` | 新增 | `inspect / prepare / confirm / history` 四个入口；复用 `CoordinationCommandReceipt` 做 CAS + 幂等 receipt；单次提交写入状态 + 审计事件 + Receipt |
| `loopx/control_plane/effect_runtime_handlers.ts` | 修改 | 注册 `goal.closeout.{inspect,prepare,confirm,history}`（紧随既有 `goal.acceptance.*`） |
| `tests/control_plane_ts/goal_closeout_authority.test.ts` | 新增 | 25 条用例，file + sqlite 双 provider |

设计约束落实：D01/D07（同一 SQLite Canonical Authority 与既有 CAS 提交）、D03（Todo done ≠ Goal completed）、D04（复用 `goal-acceptance`，验收契约由**真实验收引擎**产生，不手写通过标志）、D05（Agent 不能 confirm）、D06（completed 为终态）。

## 2. 真实测试证据

```
$ cd /data/loopx && node --no-warnings --experimental-sqlite --experimental-strip-types \
      --test tests/control_plane_ts/goal_closeout_authority.test.ts

✔ file: an open required Todo blocks Closeout
✔ file: done Todos without a configured acceptance contract are refused
✔ file: a failed independent verification blocks Closeout
✔ file: work changed after verification makes the basis stale
✔ file: accepted independent verification admits owner confirmation
✔ file: an agent actor can never confirm the Goal
✔ file: a stale basis digest is refused before any write
✔ file: owner confirmation commits completed atomically and replays
✔ file: an active lease blocks Closeout
✔ file: a continuous monitor must be retired or handed over
✔ file: an unsettled Turn and a vision close both fail closed
✔ file: inspect reports blockers without writing
✔ sqlite: <同上 12 条>
✔ the runtime boundary registers exactly the four canonical closeout RPCs
ℹ tests 25  ℹ pass 25  ℹ fail 0
```

既有回归：`effect_runtime_handlers.test.ts` → `tests 13 / pass 13 / fail 0`（证明新增 RPC 未破坏运行期注册表）。

## 3. AC 覆盖（本阶段实测通过的部分）

| AC | 状态 | 证据（用例名） |
|---|---|---|
| AC-02 | ✅ | `an open required Todo blocks Closeout`（并断言 head 未被写） |
| AC-03 | ✅ | `done Todos without a configured acceptance contract are refused`、`a failed independent verification blocks Closeout` |
| AC-04 | ✅ 部分 | 验证失败与非零退出 → `closeout_acceptance_failed`；验证后交付物变化 → `closeout_acceptance_stale`。**Verifier 文件 Pin 变化**由既有 acceptance 套件覆盖（本机无法加载，见 §5） |
| AC-05 | ✅ | `an agent actor can never confirm the Goal`（`closeout_owner_required`，且状态不变） |
| AC-06 | ✅ | `a stale basis digest is refused before any write`（head 完全一致） |
| AC-07 | ✅ | `owner confirmation commits completed atomically and replays`（同 operation_id → `replayed` + 同一 `completion_receipt_id`） |
| AC-08 | ✅ | 同一用例：一次 applied 提交后 `state/owner_confirmation/completion_receipt_id/completed_at` 同时存在；被拒的 prepare 不写 head |
| AC-09 | ✅ 部分 | `closeout_already_completed`（prepare/confirm 双向）。**quota/Turn/旧 Host 硬拒绝**属 P2 |
| AC-14 | ✅ 部分 | `prepare --dry-run` 不写 + `confirm` 必须携带 prepare 返回的 `basis_digest` |
| AC-16 | ✅ 部分 | `an active lease blocks Closeout`、`an unsettled Turn …`（`closeout_unsettled_turn`）。阻塞 Gate 的求值已实现但未单独断言 |
| AC-17 | ✅ | `a continuous monitor must be retired or handed over` |
| AC-18 | ✅ | `an unsettled Turn and a vision close both fail closed`（`vision_closed` 不产生 completed） |
| AC-20 | ✅ 部分 | `the runtime boundary registers exactly the four canonical closeout RPCs`（含 `goal.closeout.archive/force/complete/set_state` 必须不存在） |

**尚未覆盖（后续阶段，不在 P1 声明通过）**：AC-01（三类创建入口统一策略 → P2）、AC-10（Web 历史投影 → P3）、AC-11（`goal-lifecycle` 对 completed 的处理 → P2）、AC-12/13/19（老 Goal 迁移 → P4）、AC-15（Global Registry 投影补偿 → P3）。

## 4. 测试发现并修复的两个真实缺陷

1. **prepare 提交后的回读缺少 `basis_digest`** —— 调用方无法取得 confirm 所需摘要，`confirm` 必然失败。已修复（提交结果补 `basis_digest` / `can_close` / `blockers`）。
2. **`completed` 不变量要求 `goal_ref`，但请求线格式不接受、也未校验它** —— 实测抛 `completed Goal closeout requires goal_ref`。已修复：`goal_ref` 加入 prepare/confirm 请求白名单，confirm 额外校验 Goal Instance 一致（不匹配 → `closeout_goal_ref_mismatch`）。

这两条都只有在真实执行路径下才会暴露，说明用例确实在驱动真实行为而非复述实现。

## 5. 本机环境限制（如实记录，未绕过）

- `/data/loopx` 是**无 `node_modules` 的 checkout**，因此：
  - `npm run typecheck:control-plane` 无法执行（`tsc: not found`）→ **类型检查尚未运行**，P1 只以真实行为测试为准；
  - 任何 `import "pg"` 的既有测试（如 `goal_acceptance_authority.test.ts`）在本机 `ERR_MODULE_NOT_FOUND: Cannot find package 'pg'` 而无法加载 —— 这是既有环境限制，与本次改动无关。
- 需要时可用 `pnpm install`（或 `npm ci`）补齐依赖后运行完整 `test:control-plane` 与 `typecheck:control-plane`；在此之前不会声称「全套通过」。

## 6. 下一步（P2）

1. `loopx/control_plane/goals/closeout.py`（Python facade，仿 `acceptance.py`）
2. `loopx/cli_commands/goal_closeout.py`（inspect/prepare/confirm/history）
3. 终态硬门禁：`turn_run_once.py`、`dsh_goal_mode/governed_turn_loop.py`、`quota.py`、TS Todo 写路径
4. `goal_lifecycle.py`：completed 拒绝 stop/resume（AC-11）
5. 目标 → AC-01（创建入口统一 `strict_v1`）、AC-09 完整、AC-11
