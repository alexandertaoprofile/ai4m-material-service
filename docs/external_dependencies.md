# 仓库外的计算程序、模型与环境

主要版本记录日期：2026-10-04；2026-10-05 补充 1105 专项数据库和 1116 证据归档说明。仓库负责服务源码、测试和说明；下表资源在 75 服务器其他目录中管理。每个服务的准备顺序和启动方式见[运行与复现说明](service_setup.md)。

按无密钥配置样例及服务 README 中的路径记录软件版本、源码和模型文件校验值。完整记录见[资源版本 JSON](external_resources_20261004.json)。这些记录用于核对资源版本；运行能力按服务 README 执行预检及真实请求检查。

## 1. 资源分别做什么

| 资源 | 服务用途 | 75 服务器位置 | 已记录的内容 |
| --- | --- | --- | --- |
| 主 Web 环境 | 接收请求、组织计算和返回报告。 | `/home/ubuntu/miniconda3/envs/ai4m-service-py310` | Python 3.10.19 和主要软件包的安装版本。 |
| 1105 导电润滑剂数据库 | 读取专项材料性质和来源证据。 | `/data/se42/alpha_project/material_service_hub/material_database/data/processed/fluid_lubricant/2026-08-04_v1/fluid_property_evidence.sqlite` | 2026-10-05 核对为 381,964,288 字节，约 364 MiB；当前 Git 提交不含该文件，需单独交接。 |
| 1105 原始材料资料 | 重建通用目录、核验和导入新增数据。 | `PROPERTY_DATA_ROOT`，样例为 `/data/se42/backend/property datasets` | 通用整理 CSV 已随 Git 提供；原始工作簿和 PDF 从资料维护者取得。 |
| OMatG 源码 | 固定组成生成结构（CSP），或探索组成与结构（DNG）。 | `/data/third_party/OMatG` | 已核对选定的 83 个源码及配置文件；目录没有 Git 提交信息，当前版本按文件校验值识别。 |
| OMatG 环境 | 运行结构生成。 | `/data/mamba/envs/omatg-py312` | Python 3.12.14、OMatG 2.0.0、PyTorch 2.8.0+cu126；162 条软件包安装版本记录。 |
| OMatG 模型文件 | 分别支持 DNG 和 CSP。 | `/data/se42/omatg_local/checkpoints` 下的 `MP-20-DNG/Linear-SDE-Gamma`、`Alex-MP-20-CSP/Trig-SDE-Gamma` | 两个 `checkpoint.ckpt` 和各自 `train.yaml` 的大小及 SHA256。配置文件说明模型训练时的设置。 |
| MatterSim 环境与模型 | 调整晶胞和原子位置，计算结构能量与原子受力。 | `/data/mamba/envs/mattergen-py310`；模型在 `/data/se42/omatg_local/checkpoints/mattersim-v1.0.0-1M.pth` | Python 3.10.20、MatterSim 软件包 1.1.2、PyTorch 2.2.1+cu118，以及模型文件 SHA256。环境名和模型名保留原有名称。 |
| 稳定性参考库 | 比较候选结构与已有参考相的能量。 | `/data/mattersim_reference_cache/reference_MP2020correction` | 3,848,802,304 字节的参考文件 SHA256。迁移时保留同一版参考库，避免改变比较依据。 |
| ALIGNN 环境与模型 | 快速预测带隙、模量和电子/空穴有效质量。 | 解释器为 `/home/ubuntu/miniconda3/envs/dsc2/bin/python`，另加载 `/data/mamba/envs/alignn-gpu-test/lib/python3.10/site-packages` | 两处软件包记录及五个性质模型 ZIP 的大小和 SHA256；需保留既有加载顺序。 |
| 1111 专项计算代码 | 调用各材料的预测与计算程序。 | `/data/se42/hea_surrogate` | Git 提交号及所选 `src/`、`tools/` 共 105 个文件的实际校验值；实际文件版本以校验值为准。 |
| 1111 优化程序 | 运行已验收的配比优化。 | `/data/se42/experiments/material_rsi_prelaunch_20260928/package/runtime` | 顶层 44 个 Python 文件、两份模型文件和四份版本/依赖清单的校验值。 |
| 1111 优化环境 | 运行上述优化程序。 | `/data/se42/experiments/patent_optimization_v0_20260922/.venv` | Python 3.13.11 和主要软件包安装版本。 |
| 1111 报告程序 | 生成当前请求的配比报告与图像。 | `/data/se42/experiments/material_shadow_frontend_v2_20260928` | 六个运行入口或 `recipe_*` 模块的实际校验值；这些文件虽在实验目录，仍是运行依赖。 |
| 1116 训练、模型与 MD 归档 | 读取纯钨 W-14 标杆计算证据。 | `/data/se42/cache/DeepMD-W-14-commercial-level`、`/data/se42/cache/DeepMD_W-14_commercial-level` | 2026-10-05 确认主要训练和 MD 文件存在；75 服务的 `.env` 仍记录两处旧路径，联调前核对。文件说明和历史副本位置见运行说明。 |

SHA256 是由文件内容计算出的校验值。转移文件后值相同，可以确认内容一致。JSON 保存 2026-10-04 记录的具体文件、大小、校验值和检查范围；新增的 1105/1116 说明不是该 JSON 的追加哈希记录，后续交接时单独记录对应资源校验值。

## 2. 软件包记录怎样使用

[OMatG 软件包版本表](../env/omatg-py312.packages-20261004.txt)按安装信息导出，仅含名称和版本。PyTorch 中的 `cu126` 表示这一软件包对应 CUDA 12.6 构建，重建环境时还需匹配 GPU 驱动和相应扩展包来源。

OMatG 源码安装、操作系统库、软件包下载来源和 GPU 环境要一并准备，之后在新环境运行检查，才能形成完整安装指南。其他环境的 JSON 只记录选定的主要软件包；部分环境存在同名包安装记录或额外搜索路径，实际加载版本要按服务预检确认。

## 3. 新机器接手时怎么准备

1. 确定要运行哪些服务，先阅读服务 README 和本页对应行。
2. 请服务维护者从 75 导出所需计算源码、模型、参考库和数据，在团队指定且有访问权限的存储位置交接。导出时保留目录结构、版本清单和校验值。
3. 请算法维护者补充 OMatG 上游提交号或原始下载包版本、安装来源及本地修改说明。现有目录没有 Git 信息，迁移时先保留已核对的源码文件，避免直接以另一版官方代码替换。
4. 请环境维护者在新机器准备独立 Python 环境，记录完整安装命令、CUDA/GPU 依赖及所需软件包来源。新增文件放在数据盘或指定模型盘。
5. 从各服务无密钥样例配置路径、端口和结果目录，凭据由管理者另外提供，保存在服务自身的本机 `.env`。
6. 按 README 完成环境预检、程序测试和真实请求检查；确认模型结果与报告可用后，再安排部署。

新机器下载资源前先确定所需版本；当前运行使用的模型和源码由维护者按清单提供。交接存储位置和具体负责人需要在实际迁移前登记，不在此填写未经确认的人名或下载地址。

## 4. 尚需补齐的内容

| 内容 | 对接手的影响 | 下一步 |
| --- | --- | --- |
| OMatG 上游代码版本及下载包来源 | 当前能比较文件内容，尚不能仅凭提交号从上游重新取得相同源码。 | 算法维护者确认下载包或提交版本，保存本地修改和源码包校验值。 |
| 经验证的新机器安装流程 | 版本记录方便核对，但尚未在全新环境逐步重建并验收。 | 环境维护者在独立环境验证安装、模型导入与最小真实任务，记录成功命令。 |
| 1111 全部专项权重和数据 | 本次检查了指定优化程序的模型与依赖清单；其他专项数据和模型仍需完整交接。 | 专项维护者逐类别核对现有 `PACKAGE_MANIFEST.json`、`FROZEN_INPUT_INVENTORY.json`、`EXTERNAL_DEPENDENCIES.json` 及模型/数据来源记录，补齐实际依赖。 |
| 1105 原始数据与 1116 外部证据 | 原始工作簿、完整训练或模拟输出需要另行交接。 | 按各服务的数据与证据导入说明准备文件及来源；1116 再单独确认在线运行。 |
| 资源存储位置及维护人 | 新同事需要知道向谁取得文件、从哪里下载。 | 实际交接前登记团队存储位置、访问方式和负责人员。 |

目录核对和模型版本记录见本页；实际部署日期和运行检查结果见[部署记录](deployment_status.md)。
