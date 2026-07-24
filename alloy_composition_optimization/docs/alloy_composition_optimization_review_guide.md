# 合金成分优化服务：代码审阅指南

本文说明当前改造后的审阅顺序。审阅时先确认实际调用链和前端契约，再看计算细节；不要从旧的无机生成代码或英文兼容版本倒推当前行为。

## 一条实际调用链

```text
前端或母服务
  ├─ 直连：main.py 的 HTTP / WebSocket 路由
  └─ 上游：src/team_config.py 的 AlloyCompositionOptimizationRole / Coding
                   ↓
            合金需求归一化与用例执行
            src/alloy_workflow/contracts.py
            src/alloy_workflow/runtime.py
            src/alloy_workflow/application.py
            src/alloy_workflow/runner.py
                   ↓
            展示、资产发布与协议适配
            presentation.py / protocol.py / assets.py / storage_utils.py
```

`main.py` 是传输边界：接收请求、记录连接、保持 `[start]`、进度、正文、`MaterialsPNG`、`result`、`[end]` 的事件顺序，并提供 REST 端点。它不应承载合金领域规则。

`src/team_config.py` 是母服务编排边界：将 Alpha 的消息、任务和文件元数据转换为服务请求，按阶段推进并复用同一套合金用例与前端协议。它不应成为领域计算或对象存储实现的容器。

`runtime.py` 是共享装配：直连入口和母服务编排入口都由它取得同一套运行时，并完成任务清单与图表资产的准备。`application.py` 是用例层：安排“需求归一化 → 合同校验 → 代理模型执行 → 结果补充”的顺序。`runner.py` 才负责隔离环境中的数值程序；Markdown、MinIO 和 WebSocket 分别属于展示/资产/协议模块。

## 推荐审阅顺序

1. 阅读 [README.md](../README.md) 与 [改造前基线](alloy_composition_optimization_pre_refactor_baseline.md)，确认服务边界：只做高熵合金/多主元合金（HEA/MPEA）及明确合金体系的成分空间、约束、代理评估和候选建议。
2. 阅读 [main.py](../main.py)，先检查路由、异常处理、日志和 WebSocket 事件顺序是否保持兼容。
3. 阅读 [src/team_config.py](../src/team_config.py)，按中文“阶段 0—4”注释确认母服务调用顺序与直连路径一致。
4. 阅读 [runtime.py](../src/alloy_workflow/runtime.py)、[contracts.py](../src/alloy_workflow/contracts.py) 和 [application.py](../src/alloy_workflow/application.py)，确认两个入口共享同一装配，请求识别、默认模板、约束、结论措辞和“预测不等于实验/DFT 结论”的边界。
5. 阅读 [runner.py](../src/alloy_workflow/runner.py)，确认代理模型命令、隔离环境、输入输出清单和失败处理。
6. 阅读 [presentation.py](../src/alloy_workflow/presentation.py)、[protocol.py](../src/alloy_workflow/protocol.py)、[assets.py](../src/alloy_workflow/assets.py) 与 [storage_utils.py](../src/storage_utils.py)，确认正文、PNG、MinIO 回退和 `MaterialsPNG` 兼容事件。
7. 最后运行并阅读 [tests/test_service_contract.py](../tests/test_service_contract.py)：它锁定 `/start`、`/alloy/start`、`/roles`、标记文本、资产事件与最终 `result` 的关键契约。

清理依据、保留项和三服务职责对齐见[清理审计](alloy_composition_optimization_cleanup_audit.md)。

## 三个服务的统一目标与当前状态

三项服务统一采用同一条职责路线：**传输入口 → 服务编排 → 用例 → 领域/数据执行器 → 展示与基础设施适配器**。这不是要求三个业务拥有完全相同的目录名。

| 服务 | 当前业务工作流 | 当前状态 | 后续统一方式 |
| --- | --- | --- | --- |
| alloy_composition_optimization | `alloy_workflow` | 已显式具备用例层 `application.py` | 继续将残留展示和落盘细节从 `main.py` 下沉到对应模块。 |
| mature_material | `catalog` | 查询、清洗、叙述、展示已分模块 | 在不改变商品材料查询协议的前提下，补齐显式用例层。 |
| inorganic_new_material | `material_workflow` | 生成、校验、排序、展示已分模块 | 在不改变晶体生成协议的前提下，补齐显式用例层。 |

因此，`application.py` 并不是所有服务历史上都有的文件；它是本次为合金服务补齐的“用例层”名称。后续另外两个服务应迁移到同一职责边界，而不是反向把合金服务塞回旧目录结构。

## 审阅中的删除原则

- 已删除 `src/team_config_inorganic_legacy.py`：当前入口、角色发现和合金工作流没有对它的引用；它是复制进来的无机晶体生成角色。
- 已删除复制进来的 `src/material_workflow/` 与调用它的 `tools/run_refractory_alloy_demo.py`：二者属于无机新材料生成链，不属于合金服务边界。
- `src/team_config_en.py`、`src/llm_utils.py`、`alpha/` 和 `config/config.yaml` 属于明确保留的兼容项；在证明无调用方前不删除。
