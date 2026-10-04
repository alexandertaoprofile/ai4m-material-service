# 线上源码、开发内容与部署状态

核对日期：2026-10-04。仓库：`AI4SciFoundation/Material-Computation`。

## 1. 分支与目录含义

`main` 是完整项目，不单独等同于“已经全部部署”。根目录四个服务保存 75 当前源码快照，`development/` 保存下一版整合代码与独立实验；`backup` 固定保留旧 main 的 `34305b968445186d93cb37a96bcb1a338e845a86`，不滚动更新。

GitHub 历史备份与生产回退备份用途不同：部署前仍需保存当前目标文件和 `.env` 等机器配置，后者不入 Git。原整合版本 `213155c608677612a1e7845a5986ce42cb55e0c2` 保留在提交历史中，开发副本的实现源于该版本。

## 2. 当前服务器对应关系

| 仓库目录 | 75 服务器来源 | 状态 |
| --- | --- | --- |
| `material_database/` | `/data/se42/alpha_project/material_service_hub/material_database` | 核对到当前活动服务进程；本次检查的源码与配置文件相符。 |
| `material_composition_optimization/` | `/data/se42/alpha_project/material_service_hub/material_composition_optimization` | 核对到当前活动 1111 进程；当前源码快照，保留逐材料正式路线。 |
| `material_discovery/` | `/data/se42/alpha_project/material_service_hub/material_discovery` | 核对到当前活动 1115 进程；生成器已接入 OMatG，后续报告更新未部署。 |
| `material_validation/` | `/data/se42/alpha_project/material_service_hub/material_validation` | 检查的源码与配置文件相符；本次未确认对应活动进程，不据此宣称已完成在线验收。 |
| `development/material_composition_optimization/` | 原整合工作树及已完成历史工作分支。 | 完整下一版副本，不作为当前运行目录。 |
| `development/material_discovery/` | 整合工作树；改进来源包括 `/data/se42/omatg_local/1115_display_shadow_20261001/service`。 | 已验证待部署，包括新异常原因模块和相互依赖的调用方。 |
| `development/1111_physics/` | 两个 `oxide_glass_*physics*` 离线实验目录。 | 玻璃约束训练与评估源码，未接入线上服务。 |

根目录的新增文档与无密钥配置样例是交接材料，不是服务器新增的运行配置。线上快照包含服务器尚未提交的源码；不能仅用生产 Git HEAD 判断当前文件版本。

## 3. 仍在仓库外的运行依赖

1111 的专项执行器、冻结运行时和展示模块仍分布在 `/data/se42/hea_surrogate`、`/data/se42/experiments/material_rsi_prelaunch_20260928/package/runtime` 和 `/data/se42/experiments/material_shadow_frontend_v2_20260928` 等路径；名称含 `experiments` 或 `shadow` 不意味着可以移除。具体用途见[1111 README](../material_composition_optimization/README.md)。

1115 的 OMatG 源码、权重、MatterSim/ALIGNN 环境和参考库保持现有路径，见[1115 README](../material_discovery/README.md)与[环境说明](../env/README.md)。本次不移动或复制这些资源，不安装依赖，不修改生产 `.env`。

## 4. 本次整理的验证与边界

整理在 `/data/se42/worktrees/material_service_hub_main_layout_20261004` 的独立克隆中进行。原生产源码、配置和活动进程保持原状。根目录源码与服务器逐文件核对；开发副本的源文件与原整合提交核对；物理约束脚本与原实验文件核对。只比较代码及受版本管理的配置，不读取或上传生产密钥、模型与任务结果。

具体验证结果在整理完成后记录于[目录整理验收](MAIN_LAYOUT_VERIFICATION_20261004.md)。历史整合测试见[版本交接](RELEASE_HANDOFF_20261004.md)，与本次实际执行分别记录。

可重复执行的只读检查，在仓库根目录运行：

```bash
python3 tools/verify_repository_layout.py
# 在 75 上另外与当前生产源文件比较；如果生产后来有更新，会报告差异：
python3 tools/verify_repository_layout.py --live-root /data/se42/alpha_project/material_service_hub
```

## 5. 后续更新与上线

1. 在开发目录实现、测试并记录改进；联调端口、配置、结果目录与线上分开。
2. 验收通过后，把相关改动成组纳入根目录对应服务，并同步部署状态与源码快照记录。
3. 按用户批准的部署清单单独更新 75，保留同事编辑和机器配置；只重启对应服务。
4. 核验真实任务、最终图文及资产后，记录部署提交、资源版本和验收结果，再标记为已上线。

当前生产工作树有本机修改，不在其中直接拉取、切分支或整仓库覆盖。读取 GitHub main 不会自动改动现有进程；本次操作只整理并推送仓库。

生产工作树仍保留原有 Git 分支及上游设置，本次不修改。原 `update/material-services-2026-08` 整合分支归档为 `archive/material-services-integration-20261004` 标签后，不再作为后续拉取入口；接手者从独立的 main 克隆制作部署清单，而不是在生产工作树继续拉取旧分支。
