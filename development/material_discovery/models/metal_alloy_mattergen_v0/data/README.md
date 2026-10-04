# 历史金属晶体训练数据与准入规则

本目录记录 `metal_alloy_mattergen_v0` 数据的来源、过滤和划分。用途是准备离线训练数据，不是每次 1115 用户请求的生成约束。模型包状态见[上级说明](../README.md)。

## 1. 已记录的数据状态

[dataset_manifest.json](dataset_manifest.json) 记录 2026-08-19 的数据准备版本，状态为 `ready_for_cache_conversion`，即可进入 MatterGen 训练缓存转换阶段：

| 项目 | 清单记录 |
| --- | --- |
| 原始 JARVIS 导出记录 | 66,603 条。 |
| 过滤后主池 | 10,295 条。 |
| 训练 / 验证 / 测试划分 | 8,238 / 1,034 / 1,023 条。 |
| 版本核对信息 | 原始文件和各划分 ID 清单的 SHA-256。 |

JARVIS 是材料计算数据来源，JARVIS ID 用于定位原始记录。以上数量是该历史清单的统计，不是已训练模型的能力或当前服务器文件存在性的保证；清单中的 `/tmp` 原始路径也不是长期部署资源，重建前需要取得实际导出文件。

## 2. 数据准入规则

1. 从 `material.jarvis_structure` 导出结构，保留记录 ID 和来源。
2. 解析并标准化晶体，以实际原子位点数判断规模，而不是仅依赖顶层 `atom_count` 字段。
3. 主池仅保留二元及以上、全金属、有序、实际位点不超过 20 的晶体；排除 Tc、Pm 及原子序数不低于 84 的元素。
4. 主池稳定性差值 `E_hull≤0.10 eV/atom`，其中 `≤0.05` 为稳定优先子集。`0.10–0.20` 如需研究，应另建探索/评测池，不混入当前主池。
5. 按结构匹配去重，并按化学体系分组划分训练/验证/测试集，减少相近结构跨集合造成的评估偏高。

`E_hull` 是候选相对参考相组合的能量差，每原子电子伏特（eV/atom）越低通常越有利。这些阈值是该训练数据包的准入规则；现行 OMatG 用户任务的稳定性参考线与硬性条件另见[服务说明](../../../README.md)。

## 3. 如何重建和核验

准备工具为服务目录中的 `tools/prepare_jarvis_metal_alloy_dataset.py`，需在已配置的 MatterGen 环境使用。先查看实际参数：

```bash
python tools/prepare_jarvis_metal_alloy_dataset.py --help
```

在 `material_discovery/` 目录执行上述命令。实际生成时用 `--expected-records` 绑定此次完整导出的精确行数；输入不完整时停止生成。`--allow-incomplete-input` 仅用于调试，产物会标为非最终数据。

新数据包需记录源文件版本及校验和、过滤数量/原因、每个划分的 ID 清单校验和和生成命令版本。大型 CSV 在外部数据目录管理，Git 保留清单；训练状态另由 [model_manifest.json](../model_manifest.json) 管理。
