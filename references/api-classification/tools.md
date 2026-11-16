# 工具

新增定义只登记到 data/api-classification/classification-evidence.jsonl，人工审核通过后再写入本文件。

本文件在分类已确定为工具后使用。先按模块定义选择一个细分模块，再只在该模块内按场景定义选择一个细分场景。示例是定义的辅助证据，不能跳过这两个判断。

## 细分模块定义

| 细分模块 | 职责定义 | 相邻模块的边界 | 审核状态 |
| --- | --- | --- | --- |
| 工具函数 | 帮助开发、测试、互操作、统计、环境诊断及后端接入的通用辅助功能。 | 先排除数据加载、Profiler、TensorBoard、基准测试、梯度检查点和HIP迁移等专门模块。 | 基线 |
| 性能分析 | 采集、记录、组织或展示执行事件、耗时和性能信息，以定位运行开销。 | 对比可控基准测量属于基准测试。设备运行时专用Profiler入口需核对Core中的边界。 | 基线 |
| 数据加载 | 组织数据集、采样、迭代和批处理，将数据提供给训练或推理过程。 | 张量数学算子不属于数据加载。分布式通信不因传递训练数据就进入此模块。 | 基线 |
| 基准测试 | 在受控条件下计时、统计、比较或分析基准实验及指令开销。 | 一般执行跟踪和端到端事件采集检查性能分析。 | 基线 |
| HIP迁移工具 | 转换CUDA相关源码或符号，使代码适配HIP或ROCm工具链。 | 运行时设备操作属于Core。这里只处理迁移和源码转换过程。 | 基线 |
| TensorBoard | 把训练指标、图像、模型图等记录写成供TensorBoard读取的事件数据。 | 普通日志控制检查工具函数。训练计算本身按相应计算模块分类。 | 基线 |
| 梯度检查点 | 通过重计算中间激活来节省训练内存，并管理重计算所需的执行上下文。 | 保存和恢复模型或优化器状态不属于此模块。分布式检查点进入分布式训练与状态管理。 | 基线 |

## 工具函数中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| C++扩展 | 构建、加载或检查PyTorch的C++和CUDA扩展开发环境。 | `torch.utils.cpp_extension.BuildExtension`；`torch.utils.cpp_extension.verify_ninja_availability` | 11 | 基线 |
| DLPack互操作 | 通过DLPack协议在框架之间交换张量或封装共享数据。 | `torch.utils.dlpack.ReadOnlyTensorWrapper`；`torch.utils.dlpack.to_dlpack` | 3 | 基线 |
| FLOP统计 | 记录、估计或汇总算子计算量，管理计算量统计模式。 | `torch.utils.flop_counter.FlopCounterMode`；`torch.utils.flop_counter.shape_wrapper` | 12 | 基线 |
| JIT工具 | 读取JIT日志、运行融合或编译对比实验，服务于开发诊断而非提供TorchScript编程协议。 | `torch.utils.jit.log_extract.run_baseline_no_fusion`；`torch.utils.jit.log_extract.run_test` | 4 | 基线 |
| Pytree工具 | 把嵌套Python结构展平、还原、映射或描述为树结构。 | `torch.utils._pytree.LeafSpec`；`torch.utils._pytree.tree_unflatten` | 18 | 基线 |
| 内部工具 | 实现设备索引、张量打包、异常转发等被多个模块复用的内部辅助功能。 | `torch._utils.ExceptionWrapper`；`torch._utils_internal.get_file_path_2` | 10 | 基线 |
| 后端注册工具 | 接入或重命名PrivateUse1后端，并生成后端对应的Python方法。 | `torch.utils.backend_registration.generate_methods_for_privateuse1_backend`；`torch.utils.backend_registration.rename_privateuse1_backend` | 2 | 基线 |
| 工具函数 | 提供尚不属于专项工具场景的具体辅助能力，如确定性开关、日志控制、有序集合和可视化诊断。需要文档能确认其辅助职责。 | `torch.utils._config_module._Config`；`torch.utils.weak.TensorWeakRef` | 27 | 基线 |
| 测试工具 | 构造测试环境、比较结果、运行测试或控制测试条件。 | `torch.testing._comparison.not_close_error_metas`；`torch.testing.make_tensor` | 22 | 基线 |
| 环境信息收集 | 收集、解析或格式化系统、驱动、编译器及依赖环境信息。 | `torch.utils.collect_env.check_release_file`；`torch.utils.collect_env.run_and_return_first_line` | 9 | 基线 |

## 性能分析中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 性能分析 | 管理通用Profiler的活动、事件、元数据、统计与输出。 | `profile.export_chrome_trace`；`torch.profiler.tensorboard_trace_handler` | 41 | 基线 |
| 自动微分Profiler | 以自动微分Profiler及其事件、时间线和统计数据结构采集或分析执行开销。 | `torch.autograd._disable_profiler`；`torch.autograd.profiler_util._format_memory` | 57 | 基线 |

## 数据加载中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 数据加载 | 实现Dataset、Sampler、DataLoader及批处理或迭代器协议。 | `torch.utils.data.BatchSampler`；`torch.utils.data.random_split` | 52 | 基线 |

## 基准测试中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 基准测试 | 执行可重复的计时或指令统计，组织测量结果并比较基准实验。 | `torch.utils.benchmark.CallgrindStats`；`torch.utils.benchmark.utils.fuzzer.prod` | 28 | 基线 |

## HIP迁移工具中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| HIP迁移工具 | 读取和改写CUDA源码、头文件、API名称与编译相关文本以适配HIP。 | `torch.utils.hipify.hipify_python.compute_stats`；`torch.utils.hipify.hipify_python.str2bool` | 23 | 基线 |

## TensorBoard中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| TensorBoard | 通过SummaryWriter或对应写入器输出可视化事件、指标、图像与图信息。 | `torch.utils.tensorboard.SummaryWriter`；`torch.utils.tensorboard.writer.SummaryWriter.close` | 18 | 基线 |

## 梯度检查点中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 梯度检查点 | 管理激活重计算、检查点上下文和重计算过程中执行状态。 | `torch.utils.checkpoint.CheckpointFunction.backward`；`torch.utils.checkpoint.set_checkpoint_early_stop` | 16 | 基线 |
