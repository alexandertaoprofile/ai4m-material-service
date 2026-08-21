# material_discovery

无机新材料服务，面向生成式无机晶体发现：MatterGen 候选生成、pymatgen 结构准入和确定性排序。

## 当前状态

- 服务可启动（FastAPI/WebSocket 主入口存在）
- 已移除退役的 `handler/pipeline` 案例执行链；主线由 `src/team_config.py` 和 `src/material_workflow/` 承担
- 已新增 `src/material_workflow/` 作为新材料主线的规范接口层
- `POST /new-material/generate` 已接入真实 MatterGen 子进程；每次任务保留命令、日志、CIF 与 manifest
- `POST /new-material/constraints`：上游任务的约束预览，不占用 GPU
- `WS /new-material/start`：兼容现有 `/start` 的 `taskid / idea / user_name / file_metadata` envelope
- `GET /new-material/tasks/{taskid}`：查询已落盘的任务 manifest
- 生成后使用 pymatgen 执行轻量结构准入（有序性、最短原子间距、密度与空间群）
- `team_config_en.py` 暂保留，后续用于英文流程设计

## 主要入口

- `main.py`：服务启动入口
- `team_config.py`：兼容入口（桥接 `src/team_config.py`）
- `src/`：核心业务代码

## 新材料主线规划

目标链路：

1. 需求解析：把用户输入整理为生成约束与验证目标
2. 候选生成：通过 MatterGen 或兼容生成器产生候选结构
3. 结构验证：通过 pymatgen 结构准入检查，再进入后续热力学评估
4. 性质初筛：对通过结构准入的候选使用 ALIGNN 快速预测带隙、弹性、介电和有效质量；模量齐全时另给出带不确定性区间的硬度工程估算
5. 候选排序：基于真实验证结果和用户目标进行确定性排序
6. 前端输出：基于 manifest 渲染摘要、动图和可追溯资源

主链实现位于：

- `src/material_workflow/schemas.py`：Generation/Validation/Ranking/Pipeline schema
- `src/material_workflow/generation.py`：MatterGen CLI runner、CIF 解包、生成 manifest
- `src/material_workflow/validation.py`：pymatgen 结构准入与 validation manifest
- `src/material_workflow/alignn.py`：ALIGNN 轻量结构性质初筛；离子电导率等无对应预训练模型的性质不会生成虚构数值
- `src/material_workflow/ranking.py`：确定性排序帮助函数
- `src/material_workflow/emitters.py`：前端 payload 与 manifest 输出
- `src/material_workflow/pipeline.py`：新材料主线编排骨架
- `docs/new_material_pipeline_plan.md`：后续接入计划

## 目录说明

- `config/`：配置文件
- `src/`：业务实现
- `src/material_workflow/`：新材料主线规范接口层
- `tools/`：工具脚本
- `alpha/`：历史框架代码（暂保留）
- `docs/`：结构规范与迁移计划

## 运行方式（开发态）

主服务应运行在 `ai4m-service-py310` 环境；MatterGen 仍由独立的
`mattergen-py310` 环境执行。不要在基础 `base` 环境直接启动旧 `alpha` 外壳。

```bash
conda run -n ai4m-service-py310 python main.py
```

生产/联调环境由 tmux 会话直接启动和守护，不以 `start.sh` 作为标准入口。实际密钥写入本机
`.env`，可从 `.env.example` 复制后填写；`.env` 不提交到 Git。

## 环境与调用

Web 服务与 GPU 生成环境隔离：主服务继续在 `conda` 的 `ai4m-service-py310` 环境中运行；
MatterGen 使用 `micromamba` 的 `/data/mamba/envs/mattergen-py310` 子环境。官方源码默认安装到
Git 仓库之外的 `/data/third_party/mattergen`，首次调用会下载预训练权重。

```bash
bash tools/setup_mattergen_env.sh
python main.py
```

请求示例：

```json
{
  "taskid": "solid-electrolyte-001",
  "allowed_elements": ["Li", "P", "S", "Cl"],
  "target_properties": {"energy_above_hull": 0.05},
  "max_candidates": 8
}
```

生成结果位置由 `src/service_paths.py` 统一定义；当前落在历史兼容目录
`src/MNS_CaseHub/cases/material_discovery_demo/results/new_material/<taskid>/`。
`target_formula` 目前用于提取元素体系，而非严格化学计量 CSP；严格组分生成需要部署自训练的 CSP checkpoint。

### 上游接口约定

上游可直接提交既有任务 envelope，并把可计算约束放入 `new_material`（也兼容
`constraints` / `mattergen` / `generation_constraints`）。显式约束优先。没有 JSON 时，服务会从
当前需求与上文中确定性提取元素体系（如 `Nb-Mo-Ta-W` 或“铌、钼、钽、钨”）、化学式、明确写出的
`E_hull` 阈值（支持 `meV/atom`）以及高温/蠕变/抗氧化等验证关注点；提取结果会显示在“解析说明”中。
如果用户未给出 `E_hull`，服务会使用默认值 `E_hull ≤ 0.05 eV/atom` 作为 MatterGen 生成引导，确保能够选用相应的条件模型；无法确定元素体系时仍会要求补充，而不是启动无约束生成。

对于明确但未给出元素的无机固态电解质方向，服务可使用可见、可覆盖的领域起始模板，并优先由受限 LLM 结合完整上游材料结论细化元素体系。模板仅作为生成起点，默认 `E_hull ≤ 0.05 eV/atom`，会在前端“解析说明”和设计约束卡中标记；用户一旦明确给出元素或 JSON，模板立即失效。

高熵合金、难熔合金、元素比例、原子百分比与成分空间优化不属于本服务；这些请求会被明确拒绝，应进入 `alloy_composition_optimization`。

```json
{
  "taskid": "solid-electrolyte-001",
  "user_name": "upstream-agent",
  "idea": "开发 Li-P-S-Cl 固态电解质",
  "file_metadata": [],
  "new_material": {
    "allowed_elements": ["Li", "P", "S", "Cl"],
    "target_properties": {"energy_above_hull": 0.05},
    "validation_targets": {"ionic_conductivity": 0.001},
    "max_candidates": 4
  }
}
```

`target_properties` 仅放 MatterGen 已支持的生成引导目标；`validation_targets` 会记录到
manifest，供后续 MatterSim/DFT/实验验证使用，不会被误报为本轮计算结果。

通过结构准入后，服务默认使用 `ALIGNN_ENV=alignn-gpu-test` 为每个候选快速计算带隙、体积模量和剪切模量；`ALIGNN_TIMEOUT_SEC` 默认 600 秒，以容纳新部署节点的首次权重下载。生产启动前可预热这三套 ALIGNN 权重，后续推理不需要分子动力学。

### 前端叙事与真实可视化

WebSocket 结果按“设计任务解读 → 生成与筛选证据 → 阶段判断”流式输出 Markdown 文本和候选表格。
每个完成任务还会基于真实候选结构生成 `presentation/` 资产：晶体结构 PNG、旋转 GIF、
MatterSim--MP 热力学评分卡，以及可交互的 GLB。资产通过既有 `MaterialsPNG` / `MaterialsGLB`
协议下发；若对象存储不可用，计算结果与本地资产路径仍保存在 manifest 中。

### 供母 Agent 复用

`InorganicNewMaterialDiscoveryRole` 是“生成式无机新材料发现”专属角色，而不是已有材料 MP 检索角色。
母 Agent 在需要探索新结构时调用 `WS /new-material/start` 并传递上游 envelope；优先提供
`new_material.allowed_elements` 和数值化 `target_properties`。若只有自然语言，上文中至少应包含
元素体系；服务会自动提取明确的稳定性阈值和验证关注点，并标记其解析来源。纯对话请求默认只生成
1 个候选，可通过 `max_candidates` 或 `MATTERGEN_DEFAULT_CANDIDATES` 调整。
角色会返回候选结构、MatterSim--MP 热力学初筛、阶段结论和 manifest；其结论只能用于决定
是否进入 DFT/专项性能验证，不能替代这些验证。

## 依赖建议

- `requirements.minimal.txt`：Web 与新材料主线的最小依赖；旧 `alpha` 通用框架仍需
  `ai4m-service-py310` 环境中的完整运行依赖，后续解耦后再统一。
- `pip_requirements.txt`：历史全量依赖（体积大，建议按需补装）

## 无 GPU 回归测试

下列测试不启动 MatterGen、MatterSim 或 MP 请求，可验证入口导入、`/roles`、约束预览和 WebSocket 事件边界：

```bash
conda run -n ai4m-service-py310 python -m unittest discover -s tests -v
```

## 后续演进方向

1. 继续扩展对更多 MatterGen 可条件化性质的自然语言数值解析。
2. MatterSim 评估默认开启；
   默认会在 MatterGen 后对通过基础结构准入的候选自动执行 MatterSim 松弛，回填
   `formation_energy_per_atom` 与 `energy_above_hull`。默认只经 MP API 查询候选元素体系的竞争相，
   避免加载全量参考库；结果明确标为 **MatterSim--MP 混合近似**，不能替代 DFT。设置
   `MATTERSIM_REFERENCE_MODE=official` 才会使用完整 MatterGen MP2020/Alexandria 参考库。参考数据准备：

   ```bash
   bash tools/setup_mattersim_reference.sh
   ```

   也可单独对已有输出运行：

   ```bash
   micromamba run -p /data/mamba/envs/mattergen-py310 python \
     tools/run_mattersim_evaluation.py \
     --input src/MNS_CaseHub/cases/material_discovery_demo/results/new_material/<taskid>/generation/cifs/gen_0.cif \
     --output-dir src/MNS_CaseHub/cases/material_discovery_demo/results/new_material/<taskid>/mattersim
   ```
3. 与已有材料服务共享的公共能力逐步抽到通用层

详细的交接、协议和排障说明见
[`docs/inorganic_new_material_service_guide.md`](docs/inorganic_new_material_service_guide.md)。
