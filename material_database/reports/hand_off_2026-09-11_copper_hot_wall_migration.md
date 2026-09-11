# 铜基热壁牌号证据迁移交接（2026-09-11）

## 目标与边界

将原先 1111 的已有牌号证据筛选迁入 1105 成熟材料目录；1111 仅保留铜基局部新配比优化。导入包只含来源可追溯的已有牌号、状态、温度和性质事实，不含任何模型预测或工程估算。

## 导入包

目录：`data/processed/material_core/2026-09-11_copper_hot_end_grade_evidence_v1/`

- 4 个材料身份：GRCop-42、GRCop-84、NARloy-Z、Cu-1Cr-0.1Zr；
- 15 条屈服强度点；
- 6 条 GRCop-84 导热曲线点，均为 NASA/CR-2000-210055 Eq. 17 的可复算发布公式节点，明确标为 `published_formula_recalculation`；
- 7 条 NARloy-Z 538 °C 低周疲劳应变—寿命点；
- 名义 wt.%、状态、原始报告定位及检索别名均独立保留。

来源为 NASA/CR-2000-210055、NASA/TM-2007-214663、NASA CR-132555、NASA/TM-2019-220318。CuCrZr 已有 Kemper 产品状态记录，因此 NASA 的 `Cu-1Cr-0.1Zr` 以实际牌号检索，未新增会造成状态混淆的裸 `CuCrZr` 别名。

## 重建与验证

```bash
cd /data/se42/alpha_project/material_service_hub/material_database
PYTHONPATH=. python scripts/import_copper_hot_end_evidence_v1.py \
  --output data/processed/material_core/2026-09-11_copper_hot_end_grade_evidence_v1
PYTHONPATH=. pytest -q tests/test_mature_material_service.py
```

最新回归：`65 passed, 4 subtests passed`。
