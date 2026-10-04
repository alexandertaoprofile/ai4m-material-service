"""Presence templates, minimum chemistry diversity and honest report contracts."""
from __future__ import annotations

import random
import unittest
from unittest.mock import patch

from src.material_workflow.constraints import constraint_from_payload
from src.material_workflow.omatg_sampling import composition_errors, composition_summary, presence_anchor_plan
from src.material_workflow.omatg_report import build_plan, build_result_report
from src.material_workflow.schemas import GenerationConstraint, GenerationManifest, GeneratedCandidate, NewMaterialPipelineResult


class PresenceSamplingTests(unittest.TestCase):
    def parse(self, **fields):
        with patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"}):
            return constraint_from_payload({"taskid": "diversity-test", "new_material": fields})

    def test_multi_element_dng_defaults_to_two_with_generation_conditioning(self):
        c = self.parse(allowed_elements=["Fe", "Cr", "Ni"], required_elements=["Fe"])
        self.assertEqual(c.min_distinct_elements, 2)
        self.assertEqual(c.dng_species_strategy, "presence_anchors")
        self.assertEqual(c.required_elements, ["Fe"])
        self.assertFalse(c.refine_with_csp)

    def test_explicit_pure_element_task_is_preserved(self):
        c = self.parse(allowed_elements=["Fe"])
        self.assertEqual((c.min_distinct_elements, c.dng_species_strategy), (1, "masked"))
        c = self.parse(target_formula="Fe", formula_units=8)
        self.assertEqual((c.generation_mode, c.num_atoms, c.min_distinct_elements), ("csp", 8, 1))

    def test_explicit_unary_and_old_mask_baseline_remain_available(self):
        c = self.parse(allowed_elements=["Fe", "Cr", "Ni"], min_distinct_elements=1, dng_species_strategy="masked")
        self.assertEqual((c.min_distinct_elements, c.dng_species_strategy), (1, "masked"))

    def test_open_dng_does_not_add_random_element_anchors(self):
        c = self.parse(allow_open_chemical_space=True, material_family="无机晶体")
        self.assertEqual((c.min_distinct_elements, c.dng_species_strategy), (2, "masked"))
        self.assertEqual(c.required_elements, [])

    def test_impossible_and_malformed_requests_block_before_gpu(self):
        for extra in ({"min_distinct_elements": 4}, {"min_distinct_elements": True},
            {"min_distinct_elements": 0}, {"min_distinct_elements": 2.5},
            {"min_distinct_elements": 3, "num_atoms": 2}, {"dng_species_strategy": "magic"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.parse(allowed_elements=["Fe", "Cr", "Ni"], **extra)
        with self.assertRaises(ValueError):
            self.parse(target_formula="Fe", min_distinct_elements=2)
        with self.assertRaises(ValueError):
            self.parse(target_formula="FeCr", dng_species_strategy="presence_anchors")

    def test_required_set_sets_minimum_default_not_all_optional_elements(self):
        c = self.parse(allowed_elements=["Fe", "Cr", "Ni", "Al"], required_elements=["Fe", "Cr", "Ni"])
        self.assertEqual(c.min_distinct_elements, 3)
        self.assertNotIn("Al", c.required_elements)

    def test_templates_cover_required_and_minimum_and_leave_free_sites(self):
        for required, minimum in (([26], 2), ([26, 24, 28], 3), ([], 2)):
            for template in presence_anchor_plan([26, 24, 28], required, minimum, 8, 10, 51):
                self.assertTrue(set(required) <= set(template.values()))
                self.assertGreaterEqual(len(set(template.values())), minimum)
                self.assertLessEqual(len(template), 4)
                self.assertTrue(set(template) <= set(range(8)))
                self.assertTrue(set(template.values()) <= {26, 24, 28})

    def test_templates_rotate_optional_domain_instead_of_requiring_all(self):
        templates = presence_anchor_plan([26, 24, 28], [26], 2, 8, 10, 51)
        sets = {frozenset(row.values()) for row in templates}
        self.assertEqual(sets, {frozenset([26, 24]), frozenset([26, 28])})
        self.assertGreater(len({tuple(sorted(row.items())) for row in templates}), 5)

    def test_seed_is_reproducible_and_does_not_change_global_rng(self):
        state = random.getstate()
        first = presence_anchor_plan([26, 24, 28], [26], 2, 8, 10, 51)
        self.assertEqual(first, presence_anchor_plan([26, 24, 28], [26], 2, 8, 10, 51))
        self.assertNotEqual(first, presence_anchor_plan([26, 24, 28], [26], 2, 8, 10, 52))
        self.assertEqual(state, random.getstate())

    def test_empty_condition_is_not_fake_conditioning(self):
        self.assertEqual(presence_anchor_plan([26, 24], [], 1, 8, 2, 51), [{}, {}])

    def test_templates_reject_impossible_conditions(self):
        for allowed, required, minimum, atoms in (([26], [], 2, 8), ([26, 24], [28], 2, 8), ([26, 24, 28], [], 3, 2)):
            with self.subTest(allowed=allowed), self.assertRaises(ValueError):
                presence_anchor_plan(allowed, required, minimum, atoms, 10, 51)

    def test_pure_fe_cannot_pass_multi_element_admission(self):
        c = self.parse(allowed_elements=["Fe", "Cr", "Ni"], required_elements=["Fe"])
        failures = composition_errors({"Fe": 8}, c.to_dict())
        self.assertTrue(any("至少 2" in reason for reason in failures))
        self.assertEqual(composition_errors({"Fe": 7, "Cr": 1}, c.to_dict()), [])
        self.assertTrue(any("Fe" in reason for reason in composition_errors({"Ni": 8}, c.to_dict())))

    def test_composition_metrics_reduce_cell_multiples_not_element_ratios(self):
        rows = [{"metadata": {"element_counts": row}} for row in ({"Fe": 8}, {"Fe": 4}, {"Fe": 7, "Cr": 1}, {"Fe": 14, "Cr": 2}, {"Fe": 6, "Cr": 2})]
        self.assertEqual(composition_summary(rows), {"parsed_count": 5, "multi_element_count": 3,
            "unary_count": 2, "distinct_composition_count": 3, "distinct_element_sets": 2})

    def test_plan_labels_service_sampling_and_keeps_csp_optional(self):
        c = self.parse(allowed_elements=["Fe", "Cr", "Ni"], required_elements=["Fe"])
        markdown = build_plan(c)
        self.assertIn("每个候选必须包含", markdown)
        self.assertIn("至少包含 2 种元素", markdown)
        self.assertNotIn("已确认追加 CSP", markdown)

    def test_result_metrics_are_not_a_novelty_claim(self):
        c = self.parse(allowed_elements=["Fe", "Cr", "Ni"])
        candidate = GeneratedCandidate(candidate_id="dng-001", formula_pretty="Fe7Cr", metadata={"element_counts": {"Fe": 7, "Cr": 1}})
        generation = GenerationManifest(taskid=c.taskid, status="ok", backend="omatg", candidates=[candidate],
            metadata={"mode": "dng", "model_provenance": {"service_conditioning": True}})
        result = NewMaterialPipelineResult(taskid=c.taskid, status="ok", constraints=c, generation=generation)
        markdown = build_result_report(result)
        self.assertIn("1 个多元素候选、0 个单质候选", markdown)
        self.assertIn("采样前选定的部分元素锚定位点", markdown)
        self.assertIn("最终实际出现的不同元素数量", markdown)
        self.assertNotIn("已发现新材料", markdown)


if __name__ == "__main__":
    unittest.main()
