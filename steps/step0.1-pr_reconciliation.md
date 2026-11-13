# Step 0.1：同步PR与开发记录

每次运行完成Step 0的源码、安装包和环境准备后，同步已合入PR的适配结果，再进入Step 1。沿用Step 0已拉取的`upstream/master`。

从AI_LOOP根目录执行：

```bash
python3 -B scripts/reconcile_prs.py \
  --basic-information data/run_data/<run-id>/basic_information.json
```

## 第一步：同步已合入PR

[Step 4.2](step4.2-create_pr.md)创建或更新PR后，由执行发布的主agent在本次运行目录保存`pr.json`，记录PR所在仓库、编号、来源分支和提交，以及该PR负责的API状态。这里读取已有运行目录中的这些发布记录，查询对应PR是否合入。

逐API状态由api-adaptation写入`adapt-information.json`的`api-status`，Step 4.2发布时复制到`pr.json`。旧发布记录没有这份快照时，读取同一运行目录的逐API状态。

确认PR已合入，且来源仓库、分支和提交与发布记录一致后，按顺序处理：

1. **回填适配结果。** 按下表更新该PR负责的公开API正名行。
2. **完成分类审核收尾。** 将本批正名的`分类待审核`改为`否`。使用了已批准的新分类时，将定义补入对应的`references/api-classification/`文件；相关新定义仍待审核时，先完成审核。
3. **清理分类理由。** 删除`api-classification/classification-evidence.jsonl`中本批API的分类理由，其他API的理由和共享定义保持原样。
4. **清理开发记录。** 移除`branch-mapping.json`中该PR已处理API的对应记录，再删除本地的`pr.json`跟踪文件。本地分支、origin分支和上游已合入PR均保留。

| 发布时的API状态 | 合入后回填正名行 |
|---|---|
| 支持 | `适配策略=已支持`，`是否支持=支持` |
| 待适配 | `适配策略=待适配`，保留支持结论 |
| 补充testcase | 保留该补测策略 |

别名业务字段和对内条目保持原样，`API删除，待下线`的支持状态保持下线。只处理本PR负责的API，其他任务的记录保持原样。

缺少API状态或同步失败时，保留PR跟踪记录，处理后重跑。尚未合入的PR留待下次同步。

## 第二步：检查上游是否已补测试（待做）

后续从`branch-mapping.json`中的API出发，逐个检查上游是否已有对应测试用例。目前默认未找到，直接继续。

## 完成条件

| 返回结果 | 下一步 |
|---|---|
| `PR_RECONCILIATION_READY`、`complete=true` | 进入[Step 1](step1-analysis_requirements.md) |
| `PR_RECONCILIATION_INCOMPLETE`、退出码2 | 处理返回的事项后重跑本步 |
| 查询或执行失败、退出码1 | 根据错误原因处理后重跑本步 |
