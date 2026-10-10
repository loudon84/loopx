# DSH Coding Worker — Phase 0 版本/接口探测报告

> 状态：Phase 0 已完成主体探测，Gate P0 判定见 §7
> 日期：2026-10-10
> 对应 PRD：LoopX DSH Coding Worker v1.0（Phase 0）
> 探测主机：Ubuntu，用户 `cowork`

---

## 1. 版本锚点（本次实测）

| 组件 | 值 | 来源 |
|---|---|---|
| 目标仓库 | `/data/loopx` | PRD §12 |
| Git base（HEAD） | `2643d4ff79acfb5ae8730b11c329d7c6287e3262` | `git rev-parse HEAD` |
| Git describe | `desktop-main-38000237085-1-3-g2643d4ff7` | `git describe --tags --always` |
| 探测前工作区 | clean（`git status --short` 空） | `git status --short` |
| 开发分支 | `feature/dsh-coding-worker` | 本报告所用分支 |
| DSH CLI | `0.2.0-rc.2`（`/home/cowork/.npm-global/bin/dsh`） | `dsh --version` |
| `@deepseek-ai/dsh-headless` | `0.2.0-rc.2` | 发行包 `node_modules` |
| LoopX | `1.3.1`（release snapshot `20261010T030536Z`） | `loopx --version` / `loopx doctor` |
| LoopX canary | `/data/loopx/scripts/loopx`（与本仓库同一 checkout） | `loopx doctor` |
| `dsh-loopx-plugin` | `0.1.1-beta.6` | `~/.dsh/profiles/web/node_modules/dsh-loopx-plugin/package.json` |
| Node | `v24.21.0` | `node --version` |
| Python（系统） | `3.14.4` | `python3 --version` |
| Python（LoopX CLI 选用） | `3.12.13`（`/home/cowork/.local/bin/python3.12`） | `loopx doctor` |

与 PRD §1.2 的差异：PRD 记 B-01 为 `loopx doctor --agent-type deepseek-harness` 返回 `ok: True`；本次在**受限文件沙箱**下返回 `ok: False`，唯一失败项为 `global_registry_writable (required)`，报 `[Errno 30] Read-only file system: '/home/cowork/.loopx/.registry.global.json.6.write-probe.tmp'`。这是分析会话的沙箱产物，不是 LoopX 缺陷，详见 §3。在放开文件权限后 `~/.loopx` 写入正常（本次已实际写入 global registry）。

---

## 2. DSH 0.2.0-rc.2 Headless 兼容矩阵

规范优先级按 PRD：**本机已安装发行包实测 > 同版本准确源码 > 主分支文档**。下表 `实测` 一律给出本次可复现命令。

| # | 能力 | 期望（主分支文档） | 本机 0.2.0-rc.2 实测 | 状态 |
|---|---|---|---|---|
| C-01 | `headless` profile 随包发行 | 存在 | `@deepseek-ai/dsh-headless@0.2.0-rc.2` 存在；`dsh --profile headless --help` 正常打印 usage | ✅ 已验证 |
| C-02 | `--json` NDJSON 事件流 | 有 | 实测 13 行合法 NDJSON | ✅ 已验证 |
| C-03 | stdin 任务（省略或 `-`） | 有 | `printf '%s' "$TASK" \| dsh --profile headless --json -` 成功执行 | ✅ 已验证 |
| C-04 | `--session-id <id>` 恢复 | 有，未知 id 报错 | 选项存在；未知 id → **exit 1** + stdout `{"type":"error",...}` + stderr 同文 | ✅ 负路径已验证／⏳ 正路径未验证 |
| C-05 | 单进程执行一个任务片段后退出 | 有 | 进程自行退出，exit 0 | ✅ 已验证 |
| C-06 | 事件词表 `session/status/text/thinking/tool_call/tool_result/final` | 有 | 全部真实出现（见 §4） | ✅ 已验证 |
| C-07 | pre-turn 失败写 `error` 事件且**无** `final` | 有 | 未知 session 实测：仅 `error`，无 `final` | ✅ 已验证 |
| C-08 | `turn_end.reason` 作为终态判据 | 有 | 实测 `{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"completed"}}` | ✅ 已验证 |
| C-09 | 退出码映射（completed→0，其余→1） | 有 | completed → 0；未知 session → 1；发行源码 `dsh-headless/lib/index.js:345` `io.exit(outcome.reason?.kind === "completed" ? 0 : 1)` | ✅ 已验证 |
| C-10 | in-turn 失败仍以 `final`（常为空）收尾且无 `error` | 有 | — | ⏳ 未验证（需一次失败的真实 Turn） |
| C-11 | 字符串 8 KiB / 行 32 KiB 截断 + `truncated` 标记 | 有 | 发行源码 `dsh-headless/lib/json-stream-BA-F3lfb.js`：`MAX_STRING_BYTES = 8*1024`、`MAX_EVENT_BYTES = 32*1024`、`MAX_DEPTH = 64`，越界置 `truncated:true`；运行期未实际触发 | ✅ 源码级确认／⏳ 运行期未验证 |
| C-12 | 真实工具执行（`tool_call`/`tool_result` 配对） | 有 | 真实执行 `echo LOOPX-PROBE-OK`，`callId` 配对，`status:"completed"` | ✅ 已验证 |
| C-13 | token 用量上报 | 未在 PRD 断言 | `step_end.usage` 给出 `inputTokens/outputTokens/cacheReadTokens/cacheWriteTokens/totalTokens` | ✅ 已验证（对 FR-15 有利） |
| C-14 | Headless composition 挂载编码工具 | 未逐项断言 | `--dump-config`：`tool-bash/tool-fs/tool-fs-search/tool-skill/skill/skill-filesystem/tool-todo/tool-goal/tool-subagent/tool-workflow/tool-web` + `sandbox/sandbox-policy/bash-sandbox/fs-sandbox` | ✅ 已验证 |
| C-15 | Headless 具备 Session 持久化与查询 | 未断言 | composition 含 `session-persistence-jsonl`、`session-query-sqlite`、`session-projection` | ✅ 已验证 |
| C-16 | Session 落盘 | 未断言 | `~/.dsh/sessions/--home-cowork-Documents-deepseek-harness-default-workspace--/session-eb12661f-.../session.v4.jsonl.zstd`（16242 B） | ✅ 已验证 |
| C-17 | 真实模型调用可用（凭据可达） | 未断言 | 使用默认 `DSH_HOME=~/.dsh` 完成一次真实调用，wall≈3s，exit 0 | ✅ 已验证 |
| C-18 | 自定义 Skills 在 headless 可用 | PRD §2.2 不承诺 | — | ⏳ 未逐项验证 |
| C-19 | 跨进程 `--session-id` 正路径恢复 | 有 | — | ⏳ **未验证**（需第二次真实调用，当前仅获批一次） |

**结论：Plan A（AD-02 首选：`dsh --profile headless --json -` 子进程）在本机 0.2.0-rc.2 上成立**，不需要启用备选 B（SDK/JSON-RPC 适配）。

---

## 3. 环境约束（分析会话沙箱产物，非 DSH/LoopX 缺陷）

DSH 每次启动（**包括 `--help`**）都会重写 `$DSH_HOME/profiles/<name>/cordis.yml`：

```js
// @deepseek-ai/dsh/lib/profile-boot-BZ2ZjNWi.js:189 (prepareProfile)
writeFileSync(join(profile.dir, PROFILE_ROOT_FILENAME), PROFILE_ROOT_CONFIG);
```

在 workspace-write 沙箱下、且 `DSH_HOME=~/.dsh`（在会话工作区之外）时：

```
Error: EROFS: read-only file system, open '/home/cowork/.dsh/profiles/headless/cordis.yml'
```

同类产物：

- `dsh --profile headless --help` → EROFS（即使该 profile 目录已存在）。
- `loopx doctor` → `ok:False`，唯一失败项 `global_registry_writable`。
- `loopx chat-endpoint inspect-steward` → `{"ok":false,"error":"[Errno 30] Read-only file system: '/home/cowork/.loopx/chat'"}`。

**规避方式（已验证）**：把 `DSH_HOME` 指向可写目录后 headless 正常引导；放开文件权限后，使用真实 `DSH_HOME` 的真实模型调用也成功（§4 证据即由真实 `DSH_HOME` 产生）。

**对实现的强制含义**：Coding Worker 拉起 `dsh` 子进程时必须保证 **`DSH_HOME` 可写**，否则每次 Turn 都会在引导阶段 EROFS 失败。该约束须进入 `runtime_probe.py` 的可用性检查。

---

## 4. 实测 `--json` 事件 Schema（真实调用原始证据）

命令（等价）：`printf '%s' "$TASK" | dsh --profile headless --json -`
实测结果：`exit=0`，`wall_seconds=3`，stdout 1261 B / 13 行，stderr 空。完整事件流：

```json
{"type":"session","sessionId":"session-eb12661f-0101-4945-be3f-9a3d6707dab2","cwd":"/home/cowork/Documents/deepseek-harness/default-workspace"}
{"type":"status","phase":"turn_start","turn":1}
{"type":"status","phase":"step_start","turn":1,"step":1}
{"type":"thinking","text":"The user gave a simple, explicit instruction: ..."}
{"type":"tool_call","callId":"call_00_1xSmFaMTV6Uu6UrtZOEZ8930","tool":"bash","input":{"command":"echo LOOPX-PROBE-OK","description":"Echo the probe string"}}
{"type":"tool_result","callId":"call_00_1xSmFaMTV6Uu6UrtZOEZ8930","status":"completed","result":"LOOPX-PROBE-OK\n"}
{"type":"status","phase":"step_end","turn":1,"step":1,"usage":{"inputTokens":7501,"outputTokens":95,"cacheReadTokens":0,"cacheWriteTokens":0,"totalTokens":7596}}
{"type":"status","phase":"step_start","turn":1,"step":2}
{"type":"thinking","text":""}
{"type":"text","text":"LOOPX-PROBE-OK"}
{"type":"status","phase":"step_end","turn":1,"step":2,"usage":{"inputTokens":193,"outputTokens":9,"cacheReadTokens":7424,"cacheWriteTokens":0,"totalTokens":7626}}
{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"completed"}}
{"type":"final","text":"LOOPX-PROBE-OK"}
```

失败路径原始证据（keyless，未知 session）：

```
$ dsh --profile headless --session-id session-does-not-exist-xyz "say hi"        # exit=1
stderr: dsh: session "session-does-not-exist-xyz" does not exist; omit --session-id to start a new Session

$ dsh --profile headless --json --session-id session-does-not-exist-xyz "say hi"  # exit=1
stdout: {"type":"error","message":"session \"session-does-not-exist-xyz\" does not exist; omit --session-id to start a new Session"}
stderr: (同上)
```

### 对 NDJSON codec 的可执行结论

- 事件判别键是 `type`；`status.phase ∈ {turn_start, step_start, step_end, turn_end}`。
- **完成判据必须同时用** exit code（0）与 `status(phase=turn_end).reason.kind == "completed"`；不得只凭 `final` 存在。
- `tool_call.callId` ↔ `tool_result.callId` 是唯一配对键；`tool_result.status` 是工具级结论。
- `thinking.text` 可能为空串，`text` 可能只出现一次——不得以 `text` 缺失判定失败。
- `usage` 只在 `step_end` 出现，可累加得到 Turn 用量（FR-15）；缺失时必须标注而非推算。
- `session.cwd` 可用于与 LoopX 授权的 workspace identity 做交叉校验（FR-13/AC-05）。
- 未知 `type` 必须 fail closed 记录，不得静默忽略（FR-06）。

---

## 5. LoopX 侧调用链与文件级改造清单

（见同目录 `dsh-coding-worker-file-change-list.md`；本节由同一次 Phase 0 追踪产出。）

### 5.1 已验证的前置事实：现有 DSH Turn 走旧 Python SDK，不是 npm CLI 0.2

PRD §1.3 / AD-02 的关键前提已在本仓库确认：

- `pyproject.toml:30`：`"deepseek-harness-sdk==0.1.5rc1"` —— LoopX 依赖并锁定**旧 Python SDK**。
- `loopx/dsh_goal_mode/turn_host_adapter.py:238-241`：`from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig`，缺失时报 `deepseek-harness-sdk is required; install it with ...`。
- `loopx/dsh_goal_mode/README.md:125`：明确写的是 “validated `deepseek-harness-sdk==0.1.5rc1` API, or a compatible runner”。
- CLI 入口：`loopx/cli_commands/turn_dsh_host.py:8,33` → `DshHostConfig` / `run_dsh_host`。

**结论**：`dsh` 0.2.0-rc.2 的 npm CLI 与 LoopX 现有 DSH Turn 路径是两条不同技术栈，**新增独立 host route/adapter 确有必要**，且不得以旧 SDK 调用冒充新 CLI 调用（AD-02 不采用项、PRD 一票否决条件）。


---

## 6. 尚需评审的未验证项（不得默认通过）

| 编号 | 未验证项 | 影响 | 需要的动作 |
|---|---|---|---|
| U-01 | `--session-id` 跨进程正路径恢复 | FR-13 / AC-17 / R-03 | 需第二次获批的真实调用 |
| U-02 | in-turn 失败仍发 `final` 且无 `error` | FR-06 / AC-11 | 需一次失败的真实 Turn |
| U-03 | 8 KiB/32 KiB 截断与 `truncated` 的运行期表现 | FR-06 | 常量与置位逻辑已按**发行源码**确认（C-11）；仍需构造大输出的 keyless 用例实测 |
| U-04 | Headless 下自定义 Skills 可用性 | R-02 / FR-01 | 逐项探测 |
| U-05 | 并发/孤儿进程与 SIGTERM 语义 | FR-10/FR-14 | Phase 1 实现后验证 |

---

## 7. Gate P0 判定

**PASS（有条件）**：Headless CLI 需要的全部关键选项在**本机已安装的 0.2.0-rc.2 上实测存在且可用**，AD-02 首选路线（A）成立；无需退回备选 B，也不需要以旧 Python `deepseek-harness-sdk==0.1.5rc1` 替代。

进入 Phase 1 的前置条件：

1. `DSH_HOME` 可写性必须成为 Worker 可用性探测与启动的硬前提（§3）。
2. U-01/U-02 在 Phase 1 的真实受控 Turn 中一并补齐（Phase 1 本就需要真实 Turn）。
3. 不得在未验证前把 U-01..U-05 当作已支持能力写入 capability 声明。

---

## 8. 原始证据索引

| 证据 | 路径 |
|---|---|
| 主分支/发行包帮助输出 | `.dsh-scratch/phase0/` 分析工作区内（会话工作区） |
| Headless composition dump | 会话工作区 `.dsh-scratch/phase0/headless-config.yml`（96 行） |
| 真实 NDJSON 事件流 | 会话工作区 `.dsh-scratch/phase0/probe.ndjson` |
| 失败路径 stdout/stderr | 会话工作区 `.dsh-scratch/phase0/b.out`、`b.err` |
| LoopX 引导包（dry-run） | 会话工作区 `.dsh-scratch/sg-loopx.json`、`sg-bound.json` |
| LoopX 目标注册回执 | `/data/loopx/.loopx/registry.json`、`~/.loopx/registry.global.json` |

> 说明：Phase 0 的中间证据保存在分析会话工作区，未写入 `/data/loopx` 以保持目标仓库干净（仅本报告与改造清单属于交付物）。
