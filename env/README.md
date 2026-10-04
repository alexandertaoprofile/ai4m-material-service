# 服务环境与外部计算资源

本目录保存环境定义和历史依赖快照，不是包含全部模型与依赖的安装包。以下说明对应 75 服务器当前分工；其他机器需按硬件、CUDA 和模型版本复核路径及依赖。

## 1. 为什么使用多个环境

Web 服务负责连接、需求解析和报告交付；OMatG、MatterSim、ALIGNN 等模型依赖不同的 Python、PyTorch 和图计算库。独立环境可以减少版本冲突，Web 进程通过数值执行器调用模型。

| 环节 | 当前服务器解释器或目录 | 用途 |
| --- | --- | --- |
| 主 Web 服务 | `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python` | HTTP/WebSocket、服务编排和报告。 |
| 1111 专项数值计算、1115 MatterSim | `/data/mamba/envs/mattergen-py310/bin/python` | 已部署数值依赖；名称沿用历史版本，不表示当前生成器仍是 MatterGen。 |
| 1115 OMatG | `/data/mamba/envs/omatg-py312/bin/python` | CSP/DNG 晶体生成。 |
| 1115 ALIGNN | `/home/ubuntu/miniconda3/envs/dsc2/bin/python` | 性质预测，并复用 `/data/mamba/envs/alignn-gpu-test/lib/python3.10/site-packages`。 |
| 1111 冻结优化运行时 | `/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python` | 调用已验收的专项优化包，见[1111说明](../material_composition_optimization/README.md)。 |

这些路径是现有部署记录，实际调用由各服务配置决定。首次迁移到其他机器时先核对外部资源，再配置解释器；日常更新服务代码直接复用已验收环境。

## 2. 仓库中的文件代表什么

| 文件 | 用途与状态 |
| --- | --- |
| `ai4m-service-py310.yml` | 主服务 Conda 环境定义；不是当前机器完整安装状态的逐包审计记录。 |
| `hea-surrogate-py310.yml` | HEA 专项计算独立环境的定义，供需要单独重建时参考。HEA 指高熵/多主元合金。 |
| `mattergen-py310.yml`、`mattergen-py310.requirements.txt` | 原 MatterGen/结构计算依赖记录；当前同名数值环境也承载 MatterSim 等任务。 |
| `alignn-gpu-test.conda.before.txt`、`alignn-gpu-test.freeze.before.txt` | 历史 ALIGNN 环境快照；当前 1115 解释器选择见上表。 |
| `mp-api-py311.requirements.txt` | Materials Project 数据访问子环境依赖，只有相应链路需要时使用。 |
| `organic-predict-py310.requirements.txt` | 历史有机性质预测依赖记录，不是 OMatG 或 PropMolFlow 的环境定义。 |

当前目录没有完整的 OMatG 专属环境锁定文件。新机器迁移时还需取得 OMatG 源码版本、检查点和已验收环境依赖记录，不能把历史 MatterGen 文件当作 OMatG 安装清单。现行资源配置见[1115配置样例](../material_discovery/.env.omatg.example)。

## 3. 新机器接手与当前机器检查

1. 确定要运行的服务，阅读对应 README 的外部资源表。
2. 获取该版本需要的模型源码、权重、数据清单和参考库。
3. 核对 Python、CUDA、PyTorch 等依赖组合；按实际目录配置解释器和模型路径。
4. 在服务自己的 `.env` 中配置端口、接口及对象存储凭据。`.env.example` 是无密钥样例。
5. 完成环境预检与回归测试，再安排真实任务联调和部署。

已有 75 服务器的 OMatG 只读检查方式：

```bash
cd /data/se42/worktrees/material_service_hub_release_20261004/material_discovery
/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python tools/preflight_omatg.py --env-file .env.omatg.example
```

预检检查已有资源，不安装、下载或启动生成。2026-10-04 的预检结果与测试环境记录见[版本交接](../docs/RELEASE_HANDOFF_20261004.md)。

## 4. 文件存放与历史后端

模型源码、权重、参考库、缓存及临时文件使用 `/data` 下的外部目录；生产密钥在本机 `.env` 中管理。环境文件不包含密钥，运行结果不放入本目录。

只有明确维护旧 MatterGen 后端时，才参考 `material_discovery/tools/setup_mattergen_env.sh` 和[旧后端手册](../material_discovery/docs/inorganic_new_material_service_guide.md)。该脚本可能安装依赖和准备源码，执行前须核对目标目录和磁盘空间。现行 1115 生成路线是 OMatG，部署与回退见[OMatG指南](../material_discovery/docs/OMATG_DEPLOYMENT.md)。
