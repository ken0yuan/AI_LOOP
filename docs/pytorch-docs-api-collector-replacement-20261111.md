# PyTorch 文档 API 采集脚本替换说明

当前采用只读 objects.inv 的采集方式，不再读取三千多个 API 正文页面。

- [服务器原有旧脚本](../scripts/pytorch_api_diff/extract_docs_crawl.py)：保留原位作为旧方案对照。
- [当前采集器](../scripts/extract_docs_inventory.py)：共 131 行，Step 2.1 通过刷新入口调用。
- [索引提取测试](../tests/test_extract_docs_inventory.py)：验证索引范围、名称、请求次数、失败保护和版本捕获。

## 新旧方案区别

旧方案从入口导航和模块页面寻找 API，再抓取独立页面、提取签名、回填和纠正名称。路径过滤及相对链接处理曾导致遗漏；旧流程还漏选了四个包含正式声明的页面，合计 49 项。

新方案直接读取官方 objects.inv，解析登记的 Python API 对象和声明锚点，规范化名称、合并同一声明目标，然后按原模块到短名称数组格式写出。正常成功路径只有一次索引下载，不下载 HTML，也不再提取签名和源码链接。

收录 function、class、exception、method、attribute、property、data 七类 Python 对象；模块和章节不计为 API。保留下划线名称，链接含目录也不排除。普通正文提及或示例调用不凭字符串匹配加入。

同一文档目标去重不等于运行时对象身份判定，wheel 存在性和 is 别名核验仍由后续流程负责。

## 输入输出

命令保持：

```text
python scripts/extract_docs_inventory.py <version> [output.json] [--cache-dir DIR]
```

输出文件仍默认 api.json，缓存目录仍默认 .docs_cache。2.14.0 仍对应文档路径 2.14，main 保持 main。数字版本复用索引缓存，main 在进入提取时重新读取当前索引，索引请求原有的有界重试保留。

业务 JSON 的模块名单结构和排序不变，保留 _signatures、_sources 两个键，值都为空对象。不生成伪造的参数或源码地址，也不承诺保留原有签名数据。

```json
{
  "torch": ["a", "b"],
  "_signatures": {},
  "_sources": {}
}
```

以上仅为格式示意。Step 2.1 只消费名称清单；首次出现版本继续由 api_first_seen.py 从同一次 fetch_inventory() 返回的索引头捕获，不增加网络请求，不向此 JSON 添加字段。

索引下载、格式或目标校验失败时抛错，不以空清单覆盖上次成功输出。文档版本记录、数据库事务和 wheel 核验继续使用现有入口。

## 已核对的历史快照

| 快照 | D 盘原版样本 | 相对链接修正版 | 只读索引名称 |
|---|---:|---:|---:|
| 2.14 | 7666 | 7995 | 8044 |
| main（2.16 开发快照） | 7366 | 8291 | 8340 |

这些是已保存输入快照的结果，不是硬编码数量，也不是 is 去重后的独立适配任务数。D 盘样本与服务器原有旧文件分别记录，不能据此认定两者逐字节相同。

与此前逐页读取的候选比较，两个快照的模块和 API 名单完全一致。此次变化是停止正文抓取，将签名和源码映射明确置空。main 更新后，数量以当次索引为准。

## 附录：两个版本共同缺漏的 49 项

`torch.compiler.config`，18 项：

```text
torch.compiler.config.accumulated_recompile_limit
torch.compiler.config.allow_unspec_int_on_nn_module
torch.compiler.config.assume_static_by_default
torch.compiler.config.automatic_dynamic_shapes
torch.compiler.config.capture_dynamic_output_shape_ops
torch.compiler.config.capture_scalar_outputs
torch.compiler.config.compile_on_one_rank
torch.compiler.config.dynamic_shapes
torch.compiler.config.enable_cpp_symbolic_shape_guards
torch.compiler.config.fail_on_recompile_limit_hit
torch.compiler.config.force_disable_caches
torch.compiler.config.job_id
torch.compiler.config.log_file_name
torch.compiler.config.recompile_limit
torch.compiler.config.reorderable_logging_functions
torch.compiler.config.skip_tensor_guards_with_matching_dict_tags
torch.compiler.config.verbose
torch.compiler.config.wrap_top_frame
```

`torch.distributed._local_tensor`，12 项：

```text
torch.distributed._local_tensor.LocalIntNode
torch.distributed._local_tensor.LocalTensor
torch.distributed._local_tensor.LocalTensor.reconcile
torch.distributed._local_tensor.LocalTensor.tolist
torch.distributed._local_tensor.LocalTensorMode
torch.distributed._local_tensor.LocalTensorMode.disable
torch.distributed._local_tensor.LocalTensorMode.rank_map
torch.distributed._local_tensor.LocalTensorMode.tensor_map
torch.distributed._local_tensor.enabled_local_tensor_mode
torch.distributed._local_tensor.local_tensor_mode
torch.distributed._local_tensor.maybe_disable_local_tensor_mode
torch.distributed._local_tensor.maybe_run_for_local_tensor
```

`torch.multiprocessing`，6 项：

```text
torch.multiprocessing.SpawnContext
torch.multiprocessing.SpawnContext.join
torch.multiprocessing.get_all_sharing_strategies
torch.multiprocessing.get_sharing_strategy
torch.multiprocessing.set_sharing_strategy
torch.multiprocessing.spawn.spawn
```

`torch.multiprocessing.spawn.spawn` 是本次文档声明名称，保留证据，不擅自改名；其可用路径和别名关系另由 wheel 核验。

`torch.serialization`，13 项：

```text
torch.serialization.add_safe_globals
torch.serialization.clear_safe_globals
torch.serialization.get_crc32_options
torch.serialization.get_default_load_endianness
torch.serialization.get_default_mmap_options
torch.serialization.get_safe_globals
torch.serialization.get_unsafe_globals_in_checkpoint
torch.serialization.register_package
torch.serialization.safe_globals
torch.serialization.set_crc32_options
torch.serialization.set_default_load_endianness
torch.serialization.set_default_mmap_options
torch.serialization.skip_data
```
