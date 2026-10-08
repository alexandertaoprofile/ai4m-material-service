# 模型、数据库和验证资料

版本：`runtime-20261008`。下载位置：[公司仓库运行资源 Release](https://github.com/AI4SciFoundation/Material-Computation/releases/tag/runtime-20261008)。访问权限与公司私有仓库相同。

源码、小型配置及钙钛矿查找表随 Git 提供；权重、专项数据表、SQLite 和稳定性参考库通过 Release 交付。[runtime-manifest.json](runtime-manifest.json)记录每个文件的用途分组、原服务器位置、安装后的相对路径、大小和 SHA256。

## 按服务选资源

| 分组 | 用途 | 包含内容 |
| --- | --- | --- |
| `1105` | 已有材料查询 | 事务一致的 SQLite 快照及全部已核验增量材料目录；基础 CSV 已在服务目录。 |
| `1111` | 八类专项计算与正式配比优化 | 专项 joblib、合金/玻璃 Transformer 权重、候选组成表及参考数据。 |
| `1115` | 晶体生成源码 | OMatG 源码随 Git 提供，此分组用于核对源码。 |
| `1115-models` | 晶体生成、松弛和性质预测 | CSP、DNG、MatterSim 权重；五个 ALIGNN 性质模型 ZIP。 |
| `1115-reference` | 稳定性估算 | MP2020 修正参考库，分为八个文件下载，安装工具自动拼接并检查整体 SHA256。 |
| `1116` | W-14 参考案例核验 | DeepMD 冻结势、训练记录、整理的数据及 LAMMPS 输出。 |

1115 的五个 ALIGNN 模型对应带隙、体积模量、剪切模量、电子有效质量和空穴有效质量。介电常数模型另行准备；服务按实际已取得的模型显示结果。

## 下载与检查

先安装 GitHub CLI 并使用有公司仓库访问权限的账号登录。以下命令在仓库根目录运行，Python 工具只使用标准库。

```bash
gh auth login
python tools/runtime_resources.py list
python tools/runtime_resources.py download --cache /data/material-repro/downloads
python tools/runtime_resources.py install --cache /data/material-repro/downloads --destination /data/material-repro/resources
python tools/runtime_resources.py verify --destination /data/material-repro/resources
```

仅准备 1115 时，给 download/install/verify 三个命令增加 `--groups 1115 1115-models 1115-reference`；其余服务使用对应分组。重复下载会检查并复用已有文件，安装工具要求目标文件尚不存在，以保留本机已有版本。

约 3.85 GB 的参考库采用 512 MiB 分片。GitHub Release 每个文件需小于 2 GiB，分片也便于中断后续传各个文件。[GitHub Release 说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。

模型和 joblib 文件应从本仓库受信任的 Release 取得，并通过校验后加载。数据与第三方模型沿用原来的来源和使用要求；对外公开发布前另行确认授权。

安装后的环境、配置和启动步骤见[迁移指南](../docs/reproduction.md)。完整训练复现所需的数据、划分与实验设置见[研究资源交接清单](../docs/research_resources.md)。
