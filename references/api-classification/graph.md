# 图模式

新增定义只登记到 data/api-classification/classification-evidence.jsonl，人工审核通过后再写入本文件。

本文件在分类已确定为图模式后使用。先按模块定义选择一个细分模块，再只在该模块内按场景定义选择一个细分场景。示例是定义的辅助证据，不能跳过这两个判断。

## 细分模块定义

| 细分模块 | 职责定义 | 相邻模块的边界 | 审核状态 |
| --- | --- | --- | --- |
| FX图变换 | 表示、跟踪、检查或改写FX程序图，包含图解释、Pass、代理跟踪和符号形状。 | 量化场景里的FX工具按量化目的分类。Dynamo和Inductor各有独立模块。 | 基线 |
| Torch.JIT | 使用或管理TorchScript程序、脚本模块和JIT执行机制，包括script、trace、融合等接口。 | 位于工具包中用于日志分析和基准调试的JIT辅助功能检查工具。 | 基线 |
| Dynamo编译器 | 捕获Python执行并生成可编译图，管理Dynamo的配置、守卫、后端和编译上下文。 | 仅提供统一编译入口和控制策略时检查图编译。低层代码生成与内核降低检查Inductor编译器。 | 基线 |
| Inductor编译器 | 把计算图降低、调度或生成为可执行内核，管理Inductor的代码缓存、配置和编译实现。 | Python图捕获属于Dynamo。FX图的通用操作属于FX图变换。 | 基线 |
| 图编译 | 提供统一编译入口、编译器注册和调用控制，以及编译相关的跨组件入口。 | 具体Dynamo或Inductor内部职责进入各自模块。可保存交付的模型导出进入Core。 | 基线 |
| cuda graph | 捕获、构建、管理和重放CUDA执行图，并控制图捕获所需的内存池或标注。 | 仅对CUDA运行时可用性或一般设备资源进行操作时检查Core。图对象的别名仍需阅读文档确认实际职责。 | 基线 |

## FX图变换中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| FX Pass | 针对已有FX图进行分割、元信息传播、算子替换、模式匹配或其他Pass处理。 | `torch.fx.passes.annotate_getitem_nodes.annotate_getitem_nodes`；`torch.fx.passes.utils.source_matcher_utils.get_source_partitions` | 50 | 基线 |
| FX ProxyTensor | 使用代理张量跟踪张量程序并管理相关分解、代理槽位与跟踪上下文。 | `torch.fx.experimental.proxy_tensor.decompose`；`torch.fx.experimental.proxy_tensor.thunkify` | 23 | 基线 |
| FX图变换 | 构造和操作FX的Graph、Node、GraphModule、Tracer、Interpreter及其基础图协议。 | `torch.fx.Graph`；`torch.fx.wrap` | 142 | 基线 |
| FX实验 | 提供FX实验性分析、类型推导、统一化或尚未归入专项场景的研究工具。 | `torch.fx.experimental.accelerator_partitioner.check_dependency`；`torch.fx.experimental.validator.z3str` | 214 | 基线 |
| FX符号形状 | 推导、表示、约束和检查符号形状及其环境和守卫。 | `torch.fx.experimental.symbolic_shapes.CallMethodKey`；`torch.fx.experimental.symbolic_shapes.sym_or` | 128 | 基线 |

## Torch.JIT中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| TorchScript JIT | 提供TorchScript的脚本化、跟踪、类型注解、融合配置和异步执行编程接口。 | `torch.jit.Attribute`；`torch.jit.wait` | 32 | 基线 |
| TorchScript模块 | 表示ScriptModule及脚本化模块的属性、状态、调用与通用Module协议。 | `torch.jit.ScriptModule`；`torch.jit.ScriptModule.zero_grad` | 49 | 基线 |

## Dynamo编译器中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| Dynamo编译器 | 管理Python图捕获、Dynamo后端、配置和编译自动微分等Dynamo实现能力。 | `torch._dynamo.OptimizedModule`；`torch._dynamo.utils.to_fake_tensor` | 37 | 基线 |

## Inductor编译器中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| Inductor编译器 | 执行Inductor图降低、模式优化、内核调度、代码生成或代码缓存。 | `torch._inductor.CompiledArtifact.load`；`torch._inductor.virtualized.V.graph.wrapper_code.generate_fallback_kernel` | 39 | 基线 |

## 图编译中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 动态编译 | 提供动态编译主入口及其调用控制，中心职责是决定函数或Module如何被动态编译执行。 | `torch.compile`；`torch.compiled_with_cxx11_abi` | 4 | 基线 |
| 图模式 | 提供跨组件图编译启停控制、Dynamo禁用和图捕获边界等辅助协议。 | `torch._C._dynamo.eval_frame._set_lru_cache`；`torch._disable_dynamo` | 2 | 基线 |
| 编译器 | 注册、选择或控制编译器行为，管理编译缓存、守卫策略及统一编译器接口。 | `torch.compiler.PrecompileError`；`torch.compiler.wrap_numpy` | 29 | 基线 |

## cuda graph中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| CUDA图捕获 | 构造和管理CUDA图对象，执行capture、replay、reset、debug及相关图生命周期操作。 | `torch.cuda.graphs.CUDAGraph.capture_begin`；`torch.cuda.graphs.register_graph_replay_start_hook` | 29 | 基线 |
| CUDA设备相关 | 在图模式的cuda graph模块下，仅指图捕获上下文、图内存池句柄和图捕获标注等入口。 | `torch.cuda.graph`；`torch.cuda.graph_pool_handle` | 6 | 基线 |
