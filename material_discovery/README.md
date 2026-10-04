# material_discovery（1115）

周期性无机晶体发现服务。现行生成主链是 **OMatG CSP / DNG → 周期几何与组成检查 → MatterSim 结构松弛及稳定性估算 → ALIGNN 性质预测 → 相似结构合并与候选比较 → 图文及三维结构输出**。

本次正式代码整合已部署的 OMatG 适配与 2026-10-01 验证过的后续显示优化，包括需求承接、精简参数表、图文一致性、弹窗起点说明及逐项异常原因。后续显示优化此前仅在影子目录验证，是否部署以生产验收记录为准；本次 Git 归档不改变线上运行进程。

## 1. 输入与计算路线

| 用户提供的信息 | 执行路线 |
| --- | --- |
| 确定的整数化学式 | CSP：固定化学计量，生成不同晶格和原子排布。 |
| 允许使用的元素集合 | DNG：在元素白名单内生成组成与晶体结构，并检查必含、禁止元素及组成条件。 |
| 探索需求与明确的开放化学空间授权 | DNG：在模型支持的化学空间生成结构，再根据实际可计算的目标比较。 |
| 组成未确定，也未授权开放探索 | 展示可选输入与能力说明；用户补充后进入计算。 |

每个生成阶段默认 10 个候选，接口允许 1–10 个；每个晶胞最多 20 个原子。DNG 默认直接评估其生成结构；选定组成后的追加 CSP 由 `refine_with_csp` 显式开启。

稳定性默认采用 `0.05 eV/atom` 作为复核参考线：超过参考线仍继续结构与性质评估，报告标明复核需求。用户明确设置硬性稳定性上限时，按该上限判断。性质与稳定性目标通过生成后的评价实现；当前主链使用预训练 OMatG 推理，未接入 OMatG-IRL 在线训练。

可直接计算的性质取决于实际可用的 ALIGNN 权重，包括带隙、体积模量、剪切模量和有效质量。硬度由模量推算，单独标为工程估算。聚合物配方、独立有机分子生成、已有材料检索及合金配比优化分别由其他专项服务处理。

## 2. 接口与上游上下文

- `WS /start`、`WS /new-material/start`：兼容既有 `taskid / idea / user_name / file_metadata` envelope。
- `POST /new-material/constraints`：约束预览及补充条件解析。
- `POST /new-material/generate`：运行真实候选生成与评价。
- `GET /new-material/tasks/{taskid}`：查询已保存任务。
- `GET /roles`：供上游发现服务能力。

上游需传递当前用户要求及相关历史上下文；显式确认的化学式、元素与数值条件优先于历史线索。结构化输入放在 `new_material` 内。弹窗接入说明见 [OMATG_POPUP_INPUTS.md](docs/OMATG_POPUP_INPUTS.md)，补充确认约定见 [REQUIREMENT_REVIEW.md](docs/REQUIREMENT_REVIEW.md)。

CSP 示例：

```json
{
  "taskid": "crystal-csp-001",
  "user_name": "upstream-agent",
  "idea": "探索 BaTiO3 的周期晶体结构，比较结构稳定性",
  "file_metadata": [],
  "new_material": {
    "generation_mode": "csp",
    "target_formula": "BaTiO3",
    "formula_units": 1,
    "max_candidates": 10
  }
}
```

DNG 示例：

```json
{
  "taskid": "crystal-dng-001",
  "user_name": "upstream-agent",
  "idea": "在 Li-P-S-Cl 元素范围内探索无机晶体，关注结构稳定性",
  "file_metadata": [],
  "new_material": {
    "generation_mode": "dng",
    "allowed_elements": ["Li", "P", "S", "Cl"],
    "max_candidates": 10,
    "refine_with_csp": false
  }
}
```

结果保存在 `src/MNS_CaseHub/cases/material_discovery_demo/results/new_material/<taskid>/`。
每次任务使用独立标识；重复 `taskid` 返回冲突，原结果保留。候选 CIF、计算 manifest、模型日志、预测值和可视化资产均可追溯。

## 3. 环境与配置

Web 服务与科学计算解释器隔离，沿用服务器既有环境：

| 环节 | 解释器或目录 |
| --- | --- |
| Web 服务 | `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python` |
| OMatG 推理 | `/data/mamba/envs/omatg-py312/bin/python` |
| OMatG 官方源码 | `/data/third_party/OMatG` |
| OMatG 与性质模型权重 | `/data/se42/omatg_local/checkpoints` |
| MatterSim | `/data/mamba/envs/mattergen-py310/bin/python`，环境名称为历史兼容名称 |
| ALIGNN | `/home/ubuntu/miniconda3/envs/dsc2/bin/python`，复用 `/data/mamba/envs/alignn-gpu-test/lib/python3.10/site-packages` |
| MP2020 参考库 | `/data/mattersim_reference_cache/reference_MP2020correction` |
| 缓存、队列锁与临时文件 | `/data/se42/omatg_local/runtime` |

`.env.example` 提供服务及外部接口变量；[.env.omatg.example](.env.omatg.example) 提供 OMatG 专项配置。按需合并到本机 `.env`，保留当前密钥、对象存储、端口和弹窗配置。生产 `.env` 不提交。新增依赖、下载、临时文件与缓存应放在 `/data`，避免占用系统盘。

只读环境预检：

```bash
/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python tools/preflight_omatg.py --env-file .env.omatg.example
```

预检检查已有解释器、依赖、检查点与参考库，不安装环境、下载模型或启动结构生成。运行时按 `.env` 选择 `NEW_MATERIAL_GENERATOR=omatg`。生产服务由既有 tmux 会话管理；Git 推送不触发启动、重启或部署。

## 4. 实现位置

| 模块 | 作用 |
| --- | --- |
| `main.py`、`src/team_config.py` | HTTP / WebSocket 入口、角色与服务编排。 |
| `omatg_contract.py`、`task_context.py`、`requirement_review.py` | 组成与条件契约、历史上下文解析、补充确认。 |
| `omatg_generation.py`、`omatg_sampling.py`、`tools/run_omatg_generation.py` | 独立生成进程、元素域及组成约束。 |
| `omatg_pipeline.py`、`runtime_lock.py` | CSP / DNG 流程、GPU 队列与任务唯一性。 |
| `tools/crystal_checks.py`、`tools/run_omatg_mattersim.py`、`tools/mp2020_reference.py` | 周期几何、松弛与混合参考凸包评价。 |
| `alignn.py`、`tools/run_omatg_alignn.py` | 从保留结构计算已接入性质。 |
| `omatg_report.py`、`omatg_chart.py`、`omatg_output_status.py`、`presentation.py` | 可追溯图文报告、候选比较图及逐项执行状态解释。 |
| `tools/render_new_material_assets.py` | 晶体 PNG、旋转 GIF 和 GLB。 |

MatterGen 及其 HEA 微调适配器作为旧后端兼容代码保留，通过显式配置选择；旧 MatterGen 文档见 `docs/inorganic_new_material_service_guide.md`，不作为现行 OMatG 部署指南。

## 5. 回归验证

以下回归采用 mock 和本地夹具，不启动真实生成任务，也不请求 MP 或对象存储：

```bash
OPENAI_API_KEY=test NEW_MATERIAL_GENERATOR=omatg PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python -m unittest \
  tests.test_omatg_contract tests.test_omatg_task_context \
  tests.test_omatg_presentation tests.test_omatg_report_readability \
  tests.test_requirement_review tests.test_omatg_chart tests.test_omatg_sampling \
  tests.test_omatg_output_status tests.test_omatg_display_flow tests.test_capability_fallback -q
```

科学计算依赖与绘图回归：

```bash
OPENAI_API_KEY=test NEW_MATERIAL_GENERATOR=omatg PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /data/mamba/envs/mattergen-py310/bin/python -m unittest \
  tests.test_structure_visualization tests.test_omatg_geometry tests.test_omatg_chart -q
```

历史后端契约回归：

```bash
OPENAI_API_KEY=test NEW_MATERIAL_GENERATOR=mattergen PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python -m unittest \
  tests.test_service_contract tests.test_alignn_property_screening -q
```

模型结果用于候选比较；采用前结合对应的稳定性、性质及制备条件安排 DFT 或实验核验。

采样掩码与必含元素锚点测试使用 OMatG 专属环境：

```bash
OPENAI_API_KEY=test NEW_MATERIAL_GENERATOR=omatg PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /data/mamba/envs/omatg-py312/bin/python -m unittest discover -s tests -p test_omatg_sampling_worker.py -q
```

部署验收与回退说明见 [OMATG_DEPLOYMENT.md](docs/OMATG_DEPLOYMENT.md)；用户报告格式规范见 [omatg_customer_report_style.md](docs/omatg_customer_report_style.md)。
