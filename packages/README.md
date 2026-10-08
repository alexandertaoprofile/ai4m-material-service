# 配比优化使用的计算程序

这里保存 2026-10-08 从 75 服务器导出的运行源码，便于在同一仓库评审、迁移和调试。权重及运行时读取的数据表由[运行资源包](../resources/README.md)提供。文件来源和校验值在[资源清单](../resources/runtime-manifest.json)中逐项记录。

| 目录 | 做什么 | 原服务器位置 |
| --- | --- | --- |
| `hea_surrogate/` | 八类专项的性质计算与初始候选生成；包含钙钛矿输运模型的程序、参数和小型查找表。 | `/data/se42/hea_surrogate` |
| `recipe/runtime/` | 固定版本的模型加载、组成变量处理和多目标配比优化。 | `/data/se42/experiments/material_rsi_prelaunch_20260928/package/runtime` |
| `recipe/specialists/` | 与固定优化包配套的专项源码和支持信息。 | 上述 `package/specialists` |
| `recipe_report/` | 将数值结果整理为配比表、性质比较、中文报告和图像。 | `/data/se42/experiments/material_shadow_frontend_v2_20260928` |

`hea_surrogate` 与 `recipe/specialists` 是不同时间固定的版本，分别服务于原专项执行器与固定优化包。保留两套源码可以按原来的调用方式运行；合并时先验证模型与源码的组合，不能只按同名文件互相覆盖。

模型加载可能引用名称中含 `train` 的文件，因为这些文件同时定义了神经网络类。首次启动需要这些类定义；请求执行时加载已有权重进行推理。

恢复资源后，`prepare_runtime.py` 把源码与外部权重组合到本机资源目录，保留 `runtime/` 与 `specialists/` 的相邻关系。移植启动使用 `launch_service.py`，通过既有构造函数配置优化包、报告模块和解释器的位置。详细命令见[迁移指南](../docs/reproduction.md)。

源码中的历史绝对路径及验收文件名作为来源记录保留。运行时使用迁移配置中的路径。各服务根目录继续保存原部署源码，`development/` 继续保存后续开发版本。
