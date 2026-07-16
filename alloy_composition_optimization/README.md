# alloy_composition_optimization

合金配比优化总服务。它使用共享的合金成分解析、描述符、任务 envelope 和流式进度协议，但按材料域路由至**独立训练和验证的模型**：

- `hea_mpea`：实验 HEA/MPEA；当前第一条训练线。
- `conventional_alloy`：成熟常规合金；待商品材料数据清洗后独立训练。
- `refractory_calculated`：NbCrVWZr 等计算/模型派生数据；严格独立于实验标签。

因此该服务不是“把所有合金强行训练成一个模型”。不同数据生成机制、成分空间和测试条件会保留独立的适用域与不确定性；可共享的仅是元素特征与 API。

## 运行架构

网页调用统一进入本服务；无需由前端判断或直接调用数值子进程。

- **主服务 / WebSocket / 流式结果层**：固定使用 Conda 环境 `ai4m-service-py310` 启动。它负责需求解释、任务协议、结果页、图表、以及按邻近服务约定调用 SeLLM 的 token/chunk 流式文本输出。
- **HEA 数值代理层**：默认复用已部署的 micromamba 环境 `mattergen-py310`，只加载并运行 HEA/MPEA 的 sklearn ensemble；主服务通过 JSON 请求/响应文件和 `micromamba run` 调用它。若未来需要独立版本锁定，可运行 `tools/setup_hea_surrogate_env.sh` 并用 `HEA_SURROGATE_ENV_PREFIX` 切换到专属环境。
- **上游编排层**：决定结果是否交给数学优化服务。本服务只在任务结果中生成可读结论和机器可读交接字段，不主动向下游发送请求。

首次部署在现有 `mattergen-py310` 环境中训练：

```bash
cd /data/se42/hea_surrogate
/home/ubuntu/micromamba run -p /data/mamba/envs/mattergen-py310 python -m src.models.train_baselines --task all
cd /data/se42/alpha_project/alpha_material_sync/alloy_composition_optimization
./start.sh
```

`start.sh` 会使用 `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python`（可用 `AI4M_SERVICE_PYTHON` 覆盖）。`/health` 中 `hea_runner_ready: true` 才表示可进行真实的 HEA 预测。

容器化部署若需要沿用此架构，镜像必须同时提供上述 Conda 主服务环境和 micromamba 模型环境；当前 `Dockerfile` 仅是开发用单环境示例，不能替代此部署命令。

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
  "idea": "900°C 高强度、低密度难熔 HEA",
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

`/alloy/propose-space` 还会生成用户结果页资产：`summary.md`、候选强度—硬度散点图和筛后成分分位图。通过任务 manifest 的 `presentation` 字段或 `GET /alloy/tasks/{taskid}/assets/{asset_name}` 获取。
