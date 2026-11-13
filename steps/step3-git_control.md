# Step 3：版本控制

根据前置步骤提供的`task-branches`定位已有开发成果。全部任务已有对应本地分支时，当前直接结束；存在未对应任务时，准备一个实际开发分支，交后续Step 3.1分发。

## 输入与入口

前置步骤提供本次任务名单和源码位置，任务类型与业务策略由前置步骤确定。本步只根据任务标识查找已有成果并准备开发分支。

| 输入 | 用途 |
|---|---|
| `data/run_data/<run-id>/basic_information.json` | `torch_npu_latest_source`指定固定仓库`data/source_code/torch_npu` |
| 同一run-id下的`adapt-information.json` | `task-branches`提供本次任务名单 |
| `data/branch-mapping.json` | 保存“任务标识 → 实际开发分支”的对应关系 |

从AI_LOOP根目录执行，相对路径也以该目录为基准：

```bash
python3 -B scripts/prepare_development_branch.py \
  --basic-information data/run_data/<run-id>/basic_information.json
```

仓库操作串行进行，上一批完成[Step 4.2的首次发布](step4.2-create_pr.md)后再开始下一批。本步使用本地分支和映射表；fork用于保存成果，本地领先于fork不影响已有分支的使用。`upstream`主线和本地master由Step 0准备，开发分支的推送由Step 4.2负责。

## 任务与分支的区别

`task-branches`是非空、无重复的任务标识字符串数组，保留原顺序。任务标识与实际Git分支名分别保存：

| 字段或文件 | 示例 | 含义 |
|---|---|---|
| `adapt-information.json.task-branches` | `["foo", "bar", "baz"]` | 本次任务名单 |
| `branch-mapping.json` | `{"foo": "分支A", "bar": "分支A"}` | foo、bar已归分支A负责 |
| `adapt-information.json.development-branch` | `"adapt-set-a31f92c8d570"` | 存在新增任务时，本次准备的实际开发分支 |

一个分支可负责多个任务。新分支名称由脚本生成，任务标识本身不用于Git分支命名校验。

## 第一步：查询已有任务

脚本对仓库操作加锁，并按任务名单查询映射和本地分支：

| 查询结果 | 处理方式 |
|---|---|
| 有映射，且本地分支存在 | 作为已有任务，复用该分支的代码 |
| 没有映射 | 作为新增任务 |
| 有映射，但本地分支不存在 | 停止并核查记录或恢复源码 |

准备新分支前，先处理当前工作树中的未提交改动、未跟踪文件或未完成Git操作。多个已有任务指向同一分支时，该分支只合入一次。

## 第二步：按查询结果处理

### 全部任务已有分支

返回`EXISTING_BRANCHES_READY`、`stop-task=true`，退出码0，结束本次任务。保留原任务名单和映射；本次只完成查询，不切换或合并分支，也不进入后续分发、开发和发布。

### 存在新增任务

1. **创建新开发分支。** 已有任务分支时，从第一个分支创建，再合入本地master及其余任务分支；否则从本地master创建。原分支保持原样。
2. **登记新增任务。** 只将新增任务登记到新分支，已有任务仍指向原分支。继承代码不转移已有任务的归属和收尾责任。
3. **交付待开发名单。** 把新分支写入`development-branch`，从本次`task-branches`移除已有任务，保留新增任务的原顺序。

例如foo、bar归分支A，baz尚无分支。从A创建B后，foo、bar仍归A，只有baz登记到B；本次待开发名单为`["baz"]`。

已有任务在这里按映射移出名单，脚本尚未执行依赖PR中的业务测试；移出名单不代表这些测试已经通过。

新分支准备完成后，返回`BRANCH_READY`、`stop-task=false`，退出码0，进入[Step 3.1：任务分发](step3.1-dispatcher.md)。

## 输出与恢复记录

| 输出字段 | 含义 |
|---|---|
| `status`、`stop-task` | 本次结束，或继续分发 |
| `task-branches` | 全部已有时保留原名单；存在新增时只保留新增任务 |
| `development-branch` | 本次新准备的开发分支，仅存在新增任务时写入 |
| `message` | 本次处理结果 |

`adapt-information.json`同时保存脚本维护的`git-control`恢复进度。任务到开发分支的归属仍以共享`branch-mapping.json`为准。

## 冲突与重试

合并冲突时返回`MERGE_CONFLICT`和冲突文件列表，退出码2；保留当前开发分支，映射和任务名单暂不更新。agent在该分支解决冲突，暂存修改并执行`git diff --cached --check`，然后完成合并：

```bash
git -C data/source_code/torch_npu -c core.editor=true merge --continue
```

冲突已解决、合并提交尚未完成时返回`MERGE_PENDING`，退出码同样为2。其他错误返回`BRANCH_PREPARATION_FAILED`，退出码1，并保留代码和进度。

处理后，使用同一run-id重新执行入口命令。脚本复用原开发分支，补齐剩余合并和记录；重试期间保持原任务选择。

来源任务的记录清理后，保留的来源分支仍可继续使用。重试沿用原开发分支和来源代码，结合前置步骤准备好的master继续处理。

## 尚未实现的部分

全部任务已有分支时，合并已有成果、运行测试及决定后续开发的流程尚未实现。依赖PR的业务测试、评审更新后的分支同步也尚未实现，见[Step 5](step5-pr_tracking&branch_management.md)。
