---
document_type: Engineering PRD
status: DRAFT
title: LoopX Coding Runtime Router + GES Capability Injection + Jev MCP
  Integration PRD
version: v1.0
---

# 1. 文档信息

  -----------------------------------------------------------------------
  项                                  内容
  ----------------------------------- -----------------------------------
  产品                                LoopX

  项目                                feature/dsh-coding-worker 演进

  目标版本                            v1.0

  范围                                Phase 1 Runtime Adapter + Phase 2
                                      GES Integration + Jev MCP Model
                                      Routing

  非目标                              项目管理、人员管理、替代
                                      Multica、替代 Hermes
  -----------------------------------------------------------------------

------------------------------------------------------------------------

# 2. 背景与目标

## 2.1 背景

当前 LoopX 已支持 DSH Coding Worker。

现阶段架构：

    Hermes
      |
    LoopX
      |
    DSH
      |
    Repository

存在以下限制：

1.  Coding Runtime 与 LoopX 强绑定。
2.  无法根据任务类型选择不同 Coding Agent。
3.  无法动态注入仓库工程能力。
4.  无法根据任务选择不同模型。

目标架构：

    Hermes

      |

    LoopX

    Goal / Task / Handoff

      |

    Runtime Router

      |

    +---------+----------+---------+

    Codex   Cursor      DSH


      |

    GES Engineering Capability


      |

    Repository

------------------------------------------------------------------------

# 3. 产品边界

## 3.1 LoopX 负责

MUST:

-   Goal 管理
-   Task 管理
-   Handoff
-   Runtime 调度
-   Evidence 收集
-   执行状态记录

MUST NOT:

-   管理企业项目进度
-   替代 GitHub
-   管理工程技能内容
-   直接实现代码修改逻辑

------------------------------------------------------------------------

## 3.2 GES 负责

MUST:

-   分析 Repository
-   安装工程 Skills
-   生成工程能力 Manifest

MUST NOT:

-   保存任务状态
-   调度 Agent
-   执行代码

------------------------------------------------------------------------

## 3.3 Runtime 负责

Runtime 包括：

-   DSH
-   Cursor CLI
-   Codex CLI

职责：

-   执行代码任务
-   修改文件
-   执行测试
-   返回执行证据

------------------------------------------------------------------------

# 4. SOT（Source of Truth）

  数据          唯一事实源
  ------------- ----------------------
  Goal          LoopX
  Task          LoopX
  Runtime选择   LoopX Runtime Router
  工程能力      GES Manifest
  代码状态      Git Repository
  模型策略      LoopX Model Policy

禁止：

-   Runtime 自己创建任务状态
-   GES 修改任务状态
-   多 Runtime 保存第二份任务状态

------------------------------------------------------------------------

# 5. Phase 1 Runtime Adapter

## 5.1 目标

将 DSH 从固定 Worker 改造为通用 Runtime Provider。

新增：

    loopx/

    runtime/

     base.py

     dsh/

     cursor/

     codex/

------------------------------------------------------------------------

# 6. Runtime Interface

统一接口：

``` python
class RuntimeAdapter:

    def prepare(task):
        pass

    def execute(task):
        pass

    def collect_result():
        pass

    def handoff():
        pass
```

所有 Runtime 必须实现。

------------------------------------------------------------------------

# 7. DSH Adapter

保持已有能力。

输入：

    Task
    Workspace
    Prompt
    Skills

输出：

    Execution Result
    Commit
    Evidence

------------------------------------------------------------------------

# 8. Cursor CLI Adapter

新增：

    runtime/cursor/

支持：

-   workspace
-   prompt
-   stream output
-   session resume

执行模式：

    LoopX

    ↓

    Cursor Adapter

    ↓

    cursor-agent CLI

    ↓

    Repository

------------------------------------------------------------------------

# 9. Codex Adapter

用于：

-   架构设计
-   Code Review
-   安全分析

支持：

-   headless execution
-   structured output
-   evidence collection

------------------------------------------------------------------------

# 10. Runtime Profile

新增：

    profiles/

示例：

## frontend-coding

``` yaml
runtime: cursor

skills:
 - react
 - electron
 - ui-testing
```

## backend-coding

``` yaml
runtime: dsh

skills:
 - python
 - api
 - database
```

## architecture-review

``` yaml
runtime: codex

skills:
 - architecture
 - review
```

------------------------------------------------------------------------

# 11. Phase 2 GES Integration

## 11.1 目标

在 Runtime 执行前，根据 Repository 自动注入工程能力。

流程：

    Task

    ↓

    Repository Analyzer

    ↓

    GES

    ↓

    Capability Manifest

    ↓

    Runtime Execution

------------------------------------------------------------------------

# 12. GES Manifest

新增：

    .ges/context.yaml

示例：

``` yaml
stack:

 - electron
 - react
 - typescript

skills:

 - frontend-react
 - electron-pattern
 - testing
```

------------------------------------------------------------------------

# 13. Runtime Context Assembly

最终执行上下文：

    Task Context

    +

    Runtime Profile

    +

    GES Capability

    +

    Repository Context

生成：

    Execution Package

传递给 Runtime。

------------------------------------------------------------------------

# 14. Jev MCP Integration

## 14.1 目标

增加模型路由能力。

职责：

    Task

    ↓

    Model Policy

    ↓

    Jev MCP

    ↓

    Model Selection

------------------------------------------------------------------------

# 15. Jev 角色边界

Jev 不负责：

-   Task管理
-   Workflow
-   Runtime生命周期

Jev负责：

-   模型选择
-   推理策略
-   多模型调用

------------------------------------------------------------------------

# 16. Model Policy

示例：

``` yaml
frontend:

 runtime:
   cursor

 model:
   jev_policy:
     claude


backend:

 runtime:
   dsh

 model:
   jev_policy:
     deepseek


review:

 runtime:
   codex

 model:
   jev_policy:
     gpt
```

------------------------------------------------------------------------

# 17. Task Router

输入：

    Task Description

    Repository Context

    GES Manifest

输出：

``` json
{
 runtime:"cursor",
 profile:"frontend-coding"
}
```

------------------------------------------------------------------------

# 18. 状态模型

## Task状态

    CREATED

    ↓

    READY

    ↓

    RUNNING

    ↓

    WAITING

    ↓

    SUCCESS

    ↓

    FAILED

------------------------------------------------------------------------

# 19. Failure Semantics

## Runtime失败

处理：

1.  保存错误日志
2.  标记 Task Failed
3.  创建 Retry Evidence

禁止：

-   自动修改任务目标
-   丢失执行上下文

------------------------------------------------------------------------

## GES失败

处理：

1.  阻止 Runtime启动
2.  输出缺失能力
3.  请求重新安装

------------------------------------------------------------------------

# 20. Acceptance Criteria

## Runtime

AC-001:

LoopX 可以注册多个 Runtime。

AC-002:

同一个 Task 可以根据 Profile 调用不同 Runtime。

AC-003:

DSH/Cursor/Codex 返回统一 Evidence。

------------------------------------------------------------------------

## GES

AC-004:

Runtime启动前生成 Capability Manifest。

AC-005:

Skills注入结果可审计。

------------------------------------------------------------------------

## Jev

AC-006:

Task 可以通过 Policy选择模型。

AC-007:

模型选择结果保存 Evidence。

------------------------------------------------------------------------

# 21. Test Matrix

  测试                     结果
  ------------------------ ------
  DSH执行Backend任务       PASS
  Cursor执行Frontend任务   PASS
  Codex执行Review任务      PASS
  GES识别React项目         PASS
  Jev选择模型              PASS
  Runtime失败恢复          PASS

------------------------------------------------------------------------

# 22. Evidence要求

每次执行必须保存：

    execution_id

    task_id

    runtime

    model

    skills

    git_commit

    test_result

    logs

------------------------------------------------------------------------

# 23. Release Gate

Release 前必须满足：

1.  Runtime Adapter测试通过。
2.  GES Manifest生成正确。
3.  Runtime切换无代码修改。
4.  Evidence完整。
5.  Git状态可追踪。

------------------------------------------------------------------------

# 24. 后续扩展

未来：

    LoopX

     |

    Hatchet

     |

    Runtime Worker Pool

     |

    DSH/Cursor/Codex

     |

    Jev MCP

Hatchet仅用于企业级可靠执行，不进入第一阶段核心链路。

------------------------------------------------------------------------

# 25. Definition of Done

完成：

-   Runtime Adapter Framework
-   Cursor CLI Adapter
-   Codex Adapter
-   GES Capability Provider
-   Jev MCP Model Router
-   Runtime Profile
-   Evidence Pipeline

达到：

LoopX 成为多 Coding Runtime 调度中心。
