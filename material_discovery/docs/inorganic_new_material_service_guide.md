# 无机新材料服务交接指南

本文描述当前线上主线，供接手开发、联调和排障使用。历史盘点和重构前情况见
[`inorganic_new_material_pre_refactor_baseline.md`](inorganic_new_material_pre_refactor_baseline.md)。

## 1. 服务边界

本服务只做**数据库外的无机晶体候选发现与初步验证**：根据上游的材料方向、元素体系和稳定性偏好，生成候选晶体，并提供结构准入、MatterSim--MP 初筛、排序和可追溯资产。

不负责以下工作：

- 已有商品、牌号、成熟材料性质的查询与比较（交给 `mature_material`）；
- 文献检索或从外部资料中确认已有材料事实；
- 高熵/难熔合金的元素比例、原子百分比或成分空间优化（交给 `alloy_composition_optimization`）；
- 把 MatterSim--MP 初筛当作 DFT 或实验结论。

当元素体系无法可靠确定时，服务不会无约束生成：它会以流式文字说明缺少的信息，并等待补充。对已识别的无机材料方向，可使用明确标注的领域起始模板，并优先由受限 LLM 结合完整上游结论细化；模板不是最终化学式承诺。

## 2. 输入、输出与运行链路

推荐上游输入为既有 envelope，约束放在 `new_material`（也兼容 `constraints`、`mattergen`、`generation_constraints`）：

```json
{
  "taskid": "solid-electrolyte-001",
  "user_name": "upstream-agent",
  "idea": "探索 Li-P-S-Cl 固态电解质",
  "file_metadata": [],
  "new_material": {
    "allowed_elements": ["Li", "P", "S", "Cl"],
    "target_properties": {"energy_above_hull": 0.05},
    "validation_targets": {"ionic_conductivity": 0.001},
    "max_candidates": 4
  }
}
```

`target_formula` 目前用于提取元素集合，并不保证精确化学计量；`validation_targets` 记录后续验证重点，不能作为本轮已算出的性能。

实际执行顺序为：

```text
上游 envelope
  → 约束归纳与元素体系确认
  → MatterGen 生成候选 CIF
  → pymatgen 结构准入
  → MatterSim 弛豫 + MP 竞争相初筛
  → 确定性排序、Markdown 报告和 PNG/GIF/GLB 资产
  → manifest 与 WebSocket 下发
```

输出应表述为“候选及初筛证据”。MatterSim--MP 形成能/E_hull 只用于排序和决定是否进入 DFT/专项性能验证。

## 3. 接口与前端协议（冻结）

| 用途 | 接口 |
| --- | --- |
| 前端/母服务流式入口 | `WS /start`、`WS /new-material/start` |
| 同步执行 | `POST /new-material/generate` |
| 约束预览 | `POST /new-material/constraints` |
| 任务结果 | `GET /new-material/tasks/{taskid}` |
| 角色描述 | `GET /roles` |
| 文件上传兼容接口 | `POST /uploadFile`、`POST /files` |

在未与前端共同升级前，下列协议不得单独改名或删除：`[start]` / `[end]`、
`<<<CONTENT_START:...>>>` / `<<<CONTENT_END:...>>>`、步骤标识
`FILAMENT_SELECTION_OPTIMIZATION`、资产类型 `MaterialsPNG` / `MaterialsGLB`，以及 `/roles` 的字段结构。

服务在一次 WebSocket 任务中只发送一次开始进度 JSON；正文通过既有流式文本通道输出。图片和 GLB 走既有资产 JSON 协议，上传失败时正文和 manifest 仍需完成，不能把资产失败伪装成计算失败。

## 4. 代码与产物位置

| 位置 | 职责 |
| --- | --- |
| `main.py` | FastAPI/WS 传输层、HTTP 兼容接口 |
| `src/team_config.py` | 唯一主编排层：事件次序、角色/Action 生命周期、任务执行 |
| `src/material_workflow/` | 约束、生成、结构准入、热力学、排序、展示和 manifest 模块 |
| `src/service_paths.py` | 结果根目录的唯一所有者 |
| `tools/` | MatterGen/MatterSim 调用与 PNG/GIF/GLB 渲染工具 |
| `tests/test_service_contract.py` | 不启 GPU 的协议、边界和路由回归测试 |

当前任务产物根目录由 `src/service_paths.py` 的 `NEW_MATERIAL_RESULTS_ROOT` 定义。路径中保留
`MNS_CaseHub` 仅因为已有部署和历史产物仍使用该位置；它不是当前服务名，也不是可执行旧管线。禁止在其他模块重新拼接这条路径。

`alpha/`、`src/team_config_en.py`、`src/llm_utils.py`、`config/config.yaml` 目前按通用框架兼容要求保留；不要因本服务清理而删除。

## 5. 运行与配置

Web 服务运行于 `ai4m-service-py310`，MatterGen/MatterSim 运行于独立的
`/data/mamba/envs/mattergen-py310`。在 tmux 会话内启动：

```bash
conda run -n ai4m-service-py310 python main.py
```

首次部署从 `.env.example` 复制出本机 `.env` 并填写密钥；`.env` 不提交 Git。关键配置包括：

- `MATTERGEN_ENV_PREFIX`、`MATTERGEN_TIMEOUT_SEC`、`MATTERGEN_DEFAULT_CANDIDATES`；
- `MATTERSIM_ENABLED`、`MATTERSIM_REFERENCE_MODE`、`MATTERSIM_TIMEOUT_SEC`；
- `MP_API_KEY`；
- MinIO 凭证和图片/GLB 公网基础 URL；
- 可选的 `NEW_MATERIAL_LLM_*`，仅用于无法从确定性规则得到元素体系时的受限补全。

不要在 `base` 环境启动，也不要把启动脚本或 Docker 当作本服务的标准运行方式。

## 6. 验收与排障

提交代码前至少运行：

```bash
conda run -n ai4m-service-py310 python -m unittest discover -s tests -v
conda run -n ai4m-service-py310 python -m compileall -q main.py src tests tools
git diff --check
```

常见现象的判断方式：

- **“需要补充生成信息”**：元素体系/材料起点不够可靠，正常等待用户或上游补充，不应执行生成。
- **“不属于无机新晶体生成服务”**：FDM/已有商品/合金配比等请求路由错误，应交给相应服务。
- **只有 GLB 或 PNG 未显示**：先检查 task 的 manifest、资产上传日志及资源 URL 的 HTTP 状态；若 URL 可访问，则通常是前端相应资产类型的渲染分支问题。
- **GLB 看起来有断开的连接**：模型默认显示按 CrystalNN 识别的周期近邻/配位连接，便于阅读整体结构拓扑；连线不代表计算得到的键级或键能。模型以相邻重复单元表达周期性，并隐藏展示范围外的连接，避免出现悬空线。若原子位置本身仍异常，再检查候选 CIF、松弛结构和结构准入记录。
- **没有热力学数值**：候选可仍有结构资产，但只能标为初筛未完成；排查 MatterSim、MP、GPU、超时和对应任务日志，不能将其与已完成 E_hull 初筛的候选混称为推荐。
