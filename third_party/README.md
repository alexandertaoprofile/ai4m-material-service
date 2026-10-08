# 第三方生成模型源码

`OMatG/` 保存 2026-10-08 从 `/data/third_party/OMatG` 导出的 OMatG 2.0.0 源码、推理配置和许可证，用于 1115 的晶体结构生成。

| 内容 | 保存方式 |
| --- | --- |
| OMatG 程序及配置 | 本目录，可按资源清单核对文件内容。 |
| CSP、DNG 权重及各自的训练配置 | [运行资源 Release](https://github.com/AI4SciFoundation/Material-Computation/releases/tag/runtime-20261008)。 |
| 原始 OMatG 训练数据 | 原服务器 `OMatG/omg/data/` 中的 LMDB；日常服务推理按请求构造临时输入。 |

上游项目为 [FERMat-ML/OMatG](https://github.com/FERMat-ML/OMatG)，许可证和作者信息随[LICENSE](OMatG/LICENSE)、[AUTHORS.md](OMatG/AUTHORS.md)保留。当前服务器目录没有 `.git`，本次源码版本使用逐文件 SHA256 固定，详见[资源清单](../resources/runtime-manifest.json)。

上游 README 保留完整研究说明，其中的训练命令需要另行取得对应数据集。服务迁移使用[本项目的安装与运行指南](../docs/reproduction.md)。
