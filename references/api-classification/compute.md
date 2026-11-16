# 基础计算

新增定义只登记到 data/api-classification/classification-evidence.jsonl，人工审核通过后再写入本文件。

本文件在分类已确定为基础计算后使用。先按模块定义选择一个细分模块，再只在该模块内按场景定义选择一个细分场景。示例是定义的辅助证据，不能跳过这两个判断。

## 细分模块定义

| 细分模块 | 职责定义 | 相邻模块的边界 | 审核状态 |
| --- | --- | --- | --- |
| 基础计算 | 创建、访问、变换或计算张量数值，覆盖通用算子、类型和随机数等计算基础。 | 具备明确神经网络、稀疏、量化、概率分布、优化或信号职责时进入对应专项模块。设备配置检查Core。 | 基线 |
| 神经网络 | 构造、执行或辅助训练神经网络层、激活、损失、参数及其结构。 | 量化网络模块进入量化。通用矩阵或张量数学操作进入基础计算。 | 基线 |
| 优化器 | 更新模型参数、维护优化状态、控制学习率或进行优化过程中的参数平均。 | 分布式优化器检查分布式训练与状态管理。通用Module的训练方法不能仅凭方法名归优化器。 | 基线 |
| 概率分布 | 表示概率分布、概率约束和变量变换，支持抽样、概率密度及分布统计量。 | 通用随机数状态控制属于基础计算。分布对象中继承的普通方法仍按所属分布对象判定。 | 基线 |
| 稀疏张量 | 表示和计算稀疏或半结构化稀疏数据，并管理其布局及专用派生张量对象。 | 稀疏派生类继承的运算沿所属稀疏对象分类。仅操作普通稠密张量时检查基础计算。 | 基线 |
| 量化 | 表示、转换、校准、配置或分析量化模型及低精度算术。覆盖QAT、观察者、量化模块和图量化流程。 | 量化工作流使用FX或导出机制时，仍以量化目的判断本模块。通用图变换检查图模式。 | 基线 |
| 信号处理 | 提供信号处理专用构造和操作。当前已学习的场景是用于加窗的窗函数。 | 快速傅里叶变换沿基线放在基础计算。出现新的信号职责且无对应场景时保留模块并请求扩展场景。 | 基线 |
| 自定义API | 提供torch-npu等扩展包定义的加速计算接口，按实际数学或融合计算职责细分。 | 当前已学习的是15类NPU计算。设备管理、通信和编译扩展不能仅因属于扩展包就进入本模块。 | 基线 |

## 基础计算中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| ATen算子注册 | 通过ATen算子命名空间定位或调用已登记的张量算子及重载。实际增加或修改注册关系归Core算子注册。 | `torch.ops.aten._dyn_quant_pack_4bit_weight`；`torch.ops.aten.view.dtype` | 10 | 基线 |
| 基础张量 | 表示基础张量类型、尺寸、标量类型、低层检查及基础类型构造。一般数学运算检查顶层算子/类。 | `torch.BFloat16Storage`；`torch.version.cuda.split` | 92 | 基线 |
| 子类 | 表示、转换、检查或构造特殊张量子类的数据语义。仅控制框架子类模式上下文时检查Core。 | `torch._subclasses.fake_tensor.FakeTensorConverter`；`torch._subclasses.functional_tensor.mb_unwrap_functional_tensor` | 4 | 基线 |
| 嵌套张量 | 创建、转换或访问可表达不规则形状的嵌套张量及其填充表示。 | `torch.nested._internal.nested_tensor.NestedTensor`；`torch.nested.to_padded_tensor` | 8 | 基线 |
| 张量类方法 | 由普通Tensor实例提供的运算、变形、类型转换和属性访问。沿基线保留Tensor类限定写法的接口粒度。 | `Tensor.H`；`Tensor.zero_` | 517 | 基线 |
| 快速傅里叶变换 | 在时域与频域之间进行FFT变换及频率、移位等相关辅助操作。 | `torch.fft.fft`；`torch.fft.rfftn` | 20 | 基线 |
| 拟随机数 | 产生低差异序列或管理Sobol等准蒙特卡洛序列的状态和推进。 | `torch.quasirandom.SobolEngine`；`torch.quasirandom.SobolEngine.reset` | 5 | 基线 |
| 掩码张量 | 表示显式掩码张量，或以掩码选择、聚合和修改张量数据。 | `torch.masked`；`torch.masked_select` | 5 | 基线 |
| 特殊数学函数 | 计算特殊函数、概率相关数学函数或专门数学近似。 | `torch.special.airy_ai`；`torch.special.zeta` | 50 | 基线 |
| 硬件计算实现 | 暴露特定计算库或硬件后端的低层计算入口，如MIOpen或MKLDNN计算实现。 | `torch.miopen_batch_norm`；`torch.mkldnn_rnn_layer` | 11 | 基线 |
| 线性代数 | 执行矩阵分解、线性方程、范数、特征值或其他线性代数操作。 | `torch.linalg.cholesky`；`torch.linalg_solve_triangular` | 43 | 基线 |
| 随机数生成器 | 表示可单独操作的Generator对象，管理其种子、设备、状态及克隆。 | `torch.Generator`；`torch.Generator.set_state` | 10 | 基线 |
| 随机数管理 | 管理框架级随机数种子、状态及保存恢复上下文，不以独立Generator对象为中心。 | `torch.random.fork_rng`；`torch.random.thread_safe_generator` | 8 | 基线 |
| 顶层算子/类 | 通过顶层接口创建或计算普通张量，包含通用算术、索引、形状及计算控制。若有更具体的数值领域场景则优先使用。 | `__getstate__`；`view` | 740 | 基线 |

## 神经网络中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| ATen C++绑定(NN) | 以C++绑定形式执行激活、池化、填充、损失等NN计算。参数解析和张量打包等绑定辅助操作检查Core。 | `torch._C._nn.elu`；`torch._C._nn.upsample_linear1d` | 10 | 基线 |
| 注意力机制 | 配置或执行注意力计算、构造注意力偏置与变长注意力相关辅助对象。 | `torch.nn.attention.SDPBackend`；`torch.nn.attention.varlen.varlen_attn_out` | 12 | 基线 |
| 注意力机制(flex) | 构造可定制的注意力分数、块掩码和FlexAttention内核选项，并执行相应计算。 | `torch.nn.attention.flex_attention.AuxOutput`；`torch.nn.attention.flex_attention.or_masks` | 47 | 基线 |
| 神经网络RNN工具 | 把变长序列打包、填充或还原为RNN可用表示，并管理PackedSequence元信息。 | `torch.nn.utils.rnn.PackedSequence`；`torch.nn.utils.rnn.unpad_sequence` | 15 | 基线 |
| 神经网络函数式API | 以函数形式执行神经网络操作，包括卷积、激活、归一化、损失和插值。 | `torch.nn.functional.ScalingType`；`torch.nn.functional.upsample_nearest` | 110 | 基线 |
| 神经网络剪枝工具 | 生成、应用、组合或移除参数剪枝掩码和剪枝参数化。 | `torch.nn.utils.prune.BasePruningMethod`；`torch.nn.utils.prune.random_unstructured` | 48 | 基线 |
| 神经网络参数初始化 | 根据规定分布、常数或增益策略初始化网络参数。 | `torch.nn.init._calculate_correct_fan`；`torch.nn.init.zeros_` | 16 | 基线 |
| 神经网络工具 | 对网络参数进行裁剪、展平、参数化、归一化或层融合等通用辅助操作。 | `torch.nn.utils._named_member_accessor.swap_tensor`；`torch.nn.utils_weight_norm` | 49 | 基线 |
| 神经网络模块 | 面向使用者提供神经网络层和Module对象及其成员，关注公开层对象的调用与训练协议。 | `torch.nn.AdaptiveAvgPool1d`；`torch.nn.parameter.is_lazy` | 396 | 基线 |
| 神经网络模块类 | 描述nn.modules实现层中的模块类定义及其成员。与公开模块职责相同，当前路径明确落在模块实现层时保留这一基线粒度。 | `torch.nn.modules.Linear`；`torch.nn.modules.utils.consume_prefix_in_state_dict_if_present` | 329 | 基线 |

## 优化器中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 优化器 | 管理参数更新公式、参数组、优化状态和步骤钩子。具体优化器类及其继承方法属于此场景。 | `torch._fused_adamw_`；`torch.optim.zero_grad` | 338 | 基线 |
| 优化器-SWA | 累计或平均模型参数，管理SWA及相关平均模型、状态和批归一化更新。 | `torch.optim.swa_utils.AveragedModel`；`torch.optim.swa_utils.update_bn` | 56 | 基线 |
| 优化器-学习率调度 | 按步数、指标、周期或预热策略计算和维护学习率计划。 | `torch.optim.lr_scheduler.ChainedScheduler`；`torch.optim.lr_scheduler.StepLR.step` | 98 | 基线 |
| 优化器基类 | 提供所有优化器共享的参数组、更新步骤、清零梯度、状态保存和钩子协议。输入为基类接口时使用本场景。 | `Optimizer.add_param_group`；`Optimizer.zero_grad` | 10 | 基线 |

## 概率分布中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 概率分布 | 表示具体概率分布，进行抽样、密度计算、支持集检查和分布统计。 | `torch.distributions.LogisticNormal`；`torch.distributions.wishart.Wishart.variance` | 444 | 基线 |
| 概率分布-变换 | 定义可组合的变量变换、逆变换、形状映射及雅可比关系。 | `torch.distributions.transforms.AbsTransform`；`torch.distributions.transforms.identity_transform` | 22 | 基线 |
| 概率分布-基类 | 定义概率分布共享的抽样、概率、统计量与参数验证协议，中心对象是Distribution基类。 | `torch.distributions.distribution.Distribution`；`torch.distributions.distribution.Distribution.variance` | 18 | 基线 |
| 概率分布-约束 | 描述参数或样本的有效域，检查区间、形状和约束条件。 | `torch.distributions.constraints.Constraint`；`torch.distributions.constraints.stack` | 16 | 基线 |

## 稀疏张量中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 稀疏张量 | 创建或操作一般COO、CSR等稀疏布局、索引和值，并执行其专用计算。 | `torch.sparse.addmm`；`torch.sparse_csr_tensor` | 17 | 基线 |
| 稀疏张量-半结构化稀疏(CUDA专属Beta) | 表示和操作受约束的半结构化稀疏张量及CUTLASS、CUSPARSELT等实现，包括其继承的普通Tensor操作。 | `torch.sparse.semi_structured.SparseSemiStructuredTensorCUSPARSELT`；`torch.sparse.semi_structured.SparseSemiStructuredTensorCUTLASS.zero_` | 1018 | 基线 |

## 量化中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 量化-FX流程 | 使用FX图完成量化准备、融合、转换及对应自定义配置或图模块管理。 | `torch.ao.quantization.fx.custom_config.ConvertCustomConfig`；`torch.ao.quantization.fx.utils.return_arg_list` | 38 | 基线 |
| 量化-伪量化 | 在训练中模拟量化与反量化误差，管理伪量化器及相关参数。 | `torch.ao.quantization.fake_quantize.FakeQuantize`；`torch.ao.quantization.fake_quantize.fused_wt_fake_quant_range_neg_127_to_127` | 22 | 基线 |
| 量化-后端配置 | 描述目标后端允许的量化模式、dtype、观察规则及模块匹配配置。 | `torch.ao.quantization.backend_config.BackendConfig`；`torch.ao.quantization.backend_config.utils.remove_boolean_dispatch_from_name` | 38 | 基线 |
| 量化-工具 | 执行量化校验、dtype判断、量化范围和中间数据转换等辅助功能。 | `torch.ao.quantization.utils.NodePattern`；`torch.quantization.default_eval_fn` | 16 | 基线 |
| 量化-数值套件 | 对比浮点和量化模型的激活、权重或中间数值，收集和展示量化误差。 | `torch.ao.ns._numeric_suite.Logger`；`torch.ao.ns.fx.utils.compute_sqnr` | 38 | 基线 |
| 量化-映射 | 管理浮点、QAT、量化、引用量化等模块或算子之间的映射关系。 | `torch.ao.quantization.quantization_mappings.get_default_compare_output_module_list`；`torch.ao.quantization.quantization_mappings.no_observer_set` | 13 | 基线 |
| 量化-流程 | 组织量化准备、校准、转换、存根和顶层配置入口。若API专用于某一观察器、映射或配置对象则优先使用更具体场景。 | `torch.ao.quantization.DeQuantStub`；`torch.ao.quantization.swap_module` | 27 | 基线 |
| 量化-神经网络模块 | 表示以QAT或量化训练为目的的基础网络模块及其浮点转换接口。已明确完成量化部署的模块检查量化-量化模块。 | `torch.ao.nn.qat.Conv2d`；`torch.ao.nn.quantizable.modules.rnn.LSTMCell.from_params` | 21 | 基线 |
| 量化-融合模块 | 把卷积、线性、归一化和激活等多个层组合为融合模块，包括其QAT或量化实现。 | `torch.ao.nn.intrinsic.BNReLU2d`；`torch.ao.nn.intrinsic.quantized.modules.linear_relu.LinearTanh` | 104 | 基线 |
| 量化-观察者 | 采集张量范围、直方图和其他统计量，推导scale、zero_point等量化参数。 | `torch.ao.quantization.observer.AffineQuantizedObserverBase`；`torch.ao.quantization.observer.per_channel_weight_observer_range_neg_127_to_127` | 43 | 基线 |
| 量化-配置 | 描述或分配量化参数与观察器选择方案，例如qconfig及配置映射。 | `torch.ao.quantization.qconfig.QConfig`；`torch.ao.quantization.qconfig_mapping.get_default_qat_qconfig_mapping` | 24 | 基线 |
| 量化-量化模块 | 表示可部署的量化或动态量化网络层及其成员和浮点转换关系。 | `torch.ao.nn.quantized.BatchNorm2d`；`torch.ao.nn.quantized.functional.upsample_bilinear` | 70 | 基线 |

## 信号处理中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| 信号处理窗函数 | 根据长度和形状参数构造用于信号加窗的权重序列。 | `torch.signal.windows.bartlett`；`torch.signal.windows.windows.nuttall` | 20 | 基线 |

## 自定义API中的细分场景

| 细分场景 | 职责描述与选择条件 | 训练例 | 训练条数 | 审核状态 |
|---|---|---|
| NPU自定义-AdamW融合 | 以NPU融合实现执行AdamW参数与优化状态更新。 | `torch_npu.npu_apply_adam_w` | 1 | 基线 |
| NPU自定义-AllGather+MM | 把跨设备AllGather与矩阵乘法组合为一次融合计算。 | `torch_npu.npu_all_gather_base_mm` | 1 | 基线 |
| NPU自定义-Grouped Matmul | 对多组输入执行分组矩阵乘法或融合累加。 | `torch_npu.npu_grouped_matmul_add_` | 1 | 基线 |
| NPU自定义-MM+ReduceScatter | 把矩阵乘法与跨设备ReduceScatter组合为融合计算。 | `torch_npu.npu_mm_reduce_scatter_base` | 1 | 基线 |
| NPU自定义-Matmul+Add | 将矩阵乘法和加法合并执行。 | `torch_npu.matmul_add` | 1 | 基线 |
| NPU自定义-MoE Token置换 | 根据路由或专家分配重排MoE token，使其适合专家计算。 | `torch_npu.npu_moe_token_permute` | 1 | 基线 |
| NPU自定义-MoE Token逆置换 | 恢复经过MoE专家路由置换的token顺序。 | `torch_npu.npu_moe_token_unpermute` | 1 | 基线 |
| NPU自定义-RMS归一化 | 根据均方根统计量对输入进行归一化与缩放。 | `torch_npu.npu_rms_norm` | 1 | 基线 |
| NPU自定义-RoPE旋转位置编码 | 对查询或键向量应用旋转式位置编码。 | `torch_npu.npu_rotary_mul` | 1 | 基线 |
| NPU自定义-SwiGLU | 执行SwiGLU门控激活及其融合计算。 | `torch_npu.npu_swiglu` | 1 | 基线 |
| NPU自定义-交叉熵 | 计算分类交叉熵损失。 | `torch_npu.npu_cross_entropy_loss` | 1 | 基线 |
| NPU自定义-加法RMSNorm | 把残差加法与RMS归一化组合为融合计算。 | `torch_npu.npu_add_rms_norm` | 1 | 基线 |
| NPU自定义-卷积 | 执行NPU自定义二维卷积计算。 | `torch_npu.npu_conv2d` | 1 | 基线 |
| NPU自定义-数据类型转换 | 把张量数据转换为指定数值类型。 | `torch_npu.npu_dtype_cast` | 1 | 基线 |
| NPU自定义-融合注意力 | 以融合NPU接口执行注意力计算及其关联步骤。 | `torch_npu.npu_fusion_attention` | 1 | 基线 |
