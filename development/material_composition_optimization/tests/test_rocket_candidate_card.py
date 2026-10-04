"""Focused customer-page checks without importing unrelated service integrations."""

from src.alloy_workflow.presentation import _embed_rocket_visuals, rocket_stainless_summary_block


def test_rocket_candidate_card_explains_composition_prediction_and_validation() -> None:
    result = {
        "mode": "short_time_tensile_screening",
        "screening_conditions": {
            "test_temperature_K": 293,
            "processing": {
                "solution_treatment_temperature_K": 1323,
                "solution_treatment_time_s": 3600,
                "quench": "water",
            },
        },
        "initial_candidates": [{
            "candidate_id": "RSS-001",
            "composition_wt_percent": {"Cr": 18.0, "Ni": 9.0, "N": 0.011, "Fe": 72.989},
            "short_time_tensile": {
                "yield_0p2_MPa": {"mean": 250, "screening_MAE": 16.2},
                "uts_MPa": {"mean": 600, "screening_MAE": 16.3},
                "elongation_pct": {"mean": 60, "screening_MAE": 3.29},
            },
            "applicability_domain": {"level": "inside", "nearest_training_composition_distance": 2.1},
        }],
    }
    page = rocket_stainless_summary_block(result)
    assert "### 5.2 优先候选" in page
    assert "### 奥氏体不锈钢｜本次优选配方" in page
    assert "| 铁（Fe） · 平衡组分 | 约 72.99% |" in page
    assert "| 铬（Cr） | 18% |" in page
    assert "| 氮（N） | 0.011% |" in page
    assert "293 K（约 20 °C）" in page
    assert "1323 K（约 1050 °C），保温 3600 s（1 h）；水淬" in page
    assert "| 0.2% 屈服强度（开始产生永久变形） | 250 MPa | 16.2 MPa |" in page
    assert "| 抗拉强度 UTS（最大承载） | 600 MPa | 16.3 MPa |" in page
    assert "| 断后延伸率（拉断前可伸长程度） | 60.0% | 3.29 个百分点 |" in page
    assert "不是这组候选的实测值或单点误差范围" in page
    assert "未列出的元素不表示已有实测零含量" in page
    assert "Cr18.0 Ni9.0" not in page

    assets = [
        {"name": name, "title": title, "description": "图表说明", "url": f"https://example.org/{name}.png"}
        for name, title in (
            ("rocket_screening_funnel", "候选筛选路径"),
            ("rocket_strength_ductility_tradeoff", "强度—延性取舍"),
            ("rocket_composition_comparison", "优先候选成分"),
        )
    ]
    rendered = _embed_rocket_visuals(page, assets)
    assert "{{VISUAL:" not in rendered
    assert rendered.count("![") == 3
    assert rendered.index("### 5.1 筛选过程") < rendered.index("### 5.2 优先候选")
    assert rendered.index("### 5.2 优先候选") < rendered.index("### 奥氏体不锈钢｜本次优选配方")
    assert rendered.index("### 奥氏体不锈钢｜本次优选配方") < rendered.index("#### 结论")
