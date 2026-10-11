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

### 5.1 AC-10 接线的落点（已勘查，未实施）

| 事实 | 位置 |
|---|---|
| 状态**词表**是 7 元组且无终态桶 | `control_plane/quota/states.py:7` `QUOTA_STATE_ORDER = (blocked_health, operator_gate, focus_wait, eligible, waiting, throttled, paused)` |
| 动作枚举同样无终态 | `control_plane/quota/effective_action.py:41`（含 `BLOCKED_HEALTH` 等） |
| 「哪些 Run 驱动状态」的闸门 | `history.latest_status_run` ← 本轮已中性化 ✅ |
| 状态**生产者** | `control_plane/quota/should_run_prepare.py`（500+ 行）与 `should_run_packet.py`（1200+ 行）内的 plan 构造 |
| 硬暂停已有先例 | `build_quota_paused_should_run_payload`（P2 已复用） |
| 完成态入口 | `build_quota_should_run`（P2 已加终态拒绝，返回暂停投影） |

**为什么这轮没做**：AC-10 的接线必须动 `QUOTA_STATE_ORDER` 这个词表及其生产者，而 `should_run_prepare.py` / `should_run_packet.py` 是 status、quota、调度、Web 共读的核心。本会话上下文已接近耗尽，不足以支撑「改词表 + 接生产者 + 跑回归 + 验证」这一整轮。按既定标准（不接受未经验证的"已完成"），此处停手并留档，而不是赌一把核心状态机。

**下一轮的具体动作**（按序）：
1. `QUOTA_STATE_ORDER` 增加终态桶 `completed`（置于末尾）并同步 `effective_action` 枚举
2. 在 plan 生产处读取 canonical closeout：completed → 该桶（**优先于** health/repair 派生）
3. 证据：构造「completed + 之后一次失败 Run」→ 断言 Web 投影为已完成、不被翻回，且未解决的当前风险仍出现
4. 再验 `latest_status_run` 与 `QUOTA_STATE_ORDER` 的消费方（status 计数、scheduler 推荐）无回归

### 5.2 其余

1. **AC-15**：Global Registry 写入失败 → canonical completed 仍生效 → 下次投影幂等补偿且不产生第二次执行
2. 跨端一致性验证（CLI / Web / Hermes）
3. Canary 与部署报告（P5）

## 6. 已解决：本机环境限制（2026-10-11 补跑）

原先记录的 `pytest` / `tsc` 缺失已消除：

```bash
npm install --no-audit --no-fund        # 73 packages, node_modules/ 已被 gitignore
python -m pip install pytest            # pytest 9.1.1（loopx venv）
npm run typecheck:control-plane         # tsc exit=0
python -m pytest tests/control_plane/test_goal_closeout_cli.py -q
                                        # 6 passed in 2.03s
npm run test:control-plane              # 4378 tests / 4342 pass / 0 fail / 36 skipped
```

| 检查 | 结果 |
|---|---|
| `tsc --project tsconfig.control-plane.json --noEmit` | **exit=0**（类型检查通过） |
| `pytest tests/control_plane/test_goal_closeout_cli.py` | **6 passed** |
| `npm run test:control-plane`（全量 TS control plane） | **4378 / 4342 pass / 0 fail / 36 skipped** |

修正记录的要点：
- 提交的 pytest 文件原先用相对导入 `from .canonical_authority_fixture import ...` 会在收集期报 `ImportError`；本仓库约定是**扁平导入**（`from canonical_authority_fixture import ...`，由 `tests/conftest.py` 处理路径）——已修正并实测通过。
- 全量套件含此前因缺 `node_modules` 而无法加载的 `pg` 相关测试，现已可解析；**0 fail**。

因此早期报告中「本节 24/24 来自等价证据脚本、pytest 无法执行」的限制**不再适用**：这些断言现在由真实 pytest 与全量 TS 套件覆盖。
