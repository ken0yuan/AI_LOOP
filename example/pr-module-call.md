# Step 4.2 填写示例 · 导出程序的两个元数据类

当 `adapt-information.json.adapt-plan.step1.agentname` 为 `api-adaptation` 时，按 torch-npu 目标分支的 `.gitcode/PULL_REQUEST_TEMPLATE.md` 和 [API 适配填写模板](../templates/api-adaptation-pr-template.md)填写。下面是已填写的示例。

示例数据来自上次实际运行，不作为新任务的默认值；标题按该次模板的 CheckList 使用类型标签。该记录对应提交 `d3500dcecb26b620cd230eb519884bbf29800451`。[历史 PR #49738](https://gitcode.com/Ascend/pytorch/merge_requests/49738)

**标题**　`test: 补充 ModuleCallEntry 和 ModuleCallSignature 的功能测试`

# 【合入来源】

- [ ] 需求
- [ ] 问题单
- [x] issue/工单
- [ ] 重构优化
- [ ] 资料更新

为导出程序的模块调用记录与签名补充回归测试。在调用图、签名结构或设备执行行为发生变化时，可以用断言定位差异。[issue #6129](https://gitcode.com/Ascend/pytorch/issues/6129)

# 【修改方案】

新增两个测试文件。`ModuleCallEntry` 的测试覆盖构造、序列化和实际导出得到的模块调用图，其中一个用例在目标设备上执行导出程序并与 CPU 结果对照。`ModuleCallSignature` 的测试覆盖参数替换、保留子模块签名和 CPU 与设备导出得到的签名结构。

# 【资料变更】

无。本次提交只新增测试文件。

# 【接口变更】

无。两个被测类的公开接口和实现均无变更。

# 【功能验证】

| 执行端 | 原始测试汇总 | JUnit 原始统计 | 测试退出码 | 执行状态 / 测试结论 |
|---|---|---|---|---|
| NPU Atlas 910B4 ×8 | `20 passed, 1 warning in 9.29s` | `tests=20 failures=0 errors=0 skipped=0` | 0 | 完整 / 通过 |
| GPU NVIDIA GeForce RTX 5070 Ti | `20 passed in 2.13s` | `tests=20 failures=0 errors=0 skipped=0` | 0 | 完整 / 通过 |

# 【CheckList】

- [ ] 代码注释完备，正确记录错误日志
- [ ] 代码实现进行了返回值、空指针等校验
- [x] PR标题正确使用类型标签，如：feat、fix、refactor、docs、test等
- [ ] PR持续集成流水线（CI）执行通过，代码检查无异常

本次只新增测试文件。前两项涉及的实现检查未单独验证。创建时官方 CI 结果待回传。
