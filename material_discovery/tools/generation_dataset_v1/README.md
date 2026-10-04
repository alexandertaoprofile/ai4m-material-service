# MatterGen 离线批量结构生成工具（历史实验）

本目录用于复现历史 MatterGen 批量生成和候选整理实验，不是当前 1115 的在线调用入口。现行 OMatG CSP/DNG 服务见[服务 README](../../README.md)。

## 1. 入口和辅助工具的分工

| 文件 | 用途 |
| --- | --- |
| `generate_dataset.py` | 从本地 MatterGen checkpoint 批量生成结构，整理组成/几何记录并标记重复。此入口不执行 MatterSim、ALIGNN 或 DFT。 |
| `summarize_mattersim_screening.py` | 汇总另外完成的 MatterSim 稳定性评价结果。 |
| `screen_shortlist_properties.py` | 读取已完成松弛的结构，使用 ALIGNN 预测预定义短名单的性质。 |
| `render_shortlist_structures.py` | 渲染预定义短名单结构。 |
| `publish_shortlist_assets.py` | 发布短名单资产，涉及对象存储写入，需单独授权并配置凭据。 |

辅助脚本保留原实验的候选选择和路径假设；它们不是面向任意请求的通用服务流程。结构生成、后续评价和公开发布是分别执行的步骤。

## 2. 输入与适用范围

`generate_dataset.py` 在已配置 MatterGen 的环境中运行，显式提供场景、模型类型、本地权重路径、checkpoint 轮次、元素体系、稳定性条件、样本数、批大小、随机种子和新输出目录。

| 场景参数 | 当前允许范围 |
| --- | --- |
| `aerospace_alloy` | 固定 Co-Cr-Fe-Mn-Ni 元素体系；原实验采用 HEA v2 的 epoch-18 权重。 |
| `chip_packaging` | Al-Si-B-N-O 元素空间内的子集；原实验使用本地官方条件模型副本。 |

场景名是历史实验标签，生成范围仍以上述代码限制为准。生成时传入的 `energy-above-hull` 是条件模型的输入，候选最终稳定性需要后续独立评价。

样本数必须为正且能被批大小整除，以保证请求生成数量。输出目录必须是新路径；脚本拒绝复用已有目录。它读取已有本地权重，不承担在线下载或训练任务。离线批量大小不受现行 1115 每阶段 10 个候选的接口限制。

## 3. 使用与输出

从 `material_discovery/` 目录查看完整参数：

```bash
python tools/generation_dataset_v1/generate_dataset.py --help
```

运行环境、本地模型版本和 `/data` 输出路径确认后再启动真实生成。每次产物包括：

- `manifest.json`：输入、模型位置、checkpoint、种子、数量和时间记录。
- `raw/`：原始生成文件；`cif/`：候选晶体结构文件。
- `processed/candidates.jsonl`、`candidates.csv`：组成、几何、场景和重复标记。
- `summary/`：候选质量与数量统计。

CIF 描述晶体中的晶胞和原子坐标；JSONL 每行保存一个候选记录。组成/结构哈希及近似重复标记用于复现实验，不作为新材料新颖性或制备成功的证明。

本次文档整理不重新生成数据、不执行后续性质计算，也不发布资产。复现时记录命令、权重版本和结果路径，保留原实验产物供比较。
