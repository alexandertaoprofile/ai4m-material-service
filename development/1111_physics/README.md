# 1111 物理约束实验（离线）

本目录保存玻璃性质模型的独立改进实验，不启动 1111 服务，也不改变当前线上权重。物理约束在这里指让模型输出符合物理可行范围、同一成分的温度响应和弹性关系；不是新增一个通用材料服务。

## 1. 当前研究内容

| 内容 | 做什么 | 验证状态 |
| --- | --- | --- |
| 输出可行范围 | 密度和模量等保持正值，泊松比限制在当前玻璃适用范围。 | 方案和数据审计已归档。 |
| 热膨胀温度响应 | 利用同来源、同成分的不同温度观测，学习温度变化引起的响应。 | 已有多任务适配器离线验证；不预设所有玻璃都具有同一变化方向。 |
| 弹性关系一致性 | 根据杨氏模量和泊松比派生剪切模量与体积模量。 | 关系及数据条件见研究方案，不把方案内容全部声称为已上线实现。 |

热导率数据缺少足够的测试温度信息，因此温度响应约束仍需补充数据。当前实验不把输出一致性约束称为已完成严格物理控制方程训练。

## 2. 文件和外部依赖

| 文件 | 用途 |
| --- | --- |
| `glass/code/run_glass_constraint_ablation_v1.py` | 对照比较加入约束前后的效果。 |
| `glass/code/train_oxide_glass_multitask_cte_adapter_v1.py` | 多任务玻璃模型与热膨胀温度响应适配器训练、评估。 |
| `glass/code/run_glass_fresh_endpoint_challenge.py` | 用此前未使用的温度端点组进行独立挑战评估。 |
| `glass/code/analyze_glass_endpoint_predictions.py` | 分析预测记录和按组比较。 |
| [研究方案](glass/references/GLASS_PHYSICS_CONSTRAINT_PLAN.md) | 约束定义、数据条件和实验优先级。 |
| [数据审计](glass/references/GLASS_PHYSICS_CONSTRAINT_AUDIT.json) | 数据量、温度覆盖和约束适用条件。 |

原始目录为 `/data/se42/experiments/oxide_glass_physics_constraints_v1_20260924` 与 `/data/se42/experiments/oxide_glass_multitask_physics_v2_20260928`。脚本需要原始 SciGlass 数据、基础玻璃训练模块以及 NumPy、pandas、PyTorch 等依赖；迁移时需同时交接这些资源。

基础训练模块为 `/data/se42/experiments/oxide_glass_transformer_v0_20260922/code/train_oxide_glass_transformer.py`，数据为同目录下的 `dataset/sciglass_v2_strict_database_observations.csv.gz`。仓库仅保存实验源码和小型说明，不复制原始数据、模型检查点、环境或大型预测记录。

## 3. 验证与部署状态

2026-09-28 的外部记录 `formal_v1/FORMAL_VALIDATION_RESULTS.json` 标为 `offline_candidate_not_deployed`；`fresh_endpoint_v1/FRESH_ENDPOINT_RESULTS.json` 属于后续独立端点验证。训练完成表示产生了离线结果，不表示各项指标均已满足上线条件。

这次整理只复制现有源码并检查语法、来源一致性，不重新训练，也不把历史指标记作本次重新计算。接入 1111 前还需要审阅验证指标、支持范围和权重版本，并补推理适配及真实服务验收。当前线上依赖中已经存在的物理可行域处理，与本目录的新增实验分开判断。
