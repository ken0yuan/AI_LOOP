# Step 3：版本控制

根据前置步骤提供的`task-branches`定位已有开发成果。全部任务已有对应本地分支时，当前直接结束；存在未对应任务时，准备一个实际开发分支，交后续分发。执行顺序及agent角色由独立的[Step 3.1分发器](step3.1-dispatcher.md)负责，本步不启动开发subagent。

## 输入与入口

所有命令从AI_LOOP根目录执行，相对路径也按该根目录解析。

| 输入 | 用途 |
|---|---|
| `data/run_data/<run-id>/basic_information.json` | `torch_npu_latest_source`指定固定仓库`data/source_code/torch_npu` |
| 同一run-id下的`adapt-information.json` | `task-branches`：本次待适配任务标识的字符串数组，不包含键值对 |
| `data/branch-mapping.json` | 跨运行共享的“待适配任务标识 → 实际开发分支名称”映射 |

任务名单由前置准备步骤提供；本步只按任务标识查表，不解析任务类型，也不访问API数据库。当前API适配流程仍由Step 2.1输出完整名单，且仅在本次返回`ADAPT_INFORMATION_READY`时执行；返回`NO_ADAPTATION_REQUIRED`或失败时结束。其他适配任务准备好同样的输入后也可调用此入口。

本仓库由同一个agent独占使用，开发任务串行，本地源码和`data/branch-mapping.json`是唯一的工作记录，以本地最新状态为准。fork用于同步保存成果，不作为另一份更权威的分支或任务记录，也不从fork猜测、复用其他agent的分支。

开始新一批任务前，先按[Step 4.2的完成条件](step4.2-create_pr.md)完成上一批开发与首次发布；恢复当前任务时，使用已有本地分支和进度继续。本地领先于fork或尚未推送是当前开发过程中的正常状态，不因此把本地成果判为异常。

```bash
python3 -B scripts/prepare_development_branch.py \
  --basic-information data/run_data/<run-id>/basic_information.json
```

`upstream`指向主仓，由Step 0负责拉取并更新本地master；`origin`指向fork仓，本批开发成果由Step 4.2推送，评审后的更新由Step 5处理。Step 3直接使用本地master、开发分支和映射表，不拉取远端分支，也不push、不提PR或issue。

## 名称约定

`task-branches`是待适配任务标识的数组，每个元素就是一个待适配任务标识，不再增加任务名称到标识的键值对应。数组必须非空，元素为非空且不重复的字符串；保留原始顺序及内容。它不等于实际Git开发分支，Git命名校验仅用于实际开发分支。

例如本次`adapt-information.json`可以包含：

```json
{
  "task-branches": [
    "fix-collective-timeout",
    "support-checkpoint-restore"
  ]
}
```

对应共享`branch-mapping.json`以任务标识为键：

```json
{
  "fix-collective-timeout": "adapt-set-a31f92c8d570",
  "support-checkpoint-restore": "adapt-set-a31f92c8d570"
}
```

只有`branch-mapping.json`保存“任务标识 → 实际开发分支”的键值对；`development-branch`只在情况二中保存本次准备好的实际开发分支名，情况一不写这个字段。同一实际分支可以关联多个适配任务。任务名单统一称为`task-branches`，不再为任务名单另起字段名；完整关联范围由已有映射及其恢复快照保留。

需要新分支时，由脚本生成简短且不重复的名称。

## 共同的分支准备

两种情况都先完成本地分支查询。情况一当前直接结束；情况二进入Step 4，先由Step 4.1提issue，全部适配完成后由Step 4.2完成提交、push和PR。

1. **确认上一任务已收尾，当前代码可以切换分支。** 使用`data/source_code/.torchnpu-source.lock`（仓库操作锁，防止准备脚本同时操作同一仓库）。整个开发任务仍须串行，不能只在切分支时排队。如果还有未提交的改动、未加入Git的文件，或上一次Git操作尚未完成，就先停下，保留现场，不自动清理或覆盖。

2. **复用本地master。** Step 0已经拉取主仓最新master，再把工作区切到wheel对应版本；本地master仍保留更新后的提交。Step 3直接用它作为基线（本次开发需要包含的主仓版本），不再拉取upstream或fork。整个流程只切到最终开发分支。

3. **按本地映射查找已有成果。** 用`task-branches`中的任务标识查询`data/branch-mapping.json`（任务到实际开发分支的映射表），再检查对应的本地分支：
   - 查到对应关系，并且本地分支存在：记为已有成果，fork是否已经同步不影响分支定位。
   - 没有对应记录：记为新任务；不因fork上有同名分支就自动复用。
   - 有对应记录，但本地分支不存在：停止核查本地记录或源码是否需要恢复，不自动改用远端分支。

4. **整理查询结果。** 将本次任务分为“已有对应本地分支”和“没有对应记录”两类。多个任务对应同一个本地分支时，将这个分支去重，后续需要合并时只处理一次。

整理完成后，根据查询结果进入下面的处理：

- 所有任务都有对应分支：进入“情况一”，当前直接结束。
- 存在没有对应记录的任务：进入“情况二”，基于已有成果准备开发分支，再处理新增任务。

当前只有情况二执行分支准备和`task-branches`的调整，情况一不执行合并。

## 情况一：全部task-branches已有对应分支

确认本次所有任务都有对应的本地分支后，当前直接结束。

- 返回`EXISTING_BRANCHES_READY`，退出码0，`stop-task=true`。
- 保留完整的`task-branches`和原有`branch-mapping.json`。
- 不切换分支、不创建分支、不合并、不运行测试，也不写入`development-branch`。
- 不进入Step 3.1、Step 4或Step 5，不提交issue、代码或PR，也不push。

向用户说明：未来会补充已有分支的合并、测试检验，并根据结果决定是否继续开发和刷新映射；这些内容目前只记录为后续计划，本次不执行。

## 情况二：存在task-branches尚无对应分支

先基于依赖成果切出**新的**实际开发分支，不直接在旧依赖分支上继续新增适配：

- 有依赖分支：从第一个本地依赖分支创建新分支，再合入Step 0准备好的master及其余本地依赖成果。
- 没有依赖分支：从Step 0准备好的master创建新分支，本次全部任务均保留为待开发项。

正常的后续衔接应是：运行依赖分支PR中写明的必要测试，确认后刷新 `branch-mapping.json` 中的 `task-branches` 与 `development-branch` 对应关系并从本次名单移除已测试成功的 `task-branches`。**当前按用户要求暂不实现、不读取PR测试计划，也不运行这些测试，直接进行移除。**

新开发分支准备好后，按顺序完成：

1. **记录本次开发分支。** 将新分支名写入本次`adapt-information.json`的`development-branch`，后续agent就在这个分支上开发。
2. **更新任务对应的分支。** 在`data/branch-mapping.json`中，将本次所有`task-branches`对应到新分支。依赖分支原来还关联了其他任务，也将这些任务的对应关系改到新分支，因为新分支已包含它们的代码。其他映射不变，旧分支保留。
3. **只分发新增任务。** 从本次`adapt-information.json`的`task-branches`中删去原来已有对应分支的任务，保留未对应任务及其原顺序，交给[Step 3.1分发器](step3.1-dispatcher.md)。返回`BRANCH_READY`、`stop-task=false`，退出码0。

例如：本次任务是`["foo", "bar", "baz"]`，foo、bar已有对应分支，baz没有。准备新分支后，映射表中的foo、bar、baz都指向新分支，本次待开发名单只留下`["baz"]`。登记对应关系不代表开发完成；移除已有任务也不代表测试通过，依赖分支PR测试仍留待后续实现。

## 输出字段

后续流程只读取以下结果：

| 字段 | 含义 |
|---|---|
| `status`、`stop-task` | 本次直接结束，还是继续分发 |
| `task-branches` | 情况一保留完整名单；情况二只保留新增任务 |
| `development-branch` | 情况二准备好的实际开发分支；情况一不写 |
| `message` | 简短说明本次结果及暂未执行的事项 |

命令返回以上结果。`adapt-information.json`保存任务名单、情况二的开发分支，以及`git-control`内部进度记录；原始名单、来源分支和提交号等仅供重试恢复，不再重复输出。共享的任务到分支对应关系保存在`data/branch-mapping.json`。

`EXISTING_BRANCHES_READY`且`stop-task=true`时直接结束；`BRANCH_READY`且`stop-task=false`时交给Step 3.1继续分发。暂未实现的合并、测试和开发事项通过`message`说明，不另设占位字段。

## 冲突、失败与重试

### 遇到合并冲突

返回`MERGE_CONFLICT`和冲突文件列表，退出码为2。保留当前开发分支，暂不更新映射表或删减任务名单。

由执行本步的agent逐个解决冲突，保留双方需要的改动。解决后：

1. 将修改加入暂存区，确认没有未解决的冲突。
2. 执行`git diff --cached --check`，检查合并内容。
3. 执行以下命令完成合并提交：

```bash
git -C data/source_code/torch_npu -c core.editor=true merge --continue
```

如果冲突已经解决，但合并提交尚未完成，返回`MERGE_PENDING`，退出码同样为2。

这里只完成合并检查，不运行暂缓的依赖PR测试。

### 其他失败

返回`BRANCH_PREPARATION_FAILED`和错误原因，退出码为1。停止后续开发，保留当前代码和进度，根据错误原因处理，不自动清理改动或重置分支。

### 处理后重试

重新执行本次Step 3入口命令，仍使用同一个run-id的`basic_information.json`。

脚本根据`adapt-information.json`中的`git-control`进度记录，继续使用原开发分支，完成剩余合并及映射、名单更新，不重复创建分支。如果上次只写入了一部分结果，重试时补齐。

重试期间不要更换任务名单；需要更换时，先处理完当前未完成的Git操作。

验证入口：`python3 -B -m unittest discover -s tests -p 'test_prepare_development_branch.py' -v`。这些是实现验证，只使用临时本地Git仓库；上述暂缓的是实际开发分支的业务测试。
