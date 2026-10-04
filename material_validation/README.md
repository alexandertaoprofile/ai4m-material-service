# 难熔金属计算证据核验服务（1116）

本服务把已有模型训练、原子模拟和实验/文献记录组织成可追溯验证报告，回答“这个计算结果依据什么、哪些证据已经齐备”。难熔金属指熔点较高的一类金属；当前首版仅支持纯钨（W）的 W-14 标杆案例。

## 1. 当前实现与证据状态

当前执行模式为 `reference_case`：读取版本化标杆文件，以及配置的 DeepMD 训练/模型目录和 LAMMPS 模拟输出目录，汇总已有结果并生成报告。真实 DeepMD/LAMMPS 执行器尚未配置，`execution_mode=execute` 会明确返回未就绪原因；仅打开 `ENABLE_EXECUTION` 配置也不会建立执行器。

随仓库提交的证据清单状态为 `awaiting_evidence_import`，即完整证据仍需导入。仓库包含用户提供的 300 K 内部对标记录，外部训练和模拟数据按部署配置读取；完整 DFT 数据划分、模型与 MD 势文件绑定，以及正式实验/文献来源定位需要分别核对。具体可展示的数据以实际挂载文件和证据检查为准。

## 2. 四个阶段分别说明什么

| 阶段 | 方法含义 | 核验内容 |
| --- | --- | --- |
| 材料任务定义 | 确定材料、温度和目标性质。 | 是否属于当前纯钨标杆范围，输入是否有效。 |
| DFT 基准 | 密度泛函理论：从电子结构计算能量、原子受力等。 | 原始结构、能量/力/应力和训练/验证数据划分是否齐全。 |
| MLIP 与 MD | MLIP 是预测原子能量和力的机器学习势；MD 是逐步模拟原子运动的分子动力学。 | 模型文件、训练误差、模拟脚本和输出；模拟实际使用的势文件是否与记录模型一致。 |
| 实验/文献对标 | 在相同材料状态和工况下比较模拟与测量。 | 来源、测试方法、单位和温度是否可追溯，能否计算有效误差。 |

DeepMD 用于训练/运行机器学习势，LAMMPS 用于原子模拟。模型相对 DFT 的误差与模拟相对实验的误差属于不同验证环节，报告分别保留。

## 3. 输入、接口与输出

通用任务字段外，条件放在 `refractory_validation` 中。例如读取 300/600/900 K 下的标杆证据：

```json
{
  "taskid": "w14-reference-example-001",
  "idea": "检查纯钨 W-14 的弹性与声学性质证据",
  "file_metadata": [],
  "refractory_validation": {
    "material_system": "W",
    "temperature_K": [300, 600, 900],
    "target_properties": ["elastic_constants", "sound_speed"],
    "execution_mode": "reference_case"
  }
}
```

温度单位 K 为开尔文；目标性质列表表示希望核对的项目，实际数值是否可用取决于相应归档数据。

| 接口 | 用途 |
| --- | --- |
| `GET /health`、`GET /roles` | 服务状态和能力注册，默认端口 1116。 |
| `POST /refractory-validation/requirements/preview` | 需求和标杆范围校验。 |
| `POST /refractory-validation/evaluate` | 读取证据并生成报告。 |
| `WS /start`、`WS /refractory-validation/start` | 分阶段流式报告与最终结果。 |
| `GET /refractory-validation/tasks/{taskid}` | 获取任务清单。 |
| `GET /refractory-validation/tasks/{taskid}/assets/{asset_name}` | 获取任务报告和图表。 |

任务默认保存在 `results/<taskid>/`，包括结果清单、诊断记录和展示资产。页面说明已有证据、缺失原因及补充事项。图表依据实际源文件或证据状态生成；DFT 覆盖、预测-参考一致性图等需要对应原始数据后才能展示。

## 4. 代码分层与配置

| 位置 | 职责 |
| --- | --- |
| `main.py`、`src/team_config.py` | 传输入口、服务编排、任务保存和流式交付。 |
| `src/application/request_normalization.py`、`validation_workflow.py` | 请求校验与四阶段证据流程。 |
| `src/contracts/schemas.py` | 请求、阶段证据状态和结果结构。 |
| `src/infrastructure/reference_case_reader.py`、`w14_source_aggregation.py` | 读取标杆及外部训练/MD 文件，计算可追溯文件校验和。 |
| `src/presentation/` | 中文报告、诊断、图表、对象存储和流式适配。 |
| `data/reference_cases/w14_phase_i/` | 标杆清单和内部对标记录，不包含完整训练/模拟数据。 |

配置参考 `.env.example`：`REFERENCE_CASE_ROOT` 指定标杆目录，`W14_MLIP_ROOT` 和 `W14_MD_ROOT` 指定外部只读数据。导入文件按 `raw/`、`models/`、`literature/` 分类，并记录 SHA-256 文件校验和以识别版本。密钥、图片发布和语言模型设置放本服务 `.env`。

配置完成、端口空闲后，在已准备的 Web 环境和联调 tmux 会话中运行：

```bash
cd /path/to/material_validation
python main.py
```

75 服务器可复用 `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python`；绘图等依赖需按实际环境核对。

## 5. 验证与交接

```bash
python -m unittest discover -s tests -v
```

测试检查请求边界、证据读取、报告及既有流式协议；证据报告测试通过不等于真实执行器或完整科学验证已通过。本次文档整理不导入数据、不启用计算。

进一步阅读：[服务边界](docs/service_boundary.md)、[证据导入指南](docs/evidence_import_guide.md)、[客户输出预览](docs/customer_output_preview.md)。计划性输出以这些文件的版本日期和实际数据状态为准。
