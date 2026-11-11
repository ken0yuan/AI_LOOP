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
