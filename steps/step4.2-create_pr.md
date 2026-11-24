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

## 1. 确定发布哪个提交

每个 PR 相对目标分支保留一个普通提交。在本批开发分支上操作：

- **首次发布**：把已验收的改动一起提交。已有一个符合范围的提交就直接沿用。
- **更新原 PR**：修改前核对 fork 和原分支，把远端完整 SHA 记为 `EXPECTED_HEAD`。在此基础上暂存代码改动，用 `git commit --amend --no-edit` 更新原提交。仅改正文或验证说明且原 PR 已是单提交时，沿用原 SHA。

**已有多个提交时**，先确认目标分支正确、预期文件均已提交且工作区干净，再整理：

1. 抓取目标分支到 `FETCH_HEAD`，用 `git rev-parse HEAD` 记下整理前的 `OLD_HEAD`。
2. 用 `git merge-base --all FETCH_HEAD "$OLD_HEAD"` 查共同祖先。仅当结果唯一时，将它记为 `PARENT`，运行 `git reset --soft "$PARENT"`，再用 `git commit -m "<本 PR 的改动摘要>"` 生成一个提交。
3. 用 `git diff --exit-code "$OLD_HEAD" HEAD --` 确认整理前后的文件内容一致。

整理完成后，审阅相对目标分支的最终差异，用 `git rev-parse HEAD` 记下本次要发布的 `REVIEWED_SHA`。

## 2. 核对哪些验证结果可以沿用

1. **对文件。** 以 `REVIEWED_SHA` 中保留的测试和所用辅助文件为准，按路径核对报告里的 SHA256。推送前，`publish_pr.py` 自动核对 GPU 原始报告中的测试文件哈希，以及已登记辅助文件的 manifest 哈希。
2. **对设备和范围。** 主控核对每份报告的设备型号、实际 PyTorch 构建、执行范围及影响结果的依赖或实现；NPU 还要核对 torch-npu 构建。文件和运行条件都匹配，才沿用该设备的结果。仅删除文件时，从原始记录汇总保留用例，并注明原 JUnit 和退出码对应的运行范围。
3. **记结果和缺口。** 按设备写明已验证和待验证数量。实测失败照实记失败；还在排队的列为待复测，设备不可访问的注明受阻原因。允许部分覆盖发布时，在 GPU 行的结果栏标明“待验证”及具体范围。

GPU 验证缺少匹配证据时，主控在本批记录待复测的文件，交给原任务的验证流程处理。已有复测在运行时，继续跟踪该次运行。取得报告后，核对它覆盖原 PR 当前提交中的哪些文件，再更新 GPU 验证行。

发布前按 Step 4 核对交付范围：已确认不支持的 API 或场景，排除其对应测试，PR 正文和验证统计只写保留项。保留项仅因其他设备不可访问或复测尚在排队时，在对应设备行写明待测范围和原因。

## 3. 选择模板并保存草稿

按本批计划项的 `agentname` 选择填写模板。栏目、顺序和 CheckList 以目标分支的 `.gitcode/PULL_REQUEST_TEMPLATE.md` 为准；目标分支缺少模板时，使用[通用 PR 模板](../templates/pr-template.md)的栏目。

### API适配

API 适配 PR 在“问题与目标”中写明 PyTorch nightly 或固定版本的主次版本号。若关联 issue 的适配目标不同，先回 Step 4.1 核对。

读取本次 `basic_information.json.venv`，按下表选择 API 适配模板。即使使用通用栏目，API 内容仍按对应版本填写。

| Step 0 的 venv（相对 AI_LOOP 根目录） | API 适配 PR 填写模板 |
|---|---|
| `data/venv/python-<Python版本>/` | [nightly 模板](../templates/api-adaptation-pr-template.md)，说明本次 nightly 构建验证了哪些 API |
| `data/venv/torch-<torch版本>/python-<Python版本>/` | [固定版本模板](../templates/api-adaptation-fixed-pr-template.md)，写明目标主次版本、该版本专属 issue 和验证结论。[填写示例：PyTorch 2.15 flex_attention](../example/pr-flex-attention-2.15.md) |

绝对路径按同一目录结构识别。固定版本环境即使安装了 nightly 的 torch-npu，也使用固定版本模板。

仅 nightly API 适配 PR 在【修改方案】中填写分类表。按本 PR 负责的 API 正名，从 `data/api-support.sqlite3` 的 `API支持数据` 表读取 `分类 → 细分模块 → 细分场景` 和单独的 `API适配分类`。固定版本 PR 按固定版本模板说明改动和测试覆盖，无需填写分类表。

- 当前 PR 最终保留的每个 API 正名写一行。「分类原因」按库内的 `分类待审核` 填写：为「否」时按 nightly 模板留空；为「是」时从 `data/api-classification/classification-evidence.jsonl` 取 `category_reason`、`module_reason`、`scenario_reason`，在同一格按分类、模块、场景各写一句。缺哪层依据就注明哪层待补。
- 若使用候选标签，在表后列出候选名称、父级、职责和适用边界，说明现有定义为何无法覆盖，并引用候选定义记录中的审核状态。同一候选列一次。详见[分类审核规则](../references/api-classification/rules.md)。

### 通用填写与保存

正文中的开发做法、测试验证及评审回复写法，见[PR 正文和评论怎么写](../references/pr-writing.md)。

1. 从目标分支 PR 模板（含 CheckList）及 `CONTRIBUTING.md` 查标题要求；没有规定时写清改动对象和内容。
2. 【功能验证】使用所选模板的原列名和顺序，逐列填写。默认 NPU、GPU 各一行，硬件型号不同时增行，同型号仅保留最终结果。部分覆盖时列明已验证和待验证的数量，失败、错误和跳过按实填写。后续结果更新对应设备行。历史轮次和详细日志留在批次记录，评审需要时在相关评论中引用。
3. 首次发布按模板起草。更新原 PR 时先读取线上最新正文，按当前模板整理本次涉及的段落。在 `RUN_DIR` 中以 UTF-8 保存为 `pr-title.txt`、`pr-body.md`，核对提交和关联 issue，清除占位符、填写提示、内部单据、服务器绝对路径和认证信息。

```bash
python3 -B scripts/render_gpu_result.py "$RECEIPT" --draft "$RUN_DIR/pr-body.md" \
  --plan "$DEVICE_PLAN" --source "$SOURCE_REPO" --commit "$REVIEWED_SHA"
```

固定版本和 nightly 都用这条命令，每次更新一个 GPU 或 NPU 型号的验证行。

- **全量测试**：按当前提交在该型号上的完整用例清单填结果。
- **局部补测**：JUnit 和退出码填本轮。重跑文件取本轮结果，其余文件用 `--reuse` 沿用已核实的同型号记录，缺少证据的用例计为待验证。

按当前结果重写表下对应设备的说明。回执字段、历史日志和 `--reuse` 示例见[设备验证回执](../references/device-verification-receipts.md)。

用户只要草稿时，交付标题、正文和待补信息并结束；要求发布且开发、必要验证和审阅已完成时，继续下节。[已填写示例](../example/pr-module-call.md)

## 4. 发布并回读

首次发布、补测和 Step 5 更新都走本节入口。格式检查失败时，按报错修正草稿后重试。

在 AI_LOOP 根目录按发布场景选择一条命令。

```bash
# 首次发布
python3 -B scripts/publish_pr.py --run-dir "$RUN_DIR" --commit "$REVIEWED_SHA"
# 更新原 PR
python3 -B scripts/publish_pr.py --run-dir "$RUN_DIR" --commit "$REVIEWED_SHA" --expected-head "$EXPECTED_HEAD"
```

脚本核对来源 fork、开发分支和目标分支，并检查目标分支到审阅提交的范围内恰有一个普通提交。覆盖原 PR 分支时，Git 只在远端仍指向 `EXPECTED_HEAD` 时接受推送。远端已经前进时，重新核对差异、整合改动并验证后再发布。远端已是审阅提交时，直接继续回读。脚本创建或更新匹配的 PR，再回读关联 issue、提交、正文和文件清单。回读成功后，脚本保存本批 `pr.json`，并在 `data/pr-tracking.json` 按 PR 链接登记或更新一项。每项只含 `pr_url`、相对 AI_LOOP 根目录的 `run_data` 和远端状态 `pr_status`。

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
