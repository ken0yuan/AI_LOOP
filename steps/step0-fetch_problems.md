# Step 0：获取问题并准备环境

## 输入内容

一组API名 + torch版本号, 或适配最新API

## 获取问题和各自需要的信息

### 当输入为适配API时

明确API任务的`problem-description`固定为``<torch-version> API `<API1>` `<API2>` …… needs adaptation``。如果输入为适配最新API的表述，`problem-description`固定为``New API needs adaptation``。Step 0先调用脚本准备材料与环境。

从 AI_LOOP 根目录执行。指定版本的任务目录名为 `adapt-<torch-version>-<API1>-<YYMMDD>`，其中 `<API1>` 为第一个 API 的完整名称。以下命令中，`TORCH_VERSION` 只接受主次版本（如 `2.14`）。带补丁号的输入会在联网前报错。

```bash
python3 -B scripts/prepare_api_inputs.py --torch-version "${TORCH_VERSION:?请先设置任务指定的版本}" --api torch.Tensor.add \
  --run-dir "data/run_data/adapt-${TORCH_VERSION}-torch.Tensor.add-261109"
```

`--api`接收一个或多个API，不限制数量。适配最新API时改用`--new-apis`，两种输入方式二选一：

```bash
python3 -B scripts/prepare_api_inputs.py --new-apis --run-dir data/run_data/adapt-new-261109
```

这个脚本只负责生成基础信息的初始部分: 在run-id目录下创建`basic_information.json`和`requirements.txt`。

它先执行`npu-smi info`读取真实芯片型号和驱动版本，并读取当前CANN安装记录，再将任务信息、实际硬件信息及默认配置中的Python版本、系统和架构写入`data/run_data/<run-id>/basic_information.json`。芯片型号不参与安装包筛选；后续源码和下载配置保存在`preparation_config`条目中。

脚本将 `requirements-default.txt` 与 `--requirement` 指定的依赖合并到任务的 `requirements.txt`。指定 `--torch-version X.Y` 时，先查询 PyPI 的 `torch X.Y.0` 发行记录。已发行时写入 `torch==X.Y`，即选择 X.Y.0。PyPI 返回 404 时再读取 PyTorch main 文档索引：主次版本相同则走 nightly，省略 torch 约束；其余版本报错。查询失败时报告错误，平台 wheel 是否齐全在准备安装包时核验。最新 API 任务使用 `--new-apis`，直接走 nightly。初始化成功后输出 `BASIC_READY`。

### 当输入为 PR 链接时

## 准备安装包和源码

读取本次任务的 `requirements.txt`。有生效的 torch 精确版本约束时，先检查已有环境。最新 API 任务沿用下方 nightly 流程。

### 固定版本：先检查 venv

从 AI_LOOP 根目录运行固定版本入口：

```bash
python3 -B scripts/prepare_fixed_wheels.py --basic-information data/run_data/<run-id>/basic_information.json
```

1. 按生效的 torch 精确版本约束和完整 Python 版本检查 `data/venv/torch-<PyTorch版本>/python-<Python版本>/`。环境匹配时沿用已安装的 torch-npu，直接进入[准备源码](#准备源码)。即使本地 wheel 缓存已清理，也可复用。
2. 只有普通依赖有差异时，单独补齐依赖，固定已安装的框架版本，复查后进入[准备源码](#准备源码)。
3. 首次准备时先检查本地 wheel。缺少的 torch 从南京大学 PyTorch CPU wheel 镜像下载，按目标平台选择 CPU 构建并核对索引给出的 SHA-256。torch-npu 优先从华为云普通 pip 镜像选择配套稳定版，确认缺包才选华为云 Ascend 镜像当时可用的配套 nightly。安装到独立 venv 后验证真实 NPU 运算，再进入[准备源码](#准备源码)。

此命令同时完成固定版本的安装和 torch-npu 源码检出。成功后直接进入[同步 PR 与开发记录](#同步pr与开发记录)。

### Nightly 安装包

已有环境需要检查更新时，先执行以下命令。它只查询可用 nightly，不修改当前环境：

```bash
python3 -B scripts/check_nightly_availability.py \
  --basic-information data/run_data/<run-id>/basic_information.json \
  --output data/run_data/<run-id>/log/nightly-availability.json
```

根据检查结果决定更新或复用环境；检查失败时先处理原因。首次准备环境直接执行下方步骤。

需要获取 nightly 安装包时，从 AI_LOOP 根目录执行以下命令：

```bash
python3 -B scripts/fetch_nightly_wheels.py --basic-information data/run_data/<run-id>/basic_information.json
```

wheel 压缩包在临时目录下载、解压和校验，解出的 `.whl` 文件平铺在 `data/wheels/`。确定本次使用的环境或安装包后，进入[准备源码](#准备源码)。

### 准备源码

- **Nightly** 由 `fetch_nightly_wheels.py` 从原有 OBS 目录选择配套 wheel 和对应提交的 PyTorch 源码归档。校验后将 PyTorch 源码放入 `data/source_code/pytorch/`。
- **固定版本** 按实际 torch 发行版本找到 `data/source_code/pytorch-<完整发行版本>/`，将目录写入 `pytorch_source`。例如，`2.15.0+cpu` 使用 `data/source_code/pytorch-2.15.0/`。
- **两种任务** 都从 GitCode 主仓更新 torch-npu 主线，再按所选 wheel 或已安装 torch-npu 的构建提交检出。源码路径和实际提交沿用既有字段。

固定版本由其独立脚本完成环境核验并进入 Step 0.1。Nightly 取得安装包和源码后进入[安装环境](#安装环境)。

### 源码仓库远端约定

`preparation_config.torch_npu_repo`始终是主仓地址，Git远端名为`upstream`，拉取主线和wheel提交均使用该远端。新克隆使用`--origin upstream`；旧仓库若将同一主仓配置为`origin`，更新时将其改名为`upstream`并保留引用。

`api-defaults.json.torch_npu_fork_repo`默认是`https://gitcode.com/ken0yuan/pytorch.git`，保存到本次`preparation_config`后配置为fork远端`origin`；主仓仍为`https://gitcode.com/Ascend/pytorch.git`，配置为`upstream`。拉主线使用主仓，开发分支后续push到fork。已有origin与指定fork不一致时停止，不覆盖已有远端。Step 4.1在开发前向主仓提issue；Step 4.2 在满足 Step 4 的适配或补测 PR 发布条件后提交代码、push 到 fork 并向主仓提 PR，本步不执行这些操作。

## 安装环境

以下命令用于 nightly 任务。固定版本的环境由 `prepare_fixed_wheels.py` 处理。

安装脚本先读取本次`run-dir`中的`basic_information.json`和`requirements.txt`，确认`preparation_status`为`INPUTS_READY`，并检查所需文件是否齐全、版本是否一致。随后找到符合`python_version`的解释器，解析依赖清单；检查通过后才创建或复用虚拟环境并安装，缺少材料时直接指出需要补齐的内容。

从 AI_LOOP 根目录执行：

```bash
python3 -B scripts/install_env.py --run-dir data/run_data/<run-id>
```

1. 从 basic_information 读取完整 `python_version`，找到相同版本的现有解释器。
2. Nightly 沿用 `data/venv/python-<完整版本>/`，按本次 OBS 构建安装确切的 wheel。
3. 读取前面已合并生成的`data/run_data/<run-id>/requirements.txt`，按其中列出的依赖和版本约束安装，不再读取默认清单补包或改版本。
4. 读取本次`basic_information.json`中的`pytorch_wheel`和`torch_npu_wheel`，安装指定的PyTorch和torch-npu安装包。
5. 检查实际Python、PyTorch、torch-npu版本及导入位置；通过后，将本次`basic_information.json`的`environment_status`写为`ENV_READY`，同时补入`python`、`venv`和`activate`。失败时将`environment_status`写为`FAILED`并记录`environment_error`；下次重新准备源码或wheel时，环境状态重置为`NOT_READY`，重新安装并检查后才恢复就绪。

## 同步PR与开发记录

环境安装完成、`environment_status=ENV_READY`后，进入[Step 0.1](step0.1-pr_reconciliation.md)同步已合入PR和开发记录。每次运行都执行该同步，完成后再进入Step 1分析本次需求。

## 输出目录

每个任务使用独立目录`data/run_data/<run-id>/`，保存两份必需文件和按实际输入提供的PR、CI材料：

```text
AI_LOOP/
  requirements-default.txt                   全局默认依赖，由 AI_LOOP 维护
  api-defaults.json                          API任务默认环境与源码/下载配置
  data/
    source_code/                             共享 PyTorch / torch-npu 源码缓存
    wheels/                                  共享框架 wheel 缓存
    venv/torch-<PyTorch版本>/python-<Python版本>/  固定版本环境
    venv/python-<完整版本>/                    nightly 环境
    run_data/<run-id>/
      basic_information.json                 必需：任务、环境、源码和wheel信息
      requirements.txt                       必需：上游依赖清单，独立文件
      pr_info.json                           按需：PR 基本信息
      pr_diff.diff                           按需：真实 PR diff
      CI_result.json                         按需：结构化测试结果
```

源码和wheel不复制进单次任务目录。Nightly 的 PyTorch 源码放在`data/source_code/pytorch/`，获取时校验新源码后删除旧内容并替换；torch-npu固定使用`data/source_code/torch_npu/`，在原仓库更新实际主线，当前为`master`，随后在同一目录切换到wheel对应提交。wheel直接平铺在`data/wheels/`，该目录只存放`.whl`文件，不按版本、提交或构建另建子目录。同名wheel在校验通过后替换，其他wheel可能保留；本次任务使用哪个文件，以`basic_information.json`中的相对路径为准。

### 1. basic_information.json（必需）

UTF-8 JSON对象，由准备、源码、wheel和安装脚本依次补写。复用已有 venv 时沿用既有字段，环境版本取自实际导入结果。以下为准备 wheel 并安装时的状态流转。初始`preparation_status`为`BASIC_READY`，准备源码后为`SOURCES_READY`；安装包和源码版本全部准备好后，变为`INPUTS_READY`。安装脚本继续向同一文件补入环境路径，将`environment_status`设为`ENV_READY`；此时Step 0完成。

| 字段 | 内容 |
|---|---|
| `run_id` | 指定版本时为 `adapt-<torch-version>-<API1>-<YYMMDD>`，最新 API 任务为 `adapt-new-<YYMMDD>`。兼容原有未指定版本的 `adapt-<API1>-<YYMMDD>` |
| `problem-description` | 指定版本时为 ``<torch-version> API `<API1>` `<API2>` …… needs adaptation``，最新 API 任务为 `New API needs adaptation` |
| `cann_version`、`driver_version` | 准备脚本在初始阶段采集当前服务器的CANN和驱动版本；驱动来自`npu-smi info`，CANN来自当前安装记录 |
| `python_version` | 完整Python版本，如`3.11.17` |
| `chip_type` | 从`npu-smi info`读取的真实芯片型号，如`Ascend910B4`；不参与安装包筛选 |
| `os`、`architecture` | 本任务的系统和架构，用于选择配套wheel |
| `pytorch_source` | 本次 PyTorch 源码目录。nightly 使用 OBS 配套目录，固定版本使用对应的发行版本目录 |
| `pytorch_source_commit` | nightly 的 OBS 源码对象提交前缀；固定版本留空 |
| `torch_npu_latest_source` | 固定为`data/source_code/torch_npu`，准备仓库后在同一目录切换到本次wheel对应提交 |
| `pytorch_version` | 本次实际使用的 PyTorch 版本 |
| `pytorch_wheel` | 本次选定的具体文件，如`data/wheels/torch-<版本>-<标签>.whl` |
| `torch_npu_version` | 本次实际使用的 torch-npu 版本 |
| `torch_npu_wheel` | 本次选定的具体文件，如`data/wheels/torch_npu-<版本>-<标签>.whl` |
| `torch_npu_commit` | 本次torch-npu源码对应的提交，由配套wheel取得，用于定位本次源码版本 |
| `preparation_status` | 当前准备状态；源码和wheel补齐后为`INPUTS_READY` |
| `environment_status` | 环境状态：`NOT_READY`、`INSTALLING`、`ENV_READY`或`FAILED` |
| `python` | 安装并验证后的解释器路径，如`data/venv/torch-<PyTorch版本>/python-<Python版本>/bin/python` |
| `venv` | 本次使用的虚拟环境目录，如`data/venv/torch-<PyTorch版本>/python-<Python版本>` |
| `activate` | 加载CANN及虚拟环境的脚本，如`data/venv/torch-<PyTorch版本>/python-<Python版本>/env.sh` |
| `environment_completed_at` | 本次环境验证完成时间 |
| `environment_error` | 环境准备失败时的具体原因，成功后清除 |

文中的本地路径均相对AI_LOOP根目录，命令也从该根目录执行。基础信息中记录本次实际使用的源码目录、wheel文件和环境路径。

PR 详细元数据放 `pr_info.json`，测试详细结果放 `CI_result.json`；基础信息不重复保存完整diff或依赖清单。

### 2. requirements.txt（必需）

本次任务的依赖清单保存在`data/run_data/<run-id>/requirements.txt`。有必须依赖时，在首次调用`scripts/prepare_api_inputs.py`时追加`--requirement '包名及版本约束'`，例如`--requirement 'pytest==8.3.0'`；多个依赖重复使用该参数。脚本将这些依赖与根目录`requirements-default.txt`合并：保留未被覆盖的默认依赖，同名包采用任务指定的约束，新增包一并加入，最终写成一份新的任务`requirements.txt`。环境标记按任务的目标Python和平台判断，未生效的条件依赖不覆盖当前目标需要的默认依赖。没有指定必须依赖时，直接完整复制默认清单；根目录默认文件保持原样。

固定版本任务从本清单读取 torch 版本约束，先检查对应 venv；需要安装时，使用本次选定的配套 wheel。清单支持环境标记、`--find-links`、`-r`和`-c`等pip格式；附属清单应与本次任务一起保留，`-r`和`-c`引用按所在清单的相对路径填写。

### 3. pr_info.json（按需）

上游合入的有关联 PR 且取得真实信息时提供，记录 PR 号、仓库/链接、标题、提交等已有事实。API适配或没有关联 PR 的 CI 任务可以不提供；不要为凑齐文件伪造 PR。

### 4. pr_diff.diff（按需）

上游取得的真实原始 diff。没有关联 PR 或尚未取得 diff 时不生成空文件冒充完整材料；已知缺口在基础信息中说明。

### 5. CI_result.json（按需）

已有测试结果的结构化摘要：测试项、实际状态、失败摘要和可获得的运行环境。CI 与本地测试注明实际来源；无结果时不生成，不把环境安装冒烟当成业务测试已通过。

### 按输入来源生成的文件

- API适配：至少 basic_information + requirements；API名称或最新API需求放基础信息。
- PR：上述两份，加实际取得的 pr_info / pr_diff；有测试结果时再附 CI_result。
- CI：上述两份，加已有 CI_result；有关联 PR 时可同时提供 PR 材料。

上述五份是业务材料，不要求凑齐。环境结果补入`basic_information.json`，共享Python环境保存在`data/venv/`。
