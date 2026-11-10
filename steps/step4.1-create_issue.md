# Step 4.1：提交issue

由主agent在调用任何开发subagent之前执行，在主仓`upstream`创建或复用本批任务的issue。

1. 读取本次完整`data/run_data/<run-id>/`材料，根据问题描述、`adapt-plan`中的任务及实际环境整理issue。写清需要解决的问题、本批适配范围和计划如何验证；此时尚未执行的测试写为计划。
2. 本次材料中已有对应issue时直接复用；没有时提交到主仓，避免为同一批任务重复创建。
3. 将issue链接保存到本次run-id材料，供所有subagent读取，也供Step 4.2关联PR。

issue创建或复用成功后，返回Step 4继续调用subagent；失败时先处理失败，不启动开发。
