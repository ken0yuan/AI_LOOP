# API 适配前置准备

## 输入内容

本次任务只接收Step 0交付的`data/run_data/<run-id>/basic_information.json`及需要适配的API名称。已有API支持数据库随本skill保存在`data/api-support.sqlite3`。所有命令从AI_LOOP根目录执行，产物路径相对该根目录。

| 输入 | 路径与读取内容 |
|---|---|
| 本次任务信息 | `data/run_data/<run-id>/basic_information.json`：`venv`字段指定本次运行环境 |
| 既有API支持数据库 | `data/api-support.sqlite3`：表`API支持数据`，主键`API名称` |

数据库使用十二列：`API名称`、`是否支持`、`API等级`、`API适配分类`、`是否对外`、`分类`、`细分模块`、`细分场景`、`适配策略`、`本地是否适配`、`分类待审核`、`api首次出现版本`。后续在同一份数据库上增量更新。`API适配分类`紧接`API等级`，只允许`框架类API`、`算子类API`、`专项适配API`，新增条目默认`框架类API`；它与L0/L1/L2等级及三级分类分别记录。

`api首次出现版本`由脚本维护：现有2.14原版条目标`2.14`，现有2.15新增条目标`2.15`，历史回填只依据现有条目，不依据网页重新增删或改判；后续新增文档API填本次main实际对应的主次版本号（如`2.16`），不填`main`。已有值保留，补全分类时不重新填写。

文档来源固定为最新main页面；刷新入口内部调用`scripts/extract_docs_inventory.py`，从官方`objects.inv`获取官方登记的API名称和声明目标，不逐页读取HTML。运行环境直接使用基础信息中的`venv`，不依据Python版本重新推导路径。

先刷新数据库，有新增API时补全条目，再根据问题描述准备版本控制信息。缺少输入或前一步失败时停止。

## 第一步：刷新数据库

本步只处理对外API：登记新增名称，并给文档中消失的条目标记下线原因。支持能力判断、分类和适配策略留到第二步补充。

输入为本次`basic_information.json`及skill内的`data/api-support.sqlite3`。只调用这一个入口：

```bash
python3 -B scripts/refresh_api_support_db.py \
  --basic-information data/run_data/<run-id>/basic_information.json \
  --database data/api-support.sqlite3 \
  --new-apis data/run_data/<run-id>/api-refresh/new-apis.json
```

脚本自动完成以下五小步：

1. **提取最新main文档中的API。** 调用`scripts/extract_docs_inventory.py`，读取`https://docs.pytorch.org/docs/main/objects.inv`，从索引中提取正式登记的Python API；索引获取或解析失败时停止，不使用不完整清单更新数据库。

2. **与现有数据库对照。** 按API名称分成三类：文档有、数据库没有；数据库有、文档没有；两边都有。对照时，`Tensor`与`torch.Tensor`及对应成员（如`Tensor.add`与`torch.Tensor.add`）可双向匹配，精确同名优先；保留原数据库主键，不改变文档清单。已有内部条目保持原样。

3. **验证并登记新增API。** 对“文档有、数据库没有”的名称，脚本自动使用`basic_information.json.venv`中的解释器核验wheel。存在就新增数据库条目，`API适配分类`默认填`框架类API`、`是否对外`设为`是`、`分类待审核`设为`是`，`api首次出现版本`填写本次main索引声明的实际主次版本，其他字段交给第二步补充；不存在则本轮不上新，下次刷新继续检查。

4. **给文档中消失的API标记下线原因。** 对“数据库有、文档没有”的对外条目，只检查`是否支持`为`支持`、`不支持`、`未提供`的API。wheel中也没有，就把`是否支持`写为`API删除，待下线`；wheel中仍然存在，就写为`文档删除，待下线`。保留原条目和其他字段；其他状态及已经待下线的条目不重复检查。

5. **保存更新，把新增API交给第二步。** 文档提取、wheel核验完成后，统一用事务提交新增条目和下线状态，把本次任务实际新增的API名称写入`api-refresh/new-apis.json`。两边都有的名称不核验wheel。脚本执行失败时回滚数据库更新，不把依赖或导入异常当作API删除。

持久结果只有更新后的`data/api-support.sqlite3`和交给第二步的`data/run_data/<run-id>/api-refresh/new-apis.json`。新增清单是精确API名称数组，没有新增时为`[]`；同一任务重跑会保留该任务已经输出的新增名称，避免丢失待补充条目。成功退出码为0，标准输出包含本轮文档数量和更新数量。命令成功后先读取新增清单：为空则跳过第二步，直接进入第三步处理问题描述中的API；非空则先进入第二步补全，再进入第三步。失败退出码非零，不进入第二步。

## 第二步：补全新增API条目

本步填写新增API的默认值，完成三级分类，再调用脚本把全部十一列写完整，再交给第三步。实际补充功能测试用例由后续API subagent执行。

### 输入

| 输入 | 用途 |
|---|---|
| `api-refresh/new-apis.json` | 第一步登记成功的API精确名称数组；不改名、不自行增加API |
| `data/api-support.sqlite3` | 读取本批API原条目，复用已填写的业务值 |
| `basic_information.json` | 本次框架版本、源码位置及`venv`；沿用Step 0已准备的环境 |
| 官方文档、分类references及同一说明文件中的候选定义 | 判断API职责及三级分类；缺少文档时才依据名称推断 |

任务输入位于`data/run_data/<run-id>/`，分类说明固定保存在共享目录`data/api-classification/`。后续任务按API名称复用，不因run-id变化重新保存。数据库和references路径均相对AI_LOOP根目录。

### 1. 读取新增API与原条目

接收第一步确认非空的`api-refresh/new-apis.json`，按其中的精确名称查询`API支持数据`。名单中的API必须已由第一步登记；名称缺失或重复时先修正衔接问题。

使用Python标准库`sqlite3`读取表。若该API的十一列已完整且共享分类说明已存在，直接复用，不重复分类。需要补全时，准备该API最终的十一列值；不在临时文件中复制原条目，不另存数据库备份。

新增API尚无`本地是否适配`值，在下一小步填为默认`是`。第二步只负责补全条目，不使用该列决定是否继续；该列在第三步派发任务时才用于判断。

### 2. 补齐默认值

按下表形成最终十一列。已有业务值保留，默认值只补空值；首次分类和本次明确完成的分类修订写入分类列。

| 数据库列 | 新增API的填写方式 |
|---|---|
| API名称 | 使用新增清单中的精确名称，保留数据库主键 |
| 是否支持 | 缺省填`未提供`，不在本步推断为支持 |
| API等级 | 缺省填`L2`；已有L0、L1等等级保留 |
| API适配分类 | 新增API统一填`框架类API` |
| 是否对外 | `是`，本步只处理对外API |
| 分类 | 按职责完成第一次分类，不留空 |
| 细分模块 | 在所选分类内完成第二次分类，不留空 |
| 细分场景 | 在所选模块内完成第三次分类，不留空 |
| 适配策略 | 缺省填`补充testcase` |
| 本地是否适配 | 新增API默认填`是`，供第三步判断是否派发任务 |
| 分类待审核 | 新增API统一填`是`，人类审核通过后才能改为`否` |
| api首次出现版本 | 第一步已由刷新脚本按本次main文档实际版本填写；保留原值，不放入临时十一列写库数据 |

默认`未提供`表示尚无支持结论；默认`补充testcase`对应后续补充功能测试用例。本步不追加功能、内存、精度、确定性的完整测试评估，不把名称存在或导入成功当作NPU支持证据。

### 3. 依次完成三级分类

读取[API三级分类规则](../references/api-classification/rules.md)，依次确定分类、细分模块、细分场景，三个结果均不得留空。每层保存一句简短原因；新增API的分类待审核保持`是`。

需要新标签时，按该规则在classification-evidence.jsonl中登记候选定义，本轮不修改references。

### 4. 保存简短分类说明，准备十一列写库数据

**长期说明：**统一放在`data/api-classification/classification-evidence.jsonl`。每个API只保留一条简短说明；候选分类定义在同一文件中各存一条独立记录，不重复到每个API中。不另建候选表，也不保存文档正文或原数据库值。

| 字段 | 内容 |
|---|---|
| `api` | 精确API名称，唯一关联键 |
| `category_reason` | 为什么属于该分类，一句职责匹配说明 |
| `module_reason` | 为什么属于该细分模块，一句职责匹配说明 |
| `scenario_reason` | 为什么属于该细分场景，一句职责匹配说明 |
| `new_label_reasons` | 仅涉及新增或待审核标签时填写；按category、module、scenario记录对应层的职责缺口 |

每层理由建议一个短句，最多100字。没有官方文档而按名称推断时，在相应理由中说明。新增API无论分类是否确定，`分类待审核`都填`是`，本步不改为`否`。不另存status、review_reasons、链接数组、三次选择对象或审核流程记录。

新标签的判断标准和审核后的规则更新方式统一见[API三级分类规则](../references/api-classification/rules.md)。本节只说明保存格式与脚本调用。

先把每个API的三层分类原因填入下面的代码，再执行，保存到`data/api-classification/classification-evidence.jsonl`。将`<API>`和三处原因替换为实际内容；`save_reasons`只负责保存这些说明，不做分类判断，也不修改支持数据库：

```bash
python3 -B - <<'PY'
import sys
sys.path.insert(0, 'scripts')
from update_api_support_entries import save_reasons
save_reasons([
    {'api': '<API>',
     'category_reason': '<一级分类的简短原因>',
     'module_reason': '<细分模块的简短原因>',
     'scenario_reason': '<细分场景的简短原因>'}
])
PY
```

如果该API已经有说明，而且分类没变，就直接使用原说明。如果分类改了，就用新原因替换这个API的旧说明，其他API的说明不动。一个API始终只保留一条，不在每次任务目录中另存副本。

新定义通过同一脚本的`save_definitions(候选定义列表)`保存。脚本自动添加`kind=definition`，候选记录包含level、parent、name、definition、apply_when、do_not_apply_when、example_apis、why_existing_does_not_fit、review_status、reference_file；按level、parent、name唯一保存，重复提交只更新原记录。API说明与定义记录互不覆盖。


**临时写库数据：**`data/run_data/<run-id>/api-entries.jsonl`每行直接使用数据库十一列中文列名和值，除此之外不放任何字段。不要包fields，不带before、api、decisions、evidence或分类理由。API名称列就是关联键。

```json
{"API名称":"<API>","是否支持":"未提供","API等级":"L2","API适配分类":"框架类API","是否对外":"是","分类":"<分类>","细分模块":"<细分模块>","细分场景":"<细分场景>","适配策略":"补充testcase","本地是否适配":"是","分类待审核":"是"}
```

示例中的三级分类替换为实际结果；新增API的`分类待审核`保持`是`，不按AI的判断置信度改为`否`。每个待补全API只有一行，十一列均为非空字符串；已完整且有共享说明的API无需再次提交，整批可复用时不创建临时文件。

### 5. 调用脚本写库并交付

从AI_LOOP根目录执行：

```bash
python3 -B scripts/update_api_support_entries.py \
  --basic-information data/run_data/<run-id>/basic_information.json \
  --database data/api-support.sqlite3 \
  --new-apis data/run_data/<run-id>/api-refresh/new-apis.json \
  --entries data/run_data/<run-id>/api-entries.jsonl
```

数据库更新用事务整批提交，任一条目失败则整批回滚，临时输入保留供修正；成功后删除临时输入。分类说明已经由上一步保存到共享目录，写库失败时保留，重试复用，不重新生成。既有业务值保留，不新增备份。

成功后返回`API_ENTRIES_READY`，再进入第三步。

**本步输出：**更新后的数据库和共享`data/api-classification/classification-evidence.jsonl`；提出新定义时还包括同一说明文件中的候选定义记录，不包含references变更。后续任务按API名称复用。

## 第三步：准备版本控制信息

### 输入

| 输入 | 读取内容 |
|---|---|
| `data/run_data/<run-id>/basic_information.json` | `problem-description`：本次要处理的问题描述 |
| `data/api-support.sqlite3` | `API支持数据`表中的`API名称`和`本地是否适配` |

本步处理问题描述中的API，不使用文档刷新产生的新增清单作为任务名单。没有新增API时也可以处理数据库中已有的API。

### 执行

从AI_LOOP根目录调用：

```bash
python3 -B scripts/prepare_adapt_information.py \
  --basic-information data/run_data/<run-id>/basic_information.json \
  --database data/api-support.sqlite3
```

脚本依次完成：

1. 从问题描述提取明确写出的API名称，例如`torch.nn.functional.relu`。没有命名空间的名称用反引号标明，例如`view`。重复提及的同一API只处理一次；没有提取到API就直接结束，不生成文件。
2. 与数据库匹配。优先精确同名，仅复用已验证的Tensor简写匹配，例如输入`torch.Tensor.add`、数据库名称为`Tensor.add`时，最终使用`Tensor.add`。数据库没有对应名称时停止，不自行造名字或取父模块代替。
3. 查看`本地是否适配`：为`是`就生成`adapt-<数据库API名称>`；为`否`跳过。全部为否时直接结束，不生成文件。该值为空或不是是/否时停止，先补正数据库。
4. 至少有一个API需要适配时，才在本次run-id目录写入`adapt-information.json`。本步只准备分支名称，不创建Git分支或启动subagent。

### 输出

`data/run_data/<run-id>/adapt-information.json`中的`task-branches`是待适配任务标识的字符串数组。本步按API生成`adapt-<数据库API名称>`标识，不额外保存API名称到标识的键值对应。格式示例：

```json
{
  "task-branches": [
    "adapt-Tensor.add",
    "adapt-torch.nn.functional.relu"
  ]
}
```

每个元素中`adapt-`后面的名称必须与数据库`API名称`逐字一致，不增加日期，不补前缀，不改写名称。按问题描述中的提取顺序输出，每个需要适配的API只生成一个元素，不生成空的`task-branches`。

`scripts/prepare_adapt_information.py`成功写入文件后，会在命令的标准输出中打印JSON，其中`status`为`ADAPT_INFORMATION_READY`。这个状态由脚本生成，保存在标准输出中；`adapt-information.json`保存任务标识数组。

执行命令后，读取本次标准输出JSON中的`status`：为`ADAPT_INFORMATION_READY`才进入后续版本控制；为`NO_ADAPTATION_REQUIRED`则直接结束。找不到API或数据库没有对应名称时，脚本非零退出，也直接结束，不读取旧文件继续执行。

重跑成功时更新任务标识数组，保留其他已存在的值。兼容读取旧对象时仅比较其中的值，输出统一为数组，避免丢失同一任务的Step 3恢复信息。数据库只读，不新增备份或其他持久产物。

### 与Step 3的衔接

Step 3的`task-branches`是通用待适配任务标识的数组，只有`branch-mapping`记录“任务标识 → 实际开发分支名称”的键值对。本节API适配只是任务来源之一；Step 3不要求任务标识是API名，也不要求通用任务标识采用`adapt-<API>`格式。本步仍按已有API规则生成输入。

`task-branches`中的`adapt-<API>`是跨运行定位API适配成果的稳定标识，本步按原规则输出完整名单，不因多个API共用实际开发分支而改名。只有本次返回`ADAPT_INFORMATION_READY`才执行[Step 3](step3-git_control.md)。

Step 3通过`data/branch-mapping.json`定位实际分支。全部标识已有对应本地分支时，返回`EXISTING_BRANCHES_READY`及`stop-task=true`，本次直接结束，不切换或合并分支，不写入`development-branch`，也不进入Step 4或Step 5；未来再补充合并、测试检验、必要开发和映射刷新。存在未对应项时，Step 3基于依赖成果创建新分支，写入`development-branch`，暂不运行依赖PR测试，直接从本次`task-branches`移除原已对应项，只留下新增适配项。

只有Step 3本次返回`BRANCH_READY`且`stop-task=false`才进入[Step 3.1任务分发器](step3.1-dispatcher.md)。Step 3.1读取本次`basic_information.json`及`adapt-information.json`，在后者添加`adapt-plan`。当前整批剩余任务均为`adapt-<API>`时，只生成一个`step1`，其中`agentname`为`api-adaptation`，`task-branches`保留整批任务及其原顺序；后续由Step 4按计划执行。

Step 3在`git-control.requested-branches`保留原始完整名单，名单缩减不代表测试通过。本步恢复同一份原始名单时保留其检查点；真正更换API名单才清除旧结果，已有未完成操作时先完成原操作。后续根据Step 3本次返回的状态决定结束还是继续，不把完整Step 2.1名单直接全部交给开发。

## 输出目录与完成条件

```text
data/
  api-support.sqlite3                           支持数据库，保存完整分类结果
  api-classification/
    classification-evidence.jsonl               API逐层理由及候选定义，分别去重
  run_data/<run-id>/
    basic_information.json                      沿用Step 0输入
    adapt-information.json                      问题描述中需要适配的API与分支名
    api-refresh/new-apis.json                    第一步新增清单
    api-entries.jsonl                           临时写库输入，成功后删除
```

不新增备份。文档提取和wheel核验临时文件由第一步自动清理；第二步仅在共享data/api-classification/保留简短PR说明及定义提案。第二步失败时不进入第三步。无新增API时跳过第二步，第三步仍按问题描述判断数据库中哪些API需要适配。
