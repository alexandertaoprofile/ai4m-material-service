# 训练数据准入

本目录只放可复现的最终训练 CSV、数据清单和分割清单；大型 CSV 不提交 Git。

1. 从 `material.jarvis_structure` 导出原始结构，保留 JARVIS ID 和来源记录。
2. 重建并标准化晶体结构；以 `structure.elements` 的实际位点数过滤，不使用
   顶层 `atom_count` 作为唯一依据。
3. 首版仅保留二元及以上、全金属、有序、实际位点数不超过 20 的结构；排除
   `Tc`、`Pm` 和原子序数不低于 84 的元素。
4. `E_hull≤0.10 eV/atom` 为训练主池；`≤0.05` 标为稳定优先子集；
   `0.10–0.20` 只保留为独立探索/评测池。
5. 用结构匹配去重，并按化学体系整体划分 train/validation/test，防止原型泄漏。

每次生成数据包时创建 `dataset_manifest.json`，至少记录源导出哈希、过滤计数、
排除原因统计、每个 split 的 JARVIS ID 清单哈希和生成命令版本。

使用 `tools/prepare_jarvis_metal_alloy_dataset.py` 在 MatterGen 环境中生成。
调用时必须以 `--expected-records` 绑定该次完整导出的精确行数；它会在输入
少于此数时拒绝生成最终包。只有显式传入 `--allow-incomplete-input` 才可用于
调试，且输出会标记为非最终数据。
