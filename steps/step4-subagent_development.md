# Step 4：适配开发

## 输入

接收AI_LOOP根目录和本次`data/run_data/<run-id>/`目录的位置。进入本步时，环境和开发分支已准备好，Step 3.1已将`adapt-plan`写入本次`adapt-information.json`。

本次运行目录提供`basic_information.json`、`requirements.txt`、`adapt-information.json`及已有问题、PR、CI等材料。主agent按以下三步完成本批任务。

## 第一步：提交issue

执行[Step 4.1：提交issue](step4.1-create_issue.md)，传入问题描述、问题分析及补充证据的实际文件路径，并提供本次运行目录和目标仓。材料可以合并为一份报告，也可以是多份文件，不固定文件名。主agent先按适用的issue模板整理正文，核对材料和缺项后再创建或复用issue，将链接保存到本次运行目录；核心材料缺失时返回补充，不启动开发。

## 第二步：编排并调用subagent

### 1. 编排调用顺序

读取本次`adapt-information.json`的`adapt-plan`，按[Step 3.1的编号规则](step3.1-dispatcher.md)整理执行顺序：先执行第一组，再执行第二组，依次推进；同组可乱序，当前所有subagent仍逐个串行调用。

每项按`agentname`选择subagent，执行范围为该项的`task-branches`。当前串行调用subagent，使用前台调用，等待其返回后再验收结果并继续下一项；失败或未完成时先处理当前项。

### 2. 准备通用交接内容

本节交接表即为主控调用依据，无需预读下游技能文档。下游的输入解释、策略选择和内部规则由对应子代理自行读取处理。

每次调用都交付当前计划项，并明确以下信息的来源。交接前确认来源可访问，由subagent从来源读取实际内容，不另存一份环境、源码或分支配置。

| 通用信息 | 提供的地址或读取位置 |
|---|---|
| 路径基准 | AI_LOOP根目录的绝对路径；基础信息中的相对路径均以此解析 |
| 本次运行目录 | 完整run-id目录的绝对路径；其中全部文件和子目录均可按需读取，后续新增材料自动纳入 |
| 本次任务与问题 | 当前计划项；`basic_information.json`的`problem-description`及运行目录中的issue、PR、CI等材料 |
| 已安装环境 | `basic_information.json`的`python`、`venv`、`activate`及环境准备状态；`requirements.txt`为本次依赖清单 |
| 两份源码 | `basic_information.json`的`pytorch_source`、`torch_npu_latest_source`，以及同文件中的版本和提交信息 |
| 实际开发分支 | `adapt-information.json`的`development-branch`；整批沿用该分支 |

### 3. 按subagent类型准备专属交接内容

当前支持`api-adaptation`：由该subagent使用已安装的`api-adaptation` skill处理任务。除通用内容外，还要明确：

| 专属信息 | 提供的来源与约定 |
|---|---|
| 待处理API | 当前计划项的`task-branches`，去掉`adapt-`前缀得到API名称，保持本项原顺序和范围 |
| API策略表 | 交接 `data/api-support.sqlite3` 的绝对路径及本次表名，按下表从 `basic_information.json.venv` 选取。按 `API名称` 匹配，在同表通过 `API真正名字` 定位正名并读取 `适配策略`、`API适配分类`；本批分类反馈也归该表 |
| 结果位置 | 本次run-id目录为任务结果目录，逐API结果保存为`api-loop/result.json`；测试文件写入本次torch_npu源码仓 |
| 逐API状态 | subagent把进度写到本次`adapt-information.json`的`api-status`；测试通过为`支持`，需要继续适配为`待适配`，尚未完成的补测保持`补充testcase` |

沿用 [Step 2.1 的选表规则](step2.1-api_prepare.md)。下表路径均相对 AI_LOOP 根目录，绝对路径按同一目录结构识别。

| Step 0 的 venv | 适配目标 | 交接表名 |
|---|---|---|
| `data/venv/python-<Python版本>` | PyTorch nightly | `API支持数据` |
| `data/venv/torch-<torch版本>/python-<Python版本>` | 目录中 torch 的主次版本 | `API支持数据_<主版本>.<次版本>` |

固定版本即使用了 nightly 的 torch-npu wheel，也沿用固定版本表。路径无法识别或目标表缺失时，向调用方报告缺项。若已安装的 `api-adaptation` 只支持主表，固定版本任务停在调用前，报告待支持的表名，以免分类反馈写入主表。

派发时指明当前 `adapt-plan` 的计划项键（如 `step1`），由子代理直接读取该项的 `task-branches`。主控核对派发范围与原项逐名、逐序一致。Step 2.1 已按数据库正名合并的任务沿用计划结果。计划中仍列出的不同入口逐名下发，主控和子代理不得再按对象归并。原有 Step0 分流照常执行。验收本轮生成时，核对 Step0.1 生成器收件回执中的全名与该计划项本轮待生成的全名。

按编排顺序调用各subagent，等待其返回代码改动、任务结果、实际验证情况、未完成事项和发布材料。主agent汇总本批结果；“用例已生成”“待运行”等阶段性结果按实际进度记录，不能视为全部适配完成。

## 第三步：生成 PR

### 1. 确认哪些任务可以发布

主控逐项核对计划和验证结果。混合批次中的每一项都按对应条件判断。

以本批 `adapt-information.json` 的 `task-branches` 为交付清单。对每个保留的 API，在 `api-loop/result.json` 的 `results[]` 中按任务 API 名找到唯一记录，核对 `next_step=step4`、`status=支持`，并按 API 正名核对 `api-status=支持`。

| 任务 | 可以发布的条件 |
|---|---|
| 适配实现 | 计划项和验证均已完成，对应 API 状态为 `支持`。 |
| 仅补充测试 | 只交付目标平台已支持、对应测试已验证通过的内容。其他设备尚待补测时，按 Step 4.2 记录验证缺口。 |

用例写错时，先修正并复测。最终仍有任一规定用例失败，包括交叉用例，就从本批交付中撤回整个 API。

只要清单中还有一项未到发布步骤，整份清单暂停发布。按该项的 `next_step` 继续处理。补测发现实现缺口后，`待适配 / step2` 交回设计；需要改 torch-npu C++ 或重编 wheel 时，照常开发和复测。只有交回了最终失败、验证不足或已核实的平台限制及其依据，才按 Step 4 撤回该项。创建和更新 PR 时，`scripts/publish_pr.py` 都会在推送前检查这份清单，逐项列出未通过的原因。

主控复测后改过测试或辅助文件的，将受影响项交回 api-adaptation Step 1。重新验收后走 Step 1.1，以本次文件对应的 GPU 请求登记待回传。

### 2. 由主控调用 Step 4.2 发布

把第一步的 issue、问题分析、实际方案、各子代理的开发结果和测试记录交给 [Step 4.2](step4.2-create_pr.md)，附上材料路径、源码和开发分支。材料可放在一份报告里，也可分文件提供。

主控按目标仓模板核对代码和验证材料，只汇总本次实际交付的内容。将已验收的改动整理为一个提交，创建或更新原 PR，并关联第一步的 issue。issue 和 PR 均由主控提交。

### 3. 留下可恢复的验收环境

- **安装包。** 子代理每次成功重编 torch-npu wheel 后，运行 `scripts/task_torchnpu_wheel.py --basic-information <本批基础信息> --built-wheel <本次产出的.whl>` 更新 `last-torchnpu-wheel`。主控交接前按最后一次验收报告核对实际使用的包。
- **提交和报告。** 在本批 `pr.json` 中核对发布提交 `commit` 和验证入口 `verification`。Step 5 从 `adapt-information.json` 查找原计划项及其子代理。
- **环境差异。** 对照 `basic_information.json` 核实验收时的解释器、venv、torch 和 torch-npu 构建、源码提交及设备。若有差异，将实际值、依赖或安装包来源和报告路径记入本批 `step4-environment.json`，供 Step 5 优先恢复。
