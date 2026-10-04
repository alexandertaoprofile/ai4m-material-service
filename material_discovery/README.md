# 晶体结构发现服务（1115）

本服务生成并比较周期晶体，回答“给定组成或探索范围，原子可以怎样排列，哪些结构值得继续研究”。周期晶体由重复的晶胞组成；晶胞是描述原子排列的基本空间单元。

当前计算流程为 OMatG 生成 → 组成和几何检查 → MatterSim 结构优化及稳定性估算 → ALIGNN 性质预测 → 相似结构合并、比较和图文/三维交付。

## 1. 当前能力与版本状态

本目录是 2026-10-04 核对的 75 当前源码快照，运行目录为 `/data/se42/alpha_project/material_service_hub/material_discovery`，生成器已接入 OMatG。后续显示与异常原因改进完整保存在[开发副本](../development/material_discovery/README.md)，其中包括 `omatg_output_status.py` 和 `user_requirements.py`；这两个模块尚不属于当前线上目录。

本页说明当前快照；开发版详细字段、逐项原因与测试清单位于开发副本。

当前使用已训练 OMatG 推理和生成后的评价。OMatG-IRL 训练、PropMolFlow 分子生成、DFT 电子结构计算、有限温自由能和声子计算尚未接入上述计算流程。已有材料查询、合金配比、聚合物或非晶材料配方分别走相应服务/专项模型。

## 2. 输入如何决定路线

| 用户提供的信息 | 执行路线 | 实际改变什么 |
| --- | --- | --- |
| 明确的整数化学式，如 `BaTiO3` | CSP：固定组成的晶体结构预测。 | 固定元素和原子数量，生成不同晶格和原子排布。 |
| 允许使用的元素集合，如 Li、P、S、Cl | DNG：从头探索组成与结构。 | 在元素白名单内探索比例和排布，再检查必含、禁止元素及组成条件。 |
| 材料方向或性质需求，并明确授权开放化学空间 | DNG：开放组成探索。 | 生成后按可计算的性质、稳定性和显式条件进行比较。 |
| 未确定组成，也未授权开放探索 | 补充确认和能力说明。 | 用户选择化学式、元素范围或开放探索后再启动生成。 |

CSP 每胞原子数等于化学式原子数乘以 `formula_units`；当前最多 20 个原子。DNG 默认每胞 8 个原子，支持 2–20 个。元素列表最多 8 种；“允许出现”与“每个都必须出现”分开设置，后者使用 `required_elements`。多元素 DNG 默认要求至少两种元素，组成多样性以真实结果统计。

元素范围由本地采样掩码和部分位点锚定适配；它限制可选元素并引导必含元素出现，生成后仍核验全部条件。每阶段默认 10 个候选，接口允许 1–10 个，最终可用的独立结构数量以计算结果为准。

DNG 默认直接评价自己的生成结构。只有 `refine_with_csp=true` 时，才从组成合规、结构收敛且有稳定性结果的 DNG 候选中选择一个组成，追加固定组成的 CSP；没有合适起点时说明原因并保留 DNG 记录。

上游当前要求和显式确认字段优先于历史线索；相关上文用于说明应用场景。固定化学式、元素及数值条件经确认后写入 `new_material`。弹窗字段见[输入说明](docs/OMATG_POPUP_INPUTS.md)，补充确认见[约定](docs/REQUIREMENT_REVIEW.md)。

## 3. 模型、评价与输出怎样理解

| 环节 | 模型或工具 | 输入与输出 | 用途 |
| --- | --- | --- | --- |
| 结构生成 | OMatG CSP/DNG | 固定组成或元素范围 → 晶胞、元素和原子坐标。 | 探索候选组成/排布。 |
| 组成与几何检查 | 本地检查模块 | 原始晶体 → 元素计数、周期距离和异常原因。 | 检查组成条件及过近原子等几何问题。 |
| 结构优化（松弛） | MatterSim | 晶体及预测能量/力 → 调整后的晶胞和原子位置。 | 寻找较低能量的局部结构，并记录收敛状态。 |
| 稳定性估算 | MatterSim + MP2020 参考库 | 优化结构与参考相数据 → 能量差估算。 | 比较结构相对参考相的热力学稳定性。 |
| 性质预测 | ALIGNN | 保留的收敛结构的原子图和键角图 → 已接入性质。 | 快速比较结构的电子和力学性质。 |
| 结果组织 | 结构匹配、报告和绘图模块 | 实际结果 → 相似结构组、比较表、图像和三维文件。 | 避免重复结构占据推荐位置，保持图文对应同一候选。 |

稳定性差值（`energy_above_hull`）描述候选相对参考相组合的能量差，单位 `eV/atom` 表示每个原子的电子伏特能量，通常越低越有利。当前采用模型能量与 MP2020 参考数据组成的混合参考估算；采用前需安排一致计算条件下的进一步核验。

默认 `0.05 eV/atom` 是复核参考线，超过仍继续满足执行条件的结构和性质评价。硬性稳定性上限须用 `stability_limit_mode=hard` 明确确认。明确的性质上下限放在 `property_constraints`；默认参考线、定性关注点与硬性条件分别记录。基础 OMatG 的性质导向比较发生在生成后，当前没有用这些目标在线训练生成模型。

当前预检可用的五个 ALIGNN 属性头为：带隙、体积模量、剪切模量、电子有效质量、空穴有效质量。带隙用于描述电子能带间隔；两种模量分别描述抵抗体积压缩和剪切变形的能力；有效质量描述载流子的运动响应。介电常数属性头当前未缓存，实际可用性以预检为准。硬度由模量推算，标为工程估算，与直接模型预测分开。

当前任务清单保存计算状态与日志。逐项区分未收敛、缺权重、计算失败和跳过原因，以及改进结果表中重复“待补充计算”的显示，属于开发副本的待部署更新；当前线上页面尚未采用该套新说明模块。

## 4. 接口与可复制的请求格式

| 接口 | 用途 |
| --- | --- |
| `WS /start`、`WS /new-material/start` | 既有流式入口，接收任务和上下文。 |
| `POST /new-material/constraints` | 约束预览及补充信息解析。 |
| `POST /new-material/generate` | 运行真实生成与评价。 |
| `GET /new-material/tasks/{taskid}` | 查询保存的任务结果。 |
| `GET /roles` | 上游能力发现。 |

通用字段为 `taskid / idea / user_name / file_metadata`，结构化条件放在 `new_material` 内。以下示例说明格式，不预设生成结果。

CSP：固定钛酸钡组成，比较原子排布。

```json
{
  "taskid": "crystal-csp-example-001",
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

DNG：允许四种元素，明确要求其中 Li、P、S 出现，默认不追加 CSP。

```json
{
  "taskid": "crystal-dng-example-001",
  "user_name": "upstream-agent",
  "idea": "在 Li-P-S-Cl 范围内探索含 Li、P、S 的周期晶体，关注稳定性",
  "file_metadata": [],
  "new_material": {
    "generation_mode": "dng",
    "allowed_elements": ["Li", "P", "S", "Cl"],
    "required_elements": ["Li", "P", "S"],
    "max_candidates": 10,
    "refine_with_csp": false
  }
}
```

开放探索时设置 `allow_open_chemical_space=true`，并保留具体材料方向或性质需求。完整字段和冲突输入说明见[弹窗输入文档](docs/OMATG_POPUP_INPUTS.md)。

每次任务使用独立 `taskid`，重复任务号返回冲突并保留原结果。默认结果位于 `src/MNS_CaseHub/cases/material_discovery_demo/results/new_material/<taskid>/`，由 `src/service_paths.py` 统一定义。结果包含晶体 CIF、输入与计算清单（manifest）、模型日志、预测值和 PNG/GIF/GLB 资产。

## 5. 环境、外部资源与实现位置

| 环节 | 当前 75 服务器解释器或目录 |
| --- | --- |
| Web 服务 | `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python` |
| OMatG 推理 / 官方源码 | `/data/mamba/envs/omatg-py312/bin/python` / `/data/third_party/OMatG` |
| OMatG、MatterSim 和性质模型权重 | `/data/se42/omatg_local/checkpoints` |
| MatterSim | `/data/mamba/envs/mattergen-py310/bin/python`，名称沿用历史版本。 |
| ALIGNN | `/home/ubuntu/miniconda3/envs/dsc2/bin/python`，复用 `/data/mamba/envs/alignn-gpu-test/lib/python3.10/site-packages`。 |
| MP2020 参考库 | `/data/mattersim_reference_cache/reference_MP2020correction` |
| 缓存、GPU 锁及临时文件 | `/data/se42/omatg_local/runtime` |

`.env.example` 提供服务与外部接口变量；[.env.omatg.example](.env.omatg.example) 提供模型专项配置。获批部署时按需合并到本机 `.env`，保留密钥、端口、对象存储及弹窗设置。运行时显式设置 `NEW_MATERIAL_GENERATOR=omatg`；缺少该配置时仍可能走历史后端默认值。

入口为 `main.py` 和 `src/team_config.py`，主要业务模块在 `src/material_workflow/`，独立模型执行器在 `tools/`。具体模块表见[业务模块 README](src/material_workflow/README.md)。旧 MatterGen 与已适配的 HEA 微调路线按显式配置保留；`metal_alloy_mattergen_v0` 是另外保存的历史训练骨架，状态见其 README。

只读预检，在本服务目录执行：

```bash
/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python tools/preflight_omatg.py --env-file .env.omatg.example
```

预检检查解释器、模型文件及参考库，输出可用性报告。新增依赖、缓存和输出文件放在 `/data`。联调启动使用已有 Web 环境，在配置完成、端口空闲的 tmux 会话内执行 `python main.py`；线上部署、配置合并与回退按[部署指南](docs/OMATG_DEPLOYMENT.md)审批和验收。

## 6. 测试与验收

以下命令在本服务目录执行，使用本地输入及模拟模型调用，检查需求解析、程序流程和报告：

```bash
OPENAI_API_KEY=test NEW_MATERIAL_GENERATOR=omatg PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python -m unittest \
  tests.test_requirement_review tests.test_capability_fallback -q
```

科学计算依赖与绘图测试：

```bash
OPENAI_API_KEY=test NEW_MATERIAL_GENERATOR=omatg PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /data/mamba/envs/mattergen-py310/bin/python -m unittest \
  tests.test_structure_visualization -q
```

历史后端接口兼容性测试：

```bash
OPENAI_API_KEY=test NEW_MATERIAL_GENERATOR=mattergen PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python -m unittest \
  tests.test_service_contract tests.test_alignn_property_screening \
  tests.test_constraints_formula_extraction -q
```

完整的新报告、采样约束和异常原因测试位于[开发副本](../development/material_discovery/README.md)，其测试入口和依赖在那里单独说明。本目录测试结果见[当前状态](../docs/deployment_status.md)，此前整合验证见[版本交接](../docs/RELEASE_HANDOFF_20261004.md)。真实上线还需验证实际请求、最终图文和三维结构一致性。用户报告规范见[格式说明](docs/omatg_customer_report_style.md)，其中后续显示要求按开发版验收。
