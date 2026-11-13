# Step 4.2：根据方案、开发和测试材料填写模板并提交 PR

接收前序 agent 的问题分析、实现方案、开发结果和测试记录，结合真实代码差异填写 PR 模板，再完成 commit、push 和创建或更新 PR。AI_LOOP 中由主 agent 在本批开发全部完成、必要验证材料收齐后执行；未完成或验证阻塞时返回 Step 4 处理。

## 输入：前序 agent 交接的成果材料

| 输入 | 说明 |
|---|---|
| 问题与关联 issue | 已核实的 issue 链接 / 记录，以及问题描述和分析材料 |
| 方案材料 | 方案分析、设计、选择依据、影响范围和验收标准 |
| 开发结果 | 修改说明、文件范围、任务完成状态、与原方案的差异、未完成事项 |
| 测试记录 | 实际场景、方法或命令、环境、结果、日志 / 报告、执行时的代码版本；计划不能代替记录 |
| 发布上下文 | 源码目录、实际开发分支、主仓、fork、目标分支、结果目录及已有 PR 记录 |
| 模板与规范 | 用户指定模板，或目标仓 PR 模板及贡献要求 |

这些材料可以分别放在文件里，也可以合并在一份交接报告中；文件名和格式不固定，调用方交付实际路径即可。读取实际内容及必要引用，不要求上游转换成新格式。AI_LOOP 中另接收根目录及本次 `data/run_data/<run-id>/`，环境、源码和 `development-branch` 沿用已有基础信息，不把任务标识当 Git 分支。

## 1. 选择模板，对照材料与代码

模板优先级：用户指定模板 → 目标仓适用的 PR 模板 → [默认 PR 模板](../templates/pr-template.md)。本次目标仓实际存在 `.gitcode/PULL_REQUEST_TEMPLATE.md`；默认模板保留其“合入来源、修改方案、资料变更、接口变更、功能验证、CheckList”结构，给出材料填写位置。后续仍需读取目标分支的最新模板，不因已有本地模板而跳过仓库规范。

| 模板内容 | 来源与核对方式 |
|---|---|
| 合入来源、关联问题 | issue 与问题分析；说明真实问题，不用内部交接过程充当业务背景 |
| 修改方案 | 方案分析 + 实际代码 diff；写实际实现，不能把未实现设计写成完成 |
| 资料变更 | 开发说明 + diff；列出文档变化，或有依据地填“不涉及” |
| 接口变更 | 设计 / 影响分析 + 代码；说明用户可见变化、兼容性和配套资料 |
| 功能验证 | 前序 agent 的真实记录，包含场景、方法、环境、版本和结果 |
| CheckList | 分项核验；只勾选确认通过或依仓库规则确认不适用的项目 |

核对全部任务与结果、分支和文件。阅读目标分支到开发分支的完整 diff，包括依赖改动，不能只读未提交差异或照抄“已完成”。方案、开发记录和代码不一致时，先确认实际方案及变更原因。

已有测试记录覆盖充分且对应当前代码时可直接引用，不为整理正文机械重跑。缺少记录、测试后又改了相关代码、日志与结论冲突或必要测试失败时，交回补充或执行受影响的验证，解决前不发布。“用例已生成”不等于运行通过。

按 [PR #49212 的完整闭环参考](../references/success-case-49212.md) 验收实际执行：补充 API 用例需确认测试开关已开启，实际使用了要求的设备，本批范围确实执行，结果不是整批跳过。当前 api-adaptation 的 Step1 明确暂不开发，交回 `not_run` 时应返回 Step 4 保留阶段性成果，不能通过编写 PR 正文补成“已测试通过”，也不在发布步骤里绕过未实现的执行环节。

## 2. 填写正文，检查缺项

复制所选模板写为 UTF-8 的 `pr-body.md`，标题按目标仓要求使用 `fix`、`feat`、`docs`、`test` 等实际变更类型。逐栏从材料填写，在本地保留“正文结论 → 文件及具体章节 / 日志”的来源对照；不直接发布内部路径、单据或整份交接材料。

验证结果逐项标明通过、失败、未执行或不适用及原因，不把平台可合并状态当成 CI 成功。需创建 PR 才触发的 CI 可写“待触发 / 待回传”，对应自检项保持未勾选，交 Step 5 跟踪；仓库另有发布门槛时按其执行。

缺少实际方案、完成证据、必要测试记录或对应 issue 时，保存草稿并列出缺项，返回调用方补充；不编造。非本类改动适用的栏目经核实后写“不涉及 / 不适用”及依据，不能批量默认填“不涉及”。提交前清除占位符和填写提示，核对栏目符合仓库模板。仅要求草稿时到此交付。

## 3. 核实分支，完成 commit 和 push

`SOURCE` 为源码目录，`RUN_DIR` 为结果目录，均用绝对路径；`BRANCH` 为实际开发分支，`UPSTREAM_REPO` / `FORK_REPO` 为核实的主仓 / fork 的 `owner/repo`，`BASE` 为本批目标分支，现有 AI_LOOP 流程使用 `master`。

确认当前分支、origin 的 push 地址及 upstream 与交接相符，没有冲突或未完成 merge / rebase；不要为模拟修改正式源码仓远端。

```bash
gitcode auth status
git -C "$SOURCE" branch --show-current
git -C "$SOURCE" status --short --branch
git -C "$SOURCE" remote -v
git -C "$SOURCE" fetch upstream "$BASE"
git -C "$SOURCE" log --oneline "FETCH_HEAD..HEAD"
git -C "$SOURCE" diff "FETCH_HEAD...HEAD"
```

fetch 仅获取比较基线，不自动合并或重置。按仓库要求完成相关检查；有 pre-commit 配置时执行相应检查，无配置则记录不适用。只暂存审查过的本批文件，核对无无关内容后 commit；已有本批提交直接复用，不重复或创建空提交。

```bash
git -C "$SOURCE" add -- path/to/reviewed-file
git -C "$SOURCE" diff --cached --check
git -C "$SOURCE" diff --cached
git -C "$SOURCE" commit -m "$COMMIT_TITLE"
git -C "$SOURCE" push -u origin "$BRANCH"
git -C "$SOURCE" rev-parse HEAD
git -C "$SOURCE" ls-remote --heads origin "refs/heads/$BRANCH"
```

按本批状态逐项执行，不无条件重复 commit / push。提交后确认本批成果均已提交；远端 SHA 等于待发布 HEAD 才继续。认证或推送失败时保留现场恢复，不 force push。

## 4. 创建或更新 PR

先回读已有 `pr.json` / 链接，没有时查询目标仓同一来源分支与目标分支的所有状态 PR，并核对来源 fork，避免同名分支混淆。已有匹配开放 PR 时更新原分支和正文，不重复创建；已关闭或合并时先核查本批状态。

```bash
gitcode pr list -R "$UPSTREAM_REPO" --state all \
  --head "$BRANCH" --base "$BASE" --paginate --json
gitcode pr create -R "$UPSTREAM_REPO" \
  --fork "$FORK_REPO" --head "$BRANCH" --base "$BASE" \
  --title "$PR_TITLE" --body-file "$RUN_DIR/pr-body.md" --json
```

创建前完成正文检查及发布准备。用户已要求提交时直接执行；仅编写文档 / 草稿不实际发布。此版没有 PR dry-run，不能用 create 预览。

更新已有 PR 时，使用真实 `PR_NUMBER`：

```bash
gitcode pr edit "$PR_NUMBER" -R "$UPSTREAM_REPO" \
  --title "$PR_TITLE" --body-file "$RUN_DIR/pr-body.md" --json
```

## 5. 回读与交接

保存原始响应，回读核对正文符合模板且与材料一致，核对 issue 链接、来源仓 / 分支 / SHA、目标仓 / 分支及文件范围：

```bash
gitcode pr view "$PR_NUMBER" -R "$UPSTREAM_REPO" --json
gitcode pr diff "$PR_NUMBER" -R "$UPSTREAM_REPO" --json
```

保留 `pr-body.md`、本地来源对照和真实测试材料引用，在 `pr.json` 记录 `status`、`repository`、`number`、`url`、`fork`、`head`、`base`、`commit`、`remote-sha`、`issue-url`、`verification`；AI_LOOP 中保留 `run-id`。`verification` 指向已有材料，不强制复制或改写上游报告。

任务和必要验证完成、代码已推送、PR 已核实后交 [Step 5](step5-pr_tracking&branch_management.md) 跟踪评审及 CI。失败时交回原因和已有材料，恢复本批，不虚报完成或开始下一批。

CLI 参数、认证与响应兼容问题按需阅读 [发布说明](../references/gitcode-cli-publishing.md)，操作细节不能替代材料整理与模板填写。
