# 设备验证回执

## 命令输入

`RECEIPT` 指向本轮回执。`DEVICE_PLAN` 是当前提交在该设备型号上保留的完整用例清单，使用 `test-plan.json` 格式。`device` 填 `GPU` 或 `NPU`，`required_test_ids` 列出唯一的用例 ID，例如 `test.example.TestExample.test_forward`。`SOURCE_REPO` 是源码仓，`REVIEWED_SHA` 是已审阅的完整提交号。

已有统一回执能独立证明整批覆盖时，可直接传回执和 `--draft`。使用历史 NPU 日志或汇总局部补测时，按 [Step 4.2](../steps/step4.2-create_pr.md) 的完整命令填写。省略 `--draft` 可在终端预览设备行。

## 回执与历史归档

| 来源 | 取哪些记录 |
|---|---|
| GPU 或 NPU 的 unittest 回执 | 读取 `execution-result.json` 的 `device_kind`、`device_model`、`torch_version`、本轮 JUnit 统计、退出码和执行状态。NPU 还需 `torch_npu_version`。用 JUnit 汇总单条用例时，`junit.xml` 与回执放在同一目录。 |
| 旧 GPU Action | 读取 `evidence/report.json` 及同轮 JUnit、设备审计附件。脚本支持原 pytest 归档。 |
| 950 的逐文件 unittest 日志 | 从同轮环境记录取设备型号和构建，从 `summary.json` 对应 PR 的 `files` 取文件路径、SHA256、日志路径和退出码。 |
| 910B4 的逐文件 unittest 日志 | 从本次验证的 `handoff_files` 取测试路径和 SHA256，从 `log` 取日志，从环境报告取设备及构建。 |

只有逐文件 NPU 日志时，将记录关联成下列索引。`log` 可写相对索引文件的路径。未知退出码可省略 `exit`。

```json
{
  "device_kind": "NPU",
  "device_model": "Ascend950DT_9582",
  "torch_version": "2.15.0+cpu",
  "torch_npu_version": "2.15.0.dev20261118",
  "files": [
    {"file": "test/example.py", "sha256": "<测试文件 SHA256>", "log": "results/example.log", "exit": 0}
  ]
}
```

无 JUnit 时从单文件 verbose 日志识别用例及结局，JUnit 列填“未生成”。类或模块清理失败、进程异常退出时，在执行状态保留该文件的错误。只有总数、无法对应具体用例时，那部分计为待验证。已实证的 CPU 回退、纯 Python 契约或平台限制写入 `execution_status`。

## 补测时沿用其他文件

按 Step 4.2 第二步核对文件、辅助依赖、设备型号和实际构建，NPU 包括 torch-npu 构建。GPU 草稿和发布检查读取 `cuda.report` 指向的同一份回执。unittest 回执使用同目录的 `source-files.json` 核对回执、JUnit 和源码哈希。历史回执只有文件名时，可从 `commit.txt` 对应的 Git 对象核对源码；缺少文件证据的范围记为待验证。

例如本轮重跑 C，A、B 的有效结果来自同一份旧回执，在共用命令末尾追加：

```bash
--reuse "$PREVIOUS_RECEIPT" test/a.py test/b.py
```

有多份旧回执时，每份追加一组 `--reuse`；所有文件都已重跑时省略该参数。
