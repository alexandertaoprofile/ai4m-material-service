#!/usr/bin/env python3
"""Build the evidence-admitted tensile dataset and train the v0 reference baseline.

This is deliberately *not* a 30X model or a free-composition optimizer.  It is
limited to documented 301/304L reference heats and processing states, and is
packaged so that a later composition model can reuse its traceable labels.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/processed/reusable_rocket_stainless_reference_baseline/20260902_v0"
KSI_TO_MPA = 6.89476


def row(**values):
    values.setdefault("evidence_level", "A")
    values.setdefault("admission", "included_training")
    values.setdefault("specimen_type", "smooth parent-metal tensile coupon")
    values.setdefault("weld_state", "parent metal / no weld")
    return values


def build_rows() -> list[dict]:
    rows: list[dict] = []
    # Metals 2023, Table 1 and Table 2.  All values are as printed in the paper.
    comp_304l = dict(C_wt_pct=0.0217, Si_wt_pct=0.375, Mn_wt_pct=1.432,
                     P_wt_pct=0.0338, S_wt_pct=0.0032, Cr_wt_pct=18.121,
                     Ni_wt_pct=8.052, Mo_wt_pct=0.134, N_wt_pct=0.0378,
                     Cu_wt_pct=0.378)
    for t_k, ys, uts, elong in [(300, 258.0, 724.9, 75.9),
                                (77, 856.0, 2172.0, 35.7),
                                (20, 1059.0, 2433.4, 32.6)]:
        rows.append(row(
            record_id=f"M2023_304L_{t_k}K", alloy_reference="304L",
            heat_id="not_reported", material_state="hot rolled plate; solution annealed >=1040 C",
            forming_method="solution annealed", forming_stress_ksi=None,
            forming_strain_pct=None, stress_relief="not reported", orientation="longitudinal / rolling direction",
            thickness_mm=13.0, test_temperature_K=t_k, yield_0p2_MPa=ys,
            uts_MPa=uts, elongation_pct=elong,
            source_file="Metals_2023_13_1774_304L_cryo_tensile_fracture.pdf",
            source_locator="Table 1 (PDF p. 3) and Table 2 (PDF p. 7)",
            source_doi="10.3390/met13101774", **comp_304l))

    # NASA TN D-3445 Table II and Table III.  The table reports ksi and F;
    # temperatures are converted exactly using (F-32)*5/9+273.15.
    comp_40226 = dict(C_wt_pct=0.006, Si_wt_pct=None, Mn_wt_pct=0.01,
                      P_wt_pct=0.032, S_wt_pct=0.006, Cr_wt_pct=18.90,
                      Ni_wt_pct=7.65, N_wt_pct=0.015, O_wt_pct=0.010)
    comp_58044 = dict(C_wt_pct=0.015, Si_wt_pct=None, Mn_wt_pct=0.41,
                      P_wt_pct=0.027, S_wt_pct=None, Cr_wt_pct=17.19,
                      Ni_wt_pct=7.53, N_wt_pct=0.045, O_wt_pct=None)

    def add_301(heat, comp, thickness, form, form_stress, form_strain, treatment,
                orientation, measurements):
        for f, uts_ksi, ys_ksi, elong in measurements:
            t_k = round((f - 32) * 5 / 9 + 273.15, 2)
            rows.append(row(
                record_id=f"NASA_D3445_{heat}_{form}_{treatment}_{orientation}_{f}F".replace(" ", "_"),
                alloy_reference="301-type stainless", heat_id=heat,
                material_state=f"{form}; forming stress {form_stress} ksi; {treatment}",
                forming_method=form, forming_stress_ksi=form_stress,
                forming_strain_pct=form_strain, stress_relief=treatment,
                orientation=orientation, thickness_mm=thickness * 25.4,
                test_temperature_K=t_k, yield_0p2_MPa=round(ys_ksi * KSI_TO_MPA, 3),
                uts_MPa=round(uts_ksi * KSI_TO_MPA, 3), elongation_pct=elong,
                source_file="NASA_TN_D-3445_1966_special_301_cryoformed_toughness.pdf",
                source_locator="Table II (PDF p. 6) and Table III (PDF p. 13)",
                source_doi="NASA TN D-3445", **comp))

    add_301("58044", comp_58044, 0.029, "biaxial cryoformed", 267, 14.8, "none", "hoop",
            [(70, 222, 186, None), (-320, 304, 241, None), (-423, 332, 267, None)])
    add_301("58044", comp_58044, 0.029, "biaxial cryoformed", 267, 14.8, "none", "axial",
            [(70, 224, 168, None), (-320, 304, 213, None), (-423, 310, 232, None)])
    add_301("40226", comp_40226, 0.036, "biaxial cryoformed", 277, 7.1, "none", "hoop",
            [(70, 196, 180, 6), (-320, 272, 244, 14), (-423, 320, 289, 14)])
    add_301("40226", comp_40226, 0.036, "biaxial cryoformed", 277, 7.1, "none", "axial",
            [(70, 202, 176, 5), (-320, 278, 237, 9), (-423, 326, 279, 4)])
    add_301("40226", comp_40226, 0.036, "biaxial cryoformed", 277, 7.1, "790 F for 20 h", "hoop",
            [(70, 231, 225, 4), (-320, 291, 279, 9), (-423, 335, 323, 2)])
    add_301("40226", comp_40226, 0.036, "biaxial cryoformed", 277, 7.1, "790 F for 20 h", "axial",
            [(70, 235, 221, 4), (-320, 297, 285, 4), (-423, 345, 328, 4)])

    # NASA TN D-5413, Table I and Table III. The source calls this 60% cold
    # reduced; its process narrative records 64% reduction after the final anneal.
    comp_348524 = dict(C_wt_pct=0.11, Si_wt_pct=0.60, Mn_wt_pct=1.44,
                       P_wt_pct=0.23, S_wt_pct=0.009, Cr_wt_pct=17.26,
                       Ni_wt_pct=7.10)
    d5413 = {
        294: [(0, 206, 239), (11, 215, 236), (79, 203, 240), (90, 212, 246)],
        77: [(0, 230, 320), (11, 239, 329), (28, 240, 330), (45, 244, 312),
             (62, 250, 329), (79, 246, 328), (90, 255, 331)],
        20: [(0, 258, 337), (11, 265, 322), (28, 266, 335), (45, 266, 331),
             (62, 283, 340), (79, 278, 346), (90, 293, 353)],
    }
    for t_k, entries in d5413.items():
        for angle, ys_ksi, uts_ksi in entries:
            rows.append(row(
                record_id=f"NASA_D5413_348524_{t_k}K_{angle}deg",
                alloy_reference="301 stainless", heat_id="348524",
                material_state="flat sheet; 64% final cold reduction after anneal",
                forming_method="cold reduced sheet", forming_stress_ksi=None,
                forming_strain_pct=64.0,
                stress_relief="final state as received; no post-form stress relief reported",
                orientation=f"{angle} deg from rolling direction", thickness_mm=0.022 * 25.4,
                test_temperature_K=t_k, yield_0p2_MPa=round(ys_ksi * KSI_TO_MPA, 3),
                uts_MPa=round(uts_ksi * KSI_TO_MPA, 3), elongation_pct=None,
                source_file="NASA_19690026310_301_cryo_fracture.pdf",
                source_locator="Table I (PDF p. 5) and Table III (PDF pp. 14–15)",
                source_doi="NASA TN D-5413", **comp_348524))
    return rows


def make_model(data: pd.DataFrame, target: str) -> Pipeline:
    categorical = ["alloy_reference", "heat_id", "forming_method", "stress_relief", "orientation"]
    numeric = ["forming_stress_ksi", "forming_strain_pct", "thickness_mm", "test_temperature_K"]
    transform = ColumnTransformer([
        ("categorical", Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]), categorical),
        ("numeric", SimpleImputer(strategy="median"), numeric),
    ])
    model = Pipeline([
        ("features", transform),
        ("regressor", RandomForestRegressor(n_estimators=400, random_state=20260902,
                                               min_samples_leaf=1, n_jobs=-1)),
    ])
    model.fit(data[categorical + numeric], data[target])
    return model


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = pd.DataFrame(build_rows()).sort_values("record_id").reset_index(drop=True)
    data.to_csv(OUT / "admitted_tensile_observations.csv", index=False)

    models = {}
    for target in ["yield_0p2_MPa", "uts_MPa"]:
        models[target] = make_model(data[data[target].notna()].copy(), target)
    # Elongation has 15 labels. It is retained as a narrow reference baseline,
    # not as a ductility/toughness safety model.
    models["elongation_pct"] = make_model(data[data["elongation_pct"].notna()].copy(), "elongation_pct")
    joblib.dump(models, OUT / "reference_tensile_baseline_v0.joblib")

    summary = {
        "service_name": "可回收火箭不锈钢配方设计",
        "internal_scope": "低温奥氏体不锈钢承压壳体",
        "artifact": "reference_tensile_baseline_v0",
        "trained_targets": {"yield_0p2_MPa": int(data.yield_0p2_MPa.notna().sum()),
                            "uts_MPa": int(data.uts_MPa.notna().sum()),
                            "elongation_pct": int(data.elongation_pct.notna().sum())},
        "independent_heats_or_material_lots": ["304L/not reported", "301-type/40226", "301-type/58044", "301/348524"],
        "allowed_use": "只用于已收录牌号/炉号/工艺状态及其相邻测试温度的参考筛选与补数排序。",
        "prohibited_use": [
            "不得命名、宣称或输出为 30X 性能预测。",
            "不得对未知化学成分、未知热处理或未知焊接状态进行配方优化外推。",
            "不得以拉伸标签推导断裂韧性、疲劳、焊接性能或 LOX 相容性。",
        ],
        "validation_status": "未报告泛化精度：仅 3 个独立炉号/材料批次，任何随机切分分数都会发生同批次泄漏。",
        "next_training_gate": "至少 30 个独立的不锈钢成分—状态组合，且每个组合保留成分、工艺、厚度、焊接状态、温度与可追溯拉伸标签。",
        "sources": [
            "Metals 2023, 13, 1774, Tables 1–2; DOI 10.3390/met13101774",
            "NASA TN D-3445 (1966), Tables II–III",
            "NASA TN D-5413 (1969), Tables I and III",
        ],
    }
    (OUT / "model_card.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text("""# 可复用火箭不锈钢配方优化：v0 参考拉伸基线\n\n这是一个已训练的**参考状态**模型，而非 30X 模型或自由配方优化器。\n\n- 样本：21 条可追溯的 301-type / 304L 平滑母材拉伸记录；屈服和 UTS 各 21 标签，延伸率 15 标签。\n- 输入：牌号/炉号、低温成形状态、成形应力和应变、应力消除、取向、厚度、测试温度。\n- 输出：0.2% 屈服、UTS、延伸率的参考估计。\n- 硬边界：只允许用于已收录条件的比较和补数优先级；不输出未知化学成分的配方建议，不预测 30X，也不外推疲劳、断裂韧性、焊接或 LOX 相容性。\n\n`admitted_tensile_observations.csv` 中每行保存原始 PDF 文件名、表号和材料状态。\n""", encoding="utf-8")
    print(json.dumps({"rows": len(data), "output": str(OUT), "targets": summary["trained_targets"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
