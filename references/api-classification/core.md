# Core

新增定义只登记到 data/api-classification/classification-evidence.jsonl，人工审核通过后再写入本文件。

本文件在分类已确定为Core后使用。先按模块定义选择一个细分模块，再只在该模块内按场景定义选择一个细分场景。示例是定义的辅助证据，不能跳过这两个判断。

## 细分模块定义

| 细分模块 | 职责定义 | 相邻模块的边界 | 审核状态 |
| --- | --- | --- | --- |
| 设备管理 | 管理设备可用性、执行环境、后端行为及设备资源。API的主要对象是CPU或加速器以及其运行时状态。 | 显式图捕获与图重放优先检查图模式。通用事件与流抽象检查流与事件管理。 | 基线 |
| 张量管理 | 管理存储对象、张量类型表示、序列化和张量附属元信息。主要结果是数据的存放、恢复或表示状态。 | 张量数值计算与变形归基础计算。分布式训练状态保存检查分布式训练与状态管理。 | 基线 |
| 模型打包与导出 | 把模型及其计算、权重、依赖或调用约定转成可保存和交付的产物，并管理该产物。 | 执行期编译加速和图内改写检查图模式。普通张量或存储读写检查张量管理。 | 基线 |
| 流与事件管理 | 管理通用异步执行顺序、完成通知或事件处理。API的中心对象是流、事件或监控事件订阅。 | 后端专有流包装先判断其职责是否属于设备运行时。统计量聚合不属于事件处理。 | 基线 |
| 算子注册 | 定义或登记算子接口、schema、实现及派发行为，使框架能够发现和调用算子。 | 运行已注册算子的计算归基础计算。算子重载协议与Python调用分派检查其他。 | 基线 |
| 自动微分 | 构建、控制或检查求导过程，包括梯度计算、求导图和自定义求导协议。 | 测量执行时间或收集性能事件检查工具的性能分析。跨进程分布式求导检查分布式训练与状态管理。 | 基线 |
| 自动混合精度 | 协调自动类型转换与梯度缩放，使训练在混合精度条件下工作。 | 通用AMP接口属于本模块。设备专有兼容AMP入口同时核对设备管理中的CUDA AMP场景。 | 基线 |
| 其他 | 容纳函数式变换、模型仓库、多进程、Future、系统统计、算子重载及框架内部守卫等明确的框架运行职责。 | 只在下列具体场景有定义时选择本模块。不能把职责尚不明确的接口直接放入其他。 | 基线 |

## 设备管理中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| CPU相关 | 查询或控制CPU执行环境、设备式抽象及CPU侧兼容运行功能。 | `torch.cpu.Stream`；`torch.cpu.synchronize` | 12 | 基线 |
| CUDA AMP | 提供CUDA专用或兼容的autocast、GradScaler及其缩放状态管理。 | `torch.cuda.amp.GradScaler`；`torch.cuda.amp.grad_scaler._MultiDeviceReplicator` | 17 | 基线 |
| CUDA NVTX | 给CUDA执行添加可被性能工具读取的范围、标记和嵌套标注。 | `torch.cuda.nvtx.mark`；`torch.cuda.nvtx.range_start` | 6 | 基线 |
| CUDA Profiler | 开启、关闭或控制CUDA运行时性能采集会话。 | `torch.cuda.profiler.profile`；`torch.cuda.profiler.stop` | 3 | 基线 |
| CUDA显存管理 | 分配、释放、统计、缓存或快照化CUDA设备内存及分配器状态。 | `torch.cuda.memory.CUDAPluggableAllocator`；`torch.cuda.memory_usage` | 40 | 基线 |
| CUDA流 | 管理CUDA专有流和事件的实现对象、外部流包装与等待同步。 | `torch.cuda.streams.Event`；`torch.cuda.streams.Stream.wait_stream` | 18 | 基线 |
| CUDA设备相关 | 管理CUDA设备状态、运行能力、上下文或通用设备对象。若主职责已明确为图捕获，优先检查图模式而不是仅凭cuda名字判定。 | `torch.cuda.AcceleratorError`；`torch.cuda.utilization` | 143 | 基线 |
| CUDA调优 | 设置、读取或运行CUDA算子算法选择与调优结果，关注执行算法的性能选择。 | `torch.cuda.tunable.enable`；`torch.cuda.tunable.write_file_on_exit` | 21 | 基线 |
| CUDA随机数 | 维护CUDA设备上的随机数状态、种子和随机数发生器上下文。 | `torch.cuda.random.get_rng_state`；`torch.cuda.random.set_rng_state_all` | 9 | 基线 |
| MPS设备相关 | 管理Apple MPS设备的执行、内存、同步、事件和随机数等运行状态。 | `torch.mps.compile_shader`；`torch.mps.synchronize` | 23 | 基线 |
| MTIA设备相关 | 管理MTIA设备的可用性、执行流、内存、同步和随机数等设备功能。 | `torch.mtia.DeferredMtiaCallError`；`torch.mtia.synchronize` | 51 | 基线 |
| XPU图捕获 | 管理XPU后端图捕获、图对象及图内存池。当前基线把XPU图功能纳入设备管理。 | `torch.xpu.graphs.XPUGraph.capture_begin`；`torch.xpu.graphs.make_graphed_callables` | 13 | 基线 |
| XPU显存管理 | 分配、释放或统计XPU内存，管理XPU缓存分配器与内存池。 | `torch.xpu.memory.MemPool`；`torch.xpu.memory_usage` | 23 | 基线 |
| XPU设备相关 | 管理XPU设备能力、运行状态、同步、功耗和随机数等通用功能。 | `torch.xpu.Event`；`torch.xpu.utilization` | 88 | 基线 |
| accelerator | 通过统一加速器抽象管理设备选择、同步、图和内存等跨后端运行功能。 | `torch.accelerator.Graph`；`torch.accelerator.stream` | 35 | 基线 |
| backends | 查询或配置CPU、MPS及其他后端的可用能力、执行选项与实现特征。 | `torch.backends.cpu.get_cpu_capability`；`torch.backends.xeon.run_cpu.create_args` | 39 | 基线 |
| 后端CUDA配置 | 选择CUDA计算库、精度控制、注意力后端和CUDA后端行为开关。 | `torch.backends.cuda.SDPAParams`；`torch.backends.cuda.sdp_kernel` | 31 | 基线 |

## 张量管理中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| C++绑定 | 提供框架级C++状态、运行配置或底层对象操作的Python入口。张量存储引用计数的专门操作在张量管理下使用同名场景。 | `torch._C._storage_Use_Count` | 1 | 基线 |
| 全局默认类型配置 | 读取或改变随后创建张量所采用的默认类型或精度设置。 | `torch.set_default_dtype` | 1 | 基线 |
| 字节存储 | 以字节为基础组织原始存储及其类型、缓冲区或构造信息。 | `torch.ByteStorage`；`torch.ByteStorage.from_buffer` | 4 | 基线 |
| 序列化 | 把普通框架数据或存储写入、恢复为文件表示，并控制安全反序列化所允许的对象。 | `torch.serialization.StorageType`；`torch.serialization.skip_data` | 10 | 基线 |
| 无类型存储 | 管理不携带张量dtype解释的底层字节存储，包括共享内存、大小、设备与类型视图转换。 | `torch.UntypedStorage`；`torch.UntypedStorage.untyped` | 45 | 基线 |
| 符号浮点 | 表示可能由跟踪或形状推理产生的符号浮点值，提供其浮点语义或表示方法。 | `torch.SymFloat`；`torch.SymFloat.is_integer` | 5 | 基线 |
| 类型存储 | 管理带类型解释的存储对象及其设备、大小、共享状态和类型转换。 | `torch.TypedStorage`；`torch.TypedStorage.untyped` | 42 | 基线 |
| 返回类型 | 表示多返回值算子的命名返回容器及其字段结构，核心是结果元信息。 | `torch.return_types.cummax`；`torch.return_types.min` | 4 | 基线 |

## 模型打包与导出中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| ONNX导出 | 把模型转为ONNX表示或执行导出检查、验证、配置、注册和诊断。 | `torch.onnx.DiagnosticOptions`；`torch.onnx.verification.verify_onnx_program` | 83 | 基线 |
| 模型导出 | 捕获和管理可交付的模型程序及其签名、约束、动态形状和导出参数。结合文档判定其目标是模型产物。 | `torch.export.Constraint`；`torch.export.unflatten.unflatten` | 42 | 基线 |
| 模型打包 | 把Python模块、代码依赖和模型资源组合成可重新导入的包，并进行包内资源访问。 | `torch.package.Directory.has_file`；`torch.package.analyze.trace_dependencies.trace_dependencies` | 37 | 基线 |

## 流与事件管理中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 事件 | 表示通用执行完成或同步事件，支持记录、查询、等待与计时。 | `torch.Event`；`torch.Event.wait` | 6 | 基线 |
| 多进程 | 建立和管理进程、队列、共享资源或同步设施。其他模块下的多进程指通用进程管理，流与事件管理下的多进程只指进程间事件同步。 | `torch.multiprocessing.Event` | 1 | 基线 |
| 流 | 表示通用异步执行流，控制流的相互等待、同步和事件记录。 | `torch.Stream`；`torch.Stream.wait_stream` | 7 | 基线 |
| 系统监控 | 其他模块下表示统计项、聚合方式及测量数据。流与事件管理模块下表示监控事件的生成、订阅、处理和时间信息。 | `torch.monitor.Event`；`torch.monitor.unregister_event_handler` | 10 | 基线 |

## 算子注册中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 算子库注册 | 定义算子及schema，登记实现、伪实现、自动微分或编译相关注册信息。 | `torch.library`；`torch.library.wrap_triton` | 21 | 基线 |

## 自动微分中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 自动微分 | 计算梯度、控制梯度记录或前向自动微分上下文。关注求导动作及是否记录求导过程。 | `Function.backward`；`torch.autograd.vjp` | 49 | 基线 |
| 自动微分Function | 实现自定义求导函数或函数级微分操作，包括Function类的前后向协议、保存状态以及functional求导工具。 | `torch.autograd.function.BackwardCFunction`；`torch.autograd.functional.vjp` | 47 | 基线 |
| 自动微分上下文 | 管理自定义求导中保存的张量、非可微标记、脏标记和梯度物化选项，中心对象是FunctionCtx上下文。 | `FunctionCtx.mark_dirty`；`FunctionCtx.set_materialize_grads` | 4 | 基线 |
| 自动微分图 | 操作反向传播图、保存张量钩子和求导图上下文，关注图级关系和执行行为。 | `torch.autograd.graph.GradientEdge`；`torch.autograd.graph.set_warn_on_accumulate_grad_stream_mismatch` | 16 | 基线 |
| 自动微分节点 | 查看求导节点的名称、元数据、后继连接并注册节点钩子，中心对象是Node。 | `Node.metadata`；`Node.register_prehook` | 5 | 基线 |

## 自动混合精度中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 自动混合精度 | 控制通用自动类型转换及梯度缩放，使混合精度计算保持合适的数值范围。 | `torch.amp.GradScaler`；`torch.amp.scale_loss` | 9 | 基线 |

## 其他中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| ATen C++绑定(NN) | 把张量集合打包、拆包或解析设备与类型转换参数，服务于NN与框架之间的绑定辅助。若直接执行激活、卷积或损失计算则进入基础计算的神经网络。 | `torch._C._nn._parse_to`；`torch._C._nn.unflatten_dense_tensors` | 3 | 基线 |
| C++绑定 | 提供框架级C++状态、运行配置或底层对象操作的Python入口。张量存储引用计数的专门操作在张量管理下使用同名场景。 | `torch._C.DisableTorchFunction`；`torch._C._xpu_getCurrentRawStream` | 36 | 基线 |
| 函数式变换 | 把函数变为求导、向量化、雅可比或函数式调用等形式，核心结果是函数或执行方式的转换。 | `torch.func.debug_unwrap`；`torch.func.vmap` | 13 | 基线 |
| 函数式变换内部 | 维护变换过程的批维、树结构、参数校验和内部包装状态，服务于函数式变换的实现。 | `torch._functorch.aot_autograd.aot_compile_joint_with_descriptors`；`torch._functorch.vmap.tree_unflatten` | 10 | 基线 |
| 多进程 | 建立和管理进程、队列、共享资源或同步设施。其他模块下的多进程指通用进程管理，流与事件管理下的多进程只指进程间事件同步。 | `torch.multiprocessing.Array`；`torch.multiprocessing.start_processes` | 21 | 基线 |
| 子类 | 维护张量子类模式及其进入退出上下文，关注框架模式控制。直接表示、转换或检查张量子类的数据对象时核对基础计算的子类场景。 | `torch._subclasses.FakeTensorMode`；`torch._subclasses.fake_tensor.unset_fake_temporarily` | 2 | 基线 |
| 异步并发工具 | 表示未来结果及其等待、回调和汇聚过程，关注尚未完成的异步任务。 | `torch.futures.Future`；`torch.futures.wait_all` | 9 | 基线 |
| 模型仓库 | 定位、缓存、下载或加载模型仓库资源和预训练模型入口。 | `torch.hub._get_torch_home`；`torch.hub.set_dir` | 8 | 基线 |
| 注意力机制 | 表示注意力后端的控制枚举和框架侧元信息。实际注意力计算、掩码和专用内核接口检查神经网络。 | `torch.nn.attention.SDPBackend.name` | 1 | 基线 |
| 算子重载 | 检查、定义或触发Python层算子重载协议和参数分派，核心职责是决定调用哪个实现。 | `torch.overrides.TorchFunctionMode`；`torch.overrides.wrap_torch_function` | 10 | 基线 |
| 系统监控 | 其他模块下表示统计项、聚合方式及测量数据。流与事件管理模块下表示监控事件的生成、订阅、处理和时间信息。 | `torch.monitor.Aggregation`；`torch.monitor.data_value_t` | 9 | 基线 |
| 编译守卫 | 维护框架执行或跟踪上下文中的一致性条件和守卫状态。属于具体编译器实现的守卫则检查图模式。 | `torch._guards.TracingContext`；`torch._guards.tracing` | 5 | 基线 |
