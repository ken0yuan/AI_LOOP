# Step 0.1 同步 PR 与开发记录

## 第一步 同步 PR 状态

```bash
python3 -B scripts/reconcile_prs.py
```

在 AI_LOOP 根目录执行。主流程在每次 Step 0 完成后执行一次，定时任务直接从这里开始。脚本读取 `data/pr-tracking.json`，返回本轮已收尾的 `merged`、仍需重试的 `pending` 和已核实的开放 PR 清单 `open_pr_reviews`。

脚本按以下顺序同步。

1. **核对 PR 状态。** 索引中已标为 `merged` 的条目直接跳过。其余条目按 `run_data` 找到本批 `pr.json` 核对身份，刷新开放或关闭状态。首次创建索引时从已有发布记录补登。
2. **回填已合入 PR。** 确认合入后先记为 `merged_pending`。核对来源仓库、目标仓库、分支和提交后，从 `pr.json` 取发布时的 `api-status` 快照。旧记录缺少快照时读同批 `adapt-information.json`。按来源分支选表，固定版本使用对应版本表（如 `API支持数据_2.15`），nightly 和旧分支使用 `API支持数据`。在该表定位本 PR 负责的对外 API 正名，按下表写入并将 `分类待审核` 设为 `否`。

   | 发布时的 API 状态 | 适配策略 | 是否支持 |
   |---|---|---|
   | 支持 | 已支持 | 支持，已有“API删除，待下线”则保留 |
   | 待适配 | 待适配 | 保留原值 |
   | 补充testcase | 补充testcase | 保留原值 |

3. **完成收尾。** nightly 将已批准的新分类写入对应的 `references/api-classification/` 文件，并从 `classification-evidence.jsonl` 清理本批 API 的分类理由。相关新定义仍待审核时保留待办。固定版本沿用已有分类。从 `branch-mapping.json` 对应版本分组移除本 PR 已处理的任务映射。全部成功后，在原 `pr.json` 写入 `cleanup-completed: true`，再把索引改为 `merged`，保留索引项和批次文件。若上轮已写入完成标记，本轮只补齐索引状态。

**拿到同步结果后，由调用方决定下一步。**

| 脚本结果 | AI_LOOP 主流程 | 定时任务 |
|---|---|---|
| 退出码 0，`complete=true` | 直接进入 [Step 1](step1-analysis_requirements.md) | 按第二步采集 `open_pr_reviews`，清单为空则结束本轮 |
| 退出码 2，`complete=false` | 处理 `pending` 并重跑，同步成功后进入 Step 1 | 采集清单中已核实的开放 PR，报告 `pending` 供重试 |
| 退出码 1，执行失败 | 根据错误原因处理后重跑 | 报告错误并重试 |

## 第二步 生成每个 PR 的评审记录

**仅定时任务执行本步。** 常规 AI_LOOP 在第一步同步成功后即结束 Step 0.1，直接进入 [Step 1](step1-analysis_requirements.md)。定时任务按 `open_pr_reviews` 遍历开放 PR，对每一项取其 `pr_record` 作为 `PR_RECORD`，执行：

```bash
python3 -B scripts/reconcile_prs.py --review-comments "$PR_RECORD"
```

脚本分页读取该 PR 的普通评论、行内评论和回复，在其原批次目录生成 `pr-review-comments.json`。每个讨论保留平台返回的原文、作者、评论 ID、更新时间、讨论串 ID、解决状态及已有的文件和行号。文件同时记录 PR 身份、发布提交和采集时的远端提交。`candidate_ids` 标出仍需 Step 5 判断的留言版本，人工留言与机器人留言一并交给 Step 5 按内容处理。已解决、已处理的留言和本流程已登记的答复仍保留在讨论上下文中。

脚本返回文件路径 `review_file` 和候选数 `candidate_count`。开放 PR 即使没有评论也生成一份空清单；只有全部分页读取成功才替换上次记录。读取失败的 PR 报告后留待重试，已关闭或已合入的 PR 结束采集。

所有开放 PR 都采集完后，定时任务逐个查看本轮采集成功的 PR。遇到以下情况，就把该 PR 的评审记录交给 [Step 5](step5-pr_tracking&branch_management.md)：

- **有意见要处理。** 有新增或编辑过的评审意见，或上轮还有修复、答复、GPU 复测结果要跟进。
- **有讨论要补标「已解决」。** GitCode 上的行内讨论仍显示「待解决」，但本地已把这条意见的当前版本记入 `processed`。即使本轮没有新意见（`candidate_count=0`），也交给 Step 5 第四步逐串核对。确认整串都已办完，再标为「已解决」。
- **判断是否需要 AI 检视。** 新 PR 从未发起过检视时，交给 Step 5 发起首检。已有检视记录的 PR，只有上一轮提出了新问题，且这些意见已全部解决，才交给 Step 5 发起一次复审。上一轮没有新增问题或结论为「未发现问题」，该轮检视结束。
- **有审批或合入进展要跟踪。** 交给 Step 5 第五步核对最新审批；已发过 `compile` 的，跟踪平台合入结果。

**什么时候可以发 `compile`？** 如果本轮处理了评审意见、更新了 PR 或发了 `/ai review`，这个 PR 就在本轮停在这里。等下一次定时任务重新检查：当前提交与先前轮次的完成记录一致、审批仍有效、也没有待办意见时，才按 Step 5 第五步发 `compile`。已经发过的，跟踪该次结果；平台确认任务超时或发生可重试故障时，按 Step 5 的规则最多重发一次。

从开始采集到 Step 5 处理结束，调度器全程持有 `data/.pr-review-cycle.lock`，保证同一时间只有一轮定时任务在处理 PR。

上游测试匹配尚待实现，当前 `upstream_test_overlaps` 为空。
