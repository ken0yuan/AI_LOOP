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

按编排顺序调用各subagent，等待其返回代码改动、任务结果、实际验证情况、未完成事项和发布材料。主agent汇总本批结果；“用例已生成”“待运行”等阶段性结果按实际进度记录，不能视为全部适配完成。

## 第三步：生成PR

适配任务在各 API 状态为`支持`、计划项和验证完成后发布。对于仅补充测试的 PR，逐项核对：已验证通过的用例可以发布；目标平台明确不支持某个被测场景时，只要用例正确、该场景有可核验的平台限制证据，也直接发布补测 PR。混合批次逐项应用这两种条件；仍有用例错误、未查明的失败或其他未解释的缺项时，先处理对应项。

核对设备型号、受限调用和原始报告：运行时给出的明确型号限制，或与实测设备、软件版本和调用对应的支持范围说明，可作为证据。在 PR 中分别列明通过项与受限项，沿用真实的逐 API 状态。已确认的平台限制无需等待适配实现或支持该场景的设备复测。满足上述条件后，执行[Step 4.2：提交PR](step4.2-create_pr.md)。传入第一步的issue、问题分析、实际方案、各agent开发结果和测试记录的文件路径，以及本次源码和分支上下文；一份交接报告或多份文件均可。主agent按目标仓PR模板汇总，逐栏对照真实材料与代码，缺少完成证据或必要测试记录时先返回补充。正文核实后统一commit、push并创建或更新PR，关联第一步的issue，再将结果交给Step 5。

给 Step 5 留下本批最后一次完成适配并验收时的恢复依据。子代理每次成功重编 torch-npu wheel 后，用 `scripts/task_torchnpu_wheel.py --basic-information <本批基础信息> --built-wheel <本次产出的.whl>` 更新本批 `last-torchnpu-wheel`。交接时对照最后的验证记录核实使用的包。发布提交和验证入口分别由 `pr.json` 的 `commit`、`verification` 指向；原计划项和子代理见 `adapt-information.json`。核对验收时的解释器、venv、实际 torch/torch-npu 构建、源码提交及设备环境。若最终环境与 `basic_information.json` 的准备记录有差异，将实际值、依赖或安装包来源和对应报告路径补记在本批 `step4-environment.json`，供恢复时优先使用。

其他未完成项或验证阻塞仍按原流程处理；subagent不独立提交issue或PR。
