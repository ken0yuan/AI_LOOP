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
| `test_files[]` | 测试文件，相对源码仓 |
| `execution` | NPU 验证进展 |
| `report` | NPU 报告及其关联的 JUnit |
| `device_validation.npu.log` | NPU 原始日志（如有） |
| `device_validation.cuda.execution` | GPU 验证进展 |
| `device_validation.cuda.report` | GPU 报告及该次运行的环境、JUnit、审计和日志 |
| `device_validation.cuda.reason` | GPU 验证受阻原因（如有） |
| `notes` | 失败或未完成项的说明 |

沿用开发交接时已完成的本地提交，以 `git rev-parse HEAD` 取得 `REVIEWED_SHA`。`publish_pr.py` 只检查验证交付件存在，正文中的测试信息按填写模板从原始记录核对。

## 1. 选择模板并保存草稿

1. 按 `agentname` 选择模板。
   1. `api-adaptation` 使用 torch-npu 目标分支的 `.gitcode/PULL_REQUEST_TEMPLATE.md`，内容按 [API 适配填写模板](../templates/api-adaptation-pr-template.md)填写；栏目和顺序以仓库模板为准。
   2. 其他任务使用对应的仓库模板；目标分支没有适用模板时使用 [通用 PR 模板](../templates/pr-template.md)，API 任务仍沿用其专属填写规则。
2. 从目标分支 PR 模板（含 CheckList）及 `CONTRIBUTING.md` 查标题要求；没有规定时写清改动对象和内容。
3. 按模板填写，在 `RUN_DIR` 中以 UTF-8 保存为 `pr-title.txt`、`pr-body.md`，核对提交和关联 issue，清除占位符及填写提示。

用户只要草稿时，交付标题、正文和待补信息并结束；要求发布且开发、必要验证和审阅已完成时，继续下节。[已填写示例](../example/pr-module-call.md)

## 2. 发布并回读

在 AI_LOOP 根目录调用发布脚本，沿用已登录的 `gitcode` 和可向 fork 推送的 Git 凭据。

```bash
python3 -B scripts/publish_pr.py --run-dir "$RUN_DIR" --commit "$REVIEWED_SHA"
```

脚本核对来源 fork、开发分支和目标分支，将审阅提交推送到 fork，创建或更新匹配的 PR，再回读关联 issue、提交、正文和文件清单。成功后写入 `pr.json` 并返回 PR 链接。

中断、超时或返回 409 时，用同一 `RUN_DIR` 重试，由脚本先查询远端实际状态；结果不确定时保留待确认状态，不另建 PR。

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
