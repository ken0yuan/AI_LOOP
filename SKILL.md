---
name: ai-loop
description: 按既定步骤编排 PyTorch API 的 NPU 适配准备、分支管理、任务分发及开发交接。
---

# AI_LOOP

接收适配需求，准备环境与API数据，安排开发任务，并跟踪issue、PR及合入后的收尾。

按以下顺序执行，完成当前步骤后，根据返回状态继续或结束。用户指定的跳过阶段和任务终点优先。

1. [Step 0：获取问题并准备环境](steps/step0-fetch_problems.md)
2. [Step 0.1：同步PR与开发记录](steps/step0.1-pr_reconciliation.md)
3. [Step 1：分析需求](steps/step1-analysis_requirements.md)
4. [Step 2.1：API适配前置准备](steps/step2.1-api_prepare.md)
5. [Step 3：版本控制](steps/step3-git_control.md)
6. [Step 3.1：任务分发](steps/step3.1-dispatcher.md)
7. [Step 4：适配开发与交接](steps/step4-subagent_development.md)，包含开发前提交issue、调用开发agent和完成后提交PR。
8. [Step 5：PR跟踪与分支管理](steps/step5-pr_tracking&branch_management.md)

按需读取当前步骤及其引用材料。主控按Step 4的交接表调用子代理并验收结果；下游技能的步骤和内部规则由子代理自行读取执行。
