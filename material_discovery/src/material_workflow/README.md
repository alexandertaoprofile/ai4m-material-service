# 材料发现业务模块（material_workflow）

本包实现 1115 的需求解析、晶体生成、结构评价和报告组织。服务用途、请求样例和环境配置见[服务 README](../../README.md)；本文用于按模块阅读代码。

本目录对应 75 当前源码快照。开发副本中新加的 `user_requirements.py` 与 `omatg_output_status.py` 不在本目录中，完整改进见[开发版业务模块](../../../development/material_discovery/src/material_workflow/README.md)。

## 1. 当前调用关系

```text
main.py → src/team_config.py → upstream_api.py → pipeline.py
                                                   ↓
                                 OMatG：omatg_pipeline.py
                                                   ↓
                            生成 → 检查 → 松弛 → 预测 → 比较
                                                   ↓
                           报告与资产 → 前端流式交付
```

`src/team_config.py` 管理服务阶段、角色生命周期和内容输出次序；`main.py` 管理 WebSocket 连接和协议。`pipeline.py` 根据配置选择后端；现行 OMatG 使用 `omatg_pipeline.py`，旧 MatterGen 实现显式保留为兼容路线。

## 2. 模块职责

下表文件均相对于本目录，`tools/` 和 `src/service_paths.py` 除外。

| 职责 | 文件 | 输入与输出 |
| --- | --- | --- |
| 需求与历史上下文 | `constraints.py`、`task_context.py`、`llm_constraint_inference.py` | 上游文本、显式条件与相关历史 → 需求和约束；后续解析改进见开发副本。 |
| OMatG 输入与补充确认 | `omatg_contract.py`、`requirement_review.py`、`capability_fallback.py` | 化学式、元素或开放探索选择 → 计算条件、补充问题或能力说明。 |
| 生成与组成约束 | `omatg_generation.py`、`omatg_sampling.py`；服务 `tools/run_omatg_generation.py` | CSP 固定组成或 DNG 元素范围 → 晶体候选及组成核验记录。 |
| OMatG 工作流与资源保护 | `omatg_pipeline.py`、`runtime_lock.py` | 已校验条件 → 独立任务目录、GPU 排队及分阶段计算结果。 |
| 几何、松弛与稳定性 | 服务 `tools/crystal_checks.py`、`tools/run_omatg_mattersim.py`、`tools/mp2020_reference.py` | 周期晶体 → 几何状态、优化后结构和稳定性估算。 |
| 性质预测 | `alignn.py`；服务 `tools/run_omatg_alignn.py` | 保留的优化后结构 → 已缓存性质模型的预测值、版本和逐项状态。 |
| 用户报告 | `omatg_report.py`、`omatg_chart.py`、`presentation.py` | 实际计算记录 → 中文说明、表格和比较图；逐项异常原因新版见开发副本。 |
| 请求结果与交付 | `schemas.py`、`upstream_api.py`、`emitters.py`、`payloads.py`、`llm_streaming.py` | 业务结果 → 结果清单、HTTP 响应和既有流式内容。 |
| 历史 MatterGen 路线 | `generation.py`、`validation.py`、`mattersim.py`、`ranking.py` | 旧后端生成、结构检查、评价与排序，按显式后端配置调用。 |

## 3. 阅读时需要区分的概念

- CSP：固定化学式，探索不同晶格和原子排布；DNG：同时探索组成与结构。
- 元素白名单和必含元素锚点是本地采样适配，不是新训练出的条件模型。生成后仍检查组成与几何。
- 松弛：利用 MatterSim 预测的能量与力调整晶胞和原子位置。生成结构和优化结构分别保留来源。
- 性质预测：ALIGNN 从原子及键角构成的图读取结构并预测性质；与早期已有材料检索中的属性补全逻辑不是同一模块。
- 稳定性默认参考线用于提示复核；用户明确给出的硬性条件单独处理。
- 当前使用预训练模型推理及后处理，OMatG-IRL 在线训练尚未并入主链。

## 4. 路径、资产与维护规则

`src/service_paths.py` 统一定义任务产物根目录。路径中保留 `MNS_CaseHub` 是历史兼容要求；调用方引用路径常量，不自行重建目录字符串。

PNG/GIF 通过既有图片发布模块交付；GLB 通过约定的 `MaterialsGLB` 事件交付。`llm_streaming.py` 用现有 token 流发送已组织好的 Markdown，保留公式、表格、图片链接和内容块标记。

重型模型依赖尽量在执行时加载，并在独立环境中调用。新增规则放在对应业务模块，保持编排、计算、展示和传输职责清楚。涉及接口、展示或计算规则的修改按[服务 README](../../README.md)运行对应回归。
