---
name: ai-loop
description: 按既定步骤编排 PyTorch API 的 NPU 适配准备、分支管理、任务分发及开发交接。
---

# AI_LOOP

## 全局约束

当前步骤完成并核验后才进入下一步，仅按需读取本技能材料。下游技能入口、步骤及引用文档由对应子代理读取；主控只按交接表传参、验收产物，不预读或代做。等待、失败时不得提前推进。

依次按以下既有步骤处理用户输入，并依据各步状态决定是否继续：

1. [Step 0：获取问题并准备环境](steps/step0-fetch_problems.md)
2. [Step 1：分析需求](steps/step1-analysis_requirements.md)
3. [Step 2.1：API 适配前置准备](steps/step2.1-api_prepare.md)
4. [Step 3：版本控制](steps/step3-git_control.md)
5. [Step 3.1：任务分发](steps/step3.1-dispatcher.md)
6. [Step 4：适配开发与交接](steps/step4-subagent_development.md)

用户明确指定的跳过阶段和任务终点优先于步骤文件中的默认流程。
