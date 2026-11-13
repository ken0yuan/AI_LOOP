# GitCode CLI 发布说明

这里只处理模板正文准备好之后的操作。问题、分析、方案和测试内容必须来自调用方交付的材料；模拟创建不能证明业务材料完整。

## 仓库与模板

模拟主仓为 `yt1525/pytorch`，fork 为 `ken0yuan/pytorch_8990`，目标 `master`。正式任务从调用上下文及真实 upstream / origin 核实，不覆盖现有远端。

2026-11-12 检查了目标 master 的 `.gitcode`、`.gitee`、`.github` 模板，发现 `.gitcode/PULL_REQUEST_TEMPLATE.md`，未发现仓内 issue 模板。PR 要求“合入来源、修改方案、资料变更、接口变更、功能验证、CheckList”，不携带内部流程信息；后续仍需检查本批适用模板。

## 命令核对

使用 `gitcode`；Windows PowerShell 的 gc 是读取文件别名。服务器已核对 CLI `0.16.0`：

```bash
gitcode version
gitcode auth status
gitcode help issue create
gitcode help pr create
gitcode schema 'issue create'
gitcode schema 'pr create'
```

多行正文使用 UTF-8 文件及 `--body-file`，结果用 `--json`。issue 支持 dry-run，PR 此版没有。跨仓用 `-R 主仓 --fork fork仓 --head 分支 --base 目标分支`；显式传 base，此版默认 main，不是本流程的 master。

## 认证与 push

CLI 登录和 Git push 认证分别核实。.2.2 此次没有 SSH 客户端，CLI 的 setup-git 帮助也说明其指向的 helper 尚未实现；不凭 auth status 或 setup-git 成功推断 push 可用。沿用本批已有认证，Token 不进入远端 URL、正文或日志。

helper 按仓库路径匹配时，启用 `credential.useHttpPath=true`。认证失败时检查登录状态、仓库权限和 helper 配置，不执行会打印凭据的诊断命令，不输出认证文件或 helper 返回的凭据。

此前模拟用单次 push 的 credential helper 复用已有登录，未改全局配置。服务器脚本 `/home/workspace-ycy/handoffs/gitcode-issue-pr-20261112/simulation-run/credential-helper.py` 仅匹配模拟 fork 的 HTTPS 请求，不能直接用于其他仓库。脚本无 Token，凭据只交给 Git，不打印或保存。后续优先用实际支持的官方 helper 或可用 Git 认证；调整适配脚本时按所用版本的官方源码核实配置和请求匹配。

## 回读与重试

- issue 创建响应实测为字符串 number 和 html_url，按实际响应保存。
- PR 创建可能已有 number，但 html_url 为空、head / base 为 null。先按编号 pr view 回读完整信息，不能因此重复 create。
- pr view 可能输出部分内容同时以非零退出，需同时检查退出码及必要字段。
- pr diff JSON 可能有文件数但文件 path 为空。需要具体文件时，通过 CLI 只读接口补充：`gitcode api "repos/$UPSTREAM_REPO/pulls/$PR_NUMBER/files"`，核对 filename、patch 及来源目标。
- 超时或 409 先查远端是否已有匹配记录；401 / 403 先处理认证及权限。保留草稿、原始响应及完成的 commit / push 进度后恢复。

## 参考与模拟记录

参考 [官方 skills 仓库](https://gitcode.com/atomgit-cli/skills)，读取提交 `ca0b8850200c15e08bb1d6a679734afc8835c1ff` 中 issue-create、pr-create 工作流。

先前的 [模拟 issue #1](https://gitcode.com/yt1525/pytorch/issues/1) 和 [模拟 PR #1](https://gitcode.com/yt1525/pytorch/merge_requests/1) 只验证 CLI 发布链路。PR 仅新增说明文件，提交 `8490e183403697575a4294ee912fa88bac736594`；它不是依据真实业务材料生成的合格示例，不用其正文作模板。回读和验证数据保留在上述服务器 simulation-run 目录。
