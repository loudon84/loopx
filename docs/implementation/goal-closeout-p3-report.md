# LoopX Goal Closeout v1.0 — P3 阶段报告（方案 A：完成态分类中性化）

| 项 | 值 |
|---|---|
| 分支 | `feature/dsh-coding-worker` |
| 前置 | P0 `d389037af`、P1 `b5565a0f0`、P2 `06a4f4aca` / `2d035c589` / `e2ae5b8b6` / `9df3beb18` |
| 阶段 | P3 第一步（AC-10 机制） |
| 日期 | 2026-10-11 |

## 1. PRD 原文要求

| AC | 原文 |
|---|---|
| AC-10 | 完成后 Web 显示"已完成"并进入历史，**不因最新失败 Run 的历史 Classification 自动回到"需修复"**；未解决的**当前**数据完整性风险仍提示 |
| AC-15 | Global Registry 失败后 Canonical completed 生效，后续**投影幂等补偿**到 Web，**不出现再次执行** |

## 2. 采纳方案 A（最小侵入）

在既有「状态中性分类」机制上落地 AC-10 的第一半。

| 文件 | 改动 |
|---|---|
| `control_plane/goals/closeout.py` | 新增 `GOAL_CLOSEOUT_COMPLETED_CLASSIFICATION = "goal_closeout_completed"` |
| `history.py` | 该分类加入 `STATUS_NEUTRAL_CLASSIFICATIONS`，并显式导入常量 |

`STATUS_NEUTRAL_CLASSIFICATIONS` 的消费方自动继承：`history.latest_status_run`（驱动状态的"最新有效 Run"选择）、`dreaming.py`、`chat_manager_history.py`、`status.py`（别名导出）。

## 3. 真实证据

```
== AC-10: the completion classification cannot flip a Goal back ==
  PASS  the completion classification is status neutral
  PASS  a failed completion run never drives the status
  PASS  a completion run alone drives no status at all
  PASS  an unresolved current risk still drives the status
ALL 24 CHECKS PASSED
```

第 3 条是 AC-10 的**后半句**：`run_index_collision` 这类**未解决的当前**风险仍然照常驱动状态 —— 中性化只排除完成态自身，不掩盖真实问题。

`latest_status_run` 是 `loopx/status.py` 派生展示状态的唯一闸门，因此该断言直接对应该 AC。

无循环导入：`loopx.history` / `loopx.status` / `loopx.chat_manager_history` / `loopx.dreaming` 全部干净导入。

真实 CLI 回归：

```
$ loopx --registry …/ges-delivery/.loopx/registry.json status
{"ok": true, "goal_count": 1, "run_count": 3, "global_goal_count": 3}
$ loopx … goal-closeout inspect --goal-id ges-dsh-skills-integration-goal
{"ok": true, "state": "closeout_blocked", "reason_code": "closeout_acceptance_not_configured"}
```

## 4. ⚠️ 未完成的部分（不得声明 AC-10 通过）

**已核实：`closeout_authority.ts` 不写任何 run-history classification** —— 它只写自己的 closeout history / receipt（`loopx_goal_closeout_history_v0`）。已确认的 grep 结果为 0 命中。

因此当前状态是：**分类已定义且中性，但尚无生产者写入它。** AC-10 要真正成立，缺一步接线：

> Goal 完成时，需要有一条 run-history / run-index 记录携带 `goal_closeout_completed` 分类（或等价地把"已完成"投影进 status），Web 才能从"需修复"稳定翻到"已完成并进入历史"。

同理 **AC-15 完全未开始**：Global Registry 失败后的幂等补偿路径尚未实现（`status` 已显示 `global_goal_count: 3`，说明 Global Registry 存在，是补偿的落点）。

## 5. 下一步（P3 剩余）

1. **AC-10 接线**：让完成动作产生携带 `goal_closeout_completed` 的运行记录（或让 status 投影直接读 canonical closeout 并产出 `completed` 桶）
2. **AC-10 端到端证据**：构造「completed + 之后一次失败 Run」→ 断言 Web 投影为已完成且不被翻回；同时断言未解决的当前风险仍出现
3. **AC-15**：Global Registry 写入失败 → canonical completed 仍生效 → 下次投影幂等补偿且不产生第二次执行
4. 跨端一致性验证（CLI / Web / Hermes）

## 6. 本机环境限制（沿用）

`pytest`、`tsc` 仍未安装：第 3 节 24 项来自与提交测试**同一 facade / 同一 fixture / 同一断言**的等价证据脚本；`pytest` 文件已同步加入对应用例但本机无法执行。
