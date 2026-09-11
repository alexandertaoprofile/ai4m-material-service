from src.alloy_workflow.contracts import (
    is_copper_hot_end_intent,
    is_chip_glass_intent,
    is_ni_hot_end_intent,
    is_perovskite_intent,
    is_reusable_rocket_stainless_intent,
    is_short_cf_intent,
    is_composite_material_request,
    requirement_plan,
    upstream_requirement,
)


STARSHIP_30X = (
    "星舰30X为单一 Fe-Cr-Ni-Mn-C-N 系奥氏体不锈钢，不含树脂、纤维、填料或其他复合相；"
    "以 Fe 为余量，围绕 Cr 18–20 wt.%、Ni 8–12 wt.%、Mn 0.5–2 wt.%、"
    "C≤0.03 wt.%、N 0.1–0.3 wt.% 进行候选配比筛选。"
)


def test_latest_direct_stainless_request_overrides_stale_composite_rejection_history():
    payload = {
        "idea": STARSHIP_30X,
        "history": [
            {"role": "user", "content": "针对星舰30X不锈钢做配比。"},
            {"role": "assistant", "content": "本服务仅适用于单一金属合金；包含树脂、纤维、填料或其他复合相时应使用复合材料专项流程。"},
        ],
    }
    text, keys = upstream_requirement(payload)
    assert keys == ["idea"]
    assert text == STARSHIP_30X
    assert not is_composite_material_request(text, {})
    effective, plan = requirement_plan(payload)
    assert effective["model_domain"] == "reusable_rocket_stainless"
    assert plan["template"] == "reusable_rocket_stainless_screening"


def test_role_labelled_history_uses_latest_user_message_not_prior_material_system():
    payload = {
        "messages": [
            {"role": "user", "content": "请针对 PETG 碳纤维复合耗材进行配比。"},
            {"role": "assistant", "content": "复合材料专项流程已识别。"},
            {"role": "user", "content": STARSHIP_30X},
        ]
    }
    text, _ = upstream_requirement(payload)
    assert text == STARSHIP_30X
    assert not is_composite_material_request(text, {})


def test_glass_core_substrate_alias_routes_without_needing_other_glass_keywords():
    assert is_chip_glass_intent("玻璃芯基板的配方设计", {})
    effective, plan = requirement_plan({"idea": "玻璃芯基板的配方设计"})
    assert effective["model_domain"] == "chip_glass_thermomechanical_family_v1"
    assert plan["template"] == "chip_glass_thermomechanical_local_screening"


def test_explicitly_excluded_material_domains_do_not_become_routing_signals():
    assert not is_chip_glass_intent("本方案不做玻璃芯基板，改做金属壳体。", {})
    assert not is_short_cf_intent("不考虑使用短碳纤维复合耗材，只评估单一金属。", {})
    assert not is_perovskite_intent("不采用钙钛矿体系，当前只讨论陶瓷。", {})
    assert not is_ni_hot_end_intent("不采用镍基高温合金，也不进入发动机热端路线。", {})
    assert not is_copper_hot_end_intent("排除 GRCop-84 与 CuCrZr 铜基热端材料。", {})
    assert not is_reusable_rocket_stainless_intent("不选 Starship 30X 或火箭不锈钢，改做其他结构。", {})


def test_non_negated_stainless_word_remains_an_active_material_signal():
    assert is_reusable_rocket_stainless_intent("针对 Starship 30X 奥氏体不锈钢壳体做配方筛选。", {})


def test_requested_specialist_domain_wins_over_excluded_material_terms():
    glass_text = "针对玻璃基板材料做一个配比方案，不包含碳纤维材料。"
    assert not is_composite_material_request(glass_text, {})
    glass_effective, _ = requirement_plan({"idea": glass_text})
    assert glass_effective["model_domain"] == "chip_glass_thermomechanical_family_v1"

    stainless_text = "针对星舰30X单一奥氏体不锈钢，排除树脂、纤维、填料和复合相，做元素配比筛选。"
    assert not is_composite_material_request(stainless_text, {})
    stainless_effective, _ = requirement_plan({"idea": stainless_text})
    assert stainless_effective["model_domain"] == "reusable_rocket_stainless"
