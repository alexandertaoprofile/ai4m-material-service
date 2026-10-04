# 开发目录与线上源码的差异

核对日期：2026-10-04。比较对象是根目录的服务器源码记录与 `development/` 中的完整服务副本。下面只列代码及受版本管理的配置差异；README 等说明文件单独维护。当前开发副本来自整合提交 `213155c`，尚未整体部署。

## 1. 先看差异数量

| 服务 | 开发副本代码及配置文件 | 与根目录一致 | 内容不同 | 开发副本新增 | 仅根目录有 |
| --- | --- | --- | --- | --- | --- |
| 1111 配比优化 | 296 | 283 | 9 | 4 | 0 |
| 1115 晶体结构生成 | 327 | 297 | 15 | 15 | 0 |
| 合计 | 623 | 580 | 24 | 19 | 0 |

43 个不同或新增文件中也包括测试、诊断记录及仅修改空格的文件。重点按下面的分组阅读，功能是否上线看[部署记录](deployment_status.md)。

在仓库根目录运行以下命令可以重新比较。工具只读 Git 管理的文件，检查 `.py`、`.json`、`.yaml`、`.yml`、`.toml`、`.cfg`、`.ini`、`.sh`；比较时统一换行符，保留其他空格差异。

```bash
python3 tools/compare_service_versions.py
python3 tools/compare_service_versions.py --format json
```

## 2. 1111：候选展示与计算检查

| 开发文件 | 差异类型 | 阅读重点 |
| --- | --- | --- |
| [main.py](../development/material_composition_optimization/main.py) | 修改 | 健康检查增加对照计算的详细状态，保留已有字段。 |
| [presentation.py](../development/material_composition_optimization/src/alloy_workflow/presentation.py) | 修改 | 候选配比表和报告内容的展示调整。 |
| [runner.py](../development/material_composition_optimization/src/alloy_workflow/runner.py) | 修改 | 对照计算资源、运行状态及调用检查。 |
| [tensile_shadow.py](../development/material_composition_optimization/src/alloy_workflow/tensile_shadow.py) | 修改 | 拉伸性质对照计算的健康与诊断信息。 |
| [requirement_semantics.py](../development/material_composition_optimization/src/alloy_workflow/requirement_semantics.py) | 修改 | 仅去除一处行尾空格，解析规则保持原样。 |
| [test_rocket_candidate_card.py](../development/material_composition_optimization/tests/test_rocket_candidate_card.py) | 修改 | 检查火箭材料候选信息的报告显示。 |
| [test_service_contract.py](../development/material_composition_optimization/tests/test_service_contract.py) | 修改 | 检查接口和新增健康信息的兼容性。 |
| [test_unified_tensile_shadow.py](../development/material_composition_optimization/tests/test_unified_tensile_shadow.py) | 修改 | 检查拉伸性质对照计算的诊断行为。 |
| [verify_unified_tensile_shadow.py](../development/material_composition_optimization/tools/verify_unified_tensile_shadow.py) | 修改 | 核对对照计算程序的可用性与验证记录。 |
| [test_candidate_composition_table.py](../development/material_composition_optimization/tests/test_candidate_composition_table.py) | 新增 | 检查不同材料候选的组成表能正确显示含量。 |
| [stainless_canonical_composition_support_audit_20260922.json](../development/material_composition_optimization/reports/stainless_canonical_composition_support_audit_20260922.json) | 新增 | 不锈钢组成支持范围的历史检查记录。 |
| [unified_alloy_tensile_shadow_diagnostics_20260922.json](../development/material_composition_optimization/reports/unified_alloy_tensile_shadow_diagnostics_20260922.json) | 新增 | 拉伸性质对照计算的历史诊断记录。 |
| [unified_alloy_tensile_shadow_release_verification_20260923.json](../development/material_composition_optimization/reports/unified_alloy_tensile_shadow_release_verification_20260923.json) | 新增 | 该对照程序的历史发布检查记录。 |

候选展示与计算诊断是两组相关更新。部署清单要同时核对调用方、对应测试和外部计算程序，历史诊断 JSON 用于阅读依据，单独提交它们不会启用功能。2026-10-04 本副本的测试结果为 271 项通过，见[检查记录](MAIN_LAYOUT_VERIFICATION_20261004.md)。

## 3. 1115：需求理解、报告与逐项计算原因

### 3.1 输入解析和执行流程

| 开发文件 | 差异类型 | 阅读重点 |
| --- | --- | --- |
| [user_requirements.py](../development/material_discovery/src/material_workflow/user_requirements.py) | 新增 | 从当前需求和相关上文整理用户条件。 |
| [omatg_contract.py](../development/material_discovery/src/material_workflow/omatg_contract.py) | 修改 | 将条件转换为 CSP/DNG 使用的字段，处理明确的数值范围。 |
| [requirement_review.py](../development/material_discovery/src/material_workflow/requirement_review.py) | 修改 | 用户补充信息和确认内容。 |
| [upstream_api.py](../development/material_discovery/src/material_workflow/upstream_api.py) | 修改 | 上游任务与输入内容的接收、转换。 |
| [team_config.py](../development/material_discovery/src/team_config.py) | 修改 | 需求确认、计算和结果交付的执行顺序。 |
| [omatg_pipeline.py](../development/material_discovery/src/material_workflow/omatg_pipeline.py) | 修改 | 严格或包含端点的数值范围、实际执行进度和完成状态；说明没有结构时后续计算为何未启动。 |

### 3.2 模型结果和缺失原因

| 开发文件 | 差异类型 | 阅读重点 |
| --- | --- | --- |
| [omatg_output_status.py](../development/material_discovery/src/material_workflow/omatg_output_status.py) | 新增 | 区分计算失败、资源缺失、结构未收敛及后续计算跳过，提供对应原因。 |
| [mattersim.py](../development/material_discovery/src/material_workflow/mattersim.py) | 修改 | 保存结构优化和稳定性评价的状态、日志及原因。 |
| [alignn.py](../development/material_discovery/src/material_workflow/alignn.py) | 修改 | 保存性质预测的实际计算状态和失败原因。 |
| [run_omatg_alignn.py](../development/material_discovery/tools/run_omatg_alignn.py) | 修改 | 独立性质计算程序的诊断信息。 |

### 3.3 报告、图片及三维文件

| 开发文件 | 差异类型 | 阅读重点 |
| --- | --- | --- |
| [omatg_report.py](../development/material_discovery/src/material_workflow/omatg_report.py) | 修改 | 候选编号、需求和结果说明、逐项缺失原因。 |
| [presentation.py](../development/material_discovery/src/material_workflow/presentation.py) | 修改 | 中文报告章节、参数说明和前后文对应。 |
| [omatg_chart.py](../development/material_discovery/src/material_workflow/omatg_chart.py) | 修改 | 候选比较图的标签和解释。 |
| [emitters.py](../development/material_discovery/src/material_workflow/emitters.py) | 修改 | 按已有消息格式交付报告与文件。 |
| [render_new_material_assets.py](../development/material_discovery/tools/render_new_material_assets.py) | 修改 | 结构图、动画和三维文件的生成。 |

### 3.4 测试和预览工具

| 开发文件 | 差异类型 | 检查内容 |
| --- | --- | --- |
| [test_requirement_review.py](../development/material_discovery/tests/test_requirement_review.py) | 修改 | 补充确认与需求解析。 |
| [test_structure_visualization.py](../development/material_discovery/tests/test_structure_visualization.py) | 修改 | 结构图和文件生成。 |
| [omatg_context_requests.json](../development/material_discovery/tests/fixtures/omatg_context_requests.json) | 新增 | 用于测试上文场景和用户条件的输入样例。 |
| [test_omatg_contract.py](../development/material_discovery/tests/test_omatg_contract.py) | 新增 | CSP/DNG 字段及条件检查。 |
| [test_omatg_chart.py](../development/material_discovery/tests/test_omatg_chart.py) | 新增 | 比较图生成与显示。 |
| [test_omatg_display_flow.py](../development/material_discovery/tests/test_omatg_display_flow.py) | 新增 | 用户输入后的报告与进度流程。 |
| [test_omatg_geometry.py](../development/material_discovery/tests/test_omatg_geometry.py) | 新增 | 周期结构的几何检查。 |
| [test_omatg_output_status.py](../development/material_discovery/tests/test_omatg_output_status.py) | 新增 | 各种未完成计算的原因说明。 |
| [test_omatg_presentation.py](../development/material_discovery/tests/test_omatg_presentation.py) | 新增 | 计算方法和参数的报告展示。 |
| [test_omatg_report_readability.py](../development/material_discovery/tests/test_omatg_report_readability.py) | 新增 | 标题、候选标识和说明文字。 |
| [test_omatg_sampling.py](../development/material_discovery/tests/test_omatg_sampling.py) | 新增 | 元素与组成条件的采样适配。 |
| [test_omatg_sampling_worker.py](../development/material_discovery/tests/test_omatg_sampling_worker.py) | 新增 | OMatG 环境下的 CPU/CUDA 采样约束。 |
| [test_omatg_task_context.py](../development/material_discovery/tests/test_omatg_task_context.py) | 新增 | 当前要求、上文场景和数值条件的对应。 |
| [preview_omatg_display_bundle.py](../development/material_discovery/tools/preview_omatg_display_bundle.py) | 新增 | 使用已有任务结果生成独立报告预览。 |
| [verify_omatg_readable_report.py](../development/material_discovery/tools/verify_omatg_readable_report.py) | 新增 | 检查报告标题、内容、图像引用和显示完整性。 |

原因模块由输入、执行流程、模型评价及报告共同调用，部署时需要一起核对。测试命令见[开发服务 README](../development/material_discovery/README.md)，已有结果见[检查记录](MAIN_LAYOUT_VERIFICATION_20261004.md)。真实上线另检查 CSP、DNG、补充输入、计算异常、最终图片与三维文件。

## 4. 1111 物理约束实验

[实验目录](../development/1111_physics/README.md)单独保存四个训练或评估脚本、研究方案和数据检查记录。它研究玻璃模型的输出范围、温度响应与弹性关系，尚未接入线上请求。数据、基础训练模块、模型文件和历史结果保留在该 README 写明的服务器目录。

## 5. 怎样保持清单准确

代码改变后重新运行比较工具，更新数量、对应文件和修改目的。测试通过后记录待部署清单；实际部署并完成真实请求检查后，再同步根目录代码和部署记录。以后比较结果中的“仅根目录有”表示开发副本缺少该文件，先确认是否需要删除或补齐，勿自动删除服务器文件。
