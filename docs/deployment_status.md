# 线上源码、开发内容与部署状态

核对日期：2026-10-04。仓库：`AI4SciFoundation/Material-Computation`。

## 2026-10-14 材料筛选目录名称与重启说明

1105 服务目录统一为 `material_selection`。75 正式目录为 `/data/se42/alpha_project/material_service_hub/material_selection`，tmux 会话仍是 `material-product-1105`，端口仍为 1105。服务器目录迁移提交为 `c47bab2`；73 项测试及 4 个子测试通过，当前目录为 1,634 个材料身份、7,620 条性质点。

本次 GitHub 更新记录目录改名、README、启动与资源恢复工具的路径，以及旧入口清理工具。其余服务源码继续保留对应快照和开发版本，服务器上的其他本机编辑单独处理。2026-10-04 原始源码清单保持原值；后续目录映射和新增文件记录在 `source_layout_20261014.json`，结构检查分别核验原始源码和新增清理工具。

2026-10-14 最终核对：旧 `material_database` 入口已清理，1105 从 `material_selection` 运行，进程启动时间为当天 13:35:12，`/health` 返回 `status=ok`、`catalog_ready=true`。迁移期间保留旧入口，是为了兼容旧进程中已保存的数据库、字体等绝对路径；迁移尚未完成的副本须先停止旧进程，再清理软链接和启动服务。详细步骤见[1105 README](../material_selection/README.md#75-服务器的手动重启)。源文件和数据仍保留在正式目录中。

GitHub 提交前在独立副本恢复清单中已校验的 1105 资源，完成以下检查：服务回归 73 项及 4 个子测试通过；运行工具 16 项及 4 个子测试通过；24 份 README、41 份文档、299 个链接、16 个 JSON 示例和 45 个 shell 代码块检查通过；启动工具成功加载 1105 应用，`/roles` 返回 HTTP 200。目录校验确认其余服务及开发副本源码与原始清单一致。

## 1. 分支与目录含义

`main` 包含完整项目。根目录四个服务保存本页核对日期对应的 75 源码，`development/` 保存后续更新与独立实验；`backup` 固定保留旧 main 的 `34305b968445186d93cb37a96bcb1a338e845a86`，不滚动更新。更新 GitHub 文件与部署服务器分别记录。

GitHub 历史备份与生产回退备份用途不同：部署前仍需保存当前目标文件和 `.env` 等机器配置，后者不入 Git。原整合版本 `213155c608677612a1e7845a5986ce42cb55e0c2` 保留在提交历史中，开发副本的实现源于该版本。

## 2. 当前服务器对应关系

| 仓库目录 | 75 服务器来源 | 状态 |
| --- | --- | --- |
| `material_selection/` | `/data/se42/alpha_project/material_service_hub/material_selection` | 核对到当前活动服务进程；本次检查的源码与配置文件相符。 |
| `material_composition_optimization/` | `/data/se42/alpha_project/material_service_hub/material_composition_optimization` | 核对到当前活动 1111 进程；当前源码快照，保留逐材料正式路线。 |
| `material_discovery/` | `/data/se42/alpha_project/material_service_hub/material_discovery` | 核对到当前活动 1115 进程；生成器已接入 OMatG，后续报告更新未部署。 |
| `material_validation/` | `/data/se42/alpha_project/material_service_hub/material_validation` | 源码与配置文件相符；尚未确认对应在线进程，在线运行还需单独检查。 |
| `development/material_composition_optimization/` | 原整合工作树及已完成历史工作分支。 | 完整下一版副本，不作为当前运行目录。 |
| `development/material_discovery/` | 整合工作树；改进来源包括 `/data/se42/omatg_local/1115_display_shadow_20261001/service`。 | 已验证待部署，包括新异常原因模块和相互依赖的调用方。 |
| `development/1111_physics/` | 两个 `oxide_glass_*physics*` 离线实验目录。 | 玻璃约束训练与评估源码，未接入线上服务。 |

根目录的说明文件与无密钥配置样例用于交接。服务器还保留尚未提交的源码，因此核对运行目录时要比较实际文件，不能只看 Git 提交号。下一版的不同文件、修改目的和测试位置见[开发改动清单](development_changes.md)。

## 3. 仍在仓库外的运行依赖

1111 的专项计算程序、固定版本的优化程序和报告模块仍分布在 `/data/se42/hea_surrogate`、`/data/se42/experiments/material_rsi_prelaunch_20260928/package/runtime` 和 `/data/se42/experiments/material_shadow_frontend_v2_20260928` 等路径；这些目录中的部分文件参与当前请求计算，保留原路径。具体用途见[1111 README](../material_composition_optimization/README.md)。

1115 的 OMatG 源码、模型文件、MatterSim/ALIGNN 环境和参考库保持现有路径，见[1115 README](../material_discovery/README.md)与[环境说明](../env/README.md)。已核对的版本、文件校验值和迁移准备事项集中在[外部依赖说明](external_dependencies.md)。

2026-10-05 资源路径补充：1116 的训练与 MD 归档已在 `/data/se42/cache/DeepMD-W-14-commercial-level` 和 `/data/se42/cache/DeepMD_W-14_commercial-level` 确认主要文件。75 服务配置仍记录不含 `cache/` 的旧路径。联调或部署前需核对配置与证据目录，并完成读取检查；在线状态按实际部署验收记录确认。完整环境、模型、数据和启动说明见[运行说明](service_setup.md)。

## 4. 源码和目录检查

整理在 `/data/se42/worktrees/material_service_hub_main_layout_20261004` 的独立克隆中进行。根目录源码与服务器逐文件核对；开发副本与原整合提交核对；物理约束脚本与原实验文件核对。比较范围为代码及受版本管理的配置，服务器资源及版本见外部依赖说明。

具体验证结果在整理完成后记录于[目录整理验收](MAIN_LAYOUT_VERIFICATION_20261004.md)。历史整合测试见[版本交接](RELEASE_HANDOFF_20261004.md)，与本次实际执行分别记录。

可重复执行的只读检查，在仓库根目录运行：

```bash
python3 tools/verify_repository_layout.py
# 在 75 上另外与当前生产源文件比较；如果生产后来有更新，会报告差异：
python3 tools/verify_repository_layout.py --live-root /data/se42/alpha_project/material_service_hub
```

## 5. 后续更新与上线

1. 在开发目录实现、测试并记录改进。联调端口、配置和结果目录与线上分开，根目录继续保存最近一次确认的部署源码。
2. 测试通过后制作部署清单，列出需要一起更新的文件、配置差异、依赖版本和恢复旧版本的方法。此时状态写为“测试通过，待部署”，代码仍在开发目录。
3. 获得部署批准后，先保存目标服务器的原文件与本机配置，再按清单更新 75，保留同事编辑和机器配置；只重启对应服务。
4. 检查真实任务、最终报告、图片和三维文件。通过后，把实际部署的源码同步到根目录对应服务，记录部署日期、文件版本、使用的模型及检查结果，再标为“已上线”。同步时保留 Git 中的文档与无密钥样例，生产 `.env` 不入 Git。
5. 部署中或检查未通过时，根目录保留上一次已确认的源码，并在本页说明服务器的实际状态及恢复结果。部分文件上线时逐项记录，避免把整个开发副本标为已上线。

部署状态写法如下：

| 状态 | 要记录的内容 |
| --- | --- |
| 开发中 | 改动目的、相关文件、尚需完成的测试。 |
| 测试通过，待部署 | 测试日期和结果、完整部署清单；线上仍使用原版本。 |
| 部署中或检查未通过 | 已更新的文件、问题、处理或恢复结果；尚未确认的功能。 |
| 已上线 | 实际部署日期和源码版本、环境与模型版本、真实请求检查结果。 |

日常同步和推送从独立的 `main` 仓库副本进行，步骤见[代码同步说明](code_synchronization.md)。生产工作目录仍保留旧分支和本机编辑，直接拉取或合并有文件冲突及覆盖风险。

原 `update/material-services-2026-08` 整合分支已通过 `archive/material-services-integration-20261004` 标签归档。后续代码维护使用 `main`；部署时按文件清单更新服务器，并单独检查服务运行结果。
