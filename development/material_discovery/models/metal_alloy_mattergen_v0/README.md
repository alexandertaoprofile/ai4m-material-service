# 有序金属晶体 MatterGen 训练包（历史准备模块）

本目录是 `metal_alloy_mattergen_v0` 的数据与训练准备包，研究对象为金属间化合物及有序合金晶体：两种及以上金属以明确比例和位置构成的周期结构。

## 1. 当前状态与线上关系

以 [model_manifest.json](model_manifest.json) 为准，当前模型状态为 `scaffolded`：包结构和数据准入记录已建立，验收 checkpoint、训练配置和指标尚未填入。数据清单记录 10,295 条主池结构，但这不等于模型已经训练完成。

当前 1115 当前结构生成流程为 OMatG，见[服务 README](../../README.md)。旧 MatterGen 后端另有 HEA 条件微调适配路线；该路线与本目录的训练骨架是不同模型包，不能互相替代状态或验收记录。

## 2. 文件职责

| 位置 | 内容 |
| --- | --- |
| `model_manifest.json` | 模型名称、预训练模型、来源、模型文件校验值及验证状态。 |
| [data/README.md](data/README.md)、`data/dataset_manifest.json` | 数据来源、过滤规则、数据集划分和版本记录。 |
| [training/README.md](training/README.md) | 数据缓存、训练和独立验收的步骤。 |
| `checkpoints/` | 未来通过验收的本机权重存放位置；二进制不入 Git。 |
| `outputs/` | 训练日志、指标和临时输出文件；运行文件不入 Git。 |

checkpoint 是保存的模型参数；manifest 是记录数据和模型版本的清单；SHA-256 是用于确认文件身份的校验和。

## 3. 启用前的交接要求

1. 获取实际训练数据，并核对数据清单、来源与分割记录。
2. 完成训练，记录配置、软件版本及最佳 checkpoint 的校验和。
3. 在独立数据上核验组成、结构有效性、稳定性和目标体系覆盖。
4. 验收通过后将模型清单更新为 `accepted`，再单独审查后端适配和部署。

早期接入约定使用 `MATTERGEN_ALLOY_MODEL_PATH` 指向已验收权重目录；仅填写该路径并不会让当前 OMatG 生成流程加载此模型。实际启用时须核对调用代码，并把清单、配置及权重版本写入任务记录。当前 README 不提供上线或训练启动指令。
