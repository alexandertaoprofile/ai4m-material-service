# 训练顺序

1. 完成全量 JARVIS 结构质量审计，并生成 `data/dataset_manifest.json`。
2. 将最终 CSV 转为 MatterGen 数据缓存；训练与验证都使用同一结构标准化规则。
3. 从 `mattergen_base` 微调，训练配置、随机种子、软件版本和数据清单哈希写入
   `outputs/` 的运行记录。
4. 将最佳 checkpoint 放入 `checkpoints/`，计算 SHA-256 后回填
   `model_manifest.json`；不要覆盖通用 MatterGen checkpoint。
5. 用独立元素体系和统一 MatterSim/参考相图评估，通过验收后才将状态改为
   `accepted` 并接入服务路由。

本包不提供可直接执行的微调命令：命令必须在数据清单、GPU 可用性和最终的
MatterGen 数据模块配置都已确认后生成，避免把未经审计的结构集用于训练。
