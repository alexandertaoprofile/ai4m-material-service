# 1111 物理约束实验（离线）

本目录保存玻璃性质模型的离线改进实验。物理约束指让模型输出符合物理可行范围、同一成分的温度响应和弹性关系；成果通过验证后再安排接入 1111。

## 1. 当前研究内容

| 内容 | 做什么 | 验证状态 |
| --- | --- | --- |
| 输出可行范围 | 密度和模量等保持正值，泊松比限制在当前玻璃适用范围。 | 方案和数据审计已归档。 |
| 热膨胀温度响应 | 利用同来源、同成分的不同温度观测，学习温度变化引起的响应。 | 已有多任务适配器离线验证；温度响应按各成分的观测数据学习。 |
| 弹性关系一致性 | 根据杨氏模量和泊松比派生剪切模量与体积模量。 | 约束定义及数据条件见研究方案，接入进度按验证记录核对。 |

热导率温度响应约束还需补充带测试温度的数据。当前研究重点为输出范围、温度观测和弹性关系的一致性。

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

本目录已检查脚本语法和源码来源，历史指标按原实验记录查阅。接入 1111 前，审阅验证指标、支持范围和权重版本，再完成推理适配及真实服务验收。当前线上已有的物理范围检查见服务实现；本目录记录新增模型实验。
