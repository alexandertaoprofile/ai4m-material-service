# README 统一修订与交接说明（2026-10-04）

## 1. 阅读对象与统一标准

本次按“理解代码架构、未必熟悉材料专业术语”的接手者整理全部 README：

- 先说明用途和当前状态，再说明实现，不把计划能力写成已实现能力。
- 入口、编排、业务计算、外部模型、展示和传输分别写清职责。
- 材料与方法缩写首次出现时补简短中文含义。
- 输入字段、单位、配置、接口和结果路径与实际代码对应。
- 已部署、已验证待部署、历史兼容和训练骨架分别标注。
- 普通服务更新复用已验收模型；离线训练和旧安装流程单独说明。

## 2. 覆盖范围

| README | 本次重点 |
| --- | --- |
| [仓库入口](../README.md) | 四服务分工、目录职责、版本/部署区别及阅读导航。 |
| [环境](../env/README.md) | 当前解释器与外部资源、历史快照含义和 OMatG 环境交接缺口。 |
| [1111](../material_composition_optimization/README.md) | 八类专项、模型调用与优化分工、外部依赖、含量单位、请求和回归。 |
| [1105](../material_database/README.md) | 目录来源、材料状态、温度证据、导电润滑专项、数据与接口。 |
| [1115](../material_discovery/README.md) | CSP/DNG、元素约束、生成/松弛/预测、稳定性模式、版本和验收。 |
| [1116](../material_validation/README.md) | 标杆证据读取、执行器未接入、方法含义与源文件核验。 |
| [1115 业务模块](../material_discovery/src/material_workflow/README.md) | 当前 OMatG 调用关系、模块职责和旧 MatterGen 兼容路线。 |
| [历史模型包](../material_discovery/models/metal_alloy_mattergen_v0/README.md) | scaffolded 状态与独立模型身份。 |
| [历史训练数据](../material_discovery/models/metal_alloy_mattergen_v0/data/README.md) | 已有数据清单、过滤规则、重建条件和来源路径。 |
| [历史训练流程](../material_discovery/models/metal_alloy_mattergen_v0/training/README.md) | 训练准备、产物及独立验收，不提供未确认的启动命令。 |
| [离线生成工具](../material_discovery/tools/generation_dataset_v1/README.md) | 批量入口、辅助评价/发布脚本、适用范围和输出结构。 |

## 3. 验证与操作范围

文档专用校验检查本地链接、标题空行、表格列数、代码块、JSON 示例、Shell 命令语法和 Python 模块引用；请求样例另外调用现有解析器校验。仅解析请求和检查文档，不运行真实模型、生成候选、下载依赖或发布资产。

在独立发布工作树中完成以下校验：

| 校验项目 | 结果 |
| --- | --- |
| 文档范围 | 11 份 README 和 2 份修订/交接说明。 |
| 仓库内链接 | 66 个目标检查通过。 |
| 标题空行、表格列数和代码块 | 检查通过。 |
| JSON 请求格式及现有解析器 | 5 个示例全部通过；覆盖 1111、1105、1115 CSP/DNG 和 1116。 |
| Shell 命令语法 | 17 个命令块通过；未执行安装、训练或启动命令。 |
| Python 文件引用 | 80 个文档内引用检查通过。 |
| Git 差异 | 空白检查通过，提交范围仅为 Markdown。 |

代码层面已执行的运行回归见[整合版本交接](RELEASE_HANDOFF_20261004.md)。本次文档提交不重复宣称科学计算验收或线上部署完成。

分支保持 `update/material-services-2026-08` 正式维护、`main` 保留旧版；提交和推送仅更新正式分支文档。生产目录、密钥、外部数据、模型权重和服务进程保持原状。
