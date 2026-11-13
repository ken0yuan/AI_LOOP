# Step 1：分析需求

完成[Step 0.1](step0.1-pr_reconciliation.md)的本轮同步后，读取`data/run_data/<run-id>/`下的`basic_information.json`和`requirements.txt`，确认基础信息中的`preparation_status`为`INPUTS_READY`、`environment_status`为`ENV_READY`，以及`python`、`venv`、`activate`、`pytorch_source`和`torch_npu_latest_source`指向的文件或目录存在。所有本地路径均以AI_LOOP根目录为起点。缺少文件、状态未就绪或引用路径不存在时，停止并说明缺项。

## 问题分拣

前置条件满足后，读取本次`basic_information.json`中的`problem-description`，按下表进入对应的处理条目。

| 问题描述 | 处理条目 |
|---|---|
| ``API `<API>` needs adaptation``，例如``API `torch.Tensor.add` needs adaptation`` | [API 适配](#api-适配) |

## API 适配

进入[Step 2.1：API 适配前置准备](step2.1-api_prepare.md)，将本次`data/run_data/<run-id>/basic_information.json`和从问题描述中提取的完整API名称作为输入，按该子流程继续处理。
