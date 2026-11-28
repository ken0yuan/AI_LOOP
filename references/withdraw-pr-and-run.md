# 撤回未合并的 PR 并清理本批运行记录

适用于需要将同一批 API 从头重跑，且已经向 GitCode 提交 PR 的情况。完成后，测试 PR 和本批专用 issue 均已关闭，来源分支已从 fork 和服务器本地仓库删除，本批运行目录、跟踪索引项和分支映射均已清理。GitCode 保留关闭记录和讨论历史。

## 1. 从发布回执确定清理范围

在 AI_LOOP 的 `data/run_data/<run-id>/pr.json` 读取目标仓库、PR 编号和 URL、目标分支、来源 fork、来源分支、发布提交和 issue URL。用 `issue.json` 核对 issue 的归属。删除目录前，将这些值以及下表命中的任务键记在目录外，供核验和中断后续做。

| 对象 | 如何精确定位 |
|---|---|
| PR 和来源分支 | 从 GitCode 回读 PR 的状态、`merged`、来源仓、来源分支及其 HEAD。对照发布回执，确认该分支仍专属于本批且没有后续他人的提交 |
| PR 跟踪索引 | 在 `data/pr-tracking.json` 找到 **PR URL 和相对运行目录均匹配** 的一条记录 |
| 任务分支映射 | 从 `adapt-information.json` 取 `task-branches`，按本批版本定位 `data/branch-mapping.json` 的分组。只选任务键属于本批且值仍等于来源分支的条目。旧版平铺映射按 nightly 解释 |
| 本地分支 | 在本批实际使用的 torch-npu 仓库核对本地分支 HEAD，查看 `git worktree list`。若有工作树正在使用该分支，先核实占用和未提交工作。也要核对其他批次是否仍依赖它 |

如果远端已合并，转入目标仓库的 revert PR 流程，并核对 Step 0.1 是否回填过 API 支持状态或分类。保留原运行记录，直到合入影响处理完毕。若分支 HEAD 已变化、issue 由多批共用或映射已转属，先核实受影响对象，保留其他任务的工作。

## 2. 关闭远端记录，删除来源分支

1. 在 GitCode 关闭目标 PR，回读确认状态为关闭且 `merged=false`。关闭本批创建并专用的 issue，回读确认。复用的共享 issue 保留。
2. 在服务器本地 torch-npu 仓库核对推送远端确实指向回执里的 fork。删除该 fork 上的来源分支。下面的命令使用刚核实的完整 HEAD SHA，远端分支发生变化时 Git 会拒绝删除。

   ```bash
   git remote get-url --push "$FORK_REMOTE"
   git push --force-with-lease="refs/heads/$BRANCH:$EXPECTED_SHA" \
     "$FORK_REMOTE" ":refs/heads/$BRANCH"
   ```

3. 再核对本地同名分支的 HEAD、工作树占用和批次归属，删除已确认撤回的本地开发分支。两端分支删除即可，无需单列清理 `origin/<分支>`。对未合并的 PR，目标分支无需执行 `git revert`。

## 3. 清理 AI_LOOP 活动记录

选择本批没有发布、准备或评审任务正在写入的窗口。共享 JSON 文件按现有写入机制处理，与 `scripts/reconcile_prs.py` 保持相同的锁顺序：先取 `data/source_code/.torchnpu-source.lock`，再取 `data/.pr-tracking.lock`。在锁内重新读取最新内容，按下面的条件更新，逐文件原子写回。

1. 从 `data/pr-tracking.json` 移除步骤 1 确认的那一条记录。
2. 从 `data/branch-mapping.json` 移除本批已确认的任务键。逐键复核版本分组和当前分支值，保留同组其他任务及其他版本分组。
3. 两份索引写回成功后，核对运行目录的解析路径确实是 `data/run_data` 的直接子目录、目录名与回执一致，且路径未通过符号链接指向别处。删除该批次目录。目录内的 PR 回执、评审记录、用例报告和日志随目录清理。

共享的 API 支持库、分类资产、wheel 缓存和 venv 继续保留。彻底重跑时，在新批次核对实际加载的初始 wheel 和测试结果来源。

## 4. 核验与中断恢复

- 远端 PR 已关闭且未合并，本批专用 issue 已关闭。
- 远端查询成功且结果中没有目标分支，本地 `refs/heads/<分支>` 也已不存在。网络或鉴权错误不能算作分支已删除。
- 两份 JSON 均可解析。索引中已无本批 URL 和运行目录，目标分组已释放本批任务。与清理前对比，其他条目保持一致。
- `data/run_data/<run-id>/` 已不存在。若中途失败，使用步骤 1 留下的编号、路径、分支和任务键重新核对，只补做未完成项。若同名分支已被重建或任务键已有新归属，保留新对象。

## 实例：nightly !49738

| 对象 | 本批清理范围 |
|---|---|
| GitCode | 关闭 `Ascend/pytorch!49738` 和本批 issue `Ascend/pytorch#6129` |
| 来源分支 | `ken0yuan/pytorch` 和服务器本地 torch-npu 仓库中的 `adapt-set-633d5e250fcc` |
| 跟踪索引 | `data/pr-tracking.json` 中指向 !49738 及下方运行目录的 1 条记录 |
| 分支映射 | `nightly` 组中的 `adapt-torch.export.exported_program.ModuleCallEntry`、`adapt-torch.export.exported_program.ModuleCallSignature`，共 2 条 |
| 运行目录 | `data/run_data/adapt-torch.export.exported_program.ModuleCallEntry-261116/` |

此表是范围示例。执行时重新回读 PR 状态和分支 HEAD。
