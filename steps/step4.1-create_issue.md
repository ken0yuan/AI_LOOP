# Step 4.1：创建或复用 issue

在开发前整理问题材料，创建或复用目标主仓的 issue，完成后交回 Step4。

## 输入交付件

`RUN_DIR` 为 `AI_LOOP/data/run_data/<run-id>/`，下列 JSON 文件均位于该目录。

| 前序步骤 | 字段 | 用途 |
|---|---|---|
| Step0 | `basic_information.json.problem-description` | 问题描述与任务目标 |
| Step0 | `basic_information.json.torch_npu_latest_source` | 源码仓路径，读取贡献要求 |
| Step0 | `basic_information.json.preparation_config.torch_npu_repo` | 接收 issue 的主仓 |
| Step0 | `basic_information.json.chip_type` | 已记录的设备型号 |
| Step0 | `basic_information.json.venv` | 按 Step 4 的目录规则确定 API 适配目标及策略表 |
| Step0 | `basic_information.json.pytorch_version` | 已准备的 PyTorch 版本 |
| Step0 | `basic_information.json.torch_npu_version` | 已准备的 torch-npu 版本 |
| Step3.1 | `adapt-information.json.adapt-plan.step1.agentname` | 选择对应任务的模板 |
| Step3.1 | `adapt-information.json.adapt-plan.step1.task-branches[]` | 本次处理对象 |
| 本步续跑 | `issue.json.repository` | 已有 issue 所在仓库 |
| 本步续跑 | `issue.json.number` | 已有 issue 编号 |

### `api-adaptation` 的策略输入

将 `task-branches[]` 每项去掉 `adapt-` 前缀，按 [Step 4 的 venv 对照表](step4-subagent_development.md) 在 `AI_LOOP/data/api-support.sqlite3` 选择本次表。固定版本只读对应版本表；目标表或 API 缺失时交回具体缺项。

| 字段 | 用途 |
|---|---|
| `API名称`、`API真正名字` | 在所选表匹配 API，别名定位到同表正名 |
| 正名行的 `适配策略` | 确定本次任务目标和验证计划 |

## 1. 选择模板并保存草稿

1. 按 `agentname` 选择对应的 issue 模板。
   1. `api-adaptation` 使用 [templates/issue-template.md](../templates/issue-template.md)。
   2. 其他任务未配置模板或对应文件不存在时，也使用该默认模板；默认模板缺失时交回缺失路径。
2. 按模板填写标题和正文，在 `RUN_DIR` 中以 UTF-8 保存为 `issue-title.txt`、`issue-body.md`。
3. 核对标题为非空单行、任务范围与输入一致，清除占位符和填写提示。

用户只要草稿时，交付两份文件并结束。材料不足时，交回草稿及缺项；要求发布且核对通过时，继续下节。

## 2. 查重并发布

在 AI_LOOP 根目录调用 `scripts/publish_issue.py`，沿用同一 `RUN_DIR`。

1. 已有 `issue.json` 时，按其中的仓库和编号查询；已有交接链接时传入 `--issue-url`。
2. 未找到对应 issue 时，由 AI 根据标题及任务对象选择查询词，每词传一个 `--keyword`，搜索开放和已关闭的 issue。
   1. API 适配按目标 PyTorch 版本分开建 issue。用完整 API 名称搜索，逐条核对候选 issue 的目标版本。例如，同一个 API 的 nightly 任务和固定版本任务各用一个 issue；两个不同的主次版本也各用一个 issue。固定版本按主次版本查重，例如 `2.14.0+cpu` 的适配目标写作 `PyTorch 2.14`。
3. 只有目标主仓、问题、处理对象和验收目标一致才复用；API 适配还要求候选 issue 专门跟踪本次目标版本。只有其他版本的 issue 时，为本次版本创建新 issue。旧 issue 未注明版本时先核对关联材料。

| 操作 | 命令 |
|---|---|
| 按已有记录查询 | `python3 -B scripts/publish_issue.py search --run-dir "$RUN_DIR"` |
| 按交接链接查询 | `python3 -B scripts/publish_issue.py search --run-dir "$RUN_DIR" --issue-url "$ISSUE_URL"` |
| 按 AI 选择的关键词查询 | `python3 -B scripts/publish_issue.py search --run-dir "$RUN_DIR" --keyword "$QUERY"` |
| 复用相同问题 | `python3 -B scripts/publish_issue.py reuse --run-dir "$RUN_DIR" --number "$NUMBER"` |
| 确认没有相同问题后创建 | `python3 -B scripts/publish_issue.py create --run-dir "$RUN_DIR"` |

查询返回 `status=searched`、`issues[]`；每条含 `number`、`title`、`body`、`state`、`html_url`。查询词和搜索结果可分次累积，判断由 AI 完成。仅在已获授权用草稿替换已有正文时，给 `reuse` 加 `--update-body`。

发布返回 `searched` 时重新比对；`pending` 时回查原操作，避免重复创建；`error` 时按错误处理。查询失败或无法判断是否同一问题时，保留草稿及查到的编号，不创建新 issue。

## 输出交付件

脚本创建、复用或更新后回读远端，保存最终正文到 `issue.json.body`。将 `issue.json.url` 交回 Step4，独立调用时交回调用方。

| 文件或字段 | 内容 |
|---|---|
| `issue-title.txt` | 单行 issue 标题 |
| `issue-body.md` | UTF-8 的 issue 草稿 |
| `issue.json.status` | 本次创建或复用的结果 |
| `issue.json.repository` | 接收 issue 的主仓 |
| `issue.json.number` | 回读确认的 issue 编号 |
| `issue.json.url` | 回读确认的 issue 链接 |
| `issue.json.title` | 远端标题 |
| `issue.json.body` | 远端最终正文，供 Step4.2 使用 |
| `issue.json.run-id` | 本次运行目录名 |
