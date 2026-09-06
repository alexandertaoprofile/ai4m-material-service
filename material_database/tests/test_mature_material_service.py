from __future__ import annotations

import asyncio
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from src.team_config import MaterialMature


ROOT = Path(__file__).resolve().parents[1]


def _payload(taskid: str) -> dict:
    return {
        "taskid": taskid,
        "idea": "查询 IN718 在常温下的材料性质",
        "mature_material": {
            "material_queries": ["IN718"],
            "service_temperature_C": 21,
            "property_constraints": [
                {"property": "density", "operator": "<=", "value": 8300, "unit": "kg/m³"}
            ],
        },
    }


def _fluid_payload(taskid: str) -> dict:
    return {
        "taskid": taskid,
        "idea": "需要一款室温低黏度的导电润滑油，电导率不低于 0.1 S/m。",
        "mature_material": {
            "fluid_initial_screen": {
                "conditions": {"temperature_k": {"min": 293.15, "max": 303.15}},
                "property_constraints": [
                    {"name": "conductivity", "operator": ">=", "value": 0.1, "unit": "S/m"},
                    {"name": "dynamic_viscosity", "operator": ">=", "value": 130, "unit": "mPa*s"},
                    {"name": "dynamic_viscosity", "operator": "<=", "value": 150, "unit": "mPa*s"},
                ],
                "evidence_policy": {"composition": "include_flagged", "manual_review": "include_flagged"},
                "limit": 20,
            },
        },
    }


class _FakeWebSocket:
    def __init__(self, payload: dict):
        self.payload = payload
        self.client = None
        self.events: list[tuple[str, object]] = []
        self.closed = False

    async def accept(self) -> None:
        self.events.append(("accept", None))

    async def receive(self) -> dict:
        return {"type": "websocket.receive", "text": json.dumps(self.payload, ensure_ascii=False)}

    async def send_text(self, value: str) -> None:
        self.events.append(("text", value))

    async def send_json(self, value: dict) -> None:
        self.events.append(("json", value))

    async def close(self) -> None:
        self.closed = True


class MatureMaterialServiceTest(unittest.TestCase):
    def _service(self, results_root: Path) -> MaterialMature:
        return MaterialMature(
            catalog_root=ROOT / "data" / "processed",
            raw_data_root=ROOT / "data",
            results_root=results_root,
            service_name="mature-material",
        )

    def test_customer_report_contract_is_shared_by_catalogue_and_fluid_paths(self) -> None:
        expected = (
            "## 1. 需求与已知工况",
            "## 2. 本轮筛选/比较口径",
            "## 3. 证据覆盖与候选核验",
            "## 4. 结论",
            "## 5. 材料性质汇总",
        )
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            catalogue = asyncio.run(service.run(service.contract(_payload("unit-shared-report-catalogue"))))
            fluid = asyncio.run(service.run(service.contract(_fluid_payload("unit-shared-report-fluid"))))

        for result in (catalogue, fluid):
            report = service.summary(result)
            positions = [report.index(heading) for heading in expected]
            self.assertEqual(positions, sorted(positions))

    def test_orchestrator_returns_traceable_catalogue_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract(_payload("unit-in718"))
            result = asyncio.run(service.run(contract))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["workflow_kind"], "mature_material_catalogue_initial_screen")
        self.assertEqual(result["results"][0]["material"]["material_id"], "MAT-IN718")
        self.assertTrue(result["results"][0]["eligible"])
        self.assertEqual(result["name_resolution"][0]["status"], "matched")
        self.assertEqual(result["screening"]["summary"]["candidates_evaluated"], 1)
        self.assertEqual(result["screening"]["summary"]["eligible_candidates"], 1)

    def test_catalogue_screening_presentation_uses_funnel_and_distribution_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract(_payload("unit-catalogue-screen-presentation"))))
            summary = service.summary(result)
            assets = service.render_assets(result)

        self.assertIn("筛选漏斗", summary)
        self.assertIn("约束证据状态", summary)
        self.assertIn("候选核验", summary)
        self.assertEqual([asset["name"] for asset in assets], ["evidence_funnel", "property_comparison"])
        self.assertEqual(assets[0]["title"], "材料筛选漏斗")
        self.assertIn("筛选边界", assets[1]["description"])

    def test_1101_material_core_bundle_is_queryable_without_replacing_curated_alloy_baselines(self) -> None:
        payload = {
            "taskid": "unit-1101-material-core",
            "idea": "核验 Al0.25 Co1 Fe1 Ni1 高熵合金的屈服强度",
            "mature_material": {
                "material_queries": ["Al0.25 Co1 Fe1 Ni1"],
                "service_temperature_C": 25,
                "property_constraints": [
                    {"property": "yield_strength", "operator": ">=", "value": 150, "unit": "MPa"},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertEqual(result["data_status"]["outcome"], "catalog_matched")
        candidate = result["results"][0]
        self.assertEqual(candidate["material"]["display_name"], "Al0.25 Co1 Fe1 Ni1")
        self.assertEqual(candidate["material"]["data_role"], "1101 material-core evidence")
        self.assertTrue(candidate["eligible"])
        self.assertEqual(candidate["evidence"][0]["observed"]["value"], 158.0)

    def test_high_temperature_bundle_is_queryable_with_ultimate_tensile_strength_alias(self) -> None:
        payload = {
            "taskid": "unit-1101-high-temperature",
            "idea": "核验 HAYNES 556 在 649°C 的抗拉强度",
            "mature_material": {
                "material_queries": ["UNS R30556"],
                "service_temperature_C": 649,
                "property_constraints": [
                    {"property": "抗拉强度", "operator": ">=", "value": 600, "unit": "MPa"},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertEqual(result["data_status"]["outcome"], "catalog_matched")
        candidate = result["results"][0]
        self.assertEqual(candidate["material"]["material_id"], "MAT-1101-HT-H556")
        self.assertTrue(candidate["eligible"])
        self.assertEqual(candidate["evidence"][0]["observed"]["value"], 601.0)
        self.assertEqual(candidate["evidence"][0]["observed"]["coverage"], "measured_exact")
        summary = service.summary(result)
        self.assertIn("测试温度 649 °C", summary)
        self.assertNotIn("| 抗拉强度 | 922.15 K", summary)

    def test_broad_nickel_superalloy_scope_expands_to_traceable_high_temperature_families(self) -> None:
        """A user-facing broad family must not be defeated by source-family granularity."""
        payload = {
            "taskid": "unit-broad-nickel-high-temperature-scope",
            "idea": "筛选 649°C 使用的镍基高温合金，屈服强度不低于 300 MPa。",
            "mature_material": {
                "material_families": ["镍基高温合金"],
                "service_temperature_C": 649,
                "property_constraints": [{
                    "property": "yield_strength", "operator": ">=", "value": 300, "unit": "MPa",
                }],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertGreater(result["screening"]["summary"]["candidates_evaluated"], 0)
        self.assertTrue(result["results"])
        self.assertTrue(all(
            item["material"]["material_id"].startswith("MAT-1101-HT-")
            and "nickel" in item["material"].get("family", "").casefold()
            for item in result["results"]
        ))

    def test_oxidation_requirement_is_visible_as_a_gap_not_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract({
                "taskid": "unit-high-temperature-oxidation-gap",
                "idea": "筛选镍基高温合金，抗氧化性要好。",
                "mature_material": {"material_families": ["镍基高温合金"]},
            })
            result = asyncio.run(service.run(contract))

        self.assertEqual(contract["preference_goals"], [{"property": "oxidation_resistance", "direction": "maximize"}])
        self.assertFalse(result["results"])
        self.assertTrue(result["preference_data_gaps"])
        self.assertTrue(all(
            item["missing_properties"] == ["oxidation_resistance"]
            for item in result["preference_data_gaps"]
        ))

    def test_reviewed_haynes_718_solution_annealed_tensile_table_is_queryable(self) -> None:
        payload = {
            "taskid": "unit-haynes-718-room-temperature",
            "idea": "核验 HAYNES 718 在室温下的抗拉强度",
            "mature_material": {
                "material_queries": ["HAYNES 718"],
                "property_constraints": [
                    {"property": "抗拉强度", "operator": ">=", "value": 850, "unit": "MPa"},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract(payload)))

        candidate = result["results"][0]
        self.assertEqual(candidate["material"]["material_id"], "MAT-1101-HT-H718")
        self.assertTrue(candidate["eligible"])
        self.assertEqual(candidate["evidence"][0]["observed"]["value"], 871.0)
        summary = service.summary(result)
        self.assertIn("固溶退火态；室温测试", summary)
        self.assertIn("718 technical brochure；表 7，第 7 页", summary)
        self.assertNotIn("745317870285-table-0007", summary)

    def test_reviewed_thermal_batch_preserves_temperature_specific_conductivity(self) -> None:
        payload = {
            "taskid": "unit-in625-thermal-batch",
            "idea": "核验 INCONEL 625 在 20°C 的导热系数",
            "mature_material": {
                "material_queries": ["INCONEL 625"],
                "service_temperature_C": 20,
                "property_constraints": [
                    {"property": "导热系数", "operator": ">=", "value": 9.5, "unit": "W/(m·K)"},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract(payload)))

        evidence = result["results"][0]["evidence"][0]
        self.assertEqual(evidence["status"], "pass")
        self.assertEqual(evidence["observed"]["coverage"], "nearest_measured")
        self.assertEqual(evidence["observed"]["temperature_K"], 294.15)
        self.assertEqual(evidence["observed"]["value"], 9.8)

    def test_c276_physical_property_table_is_available_as_separate_traceable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-c276-physical-properties",
                "idea": "查看 INCONEL C-276 的导热与弹性数据",
                "mature_material": {"material_queries": ["INCONEL C-276"]},
            })))

        properties = result["results"][0]["available_properties"]
        c276_points = [item for item in properties if item.get("source", {}).get("source_id") == "5830e84da253"]
        self.assertTrue(any(item["property"] == "thermal_conductivity" for item in c276_points))
        self.assertTrue(any(item["property"] == "youngs_modulus" for item in c276_points))
        self.assertTrue(any(item["property"] == "thermal_expansion_coefficient" for item in c276_points))

    def test_n06230_import_excludes_adjacent_figure_series(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-n06230-cte",
                "idea": "核验 INCONEL N06230 在约 200°C 的线膨胀系数",
                "mature_material": {
                    "material_queries": ["UNS N06230"],
                    "service_temperature_C": 204.44,
                    "property_constraints": [{
                        "property": "thermal_expansion_coefficient", "operator": "<=", "value": 13.0, "unit": "ppm/K",
                    }],
                },
            })))

        candidate = result["results"][0]
        evidence = candidate["evidence"][0]
        self.assertEqual(candidate["material"]["material_id"], "MAT-1101-HT-INN06230")
        self.assertTrue(candidate["eligible"])
        self.assertEqual(evidence["observed"]["value"], 12.78)
        n06230_points = [item for item in candidate["available_properties"] if item.get("source", {}).get("source_id") == "99a780063c24"]
        self.assertEqual(len(n06230_points), 15)

    def test_unconstrained_lookup_does_not_claim_performance_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = _payload("unit-unconstrained")
            payload["mature_material"].pop("property_constraints")
            result = asyncio.run(service.run(service.contract(payload)))

        conclusion = service.summary(result)
        self.assertEqual(result["screening"]["strategy"]["mode"], "catalogue_index")
        self.assertEqual(result["data_status"]["outcome"], "catalog_matched")
        self.assertIn("材料索引核验", conclusion)
        self.assertIn("目录证据覆盖", conclusion)
        self.assertNotIn("满足当前可比较的性质条件", conclusion)

    def test_6061_t6_data_card_keeps_missing_catalogue_values_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-6061-reference-card",
                "idea": "查看 Al 6061-T6 的材料数据",
                "mature_material": {"material_queries": ["Al 6061-T6"]},
            })))

        summary = service.summary(result)
        self.assertIn("4 K：5.3474 W/(m·K)；300 K：155.32 W/(m·K)", summary)
        self.assertIn("测量温度 4–300 K", summary)
        self.assertNotIn("工程估算", summary)

    def test_chinese_grade_lookup_uses_catalogue_index_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-6061-chinese-index",
                "idea": "寻找一下铝合金6061",
            })))

        self.assertEqual(result["constraints"]["material_queries"], ["铝合金6061"])
        self.assertEqual(result["screening"]["strategy"]["mode"], "catalogue_index")
        # The alias is deliberately resolved at catalogue query time, so the
        # input remains auditable while the customer gets a direct record.
        self.assertEqual(result["results"][0]["material"]["material_id"], "MAT-AL6061-T6")

    def test_single_alloy_candidate_renders_melting_temperature_chart(self) -> None:
        payload = _payload("unit-single-alloy-chart")
        payload["mature_material"].pop("property_constraints")
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract(payload)))
            assets = service.render_assets(result)
            chart_exists = Path(assets[0]["local_path"]).is_file() if assets else False

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["name"], "melting_temperature_interval")
        self.assertEqual(assets[0]["title"], "候选合金熔化温度区间")
        self.assertTrue(chart_exists)

    def test_unconstrained_filament_lookup_renders_shared_property_chart(self) -> None:
        payload = {
            "taskid": "unit-filament-default-chart",
            "mature_material": {"material_queries": ["PETG", "PLA"]},
        }
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract(payload)))
            assets = service.render_assets(result)
            chart_exists = Path(assets[0]["local_path"]).is_file() if assets else False

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["name"], "default_density_comparison")
        self.assertEqual(assets[0]["title"], "候选材料密度对比")
        self.assertTrue(chart_exists)

    def test_long_history_does_not_promote_stale_filament_aliases(self) -> None:
        payload = {
            "taskid": "unit-stale-alias",
            "idea": ("历史分支曾比较 PLA、ASA 和 PETG。\n" * 120) + "请继续整理本轮材料需求。",
        }
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract(payload)
            result = asyncio.run(service.run(contract))

        self.assertEqual(contract["material_queries"], [])
        self.assertEqual(contract["alias_extraction_text"], "")
        self.assertFalse(result["results"])

    def test_catalogue_miss_suggests_literature_without_llm_material_advice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "unit-catalogue-miss",
                "idea": "核验未入库商品牌号 ExampleSeal-X9 的性能数据",
                "mature_material": {"material_queries": ["ExampleSeal-X9"]},
            }
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["data_status"]["outcome"], "needs_literature_screening")
        self.assertFalse(result["results"])
        self.assertNotIn("llm_fallback", result)
        self.assertNotIn("recommendation", result)
        self.assertIn("建议进入文献筛选", service.summary(result))

    def test_open_metal_selection_starts_with_traceable_catalogue_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-open-metal-selection",
                "idea": "帮我挑选一款成熟金属材料",
            })))

        summary = service.summary(result)
        self.assertEqual(result["workflow_kind"], "mature_material_catalogue_initial_screen")
        self.assertEqual(result["data_status"]["outcome"], "catalogue_guided_start")
        self.assertTrue(result["results"])
        self.assertEqual(result["screening"]["strategy"]["mode"], "criteria_collection")
        self.assertEqual(result["screening"]["next_action"], "continue_guided_discovery")
        self.assertIn("先从这几种已收录材料开始看", summary)
        self.assertIn("Al 6061-T6", summary)
        self.assertNotIn("当前目录尚无可作为材料事实展示的证据卡", summary)

    def test_under_specified_structure_request_gets_routes_before_hard_screening(self) -> None:
        """A sparse structural brief should be actionable, not a refusal page."""
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-structure-route-before-screening",
                "idea": "碳纤维主梁、金属连接件及轻木/泡沫蒙皮混合体系，检索强度、密度及耐环境数据并验证方案可行性。",
            })))

        summary = service.summary(result)
        self.assertEqual(result["data_status"]["outcome"], "catalogue_guided_start")
        self.assertIn("主梁起步候选", summary)
        self.assertIn("连接件起步候选", summary)
        self.assertIn("Hexcel IM7/8552", summary)
        self.assertIn("InstaVoxel 7075-T6 铝合金", summary)
        self.assertIn("不需要先给出性能指标", summary)
        self.assertNotIn("当前目录尚无可作为材料事实展示的证据卡", summary)

    def test_contextual_material_mentions_are_shown_without_a_followup_request(self) -> None:
        """A gateway may send the useful material context outside the user sentence."""
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-contextual-material-mentions",
                "idea": "请先整理方案。",
                "mature_material": {
                    "selection_context": {
                        "application": "轻量化结构方案",
                        "component": "碳纤维主梁与金属连接件",
                    },
                },
            })))

        summary = service.summary(result)
        self.assertEqual(result["data_status"]["outcome"], "catalogue_guided_start")
        self.assertIn("Hexcel IM7/8552", summary)
        self.assertIn("InstaVoxel 7075-T6 铝合金", summary)
        self.assertIn("你可以直接查看", result["data_status"]["message"])

    def test_catalogue_screening_strategy_scales_with_stated_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            single = service.contract({
                "taskid": "unit-one-dimension",
                "mature_material": {"material_families": ["nickel-molybdenum-chromium alloy"]},
            })
            paired = service.contract({
                "taskid": "unit-two-dimensions",
                "mature_material": {
                    "material_families": ["nickel-molybdenum-chromium alloy"],
                    "property_constraints": [{"property": "抗拉强度", "operator": ">=", "value": 600, "unit": "MPa"}],
                },
            })
            strict = service.contract({
                "taskid": "unit-three-dimensions",
                "mature_material": {
                    "material_families": ["nickel-molybdenum-chromium alloy"],
                    "temperature_C": 649,
                    "property_constraints": [{"property": "抗拉强度", "operator": ">=", "value": 600, "unit": "MPa"}],
                },
            })

        self.assertEqual(single["screening_strategy"]["mode"], "evidence_landscape")
        self.assertEqual(paired["screening_strategy"]["mode"], "cross_filter")
        self.assertEqual(strict["screening_strategy"]["mode"], "strict_evidence_screen")

    def test_natural_language_numeric_thresholds_are_constraints_not_material_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract({
                "taskid": "unit-natural-thresholds",
                "idea": "现在从材料库中找导热率≥100 W/(m·K); 屈服强度≥600 MPa的材料",
            })
            result = asyncio.run(service.run(contract))

        self.assertEqual(contract["material_queries"], [])
        self.assertEqual(contract["property_constraints"], [
            {"property": "thermal_conductivity", "operator": ">=", "value": 100.0, "unit": "W/(m·K)"},
            {"property": "yield_strength", "operator": ">=", "value": 600.0, "unit": "MPa"},
        ])
        self.assertEqual(contract["screening_strategy"]["mode"], "cross_filter")
        self.assertEqual(contract["screening_strategy"]["property_target_count"], 2)
        self.assertFalse(any(item["input"] == "MPa" for item in result["name_resolution"]))
        self.assertEqual(result["data_status"]["outcome"], "catalogue_no_eligible_candidates")
        self.assertTrue(result["results"])
        self.assertEqual(result["screening"]["summary"]["eligible_candidates"], 0)
        self.assertIn("thermal_conductivity", result["screening"]["summary"]["constraint_status_counts"])
        self.assertIn("暂未找到能同时满足全部条件", service.summary(result))

    def test_natural_language_property_ranges_expand_to_two_bounds_each(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract({
                "taskid": "unit-natural-ranges",
                "idea": "上一步总结：接下来需要进行执行的任务:调用材料筛选计算，输入屈服强度600-800MPa及导热率300-350W/(m·K)的约束条件执行目录匹配。",
            })
            result = asyncio.run(service.run(contract))

        self.assertEqual(contract["property_constraints"], [
            {"property": "yield_strength", "operator": ">=", "value": 600.0, "unit": "MPa"},
            {"property": "yield_strength", "operator": "<=", "value": 800.0, "unit": "MPa"},
            {"property": "thermal_conductivity", "operator": ">=", "value": 300.0, "unit": "W/(m·K)"},
            {"property": "thermal_conductivity", "operator": "<=", "value": 350.0, "unit": "W/(m·K)"},
        ])
        self.assertEqual(contract["screening_strategy"]["mode"], "cross_filter")
        self.assertEqual(contract["screening_strategy"]["property_target_count"], 2)
        self.assertEqual(result["data_status"]["outcome"], "catalogue_no_eligible_candidates")

    def test_execution_summary_retains_peek_and_thermal_interface_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract({
                "taskid": "unit-peek-summary-thresholds",
                "idea": (
                    "上一步总结：材料检索完成。接下来需要进行执行的任务:调用材料筛选计算团队，"
                    "基于PEEK/碳纤维复合材料体系及关键性能阈值（导热≥10 \\text{W/(m·K)}，"
                    "层间结合力≥20 MPa），检索成熟商业目录。"
                ),
            })
            result = asyncio.run(service.run(contract))

        self.assertEqual(contract["material_queries"], ["PEEK"])
        self.assertEqual(contract["property_constraints"], [
            {"property": "thermal_conductivity", "operator": ">=", "value": 10.0, "unit": "W/(m·K)"},
            {"property": "interfacial_bond_strength", "operator": ">=", "value": 20.0, "unit": "MPa"},
        ])
        self.assertNotEqual(result["data_status"]["outcome"], "needs_screening_criteria")

    def test_common_ceek_typo_is_canonically_resolved_to_peek(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract({
                "taskid": "unit-ceek-typo",
                "idea": "帮我查询一下CEEK材料的性质",
            })
            result = asyncio.run(service.run(contract))

        self.assertEqual(contract["material_queries"], ["PEEK"])
        self.assertEqual(result["data_status"]["outcome"], "catalog_matched")
        self.assertEqual(result["results"][0]["material"]["material_id"], "MAT-3D4MAKERS-PEEK")

    def test_current_question_marker_overrides_long_gateway_history_for_peek_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract({
                "taskid": "unit-current-question-peek",
                "idea": "=== 对话历史 ===\n" + "旧内容 PLA ASA PETG。\n" * 500 + (
                    "=== 当前问题 ===\n用户: 帮我查询一下PEEK材料性质\n"
                    "补充说明：材料选型和计算优化\n约束条件：\n1. 应用部位与工况：参考STL"
                ),
            })
            result = asyncio.run(service.run(contract))

        self.assertEqual(contract["material_queries"], ["PEEK"])
        self.assertEqual(result["data_status"]["outcome"], "catalog_matched")
        self.assertEqual(result["results"][0]["material"]["material_id"], "MAT-3D4MAKERS-PEEK")

    def test_property_vocabulary_recognizes_cross_domain_engineering_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract({
                "taskid": "unit-property-vocabulary",
                "idea": "筛选压缩强度≥120 MPa、热膨胀系数≤12 ppm/K、表面粗糙度 Ra≤1.6 μm 的材料。",
            })

        self.assertEqual(contract["property_constraints"], [
            {"property": "compressive_strength", "operator": ">=", "value": 120.0, "unit": "MPa"},
            {"property": "thermal_expansion_coefficient", "operator": "<=", "value": 12.0, "unit": "ppm/K"},
            {"property": "surface_roughness_ra", "operator": "<=", "value": 1.6, "unit": "μm"},
        ])

    def test_directional_goal_returns_catalogue_evidence_landscape_without_invented_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-directional-catalogue",
                "idea": "帮我选成熟金属，抗拉强度越高越好",
            })))

        self.assertEqual(result["data_status"]["outcome"], "catalogue_evidence_landscape")
        self.assertEqual(result["screening"]["strategy"]["mode"], "evidence_landscape")
        self.assertEqual(result["constraints"]["preference_goals"], [{"property": "ultimate_tensile_strength", "direction": "maximize"}])
        self.assertTrue(result["results"])
        self.assertTrue(result["results"][0]["preference_evidence"])

    def test_low_density_compact_goal_is_ranked_and_reports_catalogue_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-low-density-preference",
                "idea": "机械臂零件需要低密度、强度高。",
            })))
            summary = service.summary(result)

        self.assertEqual(result["constraints"]["preference_goals"], [
            {"property": "density", "direction": "minimize"},
            {"property": "ultimate_tensile_strength", "direction": "maximize"},
        ])
        self.assertEqual(result["constraints"]["top_k"], 10)
        self.assertGreater(result["screening"]["summary"]["candidates_evaluated"], 50)
        self.assertTrue(result["screening"]["summary"]["candidates_truncated"])
        self.assertEqual(result["screening"]["summary"]["candidates_returned"], 10)
        self.assertGreater(result["screening"]["summary"]["preference_funnel_counts"][0]["count"], 10)
        self.assertEqual(
            result["screening"]["summary"]["preference_funnel_counts"][-1]["count"],
            result["screening"]["summary"]["complete_preference_candidate_count"],
        )
        self.assertGreater(result["screening"]["summary"]["complete_preference_candidate_count"], 0)
        self.assertTrue(all(
            all(item["status"] == "observed" for item in candidate["preference_evidence"])
            for candidate in result["results"][:result["screening"]["summary"]["complete_preference_candidate_count"]]
        ))
        self.assertIn("当前目录共有", summary)
        self.assertIn("本页仅展示前 10 种", summary)
        self.assertIn(f"| 已纳入本轮目录候选 | {result['screening']['summary']['candidates_evaluated']} |", summary)
        self.assertNotIn("暂待补充数据的材料", summary)

    def test_printing_consumable_scope_and_composite_grade_are_not_lost(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            scoped = asyncio.run(service.run(service.contract({
                "taskid": "unit-print-consumable-scope",
                "idea": "我想找3D打印耗材，低密度高强度。",
            })))
            composite = asyncio.run(service.run(service.contract({
                "taskid": "unit-ppscf-exact",
                "idea": "打印材料 PPS-CF 的性能。",
            })))

        self.assertEqual(scoped["constraints"]["material_families"], ["__3d_printing_consumables__"])
        self.assertTrue(scoped["results"])
        self.assertTrue(all(
            any(token in " ".join(str(item["material"].get(key) or "").casefold() for key in ("display_name", "family", "product_state"))
                for token in ("fdm", "fff", "sls", "sla", "耗材", "filament", "线材", "树脂", "工程塑料", "尼龙", "pekk", "peek", "pei", "pps", "abs", "asa", "onyx"))
            for item in scoped["results"]
        ))
        self.assertTrue(any(
            all(evidence["status"] == "observed" for evidence in item["preference_evidence"])
            for item in scoped["results"]
        ))
        self.assertEqual(composite["results"][0]["material"]["material_id"], "MAT-ROBOT-SEED-PPS-CF-FDM")

    def test_compact_high_heat_dissipation_and_hardness_request_becomes_preference_screening(self) -> None:
        """Do not send a qualitative but actionable request back to empty criteria collection."""
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-robot-stl-qualitative-goals",
                "idea": "面向机器人高散热、硬度的零部件，参考STL完成材料选型和计算优化。",
            })))
            summary = service.summary(result)
            assets = service.render_assets(result)

        self.assertEqual(result["data_status"]["outcome"], "catalogue_evidence_landscape")
        self.assertEqual(result["screening"]["strategy"]["mode"], "evidence_landscape")
        self.assertEqual(result["constraints"]["preference_goals"], [
            {"property": "thermal_conductivity", "direction": "maximize"},
            {"property": "hardness", "direction": "maximize"},
        ])
        self.assertEqual(result["constraints"]["selection_context"]["application"], "机器人零部件")
        self.assertIn("STL", result["constraints"]["selection_context"]["manufacturing"])
        self.assertIn("## 3. 证据覆盖与候选核验", summary)
        self.assertIn("证据覆盖漏斗", summary)
        self.assertIn("## 1. 需求与已知工况", summary)
        self.assertIn("| 应用场景 | 机器人零部件 |", summary)
        requirement_section = summary.split("## 2. 本轮筛选/比较口径", 1)[0]
        self.assertIn("我先根据你已经描述的内容整理如下", requirement_section)
        self.assertIn("| 性能关注点 | 导热系数越高越好；硬度越高越好 |", requirement_section)
        self.assertIn("已提供 STL 几何文件，可用于理解零件边界；制造工艺尚待补充", requirement_section)
        self.assertIn("### 为形成部件级判断，建议补充", requirement_section)
        self.assertNotIn("| 已指定材料/牌号 | 未指定 |", requirement_section)
        self.assertNotIn("| 材料体系约束 | 未限定 |", requirement_section)
        self.assertNotIn("| 已给出的数值门槛 | 0 项 |", requirement_section)
        self.assertIn("## 4. 结论", summary)
        self.assertIn("## 5. 材料性质汇总", summary)
        self.assertNotIn("## 6. 缺失项与下一步", summary)
        self.assertIn("当前优先评估", summary)
        self.assertIn("可比较证据的候选覆盖数，不是已选材料数", summary)
        self.assertIn("硬度 |", summary)
        self.assertNotIn("硬度：当前未收录", summary)
        self.assertIn("D：模型/工程估算，不能用于通过判断", summary)
        property_summary = summary.split("## 5. 材料性质汇总", 1)[1]
        self.assertIn("热/力预建模参数", property_summary)
        self.assertNotIn("COMSOL 热/力预建模参数", property_summary)
        self.assertIn("| 热/力仿真 | 密度 |", property_summary)
        self.assertIn("| 力仿真 | 杨氏模量 |", property_summary)
        self.assertRegex(property_summary, r"\d+(?:\.\d+)? HV（工程估算）")
        self.assertNotIn("导热系数↑", summary)
        self.assertNotIn("硬度↑", summary)
        self.assertNotIn("优先选择", summary)
        self.assertEqual(len({item["material"]["display_name"] for item in result["results"]}), len(result["results"]))
        self.assertEqual(assets[0]["name"], "evidence_funnel")

    def test_partial_traceable_evidence_can_support_a_provisional_material_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-industrial-robot-alloy-priority",
                "idea": "工业搬运机械臂的零部件需要高散热和硬度，参考 STL 完成材料选型。",
                "mature_material": {
                    "material_families": ["镍基高温合金", "铝合金"],
                    "selection_context": {
                        "component": "承力连接件",
                        "operating_conditions": "15 kg 负载、0.5 m 工作半径",
                        "project_progress": "已完成 STL 选型，进入材料与制造方案比较",
                    },
                },
            })))

        summary = service.summary(result)
        self.assertIn("INCONEL 718", summary)
        self.assertIn("暂定优先评估材料", summary)
        self.assertIn("A：可追溯，材料状态/测试条件已记录", summary)
        self.assertIn("INCONEL alloy 718；表 5，第 2 页", summary)
        self.assertNotIn("d47a32564d01-table-0005", summary)
        self.assertIn("D：模型/工程估算，不能用于通过判断", summary)
        self.assertIn("不作为工程放行结论", summary)
        self.assertIn("15 kg 负载、0.5 m 工作半径", summary)
        self.assertIn("已完成 STL 选型，进入材料与制造方案比较", summary)
        self.assertIn("承力连接件", summary)
        self.assertIn("热/力预建模参数", summary)
        self.assertIn("来源表包含多种热处理/测试脚注", summary)
        self.assertNotIn("Annealing was 1800°F/1 hr", summary)
        self.assertNotIn("test_temperature_deg_f=Room", summary)

    def test_temperature_curve_card_shows_property_values_not_temperature_as_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-316-curve-card",
                "mature_material": {"material_queries": ["316不锈钢（上传表）"]},
            })))

        summary = service.summary(result)
        self.assertIn("260 °C：17.832 W/(m·K)；820 °C：23.123 W/(m·K)", summary)
        self.assertIn("测量温度 260–820 °C", summary)
        self.assertNotIn("533.15 K–1,093.2 K 温度曲线", summary)

    def test_engineering_estimate_is_visibly_separate_and_never_used_for_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-estimate-separation",
                "idea": "为 Al 6061-T6 比较导热和硬度，允许给出缺失数据的工程估算。",
                "mature_material": {
                    "material_queries": ["Al 6061-T6"],
                    "preference_goals": [
                        {"property": "thermal_conductivity", "direction": "maximize"},
                        {"property": "hardness", "direction": "maximize"},
                    ],
                    "engineering_estimates": [{
                        "material": "Al 6061-T6", "property": "hardness",
                        "value_min": 90, "value_max": 110, "unit": "HV",
                        "condition": "T6；室温；具体产品状态待复核",
                        "basis": "上游模型按同牌号 T6 公开数据的保守区间整理",
                        "source": "上游工程估算 v1",
                    }],
                },
            })))

        summary = service.summary(result)
        self.assertIn("工程估算", summary)
        self.assertIn("D：模型/工程估算，不能用于通过判断", summary)
        self.assertIn("上游工程估算 v1", summary)
        self.assertEqual(result["results"][0]["preference_evidence"][1]["status"], "missing")

    def test_upstream_evidence_is_preserved_but_not_promoted_to_catalogue_fact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "unit-upstream-evidence",
                "idea": "整理已有商品材料信息",
                "mature_material": {
                    "upstream_evidence": [{
                        "material": "ExampleSeal-X9",
                        "property": "拉伸强度",
                        "value": 12.5,
                        "unit": "MPa",
                        "condition": "23 °C",
                        "source": "供应商数据表第 2 页",
                    }],
                },
            }
            result = asyncio.run(service.run(service.contract(payload)))

        summary = service.summary(result)
        self.assertEqual(result["data_status"]["outcome"], "upstream_evidence_only")
        self.assertIn("供应商数据表第 2 页", summary)
        self.assertIn("待目录核验", summary)
        self.assertIn("不视为本服务已核验的数据库事实", summary)

    def test_upstream_evidence_material_anchor_is_used_for_catalogue_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "unit-upstream-evidence-match",
                "idea": "整理供应商数据表",
                "mature_material": {
                    "upstream_evidence": [{
                        "material": "IN718",
                        "property": "屈服强度",
                        "value": 1034,
                        "unit": "MPa",
                        "source": "供应商数据表",
                    }],
                },
            }
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertEqual(result["constraints"]["material_queries"], ["IN718"])
        self.assertEqual(result["data_status"]["outcome"], "catalog_matched")
        self.assertEqual(result["results"][0]["material"]["material_id"], "MAT-IN718")

    def test_frontend_event_contract(self) -> None:
        async def fake_publish(taskid: str, assets: list[dict]) -> dict[str, str]:
            return {item["name"]: f"https://example.invalid/{taskid}/{item['name']}.png" for item in assets}

        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            websocket = _FakeWebSocket(_payload("ws-contract"))
            with patch.object(main, "ORCHESTRATOR", service), patch.object(main, "publish_png_assets", fake_publish), patch.dict(os.environ, {"MATURE_MATERIAL_LLM_STREAM": "false"}):
                asyncio.run(main.start(websocket))

        route_paths = {route.path for route in main.app.routes}
        self.assertTrue({"/start", "/mature-material/start", "/roles"}.issubset(route_paths))

        texts = [value for kind, value in websocket.events if kind == "text"]
        json_events = [value for kind, value in websocket.events if kind == "json"]
        progress_events = [event for event in json_events if event.get("type") == "progress"]
        result_events = [event for event in json_events if event.get("type") == "result"]
        self.assertEqual(texts[0], "[start]")
        self.assertEqual(texts.count(f"<<<CONTENT_START:{main.FRONTEND_STEP_ID}>>>"), 2)
        self.assertEqual(texts.count(f"<<<CONTENT_END:{main.FRONTEND_STEP_ID}>>>"), 2)
        self.assertEqual(texts[-1], "[end]")
        self.assertEqual(len(progress_events), 1)
        self.assertEqual(progress_events[0]["data"]["id"], main.FRONTEND_STEP_ID)
        self.assertEqual(progress_events[0]["data"]["stepId"], main.FRONTEND_STEP_ID)
        self.assertEqual(len(result_events), 1)
        asset_events = [event for event in json_events if event.get("type") == "MaterialsPNG"]
        self.assertEqual(len(asset_events), 2)
        self.assertTrue(all(event["stepId"] == main.FRONTEND_STEP_ID for event in asset_events))
        self.assertTrue(all(event["url"].startswith("https://example.invalid/ws-contract/") for event in asset_events))

        start_index = next(index for index, event in enumerate(websocket.events) if event == ("text", "[start]"))
        progress_index = next(index for index, event in enumerate(websocket.events) if event == ("json", progress_events[0]))
        result_index = next(index for index, event in enumerate(websocket.events) if event == ("json", result_events[0]))
        end_index = next(index for index, event in enumerate(websocket.events) if event == ("text", "[end]"))
        self.assertLess(start_index, progress_index)
        self.assertLess(progress_index, result_index)
        self.assertLess(result_index, end_index)
        first_asset_index = next(index for index, event in enumerate(websocket.events) if event == ("json", asset_events[0]))
        conclusion_index = next(
            index for index, event in enumerate(websocket.events)
            if event[0] == "text" and "## 4. 结论" in event[1]
        )
        self.assertLess(first_asset_index, conclusion_index)
        self.assertTrue(websocket.closed)

    def test_conductive_lubricant_uses_existing_frontend_protocol(self) -> None:
        async def fake_publish(taskid: str, assets: list[dict]) -> dict[str, str]:
            return {item["name"]: f"https://example.invalid/{taskid}/{item['name']}.png" for item in assets}

        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract(_fluid_payload("ws-fluid-contract"))
            self.assertEqual(contract["workflow_kind"], "conductive_lubricant_initial_screen")
            result = asyncio.run(service.run(contract))
            self.assertEqual(result["data_status"]["outcome"], "fluid_initial_screen_completed")
            self.assertEqual(result["screening"]["summary"]["matched_before_limit"], 41)
            self.assertEqual(len(result["shortlist"]["a_candidates"]), 0)
            self.assertEqual(len(result["shortlist"]["b_candidates"]), 4)
            self.assertEqual(len(result["results"]), 20)
            websocket = _FakeWebSocket(_fluid_payload("ws-fluid-contract"))
            with patch.object(main, "ORCHESTRATOR", service), patch.object(main, "publish_png_assets", fake_publish), patch.dict(os.environ, {"MATURE_MATERIAL_LLM_STREAM": "false"}):
                asyncio.run(main.start(websocket))

        texts = [value for kind, value in websocket.events if kind == "text"]
        json_events = [value for kind, value in websocket.events if kind == "json"]
        self.assertEqual(texts[0], "[start]")
        self.assertEqual(texts.count(f"<<<CONTENT_START:{main.FRONTEND_STEP_ID}>>>"), 2)
        self.assertEqual(texts.count(f"<<<CONTENT_END:{main.FRONTEND_STEP_ID}>>>"), 2)
        self.assertEqual(texts[-1], "[end]")
        self.assertEqual(len([event for event in json_events if event.get("type") == "progress"]), 1)
        assets = [event for event in json_events if event.get("type") == "MaterialsPNG"]
        self.assertEqual(len(assets), 2)
        self.assertTrue(all(event["stepId"] == main.FRONTEND_STEP_ID for event in assets))
        result_event = next(event for event in json_events if event.get("type") == "result")
        self.assertEqual(result_event["data"]["workflow_kind"], "conductive_lubricant_initial_screen")

    def test_directional_fluid_goals_create_a_preference_landscape_without_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract({
                "taskid": "unit-directional-fluid",
                "idea": "帮我找导电润滑油，电导率越高越好，黏度越低越好",
            })))

        self.assertEqual(result["data_status"]["outcome"], "fluid_evidence_landscape")
        self.assertFalse(result["constraints"]["default_profile_applied"])
        self.assertEqual(result["screening"]["summary"]["mode"], "preference_landscape")
        self.assertEqual(result["screening"]["request"]["property_constraints"], [])
        self.assertEqual(result["screening"]["request"]["preference_goals"], [
            {"name": "conductivity", "direction": "maximize"},
            {"name": "dynamic_viscosity", "direction": "minimize"},
        ])

    def test_fluid_impossible_threshold_keeps_the_funnel_without_inventing_substitutes(self) -> None:
        payload = _fluid_payload("unit-fluid-no-match")
        payload["mature_material"]["fluid_initial_screen"]["property_constraints"][0]["value"] = 9999
        payload["idea"] = "需要一款室温导电润滑油，电导率不低于 9999 S/m。"
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertEqual(result["data_status"]["outcome"], "fluid_no_matching_evidence")
        self.assertEqual(result["screening"]["summary"]["matched_before_limit"], 0)
        self.assertEqual(result["screening"]["funnel"][-1]["count"], 0)
        self.assertIn("没有证据配对同时通过", result["data_status"]["message"])

    def test_fluid_workflow_uses_default_profile_after_one_missing_criteria_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            first_payload = {"taskid": "fluid-default-after-followup", "idea": "推荐一款导电润滑油"}
            first = asyncio.run(service.run(service.contract(first_payload)))
            self.assertEqual(first["data_status"]["outcome"], "needs_screening_criteria")
            service.save(first)

            followup_payload = {"taskid": "fluid-default-after-followup", "idea": "我希望温度在135度还可以保持稳定，剩下的你自己默认"}
            contract = service.contract(followup_payload)
            self.assertTrue(contract["default_profile_applied"])
            self.assertEqual(contract["application_operating_temperature_c"], 135.0)
            result = asyncio.run(service.run(contract))

        self.assertEqual(result["data_status"]["outcome"], "fluid_initial_screen_completed")
        self.assertEqual(result["screening"]["summary"]["matched_before_limit"], 41)
        self.assertTrue(result["constraints"]["default_profile_applied"])

    def test_new_metal_request_does_not_inherit_previous_fluid_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            first = asyncio.run(service.run(service.contract({
                "taskid": "route-reset-after-fluid",
                "idea": "推荐一款导电润滑油",
            })))
            self.assertEqual(first["workflow_kind"], "conductive_lubricant_initial_screen")
            service.save(first)

            second = service.contract({
                "taskid": "route-reset-after-fluid",
                "current_user_message": "面向机器人高散热、硬度的零部件，推荐一款成熟的金属材料，参考 STL。",
                # Simulate stale gateway context from the prior turn.
                "idea": "推荐一款导电润滑油",
            })

        self.assertEqual(second["workflow_kind"], "mature_material_catalogue_initial_screen")
        self.assertEqual(second["selection_context"]["application"], "机器人零部件")

    def test_gateway_summary_without_numbers_does_not_claim_a_default_screen(self) -> None:
        """A rewritten upstream summary cannot stand in for the user turn."""
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "fluid-gateway-summary-without-values",
                "idea": "调用材料筛选计算服务，输入严格电阻率、黏度、耐温指标及复合体系约束，检索导电润滑液体。",
            }
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertEqual(result["data_status"]["outcome"], "needs_screening_criteria")
        self.assertIn("没有包含电阻率/电导率、黏度或温度的具体数值", result["data_status"]["message"])
        self.assertNotIn("默认初筛口径", result["data_status"]["message"])

    def test_fluid_followup_keeps_user_viscosity_boundary_and_explains_grades(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            first = asyncio.run(service.run(service.contract({"taskid": "fluid-user-band", "idea": "推荐导电润滑油"})))
            service.save(first)
            payload = {
                "taskid": "fluid-user-band",
                "idea": "电阻率小于10Ω·m，旋转黏度在130-150mPa·s之间，最高使用温度不超过135℃，其余按默认。",
            }
            contract = service.contract(payload)
            result = asyncio.run(service.run(contract))

        constraints = result["screening"]["request"]["property_constraints"]
        self.assertIn({"name": "resistivity", "operator": "<=", "value": 10.0, "unit": "ohm*m"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": ">=", "value": 130.0, "unit": "mPa*s"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": "<=", "value": 150.0, "unit": "mPa*s"}, constraints)
        first_section, shortlist_section, _ = service.presentation_sections(result)
        self.assertIn("130–150 mPa·s", first_section)
        self.assertIn("严格区间 130 ≤ η ≤ 150", first_section)
        self.assertIn("数值匹配证据", shortlist_section)
        self.assertIn("润滑基础油补测线索", shortlist_section)
        self.assertIn("季戊四醇四油酸酯", shortlist_section)

    def test_two_sided_resistivity_constraint_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "fluid-two-sided-resistivity",
                "idea": "推荐一款可以导电的润滑油，电阻率小于10Ω·m，但是大于1Ω·m，旋转粘度130-150mPa·s。",
            }
            result = asyncio.run(service.run(service.contract(payload)))

        constraints = result["screening"]["request"]["property_constraints"]
        self.assertIn({"name": "resistivity", "operator": ">=", "value": 1.0, "unit": "ohm*m"}, constraints)
        self.assertIn({"name": "resistivity", "operator": "<=", "value": 10.0, "unit": "ohm*m"}, constraints)

    def test_execution_summary_resistivity_range_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "fluid-summary-resistivity-range",
                "idea": "调用材料筛选计算，针对电阻率1-10Ω·m、粘度130-150mPa·s且适用温度覆盖135℃的成熟导电润滑油商品库进行检索。",
            }
            result = asyncio.run(service.run(service.contract(payload)))

        constraints = result["screening"]["request"]["property_constraints"]
        self.assertIn({"name": "resistivity", "operator": ">=", "value": 1.0, "unit": "ohm*m"}, constraints)
        self.assertIn({"name": "resistivity", "operator": "<=", "value": 10.0, "unit": "ohm*m"}, constraints)

    def test_two_sided_conductivity_constraint_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "fluid-two-sided-conductivity",
                "idea": "导电润滑油，电导率不低于0.1 S/m、且不高于1 S/m，旋转粘度130-150mPa·s。",
            }
            result = asyncio.run(service.run(service.contract(payload)))

        constraints = result["screening"]["request"]["property_constraints"]
        self.assertIn({"name": "conductivity", "operator": ">=", "value": 0.1, "unit": "S/m"}, constraints)
        self.assertIn({"name": "conductivity", "operator": "<=", "value": 1.0, "unit": "S/m"}, constraints)

    def test_constraint_wording_matrix_preserves_both_bounds(self) -> None:
        examples = (
            ("导电润滑油，电阻率不低于1 Ω·m，且不高于10 Ω·m。", "resistivity", "ohm*m", 1.0, 10.0),
            ("导电润滑油，电导率至少0.1 S/m，至多1 S/m。", "conductivity", "S/m", 0.1, 1.0),
            ("导电润滑油，旋转黏度下限为130 mPa·s，上限为150 mPa·s。", "dynamic_viscosity", "mPa*s", 130.0, 150.0),
            ("导电润滑油，电阻率介于1 Ω·m和10 Ω·m之间。", "resistivity", "ohm*m", 1.0, 10.0),
        )
        for text, name, unit, lower, upper in examples:
            with self.subTest(text=text):
                from src.fluid_lubricant.workflow import _request_from_text
                request, _ = _request_from_text(text)
                constraints = request["property_constraints"]
                self.assertIn({"name": name, "operator": ">=", "value": lower, "unit": unit}, constraints)
                self.assertIn({"name": name, "operator": "<=", "value": upper, "unit": unit}, constraints)

    def test_conductive_lubricant_material_library_summary_routes_to_fluid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "fluid-library-summary-route",
                "idea": "调用材料筛选计算，依据温度293.15–303.15 K、动态黏度130–150 mPa·s及电导率0.1–1 S/m的约束条件，重新检索并匹配现有导电润滑材料库。",
            }
            contract = service.contract(payload)

        self.assertEqual(contract["workflow_kind"], "conductive_lubricant_initial_screen")

    def test_condensed_conductive_lubrication_execution_summary_routes_and_preserves_constraints(self) -> None:
        """The gateway may omit “油” and retain only “导电润滑需求”."""
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "fluid-condensed-routing",
                "idea": (
                    "针对电阻率介于 1 至 10 Ω·m、旋转粘度控制在 130-150 mPa·s "
                    "且耐温上限达 135℃的导电润滑需求，执行现有材料筛选与计算任务。"
                ),
            }
            contract = service.contract(payload)
            result = asyncio.run(service.run(contract))

        self.assertEqual(contract["workflow_kind"], "conductive_lubricant_initial_screen")
        constraints = result["screening"]["request"]["property_constraints"]
        self.assertIn({"name": "resistivity", "operator": ">=", "value": 1.0, "unit": "ohm*m"}, constraints)
        self.assertIn({"name": "resistivity", "operator": "<=", "value": 10.0, "unit": "ohm*m"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": ">=", "value": 130.0, "unit": "mPa*s"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": "<=", "value": 150.0, "unit": "mPa*s"}, constraints)
        self.assertEqual(result["screening"]["summary"]["matched_before_limit"], 41)

    def test_numeric_oil_followup_executes_without_saved_previous_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "fluid-direct-numeric-followup",
                "idea": "电阻率小于10Ω·m，旋转粘度130-150mPa·s，油的最高使用温度不超过135℃。",
            }
            contract = service.contract(payload)
            result = asyncio.run(service.run(contract))

        self.assertEqual(contract["workflow_kind"], "conductive_lubricant_initial_screen")
        self.assertEqual(result["data_status"]["outcome"], "fluid_initial_screen_completed")
        self.assertEqual(result["screening"]["summary"]["matched_before_limit"], 41)

    def test_user_numeric_text_overrides_incorrect_upstream_fluid_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = _fluid_payload("fluid-text-overrides-upstream")
            payload["idea"] = "推荐导电润滑油，电阻率小于10Ω·m，旋转粘度130-150mPa·s。"
            # Simulate an upstream LLM that incorrectly compressed the range.
            payload["mature_material"]["fluid_initial_screen"]["property_constraints"] = [
                {"name": "conductivity", "operator": ">=", "value": 0.1, "unit": "S/m"},
                {"name": "dynamic_viscosity", "operator": "<=", "value": 130, "unit": "mPa*s"},
            ]
            result = asyncio.run(service.run(service.contract(payload)))

        constraints = result["screening"]["request"]["property_constraints"]
        self.assertIn({"name": "resistivity", "operator": "<=", "value": 10.0, "unit": "ohm*m"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": ">=", "value": 130.0, "unit": "mPa*s"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": "<=", "value": 150.0, "unit": "mPa*s"}, constraints)
        self.assertNotIn({"name": "dynamic_viscosity", "operator": "<=", "value": 130, "unit": "mPa*s"}, constraints)

    def test_current_user_message_overrides_upstream_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = _fluid_payload("fluid-current-user-message")
            payload["idea"] = "上游摘要：按默认动态黏度≤130 mPa·s 检索。"
            payload["current_user_message"] = "导电润滑油，电阻率小于10Ω·m，旋转粘度130-150mPa·s。"
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertIn("130-150", result["constraints"]["raw_requirement"])
        self.assertEqual(result["screening"]["summary"]["matched_before_limit"], 41)
        _, table, conclusion = service.presentation_sections(result)
        self.assertEqual(table.count("| E"), 5)
        self.assertIn("## 5. 材料性质汇总", conclusion)
        self.assertIn("| 项目 | 当前已知信息 |", conclusion)
        self.assertIn("| 体系 |", conclusion)
        self.assertIn("| 组分 |", conclusion)
        self.assertIn("| 测试条件与数值 |", conclusion)
        self.assertIn("| 尚待验证 |", conclusion)

    def test_nested_gateway_user_turn_overrides_default_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = _fluid_payload("fluid-nested-user-turn")
            payload["idea"] = "上游摘要：针对导电润滑介质，依据用户提供的精确电阻率、粘度及温度指标执行匹配。"
            payload["data"] = {"messages": [{
                "sender": "user",
                "message": {"content": "温度在室温到135度即可，电阻率小于10Ω·m，旋转粘度130-150mPa·s。"},
            }]}
            result = asyncio.run(service.run(service.contract(payload)))

        constraints = result["screening"]["request"]["property_constraints"]
        self.assertIn({"name": "resistivity", "operator": "<=", "value": 10.0, "unit": "ohm*m"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": ">=", "value": 130.0, "unit": "mPa*s"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": "<=", "value": 150.0, "unit": "mPa*s"}, constraints)

    def test_missing_user_numbers_do_not_execute_structured_default_as_user_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = _fluid_payload("fluid-summary-lost-numbers")
            payload["idea"] = "上游摘要：针对导电润滑介质，依据用户提供的精确电阻率、粘度及温度指标执行匹配。"
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertIsNone(result["screening"])
        self.assertEqual(result["data_status"]["outcome"], "needs_screening_criteria")

    def test_upstream_mention_of_default_profile_does_not_authorize_default_screening(self) -> None:
        """A planner's history must not replace a missing current user turn."""
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = _fluid_payload("fluid-summary-mentions-default")
            payload["idea"] = (
                "上一步总结：已建立导电润滑油的默认初筛口径，温度 293.15–303.15 K、电导率≥0.1 S/m，"
                "动态黏度 130–150 mPa·s。"
                "接下来需要进行执行的任务：依据用户提供的电阻率、黏度及 135℃ 参数筛选导电润滑油。"
            )
            result = asyncio.run(service.run(service.contract(payload)))

        self.assertIsNone(result["screening"])
        self.assertEqual(result["data_status"]["outcome"], "needs_screening_criteria")

    def test_execution_summary_viscosity_range_overrides_default_profile(self) -> None:
        """Accept the summary wording emitted by the production gateway.

        The gateway may transmit only its final execution clause rather than
        a separate ``current_user_message`` field.  ``黏度范围（130-150
        mPa·s）`` must retain both bounds and must never become a one-sided limit.
        """
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = {
                "taskid": "fluid-summary-range",
                "idea": (
                    "上一步总结：导电润滑介质初筛使用默认条件。\n"
                    "接下来需要进行执行的任务:调用材料筛选计算（现有材料）团队，"
                    "依据修正后的电阻率<10Ω・m、温度及粘度范围（130-150 mPa・s）检索现有导电润滑油目录。"
                ),
            }
            result = asyncio.run(service.run(service.contract(payload)))

        constraints = result["screening"]["request"]["property_constraints"]
        self.assertIn({"name": "resistivity", "operator": "<=", "value": 10.0, "unit": "ohm*m"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": ">=", "value": 130.0, "unit": "mPa*s"}, constraints)
        self.assertIn({"name": "dynamic_viscosity", "operator": "<=", "value": 150.0, "unit": "mPa*s"}, constraints)
        self.assertEqual(result["screening"]["summary"]["matched_before_limit"], 41)
        first_section, _, conclusion = service.presentation_sections(result)
        self.assertIn("严格区间 130 ≤ η ≤ 150", first_section)
        self.assertIn("## 5. 材料性质汇总", conclusion)
        self.assertIn("**E37**", conclusion)
        self.assertIn("| 项目 | 当前已知信息 |", conclusion)

    def test_catalogue_miss_uses_existing_streaming_markers_for_literature_prompt(self) -> None:
        payload = {
            "taskid": "ws-catalogue-miss",
            "idea": "核验未入库商品牌号 ExampleSeal-X9 的性能数据",
            "mature_material": {"material_queries": ["ExampleSeal-X9"]},
        }
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            websocket = _FakeWebSocket(payload)
            with patch.object(main, "ORCHESTRATOR", service), patch.dict(os.environ, {"MATURE_MATERIAL_LLM_STREAM": "false"}):
                asyncio.run(main.start(websocket))

        texts = [value for kind, value in websocket.events if kind == "text"]
        json_events = [value for kind, value in websocket.events if kind == "json"]
        self.assertEqual(texts.count(f"<<<CONTENT_START:{main.FRONTEND_STEP_ID}>>>"), 2)
        self.assertEqual(texts.count(f"<<<CONTENT_END:{main.FRONTEND_STEP_ID}>>>"), 2)
        self.assertTrue(any("建议进入文献筛选" in text for text in texts))
        self.assertFalse(any("LLM 托底建议" in text for text in texts))
        self.assertEqual(len([event for event in json_events if event.get("type") == "progress"]), 1)
        self.assertEqual(len([event for event in json_events if event.get("type") == "result"]), 1)

    def test_unhandled_websocket_failure_uses_plain_text_notice(self) -> None:
        websocket = _FakeWebSocket(_payload("ws-runtime-failure"))
        with patch.object(main, "_contract", side_effect=RuntimeError("目录暂不可用")):
            asyncio.run(main.start(websocket))

        self.assertEqual(websocket.events, [("accept", None), ("text", "\n材料目录查询失败：目录暂不可用\n")])
        self.assertTrue(websocket.closed)

    def test_websocket_already_closed_by_peer_does_not_raise(self) -> None:
        websocket = _FakeWebSocket(_payload("ws-close-race"))
        with patch.object(main, "_contract", side_effect=RuntimeError("目录暂不可用")), patch.object(
            websocket, "close", side_effect=RuntimeError("websocket.close already sent")
        ):
            asyncio.run(main.start(websocket))

        self.assertEqual(websocket.events, [("accept", None), ("text", "\n材料目录查询失败：目录暂不可用\n")])

    def test_roles_contract_shape(self) -> None:
        payload = main.roles()
        self.assertEqual(len(payload), 1)
        role = next(iter(payload.values()))
        self.assertEqual(role["role_id"], "mature_material_catalog_v1")
        self.assertEqual(role["profile"], main.ORCHESTRATOR.profile)
        self.assertEqual(role["addresses"][0], "src.team_config.MaterialMature")
        self.assertEqual(role["__module_class_name"], "src.team_config.MaterialMature")
        self.assertEqual(role["actions"][0]["__module_class_name"], "src.team_config.MatureMaterialCatalogQuery")
        self.assertIn("rc", role)
        self.assertIn("planner", role)
        self.assertIn("input_contract", role["routing"])
        self.assertIn("output_contract", role["routing"])
        self.assertIn("needs_literature_screening", role["routing"]["output_contract"])
        self.assertEqual(role["routing"]["route_before"], [])
        self.assertNotIn("alloy_reference_catalogued", role["routing"]["output_contract"])

    def test_processed_catalogue_integrity(self) -> None:
        processed = ROOT / "data" / "processed"

        def read(name: str) -> list[dict[str, str]]:
            with (processed / name).open(encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(handle))

        materials = read("materials.csv")
        material_ids = [row.get("material_id", "") for row in materials]
        self.assertTrue(material_ids)
        self.assertNotIn("", material_ids)
        self.assertEqual(len(material_ids), len(set(material_ids)))

        known_ids = set(material_ids)
        for row in read("material_aliases.csv"):
            self.assertIn(row.get("material_id"), known_ids)
            self.assertTrue(row.get("alias", "").strip())

        for row in read("property_points.csv"):
            self.assertIn(row.get("material_id"), known_ids)
            self.assertTrue(row.get("property", "").strip())
            self.assertTrue(row.get("unit", "").strip())
            float(row["value"])

        for row in read("curve_data.csv"):
            self.assertIn(row.get("material_id"), known_ids)
            self.assertTrue(row.get("property", "").strip())
            self.assertTrue(row.get("SI_unit", "").strip())
            float(row["temperature_K"])
            float(row["value_SI"])

        for row in read("composition_long.csv"):
            self.assertIn(row.get("material_id"), known_ids)
            self.assertTrue(row.get("component", "").strip())


if __name__ == "__main__":
    unittest.main()
