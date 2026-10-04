# 材料计算服务正式版本交接（2026-10-04）

## 版本与范围

仓库：`AI4SciFoundation/Material-Computation`。
正式维护分支：`update/material-services-2026-08`，即 `service-08`。
备份分支：`main`，保留 `34305b968445186d93cb37a96bcb1a338e845a86`，本次不移动。

本次整合：

- 1111 当前本机尚未提交的逐域正式优化适配、镍基/铜基专项、数值条件与需求解析、通用设计准备及回归测试。
- 1111 历史工作分支中的共用组成表、候选展示、支持域诊断、影子运行时健康检查与验收记录；冲突处保留后续正式实现，旧的一行成分展示由逐元素表替代。
- 1115 OMatG CSP / DNG、元素约束与必含锚点、MatterSim / ALIGNN 评价、图文及三维输出、弹窗确认、完整上文解析。
- 1115 已在影子目录验证的后续显示流程：需求场景承接、精简参数表、候选唯一编号、默认稳定性复核参考线以及未收敛/计算失败/未执行的逐项原因解释。
- 根 README、1111/1115 README、OMatG 无密钥环境模板、部署验收与回退指南。

其他服务保持此前已提交的代码。历史工作分支已完成内容在此整合；实验结果、运行文件和模型本体按下述边界交接。

## 从哪里读起

1. [仓库 README](../README.md)：正式与备份版本、服务分工及共同约定。
2. [1111 README](../material_composition_optimization/README.md)：八类材料任务、正式路由、数值资源、请求与测试。
3. [1115 README](../material_discovery/README.md)：CSP / DNG 条件、模型链、接口示例、配置及回归命令。
4. [1115 部署与回退](../material_discovery/docs/OMATG_DEPLOYMENT.md)：版本差异、生产目录保护及前端验收。
5. [1115 弹窗输入](../material_discovery/docs/OMATG_POPUP_INPUTS.md)：化学式、元素范围和开放探索的字段映射。

## 已执行验证

验证在 `/data/se42/worktrees/material_service_hub_release_20261004` 中完成。生产目录只读，不重启服务，不上传前端资产，不请求在线生成。

| 验证项目 | 结果 |
| --- | --- |
| 1111 `tests/` 全量 pytest | 271 passed；15 条既有字符串转义及依赖弃用警告。 |
| 1115 OMatG 契约、场景、报告、展示、异常原因与采样计划 | 176 项：174 通过，2 项采样 worker 测试在 Web 环境跳过，随后在专属环境运行。 |
| 1115 旧后端服务契约与 ALIGNN 属性 | 25 项通过。 |
| 1115 周期几何、结构可视化与比较图 | 37 项通过；其中比较图断言也在 Web 回归中执行，数量不作为独立用例累计。 |
| OMatG 专属环境的 CPU / CUDA 元素锚点测试 | 2 项通过。 |
| `tools/preflight_omatg.py` | `ready=true`，环境、CSP/DNG 检查点、MatterSim 参考库及五个 ALIGNN 属性头可用。 |
| 提交文件审计 | Python 语法、意外二进制/模型文件、明显密钥及私钥检查；提交不含生产 `.env`。 |

预检仍记录 `dielectric_constant_x` 权重不可用；弹窗与报告按实际可用能力显示。环境预检不是新的科学候选计算，本文不把此前任务的生成结果当作本次重新运行。

## 外部资源与复现

Git 包含服务源码、测试、无密钥配置与文档。以下资源由服务器单独管理：

- 1111 专项 runner、冻结优化运行时、展示模块、数据及模型：以1111 README列出的目录为准。
- OMatG 源码 `/data/third_party/OMatG` 与权重 `/data/se42/omatg_local/checkpoints`。
- MatterSim / ALIGNN 环境和本机权重、MP2020 只读参考库。
- 生产 `.env`、对象存储密钥、任务 CIF、日志、缓存、PNG/GIF/GLB。

接手人在新机器运行数值计算时，需同时取得对应外部资源版本；仅拉取 Git 不会下载模型或重新训练。禁止把模型、密钥或大体积实验目录一起提交。

## 部署状态与后续操作

此次操作是代码整理与推送，线上1111/1115源文件和进程保持原状。正式仓库代码包含此前仅在影子目录验证的显示优化；安排上线前，按独立部署清单审批和验收。

生产 Git 工作目录保留原有本机修改，不因本次整合切换分支、覆盖文件或清空状态。生产目录直接拉取正式分支可能与这些编辑冲突；先查看差异并保存目标文件，再进行明确的增量部署。

GitHub 默认分支拟设为 `update/material-services-2026-08`。当前登录账号具备 `push`，不具备 `admin/maintain`；默认分支修改需仓库管理员完成。管理员可执行：

```bash
gh repo edit AI4SciFoundation/Material-Computation --default-branch update/material-services-2026-08
```

保留两条长期分支；旧 MatterGen 分支先保存为 `archive/mattergen-discovery-pipeline-20261004` 标签，再移除旧分支引用，历史代码仍可追溯。
