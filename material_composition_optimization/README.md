# material_composition_optimization（1111）

## 当前正式计算范围

本服务负责材料配比优化。正式配置以 `config/recipe_release.json` 为准；当前八个专项域已设置为 `primary`，用户条件经解析后调用对应数值模型与优化器。此表替代下文早期合金主线说明作为当前入口。

| 材料类别 | 路由域 | 主要输出 |
| --- | --- | --- |
| 高熵与多主元合金 | `hea_mpea` | 屈服强度、硬度及相组成相关预测，候选配比与比较。 |
| 热端镍基高温合金 | `ni_superalloy_hot_end` | 指定工况下的短时抗拉/屈服强度、蠕变断裂寿命及延性辅助预测。 |
| 铜基再生冷却热壁材料 | `copper_hot_end_local_composition_v1` | 局部组成范围内的强度、导电率辅助预测与配比优化。 |
| 可回收火箭奥氏体不锈钢 | `reusable_rocket_stainless` | 成分与状态条件下的强度、延性及配比比较；深冷请求进入参考与验证路径。 |
| 航空铝合金 | `aerospace_aluminum_composition_v1` | 对应工艺类别下的强度、延性及候选成分。 |
| 芯片封装玻璃 | `chip_glass_thermomechanical_family_v1` | 氧化物摩尔配比、热膨胀、模量、密度及专项可用性质。 |
| 短碳纤维热塑性复合材料 | `short_cf_thermomechanical_rve_v1` | 配比及结构参数、热机械性能和仿真代理结果。 |
| 钙钛矿 | `perovskite_transport_stability_v2` | 文档化组成与温度范围内的输运响应和稳定性比较。 |

未匹配专项模型的材料请求进入通用配比设计准备：展示支持类别、当前需求对应的组分角色、配比变量与试验安排。通用内容是设计框架，专项模型的数值预测只在实际执行对应模型后提供。

`recipe_primary.py` 按逐域授权配置选择正式结果；镍基与铜基额外校验专项适配器版本和验收记录。统一 Transformer 与材料专项模型各有属性职责，模型与数据状态见具体报告，不能只按架构名推断所有性质使用同一个模型。

## 关键实现与外部运行资源

| 位置 | 职责 |
| --- | --- |
| `src/alloy_workflow/application.py`、`contracts.py` | 请求入口、材料路由与计算条件。 |
| `requirement_semantics.py`、`numeric_conditions.py` | 当前需求与历史场景、数值条件解析。 |
| `recipe_primary.py`、`config/recipe_release.json` | 正式配比优化适配与逐域版本开关。 |
| `runner.py`、`recipe_shadow.py` | 隔离数值执行器、资源检查与可选影子对照。 |
| `request_capabilities.py`、`fallback_design.py`、`specialist_outcome.py` | 能力说明、通用设计准备与专项计算结果解释。 |
| `presentation.py`、`protocol.py` | 结果页、图像与既有流式交付协议。 |

本仓库归档服务代码，不打包训练数据、环境或模型权重。75 服务器实际依赖下列外部资源，接手者应同时核对，而不是仅拉取仓库后重新训练：

- `/data/se42/hea_surrogate`：专项数值 runner、训练/推理代码、模型与来源记录。
- `/data/se42/experiments/material_shadow_frontend_v2_20260928`：`shadow_recipe_runner.py` 与正式报告所用的 `recipe_*` 展示模块；目录名为历史名称，正式适配器仍调用其中模块。
- `/data/se42/experiments/material_rsi_prelaunch_20260928/package/runtime`：冻结的优化运行时，入口为 `rsi_api.py`。
- `/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python`：该优化运行时的独立解释器。
- `/data/mamba/envs/mattergen-py310`：材料专项数值环境，名称沿用旧版本。

后续部署保留原有 `.env`、对象存储设置与模型目录。新增下载、环境、缓存和中间结果均放在 `/data`；Git 推送不触发模型训练或服务重启。

## 回归验证

测试使用本地夹具与 mock。Web 解释器未安装 pytest 时，可复用已有数值环境的测试依赖；将其追加到搜索路径，保持 Web 环境自身依赖优先：

```bash
OPENAI_API_KEY=test MINIO_ENDPOINT=localhost MINIO_ACCESS_KEY_ID=test MINIO_ACCESS_KEY_SECRET=test \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python -c \
  'import sys; sys.path.append("/data/mamba/envs/mattergen-py310/lib/python3.10/site-packages"); import pytest; raise SystemExit(pytest.main(["-q", "tests"]))'
```

部署时先检查 `/health`、角色信息和数值资源，再逐域验收真实图文输出。下面保留早期合金接口与模型演进说明，供理解兼容调用；当前正式路由以本页首表和逐域配置为准。

## 历史合金主线与兼容接口

合金配比优化总服务。它使用共享的合金成分解析、描述符、任务 envelope 和流式进度协议，但按材料域路由至**独立训练和验证的模型**：

- `hea_mpea`：实验高熵合金/多主元合金（HEA/MPEA）；当前第一条训练线。
- `conventional_alloy`：成熟常规合金；待商品材料数据清洗后独立训练。
- `refractory_calculated`：NbCrVWZr 等计算/模型派生数据；严格独立于实验标签。
- `ni_superalloy_hot_end`：剑桥高温镍基合金数据；在明确路线、热处理、温度和载荷后，联合比较短时强度与蠕变断裂寿命。
- `reusable_rocket_stainless`：可回收火箭奥氏体不锈钢；在成分、固溶处理和 293–1273 K 条件下筛选短时强度—延性，低温自动转为 301/304L 参考与验证规划。

因此该服务不是“把所有合金强行训练成一个模型”。不同数据生成机制、成分空间和测试条件会保留独立的适用域与不确定性；可共享的仅是元素特征与 API。

## 运行架构

网页调用统一进入本服务；无需由前端判断或直接调用数值子进程。

- **主服务 / WebSocket / 流式结果层**：固定使用 Conda 环境 `ai4m-service-py310` 启动。它负责需求解释、任务协议、结果页、图表、以及按邻近服务约定调用 SeLLM 的 token/chunk 流式文本输出。
- **高熵/多主元合金数值代理层**：默认复用已部署的 micromamba 环境 `mattergen-py310`，只加载并运行高熵合金/多主元合金（HEA/MPEA）的 sklearn ensemble；主服务通过 JSON 请求/响应文件和 `micromamba run` 调用它。若未来需要独立版本锁定，可运行 `tools/setup_hea_surrogate_env.sh` 并用 `HEA_SURROGATE_ENV_PREFIX` 切换到专属环境。
- **上游编排层**：决定结果是否交给数学优化服务。本服务只在任务结果中生成可读结论和机器可读交接字段，不主动向下游发送请求。

首次部署在现有 `mattergen-py310` 环境中训练：

```bash
cd /data/se42/hea_surrogate
/home/ubuntu/micromamba run -p /data/mamba/envs/mattergen-py310 python -m src.models.train_baselines --task all
cd /data/se42/alpha_project/material_service_hub/material_composition_optimization
tmux new-session -d -s material-composition-optimization-1111 \
  '/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py'
```

服务由 tmux 启动；`/health` 的 `runner_ready` 会分别显示 HEA 与热端镍基 runner 是否就绪。旧 `start.sh` 与 Docker 文件不属于当前启动路径。

## 接口

- `POST /alloy/evaluate`
- `POST /alloy/evaluate-batch`
- `POST /alloy/propose-space`：快速生成可行初始候选池，供下游优化器使用。
- `POST /alloy/requirements/preview`：从自然语言生成可见、可覆盖的探索模板。
- `GET /alloy/tasks/{taskid}`
- `WS /alloy/start`

旧的 `hea_optimization` 约束块暂时兼容；新调用使用 `alloy_optimization`：

```json
{
  "taskid": "alloy-001",
  "idea": "900°C 高强度、低密度难熔高熵合金（HEA）",
  "file_metadata": [],
  "alloy_optimization": {
    "model_domain": "hea_mpea",
    "allowed_elements": ["Nb", "Cr", "V", "W", "Zr"],
    "element_bounds_at_pct": {"Nb": [20, 50], "Cr": [0, 30]},
    "test_temperature_C": 900,
    "objectives": {"yield_strength_MPa": "maximize", "density_g_cm3": "minimize"}
  }
}
```

在隔离模型环境未创建或未完成训练前，接口返回明确的未就绪错误，不会制造预测结果。

热端镍基合金使用 wt.% 而不是 at.%；若 WebSocket 只收到“镍基/蠕变/单晶叶片”等需求，会先返回结构化补充清单，而不会擅自假设热处理或工况。完整请求例如：

```json
{
  "taskid": "ni-hotend-001",
  "idea": "为单晶镍基发动机叶片筛选 950°C、250 MPa 下的候选成分",
  "alloy_optimization": {
    "model_domain": "ni_superalloy_hot_end",
    "manufacturing_route": "single_crystal",
    "test_temperature_C": 950,
    "applied_stress_MPa": 250,
    "heat_treatment": "solution_stage_1_temp_C=1302; solution_stage_1_time_h=4; precipitation_stage_1_temp_C=982; precipitation_stage_1_time_h=5; precipitation_stage_2_temp_C=871; precipitation_stage_2_time_h=20",
    "element_bounds_wt_percent": {"Ni": [55, 75], "Cr": [5, 12], "Al": [3, 8], "Ta": [0, 8], "W": [0, 12], "Ti": [0, 4], "Mo": [0, 5], "C": [0, 0.2], "B": [0, 0.1]}
  }
}
```

`/alloy/propose-space` 会生成用户结果页资产。HEA 为强度—硬度等图；热端镍基为候选筛选漏斗和短时强度—蠕变寿命对比图。通过任务 manifest 的 `presentation` 字段或 `GET /alloy/tasks/{taskid}/assets/{asset_name}` 获取。
