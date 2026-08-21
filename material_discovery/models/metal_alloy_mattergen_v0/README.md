# metal_alloy_mattergen_v0

用于“新金属间化合物/有序合金结构发现”的 MatterGen 模型包。它与通用无机
发现模型并存；在完成训练和验收前，服务不得将它作为可用的生成后端。

## 包内容

- `model_manifest.json`：模型身份、基座、工件状态和验收门槛。
- `data/README.md`：训练数据来源、准入规则和落盘约定。
- `training/README.md`：从数据包到 checkpoint 的执行顺序。
- `checkpoints/`：本地保存最佳与最终 checkpoint，Git 不跟踪二进制权重。
- `outputs/`：训练日志、指标和临时产物，Git 不跟踪。

## 运行时约定

完成训练后，将 `MATTERGEN_ALLOY_MODEL_PATH` 指向本目录内通过验收的
checkpoint 目录。服务接入前必须读取 `model_manifest.json`，确认
`status` 为 `accepted`，并把数据清单哈希、配置和 checkpoint 哈希写入
任务 manifest。

当前状态为 `scaffolded`，不包含可调用模型权重。
