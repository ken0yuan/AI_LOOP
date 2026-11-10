# Step 3.1：任务分发器

读取本次任务，确定执行顺序及各agent负责的任务，将计划写入`adapt-information.json`的`adapt-plan`。本步只生成计划，后续由[Step 4](step4-subagent_development.md)按计划调用agent执行。

## 输入

输入只有同一run-id下的两份文件：

| 文件 | 用途 |
|---|---|
| `basic_information.json` | 本次任务的基础信息，以及源码、环境的位置 |
| `adapt-information.json` | Step 3处理后剩余的`task-branches`、实际开发分支及准备结果 |

只有Step 3返回`BRANCH_READY`且`stop-task=false`才进入本步。情况一已经直接结束，不再编排；存在冲突或失败时，先完成Step 3。

从AI_LOOP根目录执行：

```bash
python3 -B scripts/dispatch_adaptation_tasks.py \
  --basic-information data/run_data/<run-id>/basic_information.json \
  --adapt-information data/run_data/<run-id>/adapt-information.json
```

## 输出：adapt-plan

在输入的`adapt-information.json`中添加`adapt-plan`，保留其他任务信息。计划中的每一项只包含：

| 字段 | 含义 |
|---|---|
| `agentname` | 负责执行这一项的agent名称 |
| `task-branches` | 交给这个agent的任务标识数组 |

当前将整批`adapt-<API>`任务交给一个`api-adaptation` agent，生成一个`step1`，保留任务数组的原顺序。包含其他类型任务时，停止编排并说明需要补充对应agent的分配规则。

例如：

```json
{
  "task-branches": ["adapt-Tensor.add", "adapt-view"],
  "development-branch": "adapt-set-a31f92c8d570",
  "adapt-plan": {
    "step1": {
      "agentname": "api-adaptation",
      "task-branches": ["adapt-Tensor.add", "adapt-view"]
    }
  }
}
```

`api-adaptation`表示API适配agent。源码、环境和实际开发分支从输入文件读取，不在计划中重复保存，也不另存`apis`、`prompt`或`dispatcher`。

## 计划编号与执行顺序

这里的`step`编号只表示计划内的执行顺序，与AI_LOOP自身的步骤编号无关。

- `step1`表示第一组，先由其中指定的agent完成任务。
- `step2.1`、`step2.2`表示第二组中的两项任务分配，二者没有先后要求，可以先执行任意一项。
- 第一组全部完成后，才能进入第二组；第二组全部完成后，才能进入第三组，依此类推。某项失败时先处理该项，不继续后面的组。
- **当前所有agent仍逐个串行执行。** 同组的`.1`、`.2`只表示可以乱序执行，不同时启动多个agent操作源码。
