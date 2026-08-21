from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.material_workflow.alignn import predict_candidate_properties, requested_properties
from src.material_workflow.presentation import build_property_screening_card
from src.material_workflow.presentation import build_discovery_conclusion
from src.material_workflow.ranking import rank_candidates
from src.material_workflow.upstream_api import result_summary
from src.material_workflow.schemas import (
    GeneratedCandidate,
    GenerationConstraint,
    GenerationManifest,
    NewMaterialPipelineResult,
    RankedCandidate,
    ValidationResult,
)


class AlignnPropertyScreeningTests(unittest.TestCase):
    def test_default_panel_and_explicit_property_are_selected(self) -> None:
        self.assertEqual(
            requested_properties({}, {"electron_effective_mass": None}),
            ("band_gap", "bulk_modulus", "shear_modulus", "dielectric_constant_x", "electron_effective_mass", "hole_effective_mass"),
        )

    def test_prediction_is_traceable_and_does_not_change_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cif = Path(temporary) / "candidate.cif"
            cif.write_text("data_test", encoding="utf-8")
            candidate = GeneratedCandidate(candidate_id="gen_0", formula_pretty="LiPS", cif_path=cif)
            validation = ValidationResult(candidate_id="gen_0", status="ok", is_valid=True)

            def predictor(model: str, _path: Path, _timeout: int) -> float:
                return {"jv_mbj_bandgap_alignn": 2.3, "jv_bulk_modulus_kv_alignn": 42.0}[model]

            result = predict_candidate_properties(candidate, validation, ("band_gap", "bulk_modulus"), predictor=predictor)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.property_predictions["band_gap"]["value"], 2.3)
        self.assertEqual(result.property_predictions["bulk_modulus"]["model"], "jv_bulk_modulus_kv_alignn")
        self.assertEqual(result.property_predictions["band_gap"]["evidence_level"], "C：结构模型快速预测")

    def test_property_card_only_lists_computed_predictions(self) -> None:
        validation = ValidationResult(
            candidate_id="gen_0", status="ok", is_valid=True, formula_pretty="LiPS",
            property_predictions={"band_gap": {
                "label": "带隙", "value": 2.3, "unit": "eV", "model": "jv_mbj_bandgap_alignn",
                "model_version": "ALIGNN 2025.4.1", "display_method": "ALIGNN 带隙快速预测", "evidence_level": "C：结构模型快速预测",
            }},
        )
        candidate = GeneratedCandidate(candidate_id="gen_0", formula_pretty="LiPS")
        result = NewMaterialPipelineResult(
            taskid="test", status="ok", constraints=GenerationConstraint(taskid="test"),
            generation=GenerationManifest(taskid="test", status="ok", candidates=[candidate]),
            validations=[validation], ranked_candidates=[RankedCandidate(candidate=candidate, rank=1, score=1.0, validation=validation)],
        )
        card = build_property_screening_card(result)
        self.assertIn("带隙", card)
        self.assertNotIn("离子电导率", card)
        self.assertNotIn("| 来源 |", card)

    def test_property_card_adds_hardness_as_an_engineering_estimate(self) -> None:
        validation = ValidationResult(
            candidate_id="gen_0", status="ok", is_valid=True, formula_pretty="LiPS",
            property_predictions={
                "bulk_modulus": {"label": "体积模量", "value": 42.0, "unit": "GPa", "display_method": "ALIGNN 弹性性质快速预测"},
                "shear_modulus": {"label": "剪切模量", "value": 18.0, "unit": "GPa", "display_method": "ALIGNN 弹性性质快速预测"},
            },
        )
        candidate = GeneratedCandidate(candidate_id="gen_0", formula_pretty="LiPS")
        result = NewMaterialPipelineResult(
            taskid="test", status="ok", constraints=GenerationConstraint(taskid="test"),
            generation=GenerationManifest(taskid="test", status="ok", candidates=[candidate]),
            validations=[validation], ranked_candidates=[RankedCandidate(candidate=candidate, rank=1, score=1.0, validation=validation)],
        )
        self.assertIn("D：工程估算", build_property_screening_card(result))

    def test_best_structure_is_retained_when_every_candidate_misses_ehull(self) -> None:
        first = GeneratedCandidate(candidate_id="gen_0", formula_pretty="LiPS", generation_score=99.0)
        second = GeneratedCandidate(candidate_id="gen_1", formula_pretty="Li2PS3", generation_score=0.0)
        first_validation = ValidationResult(candidate_id="gen_0", status="ok", is_valid=True, energy_above_hull=0.12)
        second_validation = ValidationResult(
            candidate_id="gen_1", status="ok", is_valid=True, energy_above_hull=0.08,
            property_predictions={
                "bulk_modulus": {"value": 40.0, "label": "体积模量", "unit": "GPa"},
                "shear_modulus": {"value": 16.0, "label": "剪切模量", "unit": "GPa"},
            },
        )
        ranked = rank_candidates([first, second], [first_validation, second_validation])
        self.assertEqual(ranked[0].candidate.candidate_id, "gen_1")
        result = NewMaterialPipelineResult(
            taskid="miss-threshold", status="ok", constraints=GenerationConstraint(taskid="miss-threshold"),
            generation=GenerationManifest(taskid="miss-threshold", status="ok", candidates=[first, second]),
            validations=[first_validation, second_validation], ranked_candidates=ranked,
        )
        self.assertIn("最接近", build_discovery_conclusion(result))
        self.assertIn("D：工程估算", build_property_screening_card(result))

    def test_rejected_structure_explains_distance_failure_and_skips_properties(self) -> None:
        candidate = GeneratedCandidate(candidate_id="mg-001", formula_pretty="Mn3CrFe(CoNi)2")
        validation = ValidationResult(
            candidate_id="mg-001",
            status="ok",
            is_valid=False,
            formula_pretty="Mn3CrFe(CoNi)2",
            density=8.11,
            errors=["Atoms are implausibly close for their elements."],
            metadata={"close_pair_violations": [{
                "elements": ["Mn", "Cr"],
                "distance_angstrom": 1.9480107086882277,
                "minimum_allowed_angstrom": 2.1675,
            }]},
        )
        result = NewMaterialPipelineResult(
            taskid="rejected-structure",
            status="ok",
            constraints=GenerationConstraint(taskid="rejected-structure"),
            generation=GenerationManifest(taskid="rejected-structure", status="ok", candidates=[candidate]),
            validations=[validation],
            ranked_candidates=[RankedCandidate(candidate=candidate, rank=1, score=1.0, validation=validation)],
        )

        conclusion = build_discovery_conclusion(result)
        self.assertIn("未通过基础结构检查", conclusion)
        self.assertIn("Mn–Cr", conclusion)
        self.assertIn("1.948 Å", conclusion)
        self.assertIn("2.167 Å", conclusion)
        self.assertNotIn("已通过基础结构检查", conclusion)
        self.assertEqual(build_property_screening_card(result), "")
        summary = result_summary(result)
        self.assertIn("未通过原因", summary)
        self.assertIn("未计算（结构未准入）", summary)
        self.assertIn("Mn–Cr", summary)


if __name__ == "__main__":
    unittest.main()
