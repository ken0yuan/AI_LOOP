---
name: ai-loop
description: 按既定步骤编排 PyTorch API 的 NPU 适配准备、分支管理、任务分发及开发交接。
---

# AI_LOOP

## 进入和退出共享工作区

每次开始 AI_LOOP 任务，主控先从本 skill 的目录运行 `python3 -B scripts/workspace_lock.py enter`。确认命令成功后才进入以下步骤。锁忙时在当前步骤外等待或结束本轮，保留待办。把返回的任务票据记入本次任务上下文或运行记录，供结束和异常恢复使用。宿主提供稳定的任务进程 PID 时，可在 enter 命令附加 `--owner-pid <PID>`，使任务进程退出时自动释放锁；一次性工具 shell 的 PID 不适用。

主控在任务结束、取消或已保存待办准备等待外部结果时，先结束使用该工作区的子任务，再运行 `python3 -B scripts/workspace_lock.py leave <票据>`。同一任务内的子代理沿用主控持锁期。后续轮次恢复待办时重新 enter。返回 `inherited` 表示外层定时或回传进程已持锁，leave 会把释放工作留给外层。异常中断且无进程退出监视时，由恢复任务核实原任务已停止，凭原票据 leave。

接收适配需求，准备环境与API数据，安排开发任务，并跟踪issue、PR及合入后的收尾。

定时触发 PR 跟踪时，直接按 [Step 0.1 的定时入口](steps/step0.1-pr_reconciliation.md)执行合入收尾和开放 PR 评审，本轮在该步骤结束。

常规适配任务按以下顺序执行，完成当前步骤后，根据返回状态继续或结束。用户指定的跳过阶段和任务终点优先。

1. [Step 0：获取问题并准备环境](steps/step0-fetch_problems.md)
2. [Step 0.1：同步PR与开发记录](steps/step0.1-pr_reconciliation.md)
3. [Step 1：分析需求](steps/step1-analysis_requirements.md)
4. [Step 2.1：API适配前置准备](steps/step2.1-api_prepare.md)
5. [Step 3：版本控制](steps/step3-git_control.md)
6. [Step 3.1：任务分发](steps/step3.1-dispatcher.md)
7. [Step 4：适配开发与交接](steps/step4-subagent_development.md)，包含开发前提交issue、调用开发agent和完成后提交PR。
8. [Step 5：PR跟踪与分支管理](steps/step5-pr_tracking&branch_management.md)

按需读取当前步骤及其引用材料。主控按Step 4的交接表调用子代理并验收结果；下游技能的步骤和内部规则由子代理自行读取执行。
