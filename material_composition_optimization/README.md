# 材料配比优化服务（1111）

本服务在指定的材料类别、组分含量和工况范围内调整配比，使用已训练的性质模型比较性能，输出优选配比及其预测性质。它解决“各组分用多少”的问题；已有材料查询由 1105 负责，晶体中原子如何排列由 1115 负责。

## 1. 当前能力与状态

本目录保存 2026-10-04 核对的 75 线上源码快照，运行目录为 `/data/se42/alpha_project/material_service_hub/material_composition_optimization`。后续整合更新见[开发副本](../development/material_composition_optimization/README.md)，离线物理约束训练见[实验说明](../development/1111_physics/README.md)。这两类内容各自记录验证与部署状态。

正式配置见 `config/recipe_release.json`。当前八类专项均设置为 `primary`，即采用已验收的专项优化结果作为正式输出；模型、数据和评价条件随材料类别选择。

| 材料类别 | 代码路由标识 | 主要输出与含义 |
| --- | --- | --- |
| 高熵/多主元合金（HEA/MPEA） | `hea_mpea` | 多种主要元素的配比、屈服强度、硬度和相组成倾向；屈服强度表示开始产生永久变形时的应力。 |
| 热端镍基高温合金 | `ni_superalloy_hot_end` | 指定温度和载荷下的短时强度、蠕变断裂寿命及延性辅助预测；蠕变是长期受力下缓慢发生的变形。 |
| 铜基再生冷却热壁材料 | `copper_hot_end_local_composition_v1` | 已支持的局部组成范围内的配比、强度和导电率辅助预测，面向冷却通道附近的受热材料。 |
| 可回收火箭奥氏体不锈钢 | `reusable_rocket_stainless` | 成分及状态条件下的强度、延性和配比比较；低于支持温区的深冷请求转入参考与验证准备。 |
| 航空铝合金 | `aerospace_aluminum_composition_v1` | 对应工艺类别下的成分、强度与延性预测；铝锂等局部体系按各自数据范围处理。 |
| 芯片封装玻璃 | `chip_glass_thermomechanical_family_v1` | 氧化物配比、热膨胀、模量、密度和可用专项性质；模量描述抵抗弹性变形的能力。 |
| 短碳纤维热塑性复合材料 | `short_cf_thermomechanical_rve_v1` | 组分与结构参数、热机械响应及仿真代理结果。RVE 是用于代表材料局部微结构的小型计算单元。 |
| 钙钛矿 | `perovskite_transport_stability_v2` | 已记录的组成网格和温区内的输运响应、稳定性与配比比较；输运指电荷或离子的移动。 |

专项开关只决定使用哪个结果版本，具体请求仍需满足该模型支持的材料、组成、工艺和工况。未命中专项的请求提供通用设计准备：支持类别、组分角色、配比变量和试验安排。数值预测以实际执行的模型为准。

## 2. 一次请求如何完成

```text
当前需求 + 相关上文 + 显式条件
  → 识别材料类别，保留应用场景和用户约束
  → 检查专项能力、数据范围与运行资源
  → 调用已训练性质模型及对应配比优化器
  → 比较结果，组织优选配比、性质表和图像
  → 保存任务清单，按既有 HTTP/WebSocket 协议交付
```

用户明确的数值条件优先于历史线索。不同材料使用不同数据和模型；统一 Transformer 是一类性质模型架构，不表示所有材料或所有性质都由同一个模型计算。多数专项使用 NSGA-II 多目标优化，比较不同性能的取舍；钙钛矿按文档化网格计算，准确方法以任务记录为准。

训练是离线准备模型的工作；推理是用已有模型计算性质；配比优化反复调整输入并比较推理结果。正常部署和用户请求复用已验收模型，不重新训练。

## 3. 代码分层与外部资源

下表除注明外，模块均位于 `src/alloy_workflow/`。

| 位置 | 职责 |
| --- | --- |
| `main.py`、`src/team_config.py`、`runtime.py` | 请求入口、执行流程、任务保存和交付。 |
| `application.py`、`contracts.py` | 业务用例、材料路由、结构化输入校验。 |
| `requirement_semantics.py`、`numeric_conditions.py` | 解析当前要求、相关上文、工况和数值条件。 |
| `recipe_primary.py`、服务 `config/recipe_release.json` | 正式优化结果适配、逐材料版本开关和验收校验。 |
| `runner.py`、`recipe_shadow.py` | 独立计算程序与可选的对照测试。对照测试用于检查新模型表现，正式结果仍按已配置的路线输出。 |
| `request_capabilities.py`、`fallback_design.py`、`specialist_outcome.py` | 支持范围、通用设计准备与专项结果解释。 |
| `presentation.py`、`protocol.py` | 中文报告、图表和既有流式协议。 |

75 服务器当前还依赖以下仓库外资源：

| 目录 | 用途 |
| --- | --- |
| `/data/se42/hea_surrogate` | 专项数值执行器、训练/推理代码、模型和来源记录。 |
| `/data/se42/experiments/material_shadow_frontend_v2_20260928` | `shadow_recipe_runner.py` 和正式报告仍调用的 `recipe_*` 展示模块；目录名虽含 shadow/experiments，当前仍是运行依赖。 |
| `/data/se42/experiments/material_rsi_prelaunch_20260928/package/runtime` | 固定版本优化程序，入口 `rsi_api.py`。 |
| `/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python` | 固定版本优化程序使用的 Python 解释器。 |
| `/data/mamba/envs/mattergen-py310` | 专项数值环境，名称沿用历史版本。 |

迁移时同时交接这些资源的版本。当前 Git 仓库并未将所有外部算法包、权重和数据收拢为自包含安装包；不要因目录名看似实验用途而删除实际依赖。

## 4. 输入、接口与结果

所有请求使用任务号 `taskid`、需求文本 `idea` 等通用字段，专项条件放在 `alloy_optimization` 中。旧字段 `hea_optimization` 仍兼容。

含量单位按类别区分：`at.%` 是按原子数计算的百分比，`wt.%` 是按质量计算的百分比，`mol.%` 是按物质的量计算的百分比。接口字段须与模型所需单位一致。

| 接口 | 用途 |
| --- | --- |
| `GET /health`、`GET /roles` | 检查服务、数值资源和注册能力。 |
| `POST /alloy/requirements/preview` | 解析需求，预览可见、可覆盖的条件。 |
| `POST /alloy/propose-space` | 按材料路线执行候选生成及相应正式配比优化；保留历史接口名称。 |
| `POST /alloy/evaluate` | 评价给定配比请求。 |
| `POST /alloy/evaluate-batch` | 评价请求中的多个候选。 |
| `GET /alloy/tasks/{taskid}` | 读取保存的任务结果清单。 |
| `GET /alloy/tasks/{taskid}/assets/{asset_name}` | 获取任务图表资产。 |
| `WS /start`、`WS /alloy/start` | 流式需求解析、计算进度、报告及最终结果。 |

以下为室温 HEA 请求格式示例；正式计算前仍校验组成范围和模型条件：

```json
{
  "taskid": "hea-example-001",
  "idea": "在 Co-Cr-Fe-Mn-Ni 范围内优化室温强度与硬度",
  "file_metadata": [],
  "alloy_optimization": {
    "model_domain": "hea_mpea",
    "allowed_elements": ["Co", "Cr", "Fe", "Mn", "Ni"],
    "element_bounds_at_pct": {"Co": [10, 30]},
    "test_temperature_C": 25,
    "objectives": {"yield_strength_MPa": "maximize", "hardness_HV": "maximize"}
  }
}
```

镍基请求还需制造路线、温度、载荷和热处理等条件，完整约定见[镍基专项手册](docs/hot_end_ni_superalloy_service_v1.md)；铜基约定见[铜基局部组成手册](docs/copper_hot_end_local_composition_service_v1.md)。

任务默认保存于 `results/alloy_composition_optimization/<taskid>/`，可由 `ALLOY_RESULTS_ROOT` 配置。结果清单记录计算条件、方法和资产；正文给出优选配比、预测性质、比较图与验证建议。保存的 `summary.md` 可含内部图像占位符，用户页面以完成资产替换后的流式报告为准。

## 5. 环境、配置与联调

Web 解释器为 `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python`。服务配置参考 `.env.example`，重点包括端口、数值资源路径、结果目录、语言模型和对象存储设置。正式优化配置可由 `MATERIAL_RECIPE_RELEASE_CONFIG` 指定。

新联调副本仅在 `.env` 尚不存在时复制样例，再填入本机配置；已有生产配置保留。启动前先核对端口和数值资源，不执行历史训练命令。

```bash
cd /path/to/material_composition_optimization
# 配置完成、端口空闲后，在联调 tmux 会话内运行：
/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py
```

调试使用 tmux，稳定运行可由运维登记系统服务。旧 `start.sh` 与 Docker 文件不是当前标准启动路径。

## 6. 测试与交接

在本服务目录执行。测试使用模拟输入和模型调用；Web 环境未安装 pytest 时，下面命令复用已有数值环境的测试依赖，并保持 Web 依赖优先：

```bash
OPENAI_API_KEY=test MINIO_ENDPOINT=localhost MINIO_ACCESS_KEY_ID=test MINIO_ACCESS_KEY_SECRET=test \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python -c \
  'import sys; sys.path.append("/data/mamba/envs/mattergen-py310/lib/python3.10/site-packages"); import pytest; raise SystemExit(pytest.main(["-q", "tests"]))'
```

本目录与开发副本分别运行测试，结果见[当前目录交接](../docs/deployment_status.md)。此前整合代码的 271 项通过属于开发副本的历史验证，见[版本交接](../docs/RELEASE_HANDOFF_20261004.md)。查看测试结果时按对应代码目录和验证日期核对。

部署验收需另检查健康状态、角色、各专项真实任务及完整图文显示。代码审阅和兼容项见[阅读指南](docs/alloy_composition_optimization_review_guide.md)、[清理审计](docs/alloy_composition_optimization_cleanup_audit.md)。较早的覆盖报告、对照测试方案和训练说明属于历史记录，当前路线以本页、正式配置和实际任务结果为准。
