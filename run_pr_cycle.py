#!/usr/bin/env python3
"""AI_LOOP 定时 PR 跟踪入口（Linux）。运行示例见 --help。"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from threading import Event
import time
import uuid


ROOT = Path(__file__).resolve().parent
TOOLS = "Read,Glob,Grep,Write,Edit,Bash,Skill,Agent,Task,SendMessage,ToolSearch"
STEPS = ("SKILL.md", "steps/step0.1-pr_reconciliation.md",
         "steps/step5-pr_tracking&branch_management.md", "scripts/reconcile_prs.py")


def log(message):
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {message}", flush=True)


def try_lock(handle):
    import fcntl  # Linux flock; keep --help available on the Windows workstation.

    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def run_once(claude, data):
    with (data / ".pr-review-cycle.lock").open("a") as lock:
        if not try_lock(lock):
            log("跳过本轮：AI_LOOP 工作区正被占用。")
            return 0
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        directory = data / "pr-review-runs"
        directory.mkdir(exist_ok=True)
        logfile = directory / f"{run_id}.log"
        prompt = (
            f"在当前 AI_LOOP 工作树执行一轮定时 PR 跟踪，轮次标识 {run_id}。"
            "调度器已取得 data/.pr-review-cycle.lock，并在本轮全程持锁。"
            "阅读 SKILL.md 与 steps/step0.1-pr_reconciliation.md，按定时入口从无参 "
            "python3 -B scripts/reconcile_prs.py 开始。按返回结果处理已核实的开放 PR，"
            "按需进入 Step 5，遵守其回读去重和跨轮审批规则。本轮不进入常规新需求适配。"
            "结束时汇总已处理项、未完成项及原因。"
        )
        with logfile.open("x", encoding="utf-8") as output:
            log(f"开始本轮；日志 {logfile}")
            result = subprocess.run(
                [claude, "-p", "--add-dir", str(ROOT.parent), "--permission-mode", "acceptEdits",
                 "--allowedTools", TOOLS, "--output-format", "text", "--", prompt],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                pass_fds=(lock.fileno(),),
                env=dict(os.environ, CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="0"),
            )
            output.write(f"\n[run_pr_cycle] Claude exit {result.returncode}\n")
        log(f"本轮结束，Claude 退出码 {result.returncode}；日志 {logfile}")
        return result.returncode if result.returncode >= 0 else 128 - result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "示例：python3 run_pr_cycle.py --once  或  python3 run_pr_cycle.py --every-hours 1。"
        "循环启动后立即尝试首轮。SIGTERM 停止调度，正在执行的轮次可正常收尾。"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="只尝试一轮，适合外部定时器")
    mode.add_argument("--every-hours", type=int, metavar="N", help="前台循环，每 N 小时尝试一轮")
    args = parser.parse_args()
    if args.every_hours is not None and args.every_hours <= 0:
        parser.error("间隔必须是正整数小时")
    if not sys.platform.startswith("linux"):
        parser.exit(1, "此入口需在 Linux 上运行。\n")
    missing = [name for name in STEPS if not (ROOT / name).is_file()]
    if missing:
        parser.exit(1, "当前 AI_LOOP 缺少定时流程文件：" + "、".join(missing) + "\n")
    claude = shutil.which("claude")
    if not claude:
        parser.exit(1, "当前 PATH 中找不到 claude\n")
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    if args.once:
        return run_once(claude, data)

    with (data / ".pr-review-scheduler.lock").open("a") as scheduler:
        if not try_lock(scheduler):
            parser.exit(1, "本工作区已有 PR 跟踪定时进程\n")
        stop = Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        period = args.every_hours * 3600
        tick = time.monotonic()
        while not stop.is_set():
            run_once(claude, data)
            # Skip elapsed slots even when one round takes several intervals.
            now = time.monotonic()
            tick += (int((now - tick) // period) + 1) * period
            stop.wait(max(0, tick - now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
