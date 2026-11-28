# AI_LOOP 启动说明

## 后台派发

```bash
cd /home/workspace-ycy/SKILLS/AI_LOOP
claude --background \
  --add-dir /home/workspace-ycy/SKILLS \
  --permission-mode acceptEdits \
  --settings '{"worktree":{"bgIsolation":"none"}}' \
  --allowedTools 'Read,Glob,Grep,Write,Edit,Bash,Skill,Agent,Task,SendMessage,ToolSearch' \
  -- '/ai-loop <任务说明>'
```

## 终端运行

```bash
cd /home/workspace-ycy/SKILLS/AI_LOOP
claude --add-dir /home/workspace-ycy/SKILLS --permission-mode acceptEdits \
  --allowedTools 'Read,Glob,Grep,Write,Edit,Bash,Skill,Agent,Task,SendMessage,ToolSearch'
```

```text
/ai-loop <任务说明>
```

## 脚本启动并等待后台子任务

用 `claude -p` 运行 AI_LOOP 时，如果脚本需要等到后台子任务交付，按下面的方式启动：

```bash
cd /home/workspace-ycy/SKILLS/AI_LOOP
CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 claude -p \
  --add-dir /home/workspace-ycy/SKILLS \
  --permission-mode acceptEdits \
  --allowedTools 'Read,Glob,Grep,Write,Edit,Bash,Skill,Agent,Task,SendMessage,ToolSearch' \
  -- '/ai-loop <任务说明>'
```

在 Claude Code 2.1.326 中，主控最后一轮对话结束后，如果后台子任务仍在运行，默认再等 10 分钟就会停止它们。将该变量设为 `0` 可取消这段等待的上限。示例中的设置只对本次命令生效。

如果任务要求发布 PR，脚本结束后还要核对本批次 `pr.json` 中的 PR 链接；即使后台子任务因超时被停止，主进程的退出码也可能是 `0`。
