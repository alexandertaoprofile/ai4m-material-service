# Organic Existing Material Service

## 1. 服务定位

`organic_existing_material` 是有机已有材料筛选与性质补全服务。

该服务聚焦以下职责：
- 基于上游给出的 `Name` 或 `PSMILES` 做 OpenPoly 候选检索。
- 生成首条候选结构的 3D 可视化资产（GLB）并回传前端。
- 对首条候选进行关键性质补全（数据库优先，缺失字段走预测模型）。
- 输出结构化文本与资产索引，供后续材料制备/性能评估流程使用。

不负责：
- 文献再次筛选。
- 实验制备执行。
- 无机 MP/ADiT/MACE 全流程计算。

## 2. 当前主流程（以 `src/team_config.py` 为准）

统一入口在 `Coding.run` 的有机主路径：
- 输入归一化后，先走 `_search_openpoly_candidates`。
- 若命中候选，执行前置分析 `_stream_organic_pre_analysis`。
- 输出 OpenPoly 候选表（含 `Name/PSMILES/Tg/Td/Tm/Water_Uptake/Dielectric_Constant/Thermal_Conductivity`）。
- 对首条候选执行 `_generate_and_send_openpoly_first_glb`：
  - 调用 `tools/psmiles_to_glb.py` 生成 GLB 与 meta。
  - 写入 manifest 并通过 `send_results_to_frontend` 回传右侧资产。
- 对首条候选执行 `_stream_first_hit_xgb_completion`：
  - 字段有数据库原值则直接使用。
  - 缺失字段调用 OpenPoly 预测脚本补全。
- 最后发送路由锚点，明确本服务本轮结束，避免上游重复回调。

说明：`/mp` 旧路由已关闭，输入 `/mp ...` 会提示改用有机 `Name/PSMILES`。

## 3. 输入与输出

输入建议：
- 候选材料名称（Name），例如树脂/聚合物名。
- 或可解析的 `PSMILES`。

主要输出：
- 左侧文本：需求提取、关键性质分析、候选检索表、首条性质补全表。
- 右侧资产：首条候选结构 GLB、阶段图片、对应 manifest 索引。

典型结果目录：
- `src/MNS_CaseHub/cases/material_discovery_demo/results/psmiles/<taskid>/openpoly/`

## 4. 关键环境变量

存储与访问：
- `MINIO_ENDPOINT`
- `MINIO_ACCESS_KEY_ID`
- `MINIO_ACCESS_KEY_SECRET`
- `MINIO_SECURE`
- `GLB_PUBLIC_BASE_URL`
- `PICTURE_PUBLIC_BASE_URL`

预测/可视化环境：
- `OPENPOLY_XGB_ENV`（默认 `organic-predict-py310`）
- `OPENPOLY_XGB_MAMBA_ROOT`（默认 `/data/mamba`）

通用：
- `server_base`
- `base_url`
- `api_key`

## 5. 运行依赖

该服务依赖 `micromamba` 环境执行部分子流程：
- `tools/psmiles_to_glb.py`（结构可视化转换）
- OpenPoly 预测脚本（性质补全）

请确保对应环境存在，且模型/脚本路径有效。

## 6. 日志与稳定性说明

流式输出由 `_stream_llm_response` 管理，包含：
- 请求超时重试。
- chunk 级超时保护。
- 总字符上限保护。
- 高碎片流提示（用于定位超长结构串导致的异常分段）。

## 7. 与旧 README 的区别

旧版 README 描述的是无机 `MP -> ADiT -> MACE` 链路。
当前目录服务已切换为有机 OpenPoly 主线，本文档已按当前 `src/team_config.py` 行为重写。
