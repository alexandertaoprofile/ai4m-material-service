# 合金成分优化服务：清理审计（2026-07-24）

本记录是改造后清理的引用证据，不替代改造前基线文档。

## 已删除

以下项目不被 `main.py`、根 `team_config.py`、`src/team_config.py`、`src/alloy_workflow/`、测试或另外两个服务引用；其内容属于无机晶体、Materials Project、MatterGen/MatterSim、ALIGNN 或旧 MNS 案例链，超出本服务边界。

- `src/team_config_inorganic_legacy.py` 与复制进来的 `src/material_workflow/`；
- `src/MNS_CaseHub/`（包括 `registry/` 中的旧数据集和 material discovery demo）；
- `tools/mp_*`、`tools/run_mattersim_evaluation.py`、`tools/run_pymatgen_validation.py`、`tools/render_new_material_assets.py`、GLB/EXTXYZ 工具、MatterGen/MatterSim 环境工具和旧 case 启动器；
- `src/oss_utils.py`（旧阿里云 OSS，生产资产路径使用 `src/storage_utils.py` 的 MinIO/S3 实现）；
- `src/utils/` 的旧 demo 子进程/WebSocket/UI 适配器；
- `alignn-gpu-test.conda.before.txt` 与 `alignn-gpu-test.freeze.before.txt` 环境快照；
- 调用无机工作流的 `tools/run_refractory_alloy_demo.py`。

其中 `src/team_config_en.py` 虽然仍有旧 MNS 路径文字，但它指向的 `dataset_en.json` 本就不在仓内；该文件按通用兼容约定保留，不能作为生产引用证据。

## 保留项及理由

| 项目 | 理由 |
| --- | --- |
| `alpha/` | 共享 Alpha 框架兼容项；根 `team_config.py` 与角色运行仍依赖它。 |
| `src/team_config_en.py`、`src/llm_utils.py` | 明确保留的旧兼容项；尚未进行外部动态导入迁移。 |
| `config/config.yaml`、`config/config2.yaml` | Alpha 配置兼容项；`alpha/config2.py` 会寻找 `config/config2.yaml`。 |
| `src/storage_utils.py` | 当前 PNG 资产的 MinIO/S3 发布路径。 |
| `tools/setup_hea_surrogate_env.sh` | 当前 HEA/MPEA 独立 runner 的环境准备工具。 |
| `deploy/alloy-composition-optimization-1111`、`start.sh` | 仓内部署包装仍引用 `start.sh`；虽非 tmux 标准入口，删除前必须确认运维侧不再使用。 |
| `Dockerfile`、`start_docker.sh`、`requirements.minimal.txt` | 不属于当前 tmux 启动路线；暂保留直到部署资产的外部使用状态确认。 |

## 三服务职责对齐核验

| 服务 | 传输入口 | 服务编排 | 领域/用例 | 结果与基础设施 |
| --- | --- | --- | --- | --- |
| alloy_composition_optimization | `main.py` | `src/team_config.py` | `runtime.py`、`application.py`、`contracts.py`、`runner.py` | `presentation.py`、`protocol.py`、`assets.py`、`storage_utils.py` |
| mature_material | `main.py` | `src/team_config.py:MaterialMature` | `src/catalog/` | `catalog/assets.py`、`catalog/narration.py`、`storage_utils.py` |
| inorganic_new_material | `main.py` | `src/team_config.py:InorganicNewMaterialService` | `src/material_workflow/` | `material_workflow/presentation.py`、`emitters.py`、`storage_utils.py` |

三者均没有 `team_config.py` 反向 import 自身的 `main.py`。目录名因业务不同而不同，但职责方向一致：传输入口 → 服务编排 → 用例/领域执行 → 展示与基础设施。

## 尚待业务决策的风险

`alloy_optimization.model_domain` 仍接受 `conventional_alloy` 和 `refractory_calculated`，但目前已验证的模型、数据说明和测试只覆盖 `hea_mpea`。在为两个域提供独立模型、数据契约和验收测试前，应决定是暂时拒绝它们，还是实施对应模型；不能将其视为已可用预测域。
