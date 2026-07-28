# 无机新材料服务：改造前基线

> 本文只记录现状、边界和迁移顺序，不改变现有前端协议、计算链路或通用 `alpha/` 框架。

## 1. 服务边界

本服务的主职责是发现数据库外的无机晶体：把元素体系/化学式和可计算约束转换为 MatterGen 候选，随后执行 pymatgen 结构准入、MatterSim 松弛、Materials Project 同元素体系竞争相比较、排序和可视化。

它不应承担以下职责：

- 已有商品材料、牌号、标准或性质库查询；该请求属于 `mature_material`；
- 合金/高熵合金的元素比例、配方与成分空间优化；该请求属于 `alloy_composition_optimization`；
- 将生成引导目标、MLFF 结果误报为 DFT 或实验验证结果。

## 2. 当前实际主链

```text
main.py
  └─ Team / InorganicNewMaterialDiscoveryRole
       └─ InorganicNewMaterialDiscoveryAction
            ├─ constraints + LLM 受限补全
            ├─ material_workflow.upstream_api.run_upstream_request
            │    └─ pipeline: MatterGen → pymatgen → MatterSim/MP → ranking
            ├─ presentation: manifest、PNG/GIF/GLB
            └─ WebSocket Markdown、图片资产、最终结论
```

`POST /new-material/generate` 直接调用同一 `run_upstream_request`；`POST /new-material/constraints` 仅预览约束，不启动 GPU。

## 3. 当前外部协议：冻结项

在完成前端联调前，下列内容不得改名、删除或调整事件顺序：

- WebSocket：`/start` 与 `/new-material/start`；
- 文本标记：`[start]`、`[end]`、`<<<CONTENT_START/END:...>>>`；
- 步骤 ID：`FILAMENT_SELECTION_OPTIMIZATION`；
- 图片/模型类型：`MaterialsPNG`、`MaterialsGLB`；
- `/roles` 返回字段结构；
- 现有任务 envelope：`taskid`、`idea`、`user_name`、`file_metadata`。

正常新材料流程当前只有一条初始化 `progress` JSON；长时间 MatterGen 阶段通过普通 Markdown 文本心跳提示进度。`result` 由不同入口分别以 HTTP 响应或 WebSocket 总结呈现，后续需要先采样线上消息再统一。

## 4. 遗留结构与风险分类

### 4.1 通用框架：保留

- `alpha/`、`src/team_config_en.py`、`src/llm_utils.py`、`config/config.yaml`：按当前约定保留，不能与业务清理混为一次删除。

### 4.2 当前主线使用的 MNS 路径：暂不改

- `src/MNS_CaseHub/cases/material_discovery_demo/results/new_material`：当前任务、进度和资产落盘目录；
- MinIO 历史对象 key 前缀：已有对象访问路径的一部分；
- `/roles` 仍通过旧 `Team/Role` 序列化生成。

其中任务产物目录和对象存储历史路径仍需保持兼容；角色兼容别名已在本轮实际前端验证后删除。

`MNS_CaseHub/cases/material_discovery_demo` 原有的 `handler.py`、`pipeline.py`、
`material_discovery_demo_main.py` 和 `ls_service.py` 是旧 MP 查询与文件监听入口，
不被现役 HTTP、WebSocket 或 MatterGen 主链导入，已删除；保留的只是任务产物目录与兼容性数据。

与其配套的旧 MP/ADiT 工具（`mp_export_assets.py`、`mp_query.py`、
`mp_fetch_structure.py`、`mp_batch_build_demo.py`、`adit_pymatgen_eval.py`、
旧 case runner）同样未被主链引用，已删除。现役工具仅保留 MatterGen 生成后的
pymatgen 验证、MatterSim 评估与展示资产导出。

### 4.3 已完成隔离的旧业务

`Coding` Action 是旧 MP/ALIGNN 已有材料检索流，未被
`InorganicNewMaterialDiscoveryRole` 注册。完成 import、路由和前端输出审计后，
已从 `src/team_config.py` 移除；其专用的 MP 结果整理、ALIGNN 补全、旧图片下发、
静态数据库图片和公式路由模块也已删除。现役入口不再在启动时加载这些旧业务代码。

## 5. 改造顺序

1. 补新材料 WebSocket/`/roles`/HTTP manifest 的契约测试，并采集一次真实前端任务事件；
2. 将 `main.py` 收敛为传输层，把角色创建、任务目录、约束预览和运行编排逐步移至 `src/team_config.py` 与 `src/material_workflow/`；
3. 已隔离并清理旧 `Coding`（MP/ALIGNN）流；
4. 迁移 MNS 结果目录、MinIO key、角色兼容名，并逐项由前端验证；
5. 参照成熟材料服务统一 `.env.example`、Docker/启动脚本、`.dockerignore`、数据与测试约定；
6. 最后更新新人接入手册和三个服务的统一架构文档。

## 6. 本轮明确不做

- 不运行 MatterGen、MatterSim、MP 或 GPU 作业；
- 不删 `alpha/` 与英文/LLM 通用框架；
- 不删 `MNS_CaseHub` 或修改 MinIO 对象 key；
- 不改前端事件、角色发现格式或端口；
- 不修改仍在运行的 MatterGen → pymatgen → MatterSim/MP 主链。
