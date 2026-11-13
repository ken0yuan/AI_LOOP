# Step 0：获取问题并准备环境

## 输入内容

API名, 或xxx

## 获取问题和各自需要的信息

### 当输入为一个单独的API时

单API任务的`problem-description`固定为``API `<API>` needs adaptation``。Step 0先调用脚本准备材料与环境。

从AI_LOOP根目录执行，单个API适配任务的`<run-id>`命名为`adapt-<API>-<年月日>`；`<API>`使用完整API名称，日期采用`YYMMDD`，例如`adapt-torch.Tensor.add-261109`：

```bash
python3 -B scripts/prepare_api_inputs.py --api <API> --run-dir data/run_data/<run-id>
```

这个脚本只负责生成基础信息的初始部分: 在run-id目录下创建`basic_information.json`和`requirements.txt`。

它先执行`npu-smi info`并读取当前CANN安装记录，再将`run_id`、`problem-description`、实际CANN和驱动版本，以及默认配置中的Python版本、目标芯片、系统和架构写入`data/run_data/<run-id>/basic_information.json`；后续需要的源码仓库和OBS配置保存在该文件的`preparation_config`中。

此时`preparation_status`为`BASIC_READY`，源码和wheel字段尚未填写。随后将根目录`requirements-default.txt`与`--requirement`指定的必须依赖合并，同名包采用任务指定的约束，将结果写入`data/run_data/<run-id>/requirements.txt`；没有指定必须依赖时，完整复制默认清单。这个阶段不拉取源码，也不查询或下载wheel。

### 当输入为 PR 链接时

## 准备安装包和源码

得到基础信息后，将`data/run_data/<run-id>/basic_information.json`传给`scripts/fetch_nightly_wheels.py`，一次完成安装包下载、源码准备和TorchNPU版本切换：

```bash
python3 -B scripts/fetch_nightly_wheels.py --basic-information data/run_data/<run-id>/basic_information.json
```

脚本根据基础信息选择配套wheel和PyTorch源码包。新源码校验通过后，删除`data/source_code/pytorch/`中的旧内容并放入新源码；然后准备`data/source_code/torch_npu/`，没有仓库时从`preparation_config.torch_npu_repo`克隆，已有仓库时在原目录更新实际主线，当前为`master`。

wheel压缩包在临时目录下载、解压和校验，只将解出的`.whl`文件平铺到`data/wheels/`，包括`torch-*.whl`、`torch_npu-*.whl`及配套包附带的`torchvision-*.whl`。

随后从刚下载的torch-npu wheel中读取对应的Git提交号。在`data/source_code/torch_npu/`仓库中，先切回`master`分支，再切到这个提交。完成后，这个目录中的源码就是本次wheel对应的版本，你后续可以从这里新建适配分支。

脚本把两个源码目录、wheel文件的相对路径、版本和TorchNPU提交号写回同一份`basic_information.json`。安装包已下载到位、源码也已切换到wheel对应版本后，将`preparation_status`设为`INPUTS_READY`，接着运行环境安装脚本。

### 源码仓库远端约定

`preparation_config.torch_npu_repo`始终是主仓地址，Git远端名为`upstream`，拉取主线和wheel提交均使用该远端。新克隆使用`--origin upstream`；旧仓库若将同一主仓配置为`origin`，更新时将其改名为`upstream`并保留引用。

`api-defaults.json.torch_npu_fork_repo`默认是`https://gitcode.com/ken0yuan/pytorch.git`，保存到本次`preparation_config`后配置为fork远端`origin`；主仓仍为`https://gitcode.com/Ascend/pytorch.git`，配置为`upstream`。拉主线使用主仓，开发分支后续push到fork。已有origin与指定fork不一致时停止，不覆盖已有远端。Step 4.1在开发前向主仓提issue；Step 4.2在全部适配完成后提交代码、push到fork并向主仓提PR，本步不执行这些操作。

## 安装环境

安装脚本先读取本次`run-dir`中的`basic_information.json`和`requirements.txt`，确认`preparation_status`为`INPUTS_READY`，并检查所需文件是否齐全、版本是否一致。随后找到符合`python_version`的解释器，解析依赖清单；检查通过后才创建或复用虚拟环境并安装，缺少材料时直接指出需要补齐的内容。

从 AI_LOOP 根目录执行：

```bash
python3 -B scripts/install_env.py --run-dir data/run_data/<run-id>
```

1. 从 basic_information 读取完整 `python_version`，找到相同版本的现有解释器。
2. 选择 `data/venv/python-<完整版本>/`：存在就复用，不存在才创建。不同补丁版本也各有一个 venv，不按任务或框架版本重复建立。
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
  api-defaults.json                          单 API 任务默认环境与源码/下载配置
  data/
    source_code/                             共享 PyTorch / torch-npu 源码缓存
    wheels/                                  共享框架 wheel 缓存
    venv/python-<完整版本>/                    本次安装或复用的Python环境
    run_data/<run-id>/
      basic_information.json                 必需：任务、环境、源码和wheel信息
      requirements.txt                       必需：上游依赖清单，独立文件
      pr_info.json                           按需：PR 基本信息
      pr_diff.diff                           按需：真实 PR diff
      CI_result.json                         按需：结构化测试结果
```

源码和wheel不复制进单次任务目录。PyTorch源码固定放在`data/source_code/pytorch/`，获取时校验新源码后删除旧内容并替换；torch-npu固定使用`data/source_code/torch_npu/`，在原仓库更新实际主线，当前为`master`，随后在同一目录切换到wheel对应提交。wheel直接平铺在`data/wheels/`，该目录只存放`.whl`文件，不按版本、提交或构建另建子目录。同名wheel在校验通过后替换，其他wheel可能保留；本次任务使用哪个文件，以`basic_information.json`中的相对路径为准。

### 1. basic_information.json（必需）

UTF-8 JSON对象，由准备、源码、wheel和安装脚本依次补写。初始`preparation_status`为`BASIC_READY`，准备源码后为`SOURCES_READY`；安装包和源码版本全部准备好后，变为`INPUTS_READY`。安装脚本继续向同一文件补入环境路径，将`environment_status`设为`ENV_READY`；此时Step 0完成。

| 字段 | 内容 |
|---|---|
| `run_id` | API任务为`adapt-<API>-<年月日>`，日期采用`YYMMDD` |
| `problem-description` | API任务固定为``API `<API>` needs adaptation`` |
| `cann_version`、`driver_version` | 准备脚本在初始阶段采集当前服务器的CANN和驱动版本；驱动来自`npu-smi info`，CANN来自当前安装记录 |
| `python_version` | 完整Python版本，如`3.11.17` |
| `chip_type`、`os`、`architecture` | 本任务的目标芯片、系统和架构，用于选择配套wheel |
| `pytorch_source` | 固定为`data/source_code/pytorch`，内容来自与所选wheel配套的源码包 |
| `pytorch_source_commit` | PyTorch源码包OBS对象名中的提交前缀，不冒充最新主线提交 |
| `torch_npu_latest_source` | 固定为`data/source_code/torch_npu`，准备仓库后在同一目录切换到本次wheel对应提交 |
| `pytorch_version` | 本次选定PyTorch wheel的实际版本 |
| `pytorch_wheel` | 本次选定的具体文件，如`data/wheels/torch-<版本>-<标签>.whl` |
| `torch_npu_version` | 本次选定torch-npu wheel的实际版本 |
| `torch_npu_wheel` | 本次选定的具体文件，如`data/wheels/torch_npu-<版本>-<标签>.whl` |
| `torch_npu_commit` | 本次torch-npu源码对应的提交，由配套wheel取得，用于定位本次源码版本 |
| `preparation_status` | 当前准备状态；源码和wheel补齐后为`INPUTS_READY` |
| `environment_status` | 环境状态：`NOT_READY`、`INSTALLING`、`ENV_READY`或`FAILED` |
| `python` | 安装并验证后的解释器路径，如`data/venv/python-3.11.17/bin/python` |
| `venv` | 本次使用的虚拟环境目录，如`data/venv/python-3.11.17` |
| `activate` | 加载CANN及虚拟环境的脚本，如`data/venv/python-3.11.17/env.sh` |
| `environment_reused`、`environment_completed_at` | 是否复用已有环境及本次验证完成时间 |
| `environment_error` | 环境准备失败时的具体原因，成功后清除 |

文中的本地路径均相对AI_LOOP根目录，命令也从该根目录执行。基础信息中记录本次实际使用的源码目录、wheel文件和环境路径。

PR 详细元数据放 `pr_info.json`，测试详细结果放 `CI_result.json`；基础信息不重复保存完整diff或依赖清单。

### 2. requirements.txt（必需）

本次任务的依赖清单保存在`data/run_data/<run-id>/requirements.txt`。有必须依赖时，在首次调用`scripts/prepare_api_inputs.py`时追加`--requirement '包名及版本约束'`，例如`--requirement 'pytest==8.3.0'`；多个依赖重复使用该参数。脚本将这些依赖与根目录`requirements-default.txt`合并：保留未被覆盖的默认依赖，同名包采用任务指定的约束，新增包一并加入，最终写成一份新的任务`requirements.txt`。环境标记按任务的目标Python和平台判断，未生效的条件依赖不覆盖当前目标需要的默认依赖。没有指定必须依赖时，直接完整复制默认清单；根目录默认文件保持原样。

PyTorch和torch-npu使用`basic_information.json`中指定的wheel安装，依赖清单不必重复列出；如果列出，约束须与这两个wheel一致。清单支持环境标记、`--find-links`、`-r`和`-c`等pip格式；附属清单应与本次任务一起保留，`-r`和`-c`引用按所在清单的相对路径填写。

### 3. pr_info.json（按需）

有关联 PR 且取得真实信息时提供，记录 PR 号、仓库/链接、标题、提交等已有事实。单 API 或没有关联 PR 的 CI 任务可以不提供；不要为凑齐文件伪造 PR。

### 4. pr_diff.diff（按需）

上游取得的真实原始 diff。没有关联 PR 或尚未取得 diff 时不生成空文件冒充完整材料；已知缺口在基础信息中说明。

### 5. CI_result.json（按需）

已有测试结果的结构化摘要：测试项、实际状态、失败摘要和可获得的运行环境。CI 与本地测试注明实际来源；无结果时不生成，不把环境安装冒烟当成业务测试已通过。

### 按输入来源生成的文件

- 单 API：至少 basic_information + requirements；API 名及需求放基础信息。
- PR：上述两份，加实际取得的 pr_info / pr_diff；有测试结果时再附 CI_result。
- CI：上述两份，加已有 CI_result；有关联 PR 时可同时提供 PR 材料。

上述五份是业务材料，不要求凑齐。环境结果补入`basic_information.json`，共享Python环境保存在`data/venv/`。
