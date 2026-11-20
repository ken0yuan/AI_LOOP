# 固定版本 PR 示例：PyTorch 2.15 flex_attention

来源：[Ascend/pytorch !49929](https://gitcode.com/Ascend/pytorch/merge_requests/49929)，提交 `a9fe21f3`。表中结果为该提交的验证记录。

**标题**　`【API看护补齐】test: 补充 PyTorch 2.15 flex_attention 及默认 Flash 实现恢复接口的功能测试`

# 【合入来源】

- [ ] 需求
- [ ] 问题单
- [x] issue/工单
- [ ] 重构优化
- [ ] 资料更新

[关联 issue #6040：API testcase 补齐](https://gitcode.com/Ascend/pytorch/issues/6040)

为 PyTorch 2.15 补齐 `torch.nn.attention` 下 4 个 API 的功能测试，验证可编程注意力的输出与梯度、辅助输出的请求与返回，以及默认 Flash Attention 实现的恢复行为。

# 【修改方案】

在 `test/api_supplement/torch/nn/attention/` 下新增 4 个测试文件，共 42 个用例。

- `flex_attention.flex_attention`（22 例）对照参考结果验证不同精度、缩放、分数修改函数、块掩码和分组查询注意力的输出，并检查反向梯度、辅助输出、重复调用及非法输入。
- `flex_attention.AuxRequest`（6 例）验证请求标志的默认值和组合、NamedTuple 行为及非法关键字，并检查请求标志是否控制返回的辅助字段。
- `flex_attention.AuxOutput`（8 例）验证默认字段和显式构造，检查单独或同时请求 `lse`、`max_scores` 时的返回值，以及混合设备输入的异常行为。
- `restore_flash_attention_impl`（6 例）验证移除自定义实现并恢复默认实现的行为，覆盖重复恢复、静默模式、切换实现和空句柄。

`flex_attention.flex_attention` 和 `AuxOutput` 两个测试类在 NPU、CUDA 均不可用时，于测试基类初始化前跳过，确保设备对照用例只在有加速卡时执行。

# 【资料变更】

不涉及。

# 【接口变更】

不涉及。

# 【功能验证】

| 执行端及设备 | 实测包版本 | 原始测试汇总 | JUnit 原始统计 | 测试退出码 | 执行状态 / 测试结论 |
|---|---|---|---|---|---|
| NPU Ascend 910B4 | PyTorch 2.15.0+cpu，torch-npu 2.15.0.dev20261118 | unittest：flex_attention 22 例 OK，AuxOutput 8 例 OK，restore 6 例 OK；AuxRequest 7 例 OK 中保留 6 例 | 未生成（unittest） | 四个文件的验证进程均为 0 | 完整，42/42 通过 |
| GPU NVIDIA RTX 5070 Ti | PyTorch 2.15.0+cu132 | pytest：flex_attention 22 例、AuxOutput 8 例通过；AuxRequest、restore 各 6 个保留用例通过 | 30 例报告：`tests=30 failures=0 errors=0 skipped=0`；其余 12 个匹配条目全部通过 | 30 例的进程为 0；其余 12 例无独立退出码 | 完整，42/42 通过 |
| NPU Ascend 950DT_9582 | PyTorch 2.15.0+cpu，torch-npu 2.15.0.dev20261118 | unittest：restore 6 例 OK；AuxRequest 7 例 OK 中保留 6 例。另 30 例待验证 | 未生成（unittest） | 已有两份文件记录均为 0；待验证的 30 例无退出码 | 部分完成，12/42 通过，30 条待验证 |

# 【CheckList】

- [x] 代码注释完备，正确记录错误日志
- [x] 代码实现进行了返回值、空指针等校验
- [x] PR标题正确使用类型标签，如：feat、fix、refactor、docs、test等
- [ ] PR持续集成流水线（CI）执行通过，代码检查无异常

本 PR 仅新增测试，生产代码的错误日志、返回值及空指针处理不涉及。完整 PR CI 结果待确认。
