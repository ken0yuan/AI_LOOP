# 分布式

新增定义只登记到 data/run_data/<run-id>/api-classification/classification-evidence.jsonl，人工审核通过后再写入本文件。

本文件在分类已确定为分布式后使用。先按模块定义选择一个细分模块，再只在该模块内按场景定义选择一个细分场景。示例是定义的辅助证据，不能跳过这两个判断。

## 细分模块定义

| 细分模块 | 职责定义 | 相邻模块的边界 | 审核状态 |
| --- | --- | --- | --- |
| 分布式通信 | 在进程或设备之间交换数据、梯度或控制信息，管理通信组、请求和后端。 | 张量或模型如何分片执行检查分布式并行。数据持久化检查分布式训练与状态管理。 | 基线 |
| 分布式并行 | 描述或执行模型、参数和张量跨设备分布的训练布局与并行策略。 | 通信原语作为底层操作进入分布式通信。优化和保存状态的职责检查分布式训练与状态管理。 | 基线 |
| 分布式训练与状态管理 | 维护跨进程训练的优化状态、求导状态以及分布式检查点的保存和恢复。 | 启动进程和弹性恢复检查基础或弹性容错。通信请求的生命周期检查分布式通信。 | 基线 |
| 分布式弹性运行与容错 | 处理弹性成员关系、进程重启、故障恢复、健康检查和弹性运行环境。 | 固定进程启动或普通会合基础设施检查分布式基础。是否属于弹性环境的检测也属于本模块。 | 基线 |
| 分布式基础 | 提供启动、会合、键值存储、分片元信息、组合式契约及可复用分布式算法等基础设施。 | 请求传输或进程组操作检查通信。故障恢复与弹性成员管理检查弹性容错。 | 基线 |

## 分布式通信中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 分布式-ProcessGroup | 以实验性ProcessGroup对象提供通信组、配置、能力和集体通信方法。与公开进程组场景按文档所描述的接口层区分。 | `torch.distributed._dist2.ProcessGroup`；`torch.distributed._dist2.register_backend` | 55 | 基线 |
| 分布式-RPC | 执行远程调用并管理RRef、RPC代理、后端选项和远程对象。 | `torch.distributed.rpc.BackendType`；`torch.distributed.rpc.shutdown` | 33 | 基线 |
| 分布式-Work | 表示异步通信请求及其等待、完成、结果和同步协议。 | `torch.distributed.Work`；`torch.distributed.Work.wait` | 12 | 基线 |
| 分布式-c10d后端 | 通信模块下表示c10d进程组、后端及集体通信的实现辅助。弹性模块下只表示c10d中的弹性运行环境检测。 | `torch.distributed.distributed_c10d.BackendConfig`；`torch.distributed.distributed_c10d.split_group` | 58 | 基线 |
| 分布式-后端 | 定义分布式Backend枚举、后端注册关系及后端能力描述。 | `torch.distributed.Backend`；`torch.distributed.Backend.register_backend` | 3 | 基线 |
| 分布式-梯度通信 | 访问DDP梯度桶、梯度列表与缓冲区，服务于梯度通信hook。 | `torch.distributed.GradBucket`；`torch.distributed.GradBucket.set_buffer` | 7 | 基线 |
| 分布式-进程组 | 管理公开ProcessGroup对象、选项、成员与通信组基本行为。 | `torch.distributed.ProcessGroup`；`torch.distributed.ProcessGroupNCCL.Options` | 4 | 基线 |
| 分布式-通信原语 | 提供函数式、低层或融合的集体通信原语及其包装。 | `torch.distributed._all_gather_base`；`torch.distributed._functional_collectives.wait_tensor` | 20 | 基线 |
| 分布式后端 | 描述C++绑定层的分布式后端选项与底层后端对象。 | `torch._C._distributed_c10d.Backend.Options` | 1 | 基线 |
| 分布式训练 | 通信模块下表示公开分布式collective、进程组控制与一般分布式通信状态。弹性模块下表示公开的弹性运行环境检测。 | `torch.distributed.DistBackendError`；`torch.distributed.utils._to_kwargs` | 85 | 基线 |
| 对称内存算子注册 | 通过对称内存算子命名空间执行专用集体通信或内存传输操作。 | `torch.ops.symm_mem.all_to_all_vdev`；`torch.ops.symm_mem.two_shot_all_reduce_` | 9 | 基线 |

## 分布式并行中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 分布式-DTensor张量并行 | 使用分布式张量及放置策略组织张量并行和分布式张量计算。 | `torch.distributed.tensor.DTensor`；`torch.distributed.tensor.zeros` | 65 | 基线 |
| 分布式-DeviceMesh | 描述设备网格、维度关系、子网格和网格与进程组之间的对应关系。 | `torch.distributed.device_mesh.DeviceMesh`；`torch.distributed.device_mesh.init_device_mesh` | 17 | 基线 |
| 分布式-FSDP | 以完全分片方式管理参数、梯度、通信调度和FSDP模块训练状态。 | `torch.distributed.fsdp.BackwardPrefetch`；`torch.distributed.fsdp.wrap.wrap` | 90 | 基线 |
| 分布式-nn | 提供分布式神经网络功能和远程Module抽象，包括其可微通信接口。 | `torch.distributed.nn.all_gather`；`torch.distributed.nn.functional.all_reduce` | 7 | 基线 |
| 分布式-流水并行 | 划分模型执行阶段、微批和调度计划，实现流水线并行。 | `torch.distributed.pipelining.Pipe`；`torch.distributed.pipelining.stage.build_stage` | 20 | 基线 |

## 分布式训练与状态管理中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 分布式-优化器 | 管理跨进程优化参数和状态，包括分片优化器与局部SGD优化器。 | `torch.distributed.optim.DistributedOptimizer`；`torch.distributed.optim.ZeroRedundancyOptimizer.step` | 14 | 基线 |
| 分布式-检查点保存 | 训练与状态管理下表示分布式检查点的读写、计划、存储和元数据。基础模块下表示跨模块共享的检查点异常协议。 | `torch.distributed.checkpoint.ChunkStorageMetadata`；`torch.distributed.checkpoint.utils._get_failure_dict` | 122 | 基线 |
| 分布式-自动微分 | 执行或管理跨进程的自动微分上下文、梯度与反向计算。 | `torch.distributed.autograd.backward`；`torch.distributed.autograd.is_available` | 4 | 基线 |

## 分布式弹性运行与容错中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 分布式-c10d后端 | 通信模块下表示c10d进程组、后端及集体通信的实现辅助。弹性模块下只表示c10d中的弹性运行环境检测。 | `torch.distributed.distributed_c10d.is_torchelastic_launched` | 1 | 基线 |
| 分布式-启动器 | 基础模块下表示启动配置与启动代理。弹性模块下表示提供重启与弹性成员能力的启动入口。 | `torch.distributed.launcher.api.elastic_launch`；`torch.distributed.launcher.elastic_launch` | 2 | 基线 |
| 分布式-弹性训练 | 管理弹性worker、agent、会合状态、错误、重启、事件、指标和健康检查。 | `torch.distributed.elastic.agent.server.ElasticAgent`；`torch.distributed.elastic.utils.macros.substitute` | 122 | 基线 |
| 分布式训练 | 通信模块下表示公开分布式collective、进程组控制与一般分布式通信状态。弹性模块下表示公开的弹性运行环境检测。 | `torch.distributed.is_torchelastic_launched` | 1 | 基线 |

## 分布式基础中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 分布式-会合 | 发现参与进程、分配会合信息并管理会合处理器。 | `torch.distributed.rendezvous`；`torch.distributed.rendezvous.rendezvous` | 3 | 基线 |
| 分布式-分片 | 描述数据分片的元信息、分片张量属性及构造协议。 | `torch.distributed._shard.metadata.ShardMetadata`；`torch.distributed._sharding_spec.ChunkShardingSpec` | 7 | 基线 |
| 分布式-启动 | 解析分布式启动参数并运行固定进程启动入口。 | `torch.distributed.launch.launch`；`torch.distributed.run.run` | 6 | 基线 |
| 分布式-启动器 | 基础模块下表示启动配置与启动代理。弹性模块下表示提供重启与弹性成员能力的启动入口。 | `torch.distributed.launcher.LaunchConfig`；`torch.distributed.launcher.api.launch_agent` | 3 | 基线 |
| 分布式-存储 | 使用分布式键值Store完成信息交换和协调，包括TCPStore、FileStore及原子键值操作。 | `torch.distributed.FileStore.__init__`；`torch.distributed.TCPStore.port` | 31 | 基线 |
| 分布式-对称内存 | 分配、设置或协调对称内存区域及其后端。直接执行通信请求的算子检查通信模块。 | `torch.distributed._symmetric_memory.empty`；`torch.distributed._symmetric_memory.set_signal_pad_size` | 11 | 基线 |
| 分布式-检查点保存 | 训练与状态管理下表示分布式检查点的读写、计划、存储和元数据。基础模块下表示跨模块共享的检查点异常协议。 | `torch.distributed.checkpoint.CheckpointException` | 1 | 基线 |
| 分布式-算法 | 提供Join、梯度通信hook、模型平均等可组合的分布式训练算法。 | `torch.distributed.algorithms.Join`；`torch.distributed.algorithms.model_averaging.utils.get_params_to_average` | 31 | 基线 |
| 分布式-组合式 | 为分布式功能提供组合式契约、状态关联及组合式封装入口。 | `torch.distributed._composable.contract`；`torch.distributed._composable_state._get_module_state` | 10 | 基线 |
