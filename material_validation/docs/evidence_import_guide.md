# W-14 证据导入说明

每个文件必须保留原始文件名、来源定位、生成条件和 SHA-256；不要用截图、转抄数值或无来源 CSV 代替原始证据。

```text
data/reference_cases/w14_phase_i/
├── evidence_manifest.json
├── raw/
│   ├── dft/              # set.000、type.raw、type_map.raw、结构/能量/力/应力数据
│   └── md/               # LAMMPS 输入、data、时间序列、原始输出与后处理输入
├── models/               # input.json、frozen_*.pb、训练日志、train/valid/test 指标
└── literature/           # 文献 PDF/许可允许的摘录、来源登记 CSV 或 JSON
```

建议为每一份文献或实验登记以下字段：材料身份与纯度/状态、温度、试验方法、性质、单位、数值或范围、DOI/标准号、页码/表号。只有这些字段齐备后，页面才应给出 MD 与实验/文献的正式相对误差。

W-14 的现有 LAMMPS 复现脚本和输出在迁入前目录中。导入时请一并保留其所引用的冻结势、实际 LAMMPS/DeepMD 版本、运行命令和后处理脚本，以便服务判断一条性质是 MD 直接输出、派生量还是工程估算。

当前服务的本机 `.env` 已将 `DeepMD-W-14-commercial-level` 配置为训练/势源，将 `DeepMD_W-14_commercial-level` 配置为 MD 验证源。两者以只读方式统一汇总，不能相互覆盖或删除。

## 前端图表映射

| 归档数据 | 页面资产 | 生成条件 |
|---|---|---|
| train/validation/test 的 DFT 与 MLIP 预测数组 | Energy、Force、Stress parity 与误差分布 | 数组、划分标签、单位齐备 |
| NPT/弹性/NVT 原始输出 | 温度—晶格参数、密度、模量、热膨胀、Cv 曲线 | LAMMPS 输入与原始输出可复算 |
| 文献/实验登记 | MD—实验/文献对标图及相对误差表 | 材料状态、温度、方法、来源定位可比 |

所有图表写入 `results/<taskid>/presentation/`，经既有任务资产路由或对象存储发布；PNG 仅在流式 Markdown 中嵌入，避免与 `MaterialsPNG` 事件重复渲染。
