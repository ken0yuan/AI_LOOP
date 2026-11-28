# AI_LOOP

如何启动，请看[启动说明](docs/start-ai-loop.md)。

## 定时跟踪 PR

在 Linux 服务器的 AI_LOOP 根目录运行。脚本会启动 Claude，检查开放 PR 的评审意见和进展。

```bash
python3 run_pr_cycle.py --once           # 立即检查一次
python3 run_pr_cycle.py --every-hours 1  # 立即检查，之后每小时一次
```

把 `1` 换成需要的小时数。工作区忙时跳过本轮，日志在 `data/pr-review-runs/`。需要退出 SSH 后继续运行时，用 `nohup python3 run_pr_cycle.py --every-hours 1 > data/pr-cycle.log 2>&1 &`。
