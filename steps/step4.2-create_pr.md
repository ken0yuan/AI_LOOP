# Step 4.2：为一个 issue 创建或更新 PR

沿用 Step4.1 的 issue，同一 issue 已有对应 PR 时续用原 PR。

## 输入交付件

`RUN_DIR` 为 `AI_LOOP/data/run_data/<run-id>/`，下列 JSON 文件均位于该目录。

| 字段 | 用途 |
|---|---|
| `issue.json.repository` | 关联 issue 所在主仓 |
| `issue.json.number` | 关联 issue 编号 |
| `issue.json.url` | PR 关联的 issue 链接 |
| `issue.json.body` | 问题描述与验收目标 |
| `basic_information.json.venv` | 按 [Step 4 的目录规则](step4-subagent_development.md) 确定本批 API 的适配目标，与关联 issue 核对 |
| `basic_information.json.torch_npu_latest_source` | 源码仓路径，相对 AI_LOOP 根目录 |
| `adapt-information.json.adapt-plan.step1.agentname` | 选择对应任务的填写模板 |
| `adapt-information.json.development-branch` | 本次开发分支 |
| `adapt-information.json.task-branches[]` | 本分支负责的任务范围 |
| `adapt-information.json.api-status` | 发布时的适配进展 |
| `REVIEWED_SHA` | 已审阅并用于发布的 40 位提交 SHA |

旧 `issue.json` 缺少 `body` 时，按仓库和编号回读远端补齐。PR 的改动说明对照 `REVIEWED_SHA` 的实际提交差异。

### `api-adaptation` 的验证输入

从 `api-loop/result.json` 的每条 `results[]` 读取下列字段，报告路径相对 `RUN_DIR`。

| 字段（相对 `results[]`） | 用途 |
|---|---|
| `api` | 被验证的 API |
| `database_api`、`classification_source.table` | 定位本批 API 正名及策略表；nightly PR 据此填写分类表 |
| `test_files[]` | 测试文件，相对源码仓 |
| `execution` | NPU 验证进展 |
| `report` | NPU 报告及其关联的 JUnit |
| `device_validation.npu.log` | NPU 原始日志（如有） |
| `device_validation.cuda.execution` | GPU 验证进展 |
| `device_validation.cuda.report` | GPU 报告及该次运行的环境、JUnit、审计和日志 |
| `device_validation.cuda.reason` | GPU 验证受阻原因（如有） |
| `notes` | 失败或未完成项的说明 |

沿用开发交接时已完成的本地提交，以 `git rev-parse HEAD` 取得 `REVIEWED_SHA`。在 PR 的问题与目标中写明 PyTorch nightly 或固定版本的主次版本号；如果关联 issue 指向另一个适配目标，先回 Step 4.1 核对关联。核对每台设备报告的实际 PyTorch 构建，NPU 侧同时核对 torch-npu 版本。`publish_pr.py` 检查验证交付件存在，正文中的测试信息由主 agent 对照原始记录核实。

按 Step 4 的平台限制条件发布补测 PR 时，按设备将失败或待验证计入最终结果。在 PR 评论中说明受限调用、对应设备和报告依据，区分 API 本身与消费它的算子。

## 1. 选择模板并保存草稿

1. 对于 `api-adaptation`，读取本次 `basic_information.json.venv`，按下表选择填写模板；仓库要求的栏目和顺序以目标分支的 `.gitcode/PULL_REQUEST_TEMPLATE.md` 为准。

   | Step 0 的 venv（相对 AI_LOOP 根目录） | API 适配 PR 填写模板 |
   |---|---|
   | `data/venv/python-<Python版本>/` | [nightly 模板](../templates/api-adaptation-pr-template.md)，说明本次 nightly 构建验证了哪些 API |
   | `data/venv/torch-<torch版本>/python-<Python版本>/` | [固定版本模板](../templates/api-adaptation-fixed-pr-template.md)，写明目标主次版本、该版本专属 issue 和验证结论。[填写示例：PyTorch 2.15 flex_attention](../example/pr-flex-attention-2.15.md) |

   绝对路径按同一目录结构识别。固定版本环境即使安装了 nightly 的 torch-npu，也使用固定版本模板。其他任务按 `agentname` 使用对应的仓库模板；目标分支缺少模板时，使用[通用 PR 模板](../templates/pr-template.md)的栏目，API 内容仍按上表填写。
2. 从目标分支 PR 模板（含 CheckList）及 `CONTRIBUTING.md` 查标题要求；没有规定时写清改动对象和内容。
3. 仅 nightly API 适配 PR 在【修改方案】中填写分类表。按本 PR 负责的 API 正名，从 `data/api-support.sqlite3` 的 `API支持数据` 表读取 `分类 → 细分模块 → 细分场景` 和单独的 `API适配分类`。固定版本 PR 按固定版本模板说明改动和测试覆盖，无需填写分类表。
   - nightly 模板按 API 一行填写。对 `分类待审核=是` 的正名，从 `data/api-classification/classification-evidence.jsonl` 取 `category_reason`、`module_reason`、`scenario_reason`，让评审人看到每一层的分类依据。缺少依据时在对应项注明待补。
   - 若使用候选标签，在表后列出候选名称、父级、职责和适用边界，说明现有定义为何无法覆盖，并引用候选定义记录中的审核状态。同一候选列一次。详见[分类审核规则](../references/api-classification/rules.md)。
4. 【功能验证】使用所选模板的原列名和顺序，逐列填写。Ascend 910B4、NVIDIA RTX 5070 Ti、Ascend 950 各占一行。部分覆盖时列明已验证和待验证的数量，失败、错误和跳过按实填写。后续结果更新对应设备行。历史轮次、详细日志及辅助检查放在 PR 评论。
5. 按模板填写，在 `RUN_DIR` 中以 UTF-8 保存为 `pr-title.txt`、`pr-body.md`，核对提交和关联 issue，清除占位符及填写提示。

用户只要草稿时，交付标题、正文和待补信息并结束；要求发布且开发、必要验证和审阅已完成时，继续下节。[已填写示例](../example/pr-module-call.md)

## 2. 发布并回读

在 AI_LOOP 根目录调用发布脚本，沿用已登录的 `gitcode` 和可向 fork 推送的 Git 凭据。

```bash
python3 -B scripts/publish_pr.py --run-dir "$RUN_DIR" --commit "$REVIEWED_SHA"
```

脚本核对来源 fork、开发分支和目标分支，将审阅提交推送到 fork，创建或更新匹配的 PR，再回读关联 issue、提交、正文和文件清单。回读成功后，脚本保存本批 `pr.json`，并在 `data/pr-tracking.json` 按 PR 链接登记或更新一项。每项只含 `pr_url`、相对 AI_LOOP 根目录的 `run_data` 和远端状态 `pr_status`。

中断、超时或返回 409 时，用同一 `RUN_DIR` 重试，由脚本先查询远端实际状态；结果不确定时保留待确认状态，不另建 PR。

发布回读成功后，按 [Step 5 第五步](step5-pr_tracking&branch_management.md#第五步-触发-ai-检视并跟进合入)在 PR 底部触发 AI 检视。评审修复轮由 Step 5 在逐串答复后统一发送。

## 输出交付件

| 文件或字段 | 内容 |
|---|---|
| `pr-title.txt` | 单行 PR 标题 |
| `pr-body.md` | UTF-8 的 PR 正文 |
| `publish-state.json` | 发布中断后的恢复记录 |
| `pr.json.status` | PR 发布结果 |
| `pr.json.number` | 回读确认的 PR 编号 |
| `pr.json.url` | 交回用户的 PR 链接 |
| `pr.json.repository` | 接收 PR 的主仓 |
| `pr.json.base` | 目标分支，默认 master |
| `pr.json.fork` | 提供改动的 fork |
| `pr.json.head` | fork 的来源分支 |
| `pr.json.commit` | 已审阅并用于发布的提交 SHA |
| `pr.json.remote-sha` | 从远端来源分支核实的 SHA |
| `pr.json.issue-url` | 关联的 issue 链接 |
| `pr.json.run-id` | 本次运行目录名 |
| `pr.json.verification` | 验证交付件路径，默认 api-loop/result.json |
| `pr.json.api-status` | 发布时的适配进展 |
